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




# --- Readable facts (independent of the digest) --------------------------------


def test_the_subcommand_is_required_and_stored_in_command(parser) -> None:
    (subparsers_action,) = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    assert subparsers_action.dest == "command"
    assert subparsers_action.required is True








def test_global_and_mask_share_a_destination(parser) -> None:
    """``op-set --global`` writes ``mask='__global__'``; whichever of ``--global`` /
    ``--mask`` comes last wins (they have no mutual exclusion)."""
    assert parser.parse_args(["op-set", "s.json", "op", "--global"]).mask == "__global__"
    assert parser.parse_args(["op-set", "s.json", "op", "--global", "--mask", "m2"]).mask == "m2"
    assert parser.parse_args(["op-set", "s.json", "op", "--mask", "m2", "--global"]).mask == "__global__"








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
