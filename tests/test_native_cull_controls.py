import copy

import pytest

from image_triage.qml_shell.native_cull_controls import NativeCullControls


def controls(selected=None):
    return NativeCullControls(selected or {}, {"require_eyes_open": True, "models": [],
        "bursts_keep": 2, "scene_keep": 1, "gates": {"quality_reject": 0, "quality_review": 0, "blur_ratio": .35}})


def test_defaults_are_displayed_without_being_written_into_profile():
    model = controls({"models": ["faces"]})
    assert model.value("bursts_keep") == 2
    assert model.value("gates.quality_reject") == 0
    assert model.draft == {"models": ["faces"]}
    assert not model.dirty


def test_false_and_zero_are_preserved_and_do_not_fall_back_to_defaults():
    model = controls({"require_eyes_open": False, "bursts_keep": 0})
    assert model.value("require_eyes_open", False) is False
    assert model.value("bursts_keep", 2) == 0


def test_control_edits_preserve_unexposed_native_settings():
    selected = {"models": ["faces", "people"], "exclude": ["exports"],
                "weights": {"sharpness": .7}, "gates": {"quality_mode": "technique", "quality_baseline": "baseline.json"}}
    untouched = copy.deepcopy(selected)
    model = controls(selected)
    model.set("bursts_keep", 3)
    model.set("gates.blur_ratio", .4)
    assert selected == untouched
    assert model.draft["exclude"] == selected["exclude"]
    assert model.draft["weights"] == selected["weights"]
    assert model.draft["gates"]["quality_baseline"] == "baseline.json"
    assert model.draft["gates"]["blur_ratio"] == .4


def test_model_toggle_preserves_models_not_exposed_by_visible_controls():
    model = controls({"models": "all"})
    assert model.value("models.maniqa") is True
    assert model.draft["models"] == "all"
    model.set("models.maniqa", False)
    assert model.draft["models"] == ["faces", "appeal", "search", "people"]


def test_sensitivity_preset_leaves_quality_floor_and_keep_counts_unchanged():
    model = controls({"bursts_keep": 4, "gates": {"quality_reject": .1, "quality_review": .2}})
    model.preset("strict")
    assert model.draft["gates"]["clip_region_reject"] == .25
    assert model.draft["gates"]["quality_reject"] == .1
    assert model.draft["gates"]["quality_review"] == .2
    assert model.draft["bursts_keep"] == 4


def test_invalid_control_input_does_not_mutate_profile():
    model = controls({"bursts_keep": 2})
    for key, value in (("bursts_keep", -1), ("blink_threshold", float("nan")), ("unknown", 2)):
        with pytest.raises(ValueError):
            model.set(key, value)
    assert model.draft == {"bursts_keep": 2}
