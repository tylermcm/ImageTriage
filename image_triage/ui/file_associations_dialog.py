from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..file_associations import (
    APP_FRIENDLY_NAME,
    FileAssociationStatus,
    describe_windows_default_handler,
    open_windows_default_apps_settings,
    open_windows_file_association_chooser,
    query_windows_file_association_states,
    query_windows_file_association_status,
    register_windows_file_associations,
    remove_windows_file_associations,
)


class FileAssociationsDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("File Associations - Image Triage")
        self.setModal(True)
        self.resize(720, 560)
        self._row_suffixes: list[str] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        intro = QLabel(
            "Choose which extensions Image Triage should register for. "
            "Use Set As Default to open the Windows chooser for the selected extension and lock it to Image Triage there."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["Extension", "Registered", "Default"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)

        help_label = QLabel(
            "Windows controls the final default app selection. "
            "Register Selected makes Image Triage available for those extensions. "
            "Set As Default opens the Windows chooser for the selected extension."
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)
        self.register_selected_button = QPushButton("Register Selected")
        self.remove_selected_button = QPushButton("Remove Selected")
        self.set_default_button = QPushButton("Set As Default...")
        self.register_all_button = QPushButton("Register All")
        self.default_apps_button = QPushButton("Open Windows Default Apps")
        self.refresh_button = QPushButton("Refresh")
        actions_layout.addWidget(self.register_selected_button)
        actions_layout.addWidget(self.remove_selected_button)
        actions_layout.addWidget(self.set_default_button)
        actions_layout.addWidget(self.register_all_button)
        actions_layout.addWidget(self.default_apps_button)
        actions_layout.addWidget(self.refresh_button)
        actions_layout.addStretch(1)
        layout.addLayout(actions_layout)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.register_selected_button.clicked.connect(self._register_selected_associations)
        self.remove_selected_button.clicked.connect(self._remove_selected_associations)
        self.set_default_button.clicked.connect(self._set_selected_default)
        self.register_all_button.clicked.connect(self._register_all_associations)
        self.default_apps_button.clicked.connect(self._open_default_apps)
        self.refresh_button.clicked.connect(self._refresh_status)
        self.table.itemSelectionChanged.connect(self._update_action_state)
        self.table.itemDoubleClicked.connect(self._handle_item_double_click)

        self._refresh_status()

    def _refresh_status(self) -> None:
        selected_suffixes = set(self._selected_suffixes())
        status = query_windows_file_association_status()
        if not status.windows_supported:
            self.status_label.setText("This feature is only available on Windows.")
            self.table.setRowCount(0)
            self.table.setEnabled(False)
            self.register_selected_button.setEnabled(False)
            self.remove_selected_button.setEnabled(False)
            self.set_default_button.setEnabled(False)
            self.register_all_button.setEnabled(False)
            self.default_apps_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            return

        registered_count = len(status.registered_suffixes)
        total_count = len(status.supported_suffixes)
        self.status_label.setText(
            f"Image Triage is registered for {registered_count} of {total_count} supported extensions.\n"
            "Set As Default opens the Windows app chooser for the selected extension."
        )
        self._populate_table(selected_suffixes)
        self._update_action_state()

    def _populate_table(self, selected_suffixes: set[str]) -> None:
        states = query_windows_file_association_states()
        self._row_suffixes = [state.suffix for state in states]
        self.table.setRowCount(len(states))
        for row, state in enumerate(states):
            extension_item = QTableWidgetItem(state.suffix)
            extension_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            registered_item = QTableWidgetItem("Yes" if state.registered else "No")
            registered_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            default_item = QTableWidgetItem(describe_windows_default_handler(state))
            default_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if state.is_default:
                font = default_item.font()
                font.setBold(True)
                default_item.setFont(font)
            for item in (extension_item, registered_item, default_item):
                item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(row, 0, extension_item)
            self.table.setItem(row, 1, registered_item)
            self.table.setItem(row, 2, default_item)

        if not states:
            return
        if selected_suffixes:
            for row, suffix in enumerate(self._row_suffixes):
                if suffix in selected_suffixes:
                    self.table.selectRow(row)
        if not self.table.selectionModel().hasSelection():
            self.table.selectRow(0)

    def _selected_rows(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if rows:
            return rows
        current_row = self.table.currentRow()
        if current_row >= 0:
            return [current_row]
        return []

    def _selected_suffixes(self) -> list[str]:
        suffixes: list[str] = []
        for row in self._selected_rows():
            if 0 <= row < len(self._row_suffixes):
                suffixes.append(self._row_suffixes[row])
        return suffixes

    def _selected_suffix(self) -> str | None:
        suffixes = self._selected_suffixes()
        return suffixes[0] if suffixes else None

    def _update_action_state(self) -> None:
        if not self.table.isEnabled():
            return
        selected_suffixes = self._selected_suffixes()
        selected_count = len(selected_suffixes)
        status = query_windows_file_association_status()
        registered = set(status.registered_suffixes)
        self.register_selected_button.setEnabled(bool(selected_suffixes))
        self.remove_selected_button.setEnabled(any(suffix in registered for suffix in selected_suffixes))
        self.set_default_button.setEnabled(selected_count == 1)
        self.register_all_button.setEnabled(len(registered) < len(status.supported_suffixes) or not status.app_registered)

    def _register_selected_associations(self) -> None:
        suffixes = self._selected_suffixes()
        if not suffixes:
            return
        self._run_registry_action(
            lambda: register_windows_file_associations(suffixes),
            f"{APP_FRIENDLY_NAME} is now registered for: {', '.join(suffixes)}",
        )

    def _remove_selected_associations(self) -> None:
        suffixes = self._selected_suffixes()
        if not suffixes:
            return
        self._run_registry_action(
            lambda: remove_windows_file_associations(suffixes),
            f"{APP_FRIENDLY_NAME} registration was removed for: {', '.join(suffixes)}",
        )

    def _register_all_associations(self) -> None:
        self._run_registry_action(
            register_windows_file_associations,
            f"{APP_FRIENDLY_NAME} is now registered for all supported extensions.",
        )

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

    def _set_selected_default(self) -> None:
        suffix = self._selected_suffix()
        if not suffix:
            return
        try:
            open_windows_file_association_chooser(suffix)
        except Exception as exc:
            QMessageBox.warning(self, "File Associations", f"Could not open the Windows chooser.\n\n{exc}")
            return
        self._refresh_status()
        QMessageBox.information(
            self,
            "File Associations",
            f"Windows chooser opened for {suffix}.\n\nChoose {APP_FRIENDLY_NAME} there and enable always use if you want it locked in.",
        )

    def _handle_item_double_click(self, _item: QTableWidgetItem) -> None:
        self._set_selected_default()

    def _open_default_apps(self) -> None:
        try:
            open_windows_default_apps_settings()
        except Exception as exc:
            QMessageBox.warning(self, "File Associations", f"Could not open Windows Default Apps.\n\n{exc}")
