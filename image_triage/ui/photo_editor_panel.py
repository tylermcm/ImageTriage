from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QSignalBlocker, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from ..perf import perf_logger
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from PIL import Image

from ..editor_copy import (
    SAVE_COPY_FILTER_TEXT,
    default_save_copy_path,
    normalize_save_copy_path,
    selected_save_copy_filter,
    validate_save_copy_paths,
)
from ..scanner import is_editor_asset_path


_CLI_EDITOR_ROOT = Path(__file__).resolve().parents[2] / "cli_editor"
if _CLI_EDITOR_ROOT.exists() and str(_CLI_EDITOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLI_EDITOR_ROOT))

from photo_terminal.adjustments import EditRecipe  # noqa: E402
from photo_terminal.session import (  # noqa: E402
    SessionError,
    add_space,
    asset_dir_for_session,
    copy_bitmap_asset,
    default_session_path,
    export_xmp,
    image_dimensions,
    load_session,
    mask_ids,
    new_session,
    operation_ids,
    remove_mask,
    save_session,
    space_ids,
    upsert_mask,
    validate_session,
)
from photo_terminal.io import open_image  # noqa: E402
from photo_terminal.masks import refine_color_range, refine_luminance_range  # noqa: E402


ADJUSTMENT_SPECS: tuple[tuple[str, str, int, int, int], ...] = (
    ("exposure", "Exposure", -200, 200, 100),
    ("contrast", "Contrast", -100, 100, 1),
    ("highlights", "Highlights", -100, 100, 1),
    ("shadows", "Shadows", -100, 100, 1),
    ("whites", "Whites", -100, 100, 1),
    ("blacks", "Blacks", -100, 100, 1),
    ("temperature", "Temperature", -100, 100, 1),
    ("tint", "Tint", -100, 100, 1),
    ("vibrance", "Vibrance", -100, 100, 1),
    ("saturation", "Saturation", -100, 100, 1),
    ("clarity", "Clarity", -100, 100, 1),
    ("dehaze", "Dehaze", -100, 100, 1),
    ("sharpen", "Sharpen", 0, 100, 1),
    ("denoise", "Denoise", 0, 100, 1),
    ("vignette", "Vignette", -100, 100, 1),
)

ADJUSTMENT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Light", ("exposure", "contrast", "highlights", "shadows", "whites", "blacks")),
    ("Color", ("temperature", "tint", "vibrance", "saturation")),
    ("Effects", ("clarity", "dehaze", "sharpen", "denoise", "vignette")),
)

# Local (per-mask) adjustments: everything except vignette, which is
# inherently a whole-frame effect.
MASK_ADJUSTMENT_KEYS: tuple[str, ...] = tuple(
    spec[0] for spec in ADJUSTMENT_SPECS if spec[0] != "vignette"
)


SESSION_OPS: dict[str, tuple[str, str]] = {
    "exposure": ("adjust.exposure", "exposure"),
    "contrast": ("adjust.contrast", "contrast"),
    "highlights": ("adjust.highlights", "highlights"),
    "shadows": ("adjust.shadows", "shadows"),
    "whites": ("adjust.whites", "whites"),
    "blacks": ("adjust.blacks", "blacks"),
    "temperature": ("adjust.white_balance", "temperature"),
    "tint": ("adjust.white_balance", "tint"),
    "vibrance": ("adjust.vibrance", "vibrance"),
    "saturation": ("adjust.saturation", "saturation"),
    "denoise": ("adjust.denoise", "denoise"),
    "clarity": ("adjust.clarity", "clarity"),
    "dehaze": ("adjust.dehaze", "dehaze"),
    "sharpen": ("adjust.sharpen", "sharpen"),
    "vignette": ("adjust.vignette", "vignette"),
}

def _next_id(existing: set[str], prefix: str) -> str:
    index = 1
    while True:
        candidate = f"{prefix}-{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


def _format_json(data: Any) -> str:
    return json.dumps(data, indent=2, sort_keys=False)


def _friendly_mask_label(mask: dict[str, Any]) -> str:
    style = mask.get("uiStyle")
    if style == "brush":
        return "Brush"
    if style == "luminance-range":
        return "Luminance Range"
    if style == "color-range":
        return "Color Range"
    labels = {
        "radial": "Radial Gradient",
        "linear-gradient": "Linear Gradient",
        "bitmap": "Brush Mask",
        "subject-select": "Subject Mask",
    }
    return labels.get(str(mask.get("type")), "Mask")


class _CurveThumb(QWidget):
    """Photoshop-style tone curve preview: dark plot, quarter grid, identity line."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(116)

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor("#141414")))
        painter.setBrush(QColor("#1e1e1e"))
        painter.drawRoundedRect(rect, 3, 3)

        inner = rect.adjusted(9, 9, -9, -9)
        painter.setPen(QPen(QColor("#2e2e2e")))
        for step in range(1, 4):
            x = inner.left() + inner.width() * step // 4
            y = inner.top() + inner.height() * step // 4
            painter.drawLine(x, inner.top(), x, inner.bottom())
            painter.drawLine(inner.left(), y, inner.right(), y)

        path = QPainterPath()
        path.moveTo(float(inner.left()), float(inner.bottom()))
        path.lineTo(float(inner.right()), float(inner.top()))
        painter.setPen(QPen(QColor("#d9d9d9"), 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#f0f0f0"))
        for cx, cy in ((inner.left(), inner.bottom()), (inner.right(), inner.top())):
            painter.drawRect(cx - 2, cy - 2, 5, 5)
        painter.end()


class _AdjustmentRow(QWidget):
    changed = Signal(str, float)

    def __init__(self, key: str, label: str, minimum: int, maximum: int, scale: int, parent=None) -> None:
        super().__init__(parent)
        self.key = key
        self.scale = scale
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(minimum, maximum)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(10)
        self.slider.setValue(0)
        self.slider.setObjectName(f"slider_{key}")
        self.value_label = QLabel(self._format_value(0.0), self)
        self.value_label.setObjectName("editorNumber")
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.value_label.setMinimumWidth(52)
        title = QLabel(label, self)
        title.setObjectName("editorControlLabel")

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(1)
        layout.addWidget(title, 0, 0)
        layout.addWidget(self.value_label, 0, 1, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.slider, 1, 0, 1, 2)
        self.slider.valueChanged.connect(self._handle_slider_changed)

    def _handle_slider_changed(self, raw: int) -> None:
        value = raw / self.scale
        self.value_label.setText(self._format_value(value))
        self.changed.emit(self.key, value)

    def set_value(self, value: float) -> None:
        with QSignalBlocker(self.slider):
            self.slider.setValue(int(round(float(value) * self.scale)))
        self.value_label.setText(self._format_value(float(value)))

    def _format_value(self, value: float) -> str:
        if self.scale == 100:
            return f"{value:+.2f}"
        return f"{value:+.0f}"


class PhotoEditorPanel(QFrame):
    recipe_changed = Signal(object)
    saved = Signal(str)
    save_copy_requested = Signal(str, str, object, object)
    status_changed = Signal(str)
    # The on-canvas mask overlay should re-read mask_overlay_state().
    mask_overlay_changed = Signal()

    MASK_SHAPE_TYPES = ("radial", "linear-gradient")
    _MASK_IDLE_HINT = (
        "Pick a tool, then drag on the photo. Drag the handles to reshape, the "
        "inner ring to feather, the top knob to rotate. Click a mask to move it."
    )
    _MASK_ARMED_HINT = {
        "radial": "Drawing a radial mask — click and drag on the photo to place it.",
        "linear-gradient": "Drawing a linear mask — drag across the photo to place it.",
        "color-range": "Click the photo to sample the color range.",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("photoEditorPanel")
        self._source_path: Path | None = None
        self._session_path: Path | None = None
        self._session: dict[str, Any] | None = None
        self._recipe = EditRecipe()
        self._rows: dict[str, _AdjustmentRow] = {}
        self._mask_create_mode: str | None = None
        self._pending_parent_id: str | None = None
        self._pending_combine: str = "add"
        self._brush_paint_mode: str | None = None
        self._color_resample_mask_id: str | None = None
        self._pending_range_mask_id: str | None = None
        self._updating_mask_controls = False
        self._overlay_suppressed_for_drag = False
        self._source_size_cache: tuple[Path, tuple[int, int]] | None = None
        self._copy_save_busy = False
        self._mask_commit_timer = QTimer(self)
        self._mask_commit_timer.setSingleShot(True)
        self._mask_commit_timer.setInterval(400)
        self._mask_commit_timer.timeout.connect(self._flush_mask_commit)
        self._range_update_timer = QTimer(self)
        self._range_update_timer.setSingleShot(True)
        self._range_update_timer.setInterval(160)
        self._range_update_timer.timeout.connect(self._regenerate_selected_range_mask)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_tab_bar())

        doc_bar = QFrame(self)
        doc_bar.setObjectName("photoEditorDocBar")
        doc_layout = QHBoxLayout(doc_bar)
        doc_layout.setContentsMargins(12, 6, 12, 6)
        doc_layout.setSpacing(6)
        self.subtitle_label = QLabel("No image selected", doc_bar)
        self.subtitle_label.setObjectName("photoEditorSubtitle")
        self.subtitle_label.setWordWrap(True)
        doc_layout.addWidget(self.subtitle_label, 1)
        root.addWidget(doc_bar)

        self.editor_stack = QStackedWidget(self)
        self.editor_stack.setObjectName("photoEditorStack")
        self.editor_stack.addWidget(self._build_adjust_tab())
        self.editor_stack.addWidget(self._build_masks_tab())
        self.editor_stack.addWidget(self._build_session_tab())
        root.addWidget(self.editor_stack, 1)

        footer = QFrame(self)
        footer.setObjectName("photoEditorFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(12, 10, 12, 12)
        footer_layout.setSpacing(8)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(8)
        self.reset_button = QPushButton("Reset", footer)
        self.save_button = QPushButton("Save", footer)
        self.save_copy_button = QPushButton("Save Copy", footer)
        self.save_button.setObjectName("editorPrimaryButton")
        self.reset_button.clicked.connect(self.reset_recipe)
        self.save_button.clicked.connect(self.save_sidecar)
        self.save_copy_button.clicked.connect(self.save_copy)
        button_row.addWidget(self.reset_button)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.save_copy_button)
        footer_layout.addLayout(button_row)
        self.status_label = QLabel("", footer)
        self.status_label.setObjectName("photoEditorStatus")
        self.status_label.setWordWrap(True)
        footer_layout.addWidget(self.status_label)
        root.addWidget(footer)
        self._sync_enabled()
        self._sync_mask_controls(None)

    @property
    def recipe(self) -> EditRecipe:
        return self._recipe

    def _build_tab_bar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("editorTabBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(6, 5, 6, 0)
        layout.setSpacing(1)
        self._mode_buttons: list[QToolButton] = []
        tabs = (
            ("Adjust", "Tone, color and effects", 0),
            ("Masks", "Local adjustments", 1),
            ("Session", "Sidecar session", 2),
        )
        for text, tooltip, index in tabs:
            button = QToolButton(bar)
            button.setObjectName("editorTab")
            button.setText(text)
            button.setToolTip(tooltip)
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, page=index: self._set_editor_page(page))
            layout.addWidget(button)
            self._mode_buttons.append(button)
        layout.addStretch(1)
        self._mode_buttons[0].setChecked(True)
        return bar

    def _set_editor_page(self, index: int) -> None:
        self.editor_stack.setCurrentIndex(index)
        for button_index, button in enumerate(self._mode_buttons):
            button.setChecked(button_index == index)
        self.mask_overlay_changed.emit()

    def _section(self, title: str, parent: QWidget) -> tuple[QFrame, QVBoxLayout]:
        section = QFrame(parent)
        section.setObjectName("editorSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QPushButton(f"▾  {title}", section)
        header.setObjectName("editorSectionHeader")
        header.setCheckable(True)
        header.setChecked(True)
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        body = QWidget(section)
        content = QVBoxLayout(body)
        content.setContentsMargins(10, 2, 10, 10)
        content.setSpacing(8)

        def _toggle(expanded: bool) -> None:
            body.setVisible(expanded)
            arrow = "▾" if expanded else "▸"
            header.setText(f"{arrow}  {title}")

        header.toggled.connect(_toggle)
        layout.addWidget(header)
        layout.addWidget(body)
        return section, content

    def _slider_spin_row(
        self,
        label: str,
        spin: QSpinBox | QDoubleSpinBox,
        *,
        minimum: float,
        maximum: float,
        scale: int = 1,
        parent: QWidget,
    ) -> QWidget:
        row = QWidget(parent)
        title = QLabel(label, row)
        title.setObjectName("editorControlLabel")
        value_label = QLabel("", row)
        value_label.setObjectName("editorNumber")
        value_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        value_label.setMinimumWidth(52)
        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(int(round(minimum * scale)), int(round(maximum * scale)))
        slider.setValue(int(round(float(spin.value()) * scale)))
        slider.setSingleStep(1)
        spin.hide()

        def update_label(value: float) -> None:
            if isinstance(spin, QDoubleSpinBox) and spin.decimals() > 0:
                value_label.setText(f"{value:.{spin.decimals()}f}")
            else:
                value_label.setText(f"{value:.0f}")

        def slider_changed(raw: int) -> None:
            value = raw / scale
            if isinstance(spin, QSpinBox):
                spin.setValue(int(round(value)))
            else:
                spin.setValue(value)
            update_label(float(spin.value()))

        def spin_changed(value: float) -> None:
            with QSignalBlocker(slider):
                slider.setValue(int(round(float(value) * scale)))
            update_label(float(value))

        slider.valueChanged.connect(slider_changed)
        spin.valueChanged.connect(spin_changed)
        update_label(float(spin.value()))

        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(1)
        layout.addWidget(title, 0, 0)
        layout.addWidget(value_label, 0, 1)
        layout.addWidget(slider, 1, 0, 1, 2)
        return row

    def _action_button(self, text: str, parent: QWidget) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName("editorActionButton")
        return button

    def _tool_toggle(self, text: str, parent: QWidget) -> QPushButton:
        button = QPushButton(text, parent)
        button.setObjectName("editorToolToggle")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _arm_base_tool(self, mode: str | None) -> None:
        # A directly-picked tool draws a fresh top-level mask, not a submask.
        self._pending_parent_id = None
        self._pending_combine = "add"
        self._brush_paint_mode = None
        self._set_mask_tool(mode)

    def _set_mask_tool(self, mode: str | None) -> None:
        self._mask_create_mode = mode
        if mode != "color-range":
            self._color_resample_mask_id = None
        with (
            QSignalBlocker(self.radial_tool_button),
            QSignalBlocker(self.linear_tool_button),
            QSignalBlocker(self.brush_tool_button),
            QSignalBlocker(self.color_tool_button),
        ):
            self.radial_tool_button.setChecked(mode == "radial")
            self.linear_tool_button.setChecked(mode == "linear-gradient")
            self.brush_tool_button.setChecked(self._brush_paint_mode is not None)
            self.color_tool_button.setChecked(mode == "color-range")
        if mode is None:
            self._masking_hint.setText(self._MASK_IDLE_HINT)
            self._masking_hint.setStyleSheet("")
        else:
            self._masking_hint.setText(self._MASK_ARMED_HINT[mode])
            # Accent the prompt so an armed tool reads as a distinct mode.
            self._masking_hint.setStyleSheet("color: #4a9eff; font-weight: 600;")
        self.mask_overlay_changed.emit()

    def _build_adjust_tab(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setObjectName("photoEditorScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(scroll)
        body.setObjectName("photoEditorBody")
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(0)

        specs_by_key = {spec[0]: spec for spec in ADJUSTMENT_SPECS}
        for title, keys in ADJUSTMENT_GROUPS:
            section, section_layout = self._section(title, body)
            if title == "Color":
                profile_row = QHBoxLayout()
                profile_row.setContentsMargins(0, 0, 0, 4)
                profile_label = QLabel("White balance", section)
                profile_label.setObjectName("editorControlLabel")
                self.white_balance_combo = QComboBox(section)
                self.white_balance_combo.addItems(["As Shot", "Auto", "Daylight", "Cloudy", "Shade", "Custom"])
                profile_row.addWidget(profile_label)
                profile_row.addWidget(self.white_balance_combo)
                section_layout.addLayout(profile_row)
            for key in keys:
                spec = specs_by_key[key]
                row = _AdjustmentRow(*spec, parent=body)
                row.changed.connect(self._handle_adjustment_changed)
                self._rows[key] = row
                section_layout.addWidget(row)
            body_layout.addWidget(section)

        curve_section, curve_layout = self._section("Curve", body)
        curve_modes = QHBoxLayout()
        curve_modes.setContentsMargins(0, 0, 0, 0)
        curve_label = QLabel("Adjust", curve_section)
        curve_label.setObjectName("editorControlLabel")
        curve_modes.addWidget(curve_label)
        for label in ("RGB", "W", "R", "G", "B"):
            dot = QLabel(label, curve_section)
            dot.setObjectName("curveModeDot")
            curve_modes.addWidget(dot)
        curve_modes.addStretch(1)
        curve_layout.addLayout(curve_modes)
        self.curve_preview = _CurveThumb(curve_section)
        curve_layout.addWidget(self.curve_preview)
        curve_io = QHBoxLayout()
        self.curve_input_edit = QLineEdit(curve_section)
        self.curve_output_edit = QLineEdit(curve_section)
        self.curve_input_edit.setText("0")
        self.curve_output_edit.setText("0")
        curve_io.addWidget(QLabel("Input:", curve_section))
        curve_io.addWidget(self.curve_input_edit)
        curve_io.addWidget(QLabel("Output:", curve_section))
        curve_io.addWidget(self.curve_output_edit)
        curve_layout.addLayout(curve_io)
        body_layout.addWidget(curve_section)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def _build_masks_tab(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setObjectName("photoEditorScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tab = QWidget(scroll)
        tab.setObjectName("photoEditorBody")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        create_section, create_layout = self._section("Masking", tab)
        self._masking_hint = QLabel(self._MASK_IDLE_HINT, create_section)
        self._masking_hint.setObjectName("editorHint")
        self._masking_hint.setWordWrap(True)
        create_layout.addWidget(self._masking_hint)
        tool_row = QHBoxLayout()
        tool_row.setSpacing(8)
        self.radial_tool_button = self._tool_toggle("Radial", create_section)
        self.linear_tool_button = self._tool_toggle("Linear", create_section)
        self.brush_tool_button = self._tool_toggle("Brush", create_section)
        self.luma_tool_button = self._action_button("Luminance Range", create_section)
        self.color_tool_button = self._tool_toggle("Color Range", create_section)
        self.radial_tool_button.clicked.connect(
            lambda checked=False: self._arm_base_tool("radial" if checked else None)
        )
        self.linear_tool_button.clicked.connect(
            lambda checked=False: self._arm_base_tool("linear-gradient" if checked else None)
        )
        self.brush_tool_button.clicked.connect(
            lambda checked=False: self.arm_brush_mask("add" if checked else None)
        )
        self.luma_tool_button.clicked.connect(self.add_luminance_range_mask)
        self.color_tool_button.clicked.connect(
            lambda checked=False: self.arm_color_range_mask() if checked else self._set_mask_tool(None)
        )
        tool_row.addWidget(self.radial_tool_button)
        tool_row.addWidget(self.linear_tool_button)
        tool_row.addWidget(self.brush_tool_button)
        create_layout.addLayout(tool_row)
        range_row = QHBoxLayout()
        range_row.setSpacing(8)
        range_row.addWidget(self.luma_tool_button)
        range_row.addWidget(self.color_tool_button)
        create_layout.addLayout(range_row)
        overlay_row = QHBoxLayout()
        self.overlay_check = QCheckBox("Show Overlay", create_section)
        self.overlay_check.setChecked(True)
        self.overlay_check.toggled.connect(lambda _on: self.mask_overlay_changed.emit())
        overlay_row.addWidget(self.overlay_check)
        overlay_row.addStretch(1)
        create_layout.addLayout(overlay_row)
        layout.addWidget(create_section)

        masks_section, masks_layout = self._section("Masks", tab)
        self.masks_list = QListWidget(masks_section)
        self.masks_list.setObjectName("editorList")
        self.masks_list.currentItemChanged.connect(self._handle_mask_selection_changed)
        masks_layout.addWidget(self.masks_list)
        submask_row = QHBoxLayout()
        submask_row.setSpacing(8)
        self.add_submask_button = self._action_button("Add to Mask", masks_section)
        self.add_submask_button.setToolTip(
            "Draw another shape into the selected mask's group — adjustments target the combined area."
        )
        self.add_submask_button.clicked.connect(lambda: self.add_submask("add"))
        self.subtract_submask_button = self._action_button("Subtract", masks_section)
        self.subtract_submask_button.setToolTip(
            "Draw a shape that carves out of the selected mask's group."
        )
        self.subtract_submask_button.clicked.connect(lambda: self.add_submask("subtract"))
        submask_row.addWidget(self.add_submask_button)
        submask_row.addWidget(self.subtract_submask_button)
        masks_layout.addLayout(submask_row)
        self.delete_mask_button = self._action_button("Delete Mask", masks_section)
        self.delete_mask_button.clicked.connect(self.delete_selected_mask)
        masks_layout.addWidget(self.delete_mask_button)
        layout.addWidget(masks_section)

        hidden_space = QWidget(tab)
        hidden_space.hide()
        self.space_id_edit = QLineEdit(hidden_space)
        self.space_id_edit.setText("space-source-full")
        self.space_width_spin = self._spin(hidden_space, 1, 200000, 1)
        self.space_height_spin = self._spin(hidden_space, 1, 200000, 1)
        self.add_space_button = QPushButton("Add Space", hidden_space)
        self.add_space_button.clicked.connect(self.add_coordinate_space)
        self.mask_space_combo = QComboBox(hidden_space)

        selected_section, selected_layout = self._section("Selected Mask", tab)
        self.mask_feather_spin = self._double_spin(selected_section, 0, 100, 50, decimals=1)
        self.mask_density_spin = self._double_spin(selected_section, 0, 100, 100, decimals=1)
        self.mask_invert_check = QCheckBox("Invert", selected_section)
        self.mask_feather_row = self._slider_spin_row(
            "Shape Feather", self.mask_feather_spin,
            minimum=0, maximum=100, scale=10, parent=selected_section,
        )
        self.mask_density_row = self._slider_spin_row(
            "Density", self.mask_density_spin,
            minimum=0, maximum=100, scale=10, parent=selected_section,
        )
        self._mask_control_rows = [self.mask_feather_row, self.mask_density_row]
        for row in self._mask_control_rows:
            selected_layout.addWidget(row)
        selected_layout.addWidget(self.mask_invert_check)
        self.mask_feather_spin.valueChanged.connect(self._handle_mask_control_changed)
        self.mask_density_spin.valueChanged.connect(self._handle_mask_control_changed)
        self.mask_invert_check.toggled.connect(self._handle_mask_control_changed)
        layout.addWidget(selected_section)

        range_section, range_layout = self._section("Range Mask", tab)
        self.range_hint = QLabel(
            "Select a luminance or color range mask to tune its selection.", range_section
        )
        self.range_hint.setObjectName("editorHint")
        self.range_hint.setWordWrap(True)
        range_layout.addWidget(self.range_hint)
        self.range_sample_label = QLabel("", range_section)
        self.range_sample_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.range_sample_label.setMinimumHeight(26)
        range_layout.addWidget(self.range_sample_label)
        self.range_low_spin = self._spin(range_section, 0, 255, 0)
        self.range_high_spin = self._spin(range_section, 0, 255, 255)
        self.range_tolerance_spin = self._spin(range_section, 1, 255, 45)
        self.range_feather_spin = self._spin(range_section, 0, 255, 20)
        self.range_low_row = self._slider_spin_row(
            "Luminance Low", self.range_low_spin, minimum=0, maximum=255, parent=range_section
        )
        self.range_high_row = self._slider_spin_row(
            "Luminance High", self.range_high_spin, minimum=0, maximum=255, parent=range_section
        )
        self.range_tolerance_row = self._slider_spin_row(
            "Color Tolerance", self.range_tolerance_spin, minimum=1, maximum=255, parent=range_section
        )
        self.range_feather_row = self._slider_spin_row(
            "Range Feather", self.range_feather_spin, minimum=0, maximum=255, parent=range_section
        )
        range_layout.addWidget(self.range_low_row)
        range_layout.addWidget(self.range_high_row)
        range_layout.addWidget(self.range_tolerance_row)
        range_layout.addWidget(self.range_feather_row)
        self.resample_color_button = self._action_button("Resample Color", range_section)
        self.resample_color_button.setToolTip("Click, then sample a new color from the photo.")
        self.resample_color_button.clicked.connect(self.resample_selected_color_range)
        range_layout.addWidget(self.resample_color_button)
        for spin in (
            self.range_low_spin,
            self.range_high_spin,
            self.range_tolerance_spin,
            self.range_feather_spin,
        ):
            spin.valueChanged.connect(self._handle_range_control_changed)
        layout.addWidget(range_section)

        local_section, local_layout = self._section("Mask Adjustments", tab)
        local_hint = QLabel("Applied only inside the selected mask.", local_section)
        local_hint.setObjectName("editorHint")
        local_hint.setWordWrap(True)
        local_layout.addWidget(local_hint)
        specs_by_key = {spec[0]: spec for spec in ADJUSTMENT_SPECS}
        self._mask_rows: dict[str, _AdjustmentRow] = {}
        for key in MASK_ADJUSTMENT_KEYS:
            row = _AdjustmentRow(*specs_by_key[key], parent=local_section)
            row.changed.connect(self._handle_mask_adjustment_changed)
            # While a local slider is actively dragged, hide the red overlay
            # so the underlying image change is visible; restore on release.
            row.slider.sliderPressed.connect(self._begin_mask_slider_drag)
            row.slider.sliderReleased.connect(self._end_mask_slider_drag)
            self._mask_rows[key] = row
            local_layout.addWidget(row)
        self.reset_mask_adjustments_button = self._action_button("Reset Mask Adjustments", local_section)
        self.reset_mask_adjustments_button.clicked.connect(self.reset_mask_adjustments)
        local_layout.addWidget(self.reset_mask_adjustments_button)
        layout.addWidget(local_section)

        brush_section, brush_layout = self._section("Brush", tab)
        self.brush_size_spin = self._spin(brush_section, 1, 500, 25)
        self.brush_flow_spin = self._spin(brush_section, 0, 100, 100)
        brush_layout.addWidget(self._slider_spin_row("Size", self.brush_size_spin, minimum=1, maximum=500, parent=brush_section))
        brush_layout.addWidget(self._slider_spin_row("Flow", self.brush_flow_spin, minimum=0, maximum=100, parent=brush_section))
        self.brush_size_spin.valueChanged.connect(lambda _value: self.mask_overlay_changed.emit())
        self.brush_flow_spin.valueChanged.connect(lambda _value: self.mask_overlay_changed.emit())
        brush_row = QHBoxLayout()
        add_brush_button = self._action_button("Add", brush_section)
        subtract_brush_button = self._action_button("Subtract", brush_section)
        add_brush_button.clicked.connect(lambda: self.arm_brush_mask("add"))
        subtract_brush_button.clicked.connect(lambda: self.arm_brush_mask("subtract"))
        brush_row.addWidget(add_brush_button)
        brush_row.addWidget(subtract_brush_button)
        brush_layout.addLayout(brush_row)
        layout.addWidget(brush_section)

        refine_section, refine_layout = self._section("Refine Mask", tab)
        self.refine_low_spin = self._spin(refine_section, 0, 255, 0)
        self.refine_high_spin = self._spin(refine_section, 0, 255, 255)
        self.refine_pixels_spin = self._spin(refine_section, -500, 500, 0)
        refine_layout.addWidget(self._slider_spin_row("Luma Low", self.refine_low_spin, minimum=0, maximum=255, parent=refine_section))
        refine_layout.addWidget(self._slider_spin_row("Luma High", self.refine_high_spin, minimum=0, maximum=255, parent=refine_section))
        refine_layout.addWidget(self._slider_spin_row("Expand / Contract", self.refine_pixels_spin, minimum=-500, maximum=500, parent=refine_section))
        refine_row = QHBoxLayout()
        self.add_luma_refine_button = self._action_button("Luma", refine_section)
        self.add_bounds_refine_button = self._action_button("Bounds", refine_section)
        self.add_luma_refine_button.clicked.connect(self.add_luma_refinement)
        self.add_bounds_refine_button.clicked.connect(self.add_bounds_refinement)
        refine_row.addWidget(self.add_luma_refine_button)
        refine_row.addWidget(self.add_bounds_refine_button)
        refine_layout.addLayout(refine_row)
        layout.addWidget(refine_section)

        asset_section, asset_layout = self._section("AI / Imported Masks", tab)
        asset_row = QHBoxLayout()
        self.add_painted_button = self._action_button("Import Brush PNG", asset_section)
        self.add_subject_button = self._action_button("Import Subject PNG", asset_section)
        self.add_painted_button.clicked.connect(self.add_painted_mask)
        self.add_subject_button.clicked.connect(self.add_subject_mask)
        asset_row.addWidget(self.add_painted_button)
        asset_row.addWidget(self.add_subject_button)
        asset_layout.addLayout(asset_row)
        layout.addWidget(asset_section)
        layout.addStretch(1)
        scroll.setWidget(tab)
        return scroll

    def _build_session_tab(self) -> QWidget:
        scroll = QScrollArea(self)
        scroll.setObjectName("photoEditorScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        tab = QWidget(scroll)
        tab.setObjectName("photoEditorBody")
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        session_section, session_layout = self._section("Session", tab)
        self.session_summary = QPlainTextEdit(session_section)
        self.session_summary.setObjectName("editorText")
        self.session_summary.setReadOnly(True)
        session_layout.addWidget(self.session_summary)
        row = QHBoxLayout()
        self.create_session_button = QPushButton("Create", session_section)
        self.validate_button = QPushButton("Validate", session_section)
        self.export_xmp_button = QPushButton("Export XMP", session_section)
        self.reload_session_button = QPushButton("Reload", session_section)
        self.create_session_button.clicked.connect(self.create_session)
        self.validate_button.clicked.connect(self.validate_current_session)
        self.export_xmp_button.clicked.connect(self.export_current_xmp)
        self.reload_session_button.clicked.connect(self.reload_session)
        row.addWidget(self.create_session_button)
        row.addWidget(self.validate_button)
        row.addWidget(self.export_xmp_button)
        row.addWidget(self.reload_session_button)
        session_layout.addLayout(row)
        layout.addWidget(session_section)
        layout.addStretch(1)
        scroll.setWidget(tab)
        return scroll

    def set_image(self, source_path: str | Path | None) -> None:
        rejected_editor_asset = bool(source_path and is_editor_asset_path(source_path))
        if rejected_editor_asset:
            source_path = None
        if self._range_update_timer.isActive():
            self._regenerate_selected_range_mask()
        if self._mask_commit_timer.isActive():
            self._flush_mask_commit()
        self._pending_parent_id = None
        self._pending_combine = "add"
        if not source_path:
            self._source_path = None
            self._session_path = None
            self._session = None
            self._recipe = EditRecipe()
            self.subtitle_label.setText(
                "Generated mask asset" if rejected_editor_asset else "No image selected"
            )
            self._sync_rows_from_recipe()
            self._sync_enabled()
            self._refresh_session_views()
            self.recipe_changed.emit(self._recipe)
            if rejected_editor_asset:
                self._set_status("Generated mask assets cannot be edited as source photos")
            return

        path = Path(source_path)
        if self._source_path is not None and path == self._source_path:
            return
        self._source_path = path
        self._session_path = default_session_path(path)
        self._recipe = self._load_recipe_for_path(path)
        self.subtitle_label.setText(path.name)
        self._sync_rows_from_recipe()
        self._sync_enabled()
        self._refresh_session_views()
        self.recipe_changed.emit(self._recipe)

    def reset_recipe(self) -> None:
        self._recipe = EditRecipe()
        self._sync_rows_from_recipe()
        self.status_label.setText("Adjustments reset")
        self.status_changed.emit("Adjustments reset")
        self.recipe_changed.emit(self._recipe)

    def save_sidecar(self) -> None:
        if self._source_path is None:
            return
        try:
            session_path = self._persist_current_edits()
        except Exception as exc:
            self._set_status(f"Could not save edits: {exc}")
            return
        self._set_status("Saved edits")

    def save_copy(self) -> None:
        if self._source_path is None or self._copy_save_busy:
            return
        default_path = default_save_copy_path(self._source_path)
        selected_filter = selected_save_copy_filter(self._source_path)
        chosen_path, chosen_filter = QFileDialog.getSaveFileName(
            self,
            "Save Edited Copy",
            str(default_path),
            SAVE_COPY_FILTER_TEXT,
            selected_filter,
        )
        if not chosen_path:
            return
        target_path = normalize_save_copy_path(chosen_path, chosen_filter)
        try:
            validate_save_copy_paths(self._source_path, target_path)
            self._persist_current_edits()
        except Exception as exc:
            self._set_status(f"Could not save copy: {exc}")
            return

        self._copy_save_busy = True
        self._sync_enabled()
        self._set_status(f"Saving {target_path.name}...")
        self.save_copy_requested.emit(
            str(target_path),
            str(self._source_path),
            self._recipe,
            deepcopy(self.masked_adjustments()),
        )

    def finish_save_copy(self, target_path: str, error: str = "") -> None:
        self._copy_save_busy = False
        self._sync_enabled()
        if error:
            self._set_status(f"Could not save copy: {error}")
            return
        self._set_status(f"Saved copy {Path(target_path).name}")

    def _persist_current_edits(self) -> Path:
        if self._source_path is None:
            raise SessionError("no image selected")
        if self._range_update_timer.isActive():
            self._regenerate_selected_range_mask()
        if self._mask_commit_timer.isActive():
            self._flush_mask_commit()
        session_path = self._save_recipe_to_session(self._source_path, self._recipe)
        self._session_path = session_path
        self._session = load_session(session_path)
        self._refresh_session_views()
        self.saved.emit(str(session_path))
        return session_path

    def _handle_adjustment_changed(self, key: str, value: float) -> None:
        # The recipe_changed emit runs the preview render synchronously, so this
        # span measures the full slider-tick cost from the Adjust tab.
        with perf_logger().span("editslider.adjust_slider", key=key):
            data = asdict(self._recipe)
            data[key] = value
            self._recipe = EditRecipe.from_dict(data)
            self.recipe_changed.emit(self._recipe)

    def _sync_rows_from_recipe(self) -> None:
        data = asdict(self._recipe)
        for key, row in self._rows.items():
            row.set_value(float(data.get(key) or 0.0))

    def _sync_enabled(self) -> None:
        enabled = self._source_path is not None
        for row in self._rows.values():
            row.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.save_copy_button.setEnabled(enabled and not self._copy_save_busy)
        for widget_name in (
            "editor_stack",
            "create_session_button",
            "validate_button",
            "export_xmp_button",
            "reload_session_button",
            "add_space_button",
            "radial_tool_button",
            "linear_tool_button",
            "add_submask_button",
            "subtract_submask_button",
            "delete_mask_button",
            "add_luma_refine_button",
            "add_bounds_refine_button",
            "add_painted_button",
            "add_subject_button",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _spin(self, parent: QWidget, minimum: int, maximum: int, value: int) -> QSpinBox:
        spin = QSpinBox(parent)
        spin.setRange(minimum, maximum)
        spin.setValue(value)
        return spin

    def _double_spin(
        self,
        parent: QWidget,
        minimum: float,
        maximum: float,
        value: float,
        *,
        decimals: int,
    ) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(parent)
        spin.setRange(minimum, maximum)
        spin.setDecimals(decimals)
        spin.setValue(value)
        spin.setSingleStep(1.0 if decimals == 0 else 0.1)
        return spin

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)
        self.status_changed.emit(message)

    def _load_session(self) -> dict[str, Any] | None:
        if self._session_path is None or not self._session_path.exists():
            self._session = None
            return None
        session = load_session(self._session_path)
        validate_session(session, session_path=self._session_path)
        self._session = session
        return session

    def _ensure_session(self) -> tuple[Path, dict[str, Any]]:
        if self._source_path is None:
            raise SessionError("no image selected")
        if self._range_update_timer.isActive():
            self._regenerate_selected_range_mask()
        # Unsaved debounced mask edits must land before we reload from disk,
        # or the reload would silently revert them.
        if self._mask_commit_timer.isActive():
            self._flush_mask_commit()
        if self._session_path is None:
            self._session_path = default_session_path(self._source_path)
        if self._session_path.exists():
            session = load_session(self._session_path)
        else:
            self._session_path, session = new_session(self._source_path, self._session_path)
        self._session = session
        return self._session_path, session

    def _write_session(self, session: dict[str, Any], message: str) -> None:
        if self._session_path is None:
            raise SessionError("no session path")
        logger = perf_logger()
        with logger.span("editslider.write_session"):
            selected_mask_id = self._selected_mask_id()
            with logger.span("editslider.write_validate"):
                validate_session(session, session_path=self._session_path)
            with logger.span("editslider.write_save"):
                save_session(self._session_path, session)
            with logger.span("editslider.write_reload"):
                self._session = load_session(self._session_path)
                self._recipe = recipe_from_session(self._session)
            self._sync_rows_from_recipe()
            with logger.span("editslider.write_refresh_views"):
                self._refresh_session_views(selected_mask_id=selected_mask_id)
            self.recipe_changed.emit(self._recipe)
            self._set_status(message)
            self.saved.emit(str(self._session_path))

    def _refresh_session_views(self, *, selected_mask_id: str | None = None) -> None:
        # Rebuilding the mask list clears it, which would fire currentItemChanged
        # and momentarily sync controls to "no mask" — disabling the adjustment
        # sliders. If this runs mid-drag (the debounced commit fires while a
        # local slider is held), disabling the held slider drops its grab and
        # emits a spurious sliderReleased. Block the list's signals across the
        # rebuild; the explicit _sync_mask_controls at the end does the sync.
        list_blocker = QSignalBlocker(self.masks_list)
        session = None
        if self._session_path is not None and self._session_path.exists():
            try:
                session = self._load_session()
            except Exception as exc:
                self.session_summary.setPlainText(f"Sidecar load failed:\n{exc}")
                self.masks_list.clear()
                list_blocker.unblock()
                self._sync_mask_controls(None)
                self.mask_overlay_changed.emit()
                return
        self.masks_list.clear()
        self._refresh_reference_combos(session)
        if session is None:
            self.session_summary.setPlainText("No sidecar for this image.")
            list_blocker.unblock()
            self._sync_mask_controls(None)
            self.mask_overlay_changed.emit()
            return
        masks = session.get("masks", [])
        ordered: list[tuple[dict[str, Any], bool]] = []
        for mask in masks:
            if mask.get("parentId"):
                continue
            ordered.append((mask, False))
            ordered.extend(
                (child, True) for child in masks if child.get("parentId") == mask.get("id")
            )
        listed = {id(mask) for mask, _is_child in ordered}
        ordered.extend((mask, False) for mask in masks if id(mask) not in listed)
        for mask, is_child in ordered:
            label = _friendly_mask_label(mask)
            if is_child:
                marker = "−" if str(mask.get("combine", "add")) == "subtract" else "↳"
                label = f"      {marker} {label}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, mask.get("id"))
            item.setToolTip(str(mask.get("id")))
            self.masks_list.addItem(item)
            if selected_mask_id and mask.get("id") == selected_mask_id:
                self.masks_list.setCurrentItem(item)
        list_blocker.unblock()
        source = session.get("source", {})
        summary = {
            "session": str(self._session_path),
            "source": source.get("lastKnownPath") or source.get("fileName"),
            "coordinateSpaces": session.get("coordinateSpaces", []),
            "masks": session.get("masks", []),
            "operations": session.get("operations", []),
            "pipeline": session.get("pipeline"),
        }
        self.session_summary.setPlainText(_format_json(summary))
        self._sync_space_defaults(session)
        self._sync_mask_controls(self._selected_mask_dict())
        self.mask_overlay_changed.emit()

    def _refresh_reference_combos(self, session: dict[str, Any] | None) -> None:
        space_values: list[str] = []
        if session is not None:
            space_values.extend(sorted(space_id for space_id in space_ids(session) if space_id))
        self._fill_combo(self.mask_space_combo, space_values, space_values[0] if space_values else "")

    def _fill_combo(self, combo: QComboBox, values: list[str], current: str) -> None:
        with QSignalBlocker(combo):
            combo.clear()
            for value in values:
                label = "global" if value == "__global__" else ("none" if value == "" else value)
                combo.addItem(label, value)
            index = combo.findData(current)
            if index >= 0:
                combo.setCurrentIndex(index)

    def _sync_space_defaults(self, session: dict[str, Any]) -> None:
        spaces = session.get("coordinateSpaces", [])
        if not spaces:
            return
        source_space = spaces[0]
        self.space_id_edit.setText(source_space.get("id", "space-source-full"))
        width = source_space.get("sourceWidth") or 1
        height = source_space.get("sourceHeight") or 1
        self.space_width_spin.setValue(max(1, int(width)))
        self.space_height_spin.setValue(max(1, int(height)))

    def _selected_mask_id(self) -> str | None:
        item = self.masks_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _selected_mask_space(self) -> str:
        value = self.mask_space_combo.currentData()
        if not value and self._session is not None:
            for space in self._session.get("coordinateSpaces", []):
                space_id = space.get("id")
                if space_id:
                    return str(space_id)
        if not value:
            raise SessionError("coordinate space is required")
        return str(value)

    def create_session(self) -> None:
        try:
            session_path, _session = self._ensure_session()
            self._refresh_session_views()
        except Exception as exc:
            self._set_status(f"Create failed: {exc}")
            return
        self._set_status(f"Created {session_path.name}")

    def reload_session(self) -> None:
        if self._source_path is None:
            return
        self._recipe = self._load_recipe_for_path(self._source_path)
        self._sync_rows_from_recipe()
        self._refresh_session_views()
        self.recipe_changed.emit(self._recipe)

    def validate_current_session(self) -> None:
        try:
            path, session = self._ensure_session()
            validate_session(session, session_path=path, strict=True)
        except Exception as exc:
            self._set_status(f"Invalid: {exc}")
            return
        self._set_status("Session valid")

    def export_current_xmp(self) -> None:
        try:
            path, _session = self._ensure_session()
            xmp_path = export_xmp(path, None)
        except Exception as exc:
            self._set_status(f"XMP export failed: {exc}")
            return
        self._set_status(f"Exported {Path(xmp_path).name}")

    def add_coordinate_space(self) -> None:
        try:
            _path, session = self._ensure_session()
            add_space(
                session,
                {
                    "id": self.space_id_edit.text().strip() or "space-source-full",
                    "sourceWidth": self.space_width_spin.value(),
                    "sourceHeight": self.space_height_spin.value(),
                    "cropInEffect": None,
                },
            )
            self._write_session(session, "Saved coordinate space")
        except Exception as exc:
            self._set_status(f"Space failed: {exc}")

    # -- on-canvas mask editing ------------------------------------------------
    def mask_overlay_state(self) -> dict[str, Any]:
        """Snapshot for the on-canvas MaskOverlay: the selected component's
        params (handles), the whole group's components (union overlay), tool
        mode, and visibility flags."""
        mask = self._selected_mask_dict()
        mask_type: str | None = None
        params: dict[str, Any] | None = None
        components: list[tuple[str, dict[str, Any]]] | None = None
        selected_index: int | None = None
        if mask is not None:
            root_id = str(self._mask_root(mask).get("id"))
            shape_members = [
                member
                for member in self._group_members(root_id)
                if member.get("type") in (*self.MASK_SHAPE_TYPES, "bitmap")
            ]
            components = [
                (
                    str(member.get("type")),
                    self._component_params(member),
                    "add" if member.get("id") == root_id else str(member.get("combine", "add")),
                )
                for member in shape_members
            ] or None
            if mask.get("type") in self.MASK_SHAPE_TYPES:
                mask_type = str(mask.get("type"))
                params = dict(mask.get("params") or {})
            elif mask.get("type") == "bitmap":
                mask_type = "bitmap"
                params = self._component_params(mask)
            selected_index = next(
                    (
                        index
                        for index, member in enumerate(shape_members)
                        if member.get("id") == mask.get("id")
                    ),
                    None,
                )
        masks_tab = self.editor_stack.currentIndex() == 1
        interactive = masks_tab and self._source_path is not None
        return {
            "interactive": interactive,
            "show_overlay": (
                interactive
                and self.overlay_check.isChecked()
                and not self._overlay_suppressed_for_drag
            ),
            "create_mode": self._mask_create_mode if interactive else None,
            "create_combine": self._pending_combine if interactive else "add",
            "brush_mode": self._brush_paint_mode if interactive else None,
            "brush_size": self.brush_size_spin.value() if hasattr(self, "brush_size_spin") else 25,
            "brush_flow": self.brush_flow_spin.value() if hasattr(self, "brush_flow_spin") else 100,
            "mask_type": mask_type,
            "params": params,
            "components": components,
            "selected_index": selected_index,
            "source_size": self._mask_source_size(),
        }

    def _mask_source_size(self) -> tuple[int, int] | None:
        if self._session:
            spaces = self._session.get("coordinateSpaces") or []
            if spaces:
                width = spaces[0].get("sourceWidth")
                height = spaces[0].get("sourceHeight")
                if width and height:
                    return int(width), int(height)
        if self._source_path is None:
            return None
        if self._source_size_cache is not None and self._source_size_cache[0] == self._source_path:
            return self._source_size_cache[1]
        try:
            width, height = image_dimensions(self._source_path)
        except Exception:
            return None
        self._source_size_cache = (self._source_path, (int(width), int(height)))
        return self._source_size_cache[1]

    def _selected_mask_dict(self) -> dict[str, Any] | None:
        return self._mask_by_id(self._selected_mask_id())

    def _mask_by_id(self, mask_id: str | None) -> dict[str, Any] | None:
        if not mask_id or self._session is None:
            return None
        for mask in self._session.get("masks", []):
            if mask.get("id") == mask_id:
                return mask
        return None

    def _mask_root(self, mask: dict[str, Any]) -> dict[str, Any]:
        parent = self._mask_by_id(mask.get("parentId"))
        return parent if parent is not None else mask

    def _group_members(self, root_id: str) -> list[dict[str, Any]]:
        """The root mask followed by its children, in session order."""
        if self._session is None:
            return []
        members = [mask for mask in self._session.get("masks", []) if mask.get("id") == root_id]
        members.extend(
            mask for mask in self._session.get("masks", []) if mask.get("parentId") == root_id
        )
        return members

    def _group_components(self, root_id: str) -> list[tuple[str, dict[str, Any], str]]:
        out: list[tuple[str, dict[str, Any], str]] = []
        for mask in self._group_members(root_id):
            if mask.get("type") not in (*self.MASK_SHAPE_TYPES, "bitmap"):
                continue
            combine = "add" if mask.get("id") == root_id else str(mask.get("combine", "add"))
            out.append((str(mask.get("type")), self._component_params(mask), combine))
        return out

    def _component_params(self, mask: dict[str, Any]) -> dict[str, Any]:
        params = dict(mask.get("params") or {})
        if mask.get("type") == "bitmap":
            asset_path = self._bitmap_asset_path(mask)
            if asset_path is not None:
                params["assetPath"] = str(asset_path)
        return params

    def _bitmap_asset_path(self, mask: dict[str, Any]) -> Path | None:
        if self._session is None or self._session_path is None:
            return None
        asset_id = mask.get("assetId") or mask.get("cacheAssetId")
        if not asset_id:
            return None
        for asset in self._session.get("assets", {}).get("bitmapMasks", []):
            if asset.get("id") == asset_id:
                return self._session_path.parent / str(asset.get("path", ""))
        return None

    def add_submask(self, combine: str = "add") -> None:
        mask = self._selected_mask_dict()
        if mask is None:
            self._set_status("Select a mask first")
            return
        root = self._mask_root(mask)
        self._pending_parent_id = str(root.get("id"))
        self._pending_combine = "subtract" if combine == "subtract" else "add"
        if self._mask_create_mode is None:
            self._set_mask_tool("radial")
        verb = "carves out of" if self._pending_combine == "subtract" else "joins"
        self._set_status(f"Drag on the photo — the new shape {verb} the selected mask's group")

    @staticmethod
    def _normalized_mask_params(mask_type: str, params: dict[str, Any]) -> dict[str, Any]:
        out = dict(params)
        coord_keys = ("cx", "cy", "rx", "ry") if mask_type == "radial" else ("x1", "y1", "x2", "y2")
        for key in coord_keys:
            out[key] = int(round(float(out.get(key, 0))))
        if mask_type == "radial":
            out["rx"] = max(2, out["rx"])
            out["ry"] = max(2, out["ry"])
            out["angle"] = round(float(out.get("angle", 0.0)) % 360.0, 1)
        out["feather"] = round(max(0.0, min(100.0, float(out.get("feather", 65.0)))), 1)
        out["density"] = round(max(0.0, min(100.0, float(out.get("density", 100.0)))), 1)
        out["invert"] = bool(out.get("invert", False))
        return out

    def handle_overlay_mask_created(self, mask_type: str, params: dict[str, Any]) -> None:
        parent_id = self._pending_parent_id
        combine = self._pending_combine
        self._pending_parent_id = None
        self._pending_combine = "add"
        try:
            _path, session = self._ensure_session()
            spaces = sorted(space_id for space_id in space_ids(session) if space_id)
            if not spaces:
                raise SessionError("session has no coordinate space")
            if parent_id is not None and parent_id not in mask_ids(session):
                parent_id = None
            mask_id = _next_id(mask_ids(session), "mask")
            mask: dict[str, Any] = {
                "id": mask_id,
                "type": mask_type,
                "coordinateSpaceId": spaces[0],
                "params": self._normalized_mask_params(mask_type, params),
            }
            if parent_id is not None:
                mask["parentId"] = parent_id
                if combine == "subtract":
                    mask["combine"] = "subtract"
            label = "Added mask"
            if parent_id:
                label = "Subtracted submask" if combine == "subtract" else "Added submask"
            upsert_mask(session, mask)
            self._write_session(session, label)
            self._select_mask_in_list(mask_id)
            # One-shot tool: disarm after placing so the next click on the new
            # mask moves it instead of drawing another.
            self._set_mask_tool(None)
        except Exception as exc:
            self._set_status(f"Mask failed: {exc}")

    def handle_overlay_mask_edited(self, params: dict[str, Any]) -> None:
        mask = self._selected_mask_dict()
        if mask is None or mask.get("type") not in self.MASK_SHAPE_TYPES:
            return
        mask["params"] = self._normalized_mask_params(str(mask.get("type")), params)
        self._sync_mask_controls(mask)
        self._mask_commit_timer.start()

    def handle_overlay_commit(self) -> None:
        self._flush_mask_commit()

    def _flush_mask_commit(self) -> None:
        self._mask_commit_timer.stop()
        if self._session is None or self._session_path is None:
            return
        try:
            # Debounced ~400ms after a mask-slider move — the "after first move"
            # cost suspect (save + reload + list rebuild + a second render).
            with perf_logger().span("editslider.commit_flush"):
                self._write_session(self._session, "Mask updated")
        except Exception as exc:
            self._set_status(f"Mask save failed: {exc}")

    def _select_mask_in_list(self, mask_id: str) -> None:
        for row in range(self.masks_list.count()):
            item = self.masks_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == mask_id:
                self.masks_list.setCurrentItem(item)
                return

    def _handle_mask_selection_changed(self, *_args) -> None:
        self._sync_mask_controls(self._selected_mask_dict())
        self.mask_overlay_changed.emit()

    def _sync_mask_controls(self, mask: dict[str, Any] | None) -> None:
        is_shape = mask is not None and mask.get("type") in self.MASK_SHAPE_TYPES
        is_bitmap = mask is not None and mask.get("type") == "bitmap"
        self.mask_feather_row.setEnabled(is_shape)
        self.mask_density_row.setEnabled(is_shape or is_bitmap)
        self.mask_invert_check.setEnabled(is_shape or is_bitmap)
        params = (mask or {}).get("params") or {}
        style = str((mask or {}).get("uiStyle") or "")
        is_luma_range = style == "luminance-range"
        is_color_range = style == "color-range"
        is_range = is_luma_range or is_color_range
        local_recipe = EditRecipe()
        if mask is not None and self._session is not None:
            local_recipe = recipe_for_mask(self._session, str(self._mask_root(mask).get("id")))
        local_values = asdict(local_recipe)
        has_mask = mask is not None
        self._updating_mask_controls = True
        try:
            self.mask_feather_spin.setValue(float(params.get("feather", 50.0)))
            self.mask_density_spin.setValue(float(params.get("density", 100.0)))
            self.mask_invert_check.setChecked(bool(params.get("invert", False)))
            self.range_low_spin.setValue(int(params.get("low", 0)))
            self.range_high_spin.setValue(int(params.get("high", 255)))
            self.range_tolerance_spin.setValue(int(params.get("tolerance", 45)))
            self.range_feather_spin.setValue(int(params.get("feather", 20 if is_luma_range else 35)))
            for key, row in self._mask_rows.items():
                row.set_value(float(local_values.get(key) or 0.0))
                row.setEnabled(has_mask)
        finally:
            self._updating_mask_controls = False
        self.reset_mask_adjustments_button.setEnabled(has_mask)
        self.range_low_row.setVisible(is_luma_range)
        self.range_high_row.setVisible(is_luma_range)
        self.range_tolerance_row.setVisible(is_color_range)
        self.range_feather_row.setVisible(is_range)
        self.resample_color_button.setVisible(is_color_range)
        self.range_sample_label.setVisible(is_color_range)
        if is_color_range:
            sample = list(params.get("sample") or (0, 0, 0))
            if len(sample) == 3:
                red, green, blue = (max(0, min(255, int(value))) for value in sample)
                text_color = "#111111" if (red * 299 + green * 587 + blue * 114) >= 150000 else "#ffffff"
                self.range_sample_label.setText(f"Sample  RGB {red}, {green}, {blue}")
                self.range_sample_label.setStyleSheet(
                    f"background: rgb({red}, {green}, {blue}); color: {text_color}; "
                    "border: 1px solid #555555; border-radius: 3px; font-weight: 600;"
                )
        else:
            self.range_sample_label.clear()
            self.range_sample_label.setStyleSheet("")
        self.range_hint.setText(
            "Tune the tonal band selected by this luminance mask."
            if is_luma_range else
            "Tune the sampled color distance, or resample directly from the photo."
            if is_color_range else
            "Select a luminance or color range mask to tune its selection."
        )
        self.range_hint.setEnabled(is_range)

    def _handle_mask_control_changed(self, *_args) -> None:
        if self._updating_mask_controls:
            return
        mask = self._selected_mask_dict()
        if mask is None or mask.get("type") not in (*self.MASK_SHAPE_TYPES, "bitmap"):
            return
        params = mask.setdefault("params", {})
        if mask.get("type") in self.MASK_SHAPE_TYPES:
            params["feather"] = round(self.mask_feather_spin.value(), 1)
        params["density"] = round(self.mask_density_spin.value(), 1)
        params["invert"] = self.mask_invert_check.isChecked()
        self.mask_overlay_changed.emit()
        if self._mask_has_local_adjustments(mask):
            # Geometry affects where local adjustments land — recomposite live.
            self.recipe_changed.emit(self._recipe)
        self._mask_commit_timer.start()

    def _handle_range_control_changed(self, *_args) -> None:
        if self._updating_mask_controls:
            return
        mask = self._selected_mask_dict()
        if mask is None or mask.get("type") != "bitmap":
            return
        style = str(mask.get("uiStyle") or "")
        if style not in ("luminance-range", "color-range"):
            return
        params = mask.setdefault("params", {})
        if style == "luminance-range":
            low = self.range_low_spin.value()
            high = self.range_high_spin.value()
            if low >= high:
                if self.sender() is self.range_low_spin:
                    high = min(255, low + 1)
                    if high == low:
                        low = high - 1
                else:
                    low = max(0, high - 1)
                    if low == high:
                        high = low + 1
                with QSignalBlocker(self.range_low_spin), QSignalBlocker(self.range_high_spin):
                    self.range_low_spin.setValue(low)
                    self.range_high_spin.setValue(high)
            params["low"] = low
            params["high"] = high
        else:
            params["tolerance"] = self.range_tolerance_spin.value()
        params["feather"] = self.range_feather_spin.value()
        self._pending_range_mask_id = str(mask.get("id"))
        self._range_update_timer.start()

    def _regenerate_selected_range_mask(self) -> None:
        self._range_update_timer.stop()
        mask_id = self._pending_range_mask_id
        self._pending_range_mask_id = None
        mask = self._mask_by_id(mask_id)
        if mask is None or self._source_path is None or self._session is None:
            return
        style = str(mask.get("uiStyle") or "")
        params = mask.get("params") or {}
        if style not in ("luminance-range", "color-range"):
            return
        try:
            with open_image(self._source_path) as image:
                base = Image.new("L", image.size, 255)
                if style == "luminance-range":
                    rendered = refine_luminance_range(
                        image,
                        base,
                        int(params.get("low", 0)),
                        int(params.get("high", 255)),
                        feather=int(params.get("feather", 20)),
                        invert=False,
                    )
                else:
                    sample = tuple(int(value) for value in params.get("sample", (0, 0, 0)))
                    if len(sample) != 3:
                        raise SessionError("color range sample is missing")
                    rendered = refine_color_range(
                        image,
                        base,
                        sample,
                        tolerance=int(params.get("tolerance", 45)),
                        feather=int(params.get("feather", 35)),
                        invert=False,
                    )
            asset_path = self._bitmap_asset_path(mask)
            if asset_path is None:
                raise SessionError("bitmap asset missing")
            rendered.save(asset_path)
            self._write_session(self._session, "Updated range mask")
        except Exception as exc:
            self._set_status(f"Range mask update failed: {exc}")

    def _mask_has_local_adjustments(self, mask: dict[str, Any] | None) -> bool:
        if mask is None or self._session is None:
            return False
        recipe = recipe_for_mask(self._session, str(self._mask_root(mask).get("id")))
        return any(value not in (0, 0.0, None) for value in asdict(recipe).values())

    def _handle_mask_adjustment_changed(self, key: str, value: float) -> None:
        if self._updating_mask_controls:
            return
        mask = self._selected_mask_dict()
        if mask is None or self._session is None:
            return
        logger = perf_logger()
        with logger.span("editslider.mask_slider", key=key):
            values = {row_key: row.slider.value() / row.scale for row_key, row in self._mask_rows.items()}
            values[key] = value
            # Adjustments always live on the group root — the union of the whole
            # group is what they apply through.
            root_id = str(self._mask_root(mask).get("id"))
            with logger.span("editslider.replace_ops"):
                replace_mask_operations(self._session, root_id, EditRecipe.from_dict(values))
            self.recipe_changed.emit(self._recipe)
            self._mask_commit_timer.start()

    def _begin_mask_slider_drag(self) -> None:
        if not self._overlay_suppressed_for_drag:
            self._overlay_suppressed_for_drag = True
            self.mask_overlay_changed.emit()

    def _end_mask_slider_drag(self) -> None:
        if self._overlay_suppressed_for_drag:
            self._overlay_suppressed_for_drag = False
            self.mask_overlay_changed.emit()

    def reset_mask_adjustments(self) -> None:
        mask = self._selected_mask_dict()
        if mask is None or self._session is None:
            return
        replace_mask_operations(self._session, str(self._mask_root(mask).get("id")), EditRecipe())
        self._sync_mask_controls(mask)
        self.recipe_changed.emit(self._recipe)
        self._mask_commit_timer.start()

    def masked_adjustments(
        self,
    ) -> list[tuple[list[tuple[str, dict[str, Any]]], tuple[int, int], EditRecipe]]:
        """Mask groups with non-default local adjustments, for live preview
        compositing: (components, source_size, recipe). A group is a root mask
        plus its parentId children; its adjustments apply through the union of
        the components. Bitmap and subject members are excluded — their ops
        still apply at export, but the preview cannot rebuild their strength
        fields from geometry."""
        if self._session is None:
            return []
        source_size = self._mask_source_size()
        if source_size is None:
            return []
        out: list[tuple[list[tuple[str, dict[str, Any]]], tuple[int, int], EditRecipe]] = []
        for mask in self._session.get("masks", []):
            if mask.get("parentId"):
                continue
            root_id = str(mask.get("id"))
            recipe = recipe_for_mask(self._session, root_id)
            if not any(value not in (0, 0.0, None) for value in asdict(recipe).values()):
                continue
            components = self._group_components(root_id)
            if not components:
                continue
            out.append((components, source_size, recipe))
        return out

    def delete_selected_mask(self) -> None:
        mask_id = self._selected_mask_id()
        if not mask_id:
            self._set_status("Select a mask first")
            return
        try:
            _path, session = self._ensure_session()
            target = next(
                (mask for mask in session.get("masks", []) if mask.get("id") == mask_id), None
            )
            if target is None:
                raise SessionError(f"mask not found: {mask_id}")
            # Deleting a root takes its children; either way the doomed masks'
            # local adjustments go with them.
            doomed = {mask_id}
            if not target.get("parentId"):
                doomed.update(
                    str(mask.get("id"))
                    for mask in session.get("masks", [])
                    if mask.get("parentId") == mask_id
                )
            session["operations"] = [
                op for op in session.get("operations", []) if op.get("maskId") not in doomed
            ]
            for child_id in sorted(doomed - {mask_id}):
                remove_mask(session, child_id, force=False)
            remove_mask(session, mask_id, force=False)
            message = "Deleted mask group" if len(doomed) > 1 else f"Deleted mask {mask_id}"
            self._write_session(session, message)
        except Exception as exc:
            self._set_status(f"Delete mask failed: {exc}")

    def add_luma_refinement(self) -> None:
        self._add_mask_refinement(
            {
                "type": "luminance-range",
                "low": self.refine_low_spin.value(),
                "high": self.refine_high_spin.value(),
                "feather": 20,
                "invert": False,
            }
        )

    def add_bounds_refinement(self) -> None:
        self._add_mask_refinement({"type": "bounds", "pixels": self.refine_pixels_spin.value()})

    def _add_mask_refinement(self, refinement: dict[str, Any]) -> None:
        mask_id = self._selected_mask_id()
        if not mask_id:
            self._set_status("Select a mask first")
            return
        try:
            _path, session = self._ensure_session()
            for mask in session.get("masks", []):
                if mask.get("id") == mask_id:
                    mask.setdefault("refinements", []).append(refinement)
                    self._write_session(session, f"Refined {mask_id}")
                    return
            raise SessionError(f"mask not found: {mask_id}")
        except Exception as exc:
            self._set_status(f"Refine failed: {exc}")

    def _write_bitmap_mask_asset(
        self,
        session: dict[str, Any],
        asset_id: str,
        space_id: str,
        mask_image: Image.Image,
    ) -> Path:
        if self._session_path is None:
            raise SessionError("no session path")
        asset_dir = self._session_path.parent / session.get("assets", {}).get(
            "dir", asset_dir_for_session(self._session_path).name
        )
        asset_dir.mkdir(parents=True, exist_ok=True)
        target = asset_dir / f"{asset_id}.png"
        mask_image.convert("L").save(target)
        asset = {
            "id": asset_id,
            "path": f"{asset_dir.name}/{target.name}",
            "coordinateSpaceId": space_id,
        }
        assets = session.setdefault("assets", {}).setdefault("bitmapMasks", [])
        assets[:] = [item for item in assets if item.get("id") != asset_id]
        assets.append(asset)
        return target

    def _create_bitmap_mask(
        self,
        mask_image: Image.Image,
        *,
        style: str,
        params: dict[str, Any] | None = None,
        message: str,
    ) -> str:
        _path, session = self._ensure_session()
        space_id = self._selected_mask_space()
        mask_id = _next_id(mask_ids(session), "mask")
        self._write_bitmap_mask_asset(session, mask_id, space_id, mask_image)
        mask = {
            "id": mask_id,
            "type": "bitmap",
            "assetId": mask_id,
            "uiStyle": style,
            "params": params or {},
        }
        upsert_mask(session, mask)
        self._write_session(session, message)
        self._select_mask_in_list(mask_id)
        return mask_id

    def arm_brush_mask(self, mode: str | None = "add") -> None:
        if mode is None:
            self._brush_paint_mode = None
            self._set_mask_tool(None)
            return
        try:
            mask = self._selected_mask_dict()
            if mask is None or mask.get("type") != "bitmap" or mask.get("uiStyle") != "brush":
                source_size = self._mask_source_size()
                if source_size is None:
                    raise SessionError("source dimensions unavailable")
                mask_id = self._create_bitmap_mask(
                    Image.new("L", source_size, 0),
                    style="brush",
                    params={"brushSize": self.brush_size_spin.value(), "flow": self.brush_flow_spin.value()},
                    message="Created brush mask",
                )
                mask = self._mask_by_id(mask_id)
            self._brush_paint_mode = "subtract" if mode == "subtract" else "add"
            self._mask_create_mode = None
            with (
                QSignalBlocker(self.radial_tool_button),
                QSignalBlocker(self.linear_tool_button),
                QSignalBlocker(self.brush_tool_button),
                QSignalBlocker(self.color_tool_button),
            ):
                self.radial_tool_button.setChecked(False)
                self.linear_tool_button.setChecked(False)
                self.brush_tool_button.setChecked(True)
                self.color_tool_button.setChecked(False)
            self._set_status("Paint on the photo to edit the brush mask")
            self.mask_overlay_changed.emit()
        except Exception as exc:
            self._set_status(f"Brush mask failed: {exc}")

    def add_luminance_range_mask(self) -> None:
        try:
            if self._source_path is None:
                raise SessionError("no image selected")
            low = self.range_low_spin.value()
            high = self.range_high_spin.value()
            feather = self.range_feather_spin.value()
            with open_image(self._source_path) as image:
                base = Image.new("L", image.size, 255)
                mask = refine_luminance_range(
                    image,
                    base,
                    low,
                    high,
                    feather=feather,
                    invert=False,
                )
            self._create_bitmap_mask(
                mask,
                style="luminance-range",
                params={
                    "low": low,
                    "high": high,
                    "feather": feather,
                    "density": 100.0,
                    "invert": False,
                },
                message="Created luminance range mask",
            )
        except Exception as exc:
            self._set_status(f"Luminance mask failed: {exc}")

    def arm_color_range_mask(self) -> None:
        self._color_resample_mask_id = None
        self._pending_parent_id = None
        self._pending_combine = "add"
        self._brush_paint_mode = None
        self._set_mask_tool("color-range")

    def resample_selected_color_range(self) -> None:
        mask = self._selected_mask_dict()
        if mask is None or mask.get("uiStyle") != "color-range":
            self._set_status("Select a color range mask first")
            return
        self._color_resample_mask_id = str(mask.get("id"))
        self._brush_paint_mode = None
        self._set_mask_tool("color-range")
        self._set_status("Click the photo to sample a new color")

    def add_color_range_mask(self) -> None:
        self.arm_color_range_mask()

    def handle_overlay_source_clicked(self, x: float, y: float) -> None:
        if self._mask_create_mode != "color-range":
            return
        try:
            if self._source_path is None:
                raise SessionError("no image selected")
            with open_image(self._source_path) as image:
                rgb = image.convert("RGB")
                sample_xy = (
                    max(0, min(rgb.width - 1, int(round(x)))),
                    max(0, min(rgb.height - 1, int(round(y)))),
                )
                sample = rgb.getpixel(sample_xy)
                target = self._mask_by_id(self._color_resample_mask_id)
                tolerance = (
                    int((target.get("params") or {}).get("tolerance", 45))
                    if target is not None else self.range_tolerance_spin.value()
                )
                feather = (
                    int((target.get("params") or {}).get("feather", 35))
                    if target is not None else self.range_feather_spin.value()
                )
                base = Image.new("L", rgb.size, 255)
                rendered = refine_color_range(
                    rgb, base, sample, tolerance=tolerance, feather=feather, invert=False
                )
            if target is not None:
                params = target.setdefault("params", {})
                params.update({"sample": list(sample), "x": sample_xy[0], "y": sample_xy[1]})
                asset_path = self._bitmap_asset_path(target)
                if asset_path is None:
                    raise SessionError("bitmap asset missing")
                rendered.save(asset_path)
                self._write_session(self._session, "Resampled color range mask")
            else:
                self._create_bitmap_mask(
                    rendered,
                    style="color-range",
                    params={
                        "sample": list(sample),
                        "x": sample_xy[0],
                        "y": sample_xy[1],
                        "tolerance": tolerance,
                        "feather": feather,
                        "density": 100.0,
                        "invert": False,
                    },
                    message="Created color range mask",
                )
            self._color_resample_mask_id = None
            self._set_mask_tool(None)
        except Exception as exc:
            self._set_status(f"Color mask failed: {exc}")

    def handle_overlay_bitmap_edited(self, image) -> None:
        mask = self._selected_mask_dict()
        if mask is None or mask.get("type") != "bitmap" or self._session is None:
            return
        try:
            asset_path = self._bitmap_asset_path(mask)
            if asset_path is None:
                raise SessionError("bitmap asset missing")
            image.save(str(asset_path))
            self._set_status("Brush mask updated")
            self.mask_overlay_changed.emit()
            if self._mask_has_local_adjustments(mask):
                self.recipe_changed.emit(self._recipe)
        except Exception as exc:
            self._set_status(f"Brush save failed: {exc}")

    def add_painted_mask(self) -> None:
        self._add_bitmap_mask(subject=False)

    def add_subject_mask(self) -> None:
        self._add_bitmap_mask(subject=True)

    def _add_bitmap_mask(self, *, subject: bool) -> None:
        if self._session_path is None:
            try:
                self._ensure_session()
            except Exception as exc:
                self._set_status(f"Session failed: {exc}")
                return
        file_path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "Select mask PNG",
            str(self._source_path.parent if self._source_path is not None else Path.home()),
            "PNG Images (*.png)",
        )
        if not file_path:
            return
        try:
            _path, session = self._ensure_session()
            mask_id = _next_id(mask_ids(session), "mask")
            space_id = self._selected_mask_space()
            if subject:
                copy_bitmap_asset(self._session_path, session, f"{mask_id}-cache", space_id, Path(file_path))
                upsert_mask(
                    session,
                    {
                        "id": mask_id,
                        "type": "subject-select",
                        "coordinateSpaceId": space_id,
                        "model": {
                            "id": "manual-cache",
                            "version": "1",
                            "weightsHash": "manual-cache",
                        },
                        "cacheAssetId": f"{mask_id}-cache",
                    },
                )
            else:
                copy_bitmap_asset(self._session_path, session, mask_id, space_id, Path(file_path))
                upsert_mask(session, {"id": mask_id, "type": "bitmap", "assetId": mask_id, "uiStyle": "brush"})
            self._write_session(session, f"Saved mask {mask_id}")
        except Exception as exc:
            self._set_status(f"Bitmap mask failed: {exc}")

    def _load_recipe_for_path(self, path: Path) -> EditRecipe:
        session_path = default_session_path(path)
        if not session_path.exists():
            self.status_label.setText("No saved edits yet")
            return EditRecipe()
        try:
            session = load_session(session_path)
            validate_session(session, session_path=session_path)
            recipe = recipe_from_session(session)
        except Exception as exc:
            self.status_label.setText(f"Saved edits could not be loaded: {exc}")
            return EditRecipe()
        self.status_label.setText(f"Loaded {session_path.name}")
        return recipe

    def _save_recipe_to_session(self, path: Path, recipe: EditRecipe) -> Path:
        session_path = default_session_path(path)
        if session_path.exists():
            session = load_session(session_path)
        else:
            session_path, session = new_session(path, session_path)
        gui_op_types = {op_type for op_type, _param_key in SESSION_OPS.values()}
        preserved_ops = [
            op
            for op in session.get("operations", [])
            if op.get("maskId") is not None or op.get("type") not in gui_op_types
        ]
        session["operations"] = sorted(
            [*preserved_ops, *operations_from_recipe(recipe, existing_ids=operation_ids({"operations": preserved_ops}))],
            key=lambda op: _operation_order(op.get("type", "")),
        )
        validate_session(session, session_path=session_path)
        save_session(session_path, session)
        return session_path


def recipe_from_session(session: dict[str, Any]) -> EditRecipe:
    values: dict[str, Any] = {}
    for op in session.get("operations", []):
        if not op.get("enabled", True) or op.get("maskId") is not None:
            continue
        params = op.get("params") or {}
        for recipe_key, (op_type, param_key) in SESSION_OPS.items():
            if op.get("type") == op_type and param_key in params:
                values[recipe_key] = params[param_key]
    return EditRecipe.from_dict(values)


def recipe_for_mask(session: dict[str, Any], mask_id: str) -> EditRecipe:
    """Local adjustments stored as operations targeting ``mask_id``."""
    values: dict[str, Any] = {}
    for op in session.get("operations", []):
        if not op.get("enabled", True) or op.get("maskId") != mask_id:
            continue
        params = op.get("params") or {}
        for recipe_key, (op_type, param_key) in SESSION_OPS.items():
            if op.get("type") == op_type and param_key in params:
                values[recipe_key] = params[param_key]
    return EditRecipe.from_dict(values)


def replace_mask_operations(session: dict[str, Any], mask_id: str, recipe: EditRecipe) -> None:
    """Rewrite ``mask_id``'s GUI adjustment operations from ``recipe``,
    preserving every other operation (global ops, other masks, non-GUI ops)."""
    gui_types = {op_type for op_type, _param_key in SESSION_OPS.values()}
    preserved = [
        op
        for op in session.get("operations", [])
        if op.get("maskId") != mask_id or op.get("type") not in gui_types
    ]
    new_ops = operations_from_recipe(recipe, existing_ids=operation_ids({"operations": preserved}))
    for op in new_ops:
        op["maskId"] = mask_id
    session["operations"] = sorted(
        [*preserved, *new_ops], key=lambda op: _operation_order(op.get("type", ""))
    )


def operations_from_recipe(recipe: EditRecipe, *, existing_ids: set[str] | None = None) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    used_ids = set(existing_ids or set())
    values = asdict(recipe)
    grouped: dict[str, dict[str, Any]] = {}
    for recipe_key, value in values.items():
        if recipe_key not in SESSION_OPS or value in (0, 0.0, None):
            continue
        op_type, param_key = SESSION_OPS[recipe_key]
        grouped.setdefault(op_type, {})[param_key] = value

    for op_type, params in grouped.items():
        op_id = _next_id(used_ids, "gui-adjust")
        used_ids.add(op_id)
        op = {
            "id": op_id,
            "type": op_type,
            "enabled": True,
            "maskId": None,
            "params": params,
        }
        ops.append(op)
    return sorted(ops, key=lambda op: _operation_order(op["type"]))


def _operation_order(op_type: str) -> int:
    from photo_terminal.session import RENDERER_ORDER

    try:
        return RENDERER_ORDER.index(op_type)
    except ValueError:
        return len(RENDERER_ORDER)
