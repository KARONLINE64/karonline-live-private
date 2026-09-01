"""Dialogue de configuration Audio & Effets VST (Microphone, Casque & Réverbération).

Permet de choisir le périphérique d'entrée micro (Mini-jack, USB, Webcam),
d'ajuster les niveaux micro/casque et de contrôler le volume de réverbération VST.
Le test du micro effectue une vraie capture audio, appliquée en direct au casque.
"""
from __future__ import annotations

import math
from array import array

from PySide6.QtCore import Qt, QObject, QSettings, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QAudioSource, QMediaDevices
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QSlider, QVBoxLayout, QWidget,
)


class LiveMicMonitor(QObject):
    """Capture le micro en direct et le renvoie vers le casque avec gain et écho/réverb.

    Sert à la fois de moteur de test réel et de source pour le VU-mètre (basé
    sur le niveau crête effectivement mesuré, pas une simulation).
    """

    levelChanged = Signal(int)
    error = Signal(str)

    SAMPLE_RATE = 44100
    REVERB_DELAY_MS = 260
    REVERB_FEEDBACK = 0.35

    def __init__(self, parent=None):
        super().__init__(parent)
        self.mic_gain = 1.0
        self.head_gain = 1.0
        self.reverb_mix = 0.0
        self.running = False
        self._out_channels = 1
        self._delay_len = max(1, int(self.SAMPLE_RATE * self.REVERB_DELAY_MS / 1000))
        self._delay_buf = array("h", [0] * self._delay_len)
        self._delay_pos = 0
        self._source = None
        self._sink = None
        self._input_io = None
        self._output_io = None

    def start(self, input_device) -> bool:
        self.stop()

        fmt = QAudioFormat()
        fmt.setSampleRate(self.SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.Int16)
        if not input_device.isFormatSupported(fmt):
            fmt = input_device.preferredFormat()
            fmt.setChannelCount(1)

        output_device = QMediaDevices.defaultAudioOutput()
        if output_device.isNull():
            self.error.emit("Aucune sortie casque/haut-parleurs détectée sur ce PC.")
            return False
        out_fmt = fmt if output_device.isFormatSupported(fmt) else output_device.preferredFormat()
        self._out_channels = max(1, out_fmt.channelCount())

        try:
            self._source = QAudioSource(input_device, fmt, self)
            self._sink = QAudioSink(output_device, out_fmt, self)
            self._input_io = self._source.start()
            self._output_io = self._sink.start()
        except Exception as exc:
            self.error.emit(f"Impossible d'ouvrir le micro : {exc}")
            return False

        if self._input_io is None or self._output_io is None:
            self.error.emit(
                "Impossible d'ouvrir le micro sélectionné (vérifiez l'autorisation "
                "microphone dans les paramètres de confidentialité Windows)."
            )
            return False

        self._delay_buf = array("h", [0] * self._delay_len)
        self._delay_pos = 0
        self._input_io.readyRead.connect(self._on_ready_read)
        self.running = True
        return True

    def stop(self):
        if self._input_io is not None:
            try:
                self._input_io.readyRead.disconnect(self._on_ready_read)
            except Exception:
                pass
        if self._source is not None:
            self._source.stop()
        if self._sink is not None:
            self._sink.stop()
        self._source = None
        self._sink = None
        self._input_io = None
        self._output_io = None
        self.running = False
        self.levelChanged.emit(0)

    def _on_ready_read(self):
        if self._input_io is None:
            return
        data = bytes(self._input_io.readAll())
        if len(data) % 2:
            data = data[:-1]
        if len(data) < 2:
            return
        samples = array("h", data)

        gain = self.mic_gain
        mix = max(0.0, min(0.9, self.reverb_mix))
        head_gain = self.head_gain
        delay_buf = self._delay_buf
        dlen = len(delay_buf)
        pos = self._delay_pos
        peak = 0

        out_samples = array("h", [0] * len(samples))
        for i, s in enumerate(samples):
            dry = int(s * gain)
            delayed = delay_buf[pos]
            wet = int(dry * (1 - mix) + delayed * mix)
            feed = dry + int(delayed * self.REVERB_FEEDBACK)
            feed = max(-32768, min(32767, feed))
            delay_buf[pos] = feed
            pos += 1
            if pos >= dlen:
                pos = 0
            out = max(-32768, min(32767, int(wet * head_gain)))
            out_samples[i] = out
            peak = max(peak, abs(out))
        self._delay_pos = pos

        if self._out_channels > 1:
            expanded = array("h", [0] * (len(out_samples) * self._out_channels))
            for i, v in enumerate(out_samples):
                for c in range(self._out_channels):
                    expanded[i * self._out_channels + c] = v
            out_bytes = expanded.tobytes()
        else:
            out_bytes = out_samples.tobytes()

        if self._output_io is not None:
            self._output_io.write(out_bytes)

        level_pct = 0
        if peak > 0:
            db = 20 * math.log10(peak / 32768.0)
            level_pct = max(0, min(100, int((db + 45) / 45 * 100)))
        self.levelChanged.emit(level_pct)


class AudioSetupDialog(QDialog):
    """Fenêtre de configuration audio & VST micro/casque."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎧 Configuration Audio & Effets VST — KaronlineBox")
        self.setModal(True)
        self.resize(480, 420)
        self.setMinimumWidth(440)
        self.setMinimumHeight(320)
        self.settings = QSettings("Karonline", "KaronlineKJ")

        self._test_active = False
        self.monitor = LiveMicMonitor(self)
        self._init_ui()
        self.monitor.levelChanged.connect(self.vu_bar.setValue)
        self.monitor.error.connect(self._on_monitor_error)
        self.load_settings()

    def _init_ui(self):
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll_content = QWidget()
        scroll.setWidget(scroll_content)

        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        # En-tête
        header = QLabel("🎧 CONFIGURATION MICRO, CASQUE & EFFET VST")
        header.setStyleSheet("color:#00c8ff;font-size:16px;font-weight:700;")
        layout.addWidget(header)

        sub_header = QLabel(
            "Sélectionnez votre micro, ajustez les niveaux de gain et réglez la "
            "réverbération vocale pour une qualité sonore optimale en karaoké et DUO."
        )
        sub_header.setWordWrap(True)
        sub_header.setStyleSheet("color:#9aa9b7;font-size:12px;margin-bottom:4px;")
        layout.addWidget(sub_header)

        # 1. Sélection de l'entrée Micro
        input_box = QFrame()
        input_box.setStyleSheet("background:#08131c;border:1px solid #1b6f91;border-radius:6px;padding:10px;")
        input_layout = QVBoxLayout(input_box)
        input_layout.setSpacing(8)

        input_title = QLabel("🎙 PERIPHÉRIQUE D'ENTRÉE MICROPHONE")
        input_title.setStyleSheet("color:#f4f7fb;font-size:13px;font-weight:700;")
        input_layout.addWidget(input_title)

        self.mic_source_combo = QComboBox()
        self.mic_source_combo.setStyleSheet("""
            QComboBox {
                background: #0b1821;
                border: 1px solid #387a90;
                border-radius: 4px;
                color: #00c8ff;
                font-size: 13px;
                font-weight: 600;
                padding: 6px 10px;
            }
            QComboBox::drop-down { border: none; }
        """)
        self._populate_audio_devices()
        self.mic_source_combo.currentIndexChanged.connect(self._on_mic_device_changed)
        input_layout.addWidget(self.mic_source_combo)

        layout.addWidget(input_box)

        # 2. Curseurs d'ajustement Niveaux & VST
        sliders_box = QFrame()
        sliders_box.setStyleSheet("background:#08131c;border:1px solid #1b6f91;border-radius:6px;padding:10px;")
        sliders_layout = QVBoxLayout(sliders_box)
        sliders_layout.setSpacing(12)

        # Niveau Micro
        mic_row_head = QHBoxLayout()
        mic_title = QLabel("🎤 NIVEAU MICROPHONE (GAIN)")
        mic_title.setStyleSheet("color:#f4f7fb;font-size:12px;font-weight:700;")
        self.mic_val_label = QLabel("80 %")
        self.mic_val_label.setStyleSheet("color:#00c8ff;font-size:12px;font-weight:700;")
        mic_row_head.addWidget(mic_title)
        mic_row_head.addStretch()
        mic_row_head.addWidget(self.mic_val_label)
        sliders_layout.addLayout(mic_row_head)

        self.mic_slider = QSlider(Qt.Horizontal)
        self.mic_slider.setRange(0, 100)
        self.mic_slider.setValue(80)
        self.mic_slider.valueChanged.connect(self._on_mic_slider_changed)
        sliders_layout.addWidget(self.mic_slider)

        # VU-mètre Micro
        self.vu_bar = QProgressBar()
        self.vu_bar.setRange(0, 100)
        self.vu_bar.setValue(0)
        self.vu_bar.setTextVisible(False)
        self.vu_bar.setFixedHeight(8)
        self.vu_bar.setStyleSheet("""
            QProgressBar {
                background: #030507;
                border: 1px solid #20323b;
                border-radius: 4px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4ade80, stop:0.7 #facc15, stop:1 #ef4444);
                border-radius: 3px;
            }
        """)
        sliders_layout.addWidget(self.vu_bar)

        # Niveau Casque / Sortie
        head_row_head = QHBoxLayout()
        head_title = QLabel("🎧 NIVEAU CASQUE & RETOURS")
        head_title.setStyleSheet("color:#f4f7fb;font-size:12px;font-weight:700;")
        self.head_val_label = QLabel("80 %")
        self.head_val_label.setStyleSheet("color:#00c8ff;font-size:12px;font-weight:700;")
        head_row_head.addWidget(head_title)
        head_row_head.addStretch()
        head_row_head.addWidget(self.head_val_label)
        sliders_layout.addLayout(head_row_head)

        self.head_slider = QSlider(Qt.Horizontal)
        self.head_slider.setRange(0, 100)
        self.head_slider.setValue(80)
        self.head_slider.valueChanged.connect(self._on_head_slider_changed)
        sliders_layout.addWidget(self.head_slider)

        # Réverbération VST
        reverb_row_head = QHBoxLayout()
        reverb_title = QLabel("✨ VOLUME RÉVERBÉRATION VST (MICRO)")
        reverb_title.setStyleSheet("color:#00c8ff;font-size:12px;font-weight:700;")
        self.reverb_val_label = QLabel("35 %")
        self.reverb_val_label.setStyleSheet("color:#00c8ff;font-size:12px;font-weight:700;")
        reverb_row_head.addWidget(reverb_title)
        reverb_row_head.addStretch()
        reverb_row_head.addWidget(self.reverb_val_label)
        sliders_layout.addLayout(reverb_row_head)

        self.reverb_slider = QSlider(Qt.Horizontal)
        self.reverb_slider.setRange(0, 100)
        self.reverb_slider.setValue(35)
        self.reverb_slider.valueChanged.connect(self._on_reverb_slider_changed)
        sliders_layout.addWidget(self.reverb_slider)

        # Presets VST
        preset_layout = QHBoxLayout()
        preset_layout.setSpacing(6)
        presets = [
            ("🎤 Hall Karaoké", 45),
            ("🎶 Studio Vocal", 25),
            ("📢 Écho Événement", 65),
            ("🚫 VST Off", 0),
        ]
        for name, val in presets:
            btn = QPushButton(name)
            btn.setStyleSheet("""
                QPushButton {
                    background: #0b1821;
                    border: 1px solid #387a90;
                    color: #d8dee5;
                    font-size: 11px;
                    padding: 4px 8px;
                    border-radius: 4px;
                }
                QPushButton:hover {
                    background: #145cff;
                    color: #ffffff;
                }
            """)
            btn.clicked.connect(lambda _, v=val: self.reverb_slider.setValue(v))
            preset_layout.addWidget(btn)

        sliders_layout.addLayout(preset_layout)

        layout.addWidget(sliders_box)

        # Actions bas de page
        actions_layout = QHBoxLayout()

        self.test_btn = QPushButton("🔊 TESTER MON MICRO")
        self.test_btn.setStyleSheet("""
            QPushButton {
                background: #0d1822;
                border: 1px solid #00c8ff;
                color: #00c8ff;
                font-weight: 700;
                font-size: 12px;
                padding: 8px 14px;
                border-radius: 5px;
            }
            QPushButton:hover { background: #123544; }
        """)
        self.test_btn.clicked.connect(self._toggle_test_mic)

        self.save_btn = QPushButton("Valider & Enregistrer")
        self.save_btn.setStyleSheet("""
            QPushButton {
                background: linear-gradient(110deg, #124de5, #194fff);
                border: none;
                color: #ffffff;
                font-weight: 700;
                font-size: 13px;
                padding: 8px 18px;
                border-radius: 5px;
            }
            QPushButton:hover { filter: brightness(1.2); }
        """)
        self.save_btn.clicked.connect(self.save_and_accept)

        actions_layout.addWidget(self.test_btn)
        actions_layout.addStretch()
        actions_layout.addWidget(self.save_btn)

        layout.addLayout(actions_layout)

        outer_layout.addWidget(scroll)

    def _populate_audio_devices(self):
        self.mic_source_combo.clear()
        try:
            devices = list(QMediaDevices.audioInputs())
        except Exception:
            devices = []

        if not devices:
            self.mic_source_combo.addItem("🎙 Aucun micro détecté sur ce PC", None)
            return

        for dev in devices:
            desc = dev.description() or "Périphérique micro"
            self.mic_source_combo.addItem(f"🎤 {desc}", dev)

    def _on_mic_device_changed(self, _index):
        if self._test_active:
            device = self.mic_source_combo.currentData()
            if device is not None:
                self.monitor.start(device)

    def _on_mic_slider_changed(self, v):
        self.mic_val_label.setText(f"{v} %")
        self.monitor.mic_gain = (v / 100.0) * 1.5

    def _on_head_slider_changed(self, v):
        self.head_val_label.setText(f"{v} %")
        self.monitor.head_gain = (v / 100.0) * 1.2

    def _on_reverb_slider_changed(self, v):
        self.reverb_val_label.setText(f"{v} %")
        self.monitor.reverb_mix = (v / 100.0) * 0.6

    def _toggle_test_mic(self):
        if self._test_active:
            self.monitor.stop()
            self._test_active = False
            self.test_btn.setText("🔊 TESTER MON MICRO")
            self.test_btn.setStyleSheet("background:#0d1822;border:1px solid #00c8ff;color:#00c8ff;font-weight:700;padding:8px 14px;border-radius:5px;")
            return

        device = self.mic_source_combo.currentData()
        if device is None:
            QMessageBox.warning(self, "Micro introuvable", "Aucun périphérique micro valide n'est sélectionné.")
            return

        self.monitor.mic_gain = (self.mic_slider.value() / 100.0) * 1.5
        self.monitor.head_gain = (self.head_slider.value() / 100.0) * 1.2
        self.monitor.reverb_mix = (self.reverb_slider.value() / 100.0) * 0.6

        if not self.monitor.start(device):
            return

        self._test_active = True
        self.test_btn.setText("⏹ ARRETER LE TEST")
        self.test_btn.setStyleSheet("background:#3a151b;border:1px solid #e80055;color:#ff6b6b;font-weight:700;padding:8px 14px;border-radius:5px;")

    def _on_monitor_error(self, message):
        self._test_active = False
        self.test_btn.setText("🔊 TESTER MON MICRO")
        self.test_btn.setStyleSheet("background:#0d1822;border:1px solid #00c8ff;color:#00c8ff;font-weight:700;padding:8px 14px;border-radius:5px;")
        self.vu_bar.setValue(0)
        QMessageBox.warning(self, "Erreur audio", message)

    def load_settings(self):
        idx = self.settings.value("audio/mic_device_index", 0, type=int)
        if 0 <= idx < self.mic_source_combo.count():
            self.mic_source_combo.setCurrentIndex(idx)
        self.mic_slider.setValue(self.settings.value("audio/mic_level", 80, type=int))
        self.head_slider.setValue(self.settings.value("audio/headphone_level", 80, type=int))
        self.reverb_slider.setValue(self.settings.value("audio/reverb_level", 35, type=int))

    def save_and_accept(self):
        self.monitor.stop()
        self._test_active = False
        self.settings.setValue("audio/mic_device_index", self.mic_source_combo.currentIndex())
        self.settings.setValue("audio/mic_device_name", self.mic_source_combo.currentText())
        self.settings.setValue("audio/mic_level", self.mic_slider.value())
        self.settings.setValue("audio/headphone_level", self.head_slider.value())
        self.settings.setValue("audio/reverb_level", self.reverb_slider.value())
        self.accept()

    def reject(self):
        self.monitor.stop()
        self._test_active = False
        super().reject()
