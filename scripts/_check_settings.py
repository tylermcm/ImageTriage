"""Settings for the live checks (scripts/check_*.py).

The checks pin a few settings (Camera Raw first, the editor on demand) and put them back at the end; one that is interrupted or dies
left them pinned in the profile the app itself uses (the owner's browsing then silently changed). They now run against a throwaway
registry key, and the key is deleted when the check ends.

The editor they launch has a profile of its own too (``PHOTOCRAFT_CONFIG_DIR``): the handoff check cycles through the editor's themes,
and the editor saves its theme in its preferences, so a check run against the owner's PhotoCraft profile left it on the last theme it
tried. Each check gets a fresh, empty profile folder, removed when the check ends; the owner's profile is never written, and never
"restored" either (another editor may be using it).
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

CHECK_KEY = r"HKEY_CURRENT_USER\Software\Image Triage Check"
_registered = False


def use_throwaway_settings() -> None:
    """Point ``user_settings()`` at ``CHECK_KEY`` for this process. Call it before the first ``user_settings()``."""
    global _registered
    if sys.platform != "win32":
        return  # the other platforms' store cannot be told apart by key; these checks are Windows-only anyway
    import image_triage.app_identity as identity

    identity._REGISTRY_KEY = CHECK_KEY
    if not _registered:
        _registered = True
        atexit.register(_forget)
    use_throwaway_editor_profile()


_editor_profile: str | None = None


def use_throwaway_editor_profile() -> str:
    """Give every editor this process launches a fresh, private PhotoCraft profile (``PHOTOCRAFT_CONFIG_DIR``, inherited through
    the environment). Returns the folder; it is removed when the process ends."""
    global _editor_profile
    if _editor_profile is None:
        _editor_profile = tempfile.mkdtemp(prefix="photocraft_check_profile_")
        atexit.register(shutil.rmtree, _editor_profile, True)
    os.environ["PHOTOCRAFT_CONFIG_DIR"] = _editor_profile
    return _editor_profile


def _forget() -> None:
    from PySide6.QtCore import QSettings

    settings = QSettings(CHECK_KEY, QSettings.Format.NativeFormat)
    settings.clear()
    settings.sync()
    try:
        import winreg

        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, r"Software\Image Triage Check")
    except OSError:
        pass
