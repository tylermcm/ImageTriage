from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


_INVALID_NAME_CHARS = set('<>:"/\\|?*')
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}


@dataclass(slots=True)
class FileMove:
    source_path: str
    target_path: str


def unique_destination(directory: str, filename: str) -> str:
    candidate = Path(directory) / filename
    if not candidate.exists():
        return str(candidate)

    stem = candidate.stem
    suffix = candidate.suffix
    counter = 1
    while True:
        alternative = candidate.with_name(f"{stem}_{counter}{suffix}")
        if not alternative.exists():
            return str(alternative)
        counter += 1


def move_paths(source_paths: tuple[str, ...], destination_dir: str) -> tuple[FileMove, ...]:
    Path(destination_dir).mkdir(parents=True, exist_ok=True)
    file_moves: list[FileMove] = []
    moved_targets: list[FileMove] = []
    try:
        for source_path in source_paths:
            destination = unique_destination(destination_dir, Path(source_path).name)
            shutil.move(source_path, destination)
            file_move = FileMove(source_path=source_path, target_path=destination)
            file_moves.append(file_move)
            moved_targets.append(file_move)
    except OSError as exc:
        for moved in reversed(moved_targets):
            if os.path.exists(moved.target_path):
                Path(moved.source_path).parent.mkdir(parents=True, exist_ok=True)
                shutil.move(moved.target_path, moved.source_path)
        raise exc
    return tuple(file_moves)


def copy_paths(source_paths: tuple[str, ...], destination_dir: str) -> tuple[FileMove, ...]:
    Path(destination_dir).mkdir(parents=True, exist_ok=True)
    file_copies: list[FileMove] = []
    created_targets: list[FileMove] = []
    try:
        for source_path in source_paths:
            destination = unique_destination(destination_dir, Path(source_path).name)
            shutil.copy2(source_path, destination)
            file_copy = FileMove(source_path=source_path, target_path=destination)
            file_copies.append(file_copy)
            created_targets.append(file_copy)
    except OSError as exc:
        for created in reversed(created_targets):
            if os.path.exists(created.target_path):
                os.remove(created.target_path)
        raise exc
    return tuple(file_copies)


def create_folder(parent_dir: str, name: str) -> str:
    folder_name = _validated_leaf_name(name)
    destination = Path(parent_dir) / folder_name
    if destination.exists():
        raise FileExistsError(f"A folder named '{folder_name}' already exists.")
    destination.mkdir(parents=True, exist_ok=False)
    return str(destination)


def rename_folder(folder: str, new_name: str) -> str:
    source = Path(folder)
    if not source.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")
    validated_name = _validated_leaf_name(new_name)
    destination = source.with_name(validated_name)
    source_key = os.path.normcase(os.path.normpath(str(source)))
    destination_key = os.path.normcase(os.path.normpath(str(destination)))
    if destination.exists() and destination_key != source_key:
        raise FileExistsError(f"A folder named '{validated_name}' already exists.")
    os.replace(source, destination)
    return str(destination)


def move_folder(folder: str, destination_parent: str) -> str:
    source = Path(folder)
    if not source.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")
    destination_root = Path(destination_parent)
    if not destination_root.is_dir():
        raise NotADirectoryError(f"Destination folder not found: {destination_parent}")

    resolved_source = source.resolve(strict=False)
    resolved_destination_root = destination_root.resolve(strict=False)
    source_key = os.path.normcase(os.path.normpath(str(resolved_source)))
    destination_root_key = os.path.normcase(os.path.normpath(str(resolved_destination_root)))
    if destination_root_key == os.path.normcase(os.path.normpath(str(resolved_source.parent))):
        raise ValueError("Choose a different destination folder.")
    if destination_root_key == source_key or destination_root_key.startswith(source_key + os.sep):
        raise ValueError("A folder cannot be moved into itself.")

    destination = destination_root / source.name
    destination_key = os.path.normcase(os.path.normpath(str(destination.resolve(strict=False))))
    if destination.exists() and destination_key != source_key:
        raise FileExistsError(f"A folder named '{source.name}' already exists in the destination.")

    shutil.move(str(source), str(destination))
    return str(destination)


def delete_folder(folder: str) -> None:
    target = Path(folder)
    if not target.exists():
        return
    if any(target.iterdir()):
        shutil.rmtree(target)
        return
    target.rmdir()


def resolve_primary_rename_name(source_path: str, requested_name: str) -> str:
    requested = (requested_name or "").strip()
    if not requested:
        raise ValueError("Enter a file name.")

    source = Path(source_path)
    source_suffix = source.suffix
    if not source_suffix:
        raise ValueError("This file does not have a renameable extension.")

    if _has_path_components(requested):
        raise ValueError("Enter only a file name, not a full path.")

    candidate = requested
    candidate_suffix = Path(candidate).suffix
    if not candidate_suffix:
        candidate = f"{candidate}{source_suffix}"
    elif candidate_suffix.casefold() != source_suffix.casefold():
        raise ValueError(f"Keep the existing {source_suffix} extension when renaming.")

    return _validated_leaf_name(candidate)


def rename_bundle_paths(
    source_paths: tuple[str, ...],
    primary_source_path: str,
    requested_primary_name: str,
) -> tuple[FileMove, ...]:
    return rename_paths(plan_rename_bundle_paths(source_paths, primary_source_path, requested_primary_name))


def plan_rename_bundle_paths(
    source_paths: tuple[str, ...],
    primary_source_path: str,
    requested_primary_name: str,
) -> tuple[FileMove, ...]:
    new_primary_name = resolve_primary_rename_name(primary_source_path, requested_primary_name)
    primary_source = Path(primary_source_path)
    primary_name = primary_source.name
    primary_stem = primary_source.stem
    new_primary_stem = Path(new_primary_name).stem

    plans: list[FileMove] = []
    for source_path in source_paths:
        source = Path(source_path)
        target_name = _renamed_bundle_name(
            source.name,
            primary_name=primary_name,
            primary_stem=primary_stem,
            new_primary_name=new_primary_name,
            new_primary_stem=new_primary_stem,
        )
        plans.append(FileMove(source_path=str(source), target_path=str(source.with_name(target_name))))
    return tuple(plans)

def rename_paths(
    plans: tuple[FileMove, ...],
    *,
    progress_callback=None,
) -> tuple[FileMove, ...]:
    normalized_plans = tuple(
        plan
        for plan in plans
        if os.path.normcase(os.path.normpath(plan.source_path))
        != os.path.normcase(os.path.normpath(plan.target_path))
    )
    if not normalized_plans:
        return ()

    source_keys = {
        os.path.normcase(os.path.normpath(plan.source_path))
        for plan in normalized_plans
    }
    target_keys: set[str] = set()
    for plan in normalized_plans:
        target_key = os.path.normcase(os.path.normpath(plan.target_path))
        if target_key in target_keys:
            raise FileExistsError("Two files in this bundle would be renamed to the same destination.")
        target_keys.add(target_key)
        if os.path.exists(plan.target_path) and target_key not in source_keys:
            raise FileExistsError(f"Target already exists: {plan.target_path}")

    temp_paths: list[tuple[FileMove, str]] = []
    finalized: list[tuple[FileMove, str]] = []
    total_steps = len(normalized_plans) * 2
    completed_steps = 0

    def emit_progress(message: str) -> None:
        nonlocal completed_steps
        completed_steps += 1
        if progress_callback is not None:
            progress_callback(completed_steps, total_steps, message)

    try:
        for plan in normalized_plans:
            temp_path = _temporary_rename_path(plan.source_path)
            os.replace(plan.source_path, temp_path)
            temp_paths.append((plan, temp_path))
            emit_progress(f"Preparing {Path(plan.source_path).name}")

        for plan, temp_path in temp_paths:
            os.replace(temp_path, plan.target_path)
            finalized.append((plan, temp_path))
            emit_progress(f"Renaming {Path(plan.target_path).name}")
    except OSError as exc:
        for plan, temp_path in reversed(finalized):
            if os.path.exists(plan.target_path):
                os.replace(plan.target_path, temp_path)
        for plan, temp_path in reversed(temp_paths):
            if os.path.exists(temp_path):
                os.replace(temp_path, plan.source_path)
        raise exc

    return normalized_plans


def _renamed_bundle_name(
    source_name: str,
    *,
    primary_name: str,
    primary_stem: str,
    new_primary_name: str,
    new_primary_stem: str,
) -> str:
    source_lower = source_name.casefold()
    primary_name_lower = primary_name.casefold()
    primary_stem_lower = primary_stem.casefold()

    if source_lower == primary_name_lower:
        return new_primary_name
    if source_lower == f"{primary_name_lower}.xmp":
        return f"{new_primary_name}.xmp"
    if source_lower.startswith(primary_stem_lower):
        return f"{new_primary_stem}{source_name[len(primary_stem):]}"
    raise ValueError(f"Could not derive a safe renamed path for {source_name}.")


def _temporary_rename_path(source_path: str) -> str:
    source = Path(source_path)
    counter = 0
    while True:
        candidate = source.with_name(f".image-triage-rename-{counter}-{source.name}")
        if not candidate.exists():
            return str(candidate)
        counter += 1


def _validated_leaf_name(value: str) -> str:
    name = (value or "").strip()
    if not name:
        raise ValueError("Enter a name.")
    if _has_path_components(name):
        raise ValueError("Enter only a name, not a full path.")
    if any(char in _INVALID_NAME_CHARS for char in name):
        raise ValueError("Names cannot contain < > : \" / \\ | ? *")
    if name in {".", ".."}:
        raise ValueError("That name is not allowed.")
    if name[-1] in {" ", "."}:
        raise ValueError("Names cannot end with a space or period on Windows.")
    stem = Path(name).stem or Path(name).name
    if stem.casefold().upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(f"'{stem}' is reserved on Windows.")
    return name


def _has_path_components(value: str) -> bool:
    normalized = value.replace("/", os.sep).replace("\\", os.sep)
    return Path(normalized).name != normalized
