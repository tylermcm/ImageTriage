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


def test_guides_solve_the_sliders_that_make_them_upright():
    from image_triage import raw_upright as ru

    h = rp.homography({"vertical": 40, "constrain": False}, W, H)

    def seen(x1, y1, x2, y2):
        a, b = rp.apply(h, x1 * W, y1 * H), rp.apply(h, x2 * W, y2 * H)
        return (a[0] / W, a[1] / H, b[0] / W, b[1] / H)

    lines = [seen(0.25, 0.1, 0.25, 0.9), seen(0.75, 0.1, 0.75, 0.9)]
    result = ru.solve(lines, W, H, "guided")
    assert result["vertical"] < -25 and abs(result["horizontal"]) < 3
    assert ru._residual(result, lines, W, H, 0) < 1e-4                 # the guides end up vertical
    assert ru.solve([], W, H, "guided") == {"vertical": 0.0, "horizontal": 0.0, "angle": 0.0}


def test_level_only_turns_the_picture():
    from image_triage import raw_upright as ru

    tilted = [(0.1, 0.45, 0.9, 0.55)]                                   # a horizon falling to the right
    result = ru.solve(tilted, W, H, "level")
    assert result["vertical"] == 0 and result["horizontal"] == 0 and result["angle"] < -3
    assert ru._residual(result, tilted, W, H, 0) < 1e-4


def test_auto_upright_finds_the_lines_of_a_keystoned_building():
    from image_triage import raw_upright as ru

    image = QImage(W, H, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 200, 200))
    painter = QPainter(image)
    for x in (120, 240, 360, 480):
        painter.fillRect(x, 0, 8, H, QColor(40, 40, 40))
    painter.fillRect(0, 300, W, 8, QColor(40, 40, 40))
    painter.end()
    leaning = rp.warp(image, {"vertical": 35, "constrain": False})
    lines = ru.find_lines(leaning)
    assert sum(ru.classify(l) == "vertical" for l in lines) >= 4 and any(ru.classify(l) == "horizontal" for l in lines)
    full = ru.solve(ru.auto_lines(leaning, "full"), W, H, "full")
    assert full["vertical"] < -25
    blank = QImage(W, H, QImage.Format.Format_RGB32)
    blank.fill(QColor(90, 90, 90))
    assert ru.find_lines(blank) == []                                      # a plain picture has none


def test_the_editor_runs_upright_and_guides(qapp):
    from image_triage.qml_shell import raw_preview
    from image_triage.qml_shell.raw_editor import RawEditor

    image = QImage(W, H, QImage.Format.Format_RGBA8888)
    image.fill(QColor(200, 200, 200))
    painter = QPainter(image)
    for x in (120, 240, 360, 480):
        painter.fillRect(x, 0, 8, H, QColor(40, 40, 40))
    painter.end()
    leaning = rp.warp(image, {"vertical": 35, "constrain": False}).convertToFormat(QImage.Format.Format_RGBA8888)
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    frame = ({"histogram": [[0] * 256] * 3, "fullSize": [W, H]}, leaning)
    raw._proxy_arrived(raw._revision, (frame, frame, 0, "sample.NEF"))
    try:
        raw.setUpright("vertical")
        crop = raw._draft["hostCrop"]
        assert crop["upright"] == "vertical" and crop["vertical"] < -25 and raw_crop.valid(crop)
        raw.setUpright("guided")
        assert raw._draft["hostCrop"]["upright"] == "guided" and raw.uprightGuides == []
        raw.setUpright("off")
        raw.setUpright("guided")
        for start, stop in ((0, 200), (420, 600)):                       # two edges that should be vertical
            top, bottom = edge_x(leaning, 20, start, stop) / W, edge_x(leaning, H - 20, start, stop) / W
            raw.addGuide(top, 20 / H, bottom, (H - 20) / H)
        raw.addGuide(0.9, 0.05, 0.9, 0.06)                                 # too short: ignored
        assert len(raw.uprightGuides) == 2 and raw._draft["hostCrop"]["vertical"] < -20
        for shown in raw.uprightGuides:
            assert abs(shown[0] - shown[2]) < 0.01                          # the guides are shown upright once solved
        raw.clearGuides()
        assert raw.uprightGuides == [] and "vertical" not in raw._draft["hostCrop"]
        raw.resetTransform()
        assert "upright" not in raw._draft.get("hostCrop", {})
    finally:
        raw.shutdown()
