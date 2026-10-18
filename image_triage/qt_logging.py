"""Filter the known TIFF metadata warning without silencing other Qt diagnostics."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QtMsgType, qFormatLogMessage, qInstallMessageHandler


_TIFF_METADATA_WARNINGS = set()
for _tag in ("Artist", "Copyright", "Software"):
    _warning = (
        f'ASCII value for tag "{_tag}" contains null byte in value; '
        "value incorrectly truncated during reading due to implementation limitations"
    )
    # Qt's TIFF plugin quotes and escapes the libtiff text through QDebug.
    _TIFF_METADATA_WARNINGS.update(
        (_warning, f'"{_warning}"', '"' + _warning.replace('"', '\\"') + '"')
    )

_installed = False
_previous_handler = None


def _qt_message_handler(mode, context, message: str) -> None:
    if (
        mode == QtMsgType.QtWarningMsg
        and context.category == "qt.imageformats.tiff"
        and message in _TIFF_METADATA_WARNINGS
    ):
        return
    if _previous_handler is not None:
        _previous_handler(mode, context, message)
        return
    # PySide6 does not expose Qt's default handler; preserve its message pattern
    # and stderr destination for everything outside the narrow filter.
    formatted = qFormatLogMessage(mode, context, message) + "\n"
    if sys.stderr is not None:
        sys.stderr.write(formatted)
    else:
        try:
            os.write(2, formatted.encode("utf-8", errors="backslashreplace"))
        except OSError:
            pass  # A windowed executable may have no stderr handle.


def install_qt_message_filter() -> None:
    """Install once, before startup begins reading images."""
    global _installed, _previous_handler
    if not _installed:
        _previous_handler = qInstallMessageHandler(_qt_message_handler)
        _installed = True
