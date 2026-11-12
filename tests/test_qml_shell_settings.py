"""The QML shell's settings: same keys, defaults and limits as the Widgets dialog, saving and discarding, shortcuts, search, themes."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, Qt, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.pocketdrop_model import PocketDropModel
from qml_support import collection_models
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.transfer_model import TransferModel
from image_triage.qml_shell import settings_model as sm
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme, _palette_spec

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtGui import QGuiApplication as QApplication
    return QCoreApplication.instance() or QApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An empty settings file standing in for the user's store."""
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


def fields(model):
    return model._fields






def test_empty_store_gives_the_documented_defaults(qapp, store):
    m = SettingsModel()
    expect = dict(updates=True, perfLog=False, smooth=False, preload=10, autoAdvance=True, hidden=False, oneBranch=True, watch=True,
                  restorePos=True, cpuRank=False, contrast=90, theme="slate")
    for key, value in expect.items():
        assert m.value(key) == value, key
    assert m.dirtyCount == 0


def test_stored_values_are_clamped_like_the_window_does(qapp, store):
    qs = QSettings(store, QSettings.Format.IniFormat)
    qs.setValue("view/text_contrast", 250)
    qs.setValue("preview/preload_batch_size", 999)
    qs.setValue("view/dark_tone", "retired-tone")
    qs.sync()
    m = SettingsModel()
    assert m.value("contrast") == 100
    assert m.value("preload") == sm.PRELOAD_MAX       # the most the viewer reads ahead, not a number it would ignore
    assert m.value("darkTone") == "soft"


def test_the_preload_setting_offers_what_the_viewer_honours():
    from image_triage.qml_shell import viewer_host

    assert viewer_host.PRELOAD_LIMIT == sm.PRELOAD_MAX
    assert viewer_host.FILES_KEPT > viewer_host.PRELOAD_LIMIT + 1      # a full read-ahead does not push out the photo being looked at
    preload = next(f for s in sm.build_schema() for g in s.groups for f in g.rows if f.id == "preload")
    assert (preload.lo, preload.hi, preload.default) == (0, sm.PRELOAD_MAX, sm.PRELOAD_DEFAULT)


# Rows the model applies itself (properties the theme and the window bind, the perf logger, the AI scoring it pushes on save), and the one row whose
# reader is being connected elsewhere. Everything else must be read by another module or by the QML.
_PENDING_READER = {"updates/check_on_startup"}


def test_every_stored_setting_is_read_by_something():
    """A row that is saved and never read is a switch that does nothing. A reader is: the QSettings key in another module, the row's id asked
    of `settings` in QML, or the id used by the model itself outside the page definitions."""
    import inspect
    import re
    from pathlib import Path

    package = Path(sm.__file__).resolve().parents[1]
    others = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in package.rglob("*.py") if p.name != "settings_model.py")
    qml = "\n".join(p.read_text(encoding="utf-8", errors="ignore") for p in package.rglob("*.qml"))
    own = Path(sm.__file__).read_text(encoding="utf-8").replace(inspect.getsource(sm.build_schema), "")
    unread = []
    for section in sm.build_schema():
        for group in section.groups:
            for row in group.rows:
                if not row.key or row.key in _PENDING_READER:
                    continue
                in_qml = re.search(r'settings\.(value|set)\("%s"' % re.escape(row.id), qml) is not None and 'settings.value("%s")' % row.id in qml
                if row.key in others or in_qml or '"%s"' % row.id in own:
                    continue
                unread.append((row.id, row.key))
    assert unread == [], unread
    stored = {row.key for section in sm.build_schema() for group in section.groups for row in group.rows if row.key}
    for gone in ("workflow/show_ai_tags_in_grid", "view/ui_gamma", "view/burst_groups", "preview/editor_on_demand", "catalog/cache_enabled",
                 "ai/embed_batch_size", "ai/dispute_weight", "ai/phash_prefilter/enabled", "ai/keep_top_percent", "ai/base_score_weight_percent"):
        assert gone not in stored, gone


def test_removing_a_row_leaves_what_was_stored_for_it(qapp, store):
    qs = QSettings(store, QSettings.Format.IniFormat)
    qs.setValue("view/ui_gamma", 1.3)
    qs.setValue("ai/phash_prefilter/hamming_threshold", 9)
    qs.sync()
    m = SettingsModel()
    m.set("autoAdvance", False)
    assert m.save()
    qs = QSettings(store, QSettings.Format.IniFormat)
    assert float(qs.value("view/ui_gamma")) == 1.3 and int(qs.value("ai/phash_prefilter/hamming_threshold")) == 9


def test_save_writes_the_real_keys_and_discard_restores(qapp, store):
    m = SettingsModel()
    m.set("autoAdvance", False)
    m.set("preload", 20)
    m.set("theme", "forest")
    assert m.dirtyCount == 3 and m.isDirty("preload") and m.sectionDirty("browsing")
    m.discard()
    assert m.dirtyCount == 0 and m.value("preload") == 10
    m.set("autoAdvance", False)
    m.set("preload", 20)
    m.set("deleteMode", "System Trash")
    assert m.save()
    assert m.dirtyCount == 0 and m.saved
    qs = QSettings(store, QSettings.Format.IniFormat)
    assert qs.value("view/auto_advance", True, bool) is False
    assert qs.value("preview/preload_batch_size", 0, int) == 20
    assert qs.value("workflow/delete_mode", "", str) == "System Trash"
    assert SettingsModel().value("preload") == 20          # a new session sees it


def test_reject_appearance_toggle_is_saved_and_can_be_discarded(qapp, store):
    m = SettingsModel()
    assert m.value("dimRejects") is True
    m.set("dimRejects", False)
    m.discard()
    assert m.value("dimRejects") is True
    m.set("dimRejects", False)
    assert m.save()
    assert QSettings(store, QSettings.Format.IniFormat).value("view/dim_rejected_photos", True, bool) is False
    assert SettingsModel().value("dimRejects") is False


@pytest.fixture
def scoring_globals():
    """The classifier's and the workflow's module-level settings, put back after a test changes them."""
    from image_triage import ai_results, aiculler_workflow

    before = (ai_results._SINGLETON_KEEPER_PERCENTILE_THRESHOLD, ai_results._SINGLETON_REJECT_PERCENTILE_THRESHOLD, aiculler_workflow._BASE_SCORE_BLEND_WEIGHT)
    yield ai_results, aiculler_workflow
    ai_results.set_cull_thresholds(keeper_percentile=before[0], reject_percentile=before[1])
    aiculler_workflow.set_base_score_blend_weight(before[2])


def test_dependent_rows_and_reset_section(qapp, store):
    m = SettingsModel()
    m.set("preload", 20)
    m.set("smooth", True)
    assert m.dirtyCount == 2
    m.resetSection("browsing")
    assert m.value("preload") == 10 and m.value("smooth") is False and m.dirtyCount == 0
    assert m.enabled("preload") and m.enabled("no-such-row")            # a row with no switch it depends on is always available


def test_stored_scoring_values_still_reach_the_classifier_at_start(qapp, store, scoring_globals):
    ai_results, workflow = scoring_globals
    qs = QSettings(store, QSettings.Format.IniFormat)
    qs.setValue("ai/keep_top_percent", 20)
    qs.setValue("ai/review_band_percent", 5)
    qs.setValue("ai/base_score_weight_percent", 40)
    qs.sync()
    m = SettingsModel()
    assert m.value("keepTop") is None                                   # no row for it
    m.apply_ai_scoring()                                                # what the app does as it starts
    assert ai_results._SINGLETON_KEEPER_PERCENTILE_THRESHOLD == 80.0 and ai_results._SINGLETON_REJECT_PERCENTILE_THRESHOLD == 75.0
    assert workflow._BASE_SCORE_BLEND_WEIGHT == 0.4


def test_shortcut_chords_and_conflicts(qapp, store):
    m = SettingsModel()
    assert m.chord(Qt.Key.Key_N.value, (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier).value) == "Ctrl+Shift+N"
    assert m.chord(Qt.Key.Key_Shift.value, 0) == ""
    ids = [i for i in fields(m) if i.startswith("k:")]
    assert len(ids) == len(sm.LISTED_ACTIONS)        # the rows the shell carries out, and no others
    m.set(ids[0], "Ctrl+Q")
    m.set(ids[1], "Ctrl+Q")
    assert m.save() is False and "Ctrl+Q" in m.conflict()
    m.set(ids[1], "Ctrl+Alt+Q")
    assert m.save() is True
    from image_triage.shortcuts import load_shortcut_overrides

    assert load_shortcut_overrides()[ids[0][2:]] == "Ctrl+Q"


def test_search_counts_and_page(qapp, store):
    m = SettingsModel()
    counts = m.counts("folder")
    assert counts["library"] >= 2 and counts["general"] == 0 and "dups" not in counts
    page = m.page("general", "folder")
    assert {g["title"] for g in page} >= {"Library & folders"}
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
    from image_triage.qml_shell.photos import GridModel, PhotoModel
    from image_triage.qml_shell.thumbs import ThumbService

    theme = Theme(parent=qapp)
    settings = SettingsModel(memory=True, parent=qapp)
    photos = PhotoModel(parent=qapp)
    thumbs = ThumbService(DiskThumbnailCache(tmp_path / "cache"), parent=qapp)
    engine = QQmlApplicationEngine()
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    for name, obj in (("theme", theme), ("photos", photos), ("settings", settings), ("cmdPalette", PaletteModel(memory=True, parent=qapp)), ("advFilters", FiltersModel(photos, memory=True, parent=qapp)), ("batch", BatchModel(photos, memory=True, parent=qapp)), *collection_models(photos, qapp), ("handoff", HandoffModel(photos, memory=True, parent=qapp)), ("transfers", TransferModel(photos, memory=True, parent=qapp)), ("thumbs", thumbs), ("startup", "")):
        engine.rootContext().setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.rootContext().setContextProperty("gridModel", GridModel(photos, parent=qapp))
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
