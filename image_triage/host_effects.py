"""The host's own effects: the ones the editor builds into masks when a render is asked for (Light Depth, Atmosphere).

Each keeps its settings in the draft and in a small file beside the recipe, never in the recipe's masks. This module is the one
place that turns all of them into what the native renderer receives, so the live preview worker, saves and full-size exports
cannot disagree about which effects are applied or in which order.
"""
from __future__ import annotations

from . import atmosphere, light_depth

# The draft keys that are the host's own: never part of a recipe the renderer or the saved file sees.
KEYS = ("lightDepth", "atmosphere")


def without_host(settings: dict) -> dict:
    """``settings`` without the host effects' own entries."""
    return {key: value for key, value in settings.items() if key not in KEYS}


def with_masks(settings: dict, image_path) -> dict:
    """The Camera Raw ``settings`` as the renderer should get them: without the host effects' entries and with the masks of
    those that are on, after the person's own (Light Depth's, then Atmosphere's). Raises ValueError when they cannot be made
    (a depth map is gone, or the photo would hold too many masks)."""
    result = light_depth.with_masks(settings, settings.get("lightDepth"), image_path)
    return atmosphere.with_masks(result, settings.get("atmosphere"), image_path)


def masks_in_use(settings: dict, *, besides: str) -> int:
    """How many masks the host effects other than ``besides`` ("lightDepth" or "atmosphere") take, for the budget a photo has."""
    total = 0
    if besides != "lightDepth":
        total += light_depth.masks_needed(settings.get("lightDepth"))
    if besides != "atmosphere":
        total += atmosphere.masks_needed(settings.get("atmosphere"))
    return total


def load_extras(recipe_path, recipe_settings: dict) -> tuple[dict, dict]:
    """``(settings, extras)`` for a photo that is opened or exported: the recipe's Camera Raw settings (without an earlier
    version's Light Depth mask) and the host effects' saved settings, keyed as in the draft (only those that exist)."""
    settings, light = light_depth.load_settings(recipe_path, recipe_settings)
    extras = {}
    if light:
        extras["lightDepth"] = light
    air = atmosphere.read_sidecar(recipe_path)
    if air:
        extras["atmosphere"] = air
    return settings, extras


def recipe_for_render(image_path, recipe: dict | None) -> dict | None:
    """``recipe`` (as saved) with the host effects' masks added, for an export. A photo whose only edit is a host effect has
    an identity recipe, so one is made for it. Raises ValueError when the masks cannot be made."""
    from . import photocraft_bridge as bridge

    lit = light_depth.recipe_for_render(image_path, recipe)
    air = atmosphere.read_sidecar(bridge.recipe_path(image_path))
    if not atmosphere.active(air):
        return lit
    # The fog is laid over the finished render by the host (``fog_result``), so its settings ride in the recipe; the renderer is
    # never sent them (``full_render._without_host_settings``).
    base = (lit or {}).get("cameraRaw") or {}
    return {**(lit or {"version": bridge.RECIPE_VERSION}), "cameraRaw": {**base, "atmosphere": air}}


def fog_result(result, image_path, recipe):
    """A full-size render with the recipe's Atmosphere laid over it (before any crop), or ``result`` when there is none."""
    from pathlib import Path
    from uuid import uuid4

    from .full_render import RENDERED, RenderResult, failed

    air = ((recipe or {}).get("cameraRaw") or {}).get("atmosphere")
    if result.outcome != RENDERED or not atmosphere.active(air):
        return result
    target = Path(result.path).with_name(Path(result.path).stem + ".fog-" + uuid4().hex + ".png")
    try:
        atmosphere.fogged_file(result.path, target, image_path, air)
        return RenderResult(RENDERED, path=target, format="png", width=result.width, height=result.height, depth=result.depth,
                            icc=result.icc, temporary=True)
    except Exception as error:
        target.unlink(missing_ok=True)
        return failed(str(error))
    finally:
        result.release()
