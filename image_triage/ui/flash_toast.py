"""A short-lived confirmation pill shown over a widget.

The status bar is no place for "it worked": a folder rescan or any later message
replaces it within moments, and the popout hides it entirely. This pill is its own
top-level tool window, so it shows above the popout (and above the native
PhotoCraft layer stacked inside it) without taking focus or eating clicks.
"""
from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget


class FlashToast(QLabel):
    DEFAULT_MSEC = 1600

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setObjectName("flashToast")
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # Fixed dark pill: readable over any photo and in either theme.
        self.setStyleSheet(
            "QLabel#flashToast { background-color: rgba(20, 24, 30, 235); color: #f3f6fa;"
            " border: 1px solid rgba(255, 255, 255, 40); border-radius: 14px;"
            " padding: 7px 16px; font-size: 13px; font-weight: 600; }"
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def flash(self, anchor: QWidget | None, text: str, msec: int = DEFAULT_MSEC) -> bool:
        """Show ``text`` near the bottom centre of ``anchor``. Returns whether it was shown."""
        if anchor is None or not anchor.isVisible() or not text:
            return False
        self.setText(text)
        self.adjustSize()
        width = min(self.width(), max(160, anchor.width() - 40))
        self.resize(width, self.height())
        margin = min(72, max(16, anchor.height() // 8))
        top_left = anchor.mapToGlobal(QPoint((anchor.width() - width) // 2, anchor.height() - self.height() - margin))
        self.move(top_left)
        self.show()
        self.raise_()
        self._timer.start(max(300, int(msec)))
        return True
