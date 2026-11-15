"""Light Depth: change the light on what is nearer and farther than a chosen depth, using a monocular depth map.

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
NEAR_NAME, SLAB_NAME, FAR_NAME = "Light Depth near", "Light Depth depth", "Light Depth far"
SIDECAR_TAIL = ".photocraft.lightdepth.json"  # carried with the photo's other edit files (``edit_carry.EDIT_MARKERS``)
SIDECAR_VERSION = 2
MAX_MASKS = 16                        # the most masks a recipe can hold

# Coverage is authored small: it is smooth, and a continuous-tone raster is expensive in the recipe encoding. The renderer
# scales it to the photo.
COVERAGE_SIDE = 1536   # the most a raster mask holds; the depth map is kept at this size too
PAD_SIDE = 320  # the depth picture shown in the panel

DEPTH_MODELS = ("small", "base", "large")   # the depth models; ``ai_model.DEPTH_VARIANTS`` names the same ones
DEPTH_MODEL_LABELS = ("Small", "Base", "Large")

MAX_EXPOSURE = 2.0  # stops at Amount 100 and a Near or Far Brightness of +/-100
MAX_TEMPERATURE = 60.0  # local Temperature at Amount 100 and a Warmth of +/-100

# amount: how strongly the effect is applied. depth: the distance that is left as it is (0 far .. 1 near). Brightness/Warmth
# Near/Far: what happens to what is nearer than, or beyond, that depth (Brightness starts at -50, so by default both sides
# are darkened and what lies at the depth stands out, as in Luminar). warmth: colour of the whole effect. softness: how far
# the zones spill across object edges. x, y, size: an optional pool on the photo (size 100 = the whole photo). feather: how
# far from the depth the effect takes to come in (nothing at the depth, in full beyond the feather), as the two markers on
# Luminar's depth picture show. depthModel: which depth model made the map (an index into DEPTH_MODELS; Large by default).
DEFAULTS = {"amount": 0.0, "depth": 0.5, "softness": 10.0, "warmth": 0.0,
            "brightnessNear": -50.0, "warmthNear": 0.0, "brightnessFar": -50.0, "warmthFar": 0.0,
            "x": 0.5, "y": 0.6, "size": 100.0, "feather": 15.0, "shown": 1.0, "depthModel": 2.0}
RANGES = {"amount": (0, 100), "depth": (0, 1), "softness": (0, 100), "warmth": (-100, 100),
          "brightnessNear": (-100, 100), "warmthNear": (-100, 100), "brightnessFar": (-100, 100), "warmthFar": (-100, 100),
          "x": (0, 1), "y": (0, 1), "size": (0, 100), "feather": (0, 100), "shown": (0, 1), "depthModel": (0, 2)}


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


def model_key(settings) -> str:
    """Which depth model ("small", "base" or "large") the settings use: the one that made the map they are built from."""
    return DEPTH_MODELS[int(round(normalized(settings)["depthModel"]))]


def active(settings) -> bool:
    """Whether the effect changes the photo: switched on and with some Amount."""
    settings = normalized(settings)
    return settings["amount"] > 0 and settings["shown"] > 0


BAND = 0.01  # half the thickness, in depth, of what lies exactly at the chosen depth and is left fully alone


def feather_span(feather) -> float:
    """The distance in depth (0..1) from the chosen depth at which the effect reaches full strength: where the feather
    markers on the depth picture sit."""
    return BAND + 0.02 + 0.4 * max(0.0, min(100.0, float(feather))) / 100


def feather_for_span(span) -> float:
    """The Feather setting (0..100) whose markers sit ``span`` away from the chosen depth."""
    return max(0.0, min(100.0, (float(span) - BAND - 0.02) / 0.4 * 100))


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
    """Which zone each pixel is in, as three uint8 arrays: (near, slab, far).

    The slab is what lies at ``depth``. Moving away from it, ``near`` and ``far`` come in over the feather (see
    ``feather_span``): none at the depth, all of it beyond. The three add up to full. ``softness`` lets them spill across object
    edges. Below size 100 all three are also narrowed to a pool around (``x``, ``y``): the effect fades out beyond it.
    """
    import numpy as np

    settings = normalized(settings)
    scene = working_depth(depth, side)
    height, width = scene.shape
    # A touch of smoothing always: the model's map has per-pixel noise that would otherwise show as grain. Not more: the
    # objects in the map (trees, grass, a post) are what the effect should follow.
    scene = _blur(scene, max(width, height) * 0.0015)
    feather = 0.02 + 0.4 * settings["feather"] / 100   # how far the effect takes to come in beyond the depth

    def smooth(values):
        values = np.clip(values / feather, 0, 1)
        return values * values * (3 - 2 * values)

    blur = max(width, height) * 0.03 * settings["softness"] / 100
    near = _blur(smooth(scene - (settings["depth"] + BAND)), blur)
    far = _blur(smooth((settings["depth"] - BAND) - scene), blur)
    light = np.clip(1 - near - far, 0, 1)
    if settings["size"] < 100:
        columns = (np.arange(width, dtype=np.float32) + 0.5) / width
        rows = (np.arange(height, dtype=np.float32) + 0.5) / height
        distance = np.hypot(columns[None, :] - settings["x"], (rows[:, None] - settings["y"]) * (height / width))
        pool = np.exp(-0.5 * (distance / pool_spread(settings["size"])) ** 2)
        near, light, far = near * pool, light * pool, far * pool
    return tuple(np.clip(np.rint(part * 255), 0, 255).astype(np.uint8) for part in (near, light, far))


def coverage(depth, settings, side: int = COVERAGE_SIDE):
    """The slab at the chosen depth alone (255 = fully in it): what the panel's picture shows."""
    return zones(depth, settings, side)[1]


def adjustments(settings) -> tuple[dict, dict, dict]:
    """The local adjustment for (near, slab, far), as Luminar's Light Depth does it: what is nearer than the slab and what
    is beyond it change by their own Brightness and Warmth, the slab at the depth keeps its brightness, and the overall
    Warmth is added everywhere. Amount scales all of it."""
    settings = normalized(settings)
    strength = settings["amount"] / 100

    def adjust(brightness, warmth):
        result = {}
        exposure = round(MAX_EXPOSURE * strength * brightness / 100, 4)
        temperature = round(MAX_TEMPERATURE * strength * max(-100.0, min(100.0, warmth)) / 100, 2)
        if exposure:
            result["exposure"] = exposure
        if temperature:
            result["temperature"] = temperature
        return result

    return (adjust(settings["brightnessNear"], settings["warmth"] + settings["warmthNear"]),
            adjust(0, settings["warmth"]),
            adjust(settings["brightnessFar"], settings["warmth"] + settings["warmthFar"]))


def masks_needed(settings) -> int:
    """How many of a photo's masks the effect takes: one for each zone that changes."""
    return sum(1 for adjust in adjustments(settings) if adjust) if active(settings) else 0


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
    """The local masks (near, slab, far) that carry ``settings`` into a render; a zone that changes nothing has none."""
    import numpy as np

    from .qml_shell.raw_masks import encode_raster

    settings = normalized(settings)
    if not active(settings):
        return []
    depth = np.ascontiguousarray(depth)
    key = (tuple(sorted(settings.items())), hashlib.blake2b(depth.tobytes(), digest_size=16).digest(), depth.shape)
    if key not in _BUILT:
        rows = []
        for name, raster, adjust in zip((NEAR_NAME, SLAB_NAME, FAR_NAME), zones(depth, settings), adjustments(settings)):
            if adjust:
                rows.append({"name": name, "enabled": True, "invert": False, "amount": 100,
                             "shape": encode_raster(raster), "adjust": adjust})
        _BUILT[key] = rows
        while len(_BUILT) > 4:
            _BUILT.popitem(last=False)
    _BUILT.move_to_end(key)
    return [dict(row, adjust=dict(row["adjust"])) for row in _BUILT[key]]


def load_depth(image_path, model: str = "small"):
    """The depth map the depth model ``model`` kept for ``image_path`` as a uint8 array, or None when there is none."""
    import numpy as np
    from PIL import Image

    from .depth_maps import light_depth_cache_file

    kept = light_depth_cache_file(image_path, model=model)
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
    depth = load_depth(image_path, model_key(light))
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
    old light brightened what lay at its distance; that cannot be expressed now, so its depth, reach and focus carry over
    and the near and far zones get the defaults."""
    old = ("amount", "x", "y", "depth", "softness", "warmth", "reach", "focus")
    if not (isinstance(settings, dict) and settings.keys() == set(old)
            and all(type(settings[key]) in (int, float) and math.isfinite(settings[key]) for key in old)):
        return None
    return normalized({"amount": settings["amount"], "x": settings["x"], "y": settings["y"], "depth": settings["depth"],
                       "softness": settings["softness"], "warmth": settings["warmth"], "feather": settings["reach"] / 2, "depthModel": 0,
                       "size": 100 - 0.9 * settings["focus"]})


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
        if isinstance(settings, dict) and "depthModel" not in settings:
            settings = {**settings, "depthModel": 0.0}          # saved before there was a choice of model: the Small one's map
        if isinstance(settings, dict) and "wrap" in settings and "feather" not in settings:
            # An earlier build called the thickness of the untouched slab Wrap; half of it is about the same Feather.
            settings = {**{k: v for k, v in settings.items() if k != "wrap"}, "feather": settings["wrap"] / 2}
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
