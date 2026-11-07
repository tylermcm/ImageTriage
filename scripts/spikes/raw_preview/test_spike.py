"""Focused safety/ordering checks; real RAW timings live in benchmark.py."""
import io
from types import SimpleNamespace

import pytest
from PySide6.QtGui import QGuiApplication

from spike import Backend, Frames, Preview


def test_truncated_frame_is_an_error():
    backend = Backend.__new__(Backend)
    backend.proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(
        b'{"ok":true,"width":2,"height":2,"bytes":16}\nshort'))
    with pytest.raises(RuntimeError, match='Incomplete'):
        backend.request(dict(op='render'))


def test_oversized_frame_is_rejected_before_allocation():
    backend = Backend.__new__(Backend)
    backend.proc = SimpleNamespace(stdin=io.BytesIO(), stdout=io.BytesIO(b'{"ok":true,"bytes":999999999999}\n'))
    with pytest.raises(RuntimeError, match='Invalid frame'):
        backend.request(dict(op='render'))


def test_stale_results_never_replace_latest_and_mailbox_coalesces(monkeypatch, tmp_path):
    app = QGuiApplication.instance() or QGuiApplication([])
    monkeypatch.setattr('spike.threading.Thread.start', lambda self: None)
    model = Preview(SimpleNamespace(output=tmp_path), Frames())
    model.adjust(.5, 0, 'cpu')
    model._enqueue()
    model.adjust(1, 40, 'auto')
    model._enqueue()
    assert model._mailbox[0]['revision'] == 2
    assert model._mailbox[0]['exposure'] == 1
    model._arrived(dict(revision=1), b'', 0)
    assert model.source == '' and not model.ready
    model._timer.stop()
    del model
