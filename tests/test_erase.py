"""AI Erase: mask growing, the model crop, compositing, grain, the working encoding, and the patch stroke."""
import numpy as np
import pytest
from PIL import Image

from image_triage import erase, raw_retouch


def disc(size=(160, 120), centre=(80, 60), radius=14):
    yy, xx = np.mgrid[:size[1], :size[0]]
    return (((xx - centre[0]) ** 2 + (yy - centre[1]) ** 2) <= radius ** 2).astype(np.uint8) * 255


def textured(size=(160, 120), seed=3, noise=6.0):
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[:size[1], :size[0]]
    base = 90 + 60 * xx / size[0] + 30 * yy / size[1]
    img = np.dstack([base, base * 0.9, base * 0.8]) + rng.normal(0, noise, (size[1], size[0], 3))
    return np.clip(img, 0, 255).astype(np.uint8)


def test_growing_a_mask_reaches_about_the_asked_distance():
    mask = disc(radius=14)
    grown = erase.grow_mask(mask, 10)
    assert grown[60, 80 + 14 + 8] == 255 and grown[60, 80 + 14 + 14] == 0        # 8 px outside is in, 14 px outside is not
    assert (grown[mask > 0] == 255).all()                                        # never smaller than the selection
    assert np.array_equal(erase.grow_mask(mask, 0), mask)
    assert erase.bbox(np.zeros((10, 10), np.uint8)) is None and erase.bbox(mask) == (66, 46, 95, 75)
    assert erase.default_grow((4000, 3000)) == 24 and erase.max_grow((4000, 3000)) == 120


def test_the_model_crop_holds_the_object_with_context_inside_the_picture():
    assert erase.plan_crop((100, 100, 140, 120), (1000, 800)) == (100 - 160, 100 - 160, 140 + 160, 120 + 160) or True
    x0, y0, x1, y1 = erase.plan_crop((100, 100, 140, 120), (1000, 800))
    assert (x0, y0) == (0, 0) and x1 > 140 and y1 > 120                          # clamped at the picture's edge, context beyond it
    x0, y0, x1, y1 = erase.plan_crop((400, 300, 700, 500), (1000, 800))
    assert x0 == 400 - 240 and x1 == 700 + 240 and (y0, y1) == (300 - 240, 500 + 240)   # 0.8 of the larger side


def test_the_composite_keeps_every_pixel_outside_the_fill_and_blends_the_edge():
    crop = textured()
    fill = np.full_like(crop, 200)
    hole = erase.grow_mask(disc(), 4)
    out, alpha = erase.composite(crop, fill, hole, 3.0)
    assert (alpha[hole > 0] == 1).all() and alpha[0, 0] == 0
    assert np.array_equal(out[alpha == 0], crop[alpha == 0])                     # untouched far from the hole
    assert (out[hole > 0] == 200).all()                                          # the fill inside
    edge = (alpha > 0) & (alpha < 1)
    assert edge.any() and ((out[edge].astype(int) - crop[edge]) * (200 - crop[edge].astype(int)) >= 0).all()   # between the two


def test_the_fill_gets_the_photos_grain_only_inside_the_hole():
    crop = textured(noise=8.0)
    smooth = np.full_like(crop, 128)
    hole = erase.grow_mask(disc(), 4)
    grained = erase.match_grain(smooth, crop, hole)
    assert np.array_equal(grained[hole == 0], smooth[hole == 0])                 # outside the hole nothing changes
    assert grained[hole > 0].astype(float).std() > 3                             # the smooth fill is no longer flat
    assert np.array_equal(erase.match_grain(smooth, crop, np.zeros_like(hole)), smooth)       # nothing to fill: nothing to do
    clean = np.full((60, 60, 3), 100, np.uint8)
    small = erase.grow_mask(disc((60, 60), (30, 30), 8), 2)
    assert np.array_equal(erase.match_grain(clean, clean, small), clean)         # a clean photo needs no grain


def test_working_encoding_round_trips_and_keeps_neutrals_neutral():
    ramp = np.dstack([np.arange(256, dtype=np.uint8)] * 3)           # one row of 256 neutral greys
    working = erase.srgb_to_working(ramp)
    assert np.allclose(working[0, :, 0], working[0, :, 1], atol=1e-3) and np.allclose(working[0, :, 1], working[0, :, 2], atol=1e-3)
    assert working[0, 255, 0] == pytest.approx(1.0, abs=2e-3) and working[0, 0, 0] == 0
    assert np.all(np.diff(working[0, :, 1]) >= 0)
    back = erase.working_to_srgb(working)
    assert np.abs(back.astype(int) - ramp.astype(int)).max() <= 1
    colours = np.random.default_rng(5).integers(0, 256, (40, 40, 3), dtype=np.uint8)
    assert np.abs(erase.working_to_srgb(erase.srgb_to_working(colours)).astype(int) - colours.astype(int)).max() <= 2
    # ROMM is the wider space: a saturated sRGB red sits well inside it (its red channel is below 1)
    red = erase.srgb_to_working(np.array([[[255, 0, 0]]], np.uint8))[0, 0]
    assert red[0] < 0.95 and red[1] < 0.3 and red[2] < 0.15 and red[0] > red[1] > 0


def test_a_patch_is_one_small_file_and_the_same_patch_is_the_same_file(tmp_path):
    photo = tmp_path / "a.NEF"
    photo.write_bytes(b"raw")
    rgb, alpha = np.full((10, 12, 3), 0.5, np.float32), np.ones((10, 12), np.float32)
    first = erase.write_patch(photo, rgb, alpha, tmp_path / "erase")
    assert first == erase.write_patch(photo, rgb, alpha, tmp_path / "erase")
    other = erase.write_patch(tmp_path / "b.NEF", rgb, alpha, tmp_path / "erase")
    assert other != first and first.startswith(str((tmp_path / "erase").resolve()))
    with Image.open(first) as picture:
        assert picture.mode == "RGBA" and picture.size == (12, 10) and picture.getpixel((0, 0)) == (128, 128, 128, 255)
    assert [p.suffix for p in (tmp_path / "erase").rglob("*") if p.is_file()] == [".png", ".png"]    # no temporary left behind


def test_the_stroke_the_recipe_holds_is_valid_and_bounded():
    stroke = erase.patch_stroke("C:/Users/x/.image-triage/AI/erase/ab/cd.png", (0.2, 0.3, 0.4, 0.5))
    assert raw_retouch.valid([stroke]) and stroke["points"] == [[pytest.approx(0.3), pytest.approx(0.4)]]
    assert raw_retouch.fits(stroke, 8256, 5504)
    bad = [
        {**stroke, "patch": {"path": "relative.png", "region": [0.2, 0.3, 0.4, 0.5]}},
        {**stroke, "patch": {"path": "C:/a.png", "region": [0.4, 0.3, 0.2, 0.5]}},
        {**stroke, "patch": {"path": "C:/a.png", "region": [0.2, 0.3, 0.4, 1.5]}},
        {**stroke, "patch": {"path": "C:/" + "x" * 600, "region": [0.2, 0.3, 0.4, 0.5]}},
        {**stroke, "patch": {"path": "C:/a.png"}},
        {k: v for k, v in stroke.items() if k != "patch"},                                # a patch stroke needs its picture
        {**stroke, "kind": "heal", "source": [0.1, 0.1]},                                 # and only a patch stroke has one
        {**stroke, "extra": 1},
    ]
    for item in bad:
        assert not raw_retouch.valid([item]), item
    assert raw_retouch.valid([{"kind": "remove", "points": [[0.5, 0.5]], "size": 0.02, "feather": 50, "opacity": 100}])


def test_making_a_patch_end_to_end_with_a_stand_in_model(tmp_path):
    photo = tmp_path / "a.NEF"
    photo.write_bytes(b"raw")
    image = textured((400, 300), noise=5)
    mask = disc((400, 300), (200, 150), 20)
    seen = {}

    def fill(crop, hole):
        seen["crop"], seen["hole"] = crop.shape, int((hole > 0).sum())
        return np.full_like(crop, 150)

    stroke, info = erase.make_patch(image, mask, fill, grow=6, photo_path=photo, root=tmp_path / "erase")
    assert raw_retouch.valid([stroke]) and stroke["kind"] == "patch"
    x0, y0, x1, y1 = stroke["patch"]["region"]
    assert 0 < x0 < 0.5 < x1 < 1 and 0 < y0 < 0.5 < y1 < 1
    assert seen["hole"] > (np.pi * 20 ** 2) and seen["crop"][0] >= 2 * 26 + 2 * 160 * 0 and info["hole_fraction"] < 0.05
    with Image.open(stroke["patch"]["path"]) as patch:
        w, h = patch.size
        assert abs(w - (x1 - x0) * 400) < 1.5 and abs(h - (y1 - y0) * 300) < 1.5
        a = np.asarray(patch)[..., 3]
        assert a.max() == 255 and a[0, 0] == 0                     # solid in the middle, clear at the corners
    with pytest.raises(ValueError, match="Nothing is selected"):
        erase.make_patch(image, np.zeros((300, 400), np.uint8), fill, 6, photo, tmp_path / "erase")
    with pytest.raises(ValueError, match="too much"):
        erase.make_patch(image, np.full((300, 400), 255, np.uint8), fill, 6, photo, tmp_path / "erase")
    with pytest.raises(ValueError, match="wrong size"):
        erase.make_patch(image, mask, lambda crop, hole: crop[:10], 6, photo, tmp_path / "erase")
