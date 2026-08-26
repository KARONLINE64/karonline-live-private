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
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        """Gérer les requêtes de préflight CORS"""
        path = urlparse(self.path).path
        if path in {"/catalogue", "/request-demand", "/download/karonlinebox", "/session/register"} or path.startswith("/session/"):
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

    def do_GET(self):
        path = urlparse(self.path).path

        # Endpoint: GET /session/<nom> - resoudre un nom de session vers l'URL de l'hote
        if path.startswith("/session/"):
            name = unquote(path[len("/session/"):]).strip().casefold()
            entry = self._active_session(name)
            if entry is None:
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

    def _register_session(self):
        """Enregistre un nom de session -> URL d'hote (annuaire en memoire)."""
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

        if self._active_session(name) is not None:
            self._send_json(409, {"error": "SESSION NAME TAKEN", "name": name})
            return

        SESSIONS[name] = {"host_url": host_url, "ts": time.time()}
        print(f"SESSION REGISTERED = {name} -> {host_url}", flush=True)
        self._send_json(200, {"status": "ok", "name": name})

    @staticmethod
    def _active_session(name: str):
        entry = SESSIONS.get(name)
        if entry is None:
            return None
        if time.time() - entry["ts"] > SESSION_TTL_SECONDS:
            SESSIONS.pop(name, None)
            return None
        return entry

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