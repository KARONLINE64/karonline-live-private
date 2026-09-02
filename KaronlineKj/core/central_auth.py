"""Client REST du compte KJ auprès de l'API centrale karonlinelive.com.

Sans dépendance externe (stdlib uniquement) : ce module est volontairement
sans Qt pour rester testable en dehors de l'application graphique.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

# URL de l'instance centrale (annuaire + comptes). Surcharge possible pour les
# tests locaux via la variable d'environnement KL_CENTRAL_API.
CENTRAL_BASE_URL = os.environ.get(
    "KL_CENTRAL_API", "https://api.karonlinelive.com"
).rstrip("/")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

USER_MESSAGES = {
    "EMAIL TAKEN": "Ce courriel possède déjà un compte.",
    "INVALID EMAIL": "Adresse mail invalide.",
    "WEAK PASSWORD": "Mot de passe trop court (8 caractères minimum).",
    "WRONG CREDENTIALS": "Courriel ou mot de passe incorrect.",
    "EMAIL NOT VERIFIED": "Veuillez valider votre adresse e-mail avant de vous connecter.",
    "INVALID CODE": "Code de vérification invalide.",
    "CODE EXPIRED": "Le code de vérification a expiré. Demandez un nouveau code.",
    "NO CODE SENT": "Aucun code de vérification n'a été demandé pour cet e-mail.",
    "TOKEN INVALID": "Session expirée, reconnectez-vous.",
    "CARD INVALID": "Numéro de carte invalide.",
    "BAD REQUEST": "Requête incomplète.",
    "ALREADY_CONNECTED": "Ce compte est déjà connecté sur un autre appareil ou sur le site.",
    "SUBSCRIPTION CANCELLED": "Cet abonnement a été résilié. Contactez KaronlineLive pour le réactiver.",
}


class AuthError(Exception):
    """Erreur applicative d'authentification avec un code machine."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = str(code)


def normalize_email(value: str) -> str:
    return (value or "").strip().casefold()


def mask_card(raw_value: str) -> tuple[str, str]:
    """Retourne (marque, 4 derniers chiffres) ou ('', '') si vide/invalide."""
    digits = re.sub(r"\D", "", str(raw_value or ""))
    if len(digits) < 12 or len(digits) > 19:
        return "", ""
    if digits.startswith("4"):
        brand = "Visa"
    elif digits.startswith(("51", "52", "53", "54", "55")):
        brand = "Mastercard"
    else:
        brand = ""
    return brand, digits[-4:]


def request_json(path: str, payload: dict | None = None,
                 token: str | None = None, timeout: int = 8) -> dict:
    """Appel JSON vers l'API centrale ; lève AuthError sur échec applicatif."""
    body = None
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0"}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = urllib.request.Request(
        f"{CENTRAL_BASE_URL}{path}", data=body, headers=headers,
        method="POST" if payload is not None else "GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status == 204:
                return {}
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8"))
            code = str(detail.get("error") or f"HTTP_{exc.code}")
        except Exception:
            code = f"HTTP_{exc.code}"
        message = USER_MESSAGES.get(code, f"Erreur {exc.code} : {code}")
        raise AuthError(code, message) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AuthError(
            "NETWORK",
            f"Impossible de joindre {CENTRAL_BASE_URL} ({exc}). "
            "Vérifiez votre connexion Internet.",
        ) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("PROTOCOL", "Réponse illisible du serveur.") from exc


# ---- API de bas niveau (utilisables seules, sans instance client) -----
def api_register(email: str, password: str, card: str = "") -> dict:
    clean = normalize_email(email)
    if not EMAIL_RE.match(clean):
        raise AuthError("INVALID EMAIL", "Adresse mail invalide.")
    if len(password or "") < 8:
        raise AuthError(
            "WEAK PASSWORD", "Mot de passe trop court (8 caractères minimum)."
        )
    brand, last4 = ("", "")
    if (card or "").strip():
        brand, last4 = mask_card(card)
        if not brand and not last4:
            raise AuthError("CARD INVALID", "Numéro de carte invalide.")
    data = request_json("/auth/register", {
        "email": clean,
        "password": password,
        "card_brand": brand,
        "card_last4": last4,
    })
    return {
        "token": data.get("token", ""),
        "email": data.get("email", clean),
        "card_label": data.get("card_label") or (f"{brand} ••••{last4}" if last4 else ""),
        "verification_required": bool(data.get("verification_required")),
        "code": data.get("code"),
    }


def api_login(email: str, password: str, force: bool = False) -> dict:
    payload = {
        "email": normalize_email(email),
        "password": password,
    }
    if force:
        payload["force"] = True
    data = request_json("/auth/login", payload)
    return {
        "token": data.get("token", ""),
        "email": data.get("email", normalize_email(email)),
        "card_label": data.get("card_label", ""),
    }


def api_logout(token: str) -> None:
    request_json("/auth/logout", {}, token=token, timeout=5)


def api_me(token: str) -> dict:
    return request_json("/auth/me", token=token, timeout=8)


class CentralAuthClient:
    """Jeton persistant du KJ dans QSettings (favoris/réglages : locaux)."""

    def __init__(self, settings):
        self.settings = settings
        self.token = (settings.value("auth/token", "") or "") if settings else ""
        self.email = (settings.value("auth/email", "") or "") if settings else ""
        self.card_label = (
            (settings.value("auth/card", "") or "") if settings else ""
        )

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token)

    def cached_account(self) -> dict:
        return {
            "logged_in": self.is_authenticated,
            "email": self.email,
            "card": self.card_label,
        }

    def save_session(self, token: str, email: str, card: str = "") -> None:
        self.token = token or ""
        self.email = email or ""
        self.card_label = card or ""
        if self.settings is not None:
            self.settings.setValue("auth/token", self.token)
            self.settings.setValue("auth/email", self.email)
            self.settings.setValue("auth/card", self.card_label)

    def clear(self) -> None:
        self.save_session("", "", "")

    def authorization_header(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    # --- opérations réseau : à appeler hors thread UI --------------------
    def register(self, email: str, password: str, card: str = "") -> dict:
        result = api_register(email, password, card)
        if result.get("token"):
            self.save_session(result["token"], result["email"],
                              result.get("card_label", ""))
        else:
            self.clear()
        return result

    def login(self, email: str, password: str, force: bool = False) -> dict:
        result = api_login(email, password, force=force)
        self.save_session(result["token"], result["email"],
                          result.get("card_label", ""))
        return result

    def logout(self) -> None:
        if self.token:
            try:
                api_logout(self.token)
            except AuthError:
                pass
        self.clear()

    def verify_stored_token(self) -> dict:
        """Confirme que le jeton sauvegardé est toujours valide."""
        data = api_me(self.token)
        self.save_session(
            data.get("token", self.token),
            data.get("email", self.email),
            data.get("card_label", self.card_label),
        )
        return data
