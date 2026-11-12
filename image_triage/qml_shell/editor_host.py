"""The hosted PhotoCraft editor for the QML shell (`editor` in QML): starting it, opening a photo in it, saving, closing and putting its native
window inside the shell's window.

This is the groundwork: the process lifecycle and the window placement over the same `photocraft_bridge` the Widgets app uses. The rest of the
Widgets host's behaviour (save before switching, stash and restore, the persistent filmstrip and theme sync) is listed in
docs/qml_unwired.md under "Editor". The launcher and the window mover are parameters so that tests run without PhotoCraft or Windows.
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Property, QMetaMethod, QObject, Qt, QTimer, Signal, Slot

LOG = logging.getLogger(__name__)

IDLE, STARTING, EDITING, CLOSING, FAILED = "idle", "starting", "editing", "closing", "failed"

# How long quitting the app waits for the editor to save the open photo. The editor is closed after that whether or not it
# answered: a save that hangs (an unreachable share) must not keep the app from closing.
SHUTDOWN_SETTLE_S = 5.0


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
    editSaved = Signal(str)
    _done = Signal(str, object, str)        # worker -> main thread: (what finished, its result, an error message)

    def __init__(self, *, memory: bool = False, launcher=None, detector=None, mover=None, parent=None) -> None:
        super().__init__(parent)
        self._memory = memory
        self._launcher = launcher
        self._detector = detector
        self._mover = mover if mover is not None else WindowMover()
        self._state = IDLE
        self._path = ""
        self._error = ""
        self._proc = None
        self._lock = threading.Lock()
        self._geometry = (0, 0, 0, 0)
        self._parent_hwnd = 0
        self._generation = 0
        self._close_requested = False
        self._switching = False
        self._space_shortcut = None
        self._filmstrip = None
        self._edit_probe = QTimer(self)
        self._edit_probe.setInterval(1000)
        self._edit_probe.timeout.connect(lambda: self.editSaved.emit(self._path))
        self._done.connect(self._finish, Qt.ConnectionType.QueuedConnection)

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
            else:
                self._edit_probe.stop()
            self.stateChanged.emit()

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
        if not path or self._state in (STARTING, CLOSING):
            return
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
        proc = None
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
                self._settle(proc)
                self._saved(getattr(proc, "source_path", ""))
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
                if recipe is not None:
                    spec["recipe"] = recipe
                elif photocraft_bridge.recipe_is_newer(path):
                    # Opening it without its settings would have the editor write this version's recipe over them.
                    raise RuntimeError("This photo's Camera Raw settings were saved by a newer version of Image Triage. Update Image Triage to edit it.")
                if sidecar.exists():
                    spec["continueFrom"] = sidecar.name
            previous = getattr(proc, "source_path", "")
            opened = proc.control.app_open(relative, replace=True, **({"camera_raw": spec} if spec is not None else {}))
            if previous:
                self.editSaved.emit(previous)
            binding = {"path": sidecar.name, "preview": photocraft_bridge.rendered_preview_path(path).name,
                       "display": photocraft_bridge.display_render_path(path).name}
            if signature:
                binding.update(manifest=photocraft_bridge.render_manifest_path(path).name, source=signature)
            proc.control.call("app.bind", binding)
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
            proc.source_path = path
            self._done.emit("open", proc, "")
        except Exception as error:               # a failed launch or open is shown to the person, never raised into the event loop
            if proc is not None and proc is not self._proc:
                self._stop(proc)
            LOG.warning("PhotoCraft did not open %s: %s", path, error, exc_info=True)
            what = "switch-failed" if proc is self._proc and proc is not None and proc.is_running() else "open-failed"
            self._done.emit(what, None, str(error) or error.__class__.__name__)

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
            self.pathChanged.emit()
            self._set(IDLE)
            self.closed.emit(path)
            return
        if proc is not None and wants is not None and self._state == EDITING and self._parent_hwnd:
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

    @Slot(str, object, str)
    def _finish(self, what: str, result, error: str) -> None:
        self._switching = False
        if what == "open":
            self._proc = result
            if self._close_requested:
                self._close_requested = False
                self._set(EDITING, "")
                self.close()
                return
            try:
                if self._parent_hwnd and result.attached_parent != self._parent_hwnd:
                    self._mover.attach(result.hwnd, self._parent_hwnd)
                    result.attached_parent = self._parent_hwnd
                self._place(result)
                self._mover.show(result.hwnd, True)
                self._focus(result.hwnd)
            except Exception:
                LOG.warning("could not put the editor window in the shell", exc_info=True)
            self._set(EDITING, "")
            self.opened.emit(self._path)
        elif what == "open-failed":
            self._fail(self._path, error)
        elif what == "closed":
            self._hide()
            path, self._path = self._path, ""
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
            self._close_requested = False
            self._path = self._proc.source_path
            self.pathChanged.emit()
            self._set(EDITING, error)
            self.failed.emit(error)

    def _fail(self, path: str, message: str) -> None:
        self._close_requested = False
        self._switching = False
        self._set(FAILED, message)
        self.failed.emit(message)
        self._set(IDLE)

    # -- putting the editor away ---------------------------------------------------------------------------------------------
    @Slot()
    def save(self) -> None:
        """Save the open photo (a sidecar project and its render, as the Widgets host's native Save does)."""
        proc = self._proc
        if proc is None or self._state != EDITING:
            return
        threading.Thread(target=self._save_worker, args=(proc,), name="editor-save", daemon=True).start()

    def _save_worker(self, proc) -> None:
        path = proc.source_path
        try:
            self._settle(proc)
            self._saved(path)
        except Exception as error:
            LOG.warning("PhotoCraft did not save: %s", error, exc_info=True)

    def _saved(self, path: str) -> None:
        """The photo's edits were saved (called on the worker that saved them): clear away the pictures of its earlier saves,
        which nothing names any more, and tell the grid."""
        if path:
            try:
                from .. import photocraft_bridge

                photocraft_bridge.prune_render_generations(path)
            except Exception:
                LOG.debug("could not clear old renders of %s", path, exc_info=True)
        self.editSaved.emit(path)

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
        self._hide()
        path, self._path = self._path, ""
        self.pathChanged.emit()
        self._set(IDLE, "")
        self.closed.emit(path)

    def _close_worker(self, proc) -> None:
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
