from __future__ import annotations

"""Persistence helpers for workflow recipes and workspace presets."""

import json
from typing import Callable, TypeVar

from .models import (
    WorkflowRecipe,
    WorkspacePreset,
    deserialize_workflow_recipe,
    deserialize_workspace_preset,
    serialize_workflow_recipe,
    serialize_workspace_preset,
)


T = TypeVar("T")


def load_saved_workflow_recipes(raw: str) -> list[WorkflowRecipe]:
    """Load saved recipes from settings JSON, dropping invalid or duplicate entries."""
    return _load_unique_items(
        raw,
        deserialize_item=deserialize_workflow_recipe,
        key_for_item=lambda recipe: recipe.key,
    )


def dump_saved_workflow_recipes(recipes: list[WorkflowRecipe]) -> str:
    """Serialize saved recipes into the compact settings payload."""
    return json.dumps([serialize_workflow_recipe(recipe) for recipe in recipes])


def load_saved_workspace_presets(raw: str) -> list[WorkspacePreset]:
    """Load saved presets from settings JSON, dropping invalid or duplicate entries."""
    return _load_unique_items(
        raw,
        deserialize_item=deserialize_workspace_preset,
        key_for_item=lambda preset: preset.key,
    )


def dump_saved_workspace_presets(presets: list[WorkspacePreset]) -> str:
    """Serialize saved presets into the compact settings payload."""
    return json.dumps([serialize_workspace_preset(preset) for preset in presets])


def _load_unique_items(
    raw: str,
    *,
    deserialize_item: Callable[[dict[str, object] | None], T | None],
    key_for_item: Callable[[T], str],
) -> list[T]:
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(payload, list):
        return []

    items: list[T] = []
    seen: set[str] = set()
    for entry in payload:
        item = deserialize_item(entry if isinstance(entry, dict) else None)
        if item is None:
            continue
        item_key = key_for_item(item)
        if not item_key or item_key in seen:
            continue
        seen.add(item_key)
        items.append(item)
    return items
