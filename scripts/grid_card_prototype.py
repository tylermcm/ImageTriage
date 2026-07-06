"""Standalone prototype for one main-viewport grid card.

This is intentionally outside the live grid. It lets the card painter be tuned
with realistic sizes and states before the renderer is wired into
ThumbnailGridView.
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QBrush, QImageReader, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from image_triage.ui.grid_card_renderer import (
    COMPACT_COLUMN_THRESHOLD,
    GridCardData,
    paint_grid_card,
    render_grid_card_pixmap,
)


SIZE_PRESETS: dict[str, QSize] = {
    "11:8 review card - 560 x 407": QSize(560, 407),
    "11:8 compact - 480 x 349": QSize(480, 349),
    "11:8 large - 720 x 524": QSize(720, 524),
    "11:8 tuning - 900 x 655": QSize(900, 655),
    "2:3 portrait - 400 x 600": QSize(400, 600),
    "legacy wide - 560 x 330": QSize(560, 330),
}

# Grid preview layout constants (mirror ThumbnailGridView spacing).
GRID_MARGIN = 12
GRID_SPACING = 12
# height / width of the 11:8 review card.
CARD_ASPECT = 407 / 560


class CardCanvas(QWidget):
    metrics_changed = Signal(str)

    def __init__(self, source_pixmap: QPixmap | None, *, using_dummy: bool = False) -> None:
        super().__init__()
        self._source_pixmap = source_pixmap
        self._using_dummy = using_dummy
        self._card_size = QSize(560, 407)
        # 0 = single centered card at the preset size; >= 1 = grid preview
        # that reflows/scales the cards to fill the canvas width.
        self._columns = 0
        self._force_compact = False
        self._selected = True
        self._ai_visible = True
        self._duplicate_visible = True
        self._winner = True
        self._reject = False
        self._favorite = False
        self._bright = False
        self._dark = False
        self._last_metrics = ""
        self.setMouseTracking(True)
        self.setMinimumSize(640, 440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_card_size(self, size: QSize) -> None:
        self._card_size = QSize(size)
        self.updateGeometry()
        self.update()

    def set_layout_mode(self, columns: int, force_compact: bool) -> None:
        self._columns = max(0, columns)
        self._force_compact = force_compact
        self.update()

    def _is_compact(self, columns: int) -> bool:
        return self._force_compact or columns > COMPACT_COLUMN_THRESHOLD

    def set_state(
        self,
        *,
        selected: bool,
        ai_visible: bool,
        duplicate_visible: bool,
        winner: bool,
        reject: bool,
        favorite: bool,
        bright: bool,
        dark: bool,
    ) -> None:
        self._selected = selected
        self._ai_visible = ai_visible
        self._duplicate_visible = duplicate_visible
        self._winner = winner
        self._reject = reject
        self._favorite = favorite
        self._bright = bright
        self._dark = dark
        self.update()

    def sizeHint(self) -> QSize:
        return self._card_size + QSize(110, 110)

    def render_card(self) -> QPixmap:
        return render_grid_card_pixmap(
            self._card_size,
            self._pixmap_for_state(),
            self._data_for_state(),
            compact=self._force_compact,
        )

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(6, 7, 9))

        if self._columns <= 0:
            self._paint_single_card(painter)
        else:
            self._paint_grid(painter)
        painter.end()

    def _paint_single_card(self, painter: QPainter) -> None:
        compact = self._force_compact
        rect = QRect(QPoint(0, 0), self._card_size)
        rect.moveCenter(self.rect().center())
        paint_grid_card(painter, rect, self._pixmap_for_state(), self._data_for_state(), compact=compact)
        self._emit_metrics(
            f"Single card · {rect.width()} x {rect.height()} px · {'compact' if compact else 'full'} UI"
        )

    def _paint_grid(self, painter: QPainter) -> None:
        columns = self._columns
        compact = self._is_compact(columns)
        inner = max(120, self.width() - GRID_MARGIN * 2)
        card_w = max(90, (inner - (columns - 1) * GRID_SPACING) // columns)
        card_h = max(66, round(card_w * CARD_ASPECT))
        rows = max(1, math.ceil((self.height() - GRID_MARGIN) / (card_h + GRID_SPACING)))
        total = rows * columns

        pixmap = self._pixmap_for_state()
        index = 0
        for row in range(rows):
            for column in range(columns):
                rect = QRect(
                    GRID_MARGIN + column * (card_w + GRID_SPACING),
                    GRID_MARGIN + row * (card_h + GRID_SPACING),
                    card_w,
                    card_h,
                )
                data = self._data_for_state(
                    position_text=f"{index + 1} / {total}",
                    selected=self._selected and index == 0,
                )
                paint_grid_card(painter, rect, pixmap, data, compact=compact)
                index += 1

        self._emit_metrics(
            f"{columns} columns · card {card_w} x {card_h} px · {'compact' if compact else 'full'} UI"
        )

    def _emit_metrics(self, text: str) -> None:
        if text != self._last_metrics:
            self._last_metrics = text
            self.metrics_changed.emit(text)

    def _data_for_state(self, *, position_text: str = "1 / 24", selected: bool | None = None) -> GridCardData:
        if self._reject:
            status_text = "Reject"
            status_kind = "reject"
        elif self._winner:
            status_text = "Keeper"
            status_kind = "keeper"
        else:
            status_text = "Review"
            status_kind = "review"

        return GridCardData(
            filename="DSC_7149.NEF",
            exif_text="1/250s  \u00b7  f/5  \u00b7  ISO 200  \u00b7  35mm",
            meta_text="54.4 MB  \u00b7  2025-08-16 11:15  \u00b7  Banff 8-25",
            duplicate_text="Near Duplicate \u00b7 2/3",
            ai_text="AI Pick \u00b7 99",
            position_text=position_text,
            status_text=status_text,
            status_kind=status_kind,
            duplicate_visible=self._duplicate_visible,
            ai_visible=self._ai_visible,
            selected=self._selected if selected is None else selected,
            favorite=self._favorite,
            rejected=self._reject,
        )

    _dummy_cache: dict[tuple[bool, bool], QPixmap] = {}

    def _pixmap_for_state(self) -> QPixmap:
        if self._source_pixmap is None or self._source_pixmap.isNull() or self._using_dummy:
            key = (self._bright, self._dark)
            base = self._dummy_cache.get(key)
            if base is None:
                base = make_dummy_landscape(QSize(1800, 1100), bright=self._bright, dark=self._dark)
                self._dummy_cache[key] = base
        else:
            base = self._source_pixmap

        if not self._bright and not self._dark:
            return base

        adjusted = QPixmap(base.size())
        adjusted.fill(Qt.GlobalColor.transparent)
        painter = QPainter(adjusted)
        painter.drawPixmap(0, 0, base)
        if self._bright:
            painter.fillRect(adjusted.rect(), QColor(255, 255, 255, 44))
        if self._dark:
            painter.fillRect(adjusted.rect(), QColor(0, 0, 0, 76))
        painter.end()
        return adjusted


class PrototypeWindow(QMainWindow):
    def __init__(self, source_pixmap: QPixmap | None, load_message: str, *, using_dummy: bool) -> None:
        super().__init__()
        self.setWindowTitle("Grid Card Prototype")
        self.resize(980, 620)

        self.canvas = CardCanvas(source_pixmap, using_dummy=using_dummy)

        self.layout_combo = QComboBox()
        self.layout_combo.addItem("Single card")
        self.layout_combo.addItem("Grid preview")

        self.size_combo = QComboBox()
        for label in SIZE_PRESETS:
            self.size_combo.addItem(label)

        self.columns_spin = QSpinBox()
        self.columns_spin.setRange(1, 8)
        self.columns_spin.setValue(4)
        self.columns_spin.setPrefix("Columns: ")
        self.columns_spin.setEnabled(False)

        self.compact_check = QCheckBox("Force compact UI")

        self.metrics_label = QLabel("")
        self.metrics_label.setObjectName("metricsLabel")
        self.canvas.metrics_changed.connect(self.metrics_label.setText)

        self.selected_check = QCheckBox("Selected")
        self.selected_check.setChecked(True)
        self.ai_check = QCheckBox("AI badge")
        self.ai_check.setChecked(True)
        self.duplicate_check = QCheckBox("Duplicate badge")
        self.duplicate_check.setChecked(True)
        self.winner_check = QCheckBox("Winner / keeper")
        self.winner_check.setChecked(True)
        self.reject_check = QCheckBox("Reject")
        self.favorite_check = QCheckBox("Heart active")
        self.bright_check = QCheckBox("Bright image")
        self.dark_check = QCheckBox("Dark image")

        save_button = QPushButton("Save PNG")
        copy_button = QPushButton("Copy PNG")
        save_button.clicked.connect(self._save_png)
        copy_button.clicked.connect(self._copy_png)

        self.status_label = QLabel(load_message)
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("statusLabel")

        controls = QFrame()
        controls.setObjectName("controls")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(14, 14, 14, 14)
        controls_layout.setSpacing(10)

        title = QLabel("Card States")
        title.setObjectName("panelTitle")
        controls_layout.addWidget(title)
        controls_layout.addWidget(self.layout_combo)
        controls_layout.addWidget(self.size_combo)
        controls_layout.addWidget(self.columns_spin)
        controls_layout.addWidget(self.compact_check)
        controls_layout.addWidget(self.metrics_label)
        controls_layout.addSpacing(4)
        for checkbox in (
            self.selected_check,
            self.ai_check,
            self.duplicate_check,
            self.winner_check,
            self.reject_check,
            self.favorite_check,
            self.bright_check,
            self.dark_check,
        ):
            controls_layout.addWidget(checkbox)
        controls_layout.addSpacing(8)

        button_row = QHBoxLayout()
        button_row.addWidget(save_button)
        button_row.addWidget(copy_button)
        controls_layout.addLayout(button_row)
        controls_layout.addStretch(1)
        controls_layout.addWidget(self.status_label)

        root = QWidget()
        layout = QGridLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.addWidget(self.canvas, 0, 0)
        layout.addWidget(controls, 0, 1)
        layout.setColumnStretch(0, 1)
        layout.setColumnMinimumWidth(1, 250)
        self.setCentralWidget(root)

        self.layout_combo.currentTextChanged.connect(self._apply_state)
        self.size_combo.currentTextChanged.connect(self._apply_state)
        self.columns_spin.valueChanged.connect(self._apply_state)
        self.compact_check.toggled.connect(self._apply_state)
        for checkbox in (
            self.selected_check,
            self.ai_check,
            self.duplicate_check,
            self.winner_check,
            self.reject_check,
            self.favorite_check,
            self.bright_check,
            self.dark_check,
        ):
            checkbox.toggled.connect(self._apply_state)

        self._apply_state()
        self._apply_style()

    def _apply_state(self) -> None:
        if self.sender() is self.reject_check and self.reject_check.isChecked():
            self.winner_check.setChecked(False)
        if self.sender() is self.winner_check and self.winner_check.isChecked():
            self.reject_check.setChecked(False)
        if self.sender() is self.bright_check and self.bright_check.isChecked():
            self.dark_check.setChecked(False)
        if self.sender() is self.dark_check and self.dark_check.isChecked():
            self.bright_check.setChecked(False)

        grid_mode = self.layout_combo.currentText() == "Grid preview"
        self.size_combo.setEnabled(not grid_mode)
        self.columns_spin.setEnabled(grid_mode)
        columns = self.columns_spin.value() if grid_mode else 0
        self.canvas.set_layout_mode(columns, self.compact_check.isChecked())

        size = SIZE_PRESETS[self.size_combo.currentText()]
        self.canvas.set_card_size(size)
        self.canvas.set_state(
            selected=self.selected_check.isChecked(),
            ai_visible=self.ai_check.isChecked(),
            duplicate_visible=self.duplicate_check.isChecked(),
            winner=self.winner_check.isChecked(),
            reject=self.reject_check.isChecked(),
            favorite=self.favorite_check.isChecked(),
            bright=self.bright_check.isChecked(),
            dark=self.dark_check.isChecked(),
        )

    def _save_png(self) -> None:
        default = str(ROOT / "grid-card-prototype.png")
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Save prototype card",
            default,
            "PNG Images (*.png)",
        )
        if not path:
            return
        if not path.lower().endswith(".png"):
            path += ".png"
        self._rendered_output().save(path, "PNG")
        self.status_label.setText(f"Saved {path}")

    def _copy_png(self) -> None:
        QApplication.clipboard().setPixmap(self._rendered_output())
        self.status_label.setText("Copied rendered card to the clipboard.")

    def _rendered_output(self) -> QPixmap:
        if self.layout_combo.currentText() == "Grid preview":
            return self.canvas.grab()
        return self.canvas.render_card()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                background: #07080a;
                color: #dbe4ef;
                font-family: Segoe UI;
                font-size: 12px;
            }
            QFrame#controls {
                background: #111316;
                border: 1px solid #282c31;
                border-radius: 8px;
            }
            QLabel#panelTitle {
                color: #ffffff;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#statusLabel {
                color: #8f9bad;
            }
            QLabel#metricsLabel {
                color: #7fb2ff;
            }
            QComboBox, QPushButton, QSpinBox {
                background: #1b1e23;
                color: #f4f7fb;
                border: 1px solid #2f3540;
                border-radius: 6px;
                padding: 7px 9px;
            }
            QComboBox:hover, QPushButton:hover, QSpinBox:hover {
                background: #252a31;
                border-color: #465263;
            }
            QComboBox:disabled, QSpinBox:disabled {
                color: #5c6674;
                border-color: #23272e;
            }
            QCheckBox {
                color: #cbd5e1;
                spacing: 8px;
                padding: 2px 0;
            }
            QCheckBox::indicator {
                width: 15px;
                height: 15px;
                border-radius: 4px;
                border: 1px solid #44505f;
                background: #11161c;
            }
            QCheckBox::indicator:checked {
                background: #3d7cff;
                border-color: #68a2ff;
            }
            """
        )


def load_source_pixmap(path: str | None) -> tuple[QPixmap | None, str, bool]:
    if not path:
        return None, "Using generated landscape sample. Pass --image to test a real file.", True

    image_path = Path(path)
    if not image_path.exists():
        return None, f"Image not found: {image_path}. Using generated sample.", True

    reader = QImageReader(str(image_path))
    reader.setAutoTransform(True)
    image = reader.read()
    if image.isNull():
        try:
            from image_triage.imaging import load_image_for_display

            image, error = load_image_for_display(
                str(image_path),
                QSize(1800, 1400),
                prefer_embedded=True,
            )
            if image.isNull():
                return None, f"Could not load image ({error or reader.errorString()}). Using generated sample.", True
        except Exception as exc:  # pragma: no cover - depends on optional RAW stack
            return None, f"Could not load image ({exc}). Using generated sample.", True

    pixmap = QPixmap.fromImage(image)
    return pixmap, f"Loaded {image_path}", False


def make_dummy_landscape(size: QSize, *, bright: bool = False, dark: bool = False) -> QPixmap:
    pixmap = QPixmap(size)
    pixmap.fill(QColor(10, 14, 17))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    sky = QLinearGradient(0, 0, 0, size.height())
    if bright:
        sky.setColorAt(0.0, QColor(160, 191, 219))
        sky.setColorAt(0.55, QColor(118, 144, 154))
        sky.setColorAt(1.0, QColor(22, 37, 40))
    elif dark:
        sky.setColorAt(0.0, QColor(35, 48, 58))
        sky.setColorAt(0.55, QColor(22, 33, 38))
        sky.setColorAt(1.0, QColor(5, 9, 10))
    else:
        sky.setColorAt(0.0, QColor(90, 120, 145))
        sky.setColorAt(0.55, QColor(52, 71, 78))
        sky.setColorAt(1.0, QColor(8, 15, 16))
    painter.fillRect(pixmap.rect(), QBrush(sky))

    w = size.width()
    h = size.height()
    for offset, alpha in ((0, 220), (round(w * 0.12), 190), (-round(w * 0.16), 205)):
        path = QPainterPath()
        path.moveTo(-round(w * 0.05) + offset, h)
        path.lineTo(round(w * 0.14) + offset, round(h * 0.42))
        path.lineTo(round(w * 0.25) + offset, round(h * 0.55))
        path.lineTo(round(w * 0.40) + offset, round(h * 0.34))
        path.lineTo(round(w * 0.56) + offset, round(h * 0.50))
        path.lineTo(round(w * 0.74) + offset, round(h * 0.31))
        path.lineTo(round(w * 1.06) + offset, h)
        path.closeSubpath()
        painter.fillPath(path, QColor(9, 18, 22, alpha))

    tree_pen = QPen(QColor(3, 20, 16, 185), max(2, w // 360))
    painter.setPen(tree_pen)
    for x in range(round(w * 0.08), round(w * 0.96), max(18, w // 48)):
        top = round(h * (0.48 + (x % 67) / 420))
        painter.drawLine(x, top, x, round(h * 0.76))
        painter.drawLine(x, top + 16, x - 11, top + 58)
        painter.drawLine(x, top + 16, x + 11, top + 58)

    lake = QRect(0, round(h * 0.66), w, round(h * 0.35))
    painter.fillRect(lake, QColor(16, 91, 95, 130))
    painter.setPen(QPen(QColor(132, 199, 202, 38), max(1, h // 360)))
    for y in range(lake.top() + 12, lake.bottom(), max(15, h // 48)):
        painter.drawLine(round(w * 0.12), y, round(w * 0.88), y + (y % 11) - 5)

    painter.end()
    return pixmap


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prototype one main-viewport grid card.")
    parser.add_argument("--image", help="Optional image path to draw inside the card.")
    parser.add_argument("--save", help="Render once to this PNG path and exit.")
    parser.add_argument("--width", type=int, default=560, help="PNG render width when --save is used.")
    parser.add_argument("--height", type=int, default=407, help="PNG render height when --save is used.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    app = QApplication(sys.argv[:1])
    source_pixmap, load_message, using_dummy = load_source_pixmap(args.image)

    if args.save:
        data = GridCardData(selected=True)
        source = source_pixmap if not using_dummy else make_dummy_landscape(QSize(1800, 1100))
        output = render_grid_card_pixmap(QSize(args.width, args.height), source, data)
        if not output.save(args.save, "PNG"):
            print(f"Failed to save {args.save}", file=sys.stderr)
            return 2
        print(f"Saved {args.save}")
        return 0

    window = PrototypeWindow(source_pixmap, load_message, using_dummy=using_dummy)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
