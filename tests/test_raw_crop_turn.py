"""Straighten, rotate and flip after the crop: the pixels, the sizes and the point mappings agree with each other."""
import itertools

import pytest
from PySide6.QtGui import QColor, QImage

from image_triage import raw_crop
from image_triage.raw_crop import DEFAULT_CROP

W, H = 60, 40


def picture():
    """A grey frame with one distinct pixel block near a corner, so a turn can be read off the result."""
    image = QImage(W, H, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 90, 90))
    for x in range(8, 12):
        for y in range(4, 8):
            image.setPixelColor(x, y, QColor(255, 0, 0))
    return image


def red_centre(image):
    points = [(x, y) for x in range(image.width()) for y in range(image.height())
              if image.pixelColor(x, y).red() > 200 and image.pixelColor(x, y).green() < 80]
    assert points, "the marker left the picture"
    return (sum(p[0] for p in points) / len(points) + 0.5) / image.width(), (sum(p[1] for p in points) / len(points) + 0.5) / image.height()


def crop(**changes):
    return {**DEFAULT_CROP, **changes}


def test_a_recipe_written_before_turning_existed_is_valid_and_unchanged():
    old = {"x": 0.1, "y": 0.1, "w": 0.5, "h": 0.5, "ratio": "Free"}
    assert raw_crop.valid(old) and raw_crop.active(old) and raw_crop.framed(old) and not raw_crop.turned(old)
    assert not raw_crop.active({"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "ratio": "Original"})
    assert raw_crop.turn(picture(), old) == picture()                     # nothing to turn: the same pixels
    for bad in ({"rotate": 45}, {"rotate": 90.0}, {"flipH": 1}, {"angle": 46}, {"angle": float("nan")}, {"tilt": 1}):
        assert not raw_crop.valid(crop(**bad)), bad
    assert raw_crop.active(crop(rotate=90)) and raw_crop.active(crop(flipV=True)) and raw_crop.active(crop(angle=-3))


@pytest.mark.parametrize("rotate, flip_h, flip_v", list(itertools.product((0, 90, 180, 270), (False, True), (False, True))))
def test_a_point_of_the_turned_picture_maps_back_to_the_same_pixel(rotate, flip_h, flip_v):
    settings = crop(rotate=rotate, flipH=flip_h, flipV=flip_v)
    turned = raw_crop.turn(picture(), settings)
    assert (turned.width(), turned.height()) == raw_crop.turned_size(settings, W, H) == ((H, W) if rotate in (90, 270) else (W, H))
    u, v = red_centre(turned)
    back = raw_crop.view_to_frame(u, v, settings)
    assert back == pytest.approx(red_centre(picture()), abs=0.02)
    # ... and the crop tool's rectangle goes there and back
    drawn = raw_crop.rect_to_view(crop(x=0.1, y=0.2, w=0.3, h=0.5, rotate=rotate, flipH=flip_h, flipV=flip_v))
    assert raw_crop.rect_from_view(*drawn, settings) == pytest.approx((0.1, 0.2, 0.3, 0.5))


@pytest.mark.parametrize("angle", (-20, -3.5, 7, 30))
def test_straightening_keeps_the_shape_and_maps_points_back(angle):
    image = QImage(300, 200, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 90, 90))
    for x in range(170, 180):
        for y in range(80, 90):
            image.setPixelColor(x, y, QColor(255, 0, 0))                  # near the middle, so it stays inside
    settings = crop(angle=angle)
    turned = raw_crop.turn(image, settings)
    assert (turned.width(), turned.height()) == raw_crop.turned_size(settings, 300, 200)
    assert turned.width() < 300 and turned.width() / turned.height() == pytest.approx(1.5, abs=0.02)
    assert turned.format() == image.format()                              # no alpha channel is added
    for x, y in ((0, 0), (turned.width() - 1, 0), (0, turned.height() - 1), (turned.width() - 1, turned.height() - 1)):
        assert turned.pixelColor(x, y).red() in range(86, 95)             # the corners are picture, not empty or blended with empty
    u, v = red_centre(turned)
    assert raw_crop.view_to_frame(u, v, settings, shape=(300, 200)) == pytest.approx((175 / 300, 85 / 200), abs=0.02)


def test_everything_together_maps_a_click_back_to_the_recorded_picture():
    image = QImage(400, 300, QImage.Format.Format_RGB32)
    image.fill(QColor(90, 90, 90))
    for x in range(196, 206):
        for y in range(146, 156):
            image.setPixelColor(x, y, QColor(255, 0, 0))
    settings = crop(x=0.2, y=0.1, w=0.6, h=0.8, angle=6, rotate=270, flipH=True)
    cut = image.copy(*raw_crop.bounds(settings, 400, 300))
    result = raw_crop.turn(cut, settings)
    u, v = red_centre(result)
    assert raw_crop.view_to_source(u, v, settings, shape=(400, 300)) == pytest.approx((201 / 400, 151 / 300), abs=0.02)


def test_rotating_and_flipping_what_is_shown():
    shown = crop()
    for _ in range(4):
        shown = raw_crop.rotated(shown, 1)
    assert shown == crop()                                                 # four quarter-turns come back
    assert raw_crop.rotated(crop(), -1)["rotate"] == 270
    flipped = raw_crop.flipped(crop(), True)
    assert flipped["flipH"] and not flipped["flipV"]
    # Mirror, then turn: the mirror is still across what the person now sees as the other axis.
    after = raw_crop.rotated(flipped, 1)
    direct = raw_crop.turn(raw_crop.turn(picture(), flipped), crop(rotate=90))
    assert raw_crop.turn(picture(), after) == direct
    assert raw_crop.flipped(after, False) != after and raw_crop.flipped(raw_crop.flipped(after, False), False) == after
    assert raw_crop.shown_angle(crop(angle=5)) == 5 and raw_crop.shown_angle(crop(angle=5, flipH=True)) == -5
    assert raw_crop.shown_angle(crop(angle=5, flipH=True, flipV=True)) == 5


def test_the_published_picture_is_cropped_then_turned(tmp_path):
    source, target = tmp_path / "render.png", tmp_path / "out.png"
    picture().save(str(source), "PNG")
    width, height, depth, _icc = raw_crop.crop_file(source, target, crop(x=0.0, y=0.0, w=0.5, h=1.0, rotate=90))
    assert (width, height) == (H, W // 2)                                  # the left half, on its side
    written = QImage(str(target))
    assert (written.width(), written.height()) == (H, W // 2) and red_centre(written)[0] > 0.5
    raw_crop.crop_file(source, target, {"x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0, "ratio": "Free"})      # an old recipe: as before
    assert (QImage(str(target)).width(), QImage(str(target)).height()) == (W // 2, H)


def test_sixteen_bit_pictures_stay_sixteen_bit_through_a_turn():
    image = QImage(120, 80, QImage.Format.Format_RGBX64)
    image.fill(QColor(90, 90, 90))
    for settings in (crop(angle=4), crop(rotate=90), crop(flipH=True), crop(angle=-9, rotate=180, flipV=True)):
        assert raw_crop.turn(image, settings).depth() == 64, settings
