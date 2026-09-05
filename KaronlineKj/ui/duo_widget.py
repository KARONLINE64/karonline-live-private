"""Widget vidéo fixe (non volant) pour KARONLINEBOX DUO.

Affiche la webcam de l'invité dans l'espace dédié de l'onglet DUO sous 'AFFICHER/MASQUER LA WEBCAM INVITÉ',
avec contrôles Mute audio/vidéo et intégration visuelle avec KaronlineBox.
"""
from __future__ import annotations

import base64
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPlainTextEdit,
    QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)


class DuoChatPanel(QFrame):
    """Panneau de discussion fixe affiché dans l'onglet DUO."""

    message_requested = Signal(str)
    close_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("duoChatPanel")
        self.setFixedHeight(215)
        self.setStyleSheet("""
            #duoChatPanel {
                background: #030b12;
                border: 2px solid #145cff;
                border-radius: 8px;
            }
            QPlainTextEdit {
                background: #02080e;
                border: 1px solid #145cff;
                border-radius: 6px;
                color: #f4f7fb;
                font-size: 14px;
                padding: 8px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("💬 Chat Box")
        title.setStyleSheet("color:#dce8ff;font-size:17px;font-weight:700;")
        header.addWidget(title)
        header.addStretch()
        close_top = QPushButton("×")
        close_top.setFixedSize(30, 26)
        close_top.setStyleSheet("color:#f4f7fb;font-size:22px;font-weight:700;border:0;")
        close_top.clicked.connect(self.close_requested)
        header.addWidget(close_top)
        layout.addLayout(header)

        self.history = QPlainTextEdit()
        self.history.setReadOnly(True)
        self.history.document().setMaximumBlockCount(50)
        self.history.setPlaceholderText("Messages disponibles pendant la session DUO.")
        layout.addWidget(self.history, 1)

        self.input = QPlainTextEdit()
        self.input.setFixedHeight(52)
        self.input.setPlaceholderText("Écrire un message...")
        layout.addWidget(self.input)

        composer = QHBoxLayout()
        self.close_button = QPushButton("✖  Fermer")
        self.send_button = QPushButton("ENVOYER")
        self.send_button.clicked.connect(self._send_message)
        self.close_button.clicked.connect(self.close_requested)
        composer.addWidget(self.close_button)
        composer.addStretch()
        composer.addWidget(self.send_button)
        layout.addLayout(composer)

    def _send_message(self):
        text = self.input.toPlainText().strip()
        if not text:
            return
        self.message_requested.emit(text[:500])
        self.input.clear()

    def append_messages(self, messages: list):
        for message in messages:
            sender = str(message.get("sender", "Participant"))
            text = str(message.get("text", "")).strip()
            if text:
                self.history.appendPlainText(f"{sender} : {text}")


class DuoVideoOverlay(QWidget):
    """Widget vidéo fixe (non volant) pour la webcam de l'invité."""

    closed = Signal()
    toggle_audio_muted = Signal(bool)
    toggle_video_muted = Signal(bool)
    frame_error = Signal(str)
    chat_message_requested = Signal(str)
    chat_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumHeight(320)

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

        self.chat_btn = QPushButton("💬 Chat Box")
        self.chat_btn.setStyleSheet("""
            QPushButton {
                background: #0b1821;
                border: 1px solid #387a90;
                color: #f4f7fb;
                font-size: 12px;
                padding: 4px 10px;
                border-radius: 4px;
            }
            QPushButton:hover { background: #145cff; }
        """)
        self.chat_btn.clicked.connect(self.chat_requested)
        footer.addWidget(self.chat_btn)

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
            else:
                self.frame_error.emit("Image webcam invitée reçue mais illisible.")
        except Exception as exc:
            self.frame_error.emit(f"Impossible d'afficher la webcam invitée : {exc}")

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
