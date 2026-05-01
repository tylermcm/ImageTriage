from __future__ import annotations

"""Workflow export planning and execution."""

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Signal

from ..formats import suffix_for_path
from ..image_resize import (
    OUTPUT_FORMAT_NAMES,
    ResizeSourceItem,
    _load_resize_image,
    _save_resized_image,
    _scaled_image,
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


def build_workflow_export_plan(
    sources: list[ResizeSourceItem],
    recipe: WorkflowRecipe,
    *,
    destination_dir: str,
) -> WorkflowExportPlan:
    normalized_destination = str(Path(destination_dir).expanduser())
    if not normalized_destination:
        return WorkflowExportPlan(
            recipe=recipe,
            destination_dir="",
            items=(),
            executable_items=(),
            output_label="",
            error_count=1,
            can_apply=False,
            general_error="Choose a destination folder.",
        )

    destination_path = Path(normalized_destination)
    final_suffix = _normalized_output_suffix(recipe.convert_suffix)
    if final_suffix and final_suffix not in OUTPUT_FORMAT_NAMES:
        return WorkflowExportPlan(
            recipe=recipe,
            destination_dir=normalized_destination,
            items=(),
            executable_items=(),
            output_label="",
            error_count=1,
            can_apply=False,
            general_error="Choose a supported export format.",
        )

    width = 0
    height = 0
    size_label = ""
    if recipe.resize_preset_key:
        preset = preset_for_key(recipe.resize_preset_key)
        width = max(0, preset.width)
        height = max(0, preset.height)
        size_label = preset.name

    items: list[WorkflowExportItem] = []
    executable: list[WorkflowExportItem] = []
    reserved_targets: set[str] = set()
    error_count = 0

    for source in sources:
        if not source.source_path or not os.path.exists(source.source_path):
            item = WorkflowExportItem(
                source=source,
                target_path=source.source_path,
                target_name=source.source_name,
                target_suffix=final_suffix or suffix_for_path(source.source_path) or ".jpg",
                width=width,
                height=height,
                status="Error",
                message="Source file is missing.",
            )
            items.append(item)
            error_count += 1
            continue

        source_suffix = suffix_for_path(source.source_path)
        target_suffix = final_suffix or source_suffix or ".jpg"
        if target_suffix not in OUTPUT_FORMAT_NAMES:
            target_suffix = ".jpg"
        target_name = _export_target_name(
            source.source_name,
            target_suffix,
            recipe.rename_prefix,
            recipe.rename_suffix,
        )
        target_path = _unique_export_target_path(destination_path, target_name, reserved_targets)
        reserved_targets.add(normalized_path_key(target_path))
        item = WorkflowExportItem(
            source=source,
            target_path=target_path,
            target_name=Path(target_path).name,
            target_suffix=target_suffix,
            width=width,
            height=height,
            status="Export",
            message="",
        )
        items.append(item)
        executable.append(item)

    output_label_parts: list[str] = []
    if final_suffix:
        output_label_parts.append(OUTPUT_FORMAT_NAMES.get(final_suffix, final_suffix.upper().lstrip(".")))
    if size_label:
        output_label_parts.append(size_label)

    return WorkflowExportPlan(
        recipe=recipe,
        destination_dir=normalized_destination,
        items=tuple(items),
        executable_items=tuple(executable),
        output_label=" | ".join(output_label_parts) if output_label_parts else "Original Size",
        error_count=error_count,
        can_apply=bool(executable) and error_count == 0,
        general_error="",
    )


def apply_workflow_export_plan(
    plan: WorkflowExportPlan,
    *,
    progress_callback=None,
) -> tuple[str, ...]:
    if not plan.executable_items:
        return ()

    written_paths: list[str] = []
    total = len(plan.executable_items)
    for index, item in enumerate(plan.executable_items, start=1):
        target_size = QSize(item.width, item.height) if item.width > 0 and item.height > 0 else QSize()
        loaded = _load_resize_image(
            item.source.source_path,
            target_size=target_size,
            ignore_orientation=False,
            strip_metadata=plan.recipe.strip_metadata,
        )
        image = loaded.image
        if item.width > 0 and item.height > 0:
            image = _scaled_image(image, target_size=QSize(item.width, item.height), shrink_only=True)
        _save_resized_image(
            image,
            target_path=item.target_path,
            target_suffix=item.target_suffix,
            exif_bytes=None if plan.recipe.strip_metadata else loaded.exif_bytes,
            icc_profile=None if plan.recipe.strip_metadata else loaded.icc_profile,
        )
        written_paths.append(item.target_path)
        if progress_callback is not None:
            progress_callback(index, total, f"Saved {item.target_name}")
    return tuple(written_paths)


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
