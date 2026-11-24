from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout)


class LoginDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in - Meeting Assistant")
        self.email = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.error = QLabel()
        self.error.setStyleSheet("color:#c0392b")
        form = QFormLayout()
        form.addRow("Email", self.email)
        form.addRow("Password", self.password)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)
        self.setMinimumWidth(340)

    def credentials(self) -> tuple[str, str]:
        return self.email.text().strip(), self.password.text()

    def show_error(self, message: str) -> None:
        self.error.setText(message)
