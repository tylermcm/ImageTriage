"""Off-UI-thread rendering for the popout editor preview.

The editor's adjustment pipeline (global recipe + masked local adjustments)
is expensive on the CPU, and running it synchronously on every slider tick
froze the UI. This module moves that work onto a worker thread behind a
backend-agnostic interface:

- ``EditorRenderBackend`` is the compute contract: turn a base QImage + recipe
  + masked adjustments into a rendered QImage. ``CpuEditorRenderBackend`` is
  the current PIL implementation. A future GPU backend implements the same
  method and drops straight in.
- ``EditorRenderService`` owns a single worker thread and single-flights
  requests: only one render runs at a time and only the most recent request is
  ever rendered, so a fast drag coalesces to its latest value instead of
  queueing. Results are delivered back on the main thread via ``rendered``.

The backend also caches the two things that stay constant during a drag — the
base image's QImage→PIL conversion and each mask group's strength field — so a
drag reuses them instead of rebuilding every tick. All output is full
resolution; nothing here reduces fidelity.
"""
from __future__ import annotations

import threading
from typing import Any, Protocol

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Signal
from PySide6.QtGui import QImage
from PIL import Image as PILImage

from .image_resize import _pillow_from_qimage, _qimage_from_pillow
from .perf import perf_logger
from .ui.mask_overlay import mask_strength_qimage


MaskedAdjustment = tuple  # (components, source_size, mask_recipe)


def _freeze_value(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 3)
    return value


def _freeze_component(component: Any) -> tuple:
    mask_type, params = component[0], component[1]
    combine = component[2] if len(component) >= 3 else "add"
    frozen = tuple(sorted((k, _freeze_value(v)) for k, v in dict(params).items()))
    return (str(mask_type), frozen, str(combine))


class EditorRenderBackend(Protocol):
    """Compute contract shared by CPU (and, later, GPU) render backends."""

    def render(
        self,
        base_image: QImage,
        recipe: Any,
        masked: list[MaskedAdjustment],
        *,
        base_key: tuple | None = None,
    ) -> QImage:
        ...


class CpuEditorRenderBackend:
    """PIL implementation of the editor pipeline, with drag-invariant caches.

    Thread-safe: ``render`` may be called from the service's worker thread and,
    for non-drag paths (zoom/focus), synchronously from the main thread. The
    caches are lock-guarded; the heavy PIL work runs outside the lock on
    per-call local images, so a cache overwrite never disturbs an in-flight
    render (it captured its own ``source`` reference under the lock).
    """

    def __init__(self, *, max_strength_entries: int = 8) -> None:
        self._lock = threading.Lock()
        self._base_key: tuple | None = None
        self._base_pil: PILImage.Image | None = None
        self._strength_cache: dict[tuple, PILImage.Image] = {}
        self._max_strength_entries = max_strength_entries

    def invalidate(self) -> None:
        with self._lock:
            self._base_key = None
            self._base_pil = None
            self._strength_cache.clear()

    def render(
        self,
        base_image: QImage,
        recipe: Any,
        masked: list[MaskedAdjustment],
        *,
        base_key: tuple | None = None,
    ) -> QImage:
        logger = perf_logger()
        with logger.span(
            "editslider.render_image",
            w=base_image.width(),
            h=base_image.height(),
            masks=len(masked),
        ):
            source = None
            with self._lock:
                if base_key is not None and base_key == self._base_key and self._base_pil is not None:
                    source = self._base_pil
            if source is None:
                with logger.span("editslider.qimage_to_pil", w=base_image.width(), h=base_image.height()):
                    source = _pillow_from_qimage(base_image)
                with self._lock:
                    self._base_key = base_key
                    self._base_pil = source
            # apply() is functional (never mutates its input), so the cached
            # base can be reused across ticks.
            with logger.span("editslider.recipe_apply"):
                adjusted = recipe.apply(source)
            for group_index, (components, source_size, mask_recipe) in enumerate(masked):
                strength = self._strength_for(
                    components, adjusted.width, adjusted.height, source_size, logger, group_index
                )
                if strength is None:
                    continue
                with logger.span("editslider.mask_recipe_apply", group=group_index):
                    local = mask_recipe.apply(adjusted)
                with logger.span("editslider.mask_composite", group=group_index):
                    adjusted = PILImage.composite(local, adjusted, strength)
            with logger.span("editslider.pil_to_qimage", w=adjusted.width, h=adjusted.height):
                return _qimage_from_pillow(adjusted, target_size=QSize())

    def _strength_for(
        self, components, width, height, source_size, logger, group_index
    ) -> PILImage.Image | None:
        key = (tuple(_freeze_component(c) for c in components), width, height, source_size)
        with self._lock:
            cached = self._strength_cache.get(key)
        if cached is not None:
            return cached
        with logger.span("editslider.mask_strength", group=group_index, components=len(components)):
            strength_q = mask_strength_qimage(components, width, height, source_size)
            if strength_q is None:
                return None
            strength = PILImage.frombuffer(
                "L",
                (strength_q.width(), strength_q.height()),
                bytes(strength_q.constBits()),
                "raw",
                "L",
                strength_q.bytesPerLine(),
                1,
            )
        with self._lock:
            if len(self._strength_cache) >= self._max_strength_entries:
                self._strength_cache.clear()
            self._strength_cache[key] = strength
        return strength


class _RenderRunnable(QRunnable):
    def __init__(self, service: "EditorRenderService", request: dict) -> None:
        super().__init__()
        self._service = service
        self._request = request

    def run(self) -> None:  # worker thread
        self._service._run_on_worker(self._request)


class EditorRenderService(QObject):
    """Single-flight, coalescing async front for an ``EditorRenderBackend``.

    ``request`` is called on the main thread. At most one render runs at a
    time; a request that arrives while one is in flight replaces any pending
    request, so a burst of slider ticks renders only the newest state. The
    finished image is delivered on the main thread via ``rendered``.
    """

    # seq, source_key, rendered image — emitted on the main thread.
    rendered = Signal(int, object, QImage)
    # internal worker->main hop (image may be None on failure).
    _worker_done = Signal(int, object, object)

    def __init__(self, backend: EditorRenderBackend, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._backend = backend
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)  # renders serialize; don't starve decode pool
        self._seq = 0
        # The generation of the newest request. A completion whose seq is older
        # than this is stale (a newer request or a cancel superseded it) and is
        # dropped, so an in-flight older frame can never overwrite a newer one.
        self._latest_seq = 0
        self._active_seq: int | None = None
        self._pending: dict | None = None
        self._worker_done.connect(self._on_worker_done)

    @property
    def backend(self) -> EditorRenderBackend:
        return self._backend

    def request(
        self,
        base_image: QImage,
        recipe: Any,
        masked: list[MaskedAdjustment],
        *,
        base_key: tuple,
        source_key: tuple,
    ) -> None:
        self._seq += 1
        self._latest_seq = self._seq
        request = {
            "seq": self._seq,
            "base": base_image,
            "recipe": recipe,
            "masked": masked,
            "base_key": base_key,
            "source_key": source_key,
        }
        logger = perf_logger()
        if self._active_seq is None:
            logger.log("editslider.render_request", seq=self._seq, coalesced=False)
            self._start(request)
        else:
            dropped = self._pending["seq"] if self._pending is not None else None
            logger.log("editslider.render_request", seq=self._seq, coalesced=True, dropped=dropped)
            self._pending = request

    def cancel(self) -> None:
        """Invalidate both the queued and any in-flight render (e.g. on reset).
        Bumping ``_latest_seq`` makes the active render's completion stale, so it
        cannot overwrite whatever is shown after the cancel."""
        self._pending = None
        self._seq += 1
        self._latest_seq = self._seq

    def _start(self, request: dict) -> None:
        self._active_seq = request["seq"]
        self._pool.start(_RenderRunnable(self, request))

    def _run_on_worker(self, request: dict) -> None:  # worker thread
        try:
            image = self._backend.render(
                request["base"], request["recipe"], request["masked"], base_key=request["base_key"]
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as a null result
            perf_logger().log("editslider.render_failed", seq=request["seq"], error=str(exc))
            image = None
        self._worker_done.emit(request["seq"], request["source_key"], image)

    def _on_worker_done(self, seq: int, source_key: object, image: object) -> None:  # main thread
        self._active_seq = None
        # Drop stale completions: only deliver a frame that is still the latest
        # request. A newer request (or a cancel) has bumped _latest_seq past it.
        if seq == self._latest_seq and isinstance(image, QImage) and not image.isNull():
            perf_logger().log("editslider.render_delivered", seq=seq)
            self.rendered.emit(seq, source_key, image)
        elif seq != self._latest_seq:
            perf_logger().log("editslider.render_dropped_stale", seq=seq, latest=self._latest_seq)
        if self._pending is not None:
            request = self._pending
            self._pending = None
            self._start(request)
