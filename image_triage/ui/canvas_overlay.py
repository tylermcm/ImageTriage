"""Shared machinery for the editor's on-canvas overlays.

Three overlays now want the same widget — the pane's image label: the mask
overlay, the crop rectangle and the retouch spot layer. They share attachment,
geometry tracking and source<->display mapping, but nothing else, so the common
part lives here and each tool subclasses it.

Two deliberate differences from the original ``MaskOverlay``:

* ``attach_to`` does **not** call ``raise_()``. With one overlay, last-attached
  happened to be the only one; with three it would silently decide Z-order.
  Stacking belongs to :class:`OverlayStack`, which imposes a fixed order.
* Coordinate mapping goes through a :class:`~image_triage.editor_geometry.ViewTransform`
  rather than assuming the displayed pixmap is a plain scale of the source. With
  no crop the transform is the identity and the arithmetic reduces to exactly
  what it was.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPointF, Qt
from PySide6.QtWidgets import QWidget

from ..editor_geometry import ViewTransform


class CanvasOverlay(QWidget):
    """Base for an overlay parented to a preview pane's image label."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._watched: QWidget | None = None
        self._source_size: tuple[int, int] | None = None
        self._view: ViewTransform | None = None

    # -- attachment -----------------------------------------------------------

    def attach_to(self, label: QWidget) -> None:
        """Parent the overlay to ``label`` and track its size, so the overlay
        always covers exactly the displayed pixmap."""
        if self._watched is label:
            return
        if self._watched is not None:
            self._watched.removeEventFilter(self)
        self._watched = label
        self.setParent(label)
        label.installEventFilter(self)
        self.setGeometry(label.rect())
        self.show()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._watched and event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.setGeometry(self._watched.rect())
        return False

    def set_pass_through(self, on: bool) -> None:
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, on)
        if on:
            self.unsetCursor()

    # Kept for the mask overlay's existing internal callers.
    _set_pass_through = set_pass_through

    # -- geometry -------------------------------------------------------------

    def set_view_transform(self, view: ViewTransform | None) -> None:
        self._view = view
        self.update()

    def _effective_view(self) -> ViewTransform | None:
        if self._view is not None:
            return self._view
        if self._source_size is None:
            return None
        # No crop in play: an identity transform over the source frame, which
        # makes every mapping below reduce to the old scale-only arithmetic.
        return ViewTransform(source_size=self._source_size)

    def _frame_size(self) -> tuple[int, int] | None:
        view = self._effective_view()
        if view is None:
            return None
        width, height = view.frame_size()
        return (width, height) if width >= 1 and height >= 1 else None

    def _scales(self) -> tuple[float, float] | None:
        """Display pixels per frame pixel, or ``None`` when not measurable."""
        frame = self._frame_size()
        if frame is None or self.width() < 2 or self.height() < 2:
            return None
        return self.width() / frame[0], self.height() / frame[1]

    def _to_display(self, x: float, y: float) -> QPointF:
        scales = self._scales()
        view = self._effective_view()
        if scales is None or view is None:
            return QPointF(0, 0)
        frame_x, frame_y = view.source_to_frame(x, y)
        return QPointF(frame_x * scales[0], frame_y * scales[1])

    def _to_source(self, pos: QPointF) -> tuple[float, float]:
        scales = self._scales()
        view = self._effective_view()
        if scales is None or view is None:
            return 0.0, 0.0
        return view.frame_to_source(pos.x() / scales[0], pos.y() / scales[1])


class OverlayStack:
    """Owns Z-order and mouse ownership for the overlays on one pane.

    Exactly one overlay may take the mouse at a time; the rest go
    pass-through. Both facts are decided here rather than by whichever overlay
    happened to attach or set its state last.
    """

    # Bottom to top. Fixed and total: the crop box and spot pins must draw over
    # the mask tint, never under it.
    ORDER: tuple[str, ...] = ("mask", "crop", "retouch")

    def __init__(self, overlays: dict[str, CanvasOverlay]) -> None:
        missing = set(self.ORDER) - set(overlays)
        if missing:
            raise ValueError(f"OverlayStack is missing {sorted(missing)}")
        self._overlays = overlays

    def __iter__(self):
        return iter((name, self._overlays[name]) for name in self.ORDER)

    def __getitem__(self, name: str) -> CanvasOverlay:
        return self._overlays[name]

    def attach(self, label: QWidget) -> None:
        for name in self.ORDER:
            overlay = self._overlays[name]
            overlay.attach_to(label)
            # Raising in ORDER every time is what makes the stacking
            # deterministic, whether or not attach_to did any work.
            overlay.raise_()

    def set_view_transform(self, view: ViewTransform | None) -> None:
        for name in self.ORDER:
            self._overlays[name].set_view_transform(view)

    def set_active(self, name: str | None) -> None:
        for key, overlay in self._overlays.items():
            overlay.set_pass_through(key != name)
