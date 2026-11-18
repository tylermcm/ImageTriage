"""The host's own effects: the ones the editor builds into masks when a render is asked for (Light Depth, Atmosphere).

Each keeps its settings in the draft and in a small file beside the recipe, never in the recipe's masks. This module is the one
place that turns all of them into what the native renderer receives, so the live preview worker, saves and full-size exports
cannot disagree about which effects are applied or in which order.
"""
from __future__ import annotations

from . import atmosphere, bokeh, light_depth

# The draft keys that are the host's own: never part of a recipe the renderer or the saved file sees.
KEYS = ("lightDepth", "atmosphere", "bokeh")
# The ones laid over a finished render (``effects_result``), whose settings ride in an export's recipe (``recipe_for_render``).
LAID_KEYS = ("atmosphere", "bokeh")


def without_host(settings: dict) -> dict:
    """``settings`` without the host effects' own entries."""
    return {key: value for key, value in settings.items() if key not in KEYS}


def with_masks(settings: dict, image_path) -> dict:
    """The Camera Raw ``settings`` as the renderer should get them: without the host effects' entries and with the masks of
    those that are on, after the person's own (Light Depth's, then Atmosphere's). Raises ValueError when they cannot be made
    (a depth map is gone, or the photo would hold too many masks)."""
    result = light_depth.with_masks(settings, settings.get("lightDepth"), image_path)
    result = atmosphere.with_masks(result, settings.get("atmosphere"), image_path)
    return bokeh.with_masks(result, settings.get("bokeh"), image_path)


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
    blur = bokeh.read_sidecar(recipe_path)
    if blur:
        extras["bokeh"] = blur
    return settings, extras


def recipe_for_render(image_path, recipe: dict | None) -> dict | None:
    """``recipe`` (as saved) with the host effects' masks added, for an export. A photo whose only edit is a host effect has
    an identity recipe, so one is made for it. Raises ValueError when the masks cannot be made."""
    from . import photocraft_bridge as bridge

    lit = light_depth.recipe_for_render(image_path, recipe)
    path = bridge.recipe_path(image_path)
    laid = {key: found for key, found, on in (("atmosphere", atmosphere.read_sidecar(path), atmosphere.active),
                                              ("bokeh", bokeh.read_sidecar(path), bokeh.active)) if on(found)}
    if not laid:
        return lit
    # These are laid over the finished render by the host (``effects_result``), so their settings ride in the recipe; the renderer is
    # never sent them (``full_render._without_host_settings``).
    base = (lit or {}).get("cameraRaw") or {}
    return {**(lit or {"version": bridge.RECIPE_VERSION}), "cameraRaw": {**base, **laid}}


def effects_result(result, image_path, recipe):
    """A full-size render with the recipe's Atmosphere and then Bokeh laid over it (before any crop), or ``result`` when there are none."""
    from pathlib import Path
    from uuid import uuid4

    from .full_render import RENDERED, RenderResult, failed

    settings = (recipe or {}).get("cameraRaw") or {}
    todo = [(atmosphere.fogged_file, settings.get("atmosphere"), atmosphere.active),
            (bokeh.bokeh_file, settings.get("bokeh"), bokeh.active)]
    todo = [(make, found) for make, found, on in todo if on(found)]
    if result.outcome != RENDERED or not todo:
        return result
    current, temporary = Path(result.path), []
    try:
        for make, found in todo:
            target = current.with_name(current.stem + ".fx-" + uuid4().hex + ".png")
            temporary.append(target)
            make(current, target, image_path, found)
            current = target
        for stale in temporary[:-1]:
            stale.unlink(missing_ok=True)
        return RenderResult(RENDERED, path=current, format="png", width=result.width, height=result.height, depth=result.depth,
                            icc=result.icc, temporary=True)
    except Exception as error:
        for stale in temporary:
            stale.unlink(missing_ok=True)
        return failed(str(error))
    finally:
        result.release()


def effects_file(rendered, image_path, settings):
    """``(path, temporaries)``: ``rendered`` (a full-size render) with the draft's Atmosphere and then Bokeh laid over it. The caller
    removes the temporaries. Raises ValueError when a depth map is gone."""
    from pathlib import Path

    current, temporary = Path(rendered), []
    try:
        for make, found, on in ((atmosphere.fogged_file, settings.get("atmosphere"), atmosphere.active),
                                (bokeh.bokeh_file, settings.get("bokeh"), bokeh.active)):
            if on(found):
                target = current.with_name(current.stem + ".fx.png")
                if target == current:
                    target = current.with_name(current.stem + ".fx2.png")
                temporary.append(target)
                make(current, target, image_path, found)
                current = target
    except BaseException:
        for stale in temporary:
            stale.unlink(missing_ok=True)
        raise
    return current, temporary
