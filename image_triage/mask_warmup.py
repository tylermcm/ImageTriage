"""Keep the editor's mask models loaded while Camera Raw is connected, so the first mask is quick.

Measured on the author's machine (RTX, Store Python runtime): starting the mask host and importing torch and CUDA took 7 s with the
files in the operating system's cache and 32 s without; OneFormer, BiRefNet and SAM then load in about 1, 2 and 0.5 s. That cost used to
land on the first Landscape, People or Subject request. ``MaskWarmer.tick`` is called from the host-job poll (about every 1.5 s while the
editor is up): the first tick loads everything on a background thread, and later ticks only restart the host's idle countdown, so the models
stay resident for as long as the editor is open (the host still shuts itself down after its idle time once the editor is gone).

Set ``IMAGE_TRIAGE_MASK_WARMUP=0`` to turn it off (nothing is loaded until a mask is asked for).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

ENGINES = ("semantic", "subject", "prompt")
RETRY_AFTER_S = 60.0
# After the editor first connects (which is when a folder is opened) the folder's thumbnails are being made: leave them the disk and the
# CPU for this long before loading anything.
START_DELAY_S = 5.0


def _note(message: str) -> None:
    """One line in the always-on execution log, so a real session shows when the warm-up began and ended."""
    try:
        from .perf import write_execution_log

        write_execution_log("mask-warmup: " + message)
    except Exception:
        pass


def enabled() -> bool:
    return (os.environ.get("IMAGE_TRIAGE_MASK_WARMUP", "1") or "1").strip().casefold() not in {"0", "false", "no", "off"}


def _default_service():
    from .mask_engine_service import default_mask_engine_service

    return default_mask_engine_service()


def _default_installations() -> dict[str, Any]:
    from . import ai_model

    return {
        "semantic": ai_model.resolve_segmentation_model_installation(),
        "subject": ai_model.resolve_birefnet_model_installation(),
        "prompt": ai_model.resolve_sam_model_installation(),
    }


class MaskWarmer:
    def __init__(
        self,
        *,
        service: Callable[[], Any] = _default_service,
        installations: Callable[[], dict[str, Any]] = _default_installations,
        clock: Callable[[], float] = time.monotonic,
        spawn_thread: Callable[[Callable[[], None]], Any] | None = None,
    ) -> None:
        self._service = service
        self._installations = installations
        self._clock = clock
        self._spawn = spawn_thread or self._start_daemon
        self._lock = threading.Lock()
        self._running = False
        self._retry_at = 0.0
        self._first_tick: float | None = None

    @staticmethod
    def _start_daemon(target: Callable[[], None]) -> threading.Thread:
        thread = threading.Thread(target=target, name="mask-warmup", daemon=True)
        thread.start()
        return thread

    @property
    def running(self) -> bool:
        return self._running

    def tick(self) -> bool:
        """Call often and cheaply. Starts loading when the models are not all loaded; otherwise keeps the host alive. Returns whether a
        load was started."""
        if not enabled():
            return False
        with self._lock:
            now = self._clock()
            if self._first_tick is None:
                self._first_tick = now
                _note(f"editor connected; loading starts in {START_DELAY_S:.0f} s")
            if self._running or now < self._retry_at or now - self._first_tick < START_DELAY_S:
                return False
        try:
            service = self._service()
            alive = service.keep_alive()
            loaded = service.loaded_engines()
            if loaded is None:  # a request is running: the models are in use, so there is nothing to load
                return False
            if alive and set(ENGINES) <= set(loaded):
                return False
        except Exception:  # a tick must never get in the way of the autosave that follows it
            log.debug("Checking the mask host failed", exc_info=True)
            return False
        with self._lock:
            if self._running:
                return False
            self._running = True
        _note("loading the masking models in the background (below-normal priority)")
        self._spawn(self._warm)
        return True

    def _warm(self) -> None:
        started = self._clock()
        try:
            service = self._service()
            service.start_low_priority = True  # a speculative load must not slow the browsing it happens behind
            installed = {key: value for key, value in self._installations().items() if getattr(value, "is_installed", False)}
            if not installed:  # nothing to load: do not start torch and CUDA for nothing
                _note("no masking model is installed; nothing loaded")
                self._retry_at = self._clock() + RETRY_AFTER_S
                return
            service.warm_imports(next(iter(installed)))
            for engine in ENGINES:
                if engine in installed:
                    service.warm_model(engine, installed[engine].install_dir)
            log.info("Mask models loaded in %.1f s", self._clock() - started)
            _note(f"models loaded in {self._clock() - started:.1f} s ({', '.join(sorted(installed))})")
        except Exception:
            log.warning("Loading the mask models in the background failed", exc_info=True)
            _note("loading failed (see the app log); will retry in a minute")
            self._retry_at = self._clock() + RETRY_AFTER_S
        finally:
            try:
                self._service().restore_priority()
            except Exception:
                pass
            with self._lock:
                self._running = False
