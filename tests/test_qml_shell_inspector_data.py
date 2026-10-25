"""The inspector's Quality and AI tabs: the photo measured (`inspection.py`), and the AI's verdict read from the folder's saved report (`ai_scores.py`)."""
from __future__ import os
import annotations

import csv
import functools
import threading
import time

import numpy as np
import pytest
from PIL import Image, ImageFilter
from PySide6.QtCore import QObject, QPoint, QPointF, Qt
from PySide6.QtTest import QTest

from image_triage.ai_why import ACCEPTABLE_MIN, SHARP_MIN
from image_triage.qml_shell import inspection
from image_triage.qml_shell.ai_scores import AiScores, load_bundle
from image_triage.qml_shell.inspection import InspectionService
from image_triage.qml_shell.photos import PhotoModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401  (the same helpers and application fixture)


def until(qapp, condition, seconds=6.0):
    end = time.monotonic() + seconds
    while not condition() and time.monotonic() < end:
        pump(qapp, 0.02)
    assert condition()


def scene(width=1500, height=1000):
    """Sky, hills and a fine high-contrast texture, so focus can be judged."""
    y, x = np.mgrid[0:height, 0:width]
    base = np.stack([90 + 80 * y / height, 130 + 70 * y / height, 190 - 60 * y / height], axis=-1)
    texture = (np.sin(x / 7.0) * np.sin(y / 5.0) > 0.2).astype(float) * 70
    hills = (y > height * 0.55 + 60 * np.sin(x / 180.0))[..., None] * np.array([-70, -20, -60])
    return Image.fromarray(np.clip(base + texture[..., None] + hills, 0, 255).astype(np.uint8))


@pytest.fixture(scope="module")
def photos_on_disk(tmp_path_factory):
    folder = tmp_path_factory.mktemp("quality")
    sharp = scene()
    rng = np.random.default_rng(7)
    made = {
        "sharp": sharp,
        "soft": sharp.filter(ImageFilter.GaussianBlur(5)),
        "noisy": Image.fromarray(np.clip(np.asarray(sharp).astype(float) + rng.normal(0, 26, (1000, 1500, 1)), 0, 255).astype(np.uint8)),
        "dark": Image.fromarray((np.asarray(sharp).astype(float) * 0.18).astype(np.uint8)),
        "blown": Image.fromarray(np.clip(np.asarray(sharp).astype(float) * 0.5 + 170, 0, 255).astype(np.uint8)),
        "flat": Image.new("RGB", (800, 600), (120, 120, 120)),
    }
    out = {}
    for name, image in made.items():
        path = folder / f"{name}.jpg"
        image.save(path, quality=92)
        out[name] = str(path)
    return out


@functools.lru_cache(maxsize=None)
def measured(path):
    return inspection.compute(path)


def checks(path):
    result = measured(path)
    assert result is not None
    return {c["name"]: c for c in result["checks"]}, result


# -- the measuring -----------------------------------------------------------------------------------------------------------------

def test_a_sharp_photo_is_called_sharp_and_a_blurred_one_is_not(qapp, photos_on_disk):
    sharp, _ = checks(photos_on_disk["sharp"])
    soft, _ = checks(photos_on_disk["soft"])
    assert sharp["Focus"]["text"] == "Sharp" and sharp["Focus"]["tone"] == 0 and sharp["Focus"]["value"] >= SHARP_MIN
    assert soft["Focus"]["tone"] == 2 and soft["Focus"]["text"] == "Soft" and soft["Focus"]["value"] < ACCEPTABLE_MIN
    assert all(0 <= c["value"] <= 100 and c["tone"] in (0, 1, 2) for c in {**sharp, **soft}.values())


def test_noise_and_exposure_are_read_from_the_picture(qapp, photos_on_disk):
    noisy = checks(photos_on_disk["noisy"])[0]["Noise"]
    assert noisy["text"] == "High" and noisy["tone"] == 2
    assert checks(photos_on_disk["sharp"])[0]["Noise"]["text"] == "Low"
    assert checks(photos_on_disk["dark"])[0]["Exposure"]["text"] == "Dark"
    blown = checks(photos_on_disk["blown"])[0]["Exposure"]
    assert blown["text"] in ("Highlights clipped", "Bright") and blown["tone"] >= 1
    assert checks(photos_on_disk["sharp"])[0]["Exposure"]["text"] == "Balanced"


def test_a_featureless_photo_has_no_verdict_on_focus(qapp, photos_on_disk):
    flat, result = checks(photos_on_disk["flat"])
    assert flat["Focus"]["text"] == "Inconclusive" and flat["Focus"]["tone"] == 1
    assert any("can't be judged" in r["head"] for r in result["reasons"])


def test_the_findings_come_with_sentences_the_ai_tab_lists(qapp, photos_on_disk):
    _, sharp = checks(photos_on_disk["sharp"])
    heads = [r["head"] for r in sharp["reasons"]]
    assert "Subject is sharp." in heads and "Balanced exposure." in heads
    assert all(set(r) == {"good", "head", "detail"} for r in sharp["reasons"])
    _, noisy = checks(photos_on_disk["noisy"])
    assert any(not r["good"] and "Noise" in r["head"] for r in noisy["reasons"])
    assert sharp["summary"]


def test_a_file_that_is_not_a_picture_is_not_measured(qapp, tmp_path):
    path = tmp_path / "notes.jpg"
    path.write_bytes(b"not a picture")
    assert inspection.compute(str(path)) is None
    service = InspectionService(parent=qapp)
    service.request(str(path))
    until(qapp, lambda: service.get(str(path)).get("failed"))
    assert service.get(str(path)) == {"ready": False, "failed": True}
    assert service.get("/nowhere.jpg") == {"ready": False}


def test_the_service_measures_once_and_tells_qml(qapp, photos_on_disk):
    calls = []
    service = InspectionService(loader=lambda p: calls.append(p) or inspection.compute(p), parent=qapp)
    arrived = []
    service.ready.connect(arrived.append)
    path = photos_on_disk["sharp"]
    service.request(path)
    until(qapp, lambda: path in arrived)
    entry = service.get(path)
    assert entry["ready"] and [c["name"] for c in entry["checks"]] == ["Focus", "Exposure", "Noise", "Motion blur"]
    service.request(path)
    pump(qapp, 0.2)
    assert calls == [path]


# -- the AI's verdict -------------------------------------------------------------------------------------------------------------

NAMES = ("A", "B", "C", "D", "E", "F", "G")
REPORT = (            # name, cluster, size, rank, score, why, tags, category
    ("A", "c1", 4, 1, 0.91, "Sharpest of four near-identical frames.", "", "landscape"),
    ("B", "c1", 4, 2, 0.84, "Same scene, a hair softer.", "", "landscape"),
    ("C", "c1", 4, 3, 0.52, "Softer than its siblings.", "soft", "landscape"),
    ("D", "c1", 4, 4, 0.40, "", "", "landscape"),
    ("E", "c2", 1, 1, 0.30, "", "underexposed", "night_astro"),
    ("F", "c3", 1, 1, 0.22, "", "", ""),
)


def make_folder(tmp_path, report=True):
    folder = tmp_path / "shoot"
    folder.mkdir()
    for name in NAMES:
        Image.new("RGB", (64, 48), (80, 120, 160)).save(folder / f"{name}.jpg")
    if report:
        out = folder / ".image_triage_ai" / "ranker_report"
        out.mkdir(parents=True)
        with (out / "ranked_clusters_export.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["image_id", "file_path", "file_name", "cluster_id", "cluster_size", "rank_in_cluster", "score", "technical_score", "triggered_tags", "primary_category", "cluster_reason"])
            for index, (name, cluster, size, rank, score, why, tags, category) in enumerate(REPORT, 1):
                writer.writerow([index, str(folder / f"{name}.jpg"), f"{name}.jpg", cluster, size, rank, score, round(score * 0.9, 3), tags, category, why])
    return folder


def photo_rows(folder):
    return [{"path": str(folder / f"{n}.jpg"), "name": n, "ext": "JPG", "raw": False, "decision": "", "rating": 0, "ai": -1} for n in NAMES]


def opened(qapp, folder, loader=None):
    photos = PhotoModel(parent=qapp)
    scores = AiScores(photos, parent=qapp, loader=loader)
    photos.setRows(photo_rows(folder), str(folder))
    return photos, scores


def test_a_folder_with_a_saved_report_is_scored(qapp, tmp_path):
    folder = make_folder(tmp_path)
    photos, scores = opened(qapp, folder)
    path = lambda n: str(folder / f"{n}.jpg")
    until(qapp, lambda: scores.get(path("A")).get("has"))
    best = scores.get(path("A"))
    assert best["score"] == 100 and best["call"] == "Likely winner" and best["good"] is True
    assert best["line"] == "#1 of 4 in its group · Landscape" and best["size"] == 4
    assert any(r["head"] == "Sharpest of four near-identical frames." and r["good"] for r in best["reasons"])
    worst = scores.get(path("F"))
    assert worst["score"] == 0 and worst["call"] == "Likely reject" and worst["good"] is False and worst["line"] == "Scored on its own" and worst["group"] == []
    assert scores.get(path("E"))["line"] == "Night/Astro"
    assert {"good": False, "head": "Underexposed.", "detail": ""} in scores.get(path("E"))["reasons"]
    assert scores.get(path("G")) == {"has": False, "state": "missing"}                     # a photo that is not in the report


def test_the_grid_gets_the_real_scores_and_unscored_photos_have_none(qapp, tmp_path):
    folder = make_folder(tmp_path)
    photos, scores = opened(qapp, folder)
    until(qapp, lambda: photos.get(0)["ai"] == 100)
    by_name = {photos.get(i)["name"]: photos.get(i)["ai"] for i in range(photos.shown)}
    assert by_name["A"] == 100 and by_name["F"] == 0 and by_name["G"] == -1 and by_name["C"] == 60
    photos.setSort("ai")
    assert [photos.get(i)["name"] for i in range(3)] == ["A", "B", "C"] and photos.get(photos.shown - 1)["name"] == "G"


def test_the_group_lists_its_members_with_the_photo_among_them(qapp, tmp_path):
    folder = make_folder(tmp_path)
    photos, scores = opened(qapp, folder)
    path = lambda n: str(folder / f"{n}.jpg")
    until(qapp, lambda: scores.get(path("D")).get("has"))
    group = scores.get(path("D"))["group"]
    assert [g["path"] for g in group] == [path("A"), path("B"), path("C"), path("D")]
    assert [g["current"] for g in group] == [False, False, False, True]
    assert [g["row"] for g in group] == [photos.row_for_path(path(n)) for n in "ABCD"]
    assert scores.get(path("E"))["group"] == []
    photos.set_scope("x", "x", lambda row: row["name"] in ("A", "D"))                        # a filter hides two of them: they cannot be jumped to
    assert [g["row"] >= 0 for g in scores.get(path("D"))["group"]] == [True, False, False, True]


@pytest.mark.skipif(os.name != "nt", reason="paths differ by capitals only on Windows (os.path.normcase)")
def test_a_path_written_with_other_slashes_and_capitals_still_finds_its_photo(qapp, tmp_path):
    folder = make_folder(tmp_path)
    photos, scores = opened(qapp, folder)
    until(qapp, lambda: scores.get(str(folder / "A.jpg")).get("has"))
    assert photos.row_for_path(str(folder / "A.jpg").replace("\\", "/").upper()) == photos.row_for_path(str(folder / "A.jpg")) >= 0


def test_a_folder_that_was_never_analyzed_says_so(qapp, tmp_path):
    folder = make_folder(tmp_path, report=False)
    photos, scores = opened(qapp, folder)
    until(qapp, lambda: scores.get(str(folder / "A.jpg"))["state"] == "none")
    assert scores.get(str(folder / "A.jpg")) == {"has": False, "state": "none"}
    assert {photos.get(i)["ai"] for i in range(photos.shown)} == {-1}
    assert load_bundle(str(folder)) is None


def test_a_folder_opened_while_another_is_being_read_wins(qapp, tmp_path):
    first = make_folder(tmp_path)
    gate = threading.Event()
    from image_triage.ai_results import load_ai_bundle

    def loader(folder):
        if folder == str(first):
            gate.wait(5)
            return load_ai_bundle(first / ".image_triage_ai" / "ranker_report")
        return None

    photos = PhotoModel(parent=qapp)
    scores = AiScores(photos, parent=qapp, loader=loader)
    photos.setRows(photo_rows(first), str(first))
    assert scores.get(str(first / "A.jpg")) == {"has": False, "state": "loading"}
    other = tmp_path / "other"
    other.mkdir()
    photos.setRows([], str(other))                                                          # the person moved on
    until(qapp, lambda: scores.get("/x")["state"] == "none")
    gate.set()
    pump(qapp, 0.5)
    assert scores.get(str(first / "A.jpg")) == {"has": False, "state": "none"}              # the first folder's late answer was dropped


# -- the window -------------------------------------------------------------------------------------------------------------------

def prop(item, name):
    """A QML property as plain Python (a list built by a QML binding comes back as a QJSValue)."""
    value = item.property(name)
    return value.toVariant() if hasattr(value, "toVariant") else value


def find(window, name):
    stack = [window.contentItem()]
    while stack:
        item = stack.pop()
        if item.objectName() == name:
            return item
        stack.extend(item.childItems())
    return None


def centre(item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2))
    return QPoint(int(point.x()), int(point.y()))


def click(qapp, window, item):
    pump(qapp, 0.5)                                         # the tab's fade and the rows that have just arrived finish moving things
    QTest.mouseMove(window, centre(item))
    pump(qapp, 0.15)
    where = centre(item)                                    # where it is now, not where it was
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, where)
    pump(qapp, 0.4)


@pytest.fixture
def shell(qapp, tmp_path, photos_on_disk):
    folder = make_folder(tmp_path)
    for name, source in (("A", photos_on_disk["sharp"]), ("C", photos_on_disk["soft"]), ("E", photos_on_disk["dark"])):
        Image.open(source).save(folder / f"{name}.jpg")                                      # three of them real pictures to measure
    engine, window, warnings = _load_main(qapp, tmp_path)
    window.setWidth(1440)
    window.setHeight(900)
    context = engine.rootContext()
    context.contextProperty("inspection")._loader = inspection.compute
    photos = context.contextProperty("photos")
    scores = context.contextProperty("aiScores")
    photos.setRows(photo_rows(folder), str(folder))
    from image_triage.ai_results import load_ai_bundle

    scores._arrived(0, load_ai_bundle(folder / ".image_triage_ai" / "ranker_report"), {r["path"]: i for i, r in enumerate(photo_rows(folder))})
    pump(qapp, 0.6)
    yield window, context, find(window, "inspector"), folder, warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)


def test_the_quality_tab_shows_the_photo_measured(qapp, shell):
    window, context, inspector, folder, warnings = shell
    assert {r["text"] for r in prop(inspector, "checkRows")} == {"Measuring…"}                  # nothing yet: the four rows wait
    inspector.setProperty("tab", 1)
    until(qapp, lambda: inspector.property("facts")["ready"])
    rows_now = {r["name"]: r for r in prop(inspector, "checkRows")}
    assert rows_now["Focus"]["text"] == "Sharp" and rows_now["Noise"]["text"] == "Low"
    window.findChild(QObject, "photoGrid").setProperty("currentIndex", 2)                    # C is the blurred one
    until(qapp, lambda: inspector.property("facts")["ready"] and {r["name"]: r for r in prop(inspector, "checkRows")}["Focus"]["text"] == "Soft")
    window.findChild(QObject, "photoGrid").setProperty("currentIndex", 1)                    # B is a plain 64 px square: nothing to measure yet, then not enough to judge
    until(qapp, lambda: inspector.property("facts")["ready"])
    assert {r["name"]: r for r in prop(inspector, "checkRows")}["Focus"]["text"] == "Inconclusive"
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings


def test_nothing_is_measured_until_a_tab_that_needs_it_is_open(qapp, shell):
    window, context, inspector, folder, warnings = shell
    requested = []
    context.contextProperty("inspection")._loader = lambda p: requested.append(p)
    window.findChild(QObject, "photoGrid").setProperty("currentIndex", 3)
    pump(qapp, 0.4)
    assert requested == []                                                                   # the Overview is showing
    inspector.setProperty("tab", 2)
    until(qapp, lambda: len(requested) == 1)


def test_the_ai_tab_shows_the_verdict_why_and_the_group(qapp, shell):
    window, context, inspector, folder, warnings = shell
    inspector.setProperty("tab", 2)
    until(qapp, lambda: inspector.property("aiInfo")["has"] and inspector.property("facts")["ready"])
    info = inspector.property("aiInfo")
    assert info["call"] == "Likely winner" and info["score"] == 100
    heads = [r["head"] for r in prop(inspector, "whyRows")]
    assert "Sharpest of four near-identical frames." in heads and "Subject is sharp." in heads        # the AI's reason, then what was measured
    group = find(window, "aiGroup")
    assert group is not None and group.isVisible()
    grid = window.findChild(QObject, "photoGrid")
    click(qapp, window, find(window, "aiGroupPhoto-2"))                                      # a click on another photo of the group goes to it
    assert grid.property("currentIndex") == 2
    assert inspector.property("aiInfo")["line"].startswith("#3 of 4")
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w], warnings


def test_a_photo_without_a_group_has_none_and_a_photo_the_report_lacks_offers_to_analyze(qapp, shell):
    window, context, inspector, folder, warnings = shell
    inspector.setProperty("tab", 2)
    grid = window.findChild(QObject, "photoGrid")
    grid.setProperty("currentIndex", 5)                                                      # F: scored on its own
    until(qapp, lambda: inspector.property("aiInfo")["has"])
    assert not find(window, "aiGroup").isVisible()
    grid.setProperty("currentIndex", 6)                                                      # G: not in the report
    until(qapp, lambda: inspector.property("aiInfo")["state"] == "missing")
    pump(qapp, 0.4)
    button = find(window, "aiAnalyzeButton")
    assert button is not None and button.isVisible()
    click(qapp, window, button)
    assert context.contextProperty("cull").property("view")["window"] == "cull"
