"""Exposure wording, the maker-note lens, catalog reads of very large folders, the app's recycle folder staying out of the
library, and a failed thumbnail decode not being kept on disk."""
from __future__ import annotations

import tempfile
from pathlib import Path
from queue import SimpleQueue

import pytest
from PySide6.QtCore import QCoreApplication, QSize
from PySide6.QtGui import QGuiApplication, QImage

from image_triage import metadata
from image_triage.catalog import CatalogRepository
from image_triage.library_store import _catalog_folder_signature, _iter_catalog_candidate_folders
from image_triage.metadata import _format_exposure, _load_exif_capture_metadata
from image_triage.models import ImageRecord
from image_triage.scanner import is_app_recycle_directory, scan_folder


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


@pytest.mark.parametrize("seconds, text", [
    (1 / 250, "1/250s"), (1 / 8, "1/8s"), (0.25, "1/4s"),
    (0.3, "0.3s"), (0.4, "0.4s"), (0.5, "0.5s"), (0.67, "0.7s"), (0.8, "0.8s"), (0.97, "1s"),
    (1.0, "1s"), (1.3, "1.3s"), (30.0, "30s"),
])
def test_exposures_of_a_third_of_a_second_and_longer_read_in_seconds(seconds, text):
    assert _format_exposure(seconds) == text


class _Tag:
    def __init__(self, value):
        self.values = [value]
        self.printable = str(value)


def test_the_lens_comes_from_the_maker_notes_when_there_is_no_standard_tag(tmp_path, monkeypatch):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    calls = []

    def process_file(stream, details=True, **_kwargs):
        calls.append(details)
        tags = {"Image Make": _Tag("Canon")}
        if details:                                      # the library decodes maker notes only on a full read
            tags["MakerNote LensModel"] = _Tag("EF50mm f/1.8 STM")
        return tags

    monkeypatch.setattr(metadata.exifread, "process_file", process_file)

    assert _load_exif_capture_metadata(str(photo)).lens == "EF50mm f/1.8 STM"
    assert calls == [False, True]


def test_a_file_with_the_standard_lens_tag_is_read_once(tmp_path, monkeypatch):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    calls = []

    def process_file(stream, details=True, **_kwargs):
        calls.append(details)
        return {"EXIF LensModel": _Tag("RF24-70mm F2.8 L IS USM")}

    monkeypatch.setattr(metadata.exifread, "process_file", process_file)

    assert _load_exif_capture_metadata(str(photo)).lens == "RF24-70mm F2.8 L IS USM"
    assert calls == [False]


def test_review_features_load_for_a_folder_with_more_photos_than_one_statement_can_name():
    with tempfile.TemporaryDirectory(prefix="image_triage_catalog_") as temp_dir:
        repository = CatalogRepository(Path(temp_dir) / "catalog.sqlite3")
        folder = str(Path(temp_dir) / "shots")
        records = [ImageRecord(path=f"{folder}/frame_{index:05d}.jpg", name=f"frame_{index:05d}.jpg", size=1, modified_ns=1) for index in range(40_000)]
        seen = []
        import sqlite3

        real_connect = sqlite3.connect

        class Recording(sqlite3.Connection):
            def execute(self, sql, parameters=()):
                if "catalog_review_feature_cache" in sql and "IN (" in sql:
                    seen.append(len(parameters))
                return super().execute(sql, parameters)

        import image_triage.catalog.db as catalog_db

        original = catalog_db.sqlite3.connect
        catalog_db.sqlite3.connect = lambda *a, **k: real_connect(*a, factory=Recording, **k)
        try:
            loaded = repository.load_review_features(folder, records=records, cache_keys={record.path: "k" for record in records})
        finally:
            catalog_db.sqlite3.connect = original

        assert loaded == {}
        assert len(seen) == 100 and max(seen) <= 401     # every path was asked for, a few hundred at a time


def test_the_apps_recycle_folder_is_not_indexed_but_can_still_be_opened(tmp_path):
    card = tmp_path / "DCIM"
    shoot, recycle = card / "100CANON", card / "recycle bin"
    shoot.mkdir(parents=True)
    recycle.mkdir()
    (shoot / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
    (recycle / "deleted.jpg").write_bytes(b"\xff\xd8\xff\xd9")

    assert is_app_recycle_directory(str(recycle)) and not is_app_recycle_directory(str(shoot))
    assert [Path(folder).name for folder in _iter_catalog_candidate_folders(str(card))] == ["100CANON"]
    before = _catalog_folder_signature(str(card))
    (recycle / "another.jpg").write_bytes(b"x")
    assert _catalog_folder_signature(str(card)) == before
    assert [record.name for record in scan_folder(str(recycle))] == ["another.jpg", "deleted.jpg"]


def test_a_failed_decode_is_not_written_to_the_disk_cache(tmp_path):
    from image_triage.cache import ThumbnailKey
    from image_triage.thumbnails import ThumbnailRequest, ThumbnailTask

    photo = tmp_path / "broken.jpg"
    photo.write_bytes(b"not a picture")

    class Memory:
        def __init__(self):
            self.items = {}

        def get(self, key):
            return self.items.get(key)

        def put(self, key, image):
            self.items[key] = image

    class Disk:
        saved: list = []

        def load(self, key):
            return None

        def save(self, key, image):
            self.saved.append(key)

    key = ThumbnailKey(path=str(photo), modified_ns=1, file_size=13, width=64, height=64)
    request = ThumbnailRequest(path=str(photo), target_size=QSize(64, 64), key=key)
    memory, disk, results = Memory(), Disk(), SimpleQueue()

    ThumbnailTask(request, memory, disk, results, lambda _key: True).run()

    state, _key, image = results.get_nowait()
    assert state == "ready" and isinstance(image, QImage) and not image.isNull()        # the placeholder is still shown
    assert key in memory.items and disk.saved == []

