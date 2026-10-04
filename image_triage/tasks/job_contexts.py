"""The immutable context handed to a long-running file job (resize, convert, workflow export, archive)."""
from __future__ import annotations

from dataclasses import dataclass

from ..image_convert import ConvertOptions, ConvertPlan
from ..image_resize import ResizeOptions, ResizePlan
from ..workflows import WorkflowRecipe


@dataclass(slots=True)
class ResizeExecutionContext:
    """Stores the active resize plan while the resize worker is running."""
    plan: ResizePlan
    options: ResizeOptions
    refresh_folder: str = ""


@dataclass(slots=True)
class ConvertExecutionContext:
    """Stores the active convert plan while the convert worker is running."""
    plan: ConvertPlan
    options: ConvertOptions
    refresh_folder: str = ""


@dataclass(slots=True)
class WorkflowExecutionContext:
    """Stores recipe execution state across export, copy, move, and archive steps."""
    recipe: WorkflowRecipe
    action: str
    destination_root: str = ""
    destination_dir: str = ""
    refresh_folder: str = ""
    archive_after_export: bool = False
    archive_format: str = "zip"


@dataclass(slots=True)
class ArchiveExecutionContext:
    """Describes the archive job currently in flight for status and refresh logic."""
    mode: str
    archive_path: str = ""
    destination_dir: str = ""
    archive_label: str = ""
    refresh_folder: str = ""
