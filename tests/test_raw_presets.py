"""Copy and paste of Camera Raw settings, and saved presets."""
import json

import pytest

from image_triage.raw_presets import MAX_PRESETS, PresetStore, transferable, usable
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell import qapp  # noqa: F401

LINEAR = {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}


@pytest.fixture
def raw(qapp, tmp_path):
    RawEditor._copied = None
    raw = RawEditor(memory=True)
    raw._presets = PresetStore(tmp_path / "presets.json")
    raw.open("first.NEF")
    yield raw
    raw.shutdown()
    RawEditor._copied = None


def test_only_the_look_is_transferable():
    settings = {"exposure": 1, "masks": [{"shape": LINEAR}], "hostCrop": {"x": 0}, "retouch": [], "redEye": [],
                "lightDepth": {}, "superResolution": True, "seed": 7, "pointCurve": [[0, 0], [255, 200]]}
    assert transferable(settings) == {"exposure": 1, "pointCurve": [[0, 0], [255, 200]]}
    assert usable({"exposure": 1}) and not usable({"exposure": "bright"}) and not usable({"masks": []})
    assert not usable({"notASetting": 1}) and not usable(None)


def test_paste_replaces_the_look_and_keeps_what_belongs_to_the_photo(raw):
    raw.adjust("exposure", 1.5)
    raw.adjust("contrast", 25)
    raw.setCurve("pointCurve", [[0, 0], [255, 220]])
    raw._set_values({"masks": [{"name": "Sky", "shape": LINEAR}]}, "mask")
    assert not raw.canPaste
    raw.copySettings()
    assert raw.canPaste and "copied" in raw.notice

    raw.open("second.NEF")
    raw.adjust("saturation", 40)   # not in the copied look: paste sets it back
    raw._set_values({"masks": [{"name": "Face", "shape": LINEAR}]}, "mask")
    raw.setCrop(0.1, 0.1, 0.5, 0.5)
    steps = len(raw._undo)
    raw.pasteSettings()
    values = raw.values
    assert values["exposure"] == 1.5 and values["contrast"] == 25 and values["saturation"] == 0
    assert values["pointCurve"] == [[0, 0], [255, 220]]
    assert [m["name"] for m in values["masks"]] == ["Face"] and values["hostCrop"]["w"] == 0.5
    assert len(raw._undo) == steps + 1
    raw.undo()
    assert raw.values["saturation"] == 40 and raw.values["exposure"] == 0


def test_paste_onto_a_saved_edit_states_the_settings_it_clears(raw):
    raw.copySettings()                      # an untouched look
    raw.open("second.NEF")
    raw._original = {"contrast": 30}
    raw._draft = {"contrast": 30}
    raw.pasteSettings()
    assert raw._draft == {"contrast": 0} and raw.dirty   # saving must write contrast back to zero


def test_a_copied_look_is_not_changed_by_later_edits(raw):
    raw.setMixer(2, "hslSat", 30)
    raw.copySettings()
    raw.setMixer(2, "hslSat", -80)
    raw.open("second.NEF")
    raw.pasteSettings()
    assert raw.values["hslSat"][2] == 30


def test_presets_save_apply_replace_and_delete(raw, tmp_path):
    raw.adjust("temperature", 20)
    raw.adjust("vibrance", 15)
    raw.savePreset("  Warm   evening ")
    assert raw.presets == ["Warm evening"] and not raw.error
    raw.adjust("vibrance", 60)
    raw.savePreset("warm EVENING")       # same name without regard to case: replaced, not duplicated
    assert raw.presets == ["warm EVENING"]
    raw.savePreset("Alpha")
    assert raw.presets == ["Alpha", "warm EVENING"]

    again = RawEditor(memory=True)
    again._presets = PresetStore(tmp_path / "presets.json")   # a later session reads the same file
    again.open("other.NEF")
    again.adjust("exposure", -1)
    again.applyPreset("Warm Evening")
    assert again.values["temperature"] == 20 and again.values["vibrance"] == 60 and again.values["exposure"] == 0
    assert again.history[0].startswith("Preset: ")
    again.deletePreset("alpha")
    assert again.presets == ["warm EVENING"]
    again.applyPreset("Alpha")           # gone: nothing happens
    assert again.values["temperature"] == 20
    again.shutdown()


def test_a_preset_needs_a_name_and_a_damaged_file_is_an_empty_list(raw, tmp_path):
    raw.savePreset("   ")
    assert raw.error == "Give the preset a name." and raw.presets == []
    (tmp_path / "presets.json").write_text("{broken")
    assert PresetStore(tmp_path / "presets.json").presets() == []
    (tmp_path / "presets.json").write_text(json.dumps({"version": 1, "presets": [
        {"name": "Good", "settings": {"exposure": 1}},
        {"name": "Bad", "settings": {"exposure": "x"}},
        {"name": "good", "settings": {"exposure": 2}},
        "nonsense"]}))
    assert [p["name"] for p in PresetStore(tmp_path / "presets.json").presets()] == ["Good"]


def test_the_number_of_presets_is_bounded(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    store._presets = [{"name": f"P{i}", "settings": {"exposure": 1}} for i in range(MAX_PRESETS)]
    assert "Delete a preset first" in store.save("One more", {"exposure": 1})
    assert store.save("P3", {"exposure": 2}) == ""   # replacing is still allowed
