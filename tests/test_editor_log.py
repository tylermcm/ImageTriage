"""The editor's own output (a panic and its backtrace) is kept in a file beside Image Triage's logs instead of being thrown away, and a failed
handoff says whether the editor has exited and where its output is."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

from image_triage import photocraft_bridge as bridge


@pytest.fixture
def logs(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_LOG_DIR", str(tmp_path))
    return tmp_path


def test_the_editor_gets_a_file_in_the_log_folder(logs):
    handle, path = bridge._open_editor_log()
    try:
        assert path is not None and path.parent == logs and path.name.startswith("photocraft_editor_") and path.suffix == ".log"
        assert path.exists()
    finally:
        handle.close()


def test_two_starts_in_the_same_second_get_two_files(logs):
    first, first_path = bridge._open_editor_log()
    second, second_path = bridge._open_editor_log()
    first.close()
    second.close()
    assert first_path != second_path


def test_only_the_last_few_starts_are_kept(logs):
    made = []
    for n in range(bridge.EDITOR_LOGS_KEPT + 4):
        handle, path = bridge._open_editor_log()
        handle.close()
        os.utime(path, (1_000_000 + n, 1_000_000 + n))  # distinct, ordered write times
        made.append(path)
    kept = sorted(logs.glob("photocraft_editor_*.log"))
    assert len(kept) == bridge.EDITOR_LOGS_KEPT
    assert set(kept) == set(made[-bridge.EDITOR_LOGS_KEPT:]), "the newest ones"


def test_other_files_in_the_log_folder_are_left_alone(logs):
    other = logs / "errors.log"
    other.write_text("x")
    for _ in range(bridge.EDITOR_LOGS_KEPT + 2):
        handle, _path = bridge._open_editor_log()
        handle.close()
    assert other.read_text() == "x"


def test_a_log_folder_that_cannot_be_used_means_the_output_is_discarded_as_before(tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x")
    monkeypatch.setenv("IMAGE_TRIAGE_LOG_DIR", str(blocker / "logs"))
    handle, path = bridge._open_editor_log()
    assert handle is subprocess.DEVNULL and path is None


def test_what_a_child_process_writes_to_stderr_lands_in_the_file_after_the_parent_closed_its_handle(logs):
    handle, path = bridge._open_editor_log()
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys, time; time.sleep(0.3); sys.stderr.write('thread main panicked at editor.rs:1\\n'); sys.stdout.write('and stdout\\n')"],
        stdout=handle, stderr=subprocess.STDOUT,
    )
    handle.close()
    child.wait(timeout=20)
    text = path.read_text()
    assert "thread main panicked at editor.rs:1" in text and "and stdout" in text


def test_the_editor_is_started_with_its_output_going_to_the_file_and_a_backtrace_on_a_panic(logs, tmp_path, monkeypatch):
    seen = {}

    class FakeProcess:
        pid = 4242

        def __init__(self, args, **kwargs):
            seen.update(kwargs)

        def poll(self):
            return None

    control = Mock()
    monkeypatch.setattr(bridge.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(bridge, "_connect_with_retry", lambda port, token, process: control)
    monkeypatch.setattr(bridge, "_find_window_for_pid", lambda pid: 99)
    monkeypatch.setattr(bridge.ctypes.windll.user32, "ShowWindow", Mock(), raising=False)
    monkeypatch.delenv("RUST_BACKTRACE", raising=False)
    proc = bridge._launch_photocraft_binary(None, read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft.exe", idle=True)
    try:
        assert proc.log_path is not None and proc.log_path.parent == logs
        assert seen["stderr"] == subprocess.STDOUT
        assert seen["stdout"].name == str(proc.log_path)
        assert seen["env"]["RUST_BACKTRACE"] == "1"
    finally:
        proc.token_file.unlink(missing_ok=True)


def test_a_backtrace_setting_of_the_users_is_kept(logs, tmp_path, monkeypatch):
    seen = {}

    class FakeProcess:
        pid = 1

        def __init__(self, args, **kwargs):
            seen.update(kwargs)

        def poll(self):
            return None

    monkeypatch.setattr(bridge.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(bridge, "_connect_with_retry", lambda port, token, process: Mock())
    monkeypatch.setattr(bridge, "_find_window_for_pid", lambda pid: 99)
    monkeypatch.setattr(bridge.ctypes.windll.user32, "ShowWindow", Mock(), raising=False)
    monkeypatch.setenv("RUST_BACKTRACE", "full")
    proc = bridge._launch_photocraft_binary(None, read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft.exe", idle=True)
    proc.token_file.unlink(missing_ok=True)
    assert seen["env"]["RUST_BACKTRACE"] == "full"


def test_the_log_is_closed_even_when_the_editor_could_not_be_started(logs, tmp_path, monkeypatch):
    opened = []
    real_open = bridge._open_editor_log

    def tracking():
        handle, path = real_open()
        opened.append(handle)
        return handle, path

    monkeypatch.setattr(bridge, "_open_editor_log", tracking)
    monkeypatch.setattr(bridge.subprocess, "Popen", Mock(side_effect=OSError("no such file")))
    with pytest.raises(OSError):
        bridge._launch_photocraft_binary(None, read_root=str(tmp_path), write_root=str(tmp_path), executable="missing.exe")
    assert opened and opened[0].closed


def test_the_exit_note_says_where_the_output_is_and_whether_the_editor_has_gone(tmp_path):
    process = Mock()
    proc = bridge.PhotoCraftProcess(
        process=process, control=Mock(), hwnd=1, token_file=tmp_path / "t", read_root=tmp_path, log_path=tmp_path / "photocraft_editor_x.log",
    )
    process.poll.return_value = None
    assert "still running" in proc.exit_note() and "photocraft_editor_x.log" in proc.exit_note()
    process.poll.return_value = -1073741819
    assert "exited with code -1073741819" in proc.exit_note()
    proc.log_path = None
    assert "its own output" not in proc.exit_note()


def test_the_editor_is_launched_without_a_console_window(logs, tmp_path, monkeypatch):
    # A debug or test build of the editor is a console program; without CREATE_NO_WINDOW a terminal opens beside Image Triage.
    seen = {}

    class FakeProcess:
        pid = 7

        def __init__(self, args, **kwargs):
            seen.update(kwargs)

        def poll(self):
            return None

    monkeypatch.setattr(bridge.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(bridge, "_connect_with_retry", lambda port, token, process: Mock())
    monkeypatch.setattr(bridge, "_find_window_for_pid", lambda pid: 99)
    monkeypatch.setattr(bridge.ctypes.windll.user32, "ShowWindow", Mock(), raising=False)
    proc = bridge._launch_photocraft_binary(None, read_root=str(tmp_path), write_root=str(tmp_path), executable="photocraft.exe", idle=True)
    try:
        assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert seen["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    finally:
        proc.token_file.unlink(missing_ok=True)
