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
* The renderer cannot blend a pixel toward a fog colour (a fog stage in the engine would; see ``docs/creative_tools_plan.md``),
  so the look is built from what a local adjustment can do: negative Dehaze, a lift of the exposure and the blacks, less
  contrast, clarity and texture, and less colour. The density is the mask; Amount scales the adjustment. Because the
  adjustment acts on the photo's own pixels, the fog keeps the photo's colour and light (Luminar's fog "adapts to the photo's
  colours and lighting"); Vibrance says how much colour it keeps.
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

# What each preset sets (the Amount too: a wisp is not applied as strongly as a bank of fog).
PRESETS = (
    {"amount": 35.0, "height": 60.0, "patchiness": 70.0, "cloudSize": 40.0, "vibrance": 45.0, "start": 30.0},
    {"amount": 65.0, "height": 100.0, "patchiness": 35.0, "cloudSize": 60.0, "vibrance": 50.0, "start": 10.0},
    {"amount": 55.0, "height": 28.0, "patchiness": 50.0, "cloudSize": 30.0, "vibrance": 50.0, "start": 0.0},
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
    """How many of a photo's masks the effect takes: one, the fog."""
    return 1 if active(settings) else 0


# --- the fog ---------------------------------------------------------------------------------------------------------------

def _smooth(values):
    import numpy as np

    values = np.clip(values, 0.0, 1.0)
    return values * values * (3 - 2 * values)


def noise(shape, cells: float, seed: int, octaves: int = 4, drift=(0.5, 0.5)):
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
        grid = (max(2, round(across * height / max(width, height))), max(2, round(across * width / max(width, height))))
        layer = rng.random((grid[0] + 4, grid[1] + 4)).astype(np.float32)      # two spare cells each side to slide over
        left, top = 2 * drift[0], 2 * drift[1]
        box = (left, top, left + grid[1], top + grid[0])
        image = Image.fromarray(layer, mode="F").resize((width, height), Image.Resampling.BICUBIC, box=box)
        strength = 0.5 ** octave
        total += np.asarray(image, np.float32) * strength
        weight += strength
    total /= weight
    low, high = np.percentile(total, [2, 98])
    return np.clip((total - low) / max(float(high - low), 1e-6), 0.0, 1.0)


def density(depth, settings, side: int = COVERAGE_SIDE, seed: int = 0):
    """How much fog lies in front of each pixel, as a float array 0..1 at working size (long edge ``side`` at most)."""
    import numpy as np

    settings = normalized(settings)
    scene = light_depth._blur(light_depth.working_depth(depth, side), 1.5)      # the map's per-pixel noise would show as grain
    height, width = scene.shape
    distance = 1.0 - scene                                                        # 0 close .. 1 far
    begin = settings["start"] / 100 * 0.6
    by_distance = _smooth((distance - begin) / 0.55)
    rows = (np.arange(height, dtype=np.float32) + 0.5) / height
    above_ground = 1.0 - rows[:, None]                                            # 0 at the bottom edge .. 1 at the top
    reach = settings["height"] / 100
    by_height = 1.0 - _smooth((above_ground - reach) / 0.25)
    clumps = settings["patchiness"] / 100
    if clumps > 0:
        cells = 3 + (1 - settings["cloudSize"] / 100) * 21                        # cloud size 0 is small clumps, 100 is large ones
        shape = _smooth((noise((height, width), cells, seed, drift=(settings["driftX"], settings["driftY"])) - 0.25) / 0.6)
        by_clump = 1.0 - clumps * (1.0 - shape)
    else:
        by_clump = 1.0
    fog = by_distance * by_height * by_clump
    return np.clip(light_depth._blur(fog, max(width, height) * 0.004), 0.0, 1.0)


def adjustments(settings) -> dict:
    """The local adjustment for the fog: what a bank of fog does to what is seen through it, as far as a local adjustment can
    say it (the renderer cannot blend toward a colour; see the module note). Amount scales all of it. Vibrance is how much of
    the photo's own colour the fog keeps."""
    settings = normalized(settings)
    s = settings["amount"] / 100
    fog = {
        "dehaze": round(-75.0 * s, 2),            # negative Dehaze is a milky veil
        "exposure": round(0.6 * s, 3),
        "contrast": round(-40.0 * s, 2),
        "blacks": round(35.0 * s, 2),
        "shadows": round(20.0 * s, 2),
        "clarity": round(-30.0 * s, 2),
        "texture": round(-20.0 * s, 2),
        "saturation": round(max(-100.0, min(100.0, (settings["vibrance"] - 50) * 0.6 - 30.0 * s)), 2),
    }
    return {k: v for k, v in fog.items() if v}


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


# --- the masks a render uses -----------------------------------------------------------------------------------------------

_BUILT = OrderedDict()  # (settings, depth digest) -> the mask rows; a slider drag renders the same settings again and again


def render_masks(settings, depth) -> list:
    """The local mask that carries ``settings`` into a render: one row, the fog."""
    import numpy as np

    from .qml_shell.raw_masks import encode_raster

    settings = normalized(settings)
    if not active(settings):
        return []
    depth = np.ascontiguousarray(depth)
    key = (tuple(sorted(settings.items())), hashlib.blake2b(depth.tobytes(), digest_size=16).digest(), depth.shape)
    if key not in _BUILT:
        adjust = adjustments(settings)
        rows = ([{"name": FOG_NAME, "enabled": True, "invert": False, "amount": 100,
                  "shape": encode_raster(coverage(depth, settings)), "adjust": adjust}] if adjust else [])
        _BUILT[key] = rows
        while len(_BUILT) > 4:
            _BUILT.popitem(last=False)
    _BUILT.move_to_end(key)
    return [dict(row, adjust=dict(row["adjust"])) for row in _BUILT[key]]


def with_masks(settings: dict, atmosphere, image_path) -> dict:
    """The Camera Raw ``settings`` as the renderer should get them: without the host's ``atmosphere`` entry and, when
    ``atmosphere`` is on, with its masks added after the others. Raises ValueError when they cannot be made: the depth map
    is gone, or the photo already has too many masks."""
    result = {key: value for key, value in settings.items() if key != "atmosphere"}
    if not active(atmosphere):
        return result
    depth = light_depth.load_depth(image_path, model_key(atmosphere))
    if depth is None:
        raise ValueError("Atmosphere's depth map is missing. Open Atmosphere to make it again.")
    added = render_masks(atmosphere, depth)
    masks = list(result.get("masks") or [])
    if len(masks) + len(added) > MAX_MASKS:
        raise ValueError(f"Atmosphere needs {len(added)} of a photo's {MAX_MASKS} masks. Delete some masks to use it.")
    if added:
        result["masks"] = masks + added
    return result


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
