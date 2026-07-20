from __future__ import annotations

import math
from typing import Literal, Tuple

from PIL import Image, ImageChops, ImageFilter, ImageOps


MaskAction = Literal["replace", "add", "subtract", "intersect"]
Point = Tuple[int, int]


def _clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _work_size(size: Tuple[int, int], max_edge: int) -> Tuple[int, int, float]:
    width, height = size
    edge = max(width, height)
    if edge <= max_edge:
        return width, height, 1.0
    scale = max_edge / edge
    return max(1, int(width * scale)), max(1, int(height * scale)), scale


def _finish_mask(mask: Image.Image, size: Tuple[int, int], density: float, invert: bool) -> Image.Image:
    density = max(0.0, min(1.0, float(density)))
    if density < 1.0:
        mask = mask.point(lambda value: _clamp_byte(value * density))
    if invert:
        mask = ImageOps.invert(mask)
        if density < 1.0:
            mask = mask.point(lambda value: min(value, _clamp_byte(255 * density)))
    if mask.size != size:
        mask = mask.resize(size, Image.Resampling.BILINEAR)
    return mask.convert("L")


def combine_masks(base: Image.Image, incoming: Image.Image, action: MaskAction) -> Image.Image:
    base = base.convert("L")
    incoming = incoming.convert("L")
    if incoming.size != base.size:
        incoming = incoming.resize(base.size, Image.Resampling.BILINEAR)
    if action == "replace":
        return incoming
    if action == "add":
        return ImageChops.lighter(base, incoming)
    if action == "subtract":
        return ImageChops.subtract(base, incoming)
    if action == "intersect":
        return ImageChops.multiply(base, incoming)
    raise ValueError(f"unknown mask action: {action}")


def make_radial_mask(
    size: Tuple[int, int],
    start: Point,
    end: Point,
    feather: float = 60.0,
    density: float = 1.0,
    invert: bool = False,
    angle: float = 0.0,
    max_edge: int = 900,
) -> Image.Image:
    work_width, work_height, scale = _work_size(size, max_edge)
    cx = start[0] * scale
    cy = start[1] * scale
    rx = max(2.0, abs(end[0] - start[0]) * scale)
    ry = max(2.0, abs(end[1] - start[1]) * scale)
    feather_fraction = max(0.0, min(1.0, feather / 100.0))
    core = max(0.0, 1.0 - feather_fraction)
    # Rotate the sampling frame by -angle so the ellipse's major axis tilts by
    # +angle (degrees, clockwise), matching the GUI overlay.
    radians = math.radians(angle)
    cos_a = math.cos(radians)
    sin_a = math.sin(radians)

    mask = Image.new("L", (work_width, work_height), 0)
    pixels = mask.load()
    for y in range(work_height):
        dy = y - cy
        for x in range(work_width):
            dx = x - cx
            normalized_x = (dx * cos_a + dy * sin_a) / rx
            normalized_y = (-dx * sin_a + dy * cos_a) / ry
            distance = math.sqrt(normalized_x * normalized_x + normalized_y * normalized_y)
            if distance <= core:
                value = 1.0
            elif distance >= 1.0:
                value = 0.0
            else:
                value = 1.0 - ((distance - core) / max(1e-6, 1.0 - core))
            pixels[x, y] = _clamp_byte(value * 255)

    return _finish_mask(mask, size, density, invert)


def make_linear_gradient_mask(
    size: Tuple[int, int],
    start: Point,
    end: Point,
    feather: float = 100.0,
    density: float = 1.0,
    invert: bool = False,
    max_edge: int = 900,
) -> Image.Image:
    work_width, work_height, scale = _work_size(size, max_edge)
    sx = start[0] * scale
    sy = start[1] * scale
    ex = end[0] * scale
    ey = end[1] * scale
    dx = ex - sx
    dy = ey - sy
    length_squared = dx * dx + dy * dy
    if length_squared < 1.0:
        return Image.new("L", size, _clamp_byte(255 * density))

    feather_fraction = max(0.01, min(1.0, feather / 100.0))
    full_strength_until = 1.0 - feather_fraction

    mask = Image.new("L", (work_width, work_height), 0)
    pixels = mask.load()
    for y in range(work_height):
        for x in range(work_width):
            t = ((x - sx) * dx + (y - sy) * dy) / length_squared
            if t <= full_strength_until:
                value = 1.0
            elif t >= 1.0:
                value = 0.0
            else:
                value = 1.0 - ((t - full_strength_until) / max(1e-6, 1.0 - full_strength_until))
            pixels[x, y] = _clamp_byte(value * 255)

    return _finish_mask(mask, size, density, invert)


def soften_mask(mask: Image.Image, radius: float) -> Image.Image:
    if radius <= 0:
        return mask.convert("L")
    return mask.convert("L").filter(ImageFilter.GaussianBlur(radius=radius))


def refine_luminance_range(
    image: Image.Image,
    mask: Image.Image,
    low: int,
    high: int,
    feather: int = 20,
    invert: bool = False,
) -> Image.Image:
    low = max(0, min(254, int(low)))
    high = min(255, max(low + 1, int(high)))
    feather = max(0, int(feather))
    luma = ImageOps.grayscale(image).resize(mask.size, Image.Resampling.BILINEAR)

    def map_pixel(value: int) -> int:
        if value < low - feather or value > high + feather:
            amount = 0.0
        elif low <= value <= high:
            amount = 1.0
        elif value < low:
            amount = (value - (low - feather)) / max(1, feather)
        else:
            amount = ((high + feather) - value) / max(1, feather)
        if invert:
            amount = 1.0 - amount
        return _clamp_byte(amount * 255)

    range_mask = luma.point(map_pixel)
    return ImageChops.multiply(mask.convert("L"), range_mask)


def refine_color_range(
    image: Image.Image,
    mask: Image.Image,
    sample: Tuple[int, int, int],
    tolerance: int = 45,
    feather: int = 35,
    invert: bool = False,
    max_edge: int = 900,
) -> Image.Image:
    work_width, work_height, scale = _work_size(image.size, max_edge)
    work = image.convert("RGB").resize((work_width, work_height), Image.Resampling.BILINEAR)
    work_mask = mask.convert("L").resize((work_width, work_height), Image.Resampling.BILINEAR)
    out = Image.new("L", (work_width, work_height), 0)
    pixels = work.load()
    out_pixels = out.load()
    sr, sg, sb = sample
    tolerance = max(1, int(tolerance))
    feather = max(1, int(feather))
    max_distance = tolerance + feather

    for y in range(work_height):
        for x in range(work_width):
            red, green, blue = pixels[x, y]
            distance = math.sqrt((red - sr) ** 2 + (green - sg) ** 2 + (blue - sb) ** 2)
            if distance <= tolerance:
                amount = 1.0
            elif distance >= max_distance:
                amount = 0.0
            else:
                amount = 1.0 - ((distance - tolerance) / feather)
            if invert:
                amount = 1.0 - amount
            out_pixels[x, y] = _clamp_byte(amount * 255)

    refined = ImageChops.multiply(work_mask, out)
    if refined.size != mask.size:
        refined = refined.resize(mask.size, Image.Resampling.BILINEAR)
    return refined


def adjust_mask_bounds(mask: Image.Image, pixels: int) -> Image.Image:
    mask = mask.convert("L")
    pixels = int(pixels)
    if pixels == 0:
        return mask
    filt = ImageFilter.MaxFilter if pixels > 0 else ImageFilter.MinFilter
    result = mask
    for _ in range(abs(pixels)):
        result = result.filter(filt(3))
    return result
