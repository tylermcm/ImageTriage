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
import time
from dataclasses import dataclass, field
from pathlib import Path

from .edit_storage import edit_root_for
from .formats import RAW_SUFFIXES
from .shell_actions import companion_photocraft_executables, detect_photocraft_executable

# Formats PhotoCraft can open directly (docs/README "Formats" section). Anything
# else must be decoded first, except RAW which requires the sensor-backed handoff.
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


class PhotoCraftControl:
    """Blocking JSON-lines client for PhotoCraft's control channel."""

    def __init__(self, port: int, token: str, *, host: str = "127.0.0.1", timeout: float = 120.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=min(timeout, 2.0))
        self._sock.settimeout(timeout)
        self._file = self._sock.makefile("rwb")
        self._next_id = 1
        try:
            self._authenticate(token)
        except BaseException:
            self.close()
            raise

    def _authenticate(self, token: str) -> None:
        reply = self._request("auth", {"token": token})
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft control auth failed: {reply.get('error')}")

    def _request(self, method: str, params: dict | None = None) -> dict:
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
        reply = json.loads(reply_line)
        if reply.get("id") != req_id:
            raise PhotoCraftError(f"PhotoCraft control reply id mismatch: expected {req_id}, got {reply.get('id')}")
        return reply

    def call(self, method: str, params: dict | None = None) -> dict:
        try:
            reply = self._request(method, params)
        except (OSError, ValueError) as error:
            raise PhotoCraftError(f"PhotoCraft control {method!r} failed: {error}") from error
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft control {method!r} failed: {reply.get('error')}")
        return reply.get("result") or {}

    def app_open(self, path: str, *, replace: bool = False, should_continue=None) -> dict:
        params = {"path": _automation_path(path)}
        sensor = None
        if replace:
            params["replace"] = True
        source_suffix = Path(path).suffix.lower()
        raw_project = source_suffix == ".pcraft" and Path(Path(path).stem).suffix.lower() in RAW_SUFFIXES
        if source_suffix in RAW_SUFFIXES or raw_project:
            if not getattr(self, "raw_smart_supported", False):
                raise PhotoCraftCompatibilityError("This PhotoCraft build cannot retain editable RAW data. Update the hosted editor build.")
            if not raw_project:
                params["rawSmartObject"] = True
                if source_suffix == ".nef":
                    if not getattr(self, "raw_sensor_supported", False):
                        raise PhotoCraftCompatibilityError("This PhotoCraft build lacks the Nikon sensor adapter. Update the hosted editor build.")
                    read_root = getattr(self, "read_root", None)
                    if read_root is None:
                        raise PhotoCraftError("Nikon RAW import requires the editor's automation read root")
                    source = (Path(read_root) / path).resolve()
                    try:
                        source.relative_to(Path(read_root).resolve())
                        from .photocraft_raw_source import materialize_sensor_dng
                        sensor = materialize_sensor_dng(str(source), edit_root_for(source.parent) / "raw-sensors")
                        params["rawSensorPath"] = sensor.relative_to(Path(read_root).resolve()).as_posix()
                    except (OSError, ValueError, RuntimeError) as error:
                        raise PhotoCraftError(f"Could not unpack Nikon sensor data: {error}") from error
        try:
            # The unpack above can take a while; if the caller has moved on meanwhile, do not
            # start an open that would later replace the photo now on screen.
            if should_continue is not None and not should_continue():
                raise PhotoCraftSuperseded(f"{path} is no longer the selected photo")
            return self.call("app.open", params)
        finally:
            # PhotoCraft embeds the sensor bytes before replying. Recently used unpacked
            # sensors stay in a small bounded cache so revisiting a photo skips the unpack.
            if sensor is not None:
                from .photocraft_raw_source import trim_sensor_cache
                trim_sensor_cache(sensor.parent, keep=sensor)

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
        self.raw_smart_supported = capabilities.get("rawSmartObject", 0) >= 1
        self.raw_sensor_supported = capabilities.get("rawSensorAdapter", 0) >= 1
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
    initial_path: str,
    *,
    read_root: str,
    write_root: str,
    executable: str | None = None,
    on_window=None,
) -> PhotoCraftProcess:
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
            proc = _launch_photocraft_binary(initial_path, read_root=read_root, write_root=write_root, executable=candidate, on_window=on_window)
            proc.executable = candidate
            logging.getLogger(__name__).info("Hosted PhotoCraft executable: %s", candidate)
            return proc
        except PhotoCraftCompatibilityError as error:
            last_error = error
            logging.getLogger(__name__).warning("Rejected incompatible PhotoCraft executable %s: %s", candidate, error)
    raise PhotoCraftCompatibilityError(f"No compatible PhotoCraft build found. {last_error}")


def _launch_photocraft_binary(
    initial_path: str, *, read_root: str, write_root: str, executable: str, on_window=None,
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
    ]
    process = None
    control = None
    env = dict(os.environ)
    if os.name == "nt":
        # PhotoCraft's automatic backend can pick Vulkan, where game-overlay layers
        # (Epic, Galaxy, ReShade) leave the embedded canvas blank; DX12 renders correctly.
        env.setdefault("WGPU_BACKEND", "dx12")
    try:
        process = subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        timings["spawn_ms"] = (time.perf_counter() - started) * 1000
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
                                port=port, token=token)
        if on_window is not None:
            on_window(proc)
        try:
            relative = Path(initial_path).resolve().relative_to(Path(read_root).resolve()).as_posix()
        except ValueError as error:
            raise PhotoCraftError("Initial photo is outside the automation read root") from error
        # Open through the control channel so readiness includes decoding;
        # command-line opens may still be running when the HWND first appears.
        control.read_root = Path(read_root)
        control.app_open(relative)
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
