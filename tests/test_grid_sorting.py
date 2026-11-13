"""Sorting by capture time, date modified, size and natural name; the Undecided filter; remembered filters."""
import os
import time

import pytest

from image_triage.capture_times import capture_or_modified, natural_key, parse_capture_time
from image_triage.qml_shell.photos import SORT_KEYS, PhotoModel
from image_triage.scanner import scan_folder
from test_qml_shell import qapp, pump  # noqa: F401


def test_names_sort_the_way_people_read_them():
    names = ["IMG_10.jpg", "img_2.JPG", "IMG_1.jpg", "IMG_2b.jpg", "a.jpg", "IMG_02.jpg"]
    assert sorted(names, key=natural_key) == ["a.jpg", "IMG_1.jpg", "img_2.JPG", "IMG_02.jpg", "IMG_2b.jpg", "IMG_10.jpg"]
    assert sorted(["b", "10", "9", "a1", ""], key=natural_key) == ["", "9", "10", "a1", "b"]   # numbers and text never raise when compared


def test_capture_time_keeps_fractions_of_a_second_and_the_time_zone():
    base = parse_capture_time("2026:05:14 10:30:00")
    assert parse_capture_time("2026:05:14 10:30:00", "5") == pytest.approx(base + 0.5)
    assert parse_capture_time("2026:05:14 10:30:00", "050") == pytest.approx(base + 0.05)
    # Two cameras at the same moment, one set to +02:00 and one to -07:00, are the same instant.
    assert parse_capture_time("2026:05:14 12:30:00", "", "+02:00") == parse_capture_time("2026:05:14 03:30:00", "", "-07:00") == base
    assert parse_capture_time("2026-05-14T10:30:00") == base
    for bad in ("", None, "0000:00:00 00:00:00", "not a date", "2026:13:40 99:00:00"):
        assert parse_capture_time(bad) is None


def test_a_file_without_exif_falls_back_to_when_it_was_changed(tmp_path):
    plain = tmp_path / "note.jpg"
    plain.write_bytes(b"not really a jpeg")
    os.utime(plain, (1_700_000_000, 1_700_000_000))
    other = tmp_path / "later.jpg"
    other.write_bytes(b"x")
    os.utime(other, (1_700_000_500, 1_700_000_500))
    assert capture_or_modified(str(other)) - capture_or_modified(str(plain)) == pytest.approx(500)
    assert capture_or_modified(str(tmp_path / "missing.jpg")) == 0.0


def _jpeg(path, stamp, mtime, size=0):
    """A real JPEG with an Exif capture time, last changed at ``mtime``."""
    from PIL import Image

    exif = Image.Exif()
    exif[0x8769] = {0x9003: stamp}      # ExifIFD / DateTimeOriginal
    Image.new("RGB", (8 + size, 8), "gray").save(path, "JPEG", exif=exif)
    os.utime(path, (mtime, mtime))


def names(model):
    return [model.get(i)["name"] for i in range(model.shown)]


def folder(tmp_path):
    shoot = tmp_path / "shoot"
    shoot.mkdir()
    # Taken in the order c, a, b; changed in the order b, c, a; a is the largest file.
    _jpeg(shoot / "c.jpg", "2026:05:14 09:00:00", 3000)
    _jpeg(shoot / "a.jpg", "2026:05:14 10:00:00", 4000, size=400)
    _jpeg(shoot / "b.jpg", "2026:05:14 11:00:00", 2000)
    return shoot


def loaded(qapp, shoot):
    model = PhotoModel(parent=qapp)
    records = scan_folder(str(shoot))
    model.setRows([{"path": r.path, "name": r.name, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1,
                    "companions": (), "record": r} for r in records], str(shoot))
    return model


def test_sort_by_date_modified_and_by_size(qapp, tmp_path):
    model = loaded(qapp, folder(tmp_path))
    assert names(model) == ["a.jpg", "b.jpg", "c.jpg"]
    model.setSort("modified")
    assert names(model) == ["b.jpg", "c.jpg", "a.jpg"]
    model.setSort("size")
    assert names(model)[0] == "a.jpg"                # largest first
    model.setSort("nonsense")                        # an order that does not exist changes nothing
    assert names(model)[0] == "a.jpg"


def test_sort_by_capture_time_reads_the_files_off_the_ui_thread_then_reorders(qapp, tmp_path):
    model = loaded(qapp, folder(tmp_path))
    model.setSort("captured")
    assert names(model) == ["b.jpg", "c.jpg", "a.jpg"]   # at once, by date modified, while the files are read
    end = time.monotonic() + 5
    while names(model) != ["c.jpg", "a.jpg", "b.jpg"] and time.monotonic() < end:
        pump(qapp, 0.02)
    assert names(model) == ["c.jpg", "a.jpg", "b.jpg"]


def test_capture_times_of_a_folder_left_meanwhile_are_dropped(qapp, tmp_path):
    model = loaded(qapp, folder(tmp_path))
    model._capture_times_arrived(model._folder_token - 1, {"x": 1.0})
    assert model._capture_times == {}


def test_the_undecided_filter_shows_what_is_left_to_go_through(qapp):
    model = PhotoModel(parent=qapp)
    rows = [{"path": f"/p/{i}.jpg", "name": f"{i}.jpg", "ext": "JPG", "raw": False, "decision": d, "rating": 0, "ai": -1}
            for i, d in enumerate(["", "win", "", "rej", "keep", "rev"])]
    model.setRows(rows, "/p")
    assert model.undecidedCount == 2
    model.setFilter("undecided")
    assert names(model) == ["0.jpg", "2.jpg"]
    model.setDecision(0, "keep")
    # Like the other filters, the list holds still while it is worked through (a slip can be taken back where it was made);
    # the count follows at once and the photo leaves when the filter is applied again.
    assert names(model) == ["0.jpg", "2.jpg"] and model.undecidedCount == 1
    model.setFilter("all")
    model.setFilter("undecided")
    assert names(model) == ["2.jpg"]


def test_every_menu_lists_the_same_orders(qapp):
    model = PhotoModel(parent=qapp)
    assert [option[0] for option in model.sortOptions] == list(SORT_KEYS) == model.sortKeys
    assert {"name", "captured", "modified", "size", "ai", "rating", "decision"} == set(SORT_KEYS)
