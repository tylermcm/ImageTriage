"""Atmosphere: fog, mist and haze laid over a photo by distance, using a monocular depth map.

Like Light Depth, the effect belongs to its panel and shows nowhere else. The draft and a settings file beside the recipe hold
only the panel's settings (``atmosphere``); the local masks the native renderer needs are built from those settings and the
photo's cached depth map when a render is requested (preview, save, export) and are never stored in the recipe's masks. This
module is the arithmetic and the file handling only, with no UI.

How the fog is made
-------------------
* **Density** at a pixel is how much fog lies in front of it: none on what is close, rising with distance (the depth map), held
  to a height in the frame (Height: how far up from the bottom the fog reaches), and broken into patches by smooth noise
  (Patchiness: how broken, Cloud Size: how large the pieces are).
* The fog is laid over the finished picture: every pixel is blended toward a fog colour by how much fog lies in front of it
  (``composite``), as Luminar does, so distant things disappear into it and the dark foreground stays dark. The renderer
  never sees it (no masks are made); the host does it after the render, for the live preview, a save and an export alike.
  The fog colour is a cool grey-blue that takes its brightness and some of its colour from the photo (blurred), so the fog
  "adapts to the photo's colours and lighting"; Vibrance says how much of the photo's colour it takes.
* **Drift** moves the patches of fog across the picture (Luminar's "Clouds Drift" pad): the same fog, shifted.

Depth maps are 8-bit, 255 = nearest (``depth_worker``). Inside this module depth is 0 (far) .. 1 (near).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from pathlib import Path

from . import light_depth

FOG_NAME = "Atmosphere fog"
SIDECAR_TAIL = ".photocraft.atmosphere.json"      # carried with the photo's other edit files (``edit_carry.EDIT_MARKERS``)
SIDECAR_VERSION = 1
MAX_MASKS = light_depth.MAX_MASKS
COVERAGE_SIDE = light_depth.COVERAGE_SIDE
PAD_SIDE = light_depth.PAD_SIDE

PRESET_NAMES = ("Wispy Fog", "Heavy Fog", "Ground Fog")

# amount: how strongly the fog is applied. height: how far up the frame it reaches (100 = the whole frame, 0 = only the bottom
# edge). patchiness: 0 is an even fog, 100 is broken into clumps. cloudSize: the size of those clumps. vibrance: the colour left
# in the fog (50 = a neutral grey fog; more keeps the photo's colour in it, less washes it out). start: how far from the camera the
# fog begins (0 = it reaches the foreground; 100 = only the far distance). driftX, driftY: where the patches lie (0..1, a pad; the
# same fog shifted). shown: the eye. depthModel: which depth model made the map (an index into DEPTH_MODELS).
# preset: 0 = custom, otherwise which of PRESET_NAMES was chosen last (1-based; it only highlights the tile).
DEFAULTS = {"amount": 0.0, "height": 100.0, "patchiness": 35.0, "cloudSize": 50.0, "vibrance": 50.0, "start": 15.0,
            "driftX": 0.5, "driftY": 0.5, "shown": 1.0, "depthModel": 2.0, "preset": 0.0}
RANGES = {"amount": (0, 100), "height": (0, 100), "patchiness": (0, 100), "cloudSize": (0, 100), "vibrance": (0, 100),
          "start": (0, 100), "driftX": (0, 1), "driftY": (0, 1), "shown": (0, 1), "depthModel": (0, 2), "preset": (0, 3)}

# What each preset sets (the Amount too, as in Luminar: Heavy Fog is a low Amount over the whole frame, Ground Fog a full one low down).
PRESETS = (
    {"amount": 56.0, "height": 70.0, "patchiness": 56.0, "cloudSize": 0.0, "vibrance": 50.0, "start": 15.0},
    {"amount": 55.0, "height": 100.0, "patchiness": 25.0, "cloudSize": 70.0, "vibrance": 50.0, "start": 0.0},
    {"amount": 100.0, "height": 15.0, "patchiness": 45.0, "cloudSize": 100.0, "vibrance": 50.0, "start": 20.0},
)


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
    return result


def with_preset(settings, index: int) -> dict:
    """``settings`` with preset ``index`` (0-based, into ``PRESETS``) applied: what the tile sets, nothing else (the drift and
    the depth model stay as they are)."""
    if not 0 <= index < len(PRESETS):
        return normalized(settings)
    return normalized({**normalized(settings), **PRESETS[index], "preset": index + 1})


def model_key(settings) -> str:
    """Which depth model ("small", "base" or "large") the settings use: the one that made the map they are built from."""
    return light_depth.DEPTH_MODELS[int(round(normalized(settings)["depthModel"]))]


def active(settings) -> bool:
    """Whether the effect changes the photo: switched on and with some Amount."""
    settings = normalized(settings)
    return settings["amount"] > 0 and settings["shown"] > 0


def masks_needed(settings) -> int:
    """How many of a photo's masks the effect takes: none (the fog is laid over the finished picture, not made with masks)."""
    return 0


# --- the fog ---------------------------------------------------------------------------------------------------------------

def _smooth(values):
    import numpy as np

    values = np.clip(values, 0.0, 1.0)
    return values * values * (3 - 2 * values)


def noise(shape, cells: float, seed: int, octaves: int = 6, drift=(0.5, 0.5), stretch: float = 1.0, billow: bool = False):
    """Smooth value noise over ``shape`` (height, width), 0..1: ``cells`` random cells across the long side at the coarsest
    octave, each finer octave twice as many and half as strong. The same seed gives the same picture; ``drift`` (0..1 each way)
    slides the window over a larger field of the same noise, so the patches move without changing."""
    import numpy as np
    from PIL import Image

    height, width = shape
    rng = np.random.default_rng(seed)
    total = np.zeros(shape, np.float32)
    weight = 0.0
    for octave in range(octaves):
        across = max(2, round(cells * 2 ** octave))
        grid = (max(2, round(across * height / max(width, height))), max(2, round(across * width / max(width, height) / stretch)))
        layer = rng.random((grid[0] + 4, grid[1] + 4)).astype(np.float32)      # two spare cells each side to slide over
        left, top = 2 * drift[0], 2 * drift[1]
        box = (left, top, left + grid[1], top + grid[0])
        image = Image.fromarray(layer, mode="F").resize((width, height), Image.Resampling.BICUBIC, box=box)
        strength = (0.52 if billow else 0.62) ** octave
        values = np.asarray(image, np.float32)
        if billow:
            values = np.abs(2.0 * values - 1.0)                 # rounded puffs, with a crease wherever the noise crosses its middle
        total += values * strength
        weight += strength
    total /= weight
    low, high = np.percentile(total, [2, 98])
    return np.clip((total - low) / max(float(high - low), 1e-6), 0.0, 1.0)


CEILING_HALF = 0.125    # at Height 0 the fog is half as thick as it is lower down this far above the ground line (a frame fraction)


def _horizon_of(distance) -> float:
    """Where the horizon lies in a frame of ``distance`` (0 near .. 1 far), 0 (top) .. 1 (bottom): the row from which the picture is
    as far away as it gets."""
    import numpy as np

    profile = np.maximum.accumulate(np.median(distance, axis=1)[::-1])
    return float(1.0 - int(np.argmax(profile >= 0.9 * profile[-1])) / max(1, distance.shape[0]))


def horizon_fraction(depth, side: int = 256) -> float:
    return _horizon_of(1.0 - light_depth.working_depth(depth, side))


def ceiling_of(depth, settings) -> float:
    """Where the top of the fog lies in the frame, 0 (top) .. 1 (bottom): the Height marker on the picture. Height 0 puts it a little
    above the horizon, Height 100 at the top of the frame (the sky fogged too)."""
    top = max(0.0, horizon_fraction(depth) - CEILING_HALF)
    return top * (1.0 - normalized(settings)["height"] / 100)


def height_for_ceiling(depth, ceiling: float) -> float:
    """The Height setting that puts the top of the fog at ``ceiling`` (the inverse of ``ceiling_of``), 0..100."""
    top = max(0.0, horizon_fraction(depth) - CEILING_HALF)
    return 100.0 if top <= 0 else max(0.0, min(100.0, (1.0 - float(ceiling) / top) * 100))


def _above_ground(distance):
    """How far above the ground each pixel is, 0 (on it) .. 1 (the whole frame's height): the ground at a given distance lies at
    the row where that distance is typical (the rows' median distance, farther toward the top), so a bank of fog that hugs the
    ground is a band across the far ground and not the bottom edge of the picture. Things that stand up (trees, mountains, the
    sky) are above the ground line of their distance."""
    import numpy as np

    height = distance.shape[0]
    profile = np.median(distance, axis=1)[::-1]                                  # index 0 is the bottom row, the nearest
    profile = np.maximum.accumulate(profile)                                      # never nearer higher up
    horizon = int(np.argmax(profile >= 0.9 * profile[-1]))                        # where the rows are as far away as they get: the horizon
    profile = np.minimum(profile, profile[horizon])                               # above it nothing is farther: the sky is at the horizon
    profile = profile + np.linspace(0.0, 1e-3, height, dtype=np.float32)          # strictly rising
    rows = np.arange(height, dtype=np.float32)[::-1]                              # the row of each profile entry
    ground_row = np.interp(np.minimum(distance, profile[horizon]), profile, rows)  # the row the ground has this distance at
    return np.clip((ground_row - np.arange(height, dtype=np.float32)[:, None]) / height, 0.0, 1.0)


def density(depth, settings, side: int = COVERAGE_SIDE, seed: int = 0):
    """How much fog lies in front of each pixel, as a float array 0..1 at working size (long edge ``side`` at most)."""
    import numpy as np

    settings = normalized(settings)
    scene = light_depth._blur(light_depth.working_depth(depth, side), 1.5)      # the map's per-pixel noise would show as grain
    height, width = scene.shape
    distance = 1.0 - scene                                                        # 0 close .. 1 far
    begin = settings["start"] / 100 * 0.6
    by_distance = _smooth((distance - begin) / 0.55)
    above_ground = _above_ground(distance)
    up = settings["height"] / 100
    top = max(0.0, _horizon_of(distance) - CEILING_HALF)                          # where the fog's top is at Height 0 .. 100
    half = _horizon_of(distance) - top * (1.0 - up)                                # how far above the ground it is half as thick
    by_height = 1.0 - _smooth((above_ground - half) / (0.25 * (1.0 - up) + 0.03))
    clumps = settings["patchiness"] / 100
    # Cumulus-like structure: puffs with defined edges and a brighter middle. Patchiness opens gaps in a solid layer (0 is solid),
    # Cloud Size sets the size of the puffs.
    cells = 3 + (1 - settings["cloudSize"] / 100) * 21
    puffs = noise((height, width), cells, seed, drift=(settings["driftX"], settings["driftY"]), stretch=1.6, billow=True)
    inner = 0.3 + 0.7 * puffs                                                  # lit from within: the middle of a puff is brighter
    shape = _smooth((puffs - clumps * 0.5) / 0.34)
    floor = 0.9 * (1.0 - clumps) ** 1.2                                          # a thin fog lies between the puffs until the gaps open up
    by_clump = floor + (1.0 - floor) * shape * inner
    if clumps <= 0:
        by_clump = np.ones_like(by_clump)                                        # solid
    fog = by_distance * by_height * by_clump
    return np.clip(light_depth._blur(fog, max(width, height) * 0.003), 0.0, 1.0)


def seed_of(depth) -> int:
    """A seed that is the same for the same photo (its depth map) every time, so the patches do not move between renders."""
    import numpy as np

    digest = hashlib.blake2b(np.ascontiguousarray(depth).tobytes(), digest_size=8).digest()
    return int.from_bytes(digest, "little") & 0x7FFFFFFF


def coverage(depth, settings, side: int = COVERAGE_SIDE):
    """The fog's mask coverage as a uint8 raster (255 = full fog)."""
    import numpy as np

    fog = density(depth, normalized(settings), side, seed_of(depth))
    return np.clip(np.rint(fog * 255), 0, 255).astype(np.uint8)


# --- laying the fog over a picture -----------------------------------------------------------------------------------------

_COOL = (0.80, 0.89, 1.0)           # the fog's colour before the photo's own is mixed in (a cool grey-blue)
_LUMA = (0.2126, 0.7152, 0.0722)
_FIELDS = OrderedDict()             # (settings, depth digest, size) -> the fog's density; a slider drag lays the same fog again and again


def _field(depth, settings, width: int, height: int):
    """The fog's density 0..1 at exactly ``width`` x ``height``."""
    import numpy as np
    from PIL import Image

    depth = np.ascontiguousarray(depth)
    key = (tuple(sorted(settings.items())), hashlib.blake2b(depth.tobytes(), digest_size=16).digest(), depth.shape, width, height)
    if key not in _FIELDS:
        fog = density(depth, settings, 1024, seed_of(depth))
        if fog.shape != (height, width):
            fog = np.asarray(Image.fromarray(fog.astype(np.float32), mode="F").resize((width, height), Image.Resampling.BICUBIC), np.float32)
        _FIELDS[key] = np.clip(fog, 0.0, 1.0)
        while len(_FIELDS) > 3:
            _FIELDS.popitem(last=False)
    _FIELDS.move_to_end(key)
    return _FIELDS[key]


def _small(rgb):
    """The photo reduced to about 48 pixels across (float 0..1): where it is light and what colour the light is."""
    import numpy as np
    from PIL import Image

    height, width = rgb.shape[:2]
    scale = 48 / max(height, width)
    reduced = Image.fromarray((np.clip(rgb, 0, 1) * 255 + 0.5).astype(np.uint8)).resize(
        (max(2, round(width * scale)), max(2, round(height * scale))), Image.Resampling.BOX)
    return np.asarray(reduced, np.float32) / 255.0


def _shade(small, settings):
    """The fog's colour across the picture, as a small array: cool grey-blue, as bright as the light is there, with some of the
    light's own colour (Vibrance)."""
    import numpy as np

    weights = np.array(_LUMA, np.float32)
    light = small @ weights
    ambient = np.clip(0.72 + 0.22 * light, 0.0, 0.92)
    colour = np.clip(small / np.maximum(light[..., None], 0.03), 0.55, 1.6)       # the colour of the light, as a ratio
    cool = 1.0 + (np.array(_COOL, np.float32) - 1.0) * (settings["vibrance"] / 50)       # 0 is a grey fog, 50 the usual cool one, 100 twice as blue
    mix = settings["vibrance"] / 100 * 0.2
    tint = np.clip(cool, 0.2, 1.4) * (1 - mix) + colour * mix
    tint = tint / np.maximum(tint @ weights, 0.05)[..., None]
    return np.clip(tint * ambient[..., None], 0.0, 1.0)


def _spread(shade, width: int, height: int, rows=None):
    """``shade`` (small) as a ``width`` x ``height`` picture; ``rows`` = (first, last) of the frame's ``height`` when only that part is wanted."""
    import numpy as np
    from PIL import Image

    first, last = (rows[0], rows[1]) if rows else (0, height)
    full = rows[2] if rows else height
    top, bottom = shade.shape[0] * first / full, shade.shape[0] * last / full
    box = (0.0, top, float(shade.shape[1]), bottom)
    channels = [np.asarray(Image.fromarray(np.ascontiguousarray(shade[..., i]), mode="F").resize(
        (width, last - first), Image.Resampling.BICUBIC, box=box), np.float32) for i in range(3)]
    return np.clip(np.stack(channels, axis=-1), 0.0, 1.0)


def composite(rgb, depth, settings):
    """``rgb`` (float 0..1, height x width x 3, as shown) with the fog laid over it: each pixel blended toward the fog colour by
    ``0.95 x Amount x density`` (Amount is the fog's brightness: the structure does not change with it)."""
    import numpy as np

    settings = normalized(settings)
    if not active(settings):
        return rgb
    height, width = rgb.shape[:2]
    cover = 0.95 * settings["amount"] / 100 * _field(depth, settings, width, height)      # 0 clear .. 1 solid
    shade = _spread(_shade(_small(rgb), settings), width, height)
    return rgb * (1 - cover[..., None]) + shade * cover[..., None]


def fogged_image(image, depth, settings):
    """``image`` (a QImage, any format) with the fog laid over it, as an 8-bit RGB QImage; ``image`` itself when there is none."""
    import numpy as np
    from PySide6.QtGui import QImage

    if not active(settings):
        return image
    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    stride = rgb.bytesPerLine()
    flat = np.frombuffer(rgb.constBits(), np.uint8, count=stride * rgb.height()).reshape(rgb.height(), stride)
    pixels = flat[:, : rgb.width() * 3].reshape(rgb.height(), rgb.width(), 3).astype(np.float32) / 255.0
    out = np.ascontiguousarray(np.clip(composite(pixels, depth, settings) * 255 + 0.5, 0, 255).astype(np.uint8))
    return QImage(out.data, out.shape[1], out.shape[0], out.shape[1] * 3, QImage.Format.Format_RGB888).copy()


def _depth_of(image_path, settings):
    depth = light_depth.load_depth(image_path, model_key(settings))
    if depth is None:
        raise ValueError("Atmosphere's depth map is missing. Open Atmosphere to make it again.")
    return depth


def fogged_frame(frame, image_path, settings):
    """A preview worker's ``(header, QImage)`` with the fog laid over the picture. Raises ValueError when the depth map is gone."""
    if not active(settings):
        return frame
    return frame[0], fogged_image(frame[1], _depth_of(image_path, settings), settings)


def fogged_file(source, destination, image_path, settings):
    """Write ``source`` (a full-size render, 8 or 16 bit) to ``destination`` (PNG) with the fog laid over it, in strips so a
    camera-sized picture is never held as floats all at once. Raises ValueError when the depth map is gone."""
    import numpy as np
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
    sample = pixels[:: max(1, height // 600), :: max(1, width // 600), :3].astype(np.float32) / top
    shade = _shade(_small(sample), settings)               # one fog colour for the whole picture, spread over each strip
    whole = _field(depth, settings, width, height)
    strength = 0.95 * settings["amount"] / 100
    for start in range(0, height, 512):
        end = min(height, start + 512)
        strip = pixels[start:end, :, :3].astype(np.float32) / top
        cover = (strength * whole[start:end])[..., None]
        fog = _spread(shade, width, end - start, rows=(start, end, height))
        pixels[start:end, :, :3] = np.clip((strip * (1 - cover) + fog * cover) * top + 0.5, 0, top).astype(unit)
    output = QSaveFile(str(destination))
    if not output.open(QIODevice.WriteOnly) or not image.save(output, "PNG") or not output.commit():
        raise OSError("The picture with fog could not be saved")


def with_masks(settings: dict, atmosphere, image_path) -> dict:
    """The Camera Raw ``settings`` as the renderer should get them: without the host's ``atmosphere`` entry (the fog is laid over
    the render afterwards, never made with masks)."""
    return {key: value for key, value in settings.items() if key != "atmosphere"}


# --- the settings file beside the recipe -----------------------------------------------------------------------------------

def sidecar_path(recipe_path) -> Path:
    """Where a photo's Atmosphere settings are kept, next to its recipe (``<photo>.cameraraw.json``)."""
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
