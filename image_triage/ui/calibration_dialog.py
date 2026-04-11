from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..imaging import load_image_for_display
from ..review_workflows import CalibrationPair


@dataclass(slots=True, frozen=True)
class CalibrationResponse:
    pair: CalibrationPair
    choice: str

    @property
    def preferred_path(self) -> str:
        if self.choice == "left":
            return self.pair.left_path
        if self.choice == "right":
            return self.pair.right_path
        return ""


class TasteCalibrationDialog(QDialog):
    def __init__(self, pairs: tuple[CalibrationPair, ...], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Taste Calibration Wizard")
        self.resize(1220, 860)
        self._pairs = list(pairs)
        self._index = 0
        self._responses: list[CalibrationResponse] = []

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(18, 18, 18, 18)
        root_layout.setSpacing(12)

        self.title_label = QLabel("Taste Calibration")
        self.title_label.setObjectName("sectionLabel")
        root_layout.addWidget(self.title_label)

        self.progress_label = QLabel("")
        self.progress_label.setObjectName("secondaryText")
        root_layout.addWidget(self.progress_label)

        self.prompt_label = QLabel("")
        self.prompt_label.setWordWrap(True)
        root_layout.addWidget(self.prompt_label)

        compare_row = QHBoxLayout()
        compare_row.setSpacing(14)
        root_layout.addLayout(compare_row, 1)

        self.left_panel = self._build_option_panel("Option A", choose_text="Choose Left")
        self.right_panel = self._build_option_panel("Option B", choose_text="Choose Right")
        self.left_panel["button"].clicked.connect(lambda _checked=False: self._record_choice("left"))
        self.right_panel["button"].clicked.connect(lambda _checked=False: self._record_choice("right"))
        compare_row.addWidget(self.left_panel["container"], 1)
        compare_row.addWidget(self.right_panel["container"], 1)

        footer = QHBoxLayout()
        footer.setSpacing(10)
        root_layout.addLayout(footer)

        self.help_label = QLabel("Left/A chooses the left frame. Right/D chooses the right frame. N skips. Esc cancels.")
        self.help_label.setObjectName("secondaryText")
        self.help_label.setWordWrap(True)
        footer.addWidget(self.help_label, 1)

        self.skip_button = QPushButton("Skip")
        self.skip_button.clicked.connect(lambda _checked=False: self._record_choice("skip"))
        footer.addWidget(self.skip_button)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)

        self._load_current_pair()

    def responses(self) -> tuple[CalibrationResponse, ...]:
        return tuple(self._responses)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_A):
            self._record_choice("left")
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_D):
            self._record_choice("right")
            event.accept()
            return
        if key in (Qt.Key.Key_N, Qt.Key.Key_Space):
            self._record_choice("skip")
            event.accept()
            return
        super().keyPressEvent(event)

    def _build_option_panel(self, default_title: str, *, choose_text: str) -> dict[str, QWidget]:
        container = QWidget(self)
        layout = QVBoxLayout(container)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        title_label = QLabel(default_title)
        title_label.setObjectName("sectionLabel")
        layout.addWidget(title_label)

        image_label = QLabel("Loading...", alignment=Qt.AlignmentFlag.AlignCenter)
        image_label.setMinimumSize(440, 520)
        image_label.setStyleSheet("background-color: rgba(20, 24, 32, 0.85); border-radius: 12px;")
        image_label.setWordWrap(True)
        layout.addWidget(image_label, 1)

        path_label = QLabel("")
        path_label.setWordWrap(True)
        path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(path_label)

        choose_button = QPushButton(choose_text)
        layout.addWidget(choose_button)

        return {
            "container": container,
            "title": title_label,
            "image": image_label,
            "path": path_label,
            "button": choose_button,
        }

    def _load_current_pair(self) -> None:
        if not self._pairs:
            self.prompt_label.setText("No calibration comparisons are available for this folder yet.")
            self.progress_label.setText("0 comparisons ready")
            self.skip_button.setEnabled(False)
            self.left_panel["button"].setEnabled(False)
            self.right_panel["button"].setEnabled(False)
            return
        if self._index >= len(self._pairs):
            self.accept()
            return

        pair = self._pairs[self._index]
        self.progress_label.setText(f"Comparison {self._index + 1} of {len(self._pairs)}")
        self.prompt_label.setText(pair.prompt)
        self.left_panel["title"].setText(pair.left_label or "Option A")
        self.right_panel["title"].setText(pair.right_label or "Option B")
        self.left_panel["path"].setText(f"{Path(pair.left_path).name}\n{pair.left_path}")
        self.right_panel["path"].setText(f"{Path(pair.right_path).name}\n{pair.right_path}")
        self._set_image(self.left_panel["image"], pair.left_path)
        self._set_image(self.right_panel["image"], pair.right_path)

    def _set_image(self, label: QLabel, path: str) -> None:
        image, error = load_image_for_display(path, QSize(900, 700), prefer_embedded=True)
        if image.isNull():
            label.setPixmap(QPixmap())
            label.setText(error or Path(path).name)
            return
        pixmap = QPixmap.fromImage(image)
        target = label.size()
        if target.width() > 0 and target.height() > 0:
            pixmap = pixmap.scaled(target, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        label.setPixmap(pixmap)
        label.setText("")

    def _record_choice(self, choice: str) -> None:
        if not (0 <= self._index < len(self._pairs)):
            return
        self._responses.append(CalibrationResponse(pair=self._pairs[self._index], choice=choice))
        self._index += 1
        self._load_current_pair()
