"""The QML shell's settings: same keys, defaults and limits as the Widgets dialog, saving and discarding, shortcuts, search, themes."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.transfer_model import TransferModel
from image_triage.qml_shell import settings_model as sm
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme, _palette_spec

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An empty settings file standing in for the user's store."""
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


def fields(model):
    return model._fields


def test_every_stored_key_exists_in_the_widgets_window():
    """The shell must read and write the keys the main window uses (so the two front ends share one configuration)."""
    window = pytest.importorskip("image_triage.window").MainWindow
    known = {getattr(window, name) for name in dir(window) if name.endswith("_KEY") and isinstance(getattr(window, name), str)}
    from image_triage.ui import native_image_layer as nil

    known |= {nil.SETTINGS_KEY, nil.CAMERA_RAW_FIRST_KEY, nil.EDITOR_ON_DEMAND_KEY, nil.EDITOR_STARTS_OPEN_KEY, nil.EDITOR_PRESTART_KEY}
    known.add("ai/cascade_rank_on_cpu")
    shell_only = {"view/accent_hue", "view/text_contrast"}
    for f in fields(SettingsModel(memory=True)).values():
        if f.key and f.key not in shell_only:
            assert f.key in known, (f.id, f.key)


def test_limits_match_the_widgets_window():
    window = pytest.importorskip("image_triage.window").MainWindow
    assert (window.AI_KEEP_TOP_PERCENT_MIN, window.AI_KEEP_TOP_PERCENT_MAX, window.AI_KEEP_TOP_PERCENT_DEFAULT) == sm.AI_KEEP_TOP
    assert (window.AI_REVIEW_BAND_PERCENT_MIN, window.AI_REVIEW_BAND_PERCENT_MAX, window.AI_REVIEW_BAND_PERCENT_DEFAULT) == sm.AI_REVIEW_BAND
    assert (window.AI_DISPUTE_WEIGHT_MIN, window.AI_DISPUTE_WEIGHT_MAX, window.AI_DISPUTE_WEIGHT_DEFAULT) == sm.AI_DISPUTE
    assert (window.AI_BASE_SCORE_WEIGHT_PERCENT_MIN, window.AI_BASE_SCORE_WEIGHT_PERCENT_MAX, window.AI_BASE_SCORE_WEIGHT_PERCENT_DEFAULT) == sm.AI_BASE_WEIGHT
    assert window.PREVIEW_PRELOAD_BATCH_SIZE_DEFAULT == sm.PRELOAD_DEFAULT and window.PREVIEW_PRELOAD_BATCH_SIZE_MAX == sm.PRELOAD_MAX
    from image_triage.ui.theme import UI_GAMMA_MAX, UI_GAMMA_MIN, AppearanceMode, appearance_profile_modes

    assert (UI_GAMMA_MIN, UI_GAMMA_MAX) == (sm.UI_GAMMA_MIN, sm.UI_GAMMA_MAX)
    assert [value for _label, value in sm.APPEARANCE_MODES] == [m.value for m in appearance_profile_modes()]
    from image_triage.models import DeleteMode

    assert [value for _label, value in sm.DELETE_MODES] == [m.value for m in DeleteMode]
    assert AppearanceMode.AUTO.value == "auto"


def test_empty_store_gives_the_documented_defaults(qapp, store):
    m = SettingsModel()
    expect = dict(updates=True, perfLog=False, aiTags=True, pocketEdits=False, smooth=False, preload=10, editorOnDemand=True, editorOpen=True,
                  editorPrestart=True, rawFirst=True, autoAdvance=True, bursts=False, stacks=False, hidden=False, oneBranch=True, watch=True,
                  restorePos=True, catalogCache=True, aiBatch=0, aiProgress=False, cpuRank=False, keepTop=10, reviewBand=10, dispute=3,
                  baseWeight=65, phash=True, hamming=6, phashCache=True, phashDiag=True, size="automatic", card="detailed", gamma=1.0, theme="slate")
    for key, value in expect.items():
        assert m.value(key) == value, key
    assert m.dirtyCount == 0


def test_stored_values_are_clamped_like_the_window_does(qapp, store):
    qs = QSettings(store, QSettings.Format.IniFormat)
    qs.setValue("ai/keep_top_percent", 99)
    qs.setValue("ai/review_band_percent", -4)
    qs.setValue("ai/dispute_weight", 17)
    qs.setValue("preview/preload_batch_size", 999)
    qs.setValue("view/ui_gamma", 9.0)
    qs.setValue("view/interface_size", "large")
    qs.setValue("view/loupe_card_style", "retired-style")
    qs.sync()
    m = SettingsModel()
    assert m.value("keepTop") == 50 and m.value("reviewBand") == 0 and m.value("dispute") == 5
    assert m.value("preload") == 128 and m.value("gamma") == 1.6
    assert m.value("size") == "spacious"          # the alias the window also accepts
    assert m.value("card") == "detailed"


def test_save_writes_the_real_keys_and_discard_restores(qapp, store):
    m = SettingsModel()
    m.set("autoAdvance", False)
    m.set("keepTop", 20)
    m.set("theme", "forest")
    assert m.dirtyCount == 3 and m.isDirty("keepTop") and m.sectionDirty("ai")
    m.discard()
    assert m.dirtyCount == 0 and m.value("keepTop") == 10
    m.set("autoAdvance", False)
    m.set("keepTop", 20)
    m.set("deleteMode", "System Trash")
    assert m.save()
    assert m.dirtyCount == 0 and m.saved
    qs = QSettings(store, QSettings.Format.IniFormat)
    assert qs.value("view/auto_advance", True, bool) is False
    assert qs.value("ai/keep_top_percent", 0, int) == 20
    assert qs.value("workflow/delete_mode", "", str) == "System Trash"
    assert SettingsModel().value("keepTop") == 20          # a new session sees it


def test_dependent_rows_and_reset_section(qapp, store):
    m = SettingsModel()
    assert m.enabled("hamming")
    m.set("phash", False)
    assert not m.enabled("hamming") and not m.enabled("phashCache")
    m.resetSection("dups")
    assert m.value("phash") is True and m.dirtyCount == 0


def test_cull_breakdown_follows_the_two_sliders(qapp, store):
    m = SettingsModel()
    assert m.text("cull") == "~10% Winner · ~10% Review · ~80% Reject"
    m.set("keepTop", 45)
    m.set("reviewBand", 30)
    assert m.text("cull") == "~45% Winner · ~30% Review · ~25% Reject"


def test_shortcut_chords_and_conflicts(qapp, store):
    m = SettingsModel()
    assert m.chord(Qt.Key.Key_N.value, (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier).value) == "Ctrl+Shift+N"
    assert m.chord(Qt.Key.Key_Shift.value, 0) == ""
    ids = [i for i in fields(m) if i.startswith("k:")]
    assert len(ids) > 30
    m.set(ids[0], "Ctrl+Q")
    m.set(ids[1], "Ctrl+Q")
    assert m.save() is False and "Ctrl+Q" in m.conflict()
    m.set(ids[1], "Ctrl+Alt+Q")
    assert m.save() is True
    from image_triage.ui.shortcuts import load_shortcut_overrides

    assert load_shortcut_overrides()[ids[0][2:]] == "Ctrl+Q"


def test_search_counts_and_page(qapp, store):
    m = SettingsModel()
    counts = m.counts("cache")
    assert counts["library"] >= 2 and counts["dups"] >= 1 and counts["general"] == 0
    page = m.page("general", "cache")
    assert {g["title"] for g in page} >= {"Library & folders", "Duplicates"}
    assert all(row["section"] for g in page for row in g["rows"])
    assert m.page("general", "")[0]["title"] == "Review behavior"


@pytest.mark.parametrize("mode", [value for _l, value in sm.APPEARANCE_MODES])
def test_every_appearance_mode_derives_a_readable_theme(qapp, mode):
    theme = Theme(parent=qapp)
    theme.applyMode(mode, -1, 90, True)
    from tests.test_qml_shell import contrast

    assert contrast(theme.fg, theme.base) >= 7, mode
    assert contrast(theme.fg3, theme.s2) >= 3, mode          # the quietest text on a raised surface (hint text, large enough for 3:1)
    theme.applyMode(mode, 75, 100, True)                      # an accent override and the maximum contrast must also derive cleanly
    assert theme.accent.isValid()


def test_slate_and_light_are_the_mockups_own_palettes():
    assert _palette_spec("slate", True)["base"] == (0.165, 0.006, 265.0)
    assert _palette_spec("light", True)["lift"] == (0.0, 0.0, 0.0)


def pump(app, seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.005)


def test_settings_window_opens_edits_and_saves_in_qml(qapp, tmp_path):
    from PySide6.QtQml import QQmlApplicationEngine
    from image_triage.cache import DiskThumbnailCache
    from image_triage.qml_shell.batch_model import BatchModel
    from image_triage.qml_shell.filters_model import FiltersModel
    from image_triage.qml_shell.palette_model import PaletteModel
    from image_triage.qml_shell.photos import PhotoModel
    from image_triage.qml_shell.thumbs import ThumbService

    theme = Theme(parent=qapp)
    settings = SettingsModel(memory=True, parent=qapp)
    photos = PhotoModel(parent=qapp)
    thumbs = ThumbService(DiskThumbnailCache(tmp_path / "cache"), parent=qapp)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    for name, obj in (("theme", theme), ("photos", photos), ("settings", settings), ("cmdPalette", PaletteModel(memory=True, parent=qapp)), ("advFilters", FiltersModel(photos, memory=True, parent=qapp)), ("batch", BatchModel(photos, memory=True, parent=qapp)), ("pocketDrop", PocketDropModel(photos, memory=True, parent=qapp)), ("handoff", HandoffModel(photos, memory=True, parent=qapp)), ("transfers", TransferModel(photos, memory=True, parent=qapp)), ("thumbs", thumbs), ("startup", "")):
        engine.rootContext().setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    pump(qapp, 0.3)
    # open, edit, save

    settings.set("autoAdvance", False)
    assert settings.dirtyCount == 1
    assert settings.save() and settings.dirtyCount == 0
    pump(qapp, 0.3)
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "is not a type" in w or "unavailable" in w], warnings
    window.close()
    engine.deleteLater()
    pump(qapp, 0.2)
