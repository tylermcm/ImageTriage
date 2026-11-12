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

DEFAULT_CROP = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0, "ratio": "Original"}
RATIOS = ("Original", "Free", "1:1", "4:5", "3:2", "16:9")


def valid(crop):
    if not isinstance(crop, dict) or set(crop) - set(DEFAULT_CROP):
        return False
    c = {**DEFAULT_CROP, **crop}
    if c["ratio"] not in RATIOS:
        return False
    if any(type(c[k]) not in (int, float) or not math.isfinite(c[k]) for k in ("x", "y", "w", "h")):
        return False
    return (0 <= c["x"] < 1 and 0 <= c["y"] < 1 and 0 < c["w"] <= 1 and 0 < c["h"] <= 1
            and c["x"] + c["w"] <= 1.000001 and c["y"] + c["h"] <= 1.000001)


def active(crop):
    return valid(crop) and any(abs(crop.get(k, v) - v) > 1e-7 for k, v in DEFAULT_CROP.items() if k != "ratio")


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
        raise ValueError("The full-resolution crop source has invalid dimensions")
    if 0 < QImageReader.allocationLimit() < 1024:
        QImageReader.setAllocationLimit(1024)
    image = reader.read()
    if image.isNull():
        raise ValueError("The full-resolution crop source could not be decoded")
    if active(crop):
        image = image.copy(*bounds(crop, image.width(), image.height()))
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
