from __future__ import annotations

import argparse
import json
import ipaddress
import socket
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


DEFAULT_LIBRARY = Path(__file__).resolve().parent.parent / "SERVER"


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
        """Autoriser le site local ou un client du reseau prive sur le port 8000."""
        origin = self.headers.get("Origin", "")
        parsed = urlparse(origin)
        hostname = parsed.hostname or ""
        try:
            is_private_origin = ipaddress.ip_address(hostname).is_private
        except ValueError:
            is_private_origin = hostname == "localhost"

        if parsed.scheme == "http" and parsed.port == 8000 and is_private_origin:
            self.send_header("Access-Control-Allow-Origin", origin)
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self):
        """Gérer les requêtes de préflight CORS"""
        if urlparse(self.path).path in {"/catalogue", "/request-demand"}:
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
        if urlparse(self.path).path != "/catalogue":
            self._send_json(404, {"error": "NOT FOUND"})
            return

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

    def do_POST(self):
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
        if not parsed_client_ip.is_private:
            self._send_json(400, {"error": "CLIENT IP MUST BE PRIVATE"})
            return
        print(f"DEMAND RECEIVED FROM = {client_ip}", flush=True)
        print(f"DEMAND = singer={singer}, artist={artist}, title={title}, key={key}", flush=True)
        
        # Toujours relayer vers KaronlineBox qui écoute sur 127.0.0.1:8766
        # Peu importe que la demande vienne du navigateur ou d'un client local
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
            with urllib.request.urlopen(target, timeout=10) as response:
                if response.status != 202:
                    raise RuntimeError(f"HTTP {response.status}")
            print(f"DEMAND RELAYED TO KARONLINEBOX = 127.0.0.1:{self.server.request_port}", flush=True)
            self._send_json(202, {"status": "RELAYED"})
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            # Si le relais vers KaronlineBox échoue
            print(f"DEMAND RELAY ERROR = {exc}", flush=True)
            self._send_json(504, {"error": "KARONLINEBOX UNAVAILABLE"})


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