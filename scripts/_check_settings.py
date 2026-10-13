"""Settings for the live checks (scripts/check_*.py).

The checks pin a few settings (Camera Raw first, the editor on demand) and put them back at the end; one that is interrupted or dies
left them pinned in the profile the app itself uses (the owner's browsing then silently changed). They now run against a throwaway
registry key, and the key is deleted when the check ends.
"""
from __future__ import annotations

import atexit
import sys

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
