"""Atmosphere's arithmetic: where the fog lies, what it does, the masks it makes, and its settings file."""
import numpy as np
import pytest

from image_triage import atmosphere, host_effects, light_depth


def ramp(h=120, w=160):
    """A depth map: near (255) at the bottom, far (0) at the top, as a photo of ground running away to the horizon."""
    column = np.linspace(0, 255, h).astype(np.uint8)
    return np.repeat(column[:, None], w, axis=1)


def fog_of(settings, depth=None, side=160):
    depth = ramp() if depth is None else depth
    return atmosphere.density(depth, atmosphere.normalized(settings), side, atmosphere.seed_of(depth))


def test_fog_is_thin_close_and_dense_far_away_and_the_nearest_layer_can_be_kept_clean():
    fog = fog_of({"amount": 50, "patchiness": 0, "height": 100, "start": 0})
    top, bottom = fog[:20].mean(), fog[-20:].mean()
    assert top > 0.9 and bottom < 0.1 and np.all(np.diff(fog.mean(axis=1)) <= 1e-3)       # denser with distance, steadily
    later = fog_of({"amount": 50, "patchiness": 0, "height": 100, "start": 80})
    assert later[len(later) // 2:].mean() < 0.05 < later[:10].mean()                      # start pushes the fog away from the camera


def standing_scene(h=120, w=160):
    """Ground running away up to a horizon 60 % of the way up the frame, and the sky (as far as it gets) above it."""
    ground = np.linspace(10, 255, int(h * 0.6)).astype(np.uint8)
    sky = np.zeros(h - ground.size, np.uint8)
    return np.repeat(np.concatenate([sky, ground])[:, None], w, axis=1)


def test_height_holds_the_fog_to_the_ground_and_not_the_bottom_edge_of_the_picture():
    scene = standing_scene()
    low = fog_of({"patchiness": 0, "height": 10, "start": 0}, scene)
    full = fog_of({"patchiness": 0, "height": 100, "start": 0}, scene)
    assert low[:8].max() < 0.05                                          # none high in the sky
    assert low[75:95].mean() > 0.5                                       # but a bank lying on the far ground, not at the bottom edge
    assert low[-10:].mean() < 0.1
    assert full[:8].mean() > 0.5                                         # full height: the sky is fogged too


def test_patchiness_breaks_the_fog_into_clumps_and_cloud_size_sets_their_size():
    flat = np.full((120, 160), 10, np.uint8)
    even = fog_of({"patchiness": 0, "start": 0}, flat)
    assert even.std() < 0.02
    patchy = fog_of({"patchiness": 90, "cloudSize": 50, "start": 0}, flat)
    assert patchy.std() > 0.1 and patchy.min() < 0.3 < patchy.max()
    small = fog_of({"patchiness": 90, "cloudSize": 0, "start": 0}, flat)
    large = fog_of({"patchiness": 90, "cloudSize": 100, "start": 0}, flat)

    def change_rate(a):
        return float(np.abs(np.diff(a, axis=1)).mean())

    assert change_rate(small) > change_rate(large)                       # small clouds change faster across the picture


def test_the_same_photo_gets_the_same_fog_and_drift_moves_the_patches_without_changing_the_fog():
    flat = np.full((120, 160), 10, np.uint8)
    base = {"patchiness": 80, "start": 0}
    a, b = fog_of(base, flat), fog_of(base, flat)
    assert np.array_equal(a, b)                                            # no randomness between renders
    moved = fog_of({**base, "driftX": 0.9, "driftY": 0.2}, flat)
    assert np.abs(moved - a).mean() > 0.03                                 # the patches lie elsewhere ...
    assert abs(moved.mean() - a.mean()) < 0.12                             # ... and it is the same amount of fog
    other = fog_of(base, np.full((120, 160), 11, np.uint8))
    assert np.abs(other - a).mean() > 0.01                                 # another photo's map: another arrangement


def test_the_fog_is_laid_over_the_picture_by_distance_and_amount_and_the_foreground_stays_clear():
    depth = ramp()
    picture = np.full((120, 160, 3), 0.15, np.float32)                   # a dark scene
    settings = atmosphere.normalized({"amount": 60, "patchiness": 0, "height": 100, "start": 0})
    out = atmosphere.composite(picture, depth, settings)
    assert out[:15].mean() > 0.45 and out[-15:].mean() < 0.25           # far away it is nearly all fog (lifted to the fog's grey-blue), the foreground is left alone
    assert out[:15, :, 2].mean() > out[:15, :, 0].mean()                 # a cool fog
    thin = atmosphere.composite(picture, depth, {**settings, "amount": 15})
    assert 0.15 < thin[:15].mean() < out[:15].mean()                     # Amount sets how thick
    assert atmosphere.composite(picture, depth, {**settings, "amount": 0}) is picture or np.array_equal(
        atmosphere.composite(picture, depth, {**settings, "amount": 0}), picture)
    assert atmosphere.composite(picture, depth, {**settings, "shown": 0}) is picture or np.array_equal(
        atmosphere.composite(picture, depth, {**settings, "shown": 0}), picture)
    bright = np.full((120, 160, 3), 0.8, np.float32)
    assert atmosphere.composite(bright, depth, settings)[:15].mean() > out[:15].mean()           # the fog is as light as the photo


def test_vibrance_decides_how_much_of_the_photos_colour_the_fog_takes():
    depth = ramp()
    warm = np.zeros((120, 160, 3), np.float32)
    warm[...] = (0.55, 0.30, 0.10)
    base = {"amount": 80, "patchiness": 0, "height": 100, "start": 0}
    grey = atmosphere.composite(warm, depth, {**atmosphere.normalized(base), "vibrance": 0})[:15].mean(axis=(0, 1))
    kept = atmosphere.composite(warm, depth, {**atmosphere.normalized(base), "vibrance": 100})[:15].mean(axis=(0, 1))
    assert kept[0] - kept[2] > grey[0] - grey[2]                         # more of the warm light stays in the fog


def test_a_qimage_a_preview_frame_and_a_file_all_get_the_same_fog(tmp_path, monkeypatch):
    from PySide6.QtGui import QImage

    depth = ramp(60, 80)
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": depth)
    settings = atmosphere.with_preset({}, 1)
    flat = np.full((60, 80, 3), 40, np.uint8)
    image = QImage(flat.data, 80, 60, 240, QImage.Format.Format_RGB888).copy()
    fogged = atmosphere.fogged_image(image, depth, settings)
    assert fogged.width() == 80 and fogged.height() == 60 and fogged.pixelColor(40, 2).blue() > 60
    header, frame = atmosphere.fogged_frame(({"x": 1}, image), "photo.nef", settings)
    assert header == {"x": 1} and frame.pixelColor(40, 2).blue() == fogged.pixelColor(40, 2).blue()
    assert atmosphere.fogged_frame(({"x": 1}, image), "photo.nef", {"amount": 0})[1] is image
    source = tmp_path / "in.png"
    image.save(str(source))
    target = tmp_path / "out.png"
    atmosphere.fogged_file(source, target, "photo.nef", settings)
    saved = QImage(str(target))
    assert abs(saved.pixelColor(40, 2).blue() - fogged.pixelColor(40, 2).blue()) <= 3
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": None)
    with pytest.raises(ValueError, match="depth map is missing"):
        atmosphere.fogged_frame(({}, image), "photo.nef", settings)


def test_presets_set_the_kind_of_fog_and_leave_the_drift_and_model_alone():
    mine = atmosphere.normalized({"driftX": 0.2, "driftY": 0.9, "depthModel": 1, "amount": 5})
    wispy, heavy, ground = (atmosphere.with_preset(mine, i) for i in range(3))
    assert wispy["preset"] == 1 and heavy["preset"] == 2 and ground["preset"] == 3
    assert ground["height"] < heavy["height"] and wispy["patchiness"] > heavy["patchiness"] and heavy["amount"] < wispy["amount"] < ground["amount"]
    assert all(s["driftX"] == 0.2 and s["driftY"] == 0.9 and s["depthModel"] == 1 for s in (wispy, heavy, ground))
    assert atmosphere.active(heavy) and atmosphere.normalized(atmosphere.with_preset(mine, 9)) == mine      # a bad index: unchanged
    assert atmosphere.valid(heavy) and not atmosphere.valid({**heavy, "amount": 120}) and not atmosphere.valid({"amount": 1})


def test_normalized_clamps_and_repairs():
    assert atmosphere.normalized(None) == atmosphere.DEFAULTS
    fixed = atmosphere.normalized({"amount": 500, "height": -4, "x": 3, "cloudSize": float("nan"), "junk": 1})
    assert fixed["amount"] == 100 and fixed["height"] == 0 and fixed["cloudSize"] == atmosphere.DEFAULTS["cloudSize"] and "junk" not in fixed


def test_fog_uses_no_masks_and_the_hosts_entry_is_dropped_from_what_the_renderer_gets(monkeypatch):
    settings = atmosphere.with_preset({}, 1)
    assert atmosphere.masks_needed(settings) == 0
    mine = {"name": "Window", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}}
    full = {"exposure": 0.3, "masks": [dict(mine)] * atmosphere.MAX_MASKS, "atmosphere": settings}
    out = atmosphere.with_masks(full, settings, "photo.nef")
    assert "atmosphere" not in out and out["exposure"] == 0.3 and len(out["masks"]) == atmosphere.MAX_MASKS      # no budget taken
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": ramp())
    both = {"lightDepth": light_depth.normalized({"amount": 60}), "atmosphere": settings}
    names = [m["name"] for m in host_effects.with_masks(both, "photo.nef")["masks"]]
    assert names == [light_depth.NEAR_NAME, light_depth.FAR_NAME] and not (set(host_effects.with_masks(both, "photo.nef")) & set(host_effects.KEYS))
    assert host_effects.masks_in_use(both, besides="atmosphere") == 2 and host_effects.masks_in_use(both, besides="lightDepth") == 0
    assert host_effects.without_host({"a": 1, **both}) == {"a": 1}


def test_the_settings_file_round_trips_and_an_unusable_one_is_ignored(tmp_path):
    recipe = tmp_path / "photo.nef.cameraraw.json"
    assert atmosphere.read_sidecar(recipe) is None
    settings = atmosphere.with_preset({"driftX": 0.7}, 2)
    atmosphere.write_sidecar(recipe, settings)
    assert atmosphere.sidecar_path(recipe).name == "photo.nef.photocraft.atmosphere.json"
    assert atmosphere.read_sidecar(recipe) == settings
    atmosphere.sidecar_path(recipe).write_text("not json")
    assert atmosphere.read_sidecar(recipe) is None
    atmosphere.sidecar_path(recipe).write_text('{"version": 1, "settings": {"amount": 500}}')
    assert atmosphere.read_sidecar(recipe) is None
    atmosphere.write_sidecar(recipe, settings)
    atmosphere.write_sidecar(recipe, None)
    assert not atmosphere.sidecar_path(recipe).exists()
    from image_triage import edit_carry
    assert atmosphere.SIDECAR_TAIL.casefold().startswith(edit_carry.EDIT_MARKERS) and edit_carry.EDIT_MARKERS[-1] in atmosphere.SIDECAR_TAIL
