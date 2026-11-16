"""Auto tone and white balance: the rules move the right sliders the right way, stay in range and leave good pictures alone."""
import numpy as np
import pytest

from image_triage import auto_tone
from test_qml_shell_viewer import qapp  # noqa: F401  (a QApplication for the editor tests)


def scene(level=0.45, spread=0.25, seed=0, size=(240, 320), tint=(1.0, 1.0, 1.0)):
    """A grey-ish scene (display values) of the given median level and spread, with an optional colour cast."""
    rng = np.random.default_rng(seed)
    base = np.clip(level + spread * rng.standard_normal(size) * 0.5, 0.0, 1.0)
    pic = np.stack([base * tint[0], base * tint[1], base * tint[2]], axis=-1)
    return (np.clip(pic, 0, 1) * 255).astype(np.uint8)


def test_a_dark_picture_is_brightened_and_a_bright_one_darkened():
    assert auto_tone.tone(scene(0.12))["exposure"] > 0.5
    assert auto_tone.tone(scene(0.85, 0.12))["exposure"] < -0.5


def test_exposure_never_pushes_the_top_into_clipping():
    pic = scene(0.2, 0.1)
    pic[:30] = 250                                                 # a band already near white
    assert auto_tone.tone(pic).get("exposure", 0.0) < 0.3


def test_a_good_picture_is_left_nearly_alone():
    changes = auto_tone.tone(scene(0.46, 0.30))
    assert abs(changes.get("exposure", 0)) < 0.4 and abs(changes.get("highlights", 0)) < 15 and abs(changes.get("shadows", 0)) < 15


def test_crushed_shadows_and_blown_highlights_get_shadows_and_highlights():
    split = scene(0.5, 0.05)
    split[: int(split.shape[0] * 0.4)] = 12                        # a big dark foreground under a normal sky: exposure is right, shadows are not
    assert auto_tone.tone(split).get("shadows", 0) > 20
    bright = scene(0.45, 0.1)
    bright[: int(bright.shape[0] * 0.12)] = 252                     # a bright sky over a normal scene
    assert auto_tone.tone(bright).get("highlights", 0) < -20


def test_flat_pictures_gain_contrast_and_harsh_ones_lose_some():
    assert auto_tone.tone(scene(0.45, 0.06)).get("contrast", 0) > 5
    harsh = np.where(np.random.default_rng(2).random((200, 300)) > 0.5, 0.05, 0.95)
    assert auto_tone.tone((np.stack([harsh] * 3, -1) * 255).astype(np.uint8)).get("contrast", 0) < -5


def test_values_are_relative_to_the_current_settings_and_stay_in_range():
    base = scene(0.1, 0.15)
    first = auto_tone.tone(base, {})
    again = auto_tone.tone(base, first)                            # the same picture, as if nothing had been applied
    assert again["exposure"] >= first["exposure"]
    assert all(-5 <= v <= 5 for k, v in again.items() if k == "exposure") and all(-100 <= v <= 100 for v in again.values())
    assert auto_tone.tone(base, {"exposure": 4.9}).get("exposure", 4.9) <= 5.0
    half = auto_tone.tone(base, {}, strength=0.5)
    assert 0 < half["exposure"] < first["exposure"]
    assert auto_tone.tone(base, {}, strength=0.0) == {}


def test_a_blue_cast_on_neutral_things_is_warmed_and_a_green_cast_gets_tint():
    blue = auto_tone.white_balance(scene(0.45, 0.2, tint=(0.82, 0.95, 1.1)))
    assert blue.get("temperature", 0) > 5
    orange = auto_tone.white_balance(scene(0.45, 0.2, tint=(1.12, 0.96, 0.8)))
    assert orange.get("temperature", 0) < -5
    green = auto_tone.white_balance(scene(0.45, 0.2, tint=(0.9, 1.08, 0.9)))
    assert green.get("tint", 0) != 0 and abs(green.get("tint", 0)) > 3


def test_a_neutral_picture_and_a_picture_with_nothing_neutral_in_it_keep_their_balance():
    assert auto_tone.white_balance(scene(0.45, 0.2)) == {}
    lake = np.zeros((200, 300, 3), np.uint8)
    lake[...] = (20, 150, 190)                                     # all one saturated colour: that is the subject, not a cast
    assert auto_tone.white_balance(lake) == {}


def test_a_stronger_setting_moves_further_and_zero_moves_nothing():
    pic = scene(0.45, 0.2, tint=(0.8, 0.95, 1.12))
    full, half = auto_tone.white_balance(pic, {}, 1.0), auto_tone.white_balance(pic, {}, 0.5)
    assert full["temperature"] > half["temperature"] > 0
    assert auto_tone.white_balance(pic, {}, 0.0) == {}


def test_auto_is_both_and_small_pictures_work():
    pic = scene(0.15, 0.15, tint=(0.8, 0.95, 1.12), size=(20, 30))
    both = auto_tone.auto(pic)
    assert "temperature" in both and "exposure" in both
    auto_tone.tone(np.zeros((4, 4, 3), np.uint8))                  # all black: no crash
    auto_tone.white_balance(np.full((4, 4, 3), 255, np.uint8))


def make_editor(monkeypatch, picture):
    from PySide6.QtGui import QImage
    from image_triage.qml_shell import raw_preview
    from image_triage.qml_shell.raw_editor import RawEditor
    from test_qml_shell_viewer import qapp  # noqa: F401  (a QApplication for the editor)

    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(picture.data, picture.shape[1], picture.shape[0], picture.shape[1] * 3, QImage.Format.Format_RGB888).copy()
    raw._preview = "image://rawpreview/x"
    monkeypatch.setattr(raw_preview, "frame_image", lambda url: image)
    return raw


def test_auto_tone_is_a_switch_press_to_apply_press_again_to_take_it_off(qapp, monkeypatch):
    raw = make_editor(monkeypatch, scene(0.1, 0.15))
    before = len(raw._undo)
    assert not raw.autoToneOn
    raw.toggleAuto("tone")
    assert raw.autoToneOn and len(raw._undo) == before + 1 and raw._draft["exposure"] > 0.5
    applied = {k: raw._draft[k] for k in ("exposure",)}
    raw.toggleAuto("tone")                                          # the second press takes it off, it does not stack
    assert not raw.autoToneOn and len(raw._undo) == before + 2
    assert raw._draft.get("exposure", 0) == 0 and "contrast" not in raw._draft or raw._draft.get("contrast", 0) == 0
    raw.toggleAuto("tone")                                          # and a third applies the same thing again
    assert raw.autoToneOn and raw._draft["exposure"] == applied["exposure"]
    raw.undo()
    assert not raw.autoToneOn                                       # undoing it by hand also switches it off
    raw.shutdown()


def test_auto_white_balance_is_a_switch_and_tone_and_white_balance_are_independent(qapp, monkeypatch):
    raw = make_editor(monkeypatch, scene(0.15, 0.15, tint=(0.8, 0.95, 1.12)))
    raw.toggleAuto("wb")
    assert raw.autoWbOn and not raw.autoToneOn and raw._draft.get("temperature", 0) > 0
    raw.toggleAuto("tone")
    assert raw.autoWbOn and raw.autoToneOn
    raw.toggleAuto("wb")
    assert not raw.autoWbOn and raw.autoToneOn and raw._draft.get("temperature", 0) == 0 and raw._draft["exposure"] > 0
    raw.shutdown()


def test_the_strength_slider_changes_what_is_applied_at_once(qapp, monkeypatch):
    raw = make_editor(monkeypatch, scene(0.1, 0.15))
    raw.toggleAuto("tone")
    full = raw._draft["exposure"]
    raw.setAutoStrength(50)
    assert raw.autoToneOn and 0 < raw._draft["exposure"] < full and raw.autoStrength == 50
    raw.setAutoStrength(0)
    assert raw._draft.get("exposure", 0) == 0 and raw.autoToneOn
    raw.setAutoStrength(100)
    assert raw._draft["exposure"] == full
    raw.toggleAuto("tone")
    raw.setAutoStrength(30)                                         # nothing applied: the slider changes nothing in the photo
    assert raw._draft.get("exposure", 0) == 0
    raw.toggleAuto("tone")                                          # but it is used by the next press
    assert 0 < raw._draft["exposure"] < full
    raw.shutdown()


def test_auto_says_so_when_nothing_is_needed_or_the_preview_is_not_ready(qapp, monkeypatch):
    raw = make_editor(monkeypatch, scene(0.46, 0.30))
    raw.toggleAuto("wb")
    assert raw.notice == "No clear colour cast found." and not raw.autoWbOn
    raw._preview = ""
    raw.toggleAuto("tone")
    assert "preview" in raw.notice
    raw.shutdown()
