"""The QML shell's Cull window and AI Center: the flow, the wording and the settings it keeps. The analysis itself is scripted (Sample)."""
from __future__ import annotations

import time

import pytest
from PySide6.QtCore import QCoreApplication, QSettings, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQuick import QQuickWindow, QSGRendererInterface

from image_triage.cascade.model import CascadeSettings
from image_triage.qml_shell import app as shell_app
from image_triage.qml_shell.batch_model import BatchModel
from image_triage.qml_shell.cull_model import PRESETS, CullModel
from image_triage.qml_shell.filters_model import FiltersModel
from image_triage.qml_shell.handoff_model import HandoffModel
from image_triage.qml_shell.palette_model import PaletteModel
from image_triage.qml_shell.photos import PhotoModel
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import Theme
from image_triage.qml_shell.thumbs import ThumbService
from image_triage.qml_shell.transfer_model import TransferModel
from qml_support import collection_models

QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
NAMES = ["DSC_4201", "DSC_4203", "DSC_4207", "DSC_4210", "DSC_4214", "DSC_4219", "DSC_4222", "DSC_4226", "DSC_4231", "DSC_4236", "DSC_4240", "DSC_4244"]


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
        rows.append({"path": str(path), "name": name, "ext": "JPG", "raw": False, "decision": "", "rating": 3, "ai": 70})
    model.setRows(rows, str(tmp_path))
    return model


@pytest.fixture
def cull(qapp, photos):
    return CullModel(photos, memory=True, parent=qapp)


def run_to_the_end(qapp, cull):
    cull.start()
    deadline = time.monotonic() + 20
    while cull.view["page"] == "run" and time.monotonic() < deadline:
        pump(qapp, 0.1)
    assert cull.view["page"] == "rev"


def flagged(cull) -> dict:
    return {c["key"]: c["n"] for c in cull.view["chips"]}


def test_balanced_is_what_the_cascade_measured():
    d = CascadeSettings()
    assert PRESETS["balanced"] == {"split": d.split_jump, "dup": d.duplicate_distance, "soft": 40, "clip": 10, "blown": 40, "blink": 60}
    assert PRESETS["careful"]["soft"] < PRESETS["balanced"]["soft"] < PRESETS["strict"]["soft"]


def test_strictness_and_fine_tuning(cull):
    cull.setSens("strict")
    assert [s["on"] for s in cull.view["sens"]] == [False, False, True] and cull.view["sensText"].startswith("Flags borderline")
    cull.toggleTune()
    soft = next(t for t in cull.view["tunes"] if t["key"] == "soft")
    assert (soft["raw"], soft["val"], soft["min"], soft["max"]) == (50, "50%", 20, 70)
    cull.setVal("blink", 75)
    assert next(t for t in cull.view["tunes"] if t["key"] == "blink")["val"] == "0.75"
    cull.resetTune()
    assert cull.view["sens"][1]["on"] and next(t for t in cull.view["tunes"] if t["key"] == "soft")["raw"] == 40


def test_ranking_asks_before_using_the_cpu(cull):
    assert cull.view["rank"]["canSetup"] and not cull.view["rank"]["on"]
    cull.toggleRank()
    assert cull.view["rank"]["ask"] and not cull.view["rank"]["on"]
    cull.answerCpu(False)
    assert not cull.view["rank"]["ask"] and not cull.view["rank"]["on"]
    cull.toggleRank()
    cull.answerCpu(True)
    assert cull.view["rank"]["on"]
    assert "7 seconds to over a minute per frame" in cull.view["rank"]["desc"]


def test_the_run_finds_what_the_mockup_found(qapp, cull):
    cull.openCull()
    run_to_the_end(qapp, cull)
    assert flagged(cull) == {"all": 5, "blink": 1, "soft": 1, "clipped": 1, "blown_out": 1, "duplicate": 1}
    assert [t["n"] for t in cull.view["tiles"]] == [12, 6, 4, 1]
    assert cull.view["steps"][2]["sub"] == "5 flagged" and cull.view["applyCount"] == 4


def test_without_the_runtime_closed_eyes_are_not_checked(qapp, cull):
    cull.setupRuntime()
    cull.cancelSetup()
    cull._s["rt"] = "missing"
    cull._rebuild()
    assert not cull.view["eyes"]["on"]
    run_to_the_end(qapp, cull)
    assert "blink" not in flagged(cull) and cull.view["applyCount"] == 3


def test_winners_are_never_flagged(qapp, photos, cull):
    photos.setDecision(2, "win")                            # DSC_4207, which the sample would flag as clipped
    run_to_the_end(qapp, cull)
    assert "clipped" not in flagged(cull)


def test_restoring_and_applying_and_undoing(qapp, photos, cull):
    run_to_the_end(qapp, cull)
    bad = next(r for r in photos.collect() if r["name"] == "DSC_4210")
    cull.pick(bad["path"])
    assert cull.view["det"]["flagged"] and cull.view["det"]["restoreLabel"] == "Restore" and "31%" in cull.view["det"]["why"]
    cull.toggleRestore()
    assert cull.view["applyCount"] == 3 and cull.view["det"]["restoreLabel"] == "Flag again" and cull.gridFlag(bad["path"]) == {}
    cull.toggleInc("blown_out")                               # leave that reason out
    assert cull.view["applyCount"] == 2
    cull.toApply()
    assert cull.view["page"] == "apply" and cull.view["applyLabel"] == "Mark 2 as Rejects"
    cull.apply()
    decisions = {r["name"]: r["decision"] for r in photos.collect()}
    assert [n for n, d in decisions.items() if d == "rej"] == ["DSC_4207", "DSC_4226"] and cull.view["doneTitle"] == "2 frames marked as Rejects"
    cull.undo()
    assert not [r for r in photos.collect() if r["decision"] == "rej"] and cull.view["page"] == "rev"


def test_the_grid_flag_follows_the_review(qapp, photos, cull):
    run_to_the_end(qapp, cull)
    soft = next(r for r in photos.collect() if r["name"] == "DSC_4210")
    assert cull.gridFlag(soft["path"]) == {"label": "Soft", "color": "#d9b650"}
    photos.setDecision(3, "keep")                               # you decided: it is not dimmed any more
    assert cull.gridFlag(soft["path"]) == {}
    assert cull.aiState == 2


def test_setting_up_the_runtime_is_scripted_and_the_vc_error_is_shown(qapp, cull):
    cull._s.update(rt="missing", vcOld=True)
    cull._rebuild()
    cull.setupRuntime()
    assert cull.view["rt"]["state"] == "installing"
    deadline = time.monotonic() + 10
    while cull.view["rt"]["state"] == "installing" and time.monotonic() < deadline:
        pump(qapp, 0.1)
    assert cull.view["rt"]["state"] == "error" and cull.view["center"]["vcShow"] and "14.40" in cull.view["rt"]["errBody"]
    cull.recheckRuntime()
    assert cull.view["rt"]["state"] == "ready" and not cull.view["center"]["vcShow"]


def test_the_cpu_default_is_the_widgets_setting(qapp, photos, tmp_path):
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(tmp_path / "cfg"))
    model = CullModel(photos, memory=False, parent=qapp)
    assert not model.view["center"]["cpu"]
    model.setCpuDefault(True)
    assert CullModel(photos, memory=False, parent=qapp).view["center"]["cpu"]
    model.toggleRank()                                          # no GPU, but the default says yes: no question
    assert model.view["rank"]["on"] and not model.view["rank"]["ask"]


def test_windows_follow_each_other(cull):
    cull.openCull()
    assert cull.view["window"] == "cull"
    cull.openCenter()
    assert cull.view["window"] == "center"
    cull.close()
    assert cull.view["window"] == "cull"                        # back to where it was opened from
    cull.close()
    assert cull.view["window"] == ""


@pytest.mark.parametrize("scene", ["cu-prep", "cu-missing", "cu-installing", "cu-error", "cu-gpu", "cu-cpu", "cu-tune", "cu-run", "cu-mini", "cu-review", "cu-eyes",
                                   "cu-ranked", "cu-restored", "cu-apply", "cu-bin", "cu-done", "cu-grid", "ac-ready", "ac-fresh", "ac-error"])
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
                      ("thumbs", ThumbService(parent=qapp)), ("startup", f"cullScene={scene}")):
        context.setContextProperty(name, obj)
    engine.addImportPath(str(shell_app.QML_DIR))
    engine.load(QUrl.fromLocalFile(str(shell_app.QML_DIR / "Main.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    window.setWidth(1440)
    window.setHeight(900)
    QMetaObject.invokeMethod(window, "applyStartup", Q_ARG("QVariant", 1440))
    pump(qapp, 0.8)
    assert not [w for w in warnings if any(k in w for k in ("TypeError", "ReferenceError", "is not a type", "Unable to assign", "non-existent", "Binding loop"))], warnings
    window.close()
