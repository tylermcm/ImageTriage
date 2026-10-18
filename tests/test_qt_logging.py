"""Exercise the filter through Qt's actual categorized logging in a fresh process."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_tiff_metadata_filter_keeps_other_qt_diagnostics():
    script = r'''
from PySide6.QtCore import QLoggingCategory, QMessageLogger
from image_triage.qt_logging import install_qt_message_filter
install_qt_message_filter()
install_qt_message_filter()
tiff = QLoggingCategory("qt.imageformats.tiff")
other = QLoggingCategory("image_triage.filter_test")
logger = QMessageLogger()
for tag in ("Artist", "Copyright", "Software"):
    warning = f'ASCII value for tag "{tag}" contains null byte in value; value incorrectly truncated during reading due to implementation limitations'
    logger.warning(tiff, warning)
    logger.warning(tiff, '"' + warning.replace('"', '\\"') + '"')
logger.warning(tiff, "Could not read TIFF image data")
logger.warning(other, warning)
logger.critical(tiff, warning)
logger.warning(tiff, 'ASCII value for tag "ImageDescription" contains null byte in value; value incorrectly truncated during reading due to implementation limitations')
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stderr.splitlines()
    assert len(lines) == 4, result.stderr
    assert "Could not read TIFF image data" in lines[0]
    assert lines[1].startswith("image_triage.filter_test:")
    assert 'tag "Software"' in lines[2]  # Critical messages are never suppressed.
    assert 'tag "ImageDescription"' in lines[3]


def test_filter_chains_preexisting_qt_handler():
    script = r'''
from PySide6.QtCore import QLoggingCategory, QMessageLogger, qInstallMessageHandler
from image_triage.qt_logging import install_qt_message_filter
messages = []
qInstallMessageHandler(lambda mode, context, message: messages.append(message))
install_qt_message_filter()
category = QLoggingCategory("qt.imageformats.tiff")
logger = QMessageLogger()
logger.warning(category, 'ASCII value for tag "Artist" contains null byte in value; value incorrectly truncated during reading due to implementation limitations')
logger.warning(category, "A real TIFF warning")
assert messages == ["A real TIFF warning"], messages
'''
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
