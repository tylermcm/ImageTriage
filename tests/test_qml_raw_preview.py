"""Persistent-preview ordering, image publication, lifecycle and input safety."""
import io
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QColor, QImage
from PySide6.QtCore import QObject, Signal

from image_triage.qml_shell import raw_preview
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell import qapp, pump  # noqa: F401


def wait(app, condition):
    until = time.monotonic() + 3
    while not condition() and time.monotonic() < until:
        pump(app, .02)
    assert condition()


def picture(value=40):
    image = QImage(40, 30, QImage.Format_RGBA8888)
    image.fill(QColor(value, 80, 120))
    return ({"histogram": [[1200]+[0]*255]*3, "fullSize": [8000, 6000]}, image)


def test_one_undo_step_for_a_drag_and_separate_following_adjustment(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw.beginGesture()
    for exposure in (.1, .2, .3, .4):
        raw.adjust("exposure", exposure)
    raw.endGesture()
    assert len(raw.history) == 1
    raw.adjust("contrast", 20)
    assert len(raw.history) == 2
    raw.undo()
    assert raw.values["exposure"] == .4 and raw.values["contrast"] == 0
    raw.undo()
    assert raw.values["exposure"] == 0
    raw.shutdown()


def test_frame_ordering_histogram_and_no_cross_photo_publication(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    revision = raw._revision
    result = (picture(), picture(), 20, "sample.NEF")
    raw._proxy_arrived(revision, result)
    assert raw.preview.startswith("image://rawpreview/")
    assert raw.histograms.get(raw.histogramPath)["ready"]
    initial = raw.preview
    raw.beginGesture()
    raw.adjust("exposure", .5)
    raw._proxy_arrived(revision, result)  # Last completed working frame during a drag.
    raw.endGesture()
    raw._proxy_arrived(revision-1, result)
    assert raw.preview == initial
    raw.open("other.NEF")
    raw._proxy_arrived(raw._revision, result)
    assert not raw.preview
    raw.shutdown()
    assert not raw_preview.has_frame(initial)


def test_full_resolution_is_separate_and_invalidated_by_new_edits(qapp, tmp_path):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw._proxy_arrived(raw._revision, (picture(), picture(), 20, "sample.NEF"))
    working = raw.preview
    raw.setDetailMode(True)
    full = str(tmp_path / "detail.png")
    raw._arrived(raw._revision, "detail", full, "")
    assert raw.preview.endswith("detail.png") and not raw.detailPending
    raw.adjust("exposure", 1)
    assert raw.preview == working and raw.detailPending
    raw._arrived(raw._revision-1, "detail", full, "")
    assert raw.preview == working
    raw.setDetailMode(False)
    assert not raw.detailPending
    raw.shutdown()


@pytest.mark.parametrize("header", [b'{"ok":true,"bytes":9999999999}\n',
    b'{"ok":true,"bytes":4,"width":2,"height":2}\n',
    b'{"ok":true,"bytes":4,"width":1,"height":1}\nxx'])
def test_native_frames_are_validated_before_use(header):
    connection = raw_preview.Connection.__new__(raw_preview.Connection)
    connection.proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(header))
    with pytest.raises(RuntimeError):
        connection.call({"op": "render"})


def _fake_worker(tmp_path, after_hello):
    """A stand-in for the native worker: answers hello, then runs `after_hello` (Python source) for the next request."""
    import sys

    script = tmp_path / "worker.py"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import sys, time\n"
        "sys.stdin.readline()\n"
        "sys.stdout.write('{\"ok\": true, \"protocol\": 2, \"bytes\": 0}\\n'); sys.stdout.flush()\n"
        "sys.stdin.readline()\n" + after_hello + "\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


@pytest.mark.skipif(__import__("os").name == "nt", reason="the stand-in worker is a script with a shebang line")
def test_a_worker_that_hangs_is_stopped_and_its_error_output_is_reported(tmp_path):
    connection = raw_preview.Connection(_fake_worker(tmp_path, "sys.stderr.write('decoder stuck at tile 7\\n'); sys.stderr.flush(); time.sleep(60)"))
    connection.timeout = 0.5
    started = time.monotonic()
    with pytest.raises(RuntimeError) as error:
        connection.call({"op": "render"})
    assert time.monotonic() - started < 10
    assert "did not answer" in str(error.value) and "decoder stuck at tile 7" in str(error.value)
    assert connection.proc.poll() is not None
    connection.close()


@pytest.mark.skipif(__import__("os").name == "nt", reason="the stand-in worker is a script with a shebang line")
def test_a_worker_that_dies_says_what_it_wrote_before_it_went(tmp_path):
    connection = raw_preview.Connection(_fake_worker(tmp_path, "sys.stderr.write('out of memory\\n'); sys.exit(3)"))
    with pytest.raises(RuntimeError) as error:
        connection.call({"op": "render"})
    assert "worker stopped" in str(error.value) and "out of memory" in str(error.value)
    connection.close()


def test_worker_reuses_source_and_reopens_when_signature_changes(qapp, monkeypatch, tmp_path):
    path = tmp_path / "sample.NEF"
    path.write_bytes(b"raw")
    calls, closed = [], []

    class Connection:
        def __init__(self, exe, own):
            own(self)

        def call(self, request):
            calls.append(request)
            return picture()

        def close(self):
            closed.append(True)

        def interrupt(self):
            pass

    monkeypatch.setattr(raw_preview, "Connection", Connection)
    worker = raw_preview.PreviewWorker("worker.exe")
    completed, errors = [], []
    worker.completed.connect(lambda *args: completed.append(args))
    worker.failed.connect(lambda *args: errors.append(args))
    worker.submit(1, str(path), {}, {"exposure": .5})
    wait(qapp, lambda: len(completed) == 1 or errors)
    worker.submit(2, str(path), {}, {"pointCurve": [[0, 0], [128, 160], [255, 255]], "superResolution": True})
    wait(qapp, lambda: len(completed) == 2 or errors)
    assert not errors
    assert [c["op"] for c in calls] == ["open", "render", "render"]
    assert calls[-1]["recipe"]["cameraRaw"] == {"pointCurve": [[0, 0], [128, 160], [255, 255]]}
    path.write_bytes(b"different sensor file")
    worker.submit(3, str(path), {}, {"exposure": .5})
    wait(qapp, lambda: len(completed) == 3 or errors)
    assert [c["op"] for c in calls][-2:] == ["open", "render"]
    worker.close()
    assert closed and not worker._thread.is_alive()


def test_close_interrupts_its_owned_native_request(qapp, monkeypatch, tmp_path):
    path = tmp_path / "sample.NEF"
    path.write_bytes(b"raw")
    started, stopped = threading.Event(), threading.Event()

    class Connection:
        def __init__(self, exe, own):
            own(self)

        def call(self, request):
            started.set()
            assert stopped.wait(2)
            raise RuntimeError("Stopped")

        def interrupt(self):
            stopped.set()

        def close(self):
            stopped.set()

    monkeypatch.setattr(raw_preview, "Connection", Connection)
    worker = raw_preview.PreviewWorker("worker.exe")
    worker.submit(1, str(path), {}, {})
    assert started.wait(2)
    worker.close()
    assert not worker._thread.is_alive()


def test_white_balance_picker_uses_cached_preview_and_one_undo_step(qapp, monkeypatch):
    calls = []

    class Worker(QObject):
        completed = Signal(int, object)
        sampled = Signal(int, object)
        maskSampled = Signal(int, object)
        coverageReady = Signal(int, object)
        failed = Signal(int, str)

        def __init__(self, path, parent):
            super().__init__(parent)

        def submit(self, revision, path, original, draft, quality, sample):
            calls.append((path, sample))
            self.sampled.emit(revision, (picture()[1], sample))

        def close(self):
            pass

    monkeypatch.setattr(raw_preview, "executable", lambda: "worker.exe")
    monkeypatch.setattr(raw_preview, "PreviewWorker", Worker)
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    monkeypatch.setattr(raw, "_work", lambda *args: pytest.fail("Picker started a full RAW export"))
    raw.pickWhiteBalance(.5, .5)
    wait(qapp, lambda: bool(calls))
    assert not raw.busy and raw.values["temperature"] > 0
    assert len(raw.history) == 1
    raw.undo()
    assert raw.values["temperature"] == raw.values["tint"] == 0
    raw.shutdown()
