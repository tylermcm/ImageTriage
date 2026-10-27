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

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot

LOG = logging.getLogger(__name__)

IDLE, STARTING, EDITING, CLOSING, FAILED = "idle", "starting", "editing", "closing", "failed"


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
        self._space_shortcut = None
        self._filmstrip = None
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

    def _set(self, state: str, error: str | None = None) -> None:
        if error is not None and error != self._error:
            self._error = error
            self.errorChanged.emit()
        if state != self._state:
            self._state = state
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
        self._set(STARTING, "")
        threading.Thread(target=self._open_worker, args=(self._generation, path, switching), name="editor-open", daemon=True).start()

    def _open_worker(self, generation: int, path: str, switching: bool = False) -> None:
        proc = None
        try:
            from .. import edit_storage, photocraft_bridge

            directory = str(Path(path).parent)
            proc = self._proc
            if proc is not None and switching and proc.is_running():
                self._settle(proc)
            if proc is not None and (not proc.is_running() or proc.path_within_root(path) is None):
                self._stop(proc)               # the editor can only read the folder it was started on
                proc = None
            if proc is None:
                launch = self._launcher or photocraft_bridge.launch_photocraft
                write_root = edit_storage.ensure_edit_root(directory)
                proc = launch(None, read_root=directory, write_root=str(write_root), idle=True, extra_args=("--raw-workspace",))
            sidecar = photocraft_bridge.sidecar_pcraft_path(path)
            is_raw = Path(path).suffix.lower() in photocraft_bridge.RAW_SUFFIXES
            open_path = str(sidecar) if sidecar.exists() and not is_raw else path
            relative = proc.path_within_root(open_path)
            if relative is None:
                raise RuntimeError("That photo is outside the folder PhotoCraft can read.")
            if proc.idle_priority:
                from .. import photocraft_bridge

                photocraft_bridge.raise_priority(proc)
            spec = None
            if is_raw:
                spec = {"recipePath": photocraft_bridge.recipe_path(path).name,
                        "displayPath": photocraft_bridge.display_render_path(path).name}
                recipe = photocraft_bridge.load_recipe(path)
                if recipe is not None:
                    spec["recipe"] = recipe
                if sidecar.exists():
                    spec["continueFrom"] = sidecar.name
            proc.control.app_open(relative, replace=True, **({"camera_raw": spec} if spec is not None else {}))
            proc.control.call("app.bind", {"path": sidecar.name, "preview": photocraft_bridge.rendered_preview_path(path).name,
                                          "display": photocraft_bridge.display_render_path(path).name})
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
            self.failed.emit(error)
        elif what == "switch-failed":
            self._close_requested = False
            self._path = self._proc.source_path
            self.pathChanged.emit()
            self._set(EDITING, error)
            self.failed.emit(error)

    def _fail(self, path: str, message: str) -> None:
        self._close_requested = False
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
        try:
            proc.control.app_save()
        except Exception as error:
            LOG.warning("PhotoCraft did not save: %s", error, exc_info=True)

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

    def _close_worker(self, proc) -> None:
        message = ""
        try:
            self._settle(proc)
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
            self._stop(proc)
