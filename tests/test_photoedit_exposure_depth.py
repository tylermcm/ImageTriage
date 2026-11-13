"""`photoedit` exposure is in stops (new edits) while saved edits keep the curve they were made with, and a 16-bit PNG or
TIFF stays 16-bit from input to output, with its colour profile and EXIF."""
from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image, ImageCms, ImageOps

from image_triage import edit_recipe_session
from image_triage.photo_terminal import cli, io as terminal_io, session as terminal_session
from image_triage.photo_terminal.adjustments import (
    EXPOSURE_MODEL_LEGACY, EXPOSURE_MODEL_STOPS, EditRecipe, apply_exposure, exposure_stops_array, linear_to_srgb, srgb_to_linear,
)

QtGui = pytest.importorskip("PySide6.QtGui")

MID_GREY = 118                      # 18% reflectance, sRGB-encoded, 8-bit


def one_stop(value: float, stops: float = 1.0) -> float:
    """Where an sRGB-encoded value (0..1) lands when the light it stands for is doubled per stop."""
    return float(linear_to_srgb(np.float32(srgb_to_linear(np.float32(value)) * 2.0 ** stops)))


def grey(tmp_path, value=MID_GREY, name="grey.png"):
    path = tmp_path / name
    Image.new("RGB", (16, 12), (value, value, value)).save(path)
    return path


def render(*argv) -> None:
    assert cli.main(["render", *map(str, argv)]) == 0


# --- exposure --------------------------------------------------------------------------------------------------------------

def test_one_stop_on_the_command_line_doubles_the_light_of_a_mid_grey_patch(tmp_path):
    out = tmp_path / "out.png"
    render(grey(tmp_path), out, "--exposure", "1")
    expected = one_stop(MID_GREY / 255.0) * 255.0                 # 118 -> about 162
    assert 160 < expected < 164
    assert np.abs(np.asarray(Image.open(out)).astype(float) - expected).max() <= 0.51
    # What this used to do: the first curve puts the same patch well above one stop (about 186, close to +1.5 stops).
    assert np.asarray(apply_exposure(Image.open(grey(tmp_path)), 1.0))[0, 0, 0] > expected + 15


@pytest.mark.parametrize("stops", [-2.0, -1.0, 0.5, 1.0])
def test_each_stop_halves_or_doubles_linear_light_below_the_highlight_shoulder(tmp_path, stops):
    out = tmp_path / "out.png"
    render(grey(tmp_path, 60), out, f"--exposure={stops}")
    got = np.asarray(Image.open(out))[0, 0, 0] / 255.0
    assert float(srgb_to_linear(np.float32(got))) == pytest.approx(float(srgb_to_linear(np.float32(60 / 255.0))) * 2.0 ** stops, rel=0.03)


def test_raising_the_exposure_rolls_off_into_white_instead_of_clipping():
    ramp = np.linspace(0.0, 1.0, 512, dtype=np.float32).reshape(1, -1, 1).repeat(3, axis=2)
    out = exposure_stops_array(ramp, 2.0)[0, :, 0]
    assert np.all(np.diff(out) >= 0) and out[-1] < 1.0 and len(np.unique(out[out > 0.9])) > 50   # still separated, never flat
    colour = exposure_stops_array(np.asarray([[[0.2, 0.4, 0.1]]], dtype=np.float32), 1.0)[0, 0]
    before = srgb_to_linear(np.asarray([0.2, 0.4, 0.1], dtype=np.float32))
    assert np.allclose(srgb_to_linear(colour) / before, 2.0, rtol=0.01)                          # the same factor on every channel


def test_a_recipe_saved_before_the_marker_keeps_its_look(tmp_path):
    source = grey(tmp_path)
    old = tmp_path / "old.json"
    old.write_text(json.dumps({"exposure": 1.0, "contrast": 10}), encoding="utf-8")
    loaded = EditRecipe.load(old)
    assert loaded.exposure_model == EXPOSURE_MODEL_LEGACY
    as_before = tmp_path / "before.png"
    render(source, as_before, "--recipe", old)
    assert np.array_equal(np.asarray(Image.open(as_before)), np.asarray(EditRecipe(exposure=1.0, contrast=10).apply(Image.open(source))))
    # An exposure given on the command line replaces it, in stops.
    replaced = tmp_path / "replaced.png"
    render(source, replaced, "--recipe", old, "--exposure", "1", "--contrast", "0")
    assert abs(int(np.asarray(Image.open(replaced))[0, 0, 0]) - round(one_stop(MID_GREY / 255.0) * 255.0)) <= 1


def test_a_recipe_written_now_says_its_exposure_is_in_stops(tmp_path):
    out = tmp_path / "look.json"
    assert cli.main(["recipe", str(out), "--exposure", "1"]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["exposure_model"] == EXPOSURE_MODEL_STOPS
    assert EditRecipe.load(out).exposure_model == EXPOSURE_MODEL_STOPS
    with pytest.raises(ValueError, match="'exposure_model'"):
        EditRecipe.from_dict({"exposure": 1, "exposure_model": 3})


def test_session_exposure_operations_carry_the_model_and_old_ones_keep_theirs(tmp_path):
    image = grey(tmp_path)
    path, session = terminal_session.new_session(image)
    session["operations"].append({"id": "old-1", "type": "adjust.exposure", "enabled": True, "maskId": None, "params": {"exposure": 0.5}})
    terminal_session.save_session(path, session)
    assert edit_recipe_session.recipe_from_session(terminal_session.load_session(path)).exposure_model == EXPOSURE_MODEL_LEGACY

    assert cli.main(["op-set", str(path), "old-1", "--param", "exposure=1"]) == 0          # a new value is given in stops
    stored = terminal_session.load_session(path)
    assert stored["operations"][0]["params"] == {"exposure": 1, "model": EXPOSURE_MODEL_STOPS}
    terminal_session.validate_session(stored)
    recipe = edit_recipe_session.recipe_from_session(stored)
    assert (recipe.exposure, recipe.exposure_model) == (1, EXPOSURE_MODEL_STOPS)

    other, _session = terminal_session.new_session(image, tmp_path / "other.edit.json")
    assert cli.main(["op-add", str(other), "adjust.exposure", "--param", "exposure=0.35", "--last"]) == 0
    assert terminal_session.load_session(other)["operations"][0]["params"]["model"] == EXPOSURE_MODEL_STOPS

    # Written back from a recipe: with the exposure, and never as an operation of its own.
    ops = edit_recipe_session.operations_from_recipe(recipe)
    assert [op["params"] for op in ops if op["type"] == "adjust.exposure"] == [{"exposure": 1, "model": EXPOSURE_MODEL_STOPS}]
    assert edit_recipe_session.operations_from_recipe(EditRecipe(exposure_model=EXPOSURE_MODEL_STOPS)) == []
    stored["operations"][0]["params"]["model"] = 5
    with pytest.raises(terminal_session.ValidationError, match="model"):
        terminal_session.validate_session(stored)


# --- 16 bits per channel ---------------------------------------------------------------------------------------------------

def gradient16(height=24, width=400) -> np.ndarray:
    """Float RGB with far more than 256 levels across: a slow ramp, different per channel."""
    x = np.linspace(0.05, 0.45, width, dtype=np.float32)
    row = np.stack([x, x * 0.9 + 0.02, x * 0.8 + 0.05], axis=-1)
    return np.repeat(row[None], height, axis=0)


def qt_write(path, pixels: np.ndarray) -> None:
    """A 16-bit file written by Qt, not by the code under test."""
    values = np.rint(pixels * 65535.0).astype(np.uint16)
    height, width = values.shape[:2]
    padded = np.full((height, width, 4), 65535, dtype=np.uint16)
    padded[..., :3] = values
    image = QtGui.QImage(padded.tobytes(), width, height, width * 8, QtGui.QImage.Format.Format_RGBX64).copy()
    assert image.save(str(path))


def qt_read(path) -> np.ndarray:
    image = QtGui.QImage(str(path))
    assert not image.isNull() and image.depth() == 64, "the file does not hold 16 bits per channel"
    image = image.convertToFormat(QtGui.QImage.Format.Format_RGBX64)
    height, width = image.height(), image.width()
    rows = np.frombuffer(image.constBits(), dtype=np.uint16).reshape(height, image.bytesPerLine() // 2)
    return rows[:, : width * 4].reshape(height, width, 4)[..., :3].astype(np.float32) / 65535.0


@pytest.mark.parametrize("source, output", [("in.png", "out.png"), ("in.tif", "out.tif"), ("in.png", "out.tiff"), ("in.tif", "out.png")])
def test_a_16_bit_picture_is_edited_and_written_at_16_bits(tmp_path, source, output):
    pixels = gradient16()
    qt_write(tmp_path / source, pixels)
    assert terminal_io.bits_per_channel(tmp_path / source) == 16
    render(tmp_path / source, tmp_path / output, "--exposure", "1")
    assert terminal_io.bits_per_channel(tmp_path / output) == 16
    got = qt_read(tmp_path / output)
    quantised = np.rint(pixels * 65535.0) / 65535.0
    assert np.abs(got - exposure_stops_array(quantised.astype(np.float32), 1.0)).max() < 2.5 / 65535.0
    assert len(np.unique(got[0, :, 0])) > 300                    # an 8-bit pipeline could give at most 256 levels


def test_an_unedited_16_bit_picture_comes_out_exactly_as_it_went_in(tmp_path):
    pixels = gradient16()
    qt_write(tmp_path / "in.png", pixels)
    render(tmp_path / "in.png", tmp_path / "out.tif")
    assert np.array_equal(qt_read(tmp_path / "out.tif"), qt_read(tmp_path / "in.png"))


def test_steps_that_only_exist_at_8_bits_do_not_flatten_a_16_bit_picture(tmp_path):
    pixels = gradient16()
    qt_write(tmp_path / "in.tif", pixels)
    render(tmp_path / "in.tif", tmp_path / "out.tif", "--vignette", "-40", "--sharpen", "30", "--contrast", "15")
    got = qt_read(tmp_path / "out.tif")
    assert len(np.unique(got[got.shape[0] // 2, :, 0])) > 300
    eight = np.asarray(EditRecipe(vignette=-40, sharpen=30, contrast=15).apply(Image.fromarray(np.rint(pixels * 255).astype(np.uint8), "RGB")))
    assert np.abs(got - eight / 255.0).max() < 3.0 / 255.0        # the same picture as the 8-bit render, to within rounding


@pytest.mark.parametrize("suffix", [".png", ".tif"])
def test_the_16_bit_output_keeps_the_colour_profile_and_exif_and_is_turned_once(tmp_path, suffix):
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    exif = Image.Exif()
    exif[0x010F] = "Synthetic Maker"
    exif[0x0112] = 6                                             # the camera was held on its side
    exif.get_ifd(0x8769)[0x9003] = "2026:01:02 03:04:05"
    pixels = gradient16(height=10, width=30)
    source = tmp_path / f"in{suffix}"
    terminal_io.save_high_depth(pixels, source, icc_profile=profile, exif=exif)
    assert np.array_equal(qt_read(source), np.rint(pixels * 65535.0).astype(np.float32) / 65535.0)   # the writer itself, read by Qt
    with Image.open(source) as written:
        assert written.getexif()[0x0112] == 6 and written.info["icc_profile"] == profile

    out = tmp_path / f"out{suffix}"
    render(source, out)
    got = qt_read(out)
    assert got.shape[:2] == (30, 10)                             # upright: turned once, by the edit
    assert np.array_equal(got, np.rot90(qt_read(source), k=-1))
    with Image.open(out) as result:
        tags = result.getexif()
        assert tags[0x010F] == "Synthetic Maker" and 0x0112 not in tags
        assert tags.get_ifd(0x8769)[0x9003] == "2026:01:02 03:04:05"
        assert result.info["icc_profile"] == profile


@pytest.mark.parametrize("orientation", range(1, 9))
def test_the_array_is_turned_exactly_as_pillow_turns_a_picture(orientation):
    rng = np.random.default_rng(orientation)
    pixels = rng.integers(0, 255, size=(5, 7, 3), dtype=np.uint8)
    picture = Image.fromarray(pixels, "RGB")
    exif = picture.getexif()
    exif[0x0112] = orientation
    picture.info["exif"] = exif.tobytes()
    expected = np.asarray(ImageOps.exif_transpose(picture))
    assert np.array_equal(terminal_io._upright(pixels, orientation), expected)


def test_a_16_bit_picture_into_a_format_without_16_bits_and_an_8_bit_picture_take_the_8_bit_path(tmp_path):
    qt_write(tmp_path / "in.png", gradient16())
    render(tmp_path / "in.png", tmp_path / "out.jpg", "--exposure", "1")
    assert Image.open(tmp_path / "out.jpg").mode == "RGB"
    render(grey(tmp_path), tmp_path / "eight.png", "--exposure", "1")
    assert terminal_io.bits_per_channel(tmp_path / "eight.png") == 8
    assert terminal_io.open_high_depth(grey(tmp_path)) is None


def test_a_spot_edit_keeps_16_bits(tmp_path):
    qt_write(tmp_path / "in.png", gradient16())
    assert cli.main(["spot", str(tmp_path / "in.png"), str(tmp_path / "out.png"), "--x", "50", "--y", "10", "--radius", "6"]) == 0
    got = qt_read(tmp_path / "out.png")
    assert len(np.unique(got[0, :, 0])) > 300
