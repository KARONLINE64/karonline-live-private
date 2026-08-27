"""Dialogue de connexion / enregistrement du compte KJ (API centrale).

La requête réseau tourne dans un thread annexe et le retour est sondé par un
QTimer afin de ne jamais bloquer la boucle événementielle Qt.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)


class AuthDialog(QDialog):
    MODE_LOGIN = "login"
    MODE_REGISTER = "register"

    def __init__(self, parent=None, mode: str = MODE_LOGIN, notice: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Compte KaronlineLive")
        self.setModal(True)
        self.setMinimumWidth(440)

        self.result_ok = False
        self.result_token = ""
        self.result_email = ""
        self.result_card = ""

        self._done = False
        self._result = None
        self._poll_timer = None

        layout = QVBoxLayout(self)
        intro = QLabel(
            "Connectez-vous pour consulter le catalogue et démarrer une "
            "session. Favoris et réglages restent utilisables sans connexion."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#9aa9b7;font-size:13px;")
        layout.addWidget(intro)

        if notice:
            banner = QLabel(notice)
            banner.setWordWrap(True)
            banner.setStyleSheet("color:#00c8ff;font-size:13px;font-weight:600;")
            layout.addWidget(banner)

        self.stack = QStackedWidget()
        self.login_page = self._build_page(is_register=False)
        self.register_page = self._build_page(is_register=True)
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.register_page)
        layout.addWidget(self.stack)

        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setStyleSheet(
            "color:#ff6b6b;font-size:13px;min-height:32px;"
        )
        layout.addWidget(self.feedback)

        buttons = QHBoxLayout()
        self.toggle_btn = QPushButton("Créer un compte")
        self.toggle_btn.setCursor(Qt.PointingHandCursor)
        self.toggle_btn.clicked.connect(self.toggle_mode)
        self.cancel_btn = QPushButton("Plus tard")
        self.cancel_btn.clicked.connect(self.reject)
        self.submit_btn = QPushButton("Se connecter")
        self.submit_btn.setDefault(True)
        self.submit_btn.setStyleSheet(
            "background:linear-gradient(110deg,#124de5,#194fff);"
            "border:0;border-radius:5px;padding:11px 24px;"
            "color:#fff;font-weight:600;"
        )
        self.submit_btn.clicked.connect(self.submit)
        buttons.addWidget(self.toggle_btn)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.submit_btn)
        layout.addLayout(buttons)

        self._apply_mode(mode)

    # ------------------------------------------------------------------
    def _build_page(self, is_register: bool):
        page = QWidget(self)
        form = QFormLayout(page)
        form.setSpacing(10)

        email_edit = QLineEdit()
        email_edit.setPlaceholderText("vous@exemple.com")
        password_edit = QLineEdit()
        password_edit.setEchoMode(QLineEdit.Password)
        password_edit.setPlaceholderText("••••••••")
        form.addRow("Adresse mail", email_edit)
        form.addRow("Mot de passe", password_edit)

        if is_register:
            prefix = "register"
            hint = QLabel(
                "Phase de tests amis/famille : aucun prélèvement. La liaison "
                "réelle de paiement sera activée plus tard ; seuls les 4 "
                "derniers chiffres sont mémorisés."
            )
            hint.setWordWrap(True)
            hint.setStyleSheet("color:#9aa9b7;font-size:11px;")
            form.addRow(hint)
            card_edit = QLineEdit()
            card_edit.setPlaceholderText(
                "Optionnel — ex. 4000 1234 5678 9010"
            )
            form.addRow(QLabel("<b>Carte bancaire</b>"), card_edit)
            self.register_card = card_edit
        else:
            prefix = "login"

        setattr(self, f"{prefix}_email", email_edit)
        setattr(self, f"{prefix}_password", password_edit)
        return page

    def _apply_mode(self, mode: str) -> None:
        register = mode == self.MODE_REGISTER
        self.stack.setCurrentIndex(1 if register else 0)
        self.submit_btn.setText(
            "Créer mon compte" if register else "Se connecter"
        )
        self.toggle_btn.setText(
            "← Retour à la connexion" if register else "Créer un compte"
        )
        self.setWindowTitle(
            "S'enregistrer — KaronlineLive" if register
            else "Connexion — KaronlineLive"
        )
        self.feedback.setText("")

    def toggle_mode(self) -> None:
        self._apply_mode(
            self.MODE_LOGIN if self.stack.currentIndex() == 1
            else self.MODE_REGISTER
        )

    # ------------------------------------------------------------------
    def submit(self) -> None:
        register = self.stack.currentIndex() == 1
        if register:
            email = self.register_email.text().strip()
            password = self.register_password.text()
            card = getattr(self, "register_card", None)
            card = card.text().strip() if card else ""
        else:
            email = self.login_email.text().strip()
            password = self.login_password.text()
            card = ""

        if not email or not password:
            self.feedback.setText("Courriel et mot de passe sont requis.")
            return

        self.feedback.setStyleSheet("color:#9aa9b7;font-size:13px;")
        self.feedback.setText("Communication avec le serveur…")
        self._set_busy(True)
        self._result = None
        self._done = False
        threading.Thread(
            target=self._work,
            args=(register, email, password, card),
            daemon=True,
        ).start()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(120)
        self._poll_timer.timeout.connect(self._poll_result)
        self._poll_timer.start()

    def _set_busy(self, busy: bool) -> None:
        self.submit_btn.setEnabled(not busy)
        self.toggle_btn.setEnabled(not busy)

    def _work(self, register: bool, email: str,
              password: str, card: str) -> None:
        from core.central_auth import api_login, api_register

        try:
            if register:
                outcome = (True, api_register(email, password, card), "")
            else:
                outcome = (True, api_login(email, password), "")
        except Exception as exc:  # noqa: BLE001 — affiché tel quel
            outcome = (False, None, str(exc))
        self._result = outcome

    def _poll_result(self) -> None:
        outcome = self._result
        if outcome is None:
            return
        self._poll_timer.stop()
        ok, data, message = outcome
        self._result = None
        self._set_busy(False)
        if not ok:
            self.feedback.setStyleSheet("color:#ff6b6b;font-size:13px;")
            self.feedback.setText(message)
            return
        self.result_ok = True
        self.result_token = data.get("token", "")
        self.result_email = data.get("email", "")
        self.result_card = data.get("card_label", "")
        self.accept()
