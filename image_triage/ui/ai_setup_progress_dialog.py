"""The Set Up AI progress view: a bar, the three steps (Downloading, Installing, Checking that it works) and the time left. Not modal: the person keeps working and
the status chip carries the same progress. When the run ends it turns into "AI is ready" or says what went wrong."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QConicalGradient, QPainter, QPen
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QProgressBar, QPushButton, QStackedLayout, QVBoxLayout, QWidget

from ..ai_setup_progress import STEPS

_RING = ("#ff9a5a", "#ff5e8a", "#b25cff", "#5a8cff", "#ff9a5a")
_GREEN = "#1f9d63"


class _StepIcon(QWidget):
    """Done: a green tick. Running: a ring that turns. To do: its number in a grey ring."""

    def __init__(self, number: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._number = number
        self._state = "todo"
        self._angle = 0.0
        self.setFixedSize(26, 26)
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(360.0)
        self._animation.setDuration(1100)
        self._animation.setLoopCount(-1)
        self._animation.valueChanged.connect(self._turn)

    def state(self) -> str:
        return self._state

    def set_state(self, state: str) -> None:
        self._state = state
        if state == "run":
            self._animation.start()
        else:
            self._animation.stop()
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(26, 26)

    def _turn(self, value: object) -> None:
        self._angle = float(value)  # type: ignore[arg-type]
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt override
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        if self._state == "done":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(_GREEN))
            painter.drawEllipse(rect)
            painter.setPen(QPen(QColor("white"), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.drawPolyline([_pt(rect, 0.27, 0.52), _pt(rect, 0.44, 0.69), _pt(rect, 0.74, 0.33)])
        elif self._state == "run":
            gradient = QConicalGradient(rect.center(), -self._angle)
            for index, color in enumerate(_RING):
                gradient.setColorAt(index / (len(_RING) - 1), QColor(color))
            painter.setPen(QPen(gradient, 3.0))
            painter.drawEllipse(rect)
        else:
            painter.setPen(QPen(self.palette().midlight().color(), 2.0))
            painter.drawEllipse(rect)
            painter.setPen(self.palette().mid().color())
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self._number))


def _pt(rect: QRectF, x: float, y: float):
    from PySide6.QtCore import QPointF

    return QPointF(rect.left() + rect.width() * x, rect.top() + rect.height() * y)


class AISetupProgressDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiSetupProgressDialog")
        self.setWindowTitle("Installing AI")
        self.setModal(False)
        self.setFixedSize(520, 365)
        self.setStyleSheet(
            """
            QDialog#aiSetupProgressDialog { background: palette(window); }
            QLabel#aiProgressTitle { font-size: 21px; font-weight: 700; }
            QLabel#aiProgressMuted { color: palette(mid); }
            QLabel#aiProgressStep { font-size: 14px; }
            QProgressBar { border: none; background: palette(midlight); border-radius: 4px; }
            QProgressBar::chunk { border-radius: 4px; background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ff9a5a, stop:0.35 #ff5e8a, stop:0.68 #b25cff, stop:1 #5a8cff); }
            """
        )
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        aurora = QFrame(self)
        aurora.setFixedHeight(3)
        aurora.setStyleSheet("background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #ff9a5a, stop:0.35 #ff5e8a, stop:0.68 #b25cff, stop:1 #5a8cff);")
        root.addWidget(aurora)
        self._stack = QStackedLayout()
        holder = QWidget(self)
        holder.setLayout(self._stack)
        root.addWidget(holder, 1)

        # ---- the running view
        run = QWidget()
        layout = QVBoxLayout(run)
        layout.setContentsMargins(26, 18, 26, 14)
        layout.setSpacing(10)
        self.title = QLabel("Installing AI", run)
        self.title.setObjectName("aiProgressTitle")
        layout.addWidget(self.title)
        self.note = QLabel("You can keep using Image Triage. Setup continues in the background.", run)
        self.note.setObjectName("aiProgressMuted")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.bar = QProgressBar(run)
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        layout.addWidget(self.bar)
        self._icons: list[_StepIcon] = []
        for index, label in enumerate(STEPS):
            row = QHBoxLayout()
            row.setSpacing(12)
            icon = _StepIcon(index + 1, run)
            text = QLabel(label, run)
            text.setObjectName("aiProgressStep")
            row.addWidget(icon)
            row.addWidget(text, 1)
            layout.addLayout(row)
            self._icons.append(icon)
        layout.addStretch(1)
        foot = QHBoxLayout()
        self.eta = QLabel("", run)
        self.eta.setObjectName("aiProgressMuted")
        self.cancel = QPushButton("Cancel", run)
        self.cancel.setObjectName("aiProgressCancel")
        self.cancel.clicked.connect(self.cancel_requested)
        foot.addWidget(self.eta, 1)
        foot.addWidget(self.cancel, 0)
        layout.addLayout(foot)
        self._stack.addWidget(run)

        # ---- the end view: ready, or what went wrong
        end = QWidget()
        end_layout = QVBoxLayout(end)
        end_layout.setContentsMargins(26, 24, 26, 18)
        end_layout.setSpacing(10)
        end_layout.addStretch(1)
        self.mark = QLabel("✓", end)
        self.mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mark.setFixedSize(54, 54)
        end_layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignHCenter)
        self.end_title = QLabel("AI is ready", end)
        self.end_title.setObjectName("aiProgressTitle")
        self.end_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        end_layout.addWidget(self.end_title)
        self.end_note = QLabel("", end)
        self.end_note.setObjectName("aiProgressMuted")
        self.end_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.end_note.setWordWrap(True)
        end_layout.addWidget(self.end_note)
        end_layout.addStretch(1)
        done_row = QHBoxLayout()
        done_row.addStretch(1)
        self.done_button = QPushButton("Done", end)
        self.done_button.setObjectName("editorPrimaryButton")
        self.done_button.clicked.connect(self.close)
        done_row.addWidget(self.done_button)
        end_layout.addLayout(done_row)
        self._stack.addWidget(end)
        self.show_progress(0, 0, "")

    # ------------------------------------------------------------------ states

    def show_progress(self, step: int, percent: int, eta: str, *, title: str = "Installing AI") -> None:
        self._stack.setCurrentIndex(0)
        self.setWindowTitle(title)
        self.title.setText(title)
        self.bar.setValue(max(0, min(100, percent)))
        self.eta.setText(eta)
        self.cancel.setEnabled(True)
        for index, icon in enumerate(self._icons):
            icon.set_state("done" if index < step else "run" if index == step else "todo")

    def show_stopping(self) -> None:
        self.cancel.setEnabled(False)
        self.eta.setText("Stopping...")

    def show_ready(self, note: str = "") -> None:
        self._stack.setCurrentIndex(1)
        self.setWindowTitle("AI is ready")
        self.mark.setStyleSheet(f"background: {_GREEN}; color: white; border-radius: 27px; font-size: 28px; font-weight: 700;")
        self.mark.setText("✓")
        self.end_title.setText("AI is ready")
        self.end_note.setText(note)
        for icon in self._icons:
            icon.set_state("done")

    def show_failed(self, message: str) -> None:
        self._stack.setCurrentIndex(1)
        self.setWindowTitle("AI setup did not finish")
        self.mark.setStyleSheet("background: #d1423f; color: white; border-radius: 27px; font-size: 28px; font-weight: 700;")
        self.mark.setText("!")
        self.end_title.setText("AI setup did not finish")
        self.end_note.setText(message)
        for icon in self._icons:
            if icon.state() == "run":
                icon.set_state("todo")

    def step_states(self) -> list[str]:
        return [icon.state() for icon in self._icons]

    def showing_end(self) -> bool:
        return self._stack.currentIndex() == 1
