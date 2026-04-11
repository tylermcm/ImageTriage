from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Signal

from .formats import MODEL_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .image_ops import load_image_for_transform, normalized_output_path_key, save_transformed_image
from .image_resize import OUTPUT_FORMAT_NAMES, WRITABLE_IMAGE_SUFFIXES


@dataclass(slots=True, frozen=True)
class ConvertFormat:
    suffix: str
    name: str
    description: str


@dataclass(slots=True, frozen=True)
class ConvertSourceItem:
    source_path: str
    source_name: str


@dataclass(slots=True)
class ConvertOptions:
    output_suffix: str = ".png"
    overwrite: bool = False
    strip_metadata: bool = False


@dataclass(slots=True)
class ConvertPlanItem:
    source: ConvertSourceItem
    target_path: str
    target_name: str
    status: str
    message: str = ""


@dataclass(slots=True)
class ConvertPlan:
    items: tuple[ConvertPlanItem, ...]
    executable_items: tuple[ConvertPlanItem, ...]
    output_suffix: str
    output_label: str
    copy_mode: str
    executable_count: int
    error_count: int
    can_apply: bool
    general_error: str = ""


FORMATS: tuple[ConvertFormat, ...] = (
    ConvertFormat(".png", "PNG", "Portable Network Graphics (.png)"),
    ConvertFormat(".jpg", "JPEG", "JPEG image (.jpg)"),
    ConvertFormat(".webp", "WebP", "Google WebP image (.webp)"),
    ConvertFormat(".tiff", "TIFF", "Tagged Image File Format (.tiff)"),
    ConvertFormat(".bmp", "BMP", "Bitmap image (.bmp)"),
)


def convert_formats() -> tuple[ConvertFormat, ...]:
    return FORMATS


def default_convert_format() -> ConvertFormat:
    return FORMATS[0]


def format_for_suffix(suffix: str) -> ConvertFormat:
    normalized = _normalize_output_suffix(suffix)
    for item in FORMATS:
        if item.suffix == normalized:
            return item
    return default_convert_format()


def build_convert_plan(sources: list[ConvertSourceItem], options: ConvertOptions) -> ConvertPlan:
    target_format = format_for_suffix(options.output_suffix)
    target_suffix = target_format.suffix
    if target_suffix not in OUTPUT_FORMAT_NAMES:
        items = tuple(
            ConvertPlanItem(
                source=source,
                target_path=source.source_path,
                target_name=source.source_name,
                status="Error",
                message="Choose a supported output format.",
            )
            for source in sources
        )
        return ConvertPlan(
            items=items,
            executable_items=(),
            output_suffix=target_suffix,
            output_label=target_format.name,
            copy_mode="Overwrite" if options.overwrite else "Copy",
            executable_count=0,
            error_count=len(items),
            can_apply=False,
            general_error="Choose a supported output format.",
        )

    items: list[ConvertPlanItem] = []
    executable_items: list[ConvertPlanItem] = []
    reserved_target_keys: set[str] = set()
    error_count = 0
    copy_mode = "Overwrite" if options.overwrite else "Copy"

    for source in sources:
        source_path = source.source_path
        source_name = source.source_name
        if not source_path or not os.path.exists(source_path):
            items.append(
                ConvertPlanItem(
                    source=source,
                    target_path=source_path,
                    target_name=source_name,
                    status="Error",
                    message="Source file is missing.",
                )
            )
            error_count += 1
            continue

        source_suffix = suffix_for_path(source_path)
        if source_suffix in RAW_SUFFIXES:
            items.append(
                ConvertPlanItem(
                    source=source,
                    target_path=source_path,
                    target_name=source_name,
                    status="Error",
                    message="RAW files are not supported by Convert.",
                )
            )
            error_count += 1
            continue
        if source_suffix in MODEL_SUFFIXES:
            items.append(
                ConvertPlanItem(
                    source=source,
                    target_path=source_path,
                    target_name=source_name,
                    status="Error",
                    message="This file type is not supported by Convert.",
                )
            )
            error_count += 1
            continue

        if options.overwrite:
            target_path = source_path if source_suffix == target_suffix else str(Path(source_path).with_suffix(target_suffix))
            target_key = normalized_output_path_key(target_path)
            if target_key in reserved_target_keys:
                items.append(
                    ConvertPlanItem(
                        source=source,
                        target_path=target_path,
                        target_name=Path(target_path).name,
                        status="Error",
                        message="Another selected file already targets this output path.",
                    )
                )
                error_count += 1
                continue
            reserved_target_keys.add(target_key)
            target_exists = os.path.exists(target_path) and (
                normalized_output_path_key(target_path) != normalized_output_path_key(source_path)
            )
            status = "Overwrite" if target_exists or target_path == source_path else "Convert"
            message = f"Converts to {target_format.name}."
            items.append(
                ConvertPlanItem(
                    source=source,
                    target_path=target_path,
                    target_name=Path(target_path).name,
                    status=status,
                    message=message,
                )
            )
            executable_items.append(items[-1])
            continue

        requested_name = _copy_target_name(source_name, target_suffix, target_format.name)
        target_path = _unique_copy_target_path(source_path, requested_name, reserved_target_keys)
        reserved_target_keys.add(normalized_output_path_key(target_path))
        message = f"Converts to {target_format.name}."
        if source_suffix == target_suffix:
            message = f"Creates a {target_format.name} copy."
        items.append(
            ConvertPlanItem(
                source=source,
                target_path=target_path,
                target_name=Path(target_path).name,
                status="Copy",
                message=message,
            )
        )
        executable_items.append(items[-1])

    return ConvertPlan(
        items=tuple(items),
        executable_items=tuple(executable_items),
        output_suffix=target_suffix,
        output_label=target_format.name,
        copy_mode=copy_mode,
        executable_count=len(executable_items),
        error_count=error_count,
        can_apply=bool(executable_items) and error_count == 0,
    )


def apply_convert_plan(
    plan: ConvertPlan,
    options: ConvertOptions,
    *,
    progress_callback=None,
) -> tuple[str, ...]:
    if not plan.executable_items:
        return ()

    written_paths: list[str] = []
    total_steps = len(plan.executable_items)
    for index, item in enumerate(plan.executable_items, start=1):
        _convert_item(item, plan, options)
        written_paths.append(item.target_path)
        if progress_callback is not None:
            progress_callback(index, total_steps, f"Saved {item.target_name}")
    return tuple(written_paths)


class ConvertApplySignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class ConvertApplyTask(QRunnable):
    def __init__(self, plan: ConvertPlan, options: ConvertOptions) -> None:
        super().__init__()
        self.plan = plan
        self.options = options
        self.signals = ConvertApplySignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        total = max(1, len(self.plan.executable_items))
        self.signals.started.emit(total)
        try:
            written_paths = apply_convert_plan(
                self.plan,
                self.options,
                progress_callback=lambda current, total_steps, message: self.signals.progress.emit(current, total_steps, message),
            )
        except Exception as exc:  # pragma: no cover - worker UI error path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(written_paths)


def _convert_item(item: ConvertPlanItem, plan: ConvertPlan, options: ConvertOptions) -> None:
    loaded = load_image_for_transform(
        item.source.source_path,
        target_size=QSize(),
        ignore_orientation=False,
        strip_metadata=options.strip_metadata,
    )
    save_transformed_image(
        loaded.image,
        target_path=item.target_path,
        target_suffix=plan.output_suffix,
        exif_bytes=None if options.strip_metadata else loaded.exif_bytes,
        icc_profile=None if options.strip_metadata else loaded.icc_profile,
    )


def _normalize_output_suffix(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized:
        return default_convert_format().suffix
    if not normalized.startswith("."):
        normalized = f".{normalized}"
    if normalized == ".jpeg":
        return ".jpg"
    if normalized == ".tif":
        return ".tiff"
    return normalized


def _copy_target_name(source_name: str, target_suffix: str, format_name: str) -> str:
    source_path = Path(source_name)
    return f"{source_path.stem} ({format_name}){target_suffix}"


def _unique_copy_target_path(source_path: str, requested_name: str, reserved_target_keys: set[str]) -> str:
    directory = Path(source_path).parent
    requested = Path(requested_name)
    stem = requested.stem
    suffix = requested.suffix
    counter = 0
    while True:
        candidate_name = requested.name if counter == 0 else f"{stem}_{counter}{suffix}"
        candidate_path = directory / candidate_name
        candidate_key = normalized_output_path_key(candidate_path)
        if candidate_key not in reserved_target_keys and not candidate_path.exists():
            return str(candidate_path)
        counter += 1
