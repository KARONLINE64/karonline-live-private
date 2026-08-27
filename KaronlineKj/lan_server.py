from __future__ import annotations

import argparse
import json
import ipaddress
import re
import socket
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "SERVER"

# Annuaire de sessions en memoire : nom choisi par le KJ -> URL de son tunnel.
# Sert uniquement sur l'instance centrale (api.karonlinelive.com).
SESSIONS: dict[str, dict] = {}
SESSION_TTL_SECONDS = 24 * 3600
SESSION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")

# ---------------------------------------------------------------------------
# Comptes KJ (identifiant = adresse mail). Phase de test amis/famille :
# stockage fichier JSON + PBKDF2, sans dépendance externe. Aucun numéro de
# carte n'est jamais stocké côté serveur (marque + 4 derniers chiffres max).
import hashlib  # noqa: E402
import hmac  # noqa: E402
import os  # noqa: E402
import secrets  # noqa: E402
import threading  # noqa: E402

DATA_DIR = Path(os.environ.get("KL_DATA_DIR",
                               Path(__file__).resolve().parent))
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
ACCOUNTS_PATH = DATA_DIR / "accounts.json"
TOKENS_PATH = DATA_DIR / "tokens.json"
TOKEN_TTL_SECONDS = 30 * 24 * 3600
PBKDF2_ITERATIONS = 120_000

# RLock : les fonctions se composent entre elles (ex. auth_create_account
# tient le verrou puis appelle _accounts qui le reprenne) -> réentrant requis.
_AUTH_LOCK = threading.RLock()
_ACCOUNTS: dict[str, dict] | None = None
_TOKENS: dict[str, dict] | None = None


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
    }
    with _AUTH_LOCK:
        accounts = _accounts()
        if email in accounts:
            return None
        accounts[email] = record
        _save_json(ACCOUNTS_PATH, accounts)
    return email


def auth_verify_credentials(email: str, password: str) -> dict | None:
    record = _accounts().get(email)
    if not record:
        return None
    expected = record.get("password_hash", "")
    candidate = _hash_password(password, record.get("salt", ""))
    if not hmac.compare_digest(expected, candidate):
        return None
    return record


def auth_issue_token(email: str) -> str:
    token = secrets.token_urlsafe(32)
    with _AUTH_LOCK:
        _tokens()[token] = {"email": email, "ts": time.time()}
        _save_json(TOKENS_PATH, _tokens())
    return token


def auth_revoke_token(token: str) -> None:
    with _AUTH_LOCK:
        if token in _tokens():
            _tokens().pop(token)
            _save_json(TOKENS_PATH, _tokens())


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


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "KaronlineLAN/1.0"

    def log_message(self, format, *args):
        return

    def _send_cors_headers(self):
        """Autoriser le site local, reseau prive, Tailscale, et domaine public karonlinelive.com."""
        origin = self.headers.get("Origin", "")
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
            if hostname == "localhost":
                allow_origin = True
            # Allow HTTPS from karonlinelive.com domain
            elif parsed.scheme == "https" and (hostname == "karonlinelive.com" or hostname.endswith(".karonlinelive.com")):
                allow_origin = True
        
        if allow_origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, HEAD")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def do_OPTIONS(self):
        """Gérer les requêtes de préflight CORS"""
        path = urlparse(self.path).path
        if path in {"/catalogue", "/request-demand", "/download/karonlinebox",
                    "/session/register", "/auth/register", "/auth/login",
                    "/auth/logout"} \
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

        # Endpoint: GET /session/<nom> - resoudre un nom de session vers l'URL de l'hote
        if path.startswith("/session/"):
            name = unquote(path[len("/session/"):]).strip().casefold()
            entry = SESSIONS.get(name)
            if entry is None or time.time() - entry["ts"] > SESSION_TTL_SECONDS:
                SESSIONS.pop(name, None)
                self._send_json(404, {"error": "SESSION NOT FOUND"})
                return
            self._send_json(200, {"host_url": entry["host_url"]})
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
                })
            songs.sort(key=lambda song: (song["artist"].casefold(), song["title"].casefold()))
            self._send_json(200, songs)
            return
        
        # Endpoint: GET /download/karonlinebox - Télécharger l'installateur KaronlineBox
        if path == "/download/karonlinebox":
            # Chercher le fichier setup dans les emplacements courants
            setup_paths = [
                self.server.library.parent / "KaronlineBox_Install" / "KaronlineBox_Installer.exe",
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
        if self.path == "/auth/login":
            self._auth_login()
            return
        if self.path == "/auth/logout":
            self._auth_logout()
            return
        if self.path == "/session/register":
            self._register_session()
            return
        if self.path == "/request-demand":
            self._relay_demand()
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

        print(f"ACCOUNT CREATED = {email}", flush=True)
        self._send_json(201, {
            "token": auth_issue_token(email),
            "email": email,
            "card_label": auth_card_label(_accounts()[email]),
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
        record = auth_verify_credentials(email, password)
        if record is None:
            print(f"LOGIN FAILED = {email}", flush=True)
            self._send_json(401, {"error": "WRONG CREDENTIALS"})
            return

        print(f"LOGIN OK = {email}", flush=True)
        self._send_json(200, {
            "token": auth_issue_token(email),
            "email": email,
            "card_label": auth_card_label(record),
        })

    def _auth_logout(self):
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            auth_revoke_token(header[7:].strip())
        self._send_json(200, {"status": "ok"})

    def _register_session(self):
        """Enregistre/actualise un nom de session -> URL d'hote (annuaire).

        Requiert une session authentifiee : chaque nom rattache a son compte
        proprietaire, seul compte qui supportera plus tard les frais."""
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
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        if not SESSION_NAME_RE.match(name):
            self._send_json(400, {"error": "INVALID SESSION NAME"})
            return

        parsed_host = urlparse(host_url)
        if parsed_host.scheme != "https" or not parsed_host.hostname:
            self._send_json(400, {"error": "INVALID HOST URL"})
            return

        SESSIONS[name] = {"host_url": host_url, "ts": time.time(),
                          "owner": owner}
        print(f"SESSION REGISTERED = {name} -> {host_url} "
              f"(owner={owner})", flush=True)
        self._send_json(200, {"status": "ok", "name": name, "owner": owner})

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