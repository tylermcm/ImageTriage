"""Characterization test for the ``photoedit`` argparse tree (audit finding A2, stage 2).

``build_parser`` (``image_triage/photo_terminal/cli.py``) used to be a single
~240-line function; it is now a short function that registers one subcommand
per ``_add_<name>_parser`` helper, in the same order. The CLI is a published
interface (``[project.scripts] photoedit``), so this module pins two things as
SHA-256 digests of plain text:

* the parser *structure*: the top-level parser and every subparser, with each
  argument's option strings, dest, type, default, nargs, const, choices,
  required flag, help text and metavar, plus ``set_defaults`` (which handler a
  subcommand dispatches to and fixed values such as ``refine_type``); and
* the ``parse_args`` result for a representative argv per subcommand - the
  minimal required arguments (so every default shows) and one with every option
  given a non-default value.

Rejected command lines (bad choices, missing required options, wrong arity,
unknown subcommand) are asserted separately: each must exit with status 2 and
name the offending argument. The structure is read from argparse's own objects
rather than from ``--help`` text, which differs between Python versions.

If a CLI change is intentional, regenerate the digest table:

    # PowerShell
    $env:UPDATE_BUILDER_SNAPSHOTS = "1"
    pythonw3.13.exe scripts/run313.py regen.log pytest -q -s tests/test_photoedit_cli_snapshot.py
    Remove-Item Env:UPDATE_BUILDER_SNAPSHOTS

then paste the printed table (also written to
``<temp dir>/image_triage_test_photoedit_cli_snapshot.txt``) over ``_GOLDEN``
below, extend ``CLI_PARSE_CASES`` in ``tests/builder_snapshot_support.py`` for any
new option, update the readable expectations the change touched, and review the diff.
"""
from __future__ import annotations

import argparse
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from image_triage.photo_terminal import cli
from tests import builder_snapshot_support as support

# case id -> (sha256 of the text, its length in characters)
_GOLDEN: dict[str, tuple[str, int]] = {
    "structure": ("667fdea7da45c0046f377fc7b1481e961373e75d1517b6a6dc4792bd97b0dcd0", 55154),
    "parse": ("8b4175bd81b73c166a5dbaae7dfd19c7be45910e3d84d2b45a1753213a2253df", 9282),
}

_SUBCOMMANDS = [
    "inspect", "preview", "recipe", "render", "spot",
    "session-new", "session-info", "validate", "migrate",
    "op-add", "op-set", "op-move", "op-delete", "wb-kelvin", "levels", "point-curve", "crop-preset",
    "space-add", "mask-radial", "mask-gradient", "mask-painted-add", "mask-subject",
    "mask-refine-luma", "mask-refine-color", "mask-bounds", "mask-delete",
    "relink", "export-xmp",
]

# subcommand -> handler it dispatches to
_HANDLERS = {
    "inspect": "command_inspect",
    "preview": "command_preview",
    "recipe": "command_recipe",
    "render": "command_render",
    "spot": "command_spot",
    "session-new": "command_session_new",
    "session-info": "command_session_info",
    "validate": "command_validate",
    "migrate": "command_migrate",
    "op-add": "command_op_add",
    "op-set": "command_op_set",
    "op-move": "command_op_move",
    "op-delete": "command_op_delete",
    "wb-kelvin": "command_wb_kelvin",
    "levels": "command_levels",
    "point-curve": "command_point_curve",
    "crop-preset": "command_crop_preset",
    "space-add": "command_space_add",
    "mask-radial": "command_mask_radial",
    "mask-gradient": "command_mask_gradient",
    "mask-painted-add": "command_mask_painted_add",
    "mask-subject": "command_mask_subject",
    "mask-refine-luma": "command_mask_refine",
    "mask-refine-color": "command_mask_refine",
    "mask-bounds": "command_mask_refine",
    "mask-delete": "command_mask_delete",
    "relink": "command_relink",
    "export-xmp": "command_export_xmp",
}


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return cli.build_parser()


def test_parser_structure_and_parse_results_are_unchanged(parser) -> None:
    dumps = {
        "structure": support.parser_structure_lines(parser),
        "parse": support.cli_parse_lines(parser),
    }
    support.assert_matches_golden(
        module="test_photoedit_cli_snapshot",
        table_name="_GOLDEN",
        golden=_GOLDEN,
        actual={case: support.digest_lines(lines) for case, lines in dumps.items()},
        what="The photoedit argparse tree built by build_parser",
        dumps=dumps,
    )


# --- Readable facts (independent of the digest) --------------------------------
def test_subcommands_and_their_registration_order(parser) -> None:
    assert parser.prog == "photoedit"
    assert support.subcommand_names(parser) == _SUBCOMMANDS


def test_the_subcommand_is_required_and_stored_in_command(parser) -> None:
    (subparsers_action,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert subparsers_action.dest == "command"
    assert subparsers_action.required is True


@pytest.mark.parametrize("name", _SUBCOMMANDS)
def test_each_subcommand_dispatches_to_its_handler(parser, name) -> None:
    argv = dict(support.CLI_PARSE_CASES)[f"{name}|min"]
    namespace = parser.parse_args(argv)
    assert namespace.command == name
    assert namespace.func is getattr(cli, _HANDLERS[name])
    assert callable(namespace.func)


def test_mask_refinements_share_a_handler_and_set_their_type(parser) -> None:
    cases = dict(support.CLI_PARSE_CASES)
    refine_types = {
        name: parser.parse_args(cases[f"{name}|min"]).refine_type
        for name in ("mask-refine-luma", "mask-refine-color", "mask-bounds")
    }
    assert refine_types == {
        "mask-refine-luma": "luminance-range",
        "mask-refine-color": "color-range",
        "mask-bounds": "bounds",
    }


def test_defaults_of_a_few_options(parser) -> None:
    cases = dict(support.CLI_PARSE_CASES)
    preview = parser.parse_args(cases["preview|min"])
    assert (preview.width, preview.height) == (80, None)
    render = parser.parse_args(cases["render|min"])
    assert render.quality == 95 and render.exposure == 0.0 and render.vignette_midpoint == 50.0
    assert render.vignette_feather == 50.0 and render.crop is None and render.recipe is None
    spot = parser.parse_args(cases["spot|min"])
    assert spot.strength == 0.8 and spot.quality == 95
    assert parser.parse_args(cases["migrate|min"]).to == 1
    assert parser.parse_args(cases["relink|min"]).sessions == "*.edit.json"
    assert parser.parse_args(cases["crop-preset|min"]).anchor == "center"
    radial = parser.parse_args(cases["mask-radial|min"])
    assert (radial.angle, radial.feather, radial.density, radial.invert) == (0.0, 65.0, 100.0, False)
    gradient = parser.parse_args(cases["mask-gradient|min"])
    assert (gradient.feather, gradient.density) == (100.0, 100.0)
    assert parser.parse_args(cases["mask-refine-luma|min"]).feather == 20
    color = parser.parse_args(cases["mask-refine-color|min"])
    assert (color.tolerance, color.feather) == (45, 35)


def test_global_and_mask_share_a_destination(parser) -> None:
    """``op-set --global`` writes ``mask='__global__'``; whichever of ``--global`` /
    ``--mask`` comes last wins (they have no mutual exclusion)."""
    assert parser.parse_args(["op-set", "s.json", "op", "--global"]).mask == "__global__"
    assert parser.parse_args(["op-set", "s.json", "op", "--global", "--mask", "m2"]).mask == "m2"
    assert parser.parse_args(["op-set", "s.json", "op", "--mask", "m2", "--global"]).mask == "__global__"


@pytest.mark.parametrize(("label", "argv", "needle"), support.CLI_ERROR_CASES, ids=[case[0] for case in support.CLI_ERROR_CASES])
def test_invalid_command_lines_exit_with_status_2(parser, capsys, label, argv, needle) -> None:
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(argv)
    assert raised.value.code == 2, label
    stderr = capsys.readouterr().err
    assert stderr.startswith("usage: photoedit"), label
    assert needle in stderr, f"{label}: the error does not mention {needle!r}: {stderr!r}"


def test_every_option_of_every_subcommand_is_exercised_by_a_parse_case(parser) -> None:
    """A new option must come with a parse case in ``support.CLI_PARSE_CASES``
    (and a regenerated digest), otherwise it is invisible to this test."""
    exercised: dict[str, set[str]] = {}
    for label, argv in support.CLI_PARSE_CASES:
        exercised.setdefault(argv[0], set()).update(token for token in argv[1:] if token.startswith("--"))
    (subparsers_action,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    for name, sub in subparsers_action.choices.items():
        options = {
            option
            for action in sub._actions
            if not isinstance(action, argparse._HelpAction)
            for option in action.option_strings
        }
        assert options <= exercised[name], f"{name}: options never parsed by a case: {sorted(options - exercised[name])}"


def test_every_subcommand_has_a_minimal_and_a_handler_case(parser) -> None:
    labels = {label for label, _argv in support.CLI_PARSE_CASES}
    assert all(f"{name}|min" in labels or f"{name}|full" in labels for name in _SUBCOMMANDS)
    assert {argv[0] for _label, argv in support.CLI_PARSE_CASES} == set(_SUBCOMMANDS)


def test_top_level_help_lists_every_subcommand(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: photoedit")
    for name in _SUBCOMMANDS:
        assert name in out


def test_every_subcommand_help_exits_cleanly(capsys) -> None:
    for name in _SUBCOMMANDS:
        with pytest.raises(SystemExit) as raised:
            cli.main([name, "--help"])
        assert raised.value.code == 0, name
        assert capsys.readouterr().out.startswith(f"usage: photoedit {name}"), name


def test_help_text_carries_every_subcommand_help_string(parser) -> None:
    """The top-level ``--help`` lists each subcommand with its help string (wrapped
    to the terminal width, so compare with whitespace collapsed)."""
    text = " ".join(" ".join(support.parser_help_lines(parser)).split())
    (subparsers_action,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    for choice in subparsers_action._choices_actions:
        assert " ".join(choice.help.split()) in text, f"{choice.dest}: help string missing from --help"
    assert len(subparsers_action._choices_actions) == len(_SUBCOMMANDS)


def test_building_the_parser_twice_gives_equal_structure() -> None:
    first = support.parser_structure_lines(cli.build_parser())
    second = support.parser_structure_lines(cli.build_parser())
    assert first == second
