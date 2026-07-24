from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any, Optional, Tuple, Union

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageOps


def _clamp_percent(value: float) -> float:
    return max(-100.0, min(100.0, float(value)))


def _clamp_byte(value: float) -> int:
    return max(0, min(255, int(round(value))))


def _exif_rgb(image: Image.Image) -> Image.Image:
    return ImageOps.exif_transpose(image).convert("RGB")


def _scale_channel(channel: Image.Image, factor: float) -> Image.Image:
    return channel.point(lambda pixel: _clamp_byte(pixel * factor))


def _offset_channel(channel: Image.Image, offset: float) -> Image.Image:
    return channel.point(lambda pixel: _clamp_byte(pixel + offset))


def _tone_mask(luma: Image.Image, low: int, high: int, power: float = 1.0, invert: bool = False) -> Image.Image:
    span = max(1, high - low)

    def map_pixel(pixel: int) -> int:
        value = (pixel - low) / span
        value = max(0.0, min(1.0, value))
        if invert:
            value = 1.0 - value
        return _clamp_byte((value**power) * 255)

    return luma.point(map_pixel)


def _composite_adjusted(base: Image.Image, adjusted: Image.Image, mask: Image.Image) -> Image.Image:
    return Image.composite(adjusted.convert("RGB"), base.convert("RGB"), mask.convert("L"))


@dataclass
class EditRecipe:
    exposure: float = 0.0
    contrast: float = 0.0
    highlights: float = 0.0
    shadows: float = 0.0
    whites: float = 0.0
    blacks: float = 0.0
    temperature: float = 0.0
    tint: float = 0.0
    vibrance: float = 0.0
    saturation: float = 0.0
    clarity: float = 0.0
    dehaze: float = 0.0
    sharpen: float = 0.0
    denoise: float = 0.0
    luminance_noise: float = 0.0
    color_noise: float = 0.0
    vignette: float = 0.0
    # Post-crop vignette shape. Midpoint/feather are 0..100 (50 = neutral),
    # roundness is -100 (frame-shaped) .. 100 (circular), highlights 0..100
    # protects bright pixels from the effect.
    vignette_midpoint: float = 50.0
    vignette_roundness: float = 0.0
    vignette_feather: float = 50.0
    vignette_highlights: float = 0.0
    texture: float = 0.0
    grain: float = 0.0
    curve_shadows: float = 0.0
    curve_mids: float = 0.0
    curve_highlights: float = 0.0
    # Photoshop-style point curves: [[x, y], ...] in 0..255, or None/[] for
    # identity. ``curve_rgb`` is the composite; the others are per channel.
    curve_rgb: Optional[list] = None
    curve_red: Optional[list] = None
    curve_green: Optional[list] = None
    curve_blue: Optional[list] = None
    red_saturation: float = 0.0
    orange_saturation: float = 0.0
    yellow_saturation: float = 0.0
    green_saturation: float = 0.0
    aqua_saturation: float = 0.0
    blue_saturation: float = 0.0
    purple_saturation: float = 0.0
    magenta_saturation: float = 0.0
    hsl_luminance: float = 0.0
    vignette_correction: float = 0.0
    chromatic_aberration: float = 0.0
    perspective_x: float = 0.0
    perspective_y: float = 0.0
    rotate: float = 0.0
    crop: Optional[Tuple[int, int, int, int]] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EditRecipe":
        valid = {field.name for field in fields(cls)}
        kwargs = {key: value for key, value in data.items() if key in valid}
        if kwargs.get("crop") is not None:
            kwargs["crop"] = tuple(int(v) for v in kwargs["crop"])
        for curve_key in ("curve_rgb", "curve_red", "curve_green", "curve_blue"):
            if kwargs.get(curve_key) is not None:
                kwargs[curve_key] = normalize_curve_points(kwargs[curve_key]) or None
        return cls(**kwargs)

    @classmethod
    def load(cls, path: Union[str, Path]) -> "EditRecipe":
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def save(self, path: Union[str, Path]) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle, indent=2)
            handle.write("\n")

    def merged(self, override: "EditRecipe") -> "EditRecipe":
        base = asdict(self)
        defaults = {field.name: field.default for field in fields(self)}
        for key, value in asdict(override).items():
            # An override counts only when it differs from the field default.
            # Plain zero-checking breaks fields whose neutral value is not zero
            # (the vignette shape controls sit at 50).
            if value != defaults.get(key, 0):
                base[key] = value
        return EditRecipe.from_dict(base)

    def apply(self, image: Image.Image) -> Image.Image:
        working = _exif_rgb(image)
        if self.crop:
            working = working.crop(self.crop)
        if self.rotate:
            working = working.rotate(self.rotate, expand=True, resample=Image.Resampling.BICUBIC)
        if self.perspective_x or self.perspective_y:
            working = apply_perspective(working, self.perspective_x, self.perspective_y)

        if self.exposure:
            working = ImageEnhance.Brightness(working).enhance(2.0 ** float(self.exposure))

        if self.temperature or self.tint:
            working = apply_white_balance(working, self.temperature, self.tint)

        if self.contrast:
            working = ImageEnhance.Contrast(working).enhance(1.0 + _clamp_percent(self.contrast) / 100.0)

        if self.highlights:
            amount = _clamp_percent(self.highlights) / 100.0
            mask = _tone_mask(ImageOps.grayscale(working), 118, 255, power=1.35)
            adjusted = ImageEnhance.Brightness(working).enhance(1.0 + amount * 0.55)
            working = _composite_adjusted(working, adjusted, mask)

        if self.shadows:
            amount = _clamp_percent(self.shadows) / 100.0
            mask = _tone_mask(ImageOps.grayscale(working), 0, 165, power=1.15, invert=True)
            adjusted = ImageEnhance.Brightness(working).enhance(1.0 + amount * 0.65)
            working = _composite_adjusted(working, adjusted, mask)

        if self.whites:
            amount = _clamp_percent(self.whites) / 100.0
            mask = _tone_mask(ImageOps.grayscale(working), 185, 255, power=1.0)
            adjusted = ImageEnhance.Brightness(working).enhance(1.0 + amount * 0.35)
            working = _composite_adjusted(working, adjusted, mask)

        if self.blacks:
            amount = _clamp_percent(self.blacks) / 100.0
            mask = _tone_mask(ImageOps.grayscale(working), 0, 75, power=1.0, invert=True)
            adjusted = ImageEnhance.Brightness(working).enhance(1.0 + amount * 0.45)
            working = _composite_adjusted(working, adjusted, mask)

        if self.vibrance:
            working = ImageEnhance.Color(working).enhance(1.0 + _clamp_percent(self.vibrance) / 135.0)
        if self.saturation:
            working = ImageEnhance.Color(working).enhance(1.0 + _clamp_percent(self.saturation) / 100.0)

        if self.denoise:
            amount = abs(_clamp_percent(self.denoise)) / 100.0
            blurred = working.filter(ImageFilter.GaussianBlur(radius=0.4 + amount * 1.8))
            working = Image.blend(working, blurred, min(0.85, amount))
        if self.luminance_noise:
            working = reduce_luminance_noise(working, self.luminance_noise)
        if self.color_noise:
            working = reduce_color_noise(working, self.color_noise)

        if self.clarity:
            amount = _clamp_percent(self.clarity) / 100.0
            if amount >= 0:
                sharpened = working.filter(ImageFilter.UnsharpMask(radius=7, percent=int(80 + amount * 160), threshold=8))
                working = Image.blend(working, sharpened, min(1.0, amount))
            else:
                blurred = working.filter(ImageFilter.GaussianBlur(radius=2.0 + abs(amount) * 4.0))
                working = Image.blend(working, blurred, min(0.7, abs(amount)))

        if self.texture:
            working = apply_texture(working, self.texture)

        if self.dehaze:
            amount = _clamp_percent(self.dehaze) / 100.0
            working = ImageEnhance.Contrast(working).enhance(1.0 + amount * 0.45)
            working = ImageEnhance.Brightness(working).enhance(1.0 - amount * 0.06)

        if self.sharpen:
            amount = abs(_clamp_percent(self.sharpen)) / 100.0
            sharp = working.filter(ImageFilter.UnsharpMask(radius=1.4, percent=int(70 + 180 * amount), threshold=3))
            working = Image.blend(working, sharp, min(1.0, amount))

        if any(
            (
                self.red_saturation,
                self.orange_saturation,
                self.yellow_saturation,
                self.green_saturation,
                self.aqua_saturation,
                self.blue_saturation,
                self.purple_saturation,
                self.magenta_saturation,
                self.hsl_luminance,
            )
        ):
            working = apply_hsl_adjustments(working, self)

        if self.curve_shadows or self.curve_mids or self.curve_highlights:
            working = apply_tone_curve(working, self.curve_shadows, self.curve_mids, self.curve_highlights)

        if not all(
            is_identity_curve(points)
            for points in (self.curve_rgb, self.curve_red, self.curve_green, self.curve_blue)
        ):
            working = apply_point_curves(
                working, self.curve_rgb, self.curve_red, self.curve_green, self.curve_blue
            )

        if self.vignette_correction:
            working = apply_vignette(working, -_clamp_percent(self.vignette_correction) / 100.0)
        if self.vignette:
            working = apply_vignette(
                working,
                _clamp_percent(self.vignette) / 100.0,
                midpoint=self.vignette_midpoint,
                roundness=self.vignette_roundness,
                feather=self.vignette_feather,
                highlights=self.vignette_highlights,
            )
        if self.chromatic_aberration:
            working = reduce_chromatic_aberration(working, self.chromatic_aberration)
        if self.grain:
            working = apply_grain(working, self.grain)

        return working


def apply_white_balance(image: Image.Image, temperature: float, tint: float) -> Image.Image:
    temp = _clamp_percent(temperature) / 100.0
    tint_value = _clamp_percent(tint) / 100.0
    red, green, blue = image.split()
    red = _scale_channel(red, 1.0 + temp * 0.18 - tint_value * 0.05)
    green = _scale_channel(green, 1.0 + tint_value * 0.13)
    blue = _scale_channel(blue, 1.0 - temp * 0.18 - tint_value * 0.05)
    return Image.merge("RGB", (red, green, blue))


VIGNETTE_MAX_STOPS = 2.0
VIGNETTE_DEFAULTS = {"midpoint": 50.0, "roundness": 0.0, "feather": 50.0, "highlights": 0.0}


@lru_cache(maxsize=8)
def _vignette_falloff(
    width: int, height: int, midpoint: float, roundness: float, feather: float
) -> Image.Image:
    """Frame-shaped falloff, 0 in the protected centre and 255 at the corners.

    Cached because it depends only on the frame and the shape controls — never
    on Amount — so dragging Amount costs one PIL composite and nothing else.
    """
    shape = max(-1.0, min(1.0, roundness / 100.0))
    if shape >= 0.0:
        # Toward a true circle in *pixel* space: scaling the normalized axes by
        # the frame's own proportions stops the contours tracking the aspect
        # ratio, which is why the old fixed formula bit harder on the long edge.
        longest = float(max(width, height, 1))
        scale_x = 1.0 + shape * (width / longest - 1.0)
        scale_y = 1.0 + shape * (height / longest - 1.0)
        power = 2.0
    else:
        # Toward the frame shape: a superellipse with a rising exponent squares
        # the corners off.
        scale_x = scale_y = 1.0
        power = 2.0 + (-shape) * 6.0

    ys, xs = np.ogrid[0:height, 0:width]
    norm_x = np.abs(xs * (2.0 / max(1, width - 1)) - 1.0).astype(np.float32) * scale_x
    norm_y = np.abs(ys * (2.0 / max(1, height - 1)) - 1.0).astype(np.float32) * scale_y
    if power == 2.0:
        radius = np.sqrt(norm_x * norm_x + norm_y * norm_y)
        corner = math.sqrt(scale_x * scale_x + scale_y * scale_y)
    else:
        radius = (norm_x**power + norm_y**power) ** (1.0 / power)
        corner = (scale_x**power + scale_y**power) ** (1.0 / power)
    radius = radius / corner  # 1.0 at the corners whatever the shape

    centre = max(0.0, min(1.0, midpoint / 100.0))
    half_width = 0.02 + max(0.0, min(1.0, feather / 100.0)) * 0.6
    inner = centre - half_width
    ramp = np.clip((radius - inner) / max(1e-6, 2.0 * half_width), 0.0, 1.0)
    # Smoothstep instead of the old clipped x**1.8 ramp: that one only reached
    # full strength at the literal corner pixel and left a kink where it hit 0.
    ramp = ramp * ramp * (3.0 - 2.0 * ramp)
    return Image.fromarray(np.rint(ramp * 255.0).astype(np.uint8), "L")


def _vignette_highlight_guard(image: Image.Image, highlights: float) -> Image.Image:
    """Attenuates the falloff over already-bright pixels, so a strong vignette
    darkens the sky without swallowing the sun (and a negative one does not
    blow the corners straight to white)."""
    strength = max(0.0, min(1.0, highlights / 100.0))
    lut = [_clamp_byte(255.0 * (1.0 - strength * (index / 255.0) ** 2)) for index in range(256)]
    return image.convert("L").point(lut)


def apply_vignette(
    image: Image.Image,
    amount: float,
    *,
    midpoint: float = 50.0,
    roundness: float = 0.0,
    feather: float = 50.0,
    highlights: float = 0.0,
) -> Image.Image:
    """Post-crop vignette. ``amount`` is -1..1, positive darkens the corners."""
    amount = max(-1.0, min(1.0, float(amount)))
    if amount == 0.0:
        return image
    width, height = image.size
    mask = _vignette_falloff(
        width,
        height,
        round(float(midpoint), 2),
        round(float(roundness), 2),
        round(float(feather), 2),
    )
    if highlights > 0.0:
        mask = ImageChops.multiply(mask, _vignette_highlight_guard(image, highlights))
    # Symmetric in stops. The old fixed 0.45/1.35 brightness pair meant +100 was
    # -1.15 EV while -100 was only +0.43 EV, so a negative vignette barely
    # registered on anything but a dark frame.
    adjusted = ImageEnhance.Brightness(image).enhance(2.0 ** (-VIGNETTE_MAX_STOPS * amount))
    return _composite_adjusted(image, adjusted, mask)


def apply_texture(image: Image.Image, amount: float) -> Image.Image:
    amount = _clamp_percent(amount) / 100.0
    if amount == 0:
        return image
    blur = image.filter(ImageFilter.GaussianBlur(radius=1.2))
    detail = ImageChops.subtract(image, blur, scale=1.0, offset=128)
    detail = ImageEnhance.Contrast(detail).enhance(1.0 + abs(amount) * 1.5)
    enhanced = ImageChops.add(image, ImageChops.subtract(detail, Image.new("RGB", image.size, (128, 128, 128))), scale=1.0)
    if amount > 0:
        return Image.blend(image, enhanced, min(0.9, amount))
    softened = image.filter(ImageFilter.GaussianBlur(radius=0.7 + abs(amount)))
    return Image.blend(image, softened, min(0.65, abs(amount)))


def reduce_luminance_noise(image: Image.Image, amount: float) -> Image.Image:
    amount = abs(_clamp_percent(amount)) / 100.0
    if amount == 0:
        return image
    y, cb, cr = image.convert("YCbCr").split()
    smooth = y.filter(ImageFilter.MedianFilter(size=3)).filter(ImageFilter.GaussianBlur(radius=amount * 1.2))
    y = Image.blend(y, smooth, min(0.9, amount))
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def reduce_color_noise(image: Image.Image, amount: float) -> Image.Image:
    amount = abs(_clamp_percent(amount)) / 100.0
    if amount == 0:
        return image
    y, cb, cr = image.convert("YCbCr").split()
    cb = Image.blend(cb, cb.filter(ImageFilter.GaussianBlur(radius=0.8 + amount * 2.0)), min(0.95, amount))
    cr = Image.blend(cr, cr.filter(ImageFilter.GaussianBlur(radius=0.8 + amount * 2.0)), min(0.95, amount))
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


IDENTITY_CURVE: Tuple[Tuple[int, int], ...] = ((0, 0), (255, 255))


def normalize_curve_points(points: Any) -> list[list[int]]:
    """Coerce control points to sorted, deduplicated, in-range [[x, y], ...]."""
    if not points:
        return []
    cleaned: dict[int, int] = {}
    for point in points:
        try:
            x, y = point
        except (TypeError, ValueError):
            continue
        xi = max(0, min(255, int(round(float(x)))))
        yi = max(0, min(255, int(round(float(y)))))
        cleaned[xi] = yi
    return [[x, cleaned[x]] for x in sorted(cleaned)]


def is_identity_curve(points: Any) -> bool:
    normalized = normalize_curve_points(points)
    if not normalized:
        return True
    return normalized == [[0, 0], [255, 255]]


def curve_lut(points: Any) -> list[int]:
    """256-entry lookup table through ``points`` using monotone cubic (Fritsch-
    Carlson) interpolation — smooth like Photoshop's curve but guaranteed not to
    overshoot or wobble between control points. Values outside the control range
    are clamped to the nearest endpoint."""
    pts = normalize_curve_points(points)
    if len(pts) < 2:
        return list(range(256))
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    delta = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]

    # Tangents: interior points use the weighted harmonic mean, and any local
    # extremum gets a zero tangent so the curve stays monotone between points.
    m = [0.0] * n
    m[0] = delta[0]
    m[n - 1] = delta[-1]
    for i in range(1, n - 1):
        if delta[i - 1] * delta[i] <= 0.0:
            m[i] = 0.0
        else:
            w1 = 2.0 * h[i] + h[i - 1]
            w2 = h[i] + 2.0 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / delta[i - 1] + w2 / delta[i])

    lut: list[int] = []
    segment = 0
    for x in range(256):
        if x <= xs[0]:
            lut.append(_clamp_byte(ys[0]))
            continue
        if x >= xs[-1]:
            lut.append(_clamp_byte(ys[-1]))
            continue
        while segment < n - 2 and x > xs[segment + 1]:
            segment += 1
        span = h[segment]
        t = (x - xs[segment]) / span
        t2 = t * t
        t3 = t2 * t
        value = (
            (2 * t3 - 3 * t2 + 1) * ys[segment]
            + (t3 - 2 * t2 + t) * span * m[segment]
            + (-2 * t3 + 3 * t2) * ys[segment + 1]
            + (t3 - t2) * span * m[segment + 1]
        )
        lut.append(_clamp_byte(value))
    return lut


def apply_point_curves(
    image: Image.Image,
    rgb: Any = None,
    red: Any = None,
    green: Any = None,
    blue: Any = None,
) -> Image.Image:
    """Apply Photoshop-style point curves. Each channel is mapped by its own
    curve first, then the composite RGB curve is applied on top, so the RGB
    curve shapes the combined result exactly as it does in Photoshop."""
    if all(is_identity_curve(points) for points in (rgb, red, green, blue)):
        return image
    composite = curve_lut(rgb)
    per_channel = (curve_lut(red), curve_lut(green), curve_lut(blue))
    combined: list[int] = []
    for channel_lut in per_channel:
        combined.extend(composite[channel_lut[value]] for value in range(256))
    return image.convert("RGB").point(combined)


def apply_tone_curve(image: Image.Image, shadows: float, mids: float, highlights: float) -> Image.Image:
    shadows = _clamp_percent(shadows) / 100.0
    mids = _clamp_percent(mids) / 100.0
    highlights = _clamp_percent(highlights) / 100.0
    lut = []
    for value in range(256):
        x = value / 255.0
        shadow_weight = max(0.0, 1.0 - x * 2.0)
        mid_weight = max(0.0, 1.0 - abs(x - 0.5) * 2.0)
        highlight_weight = max(0.0, x * 2.0 - 1.0)
        y = x + shadows * shadow_weight * 0.22 + mids * mid_weight * 0.18 + highlights * highlight_weight * 0.22
        lut.append(_clamp_byte(y * 255))
    return image.point(lut * 3)


def apply_hsl_adjustments(image: Image.Image, recipe: EditRecipe) -> Image.Image:
    hsv = image.convert("HSV")
    h, s, v = hsv.split()
    hue_ranges = (
        ((0, 12), recipe.red_saturation),
        ((13, 28), recipe.orange_saturation),
        ((29, 48), recipe.yellow_saturation),
        ((49, 100), recipe.green_saturation),
        ((101, 135), recipe.aqua_saturation),
        ((136, 175), recipe.blue_saturation),
        ((176, 210), recipe.purple_saturation),
        ((211, 255), recipe.magenta_saturation),
    )
    lum = _clamp_percent(recipe.hsl_luminance) / 100.0
    # Vectorized replacement for the former per-pixel Python loop (byte-identical).
    # Per-hue saturation is a 256-entry lookup by hue; ranges are contiguous and
    # non-overlapping, so first-match ordering is preserved.
    hue_arr = np.asarray(h, dtype=np.uint8)
    s_arr = np.asarray(s, dtype=np.float64)
    v_arr = np.asarray(v, dtype=np.float64)
    sat_lut = np.zeros(256, dtype=np.float64)
    for (low, high), amount in hue_ranges:
        sat_lut[low : high + 1] = _clamp_percent(amount) / 100.0
    sat_delta = sat_lut[hue_arr]
    # round(s*1)==s where sat_delta is 0, so applying everywhere matches the
    # loop's "only touch pixels whose hue is in a band" behaviour exactly.
    s_new = np.clip(np.rint(s_arr * (1.0 + sat_delta)), 0, 255).astype(np.uint8)
    if lum:
        v_new = np.clip(np.rint(v_arr * (1.0 + lum * 0.35)), 0, 255).astype(np.uint8)
    else:
        v_new = np.asarray(v, dtype=np.uint8)
    merged = Image.merge(
        "HSV",
        (h, Image.fromarray(s_new, "L"), Image.fromarray(v_new, "L")),
    )
    return merged.convert("RGB")


def apply_grain(image: Image.Image, amount: float) -> Image.Image:
    amount = abs(_clamp_percent(amount)) / 100.0
    if amount == 0:
        return image
    noise = Image.effect_noise(image.size, 60).convert("L")
    noise_rgb = ImageOps.colorize(noise, (105, 105, 105), (150, 150, 150)).convert("RGB")
    return Image.blend(image, Image.blend(image, noise_rgb, 0.45), min(0.35, amount * 0.35))


def apply_perspective(image: Image.Image, horizontal: float, vertical: float) -> Image.Image:
    horizontal = _clamp_percent(horizontal) / 100.0
    vertical = _clamp_percent(vertical) / 100.0
    if not horizontal and not vertical:
        return image
    width, height = image.size
    x_shift = width * horizontal * 0.12
    y_shift = height * vertical * 0.12
    coeffs = (
        1,
        horizontal * 0.18,
        -x_shift,
        vertical * 0.18,
        1,
        -y_shift,
    )
    return image.transform(image.size, Image.Transform.AFFINE, coeffs, resample=Image.Resampling.BICUBIC)


def reduce_chromatic_aberration(image: Image.Image, amount: float) -> Image.Image:
    amount = abs(_clamp_percent(amount)) / 100.0
    if amount == 0:
        return image
    red, green, blue = image.split()
    shrink = max(1, int(round(amount * 2)))
    red = ImageChops.offset(red, -shrink, 0).filter(ImageFilter.GaussianBlur(radius=amount * 0.35))
    blue = ImageChops.offset(blue, shrink, 0).filter(ImageFilter.GaussianBlur(radius=amount * 0.35))
    corrected = Image.merge("RGB", (red, green, blue))
    return Image.blend(image, corrected, min(0.7, amount))


def heal_spot(image: Image.Image, x: int, y: int, radius: int, strength: float = 0.8) -> Image.Image:
    if radius <= 0:
        raise ValueError("radius must be greater than 0")

    strength = max(0.0, min(1.0, float(strength)))
    base = _exif_rgb(image)
    filter_size = max(3, radius // 2 * 2 + 1)
    filtered = base.filter(ImageFilter.MedianFilter(size=filter_size))
    mask = Image.new("L", base.size, 0)
    draw = ImageDraw.Draw(mask)

    x = int(x)
    y = int(y)
    radius = int(radius)
    for current_radius in range(radius, 0, -1):
        alpha = int(255 * strength * (current_radius / radius))
        draw.ellipse(
            (
                x - current_radius,
                y - current_radius,
                x + current_radius,
                y + current_radius,
            ),
            fill=alpha,
        )

    return Image.composite(filtered, base, mask)


def clone_stamp(
    image: Image.Image,
    source: Tuple[int, int],
    target: Tuple[int, int],
    radius: int,
    strength: float = 0.85,
) -> Image.Image:
    base = _exif_rgb(image)
    radius = max(1, int(radius))
    sx, sy = source
    tx, ty = target
    source_box = (sx - radius, sy - radius, sx + radius, sy + radius)
    patch = base.crop(source_box)
    mask = Image.new("L", patch.size, 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, patch.width - 1, patch.height - 1), fill=_clamp_byte(255 * max(0.0, min(1.0, strength))))
    mask = mask.filter(ImageFilter.GaussianBlur(radius=max(1, radius * 0.25)))
    result = base.copy()
    result.paste(patch, (tx - radius, ty - radius), mask)
    return result


def remove_red_eye(image: Image.Image, x: int, y: int, radius: int) -> Image.Image:
    base = _exif_rgb(image)
    radius = max(2, int(radius))
    box = (max(0, x - radius), max(0, y - radius), min(base.width, x + radius), min(base.height, y + radius))
    patch = base.crop(box)
    pixels = patch.load()
    for py in range(patch.height):
        for px in range(patch.width):
            red, green, blue = pixels[px, py]
            if red > 80 and red > green * 1.35 and red > blue * 1.35:
                replacement = int((green + blue) / 2)
                pixels[px, py] = (replacement, green, blue)
    result = base.copy()
    result.paste(patch, box[:2])
    return result


def auto_remove_dust(image: Image.Image, radius: int = 5, threshold: int = 38, max_spots: int = 80) -> Image.Image:
    base = _exif_rgb(image)
    small = ImageOps.grayscale(base)
    median = small.filter(ImageFilter.MedianFilter(size=max(3, radius * 2 + 1)))
    diff = ImageChops.subtract(median, small)
    candidates = diff.point(lambda value: 255 if value > threshold else 0)
    result = base
    found = 0
    step = max(2, radius)
    pixels = candidates.load()
    for y in range(radius, base.height - radius, step):
        for x in range(radius, base.width - radius, step):
            if pixels[x, y] > 0:
                result = heal_spot(result, x, y, radius, strength=0.75)
                found += 1
                if found >= max_spots:
                    return result
    return result
