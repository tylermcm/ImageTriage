"""Draws the app icon and writes every file that carries it.

The icon is the Image Triage logo (`image_triage/qml_shell/qml/LogoMark.qml`), drawn here from the same geometry in a 1024 box: an ember ring
(radius 374 outside, 314 inside, a linear gradient from #ffbb4d through #ff8a32 to #ff6a2a), six round blades turned 60 degrees apart about the
centre (each a 60 wide capsule, lighter at its outer end, leaned back 22 degrees) and a white dot. There is no tile behind it; the mark is scaled
up so the ring fills the frame.

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
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath

ROOT = Path(__file__).resolve().parents[2]
VERSION = "v5"
RING_OUTER = 374            # LogoMark.qml: the 1024 box the mark is drawn in
RING_INNER = 314
FILL = 0.47                 # the ring's outer radius as a share of the frame (a little room round it)
BLADES = 6
BLADE_LEAN = -22.0
FRAMES = (16, 20, 24, 30, 32, 36, 40, 48, 60, 64, 72, 96, 128, 256, 1024)
ICO_SIZES = [size for size in FRAMES if size <= 256]


def _ring() -> QPainterPath:
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    path.addEllipse(QPointF(512, 512), RING_OUTER, RING_OUTER)
    path.addEllipse(QPointF(512, 512), RING_INNER, RING_INNER)
    return path


def draw(px: int) -> QImage:
    image = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(px / 2, px / 2)
    painter.scale(FILL * px / RING_OUTER, FILL * px / RING_OUTER)
    painter.translate(-512, -512)
    painter.setPen(Qt.PenStyle.NoPen)
    ring = QLinearGradient(170, 860, 860, 170)
    ring.setColorAt(0.0, QColor("#ffbb4d"))
    ring.setColorAt(0.55, QColor("#ff8a32"))
    ring.setColorAt(1.0, QColor("#ff6a2a"))
    painter.fillPath(_ring(), ring)
    for index in range(BLADES):
        painter.save()
        painter.translate(512, 512)
        painter.rotate(index * 360 / BLADES)
        # the blade runs from (170, 510) to (462, 392) before it is turned: a capsule leaned about its left end
        painter.translate(-342, -2)
        painter.rotate(BLADE_LEAN)
        painter.translate(342, 2)
        blade = QLinearGradient(-372, 0, 3, 0)
        blade.setColorAt(0.0, QColor("#ffc24d"))
        blade.setColorAt(1.0, QColor("#ff6a2a"))
        painter.setBrush(blade)
        painter.drawRoundedRect(QRectF(-372, -32, 375, 60), 30, 30)
        painter.restore()
    painter.setBrush(QColor("white"))
    painter.drawEllipse(QPointF(512, 512), 36, 36)
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
