"""The inspector's "Analyze folder" control, styled so it reads as an AI feature (docs/ai_button_mockup.html).

* AuroraButton: a button with a slowly turning conic-gradient border (the Dawn palette) and a sparkle.
* ProgressBlock: shown while an analysis runs; a glowing border, the stage text, a gradient bar and Cancel.
* AIAnalyzeCard: switches between the button, the progress block and a short hint (used when a filter is on,
  because analysis always covers the whole folder).

Motion follows the system's animation setting. With reduced motion the gradient border and sparkle stay and only the
movement stops, so the AI cue is never lost. Animations also pause when the window is not active.
"""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QConicalGradient, QFont, QGuiApplication, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget

from .theme import ThemePalette, default_theme

# Dawn palette from the mockup; the loop returns to the first colour so the border has no seam.
AURORA_COLORS = ("#ff9a5a", "#ff5e8a", "#b25cff", "#5a8cff")
IDLE_TURN_MS = 7000
HOVER_TURN_MS = 2500
RUNNING_TURN_MS = 3000


def animations_enabled() -> bool:
    """False when the user has turned system animations off (Windows) or set IMAGE_TRIAGE_REDUCE_MOTION=1."""
    if (os.environ.get("IMAGE_TRIAGE_REDUCE_MOTION", "") or "").strip() == "1":
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            enabled = ctypes.c_int(1)
            # SPI_GETCLIENTAREAANIMATION
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0):
                return bool(enabled.value)
        except Exception:
            return True
    return True


def aurora_gradient(center: QPointF, angle_degrees: float) -> QConicalGradient:
    gradient = QConicalGradient(center, angle_degrees)
    colors = [QColor(c) for c in AURORA_COLORS]
    stops = len(colors)
    for index, color in enumerate(colors):
        gradient.setColorAt(index / stops, color)
    gradient.setColorAt(1.0, colors[0])
    return gradient


def sparkle_path(center: QPointF, size: float) -> QPainterPath:
    """A four-point star with a small second star, as in the mockup."""
    def star(c: QPointF, r: float) -> QPainterPath:
        path = QPainterPath()
        inner = r * 0.32
        points = []
        for i in range(8):
            radius = r if i % 2 == 0 else inner
            angle = (i * 45 - 90) * 3.14159265 / 180.0
            from math import cos, sin

            points.append(QPointF(c.x() + radius * cos(angle), c.y() + radius * sin(angle)))
        path.moveTo(points[0])
        for point in points[1:]:
            path.lineTo(point)
        path.closeSubpath()
        return path

    path = star(center, size * 0.5)
    path.addPath(star(QPointF(center.x() + size * 0.55, center.y() + size * 0.42), size * 0.22))
    return path


class _Animated(QWidget):
    """Mixin-style base: owns a turning angle that runs only while visible, in an active window, with motion allowed."""

    def __init__(self, turn_ms: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._angle = 0.0
        self._turn_ms = turn_ms
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(360.0)
        self._animation.setLoopCount(-1)
        self._animation.setEasingCurve(QEasingCurve.Type.Linear)
        self._animation.setDuration(turn_ms)
        self._animation.valueChanged.connect(self._on_angle)
        self._motion_allowed = animations_enabled()
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._sync_animation)

    def _on_angle(self, value) -> None:
        self._angle = float(value)
        self.update()

    def set_turn_ms(self, turn_ms: int) -> None:
        if turn_ms != self._turn_ms:
            self._turn_ms = turn_ms
            self._animation.setDuration(turn_ms)

    def refresh_motion_setting(self) -> None:
        self._motion_allowed = animations_enabled()
        self._sync_animation()

    def is_animating(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    def _should_animate(self) -> bool:
        app = QGuiApplication.instance()
        active = app is None or app.applicationState() == Qt.ApplicationState.ApplicationActive
        # An offscreen test platform reports an inactive application, so treat only a real inactive state as paused.
        offscreen = QGuiApplication.platformName() == "offscreen"
        return self._motion_allowed and self.isVisible() and (active or offscreen)

    def _sync_animation(self, *_args) -> None:
        if self._should_animate():
            if self._animation.state() != QVariantAnimation.State.Running:
                self._animation.start()
        elif self._animation.state() == QVariantAnimation.State.Running:
            self._animation.pause()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._sync_animation()


class AuroraButton(QPushButton):
    """Raised button with a turning conic-gradient border and a sparkle."""

    def __init__(self, text: str, parent: QWidget | None = None, *, compact: bool = False) -> None:
        super().__init__(text, parent)
        self._compact = compact
        self._theme = default_theme()
        self._hover = False
        self._keyboard_focus = False
        self._angle = 0.0
        self._motion_allowed = animations_enabled()
        self._animation = QVariantAnimation(self)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(360.0)
        self._animation.setLoopCount(-1)
        self._animation.setDuration(IDLE_TURN_MS)
        self._animation.valueChanged.connect(self._on_angle)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setObjectName("inspectorAnalyzeButton")
        self.setMinimumHeight(26 if compact else 32)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        app = QGuiApplication.instance()
        if app is not None:
            app.applicationStateChanged.connect(self._sync_animation)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def refresh_motion_setting(self) -> None:
        self._motion_allowed = animations_enabled()
        self._sync_animation()

    def is_animating(self) -> bool:
        return self._animation.state() == QVariantAnimation.State.Running

    def sizeHint(self):  # type: ignore[override]
        metrics = self.fontMetrics()
        pad = 22 if self._compact else 30
        icon = 22 if self._compact else 26
        size = super().sizeHint()
        size.setWidth(metrics.horizontalAdvance(self.text()) + pad + icon)
        size.setHeight(max(size.height(), 26 if self._compact else 32))
        return size

    def _on_angle(self, value) -> None:
        self._angle = float(value)
        self.update()

    def _should_animate(self) -> bool:
        app = QGuiApplication.instance()
        active = app is None or app.applicationState() == Qt.ApplicationState.ApplicationActive
        offscreen = QGuiApplication.platformName() == "offscreen"
        return self._motion_allowed and self.isVisible() and (active or offscreen)

    def _sync_animation(self, *_args) -> None:
        if self._should_animate():
            self._animation.setDuration(HOVER_TURN_MS if self._hover else IDLE_TURN_MS)
            if self._animation.state() != QVariantAnimation.State.Running:
                self._animation.start()
        elif self._animation.state() == QVariantAnimation.State.Running:
            self._animation.pause()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._sync_animation()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._sync_animation()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hover = True
        self._sync_animation()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover = False
        self._sync_animation()
        self.update()
        super().leaveEvent(event)

    def focusInEvent(self, event) -> None:  # type: ignore[override]
        self._keyboard_focus = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason, Qt.FocusReason.ShortcutFocusReason)
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event) -> None:  # type: ignore[override]
        self._keyboard_focus = False
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        self._keyboard_focus = False
        super().mousePressEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = 7.0 if self._compact else 8.0
        fill = self._theme.raised_bg.qcolor()
        if self._hover or self.isDown():
            fill = self._theme.input_hover_bg.qcolor()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(rect, radius, radius)
        if self._hover:  # soft halo under the border
            halo = QColor(AURORA_COLORS[1])
            halo.setAlpha(60)
            painter.setPen(QPen(halo, 5.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, radius, radius)
        gradient = aurora_gradient(rect.center(), -self._angle)
        painter.setPen(QPen(gradient, 2.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        icon_size = 11.0 if self._compact else 13.0
        icon_center = QPointF(rect.left() + 12 + icon_size * 0.5, rect.center().y() - 0.5)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(AURORA_COLORS[1]))
        painter.drawPath(sparkle_path(icon_center, icon_size))
        font = QFont(self.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(self._theme.text_primary.qcolor())
        text_rect = rect.adjusted(12 + icon_size + 10, 0, -10, 0)
        painter.drawText(text_rect, int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), self.text())
        if self.hasFocus() and self._keyboard_focus:
            painter.setPen(QPen(self._theme.accent_hover.qcolor(), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), radius - 2, radius - 2)


class GradientBar(_Animated):
    """Thin progress bar; determinate when a total is known, otherwise a sweeping chunk."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(2000, parent)
        self._theme = default_theme()
        self._value = 0
        self._total = 0
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.update()

    def set_progress(self, value: int, total: int) -> None:
        self._value, self._total = max(0, int(value)), max(0, int(total))
        self.update()

    def fraction(self) -> float | None:
        if self._total <= 0:
            return None
        return min(1.0, self._value / self._total)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        track = QRectF(self.rect())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._theme.border.qcolor())
        painter.drawRoundedRect(track, 3, 3)
        fraction = self.fraction()
        if fraction is None:
            width = track.width() * 0.3
            offset = (self._angle / 360.0) * (track.width() + width) - width
            x0, x1 = max(0.0, offset), min(track.width(), offset + width)
            chunk = QRectF(x0, 0, max(0.0, x1 - x0), track.height())
        else:
            chunk = QRectF(0, 0, max(track.height(), track.width() * fraction), track.height())
        if chunk.width() <= 0:
            return
        gradient = QLinearGradient(chunk.left(), 0, chunk.right(), 0)
        colors = [QColor(c) for c in AURORA_COLORS]
        for index, color in enumerate(colors):
            gradient.setColorAt(index / (len(colors) - 1), color)
        painter.setBrush(gradient)
        painter.drawRoundedRect(chunk, 3, 3)


class ProgressBlock(_Animated):
    """Stage text, a gradient bar, time left and Cancel, inside a glowing border (mockup A2)."""

    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(RUNNING_TURN_MS, parent)
        self._theme = default_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        self.stage_label = QLabel("Starting")
        self.stage_label.setObjectName("inspectorHint")
        self.stage_label.setWordWrap(True)
        self.stage_label.setStyleSheet("background: transparent;")
        self.bar = GradientBar(self)
        self.detail_label = QLabel("")
        self.detail_label.setObjectName("secondaryText")
        self.detail_label.setWordWrap(True)
        self.detail_label.setStyleSheet("background: transparent;")
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.cancel_button.clicked.connect(lambda _checked=False: self.cancel_requested.emit())
        layout.addWidget(self.stage_label)
        layout.addWidget(self.bar)
        layout.addWidget(self.detail_label)
        row = QHBoxLayout()
        row.addWidget(self.cancel_button)
        row.addStretch(1)
        layout.addLayout(row)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.bar.apply_theme(theme)
        self.update()

    def refresh_motion_setting(self) -> None:
        super().refresh_motion_setting()
        self.bar.refresh_motion_setting()

    def set_progress(self, stage: str, current: int, total: int, eta: str, stopping: bool = False) -> None:
        self.stage_label.setText("Stopping..." if stopping else (stage or "Working"))
        self.bar.set_progress(current, total)
        parts = []
        if total > 0:
            parts.append(f"{current:,} of {total:,}")
        if eta and not stopping:
            parts.append(f"about {eta} left")
        parts.append("You can keep browsing.")
        self.detail_label.setText("  ·  ".join(parts))
        self.cancel_button.setEnabled(not stopping)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(2, 2, -2, -2)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._theme.raised_bg.qcolor())
        painter.drawRoundedRect(rect, 8, 8)
        halo = QColor(AURORA_COLORS[2])
        halo.setAlpha(55)
        painter.setPen(QPen(halo, 6.0))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QPen(aurora_gradient(rect.center(), -self._angle), 2.0))
        painter.drawRoundedRect(rect, 8, 8)


class AIAnalyzeCard(QWidget):
    """What sits under the inspector's AI analysis readout: the button, the progress block, or an explanatory hint."""

    analyze_requested = Signal()
    cancel_requested = Signal()
    clear_filter_requested = Signal()

    PAGE_BUTTON, PAGE_PROGRESS, PAGE_BLOCKED = 0, 1, 2

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._theme = default_theme()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(6)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("secondaryText")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("background: transparent;")
        self.summary_label.hide()
        layout.addWidget(self.summary_label)

        self.stack = QStackedWidget(self)
        button_page = QWidget(self.stack)
        button_layout = QHBoxLayout(button_page)
        button_layout.setContentsMargins(0, 0, 0, 0)
        self.button = AuroraButton("Analyze folder", button_page)
        self.button.setToolTip("Analyze every photo in this folder with AI")
        self.button.clicked.connect(lambda _checked=False: self.analyze_requested.emit())
        button_layout.addWidget(self.button, 0, Qt.AlignmentFlag.AlignLeft)
        button_layout.addStretch(1)
        self.stack.addWidget(button_page)

        self.progress = ProgressBlock(self.stack)
        self.progress.cancel_requested.connect(self.cancel_requested.emit)
        self.stack.addWidget(self.progress)

        blocked_page = QWidget(self.stack)
        blocked_layout = QVBoxLayout(blocked_page)
        blocked_layout.setContentsMargins(0, 0, 0, 0)
        blocked_layout.setSpacing(4)
        self.blocked_label = QLabel("")
        self.blocked_label.setObjectName("secondaryText")
        self.blocked_label.setWordWrap(True)
        self.blocked_label.setStyleSheet("background: transparent;")
        self.clear_filter_button = QPushButton("Clear filter")
        self.clear_filter_button.setFlat(True)
        self.clear_filter_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_filter_button.clicked.connect(lambda _checked=False: self.clear_filter_requested.emit())
        blocked_layout.addWidget(self.blocked_label)
        blocked_layout.addWidget(self.clear_filter_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.stack.addWidget(blocked_page)
        layout.addWidget(self.stack)
        self._show_page(self.PAGE_BUTTON)

    def apply_theme(self, theme: ThemePalette) -> None:
        self._theme = theme
        self.button.apply_theme(theme)
        self.progress.apply_theme(theme)

    def refresh_motion_setting(self) -> None:
        self.button.refresh_motion_setting()
        self.progress.refresh_motion_setting()

    def _show_page(self, index: int) -> None:
        """Switch pages and let only the current one size the card (a stack otherwise takes its tallest page)."""
        for page_index in range(self.stack.count()):
            page = self.stack.widget(page_index)
            policy = QSizePolicy.Policy.Preferred if page_index == index else QSizePolicy.Policy.Ignored
            page.setSizePolicy(policy, policy)
        self.stack.setCurrentIndex(index)
        self.stack.updateGeometry()
        self.updateGeometry()

    def state(self) -> str:
        return {self.PAGE_BUTTON: "idle", self.PAGE_PROGRESS: "running", self.PAGE_BLOCKED: "blocked"}[self.stack.currentIndex()]

    def set_idle(self, *, has_results: bool, summary: str = "") -> None:
        self._show_page(self.PAGE_BUTTON)
        self.summary_label.setText(summary)
        self.summary_label.setVisible(bool(summary))
        self.button.setText("Analyze folder")

    def set_running(self, *, stage: str, current: int, total: int, eta: str, stopping: bool = False) -> None:
        self._show_page(self.PAGE_PROGRESS)
        self.summary_label.hide()
        self.progress.set_progress(stage, current, total, eta, stopping)

    def set_blocked(self, text: str, *, can_clear_filter: bool) -> None:
        """The button is hidden (a filter is on, or this is not a folder view); explain why in one line."""
        self._show_page(self.PAGE_BLOCKED)
        self.summary_label.hide()
        self.blocked_label.setText(text)
        self.clear_filter_button.setVisible(can_clear_filter)
