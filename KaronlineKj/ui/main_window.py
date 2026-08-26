from pathlib import Path
import json
import re
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QFont, QCursor, QAction, QGuiApplication, QPixmap
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QVBoxLayout, QFormLayout, QHBoxLayout, QGridLayout, QListWidget, QListWidgetItem,
    QSlider, QFrame, QGroupBox, QStackedWidget,
    QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy, QTabWidget,
    QMessageBox, QCheckBox, QButtonGroup, QFileDialog, QMenu,
    QDialog, QLineEdit, QSpinBox, QDialogButtonBox, QInputDialog
)
from core.gstreamer_player import GStreamerPlayer, GStreamerError
from core.models import Song
from core.queue_manager import QueueManager
from core.favorites_manager import FavoritesManager
from core.media_provider import LanMediaProvider
from core.lan_config import (
    LAN_MEDIA_PORT,
    LAN_RECEIVER_HOST,
    LAN_REQUEST_PORT,
    LAN_TEST_SERVER,
)
from core.lan_request_receiver import LanRequestReceiver

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
        self.resize(1500, 900)
        self.setMinimumSize(1500, 900)

        self.queue = QueueManager()

        self.gst_player = None
        self.public_window = None
        self.public_container = None
        self.public_video = None
        self.public_bg_label = None
        self.public_warning_label = None
        self.settings = QSettings("Karonline", "KaronlineKJ")

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
        self.media_dir = Path(__file__).resolve().parent.parent / "media"
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
        if self.public_video:
            self.public_video.show()
            self.public_video.raise_()

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
        logo_path = Path(__file__).resolve().parent / "box.jpg"
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

        self.demands_btn = QPushButton("DEMANDES")
        self.queue_nav_btn = QPushButton("☷  FILE D'ATTENTE")
        self.favorites_btn = QPushButton("☆  FAVORIS")
        self.settings_btn = QPushButton("⚙  RÉGLAGES")

        for b in [
            self.demands_btn, self.queue_nav_btn,
            self.favorites_btn, self.settings_btn
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

        nav.addStretch()
        self.status = QLabel("● Prêt")
        nav.addWidget(self.status)
        outer.addLayout(nav)

        grid = QGridLayout()
        self.kj_video_grid = grid
        # GRANDE preserves the existing V67 proportions.
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 5)

        left = QVBoxLayout()

        box = QFrame()
        box.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(1)
        lab = QLabel("LIVE")
        lab.setObjectName("section")
        lab.setStyleSheet("font-size:26px;font-weight:700;color:#00a7ff;")
        lab.setMaximumHeight(34)
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
        nb.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        nl = QVBoxLayout(nb)
        nl.setContentsMargins(8, 4, 8, 4)
        nl.setSpacing(1)
        lab = QLabel("SUIVANT")
        lab.setObjectName("section")
        lab.setStyleSheet("font-size:26px;font-weight:700;color:#00a7ff;")
        lab.setMaximumHeight(34)
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
        ql.setContentsMargins(10, 2, 10, 4)
        ql.setSpacing(0)

        queue_header = QHBoxLayout()
        queue_header.setContentsMargins(4, 0, 4, 0)

        queue_title = QLabel("FILE D'ATTENTE")
        queue_title.setObjectName("section")
        queue_title.setStyleSheet("font-size:26px;")
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
                padding:8px 6px;
                font-size:14px;
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

        self.queue_add_btn = QPushButton("＋ AJOUTER UNE VIDÉO")
        self.queue_remove_btn = QPushButton("SUPPRIMER")
        self.add_favorite_button = QPushButton("AJOUTER AUX FAVORIS")
        self.add_favorite_button.clicked.connect(self.add_selected_to_favorites)
        self.queue_up_btn = QPushButton("↑ MONTER")
        self.queue_down_btn = QPushButton("↓ DESCENDRE")
        self.queue_play_btn = QPushButton("▶ LIRE MAINTENANT")

        for _act_btn in (
            self.queue_add_btn, self.queue_remove_btn,
            self.add_favorite_button, self.queue_up_btn,
            self.queue_down_btn, self.queue_play_btn,
        ):
            _act_btn.setStyleSheet("font-size:11px;padding:4px 6px;")

        acts.addWidget(self.queue_add_btn)
        acts.addWidget(self.clear_queue_button)
        acts.addWidget(self.queue_remove_btn)
        acts.addWidget(self.add_favorite_button)
        acts.addWidget(self.queue_up_btn)
        acts.addWidget(self.queue_down_btn)
        acts.addWidget(self.queue_play_btn)

        self.queue_add_btn.clicked.connect(self.add_selected_video_to_queue)
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

        session_info = QLabel(
            "Choisissez un nom simple (ex. soiree-marc) et cliquez sur "
            "DÉMARRER : ce nom sera à partager avec vos invités sur "
            "karonlinelive.com."
        )
        session_info.setWordWrap(True)
        session_layout.addWidget(session_info)

        session_form = QFormLayout()
        self.session_name_input = QLineEdit()
        self.session_name_input.setPlaceholderText("soiree-marc")
        session_form.addRow("NOM DE SESSION", self.session_name_input)
        session_layout.addLayout(session_form)

        session_buttons = QHBoxLayout()
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

        favorites_test_row = QHBoxLayout()
        self.favorites_test_button = QPushButton("🧪 CHARGER FAVORIS TEST")
        self.favorites_test_button.setToolTip(
            "Mode test : remplir MES FAVORIS et NOTRE SOIRÉE"
        )
        self.favorites_test_button.clicked.connect(self.inject_test_favorites)
        self.favorites_test_clear = QPushButton("EFFACER TEST")
        self.favorites_test_clear.clicked.connect(self.clear_test_favorites)
        favorites_test_row.addWidget(self.favorites_test_button)
        favorites_test_row.addWidget(self.favorites_test_clear)
        favorites_test_row.addStretch()
        favorites_box_layout.addLayout(favorites_test_row)

        favorites_layout.addWidget(favorites_box)

        self.solo_favorites_list.itemClicked.connect(self.request_solo_favorite)
        self.group_favorites_list.itemClicked.connect(self.request_group_favorite)
        self.favorites.changed.connect(self.refresh_favorites)

        self.queue_area_stack.addWidget(queue_page)
        self.queue_area_stack.addWidget(demands_page)
        self.queue_area_stack.addWidget(favorites_page)
        self.queue_area_stack.addWidget(self.settings_page)

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
        self.video.setMinimumHeight(320)
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
        self.back_btn = QPushButton("⏪")
        self.play_btn = QPushButton("▶")
        self.stop_btn = QPushButton("■")
        self.replay_btn = QPushButton("↻")
        self.forward_btn = QPushButton("⏩")

        for b in [
            self.back_btn, self.play_btn, self.stop_btn,
            self.replay_btn, self.forward_btn
        ]:
            b.setMinimumHeight(64)
            b.setMinimumWidth(90)
            b.setStyleSheet(
                "font-size:24px;font-weight:700;"
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
            lab.setMinimumHeight(38)
            lab.setMaximumHeight(42)
            lab.setStyleSheet(
                "color:#ff8a00;font-size:22px;font-weight:700;"
                "background:#080d12;border:1px solid #1d2a35;"
                "border-radius:5px;padding:2px 8px;"
            )

        time_row.addWidget(self.elapsed_label, 1)
        time_row.addWidget(self.total_label, 1)
        time_row.addWidget(self.remaining_label, 1)
        vl.addLayout(time_row)

        grid.addWidget(vb, 0, 1)
        self.kj_video_panel = vb
        self._update_kj_video_width()

        bottom = QHBoxLayout()

        kb = QGroupBox("CHANGEUR DE TONALITÉ")
        kl = QHBoxLayout(kb)
        self.key_buttons = {}

        for k in range(-6, 7):
            b = QPushButton(f"{k:+d}")
            b.setMinimumWidth(48)
            self.key_buttons[k] = b
            b.clicked.connect(
                lambda checked=False, x=k: self.highlight_key(x)
            )
            kl.addWidget(b)

        bottom.addWidget(kb, 3)

        vb2 = QGroupBox("VOLUME KARAOKÉ & MUSIQUE")
        v = QVBoxLayout(vb2)
        v.addLayout(self.volume_row("Karaoké"))
        v.addLayout(self.volume_row("Musique"))
        bottom.addWidget(vb2, 2)

        eb = QGroupBox("ÉCRAN PUBLIC")
        el = QVBoxLayout(eb)
        pb = QPushButton(
            "▣  Ouvrir une fenêtre pour la vidéo sur écran externe"
        )
        pb.clicked.connect(self.open_public_window)
        el.addWidget(pb)
        bottom.addWidget(eb, 1)

        outer.addLayout(grid)
        outer.addLayout(bottom)

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


    def inject_test_favorites(self):
        self.favorites.add_solo("Don't Stop Me Now", "Queen")
        self.favorites.add_solo("Dancing Queen", "ABBA")
        self.favorites.add_group("Marc", "Alors on danse", "Stromae")
        self.favorites.add_group("Sophie", "Je veux", "Zaz")
        self.favorites.add_group("Marc", "Allumer le feu", "Johnny Hallyday")
        self.refresh_favorites()
        self.show_main_view("favorites")
        self.set_status("● Favoris de test chargés", True)

    def clear_test_favorites(self):
        self.favorites.clear()
        self.refresh_favorites()
        self.show_main_view("favorites")
        self.set_status("● Favoris locaux effacés", True)

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


    def open_lan_test_dialog(self):
        """Simulateur de client distant pour alimenter DEMANDES."""
        dialog = QDialog(self)
        dialog.setWindowTitle("TESTER LA DEMANDE LAN")
        dialog.resize(520, 320)

        layout = QVBoxLayout(dialog)
        info = QLabel(
            "Envoie une demande distante dans l'onglet DEMANDES. Le MP4 sera téléchargé uniquement à la lecture."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form = QGridLayout()
        server_edit = QLineEdit(LAN_TEST_SERVER)
        port_edit = QLineEdit(str(LAN_MEDIA_PORT))
        client_ip_edit = QLineEdit(self._detect_lan_client_ip(server_edit.text()))
        singer_edit = QLineEdit()
        artist_edit = QLineEdit()
        title_edit = QLineEdit()
        key_spin = QSpinBox()
        key_spin.setRange(-12, 12)
        key_spin.setValue(0)
        key_spin.setSuffix(" demi-ton(s)")

        form.addWidget(QLabel("Serveur IP"), 0, 0)
        form.addWidget(server_edit, 0, 1)
        form.addWidget(QLabel("Port"), 1, 0)
        form.addWidget(port_edit, 1, 1)
        form.addWidget(QLabel("IP KaronlineBox client"), 2, 0)
        form.addWidget(client_ip_edit, 2, 1)
        form.addWidget(QLabel("Chanteur"), 3, 0)
        form.addWidget(singer_edit, 3, 1)
        form.addWidget(QLabel("Artiste"), 4, 0)
        form.addWidget(artist_edit, 4, 1)
        form.addWidget(QLabel("Titre"), 5, 0)
        form.addWidget(title_edit, 5, 1)
        form.addWidget(QLabel("Tonalité"), 6, 0)
        form.addWidget(key_spin, 6, 1)
        layout.addLayout(form)

        send_button = QPushButton("ENVOYER LA DEMANDE")
        close_button = QPushButton("FERMER")
        send_button.clicked.connect(
            lambda: self._send_lan_request(
                server_edit,
                port_edit,
                client_ip_edit,
                singer_edit,
                artist_edit,
                title_edit,
                key_spin,
            )
        )
        close_button.clicked.connect(dialog.close)
        buttons = QHBoxLayout()
        buttons.addWidget(send_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)
        dialog.show()

    def _send_lan_request(
        self, server_edit, port_edit, client_ip_edit, singer_edit, artist_edit, title_edit, key_spin
    ):
        server = server_edit.text().strip() or LAN_TEST_SERVER
        client_ip = client_ip_edit.text().strip()
        singer = singer_edit.text().strip()
        artist = artist_edit.text().strip()
        title = title_edit.text().strip()
        try:
            port = int(port_edit.text().strip() or LAN_MEDIA_PORT)
        except ValueError:
            QMessageBox.warning(self, "PARAMÈTRES INVALIDES", "Le port doit être un nombre valide.")
            return
        if not client_ip or not singer or not artist or not title:
            QMessageBox.warning(self, "DEMANDE INCOMPLÈTE", "Chanteur, artiste et titre sont obligatoires.")
            return

        import urllib.request
        import json

        payload = json.dumps({
            "singer": singer,
            "artist": artist,
            "title": title,
            "key": key_spin.value(),
            "client_ip": client_ip,
        }).encode("utf-8")
        request = urllib.request.Request(
            f"http://{server}:{port}/request-demand",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status != 202:
                    raise RuntimeError(f"HTTP {response.status}")
            self.set_status(f"● Demande LAN envoyée : {title}", True)
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            self.set_status(f"● DEMANDE LAN : {exc}", False)
            QMessageBox.warning(self, "DEMANDE LAN", str(exc), QMessageBox.Ok)

    @staticmethod
    def _detect_lan_client_ip(server):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect((server, LAN_MEDIA_PORT))
            return probe.getsockname()[0]
        except OSError:
            return ""
        finally:
            probe.close()

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
        if view == "demands":
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

            for col, value in enumerate([
                request["singer"],
                request["artist"],
                request["title"],
                f'{request["key"]:+d}',
            ]):
                self.requests_table.setItem(
                    row, col, QTableWidgetItem(str(value))
                )

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

            new_btn.setStyleSheet(
                "QPushButton{border:1px solid #00a7ff;"
                "color:#00a7ff;font-weight:700;}"
            )
            delete_btn.setStyleSheet(
                "QPushButton{border:1px solid #7a3030;"
                "color:#ff6b6b;font-weight:700;}"
                "QPushButton:hover{background:#251719;}"
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

    def start_public_session(self):
        """Demarre lan_server.py + un tunnel Cloudflare et enregistre le nom de session."""
        name = re.sub(
            r"[^a-z0-9-]", "-",
            self.session_name_input.text().strip().lower()
        ).strip("-")

        if not name:
            QMessageBox.warning(
                self, "NOM DE SESSION MANQUANT",
                "Entrez un nom de session (ex. soiree-marc)."
            )
            return

        name_available = self._session_name_available(name)
        if name_available is False:
            QMessageBox.warning(
                self,
                "NOM DE SESSION DÉJÀ UTILISÉ",
                f"Le nom de session « {name} » est déjà actif.\n\n"
                "Choisissez un autre nom pour éviter que les invités rejoignent le mauvais hôte.",
                QMessageBox.Ok,
            )
            self.session_status_label.setText("● Nom de session déjà utilisé")
            return
        self.session_start_btn.setEnabled(False)
        if name_available is None:
            self.session_status_label.setText(
                "● Vérification du nom impossible, tentative d'enregistrement..."
            )
        else:
            self.session_status_label.setText("● Démarrage du serveur LAN...")

        karonline_kj_dir = Path(__file__).resolve().parent.parent

        if not self._is_port_open("127.0.0.1", 8765):
            subprocess.Popen(
                [sys.executable, "lan_server.py", "--port", "8765"],
                cwd=str(karonline_kj_dir),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        cloudflared = Path.home() / "AppData" / "Local" / "cloudflared" / "cloudflared.exe"
        if not cloudflared.is_file():
            self.session_start_btn.setEnabled(True)
            self.session_status_label.setText(
                f"● cloudflared introuvable ({cloudflared})"
            )
            return

        self._tunnel_log = Path(tempfile.gettempdir()) / "kbox_tunnel.log"
        self._tunnel_log.unlink(missing_ok=True)
        subprocess.Popen(
            [str(cloudflared), "tunnel", "--url", "http://localhost:8765"],
            stderr=open(self._tunnel_log, "w", encoding="utf-8"),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        self.session_status_label.setText("● Ouverture du tunnel public...")
        self._session_pending_name = name
        self._session_wait_ticks = 0
        self._session_timer = QTimer(self)
        self._session_timer.timeout.connect(self._poll_tunnel_log)
        self._session_timer.start(1000)

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

    def _poll_tunnel_log(self):
        self._session_wait_ticks += 1
        text = self._tunnel_log.read_text(encoding="utf-8", errors="ignore") if self._tunnel_log.exists() else ""
        match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", text)

        if not match:
            if self._session_wait_ticks >= 30:
                self._session_timer.stop()
                self.session_start_btn.setEnabled(True)
                self.session_status_label.setText(
                    "● Impossible d'ouvrir le tunnel (délai dépassé)."
                )
            return

        self._session_timer.stop()
        host_url = match.group(0)

        try:
            payload = json.dumps({
                "name": self._session_pending_name,
                "host_url": host_url,
            }).encode("utf-8")
            request = urllib.request.Request(
                "https://api.karonlinelive.com/session/register",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
            self.session_status_label.setText(
                f"● Session active : « {self._session_pending_name} » "
                "— partagez ce nom à vos invités sur karonlinelive.com"
            )
            self.set_status(f"● Session « {self._session_pending_name} » démarrée", True)
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                QMessageBox.warning(
                    self,
                    "NOM DE SESSION DÉJÀ UTILISÉ",
                    f"Le nom de session « {self._session_pending_name} » vient d'être pris.\n\n"
                    "Choisissez un autre nom pour éviter que les invités rejoignent le mauvais hôte.",
                    QMessageBox.Ok,
                )
                self.session_status_label.setText("● Nom de session déjà utilisé")
            else:
                self.session_status_label.setText(f"● Erreur d'enregistrement : HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError, OSError, RuntimeError) as exc:
            self.session_status_label.setText(f"● Erreur d'enregistrement : {exc}")
        finally:
            self.session_start_btn.setEnabled(True)

    def _session_name_available(self, name):
        url = f"https://api.karonlinelive.com/session/{name}"
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.status == 404
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return True
            if exc.code == 200:
                return False
            return None
        except (urllib.error.URLError, TimeoutError, OSError):
            return None

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
        media_dir = Path(__file__).resolve().parent.parent / "media"
        self.video_list.clear()

        if not media_dir.exists():
            self.set_status("● dossier media introuvable", False)
            return

        files = sorted(
            [p for p in media_dir.iterdir()
             if p.is_file() and p.suffix.lower() == ".mp4"],
            key=lambda p: p.name.lower()
        )

        for video_file in files:
            size_mb = video_file.stat().st_size / (1024 * 1024)
            item = QListWidgetItem(
                f"{video_file.name}    [{size_mb:.1f} Mo]"
            )
            item.setData(Qt.UserRole, str(video_file))
            self.video_list.addItem(item)

        self.set_status(
            f"● {len(files)} vidéo(s) détectée(s)"
            if files else "● aucune vidéo MP4",
            bool(files)
        )

    def play_selected_video(self, item):
        filename = item.data(Qt.UserRole)
        if not filename:
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

    def add_selected_video_to_queue(self):
        start_dir = str(self.media_dir) if self.media_dir.is_dir() else str(
            Path(__file__).resolve().parent.parent
        )

        filename, _ = QFileDialog.getOpenFileName(
            self,
            "Ajouter une vidéo à la file d'attente",
            start_dir,
            "Vidéos MP4 (*.mp4);;Toutes les vidéos (*.mp4 *.mkv *.avi *.mov);;Tous les fichiers (*.*)",
        )
        if not filename:
            return

        path = Path(filename)
        if path.suffix.lower() != ".mp4":
            self.set_status("● Sélectionne un fichier MP4", False)
            return

        # A raw MP4 has no singer/artist/title metadata in Karonline yet.
        # Keep those fields empty instead of inventing fake metadata.
        song = Song("", "", path.stem, 0)
        self.song_files[id(song)] = str(path)
        new_queue_index = len(self.queue.items)
        self.queue.add(song)
        self.refresh_queue()
        self._select_queue_index(new_queue_index)
        self.set_status(f"● Ajouté à la file : {path.name}")

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
            try:
                provider = LanMediaProvider(
                    server=LAN_TEST_SERVER,
                    port=LAN_MEDIA_PORT,
                )
                filename = str(provider.fetch_mp4(f"{song.artist}-{song.title}"))
                self.song_files[id(song)] = filename
            except (ConnectionError, FileNotFoundError, RuntimeError, OSError) as exc:
                self.play_btn.setText("▶")
                self.set_status(f"● Média LAN indisponible : {exc}", False)
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

            self.queue_list.setRowHeight(row, 26)

        # V29/V31/V32: intentionally no count beside FILE D'ATTENTE.
        self.queue_count_label.setText("")

        if 0 <= selected < self.queue_list.rowCount():
            self.queue_list.setCurrentCell(selected, 1)


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
        if self.public_warning_label:
            self.public_warning_label.setGeometry(
                0, 0, rect.width(), 48
            )

    def open_public_window(self):
        self._ensure_public_window()

        screens = QGuiApplication.screens()
        if len(screens) >= 2:
            self.public_window.setGeometry(
                screens[1].availableGeometry()
            )
            self.public_window.showFullScreen()
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







