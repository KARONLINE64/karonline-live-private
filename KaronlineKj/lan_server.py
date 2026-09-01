from __future__ import annotations

import argparse
import json
import ipaddress
import re
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "SERVER"

# Annuaire de sessions en memoire : nom choisi par le KJ -> etat de sa
# session (mode relais = connexion sortante uniquement, aucun tunnel/port
# entrant requis chez le KJ). Sert uniquement sur l'instance centrale
# (api.karonlinelive.com).
SESSION_TTL_SECONDS = 24 * 3600
SESSION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,39}$")

# Files d'attente de jobs (relais long-polling) : job_id -> etat. Chaque
# session possede une file "queue" de job_id en attente d'etre tires par
# KaronlineBox via /relay/pull, puis le resultat est depose via /relay/push.
RELAY_JOBS: dict[str, dict] = {}
_RELAY_LOCK = threading.RLock()
RELAY_PULL_TIMEOUT_SECONDS = 25
RELAY_JOB_TIMEOUT_SECONDS = 12

# ---------------------------------------------------------------------------
# Comptes KJ (identifiant = adresse mail). Phase de test amis/famille :
# stockage fichier JSON + PBKDF2, sans dépendance externe. Aucun numéro de
# carte n'est jamais stocké côté serveur (marque + 4 derniers chiffres max).
import hashlib  # noqa: E402
import hmac  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402

DATA_DIR = Path(os.environ.get("KL_DATA_DIR",
                               Path(__file__).resolve().parent))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
ACCOUNTS_PATH = DATA_DIR / "accounts.json"
TOKENS_PATH = DATA_DIR / "tokens.json"
SESSIONS_PATH = DATA_DIR / "sessions.json"
TOKEN_TTL_SECONDS = 30 * 24 * 3600
PBKDF2_ITERATIONS = 120_000

# RLock : les fonctions se composent entre elles (ex. auth_create_account
# tient le verrou puis appelle _accounts qui le reprenne) -> réentrant requis.
_AUTH_LOCK = threading.RLock()
_DUO_LOCK = threading.RLock()
_ACCOUNTS: dict[str, dict] | None = None
_TOKENS: dict[str, dict] | None = None
_VERIFICATIONS: dict[str, dict] = {}
SESSIONS: dict[str, dict] = {}
DUO_SESSIONS: dict[str, dict] = {}

try:
    _load_sessions_on_startup()
except Exception:
    pass


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _save_json(path: Path, value) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(path)


def _load_sessions_on_startup():
    global SESSIONS
    with _RELAY_LOCK:
        data = _load_json(SESSIONS_PATH, {})
        if isinstance(data, dict):
            now = time.time()
            for name, entry in data.items():
                if isinstance(entry, dict) and now - entry.get("ts", 0) <= SESSION_TTL_SECONDS:
                    entry["queue"] = []
                    SESSIONS[name] = entry


def _save_sessions():
    with _RELAY_LOCK:
        serializable = {}
        now = time.time()
        for name, entry in SESSIONS.items():
            if now - entry.get("ts", 0) <= SESSION_TTL_SECONDS:
                serializable[name] = {
                    "ts": entry.get("ts"),
                    "owner": entry.get("owner"),
                    "host_url": entry.get("host_url"),
                }
        _save_json(SESSIONS_PATH, serializable)


def _accounts() -> dict[str, dict]:
    global _ACCOUNTS
    with _AUTH_LOCK:
        if _ACCOUNTS is None:
            data = _load_json(ACCOUNTS_PATH, {})
            _ACCOUNTS = data if isinstance(data, dict) else {}
        return _ACCOUNTS


def _tokens() -> dict[str, dict]:
    global _TOKENS
    with _AUTH_LOCK:
        if _TOKENS is None:
            data = _load_json(TOKENS_PATH, {})
            _TOKENS = data if isinstance(data, dict) else {}
            now = time.time()
            for stale in [t for t, meta in _TOKENS.items()
                          if now - meta.get("ts", 0) > TOKEN_TTL_SECONDS]:
                _TOKENS.pop(stale, None)
        return _TOKENS


def _hash_password(password: str, salt_hex: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        bytes.fromhex(salt_hex), PBKDF2_ITERATIONS,
    ).hex()


def auth_create_account(email: str, password: str,
                        card_brand: str = "", card_last4: str = "") -> str | None:
    """Crée le compte et retourne l'email normalisé ; None si déjà pris."""
    now = time.time()
    salt = secrets.token_hex(16)
    record = {
        "email": email,
        "salt": salt,
        "password_hash": _hash_password(password, salt),
        "iterations": PBKDF2_ITERATIONS,
        "card_brand": str(card_brand or "")[:32],
        "card_last4": "".join(ch for ch in str(card_last4 or "")
                              if ch.isdigit())[-4:],
        "created": int(now),
        "verified": False,
    }
    with _AUTH_LOCK:
        accounts = _accounts()
        if email in accounts:
            return None
        accounts[email] = record
        _save_json(ACCOUNTS_PATH, accounts)
    return email


def _verification_hash(code: str) -> str:
    return hashlib.sha256(str(code or "").strip().encode("utf-8")).hexdigest()


def _generate_verification_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _send_verification_email(email: str, code: str) -> tuple[bool, str]:
    api_key = os.environ.get("KL_BREVO_API_KEY") or os.environ.get("BREVO_API_KEY") or ""
    if not api_key:
        if os.environ.get("KL_DEBUG_VERIFY_CODE", "0").lower() in {"1", "true", "yes", "on"}:
            print(f"VERIFY CODE = {email} {code}", flush=True)
            return True, "debug"
        return False, "NO_BREVO_API_KEY"

    sender_email = (
        os.environ.get("KL_BREVO_SENDER_EMAIL")
        or os.environ.get("BREVO_SENDER_EMAIL")
        or "noreply@karonlinelive.com"
    )
    payload = {
        "sender": {"name": "KaronlineLive", "email": sender_email},
        "to": [{"email": email, "name": email}],
        "subject": "Votre code de vérification KaronlineLive",
        "htmlContent": (
            f"<p>Votre code de vérification est <strong>{code}</strong>.</p>"
            f"<p>Il expire dans 10 minutes.</p>"
        ),
        "textContent": f"Votre code de vérification KaronlineLive est {code}. Il expire dans 10 minutes.",
    }
    request = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status < 400, response.read().decode("utf-8", errors="replace")
    except Exception as exc:  # pragma: no cover - réseau / config externe
        print(f"BREVO ERROR = {exc}", flush=True)
        return False, str(exc)


def _issue_verification_code(email: str) -> str:
    code = _generate_verification_code()
    with _AUTH_LOCK:
        _VERIFICATIONS[email] = {
            "code_hash": _verification_hash(code),
            "expires_at": time.time() + 600,
        }
    return code


def auth_verify_credentials(email: str, password: str) -> dict | None:
    record = _accounts().get(email)
    if not record:
        return None
    expected = record.get("password_hash", "")
    candidate = _hash_password(password, record.get("salt", ""))
    if not hmac.compare_digest(expected, candidate):
        return None
    return record


def auth_issue_token(email: str, kind: str = "site") -> str:
    token = secrets.token_urlsafe(32)
    with _AUTH_LOCK:
        _tokens()[token] = {"email": email, "ts": time.time(), "kind": kind}
        _save_json(TOKENS_PATH, _tokens())
    return token


def auth_revoke_token(token: str) -> None:
    with _AUTH_LOCK:
        if token in _tokens():
            _tokens().pop(token)
            _save_json(TOKENS_PATH, _tokens())


def auth_has_active_token(email: str, kind: str) -> bool:
    """Un seul appareil connecte a la fois par compte ET par type de client
    (site web / KaronlineBox) : les deux doivent pouvoir etre connectes en
    meme temps avec les memes identifiants (c'est la paire requise), mais pas
    deux navigateurs, ni deux KaronlineBox, simultanement."""
    with _AUTH_LOCK:
        return any(meta.get("email") == email and meta.get("kind", "site") == kind
                   for meta in _tokens().values())


def auth_revoke_tokens_for_email(email: str, kind: str | None = None) -> None:
    """kind=None revoque tout (site + desktop), ex. reset de mot de passe."""
    with _AUTH_LOCK:
        stale = [t for t, meta in _tokens().items()
                 if meta.get("email") == email
                 and (kind is None or meta.get("kind", "site") == kind)]
        for token in stale:
            _tokens().pop(token, None)
        if stale:
            _save_json(TOKENS_PATH, _tokens())


def auth_set_password(email: str, new_password: str) -> None:
    with _AUTH_LOCK:
        record = _accounts().get(email)
        if record is None:
            return
        salt = secrets.token_hex(16)
        record["salt"] = salt
        record["password_hash"] = _hash_password(new_password, salt)
        _save_json(ACCOUNTS_PATH, _accounts())
    auth_revoke_tokens_for_email(email)


def auth_resolve_token(token: str) -> str | None:
    """Retourne l'email propriétaire si le jeton est valide, sinon None."""
    with _AUTH_LOCK:
        meta = _tokens().get(token)
    if not meta:
        return None
    if time.time() - meta.get("ts", 0) > TOKEN_TTL_SECONDS:
        auth_revoke_token(token)
        return None
    return str(meta.get("email") or "")


def auth_card_label(record: dict) -> str:
    brand, last4 = record.get("card_brand", ""), record.get("card_last4", "")
    return f"{brand} ••••{last4}" if last4 else ""



def detect_lan_ip():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return socket.gethostbyname(socket.gethostname())
    finally:
        probe.close()


# ---------------------------------------------------------------------------
# Relais central (mode "outbound-only") : KaronlineBox n'ouvre plus aucun
# port entrant ni tunnel. Il tire (long-poll) les jobs en attente pour sa
# session via /relay/pull, les execute localement, puis renvoie le resultat
# via /relay/push. Les clients mobiles/PC declenchent un job via
# /session/<nom>/catalogue ou /session/<nom>/request-demand et attendent
# (bloquant, avec timeout) que KaronlineBox le resolve.
def _relay_run_job(name: str, job_type: str, payload: dict) -> tuple[int, dict]:
    entry = SESSIONS.get(name)
    if entry is None or time.time() - entry["ts"] > SESSION_TTL_SECONDS:
        SESSIONS.pop(name, None)
        return 404, {"error": "SESSION NOT FOUND"}

    job_id = uuid.uuid4().hex
    event = threading.Event()
    job = {
        "job_id": job_id, "session": name, "type": job_type,
        "payload": payload, "event": event, "status": None, "body": None,
    }
    with _RELAY_LOCK:
        RELAY_JOBS[job_id] = job
        entry.setdefault("queue", []).append(job_id)

    resolved = event.wait(timeout=RELAY_JOB_TIMEOUT_SECONDS)
    with _RELAY_LOCK:
        RELAY_JOBS.pop(job_id, None)

    if not resolved:
        return 504, {"error": "KARONLINEBOX TIMEOUT"}
    return job["status"] or 500, job["body"] if job["body"] is not None else {}


def _relay_pull(name: str):
    entry = SESSIONS.get(name)
    if entry is None:
        return None
    entry["ts"] = time.time()  # heartbeat : garde la session vivante

    deadline = time.time() + RELAY_PULL_TIMEOUT_SECONDS
    while time.time() < deadline:
        with _RELAY_LOCK:
            queue = entry.setdefault("queue", [])
            if queue:
                job_id = queue.pop(0)
                job = RELAY_JOBS.get(job_id)
                if job is not None:
                    return job
                continue
        time.sleep(0.3)
    return None


def _relay_push(job_id: str, owner: str, status: int, body: dict) -> bool:
    with _RELAY_LOCK:
        job = RELAY_JOBS.get(job_id)
        if job is None:
            return False
        entry = SESSIONS.get(job["session"])
        if entry is None or entry.get("owner") != owner:
            return False
        job["status"] = status
        job["body"] = body
    job["event"].set()
    return True


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "KaronlineLAN/1.0"

    def log_message(self, format, *args):
        return

    def _send_cors_headers(self):
        """Autoriser le site local, reseau prive, Tailscale, et domaine public karonlinelive.com."""
        origin = self.headers.get("Origin", "")
        if not origin or origin == "null":
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
            return

        parsed = urlparse(origin)
        hostname = parsed.hostname or ""
        allow_origin = False

        try:
            origin_ip = ipaddress.ip_address(hostname)
            is_private_origin = (
                origin_ip.is_private
                or origin_ip in ipaddress.ip_network("100.64.0.0/10")
            )
            # Allow HTTP from private networks and Tailscale
            if parsed.scheme == "http" and is_private_origin:
                allow_origin = True
        except ValueError:
            # Allow localhost
            if hostname in {"localhost", "127.0.0.1", "::1"}:
                allow_origin = True
            # Allow HTTPS from karonlinelive.com domains
            elif parsed.scheme == "https" and (
                hostname in {"karonlinelive.com", "www.karonlinelive.com"}
                or hostname.endswith(".karonlinelive.com")
            ):
                allow_origin = True

        if allow_origin:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        else:
            self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self):
        """Gérer les requêtes de préflight CORS"""
        path = urlparse(self.path).path
        if path in {"/catalogue", "/request-demand", "/download/karonlinebox",
                    "/session/register", "/auth/register", "/auth/login",
                    "/auth/verify", "/auth/resend", "/auth/logout",
                    "/auth/forgot", "/auth/reset",
                    "/relay/pull", "/relay/push"} \
                or path.startswith("/session/") or path.startswith("/auth/"):
            self.send_response(200)
            self._send_cors_headers()
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _bearer_email(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return None
        return auth_resolve_token(header[7:].strip())

    def do_GET(self):
        path = urlparse(self.path).path

        # Endpoint: GET /auth/me - profil du compte attache au jeton envoye
        if path == "/auth/me":
            email = self._bearer_email()
            if not email or email not in _accounts():
                self._send_json(401, {"error": "TOKEN INVALID"})
                return
            record = _accounts()[email]
            self._send_json(200, {
                "email": email,
                "card_label": auth_card_label(record),
            })
            return

        # Endpoint: GET /auth/session-pair-status - le jeton desktop appelant
        # est valide ; indique en plus si une session "site" (navigateur) est
        # aussi active pour ce meme compte (paire requise pour demarrer une
        # session KaronlineBox).
        if path == "/auth/session-pair-status":
            email = self._bearer_email()
            if not email or email not in _accounts():
                self._send_json(401, {"error": "TOKEN INVALID"})
                return
            self._send_json(200, {"site_active": auth_has_active_token(email, "site")})
            return

        # Endpoint: GET /duo/status - statut d'une session DUO (invité connecté, synchro master clock, frames webcam)
        if path == "/duo/status":
            requester = self._bearer_email()
            if not requester or "KaronlineBox" not in self.headers.get("User-Agent", ""):
                self._send_json(401, {"error": "DESKTOP AUTH REQUIRED"})
                return
            query = urlparse(self.path).query
            params = dict(pair.split("=", 1) for pair in query.split("&") if "=" in pair)
            code = unquote(params.get("code", "")).strip().upper()
            with _DUO_LOCK:
                entry = DUO_SESSIONS.get(code)
                if not entry or time.time() - entry.get("ts", 0) > 24 * 3600:
                    DUO_SESSIONS.pop(code, None)
                    self._send_json(404, {"error": "DUO SESSION NOT FOUND"})
                    return
                guest = entry.get("guest") or {}
                if requester not in {entry.get("owner"), guest.get("email")}:
                    self._send_json(403, {"error": "DUO ACCESS DENIED"})
                    return
                self._send_json(200, {
                    "code": code,
                    "guest": entry.get("guest"),
                    "sync": entry.get("sync"),
                    "guest_frame": entry.get("guest_frame"),
                    "host_frame": entry.get("host_frame"),
                })
            return

        # Endpoint: GET /session/<nom> - resoudre un nom de session (legacy :
        # renvoie host_url si l'hote expose un tunnel ; sinon mode relais).
        # Endpoint: GET /session/<nom>/catalogue - relais long-polling du
        # catalogue de l'hote (aucun port entrant requis chez le KJ).
        if path.startswith("/session/"):
            remainder = unquote(path[len("/session/"):]).strip()
            parts = remainder.split("/", 1)
            name = parts[0].casefold()
            entry = SESSIONS.get(name)
            if entry is None or time.time() - entry["ts"] > SESSION_TTL_SECONDS:
                SESSIONS.pop(name, None)
                self._send_json(404, {"error": "SESSION NOT FOUND"})
                return

            if len(parts) == 1:
                if entry.get("host_url"):
                    self._send_json(200, {"host_url": entry["host_url"]})
                else:
                    self._send_json(200, {"relay": True})
                return

            if parts[1] == "catalogue":
                status, body = _relay_run_job(name, "catalogue", {})
                self._send_json(status, body)
                return

            self._send_json(404, {"error": "NOT FOUND"})
            return

        # Endpoint: GET /relay/pull - KaronlineBox tire (long-poll) le prochain
        # job en attente pour sa session. Requiert une session authentifiee.
        if path == "/relay/pull":
            owner = self._bearer_email()
            if not owner:
                self._send_json(401, {"error": "AUTH REQUIRED"})
                return
            query = urlparse(self.path).query
            params = dict(pair.split("=", 1) for pair in query.split("&") if "=" in pair)
            name = unquote(params.get("name", "")).strip().casefold()
            entry = SESSIONS.get(name)
            if entry is None or entry.get("owner") != owner:
                self._send_json(404, {"error": "SESSION NOT FOUND"})
                return
            job = _relay_pull(name)
            if job is None:
                self._send_json(200, {"job_id": None})
                return
            self._send_json(200, {
                "job_id": job["job_id"], "type": job["type"], "payload": job["payload"],
            })
            return

        # Endpoint: GET /catalogue
        if path == "/catalogue":
            songs = []
            for file_path in self.server.library.iterdir():
                if not file_path.is_file() or file_path.suffix.casefold() != ".mp4":
                    continue
                artist, separator, title = file_path.stem.partition("-")
                songs.append({
                    "artist": artist.strip() if separator else "Artiste inconnu",
                    "title": title.strip() if separator else artist.strip(),
                    "filename": file_path.name,
                })
            songs.sort(key=lambda song: (song["artist"].casefold(), song["title"].casefold()))
            self._send_json(200, songs)
            return
        
        # Endpoint: GET /download/karonlinebox - Télécharger l'installateur KaronlineBox
        if path == "/download/karonlinebox":
            # Chercher le fichier setup dans les emplacements courants
            setup_paths = [
                self.server.library.parent / "KaronlineBox_Install" / "KaronlineBox_Installer.exe",
                self.server.library.parent / "KaronlineKj" / "karonlinebox_setup.exe",
                Path.cwd() / "karonlinebox_setup.exe",
                Path.cwd().parent / "karonlinebox_setup.exe",
                self.server.library.parent / "KaronlineKj" / "setup.exe",
                Path.cwd() / "setup.exe",
                Path.cwd().parent / "setup.exe",
                Path.cwd() / "KaronlineBox_V90_Setup.exe",
                self.server.library.parent / "KaronlineBox_V90_Setup.exe",
            ]
            
            setup_file = None
            for path_candidate in setup_paths:
                if path_candidate.is_file():
                    setup_file = path_candidate
                    break
            
            if setup_file is None:
                self._send_json(404, {"error": "KaronlineBox installer not found"})
                return
            
            # Envoyer le fichier exe
            file_size = setup_file.stat().st_size
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/x-msdownload")
                self.send_header("Content-Disposition", f'attachment; filename="{setup_file.name}"')
                self.send_header("Content-Length", str(file_size))
                self._send_cors_headers()
                self.end_headers()
                with setup_file.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        self.wfile.write(chunk)
                print(f"DOWNLOAD KARONLINEBOX = {setup_file.name}", flush=True)
            except (BrokenPipeError, ConnectionResetError, OSError) as exc:
                print(f"DOWNLOAD ERROR = {exc}", flush=True)
            return
        
        self._send_json(404, {"error": "NOT FOUND"})

    def do_POST(self):
        if self.path == "/auth/register":
            self._auth_register()
            return
        if self.path == "/auth/verify":
            self._auth_verify()
            return
        if self.path == "/auth/resend":
            self._auth_resend()
            return
        if self.path == "/auth/login":
            self._auth_login()
            return
        if self.path == "/auth/forgot":
            self._auth_forgot()
            return
        if self.path == "/auth/reset":
            self._auth_reset()
            return
        if self.path == "/auth/logout":
            self._auth_logout()
            return
        if self.path == "/session/register":
            self._register_session()
            return
        if self.path == "/session/unregister":
            self._unregister_session()
            return
        if self.path == "/duo/create":
            self._duo_create()
            return
        if self.path == "/duo/join":
            self._duo_join()
            return
        if self.path == "/duo/frame":
            self._duo_frame()
            return
        if self.path == "/duo/sync":
            self._duo_sync()
            return
        if self.path == "/duo/close":
            self._duo_close()
            return
        if self.path == "/request-demand":
            self._relay_demand()
            return
        if self.path == "/relay/push":
            self._relay_push_endpoint()
            return
        path = urlparse(self.path).path
        if path.startswith("/session/") and path.endswith("/request-demand"):
            self._relay_mobile_demand(path)
            return
        if self.path != "/request":
            self._send_json(404, {"error": "NOT FOUND"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            title = str(request.get("title", "")).strip()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        print(f"REQUEST RECEIVED = {title}", flush=True)
        library = self.server.library
        requested = Path(unquote(title)).name
        matches = {
            path.name.casefold(): path
            for path in library.iterdir()
            if path.is_file() and path.suffix.casefold() == ".mp4"
        }
        file_path = matches.get(requested.casefold())
        if file_path is None and not requested.casefold().endswith(".mp4"):
            file_path = matches.get(f"{requested}.mp4".casefold())

        if file_path is None:
            print("FILE NOT FOUND", flush=True)
            self._send_json(404, {"error": "FILE NOT FOUND"})
            return

        file_size = file_path.stat().st_size
        print(f"FILE FOUND = {file_path.name}", flush=True)
        print(f"FILE SIZE = {file_size}", flush=True)
        print("TRANSFER STARTED", flush=True)
        try:
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
            self.send_header("Content-Length", str(file_size))
            self.end_headers()
            with file_path.open("rb") as source:
                while chunk := source.read(1024 * 1024):
                    self.wfile.write(chunk)
            print("TRANSFER COMPLETED", flush=True)
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            print(f"TRANSFER ERROR = {exc}", flush=True)

    # ------------------------------------------------------------------
    # Comptes KJ : enregistrer / se connecter / se deconnecter
    # ------------------------------------------------------------------
    def _auth_register(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "BAD REQUEST"})
            return

        email = str(payload.get("email", "")).strip().casefold()
        password = str(payload.get("password", ""))
        if "@" not in email or len(email) < 6 \
                or "." not in email.rsplit("@", 1)[-1]:
            self._send_json(400, {"error": "INVALID EMAIL"})
            return
        if len(password) < 8:
            self._send_json(400, {"error": "WEAK PASSWORD"})
            return

        created = auth_create_account(
            email, password,
            str(payload.get("card_brand", "")),
            str(payload.get("card_last4", "")),
        )
        if created is None:
            self._send_json(409, {"error": "EMAIL TAKEN"})
            return

        code = _issue_verification_code(email)
        sent, detail = _send_verification_email(email, code)
        if not sent:
            print(f"VERIFY EMAIL FAILED = {email} ; {detail}", flush=True)
        print(f"ACCOUNT CREATED = {email} | VERIFY_CODE = {code}", flush=True)
        self._send_json(202, {
            "verification_required": True,
            "email": email,
            "card_label": auth_card_label(_accounts()[email]),
            "message": "Code de vérification envoyé par e-mail.",
            "code": code if os.environ.get("KL_DEBUG_VERIFY_CODE", "0").lower() in {"1", "true", "yes", "on"} else None,
        })

    def _auth_verify(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "BAD REQUEST"})
            return

        email = str(payload.get("email", "")).strip().casefold()
        code = str(payload.get("code", "")).strip()
        record = _accounts().get(email)
        if record is None:
            self._send_json(404, {"error": "EMAIL NOT FOUND"})
            return

        info = _VERIFICATIONS.get(email)
        if not info:
            self._send_json(400, {"error": "NO CODE SENT"})
            return

        if time.time() > float(info.get("expires_at", 0)):
            _VERIFICATIONS.pop(email, None)
            self._send_json(410, {"error": "CODE EXPIRED"})
            return

        if not hmac.compare_digest(info.get("code_hash", ""), _verification_hash(code)):
            self._send_json(401, {"error": "INVALID CODE"})
            return

        record["verified"] = True
        _VERIFICATIONS.pop(email, None)
        _save_json(ACCOUNTS_PATH, _accounts())
        self._send_json(200, {
            "token": auth_issue_token(email),
            "email": email,
            "card_label": auth_card_label(record),
            "verified": True,
        })

    def _auth_resend(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "BAD REQUEST"})
            return

        email = str(payload.get("email", "")).strip().casefold()
        record = _accounts().get(email)
        if record is None:
            self._send_json(404, {"error": "EMAIL NOT FOUND"})
            return

        code = _issue_verification_code(email)
        sent, detail = _send_verification_email(email, code)
        if not sent:
            print(f"RESEND VERIFY EMAIL FAILED = {email} ; {detail}", flush=True)
        self._send_json(200, {
            "verification_required": True,
            "email": email,
            "message": "Nouveau code de vérification envoyé.",
            "code": code if os.environ.get("KL_DEBUG_VERIFY_CODE", "0").lower() in {"1", "true", "yes", "on"} else None,
        })

    def _auth_login(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "BAD REQUEST"})
            return

        email = str(payload.get("email", "")).strip().casefold()
        password = str(payload.get("password", ""))
        force = bool(payload.get("force"))
        # KaronlineBox et le site web doivent pouvoir etre connectes en meme
        # temps avec les memes identifiants (paire requise, ex. verification
        # d'identite avant liaison d'une carte bancaire) : la limite "un seul
        # appareil a la fois" s'applique par type de client, pas globalement.
        kind = "desktop" if "KaronlineBox" in self.headers.get("User-Agent", "") else "site"
        record = auth_verify_credentials(email, password)
        if record is None:
            print(f"LOGIN FAILED = {email}", flush=True)
            self._send_json(401, {"error": "WRONG CREDENTIALS"})
            return
        if not record.get("verified", False):
            print(f"LOGIN BLOCKED UNTIL VERIFICATION = {email}", flush=True)
            self._send_json(403, {"error": "EMAIL NOT VERIFIED"})
            return
        if auth_has_active_token(email, kind) and not force:
            print(f"LOGIN BLOCKED ALREADY CONNECTED = {email} ({kind})", flush=True)
            self._send_json(409, {"error": "ALREADY_CONNECTED"})
            return
        auth_revoke_tokens_for_email(email, kind)

        print(f"LOGIN OK = {email} ({kind})", flush=True)
        self._send_json(200, {
            "token": auth_issue_token(email, kind),
            "email": email,
            "card_label": auth_card_label(record),
        })

    def _auth_logout(self):
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            auth_revoke_token(header[7:].strip())
        self._send_json(200, {"status": "ok"})

    def _auth_forgot(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "BAD REQUEST"})
            return

        email = str(payload.get("email", "")).strip().casefold()
        record = _accounts().get(email)
        if record is None:
            # Ne pas reveler si le compte existe : reponse generique.
            self._send_json(200, {"status": "ok"})
            return

        code = _issue_verification_code(email)
        sent, detail = _send_verification_email(email, code)
        if not sent:
            print(f"RESET EMAIL FAILED = {email} ; {detail}", flush=True)
        self._send_json(200, {
            "status": "ok",
            "code": code if os.environ.get("KL_DEBUG_VERIFY_CODE", "0").lower() in {"1", "true", "yes", "on"} else None,
        })

    def _auth_reset(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "BAD REQUEST"})
            return

        email = str(payload.get("email", "")).strip().casefold()
        code = str(payload.get("code", "")).strip()
        new_password = str(payload.get("password", ""))
        record = _accounts().get(email)
        if record is None:
            self._send_json(404, {"error": "EMAIL NOT FOUND"})
            return

        info = _VERIFICATIONS.get(email)
        if not info:
            self._send_json(400, {"error": "NO CODE SENT"})
            return
        if time.time() > float(info.get("expires_at", 0)):
            _VERIFICATIONS.pop(email, None)
            self._send_json(410, {"error": "CODE EXPIRED"})
            return
        if not hmac.compare_digest(info.get("code_hash", ""), _verification_hash(code)):
            self._send_json(401, {"error": "INVALID CODE"})
            return
        if len(new_password) < 8:
            self._send_json(400, {"error": "WEAK PASSWORD"})
            return

        auth_set_password(email, new_password)
        _VERIFICATIONS.pop(email, None)
        print(f"PASSWORD RESET OK = {email}", flush=True)
        self._send_json(200, {"status": "ok"})

    def _register_session(self):
        """Enregistre/actualise un nom de session (annuaire).

        Requiert une session authentifiee : chaque nom rattache a son compte
        proprietaire, seul compte qui supportera plus tard les frais.
        Mode relais (par defaut, recommande) : aucun host_url requis, aucun
        tunnel/port entrant chez le KJ, KaronlineBox tire ses jobs via
        /relay/pull. Mode legacy (host_url fourni) : conserve pour
        compatibilite ascendante."""
        owner = self._bearer_email()
        if not owner:
            self._send_json(401, {"error": "AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            request = json.loads(body.decode("utf-8"))
            name = str(request.get("name", "")).strip().casefold()
            host_url = str(request.get("host_url", "")).strip().rstrip("/")
            force = bool(request.get("force"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        if not SESSION_NAME_RE.match(name):
            self._send_json(400, {"error": "INVALID SESSION NAME"})
            return

        now = time.time()
        other_name = next((n for n, e in SESSIONS.items()
                           if e.get("owner") == owner and n != name
                           and now - e.get("ts", 0) <= SESSION_TTL_SECONDS), None)
        if other_name and not force:
            self._send_json(409, {"error": "SESSION_ALREADY_EXISTS", "existing_name": other_name})
            return
        if other_name:
            SESSIONS.pop(other_name, None)

        entry = {"ts": time.time(), "owner": owner, "queue": []}
        if host_url:
            parsed_host = urlparse(host_url)
            if parsed_host.scheme != "https" or not parsed_host.hostname:
                self._send_json(400, {"error": "INVALID HOST URL"})
                return
            entry["host_url"] = host_url

        SESSIONS[name] = entry
        _save_sessions()
        print(f"SESSION REGISTERED = {name} "
              f"(owner={owner}, mode={'legacy' if host_url else 'relay'})", flush=True)
        self._send_json(200, {"status": "ok", "name": name, "owner": owner})

    def _unregister_session(self):
        owner = self._bearer_email()
        if not owner:
            self._send_json(401, {"error": "AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            name = str(request.get("name", "")).strip().casefold()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        entry = SESSIONS.get(name)
        if entry is None or entry.get("owner") != owner:
            self._send_json(404, {"error": "SESSION NOT FOUND"})
            return

        SESSIONS.pop(name, None)
        _save_sessions()
        self._send_json(200, {"status": "ok"})

    def _duo_create(self):
        owner = self._bearer_email()
        if not owner or "KaronlineBox" not in self.headers.get("User-Agent", ""):
            self._send_json(401, {"error": "DESKTOP AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            code = str(payload.get("code", "")).strip().upper()
            session_name = str(payload.get("session_name", "")).strip().casefold()
        except Exception:
            code, session_name = "", ""
        session = SESSIONS.get(session_name)
        if not code or not session_name or not session or session.get("owner") != owner:
            self._send_json(400, {"error": "HOST SESSION REQUIRED"})
            return
        if time.time() - session.get("ts", 0) > SESSION_TTL_SECONDS:
            SESSIONS.pop(session_name, None)
            self._send_json(400, {"error": "HOST SESSION REQUIRED"})
            return
        with _DUO_LOCK:
            DUO_SESSIONS[code] = {
                "code": code,
                "ts": time.time(),
                "guest": None,
                "sync": None,
                "owner": owner,
                "session_name": session_name,
            }
        print(f"DUO SESSION CREATED = {code}", flush=True)
        self._send_json(200, {"code": code})

    def _duo_join(self):
        guest_email = self._bearer_email()
        if not guest_email or "KaronlineBox" not in self.headers.get("User-Agent", ""):
            self._send_json(401, {"error": "DESKTOP AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            code = str(payload.get("code", "")).strip().upper()
            guest_name = str(payload.get("guest_name", "Invité")).strip()
        except Exception:
            code, guest_name = "", "Invité"
        if not code:
            self._send_json(400, {"error": "INVALID CODE"})
            return
        with _DUO_LOCK:
            entry = DUO_SESSIONS.get(code)
            if not entry:
                self._send_json(404, {"error": "DUO SESSION NOT FOUND"})
                return
            entry["guest"] = {
                "name": guest_name,
                "email": guest_email,
                "connected_at": time.time(),
            }
            entry["ts"] = time.time()
        print(f"DUO GUEST JOINED = {code} ({guest_name})", flush=True)
        self._send_json(200, {"status": "ok", "code": code})

    def _duo_frame(self):
        sender = self._bearer_email()
        if not sender or "KaronlineBox" not in self.headers.get("User-Agent", ""):
            self._send_json(401, {"error": "DESKTOP AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            code = str(payload.get("code", "")).strip().upper()
            role = str(payload.get("role", "guest")).strip().lower()
            frame = str(payload.get("frame", "")).strip()
        except Exception:
            code, role, frame = "", "guest", ""
        if code and frame:
            with _DUO_LOCK:
                entry = DUO_SESSIONS.get(code)
                if not entry:
                    self._send_json(404, {"error": "DUO SESSION NOT FOUND"})
                    return
                guest = entry.get("guest") or {}
                allowed_role = "host" if sender == entry.get("owner") else "guest"
                if sender != entry.get("owner") and sender != guest.get("email"):
                    self._send_json(403, {"error": "DUO ACCESS DENIED"})
                    return
                if role != allowed_role:
                    self._send_json(403, {"error": "DUO ROLE DENIED"})
                    return
                if allowed_role == "host":
                    entry["host_frame"] = frame
                else:
                    entry["guest_frame"] = frame
                entry["ts"] = time.time()
        self._send_json(200, {"status": "ok"})

    def _duo_sync(self):
        owner = self._bearer_email()
        if not owner or "KaronlineBox" not in self.headers.get("User-Agent", ""):
            self._send_json(401, {"error": "DESKTOP AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            code = str(payload.get("code", "")).strip().upper()
        except Exception:
            code = ""
        if code:
            with _DUO_LOCK:
                entry = DUO_SESSIONS.get(code)
                if not entry:
                    self._send_json(404, {"error": "DUO SESSION NOT FOUND"})
                    return
                if owner != entry.get("owner"):
                    self._send_json(403, {"error": "HOST CONTROL REQUIRED"})
                    return
                entry["sync"] = payload
                entry["ts"] = time.time()
        self._send_json(200, {"status": "ok"})

    def _duo_close(self):
        requester = self._bearer_email()
        if not requester or "KaronlineBox" not in self.headers.get("User-Agent", ""):
            self._send_json(401, {"error": "DESKTOP AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            code = str(payload.get("code", "")).strip().upper()
        except Exception:
            code = ""
        if code:
            with _DUO_LOCK:
                entry = DUO_SESSIONS.get(code)
                if not entry:
                    self._send_json(404, {"error": "DUO SESSION NOT FOUND"})
                    return
                if requester == entry.get("owner"):
                    DUO_SESSIONS.pop(code, None)
                    print(f"DUO SESSION CLOSED BY HOST = {code}", flush=True)
                elif requester == (entry.get("guest") or {}).get("email"):
                    entry["guest"] = None
                    entry.pop("guest_frame", None)
                    entry["ts"] = time.time()
                    print(f"DUO GUEST LEFT = {code}", flush=True)
                else:
                    self._send_json(403, {"error": "DUO ACCESS DENIED"})
                    return
        self._send_json(200, {"status": "ok"})

    def _relay_push_endpoint(self):
        """Endpoint POST /relay/push : KaronlineBox depose le resultat d'un
        job precedemment tire via /relay/pull."""
        owner = self._bearer_email()
        if not owner:
            self._send_json(401, {"error": "AUTH REQUIRED"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(length).decode("utf-8"))
            job_id = str(request.get("job_id", "")).strip()
            status = int(request.get("status", 500))
            resp_body = request.get("body") or {}
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        if not job_id or not _relay_push(job_id, owner, status, resp_body):
            self._send_json(404, {"error": "JOB NOT FOUND"})
            return
        self._send_json(200, {"status": "ok"})

    def _relay_mobile_demand(self, path: str):
        """Endpoint POST /session/<nom>/request-demand : un invite mobile/PC
        declenche une demande de chanson, relayee (long-poll) vers
        KaronlineBox via /relay/pull puis /relay/push."""
        name = unquote(path[len("/session/"):-len("/request-demand")]).strip().casefold()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        singer = str(payload.get("singer", "")).strip()
        artist = str(payload.get("artist", "")).strip()
        title = str(payload.get("title", "")).strip()
        try:
            key = int(payload.get("key", 0))
        except (TypeError, ValueError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return
        if not singer or not artist or not title or not -12 <= key <= 12:
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        status, body = _relay_run_job(name, "request-demand", {
            "singer": singer, "artist": artist, "title": title, "key": key,
        })
        self._send_json(status, body)

    def _relay_demand(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            request = json.loads(body.decode("utf-8"))
            singer = str(request.get("singer", "")).strip()
            artist = str(request.get("artist", "")).strip()
            title = str(request.get("title", "")).strip()
            key = int(request.get("key", 0))
            client_ip = str(request.get("client_ip", "")).strip()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        if not singer or not artist or not title or not -12 <= key <= 12:
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        client_ip = client_ip or self.client_address[0]
        try:
            parsed_client_ip = ipaddress.ip_address(client_ip)
        except ValueError:
            self._send_json(400, {"error": "INVALID CLIENT IP"})
            return
        is_tailscale_client = parsed_client_ip in ipaddress.ip_network("100.64.0.0/10")
        if not parsed_client_ip.is_private and not is_tailscale_client:
            self._send_json(400, {"error": "CLIENT IP MUST BE PRIVATE"})
            return
        print(f"DEMAND RECEIVED FROM = {client_ip}", flush=True)
        print(f"DEMAND = singer={singer}, artist={artist}, title={title}, key={key}", flush=True)
        
        # Toujours relayer vers KaronlineBox qui écoute sur 127.0.0.1:8766
        # Peu importe que la demande vienne du navigateur ou d'un client local
        # Test de connexion rapide d'abord: si KaronlineBox n'ecoute pas, on
        # repond tout de suite pour eviter la course avec le timeout Cloudflare.
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            probe.connect(("127.0.0.1", self.server.request_port))
            probe.close()
        except OSError as exc:
            probe.close()
            print(f"DEMAND RELAY ERROR = {exc}", flush=True)
            self._send_json(409, {"error": "KARONLINEBOX UNAVAILABLE"})
            return

        forward = json.dumps({
            "singer": singer,
            "artist": artist,
            "title": title,
            "key": key,
        }).encode("utf-8")
        target = urllib.request.Request(
            f"http://127.0.0.1:{self.server.request_port}/request",
            data=forward,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(target, timeout=2) as response:
                if response.status != 202:
                    raise RuntimeError(f"HTTP {response.status}")
            print(f"DEMAND RELAYED TO KARONLINEBOX = 127.0.0.1:{self.server.request_port}", flush=True)
            self._send_json(202, {"status": "RELAYED"})
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            # Si le relais vers KaronlineBox échoue
            print(f"DEMAND RELAY ERROR = {exc}", flush=True)
            self._send_json(409, {"error": "KARONLINEBOX UNAVAILABLE"})


def main():
    parser = argparse.ArgumentParser(description="Karonline LAN MP4 server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--library", type=Path, default=DEFAULT_LIBRARY)
    args = parser.parse_args()

    library = args.library.expanduser().resolve()
    if not library.is_dir():
        raise SystemExit(f"MUSIC FOLDER NOT FOUND = {library}")

    server = ThreadingHTTPServer((args.host, args.port), RequestHandler)
    server.library = library
    server.request_port = 8766
    print("SERVER STARTED", flush=True)
    print(f"SERVER IP = {detect_lan_ip() if args.host == '0.0.0.0' else args.host}", flush=True)
    print(f"SERVER PORT = {args.port}", flush=True)
    print(f"MUSIC FOLDER = {library}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("SERVER STOPPED", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()