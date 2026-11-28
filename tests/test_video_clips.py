"""Video clips beside the photos: found by the scan on their own (never taken for a photo's edit), a poster frame and a length on their
card, a Videos filter, and left out of everything that reads pictures (the AI cull, export, best-of, the editor)."""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QSize
from PySide6.QtGui import QGuiApplication

from image_triage import formats, video

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LANDSCAPE = FIXTURES / "clip_landscape.mp4"         # 160 x 90, 2 s, 23.976 fps, with sound
PORTRAIT = FIXTURES / "clip_portrait.mov"           # 90 x 160, 1 s, 30 fps


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def photo(path: Path, color="teal") -> Path:
    from PIL import Image

    Image.new("RGB", (64, 48), color).save(path, "JPEG")
    return path


def test_clips_are_media_but_not_pictures():
    for name in ("a.MP4", "a.mov", "b.mts", "c.mkv"):
        assert formats.is_video_file_candidate(name) and formats.is_media_file_candidate(name) and not formats.is_image_file_candidate(name)
    assert formats.is_media_file_candidate("a.jpg") and not formats.is_video_file_candidate("a.jpg")
    assert not formats.is_video_file_candidate("._a.mp4"), "a macOS sidecar is no clip"


def test_a_frame_its_length_size_and_rate(qapp):
    frame = video.grab_frame(str(LANDSCAPE), 1000, longest=0)
    assert (frame.image.width(), frame.image.height()) == (160, 90)
    assert 950 <= frame.position_ms <= 1000, "the frame that holds the moment asked for"
    info = frame.info
    assert 1900 <= info.duration_ms <= 2100 and (info.width, info.height) == (160, 90) and abs(info.fps - 23.976) < 0.01 and info.has_audio
    upright = video.grab_frame(str(PORTRAIT), -1, longest=80)
    assert (upright.image.width(), upright.image.height()) == (45, 80), "a portrait clip stays upright, scaled to fit"
    assert video.grab_frame(str(FIXTURES / "missing.mp4")).image.isNull()


def test_lengths_and_timecodes_read_as_people_write_them():
    assert video.format_duration(34_000) == "0:34" and video.format_duration(605_400) == "10:05" and video.format_duration(3_723_000) == "1:02:03"
    assert video.format_timecode(12_417) == "00:12.417"


def test_what_is_known_of_a_clip_is_kept_between_runs(tmp_path, qapp):
    clip = shutil.copy(LANDSCAPE, tmp_path / "C0001.MP4")
    cache = video.ClipInfoCache(tmp_path / "clip_info.json")
    assert cache.get(clip) is None
    cache.put(clip, video.ClipInfo(2002, 160, 90, 23.976, True))
    cache.save()
    again = video.ClipInfoCache(tmp_path / "clip_info.json")
    assert again.get(clip).duration_ms == 2002
    Path(clip).write_bytes(Path(clip).read_bytes() + b"x")   # changed since: not the same clip
    assert again.get(clip) is None


def test_the_clips_service_probes_an_unknown_clip_once(qapp, tmp_path, monkeypatch):
    from image_triage.qml_shell.clips import ClipService

    monkeypatch.setattr(video, "_CACHE", video.ClipInfoCache(tmp_path / "info.json"))
    clip = str(shutil.copy(LANDSCAPE, tmp_path / "C0002.MP4"))
    service = ClipService()
    assert service.duration(clip) == ""
    end = time.monotonic() + 15
    while service.revision == 0 and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert service.duration(clip) == "0:02" and service.info(clip)["width"] == 160


# ---- the scan ---------------------------------------------------------------------------------------------------------------------------
def test_a_clip_named_like_a_photo_keeps_its_own_row(tmp_path):
    from image_triage.scanner import scan_folder

    photo(tmp_path / "GOPR0001.JPG")
    shutil.copy(LANDSCAPE, tmp_path / "GOPR0001.MP4")
    photo(tmp_path / "IMG_1.jpg")
    shutil.copy(PORTRAIT, tmp_path / "IMG_1_1.mov")
    records = {r.name: r for r in scan_folder(str(tmp_path))}
    assert set(records) == {"GOPR0001.JPG", "GOPR0001.MP4", "IMG_1.jpg", "IMG_1_1.mov"}
    assert all(not r.edited_paths and not r.variants for r in records.values()), "a clip is never a photo's edit or variant"


def test_rows_say_which_are_clips_and_the_filter_shows_them(qapp, tmp_path):
    from image_triage.qml_shell.photos import _format_ok

    rows = {"raw": {"raw": True, "video": False}, "jpg": {"raw": False, "video": False}, "clip": {"raw": False, "video": True}}
    assert [k for k, r in rows.items() if _format_ok(r, "video")] == ["clip"]
    assert [k for k, r in rows.items() if _format_ok(r, "jpg")] == ["jpg"], "JPG and other photos: no clips"
    assert [k for k, r in rows.items() if _format_ok(r, "raw")] == ["raw"]
    assert len([r for r in rows.values() if _format_ok(r, "all")]) == 3


# ---- what reads pictures leaves clips out --------------------------------------------------------------------------------------------
def test_the_picture_loader_shows_a_clips_first_frame_and_export_refuses_it(qapp, tmp_path):
    from image_triage.image_resize import load_image_for_export
    from image_triage.imaging import load_image_for_display

    image, error = load_image_for_display(str(LANDSCAPE), QSize(80, 80), prefer_embedded=True)
    assert not image.isNull() and image.width() == 80 and not error
    with pytest.raises(OSError, match="video clip"):
        load_image_for_export(str(LANDSCAPE), target_size=QSize(), ignore_orientation=False, strip_metadata=False)


def test_no_photo_metadata_is_read_from_a_clip():
    from image_triage.capture_times import read_capture_time
    from image_triage.metadata import load_capture_metadata

    assert read_capture_time(str(LANDSCAPE)) is None
    assert load_capture_metadata(str(LANDSCAPE)).path == str(LANDSCAPE)


def test_the_ai_cull_never_gets_a_clip(tmp_path):
    from image_triage.cascade.task import CascadeRunTask
    from image_triage.models import ImageRecord

    records = [ImageRecord(path=str(tmp_path / n), name=n, size=1, modified_ns=1) for n in ("a.jpg", "b.MP4", "c.NEF")]
    task = CascadeRunTask(folder=tmp_path, records=records, paths=None)
    assert [r.name for r in task.records] == ["a.jpg", "c.NEF"]


def test_export_and_best_of_take_photos_and_rename_takes_clips_too(qapp, tmp_path):
    from image_triage.qml_shell.batch_model import BatchModel
    from image_triage.qml_shell.photos import PhotoModel
    from image_triage.qml_shell.quick_export_model import QuickExportModel

    photo(tmp_path / "a.jpg")
    shutil.copy(LANDSCAPE, tmp_path / "b.mp4")
    photos = PhotoModel()
    photos.loadFolder(str(tmp_path))
    end = time.monotonic() + 10
    while (photos.loading or photos.total < 2) and time.monotonic() < end:
        qapp.processEvents()
        time.sleep(0.02)
    assert sorted((r["name"], r["video"]) for r in photos.all_rows()) == [("a.jpg", False), ("b.mp4", True)]
    quick = QuickExportModel(photos, memory=False, store=None, settings=tmp_path / "q.json")
    quick.begin("")
    assert [Path(r["path"]).name for r in quick._rows()] == ["a.jpg"]
    batch = BatchModel(photos)
    batch._mode = "export"
    assert [b["name"] for b in batch._batch()] == ["a.jpg"]
    batch._mode = "rename"
    assert sorted(b["name"] for b in batch._batch()) == ["a.jpg", "b.mp4"]


def test_the_editor_does_not_open_a_clip(qapp):
    from image_triage.qml_shell.editor_host import EditorHost

    host = EditorHost(memory=True)
    host.openPath(str(LANDSCAPE))
    assert host.path in ("", None), "nothing opened for a clip"
