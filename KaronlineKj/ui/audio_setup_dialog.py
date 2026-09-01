"""Dialogue de configuration Audio & Effets VST (Microphone, Casque & Réverbération).

Permet de choisir le périphérique d'entrée micro (Mini-jack, USB, Webcam),
d'ajuster les niveaux micro/casque et de contrôler le volume de réverbération VST.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QSettings
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget,
)


class AudioSetupDialog(QDialog):
    """Fenêtre de configuration audio & VST micro/casque."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎧 Configuration Audio & Effets VST — KaronlineBox")
        self.setModal(True)
        self.resize(480, 520)
        self.setMinimumWidth(440)
        self.settings = QSettings("Karonline", "KaronlineKJ")

        self._test_active = False
        self._init_ui()
        self.load_settings()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

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
        self.mic_slider.valueChanged.connect(lambda v: self.mic_val_label.setText(f"{v} %"))
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
        self.head_slider.valueChanged.connect(lambda v: self.head_val_label.setText(f"{v} %"))
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
        self.reverb_slider.valueChanged.connect(lambda v: self.reverb_val_label.setText(f"{v} %"))
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

        # Timer d'animation du VU-mètre de test
        self._vu_timer = QTimer(self)
        self._vu_timer.setInterval(80)
        self._vu_timer.timeout.connect(self._animate_vu_meter)

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

    def _populate_audio_devices(self):
        self.mic_source_combo.clear()
        devices = []
        try:
            from PySide6.QtMultimedia import QMediaDevices
            for dev in QMediaDevices.audioInputs():
                desc = dev.description()
                if desc:
                    devices.append(desc)
        except Exception:
            pass

        if not devices:
            devices = [
                "🎤 Mini-jack PC (Entrée Micro / Line-In 3.5mm)",
                "🎧 USB PC (Microphone / Casque USB)",
                "📹 Webcam HD (Microphone Webcam DUO)",
                "🎙 Microphone par défaut du système",
            ]

        for d in devices:
            self.mic_source_combo.addItem(d)

    def _toggle_test_mic(self):
        self._test_active = not self._test_active
        if self._test_active:
            self.test_btn.setText("⏹ ARRETER LE TEST")
            self.test_btn.setStyleSheet("background:#3a151b;border:1px solid #e80055;color:#ff6b6b;font-weight:700;padding:8px 14px;border-radius:5px;")
            self._vu_timer.start()
        else:
            self.test_btn.setText("🔊 TESTER MON MICRO")
            self.test_btn.setStyleSheet("background:#0d1822;border:1px solid #00c8ff;color:#00c8ff;font-weight:700;padding:8px 14px;border-radius:5px;")
            self._vu_timer.stop()
            self.vu_bar.setValue(0)

    def _animate_vu_meter(self):
        if not self._test_active:
            return
        import random
        base = self.mic_slider.value()
        val = max(0, min(100, int(base * (0.6 + random.random() * 0.45))))
        self.vu_bar.setValue(val)

    def load_settings(self):
        idx = self.settings.value("audio/mic_device_index", 0, type=int)
        if 0 <= idx < self.mic_source_combo.count():
            self.mic_source_combo.setCurrentIndex(idx)
        self.mic_slider.setValue(self.settings.value("audio/mic_level", 80, type=int))
        self.head_slider.setValue(self.settings.value("audio/headphone_level", 80, type=int))
        self.reverb_slider.setValue(self.settings.value("audio/reverb_level", 35, type=int))

    def save_and_accept(self):
        self._vu_timer.stop()
        self.settings.setValue("audio/mic_device_index", self.mic_source_combo.currentIndex())
        self.settings.setValue("audio/mic_device_name", self.mic_source_combo.currentText())
        self.settings.setValue("audio/mic_level", self.mic_slider.value())
        self.settings.setValue("audio/headphone_level", self.head_slider.value())
        self.settings.setValue("audio/reverb_level", self.reverb_slider.value())
        self.accept()
