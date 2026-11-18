"""The background save queue for RAW edits: one at a time, a photo saved again stacks on its earlier save, exports wait for it."""
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication

from image_triage.qml_shell.edit_saves import EditSaves


@pytest.fixture(scope="module")
def app():
    return QCoreApplication.instance() or QCoreApplication([])


def until(app, cond, secs=5):
    end = time.monotonic() + secs
    while not cond() and time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


def test_saves_run_in_order_and_a_second_save_of_a_photo_stacks_on_the_first(app):
    gate, calls, landed = threading.Event(), [], []

    def save(path, original, draft):
        gate.wait(5)
        calls.append((path, original, draft))
        return path + ".png"

    q = EditSaves(save)
    q.landed.connect(lambda p, d, r, e: landed.append((p, d, r, e)))
    assert q.submit("a.NEF", {}, {"exposure": 1})
    assert q.submit("b.NEF", {}, {"contrast": 5})
    assert q.submit("a.NEF", {}, {"exposure": 1, "shadows": 3})      # handed over again before the first landed
    assert not q.submit("b.NEF", {}, {"contrast": 5})                  # nothing new
    assert q.pending("a.NEF") and q.latest("a.NEF") == {"exposure": 1, "shadows": 3}
    gate.set()
    assert until(app, lambda: len(landed) == 3)
    assert [c[0] for c in calls] == ["a.NEF", "b.NEF", "a.NEF"]
    assert calls[2][1] == {"exposure": 1}                              # it is told what the first one wrote
    assert not q.pending("a.NEF") and q.saved("a.NEF") == {"exposure": 1, "shadows": 3}


def test_an_export_waits_for_the_saves_of_its_photos(app):
    gate = threading.Event()
    q = EditSaves(lambda path, original, draft: gate.wait(5) and None)
    q.submit("a.NEF", {}, {"exposure": 1})
    assert not q.wait_for(["a.NEF"], timeout=0.2)
    assert q.wait_for(["other.NEF"], timeout=0.2)
    threading.Timer(0.2, gate.set).start()
    assert q.wait_for(["a.NEF"], timeout=5)


def test_a_failed_save_is_reported_and_not_taken_as_saved(app):
    landed = []

    def save(path, original, draft):
        raise ValueError("The disk is full")

    q = EditSaves(save)
    q.landed.connect(lambda p, d, r, e: landed.append(e))
    q.submit("a.NEF", {}, {"exposure": 1})
    assert until(app, lambda: landed == ["The disk is full"])
    assert not q.pending("a.NEF") and q.saved("a.NEF") is None
