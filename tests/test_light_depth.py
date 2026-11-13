"""Light Depth: depth-driven coverage, the mask it manages, and the settings kept beside the recipe."""
import base64

import numpy as np
import pytest

from image_triage import light_depth
from image_triage.camera_raw_recipe_masks import masks_valid
from image_triage.qml_shell.raw_masks import decode_raster, encode_raster


def scene(width=120, height=80):
    """Far at the top (0), near at the bottom (255), like a ground plane."""
    return np.repeat(np.linspace(0, 255, height).astype(np.uint8)[:, None], width, axis=1)


def settings(**changes):
    return {**light_depth.DEFAULTS, "amount": 70, "softness": 0, **changes}


def test_light_falls_at_its_own_distance_and_fades_either_side():
    lit = light_depth.coverage(scene(), settings(depth=0.5, reach=20))
    column = lit[:, lit.shape[1] // 2].astype(int)
    assert column[len(column) // 2] > 240
    assert column[0] < 40 and column[-1] < 40
    far = light_depth.coverage(scene(), settings(depth=0.0, reach=20))[:, 0].astype(int)
    assert far[0] > 240 and far[-1] < 10


def test_reach_carries_the_light_further_through_the_scene():
    narrow = light_depth.coverage(scene(), settings(reach=10)).astype(int).sum()
    wide = light_depth.coverage(scene(), settings(reach=90)).astype(int).sum()
    assert wide > narrow * 2


def test_focus_turns_the_sheet_of_light_into_a_pool_around_the_handle():
    sheet = light_depth.coverage(scene(), settings(depth=0.5, x=0.1, y=0.5))
    row = sheet[sheet.shape[0] // 2].astype(int)
    assert abs(row[2] - row[-3]) <= 2  # no sideways fall-off without focus
    pool = light_depth.coverage(scene(), settings(depth=0.5, x=0.1, y=0.5, focus=100))
    row = pool[pool.shape[0] // 2].astype(int)
    assert row[5] > row[-5] + 100


def test_softness_spills_light_across_a_depth_edge():
    wall = np.zeros((80, 120), dtype=np.uint8)
    wall[:, 60:] = 255  # a near object against a far background
    hard = light_depth.coverage(wall, settings(depth=1.0, reach=10, softness=0)).astype(int)
    soft = light_depth.coverage(wall, settings(depth=1.0, reach=10, softness=100)).astype(int)
    assert hard[40, 52] < 5 and soft[40, 52] > hard[40, 52] + 20
    assert soft[40, 110] > 200


def test_depth_under_a_point_ignores_one_noisy_pixel():
    values = scene()
    values[40, 60] = 0
    assert light_depth.depth_at(values, 0.5, 0.5) == pytest.approx(0.5, abs=0.03)
    assert light_depth.depth_at(values, 0.5, 0.0) < 0.05 and light_depth.depth_at(values, 0.5, 1.0) > 0.95


def test_amount_and_warmth_become_a_local_exposure_and_temperature():
    assert light_depth.adjustment(settings(amount=100)) == {"exposure": light_depth.MAX_EXPOSURE}
    warm = light_depth.adjustment(settings(amount=50, warmth=-100))
    assert warm == {"exposure": light_depth.MAX_EXPOSURE / 2, "temperature": -light_depth.MAX_TEMPERATURE}


def test_the_managed_mask_is_a_valid_recipe_mask_and_keeps_what_the_person_added():
    shape = encode_raster(light_depth.coverage(scene(), settings()))
    other = {"name": "Brush 1", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}, "adjust": {"contrast": 10}}
    rows = light_depth.apply([other], settings(), shape)
    assert masks_valid(rows) and [row["name"] for row in rows] == ["Brush 1", "Light Depth"]
    rows[1]["parts"] = [{"op": "subtract", "shape": other["shape"], "invert": False}]
    rows[1]["adjust"]["contrast"] = 15
    again = light_depth.apply(rows, settings(amount=20, warmth=40), shape)
    assert len(again) == 2 and again[0] == other
    assert again[1]["parts"] == rows[1]["parts"] and again[1]["adjust"]["contrast"] == 15
    assert again[1]["adjust"]["exposure"] == pytest.approx(0.4) and again[1]["adjust"]["temperature"] == pytest.approx(24)
    cold = light_depth.apply(again, settings(amount=20, warmth=0), shape)
    assert "temperature" not in cold[1]["adjust"]
    assert rows[1]["adjust"]["exposure"] != again[1]["adjust"]["exposure"]  # the input rows are not modified


def test_a_renamed_mask_is_no_longer_managed():
    rows = light_depth.apply([], settings(), encode_raster(np.zeros((4, 4), dtype=np.uint8)))
    rows[0]["name"] = "Window light"
    assert light_depth.find_mask(rows) == -1
    assert len(light_depth.apply(rows, settings(), rows[0]["shape"])) == 2


def test_coverage_stays_small_enough_for_the_recipe():
    rng = np.random.default_rng(3)
    noisy = np.clip(scene(1500, 1000).astype(int) + rng.integers(-6, 7, (1000, 1500)), 0, 255).astype(np.uint8)
    lit = light_depth.coverage(noisy, settings(softness=30))
    assert max(lit.shape) == light_depth.COVERAGE_SIDE
    shape = encode_raster(lit)
    assert masks_valid([{"shape": shape}]) and len(shape["data"]) < 700_000
    assert np.array_equal(decode_raster(shape), lit)


def _reference_encoding(array):
    values = np.asarray(array, dtype=np.uint8).ravel()
    out, index = bytearray(), 0
    while index < len(values):
        end = index
        while end < len(values) and values[end] == values[index]:
            end += 1
        out.append(int(values[index]))
        run = end - index
        while run >= 128:
            out.append((run & 127) | 128)
            run >>= 7
        out.append(run)
        index = end
    return bytes(out)


@pytest.mark.parametrize("array", [
    np.zeros((1, 1), dtype=np.uint8),
    np.full((130, 130), 7, dtype=np.uint8),       # one run needing three digits
    np.full((1536, 1536), 255, dtype=np.uint8),   # the longest run a raster can hold
    np.arange(300, dtype=np.uint8).reshape(15, 20),
    (np.arange(127 * 3).reshape(3, 127) // 127).astype(np.uint8),
    (np.arange(128 * 3).reshape(3, 128) // 128).astype(np.uint8),
])
def test_raster_encoding_is_byte_identical_to_the_recipe_format(array):
    shape = encode_raster(array)
    assert base64.b64decode(shape["data"]) == _reference_encoding(array)
    assert masks_valid([{"shape": shape}]) or max(array.shape) > 1536
    assert np.array_equal(decode_raster(shape), array)


def test_settings_survive_beside_the_recipe_and_bad_files_are_ignored(tmp_path):
    recipe = tmp_path / "IMG_1.NEF.cameraraw.json"
    assert light_depth.sidecar_path(recipe).name == "IMG_1.NEF.photocraft.lightdepth.json"
    assert light_depth.read_sidecar(recipe) is None
    light_depth.write_sidecar(recipe, settings(warmth=12))
    assert light_depth.read_sidecar(recipe) == light_depth.normalized(settings(warmth=12))
    assert [p.name for p in tmp_path.iterdir()] == ["IMG_1.NEF.photocraft.lightdepth.json"]  # no temporary left behind
    light_depth.sidecar_path(recipe).write_text('{"version": 1, "settings": {"amount": "lots"}}')
    assert light_depth.read_sidecar(recipe) is None
    light_depth.write_sidecar(recipe, None)
    assert not light_depth.sidecar_path(recipe).exists()
    light_depth.write_sidecar(recipe, None)  # removing what is not there is fine


def test_unusable_values_fall_back_to_defaults():
    assert light_depth.normalized({"amount": float("nan"), "warmth": 500, "x": "left"}) == {
        **light_depth.DEFAULTS, "warmth": 100.0}
    assert not light_depth.valid({**light_depth.DEFAULTS, "amount": 101})
    assert light_depth.valid(dict(light_depth.DEFAULTS))


def test_the_settings_file_travels_with_the_photos_other_edit_files():
    from image_triage.edit_carry import EDIT_MARKERS

    assert light_depth.SIDECAR_TAIL.startswith(EDIT_MARKERS)
