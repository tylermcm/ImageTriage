"""The editor is started quietly in the background after Image Triage boots: no photo open, below-normal priority, rooted at the
folder being browsed, and used (not restarted) by the first photo that is opened in it."""
from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from image_triage import photocraft_bridge as bridge


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])




def prestarted_proc(tmp_path, **kwargs):
    control = Mock()
    control.document_revision.return_value = None
    proc = bridge.PhotoCraftProcess(
        process=Mock(), control=control, hwnd=123, token_file=tmp_path / "token",
        read_root=tmp_path, write_root=tmp_path / ".image_triage_edits", executable="photocraft.exe", idle_priority=True, **kwargs,
    )
    proc.process.poll.return_value = None
    return proc


def drain(ctl):
    ctl._photocraft_executor.submit(lambda: None).result()














# --- the bridge ----------------------------------------------------------------------------------------

@pytest.mark.skipif(os.name != "nt", reason="priority classes and window handling are Windows-only")
def test_an_idle_launch_opens_nothing_and_starts_below_normal(tmp_path, monkeypatch):
    started = {}

    class FakeProcess:
        pid = 4242

        def __init__(self, args, **kwargs):
            started["args"], started["flags"] = args, kwargs.get("creationflags", 0)

        def poll(self):
            return None

    control = Mock()
    monkeypatch.setattr(bridge.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(bridge, "_connect_with_retry", lambda port, token, process: control)
    monkeypatch.setattr(bridge, "_find_window_for_pid", lambda pid: 99)
    monkeypatch.setattr(bridge.ctypes.windll.user32, "ShowWindow", Mock(), raising=False)
    proc = bridge._launch_photocraft_binary(
        None, read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft.exe", extra_args=("--raw-workspace",), idle=True,
    )
    assert started["flags"] & bridge._BELOW_NORMAL_PRIORITY_CLASS, "started at below-normal priority"
    control.app_open.assert_not_called()
    assert proc.idle_priority and proc.camera_raw_first and proc.current_source is None
    proc.token_file.unlink(missing_ok=True)


@pytest.mark.skipif(os.name != "nt", reason="priority classes are Windows-only")
def test_raising_the_priority_of_a_background_editor_sets_the_process_to_normal(tmp_path):
    psutil = pytest.importorskip("psutil")
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], creationflags=bridge._BELOW_NORMAL_PRIORITY_CLASS)
    try:
        proc = bridge.PhotoCraftProcess(process=child, control=Mock(), hwnd=1, token_file=tmp_path / "t", read_root=tmp_path, idle_priority=True)
        assert psutil.Process(child.pid).nice() == psutil.BELOW_NORMAL_PRIORITY_CLASS
        bridge.raise_priority(proc)
        assert psutil.Process(child.pid).nice() == psutil.NORMAL_PRIORITY_CLASS
        assert not proc.idle_priority
        bridge.raise_priority(proc)  # again: nothing to do
    finally:
        child.kill()
        child.wait()


# --- shutting down while the background start is still running ------------------------------------------------------
