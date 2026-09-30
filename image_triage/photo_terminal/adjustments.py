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


def _composite_adjusted(base: Image.Image, adjusted: Image.Image, mask: Image.Image) -> Image.Image:
    return Image.composite(adjusted.convert("RGB"), base.convert("RGB"), mask.convert("L"))


_TONE_CURVE_INPUT = np.asarray(
    (0.0, 0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95, 1.0),
    dtype=np.float32,
)

# Endpoint curves measured from matched Photoshop Camera Raw screenshots. The
# intermediate slider positions blend continuously with the identity curve.
_TONE_CURVE_ENDPOINTS: dict[str, tuple[np.ndarray, np.ndarray]] = {
    "contrast": (
        np.asarray((0.08, 0.112, 0.221, 0.327, 0.421, 0.492, 0.545, 0.598, 0.662, 0.761, 0.902, 0.92), dtype=np.float32),
        np.asarray((0.0, 0.008, 0.042, 0.119, 0.238, 0.369, 0.540, 0.702, 0.816, 0.912, 0.980, 1.0), dtype=np.float32),
    ),
    "highlights": (
        np.asarray((0.0, 0.047, 0.122, 0.216, 0.320, 0.405, 0.467, 0.507, 0.567, 0.666, 0.804, 0.90), dtype=np.float32),
        np.asarray((0.0, 0.050, 0.134, 0.242, 0.363, 0.488, 0.622, 0.767, 0.866, 0.940, 0.988, 1.0), dtype=np.float32),
    ),
    "shadows": (
        np.asarray((0.0, 0.011, 0.066, 0.157, 0.284, 0.414, 0.520, 0.624, 0.720, 0.829, 0.942, 1.0), dtype=np.float32),
        np.asarray((0.08, 0.137, 0.240, 0.320, 0.407, 0.485, 0.571, 0.661, 0.747, 0.844, 0.948, 1.0), dtype=np.float32),
    ),
    "whites": (
        np.asarray((0.0, 0.048, 0.126, 0.222, 0.327, 0.417, 0.490, 0.557, 0.624, 0.719, 0.857, 0.92), dtype=np.float32),
        np.asarray((0.0, 0.051, 0.146, 0.279, 0.466, 0.653, 0.789, 0.887, 0.954, 1.0, 1.0, 1.0), dtype=np.float32),
    ),
    "blacks": (
        np.asarray((0.0, 0.001, 0.003, 0.021, 0.147, 0.276, 0.387, 0.528, 0.665, 0.803, 0.937, 1.0), dtype=np.float32),
        np.asarray((0.04, 0.075, 0.190, 0.306, 0.420, 0.517, 0.597, 0.672, 0.750, 0.844, 0.947, 1.0), dtype=np.float32),
    ),
}

_EXPOSURE_TWO_STOP_CURVES: dict[int, np.ndarray] = {
    -1: np.asarray(
        (0.0, 0.004, 0.007, 0.013, 0.022, 0.032, 0.043, 0.055, 0.075, 0.101, 0.149, 0.18),
        dtype=np.float32,
    ),
    1: np.asarray(
        (0.0, 0.598, 0.898, 0.970, 0.992, 0.997, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
        dtype=np.float32,
    ),
}


def _apply_luma_transform(
    image: Image.Image,
    mapper: Any,
    *,
    scale_chroma: bool,
) -> Image.Image:
    """Apply a luminance mapping in bounded-memory strips."""
    source = image.convert("RGB")
    result = Image.new("RGB", source.size)
    weights = np.asarray((0.2126, 0.7152, 0.0722), dtype=np.float32)
    for top in range(0, source.height, 128):
        bottom = min(source.height, top + 128)
        rgb = np.asarray(source.crop((0, top, source.width, bottom)), dtype=np.float32) / 255.0
        luma = rgb @ weights
        mapped = np.asarray(mapper(luma), dtype=np.float32)
        if scale_chroma:
            scale = np.divide(mapped, luma, out=np.ones_like(mapped), where=luma > 1e-6)
            adjusted = rgb * scale[..., None]
        else:
            # Equal channel deltas behave like a luminance/chroma color space
            # and avoid the saturation spikes caused by RGB multipliers.
            adjusted = rgb + (mapped - luma)[..., None]
        tile = Image.fromarray(
            np.rint(np.clip(adjusted, 0.0, 1.0) * 255.0).astype(np.uint8),
            mode="RGB",
        )
        result.paste(tile, (0, top))
    return result


def apply_exposure(image: Image.Image, exposure: float) -> Image.Image:
    """Apply the measured Camera Raw exposure response over a +/-5 EV range."""
    exposure = max(-5.0, min(5.0, float(exposure)))
    if abs(exposure) < 1e-9:
        return image.convert("RGB")
    direction = -1 if exposure < 0.0 else 1
    reference = _EXPOSURE_TWO_STOP_CURVES[direction]

    def map_exposure(luma: np.ndarray) -> np.ndarray:
        measured_stops = min(2.0, abs(exposure))
        endpoint = np.interp(luma, _TONE_CURVE_INPUT, reference).astype(np.float32)
        mapped = luma + (measured_stops / 2.0) * (endpoint - luma)
        extra_stops = max(0.0, abs(exposure) - 2.0)
        if extra_stops > 0.0:
            factor = 2.0**extra_stops
            mapped = mapped / factor if direction < 0 else np.clip(mapped * factor, 0.0, 1.0)
        return mapped

    # Positive exposure approaches white and loses saturation; negative
    # exposure scales emitted light and preserves chromaticity in the shadows.
    return _apply_luma_transform(
        image,
        map_exposure,
        scale_chroma=direction < 0,
    )


def apply_tone_control(image: Image.Image, control: str, amount: float) -> Image.Image:
    """Apply one Photoshop-style, monotonic tonal-range adjustment."""
    amount = _clamp_percent(amount)
    if abs(amount) < 1e-9:
        return image.convert("RGB")
    if control not in _TONE_CURVE_ENDPOINTS:
        raise ValueError(f"unsupported tone control: {control}")

    negative, positive = _TONE_CURVE_ENDPOINTS[control]
    endpoint = negative if amount < 0.0 else positive
    strength = abs(amount) / 100.0
    output_curve = _TONE_CURVE_INPUT + strength * (endpoint - _TONE_CURVE_INPUT)

    def map_tones(luma: np.ndarray) -> np.ndarray:
        return np.interp(luma, _TONE_CURVE_INPUT, output_curve).astype(np.float32)

    return _apply_luma_transform(
        image,
        map_tones,
        scale_chroma=amount > 0.0 and control in {"shadows", "whites", "blacks"},
    )


def apply_contrast(image: Image.Image, amount: float) -> Image.Image:
    return apply_tone_control(image, "contrast", amount)


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
    # Color Mixer. Saturation came first (and is what older sidecars carry);
    # hue and luminance complete the per-band HSL set. All -100..100, 0 neutral.
    red_hue: float = 0.0
    red_saturation: float = 0.0
    red_luminance: float = 0.0
    orange_hue: float = 0.0
    orange_saturation: float = 0.0
    orange_luminance: float = 0.0
    yellow_hue: float = 0.0
    yellow_saturation: float = 0.0
    yellow_luminance: float = 0.0
    green_hue: float = 0.0
    green_saturation: float = 0.0
    green_luminance: float = 0.0
    aqua_hue: float = 0.0
    aqua_saturation: float = 0.0
    aqua_luminance: float = 0.0
    blue_hue: float = 0.0
    blue_saturation: float = 0.0
    blue_luminance: float = 0.0
    purple_hue: float = 0.0
    purple_saturation: float = 0.0
    purple_luminance: float = 0.0
    magenta_hue: float = 0.0
    magenta_saturation: float = 0.0
    magenta_luminance: float = 0.0
    # Legacy global luminance lift, kept so old sidecars keep rendering the
    # same; the per-band controls above are what the GUI writes now.
    hsl_luminance: float = 0.0
    # Point Color: [{"h","s","l","hueShift","satShift","lumShift","range"}, ...]
    # sampled from the image, each shifting only colors near the sample.
    point_colors: Optional[list] = None
    # Color grading: four zones, each a hue (0..360) + saturation (0..100) +
    # luminance (-100..100). Blending/balance set how the zones meet.
    grading_shadow_hue: float = 0.0
    grading_shadow_sat: float = 0.0
    grading_shadow_lum: float = 0.0
    grading_midtone_hue: float = 0.0
    grading_midtone_sat: float = 0.0
    grading_midtone_lum: float = 0.0
    grading_highlight_hue: float = 0.0
    grading_highlight_sat: float = 0.0
    grading_highlight_lum: float = 0.0
    grading_global_hue: float = 0.0
    grading_global_sat: float = 0.0
    grading_global_lum: float = 0.0
    grading_blending: float = 50.0
    grading_balance: float = 0.0
    # Color calibration: shadow tint plus per-primary hue/saturation, applied
    # before the tonal stack the way a camera profile would be.
    calibration_shadow_tint: float = 0.0
    calibration_red_hue: float = 0.0
    calibration_red_saturation: float = 0.0
    calibration_green_hue: float = 0.0
    calibration_green_saturation: float = 0.0
    calibration_blue_hue: float = 0.0
    calibration_blue_saturation: float = 0.0
    # Defringe: desaturate purple/green edge fringing within a hue window.
    defringe_purple_amount: float = 0.0
    defringe_purple_hue_low: float = 30.0
    defringe_purple_hue_high: float = 70.0
    defringe_green_amount: float = 0.0
    defringe_green_hue_low: float = 40.0
    defringe_green_hue_high: float = 60.0
    # Grain shape. Amount is `grain` above; these two are its neutral-at-
    # non-zero shape controls, like the vignette's.
    grain_size: float = 25.0
    grain_roughness: float = 50.0
    vignette_correction: float = 0.0
    chromatic_aberration: float = 0.0
    perspective_x: float = 0.0
    perspective_y: float = 0.0
    rotate: float = 0.0
    crop: Optional[Tuple[int, int, int, int]] = None
    # Crop geometry. `crop_angle` is the straighten in degrees; it and the
    # flips fold into the crop's single affine rather than becoming extra
    # resamples, and the crop box is defined in *straightened* space so
    # dragging the angle does not change what is inside the box.
    crop_angle: float = 0.0
    flip_h: bool = False
    flip_v: bool = False
    crop_aspect: str = ""
    # Parametric retouch spots, source-pixel coordinates:
    # [{"id","kind","x","y","r","feather","strength","sx","sy","seq"}, ...].
    # Optional[list] rather than a default_factory on purpose: merged() reads
    # field.default, which is MISSING for a factory field, so a factory here
    # would quietly break preset merging.
    retouch: Optional[list] = None
    # AI background tool (blur / replace) — a compositing pass driven by a
    # BiRefNet matte, applied by the editor render backend (not apply() below,
    # which has no access to the matte). Only the settings persist; the matte
    # itself is resolved on demand from the subject-mask cache, keyed by source.
    background_mode: str = "off"          # off | blur | color (derived from the UI)
    background_amount: float = 0.0        # blur strength, 0..100 (0 = off)
    background_color: str = "#000000"
    # Depth-aware Lens Blur (Depth Anything map): blur grows with distance from
    # the focal plane. Applied by the render backend, not apply() below.
    lensblur_amount: float = 0.0          # 0 = off
    lensblur_focus: float = 0.7           # focal depth, 0 = far … 1 = near

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
        # Retouch runs first, in source space: RENDERER_ORDER puts it ahead of
        # every transform, and a heal must sample ungraded pixels.
        working = apply_retouch(working, self.retouch)
        # Flip, straighten and crop are one transform: two resamples in a row
        # cost quality for nothing, and the composed affine is the same matrix
        # the overlays map through (see image_triage/editor_geometry.py).
        working = apply_view_geometry(working, self)
        if self.rotate:
            working = working.rotate(self.rotate, expand=True, resample=Image.Resampling.BICUBIC)
        if self.perspective_x or self.perspective_y:
            working = apply_perspective(working, self.perspective_x, self.perspective_y)

        # Calibration sits at the head of the colour pipeline: it redefines the
        # primaries, so everything downstream should see the corrected colours.
        working = apply_color_calibration(working, self)

        if self.exposure:
            working = apply_exposure(working, self.exposure)

        if self.temperature or self.tint:
            working = apply_white_balance(working, self.temperature, self.tint)

        if self.contrast:
            working = apply_contrast(working, self.contrast)

        if self.highlights:
            working = apply_tone_control(working, "highlights", self.highlights)

        if self.shadows:
            working = apply_tone_control(working, "shadows", self.shadows)

        if self.whites:
            working = apply_tone_control(working, "whites", self.whites)

        if self.blacks:
            working = apply_tone_control(working, "blacks", self.blacks)

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

        if hsl_adjustments_active(self):
            working = apply_hsl_adjustments(working, self)

        working = apply_point_color(working, self)
        working = apply_color_grading(working, self)

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
        working = apply_defringe(working, self)
        if self.grain:
            working = apply_grain(
                working,
                self.grain,
                size=self.grain_size,
                roughness=self.grain_roughness,
            )

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


# Color Mixer bands, in PIL's 0..255 hue units. Contiguous and non-overlapping,
# so a hue-indexed LUT reproduces "first band that matches wins" exactly.
HSL_BANDS: tuple[tuple[str, tuple[int, int]], ...] = (
    ("red", (0, 12)),
    ("orange", (13, 28)),
    ("yellow", (29, 48)),
    ("green", (49, 100)),
    ("aqua", (101, 135)),
    ("blue", (136, 175)),
    ("purple", (176, 210)),
    ("magenta", (211, 255)),
)
# A hue slider at ±100 walks the hue this far around the 256-unit circle
# (≈ ±17°), which keeps a band's shift inside its neighbours as Lightroom does.
HSL_HUE_TRAVEL = 12.0


def apply_hsl_adjustments(image: Image.Image, recipe: EditRecipe) -> Image.Image:
    """Per-band hue / saturation / luminance (the Color Mixer).

    Saturation-only recipes stay byte-identical to the original implementation
    — older sidecars carry saturation alone, and the vectorization tests lock
    that path.
    """
    hsv = image.convert("HSV")
    h, s, v = hsv.split()
    lum = _clamp_percent(recipe.hsl_luminance) / 100.0
    hue_arr = np.asarray(h, dtype=np.uint8)
    s_arr = np.asarray(s, dtype=np.float32)
    v_arr = np.asarray(v, dtype=np.float32)

    sat_lut = np.zeros(256, dtype=np.float32)
    hue_lut = np.zeros(256, dtype=np.float32)
    band_lum_lut = np.zeros(256, dtype=np.float32)
    for band, (low, high) in HSL_BANDS:
        sat_lut[low : high + 1] = _clamp_percent(getattr(recipe, f"{band}_saturation")) / 100.0
        hue_lut[low : high + 1] = _clamp_percent(getattr(recipe, f"{band}_hue")) / 100.0
        band_lum_lut[low : high + 1] = _clamp_percent(getattr(recipe, f"{band}_luminance")) / 100.0

    # round(s*1)==s where the delta is 0, so applying everywhere matches the
    # original "only touch pixels whose hue is in a band" behaviour exactly.
    s_new = np.clip(np.rint(s_arr * (1.0 + sat_lut[hue_arr])), 0, 255).astype(np.uint8)

    if hue_lut.any():
        shifted = np.rint(hue_arr.astype(np.float32) + hue_lut[hue_arr] * HSL_HUE_TRAVEL)
        h_new = np.mod(shifted, 256.0).astype(np.uint8)
        h_img = Image.fromarray(h_new, "L")
    else:
        h_img = h

    band_lum = band_lum_lut[hue_arr]
    if lum or band_lum.any():
        # The legacy global lift and the per-band lift compose multiplicatively;
        # with no per-band values this reduces to the original expression.
        v_new = np.clip(
            np.rint(v_arr * (1.0 + lum * 0.35) * (1.0 + band_lum * 0.45)), 0, 255
        ).astype(np.uint8)
    else:
        v_new = np.asarray(v, dtype=np.uint8)
    merged = Image.merge(
        "HSV",
        (h_img, Image.fromarray(s_new, "L"), Image.fromarray(v_new, "L")),
    )
    return merged.convert("RGB")


def hsl_adjustments_active(recipe: EditRecipe) -> bool:
    if recipe.hsl_luminance:
        return True
    for band, _range in HSL_BANDS:
        if (
            getattr(recipe, f"{band}_hue")
            or getattr(recipe, f"{band}_saturation")
            or getattr(recipe, f"{band}_luminance")
        ):
            return True
    return False


def _hsv_to_rgb_unit(hue_degrees: float, saturation: float, value: float = 1.0) -> np.ndarray:
    """One HSV triple to a float RGB triple in 0..1."""
    hue = (float(hue_degrees) % 360.0) / 60.0
    chroma = value * saturation
    second = chroma * (1.0 - abs((hue % 2.0) - 1.0))
    sector = int(hue) % 6
    table = (
        (chroma, second, 0.0),
        (second, chroma, 0.0),
        (0.0, chroma, second),
        (0.0, second, chroma),
        (second, 0.0, chroma),
        (chroma, 0.0, second),
    )
    base = value - chroma
    return np.asarray(table[sector], dtype=np.float32) + base


def _luma(rgb: np.ndarray) -> np.ndarray:
    """Rec.601 luma of a float HxWx3 array in 0..1."""
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def _smoothstep(edge: np.ndarray) -> np.ndarray:
    edge = np.clip(edge, 0.0, 1.0)
    return edge * edge * (3.0 - 2.0 * edge)


COLOR_GRADING_ZONES: tuple[str, ...] = ("shadow", "midtone", "highlight", "global")


def color_grading_active(recipe: EditRecipe) -> bool:
    # Saturation is the gate: a hue with no saturation tints nothing, and
    # luminance alone still counts as an edit.
    return any(
        getattr(recipe, f"grading_{zone}_sat") or getattr(recipe, f"grading_{zone}_lum")
        for zone in COLOR_GRADING_ZONES
    )


def apply_color_grading(image: Image.Image, recipe: EditRecipe) -> Image.Image:
    """Four-zone color grading (shadows / midtones / highlights / global).

    Each zone tints by pushing chroma toward its hue without moving overall
    brightness, then optionally lifts or drops that zone's luminance. Balance
    slides the split point; blending widens how far the zones overlap.
    """
    if not color_grading_active(recipe):
        return image
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    luma = _luma(rgb)

    balance = max(-100.0, min(100.0, float(recipe.grading_balance))) / 100.0
    pivot = 0.5 - balance * 0.25
    width = 0.15 + max(0.0, min(100.0, float(recipe.grading_blending))) / 100.0 * 0.5

    weights = {
        "shadow": _smoothstep((pivot + width - luma) / (2.0 * width)),
        "highlight": _smoothstep((luma - (pivot - width)) / (2.0 * width)),
        "midtone": _smoothstep(1.0 - np.abs(luma - pivot) / width),
        "global": np.ones_like(luma),
    }

    out = rgb
    for zone in COLOR_GRADING_ZONES:
        saturation = max(0.0, min(100.0, float(getattr(recipe, f"grading_{zone}_sat")))) / 100.0
        luminance = _clamp_percent(getattr(recipe, f"grading_{zone}_lum")) / 100.0
        if not saturation and not luminance:
            continue
        weight = weights[zone][..., None]
        if saturation:
            tint = _hsv_to_rgb_unit(float(getattr(recipe, f"grading_{zone}_hue")), 1.0)
            # Subtracting the tint's own luma keeps the push chromatic: the
            # frame gets the colour without also getting brighter.
            direction = tint - float(tint[0] * 0.299 + tint[1] * 0.587 + tint[2] * 0.114)
            out = out + weight * (saturation * 0.5) * direction
        if luminance:
            out = out * (1.0 + weight * luminance * 0.5)
    return Image.fromarray(np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8), "RGB")


def color_calibration_active(recipe: EditRecipe) -> bool:
    return bool(
        recipe.calibration_shadow_tint
        or recipe.calibration_red_hue
        or recipe.calibration_red_saturation
        or recipe.calibration_green_hue
        or recipe.calibration_green_saturation
        or recipe.calibration_blue_hue
        or recipe.calibration_blue_saturation
    )


def _calibrated_primary(base_hue: float, hue_shift: float, saturation: float) -> np.ndarray:
    """One rotated / resaturated primary, as a float RGB column.

    Saturation pushes the primary away from its own gray rather than scaling
    the vector — scaling would change the primary's luminance and show up as a
    brightness jump instead of a colour shift.
    """
    hue = base_hue + _clamp_percent(hue_shift) / 100.0 * 30.0
    color = _hsv_to_rgb_unit(hue, 1.0)
    gray = float(color[0] * 0.299 + color[1] * 0.587 + color[2] * 0.114)
    sat = max(0.0, min(2.0, 1.0 + _clamp_percent(saturation) / 100.0))
    return gray + (color - gray) * sat


def apply_color_calibration(image: Image.Image, recipe: EditRecipe) -> Image.Image:
    """Camera-profile style calibration: rotate and resaturate each primary.

    Rebuilding the three primaries and recombining is one 3x3 matrix multiply,
    which is why this is cheap enough to sit in the per-tick pipeline.
    """
    if not color_calibration_active(recipe):
        return image
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    matrix = np.stack(
        (
            _calibrated_primary(0.0, recipe.calibration_red_hue, recipe.calibration_red_saturation),
            _calibrated_primary(120.0, recipe.calibration_green_hue, recipe.calibration_green_saturation),
            _calibrated_primary(240.0, recipe.calibration_blue_hue, recipe.calibration_blue_saturation),
        ),
        axis=1,
    )
    # Normalize the rows so the matrix still maps white to white — without this
    # a calibration change reads as an exposure change.
    row_sums = matrix.sum(axis=1, keepdims=True)
    matrix = np.divide(
        matrix,
        row_sums,
        out=np.eye(3, dtype=np.float32),
        where=np.abs(row_sums) > 1e-6,
    )
    out = rgb @ matrix.T
    tint = _clamp_percent(recipe.calibration_shadow_tint) / 100.0
    if tint:
        # Green/magenta shift that fades out as the frame brightens, which is
        # what "shadow tint" means on a calibration panel.
        shadow_weight = (1.0 - np.clip(_luma(rgb), 0.0, 1.0))[..., None] ** 2
        shift = np.asarray((tint * 0.06, -tint * 0.06, tint * 0.06), dtype=np.float32)
        out = out + shadow_weight * shift
    return Image.fromarray(np.clip(np.rint(out * 255.0), 0, 255).astype(np.uint8), "RGB")


def defringe_active(recipe: EditRecipe) -> bool:
    return bool(recipe.defringe_purple_amount or recipe.defringe_green_amount)


def _edge_weight(luma: np.ndarray) -> np.ndarray:
    """Normalized gradient magnitude — fringing lives on edges, so this is what
    stops a defringe pass desaturating a flat purple flower."""
    gy = np.zeros_like(luma)
    gx = np.zeros_like(luma)
    gy[1:-1, :] = luma[2:, :] - luma[:-2, :]
    gx[:, 1:-1] = luma[:, 2:] - luma[:, :-2]
    magnitude = np.sqrt(gx * gx + gy * gy)
    peak = float(magnitude.max())
    if peak <= 1e-6:
        return np.zeros_like(luma)
    return _smoothstep(magnitude / peak * 3.0)


def apply_defringe(image: Image.Image, recipe: EditRecipe) -> Image.Image:
    """Desaturate purple and green fringing inside a hue window, on edges only.

    Hue is only ever *read* from HSV; the result is blended in RGB. Writing an
    HSV array back would requantize every pixel, so a defringe that removes
    nothing would still degrade the frame.
    """
    if not defringe_active(recipe):
        return image
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    hsv = np.asarray(image.convert("HSV"), dtype=np.float32)
    hue = hsv[..., 0]
    edges = _edge_weight(np.asarray(image.convert("L"), dtype=np.float32) / 255.0)

    removal = np.zeros_like(hue)
    for amount_key, low_key, high_key, band in (
        ("defringe_purple_amount", "defringe_purple_hue_low", "defringe_purple_hue_high", (176, 232)),
        ("defringe_green_amount", "defringe_green_hue_low", "defringe_green_hue_high", (49, 135)),
    ):
        amount = max(0.0, min(20.0, float(getattr(recipe, amount_key)))) / 20.0
        if not amount:
            continue
        # The hue sliders are 0..100 across the band, so the window the user
        # sets maps onto the band's own hue span rather than the whole circle.
        span = band[1] - band[0]
        low = band[0] + span * max(0.0, min(100.0, float(getattr(recipe, low_key)))) / 100.0
        high = band[0] + span * max(0.0, min(100.0, float(getattr(recipe, high_key)))) / 100.0
        if high <= low:
            continue
        inside = (hue >= low) & (hue <= high)
        removal = np.maximum(removal, np.where(inside, amount, 0.0))

    strength = removal * edges
    if not strength.any():
        return image
    # Pull the fringe toward its own luma — that is what removing a fringe is,
    # and it leaves untouched pixels bit-identical.
    luma = _luma(rgb / 255.0)[..., None] * 255.0
    out = rgb + (luma - rgb) * strength[..., None]
    return Image.fromarray(np.clip(np.rint(out), 0, 255).astype(np.uint8), "RGB")


def point_colors_active(recipe: EditRecipe) -> bool:
    return bool(recipe.point_colors)


def apply_point_color(image: Image.Image, recipe: EditRecipe) -> Image.Image:
    """Point Color: shift hue / saturation / luminance near a sampled color.

    Each entry falls off with distance from its sample in HSV, so only the
    colors the user picked move.
    """
    if not point_colors_active(recipe):
        return image
    original = np.asarray(image.convert("RGB"), dtype=np.float32)
    hsv = np.asarray(image.convert("HSV"), dtype=np.float32)
    hue, sat, val = hsv[..., 0].copy(), hsv[..., 1].copy(), hsv[..., 2].copy()
    # Tracks how much any sample reaches each pixel, so pixels no sample
    # touches come back exactly as they went in rather than HSV-requantized.
    touched = np.zeros_like(hue)
    for entry in recipe.point_colors or ():
        try:
            target_h = float(entry["h"]) % 256.0
            target_s = float(entry["s"])
            target_v = float(entry["l"])
        except (KeyError, TypeError, ValueError):
            continue
        reach = max(1.0, min(100.0, float(entry.get("range", 25.0))))
        # Hue distance wraps; saturation/value distances are looser so a
        # sample still catches the same colour in shade.
        hue_distance = np.abs(((hue - target_h + 128.0) % 256.0) - 128.0) / (reach * 0.6)
        sat_distance = np.abs(sat - target_s) / (reach * 2.4)
        val_distance = np.abs(val - target_v) / (reach * 3.2)
        weight = _smoothstep(
            1.0 - np.sqrt(hue_distance**2 + sat_distance**2 + val_distance**2)
        )
        if not weight.any():
            continue
        touched = np.maximum(touched, weight)
        hue = hue + weight * (_clamp_percent(entry.get("hueShift", 0.0)) / 100.0) * HSL_HUE_TRAVEL
        sat = sat * (1.0 + weight * _clamp_percent(entry.get("satShift", 0.0)) / 100.0)
        val = val * (1.0 + weight * _clamp_percent(entry.get("lumShift", 0.0)) / 100.0 * 0.45)
    if not touched.any():
        return image
    hsv[..., 0] = np.mod(np.rint(hue), 256.0)
    hsv[..., 1] = np.clip(np.rint(sat), 0, 255)
    hsv[..., 2] = np.clip(np.rint(val), 0, 255)
    shifted = np.asarray(
        Image.fromarray(hsv.astype(np.uint8), "HSV").convert("RGB"), dtype=np.float32
    )
    blend = touched[..., None]
    out = original + (shifted - original) * blend
    return Image.fromarray(np.clip(np.rint(out), 0, 255).astype(np.uint8), "RGB")


GRAIN_DEFAULTS = {"size": 25.0, "roughness": 50.0}


def apply_grain(
    image: Image.Image,
    amount: float,
    *,
    size: float = 25.0,
    roughness: float = 50.0,
) -> Image.Image:
    """Film grain. ``size`` grows the grain cell, ``roughness`` its contrast.

    Bigger grain is generated small and scaled up rather than blurred, so the
    cells stay crisp the way real grain does.
    """
    amount = abs(_clamp_percent(amount)) / 100.0
    if amount == 0:
        return image
    width, height = image.size
    cell = 1.0 + max(0.0, min(100.0, float(size))) / 100.0 * 3.0
    sigma = 30.0 + max(0.0, min(100.0, float(roughness))) / 100.0 * 70.0
    noise_size = (max(1, int(width / cell)), max(1, int(height / cell)))
    noise = Image.effect_noise(noise_size, sigma).convert("L")
    if noise_size != (width, height):
        noise = noise.resize((width, height), Image.Resampling.BILINEAR)
    noise_rgb = ImageOps.colorize(noise, (105, 105, 105), (150, 150, 150)).convert("RGB")
    return Image.blend(image, Image.blend(image, noise_rgb, 0.45), min(0.35, amount * 0.35))


def view_geometry_active(recipe: "EditRecipe") -> bool:
    return bool(recipe.crop or recipe.crop_angle or recipe.flip_h or recipe.flip_v)


def apply_view_geometry(image: Image.Image, recipe: "EditRecipe") -> Image.Image:
    """Flip + straighten + crop, as one affine resample.

    The coefficients come from ``ViewTransform.affine`` so the pixels and every
    on-canvas overlay go through the identical mapping.
    """
    if not view_geometry_active(recipe):
        return image
    from image_triage.editor_geometry import view_transform_for

    # The legacy rotate field is still performed immediately after this call.
    # Exclude it from this affine while allowing display/overlay transforms to
    # include it and describe the final oriented frame.
    view = view_transform_for(recipe, image.size, include_rotate=False)
    if view.is_identity():
        return image
    return image.transform(
        view.frame_size(),
        Image.Transform.AFFINE,
        view.affine(),
        resample=Image.Resampling.BICUBIC,
    )


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


# --- retouch ----------------------------------------------------------------
# These run on every render tick once a photo carries spots, so they are all
# patch-local: bounded by the spot own box, never the frame. The originals
# median-filtered the whole image per spot and looped in Python per pixel,
# which is the exact class of bug this module has already had to fix once.


def _patch_box(
    x: float, y: float, radius: float, width: int, height: int, pad: int = 0
) -> Tuple[int, int, int, int]:
    """Clamped integer box around a spot, optionally padded."""
    reach = radius + pad
    left = max(0, int(math.floor(x - reach)))
    top = max(0, int(math.floor(y - reach)))
    right = min(width, int(math.ceil(x + reach)) + 1)
    bottom = min(height, int(math.ceil(y + reach)) + 1)
    return left, top, right, bottom


def _radial_alpha(
    box: Tuple[int, int, int, int],
    centre_x: float,
    centre_y: float,
    radius: float,
    feather: float,
    strength: float,
) -> np.ndarray:
    """Feathered disc coverage in 0..1, shaped for the box."""
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return np.zeros((0, 0), dtype=np.float32)
    ys = np.arange(top, bottom, dtype=np.float32)[:, None] - centre_y
    xs = np.arange(left, right, dtype=np.float32)[None, :] - centre_x
    distance = np.sqrt(xs * xs + ys * ys)
    softness = max(0.0, min(100.0, float(feather))) / 100.0
    inner = radius * (1.0 - softness)
    if radius - inner < 1e-3:
        alpha = (distance <= radius).astype(np.float32)
    else:
        alpha = np.clip((radius - distance) / (radius - inner), 0.0, 1.0)
        alpha = alpha * alpha * (3.0 - 2.0 * alpha)  # smoothstep
    return alpha * max(0.0, min(1.0, float(strength)))


def _blend_patch(
    arr: np.ndarray,
    box: Tuple[int, int, int, int],
    patch: np.ndarray,
    alpha: np.ndarray,
) -> None:
    left, top, right, bottom = box
    if right <= left or bottom <= top:
        return
    target = arr[top:bottom, left:right].astype(np.float32)
    weight = alpha[..., None]
    arr[top:bottom, left:right] = np.clip(
        np.rint(target + (patch.astype(np.float32) - target) * weight), 0, 255
    ).astype(np.uint8)


def heal_spot_patch(
    arr: np.ndarray,
    x: float,
    y: float,
    radius: float,
    *,
    feather: float = 50.0,
    strength: float = 0.8,
) -> None:
    """Median-blend a blemish away, in place, over the spot box only."""
    height, width = arr.shape[:2]
    pad = HEAL_MEDIAN_SIZE // 2 + 1
    outer = _patch_box(x, y, radius, width, height, pad=pad)
    inner = _patch_box(x, y, radius, width, height)
    if inner[2] <= inner[0] or inner[3] <= inner[1]:
        return
    # Filter the padded neighbourhood, then trim the pad off, so the median has
    # real context at the spot edge instead of a clamped border.
    #
    # PIL's MedianFilter is O(pixels * window^2) and its window would have to
    # grow with the spot to keep erasing texture — which made a big spot cost
    # hundreds of milliseconds on every render tick. Instead the window is
    # fixed and *the patch* is shrunk, so a heal costs about the same whatever
    # its radius. The replacement is low-frequency fill either way, so nothing
    # visible is lost.
    region = np.ascontiguousarray(arr[outer[1]:outer[3], outer[0]:outer[2]])
    patch_h, patch_w = region.shape[:2]
    scale = max(1, int(math.ceil(max(patch_w, patch_h) / HEAL_WORKING_SIZE)))
    source = Image.fromarray(region, "RGB")
    if scale > 1:
        small = source.resize(
            (max(1, patch_w // scale), max(1, patch_h // scale)),
            Image.Resampling.BILINEAR,
        )
        small = small.filter(ImageFilter.MedianFilter(size=HEAL_MEDIAN_SIZE))
        source = small.resize((patch_w, patch_h), Image.Resampling.BILINEAR)
    else:
        source = source.filter(ImageFilter.MedianFilter(size=HEAL_MEDIAN_SIZE))
    filtered = np.asarray(source)
    trim_x = inner[0] - outer[0]
    trim_y = inner[1] - outer[1]
    patch = filtered[
        trim_y:trim_y + (inner[3] - inner[1]),
        trim_x:trim_x + (inner[2] - inner[0]),
    ]
    _blend_patch(arr, inner, patch, _radial_alpha(inner, x, y, radius, feather, strength))


def clone_stamp_patch(
    arr: np.ndarray,
    source_x: float,
    source_y: float,
    x: float,
    y: float,
    radius: float,
    *,
    feather: float = 50.0,
    strength: float = 0.85,
) -> None:
    """Copy a disc from the source to the target, in place."""
    height, width = arr.shape[:2]
    target = _patch_box(x, y, radius, width, height)
    if target[2] <= target[0] or target[3] <= target[1]:
        return
    offset_x = int(round(source_x - x))
    offset_y = int(round(source_y - y))
    src_left = target[0] + offset_x
    src_top = target[1] + offset_y
    src_right = src_left + (target[2] - target[0])
    src_bottom = src_top + (target[3] - target[1])
    if src_left < 0 or src_top < 0 or src_right > width or src_bottom > height:
        return  # source hangs off the frame; nothing sensible to copy
    # Copy before writing: source and target discs may overlap, and blending
    # from a half-written array smears.
    patch = arr[src_top:src_bottom, src_left:src_right].copy()
    _blend_patch(arr, target, patch, _radial_alpha(target, x, y, radius, feather, strength))


def red_eye_patch(
    arr: np.ndarray,
    x: float,
    y: float,
    radius: float,
    *,
    feather: float = 50.0,
    strength: float = 1.0,
) -> None:
    """Neutralize red pupils inside the disc, in place.

    Unlike the original this is confined to the feathered disc rather than the
    whole bounding box, which is correct for a click-placed spot and a
    deliberate improvement on the old behaviour.
    """
    height, width = arr.shape[:2]
    box = _patch_box(x, y, radius, width, height)
    if box[2] <= box[0] or box[3] <= box[1]:
        return
    patch = arr[box[1]:box[3], box[0]:box[2]].astype(np.int16)
    red, green, blue = patch[..., 0], patch[..., 1], patch[..., 2]
    is_red = (red > 80) & (red > green * 1.35) & (red > blue * 1.35)
    replacement = (green + blue) // 2
    alpha = _radial_alpha(box, x, y, radius, feather, strength) * is_red
    corrected = patch.copy()
    corrected[..., 0] = np.rint(red * (1.0 - alpha) + replacement * alpha)
    _blend_patch(arr, box, corrected.astype(np.uint8), np.ones_like(alpha))


# Heal tuning. The median window is fixed and the patch is downsampled to at
# most HEAL_WORKING_SIZE on its long side, so cost is bounded by the window
# rather than by the spot's radius.
HEAL_MEDIAN_SIZE = 9
HEAL_WORKING_SIZE = 64

RETOUCH_KINDS = ("heal", "clone", "red_eye")


def apply_retouch(image: Image.Image, spots: Any) -> Image.Image:
    """Apply a parametric spot list. Returns ``image`` untouched when empty."""
    if not spots:
        return image
    # convert() copies even when the mode already matches, and the array copy
    # below is the one we actually need — skip the redundant one.
    base = image if image.mode == "RGB" else image.convert("RGB")
    arr = np.array(base, dtype=np.uint8, copy=True)
    for spot in spots:
        try:
            kind = str(spot["kind"])
            x = float(spot["x"])
            y = float(spot["y"])
            radius = float(spot["r"])
        except (KeyError, TypeError, ValueError):
            continue
        if radius <= 0:
            continue
        feather = float(spot.get("feather", 50.0))
        strength = float(spot.get("strength", 0.8))
        if kind == "heal":
            heal_spot_patch(arr, x, y, radius, feather=feather, strength=strength)
        elif kind == "clone" and spot.get("sx") is not None:
            clone_stamp_patch(
                arr,
                float(spot["sx"]),
                float(spot["sy"]),
                x,
                y,
                radius,
                feather=feather,
                strength=strength,
            )
        elif kind == "red_eye":
            red_eye_patch(arr, x, y, radius, feather=feather, strength=1.0)
    return Image.fromarray(arr, "RGB")


def heal_spot(image: Image.Image, x: int, y: int, radius: int, strength: float = 0.8) -> Image.Image:
    """Single-spot heal (the CLI entry point), over the patch kernel."""
    if radius <= 0:
        raise ValueError("radius must be greater than 0")
    base = _exif_rgb(image)
    arr = np.array(base, dtype=np.uint8, copy=True)
    heal_spot_patch(arr, float(x), float(y), float(radius), feather=100.0, strength=strength)
    return Image.fromarray(arr, "RGB")


def clone_stamp(
    image: Image.Image,
    source: Tuple[int, int],
    target: Tuple[int, int],
    radius: int,
    strength: float = 0.85,
) -> Image.Image:
    """Single clone (the CLI entry point), over the patch kernel."""
    base = _exif_rgb(image)
    arr = np.array(base, dtype=np.uint8, copy=True)
    clone_stamp_patch(
        arr,
        float(source[0]),
        float(source[1]),
        float(target[0]),
        float(target[1]),
        float(max(1, int(radius))),
        feather=50.0,
        strength=strength,
    )
    return Image.fromarray(arr, "RGB")


def remove_red_eye(image: Image.Image, x: int, y: int, radius: int) -> Image.Image:
    """Single red-eye fix (the CLI entry point), over the patch kernel.

    The old body walked the bounding box pixel by pixel in Python. This is the
    vectorized equivalent, confined to the disc rather than the box.
    """
    base = _exif_rgb(image)
    arr = np.array(base, dtype=np.uint8, copy=True)
    red_eye_patch(arr, float(x), float(y), float(max(2, int(radius))), feather=0.0)
    return Image.fromarray(arr, "RGB")


def auto_remove_dust(image: Image.Image, radius: int = 5, threshold: int = 38, max_spots: int = 80) -> Image.Image:
    base = _exif_rgb(image)
    small = ImageOps.grayscale(base)
    median = small.filter(ImageFilter.MedianFilter(size=max(3, radius * 2 + 1)))
    diff = ImageChops.subtract(median, small)
    candidates = diff.point(lambda value: 255 if value > threshold else 0)
    hits = np.asarray(candidates, dtype=np.uint8)
    step = max(2, radius)
    ys = np.arange(radius, max(radius + 1, base.height - radius), step)
    xs = np.arange(radius, max(radius + 1, base.width - radius), step)
    arr = np.array(base, dtype=np.uint8, copy=True)
    found = 0
    for y in ys:
        row = hits[y]
        for x in xs:
            if row[x] > 0:
                heal_spot_patch(arr, float(x), float(y), float(radius), feather=100.0, strength=0.75)
                found += 1
                if found >= max_spots:
                    return Image.fromarray(arr, "RGB")
    return Image.fromarray(arr, "RGB")
