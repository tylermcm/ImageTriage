"""WI-8.1: ``FullScreenPreview.apply_theme`` must not restyle the whole dialog
tree when nothing it consumes has changed (Qt re-polishes on every identical
``setStyleSheet``, ~230 ms), yet must still restyle for every input that does
feed the Studio styling: the theme (so gamma), the dialog height, and the set
of panes."""
from __future__ import annotations

import os
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication

from image_triage.preview import FullScreenPreview
from image_triage.ui.theme import AppearanceMode, apply_gamma, default_theme, resolve_theme


@pytest.fixture
def preview():
    app = QApplication.instance() or QApplication([])
    dialog = FullScreenPreview()
    dialog.resize(1200, 800)
    app.processEvents()
    dialog.apply_theme(dialog._theme)  # settle at the new height (a real input change, so this one restyles)
    try:
        yield dialog
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()


def _watch(preview: FullScreenPreview):
    """Counters for the expensive steps; both delegate to the real code."""
    studio = mock.patch.object(preview, "_apply_studio_theme", wraps=preview._apply_studio_theme)
    sheet = mock.patch.object(preview, "setStyleSheet", wraps=preview.setStyleSheet)
    return studio, sheet


def test_identical_theme_is_a_noop_after_the_studio_style_was_applied(preview) -> None:
    theme = preview._theme
    assert getattr(preview, "_studio_applied_key", None) is not None, "Studio style should be applied by __init__"
    studio, sheet = _watch(preview)
    with studio as studio_mock, sheet as sheet_mock:
        preview.apply_theme(theme)
        preview.apply_theme(default_theme())  # an equal-by-value theme object, not the same instance
    assert studio_mock.call_count == 0
    assert sheet_mock.call_count == 0


def test_a_genuinely_different_theme_still_restyles_once(preview) -> None:
    app = QApplication.instance()
    other = resolve_theme(AppearanceMode.LIGHT, app)
    assert other != preview._theme
    studio, sheet = _watch(preview)
    with studio as studio_mock, sheet as sheet_mock:
        preview.apply_theme(other)
        assert studio_mock.call_count == 1
        assert sheet_mock.call_count >= 1
        assert preview._theme == other
        # Re-applying that same theme is now a no-op again.
        calls = studio_mock.call_count
        preview.apply_theme(resolve_theme(AppearanceMode.LIGHT, app))
        assert studio_mock.call_count == calls
        # And going back is a real change again.
        preview.apply_theme(default_theme())
        assert studio_mock.call_count == calls + 1


def test_a_changed_gamma_input_still_restyles(preview) -> None:
    base = preview._theme
    brighter = apply_gamma(base, 1.3)
    assert brighter != base, "gamma 1.3 must produce a different palette for this test to mean anything"
    studio, _sheet = _watch(preview)
    with studio as studio_mock:
        preview.apply_theme(brighter)
        assert studio_mock.call_count == 1
        preview.apply_theme(apply_gamma(base, 1.3))  # same gamma again: nothing new
        assert studio_mock.call_count == 1
        preview.apply_theme(apply_gamma(base, 0.8))  # another gamma: restyle
        assert studio_mock.call_count == 2


def test_a_changed_dialog_height_still_restyles(preview) -> None:
    # The Studio stylesheet scales its type sizes with the dialog height, so an
    # equal theme at a different height is NOT a no-op.
    preview.resize(1200, 500)
    tall_sheet = preview._studio_stylesheet_full()
    preview.resize(1200, 1100)
    preview._apply_studio_theme()
    assert preview._studio_stylesheet_full() != tall_sheet, "the stylesheet no longer depends on height; update the key"
    # Same theme, same height as the last application: no-op.
    studio, _sheet = _watch(preview)
    with studio as studio_mock:
        preview.apply_theme(preview._theme)
        assert studio_mock.call_count == 0
        preview.resize(1200, 500)
        preview.apply_theme(preview._theme)
        assert studio_mock.call_count == 1


def test_a_new_pane_forces_the_next_apply(preview) -> None:
    studio, _sheet = _watch(preview)
    with studio as studio_mock:
        preview.apply_theme(preview._theme)
        assert studio_mock.call_count == 0
        preview._ensure_panes(len(preview._panes) + 1)
        preview.apply_theme(preview._theme)
        assert studio_mock.call_count == 1


def test_a_failed_application_is_not_recorded_as_done(preview) -> None:
    other = resolve_theme(AppearanceMode.LIGHT, QApplication.instance())
    with mock.patch.object(preview, "_studio_stylesheet_full", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            preview.apply_theme(other)
    studio, _sheet = _watch(preview)
    with studio as studio_mock:
        preview.apply_theme(other)  # must retry, not believe it was applied
        assert studio_mock.call_count == 1


def test_main_window_startup_restyles_the_preview_only_once(dialogs) -> None:
    """The viewer is no longer built during window startup (WI-8.1 stage B), so startup applies no
    studio theme at all; when it is built on first use it styles itself in its own __init__ and the
    window's replay of the identical theme must be a no-op, i.e. one pass in total."""
    # _fresh_window leaves the app-wide stylesheet/palette alone. Applying them repolishes every live
    # widget, and late in a full run the widgets leaked by earlier tests made this one test take ~110 s
    # against conftest's 120 s per-test hard timeout. The viewer's studio styling is per-widget, so
    # nothing counted here depends on the app-wide sheet.
    from tests.test_preview_lazy_build import _fresh_window

    calls = []
    real = FullScreenPreview._apply_studio_theme

    def counting(self):
        calls.append(self.height())
        return real(self)

    with mock.patch.object(FullScreenPreview, "_apply_studio_theme", counting):
        with _fresh_window() as window:
            assert calls == [], f"startup applied the studio theme {len(calls)} times; the viewer should not exist yet"
            window.preview  # the lazy build
            assert len(calls) == 1, f"preview studio theme applied {len(calls)} times while building it"
