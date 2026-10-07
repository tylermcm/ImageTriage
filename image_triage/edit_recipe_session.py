"""Translates between a ``photo_terminal`` session dict and an ``EditRecipe``.

Extracted from the now-removed built-in manual editor (``ui/photo_editor_panel.py``)
because ``edit_render_headless.py`` (PocketDrop's "apply edits" export) and
``edit_session_geometry.py`` (per-mask adjustments) still need this
session<->recipe translation even though the editor UI itself is gone.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from typing import Any

from .photo_terminal.adjustments import EditRecipe, is_identity_curve, normalize_curve_points

CURVE_OP_TYPE = "adjust.point_curve"
POINT_COLOR_OP_TYPE = "adjust.point_color"
CROP_OP_TYPE = "transform.crop"
RETOUCH_OP_TYPES: dict[str, str] = {
    "heal": "retouch.heal",
    "clone": "retouch.clone",
    "red_eye": "retouch.red_eye",
}
RETOUCH_TYPE_TO_KIND: dict[str, str] = {v: k for k, v in RETOUCH_OP_TYPES.items()}
# Every GUI-authored spot and crop lives in the session's full-source space —
# these op types are in PIXEL_OPERATION_TYPES and the schema requires it.
SOURCE_SPACE_ID = "space-source-full"
# Curve editor channel -> EditRecipe field. Persisted as one point_curve op per
# channel (the op carries a ``channel`` param) since the values are lists.
CURVE_RECIPE_KEYS: dict[str, str] = {
    "rgb": "curve_rgb",
    "red": "curve_red",
    "green": "curve_green",
    "blue": "curve_blue",
}

COLOR_MIXER_BANDS: tuple[tuple[str, str], ...] = (
    ("red", "Red"),
    ("orange", "Orange"),
    ("yellow", "Yellow"),
    ("green", "Green"),
    ("aqua", "Aqua"),
    ("blue", "Blue"),
    ("purple", "Purple"),
    ("magenta", "Magenta"),
)
COLOR_MIXER_CHANNELS: tuple[tuple[str, str], ...] = (
    ("hue", "Hue"),
    ("saturation", "Saturation"),
    ("luminance", "Luminance"),
)
COLOR_GRADING_ZONES: tuple[tuple[str, str], ...] = (
    ("shadow", "Shadows"),
    ("midtone", "Midtones"),
    ("highlight", "Highlights"),
    ("global", "Global"),
)


def _camel_param(key: str, prefix: str) -> str:
    """``calibration_red_hue`` -> ``redHue`` — session params are camelCase."""
    head, *rest = key[len(prefix):].split("_")
    return head + "".join(part.capitalize() for part in rest)


CALIBRATION_KEYS: frozenset[str] = frozenset((
    "calibration_shadow_tint",
    "calibration_red_hue", "calibration_red_saturation",
    "calibration_green_hue", "calibration_green_saturation",
    "calibration_blue_hue", "calibration_blue_saturation",
))
DEFRINGE_KEYS: frozenset[str] = frozenset((
    "defringe_purple_amount", "defringe_purple_hue_low", "defringe_purple_hue_high",
    "defringe_green_amount", "defringe_green_hue_low", "defringe_green_hue_high",
))
# Hue windows have non-zero neutrals, so like the vignette shape they are only
# worth persisting when their amount is doing something.
DEFRINGE_SHAPE_KEYS: frozenset[str] = frozenset(
    key for key in DEFRINGE_KEYS if not key.endswith("_amount")
)
GRAIN_SHAPE_KEYS: frozenset[str] = frozenset(("grain_size", "grain_roughness"))
VIGNETTE_OPTION_KEYS: frozenset[str] = frozenset((
    "vignette_midpoint", "vignette_roundness", "vignette_feather", "vignette_highlights",
))

SESSION_OPS: dict[str, tuple[str, str]] = {
    "exposure": ("adjust.exposure", "exposure"),
    "contrast": ("adjust.contrast", "contrast"),
    "highlights": ("adjust.highlights", "highlights"),
    "shadows": ("adjust.shadows", "shadows"),
    "whites": ("adjust.whites", "whites"),
    "blacks": ("adjust.blacks", "blacks"),
    "temperature": ("adjust.white_balance", "temperature"),
    "tint": ("adjust.white_balance", "tint"),
    "vibrance": ("adjust.vibrance", "vibrance"),
    "saturation": ("adjust.saturation", "saturation"),
    "denoise": ("adjust.denoise", "denoise"),
    "clarity": ("adjust.clarity", "clarity"),
    "dehaze": ("adjust.dehaze", "dehaze"),
    "sharpen": ("adjust.sharpen", "sharpen"),
    "vignette": ("adjust.vignette", "vignette"),
    # Written by operations_from_recipe only when the amount is non-zero, but
    # always read back so a saved shape survives a reload.
    "vignette_midpoint": ("adjust.vignette", "midpoint"),
    "vignette_roundness": ("adjust.vignette", "roundness"),
    "vignette_feather": ("adjust.vignette", "feather"),
    "vignette_highlights": ("adjust.vignette", "highlights"),
    "texture": ("adjust.texture", "texture"),
    "grain": ("adjust.grain", "grain"),
    # Shape controls, written alongside a non-zero amount (see the vignette
    # note above — same neutral-is-not-zero problem).
    "grain_size": ("adjust.grain", "size"),
    "grain_roughness": ("adjust.grain", "roughness"),
}
# The Color Mixer's 24 sliders all ride the one pre-existing HSL op.
SESSION_OPS.update(
    {
        f"{band}_{channel}": (
            "adjust.hsl_saturation_luminance",
            f"{band}{channel.capitalize()}",
        )
        for band, _label in COLOR_MIXER_BANDS
        for channel, _channel_label in COLOR_MIXER_CHANNELS
    }
)
SESSION_OPS["hsl_luminance"] = ("adjust.hsl_saturation_luminance", "luminance")
SESSION_OPS.update(
    {
        f"grading_{zone}_{field}": ("adjust.color_grading", f"{zone}{field.capitalize()}")
        for zone, _label in COLOR_GRADING_ZONES
        for field in ("hue", "sat", "lum")
    }
)
SESSION_OPS["grading_blending"] = ("adjust.color_grading", "blending")
SESSION_OPS["grading_balance"] = ("adjust.color_grading", "balance")
SESSION_OPS.update(
    {key: ("adjust.calibration", _camel_param(key, "calibration_")) for key in CALIBRATION_KEYS}
)
SESSION_OPS.update(
    {key: ("adjust.defringe", _camel_param(key, "defringe_")) for key in DEFRINGE_KEYS}
)

# Controls whose neutral value is not zero. They only reach the session when the
# amount they shape is non-zero, but they are always read back so a saved shape
# survives a reload.
SHAPE_KEY_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("adjust.vignette", VIGNETTE_OPTION_KEYS),
    ("adjust.grain", GRAIN_SHAPE_KEYS),
    ("adjust.defringe", DEFRINGE_SHAPE_KEYS),
    ("adjust.color_grading", frozenset(("grading_blending",))),
)
SHAPE_ONLY_KEYS: frozenset[str] = frozenset().union(
    *(keys for _op_type, keys in SHAPE_KEY_GROUPS)
)


def _next_id(existing: set[str], prefix: str) -> str:
    index = 1
    while True:
        candidate = f"{prefix}-{index:03d}"
        if candidate not in existing:
            return candidate
        index += 1


def recipe_from_session(session: dict[str, Any]) -> EditRecipe:
    values: dict[str, Any] = {}
    spots: list[dict[str, Any]] = []
    for op in session.get("operations", []):
        if not op.get("enabled", True) or op.get("maskId") is not None:
            continue
        params = op.get("params") or {}
        if op.get("type") == CURVE_OP_TYPE and params.get("points"):
            channel = str(params.get("channel") or "rgb")
            if channel in CURVE_RECIPE_KEYS:
                values[CURVE_RECIPE_KEYS[channel]] = params["points"]
            continue
        op_type = op.get("type")
        if op_type == CROP_OP_TYPE:
            left = params.get("left", 0)
            right = params.get("right", 0)
            top = params.get("top", 0)
            bottom = params.get("bottom", 0)
            if right > left and bottom > top:
                values["crop"] = (int(left), int(top), int(right), int(bottom))
            values["crop_angle"] = float(params.get("angle", 0.0) or 0.0)
            values["flip_h"] = bool(params.get("flipH", False))
            values["flip_v"] = bool(params.get("flipV", False))
            values["crop_aspect"] = str(params.get("aspect", "") or "")
            continue
        if op_type in RETOUCH_TYPE_TO_KIND:
            spot = {
                "id": str(op.get("id") or ""),
                "kind": RETOUCH_TYPE_TO_KIND[op_type],
                "x": float(params.get("x", 0.0)),
                "y": float(params.get("y", 0.0)),
                "r": float(params.get("radius", 0.0)),
                "feather": float(params.get("feather", 50.0)),
                "strength": float(params.get("strength", 0.8)),
                "seq": int(params.get("seq", len(spots))),
            }
            if op_type == "retouch.clone":
                spot["sx"] = float(params.get("sourceX", 0.0))
                spot["sy"] = float(params.get("sourceY", 0.0))
            spots.append(spot)
            continue
        if op_type == POINT_COLOR_OP_TYPE:
            # List-valued, like the curves above, so it cannot ride the scalar
            # SESSION_OPS mapping.
            colors = params.get("colors")
            if isinstance(colors, list) and colors:
                values["point_colors"] = colors
            continue
        for recipe_key, (mapped_type, param_key) in SESSION_OPS.items():
            if op_type == mapped_type and param_key in params:
                values[recipe_key] = params[param_key]
    if spots:
        values["retouch"] = sorted(spots, key=lambda entry: entry.get("seq", 0))
    return EditRecipe.from_dict(values)


def recipe_for_mask(session: dict[str, Any], mask_id: str) -> EditRecipe:
    """Local adjustments stored as operations targeting ``mask_id``."""
    values: dict[str, Any] = {}
    for op in session.get("operations", []):
        if not op.get("enabled", True) or op.get("maskId") != mask_id:
            continue
        params = op.get("params") or {}
        for recipe_key, (op_type, param_key) in SESSION_OPS.items():
            if op.get("type") == op_type and param_key in params:
                values[recipe_key] = params[param_key]
    return EditRecipe.from_dict(values)


def replace_mask_operations(session: dict[str, Any], mask_id: str, recipe: EditRecipe) -> None:
    """Rewrite ``mask_id``'s GUI adjustment operations from ``recipe``,
    preserving every other operation (global ops, other masks, non-GUI ops)."""
    gui_types = {op_type for op_type, _param_key in SESSION_OPS.values()}
    preserved = [
        op
        for op in session.get("operations", [])
        if op.get("maskId") != mask_id or op.get("type") not in gui_types
    ]
    from .photo_terminal.session import operation_ids

    new_ops = operations_from_recipe(recipe, existing_ids=operation_ids({"operations": preserved}))
    for op in new_ops:
        op["maskId"] = mask_id
    session["operations"] = sorted(
        [*preserved, *new_ops], key=lambda op: _operation_order(op.get("type", ""))
    )


def operations_from_recipe(recipe: EditRecipe, *, existing_ids: set[str] | None = None) -> list[dict[str, Any]]:
    ops: list[dict[str, Any]] = []
    used_ids = set(existing_ids or set())
    values = asdict(recipe)
    grouped: dict[str, dict[str, Any]] = {}
    for recipe_key, value in values.items():
        if recipe_key in CURVE_RECIPE_KEYS or recipe_key not in SESSION_OPS:
            continue
        if recipe_key in SHAPE_ONLY_KEYS:
            continue  # written below, and only alongside a non-zero amount
        if value in (0, 0.0, None):
            continue
        op_type, param_key = SESSION_OPS[recipe_key]
        grouped.setdefault(op_type, {})[param_key] = value

    # Shape controls have non-zero neutral values, so the zero test above cannot
    # decide whether they are worth persisting — the amount they belong to does.
    for op_type, shape_keys in SHAPE_KEY_GROUPS:
        if op_type not in grouped:
            continue
        for recipe_key in shape_keys:
            _op_type, param_key = SESSION_OPS[recipe_key]
            grouped[op_type][param_key] = values[recipe_key]

    for op_type, params in grouped.items():
        op_id = _next_id(used_ids, "gui-adjust")
        used_ids.add(op_id)
        op = {
            "id": op_id,
            "type": op_type,
            "enabled": True,
            "maskId": None,
            "params": params,
        }
        ops.append(op)

    if recipe.crop or recipe.crop_angle or recipe.flip_h or recipe.flip_v:
        op_id = _next_id(used_ids, "gui-adjust")
        used_ids.add(op_id)
        left, top, right, bottom = (
            tuple(int(v) for v in recipe.crop) if recipe.crop else (0, 0, 0, 0)
        )
        ops.append(
            {
                "id": op_id,
                "type": CROP_OP_TYPE,
                "enabled": True,
                "maskId": None,
                "coordinateSpaceId": SOURCE_SPACE_ID,
                "params": {
                    "left": left,
                    "top": top,
                    "right": right,
                    "bottom": bottom,
                    "angle": float(recipe.crop_angle),
                    "flipH": bool(recipe.flip_h),
                    "flipV": bool(recipe.flip_v),
                    "aspect": str(recipe.crop_aspect or ""),
                },
            }
        )

    for index, spot in enumerate(recipe.retouch or ()):
        op_type = RETOUCH_OP_TYPES.get(str(spot.get("kind")))
        if op_type is None:
            continue
        op_id = _next_id(used_ids, "gui-retouch")
        used_ids.add(op_id)
        params = {
            "x": float(spot.get("x", 0.0)),
            "y": float(spot.get("y", 0.0)),
            "radius": float(spot.get("r", 0.0)),
            "feather": float(spot.get("feather", 50.0)),
            # RENDERER_ORDER sorts heal before clone before red_eye, so a mixed
            # list is re-ordered by kind on save; seq is what restores the order
            # the user actually placed them in.
            "seq": int(spot.get("seq", index)),
        }
        if op_type != "retouch.red_eye":
            params["strength"] = float(spot.get("strength", 0.8))
        if op_type == "retouch.clone":
            params["sourceX"] = float(spot.get("sx", 0.0))
            params["sourceY"] = float(spot.get("sy", 0.0))
        ops.append(
            {
                "id": op_id,
                "type": op_type,
                "enabled": True,
                "maskId": None,
                "coordinateSpaceId": SOURCE_SPACE_ID,
                "params": params,
            }
        )

    if recipe.point_colors:
        op_id = _next_id(used_ids, "gui-adjust")
        used_ids.add(op_id)
        ops.append(
            {
                "id": op_id,
                "type": POINT_COLOR_OP_TYPE,
                "enabled": True,
                "maskId": None,
                "params": {"colors": deepcopy(recipe.point_colors)},
            }
        )

    # Point curves are lists, so they get one op per channel rather than the
    # scalar param grouping above.
    for channel, recipe_key in CURVE_RECIPE_KEYS.items():
        points = values.get(recipe_key)
        if is_identity_curve(points):
            continue
        op_id = _next_id(used_ids, "gui-adjust")
        used_ids.add(op_id)
        ops.append(
            {
                "id": op_id,
                "type": CURVE_OP_TYPE,
                "enabled": True,
                "maskId": None,
                "params": {
                    "points": [[int(x), int(y)] for x, y in normalize_curve_points(points)],
                    "channel": channel,
                },
            }
        )
    return sorted(ops, key=lambda op: _operation_order(op["type"]))


def _operation_order(op_type: str) -> int:
    from .photo_terminal.session import RENDERER_ORDER

    try:
        return RENDERER_ORDER.index(op_type)
    except ValueError:
        return len(RENDERER_ORDER)
