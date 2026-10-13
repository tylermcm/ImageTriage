"""Launches a PhotoCraft process, drives it over its JSON-lines control channel
(docs/control-protocol.md in the PhotoCraft repo), and embeds its native window
as a Win32 child so it can sit inside the popout preview's editor pane.

Windows-only (uses ctypes/user32 the same way image_triage.window does for its
custom frame), matching the "Edit in PhotoCraft" feature wired from
preview.py/preview_controller.py.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .edit_storage import edit_root_for
from .formats import RAW_SUFFIXES
from .shell_actions import companion_photocraft_executables, detect_photocraft_executable

# Formats PhotoCraft can open directly (docs/README "Formats" section). Anything
# else must be decoded first, except RAW, which the editor develops itself.
NATIVE_SUFFIXES = {
    ".psd", ".psb", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif",
    ".bmp", ".tga", ".ico", ".qoi", ".pnm", ".exr", ".hdr", ".avif", ".heic", ".pcraft",
}

_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020


class PhotoCraftError(RuntimeError):
    """A PhotoCraft launch, control-channel, or embedding failure."""


class PhotoCraftCompatibilityError(PhotoCraftError):
    """The selected binary cannot implement the hosted handoff protocol."""


class PhotoCraftSuperseded(PhotoCraftError):
    """An open was abandoned because the selection moved on before it started."""


def path_needs_conversion(path: str) -> bool:
    return Path(path).suffix.lower() not in NATIVE_SUFFIXES | RAW_SUFFIXES


def sidecar_pcraft_path(image_path: str) -> Path:
    """Where a photo's non-destructive PhotoCraft project lives: a ``.pcraft``
    file in the same hidden per-folder edit root ``edit_storage`` uses for the
    built-in editor's sidecars, so both conventions stay out of the user's
    photo folders and travel together with the rest of a folder's edit state.
    """
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".pcraft")


def rendered_preview_path(image_path: str) -> Path:
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".photocraft.png")


RECIPE_VERSION = 1


def file_signature(path: str) -> str | None:
    """``"<bytes>:<mtime_ns>"`` for the file at ``path``, the identity the editor checks a prepared raw against."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def recipe_path(image_path: str) -> Path:
    """Where a photo's Camera Raw recipe lives: beside its other edit data, keyed by the photo's name."""
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".cameraraw.json")


def load_recipe(image_path: str) -> dict | None:
    """The photo's stored recipe, or ``None`` when it has none or the file is unusable.

    A damaged file is set aside (``.bad``) rather than deleted, so a bad write can never make a photo
    unopenable and never silently throws a user's adjustments away.
    """
    target = recipe_path(image_path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as error:
        logging.getLogger(__name__).warning("Could not read the Camera Raw recipe %s: %s", target, error)
        return None
    try:
        recipe = json.loads(text)
        if isinstance(recipe, dict) and recipe.get("version") == RECIPE_VERSION and isinstance(recipe.get("cameraRaw"), dict):
            return recipe
    except ValueError:
        pass
    quarantine_recipe(image_path)
    return None


def quarantine_recipe(image_path: str) -> None:
    target = recipe_path(image_path)
    try:
        os.replace(target, target.with_name(target.name + ".bad"))
        logging.getLogger(__name__).warning("Set aside an unusable Camera Raw recipe: %s", target)
    except OSError:
        pass


def display_render_path(image_path: str) -> Path:
    """The screen-sized JPEG of the photo's edits that autostash writes beside the full-size render."""
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".photocraft.display.jpg")


# Stash writes the display JPEG first and the PNG when the whole save lands, which takes seconds and
# up to ~25 s under disk contention. A display JPEG much older than the PNG predates it.
DISPLAY_STALE_AFTER_S = 120.0


def saved_render_path(image_path: str) -> Path | None:
    """The cheapest picture of the photo's saved PhotoCraft edits, or ``None`` when it has none.

    The screen-sized display JPEG decodes in milliseconds; the full-size PNG (hundreds of MB for a
    45 MP photo) does not, so it is only the fallback for edits saved before display JPEGs existed.
    """
    png = rendered_preview_path(image_path)
    display = display_render_path(image_path)
    try:
        shown = display.stat()
    except OSError:
        shown = None
    try:
        full = png.stat()
    except OSError:
        full = None
    if shown is not None and shown.st_size > 0:
        if full is None or shown.st_mtime >= full.st_mtime - DISPLAY_STALE_AFTER_S:
            return display
    return png if full is not None else None


# A PNG written within this long before the display JPEG is taken as part of the same save (the JPEG is written first, the PNG when the
# whole save lands); one older than that is from an earlier save.
FULL_RENDER_TOLERANCE_S = 2.0


def full_render_path(image_path: str) -> Path | None:
    """The largest picture of the photo's saved PhotoCraft edits that is not out of date: the full-size PNG, unless a later save wrote only
    the screen-sized JPEG (autosaves while the editor is up do; the PNG follows when the photo is left), else that JPEG; ``None`` when the
    photo has neither. For readers that want full size (exports, hand-offs, the edited version beside the original)."""
    png = rendered_preview_path(image_path)
    display = display_render_path(image_path)
    try:
        shown = display.stat()
    except OSError:
        shown = None
    try:
        full = png.stat()
    except OSError:
        full = None
    shown_is_usable = shown is not None and shown.st_size > 0
    if full is not None and full.st_size > 0 and (not shown_is_usable or full.st_mtime >= shown.st_mtime - FULL_RENDER_TOLERANCE_S):
        return png
    return display if shown_is_usable else None


def render_is_behind(image_path: str) -> bool:
    """Whether the photo has a saved project whose full-size render is missing or older than it: a save that left the render out (an
    autosave does, and so does closing the popout or quitting) has been made since the render was. The project is written before the
    render in a full save, so a render within ``FULL_RENDER_TOLERANCE_S`` of the project (or newer) belongs to it."""
    try:
        project = sidecar_pcraft_path(image_path).stat().st_mtime
    except OSError:
        return False  # no project: a photo with only a recipe has no full-size render, and nothing is behind
    try:
        render = rendered_preview_path(image_path).stat().st_mtime
    except OSError:
        return True
    return render < project - FULL_RENDER_TOLERANCE_S


def materialize_for_photocraft(path: str, cache_dir: Path) -> str:
    """Decode an unsupported non-RAW file to a TIFF PhotoCraft can open directly.

    Never points PhotoCraft at the original source file for formats it can't
    read natively; the converted copy lives in ``cache_dir``.
    """
    from PySide6.QtCore import QSize

    from .imaging import load_image_for_display

    # Larger than any current camera sensor, so imaging.py's quality-mode
    # selection always picks the full-resolution decode, not a half-size one.
    image, error = load_image_for_display(path, QSize(16384, 16384), prefer_embedded=False)
    if image.isNull():
        raise PhotoCraftError(f"Could not decode {path!r} for PhotoCraft: {error}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / (Path(path).name + "__photocraft_source.tiff")
    if not image.save(str(out_path), "TIFF"):
        raise PhotoCraftError(f"Could not write converted TIFF for {path!r}")
    return str(out_path)


# PhotoCraft's control server closes a connection that has sent nothing for 30 s (``IO_TIMEOUT`` in its automation crate), and its own
# client reconnects after 25 s of quiet. A request sent down a connection the editor has closed fails with "[WinError 10053] An
# established connection was aborted", which is what the first E showed when the editor had been waiting in the background.
IDLE_RECONNECT_S = 20.0


class PhotoCraftControl:
    """Blocking JSON-lines client for PhotoCraft's control channel. A connection that has been quiet for ``IDLE_RECONNECT_S`` is
    replaced by a fresh one (same token, same editor) before the next request, so a wait of any length is harmless."""

    def __init__(self, port: int, token: str, *, host: str = "127.0.0.1", timeout: float = 120.0) -> None:
        self._address = (host, port)
        self._token = token
        self._timeout = timeout
        self._next_id = 1
        self._exchange_lock = threading.RLock()
        self._open(timeout)

    def _open(self, timeout: float) -> None:
        self._sock = socket.create_connection(self._address, timeout=min(timeout, 2.0))
        self._sock.settimeout(timeout)
        self._file = self._sock.makefile("rwb")
        try:
            self._authenticate(self._token)
        except BaseException:
            self.close()
            raise
        self._last_used = time.monotonic()

    def _reconnect_if_quiet(self, timeout: float | None = None) -> None:
        last_used = getattr(self, "_last_used", None)
        if last_used is None or time.monotonic() - last_used <= IDLE_RECONNECT_S:
            return
        try:
            self.close()
        except OSError:
            pass  # the editor closed its end already
        self._open(self._timeout if timeout is None else timeout)

    def _authenticate(self, token: str) -> None:
        reply = self._request("auth", {"token": token})
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft control auth failed: {reply.get('error')}")

    def _request(self, method: str, params: dict | None = None) -> dict:
        # One request and its reply at a time: the connection is used from the UI thread and from workers (prefetch, the
        # viewport query, saves), and two exchanges interleaved on one socket read each other's replies, or half of one.
        lock = self.__dict__.get("_exchange_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_exchange_lock", threading.RLock())
        with lock:
            return self._exchange(method, params)

    def _exchange(self, method: str, params: dict | None) -> dict:
        if method != "auth":
            self._reconnect_if_quiet()
        req_id = self._next_id
        self._next_id += 1
        payload: dict = {"id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._file.write((json.dumps(payload) + "\n").encode("utf-8"))
        self._file.flush()
        reply_line = self._file.readline()
        if not reply_line:
            raise PhotoCraftError("PhotoCraft control channel closed unexpectedly")
        try:
            reply = json.loads(reply_line)
        except ValueError as error:
            raise PhotoCraftError(f"PhotoCraft control sent an unreadable reply to {method!r}: {reply_line[:200]!r}") from error
        if reply.get("id") != req_id:
            raise PhotoCraftError(f"PhotoCraft control reply id mismatch: expected {req_id}, got {reply.get('id')}")
        self._last_used = time.monotonic()
        return reply

    def call(self, method: str, params: dict | None = None) -> dict:
        try:
            reply = self._request(method, params)
        except (OSError, ValueError) as error:
            raise PhotoCraftError(f"PhotoCraft control {method!r} failed: {error}") from error
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft control {method!r} failed: {reply.get('error')}")
        return reply.get("result") or {}

    def app_open(self, path: str, *, replace: bool = False, should_continue=None, camera_raw: dict | None = None) -> dict:
        params = {"path": _automation_path(path)}
        if replace:
            params["replace"] = True
        if camera_raw is not None and Path(path).suffix.lower() in RAW_SUFFIXES and getattr(self, "camera_raw_supported", False):
            # Camera Raw workspace: the editor develops the raw itself and opens it in Camera Raw. A raw it cannot
            # develop from sensor data opens as the camera's embedded preview, with no dialog (``cameraRaw.dialog``
            # is false in the reply).
            params["cameraRaw"] = camera_raw
        # The caller may have moved on while this request waited its turn: do not start an open that would later
        # replace the photo now on screen.
        if should_continue is not None and not should_continue():
            raise PhotoCraftSuperseded(f"{path} is no longer the selected photo")
        return self.call("app.open", params)

    def app_prefetch(self, path: str, signature: str) -> dict:
        """Ask the editor to develop ``path`` for Camera Raw in the background (``signature`` is the file's
        size and modified time as ``file_signature`` gives them): opening it with that signature is then nearly instant."""
        return self.call("app.prefetch", {"path": _automation_path(path), "signature": signature})

    def require_hosted_handoff(self) -> None:
        """Probe validation without opening a document or writing any files."""
        try:
            capabilities = self.call("app.handoff")
        except PhotoCraftError as error:
            if "unknown method" not in str(error):
                raise
            raise PhotoCraftCompatibilityError("This PhotoCraft build lacks persistent hosted document replacement") from error
        if capabilities.get("version", 0) < 2:
            raise PhotoCraftCompatibilityError("This PhotoCraft build lacks persistent hosted document replacement")
        self.camera_raw_supported = capabilities.get("cameraRaw", 0) >= 1
        self.camera_raw_prefetch_supported = capabilities.get("cameraRawPrefetch", 0) >= 1
        self.camera_raw_switch_supported = capabilities.get("cameraRawSwitch", 0) >= 1
        # ``app.stash`` can save the project and the display JPEG without the full-size PNG (``preview`` left out).
        self.stash_without_preview_supported = capabilities.get("stashWithoutPreview", 0) >= 1
        for method, validation in (
            ("app.stash", "stash requires path and preview"),
            ("app.bind", "bind requires an open document, .pcraft path and .png preview"),
        ):
            try:
                self.call(method, {})
            except PhotoCraftError as error:
                if validation in str(error):
                    continue
                if "unknown method" not in str(error):
                    raise
                raise PhotoCraftCompatibilityError(
                    "This PhotoCraft build does not support the hosted editor handoff. "
                    "Use the companion photocraft.exe build or update IMAGE_TRIAGE_PHOTOCRAFT_EXE. "
                    f"Compatibility check: {error}"
                ) from error
            raise PhotoCraftCompatibilityError(f"PhotoCraft returned an unexpected response to the {method} compatibility check")

    def app_save(self, path: str | None = None) -> dict:
        return self.call("app.save", {"path": _automation_path(path)} if path else {})

    def ui_resize(self, width: int, height: int) -> dict:
        return self.call("ui.resize", {"width": width, "height": height})

    def ui_set(self, **fields) -> dict:
        return self.call("ui.set", fields)

    def execute(self, command: str, params: dict | None = None) -> dict:
        return self.call("engine.execute", {"command": command, "params": params or {}})

    def edit_menus(self, *, hide: list[str] | None = None, show: list[str] | None = None) -> dict:
        params: dict = {}
        if hide:
            params["hide"] = hide
        if show:
            params["show"] = show
        return self.execute("edit.menus", params)

    def document_revision(self) -> int | None:
        """The open document's undo/history revision counter: unchanged since
        open means nothing worth saving happened. None means no document is open."""
        # Session inspection also works after the user closes the document tab.
        # It avoids treating an empty editor as a failed save/transport request.
        session = self.execute("session.inspect")
        if "active" not in session:
            raise PhotoCraftError("PhotoCraft session inspection did not report an active document")
        active = session["active"]
        if active is None:
            return None
        for document in session.get("documents", []):
            if document.get("index") == active:
                return int(document["revision"])
        raise PhotoCraftError("PhotoCraft session inspection omitted the active document")

    def close_document(self) -> None:
        """Close the active document. Over the control channel this never
        opens an "unsaved changes?" dialog — unsaved edits are just discarded
        — so callers that want to keep them must app_save first."""
        self.execute("file.close")

    def quit(self) -> None:
        try:
            # Short timeouts: an editor that has stopped answering must not hold up closing. A connection it has dropped is replaced
            # by one with the same short limit.
            self._reconnect_if_quiet(timeout=2.0)
            self._sock.settimeout(2.0)
            self.call("app.quit")
        except (PhotoCraftError, OSError):
            pass

    def close(self) -> None:
        try:
            self._file.close()
        finally:
            self._sock.close()


@dataclass(slots=True)
class PhotoCraftProcess:
    """A launched PhotoCraft subprocess, its control channel, and its HWND."""

    process: subprocess.Popen
    control: PhotoCraftControl
    hwnd: int
    token_file: Path
    read_root: Path
    write_root: Path | None = None
    executable: str = ""
    attached_parent: int | None = None
    launch_timings: dict[str, float] = field(default_factory=dict)
    current_source: str | None = None
    source_path: str = ""
    pending_stashes: dict[str, tuple[int, str]] = field(default_factory=dict)
    stash_errors: dict[str, str] = field(default_factory=dict)
    source_by_sidecar: dict[str, str] = field(default_factory=dict)
    observed_stashes: set[int] = field(default_factory=set)
    # The sidecar the *currently open* document would be saved to, and its
    # document.inspect() revision right after that open — together these say
    # whether switching away should save first (preview_controller.py owns
    # the actual save-before-close-before-open sequence).
    current_sidecar: Path | None = None
    opened_revision: int = 0
    port: int = 0
    token: str = ""
    # A second control connection for work that must not wait behind a long RAW open on
    # ``control`` (previews, cancelling an obsolete open). PhotoCraft serves each
    # connection on its own thread.
    fast: PhotoCraftControl | None = None
    fast_unavailable: bool = False
    preview_source: str | None = None
    # Launched with ``--raw-workspace``: Camera Raw opens by itself on each document, inside the window.
    camera_raw_first: bool = False
    # Started in the background with no photo open, at below-normal priority so that starting it never slows what the user is
    # doing; `raise_priority` puts it back to normal when it is first used.
    idle_priority: bool = False
    # Where the editor's own output goes (see ``_open_editor_log``); None when no file could be made.
    log_path: Path | None = None
    # Sidecars (as ``str``) whose last save left the full-size render out (an autosave while the editor is up writes the project and
    # the screen-sized JPEG only): leaving the photo saves it again, with the render, even if nothing changed since.
    preview_missing: set[str] = field(default_factory=set)
    # When the open photo was last found changed (``time.monotonic()``): the autosave writes the full-size render after a pause in editing.
    last_edit_at: float = 0.0
    # The editor's colours have been fetched once (the filmstrip follows them); the autosave tick keeps them current after that.
    theme_synced: bool = False
    # The open photo has been left (the editor key, or the popout closing): it was settled, which in the Camera Raw workspace
    # closes its dialog. Coming back to the same photo must open it again, not show what is left of it.
    left: bool = False

    def exit_note(self) -> str:
        """Whether the editor is still running or has exited (with what code), and where its own output is: for the log line of a
        failed handoff, which otherwise says only that the connection broke."""
        code = self.process.poll()
        state = "the editor is still running" if code is None else f"the editor has exited with code {code}"
        return f"{state}; its own output is in {self.log_path}" if self.log_path is not None else state
    # A raw is open in its Camera Raw dialog and has not been left yet: once the dialog is over (the user
    # pressed Open or Cancel, or we left the photo) the document's revision is taken as the baseline, so
    # developing the photo is not mistaken for editing it.
    camera_raw_rebaseline: bool = False
    # The raw the editor was last asked to develop ahead of time (path as sent, the host's normalised key for it), and the
    # key of the one it reported ready: opening that photo costs the editor next to nothing, so nothing is waited for.
    prefetch_sent: tuple[str, str] | None = None
    prefetch_ready: str | None = None

    def is_running(self) -> bool:
        return self.process.poll() is None

    def fast_lane(self) -> PhotoCraftControl:
        if self.fast is None:
            fast = PhotoCraftControl(self.port, self.token)
            fast.read_root = self.read_root
            self.fast = fast
        return self.fast

    def cancel_open_jobs(self) -> int:
        """Cancel a RAW (or any) document open that is still running, never a save."""
        fast = self.fast_lane()
        cancelled = 0
        for job in fast.call("jobs.list").get("jobs", []):
            if job.get("command") == "app.open" and job.get("state") == "running":
                try:
                    fast.call("jobs.cancel", {"job": int(job["id"])})
                    cancelled += 1
                except PhotoCraftError:
                    pass  # it finished between the list and the cancel
        return cancelled

    def path_within_root(self, path: str) -> str | None:
        """``path`` relative to this process's automation read root, or ``None``
        if it falls outside it (app.open/app.save refuse paths outside the root)."""
        try:
            return Path(path).resolve().relative_to(self.read_root.resolve()).as_posix()
        except ValueError:
            return None

    def path_within_write_root(self, path: str) -> str | None:
        try:
            return Path(path).resolve().relative_to((self.write_root or self.read_root).resolve()).as_posix()
        except ValueError:
            return None

    def shutdown(self, *, timeout: float = 3.0) -> None:
        """Ask PhotoCraft to quit over the control channel, then fall back to
        terminate()/kill() if it doesn't exit in time."""
        if self.fast is not None:
            try:
                self.fast.close()
            except OSError:
                pass
            self.fast = None
        try:
            if self.is_running():
                self.control.quit()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=timeout)
        finally:
            try:
                self.control.close()
            except OSError:
                pass
            try:
                self.token_file.unlink(missing_ok=True)
            except OSError:
                pass


def _automation_path(path: str) -> str:
    """PhotoCraft's control channel refuses backslashes in automation paths."""
    return path.replace("\\", "/")


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _connect_with_retry(port: int, token: str, process: subprocess.Popen, *, timeout: float = 20.0) -> PhotoCraftControl:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PhotoCraftError(f"PhotoCraft exited before its control channel opened (code {process.returncode})")
        try:
            return PhotoCraftControl(port, token)
        except OSError as exc:
            last_error = exc
            time.sleep(0.2)
    raise PhotoCraftError(f"Timed out connecting to PhotoCraft's control channel: {last_error}")


def _find_window_for_pid(pid: int, *, timeout: float = 10.0) -> int | None:
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    def _callback(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and (user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) > 0):
            found.append(int(hwnd))
            return False
        return True

    callback = enum_proc_type(_callback)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not found:
        user32.EnumWindows(callback, 0)
        if found:
            break
        time.sleep(0.1)
    return found[0] if found else None


def launch_photocraft(
    initial_path: str | None,
    *,
    read_root: str,
    write_root: str,
    executable: str | None = None,
    on_window=None,
    extra_args: tuple[str, ...] = (),
    camera_raw: dict | None = None,
    idle: bool = False,
) -> PhotoCraftProcess:
    """Starts PhotoCraft rooted at ``read_root`` / ``write_root`` and opens ``initial_path`` in it. Without a path (``idle``
    start-ups) nothing is opened: the editor waits, hidden, at below-normal priority, for the first photo."""
    exe = executable or detect_photocraft_executable()
    if not exe:
        raise PhotoCraftError("PhotoCraft executable not found")

    # A launcher/dev restart may inherit an override for an older release.
    # Honor compatible overrides, but recover from protocol incompatibility
    # before any document has opened. Each failed attempt cleans up below.
    candidates = list(dict.fromkeys([exe, *companion_photocraft_executables()]))
    last_error = None
    for candidate in candidates:
        try:
            proc = _launch_photocraft_binary(
                initial_path, read_root=read_root, write_root=write_root, executable=candidate, on_window=on_window,
                extra_args=extra_args, camera_raw=camera_raw, idle=idle,
            )
            proc.executable = candidate
            logging.getLogger(__name__).info("Hosted PhotoCraft executable: %s", candidate)
            return proc
        except PhotoCraftCompatibilityError as error:
            last_error = error
            logging.getLogger(__name__).warning("Rejected incompatible PhotoCraft executable %s: %s", candidate, error)
    raise PhotoCraftCompatibilityError(f"No compatible PhotoCraft build found. {last_error}")


EDITOR_LOGS_KEPT = 5


def _open_editor_log():
    """A file for the editor's own output (the message and backtrace of a panic, which nothing else records), beside Image Triage's other
    logs, with the last ``EDITOR_LOGS_KEPT`` starts kept. Returns (file object or ``subprocess.DEVNULL``, its path or None)."""
    try:
        from .perf import performance_log_dir

        directory = performance_log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        older = sorted(directory.glob("photocraft_editor_*.log"), key=lambda path: path.stat().st_mtime)
        for stale in older[: max(0, len(older) - (EDITOR_LOGS_KEPT - 1))]:
            try:
                stale.unlink()
            except OSError:
                pass
        path = directory / f"photocraft_editor_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{secrets.token_hex(2)}.log"
        return open(path, "wb"), path
    except OSError:
        return subprocess.DEVNULL, None


def _launch_photocraft_binary(
    initial_path: str | None, *, read_root: str, write_root: str, executable: str, on_window=None,
    extra_args: tuple[str, ...] = (),
    camera_raw: dict | None = None,
    idle: bool = False,
) -> PhotoCraftProcess:
    exe = executable
    started = time.perf_counter()
    timings = {}

    port = _free_tcp_port()
    token = secrets.token_hex(32)
    fd, token_path_str = tempfile.mkstemp(prefix="photocraft_token_", suffix=".txt")
    token_file = Path(token_path_str)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)

    args = [
        exe,
        "--hosted",
        "--control", str(port),
        "--control-token-file", str(token_file),
        "--automation-read-root", read_root,
        "--automation-write-root", write_root,
        *extra_args,
    ]
    process = None
    control = None
    env = dict(os.environ)
    if os.name == "nt":
        # PhotoCraft's automatic backend can pick Vulkan, where game-overlay layers
        # (Epic, Galaxy, ReShade) leave the embedded canvas blank; DX12 renders correctly.
        env.setdefault("WGPU_BACKEND", "dx12")
    env.setdefault("RUST_BACKTRACE", "1")  # only a panic prints it
    log_file, log_path = _open_editor_log()
    try:
        try:
            process = subprocess.Popen(
                args,
                stdout=log_file,
                stderr=subprocess.STDOUT if log_path is not None else subprocess.DEVNULL,
                env=env,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | (_BELOW_NORMAL_PRIORITY_CLASS if idle else 0)) if os.name == "nt" else 0,
            )
        finally:
            if log_path is not None:
                log_file.close()  # the editor has its own copy of the handle
        timings["spawn_ms"] = (time.perf_counter() - started) * 1000
        tie_to_this_process(process)
        control = _connect_with_retry(port, token, process)
        timings["control_ready_ms"] = (time.perf_counter() - started) * 1000
        control.require_hosted_handoff()
        timings["capabilities_ready_ms"] = (time.perf_counter() - started) * 1000
        hwnd = _find_window_for_pid(process.pid)
        if hwnd is None:
            raise PhotoCraftError("Could not find PhotoCraft's window")
        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow(hwnd, 0)  # SW_HIDE until attached to the Qt host.
        # Respect PhotoCraft's persisted theme; the filmstrip follows its colors.
        timings["shell_configured_ms"] = (time.perf_counter() - started) * 1000
        proc = PhotoCraftProcess(process=process, control=control, hwnd=hwnd, token_file=token_file,
                                read_root=Path(read_root), write_root=Path(write_root), launch_timings=timings,
                                port=port, token=token, camera_raw_first="--raw-workspace" in extra_args, idle_priority=idle,
                                log_path=log_path)
        control.read_root = Path(read_root)
        if initial_path is None:
            return proc
        if on_window is not None:
            on_window(proc)
        try:
            relative = Path(initial_path).resolve().relative_to(Path(read_root).resolve()).as_posix()
        except ValueError as error:
            raise PhotoCraftError("Initial photo is outside the automation read root") from error
        # Open through the control channel so readiness includes decoding;
        # command-line opens may still be running when the HWND first appears.
        opened = control.app_open(relative, camera_raw=camera_raw)
        proc.camera_raw_rebaseline = bool((opened.get("cameraRaw") or {}).get("dialog"))
        timings["document_ready_ms"] = (time.perf_counter() - started) * 1000
    except BaseException:
        if control is not None:
            control.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
        token_file.unlink(missing_ok=True)
        raise

    return proc


_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_NORMAL_PRIORITY_CLASS = 0x00000020
_PROCESS_SET_INFORMATION = 0x0200


_editor_job = None  # the job object's handle, kept for the life of this process


def tie_to_this_process(process: subprocess.Popen) -> bool:
    """Makes sure the editor cannot outlive Image Triage. The editor now stays up for as long as the app does, and an app that
    crashes, is killed or restarts for development runs no cleanup: the editor is put in a Windows job object that ends its
    processes when the last handle to the job closes, which the system does for us when this process ends, however it ends.
    Returns whether it was tied (False elsewhere than Windows, or if the system refused: the editor then runs as before)."""
    global _editor_job
    if os.name != "nt":
        return False

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    try:
        if _editor_job is None:
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return False
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):  # JobObjectExtendedLimitInformation
                kernel32.CloseHandle(wintypes.HANDLE(job))
                return False
            _editor_job = job
        return bool(kernel32.AssignProcessToJobObject(_editor_job, wintypes.HANDLE(int(process._handle))))
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def raise_priority(proc: PhotoCraftProcess) -> None:
    """Back to normal priority for an editor that was started in the background: from here on it is what the user is using."""
    if not proc.idle_priority:
        return
    proc.idle_priority = False
    if os.name != "nt":
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_PROCESS_SET_INFORMATION, False, proc.process.pid)
    if not handle:
        return
    try:
        kernel32.SetPriorityClass(handle, _NORMAL_PRIORITY_CLASS)
    finally:
        kernel32.CloseHandle(handle)


def embed_in_widget(photocraft_hwnd: int, parent_hwnd: int) -> None:
    """Reparent PhotoCraft's native window as a borderless child of ``parent_hwnd``."""
    user32 = ctypes.windll.user32
    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SetParent.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]

    style = get_style(photocraft_hwnd, _GWL_STYLE)
    style &= ~(_WS_POPUP | _WS_CAPTION | _WS_THICKFRAME | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX)
    style |= _WS_CHILD
    set_style(photocraft_hwnd, _GWL_STYLE, style)

    user32.SetParent(photocraft_hwnd, parent_hwnd)
    user32.SetWindowPos(photocraft_hwnd, 0, 0, 0, 0, 0, _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow(photocraft_hwnd, 4)  # SW_SHOWNOACTIVATE, already a child.


def resize_embedded(photocraft_hwnd: int, width: int, height: int) -> None:
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos(photocraft_hwnd, 0, 0, 0, max(1, width), max(1, height), _SWP_NOZORDER | _SWP_NOACTIVATE)
