"""The hosted PhotoCraft editor for the QML shell (`editor` in QML): starting it, opening a photo in it, saving, closing and putting its native
window inside the shell's window.

This is the groundwork: the process lifecycle and the window placement over the same `photocraft_bridge` the Widgets app uses. The rest of the
Widgets host's behaviour (save before switching, stash and restore, the persistent filmstrip and theme sync) is listed in
docs/qml_unwired.md under "Editor". The launcher and the window mover are parameters so that tests run without PhotoCraft or Windows.
"""
from __future__ import annotations

import logging
import re
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Property, QMetaMethod, QObject, Qt, QTimer, Signal, Slot

LOG = logging.getLogger(__name__)

IDLE, STARTING, EDITING, CLOSING, FAILED = "idle", "starting", "editing", "closing", "failed"

# How long quitting the app waits for the editor to save the open photo. The editor is closed after that whether or not it
# answered: a save that hangs (an unreachable share) must not keep the app from closing.
SHUTDOWN_SETTLE_S = 5.0
HOST_JOB_POLL_S = 1.5        # how often the editor is asked for a mask or Super Resolution request while it is open


_DIGITS = re.compile(r"\d+")


class _DocumentUncertain(Exception):
    """A switch failed at a point where the editor's document may already be the new photo while saves are still bound to
    the previous one (`error` is what went wrong)."""

    def __init__(self, error: Exception) -> None:
        super().__init__(str(error))
        self.error = error


def _document_kept(error: Exception) -> bool:
    """Whether a failed `app.open` is known to have left the editor's document alone: the request never went out (the
    photo was refused by the host, or superseded), or the editor answered that it could not open the photo. A broken or
    timed-out exchange says nothing about what the editor did."""
    from .. import photocraft_bridge

    if isinstance(error, (photocraft_bridge.PhotoCraftCompatibilityError, photocraft_bridge.PhotoCraftSuperseded)):
        return True
    return (isinstance(error, photocraft_bridge.PhotoCraftError) and error.__cause__ is None
            and str(error).startswith("PhotoCraft control 'app.open' failed:"))


class WindowMover:
    """Puts PhotoCraft's native window inside the shell's window. Windows only; elsewhere nothing moves (and the editor is not offered)."""

    supported = sys.platform == "win32"

    def attach(self, hwnd: int, parent: int) -> None:
        from .. import photocraft_bridge

        photocraft_bridge.embed_in_widget(hwnd, parent)

    def place(self, hwnd: int, x: int, y: int, width: int, height: int) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.SetWindowPos(hwnd, 0, int(x), int(y), max(1, int(width)), max(1, int(height)), 0x0004 | 0x0010)    # no z-order change, no activation

    def show(self, hwnd: int, visible: bool) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow(hwnd, 4 if visible else 0)         # SW_SHOWNOACTIVATE / SW_HIDE


    def focus(self, hwnd: int) -> None:
        """Keyboard focus into the editor's window (it is shown without activation, so the arrow keys and E would otherwise go to the shell)."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.SetFocus.argtypes = [wintypes.HWND]
        user32.SetFocus(hwnd)

    def wants_focus(self, hwnd: int, parent: int) -> bool:
        """True while the shell's window is the active one, the left button is down over the editor's window and the editor does not hold the focus."""
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetFocus.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        if not (user32.GetAsyncKeyState(0x01) & 0x8000) or user32.GetForegroundWindow() != parent or user32.GetFocus() == hwnd:
            return False
        rect, point = wintypes.RECT(), wintypes.POINT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        user32.GetCursorPos(ctypes.byref(point))
        return rect.left <= point.x < rect.right and rect.top <= point.y < rect.bottom


class EditorHost(QObject):
    stateChanged = Signal()
    pathChanged = Signal()
    errorChanged = Signal()
    opened = Signal(str)                    # the photo is open in the editor
    closed = Signal(str)                    # the editor was put away (the photo that was in it)
    failed = Signal(str)
    # Closing could not save the photo and the editor is still up. Its own signal so the shell can offer "Close without saving"
    # (`closeWithoutSaving`); while nothing listens to it, the failure goes out as `failed` like any other.
    closeFailed = Signal(str)
    toolBlocked = Signal(str)               # a mask or tool the editor asked for cannot run (its model is not installed): what to turn on
    editSaved = Signal(str)
    # The photo's Camera Raw settings could not be read and were set aside; it opened from its original develop (the photo,
    # what to tell the person). Sent once per set-aside file. `recoverRecipe` is the offer that goes with it.
    recipeSetAside = Signal(str, str)
    recipeRecovery = Signal(str, bool, str) # `recoverRecipe` finished: the photo, whether an edit was put back, what to say
    _done = Signal(str, object, str)        # worker -> main thread: (what finished, its result, an error message)

    def __init__(self, *, memory: bool = False, launcher=None, detector=None, mover=None, host_jobs=None, parent=None) -> None:
        super().__init__(parent)
        self._memory = memory
        self._launcher = launcher
        self._detector = detector
        self._mover = mover if mover is not None else WindowMover()
        self._state = IDLE
        self._path = ""
        self._error = ""
        self._proc = None
        # Own the complete document transaction, not just individual control calls.
        # Workers may save, replace/bind a photo, or settle on close concurrently.
        self._lock = threading.RLock()
        self._geometry = (0, 0, 0, 0)
        self._parent_hwnd = 0
        self._generation = 0
        self._close_requested = False
        self._switching = False
        self._wanted: tuple[str, bool] | None = None      # asked for while another open was running (`_open_path`)
        self._shown_path = ""                             # the photo the editor was last confirmed to show
        self._told: set[tuple[str, str]] = set()          # (photo, cause) of failures already reported (`_report`)
        self._presentation_blocked = False
        self._space_shortcut = None
        self._filmstrip = None
        self._edit_probe = QTimer(self)
        self._edit_probe.setInterval(1000)
        self._edit_probe.timeout.connect(lambda: self.editSaved.emit(self._path))
        self._done.connect(self._finish, Qt.ConnectionType.QueuedConnection)
        # The editor's Camera Raw dialog asks Image Triage for the heavy work (Subject, Landscape and People masks, Super Resolution). It
        # cannot call the host, so it holds each request until the host polls; nothing answers unless something polls while it is open.
        self._host_jobs = host_jobs
        self._host_job_stop = threading.Event()
        self._host_job_thread: threading.Thread | None = None

    # -- what QML sees ------------------------------------------------------------------------------------------------------
    def _available(self) -> bool:
        if self._memory:
            return True                     # the sample page shows the button as it is drawn in the mockup
        if self._detector is not None:
            return bool(self._detector())
        if not self._mover.supported:
            return False
        try:
            from ..shell_actions import detect_photocraft_executable

            return bool(detect_photocraft_executable())
        except Exception:
            return False

    available = Property(bool, _available, constant=True)
    state = Property(str, lambda self: self._state, notify=stateChanged)
    path = Property(str, lambda self: self._path, notify=pathChanged)
    error = Property(str, lambda self: self._error, notify=errorChanged)
    active = Property(bool, lambda self: self._state in (STARTING, EDITING), notify=stateChanged)
    # Moving from one photo to the next while the editor is up: the stage stays on screen (nothing flashes between photos).
    switching = Property(bool, lambda self: self._switching, notify=stateChanged)

    def _set(self, state: str, error: str | None = None) -> None:
        if error is not None and error != self._error:
            self._error = error
            self.errorChanged.emit()
        if state != self._state:
            self._state = state
            if state == EDITING and not self._memory:
                self._edit_probe.start()
                self._start_host_jobs()
            else:
                self._edit_probe.stop()
            self.stateChanged.emit()

    # -- the editor's requests to the host ----------------------------------------------------------------------------------
    def _start_host_jobs(self) -> None:
        """Poll the editor for mask and Super Resolution requests while it is up. On a thread of its own: the poll talks to the editor
        over its control socket, which must never be waited for on the interface thread."""
        thread = self._host_job_thread
        if thread is not None and thread.is_alive():
            return
        self._host_job_stop.clear()
        self._host_job_thread = threading.Thread(target=self._poll_host_jobs, name="editor-host-jobs", daemon=True)
        self._host_job_thread.start()

    def _poll_host_jobs(self) -> None:
        runner = self._host_jobs
        if runner is None:
            try:
                from ..camera_raw_host_jobs import HostJobRunner

                runner = self._host_jobs = HostJobRunner(on_blocked=lambda _kind, why: self.toolBlocked.emit(why))
            except Exception:
                LOG.warning("the editor's mask and Super Resolution requests cannot be answered", exc_info=True)
                return
        while not self._host_job_stop.is_set():
            proc = self._proc
            if proc is None or self._state not in (EDITING, CLOSING):
                return                                   # the editor is hidden or gone: polling starts again when a photo is opened in it
            try:
                runner.poll(proc)                        # logs and swallows its own failures
            except Exception:
                LOG.debug("host-job poll", exc_info=True)
            self._host_job_stop.wait(HOST_JOB_POLL_S)

    # -- opening a photo ----------------------------------------------------------------------------------------------------
    def setBrowseSource(self, photos, thumbs, preview) -> None:
        """Use the preview's ordered/filtered photo model for native Camera Raw browsing."""
        from .editor_filmstrip import EditorFilmstrip

        if self._filmstrip is not None:
            self._filmstrip.stop()
        self._filmstrip = EditorFilmstrip(self, photos, thumbs, preview)

    @Slot(str)
    def openPath(self, path: str) -> None:
        self._open_path(path, full_editor=False)

    @Slot(str)
    def openFullEditor(self, path: str) -> None:
        self._open_path(path, full_editor=True)

    def _open_path(self, path: str, *, full_editor: bool) -> None:
        if not path or self._state == CLOSING:
            return
        if self._state == STARTING:
            # One open at a time. The newest request made meanwhile is kept and started when this one is over, so the
            # editor ends on the photo that was asked for last and never on one the selection has already left.
            if not self._memory:
                self._wanted = (path, full_editor)
            return
        self._wanted = None
        if self._memory:                    # the sample page has no editor: it only plays the opening screen
            self._path = path
            self.pathChanged.emit()
            self._set(STARTING, "")
            return
        if not self._available():
            self._fail(path, "PhotoCraft is not installed. Install it from Settings › Editor.")
            return
        switching = self._state == EDITING
        if switching and path == self._path:
            return
        self._generation += 1
        self._path = path
        self.pathChanged.emit()
        self._switching = switching
        self._set(STARTING, "")
        self.stateChanged.emit()            # `switching` changes with the state, whether or not the state does
        threading.Thread(target=self._open_worker, args=(self._generation, path, switching, full_editor), name="editor-open", daemon=True).start()

    def _open_worker(self, generation: int, path: str, switching: bool = False, full_editor: bool = False) -> None:
        with self._lock:
            self._open_locked(generation, path, switching, full_editor)

    def _open_locked(self, generation: int, path: str, switching: bool, full_editor: bool) -> None:
        proc = None
        leaving = False                         # the step in hand is saving the photo being left
        try:
            from .. import edit_storage, photocraft_bridge

            directory = str(Path(path).parent)
            proc = self._proc
            is_raw = Path(path).suffix.lower() in photocraft_bridge.RAW_SUFFIXES
            raw_workspace = is_raw or not full_editor
            # A raw going into Camera Raw while Camera Raw shows another: the editor keeps the dialog up and the next photo takes it over
            # (it keeps what was adjusted as the recipe by itself), so the dialog is never closed and reopened, which would show as a flash.
            in_place = bool(
                not full_editor and switching and is_raw and proc is not None and proc.is_running() and getattr(proc, "camera_raw_first", False)
                and getattr(proc.control, "camera_raw_switch_supported", False) is True and proc.path_within_root(path) is not None
            )
            if in_place:
                try:
                    in_place = proc.control.call("ui.cameraRaw").get("open") is True
                except Exception:
                    in_place = False
            if proc is not None and switching and proc.is_running() and not in_place:
                leaving = True
                self._settle(proc)
                self._saved(getattr(proc, "source_path", ""))
                leaving = False
            if proc is not None and (not proc.is_running() or proc.path_within_root(path) is None
                                     or getattr(proc, "camera_raw_first", True) != raw_workspace):
                self._stop(proc)               # the editor can only read the folder it was started on
                proc = None
                in_place = False
            if proc is None:
                launch = self._launcher or photocraft_bridge.launch_photocraft
                write_root = edit_storage.ensure_edit_root(directory)
                extra_args = ("--raw-workspace",) if raw_workspace else ()
                proc = launch(None, read_root=directory, write_root=str(write_root), idle=True, extra_args=extra_args)
                self._sweep_save_temps(write_root, proc)
            sidecar = photocraft_bridge.sidecar_pcraft_path(path)
            open_path = str(sidecar) if sidecar.exists() and not is_raw else path
            relative = proc.path_within_root(open_path)
            if relative is None:
                raise RuntimeError("That photo is outside the folder PhotoCraft can read.")
            if proc.idle_priority:
                from .. import photocraft_bridge

                photocraft_bridge.raise_priority(proc)
            spec = None
            manifest_supported = getattr(proc.control, "render_manifest_supported", False) is True
            signature = photocraft_bridge.file_signature(path) if manifest_supported else None
            if is_raw and open_path == path:
                spec = {"recipePath": photocraft_bridge.recipe_path(path).name,
                        "displayPath": photocraft_bridge.display_render_path(path).name}
                if signature:
                    spec["manifestPath"] = photocraft_bridge.render_manifest_path(path).name
                    spec["signature"] = signature
                recipe = photocraft_bridge.load_recipe(path)
                notice = photocraft_bridge.take_quarantine_notice(path)
                if notice is not None:               # unreadable settings were set aside: the photo opens without them, and says so once
                    self.recipeSetAside.emit(path, notice.message)
                if recipe is not None:
                    spec["recipe"] = recipe
                elif photocraft_bridge.recipe_is_newer(path):
                    # Opening it without its settings would have the editor write this version's recipe over them.
                    raise RuntimeError("This photo's Camera Raw settings were saved by a newer version of Image Triage. Update Image Triage to edit it.")
                if sidecar.exists():
                    spec["continueFrom"] = sidecar.name
            previous = getattr(proc, "source_path", "")
            binding = {"path": sidecar.name, "preview": photocraft_bridge.rendered_preview_path(path).name,
                       "display": photocraft_bridge.display_render_path(path).name}
            if signature:
                binding.update(manifest=photocraft_bridge.render_manifest_path(path).name, source=signature)
            # From the replacement until the binding the editor's document and the files a save would write belong to
            # different photos. A failure in between must not be left like that (`_abandon_switch`).
            try:
                opened = proc.control.app_open(relative, replace=True, **({"camera_raw": spec} if spec is not None else {}))
            except Exception as error:
                if _document_kept(error):
                    raise                            # the editor refused the photo and still shows the previous one
                raise _DocumentUncertain(error) from error
            if previous:
                self.editSaved.emit(previous)
            try:
                proc.control.call("app.bind", binding)
            except Exception as error:
                raise _DocumentUncertain(error) from error
            # Bound: a save now goes to this photo's files, so this is the photo the host has open whatever follows.
            proc.source_path = path
            dialog = in_place and isinstance(opened, dict) and bool((opened.get("cameraRaw") or {}).get("dialog"))
            if full_editor:
                # The RAW workspace develops the saved recipe while the host window is hidden.
                if isinstance(opened, dict) and (opened.get("cameraRaw") or {}).get("dialog"):
                    state = proc.control.call("ui.cameraRaw", {"commit": True})
                    if state.get("open") or not state.get("applied"):
                        raise RuntimeError("PhotoCraft could not develop this RAW for the full editor.")
            elif not dialog:
                raw_state = proc.control.call("ui.cameraRaw", {"open": True})
                if raw_state.get("open") is not True:
                    raise RuntimeError("PhotoCraft did not open Camera Raw for this photo.")
            self._done.emit("open", proc, "")
        except Exception as error:               # a failed launch or open is shown to the person, never raised into the event loop
            uncertain = isinstance(error, _DocumentUncertain)
            if uncertain:
                error = error.error
            message = str(error) or error.__class__.__name__
            LOG.warning("PhotoCraft did not open %s: %s", path, error, exc_info=True)
            if proc is not None and proc is not self._proc:
                self._stop(proc)
            elif uncertain and proc is not None:
                message = self._abandon_switch(proc, message)
            what = "switch-failed" if proc is self._proc and proc is not None and proc.is_running() else "open-failed"
            # Which photo the failure is about: the one that could not be opened, or (a save before the switch) the one left.
            about = (getattr(proc, "source_path", "") if leaving else "") or path
            self._done.emit(what, about if what == "switch-failed" else None, message)

    def _abandon_switch(self, proc, message: str) -> str:
        """A switch failed after the editor may have replaced its document and before the new photo was bound: put the
        previous photo back (it was saved before the switch), so the editor again shows what the host says it shows. If
        that fails too the editor is stopped: better no editor than one that would save one photo under another's name.
        Returns what to tell the person."""
        previous = getattr(proc, "source_path", "")
        try:
            if not previous or not proc.is_running():
                raise RuntimeError("the editor is not running")
            self._reopen(proc, previous)
            return message
        except Exception as error:
            LOG.warning("The editor could not be put back on %s after a failed switch: %s", previous, error, exc_info=True)
            self._stop(proc)
            return message + " The editor was closed because it could not be returned to the previous photo; its saved edits are intact."

    @staticmethod
    def _reopen(proc, path: str) -> None:
        """Open and bind `path` again in the running editor, from what is saved for it."""
        from .. import photocraft_bridge

        is_raw = Path(path).suffix.lower() in photocraft_bridge.RAW_SUFFIXES
        sidecar = photocraft_bridge.sidecar_pcraft_path(path)
        open_path = str(sidecar) if sidecar.exists() and not is_raw else path
        relative = proc.path_within_root(open_path)
        if relative is None:
            raise RuntimeError("the previous photo is outside the folder PhotoCraft can read")
        signature = photocraft_bridge.file_signature(path) if getattr(proc.control, "render_manifest_supported", False) is True else None
        spec = None
        if is_raw:
            spec = {"recipePath": photocraft_bridge.recipe_path(path).name, "displayPath": photocraft_bridge.display_render_path(path).name}
            if signature:
                spec.update(manifestPath=photocraft_bridge.render_manifest_path(path).name, signature=signature)
            state, recipe, _reason = photocraft_bridge.recipe_state(path)
            if state == photocraft_bridge.RECIPE_INVALID:
                raise RuntimeError("the previous photo's Camera Raw settings cannot be read")
            if recipe is not None:
                spec["recipe"] = recipe
            if sidecar.exists():
                spec["continueFrom"] = sidecar.name
        opened = proc.control.app_open(relative, replace=True, **({"camera_raw": spec} if spec is not None else {}))
        binding = {"path": sidecar.name, "preview": photocraft_bridge.rendered_preview_path(path).name,
                   "display": photocraft_bridge.display_render_path(path).name}
        if signature:
            binding.update(manifest=photocraft_bridge.render_manifest_path(path).name, source=signature)
        proc.control.call("app.bind", binding)
        if getattr(proc, "camera_raw_first", False) and not (isinstance(opened, dict) and (opened.get("cameraRaw") or {}).get("dialog")):
            proc.control.call("ui.cameraRaw", {"open": True})

    # -- a set-aside recipe ---------------------------------------------------------------------------------------------------
    @Slot(str)
    def recoverRecipe(self, path: str) -> None:
        """"Try to recover the previous edit": put back what can still be read of the photo's set-aside Camera Raw settings
        (`photocraft_bridge.recover_quarantined_recipe`). When the photo is the one open in the editor it is opened again
        with the recovered settings, in place of what the editor shows; `recipeRecovery` reports the outcome either way."""
        if not path or self._memory:
            return
        if self._state in (STARTING, CLOSING):
            self.recipeRecovery.emit(path, False, "The editor is busy. Try again when it has finished.")
            return
        reopen = self._state == EDITING and path == self._path and self._proc is not None
        if reopen:
            self._generation += 1
            self._switching = True
            self._set(STARTING, "")
            self.stateChanged.emit()
        threading.Thread(target=self._recover_worker, args=(self._generation, path, reopen), name="editor-recover", daemon=True).start()

    def _recover_worker(self, generation: int, path: str, reopen: bool) -> None:
        with self._lock:
            try:
                from .. import photocraft_bridge

                result = photocraft_bridge.recover_quarantined_recipe(path)
                recovered, message = result.recovered, result.message
            except Exception as error:
                LOG.warning("Could not recover the set-aside recipe of %s: %s", path, error, exc_info=True)
                recovered, message = False, "The previous edit could not be recovered. " + (str(error) or error.__class__.__name__)
            self.recipeRecovery.emit(path, recovered, message)
            if not reopen:
                return
            if recovered:
                self._open_locked(generation, path, False, False)
            else:
                self._done.emit("switch-failed", None, "")     # back to the photo as it is open; the outcome was already reported

    # -- the editor is up: put its window in ours ---------------------------------------------------------------------------
    @Slot(object)
    def setWindow(self, window) -> None:
        """The shell's main window: the native window the editor is placed inside."""
        try:
            self._parent_hwnd = int(window.winId())
        except Exception:
            self._parent_hwnd = 0
        if sys.platform == "win32" and not self._memory and isinstance(self._mover, WindowMover) and self._space_shortcut is None:
            from .editor_keys import EditorSpaceShortcut

            self._space_shortcut = EditorSpaceShortcut(
                lambda: self._proc.hwnd if self._proc is not None and self._state == EDITING else 0, self)
            self._space_shortcut.pressed.connect(self.close, Qt.ConnectionType.QueuedConnection)
            self._focus_timer = QTimer(self)
            self._focus_timer.setInterval(60)
            self._focus_timer.timeout.connect(self._follow_clicks)
            self._focus_timer.start()
            try:
                self._space_shortcut.start()
            except OSError:
                LOG.exception("Could not register the hosted editor Space shortcut")

    def _focus(self, hwnd: int) -> None:
        focus = getattr(self._mover, "focus", None)
        if focus is not None:
            focus(hwnd)

    def _follow_clicks(self) -> None:
        """A click inside the editor hands it the keyboard (Windows does not, for a window of another process placed inside ours)."""
        proc, wants = self._proc, getattr(self._mover, "wants_focus", None)
        if proc is not None and self._state == EDITING and not proc.is_running():
            # The editor was closed from its own window (its native exit button): back to the grid rather than an empty stage.
            LOG.info("The hosted editor exited on its own")
            path, self._path = self._path, ""
            self._proc = None
            self._wanted = None
            self._shown_path = ""
            self._told = set()
            self.pathChanged.emit()
            self._set(IDLE)
            self.closed.emit(path)
            return
        if proc is not None and wants is not None and self._state == EDITING and self._parent_hwnd and not self._presentation_blocked:
            try:
                if wants(proc.hwnd, self._parent_hwnd):
                    self._focus(proc.hwnd)
            except Exception:
                LOG.debug("editor focus follow", exc_info=True)

    @Slot(int, int, int, int)
    def setGeometry(self, x: int, y: int, width: int, height: int) -> None:
        """Where the editor's window goes, in device pixels of the shell's window (QML's stage reports it as it moves and resizes)."""
        self._geometry = (x, y, width, height)
        proc = self._proc
        if proc is not None and self._state == EDITING:
            self._place(proc)

    def _place(self, proc) -> None:
        try:
            self._mover.place(proc.hwnd, *self._geometry)
        except Exception:
            LOG.debug("could not place the editor window", exc_info=True)

    @Slot(bool)
    def setPresentationBlocked(self, blocked: bool) -> None:
        """Let a shell confirmation cover the foreign native child without closing its document."""
        self._presentation_blocked = blocked
        if blocked:
            self._hide()
        elif self._proc is not None and self._state == EDITING:
            self._place(self._proc)
            self._mover.show(self._proc.hwnd, True)

    @Slot(str, object, str)
    def _finish(self, what: str, result, error: str) -> None:
        if what == "save-failed":
            # A Save failure must reach the shell, but must not close the editor
            # or change a newer navigation operation's state.
            if result is self._proc:
                self._set(self._state, error)
                self._told = self._told - {self._cause(self._proc.source_path, error)}   # Save was asked for: always say how it went
                self._report(self._proc.source_path, error)
            return
        self._switching = False
        if what == "open":
            self._proc = result
            if self._close_requested:
                self._close_requested = False
                self._wanted = None
                self._shown_path = self._path
                self._set(EDITING, "")
                self.close()
                return
            self._show(result)
            self._shown_path = self._path
            self._forget(self._path)
            self._set(EDITING, "")
            self.opened.emit(self._path)
            self._open_wanted()
        elif what == "open-failed":
            self._wanted = None
            if self._proc is not None and not self._running(self._proc):
                # The editor that was up is gone (it was stopped to keep one photo from being saved as another).
                left, self._proc, self._shown_path = self._shown_path, None, ""
                self._fail(self._path, error)
                if self._path:
                    self._path = ""
                    self.pathChanged.emit()
                if left:
                    self.closed.emit(left)
                return
            self._fail(self._path, error)
        elif what == "closed":
            self._wanted = None
            self._told = set()
            self._hide()
            path, self._path = self._path, ""
            self._shown_path = ""
            self.pathChanged.emit()
            self._set(IDLE, error)
            self.closed.emit(path)
        elif what == "close-failed":
            self._set(EDITING, error)
            if self.isSignalConnected(QMetaMethod.fromSignal(self.closeFailed)):
                self.closeFailed.emit(error)
            else:
                self.failed.emit(error)
        elif what == "switch-failed":
            # The switch is over and the editor shows one definite photo: the previous one, or (when the failure came
            # after the new photo was bound) the new one. The host's path and the selection follow the editor.
            self._close_requested = False
            landed = self._proc.source_path
            self._path = landed
            self.pathChanged.emit()
            self._set(EDITING, error)
            if landed != self._shown_path:
                self._show(self._proc)
                self._shown_path = landed
                self.opened.emit(landed)
            if error:
                self._report(result if isinstance(result, str) and result else landed, error)
            self._open_wanted(failed_for=result if isinstance(result, str) else "")

    def _show(self, proc) -> None:
        try:
            if self._parent_hwnd and proc.attached_parent != self._parent_hwnd:
                self._mover.attach(proc.hwnd, self._parent_hwnd)
                proc.attached_parent = self._parent_hwnd
            self._place(proc)
            self._mover.show(proc.hwnd, True)
            self._focus(proc.hwnd)
        except Exception:
            LOG.warning("could not put the editor window in the shell", exc_info=True)

    def _open_wanted(self, failed_for: str = "") -> None:
        """Start the open that was asked for while the last one ran, unless it is where the editor already is, or the
        last one failed because the photo being left could not be saved (the next would fail the same way)."""
        wanted, self._wanted = self._wanted, None
        if wanted is None or self._state != EDITING or wanted[0] == self._path:
            return
        if failed_for and failed_for == self._path:
            return
        self._open_path(wanted[0], full_editor=wanted[1])

    def _fail(self, path: str, message: str) -> None:
        self._close_requested = False
        self._switching = False
        self._wanted = None
        self._set(FAILED, message)
        self.failed.emit(message)
        self._set(IDLE)

    # -- telling the person about a failure, once -------------------------------------------------------------------------------
    @staticmethod
    def _cause(path: str, message: str) -> tuple[str, str]:
        # Without the numbers: the same failure names a new temporary file or generation every time.
        return path, _DIGITS.sub("#", message)

    def _report(self, path: str, message: str) -> None:
        """Send `failed` unless this photo already failed for this cause and nothing has succeeded for it since. The
        message stays readable as `error` either way. A new cause, or the same one after a success, is reported."""
        key = self._cause(path, message)
        if key in self._told:
            LOG.info("Not repeating the failure notice for %s: %s", path, message)
            return
        self._told = self._told | {key}
        self.failed.emit(message)

    def _forget(self, path: str) -> None:
        """Something worked for this photo: a later failure is news again. (Also called from the save workers: the set
        is replaced, never changed in place.)"""
        self._told = {key for key in self._told if key[0] != path}

    # -- putting the editor away ---------------------------------------------------------------------------------------------
    @Slot()
    def save(self) -> None:
        """Save the open photo (a sidecar project and its render, as the Widgets host's native Save does)."""
        proc = self._proc
        if proc is None or self._state != EDITING:
            return
        threading.Thread(target=self._save_worker, args=(proc, proc.source_path, self._generation), name="editor-save", daemon=True).start()

    def _save_worker(self, proc, path: str, generation: int) -> None:
        with self._lock:
            # A queued save belongs to the document visible when Save was pressed.
            # Navigation settles that document itself if it wins the lock first.
            if proc is not self._proc or generation != self._generation or proc.source_path != path:
                return
            try:
                self._settle(proc)
                self._saved(path)
            except Exception as error:
                LOG.warning("PhotoCraft did not save: %s", error, exc_info=True)
                self._done.emit("save-failed", proc,
                    "Could not confirm the save. The editor is still open; check the photo before retrying. " + str(error))

    def _saved(self, path: str) -> None:
        """The photo's edits were saved (called on the worker that saved them): clear away the pictures of its earlier saves,
        which nothing names any more, and tell the grid."""
        if path:
            try:
                from .. import photocraft_bridge

                photocraft_bridge.prune_render_generations(path)
            except Exception:
                LOG.debug("could not clear old renders of %s", path, exc_info=True)
            self._sweep_save_temps(None, self._proc, beside=path)
            self._forget(path)
        self.editSaved.emit(path)

    @staticmethod
    def _sweep_save_temps(folder, proc, beside: str = "") -> None:
        """Clear away `*.pcsave.tmp` files that failed saves left in the edit folder (as stale token files are cleared):
        when an editor starts on the folder and after each completed save. Only old ones, never one a running editor
        could still be writing (`photocraft_bridge.sweep_stale_save_temps`)."""
        try:
            from .. import edit_storage, photocraft_bridge

            if folder is None:
                folder = edit_storage.edit_root_for(Path(beside).parent)
            pid = getattr(getattr(proc, "process", None), "pid", None)
            photocraft_bridge.sweep_stale_save_temps(folder, live_pids=(pid,) if isinstance(pid, int) else ())
        except Exception:
            LOG.debug("could not clear stale save temporaries", exc_info=True)

    @Slot()
    def close(self) -> None:
        """Settle/save the edit before returning to the grid; keep the process for reuse."""
        if self._memory:
            path = self._path
            self._path = ""
            self.pathChanged.emit()
            self._set(IDLE)
            self.closed.emit(path)
            return
        if self._state == STARTING:
            self._close_requested = True
            return
        proc = self._proc
        if proc is None or self._state not in (EDITING, STARTING):
            return
        self._set(CLOSING)
        threading.Thread(target=self._close_worker, args=(proc,), name="editor-close", daemon=True).start()

    @Slot()
    def closeWithoutSaving(self) -> None:
        """Leave the editor and drop what was changed in the open photo since its last save. The way out when the save on close
        fails (a share that went away): without it the editor could not be left at all. The process is kept for the next photo,
        which replaces the unsaved document."""
        if self._memory:
            self.close()
            return
        if self._proc is None or self._state != EDITING:
            return
        self._close_requested = False
        self._switching = False
        self._wanted = None
        self._shown_path = ""
        self._told = set()
        self._hide()
        path, self._path = self._path, ""
        self.pathChanged.emit()
        self._set(IDLE, "")
        self.closed.emit(path)

    def _close_worker(self, proc) -> None:
        with self._lock:
            self._close_locked(proc)

    def _close_locked(self, proc) -> None:
        message = ""
        try:
            self._settle(proc)
            self._saved(proc.source_path)
        except Exception as error:
            LOG.warning("PhotoCraft did not save on close: %s", error, exc_info=True)
            self._done.emit("close-failed", None, "The edit could not be saved. " + str(error))
            return
        self._done.emit("closed", None, message)

    @staticmethod
    def _settle(proc) -> None:
        state = proc.control.call("ui.cameraRaw", {"done": True})
        if state.get("open"):
            raise RuntimeError("Camera Raw is still finishing this photo. Try again.")
        state = proc.control.document_state()
        if state is not None and state[1] is not False:
            if getattr(proc.control,"render_manifest_supported",False) is True:
                from .. import photocraft_bridge as bridge
                source=proc.source_path
                params={"path":bridge.sidecar_pcraft_path(source).name,
                        "preview":bridge.rendered_preview_path(source).name,
                        "display":bridge.display_render_path(source).name,
                        "manifest":bridge.render_manifest_path(source).name,
                        "source":bridge.file_signature(source),"wait":True}
                proc.control.call("app.stash",params)
            else:
                proc.control.app_save()

    def _hide(self) -> None:
        proc = self._proc
        if proc is not None:
            try:
                self._mover.show(proc.hwnd, False)
            except Exception:
                pass

    @staticmethod
    def _stop(proc) -> None:
        try:
            proc.shutdown()
        except Exception:
            LOG.debug("editor shutdown", exc_info=True)

    @Slot()
    def shutdown(self) -> None:
        self._host_job_stop.set()
        if self._filmstrip is not None:
            self._filmstrip.stop()
        if self._space_shortcut is not None:
            self._space_shortcut.stop()
        proc, self._proc = self._proc, None
        if proc is not None:
            if not self._memory and self._state in (EDITING, CLOSING) and self._running(proc):
                self._settle_within(proc, SHUTDOWN_SETTLE_S)
            self._stop(proc)

    @staticmethod
    def _running(proc) -> bool:
        try:
            return bool(proc.is_running())
        except Exception:
            return False

    def _settle_within(self, proc, seconds: float) -> bool:
        """Save the open photo as closing the editor does, waiting at most `seconds`. True when it was saved in time."""
        outcome: list[str] = []

        def work() -> None:
            try:
                with self._lock:
                    self._settle(proc)
                outcome.append("")
            except Exception as error:
                outcome.append(str(error) or error.__class__.__name__)

        worker = threading.Thread(target=work, name="editor-quit-save", daemon=True)
        worker.start()
        worker.join(seconds)
        if not outcome:
            LOG.warning("The editor did not save %s within %.0f s of quitting; it was closed without it", getattr(proc, "source_path", ""), seconds)
            return False
        if outcome[0]:
            LOG.warning("The editor could not save %s while quitting: %s", getattr(proc, "source_path", ""), outcome[0])
            return False
        return True
