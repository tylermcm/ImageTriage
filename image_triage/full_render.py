"""A photo's PhotoCraft edits at full size, for exports and hand-offs (audit findings F2 and F3).

``request_full_render(path)`` answers with one of three outcomes and never substitutes anything:

- ``NO_EDITS``: the photo has no PhotoCraft edits (no project, no Camera Raw recipe or only one that changes nothing, and no
  adjusted dialog); exporting the original is right.
- ``rendered``: a full-size picture of the edits, with what the file actually holds (format, size, bits per channel, ICC).
- ``failed``: the photo has edits that could not be rendered (or edit data that could not be read). The photo must be reported as
  not exported, never exported as the original or from the screen-sized picture.

Which edits are rendered, for the requested photo: first its live state in a running editor (a Camera Raw dialog open on it with
adjustments, read without changing the dialog; or unsaved changes in the full editor, saved first), then its saved full-editor
project, then its saved Camera Raw recipe. A project is taken from its manifest-verified full-size render when that is current,
otherwise the editor renders it (``app.render``), from its in-memory snapshot while a save of it is in flight.

Renders go through a connection of their own (``PhotoCraftProcess.render_lane``), one at a time, and can be cancelled. A photo
outside every running editor's folder gets a hidden editor of its own for the render, quit afterwards. The service is registered by
the preview controller, which owns the running editor; without one, only current full-size renders on disk can be used.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import photocraft_bridge as bridge

NO_EDITS_OUTCOME = "no_edits"
RENDERED = "rendered"
FAILED = "failed"


@dataclass(frozen=True)
class RenderResult:
    outcome: str
    path: Path | None = None
    format: str = ""
    width: int = 0
    height: int = 0
    depth: int = 0
    icc: bool = False
    reason: str = ""
    # The file was made for this request: the caller deletes it once it has read it (``release``).
    temporary: bool = False

    def release(self) -> None:
        if self.temporary and self.path is not None:
            try:
                os.remove(self.path)
            except OSError:
                pass


NO_EDITS = RenderResult(NO_EDITS_OUTCOME)


def failed(reason: str) -> RenderResult:
    return RenderResult(FAILED, reason=reason)


class RenderCancelled(bridge.PhotoCraftError):
    """The render was cancelled (the app is shutting down)."""


_service: "FullRenderService | None" = None
_service_lock = threading.Lock()


def register(service: "FullRenderService | None") -> None:
    global _service
    with _service_lock:
        _service = service


def unregister(service: "FullRenderService") -> None:
    global _service
    with _service_lock:
        if _service is service:
            _service = None


def request_full_render(image_path: str, *, progress=None, cancel: threading.Event | None = None) -> RenderResult:
    """See the module docs. Blocks while the editor renders: call it from a worker (or wait for it with the event loop running).
    ``progress`` receives ``upscale_service.UpscaleProgress`` while Super Resolution is applied; setting ``cancel`` stops it."""
    with _service_lock:
        service = _service
    if service is not None:
        return service.request(image_path, progress=progress, cancel=cancel)
    return FullRenderService(lambda: None).request(image_path, progress=progress, cancel=cancel)


def _described(path: Path, reply: dict, *, temporary: bool) -> RenderResult:
    if (reply.get("format") not in ("png", "tiff") or reply.get("depth") not in (8, 16, 32)
            or not isinstance(reply.get("icc"), bool)
            or any(not isinstance(reply.get(key), int) or isinstance(reply[key], bool) or reply[key] <= 0 for key in ("width", "height"))):
        raise bridge.PhotoCraftError("the editor did not describe a full-resolution render")
    return RenderResult(
        RENDERED, path=path, format=str(reply.get("format", "")), width=int(reply.get("width", 0)), height=int(reply.get("height", 0)),
        depth=int(reply.get("depth", 0)), icc=bool(reply.get("icc", False)), temporary=temporary,
    )


def _without_host_settings(recipe: dict) -> dict:
    """The recipe as the editor should see it: settings the host carries out itself (Super Resolution) are not sent."""
    settings = recipe.get("cameraRaw") if isinstance(recipe, dict) else None
    if not isinstance(settings, dict) or "superResolution" not in settings:
        return recipe
    return {**recipe, "cameraRaw": {k: v for k, v in settings.items() if k != "superResolution"}}


class FullRenderService:
    """The renders behind ``request_full_render``. ``editor`` returns the running editor (a ``PhotoCraftProcess``) or None;
    ``save_open_photo`` captures unsaved changes for the named photo; pending saves are rendered from their immutable snapshot. ``launch`` starts a
    hidden editor for a folder (``launch(folder) -> PhotoCraftProcess``)."""

    POLL_S = 0.05
    TIMEOUT_S = 600.0

    def __init__(
        self,
        editor: Callable[[], "bridge.PhotoCraftProcess | None"],
        *,
        save_open_photo: Callable[["bridge.PhotoCraftProcess", str], None] | None = None,
        launch: Callable[[str], "bridge.PhotoCraftProcess"] | None = None,
        source_key: Callable[[str], str] | None = None,
        super_resolution: Callable[[RenderResult, threading.Event], RenderResult] | None = None,
    ) -> None:
        self._editor = editor
        self._save_open_photo = save_open_photo
        self._launch = launch
        self._source_key = source_key or (lambda path: os.path.normcase(os.path.abspath(path)))
        self._one_at_a_time = threading.Lock()
        self._state = threading.Lock()
        self._closing = False
        self._cancel = threading.Event()  # set on shutdown: stops a Super Resolution upsize in progress
        self._active_cancels: set[threading.Event] = set()  # the events callers gave their requests: shutdown sets them too
        self._super_resolution = super_resolution
        self._running: tuple[object, int] | None = None  # (render lane, job) of the render in progress
        self._temporary: dict[int, object] = {}

    # --- what is rendered -------------------------------------------------------------------------------------------------

    def request(self, image_path: str, *, progress=None, cancel: threading.Event | None = None) -> RenderResult:
        if self._closing:
            return failed("Image Triage is closing")
        try:
            with self._one_at_a_time:
                if self._closing:
                    return failed("Image Triage is closing")
                return self._request(image_path, progress, cancel)
        except RenderCancelled:
            return failed("the render was cancelled")
        except Exception as error:  # an editor that broke, a file that went away: a failure for this photo, never the original
            logging.getLogger(__name__).warning("Full-size render of %s failed: %s", image_path, error)
            return failed(str(error) or error.__class__.__name__)

    def _request(self, image_path: str, progress=None, cancel: threading.Event | None = None) -> RenderResult:
        proc = self._editor()
        live = proc if proc is not None and proc.is_running() and proc.current_source == self._source_key(image_path) else None
        if live is not None:
            dialog = self._open_dialog_recipe(live, image_path)
            if dialog is not None:
                state, reason = bridge.classify_recipe(dialog)
                if state in (bridge.RECIPE_EDITED, bridge.RECIPE_IDENTITY):
                    return self._with_super_resolution(
                        image_path, dialog, lambda: self._render(image_path, {"recipe": dialog}, proc=live), progress, cancel
                    )
                if state == bridge.RECIPE_INVALID:
                    return failed(reason)
            elif self._save_open_photo is not None:
                self._save_open_photo(live, image_path)
        sidecar = bridge.sidecar_pcraft_path(image_path)
        pending = proc is not None and (str(sidecar) in proc.pending_stashes or str(sidecar) in proc.stash_errors)
        if pending or sidecar.is_file():
            if not pending:
                manifest = bridge.current_render_manifest(image_path, verify_project=True)
                if manifest and manifest.kind == "project" and "png" in manifest.outputs and manifest.project_depth == manifest.outputs["png"].depth:
                    output = manifest.outputs["png"]
                    return RenderResult(RENDERED, path=output.path, format=output.format, width=output.width, height=output.height,
                                        depth=output.depth, icc=output.icc)
            return self._render(image_path, {"project": sidecar}, proc=proc, pending=pending)
        state, recipe, reason = bridge.recipe_state(image_path)
        if state == bridge.RECIPE_INVALID:
            return failed(reason)
        if state == bridge.RECIPE_EDITED:
            return self._with_super_resolution(
                image_path, recipe, lambda: self._render(image_path, {"recipe": recipe}, proc=proc), progress, cancel
            )
        return NO_EDITS

    def _with_super_resolution(self, image_path: str, recipe: dict, render, progress, cancel: threading.Event | None) -> RenderResult:
        """Detail > Super Resolution: the editor only records the setting; the host upsizes the finished render, after the editor is done
        with it, and never exports the un-upsized picture in its place. A photo already made for exactly these settings is not made again,
        and a new one is kept for the next output."""
        from . import camera_raw_super_resolution as sr  # imported late: these modules import this one
        from . import super_resolution_cache as cache

        if not sr.wants_super_resolution(recipe):
            return render()
        kept = cache.lookup(image_path, recipe)
        if kept is not None:
            return kept
        result = render()
        if result.outcome != RENDERED:
            return result
        event = cancel or self._cancel
        with self._state:
            self._active_cancels.add(event)
        try:
            if self._super_resolution is not None:
                upsized = self._super_resolution(result, event)
            else:
                upsized = sr.apply_super_resolution(result, cancel_event=event, progress=progress)
        finally:
            with self._state:
                self._active_cancels.discard(event)
        return cache.store(image_path, recipe, upsized) if upsized.outcome == RENDERED else upsized

    def _open_dialog_recipe(self, proc, image_path: str) -> dict | None:
        """The settings of a Camera Raw dialog open on the editor's photo, read without changing it; None when none is open."""
        if not getattr(proc, "camera_raw_first", False):
            return None
        state = proc.render_lane().call("ui.cameraRaw", {})
        if not state.get("open") or state.get("target") != "openRaw" or state.get("changed") is not True:
            return None
        source = state.get("source")
        if source is None or os.path.normcase(proc.path_within_root(image_path) or "") != os.path.normcase(source):
            raise bridge.PhotoCraftError("the editor switched photos during export; retry the export")
        recipe = state.get("recipe")
        return recipe if isinstance(recipe, dict) else None

    # --- the render itself -------------------------------------------------------------------------------------------------

    def _render(self, image_path: str, what: dict, *, proc, pending: bool = False) -> RenderResult:
        folder = str(Path(image_path).parent)
        temporary = None
        if proc is None or not proc.is_running() or proc.path_within_root(image_path) is None:
            if self._launch is None:
                return failed("PhotoCraft is not running, and the photo's edits need it to be rendered at full size")
            temporary = self._launch(folder)
            with self._state:
                self._temporary[id(temporary)] = temporary
            proc = temporary
            pending = False  # a new editor holds no snapshots
        try:
            if self._closing:
                raise RenderCancelled("closing")
            if getattr(proc.control, "full_render_supported", False) is not True:
                return failed("this PhotoCraft build cannot render edits at full size")
            params: dict = {"wait": False}
            name = Path(image_path).name
            out = proc.path_within_write_root(str(bridge.edit_root_for(Path(folder)) / f"{name}.photocraft.export-{uuid.uuid4().hex}"))
            if out is None:
                return failed("the edits folder is outside the editor's write root")
            params["out"] = out
            if "project" in what:
                project = Path(what["project"])
                relative = proc.path_within_root(str(project))
                if relative is None:
                    return failed("the project is outside the editor's folder")
                params["project"] = relative
                if pending:
                    params["snapshot"] = proc.path_within_write_root(str(project))
            else:
                source = proc.path_within_root(image_path)
                if source is None:
                    return failed("the photo is outside the editor's folder")
                params["source"] = source
                bridge.require_finishing_support({"recipe": what["recipe"]}, getattr(proc.control, "camera_raw_finishing_supported", False) is True)
                bridge.require_calibration_support({"recipe": what["recipe"]}, getattr(proc.control, "camera_raw_calibration_supported", False) is True)
                bridge.require_masks_support({"recipe": what["recipe"]}, getattr(proc.control, "camera_raw_masks_supported", False) is True)
                params["recipe"] = _without_host_settings(what["recipe"])
            reply = self._run_job(proc.render_lane(), params)
            if reply.get("out") not in (out + ".png", out + ".tif", out + ".tiff"):
                raise bridge.PhotoCraftError("the editor returned an unexpected render path")
            target = (proc.write_root or proc.read_root) / str(reply["out"])
            try:
                return _described(target, reply, temporary=True)
            except Exception:
                target.unlink(missing_ok=True)
                raise
        finally:
            if temporary is not None:
                with self._state:
                    self._temporary.pop(id(temporary), None)
                temporary.shutdown()

    def _run_job(self, lane, params: dict) -> dict:
        started = lane.call("app.render", params)
        if not started.get("pending"):
            return started
        job = int(started["job"])
        with self._state:
            closing = self._closing
            self._running = (lane, job)
        try:
            if closing:
                lane.call("jobs.cancel", {"job": job})
                raise RenderCancelled("closing")
            deadline = time.monotonic() + self.TIMEOUT_S
            while True:
                info = next((j for j in lane.call("jobs.list").get("jobs", []) if int(j.get("id", -1)) == job), None)
                if info is None:
                    raise bridge.PhotoCraftError("the editor lost track of the render")
                state = info.get("state")
                if state == "done":
                    return info.get("result") or {}
                if state == "cancelled":
                    raise RenderCancelled("cancelled")
                if state not in ("running", "queued"):
                    raise bridge.PhotoCraftError(str(info.get("error") or state))
                if time.monotonic() > deadline:
                    lane.call("jobs.cancel", {"job": job})
                    raise bridge.PhotoCraftError("the render took too long")
                time.sleep(self.POLL_S)
        finally:
            with self._state:
                self._running = None

    # --- shutdown --------------------------------------------------------------------------------------------------------------

    def shutdown(self) -> None:
        """Cancels the render in progress (through its own connection) and quits the hidden editors started for renders. Requests
        after this fail."""
        self._cancel.set()
        with self._state:
            self._closing = True
            running = self._running
            for event in self._active_cancels:
                event.set()
        unregister(self)
        if running is not None:
            lane, job = running
            try:
                lane.call("jobs.cancel", {"job": job})
            except Exception:
                pass
        # The worker owns its temporary process, including a launch that was in
        # flight when shutdown began. Drain it so no editor can appear afterwards.
        with self._one_at_a_time:
            pass
