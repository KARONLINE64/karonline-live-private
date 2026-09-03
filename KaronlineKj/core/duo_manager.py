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
import urllib.error
import urllib.request
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, Qt, Signal
from core.duo_audio import DuoAudioLink

CENTRAL_API_BASE = "https://api.karonlinelive.com"


def generate_duo_code() -> str:
    """Génère un code temporaire unique de type DUO-8492."""
    digits = "".join(random.choices(string.digits, k=4))
    return f"DUO-{digits}"


def normalize_duo_code(raw: str) -> str:
    """Normalise un code DUO (ex: '4891', 'duo 4891', 'DUO-4891' -> 'DUO-4891')."""
    clean = (raw or "").strip().upper().replace(" ", "-")
    if not clean:
        return ""
    if not clean.startswith("DUO-"):
        digits = "".join(c for c in clean if c.isdigit())
        if digits:
            return f"DUO-{digits}"
    return clean


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
    webcam_status_changed = Signal(str, bool)

    def __init__(self, central_auth=None):
        super().__init__()
        self.central_auth = central_auth
        self.active_code: str | None = None
        self.is_host: bool = True
        self.guest_info: dict | None = None
        self.is_connected: bool = False
        self.webcam_capturer: DuoWebcamCapturer | None = None
        self._active_api_base: str = CENTRAL_API_BASE
        self._last_webcam_error = ""
        self._guest_frame_seen = False
        self.audio_link = DuoAudioLink(
            CENTRAL_API_BASE,
            lambda: getattr(self.central_auth, "token", ""),
            lambda: self.central_auth.settings.value("audio/mic_device_name", "")
            if getattr(self.central_auth, "settings", None) is not None else "",
            self,
        )
        self._polling_thread: threading.Thread | None = None
        self._running: bool = False

    def _request_api(self, path: str, payload_dict: dict | None = None, method: str = "POST", timeout: int = 8) -> tuple[int, dict]:
        """Effectue une requête REST DUO vers le relais public unique."""
        payload = json.dumps(payload_dict).encode("utf-8") if payload_dict is not None else None
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
        }
        if payload_dict is not None:
            headers["Content-Type"] = "application/json"
        if self.central_auth and hasattr(self.central_auth, "authorization_header"):
            headers.update(self.central_auth.authorization_header())

        url = f"{CENTRAL_API_BASE}{path}"
        try:
            req = urllib.request.Request(url, data=payload, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8")) if resp.status != 204 else {}
                return resp.status, data
        except urllib.error.HTTPError as exc:
            try:
                err_body = exc.read().decode("utf-8", errors="replace")
                err_data = json.loads(err_body)
            except Exception:
                err_data = {"error": f"HTTP_{exc.code}"}
            return exc.code, err_data
        except Exception as exc:
            return 500, {"error": str(exc)}

    def create_session(self, session_name: str | None = None) -> tuple[bool, str, str]:
        """Crée une nouvelle session DUO auprès du serveur central.

        Renvoie (success, code_ou_erreur, qr_url).
        """
        self.is_host = True
        code = generate_duo_code()
        payload = {
            "code": code,
            "session_name": session_name or "",
            "mode": "duo",
            "created_at": time.time(),
        }

        status, data = self._request_api("/duo/create", payload_dict=payload, method="POST", timeout=8)
        if status == 200:
            self.active_code = data.get("code", code)
            self._guest_frame_seen = False
            qr_url = ""
            self._start_polling()
            self.start_webcam_if_available()
            self.session_created.emit(self.active_code, qr_url)
            return True, self.active_code, qr_url
        else:
            err_msg = data.get("error", f"Erreur {status}")
            return False, f"Impossible de créer la session DUO ({status}) : {err_msg}", ""

    def join_session(self, code: str, guest_name: str = "Invité Desktop") -> tuple[bool, str]:
        """Rejoint une session DUO existante en tant qu'invité Desktop.

        Renvoie (success, message).
        """
        clean_code = normalize_duo_code(code)
        if not clean_code:
            return False, "Code de session invalide."

        payload = {
            "code": clean_code,
            "guest_name": guest_name,
        }

        status, data = self._request_api("/duo/join", payload_dict=payload, method="POST", timeout=8)
        if status == 200:
            self.active_code = clean_code
            self.is_host = False
            self.is_connected = True
            self._start_polling()
            self.start_webcam_if_available()
            self.audio_link.start(self.active_code, is_host=False)
            return True, f"Connecté à la session {clean_code}"
        elif status == 404:
            return False, (
                f"Session DUO « {clean_code} » introuvable (404).\n\n"
                "• Assurez-vous que l'hôte a bien cliqué sur DÉMARRER SESSION (HÔTE).\n"
                "• Vérifiez le code fourni (ex: DUO-8492)."
            )
        else:
            err_msg = data.get("error", f"Erreur {status}")
            return False, f"Impossible de rejoindre la session DUO ({status}) : {err_msg}"

    def start_webcam_if_available(self):
        if not getattr(self, "webcam_capturer", None):
            self.webcam_capturer = DuoWebcamCapturer(self)
            self.webcam_capturer.frame_ready.connect(self.send_webcam_frame)
        if self.webcam_capturer.start():
            self.webcam_status_changed.emit("Webcam DUO active", True)
        else:
            self.webcam_status_changed.emit("Webcam DUO indisponible sur ce PC", False)

    def stop_webcam(self):
        if getattr(self, "webcam_capturer", None):
            self.webcam_capturer.stop()

    def send_webcam_frame(self, jpeg_bytes: bytes):
        if not self.active_code:
            return
        role = "host" if self.is_host else "guest"
        b64_str = base64.b64encode(jpeg_bytes).decode("ascii")
        frame_data = f"data:image/jpeg;base64,{b64_str}"
        payload = {
            "code": self.active_code,
            "role": role,
            "frame": frame_data,
        }
        threading.Thread(
            target=self._post_frame,
            args=(payload,),
            daemon=True
        ).start()

    def _post_frame(self, payload: dict):
        status, data = self._request_api("/duo/frame", payload_dict=payload, method="POST", timeout=3)
        if status == 200:
            self._last_webcam_error = ""
            return
        error = data.get("error", f"Erreur {status}")
        if error != self._last_webcam_error:
            self._last_webcam_error = error
            self.webcam_status_changed.emit(
                f"Envoi webcam DUO impossible ({status}) : {error}", False
            )

    def close_session(self):
        """Ferme la session DUO en cours."""
        self._running = False
        self.audio_link.stop()
        self.stop_webcam()
        if self.active_code:
            self._request_api("/duo/close", payload_dict={"code": self.active_code}, method="POST", timeout=4)
        self.active_code = None
        self.is_host = True
        self.is_connected = False
        self.guest_info = None
        self.session_closed.emit()

    def send_sync_state(self, song_title: str, singer: str, position_ms: int,
                        duration_ms: int, is_playing: bool, artist: str = "",
                        key: int = 0):
        """Émet un tick de synchronisation Master Clock vers l'invité."""
        if not self.active_code:
            return
        payload = {
            "type": "sync",
            "code": self.active_code,
            "song": song_title,
            "artist": artist,
            "singer": singer,
            "key": max(-6, min(6, int(key))),
            "position_ms": position_ms,
            "duration_ms": duration_ms,
            "is_playing": is_playing,
            "timestamp": time.time(),
        }
        self._latest_sync_payload = payload
        self.sync_tick.emit(payload)

    def send_playback_stopped(self):
        """Pousse un arrêt immédiat : la boucle de position ne tourne plus après un STOP."""
        if not self.active_code:
            return
        payload = {
            "type": "sync",
            "code": self.active_code,
            "song": "",
            "artist": "",
            "singer": "",
            "position_ms": 0,
            "duration_ms": 0,
            "is_playing": False,
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
                status, data = self._request_api(f"/duo/status?code={self.active_code}", method="GET", timeout=4)
                if status == 200:
                    guest = data.get("guest")
                    if guest and not self.is_connected:
                        self.is_connected = True
                        self.guest_info = guest
                        # L'offre WebRTC n'est emise qu'une fois l'invite present,
                        # sinon ses candidats ICE expirent avant l'appairage.
                        if self.is_host:
                            self.audio_link.start(self.active_code, is_host=True)
                        self.guest_connected.emit(guest)
                    elif not guest and self.is_connected:
                        self.is_connected = False
                        self.guest_info = None
                        if self.is_host:
                            self.audio_link.stop()
                        self.guest_disconnected.emit()

                    if self.is_host:
                        guest_frame = data.get("guest_frame")
                        if guest_frame:
                            if not self._guest_frame_seen:
                                self._guest_frame_seen = True
                                self.webcam_status_changed.emit(
                                    "Webcam invitée reçue par l'hôte", True
                                )
                            self.guest_frame_received.emit(guest_frame)
                    else:
                        host_frame = data.get("host_frame")
                        if host_frame:
                            self.host_frame_received.emit(host_frame)

                        sync = data.get("sync")
                        if sync:
                            self.sync_tick.emit(sync)
                elif status == 404:
                    if not self.is_host:
                        self._running = False
                        self.audio_link.stop()
                        self.stop_webcam()
                        self.active_code = None
                        self.is_connected = False
                        self.guest_info = None
                        self.session_closed.emit()
                    elif self.is_host:
                        self.is_connected = False
                        self.guest_info = None
                        self.audio_link.stop()
                        self.guest_disconnected.emit()

                if self.is_host and getattr(self, "_latest_sync_payload", None):
                    self._request_api("/duo/sync", payload_dict=self._latest_sync_payload, method="POST", timeout=3)
            except Exception:
                pass
            # Cycle court : reduit le decalage de demarrage video hote/invite
            # (avant : 0.4s pouvait cumuler jusqu'a ~0.8s cote hote+invite).
            time.sleep(0.15)
