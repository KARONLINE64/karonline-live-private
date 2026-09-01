"""Widget vidéo fixe (non volant) pour KARONLINEBOX DUO.

Affiche la webcam de l'invité dans l'espace dédié de l'onglet DUO sous 'AFFICHER/MASQUER LA WEBCAM INVITÉ',
avec contrôles Mute audio/vidéo et intégration visuelle avec KaronlineBox.
"""
from __future__ import annotations

import base64
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)


class DuoVideoOverlay(QWidget):
    """Widget vidéo fixe (non volant) pour la webcam de l'invité."""

    closed = Signal()
    toggle_audio_muted = Signal(bool)
    toggle_video_muted = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(200)

        self._audio_muted = False
        self._video_muted = False

        self._init_ui()

    def _init_ui(self):
        container = QFrame(self)
        container.setObjectName("duoContainer")
        container.setStyleSheet("""
            #duoContainer {
                background: #080e15;
                border: 2px solid #145cff;
                border-radius: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(8, 8, 8, 8)
        container_layout.setSpacing(6)

        # Header bar avec titre
        header = QHBoxLayout()
        header.setContentsMargins(4, 2, 4, 2)

        title = QLabel("🎥 WEBCAM INVITÉ DUO")
        title.setStyleSheet("color: #00c8ff; font-weight: 700; font-size: 13px;")
        header.addWidget(title)
        header.addStretch()

        self.status_tag = QLabel("○ En attente")
        self.status_tag.setStyleSheet("color: #ff6b6b; font-size: 12px; font-weight: 600;")
        header.addWidget(self.status_tag)

        container_layout.addLayout(header)

        # Zone d'affichage vidéo principal (Webcam Invité)
        self.video_surface = QFrame()
        self.video_surface.setStyleSheet("""
            background: #030507;
            border: 1px solid #20323b;
            border-radius: 6px;
        """)
        video_layout = QVBoxLayout(self.video_surface)
        video_layout.setContentsMargins(0, 0, 0, 0)

        self.avatar_label = QLabel("📷 En attente de la caméra de l'invité...")
        self.avatar_label.setAlignment(Qt.AlignCenter)
        self.avatar_label.setStyleSheet("color: #9aa9b7; font-size: 14px; font-weight: 600;")
        video_layout.addWidget(self.avatar_label)

        container_layout.addWidget(self.video_surface, 1)

        # Footer bar avec contrôles audio / vidéo
        footer = QHBoxLayout()
        footer.setContentsMargins(4, 2, 4, 2)

        self.mic_btn = QPushButton("🎤 Micro ON")
        self.mic_btn.setStyleSheet("""
            QPushButton {
                background: #0b1821;
                border: 1px solid #387a90;
                color: #f4f7fb;
                font-size: 12px;
                padding: 4px 10px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: #145cff;
            }
        """)
        self.mic_btn.clicked.connect(self._toggle_mic)
        footer.addWidget(self.mic_btn)

        self.cam_btn = QPushButton("📹 Cam ON")
        self.cam_btn.setStyleSheet("""
            QPushButton {
                background: #0b1821;
                border: 1px solid #387a90;
                color: #f4f7fb;
                font-size: 12px;
                padding: 4px 10px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: #145cff;
            }
        """)
        self.cam_btn.clicked.connect(self._toggle_cam)
        footer.addWidget(self.cam_btn)

        footer.addStretch()

        container_layout.addLayout(footer)

    def set_guest_name(self, name: str):
        self.avatar_label.setText(f"👤 {name}")

    def update_frame(self, frame_data: str | bytes):
        if not frame_data or self._video_muted:
            return
        try:
            if isinstance(frame_data, str) and frame_data.startswith("data:image"):
                base64_str = frame_data.split(",", 1)[-1]
                raw_bytes = base64.b64decode(base64_str)
            elif isinstance(frame_data, bytes):
                raw_bytes = frame_data
            else:
                return

            pixmap = QPixmap()
            if pixmap.loadFromData(raw_bytes):
                target_size = self.video_surface.size()
                if target_size.width() > 10 and target_size.height() > 10:
                    scaled = pixmap.scaled(
                        target_size,
                        Qt.KeepAspectRatio,
                        Qt.SmoothTransformation
                    )
                    self.avatar_label.setPixmap(scaled)
                else:
                    self.avatar_label.setPixmap(pixmap)
        except Exception:
            pass

    def set_connected_status(self, connected: bool):
        if connected:
            self.status_tag.setText("● Connecté")
            self.status_tag.setStyleSheet("color: #4ade80; font-size: 12px; font-weight: 600;")
        else:
            self.status_tag.setText("○ En attente")
            self.status_tag.setStyleSheet("color: #ff6b6b; font-size: 12px; font-weight: 600;")
            self.avatar_label.setText("📷 En attente de la caméra de l'invité...")

    def _toggle_mic(self):
        self._audio_muted = not self._audio_muted
        if self._audio_muted:
            self.mic_btn.setText("🔇 Micro OFF")
            self.mic_btn.setStyleSheet("""
                QPushButton {
                    background: #3a151b;
                    border: 1px solid #e80055;
                    color: #ff6b6b;
                    font-size: 12px;
                    padding: 4px 10px;
                    border-radius: 4px;
                }
            """)
        else:
            self.mic_btn.setText("🎤 Micro ON")
            self.mic_btn.setStyleSheet("""
                QPushButton {
                    background: #0b1821;
                    border: 1px solid #387a90;
                    color: #f4f7fb;
                    font-size: 12px;
                    padding: 4px 10px;
                    border-radius: 4px;
                }
            """)
        self.toggle_audio_muted.emit(self._audio_muted)

    def _toggle_cam(self):
        self._video_muted = not self._video_muted
        if self._video_muted:
            self.cam_btn.setText("🚫 Cam OFF")
            self.cam_btn.setStyleSheet("""
                QPushButton {
                    background: #3a151b;
                    border: 1px solid #e80055;
                    color: #ff6b6b;
                    font-size: 12px;
                    padding: 4px 10px;
                    border-radius: 4px;
                }
            """)
        else:
            self.cam_btn.setText("📹 Cam ON")
            self.cam_btn.setStyleSheet("""
                QPushButton {
                    background: #0b1821;
                    border: 1px solid #387a90;
                    color: #f4f7fb;
                    font-size: 12px;
                    padding: 4px 10px;
                    border-radius: 4px;
                }
            """)
        self.toggle_video_muted.emit(self._video_muted)
