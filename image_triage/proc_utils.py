"""Small subprocess helpers shared by the window and the background tasks."""
from __future__ import annotations

import os
import subprocess


def _headless_background_popen_kwargs() -> dict[str, object]:
    """Hide console windows for background helper processes on Windows."""
    if os.name != "nt":
        return {}
    kwargs: dict[str, object] = {}
    creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)
    if creationflags:
        kwargs["creationflags"] = creationflags
    startupinfo_cls = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_cls is not None:
        startupinfo = startupinfo_cls()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0) or 0)
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0) or 0)
        kwargs["startupinfo"] = startupinfo
    return kwargs
