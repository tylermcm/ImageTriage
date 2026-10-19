from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from image_triage import photocraft_bridge as bridge
from image_triage import super_resolution_cache as cache
from image_triage.full_render import RENDERED, RenderResult

RECIPE = {"version": 1, "cameraRaw": {"exposure": 1.0, "superResolution": True}}


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photo.nef"
    path.write_bytes(b"raw bytes")
    bridge.edit_root_for(tmp_path).mkdir(parents=True, exist_ok=True)
    return str(path)


def made(tmp_path, name="work.png", fmt="png", width=200, height=100, icc=True):
    path = tmp_path / name
    path.write_bytes(b"upscaled pixels")
    return RenderResult(RENDERED, path=path, format=fmt, width=width, height=height, depth=8, icc=icc, temporary=True)


def test_a_stored_render_is_found_again_and_is_not_ours_to_delete(photo, tmp_path):
    kept = cache.store(photo, RECIPE, made(tmp_path))
    assert kept.outcome == RENDERED and not kept.temporary and kept.path.read_bytes() == b"upscaled pixels"
    assert not (tmp_path / "work.png").exists()  # the temporary was moved into the cache
    hit = cache.lookup(photo, RECIPE)
    assert hit is not None and hit.path == kept.path and (hit.width, hit.height, hit.depth, hit.icc, hit.temporary) == (200, 100, 8, True, False)
    hit.release()
    assert kept.path.exists()


def test_any_change_to_the_settings_or_the_photo_is_a_miss(photo, tmp_path):
    cache.store(photo, RECIPE, made(tmp_path))
    assert cache.lookup(photo, {"version": 1, "cameraRaw": {"exposure": 1.1, "superResolution": True}}) is None
    assert cache.lookup(photo, {"version": 1, "cameraRaw": {"superResolution": True, "exposure": 1.0}}) is not None  # order is irrelevant
    Path(photo).write_bytes(b"a different raw, longer than before")
    assert cache.lookup(photo, RECIPE) is None
    Path(photo).write_bytes(b"raw bytes")
    os.utime(photo, (1, 1))
    assert cache.lookup(photo, RECIPE) is None


def test_a_new_render_replaces_the_old_entry_even_in_another_format(photo, tmp_path):
    first = cache.store(photo, RECIPE, made(tmp_path))
    second_recipe = {"version": 1, "cameraRaw": {"exposure": 2.0, "superResolution": True}}
    second = cache.store(photo, second_recipe, made(tmp_path, "w2.tif", fmt="tiff"))
    assert second.path.suffix == ".tif" and not first.path.exists()
    assert cache.lookup(photo, RECIPE) is None and cache.lookup(photo, second_recipe) is not None


def test_a_damaged_or_incomplete_entry_is_a_miss_never_an_error(photo, tmp_path):
    kept = cache.store(photo, RECIPE, made(tmp_path))
    manifest = kept.path.with_name("photo.nef.superres.json")
    kept.path.write_bytes(b"short")  # the picture no longer matches the manifest
    assert cache.lookup(photo, RECIPE) is None
    kept.path.write_bytes(b"upscaled pixels")
    assert cache.lookup(photo, RECIPE) is not None
    manifest.write_text("{not json")
    assert cache.lookup(photo, RECIPE) is None
    manifest.write_text(json.dumps({"key": "x"}))
    assert cache.lookup(photo, RECIPE) is None
    manifest.unlink()
    assert cache.lookup(photo, RECIPE) is None


def test_nothing_is_stored_that_should_not_be(photo, tmp_path):
    work = made(tmp_path)
    not_temporary = RenderResult(RENDERED, path=work.path, format="png", width=1, height=1, depth=8, temporary=False)
    assert cache.store(photo, RECIPE, not_temporary) is not_temporary and work.path.exists()
    assert cache.lookup("C:/nowhere/missing.nef", RECIPE) is None  # a file that is not there has no signature
    assert cache.cache_key(photo, {"version": 1}) is None


def test_a_store_that_fails_leaves_the_render_usable(photo, tmp_path, monkeypatch):
    work = made(tmp_path)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(cache.shutil, "copyfile", broken)
    assert cache.store(photo, RECIPE, work) is work and work.path.exists()
    assert cache.lookup(photo, RECIPE) is None
