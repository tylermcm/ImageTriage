from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QFileInfo, QItemSelectionModel, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileIconProvider,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..file_associations import (
    APP_FRIENDLY_NAME,
    ExtensionAssociationState,
    FileAssociationStatus,
    describe_windows_default_handler,
    open_windows_default_apps_settings,
    open_windows_file_association_chooser,
    query_windows_file_association_states,
    query_windows_file_association_status,
    register_windows_file_associations,
    remove_windows_file_associations,
)
from ..formats import FITS_SUFFIXES, PSD_SUFFIXES, RAW_SUFFIXES


_APP_ICON_PATH = Path(__file__).resolve().parent / "assets" / "app_icon-v2.png"

_FILE_TYPE_NAMES = {
    ".3fr": "Hasselblad RAW File", ".ari": "ARRI RAW Image", ".arw": "Sony RAW File",
    ".avif": "AV1 Image File", ".bay": "Casio RAW File", ".bmp": "Bitmap Image",
    ".bmq": "NuCore RAW File", ".cap": "Phase One RAW File", ".cr2": "Canon RAW 2 Image",
    ".cr3": "Canon RAW 3 Image", ".crw": "Canon RAW Image", ".cs1": "Sinar RAW File",
    ".dc2": "Kodak RAW File", ".dcr": "Kodak RAW File", ".dib": "Device Independent Bitmap",
    ".dng": "Adobe Digital Negative", ".drf": "Kodak RAW File", ".erf": "Epson RAW File",
    ".fff": "Hasselblad RAW File", ".fit": "FITS Image", ".fits": "FITS Image",
    ".fts": "FITS Image", ".gif": "Graphics Interchange Format", ".gpr": "GoPro RAW File",
    ".heic": "High Efficiency Image", ".heif": "High Efficiency Image", ".icns": "Apple Icon Image",
    ".ico": "Windows Icon", ".iiq": "Phase One RAW File", ".jpe": "JPEG Image",
    ".jpeg": "JPEG Image", ".jfif": "JPEG File Interchange Format", ".jpg": "JPEG Image",
    ".jxl": "JPEG XL Image", ".k25": "Kodak RAW File", ".kdc": "Kodak RAW File",
    ".mdc": "Minolta RAW File", ".mef": "Mamiya RAW File", ".mos": "Leaf RAW File",
    ".mrw": "Minolta RAW File", ".nef": "Nikon RAW File", ".nrw": "Nikon RAW File",
    ".obm": "Olympus RAW File", ".orf": "Olympus RAW File", ".pbm": "Portable Bitmap",
    ".pef": "Pentax RAW File", ".pgm": "Portable Graymap", ".png": "Portable Network Graphics",
    ".pnm": "Portable Anymap", ".ppm": "Portable Pixmap", ".psb": "Photoshop Large Document",
    ".psd": "Photoshop Document", ".ptx": "Pentax RAW File", ".pxn": "Logitech RAW File",
    ".raf": "Fujifilm RAW File", ".raw": "Camera RAW Image", ".rdc": "Digital Foto Maker RAW File",
    ".rw2": "Panasonic RAW File", ".rwl": "Leica RAW File", ".sr2": "Sony RAW File",
    ".srf": "Sony RAW File", ".srw": "Samsung RAW File", ".tga": "Targa Image",
    ".tif": "TIFF Image", ".tiff": "TIFF Image", ".wbmp": "Wireless Bitmap",
    ".webp": "WebP Image", ".x3f": "Sigma RAW File", ".xbm": "X Bitmap",
    ".xpm": "X Pixmap",
}


def file_type_name(suffix: str) -> str:
    return _FILE_TYPE_NAMES.get(suffix.casefold(), "Image File")


def file_type_category(suffix: str) -> str:
    normalized = suffix.casefold()
    if normalized in RAW_SUFFIXES:
        return "raw"
    if normalized in FITS_SUFFIXES or normalized in PSD_SUFFIXES:
        return "mixed"
    return "photo"


def _manager_icon(kind: str, color: str = "#68c2ed") -> QIcon:
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(color), 3.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "search":
        painter.drawEllipse(QRectF(10, 9, 21, 21))
        painter.drawLine(28, 28, 38, 38)
    elif kind == "windows":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRect(7, 8, 15, 14)
        painter.drawRect(25, 6, 16, 16)
        painter.drawRect(7, 25, 15, 14)
        painter.drawRect(25, 25, 16, 16)
    elif kind == "photo":
        painter.drawRoundedRect(QRectF(7, 8, 34, 32), 3, 3)
        painter.drawEllipse(QRectF(29, 13, 6, 6))
        painter.drawLine(11, 34, 20, 24)
        painter.drawLine(20, 24, 27, 31)
        painter.drawLine(27, 31, 32, 26)
        painter.drawLine(32, 26, 38, 34)
    elif kind == "clipboard":
        painter.drawRoundedRect(QRectF(11, 10, 25, 31), 3, 3)
        painter.drawRoundedRect(QRectF(18, 6, 12, 8), 2, 2)
        painter.drawLine(18, 23, 30, 23)
        painter.drawLine(18, 30, 28, 30)
    elif kind == "settings":
        painter.drawEllipse(QRectF(16, 16, 16, 16))
        painter.drawEllipse(QRectF(21, 21, 6, 6))
        for x1, y1, x2, y2 in ((24, 7, 24, 13), (24, 35, 24, 41), (7, 24, 13, 24), (35, 24, 41, 24), (12, 12, 16, 16), (32, 32, 36, 36), (12, 36, 16, 32), (32, 16, 36, 12)):
            painter.drawLine(x1, y1, x2, y2)
    elif kind == "all":
        painter.drawLine(6, 16, 13, 23)
        painter.drawLine(13, 23, 24, 10)
        painter.drawLine(7, 29, 14, 36)
        painter.drawLine(14, 36, 39, 9)
    painter.end()
    return QIcon(pixmap)


class _AssociationSwitch(QAbstractButton):
    """Compact registration switch matching the manager mockup."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("associationSwitch")
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setFixedSize(46, 24)

    def sizeHint(self) -> QSize:
        return QSize(46, 24)

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(1.5, 3.0, 43.0, 18.0)
        checked = self.isChecked()
        track_color = QColor("#55b9ec") if checked else QColor("#202326")
        border_color = QColor("#69c7f2") if checked else QColor("#8a8d91")
        knob_color = QColor("#202326") if checked else QColor("#9da0a4")
        if not self.isEnabled():
            track_color.setAlpha(90)
            border_color.setAlpha(110)
            knob_color.setAlpha(110)
        painter.setPen(QPen(border_color, 1.4))
        painter.setBrush(track_color)
        painter.drawRoundedRect(track, 9.0, 9.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_color)
        painter.drawEllipse(QRectF(28.0 if checked else 5.0, 5.5, 13.0, 13.0))


class FileAssociationsDialog(QDialog):
    """Windows file-association manager with direct, per-format controls."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("fileAssociationManager")
        self.setWindowTitle("Image Triage File Association Manager")
        self.setWindowIcon(QIcon(str(_APP_ICON_PATH)))
        self.setModal(True)
        # The reference is 1024x775 including the standard Windows title bar.
        self.resize(940, 670)
        self.setMinimumSize(820, 600)
        self._row_suffixes: list[str] = []
        self._all_states: tuple[ExtensionAssociationState, ...] = ()
        self._status: FileAssociationStatus | None = None
        self._activity_log: list[str] = []
        self._file_icon_provider = QFileIconProvider()
        self._file_icon_cache: dict[str, QIcon] = {}
        self._handler_names: dict[str, str] = {}

        self._build_ui()
        self._apply_manager_style()
        self._connect_signals()
        self._refresh_status()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(12)
        brand = QWidget(self)
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(11)
        logo = QLabel(brand)
        logo.setObjectName("associationLogo")
        logo.setFixedSize(62, 62)
        logo.setPixmap(QIcon(str(_APP_ICON_PATH)).pixmap(QSize(58, 58)))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_layout.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 4, 0, 0)
        brand_text.setSpacing(0)
        title = QLabel("Image Triage", brand)
        title.setObjectName("associationBrandTitle")
        subtitle = QLabel("File Association Manager", brand)
        subtitle.setObjectName("associationBrandSubtitle")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        brand_text.addStretch(1)
        brand_layout.addLayout(brand_text)
        header.addWidget(brand, 1)

        status_card = QFrame(self)
        status_card.setObjectName("associationStatusCard")
        status_card.setFixedHeight(60)
        status_card.setFixedWidth(480)
        status_layout = QHBoxLayout(status_card)
        status_layout.setContentsMargins(13, 7, 9, 7)
        status_layout.setSpacing(7)
        status_text = QVBoxLayout()
        status_text.setSpacing(4)
        self.registered_count_label = QLabel(status_card)
        self.registered_count_label.setObjectName("associationCountLabel")
        self.registered_count_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.registration_progress = QProgressBar(status_card)
        self.registration_progress.setObjectName("associationProgress")
        self.registration_progress.setTextVisible(False)
        self.registration_progress.setFixedHeight(8)
        status_text.addWidget(self.registered_count_label)
        status_text.addWidget(self.registration_progress)
        status_layout.addLayout(status_text, 1)
        self.copy_status_button = self._header_icon_button("clipboard", "Copy association status")
        self.settings_button = self._header_icon_button("settings", "Open Windows Default Apps settings")
        status_layout.addWidget(self.copy_status_button)
        status_layout.addWidget(self.settings_button)
        header.addWidget(status_card, 0)
        root.addLayout(header)

        intro = QLabel(
            "Choose file extensions to register. Image Triage registration makes it an available application for these types.\n"
            'Use "Configure Defaults" to set it as the primary app in Windows Settings.',
            self,
        )
        intro.setObjectName("associationIntro")
        intro.setWordWrap(True)
        root.addWidget(intro)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(7)
        filter_row.addStretch(1)
        self.search_field = QLineEdit(self)
        self.search_field.setObjectName("associationSearch")
        self.search_field.setPlaceholderText("Search")
        self.search_field.setClearButtonEnabled(True)
        self.search_field.setFixedWidth(220)
        self.search_field.addAction(_manager_icon("search", "#c8cacc"), QLineEdit.ActionPosition.TrailingPosition)
        self.category_combo = QComboBox(self)
        self.category_combo.setObjectName("associationCategory")
        self.category_combo.addItem("Photo, RAW, Mixed   ▾", "all")
        self.category_combo.addItem("Photo", "photo")
        self.category_combo.addItem("RAW", "raw")
        self.category_combo.addItem("Mixed", "mixed")
        self.category_combo.setFixedWidth(170)
        filter_row.addWidget(self.search_field)
        filter_row.addWidget(self.category_combo)
        root.addLayout(filter_row)

        self.table = QTableWidget(0, 4, self)
        self.table.setObjectName("associationTable")
        self.table.setHorizontalHeaderLabels(
            ["Extension & Type", "Registration Status", "Current Default Application", "Manage Default"]
        )
        for column in range(4):
            self.table.horizontalHeaderItem(column).setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(35)
        self.table.setIconSize(QSize(21, 21))
        header_view = self.table.horizontalHeader()
        header_view.setHighlightSections(False)
        header_view.setMinimumSectionSize(120)
        header_view.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header_view.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 320)
        self.table.setColumnWidth(1, 150)
        self.table.setColumnWidth(3, 185)
        root.addWidget(self.table, 1)

        self.help_label = QLabel(
            "Choose file extensions to register. Image Triage registration makes it an available application for these types. "
            'Use "Configure Defaults" to set it as the primary app in Windows Settings.',
            self,
        )
        self.help_label.setObjectName("associationHelp")
        self.help_label.setWordWrap(True)
        root.addWidget(self.help_label)

        bulk_actions = QHBoxLayout()
        bulk_actions.setSpacing(8)
        self.register_all_button = QPushButton("Register All", self)
        self.register_all_button.setObjectName("associationRegisterAllButton")
        self.register_all_button.setIcon(_manager_icon("all"))
        self.default_apps_button = QPushButton("Configure All", self)
        self.default_apps_button.setObjectName("associationActionButton")
        self.default_apps_button.setIcon(_manager_icon("windows"))
        self.close_button = QPushButton("Close", self)
        self.close_button.setObjectName("associationCloseButton")
        for button in (self.register_all_button, self.default_apps_button):
            button.setIconSize(QSize(19, 19))
        self.register_all_button.setFixedWidth(124)
        self.default_apps_button.setFixedWidth(128)
        self.close_button.setFixedWidth(96)
        bulk_actions.addWidget(self.register_all_button)
        bulk_actions.addWidget(self._vertical_divider())
        bulk_actions.addWidget(self.default_apps_button)
        bulk_actions.addStretch(1)
        bulk_actions.addWidget(self.close_button)
        root.addLayout(bulk_actions)

        bottom = QHBoxLayout()
        bottom.setSpacing(14)
        self.refresh_button = self._link_button("Refresh List")
        self.view_log_button = self._link_button("View Log")
        bottom.addWidget(self.refresh_button)
        bottom.addWidget(self.view_log_button)
        bottom.addStretch(1)
        root.addLayout(bottom)

    def _header_icon_button(self, kind: str, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("associationHeaderIconButton")
        button.setIcon(_manager_icon(kind, "#e4e5e7"))
        button.setIconSize(QSize(19, 19))
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setFixedSize(26, 26)
        return button

    def _link_button(self, text: str) -> QPushButton:
        button = QPushButton(text, self)
        button.setObjectName("associationLinkButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFlat(True)
        return button

    def _vertical_divider(self) -> QFrame:
        divider = QFrame(self)
        divider.setObjectName("associationActionDivider")
        divider.setFrameShape(QFrame.Shape.VLine)
        divider.setFixedWidth(2)
        return divider

    def _connect_signals(self) -> None:
        self.register_all_button.clicked.connect(self._register_all_associations)
        self.default_apps_button.clicked.connect(self._open_default_apps)
        self.settings_button.clicked.connect(self._open_default_apps)
        self.copy_status_button.clicked.connect(self._copy_status)
        self.refresh_button.clicked.connect(self._refresh_status)
        self.view_log_button.clicked.connect(self._view_log)
        self.close_button.clicked.connect(self.accept)
        self.search_field.textChanged.connect(self._apply_filters)
        self.category_combo.currentIndexChanged.connect(self._apply_filters)
        self.table.itemSelectionChanged.connect(self._update_action_state)
        self.table.itemDoubleClicked.connect(self._handle_item_double_click)

    def _refresh_status(self) -> None:
        selected_suffixes = set(self._selected_suffixes())
        status = query_windows_file_association_status()
        self._status = status
        total_count = len(status.supported_suffixes)
        registered_count = len(status.registered_suffixes)
        self.registered_count_label.setText(
            f"Registered File Types: <b>{registered_count}</b> / <b>{total_count}</b>"
        )
        self.registration_progress.setRange(0, max(1, total_count))
        self.registration_progress.setValue(registered_count)

        if not status.windows_supported:
            self.registered_count_label.setText("File associations are only available on Windows")
            self._all_states = ()
            self.table.setRowCount(0)
            self.table.setEnabled(False)
            for button in (
                self.register_all_button, self.default_apps_button, self.settings_button, self.refresh_button,
            ):
                button.setEnabled(False)
            return

        self.table.setEnabled(True)
        self._all_states = query_windows_file_association_states()
        self._handler_names = {
            state.suffix: describe_windows_default_handler(state) for state in self._all_states
        }
        self._apply_filters(selected_suffixes=selected_suffixes)
        self._update_action_state()

    def _apply_filters(self, _value=None, *, selected_suffixes: set[str] | None = None) -> None:
        if selected_suffixes is None:
            selected_suffixes = set(self._selected_suffixes())
        query = " ".join(self.search_field.text().casefold().split())
        category = str(self.category_combo.currentData() or "all")
        visible_states = []
        for state in self._all_states:
            if category != "all" and file_type_category(state.suffix) != category:
                continue
            handler = self._handler_names.get(state.suffix, "")
            haystack = " ".join((state.suffix, file_type_name(state.suffix), handler)).casefold()
            if query and query not in haystack:
                continue
            visible_states.append(state)
        self._populate_table(tuple(visible_states), selected_suffixes)

    def _populate_table(
        self, states: tuple[ExtensionAssociationState, ...], selected_suffixes: set[str]
    ) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            self._row_suffixes = [state.suffix for state in states]
            self.table.setRowCount(len(states))
            for row, state in enumerate(states):
                for column in range(4):
                    item = QTableWidgetItem()
                    item.setFlags(Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled)
                    self.table.setItem(row, column, item)
                self.table.setCellWidget(row, 0, self._extension_cell(state, row))
                self.table.setCellWidget(row, 1, self._registration_cell(state))
                self.table.setCellWidget(row, 2, self._default_app_cell(state))
                self.table.setCellWidget(row, 3, self._configure_cell(state.suffix))
                self.table.setRowHeight(row, 35)
            if selected_suffixes:
                for row, suffix in enumerate(self._row_suffixes):
                    if suffix in selected_suffixes:
                        self.table.selectionModel().select(
                            self.table.model().index(row, 0),
                            QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
                        )
            if states and not self.table.selectionModel().hasSelection():
                self.table.selectRow(0)
        finally:
            self.table.blockSignals(False)
            self.table.setUpdatesEnabled(True)
        self._sync_selected_row_accents()

    def _extension_cell(self, state: ExtensionAssociationState, row: int) -> QFrame:
        cell = QFrame(self.table)
        cell.setObjectName("associationExtensionCell")
        cell.setProperty("row", row)
        cell.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(7, 1, 5, 1)
        layout.setSpacing(8)
        icon = QLabel(cell)
        icon.setFixedSize(24, 22)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        category = file_type_category(state.suffix)
        if category in {"raw", "mixed"}:
            icon.setText("RAW" if category == "raw" else "MIX")
            icon.setObjectName("associationRawBadge")
        else:
            icon.setPixmap(_manager_icon("photo", "#94aec6").pixmap(QSize(20, 20)))
        label = QLabel(cell)
        label.setObjectName("associationExtensionLabel")
        label.setText(f"<b>{state.suffix}</b> <span style='color:#c6c8cb'>[{file_type_name(state.suffix)}]</span>")
        label.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(icon)
        layout.addWidget(label, 1)
        return cell

    def _registration_cell(self, state: ExtensionAssociationState) -> QWidget:
        cell = QWidget(self.table)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        switch = _AssociationSwitch(cell)
        switch.setChecked(state.registered)
        switch.setToolTip(f"Unregister {state.suffix}" if state.registered else f"Register {state.suffix}")
        switch.clicked.connect(
            lambda checked=False, suffix=state.suffix: self._handle_registration_toggle(suffix, checked)
        )
        layout.addWidget(switch, 0, Qt.AlignmentFlag.AlignCenter)
        return cell

    def _default_app_cell(self, state: ExtensionAssociationState) -> QWidget:
        cell = QWidget(self.table)
        cell.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(8, 1, 8, 1)
        layout.setSpacing(9)
        icon = QLabel(cell)
        icon.setFixedSize(23, 23)
        icon.setPixmap(self._file_icon(state.suffix).pixmap(QSize(21, 21)))
        handler = self._handler_names.get(state.suffix)
        if handler is None:
            handler = describe_windows_default_handler(state)
        label = QLabel(handler, cell)
        label.setObjectName("associationDefaultAppLabel")
        label.setProperty("ours", state.is_default)
        layout.addWidget(icon)
        layout.addWidget(label, 1)
        return cell

    def _configure_cell(self, suffix: str) -> QWidget:
        cell = QWidget(self.table)
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(7, 2, 7, 2)
        button = QPushButton("Configure Default...", cell)
        button.setObjectName("associationConfigureButton")
        button.setIcon(_manager_icon("windows"))
        button.setIconSize(QSize(18, 18))
        button.setToolTip(f"Choose the default application for {suffix}")
        button.clicked.connect(lambda _checked=False, target=suffix: self._set_default_for_suffix(target))
        layout.addWidget(button)
        return cell

    def _file_icon(self, suffix: str) -> QIcon:
        cached = self._file_icon_cache.get(suffix)
        if cached is None:
            cached = self._file_icon_provider.icon(QFileInfo(f"image_triage_file{suffix}"))
            self._file_icon_cache[suffix] = cached
        return cached

    def _selected_rows(self) -> list[int]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        if rows:
            return rows
        current_row = self.table.currentRow()
        return [current_row] if current_row >= 0 else []

    def _selected_suffixes(self) -> list[str]:
        return [self._row_suffixes[row] for row in self._selected_rows() if 0 <= row < len(self._row_suffixes)]

    def _selected_suffix(self) -> str | None:
        suffixes = self._selected_suffixes()
        return suffixes[0] if suffixes else None

    def _sync_selected_row_accents(self) -> None:
        selected = set(self._selected_rows())
        for row in range(self.table.rowCount()):
            cell = self.table.cellWidget(row, 0)
            if cell is None:
                continue
            cell.setProperty("selected", row in selected)
            cell.style().unpolish(cell)
            cell.style().polish(cell)

    def _update_action_state(self) -> None:
        if not self.table.isEnabled() or self._status is None:
            return
        self._sync_selected_row_accents()
        registered = set(self._status.registered_suffixes)
        self.register_all_button.setEnabled(
            len(registered) < len(self._status.supported_suffixes) or not self._status.app_registered
        )

    def _handle_registration_toggle(self, suffix: str, checked: bool) -> None:
        action = (
            (lambda: register_windows_file_associations([suffix]))
            if checked
            else (lambda: remove_windows_file_associations([suffix]))
        )
        self._run_registry_action(
            action, f"{suffix} {'registered' if checked else 'unregistered'}.", show_confirmation=False
        )

    def _register_all_associations(self) -> None:
        self._run_registry_action(
            register_windows_file_associations,
            f"{APP_FRIENDLY_NAME} is now registered for all supported extensions.",
        )

    def _run_registry_action(self, action, success_message: str, *, show_confirmation: bool = True) -> None:
        try:
            action()
        except Exception as exc:
            message = f"Could not update file associations.\n\n{exc}"
            self._activity_log.append(message)
            QMessageBox.warning(self, "File Associations", message)
            self._refresh_status()
            return
        self._activity_log.append(success_message)
        self._refresh_status()
        if show_confirmation:
            QMessageBox.information(self, "File Associations", success_message)

    def _set_selected_default(self) -> None:
        suffix = self._selected_suffix()
        if suffix:
            self._set_default_for_suffix(suffix)

    def _set_default_for_suffix(self, suffix: str) -> None:
        try:
            open_windows_file_association_chooser(suffix)
        except Exception as exc:
            message = f"Could not open the Windows chooser.\n\n{exc}"
            self._activity_log.append(message)
            QMessageBox.warning(self, "File Associations", message)
            return
        self._activity_log.append(f"Opened the Windows default-app chooser for {suffix}.")

    def _handle_item_double_click(self, _item: QTableWidgetItem) -> None:
        self._set_selected_default()

    def _open_default_apps(self) -> None:
        try:
            open_windows_default_apps_settings()
        except Exception as exc:
            message = f"Could not open Windows Default Apps.\n\n{exc}"
            self._activity_log.append(message)
            QMessageBox.warning(self, "File Associations", message)
            return
        self._activity_log.append("Opened Windows Default Apps settings.")

    def _association_report(self) -> str:
        status = self._status
        if status is None:
            return "Image Triage file-association status is unavailable."
        registered = ", ".join(status.registered_suffixes) or "None"
        return (
            "Image Triage File Association Manager\n"
            f"Registered File Types: {len(status.registered_suffixes)} / {len(status.supported_suffixes)}\n"
            f"Registered: {registered}\nOpen command: {status.command}"
        )

    def _copy_status(self) -> None:
        QApplication.clipboard().setText(self._association_report())
        self._activity_log.append("Copied association status to the clipboard.")
        self.copy_status_button.setToolTip("Association status copied")

    def _view_log(self) -> None:
        text = "\n".join(self._activity_log[-40:]) or "No file-association changes in this session."
        QMessageBox.information(self, "File Association Log", text)

    def _apply_manager_style(self) -> None:
        self.setStyleSheet(
            """
            QDialog#fileAssociationManager { background-color: #101112; color: #f1f2f3; font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 14px; }
            QLabel { background-color: transparent; color: #eef0f2; }
            QLabel#associationBrandTitle { font-size: 25px; font-weight: 700; }
            QLabel#associationBrandSubtitle { font-size: 16px; font-weight: 600; color: #e3e4e6; }
            QLabel#associationIntro, QLabel#associationHelp { font-size: 14px; color: #f0f1f2; }
            QFrame#associationStatusCard { background-color: #232425; border: 1px solid #3a3c3e; border-radius: 8px; }
            QLabel#associationCountLabel { font-size: 16px; color: #f4f5f6; }
            QProgressBar#associationProgress { background-color: #494b4d; border: none; border-radius: 4px; }
            QProgressBar#associationProgress::chunk { background-color: #58b9eb; border-radius: 4px; }
            QToolButton#associationHeaderIconButton { background-color: transparent; border: 1px solid transparent; border-radius: 6px; color: #e4e5e7; font-family: "Segoe Fluent Icons", "Segoe MDL2 Assets"; font-size: 20px; }
            QToolButton#associationHeaderIconButton:hover { background-color: #383a3c; }
            QLineEdit#associationSearch, QComboBox#associationCategory { background-color: #2b2c2e; border: 1px solid #46484b; border-radius: 7px; color: #f2f3f4; min-height: 30px; padding: 0 8px; selection-background-color: #315b78; }
            QLineEdit#associationSearch:focus, QComboBox#associationCategory:focus { border-color: #68bce8; }
            QComboBox#associationCategory::drop-down { border: none; width: 30px; }
            QComboBox#associationCategory QAbstractItemView { background-color: #242527; border: 1px solid #494b4e; color: #f0f1f2; selection-background-color: #34495b; }
            QTableWidget#associationTable { background-color: #171819; alternate-background-color: #171819; border: 1px solid #414346; border-radius: 7px; color: #eef0f2; outline: none; selection-background-color: #25292c; selection-color: #ffffff; }
            QTableWidget#associationTable::item { background-color: transparent; border-bottom: 1px solid #242628; padding: 0; }
            QTableWidget#associationTable::item:selected { background-color: #242729; }
            QHeaderView::section { background-color: #1f2022; border: none; border-right: 1px solid #414346; border-bottom: 1px solid #414346; color: #cfd1d3; font-size: 14px; font-weight: 500; padding: 6px 7px; text-align: left; }
            QFrame#associationExtensionCell { background-color: transparent; border-left: 3px solid transparent; }
            QFrame#associationExtensionCell[selected="true"] { border-left-color: #55b9ec; }
            QLabel#associationExtensionLabel { font-size: 14px; color: #f2f3f4; }
            QLabel#associationDefaultAppLabel { font-size: 14px; color: #e3e5e7; }
            QLabel#associationDefaultAppLabel[ours="true"] { color: #7dcef4; font-weight: 600; }
            QLabel#associationRawBadge { background-color: #dedede; border-radius: 2px; color: #151617; font-size: 8px; font-weight: 800; padding: 1px 2px; }
            QPushButton#associationConfigureButton { background-color: #343638; border: 1px solid #46484a; border-radius: 6px; color: #f3f4f5; min-height: 25px; padding: 0 7px; font-size: 13px; text-align: left; }
            QPushButton#associationConfigureButton:hover { background-color: #414447; border-color: #5a5d60; }
            QPushButton#associationPrimaryButton, QPushButton#associationActionButton, QPushButton#associationRegisterAllButton, QPushButton#associationCloseButton { background-color: #2d2f31; border: 1px solid #424548; border-radius: 7px; color: #f2f3f4; min-height: 34px; padding: 0 11px; font-size: 14px; }
            QPushButton#associationPrimaryButton { background-color: #58b9eb; border-color: #70c8f1; color: #10202a; }
            QPushButton#associationRegisterAllButton { background-color: #263c34; border-color: #3c5a4e; color: #e9f5f0; }
            QPushButton#associationPrimaryButton:hover { background-color: #6bc4ef; }
            QPushButton#associationActionButton:hover, QPushButton#associationCloseButton:hover { background-color: #3a3d40; }
            QPushButton#associationRegisterAllButton:hover { background-color: #315044; }
            QPushButton:disabled { background-color: #222426; border-color: #303234; color: #74777a; }
            QFrame#associationActionDivider { color: #3f4144; margin: 3px 3px; }
            QPushButton#associationLinkButton { background-color: transparent; border: none; color: #9cc9ed; min-height: 28px; padding: 0 2px; font-size: 14px; text-decoration: underline; }
            QPushButton#associationLinkButton:hover { color: #c8e5fb; }
            QScrollBar:vertical { background: #18191a; border: none; width: 12px; margin: 1px; }
            QScrollBar::handle:vertical { background: #777a7d; border-radius: 5px; min-height: 36px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            """
        )
