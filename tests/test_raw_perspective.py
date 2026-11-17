"""Crop > Transform: the perspective homography, Constrain crop, warping, mapping points back, and the editor's controls."""
import math

import pytest
from PySide6.QtCore import QCoreApplication, QSize
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter

from image_triage import raw_crop, raw_perspective as rp


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


W, H = 600, 400


def corners(crop):
    h = rp.homography({**crop, "constrain": False}, W, H)
    return [rp.apply(h, x, y) for x, y in ((0, 0), (W, 0), (0, H), (W, H))]


def test_nothing_set_is_the_identity():
    assert rp.homography({}, W, H) == [[1, 0, 0], [0, 1, 0], [0, 0, 1]] and not rp.active({})
    assert rp.warped_to_recorded(0.3, 0.6, {}, (W, H)) == (0.3, 0.6)


def test_each_slider_does_what_it_says():
    tl, tr, bl, br = corners({"vertical": -50})
    assert tr[0] - tl[0] > br[0] - bl[0]                     # negative Vertical widens the top (buildings leaning back)
    tl, tr, bl, br = corners({"horizontal": 50})
    assert br[1] - tr[1] > bl[1] - tl[1]                     # positive Horizontal makes the right side taller
    tl, tr, bl, br = corners({"aspect": 50})
    assert tr[0] - tl[0] > W and bl[1] - tl[1] < H
    tl, tr, bl, br = corners({"scale": 120})
    assert tr[0] - tl[0] == pytest.approx(W * 1.2)
    tl, _, _, _ = corners({"offsetX": 50})
    assert tl[0] == pytest.approx(W / 4)


def test_sliders_follow_what_is_shown_after_a_quarter_turn():
    h = rp.homography({"vertical": -50, "rotate": 90, "constrain": False}, W, H)
    tl, tr, bl, br = [rp.apply(h, x, y) for x, y in ((0, 0), (W, 0), (0, H), (W, H))]
    assert abs((bl[1] - tl[1]) - (br[1] - tr[1])) > 40         # on the recorded frame it is a sideways keystone


def test_constrain_crop_leaves_no_empty_corner():
    for crop in ({"vertical": -60}, {"horizontal": 70, "aspect": 30}, {"offsetX": 40, "scale": 80}):
        h = rp.homography({**crop, "constrain": True}, W, H)
        assert rp._covers(h, W, H)
        assert not rp._covers(rp.homography({**crop, "constrain": False}, W, H), W, H)


def test_points_map_back_through_the_warp():
    crop = {"vertical": 30, "horizontal": -20, "aspect": 10, "constrain": True}
    h = rp.homography(crop, W, H)
    x, y = rp.apply(h, 150, 280)
    assert rp.warped_to_recorded(x / W, y / H, crop, (W, H)) == pytest.approx((150 / W, 280 / H), abs=1e-6)


def building(lean: float):
    """Two dark vertical edges on white, then keystoned as a camera tilted up would see them."""
    image = QImage(W, H, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    painter = QPainter(image)
    painter.fillRect(150, 0, 6, H, QColor("black"))
    painter.fillRect(450, 0, 6, H, QColor("black"))
    painter.end()
    return rp.warp(image, {"vertical": lean, "constrain": False})


def edge_x(image, y, start, stop):
    return next(x for x in range(start, stop) if image.pixelColor(x, y).red() < 100)


def test_a_keystoned_building_becomes_upright():
    leaning = building(40)                                      # verticals converge
    top, bottom = edge_x(leaning, 20, 0, 300), edge_x(leaning, H - 20, 0, 300)
    assert abs(top - bottom) > 10
    fixed = rp.warp(leaning, {"vertical": -40, "constrain": False})
    top, bottom = edge_x(fixed, 60, 0, 300), edge_x(fixed, H - 60, 0, 300)
    assert abs(top - bottom) <= 2


def test_sixteen_bit_pictures_stay_sixteen_bit():
    deep = QImage(64, 48, QImage.Format.Format_RGBA64)
    deep.fill(QColor(30, 60, 90))
    assert rp.warp(deep, {"vertical": 20}).format() == QImage.Format.Format_RGBA64


def test_the_crop_file_applies_the_warp_before_the_rectangle(tmp_path):
    leaning = building(40)
    source = tmp_path / "render.png"
    leaning.save(str(source))
    crop = {**raw_crop.DEFAULT_CROP, "vertical": -40.0, "constrain": False, "x": 0.0, "y": 0.0, "w": 0.5, "h": 1.0}
    assert raw_crop.valid(crop) and raw_crop.active(crop) and raw_crop.transformed(crop)
    width, height, *_ = raw_crop.crop_file(source, tmp_path / "out.png", crop)
    out = QImage(str(tmp_path / "out.png"))
    assert (width, height) == (300, 400)
    assert abs(edge_x(out, 60, 0, 300) - edge_x(out, H - 60, 0, 300)) <= 2
    assert not raw_crop.valid({**crop, "vertical": 150.0}) and not raw_crop.valid({**crop, "constrain": 1})


def test_the_address_round_trips():
    crop = {"vertical": -12.5, "aspect": 3.0, "constrain": False, "rotate": 90, "flipH": True}
    back = rp.from_query(rp.query(crop).removeprefix("persp="))
    assert back["vertical"] == -12.5 and back["aspect"] == 3.0 and back["rotate"] == 90 and back["flipH"] and not back["constrain"]
    assert rp.query({}) == "" and rp.from_query("junk") is None


def test_the_editor_sets_and_resets_the_transform(qapp):
    from image_triage.qml_shell.raw_editor import RawEditor

    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    image = QImage(80, 60, QImage.Format.Format_RGBA8888)
    image.fill(QColor("gray"))
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [4000, 3000]}, image)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    try:
        raw.setTransform("vertical", -30)
        raw.setTransform("constrain", 0)
        assert raw._draft["hostCrop"]["vertical"] == -30 and raw._draft["hostCrop"]["constrain"] is False
        assert "persp=" in raw.preview
        raw.setSourceView(True)
        assert "persp=" not in raw.preview                       # masks are drawn on the picture as recorded
        raw.setSourceView(False)
        raw.setCrop(0.1, 0.1, 0.5, 0.5)
        raw.resetCrop()
        assert raw._draft["hostCrop"]["vertical"] == -30          # Reset crop keeps the transform
        raw.resetTransform()
        assert "vertical" not in raw._draft.get("hostCrop", {}) and "persp=" not in raw.preview
        raw.undo()
        assert raw._draft["hostCrop"]["vertical"] == -30
    finally:
        raw.shutdown()
