"""Free-function mask/session geometry helpers.

Extracted from ``PhotoEditorPanel``'s instance methods of the same name
(``_mask_root``, ``_group_members``, ``_group_components``,
``_component_params``, ``_bitmap_asset_path``, ``_mask_source_size``,
``masked_adjustments``) so the same mask-assembly logic can run without a
live widget. The panel still owns the interactive UI (selection state,
sliders, overlays); it now delegates to these functions, passing its own
``self._session`` / ``self._session_path`` / ``self._source_path`` instead of
keeping a second copy of the logic.

``recipe_for_mask`` (the per-mask adjustment reader) lives in
``edit_recipe_session`` and is imported locally inside the one function that
needs it, matching this file's existing deferred-import style.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from .photo_terminal.adjustments import EditRecipe
from .photo_terminal.session import image_dimensions

# Geometric mask types whose adjustments apply through a drawn shape rather
# than a bitmap/selection. Mirrors ``PhotoEditorPanel.MASK_SHAPE_TYPES``.
MASK_SHAPE_TYPES: tuple[str, ...] = ("radial", "linear-gradient")


def component_type(mask: dict[str, Any]) -> str:
    return "bitmap" if mask.get("type") == "subject-select" else str(mask.get("type"))


def mask_by_id(session: dict[str, Any] | None, mask_id: str | None) -> dict[str, Any] | None:
    if not mask_id or session is None:
        return None
    for mask in session.get("masks", []):
        if mask.get("id") == mask_id:
            return mask
    return None


def mask_root(session: dict[str, Any] | None, mask: dict[str, Any]) -> dict[str, Any]:
    parent = mask_by_id(session, mask.get("parentId"))
    return parent if parent is not None else mask


def group_members(session: dict[str, Any] | None, root_id: str) -> list[dict[str, Any]]:
    """The root mask followed by its children, in session order."""
    if session is None:
        return []
    members = [mask for mask in session.get("masks", []) if mask.get("id") == root_id]
    members.extend(mask for mask in session.get("masks", []) if mask.get("parentId") == root_id)
    return members


def bitmap_asset_path(
    session: dict[str, Any] | None,
    session_path: Path | None,
    mask: dict[str, Any],
) -> Path | None:
    if session is None or session_path is None:
        return None
    asset_id = mask.get("assetId") or mask.get("cacheAssetId")
    if not asset_id:
        return None
    for asset in session.get("assets", {}).get("bitmapMasks", []):
        if asset.get("id") == asset_id:
            return Path(session_path).parent / str(asset.get("path", ""))
    return None


def component_params(
    session: dict[str, Any] | None,
    session_path: Path | None,
    mask: dict[str, Any],
) -> dict[str, Any]:
    params = dict(mask.get("params") or {})
    if mask.get("type") in ("bitmap", "subject-select"):
        asset_path = bitmap_asset_path(session, session_path, mask)
        if asset_path is not None:
            params["assetPath"] = str(asset_path)
    return params


def group_components(
    session: dict[str, Any] | None,
    session_path: Path | None,
    root_id: str,
) -> list[tuple[str, dict[str, Any], str]]:
    out: list[tuple[str, dict[str, Any], str]] = []
    for mask in group_members(session, root_id):
        if mask.get("type") not in (*MASK_SHAPE_TYPES, "bitmap", "subject-select"):
            continue
        combine = "add" if mask.get("id") == root_id else str(mask.get("combine", "add"))
        out.append((component_type(mask), component_params(session, session_path, mask), combine))
    return out


def mask_source_size(
    session: dict[str, Any] | None,
    source_path: Path | None,
    *,
    size_cache: dict[str, tuple[int, int]] | None = None,
) -> tuple[int, int] | None:
    if session:
        spaces = session.get("coordinateSpaces") or []
        if spaces:
            width = spaces[0].get("sourceWidth")
            height = spaces[0].get("sourceHeight")
            if width and height:
                return int(width), int(height)
    if source_path is None:
        return None
    cache_key = str(source_path)
    if size_cache is not None and cache_key in size_cache:
        return size_cache[cache_key]
    try:
        width, height = image_dimensions(source_path)
    except Exception:
        return None
    if not width or not height:
        # Unreadable or vanished file; callers treat None as "no canvas".
        return None
    resolved = (int(width), int(height))
    if size_cache is not None:
        size_cache[cache_key] = resolved
    return resolved


def build_masked_adjustments(
    session: dict[str, Any] | None,
    session_path: Path | None,
    source_path: Path | None,
    *,
    size_cache: dict[str, tuple[int, int]] | None = None,
) -> list[tuple[list[tuple[str, dict[str, Any]]], tuple[int, int], EditRecipe]]:
    """Mask groups with non-default local adjustments, for compositing:
    (components, source_size, recipe). A group is a root mask plus its
    parentId children; its adjustments apply through the union of the
    components. Cached bitmap and subject selections participate in the same
    compositing path as geometric masks. Shared by the live editor preview
    and headless rendering (thumbnails, export)."""
    if session is None:
        return []
    source_size = mask_source_size(session, source_path, size_cache=size_cache)
    if source_size is None:
        return []

    from .edit_recipe_session import recipe_for_mask

    out: list[tuple[list[tuple[str, dict[str, Any]]], tuple[int, int], EditRecipe]] = []
    for mask in session.get("masks", []):
        # A hidden layer (eye off in the overview) keeps its operations but
        # sits out of the composite; preview and Save Copy both read here.
        if mask.get("parentId") or not mask.get("enabled", True):
            continue
        root_id = str(mask.get("id"))
        recipe = recipe_for_mask(session, root_id)
        if not any(value not in (0, 0.0, None) for value in asdict(recipe).values()):
            continue
        components = group_components(session, session_path, root_id)
        if not components:
            continue
        out.append((components, source_size, recipe))
    return out
