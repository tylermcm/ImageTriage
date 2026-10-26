"""The three tones of the dark theme (Slate, Soft, Dim), the setting that picks one, the sun button, and the dim behind dialogs."""
from __future__ import annotations

import pytest
from PySide6.QtCore import QCoreApplication, QSettings
from PySide6.QtGui import QColor, QGuiApplication

from image_triage.qml_shell import theme as theme_module
from image_triage.qml_shell.settings_model import SettingsModel
from image_triage.qml_shell.theme import DARK_TONES, Theme, _palette_spec


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = str(tmp_path / "settings.ini")
    monkeypatch.setattr("image_triage.app_identity.user_settings", lambda parent=None: QSettings(path, QSettings.Format.IniFormat))
    return path


def lightness(color: QColor) -> float:
    return color.lightnessF()


def test_each_tone_lifts_the_whole_dark_theme_and_slate_stays_the_original(qapp):
    tones = {}
    for tone in ("slate", "soft", "dim"):
        theme = Theme(parent=qapp)
        theme.applyMode("slate", -1, 90, True, tone)
        tones[tone] = theme
    base = {t: tones[t].base.name() for t in tones}
    assert base == {"slate": "#0d0e11", "soft": "#1d1f23", "dim": "#2b2e33"}
    for name in ("s1", "s2", "s3", "line"):               # every step keeps its distance above the base, so panels and lines still read
        values = [lightness(getattr(tones[t], name)) for t in ("slate", "soft", "dim")]
        assert values == sorted(values) and values[0] < values[2], name
    assert not tones["dim"].light and tones["dim"].fg.lightnessF() > 0.85          # text stays light on the lifted base


def test_only_the_slate_theme_takes_a_tone_and_the_other_themes_are_unchanged(qapp):
    midnight_slate = _palette_spec("midnight", True, "dim")
    assert midnight_slate == _palette_spec("midnight", True, "slate")
    assert _palette_spec("light", True, "dim") == _palette_spec("light", True)
    assert _palette_spec("slate", True)["base"][0] == DARK_TONES["slate"][0]       # the default argument is the original
    assert _palette_spec("auto", True, "soft")["base"][0] == DARK_TONES["soft"][0] and _palette_spec("auto", False, "soft")["base"][0] > 0.9


def test_the_dim_behind_a_dialog_follows_the_tone_and_the_light_theme_has_its_own(qapp):
    theme = Theme(parent=qapp)
    alphas = {}
    for tone in ("slate", "soft", "dim"):
        theme.applyMode("slate", -1, 90, True, tone)
        alphas[tone] = theme.scrim.alpha()
    assert alphas["slate"] > alphas["soft"] > alphas["dim"] and alphas["slate"] == 133      # 52%, the mockup's value
    theme.applyMode("midnight", -1, 90, True, "dim")
    assert theme.scrim.alpha() == 133                                                         # another dark theme keeps the full dim
    theme.applyMode("light", -1, 90, True, "dim")
    assert (theme.scrim.red(), theme.scrim.alpha()) == (20, 87)


def test_the_setting_defaults_to_soft_for_real_users_and_slate_for_the_sample_page(qapp, store):
    real = SettingsModel(parent=qapp)
    real.load()
    assert real.darkTone == "soft"
    sample = SettingsModel(memory=True, parent=qapp)
    sample.load()
    assert sample.darkTone == "slate"                      # the mockup's own look, so the pixel checks against it hold


def test_picking_a_tone_previews_at_once_saves_under_its_own_key_and_survives_a_reload(qapp, store):
    model = SettingsModel(parent=qapp)
    model.load()
    changed = []
    model.previewChanged.connect(lambda: changed.append(model.darkTone))
    model.set("darkTone", "dim")
    assert changed[-1] == "dim"
    assert model.save()
    assert QSettings(store, QSettings.Format.IniFormat).value("view/dark_tone") == "dim"
    assert QSettings(store, QSettings.Format.IniFormat).value("window/appearance") in (None, "slate")        # the Widgets app's own key is not touched
    again = SettingsModel(parent=qapp)
    again.load()
    assert again.darkTone == "dim"
    again.set("darkTone", "nonsense")
    assert again.darkTone in ("soft", "dim", "slate")


def test_the_sun_button_swaps_between_light_and_the_dark_theme_that_was_in_use(qapp, store):
    model = SettingsModel(parent=qapp)
    model.load()
    model.set("theme", "midnight")
    model.toggleLight()
    assert model.themeMode == "light"
    model.toggleLight()
    assert model.themeMode == "midnight"                   # back to the dark theme it left, not always Slate
    model.set("theme", "slate")
    model.toggleLight()
    model.toggleLight()
    assert model.themeMode == "slate"
