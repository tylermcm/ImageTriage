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
    base_name = _archive_base_name(recipe.destination_subfolder) or _archive_base_name(recipe.name) or "handoff"
    # A handoff run again must not replace the archive of the run before it: the next free name is taken.
    candidate = Path(root) / f"{base_name}{archive_format.suffix}"
    counter = 1
    while candidate.exists():
        candidate = Path(root) / f"{base_name}_{counter}{archive_format.suffix}"
        counter += 1
    return str(candidate)


_UNSAFE_NAME_CHARS = frozenset('<>:"/\\|?*')


def _archive_base_name(text: str) -> str:
    """``text`` (a recipe's subfolder or name, typed by a person) as one file name: a separator would put the archive in another
    folder, and the other characters cannot be in a Windows file name at all."""
    cleaned = "".join("_" if char in _UNSAFE_NAME_CHARS or ord(char) < 32 else char for char in (text or ""))
    return cleaned.strip(" ._")


def workflow_record_folder_name(record_name: str) -> str:
    """Derive a stable per-record folder name from the displayed record name."""
    return Path(record_name).stem or "record"
