import os
import sys
import threading
import time
import types

import pytest

from image_triage import photocraft_raw_source as source


@pytest.fixture(autouse=True)
def fake_rawpy(monkeypatch):
    unpacks = []

    class FakeRaw:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def imread(path):
        unpacks.append(path)
        time.sleep(getattr(imread, "delay", 0))
        return FakeRaw()

    fake = types.SimpleNamespace(__version__="0", imread=imread)
    monkeypatch.setitem(sys.modules, "rawpy", fake)
    monkeypatch.setattr(source, "_write_dng", lambda file, raw: file.write(b"x" * 1000))
    monkeypatch.setattr(source, "_stats", {"hits": 0, "misses": 0, "joined": 0, "unpack_ms": 0.0})
    monkeypatch.setattr(source, "_inflight", {})
    fake.unpacks = unpacks
    fake.imread = imread
    return fake


def photo(tmp_path, name="a.NEF", size=10):
    path = tmp_path / name
    path.write_bytes(b"n" * size)
    return path


def test_revisiting_a_photo_reuses_the_unpacked_sensor(tmp_path, fake_rawpy):
    cache = tmp_path / "cache"
    path = photo(tmp_path)
    first = source.materialize_sensor_dng(str(path), cache)
    second = source.materialize_sensor_dng(str(path), cache)
    assert first == second
    assert len(fake_rawpy.unpacks) == 1
    assert source.cache_stats()["misses"] == 1 and source.cache_stats()["hits"] == 1


def test_a_changed_source_is_unpacked_again(tmp_path, fake_rawpy):
    cache = tmp_path / "cache"
    path = photo(tmp_path)
    first = source.materialize_sensor_dng(str(path), cache)
    path.write_bytes(b"changed!!")
    os.utime(path, (time.time() + 5, time.time() + 5))
    assert source.materialize_sensor_dng(str(path), cache) != first
    assert len(fake_rawpy.unpacks) == 2


def test_concurrent_requests_for_one_photo_unpack_it_once(tmp_path, fake_rawpy):
    fake_rawpy.imread.delay = 0.3
    cache = tmp_path / "cache"
    path = photo(tmp_path)
    results = []
    threads = [threading.Thread(target=lambda: results.append(source.materialize_sensor_dng(str(path), cache))) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(fake_rawpy.unpacks) == 1
    assert len(set(results)) == 1
    stats = source.cache_stats()
    assert stats["misses"] == 1 and stats["joined"] + stats["hits"] == 2


def test_trim_keeps_the_most_recent_within_the_file_bound(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    files = []
    for i in range(6):
        f = cache / f"{i}.sensor.dng"
        f.write_bytes(b"x" * 100)
        os.utime(f, (1000 + i, 1000 + i))
        files.append(f)
    source.trim_sensor_cache(cache, max_files=3, max_bytes=10_000)
    assert sorted(p.name for p in cache.glob("*.sensor.dng")) == ["3.sensor.dng", "4.sensor.dng", "5.sensor.dng"]


def test_trim_respects_the_byte_bound_and_never_evicts_the_current_file(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    old = cache / "old.sensor.dng"
    new = cache / "new.sensor.dng"
    for f, t in ((old, 1000), (new, 2000)):
        f.write_bytes(b"x" * 600)
        os.utime(f, (t, t))
    source.trim_sensor_cache(cache, keep=old, max_files=4, max_bytes=700)
    assert old.exists()
    assert not new.exists()


def test_trim_ignores_a_missing_directory_and_other_files(tmp_path):
    source.trim_sensor_cache(tmp_path / "absent")
    cache = tmp_path / "cache"
    cache.mkdir()
    other = cache / "notes.txt"
    other.write_text("keep")
    source.trim_sensor_cache(cache, max_files=0, max_bytes=0)
    assert other.exists()
