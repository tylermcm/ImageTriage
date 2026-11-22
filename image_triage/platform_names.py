"""What the user's system calls the things Image Triage hands work to: its file manager and its trash. Shown in menus and messages, so a
Linux or macOS user does not read about File Explorer and the Recycle Bin. (QML has the same names from ``Qt.platform.os``.)"""
from __future__ import annotations

import sys

if sys.platform == "win32":
    FILE_MANAGER = "File Explorer"
    TRASH = "Recycle Bin"
elif sys.platform == "darwin":
    FILE_MANAGER = "Finder"
    TRASH = "Trash"
else:
    FILE_MANAGER = "the file manager"
    TRASH = "Trash"


def show_in_file_manager(what: str = "") -> str:
    """The menu item's text: "Show in File Explorer", "Show folder in Finder", "Show in the File Manager" (``what``: "folder")."""
    target = FILE_MANAGER if sys.platform in ("win32", "darwin") else "File Manager"
    return f"Show {what + ' ' if what else ''}in {target}"
