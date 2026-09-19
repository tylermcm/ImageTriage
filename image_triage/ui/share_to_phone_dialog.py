from __future__ import annotations

"""Local Share to Phone composer and lightweight posting queue."""

import math
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
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
    share_preset_for_key,
)
from ..share_queue import ShareQueueEntry, ShareQueueStore
from .collection_dialog import _PurposeComboBox


_ICON_FONT_FAMILIES = ["Segoe Fluent Icons", "Segoe MDL2 Assets"]
_GLYPH_PHOTO = 0xE91B
_GLYPH_PACKAGE = 0xE7B8
_GLYPH_WIFI = 0xE701
_GLYPH_WARNING = 0xE7BA
_GLYPH_REFRESH = 0xE72C

_PREPARE_SUBTITLE = "Send resized copies to your phone over Wi-Fi. Nothing is uploaded."
_QUEUE_SUBTITLE = "Packages you've sent to a phone. Mark them posted once they're live."
_RESULT_SUBTITLE = "Your phone needs to be on the same Wi-Fi network as this PC."


class _DialogSignals(QObject):
    transferred = Signal(str)


class ShareToPhoneDialog(QDialog):
    LINK_LIFETIME_MINUTES = 30
    PREPARE_SIZE = (460, 500)
    QR_SIZE = 200

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
        self.setMinimumWidth(420)
        self.resize(*self.PREPARE_SIZE)
        self._sources = tuple(sources)
        self._store = store or ShareQueueStore()
        self._task: SharePackageTask | None = None
        self._server: LocalShareServer | None = None
        self._active_entry_id = ""
        self._result_mode = False
        self._link_deadline = 0.0
        self._signals = _DialogSignals(self)
        self._signals.transferred.connect(self._handle_transferred)
        self._expiry_timer = QTimer(self)
        self._expiry_timer.setSingleShot(True)
        self._expiry_timer.timeout.connect(self._expire_link)
        self._countdown_timer = QTimer(self)
        self._countdown_timer.setInterval(15_000)
        self._countdown_timer.timeout.connect(self._update_expiry_note)
        self._copy_feedback_timer = QTimer(self)
        self._copy_feedback_timer.setSingleShot(True)
        self._copy_feedback_timer.timeout.connect(lambda: self.copy_link_button.setText("Copy"))

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 16)
        root.setSpacing(0)

        self.title_label = QLabel("Share to Phone", self)
        self.title_label.setObjectName("sharePhoneTitle")
        root.addWidget(self.title_label)
        root.addSpacing(3)
        self.intro_label = QLabel(_PREPARE_SUBTITLE, self)
        self.intro_label.setWordWrap(True)
        self.intro_label.setObjectName("sharePhoneSubtitle")
        root.addWidget(self.intro_label)
        root.addSpacing(12)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("sharePhoneTabs")
        self.tabs.tabBar().setDrawBase(False)
        self.tabs.tabBar().setExpanding(False)
        self.prepare_tab = QWidget(self.tabs)
        self.queue_tab = QWidget(self.tabs)
        self.tabs.addTab(self.prepare_tab, "Prepare")
        self.tabs.addTab(self.queue_tab, "Posting Queue")
        root.addWidget(self.tabs, 1)

        self._build_prepare_tab()
        self._build_queue_tab()

        footer = QWidget(self)
        self._footer_layout = QHBoxLayout(footer)
        self._footer_layout.setContentsMargins(0, 14, 0, 0)
        self._footer_layout.setSpacing(8)
        self.footer_note = QLabel("", footer)
        self.footer_note.setObjectName("sharePhoneFooterNote")
        self.footer_note.setToolTip("The link also stops working when this window closes.")
        self._footer_layout.addWidget(self.footer_note, 1)
        self.close_button = QPushButton("Close", footer)
        self.close_button.setObjectName("sharePhoneCancelButton")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        root.addWidget(footer)
        self._arrange_footer()
        self.tabs.currentChanged.connect(self._sync_footer_buttons)

        self._set_sources(self._sources)
        self._sync_preset_hint()
        self._refresh_queue()
        if show_queue:
            self.tabs.setCurrentWidget(self.queue_tab)
        self._sync_footer_buttons()

    # ------------------------------------------------------------------ build

    def _build_prepare_tab(self) -> None:
        layout = QVBoxLayout(self.prepare_tab)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(0)

        self.package_group = QWidget(self.prepare_tab)
        package_layout = QVBoxLayout(self.package_group)
        package_layout.setContentsMargins(0, 0, 0, 0)
        package_layout.setSpacing(0)

        summary = QFrame(self.package_group)
        summary.setObjectName("sharePhoneSelectionCard")
        summary_layout = QHBoxLayout(summary)
        summary_layout.setContentsMargins(10, 9, 12, 9)
        summary_layout.setSpacing(10)
        selection_icon = _glyph_label(_GLYPH_PHOTO, 17, "sharePhoneSelectionIcon")
        selection_icon.setFixedSize(34, 34)
        summary_layout.addWidget(selection_icon)
        summary_text = QVBoxLayout()
        summary_text.setContentsMargins(0, 0, 0, 0)
        summary_text.setSpacing(0)
        self.selection_label = QLabel()
        self.selection_label.setObjectName("sharePhoneSelectionCount")
        self.source_label = QLabel()
        self.source_label.setObjectName("sharePhoneSelectionSource")
        summary_text.addWidget(self.selection_label)
        summary_text.addWidget(self.source_label)
        summary_layout.addLayout(summary_text, 1)
        package_layout.addWidget(summary)
        package_layout.addSpacing(16)

        package_layout.addWidget(_field_label("Package name"))
        package_layout.addSpacing(5)
        self.name_field = QLineEdit()
        self.name_field.setObjectName("sharePhoneField")
        package_layout.addWidget(self.name_field)
        package_layout.addSpacing(12)

        details_row = QHBoxLayout()
        details_row.setContentsMargins(0, 0, 0, 0)
        details_row.setSpacing(12)
        target_column = QVBoxLayout()
        target_column.setSpacing(5)
        target_column.addWidget(_field_label("For"))
        self.target_field = QLineEdit()
        self.target_field.setObjectName("sharePhoneField")
        self.target_field.setPlaceholderText("Optional — social, client, portfolio")
        target_column.addWidget(self.target_field)
        size_column = QVBoxLayout()
        size_column.setSpacing(5)
        size_column.addWidget(_field_label("Image size"))
        self.preset_combo = _PurposeComboBox()
        self.preset_combo.setObjectName("sharePhoneSizeCombo")
        for preset in SHARE_PRESETS:
            self.preset_combo.addItem(preset.name, preset.key)
            self.preset_combo.setItemData(self.preset_combo.count() - 1, preset.description, Qt.ItemDataRole.ToolTipRole)
        self.preset_combo.currentIndexChanged.connect(self._sync_preset_hint)
        size_column.addWidget(self.preset_combo)
        details_row.addLayout(target_column, 3)
        details_row.addLayout(size_column, 2)
        package_layout.addLayout(details_row)
        package_layout.addSpacing(5)
        self.preset_hint = QLabel()
        self.preset_hint.setObjectName("sharePhoneHint")
        self.preset_hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        package_layout.addWidget(self.preset_hint)
        layout.addWidget(self.package_group)
        layout.addSpacing(12)

        self.caption_group = QWidget(self.prepare_tab)
        caption_layout = QVBoxLayout(self.caption_group)
        caption_layout.setContentsMargins(0, 0, 0, 0)
        caption_layout.setSpacing(5)
        caption_header = QHBoxLayout()
        caption_header.setContentsMargins(0, 0, 0, 0)
        caption_header.addWidget(_field_label("Caption"))
        caption_header.addStretch(1)
        caption_hint = QLabel("Copy it from the phone page")
        caption_hint.setObjectName("sharePhoneHint")
        caption_header.addWidget(caption_hint)
        caption_layout.addLayout(caption_header)
        self.caption_field = QTextEdit()
        self.caption_field.setObjectName("sharePhoneCaptionField")
        self.caption_field.setAcceptRichText(False)
        self.caption_field.setPlaceholderText("Caption and hashtags to copy on the phone...")
        self.caption_field.setFixedHeight(80)
        caption_layout.addWidget(self.caption_field)
        layout.addWidget(self.caption_group)

        self.progress_row = QWidget(self.prepare_tab)
        progress_row = QHBoxLayout(self.progress_row)
        progress_row.setContentsMargins(0, 12, 0, 0)
        progress_row.setSpacing(10)
        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("sharePhoneProgress")
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setVisible(False)
        self.progress_label = QLabel("")
        self.progress_label.setObjectName("sharePhoneHint")
        progress_row.addWidget(self.progress_bar, 1)
        progress_row.addWidget(self.progress_label)
        layout.addWidget(self.progress_row)

        self._build_result_card()
        layout.addWidget(self.result_group)
        layout.addStretch(1)

        self.prepare_button = QPushButton("Share")
        self.prepare_button.setObjectName("sharePhonePrimaryButton")
        self.prepare_button.setDefault(True)
        self.prepare_button.clicked.connect(self._prepare)

    def _build_result_card(self) -> None:
        self.result_group = QFrame()
        self.result_group.setObjectName("phoneShareConnectionCard")
        result_layout = QVBoxLayout(self.result_group)
        result_layout.setContentsMargins(0, 0, 0, 0)
        result_layout.setSpacing(0)

        self.status_pill = QFrame()
        self.status_pill.setObjectName("phoneShareStatusPill")
        status_layout = QHBoxLayout(self.status_pill)
        status_layout.setContentsMargins(10, 4, 12, 4)
        status_layout.setSpacing(7)
        self.status_dot = QFrame()
        self.status_dot.setObjectName("phoneShareStatusDot")
        self.status_dot.setFixedSize(8, 8)
        self.status_label = QLabel()
        self.status_label.setObjectName("phoneShareStatusLabel")
        status_layout.addWidget(self.status_dot)
        status_layout.addWidget(self.status_label)
        result_layout.addWidget(self.status_pill, 0, Qt.AlignmentFlag.AlignHCenter)
        result_layout.addSpacing(14)

        self.qr_frame = QFrame()
        self.qr_frame.setObjectName("phoneShareQrFrame")
        qr_layout = QVBoxLayout(self.qr_frame)
        qr_layout.setContentsMargins(12, 12, 12, 12)
        self.qr_label = QLabel()
        self.qr_label.setFixedSize(self.QR_SIZE, self.QR_SIZE)
        self.qr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        qr_layout.addWidget(self.qr_label)
        result_layout.addWidget(self.qr_frame, 0, Qt.AlignmentFlag.AlignHCenter)
        result_layout.addSpacing(10)

        self.result_status = QLabel()
        self.result_status.setWordWrap(True)
        self.result_status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.result_status.setObjectName("phoneShareResultStatus")
        self.result_status.setVisible(False)
        result_layout.addWidget(self.result_status)
        result_layout.addSpacing(14)

        result_layout.addWidget(_field_label("Or open this link on the phone"))
        result_layout.addSpacing(5)
        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.url_field = QLineEdit()
        self.url_field.setObjectName("phoneShareUrlField")
        self.url_field.setReadOnly(True)
        url_row.addWidget(self.url_field, 1)
        self.copy_link_button = QPushButton("Copy")
        self.copy_link_button.setObjectName("phoneShareInlineButton")
        self.copy_link_button.setAccessibleName("Copy link")
        self.copy_link_button.setAutoDefault(False)
        self.copy_link_button.clicked.connect(self._copy_link)
        url_row.addWidget(self.copy_link_button)
        self.open_local_button = QPushButton("Open on PC")
        self.open_local_button.setObjectName("phoneShareInlineButton")
        self.open_local_button.setToolTip("Preview the phone page in this PC's browser")
        self.open_local_button.setAutoDefault(False)
        self.open_local_button.clicked.connect(self._open_local_page)
        url_row.addWidget(self.open_local_button)
        result_layout.addLayout(url_row)
        result_layout.addSpacing(12)

        self.network_panel = QFrame()
        self.network_panel.setObjectName("phoneShareNetworkPanel")
        network_layout = QHBoxLayout(self.network_panel)
        network_layout.setContentsMargins(12, 10, 8, 10)
        network_layout.setSpacing(10)
        self.network_icon = _glyph_label(_GLYPH_WIFI, 16, "phoneShareNetworkIcon")
        self.network_icon.setFixedSize(20, 20)
        network_layout.addWidget(self.network_icon, 0, Qt.AlignmentFlag.AlignTop)

        network_text = QVBoxLayout()
        network_text.setContentsMargins(0, 1, 0, 0)
        network_text.setSpacing(2)
        self.network_status = QLabel("Local network")
        self.network_status.setObjectName("phoneShareNetworkTitle")
        network_text.addWidget(self.network_status)
        self.network_detail = QLabel()
        self.network_detail.setObjectName("sharePhoneHint")
        self.network_detail.setWordWrap(True)
        network_text.addWidget(self.network_detail)
        self.network_warning = QLabel()
        self.network_warning.setWordWrap(True)
        self.network_warning.setObjectName("phoneShareNetworkWarning")
        self.network_warning.setVisible(False)
        network_text.addWidget(self.network_warning)
        self.network_buttons = QWidget()
        network_button_layout = QHBoxLayout(self.network_buttons)
        network_button_layout.setContentsMargins(0, 6, 0, 0)
        network_button_layout.setSpacing(6)
        self.allow_private_button = QPushButton("Allow on Private Networks")
        self.allow_private_button.setObjectName("phoneShareInlineButton")
        self.allow_private_button.setAutoDefault(False)
        self.allow_private_button.clicked.connect(self._allow_private_network)
        network_button_layout.addWidget(self.allow_private_button)
        network_button_layout.addStretch(1)
        self.network_buttons.setVisible(False)
        network_text.addWidget(self.network_buttons)
        network_layout.addLayout(network_text, 1)

        open_settings = QPushButton("Network settings")
        open_settings.setObjectName("phoneShareLinkButton")
        open_settings.setToolTip("Open Windows network settings")
        open_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        open_settings.setAutoDefault(False)
        open_settings.clicked.connect(self._open_network_settings)
        network_layout.addWidget(open_settings, 0, Qt.AlignmentFlag.AlignTop)
        result_layout.addWidget(self.network_panel)
        result_layout.addStretch(1)

        self.result_group.setVisible(False)
        self._set_link_state("waiting", "Waiting for your phone")

    def _build_queue_tab(self) -> None:
        layout = QVBoxLayout(self.queue_tab)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        explanation = QLabel("Transferred means the phone downloaded it. Posting is marked by hand.")
        explanation.setWordWrap(True)
        explanation.setObjectName("sharePhoneHint")
        explanation.setToolTip("No social account is connected, so Image Triage can't see when something is posted.")
        header.addWidget(explanation, 1)
        refresh_button = QToolButton()
        refresh_button.setObjectName("sharePhoneIconButton")
        refresh_button.setText(chr(_GLYPH_REFRESH))
        refresh_button.setFont(_icon_font(14))
        refresh_button.setToolTip("Refresh")
        refresh_button.setAccessibleName("Refresh")
        refresh_button.setFixedSize(30, 30)
        refresh_button.clicked.connect(self._refresh_queue)
        header.addWidget(refresh_button)
        layout.addLayout(header)
        layout.addSpacing(8)

        self.queue_stack = QStackedWidget()
        self.queue_table = QTableWidget(0, 4)
        self.queue_table.setObjectName("sharePhoneQueueTable")
        self.queue_table.setHorizontalHeaderLabels(("Package", "For", "Status", "Created"))
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.verticalHeader().setDefaultSectionSize(40)
        self.queue_table.setShowGrid(False)
        self.queue_table.setWordWrap(False)
        self.queue_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.queue_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.queue_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header_view = self.queue_table.horizontalHeader()
        header_view.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        header_view.setHighlightSections(False)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header_view.resizeSection(2, 108)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.queue_table.itemSelectionChanged.connect(self._refresh_queue_buttons)
        self.queue_stack.addWidget(self.queue_table)

        self.queue_empty = QFrame()
        self.queue_empty.setObjectName("sharePhoneEmptyState")
        empty_layout = QVBoxLayout(self.queue_empty)
        empty_layout.setContentsMargins(24, 24, 24, 24)
        empty_layout.setSpacing(6)
        empty_layout.addStretch(1)
        empty_icon = _glyph_label(_GLYPH_PACKAGE, 28, "sharePhoneEmptyIcon")
        empty_layout.addWidget(empty_icon)
        empty_title = QLabel("Nothing shared yet")
        empty_title.setObjectName("sharePhoneEmptyTitle")
        empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(empty_title)
        empty_body = QLabel("Packages you send to your phone will show up here.")
        empty_body.setObjectName("sharePhoneHint")
        empty_body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_body.setWordWrap(True)
        empty_layout.addWidget(empty_body)
        empty_layout.addStretch(1)
        self.queue_stack.addWidget(self.queue_empty)
        layout.addWidget(self.queue_stack, 1)
        layout.addSpacing(12)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        self.share_again_button = QPushButton("Share Again")
        self.share_again_button.setObjectName("sharePhoneSecondaryButton")
        self.share_again_button.clicked.connect(self._share_again)
        self.posted_button = QPushButton("Mark Posted")
        self.posted_button.setObjectName("sharePhoneSecondaryButton")
        self.posted_button.clicked.connect(lambda: self._set_selected_status("posted"))
        self.archive_button = QPushButton("Archive")
        self.archive_button.setObjectName("sharePhoneGhostButton")
        self.archive_button.clicked.connect(lambda: self._set_selected_status("archived"))
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("sharePhoneDangerButton")
        self.delete_button.clicked.connect(self._delete_selected_entry)
        for button in (self.share_again_button, self.posted_button, self.archive_button, self.delete_button):
            button.setAutoDefault(False)
        actions.addWidget(self.share_again_button)
        actions.addWidget(self.posted_button)
        actions.addStretch(1)
        actions.addWidget(self.archive_button)
        actions.addWidget(self.delete_button)
        layout.addLayout(actions)

    # ------------------------------------------------------------ view state

    def _arrange_footer(self) -> None:
        layout = self._footer_layout
        layout.removeWidget(self.close_button)
        layout.removeWidget(self.prepare_button)
        # After a share, waiting is the expected outcome, so "Done" takes the
        # primary slot and "Share Another" steps back.
        if self._result_mode:
            layout.addWidget(self.prepare_button)
            layout.addWidget(self.close_button)
        else:
            layout.addWidget(self.close_button)
            layout.addWidget(self.prepare_button)
        self.close_button.setText("Done" if self._result_mode else "Close")
        self.prepare_button.setDefault(not self._result_mode)
        self.close_button.setDefault(self._result_mode)
        _set_style_property(self.prepare_button, "quiet", self._result_mode)
        _set_style_property(self.close_button, "emphasis", self._result_mode)

    def _sync_footer_buttons(self, _index: int = -1) -> None:
        on_prepare = self.tabs.currentWidget() is self.prepare_tab
        self.prepare_button.setVisible(on_prepare)
        if not self._result_mode:
            self.intro_label.setText(_PREPARE_SUBTITLE if on_prepare else _QUEUE_SUBTITLE)

    def _set_sources(self, sources: tuple[ResizeSourceItem, ...]) -> None:
        self._sources = tuple(sources)
        count = len(self._sources)
        self.selection_label.setText(f"{count} image{'s' if count != 1 else ''}")
        folders = {Path(source.source_path).parent for source in self._sources}
        if len(folders) == 1:
            folder = next(iter(folders))
            source_text = f"From {folder.name or folder}"
            self.source_label.setToolTip(str(folder))
        elif folders:
            source_text = f"From {len(folders)} folders"
            self.source_label.setToolTip("\n".join(sorted(str(folder) for folder in folders)[:12]))
        else:
            source_text = "Select images in the grid to share them"
            self.source_label.setToolTip("")
        self.source_label.setText(self.source_label.fontMetrics().elidedText(source_text, Qt.TextElideMode.ElideMiddle, 320))
        self.prepare_button.setEnabled(count > 0 and self._task is None)
        if not self.name_field.text().strip():
            self.name_field.setText(_default_package_name(self._sources))

    def _sync_preset_hint(self, _index: int = -1) -> None:
        preset = share_preset_for_key(str(self.preset_combo.currentData() or ""))
        self.preset_hint.setText(preset.description)

    def _set_link_state(self, state: str, text: str) -> None:
        self.status_label.setText(text)
        _set_style_property(self.status_pill, "state", state)

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

    # ---------------------------------------------------------------- sharing

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
        self.progress_label.clear()
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
        self.qr_label.setGraphicsEffect(None)
        self.qr_label.setPixmap(pixmap.scaled(self.qr_label.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.url_field.setText(url)
        self.url_field.setCursorPosition(0)
        self._set_link_state("waiting", "Waiting for your phone")
        self.result_status.clear()
        self.result_status.setVisible(False)
        self._refresh_network_diagnostic()
        self._show_result_mode()
        self._start_link_timers()
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
            self._set_link_state("done", "Downloaded to a device")
            self.result_status.setText("The photos are on the phone. The link stays open until it expires.")
            self.result_status.setVisible(True)
        self._refresh_queue()

    def _start_link_timers(self) -> None:
        self._link_deadline = time.monotonic() + self.LINK_LIFETIME_MINUTES * 60
        self._expiry_timer.start(self.LINK_LIFETIME_MINUTES * 60 * 1000)
        self._countdown_timer.start()
        self._update_expiry_note()

    def _update_expiry_note(self) -> None:
        if not self._result_mode or self._server is None:
            return
        remaining = max(0.0, self._link_deadline - time.monotonic())
        self.footer_note.setText(f"Link expires in {max(1, math.ceil(remaining / 60))} min")

    def _expire_link(self) -> None:
        self._stop_server()
        self._set_link_state("expired", "Link expired")
        self.result_status.setText("This link has expired. Choose Share Another to make a new one.")
        self.result_status.setVisible(True)
        self.url_field.clear()
        self.footer_note.setText("Link expired")
        faded = QGraphicsOpacityEffect(self.qr_label)
        faded.setOpacity(0.18)
        self.qr_label.setGraphicsEffect(faded)

    def _show_result_mode(self) -> None:
        self._result_mode = True
        self.title_label.setText("Scan with your phone")
        self.intro_label.setText(_RESULT_SUBTITLE)
        self.tabs.tabBar().setVisible(False)
        _set_style_property(self.tabs, "resultMode", True)
        self.package_group.setVisible(False)
        self.caption_group.setVisible(False)
        self.progress_row.setVisible(False)
        self.result_group.setVisible(True)
        self.prepare_button.setText("Share Another")
        self._arrange_footer()
        self.resize(self.width(), self.minimumSizeHint().height())

    def _reset_prepare_view(self) -> None:
        self._result_mode = False
        self._stop_server()
        self._active_entry_id = ""
        self.title_label.setText("Share to Phone")
        self.tabs.tabBar().setVisible(True)
        _set_style_property(self.tabs, "resultMode", False)
        self.result_group.setVisible(False)
        self.package_group.setVisible(True)
        self.caption_group.setVisible(True)
        self.progress_row.setVisible(True)
        self.prepare_button.setText("Share")
        self.progress_label.clear()
        self.progress_label.setVisible(True)
        self.footer_note.clear()
        self._arrange_footer()
        self._sync_footer_buttons()
        self.resize(self.width(), self.PREPARE_SIZE[1])

    def _stop_server(self) -> None:
        self._expiry_timer.stop()
        self._countdown_timer.stop()
        server = self._server
        self._server = None
        if server is not None:
            server.stop()

    def _copy_link(self) -> None:
        if self.url_field.text():
            QApplication.clipboard().setText(self.url_field.text())
            self.copy_link_button.setText("Copied")
            self._copy_feedback_timer.start(1500)

    def _open_local_page(self) -> None:
        if self._server is not None and self._server.loopback_url:
            QDesktopServices.openUrl(QUrl(self._server.loopback_url))

    def _refresh_network_diagnostic(self) -> None:
        diagnostic = phone_share_network_diagnostic()
        profile = diagnostic.profile
        if profile != "unknown":
            self.network_status.setText(f"{profile.title()} network")
        else:
            self.network_status.setText("Network status unknown")
        warning = diagnostic.warning
        self.network_warning.setText(warning)
        self.network_warning.setVisible(bool(warning))
        detail = ""
        if not warning:
            detail = "Phones on the same Wi-Fi can connect." if profile != "unknown" else "Make sure your phone is on the same Wi-Fi as this PC."
        self.network_detail.setText(detail)
        self.network_detail.setVisible(bool(detail))
        self.network_buttons.setVisible(bool(warning) and not diagnostic.private_rule_present)
        self.allow_private_button.setEnabled(not diagnostic.private_rule_present)
        self.allow_private_button.setText(
            "Allowed on Private Networks" if diagnostic.private_rule_present else "Allow on Private Networks"
        )
        blocked = profile == "public" or (diagnostic.inbound_blocked and not diagnostic.private_rule_present)
        self.network_icon.setText(chr(_GLYPH_WARNING if blocked else _GLYPH_WIFI))
        _set_style_property(self.network_panel, "state", "warning" if blocked else "ok")

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

    # ------------------------------------------------------------------ queue

    def _refresh_queue(self) -> None:
        entries = self._store.list_entries()
        selected_id = self._selected_entry_id()
        self.queue_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name_item = QTableWidgetItem(entry.name)
            name_item.setData(Qt.ItemDataRole.UserRole, entry.id)
            count = len(entry.source_paths)
            preset_name = share_preset_for_key(entry.preset_key).name
            name_item.setToolTip(f"{entry.name}\n{count} image{'s' if count != 1 else ''} · {preset_name}")
            target = (entry.target or "—") + (f" · {entry.account}" if entry.account else "")
            status_item = QTableWidgetItem()
            status_item.setData(Qt.ItemDataRole.UserRole, entry.status)
            created_item = QTableWidgetItem(_display_date(entry.created_at))
            created_item.setToolTip(_full_date(entry.created_at))
            values = (name_item, QTableWidgetItem(target), status_item, created_item)
            for column, item in enumerate(values):
                self.queue_table.setItem(row, column, item)
            self.queue_table.setCellWidget(row, 2, _status_chip(entry.status))
            if entry.id == selected_id:
                self.queue_table.selectRow(row)
        self.queue_stack.setCurrentWidget(self.queue_table if entries else self.queue_empty)
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
            self.footer_note.clear()
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


def _icon_font(pixel_size: int) -> QFont:
    font = QFont()
    font.setFamilies(_ICON_FONT_FAMILIES)
    font.setPixelSize(pixel_size)
    return font


def _glyph_label(codepoint: int, pixel_size: int, object_name: str) -> QLabel:
    label = QLabel(chr(codepoint))
    label.setObjectName(object_name)
    label.setFont(_icon_font(pixel_size))
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return label


def _field_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sharePhoneFieldLabel")
    return label


def _status_chip(status: str) -> QWidget:
    holder = QWidget()
    holder.setObjectName("sharePhoneStatusCell")
    holder.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    layout = QHBoxLayout(holder)
    layout.setContentsMargins(8, 0, 0, 0)
    layout.setSpacing(0)
    text = status.title()
    chip = QLabel(f"✓ {text}" if status == "posted" else text)
    chip.setObjectName("sharePhoneStatusChip")
    chip.setProperty("shareStatus", status)
    chip.setAccessibleName(f"Status: {text}")
    layout.addWidget(chip, 0, Qt.AlignmentFlag.AlignVCenter)
    layout.addStretch(1)
    return holder


def _set_style_property(widget: QWidget, name: str, value: object) -> None:
    """Set a stylesheet selector property and re-polish so the rule applies."""
    widget.setProperty(name, value)
    style = widget.style()
    for target in (widget, *widget.findChildren(QWidget)):
        style.unpolish(target)
        style.polish(target)
    widget.update()


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
    """Compact queue date: time for today, month/day this year, full date otherwise."""
    try:
        moment = datetime.fromisoformat(value).astimezone()
    except (TypeError, ValueError):
        return value
    now = datetime.now().astimezone()
    if moment.date() == now.date():
        return moment.strftime("%I:%M %p").lstrip("0")
    if moment.year == now.year:
        return moment.strftime("%b %d").replace(" 0", " ")
    return moment.strftime("%b %d, %Y").replace(" 0", " ")


def _full_date(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().strftime("%b %d, %Y %I:%M %p")
    except (TypeError, ValueError):
        return value
