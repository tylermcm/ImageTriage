"""Light Depth: near/far coverage, the masks built for a render, the legacy mask filter, and the settings beside the recipe."""
import base64
import json

import numpy as np
import pytest

from image_triage import light_depth, photocraft_bridge as bridge
from image_triage.camera_raw_recipe_masks import masks_valid
from image_triage.qml_shell.raw_masks import decode_raster, encode_raster


def scene(width=120, height=80):
    """Far at the top (0), near at the bottom (255), like a ground plane."""
    return np.repeat(np.linspace(0, 255, height).astype(np.uint8)[:, None], width, axis=1)


def settings(**changes):
    return {**light_depth.DEFAULTS, "amount": 70, "softness": 0, **changes}


def test_the_light_falls_only_on_what_lies_at_the_depth():
    near, light, far = (part.astype(int) for part in light_depth.zones(scene(), settings(depth=0.5, feather=0)))
    assert light[40, 60] > 250 and light[2, 60] < 5 and light[-3, 60] < 5       # the middle of the ramp is lit, the ends are not
    assert near[-1, 60] > 250 and near[2, 60] < 5                                 # what is nearer than the slab
    assert far[2, 60] > 250 and far[-1, 60] < 5                                   # and what is beyond it
    assert np.abs(near + light + far - 255).max() <= 2                            # every pixel is in exactly one zone
    shallow = light_depth.coverage(scene(), settings(depth=0.9, feather=100)).astype(int).sum()
    assert shallow > light_depth.coverage(scene(), settings(depth=0.9, feather=0)).astype(int).sum() * 3


def test_feather_makes_the_slab_at_the_depth_thicker():
    def rows(feather):
        return int(np.count_nonzero(light_depth.coverage(scene(), settings(feather=feather))[:, 60] > 128))
    assert rows(100) > rows(0) * 4


def test_softness_spills_the_light_across_a_depth_edge():
    wall = np.zeros((80, 120), dtype=np.uint8)
    wall[:, 60:] = 255  # a near object against a far background
    hard = light_depth.coverage(wall, settings(depth=1.0, feather=0, softness=0)).astype(int)
    soft = light_depth.coverage(wall, settings(depth=1.0, feather=0, softness=100)).astype(int)
    assert hard[40, 57] < 5 and soft[40, 57] > hard[40, 57] + 20      # light reaches the background beside the wall
    assert soft[40, 110] > 200


def test_the_effect_follows_the_objects_at_the_depth_not_the_rows_they_stand_on():
    """A tree at the chosen depth is lit; the sky beside it on the very same rows is not, and vice versa."""
    scene_ = np.full((120, 160), 40, dtype=np.uint8)           # far sky
    scene_[:, 90:100] = 200                                    # a tree, tall and narrow, at depth ~0.78
    scene_[100:, :] = np.linspace(120, 255, 20).astype(np.uint8)[:, None]    # ground in front
    tree, sky = (slice(20, 80), slice(92, 98)), (slice(20, 80), slice(30, 60))
    on_tree = light_depth.coverage(scene_, settings(depth=0.78, feather=0, softness=0)).astype(int)
    assert on_tree[tree].min() > 250 and on_tree[sky].max() < 5
    on_sky = light_depth.coverage(scene_, settings(depth=0.157, feather=0, softness=0)).astype(int)
    assert on_sky[sky].min() > 250 and on_sky[tree].max() < 5


def test_a_size_below_100_narrows_the_effect_to_a_pool_around_the_position():
    whole = light_depth.zones(scene(), settings(x=0.1, y=0.5, size=100, depth=0.85, feather=100))
    assert whole[1][68, 12] > 200 and whole[1][68, -5] > 200       # the whole photo: lit wherever the depth is
    pool = light_depth.zones(scene(), settings(x=0.1, y=0.85, size=0, depth=0.85, feather=100))
    light = pool[1].astype(int)
    assert light[68, 12] > 200 and light[68, -5] < 5                # lit by the pool at the left, not at the far right
    wide = light_depth.zones(scene(), settings(x=0.1, y=0.85, size=60, depth=0.85, feather=100))[1].astype(int)
    assert wide[68, -5] > light[68, -5] + 40                        # a larger size reaches further across the photo


def test_depth_under_a_point_ignores_one_noisy_pixel():
    values = scene()
    values[40, 60] = 0
    assert light_depth.depth_at(values, 0.5, 0.5) == pytest.approx(0.5, abs=0.03)
    assert light_depth.depth_at(values, 0.5, 0.0) < 0.05 and light_depth.depth_at(values, 0.5, 1.0) > 0.95


def test_amount_brightness_and_warmth_become_local_adjustments_for_each_zone():
    near, slab, far = light_depth.adjustments(settings(amount=100, warmth=20, brightnessNear=40, brightnessFar=-100, warmthFar=-30))
    assert near == {"exposure": pytest.approx(0.4 * light_depth.MAX_EXPOSURE), "temperature": pytest.approx(0.2 * light_depth.MAX_TEMPERATURE)}
    assert far == {"exposure": -light_depth.MAX_EXPOSURE, "temperature": pytest.approx(-0.1 * light_depth.MAX_TEMPERATURE)}
    assert slab == {"temperature": pytest.approx(0.2 * light_depth.MAX_TEMPERATURE)}     # the depth keeps its brightness; Warmth is overall
    half = light_depth.adjustments(settings(amount=50, brightnessNear=100))[0]
    assert half == {"exposure": light_depth.MAX_EXPOSURE / 2}                            # Amount scales it; zero values are left out
    assert light_depth.adjustments(settings(amount=0)) == ({}, {}, {})
    assert light_depth.masks_needed(settings()) == 2 and light_depth.masks_needed(settings(warmth=10)) == 3
    assert light_depth.masks_needed(settings(amount=0)) == 0 and light_depth.masks_needed(settings(shown=0)) == 0


def test_by_default_both_sides_are_darkened_and_the_depth_is_left_alone_like_luminar():
    assert light_depth.DEFAULTS["brightnessNear"] == light_depth.DEFAULTS["brightnessFar"] == -50
    near, slab, far = light_depth.adjustments({**light_depth.DEFAULTS, "amount": 100})
    assert near == far == {"exposure": -1.0} and slab == {}


def test_active_needs_an_amount_and_to_be_switched_on():
    assert not light_depth.active(None) and not light_depth.active(settings(amount=0))
    assert light_depth.active(settings()) and not light_depth.active(settings(shown=0))


def test_render_masks_are_valid_recipe_masks_for_the_zones_that_change():
    rows = light_depth.render_masks(settings(depth=0.5, feather=0), scene())
    assert masks_valid(rows) and [row["name"] for row in rows] == [light_depth.NEAR_NAME, light_depth.FAR_NAME]
    assert [row["adjust"] for row in rows] == [{"exposure": pytest.approx(-0.7)}] * 2
    assert decode_raster(rows[0]["shape"])[-1, 60] > 250 and decode_raster(rows[1]["shape"])[2, 60] > 250
    assert light_depth.SLAB_NAME not in [row["name"] for row in rows]             # the depth itself is not touched
    rows[0]["adjust"]["exposure"] = 99                  # what a render gets is a copy: the cache is not altered by its user
    assert light_depth.render_masks(settings(depth=0.5, feather=0), scene())[0]["adjust"]["exposure"] == pytest.approx(-0.7)


def test_a_zone_that_changes_nothing_gets_no_mask_and_an_inactive_light_none():
    only_near = light_depth.render_masks(settings(brightnessFar=0), scene())
    assert [row["name"] for row in only_near] == [light_depth.NEAR_NAME]
    warm = light_depth.render_masks(settings(warmth=30), scene())
    assert [row["name"] for row in warm] == [light_depth.NEAR_NAME, light_depth.SLAB_NAME, light_depth.FAR_NAME]
    assert warm[1]["adjust"].keys() == {"temperature"}
    assert light_depth.render_masks(settings(shown=0), scene()) == []
    assert light_depth.render_masks(settings(amount=0), scene()) == []


def test_coverage_stays_small_enough_for_the_recipe():
    rng = np.random.default_rng(3)
    noisy = np.clip(scene(2000, 1400).astype(int) + rng.integers(-6, 7, (1400, 2000)), 0, 255).astype(np.uint8)
    lit = light_depth.coverage(noisy, settings(softness=30))
    assert max(lit.shape) == light_depth.COVERAGE_SIDE
    shape = encode_raster(lit)
    assert masks_valid([{"shape": shape}]) and len(shape["data"]) < 700_000
    assert np.array_equal(decode_raster(shape), lit)


@pytest.fixture
def photo(tmp_path, monkeypatch):
    """A photo with a recipe folder and a kept depth map."""
    from PIL import Image

    path = tmp_path / "IMG_1.NEF"
    path.write_bytes(b"raw")
    bridge.recipe_path(str(path)).parent.mkdir(parents=True, exist_ok=True)
    kept = tmp_path / "kept" / "light_depth.png"
    kept.parent.mkdir()
    Image.fromarray(scene()).save(kept)
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: kept)
    return path


def test_with_masks_adds_the_masks_after_the_persons_own_and_drops_the_host_entry(photo):
    own = {"name": "Brush 1", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}, "adjust": {"contrast": 10}}
    draft = {"exposure": 1, "masks": [own], "lightDepth": settings(brightnessFar=0)}
    result = light_depth.with_masks(draft, draft["lightDepth"], str(photo))
    assert "lightDepth" not in result and result["exposure"] == 1
    assert [mask["name"] for mask in result["masks"]] == ["Brush 1", light_depth.NEAR_NAME]
    assert draft["masks"] == [own] and "lightDepth" in draft          # the draft itself is not changed
    off = light_depth.with_masks({"masks": [own], "lightDepth": settings(shown=0)}, settings(shown=0), str(photo))
    assert off == {"masks": [own]}


def test_with_masks_says_so_when_it_cannot_make_them(photo, monkeypatch):
    shape = {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}
    many = {"masks": [{"name": f"M{i}", "shape": shape} for i in range(16)]}
    with pytest.raises(ValueError, match="2 of a photo's 16"):
        light_depth.with_masks({"masks": many["masks"][:15]}, settings(), str(photo))
    assert len(light_depth.with_masks({"masks": many["masks"][:14]}, settings(), str(photo))["masks"]) == 16
    with pytest.raises(ValueError, match="3 of a photo's 16"):
        light_depth.with_masks({"masks": many["masks"][:14]}, settings(warmth=20), str(photo))
    monkeypatch.setattr("image_triage.depth_maps.light_depth_cache_file", lambda source, **kw: photo.parent / "gone.png")
    with pytest.raises(ValueError, match="depth map is missing"):
        light_depth.with_masks({}, settings(), str(photo))
    assert light_depth.with_masks({}, settings(amount=0), str(photo)) == {}      # an effect that does nothing needs no map


def test_an_earlier_versions_mask_is_removed_wherever_it_is_found():
    mine = {"name": "Window light", "shape": {}}
    legacy = {"name": "Light Depth", "shape": {}}
    assert light_depth.strip_legacy({"masks": [mine, legacy], "exposure": 1}) == {"masks": [mine], "exposure": 1}
    assert light_depth.strip_legacy({"masks": [legacy]}) == {}
    untouched = {"masks": [mine]}
    assert light_depth.strip_legacy(untouched) is untouched


def old_sidecar(recipe, **changes):
    old = {"amount": 55, "x": 0.3, "y": 0.4, "depth": 0.3, "softness": 20, "warmth": 10, "reach": 60, "focus": 50, **changes}
    light_depth.sidecar_path(recipe).write_text(json.dumps({"version": 1, "settings": old}))


def test_an_earlier_versions_mask_with_its_settings_carries_on_as_the_panels_effect(photo):
    recipe = bridge.recipe_path(str(photo))
    legacy = {"name": "Light Depth", "shape": {}}
    mine = {"name": "Brush 1", "shape": {}}
    old_sidecar(recipe)
    cleaned, light = light_depth.load_settings(recipe, {"masks": [mine, legacy]})
    assert cleaned == {"masks": [mine]} and light_depth.valid(light)
    assert light["amount"] == 55 and light["depth"] == 0.3 and light["feather"] == 30 and light["size"] == pytest.approx(55)
    assert light["depthModel"] == 0                          # the first version's maps were the Small model's
    assert light["brightnessNear"] == light["brightnessFar"] == -50


def test_an_earlier_versions_mask_without_settings_is_dropped_and_stale_settings_are_ignored(photo):
    recipe = bridge.recipe_path(str(photo))
    legacy = {"name": "Light Depth", "shape": {}}
    assert light_depth.load_settings(recipe, {"masks": [legacy]}) == ({}, None)
    old_sidecar(recipe)
    assert light_depth.load_settings(recipe, {"exposure": 1}) == ({"exposure": 1}, None)       # its mask was deleted elsewhere
    light_depth.write_sidecar(recipe, settings(amount=40))
    assert light_depth.load_settings(recipe, {"exposure": 1})[1]["amount"] == 40               # current settings need no mask


def test_export_adds_the_masks_and_a_photo_with_only_light_depth_is_edited(photo):
    recipe = bridge.recipe_path(str(photo))
    assert light_depth.recipe_for_render(str(photo), None) is None
    saved = {"version": bridge.RECIPE_VERSION, "cameraRaw": {"exposure": 1}}
    assert light_depth.recipe_for_render(str(photo), saved) is saved
    light_depth.write_sidecar(recipe, settings())
    result = light_depth.recipe_for_render(str(photo), saved)
    assert [m["name"] for m in result["cameraRaw"]["masks"]] == [light_depth.NEAR_NAME, light_depth.FAR_NAME]
    assert "masks" not in saved["cameraRaw"]
    only = light_depth.recipe_for_render(str(photo), None)
    assert bridge.classify_recipe(only)[0] == bridge.RECIPE_EDITED and only["version"] == bridge.RECIPE_VERSION


def test_settings_survive_beside_the_recipe_and_bad_files_are_ignored(tmp_path):
    recipe = tmp_path / "IMG_1.NEF.cameraraw.json"
    assert light_depth.sidecar_path(recipe).name == "IMG_1.NEF.photocraft.lightdepth.json"
    assert light_depth.read_sidecar(recipe) == (None, False)
    light_depth.write_sidecar(recipe, settings(warmth=12))
    assert light_depth.read_sidecar(recipe) == (light_depth.normalized(settings(warmth=12)), False)
    assert [p.name for p in tmp_path.iterdir()] == ["IMG_1.NEF.photocraft.lightdepth.json"]  # no temporary left behind
    light_depth.sidecar_path(recipe).write_text('{"version": 2, "settings": {"amount": "lots"}}')
    assert light_depth.read_sidecar(recipe) == (None, False)
    light_depth.sidecar_path(recipe).write_text('{"version": 1, "settings": {"amount": "lots"}}')
    assert light_depth.read_sidecar(recipe) == (None, True)
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


def test_the_effect_comes_in_over_the_feather_and_the_markers_sit_where_it_is_full():
    ramp = np.repeat(np.linspace(0, 255, 256).astype(np.uint8)[:, None], 20, axis=1)      # depth 0..1 down the rows
    for feather in (0, 25, 100):
        span = light_depth.feather_span(feather)
        near, light, far = (part[:, 10].astype(int) for part in light_depth.zones(ramp, settings(depth=0.5, feather=feather, softness=0)))
        row = lambda depth: int(round(depth * 255))      # noqa: E731
        assert light[row(0.5)] > 250 and near[row(0.5)] < 5 and far[row(0.5)] < 5          # nothing happens at the depth itself
        assert near[min(255, row(0.5 + span) + 2)] > 245 or 0.5 + span > 1                 # in full at the near marker
        assert far[max(0, row(0.5 - span) - 2)] > 245 or 0.5 - span < 0                    # and at the far one
        if feather == 100:
            assert 40 < near[row(0.5 + span / 2)] < 215                                    # between them it is gradual
    assert light_depth.feather_span(0) < light_depth.feather_span(50) < light_depth.feather_span(100)
    for feather in (0, 13, 50, 100):
        assert light_depth.feather_for_span(light_depth.feather_span(feather)) == pytest.approx(feather)
    assert light_depth.feather_for_span(5) == 100 and light_depth.feather_for_span(0) == 0       # out of range: held at the ends


def test_light_depth_uses_the_large_model_by_default_and_an_earlier_builds_wrap_becomes_feather(tmp_path):
    assert light_depth.model_key(None) == "large" and light_depth.DEFAULTS["feather"] == 15
    recipe = tmp_path / "IMG_1.NEF.cameraraw.json"
    old = {k: v for k, v in light_depth.normalized({"amount": 40}).items() if k not in ("feather", "depthModel")}
    light_depth.sidecar_path(recipe).write_text('{"version": 2, "settings": %s}' % __import__("json").dumps({**old, "wrap": 40}))
    settings_, legacy = light_depth.read_sidecar(recipe)
    assert settings_["feather"] == 20 and "wrap" not in settings_ and settings_["depthModel"] == 0 and not legacy
