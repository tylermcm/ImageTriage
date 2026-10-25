"""The Advanced filters on photos read from a folder: file types, camera, ISO and dates from the photos' own EXIF, AI states from the saved report."""
from __future__ import annotations

import csv
import time

import pytest
from PIL import Image
from PIL.TiffImagePlugin import IFDRational
from PySide6.QtCore import QSettings

from image_triage.filtering import AIStateFilter, FileTypeFilter, RecordFilterQuery
from image_triage.models import FilterMode
from image_triage.qml_shell.ai_scores import AiScores
from image_triage.qml_shell.filter_match import FilterMatcher, rest_of, undecidable
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import pump, qapp  # noqa: F401  (the same application fixture)


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


def until(qapp, condition, seconds=8.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        pump(qapp, 0.02)
    assert condition()


def exif_photo(path, make, model, iso, focal, when, size=(600, 400)):
    exif = Image.Exif()
    exif[0x010F], exif[0x0110] = make, model
    sub = exif.get_ifd(0x8769)
    sub[0x8827], sub[0x920A], sub[0x9003] = iso, IFDRational(focal, 1), when
    sub[0xA002], sub[0xA003] = size[0], size[1]
    Image.new("RGB", size, (90, 120, 180)).save(path, exif=exif)


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "shoot"
    root.mkdir()
    exif_photo(root / "a_sony.jpg", "SONY", "ILCE-7RM5", 3200, 35, "2026:05:14 21:10:00")                     # night, high ISO, landscape
    exif_photo(root / "b_nikon.jpg", "NIKON CORPORATION", "NIKON Z 6", 100, 85, "2026:06:02 10:00:00", size=(400, 600))   # day, low ISO, portrait
    Image.new("RGB", (32, 24), (5, 5, 5)).save(root / "c_plain.png")
    Image.new("RGB", (32, 24), (5, 5, 5)).save(root / "d_scan.tif")
    (root / "e_raw.nef").write_bytes(b"not really a raw")
    return root


@pytest.fixture
def grid(qapp, store, folder):
    photos = PhotoModel(parent=qapp)
    matcher = FilterMatcher(parent=qapp)
    photos.set_matcher(matcher)
    photos.loadFolder(str(folder))
    until(qapp, lambda: not photos.loading and photos.total == 5)
    return photos, FiltersModel(photos, parent=qapp)


def shown(photos):
    return sorted(photos.row_at(i)["name"] for i in range(photos.shown))


def apply(filters, **fields):
    for key, value in fields.items():
        filters.set(key, value)
    filters.apply()


# -- what is decided from the record ----------------------------------------------------------------------------------------------

def test_file_types_beyond_raw_and_jpeg_are_applied(qapp, grid):
    photos, filters = grid
    apply(filters, ftype="TIFF")
    assert shown(photos) == ["d_scan.tif"]
    filters.reset()
    apply(filters, ftype="Other")
    assert shown(photos) == ["c_plain.png"]                                          # the Widgets' categories: a PNG is not RAW, JPEG, PSD, TIFF, FITS or STL
    filters.reset()
    apply(filters, ftype="RAW")
    assert shown(photos) == ["e_raw.nef"]                                            # the old path still decides RAW and JPEG
    filters.reset()
    apply(filters, ftype="JPEG")
    assert shown(photos) == ["a_sony.jpg", "b_nikon.jpg"]


def test_the_folder_text_matches_the_photos_folder(qapp, grid):
    photos, filters = grid
    apply(filters, folder="shoot")
    assert len(shown(photos)) == 5
    filters.reset()
    apply(filters, folder="a folder that is not here")
    assert shown(photos) == []


# -- what needs the photos' own EXIF -----------------------------------------------------------------------------------------------

def test_camera_and_iso_come_from_the_photos_exif_as_they_are_read(qapp, grid):
    photos, filters = grid
    apply(filters, camera="sony")
    until(qapp, lambda: shown(photos) == ["a_sony.jpg"])                             # the reading is on a worker thread: the grid fills in when it lands
    filters.reset()
    apply(filters, isoMax="400")
    until(qapp, lambda: shown(photos) == ["b_nikon.jpg"])
    filters.reset()
    apply(filters, focMin="50")
    until(qapp, lambda: shown(photos) == ["b_nikon.jpg"])


def test_dates_and_orientation_follow_the_exif(qapp, grid):
    photos, filters = grid
    apply(filters, after="2026-06-01")
    until(qapp, lambda: shown(photos) == ["b_nikon.jpg"])
    filters.reset()
    apply(filters, before="2026-05-31")
    until(qapp, lambda: shown(photos) == ["a_sony.jpg"])
    filters.reset()
    apply(filters, orient="Portrait")
    until(qapp, lambda: shown(photos) == ["b_nikon.jpg"])


def test_a_photo_with_no_exif_does_not_match_a_camera_filter(qapp, grid):
    photos, filters = grid
    apply(filters, camera="o")                                                       # an "o" is in both makes, but only the photos that have one can match
    until(qapp, lambda: not shown(photos) or set(shown(photos)) <= {"a_sony.jpg", "b_nikon.jpg"})
    pump(qapp, 0.5)
    assert set(shown(photos)) == {"a_sony.jpg", "b_nikon.jpg"}


def test_the_match_count_in_the_dialog_follows_the_reading(qapp, grid):
    photos, filters = grid
    for key, value in (("camera", "nikon"),):
        filters.set(key, value)
    filters.apply()
    until(qapp, lambda: filters.matchCount == 1)


def test_opening_another_folder_reads_its_photos_for_the_applied_query(qapp, grid, tmp_path):
    photos, filters = grid
    apply(filters, camera="canon")
    until(qapp, lambda: shown(photos) == [])
    other = tmp_path / "other"
    other.mkdir()
    exif_photo(other / "z_canon.jpg", "Canon", "EOS R5", 400, 50, "2026:01:01 12:00:00")
    Image.new("RGB", (16, 12), (1, 1, 1)).save(other / "y_plain.png")
    photos.loadFolder(str(other))
    until(qapp, lambda: not photos.loading and photos.total == 2)
    until(qapp, lambda: shown(photos) == ["z_canon.jpg"])


# -- the AI report -----------------------------------------------------------------------------------------------------------------

def report_for(folder):
    out = folder / ".image_triage_ai" / "ranker_report"
    out.mkdir(parents=True)
    rows = (("a_sony.jpg", "c1", 2, 1, 0.9), ("b_nikon.jpg", "c1", 2, 2, 0.3), ("c_plain.png", "c2", 1, 1, 0.5))
    with (out / "ranked_clusters_export.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["image_id", "file_path", "file_name", "cluster_id", "cluster_size", "rank_in_cluster", "score"])
        for index, (name, cluster, size, rank, score) in enumerate(rows, 1):
            writer.writerow([index, str(folder / name), name, cluster, size, rank, score])


def test_ai_states_come_from_the_saved_report_and_match_nothing_without_one(qapp, store, folder):
    photos = PhotoModel(parent=qapp)
    scores = AiScores(photos, parent=qapp)
    photos.set_matcher(FilterMatcher(ai_result=scores.result_for_row, parent=qapp))
    filters = FiltersModel(photos, parent=qapp)
    photos.loadFolder(str(folder))
    until(qapp, lambda: not photos.loading and photos.total == 5)
    apply(filters, ai="Grouped")
    until(qapp, lambda: scores.get(str(folder / "a_sony.jpg")).get("state") == "none")
    assert shown(photos) == []                                                       # no report: no AI result for any photo
    report_for(folder)
    scores.reload()
    until(qapp, lambda: scores.get(str(folder / "a_sony.jpg")).get("has"))
    until(qapp, lambda: shown(photos) == ["a_sony.jpg", "b_nikon.jpg"])              # the two of the cluster of two
    filters.reset()
    apply(filters, quick="AI Top Picks")
    until(qapp, lambda: len(shown(photos)) >= 1 and "b_nikon.jpg" not in shown(photos))


# -- what is still not applied -----------------------------------------------------------------------------------------------------

def test_the_count_of_filters_not_applied_is_what_the_shell_cannot_decide(qapp, grid):
    photos, filters = grid
    assert filters.unsupported == 0
    filters.set("camera", "sony")
    filters.set("ftype", "TIFF")
    filters.set("ai", "Grouped")
    assert filters.unsupported == 0                                                  # all of these are applied now
    filters.set("tags", "wedding")
    filters.set("quick", "Duplicates")
    assert filters.unsupported == 2
    assert undecidable(RecordFilterQuery(quick_filter=FilterMode.SMART_GROUPS, ai_state=AIStateFilter.DISAGREEMENTS, min_search_confidence=0.4)) == 3


def test_what_the_query_leaves_for_the_matcher_does_not_repeat_the_models_own_part(qapp):
    query = RecordFilterQuery(quick_filter=FilterMode.WINNERS, min_rating=3, camera_text="sony", file_type=FileTypeFilter.RAW)
    rest = rest_of(query)
    assert rest.quick_filter == FilterMode.ALL and rest.min_rating == 0 and rest.camera_text == "sony" and rest.file_type == FileTypeFilter.RAW


def test_the_sample_pages_photos_keep_the_old_behaviour(qapp, store):
    photos = PhotoModel(parent=qapp)
    photos.set_matcher(FilterMatcher(parent=qapp))
    photos.setRows([{"path": f"/x/p{i}.jpg", "name": f"p{i}", "ext": "JPG", "raw": False, "decision": "", "rating": i, "ai": 50} for i in range(6)], "/x")
    filters = FiltersModel(photos, parent=qapp)
    filters.set("camera", "Sony")                                                    # no record, no EXIF: stored and shown as not applied, as before
    filters.set("rating", 3)
    filters.apply()
    assert shown(photos) == ["p3", "p4", "p5"] and filters.unsupported == 1
