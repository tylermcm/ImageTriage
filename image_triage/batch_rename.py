from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, Signal

from .file_ops import FileMove, plan_rename_bundle_paths, rename_paths
from .models import ImageRecord
from .xmp import sidecar_bundle_paths


class RenameCaseMode(str, Enum):
    KEEP = "Keep"
    LOWER = "lowercase"
    UPPER = "UPPERCASE"
    TITLE = "Title Case"


@dataclass(slots=True)
class BatchRenameRules:
    new_name: str = ""
    prefix: str = ""
    suffix: str = ""
    case_mode: RenameCaseMode = RenameCaseMode.KEEP
    collapse_whitespace: bool = False
    sequence_enabled: bool = False
    sequence_start: int = 1
    sequence_step: int = 1
    sequence_padding: int = 3
    sequence_separator: str = "_"


@dataclass(slots=True)
class BatchRenamePreviewItem:
    record: ImageRecord
    source_name: str
    target_name: str
    status: str
    message: str = ""
    planned_moves: tuple[FileMove, ...] = ()


@dataclass(slots=True)
class BatchRenamePreview:
    items: tuple[BatchRenamePreviewItem, ...]
    planned_moves: tuple[FileMove, ...]
    renamed_count: int
    unchanged_count: int
    error_count: int
    can_apply: bool
    general_error: str = ""


def build_batch_rename_preview(records: list[ImageRecord], rules: BatchRenameRules) -> BatchRenamePreview:
    items: list[BatchRenamePreviewItem] = []
    move_owners: dict[int, list[FileMove]] = {}
    source_owner_by_key: dict[str, int] = {}

    for index, record in enumerate(records):
        source_name = record.name
        try:
            transformed_stem = _transformed_stem(Path(source_name).stem, index, rules)
            requested_name = f"{transformed_stem}{Path(source_name).suffix}"
            planned_moves = plan_rename_bundle_paths(_record_paths(record), record.path, requested_name)
        except ValueError as exc:
            items.append(
                BatchRenamePreviewItem(
                    record=record,
                    source_name=source_name,
                    target_name=source_name,
                    status="Error",
                    message=str(exc),
                )
            )
            continue

        target_name = source_name
        if planned_moves:
            target_name = next(
                (
                    Path(move.target_path).name
                    for move in planned_moves
                    if _path_key(move.source_path) == _path_key(record.path)
                ),
                source_name,
            )
        status = "Rename" if planned_moves else "Unchanged"
        item = BatchRenamePreviewItem(
            record=record,
            source_name=source_name,
            target_name=target_name,
            status=status,
            planned_moves=planned_moves,
        )
        items.append(item)
        move_owners[id(item)] = list(planned_moves)
        for move in planned_moves:
            source_owner_by_key[_path_key(move.source_path)] = id(item)

    target_to_owner_ids: dict[str, set[int]] = {}
    target_to_existing_conflict_ids: dict[str, set[int]] = {}
    for item in items:
        if item.status != "Rename":
            continue
        owner_id = id(item)
        for move in move_owners[owner_id]:
            target_key = _path_key(move.target_path)
            target_to_owner_ids.setdefault(target_key, set()).add(owner_id)
            if os.path.exists(move.target_path) and target_key not in source_owner_by_key:
                target_to_existing_conflict_ids.setdefault(target_key, set()).add(owner_id)

    preview_items: list[BatchRenamePreviewItem] = []
    renamed_count = 0
    unchanged_count = 0
    error_count = 0
    planned_moves: list[FileMove] = []
    for item in items:
        if item.status != "Rename":
            if item.status == "Unchanged":
                unchanged_count += 1
            else:
                error_count += 1
            preview_items.append(item)
            continue

        owner_id = id(item)
        duplicate_conflict = any(len(target_to_owner_ids[_path_key(move.target_path)]) > 1 for move in move_owners[owner_id])
        existing_conflict = any(owner_id in target_to_existing_conflict_ids.get(_path_key(move.target_path), set()) for move in move_owners[owner_id])
        if duplicate_conflict or existing_conflict:
            message = "Two items would rename to the same target."
            if existing_conflict:
                message = "A target file already exists outside the rename batch."
            preview_items.append(
                BatchRenamePreviewItem(
                    record=item.record,
                    source_name=item.source_name,
                    target_name=item.target_name,
                    status="Error",
                    message=message,
                    planned_moves=item.planned_moves,
                )
            )
            error_count += 1
            continue

        preview_items.append(item)
        renamed_count += 1
        planned_moves.extend(item.planned_moves)

    return BatchRenamePreview(
        items=tuple(preview_items),
        planned_moves=tuple(planned_moves),
        renamed_count=renamed_count,
        unchanged_count=unchanged_count,
        error_count=error_count,
        can_apply=(renamed_count > 0 and error_count == 0),
    )


def _transformed_stem(stem: str, index: int, rules: BatchRenameRules) -> str:
    working = rules.new_name.strip() or stem

    if rules.case_mode == RenameCaseMode.LOWER:
        working = working.lower()
    elif rules.case_mode == RenameCaseMode.UPPER:
        working = working.upper()
    elif rules.case_mode == RenameCaseMode.TITLE:
        working = _title_case(working)

    if rules.collapse_whitespace:
        working = re.sub(r"\s+", " ", working).strip()

    working = f"{rules.prefix}{working}{rules.suffix}"

    if rules.sequence_enabled:
        sequence_value = rules.sequence_start + (index * rules.sequence_step)
        sequence_text = str(sequence_value).zfill(max(0, rules.sequence_padding))
        separator = rules.sequence_separator if working and rules.sequence_separator else ""
        working = f"{working}{separator}{sequence_text}"

    working = working.strip()
    if not working:
        raise ValueError("This rename rule would produce an empty file name.")
    return working


def _record_paths(record: ImageRecord) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for path in (*record.stack_paths, *sidecar_bundle_paths(record)):
        key = _path_key(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return tuple(ordered)


def _path_key(path: str) -> str:
    return os.path.normcase(os.path.normpath(path))


def _title_case(value: str) -> str:
    tokens = re.split(r"([\s._-]+)", value)
    return "".join(token.title() if index % 2 == 0 else token for index, token in enumerate(tokens))


class BatchRenameApplySignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class BatchRenameApplyTask(QRunnable):
    def __init__(self, planned_moves: tuple[FileMove, ...]) -> None:
        super().__init__()
        self.planned_moves = planned_moves
        self.signals = BatchRenameApplySignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        total_steps = max(1, len(self.planned_moves) * 2)
        self.signals.started.emit(total_steps)
        try:
            applied_moves = rename_paths(
                self.planned_moves,
                progress_callback=lambda current, total, message: self.signals.progress.emit(current, total, message),
            )
        except Exception as exc:  # pragma: no cover - UI worker failure path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(applied_moves)
