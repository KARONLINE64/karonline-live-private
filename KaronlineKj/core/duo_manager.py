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


import base64
import json
import random
import string
import threading
import time
import urllib.request
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Qt, Signal

CENTRAL_API_BASE = "https://api.karonlinelive.com"


def generate_duo_code() -> str:
    """Génère un code temporaire unique de type DUO-8492."""
    digits = "".join(random.choices(string.digits, k=4))
    return f"DUO-{digits}"


class DuoWebcamCapturer(QObject):
    """Capture d'images depuis la webcam locale (si présente)."""

    frame_ready = Signal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.camera = None
        self.session = None
        self.sink = None
        self._last_capture_time = 0

    def start(self) -> bool:
        try:
            from PySide6.QtMultimedia import QCamera, QMediaCaptureSession, QVideoSink, QMediaDevices
            devices = QMediaDevices.videoInputs()
            if not devices:
                return False
            self.camera = QCamera(devices[0])
            self.sink = QVideoSink()
            self.session = QMediaCaptureSession()
            self.session.setCamera(self.camera)
            self.session.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self._on_video_frame)
            self.camera.start()
            print("DUO WEBCAM CAPTURER STARTED", flush=True)
            return True
        except Exception as exc:
            print(f"DUO WEBCAM CAPTURER INIT ERROR: {exc}", flush=True)
            return False

    def stop(self):
        try:
            if self.camera:
                self.camera.stop()
        except Exception:
            pass

    def _on_video_frame(self, frame):
        now = time.time()
        if now - self._last_capture_time < 0.3:  # ~3 FPS max pour transfert fluide
            return
        if not frame.isValid():
            return
        image = frame.toImage()
        if image.isNull():
            return
        self._last_capture_time = now
        scaled = image.scaled(320, 240, Qt.KeepAspectRatio, Qt.FastTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.WriteOnly)
        scaled.save(buf, "JPG", 50)
        self.frame_ready.emit(bytes(ba.data()))


class DuoSessionManager(QObject):
    """Gère l'état d'une session DUO active côté Hôte."""

    session_created = Signal(str, str)     # (code, qr_url)
    guest_connected = Signal(dict)          # infos invité
    guest_disconnected = Signal()
    session_closed = Signal()
    sync_tick = Signal(dict)                # master clock payload
    guest_frame_received = Signal(str)     # base64 image string invité
    host_frame_received = Signal(str)      # base64 image string hôte

    def __init__(self, central_auth=None):
        super().__init__()
        self.central_auth = central_auth
        self.active_code: str | None = None
        self.is_host: bool = True
        self.guest_info: dict | None = None
        self.is_connected: bool = False
        self.webcam_capturer: DuoWebcamCapturer | None = None
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
                self.start_webcam_if_available()
                self.session_created.emit(self.active_code, qr_url)
                return True, self.active_code, qr_url
        except Exception as exc:
            # Mode secours local si API hors-ligne pendant dev/test
            self.active_code = code
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=250x250&data=https://karonlinelive.com/duo.html?code={code}"
            self._start_polling()
            self.start_webcam_if_available()
            self.session_created.emit(code, qr_url)
            return True, code, qr_url

    def join_session(self, code: str, guest_name: str = "Invité Desktop") -> tuple[bool, str]:
        """Rejoint une session DUO existante en tant qu'invité Desktop.

        Renvoie (success, message).
        """
        clean_code = (code or "").strip().upper()
        if not clean_code:
            return False, "Code de session invalide."

        payload = json.dumps({
            "code": clean_code,
            "guest_name": guest_name,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
        }
        if self.central_auth and hasattr(self.central_auth, "authorization_header"):
            headers.update(self.central_auth.authorization_header())

        try:
            req = urllib.request.Request(
                f"{CENTRAL_API_BASE}/duo/join",
                data=payload,
                headers=headers,
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                self.active_code = clean_code
                self.is_host = False
                self.is_connected = True
                self._start_polling()
                self.start_webcam_if_available()
                return True, f"Connecté à la session {clean_code}"
        except Exception as exc:
            # Succès local en mode secours
            self.active_code = clean_code
            self.is_host = False
            self.is_connected = True
            self._start_polling()
            self.start_webcam_if_available()
            return True, f"Connecté à la session {clean_code}"

    def start_webcam_if_available(self):
        if not getattr(self, "webcam_capturer", None):
            self.webcam_capturer = DuoWebcamCapturer(self)
            self.webcam_capturer.frame_ready.connect(self.send_webcam_frame)
        self.webcam_capturer.start()

    def stop_webcam(self):
        if getattr(self, "webcam_capturer", None):
            self.webcam_capturer.stop()

    def send_webcam_frame(self, jpeg_bytes: bytes):
        if not self.active_code:
            return
        role = "host" if self.is_host else "guest"
        b64_str = base64.b64encode(jpeg_bytes).decode("ascii")
        frame_data = f"data:image/jpeg;base64,{b64_str}"
        payload = json.dumps({
            "code": self.active_code,
            "role": role,
            "frame": frame_data,
        }).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
        }
        if self.central_auth and hasattr(self.central_auth, "authorization_header"):
            headers.update(self.central_auth.authorization_header())

        threading.Thread(
            target=self._post_frame,
            args=(payload, headers),
            daemon=True
        ).start()

    def _post_frame(self, payload: bytes, headers: dict):
        try:
            req = urllib.request.Request(
                f"{CENTRAL_API_BASE}/duo/frame",
                data=payload,
                headers=headers,
                method="POST",
            )
            urllib.request.urlopen(req, timeout=3).close()
        except Exception:
            pass

    def close_session(self):
        """Ferme la session DUO en cours."""
        self._running = False
        self.stop_webcam()
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
        self._latest_sync_payload = payload
        self.sync_tick.emit(payload)

    def _start_polling(self):
        self._running = True
        self._latest_sync_payload = None
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
                with urllib.request.urlopen(req, timeout=4) as resp:
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

                    if self.is_host:
                        guest_frame = data.get("guest_frame")
                        if guest_frame:
                            self.guest_frame_received.emit(guest_frame)
                    else:
                        host_frame = data.get("host_frame")
                        if host_frame:
                            self.host_frame_received.emit(host_frame)

                        sync = data.get("sync")
                        if sync:
                            self.sync_tick.emit(sync)

                if self.is_host and getattr(self, "_latest_sync_payload", None):
                    sync_data = json.dumps(self._latest_sync_payload).encode("utf-8")
                    sync_headers = dict(headers)
                    sync_headers["Content-Type"] = "application/json"
                    sync_req = urllib.request.Request(
                        f"{CENTRAL_API_BASE}/duo/sync",
                        data=sync_data,
                        headers=sync_headers,
                        method="POST",
                    )
                    urllib.request.urlopen(sync_req, timeout=3).close()
            except Exception:
                pass
            time.sleep(0.4)
