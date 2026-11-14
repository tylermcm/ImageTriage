"""Every engine setting has a control: the native engine's `CullSettings` / `GateSettings` and the cascade's `CascadeSettings`.

The native engine is not installed here. Its settings are read from the source inside the pinned wheel (packaging/cli_culler_wheels)
with `ast`, without importing or running it; the engine's own validation on Save is a stand-in in these tests."""
from __future__ import annotations

import ast
import os
import zipfile
from dataclasses import fields
from pathlib import Path

import pytest
from PySide6.QtCore import QMetaObject, QObject, QPointF, QSettings, Qt, QUrl
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from image_triage.cascade import controls as cascade_controls
from image_triage.cascade.model import CascadeSettings
from image_triage.qml_shell import native_cull_controls as native
from image_triage.qml_shell.cull_model import PRESETS, TUNES, CullModel
from image_triage.qml_shell.native_cull_controls import NativeCullControls
from image_triage.qml_shell.photos import PhotoModel

ROOT = Path(__file__).resolve().parents[1]
WHEEL = next((ROOT / "packaging" / "cli_culler_wheels").glob("ai_culler-*.whl"))


def engine_settings() -> dict[str, dict[str, object]]:
    """{class name: {field: default}} of the engine's two settings dataclasses, read from the wheel's source."""
    with zipfile.ZipFile(WHEEL) as wheel:
        tree = ast.parse(wheel.read("aiculler/api.py").decode("utf-8"))
    missing = object()
    out: dict[str, dict[str, object]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name in ("CullSettings", "GateSettings"):
            found = {}
            for item in node.body:
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                    try:
                        found[item.target.id] = ast.literal_eval(item.value)
                    except (ValueError, TypeError):
                        found[item.target.id] = missing
                        if item.target.id == "weights":        # field(default_factory=lambda: {...}): the dict is the lambda's body
                            found["weights"] = ast.literal_eval(item.value.keywords[0].value.body)
                        elif item.target.id in ("models", "exclude"):
                            found[item.target.id] = []
            out[node.name] = found
    return out


ENGINE = engine_settings()
ENGINE_DEFAULTS = {**{k: v for k, v in ENGINE["CullSettings"].items() if k != "gates"}, "gates": dict(ENGINE["GateSettings"])}
SPECS = {spec[0]: spec for spec in native.ALL_FIELDS}


def controls(selected=None):
    return NativeCullControls(selected or {}, ENGINE_DEFAULTS)


# -- the native engine ---------------------------------------------------------------------------------------------------------------

def test_every_native_setting_has_a_control_and_no_control_is_invented():
    expected = set()
    for name in ENGINE["CullSettings"]:
        if name == "gates":
            expected |= {f"gates.{gate}" for gate in ENGINE["GateSettings"]}
        elif name == "weights":
            expected |= {f"weights.{weight}" for weight in ENGINE["CullSettings"]["weights"]}
        elif name == "models":
            expected |= {f"models.{model}" for model in ("faces", "maniqa", "appeal", "search", "people")}      # MODEL_FEATURES
        else:
            expected.add(name)
    assert len(ENGINE["CullSettings"]) == 26 and len(ENGINE["GateSettings"]) == 19          # the pinned engine; a new pin must be looked at
    assert set(SPECS) == expected
    assert len(native.MORE_FIELDS) == 28                                    # the settings that had no control before


def test_every_control_has_a_label_a_description_a_group_and_the_engines_default():
    groups = dict(native.GROUPS)
    model = controls()
    for key, label, group, kind, default, low, high, step in native.ALL_FIELDS:
        assert label and native.HELP.get(key, "").endswith("."), key
        assert group in groups and kind in ("toggle", "slider", "number", "choice", "text", "list"), key
        engine_default = model.default(key)
        if not key.startswith("models.") and key != "refine_scenes":       # the app's own profile turns scene refinement on; the engine's default is off
            assert engine_default == default, (key, engine_default, default)
        if kind in ("slider", "number"):
            assert low <= engine_default <= high and step > 0, key
        if kind == "choice":
            assert engine_default in native.CHOICES[key], key
    shown = {field["key"]: field for group in model.groups() for field in group["fields"]}
    assert set(shown) == set(SPECS) and all(field["isDefault"] or field["key"].startswith("models.") for field in shown.values())
    assert shown["gates.quality_baseline"]["default"] == "none" and shown["exclude"]["default"] == "none" and shown["keep_people_coverage"]["default"] == "On"
    assert shown["weights.appeal"]["default"] == "0.40" and shown["appeal_shortlist"]["default"] == "3" and shown["moving_brackets"]["default"] == "as burst"


def test_the_choices_are_the_engines_own_lists():
    with zipfile.ZipFile(WHEEL) as wheel:
        tree = ast.parse(wheel.read("aiculler/api.py").decode("utf-8"))
    lists = {node.targets[0].id: list(ast.literal_eval(node.value)) for node in tree.body
             if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id.isupper()
             and isinstance(node.value, ast.Tuple)}
    for key, name in (("brackets", "BRACKET_MODES"), ("moving_brackets", "MOVING_BRACKET_MODES"), ("focus_stacks", "SEQUENCE_MODES"),
                      ("timelapses", "SEQUENCE_MODES"), ("gates.featureless", "FEATURELESS_MODES"), ("normalise", "NORMALISE_MODES"),
                      ("review_warnings", "REVIEW_WARNING_MODES"), ("maniqa_scope", "MANIQA_SCOPES"), ("gates.quality_mode", "QUALITY_MODES"),
                      ("gates.quality_signal", "QUALITY_SIGNALS"), ("gates.quality_unique", "QUALITY_UNIQUE")):
        assert native.CHOICES[key] == lists[name], key


def test_the_groups_are_split_into_fine_tune_and_advanced():
    groups = {group["key"]: group for group in controls().groups()}
    assert [key for key, group in groups.items() if group["tier"] == "advanced"] == ["ranking", "shoot"]
    assert {field["key"] for field in groups["ranking"]["fields"]} >= {"weights.appeal", "normalise", "maniqa_scope", "appeal_shortlist", "preference_model"}
    assert {field["key"] for field in groups["exposure"]["fields"]} == {"gates.black_mean", "gates.sky_blown", "gates.washed_mean", "gates.washed_spread",
                                                                         "gates.shadows_crushed", "gates.featureless"}
    assert not groups["models"]["resettable"] and all(group["resettable"] for key, group in groups.items() if key != "models")


def test_new_controls_write_real_engine_values_and_keep_what_they_do_not_touch():
    model = controls({"models": ["faces"], "gates": {"quality_reject": .1}})
    model.set("gates.sky_blown", .9)
    model.set("moving_brackets", "bracket")
    model.set("keep_people_coverage", False)
    model.set("appeal_shortlist", 5.0)
    model.set("gates.quality_baseline", "  baselines/library.json ")
    model.set("exclude", "exports, Selects ,, exports")
    assert model.draft == {"models": ["faces"], "gates": {"quality_reject": .1, "sky_blown": .9, "quality_baseline": "baselines/library.json"},
                           "moving_brackets": "bracket", "keep_people_coverage": False, "appeal_shortlist": 5, "exclude": ["exports", "Selects"]}
    assert isinstance(model.draft["appeal_shortlist"], int)
    model.set("gates.quality_baseline", "")                                  # an emptied path is "none", not an empty string
    model.set("preference_model", None)
    assert model.draft["gates"]["quality_baseline"] is None and model.draft["preference_model"] is None
    shown = {field["key"]: field for group in model.groups() for field in group["fields"]}
    assert shown["exclude"]["value"] == "exports, Selects" and not shown["exclude"]["isDefault"] and shown["gates.quality_baseline"]["value"] == ""


def test_setting_one_weight_keeps_the_other_weights():
    model = controls()
    model.set("weights.appeal", .6)
    assert model.draft["weights"] == {**ENGINE["CullSettings"]["weights"], "appeal": .6}       # the engine takes the dictionary whole
    saved = controls({"weights": {"appeal": .2, "sharpness": 1.0}})
    saved.set("weights.exposure", .3)
    assert saved.draft["weights"] == {"appeal": .2, "sharpness": 1.0, "exposure": .3}          # a profile's own weights are not refilled


def test_values_outside_a_controls_range_are_refused_and_change_nothing():
    model = controls({"bursts_keep": 2})
    for key, value in (("appeal_shortlist", 0), ("appeal_shortlist", 2.5), ("weights.appeal", -0.1), ("gates.sky_blown", 1.2), ("normalise", "median"),
                       ("keep_people_coverage", 1), ("exclude", 4), ("gates.quality_baseline", 3), ("not_a_setting", 1)):
        with pytest.raises(ValueError):
            model.set(key, value)
    assert model.draft == {"bursts_keep": 2} and not model.dirty


def test_reset_puts_one_group_back_to_the_engines_defaults_and_leaves_the_rest():
    model = controls({"models": ["faces"], "bursts_keep": 4, "gates": {"sky_blown": .9, "black_mean": .05, "blur_ratio": .5}, "weights": {"appeal": 1.0}, "normalise": "rank"})
    groups = {group["key"]: group for group in model.groups()}
    assert groups["exposure"]["changed"] and groups["ranking"]["changed"] and not groups["scenes"]["changed"] and not groups["models"]["changed"]
    model.reset_group("exposure")
    assert model.draft == {"models": ["faces"], "bursts_keep": 4, "gates": {"blur_ratio": .5}, "weights": {"appeal": 1.0}, "normalise": "rank"} and model.dirty
    model.reset_group("ranking")
    model.reset_group("quality")
    assert model.draft == {"models": ["faces"], "bursts_keep": 4}            # the emptied "gates" is left out, not sent as {}
    assert not {group["key"] for group in model.groups() if group["changed"]} - {"keepers"}
    with pytest.raises(ValueError):
        model.reset_group("models")
    with pytest.raises(ValueError):
        model.reset_group("nonsense")


def test_presets_still_set_their_three_values_and_a_change_reads_as_custom(tmp_path):
    app = QApplication.instance() or QApplication([])
    cull = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    cull._native = True
    cull._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    cull._native_controls = controls({"models": ["faces"]})
    cull.setNativePreset("strict")
    assert cull.view["nativePreset"] == 2
    assert [cull._native_controls.value(key) for key in ("gates.clip_region_reject", "gates.blur_ratio", "blink_threshold")] == [.25, .50, .45]
    cull.setNativeValue("gates.sky_blown", .9)                             # not one of the preset's values: still Strict
    assert cull.view["nativePreset"] == 2
    cull.setNativeValue("blink_threshold", .5)
    assert cull.view["nativePreset"] == -1                                 # Custom
    cull.resetNativeGroup("eyes")
    assert cull._native_controls.value("blink_threshold") == .6 and cull.view["nativeDirty"] and cull.view["nativeError"] == ""
    cull.resetNativeGroup("models")
    assert "cannot be reset" in cull.view["nativeError"]


def test_edits_reach_the_engines_validation_through_the_asynchronous_save(tmp_path, monkeypatch):
    from image_triage import cli_culler_runtime
    from image_triage.qml_shell.native_cull_controls import NativeDefaultsRead

    seen = []

    def validate(self, python, value, timeout=30):                         # the engine's CullSettings.from_dict, in its own process
        seen.append(value)
        if value.get("gates", {}).get("clip_region_ignore", 0) >= value.get("gates", {}).get("clip_region_reject", .35):
            raise ValueError("gates: need 0 <= clip_region_ignore < clip_region_reject <= 1")

    monkeypatch.setattr(NativeDefaultsRead, "validate", validate)
    monkeypatch.setattr(cli_culler_runtime, "load_native_runtime", lambda: {"python": "engine-python", "default_settings": {"models": ["faces"]}})
    app = QApplication.instance() or QApplication([])
    cull = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    cull._native = True
    cull._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    cull._native_controls = controls({"models": ["faces"]})
    cull.setNativeValue("gates.clip_region_ignore", .6)                    # allowed by the control, refused by the engine's rule between two fields
    cull.saveNativeDraft()
    for _ in range(400):
        if cull._native_save is None:
            break
        QTest.qWait(5)
    assert seen and seen[-1]["gates"]["clip_region_ignore"] == .6
    assert "clip_region_ignore < clip_region_reject" in cull.view["nativeError"] and cull.view["nativeDirty"]
    cull.resetNativeGroup("quality")
    cull.setNativeValue("weights.sharpness", .3)
    cull.saveNativeDraft()
    for _ in range(400):
        if cull._native_save is None:
            break
        QTest.qWait(5)
    assert seen[-1]["weights"]["sharpness"] == .3 and cull.view["nativeError"] == "" and not cull.view["nativeDirty"]


# -- the cascade ---------------------------------------------------------------------------------------------------------------------

def test_every_cascade_setting_has_a_slider_or_a_control():
    assert cascade_controls.uncovered_fields() == []
    names = {field.name for field in fields(CascadeSettings)}
    assert set(cascade_controls.TUNED_FIELDS) | {control.key for control in cascade_controls.CONTROLS} == names
    assert len(cascade_controls.CONTROLS) == 11 and len(TUNES) == 10
    assert all(control.label and control.help.endswith(".") for control in cascade_controls.CONTROLS)


def test_the_cascade_defaults_reproduce_the_cascade_exactly():
    assert CascadeSettings(**cascade_controls.settings_kwargs(cascade_controls.defaults())) == CascadeSettings()
    rows = {row["key"]: row for row in cascade_controls.view(cascade_controls.defaults())}
    assert all(row["isDefault"] for row in rows.values())
    assert rows["max_stack_span"]["text"] == "Off" and rows["split_focal_ratio"]["text"] == "Off" and rows["orb_inliers"]["text"] == "Off"
    assert rows["clip_ratio"]["text"] == "2.0x" and rows["orb_share"]["text"] == "60%" and rows["final_keep"]["text"] == "2" and rows["linkage"]["text"] == "First fit"


def test_cascade_positions_become_real_settings_and_bad_ones_are_refused():
    values = dict(cascade_controls.defaults())
    for key, value in (("max_stack_span", 24.0), ("split_focal_ratio", 1.5), ("orb_inliers", 30), ("linkage", "closest"), ("all_flagged_skips_collapse", True),
                       ("final_keep", 3), ("clip_ratio", 2.54)):
        values[key] = cascade_controls.checked(key, value)
    made = CascadeSettings(**cascade_controls.settings_kwargs(values))
    assert (made.max_stack_span, made.split_focal_ratio, made.split_face_ratio, made.orb_inliers, made.linkage, made.all_flagged_skips_collapse, made.final_keep) == (
        24, 1.5, None, 30, "closest", True, 3)
    assert made.clip_ratio == 2.5 and isinstance(made.max_stack_span, int)   # snapped to the slider's step
    for key, value in (("final_keep", 0), ("clip_ratio", 9), ("linkage", "nearest"), ("all_flagged_skips_collapse", 1), ("repeat_gate", float("nan")), ("nothing", 1), ("maniqa_keep", True)):
        with pytest.raises(ValueError):
            cascade_controls.checked(key, value)


def test_the_cull_window_sets_resets_and_runs_with_the_advanced_values(tmp_path):
    app = QApplication.instance() or QApplication([])
    cull = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    assert not cull.view["advOpen"] and not cull.view["advChanged"] and len(cull.view["advanced"]) == 11
    cull.toggleAdvanced()
    cull.setAdvanced("final_keep", 3)
    cull.setAdvanced("linkage", "closest")
    cull.setAdvanced("split_face_ratio", 2.0)
    rows = {row["key"]: row for row in cull.view["advanced"]}
    assert cull.view["advChanged"] and rows["final_keep"]["value"] == 3 and not rows["final_keep"]["isDefault"] and rows["final_keep"]["default"] == "2"
    assert rows["split_face_ratio"]["text"] == "2.0x" and rows["repeat_gate"]["isDefault"]
    cull.setAdvanced("final_keep", 99)                                      # refused: nothing changes
    assert {row["key"]: row for row in cull.view["advanced"]}["final_keep"]["value"] == 3
    made = CascadeSettings(**cascade_controls.settings_kwargs(cull._s["adv"]))
    assert made.final_keep == 3 and made.linkage == "closest" and made.split_face_ratio == 2.0
    cull.setSens("strict")                                                  # a preset sets its sliders and leaves the advanced values alone
    assert cull._s["adv"]["final_keep"] == 3 and cull.view["sens"][2]["on"] and not cull.view["sensCustom"]
    cull.setVal("soft", 45)
    assert [s["on"] for s in cull.view["sens"]] == [False, False, False] and cull.view["sensCustom"] and cull.view["sensText"].startswith("Custom")
    soft = next(t for t in cull.view["tunes"] if t["key"] == "soft")
    assert soft["def"] == "40%" and not soft["isDefault"]
    cull.setVal("soft", PRESETS["strict"]["soft"])
    assert cull.view["sens"][2]["on"] and not cull.view["sensCustom"]       # back on the preset's values: Strict again
    cull.resetAdvanced()
    assert not cull.view["advChanged"] and cull._s["adv"] == cascade_controls.defaults()


def test_a_real_run_is_started_with_the_advanced_settings(tmp_path, monkeypatch):
    from image_triage.qml_shell import cull_model as module

    app = QApplication.instance() or QApplication([])
    cull = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    captured = {}

    class Run:
        running = False

        def start(self, **kwargs):
            captured.update(kwargs)

    cull._run, cull._memory, cull._ai = Run(), False, None
    monkeypatch.setattr(cull, "_ranking_decision", lambda: type("D", (), {"use_rankers": False, "use_blink": False})())
    monkeypatch.setattr(cull, "_protected_paths", lambda: [])
    monkeypatch.setattr(cull._photos, "folder_dir", lambda: str(tmp_path))
    cull.setAdvanced("maniqa_keep", 5)
    cull.setAdvanced("max_stack_span", 30)
    cull._start_real() if hasattr(cull, "_start_real") else cull.start()
    settings = captured.get("cascade")
    assert settings is not None and settings.maniqa_keep == 5 and settings.max_stack_span == 30 and settings.orb_inliers == 0 and settings.split_focal_ratio is None
    assert module.CascadeSettings is CascadeSettings


# -- the windows ---------------------------------------------------------------------------------------------------------------------

def visual_child(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_child(child, name)
        if found is not None:
            return found
    return None


def click(window, item):
    point = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    QTest.mouseClick(window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    QTest.qWait(60)


def test_the_native_groups_render_with_defaults_reset_and_the_advanced_tier(tmp_path):
    from PySide6.QtQml import QQmlApplicationEngine
    from image_triage.qml_shell.app import QML_DIR, _configure_qt_dll_search
    from image_triage.qml_shell.theme import Theme

    _configure_qt_dll_search()
    from PySide6 import QtQuick  # noqa: F401
    app = QApplication.instance() or QApplication([])
    model = CullModel(PhotoModel(parent=app), memory=True, parent=app)
    model._native = True
    model._settings = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    model._native_controls = controls({"models": ["faces"], "gates": {"sky_blown": .9}})
    model.openCull()
    engine = QQmlApplicationEngine()
    engine.addImportPath(str(QML_DIR))
    engine.rootContext().setContextProperty("cull", model)
    engine.rootContext().setContextProperty("theme", Theme(parent=app))
    warnings = []
    engine.warnings.connect(lambda items: warnings.extend(str(item) for item in items))
    engine.loadData(b'import QtQuick; import QtQuick.Window; Window { width: 920; height: 5200; visible: true; color: theme.base; NativeCullOptions { x: 10; y: 10; width: 900 } }',
                    QUrl.fromLocalFile(str(QML_DIR / "NativeAllSettingsTest.qml")))
    assert engine.rootObjects(), warnings
    window = engine.rootObjects()[0]
    QTest.qWait(100)
    click(window, window.findChild(QObject, "nativeFineTune"))
    root = window.contentItem()
    assert visual_child(root, "nativeGroup_exposure") is not None and visual_child(root, "nativeGroup_people") is not None
    advanced = visual_child(root, "nativeAdvanced")
    assert not advanced.property("visible")                                # the weights are not in Fine-tune
    assert visual_child(root, "nativeDefault_gates.sky_blown").property("text") == "Changed. Default: 0.75"
    assert visual_child(root, "nativeDefault_gates.black_mean").property("text") == "Default: 0.02"
    reset = visual_child(root, "nativeReset_exposure")
    assert reset.property("visible") and not visual_child(root, "nativeReset_scenes").property("visible")
    shot = os.environ.get("IT_SHOT_DIR")
    click(window, visual_child(root, "nativeAdvancedButton"))
    assert advanced.property("visible") and visual_child(root, "nativeGroup_ranking") is not None and visual_child(root, "nativeGroup_shoot") is not None
    if shot:
        window.grabWindow().save(os.path.join(shot, "native_all_settings.png"))
    field = visual_child(root, "nativeText_exclude")
    field.setProperty("text", "exports, proofs")                           # typed, then Return
    QMetaObject.invokeMethod(field, "editingFinished")
    QTest.qWait(60)
    assert model._native_controls.draft["exclude"] == ["exports", "proofs"]
    click(window, visual_child(root, "nativeReset_exposure"))
    assert "gates" not in model._native_controls.draft and model._native_controls.draft["exclude"] == ["exports", "proofs"]
    assert not visual_child(root, "nativeReset_exposure").property("visible")
    assert not warnings, warnings
    window.close()
    engine.deleteLater()
    QTest.qWait(50)


def test_the_cascade_advanced_controls_render_and_work(tmp_path):
    from test_qml_shell import _load_main, pump

    app = QApplication.instance() or QApplication([])
    engine, window, warnings = _load_main(app, tmp_path)
    window.setWidth(1440)
    window.setHeight(1000)
    cull = engine.rootContext().contextProperty("cull")
    cull.openCull()
    cull.toggleTune()
    pump(app, 0.8)
    root = window.contentItem()
    toggle = visual_child(root, "cullAdvancedToggle")
    assert toggle is not None and visual_child(root, "cullAdvanced_final_keep") is None
    cull.toggleAdvanced()
    cull.setAdvanced("final_keep", 3)
    cull.setVal("soft", 45)
    pump(app, 0.5)
    assert visual_child(root, "cullAdvanced_final_keep") is not None and visual_child(root, "cullAdvancedReset").property("visible")
    body = visual_child(root, "cullBody")
    body.setProperty("contentY", max(0, body.property("contentHeight") - body.height()))      # the advanced rows are at the end of the page
    pump(app, 0.3)
    shot = os.environ.get("IT_SHOT_DIR")
    if shot:
        window.grabWindow().save(os.path.join(shot, "cascade_advanced.png"))
    cull.resetAdvanced()
    pump(app, 0.3)
    assert not visual_child(root, "cullAdvancedReset").property("visible")
    assert not [w for w in warnings if "ReferenceError" in w or "TypeError" in w or "Required property" in w or "Unable to assign" in w], warnings
    window.close()
    engine.deleteLater()
    pump(app, 0.2)
