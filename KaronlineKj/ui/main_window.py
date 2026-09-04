from pathlib import Path
import base64
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QFont, QCursor, QAction, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QFormLayout, QHBoxLayout, QGridLayout, QListWidget, QListWidgetItem,
    QSlider, QFrame, QGroupBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy, QTabWidget,
    QMessageBox, QCheckBox, QButtonGroup, QFileDialog, QMenu,
    QDialog, QLineEdit, QSpinBox, QDialogButtonBox, QInputDialog, QPlainTextEdit
)
from core.gstreamer_player import GStreamerPlayer, GStreamerError
from core.models import Song
from core.queue_manager import QueueManager
from core.favorites_manager import FavoritesManager
from core.lan_config import (
    LAN_RECEIVER_HOST,
    LAN_REQUEST_PORT,
)
from core.lan_request_receiver import LanRequestReceiver
from core.central_auth import CentralAuthClient
from core.duo_manager import DuoSessionManager
from ui.audio_setup_dialog import AudioSetupDialog, LiveMicMonitor
from ui.auth_dialog import AuthDialog
from ui.duo_widget import DuoVideoOverlay


def default_media_dir() -> Path:
    """Dossier local des MP4 : jamais sous le dossier d'installation (souvent
    Program Files, non inscriptible sans droits admin), toujours sous le
    profil utilisateur courant."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "KaronlineBox" / "media"


STYLE = """
QMainWindow,QWidget{background:#05090d;color:#f1f4f7;font-family:"Segoe UI";}
QFrame,QGroupBox{background:#0b1117;border:1px solid #1b2732;border-radius:5px;}
QGroupBox{margin-top:10px;padding:12px;}
QGroupBox::title{subcontrol-origin:margin;left:16px;padding:0 6px;color:#00a7ff;font-weight:700;}
QLabel#section{color:#00a7ff;font-size:32px;font-weight:700;}
QLabel#current{font-size:30px;font-weight:700;}
QLabel#artist{color:#c7cdd3;font-size:18px;}
QPushButton{background:#0d141b;border:1px solid #273440;border-radius:4px;padding:9px 13px;color:#f1f4f7;}
QPushButton:hover{border-color:#00a7ff;}
QPushButton#nav{background:transparent;border:none;font-size:15px;}
QSlider::groove:horizontal{height:5px;background:#26313b;}
QSlider::handle:horizontal{width:14px;margin:-5px 0;background:#00a7ff;border-radius:7px;}
QListWidget{background:#080d12;border:none;outline:none;}
QListWidget::item{padding:8px;border-bottom:1px solid #17212a;}
"""

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KaronlineBox")
        # Taille minimale reduite (etait 1500x900) : sur un ecran/portable
        # plus petit que 900px de haut (ex. 1366x768), forcer 900 poussait
        # Windows a violer la contrainte et provoquait un rendu deforme /
        # dedouble de la barre du bas (CHANGEUR DE TONALITE, etc.).
        self.resize(1400, 780)
        self.setMinimumSize(1280, 720)

        self.queue = QueueManager()

        self.gst_player = None
        self.public_window = None
        self.public_container = None
        self.public_video = None
        self.public_bg_label = None
        self.public_warning_label = None
        self.public_duo_webcam_label = None
        self._public_screen_mode = "video"
        self._last_duo_guest_frame_data = None
        self._duo_guest_connected_flag = False
        self.settings = QSettings("Karonline", "KaronlineKJ")

        # Compte KJ (API centrale) : jeton persistant ; favoris/réglages locaux.
        self.central_auth = CentralAuthClient(self.settings)
        self._central_session_ok = False
        self._active_relay_session = None

        # KARONLINEBOX DUO Manager & Overlay
        self.duo_manager = DuoSessionManager(self.central_auth)
        self.duo_overlay = None
        self.duo_manager.session_created.connect(self._on_duo_session_created)
        self.duo_manager.guest_connected.connect(self._on_duo_guest_connected)
        self.duo_manager.guest_disconnected.connect(self._on_duo_guest_disconnected)
        self.duo_manager.session_closed.connect(self._on_duo_session_closed)
        self.duo_manager.guest_frame_received.connect(self._on_duo_guest_frame_received)
        self.duo_manager.host_frame_received.connect(self._on_duo_host_frame_received)
        self.duo_manager.sync_tick.connect(self._on_duo_sync_tick_received)
        self.duo_manager.audio_link.status_changed.connect(self._on_duo_audio_status)
        self.duo_manager.audio_link.error.connect(self._on_duo_audio_error)
        self.duo_manager.webcam_status_changed.connect(self._on_duo_webcam_status)
        self.duo_manager.chat_messages_received.connect(self._on_duo_chat_messages)

        # Moniteur micro->casque en direct (retour vocal + EQ + reverb), partagé
        # avec le dialogue de configuration audio pour rester actif pendant le karaoké.
        self.mic_monitor = LiveMicMonitor(self)

        # V45 — actual RÉGLAGES runtime state.
        self.public_bg_files = []
        self.public_bg_index = 0
        self._public_bg_timer = QTimer(self)
        self._public_bg_timer.timeout.connect(self._rotate_public_background)

        self._warning_shown = False
        self._karaoke_faded = False
        self._eos_handled = False
        self.next_warning_timer = QTimer(self)
        self.next_warning_timer.setSingleShot(True)
        self.next_warning_timer.timeout.connect(self._show_public_warning)

        self.break_playlist = []
        self.break_index = 0
        self.break_active = False
        self.break_playlist_running = False
        self.break_audio_suspended_for_karaoke = False
        self.audio_owner = "none"  # none / break / karaoke
        self.break_track_durations_ms = []
        self.break_timeline_position_ms = 0
        self.break_timeline_last_update = None
        self.break_auto_pending = False
        self.break_crossfade_active = False
        self.break_crossfade_started = 0.0
        self.break_crossfade_next_index = None
        self.break_crossfade_active = False
        self.break_crossfade_started = 0.0
        self.break_crossfade_next_index = None
        self.break_crossfade_duration = 8.0
        self.break_fade_timer = QTimer(self)
        self.break_fade_timer.setInterval(100)
        self.break_fade_timer.timeout.connect(self._break_fade_dispatch)
        self.break_fade_start_volume = 0.0
        self.break_fade_target_volume = 1.0
        self.break_fade_started_ms = 0
        self.break_fade_duration_ms = 3000
        self.break_auto_timer = QTimer(self)
        self.break_auto_timer.setSingleShot(True)
        self.break_auto_timer.timeout.connect(self._end_auto_break)
        self.song_files = {}
        self.media_dir = default_media_dir()
        self.current_key_value = 0

        # V18: remote requests waiting for KJ validation.
        self.requests = []
        self.remote_songs = set()
        self.lan_request_receiver = LanRequestReceiver(
            LAN_RECEIVER_HOST,
            LAN_REQUEST_PORT,
            self,
        )
        self.lan_request_receiver.request_received.connect(
            self.add_remote_request
        )
        self._demand_blink_on = False
        self._demand_blink_timer = QTimer(self)
        self._demand_blink_timer.setInterval(500)
        self._demand_blink_timer.timeout.connect(self._blink_demands)

        # V23: persistent singer roster. KJ can later remove names manually.
        self.singer_roster = {}
        self.singer_aliases = {}

        # V36: client-style favorites are persisted locally with QSettings.
        # They remain available offline; sending a request is a separate action.
        self.favorites = FavoritesManager(self.settings)
        self.build_ui()
        self.lan_request_receiver.start()

        # Prepare the public render target without displaying the public window.
        # The D3D11 public sink must always have a valid external HWND, otherwise
        # GStreamer creates its own "Direct3D11 render" window.
        self._ensure_public_window()

        # Load saved settings BEFORE starting BREAK MUSIC.
        # This ensures the startup state respects MODE KJ,
        # BREAK MUSIC ON/OFF, playlist folder and AUTO duration.
        self.load_settings()

        # V79: BREAK MUSIC is also the pre-show background.
        # AUTO ON => mandatory; AUTO OFF + BREAK MUSIC ON => enabled.
        # It remains active until the first karaoke video is launched.
        self._start_initial_break_music()

        self.load_demo()

        self.gst_timer = QTimer(self)
        self.gst_timer.setInterval(100)
        self.gst_timer.timeout.connect(self.poll_gstreamer)
        self.gst_timer.start()

        self.queue.changed.connect(self.refresh_queue)
        self.queue.current_changed.connect(self.refresh_current)
        self.queue.next_changed.connect(self.refresh_next)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_kj_video_width()

    def poll_gstreamer(self):
        if not self.gst_player:
            return

        self.gst_player.poll_bus()
        self.gst_player.poll_audio_bus()
        self._update_break_playlist_crossfade()

        duration = self.gst_player.duration_ms()
        position = self.gst_player.position_ms()

        if (
            duration > 0
            and self.settings.value("next_warning_on", True, type=bool)
            and not self._warning_shown
        ):
            warning_seconds = self.settings.value(
                "next_warning_duration", 10, type=int
            )
            if not self.next_warning_timer.isActive() and position < max(0, duration - warning_seconds * 1000):
                self.next_warning_timer.start(
                    max(1, duration - position - warning_seconds * 1000)
                )

        self.set_video_time_labels(position, duration)
        self._update_runtime_features(position, duration)

        if duration > 0:
            self.progress.setRange(0, duration)
            if not self.progress.isSliderDown():
                self.progress.setValue(position)
        else:
            self.progress.setRange(0, 0)

        # Synchro Master Clock DUO & Capture d'écran vidéo Hôte (Webcam ou Karaoké)
        if hasattr(self, "duo_manager") and self.duo_manager and self.duo_manager.active_code:
            song = self.queue.current
            is_karaoke = bool(self.gst_player and self.audio_owner == "karaoke")
            self.duo_manager.send_sync_state(
                song.title if song else "",
                song.singer if song else "",
                position,
                duration,
                is_karaoke,
                song.artist if song else "",
                self.current_key_value,
            )
            if self.duo_manager.is_host and is_karaoke:
                self._capture_and_send_duo_host_video_frame()


    def _capture_and_send_duo_host_video_frame(self):
        now = time.time()
        if getattr(self, "_last_host_video_frame_time", 0) and now - self._last_host_video_frame_time < 0.35:
            return
        self._last_host_video_frame_time = now
        try:
            if getattr(self, "video", None):
                pixmap = self.video.grab()
                if not pixmap.isNull():
                    scaled = pixmap.scaled(320, 240, Qt.KeepAspectRatio, Qt.FastTransformation)
                    ba = QByteArray()
                    buf = QBuffer(ba)
                    buf.open(QIODevice.WriteOnly)
                    scaled.save(buf, "JPG", 45)
                    self.duo_manager.send_webcam_frame(bytes(ba.data()))
        except Exception:
            pass


    def _schedule_next_warning(self):
        self.next_warning_timer.stop()
        self._warning_shown = False
        if not self.settings.value("next_warning_on", True, type=bool):
            self._hide_public_warning()
            return
        duration = self.gst_player.duration_ms() if self.gst_player else 0
        seconds = self.settings.value("next_warning_duration", 10, type=int)
        if duration > seconds * 1000:
            self.next_warning_timer.start(duration - seconds * 1000)

    def _seek_from_slider(self):
        slider = None
        for name in ("progress_slider","position_slider","seek_slider","progress"):
            candidate = getattr(self, name, None)
            if candidate is not None and hasattr(candidate, "value"):
                slider = candidate
                break
        if slider is None or not self.gst_player:
            return
        value = int(slider.value())
        duration = int(self.gst_player.duration_ms())
        if duration <= 0:
            return
        maximum = int(slider.maximum())
        if maximum <= 1000:
            position = int(duration * value / max(1, maximum))
        else:
            position = min(duration, value)
        self.gst_player.seek_ms(position)

    def _update_runtime_features(self, position, duration):
        if duration <= 0:
            # Keep SUIVANT visible once it has appeared; EOS handles the
            # transition to the public background.
            self._karaoke_faded = False
            return

        remaining_ms = max(0, duration - position)

        # Robust EOS fallback: some Windows GStreamer/D3D11 combinations
        # finish the video without delivering the expected bus EOS in time.
        if position >= max(0, duration - 150) and not self._eos_handled:
            self._eos_handled = True
            self.on_gst_eos()
            return

        # AVERTISSEMENT SUIVANT is independent from MODE KJ.
        warning_on = self.settings.value(
            "next_warning_on", True, type=bool
        )
        warning_seconds = self.settings.value(
            "next_warning_duration", 10, type=int
        )

        if warning_on and remaining_ms <= warning_seconds * 1000:
            if not self._warning_shown:
                self._warning_shown = True
                self._show_public_warning()
        elif remaining_ms > warning_seconds * 1000:
            # Before T-10: no warning yet. Once shown, it stays until the
            # next karaoke video actually starts.
            pass

        # Fixed 3 s karaoke fade before Break Music.
        break_on = self._break_music_effective_on()
        if (
            break_on
            and remaining_ms <= 3000
            and not self._karaoke_faded
        ):
            self._karaoke_faded = True
            self._fade_karaoke_out()

    def _break_music_effective_on(self):
        if self.kj_auto_on.isChecked():
            return True
        return self.break_manual_on.isChecked()

    def _music_volume_changed_for_break(self, value):
        """Volume 0–100 % de la MUSIQUE DU BREAK uniquement."""
        if self.gst_player and self.gst_player.audio_pipeline:
            self.gst_player.set_audio_volume(value)


    def _get_music_volume_ratio(self):
        # V52: the existing MUSIC 0–100% control is the sole music master.
        for name in ("music_volume_slider", "music_slider", "music_volume"):
            control = getattr(self, name, None)
            if control is not None and hasattr(control, "value"):
                try:
                    return max(0.0, min(1.0, float(control.value()) / 100.0))
                except (TypeError, ValueError):
                    pass
        return 1.0

    def _reset_break_eq(self):
        """Remet toutes les bandes de l'EQ MUSIQUE DU BREAK à 0 dB."""
        for slider in getattr(self, "break_eq_sliders", []):
            slider.blockSignals(True)
            slider.setValue(0)
            slider.blockSignals(False)
        self._apply_break_eq()

    def _set_break_eq_enabled(self, enabled):
        enabled = bool(enabled)
        for slider in getattr(self, "break_eq_sliders", []):
            slider.setEnabled(enabled)
        self._apply_break_eq()

    def _apply_break_eq(self):
        if not hasattr(self, "break_eq_on") or not self.gst_player:
            return
        self.gst_player.set_break_eq(
            self.break_eq_on.isChecked(),
            [slider.value() for slider in self.break_eq_sliders]
        )

    def _prepare_break_track_durations(self, playlist):
        durations = {}
        folder = self.settings.value("break_folder", "", type=str)
        root = Path(folder)
        if root.is_dir():
            playlists = sorted(
                [p for p in root.iterdir()
                 if p.is_file() and p.suffix.lower() in {".m3u", ".m3u8"}],
                key=lambda p: p.name.casefold(),
            )
            if playlists:
                current_ms = None
                try:
                    for raw in playlists[0].read_text(
                        encoding="utf-8-sig", errors="replace"
                    ).splitlines():
                        line = raw.strip()
                        if not line:
                            continue
                        if line.upper().startswith("#EXTINF:"):
                            try:
                                current_ms = int(float(
                                    line.split(":", 1)[1].split(",", 1)[0]
                                ) * 1000)
                            except (ValueError, IndexError):
                                current_ms = None
                            continue
                        if line.startswith("#"):
                            continue
                        candidate = Path(line)
                        if not candidate.is_absolute():
                            candidate = (playlists[0].parent / candidate).resolve()
                        if current_ms and candidate.is_file():
                            durations[str(candidate)] = current_ms
                        current_ms = None
                except OSError:
                    pass
        return [max(1000, int(durations.get(f, 180000))) for f in playlist]

    def _break_effective_track_span(self, index):
        duration = (
            self.break_track_durations_ms[index]
            if 0 <= index < len(self.break_track_durations_ms)
            else 180000
        )
        return max(1000, duration - 8000)

    def _advance_silent_break_timeline(self):
        if (
            not self.break_playlist_running
            or not self.break_playlist
            or not self.break_audio_suspended_for_karaoke
        ):
            return
        now = time.monotonic()
        if self.break_timeline_last_update is None:
            self.break_timeline_last_update = now
            return
        elapsed = max(0, int((now - self.break_timeline_last_update) * 1000))
        self.break_timeline_last_update = now
        self.break_timeline_position_ms += elapsed
        while self.break_timeline_position_ms >= self._break_effective_track_span(
            self.break_index
        ):
            self.break_timeline_position_ms -= self._break_effective_track_span(
                self.break_index
            )
            self.break_index = (self.break_index + 1) % len(self.break_playlist)

    def _suspend_break_audio_for_karaoke(self):
        if not self.break_playlist_running or not self.gst_player:
            return
        if self.gst_player.audio_pipeline:
            self.break_timeline_position_ms = self.gst_player.audio_position_ms()
        self.break_timeline_last_update = time.monotonic()
        self.break_audio_suspended_for_karaoke = True
        self.audio_owner = "karaoke"
        self.break_crossfade_active = False
        self.break_crossfade_started = 0.0
        self.break_crossfade_next_index = None
        self.gst_player.stop_audio()

    def _prepare_break_playlist(self):
        folder = self.settings.value("break_folder", "", type=str)
        if not folder:
            return []
        root = Path(folder)
        if not root.is_dir():
            return []

        allowed = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma"}

        # Prefer an M3U/M3U8 playlist when one exists in the selected folder.
        playlists = sorted(
            [p for p in root.iterdir()
             if p.is_file() and p.suffix.lower() in {".m3u", ".m3u8"}],
            key=lambda p: p.name.casefold(),
        )
        if playlists:
            playlist_file = playlists[0]
            result = []
            try:
                for raw in playlist_file.read_text(
                    encoding="utf-8-sig", errors="replace"
                ).splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#"):
                        continue
                    candidate = Path(line)
                    if not candidate.is_absolute():
                        candidate = (playlist_file.parent / candidate).resolve()
                    if candidate.is_file() and candidate.suffix.lower() in allowed:
                        result.append(str(candidate))
            except OSError:
                result = []
            if result:
                return result

        # Fallback: all supported audio files in the selected folder.
        return sorted(
            [str(p) for p in root.iterdir()
             if p.is_file() and p.suffix.lower() in allowed],
            key=lambda x: Path(x).name.casefold(),
        )

    def _write_break_diag(self, lines):
        try:
            import sys
            from pathlib import Path

            if getattr(sys, "frozen", False):
                diag_path = Path(sys.executable).resolve().parent / "BREAK_DIAG.txt"
            else:
                diag_path = Path.cwd() / "BREAK_DIAG.txt"

            with diag_path.open("a", encoding="utf-8") as f:
                f.write("\n".join(str(x) for x in lines) + "\n")
        except Exception:
            pass
    def _start_initial_break_music(self):
        """Démarre la BREAK MUSIC comme fond d'attente avant la 1re chanson."""

        diag = [
            "===== BREAK MUSIC DIAGNOSTIC =====",
            f"frozen           = {getattr(__import__('sys'), 'frozen', False)!r}",
            f"executable       = {__import__('sys').executable!r}",
            f"cwd              = {__import__('os').getcwd()!r}",
            f"gst_player       = {self.gst_player!r}",
            f"gst_player_type  = {type(self.gst_player).__name__}",
            f"pipeline         = {getattr(self.gst_player, 'pipeline', None)!r}",
            f"audio_owner      = {self.audio_owner!r}",
            f"effective_on     = {self._break_music_effective_on()!r}",
            f"break_folder     = {self.settings.value('break_folder', '', type=str)!r}",
        ]

        if not self.gst_player:
            diag.append("RESULT = NO GST PLAYER")
            self._write_break_diag(diag)
            return False

        # AUTO ON => BREAK MUSIC is mandatory.
        # AUTO OFF + BREAK MUSIC ON => BREAK MUSIC is enabled manually.
        if not self._break_music_effective_on():
            return False

        # At startup there is NO automatic-break countdown: the music runs
        # until the KJ launches the first karaoke video.
        result = self._start_break_music(auto=False)

        diag.extend([
            f"_start_break_music = {result!r}",
            f"playlist_count    = {len(self.break_playlist)}",
            f"break_index       = {self.break_index}",
            f"audio_owner_after = {self.audio_owner!r}",
            f"break_active      = {self.break_active!r}",
            "===== END BREAK DIAGNOSTIC =====",
        ])

        self._write_break_diag(diag)
        return result

    def _start_break_music(self, auto=False):
        # Never start/revive Break Music while a karaoke video owns audio.
        if self.gst_player and self.gst_player.pipeline and self.audio_owner == "karaoke":
            return False

        playlist = self._prepare_break_playlist()
        if not playlist or not self.gst_player:
            return False

        previous_playlist = self.break_playlist
        playlist_changed = playlist != previous_playlist
        self.break_playlist = playlist

        if playlist_changed:
            self.break_index = 0
            self.break_timeline_position_ms = 0
            self.break_track_durations_ms = self._prepare_break_track_durations(
                self.break_playlist
            )
            self.break_crossfade_active = False
            self.break_crossfade_started = 0.0
            self.break_crossfade_next_index = None
        elif not self.break_track_durations_ms:
            self.break_track_durations_ms = self._prepare_break_track_durations(
                self.break_playlist
            )
        else:
            self.break_index %= len(self.break_playlist)

        if self.break_audio_suspended_for_karaoke:
            self._advance_silent_break_timeline()
            self.break_audio_suspended_for_karaoke = False

        self.break_active = True
        self.break_auto_pending = bool(auto)
        self._show_public_background()

        try:
            target_volume = self._get_music_volume_ratio()
            if not self.gst_player.audio_pipeline:
                self.gst_player.load_audio(
                    self.break_playlist[self.break_index], volume=0.0
                )
                if self.break_timeline_position_ms > 0:
                    self.gst_player.seek_audio(self.break_timeline_position_ms)
                self.break_playlist_running = True
                self.break_audio_suspended_for_karaoke = False
                self.audio_owner = "break"
                self.break_timeline_last_update = time.monotonic()
                self._start_break_fade(0.0, target_volume)

            if auto:
                seconds = self.settings.value(
                    "break_auto_duration", 30, type=int
                )
                self.break_auto_timer.start(max(1, seconds) * 1000)

            self.set_status("● BREAK MUSIC")
            return True
        except Exception as exc:
            self.break_active = False
            self.break_playlist_running = False
            self.set_status(f"● Break Music : {exc}", False)
            return False


    def _update_break_playlist_crossfade(self):
        if (
            not self.break_playlist_running
            or not self.break_playlist
            or not self.gst_player
        ):
            return

        if self.break_audio_suspended_for_karaoke:
            self._advance_silent_break_timeline()
            return

        if not self.gst_player.audio_pipeline or len(self.break_playlist) < 2:
            return

        duration = self.gst_player.audio_duration_ms()
        position = self.gst_player.audio_position_ms()
        if duration <= 8000:
            return

        target = self._get_music_volume_ratio()

        if not self.break_crossfade_active:
            if position < duration - 8000:
                return
            next_index = (self.break_index + 1) % len(self.break_playlist)
            try:
                self.gst_player.start_audio_crossfade(
                    self.break_playlist[next_index]
                )
            except Exception as exc:
                self.set_status(f"● Break Music : {exc}", False)
                return
            self.break_crossfade_active = True
            self.break_crossfade_started = time.monotonic()
            self.break_crossfade_next_index = next_index
            return

        ratio = max(
            0.0,
            min(1.0, (time.monotonic() - self.break_crossfade_started) / 8.0)
        )
        self.gst_player.set_audio_crossfade_volumes(
            target * (1.0 - ratio),
            target * ratio,
        )
        if ratio >= 1.0:
            self.gst_player.finish_audio_crossfade(target)
            self.break_index = self.break_crossfade_next_index
            self.break_timeline_position_ms = 0
            self.break_timeline_last_update = time.monotonic()
            self.break_crossfade_active = False
            self.break_crossfade_started = 0.0
            self.break_crossfade_next_index = None


    def _on_break_audio_eos(self):
        if not self.break_active or not self.break_playlist:
            return
        if self.break_crossfade_active:
            return

        # Fallback for very short/unusual files where an 8 s pre-EOS
        # crossover cannot be started.
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self._start_next_break_track)

    def _start_next_break_track(self):
        if not self.break_active or not self.break_playlist or not self.gst_player:
            return

        self.break_index = (self.break_index + 1) % len(self.break_playlist)
        self.break_crossfade_active = False
        self.break_crossfade_started = 0.0
        self.break_crossfade_next_index = None
        target_volume = self._get_music_volume_ratio()

        try:
            self.gst_player.load_audio(
                self.break_playlist[self.break_index],
                volume=target_volume
            )
        except Exception as exc:
            self.set_status(f"● Break Music : {exc}", False)

    def _start_break_fade(self, start, target):
        self.break_fade_start_volume = float(start)
        self.break_fade_target_volume = float(target)
        self.break_fade_started_ms = QTimer().remainingTime()  # reset marker below
        self._break_fade_elapsed = 0
        self.break_fade_timer.start()

    def _break_fade_dispatch(self):
        if hasattr(self, "_break_fade_callback") and self._break_fade_callback:
            self._break_fade_tick_out()
        else:
            self._break_fade_in_tick()

    def _break_fade_in_tick(self):
        if not self.gst_player or not self.gst_player.audio_pipeline:
            self.break_fade_timer.stop()
            return

        self._break_fade_elapsed = getattr(self, "_break_fade_elapsed", 0) + 100
        ratio = min(1.0, self._break_fade_elapsed / self.break_fade_duration_ms)
        value = (
            self.break_fade_start_volume
            + (self.break_fade_target_volume - self.break_fade_start_volume) * ratio
        )
        self.gst_player._set_break_volume_level(value)

        if ratio >= 1.0:
            self.break_fade_timer.stop()

    def _break_fade_tick_out(self):
        if not self.gst_player or not self.gst_player.audio_pipeline:
            self.break_fade_timer.stop()
            callback = getattr(self, "_break_fade_callback", None)
            self._break_fade_callback = None
            if callback:
                callback()
            return

        self._break_fade_elapsed = getattr(self, "_break_fade_elapsed", 0) + 100
        ratio = min(1.0, self._break_fade_elapsed / 3000.0)
        value = 1.0 - ratio
        self.gst_player.set_audio_volume(value)
        if ratio >= 1.0:
            self.break_fade_timer.stop()
            self.gst_player._set_break_volume_level(0.0)
            callback = getattr(self, "_break_fade_callback", None)
            self._break_fade_callback = None
            if callback:
                callback()

    def _fade_karaoke_out(self):
        # The current GStreamer video pipeline exposes mastervolume.
        # Fade is performed over the fixed 3 seconds using the main timer.
        self._karaoke_fade_elapsed = 0
        self._karaoke_fade_timer = getattr(self, "_karaoke_fade_timer", None)
        if self._karaoke_fade_timer is None:
            self._karaoke_fade_timer = QTimer(self)
            self._karaoke_fade_timer.setInterval(100)
            self._karaoke_fade_timer.timeout.connect(self._karaoke_fade_tick)
        self._karaoke_fade_timer.start()

    def _karaoke_fade_tick(self):
        if not self.gst_player or not self.gst_player.pipeline:
            self._karaoke_fade_timer.stop()
            return
        self._karaoke_fade_elapsed += 100
        ratio = min(1.0, self._karaoke_fade_elapsed / 3000.0)
        self.gst_player.set_volume(1.0 - ratio)
        if ratio >= 1.0:
            self._karaoke_fade_timer.stop()

    def _end_auto_break(self):
        if not self.break_active:
            return
        # In KJ Auto mode: if no songs are queued, do NOT stop break music after 30s.
        # Keep break music playing continuously until a new song is queued/ready.
        if self.kj_auto_on.isChecked() and not self.queue.items:
            self.break_auto_pending = False
            return
        self.break_active = False
        self.break_auto_pending = False
        self._fade_break_to_silence(callback=self._play_next_after_break)

    def _play_next_after_break(self):
        if self.queue.current is not None:
            next_song = self.queue.advance()
            if next_song is not None:
                self.play_song_object(next_song)
                return
        self._show_public_background()
        self.play_btn.setText("▶")
        self.set_status("● FIN DE VIDÉO")

    def _finish_break_and_play_next(self):
        self.break_auto_timer.stop()
        self.break_fade_timer.stop()
        self.break_active = False
        self.break_auto_pending = False
        self.break_crossfade_active = False
        self.break_crossfade_started = 0.0
        self.break_crossfade_next_index = None
        self._fade_break_to_silence(
            callback=self._suspend_break_audio_for_karaoke
        )
        self._show_public_background()
        if self.queue.current is not None:
            next_song = self.queue.advance()
            if next_song is not None:
                self.play_song_object(next_song)
                return
        self.play_btn.setText("▶")
        self.set_status("● FIN DE VIDÉO")

    def _hide_public_video_sink(self):
        if self.gst_player:
            self.gst_player.set_public_video_enabled(False)

    def _show_kj_background_at_eos(self):
        # KJ preview: after EOS, the video widget itself becomes the same
        # public-background visual instead of freezing on the last frame.
        if not getattr(self, "video", None):
            return
        bg_on = self.settings.value("public_bg_on", True, type=bool)
        if bg_on and self.public_bg_files:
            path = self.public_bg_files[self.public_bg_index]
            self.video.setStyleSheet(
                'background-image:url("' + str(path).replace("\\", "/") + '");'
                'background-position:center;'
                'background-repeat:no-repeat;'
                'background-color:#020305;'
            )
        else:
            self.video.setStyleSheet(
                "background:#020305;border:1px solid #16222c;"
            )

    def _restore_kj_video_surface(self):
        if not getattr(self, "video", None):
            return
        self.video.setStyleSheet(
            "background:#020305;border:1px solid #16222c;"
        )

    def _show_public_background(self):
        if not self.public_window:
            return
        if self.public_video:
            self.public_video.hide()
        if self.public_duo_webcam_label:
            self.public_duo_webcam_label.hide()
        bg_on = self.settings.value("public_bg_on", True, type=bool)
        if bg_on and self.public_bg_files and self.public_bg_label:
            self.public_bg_label.show()
            self.public_bg_label.lower()
        elif self.public_bg_label:
            self.public_bg_label.hide()

        # SUIVANT must remain above the public background after EOS.
        if self.public_warning_label and self.public_warning_label.isVisible():
            self.public_warning_label.raise_()

    def _show_public_video(self):
        if self.public_bg_label:
            self.public_bg_label.hide()
        if self.public_duo_webcam_label:
            self.public_duo_webcam_label.hide()
        if self.public_video:
            self.public_video.show()
            self.public_video.raise_()

    def _show_public_duo_webcam(self):
        if self.public_bg_label:
            self.public_bg_label.hide()
        if self.public_video:
            self.public_video.hide()
        if self.public_duo_webcam_label:
            self.public_duo_webcam_label.show()
            self.public_duo_webcam_label.raise_()
            if self._last_duo_guest_frame_data:
                self._render_duo_frame_on_label(
                    self.public_duo_webcam_label, self._last_duo_guest_frame_data
                )
            else:
                self.public_duo_webcam_label.setText(
                    "En attente de l'image webcam de l'invité..."
                )
                self.public_duo_webcam_label.setStyleSheet(
                    "background:#000;color:#aeb7bf;font-size:16px;"
                )

    def _load_public_backgrounds(self):
        folder = self.settings.value("public_bg_folder", "", type=str)
        self.public_bg_files = []
        if folder:
            root = Path(folder)
            if root.is_dir():
                allowed = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
                self.public_bg_files = sorted(
                    [p for p in root.iterdir() if p.is_file() and p.suffix.lower() in allowed],
                    key=lambda p: p.name.casefold(),
                )
        self.public_bg_index = 0
        if self.public_bg_files:
            self._set_public_background_image()
            seconds = self.settings.value("public_bg_rotation", 30, type=int)
            self._public_bg_timer.start(max(1, seconds) * 1000)
        else:
            self._public_bg_timer.stop()

    def _set_public_background_image(self):
        if not self.public_bg_files or not self.public_bg_label:
            return
        from PySide6.QtGui import QPixmap
        pix = QPixmap(str(self.public_bg_files[self.public_bg_index]))
        if not pix.isNull():
            self.public_bg_label.setPixmap(
                pix.scaled(
                    self.public_bg_label.size(),
                    Qt.KeepAspectRatioByExpanding,
                    Qt.SmoothTransformation,
                )
            )

    def _rotate_public_background(self):
        if not self.public_bg_files:
            return
        self.public_bg_index = (self.public_bg_index + 1) % len(self.public_bg_files)
        self._set_public_background_image()

    def _show_public_warning(self):
        if not self.public_warning_label or not self.queue.next_song:
            return
        if not self.settings.value("next_warning_on", True, type=bool):
            return
        song = self.queue.next_song
        self.public_warning_label.setText(
            f"SUIVANT : {song.singer}\n{song.title}"
        )
        self.public_warning_label.show()
        self.public_warning_label.raise_()

    def _hide_public_warning(self):
        if self.public_warning_label:
            self.public_warning_label.hide()
        # Warning is a Qt overlay band; karaoke video remains visible.

    @staticmethod
    def format_time(ms):
        total_seconds = max(0, int(ms) // 1000)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def set_video_time_labels(self, position, duration):
        if duration <= 0:
            self.elapsed_label.setText("ÉCOULÉ 00:00")
            self.total_label.setText("DURÉE 00:00")
            self.remaining_label.setText("RESTE 00:00")
            return

        position = min(max(0, position), duration)
        remaining = max(0, duration - position)

        self.elapsed_label.setText(
            f"ÉCOULÉ {self.format_time(position)}"
        )
        self.total_label.setText(
            f"DURÉE {self.format_time(duration)}"
        )
        self.remaining_label.setText(
            f"RESTE {self.format_time(remaining)}"
        )

    def set_status(self, text, ok=True):
        self.status.setText(text)
        self.status.setStyleSheet(
            "color:#48d62c;" if ok else "color:#ff5b5b;"
        )

    def build_ui(self):
        self.setStyleSheet(STYLE)
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(14, 10, 14, 10)

        nav = QHBoxLayout()
        logo = QLabel()
        logo_path = Path(__file__).resolve().parent / "kb_logo_luxury.png"
        logo_pixmap = QPixmap(str(logo_path))
        if not logo_pixmap.isNull():
            logo.setPixmap(
                logo_pixmap.scaledToHeight(
                    80, Qt.SmoothTransformation
                )
            )
        nav.addWidget(logo)

        app_title = QLabel("KaronlineBox")
        app_title.setStyleSheet("font-size:22px;font-weight:700;color:#f1f4f7;padding-left:8px;")
        nav.addWidget(app_title)

        nav.addStretch()

        self.audio_setup_btn = QPushButton("🎧  MICRO/CASQUE")
        self.duo_btn = QPushButton("🎙  DUO")
        self.demands_btn = QPushButton("DEMANDES")
        self.queue_nav_btn = QPushButton("☷  FILE D'ATTENTE")
        self.favorites_btn = QPushButton("☆  FAVORIS")
        self.settings_btn = QPushButton("⚙  RÉGLAGES")
        self.help_btn = QPushButton("❓  HELP")

        for b in [
            self.audio_setup_btn, self.duo_btn, self.demands_btn, self.queue_nav_btn,
            self.favorites_btn, self.settings_btn, self.help_btn
        ]:
            b.setObjectName("nav")
            nav.addWidget(b)

        # V22: small independent yellow request-alert square.
        self.demands_indicator = QLabel()
        self.demands_indicator.setFixedSize(10, 10)
        self.demands_indicator.hide()
        nav.insertWidget(
            nav.indexOf(self.demands_btn) + 1,
            self.demands_indicator
        )

        self.audio_setup_btn.clicked.connect(
            self.open_audio_setup_dialog
        )
        self.duo_btn.clicked.connect(
            lambda: self.show_main_view("duo")
        )
        self.demands_btn.clicked.connect(
            lambda: self.show_main_view("demands")
        )
        self.queue_nav_btn.clicked.connect(
            lambda: self.show_main_view("queue")
        )
        self.favorites_btn.clicked.connect(
            lambda: self.show_main_view("favorites")
        )
        self.settings_btn.clicked.connect(
            lambda: self.show_main_view("settings")
        )
        self.help_btn.clicked.connect(
            lambda: self.show_main_view("help")
        )

        nav.addStretch()
        self.status = QLabel("● Prêt")
        nav.addWidget(self.status)
        outer.addLayout(nav)

        grid = QGridLayout()
        self.kj_video_grid = grid
        grid.setRowStretch(0, 5)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 5)

        left = QVBoxLayout()

        # N'est visible que sous l'onglet FILE D'ATTENTE (voir show_main_view) :
        # inutile ailleurs et ca reduit la hauteur disponible pour les formulaires.
        self.live_box = QFrame()
        box = self.live_box
        box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(0)
        lab = QLabel("LIVE")
        lab.setObjectName("section")
        lab.setStyleSheet("font-size:20px;font-weight:700;color:#00a7ff;")
        lab.setMaximumHeight(26)
        lay.addWidget(lab)

        self.current_song = QLabel("")
        self.current_song.setObjectName("current")
        self.current_song.setStyleSheet("font-size:15px;font-weight:700;")
        self.current_song.setMaximumHeight(30)
        lay.addWidget(self.current_song)

        self.current_artist = QLabel("")
        self.current_artist.setObjectName("artist")
        self.current_artist.setMaximumHeight(26)
        lay.addWidget(self.current_artist)

        self.current_singer = QLabel("")
        self.current_singer.setMaximumHeight(22)
        lay.addWidget(self.current_singer)

        self.current_key = QLabel("")
        self.current_key.setMaximumHeight(22)
        lay.addWidget(self.current_key)
        left.addWidget(box)

        nb = QFrame()
        self.next_box = nb
        nb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        nl = QVBoxLayout(nb)
        nl.setContentsMargins(8, 2, 8, 2)
        nl.setSpacing(0)
        lab = QLabel("SUIVANT")
        lab.setObjectName("section")
        lab.setStyleSheet("font-size:20px;font-weight:700;color:#00a7ff;")
        lab.setMaximumHeight(26)
        nl.addWidget(lab)

        self.next_singer = QLabel("")
        self.next_singer.setObjectName("current")
        self.next_singer.setMaximumHeight(30)
        nl.addWidget(self.next_singer)

        self.next_song = QLabel("")
        self.next_song.setObjectName("artist")
        self.next_song.setMaximumHeight(26)
        nl.addWidget(self.next_song)
        left.addWidget(nb)

        qb = QGroupBox()
        # This group box has no title; the app-wide QGroupBox rule still
        # reserves margin-top/padding for a title area, wasting height.
        qb.setStyleSheet("margin-top:0px;padding:4px;")
        ql = QVBoxLayout(qb)
        ql.setContentsMargins(8, 0, 8, 2)
        ql.setSpacing(0)

        queue_header = QHBoxLayout()
        queue_header.setContentsMargins(4, 0, 4, 0)

        queue_title = QLabel("FILE D'ATTENTE")
        queue_title.setObjectName("section")
        queue_title.setStyleSheet("font-size:20px;")
        queue_header.addWidget(queue_title)
        queue_header.addStretch()

        self.queue_count_label = QLabel("0 titres")
        self.queue_count_label.setStyleSheet(
            "color:#f1f4f7;font-size:16px;"
        )
        queue_header.addWidget(self.queue_count_label)
        ql.addLayout(queue_header)

        self.queue_list = QTableWidget(0, 8)
        self.queue_list.setHorizontalHeaderLabels(
            ["", "#", "", "Chanteur", "Artiste", "Titre", "Tonalité", ""]
        )
        self.queue_list.setSelectionBehavior(QTableWidget.SelectRows)
        self.queue_list.setSelectionMode(QTableWidget.SingleSelection)
        self.queue_list.setEditTriggers(QTableWidget.NoEditTriggers)
        self.queue_list.setShowGrid(False)
        self.queue_list.verticalHeader().setVisible(False)
        self.queue_list.setAlternatingRowColors(False)
        self.queue_list.setFocusPolicy(Qt.NoFocus)
        self.queue_list.setSizePolicy(
            QSizePolicy.Expanding, QSizePolicy.Expanding
        )
        self.queue_list.setMinimumHeight(0)

        header = self.queue_list.horizontalHeader()
        header.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        header.setSectionResizeMode(0, QHeaderView.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.Fixed)
        header.resizeSection(0, 18)
        header.resizeSection(2, 28)
        header.resizeSection(7, 42)

        self.queue_list.setStyleSheet(
            """
            QTableWidget {
                background:#080d12;
                border:1px solid #16222c;
                color:#f1f4f7;
                font-size:11px;
                outline:none;
            }
            QHeaderView::section {
                background:#080d12;
                color:#f1f4f7;
                border:none;
                border-bottom:1px solid #1d2a35;
                padding:4px 6px;
                font-size:12px;
                font-weight:500;
            }
            QTableWidget::item {
                background:#080d12;
                border:none;
                border-bottom:1px solid #16222c;
                padding:2px 4px;
            }
            QTableWidget::item:selected {
                background:#10202c;
                color:#ffffff;
            }
            """
        )
        self.queue_list.cellDoubleClicked.connect(
            lambda row, column: self.play_selected_queue_item()
        )
        ql.addWidget(self.queue_list, 1)
        ql.addSpacing(3)

        ql.addSpacing(3)

        acts = QHBoxLayout()

        self.clear_queue_button = QPushButton("VIDER LA LISTE")
        self.clear_queue_button.setStyleSheet(
            "QPushButton{color:#ff6b6b;background:#251719;"
            "border:1px solid #7a3030;padding:4px 6px;border-radius:4px;"
            "font-size:11px;}"
            "QPushButton:hover{background:#3a1d20;}"
        )
        self.clear_queue_button.clicked.connect(self.confirm_clear_queue)

        self.queue_remove_btn = QPushButton("SUPPRIMER")
        self.add_favorite_button = QPushButton("AJOUTER AUX FAVORIS")
        self.add_favorite_button.clicked.connect(self.add_selected_to_favorites)
        self.queue_up_btn = QPushButton("↑ MONTER")
        self.queue_down_btn = QPushButton("↓ DESCENDRE")
        self.queue_play_btn = QPushButton("▶ LIRE MAINTENANT")

        for _act_btn in (
            self.queue_remove_btn,
            self.add_favorite_button, self.queue_up_btn,
            self.queue_down_btn, self.queue_play_btn,
        ):
            _act_btn.setStyleSheet("font-size:11px;padding:4px 6px;")

        acts.addWidget(self.clear_queue_button)
        acts.addWidget(self.queue_remove_btn)
        acts.addWidget(self.add_favorite_button)
        acts.addWidget(self.queue_up_btn)
        acts.addWidget(self.queue_down_btn)
        acts.addWidget(self.queue_play_btn)

        self.queue_remove_btn.clicked.connect(self.remove_selected_queue_item)
        self.queue_up_btn.clicked.connect(lambda: self.move_selected_queue_item(-1))
        self.queue_down_btn.clicked.connect(lambda: self.move_selected_queue_item(1))
        self.queue_play_btn.clicked.connect(self.play_selected_queue_item)

        self.queue_list.itemDoubleClicked.connect(
            lambda item: self.play_selected_queue_item()
        )

        ql.addLayout(acts)

        # V19: DEMANDES and FILE D'ATTENTE share ONLY this lower-left area.
        # ACTUELLEMENT and SUIVANT remain permanently visible above it.
        # V41 — RÉGLAGES
        self.settings_page = QWidget()
        settings_layout = QVBoxLayout(self.settings_page)
        box = QGroupBox("RÉGLAGES")
        box_layout = QVBoxLayout(box)
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setStyleSheet("""
            QTabWidget::pane{border:1px solid #1b2732;background:#080d12;}
            QTabBar::tab{background:#0d141b;color:#f1f4f7;border:1px solid #273440;padding:7px 14px;font-weight:600;}
            QTabBar::tab:selected{background:#f1f4f7;color:#05090d;}
        """)

        page=QWidget(); form=QFormLayout(page)
        self.public_bg_on=QCheckBox("ON"); self.public_bg_off=QCheckBox("OFF")
        g=QButtonGroup(page); g.setExclusive(True); g.addButton(self.public_bg_on); g.addButton(self.public_bg_off)
        row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(self.public_bg_on); h.addWidget(self.public_bg_off)
        self.public_bg_folder=QLineEdit(); self.public_bg_folder.setReadOnly(True)
        b=QPushButton("CHOISIR LE DOSSIER"); b.clicked.connect(self.choose_public_bg_folder)
        fr=QWidget(); fh=QHBoxLayout(fr); fh.setContentsMargins(0,0,0,0); fh.addWidget(self.public_bg_folder); fh.addWidget(b)
        self.public_bg_rotation=QSpinBox(); self.public_bg_rotation.setRange(1,3600); self.public_bg_rotation.setSuffix(" s")
        form.addRow("FOND ÉCRAN PUBLIC",row); form.addRow("DOSSIER IMAGES",fr); form.addRow("DURÉE ROTATION",self.public_bg_rotation)
        self.settings_tabs.addTab(page,"FOND ÉCRAN PUBLIC")

        page=QWidget(); form=QFormLayout(page)
        self.next_warning_on=QCheckBox("ON"); self.next_warning_off=QCheckBox("OFF")
        g=QButtonGroup(page); g.setExclusive(True); g.addButton(self.next_warning_on); g.addButton(self.next_warning_off)
        row=QWidget(); h=QHBoxLayout(row); h.setContentsMargins(0,0,0,0); h.addWidget(self.next_warning_on); h.addWidget(self.next_warning_off)
        self.next_warning_duration=QSpinBox(); self.next_warning_duration.setRange(1,120); self.next_warning_duration.setSuffix(" s")
        form.addRow("AFFICHAGE",row); form.addRow("DURÉE AVANT FIN",self.next_warning_duration); form.addRow("MESSAGE",QLabel("SUIVANT : NOM CHANTEUR — TITRE"))
        self.settings_tabs.addTab(page,"AVERTISSEMENT SUIVANT")

        page=QWidget(); form=QFormLayout(page)
        self.break_folder=QLineEdit(); self.break_folder.setReadOnly(True)
        b=QPushButton("CHOISIR LE DOSSIER"); b.clicked.connect(self.choose_break_folder)
        fr=QWidget(); fh=QHBoxLayout(fr); fh.setContentsMargins(0,0,0,0); fh.addWidget(self.break_folder); fh.addWidget(b)
        form.addRow("DOSSIER PLAYLIST",fr); form.addRow("FONDU AUDIO",QLabel("3 s — FIXE, NON MODIFIABLE"))
        # ÉGALISEUR — MUSIQUE DU BREAK uniquement
        self.break_eq_on = QCheckBox("ON")
        self.break_eq_off = QCheckBox("OFF")
        self.break_eq_group = QButtonGroup(page)
        self.break_eq_group.setExclusive(True)
        self.break_eq_group.addButton(self.break_eq_on)
        self.break_eq_group.addButton(self.break_eq_off)

        break_eq_switch = QWidget()
        break_eq_layout = QHBoxLayout(break_eq_switch)
        break_eq_layout.setContentsMargins(0, 0, 0, 0)
        break_eq_layout.addWidget(self.break_eq_on)
        break_eq_layout.addWidget(self.break_eq_off)
        break_eq_layout.addStretch(1)

        self.break_eq_sliders = []
        self.break_eq_labels = ["SOUS-GRAVE", "GRAVE", "BAS-MÉDIUM", "MÉDIUM", "AIGUS"]
        for label in self.break_eq_labels:
            slider = QSlider(Qt.Horizontal)
            slider.setRange(-12, 12)
            slider.setValue(0)
            slider.setEnabled(False)
            slider.setToolTip(f"{label} — MUSIQUE DU BREAK")
            self.break_eq_sliders.append(slider)

        form.addRow("ÉGALISEUR", break_eq_switch)
        self.break_eq_reset = QPushButton("REMETTRE À 0 dB")
        self.break_eq_reset.clicked.connect(self._reset_break_eq)
        form.addRow("", self.break_eq_reset)

        for label, slider in zip(self.break_eq_labels, self.break_eq_sliders):
            form.addRow(label, slider)

        self.break_eq_on.toggled.connect(self._set_break_eq_enabled)
        self.break_eq_off.setChecked(True)
        for slider in self.break_eq_sliders:
            slider.valueChanged.connect(lambda _v: self._apply_break_eq())

        self.settings_tabs.addTab(page,"MUSIQUE DU BREAK")
        # MODE KJ — AUTO / BREAK MUSIC logic
        page = QWidget()
        form = QFormLayout(page)

        self.kj_auto_on = QCheckBox("ON")
        self.kj_auto_off = QCheckBox("OFF")
        g_mode = QButtonGroup(page)
        g_mode.setExclusive(True)
        g_mode.addButton(self.kj_auto_on)
        g_mode.addButton(self.kj_auto_off)

        mode_row = QWidget()
        mh = QHBoxLayout(mode_row)
        mh.setContentsMargins(0, 0, 0, 0)
        mh.addWidget(self.kj_auto_on)
        mh.addWidget(self.kj_auto_off)

        self.break_manual_on = QCheckBox("ON")
        self.break_manual_off = QCheckBox("OFF")
        g_break = QButtonGroup(page)
        g_break.setExclusive(True)
        g_break.addButton(self.break_manual_on)
        g_break.addButton(self.break_manual_off)

        break_row = QWidget()
        bh = QHBoxLayout(break_row)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(18)
        bh.addWidget(self.break_manual_on)
        bh.addWidget(self.break_manual_off)
        bh.addStretch(1)

        # The entire BREAK MUSIC choice row is hidden in MODE KJ AUTO ON.
        self.break_manual_row = QWidget()
        br = QHBoxLayout(self.break_manual_row)
        br.setContentsMargins(0, 0, 0, 0)
        br.addWidget(break_row)

        self.break_auto_duration = QSpinBox()
        self.break_auto_duration.setRange(1, 600)
        self.break_auto_duration.setSuffix(" s")

        # TAILLE VIDÉO KJ — independent from MODE KJ AUTO.
        self.video_size_large = QCheckBox("GRANDE")
        self.video_size_medium = QCheckBox("MOYENNE")
        self.video_size_small = QCheckBox("PETITE")

        self.video_size_group_button = QButtonGroup(page)
        self.video_size_group_button.setExclusive(True)
        self.video_size_group_button.addButton(self.video_size_large)
        self.video_size_group_button.addButton(self.video_size_medium)
        self.video_size_group_button.addButton(self.video_size_small)

        video_size_row = QWidget()
        video_size_layout = QHBoxLayout(video_size_row)
        video_size_layout.setContentsMargins(0, 0, 0, 0)
        video_size_layout.setSpacing(18)
        video_size_layout.addWidget(self.video_size_large)
        video_size_layout.addWidget(self.video_size_medium)
        video_size_layout.addWidget(self.video_size_small)
        video_size_layout.addStretch(1)

        saved_video_size = self.settings.value(
            "kj_video_size", "GRANDE", type=str
        )

        if saved_video_size == "MOYENNE":
            self.video_size_medium.setChecked(True)
        elif saved_video_size == "PETITE":
            self.video_size_small.setChecked(True)
        else:
            self.video_size_large.setChecked(True)

        self.video_size_large.toggled.connect(self._apply_kj_video_size)
        self.video_size_medium.toggled.connect(self._apply_kj_video_size)
        self.video_size_small.toggled.connect(self._apply_kj_video_size)

        form.addRow("TAILLE VIDÉO KJ", video_size_row)
        form.addRow("MODE KJ AUTO", mode_row)
        form.addRow("MUSIQUE DU BREAK", self.break_manual_row)
        form.addRow("DURÉE BREAK AUTO", self.break_auto_duration)
        form.addRow("FONDU AUDIO", QLabel("3 s — FIXE, NON MODIFIABLE"))

        self.settings_tabs.addTab(page, "MODE KJ")

        # ONGLET SESSION — demarre le serveur LAN + tunnel public et
        # enregistre un nom de session simple pour les invites distants.
        page = QWidget()
        session_layout = QVBoxLayout(page)

        session_account_info = QLabel(
            "🔒 <b>COMPTE KARONLINELIVE OBLIGATOIRE</b><br>"
            "Toute session KaronlineBox doit être obligatoirement liée à votre compte KaronlineLive (mêmes identifiants).<br>"
            "Cette authentification garantit le débit de la bonne carte bancaire et l'arrivée certaine des demandes mobiles."
        )
        session_account_info.setWordWrap(True)
        session_account_info.setStyleSheet("color:#00c8ff;font-size:12px;background:#08131c;border:1px solid #1b6f91;border-radius:6px;padding:10px;margin-bottom:6px;")
        session_layout.addWidget(session_account_info)

        session_info = QLabel(
            "Choisissez un nom de session (ex. soiree-marc) et cliquez sur DÉMARRER LA SESSION :"
        )
        session_info.setWordWrap(True)
        session_layout.addWidget(session_info)

        session_form = QFormLayout()
        self.session_name_input = QLineEdit()
        self.session_name_input.setPlaceholderText("soiree-marc")
        session_form.addRow("NOM DE SESSION", self.session_name_input)
        session_layout.addLayout(session_form)

        session_buttons = QHBoxLayout()
        self.account_btn = QPushButton("👤 COMPTE : non connecté")
        self.account_btn.clicked.connect(self.open_account_dialog)
        session_buttons.addWidget(self.account_btn)
        self.session_start_btn = QPushButton("▶ DÉMARRER LA SESSION")
        self.session_start_btn.clicked.connect(self.start_public_session)
        session_buttons.addWidget(self.session_start_btn)
        session_layout.addLayout(session_buttons)

        self.session_status_label = QLabel("● Session non démarrée")
        self.session_status_label.setWordWrap(True)
        self.session_status_label.setStyleSheet("color:#aeb7bf;font-size:13px;padding-top:8px;")
        session_layout.addWidget(self.session_status_label)
        session_layout.addStretch()

        self.settings_tabs.addTab(page, "SESSION")

        box_layout.addWidget(self.settings_tabs)
        settings_layout.addWidget(box)
        self.queue_area_stack = QStackedWidget()

        queue_page = QWidget()
        queue_page_layout = QVBoxLayout(queue_page)
        queue_page_layout.setContentsMargins(0, 0, 0, 0)
        queue_page_layout.addWidget(qb)

        demands_page = QWidget()
        demands_layout = QVBoxLayout(demands_page)
        demands_layout.setContentsMargins(0, 0, 0, 0)

        demands_box = QGroupBox("DEMANDES")
        demands_box_layout = QVBoxLayout(demands_box)

        demands_info = QLabel(
            "Requests distantes reçues — validation du nom du chanteur "
            "avant ajout à la file d'attente."
        )
        demands_info.setStyleSheet("color:#aeb7bf;font-size:13px;")
        demands_box_layout.addWidget(demands_info)

        self.requests_table = QTableWidget(0, 5)
        self.requests_table.setHorizontalHeaderLabels(
            ["CHANTEUR", "ARTISTE", "TITRE", "TONALITÉ", "ACTION"]
        )
        self.requests_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.requests_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.requests_table.verticalHeader().setVisible(False)
        self.requests_table.verticalHeader().setDefaultSectionSize(44)
        self.requests_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeToContents
        )
        self.requests_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )
        self.requests_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.Stretch
        )
        self.requests_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeToContents
        )
        self.requests_table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeToContents
        )
        demands_box_layout.addWidget(self.requests_table, 1)

        self.demands_empty = QLabel("Aucune demande reçue")
        self.demands_empty.setAlignment(Qt.AlignCenter)
        self.demands_empty.setStyleSheet(
            "color:#68737d;font-size:18px;padding:30px;"
        )
        demands_box_layout.addWidget(self.demands_empty)

        demands_layout.addWidget(demands_box)

        # V36: FAVORIS — two offline sub-tabs. Clicking a title creates a
        # test request locally; the real website/payment endpoint will later
        # call add_remote_request() after payment confirmation.
        favorites_page = QWidget()
        favorites_layout = QVBoxLayout(favorites_page)
        favorites_layout.setContentsMargins(0, 0, 0, 0)

        favorites_box = QGroupBox("FAVORIS")
        favorites_box_layout = QVBoxLayout(favorites_box)

        self.favorites_tabs = QTabWidget()

        self.favorites_tabs.setStyleSheet('\nQTabWidget::pane {\n    border: 1px solid #1b2732;\n    background: #080d12;\n}\n\nQTabBar::tab {\n    background: #0d141b;\n    color: #f1f4f7;\n    border: 1px solid #273440;\n    padding: 7px 14px;\n    min-width: 75px;\n    font-weight: 600;\n}\n\nQTabBar::tab:selected {\n    background: #f1f4f7;\n    color: #05090d;\n}\n\nQTabBar::tab:hover {\n    border-color: #00a7ff;\n}\n')
        self.solo_favorites_list = QListWidget()
        self.group_favorites_list = QListWidget()
        self.favorites_tabs.addTab(self.solo_favorites_list, "MOI")
        self.favorites_tabs.addTab(self.group_favorites_list, "GROUPE")
        favorites_box_layout.addWidget(self.favorites_tabs, 1)

        favorites_clear_row = QHBoxLayout()
        self.clear_solo_favorites_button = QPushButton("Vider mes favoris")
        self.clear_solo_favorites_button.clicked.connect(self.clear_my_favorites)
        self.clear_group_favorites_button = QPushButton("Vider les favoris du groupe")
        self.clear_group_favorites_button.clicked.connect(self.clear_group_favorites)
        favorites_clear_row.addWidget(self.clear_solo_favorites_button)
        favorites_clear_row.addWidget(self.clear_group_favorites_button)
        favorites_clear_row.addStretch()
        favorites_box_layout.addLayout(favorites_clear_row)

        favorites_layout.addWidget(favorites_box)

        self.solo_favorites_list.itemClicked.connect(self.request_solo_favorite)
        self.group_favorites_list.itemClicked.connect(self.request_group_favorite)
        self.favorites.changed.connect(self.refresh_favorites)

        # PAGE DUO
        duo_page = QWidget()
        duo_layout = QVBoxLayout(duo_page)
        duo_layout.setContentsMargins(0, 0, 0, 0)

        duo_box = QGroupBox("KARONLINEBOX DUO — CHANTEZ ENSEMBLE À DISTANCE")
        duo_box_layout = QVBoxLayout(duo_box)

        duo_intro = QLabel(
            "Invitez un ami ou votre famille à chanter avec vous en direct.\n"
            "Chantez ensemble comme si vous étiez dans la même pièce !"
        )
        duo_intro.setStyleSheet("color:#aeb7bf;font-size:13px;font-weight:600;")
        duo_intro.setWordWrap(True)
        duo_box_layout.addWidget(duo_intro)

        duo_form = QFormLayout()
        duo_form.setSpacing(6)

        self.duo_code_label = QLabel("○ Aucune session DUO active")
        self.duo_code_label.setStyleSheet("color:#00c8ff;font-size:18px;font-weight:700;")
        duo_form.addRow("CODE DE SESSION", self.duo_code_label)

        self.duo_guest_label = QLabel("○ Aucun invité connecté")
        self.duo_guest_label.setStyleSheet("color:#aeb7bf;font-size:13px;font-weight:600;")
        duo_form.addRow("STATUT INVITÉ", self.duo_guest_label)

        self.duo_audio_label = QLabel("○ Audio DUO en attente d'un invité")
        self.duo_audio_label.setStyleSheet("color:#aeb7bf;font-size:13px;font-weight:600;")
        duo_form.addRow("AUDIO DUO", self.duo_audio_label)

        self.duo_video_label = QLabel("○ Webcam DUO en attente")
        self.duo_video_label.setStyleSheet("color:#aeb7bf;font-size:13px;font-weight:600;")
        duo_form.addRow("WEBCAM DUO", self.duo_video_label)

        # Champ de saisie pour rejoindre une session en tant qu'invité Desktop
        self.duo_join_input = QLineEdit()
        self.duo_join_input.setPlaceholderText("Entrez le code hôte (ex: DUO-8492)")
        self.duo_join_input.setStyleSheet(
            "background:#0b1821;border:1px solid #387a90;border-radius:4px;"
            "color:#00c8ff;font-size:13px;font-weight:700;padding:4px 8px;"
        )
        duo_form.addRow("REJOINDRE (INVITÉ)", self.duo_join_input)

        duo_box_layout.addLayout(duo_form)

        duo_buttons = QHBoxLayout()
        self.duo_start_btn = QPushButton("▶ DÉMARRER SESSION (HÔTE)")
        self.duo_start_btn.setStyleSheet(
            "background:linear-gradient(110deg,#124de5,#194fff);"
            "border:0;border-radius:5px;padding:8px 14px;"
            "color:#fff;font-weight:700;font-size:13px;"
        )
        self.duo_start_btn.clicked.connect(self._start_duo_session_action)

        self.duo_join_btn = QPushButton("🔗 REJOINDRE")
        self.duo_join_btn.setStyleSheet(
            "background:#0d1822;border:1px solid #00c8ff;border-radius:5px;"
            "padding:8px 14px;color:#00c8ff;font-weight:700;font-size:13px;"
        )
        self.duo_join_btn.clicked.connect(self._join_duo_session_action)

        self.duo_stop_btn = QPushButton("✕ FERMER LA SESSION")
        self.duo_stop_btn.setStyleSheet(
            "background:#3a151b;border:1px solid #e80055;border-radius:5px;"
            "padding:8px 14px;color:#ff6b6b;font-weight:700;font-size:13px;"
        )
        self.duo_stop_btn.setEnabled(False)
        self.duo_stop_btn.clicked.connect(self._stop_duo_session_action)

        duo_buttons.addWidget(self.duo_start_btn)
        duo_buttons.addWidget(self.duo_join_btn)
        duo_buttons.addWidget(self.duo_stop_btn)
        duo_buttons.addStretch()
        duo_box_layout.addLayout(duo_buttons)

        self.duo_qr_box = QWidget()
        qr_layout = QVBoxLayout(self.duo_qr_box)
        self.duo_qr_label = QLabel()
        self.duo_qr_label.setAlignment(Qt.AlignCenter)
        self.duo_instructions = QLabel(
            "📱 Scannez le QR Code avec un smartphone pour chanter immédiatement à deux !"
        )
        self.duo_instructions.setAlignment(Qt.AlignCenter)
        self.duo_instructions.setStyleSheet("color:#00c8ff;font-size:12px;font-weight:700;")
        qr_layout.addWidget(self.duo_qr_label)
        qr_layout.addWidget(self.duo_instructions)
        self.duo_qr_box.hide()
        duo_box_layout.addWidget(self.duo_qr_box)

        self.duo_overlay_btn = QPushButton("📹 AFFICHER / MASQUER LA WEBCAM INVITÉ")
        self.duo_overlay_btn.setStyleSheet(
            "background:#0d1822;border:1px solid #387a90;border-radius:5px;"
            "padding:8px 14px;color:#f4f7fb;font-weight:700;font-size:13px;"
        )
        self.duo_overlay_btn.clicked.connect(self._toggle_duo_overlay)
        duo_box_layout.addWidget(self.duo_overlay_btn)

        # Widget webcam fixe (non volant) sous le bouton AFFICHER/MASQUER
        self.duo_overlay = DuoVideoOverlay(self)
        self.duo_overlay.frame_error.connect(
            lambda message: self._on_duo_webcam_status(message, False)
        )
        duo_box_layout.addWidget(self.duo_overlay, 4)

        self.duo_chat_box = QGroupBox("CHAT DUO")
        duo_chat_layout = QVBoxLayout(self.duo_chat_box)
        self.duo_chat_history = QPlainTextEdit()
        self.duo_chat_history.setReadOnly(True)
        self.duo_chat_history.document().setMaximumBlockCount(50)
        self.duo_chat_history.setFixedHeight(110)
        self.duo_chat_history.setPlaceholderText("Messages disponibles uniquement pendant la session DUO.")
        duo_chat_layout.addWidget(self.duo_chat_history)
        duo_chat_row = QHBoxLayout()
        self.duo_chat_input = QLineEdit()
        self.duo_chat_input.setMaxLength(500)
        self.duo_chat_input.setPlaceholderText("Écrire un message...")
        self.duo_chat_send_btn = QPushButton("ENVOYER")
        self.duo_chat_send_btn.clicked.connect(self._send_duo_chat_message)
        self.duo_chat_input.returnPressed.connect(self._send_duo_chat_message)
        duo_chat_row.addWidget(self.duo_chat_input, 1)
        duo_chat_row.addWidget(self.duo_chat_send_btn)
        duo_chat_layout.addLayout(duo_chat_row)
        self.duo_chat_box.setVisible(False)
        duo_box_layout.addWidget(self.duo_chat_box)

        duo_layout.addWidget(duo_box)

        # PAGE HELP / AIDE & INFORMATIONS
        help_page = QWidget()
        help_layout = QVBoxLayout(help_page)
        help_layout.setContentsMargins(0, 0, 0, 0)

        help_box = QGroupBox("❓ AIDE, N° DE VERSION & SYSTEM INFO")
        help_box_layout = QVBoxLayout(help_box)

        version_card = QLabel(
            "<b>SOFTWARE VERSION & SYSTEM BUILD</b><br>"
            "<span style='color:#00c8ff;font-size:20px;font-weight:700;'>KaronlineBox V90.2</span><br>"
            "<span style='color:#4ade80;font-size:14px;font-weight:600;'>Date de Build : 01 Septembre 2026</span><br>"
            "<span style='color:#9aa9b7;font-size:12px;'>Fichier d'installation : karonlinebox_setup.exe (~44,3 Mo)</span>"
        )
        version_card.setStyleSheet(
            "background:#08131c;border:1px solid #1b6f91;border-radius:8px;padding:14px;margin-bottom:12px;"
        )
        version_card.setWordWrap(True)
        help_box_layout.addWidget(version_card)

        help_info = QLabel(
            "🔒 <b>RAPPEL DE SÉCURITÉ ET DOUBLE AUTHENTIFICATION :</b><br>"
            "Toute session KaronlineBox requiert obligatoirement que vous soyez connecté à votre compte KaronlineLive sur le site <code>karonlinelive.com</code> avec le MÊME e-mail et mot de passe.<br>"
            "Cette validation garantit le débit de la bonne carte bancaire et le routage direct des demandes mobiles vers ce logiciel.<br><br>"
            "<b>MODUS OPERANDI DES SESSIONS :</b><br>"
            "1. <b>Session Unique Karaoké</b> : Onglet SESSION ➔ Démarrer 'soiree-marc' ➔ Les demandes mobiles arrivent en direct dans l'onglet DEMANDES.<br>"
            "2. <b>Session DUO</b> : Onglet DUO ➔ Démarrer ➔ Transmettre le code DUO-XXXX (ou scanner QR code sur mobile) ➔ Visioconférence FaceTime Karaoké synchronisée !"
        )
        help_info.setStyleSheet("color:#d8dee5;font-size:13px;line-height:1.5;")
        help_info.setWordWrap(True)
        help_box_layout.addWidget(help_info)
        help_box_layout.addStretch()

        help_layout.addWidget(help_box)

        self.queue_area_stack.addWidget(queue_page)
        self.queue_area_stack.addWidget(demands_page)
        self.queue_area_stack.addWidget(favorites_page)
        self.queue_area_stack.addWidget(self.settings_page)
        self.queue_area_stack.addWidget(duo_page)
        self.queue_area_stack.addWidget(help_page)

        # Start on the real queue.
        self.queue_area_stack.setCurrentIndex(0)
        self.kj_auto_on.toggled.connect(self._update_manual_break_enabled)
        self._update_manual_break_enabled()
        left.addWidget(self.queue_area_stack, 1)

        grid.addLayout(left, 0, 0)
        self._apply_kj_video_size()

        vb = QFrame()
        vl = QVBoxLayout(vb)

        lab = QLabel("APERÇU VIDÉO KARAOKÉ")
        lab.setObjectName("section")
        vl.addWidget(lab)

        self.video = QWidget()
        self.video.setMinimumHeight(340)
        self.video.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.video.setAttribute(Qt.WA_NativeWindow, True)
        self.video.setStyleSheet(
            "background:#020305;border:1px solid #16222c;"
        )
        vl.addWidget(self.video)

        try:
            self.gst_player = GStreamerPlayer(
                self.video,
                public_widget=None,
                on_eos=self.on_gst_eos,
                on_error=lambda msg: self.set_status(
                    f"● Erreur GStreamer : {msg}", False
                ),
            )
            self.gst_player.on_audio_eos = self._on_break_audio_eos
            self.gst_player.on_audio_error = lambda msg: self.set_status(
                f"● Break Music : {msg}", False
            )
        except GStreamerError as exc:
            self.set_status(f"● GStreamer : {exc}", False)

        ctl = QHBoxLayout()
        ctl.setContentsMargins(0, 0, 0, 0)
        ctl.setSpacing(4)
        self.back_btn = QPushButton("⏪")
        self.play_btn = QPushButton("▶")
        self.stop_btn = QPushButton("■")
        self.replay_btn = QPushButton("↻")
        self.forward_btn = QPushButton("⏩")

        for b in [
            self.back_btn, self.play_btn, self.stop_btn,
            self.replay_btn, self.forward_btn
        ]:
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            b.setMinimumHeight(32)
            b.setMaximumHeight(38)
            b.setStyleSheet(
                "font-size:16px;font-weight:700;padding:2px 4px;"
            )
            ctl.addWidget(b)

        self.back_btn.clicked.connect(
            lambda: self.gst_player.seek_ms(
                max(0, self.gst_player.position_ms() - 10000)
            ) if self.gst_player else None
        )
        self.forward_btn.clicked.connect(
            lambda: self.gst_player.seek_ms(
                self.gst_player.position_ms() + 10000
            ) if self.gst_player else None
        )
        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn.clicked.connect(self.confirm_stop_video)
        self.replay_btn.clicked.connect(self.replay)

        vl.addLayout(ctl)

        self.progress = QSlider(Qt.Horizontal)
        self.progress.sliderReleased.connect(self._seek_from_slider)
        self.progress.setRange(0, 0)
        self.progress.sliderMoved.connect(
            lambda x: self.gst_player.seek_ms(x) if self.gst_player else None
        )
        vl.addWidget(self.progress)

        # Compact timing display — the only V11 visual change.
        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 2, 0, 2)
        time_row.setSpacing(8)

        self.elapsed_label = QLabel("ÉCOULÉ 00:00")
        self.total_label = QLabel("DURÉE 00:00")
        self.remaining_label = QLabel("RESTE 00:00")

        for lab in [
            self.elapsed_label, self.total_label, self.remaining_label
        ]:
            lab.setAlignment(Qt.AlignCenter)
            lab.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            lab.setMinimumHeight(24)
            lab.setMaximumHeight(28)
            lab.setStyleSheet(
                "color:#ff8a00;font-size:13px;font-weight:700;"
                "background:#080d12;border:1px solid #1d2a35;"
                "border-radius:4px;padding:1px 3px;"
            )

        time_row.addWidget(self.elapsed_label, 1)
        time_row.addWidget(self.total_label, 1)
        time_row.addWidget(self.remaining_label, 1)
        vl.addLayout(time_row)

        grid.addWidget(vb, 0, 1)
        self.kj_video_panel = vb
        self._update_kj_video_width()

        bottom = QHBoxLayout()

        logo_widget = QWidget()
        logo_layout = QVBoxLayout(logo_widget)
        logo_layout.setContentsMargins(8, 4, 8, 4)
        logo_layout.setSpacing(2)
        brand_label = QLabel(
            '🎤 <span style="color:#f4f7fb;">Karonline</span>'
            '<span style="color:#145cff;">Live</span>'
        )
        brand_label.setStyleSheet("font-size:22px;font-weight:700;")
        tagline_label = QLabel("LE SITE DE KARAOKÉ &amp; ANIMATION")
        tagline_label.setStyleSheet(
            "color:#ff2e63;font-size:11px;font-weight:700;"
        )
        logo_layout.addWidget(brand_label)
        logo_layout.addWidget(tagline_label)
        logo_layout.addStretch()
        bottom.addWidget(logo_widget, 1)

        kb = QGroupBox("CHANGEUR DE TONALITÉ")
        kl = QHBoxLayout(kb)
        kl.setContentsMargins(4, 4, 4, 4)
        kl.setSpacing(2)
        self.key_buttons = {}

        for k in range(-6, 7):
            b = QPushButton(f"{k:+d}")
            b.setMinimumWidth(32)
            b.setMaximumHeight(30)
            b.setStyleSheet("font-size:12px;font-weight:600;padding:2px;")
            self.key_buttons[k] = b
            b.clicked.connect(
                lambda checked=False, x=k: self.highlight_key(x)
            )
            kl.addWidget(b)

        bottom.addWidget(kb, 3)

        vb2 = QGroupBox("VOLUME KARAOKÉ & MUSIQUE")
        v = QVBoxLayout(vb2)
        v.setContentsMargins(4, 4, 4, 4)
        v.setSpacing(2)
        v.addLayout(self.volume_row("Karaoké"))
        v.addLayout(self.volume_row("Musique"))
        bottom.addWidget(vb2, 2)

        eb = QGroupBox("ÉCRAN PUBLIC")
        el = QVBoxLayout(eb)
        el.setContentsMargins(4, 4, 4, 4)
        pb = QPushButton(
            "▣  Ouvrir une fenêtre pour la vidéo sur écran externe"
        )
        pb.setMaximumHeight(32)
        pb.setStyleSheet("font-size:11px;padding:3px 6px;")
        pb.clicked.connect(self.open_public_window)
        el.addWidget(pb)

        self.public_duo_webcam_btn = QPushButton(
            "📹  Ouvrir une fenêtre pour la webcam invité sur écran externe (DUO)"
        )
        self.public_duo_webcam_btn.setMaximumHeight(32)
        self.public_duo_webcam_btn.setStyleSheet("font-size:11px;padding:3px 6px;")
        self.public_duo_webcam_btn.setEnabled(False)
        self.public_duo_webcam_btn.setToolTip(
            "Disponible uniquement en session DUO avec un invité connecté "
            "diffusant sa webcam."
        )
        self.public_duo_webcam_btn.clicked.connect(self.open_public_duo_webcam_window)
        el.addWidget(self.public_duo_webcam_btn)
        bottom.addWidget(eb, 1)

        outer.addLayout(grid, 4)
        outer.addLayout(bottom, 1)

    def _favorite_link_item(self, display_text, data):
        item = QListWidgetItem()
        item.setText(display_text)
        item.setData(Qt.UserRole, data)
        return item

    def add_selected_to_favorites(self):
        """Demande MOI ou GROUPE puis ajoute le titre sélectionné."""
        row = self.queue_list.currentRow()
        if row < 0:
            self.set_status("● Sélectionne un chanteur", False)
            return

        name_item = self.queue_list.item(row, 3)
        data = name_item.data(Qt.UserRole) if name_item else None
        if not isinstance(data, dict):
            return

        songs = data.get("songs", [])
        if not songs:
            self.set_status("● Aucune chanson en attente pour ce chanteur", False)
            return

        # The row represents one singer and the first song is the visible
        # song. The ⋮ menu remains available for the other titles.
        song = songs[0][1]

        singer = str(song.singer or data.get("singer", "")).strip()
        artist = str(song.artist or "").strip()
        title = str(song.title or "").strip()
        if not title:
            return

        box = QMessageBox(self)
        box.setWindowTitle("AJOUTER AUX FAVORIS")
        box.setText("Dans quel onglet ajouter cette chanson ?")
        moi = box.addButton("MOI", QMessageBox.AcceptRole)
        groupe = box.addButton("GROUPE", QMessageBox.AcceptRole)
        annuler = box.addButton("ANNULER", QMessageBox.RejectRole)
        box.exec()

        clicked = box.clickedButton()

        if clicked is moi:
            # MOI: duplicate = explicit warning, nothing is added.
            exists = any(
                str(x.get("title", "")).casefold() == title.casefold()
                for x in self.favorites.solo
            )
            if exists:
                QMessageBox.warning(
                    self,
                    "FAVORIS",
                    f'« {title} » existe déjà dans MES FAVORIS.',
                    QMessageBox.Ok,
                )
                return
            self.favorites.add_solo(title, artist)

        elif clicked is groupe:
            # GROUPE: duplicate singer + title = explicit warning.
            exists = any(
                str(x.get("singer", "")).casefold() == singer.casefold()
                and str(x.get("title", "")).casefold() == title.casefold()
                for x in self.favorites.group
            )
            if exists:
                QMessageBox.warning(
                    self,
                    "FAVORIS",
                    f'« {singer} — {artist} — {title} » existe déjà dans GROUPE.',
                    QMessageBox.Ok,
                )
                return
            self.favorites.add_group(singer, title, artist)

        else:
            return

        self.refresh_favorites()
        self.set_status("● Ajouté aux FAVORIS")


    def _delete_selected_queue_rows(self):
        """Supprime complètement les lignes sélectionnées de la file."""
        table=getattr(self,"queue_table",None)
        if table is None:
            return
        rows=sorted({i.row() for i in table.selectionModel().selectedRows()},reverse=True)
        for row in rows:
            singer=""
            item=table.item(row,0)
            if item: singer=item.text().strip()
            removed=False
            for name in ("remove_singer","remove_chanteur","remove_name","remove"):
                fn=getattr(self.queue,name,None)
                if callable(fn):
                    for arg in (singer,row):
                        try:
                            fn(arg); removed=True; break
                        except Exception:
                            pass
                    if removed: break
            if not removed:
                table.removeRow(row)
        if hasattr(self,"refresh_queue"):
            self.refresh_queue()

    def refresh_favorites(self):
        self.solo_favorites_list.clear()
        self.group_favorites_list.clear()

        for fav in self.favorites.solo:
            title = fav.get("title", "")
            label = f"🎵  {title}"
            self.solo_favorites_list.addItem(
                self._favorite_link_item(label, {"mode": "solo", **fav})
            )

        for fav in self.favorites.group:
            singer = fav.get("singer", "")
            title = fav.get("title", "")
            artist = fav.get("artist", "")
            label = f"🎤  {singer} — {title}"
            if artist:
                label += f"  —  {artist}"
            self.group_favorites_list.addItem(
                self._favorite_link_item(label, {"mode": "group", **fav})
            )

        if not self.favorites.solo:
            self.solo_favorites_list.addItem("Aucun favori — disponible hors ligne")
        if not self.favorites.group:
            self.group_favorites_list.addItem("Aucune chanson de soirée enregistrée")

    def request_solo_favorite(self, item):
        self._favorite_item_action(item, "solo")


    def request_group_favorite(self, item):
        self._favorite_item_action(item, "group")


    def _favorite_item_action(self, item, mode):
        """Un clic sur un favori ne déclenche jamais directement une demande."""
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict) or not data.get("title"):
            return

        if mode == "solo":
            singer = "MOI"
            artist = data.get("artist", "")
            title = data.get("title", "")
        else:
            singer = data.get("singer", "")
            artist = data.get("artist", "")
            title = data.get("title", "")

        box = QMessageBox(self)
        box.setWindowTitle("FAVORIS")
        box.setText(title)

        send = box.addButton(
            "ENVOYER UNE DEMANDE", QMessageBox.AcceptRole
        )
        delete = box.addButton(
            "SUPPRIMER DES FAVORIS", QMessageBox.DestructiveRole
        )
        cancel = box.addButton("ANNULER", QMessageBox.RejectRole)
        box.exec()

        clicked = box.clickedButton()

        if clicked is send:
            # This is the only path that sends a favorite request.
            self._request_favorite_for_test(
                singer=singer,
                artist=artist,
                title=title,
            )
            return

        if clicked is delete:
            if mode == "solo":
                index = self.solo_favorites_list.row(item)
                self.favorites.remove_solo(index)
            else:
                index = self.group_favorites_list.row(item)
                self.favorites.remove_group(index)

            self.refresh_favorites()
            self.set_status("● Favori supprimé")
            return

    def _request_favorite_for_test(self, singer, artist, title):
        """Demande favorite après paiement, refusée si la ligne existe déjà."""
        singer = str(singer or "").strip()
        artist = str(artist or "").strip()
        title = str(title or "").strip()

        duplicate = any(
            (str(song.singer or "").strip().casefold() == singer.casefold()
             and str(song.artist or "").strip().casefold() == artist.casefold()
             and str(song.title or "").strip().casefold() == title.casefold())
            for song in self.queue.items
        )

        if duplicate:
            QMessageBox.warning(
                self,
                "DEMANDE DÉJÀ EN ATTENTE",
                f"« {singer} — {artist} — {title} » est déjà dans la FILE D’ATTENTE.",
                QMessageBox.Ok,
            )
            return

        self.add_remote_request(singer, artist, title, 0)
        self.show_main_view("demands")
        self.set_status(
            f"● DEMANDE FAVORI TEST reçue après paiement : {title}",
            True
        )


    def _confirm_oui_non(self, title, text):
        """QMessageBox.question avec des boutons Oui/Non (au lieu de Yes/No,
        qui s'affichent en anglais quand la traduction Qt n'est pas chargee)."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(title)
        box.setText(text)
        yes_btn = box.addButton("Oui", QMessageBox.YesRole)
        box.addButton("Non", QMessageBox.NoRole)
        box.exec()
        return box.clickedButton() is yes_btn

    def clear_my_favorites(self):
        if not self._confirm_oui_non(
            "VIDER MES FAVORIS",
            "Supprimer tous vos favoris personnels (onglet MOI) ?",
        ):
            return
        self.favorites.clear_solo()
        self.refresh_favorites()
        self.show_main_view("favorites")
        self.set_status("● Mes favoris effacés", True)

    def clear_group_favorites(self):
        if not self._confirm_oui_non(
            "VIDER LES FAVORIS DU GROUPE",
            "Supprimer tous les favoris du groupe (onglet GROUPE) ?",
        ):
            return
        self.favorites.clear_group()
        self.refresh_favorites()
        self.show_main_view("favorites")
        self.set_status("● Favoris du groupe effacés", True)

    def inject_20_test_requests(self):
        """Inject 20 ready-made requests, one unique singer per request."""
        test_requests = [
            {"singer": "Marc",     "artist": "Queen",          "title": "Don't Stop Me Now",          "key": -2, "test": True},
            {"singer": "Sophie",   "artist": "ABBA",           "title": "Dancing Queen",              "key":  0, "test": True},
            {"singer": "Julien",   "artist": "Stromae",        "title": "Alors on danse",             "key":  1, "test": True},
            {"singer": "Anne",     "artist": "Adele",          "title": "Rolling in the Deep",        "key": -3, "test": True},
            {"singer": "David",    "artist": "Johnny Hallyday","title": "Allumer le feu",             "key":  0, "test": True},
            {"singer": "Laura",    "artist": "Céline Dion",    "title": "Pour que tu m'aimes encore", "key":  2, "test": True},
            {"singer": "Tom",      "artist": "Goldman",        "title": "Encore un matin",            "key":  0, "test": True},
            {"singer": "Nathalie", "artist": "Zaz",            "title": "Je veux",                    "key": -1, "test": True},
            {"singer": "Philippe", "artist": "Michel Sardou",  "title": "Les lacs du Connemara",      "key":  2, "test": True},
            {"singer": "Isabelle", "artist": "France Gall",    "title": "Résiste",                   "key": -1, "test": True},
            {"singer": "Thomas",   "artist": "Elton John",      "title": "I'm Still Standing",       "key":  1, "test": True},
            {"singer": "Caroline", "artist": "Lady Gaga",      "title": "Shallow",                  "key": -2, "test": True},
            {"singer": "Nicolas",  "artist": "Bruno Mars",     "title": "Uptown Funk",              "key":  0, "test": True},
            {"singer": "Julie",    "artist": "Mylène Farmer",   "title": "Désenchantée",             "key":  2, "test": True},
            {"singer": "Patrick",  "artist": "Daniel Balavoine","title": "Le chanteur",              "key": -1, "test": True},
            {"singer": "Émilie",   "artist": "Rihanna",         "title": "Diamonds",                 "key":  1, "test": True},
            {"singer": "Laurent",  "artist": "Indochine",      "title": "L'Aventurier",              "key":  0, "test": True},
            {"singer": "Sabrina",  "artist": "France Gall",    "title": "Ella, elle l'a",            "key": -2, "test": True},
            {"singer": "Michel",   "artist": "Joe Cocker",      "title": "Unchain My Heart",         "key":  2, "test": True},
        ]

        if not hasattr(self, "requests"):
            self.requests = []

        self.requests.extend(test_requests)

        if hasattr(self, "refresh_requests"):
            self.refresh_requests()

        self._demand_blink_on = True
        if not self._demand_blink_timer.isActive():
            self._demand_blink_timer.start()
        self.update_demands_indicator()

        if hasattr(self, "demands_empty"):
            self.demands_empty.hide()

        self.set_status("● 20 requests de test — 20 chanteurs uniques", True)


    def open_test_request_dialog(self):
        """Local test injector; never contacts the network."""
        dialog = QDialog(self)
        dialog.setWindowTitle("SIMULER UNE REQUEST")
        dialog.setModal(True)
        dialog.resize(500, 300)

        layout = QVBoxLayout(dialog)

        info = QLabel(
            "MODE TEST — injecte localement une request "
            "comme si elle venait du site distant."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QGridLayout()

        singer_edit = QLineEdit()
        artist_edit = QLineEdit()
        title_edit = QLineEdit()

        singer_edit.setPlaceholderText("Nom du chanteur")
        artist_edit.setPlaceholderText("Artiste")
        title_edit.setPlaceholderText("Titre")

        key_spin = QSpinBox()
        key_spin.setRange(-12, 12)
        key_spin.setValue(0)
        key_spin.setSuffix(" demi-ton(s)")

        form.addWidget(QLabel("Chanteur"), 0, 0)
        form.addWidget(singer_edit, 0, 1)
        form.addWidget(QLabel("Artiste"), 1, 0)
        form.addWidget(artist_edit, 1, 1)
        form.addWidget(QLabel("Titre"), 2, 0)
        form.addWidget(title_edit, 2, 1)
        form.addWidget(QLabel("Tonalité"), 3, 0)
        form.addWidget(key_spin, 3, 1)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec() != QDialog.Accepted:
            return

        singer = singer_edit.text().strip()
        artist = artist_edit.text().strip()
        title = title_edit.text().strip()

        if not singer or not artist or not title:
            QMessageBox.warning(
                self,
                "REQUEST INCOMPLÈTE",
                "Chanteur, artiste et titre sont obligatoires."
            )
            return

        self._inject_test_request({
            "singer": singer,
            "artist": artist,
            "title": title,
            "key": key_spin.value(),
            "test": True,
        })

    def _inject_test_request(self, request):
        """Inject one local request into the existing DEMANDES list."""
        if not hasattr(self, "requests"):
            self.requests = []

        self.requests.append(request)

        if hasattr(self, "update_demands_indicator"):
            self.update_demands_indicator()

        if hasattr(self, "refresh_requests"):
            self.refresh_requests()

        self._demand_blink_on = True
        if not self._demand_blink_timer.isActive():
            self._demand_blink_timer.start()
        self.update_demands_indicator()

        if hasattr(self, "demands_empty"):
            self.demands_empty.hide()

    def _apply_kj_video_size(self, checked=False):
        """Applique la taille vidéo KJ : 50 %, 30 % ou 20 % de la largeur réelle."""
        if not hasattr(self, "kj_video_grid"):
            return

        if self.video_size_medium.isChecked():
            mode = "MOYENNE"
            fraction = 0.30
        elif self.video_size_small.isChecked():
            mode = "PETITE"
            fraction = 0.20
        else:
            mode = "GRANDE"
            fraction = 0.50

        self._kj_video_fraction = fraction
        self.settings.setValue("kj_video_size", mode)
        self._update_kj_video_width()

    def _update_kj_video_width(self):
        """Fixe la largeur réelle (en px) du panneau vidéo KJ selon la fraction choisie.

        Les stretch factors de QGridLayout ne répartissent que l'espace
        restant après les tailles minimales des widgets : ils ne donnent
        donc pas un pourcentage fiable de la largeur disponible. On calcule
        ici la largeur réelle disponible et on l'applique comme largeur
        fixe, recalculée à chaque redimensionnement de la fenêtre.
        """
        if not hasattr(self, "kj_video_panel"):
            return

        fraction = getattr(self, "_kj_video_fraction", 0.50)
        container = self.kj_video_grid.parentWidget()
        if container is None:
            return

        margins = self.kj_video_grid.contentsMargins()
        spacing = self.kj_video_grid.horizontalSpacing()
        if spacing < 0:
            spacing = 6

        available = (
            container.width()
            - margins.left() - margins.right()
            - spacing
        )
        if available <= 0:
            return

        self.kj_video_grid.setColumnStretch(0, 1)
        self.kj_video_grid.setColumnStretch(1, 0)
        self.kj_video_panel.setFixedWidth(int(available * fraction))

        self.kj_video_grid.invalidate()
        self.kj_video_grid.activate()

    def volume_row(self, name):
        row = QHBoxLayout()
        label = QLabel(f"{name} 100 %")
        s = QSlider(Qt.Horizontal)
        s.setRange(0, 100)
        s.setValue(100)
        s.valueChanged.connect(
            lambda x, l=label, n=name:
            l.setText(f"{n} {x} %")
        )

        if name == "Karaoké":
            self.karaoke_volume_slider = s
            s.valueChanged.connect(
                lambda x: self.gst_player.set_volume(x)
                if self.gst_player else None
            )
        elif name in ("MUSIC", "Musique"):
            self.music_volume_slider = s
            s.valueChanged.connect(self._music_volume_changed_for_break)

        row.addWidget(label)
        row.addWidget(s)
        return row

    def choose_public_bg_folder(self):
        # Information shown immediately before folder selection.
        info = QMessageBox.information(
            self,
            "FOND ÉCRAN PUBLIC",
            "FORMATS ACCEPTÉS\n"
            "• JPG / JPEG\n"
            "• PNG\n\n"
            "DIMENSION IDÉALE\n"
            "1920 × 1080 px — format 16:9\n\n"
            "MINIMUM CONSEILLÉ\n"
            "1280 × 720 px — format 16:9",
            QMessageBox.Ok,
        )

        folder=QFileDialog.getExistingDirectory(
            self,
            "Choisir le dossier des images de l'écran public"
        )
        if folder:
            self.public_bg_folder.setText(folder)
            self.settings.setValue("public_bg_folder",folder)

    def choose_break_folder(self):
        folder=QFileDialog.getExistingDirectory(self,"Choisir le dossier de la playlist Break Music")
        if folder:
            self.break_folder.setText(folder); self.settings.setValue("break_folder",folder)

    def _update_manual_break_enabled(self):
        # DEFINITIVE RULE:
        # AUTO ON  -> Break Music is mandatory ON; no ON/OFF choice is shown.
        # AUTO OFF -> KJ gets the ON/OFF choice.
        auto_on = self.kj_auto_on.isChecked()

        if auto_on:
            self.break_manual_on.setChecked(True)
            self.break_manual_off.setChecked(False)
            self.break_manual_row.hide()
        else:
            self.break_manual_row.show()
            self.break_manual_on.setEnabled(True)
            self.break_manual_off.setEnabled(True)


    def load_settings(self):
        self.public_bg_on.setChecked(self.settings.value("public_bg_on",True,type=bool))
        self.public_bg_off.setChecked(not self.public_bg_on.isChecked())
        self.public_bg_folder.setText(self.settings.value("public_bg_folder","",type=str))
        self.public_bg_rotation.setValue(self.settings.value("public_bg_rotation",30,type=int))
        self.next_warning_on.setChecked(self.settings.value("next_warning_on",True,type=bool))
        self.next_warning_off.setChecked(not self.next_warning_on.isChecked())
        self.next_warning_duration.setValue(self.settings.value("next_warning_duration",10,type=int))
        self.break_folder.setText(self.settings.value("break_folder","",type=str))
        self.kj_auto_on.setChecked(self.settings.value("kj_auto_on",False,type=bool))
        self.kj_auto_off.setChecked(not self.kj_auto_on.isChecked())
        self.break_manual_on.setChecked(self.settings.value("break_manual_on",True,type=bool))
        self.break_manual_off.setChecked(not self.break_manual_on.isChecked())
        self.break_auto_duration.setValue(self.settings.value("break_auto_duration",30,type=int))
        self.public_bg_on.toggled.connect(lambda v:self.settings.setValue("public_bg_on",v))
        self.public_bg_rotation.valueChanged.connect(lambda v:self.settings.setValue("public_bg_rotation",v))
        self.next_warning_on.toggled.connect(lambda v:self.settings.setValue("next_warning_on",v))
        self.next_warning_duration.valueChanged.connect(lambda v:self.settings.setValue("next_warning_duration",v))
        self.next_warning_on.toggled.connect(lambda v: self._schedule_next_warning())
        self.next_warning_duration.valueChanged.connect(lambda v: self._schedule_next_warning())
        self.kj_auto_on.toggled.connect(lambda v:self.settings.setValue("kj_auto_on",v))
        self.break_manual_on.toggled.connect(lambda v:self.settings.setValue("break_manual_on",v))
        self.break_auto_duration.valueChanged.connect(lambda v:self.settings.setValue("break_auto_duration",v))

    def show_main_view(self, view):
        # LIVE/SUIVANT ne sont utiles que pour la FILE D'ATTENTE : masques
        # ailleurs pour rendre de la hauteur aux formulaires (demandes,
        # favoris, reglages).
        self.live_box.setVisible(view == "queue")
        self.next_box.setVisible(view == "queue")

        if view == "duo":
            self.queue_area_stack.setCurrentIndex(4)
        elif view == "demands":
            self.queue_area_stack.setCurrentIndex(1)
            self.refresh_requests()
        elif view == "queue":
            self.queue_area_stack.setCurrentIndex(0)
        elif view == "favorites":
            self.queue_area_stack.setCurrentIndex(2)
            self.refresh_favorites()
        elif view == "settings":
            self.queue_area_stack.setCurrentIndex(3)
            self._update_manual_break_enabled()
        elif view == "help":
            self.queue_area_stack.setCurrentIndex(5)

        # Force un re-layout + repaint immediats : masquer/afficher live_box
        # et next_box laissait parfois un residu visuel (ancien contenu non
        # efface) tant que la fenetre n'etait pas redimensionnee/rafraichie.
        central = self.centralWidget()
        if central is not None and central.layout() is not None:
            central.layout().invalidate()
            central.layout().activate()
        self.update()

        self.update_demands_indicator()

    def _blink_demands(self):
        if not getattr(self, "requests", []):
            self._demand_blink_timer.stop()
            self._demand_blink_on = False
            self.update_demands_indicator()
            return

        self._demand_blink_on = not self._demand_blink_on
        self.update_demands_indicator()

    def update_demands_indicator(self):
        count = len(getattr(self, "requests", []))

        if count == 0:
            self._demand_blink_timer.stop()
            self._demand_blink_on = False
            self.demands_indicator.hide()
            self.demands_btn.setText("DEMANDES")
            return

        self.demands_indicator.show()
        self.demands_btn.setText(f"DEMANDES  {count}")

        # V23: vivid yellow when ON, much dimmer when OFF.
        if self._demand_blink_on:
            self.demands_indicator.setStyleSheet(
                "background:#FFFF00;"
                "border:1px solid #FFFFFF;"
                "border-radius:2px;"
            )
        else:
            self.demands_indicator.setStyleSheet(
                "background:#4A4A00;"
                "border:1px solid #6A6A00;"
                "border-radius:2px;"
            )


    def add_remote_request(self, singer, artist, title, key=0):
        """Entry point for the future remote-request receiver."""
        self.requests.append({
            "singer": str(singer),
            "artist": str(artist),
            "title": str(title),
            "key": int(key),
        })
        self.refresh_requests()
        if not self._demand_blink_timer.isActive():
            self._demand_blink_on = True
            self._demand_blink_timer.start()
        self.update_demands_indicator()

    def refresh_requests(self):
        self.requests_table.setRowCount(0)
        self.demands_empty.setVisible(not self.requests)

        for row, request in enumerate(self.requests):
            self.requests_table.insertRow(row)
            self.requests_table.setRowHeight(row, 44)

            for col, value in enumerate([
                request["singer"],
                request["artist"],
                request["title"],
            ]):
                self.requests_table.setItem(
                    row, col, QTableWidgetItem(str(value))
                )

            key_widget = QWidget()
            key_layout = QHBoxLayout(key_widget)
            key_layout.setContentsMargins(2, 0, 2, 0)
            key_layout.setSpacing(2)

            key_minus = QPushButton("−")
            key_value = QLabel(f'{request["key"]:+d}')
            key_plus = QPushButton("+")

            key_value.setAlignment(Qt.AlignCenter)
            key_value.setMinimumWidth(28)

            key_button_style = (
                "QPushButton{"
                "background:#08131c;"
                "color:#00ffff;"
                "border:1px solid #1b6f91;"
                "border-radius:3px;"
                "font-size:14px;"
                "font-weight:700;"
                "padding:0px;"
                "min-width:22px;"
                "max-width:22px;"
                "min-height:22px;"
                "max-height:22px;"
                "}"
                "QPushButton:hover{"
                "background:#123544;"
                "}"
            )
            key_minus.setStyleSheet(key_button_style)
            key_plus.setStyleSheet(key_button_style)
            key_value.setStyleSheet(
                "QLabel{"
                "color:#00ffff;"
                "font-size:15px;"
                "font-weight:700;"
                "padding:0px 3px;"
                "}"
            )

            def make_request_key_callback(target_request, value_label, delta):
                def callback(checked=False):
                    new_value = max(
                        -6, min(6, int(target_request["key"]) + delta)
                    )
                    target_request["key"] = new_value
                    value_label.setText(f"{new_value:+d}")
                return callback

            key_minus.clicked.connect(
                make_request_key_callback(request, key_value, -1)
            )
            key_plus.clicked.connect(
                make_request_key_callback(request, key_value, +1)
            )

            key_layout.addWidget(key_minus)
            key_layout.addWidget(key_value)
            key_layout.addWidget(key_plus)
            self.requests_table.setCellWidget(row, 3, key_widget)

            actions = QWidget()
            action_layout = QHBoxLayout(actions)
            action_layout.setContentsMargins(2, 2, 2, 2)
            action_layout.setSpacing(4)

            existing_btn = QPushButton("Nom existant")
            new_btn = QPushButton("Nouveau nom")
            delete_btn = QPushButton("✕ Supprimer")

            # V31: every request is initially handled as "Nouveau nom".
            # The KJ may explicitly choose "Nom existant" when appropriate.
            existing_btn.setEnabled(True)
            new_btn.setEnabled(True)

            action_btn_common = (
                "QPushButton{"
                "font-size:13px;"
                "font-weight:600;"
                "padding:5px 10px;"
                "border-radius:4px;"
                "min-height:26px;"
                "}"
            )
            existing_btn.setStyleSheet(
                action_btn_common +
                "QPushButton{background:#0b1c28;border:1px solid #1c5270;color:#8ccbfa;}"
                "QPushButton:hover{background:#13344b;border-color:#2a7cb0;}"
            )
            new_btn.setStyleSheet(
                action_btn_common +
                "QPushButton{background:#092536;border:1px solid #00a7ff;color:#00a7ff;font-weight:700;}"
                "QPushButton:hover{background:#103c57;}"
            )
            delete_btn.setStyleSheet(
                action_btn_common +
                "QPushButton{background:#201012;border:1px solid #7a3030;color:#ff6b6b;font-weight:700;}"
                "QPushButton:hover{background:#381619;}"
            )

            action_layout.addWidget(existing_btn)
            action_layout.addWidget(new_btn)
            action_layout.addWidget(delete_btn)

            existing_btn.clicked.connect(
                lambda checked=False, r=row:
                self.validate_request(r, False)
            )
            new_btn.clicked.connect(
                lambda checked=False, r=row:
                self.validate_request(r, True)
            )
            delete_btn.clicked.connect(
                lambda checked=False, r=row:
                self.delete_request(r)
            )

            self.requests_table.setCellWidget(row, 4, actions)

    def delete_request(self, row):
        """Supprime une demande de DEMANDES sans l'ajouter a la file d'attente."""
        if not (0 <= row < len(self.requests)):
            return
        removed = self.requests.pop(row)
        self.refresh_requests()
        self.update_demands_indicator()
        self.set_status(f"● Demande supprimée : {removed.get('title', '')}", True)

    # ------------------------------------------------------------------
    # KARONLINEBOX DUO — Méthodes et événements de session DUO
    # ------------------------------------------------------------------
    def _start_duo_session_action(self):
        if not self.ensure_central_login():
            QMessageBox.warning(
                self,
                "CONNEXION COMPTE OBLIGATOIRE",
                "Vous devez obligatoirement être connecté à votre compte KaronlineLive pour créer une session DUO."
            )
            return
        session_name = getattr(self, "_active_relay_session", None)
        if not session_name:
            QMessageBox.warning(
                self,
                "SESSION DE DEMANDES REQUISE",
                "Démarrez d'abord votre session avec un nom de soirée.\n\n"
                "En DUO, les demandes de titres sont exclusivement relayées "
                "vers cette session de l'hôte et rattachées à son compte."
            )
            return
        ok, code, qr_url = self.duo_manager.create_session(session_name)
        if ok:
            self.duo_code_label.setText(f"🟢 {code}")
            self.duo_start_btn.setEnabled(False)
            self.duo_join_btn.setEnabled(False)
            self.duo_stop_btn.setText("✕ FERMER LA SESSION POUR TOUS")
            self.duo_stop_btn.setEnabled(True)
            self.duo_audio_label.setText("○ Audio DUO en attente d'un invité")
            self.duo_audio_label.setStyleSheet("color:#aeb7bf;font-size:13px;font-weight:600;")
            self.duo_chat_history.clear()
            self.duo_chat_box.setVisible(True)
            self.set_status(f"● Session DUO Hôte {code} démarrée", True)
            self.duo_qr_box.hide()
        else:
            QMessageBox.warning(
                self,
                "ÉCHEC CRÉATION DUO",
                f"Impossible de démarrer la session DUO Hôte :\n\n{code}"
            )

    def _join_duo_session_action(self):
        if not self.ensure_central_login():
            QMessageBox.warning(
                self,
                "CONNEXION COMPTE OBLIGATOIRE",
                "Vous devez vous connecter à un compte KaronlineLive depuis "
                "KaronlineBox pour rejoindre une session DUO desktop."
            )
            return
        code = self.duo_join_input.text().strip().upper()
        if not code:
            QMessageBox.warning(self, "CODE DUO MANQUANT", "Veuillez entrer le code DUO transmis par l'hôte (ex: DUO-8492).")
            return
        ok, msg = self.duo_manager.join_session(code, guest_name="Invité Desktop")
        if ok:
            # Un invité ne peut pas garder un nom de soirée personnel pendant
            # le DUO : les demandes restent exclusivement chez l'hôte.
            self._unregister_relay_session()
            self.duo_code_label.setText(f"🟢 {code} (Invité)")
            self.duo_guest_label.setText("🟢 Connecté à l'hôte")
            self.duo_start_btn.setEnabled(False)
            self.duo_join_btn.setEnabled(False)
            self.duo_stop_btn.setText("✕ QUITTER LA SESSION")
            self.duo_stop_btn.setEnabled(True)
            self.session_start_btn.setEnabled(False)
            self.set_status(f"● {msg}", True)
            self._ensure_duo_overlay("Hôte DUO")
            self._set_duo_guest_controls_locked(True)
            self.duo_chat_history.clear()
            self.duo_chat_box.setVisible(True)
        else:
            QMessageBox.warning(self, "CONNEXION DUO IMPOSSIBLE", msg)

    def _fetch_duo_qr_pixmap(self, qr_url: str):
        try:
            req = urllib.request.Request(qr_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
            pixmap = QPixmap()
            pixmap.loadFromData(data)
            QTimer.singleShot(0, lambda: self._apply_duo_qr_pixmap(pixmap))
        except Exception:
            pass

    def _apply_duo_qr_pixmap(self, pixmap: QPixmap):
        if not pixmap.isNull():
            self.duo_qr_label.setPixmap(pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            self.duo_qr_box.show()

    def _stop_duo_session_action(self):
        self.duo_manager.close_session()
        self._set_duo_guest_controls_locked(False)
        self.duo_code_label.setText("○ Aucune session DUO active")
        self.duo_guest_label.setText("○ Aucun invité connecté")
        self.duo_guest_label.setStyleSheet("color:#aeb7bf;font-size:15px;font-weight:600;")
        self.duo_audio_label.setText("○ Audio DUO arrêté")
        self.duo_audio_label.setStyleSheet("color:#aeb7bf;font-size:13px;font-weight:600;")
        self.duo_qr_box.hide()
        self.duo_start_btn.setEnabled(True)
        self.duo_join_btn.setEnabled(True)
        self.duo_stop_btn.setText("✕ FERMER LA SESSION")
        self.duo_stop_btn.setEnabled(False)
        self.session_start_btn.setEnabled(True)
        if self.duo_overlay:
            self.duo_overlay.set_connected_status(False)
        self.set_status("● Session DUO fermée", True)

    def _set_duo_guest_controls_locked(self, locked: bool):
        """L'hôte garde seul les commandes video et le volume karaoke en DUO."""
        for control_name in (
            "back_btn", "play_btn", "stop_btn", "replay_btn", "forward_btn",
            "progress", "karaoke_volume_slider", "demands_btn", "queue_nav_btn",
            "favorites_btn", "settings_btn", "help_btn", "session_start_btn",
            "clear_queue_button", "queue_remove_btn", "add_favorite_button",
            "queue_up_btn", "queue_down_btn", "queue_play_btn", "queue_list",
        ):
            control = getattr(self, control_name, None)
            if control is not None:
                control.setEnabled(not locked)

    def _send_duo_chat_message(self):
        text = self.duo_chat_input.text().strip()
        if not text or not self.duo_manager.active_code:
            return
        self.duo_manager.send_chat_message(text)
        self.duo_chat_input.clear()

    def _on_duo_chat_messages(self, messages: list):
        for message in messages:
            sender = str(message.get("sender", "Participant"))
            text = str(message.get("text", "")).strip()
            if text:
                self.duo_chat_history.appendPlainText(f"{sender} : {text}")

    def _on_duo_session_created(self, code: str, qr_url: str):
        self.duo_code_label.setText(f"🟢 {code}")

    def _on_duo_guest_connected(self, guest_info: dict):
        guest_name = guest_info.get("name", "Invité")
        self.duo_guest_label.setText(f"🟢 Connecté : {guest_name}")
        self.duo_guest_label.setStyleSheet("color:#4ade80;font-size:15px;font-weight:700;")
        self.set_status(f"● DUO : {guest_name} a rejoint la session !", True)
        self._duo_guest_connected_flag = True
        self._update_public_duo_webcam_button_state()
        self.duo_chat_box.setVisible(True)
        if self.duo_overlay:
            self.duo_overlay.set_guest_name(guest_name)
            self.duo_overlay.set_connected_status(True)
            self.duo_overlay.show()

    def _on_duo_guest_disconnected(self):
        self.duo_guest_label.setText("○ Aucun invité connecté")
        self.duo_guest_label.setStyleSheet("color:#aeb7bf;font-size:15px;font-weight:600;")
        self.set_status("● DUO : L'invité s'est déconnecté", False)
        self._duo_guest_connected_flag = False
        self._last_duo_guest_frame_data = None
        self._update_public_duo_webcam_button_state()
        self.duo_chat_box.setVisible(False)
        if self.duo_overlay:
            self.duo_overlay.set_connected_status(False)

    def _on_duo_session_closed(self):
        self.duo_code_label.setText("○ Aucune session DUO active")
        self.duo_guest_label.setText("○ Aucun invité connecté")
        self.duo_guest_label.setStyleSheet("color:#aeb7bf;font-size:15px;font-weight:600;")
        self._duo_guest_connected_flag = False
        self._last_duo_guest_frame_data = None
        self._update_public_duo_webcam_button_state()
        self._set_duo_guest_controls_locked(False)
        self.duo_chat_box.setVisible(False)
        self.duo_start_btn.setEnabled(True)
        self.duo_join_btn.setEnabled(True)
        self.duo_stop_btn.setEnabled(False)
        self.session_start_btn.setEnabled(True)
        if self._public_screen_mode == "duo_webcam":
            self._public_screen_mode = "video"
            self._show_public_background()
        if self.duo_overlay:
            self.duo_overlay.set_connected_status(False)

    def _update_public_duo_webcam_button_state(self):
        if not getattr(self, "public_duo_webcam_btn", None):
            return
        enabled = bool(
            self.duo_manager.is_host
            and self.duo_manager.active_code
            and self._duo_guest_connected_flag
        )
        self.public_duo_webcam_btn.setEnabled(enabled)

    def _on_duo_guest_frame_received(self, frame_data: str):
        if not self.duo_manager.is_host:
            return
        self._last_duo_guest_frame_data = frame_data
        if self.duo_overlay:
            self.duo_overlay.update_frame(frame_data)
        if self._public_screen_mode == "duo_webcam" and self.public_duo_webcam_label:
            self._render_duo_frame_on_label(self.public_duo_webcam_label, frame_data)

    def _on_duo_audio_status(self, message: str):
        self.duo_audio_label.setText(f"● {message}")
        self.duo_audio_label.setStyleSheet("color:#4ade80;font-size:13px;font-weight:600;")
        self.set_status(f"● {message}", True)

    def _on_duo_audio_error(self, message: str):
        self.duo_audio_label.setText(f"● {message}")
        self.duo_audio_label.setStyleSheet("color:#ff6b6b;font-size:13px;font-weight:600;")
        self.set_status(f"● {message}", False)

    def _on_duo_webcam_status(self, message: str, ok: bool):
        self.duo_video_label.setText(f"● {message}")
        color = "#4ade80" if ok else "#ff6b6b"
        self.duo_video_label.setStyleSheet(f"color:{color};font-size:13px;font-weight:600;")

    def _on_duo_host_frame_received(self, frame_data: str):
        if self.duo_overlay and not self.duo_manager.is_host:
            self.duo_overlay.update_frame(frame_data)

    def open_audio_setup_dialog(self):
        """Ouvre le dialogue de configuration audio VST micro/casque."""
        dialog = AudioSetupDialog(self, monitor=self.mic_monitor)
        dialog.exec()

    def _on_duo_sync_tick_received(self, sync_payload: dict):
        if self.duo_manager.is_host or not sync_payload:
            return
        song_title = str(sync_payload.get("song", "")).strip()
        singer = str(sync_payload.get("singer", "")).strip()
        artist = str(sync_payload.get("artist", "")).strip()
        try:
            key = max(-6, min(6, int(sync_payload.get("key", 0))))
        except (TypeError, ValueError):
            key = 0
        pos_ms = sync_payload.get("position_ms", 0)
        is_playing = sync_payload.get("is_playing", False)

        if not is_playing:
            self._duo_guest_pending_title = ""
            if self.gst_player and self.audio_owner == "karaoke":
                self.gst_player.stop()
                self.audio_owner = "none"
                self._show_public_background()
                self.play_btn.setText("▶")
                self.progress.setRange(0, 0)
                self.set_video_time_labels(0, 0)
                self.set_status("● Vidéo arrêtée par l'hôte DUO", True)
            return

        if not song_title or not self.gst_player:
            return

        current_title = self.queue.current.title if self.queue.current else ""
        if current_title.casefold() != song_title.casefold() or self.audio_owner != "karaoke":
            # Un tick arrive toutes les 0,15 s : sans ce verrou, le meme titre
            # relancerait une recherche et un telechargement a chaque tick.
            if getattr(self, "_duo_guest_pending_title", "") == song_title.casefold():
                return
            self._duo_guest_pending_title = song_title.casefold()

            match_file = None
            for s_id, path_str in self.song_files.items():
                if song_title.casefold() in Path(path_str).stem.casefold():
                    match_file = path_str
                    break
            if not match_file and self.media_dir.exists():
                for f in self.media_dir.iterdir():
                    if f.is_file() and f.suffix.lower() == ".mp4" and song_title.casefold() in f.stem.casefold():
                        match_file = str(f)
                        break

            # If not found locally, attempt to download from central library
            if not match_file and hasattr(self, "_resolve_remote_filename"):
                try:
                    self.set_status(f"● DUO : recherche de « {song_title} »...", True)
                    remote_fn = self._resolve_remote_filename(artist, song_title)
                    if remote_fn and hasattr(self, "_download_from_central_library"):
                        match_file = self._download_from_central_library(remote_fn)
                except Exception:
                    match_file = None

            if match_file:
                guest_song = Song(singer, artist, song_title, key)
                self.song_files[id(guest_song)] = match_file
                self.queue.current = guest_song
                self.play_song_object(guest_song)
                if pos_ms > 0:
                    self.gst_player.seek_ms(pos_ms)
            else:
                self.set_status(
                    f"● DUO : « {song_title} » introuvable sur ce poste", False
                )
        else:
            if self.current_key_value != key:
                self.highlight_key(key)
            if is_playing and abs(self.gst_player.position_ms() - pos_ms) > 1200:
                self.gst_player.seek_ms(pos_ms)

    def _toggle_duo_overlay(self):
        if self.duo_overlay:
            if self.duo_overlay.isVisible():
                self.duo_overlay.hide()
            else:
                self.duo_overlay.show()

    def start_public_session(self):
        """Enregistre le nom de session et démarre le relais central (aucun
        tunnel/port entrant requis : uniquement une connexion sortante).
        Requiert obligatoirement une authentification valide au compte KaronlineLive du KJ/Hôte
        ET la présence d'une connexion simultanée sur le site karonlinelive.com avec le même compte."""
        if self.duo_manager.active_code and not self.duo_manager.is_host:
            QMessageBox.information(
                self,
                "MODE DUO INVITÉ ACTIF",
                "Vous êtes invité dans une session DUO.\n\n"
                "Quittez d'abord le mode DUO avant de démarrer une session "
                "de demandes avec votre propre nom de soirée."
            )
            return
        if not self.ensure_central_login():
            QMessageBox.warning(
                self,
                "CONNEXION COMPTE OBLIGATOIRE",
                "Connexion obligatoire à votre compte KaronlineLive (e-mail & mot de passe).\n\n"
                "Cette étape garantit que votre session KaronlineBox est authentifiée,\n"
                "que la facturation s'effectue sur le bon compte et que les demandes de titres\n"
                "envoyées et payées sur karonlinelive.com arrivent avec certitude sur ce poste."
            )
            self.set_status(
                "● Connexion au compte requise pour démarrer une session",
                False,
            )
            return

        if not self._site_session_active():
            QMessageBox.warning(
                self,
                "CONNEXION SITE REQUISE",
                "Pour démarrer votre session KaronlineBox, vous devez d'abord vous connecter\n"
                "sur karonlinelive.com (navigateur) avec les MÊMES identifiants (e-mail & mot de passe).\n\n"
                "Cette double authentification garantit le débit de la bonne carte bancaire\n"
                "et le routage certain des demandes payées depuis les mobiles."
            )
            self.set_status(
                "● Connexion simultanée site + KaronlineBox requise (mêmes identifiants)",
                False,
            )
            return

        name = re.sub(
            r"[^a-z0-9_-]", "-",
            self.session_name_input.text().strip().lower()
        ).strip("-")

        if not name:
            QMessageBox.warning(
                self, "NOM DE SESSION MANQUANT",
                "Entrez un nom de session (ex. soiree-marc)."
            )
            return

        self.session_start_btn.setEnabled(False)
        self.session_status_label.setText("● Démarrage du serveur local...")

        if not self._is_port_open("127.0.0.1", 8765):
            ok, catalogue_error = self._start_local_catalogue_server()
            if not ok:
                self.session_start_btn.setEnabled(True)
                self.session_status_label.setText(
                    f"● Impossible de démarrer le serveur catalogue local : {catalogue_error}"
                )
                return

        self.session_status_label.setText(f"● Enregistrement de la session ({self.central_auth.email})...")
        success, error_info = self._register_relay_session(name)
        if not success:
            error_code = (error_info or {}).get("error", "")
            if error_code in ("AUTH REQUIRED", "TOKEN INVALID", "HTTP 401"):
                self._central_session_ok = False
                self.central_auth.clear()
                self.update_account_ui()
                QMessageBox.warning(
                    self,
                    "SESSION COMPTE EXPIRÉE",
                    "Votre jeton de compte KaronlineLive a expiré.\n"
                    "Veuillez saisir à nouveau vos identifiants."
                )
                self.ensure_central_login()
                self.session_start_btn.setEnabled(True)
                return
            elif error_code == "SESSION_ALREADY_EXISTS":
                existing_name = (error_info or {}).get("existing_name", "")
                confirmed = self._confirm_oui_non(
                    "SESSION DÉJÀ EXISTANTE",
                    f"Une session (« {existing_name} ») est déjà active ailleurs avec le compte {self.central_auth.email}.\nLa remplacer par « {name} » ?",
                )
                if confirmed:
                    success, error_info = self._register_relay_session(name, force=True)
            if not success:
                self.session_start_btn.setEnabled(True)
                self.session_status_label.setText(
                    f"● Erreur d'enregistrement : {(error_info or {}).get('error', 'inconnue')}"
                )
                return

        self._relay_stop = False
        self._active_relay_session = name
        self._relay_thread = threading.Thread(
            target=self._relay_loop, args=(name,), daemon=True
        )
        self._relay_thread.start()

        self.session_status_label.setText(
            f"● Session active rattachée à {self.central_auth.email} :"
            f" « {name} » — les demandes mobiles sur karonlinelive.com vous parviennent en direct."
        )
        self.set_status(f"● Session « {name} » démarrée ({self.central_auth.email})", True)
        self.session_start_btn.setEnabled(True)

    @staticmethod
    def _is_port_open(host, port):
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(0.3)
        try:
            probe.connect((host, port))
            return True
        except OSError:
            return False
        finally:
            probe.close()

    @staticmethod
    def _start_local_catalogue_server():
        """Démarre le serveur catalogue local (port 8765) dans un thread du
        process courant : un subprocess `sys.executable lan_server.py` ne
        fonctionne pas dans un exe PyInstaller gelé (sys.executable est
        l'exe lui-même, pas un interpréteur python).
        Renvoie (True, "") si ok, sinon (False, "message d'erreur")."""
        media_dir = default_media_dir()
        try:
            media_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            print(f"LOCAL CATALOGUE SERVER ERROR = {exc}", flush=True)
            return False, str(exc)
        try:
            import lan_server
            # 127.0.0.1 uniquement : le relais central appelle ce serveur en
            # boucle locale (127.0.0.1:8765), aucune exposition LAN requise.
            # Se lier sur 0.0.0.0 declenche parfois une invite pare-feu
            # Windows (ou un blocage silencieux par un antivirus/EDR) sur
            # des machines clientes, ce que 127.0.0.1 evite completement.
            server = lan_server.ThreadingHTTPServer(("127.0.0.1", 8765), lan_server.RequestHandler)
            server.library = media_dir
            server.request_port = 8766
        except Exception as exc:  # noqa: BLE001 - on veut voir toute cause possible
            print(f"LOCAL CATALOGUE SERVER ERROR = {exc}", flush=True)
            return False, str(exc)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return True, ""

    def _site_session_active(self):
        """True si une session karonlinelive.com (navigateur) est active
        pour ce meme compte, en plus de la connexion KaronlineBox."""
        request = urllib.request.Request(
            "https://api.karonlinelive.com/auth/session-pair-status",
            headers={
                **self.central_auth.authorization_header(),
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
            },
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                data = json.loads(response.read().decode("utf-8"))
            return bool(data.get("site_active"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return False

    def _register_relay_session(self, name, force=False):
        """Enregistre le nom de session aupres du relais central. Renvoie
        (True, None) si ok, sinon (False, {"error": ..., ...})."""
        payload = json.dumps({
            "name": name, "mode": "relay", "force": force,
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.karonlinelive.com/session/register",
            data=payload,
            headers={
                **self.central_auth.authorization_header(),
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status != 200:
                    return False, {"error": f"HTTP {response.status}"}
            return True, None
        except urllib.error.HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                body = {}
            return False, body
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return False, {"error": str(exc)}

    def _unregister_relay_session(self):
        name = getattr(self, "_active_relay_session", None)
        if not name:
            return
        self._relay_stop = True
        request = urllib.request.Request(
            "https://api.karonlinelive.com/session/unregister",
            data=json.dumps({"name": name}).encode("utf-8"),
            headers={
                **self.central_auth.authorization_header(),
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=5).close()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            pass
        self._active_relay_session = None

    def _relay_loop(self, name):
        """Boucle de fond : long-poll sortant vers le relais central, puis
        exécution locale (catalogue/demande) et renvoi du résultat. Ne requiert
        aucun port entrant ni tunnel — uniquement des requêtes HTTP sortantes."""
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0"
        quoted_name = urllib.parse.quote(name)
        while not getattr(self, "_relay_stop", False):
            try:
                pull_request = urllib.request.Request(
                    f"https://api.karonlinelive.com/relay/pull?name={quoted_name}",
                    headers={
                        **self.central_auth.authorization_header(),
                        "User-Agent": user_agent,
                    },
                    method="GET",
                )
                with urllib.request.urlopen(pull_request, timeout=30) as response:
                    data = json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    print(f"RELAY SESSION '{name}' NOT FOUND ON SERVER, AUTO RE-REGISTERING...", flush=True)
                    self._register_relay_session(name, force=True)
                elif exc.code == 401:
                    print("RELAY TOKEN EXPIRED. CLEARING SESSION...", flush=True)
                    self._central_session_ok = False
                    self.central_auth.clear()
                    self.update_account_ui()
                    break
                time.sleep(2)
                continue
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                time.sleep(2)
                continue

            job_id = data.get("job_id")
            if not job_id:
                continue

            job_type = data.get("type")
            payload = data.get("payload") or {}
            status, body = 500, {"error": "UNKNOWN JOB TYPE"}
            try:
                if job_type == "catalogue":
                    local_songs = []
                    try:
                        with urllib.request.urlopen(
                            "http://127.0.0.1:8765/catalogue", timeout=5
                        ) as local_response:
                            status = local_response.status
                            local_songs = json.loads(
                                local_response.read().decode("utf-8")
                            )
                    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                        status = 200
                        local_songs = []

                    # Phase de test amis/famille : la bibliotheque locale de
                    # ce poste peut n'etre qu'un cache partiel (fichiers deja
                    # telecharges a la demande). On fusionne TOUJOURS avec la
                    # bibliotheque partagee du PC fixe pour ne jamais montrer
                    # un catalogue tronque au seul dernier titre telecharge.
                    local_names = {
                        str(song.get("filename", "")).strip().casefold()
                        for song in local_songs
                        if song.get("filename")
                    }
                    merged = list(local_songs)
                    try:
                        central_request = urllib.request.Request(
                            "https://api.karonlinelive.com/catalogue",
                            headers={"User-Agent": user_agent},
                            method="GET",
                        )
                        with urllib.request.urlopen(central_request, timeout=8) as central_response:
                            central_songs = json.loads(
                                central_response.read().decode("utf-8")
                            )
                        for song in central_songs:
                            filename = str(song.get("filename", "")).strip()
                            if filename and filename.casefold() not in local_names:
                                merged.append(song)
                    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                        pass

                    merged.sort(
                        key=lambda song: (
                            str(song.get("artist", "")).casefold(),
                            str(song.get("title", "")).casefold(),
                        )
                    )
                    status, body = 200, merged
                elif job_type == "request-demand":
                    demand_payload = dict(payload)
                    demand_payload["client_ip"] = "127.0.0.1"
                    local_request = urllib.request.Request(
                        "http://127.0.0.1:8765/request-demand",
                        data=json.dumps(demand_payload).encode("utf-8"),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    )
                    try:
                        with urllib.request.urlopen(local_request, timeout=5) as local_response:
                            status = local_response.status
                            body = json.loads(local_response.read().decode("utf-8"))
                    except urllib.error.HTTPError as http_error:
                        status = http_error.code
                        body = json.loads(http_error.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
                status, body = 500, {"error": str(exc)}

            try:
                push_request = urllib.request.Request(
                    "https://api.karonlinelive.com/relay/push",
                    data=json.dumps({
                        "job_id": job_id, "status": status, "body": body,
                    }).encode("utf-8"),
                    headers={
                        **self.central_auth.authorization_header(),
                        "Content-Type": "application/json",
                        "User-Agent": user_agent,
                    },
                    method="POST",
                )
                urllib.request.urlopen(push_request, timeout=10).close()
            except (urllib.error.URLError, TimeoutError, OSError):
                pass

    def _known_singer_names(self):
        names = set()

        for name in getattr(self, "singer_roster", {}).values():
            if name:
                names.add(str(name).strip().casefold())

        for song in getattr(self.queue, "items", []):
            if song.singer:
                names.add(song.singer.strip().casefold())

        return names


    def _singer_exists(self, singer):
        normalized = (singer or "").strip().casefold()
        return bool(normalized and normalized in self._known_singer_names())

    def _resolved_singer_name(self, received_name):
        key = (received_name or "").strip().casefold()
        return self.singer_aliases.get(key, received_name.strip())

    def _rename_collision(self, requested_name):
        while True:
            renamed, ok = QInputDialog.getText(
                self,
                "Ce nom existe déjà, renommer",
                f'Le nom "{requested_name}" existe déjà dans la FILE D’ATTENTE.\n\n'
                "Introduis le nom qui permettra de différencier ce chanteur :",
                text=""
            )

            if not ok:
                return None

            renamed = renamed.strip()
            if not renamed:
                QMessageBox.warning(
                    self,
                    "NOM INVALIDE",
                    "Le nouveau nom ne peut pas être vide."
                )
                continue

            if self._singer_exists(renamed):
                requested_name = renamed
                QMessageBox.warning(
                    self,
                    "Ce nom existe déjà, renommer",
                    f'Le nom "{renamed}" existe également déjà.\n\n'
                    "Introduis un autre nom."
                )
                continue

            return renamed

    def validate_request(self, row, new_name):
        if not (0 <= row < len(self.requests)):
            return

        request = self.requests[row]
        received_name = request["singer"].strip()

        if not new_name:
            # Existing singer: resolve to the LOCAL singer identity.
            if not self._singer_exists(received_name):
                QMessageBox.warning(
                    self,
                    "NOM INCONNU",
                    f'Le nom "{received_name}" n’existe pas encore dans '
                    "la FILE D’ATTENTE.\n\n"
                    "Pour une première demande, utilise « Nouveau nom »."
                )
                return

            singer = self._resolved_singer_name(received_name)

        else:
            # New singer: accept the received name as-is unless it collides.
            singer = received_name

            if self._singer_exists(singer):
                singer = self._rename_collision(singer)
                if singer is None:
                    return

            self.singer_aliases[received_name.casefold()] = singer

        self.requests.pop(row)

        # V34: the request's key belongs ONLY to the queued song.
        # Never touch the currently playing song or its active key change.
        request_key = int(request.get("key", 0))
        song = Song(
            singer,
            request["artist"],
            request["title"],
            request_key,
        )

        self.song_files[id(song)] = ""
        self.remote_songs.add(id(song))
        self.queue.add(song)

        # Keep one persistent singer identity. refresh_queue groups all
        # songs belonging to this singer on that singer's row/menu.
        self.singer_roster[singer.casefold()] = singer

        self.set_status(
            f'● Demande validée : {singer} — {request["title"]}'
        )

        self.refresh_requests()

        if not self.requests:
            self._demand_blink_timer.stop()
            self._demand_blink_on = False

        self.update_demands_indicator()
        self.refresh_queue()


    def confirm_clear_queue(self):
        reply = QMessageBox.warning(
            self,
            "VIDER LA LISTE",
            "Voulez-vous vraiment vider toute la FILE D’ATTENTE ?\n\n"
            "Toutes les chansons et tous les noms de chanteurs seront supprimés.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        if reply != QMessageBox.Yes:
            return

        self.queue.items.clear()
        self.singer_roster.clear()
        self.singer_aliases.clear()
        self.queue.emit_all()
        self.refresh_queue()
        self.set_status("● FILE D’ATTENTE vidée")


    # ------------------------------------------------------------------
    # COMPTE KJ — catalogue consulté en ligne, favoris/réglages locaux
    # ------------------------------------------------------------------
    def ensure_central_login(self) -> bool:
        """True si une session centrale valide existe ; sinon dialogue."""
        if getattr(self, "_central_session_ok", False):
            return True

        client = self.central_auth
        notice = ""
        if client.is_authenticated:
            QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
            try:
                client.verify_stored_token()
                valid = True
            except Exception:
                valid = False
            finally:
                QApplication.restoreOverrideCursor()
            if valid:
                self._central_session_ok = True
                self.update_account_ui()
                return True
            client.clear()
            notice = "Session expirée — reconnectez-vous.\n"
        else:
            notice = (
                "Consultation du catalogue : connexion obligatoire.\n"
                "Favoris et réglages restent utilisables sans connexion."
            )

        dialog = AuthDialog(self, mode=AuthDialog.MODE_LOGIN, notice=notice)
        accepted = dialog.exec() == QDialog.Accepted and dialog.result_ok
        if not accepted:
            return False

        client.save_session(
            dialog.result_token, dialog.result_email, dialog.result_card
        )
        self._central_session_ok = True
        self.update_account_ui()
        return True

    def update_account_ui(self):
        info = self.central_auth.cached_account()
        connected = bool(getattr(self, "_central_session_ok", False)
                         and info.get("logged_in"))
        if hasattr(self, "account_btn"):
            if connected:
                suffix = f" — {info.get('card')}" if info.get("card") else ""
                self._account_btn_label = f"Connecté : {info.get('email')}{suffix}"
                self._start_account_blink()
            else:
                self._stop_account_blink()
                self.account_btn.setText("👤 COMPTE : non connecté")
                self.account_btn.setStyleSheet("")
        self.setWindowTitle(
            f"KaronlineBox — compte {info.get('email')}" if connected
            else "KaronlineBox"
        )

    def _start_account_blink(self):
        """Point vert clignotant a cote du texte 'Connecte' sur le bouton compte."""
        if not hasattr(self, "_account_blink_timer"):
            self._account_blink_timer = QTimer(self)
            self._account_blink_timer.timeout.connect(self._toggle_account_blink)
        self._account_blink_on = True
        self.account_btn.setStyleSheet(
            "background:#1c7c3f; color:#f1f4f7; font-weight:600;"
            "border:1px solid #2fa257; border-radius:4px; padding:4px 10px;"
        )
        self._toggle_account_blink()
        self._account_blink_timer.start(700)

    def _stop_account_blink(self):
        if hasattr(self, "_account_blink_timer"):
            self._account_blink_timer.stop()

    def _toggle_account_blink(self):
        dot = "🟢" if self._account_blink_on else "⚪"
        self._account_blink_on = not self._account_blink_on
        self.account_btn.setText(f"{dot} {getattr(self, '_account_btn_label', 'Connecté')}")

    def open_account_dialog(self):
        """Bouton COMPTE : connexion/enregistrement ou infos + déconnexion."""
        if not self.ensure_central_login():
            return
        info = self.central_auth.cached_account()
        box = QMessageBox(self)
        box.setWindowTitle("Compte KaronlineLive")
        box.setTextFormat(Qt.RichText)
        box.setText(
            f"<b>{info.get('email')}</b><br>"
            f"Carte liée : {info.get('card') or 'aucune'}<br><br>"
            "Phase tests amis/famille : aucune facturation.<br>"
            "Catalogue consultable uniquement connecté ; favoris et "
            "réglages restent mémorisés en local."
        )
        logout_btn = box.addButton(
            "Se déconnecter", QMessageBox.DestructiveRole
        )
        box.addButton("Fermer", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is logout_btn:
            QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
            try:
                self.central_auth.logout()
            finally:
                QApplication.restoreOverrideCursor()
            self._central_session_ok = False
            self.update_account_ui()
            self.set_status("● Déconnecté du compte KaronlineLive", False)

    def load_demo(self):
        # No fictitious singers, artists or titles at startup.
        # Karonline starts with an empty queue; real MP4s are added by
        # "AJOUTER UNE VIDÉO".
        self.queue.items.clear()
        self.queue.current = None
        self.queue.emit_all()
        self.requests.clear()
        self._demand_blink_on = False
        if hasattr(self, "queue_area_stack"):
            self.queue_area_stack.setCurrentIndex(0)
        self.update_demands_indicator()
        self.set_status("● Prêt — file d'attente vide")

    def scan_video_library(self):
        # Consultation du catalogue : obligatoirement connecté au compte.
        # Hors connexion, seuls les éléments locaux restent accessibles
        # (favoris, réglages, file d'attente déjà chargée).
        if not self.ensure_central_login():
            self.video_list.clear()
            self.set_status(
                "● Catalogue indisponible sans connexion au compte", False
            )
            return
        media_dir = default_media_dir()
        self.video_list.clear()

        local_files = []
        if media_dir.exists():
            local_files = sorted(
                [p for p in media_dir.iterdir()
                 if p.is_file() and p.suffix.lower() == ".mp4"],
                key=lambda p: p.name.lower()
            )

        for video_file in local_files:
            size_mb = video_file.stat().st_size / (1024 * 1024)
            item = QListWidgetItem(
                f"{video_file.name}    [{size_mb:.1f} Mo]"
            )
            item.setData(Qt.UserRole, str(video_file))
            self.video_list.addItem(item)

        # Phase de test amis/famille : ce poste peut ne pas avoir sa propre
        # bibliotheque MP4 -> on propose aussi les titres de la bibliotheque
        # partagee du PC fixe (telecharges a la demande au moment de jouer).
        local_names = {p.name.casefold() for p in local_files}
        remote_songs = self._fetch_central_catalogue()
        remote_added = 0
        for song in remote_songs:
            filename = str(song.get("filename", "")).strip()
            if not filename or filename.casefold() in local_names:
                continue
            artist = song.get("artist", "")
            title = song.get("title", "")
            item = QListWidgetItem(f"☁ {artist} - {title}    [distant]")
            item.setData(Qt.UserRole, f"remote::{filename}")
            self.video_list.addItem(item)
            remote_added += 1

        total = len(local_files) + remote_added
        self.set_status(
            f"● {len(local_files)} vidéo(s) locale(s)"
            f"{f' + {remote_added} distante(s)' if remote_added else ''}"
            if total else "● aucune vidéo MP4",
            bool(total)
        )

    def _fetch_central_catalogue(self):
        """Bibliotheque partagee du PC fixe (annuaire central), utilisee en
        phase de test amis/famille quand ce poste n'a pas ses propres MP4."""
        try:
            request = urllib.request.Request(
                "https://api.karonlinelive.com/catalogue",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            return []

    def _resolve_remote_filename(self, artist, title):
        """Cherche dans la bibliotheque partagee du PC fixe le fichier
        correspondant a un artiste/titre (comparaison insensible a la casse)."""
        target_artist = str(artist or "").strip().casefold()
        target_title = str(title or "").strip().casefold()
        for entry in self._fetch_central_catalogue():
            if (str(entry.get("artist", "")).strip().casefold() == target_artist
                    and str(entry.get("title", "")).strip().casefold() == target_title):
                return str(entry.get("filename", "")).strip()
        return ""

    def _download_from_central_library(self, filename):
        """Telecharge un MP4 de la bibliotheque partagee vers le dossier
        media local ; renvoie le chemin local ou None en cas d'echec."""
        destination = self.media_dir / filename
        if destination.is_file():
            return str(destination)
        try:
            self.media_dir.mkdir(parents=True, exist_ok=True)
            request = urllib.request.Request(
                "https://api.karonlinelive.com/request",
                data=json.dumps({"title": filename}).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) KaronlineBox/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=120) as response:
                tmp_path = destination.with_suffix(".part")
                with tmp_path.open("wb") as out:
                    while chunk := response.read(1024 * 1024):
                        out.write(chunk)
                tmp_path.replace(destination)
            return str(destination)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"CENTRAL DOWNLOAD ERROR = {exc}", flush=True)
            return None

    def play_selected_video(self, item):
        filename = item.data(Qt.UserRole)
        if not filename:
            return

        if str(filename).startswith("remote::"):
            remote_name = filename[len("remote::"):]
            self.set_status(f"● Téléchargement de « {remote_name} »...", True)
            QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
            try:
                filename = self._download_from_central_library(remote_name)
            finally:
                QApplication.restoreOverrideCursor()
            if not filename:
                self.set_status(
                    f"● Échec du téléchargement : « {remote_name} »", False
                )
                return

        # A direct library double-click means "play now". It becomes the
        # current song, while the existing queue remains intact.
        song = Song(
            "—",
            Path(filename).stem,
            Path(filename).stem,
            0,
        )
        self.song_files[id(song)] = filename
        self.queue.current = song
        self.queue.emit_all()
        self.play_song_object(song)

    def _selected_queue_index(self):
        row = self.queue_list.currentRow()
        if row < 0:
            return -1
        item = self.queue_list.item(row, 1)
        if item is None:
            return -1
        value = item.data(Qt.UserRole)
        try:
            return int(value)
        except (TypeError, ValueError):
            return -1

    def _select_queue_index(self, queue_index):
        for row in range(self.queue_list.rowCount()):
            item = self.queue_list.item(row, 1)
            if item is not None and item.data(Qt.UserRole) == queue_index:
                self.queue_list.setCurrentCell(row, 1)
                return

    def remove_selected_queue_item(self):
        """SUPPRIMER : confirmation obligatoire avant suppression."""
        row = self.queue_list.currentRow()
        if row < 0:
            self.set_status("● Sélectionne un chanteur en attente", False)
            return

        name_item = self.queue_list.item(row, 3)
        singer = name_item.text().strip() if name_item else ""
        if not singer:
            return

        reply = QMessageBox.warning(
            self,
            "SUPPRIMER",
            f'Supprimer "{singer}" de la FILE D’ATTENTE ?\n\n'
            "Toutes ses chansons en attente seront supprimées.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        key = singer.casefold()

        self.queue.items[:] = [
            song for song in self.queue.items
            if (song.singer or "").strip().casefold() != key
        ]

        self.singer_roster.pop(key, None)

        for alias, resolved in list(self.singer_aliases.items()):
            if str(resolved).strip().casefold() == key:
                self.singer_aliases.pop(alias, None)

        self.queue.emit_all()
        self.refresh_queue()
        self.set_status(f"● {singer} supprimé de la FILE D’ATTENTE")


    def move_selected_queue_item(self, delta):
        index = self._selected_queue_index()
        if index < 0:
            self.set_status(
                "● Sélectionne une chanson en attente", False
            )
            return

        new_index = index + delta
        if not (0 <= new_index < len(self.queue.items)):
            return

        self.queue.move(index, delta)
        self.refresh_queue()
        self._select_queue_index(new_index)


    def play_selected_queue_item(self):
        index = self._selected_queue_index()
        if index < 0:
            self.set_status(
                "● Sélectionne une chanson en attente", False
            )
            return

        if self.break_active:
            self._finish_break_and_play_selected(index)
            return

        if (
            self.gst_player
            and self.gst_player.pipeline
            and self.queue.current
        ):
            self.confirm_stop_video(
                on_confirm=lambda: self._play_selected_queue_item(index)
            )
            return

        self._play_selected_queue_item(index)


    def _finish_break_and_play_selected(self, row):
        # LIRE MAINTENANT during a manual break: fixed 3 s fade-out, then
        # immediately play the selected next song.
        self.break_auto_timer.stop()
        self.break_active = False
        self.break_auto_pending = False
        if self.gst_player:
            self._fade_break_then(lambda: self._play_selected_queue_item(row))

    def _fade_break_to_silence(self, callback=None):
        if not self.gst_player or not self.gst_player.audio_pipeline:
            if callback:
                callback()
            return
        self._break_fade_callback = callback
        self._break_fade_elapsed = 0
        self.break_fade_start_volume = self._get_music_volume_ratio()
        self.break_fade_target_volume = 0.0
        self.break_fade_timer.start()

    def _fade_break_then(self, callback):
        self._fade_break_to_silence(callback=callback)


    def _break_fade_tick(self):
        if not self.gst_player or not self.gst_player.audio_pipeline:
            self.break_fade_timer.stop()
            callback = getattr(self, "_break_fade_callback", None)
            self._break_fade_callback = None
            if callback:
                callback()
            return

        self._break_fade_elapsed = getattr(self, "_break_fade_elapsed", 0) + 100
        ratio = min(1.0, self._break_fade_elapsed / 3000.0)
        value = (
            self.break_fade_start_volume
            + (self.break_fade_target_volume - self.break_fade_start_volume) * ratio
        )
        self.gst_player._set_break_volume_level(value)

        if ratio >= 1.0:
            self.break_fade_timer.stop()
            # Keep Break Music pipeline alive at 0 % during karaoke.
            self.gst_player._set_break_volume_level(0.0)
            callback = getattr(self, "_break_fade_callback", None)
            self._break_fade_callback = None
            if callback:
                callback()

    def _play_selected_queue_item(self, row):
        song = self.queue.play_now(row)
        if song is not None:
            self.play_song_object(song)

    def play_song_object(self, song):
        filename = self.song_files.get(id(song))
        if not filename and id(song) in self.remote_songs:
            # Une demande venue du mobile n'a pas de fichier local : on va le
            # chercher dans la bibliotheque partagee du PC fixe via le relais
            # central (HTTPS, fonctionne sur n'importe quel reseau, y compris
            # 4G — contrairement a l'ancien fallback LAN qui necessitait le
            # meme reseau local que le PC fixe).
            remote_filename = self._resolve_remote_filename(song.artist, song.title)
            if remote_filename:
                self.set_status(f"● Téléchargement de « {song.title} »...", True)
                QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
                try:
                    filename = self._download_from_central_library(remote_filename)
                finally:
                    QApplication.restoreOverrideCursor()
                if filename:
                    self.song_files[id(song)] = filename
            if not filename:
                self.play_btn.setText("▶")
                self.set_status(
                    f"● Média distant indisponible pour « {song.title} »", False
                )
                return
        if not filename:
            self.play_btn.setText("▶")
            self.set_status(
                f"● {song.title} est en cours, mais aucun fichier vidéo n'est associé",
                False,
            )
            return

        if not self.gst_player:
            return

        self.break_auto_timer.stop()
        self.break_fade_timer.stop()
        self.break_active = False
        self.break_auto_pending = False
        self.next_warning_timer.stop()
        self.next_warning_timer.stop()
        self._hide_public_warning()
        self._karaoke_faded = False
        self._eos_handled = False
        self._restore_kj_video_surface()

        try:
            # A new karaoke owns the audible audio channel immediately.
            # Any pending Break fade callback must not be allowed to revive it.
            self._break_fade_callback = None
            self.break_fade_timer.stop()

            if self.break_playlist_running and self.gst_player.audio_pipeline:
                if self.break_active:
                    self._fade_break_to_silence(
                        callback=self._suspend_break_audio_for_karaoke
                    )
                else:
                    self._suspend_break_audio_for_karaoke()

            self.audio_owner = "karaoke"
            self.gst_player.load(filename)
            self.gst_player.set_volume(100)
            self.gst_player.set_pitch(song.key)
            self.current_key_value = song.key
            self.highlight_key(song.key)
            self._show_public_video()
            self._schedule_next_warning()
            self.gst_player.play()
            self.play_btn.setText("⏸")
            self.set_status(
                f"● Lecture : {Path(filename).name} — GStreamer"
            )
        except Exception as exc:
            self.set_status(f"● GStreamer : {exc}", False)

    def play_local_file(self, filename):
        path = Path(filename).resolve()
        if not path.is_file() or path.suffix.lower() != ".mp4":
            self.set_status(f"● Fichier MP4 introuvable : {path}", False)
            return
        song = Song("", "", path.stem, 0)
        self.song_files[id(song)] = str(path)
        self.queue.current = song
        self.play_song_object(song)

    def confirm_stop_video(self, on_confirm=None):
        # QPushButton.clicked passes a bool. Only LIRE MAINTENANT passes
        # a real continuation callback.
        if not callable(on_confirm):
            on_confirm = None
        # QPushButton.clicked sends a boolean argument. When this method is
        # connected directly to the STOP button, that bool must NOT be
        # treated as a callback. Only a real callable is a continuation
        # (used by LIRE MAINTENANT).
        if not callable(on_confirm):
            on_confirm = None

        if not self.gst_player or not self.gst_player.pipeline:
            if on_confirm is not None:
                on_confirm()
            return

        # The warning preference is shared by STOP and LIRE MAINTENANT.
        show_warning = self.settings.value(
            "stop_video/show_warning",
            True,
            type=bool,
        )

        def perform_confirmed_action():
            self.break_auto_timer.stop()
            self.break_fade_timer.stop()
            self.break_active = False
            self.break_auto_pending = False
            self.audio_owner = "none"
            self._break_fade_callback = None
            self.break_fade_timer.stop()
            self.gst_player.stop()
            self._show_public_background()
            self.play_btn.setText("▶")
            self.progress.setRange(0, 0)
            self.set_video_time_labels(0, 0)

            if (getattr(self, "duo_manager", None)
                    and self.duo_manager.active_code
                    and self.duo_manager.is_host):
                self.duo_manager.send_playback_stopped()

            if on_confirm is not None:
                on_confirm()
            else:
                self.set_status("● Vidéo arrêtée")

            # STOP by the KJ is also a valid transition to BREAK MUSIC.
            # Use the existing Break Music workflow: playlist selection,
            # fade-in and master MUSIC volume are preserved.
            if self._break_music_effective_on() and not self.break_active:
                self._start_break_music(auto=False)

        if not show_warning:
            perform_confirmed_action()
            return

        box = QMessageBox(self)
        box.setWindowTitle("Arrêter le clip")
        box.setText("Êtes-vous sûr de vouloir arrêter le clip ?")
        box.setIcon(QMessageBox.Question)

        yes_button = box.addButton(
            "Oui",
            QMessageBox.YesRole,
        )
        box.addButton(
            "Non",
            QMessageBox.NoRole,
        )

        checkbox = QCheckBox("Afficher un avertissement pour cette action")
        checkbox.setChecked(True)
        box.setCheckBox(checkbox)

        box.exec()

        if box.clickedButton() is yes_button:
            if not checkbox.isChecked():
                self.settings.setValue(
                    "stop_video/show_warning",
                    False,
                )
                self.settings.sync()

            perform_confirmed_action()

    def toggle_play(self):
        if not self.gst_player:
            return

        if self.play_btn.text() == "⏸":
            self.gst_player.pause()
            self.play_btn.setText("▶")
        else:
            self.gst_player.play()
            self.play_btn.setText("⏸")

    def replay(self):
        if self.gst_player:
            self.gst_player.seek_ms(0)
            self.gst_player.play()
            self.play_btn.setText("⏸")

    def refresh_current(self, song):
        if not song:
            self.current_song.setText("Aucun titre")
            self.current_artist.setText("—")
            self.current_singer.setText("")
            self.current_key.setText("")
            return
        self.current_song.setText(song.title)
        self.current_artist.setText(song.artist)
        self.current_singer.setText(
            f"Chanteur : <span style='color:#00a7ff'>{song.singer}</span>"
        )
        self.current_key.setText(
            f"Tonalité : <span style='color:#00a7ff'>{song.key:+d}</span>"
        )
        self.highlight_key(song.key)

    def refresh_next(self, song):
        if not song:
            self.next_singer.setText("—")
            self.next_song.setText("Aucun titre en attente")
            return

        self.next_singer.setText(song.singer)
        self.next_song.setText(song.title)

    def refresh_queue(self):
        selected = self.queue_list.currentRow()
        songs = list(self.queue.items)

        # One display row per singer. Songs remain in QueueManager and are
        # exposed through the ⋮ menu on the singer's single row.
        grouped = {}
        order = []

        for queue_index, song in enumerate(songs):
            singer = (song.singer or "").strip()
            key = singer.casefold() if singer else f"__row_{queue_index}"

            if key not in grouped:
                grouped[key] = {
                    "singer": singer,
                    "songs": [],
                }
                order.append(key)

            grouped[key]["songs"].append((queue_index, song))

        # Persistent singers with no pending songs remain visible, diode OFF.
        for key, singer_name in self.singer_roster.items():
            if key not in grouped:
                grouped[key] = {
                    "singer": singer_name,
                    "songs": [],
                }
                order.append(key)

        self.queue_list.setRowCount(0)
        self.queue_list.setRowCount(len(order))

        for row, key in enumerate(order):
            group = grouped[key]
            singer = group["singer"]
            singer_songs = group["songs"]
            eligible = bool(singer_songs)

            self.queue_list.setItem(row, 0, QTableWidgetItem(""))

            # Store the first real QueueManager index for the row.
            # Controls can use the first song of the singer.
            number = QTableWidgetItem(
                f"{singer_songs[0][0] + 1:02d}" if singer_songs else "—"
            )
            number.setData(
                Qt.UserRole,
                singer_songs[0][0] if singer_songs else -1
            )
            number.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
            self.queue_list.setItem(row, 1, number)

            diode_cell = QWidget()
            diode_layout = QHBoxLayout(diode_cell)
            diode_layout.setContentsMargins(0, 0, 0, 0)
            diode_layout.setAlignment(Qt.AlignCenter)

            if eligible:
                diode = QLabel()
                diode.setFixedSize(14, 14)
                diode.setToolTip(
                    f"{singer} est éligible à chanter"
                )
                diode.setStyleSheet(
                    "background:#20e83b;"
                    "border:1px solid #73ff83;"
                    "border-radius:7px;"
                )
                diode_layout.addWidget(diode)

            self.queue_list.setCellWidget(row, 2, diode_cell)

            name_item = QTableWidgetItem(singer)
            name_item.setData(Qt.UserRole, {
                "singer": singer,
                "songs": singer_songs,
            })
            if not eligible:
                name_item.setForeground(Qt.GlobalColor.gray)
            self.queue_list.setItem(row, 3, name_item)

            if eligible:
                first_song = singer_songs[0][1]
                self.queue_list.setItem(
                    row, 4, QTableWidgetItem(first_song.artist or "")
                )
                self.queue_list.setItem(
                    row, 5, QTableWidgetItem(
                        first_song.title
                        + (
                            f"  (+{len(singer_songs)-1})"
                            if len(singer_songs) > 1 else ""
                        )
                    )
                )
                key_widget = QWidget()
                key_layout = QHBoxLayout(key_widget)
                key_layout.setContentsMargins(2, 0, 2, 0)
                key_layout.setSpacing(2)

                key_minus = QPushButton("−")
                key_value = QLabel(f"{first_song.key:+d}")
                key_plus = QPushButton("+")

                key_value.setAlignment(Qt.AlignCenter)
                key_value.setMinimumWidth(28)

                key_button_style = (
                    "QPushButton{"
                    "background:#08131c;"
                    "color:#00ffff;"
                    "border:1px solid #1b6f91;"
                    "border-radius:3px;"
                    "font-size:14px;"
                    "font-weight:700;"
                    "padding:0px;"
                    "min-width:22px;"
                    "max-width:22px;"
                    "min-height:22px;"
                    "max-height:22px;"
                    "}"
                    "QPushButton:hover{"
                    "background:#123544;"
                    "}"
                )

                key_minus.setStyleSheet(key_button_style)
                key_plus.setStyleSheet(key_button_style)

                key_value.setStyleSheet(
                    "QLabel{"
                    "color:#00ffff;"
                    "font-size:15px;"
                    "font-weight:700;"
                    "padding:0px 3px;"
                    "}"
                )

                def make_key_callback(target_song, value_label, delta):
                    def callback(checked=False):
                        new_value = max(
                            -6,
                            min(6, int(target_song.key) + delta)
                        )
                        target_song.key = new_value
                        value_label.setText(f"{new_value:+d}")
                    return callback

                key_minus.clicked.connect(
                    make_key_callback(first_song, key_value, -1)
                )
                key_plus.clicked.connect(
                    make_key_callback(first_song, key_value, +1)
                )

                key_layout.addWidget(key_minus)
                key_layout.addWidget(key_value)
                key_layout.addWidget(key_plus)

                self.queue_list.setCellWidget(row, 6, key_widget)
            else:
                self.queue_list.setItem(row, 4, QTableWidgetItem(""))
                empty = QTableWidgetItem("AUCUNE CHANSON EN ATTENTE")
                empty.setForeground(Qt.GlobalColor.gray)
                self.queue_list.setItem(row, 5, empty)
                self.queue_list.setItem(row, 6, QTableWidgetItem(""))

            menu_btn = QPushButton("⋮")
            menu_btn.setFixedWidth(24)
            menu_btn.setFlat(True)
            menu_btn.setStyleSheet(
                "QPushButton{color:#f1f4f7;background:transparent;"
                "border:none;font-size:16px;font-weight:700;padding:0;}"
                "QPushButton:hover{color:#00a7ff;}"
            )
            menu_btn.setToolTip(
                "Voir toutes les chansons en attente de ce chanteur"
            )
            menu_btn.clicked.connect(
                lambda checked=False, singer_name=singer:
                self.show_singer_queue_menu(singer_name)
            )
            self.queue_list.setCellWidget(row, 7, menu_btn)

            self.queue_list.setRowHeight(row, 22)

        # V29/V31/V32: intentionally no count beside FILE D'ATTENTE.
        self.queue_count_label.setText("")

        if 0 <= selected < self.queue_list.rowCount():
            self.queue_list.setCurrentCell(selected, 1)

        # In KJ Auto mode: if break music is playing continuously because the queue was empty,
        # automatically transition to the newly queued song now that a song is available.
        if (self.kj_auto_on.isChecked()
                and self.break_active
                and not self.break_auto_timer.isActive()
                and self.queue.items
                and self.audio_owner != "karaoke"):
            self._end_auto_break()


    def show_singer_queue_menu(self, singer):
        singer = (singer or "").strip()
        if not singer:
            return

        songs = [
            (index, song)
            for index, song in enumerate(self.queue.items)
            if (song.singer or "").strip().casefold() == singer.casefold()
        ]
        if not songs:
            return

        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#0b1117;color:#f1f4f7;"
            "border:1px solid #263746;padding:5px;}"
            "QMenu::item{padding:8px 18px;}"
            "QMenu::item:selected{background:#102a3a;}"
        )

        heading = QAction(
            f"{singer} — {len(songs)} titre"
            + ("" if len(songs) == 1 else "s"),
            menu,
        )
        heading.setEnabled(False)
        menu.addAction(heading)
        menu.addSeparator()

        # Each title can be selected and immediately moved one position
        # within THIS singer's own pending-song order.
        for pos, (index, song) in enumerate(songs):
            label = QAction(
                f"{index + 1:02d}  {song.artist or ''} — {song.title or ''}",
                menu,
            )
            label.triggered.connect(
                lambda checked=False, r=index:
                self._select_queue_index(r)
            )
            menu.addAction(label)

            if len(songs) > 1:
                up = QAction("    ↑ MONTER", menu)
                up.setEnabled(pos > 0)
                up.triggered.connect(
                    lambda checked=False, r=index:
                    self._move_singer_song(r, -1)
                )
                menu.addAction(up)

                down = QAction("    ↓ DESCENDRE", menu)
                down.setEnabled(pos < len(songs) - 1)
                down.triggered.connect(
                    lambda checked=False, r=index:
                    self._move_singer_song(r, +1)
                )
                menu.addAction(down)

        button = self.sender()
        if isinstance(button, QPushButton):
            menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        else:
            menu.exec(QCursor.pos())

    def _move_singer_song(self, queue_index, direction):
        """Réordonne une chanson uniquement parmi celles du même chanteur."""
        if not (0 <= queue_index < len(self.queue.items)):
            return

        song = self.queue.items[queue_index]
        singer = (song.singer or "").strip().casefold()

        singer_positions = [
            i for i, item in enumerate(self.queue.items)
            if (item.singer or "").strip().casefold() == singer
        ]
        if queue_index not in singer_positions:
            return

        pos = singer_positions.index(queue_index)
        target_pos = pos + direction
        if target_pos < 0 or target_pos >= len(singer_positions):
            return

        target_index = singer_positions[target_pos]
        self.queue.items[queue_index], self.queue.items[target_index] = (
            self.queue.items[target_index],
            self.queue.items[queue_index],
        )

        self.queue.emit_all()
        self.refresh_queue()

        # Keep the moved title selected in the singer's menu row.
        self._select_queue_index(target_index)


    def highlight_key(self, value):
        value = max(-6, min(6, int(value)))
        self.current_key_value = value

        for k, b in self.key_buttons.items():
            b.setStyleSheet(
                "background:#0077d9;border:1px solid #00a7ff;"
                "font-weight:700;"
                if k == value else ""
            )

        # V35: persist the active key on the CURRENT Song object.
        # Queue/request updates emit current_changed(), which refreshes the
        # current display. Without this, the Song retained key=0 and the
        # key changer appeared to reset after accepting a DEMANDE.
        if self.queue.current:
            self.queue.current.key = value

        # Apply the transposition immediately when a clip is loaded.
        if self.gst_player:
            self.gst_player.set_pitch(value)

        if self.queue.current:
            self.current_key.setText(
                f"Tonalité : <span style='color:#00a7ff'>{value:+d}</span>"
            )

    def on_gst_eos(self):
        # Ignore EOS from a video that is no longer the current owner.
        if self.audio_owner != "karaoke":
            return
        self.next_warning_timer.stop()
        # True end of karaoke: destroy the video pipeline so the KJ
        # preview cannot remain frozen on the last frame.
        self.gst_player._stop_video_pipeline()
        self._show_kj_background_at_eos()
        # The karaoke video is finished: remove it from the public screen
        # immediately, before showing the public background or Break Music.
        self._show_public_background()

        self.audio_owner = "none"

        # Automatic mode: Break Music is mandatory and runs for the configured
        # duration before the next queued song.
        if self.kj_auto_on.isChecked() and self._start_break_music(auto=True):
            return

        # Manual mode + BREAK MUSIC ON: playlist runs until LIRE MAINTENANT.
        if (not self.kj_auto_on.isChecked()
                and self.break_manual_on.isChecked()
                and self._start_break_music(auto=False)):
            return

        # No Break Music: immediately advance.
        if self.queue.current is not None:
            next_song = self.queue.advance()
            if next_song is not None:
                self.play_song_object(next_song)
                return

        self._show_public_background()
        self.play_btn.setText("▶")
        self.set_status("● FIN DE VIDÉO")

        if self.public_warning_label and self.public_warning_label.isVisible():
            self.public_warning_label.raise_()

    def _ensure_public_window(self):
        if self.public_window is not None:
            return

        self.public_window = QMainWindow()
        self.public_window.setWindowTitle(
            "KaronlineBox — Écran public"
        )
        self.public_window.setAttribute(Qt.WA_DeleteOnClose, False)

        self.public_container = QWidget()
        self.public_container.setStyleSheet("background:#000;")

        self.public_bg_label = QLabel(self.public_container)
        self.public_bg_label.setAlignment(Qt.AlignCenter)
        self.public_bg_label.setStyleSheet("background:#000;")
        self.public_bg_label.setGeometry(self.public_container.rect())
        self.public_bg_label.hide()

        self.public_video = QWidget(self.public_container)
        self.public_video.setAttribute(Qt.WA_NativeWindow, True)
        self.public_video.setStyleSheet("background:#000;")
        self.public_video.setGeometry(self.public_container.rect())
        self.public_video.hide()

        self.public_warning_label = QLabel(self.public_container)
        self.public_warning_label.setAlignment(Qt.AlignCenter)
        self.public_warning_label.setStyleSheet(
            "color:#ff8c00;background:rgba(0,0,0,220);"
            "font-size:18px;font-weight:700;padding:6px 12px;"
            "border:0px;border-radius:0px;"
        )
        self.public_warning_label.setGeometry(
            0, 0, max(400, self.public_container.width()), 48
        )
        self.public_warning_label.hide()

        self.public_duo_webcam_label = QLabel(self.public_container)
        self.public_duo_webcam_label.setAlignment(Qt.AlignCenter)
        self.public_duo_webcam_label.setStyleSheet("background:#000;")
        self.public_duo_webcam_label.setGeometry(self.public_container.rect())
        self.public_duo_webcam_label.hide()

        self.public_window.setCentralWidget(self.public_container)
        self.public_window.closeEvent = self._public_close_event

        # The window stays hidden. Only the render target is prepared.
        self.public_window.hide()

        if self.gst_player:
            self.gst_player.set_public_widget(self.public_video)

    def _resize_public_layers(self):
        if not self.public_container:
            return
        rect = self.public_container.rect()
        if self.public_bg_label:
            self.public_bg_label.setGeometry(rect)
        if self.public_video:
            self.public_video.setGeometry(rect)
        if self.public_duo_webcam_label:
            self.public_duo_webcam_label.setGeometry(rect)
        if self.public_warning_label:
            self.public_warning_label.setGeometry(
                0, 0, rect.width(), 48
            )

    def open_public_window(self):
        self._ensure_public_window()
        self._public_screen_mode = "video"

        screens = QGuiApplication.screens()
        if len(screens) >= 2:
            # Fenetre normale (bordee, deplacable), positionnee sur l'ecran
            # externe : jamais de plein ecran automatique ici, sinon
            # impossible de la glisser/repositionner soi-meme.
            self.public_window.setGeometry(screens[1].availableGeometry())
            self.public_window.show()
        else:
            self.public_window.resize(960, 540)
            self.public_window.show()

        # Re-attach the same stable HWND after reopening.
        if self.gst_player and self.public_video:
            self.gst_player.set_public_widget(self.public_video)

        self._resize_public_layers()
        self._load_public_backgrounds()
        if self.gst_player and self.gst_player.pipeline:
            self._show_public_video()
        else:
            self._show_public_background()
        self.public_window.raise_()
        self.public_window.activateWindow()
        self.set_status("● Écran public ouvert")

    def open_public_duo_webcam_window(self):
        """Affiche la webcam de l'invité DUO en plein écran externe (option
        mutuellement exclusive avec la vidéo karaoké sur le même écran)."""
        if not (self.duo_manager.is_host and self.duo_manager.active_code and self._duo_guest_connected_flag):
            QMessageBox.information(
                self,
                "Webcam invité indisponible",
                "Cette option n'est disponible qu'en session DUO active, avec un "
                "invité connecté diffusant sa webcam.",
            )
            return

        self._ensure_public_window()
        self._public_screen_mode = "duo_webcam"

        screens = QGuiApplication.screens()
        if len(screens) >= 2:
            self.public_window.setGeometry(screens[1].availableGeometry())
            self.public_window.show()
        else:
            self.public_window.resize(960, 540)
            self.public_window.show()

        self._resize_public_layers()
        self._show_public_duo_webcam()
        self.public_window.raise_()
        self.public_window.activateWindow()
        self.set_status("● Écran public : webcam invité DUO")

    def _render_duo_frame_on_label(self, label: QLabel, frame_data):
        """Affiche une frame DUO (JPEG base64) à la meilleure résolution
        possible dans le label donné, sans la dégrader inutilement."""
        if not frame_data:
            return
        try:
            if isinstance(frame_data, str) and frame_data.startswith("data:image"):
                raw_bytes = base64.b64decode(frame_data.split(",", 1)[-1])
            elif isinstance(frame_data, bytes):
                raw_bytes = frame_data
            else:
                return
            pixmap = QPixmap()
            if not pixmap.loadFromData(raw_bytes):
                return
            target_size = label.size()
            if target_size.width() > 10 and target_size.height() > 10:
                pixmap = pixmap.scaled(
                    target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
            label.setPixmap(pixmap)
        except Exception:
            pass


    def closeEvent(self, event):
        if self.gst_player and self.gst_player.pipeline:
            reply = QMessageBox.warning(
                self,
                "FERMER KARONLINE",
                "Une vidéo karaoké est actuellement en cours de lecture.\n\n"
                "Voulez-vous vraiment fermer le logiciel ?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
        self._shutdown_playback_and_quit()
        event.accept()

    def _shutdown_playback_and_quit(self):
        """Stop every GStreamer pipeline/timer and quit, even if the
        external public video window is still open."""
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        self._unregister_relay_session()
        if getattr(self, "mic_monitor", None) is not None:
            self.mic_monitor.stop()

        if getattr(self, "lan_request_receiver", None) is not None:
            self.lan_request_receiver.stop()

        for timer_name in (
            "gst_timer", "break_fade_timer", "break_auto_timer",
            "next_warning_timer", "_demand_blink_timer", "_public_bg_timer",
        ):
            timer = getattr(self, timer_name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

        if self.gst_player:
            try:
                self.gst_player.stop()
            except Exception:
                pass

        if self.public_window is not None:
            try:
                self.public_window.hide()
            except Exception:
                pass

        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _public_close_event(self, event):
        # Closing the public window must NOT affect playback.
        # Keep the window/HWND alive and simply hide it.
        event.ignore()
        if self.public_window:
            self.public_window.hide()
        self.set_status("● Écran public fermé")







