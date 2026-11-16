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


def test_height_holds_the_fog_to_the_lower_part_of_the_frame():
    low = fog_of({"patchiness": 0, "height": 20, "start": 0})
    full = fog_of({"patchiness": 0, "height": 100, "start": 0})
    assert low[:40].max() < 0.02                                         # nothing in the upper third
    assert full[:40].mean() > 0.5
    flat = np.full((100, 100), 20, np.uint8)                              # everything far: only height limits the fog
    ground_fog = fog_of({"patchiness": 0, "height": 30, "start": 0}, flat, 100)
    assert ground_fog[-15:].mean() > 0.9 and ground_fog[:30].mean() < 0.05


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


def test_amount_scales_the_adjustment_and_vibrance_decides_how_much_colour_the_fog_keeps():
    none = atmosphere.adjustments({"amount": 0})
    assert none == {} or all(abs(v) < 1e-9 for v in none.values())
    half, full = atmosphere.adjustments({"amount": 50}), atmosphere.adjustments({"amount": 100})
    assert full["dehaze"] < half["dehaze"] < 0 and full["exposure"] > half["exposure"] > 0 and full["contrast"] < half["contrast"] < 0
    assert full["blacks"] > half["blacks"] > 0 and full["clarity"] < 0 and full["texture"] < 0
    keeps = atmosphere.adjustments({"amount": 100, "vibrance": 100}).get("saturation", 0)
    washes = atmosphere.adjustments({"amount": 100, "vibrance": 0})["saturation"]
    assert keeps > washes and washes < 0 and -100 <= washes and keeps <= 100


def test_presets_set_the_kind_of_fog_and_leave_the_drift_and_model_alone():
    mine = atmosphere.normalized({"driftX": 0.2, "driftY": 0.9, "depthModel": 1, "amount": 5})
    wispy, heavy, ground = (atmosphere.with_preset(mine, i) for i in range(3))
    assert wispy["preset"] == 1 and heavy["preset"] == 2 and ground["preset"] == 3
    assert ground["height"] < heavy["height"] and wispy["patchiness"] > heavy["patchiness"] and heavy["amount"] > wispy["amount"]
    assert all(s["driftX"] == 0.2 and s["driftY"] == 0.9 and s["depthModel"] == 1 for s in (wispy, heavy, ground))
    assert atmosphere.active(heavy) and atmosphere.normalized(atmosphere.with_preset(mine, 9)) == mine      # a bad index: unchanged
    assert atmosphere.valid(heavy) and not atmosphere.valid({**heavy, "amount": 120}) and not atmosphere.valid({"amount": 1})


def test_normalized_clamps_and_repairs():
    assert atmosphere.normalized(None) == atmosphere.DEFAULTS
    fixed = atmosphere.normalized({"amount": 500, "height": -4, "x": 3, "cloudSize": float("nan"), "junk": 1})
    assert fixed["amount"] == 100 and fixed["height"] == 0 and fixed["cloudSize"] == atmosphere.DEFAULTS["cloudSize"] and "junk" not in fixed


def test_the_mask_is_one_row_with_the_fog_as_its_coverage_and_nothing_when_it_is_off():
    depth = ramp()
    assert atmosphere.render_masks(atmosphere.normalized({"amount": 0}), depth) == []
    assert atmosphere.render_masks(atmosphere.normalized({"amount": 60, "shown": 0}), depth) == []
    settings = atmosphere.with_preset({}, 1)
    rows = atmosphere.render_masks(settings, depth)
    assert len(rows) == 1 and atmosphere.masks_needed(settings) == 1
    row = rows[0]
    assert row["name"] == atmosphere.FOG_NAME and row["enabled"] and not row["invert"] and row["amount"] == 100
    assert row["shape"]["type"] == "raster" and row["adjust"]["dehaze"] < 0
    rows[0]["adjust"]["dehaze"] = 5                                      # callers cannot damage what is kept
    assert atmosphere.render_masks(settings, depth)[0]["adjust"]["dehaze"] < 0


def test_with_masks_adds_the_fog_after_the_persons_masks_and_drops_the_hosts_entry(monkeypatch):
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": ramp())
    mine = {"name": "Window", "shape": {"type": "linear", "x0": 0, "y0": 0, "x1": 1, "y1": 1}}
    settings = {"exposure": 0.3, "masks": [mine], "atmosphere": atmosphere.with_preset({}, 1)}
    out = atmosphere.with_masks(settings, settings["atmosphere"], "photo.nef")
    assert "atmosphere" not in out and [m["name"] for m in out["masks"]] == ["Window", atmosphere.FOG_NAME] and out["exposure"] == 0.3
    off = atmosphere.with_masks({**settings, "atmosphere": {"amount": 0}}, {"amount": 0}, "photo.nef")
    assert "atmosphere" not in off and off["masks"] == [mine]
    full = {"masks": [dict(mine)] * atmosphere.MAX_MASKS, "atmosphere": atmosphere.with_preset({}, 1)}
    with pytest.raises(ValueError, match="masks"):
        atmosphere.with_masks(full, full["atmosphere"], "photo.nef")
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": None)
    with pytest.raises(ValueError, match="depth map is missing"):
        atmosphere.with_masks(settings, settings["atmosphere"], "photo.nef")


def test_host_effects_build_light_depth_then_atmosphere_and_count_each_ones_masks(monkeypatch):
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": ramp())
    settings = {"lightDepth": light_depth.normalized({"amount": 60}), "atmosphere": atmosphere.with_preset({}, 1)}
    out = host_effects.with_masks(settings, "photo.nef")
    names = [m["name"] for m in out["masks"]]
    assert names == [light_depth.NEAR_NAME, light_depth.FAR_NAME, atmosphere.FOG_NAME] and not (set(out) & set(host_effects.KEYS))
    assert host_effects.masks_in_use(settings, besides="atmosphere") == 2 and host_effects.masks_in_use(settings, besides="lightDepth") == 1
    assert host_effects.without_host({"a": 1, **settings}) == {"a": 1}
    only = host_effects.with_masks({"atmosphere": atmosphere.with_preset({}, 0)}, "photo.nef")
    assert [m["name"] for m in only["masks"]] == [atmosphere.FOG_NAME]


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
