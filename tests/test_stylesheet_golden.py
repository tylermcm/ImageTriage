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
    "app|slate|gamma=1.0": ("3d1c36f4328d95a404db66994ca3574cbfff3820602744584f296ca9e8880238", 101874),
    "app|indigo|gamma=1.0": ("90ed7a2d774bab51cd659069ed1fe3fbdcbfa6ccdff777c16f9cffb50fe519db", 102421),
    "app|dark|gamma=1.0": ("ffa9befcb279ccd2aa61f219fb400cd9bd121d0d192be9934db40a0034179454", 101957),
    "app|midnight|gamma=1.0": ("8904bccf5e4097f9d31f7ae866d13cba51a68157f02ff2275a9d2c8344f5e21a", 102045),
    "app|graphite|gamma=1.0": ("20807bf4a958fc699a82ef5bd84b32f339bbd754adf5a539984e382861fc4e0f", 102157),
    "app|forest|gamma=1.0": ("135dbce776083985e5bea6c12cb2704a04d2c6182126d1b70af89b65043b4588", 102003),
    "app|high_contrast|gamma=1.0": ("a6a1f549bfcca503cac32e4610bec55ccac887c5ae6b76ef36e464d143576dbd", 102027),
    "app|warm_neutral|gamma=1.0": ("9328949fd1b75827e51b3d5b8ec9d610a747a012f1a2ad06036c3344f894c57e", 102373),
    "app|light|gamma=1.0": ("14a8987bf7ccbf048ab48fc4db74316038898f67d4749f3220129de5af5f7240", 102422),
    "app|slate|gamma=0.7": ("e4470bbefe430fbffbd024b7c829b11128a9749e95ead9efbdd5fcbf3dc21e6a", 101574),
    "app|slate|gamma=1.4": ("4afef2b5423bbd5f6542a0763b4aee6e6b0dde3986c7a65aa0ffaed33ecdaf66", 102018),
    "app|indigo|gamma=1.25": ("62b275f5d652d7f3189a2f9499efe1b25cf9e553311aa17aa654b550006ce525", 102481),
    "app|dark|gamma=0.6": ("a6479b77368ce19c30799a3e19fa3589ffd09de27ca18bca36ed1df605981f64", 100872),
    "app|light|gamma=0.8": ("8a4b6693caab56957a2c88a6b386718c5695bb054f3077a520b6600151bb3374", 102180),
    "app|high_contrast|gamma=1.6": ("be5a5b2604ec766a917f0ded6684792512d5e4760fb661b1d8c49c5509a272f7", 102314),
    "app|warm_neutral|gamma=1.15": ("fa512e67b9e9bcead4a08ea1fceef44580b382b2f75030c9f3feda4382433d68", 102373),
    "app|indigo|no-secondary-glow": ("3fbbe1fc55eb8cc7e07f59b91516483725eaa3c5a78a7db34c191d4c52c45738", 102421),
    "app|slate|primary-glow-only": ("9e265966731aa7b02a5f9e119bb9cea9b4023e3c9a58a0869121e5b17d9f41ae", 102253),
    "app|slate|sentinel-metrics": ("123927e6995094d6ab6d40624239e2763f485bdca23846fde0122b390e431d02", 101890),
    "app|slate|sentinel-font-stack": ("e7b60da474e1bc90889a70dba792db31095d1fa2b3e457fde2eecd2aeb2e5877", 101734),
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
