"""The QML shell's Help menu, Help window, shortcuts guide, Browse Library, File associations and splash."""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell import reference_content as content
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.menu_model import ToastModel
from image_triage.qml_shell.palette_model import PaletteModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.reference_model import ReferenceModel, _ago, _keys
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme
from image_triage.qml_shell.thumbs import ThumbService
from image_triage.qml_shell.transfer_model import TransferModel
from qml_support import collection_models

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
NAMES = ["DSC_4201", "DSC_4203", "DSC_4207", "DSC_4210", "DSC_4214", "DSC_4219", "DSC_4222", "DSC_4226"]


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication   # the shell is a widgets application: the popout is a Widgets window
    return QCoreApplication.instance() or QApplication([])


def pump(app, seconds=0.2):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


@pytest.fixture
def photos(qapp, tmp_path):
    model = PhotoModel(parent=qapp)
    rows = []
    for name in NAMES:
        path = tmp_path / f"{name}.jpg"
        path.write_bytes(b"x" * 2000)
        rows.append({"path": str(path), "name": name, "ext": "NEF", "raw": True, "decision": "", "rating": 3, "ai": 70})
    model.setRows(rows, str(tmp_path))
    return model


@pytest.fixture
def ref(qapp, photos):
    toasts = ToastModel(parent=qapp)
    model = ReferenceModel(photos, toasts, memory=True, parent=qapp)
    model.toasts = toasts
    return model


# ---- the Help menu and the windows ---------------------------------------------------------------------------------------------


def test_the_menu_lists_what_the_mockup_lists(ref):
    ref.toggleMenu()
    items = [i for i in ref.view["menuItems"] if not i["sep"]]
    assert [i["label"] for i in items] == ["Help", "Keyboard shortcuts", "Browse Library\u2026", "File associations\u2026", "Check for updates\u2026", "About Image Triage"]
    assert [i["kbd"] for i in items[:2]] == ["F1", "Ctrl /"]
    assert ref.view["menu"] is True
    ref.closeMenu()
    assert ref.view["menu"] is False


def test_one_window_at_a_time_and_the_menu_closes_when_one_opens(ref):
    ref.toggleMenu()
    ref.open("keys")
    assert (ref.view["window"], ref.view["menu"]) == ("keys", False)
    ref.open("lib")
    assert ref.view["window"] == "lib" and "keys" not in ref.view
    ref.close()
    assert ref.view["window"] == ""


def test_f1_and_ctrl_slash_toggle(ref):
    ref.toggleHelp()
    assert ref.view["window"] == "help"
    ref.toggleHelp()
    assert ref.view["window"] == ""
    ref.toggleKeys()
    assert ref.view["window"] == "keys"
    ref.toggleKeys()
    assert ref.view["window"] == ""


def test_a_button_in_an_article_opens_its_target(ref):
    seen = []
    ref.uiRequest.connect(seen.append)
    ref.open("help")
    ref.go("keys")
    assert ref.view["window"] == "keys" and seen == []
    ref.go("settings")
    assert ref.view["window"] == "" and seen == ["settings"]
    ref.open("help")
    ref.go("coll")
    assert seen == ["settings", "coll"]


# ---- help ---------------------------------------------------------------------------------------------------------------------


def test_help_shows_every_topic_and_the_first_article(ref):
    ref.open("help")
    h = ref.view["help"]
    topics = [t for t in h["topics"] if not t["sep"]]
    assert [t["title"] for t in topics] == [t["title"] for t in content.TOPICS]
    assert any(t["sep"] for t in h["topics"])                        # the divider before About
    assert h["kind"] == "article" and h["title"] == "Getting started" and h["blocks"]


def test_searching_help_filters_topics_and_counts_hits(ref):
    ref.open("help")
    ref.setQuery("collection")
    h = ref.view["help"]
    titles = [t["title"] for t in h["topics"] if not t["sep"]]
    assert "Collections and winners" in titles and "Getting started" not in titles
    assert h["title"] == "Collections and winners"                    # the first match is shown
    ref.setQuery("zzzz nothing")
    h = ref.view["help"]
    assert h["kind"] == "none" and h["topics"] == []


def test_a_topic_can_be_picked_and_about_has_no_article(ref):
    ref.open("help")
    ref.setTopic("cull")
    assert ref.view["help"]["title"] == "Finding photos to skip"
    ref.open("about")
    h = ref.view["help"]
    assert h["kind"] == "about" and h["blocks"] == [] and [l["id"] for l in h["links"]] == ["notes", "report", "logs", "licences"]


def test_the_update_check_cycles_through_its_states(qapp, ref, monkeypatch):
    ref.openAbout()
    assert ref.view["help"]["upTitle"] == "You are up to date"
    ref.checkUpdates()
    assert ref.view["help"]["upTitle"] == "Checking for updates\u2026" and ref.view["help"]["upBtn"] == "Cancel"
    ref.checkUpdates()                                                  # cancel
    assert ref.view["help"]["upTitle"] == "You are up to date"
    ref.checkUpdates()
    pump(qapp, 1.6)
    assert ref.view["help"]["upTitle"] == "You are up to date" and "Last checked today at" in ref.view["help"]["upBody"]


def test_a_newer_version_offers_a_download_that_is_not_connected(ref):
    ref.scene("hlp-about-new")
    h = ref.view["help"]
    assert h["upTitle"] == "Version 2.4.0 is available" and h["upPrimary"] is True and h["upBtn"] == "Download"
    before = len(ref.toasts.items)
    ref.checkUpdates()
    assert len(ref.toasts.items) == before + 1 and "isn\u2019t connected" in ref.toasts.items[-1]["title"]


def test_the_real_update_check_reports_what_the_feed_says(qapp, photos, monkeypatch):
    import image_triage.updater as updater

    info = SimpleNamespace(version="9.9.9", summary="Faster.", release_notes_url="https://example.invalid/notes")
    monkeypatch.setattr(updater, "check_for_update", lambda: SimpleNamespace(update_available=True, latest=info))
    ref = ReferenceModel(photos, ToastModel(parent=qapp), memory=False, parent=qapp)
    ref.openAbout()
    assert ref.view["help"]["upTitle"].startswith("Version ") and ref.view["help"]["upBtn"] == "Check for updates"
    ref.checkUpdates()
    pump(qapp, 0.8)
    h = ref.view["help"]
    assert (h["upTitle"], h["upBody"], h["upBtn"]) == ("Version 9.9.9 is available", "Faster.", "Download")
    monkeypatch.setattr(updater, "check_for_update", lambda: (_ for _ in ()).throw(OSError("no network")))
    ref._upd = "idle"
    ref.checkUpdates()
    pump(qapp, 0.8)
    assert ref.view["help"]["upTitle"] == "Could not check for updates" and "no network" in ref.view["help"]["upBody"]


def test_links_open_the_right_places(qapp, ref, monkeypatch):
    opened = []
    monkeypatch.setattr("image_triage.qml_shell.reference_model.QDesktopServices.openUrl", lambda url: opened.append(url.toString()))
    ref.openLink("report")
    ref.openLink("notes")
    assert opened[0].endswith("/issues/new") and opened[1].endswith("/releases")
    ref.openLink("licences")                                  # the sample data: it only says what it would do
    assert ref.toasts.items[-1]["title"] == "Opens the LICENSE file" and len(opened) == 2
    ref._memory = False
    ref.openLink("licences")
    assert opened[-1].endswith("/LICENSE") and opened[-1].startswith("file:")
    monkeypatch.setattr(type(ref), "_licence_file", staticmethod(lambda: None))
    ref.openLink("licences")
    assert ref.toasts.items[-1]["title"] == "The LICENSE file was not found"


# ---- the shortcuts guide --------------------------------------------------------------------------------------------------------


def test_key_text_is_split_into_caps():
    assert _keys("Ctrl+Shift+N") == ["Ctrl", "Shift", "N"]
    assert _keys("Ctrl+,") == ["Ctrl", ","]
    assert _keys("1+\u2013+5") == ["1", "\u2013", "5"]
    assert _keys("Ctrl++") == ["Ctrl", "+"]
    assert _keys("") == []


def test_the_guide_has_the_mockups_groups_and_marks_a_changed_key(ref):
    ref.open("keys")
    k = ref.view["keys"]
    assert [c["name"] for c in k["cats"]] == ["Review", "Collections", "File", "Edit", "View", "Everywhere"]
    compare = next(r for c in k["cats"] for r in c["rows"] if r["t"] == "Compare")
    assert compare["keys"] == ["V"] and compare["changed"] is True       # the sample override
    winner = next(r for c in k["cats"] for r in c["rows"] if r["t"] == "Mark a winner")
    assert winner["keys"] == ["W"] and winner["changed"] is False
    assert k["count"] == sum(len(c["rows"]) for c in k["cats"])


def test_searching_the_guide_matches_labels_and_key_names(ref):
    ref.open("keys")
    ref.setKeyQuery("rename")
    rows = [r["t"] for c in ref.view["keys"]["cats"] for r in c["rows"]]
    assert rows == ["Rename", "Batch rename"]
    ref.setKeyQuery("f11")
    assert [r["t"] for c in ref.view["keys"]["cats"] for r in c["rows"]] == ["Zen mode"]
    ref.setKeyQuery("qqqq")
    assert ref.view["keys"]["none"] is True and ref.view["keys"]["count"] == 0


def test_the_guide_reads_the_users_real_overrides(qapp, photos, monkeypatch):
    import image_triage.ui.shortcuts as shortcuts

    monkeypatch.setattr(shortcuts, "load_shortcut_overrides", lambda: {"undo": "Ctrl+Alt+Z", "open_folder": "Ctrl+Shift+O"})
    ref = ReferenceModel(photos, ToastModel(parent=qapp), memory=False, parent=qapp)
    ref.open("keys")
    rows = {r["t"]: r for c in ref.view["keys"]["cats"] for r in c["rows"]}
    assert rows["Undo"]["keys"] == ["Ctrl", "Alt", "Z"] and rows["Undo"]["changed"]
    assert rows["Open folder"]["keys"] == ["Ctrl", "Shift", "O"]
    assert rows["Settings"]["keys"] == ["Ctrl", ","] and not rows["Settings"]["changed"]


def test_every_registry_action_the_guide_names_exists():
    from image_triage.ui.shortcuts import SHORTCUT_REGISTRY

    known = {name for name, _c, _d, _n in SHORTCUT_REGISTRY}
    named = {attr for _cat, rows in content.KEY_GROUPS for _l, attr, _k in rows if attr}
    assert named <= known, named - known


# ---- Browse Library -------------------------------------------------------------------------------------------------------------


def test_the_library_lists_roots_stats_and_results(ref):
    ref.open("lib")
    lib = ref.view["lib"]
    assert [s["l"] for s in lib["stats"]] == ["Indexed folders", "Image bundles", "Matches shown"]
    assert lib["stats"][0]["n"] == "3" and lib["stats"][1]["n"] == "4,812"
    assert [c["label"] for c in lib["scopes"]] == ["All indexed folders", "Georgia trip", "Iceland", "Wedding 2026"]
    assert len(lib["cards"]) == len(NAMES) and lib["cards"][0]["name"] == "DSC_4201.nef"
    assert lib["show"] == f"Show {len(NAMES)} in the grid" and lib["canShow"]


def test_searching_and_scoping_the_library(ref):
    ref.open("lib")
    ref.setLibQuery("4203")
    assert [c["name"] for c in ref.view["lib"]["cards"]] == ["DSC_4203.nef"]
    ref.setLibQuery("")
    ref.setLibScope(content.SAMPLE_ROOTS[1]["path"])
    assert len(ref.view["lib"]["cards"]) == 4
    ref.setLibQuery("zzz")
    lib = ref.view["lib"]
    assert lib["noResults"] and lib["canShow"] is False and lib["show"] == "Show 0 in the grid"


def test_removing_adding_and_reindexing_a_root(ref):
    ref.open("lib")
    ref.removeRoot(content.SAMPLE_ROOTS[0]["path"])
    assert len(ref.view["lib"]["roots"]) == 2 and ref.toasts.items[-1]["title"] == "Removed from the library"
    ref.addFolder("D:/Photos/New shoot")
    roots = ref.view["lib"]["roots"]
    assert roots[-1]["name"] == "D:/Photos/New shoot" and roots[-1]["meta"].startswith("0 bundles")
    ref.reindex("D:/Photos/New shoot")
    assert "today" in ref.view["lib"]["roots"][-1]["meta"]
    for root in list(ref.view["lib"]["roots"]):
        ref.removeRoot(root["name"])
    lib = ref.view["lib"]
    assert lib["empty"] and lib["cards"] == [] and lib["foot"] == "Nothing indexed"


def test_showing_results_in_the_grid_says_it_is_not_connected(ref):
    ref.open("lib")
    ref.showInGrid()
    assert "isn\u2019t connected" in ref.toasts.items[-1]["title"]


def test_the_real_library_reads_the_store(qapp, photos, monkeypatch):
    import image_triage.library_store as store

    calls = []

    class Fake:
        roots = [SimpleNamespace(path="D:/Shoots/Iceland", indexed_record_count=3316, last_indexed_at="2020-01-01T10:00:00", last_error="", enabled=True)]

        def list_catalog_roots(self):
            return list(self.roots)

        def search_catalog(self, *, search_text="", root_path="", limit=800):
            calls.append((search_text, root_path))
            return [SimpleNamespace(path="D:/Shoots/Iceland/DSC_1.NEF")]

        def add_catalog_root(self, path):
            calls.append(("add", path))

        def remove_catalog_root(self, path):
            calls.append(("remove", path))
            return True

        def refresh_catalog(self, roots):
            calls.append(("refresh", tuple(roots)))

    monkeypatch.setattr(store, "LibraryStore", Fake)
    ref = ReferenceModel(photos, ToastModel(parent=qapp), memory=False, parent=qapp)
    ref.open("lib")
    lib = ref.view["lib"]
    assert lib["roots"][0]["name"] == "D:/Shoots/Iceland" and "3,316 bundles" in lib["roots"][0]["meta"] and lib["stats"][1]["n"] == "3,316"
    assert [c["name"] for c in lib["cards"]] == ["DSC_1.NEF"] and calls[-1] == ("", "")
    ref.setLibScope("D:/Shoots/Iceland")
    assert calls[-1] == ("", "D:/Shoots/Iceland")
    ref.addFolder("D:/Shoots/Georgia")
    pump(qapp, 0.6)
    assert ("add", "D:/Shoots/Georgia") in calls and ("refresh", ("D:/Shoots/Georgia",)) in calls
    ref.removeRoot("D:/Shoots/Iceland")
    assert ("remove", "D:/Shoots/Iceland") in calls


def test_ago_words():
    from datetime import datetime, timedelta

    assert _ago(datetime.now().isoformat()) == "today"
    assert _ago((datetime.now() - timedelta(days=1)).isoformat()) == "yesterday"
    assert _ago((datetime.now() - timedelta(days=5)).isoformat()) == "5 days ago"
    assert _ago("") == "never"


# ---- file associations ----------------------------------------------------------------------------------------------------------


def test_the_summary_and_groups(ref):
    ref.open("fa")
    fa = ref.view["fa"]
    assert fa["supported"] and [g["name"] for g in fa["groups"]] == ["Camera RAW", "Photos", "Other"]
    assert fa["title"].startswith("Image Triage opens ") and fa["canRemove"]
    assert [g["open"] for g in fa["groups"]] == [True, False, False]
    ref.toggleGroup("photo")
    assert [g["open"] for g in ref.view["fa"]["groups"]] == [True, True, False]
    row = next(r for g in ref.view["fa"]["groups"] for r in g["rows"] if r["ext"] == ".cr3")
    assert (row["state"], row["text"], row["canMake"]) == ("other", "Opens in Photos", True)
    row = next(r for g in ref.view["fa"]["groups"] for r in g["rows"] if r["ext"] == ".nef")
    assert (row["state"], row["text"], row["canMake"]) == ("default", "Opens in Image Triage", False)


def test_making_everything_available_and_removing_it_again(ref):
    ref.scene("fa-none")
    fa = ref.view["fa"]
    assert fa["none"] and fa["pct"] == 0 and not fa["canRemove"] and "not set up" in fa["title"]
    ref.faRegister()
    fa = ref.view["fa"]
    assert not fa["none"] and fa["canRemove"] and ref.toasts.items[-1]["kind"] == "success"
    ref.faRemove()
    assert ref.view["fa"]["none"]


def test_another_system_says_the_system_manages_it(ref):
    ref.scene("fa-other-os")
    assert ref.view["fa"]["supported"] is False and ref.view["fa"]["groups"] == []


def test_make_default_asks_windows_for_a_real_type(qapp, photos, monkeypatch):
    import image_triage.file_associations as fa

    asked = []
    monkeypatch.setattr(fa, "open_windows_file_association_chooser", lambda suffix: asked.append(suffix) or "")
    monkeypatch.setattr(fa, "open_windows_default_apps_settings", lambda: asked.append("apps"))
    ref = ReferenceModel(photos, ToastModel(parent=qapp), memory=False, parent=qapp)
    ref._fa_os = True
    ref.faMakeDefault(".cr3")
    ref.faDefaultApps()
    assert asked == [".cr3", "apps"]


def test_real_states_are_classified(qapp, photos, monkeypatch):
    import image_triage.file_associations as fa

    states = (fa.ExtensionAssociationState(".nef", True, True, "ImageTriage.SupportedImage"), fa.ExtensionAssociationState(".cr3", True, False, "AppX123"),
              fa.ExtensionAssociationState(".arw", True, False, ""), fa.ExtensionAssociationState(".raf", False, False, ""))
    monkeypatch.setattr(fa, "query_windows_file_association_states", lambda: states)
    monkeypatch.setattr(fa, "describe_windows_default_handler", lambda s: "Photos")
    ref = ReferenceModel(photos, ToastModel(parent=qapp), memory=False, parent=qapp)
    ref._fa_os = True
    ref.open("fa")
    ref.toggleGroup("raw")
    rows = {r["ext"]: r["state"] for g in ref.view["fa"]["groups"] for r in g["rows"]}
    assert rows == {".nef": "default", ".cr3": "other", ".arw": "available", ".raf": "none"}
    assert ref.view["fa"]["title"] == "Image Triage opens 1 of 4 photo types by default"


# ---- the splash -----------------------------------------------------------------------------------------------------------------


def test_the_splash_shows_and_hides(ref):
    assert ref.view["splash"]["on"] is False
    ref.splashShow("Reading your folder\u2026", 56)
    s = ref.view["splash"]
    assert (s["on"], s["p"], s["status"], s["preview"], s["version"]) == (True, 56, "Reading your folder\u2026", False, "2.3.1")
    ref.splashShow("Ready", 250)
    assert ref.view["splash"]["p"] == 100
    ref.splashHide()
    assert ref.view["splash"]["on"] is False


# ---- the windows in QML ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("scene", ["hlp-start", "hlp-search", "hlp-cull", "hlp-trouble", "hlp-about", "hlp-about-new", "hm-open", "sk-main", "sk-search",
                                   "lib-results", "lib-search", "lib-empty", "fa-main", "fa-none", "fa-other-os", "sp-start", "sp-mid", "sp-end"])
def test_every_scene_loads_without_qml_errors(qapp, photos, scene):
    from PySide6.QtCore import Q_ARG, QMetaObject
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    context = engine.rootContext()
    for name, obj in (("theme", Theme(parent=qapp)), ("photos", photos), ("settings", SettingsModel(memory=True, parent=qapp)),
                      ("cmdPalette", PaletteModel(memory=True, parent=qapp)), ("advFilters", FiltersModel(photos, memory=True, parent=qapp)),
                      ("batch", BatchModel(photos, memory=True, parent=qapp)), *collection_models(photos, qapp),
                      ("handoff", HandoffModel(photos, memory=True, parent=qapp)), ("transfers", TransferModel(photos, memory=True, parent=qapp)),
                      ("thumbs", ThumbService(parent=qapp)), ("startup", f"refScene={scene}")):
        context.setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, 0.8)
    assert not [w for w in warnings if any(k in w for k in ("TypeError", "ReferenceError", "is not a type", "Unable to assign", "non-existent", "Binding loop", "Cannot override"))], warnings
    window.close()


def test_the_splash_window_loads_on_its_own(qapp, photos):
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()
    warnings: list[str] = []
    engine.warnings.connect(lambda items: warnings.extend(str(i) for i in items))
    ref = ReferenceModel(photos, ToastModel(parent=qapp), memory=True, parent=qapp)
    engine.rootContext().setContextProperty("reference", ref)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "SplashWindow.qml")))
    assert engine.rootObjects(), warnings
    ref.splashShow("Starting\u2026", 8)
    pump(qapp, 0.4)
    assert engine.rootObjects()[0].isVisible()
    ref.splashHide()
    pump(qapp, 0.2)
    assert not engine.rootObjects()[0].isVisible()
    assert not [w for w in warnings if "Unable to assign" in w or "ReferenceError" in w], warnings
