"""Dialogue de connexion / enregistrement du compte KJ (API centrale).

La requête réseau tourne dans un thread annexe et le retour est sondé par un
QTimer afin de ne jamais bloquer la boucle événementielle Qt.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
    QStackedWidget, QVBoxLayout, QWidget,
)


class AuthDialog(QDialog):
    MODE_LOGIN = "login"
    MODE_REGISTER = "register"

    def __init__(self, parent=None, mode: str = MODE_LOGIN, notice: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Compte KaronlineLive")
        self.setModal(True)
        self.setMinimumWidth(400)

        self.result_ok = False
        self.result_token = ""
        self.result_email = ""
        self.result_card = ""

        self._done = False
        self._result = None
        self._poll_timer = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(6)

        intro = QLabel(
            "Connectez-vous pour consulter le catalogue et démarrer une "
            "session. Favoris et réglages restent utilisables sans connexion."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet("color:#9aa9b7;font-size:12px;")
        layout.addWidget(intro)

        if notice:
            banner = QLabel(notice)
            banner.setWordWrap(True)
            banner.setStyleSheet("color:#00c8ff;font-size:12px;font-weight:600;")
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
            "color:#ff6b6b;font-size:12px;min-height:16px;"
        )
        layout.addWidget(self.feedback)

        buttons = QHBoxLayout()
        self.cancel_btn = QPushButton("Annuler")
        self.cancel_btn.clicked.connect(self.reject)
        self.submit_btn = QPushButton("Se connecter")
        self.submit_btn.setDefault(True)
        self.submit_btn.setStyleSheet(
            "background:linear-gradient(110deg,#124de5,#194fff);"
            "border:0;border-radius:5px;padding:8px 20px;"
            "color:#fff;font-weight:600;"
        )
        self.submit_btn.clicked.connect(self.submit)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_btn)
        buttons.addWidget(self.submit_btn)
        layout.addLayout(buttons)

        self._apply_mode(mode)

    # ------------------------------------------------------------------
    def _build_page(self, is_register: bool):
        page = QWidget(self)
        form = QFormLayout(page)
        form.setContentsMargins(0, 2, 0, 2)
        form.setSpacing(6)

        email_edit = QLineEdit()
        email_edit.setPlaceholderText("vous@exemple.com")
        password_edit = QLineEdit()
        password_edit.setEchoMode(QLineEdit.Password)
        password_edit.setPlaceholderText("••••••••")

        password_row = QWidget()
        password_row_layout = QHBoxLayout(password_row)
        password_row_layout.setContentsMargins(0, 0, 0, 0)
        password_row_layout.setSpacing(4)
        password_toggle_btn = QPushButton("👁")
        password_toggle_btn.setCheckable(True)
        password_toggle_btn.setFixedWidth(32)
        password_toggle_btn.setToolTip("Afficher/masquer le mot de passe")
        password_toggle_btn.setStyleSheet(
            "QPushButton{background:#0b1821;border:1px solid #387a90;"
            "border-radius:4px;color:#00c8ff;}"
            "QPushButton:checked{background:#145cff;color:#ffffff;}"
        )
        password_toggle_btn.toggled.connect(
            lambda checked, edit=password_edit: edit.setEchoMode(
                QLineEdit.Normal if checked else QLineEdit.Password
            )
        )
        password_row_layout.addWidget(password_edit)
        password_row_layout.addWidget(password_toggle_btn)

        form.addRow("Adresse mail", email_edit)
        form.addRow("Mot de passe", password_row)

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
        self.setWindowTitle(
            "S'enregistrer — KaronlineLive" if register
            else "Connexion — KaronlineLive"
        )
        self.feedback.setText("")
        QTimer.singleShot(0, self.adjustSize)

    def toggle_mode(self) -> None:
        self._apply_mode(
            self.MODE_LOGIN if self.stack.currentIndex() == 1
            else self.MODE_REGISTER
        )

    # ------------------------------------------------------------------
    def submit(self, force: bool = False) -> None:
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

        self.feedback.setStyleSheet("color:#9aa9b7;font-size:12px;")
        self.feedback.setText("Communication avec le serveur…")
        self._set_busy(True)
        self._result = None
        self._done = False
        threading.Thread(
            target=self._work,
            args=(register, email, password, card, force),
            daemon=True,
        ).start()
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(120)
        self._poll_timer.timeout.connect(self._poll_result)
        self._poll_timer.start()

    def _set_busy(self, busy: bool) -> None:
        self.submit_btn.setEnabled(not busy)

    def _work(self, register: bool, email: str,
              password: str, card: str, force: bool = False) -> None:
        from core.central_auth import AuthError, api_login, api_register

        try:
            if register:
                outcome = (True, api_register(email, password, card), "")
            else:
                outcome = (True, api_login(email, password, force=force), "")
        except AuthError as exc:
            outcome = (False, {"code": exc.code, "message": str(exc)}, str(exc))
        except Exception as exc:  # noqa: BLE001 — affiché tel quel
            outcome = (False, {"code": "UNKNOWN", "message": str(exc)}, str(exc))
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
            code = data.get("code") if isinstance(data, dict) else ""
            if code == "ALREADY_CONNECTED":
                reply = QMessageBox.question(
                    self,
                    "COMPTE DÉJÀ CONNECTÉ",
                    "Ce compte est déjà connecté sur un autre appareil ou sur le site.\n\n"
                    "Voulez-vous déconnecter l'autre session et vous connecter ici ?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if reply == QMessageBox.Yes:
                    self.submit(force=True)
                    return
            self.feedback.setStyleSheet("color:#ff6b6b;font-size:12px;")
            self.feedback.setText(message)
            return
        self.result_ok = True
        self.result_token = data.get("token", "")
        self.result_email = data.get("email", "")
        self.result_card = data.get("card_label", "")
        self.accept()
