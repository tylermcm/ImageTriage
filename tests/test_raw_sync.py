"""Giving several photos the look of the one being edited."""
import json
import threading
import time

import pytest

from image_triage import light_depth, photocraft_bridge as bridge
from image_triage.qml_shell.raw_editor import DEFAULTS, RawEditor
from image_triage.qml_shell.raw_sync import sync_look
from test_qml_shell import qapp, pump  # noqa: F401

LINEAR = {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}


def wait(qapp, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        pump(qapp, .02)
    assert predicate()


def photo(folder, name, settings=None):
    path = folder / name
    path.write_bytes(b"raw " + name.encode())
    if settings is not None:
        recipe = bridge.recipe_path(str(path))
        recipe.parent.mkdir(parents=True, exist_ok=True)
        recipe.write_text(json.dumps({"version": bridge.RECIPE_VERSION, "cameraRaw": settings}))
    return str(path)


def test_each_photo_gets_the_look_and_keeps_what_is_its_own(tmp_path):
    plain = photo(tmp_path, "a.NEF")
    edited = photo(tmp_path, "b.NEF", {"contrast": 40, "masks": [{"name": "Sky", "shape": LINEAR}], "hostCrop": {"x": .1, "y": .1, "w": .5, "h": .5, "ratio": "Free"}})
    saves = []
    result = sync_look([plain, edited], {"exposure": 1.0, "vibrance": 20},
                       save=lambda path, original, draft: saves.append((path, original, draft)) or "render.png", defaults=DEFAULTS)
    assert [path for path, _render in result["saved"]] == [plain, edited] and not result["skipped"] and not result["failed"]
    assert saves[0][2] == {"exposure": 1.0, "vibrance": 20}
    _path, original, draft = saves[1]
    assert draft["masks"] == original["masks"] and draft["hostCrop"] == original["hostCrop"]
    assert draft["exposure"] == 1.0 and draft["contrast"] == 0      # the saved contrast is stated as cleared, not left out


def test_photos_that_should_not_be_touched_are_left_alone_with_a_reason(tmp_path):
    jpeg = photo(tmp_path, "c.jpg")
    project = photo(tmp_path, "d.NEF")
    bridge.sidecar_pcraft_path(project).parent.mkdir(parents=True, exist_ok=True)
    bridge.sidecar_pcraft_path(project).write_bytes(b"project")
    same = photo(tmp_path, "e.NEF", {"exposure": 1.0})
    drafted = photo(tmp_path, "f.NEF")
    broken = photo(tmp_path, "g.NEF")
    bridge.recipe_path(broken).write_text("{not json")
    result = sync_look([jpeg, project, same, drafted, broken], {"exposure": 1.0}, defaults=DEFAULTS,
                       save=lambda *args: pytest.fail("nothing here should be saved"), unsaved=lambda path: path == drafted)
    reasons = dict(result["skipped"])
    assert reasons["c.jpg"] == "not a RAW photo" and reasons["d.NEF"] == "has a PhotoCraft project"
    assert reasons["e.NEF"] == "already has these settings" and reasons["f.NEF"] == "has unsaved edits of its own"
    assert "g.NEF" in reasons and not result["saved"] and not result["failed"]
    assert bridge.recipe_path(broken).read_text() == "{not json"     # a damaged recipe is not set aside by a sync


def test_a_photo_that_fails_does_not_stop_the_rest_and_cancel_stops_before_the_next(tmp_path):
    a, b, c = (photo(tmp_path, name) for name in ("a.NEF", "b.NEF", "c.NEF"))

    def save(path, original, draft):
        if path == a:
            raise ValueError("The editor could not render it")
        return "render.png"

    result = sync_look([a, b, c], {"exposure": 1.0}, save=save, defaults=DEFAULTS)
    assert result["failed"] == [("a.NEF", "The editor could not render it")] and len(result["saved"]) == 2
    stopped = []
    result = sync_look([a, b, c], {"exposure": 1.0}, save=lambda path, *rest: stopped.append(path) or "r", defaults=DEFAULTS,
                       cancelled=lambda: len(stopped) >= 1)
    assert stopped == [a] and len(result["saved"]) == 1


def test_a_photos_light_depth_settings_survive_a_sync(tmp_path):
    import numpy as np
    from image_triage.qml_shell.raw_masks import encode_raster

    placed = light_depth.normalized({"amount": 60, "depth": 0.4})
    masks = light_depth.apply([], placed, encode_raster(np.full((4, 6), 200, dtype=np.uint8)))
    path = photo(tmp_path, "a.NEF", {"masks": masks})
    light_depth.write_sidecar(bridge.recipe_path(path), placed)
    saves = []
    sync_look([path], {"exposure": 0.5}, save=lambda p, original, draft: saves.append(draft) or "r", defaults=DEFAULTS)
    assert saves[0]["lightDepth"] == placed and saves[0]["masks"] == masks


@pytest.fixture
def raw(qapp):
    raw = RawEditor(memory=True)
    raw.open("open.NEF")
    raw.adjust("exposure", 1.5)
    yield raw
    raw.shutdown()


def test_the_editor_runs_the_sync_off_the_ui_thread_and_tells_the_grid(raw, qapp, tmp_path):
    a, b = photo(tmp_path, "a.NEF"), photo(tmp_path, "b.NEF")
    threads, gate, told = [], threading.Event(), []

    def save(path, original, draft):
        threads.append(threading.current_thread())
        gate.wait(5)
        return path + ".png"

    raw.sync._save = save
    raw.saved.connect(lambda path, render: told.append((path, render)))
    assert raw.sync.eligible([a, b, "open.NEF", str(tmp_path / "x.jpg"), a]) == 2
    raw.sync.start([a, b, "open.NEF"])
    wait(qapp, lambda: threads)
    assert raw.sync.running and raw.sync.total == 2 and threads[0] is not threading.main_thread()
    raw.sync.start([a])                                  # a second request while one runs is ignored
    gate.set()
    wait(qapp, lambda: not raw.sync.running)
    assert told == [(a, a + ".png"), (b, b + ".png")] and raw.sync.summary == "2 photos updated."
    assert raw.values["exposure"] == 1.5 and raw.dirty   # the photo being edited is not touched


def test_stop_ends_the_sync_before_the_next_photo(raw, qapp, tmp_path):
    paths = [photo(tmp_path, f"{i}.NEF") for i in range(4)]
    started, gate = threading.Event(), threading.Event()
    raw.sync._save = lambda path, *rest: (started.set(), gate.wait(5), "r")[2]
    raw.sync.start(paths)
    wait(qapp, started.is_set)
    raw.sync.cancel()
    gate.set()
    wait(qapp, lambda: not raw.sync.running)
    assert raw.sync.summary == "1 photo updated, stopped before the rest."


def test_unsaved_edits_are_found_wherever_they_are_kept(qapp, tmp_path):
    from image_triage.raw_drafts import DraftStore

    store = DraftStore(tmp_path / "drafts")
    raw = RawEditor(memory=True)
    raw._drafts = store
    raw.open("one.NEF")
    assert not raw.has_unsaved("one.NEF")
    raw.adjust("exposure", 1)
    assert raw.has_unsaved("one.NEF")
    raw.open("two.NEF")
    assert raw.has_unsaved("one.NEF") and not raw.has_unsaved("two.NEF")     # stepped away from, still unsaved
    store.put("three.NEF", {}, {"exposure": 1})
    assert store.flush() and raw.has_unsaved("three.NEF")                    # kept from an earlier session
    raw._drafts = None
    raw.shutdown()
    store.close()
