"""Legacy `photoedit` sessions and recipes read from disk are checked before use, every session write goes through a
temporary file, and an override given with its neutral value still overrides."""
from __future__ import annotations

import json
import logging

import pytest

from image_triage import edit_recipe_session
from image_triage.photo_terminal import io as terminal_io, session as terminal_session
from image_triage.photo_terminal.adjustments import EditRecipe


def session_with(**changes):
    session = {"version": terminal_session.SCHEMA_VERSION, "schema": terminal_session.SCHEMA_NAME, "operations": [], "masks": []}
    session.update(changes)
    return session


def write(tmp_path, content):
    target = tmp_path / "photo.edit.json"
    target.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
    return target


# --- E25: validation on load -------------------------------------------------------------------------------------------

@pytest.mark.parametrize("content, named", [
    ("[1, 2]", "JSON object"),
    ("not json", "not a readable session"),
    ('{"version": 1, "operations": {"a": 1}}', "operations must be a list"),
    ('{"version": 1, "operations": [3]}', "operations[0] must be an object"),
    ('{"version": 1, "operations": [{"type": "adjust.exposure", "params": [1]}]}', "operations[0].params"),
    ('{"version": 1, "operations": [{"type": 7}]}', "operations[0].type"),
    ('{"version": 1, "operations": [{"type": "adjust.exposure", "enabled": "yes"}]}', "operations[0].enabled"),
    ('{"version": 1, "masks": "none"}', "masks must be a list"),
    ('{"version": 1, "source": []}', "source must be an object"),
    ('{"version": 1, "operations": [{"type": "adjust.exposure", "params": {"ev": NaN}}]}', "NaN"),
    ('{"version": 1, "operations": [{"type": "adjust.exposure", "params": {"ev": Infinity}}]}', "Infinity"),
    ('{"version": 1, "operations": [{"type": "adjust.exposure", "params": {"ev": 1e999}}]}', "finite"),
])
def test_a_session_file_that_is_not_shaped_like_a_session_is_refused_by_name(tmp_path, content, named):
    with pytest.raises(terminal_session.ValidationError) as refused:
        terminal_session.load_session(write(tmp_path, content))
    assert named in str(refused.value)


def test_session_sizes_are_bounded(tmp_path, monkeypatch):
    many = session_with(operations=[{"id": f"op-{i}", "type": "adjust.exposure", "params": {}} for i in range(terminal_session.SESSION_MAX_OPERATIONS + 1)])
    with pytest.raises(terminal_session.ValidationError, match="at most"):
        terminal_session.load_session(write(tmp_path, many))
    deep = session_with(operations=[{"type": "x", "params": {"a": [[[[[[[[[[[[1]]]]]]]]]]]]}}])
    with pytest.raises(terminal_session.ValidationError, match="nested too deeply"):
        terminal_session.load_session(write(tmp_path, deep))
    monkeypatch.setattr(terminal_session, "SESSION_MAX_BYTES", 10)
    with pytest.raises(terminal_session.ValidationError, match="bytes"):
        terminal_session.load_session(write(tmp_path, session_with()))


@pytest.mark.parametrize("version", [2, "1", 1.0, True, None])
def test_another_version_is_still_reported_as_unsupported(tmp_path, version):
    with pytest.raises(terminal_session.UnsupportedVersionError):
        terminal_session.load_session(write(tmp_path, session_with(version=version)))


def test_entries_this_version_does_not_know_are_kept_and_logged_not_fatal(tmp_path, caplog):
    terminal_session._WARNED_UNKNOWN.clear()
    target = write(tmp_path, session_with(futureThing={"a": 1}))
    with caplog.at_level(logging.WARNING):
        session = terminal_session.load_session(target)
    assert session["futureThing"] == {"a": 1} and "futureThing" in caplog.text
    terminal_session.save_session(target, session)
    assert terminal_session.load_session(target)["futureThing"] == {"a": 1}


def test_a_session_with_a_number_that_cannot_be_read_back_is_not_written(tmp_path):
    target = write(tmp_path, session_with())
    before = target.read_bytes()
    with pytest.raises(terminal_session.ValidationError):
        terminal_session.save_session(target, session_with(operations=[{"type": "adjust.exposure", "params": {"ev": float("nan")}}]))
    assert target.read_bytes() == before and [p.name for p in tmp_path.iterdir()] == [target.name]


@pytest.mark.parametrize("data, named", [
    ({"exposure": "bright"}, "'exposure'"), ({"contrast": float("inf")}, "'contrast'"), ({"flip_h": 1}, "'flip_h'"),
    ({"crop": [1, 2]}, "'crop'"), ({"retouch": [{}] * 5001}, "'retouch'"), ({"point_colors": [{"h": float("nan")}]}, "'point_colors'"),
    ({"curve_rgb": [[0, 0]] * 300}, "'curve_rgb'"), ({"crop_aspect": "x" * 300}, "'crop_aspect'"),
    ({"retouch": [[[[[[1]]]]]]}, "'retouch'"),
])
def test_from_dict_refuses_a_wrong_value_by_name(data, named):
    with pytest.raises(ValueError) as refused:
        EditRecipe.from_dict(data)
    assert named in str(refused.value)


def test_from_dict_ignores_unknown_settings_with_a_warning_and_brings_numbers_into_range(caplog):
    with caplog.at_level(logging.WARNING):
        recipe = EditRecipe.from_dict({"contrast": 250, "exposure": -40, "vignette_midpoint": -5, "saturation": 30, "someday": 1})
    assert (recipe.contrast, recipe.exposure, recipe.vignette_midpoint, recipe.saturation) == (100, -5, 0, 30)
    assert "someday" in caplog.text and "contrast" in caplog.text


@pytest.mark.parametrize("op, named", [
    ({"id": "op-1", "type": "adjust.exposure", "params": {"exposure": "lots"}}, "'exposure'"),
    ({"id": "crop-1", "type": "transform.crop", "params": {"left": "a", "right": 10, "top": 0, "bottom": 10}}, "crop-1"),
    ({"id": "heal-1", "type": "retouch.heal", "params": {"x": None, "y": [1], "radius": 4}}, "heal-1"),
])
def test_a_session_operation_with_a_wrong_value_is_refused_by_name_when_it_becomes_a_recipe(op, named):
    with pytest.raises(ValueError) as refused:
        edit_recipe_session.recipe_from_session(session_with(operations=[op]))
    assert named in str(refused.value)


def test_a_good_session_still_becomes_its_recipe():
    session = session_with(operations=[
        {"id": "a", "type": "adjust.exposure", "enabled": True, "maskId": None, "params": {"exposure": 0.5}},
        {"id": "b", "type": "transform.crop", "enabled": True, "maskId": None, "params": {"left": 1, "top": 2, "right": 30, "bottom": 40}},
    ])
    recipe = edit_recipe_session.recipe_from_session(session)
    assert recipe.exposure == 0.5 and recipe.crop == (1, 2, 30, 40)


# --- E24: every session write is atomic ---------------------------------------------------------------------------------

@pytest.fixture
def replaced(monkeypatch):
    moved = []
    for module in (terminal_io, terminal_session):
        replace = module.os.replace
        monkeypatch.setattr(module.os, "replace", lambda source, destination, _replace=replace: (moved.append(str(destination)), _replace(source, destination))[1])
    return moved


def test_the_xmp_export_is_written_through_a_temporary_file(tmp_path, replaced):
    target = write(tmp_path, session_with(source={"contentHash": {"value": "x"}}, pipeline={"ordering": terminal_session.PIPELINE_ORDERING}))
    out = terminal_session.export_xmp(target)
    assert str(out) in replaced and out.read_text(encoding="utf-8").startswith("<?xpacket")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photo.edit.json", "photo.xmp"]


def test_a_mask_copied_into_a_session_never_leaves_a_partial_file(tmp_path, replaced, monkeypatch):
    target = write(tmp_path, session_with(coordinateSpaces=[{"id": "space-source-full"}]))
    mask = tmp_path / "mask.png"
    mask.write_bytes(b"first mask")
    session = terminal_session.load_session(target)
    asset = terminal_session.copy_bitmap_asset(target, session, "mask-1", "space-source-full", mask)
    stored = tmp_path / asset["path"]
    assert stored.read_bytes() == b"first mask" and str(stored) in replaced

    def broken(source, destination, *args, **kwargs):
        destination.write(b"par")
        raise OSError("disk full")

    monkeypatch.setattr(terminal_session.shutil, "copyfileobj", broken)
    mask.write_bytes(b"second mask")
    with pytest.raises(terminal_session.WriteError):
        terminal_session.copy_bitmap_asset(target, session, "mask-1", "space-source-full", mask)
    assert stored.read_bytes() == b"first mask" and [p.name for p in stored.parent.iterdir()] == [stored.name]


# --- E26: "not given" is not "given as the neutral value" -----------------------------------------------------------------

def test_an_override_given_as_zero_replaces_a_stored_value():
    stored = EditRecipe(contrast=40, exposure=0.5, vignette_midpoint=20)
    explicit = stored.merged(EditRecipe(contrast=0, vignette_midpoint=50), given={"contrast", "vignette_midpoint"})
    assert (explicit.contrast, explicit.exposure, explicit.vignette_midpoint) == (0, 0.5, 50)
    assert stored.merged(EditRecipe(), given=()) == stored                      # nothing given: nothing changes
    assert stored.merged(EditRecipe(contrast=0)) == stored                      # without `given` a default is still "not given"
    assert stored.merged(EditRecipe(contrast=10)).contrast == 10
    with pytest.raises(ValueError):
        stored.merged(EditRecipe(), given={"no_such_setting"})
