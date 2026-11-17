"""Soft proofing and ICC-file conversion for exports, with LittleCMS (Pillow's ImageCms).

- ``convert``: a picture from its profile into an output profile with a rendering intent and black point compensation. Used for
  output profiles given as ICC files (printer and paper profiles are usually lookup tables, which Qt's own conversion does not do).
- ``to_display``: an exported picture as it will look, on an sRGB screen. With *paper and ink* it is shown with the absolute
  colorimetric intent, so the paper's white and the ink's black show; without, relative colorimetric (white is white).
- ``gamut_mask``: which pixels of the source the output profile cannot hold (they change by more than a small step when sent
  there and back), drawn by ``with_gamut_warning`` in a flat warning colour.

Only RGB profiles are taken: CMYK output is not something these exports write.
"""
from __future__ import annotations

import io
from pathlib import Path

from PySide6.QtGui import QColorSpace, QImage

GAMUT_COLOR = (255, 40, 200)
GAMUT_STEP = 6 / 255          # a pixel that moves more than this on the round trip is out of gamut
INTENTS = {"perceptual": 0, "relative": 1, "saturation": 2, "absolute": 3}


def srgb_bytes() -> bytes:
    return bytes(QColorSpace(QColorSpace.NamedColorSpace.SRgb).iccProfile())


def profile_problem(path: str) -> str:
    """Why the ICC file at ``path`` cannot be an output or proof profile, or ""."""
    from PIL import ImageCms

    try:
        data = Path(path).read_bytes()
    except OSError:
        return "Choose an ICC profile file."
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(data))
    except Exception:
        return "That file is not an ICC profile."
    space = (profile.profile.xcolor_space or "").strip()
    if space != "RGB":
        return f"Only RGB profiles can be used (this one is {space or 'unknown'})."
    return ""


def profile_name(path: str) -> str:
    from PIL import ImageCms

    try:
        return ImageCms.getProfileDescription(ImageCms.ImageCmsProfile(str(path))).strip() or Path(path).stem
    except Exception:
        return Path(path).stem


def _cms(data: bytes):
    from PIL import ImageCms

    return ImageCms.ImageCmsProfile(io.BytesIO(data))


def to_pil(image: QImage):
    """The picture's 8-bit RGB values (in whatever profile it is in)."""
    from PIL import Image

    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    return Image.frombuffer("RGB", (rgb.width(), rgb.height()), bytes(rgb.constBits()), "raw", "RGB", rgb.bytesPerLine(), 1).copy()


def from_pil(picture, icc: bytes | None) -> QImage:
    data = picture.convert("RGB").tobytes()
    image = QImage(data, picture.width, picture.height, picture.width * 3, QImage.Format.Format_RGB888).copy()
    if icc:
        space = QColorSpace.fromIccProfile(icc)
        if space.isValid():
            image.setColorSpace(space)
    return image


def _icc_of(image: QImage) -> bytes:
    return bytes(image.colorSpace().iccProfile()) if image.colorSpace().isValid() else srgb_bytes()


def convert(image: QImage, target_icc: bytes, intent: str = "perceptual", bpc: bool = True) -> QImage:
    """``image`` (8 bits per channel after this) in ``target_icc``. The result keeps the target's colour space when Qt can read
    the profile; either way the profile to embed is ``target_icc``."""
    from PIL import ImageCms

    flags = ImageCms.Flags.BLACKPOINTCOMPENSATION if bpc else ImageCms.Flags.NONE
    transform = ImageCms.buildTransform(_cms(_icc_of(image)), _cms(target_icc), "RGB", "RGB",
                                        renderingIntent=ImageCms.Intent(INTENTS[intent]), flags=flags)
    return from_pil(ImageCms.applyTransform(to_pil(image), transform), target_icc)


def to_display(image: QImage, icc: bytes, paper: bool) -> QImage:
    """An exported picture (in profile ``icc``) as it looks on an sRGB screen; ``paper`` simulates paper white and ink black."""
    from PIL import ImageCms

    intent = ImageCms.Intent.ABSOLUTE_COLORIMETRIC if paper else ImageCms.Intent.RELATIVE_COLORIMETRIC
    transform = ImageCms.buildTransform(_cms(icc), _cms(srgb_bytes()), "RGB", "RGB", renderingIntent=intent)
    return from_pil(ImageCms.applyTransform(to_pil(image), transform), srgb_bytes())


def gamut_mask(source: QImage, target_icc: bytes):
    """A boolean array, True where the output profile cannot hold the source's colour."""
    import numpy as np
    from PIL import ImageCms

    src_icc = _icc_of(source)
    there = ImageCms.buildTransform(_cms(src_icc), _cms(target_icc), "RGB", "RGB", renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
    back = ImageCms.buildTransform(_cms(target_icc), _cms(src_icc), "RGB", "RGB", renderingIntent=ImageCms.Intent.RELATIVE_COLORIMETRIC)
    original = to_pil(source)
    round_trip = ImageCms.applyTransform(ImageCms.applyTransform(original, there), back)
    a = np.asarray(original, dtype=np.float32) / 255
    b = np.asarray(round_trip, dtype=np.float32) / 255
    return np.abs(a - b).max(axis=2) > GAMUT_STEP


def with_gamut_warning(display: QImage, mask) -> QImage:
    """``display`` with the out-of-gamut pixels painted in the warning colour (``mask`` is resized to fit if needed)."""
    import numpy as np
    from PIL import Image

    picture = to_pil(display)
    m = np.asarray(mask, dtype=bool)
    if m.shape != (picture.height, picture.width):
        m = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((picture.width, picture.height))) > 127
    pixels = np.array(picture)
    pixels[m] = GAMUT_COLOR
    return from_pil(Image.fromarray(pixels), srgb_bytes())


# ---- the RAW editor's Soft Proof view -------------------------------------------------------------------------------------------
NAMED = {"srgb": QColorSpace.NamedColorSpace.SRgb, "p3": QColorSpace.NamedColorSpace.DisplayP3,
         "adobergb": QColorSpace.NamedColorSpace.AdobeRgb, "prophoto": QColorSpace.NamedColorSpace.ProPhotoRgb}


def proof_icc(settings: dict) -> bytes:
    """The ICC bytes of a Soft Proof setting's profile (a named space, or an ICC file)."""
    if settings.get("profile") == "file":
        return Path(settings.get("profileFile", "")).read_bytes()
    return bytes(QColorSpace(NAMED.get(settings.get("profile"), QColorSpace.NamedColorSpace.SRgb)).iccProfile())


def proofed(frame: QImage, settings: dict) -> QImage:
    """A displayed editor frame (sRGB) as it will look in the proof profile: converted there with the intent and black point
    compensation, back to the screen (paper and ink simulated when asked), with the gamut warning when asked."""
    if frame.isNull():
        return frame
    source = QImage(frame)
    if not source.colorSpace().isValid():
        source.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    icc = proof_icc(settings)
    out = convert(source, icc, settings.get("intent", "perceptual"), bool(settings.get("bpc", True)))
    display = to_display(out, icc, bool(settings.get("paper", False)))
    if settings.get("gamut"):
        display = with_gamut_warning(display, gamut_mask(source, icc))
    return display
