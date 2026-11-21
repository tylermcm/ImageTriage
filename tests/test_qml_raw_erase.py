"""AI Erase in the editor: selecting, growing, filling and the stroke it adds; the fakes stand in for the models."""
import threading

import numpy as np
import pytest
from PySide6.QtGui import QImage

from image_triage import erase
from image_triage.qml_shell import raw_erase
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell_viewer import qapp, pump  # noqa: F401


def source_image(w=240, h=160):
    array = np.full((h, w, 3), 90, np.uint8)
    array[:, :, 0] = np.linspace(0, 255, w, dtype=np.uint8)
    return array


def qimage(array):
    return QImage(array.data, array.shape[1], array.shape[0], array.shape[1] * 3, QImage.Format.Format_RGB888).copy()


def test_a_picture_round_trips_through_the_array_helper():
    array = source_image(101, 53)           # an odd width, so the rows are padded
    assert np.array_equal(raw_erase.image_to_array(qimage(array)), array)


def test_the_array_does_not_borrow_the_images_memory():
    array = source_image(64, 32)             # a width whose rows are not padded: the slice would be a view
    result = raw_erase.image_to_array(qimage(array))
    import gc
    gc.collect()
    assert np.array_equal(result, array) and result.flags.owndata


def test_the_selection_tint_covers_only_the_shown_part_of_the_frame():
    mask = np.zeros((100, 200), np.uint8)
    mask[10:30, 20:60] = 255
    tint = raw_erase.overlay_image(mask, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0})
    assert (tint.width(), tint.height()) == (100, 100) and tint.pixelColor(30, 20).alpha() > 0 and tint.pixelColor(80, 80).alpha() == 0


def test_selecting_then_erasing_adds_one_patch_stroke_and_one_undo_step(qapp, monkeypatch, tmp_path):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw._full_size = [240, 160]
    eraser = raw.eraseEditor
    source = source_image()
    mask = np.zeros((160, 240), np.uint8)
    mask[60:90, 100:140] = 255
    monkeypatch.setattr("image_triage.qml_shell.raw_preview.executable", lambda: "worker.exe")
    asked = []
    monkeypatch.setattr(raw, "_enqueue", lambda action, sample=None: asked.append((action, sample)))
    eraser.select(0.5, 0.5)
    assert asked[0][0] == "maskSample" and asked[0][1]["kind"] == "erase" and eraser.busy
    # a render for some other request is ignored
    eraser.accept_source(qimage(source), {"kind": "erase", "serial": -1})
    assert eraser._source is None
    # the selection job is replaced by its result
    eraser._done.emit(eraser._serial, ("select", source, mask), "")
    pump(qapp, 0.05)
    assert eraser.hasSelection and not eraser.busy and eraser.overlay.startswith("image://rawpreview/")
    monkeypatch.setattr("image_triage.scene_mask_jobs.run_inpaint", lambda *a, **k: None)
    monkeypatch.setattr(erase, "erase_root", lambda: tmp_path)

    def fake_fill(self_, *args):
        raise AssertionError("not used")

    done = threading.Event()
    eraser._done.connect(lambda *a: done.set())
    monkeypatch.setattr(erase, "make_patch", lambda image, m, fill, grow, photo_path, root=None, grain=1.0: (
        erase.patch_stroke(str(tmp_path / "p.png"), (0.4, 0.3, 0.6, 0.6)), {"box": (0, 0, 1, 1)}))
    eraser.erase()
    for _ in range(100):
        pump(qapp, 0.05)
        if done.is_set() and not eraser.busy:
            break
    rows = raw._draft["retouch"]
    assert len(rows) == 1 and rows[0]["kind"] == "patch" and rows[0]["patch"]["region"] == [0.4, 0.3, 0.6, 0.6]
    assert not eraser.hasSelection
    raw.undo()
    assert not raw._draft.get("retouch")
    raw.shutdown()


def test_a_failed_job_reports_its_sentence_and_clears_the_busy_state(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    eraser = raw.eraseEditor
    eraser._busy, eraser._serial = True, 5
    eraser._done.emit(5, None, "Nothing was found there. Click on the object.")
    pump(qapp, 0.05)
    assert not eraser.busy and eraser.error.startswith("Nothing was found")
    raw.shutdown()


def test_grow_is_clamped_and_resets_with_the_photo(qapp):
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw._full_size = [6000, 4000]
    eraser = raw.eraseEditor
    assert eraser.grow == erase.default_grow((6000, 4000))
    eraser.setGrow(10_000)
    assert eraser.grow == erase.max_grow((6000, 4000))
    eraser.setGrow(float("nan"))
    assert eraser.grow == erase.max_grow((6000, 4000))
    raw.shutdown()


def test_zoomed_in_the_object_is_looked_for_in_the_part_on_screen(qapp, monkeypatch, tmp_path):
    """At 300% a person is a few pixels of the whole photo, and the model took the bridge they stood on: it is given the part on
    screen (and a margin) instead, and the mask it finds goes back where that part is in the photo."""
    from types import SimpleNamespace
    from PIL import Image

    seen = {}

    def fake_sam(path, points_norm, labels, progress_callback, cache_root):
        with Image.open(path) as opened:
            seen["size"] = opened.size
        seen["point"] = points_norm[0]
        found = np.zeros((opened.size[1], opened.size[0]), np.uint8)
        x, y = round(points_norm[0][0] * opened.size[0]), round(points_norm[0][1] * opened.size[1])
        found[y - 15:y + 15, x - 10:x + 10] = 255                       # a small object around the click
        out = tmp_path / "mask.png"
        Image.fromarray(found).save(out)
        return SimpleNamespace(mask_path=out)

    monkeypatch.setattr("image_triage.prompt_masks.ensure_prompt_mask", fake_sam)
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw._full_size = [1200, 1800]
    eraser = raw.eraseEditor
    results = []
    eraser._done.connect(lambda serial, result, error: results.append((result, error)))
    source = np.full((1800, 1200, 3), 120, np.uint8)
    eraser._select_job(1, source, [0.5, 0.45], threading.Event(), [0.4, 0.4, 0.6, 0.5])        # zoomed: a fifth of the width on screen
    pump(qapp, 0.05)
    (kind, picture, mask), error = results[-1]
    assert not error and seen["size"] == (756 - 444, 938 - 682)                                 # the part on screen and a margin
    ys, xs = np.nonzero(mask)
    assert mask.shape == (1800, 1200) and abs(xs.mean() - 600) < 4 and abs(ys.mean() - 810) < 6   # back at the click in the photo
    eraser._select_job(2, source, [0.5, 0.45], threading.Event(), [0.0, 0.0, 1.0, 1.0])        # the whole photo on screen: as before
    pump(qapp, 0.05)
    assert seen["size"] == (1200, 1800)
    raw.shutdown()
