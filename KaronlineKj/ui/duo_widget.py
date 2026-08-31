"""Widget vidéo flottant pour KARONLINEBOX DUO.

Affiche la webcam de l'invité dans une fenêtre flottante redimensionnable et déplaçable,
avec contrôles Mute audio/vidéo et intégration visuelle avec KaronlineBox.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QIcon, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)


class DuoVideoOverlay(QWidget):
    """Fenêtre vidéo flottante déplaçable et redimensionnable."""

    closed = Signal()
    toggle_audio_muted = Signal(bool)
    toggle_video_muted = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(320, 240)
        self.setMinimumSize(220, 165)

        self._drag_position = QPoint()
        self._is_dragging = False
        self._is_resizing = False
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
                border-radius: 10px;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(container)

        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(6, 6, 6, 6)
        container_layout.setSpacing(4)

        # Header bar avec titre et bouton fermer
        header = QHBoxLayout()
        header.setContentsMargins(4, 2, 4, 2)

        title = QLabel("🎙 KARONLINEBOX DUO")
        title.setStyleSheet("color: #00c8ff; font-weight: 700; font-size: 11px;")
        header.addWidget(title)
        header.addStretch()

        self.close_btn = QPushButton("✕")
        self.close_btn.setFixedSize(20, 20)
        self.close_btn.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #ff6b6b;
                border: none;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                color: #ffffff;
                background: #e80055;
                border-radius: 3px;
            }
        """)
        self.close_btn.clicked.connect(self.close)
        header.addWidget(self.close_btn)
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

        self.avatar_label = QLabel("📷 En attente de l'invité...")
        self.avatar_label.setAlignment(Qt.AlignCenter)
        self.avatar_label.setStyleSheet("color: #9aa9b7; font-size: 13px;")
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
                font-size: 11px;
                padding: 3px 8px;
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
                font-size: 11px;
                padding: 3px 8px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: #145cff;
            }
        """)
        self.cam_btn.clicked.connect(self._toggle_cam)
        footer.addWidget(self.cam_btn)

        footer.addStretch()

        self.status_tag = QLabel("● Connecté")
        self.status_tag.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: 600;")
        footer.addWidget(self.status_tag)

        container_layout.addLayout(footer)

    def set_guest_name(self, name: str):
        self.avatar_label.setText(f"👤 {name}")

    def set_connected_status(self, connected: bool):
        if connected:
            self.status_tag.setText("● Connecté")
            self.status_tag.setStyleSheet("color: #4ade80; font-size: 11px; font-weight: 600;")
        else:
            self.status_tag.setText("○ En attente")
            self.status_tag.setStyleSheet("color: #ff6b6b; font-size: 11px; font-weight: 600;")
            self.avatar_label.setText("📷 En attente de l'invité...")

    def _toggle_mic(self):
        self._audio_muted = not self._audio_muted
        if self._audio_muted:
            self.mic_btn.setText("🔇 Micro OFF")
            self.mic_btn.setStyleSheet("""
                QPushButton {
                    background: #3a151b;
                    border: 1px solid #e80055;
                    color: #ff6b6b;
                    font-size: 11px;
                    padding: 3px 8px;
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
                    font-size: 11px;
                    padding: 3px 8px;
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
                    font-size: 11px;
                    padding: 3px 8px;
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
                    font-size: 11px;
                    padding: 3px 8px;
                    border-radius: 4px;
                }
            """)
        self.toggle_video_muted.emit(self._video_muted)

    # ------------------------------------------------------------------
    # Gestion du glisser-déplacer & redimensionnement à la souris
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            # Coin inférieur droit pour le redimensionnement
            if event.position().x() > self.width() - 20 and event.position().y() > self.height() - 20:
                self._is_resizing = True
            else:
                self._is_dragging = True
                self._drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_resizing:
            new_width = max(self.minimumWidth(), int(event.position().x()))
            new_height = max(self.minimumHeight(), int(event.position().y()))
            self.resize(new_width, new_height)
            event.accept()
        elif self._is_dragging:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._is_dragging = False
        self._is_resizing = False

    def closeEvent(self, event):
        self.closed.emit()
        super().closeEvent(event)
