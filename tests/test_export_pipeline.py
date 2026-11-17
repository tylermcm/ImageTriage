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
