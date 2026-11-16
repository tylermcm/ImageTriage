"""Bokeh's arithmetic: where the blur falls, the kernels, the layered render, and its settings file."""
import numpy as np
import pytest

from image_triage import bokeh, host_effects, light_depth


def depth_with_subject(h=80, w=120):
    """A depth map (255 = nearest): a near subject on the left (a block), a far background everywhere else."""
    depth = np.full((h, w), 40, np.uint8)
    depth[:, : w // 3] = 230
    return depth


def checker(h=80, w=120):
    rows = (np.arange(h)[:, None] // 3 + np.arange(w)[None, :] // 3) % 2
    return np.repeat((0.15 + 0.7 * rows)[..., None], 3, axis=2).astype(np.float32)


def settings(**kw):
    return bokeh.normalized({"amount": 70, "focus": 0, "range": 20, "shape": 1, **kw})


def test_the_blur_is_zero_in_the_sharp_band_and_grows_with_distance_from_it():
    s = settings(focus=40, range=20)
    z = np.array([0.0, 0.2, 0.4, 0.5, 0.7, 1.0], np.float32)
    r = bokeh.radius_at(z, s, 1000)
    assert r[2] == 0 and r[3] == 0                                           # at and just beside the focus: sharp (the range is 0.3..0.5)
    assert r[4] > 0 and r[5] > r[4]                                          # behind it: more the farther
    assert r[0] > r[4]                                                       # in front of it: more than the same distance behind it
    assert bokeh.radius_at(z, settings(focus=40, range=20, amount=30), 1000)[5] < r[5]       # Amount scales it
    assert bokeh.radius_at(1.0, settings(amount=0), 1000) == 0
    assert bokeh.radius_at(1.0, settings(focus=0, range=0), 1000) <= bokeh.MAX_RADIUS * 1000 + 1e-3


def test_every_shape_has_a_kernel_that_sums_to_one_and_they_differ():
    kernels = {name: bokeh.kernel(i, 12) for i, name in enumerate(bokeh.SHAPE_NAMES)}
    for name, k in kernels.items():
        assert k.dtype == np.float32 and k.shape[0] == k.shape[1] and abs(float(k.sum()) - 1.0) < 1e-4, name
        assert k.shape[0] >= 24                                              # about as wide as the radius says
    ring, disc = kernels["Ring"], kernels["Circle"]
    centre = ring.shape[0] // 2
    assert ring[centre, centre] < 0.6 * ring[centre, centre + 10] and disc[centre, centre] > 0.9 * disc[centre, centre + 10]       # a bright rim
    assert kernels["Ellipse"].shape == kernels["Ellipse"].T.shape
    ys, xs = np.nonzero(kernels["Ellipse"] > 0)
    assert (xs.max() - xs.min()) > (ys.max() - ys.min())                     # wider than tall
    assert len({k.tobytes() for k in kernels.values()}) == 6
    assert bokeh.kernel(1, 0.2).sum() == pytest.approx(1.0, abs=1e-4)        # a tiny radius still makes a kernel


def test_the_depth_bands_are_a_partition_of_unity_with_the_sharp_band_exactly_between_two_of_them():
    s = settings(focus=50, range=20)
    centres = bokeh._centres(s)
    assert 0.4 in [round(float(c), 2) for c in centres] and 0.6 in [round(float(c), 2) for c in centres]
    distance = np.linspace(0, 1, 101, dtype=np.float32)[None, :]
    total = sum(bokeh._weights(distance, centres))
    assert np.allclose(total, 1.0, atol=1e-5)
    assert np.all(bokeh.radius_at(centres, s, 1000)[(centres >= 0.4) & (centres <= 0.6)] < 1e-3)


def test_what_is_in_focus_stays_sharp_what_is_not_is_blurred_and_neither_smears_into_the_other():
    depth = depth_with_subject()
    picture = checker()
    out, cover = bokeh.render(picture, depth, settings(focus=0, range=20))
    near, far = (slice(None), slice(0, 30)), (slice(None), slice(60, 120))
    assert np.abs(out[near] - picture[near]).max() < 0.06                      # the subject is as it was (apart from the grain)
    assert out[far].std() < 0.5 * picture[far].std()                           # the background is blurred
    assert cover[near].max() < 0.2 and cover[far].mean() > 0.8
    edge = out[:, 28:30]
    assert np.abs(edge - picture[:, 28:30]).mean() < 0.12                      # no background colour crept into the subject's edge
    off, nothing = bokeh.render(picture, depth, settings(amount=0))
    assert off is picture and not nothing.any()
    shown, _ = bokeh.render(picture, depth, settings(shown=0))
    assert shown is picture


def test_a_near_object_out_of_focus_blurs_over_the_sharp_subject_behind_it():
    depth = np.full((80, 120), 120, np.uint8)
    depth[:, 50:70] = 250                                                      # a near band across the middle
    picture = checker()
    out, _ = bokeh.render(picture, depth, settings(focus=100, range=10))       # focus on the far part
    middle, side = out[:, 52:68], out[:, 0:30]
    assert middle.std() < 0.5 * picture[:, 52:68].std()                        # the near band is blurred ...
    assert np.abs(side - picture[:, 0:30]).mean() < 0.1                        # ... and the sharp far part keeps its detail (apart from the foreground's blur at its edge)


def test_a_bright_light_far_away_becomes_a_disc_in_the_chosen_shape_and_highlights_lifts_it():
    depth = np.repeat(np.linspace(200, 20, 160).astype(np.uint8)[None, :], 120, axis=0)      # near on the left, far on the right
    picture = np.full((120, 160, 3), 0.02, np.float32)
    picture[58:62, 118:122] = 1.0                                              # a small light, far away
    plain, _ = bokeh.render(picture, depth, settings(focus=0, range=0, highlights=0, amount=100))
    lifted, _ = bokeh.render(picture, depth, settings(focus=0, range=0, highlights=100, amount=100))
    assert plain.max() < 0.9 and plain[60, 120].max() < 1.0                    # spread out: much dimmer than the point
    assert lifted.max() > plain.max() + 0.1                                    # lifted before the blur: a brighter disc
    lit = lifted[..., 0] > lifted[..., 0].min() + 0.5 * (lifted[..., 0].max() - lifted[..., 0].min())
    assert lit.sum() > 30                                                      # an area, not a point
    star, _ = bokeh.render(picture, depth, settings(focus=0, range=0, highlights=100, amount=100, shape=5))
    assert not np.allclose(star, lifted, atol=0.02)                            # a different shape is a different disc


def test_a_small_picture_is_not_blurred_at_a_reduced_size_a_large_one_is_and_keeps_its_sharp_part():
    depth = depth_with_subject(160, 240)
    picture = checker(160, 240)
    s = settings(focus=0, range=20)
    out, _ = bokeh.render(picture, depth, s)
    assert out.shape == picture.shape
    big = np.kron(picture, np.ones((12, 12, 1), np.float32))                  # 1920 wide: bigger than the size the layers are blurred at
    bigger, cover = bokeh.render(big, depth, s)
    assert bigger.shape == big.shape
    assert np.abs(bigger[:, :400] - big[:, :400]).max() < 0.06                 # what is in focus is the full-size picture itself
    assert cover[:, 1200:].mean() > 0.8


def test_distance_focus_and_the_first_focus_come_from_the_depth_map():
    depth = depth_with_subject(120, 160)
    near = bokeh.focus_at(depth, 0.1, 0.5)
    far = bokeh.focus_at(depth, 0.9, 0.5)
    assert near < 20 and far > 80
    assert 0 <= bokeh.auto_focus(depth) <= 100
    assert bokeh.auto_focus(depth) > 80                                        # the middle of the picture is the far background here
    distance = bokeh.distance_of(light_depth.working_depth(depth, 120))
    assert distance.min() == 0.0 and distance.max() == 1.0


def test_normalized_clamps_repairs_and_rounds_the_shape():
    assert bokeh.normalized(None) == bokeh.DEFAULTS
    fixed = bokeh.normalized({"amount": 500, "focus": -4, "range": float("nan"), "shape": 2.6, "junk": 1})
    assert fixed["amount"] == 100 and fixed["focus"] == 0 and fixed["range"] == bokeh.DEFAULTS["range"] and fixed["shape"] == 3 and "junk" not in fixed
    assert bokeh.valid(bokeh.normalized({"amount": 5})) and not bokeh.valid({"amount": 1})
    assert bokeh.active({"amount": 10}) and not bokeh.active({"amount": 0}) and not bokeh.active({"amount": 10, "shown": 0})
    assert bokeh.masks_needed({"amount": 80}) == 0


def test_a_qimage_a_preview_frame_and_a_file_all_get_the_same_blur(tmp_path, monkeypatch):
    from PySide6.QtGui import QImage

    depth = depth_with_subject(60, 90)
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": depth)
    s = settings(focus=0, range=20)
    pixels = (checker(60, 90) * 255).astype(np.uint8)
    image = QImage(pixels.data, 90, 60, 270, QImage.Format.Format_RGB888).copy()
    blurred = bokeh.bokeh_image(image, depth, s)
    assert blurred.width() == 90 and blurred.height() == 60
    frame = bokeh.bokeh_frame(({"x": 1}, image), "photo.nef", s)
    assert frame[0] == {"x": 1} and frame[1].pixelColor(70, 30) == blurred.pixelColor(70, 30)
    assert bokeh.bokeh_frame(({"x": 1}, image), "photo.nef", {"amount": 0})[1] is image
    source = tmp_path / "in.png"
    image.save(str(source))
    target = tmp_path / "out.png"
    bokeh.bokeh_file(source, target, "photo.nef", s)
    saved = QImage(str(target))
    assert saved.width() == 90 and abs(saved.pixelColor(70, 30).red() - blurred.pixelColor(70, 30).red()) <= 6
    monkeypatch.setattr(light_depth, "load_depth", lambda path, model="small": None)
    with pytest.raises(ValueError, match="depth map is missing"):
        bokeh.bokeh_frame(({}, image), "photo.nef", s)


def test_host_effects_know_bokeh_and_the_renderer_never_gets_it(monkeypatch):
    s = settings()
    both = {"exposure": .2, "bokeh": s}
    assert "bokeh" in host_effects.KEYS and host_effects.without_host(both) == {"exposure": .2}
    out = host_effects.with_masks(both, "photo.nef")
    assert "bokeh" not in out and out["exposure"] == .2
    assert host_effects.masks_in_use(both, besides="atmosphere") == 0


def test_the_settings_file_round_trips_and_an_unusable_one_is_ignored(tmp_path):
    recipe = tmp_path / "photo.nef.cameraraw.json"
    assert bokeh.read_sidecar(recipe) is None
    s = settings(shape=4, focus=33)
    bokeh.write_sidecar(recipe, s)
    assert bokeh.sidecar_path(recipe).name == "photo.nef.photocraft.bokeh.json"
    assert bokeh.read_sidecar(recipe) == s
    bokeh.sidecar_path(recipe).write_text("not json")
    assert bokeh.read_sidecar(recipe) is None
    bokeh.sidecar_path(recipe).write_text('{"version": 1, "settings": {"amount": 500}}')
    assert bokeh.read_sidecar(recipe) is None
    bokeh.write_sidecar(recipe, s)
    bokeh.write_sidecar(recipe, None)
    assert not bokeh.sidecar_path(recipe).exists()
    from image_triage import edit_carry
    assert bokeh.SIDECAR_TAIL.casefold().startswith(edit_carry.EDIT_MARKERS)
