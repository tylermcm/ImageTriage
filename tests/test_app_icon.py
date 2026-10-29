"""The app icon: the Image Triage logo (LogoMark.qml), in every file that carries it."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICON_FILES = (
    ROOT / "image_triage" / "ui" / "assets" / "app_icon-v5",
    ROOT / "build_assets" / "icons" / "image_triage-v5",
)


def test_each_icon_has_a_1024_png_and_an_ico_with_every_size():
    for stem in ICON_FILES:
        png = Image.open(stem.with_suffix(".png"))
        assert png.size == (1024, 1024) and png.mode == "RGBA"
        sizes = Image.open(stem.with_suffix(".ico")).info["sizes"]
        assert {(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)} <= set(sizes)


def test_the_icon_is_the_ember_logo_and_not_a_tile():
    icon = Image.open(ICON_FILES[0].with_suffix(".png")).convert("RGBA")
    assert icon.getpixel((2, 2))[3] == 0                               # no tile behind the mark: the corner is transparent
    red, green, blue, alpha = icon.getpixel((512, 69))                 # the top of the ring
    assert alpha == 255 and red > 220 and 90 < green < 200 and blue < 100
    assert icon.getpixel((512, 512))[:3] == (255, 255, 255)            # the white dot at the centre


def test_nothing_still_points_at_the_old_icon_files():
    old = ("app_icon-v3", "image_triage-v3", "app_icon-v4", "image_triage-v4")
    for path in list((ROOT / "image_triage").rglob("*.py")) + list((ROOT / "image_triage").rglob("*.qml")) + [ROOT / "freeze_support.py"]:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert not any(name in text for name in old), path
    for stem in ICON_FILES:
        assert not (stem.parent / stem.name.replace("v5", "v4")).with_suffix(".ico").exists()
