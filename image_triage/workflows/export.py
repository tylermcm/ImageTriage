from __future__ import annotations

"""Workflow export planning and execution."""

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Signal

from ..edited_export import BatchOutcome, EditedRenderFailed
from ..formats import suffix_for_path
from ..image_resize import (
    OUTPUT_FORMAT_NAMES,
    ResizeSourceItem,
    _save_resized_image,
    _scaled_image,
    item_failure_reason,
    load_image_for_export,
    preset_for_key,
)
from ..scanner import normalized_path_key
from .models import WorkflowRecipe


@dataclass(slots=True, frozen=True)
class WorkflowExportItem:
    source: ResizeSourceItem
    target_path: str
    target_name: str
    target_suffix: str
    width: int
    height: int
    status: str
    message: str = ""


@dataclass(slots=True, frozen=True)
class WorkflowExportPlan:
    recipe: WorkflowRecipe
    destination_dir: str
    items: tuple[WorkflowExportItem, ...]
    executable_items: tuple[WorkflowExportItem, ...]
    output_label: str
    error_count: int
    can_apply: bool
    general_error: str = ""
    values: dict | None = None       # the Batch Export settings the files are written with


_LEGACY_FORMATS = {".jpg": "jpg", ".jpeg": "jpg", ".png": "png", ".tif": "tif", ".tiff": "tif", ".webp": "webp"}


def recipe_export_values(recipe: WorkflowRecipe) -> dict:
    """The Batch Export settings a workflow exports with: its export preset's, or, for a workflow made before presets (or whose
    preset was deleted), the same export described by its own resize, format, metadata and name fields. The workflow's own
    name prefix and suffix, when it has them, are used in either case."""
    from .. import export_pipeline as pipeline
    from ..export_presets import PresetStore

    values = PresetStore().get(recipe.export_preset) if recipe.export_preset else None
    if values is None:
        values = dict(pipeline.DEFAULTS)
        if recipe.resize_preset_key:
            preset = preset_for_key(recipe.resize_preset_key)
            if preset.width > 0 and preset.height > 0:
                values.update(sizeMode="fit", fitW=str(preset.width), fitH=str(preset.height), noEnlarge=True)
        values["format"] = _LEGACY_FORMATS.get(_normalized_output_suffix(recipe.convert_suffix), "original")
        values["meta"] = "none" if recipe.strip_metadata else "all"
    values = dict(values)
    if recipe.rename_prefix or recipe.rename_suffix:
        values["xPrefix"], values["xSuffix"] = recipe.rename_prefix, recipe.rename_suffix
    return pipeline.normalized(values)


def _output_label(values: dict) -> str:
    from .. import export_pipeline as pipeline

    fmt = "Original format" if values["format"] == "original" else next(l for k, l, _ in pipeline.FORMATS if k == values["format"])
    size = values["sizeMode"]
    size_label = {"original": "Original Size", "long": f"{values['long']} px long edge", "short": f"{values['short']} px short edge",
                  "fit": f"fits {values['fitW']} x {values['fitH']}", "mp": f"{values['mp']} MP", "pct": f"{values['pct']}%"}[size]
    return f"{fmt} | {size_label}"


def build_workflow_export_plan(
    sources: list[ResizeSourceItem],
    recipe: WorkflowRecipe,
    *,
    destination_dir: str,
) -> WorkflowExportPlan:
    """The workflow's export as a Batch Export plan into ``destination_dir`` (the files get a number when a name is taken)."""
    from .. import export_pipeline as pipeline

    normalized_destination = str(Path(destination_dir).expanduser()) if destination_dir else ""
    if not normalized_destination:
        return WorkflowExportPlan(recipe=recipe, destination_dir="", items=(), executable_items=(), output_label="", error_count=1,
                                  can_apply=False, general_error="Choose a destination folder.")
    values = {**recipe_export_values(recipe), "dest": "choose", "folder": normalized_destination, "exists": "unique"}
    present = [s for s in sources if s.source_path and os.path.exists(s.source_path)]
    plan = pipeline.build_plan([pipeline.ExportSource(s.source_path, s.source_name) for s in present], values)
    by_path = {item.source.path: item for item in plan.items}
    items: list[WorkflowExportItem] = []
    for source in sources:
        item = by_path.get(source.source_path)
        if item is None:
            items.append(WorkflowExportItem(source=source, target_path=source.source_path, target_name=source.source_name,
                                            target_suffix=suffix_for_path(source.source_path) or ".jpg", width=0, height=0,
                                            status="Error", message="Source file is missing."))
            continue
        items.append(WorkflowExportItem(source=source, target_path=item.target_path, target_name=item.target_name,
                                        target_suffix=Path(item.target_path).suffix, width=0, height=0, status=item.status,
                                        message=item.message))
    executable = tuple(i for i in items if i.status in ("Export", "Overwrite"))
    errors = sum(1 for i in items if i.status == "Error")
    return WorkflowExportPlan(recipe=recipe, destination_dir=normalized_destination, items=tuple(items), executable_items=executable,
                              output_label=_output_label(plan.values), error_count=errors,
                              can_apply=bool(executable) and errors == 0 and not plan.general_error,
                              general_error=plan.general_error, values=plan.values)


def apply_workflow_export_plan(
    plan: WorkflowExportPlan,
    *,
    progress_callback=None,
) -> tuple[str, ...]:
    from .. import export_pipeline as pipeline

    if not plan.executable_items:
        return ()
    written_paths: list[str] = []
    failed: list[tuple[str, str]] = []
    total = len(plan.executable_items)
    for index, item in enumerate(plan.executable_items, start=1):
        export = pipeline.ExportItem(pipeline.ExportSource(item.source.source_path, item.source.source_name), item.target_path,
                                     item.target_name, item.status)
        try:
            pipeline.export_item(export, plan.values)
        except EditedRenderFailed as error:
            failed.append((item.source.source_path, error.reason))
            if progress_callback is not None:
                progress_callback(index, total, f"Left out {item.source.source_name}")
            continue
        except Exception as error:
            # One photo that cannot be read or written must not stop the rest: the files before it are already on disk.
            failed.append((item.source.source_path, item_failure_reason(error)))
            if progress_callback is not None:
                progress_callback(index, total, f"Left out {item.source.source_name}")
            continue
        written_paths.append(item.target_path)
        if progress_callback is not None:
            progress_callback(index, total, f"Saved {item.target_name}")
    return BatchOutcome(written_paths, failed)


class WorkflowExportSignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class WorkflowExportTask(QRunnable):
    def __init__(self, plan: WorkflowExportPlan) -> None:
        super().__init__()
        self.plan = plan
        self.signals = WorkflowExportSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        total = max(1, len(self.plan.executable_items))
        self.signals.started.emit(total)
        try:
            written_paths = apply_workflow_export_plan(
                self.plan,
                progress_callback=lambda current, total_steps, message: self.signals.progress.emit(
                    current,
                    total_steps,
                    message,
                ),
            )
        except Exception as exc:  # pragma: no cover - worker/runtime path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(written_paths)


def _normalized_output_suffix(value: str) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    if not text.startswith("."):
        text = f".{text}"
    if text == ".jpeg":
        return ".jpg"
    if text == ".tif":
        return ".tiff"
    return text


def _export_target_name(source_name: str, target_suffix: str, prefix: str, suffix: str) -> str:
    source_path = Path(source_name)
    stem = f"{prefix}{source_path.stem}{suffix}".strip()
    return f"{stem or source_path.stem}{target_suffix}"


def _unique_export_target_path(destination_dir: Path, requested_name: str, reserved_targets: set[str]) -> str:
    destination_dir.mkdir(parents=True, exist_ok=True)
    requested = Path(requested_name)
    stem = requested.stem
    suffix = requested.suffix
    counter = 0
    while True:
        candidate_name = requested.name if counter == 0 else f"{stem}_{counter}{suffix}"
        candidate_path = destination_dir / candidate_name
        candidate_key = normalized_path_key(candidate_path)
        if candidate_key not in reserved_targets and not candidate_path.exists():
            return str(candidate_path)
        counter += 1
