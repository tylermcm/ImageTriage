"""Auto tone and white balance: the rules move the right sliders the right way, stay in range and leave good pictures alone."""
import numpy as np
import pytest

from image_triage import auto_tone


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


def test_the_editor_applies_auto_as_one_undo_step_and_says_so_when_nothing_is_needed(monkeypatch):
    from PySide6.QtGui import QImage
    from image_triage.qml_shell import raw_preview
    from image_triage.qml_shell.raw_editor import RawEditor
    from test_qml_shell_viewer import qapp  # noqa: F401  (a QApplication for the editor)

    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    dark = scene(0.1, 0.15, tint=(0.85, 0.95, 1.1))
    image = QImage(dark.data, dark.shape[1], dark.shape[0], dark.shape[1] * 3, QImage.Format.Format_RGB888).copy()
    raw._preview = "image://rawpreview/x"
    monkeypatch.setattr(raw_preview, "frame_image", lambda url: image)
    before = len(raw._undo)
    raw.auto("all", 100)
    assert len(raw._undo) == before + 1 and raw._draft["exposure"] > 0.5 and raw._draft.get("temperature", 0) > 0
    raw.undo()
    assert "exposure" not in raw._draft or raw._draft["exposure"] == 0
    raw.auto("all", 0)
    assert raw.notice == "No change needed."
    raw._preview = ""
    raw.auto("tone", 100)
    assert "preview" in raw.notice
    raw.shutdown()
