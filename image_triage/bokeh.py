"""Bokeh: a depth-of-field blur laid over a photo by distance, using a monocular depth map.

Like Light Depth and Atmosphere, the effect belongs to its panel and shows nowhere else. The draft and a settings file beside the
recipe hold only the panel's settings (``bokeh``); the host lays the blur over the finished render (live preview, save and export
alike, after Atmosphere's fog: a lens blurs the fog too). This module is the arithmetic and the file handling, with no UI.

How the blur is made (after Wadhwa et al., "Synthetic Depth-of-Field with a Single-Camera Mobile Phone", and Google's Portrait Mode)
-----------------------------------------------------------------------------------------------------------------------------------
* **Distance** is the depth map stretched to its own range (0 nearest .. 1 farthest). **Focus Distance** is a place on that
  range; a band around it (the **sharp range**) stays sharp. Out of focus, the blur radius grows with the distance from the band,
  as the circle of confusion does, faster in front of the focus than behind it.
* **Layers, not a gather.** The picture is split into a handful of depth bands (a partition of unity: soft weights that sum to 1),
  each band is blurred with a kernel of its own radius, and the bands are composited back to front with "over". That is what
  makes a blurred foreground spread over the sharp subject while the sharp subject never smears into a blurred background.
* **The kernel is the bokeh shape** (Luminar's six: Ring, Circle, Ellipse, Hexagon, Heart, Star), laid over each band by FFT
  convolution.
* **Linear light and bright highlights.** The blur is done on linear light with the brightest parts of the picture lifted
  (Highlights), so a point of light spreads into a bright disc and not a grey smudge (Google blurs before tone mapping for the same
  reason). The result is re-encoded and clipped.
* **Grain.** Blurring wipes out sensor noise and leaves blurred parts looking like cardboard; fine noise is put back there.
* **Full-size pictures** are made from a blur worked out at a reduced size plus the sharp original, in the sums the compositing
  already makes: ``out = blurred_part + sharp_image x sharp_share``.

Depth maps are 8-bit, 255 = nearest (``depth_worker``).
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import light_depth

SIDECAR_TAIL = ".photocraft.bokeh.json"      # carried with the photo's other edit files (``edit_carry.EDIT_MARKERS``)
SIDECAR_VERSION = 1

SHAPE_NAMES = ("Ring", "Circle", "Ellipse", "Hexagon", "Heart", "Star")

# amount: how strong the blur is (the radius it grows to). focus: where on the photo's depth the sharp part lies (0 nearest .. 100
# farthest). range: how wide a band around it stays sharp. highlights: how much the bright parts of the picture are lifted before
# they are blurred (bokeh lights). shape: an index into SHAPE_NAMES. shown: the eye. depthModel: which depth model made the map.
DEFAULTS = {"amount": 0.0, "focus": 50.0, "range": 12.0, "highlights": 40.0, "shape": 1.0, "shown": 1.0, "depthModel": 2.0}
RANGES = {"amount": (0, 100), "focus": (0, 100), "range": (0, 100), "highlights": (0, 100), "shape": (0, 5), "shown": (0, 1),
          "depthModel": (0, 2)}

MAX_RADIUS = 0.03        # the blur radius at Amount 100, as a fraction of the picture's long side
FOREGROUND_GAIN = 1.25   # in front of the focus the blur grows faster than behind it
FALL_OFF = 0.45          # how far (in distance) from the sharp band the blur reaches its full radius
WORK_SIDE = 800          # the longest side the layers are blurred at; larger pictures are made from this plus the sharp original
GRAIN = 0.006            # the noise put back in blurred parts (in encoded 0..1 units)


def valid(settings) -> bool:
    return (isinstance(settings, dict) and settings.keys() == DEFAULTS.keys()
            and all(type(settings[key]) in (int, float) and math.isfinite(settings[key])
                    and low <= settings[key] <= high for key, (low, high) in RANGES.items()))


def normalized(settings) -> dict:
    """``settings`` with every missing or unusable value replaced by its default and every value inside its range."""
    result = dict(DEFAULTS)
    if isinstance(settings, dict):
        for key, (low, high) in RANGES.items():
            value = settings.get(key)
            if type(value) in (int, float) and math.isfinite(value):
                result[key] = float(max(low, min(high, value)))
    result["shape"] = float(round(result["shape"]))
    return result


def model_key(settings) -> str:
    """Which depth model ("small", "base" or "large") the settings use."""
    return light_depth.DEPTH_MODELS[int(round(normalized(settings)["depthModel"]))]


def active(settings) -> bool:
    """Whether the effect changes the photo: switched on and with some Amount."""
    settings = normalized(settings)
    return settings["amount"] > 0 and settings["shown"] > 0


def masks_needed(settings) -> int:
    """How many of a photo's masks the effect takes: none (the blur is laid over the finished picture)."""
    return 0


# --- distance and the blur radius ---------------------------------------------------------------------------------------------

def distance_of(near):
    """The depth map (float 0..1, 1 nearest) as a distance 0 (nearest) .. 1 (farthest), stretched to the range it really uses."""
    import numpy as np

    low, high = np.percentile(near, [1, 99])
    return np.clip((high - near) / max(float(high - low), 1e-3), 0.0, 1.0).astype(np.float32)


def auto_focus(depth) -> float:
    """A first Focus Distance for a photo: the distance of what is in the middle of the picture (0..100)."""
    import numpy as np

    distance = distance_of(light_depth.working_depth(depth, 256))
    height, width = distance.shape
    middle = distance[height // 3: 2 * height // 3, width // 3: 2 * width // 3]
    return float(np.clip(np.median(middle) * 100, 0, 100))


def focus_at(depth, x: float, y: float) -> float:
    """The Focus Distance (0..100) of the point (``x``, ``y``) of the picture, each 0..1: what a tap on the picture focuses on."""
    import numpy as np

    distance = distance_of(light_depth.working_depth(depth, 256))
    height, width = distance.shape
    column, row = int(min(width - 1, max(0, x * width))), int(min(height - 1, max(0, y * height)))
    patch = distance[max(0, row - 2): row + 3, max(0, column - 2): column + 3]
    return float(np.clip(np.median(patch) * 100, 0, 100))


def radius_at(distance, settings, long_side: float):
    """The blur radius (in pixels of a picture whose long side is ``long_side``) at distance ``distance`` (array or number)."""
    import numpy as np

    settings = normalized(settings)
    focus, half = settings["focus"] / 100, settings["range"] / 200
    z = np.asarray(distance, np.float32)
    excess = np.maximum(np.abs(z - focus) - half, 0.0)
    reach = np.clip(excess / FALL_OFF, 0.0, 1.0) ** 0.85
    nearer = np.where(z < focus, FOREGROUND_GAIN, 1.0)
    return reach * nearer * MAX_RADIUS * long_side * settings["amount"] / 100


# --- the kernels ---------------------------------------------------------------------------------------------------------------

def _smooth(values):
    import numpy as np

    values = np.clip(values, 0.0, 1.0)
    return values * values * (3 - 2 * values)


def kernel(shape: int, radius: float):
    """The blur kernel for bokeh shape ``shape`` (an index into SHAPE_NAMES) of ``radius`` pixels, as a float32 array that sums to 1."""
    import numpy as np

    radius = max(0.5, float(radius))
    half = int(math.ceil(radius * 1.25)) + 1
    steps = 3                                                                   # supersampling, for clean edges
    axis = (np.arange(-half * steps, half * steps + 1, dtype=np.float32)) / steps
    x, y = np.meshgrid(axis, axis)
    distance = np.hypot(x, y)
    name = SHAPE_NAMES[int(max(0, min(5, round(shape))))]
    if name in ("Circle", "Ring"):
        inside = distance <= radius
        weight = inside.astype(np.float32)
        if name == "Ring":
            weight *= 0.35 + 0.65 * _smooth((distance / radius - 0.5) / 0.5)    # a bright rim, as lenses with a mirror element make
    elif name == "Ellipse":
        weight = ((x / (radius * 1.2)) ** 2 + (y / (radius * 0.72)) ** 2 <= 1.0).astype(np.float32)
    elif name == "Hexagon":
        apothem = radius * math.cos(math.radians(30))
        edge = np.zeros_like(x)
        for angle in (0, 60, 120):
            a = math.radians(angle)
            edge = np.maximum(edge, np.abs(x * math.cos(a) + y * math.sin(a)))
        weight = (edge <= apothem).astype(np.float32)
    elif name == "Star":
        theta = np.arctan2(y, x)
        reach = radius * (0.42 + 0.58 * np.abs(np.cos(2.5 * (theta + math.pi / 2))) ** 1.6)
        weight = (distance <= reach).astype(np.float32)
    else:                                                                       # Heart
        u, v = x / (radius * 1.05), -(y - 0.12 * radius) / (radius * 1.05)
        weight = (((u * u + v * v - 1.0) ** 3 - u * u * v ** 3) <= 0.0).astype(np.float32)
    size = weight.shape[0] // steps
    cells = weight[: size * steps, : size * steps].reshape(size, steps, size, steps).mean(axis=(1, 3))
    total = float(cells.sum())
    return (cells / total).astype(np.float32) if total > 0 else np.ones((1, 1), np.float32)


def _fast_size(n: int) -> int:
    """The smallest length of the form 2^a 3^b 5^c that is at least ``n`` (the FFT is quick at those)."""
    while True:
        m = n
        for prime in (2, 3, 5):
            while m % prime == 0:
                m //= prime
        if m == 1:
            return n
        n += 1


def _convolve(stack, kernel_):
    """``stack`` (channels, height, width) convolved with ``kernel_`` (same size out, the kernel centred)."""
    import numpy as np

    height, width = stack.shape[1:]
    kh, kw = kernel_.shape
    padded = (_fast_size(height + kh - 1), _fast_size(width + kw - 1))
    spectrum = np.fft.rfft2(stack, s=padded) * np.fft.rfft2(kernel_, s=padded)
    out = np.fft.irfft2(spectrum, s=padded)
    top, left = kh // 2, kw // 2
    return out[:, top: top + height, left: left + width].astype(np.float32)


# --- colour --------------------------------------------------------------------------------------------------------------------

def to_linear(encoded):
    import numpy as np

    encoded = np.clip(encoded, 0.0, 1.0)
    return np.where(encoded <= 0.04045, encoded / 12.92, ((encoded + 0.055) / 1.055) ** 2.4).astype(np.float32)


def to_encoded(linear):
    import numpy as np

    linear = np.clip(linear, 0.0, 1.0)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055).astype(np.float32)


def _lift_highlights(linear, settings):
    """``linear`` with its brightest parts lifted, so a blurred light is still a bright disc."""
    import numpy as np

    strength = settings["highlights"] / 100
    if strength <= 0:
        return linear
    light = linear @ np.array([0.2126, 0.7152, 0.0722], np.float32)
    shown = to_encoded(light)
    gain = 1.0 + strength * 5.0 * _smooth((shown - 0.88) / 0.12) ** 2                   # only real lights (clipped or nearly) are lifted
    return linear * gain[..., None]


# --- the layers ----------------------------------------------------------------------------------------------------------------

def _centres(settings):
    """The distances the depth bands are centred on: an even spread, plus the two edges of the sharp band (so it is exactly sharp)."""
    import numpy as np

    focus, half = settings["focus"] / 100, settings["range"] / 200
    values = list(np.linspace(0.0, 1.0, 5)) + [max(0.0, focus - half), min(1.0, focus + half)]
    ordered = sorted(set(round(float(v), 4) for v in values))
    return np.array(ordered, np.float32)


def _weights(distance, centres):
    """Soft weights of each band at each pixel (a partition of unity): hats between neighbouring centres."""
    import numpy as np

    out = []
    count = len(centres)
    for k in range(count):
        weight = np.zeros(distance.shape, np.float32)
        if k > 0:
            span = max(float(centres[k] - centres[k - 1]), 1e-6)
            rising = np.clip((distance - centres[k - 1]) / span, 0.0, 1.0)
            weight = np.where(distance <= centres[k], rising, weight)
        else:
            weight = np.where(distance <= centres[k], 1.0, weight)
        if k < count - 1:
            span = max(float(centres[k + 1] - centres[k]), 1e-6)
            falling = np.clip((centres[k + 1] - distance) / span, 0.0, 1.0)
            weight = np.where(distance > centres[k], falling, weight)
        else:
            weight = np.where(distance > centres[k], 1.0, weight)
        out.append(weight.astype(np.float32))
    return out


def layers(linear, near, settings):
    """``(blurred, sharp_share)`` for ``linear`` (float height x width x 3, linear light) and its depth ``near`` (float 0..1, 1 nearest,
    the same size): the picture with the blur laid over it as ``blurred + linear x sharp_share`` (the part that comes from the
    layers that are in focus is not put in ``blurred``, so a bigger sharp copy of the picture can be used for it)."""
    import numpy as np

    settings = normalized(settings)
    height, width = linear.shape[:2]
    long_side = max(height, width)
    distance = distance_of(near)
    centres = _centres(settings)
    weights = _weights(distance, centres)
    radii = radius_at(centres, settings, long_side)
    lifted = _lift_highlights(linear, settings)
    blurred = np.zeros_like(linear)
    share = np.zeros((height, width), np.float32)
    transmit = np.ones((height, width), np.float32)
    for k in range(len(centres) - 1, -1, -1):                                       # far to near
        weight = weights[k]
        if not weight.any():
            continue
        radius = float(radii[k])
        if radius < 0.6:                                                              # in focus: the sharp picture itself
            share = share * (1 - weight) + weight
            blurred = blurred * (1 - weight[..., None])
            transmit = transmit * (1 - weight)
            continue
        stack = np.concatenate([(lifted * weight[..., None]).transpose(2, 0, 1), weight[None]], axis=0)
        done = _convolve(stack, kernel(settings["shape"], radius))
        colour, alpha = done[:3].transpose(1, 2, 0), np.clip(done[3], 0.0, 1.0)
        blurred = blurred * (1 - alpha[..., None]) + colour
        share = share * (1 - alpha)
        transmit = transmit * (1 - alpha)
    return blurred, share + transmit                                                  # what nothing covers is filled with the sharp picture


def _resize(array, width: int, height: int):
    """``array`` (float, height x width [x channels]) resized to ``width`` x ``height`` (bilinear)."""
    import numpy as np
    from PIL import Image

    if array.shape[1] == width and array.shape[0] == height:
        return array
    if array.ndim == 2:
        return np.asarray(Image.fromarray(array.astype(np.float32), mode="F").resize((width, height), Image.Resampling.BILINEAR), np.float32)
    channels = [np.asarray(Image.fromarray(np.ascontiguousarray(array[..., i]), mode="F").resize((width, height), Image.Resampling.BILINEAR), np.float32)
                for i in range(array.shape[2])]
    return np.stack(channels, axis=-1)


def render(encoded, depth, settings):
    """``encoded`` (float 0..1, height x width x 3, as shown) with the blur laid over it, and how much of it is blur (0..1)."""
    import numpy as np

    settings = normalized(settings)
    if not active(settings):
        return encoded, np.zeros(encoded.shape[:2], np.float32)
    height, width = encoded.shape[:2]
    scale = min(1.0, WORK_SIDE / max(height, width))
    work = (max(2, round(width * scale)), max(2, round(height * scale)))
    small = _resize(encoded, *work) if scale < 1 else encoded
    near = _resize(light_depth.working_depth(depth, max(work)), *work)
    blurred, share = layers(to_linear(small), near, settings)
    full = to_linear(encoded)
    blurred, share = _resize(blurred, width, height), _resize(share, width, height)
    out = to_encoded(blurred + full * share[..., None])
    cover = np.clip(1.0 - share, 0.0, 1.0)
    noise = np.random.default_rng(3).standard_normal(out.shape[:2]).astype(np.float32) * GRAIN
    return np.clip(out + (noise * cover)[..., None], 0.0, 1.0), cover


def bokeh_image(image, depth, settings):
    """``image`` (a QImage, any format) with the blur laid over it, as an 8-bit RGB QImage; ``image`` itself when there is none."""
    import numpy as np
    from PySide6.QtGui import QImage

    if not active(settings):
        return image
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    stride = rgb.bytesPerLine()
    flat = np.frombuffer(rgb.constBits(), np.uint8, count=stride * rgb.height()).reshape(rgb.height(), stride)
    pixels = flat[:, : rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3).astype(np.float32) / 255.0
    done, _ = render(pixels, depth, settings)
    out = np.ascontiguousarray(np.clip(done * 255 + 0.5, 0, 255).astype(np.uint8))
    return QImage(out.data, out.shape[1], out.shape[0], out.shape[1] * 3, QImage.Format.Format_RGB888).copy()


def _depth_of(image_path, settings):
    depth = light_depth.load_depth(image_path, model_key(settings))
    if depth is None:
        raise ValueError("Bokeh's depth map is missing. Open Bokeh to make it again.")
    return depth


def bokeh_frame(frame, image_path, settings):
    """A preview worker's ``(header, QImage)`` with the blur laid over the picture. Raises ValueError when the depth map is gone."""
    if not active(settings):
        return frame
    return frame[0], bokeh_image(frame[1], _depth_of(image_path, settings), settings)


def bokeh_file(source, destination, image_path, settings):
    """Write ``source`` (a full-size render, 8 or 16 bit) to ``destination`` (PNG) with the blur laid over it. The layers are blurred at a
    reduced size and the sharp original is kept for what is in focus (``layers``), so a camera-sized picture is made in strips and
    never held as floats all at once. Raises ValueError when the depth map is gone."""
    import numpy as np
    from PIL import Image
    from PySide6.QtCore import QIODevice, QSaveFile
    from PySide6.QtGui import QImage, QImageReader

    settings = normalized(settings)
    depth = _depth_of(image_path, settings)
    reader = QImageReader(str(source))
    size = reader.size()
    if size.width() <= 0 or size.height() <= 0 or size.width() * size.height() > 120_000_000:
        raise ValueError("The full-resolution picture has invalid dimensions")
    if 0 < QImageReader.allocationLimit() < 1024:
        QImageReader.setAllocationLimit(1024)
    image = reader.read()
    del reader
    if image.isNull():
        raise ValueError("The full-resolution picture could not be decoded")
    wide = image.depth() > 32
    image = image.convertToFormat(QImage.Format.Format_RGBA64 if wide else QImage.Format.Format_RGBA8888)
    height, width = image.height(), image.width()
    unit = np.uint16 if wide else np.uint8
    top = float(np.iinfo(unit).max)
    per_line = image.bytesPerLine() // np.dtype(unit).itemsize
    pixels = np.frombuffer(image.bits(), unit, count=per_line * height).reshape(height, per_line)[:, : width * 4].reshape(height, width, 4)
    scale = min(1.0, WORK_SIDE / max(height, width))
    work = (max(2, round(width * scale)), max(2, round(height * scale)))
    reduced = np.asarray(Image.fromarray(pixels[..., :3].astype(np.float32) / top, mode=None) if False else
                         _resize(pixels[:: max(1, round(1 / scale)), :: max(1, round(1 / scale)), :3].astype(np.float32) / top, *work), np.float32)
    near = _resize(light_depth.working_depth(depth, max(work)), *work)
    blurred, share = layers(to_linear(reduced), near, settings)
    rng = np.random.default_rng(3)
    for start in range(0, height, 512):
        end = min(height, start + 512)
        rows = (start / height, end / height)
        strip = pixels[start:end, :, :3].astype(np.float32) / top
        part = _strip(blurred, width, end - start, rows)
        mine = _strip(share, width, end - start, rows)
        mixed = to_encoded(part + to_linear(strip) * mine[..., None])
        cover = np.clip(1.0 - mine, 0.0, 1.0)
        noise = rng.standard_normal((end - start, width)).astype(np.float32) * GRAIN
        pixels[start:end, :, :3] = np.clip((mixed + (noise * cover)[..., None]) * top + 0.5, 0, top).astype(unit)
    output = QSaveFile(str(destination))
    if not output.open(QIODevice.WriteOnly) or not image.save(output, "PNG") or not output.commit():
        raise OSError("The picture with bokeh could not be saved")


def _strip(small, width: int, rows_high: int, fractions):
    """The part of ``small`` (float, height x width [x channels]) from ``fractions[0]`` to ``fractions[1]`` of its height, as a
    ``width`` x ``rows_high`` picture."""
    import numpy as np
    from PIL import Image

    top, bottom = small.shape[0] * fractions[0], small.shape[0] * fractions[1]
    box = (0.0, top, float(small.shape[1]), bottom)
    if small.ndim == 2:
        return np.asarray(Image.fromarray(np.ascontiguousarray(small), mode="F").resize((width, rows_high), Image.Resampling.BILINEAR, box=box), np.float32)
    channels = [np.asarray(Image.fromarray(np.ascontiguousarray(small[..., i]), mode="F").resize((width, rows_high), Image.Resampling.BILINEAR, box=box),
                           np.float32) for i in range(small.shape[2])]
    return np.stack(channels, axis=-1)


def with_masks(settings: dict, bokeh, image_path) -> dict:
    """The Camera Raw ``settings`` as the renderer should get them: without the host's ``bokeh`` entry (the blur is laid over the render
    afterwards, never made with masks)."""
    return {key: value for key, value in settings.items() if key != "bokeh"}


# --- the settings file beside the recipe -----------------------------------------------------------------------------------------

def sidecar_path(recipe_path) -> Path:
    """Where a photo's Bokeh settings are kept, next to its recipe (``<photo>.cameraraw.json``)."""
    recipe = Path(recipe_path)
    name = recipe.name
    stem = name[:-len(".cameraraw.json")] if name.endswith(".cameraraw.json") else name
    return recipe.with_name(stem + SIDECAR_TAIL)


def read_sidecar(recipe_path):
    """The saved settings, or None when there are none or the file cannot be used."""
    try:
        data = json.loads(sidecar_path(recipe_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    settings = data.get("settings") if isinstance(data, dict) and data.get("version") == SIDECAR_VERSION else None
    return dict(settings) if valid(settings) else None


def write_sidecar(recipe_path, settings) -> None:
    """Keep ``settings`` with the recipe, or remove the file when ``settings`` is None."""
    target = sidecar_path(recipe_path)
    if settings is None:
        target.unlink(missing_ok=True)
        return
    import os
    import tempfile

    data = json.dumps({"version": SIDECAR_VERSION, "settings": normalized(settings)}).encode("utf-8")
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, scratch = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(scratch, target)
    except BaseException:
        Path(scratch).unlink(missing_ok=True)
        raise


def seed_of(depth) -> int:
    import numpy as np

    digest = hashlib.blake2b(np.ascontiguousarray(depth).tobytes(), digest_size=8).digest()
    return int.from_bytes(digest, "little") & 0x7FFFFFFF
