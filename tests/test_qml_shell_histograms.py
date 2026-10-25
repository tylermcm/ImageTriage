"""The inspector's histogram: the real counts of the selected photo, computed off the GUI thread and cached."""
from __future__ import annotations

import os
import threading
import time

import pytest
from PIL import Image, ImageDraw
from PySide6.QtCore import QObject

from image_triage.qml_shell import histograms
from image_triage.qml_shell.histograms import BINS, HistogramService
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def photo(tmp_path, name, fill=None, draw=None):
    image = Image.new("RGB", (160, 120), fill or (0, 0, 0))
    if draw:
        draw(ImageDraw.Draw(image))
    path = tmp_path / name
    image.save(path)
    return str(path)


def until(qapp, condition, seconds=6.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        pump(qapp, 0.02)
    assert condition()


# -- the counting -----------------------------------------------------------------------------------------------------------------

def test_a_mid_grey_photo_has_one_bar_at_the_middle(qapp, tmp_path):
    entry = histograms.compute(photo(tmp_path, "grey.png", (128, 128, 128)))
    assert all(len(entry[k]) == BINS and all(0 <= v <= 1 for v in entry[k]) for k in ("luma", "r", "g", "b"))
    assert entry["luma"].index(max(entry["luma"])) == 32 and max(entry["luma"]) == 1.0
    assert entry["r"].index(1.0) == entry["g"].index(1.0) == entry["b"].index(1.0) == 32


def test_the_channels_follow_the_colours_of_the_photo(qapp, tmp_path):
    entry = histograms.compute(photo(tmp_path, "red.png", (230, 20, 20)))
    assert entry["r"].index(1.0) > 50                                      # red is high
    assert entry["g"].index(max(entry["g"])) < 8 and entry["b"].index(max(entry["b"])) < 8      # green and blue are low


def test_a_photo_pressed_against_the_edge_does_not_flatten_the_rest(qapp, tmp_path):
    def draw(d):                                                          # mostly pure black, and a band of mid tones
        d.rectangle([0, 0, 159, 99], fill=(0, 0, 0))
        d.rectangle([0, 100, 159, 119], fill=(100, 100, 100))

    entry = histograms.compute(photo(tmp_path, "edge.png", (0, 0, 0), draw))
    assert entry["luma"][0] == 1.0 and entry["luma"][25] == 1.0            # the black spike is capped, and the tones are as tall as the scale allows


def test_the_summary_is_the_widgets_sentence(qapp, tmp_path):
    assert histograms.compute(photo(tmp_path, "white.png", (255, 255, 255)))["summary"].startswith("Overexposed")
    assert histograms.compute(photo(tmp_path, "black.png", (0, 0, 0)))["summary"].startswith("Underexposed")


def test_a_file_that_is_not_an_image_has_no_histogram(qapp, tmp_path):
    path = tmp_path / "notes.jpg"
    path.write_bytes(b"not a picture")
    assert histograms.compute(str(path)) is None


# -- the service -------------------------------------------------------------------------------------------------------------------

def test_a_photo_is_counted_once_and_kept(qapp, tmp_path):
    path = photo(tmp_path, "a.png", (90, 90, 90))
    calls = []
    service = HistogramService(loader=lambda p: calls.append(p) or histograms.compute(p), parent=qapp)
    arrived = []
    service.ready.connect(arrived.append)
    assert service.get(path) == {"ready": False}
    service.request(path)
    until(qapp, lambda: path in arrived)
    entry = service.get(path)
    assert entry["ready"] and len(entry["luma"]) == BINS
    service.request(path)                                                  # asking again does not count again
    pump(qapp, 0.2)
    assert calls == [path]


def test_photos_the_person_has_passed_are_not_read(qapp, tmp_path):
    gate = threading.Event()
    calls = []

    def slow(path):
        calls.append(path)
        gate.wait(5)
        return {"luma": [0.0] * BINS, "r": [0.0] * BINS, "g": [0.0] * BINS, "b": [0.0] * BINS, "summary": ""}

    service = HistogramService(loader=slow, parent=qapp)
    paths = [photo(tmp_path, f"{n}.png", (n * 20, 10, 10)) for n in range(4)]
    service.request(paths[0])
    until(qapp, lambda: calls == [paths[0]])                                # the worker is busy with the first
    for p in paths[1:]:
        service.request(p)                                                  # the person flips on: only the last is wanted
    gate.set()
    until(qapp, lambda: service.get(paths[3])["ready"])
    assert calls == [paths[0], paths[3]]


def test_a_photo_that_cannot_be_read_is_not_tried_again_until_it_changes(qapp, tmp_path):
    calls = []
    service = HistogramService(loader=lambda p: calls.append(p), parent=qapp)             # returns None: unreadable
    path = photo(tmp_path, "b.png", (10, 10, 10))
    service.request(path)
    until(qapp, lambda: len(calls) == 1)
    pump(qapp, 0.2)
    service.request(path)
    pump(qapp, 0.2)
    assert len(calls) == 1 and not service.get(path)["ready"]
    Image.new("RGB", (32, 32), (200, 0, 0)).save(path)                      # edited in place: a new modification time
    os.utime(path, ns=(time.time_ns(), time.time_ns() + 5_000_000_000))
    service.request(path)
    until(qapp, lambda: len(calls) == 2)


def test_a_missing_photo_is_just_not_ready(qapp):
    service = HistogramService(loader=lambda p: pytest.fail("nothing to read"), parent=qapp)
    service.request("/nowhere/at/all.jpg")
    service.request("")
    pump(qapp, 0.2)
    assert service.get("/nowhere/at/all.jpg") == {"ready": False} and service.get("") == {"ready": False}


# -- the window --------------------------------------------------------------------------------------------------------------------

def find(window, name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def test_the_preview_histogram_shows_the_selected_photos_counts(qapp, tmp_path):
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    context = engine.rootContext()
    paths = [photo(tmp_path, f"p{n}.png", (40 + n * 80, 90, 160)) for n in range(3)]
    service = context.contextProperty("histograms")
    service._loader = histograms.compute
    context.contextProperty("photos").setRows([{"path": p, "name": f"p{n}", "ext": "PNG", "raw": False, "decision": "", "rating": 0, "ai": 50} for n, p in enumerate(paths)], str(tmp_path))
    pump(qapp, 0.6)
    chart = find(window, "histogramPreview")
    assert chart is not None
    assert chart.childItems()                                              # the drawing is the chart's child (a property called `data` would take its place)
    until(qapp, lambda: chart.property("has"))
    first = list(chart.property("counts")["luma"])
    assert len(first) == BINS and max(first) == 1.0
    window.findChild(QObject, "photoGrid").setProperty("currentIndex", 2)    # another photo: another histogram
    until(qapp, lambda: chart.property("has") and list(chart.property("counts")["luma"]) != first)
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
