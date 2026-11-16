"""The Bokeh section in the editor: its model, what the renderer is sent, what is saved, and the panel."""
import json

import pytest
from PySide6.QtGui import QColor, QImage

from image_triage import atmosphere, bokeh, light_depth, photocraft_bridge as bridge
from image_triage.qml_shell.raw_editor import RawEditor, _recipe, _without_light_depth
from test_qml_raw_light_depth import (  # noqa: F401  (the fixtures and helpers Light Depth's tests are built on)
    find_item, ground, lit_photo, make_item, preview_worker, raw as light_depth_raw, recipe_with, wait,
)
from test_qml_shell import qapp, pump  # noqa: F401


@pytest.fixture
def raw(light_depth_raw):
    light_depth_raw.bokehEditor._runner = ground            # a depth map without a model
    return light_depth_raw


def ready(raw, qapp):
    blur = raw.bokehEditor
    blur.prepare()
    wait(qapp, lambda: blur.ready and not blur.busy)
    return blur


def test_raising_blur_reads_the_depth_then_commits_only_the_settings_and_picks_a_first_focus(raw, qapp):
    blur = raw.bokehEditor
    assert not blur.ready and not blur.active
    blur.adjust("amount", 60)
    blur.flush()
    assert blur.busy and "bokeh" not in raw._draft                    # the map is read first, the change waits for it
    wait(qapp, lambda: not blur.busy)
    assert blur.ready and blur.active and blur.shown
    saved = raw._draft["bokeh"]
    assert saved["amount"] == 60 and saved["focus"] != bokeh.DEFAULTS["focus"]      # what is in the middle of the picture was focused on
    assert "masks" not in raw._draft and "Atmosphere" not in json.dumps(raw._draft)


def test_a_chosen_focus_is_not_replaced_by_the_first_focus(raw, qapp):
    blur = ready(raw, qapp)
    blur.adjust("focus", 12)
    blur.flush()
    blur.adjust("amount", 40)
    blur.flush()
    assert raw._draft["bokeh"]["focus"] == 12


def test_tapping_the_picture_focuses_on_what_is_there(raw, qapp):
    blur = ready(raw, qapp)
    blur.adjust("amount", 50)
    blur.flush()
    blur.focusAt(0.5, 0.05)                                           # the top of the ramp is the far end
    far = raw._draft["bokeh"]["focus"]
    blur.focusAt(0.5, 0.95)
    near = raw._draft["bokeh"]["focus"]
    assert far > near and near < 30 and far > 70
    blur.focusAt(float("nan"), 0.5)
    assert raw._draft["bokeh"]["focus"] == near                       # a bad value changes nothing


def test_one_drag_is_one_undo_step_and_choosing_a_shape_is_kept(raw, qapp):
    blur = ready(raw, qapp)
    blur.adjust("amount", 20)
    blur.flush()
    undo_before = len(raw._undo)
    raw.beginGesture()
    for value in range(30, 81, 10):
        blur.adjust("amount", value)
    raw.endGesture()
    assert blur.settings["amount"] == 80 and len(raw._undo) == undo_before + 1
    raw.undo()
    assert blur.settings["amount"] == 20
    blur.chooseShape(4)
    assert blur.selectedShape == 4 and raw._draft["bokeh"]["shape"] == 4 and blur.shapes[4] == "Heart"
    blur.chooseShape(9)
    assert blur.selectedShape == 4


def test_hide_show_and_remove(raw, qapp):
    blur = ready(raw, qapp)
    blur.adjust("amount", 50)
    blur.flush()
    blur.toggleShown()
    assert not blur.shown and blur.active and not bokeh.active(raw._draft["bokeh"])
    blur.toggleShown()
    assert blur.shown and bokeh.active(raw._draft["bokeh"])
    blur.remove()
    assert not blur.active and raw._draft.get("bokeh", bokeh.DEFAULTS) == bokeh.DEFAULTS
    raw.undo()
    assert blur.active


def test_it_takes_no_masks_so_a_full_set_does_not_block_it(raw, qapp):
    blur = ready(raw, qapp)
    mine = {"name": "m", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}}
    raw._set_values({"masks": [dict(mine) for _ in range(16)]}, "setup")
    blur.adjust("amount", 50)
    blur.flush()
    assert blur.error == "" and blur.active


def test_missing_model_says_so_and_starts_nothing(raw, qapp, monkeypatch):
    monkeypatch.setattr("image_triage.editor_tools.installed_models", lambda: frozenset())
    from image_triage.qml_shell.raw_light_depth import RawLightDepth
    monkeypatch.setattr(RawLightDepth, "_installed", lambda self, key: False)
    blur = raw.bokehEditor
    blur.adjust("amount", 50)
    blur.flush()
    assert blur.unavailable and not blur.busy and "bokeh" not in raw._draft and blur.error


def test_a_photo_switch_never_applies_a_late_map(raw, qapp):
    blur = raw.bokehEditor
    gate = []

    def slow(*args):
        import time
        while not gate:
            time.sleep(0.01)
        return ground()

    blur._runner = slow
    blur.adjust("amount", 50)
    blur.flush()
    assert blur.busy
    raw.open("other.NEF")
    gate.append(1)
    pump(qapp, .2)
    assert not blur.busy and "bokeh" not in raw._draft


# --- what the native renderer is sent, what is saved --------------------------------------------------------------------------

def test_the_renderer_is_never_sent_the_blur_and_a_save_lays_it_over_the_picture(lit_photo, tmp_path):
    photo, original, sent = lit_photo
    blur = bokeh.normalized({"amount": 90, "focus": 0, "range": 5, "highlights": 0})
    draft = {"exposure": .25, "bokeh": blur}
    out = tmp_path / "preview"
    out.mkdir()
    RawEditor._work("preview", str(photo), original, draft, str(out))
    assert "masks" not in sent[-1] and "bokeh" not in sent[-1]
    RawEditor._work("save", str(photo), original, draft, "")
    assert "masks" not in sent[-1] and "bokeh" not in sent[-1]
    assert bridge.load_recipe(str(photo))["cameraRaw"] == {"exposure": .25}          # no host settings in the recipe
    assert bokeh.read_sidecar(bridge.recipe_path(str(photo))) == blur
    saved = QImage(str(bridge.saved_render_path(str(photo))))
    assert saved.width() == 32 and saved.height() == 24
    # Switched off: nothing laid over it; the file stays with the sliders. At Amount 0 the file goes.
    RawEditor._work("save", str(photo), {"exposure": .25}, {"exposure": .25, "bokeh": {**blur, "shown": 0}}, "")
    assert bokeh.read_sidecar(bridge.recipe_path(str(photo)))["shown"] == 0
    RawEditor._work("save", str(photo), {"exposure": .25}, {"exposure": .25}, "")
    assert not bokeh.sidecar_path(bridge.recipe_path(str(photo))).exists()


def test_fog_and_blur_are_both_laid_over_a_save_and_the_blur_comes_second(lit_photo, tmp_path):
    photo, original, sent = lit_photo
    both = {"exposure": .25, "atmosphere": atmosphere.with_preset({}, 2), "bokeh": bokeh.normalized({"amount": 80, "focus": 0, "range": 5})}
    RawEditor._work("save", str(photo), original, both, "")
    assert atmosphere.read_sidecar(bridge.recipe_path(str(photo))) and bokeh.read_sidecar(bridge.recipe_path(str(photo)))
    assert "masks" not in sent[-1]
    assert not list(photo.parent.glob("*.fx*.png")) and not list(photo.parent.glob("*.fog.png"))      # no scratch files are left


def test_loading_a_photo_with_bokeh_attaches_its_settings_without_a_mask(tmp_path):
    photo = tmp_path / "IMG_1.NEF"
    photo.write_bytes(b"raw")
    recipe_with(photo, {"exposure": 0.1})
    blur = bokeh.normalized({"amount": 40, "shape": 3})
    bokeh.write_sidecar(bridge.recipe_path(str(photo)), blur)
    settings = RawEditor._work("load", str(photo), {}, {}, "")[0]
    assert settings["bokeh"] == blur and settings["exposure"] == 0.1 and "masks" not in settings


def test_a_save_without_the_depth_map_says_why_and_changes_nothing(lit_photo, monkeypatch):
    photo, original, sent = lit_photo
    before = bridge.recipe_path(str(photo)).read_bytes()
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: photo.parent / "gone.png")
    with pytest.raises(ValueError, match="depth map is missing"):
        RawEditor._work("save", str(photo), original, {"exposure": .25, "bokeh": bokeh.normalized({"amount": 50})}, "")
    assert bridge.recipe_path(str(photo)).read_bytes() == before
    assert not list(photo.parent.glob("*.fx*.png"))


def test_the_recipe_helpers_strip_the_hosts_entries():
    draft = {"exposure": .1, "bokeh": bokeh.normalized({"amount": 50})}
    assert _without_light_depth(draft) == {"exposure": .1}
    assert _recipe(draft, "photo.nef")["cameraRaw"] == {"exposure": .1}


def test_the_live_preview_lays_the_blur_over_the_draft_and_over_the_saved_picture(qapp, monkeypatch, tmp_path):
    photo, worker, calls, done, errors = preview_worker(monkeypatch, tmp_path)
    saved = bokeh.normalized({"amount": 30, "focus": 0, "range": 5})
    draft = {"bokeh": bokeh.normalized({"amount": 100, "focus": 0, "range": 5})}
    worker.submit(1, str(photo), {"bokeh": saved}, draft)             # the saved edit already has blur
    wait(qapp, lambda: done or errors)
    assert not errors, errors
    for call in calls:
        recipe = call.get("recipe", {}).get("cameraRaw", {})
        assert "masks" not in recipe and "bokeh" not in recipe
    assert done and done[-1][1][0][1].width() == 40                   # the frames came through the blur


def test_a_blur_is_not_part_of_a_look_that_is_pasted_to_another_photo():
    from image_triage import raw_presets
    from image_triage.full_render import HOST_SETTINGS

    assert "bokeh" in raw_presets.PHOTO_SPECIFIC and "bokeh" in HOST_SETTINGS


# --- the panel ---------------------------------------------------------------------------------------------------------------

PANEL = (b'import QtQuick\nItem { width: 300; height: 1100\n RawBokeh { objectName: "panel"; width: 300;'
         b' model: rawModel.bokehEditor; raw: rawModel } }')


def test_the_panel_renders_with_all_its_controls_and_drives_the_settings(raw, qapp):
    item, keep = make_item(raw, PANEL)
    try:
        blur = raw.bokehEditor
        wait(qapp, lambda: blur.ready)
        for name in ("bokehShapes", "bokeh_amount", "bokeh_focus", "bokeh_range", "bokehPicture", "bokehPictureFocus", "bokehAdvanced",
                     "bokehShown", "bokehRemove"):
            assert find_item(item, name) is not None, name
        for index in range(6):
            assert find_item(item, f"bokehShape_{index}") is not None
        panel = find_item(item, "panel")
        panel.setProperty("advanced", True)
        pump(qapp, .05)
        assert find_item(item, "bokeh_highlights_slider") is not None
        amount = find_item(item, "bokeh_amount_slider")
        amount.setProperty("value", 45)
        amount.moved.emit()
        blur.flush()
        assert blur.settings["amount"] == 45 and raw._draft["bokeh"]["amount"] == 45
        focus = find_item(item, "bokeh_focus_slider")
        focus.setProperty("value", 22)
        focus.moved.emit()
        blur.flush()
        assert raw._draft["bokeh"]["focus"] == 22
    finally:
        item.deleteLater()
        pump(qapp, .05)


from test_qml_shell_viewer import shell  # noqa: E402,F401


def test_bokeh_is_a_collapsible_section_in_the_creative_tab(shell, qapp):
    from PySide6.QtQuick import QQuickItem

    engine, window, model, popout, warnings = shell
    popout.open(0)
    raw = popout.rawEditor
    raw.open("sample.NEF")
    image = QImage(60, 40, QImage.Format_RGBA8888)
    image.fill(QColor("gray"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [6000, 4000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    pump(qapp, 0.4)
    dock = window.findChild(QQuickItem, "viewerRawEditorPanel")
    dock.setProperty("tool", "Creative")
    pump(qapp, 0.2)

    def walk(node):
        yield node
        for child in node.childItems():
            yield from walk(child)

    def named(name):
        return next((node for node in walk(dock) if node.objectName() == name), None)

    page = named("rawBokehPage")
    assert page is not None and page.property("visible") and page.property("title") == "Bokeh" and not page.property("expanded")
    assert named("bokeh_amount_slider") is None
    page.setProperty("expanded", True)
    pump(qapp, 0.3)
    assert named("bokeh_amount_slider") is not None and named("bokehShapes") is not None
    page.setProperty("expanded", False)
    pump(qapp, 0.2)
    assert named("bokeh_amount_slider") is None
    raw.cancel()
    pump(qapp, 0.2)
    assert not [w for w in warnings if "RawEditorPanel.qml" in w or "RawBokeh.qml" in w or "TypeError" in w], warnings
