from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import json
import sys
from pathlib import Path
import time
from typing import Any

import math

from PySide6.QtCore import QObject, QPointF, QRectF, QRunnable, QSize, QSettings, Qt, QSignalBlocker, QThreadPool, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QFont,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from ..perf import perf_logger
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
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

from ..ai_model import (
    AIModelInstallation,
    download_ai_model,
    resolve_birefnet_model_installation,
    resolve_sam_model_installation,
    resolve_segmentation_model_installation,
)

from ..editor_copy import (
    SAVE_COPY_FILTER_TEXT,
    default_save_copy_path,
    normalize_save_copy_path,
    selected_save_copy_filter,
    validate_save_copy_paths,
)
from ..scanner import is_editor_asset_path
from ..edit_storage import (
    editor_session_path,
    ensure_edit_root,
    migrate_bundle,
    resolve_session_for_read,
)
from ..semantic_masks import (
    SEMANTIC_MASK_CATEGORIES,
    SEMANTIC_MASK_INVENTORY_REQUEST,
    SemanticMaskResult,
    SemanticMaskTask,
)
from ..subject_masks import (
    SUBJECT_MASK_SEMANTIC_CATEGORIES,
    SubjectMaskResult,
    SubjectMaskTask,
    combine_subject_components,
)
from ..depth_maps import DepthMapResult, DepthMapTask, DepthWarmTask
from ..people_instances import PeopleInstanceTask, PersonInstance
from ..prompt_masks import PromptMaskResult, PromptMaskTask, PromptMaskWarmTask


_CLI_EDITOR_ROOT = Path(__file__).resolve().parents[2] / "cli_editor"
if _CLI_EDITOR_ROOT.exists() and str(_CLI_EDITOR_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLI_EDITOR_ROOT))

from photo_terminal.adjustments import (  # noqa: E402
    EditRecipe,
    curve_lut,
    is_identity_curve,
    normalize_curve_points,
)
from photo_terminal.session import (  # noqa: E402
    SessionError,
    add_space,
    asset_dir_for_session,
    copy_bitmap_asset,
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
from .scene_regions import SceneIndexTask, SceneRegionIndex  # noqa: E402
from photo_terminal.io import open_image  # noqa: E402
from photo_terminal.masks import refine_color_range, refine_luminance_range  # noqa: E402


class _MaskModelDownloadSignals(QObject):
    progress = Signal(str)
    finished = Signal()
    failed = Signal(str)


class _MaskModelDownloadTask(QRunnable):
    """Fetch the editor's local masking models without blocking the viewer."""

    def __init__(self, installations: tuple[tuple[str, AIModelInstallation], ...]) -> None:
        super().__init__()
        self.installations = installations
        self.signals = _MaskModelDownloadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            for label, installation in self.installations:
                self.signals.progress.emit(f"Downloading {label}...")

                def report(filename: str, current: int, total: int) -> None:
                    name = Path(filename).name
                    if total > 0:
                        downloaded = current / (1024 * 1024)
                        size = total / (1024 * 1024)
                        self.signals.progress.emit(
                            f"Downloading {name}: {downloaded:.1f} / {size:.1f} MB"
                        )
                    else:
                        self.signals.progress.emit(f"Downloading {name}...")

                download_ai_model(installation, progress_callback=report)
            self.signals.finished.emit()
        except Exception as exc:
            self.signals.failed.emit(str(exc))


ADJUSTMENT_SPECS: tuple[tuple[str, str, int, int, int], ...] = (
    ("exposure", "Exposure", -500, 500, 100),
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

# Shape controls that live behind the Vignette row's disclosure. Kept out of
# ADJUSTMENT_SPECS so they stay out of the flat Effects list (and out of the
# per-mask adjustment set) while still behaving like ordinary rows.
VIGNETTE_OPTION_SPECS: tuple[tuple[str, str, int, int, int], ...] = (
    ("vignette_midpoint", "Midpoint", 0, 100, 1),
    ("vignette_roundness", "Roundness", -100, 100, 1),
    ("vignette_feather", "Feather", 0, 100, 1),
    ("vignette_highlights", "Highlights", 0, 100, 1),
)
VIGNETTE_OPTION_KEYS: frozenset[str] = frozenset(spec[0] for spec in VIGNETTE_OPTION_SPECS)

BUILTIN_PHOTO_PRESETS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "clean",
        "Clean",
        {
            "contrast": 6,
            "highlights": -18,
            "shadows": 14,
            "whites": 8,
            "blacks": -6,
            "vibrance": 8,
            "sharpen": 12,
        },
    ),
    (
        "portrait",
        "Portrait",
        {
            "contrast": -4,
            "highlights": -24,
            "shadows": 18,
            "vibrance": 8,
            "saturation": -3,
            "clarity": -8,
            "sharpen": 10,
            "vignette": -8,
            "vignette_feather": 70,
            "vignette_highlights": 35,
        },
    ),
    (
        "landscape",
        "Landscape",
        {
            "contrast": 14,
            "highlights": -28,
            "shadows": 18,
            "whites": 12,
            "blacks": -14,
            "vibrance": 24,
            "clarity": 18,
            "dehaze": 12,
            "sharpen": 22,
        },
    ),
    (
        "vivid",
        "Vivid",
        {
            "contrast": 12,
            "whites": 8,
            "blacks": -8,
            "vibrance": 30,
            "saturation": 8,
            "clarity": 10,
        },
    ),
    (
        "warm",
        "Warm",
        {
            "temperature": 18,
            "tint": 3,
            "highlights": -12,
            "shadows": 10,
            "vibrance": 14,
        },
    ),
    (
        "monochrome",
        "Monochrome",
        {
            "contrast": 18,
            "highlights": -20,
            "shadows": 16,
            "whites": 10,
            "blacks": -16,
            "saturation": -100,
            "clarity": 12,
        },
    ),
)

# Local (per-mask) adjustments: everything except vignette, which is
# inherently a whole-frame effect.
MASK_ADJUSTMENT_KEYS: tuple[str, ...] = tuple(
    spec[0] for spec in ADJUSTMENT_SPECS if spec[0] != "vignette"
)

MASK_TOUCHUP_KEYS: tuple[str, ...] = (
    "edgeDetectionRadius",
    "edgeSmooth",
    "edgeFeather",
    "edgeContrast",
    "edgeShift",
    "selectionCleared",
    "invert",
)


CURVE_OP_TYPE = "adjust.point_curve"
# Curve editor channel -> EditRecipe field. Persisted as one point_curve op per
# channel (the op carries a ``channel`` param) since the values are lists.
CURVE_RECIPE_KEYS: dict[str, str] = {
    "rgb": "curve_rgb",
    "red": "curve_red",
    "green": "curve_green",
    "blue": "curve_blue",
}

PRESET_RECIPE_KEYS: tuple[str, ...] = (
    *(spec[0] for spec in ADJUSTMENT_SPECS),
    *(spec[0] for spec in VIGNETTE_OPTION_SPECS),
    *CURVE_RECIPE_KEYS.values(),
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
    # Written by operations_from_recipe only when the amount is non-zero, but
    # always read back so a saved shape survives a reload.
    "vignette_midpoint": ("adjust.vignette", "midpoint"),
    "vignette_roundness": ("adjust.vignette", "roundness"),
    "vignette_feather": ("adjust.vignette", "feather"),
    "vignette_highlights": ("adjust.vignette", "highlights"),
}

def _next_id(existing: set[str], prefix: str) -> str:
    index = 1
    while True:
        candidate = f"{prefix}-{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


def _preset_values_from_recipe(recipe: EditRecipe) -> dict[str, Any]:
    data = asdict(recipe)
    return {key: deepcopy(data[key]) for key in PRESET_RECIPE_KEYS}


def _recipe_with_preset(
    recipe: EditRecipe,
    values: dict[str, Any],
    *,
    keys: tuple[str, ...] = PRESET_RECIPE_KEYS,
) -> EditRecipe:
    data = asdict(recipe)
    defaults = asdict(EditRecipe())
    for key in keys:
        data[key] = deepcopy(defaults[key])
    normalized = EditRecipe.from_dict(values)
    normalized_data = asdict(normalized)
    for key in keys:
        if key in values:
            data[key] = deepcopy(normalized_data[key])
    return EditRecipe.from_dict(data)


def _load_custom_photo_presets(raw: str) -> list[dict[str, Any]]:
    try:
        decoded = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    presets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in decoded:
        if not isinstance(item, dict) or not isinstance(item.get("values"), dict):
            continue
        name = " ".join(str(item.get("name") or "").split())
        normalized_name = name.casefold()
        if not name or normalized_name in seen:
            continue
        seen.add(normalized_name)
        normalized = _preset_values_from_recipe(EditRecipe.from_dict(item["values"]))
        presets.append({"name": name, "values": normalized})
    return presets


def _preset_adjustment_summary(values: dict[str, Any]) -> str:
    labels = {spec[0]: spec[1] for spec in (*ADJUSTMENT_SPECS, *VIGNETTE_OPTION_SPECS)}
    defaults = asdict(EditRecipe())
    parts = [
        f"{labels[key]} {value:+g}"
        for key, value in values.items()
        if key in labels and value != defaults[key]
    ]
    return ", ".join(parts)


def _friendly_mask_label(mask: dict[str, Any]) -> str:
    semantic_category = str(mask.get("semanticCategory") or "").strip()
    if semantic_category:
        return semantic_category.replace("_", " ").title()
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


class _LuminanceRangeSlider(QWidget):
    """Compact two-handle luminance interval control."""

    valuesChanged = Signal(int, int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._low = 0
        self._high = 255
        self._active_handle: str | None = None
        self.setMinimumHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(180, 30)

    def values(self) -> tuple[int, int]:
        return self._low, self._high

    def setValues(self, low: int, high: int) -> None:  # noqa: N802
        low = max(0, min(254, int(low)))
        high = max(low + 1, min(255, int(high)))
        if (low, high) == (self._low, self._high):
            return
        self._low, self._high = low, high
        self.update()
        self.valuesChanged.emit(low, high)

    def _track_rect(self) -> QRectF:
        return QRectF(7.0, 8.0, max(1.0, self.width() - 14.0), 13.0)

    def _x_for_value(self, value: int) -> float:
        track = self._track_rect()
        return track.left() + track.width() * value / 255.0

    def _value_for_x(self, x: float) -> int:
        track = self._track_rect()
        ratio = (x - track.left()) / max(1.0, track.width())
        return int(round(max(0.0, min(1.0, ratio)) * 255.0))

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = self._track_rect()
        gradient = QLinearGradient(track.topLeft(), track.topRight())
        gradient.setColorAt(0.0, QColor("#050505"))
        gradient.setColorAt(1.0, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#777777"), 1.0))
        painter.setBrush(gradient)
        painter.drawRoundedRect(track, 2.0, 2.0)

        selected = QRectF(
            self._x_for_value(self._low),
            track.top(),
            max(1.0, self._x_for_value(self._high) - self._x_for_value(self._low)),
            track.height(),
        )
        painter.setPen(QPen(QColor("#e6e6e6"), 1.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(selected)

        painter.setPen(QPen(QColor("#111111"), 1.0))
        painter.setBrush(QColor("#f0f0f0"))
        for value in (self._low, self._high):
            x = self._x_for_value(value)
            points = (
                QPointF(x - 5.0, track.bottom() + 1.0),
                QPointF(x + 5.0, track.bottom() + 1.0),
                QPointF(x, track.bottom() + 7.0),
            )
            path = QPainterPath(points[0])
            path.lineTo(points[1])
            path.lineTo(points[2])
            path.closeSubpath()
            painter.drawPath(path)
        painter.end()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            return
        x = event.position().x()
        self._active_handle = (
            "low"
            if abs(x - self._x_for_value(self._low))
            <= abs(x - self._x_for_value(self._high))
            else "high"
        )
        self._move_active_handle(x)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._active_handle is None:
            return
        self._move_active_handle(event.position().x())
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._active_handle = None
            event.accept()

    def _move_active_handle(self, x: float) -> None:
        value = self._value_for_x(x)
        if self._active_handle == "low":
            self.setValues(min(value, self._high - 1), self._high)
        elif self._active_handle == "high":
            self.setValues(self._low, max(value, self._low + 1))


class CurveEditor(QWidget):
    """Photoshop-style tone curve editor.

    Click the curve to add a control point, drag to shape it, and drag a point
    off the plot (or press Delete) to remove it. Control points may slide in
    both axes — moving the leftmost point right sets the input black point
    exactly as it does in Photoshop, because everything below it clamps flat.
    Interpolation is monotone cubic, so the curve never overshoots between
    points.
    """

    # channel, points (or None when the channel is back to identity)
    curve_changed = Signal(str, object)
    # (x, y) of the selected point, or None
    selection_changed = Signal(object)

    CHANNELS = ("rgb", "red", "green", "blue")
    _CHANNEL_COLORS = {
        "rgb": QColor("#e8e8e8"),
        "red": QColor("#ff6b6b"),
        "green": QColor("#66d17a"),
        "blue": QColor("#6ba8ff"),
    }
    # Padding around the plot so the endpoint handles, which sit *on* the
    # frame corners, are drawn in full instead of being clipped.
    PADDING = 6
    HIT_PX = 9.0
    OFF_PLOT_PX = 22.0  # drag this far outside the plot to delete a point

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(120)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self._channel = "rgb"
        self._curves: dict[str, list[list[int]]] = {
            channel: [[0, 0], [255, 255]] for channel in self.CHANNELS
        }
        self._selected: int | None = None
        self._dragging = False
        self._drag_off = False

    # -- data ----------------------------------------------------------------
    def channel(self) -> str:
        return self._channel

    def set_channel(self, channel: str) -> None:
        if channel in self.CHANNELS and channel != self._channel:
            self._channel = channel
            self._selected = None
            self.selection_changed.emit(None)
            self.update()

    def curve_value(self, channel: str) -> list[list[int]] | None:
        """Points for ``channel``, or None when it is identity (keeps the
        recipe free of no-op curves)."""
        points = self._curves[channel]
        if is_identity_curve(points):
            return None
        return [list(point) for point in points]

    def set_points(self, channel: str, points: Any) -> None:
        """Load points without emitting (used to sync from a recipe)."""
        if channel not in self._curves:
            return
        cleaned = normalize_curve_points(points) if points else []
        if len(cleaned) < 2:
            cleaned = [[0, 0], [255, 255]]
        self._curves[channel] = cleaned
        if channel == self._channel:
            self._selected = None
            self.selection_changed.emit(None)
        self.update()

    def reset_channel(self, channel: str | None = None) -> None:
        target = channel or self._channel
        self._curves[target] = [[0, 0], [255, 255]]
        if target == self._channel:
            self._selected = None
            self.selection_changed.emit(None)
        self.update()
        self.curve_changed.emit(target, self.curve_value(target))

    def points_for_test(self, channel: str) -> list[list[int]]:
        """Raw control points for ``channel`` (identity included)."""
        return [list(point) for point in self._curves[channel]]

    def selected_point(self) -> tuple[int, int] | None:
        points = self._curves[self._channel]
        if self._selected is None or not 0 <= self._selected < len(points):
            return None
        return tuple(points[self._selected])  # type: ignore[return-value]

    def move_selected_to(self, x: int, y: int) -> None:
        """Used by the Input/Output fields."""
        if self._selected is None:
            return
        self._apply_point(self._selected, x, y)
        self._notify()

    # -- geometry ------------------------------------------------------------
    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().resizeEvent(event)
        # Keep the plot square, the way Photoshop's curve grid is.
        if self.width() > 0 and self.height() != self.width():
            self.setFixedHeight(self.width())

    def _plot_rect(self) -> QRectF:
        """The square grid the curve is drawn in, inset by PADDING so the
        endpoint handles can straddle its border without being clipped."""
        pad = self.PADDING
        available = QRectF(self.rect().adjusted(pad, pad, -pad, -pad))
        side = max(1.0, min(available.width(), available.height()))
        return QRectF(
            available.left() + (available.width() - side) / 2.0,
            available.top() + (available.height() - side) / 2.0,
            side,
            side,
        )

    def _to_widget(self, x: float, y: float) -> QPointF:
        rect = self._plot_rect()
        return QPointF(
            rect.left() + rect.width() * (x / 255.0),
            rect.bottom() - rect.height() * (y / 255.0),
        )

    def _to_data(self, pos: QPointF) -> tuple[int, int]:
        rect = self._plot_rect()
        x = (pos.x() - rect.left()) / max(1.0, rect.width()) * 255.0
        y = (rect.bottom() - pos.y()) / max(1.0, rect.height()) * 255.0
        return (
            int(round(max(0.0, min(255.0, x)))),
            int(round(max(0.0, min(255.0, y)))),
        )

    # -- editing -------------------------------------------------------------
    def _apply_point(self, index: int, x: int, y: int) -> None:
        points = self._curves[self._channel]
        if not 0 <= index < len(points):
            return
        low = points[index - 1][0] + 1 if index > 0 else 0
        high = points[index + 1][0] - 1 if index < len(points) - 1 else 255
        points[index] = [
            int(max(low, min(high, x))),
            int(max(0, min(255, y))),
        ]
        self.update()

    def _notify(self) -> None:
        self.curve_changed.emit(self._channel, self.curve_value(self._channel))
        self.selection_changed.emit(self.selected_point())

    def _point_at(self, pos: QPointF) -> int | None:
        for index, (x, y) in enumerate(self._curves[self._channel]):
            widget_point = self._to_widget(x, y)
            if (widget_point - pos).manhattanLength() <= self.HIT_PX * 1.6:
                if math.hypot(widget_point.x() - pos.x(), widget_point.y() - pos.y()) <= self.HIT_PX:
                    return index
        return None

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        pos = QPointF(event.position())
        index = self._point_at(pos)
        if index is None:
            x, y = self._to_data(pos)
            points = self._curves[self._channel]
            # Don't stack a new point on an existing x.
            if any(point[0] == x for point in points):
                index = next(i for i, point in enumerate(points) if point[0] == x)
            else:
                index = len([point for point in points if point[0] < x])
                points.insert(index, [x, y])
        self._selected = index
        self._dragging = True
        self._drag_off = False
        self._notify()
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt override
        pos = QPointF(event.position())
        if not self._dragging or self._selected is None:
            self.setCursor(
                Qt.CursorShape.PointingHandCursor
                if self._point_at(pos) is not None
                else Qt.CursorShape.CrossCursor
            )
            event.ignore()
            return
        rect = self._plot_rect()
        self._drag_off = self._deletable(self._selected) and (
            pos.x() < rect.left() - self.OFF_PLOT_PX
            or pos.x() > rect.right() + self.OFF_PLOT_PX
            or pos.y() < rect.top() - self.OFF_PLOT_PX
            or pos.y() > rect.bottom() + self.OFF_PLOT_PX
        )
        x, y = self._to_data(pos)
        self._apply_point(self._selected, x, y)
        self._notify()
        event.accept()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() != Qt.MouseButton.LeftButton or not self._dragging:
            event.ignore()
            return
        self._dragging = False
        if self._drag_off and self._selected is not None:
            self._remove(self._selected)
        self._drag_off = False
        event.accept()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace) and self._selected is not None:
            self._remove(self._selected)
            event.accept()
            return
        super().keyPressEvent(event)

    def _deletable(self, index: int) -> bool:
        # Photoshop keeps the black/white end handles: they move but never
        # delete. Only interior control points can be removed.
        points = self._curves[self._channel]
        return len(points) > 2 and 0 < index < len(points) - 1

    def _remove(self, index: int) -> None:
        points = self._curves[self._channel]
        if not self._deletable(index) or not 0 <= index < len(points):
            return
        del points[index]
        self._selected = None
        self.update()
        self._notify()

    # -- painting ------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._plot_rect()
        # The frame *is* the plot, so the end handles sit on its corners rather
        # than floating inside it.
        painter.setPen(QPen(QColor("#141414")))
        painter.setBrush(QColor("#1e1e1e"))
        painter.drawRect(rect)

        painter.setPen(QPen(QColor("#2e2e2e")))
        for step in range(1, 4):
            x = rect.left() + rect.width() * step / 4.0
            y = rect.top() + rect.height() * step / 4.0
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))

        identity_pen = QPen(QColor("#3a3a3a"), 1.0)
        identity_pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(identity_pen)
        painter.drawLine(
            QPointF(rect.left(), rect.bottom()), QPointF(rect.right(), rect.top())
        )

        points = self._curves[self._channel]
        color = self._CHANNEL_COLORS[self._channel]
        lut = curve_lut(points)
        path = QPainterPath()
        path.moveTo(self._to_widget(0, lut[0]))
        for value in range(1, 256):
            path.lineTo(self._to_widget(value, lut[value]))
        painter.setPen(QPen(color, 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(path)

        for index, (x, y) in enumerate(points):
            center = self._to_widget(x, y)
            selected = index == self._selected
            painter.setPen(QPen(QColor("#101010"), 1.0))
            painter.setBrush(color if selected else QColor("#1e1e1e"))
            radius = 4.5 if selected else 3.5
            painter.drawEllipse(center, radius, radius)
            if not selected:
                painter.setPen(QPen(color, 1.4))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(center, radius, radius)
        painter.end()


def _configure_value_box(spin: QAbstractSpinBox) -> None:
    """Compact, editable numeric readout: no spin buttons (the slider is the
    coarse control), centered text, and arrow-key stepping. keyboardTracking is
    off so a half-typed number doesn't trigger a render on every keystroke —
    the value commits on Enter, focus-out, or an arrow key."""
    spin.setObjectName("editorNumber")
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
    spin.setKeyboardTracking(False)
    spin.setAccelerated(True)
    spin.setCorrectionMode(QAbstractSpinBox.CorrectionMode.CorrectToNearestValue)


class _ValueBox(QDoubleSpinBox):
    """Editable value field that renders a leading ``+`` on non-negative values
    (matching the editor's signed readout) while still accepting typed input
    with an explicit ``+``/``-``."""

    def __init__(
        self,
        *,
        decimals: int,
        minimum: float,
        maximum: float,
        step: float,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setDecimals(decimals)
        self.setRange(minimum, maximum)
        self.setSingleStep(step)
        _configure_value_box(self)

    def textFromValue(self, value: float) -> str:
        text = super().textFromValue(value)
        if value >= 0 and not text.startswith("+"):
            return f"+{text}"
        return text

    def valueFromText(self, text: str) -> float:
        return super().valueFromText(text.strip().lstrip("+"))

    def validate(self, text: str, pos: int):  # noqa: N802 - Qt override
        # Accept the leading '+' we render (and that users may type).
        if text.startswith("+"):
            state, _cleaned, _pos = super().validate(text[1:], max(0, pos - 1))
            return state, text, pos
        return super().validate(text, pos)


class _AdjustmentRow(QWidget):
    changed = Signal(str, float)
    # Only emitted by rows built with expandable=True.
    expand_toggled = Signal(bool)

    def __init__(
        self,
        key: str,
        label: str,
        minimum: int,
        maximum: int,
        scale: int,
        parent=None,
        *,
        expandable: bool = False,
    ) -> None:
        super().__init__(parent)
        self.key = key
        self.scale = scale
        self._label = label
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(minimum, maximum)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(10)
        self.slider.setValue(0)
        self.slider.setObjectName(f"slider_{key}")
        self.value_box = _ValueBox(
            decimals=2 if scale == 100 else 0,
            minimum=minimum / scale,
            maximum=maximum / scale,
            step=0.05 if scale == 100 else 1.0,
            parent=self,
        )
        self.value_box.setValue(0.0)
        if expandable:
            # The label doubles as the disclosure so the extra controls cost no
            # vertical space at all while they are collapsed.
            title = QPushButton(f"{label}  ▸", self)
            title.setObjectName("editorExpanderLabel")
            title.setCheckable(True)
            title.setCursor(Qt.CursorShape.PointingHandCursor)
            title.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            title.toggled.connect(self._handle_expand_toggled)
            self.expander = title
        else:
            title = QLabel(label, self)
            title.setObjectName("editorControlLabel")
            self.expander = None

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(1)
        layout.addWidget(title, 0, 0)
        layout.addWidget(self.value_box, 0, 1, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.slider, 1, 0, 1, 2)
        self.slider.valueChanged.connect(self._handle_slider_changed)
        self.value_box.valueChanged.connect(self._handle_box_changed)

    def _handle_expand_toggled(self, expanded: bool) -> None:
        if self.expander is not None:
            self.expander.setText(f"{self._label}  {'▾' if expanded else '▸'}")
        self.expand_toggled.emit(expanded)

    def set_expanded(self, expanded: bool) -> None:
        if self.expander is not None and self.expander.isChecked() != expanded:
            self.expander.setChecked(expanded)

    def _handle_slider_changed(self, raw: int) -> None:
        value = raw / self.scale
        with QSignalBlocker(self.value_box):
            self.value_box.setValue(value)
        self.changed.emit(self.key, value)

    def _handle_box_changed(self, value: float) -> None:
        with QSignalBlocker(self.slider):
            self.slider.setValue(int(round(float(value) * self.scale)))
        self.changed.emit(self.key, float(value))

    def set_value(self, value: float) -> None:
        with QSignalBlocker(self.slider):
            self.slider.setValue(int(round(float(value) * self.scale)))
        with QSignalBlocker(self.value_box):
            self.value_box.setValue(float(value))


class _MaskListRow(QWidget):
    """A mask list row. Clicking the name/blank area selects the row; the trash
    button is a real child that handles its own clicks. (The container is NOT
    transparent-for-mouse — doing that swallowed the trash button's clicks and
    passed them to the list, which is why single-click delete stopped working.)"""

    clicked = Signal()
    doubleClicked = Signal()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class PhotoEditorPanel(QFrame):
    recipe_changed = Signal(object)
    saved = Signal(str)
    save_copy_requested = Signal(str, str, object, object)
    status_changed = Signal(str)
    subject_warm_requested = Signal(str)
    semantic_warm_requested = Signal(str)
    # The on-canvas mask overlay should re-read mask_overlay_state().
    mask_overlay_changed = Signal()

    MASK_SHAPE_TYPES = ("radial", "linear-gradient")
    # Two states, Lightroom-style: pick a mask type, or work on the mask you have.
    MASK_PANE_WORK = 0
    MASK_PANE_CREATE = 1
    OVERLAY_MODE_OPTIONS: tuple[tuple[str, str], ...] = (
        ("color", "Color Overlay"),
        ("color-bw", "Color Overlay on B&W"),
        ("image-bw", "Image on B&W"),
        ("image-black", "Image on Black"),
        ("image-white", "Image on White"),
        ("white-black", "White on Black"),
    )
    OVERLAY_MODE_KEY = "preview/mask_overlay_mode"
    OVERLAY_COLOR_KEY = "preview/mask_overlay_color"
    OVERLAY_AUTO_TOGGLE_KEY = "preview/mask_overlay_auto_toggle"
    OVERLAY_SHOW_TOOLS_KEY = "preview/mask_overlay_show_tools"
    CUSTOM_PRESETS_KEY = "editor/custom_photo_presets"
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
        # AI Background tool: the BiRefNet matte (resolved on demand from the
        # subject-mask cache) that separates foreground from the blur/replace.
        self._background_matte_path: str | None = None
        self._background_matte_task: object | None = None
        self._background_tool_panel: QWidget | None = None
        # Depth-aware Lens Blur: the depth map (resolved on demand + cached).
        self._depth_map_path: str | None = None
        self._depth_map_task: object | None = None
        self._lens_blur_tool_panel: QWidget | None = None
        self._status_message = ""
        self._rows: dict[str, _AdjustmentRow] = {}
        self._mask_create_mode: str | None = None
        self._pending_parent_id: str | None = None
        self._pending_combine: str = "add"
        self._brush_paint_mode: str | None = None
        self._active_mask_edit_id: str | None = None
        self._color_resample_mask_id: str | None = None
        self._pending_range_mask_id: str | None = None
        self._updating_mask_controls = False
        self._overlay_suppressed_for_drag = False
        self._mask_touchup_mask_id: str | None = None
        self._mask_touchup_original_present: set[str] = set()
        self._mask_touchup_original_values: dict[str, Any] = {}
        self._mask_touchup_spins: dict[str, QSpinBox | QDoubleSpinBox] = {}
        self._settings = QSettings()
        self._custom_photo_presets = _load_custom_photo_presets(
            self._settings.value(self.CUSTOM_PRESETS_KEY, "", str)
        )
        stored_mode = self._settings.value(self.OVERLAY_MODE_KEY, "color", str)
        supported_modes = {mode for mode, _label in self.OVERLAY_MODE_OPTIONS}
        self._overlay_mode = stored_mode if stored_mode in supported_modes else "color"
        default_overlay_color = QColor(255, 64, 64, 128)
        try:
            stored_rgba = int(
                self._settings.value(
                    self.OVERLAY_COLOR_KEY,
                    int(default_overlay_color.rgba()),
                )
            )
            self._overlay_color = QColor.fromRgba(stored_rgba)
        except (TypeError, ValueError):
            self._overlay_color = default_overlay_color
        self._overlay_auto_toggle = self._settings.value(
            self.OVERLAY_AUTO_TOGGLE_KEY, True, bool
        )
        self._overlay_show_tools = self._settings.value(
            self.OVERLAY_SHOW_TOOLS_KEY, True, bool
        )
        self._source_size_cache: tuple[Path, tuple[int, int]] | None = None
        self._copy_save_busy = False
        self._semantic_mask_task: SemanticMaskTask | None = None
        self._mask_models_download_task: _MaskModelDownloadTask | None = None
        self._semantic_mask_request_context: tuple[str | None, str] | None = None
        self._semantic_mask_result: SemanticMaskResult | None = None
        self._subject_mask_task: SubjectMaskTask | None = None
        self._subject_mask_request_context: tuple[str | None, str] | None = None
        self._subject_choice_result: SubjectMaskResult | None = None
        self._subject_choice_context: tuple[str | None, str] | None = None
        self._subject_choice_selected_ids: set[str] = set()
        # Promptable click-to-select (SAM) state. A session builds ONE mask:
        # each click adds an "add" component to the same group, edited/refined
        # together in the touchup window, committed on OK.
        self._point_select_active = False
        self._prompt_mask_task: object | None = None
        self._prompt_mask_context: dict[str, Any] | None = None
        self._prompt_mask_counter = 0
        self._prompt_session_active = False
        self._prompt_session_root_id: str | None = None
        self._prompt_session_label: str | None = None
        # How each click-to-select component was made: {mask_id: {"points",
        # "refined"}}. Kept in memory (the session schema drops unknown mask
        # keys) so "Refine Edges" can re-run the SAM prompt; mask ids survive the
        # save/reload round-trip, so keying on them is stable.
        self._prompt_meta: dict[str, dict[str, Any]] = {}
        self._refine_queue: list[str] = []
        self._refine_active_task: object | None = None
        # Lazily derived from the result above; keyed on it so a new analysis
        # invalidates the index without an explicit reset.
        self._scene_index: SceneRegionIndex | None = None
        self._scene_index_base: SceneRegionIndex | None = None
        self._scene_index_source: Path | None = None
        self._scene_index_task: SceneIndexTask | None = None
        # SAM-split individual people, folded into the scene index so hovering
        # lights up one person instead of the whole crowd.
        self._people_instances: list[PersonInstance] | None = None
        self._people_instances_source: Path | None = None
        self._people_instance_task: PeopleInstanceTask | None = None
        self._semantic_mask_pool = QThreadPool(self)
        self._semantic_mask_pool.setMaxThreadCount(1)
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

        self._editor_tab_bar = self._build_tab_bar()
        root.addWidget(self._editor_tab_bar)

        doc_bar = QFrame(self)
        self._editor_doc_bar = doc_bar
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
        self.editor_stack.addWidget(self._build_presets_tab())
        root.addWidget(self.editor_stack, 1)

        footer = QFrame(self)
        self._editor_footer = footer
        footer.setObjectName("photoEditorFooter")
        footer_layout = QVBoxLayout(footer)
        # No status line any more, so the action row sits low in the footer.
        footer_layout.setContentsMargins(12, 16, 12, 12)
        footer_layout.setSpacing(0)
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
        root.addWidget(footer)
        self._mask_touchup_page = self._build_mask_touchup_page()
        self._mask_touchup_page.hide()
        root.addWidget(self._mask_touchup_page, 1)
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
            ("Presets", "Saved editing looks", 2),
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
        if index == 1:
            self._sync_mask_model_controls()
            if self._mask_models_are_installed():
                self.subject_warm_requested.emit("model")
                # Load the OneFormer weights ahead of inventory; the expensive
                # torch/CUDA spin-up is already warmed on photo render.
                self.semantic_warm_requested.emit("model")
                self._ensure_semantic_inventory()
        elif index == 2:
            self._refresh_preset_targets()

    def show_adjustments_page(self) -> None:
        if self._mask_touchup_mask_id is not None:
            self._finish_mask_touchup(accepted=True)
        self._set_editor_page(0)

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
        # The spin box itself is the readout — editable (typing / arrow keys)
        # instead of the old static label.
        _configure_value_box(spin)
        spin.setParent(row)
        spin.show()
        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(int(round(minimum * scale)), int(round(maximum * scale)))
        slider.setValue(int(round(float(spin.value()) * scale)))
        slider.setSingleStep(1)

        def slider_changed(raw: int) -> None:
            # Left unblocked on purpose: listeners connected to spin.valueChanged
            # are how a slider drag reaches the mask/adjustment handlers.
            value = raw / scale
            if isinstance(spin, QSpinBox):
                spin.setValue(int(round(value)))
            else:
                spin.setValue(value)

        def spin_changed(value: float) -> None:
            with QSignalBlocker(slider):
                slider.setValue(int(round(float(value) * scale)))

        slider.valueChanged.connect(slider_changed)
        spin.valueChanged.connect(spin_changed)

        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(1)
        layout.addWidget(title, 0, 0)
        layout.addWidget(spin, 0, 1, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(slider, 1, 0, 1, 2)
        return row

    def _pixel_decade_slider_row(
        self,
        label: str,
        spin: QDoubleSpinBox,
        *,
        parent: QWidget,
    ) -> tuple[QWidget, QSlider]:
        """0..1000 px with one third of travel allocated to each decade."""
        row = QWidget(parent)
        title = QLabel(label, row)
        title.setObjectName("editorControlLabel")
        _configure_value_box(spin)
        spin.setParent(row)
        spin.setSuffix(" px")
        spin.show()
        slider = QSlider(Qt.Orientation.Horizontal, row)
        slider.setRange(0, 3000)
        slider.setSingleStep(1)

        slider.setValue(self._feather_pixels_to_slider(spin.value()))

        def slider_changed(position: int) -> None:
            spin.setValue(round(self._feather_slider_to_pixels(position), 1))

        def spin_changed(value: float) -> None:
            with QSignalBlocker(slider):
                slider.setValue(self._feather_pixels_to_slider(value))

        slider.valueChanged.connect(slider_changed)
        spin.valueChanged.connect(spin_changed)
        layout = QGridLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(1)
        layout.addWidget(title, 0, 0)
        layout.addWidget(spin, 0, 1, Qt.AlignmentFlag.AlignRight)
        layout.addWidget(slider, 1, 0, 1, 2)
        return row, slider

    @staticmethod
    def _feather_slider_to_pixels(position: int) -> float:
        if position <= 1000:
            return position / 100.0
        if position <= 2000:
            return 10.0 * (10.0 ** ((position - 1000) / 1000.0))
        return 100.0 * (10.0 ** ((position - 2000) / 1000.0))

    @staticmethod
    def _feather_pixels_to_slider(pixels: float) -> int:
        value = max(0.0, min(1000.0, float(pixels)))
        if value <= 10.0:
            return int(round(value * 100.0))
        if value <= 100.0:
            return int(round(1000.0 + math.log10(value / 10.0) * 1000.0))
        return int(round(2000.0 + math.log10(value / 100.0) * 1000.0))

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
        # The create pane may represent a new root or an Add/Subtract request.
        # Keep that context until the chosen tool actually creates its mask.
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

    def _build_vignette_options(self, parent: QWidget, vignette_row: _AdjustmentRow) -> QWidget:
        """Midpoint / Roundness / Feather / Highlights, folded away behind the
        Vignette row's own label so the collapsed state adds no height."""
        panel = QWidget(parent)
        panel.setObjectName("vignetteOptions")
        # Plain QWidgets ignore QSS borders without this.
        panel.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 4, 0, 2)
        layout.setSpacing(6)
        defaults = asdict(EditRecipe())
        for spec in VIGNETTE_OPTION_SPECS:
            row = _AdjustmentRow(*spec, parent=panel)
            # Rows start at zero; these are the only ones whose neutral value
            # is not zero, so seed them from the recipe or the readout lies.
            row.set_value(float(defaults[spec[0]]))
            row.changed.connect(self._handle_adjustment_changed)
            self._rows[spec[0]] = row
            layout.addWidget(row)
        panel.setVisible(False)
        vignette_row.expand_toggled.connect(panel.setVisible)
        self._vignette_options = panel
        return panel

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
                row = _AdjustmentRow(*spec, parent=body, expandable=key == "vignette")
                row.changed.connect(self._handle_adjustment_changed)
                self._rows[key] = row
                section_layout.addWidget(row)
                if key == "vignette":
                    section_layout.addWidget(self._build_vignette_options(section, row))
            body_layout.addWidget(section)

        curve_section, curve_layout = self._section("Curve", body)
        # Tighter than the default section rhythm so the square plot sits close
        # under the channel row — the Curve section is the tallest one.
        curve_layout.setSpacing(4)
        curve_modes = QHBoxLayout()
        curve_modes.setContentsMargins(0, 0, 0, 0)
        curve_modes.setSpacing(4)
        curve_label = QLabel("Channel", curve_section)
        curve_label.setObjectName("editorControlLabel")
        curve_modes.addWidget(curve_label)
        self.curve_channel_buttons: dict[str, QPushButton] = {}
        for channel, label in (("rgb", "RGB"), ("red", "R"), ("green", "G"), ("blue", "B")):
            button = QPushButton(label, curve_section)
            button.setObjectName("curveChannelButton")
            button.setCheckable(True)
            button.setChecked(channel == "rgb")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(lambda _checked=False, ch=channel: self._set_curve_channel(ch))
            self.curve_channel_buttons[channel] = button
            curve_modes.addWidget(button)
        curve_modes.addStretch(1)
        self.curve_reset_button = QPushButton("Reset", curve_section)
        self.curve_reset_button.setObjectName("curveResetButton")
        self.curve_reset_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.curve_reset_button.clicked.connect(lambda: self.curve_editor.reset_channel())
        curve_modes.addWidget(self.curve_reset_button)
        curve_layout.addLayout(curve_modes)

        self.curve_editor = CurveEditor(curve_section)
        self.curve_editor.curve_changed.connect(self._handle_curve_changed)
        self.curve_editor.selection_changed.connect(self._handle_curve_selection)
        curve_layout.addWidget(self.curve_editor)

        curve_io = QHBoxLayout()
        curve_io.setSpacing(6)
        self.curve_input_spin = self._spin(curve_section, 0, 255, 0)
        self.curve_output_spin = self._spin(curve_section, 0, 255, 0)
        for spin in (self.curve_input_spin, self.curve_output_spin):
            _configure_value_box(spin)
            spin.setEnabled(False)
            spin.valueChanged.connect(self._handle_curve_io_changed)
        input_label = QLabel("Input", curve_section)
        input_label.setObjectName("editorControlLabel")
        output_label = QLabel("Output", curve_section)
        output_label.setObjectName("editorControlLabel")
        curve_io.addWidget(input_label)
        curve_io.addWidget(self.curve_input_spin)
        curve_io.addSpacing(8)
        curve_io.addWidget(output_label)
        curve_io.addWidget(self.curve_output_spin)
        curve_io.addStretch(1)
        curve_layout.addLayout(curve_io)
        body_layout.addWidget(curve_section)
        body_layout.addStretch(1)
        scroll.setWidget(body)
        return scroll

    def build_background_tool(self, parent: QWidget | None = None) -> QWidget:
        """The Background tool as a standalone panel (hosted in a popout window
        off the tool rail). Built once and reused so its controls stay valid."""
        existing = getattr(self, "_background_tool_panel", None)
        if existing is not None:
            return existing
        panel = QWidget(parent)
        panel.setObjectName("backgroundToolPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "Keep the subject sharp — blur what's behind it, or remove it and "
            "leave a transparent background. The cut-out is found automatically.",
            panel,
        )
        hint.setObjectName("editorHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        # Blur is the primary control: one always-visible strength slider,
        # 0 = off, so it never reads as an all-or-nothing toggle.
        blur_row = QHBoxLayout()
        blur_row.setContentsMargins(0, 2, 0, 0)
        blur_row.setSpacing(8)
        blur_label = QLabel("Blur", panel)
        blur_label.setObjectName("editorControlLabel")
        blur_label.setFixedWidth(46)
        self.background_blur_slider = QSlider(Qt.Orientation.Horizontal, panel)
        self.background_blur_slider.setRange(0, 100)
        self.background_blur_slider.setValue(0)
        self.background_blur_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.background_blur_slider.valueChanged.connect(self._set_background_blur)
        self.background_blur_value = QLabel("0", panel)
        self.background_blur_value.setObjectName("editorControlLabel")
        self.background_blur_value.setFixedWidth(28)
        self.background_blur_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        blur_row.addWidget(blur_label)
        blur_row.addWidget(self.background_blur_slider, 1)
        blur_row.addWidget(self.background_blur_value)
        layout.addLayout(blur_row)

        # Remove: knock the background out to a transparent (checkerboard) field.
        self.background_remove_button = QPushButton("Remove background", panel)
        self.background_remove_button.setObjectName("backgroundRemoveButton")
        self.background_remove_button.setCheckable(True)
        self.background_remove_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.background_remove_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.background_remove_button.toggled.connect(self._toggle_background_remove)
        layout.addWidget(self.background_remove_button)
        layout.addStretch(1)

        self._background_tool_panel = panel
        self._sync_background_controls()
        return panel

    def _mask_pane(self) -> tuple[QScrollArea, QWidget, QVBoxLayout]:
        """A scrollable pane for the Masks stack."""
        scroll = QScrollArea(self)
        scroll.setObjectName("photoEditorScrollArea")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(scroll)
        body.setObjectName("photoEditorBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll.setWidget(body)
        return scroll, body, layout

    def _build_masks_tab(self) -> QWidget:
        """Two states, Lightroom-style: the Work pane (the mask list and, below
        it in one scroll, everything for the selected mask) and the Create pane
        (a picker you drop into and out of). No sub-tabs, no nav chrome."""
        tab = QWidget(self)
        tab.setObjectName("photoEditorBody")
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.mask_stack = QStackedWidget(tab)
        self.mask_stack.setObjectName("maskPaneStack")
        self.mask_stack.addWidget(self._build_mask_work_pane())
        self.mask_stack.addWidget(self._build_mask_create_pane())
        outer.addWidget(self.mask_stack, 1)
        self._show_mask_pane(self.MASK_PANE_WORK)
        return tab

    def _show_mask_pane(self, index: int) -> None:
        """Switch between the two Masks states. Leaving Create disarms whatever
        tool was picked, so a tool can never stay live under a pane that no
        longer shows it."""
        stack = getattr(self, "mask_stack", None)
        if stack is None:
            return
        leaving_create = (
            stack.currentIndex() == self.MASK_PANE_CREATE and index != self.MASK_PANE_CREATE
        )
        stack.setCurrentIndex(index)
        if leaving_create:
            self._set_mask_tool(None)
            self._brush_paint_mode = None
            self._pending_parent_id = None
            self._pending_combine = "add"
            self._clear_subject_choice()
            self._sync_mask_create_title()
        self._sync_mask_pane_enabled()
        self.mask_overlay_changed.emit()

    def _open_mask_create_pane(
        self,
        *,
        parent_id: str | None = None,
        combine: str = "add",
    ) -> None:
        self._sync_mask_model_controls()
        if self._mask_models_are_installed():
            self.subject_warm_requested.emit("model")
        self._active_mask_edit_id = None
        self._pending_parent_id = parent_id
        self._pending_combine = "subtract" if combine == "subtract" else "add"
        self._brush_paint_mode = None
        self._set_mask_tool(None)
        self._sync_mask_create_title()
        self._show_mask_pane(self.MASK_PANE_CREATE)

    def _cancel_mask_create(self) -> None:
        self._show_mask_pane(self.MASK_PANE_WORK)

    def _sync_mask_create_title(self) -> None:
        label = getattr(self, "mask_create_title", None)
        if label is None:
            return
        if self._pending_parent_id is None:
            label.setText("Create new mask")
        elif self._pending_combine == "subtract":
            label.setText("Subtract from mask")
        else:
            label.setText("Add to mask")

    def _sync_mask_pane_enabled(self) -> None:
        button = getattr(self, "new_mask_button", None)
        if button is not None:
            button.setEnabled(self._source_path is not None)

    @staticmethod
    def _mask_model_installations() -> tuple[tuple[str, AIModelInstallation], ...]:
        return (
            ("scene selection tools", resolve_segmentation_model_installation()),
            ("subject selection tools", resolve_birefnet_model_installation()),
            ("click selection tools", resolve_sam_model_installation()),
        )

    def _mask_models_are_installed(self) -> bool:
        return all(
            installation.is_installed
            for _label, installation in self._mask_model_installations()
        )

    def _sync_mask_model_controls(self) -> None:
        point_button = getattr(self, "point_select_button", None)
        download_button = getattr(self, "download_mask_models_button", None)
        if point_button is None or download_button is None:
            return
        downloading = self._mask_models_download_task is not None
        installed = self._mask_models_are_installed()
        point_button.setVisible(installed and not downloading)
        download_button.setVisible(not installed or downloading)
        download_button.setEnabled(not installed and not downloading)
        download_button.setText(
            "Downloading AI Masking Tools..." if downloading else "Download AI Masking Tools"
        )

    def _download_mask_models(self) -> None:
        if self._mask_models_download_task is not None:
            return
        missing = tuple(
            (label, installation)
            for label, installation in self._mask_model_installations()
            if not installation.is_installed
        )
        if not missing:
            self._sync_mask_model_controls()
            return
        task = _MaskModelDownloadTask(missing)
        task.signals.progress.connect(
            self._handle_mask_model_download_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            self._handle_mask_model_download_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            self._handle_mask_model_download_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._mask_models_download_task = task
        self.semantic_mask_status.setText("Downloading AI masking tools...")
        self.semantic_mask_status.show()
        self._set_status("Downloading AI masking tools...")
        self._sync_mask_model_controls()
        self.mask_overlay_changed.emit()
        self._semantic_mask_pool.start(task)

    def _handle_mask_model_download_progress(self, message: str) -> None:
        if self._mask_models_download_task is None:
            return
        self.semantic_mask_status.setText(message)
        self.semantic_mask_status.show()
        self._set_status(message)

    def _handle_mask_model_download_finished(self) -> None:
        self._mask_models_download_task = None
        self._sync_mask_model_controls()
        self.semantic_mask_status.setText("AI masking tools are ready.")
        self.semantic_mask_status.show()
        self._set_status("AI masking tools are ready")
        self.mask_overlay_changed.emit()
        if self.editor_stack.currentIndex() == 1:
            self.subject_warm_requested.emit("model")
            self.semantic_warm_requested.emit("model")
            QTimer.singleShot(0, self._ensure_semantic_inventory)

    def _handle_mask_model_download_failed(self, message: str) -> None:
        self._mask_models_download_task = None
        self._sync_mask_model_controls()
        text = f"Could not download AI masking tools: {message}"
        self.semantic_mask_status.setText(text)
        self.semantic_mask_status.show()
        self._set_status(text)
        self.mask_overlay_changed.emit()

    # -- mask type glyphs -----------------------------------------------------
    def _mask_glyph(self, kind: str) -> QIcon:
        """A small flat line-icon for a mask tool, drawn to match the panel's
        muted chrome (the studio panel is always dark, so one light stroke works
        in both app themes)."""
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor("#c4c4c4"))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if kind == "radial":
            painter.drawEllipse(QRectF(3, 4, 12, 10))
            painter.drawEllipse(QRectF(7.5, 8, 3, 2))
        elif kind == "linear":
            painter.drawLine(4, 13, 14, 5)
            painter.drawLine(6, 14, 9, 14)
        elif kind == "brush":
            painter.drawLine(11, 4, 14, 7)
            painter.drawLine(11, 4, 5, 10)
            painter.drawLine(14, 7, 8, 13)
            painter.drawLine(5, 10, 8, 13)
            painter.drawLine(5, 10, 3, 15)
            painter.drawLine(8, 13, 3, 15)
        elif kind == "range":
            painter.drawEllipse(QRectF(4, 3, 4, 4))
            painter.drawEllipse(QRectF(10, 6, 4, 4))
            painter.drawEllipse(QRectF(5, 10, 4, 4))
        elif kind == "scene":
            painter.drawLine(3, 14, 8, 6)
            painter.drawLine(8, 6, 11, 11)
            painter.drawLine(9, 13, 12, 8)
            painter.drawLine(12, 8, 15, 14)
        elif kind in ("combine-add", "combine-subtract"):
            # A mask field with an add/subtract badge. Keeping the operator
            # outside the circle makes the distinction legible at 14px.
            painter.drawEllipse(QRectF(2.5, 4.5, 9, 9))
            painter.drawLine(10.5, 9, 15.5, 9)
            if kind == "combine-add":
                painter.drawLine(13, 6.5, 13, 11.5)
        elif kind == "trash":
            painter.drawLine(4, 5, 14, 5)          # lid
            painter.drawLine(7, 5, 7, 3)           # handle
            painter.drawLine(7, 3, 11, 3)
            painter.drawLine(11, 3, 11, 5)
            painter.drawLine(5, 5, 6, 15)          # can walls
            painter.drawLine(13, 5, 12, 15)
            painter.drawLine(6, 15, 12, 15)        # can base
            painter.drawLine(9, 7, 9, 13)          # ribs
        painter.end()
        return QIcon(pixmap)

    def _mask_combine_button(
        self,
        kind: str,
        label: str,
        parent: QWidget,
    ) -> QPushButton:
        button = QPushButton(label, parent)
        button.setObjectName("maskCombineButton")
        button.setIcon(self._mask_glyph(f"combine-{kind}"))
        button.setIconSize(QSize(14, 14))
        button.setFixedHeight(22)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def _overlay_settings_icon(self) -> QIcon:
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(QColor("#a8adb5"))
        font = QFont("Segoe MDL2 Assets")
        font.setPixelSize(15)
        font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        painter.setFont(font)
        painter.drawText(
            pixmap.rect(),
            Qt.AlignmentFlag.AlignCenter,
            chr(0xE713),
        )
        painter.end()
        return QIcon(pixmap)

    def _build_overlay_menu_button(self, parent: QWidget) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName("overlayMenuButton")
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        button.setFixedSize(30, 22)
        button.setIconSize(QSize(18, 18))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        menu = QMenu(button)
        self.overlay_mode_group = QActionGroup(menu)
        self.overlay_mode_group.setExclusive(True)
        self.overlay_mode_actions: dict[str, QAction] = {}
        for mode, label in self.OVERLAY_MODE_OPTIONS:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(mode == self._overlay_mode)
            action.triggered.connect(
                lambda _checked=False, value=mode: self._set_overlay_mode(value)
            )
            self.overlay_mode_group.addAction(action)
            self.overlay_mode_actions[mode] = action

        menu.addSeparator()
        self.overlay_auto_toggle_action = menu.addAction(
            "Automatically Toggle Overlay"
        )
        self.overlay_auto_toggle_action.setCheckable(True)
        self.overlay_auto_toggle_action.setChecked(self._overlay_auto_toggle)
        self.overlay_auto_toggle_action.toggled.connect(
            self._set_overlay_auto_toggle
        )
        self.overlay_show_tools_action = menu.addAction("Show Pins and Tools")
        self.overlay_show_tools_action.setCheckable(True)
        self.overlay_show_tools_action.setChecked(self._overlay_show_tools)
        self.overlay_show_tools_action.toggled.connect(self._set_overlay_show_tools)

        menu.addSeparator()
        color_settings = menu.addAction("Color Overlay Settings...")
        color_settings.triggered.connect(self._choose_overlay_color)
        button.setMenu(menu)
        self.overlay_menu = menu
        self.overlay_menu_button = button
        self._sync_overlay_menu_button()
        return button

    def _sync_overlay_menu_button(self) -> None:
        label = dict(self.OVERLAY_MODE_OPTIONS).get(self._overlay_mode, "Color Overlay")
        opacity = round(self._overlay_color.alphaF() * 100)
        for name in ("overlay_menu_button", "touchup_overlay_menu_button"):
            button = getattr(self, name, None)
            if button is None:
                continue
            button.setIcon(self._overlay_settings_icon())
            button.setToolTip(f"Overlay settings: {label} · {opacity}% opacity")

    def _set_overlay_mode(self, mode: str) -> None:
        if mode not in {value for value, _label in self.OVERLAY_MODE_OPTIONS}:
            return
        self._overlay_mode = mode
        self._settings.setValue(self.OVERLAY_MODE_KEY, mode)
        for value, action in getattr(self, "overlay_mode_actions", {}).items():
            action.setChecked(value == mode)
        self._sync_overlay_menu_button()
        self.mask_overlay_changed.emit()

    def _set_overlay_auto_toggle(self, enabled: bool) -> None:
        self._overlay_auto_toggle = bool(enabled)
        self._settings.setValue(
            self.OVERLAY_AUTO_TOGGLE_KEY, self._overlay_auto_toggle
        )
        if not self._overlay_auto_toggle and self._overlay_suppressed_for_drag:
            self._overlay_suppressed_for_drag = False
            self.mask_overlay_changed.emit()

    def _set_overlay_show_tools(self, enabled: bool) -> None:
        self._overlay_show_tools = bool(enabled)
        self._settings.setValue(
            self.OVERLAY_SHOW_TOOLS_KEY, self._overlay_show_tools
        )
        self.mask_overlay_changed.emit()

    def _set_overlay_color(self, color: QColor) -> None:
        if not color.isValid():
            return
        self._overlay_color = QColor(color)
        self._settings.setValue(
            self.OVERLAY_COLOR_KEY, int(self._overlay_color.rgba())
        )
        self._sync_overlay_menu_button()
        self.mask_overlay_changed.emit()

    def _build_overlay_color_dialog(self) -> QColorDialog:
        dialog = QColorDialog(self._overlay_color, self)
        dialog.setWindowTitle("Color Overlay Settings")
        dialog.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, True)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)

        # Qt's non-native picker puts the numeric HSV/RGB/alpha/HTML editor in
        # a lower-right widget and inserts a stretch before Custom colors. This
        # overlay picker only needs the palettes, color field and screen picker.
        root = dialog.layout()
        main = root.itemAt(0).layout() if root is not None and root.count() else None
        if main is not None and main.count() >= 2:
            left = main.itemAt(0).layout()
            right = main.itemAt(1).layout()
            if left is not None:
                pick_index = -1
                custom_index = -1
                for index in range(left.count()):
                    widget = left.itemAt(index).widget()
                    if isinstance(widget, QPushButton) and "Pick Screen Color" in widget.text():
                        pick_index = index
                    elif isinstance(widget, QLabel) and "Custom colors" in widget.text():
                        custom_index = index
                if 0 <= pick_index < custom_index:
                    for index in range(custom_index - 1, pick_index, -1):
                        item = left.itemAt(index)
                        widget = item.widget()
                        if item.spacerItem() is not None:
                            left.takeAt(index)
                        elif isinstance(widget, QLabel) and not widget.text().strip():
                            widget.hide()
            if right is not None:
                html_editor = dialog.findChild(QLineEdit, "qt_colorname_lineedit")
                details = html_editor.parentWidget() if html_editor is not None else None
                if details is not None:
                    details.hide()
                for index in range(right.count() - 1, -1, -1):
                    if right.itemAt(index).spacerItem() is not None:
                        right.takeAt(index)

                opacity_row = QWidget(dialog)
                opacity_row.setObjectName("overlayOpacityRow")
                opacity_layout = QHBoxLayout(opacity_row)
                opacity_layout.setContentsMargins(6, 4, 6, 0)
                opacity_layout.setSpacing(8)
                opacity_label = QLabel("Opacity", opacity_row)
                opacity_slider = QSlider(Qt.Orientation.Horizontal, opacity_row)
                opacity_slider.setObjectName("overlayOpacitySlider")
                opacity_slider.setRange(1, 100)
                opacity_spin = QSpinBox(opacity_row)
                opacity_spin.setObjectName("overlayOpacitySpin")
                opacity_spin.setRange(1, 100)
                opacity_spin.setSuffix("%")
                opacity_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
                opacity_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
                opacity_spin.setFixedWidth(54)
                opacity = max(1, min(100, round(self._overlay_color.alphaF() * 100)))
                opacity_slider.setValue(opacity)
                opacity_spin.setValue(opacity)

                def apply_opacity(value: int) -> None:
                    color = dialog.currentColor()
                    color.setAlpha(max(1, min(255, round(value * 255 / 100))))
                    dialog.setCurrentColor(color)

                opacity_slider.valueChanged.connect(opacity_spin.setValue)
                opacity_spin.valueChanged.connect(opacity_slider.setValue)
                opacity_slider.valueChanged.connect(apply_opacity)
                opacity_layout.addWidget(opacity_label)
                opacity_layout.addWidget(opacity_slider, 1)
                opacity_layout.addWidget(opacity_spin)
                right.insertWidget(1, opacity_row)
        dialog.adjustSize()
        return dialog

    def _choose_overlay_color(self) -> None:
        dialog = self._build_overlay_color_dialog()
        if dialog.exec() == QColorDialog.DialogCode.Accepted:
            self._set_overlay_color(dialog.currentColor())

    def _mask_tool_row(
        self,
        kind: str,
        label: str,
        *,
        shortcut: str | None = None,
        checkable: bool = False,
    ) -> QPushButton:
        """A full-width Lightroom-style tool row: glyph, label, optional
        shortcut chip. Child labels pass their clicks through to the button."""
        row = QPushButton(self)
        row.setObjectName("maskToolRow")
        row.setAccessibleName(label)
        row.setCheckable(checkable)
        row.setCursor(Qt.CursorShape.PointingHandCursor)
        row.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        inner = QHBoxLayout(row)
        inner.setContentsMargins(10, 0, 10, 0)
        inner.setSpacing(10)
        glyph = QLabel(row)
        glyph.setPixmap(self._mask_glyph(kind).pixmap(QSize(18, 18)))
        glyph.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        text = QLabel(label, row)
        text.setObjectName("maskToolRowLabel")
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        inner.addWidget(glyph)
        inner.addWidget(text)
        inner.addStretch(1)
        if shortcut:
            chip = QLabel(shortcut, row)
            chip.setObjectName("maskShortcutChip")
            chip.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            inner.addWidget(chip)
        return row

    def _build_mask_create_pane(self) -> QWidget:
        pane, body, layout = self._mask_pane()
        header = self._mask_pane_header("Create new mask", body)
        self.mask_create_title = header.findChild(QLabel, "maskPaneTitle")
        self.mask_create_back = QPushButton("Cancel", header)
        self.mask_create_back.setObjectName("maskLinkButton")
        self.mask_create_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mask_create_back.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mask_create_back.clicked.connect(self._cancel_mask_create)
        header.layout().addWidget(self.mask_create_back)
        layout.addWidget(header)

        content = QWidget(body)
        content.setObjectName("maskCreateContent")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(11, 8, 11, 8)
        cl.setSpacing(8)

        # One instruction for the whole pill group: hovering the photo lights up
        # the person or region under the cursor; the pills are explicit choices.
        self._scene_hint = QLabel(
            "Point at the photo — the person or region under the cursor lights up. "
            "Click to mask it.",
            content,
        )
        self._scene_hint.setObjectName("editorHint")
        self._scene_hint.setWordWrap(True)
        cl.addWidget(self._scene_hint)

        self.subject_mask_options = QWidget(content)
        subject_layout = QVBoxLayout(self.subject_mask_options)
        subject_layout.setContentsMargins(0, 0, 0, 0)
        subject_layout.setSpacing(6)
        # Subject / background come from BiRefNet, the scene pills from
        # OneFormer, but they're all "let the model pick a region" — so they wear
        # the same pill styling and sit in a matching two-column row.
        self.select_subject_button = self._action_button(
            "Select subject", self.subject_mask_options
        )
        self.select_subject_button.setObjectName("semanticMaskButton")
        self.select_subject_button.setAccessibleName("Select subject")
        self.select_background_button = self._action_button(
            "Select background", self.subject_mask_options
        )
        self.select_background_button.setObjectName("semanticMaskButton")
        self.select_background_button.setAccessibleName("Select background")
        self.select_subject_button.clicked.connect(
            lambda: self.request_subject_mask("subject")
        )
        self.select_background_button.clicked.connect(
            lambda: self.request_subject_mask("background")
        )
        subject_buttons_row = QHBoxLayout()
        subject_buttons_row.setContentsMargins(0, 0, 0, 0)
        subject_buttons_row.setSpacing(6)
        subject_buttons_row.addWidget(self.select_subject_button)
        subject_buttons_row.addWidget(self.select_background_button)
        subject_layout.addLayout(subject_buttons_row)
        self.subject_choice_panel = QFrame(self.subject_mask_options)
        self.subject_choice_panel.setObjectName("subjectChoicePanel")
        choice_layout = QVBoxLayout(self.subject_choice_panel)
        choice_layout.setContentsMargins(8, 8, 8, 8)
        choice_layout.setSpacing(6)
        choice_title = QLabel("Select subjects on photo", self.subject_choice_panel)
        choice_title.setObjectName("maskPaneTitle")
        choice_layout.addWidget(choice_title)
        self.subject_choice_hint = QLabel(
            "Click the highlighted people on the photo.",
            self.subject_choice_panel,
        )
        self.subject_choice_hint.setObjectName("editorHint")
        self.subject_choice_hint.setWordWrap(True)
        choice_layout.addWidget(self.subject_choice_hint)
        self.subject_choice_apply = self._action_button(
            "Create selected mask",
            self.subject_choice_panel,
        )
        self.subject_choice_apply.clicked.connect(self._apply_subject_choice)
        self.subject_choice_apply.setEnabled(False)
        choice_layout.addWidget(self.subject_choice_apply)
        self.subject_choice_panel.hide()
        subject_layout.addWidget(self.subject_choice_panel)
        self.subject_mask_options.hide()
        cl.addWidget(self.subject_mask_options)

        # Scene category pills flow straight on from the subject pills — same
        # styling, no divider — so the whole set reads as one group.
        semantic_grid = QGridLayout()
        semantic_grid.setContentsMargins(0, 0, 0, 0)
        semantic_grid.setHorizontalSpacing(6)
        semantic_grid.setVerticalSpacing(6)
        self._semantic_grid = semantic_grid
        self._semantic_mask_buttons: dict[str, QPushButton] = {}
        for category in SEMANTIC_MASK_CATEGORIES:
            button = self._action_button(category.title(), content)
            button.setObjectName("semanticMaskButton")
            button.clicked.connect(
                lambda _checked=False, selected=category: self.request_semantic_mask(selected)
            )
            button.hide()
            self._semantic_mask_buttons[category] = button
        cl.addLayout(semantic_grid)
        # Promptable click-to-select: isolate one specific person/object (incl.
        # one of several touching people) by clicking it.
        self.point_select_button = self._action_button("Click to Select (AI)", content)
        self.point_select_button.setObjectName("semanticMaskButton")
        self.point_select_button.setCheckable(True)
        self.point_select_button.setToolTip(
            "Click a person or object on the photo to select just that one — "
            "even when people overlap."
        )
        self.point_select_button.toggled.connect(self._set_point_select_active)
        cl.addWidget(self.point_select_button)
        self.download_mask_models_button = self._action_button(
            "Download AI Masking Tools", content
        )
        self.download_mask_models_button.setObjectName("semanticMaskButton")
        self.download_mask_models_button.setToolTip(
            "Download the local AI tools used for automatic photo selections."
        )
        self.download_mask_models_button.clicked.connect(self._download_mask_models)
        self.download_mask_models_button.hide()
        cl.addWidget(self.download_mask_models_button)
        self.semantic_mask_status = QLabel("Scene analysis starts when this tab opens.", content)
        self.semantic_mask_status.setObjectName("editorHint")
        self.semantic_mask_status.setWordWrap(True)
        cl.addWidget(self.semantic_mask_status)
        self._sync_mask_model_controls()

        cl.addWidget(self._mask_hairline(content))

        # Drawing tools, as a quiet icon list with shortcut chips.
        self._masking_hint = QLabel(self._MASK_IDLE_HINT, content)
        self._masking_hint.setObjectName("editorHint")
        self._masking_hint.setWordWrap(True)
        cl.addWidget(self._masking_hint)
        self.brush_tool_button = self._mask_tool_row("brush", "Brush", shortcut="B", checkable=True)
        self.linear_tool_button = self._mask_tool_row("linear", "Linear gradient", shortcut="L", checkable=True)
        self.radial_tool_button = self._mask_tool_row("radial", "Radial gradient", shortcut="R", checkable=True)
        self.luma_tool_button = self._mask_tool_row("range", "Luminance range")
        self.color_tool_button = self._mask_tool_row("range", "Color range", checkable=True)
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
        for tool in (
            self.brush_tool_button,
            self.linear_tool_button,
            self.radial_tool_button,
            self.luma_tool_button,
            self.color_tool_button,
        ):
            cl.addWidget(tool)

        cl.addWidget(self._mask_hairline(content))

        # Import stays, demoted to two quiet rows.
        self.add_painted_button = self._mask_tool_row("brush", "Import brush PNG")
        self.add_subject_button = self._mask_tool_row("scene", "Import subject PNG")
        self.add_painted_button.clicked.connect(self.add_painted_mask)
        self.add_subject_button.clicked.connect(self.add_subject_mask)
        cl.addWidget(self.add_painted_button)
        cl.addWidget(self.add_subject_button)

        layout.addWidget(content)
        layout.addStretch(1)
        return pane

    def _build_mask_work_pane(self) -> QWidget:
        pane, body, layout = self._mask_pane()

        # -- mask list + create, always at the top ----------------------------
        list_block = QWidget(body)
        list_block.setObjectName("maskListBlock")
        lb = QVBoxLayout(list_block)
        lb.setContentsMargins(11, 9, 11, 9)
        lb.setSpacing(7)

        # Keep the mask collection visually separate from the commands below it.
        # The inset frame also gives the selected-row indicator room to paint
        # without colliding with the first character of the mask name.
        self.mask_list_viewport = QFrame(list_block)
        self.mask_list_viewport.setObjectName("maskListViewport")
        viewport_layout = QVBoxLayout(self.mask_list_viewport)
        viewport_layout.setContentsMargins(4, 4, 4, 4)
        viewport_layout.setSpacing(4)

        self.masks_list = QListWidget(self.mask_list_viewport)
        self.masks_list.setObjectName("editorList")
        # Compact: the list hugs its rows (one line when there is one mask) and
        # only scrolls past a few, instead of a tall box of dead space.
        self.masks_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.masks_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.masks_list.setViewportMargins(4, 0, 4, 0)
        # No keyboard focus → no dotted focus rectangle drawn around the current
        # row (the "weird box"); selection is driven by clicks / setCurrentItem.
        self.masks_list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.masks_list.currentItemChanged.connect(self._handle_mask_selection_changed)
        model = self.masks_list.model()
        model.rowsInserted.connect(lambda *_: self._resize_mask_list())
        model.rowsRemoved.connect(lambda *_: self._resize_mask_list())
        viewport_layout.addWidget(self.masks_list)
        lb.addWidget(self.mask_list_viewport)
        self._resize_mask_list()

        # Add / Subtract live right under the list, Lightroom-style, so shaping
        # a group does not mean scrolling to the bottom of the detail.
        submask_row = QHBoxLayout()
        submask_row.setSpacing(6)
        self.add_submask_button = self._mask_combine_button(
            "add", "Add", list_block
        )
        self.add_submask_button.setToolTip(
            "Draw another shape into this mask's group — adjustments target the combined area."
        )
        self.add_submask_button.clicked.connect(lambda: self.add_submask("add"))
        self.subtract_submask_button = self._mask_combine_button(
            "subtract", "Subtract", list_block
        )
        self.subtract_submask_button.setToolTip(
            "Draw a shape that carves out of this mask's group."
        )
        self.subtract_submask_button.clicked.connect(lambda: self.add_submask("subtract"))
        submask_row.addWidget(self.add_submask_button)
        submask_row.addWidget(self.subtract_submask_button)
        lb.addLayout(submask_row)

        self.new_mask_button = QPushButton("＋  New mask", list_block)
        self.new_mask_button.setObjectName("newMaskButton")
        self.new_mask_button.setFixedHeight(22)
        self.new_mask_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_mask_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.new_mask_button.clicked.connect(
            lambda: self._open_mask_create_pane()
        )
        lb.addWidget(self.new_mask_button)
        overlay_row = QHBoxLayout()
        self.overlay_check = QCheckBox("Show overlay", list_block)
        self.overlay_check.setChecked(True)
        self.overlay_check.toggled.connect(lambda _on: self.mask_overlay_changed.emit())
        overlay_row.addWidget(self.overlay_check)
        overlay_row.addStretch(1)
        overlay_row.addWidget(self._build_overlay_menu_button(list_block))
        lb.addLayout(overlay_row)
        layout.addWidget(list_block)

        # -- everything for the selected mask, one continuous scroll below -----
        detail = QWidget(body)
        detail.setObjectName("maskDetail")
        dl = QVBoxLayout(detail)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(0)
        self._mask_detail = detail

        hidden_space = QWidget(detail)
        hidden_space.hide()
        self.space_id_edit = QLineEdit(hidden_space)
        self.space_id_edit.setText("space-source-full")
        self.space_width_spin = self._spin(hidden_space, 1, 200000, 1)
        self.space_height_spin = self._spin(hidden_space, 1, 200000, 1)
        self.add_space_button = QPushButton("Add Space", hidden_space)
        self.add_space_button.clicked.connect(self.add_coordinate_space)
        self.mask_space_combo = QComboBox(hidden_space)

        self._build_mask_shape_section(detail, dl)
        self._build_mask_range_section(detail, dl)
        self._build_mask_brush_section(detail, dl)
        self._build_mask_adjust_section(detail, dl)
        self._build_mask_group_section(detail, dl)
        layout.addWidget(detail)
        layout.addStretch(1)
        return pane

    # -- work-pane sections (each contextual on the selected mask's type) -----
    def _build_mask_shape_section(self, parent: QWidget, into: QVBoxLayout) -> None:
        section, sl = self._section("Shape", parent)
        self._mask_shape_section = section
        self.mask_feather_spin = self._double_spin(section, 0, 100, 50, decimals=1)
        self.mask_feather_row = self._slider_spin_row(
            "Feather", self.mask_feather_spin, minimum=0, maximum=100, scale=10, parent=section,
        )
        sl.addWidget(self.mask_feather_row)
        self.mask_feather_spin.valueChanged.connect(self._handle_mask_control_changed)

        # Retained for loading older sessions and the CLI refinement commands.
        # They are intentionally not part of the contextual GUI.
        self.mask_density_spin = self._double_spin(section, 0, 100, 100, decimals=1)
        self.mask_invert_check = QCheckBox("Invert", section)
        self.mask_density_row = self._slider_spin_row(
            "Density", self.mask_density_spin, minimum=0, maximum=100, scale=10, parent=section,
        )
        self.mask_density_row.hide()
        self.mask_invert_check.hide()
        self._mask_control_rows = [self.mask_feather_row]
        self.refine_low_spin = self._spin(section, 0, 255, 0)
        self.refine_high_spin = self._spin(section, 0, 255, 255)
        self.refine_pixels_spin = self._spin(section, -500, 500, 0)
        self.add_luma_refine_button = self._action_button("Luma", section)
        self.add_bounds_refine_button = self._action_button("Bounds", section)
        self.add_luma_refine_button.clicked.connect(self.add_luma_refinement)
        self.add_bounds_refine_button.clicked.connect(self.add_bounds_refinement)
        for widget in (
            self.refine_low_spin,
            self.refine_high_spin,
            self.refine_pixels_spin,
            self.add_luma_refine_button,
            self.add_bounds_refine_button,
        ):
            widget.hide()
        into.addWidget(section)

    def _build_mask_range_section(self, parent: QWidget, into: QVBoxLayout) -> None:
        section, rl = self._section("Range", parent)
        self._mask_range_section = section
        self.luminance_range_controls = QWidget(section)
        luminance_layout = QVBoxLayout(self.luminance_range_controls)
        luminance_layout.setContentsMargins(0, 0, 0, 0)
        luminance_layout.setSpacing(4)
        self.luminance_range_slider = _LuminanceRangeSlider(self.luminance_range_controls)
        self.show_luminance_map_check = QCheckBox(
            "Show luminance map", self.luminance_range_controls
        )
        luminance_layout.addWidget(self.luminance_range_slider)
        luminance_layout.addWidget(self.show_luminance_map_check)
        rl.addWidget(self.luminance_range_controls)

        self.color_refine_spin = self._spin(section, 0, 100, 30)
        self.color_refine_row = self._slider_spin_row(
            "Refine", self.color_refine_spin, minimum=0, maximum=100, parent=section
        )
        rl.addWidget(self.color_refine_row)

        # Legacy/internal values remain in the sidecar so the renderer and CLI
        # keep a stable contract. The GUI exposes them as one task-level control.
        self.range_sample_label = QLabel("", section)
        self.range_hint = QLabel("", section)
        self.range_low_spin = self._spin(section, 0, 255, 0)
        self.range_high_spin = self._spin(section, 0, 255, 255)
        self.range_tolerance_spin = self._spin(section, 1, 255, 45)
        self.range_feather_spin = self._spin(section, 0, 255, 20)
        self.range_low_row = QWidget(section)
        self.range_high_row = QWidget(section)
        self.range_tolerance_row = QWidget(section)
        self.range_feather_row = QWidget(section)
        self.resample_color_button = self._action_button("Resample Color", section)
        self.resample_color_button.setToolTip("Click, then sample a new color from the photo.")
        self.resample_color_button.clicked.connect(self.resample_selected_color_range)
        for widget in (
            self.range_sample_label,
            self.range_hint,
            self.range_low_spin,
            self.range_high_spin,
            self.range_tolerance_spin,
            self.range_feather_spin,
            self.range_low_row,
            self.range_high_row,
            self.range_tolerance_row,
            self.range_feather_row,
            self.resample_color_button,
        ):
            widget.hide()
        for spin in (
            self.range_low_spin, self.range_high_spin,
            self.range_tolerance_spin, self.range_feather_spin,
        ):
            spin.valueChanged.connect(self._handle_range_control_changed)
        self.luminance_range_slider.valuesChanged.connect(
            self._handle_luminance_range_changed
        )
        self.color_refine_spin.valueChanged.connect(self._handle_range_control_changed)
        self.show_luminance_map_check.toggled.connect(
            lambda _checked: self.mask_overlay_changed.emit()
        )
        into.addWidget(section)

    def _build_mask_brush_section(self, parent: QWidget, into: QVBoxLayout) -> None:
        section, bl = self._section("Brush", parent)
        self._mask_brush_section = section
        self.brush_size_spin = self._spin(section, 1, 500, 25)
        self.brush_feather_spin = self._spin(section, 0, 100, 50)
        self.brush_density_spin = self._spin(section, 0, 100, 100)
        self.brush_flow_spin = self._spin(section, 0, 100, 100)
        self.brush_size_row = self._slider_spin_row(
            "Size", self.brush_size_spin, minimum=1, maximum=500, parent=section
        )
        self.brush_feather_row = self._slider_spin_row(
            "Feather", self.brush_feather_spin, minimum=0, maximum=100, parent=section
        )
        self.brush_density_row = self._slider_spin_row(
            "Density", self.brush_density_spin, minimum=0, maximum=100, parent=section
        )
        self.brush_flow_row = self._slider_spin_row(
            "Flow", self.brush_flow_spin, minimum=0, maximum=100, parent=section
        )
        for row in (
            self.brush_size_row,
            self.brush_feather_row,
            self.brush_density_row,
            self.brush_flow_row,
        ):
            bl.addWidget(row)
        for spin in (
            self.brush_size_spin,
            self.brush_feather_spin,
            self.brush_density_spin,
            self.brush_flow_spin,
        ):
            spin.valueChanged.connect(self._handle_brush_control_changed)
        brush_row = QHBoxLayout()
        add_brush_button = self._action_button("Add", section)
        subtract_brush_button = self._action_button("Subtract", section)
        add_brush_button.clicked.connect(lambda: self.arm_brush_mask("add"))
        subtract_brush_button.clicked.connect(lambda: self.arm_brush_mask("subtract"))
        brush_row.addWidget(add_brush_button)
        brush_row.addWidget(subtract_brush_button)
        bl.addLayout(brush_row)
        into.addWidget(section)

    def _build_mask_adjust_section(self, parent: QWidget, into: QVBoxLayout) -> None:
        specs_by_key = {spec[0]: spec for spec in ADJUSTMENT_SPECS}
        self._mask_rows: dict[str, _AdjustmentRow] = {}
        # Grouped Light / Color / Effects, matching the global Adjust tab, so a
        # local edit reads the same as a global one.
        for title, keys in ADJUSTMENT_GROUPS:
            local_keys = [key for key in keys if key in MASK_ADJUSTMENT_KEYS]
            if not local_keys:
                continue
            section, sl = self._section(title, parent)
            for key in local_keys:
                row = _AdjustmentRow(*specs_by_key[key], parent=section)
                row.changed.connect(self._handle_mask_adjustment_changed)
                # While a local slider is dragged, hide the red overlay so the
                # underlying image change is visible; restore on release.
                row.slider.sliderPressed.connect(self._begin_mask_slider_drag)
                row.slider.sliderReleased.connect(self._end_mask_slider_drag)
                self._mask_rows[key] = row
                sl.addWidget(row)
            into.addWidget(section)
        reset_holder = QWidget(parent)
        rl = QHBoxLayout(reset_holder)
        rl.setContentsMargins(10, 6, 10, 6)
        self.reset_mask_adjustments_button = self._action_button("Reset adjustments", reset_holder)
        self.reset_mask_adjustments_button.clicked.connect(self.reset_mask_adjustments)
        rl.addWidget(self.reset_mask_adjustments_button)
        into.addWidget(reset_holder)

    def _build_mask_group_section(self, parent: QWidget, into: QVBoxLayout) -> None:
        # Add/Subtract now live under the mask list; only the destructive action
        # stays down here, kept apart from everything else on purpose.
        section, gl = self._section("Delete", parent)
        self.delete_mask_button = self._action_button("Delete Mask", section)
        self.delete_mask_button.setObjectName("deleteMaskButton")
        self.delete_mask_button.clicked.connect(self.delete_selected_mask)
        gl.addWidget(self.delete_mask_button)
        into.addWidget(section)

    def _mask_pane_header(self, title: str, parent: QWidget) -> QWidget:
        header = QWidget(parent)
        header.setObjectName("maskPaneHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        label = QLabel(title, header)
        label.setObjectName("maskPaneTitle")
        row.addWidget(label)
        row.addStretch(1)
        return header

    # The list shows one row when there is one mask and caps at a few before it
    # scrolls, so it never eats the panel with empty space.
    MASK_LIST_MAX_ROWS = 6

    def _make_mask_row_widget(
        self,
        mask_id: str,
        label: str,
        marker: str,
        is_child: bool,
        *,
        separated: bool = False,
        can_touch_up: bool = False,
    ) -> "_MaskListRow":
        """A compact mask row with contextual actions in a fixed right gutter."""
        row = _MaskListRow(self.masks_list)
        row.setObjectName("maskRow")
        row.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row.setProperty("selected", False)
        row.setFixedHeight(23 if separated else 22)  # +1 for the divider line
        if separated:
            # A rule between parent groups.
            row.setProperty("separated", True)
        inner = QHBoxLayout(row)
        # Children indent so the group reads as a group; the trash button keeps
        # a fixed gutter on the right whatever the row's depth.
        inner.setContentsMargins(22 if is_child else 10, 0, 10, 0)
        inner.setSpacing(6)
        if marker:
            tag = QLabel(marker, row)
            tag.setObjectName("maskRowMarker")
            tag.setStyleSheet("background-color: transparent; border: none;")
            tag.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            inner.addWidget(tag)
        text = QLabel(label, row)
        text.setObjectName("maskRowLabel")
        # This must be local rather than QSS-only: on Windows, QListWidget's
        # styled item background can otherwise give an embedded QLabel its own
        # opaque palette and cut a dark rectangle through the selected row.
        text.setStyleSheet("background-color: transparent; border: none;")
        # The label eats clicks by default; make it transparent so a click on the
        # name reaches the row's own mousePressEvent and selects it.
        text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        inner.addWidget(text)
        inner.addStretch(1)
        if can_touch_up:
            touchup = QToolButton(row)
            touchup.setObjectName("maskRowTouchup")
            touchup.setCursor(Qt.CursorShape.PointingHandCursor)
            touchup.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            touchup.setFixedSize(20, 20)
            touchup.setIcon(self._mask_glyph("brush"))
            touchup.setIconSize(QSize(15, 15))
            touchup.setToolTip("Touch up mask")
            touchup.clicked.connect(
                lambda _checked=False, mid=mask_id: self.open_mask_touchup(mid)
            )
            inner.addWidget(touchup)
        trash = QToolButton(row)
        trash.setObjectName("maskRowTrash")
        trash.setCursor(Qt.CursorShape.PointingHandCursor)
        trash.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        trash.setFixedSize(20, 20)
        trash.setIcon(self._mask_glyph("trash"))
        trash.setIconSize(QSize(15, 15))
        trash.setToolTip(
            "Delete this mask group" if not is_child else "Delete this submask"
        )
        trash.clicked.connect(lambda _checked=False, mid=mask_id: self.delete_mask(mid))
        inner.addWidget(trash)
        return row

    def _build_mask_touchup_page(self) -> QWidget:
        page = QWidget(self)
        page.setObjectName("maskTouchupPage")
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(page)
        header.setObjectName("maskTouchupHeader")
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(12, 9, 12, 8)
        header_layout.setSpacing(2)
        title = QLabel("Mask Touchup", header)
        title.setObjectName("maskTouchupTitle")
        self.mask_touchup_subtitle = QLabel("Mask", header)
        self.mask_touchup_subtitle.setObjectName("maskTouchupSubtitle")
        header_layout.addWidget(title)
        header_layout.addWidget(self.mask_touchup_subtitle)
        root.addWidget(header)

        overlay_bar = QWidget(page)
        overlay_layout = QHBoxLayout(overlay_bar)
        overlay_layout.setContentsMargins(12, 7, 12, 7)
        self.touchup_overlay_check = QCheckBox("Show overlay", overlay_bar)
        self.touchup_overlay_check.setChecked(self.overlay_check.isChecked())
        self.touchup_overlay_check.toggled.connect(self.overlay_check.setChecked)
        self.overlay_check.toggled.connect(self.touchup_overlay_check.setChecked)
        overlay_layout.addWidget(self.touchup_overlay_check)
        overlay_layout.addStretch(1)
        self.touchup_overlay_menu_button = QToolButton(overlay_bar)
        self.touchup_overlay_menu_button.setObjectName("overlayMenuButton")
        self.touchup_overlay_menu_button.setFixedSize(30, 22)
        self.touchup_overlay_menu_button.setIconSize(QSize(18, 18))
        self.touchup_overlay_menu_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.touchup_overlay_menu_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.touchup_overlay_menu_button.clicked.connect(
            lambda: self.overlay_menu.exec(
                self.touchup_overlay_menu_button.mapToGlobal(
                    self.touchup_overlay_menu_button.rect().bottomLeft()
                )
            )
        )
        overlay_layout.addWidget(self.touchup_overlay_menu_button)
        root.addWidget(overlay_bar)

        edge_section, edge_layout = self._section("Edge Detection", page)
        radius_spin = self._double_spin(edge_section, 0, 250, 0, decimals=1)
        radius_spin.setObjectName("maskTouchupRadius")
        radius_row = self._slider_spin_row(
            "Radius",
            radius_spin,
            minimum=0,
            maximum=250,
            scale=10,
            parent=edge_section,
        )
        radius_row.setToolTip(
            "Use nearby image edges to tighten the generated mask boundary."
        )
        edge_layout.addWidget(radius_row)
        root.addWidget(edge_section)

        refinement_section, refinement_layout = self._section(
            "Global Refinements", page
        )
        smooth_spin = self._spin(refinement_section, 0, 100, 0)
        feather_spin = self._double_spin(
            refinement_section, 0, 1000, 0, decimals=1
        )
        contrast_spin = self._spin(refinement_section, 0, 100, 0)
        shift_spin = self._spin(refinement_section, -100, 100, 0)
        self._mask_touchup_spins = {
            "edgeDetectionRadius": radius_spin,
            "edgeSmooth": smooth_spin,
            "edgeFeather": feather_spin,
            "edgeContrast": contrast_spin,
            "edgeShift": shift_spin,
        }
        for key, spin, label, minimum, maximum in (
            ("edgeSmooth", smooth_spin, "Smooth", 0, 100),
            ("edgeContrast", contrast_spin, "Contrast", 0, 100),
            ("edgeShift", shift_spin, "Shift Edge", -100, 100),
        ):
            spin.setObjectName(f"maskTouchup{key.removeprefix('edge')}")
            refinement_layout.addWidget(
                self._slider_spin_row(
                    label,
                    spin,
                    minimum=minimum,
                    maximum=maximum,
                    parent=refinement_section,
                )
            )
        feather_spin.setObjectName("maskTouchupFeather")
        feather_row, self.mask_touchup_feather_slider = (
            self._pixel_decade_slider_row(
                "Feather",
                feather_spin,
                parent=refinement_section,
            )
        )
        refinement_layout.insertWidget(1, feather_row)
        root.addWidget(refinement_section)

        refine_holder = QWidget(page)
        refine_layout = QHBoxLayout(refine_holder)
        refine_layout.setContentsMargins(12, 8, 12, 0)
        self.mask_touchup_refine_button = QPushButton("Refine Edges", refine_holder)
        self.mask_touchup_refine_button.setObjectName("maskTouchupSecondaryButton")
        self.mask_touchup_refine_button.setToolTip(
            "Clean up soft edges (hair, fur) with BiRefNet on any selections that "
            "haven't been refined yet. People and animals are refined automatically."
        )
        self.mask_touchup_refine_button.clicked.connect(self._refine_touchup_session)
        refine_layout.addWidget(self.mask_touchup_refine_button)
        root.addWidget(refine_holder)

        touchup_actions = QWidget(page)
        touchup_actions_layout = QHBoxLayout(touchup_actions)
        touchup_actions_layout.setContentsMargins(12, 8, 12, 0)
        touchup_actions_layout.setSpacing(8)
        self.mask_touchup_clear_button = QPushButton(
            "Clear Selection", touchup_actions
        )
        self.mask_touchup_clear_button.setObjectName("maskTouchupSecondaryButton")
        self.mask_touchup_clear_button.clicked.connect(
            self._toggle_mask_touchup_clear
        )
        self.mask_touchup_invert_button = QPushButton("Invert", touchup_actions)
        self.mask_touchup_invert_button.setObjectName("maskTouchupSecondaryButton")
        self.mask_touchup_invert_button.setCheckable(True)
        self.mask_touchup_invert_button.toggled.connect(
            self._toggle_mask_touchup_invert
        )
        touchup_actions_layout.addWidget(self.mask_touchup_clear_button)
        touchup_actions_layout.addWidget(self.mask_touchup_invert_button)
        root.addWidget(touchup_actions)

        ok_holder = QWidget(page)
        ok_layout = QHBoxLayout(ok_holder)
        ok_layout.setContentsMargins(12, 8, 12, 0)
        ok_layout.addStretch(1)
        self.mask_touchup_ok_button = QPushButton("OK", ok_holder)
        self.mask_touchup_ok_button.setObjectName("maskTouchupOkButton")
        self.mask_touchup_ok_button.setFixedWidth(72)
        self.mask_touchup_ok_button.clicked.connect(
            lambda: self._finish_mask_touchup(accepted=True)
        )
        ok_layout.addWidget(self.mask_touchup_ok_button)
        root.addWidget(ok_holder)
        root.addStretch(1)

        for spin in self._mask_touchup_spins.values():
            spin.valueChanged.connect(self._apply_mask_touchup_values)
        self._sync_overlay_menu_button()
        return page

    def open_mask_touchup(self, mask_id: str) -> QWidget | None:
        mask = self._mask_by_id(mask_id)
        if mask is None or mask.get("type") != "subject-select":
            return None
        if self._mask_touchup_mask_id is not None and self._mask_touchup_mask_id != mask_id:
            self._finish_mask_touchup(accepted=False)
        self._prompt_session_active = False   # a direct touch-up, not a click-select session
        self._bind_touchup_to_mask(mask_id)
        self.touchup_overlay_check.setChecked(self.overlay_check.isChecked())
        self._enter_touchup_view()
        return self._mask_touchup_page

    def _enter_touchup_view(self) -> None:
        for widget in (
            self._editor_tab_bar,
            self._editor_doc_bar,
            self.editor_stack,
            self._editor_footer,
        ):
            widget.hide()
        self._mask_touchup_page.show()
        self._mask_touchup_page.setFocus()

    def _bind_touchup_to_mask(self, mask_id: str) -> None:
        """Point the touch-up controls at a mask (its edge params, subtitle)."""
        mask = self._mask_by_id(mask_id)
        if mask is None:
            return
        self._select_mask_in_list(mask_id)
        params = mask.setdefault("params", {})
        self._mask_touchup_mask_id = mask_id
        self._mask_touchup_original_present = {
            key for key in MASK_TOUCHUP_KEYS if key in params
        }
        self._mask_touchup_original_values = {
            key: deepcopy(params[key]) for key in self._mask_touchup_original_present
        }
        values = {
            "edgeDetectionRadius": float(params.get("edgeDetectionRadius", 0.0)),
            "edgeSmooth": int(params.get("edgeSmooth", 0)),
            "edgeFeather": float(params.get("edgeFeather", 0.0)),
            "edgeContrast": int(params.get("edgeContrast", 0)),
            "edgeShift": int(params.get("edgeShift", 0)),
        }
        for key, spin in self._mask_touchup_spins.items():
            with QSignalBlocker(spin):
                spin.setValue(values[key])
        with QSignalBlocker(self.mask_touchup_feather_slider):
            self.mask_touchup_feather_slider.setValue(
                self._feather_pixels_to_slider(values["edgeFeather"])
            )
        cleared = bool(params.get("selectionCleared", False))
        self.mask_touchup_clear_button.setText(
            "Restore Selection" if cleared else "Clear Selection"
        )
        self.mask_touchup_clear_button.setEnabled(True)
        with QSignalBlocker(self.mask_touchup_invert_button):
            self.mask_touchup_invert_button.setChecked(bool(params.get("invert", False)))
        self.mask_touchup_invert_button.setEnabled(True)
        self.mask_touchup_refine_button.setEnabled(True)
        self.mask_touchup_subtitle.setText(_friendly_mask_label(mask))

    def _apply_mask_touchup_values(self, *_args: object) -> None:
        target = self._mask_by_id(self._mask_touchup_mask_id)
        if target is None:
            return
        target_params = target.setdefault("params", {})
        target_params.update(
            {
                "edgeDetectionRadius": round(
                    self._mask_touchup_spins["edgeDetectionRadius"].value(), 1
                ),
                "edgeSmooth": self._mask_touchup_spins["edgeSmooth"].value(),
                "edgeFeather": round(
                    self._mask_touchup_spins["edgeFeather"].value(), 1
                ),
                "edgeContrast": self._mask_touchup_spins["edgeContrast"].value(),
                "edgeShift": self._mask_touchup_spins["edgeShift"].value(),
            }
        )
        self.mask_overlay_changed.emit()
        if self._mask_has_local_adjustments(target):
            self.recipe_changed.emit(self._recipe)

    def _toggle_mask_touchup_clear(self) -> None:
        # In a click-to-select session Clear means "start this selection over":
        # delete every component built so far rather than just hiding them (a
        # hide would resurface them the moment the next click re-unions).
        if self._prompt_session_active:
            self._clear_prompt_session()
            return
        target = self._mask_by_id(self._mask_touchup_mask_id)
        if target is None:
            return
        params = target.setdefault("params", {})
        cleared = not bool(params.get("selectionCleared", False))
        params["selectionCleared"] = cleared
        self.mask_touchup_clear_button.setText(
            "Restore Selection" if cleared else "Clear Selection"
        )
        self.mask_overlay_changed.emit()
        if self._mask_has_local_adjustments(target):
            self.recipe_changed.emit(self._recipe)

    def _clear_prompt_session(self) -> None:
        """Delete every selection made in the current click-to-select session
        and reset it to empty, ready for fresh clicks."""
        root_id = self._prompt_session_root_id
        if root_id is not None:
            try:
                _path, session = self._ensure_session()
                members = [str(m.get("id")) for m in self._group_members(root_id)]
                doomed = set(members)
                session["operations"] = [
                    op
                    for op in session.get("operations", [])
                    if op.get("maskId") not in doomed
                ]
                for member_id in [m for m in members if m != root_id] + [root_id]:
                    remove_mask(session, member_id, force=True)
                for member_id in members:
                    self._prompt_meta.pop(member_id, None)
                self._write_session(session, "Cleared selection")
            except Exception as exc:
                self._set_status(f"Clear failed: {exc}")
                return
        self._prompt_session_root_id = None
        self._prompt_session_label = None
        self._mask_touchup_mask_id = None
        self._mask_touchup_original_present = set()
        self._mask_touchup_original_values = {}
        self._reset_touchup_controls()
        self.mask_touchup_subtitle.setText(
            "Click a person or object to start — each click adds to one selection"
        )
        self.mask_touchup_clear_button.setEnabled(False)
        self.mask_touchup_invert_button.setEnabled(False)
        self.mask_touchup_refine_button.setEnabled(False)
        self._set_status("Selection cleared — click to start again")
        self.mask_overlay_changed.emit()

    def _toggle_mask_touchup_invert(self, checked: bool) -> None:
        target = self._mask_by_id(self._mask_touchup_mask_id)
        if target is None:
            return
        target.setdefault("params", {})["invert"] = bool(checked)
        self.mask_overlay_changed.emit()
        if self._mask_has_local_adjustments(target):
            self.recipe_changed.emit(self._recipe)

    def _finish_mask_touchup(self, *, accepted: bool) -> None:
        target = self._mask_by_id(self._mask_touchup_mask_id)
        if target is not None and not accepted:
            target_params = target.setdefault("params", {})
            for key in MASK_TOUCHUP_KEYS:
                if key in self._mask_touchup_original_present:
                    target_params[key] = deepcopy(
                        self._mask_touchup_original_values[key]
                    )
                else:
                    target_params.pop(key, None)
            self.mask_overlay_changed.emit()
            if self._mask_has_local_adjustments(target):
                self.recipe_changed.emit(self._recipe)
        elif target is not None:
            self._mask_commit_timer.start()
            self._flush_mask_commit()
        self._mask_touchup_mask_id = None
        self._mask_touchup_original_present = set()
        self._mask_touchup_original_values = {}
        self._mask_touchup_page.hide()
        for widget in (
            self._editor_tab_bar,
            self._editor_doc_bar,
            self.editor_stack,
            self._editor_footer,
        ):
            widget.show()
        # Tear down any click-to-select session and drop the user back on the
        # mask adjustment (Work) pane with the button released.
        was_session = self._prompt_session_active
        self._prompt_session_active = False
        self._prompt_session_root_id = None
        self._prompt_session_label = None
        self._point_select_active = False
        self._refine_queue = []
        self._sync_point_select_button(False)
        if was_session:
            self._show_mask_pane(self.MASK_PANE_WORK)
        self.mask_overlay_changed.emit()

    def _resize_mask_list(self) -> None:
        list_widget = getattr(self, "masks_list", None)
        if list_widget is None:
            return
        count = list_widget.count()
        row_height = list_widget.sizeHintForRow(0) if count else 0
        if row_height <= 0:
            row_height = list_widget.fontMetrics().height() + 6
        visible = max(1, min(count, self.MASK_LIST_MAX_ROWS))
        frame = 2 * list_widget.frameWidth() + 4
        list_widget.setFixedHeight(visible * row_height + frame)

    def _mask_hairline(self, parent: QWidget) -> QFrame:
        line = QFrame(parent)
        line.setObjectName("maskHairline")
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFixedHeight(1)
        return line

    def _build_presets_tab(self) -> QWidget:
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

        target_section, target_layout = self._section("Apply To", tab)
        target_row = QHBoxLayout()
        target_label = QLabel("Target", target_section)
        target_label.setObjectName("editorControlLabel")
        self.preset_target_combo = QComboBox(target_section)
        self.preset_target_combo.currentIndexChanged.connect(
            lambda _index: self._sync_preset_buttons()
        )
        target_row.addWidget(target_label)
        target_row.addWidget(self.preset_target_combo, 1)
        target_layout.addLayout(target_row)
        layout.addWidget(target_section)

        built_in_section, built_in_layout = self._section("Built-in", tab)
        self.builtin_preset_list = QListWidget(built_in_section)
        self.builtin_preset_list.setObjectName("editorPresetList")
        self.builtin_preset_list.setMinimumHeight(190)
        for key, name, values in BUILTIN_PHOTO_PRESETS:
            item = QListWidgetItem(name)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip(_preset_adjustment_summary(values))
            self.builtin_preset_list.addItem(item)
        self.builtin_preset_list.setCurrentRow(0)
        self.builtin_preset_list.itemClicked.connect(
            lambda _item: self._apply_selected_builtin_preset()
        )
        built_in_layout.addWidget(self.builtin_preset_list)
        self.apply_builtin_preset_button = QPushButton("Apply", built_in_section)
        self.apply_builtin_preset_button.setObjectName("editorPrimaryButton")
        self.apply_builtin_preset_button.clicked.connect(self._apply_selected_builtin_preset)
        built_in_layout.addWidget(self.apply_builtin_preset_button)
        layout.addWidget(built_in_section)

        custom_section, custom_layout = self._section("My Presets", tab)
        self.custom_preset_list = QListWidget(custom_section)
        self.custom_preset_list.setObjectName("editorPresetList")
        self.custom_preset_list.setMinimumHeight(110)
        self.custom_preset_list.itemDoubleClicked.connect(
            lambda _item: self._apply_selected_custom_preset()
        )
        self.custom_preset_list.currentItemChanged.connect(
            lambda _current, _previous: self._sync_preset_buttons()
        )
        custom_layout.addWidget(self.custom_preset_list)
        custom_row = QHBoxLayout()
        self.save_custom_preset_button = QPushButton("Save Current", custom_section)
        self.apply_custom_preset_button = QPushButton("Apply", custom_section)
        self.delete_custom_preset_button = QPushButton("Delete", custom_section)
        self.save_custom_preset_button.clicked.connect(self._save_current_as_preset)
        self.apply_custom_preset_button.clicked.connect(self._apply_selected_custom_preset)
        self.delete_custom_preset_button.clicked.connect(self._delete_selected_custom_preset)
        custom_row.addWidget(self.save_custom_preset_button)
        custom_row.addWidget(self.apply_custom_preset_button)
        custom_row.addWidget(self.delete_custom_preset_button)
        custom_layout.addLayout(custom_row)
        layout.addWidget(custom_section)
        self._refresh_preset_targets()
        self._refresh_custom_preset_list()
        layout.addStretch(1)
        scroll.setWidget(tab)
        return scroll

    def _apply_selected_builtin_preset(self) -> None:
        item = self.builtin_preset_list.currentItem()
        if item is None:
            return
        preset_key = item.data(Qt.ItemDataRole.UserRole)
        for key, name, values in BUILTIN_PHOTO_PRESETS:
            if key == preset_key:
                self._apply_preset(name, values)
                return

    def _apply_selected_custom_preset(self) -> None:
        row = self.custom_preset_list.currentRow()
        if not 0 <= row < len(self._custom_photo_presets):
            return
        preset = self._custom_photo_presets[row]
        self._apply_preset(str(preset["name"]), preset["values"])

    def _apply_preset(self, name: str, values: dict[str, Any]) -> None:
        if self._source_path is None:
            return
        target = self._preset_target_mask()
        if self.preset_target_combo.currentData() == "photo":
            self._recipe = _recipe_with_preset(self._recipe, values)
            self._sync_rows_from_recipe()
            self.recipe_changed.emit(self._recipe)
            self._set_status(f"Applied {name} to photo")
            return
        if target is None or self._session is None:
            self._set_status("Select a preset target")
            return
        root_id = str(self._mask_root(target).get("id"))
        current = recipe_for_mask(self._session, root_id)
        local_recipe = _recipe_with_preset(current, values, keys=MASK_ADJUSTMENT_KEYS)
        replace_mask_operations(self._session, root_id, local_recipe)
        self._sync_mask_controls(self._selected_mask_dict())
        self.recipe_changed.emit(self._recipe)
        self._mask_commit_timer.start()
        self._set_status(f"Applied {name} to {_friendly_mask_label(target)}")

    def _save_current_as_preset(self) -> None:
        if self._source_path is None:
            return
        name, accepted = QInputDialog.getText(self, "Save Preset", "Preset name")
        name = " ".join(name.split())
        if not accepted or not name:
            return
        target = self._preset_target_mask()
        if self.preset_target_combo.currentData() == "photo":
            source_recipe = self._recipe
        elif target is not None and self._session is not None:
            source_recipe = recipe_for_mask(self._session, str(self._mask_root(target).get("id")))
        else:
            self._set_status("Select a preset target")
            return
        values = _preset_values_from_recipe(source_recipe)
        existing_row = next(
            (
                index
                for index, preset in enumerate(self._custom_photo_presets)
                if str(preset["name"]).casefold() == name.casefold()
            ),
            -1,
        )
        preset = {"name": name, "values": values}
        if existing_row >= 0:
            self._custom_photo_presets[existing_row] = preset
            selected_row = existing_row
            message = f"Updated preset {name}"
        else:
            self._custom_photo_presets.append(preset)
            selected_row = len(self._custom_photo_presets) - 1
            message = f"Saved preset {name}"
        self._store_custom_photo_presets()
        self._refresh_custom_preset_list(selected_row)
        self._set_status(message)

    def _delete_selected_custom_preset(self) -> None:
        row = self.custom_preset_list.currentRow()
        if not 0 <= row < len(self._custom_photo_presets):
            return
        name = str(self._custom_photo_presets[row]["name"])
        del self._custom_photo_presets[row]
        self._store_custom_photo_presets()
        self._refresh_custom_preset_list(min(row, len(self._custom_photo_presets) - 1))
        self._set_status(f"Deleted preset {name}")

    def _store_custom_photo_presets(self) -> None:
        self._settings.setValue(self.CUSTOM_PRESETS_KEY, json.dumps(self._custom_photo_presets))

    def _refresh_custom_preset_list(self, selected_row: int = -1) -> None:
        with QSignalBlocker(self.custom_preset_list):
            self.custom_preset_list.clear()
            for preset in self._custom_photo_presets:
                item = QListWidgetItem(str(preset["name"]))
                item.setToolTip(_preset_adjustment_summary(preset["values"]))
                self.custom_preset_list.addItem(item)
            if self._custom_photo_presets:
                self.custom_preset_list.setCurrentRow(max(0, selected_row))
        self._sync_preset_buttons()

    def _sync_preset_buttons(self) -> None:
        image_selected = self._source_path is not None
        custom_selected = self.custom_preset_list.currentItem() is not None
        target_selected = self.preset_target_combo.currentIndex() >= 0
        can_apply = image_selected and target_selected
        self.apply_builtin_preset_button.setEnabled(can_apply)
        self.save_custom_preset_button.setEnabled(can_apply)
        self.apply_custom_preset_button.setEnabled(can_apply and custom_selected)
        self.delete_custom_preset_button.setEnabled(custom_selected)

    def _preset_target_mask(self) -> dict[str, Any] | None:
        target_id = self.preset_target_combo.currentData()
        if not target_id or target_id == "photo":
            return None
        return self._mask_by_id(str(target_id))

    def _refresh_preset_targets(self) -> None:
        if not hasattr(self, "preset_target_combo"):
            return
        current = self.preset_target_combo.currentData()
        with QSignalBlocker(self.preset_target_combo):
            self.preset_target_combo.clear()
            self.preset_target_combo.addItem("Photo", "photo")
            if self._session is not None:
                for mask in self._session.get("masks", []):
                    if mask.get("parentId"):
                        continue
                    mask_id = str(mask.get("id") or "")
                    if mask_id:
                        self.preset_target_combo.addItem(
                            f"Mask: {_friendly_mask_label(mask)}",
                            mask_id,
                        )
            restored = self.preset_target_combo.findData(current)
            self.preset_target_combo.setCurrentIndex(max(0, restored))
        self._sync_preset_buttons()

    def set_image(self, source_path: str | Path | None) -> None:
        if self._mask_touchup_mask_id is not None or self._prompt_session_active:
            self._finish_mask_touchup(accepted=True)
        rejected_editor_asset = bool(source_path and is_editor_asset_path(source_path))
        if rejected_editor_asset:
            source_path = None
        if self._range_update_timer.isActive():
            self._regenerate_selected_range_mask()
        if self._mask_commit_timer.isActive():
            self._flush_mask_commit()
        self._pending_parent_id = None
        self._pending_combine = "add"
        self._active_mask_edit_id = None
        self._brush_paint_mode = None
        self._mask_create_mode = None
        self._color_resample_mask_id = None
        self._semantic_mask_request_context = None
        self._subject_mask_request_context = None
        self._point_select_active = False
        self._prompt_mask_task = None
        self._prompt_meta = {}
        self._background_matte_path = None
        self._background_matte_task = None
        self._depth_map_path = None
        self._depth_map_task = None
        self._clear_subject_choice()
        if not source_path:
            self._semantic_mask_result = None
            self._source_path = None
            self._populate_semantic_mask_buttons(())
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
        self._semantic_mask_result = None
        self._reset_scene_regions()
        self._populate_semantic_mask_buttons(())
        self._source_path = path
        # The prior image's mask bundle must never survive while the new
        # sidecar is being resolved. In particular, a photo with no sidecar
        # otherwise inherited the previous photo's in-memory bitmap masks.
        self._session = None
        migrate_bundle(path)
        self._session_path = resolve_session_for_read(path)
        self._recipe = self._load_recipe_for_path(path)
        self.subtitle_label.setText(path.name)
        self._sync_rows_from_recipe()
        self._sync_enabled()
        self._refresh_session_views()
        self.recipe_changed.emit(self._recipe)
        if self.editor_stack.currentIndex() == 1:
            QTimer.singleShot(0, self._ensure_semantic_inventory)

    def reset_recipe(self) -> None:
        self._recipe = EditRecipe()
        self._sync_rows_from_recipe()
        self._set_status("Adjustments reset")
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

    # -- curves ---------------------------------------------------------------
    _CURVE_RECIPE_KEYS = CURVE_RECIPE_KEYS

    def _set_curve_channel(self, channel: str) -> None:
        for name, button in self.curve_channel_buttons.items():
            with QSignalBlocker(button):
                button.setChecked(name == channel)
        self.curve_editor.set_channel(channel)

    def _handle_curve_changed(self, channel: str, points: object) -> None:
        data = asdict(self._recipe)
        data[self._CURVE_RECIPE_KEYS[channel]] = points
        self._recipe = EditRecipe.from_dict(data)
        self.recipe_changed.emit(self._recipe)

    def _handle_curve_selection(self, point: object) -> None:
        enabled = point is not None
        for spin in (self.curve_input_spin, self.curve_output_spin):
            spin.setEnabled(enabled)
        if not enabled:
            return
        x, y = point  # type: ignore[misc]
        self._updating_curve_io = True
        try:
            self.curve_input_spin.setValue(int(x))
            self.curve_output_spin.setValue(int(y))
        finally:
            self._updating_curve_io = False

    def _handle_curve_io_changed(self, _value: int) -> None:
        if getattr(self, "_updating_curve_io", False):
            return
        self.curve_editor.move_selected_to(
            self.curve_input_spin.value(), self.curve_output_spin.value()
        )

    def _sync_curves_from_recipe(self) -> None:
        data = asdict(self._recipe)
        for channel, key in self._CURVE_RECIPE_KEYS.items():
            self.curve_editor.set_points(channel, data.get(key))

    def _sync_rows_from_recipe(self) -> None:
        data = asdict(self._recipe)
        for key, row in self._rows.items():
            row.set_value(float(data.get(key) or 0.0))
        # A session that carries a shaped vignette opens with the controls
        # showing, otherwise the shape would be invisible behind the collapsed
        # disclosure.
        defaults = asdict(EditRecipe())
        if any(data.get(key) != defaults[key] for key in VIGNETTE_OPTION_KEYS):
            self._rows["vignette"].set_expanded(True)
        self._sync_curves_from_recipe()
        self._sync_background_controls()

    def _sync_enabled(self) -> None:
        enabled = self._source_path is not None
        for row in self._rows.values():
            row.setEnabled(enabled)
        self.reset_button.setEnabled(enabled)
        self.save_button.setEnabled(enabled)
        self.save_copy_button.setEnabled(enabled and not self._copy_save_busy)
        self._sync_semantic_mask_buttons()
        self._sync_mask_pane_enabled()
        self._sync_preset_buttons()
        for widget_name in (
            "editor_stack",
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
            "select_subject_button",
            "select_background_button",
        ):
            widget = getattr(self, widget_name, None)
            if widget is not None:
                widget.setEnabled(enabled)

    def _sync_semantic_mask_buttons(self) -> None:
        result_is_current = (
            self._source_path is not None
            and self._semantic_mask_result is not None
            and self._semantic_mask_result.source_path == self._source_path.resolve()
        )
        enabled = result_is_current and self._semantic_mask_task is None
        for button in getattr(self, "_semantic_mask_buttons", {}).values():
            button.setEnabled(enabled)
        subject_enabled = self._source_path is not None and self._subject_mask_task is None
        for name in ("select_subject_button", "select_background_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(subject_enabled)
        subject_category_detected = bool(
            result_is_current
            and self._semantic_mask_result is not None
            and SUBJECT_MASK_SEMANTIC_CATEGORIES.intersection(
                self._semantic_mask_result.detected_categories
            )
        )
        if hasattr(self, "subject_mask_options"):
            self.subject_mask_options.setVisible(subject_category_detected)

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

    @property
    def status_message(self) -> str:
        """Most recent status text. The footer status line was removed, so this
        (and the ``status_changed`` signal) is how status is surfaced."""
        return self._status_message

    def _set_status(self, message: str) -> None:
        self._status_message = message
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
            migrate_bundle(self._source_path)
            self._session_path = resolve_session_for_read(self._source_path)
        if self._session_path.exists():
            session = load_session(self._session_path)
        else:
            ensure_edit_root(self._source_path.parent)
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
        self._session = None
        session = None
        if self._session_path is not None and self._session_path.exists():
            try:
                session = self._load_session()
            except Exception as exc:
                self._set_status(f"Saved edits could not be loaded: {exc}")
                self.masks_list.clear()
                self._refresh_preset_targets()
                list_blocker.unblock()
                self._sync_mask_controls(None)
                self.mask_overlay_changed.emit()
                return
        self.masks_list.clear()
        self._refresh_reference_combos(session)
        if session is None:
            self._refresh_preset_targets()
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
        seen_parent = False
        for mask, is_child in ordered:
            mask_id = str(mask.get("id"))
            label = _friendly_mask_label(mask)
            marker = ""
            if is_child:
                marker = "−" if str(mask.get("combine", "add")) == "subtract" else "↳"
            # A divider above each parent group after the first.
            separated = not is_child and seen_parent
            if not is_child:
                seen_parent = True
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, mask.get("id"))
            item.setToolTip(mask_id)
            # No item text — the row widget draws the name. Setting item text made
            # the style draw its own selection box behind the transparent-label
            # widget (the "weird box"). Keep the label for screen readers only.
            item.setData(Qt.ItemDataRole.AccessibleTextRole, label)
            row = self._make_mask_row_widget(
                mask_id,
                label,
                marker,
                is_child,
                separated=separated,
                can_touch_up=mask.get("type") == "subject-select",
            )
            row.clicked.connect(lambda it=item: self.masks_list.setCurrentItem(it))
            row.doubleClicked.connect(
                lambda mid=mask_id: self._begin_mask_edit(mid)
            )
            # Height from the row's fixed height (its layout hint is a couple px
            # shorter, which clipped the bottom). Width 0 so the item tracks the
            # live viewport width — a captured viewport().width() went stale when
            # the panel resized and left the row (and its trash) clipped.
            item.setSizeHint(QSize(0, row.minimumHeight()))
            self.masks_list.addItem(item)
            self.masks_list.setItemWidget(item, row)
            if selected_mask_id and mask.get("id") == selected_mask_id:
                self.masks_list.setCurrentItem(item)
        list_blocker.unblock()
        self._refresh_preset_targets()
        self._sync_space_defaults(session)
        self._sync_mask_row_selection()
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
                if member.get("type") in (*self.MASK_SHAPE_TYPES, "bitmap", "subject-select")
            ]
            components = [
                (
                    self._component_type(member),
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
            elif mask.get("type") == "subject-select":
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
        subject_choice_active = self._subject_choice_result is not None
        subject_candidates: list[dict[str, Any]] | None = None
        if subject_choice_active and self._subject_choice_result is not None:
            mask_type = None
            params = None
            selected_index = None
            components = None
            subject_candidates = [
                {
                    "id": component.component_id,
                    "assetPath": str(component.mask_path),
                    "bbox": component.bbox,
                    "coordinateSize": self._subject_choice_result.source_size,
                    "selected": (
                        component.component_id in self._subject_choice_selected_ids
                    ),
                }
                for component in self._subject_choice_result.components
            ]
        masks_tab = self.editor_stack.currentIndex() == 1
        interactive = masks_tab and self._source_path is not None
        create_pane_active = bool(
            hasattr(self, "mask_stack")
            and self.mask_stack.currentIndex() == self.MASK_PANE_CREATE
        )
        # Scene picking belongs exclusively to New Mask. It yields to any
        # armed tool and, in the overlay, to the selected mask's handles.
        # Promptable click-to-select: when armed on the New Mask pane, a click
        # anywhere becomes a SAM point prompt. It takes over from scene picking.
        # Click-to-select stays live during its touch-up session even though the
        # New Mask pane is hidden behind the touch-up page, so the user can keep
        # adding to the same mask.
        point_pick = bool(
            interactive
            and (create_pane_active or getattr(self, "_prompt_session_active", False))
            and getattr(self, "_point_select_active", False)
            and self._mask_create_mode is None
            and self._brush_paint_mode is None
            and self._active_mask_edit_id is None
            and self._color_resample_mask_id is None
            and self._subject_choice_result is None
        )
        scene_pick = (
            interactive
            and create_pane_active
            and not point_pick
            and self._mask_create_mode is None
            and self._brush_paint_mode is None
            and self._active_mask_edit_id is None
            and self._color_resample_mask_id is None
            and self._subject_choice_result is None
        )
        show_luminance_map = bool(
            mask is not None
            and mask.get("uiStyle") == "luminance-range"
            and hasattr(self, "show_luminance_map_check")
            and self.show_luminance_map_check.isChecked()
        )
        scene_index_value = self._ensure_scene_index() if scene_pick else None
        # A centered "working…" spinner while New Mask analysis runs, so the pane
        # never reads as frozen. Ordered by what the user is waiting on.
        busy_message: str | None = None
        if interactive and create_pane_active and not self._prompt_session_active:
            if self._mask_models_download_task is not None:
                busy_message = "Downloading AI masking tools..."
            elif self._semantic_mask_task is not None:
                busy_message = "Analyzing photo..."
            elif self._people_instance_task is not None:
                busy_message = "Finding people..."
            elif self._scene_index_task is not None and self._scene_index is None:
                busy_message = "Mapping regions..."
        return {
            "scene_index": scene_index_value,
            "scene_pick": scene_pick,
            "point_pick": point_pick,
            "interactive": interactive,
            "busy_message": busy_message,
            "show_overlay": (
                interactive
                and (
                    self.overlay_check.isChecked()
                    or show_luminance_map
                    or subject_choice_active
                )
                and not self._overlay_suppressed_for_drag
            ),
            "overlay_mode": "white-black" if show_luminance_map else self._overlay_mode,
            "overlay_color": QColor(self._overlay_color),
            "show_tools": self._overlay_show_tools,
            "create_mode": self._mask_create_mode if interactive else None,
            "create_combine": self._pending_combine if interactive else "add",
            "brush_mode": self._brush_paint_mode if interactive else None,
            "brush_size": self.brush_size_spin.value() if hasattr(self, "brush_size_spin") else 25,
            "brush_feather": (
                self.brush_feather_spin.value()
                if hasattr(self, "brush_feather_spin")
                else 50
            ),
            "brush_flow": self.brush_flow_spin.value() if hasattr(self, "brush_flow_spin") else 100,
            "mask_type": mask_type,
            "params": params,
            "components": components,
            "subject_candidates": subject_candidates,
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
        if not width or not height:
            # Unreadable or vanished file; callers treat None as "no canvas".
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

    @staticmethod
    def _attach_mask_to_group(
        session: dict[str, Any],
        mask: dict[str, Any],
        parent_id: str | None,
        combine: str,
    ) -> bool:
        if parent_id is None or parent_id not in mask_ids(session):
            return False
        mask["parentId"] = parent_id
        if combine == "subtract":
            mask["combine"] = "subtract"
        else:
            mask.pop("combine", None)
        return True

    @staticmethod
    def _component_type(mask: dict[str, Any]) -> str:
        return "bitmap" if mask.get("type") == "subject-select" else str(mask.get("type"))

    def _group_components(self, root_id: str) -> list[tuple[str, dict[str, Any], str]]:
        out: list[tuple[str, dict[str, Any], str]] = []
        for mask in self._group_members(root_id):
            if mask.get("type") not in (*self.MASK_SHAPE_TYPES, "bitmap", "subject-select"):
                continue
            combine = "add" if mask.get("id") == root_id else str(mask.get("combine", "add"))
            out.append((self._component_type(mask), self._component_params(mask), combine))
        return out

    def _component_params(self, mask: dict[str, Any]) -> dict[str, Any]:
        params = dict(mask.get("params") or {})
        if mask.get("type") in ("bitmap", "subject-select"):
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

    def request_subject_mask(self, request: str) -> None:
        normalized = request.strip().casefold()
        if normalized not in ("subject", "background"):
            self._set_status(f"Unknown subject mask request: {request}")
            return
        if self._source_path is None:
            self._set_status("Select an image first")
            return
        self.subject_warm_requested.emit("model")
        existing_id = self._semantic_mask_id(normalized)
        if self._pending_parent_id is None and existing_id:
            self._select_mask_in_list(existing_id)
            self._set_status(f"Selected {normalized.title()} mask")
            return
        if self._subject_mask_task is not None:
            self._set_status("Subject analysis is still running")
            return
        self._subject_mask_request_context = (
            self._pending_parent_id,
            self._pending_combine,
        )
        task = SubjectMaskTask(self._source_path, normalized)
        task.signals.progress.connect(
            self._handle_subject_mask_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            self._handle_subject_mask_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            self._handle_subject_mask_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._subject_mask_task = task
        message = f"Preparing {normalized.title()} mask..."
        self.semantic_mask_status.setText(message)
        self.semantic_mask_status.show()
        self._set_status(message)
        self._sync_semantic_mask_buttons()
        self._semantic_mask_pool.start(task)

    def _handle_subject_mask_progress(self, request: str, message: str) -> None:
        if self._subject_mask_task is None:
            return
        self.semantic_mask_status.setText(message)
        self.semantic_mask_status.show()
        self._set_status(message or f"Preparing {request.title()} mask...")

    def _handle_subject_mask_finished(
        self,
        request: str,
        source_path: str,
        result: object,
    ) -> None:
        self._subject_mask_task = None
        request_context = self._subject_mask_request_context
        self._subject_mask_request_context = None
        self._sync_semantic_mask_buttons()
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            return
        if not isinstance(result, SubjectMaskResult):
            self._handle_subject_mask_failed(request, source_path, "Invalid mask result")
            return
        try:
            parent_id, combine = request_context or (None, "add")
            if request == "subject" and len(result.components) > 1:
                self._show_subject_choice(result, (parent_id, combine))
                cache_note = " from cache" if result.cache_hit else ""
                self.semantic_mask_status.setText(
                    f"Found {len(result.components)} subjects{cache_note}. Choose at least one."
                )
                self.semantic_mask_status.show()
                self._set_status(self.semantic_mask_status.text())
                return
            mask_id = self._register_subject_mask(
                request,
                result,
                parent_id=parent_id,
                combine=combine,
            )
        except Exception as exc:
            self._handle_subject_mask_failed(request, source_path, str(exc))
            return
        self.semantic_mask_status.hide()
        self._select_mask_in_list(mask_id)
        cache_note = " from cache" if result.cache_hit else ""
        self._set_status(f"Created {request.title()} mask{cache_note}")

    def _handle_subject_mask_failed(
        self,
        request: str,
        source_path: str,
        message: str,
    ) -> None:
        self._subject_mask_task = None
        self._subject_mask_request_context = None
        self._sync_semantic_mask_buttons()
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            return
        text = f"{request.title()} mask failed: {message}"
        self.semantic_mask_status.setText(text)
        self.semantic_mask_status.show()
        self._set_status(text)

    def _show_subject_choice(
        self,
        result: SubjectMaskResult,
        context: tuple[str | None, str],
    ) -> None:
        self._clear_subject_choice()
        self._subject_choice_result = result
        self._subject_choice_context = context
        self._subject_choice_selected_ids.clear()
        self.subject_choice_panel.show()
        self._sync_subject_choice()

    def _clear_subject_choice(self) -> None:
        self._subject_choice_result = None
        self._subject_choice_context = None
        self._subject_choice_selected_ids.clear()
        if hasattr(self, "subject_choice_apply"):
            self.subject_choice_apply.setEnabled(False)
            self.subject_choice_panel.hide()
        if hasattr(self, "mask_overlay_changed"):
            self.mask_overlay_changed.emit()

    def handle_overlay_subject_candidate_toggled(self, component_id: str) -> None:
        result = self._subject_choice_result
        if result is None or component_id not in {
            component.component_id for component in result.components
        }:
            return
        if component_id in self._subject_choice_selected_ids:
            self._subject_choice_selected_ids.remove(component_id)
        else:
            self._subject_choice_selected_ids.add(component_id)
        self._sync_subject_choice()

    def _sync_subject_choice(self) -> None:
        result = self._subject_choice_result
        if result is None:
            return
        count = len(self._subject_choice_selected_ids)
        self.subject_choice_apply.setEnabled(count > 0)
        self.subject_choice_hint.setText(
            "Click the highlighted people on the photo."
            if count == 0
            else f"{count} subject{'s' if count != 1 else ''} selected."
        )
        self.mask_overlay_changed.emit()

    def _apply_subject_choice(self) -> None:
        result = self._subject_choice_result
        if result is None:
            return
        selected_ids = tuple(
            component.component_id
            for component in result.components
            if component.component_id in self._subject_choice_selected_ids
        )
        if not selected_ids:
            self._set_status("Select at least one subject")
            return
        try:
            mask_path = combine_subject_components(result, selected_ids)
            all_ids = tuple(component.component_id for component in result.components)
            if selected_ids == all_ids:
                category = "subject"
            elif len(selected_ids) == 1:
                category = selected_ids[0].replace("-", "_")
            else:
                suffix = "_".join(value.rsplit("-", 1)[-1] for value in selected_ids)
                category = f"subjects_{suffix}"
            parent_id, combine = self._subject_choice_context or (None, "add")
            mask_id = self._register_subject_mask(
                "subject",
                result,
                parent_id=parent_id,
                combine=combine,
                mask_path=mask_path,
                category=category,
            )
        except Exception as exc:
            self._set_status(f"Could not create subject mask: {exc}")
            return
        self._clear_subject_choice()
        self.semantic_mask_status.hide()
        self._show_mask_pane(self.MASK_PANE_WORK)
        self._select_mask_in_list(mask_id)
        self._set_status("Created selected subject mask")

    def _populate_semantic_mask_buttons(self, categories: tuple[str, ...]) -> None:
        grid = getattr(self, "_semantic_grid", None)
        buttons = getattr(self, "_semantic_mask_buttons", {})
        if grid is None:
            return
        for button in buttons.values():
            grid.removeWidget(button)
            button.hide()
        displayed_categories = tuple(
            category
            for category in categories
            if category not in SUBJECT_MASK_SEMANTIC_CATEGORIES
        )
        for index, category in enumerate(displayed_categories):
            button = buttons.get(category)
            if button is None:
                continue
            grid.addWidget(button, index // 2, index % 2)
            button.show()
        if categories:
            self.semantic_mask_status.hide()
        elif self._source_path is None:
            self.semantic_mask_status.setText("Select an image to analyze.")
            self.semantic_mask_status.show()
        elif self._semantic_mask_result is not None:
            self.semantic_mask_status.setText("No supported scene regions detected.")
            self.semantic_mask_status.show()
        elif self._semantic_mask_task is None:
            self.semantic_mask_status.setText("Scene analysis starts when this tab opens.")
            self.semantic_mask_status.show()
        self._sync_semantic_mask_buttons()

    def _ensure_semantic_inventory(self) -> None:
        self._sync_mask_model_controls()
        if not self._mask_models_are_installed():
            self._populate_semantic_mask_buttons(())
            if self._mask_models_download_task is None:
                self.semantic_mask_status.setText(
                    "Download AI masking tools to enable automatic selections."
                )
                self.semantic_mask_status.show()
            return
        if self._source_path is None:
            self._populate_semantic_mask_buttons(())
            return
        if (
            self._semantic_mask_result is not None
            and self._semantic_mask_result.source_path == self._source_path.resolve()
        ):
            self._populate_semantic_mask_buttons(
                self._semantic_mask_result.detected_categories
            )
            return
        if self._semantic_mask_task is not None:
            return
        self._start_semantic_mask_task(SEMANTIC_MASK_INVENTORY_REQUEST)

    def _start_semantic_mask_task(self, request: str) -> None:
        if self._source_path is None or self._semantic_mask_task is not None:
            return
        self._semantic_mask_request_context = (
            None
            if request == SEMANTIC_MASK_INVENTORY_REQUEST
            else (self._pending_parent_id, self._pending_combine)
        )
        task = SemanticMaskTask(self._source_path, request)
        task.signals.progress.connect(
            self._handle_semantic_mask_progress,
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.finished.connect(
            self._handle_semantic_mask_finished,
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            self._handle_semantic_mask_failed,
            Qt.ConnectionType.QueuedConnection,
        )
        self._semantic_mask_task = task
        if request == SEMANTIC_MASK_INVENTORY_REQUEST:
            message = "Analyzing scene..."
        else:
            message = f"Preparing {request.title()} mask..."
        self.semantic_mask_status.setText(message)
        self.semantic_mask_status.show()
        self._set_status(message)
        self._sync_semantic_mask_buttons()
        self._semantic_mask_pool.start(task)
        self.mask_overlay_changed.emit()  # surface the "Analyzing…" spinner

    def request_semantic_mask(self, category: str) -> None:
        normalized = category.strip().casefold()
        if normalized not in SEMANTIC_MASK_CATEGORIES:
            self._set_status(f"Unknown mask category: {category}")
            return
        if normalized in SUBJECT_MASK_SEMANTIC_CATEGORIES:
            self.request_subject_mask("subject")
            return
        if self._source_path is None:
            self._set_status("Select an image first")
            return
        existing_id = self._semantic_mask_id(normalized)
        if self._pending_parent_id is None and existing_id:
            self._select_mask_in_list(existing_id)
            self._set_status(f"Selected {normalized.title()} mask")
            return
        result = self._semantic_mask_result
        if result is not None and result.source_path == self._source_path.resolve():
            try:
                mask_id = self._register_semantic_mask(
                    normalized,
                    result,
                    parent_id=self._pending_parent_id,
                    combine=self._pending_combine,
                )
            except Exception as exc:
                self._handle_semantic_mask_failed(
                    normalized,
                    str(self._source_path.resolve()),
                    str(exc),
                )
                return
            self._select_mask_in_list(mask_id)
            self._set_status(f"Created {normalized.title()} mask")
            return
        if self._semantic_mask_task is not None:
            self._set_status("Scene analysis is still running")
            return
        self._start_semantic_mask_task(normalized)

    def _ensure_scene_index(self) -> SceneRegionIndex | None:
        """Label map over the detected regions, built once per analysis.

        Returns None while the background build runs — hovering simply does
        nothing for that moment rather than stalling the tab switch.
        """
        result = self._semantic_mask_result
        if result is None or self._source_path is None:
            return None
        if result.source_path != self._source_path.resolve():
            return None
        self._ensure_people_instances(result)
        if self._scene_index_source == result.source_path:
            return self._scene_index
        if self._scene_index_task is None:
            # Leave the merged people region out of the base index: people are
            # only ever hovered as individuals (folded in once SAM splits them),
            # so there's no "all people" flash or old-picker click in between.
            base_categories = tuple(
                category
                for category in result.detected_categories
                if category != "people"
            )
            task = SceneIndexTask(
                result.source_path,
                result.source_size,
                result.mask_paths,
                base_categories,
            )
            task.signals.ready.connect(
                self._handle_scene_index_ready,
                Qt.ConnectionType.QueuedConnection,
            )
            self._scene_index_task = task
            self._semantic_mask_pool.start(task)
        return None

    def _handle_scene_index_ready(self, source_path: str, index: object) -> None:
        self._scene_index_task = None
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            return
        self._scene_index_base = index if isinstance(index, SceneRegionIndex) else None
        self._scene_index = self._compose_scene_index(Path(source_path))
        self._scene_index_source = Path(source_path)
        self.mask_overlay_changed.emit()

    def _reset_scene_regions(self) -> None:
        self._scene_index = None
        self._scene_index_base = None
        self._scene_index_source = None
        self._scene_index_task = None
        self._people_instances = None
        self._people_instances_source = None
        self._people_instance_task = None

    def _ensure_people_instances(self, result: SemanticMaskResult) -> None:
        """Kick off the one-time SAM split of the people region into individuals."""
        if "people" not in result.detected_categories:
            return
        if self._people_instances_source == result.source_path:
            return
        if self._people_instance_task is not None:
            return
        people_path = result.mask_paths.get("people")
        if not people_path or not Path(people_path).is_file():
            return
        task = PeopleInstanceTask(result.source_path, people_path)
        task.signals.ready.connect(
            self._handle_people_instances_ready,
            Qt.ConnectionType.QueuedConnection,
        )
        self._people_instance_task = task
        # People discovery embeds the image in SAM — later manual clicks reuse it.
        self._request_prompt_warm()
        # Run on the global pool, not the single-thread semantic pool: instance
        # discovery takes seconds and must not stall the fast category hover
        # index or a manual click-to-select queued behind it.
        # (Callers emit mask_overlay_changed after this — including during a
        # mask_overlay_state build — so no emit here, which would re-enter it.)
        QThreadPool.globalInstance().start(task)

    def _handle_people_instances_ready(self, source_path: str, instances: object) -> None:
        self._people_instance_task = None
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            return
        self._people_instances = instances if isinstance(instances, list) else None
        self._people_instances_source = Path(source_path)
        if self._scene_index_base is not None:
            self._scene_index = self._compose_scene_index(Path(source_path))
            self.mask_overlay_changed.emit()

    def _compose_scene_index(self, source_path: Path) -> SceneRegionIndex | None:
        """Fold discovered person instances into the base category index."""
        base = self._scene_index_base
        if base is None:
            return None
        if (
            self._people_instances
            and self._people_instances_source == source_path
        ):
            return base.with_person_instances(
                [(inst.mask, inst.seed_norm) for inst in self._people_instances]
            )
        return base

    # -- AI Background (blur / replace) ------------------------------------
    def background_render_spec(self) -> dict | None:
        """Resolved background compositing spec for the render backend, or None
        when the tool is off or the BiRefNet matte isn't ready yet. Kicks the
        matte computation on demand so simply turning the tool on triggers it."""
        mode = getattr(self._recipe, "background_mode", "off")
        if mode not in self._BACKGROUND_MATTE_MODES:
            return None
        matte = self._background_matte_path
        if not matte or not Path(matte).is_file():
            self._ensure_background_matte()
            return None
        return {
            "mode": mode,
            "amount": float(getattr(self._recipe, "background_amount", 60.0)),
            "color": str(getattr(self._recipe, "background_color", "#000000")),
            "matte_path": matte,
        }

    def _ensure_background_matte(self) -> None:
        """Compute the foreground matte (BiRefNet) that separates the subject
        from the blur/replace. Resolved from the subject-mask cache, so a reload
        is a cache hit."""
        if self._source_path is None or self._background_matte_task is not None:
            return
        if self._background_matte_path and Path(self._background_matte_path).is_file():
            return
        self.subject_warm_requested.emit("model")
        task = SubjectMaskTask(self._source_path, "subject")
        task.signals.finished.connect(
            self._handle_background_matte_finished, Qt.ConnectionType.QueuedConnection
        )
        task.signals.failed.connect(
            self._handle_background_matte_failed, Qt.ConnectionType.QueuedConnection
        )
        self._background_matte_task = task
        self._set_status("Separating subject from background...")
        self._semantic_mask_pool.start(task)

    def _handle_background_matte_finished(
        self, request: str, source_path: str, result: object
    ) -> None:
        self._background_matte_task = None
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            return
        matte = None
        if isinstance(result, SubjectMaskResult):
            matte = result.mask_paths.get("subject")
        if not matte or not Path(matte).is_file():
            self._handle_background_matte_failed(request, source_path, "no matte produced")
            return
        self._background_matte_path = str(matte)
        self._set_status("Background ready")
        # Version bumps on this emit, so the preview re-renders with the matte.
        self.recipe_changed.emit(self._recipe)

    def _handle_background_matte_failed(
        self, request: str, source_path: str, message: str
    ) -> None:
        self._background_matte_task = None
        if self._source_path is not None and Path(source_path) == self._source_path.resolve():
            self._set_status(f"Background separation failed: {message}")

    def _update_recipe_field(self, key: str, value: object) -> None:
        data = asdict(self._recipe)
        data[key] = value
        self._recipe = EditRecipe.from_dict(data)

    _BACKGROUND_MATTE_MODES = ("blur", "color", "remove")

    def _apply_background_settings(self, *, mode: str, amount: float) -> None:
        data = asdict(self._recipe)
        data["background_mode"] = mode
        data["background_amount"] = float(amount)
        self._recipe = EditRecipe.from_dict(data)
        self._sync_background_controls()
        if mode in self._BACKGROUND_MATTE_MODES:
            self._ensure_background_matte()
        self.recipe_changed.emit(self._recipe)

    def _set_background_blur(self, value: int) -> None:
        # Dragging blur means "blur", so a background removal, if on, yields.
        button = getattr(self, "background_remove_button", None)
        if button is not None and button.isChecked():
            with QSignalBlocker(button):
                button.setChecked(False)
        self._apply_background_settings(
            mode="blur" if value > 0 else "off", amount=float(value)
        )

    def _toggle_background_remove(self, checked: bool) -> None:
        amount = float(getattr(self._recipe, "background_amount", 0.0))
        if checked:
            self._apply_background_settings(mode="remove", amount=amount)
        else:
            self._apply_background_settings(
                mode="blur" if amount > 0 else "off", amount=amount
            )

    def _sync_background_controls(self) -> None:
        slider = getattr(self, "background_blur_slider", None)
        if slider is None:
            return
        mode = getattr(self._recipe, "background_mode", "off")
        amount = int(getattr(self._recipe, "background_amount", 0.0))
        with QSignalBlocker(slider):
            slider.setValue(amount)
        self.background_blur_value.setText(str(amount))
        button = getattr(self, "background_remove_button", None)
        if button is not None:
            with QSignalBlocker(button):
                button.setChecked(mode == "remove")

    # -- AI Lens Blur (depth-aware depth-of-field) -------------------------
    def lensblur_render_spec(self) -> dict | None:
        """Resolved lens-blur spec for the render backend, or None when off or
        the depth map isn't ready. Kicks the depth estimate on demand."""
        amount = float(getattr(self._recipe, "lensblur_amount", 0.0))
        if amount <= 0.0:
            return None
        depth = self._depth_map_path
        if not depth or not Path(depth).is_file():
            self._ensure_depth_map()
            return None
        return {
            "amount": amount,
            "focus": float(getattr(self._recipe, "lensblur_focus", 0.7)),
            "depth_path": depth,
        }

    def _ensure_depth_map(self) -> None:
        if self._source_path is None or self._depth_map_task is not None:
            return
        if self._depth_map_path and Path(self._depth_map_path).is_file():
            return
        try:
            QThreadPool.globalInstance().start(DepthWarmTask("model"))
        except Exception:
            pass
        task = DepthMapTask(self._source_path)
        task.signals.finished.connect(
            self._handle_depth_map_finished, Qt.ConnectionType.QueuedConnection
        )
        task.signals.failed.connect(
            self._handle_depth_map_failed, Qt.ConnectionType.QueuedConnection
        )
        self._depth_map_task = task
        self._set_status("Estimating depth...")
        QThreadPool.globalInstance().start(task)

    def _handle_depth_map_finished(
        self, request: str, source_path: str, result: object
    ) -> None:
        self._depth_map_task = None
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            return
        depth = result.depth_path if isinstance(result, DepthMapResult) else None
        if not depth or not Path(depth).is_file():
            self._handle_depth_map_failed(request, source_path, "no depth map")
            return
        self._depth_map_path = str(depth)
        self._set_status("Lens blur ready")
        self.recipe_changed.emit(self._recipe)

    def _handle_depth_map_failed(
        self, request: str, source_path: str, message: str
    ) -> None:
        self._depth_map_task = None
        if self._source_path is not None and Path(source_path) == self._source_path.resolve():
            self._set_status(f"Depth estimate failed: {message}")

    def _set_lensblur_amount(self, value: int) -> None:
        self._update_recipe_field("lensblur_amount", float(value))
        self._sync_lensblur_controls()
        if value > 0:
            self._ensure_depth_map()
        self.recipe_changed.emit(self._recipe)

    def _set_lensblur_focus(self, value: int) -> None:
        # Slider 0..100 (far..near) → focal depth 0..1.
        self._update_recipe_field("lensblur_focus", float(value) / 100.0)
        self._sync_lensblur_controls()
        self.recipe_changed.emit(self._recipe)

    def _sync_lensblur_controls(self) -> None:
        slider = getattr(self, "lensblur_amount_slider", None)
        if slider is None:
            return
        amount = int(getattr(self._recipe, "lensblur_amount", 0.0))
        focus = int(round(float(getattr(self._recipe, "lensblur_focus", 0.7)) * 100.0))
        with QSignalBlocker(slider):
            slider.setValue(amount)
        self.lensblur_amount_value.setText(str(amount))
        with QSignalBlocker(self.lensblur_focus_slider):
            self.lensblur_focus_slider.setValue(focus)

    def build_lens_blur_tool(self, parent: QWidget | None = None) -> QWidget:
        """The Lens Blur tool as a standalone popout panel (built once)."""
        existing = getattr(self, "_lens_blur_tool_panel", None)
        if existing is not None:
            return existing
        panel = QWidget(parent)
        panel.setObjectName("backgroundToolPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(12)
        hint = QLabel(
            "Depth-of-field blur that grows with distance. Set the strength, then "
            "slide Focus from far to near to place the sharp plane.",
            panel,
        )
        hint.setObjectName("editorHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        amount_row = QHBoxLayout()
        amount_row.setContentsMargins(0, 2, 0, 0)
        amount_row.setSpacing(8)
        amount_label = QLabel("Amount", panel)
        amount_label.setObjectName("editorControlLabel")
        amount_label.setFixedWidth(46)
        self.lensblur_amount_slider = QSlider(Qt.Orientation.Horizontal, panel)
        self.lensblur_amount_slider.setRange(0, 100)
        self.lensblur_amount_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lensblur_amount_slider.valueChanged.connect(self._set_lensblur_amount)
        self.lensblur_amount_value = QLabel("0", panel)
        self.lensblur_amount_value.setObjectName("editorControlLabel")
        self.lensblur_amount_value.setFixedWidth(28)
        self.lensblur_amount_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        amount_row.addWidget(amount_label)
        amount_row.addWidget(self.lensblur_amount_slider, 1)
        amount_row.addWidget(self.lensblur_amount_value)
        layout.addLayout(amount_row)

        focus_row = QHBoxLayout()
        focus_row.setContentsMargins(0, 2, 0, 0)
        focus_row.setSpacing(8)
        focus_label = QLabel("Focus", panel)
        focus_label.setObjectName("editorControlLabel")
        focus_label.setFixedWidth(46)
        self.lensblur_focus_slider = QSlider(Qt.Orientation.Horizontal, panel)
        self.lensblur_focus_slider.setRange(0, 100)
        self.lensblur_focus_slider.setValue(70)
        self.lensblur_focus_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.lensblur_focus_slider.valueChanged.connect(self._set_lensblur_focus)
        focus_hint = QLabel("far → near", panel)
        focus_hint.setObjectName("editorControlLabel")
        focus_hint.setFixedWidth(56)
        focus_hint.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        focus_row.addWidget(focus_label)
        focus_row.addWidget(self.lensblur_focus_slider, 1)
        focus_row.addWidget(focus_hint)
        layout.addLayout(focus_row)
        layout.addStretch(1)

        self._lens_blur_tool_panel = panel
        self._sync_lensblur_controls()
        return panel

    def handle_overlay_scene_picked(self, category: str) -> None:
        """A detected region was clicked on the photo."""
        if SceneRegionIndex.is_person(category):
            index = self._scene_index
            seed = index.seed_for(category) if index is not None else None
            if seed is None:
                self._set_status("Couldn't pin that person — try clicking again")
                return
            # Route through the same SAM + BiRefNet-refine path a manual
            # click-to-select uses, so the person comes out matte-clean.
            self._request_prompt_warm()
            self._start_prompt_mask_task(
                [seed],
                refine=True,
                parent_id=self._pending_parent_id,
                combine=self._pending_combine,
            )
            return
        self.request_semantic_mask(category)

    # -- promptable click-to-select (SAM) ----------------------------------
    def _set_point_select_active(self, active: bool) -> None:
        """The 'Click to Select (AI)' button toggled. Entering opens the
        touch-up window as a session that builds ONE mask from repeated clicks;
        the user leaves it with OK (see ``_finish_mask_touchup``)."""
        active = bool(active)
        if active and not self._prompt_session_active:
            self._enter_prompt_select_session()
        elif not active and self._prompt_session_active:
            # Rare — the button was unchecked while a session was live; treat it
            # like OK so we never strand the user on the touch-up page.
            self._finish_mask_touchup(accepted=True)

    def _sync_point_select_button(self, checked: bool) -> None:
        button = getattr(self, "point_select_button", None)
        if button is not None and button.isChecked() != checked:
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)

    def _enter_prompt_select_session(self) -> None:
        """Route into the touch-up window to build one click-to-select mask."""
        if self._source_path is None:
            self._set_status("Select an image first")
            self._sync_point_select_button(False)
            return
        # A live touch-up or subject picker would fight for the canvas/clicks.
        if self._mask_touchup_mask_id is not None or self._prompt_session_active:
            self._finish_mask_touchup(accepted=True)
        self._clear_subject_choice()
        self._point_select_active = True
        self._prompt_session_active = True
        self._prompt_session_root_id = None
        self._prompt_session_label = None
        self._mask_touchup_mask_id = None
        self._mask_touchup_original_present = set()
        self._mask_touchup_original_values = {}
        self._sync_point_select_button(True)
        self._reset_touchup_controls()
        self.mask_touchup_subtitle.setText(
            "Click a person or object to start — each click adds to one selection"
        )
        self.mask_touchup_clear_button.setEnabled(False)
        self.mask_touchup_invert_button.setEnabled(False)
        self.mask_touchup_refine_button.setEnabled(False)
        self.touchup_overlay_check.setChecked(self.overlay_check.isChecked())
        self._request_prompt_warm()
        self._enter_touchup_view()
        self._set_status("Click a person or object — each click adds to one selection")
        self.mask_overlay_changed.emit()

    def _reset_touchup_controls(self) -> None:
        defaults = {
            "edgeDetectionRadius": 0.0,
            "edgeSmooth": 0,
            "edgeFeather": 0.0,
            "edgeContrast": 0,
            "edgeShift": 0,
        }
        for key, spin in self._mask_touchup_spins.items():
            with QSignalBlocker(spin):
                spin.setValue(defaults[key])
        with QSignalBlocker(self.mask_touchup_feather_slider):
            self.mask_touchup_feather_slider.setValue(self._feather_pixels_to_slider(0.0))
        with QSignalBlocker(self.mask_touchup_invert_button):
            self.mask_touchup_invert_button.setChecked(False)
        self.mask_touchup_clear_button.setText("Clear Selection")

    def _request_prompt_warm(self) -> None:
        try:
            QThreadPool.globalInstance().start(PromptMaskWarmTask("model"))
        except Exception:
            pass

    def handle_overlay_point_picked(self, x: float, y: float) -> None:
        """A click landed on the photo in click-to-select mode."""
        if not self._point_select_active or self._source_path is None:
            return
        if self._prompt_mask_task is not None or self._refine_active_task is not None:
            self._set_status("Still working — one moment")
            return
        source_size = self._mask_source_size()
        if not source_size or source_size[0] < 1 or source_size[1] < 1:
            self._set_status("Select an image first")
            return
        nx = max(0.0, min(1.0, float(x) / source_size[0]))
        ny = max(0.0, min(1.0, float(y) / source_size[1]))
        # People/animals get an automatic BiRefNet edge refine so a selection
        # comes out matte-clean the first time (SAM picks who, BiRefNet the edge).
        refine = self._click_is_on_person(nx, ny)
        if self._prompt_session_active:
            # Every click in the session adds to the same mask.
            parent_id: str | None = self._prompt_session_root_id
            combine = "add"
        else:
            parent_id, combine = self._pending_parent_id, self._pending_combine
        self._start_prompt_mask_task(
            [(nx, ny)], refine=refine, parent_id=parent_id, combine=combine
        )

    def _click_is_on_person(self, nx: float, ny: float) -> bool:
        """Whether the click landed in OneFormer's people/animals region."""
        result = self._semantic_mask_result
        if result is None or self._source_path is None:
            return False
        if result.source_path != self._source_path.resolve():
            return False
        for category in ("people", "animals"):
            path = result.mask_paths.get(category)
            if not path or not path.is_file():
                continue
            try:
                with Image.open(path) as image:
                    gray = image.convert("L")
                    width, height = gray.size
                    px = min(width - 1, max(0, int(nx * width)))
                    py = min(height - 1, max(0, int(ny * height)))
                    if gray.getpixel((px, py)) >= 96:
                        return True
            except Exception:
                continue
        return False

    def _start_prompt_mask_task(
        self,
        points_norm: list[tuple[float, float]],
        *,
        refine: bool = False,
        parent_id: str | None = None,
        combine: str = "add",
    ) -> None:
        if self._source_path is None or self._prompt_mask_task is not None:
            return
        self._prompt_mask_context = {
            "parent_id": parent_id,
            "combine": combine,
            "refine": bool(refine),
            "points": [(float(px), float(py)) for px, py in points_norm],
        }
        task = PromptMaskTask(self._source_path, points_norm, refine=refine)
        task.signals.progress.connect(
            self._handle_prompt_mask_progress, Qt.ConnectionType.QueuedConnection
        )
        task.signals.finished.connect(
            self._handle_prompt_mask_finished, Qt.ConnectionType.QueuedConnection
        )
        task.signals.failed.connect(
            self._handle_prompt_mask_failed, Qt.ConnectionType.QueuedConnection
        )
        self._prompt_mask_task = task
        self.semantic_mask_status.setText("Selecting...")
        self.semantic_mask_status.show()
        self._set_status("Selecting...")
        self._semantic_mask_pool.start(task)

    def _handle_prompt_mask_progress(self, request_id: str, message: str) -> None:
        if self._prompt_mask_task is None:
            return
        self.semantic_mask_status.setText(message)
        self.semantic_mask_status.show()
        self._set_status(message)

    def _handle_prompt_mask_finished(
        self, request_id: str, source_path: str, result: object
    ) -> None:
        self._prompt_mask_task = None
        context = self._prompt_mask_context or {}
        self._prompt_mask_context = None
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            return
        if not isinstance(result, PromptMaskResult) or not result.mask_path.is_file():
            self._handle_prompt_mask_failed(request_id, source_path, "Selection produced no mask")
            return
        parent_id = context.get("parent_id")
        combine = str(context.get("combine", "add"))
        refine = bool(context.get("refine"))
        points = context.get("points") or []
        session_mode = self._prompt_session_active
        # In a session the whole group shares one label (it's a single mask);
        # a bare click (no session) still names each selection distinctly.
        if session_mode and self._prompt_session_root_id is None:
            self._prompt_mask_counter += 1
            self._prompt_session_label = f"Selection {self._prompt_mask_counter}"
        if session_mode:
            category = self._prompt_session_label or f"Selection {self._prompt_mask_counter}"
        else:
            self._prompt_mask_counter += 1
            category = f"Selection {self._prompt_mask_counter}"
        try:
            mask_id = self._register_generated_bitmap_mask(
                category=category,
                mask_path=result.mask_path,
                source_size=result.source_size,
                model={
                    "id": result.model_id,
                    "version": result.model_version,
                    "weightsHash": result.weights_hash,
                    "refinementVersion": "sam2.1",
                },
                parent_id=parent_id,
                combine=combine,
                ui_style="subject-select",
            )
        except Exception as exc:
            self._handle_prompt_mask_failed(request_id, source_path, str(exc))
            return
        # Remember how this component was made so "Refine Edges" can re-run the
        # same SAM point prompt with BiRefNet applied.
        self._prompt_meta[mask_id] = {
            "points": [(float(px), float(py)) for px, py in points],
            "refined": refine,
        }
        self.semantic_mask_status.hide()
        if session_mode:
            if self._prompt_session_root_id is None:
                self._prompt_session_root_id = mask_id
                self._bind_touchup_to_mask(mask_id)
            else:
                # Keep the whole-group mask (the root) as the adjustment target.
                self._select_mask_in_list(self._prompt_session_root_id)
            self._update_prompt_session_subtitle()
            self._set_status("Added to your selection — click more, refine, or press OK")
        else:
            self._select_mask_in_list(mask_id)
            self._set_status(f"Created {category} — click another to select more")
            if self._point_select_active:
                self._show_mask_pane(self.MASK_PANE_CREATE)

    def _prompt_component_unrefined(self, mask: dict[str, Any]) -> bool:
        """A click-to-select component that can still be edge-refined."""
        meta = self._prompt_meta.get(str(mask.get("id")))
        return bool(meta and meta.get("points") and not meta.get("refined"))

    def _update_prompt_session_subtitle(self) -> None:
        root_id = self._prompt_session_root_id
        if root_id is None:
            return
        members = self._group_members(root_id)
        count = len(members)
        label = self._prompt_session_label or "Selection"
        unrefined = sum(1 for m in members if self._prompt_component_unrefined(m))
        noun = "click" if count == 1 else "clicks"
        text = f"{label} • {count} {noun}"
        if unrefined:
            text += " • edges not refined"
        self.mask_touchup_subtitle.setText(text)
        self.mask_touchup_clear_button.setEnabled(True)
        self.mask_touchup_invert_button.setEnabled(True)
        self.mask_touchup_refine_button.setEnabled(unrefined > 0)

    def _handle_prompt_mask_failed(
        self, request_id: str, source_path: str, message: str
    ) -> None:
        self._prompt_mask_task = None
        self._prompt_mask_context = None
        if self._source_path is not None and Path(source_path) == self._source_path.resolve():
            text = f"Selection failed: {message}"
            self.semantic_mask_status.setText(text)
            self.semantic_mask_status.show()
            self._set_status(text)

    # -- manual "Refine Edges" (touch-up session) --------------------------
    def _refine_touchup_session(self) -> None:
        """Refine every un-refined click-to-select component in this session's
        mask with BiRefNet (people/animals were already refined on creation)."""
        root_id = self._mask_touchup_mask_id
        if root_id is None:
            self._set_status("Nothing to refine yet")
            return
        if self._refine_active_task is not None or self._prompt_mask_task is not None:
            self._set_status("Still working — one moment")
            return
        self._refine_queue = [
            str(m.get("id"))
            for m in self._group_members(root_id)
            if self._prompt_component_unrefined(m)
        ]
        if not self._refine_queue:
            self._set_status("Edges already refined")
            return
        self.mask_touchup_refine_button.setEnabled(False)
        self._refine_next()

    def _refine_next(self) -> None:
        if not self._refine_queue:
            self._refine_active_task = None
            self.semantic_mask_status.hide()
            if self._prompt_session_active and self._prompt_session_root_id is not None:
                self._update_prompt_session_subtitle()
            else:
                self.mask_touchup_refine_button.setEnabled(True)
            self._set_status("Edges refined")
            self.mask_overlay_changed.emit()
            return
        mask_id = self._refine_queue.pop(0)
        mask = self._mask_by_id(mask_id)
        meta = self._prompt_meta.get(mask_id, {})
        points = [(float(p[0]), float(p[1])) for p in meta.get("points", [])]
        if mask is None or self._source_path is None or not points:
            self._refine_next()
            return
        task = PromptMaskTask(self._source_path, points, refine=True)
        task.signals.finished.connect(
            lambda rid, sp, res, mid=mask_id: self._handle_refine_finished(mid, sp, res),
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            lambda rid, sp, msg, mid=mask_id: self._handle_refine_failed(mid, msg),
            Qt.ConnectionType.QueuedConnection,
        )
        self._refine_active_task = task
        self.semantic_mask_status.setText("Refining edges...")
        self.semantic_mask_status.show()
        self._set_status("Refining edges...")
        self._semantic_mask_pool.start(task)

    def _handle_refine_finished(
        self, mask_id: str, source_path: str, result: object
    ) -> None:
        self._refine_active_task = None
        mask = self._mask_by_id(mask_id)
        if (
            mask is not None
            and isinstance(result, PromptMaskResult)
            and result.mask_path.is_file()
            and self._source_path is not None
            and Path(source_path) == self._source_path.resolve()
        ):
            self._update_bitmap_mask_asset(mask, result.mask_path)
            self._prompt_meta.setdefault(mask_id, {})["refined"] = True
        self._refine_next()

    def _handle_refine_failed(self, mask_id: str, message: str) -> None:
        self._refine_active_task = None
        self._set_status(f"Refine failed: {message}")
        self._refine_next()

    def _update_bitmap_mask_asset(self, mask: dict[str, Any], mask_path: Path) -> None:
        """Overwrite a bitmap mask's cached asset in place (keeps its id)."""
        if self._session is None or self._session_path is None:
            return
        cache_asset_id = str(mask.get("cacheAssetId") or mask.get("assetId") or "")
        space_id = str(mask.get("coordinateSpaceId") or "")
        if not cache_asset_id or not space_id:
            return
        copy_bitmap_asset(
            self._session_path,
            self._session,
            cache_asset_id,
            space_id,
            mask_path,
        )
        self.mask_overlay_changed.emit()

    def _semantic_mask_id(self, category: str) -> str | None:
        if self._session is None:
            return None
        for mask in self._session.get("masks", []):
            if (
                mask.get("type") == "subject-select"
                and not mask.get("parentId")
                and str(mask.get("semanticCategory") or "").casefold() == category.casefold()
            ):
                return str(mask.get("id"))
        return None

    def _handle_semantic_mask_progress(self, request: str, message: str) -> None:
        if self._semantic_mask_task is None:
            return
        self.semantic_mask_status.setText(message)
        self.semantic_mask_status.show()
        fallback = (
            "Analyzing scene..."
            if request == SEMANTIC_MASK_INVENTORY_REQUEST
            else f"Preparing {request.title()} mask..."
        )
        self._set_status(message or fallback)

    def _handle_semantic_mask_finished(
        self,
        request: str,
        source_path: str,
        result: object,
    ) -> None:
        self._semantic_mask_task = None
        request_context = self._semantic_mask_request_context
        self._semantic_mask_request_context = None
        if self._source_path is None or Path(source_path) != self._source_path.resolve():
            self.semantic_mask_status.hide()
            if self.editor_stack.currentIndex() == 1:
                QTimer.singleShot(0, self._ensure_semantic_inventory)
            return
        if not isinstance(result, SemanticMaskResult):
            self._handle_semantic_mask_failed(request, source_path, "Invalid mask result")
            return
        self._semantic_mask_result = result
        self._reset_scene_regions()
        self._populate_semantic_mask_buttons(result.detected_categories)
        if SUBJECT_MASK_SEMANTIC_CATEGORIES.intersection(result.detected_categories):
            self.subject_warm_requested.emit("model")
        if "people" in result.detected_categories:
            # Split the people region into individuals now, in the background,
            # so pointing at a person lights up just them with no on-hover stall.
            self._ensure_people_instances(result)
        refreshed_count = 0
        try:
            refreshed_count = self._refresh_existing_semantic_masks(result)
        except Exception as exc:
            self._set_status(f"Scene regions found; existing mask refresh failed: {exc}")
        # Fresh regions are now pickable on the photo.
        self.mask_overlay_changed.emit()
        if request == SEMANTIC_MASK_INVENTORY_REQUEST:
            count = len(result.detected_categories)
            cache_note = " from cache" if result.cache_hit else ""
            refresh_note = (
                f"; refreshed {refreshed_count} existing mask(s)"
                if refreshed_count
                else ""
            )
            self._set_status(
                f"Found {count} scene region(s){cache_note}{refresh_note}"
            )
            return
        try:
            parent_id, combine = request_context or (None, "add")
            mask_id = self._register_semantic_mask(
                request,
                result,
                parent_id=parent_id,
                combine=combine,
            )
        except Exception as exc:
            self._handle_semantic_mask_failed(request, source_path, str(exc))
            return
        self.semantic_mask_status.hide()
        self._select_mask_in_list(mask_id)
        cache_note = " from cache" if result.cache_hit else ""
        self._set_status(f"Created {request.title()} mask{cache_note}")

    def _handle_semantic_mask_failed(
        self,
        request: str,
        source_path: str,
        message: str,
    ) -> None:
        self._semantic_mask_task = None
        self._semantic_mask_request_context = None
        self._sync_semantic_mask_buttons()
        if self._source_path is not None and Path(source_path) == self._source_path.resolve():
            label = (
                "Scene analysis"
                if request == SEMANTIC_MASK_INVENTORY_REQUEST
                else f"{request.title()} mask"
            )
            text = f"{label} failed: {message}"
            self.semantic_mask_status.setText(text)
            self.semantic_mask_status.show()
            self._set_status(text)
        elif self.editor_stack.currentIndex() == 1:
            QTimer.singleShot(0, self._ensure_semantic_inventory)

    def _register_semantic_mask(
        self,
        category: str,
        result: SemanticMaskResult,
        *,
        parent_id: str | None = None,
        combine: str = "add",
    ) -> str:
        if self._source_path is None or result.source_path != self._source_path.resolve():
            raise SessionError("semantic mask belongs to a different image")
        mask_path = result.mask_paths.get(category)
        if mask_path is None or not mask_path.is_file():
            raise SessionError(f"cached {category} mask is missing")
        return self._register_generated_bitmap_mask(
            category=category,
            mask_path=mask_path,
            source_size=result.source_size,
            model={
                "id": result.model_id,
                "version": result.model_version,
                "weightsHash": result.weights_hash,
                "refinementVersion": result.refinement_version,
            },
            parent_id=parent_id,
            combine=combine,
            ui_style="semantic-category",
        )

    def _register_subject_mask(
        self,
        request: str,
        result: SubjectMaskResult,
        *,
        parent_id: str | None = None,
        combine: str = "add",
        mask_path: Path | None = None,
        category: str | None = None,
    ) -> str:
        if self._source_path is None or result.source_path != self._source_path.resolve():
            raise SessionError("subject mask belongs to a different image")
        resolved_mask_path = mask_path or result.mask_paths.get(request)
        if resolved_mask_path is None or not resolved_mask_path.is_file():
            raise SessionError(f"cached {request} mask is missing")
        return self._register_generated_bitmap_mask(
            category=category or request,
            mask_path=resolved_mask_path,
            source_size=result.source_size,
            model={
                "id": result.model_id,
                "version": result.model_version,
                "weightsHash": result.weights_hash,
                "refinementVersion": result.refinement_version,
                "runtimeDevice": result.runtime_device,
            },
            parent_id=parent_id,
            combine=combine,
            ui_style="subject-background",
        )

    def _register_generated_bitmap_mask(
        self,
        *,
        category: str,
        mask_path: Path,
        source_size: tuple[int, int],
        model: dict[str, str],
        parent_id: str | None,
        combine: str,
        ui_style: str,
    ) -> str:
        _path, session = self._ensure_session()
        existing_id = self._semantic_mask_id(category)
        if parent_id is None and existing_id:
            return existing_id
        space_id = self._selected_mask_space()
        for space in session.get("coordinateSpaces", []):
            if space.get("id") != space_id:
                continue
            if not space.get("sourceWidth") or not space.get("sourceHeight"):
                space["sourceWidth"], space["sourceHeight"] = source_size
            break
        mask_id = _next_id(mask_ids(session), "mask")
        cache_asset_id = f"{mask_id}-cache"
        if self._session_path is None:
            raise SessionError("no session path")
        copy_bitmap_asset(
            self._session_path,
            session,
            cache_asset_id,
            space_id,
            mask_path,
        )
        mask = {
            "id": mask_id,
            "type": "subject-select",
            "coordinateSpaceId": space_id,
            "model": dict(model),
            "cacheAssetId": cache_asset_id,
            "semanticCategory": category,
            "uiStyle": ui_style,
            "params": {
                "density": 100.0,
                "invert": False,
                "edgeDetectionRadius": 0.0,
                "edgeSmooth": 0,
                "edgeFeather": 0.0,
                "edgeContrast": 0,
                "edgeShift": 0,
            },
        }
        grouped = self._attach_mask_to_group(
            session,
            mask,
            parent_id,
            combine,
        )
        upsert_mask(session, mask)
        if grouped:
            action = "Subtracted" if combine == "subtract" else "Added"
            message = f"{action} {category.title()} submask"
        else:
            message = f"Created {category.title()} mask"
        self._write_session(session, message)
        self._select_mask_in_list(mask_id)
        return mask_id

    @staticmethod
    def _semantic_model_payload(result: SemanticMaskResult) -> dict[str, str]:
        return {
            "id": result.model_id,
            "version": result.model_version,
            "weightsHash": result.weights_hash,
            "refinementVersion": result.refinement_version,
        }

    def _refresh_existing_semantic_masks(
        self,
        result: SemanticMaskResult,
    ) -> int:
        if self._source_path is None or result.source_path != self._source_path.resolve():
            return 0
        _path, session = self._ensure_session()
        if self._session_path is None:
            return 0
        expected_model = self._semantic_model_payload(result)
        refreshed = 0
        for mask in session.get("masks", []):
            if mask.get("type") != "subject-select":
                continue
            category = str(mask.get("semanticCategory") or "").casefold()
            mask_path = result.mask_paths.get(category)
            if mask_path is None or not mask_path.is_file():
                continue
            current_model = mask.get("model") or {}
            if all(current_model.get(key) == value for key, value in expected_model.items()):
                continue
            cache_asset_id = str(mask.get("cacheAssetId") or "")
            space_id = str(mask.get("coordinateSpaceId") or "")
            if not cache_asset_id or not space_id:
                continue
            copy_bitmap_asset(
                self._session_path,
                session,
                cache_asset_id,
                space_id,
                mask_path,
            )
            mask["model"] = dict(expected_model)
            refreshed += 1
        if refreshed:
            self._write_session(session, f"Refreshed {refreshed} semantic mask(s)")
        return refreshed

    def add_submask(self, combine: str = "add") -> None:
        mask = self._selected_mask_dict()
        if mask is None:
            self._set_status("Select a mask first")
            return
        root = self._mask_root(mask)
        normalized = "subtract" if combine == "subtract" else "add"
        self._open_mask_create_pane(
            parent_id=str(root.get("id")),
            combine=normalized,
        )
        verb = "subtract from" if normalized == "subtract" else "add to"
        self._set_status(f"Choose a mask type to {verb} the selected mask")

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
                # A mask that just came into existence is the one you want to
                # work on, so creation drops you back on the Work pane where its
                # controls have just appeared.
                self._show_mask_pane(self.MASK_PANE_WORK)
                return

    def _begin_mask_edit(self, mask_id: str) -> None:
        mask = self._mask_by_id(mask_id)
        if mask is None:
            return
        self._active_mask_edit_id = mask_id
        self._pending_parent_id = None
        self._pending_combine = "add"
        self._select_mask_in_list(mask_id)
        self._show_mask_pane(self.MASK_PANE_WORK)
        self._brush_paint_mode = None
        self._set_mask_tool(None)
        if mask.get("type") == "bitmap" and mask.get("uiStyle") == "brush":
            self.arm_brush_mask("add")
            return
        self._set_status(f"Editing {_friendly_mask_label(mask)}")
        self.mask_overlay_changed.emit()

    def _handle_mask_selection_changed(self, *_args) -> None:
        selected_id = self._selected_mask_id()
        if (
            self._active_mask_edit_id is not None
            and selected_id != self._active_mask_edit_id
        ):
            self._active_mask_edit_id = None
            self._brush_paint_mode = None
            self._set_mask_tool(None)
        self._sync_mask_row_selection()
        self._sync_mask_controls(self._selected_mask_dict())
        self._sync_mask_pane_enabled()
        self.mask_overlay_changed.emit()

    def _sync_mask_row_selection(self) -> None:
        """Paint selection on the custom row instead of QListWidget's item.

        Native item-view styles may add a current-row stripe at the left edge.
        That stripe is drawn under custom item widgets and can cross their text
        or push the trailing action against the viewport edge.
        """
        current = self.masks_list.currentItem()
        for index in range(self.masks_list.count()):
            item = self.masks_list.item(index)
            row = self.masks_list.itemWidget(item)
            if row is None:
                continue
            selected = item is current
            if bool(row.property("selected")) == selected:
                continue
            row.setProperty("selected", selected)
            row.style().unpolish(row)
            row.style().polish(row)
            row.update()

    def _sync_mask_controls(self, mask: dict[str, Any] | None) -> None:
        is_shape = mask is not None and mask.get("type") in self.MASK_SHAPE_TYPES
        params = (mask or {}).get("params") or {}
        style = str((mask or {}).get("uiStyle") or "")
        is_brush = mask is not None and mask.get("type") == "bitmap" and style == "brush"
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
            low = int(params.get("low", 0))
            high = int(params.get("high", 255))
            self.range_low_spin.setValue(low)
            self.range_high_spin.setValue(high)
            self.luminance_range_slider.setValues(low, high)
            tolerance = int(params.get("tolerance", 45))
            self.range_tolerance_spin.setValue(tolerance)
            self.range_feather_spin.setValue(int(params.get("feather", 20 if is_luma_range else 35)))
            refine = params.get("refine")
            if refine is None:
                refine = round((tolerance - 5) / 1.5)
            self.color_refine_spin.setValue(max(0, min(100, int(refine))))
            self.brush_size_spin.setValue(int(params.get("brushSize", 25)))
            self.brush_feather_spin.setValue(int(params.get("brushFeather", 50)))
            self.brush_density_spin.setValue(int(params.get("density", 100)))
            self.brush_flow_spin.setValue(int(params.get("flow", 100)))
            for key, row in self._mask_rows.items():
                row.set_value(float(local_values.get(key) or 0.0))
                row.setEnabled(has_mask)
        finally:
            self._updating_mask_controls = False
        self.reset_mask_adjustments_button.setEnabled(has_mask)
        # Add/Subtract sit under the list (always visible), so they gate on a
        # selection rather than appearing and disappearing.
        for name in ("add_submask_button", "subtract_submask_button"):
            button = getattr(self, name, None)
            if button is not None:
                button.setEnabled(has_mask)
        # The whole detail stack appears only when a mask is selected — with no
        # mask the Work pane is just the list and the New mask prompt.
        if getattr(self, "_mask_detail", None) is not None:
            self._mask_detail.setVisible(has_mask)
        # A selected component exposes only controls that alter that component.
        # Semantic masks already contain their generated strength map and have
        # no mask-shape controls.
        self._mask_shape_section.setVisible(is_shape)
        self._mask_range_section.setVisible(is_range)
        self._mask_brush_section.setVisible(is_brush)
        self.luminance_range_controls.setVisible(is_luma_range)
        self.color_refine_row.setVisible(is_color_range)

    def _handle_mask_control_changed(self, *_args) -> None:
        if self._updating_mask_controls:
            return
        mask = self._selected_mask_dict()
        if mask is None or mask.get("type") not in self.MASK_SHAPE_TYPES:
            return
        params = mask.setdefault("params", {})
        params["feather"] = round(self.mask_feather_spin.value(), 1)
        self.mask_overlay_changed.emit()
        if self._mask_has_local_adjustments(mask):
            # Geometry affects where local adjustments land — recomposite live.
            self.recipe_changed.emit(self._recipe)
        self._mask_commit_timer.start()

    @staticmethod
    def _color_range_values(refine: int) -> tuple[int, int]:
        normalized = max(0, min(100, int(refine)))
        return (
            max(1, min(255, round(5 + normalized * 1.5))),
            max(0, min(255, round(4 + normalized * 0.4))),
        )

    def _handle_luminance_range_changed(self, low: int, high: int) -> None:
        if self._updating_mask_controls:
            return
        with QSignalBlocker(self.range_low_spin), QSignalBlocker(self.range_high_spin):
            self.range_low_spin.setValue(low)
            self.range_high_spin.setValue(high)
        self._handle_range_control_changed()

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
            if self.sender() in (self.range_low_spin, self.range_high_spin):
                low = self.range_low_spin.value()
                high = self.range_high_spin.value()
            else:
                low, high = self.luminance_range_slider.values()
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
            with QSignalBlocker(self.luminance_range_slider):
                self.luminance_range_slider.setValues(low, high)
            params["low"] = low
            params["high"] = high
        else:
            if self.sender() is self.range_tolerance_spin:
                tolerance = self.range_tolerance_spin.value()
                refine = max(0, min(100, round((tolerance - 5) / 1.5)))
                with QSignalBlocker(self.color_refine_spin):
                    self.color_refine_spin.setValue(refine)
                feather = self._color_range_values(refine)[1]
            else:
                refine = self.color_refine_spin.value()
                tolerance, feather = self._color_range_values(refine)
                with QSignalBlocker(self.range_tolerance_spin):
                    self.range_tolerance_spin.setValue(tolerance)
            params["refine"] = refine
            params["tolerance"] = tolerance
            params["feather"] = feather
        self._pending_range_mask_id = str(mask.get("id"))
        self._range_update_timer.start()

    def _handle_brush_control_changed(self, *_args) -> None:
        if self._updating_mask_controls:
            return
        mask = self._selected_mask_dict()
        if (
            mask is None
            or mask.get("type") != "bitmap"
            or mask.get("uiStyle") != "brush"
        ):
            return
        params = mask.setdefault("params", {})
        params.update(
            {
                "brushSize": self.brush_size_spin.value(),
                "brushFeather": self.brush_feather_spin.value(),
                "density": self.brush_density_spin.value(),
                "flow": self.brush_flow_spin.value(),
            }
        )
        self.mask_overlay_changed.emit()
        if self._mask_has_local_adjustments(mask):
            self.recipe_changed.emit(self._recipe)
        self._mask_commit_timer.start()

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
        if self._overlay_auto_toggle and not self._overlay_suppressed_for_drag:
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
        the components. Cached bitmap and subject selections participate in
        the same live compositing path as geometric masks."""
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
        self.delete_mask(self._selected_mask_id())

    def delete_mask(self, mask_id: str | None) -> None:
        """Delete ``mask_id``; deleting a root takes its children with it."""
        if not mask_id:
            self._set_status("Select a mask first")
            return
        if self._mask_touchup_mask_id == mask_id:
            self._finish_mask_touchup(accepted=False)
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
        grouped = self._attach_mask_to_group(
            session,
            mask,
            self._pending_parent_id,
            self._pending_combine,
        )
        upsert_mask(session, mask)
        if grouped:
            message = (
                "Subtracted bitmap submask"
                if self._pending_combine == "subtract"
                else "Added bitmap submask"
            )
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
                    params={
                        "brushSize": self.brush_size_spin.value(),
                        "brushFeather": self.brush_feather_spin.value(),
                        "density": self.brush_density_spin.value(),
                        "flow": self.brush_flow_spin.value(),
                    },
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
            low, high = self.luminance_range_slider.values()
            feather = 20
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
                target_params = (target or {}).get("params") or {}
                refine = int(
                    target_params.get("refine", self.color_refine_spin.value())
                )
                tolerance, feather = self._color_range_values(refine)
                base = Image.new("L", rgb.size, 255)
                rendered = refine_color_range(
                    rgb, base, sample, tolerance=tolerance, feather=feather, invert=False
                )
            if target is not None:
                params = target.setdefault("params", {})
                params.update(
                    {
                        "sample": list(sample),
                        "x": sample_xy[0],
                        "y": sample_xy[1],
                        "refine": refine,
                        "tolerance": tolerance,
                        "feather": feather,
                    }
                )
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
                        "refine": refine,
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
        logger = perf_logger()
        commit_start = time.perf_counter() if logger.enabled else 0.0
        try:
            asset_path = self._bitmap_asset_path(mask)
            if asset_path is None:
                raise SessionError("bitmap asset missing")
            write_start = time.perf_counter() if logger.enabled else 0.0
            image.save(str(asset_path))
            if logger.enabled:
                asset_bytes = 0
                try:
                    asset_bytes = asset_path.stat().st_size
                except OSError:
                    pass
                logger.duration(
                    "brush.commit.asset_write",
                    (time.perf_counter() - write_start) * 1000.0,
                    width=image.width(),
                    height=image.height(),
                    asset_bytes=asset_bytes,
                )
            params = mask.setdefault("params", {})
            params["assetRevision"] = int(params.get("assetRevision", 0)) + 1
            self._set_status("Brush mask updated")
            notify_start = time.perf_counter() if logger.enabled else 0.0
            self.mask_overlay_changed.emit()
            if logger.enabled:
                logger.duration(
                    "brush.commit.overlay_notify",
                    (time.perf_counter() - notify_start) * 1000.0,
                )
            has_local_adjustments = self._mask_has_local_adjustments(mask)
            if has_local_adjustments:
                render_start = time.perf_counter() if logger.enabled else 0.0
                self.recipe_changed.emit(self._recipe)
                if logger.enabled:
                    logger.duration(
                        "brush.commit.render_notify",
                        (time.perf_counter() - render_start) * 1000.0,
                    )
            if logger.enabled:
                logger.duration(
                    "brush.commit.total",
                    (time.perf_counter() - commit_start) * 1000.0,
                    mask_id=str(mask.get("id") or ""),
                    asset_revision=params["assetRevision"],
                    has_local_adjustments=has_local_adjustments,
                )
        except Exception as exc:
            if logger.enabled:
                logger.duration(
                    "brush.commit.failed",
                    (time.perf_counter() - commit_start) * 1000.0,
                    error=str(exc),
                )
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
                mask = {
                    "id": mask_id,
                    "type": "subject-select",
                    "coordinateSpaceId": space_id,
                    "model": {
                        "id": "manual-cache",
                        "version": "1",
                        "weightsHash": "manual-cache",
                    },
                    "cacheAssetId": f"{mask_id}-cache",
                }
            else:
                copy_bitmap_asset(self._session_path, session, mask_id, space_id, Path(file_path))
                mask = {
                    "id": mask_id,
                    "type": "bitmap",
                    "assetId": mask_id,
                    "uiStyle": "brush",
                }
            grouped = self._attach_mask_to_group(
                session,
                mask,
                self._pending_parent_id,
                self._pending_combine,
            )
            upsert_mask(session, mask)
            if grouped:
                action = "Subtracted" if self._pending_combine == "subtract" else "Added"
                message = f"{action} imported submask"
            else:
                message = f"Saved mask {mask_id}"
            self._write_session(session, message)
            self._select_mask_in_list(mask_id)
        except Exception as exc:
            self._set_status(f"Bitmap mask failed: {exc}")

    def _load_recipe_for_path(self, path: Path) -> EditRecipe:
        migrate_bundle(path)
        session_path = resolve_session_for_read(path)
        if not session_path.exists():
            return EditRecipe()
        try:
            session = load_session(session_path)
            validate_session(session, session_path=session_path)
            recipe = recipe_from_session(session)
        except Exception as exc:
            self._set_status(f"Saved edits could not be loaded: {exc}")
            return EditRecipe()
        return recipe

    def _save_recipe_to_session(self, path: Path, recipe: EditRecipe) -> Path:
        migrate_bundle(path)
        session_path = resolve_session_for_read(path)
        if session_path.exists():
            session = load_session(session_path)
        else:
            ensure_edit_root(path.parent)
            session_path, session = new_session(path, session_path)
        gui_op_types = {op_type for op_type, _param_key in SESSION_OPS.values()} | {CURVE_OP_TYPE}
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
        if op.get("type") == CURVE_OP_TYPE and params.get("points"):
            channel = str(params.get("channel") or "rgb")
            if channel in CURVE_RECIPE_KEYS:
                values[CURVE_RECIPE_KEYS[channel]] = params["points"]
            continue
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
        if recipe_key in CURVE_RECIPE_KEYS or recipe_key not in SESSION_OPS:
            continue
        if recipe_key in VIGNETTE_OPTION_KEYS:
            continue  # written below, and only alongside a non-zero amount
        if value in (0, 0.0, None):
            continue
        op_type, param_key = SESSION_OPS[recipe_key]
        grouped.setdefault(op_type, {})[param_key] = value

    # The shape controls have non-zero neutral values, so the zero test above
    # cannot decide whether they are worth persisting — the amount does.
    if "adjust.vignette" in grouped:
        for recipe_key in VIGNETTE_OPTION_KEYS:
            _op_type, param_key = SESSION_OPS[recipe_key]
            grouped["adjust.vignette"][param_key] = values[recipe_key]

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

    # Point curves are lists, so they get one op per channel rather than the
    # scalar param grouping above.
    for channel, recipe_key in CURVE_RECIPE_KEYS.items():
        points = values.get(recipe_key)
        if is_identity_curve(points):
            continue
        op_id = _next_id(used_ids, "gui-adjust")
        used_ids.add(op_id)
        ops.append(
            {
                "id": op_id,
                "type": CURVE_OP_TYPE,
                "enabled": True,
                "maskId": None,
                "params": {
                    "points": [[int(x), int(y)] for x, y in normalize_curve_points(points)],
                    "channel": channel,
                },
            }
        )
    return sorted(ops, key=lambda op: _operation_order(op["type"]))


def _operation_order(op_type: str) -> int:
    from photo_terminal.session import RENDERER_ORDER

    try:
        return RENDERER_ORDER.index(op_type)
    except ValueError:
        return len(RENDERER_ORDER)
