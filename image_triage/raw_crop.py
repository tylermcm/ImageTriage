"""Non-destructive host crop, applied after PhotoCraft's RAW development."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import time
from uuid import uuid4

from PySide6.QtCore import QIODevice, QSaveFile, Qt
from PySide6.QtGui import QColorSpace, QImage, QImageReader

# The rectangle is in the developed picture as the camera recorded it (0..1). The turn comes after it, in this order: straighten
# by ``angle`` degrees (clockwise, keeping the largest upright rectangle of the same shape), a quarter-turn ``rotate`` (clockwise
# degrees), then the two mirrors as the person sees the picture. A recipe written before these existed has none of them.
DEFAULT_CROP = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "ratio": "Original", "rotate": 0, "flipH": False, "flipV": False, "angle": 0.0}
RATIOS = ("Original", "Free", "1:1", "4:5", "3:2", "16:9")
MAX_ANGLE = 45.0


def valid(crop):
    if not isinstance(crop, dict) or set(crop) - set(DEFAULT_CROP):
        return False
    c = {**DEFAULT_CROP, **crop}
    if c["ratio"] not in RATIOS:
        return False
    if any(type(c[k]) not in (int, float) or not math.isfinite(c[k]) for k in ("x", "y", "w", "h", "angle")):
        return False
    if type(c["rotate"]) is not int or c["rotate"] not in (0, 90, 180, 270):
        return False
    if type(c["flipH"]) is not bool or type(c["flipV"]) is not bool or abs(c["angle"]) > MAX_ANGLE:
        return False
    return (0 <= c["x"] < 1 and 0 <= c["y"] < 1 and 0 < c["w"] <= 1 and 0 < c["h"] <= 1
            and c["x"] + c["w"] <= 1.000001 and c["y"] + c["h"] <= 1.000001)


def framed(crop):
    """Whether the rectangle itself takes anything away."""
    return valid(crop) and any(abs(crop.get(k, DEFAULT_CROP[k]) - DEFAULT_CROP[k]) > 1e-7 for k in ("x", "y", "w", "h"))


def turned(crop):
    """Whether the picture is straightened, rotated or mirrored."""
    if not valid(crop):
        return False
    c = {**DEFAULT_CROP, **crop}
    return bool(c["rotate"] or c["flipH"] or c["flipV"] or abs(c["angle"]) > 1e-7)


def active(crop):
    return framed(crop) or turned(crop)


def _straightened(width: float, height: float, angle: float) -> float:
    """The scale of the largest upright rectangle of the same shape inside a ``width`` x ``height`` picture turned by ``angle``."""
    c, s = abs(math.cos(math.radians(angle))), abs(math.sin(math.radians(angle)))
    return min(width / (width * c + height * s), height / (width * s + height * c))


def turned_size(crop, width: int, height: int, quarter_only: bool = False) -> tuple[int, int]:
    """The size of a ``width`` x ``height`` picture after the turn (the rectangle is not applied here)."""
    c = {**DEFAULT_CROP, **crop}
    if not quarter_only and abs(c["angle"]) > 1e-7:
        scale = _straightened(width, height, c["angle"])
        # One pixel in from each edge: the turned picture's own edge is blended with nothing, and must not show as a seam.
        width, height = max(1, math.floor(width * scale) - 2), max(1, math.floor(height * scale) - 2)
    return (height, width) if c["rotate"] in (90, 270) else (width, height)


def turn(image, crop, quarter_only: bool = False):
    """``image`` straightened, rotated and mirrored as ``crop`` says. ``quarter_only`` leaves the straightening out (the crop
    tool shows the whole frame upright that way, with the rectangle drawn over it)."""
    from PySide6.QtGui import QTransform

    c = {**DEFAULT_CROP, **crop}
    if not quarter_only and abs(c["angle"]) > 1e-7:
        width, height = turned_size({"angle": c["angle"]}, image.width(), image.height())
        kind = image.format()
        spun = image.transformed(QTransform().rotate(c["angle"]), Qt.TransformationMode.SmoothTransformation)
        image = spun.copy((spun.width() - width) // 2, (spun.height() - height) // 2, width, height)
        if image.format() != kind:
            image = image.convertToFormat(kind)        # turning adds an alpha channel for the empty corners; none are kept
    if c["rotate"]:
        image = image.transformed(QTransform().rotate(c["rotate"]))
    if c["flipH"] or c["flipV"]:
        image = image.flipped((Qt.Orientation.Horizontal if c["flipH"] else Qt.Orientation(0)) | (Qt.Orientation.Vertical if c["flipV"] else Qt.Orientation(0)))
    return image


def view_to_frame(u: float, v: float, crop, quarter_only: bool = False, shape: tuple[float, float] = (1.0, 1.0)) -> tuple[float, float]:
    """Where a point of the turned picture (0..1 across what is shown) is in the picture before the turn (0..1). ``shape`` is the
    width and height of the picture before the turn; only their proportion matters, and only when it is straightened."""
    c = {**DEFAULT_CROP, **crop}
    if c["flipH"]:
        u = 1 - u
    if c["flipV"]:
        v = 1 - v
    u, v = {0: (u, v), 90: (v, 1 - u), 180: (1 - u, 1 - v), 270: (1 - v, u)}[c["rotate"]]
    if not quarter_only and abs(c["angle"]) > 1e-7:
        width, height = shape
        scale = _straightened(width, height, c["angle"])
        x, y = (u - 0.5) * width * scale, (v - 0.5) * height * scale       # from the centre, in the kept rectangle
        cos, sin = math.cos(math.radians(-c["angle"])), math.sin(math.radians(-c["angle"]))
        u, v = 0.5 + (x * cos - y * sin) / width, 0.5 + (x * sin + y * cos) / height
    return u, v


def view_to_source(u: float, v: float, crop, shape: tuple[float, float] = (1.0, 1.0)) -> tuple[float, float]:
    """Where a point of the finished picture (cropped and turned, 0..1) is in the developed picture as recorded (0..1).
    ``shape`` is the developed picture's width and height."""
    c = {**DEFAULT_CROP, **crop}
    u, v = view_to_frame(u, v, c, shape=(shape[0] * c["w"], shape[1] * c["h"]))
    return c["x"] + u * c["w"], c["y"] + v * c["h"]


def rect_to_view(crop):
    """The crop rectangle as the crop tool shows it: over the whole frame after the quarter-turn and mirrors. (x, y, w, h)."""
    c = {**DEFAULT_CROP, **crop}
    x0, y0, x1, y1 = c["x"], c["y"], c["x"] + c["w"], c["y"] + c["h"]
    corners = [_frame_to_view(x, y, c) for x, y in ((x0, y0), (x1, y1))]
    left, top = min(p[0] for p in corners), min(p[1] for p in corners)
    return left, top, abs(corners[1][0] - corners[0][0]), abs(corners[1][1] - corners[0][1])


def rect_from_view(x: float, y: float, w: float, h: float, crop):
    """The inverse of ``rect_to_view``: a rectangle drawn in the crop tool, as (x, y, w, h) in the picture as recorded."""
    c = {**DEFAULT_CROP, **crop}
    corners = [view_to_frame(px, py, c, quarter_only=True) for px, py in ((x, y), (x + w, y + h))]
    left, top = min(p[0] for p in corners), min(p[1] for p in corners)
    return left, top, abs(corners[1][0] - corners[0][0]), abs(corners[1][1] - corners[0][1])


def _frame_to_view(x: float, y: float, c) -> tuple[float, float]:
    u, v = {0: (x, y), 90: (1 - y, x), 180: (1 - x, 1 - y), 270: (y, 1 - x)}[c["rotate"]]
    return (1 - u if c["flipH"] else u), (1 - v if c["flipV"] else v)


def rotated(crop, quarter_turns: int):
    """``crop`` after turning what is shown by quarter turns clockwise. The mirrors are kept as the person sees them."""
    c = {**DEFAULT_CROP, **crop}
    for _ in range(quarter_turns % 4):
        c["rotate"] = (c["rotate"] + 90) % 360
        c["flipH"], c["flipV"] = c["flipV"], c["flipH"]        # a mirror across one axis is across the other after a quarter-turn
    return c


def flipped(crop, horizontal: bool):
    """``crop`` after mirroring what is shown left-to-right (or top-to-bottom)."""
    c = {**DEFAULT_CROP, **crop}
    key = "flipH" if horizontal else "flipV"
    c[key] = not c[key]
    return c


def shown_angle(crop) -> float:
    """The straighten angle as the person sees it turn: a single mirror reverses the direction."""
    c = {**DEFAULT_CROP, **crop}
    return -c["angle"] if c["flipH"] != c["flipV"] else c["angle"]


def bounds(crop, width, height):
    if not valid(crop):
        raise ValueError("Invalid crop rectangle")
    c = {**DEFAULT_CROP, **crop}
    x = min(width - 1, max(0, round(c["x"] * width)))
    y = min(height - 1, max(0, round(c["y"] * height)))
    right = min(width, max(x + 1, round((c["x"] + c["w"]) * width)))
    bottom = min(height, max(y + 1, round((c["y"] + c["h"]) * height)))
    return x, y, right - x, bottom - y


def atomic_write(path, data):
    output = QSaveFile(str(path))
    if not output.open(QIODevice.WriteOnly) or output.write(data) != len(data) or not output.commit():
        raise OSError(f"Could not save {Path(path).name}")


# The screen-sized picture the editor writes beside a full-size render (its DISPLAY_LONG_SIDE and DISPLAY_QUALITY).
DISPLAY_LONG_SIDE = 2560
DISPLAY_QUALITY = 90


def crop_file(source, destination, crop):
    return _crop_to_file(source, destination, crop)[:4]


def _crop_to_file(source, destination, crop):
    if not valid(crop):
        raise ValueError("Invalid crop rectangle")
    # Camera-sized 16-bit RGB PNGs can exceed Qt's conservative default limit.
    # Check dimensions before allowing the bounded full-resolution decode.
    reader = QImageReader(str(source))
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 120_000_000:
        del reader  # Release the Windows file handle before callers remove a failed render.
        raise ValueError("The full-resolution crop source has invalid dimensions")
    if 0 < QImageReader.allocationLimit() < 1024:
        QImageReader.setAllocationLimit(1024)
    image = reader.read()
    del reader
    if image.isNull():
        raise ValueError("The full-resolution crop source could not be decoded")
    if framed(crop):
        image = image.copy(*bounds(crop, image.width(), image.height()))
    if turned(crop):
        image = turn(image, crop)
    output = QSaveFile(str(destination))
    if not output.open(QIODevice.WriteOnly) or not image.save(output, "PNG") or not output.commit():
        raise OSError("The cropped image could not be saved")
    return image.width(), image.height(), (16 if image.depth() > 32 else 8), image.colorSpace().isValid(), image


def write_display_jpeg(image, destination):
    """A screen-sized 8-bit sRGB JPEG of ``image``: what thumbnails and the viewer read instead of the full-size 16-bit PNG.
    Returns ``(width, height, has a colour profile)``."""
    if max(image.width(), image.height()) > DISPLAY_LONG_SIDE:
        image = image.scaled(DISPLAY_LONG_SIDE, DISPLAY_LONG_SIDE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if image.colorSpace().isValid():
        image = image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    output = QSaveFile(str(destination))
    if image.isNull() or not output.open(QIODevice.WriteOnly) or not image.save(output, "JPEG", DISPLAY_QUALITY) or not output.commit():
        raise OSError("The display picture could not be saved")
    return image.width(), image.height(), image.colorSpace().isValid()


def publish(image_path, rendered, settings, signature):
    """Publish a full-size cropped render and recipe; the original RAW stays untouched."""
    from . import photocraft_bridge as bridge

    if bridge.file_signature(image_path) != signature:
        raise ValueError("The original photo changed while the crop was rendering")
    root = bridge.recipe_path(image_path).parent
    root.mkdir(parents=True, exist_ok=True)
    generation = time.time_ns()
    target = root / f"{Path(image_path).name}.photocraft.g{generation}.png"
    width, height, depth, icc, image = _crop_to_file(rendered, target, settings.get("hostCrop", DEFAULT_CROP))
    outputs = {"png": {"file": target.name, "format": "png", "width": width, "height": height, "depth": depth, "icc": icc}}
    display = root / f"{Path(image_path).name}.photocraft.display.g{generation}.jpg"
    try:
        shown_width, shown_height, shown_icc = write_display_jpeg(image, display)
        outputs["display"] = {"file": display.name, "format": "jpeg", "width": shown_width, "height": shown_height, "depth": 8, "icc": shown_icc}
    except OSError:
        # Without it the full-size picture is still published; only reading it for a thumbnail is slower.
        display.unlink(missing_ok=True)
    del image
    recipe = json.dumps({"version": bridge.RECIPE_VERSION, "cameraRaw": settings}, ensure_ascii=False).encode("utf-8")
    manifest = {"version": bridge.RENDER_MANIFEST_VERSION, "generation": generation, "kind": "recipe",
                "source": signature, "renderer": {"algorithm": bridge.RENDER_ALGORITHM},
                "recipe": {"sha256": hashlib.sha256(recipe).hexdigest()},
                "outputs": outputs}
    atomic_write(bridge.recipe_path(image_path), recipe)
    atomic_write(bridge.render_manifest_path(image_path), json.dumps(manifest).encode("utf-8"))
    # The manifest now names this generation, so the pictures of the ones before it can go (after their grace period).
    bridge.prune_render_generations(image_path)
    return str(target)


def apply_render(result, recipe):
    from .full_render import RENDERED, RenderResult, failed

    crop = recipe.get("cameraRaw", {}).get("hostCrop", DEFAULT_CROP)
    if result.outcome != RENDERED or not active(crop):
        return result
    target = result.path.with_name(result.path.stem + ".crop-" + uuid4().hex + ".png")
    try:
        width, height, depth, icc = crop_file(result.path, target, crop)
        return RenderResult(RENDERED, path=target, format="png", width=width, height=height, depth=depth, icc=icc, temporary=True)
    except Exception as error:
        target.unlink(missing_ok=True)
        return failed(str(error))
    finally:
        result.release()
