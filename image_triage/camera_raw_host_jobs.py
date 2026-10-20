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

import os
from pathlib import Path

from . import full_render
from . import mask_warmup
from . import scene_mask_jobs
from . import subject_mask_service
from . import upscale_service as service

KIND_SUPER_RESOLUTION = "superResolution"
KIND_SUBJECT_MASK = "subjectMask"
KIND_SCENE_MASKS = "sceneMasks"
KIND_PEOPLE_MASKS = "peopleMasks"
MASK_KINDS = (KIND_SUBJECT_MASK, KIND_SCENE_MASKS, KIND_PEOPLE_MASKS)
REPORT_EVERY_S = 0.4
log = logging.getLogger(__name__)


def _note_job(kind: str, started: float, finished: float, outcome: str) -> None:
    try:
        from .perf import write_execution_log

        write_execution_log(f"host-job: {kind} {outcome} in {finished - started:.1f} s")
    except Exception:
        pass


class HostJobRunner:
    def __init__(
        self,
        *,
        render: Callable[..., full_render.RenderResult] = full_render.request_full_render,
        subject_mask: Callable[..., Any] = scene_mask_jobs.run_subject_mask,
        warmer: Any = None,
        scene_masks: Callable[..., Any] = scene_mask_jobs.run_scene_masks,
        people_masks: Callable[..., Any] = scene_mask_jobs.run_people_masks,
        clock: Callable[[], float] = time.monotonic,
        spawn_thread: Callable[[Callable[[], None]], Any] | None = None,
    ) -> None:
        self._render = render
        self._warmer = warmer if warmer is not None else mask_warmup.MaskWarmer()
        self._subject_mask = subject_mask
        self._mask_jobs = {KIND_SUBJECT_MASK: subject_mask, KIND_SCENE_MASKS: scene_masks, KIND_PEOPLE_MASKS: people_masks}
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
            self._warmer.tick()  # the masking models load in the background as soon as the editor is up
        except Exception:
            log.debug("Warming the mask models failed", exc_info=True)
        try:
            return self._poll(proc)
        except Exception:  # a failed poll must never get in the way of the autosave that follows it
            log.debug("Polling the editor for host jobs failed", exc_info=True)
            return False

    def _poll(self, proc) -> bool:
        lane = proc.fast_lane()
        state = lane.call("ui.hostJob", {})
        if self._busy:
            return False
        pending = state.get("pending")
        if not isinstance(pending, dict) or pending.get("kind") not in (KIND_SUPER_RESOLUTION, *MASK_KINDS) or not isinstance(pending.get("id"), int):
            return False
        job_id = int(pending["id"])
        lane.call("ui.hostJob", {"claim": job_id})
        if pending["kind"] in MASK_KINDS:
            return self._start_mask_job(lane, job_id, pending)
        source = proc.source_path
        if not source:
            self._finish(lane, job_id, False, "Image Triage does not know which photo is open.")
            return False
        with self._lock:
            self._busy = True
        self._spawn(lambda: self._run(lane, job_id, source))
        return True

    # --- the subject mask ------------------------------------------------------------------------------------------------------

    def _start_mask_job(self, lane, job_id: int, pending: dict) -> bool:
        source, output = pending.get("input"), pending.get("output")
        if not isinstance(source, str) or not isinstance(output, str) or not os.path.isfile(source) or not os.path.isdir(os.path.dirname(output) or "."):
            self._finish(lane, job_id, False, "The editor did not give Image Triage a picture to find the subject in.")
            return False
        with self._lock:
            self._busy = True
        kind = pending["kind"]
        self._spawn(lambda: self._run_mask_job(lane, job_id, kind, Path(source), Path(output)))
        return True

    def _run_mask_job(self, lane, job_id: int, kind: str, source: Path, output: Path) -> None:
        cancel = threading.Event()
        started = self._clock()
        last_report = [started]

        def report(fraction: float, message: str, *, force: bool = False) -> None:
            now = self._clock()
            if not force and now - last_report[0] < REPORT_EVERY_S:
                return
            last_report[0] = now
            try:
                reply = lane.call("ui.hostJob", {"progress": {"id": job_id, "fraction": min(1.0, max(0.0, fraction)), "message": message}})
            except Exception:
                cancel.set()
                return
            if reply.get("cancel") is True:
                cancel.set()

        # BiRefNet reports phases, not a percentage: the bar moves one step at each.
        steps = {"Loading": 0.15, "Preparing": 0.5, "Finding": 0.65}

        reports = [0]

        def on_progress(text: str) -> None:
            reports[0] += 1
            if kind == KIND_SUBJECT_MASK:
                fraction = next((value for key, value in steps.items() if text.startswith(key)), 0.3)
            else:  # several models in turn, with no total to count: the bar creeps towards the end
                fraction = 0.1 + 0.85 * (1.0 - 0.85 ** reports[0])
            report(fraction, text[:190])

        try:
            report(0.05, "Starting the masking models…", force=True)
            self._mask_jobs[kind](source, output, on_progress, cancel)
            _note_job(kind, started, self._clock(), "done")
            if cancel.is_set():
                self._finish(lane, job_id, False, "Cancelled")
            else:
                self._finish(lane, job_id, True, "Subject found." if kind == KIND_SUBJECT_MASK else "Done.")
        except scene_mask_jobs.Cancelled:
            self._finish(lane, job_id, False, "Cancelled")
        except subject_mask_service.SubjectMaskError as error:
            self._finish(lane, job_id, False, "Cancelled" if error.category == "cancelled" else f"{error} {error.remediation}".strip())
        except Exception as error:  # a job that broke is reported in the popup, never swallowed
            log.exception("%s of %s failed", kind, source)
            self._finish(lane, job_id, False, f"{error.__class__.__name__}: {error}")
        finally:
            with self._lock:
                self._busy = False

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
