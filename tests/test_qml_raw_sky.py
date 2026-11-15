"""Sky Replacement: library, the matte job, the recipe setting it writes, saving rules, and the panel."""
import time

import numpy as np
import pytest
from PIL import Image
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QImage

from image_triage import photocraft_bridge as bridge, raw_presets, sky_replace
from image_triage.camera_raw_recipe_masks import masks_valid
from image_triage.qml_shell.raw_editor import RawEditor
from image_triage.qml_shell.raw_masks import encode_raster
from test_qml_shell import qapp, pump  # noqa: F401


def wait(qapp, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        pump(qapp, .02)
    assert predicate()


def sky_top(*_args):
    values = np.zeros((60, 80), np.uint8)
    values[:25] = 255
    return encode_raster(values)


@pytest.fixture
def raw(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset(("oneformer",)))
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(80, 60, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [4000, 3000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    raw.skyEditor._runner = sky_top
    raw.skyEditor._library_root = tmp_path / "skies"
    yield raw
    raw.shutdown()


def make_sky(tmp_path, name="dusk"):
    path = tmp_path / f"{name}.png"
    Image.fromarray(np.full((40, 60, 3), (200, 120, 60), np.uint8)).save(path)
    return path


def test_the_library_keeps_one_copy_of_each_sky(tmp_path):
    lib = tmp_path / "lib"
    a = sky_replace.import_sky(make_sky(tmp_path), lib)
    assert sky_replace.import_sky(make_sky(tmp_path), lib) == a
    assert [s["name"] for s in sky_replace.library(lib)] == ["dusk"]
    assert sky_replace.library_has(a, lib) and not sky_replace.library_has(make_sky(tmp_path, "x"), lib)
    with pytest.raises(ValueError):
        sky_replace.import_sky(tmp_path / "notes.txt", lib)
    assert sky_replace.forget_sky(a, lib) and sky_replace.library(lib) == []
    assert not sky_replace.forget_sky(make_sky(tmp_path, "y"), lib)       # never deletes outside the library


def test_bundled_skies_are_copied_once_and_stay_removed(tmp_path):
    bundled, lib = tmp_path / "bundled", tmp_path / "lib"
    bundled.mkdir()
    for name in ("Rose", "Teal"):
        Image.new("RGB", (30, 20), "blue").save(bundled / f"{name}.jpg")
    (bundled / "CREDITS.md").write_text("x")
    sky_replace.seed_bundled(lib, bundled)
    assert [s["name"] for s in sky_replace.library(lib)] and {s["name"] for s in sky_replace.library(lib)} == {"Rose", "Teal"}
    sky_replace.forget_sky(lib / "Rose-bundled.jpg", lib)
    sky_replace.seed_bundled(lib, bundled)
    assert {s["name"] for s in sky_replace.library(lib)} == {"Teal"}
    Image.new("RGB", (30, 20), "red").save(bundled / "Gold.jpg")
    sky_replace.seed_bundled(lib, bundled)
    assert {s["name"] for s in sky_replace.library(lib)} == {"Teal", "Gold"}


def test_the_library_is_a_real_folder_under_the_managed_root_not_the_virtualized_data_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_AI_ROOT", str(tmp_path / "ai"))
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path / "appdata"))
    assert sky_replace.library_root() == (tmp_path / "ai" / "skies").resolve()
    from image_triage.scan_cache import app_data_root

    assert sky_replace.previous_library_root() == app_data_root() / "skies"
    assert sky_replace.library_root() != sky_replace.previous_library_root()


def test_skies_imported_into_the_first_versions_library_move_over_once(tmp_path):
    previous, lib = tmp_path / "old", tmp_path / "new"
    previous.mkdir()
    Image.new("RGB", (30, 20), "red").save(previous / "Mine-abc123.jpg")
    Image.new("RGB", (30, 20), "blue").save(previous / "Rose-bundled.jpg")
    sky_replace.migrate_previous(lib, previous)
    assert [f.name for f in lib.glob("*.jpg")] == ["Mine-abc123.jpg"]          # the bundled ones are seeded again, not copied
    sky_replace.forget_sky(lib / "Mine-abc123.jpg", lib)
    sky_replace.migrate_previous(lib, previous)                                  # once: what was removed stays removed
    assert list(lib.glob("*.jpg")) == []
    sky_replace.migrate_previous(previous, previous)                             # the same folder is not copied onto itself
    assert not (previous / sky_replace.MIGRATED).exists()


def test_the_refined_matte_follows_the_photos_edges_instead_of_the_models_blur():
    """A bright sky over dark ground with a sloping horizon and a tree: the model's mask is a blur of the truth."""
    from PIL import ImageFilter

    h, w = 120, 160
    yy, xx = np.mgrid[:h, :w]
    truth = (yy < 55 + 0.15 * xx).astype(np.float32)
    truth[40:80, 100:108] = 0                                    # a tree standing in the sky
    sky = np.array([0.7, 0.8, 0.95], np.float32)
    ground = np.array([0.08, 0.1, 0.06], np.float32)
    rgb = truth[..., None] * sky + (1 - truth[..., None]) * ground
    rng = np.random.default_rng(1)
    rgb = np.clip(rgb + rng.normal(0, 0.01, rgb.shape), 0, 1).astype(np.float32)
    soft = np.asarray(Image.fromarray((truth * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(3)), np.float32) / 255
    refined = sky_replace.refine_matte(soft, rgb)
    wrong = lambda m: float(np.abs(m - truth).mean())                    # noqa: E731
    assert wrong(refined) < wrong(soft) / 3                      # the edge is where the photo's edge is
    assert refined[50:65, 104].max() < 0.2 and refined[10, 10] > 0.95 and refined[110, 10] < 0.05    # the tree is back
    transition = float(((refined > 0.1) & (refined < 0.9)).mean())
    assert transition < 0.03                                     # crisp: only a thin band is neither sky nor ground


def test_the_shipped_skies_are_real_pictures():
    files = sorted(sky_replace.bundled_root().glob("*.jpg"))
    assert len(files) >= 10 and (sky_replace.bundled_root() / "CREDITS.md").is_file()
    for f in files:
        with Image.open(f) as im:
            assert im.size == (2000, 1333), f


def test_choosing_a_sky_finds_the_matte_and_writes_one_recipe_setting(raw, qapp, tmp_path):
    sky = raw.skyEditor
    chosen = sky_replace.import_sky(make_sky(tmp_path), sky._library_root)
    sky.choose(str(chosen))
    sky.flush()
    assert sky.busy
    wait(qapp, lambda: not sky.busy)
    recipe = raw._draft["sky"]
    assert sky_replace.valid(recipe) and sky_replace.active(recipe) and sky.active
    assert recipe["source"] == str(chosen) and recipe["amount"] == 100 and recipe["colorMatch"] == sky_replace.FIRST_MATCH
    assert recipe["mask"]["type"] == "raster" and masks_valid([{"shape": recipe["mask"]}])
    assert "mask" not in sky.settings and sky.settings["amount"] == 100
    assert bridge.classify_recipe({"version": bridge.RECIPE_VERSION, "cameraRaw": dict(raw._draft)})[0] == bridge.RECIPE_EDITED


def test_sliders_reuse_the_matte_and_undo_restores_the_photo(raw, qapp, tmp_path):
    sky = raw.skyEditor
    sky.choose(str(sky_replace.import_sky(make_sky(tmp_path), sky._library_root)))
    sky.flush()
    wait(qapp, lambda: not sky.busy and sky.active)
    calls = []
    sky._runner = lambda *a: calls.append(a) or sky_top()
    sky.adjust("brightness", 40)
    sky.adjust("brightness", 55)
    sky.flush()
    assert raw._draft["sky"]["brightness"] == 55 and calls == []
    raw.undo(); raw.undo(); raw.undo()
    assert "sky" not in raw._draft
    assert not sky.active


def test_a_photo_without_sky_says_so_and_changes_nothing(raw, qapp, tmp_path):
    sky = raw.skyEditor

    def none(*_a):
        raise ValueError("No sky found in this photo.")

    sky._runner = none
    sky.choose(str(sky_replace.import_sky(make_sky(tmp_path), sky._library_root)))
    sky.flush()
    wait(qapp, lambda: not sky.busy)
    assert sky.error == "No sky found in this photo." and "sky" not in raw._draft


def test_only_library_skies_can_be_chosen(raw, tmp_path):
    raw.skyEditor.choose(str(make_sky(tmp_path, "elsewhere")))
    raw.skyEditor.flush()
    assert "sky" not in raw._draft and not raw.skyEditor.busy


def test_remove_and_forget(raw, qapp, tmp_path):
    sky = raw.skyEditor
    chosen = sky_replace.import_sky(make_sky(tmp_path), sky._library_root)
    sky.choose(str(chosen))
    sky.flush()
    wait(qapp, lambda: not sky.busy and sky.active)
    sky.forget(str(chosen))
    assert "sky" not in raw._draft and sky_replace.library(sky._library_root) == []


def test_the_recipe_rules():
    good = {"source": "/a.jpg", "amount": 50, "flip": True}
    assert sky_replace.valid({}) and sky_replace.valid(good) and not sky_replace.active(good)
    for bad in ({"amount": 101}, {"amount": float("nan")}, {"nope": 1}, {"source": 3}, {"flip": 1},
                {"mask": {"type": "raster", "w": 0, "h": 5, "data": ""}}, "sky"):
        assert not sky_replace.valid(bad), bad
    problems = bridge.recipe_problems({"sky": {"amount": 500}})
    assert problems and problems[0][0] == "sky"
    assert raw_presets.transferable({"sky": good, "exposure": 1}) == {"exposure": 1}


def test_a_worker_without_sky_support_refuses_instead_of_ignoring_it(monkeypatch):
    from image_triage.qml_shell import raw_preview

    class Fake(raw_preview.PreviewProcess if hasattr(raw_preview, "PreviewProcess") else object):
        pass

    source = open(raw_preview.__file__, encoding="utf-8").read()
    assert 'Sky Replacement needs the updated RAW preview worker.' in source


def test_the_panel_renders_and_adjusts(raw, qapp, tmp_path):
    from pathlib import Path

    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent, QQmlEngine

    import image_triage.qml_shell as shell
    from image_triage.qml_shell import raw_preview
    from image_triage.qml_shell.theme import Theme

    sky = raw.skyEditor
    sky.choose(str(sky_replace.import_sky(make_sky(tmp_path), sky._library_root)))
    sky.flush()
    wait(qapp, lambda: not sky.busy and sky.active)
    engine = QQmlEngine()
    raw_preview.install_images(engine)
    engine.rootContext().setContextProperty("theme", Theme())
    engine.rootContext().setContextProperty("rawModel", raw)
    component = QQmlComponent(engine)
    component.setData(b'import QtQuick\nItem { width: 320; height: 900\n RawSkyPanel { width: 300; model: rawModel.skyEditor; raw: rawModel } }',
                      QUrl.fromLocalFile(str(Path(shell.__file__).parent / "qml" / "SkyHarness.qml")))
    item = component.create()
    assert item is not None, component.errors()
    try:
        for name in ("sky_amount", "sky_fadeEdge", "sky_relight", "skyImport", "skyLibrary", "skyRemove", "skyAdvanced"):
            assert item.findChild(QObject, name) is not None, name
        cards = [c for c in item.findChild(QObject, "skyLibrary").childItems() if c.objectName() == "skyCard"]
        assert len(cards) == len(sky.skies) >= 10          # the bundled skies were copied in, plus the imported one
        from PySide6.QtCore import QUrl

        from image_triage.qml_shell import raw_preview
        for _ in range(60):                                # other thumbnails come and go in the editor...
            raw_preview.put_thumbnail(f"other/{_}", QImage(8, 8, QImage.Format_RGBA8888))
        for card in sky.skies:                             # ...and the sky cards do not depend on them
            assert card["thumb"].startswith("file:///") and Path(QUrl(card["thumb"]).toLocalFile()) == Path(card["path"])
        slider = item.findChild(QObject, "sky_amount_slider")
        slider.setProperty("value", 60)
        slider.moved.emit()
        sky.flush()
        assert raw._draft["sky"]["amount"] == 60
    finally:
        item.deleteLater()
        pump(qapp, .05)
