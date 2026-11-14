"""Bursts in the viewer: which burst a photo is in, [ and ] between bursts, and Keep best, reject rest."""
from __future__ import annotations

import os
import time

import pytest
from PySide6.QtCore import QObject, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from image_triage.bursts import BURST_STEP_SECONDS, burst_runs
from image_triage.qml_shell.burst_model import BurstModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.scanner import scan_folder
from test_qml_shell import _load_main, pump, qapp  # noqa: F401

# name, seconds after 09:00:00, hundredths: two bursts (3 and 4 frames) between photos taken on their own
SHOOT = (("a01", 0, 0), ("a02", 60, 10), ("a03", 60, 35), ("a04", 60, 60), ("a05", 120, 0),
         ("a06", 200, 0), ("a07", 200, 20), ("a08", 200, 40), ("a09", 201, 10), ("a10", 300, 0))
FIRST, SECOND = (1, 2, 3), (5, 6, 7, 8)


def jpeg(path, seconds=None, hundredths=0, mtime=1_700_000_000):
    """A real JPEG, with an Exif capture time when `seconds` is given, last changed at `mtime`."""
    from PIL import Image

    exif = Image.Exif()
    if seconds is not None:
        stamp = f"2026:05:14 09:{seconds // 60:02d}:{seconds % 60:02d}"
        exif[0x8769] = {0x9003: stamp, 0x9291: f"{hundredths:02d}"}      # DateTimeOriginal, SubSecTimeOriginal
    Image.new("RGB", (240, 160), (40, 90, 150)).save(path, "JPEG", exif=exif)
    os.utime(path, (mtime, mtime))


def shoot(tmp_path):
    folder = tmp_path / "shoot"
    folder.mkdir()
    for index, (name, seconds, hundredths) in enumerate(SHOOT):
        jpeg(folder / f"{name}.jpg", seconds, hundredths, mtime=1_700_000_000 + index * 1000)
    return folder


def rows_of(folder):
    return [{"path": r.path, "name": r.name, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1, "companions": (), "record": r}
            for r in scan_folder(str(folder))]


def wait_for(app, condition, seconds=8.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end and not condition():
        pump(app, 0.02)
    return condition()


# ---- the rule ----------------------------------------------------------------------------------------------------------
def test_frames_close_in_time_are_one_burst_whatever_order_they_are_given_in():
    assert burst_runs([0.0, 60.1, 60.35, 60.6, 120.0]) == [(1, 2, 3)]
    assert burst_runs([60.6, 0.0, 60.1, 120.0, 60.35]) == [(2, 4, 0)]              # each burst in the order taken
    assert burst_runs([0.0, None, 0.5, None]) == [(0, 2)]                          # a photo with no capture time is in no burst
    assert burst_runs([]) == [] and burst_runs([5.0]) == [] and burst_runs([None, None]) == []


def test_a_long_burst_stays_one_burst_and_a_pause_ends_it():
    times = [i * 0.1 for i in range(80)]                                           # eight seconds at ten frames a second
    assert burst_runs(times) == [tuple(range(80))]
    assert burst_runs([0.0, BURST_STEP_SECONDS, 2 * BURST_STEP_SECONDS + 0.01]) == [(0, 1)]


# ---- the model ---------------------------------------------------------------------------------------------------------
@pytest.fixture
def photos(qapp, tmp_path):
    model = PhotoModel(parent=qapp)
    model.setRows(rows_of(shoot(tmp_path)), str(tmp_path / "shoot"))
    return model


def test_bursts_are_found_once_the_capture_times_are_read_off_the_ui_thread(qapp, photos):
    bursts = BurstModel(photos, parent=qapp)
    assert bursts.info(2) == {} and bursts.reading                 # asked at once: nothing is known yet, and no file was read here
    assert bursts.missing(1).startswith("Still reading")
    assert wait_for(qapp, lambda: bursts.count == 2)
    assert not bursts.reading
    assert bursts.rows_of(2) == FIRST and bursts.rows_of(8) == SECOND and bursts.rows_of(4) == ()
    assert bursts.info(2) == {"burst": 1, "place": 2, "size": 3, "label": "Burst 1 · 2 of 3"}
    assert bursts.info(8)["label"] == "Burst 2 · 4 of 4"
    assert bursts.info(0) == {} and bursts.info(99) == {}


def test_next_and_previous_go_to_the_first_photo_of_another_burst(qapp, photos):
    bursts = BurstModel(photos, parent=qapp)
    assert wait_for(qapp, lambda: bursts.count == 2)
    assert bursts.target(0, 1) == 1 and bursts.target(4, 1) == 5
    assert bursts.target(2, 1) == 5                                # from inside a burst: the next one, not this one's start
    assert bursts.target(7, -1) == 1 and bursts.target(4, -1) == 1 and bursts.target(9, -1) == 5
    assert bursts.target(9, 1) == -1 and bursts.missing(1) == "No burst after this photo"
    assert bursts.target(1, -1) == -1 and bursts.missing(-1) == "No burst before this photo"


def test_a_filter_or_another_order_gives_the_bursts_of_what_is_shown(qapp, photos):
    bursts = BurstModel(photos, parent=qapp)
    assert wait_for(qapp, lambda: bursts.count == 2)
    photos.setDecision(2, "rej")
    photos.setFilter("rej")                                        # one photo shown: no burst
    assert bursts.count == 0 and bursts.info(0) == {} and bursts.missing(1) == "No bursts among the photos shown"
    photos.setFilter("all")
    assert bursts.count == 2 and bursts.rows_of(2) == FIRST


def test_files_written_one_after_another_without_a_capture_time_are_not_a_burst(qapp, tmp_path):
    folder = tmp_path / "export"
    folder.mkdir()
    for index in range(4):
        jpeg(folder / f"e{index}.jpg", None, mtime=1_700_000_000 + index)          # a second apart, no Exif date
    model = PhotoModel(parent=qapp)
    model.setRows(rows_of(folder), str(folder))
    bursts = BurstModel(model, parent=qapp)
    assert wait_for(qapp, lambda: bool(model.capture_times()) and not bursts.reading)
    assert bursts.count == 0


def test_a_camera_file_whose_date_equals_its_file_time_is_still_in_its_burst(qapp, tmp_path):
    """An older camera writes whole seconds and the file's own time is the capture time: the two are equal, and the file is read to be sure."""
    import calendar

    folder = tmp_path / "card"
    folder.mkdir()
    for index in range(3):
        path = folder / f"c{index}.jpg"
        jpeg(path, None)
        from PIL import Image

        exif = Image.Exif()
        exif[0x8769] = {0x9003: f"2026:05:14 09:00:0{index}"}
        Image.new("RGB", (64, 48), "gray").save(path, "JPEG", exif=exif)
        wall = calendar.timegm((2026, 5, 14, 9, 0, index, 0, 0, 0))
        local = time.mktime(time.gmtime(wall))                     # the moment whose local wall-clock time is the Exif time
        os.utime(path, (local, local))
    model = PhotoModel(parent=qapp)
    model.setRows(rows_of(folder), str(folder))
    bursts = BurstModel(model, parent=qapp)
    assert wait_for(qapp, lambda: bursts.count == 1)
    assert bursts.rows_of(0) == (0, 1, 2) and not bursts.reading


# ---- the window --------------------------------------------------------------------------------------------------------
@pytest.fixture
def shell(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    context = engine.rootContext()
    model = context.contextProperty("photos")
    model.setRows(rows_of(shoot(tmp_path)), str(tmp_path / "shoot"))
    window.setWidth(1440)
    window.setHeight(900)
    window.show()
    pump(qapp, 0.4)
    popout = context.contextProperty("popout")
    try:
        yield engine, window, model, popout, warnings
    finally:
        popout.shutdown()
        window.close()
        engine.deleteLater()
        pump(qapp, 0.1)


def test_the_viewer_says_which_burst_and_the_bracket_keys_jump_between_them(shell, qapp):
    engine, window, model, popout, warnings = shell
    popout.open(2)
    label = window.findChild(QQuickItem, "viewerBurstLabel")
    chip = window.findChild(QQuickItem, "viewerBurstChip")
    assert wait_for(qapp, lambda: label.property("text") == "Burst 1 · 2 of 3")
    assert chip.isVisible() and window.findChild(QQuickItem, "viewerBurstHint").isVisible()
    QTest.keyClick(window, Qt.Key.Key_BracketRight)
    pump(qapp, 0.2)
    assert popout.row == 5 and label.property("text") == "Burst 2 · 1 of 4"
    QTest.keyClick(window, Qt.Key.Key_BracketRight)                # no burst after the last: stay, and say so
    pump(qapp, 0.2)
    assert popout.row == 5 and popout.toast["text"] == "No burst after this photo"
    QTest.keyClick(window, Qt.Key.Key_Right)
    QTest.keyClick(window, Qt.Key.Key_Right)
    QTest.keyClick(window, Qt.Key.Key_BracketLeft)
    pump(qapp, 0.2)
    assert popout.row == 1
    QTest.keyClick(window, Qt.Key.Key_Left)                        # a photo taken on its own: no chip
    pump(qapp, 0.2)
    assert popout.row == 0 and not chip.isVisible()
    assert not [w for w in warnings if "Viewer.qml" in w or "Main.qml" in w], warnings


def test_the_bracket_keys_move_the_grid_and_the_palette_has_the_commands(shell, qapp):
    engine, window, model, popout, warnings = shell
    context = engine.rootContext()
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", 0)
    assert wait_for(qapp, lambda: popout.bursts.count == 2)
    QTest.keyClick(window, Qt.Key.Key_BracketRight)
    pump(qapp, 0.1)
    assert grid.property("currentIndex") == 1 and not popout.isOpen
    context.contextProperty("cmdPalette").executed.emit("review.burst_next")
    pump(qapp, 0.1)
    assert grid.property("currentIndex") == 5
    context.contextProperty("cmdPalette").executed.emit("review.burst_previous")
    pump(qapp, 0.1)
    assert grid.property("currentIndex") == 1
    palette = context.contextProperty("cmdPalette")
    palette.setShortcuts(context.contextProperty("settings").shortcutMap)
    found = {r["id"]: r for r in palette.results("burst", "main") if r.get("id")}
    assert found["review.burst_next"]["keys"] == ["]"] and found["review.burst_previous"]["keys"] == ["["]
    assert "".join(found["review.burst_next"][part] for part in ("pre", "hit", "post")) == "Next Burst"
    assert not [w for w in warnings if "Main.qml" in w], warnings
