"""Rename one photo from the grid (F2 with a single photo): the batch rename's own plan and rules, for one bundle.

The plan is `batch_rename.build_batch_rename_preview` with one record and a new name, so the RAW, its JPEG and the sidecars keep the
same stem, the name rules are the destination's (`file_ops.resolve_primary_rename_name`, `path_policy.check_name`) and a name that is
taken is an error, never an overwrite. The files are renamed on the file-operation worker (`file_jobs.FileJobs`, through
`FileActions`); the saved decision, the collections and the edit data follow as they do after a batch rename, and one undo step takes
all of it back.

`check` is what the grid asks while the name is typed: it looks at the name only, never at the disk (the folder may be a share).
Whether the name is free is decided by the worker, and the answer comes back on `PhotoModel.renameFinished`.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..batch_rename import BatchRenameRules, build_batch_rename_preview
from ..file_ops import FileMove, name_warning, record_after_moves, rename_paths, resolve_primary_rename_name
from ..models import ImageRecord

TAKEN = "A file with this name already exists."


def _file_name(row: dict) -> str:
    name = str(row["name"])
    return name if Path(name).suffix else f"{name}.{row['ext']}"


def new_stem(path: str, typed: str) -> str:
    """The stem `typed` asks for. The grid shows names without their extension, so what is typed is a stem; a name typed with the
    photo's own extension means the same thing. `ValueError` says why a name cannot be used."""
    typed = (typed or "").strip()
    suffix = Path(path).suffix
    if suffix and typed.casefold().endswith(suffix.casefold()) and len(typed) > len(suffix):
        typed = typed[: -len(suffix)]
    if not typed.strip():
        raise ValueError("Enter a file name.")
    return Path(resolve_primary_rename_name(path, f"{typed}{suffix}")).stem


def check(path: str, typed: str) -> dict:
    """{"error", "warning", "same"} for a name being typed. Nothing on the disk is looked at."""
    try:
        stem = new_stem(path, typed)
    except ValueError as exc:
        return {"error": str(exc), "warning": "", "same": False}
    name = f"{stem}{Path(path).suffix}"
    return {"error": "", "warning": name_warning(name, os.path.dirname(path)), "same": name == os.path.basename(path)}


def _work(ctx, record: ImageRecord, stem: str) -> dict:
    """On the worker: plan the bundle's rename, refuse a name that is taken, rename, and bring the edit data along."""
    from ..edit_carry import carry_edits

    ctx.step(0, record.name)
    preview = build_batch_rename_preview([record], BatchRenameRules(new_name=stem, sequence_enabled=False))
    item = preview.items[0]
    if item.status == "Unchanged":
        return {"unchanged": True}
    if item.status != "Rename":
        taken = any(os.path.exists(move.target_path) for move in item.planned_moves)
        return {"error": TAKEN if taken else (item.message or "This name cannot be used.")}
    try:
        rename_paths(item.planned_moves)
    except (OSError, ValueError) as exc:            # a name taken since the plan was made, or a file that would not move: nothing was renamed
        return {"error": TAKEN if isinstance(exc, FileExistsError) else str(exc)}
    left = carry_edits([(move.source_path, move.target_path) for move in item.planned_moves])
    ctx.step(1, "")
    return {"moves": item.planned_moves, "left": left}


def _work_back(ctx, moves: tuple) -> dict:
    from ..edit_carry import carry_edits

    back = tuple(FileMove(source_path=move.target_path, target_path=move.source_path) for move in moves)
    try:
        rename_paths(back)
    except (OSError, ValueError) as exc:
        return {"error": TAKEN if isinstance(exc, FileExistsError) else str(exc)}
    carry_edits([(move.source_path, move.target_path) for move in back])
    return {"moves": back}


def rename_row(actions, row: int, typed: str) -> str:
    """Start renaming the shown photo `row` to `typed`. "" when the rename was started (or nothing needs doing: the name is the one it
    has); otherwise why not. The result is announced on `photos.renameFinished(old path, new path, error)`."""
    photos = actions._photos
    item = photos.row_at(row)
    if item is None:
        return "There is no photo to rename."
    source = item.get("record")
    if source is None:
        return "These are sample photos: there is no file to rename."
    try:
        stem = new_stem(source.path, typed)
    except ValueError as exc:
        return str(exc)
    # the photo's own size and time: a saved decision is found again by them (as the batch rename builds its records)
    record = ImageRecord(path=source.path, name=_file_name(item), size=source.size, modified_ns=source.modified_ns,
                         companion_paths=tuple(item.get("companions", ())))
    annotation = item.get("annotation")
    started = actions._start(f"Renaming {record.name}", lambda ctx: _work(ctx, record, stem),
                             lambda result: _renamed(actions, record, annotation, result), total=1)
    return "" if started else "Another file operation is still running."


def _renamed(actions, record: ImageRecord, annotation, result: dict) -> None:
    photos = actions._photos
    if result.get("unchanged"):
        photos.renameFinished.emit(record.path, record.path, "")
        return
    if result.get("error"):
        photos.renameFinished.emit(record.path, "", result["error"])
        return
    moves = tuple(result["moves"])
    after = record_after_moves(record, moves)
    actions._rekey_decisions([(record.path, after, annotation)])
    actions._rekey_collections([(move.source_path, move.target_path) for move in moves])
    if result["left"]:
        actions._toast("warn", "Some edits did not follow the rename", ", ".join(result["left"][:3]))
    entry = {"id": photos.push_undo(f"rename of {record.name}", lambda: _undo(actions, record, after, annotation, moves))}
    actions._toast("success", f"Renamed {record.name}", f"To {after.name}.", "Undo", lambda: photos.undo_entry(entry["id"]))
    photos.renameFinished.emit(record.path, after.path, "")
    photos.reload()


def _undo(actions, record: ImageRecord, after: ImageRecord, annotation, moves: tuple) -> bool:
    photos = actions._photos
    # the decision as it is now, not as it was when the photo was renamed: it may have been changed since
    annotation = next((r.get("annotation") for r in photos.all_rows() if r["path"] == after.path), annotation)
    result = actions._run_and_wait(f"Renaming {after.name} back", lambda ctx: _work_back(ctx, moves), total=1)
    if result is None:
        return False
    if result.get("error"):
        actions._toast("error", "Could not undo the rename", result["error"])
        return False
    actions._rekey_collections([(move.source_path, move.target_path) for move in result["moves"]])
    actions._rekey_decisions([(after.path, record, annotation)])
    photos.renameFinished.emit(after.path, record.path, "")
    photos.reload()
    actions._toast("success", f"Renamed {after.name} back", f"To {record.name}.")
    return True
