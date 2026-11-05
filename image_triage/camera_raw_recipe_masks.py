"""Bounded validation of PhotoCraft's serialized local-mask recipe contract."""
from __future__ import annotations

import base64
import binascii

_ADJUST = frozenset(("temperature", "tint", "exposure", "contrast", "highlights",
                     "shadows", "whites", "blacks", "texture", "clarity", "dehaze", "saturation"))
_DEFAULT_SHAPE = {"type": "linear", "x0": 0.5, "y0": 0.25, "x1": 0.5, "y1": 0.75}


def _number(value, low, high):
    return type(value) in (int, float) and low <= value <= high


def _fields(value, required, optional=()):
    return isinstance(value, dict) and set(required) <= value.keys() <= set(required) | set(optional)


def _raster(shape):
    w, h, text = shape["w"], shape["h"], shape["data"]
    if not (type(w) is int and type(h) is int and 1 <= w <= 1536 and 1 <= h <= 1536):
        return False
    if not isinstance(text, str) or len(text) > 6 * 1024 * 1024 or len(text) % 4:
        return False
    try:
        raw = base64.b64decode(text, validate=True)
    except (ValueError, binascii.Error):
        return False
    index = total = 0
    expected = w * h
    while index < len(raw):
        index += 1  # coverage byte
        run = shift = 0
        while True:
            if index >= len(raw):
                return False
            byte = raw[index]
            index += 1
            run |= (byte & 0x7f) << shift
            if byte & 0x80 == 0:
                break
            shift += 7
            if shift > 28:
                return False
        total += run
        if run == 0 or total > expected:
            return False
    return total == expected


def _shape(shape):
    if not isinstance(shape, dict):
        return False
    kind = shape.get("type")
    fields = {
        "linear": ("x0", "y0", "x1", "y1"),
        "radial": ("cx", "cy", "rx", "ry", "angle", "feather"),
        "raster": ("w", "h", "data"),
        "colorRange": ("color", "range", "smooth"),
        "luminanceRange": ("low", "high", "smooth"),
    }
    if not isinstance(kind, str) or kind not in fields or not _fields(shape, ("type", *fields[kind])):
        return False
    if kind == "linear":
        return all(_number(shape[key], -2, 3) for key in fields[kind])
    if kind == "radial":
        return (all(_number(shape[key], -2, 3) for key in ("cx", "cy"))
                and all(_number(shape[key], 0, 4) for key in ("rx", "ry"))
                and _number(shape["angle"], -360, 360) and _number(shape["feather"], 0, 100))
    if kind == "raster":
        return _raster(shape)
    if kind == "colorRange":
        color = shape["color"]
        return (isinstance(color, list) and len(color) == 3
                and all(_number(c, -0.5, 2) for c in color)
                and all(_number(shape[k], 0, 100) for k in ("range", "smooth")))
    return (all(_number(shape[k], 0, 1) for k in ("low", "high"))
            and shape["low"] <= shape["high"] and _number(shape["smooth"], 0, 100))


def masks_valid(masks):
    if not isinstance(masks, list) or len(masks) > 16:
        return False
    for mask in masks:
        if not _fields(mask, (), ("name", "enabled", "shape", "invert", "parts", "amount", "adjust")):
            return False
        name = mask.get("name", "")
        if not isinstance(name, str) or len(name) > 64:
            return False
        if any(type(mask.get(key, default)) is not bool for key, default in (("enabled", True), ("invert", False))):
            return False
        if not _number(mask.get("amount", 100), 0, 100) or not _shape(mask.get("shape", _DEFAULT_SHAPE)):
            return False
        adjust = mask.get("adjust", {})
        if not isinstance(adjust, dict) or not adjust.keys() <= _ADJUST:
            return False
        if any(not _number(value, -5 if key == "exposure" else -100, 5 if key == "exposure" else 100)
               for key, value in adjust.items()):
            return False
        parts = mask.get("parts", [])
        if not isinstance(parts, list) or len(parts) > 8:
            return False
        for part in parts:
            if not _fields(part, (), ("op", "shape", "invert")):
                return False
            if part.get("op", "add") not in ("add", "subtract", "intersect") or type(part.get("invert", False)) is not bool:
                return False
            if not _shape(part.get("shape", _DEFAULT_SHAPE)):
                return False
    return True


def masks_active(masks):
    """Match native LocalMask::active after validation."""
    return any(mask.get("enabled", True) and mask.get("amount", 100) > 0
               and any(value != 0 for value in mask.get("adjust", {}).values()) for mask in masks)
