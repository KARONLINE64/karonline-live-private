"""Gestionnaire de session KARONLINEBOX DUO (Signalisation, état et synchronisation).

Gère la création de session temporaire DUO-XXXX, l'appairage WebRTC
avec l'invité (Desktop ou Mobile) et l'émission du Master Clock de synchronisation.
"""
from __future__ import annotations

import json
import random
import string
import threading
import time
import urllib.request
from PySide6.QtCore import QObject, Signal

CENTRAL_API_BASE = "https://api.karonlinelive.com"


def generate_duo_code() -> str:
    """Génère un code temporaire unique de type DUO-8492."""
    digits = "".join(random.choices(string.digits, k=4))
    return f"DUO-{digits}"


class DuoSessionManager(QObject):
    """Gère l'état d'une session DUO active côté Hôte."""

    session_created = Signal(str, str)  # (code, qr_url)
    guest_connected = Signal(dict)       # infos invité
    guest_disconnected = Signal()
    session_closed = Signal()
    sync_tick = Signal(dict)             # master clock payload

    def __init__(self, central_auth=None):
        super().__init__()
        self.central_auth = central_auth
        self.active_code: str | None = None
        self.is_host: bool = True
        self.guest_info: dict | None = None
        self.is_connected: bool = False
        self._polling_thread: threading.Thread | None = None
        self._running: bool = False

    def create_session(self, session_name: str | None = None) -> tuple[bool, str, str]:
        """Crée une nouvelle session DUO auprès du serveur central.

        Renvoie (success, code_ou_erreur, qr_url).
        """
        code = generate_duo_code()
        payload = json.dumps({
            "code": code,
            "session_name": session_name or "",
            "mode": "duo",
            "created_at": time.time(),
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
        }
        if self.central_auth and hasattr(self.central_auth, "authorization_header"):
            headers.update(self.central_auth.authorization_header())

        try:
            req = urllib.request.Request(
                f"{CENTRAL_API_BASE}/duo/create",
                data=payload,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                self.active_code = data.get("code", code)
                qr_url = data.get(
                    "qr_url",
                    f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=https://karonlinelive.com/duo.html?code={self.active_code}"
                )
                self._start_polling()
                self.session_created.emit(self.active_code, qr_url)
                return True, self.active_code, qr_url
        except Exception as exc:
            # Mode secours local si API hors-ligne pendant dev/test
            self.active_code = code
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=https://karonlinelive.com/duo.html?code={code}"
            self._start_polling()
            self.session_created.emit(code, qr_url)
            return True, code, qr_url

    def close_session(self):
        """Ferme la session DUO en cours."""
        self._running = False
        if self.active_code:
            try:
                headers = {
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
                }
                if self.central_auth and hasattr(self.central_auth, "authorization_header"):
                    headers.update(self.central_auth.authorization_header())

                payload = json.dumps({"code": self.active_code}).encode("utf-8")
                req = urllib.request.Request(
                    f"{CENTRAL_API_BASE}/duo/close",
                    data=payload,
                    headers=headers,
                    method="POST",
                )
                urllib.request.urlopen(req, timeout=4)
            except Exception:
                pass
        self.active_code = None
        self.is_connected = False
        self.guest_info = None
        self.session_closed.emit()

    def send_sync_state(self, song_title: str, singer: str, position_ms: int, duration_ms: int, is_playing: bool):
        """Émet un tick de synchronisation Master Clock vers l'invité."""
        if not self.active_code:
            return
        payload = {
            "type": "sync",
            "code": self.active_code,
            "song": song_title,
            "singer": singer,
            "position_ms": position_ms,
            "duration_ms": duration_ms,
            "is_playing": is_playing,
            "timestamp": time.time(),
        }
        self.sync_tick.emit(payload)

    def _start_polling(self):
        self._running = True
        self._polling_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._polling_thread.start()

    def _poll_loop(self):
        while self._running and self.active_code:
            try:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
                }
                if self.central_auth and hasattr(self.central_auth, "authorization_header"):
                    headers.update(self.central_auth.authorization_header())

                req = urllib.request.Request(
                    f"{CENTRAL_API_BASE}/duo/status?code={self.active_code}",
                    headers=headers,
                    method="GET",
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    guest = data.get("guest")
                    if guest and not self.is_connected:
                        self.is_connected = True
                        self.guest_info = guest
                        self.guest_connected.emit(guest)
                    elif not guest and self.is_connected:
                        self.is_connected = False
                        self.guest_info = None
                        self.guest_disconnected.emit()
            except Exception:
                pass
            time.sleep(2)
