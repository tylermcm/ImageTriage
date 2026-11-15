"""Light Depth: light a photo near and far apart, using a monocular depth map.

The effect belongs to the Light Depth panel and shows nowhere else. The draft and the settings file beside the recipe hold
only the panel's settings (``lightDepth``). The local masks the native renderer needs are built from those settings and the
photo's cached depth map when a render is requested (preview, save, export) and are never stored in the recipe's masks, so
they do not appear in the Mask tab. This module is the arithmetic and the file handling only, with no UI.

Depth maps are 8-bit, 255 = nearest (``depth_worker``). Inside this module depth is 0 (far) .. 1 (near).
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import OrderedDict
from pathlib import Path

LEGACY_MASK_NAME = "Light Depth"      # earlier versions stored the effect as an ordinary mask with this name
NEAR_NAME, FAR_NAME = "Light Depth near", "Light Depth far"
SIDECAR_TAIL = ".photocraft.lightdepth.json"  # carried with the photo's other edit files (``edit_carry.EDIT_MARKERS``)
SIDECAR_VERSION = 2
MAX_MASKS = 16                        # the most masks a recipe can hold

# Coverage is authored small: it is smooth, and a continuous-tone raster is expensive in the recipe encoding. The renderer
# scales it to the photo.
COVERAGE_SIDE = 640
PAD_SIDE = 320  # the depth picture shown in the panel

MAX_EXPOSURE = 2.0  # stops at Amount 100 and a zone Brightness of +/-100
MAX_TEMPERATURE = 60.0  # local Temperature at Amount 100 and a Warmth of +/-100

# amount: strength of the whole effect. depth: where near becomes far (0 far .. 1 near). softness: how far the split spills
# across object edges. warmth: colour of the whole effect. brightness/warmth Near/Far: what happens on each side of the split.
# x, y, size: an optional pool of light on the photo (size 100 = the whole photo). wrap: how wide a slab around the depth is
# left alone (the subject), so the effect "wraps" further around it.
# shown: 1 = on, 0 = switched off without losing the settings.
DEFAULTS = {"amount": 0.0, "depth": 0.5, "softness": 30.0, "warmth": 0.0,
            "brightnessNear": -50.0, "warmthNear": 0.0, "brightnessFar": -50.0, "warmthFar": 0.0,
            "x": 0.5, "y": 0.6, "size": 100.0, "wrap": 30.0, "shown": 1.0}
RANGES = {"amount": (0, 100), "depth": (0, 1), "softness": (0, 100), "warmth": (-100, 100),
          "brightnessNear": (-100, 100), "warmthNear": (-100, 100), "brightnessFar": (-100, 100), "warmthFar": (-100, 100),
          "x": (0, 1), "y": (0, 1), "size": (0, 100), "wrap": (0, 100), "shown": (0, 1)}


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


def active(settings) -> bool:
    """Whether the effect changes the photo: switched on and with some Amount."""
    settings = normalized(settings)
    return settings["amount"] > 0 and settings["shown"] > 0


def pool_spread(size) -> float:
    """The pool's reach in picture widths (a Gaussian's sigma) for a ``size`` below 100."""
    return 0.05 + 0.95 * max(0.0, min(100.0, float(size))) / 100


def _blur(values, radius: float):
    from PIL import Image, ImageFilter

    if radius < 0.3:
        return values
    import numpy as np

    image = Image.fromarray(np.clip(np.rint(values * 255), 0, 255).astype(np.uint8))
    return np.asarray(image.filter(ImageFilter.GaussianBlur(radius)), dtype=np.float32) / 255


def working_depth(depth, side: int = COVERAGE_SIDE):
    """The depth map at working size (long edge ``side`` at most), as float 0..1 with 1 nearest."""
    import numpy as np
    from PIL import Image

    values = np.asarray(depth)
    if values.ndim != 2 or min(values.shape) < 1:
        raise ValueError("The depth map is not a single-channel image")
    image = Image.fromarray(values.astype(np.uint8))
    scale = min(1.0, side / max(image.size))
    if scale < 1:
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                             Image.Resampling.BILINEAR)
    return np.asarray(image, dtype=np.float32) / 255


def depth_at(depth, x: float, y: float) -> float:
    """Scene depth (0 far .. 1 near) under a point of the photo, averaged over a small patch so one noisy pixel does
    not decide what a click picks."""
    import numpy as np

    values = np.asarray(depth, dtype=np.float32)
    if values.max(initial=0) > 1:
        values = values / 255
    height, width = values.shape
    cx, cy = min(width - 1, max(0, int(x * width))), min(height - 1, max(0, int(y * height)))
    reach = max(1, round(min(width, height) * 0.01))
    patch = values[max(0, cy - reach):cy + reach + 1, max(0, cx - reach):cx + reach + 1]
    return float(np.median(patch))


def zones(depth, settings, side: int = COVERAGE_SIDE):
    """How strongly each pixel belongs to the near side and to the far side, as two uint8 arrays.

    What lies at ``depth`` (the subject) belongs to neither and is left alone: ``wrap`` is how wide that slab is, so the
    light wraps further around the depth with more Wrap. Things nearer than the slab are near and things beyond it far,
    coming in over a short feather. ``softness`` lets both spill across object edges. Below size 100 both are also
    narrowed to a pool around (``x``, ``y``): the effect fades out beyond it.
    """
    import numpy as np

    settings = normalized(settings)
    scene = working_depth(depth, side)
    height, width = scene.shape
    # A touch of smoothing always: the model's map has per-pixel noise that would otherwise show as grain.
    scene = _blur(scene, max(width, height) * 0.004)
    band = 0.03 + 0.25 * settings["wrap"] / 100        # half the width of the slab around the depth that is left alone
    feather = 0.05 + 0.2 * settings["wrap"] / 100      # how gradually each side comes in beyond it

    def smooth(values):
        values = np.clip(values / feather, 0, 1)
        return values * values * (3 - 2 * values)

    blur = max(width, height) * 0.06 * settings["softness"] / 100
    near = _blur(smooth(scene - (settings["depth"] + band)), blur)
    far = _blur(smooth((settings["depth"] - band) - scene), blur)
    if settings["size"] < 100:
        columns = (np.arange(width, dtype=np.float32) + 0.5) / width
        rows = (np.arange(height, dtype=np.float32) + 0.5) / height
        distance = np.hypot(columns[None, :] - settings["x"], (rows[:, None] - settings["y"]) * (height / width))
        pool = np.exp(-0.5 * (distance / pool_spread(settings["size"])) ** 2)
        near, far = near * pool, far * pool
    return tuple(np.clip(np.rint(part * 255), 0, 255).astype(np.uint8) for part in (near, far))


def coverage(depth, settings, side: int = COVERAGE_SIDE):
    """The near side of the split alone (255 = fully near): what the panel's picture shows."""
    return zones(depth, settings, side)[0]


def adjustments(settings) -> tuple[dict, dict]:
    """The local adjustment applied on the near side and on the far side. Amount scales both; a zone's Brightness is
    its exposure and its Warmth, added to the overall Warmth, its temperature."""
    settings = normalized(settings)
    strength = settings["amount"] / 100
    result = []
    for zone in ("Near", "Far"):
        adjust = {}
        exposure = round(MAX_EXPOSURE * strength * settings["brightness" + zone] / 100, 4)
        warmth = max(-100.0, min(100.0, settings["warmth"] + settings["warmth" + zone]))
        temperature = round(MAX_TEMPERATURE * strength * warmth / 100, 2)
        if exposure:
            adjust["exposure"] = exposure
        if temperature:
            adjust["temperature"] = temperature
        result.append(adjust)
    return result[0], result[1]


def is_legacy_mask(mask) -> bool:
    return isinstance(mask, dict) and mask.get("name") == LEGACY_MASK_NAME


def strip_legacy(settings: dict) -> dict:
    """``settings`` (a recipe's Camera Raw settings) without a mask that an earlier version made for Light Depth. A
    photo's masks are authored in the Mask tab, which never shows that one."""
    masks = settings.get("masks")
    if not isinstance(masks, list) or not any(is_legacy_mask(mask) for mask in masks):
        return settings
    result = dict(settings)
    kept = [mask for mask in masks if not is_legacy_mask(mask)]
    if kept:
        result["masks"] = kept
    else:
        result.pop("masks")
    return result


# --- the masks a render uses ----------------------------------------------------------------------------------------------

_BUILT = OrderedDict()  # (settings, depth digest) -> the two mask rows; a slider drag renders the same settings again and again


def render_masks(settings, depth) -> list:
    """The two local masks (near, far) that carry ``settings`` into a render, or [] when the effect changes nothing."""
    import numpy as np

    from .qml_shell.raw_masks import encode_raster

    settings = normalized(settings)
    if not active(settings):
        return []
    depth = np.ascontiguousarray(depth)
    key = (tuple(sorted(settings.items())), hashlib.blake2b(depth.tobytes(), digest_size=16).digest(), depth.shape)
    if key not in _BUILT:
        rows = []
        for name, raster, adjust in zip((NEAR_NAME, FAR_NAME), zones(depth, settings), adjustments(settings)):
            if adjust:
                rows.append({"name": name, "enabled": True, "invert": False, "amount": 100,
                             "shape": encode_raster(raster), "adjust": adjust})
        _BUILT[key] = rows
        while len(_BUILT) > 4:
            _BUILT.popitem(last=False)
    _BUILT.move_to_end(key)
    return [dict(row, adjust=dict(row["adjust"])) for row in _BUILT[key]]


def load_depth(image_path):
    """The depth map kept for ``image_path`` as a uint8 array, or None when there is none."""
    import numpy as np
    from PIL import Image

    from .depth_maps import light_depth_cache_file

    kept = light_depth_cache_file(image_path)
    if kept is None or not kept.is_file():
        return None
    try:
        with Image.open(kept) as opened:
            return np.asarray(opened.convert("L"), dtype=np.uint8)
    except (OSError, ValueError):
        return None


def with_masks(settings: dict, light, image_path) -> dict:
    """The Camera Raw ``settings`` as the renderer should get them: without the host's ``lightDepth`` entry and, when
    ``light`` is on, with its masks added after the person's own. Raises ValueError when they cannot be made: the depth
    map is gone, or the photo already has too many masks."""
    result = {key: value for key, value in settings.items() if key != "lightDepth"}
    if not active(light):
        return result
    depth = load_depth(image_path)
    if depth is None:
        raise ValueError("Light Depth's depth map is missing. Open Light Depth to make it again.")
    added = render_masks(light, depth)
    masks = list(result.get("masks") or [])
    if len(masks) + len(added) > MAX_MASKS:
        raise ValueError(f"Light Depth needs {len(added)} of a photo's {MAX_MASKS} masks. Delete some masks to use it.")
    if added:
        result["masks"] = masks + added
    return result


# --- the settings file beside the recipe ----------------------------------------------------------------------------------

def sidecar_path(recipe_path) -> Path:
    """Where a photo's Light Depth settings are kept, next to its recipe (``<photo>.cameraraw.json``)."""
    recipe = Path(recipe_path)
    name = recipe.name
    stem = name[:-len(".cameraraw.json")] if name.endswith(".cameraraw.json") else name
    return recipe.with_name(stem + SIDECAR_TAIL)


def _from_version_one(settings) -> dict | None:
    """Settings of the first version (a light at a distance: reach and focus) as near/far settings, approximately: the
    old light brightened what lay near its distance, which is now the near side."""
    old = ("amount", "x", "y", "depth", "softness", "warmth", "reach", "focus")
    if not (isinstance(settings, dict) and settings.keys() == set(old)
            and all(type(settings[key]) in (int, float) and math.isfinite(settings[key]) for key in old)):
        return None
    return normalized({"amount": settings["amount"], "x": settings["x"], "y": settings["y"], "depth": settings["depth"],
                       "softness": settings["softness"], "warmth": settings["warmth"], "wrap": settings["reach"],
                       "size": 100 - 0.9 * settings["focus"], "brightnessNear": 100, "brightnessFar": 0})


def read_sidecar(recipe_path):
    """``(settings, legacy)``: the saved settings (None when there are none or the file cannot be used) and whether they
    came from the first version, which only applies to a photo whose recipe still holds that version's mask."""
    try:
        data = json.loads(sidecar_path(recipe_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, False
    if not isinstance(data, dict):
        return None, False
    settings = data.get("settings")
    if data.get("version") == SIDECAR_VERSION:
        return (dict(settings) if valid(settings) else None), False
    if data.get("version") == 1:
        return _from_version_one(settings), True
    return None, False


def load_settings(recipe_path, recipe_settings: dict) -> tuple[dict, dict | None]:
    """``(settings, light)`` for a photo that is opened or exported: the recipe's Camera Raw settings without any mask an
    earlier version made for Light Depth, and the Light Depth settings that go with them (None for none).

    An earlier version's mask is converted when settings were saved beside it (the effect carries on, now owned by the
    panel); without them it is dropped, since its settings are unknown. Settings saved by an earlier version beside a
    recipe that no longer has its mask are stale and are ignored, as before.
    """
    had_legacy = any(is_legacy_mask(mask) for mask in recipe_settings.get("masks") or [])
    light, legacy = read_sidecar(recipe_path)
    if light is not None and legacy and not had_legacy:
        light = None
    return strip_legacy(recipe_settings), light


def recipe_for_render(image_path, recipe: dict | None) -> dict | None:
    """``recipe`` (as saved) with Light Depth's masks added, for an export. A photo whose only edit is Light Depth has an
    identity recipe, so one is made for it. Raises ValueError when the masks cannot be made."""
    from . import photocraft_bridge as bridge

    base = (recipe or {}).get("cameraRaw") or {}
    settings, light = load_settings(bridge.recipe_path(image_path), base)
    if not active(light):
        return recipe if settings is base else {**recipe, "cameraRaw": settings}
    return {**(recipe or {"version": bridge.RECIPE_VERSION}), "cameraRaw": with_masks(settings, light, image_path)}


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
