from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import json
import math
from pathlib import Path
from typing import Any, Optional, Tuple, Union

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
    texture: float = 0.0
    grain: float = 0.0
    curve_shadows: float = 0.0
    curve_mids: float = 0.0
    curve_highlights: float = 0.0
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
        for key, value in asdict(override).items():
            if value not in (0, 0.0, None):
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

        if self.vignette_correction:
            working = apply_vignette(working, -_clamp_percent(self.vignette_correction) / 100.0)
        if self.vignette:
            working = apply_vignette(working, _clamp_percent(self.vignette) / 100.0)
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


def apply_vignette(image: Image.Image, amount: float) -> Image.Image:
    width, height = image.size
    center_x = width / 2.0
    center_y = height / 2.0
    max_distance = math.sqrt(center_x * center_x + center_y * center_y)
    mask = Image.new("L", image.size, 0)
    pixels = mask.load()

    for y in range(height):
        for x in range(width):
            distance = math.sqrt((x - center_x) ** 2 + (y - center_y) ** 2) / max_distance
            value = max(0.0, min(1.0, (distance - 0.22) / 0.78)) ** 1.8
            pixels[x, y] = _clamp_byte(value * 255 * min(1.0, abs(amount)))

    if amount > 0:
        adjusted = ImageEnhance.Brightness(image).enhance(0.45)
    else:
        adjusted = ImageEnhance.Brightness(image).enhance(1.35)
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
    hp = h.load()
    sp = s.load()
    vp = v.load()
    lum = _clamp_percent(recipe.hsl_luminance) / 100.0
    for y in range(image.height):
        for x in range(image.width):
            hue = hp[x, y]
            sat_delta = 0.0
            for (low, high), amount in hue_ranges:
                if low <= hue <= high:
                    sat_delta = _clamp_percent(amount) / 100.0
                    break
            if sat_delta:
                sp[x, y] = _clamp_byte(sp[x, y] * (1.0 + sat_delta))
            if lum:
                vp[x, y] = _clamp_byte(vp[x, y] * (1.0 + lum * 0.35))
    return Image.merge("HSV", (h, s, v)).convert("RGB")


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
