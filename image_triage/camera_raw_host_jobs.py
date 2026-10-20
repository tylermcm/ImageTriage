"""Applying Super Resolution when a photo is finished in the editor's Camera Raw dialog (Done), with the editor's progress popup.

Super Resolution is a setting in the edit stack, applied when the photo is output. Pressing Done is the moment Photoshop applies it, and
a minute of AI work should not hide behind a frozen button, so the editor asks for it and shows "Applying Super Resolution…". The editor
is the control server and cannot call the host: it holds the request until the host polls ``ui.hostJob`` (``HostJobRunner.poll``, on
the autosave tick), which claims it, renders the photo's live settings through ``full_render`` (which applies Super Resolution and keeps
the result in ``super_resolution_cache``, so later outputs reuse it) and reports progress back. The user's Cancel comes back in every
progress reply.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from . import editor_tools, full_render
from . import upscale_service as service

KIND_SUPER_RESOLUTION = "superResolution"
REPORT_EVERY_S = 0.4
log = logging.getLogger(__name__)


class HostJobRunner:
    def __init__(
        self,
        *,
        render: Callable[..., full_render.RenderResult] = full_render.request_full_render,
        clock: Callable[[], float] = time.monotonic,
        spawn_thread: Callable[[Callable[[], None]], Any] | None = None,
        gate: Callable[[str], str] = editor_tools.block_reason,
        on_blocked: Callable[[str, str], None] | None = None,
        tools: Callable[[], list[str]] | None = None,
    ) -> None:
        """``gate(kind)`` says why an editor-tool job cannot run (a model it needs is not installed), or "" when it can; ``on_blocked(kind, why)`` lets the window tell the person."""
        self._gate = gate
        self._tools = tools if tools is not None else (lambda: sorted(kind for kind in editor_tools.EDITOR_KINDS if not gate(kind)))
        self._on_blocked = on_blocked
        self._render = render
        self._clock = clock
        self._spawn = spawn_thread or self._start_daemon
        self._lock = threading.Lock()
        self._busy = False

    @staticmethod
    def _start_daemon(target: Callable[[], None]) -> threading.Thread:
        thread = threading.Thread(target=target, name="camera-raw-host-job", daemon=True)
        thread.start()
        return thread

    @property
    def busy(self) -> bool:
        return self._busy

    # --- polling ---------------------------------------------------------------------------------------------------------

    def poll(self, proc) -> bool:
        """Look for a request in the editor's open Camera Raw dialog and start it. Cheap and non-blocking: safe on the autosave tick.
        Returns whether a job was started. Even with nothing to do, the poll tells the editor that Image Triage is there."""
        if getattr(proc.control, "camera_raw_host_job_supported", False) is not True:
            return False
        try:
            return self._poll(proc)
        except Exception:  # a failed poll must never get in the way of the autosave that follows it
            log.debug("Polling the editor for host jobs failed", exc_info=True)
            return False

    def _poll(self, proc) -> bool:
        lane = proc.fast_lane()
        # An editor that understands it also gets the kinds this host can run right now (the editor keeps the last list): its mask buttons for the others are greyed out.
        params: dict[str, Any] = {"tools": self._tools()} if getattr(proc.control, "camera_raw_host_tools_supported", False) is True else {}
        state = lane.call("ui.hostJob", params)
        if self._busy:
            return False
        pending = state.get("pending")
        if not isinstance(pending, dict) or not isinstance(pending.get("id"), int):
            return False
        kind = pending.get("kind")
        if isinstance(kind, str) and self._blocked(lane, int(pending["id"]), kind):
            return False
        if kind != KIND_SUPER_RESOLUTION:
            return False
        job_id = int(pending["id"])
        lane.call("ui.hostJob", {"claim": job_id})
        source = proc.source_path
        if not source:
            self._finish(lane, job_id, False, "Image Triage does not know which photo is open.")
            return False
        with self._lock:
            self._busy = True
        self._spawn(lambda: self._run(lane, job_id, source))
        return True

    def _blocked(self, lane, job_id: int, kind: str) -> bool:
        """An editor-tool job whose models are not installed is answered at once, with what to turn on, instead of leaving the editor waiting."""
        why = self._gate(kind)
        if not why:
            return False
        lane.call("ui.hostJob", {"claim": job_id})
        self._finish(lane, job_id, False, why)
        if self._on_blocked is not None:
            try:
                self._on_blocked(kind, why)
            except Exception:
                log.debug("The blocked-job notice failed", exc_info=True)
        return True

    # --- the job ---------------------------------------------------------------------------------------------------------------

    def _run(self, lane, job_id: int, source: str) -> None:
        cancel = threading.Event()
        started = self._clock()
        last_report = [started]

        def report(fraction: float, message: str, *, force: bool = False) -> None:
            now = self._clock()
            if not force and now - last_report[0] < REPORT_EVERY_S:
                return
            last_report[0] = now
            fraction = min(1.0, max(0.0, fraction))
            params: dict[str, Any] = {"id": job_id, "fraction": fraction, "message": message}
            elapsed = now - started
            if fraction >= 0.05 and elapsed > 1.0:
                params["etaSeconds"] = max(0.0, elapsed * (1.0 - fraction) / fraction)
            try:
                reply = lane.call("ui.hostJob", {"progress": params})
            except Exception:
                cancel.set()  # the editor is gone or the popup was closed: stop working for nobody
                return
            if reply.get("cancel") is True:
                cancel.set()

        def on_progress(progress: service.UpscaleProgress) -> None:
            if progress.stage == "save":
                report(0.99, "Saving…")
            else:
                report(progress.fraction * 0.98, "Applying Super Resolution…")

        try:
            report(0.0, "Rendering your edits…", force=True)
            result = self._render(source, progress=on_progress, cancel=cancel)
            if cancel.is_set():
                result.release()
                self._finish(lane, job_id, False, "Cancelled")
            elif result.outcome == full_render.FAILED:
                self._finish(lane, job_id, False, result.reason or "the photo could not be rendered")
            else:
                note = "" if not result.temporary else " (it could not be kept, so it will be made again for an export)"
                result.release()
                self._finish(lane, job_id, True, f"Super Resolution applied{note}.")
        except Exception as error:  # a job that broke is reported in the popup, never swallowed
            log.exception("Super Resolution of %s failed", source)
            self._finish(lane, job_id, False, f"{error.__class__.__name__}: {error}")
        finally:
            with self._lock:
                self._busy = False

    @staticmethod
    def _finish(lane, job_id: int, ok: bool, message: str) -> None:
        try:
            lane.call("ui.hostJob", {"finish": {"id": job_id, "ok": ok, "message": message[:380]}})
        except Exception:
            log.warning("Could not report the end of host job %s to the editor", job_id, exc_info=True)
