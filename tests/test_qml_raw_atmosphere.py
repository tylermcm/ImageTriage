"""The Atmosphere section in the editor: its model, what the renderer is sent, what is saved, and the panel."""
import json

import pytest
from PySide6.QtGui import QImage
from image_triage import atmosphere, light_depth, photocraft_bridge as bridge
from image_triage.qml_shell.raw_editor import RawEditor, _recipe, _without_light_depth
from test_qml_raw_light_depth import (  # noqa: F401  (the fixtures and helpers Light Depth's tests are built on)
    find_item, ground, lit_photo, make_item, preview_worker, raw as light_depth_raw, recipe_with, wait,
)
from test_qml_shell import qapp, pump  # noqa: F401


@pytest.fixture
def raw(light_depth_raw):
    light_depth_raw.atmosphereEditor._runner = ground            # a depth map without a model
    return light_depth_raw


def ready(raw, qapp):
    air = raw.atmosphereEditor
    air.prepare()
    wait(qapp, lambda: air.ready and not air.busy)
    return air


def test_choosing_a_preset_reads_the_depth_then_commits_only_the_settings(raw, qapp):
    air = raw.atmosphereEditor
    assert not air.ready and not air.active
    air.choosePreset(1)
    assert air.busy and "atmosphere" not in raw._draft              # the map is read first, the choice waits for it
    wait(qapp, lambda: not air.busy)
    assert air.ready and air.active and air.shown and air.selectedPreset == 1
    saved = raw._draft["atmosphere"]
    assert saved["amount"] == atmosphere.PRESETS[1]["amount"] and saved["preset"] == 2
    assert "masks" not in raw._draft                                # the effect is never a mask in the Mask tab
    assert atmosphere.FOG_NAME not in json.dumps(raw._draft)


def test_a_slider_moving_a_preset_away_clears_the_tile_and_one_drag_is_one_undo_step(raw, qapp):
    air = ready(raw, qapp)
    air.choosePreset(0)
    assert air.selectedPreset == 0
    undo_before = len(raw._undo)
    raw.beginGesture()
    for value in range(20, 61, 5):
        air.adjust("amount", value)
    raw.endGesture()
    assert air.settings["amount"] == 60 and air.selectedPreset == -1 and len(raw._undo) == undo_before + 1
    raw.undo()
    assert air.settings["amount"] == atmosphere.PRESETS[0]["amount"] and air.selectedPreset == 0


def test_drift_moves_the_patches_and_is_clamped(raw, qapp):
    air = ready(raw, qapp)
    air.choosePreset(1)
    air.drift(0.8, 0.1)
    air.flush()
    assert (air.settings["driftX"], air.settings["driftY"]) == (0.8, 0.1)
    air.drift(5, -3)
    air.flush()
    assert (air.settings["driftX"], air.settings["driftY"]) == (1.0, 0.0)
    air.drift(float("nan"), 0.5)
    assert air.settings["driftX"] == 1.0                           # a bad value changes nothing


def test_hide_show_and_remove(raw, qapp):
    air = ready(raw, qapp)
    air.choosePreset(1)
    air.toggleShown()
    assert not air.shown and air.active and not atmosphere.active(raw._draft["atmosphere"])
    air.toggleShown()
    assert air.shown and atmosphere.active(raw._draft["atmosphere"])
    air.remove()
    assert not air.active and raw._draft.get("atmosphere", atmosphere.DEFAULTS) == atmosphere.DEFAULTS
    raw.undo()
    assert air.active


def test_fog_takes_none_of_the_masks_so_a_full_set_of_the_persons_own_does_not_block_it(raw, qapp):
    air = ready(raw, qapp)
    mine = {"name": "m", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}}
    raw._set_values({"masks": [dict(mine) for _ in range(atmosphere.MAX_MASKS)],
                     "lightDepth": light_depth.normalized({"amount": 50})}, "setup")
    air.choosePreset(1)
    assert air.error == "" and air.active and "atmosphere" in raw._draft


def test_missing_model_says_so_and_starts_nothing(raw, qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset())
    from image_triage.qml_shell.raw_light_depth import RawLightDepth
    monkeypatch.setattr(RawLightDepth, "_installed", lambda self, key: False)
    air = raw.atmosphereEditor
    air.choosePreset(1)
    assert air.unavailable and not air.busy and "atmosphere" not in raw._draft and air.error


def test_a_photo_switch_never_applies_a_late_map(raw, qapp):
    air = raw.atmosphereEditor
    gate = []

    def slow(*args):
        import time
        while not gate:
            time.sleep(0.01)
        return ground()

    air._runner = slow
    air.choosePreset(1)
    assert air.busy
    raw.open("other.NEF")
    gate.append(1)
    pump(qapp, .2)
    assert not air.busy and "atmosphere" not in raw._draft


# --- what the native renderer is sent, what is saved --------------------------------------------------------------------------

def test_the_renderer_is_never_sent_the_fog_and_a_save_lays_it_over_the_picture(lit_photo, tmp_path):
    photo, original, sent = lit_photo
    air = atmosphere.with_preset({}, 1)
    draft = {"exposure": .25, "atmosphere": air}
    out = tmp_path / "preview"
    out.mkdir()
    RawEditor._work("preview", str(photo), original, draft, str(out))
    assert "masks" not in sent[-1] and "atmosphere" not in sent[-1]                  # no masks: the host lays it over the render
    RawEditor._work("save", str(photo), original, draft, "")
    assert "masks" not in sent[-1] and "atmosphere" not in sent[-1]
    assert bridge.load_recipe(str(photo))["cameraRaw"] == {"exposure": .25}          # no host settings in the recipe
    assert atmosphere.read_sidecar(bridge.recipe_path(str(photo))) == air
    saved = QImage(str(bridge.saved_render_path(str(photo))))
    assert saved.pixelColor(10, 1).blue() > 20 and saved.pixelColor(10, 22).blue() < saved.pixelColor(10, 1).blue()    # the far part of a red picture is fogged blue-grey, the near part is clear
    # Light Depth and Atmosphere together: Light Depth's masks go to the renderer, the fog is laid on afterwards
    both = {"exposure": .25, "atmosphere": air, "lightDepth": light_depth.normalized({"amount": 60})}
    RawEditor._work("save", str(photo), {"exposure": .25}, both, "")
    assert [m["name"] for m in sent[-1]["masks"]] == [light_depth.NEAR_NAME, light_depth.FAR_NAME]
    assert light_depth.read_sidecar(bridge.recipe_path(str(photo)))[0] and atmosphere.read_sidecar(bridge.recipe_path(str(photo)))
    # Switched off: nothing laid over it; the file stays with the sliders. At Amount 0 the file goes.
    RawEditor._work("save", str(photo), {"exposure": .25}, {"exposure": .25, "atmosphere": {**air, "shown": 0}}, "")
    assert atmosphere.read_sidecar(bridge.recipe_path(str(photo)))["shown"] == 0
    assert QImage(str(bridge.saved_render_path(str(photo)))).pixelColor(10, 1).blue() == 0
    RawEditor._work("save", str(photo), {"exposure": .25}, {"exposure": .25}, "")
    assert not atmosphere.sidecar_path(bridge.recipe_path(str(photo))).exists()


def test_loading_a_photo_with_atmosphere_attaches_its_settings_without_a_mask(tmp_path):
    photo = tmp_path / "IMG_1.NEF"
    photo.write_bytes(b"raw")
    recipe_with(photo, {"exposure": 0.1})
    air = atmosphere.with_preset({}, 2)
    atmosphere.write_sidecar(bridge.recipe_path(str(photo)), air)
    settings = RawEditor._work("load", str(photo), {}, {}, "")[0]
    assert settings["atmosphere"] == air and settings["exposure"] == 0.1 and "masks" not in settings


def test_a_save_without_the_depth_map_says_why_and_changes_nothing(lit_photo, monkeypatch):
    photo, original, sent = lit_photo
    before = bridge.recipe_path(str(photo)).read_bytes()
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: photo.parent / "gone.png")
    with pytest.raises(ValueError, match="depth map is missing"):
        RawEditor._work("save", str(photo), original, {"exposure": .25, "atmosphere": atmosphere.with_preset({}, 1)}, "")
    assert bridge.recipe_path(str(photo)).read_bytes() == before


def test_the_recipe_helpers_strip_the_hosts_entries():
    draft = {"exposure": .1, "atmosphere": atmosphere.with_preset({}, 1)}
    assert _without_light_depth(draft) == {"exposure": .1}
    assert _recipe(draft, "photo.nef")["cameraRaw"] == {"exposure": .1}              # the fog is no part of what the renderer is sent


def test_the_live_preview_lays_the_fog_over_the_draft_and_over_the_saved_picture(qapp, monkeypatch, tmp_path):
    photo, worker, calls, done, errors = preview_worker(monkeypatch, tmp_path)
    saved = atmosphere.with_preset({}, 0)
    draft = {"atmosphere": atmosphere.with_preset({}, 1)}
    worker.submit(1, str(photo), {"atmosphere": saved}, draft)         # the saved edit already has fog
    wait(qapp, lambda: done or errors)
    assert not errors, errors
    for call in calls:
        recipe = call.get("recipe", {}).get("cameraRaw", {})
        assert "masks" not in recipe and "atmosphere" not in recipe
    result, baseline = done[-1][1][0], done[-1][1][1]
    top = lambda frame: frame[1].pixelColor(20, 0)
    assert top(result).blue() > 128 and top(baseline).blue() > 128               # a grey picture, fogged blue-grey at the far end
    assert (top(result).blue(), top(result).red()) != (top(baseline).blue(), top(baseline).red())      # the draft's fog is not the saved one


def test_a_preset_is_not_part_of_a_look_that_is_pasted_to_another_photo():
    from image_triage import raw_presets

    assert "atmosphere" in raw_presets.PHOTO_SPECIFIC
    from image_triage.full_render import HOST_SETTINGS
    assert "atmosphere" in HOST_SETTINGS


# --- the panel -----------------------------------------------------------------------------------------------------------------

PANEL = (b'import QtQuick\nItem { width: 300; height: 900\n RawAtmosphere { objectName: "panel"; width: 300;'
         b' model: rawModel.atmosphereEditor; raw: rawModel } }')


def test_the_panel_renders_with_all_its_controls_and_drives_the_settings(raw, qapp):
    item, keep = make_item(raw, PANEL)
    try:
        air = raw.atmosphereEditor
        wait(qapp, lambda: air.ready)
        for name in ("atmospherePresets", "atmosphere_amount", "atmosphere_height", "atmospherePicture", "atmosphereDrift",
                     "atmosphereDriftHandle", "atmosphereAdvanced", "atmosphereShown", "atmosphereRemove"):
            assert find_item(item, name) is not None, name
        for index in range(3):
            assert find_item(item, f"atmospherePreset_{index}") is not None
        panel = find_item(item, "panel")
        panel.setProperty("advanced", True)
        pump(qapp, .05)
        for name in ("patchiness", "cloudSize", "vibrance", "start"):
            assert find_item(item, "atmosphere_" + name + "_slider") is not None, name
        amount = find_item(item, "atmosphere_amount_slider")
        amount.setProperty("value", 45)
        amount.moved.emit()
        air.flush()
        assert air.settings["amount"] == 45 and raw._draft["atmosphere"]["amount"] == 45
        height = find_item(item, "atmosphere_height_slider")
        height.setProperty("value", 30)
        height.moved.emit()
        air.flush()
        assert raw._draft["atmosphere"]["height"] == 30
    finally:
        item.deleteLater()
        pump(qapp, .05)
