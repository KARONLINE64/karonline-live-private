from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PySide6.QtCore import QObject, Signal


class _RequestHandler(BaseHTTPRequestHandler):
    server_version = "KaronlineLANRequests/1.0"

    def log_message(self, format, *args):
        return

    def _send_json(self, status: int, payload: dict):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/request":
            self._send_json(404, {"error": "NOT FOUND"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            singer = str(payload.get("singer", "")).strip()
            artist = str(payload.get("artist", "")).strip()
            title = str(payload.get("title", "")).strip()
            key = int(payload.get("key", 0))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(400, {"error": "INVALID REQUEST"})
            return

        if not singer or not artist or not title:
            self._send_json(400, {"error": "MISSING REQUEST FIELD"})
            return
        if not -12 <= key <= 12:
            self._send_json(400, {"error": "INVALID KEY"})
            return

        self.server.receiver.request_received.emit(singer, artist, title, key)
        self._send_json(202, {"status": "RECEIVED"})


class LanRequestReceiver(QObject):
    request_received = Signal(str, str, str, int)

    def __init__(self, host: str, port: int, parent: QObject | None = None):
        super().__init__(parent)
        self.host = host
        self.port = int(port)
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self):
        if self._server is not None:
            return
        server = ThreadingHTTPServer((self.host, self.port), _RequestHandler)
        server.receiver = self
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="karonline-lan-request-receiver",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        server = self._server
        self._server = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._thread = None
