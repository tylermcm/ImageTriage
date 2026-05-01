from __future__ import annotations

"""Path helpers for workflow execution."""

from pathlib import Path

from ..archive_ops import archive_format_for_key
from ..scanner import normalize_filesystem_path
from .models import WorkflowRecipe


def workflow_destination_dir(recipe: WorkflowRecipe, destination_root: str) -> str:
    """Resolve the effective destination directory for a recipe run."""
    root = normalize_filesystem_path(destination_root or "")
    if not root:
        return ""
    if recipe.destination_subfolder:
        return normalize_filesystem_path(str(Path(root) / recipe.destination_subfolder))
    return root


def workflow_archive_path(recipe: WorkflowRecipe, destination_root: str) -> str:
    """Resolve the archive output path for archive-style handoff recipes."""
    root = normalize_filesystem_path(destination_root or "")
    if not root:
        return ""
    archive_format = archive_format_for_key(recipe.archive_format)
    base_name = recipe.destination_subfolder.strip() or recipe.name or "handoff"
    return str(Path(root) / f"{base_name}{archive_format.suffix}")


def workflow_record_folder_name(record_name: str) -> str:
    """Derive a stable per-record folder name from the displayed record name."""
    return Path(record_name).stem or "record"
