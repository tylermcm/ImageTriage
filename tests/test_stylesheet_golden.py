"""Characterization test for the large stylesheet builders (audit finding A2).

``build_app_stylesheet`` (ui/theme.py), ``build_studio_dialog_stylesheet``
(ui/preview_studio_stylesheet.py) and the People dialog's ``_stylesheet``
(ui/people_dialog.py) are each assembled from many small section functions.
The cascade is order-sensitive and the app compares stylesheet text for
equality, so splitting or reordering sections must never change the produced
text by even one character. This test pins the exact output of a compact grid
of inputs by SHA-256 digest and length; it is deliberately not a statement
that the CSS is *right*, only that it is *unchanged*.

If a stylesheet change is intentional, regenerate the digest table:

    # PowerShell
    $env:UPDATE_STYLESHEET_GOLDEN = "1"
    pythonw3.13.exe scripts/run313.py regen.log pytest -q -s tests/test_stylesheet_golden.py
    Remove-Item Env:UPDATE_STYLESHEET_GOLDEN

then paste the table that the run prints (it is also written to
``<temp dir>/image_triage_stylesheet_golden.txt``) over ``_GOLDEN`` below and
review the diff: a changed digest should correspond to a change you meant.

The checkbox image path is machine specific (it is an absolute path into the
checkout), so every case pins it to a fixed value; see ``_FIXED_ASSET``.

The structural tests further down do not depend on exact text: they check that
the sections still join in their documented order, that none is empty, and
that the output has balanced braces and no leftover f-string artifacts.
"""
from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

import image_triage.ui.preview_studio_stylesheet as studio_style
import image_triage.ui.theme as theme_mod
from image_triage.preview import FullScreenPreview
from image_triage.ui.people_dialog import PeopleSearchDialog
from image_triage.ui.preview_studio_stylesheet import build_studio_dialog_stylesheet
from image_triage.ui.theme import (
    AppearanceMode,
    ColorToken,
    ThemePalette,
    apply_gamma,
    build_app_palette,
    build_app_stylesheet,
    resolve_theme,
)

UPDATE_ENV = "UPDATE_STYLESHEET_GOLDEN"
_FIXED_ASSET = "C:/golden/checkbox_check.png"
_SENTINEL_FONT_STACK = '"Sentinel Font", serif'

# case id -> (sha256 of the stylesheet text, its length in characters)
_GOLDEN: dict[str, tuple[str, int]] = {
    "app|slate|gamma=1.0": ("30f08aa71b7e29f1d12f3fb8df3e3be2f0bf17fcbde866d7c9bf944d113aef10", 101119),
    "app|indigo|gamma=1.0": ("995c52100ab3275591321d99af27d8ba06f537261129144a1bba513002318216", 101666),
    "app|dark|gamma=1.0": ("49b5b33e5f06525aa6d30d883fee1ca1c698fed3a72ec4c6762d7137730e525d", 101202),
    "app|midnight|gamma=1.0": ("64a94f12dc7b2b07e96bfaeb1831a4ab4c4007d5e76ee35fb2b4b36d13213c81", 101290),
    "app|graphite|gamma=1.0": ("174e331511b2beed923f11d9143bca9f64123059a4a7ef928290675371de6f47", 101402),
    "app|forest|gamma=1.0": ("d5a69bb97beab3d698e0d38be5ace940113fc36daa74aa6fba8b7da7439de4d1", 101248),
    "app|high_contrast|gamma=1.0": ("3e2aad2f47e426a2a90ede6c5c3bbf8146e819a44e6654de82161070026ce22d", 101269),
    "app|warm_neutral|gamma=1.0": ("5c20795d3a606935793057b51d27d06df170088d225201e439345a02277bcbb5", 101621),
    "app|light|gamma=1.0": ("9b1e6665a9b34ea737b36eed51dc54e4679cb71df7711a2bb967909b857fe51d", 101669),
    "app|slate|gamma=0.7": ("6158bc7d836b567828d2e207b963ce71cd99682fc9e42b746cf601677f42a122", 100819),
    "app|slate|gamma=1.4": ("85c90a11aca3cf25a470c43b59a7350132025d0dbbe4e91fa886650dce1a07b6", 101263),
    "app|indigo|gamma=1.25": ("21d919212f9d9409e47b2b4b8b96b16c485826d2b65f20d607a43b2e7dd31bf4", 101726),
    "app|dark|gamma=0.6": ("94d6afe17867060d0bc44be6cae8adaf5a7f5416189d3b3c6efa748dc3fbf21e", 100120),
    "app|light|gamma=0.8": ("9767a0914c9c41a8d940d922b7b2f30d08537b0299e2f0510ef4b271f151c71f", 101429),
    "app|high_contrast|gamma=1.6": ("85670f87b2379e5ab494de2ed4fbacb2adbfde28601a1b1b7d4fc33917518689", 101556),
    "app|warm_neutral|gamma=1.15": ("0895e9d491c32fa54d80ab15e5f08d56f729b833f83a63df7710e6904ed0fb6b", 101621),
    "app|indigo|no-secondary-glow": ("be18a0a242af0444357d59f2fcc6c717b846fa12353caa6aa757091ef9888ddd", 101666),
    "app|slate|primary-glow-only": ("4fc1668fa13ad802e1095af42db79db9b663e45e573e153637c271b9e2c24fd7", 101498),
    "app|slate|sentinel-metrics": ("699e64c6dd89dc34ea9201042075ada8089242f03c6c2f77f1c84be4b9f4b527", 101135),
    "app|slate|sentinel-font-stack": ("aa96931cdb4515ecd21c859d3fda3b722a52432f81597e5d3354d5307754cb63", 100979),
    "studio|h=0": ("e81d34eacb1aac4290daa2ae181de91ee1f7018b8910ae6d397e47942832cf1f", 41726),
    "studio|h=1": ("e81d34eacb1aac4290daa2ae181de91ee1f7018b8910ae6d397e47942832cf1f", 41726),
    "studio|h=300": ("e81d34eacb1aac4290daa2ae181de91ee1f7018b8910ae6d397e47942832cf1f", 41726),
    "studio|h=720": ("e81d34eacb1aac4290daa2ae181de91ee1f7018b8910ae6d397e47942832cf1f", 41726),
    "studio|h=1080": ("cdda1de8711adc16a5b9d560330ff145f71e9d7e1692dd4a407e92708c16e49c", 41726),
    "studio|h=1191": ("3c772a0ba73e30d5ea7b1b0a3261ca30248fc65d52655ecb2d85999ab00d7597", 41726),
    "studio|h=1440": ("871e46b084d03e3ff56afcd4573f9518a33469de7c028aca91927304bbf3f3be", 41727),
    "studio|h=2160": ("13ab0b2ccd7b237bf031c60642888871a5738f781e5b551dcbb876418e2e02fe", 41727),
    "studio|h=4000": ("093ed461dff4fe3063532d5bc7862de87daae9860ffc4a7c9ea7813c703800d2", 41729),
    "studio|unscoped|h=1191": ("b7326756c59da0fca3a40796b1c87db866c736ac04f7fd4eb01564d574aafdcf", 36693),
    "studio|scope=QDialog#other|h=720": ("1a9663a9c6904f7c25f122a761f834bd7a025bc5413cea27f6c05f9ff22309e8", 37554),
    "people|slate|gamma=1.0": ("82e3e6bc7642d442c0c2bd39694707dbb6f52556122da430f6cfb0b0b3194ab7", 4343),
    "people|light|gamma=1.0": ("f6d0b3d49e33ab4e7033a5537df3d1836d21ad3501ee2f286a6224d6434533d6", 4343),
    "people|forest|gamma=1.0": ("ded3784ff8a360d708372c8d1339136a786ca513b971a560024548e9cdab27f8", 4343),
    "people|indigo|gamma=1.3": ("8d75ffeb3dd0ad8f1818cd58ade3c3d81e12647361c26a04c1824b455201ac68", 4343),
}


@pytest.fixture(scope="module")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


# --- Case grid ---------------------------------------------------------------
# Every non-auto theme at gamma 1.0, plus gamma, optional-field and module
# constant variants (the stylesheet takes only the palette, so there is no
# interface-size axis for the app sheet).
_APP_THEME_GAMMAS: list[tuple[AppearanceMode, float]] = [
    *((mode, 1.0) for mode in AppearanceMode if mode != AppearanceMode.AUTO),
    (AppearanceMode.SLATE, 0.7),
    (AppearanceMode.SLATE, 1.4),
    (AppearanceMode.INDIGO, 1.25),
    (AppearanceMode.DARK, 0.6),
    (AppearanceMode.LIGHT, 0.8),
    (AppearanceMode.HIGH_CONTRAST, 1.6),
    (AppearanceMode.WARM_NEUTRAL, 1.15),
]
# Dialog heights for the Studio sheet: edge values (0 clamps to 1), small,
# typical, the 2048 x 1191 reference composition, and large.
_STUDIO_HEIGHTS = [0, 1, 300, 720, 1080, 1191, 1440, 2160, 4000]
_PEOPLE_THEMES: list[tuple[AppearanceMode, float]] = [
    (AppearanceMode.SLATE, 1.0),
    (AppearanceMode.LIGHT, 1.0),
    (AppearanceMode.FOREST, 1.0),
    (AppearanceMode.INDIGO, 1.3),
]


@contextmanager
def _patched_theme_module(**values: object) -> Iterator[None]:
    """Pin module-level inputs of ui/theme.py for one render."""
    saved = {name: getattr(theme_mod, name) for name in values}
    try:
        for name, value in values.items():
            setattr(theme_mod, name, value)
        yield
    finally:
        for name, value in saved.items():
            setattr(theme_mod, name, value)


def _app_sheet(theme: ThemePalette, **module_values: object) -> str:
    with _patched_theme_module(CHECKBOX_CHECK_ASSET=_FIXED_ASSET, **module_values):
        return build_app_stylesheet(theme)


def _case_factories(app: QApplication) -> dict[str, Callable[[], str]]:
    """Case id -> zero-argument callable that renders that case's text."""
    cases: dict[str, Callable[[], str]] = {}

    for mode, gamma in _APP_THEME_GAMMAS:
        theme = apply_gamma(resolve_theme(mode, app), gamma)
        cases[f"app|{mode.value}|gamma={gamma}"] = lambda theme=theme: _app_sheet(theme)

    indigo = resolve_theme(AppearanceMode.INDIGO, app)
    slate = resolve_theme(AppearanceMode.SLATE, app)
    # Optional palette fields change which branches the helper sections take.
    cases["app|indigo|no-secondary-glow"] = lambda: _app_sheet(
        dataclasses.replace(indigo, backdrop_glow_secondary=None)
    )
    cases["app|slate|primary-glow-only"] = lambda: _app_sheet(
        dataclasses.replace(slate, backdrop_glow_primary=ColorToken(29, 26, 58))
    )
    # Distinct sentinel values prove every metric / font reference stays wired.
    sentinel_metrics = theme_mod.WorkspaceMetrics(
        space_4=41, space_6=61, space_8=81, space_12=121, space_16=161, radius_4=44, radius_7=77
    )
    cases["app|slate|sentinel-metrics"] = lambda: _app_sheet(slate, WORKSPACE_METRICS=sentinel_metrics)
    cases["app|slate|sentinel-font-stack"] = lambda: _app_sheet(slate, UI_FONT_STACK=_SENTINEL_FONT_STACK)

    scope = FullScreenPreview.STUDIO_SCOPE
    for height in _STUDIO_HEIGHTS:
        cases[f"studio|h={height}"] = lambda height=height: build_studio_dialog_stylesheet(scope, height)
    cases["studio|unscoped|h=1191"] = lambda: build_studio_dialog_stylesheet("", 1191)
    cases["studio|scope=QDialog#other|h=720"] = lambda: build_studio_dialog_stylesheet("QDialog#other", 720)

    for mode, gamma in _PEOPLE_THEMES:
        palette = build_app_palette(apply_gamma(resolve_theme(mode, app), gamma))
        cases[f"people|{mode.value}|gamma={gamma}"] = lambda palette=palette: _people_sheet(palette)
    return cases


class _PaletteHolder:
    """Stands in for the dialog: ``_stylesheet`` reads only ``self.palette()``."""

    def __init__(self, palette: QPalette) -> None:
        self._palette = palette

    def palette(self) -> QPalette:
        return self._palette


def _people_sheet(palette: QPalette) -> str:
    return PeopleSearchDialog._stylesheet(_PaletteHolder(palette))  # type: ignore[arg-type]


def _digest(text: str) -> tuple[str, int]:
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text)


def _format_table(actual: dict[str, tuple[str, int]]) -> str:
    rows = [f'    "{case}": ("{digest}", {length}),' for case, (digest, length) in actual.items()]
    return "_GOLDEN: dict[str, tuple[str, int]] = {\n" + "\n".join(rows) + "\n}\n"


# --- The characterization test -----------------------------------------------
def test_stylesheet_text_is_unchanged(app: QApplication) -> None:
    actual = {case: _digest(render()) for case, render in _case_factories(app).items()}

    if os.environ.get(UPDATE_ENV) == "1":
        table = _format_table(actual)
        target = Path(tempfile.gettempdir()) / "image_triage_stylesheet_golden.txt"
        target.write_text(table, encoding="utf-8")
        print("\n" + table)
        pytest.skip(f"{UPDATE_ENV}=1: new digest table printed and written to {target}; paste it over _GOLDEN")

    problems: list[str] = []
    for case in sorted(set(_GOLDEN) | set(actual)):
        want, got = _GOLDEN.get(case), actual.get(case)
        if want is None:
            problems.append(f"  {case}: no pinned digest (new case); got {got}")
        elif got is None:
            problems.append(f"  {case}: pinned but no longer generated")
        elif want != got:
            problems.append(f"  {case}: expected {want[0][:16]}.../{want[1]} chars, got {got[0][:16]}.../{got[1]} chars")
    assert not problems, (
        "The generated stylesheet text changed. This is a CHARACTERIZATION test: it pins the exact\n"
        "output (SHA-256 + length) of the app, Studio-dialog and People-dialog stylesheet builders so that\n"
        "restructuring them cannot alter the CSS, even by whitespace or rule order.\n"
        "If the change is intentional, regenerate the table deliberately:\n"
        f"  set {UPDATE_ENV}=1 and run: pythonw3.13.exe scripts/run313.py regen.log pytest -q -s "
        "tests/test_stylesheet_golden.py\n"
        "then paste the printed table over _GOLDEN in this file (see the module docstring) and review the diff.\n"
        "Differences:\n" + "\n".join(problems)
    )


# --- Structural tests (independent of the exact text) ------------------------
_ALL_THEME_MODES = [mode for mode in AppearanceMode if mode != AppearanceMode.AUTO]


def _sections() -> tuple[tuple[Callable[[ThemePalette], str], ...], tuple[Callable[[ThemePalette], str], ...]]:
    return theme_mod._APP_STYLESHEET_SECTIONS, theme_mod._APP_STYLESHEET_PADDED_SECTIONS


@pytest.mark.parametrize("mode", _ALL_THEME_MODES, ids=lambda mode: mode.value)
def test_app_sections_join_in_their_documented_order(app: QApplication, mode: AppearanceMode) -> None:
    """Each section's text sits exactly where the previous one ended: first the
    plain sections back to back, then the four-space indent the original single
    string ended with, then the padded sections (which carry their own newlines)."""
    theme = resolve_theme(mode, app)
    plain, padded = _sections()
    text = build_app_stylesheet(theme)
    cursor = 0
    for section in plain:
        piece = section(theme)
        assert text.startswith(piece, cursor), f"{section.__name__} is not where the order table puts it"
        cursor += len(piece)
    assert text.startswith("\n    ", cursor), "the closing indent between the section groups is missing"
    cursor += len("\n    ")
    for section in padded:
        piece = section(theme)
        assert text.startswith(piece, cursor), f"{section.__name__} is not where the order table puts it"
        cursor += len(piece)
    assert cursor == len(text), "text left over after the last section"


def test_the_order_table_names_every_section_function_once_or_deliberately_twice() -> None:
    """The workspace group is emitted twice on purpose (the original pasted it
    twice); every other section function appears exactly once."""
    plain, padded = _sections()
    section_functions = {
        name: fn
        for name, fn in vars(theme_mod).items()
        if callable(fn) and name.endswith("_rules") and name.startswith("_") and getattr(fn, "__module__", "") == theme_mod.__name__
    }
    listed = [fn for fn in (*plain, *padded)]
    repeated = set(theme_mod._REPEATED_WORKSPACE_SECTIONS)
    for name, fn in section_functions.items():
        expected = 2 if fn in repeated else 1
        assert listed.count(fn) == expected, f"{name} is listed {listed.count(fn)} times, expected {expected}"
    assert set(listed) == set(section_functions.values()), "the order table lists a function that is not a section"


@pytest.mark.parametrize("mode", _ALL_THEME_MODES, ids=lambda mode: mode.value)
def test_every_app_section_returns_text(app: QApplication, mode: AppearanceMode) -> None:
    theme = resolve_theme(mode, app)
    plain, padded = _sections()
    for section in (*plain, *padded):
        text = section(theme)
        if section is theme_mod._backdrop_rules and theme.backdrop_glow_primary is None:
            assert text == "", "themes without a backdrop glow add no backdrop rules"
            continue
        assert text.strip(), f"{section.__name__} returned no text for {mode.value}"
        assert "{" in text and "}" in text, f"{section.__name__} contains no CSS block"


def test_every_studio_section_returns_text() -> None:
    sizes = studio_style._StudioSizes(1191)
    for section in studio_style._FIXED_SECTIONS:
        assert section().strip(), section.__name__
    for section in studio_style._SCALED_SECTIONS:
        assert section(sizes).strip(), section.__name__


def test_every_people_section_returns_text(app: QApplication) -> None:
    from image_triage.ui import people_dialog

    colors = people_dialog._people_colors(build_app_palette(resolve_theme(AppearanceMode.SLATE, app)))
    for section in people_dialog._PEOPLE_STYLESHEET_SECTIONS:
        assert section(colors).strip(), section.__name__


_ARTIFACTS = (
    re.compile(r"\{\{|\}\}"),  # an f-string brace escape that was never formatted
    re.compile(r"\{\s*[A-Za-z_]\w*\.[A-Za-z_]"),  # an unformatted ``{theme.token...}`` expression
    re.compile(r"\bNone\b"),
    re.compile(r"<[\w. ]+ object at 0x"),  # an object repr formatted by accident
)


def _assert_well_formed(text: str, label: str) -> None:
    assert text.count("{") == text.count("}"), f"{label}: unbalanced braces"
    depth = 0
    for char in text:
        depth += (char == "{") - (char == "}")
        assert depth in (0, 1), f"{label}: CSS blocks nest or close early"
    for pattern in _ARTIFACTS:
        match = pattern.search(text)
        assert match is None, f"{label}: stray artifact {match.group(0)!r} at offset {match.start()}"


@pytest.mark.parametrize("mode", _ALL_THEME_MODES, ids=lambda mode: mode.value)
def test_app_stylesheet_is_well_formed(app: QApplication, mode: AppearanceMode) -> None:
    _assert_well_formed(_app_sheet(resolve_theme(mode, app)), f"app/{mode.value}")


@pytest.mark.parametrize("height", _STUDIO_HEIGHTS)
def test_studio_stylesheet_is_well_formed(height: int) -> None:
    _assert_well_formed(build_studio_dialog_stylesheet(FullScreenPreview.STUDIO_SCOPE, height), f"studio/h={height}")


def test_people_stylesheet_is_well_formed(app: QApplication) -> None:
    palette = build_app_palette(resolve_theme(AppearanceMode.SLATE, app))
    _assert_well_formed(_people_sheet(palette), "people")


# --- Wiring ------------------------------------------------------------------
class _SizedHolder:
    """Stands in for the preview dialog: the method reads only these two."""

    STUDIO_SCOPE = FullScreenPreview.STUDIO_SCOPE

    def __init__(self, height: int) -> None:
        self._height = height

    def height(self) -> int:
        return self._height


@pytest.mark.parametrize("height", [-5, 0, 1, 720, 1191])
def test_preview_method_delegates_with_the_clamped_height(height: int) -> None:
    got = FullScreenPreview._studio_stylesheet_full(_SizedHolder(height))  # type: ignore[arg-type]
    assert got == build_studio_dialog_stylesheet(FullScreenPreview.STUDIO_SCOPE, max(1, height))


def test_studio_type_sizes_follow_the_height() -> None:
    small = build_studio_dialog_stylesheet(FullScreenPreview.STUDIO_SCOPE, 400)
    large = build_studio_dialog_stylesheet(FullScreenPreview.STUDIO_SCOPE, 2000)
    assert small != large
    assert build_studio_dialog_stylesheet(FullScreenPreview.STUDIO_SCOPE, 0) == build_studio_dialog_stylesheet(
        FullScreenPreview.STUDIO_SCOPE, 1
    )
