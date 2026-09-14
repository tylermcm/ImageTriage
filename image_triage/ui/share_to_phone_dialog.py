from __future__ import annotations

"""Local Share to Phone composer and lightweight posting queue."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFrame,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..image_resize import ResizeSourceItem
from ..share_phone import (
    PHONE_SHARE_PORT,
    SHARE_PRESETS,
    LocalShareServer,
    PreparedSharePackage,
    SharePackageSpec,
    SharePackageTask,
    phone_share_network_diagnostic,
    qr_png_bytes,
    request_private_firewall_access,
)
from ..share_queue import ShareQueueEntry, ShareQueueStore


class _DialogSignals(QObject):
    transferred = Signal(str)


class ShareToPhoneDialog(QDialog):
    LINK_LIFETIME_MINUTES = 30

    def __init__(
        self,
        sources: tuple[ResizeSourceItem, ...] = (),
        *,
        store: ShareQueueStore | None = None,
        show_queue: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Share to Phone")
        self.setObjectName("sharePhoneDialog")
        self.resize(450, 550)
        self._sources = tuple(sources)
        self._store = store or ShareQueueStore()
        self._task: SharePackageTask | None = None
        self._server: LocalShareServer | None = None
        self._active_entry_id = ""
        self._result_mode = False
        self._signals = _DialogSignals(self)
        self._signals.transferred.connect(self._handle_transferred)
        self._expiry_timer = QTimer(self)
        self._expiry_timer.setSingleShot(True)
        self._expiry_timer.timeout.connect(self._expire_link)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(6)

        self.intro_label = QLabel(
            "Prepare private copies and transfer them directly to a phone on your local network."
        )
        self.intro_label.setWordWrap(True)
        self.intro_label.setObjectName("secondaryText")
        root.addWidget(self.intro_label)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("sharePhoneTabs")
        self.prepare_tab = QWidget(self.tabs)
        self.queue_tab = QWidget(self.tabs)
        self.tabs.addTab(self.prepare_tab, "Prepare")
        self.tabs.addTab(self.queue_tab, "Posting Queue")
        root.addWidget(self.tabs, 1)

        self._build_prepare_tab()
        self._build_queue_tab()

        footer = QHBoxLayout()
        footer.setSpacing(6)
        footer.addStretch(1)
        footer.addWidget(self.prepare_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        root.addLayout(footer)
        self.tabs.currentChanged.connect(self._sync_footer_buttons)

        self._set_sources(self._sources)
        self._refresh_queue()
        if show_queue:
            self.tabs.setCurrentWidget(self.queue_tab)
        self._sync_footer_buttons()

    def _build_prepare_tab(self) -> None:
        layout = QVBoxLayout(self.prepare_tab)
        layout.setContentsMargins(4, 6, 4, 4)
        layout.setSpacing(6)

        self.package_group = QGroupBox("Package")
        form = QFormLayout(self.package_group)
        form.setContentsMargins(9, 10, 9, 8)
        form.setSpacing(6)

        self.selection_label = QLabel()
        form.addRow("Selection", self.selection_label)

        self.name_field = QLineEdit()
        form.addRow("Package Name", self.name_field)

        self.target_field = QLineEdit()
        self.target_field.setPlaceholderText("Optional — social, client, portfolio")
        form.addRow("For", self.target_field)

        self.preset_combo = QComboBox()
        for preset in SHARE_PRESETS:
            self.preset_combo.addItem(preset.name, preset.key)
            self.preset_combo.setItemData(self.preset_combo.count() - 1, preset.description, Qt.ItemDataRole.ToolTipRole)
        form.addRow("Image Size", self.preset_combo)
        layout.addWidget(self.package_group)

        self.caption_group = QGroupBox("Caption")
        caption_layout = QVBoxLayout(self.caption_group)
        caption_layout.setContentsMargins(9, 10, 9, 8)
        self.caption_field = QTextEdit()
        self.caption_field.setPlaceholderText("Caption and hashtags to copy on the phone...")
        self.caption_field.setFixedHeight(74)
        caption_layout.addWidget(self.caption_field)
        self.caption_group.setFixedHeight(112)
        layout.addWidget(self.caption_group)

        progress_row = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("secondaryText")
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.progress_label)
        layout.addLayout(progress_row)

        self.result_group = QFrame()
        self.result_group.setObjectName("phoneShareConnectionCard")
        self.result_group.setMaximumWidth(390)
        result_layout = QVBoxLayout(self.result_group)
        result_layout.setContentsMargins(4, 2, 4, 2)
        result_layout.setSpacing(7)

        self.status_pill = QFrame()
        self.status_pill.setObjectName("phoneShareStatusPill")
        status_layout = QHBoxLayout(self.status_pill)
        status_layout.setContentsMargins(10, 4, 10, 4)
        status_layout.setSpacing(6)
        self.status_dot = QFrame()
        self.status_dot.setObjectName("phoneShareStatusDot")
        self.status_dot.setFixedSize(10, 10)
        self.status_label = QLabel("Status: READY FOR CONNECTION")
        self.status_label.setObjectName("phoneShareStatusLabel")
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_label)
        result_layout.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignHCenter)

        self.qr_frame = QFrame()
        self.qr_frame.setObjectName("phoneShareQrFrame")
        qr_layout = QVBoxLayout(self.qr_frame)
        qr_layout.setContentsMargins(8, 5, 8, 8)
        qr_layout.setSpacing(4)
        scan_label = QLabel("Scan with your phone camera")
        scan_label.setObjectName("phoneShareScanLabel")
        scan_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(scan_label)
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(180, 180)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self.qr_label, 0, Qt.AlignmentFlag.AlignCenter)
        result_layout.addWidget(self.qr_frame, 0, Qt.AlignmentFlag.AlignHCenter)

        divider_row = QHBoxLayout()
        divider_row.setSpacing(8)
        for _ in range(2):
            line = QFrame()
            line.setObjectName("phoneShareDivider")
            line.setFrameShape(QFrame.Shape.HLine)
            divider_row.addWidget(line, 1)
            if divider_row.count() == 1:
                transfer_mark = QLabel("⇄")
                transfer_mark.setObjectName("secondaryText")
                transfer_mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
                divider_row.addWidget(transfer_mark)
        result_layout.addLayout(divider_row)

        ready_label = QLabel("Ready on Your Local Network")
        ready_label.setObjectName("phoneShareReadyLabel")
        result_layout.addWidget(ready_label)
        self.result_status = QLabel()
        self.result_status.setWordWrap(True)
        self.result_status.setObjectName("secondaryText")
        self.result_status.setVisible(False)
        result_layout.addWidget(self.result_status)

        url_row = QHBoxLayout()
        url_row.setSpacing(6)
        self.url_field = QLineEdit()
        self.url_field.setReadOnly(True)
        url_row.addWidget(self.url_field, 1)
        copy_link = QToolButton()
        copy_link.setText("Copy")
        copy_link.setToolTip("Copy link")
        copy_link.setAccessibleName("Copy link")
        copy_link.setFixedSize(48, 34)
        copy_link.clicked.connect(self._copy_link)
        url_row.addWidget(copy_link)
        open_local = QToolButton()
        open_local.setText("PC")
        open_local.setToolTip("Open on this PC")
        open_local.setAccessibleName("Open on this PC")
        open_local.setFixedSize(34, 34)
        open_local.clicked.connect(self._open_local_page)
        url_row.addWidget(open_local)
        result_layout.addLayout(url_row)

        self.network_panel = QFrame()
        self.network_panel.setObjectName("phoneShareNetworkPanel")
        network_panel_layout = QHBoxLayout(self.network_panel)
        network_panel_layout.setContentsMargins(10, 6, 6, 6)
        network_panel_layout.setSpacing(7)
        network_icon = QLabel()
        network_icon.setObjectName("phoneShareNetworkIcon")
        network_icon.setPixmap(self.style().standardIcon(QStyle.StandardPixmap.SP_DriveNetIcon).pixmap(16, 16))
        network_panel_layout.addWidget(network_icon)
        self.network_status = QLabel("Local Network")
        network_panel_layout.addWidget(self.network_status, 1)
        open_settings = QPushButton("Config")
        open_settings.setToolTip("Open Windows network settings")
        open_settings.clicked.connect(self._open_network_settings)
        network_panel_layout.addWidget(open_settings)
        result_layout.addWidget(self.network_panel)

        self.network_warning = QLabel()
        self.network_warning.setWordWrap(True)
        self.network_warning.setObjectName("secondaryText")
        self.network_warning.setVisible(False)
        result_layout.addWidget(self.network_warning)
        self.network_buttons = QWidget()
        network_button_layout = QHBoxLayout(self.network_buttons)
        network_button_layout.setContentsMargins(0, 0, 0, 0)
        network_button_layout.setSpacing(6)
        self.allow_private_button = QPushButton("Allow on Private Networks")
        self.allow_private_button.clicked.connect(self._allow_private_network)
        network_button_layout.addStretch(1)
        network_button_layout.addWidget(self.allow_private_button)
        network_button_layout.addStretch(1)
        self.network_buttons.setVisible(False)
        result_layout.addWidget(self.network_buttons)

        expiry_label = QLabel(f"Link expires in {self.LINK_LIFETIME_MINUTES} minutes or when this window closes.")
        expiry_label.setObjectName("phoneShareExpiryLabel")
        expiry_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        result_layout.addWidget(expiry_label)
        self.result_group.setVisible(False)
        layout.addWidget(self.result_group, 1, Qt.AlignmentFlag.AlignHCenter)

        self.prepare_button = QPushButton("Share")
        self.prepare_button.setObjectName("sharePhonePrimaryButton")
        self.prepare_button.setDefault(True)
        self.prepare_button.clicked.connect(self._prepare)

    def _build_queue_tab(self) -> None:
        layout = QVBoxLayout(self.queue_tab)
        layout.setContentsMargins(8, 10, 8, 8)
        layout.setSpacing(8)
        explanation = QLabel(
            "Transferred means the phone downloaded the package. Posted is always a manual confirmation because no social account is connected."
        )
        explanation.setWordWrap(True)
        explanation.setObjectName("secondaryText")
        layout.addWidget(explanation)

        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setHorizontalHeaderLabels(("Package", "For", "Status", "Created"))
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.queue_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.queue_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.itemSelectionChanged.connect(self._refresh_queue_buttons)
        layout.addWidget(self.queue_table, 1)

        queue_actions = QVBoxLayout()
        queue_actions.setSpacing(4)
        primary_actions = QHBoxLayout()
        primary_actions.setSpacing(6)
        self.share_again_button = QPushButton("Share Again")
        self.share_again_button.clicked.connect(self._share_again)
        self.posted_button = QPushButton("Mark Posted")
        self.posted_button.clicked.connect(lambda: self._set_selected_status("posted"))
        self.archive_button = QPushButton("Archive")
        self.archive_button.clicked.connect(lambda: self._set_selected_status("archived"))
        self.delete_button = QPushButton("Delete")
        self.delete_button.clicked.connect(self._delete_selected_entry)
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self._refresh_queue)
        primary_actions.addWidget(self.share_again_button)
        primary_actions.addWidget(self.posted_button)
        primary_actions.addStretch(1)
        secondary_actions = QHBoxLayout()
        secondary_actions.setSpacing(6)
        secondary_actions.addStretch(1)
        secondary_actions.addWidget(self.archive_button)
        secondary_actions.addWidget(self.delete_button)
        secondary_actions.addWidget(refresh_button)
        queue_actions.addLayout(primary_actions)
        queue_actions.addLayout(secondary_actions)
        layout.addLayout(queue_actions)

    def _sync_footer_buttons(self, _index: int = -1) -> None:
        self.prepare_button.setVisible(self.tabs.currentWidget() is self.prepare_tab)

    def _set_sources(self, sources: tuple[ResizeSourceItem, ...]) -> None:
        self._sources = tuple(sources)
        count = len(self._sources)
        self.selection_label.setText(f"{count} image{'s' if count != 1 else ''}")
        self.prepare_button.setEnabled(count > 0 and self._task is None)
        if not self.name_field.text().strip():
            self.name_field.setText(_default_package_name(self._sources))

    def _spec(self) -> SharePackageSpec:
        return SharePackageSpec(
            name=self.name_field.text(),
            target=self.target_field.text(),
            account="",
            caption=self.caption_field.toPlainText(),
            preset_key=str(self.preset_combo.currentData() or SHARE_PRESETS[0].key),
            sources=self._sources,
            alt_text=tuple("" for _ in self._sources),
        )

    def _prepare(self) -> None:
        if self._result_mode:
            self._reset_prepare_view()
            return
        if self._task is not None or not self._sources:
            return
        missing = [source.source_name for source in self._sources if not Path(source.source_path).is_file()]
        if missing:
            QMessageBox.warning(self, "Missing Images", f"These source images could not be found:\n\n" + "\n".join(missing[:8]))
            return
        self._stop_server()
        self.result_group.setVisible(False)
        self.prepare_button.setEnabled(False)
        self.close_button.setEnabled(False)
        self.progress_bar.setRange(0, len(self._sources))
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.progress_label.setText("Preparing...")
        task = SharePackageTask(self._spec())
        task.signals.started.connect(lambda total: self.progress_bar.setRange(0, max(1, total)), Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self._handle_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self._handle_prepared, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self._handle_failed, Qt.ConnectionType.QueuedConnection)
        self._task = task
        QThreadPool.globalInstance().start(task)

    def _handle_progress(self, current: int, total: int, message: str) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(current)
        self.progress_label.setText(message)

    def _handle_prepared(self, result: object) -> None:
        self._task = None
        self.prepare_button.setEnabled(bool(self._sources))
        self.close_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        package = result if isinstance(result, PreparedSharePackage) else None
        if package is None:
            self._handle_failed("The prepared package result was invalid.")
            return
        entry = self._store.create_ready(
            name=package.name,
            target=package.target,
            account=package.account,
            caption=package.caption,
            preset_key=package.preset_key,
            source_paths=package.source_paths,
            output_paths=package.output_paths,
            alt_text=package.alt_text,
            package_dir=package.package_dir,
        )
        self._active_entry_id = entry.id
        self._server = LocalShareServer(package, on_transfer=lambda: self._mark_transferred_from_server(entry.id))
        try:
            url = self._server.start()
            qr_bytes = qr_png_bytes(url)
        except Exception as exc:
            self._stop_server()
            self._store.set_status(entry.id, "failed")
            QMessageBox.warning(self, "Phone Share Failed", f"The files were prepared, but the local sharing link could not start.\n\n{exc}")
            self._refresh_queue()
            return
        pixmap = QPixmap()
        pixmap.loadFromData(qr_bytes, "PNG")
        self.qr_label.setPixmap(pixmap.scaled(self.qr_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.url_field.setText(url)
        self.status_label.setText("Status: READY FOR CONNECTION")
        self.result_status.clear()
        self.result_status.setVisible(False)
        self._refresh_network_diagnostic()
        self._show_result_mode()
        self._expiry_timer.start(self.LINK_LIFETIME_MINUTES * 60 * 1000)
        self._refresh_queue()

    def _handle_failed(self, message: str) -> None:
        self._task = None
        self.prepare_button.setEnabled(bool(self._sources))
        self.close_button.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.progress_label.setText("Preparation failed")
        QMessageBox.warning(self, "Share Preparation Failed", message)

    def _mark_transferred_from_server(self, entry_id: str) -> None:
        self._store.mark_transferred(entry_id)
        self._signals.transferred.emit(entry_id)

    def _handle_transferred(self, entry_id: str) -> None:
        if entry_id == self._active_entry_id:
            self.status_label.setText("Status: DOWNLOAD COMPLETE")
            self.result_status.setText("Downloaded by a device. The link remains available until it expires.")
            self.result_status.setVisible(True)
        self._refresh_queue()

    def _expire_link(self) -> None:
        self._stop_server()
        self.status_label.setText("Status: LINK EXPIRED")
        self.result_status.setText("This local sharing link has expired. Use Share Again to create a new one.")
        self.result_status.setVisible(True)
        self.url_field.clear()

    def _show_result_mode(self) -> None:
        self._result_mode = True
        self.intro_label.setVisible(False)
        self.tabs.tabBar().setVisible(False)
        self.package_group.setVisible(False)
        self.caption_group.setVisible(False)
        self.result_group.setVisible(True)
        self.prepare_button.setText("Share Another")
        self.resize(450, 550)

    def _reset_prepare_view(self) -> None:
        self._result_mode = False
        self._stop_server()
        self._active_entry_id = ""
        self.intro_label.setVisible(True)
        self.tabs.tabBar().setVisible(True)
        self.result_group.setVisible(False)
        self.package_group.setVisible(True)
        self.caption_group.setVisible(True)
        self.prepare_button.setText("Share")
        self.progress_label.clear()
        self.progress_label.setVisible(True)
        self.resize(450, 550)

    def _stop_server(self) -> None:
        self._expiry_timer.stop()
        server = self._server
        self._server = None
        if server is not None:
            server.stop()

    def _copy_link(self) -> None:
        if self.url_field.text():
            QApplication.clipboard().setText(self.url_field.text())

    def _open_local_page(self) -> None:
        if self._server is not None and self._server.loopback_url:
            QDesktopServices.openUrl(QUrl(self._server.loopback_url))

    def _refresh_network_diagnostic(self) -> None:
        diagnostic = phone_share_network_diagnostic()
        if diagnostic.profile != "unknown":
            profile_label = diagnostic.profile.title()
            self.network_status.setText(f"Local Network: {profile_label} Active")
        else:
            self.network_status.setText("Local Network: Status Unknown")
        warning = diagnostic.warning
        self.network_warning.setText(warning)
        self.network_warning.setVisible(bool(warning))
        self.network_buttons.setVisible(bool(warning) and not diagnostic.private_rule_present)
        self.allow_private_button.setEnabled(not diagnostic.private_rule_present)
        self.allow_private_button.setText(
            "Allowed on Private Networks" if diagnostic.private_rule_present else "Allow on Private Networks"
        )

    def _open_network_settings(self) -> None:
        QDesktopServices.openUrl(QUrl("ms-settings:network-status"))

    def _allow_private_network(self) -> None:
        if not request_private_firewall_access():
            QMessageBox.warning(
                self,
                "Firewall Access",
                f"Windows did not start the firewall permission request. You can allow TCP port {PHONE_SHARE_PORT} for Private networks in Windows Defender Firewall.",
            )
            return
        self.allow_private_button.setEnabled(False)
        QMessageBox.information(
            self,
            "Firewall Access Requested",
            f"Approve the Windows prompt. The rule allows only TCP port {PHONE_SHARE_PORT}, only from the local subnet, and only on Private networks.\n\nIf this connection is currently Public, change your trusted home network to Private first.",
        )
        QTimer.singleShot(1500, self._refresh_network_diagnostic)

    def _refresh_queue(self) -> None:
        entries = self._store.list_entries()
        selected_id = self._selected_entry_id()
        self.queue_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.name)
            name_item.setData(Qt.ItemDataRole.UserRole, entry.id)
            target = (entry.target or "—") + (f" · {entry.account}" if entry.account else "")
            values = (name_item, QTableWidgetItem(target), QTableWidgetItem(entry.status.title()), QTableWidgetItem(_display_date(entry.created_at)))
            for column, item in enumerate(values):
                self.queue_table.setItem(row, column, item)
            if entry.id == selected_id:
                self.queue_table.selectRow(row)
        self._refresh_queue_buttons()

    def _selected_entry_id(self) -> str:
        row = self.queue_table.currentRow()
        if row < 0:
            return ""
        item = self.queue_table.item(row, 0)
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def _selected_entry(self) -> ShareQueueEntry | None:
        entry_id = self._selected_entry_id()
        return self._store.get(entry_id) if entry_id else None

    def _refresh_queue_buttons(self) -> None:
        entry = self._selected_entry()
        enabled = entry is not None
        self.share_again_button.setEnabled(enabled and bool(entry.source_paths) if entry is not None else False)
        self.posted_button.setEnabled(enabled and entry.status != "posted" if entry is not None else False)
        self.archive_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    def _share_again(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        self._reset_prepare_view()
        sources = tuple(ResizeSourceItem(path, Path(path).name) for path in entry.source_paths)
        self.name_field.setText(entry.name)
        self.target_field.setText(entry.target)
        self.caption_field.setPlainText(entry.caption)
        preset_index = self.preset_combo.findData(entry.preset_key)
        if preset_index >= 0:
            self.preset_combo.setCurrentIndex(preset_index)
        self._set_sources(sources)
        self.tabs.setCurrentWidget(self.prepare_tab)

    def _set_selected_status(self, status: str) -> None:
        entry_id = self._selected_entry_id()
        if entry_id:
            self._store.set_status(entry_id, status)
            self._refresh_queue()

    def _delete_selected_entry(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        answer = QMessageBox.question(
            self,
            "Delete Share Package?",
            f"Delete {entry.name} and its prepared copies?\n\nThe original images will not be touched.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        if entry.id == self._active_entry_id:
            self._stop_server()
            self.result_group.setVisible(False)
            self._active_entry_id = ""
        self._store.delete(entry.id, remove_files=True)
        self._refresh_queue()

    def reject(self) -> None:
        if self._task is not None:
            return
        self._stop_server()
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._task is not None:
            event.ignore()
            return
        self._stop_server()
        super().closeEvent(event)


def _default_package_name(sources: tuple[ResizeSourceItem, ...] = ()) -> str:
    date = datetime.now().strftime("%Y-%m-%d")
    folders = {Path(source.source_path).parent for source in sources}
    if len(folders) == 1:
        folder = next(iter(folders)).name.strip() or "Phone Share"
    elif folders:
        folder = "Mixed Folders"
    else:
        folder = "Phone Share"
    return f"{folder} - {date}"


def _display_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%b %d, %Y %I:%M %p")
    except (TypeError, ValueError):
        return value
