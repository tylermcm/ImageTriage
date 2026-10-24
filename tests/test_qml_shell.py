"""The QML shell: theme maths, the photo model, the thumbnail service, and that Main.qml loads and shows the model."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QObject, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QImage
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.cache import DiskThumbnailCache
from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.theme import DARK, LIGHT, Theme, derive
from image_triage.qml_shell.thumbs import ThumbService

# Software scene graph: no GPU needed (effects such as blur are simply not drawn, which is fine for logic checks).
QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def luminance(color: QColor) -> float:
    def channel(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color.redF()) + 0.7152 * channel(color.greenF()) + 0.0722 * channel(color.blueF())


def contrast(a: QColor, b: QColor) -> float:
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- theme -------------------------------------------------------------------------------------------------------------------


def test_dark_surfaces_match_the_apps_slate_palette():
    colors = derive(DARK)
    # The values the existing Widgets theme was drawn with (ui/theme.py _slate_theme), within rounding.
    for token, expected in (("base", (14, 15, 17)), ("s1", (20, 21, 23)), ("s2", (24, 25, 28))):
        got = colors[token]
        assert max(abs(got.red() - expected[0]), abs(got.green() - expected[1]), abs(got.blue() - expected[2])) <= 3, (token, got.name())


@pytest.mark.parametrize("spec", [DARK, LIGHT], ids=["dark", "light"])
def test_text_meets_wcag_aa_on_every_surface(spec):
    colors = derive(spec)
    for text in ("fg", "fg2", "fg3", "accentFg"):
        for surface in ("base", "s1", "s2"):
            assert contrast(colors[text], colors[surface]) >= 4.5, (text, surface)


def test_theme_object_flips_and_notifies(qapp):
    theme = Theme()
    seen = []
    theme.changed.connect(lambda: seen.append(1))
    dark_base = theme.base
    theme.light = True
    assert theme.light and theme.base != dark_base and seen == [1]
    theme.light = True
    assert seen == [1], "no change, no signal"


# --- model ---------------------------------------------------------------------------------------------------------------------


def rows(count=40):
    out = []
    for index in range(count):
        out.append({"path": f"/p/{index:03d}.{'nef' if index % 3 == 0 else 'jpg'}", "name": f"{index:03d}.{'nef' if index % 3 == 0 else 'jpg'}",
                    "ext": "NEF" if index % 3 == 0 else "JPG", "raw": index % 3 == 0,
                    "decision": ("win", "keep", "rej", "")[index % 4], "rating": index % 6, "ai": 40 + index})
    return out


def test_model_counts_filters_and_sorts(qapp):
    model = PhotoModel()
    model.setRows(rows(40), "/p")
    assert (model.total, model.shown) == (40, 40)
    assert model.winCount == 10 and model.keepCount == 10 and model.rejCount == 10
    model.setFilter("win")
    assert model.shown == 10
    model.setFilter("all")
    model.setMinRating(4)
    assert all(model.get(i)["rating"] >= 4 for i in range(model.shown)) and 0 < model.shown < 40
    model.clearFilters()
    model.setFormat("raw")
    assert all(model.get(i)["isRaw"] for i in range(model.shown)) and model.shown == 14
    model.clearFilters()
    model.setSort("ai")
    assert model.get(0)["ai"] == 79 and model.get(39)["ai"] == 40


def test_decision_toggles_and_updates_counts_and_revision(qapp):
    model = PhotoModel()
    model.setRows(rows(8), "/p")
    revision = model.revision
    first = model.get(0)["decision"]
    model.setDecision(0, "keep")
    assert model.get(0)["decision"] == ("" if first == "keep" else "keep")
    assert model.revision > revision
    model.setDecision(0, "rej")
    model.setDecision(0, "rej")
    assert model.get(0)["decision"] == "", "choosing the same decision again clears it"
    model.setRating(1, 3)
    assert model.get(1)["rating"] == 3


# --- thumbnails ------------------------------------------------------------------------------------------------------------------


def test_thumbnail_service_makes_a_cache_file_then_serves_its_url(qapp, tmp_path):
    photo = tmp_path / "a.jpg"
    picture = QImage(800, 600, QImage.Format.Format_RGB32)
    picture.fill(QColor("teal"))
    assert picture.save(str(photo), "JPEG")
    service = ThumbService(DiskThumbnailCache(tmp_path / "cache"), workers=1)
    ready = []
    service.ready.connect(ready.append)

    assert service.url(str(photo), 240) == "", "not cached yet: nothing to show, a job is queued"
    deadline = time.time() + 10
    while not ready and time.time() < deadline:
        pump(qapp, 0.05)
    assert ready == [str(photo)]
    url = service.url(str(photo), 240)
    assert url.startswith("file://") and url.endswith(".jpg")
    local = Path(QUrl(url).toLocalFile())
    assert local.is_file() and QImage(str(local)).width() == 240


def test_a_thumbnail_nobody_wants_any_more_is_not_made(qapp, tmp_path):
    photo = tmp_path / "b.jpg"
    picture = QImage(400, 300, QImage.Format.Format_RGB32)
    picture.fill(QColor("orange"))
    picture.save(str(photo), "JPEG")
    cache = DiskThumbnailCache(tmp_path / "cache")
    service = ThumbService(cache, workers=1)
    service.pool.setMaxThreadCount(1)
    blocker = tmp_path / "block.jpg"
    picture.save(str(blocker), "JPEG")
    service.url(str(blocker), 240)  # occupies the single worker briefly
    service.url(str(photo), 240)
    service.unwant(str(photo))  # its delegate went away before the worker got to it
    service.pool.waitForDone(10000)
    assert not any(cache.root.rglob("*.jpg")) or len(list(cache.root.rglob("*.jpg"))) == 1


# --- the window -------------------------------------------------------------------------------------------------------------------


def test_main_qml_loads_and_the_grid_shows_the_model(qapp, tmp_path):
    from image_triage.qml_shell.palette_model import PaletteModel
    from PySide6.QtQml import QQmlApplicationEngine

    theme = Theme(parent=qapp)
    model = PhotoModel(parent=qapp)
    from image_triage.qml_shell.settings_model import SettingsModel

    settings = SettingsModel(memory=True, parent=qapp)
    thumbs = ThumbService(DiskThumbnailCache(tmp_path / "cache"), parent=qapp)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    context = engine.rootContext()
    context.setContextProperty("theme", theme)
    context.setContextProperty("photos", model)
    context.setContextProperty("settings", settings)
    context.setContextProperty("cmdPalette", PaletteModel(memory=True, parent=qapp))
    context.setContextProperty("thumbs", thumbs)
    context.setContextProperty("startup", "")
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    model.setRows(rows(60), "/p")
    pump(qapp, 0.6)
    grid = window.findChild(QObject, "photoGrid")
    assert grid is not None and grid.property("count") == 60
    model.setFilter("win")
    pump(qapp, 0.3)
    assert grid.property("count") == model.shown == 15
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "is not a type" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
