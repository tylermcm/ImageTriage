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
import os
import secrets
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .shell_actions import detect_photocraft_executable

# Formats PhotoCraft can open directly (docs/README "Formats" section). Anything
# else (RAW, FITS, ...) must be decoded to one of these first.
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


def path_needs_conversion(path: str) -> bool:
    return Path(path).suffix.lower() not in NATIVE_SUFFIXES


def materialize_for_photocraft(path: str, cache_dir: Path) -> str:
    """Decode a RAW/unsupported file to a TIFF PhotoCraft can open directly.

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
    out_path = cache_dir / (Path(path).stem + "__photocraft_source.tiff")
    if not image.save(str(out_path), "TIFF"):
        raise PhotoCraftError(f"Could not write converted TIFF for {path!r}")
    return str(out_path)


class PhotoCraftControl:
    """Blocking JSON-lines client for PhotoCraft's control channel."""

    def __init__(self, port: int, token: str, *, host: str = "127.0.0.1", timeout: float = 5.0) -> None:
        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._file = self._sock.makefile("rwb")
        self._next_id = 1
        self._authenticate(token)

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
        reply = self._request(method, params)
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft control {method!r} failed: {reply.get('error')}")
        return reply.get("result") or {}

    def app_open(self, path: str) -> dict:
        return self.call("app.open", {"path": _automation_path(path)})

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

    def quit(self) -> None:
        try:
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

    def is_running(self) -> bool:
        return self.process.poll() is None

    def path_within_root(self, path: str) -> str | None:
        """``path`` relative to this process's automation read root, or ``None``
        if it falls outside it (app.open/app.save refuse paths outside the root)."""
        try:
            return Path(path).resolve().relative_to(self.read_root.resolve()).as_posix()
        except ValueError:
            return None

    def shutdown(self, *, timeout: float = 3.0) -> None:
        """Ask PhotoCraft to quit over the control channel, then fall back to
        terminate()/kill() if it doesn't exit in time."""
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
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    def _callback(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and user32.IsWindowVisible(hwnd):
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
) -> PhotoCraftProcess:
    exe = executable or detect_photocraft_executable()
    if not exe:
        raise PhotoCraftError("PhotoCraft executable not found")

    port = _free_tcp_port()
    token = secrets.token_hex(32)
    fd, token_path_str = tempfile.mkstemp(prefix="photocraft_token_", suffix=".txt")
    token_file = Path(token_path_str)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)

    args = [
        exe,
        "--control", str(port),
        "--control-token-file", str(token_file),
        "--automation-read-root", read_root,
        "--automation-write-root", write_root,
        initial_path,
    ]
    process = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )

    try:
        control = _connect_with_retry(port, token, process)
        hwnd = _find_window_for_pid(process.pid)
        if hwnd is None:
            control.close()
            raise PhotoCraftError("Could not find PhotoCraft's window")
    except BaseException:
        if process.poll() is None:
            process.terminate()
        token_file.unlink(missing_ok=True)
        raise

    return PhotoCraftProcess(process=process, control=control, hwnd=hwnd, token_file=token_file, read_root=Path(read_root))


def embed_in_widget(photocraft_hwnd: int, parent_hwnd: int) -> None:
    """Reparent PhotoCraft's native window as a borderless child of ``parent_hwnd``."""
    user32 = ctypes.windll.user32
    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)

    style = get_style(photocraft_hwnd, _GWL_STYLE)
    style &= ~(_WS_POPUP | _WS_CAPTION | _WS_THICKFRAME | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX)
    style |= _WS_CHILD
    set_style(photocraft_hwnd, _GWL_STYLE, style)

    user32.SetParent(photocraft_hwnd, parent_hwnd)
    user32.SetWindowPos(photocraft_hwnd, 0, 0, 0, 0, 0, _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)


def resize_embedded(photocraft_hwnd: int, width: int, height: int) -> None:
    user32 = ctypes.windll.user32
    user32.SetWindowPos(photocraft_hwnd, 0, 0, 0, max(1, width), max(1, height), _SWP_NOZORDER | _SWP_NOACTIVATE)
