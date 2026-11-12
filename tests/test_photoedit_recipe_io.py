"""The ``photoedit`` command line: recipe overrides, recipe and session files, and what a render keeps from the source."""
from __future__ import annotations

import json

import pytest
from PIL import Image, ImageCms

from image_triage.photo_terminal import cli, io as terminal_io, session as terminal_session
from image_triage.photo_terminal.adjustments import EditRecipe


def _args(*argv: str):
    return cli.build_parser().parse_args(list(argv))


def test_an_option_given_with_its_neutral_value_overrides_the_recipe(tmp_path):
    recipe = tmp_path / "look.json"
    EditRecipe(contrast=40, exposure=0.5, vignette_midpoint=20).save(recipe)
    out = str(tmp_path / "out.json")
    untouched = cli.recipe_from_args(_args("recipe", out, "--recipe", str(recipe)))
    assert (untouched.contrast, untouched.exposure, untouched.vignette_midpoint) == (40, 0.5, 20)
    changed = cli.recipe_from_args(_args("recipe", out, "--recipe", str(recipe), "--contrast", "0", "--vignette-midpoint", "50"))
    assert (changed.contrast, changed.exposure, changed.vignette_midpoint) == (0, 0.5, 50)


def test_without_a_recipe_the_options_are_the_recipe(tmp_path):
    made = cli.recipe_from_args(_args("recipe", str(tmp_path / "out.json"), "--exposure", "1", "--crop", "1", "2", "3", "4"))
    assert made == EditRecipe(exposure=1.0, crop=(1, 2, 3, 4))


@pytest.mark.parametrize("content, named", [
    ("[1, 2]", "JSON object"), ('{"exposure": "bright"}', "'exposure'"), ('{"contrast": true}', "'contrast'"),
    ('{"flip_h": 1}', "'flip_h'"), ('{"crop": [1, 2, 3]}', "'crop'"), ('{"retouch": "x"}', "'retouch'"),
    ('{"background_mode": 3}', "'background_mode'"), ('{"exposure": NaN}', "'exposure'"),
])
def test_a_recipe_file_with_a_wrong_value_is_refused_by_name(tmp_path, content, named):
    recipe = tmp_path / "look.json"
    recipe.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=named):
        EditRecipe.load(recipe)


def test_a_saved_recipe_loads_back_and_unknown_settings_are_ignored(tmp_path):
    recipe = tmp_path / "look.json"
    original = EditRecipe(exposure=0.5, crop=(0, 0, 10, 10), flip_h=True, curve_rgb=[[0, 0], [128, 140], [255, 255]])
    original.save(recipe)
    assert EditRecipe.load(recipe).exposure == 0.5 and EditRecipe.load(recipe).crop == (0, 0, 10, 10)
    data = json.loads(recipe.read_text(encoding="utf-8"))
    data["from_a_later_version"] = {"anything": 1}
    recipe.write_text(json.dumps(data), encoding="utf-8")
    assert EditRecipe.load(recipe).flip_h is True


def test_a_failed_write_leaves_the_previous_file_whole(tmp_path, monkeypatch):
    target = tmp_path / "photo.edit.json"
    terminal_io.write_text_atomic(target, "first\n")

    def broken(source, destination):
        raise OSError("disk full")

    monkeypatch.setattr(terminal_io.os, "replace", broken)
    with pytest.raises(terminal_session.WriteError):
        terminal_session.save_session(target, {"version": 1})
    with pytest.raises(OSError):
        EditRecipe().save(target)
    assert target.read_text(encoding="utf-8") == "first\n"
    assert [p.name for p in tmp_path.iterdir()] == ["photo.edit.json"]      # no temporary file is left either


def test_a_session_is_written_through_a_temporary_file(tmp_path, monkeypatch):
    target = tmp_path / "photo.edit.json"
    moved = []
    replace = terminal_io.os.replace
    monkeypatch.setattr(terminal_io.os, "replace", lambda source, destination: (moved.append(str(destination)), replace(source, destination))[1])
    terminal_session.save_session(target, {"version": terminal_session.SCHEMA_VERSION, "operations": []})
    assert moved == [str(target)] and terminal_session.load_session(target)["operations"] == []


def test_a_render_keeps_the_colour_profile_and_exif_and_does_not_turn_the_picture_twice(tmp_path):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    source, output = tmp_path / "in.jpg", tmp_path / "out.jpg"
    image = Image.new("RGB", (40, 20), (120, 90, 60))
    exif = Image.Exif()
    exif[0x010F] = "Synthetic Camera"       # Make
    exif[0x0112] = 6                        # Orientation: turned a quarter
    image.save(source, icc_profile=profile, exif=exif)
    assert cli.main(["render", str(source), str(output), "--contrast", "5"]) == 0
    with Image.open(output) as rendered:
        assert rendered.info.get("icc_profile") == profile
        kept = rendered.getexif()
        assert kept.get(0x010F) == "Synthetic Camera"
        assert kept.get(0x0112) in (None, 1)                 # the pixels were turned, so the tag must not turn them again
        assert rendered.size == (20, 40)
