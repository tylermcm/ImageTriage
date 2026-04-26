from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout

from ..file_associations import (
    FileAssociationStatus,
    open_windows_default_apps_settings,
    query_windows_file_association_status,
    register_windows_file_associations,
    remove_windows_file_associations,
)


class FileAssociationsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("File Associations - Image Triage")
        self.setModal(True)
        self.resize(560, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "Register Image Triage as a supported handler for standard images, RAW files, FITS files, and PSD files. "
            "This makes it available in Windows Open with and Default apps. It does not silently replace your current defaults."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self.extensions_label = QLabel()
        self.extensions_label.setWordWrap(True)
        self.extensions_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.extensions_label)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        self.register_button = QPushButton("Register")
        self.remove_button = QPushButton("Remove")
        self.default_apps_button = QPushButton("Open Windows Default Apps")
        self.refresh_button = QPushButton("Refresh")
        actions_layout.addWidget(self.register_button)
        actions_layout.addWidget(self.remove_button)
        actions_layout.addWidget(self.default_apps_button)
        actions_layout.addWidget(self.refresh_button)
        actions_layout.addStretch(1)
        layout.addLayout(actions_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.register_button.clicked.connect(self._register_associations)
        self.remove_button.clicked.connect(self._remove_associations)
        self.default_apps_button.clicked.connect(self._open_default_apps)
        self.refresh_button.clicked.connect(self._refresh_status)

        self._refresh_status()

    def _refresh_status(self) -> None:
        status = query_windows_file_association_status()
        if not status.windows_supported:
            self.status_label.setText("This feature is only available on Windows.")
            self.extensions_label.setText("")
            self.register_button.setEnabled(False)
            self.remove_button.setEnabled(False)
            self.default_apps_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            return

        registered_count = len(status.registered_suffixes)
        total_count = len(status.supported_suffixes)
        self.status_label.setText(
            f"Registered extensions: {registered_count} / {total_count}\n"
            f"Launch command:\n{status.command}"
        )
        extensions_preview = ", ".join(status.supported_suffixes[:20])
        if total_count > 20:
            extensions_preview = f"{extensions_preview}, ..."
        self.extensions_label.setText(f"Supported extensions:\n{extensions_preview}")
        self.register_button.setEnabled(registered_count < total_count or not status.app_registered)
        self.remove_button.setEnabled(registered_count > 0 or status.app_registered)

    def _register_associations(self) -> None:
        self._run_registry_action(register_windows_file_associations, "Image Triage is now registered in Windows file associations.")

    def _remove_associations(self) -> None:
        self._run_registry_action(remove_windows_file_associations, "Image Triage file association registration was removed.")

    def _run_registry_action(self, action, success_message: str) -> None:
        try:
            status = action()
        except Exception as exc:
            QMessageBox.warning(self, "File Associations", f"Could not update file associations.\n\n{exc}")
            return
        self._refresh_status_with_message(status, success_message)

    def _refresh_status_with_message(self, _status: FileAssociationStatus, message: str) -> None:
        self._refresh_status()
        QMessageBox.information(self, "File Associations", message)

    def _open_default_apps(self) -> None:
        try:
            open_windows_default_apps_settings()
        except Exception as exc:
            QMessageBox.warning(self, "File Associations", f"Could not open Windows Default Apps.\n\n{exc}")
