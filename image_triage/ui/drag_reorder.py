"""Press-and-drag reordering for a row or column of buttons, with the neighbours sliding aside as you move.

The owner hands over the widgets in display order (``set_items``). Dragging one lifts it under the cursor along the
row's axis; as it passes the midpoint of a neighbour, that neighbour slides into the gap it left, the way the left rail
of a chat app behaves. On release the dragged widget settles into its slot and ``order_committed`` reports the new key
order. Nothing is written to the layout here: the owner saves the order and rebuilds, so a drag that changes nothing
leaves nothing behind. A press that never travels ``startDragDistance`` is an ordinary click.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRect, Qt, Signal
from PySide6.QtWidgets import QAbstractButton, QApplication, QToolButton, QWidget

SLIDE_MS = 170


class _Drag:
    def __init__(self, widget: QWidget, start_index: int, slots: list[QRect], grab_offset: int) -> None:
        self.widget = widget
        self.start_index = start_index
        self.slots = slots
        self.grab_offset = grab_offset
        self.target = start_index


class DragReorder(QObject):
    order_committed = Signal(list)

    def __init__(
        self,
        orientation: Qt.Orientation,
        parent: QObject | None = None,
        *,
        enabled: Callable[[], bool] | None = None,
        duration_ms: int = SLIDE_MS,
    ) -> None:
        super().__init__(parent)
        self._horizontal = orientation == Qt.Orientation.Horizontal
        self._enabled = enabled
        self._duration = duration_ms
        self._items: list[tuple[str, QWidget]] = []
        self._press: tuple[QWidget, QPoint] | None = None
        # A button whose menu opens the moment it is pressed: the press is held back until we know it is not a drag.
        self._held_back: QToolButton | None = None
        self._drag: _Drag | None = None
        self._animations: dict[QWidget, QPropertyAnimation] = {}
        self._cursor_set = False

    # -- what the owner tells it -----------------------------------------------

    def set_items(self, items: Sequence[tuple[str, QWidget]]) -> None:
        """The draggable widgets, in display order. Call again after every rebuild."""
        self.cancel()
        for _key, widget in self._items:
            try:
                widget.removeEventFilter(self)
            except RuntimeError:  # the widget was deleted by a rebuild
                pass
        self._items = list(items)
        for _key, widget in self._items:
            widget.installEventFilter(self)

    def is_dragging(self) -> bool:
        return self._drag is not None

    def keys(self) -> list[str]:
        return [key for key, _widget in self._items]

    def cancel(self) -> None:
        """Drop any drag in progress without committing it."""
        for animation in self._animations.values():
            animation.stop()
        self._animations.clear()
        self._press = None
        self._held_back = None
        if self._drag is not None:
            self._drag = None
        self._restore_cursor()

    # -- geometry helpers ------------------------------------------------------

    def _start(self, rect: QRect) -> int:
        return rect.left() if self._horizontal else rect.top()

    def _end(self, rect: QRect) -> int:
        return rect.right() + 1 if self._horizontal else rect.bottom() + 1

    def _centre(self, rect: QRect) -> int:
        return (self._start(rect) + self._end(rect)) // 2

    def _size(self, widget: QWidget) -> int:
        return widget.width() if self._horizontal else widget.height()

    def _origin_in_slot(self, widget: QWidget, slot: QRect) -> QPoint:
        """Where ``widget`` sits when it occupies ``slot``: centred on the slot along the axis, its own place across it."""
        along = self._centre(slot) - self._size(widget) // 2
        return QPoint(along, widget.y()) if self._horizontal else QPoint(widget.x(), along)

    def _axis_of(self, point: QPoint) -> int:
        return point.x() if self._horizontal else point.y()

    # -- the drag --------------------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: D102 - Qt override
        kind = event.type()
        if kind == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton and (self._enabled is None or self._enabled()):
                self._press = (watched, event.globalPosition().toPoint())
                if self._opens_menu_on_press(watched):
                    self._held_back = watched
                    return True
            return False
        if kind == QEvent.Type.MouseMove and self._press is not None and event.buttons() & Qt.MouseButton.LeftButton:
            if self._drag is None:
                travelled = (event.globalPosition().toPoint() - self._press[1]).manhattanLength()
                if travelled >= QApplication.startDragDistance():
                    self._begin(watched, event)
            if self._drag is not None:
                self._follow(event)
                return True
            return False
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            self._press = None
            held_back, self._held_back = self._held_back, None
            if self._drag is not None:
                self._finish()
                return True
            if held_back is not None and held_back is watched:
                held_back.showMenu()  # a plain press-and-release: do what the press would have done
                return True
        return False

    @staticmethod
    def _opens_menu_on_press(widget: QWidget) -> bool:
        return (
            isinstance(widget, QToolButton)
            and widget.menu() is not None
            and widget.popupMode() == QToolButton.ToolButtonPopupMode.InstantPopup
        )

    def _begin(self, widget: QWidget, event) -> None:
        widgets = [candidate for _key, candidate in self._items]
        if widget not in widgets or widget.parentWidget() is None:
            self._press = None
            return
        ordered = sorted(widgets, key=lambda candidate: self._start(candidate.geometry()))
        slots = [candidate.geometry() for candidate in ordered]
        # The keys follow the on-screen order, which is what ``_follow`` reasons about.
        by_widget = {candidate: key for key, candidate in self._items}
        self._items = [(by_widget[candidate], candidate) for candidate in ordered]
        parent = widget.parentWidget()
        # Where the button was grabbed, not where the pointer is once the drag starts: it must not jump under the cursor.
        grabbed_at = self._press[1] if self._press is not None else event.globalPosition().toPoint()
        mouse = self._axis_of(parent.mapFromGlobal(grabbed_at))
        index = ordered.index(widget)
        self._drag = _Drag(widget, index, slots, mouse - self._start(widget.geometry()))
        widget.raise_()
        if isinstance(widget, QAbstractButton):
            widget.setDown(False)  # the press is ours now; the button must not fire on release
        if not self._cursor_set:
            QApplication.setOverrideCursor(Qt.CursorShape.ClosedHandCursor)
            self._cursor_set = True

    def _follow(self, event) -> None:
        drag = self._drag
        assert drag is not None
        widget = drag.widget
        parent = widget.parentWidget()
        mouse = self._axis_of(parent.mapFromGlobal(event.globalPosition().toPoint()))
        low = self._start(drag.slots[0])
        high = self._end(drag.slots[-1]) - self._size(widget)
        along = max(low, min(high, mouse - drag.grab_offset))
        widget.move(QPoint(along, widget.y()) if self._horizontal else QPoint(widget.x(), along))
        centre = along + self._size(widget) // 2
        target = min(range(len(drag.slots)), key=lambda i: abs(self._centre(drag.slots[i]) - centre))
        if target == drag.target:
            return
        drag.target = target
        for slot_index, (_key, other) in enumerate(self._arranged(drag)):
            if other is not widget:
                self._slide(other, self._origin_in_slot(other, drag.slots[slot_index]))

    def _arranged(self, drag: _Drag) -> list[tuple[str, QWidget]]:
        """The items in the order they would take if the drag ended now."""
        items = list(self._items)
        moved = items.pop(drag.start_index)
        items.insert(drag.target, moved)
        return items

    def _slide(self, widget: QWidget, end: QPoint) -> None:
        running = self._animations.get(widget)
        if running is not None and running.state() == QPropertyAnimation.State.Running and running.endValue() == end:
            return
        if running is not None:
            running.stop()
        if widget.pos() == end:
            return
        animation = QPropertyAnimation(widget, b"pos", self)
        animation.setDuration(self._duration)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.setStartValue(widget.pos())
        animation.setEndValue(end)
        self._animations[widget] = animation
        animation.start()

    def _finish(self) -> None:
        drag = self._drag
        assert drag is not None
        self._restore_cursor()
        widget = drag.widget
        end = self._origin_in_slot(widget, drag.slots[drag.target])
        order = [key for key, _widget in self._arranged(drag)]
        changed = drag.target != drag.start_index
        self._drag = None
        self._slide(widget, end)
        settled = self._animations.get(widget)

        def commit() -> None:
            self._animations.clear()
            if changed:
                self.order_committed.emit(order)

        if settled is None:  # already home: nothing to wait for
            commit()
        else:
            settled.finished.connect(commit)

    def _restore_cursor(self) -> None:
        if self._cursor_set:
            QApplication.restoreOverrideCursor()
            self._cursor_set = False
