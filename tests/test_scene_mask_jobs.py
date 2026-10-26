from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from image_triage import scene_mask_jobs as jobs


def save(path, array):
    Image.fromarray(array.astype(np.uint8), mode="L").save(path)
    return path


def scene(tmp_path):
    h, w = 40, 60
    sky = np.zeros((h, w)); sky[:15] = 255
    water = np.zeros((h, w)); water[30:] = 255
    trees = np.zeros((h, w)); trees[15:25, :20] = 255
    foliage = np.zeros((h, w)); foliage[15:25, 40:] = 255
    tiny = np.zeros((h, w)); tiny[0, 0] = 255  # far under the minimum coverage
    people = np.zeros((h, w)); people[10:35, 25:35] = 255
    paths = {name: save(tmp_path / f"{name}.png", a) for name, a in dict(sky=sky, water=water, trees=trees, foliage=foliage, mountains=tiny, people=people).items()}
    return SimpleNamespace(mask_paths=paths)


def test_landscape_categories_are_written_merged_and_listed_by_size(tmp_path):
    manifest = tmp_path / "out" / "manifest.json"
    payload = jobs.run_scene_masks(tmp_path / "in.png", manifest, ensure=lambda *a, **k: scene(tmp_path))
    ids = [c["id"] for c in payload["categories"]]
    assert set(ids) == {"sky", "water", "vegetation"}, "mountains is too small, people are not landscape"
    assert ids[0] in ("sky", "water") and ids[-1] == "vegetation", "largest first"
    assert json.loads(manifest.read_text()) == payload
    vegetation = np.asarray(Image.open(tmp_path / "out" / "vegetation.png"))
    assert vegetation[20, 5] == 255 and vegetation[20, 50] == 255 and vegetation[0, 0] == 0, "trees and foliage are one choice"


def test_people_become_one_refined_map_each(tmp_path):
    manifest = tmp_path / "out" / "manifest.json"
    left = np.zeros((40, 60), bool); left[10:35, 25:30] = True
    right = np.zeros((40, 60), bool); right[10:35, 30:35] = True
    people = [SimpleNamespace(mask=left, bounds=(25, 10, 30, 35)), SimpleNamespace(mask=right, bounds=(30, 10, 35, 35))]
    refined = []

    def refine(preview, mask, work, progress):
        refined.append(mask.sum())
        return mask.astype(np.float32) * 0.8 if len(refined) == 1 else None  # the second polish fails to find anything

    # The preview picture is only decoded when there are people; give it something to read.
    Image.new("RGB", (60, 40), "grey").save(tmp_path / "in.png")
    payload = jobs.run_people_masks(tmp_path / "in.png", manifest, ensure=lambda *a, **k: scene(tmp_path), split=lambda *a, **k: people, refine=refine)
    assert [p["id"] for p in payload["people"]] == ["person-1", "person-2"]
    assert payload["people"][0]["refined"] is True and payload["people"][1]["refined"] is False, "a failed polish keeps SAM's mask"
    first = np.asarray(Image.open(tmp_path / "out" / "person-1.png"))
    second = np.asarray(Image.open(tmp_path / "out" / "person-2.png"))
    assert first[20, 27] == 204 and second[20, 32] == 255 and first[20, 32] == 0
    assert payload["people"][0]["bounds"] == [round(25 / 60, 4), 0.25, 0.5, 0.875]


def test_no_people_is_an_empty_list_not_an_error(tmp_path):
    scene_without = SimpleNamespace(mask_paths={})
    payload = jobs.run_people_masks(tmp_path / "in.png", tmp_path / "out" / "manifest.json", ensure=lambda *a, **k: scene_without, split=lambda *a, **k: pytest.fail("nobody to split"))
    assert payload == {"kind": "people", "people": []}


def test_a_cancel_stops_between_steps(tmp_path):
    stop = threading.Event()
    stop.set()
    with pytest.raises(jobs.Cancelled):
        jobs.run_scene_masks(tmp_path / "in.png", tmp_path / "out" / "manifest.json", cancel=stop, ensure=lambda *a, **k: pytest.fail("must not run"))


def test_a_sleeve_next_to_a_person_is_not_another_person_but_a_distant_person_is():
    def person(x0, y0, x1, y1, shape=(100, 100)):
        mask = np.zeros(shape, bool)
        mask[y0:y1, x0:x1] = True
        return SimpleNamespace(mask=mask, bounds=(x0, y0, x1, y1))

    big = person(30, 10, 70, 90)
    sleeve = person(20, 60, 30, 70)  # touches the big one and is tiny next to it
    far = person(2, 40, 12, 60)  # small too, but well apart
    kept = jobs.drop_fragments([sleeve, far, big])
    assert kept[0] is big and not any(p is sleeve for p in kept) and any(p is far for p in kept)


def test_the_subject_uses_the_warm_host_and_falls_back_to_the_one_shot_worker(tmp_path):
    calls = []
    jobs.run_subject_mask(tmp_path / "in.png", tmp_path / "out.png", engine=lambda s, o, p: calls.append("host"), fallback=lambda *a: calls.append("fallback"))
    assert calls == ["host"]

    def broken(s, o, p):
        raise RuntimeError("host down")

    jobs.run_subject_mask(tmp_path / "in.png", tmp_path / "out.png", engine=broken, fallback=lambda *a: calls.append("fallback"))
    assert calls == ["host", "fallback"]
    stop = threading.Event()
    stop.set()
    with pytest.raises(jobs.Cancelled):
        jobs.run_subject_mask(tmp_path / "in.png", tmp_path / "out.png", cancel=stop, engine=lambda *a: calls.append("never"))
    assert calls == ["host", "fallback"]


def test_maps_are_written_no_larger_than_the_editor_accepts(tmp_path):
    h, w = 1067, 1600  # what the host really works on
    sky = np.zeros((h, w)); sky[: h // 2] = 255
    paths = {"sky": save(tmp_path / "sky.png", sky)}
    manifest = tmp_path / "out" / "manifest.json"
    payload = jobs.run_scene_masks(tmp_path / "in.png", manifest, ensure=lambda *a, **k: SimpleNamespace(mask_paths=paths))
    assert [c["id"] for c in payload["categories"]] == ["sky"]
    written = Image.open(tmp_path / "out" / "sky.png")
    assert max(written.size) == jobs.MAP_LONG_SIDE and abs(written.width / written.height - w / h) < 0.01
    assert np.asarray(written)[10, 100] == 255 and np.asarray(written)[-10, 100] == 0
