"""Pipe framing stays bounded even when a descendant never closes stdout."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from image_triage.cli_culler_process import WorkerOutput


@pytest.fixture
def pipe():
    read_fd, write_fd = os.pipe()
    with os.fdopen(read_fd, "rb", buffering=0) as stream:
        try:
            yield WorkerOutput(stream), write_fd
        finally:
            os.close(write_fd)


def read_line(output):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        line = output.readline()
        if line is not None:
            return line
    pytest.fail("Protocol reader did not return a complete line")


def test_partial_utf8_event_can_be_interrupted_without_waiting_for_newline(pipe):
    output, writer = pipe
    event = '{"name":"拍摄"}\n'.encode()
    os.write(writer, event[:10])  # includes a partial UTF-8 code point
    assert output.readline() is None
    started = time.monotonic()
    assert output.readline() is None
    assert time.monotonic() - started < 0.5
    os.write(writer, event[10:])
    assert read_line(output).decode() == event.decode()


def test_dead_worker_does_not_wait_for_descendant_to_close_inherited_stdout(pipe):
    output, writer = pipe
    os.write(writer, b'{"event":"runtime"}\n{"event":"complete"}')
    assert output.readline(exited=True) == b'{"event":"runtime"}\n'
    assert output.readline(exited=True) == b'{"event":"complete"}'
    started = time.monotonic()
    assert output.readline(exited=True) == b""
    assert time.monotonic() - started < 0.5  # writer is deliberately still open


def test_long_unterminated_event_is_rejected_before_eof(pipe):
    output, writer = pipe
    output.LIMIT = 32
    os.write(writer, b"x" * 33)
    with pytest.raises(ValueError, match="exceeds 1 MiB"):
        output.readline()


@pytest.mark.skipif(os.name != "nt", reason="Windows cleanup-job bootstrap")
def test_missing_cleanup_job_prevents_worker_from_running(tmp_path):
    marker = tmp_path / "must-not-run"
    worker = tmp_path / "worker.py"
    worker.write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    bootstrap = Path(__file__).resolve().parents[1] / "image_triage/cli_culler_process.py"
    result = subprocess.run([sys.executable, "-I", str(bootstrap),
                             "Local\\ImageTriage-missing-job", str(worker)],
                            capture_output=True, timeout=5)
    assert result.returncode != 0
    assert not marker.exists()
