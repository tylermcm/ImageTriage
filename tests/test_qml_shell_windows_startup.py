"""Exercise QML plugin loading in a fresh Windows process."""
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="Windows DLL search regression")
def test_dialog_plugin_loads_in_fresh_process():
    result = subprocess.run(
        [sys.executable, "-c", """
from image_triage.qml_shell.app import _configure_qt_dll_search
_configure_qt_dll_search()
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlComponent, QQmlEngine
from PySide6 import QtQuick
app = QApplication([])
engine = QQmlEngine()
component = QQmlComponent(engine)
component.setData(b'import QtQuick\\nimport QtQuick.Dialogs\\nimport QtQuick.Shapes\\nItem {}', QUrl())
assert component.isReady(), [e.toString() for e in component.errors()]
"""],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
