"""Batch Export's pipeline and presets: sizes, colour profiles, metadata, the preview matching the file, and preset storage."""
import io

import pytest
from PIL import Image
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QColor, QColorSpace, QGuiApplication, QImage

from image_triage import export_pipeline as ep
from image_triage.edited_export import bit_depth
from image_triage.export_presets import BUILTIN_NAMES, PresetStore


@pytest.fixture(scope="module", autouse=True)
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


def picture(w=400, h=300, color=(200, 40, 40)):
    image = QImage(w, h, QImage.Format.Format_RGB32)
    image.fill(QColor(*color))
    return image


@pytest.mark.parametrize("values, size", [
    ({"sizeMode": "original"}, (4000, 3000)),
    ({"sizeMode": "long", "long": "2048"}, (2048, 1536)),
    ({"sizeMode": "short", "short": "1080"}, (1440, 1080)),
    ({"sizeMode": "fit", "fitW": "1080", "fitH": "1350"}, (1080, 810)),
    ({"sizeMode": "mp", "mp": "3"}, (2000, 1500)),
    ({"sizeMode": "pct", "pct": "25"}, (1000, 750)),
    ({"sizeMode": "long", "long": "8000"}, (4000, 3000)),                       # not enlarged
    ({"sizeMode": "long", "long": "8000", "noEnlarge": False}, (8000, 6000)),
])
def test_output_sizes(values, size):
    assert ep.output_size(4000, 3000, ep.normalized(values)) == size


def test_bad_settings_are_named():
    assert ep.size_problem(ep.normalized({"sizeMode": "pct", "pct": "abc"})) == "Enter a size above zero."
    assert ep.size_problem(ep.normalized({"quality": "0"})) == "Quality goes from 1 to 100."
    assert ep.size_problem(ep.normalized({"format": "png", "quality": "0"})) == ""     # PNG has no quality
    assert ep.normalized({"format": "gif", "profile": "cmyk", "junk": 1})["format"] == "jpg"


def test_the_profile_is_converted_and_embedded():
    values = ep.normalized({"profile": "adobergb"})
    data = ep.encode(ep.prepare(picture(), None, values), values, None)
    with Image.open(io.BytesIO(data)) as out:
        icc = out.info["icc_profile"]
        red = out.getpixel((10, 10))[0]
    assert QColorSpace.fromIccProfile(icc).description() == "Adobe RGB"
    assert red < 190                                         # the same red is a smaller number in the wider space


def test_sixteen_bit_tiff_keeps_its_depth_and_profile():
    values = ep.normalized({"format": "tif", "depth": "16", "profile": "prophoto"})
    out = QImage()
    out.loadFromData(ep.encode(ep.prepare(picture(), None, values), values, None))
    assert bit_depth(out) == 16 and out.colorSpace().description() == "ProPhoto RGB"


def exif_with_gps():
    exif = Image.Exif()
    exif[0x013B] = "Ann Author"
    exif[0x8298] = "(c) Ann"
    exif[0x010F] = "Camera Co"
    exif[0x0112] = 1
    exif.get_ifd(0x8825)[2] = (51.0, 30.0, 0.0)
    return exif.tobytes()


def tags(data):
    exif = Image.Exif()
    exif.load(data)
    return exif


def test_metadata_choices():
    raw = exif_with_gps()
    assert ep.filtered_exif(raw, "all") == raw
    assert ep.filtered_exif(raw, "none") is None
    no_gps = tags(ep.filtered_exif(raw, "nogps"))
    assert 0x8825 not in no_gps and no_gps[0x013B] == "Ann Author"
    only = tags(ep.filtered_exif(raw, "copyright"))
    assert set(only) == {0x013B, 0x8298, 0x010F}


def test_the_preview_is_the_file_made_smaller():
    image = picture(1200, 800, (30, 160, 90))
    values = ep.normalized({"sizeMode": "long", "long": "600", "profile": "p3", "format": "png"})
    preview = ep.make_preview(image, None, values, side=600)
    assert (preview.width, preview.height) == (600, 400) and preview.after.size().width() == 600
    written = QImage()
    written.loadFromData(ep.encode(ep.prepare(image, None, values), values, None))
    shown = written.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    a, b = preview.after.pixelColor(300, 200), shown.pixelColor(300, 200)
    assert max(abs(a.red() - b.red()), abs(a.green() - b.green()), abs(a.blue() - b.blue())) <= 2
    assert preview.estimated_bytes > 0


def test_exported_file_is_written_atomically(tmp_path):
    source = tmp_path / "a.jpg"
    Image.new("RGB", (300, 200), (10, 20, 30)).save(source)
    plan = ep.build_plan([ep.ExportSource(str(source), "a.jpg")], {"sizeMode": "long", "long": "150", "format": "webp"})
    assert plan.can_apply and plan.executable[0].target_path == str(tmp_path / "Export" / "a.webp")
    outcome = ep.apply_plan(plan, loader=lambda p: (QImage(p), None, None))
    assert list(outcome) == [str(tmp_path / "Export" / "a.webp")] and not outcome.failed
    with Image.open(outcome[0]) as out:
        assert out.size == (150, 100)
    assert [p.name for p in (tmp_path / "Export").iterdir()] == ["a.webp"]


def test_a_failing_photo_is_reported_and_the_rest_continue(tmp_path):
    sources = [ep.ExportSource(str(tmp_path / n), n) for n in ("a.jpg", "b.jpg")]

    def loader(path):
        if path.endswith("a.jpg"):
            raise OSError("cannot read")
        return picture(), None, None

    outcome = ep.apply_plan(ep.build_plan(sources, {}), loader=loader)
    assert len(outcome) == 1 and outcome.failed[0][1] == "cannot read"


def test_presets_store(tmp_path):
    store = PresetStore(tmp_path / "presets.json")
    assert {p["name"] for p in store.presets()} == BUILTIN_NAMES
    assert store.save("Proofs", {"format": "png", "dest": "choose", "folder": "/somewhere/else"}) == ""
    saved = store.get("Proofs")
    assert saved["format"] == "png" and saved["dest"] == "subfolder" and "folder" not in saved   # no machine-specific folder
    assert store.save("Web 2048 px", {}) == "That name belongs to a built-in preset."
    assert store.save("  ", {}) == "Give the preset a name."
    assert store.delete("Proofs") and store.get("Proofs") is None
    (tmp_path / "presets.json").write_text("not json")
    assert {p["name"] for p in store.presets()} == BUILTIN_NAMES                                    # a damaged file is ignored


def edge_picture():
    image = QImage(200, 120, QImage.Format.Format_RGB32)
    image.fill(QColor(60, 60, 60))
    for y in range(120):
        for x in range(100, 200):
            image.setPixelColor(x, y, QColor(190, 190, 190))
    return image


def contrast_at_edge(image):
    return image.pixelColor(100, 60).red() - image.pixelColor(99, 60).red()


def test_sharpening_raises_edge_contrast_by_medium_and_amount():
    image = edge_picture()
    plain = contrast_at_edge(ep.prepare(image, None, ep.normalized({})))
    screen = contrast_at_edge(ep.prepare(image, None, ep.normalized({"sharpen": "screen"})))
    high = contrast_at_edge(ep.prepare(image, None, ep.normalized({"sharpen": "matte", "sharpenAmount": "high"})))
    assert plain < screen < high
    flat = picture(50, 50, (120, 120, 120))
    out = ep.prepare(flat, None, ep.normalized({"sharpen": "matte", "sharpenAmount": "high"}))
    assert out.pixelColor(25, 25).red() == 120                       # flat areas (and their noise) are left alone


def test_sixteen_bit_pictures_stay_sixteen_bit_when_sharpened():
    deep = edge_picture().convertToFormat(QImage.Format.Format_RGBA64)
    assert bit_depth(ep.sharpened(deep, ep.normalized({"sharpen": "glossy"}))) == 16


def test_reduced_previews_leave_sharpening_to_the_100_percent_view():
    image = edge_picture()
    values = ep.normalized({"sharpen": "matte", "sharpenAmount": "high"})
    small = ep.prepare(image, None, values, size=(100, 60))
    assert small.pixelColor(10, 30).red() == 60
    preview = ep.make_preview(image, None, values, side=100)
    assert preview.detail.size().width() == 200 and contrast_at_edge(preview.detail) == contrast_at_edge(ep.prepare(image, None, values))


def test_text_watermark_lands_in_its_corner_at_its_opacity():
    image = picture(400, 300, (0, 0, 0))
    values = ep.normalized({"wm": "text", "wmText": "WWWW", "wmPos": "br", "wmSize": "10", "wmOpacity": "100"})
    out = ep.prepare(image, None, values)
    corner = [out.pixelColor(x, y).red() for x in range(250, 400) for y in range(240, 300)]
    other = [out.pixelColor(x, y).red() for x in range(0, 150) for y in range(0, 60)]
    assert max(corner) > 200 and max(other) == 0
    faint = ep.prepare(image, None, {**values, "wmOpacity": "30"})
    assert max(faint.pixelColor(x, y).red() for x in range(250, 400) for y in range(240, 300)) < 120


def test_picture_watermark_is_sized_by_the_short_edge(tmp_path):
    mark = QImage(100, 50, QImage.Format.Format_ARGB32)
    mark.fill(QColor(255, 255, 255))
    mark.save(str(tmp_path / "logo.png"))
    image = picture(400, 200, (0, 0, 0))
    values = ep.normalized({"wm": "image", "wmImage": str(tmp_path / "logo.png"), "wmPos": "tl", "wmSize": "20",
                            "wmInset": "0", "wmOpacity": "100"})
    out = ep.prepare(image, None, values)
    assert out.pixelColor(70, 30).red() == 255 and out.pixelColor(90, 30).red() == 0     # 40 px high, so 80 px wide
    assert ep.size_problem(ep.normalized({"wm": "image", "wmImage": str(tmp_path / "none.png")})) == "Choose the watermark picture."
    assert ep.size_problem(ep.normalized({"wm": "text"})) == "Type the watermark's text."


def narrow_profile(tmp_path, white=(0.3127, 0.3290)):
    """A small-gamut RGB profile standing in for a printer and paper (a warmer white stands in for the paper)."""
    from PySide6.QtCore import QPointF

    space = QColorSpace(QPointF(*white), QPointF(0.55, 0.36), QPointF(0.33, 0.50), QPointF(0.20, 0.17),
                        QColorSpace.TransferFunction.Gamma, 2.2)
    path = tmp_path / "printer.icc"
    path.write_bytes(bytes(space.iccProfile()))
    return str(path)


def test_an_icc_file_is_the_output_profile_with_the_chosen_intent(tmp_path):
    values = ep.normalized({"profile": "file", "profileFile": narrow_profile(tmp_path), "intent": "relative"})
    assert ep.size_problem(values) == ""
    data = ep.encode(ep.prepare(picture(color=(255, 0, 0)), None, values), values, None)
    with Image.open(io.BytesIO(data)) as out:
        assert out.info["icc_profile"] == open(values["profileFile"], "rb").read()
    assert ep.size_problem(ep.normalized({"profile": "file", "profileFile": str(tmp_path / "x.icc")})) == "Choose an ICC profile file."
    (tmp_path / "bad.icc").write_bytes(b"nope")
    assert ep.size_problem(ep.normalized({"profile": "file", "profileFile": str(tmp_path / "bad.icc")})) == "That file is not an ICC profile."


def test_gamut_warning_marks_only_colours_the_output_cannot_hold(tmp_path):
    from image_triage import soft_proof

    image = QImage(100, 50, QImage.Format.Format_RGB32)
    image.fill(QColor(128, 128, 128))
    for x in range(50, 100):
        for y in range(50):
            image.setPixelColor(x, y, QColor(255, 0, 0))
    image.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    mask = soft_proof.gamut_mask(image, open(narrow_profile(tmp_path), "rb").read())
    assert not mask[:, :50].any() and mask[:, 50:].all()
    values = ep.normalized({"profile": "file", "profileFile": narrow_profile(tmp_path), "proofGamut": True})
    preview = ep.make_preview(image, None, values)
    assert preview.after.pixelColor(75, 25) == QColor(*soft_proof.GAMUT_COLOR) and preview.after.pixelColor(25, 25) != QColor(*soft_proof.GAMUT_COLOR)


def test_paper_simulation_shows_the_papers_white(tmp_path):
    warm = narrow_profile(tmp_path, white=(0.3457, 0.3585))          # a D50, warmer white
    image = picture(100, 50, (255, 255, 255))
    plain = ep.make_preview(image, None, ep.normalized({"profile": "file", "profileFile": warm}))
    paper = ep.make_preview(image, None, ep.normalized({"profile": "file", "profileFile": warm, "proofPaper": True}))
    white, shown = plain.after.pixelColor(50, 25), paper.after.pixelColor(50, 25)
    assert (white.red(), white.green(), white.blue()) == (255, 255, 255)
    assert shown.blue() < shown.red() - 10                          # the paper's warm white shows
