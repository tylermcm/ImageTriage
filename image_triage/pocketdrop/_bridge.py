"""ctypes bindings for pocketdrop.dll (native/pocketdrop/capi/pocketdrop_capi.h).

Only the layout of the C interface lives here; panel.py implements it with Qt.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import CFUNCTYPE, POINTER, c_char_p, c_float, c_int, c_ssize_t, c_uint8, c_void_p
from pathlib import Path

ABI_VERSION = 1

_F4 = POINTER(c_float)
_SINK = c_void_p

# pd_gfx
CLEAR = CFUNCTYPE(None, c_void_p, _F4)
FILL_RECT = CFUNCTYPE(None, c_void_p, _F4, _F4)
FILL_ROUND = CFUNCTYPE(None, c_void_p, _F4, c_float, _F4)
STROKE_ROUND = CFUNCTYPE(None, c_void_p, _F4, c_float, _F4, c_float, c_int)
FILL_CIRCLE = CFUNCTYPE(None, c_void_p, c_float, c_float, c_float, _F4)
GRADIENT_ROUND = CFUNCTYPE(None, c_void_p, _F4, c_float, _F4, _F4)
STROKE_POLYLINE = CFUNCTYPE(None, c_void_p, _F4, c_int, c_int, _F4, c_float)
TEXT = CFUNCTYPE(None, c_void_p, c_char_p, _F4, c_int, _F4, c_int)
MEASURE = CFUNCTYPE(c_float, c_void_p, c_char_p, c_int)
SET_ALIASED = CFUNCTYPE(None, c_void_p, c_int)
PUSH_CLIP = CFUNCTYPE(None, c_void_p, _F4)
POP_CLIP = CFUNCTYPE(None, c_void_p)
FILE_ICON = CFUNCTYPE(None, c_void_p, c_char_p, _F4)


class Gfx(ctypes.Structure):
    _fields_ = [
        ("ctx", c_void_p),
        ("clear", CLEAR),
        ("fill_rect", FILL_RECT),
        ("fill_round", FILL_ROUND),
        ("stroke_round", STROKE_ROUND),
        ("fill_circle", FILL_CIRCLE),
        ("gradient_round", GRADIENT_ROUND),
        ("stroke_polyline", STROKE_POLYLINE),
        ("text", TEXT),
        ("measure", MEASURE),
        ("set_aliased", SET_ALIASED),
        ("push_clip", PUSH_CLIP),
        ("pop_clip", POP_CLIP),
        ("file_icon", FILE_ICON),
    ]


# pd_shell
VOID = CFUNCTYPE(None, c_void_p)
SET_FLAG = CFUNCTYPE(None, c_void_p, c_int)
CLIENT_SIZE = CFUNCTYPE(None, c_void_p, _F4, _F4)
WITH_TEXT = CFUNCTYPE(None, c_void_p, c_char_p)
COPY_IMAGE = CFUNCTYPE(c_int, c_void_p, POINTER(c_uint8), c_int, c_int)
INTO_SINK = CFUNCTYPE(None, c_void_p, _SINK)
BROWSE = CFUNCTYPE(None, c_void_p, c_int, _SINK)
TEXT_INTO_SINK = CFUNCTYPE(None, c_void_p, c_char_p, _SINK)
CONFIRM = CFUNCTYPE(c_int, c_void_p, POINTER(c_int))
POPUP_MENU = CFUNCTYPE(c_int, c_void_p, c_char_p, c_float, c_float)
ALERT = CFUNCTYPE(None, c_void_p, c_char_p, c_char_p)
LOAD_SETTING = CFUNCTYPE(c_int, c_void_p, c_char_p, c_int)
SAVE_SETTING = CFUNCTYPE(None, c_void_p, c_char_p, c_int)
SAVE_STRING = CFUNCTYPE(None, c_void_p, c_char_p, c_char_p)
NATIVE_WINDOW = CFUNCTYPE(c_ssize_t, c_void_p)


class Shell(ctypes.Structure):
    _fields_ = [
        ("ctx", c_void_p),
        ("invalidate", VOID),
        ("set_animating", SET_FLAG),
        ("wake", VOID),
        ("client_size", CLIENT_SIZE),
        ("copy_text", WITH_TEXT),
        ("copy_image", COPY_IMAGE),
        ("read_clipboard", INTO_SINK),
        ("browse", BROWSE),
        ("choose_folder", TEXT_INTO_SINK),
        ("open_url", WITH_TEXT),
        ("open_bluetooth_setup", VOID),
        ("confirm_anywhere_risk", CONFIRM),
        ("reveal_path", WITH_TEXT),
        ("attention", VOID),
        ("popup_menu", POPUP_MENU),
        ("alert", ALERT),
        ("load_setting", LOAD_SETTING),
        ("save_setting", SAVE_SETTING),
        ("load_string", TEXT_INTO_SINK),
        ("save_string", SAVE_STRING),
        ("set_topmost", SET_FLAG),
        ("clean_path", TEXT_INTO_SINK),
        ("native_window", NATIVE_WINDOW),
    ]


def library_path() -> Path:
    """Beside this module, both from source and in the frozen build, where
    freeze_support copies it to lib/image_triage/pocketdrop."""
    name = {"win32": "pocketdrop.dll", "darwin": "libpocketdrop.dylib"}.get(sys.platform, "libpocketdrop.so")
    return Path(__file__).resolve().parent / name


class NativeUnavailable(RuntimeError):
    pass


def load_library() -> ctypes.CDLL:
    path = library_path()
    if not path.is_file():
        raise NativeUnavailable(f"{path.name} is not built. Run native/pocketdrop/build_windows.bat.")
    try:
        lib = ctypes.CDLL(str(path))
    except OSError as exc:
        raise NativeUnavailable(f"Couldn't load {path.name}: {exc}") from exc

    host = c_void_p
    lib.pd_abi_version.restype = c_int
    lib.pd_create.argtypes = [POINTER(Shell), POINTER(Gfx)]
    lib.pd_create.restype = host
    for name in ("pd_start", "pd_destroy", "pd_tick", "pd_run_posted", "pd_mouse_leave", "pd_paste",
                 "pd_copy_link", "pd_share_link", "pd_refresh_network"):
        fn = getattr(lib, name)
        fn.argtypes = [host]
        fn.restype = None
    lib.pd_paint.argtypes = [host, c_float]
    for name in ("pd_mouse_move", "pd_mouse_down", "pd_mouse_up"):
        fn = getattr(lib, name)
        fn.argtypes = [host, c_float, c_float]
        fn.restype = None
    lib.pd_wheel.argtypes = [host, c_float]
    lib.pd_wants_pointer.argtypes = [host]
    lib.pd_wants_pointer.restype = c_int
    lib.pd_set_drag_over.argtypes = [host, c_int]
    lib.pd_browse.argtypes = [host, c_int]
    lib.pd_add_paths.argtypes = [host, POINTER(c_char_p), c_int]
    lib.pd_add_text.argtypes = [host, c_char_p]
    lib.pd_sink_add.argtypes = [_SINK, c_char_p]
    lib.pd_sink_set.argtypes = [_SINK, c_char_p]
    if lib.pd_abi_version() != ABI_VERSION:
        raise NativeUnavailable(f"{path.name} is out of date. Rebuild it with native/pocketdrop/build_windows.bat.")
    return lib


def floats(pointer, count: int) -> list[float]:
    return [pointer[i] for i in range(count)]


def text(value: bytes | None) -> str:
    return value.decode("utf-8", "replace") if value else ""


def encode(value: str) -> bytes:
    return value.encode("utf-8")
