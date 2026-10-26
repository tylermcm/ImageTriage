"""Draws the app icon and writes every file that carries it.

The icon is the mark in the top left of the QML shell (`TopBar.qml`): a rounded square filled with a conical gradient that runs from the
accent colour through a lavender back to the accent. It is drawn here with the same numbers (corner radius a third of the side, the
gradient centred, starting at 210 degrees, the stops at 0, 0.5 and 1), in the default dark theme's accent.

    python scripts/icon_art/render.py          # writes the files below
    python scripts/icon_art/render.py OUTDIR   # only writes the frames (icon_<px>.png) there, for a look

Each size of the .ico keeps its own render rather than being scaled down from a big one. Bump VERSION when the icon changes.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QGuiApplication, QImage, QPainter, QPainterPath

ROOT = Path(__file__).resolve().parents[2]
VERSION = "v4"
ACCENT = "#59a0f9"          # the dark theme's accent (`Theme(light=False).accent`)
LAVENDER = "#c58ae6"
ANGLE = 210.0
MARGIN = 8 / 256            # the space round the square, as a share of the side (the previous icon's)
RADIUS = 1 / 3              # TopBar.qml: 6 px of 18
FRAMES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 96, 128, 256, 1024)
ICO_SIZES = [size for size in FRAMES if size <= 256]


def draw(px: int) -> QImage:
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = px * MARGIN
    side = px - 2 * margin
    gradient = QConicalGradient(QPointF(px / 2, px / 2), ANGLE)
    gradient.setColorAt(0.0, QColor(ACCENT))
    gradient.setColorAt(0.5, QColor(LAVENDER))
    gradient.setColorAt(1.0, QColor(ACCENT))
    path = QPainterPath()
    path.addRoundedRect(QRectF(margin, margin, side, side), side * RADIUS, side * RADIUS)
    painter.fillPath(path, gradient)
    painter.end()
    return image


def to_pil(image: QImage) -> Image.Image:
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombuffer("RGBA", (image.width(), image.height()), bytes(image.constBits()), "raw", "RGBA", image.bytesPerLine(), 1).copy()


def main(argv: list[str]) -> None:
    app = QGuiApplication.instance() or QGuiApplication([])
    frames = {px: to_pil(draw(px)) for px in FRAMES}
    if argv:
        out = Path(argv[0])
        out.mkdir(parents=True, exist_ok=True)
        for px, frame in frames.items():
            frame.save(out / f"icon_{px}.png")
        print(f"wrote {len(frames)} frames to {out}")
        return
    # the largest frame leads: Pillow skips every size bigger than the image it is saving, and keeps the matching frame for each size it lists
    ico_frames = [frames[px] for px in sorted(ICO_SIZES, reverse=True)]
    for ico, png in (
        (ROOT / "image_triage" / "ui" / "assets" / f"app_icon-{VERSION}.ico", ROOT / "image_triage" / "ui" / "assets" / f"app_icon-{VERSION}.png"),
        (ROOT / "build_assets" / "icons" / f"image_triage-{VERSION}.ico", ROOT / "build_assets" / "icons" / f"image_triage-{VERSION}.png"),
    ):
        ico_frames[0].save(ico, format="ICO", sizes=[(px, px) for px in ICO_SIZES], append_images=ico_frames[1:])
        frames[1024].save(png)
        print("wrote", ico.relative_to(ROOT), "and", png.relative_to(ROOT))
    for path in (ROOT / "website" / "assets" / "image-triage-icon.png", ROOT / "scripts" / "qml_parity" / "help_icon.png"):
        frames[256].save(path)
        print("wrote", path.relative_to(ROOT))
    del app


if __name__ == "__main__":
    main(sys.argv[1:])
