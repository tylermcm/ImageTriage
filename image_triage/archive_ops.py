from __future__ import annotations

import os
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import py7zr
from PySide6.QtCore import QObject, QRunnable, Signal


@dataclass(slots=True, frozen=True)
class ArchiveFormat:
    key: str
    label: str
    suffix: str
    save_filter: str


@dataclass(slots=True, frozen=True)
class ArchiveEntry:
    source_path: str
    archive_name: str


ARCHIVE_FORMATS: tuple[ArchiveFormat, ...] = (
    ArchiveFormat("zip", "ZIP", ".zip", "ZIP Archive (*.zip)"),
    ArchiveFormat("7z", "7-Zip", ".7z", "7-Zip Archive (*.7z)"),
    ArchiveFormat("tar_gz", "TAR.GZ", ".tar.gz", "TAR.GZ Archive (*.tar.gz)"),
)

EXTRACT_ARCHIVE_FILTER = (
    "Supported Archives (*.zip *.7z *.tar *.tar.gz *.tgz *.tar.bz2 *.tbz2 *.tar.xz *.txz);;"
    "ZIP Archive (*.zip);;"
    "7-Zip Archive (*.7z);;"
    "TAR.GZ Archive (*.tar.gz *.tgz);;"
    "TAR.BZ2 Archive (*.tar.bz2 *.tbz2);;"
    "TAR.XZ Archive (*.tar.xz *.txz);;"
    "TAR Archive (*.tar)"
)


def archive_formats() -> tuple[ArchiveFormat, ...]:
    return ARCHIVE_FORMATS


def archive_format_for_key(key: str) -> ArchiveFormat:
    normalized = (key or "").strip().lower()
    for item in ARCHIVE_FORMATS:
        if item.key == normalized:
            return item
    return ARCHIVE_FORMATS[0]


def ensure_archive_suffix(path: str, archive_format: ArchiveFormat) -> str:
    normalized = (path or "").strip()
    if not normalized:
        raise ValueError("Choose a destination archive path.")
    if archive_format.key == "tar_gz" and normalized.casefold().endswith(".tgz"):
        return normalized
    if normalized.casefold().endswith(archive_format.suffix.casefold()):
        return normalized
    return f"{normalized}{archive_format.suffix}"


def build_archive_entries(source_paths: list[str] | tuple[str, ...], *, root_dir: str | None = None) -> tuple[ArchiveEntry, ...]:
    unique_paths: list[str] = []
    seen: set[str] = set()
    for source_path in source_paths:
        source = (source_path or "").strip()
        if not source:
            continue
        key = _path_key(source)
        if key in seen:
            continue
        seen.add(key)
        unique_paths.append(source)
    if not unique_paths:
        raise ValueError("Choose one or more files to archive.")

    resolved_sources = [Path(path).resolve(strict=False) for path in unique_paths]
    missing = next((path for path in resolved_sources if not path.exists()), None)
    if missing is not None:
        raise FileNotFoundError(f"File not found: {missing}")

    root_path: Path | None = None
    if root_dir:
        candidate_root = Path(root_dir).resolve(strict=False)
        if candidate_root.exists() and candidate_root.is_dir():
            root_path = candidate_root
    if root_path is None:
        common_root = os.path.commonpath([str(path.parent) for path in resolved_sources])
        root_path = Path(common_root).resolve(strict=False)

    entries: list[ArchiveEntry] = []
    seen_archive_names: set[str] = set()
    for source in resolved_sources:
        archive_name = source.name
        try:
            archive_name = source.relative_to(root_path).as_posix()
        except ValueError:
            archive_name = source.name
        archive_name = _validated_archive_member_name(archive_name)
        if archive_name in seen_archive_names:
            raise ValueError("Two selected files would archive to the same destination path.")
        seen_archive_names.add(archive_name)
        entries.append(ArchiveEntry(source_path=str(source), archive_name=archive_name))
    return tuple(entries)


def create_archive(
    source_paths: list[str] | tuple[str, ...],
    archive_path: str,
    *,
    archive_key: str = "",
    root_dir: str | None = None,
    progress_callback=None,
) -> str:
    entries = build_archive_entries(source_paths, root_dir=root_dir)
    return _create_archive_from_entries(entries, archive_path, archive_key=archive_key, progress_callback=progress_callback)


def extract_archive(archive_path: str, destination_dir: str, *, progress_callback=None) -> tuple[str, ...]:
    archive_type = _archive_type_for_path(archive_path)
    destination = Path(destination_dir).resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True)

    if archive_type == "zip":
        return _extract_zip_archive(archive_path, destination, progress_callback=progress_callback)
    if archive_type == "7z":
        return _extract_7z_archive(archive_path, destination, progress_callback=progress_callback)
    if archive_type == "tar":
        return _extract_tar_archive(archive_path, destination, mode="r:", progress_callback=progress_callback)
    if archive_type == "tar_gz":
        return _extract_tar_archive(archive_path, destination, mode="r:gz", progress_callback=progress_callback)
    if archive_type == "tar_bz2":
        return _extract_tar_archive(archive_path, destination, mode="r:bz2", progress_callback=progress_callback)
    if archive_type == "tar_xz":
        return _extract_tar_archive(archive_path, destination, mode="r:xz", progress_callback=progress_callback)
    raise ValueError("Choose a supported archive file.")


class ArchiveTaskSignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class CreateArchiveTask(QRunnable):
    def __init__(
        self,
        source_paths: list[str] | tuple[str, ...],
        archive_path: str,
        *,
        archive_key: str,
        root_dir: str | None = None,
    ) -> None:
        super().__init__()
        self.source_paths = tuple(source_paths)
        self.archive_path = archive_path
        self.archive_key = archive_key
        self.root_dir = root_dir
        self.signals = ArchiveTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            entries = build_archive_entries(self.source_paths, root_dir=self.root_dir)
            self.signals.started.emit(max(1, len(entries)))
            created_path = _create_archive_from_entries(
                entries,
                self.archive_path,
                archive_key=self.archive_key,
                progress_callback=lambda current, total, message: self.signals.progress.emit(current, total, message),
            )
        except Exception as exc:  # pragma: no cover - worker UI failure path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(created_path)


class ExtractArchiveTask(QRunnable):
    def __init__(self, archive_path: str, destination_dir: str) -> None:
        super().__init__()
        self.archive_path = archive_path
        self.destination_dir = destination_dir
        self.signals = ArchiveTaskSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            total_members = _count_extractable_members(self.archive_path)
            self.signals.started.emit(max(1, total_members))
            extracted_paths = extract_archive(
                self.archive_path,
                self.destination_dir,
                progress_callback=lambda current, total, message: self.signals.progress.emit(current, total, message),
            )
        except Exception as exc:  # pragma: no cover - worker UI failure path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(extracted_paths)


def _count_extractable_members(archive_path: str) -> int:
    archive_type = _archive_type_for_path(archive_path)
    if archive_type == "zip":
        with zipfile.ZipFile(archive_path, "r") as archive:
            return max(1, len(archive.infolist()))
    if archive_type == "7z":
        with py7zr.SevenZipFile(archive_path, "r") as archive:
            return max(1, len(archive.list()))
    tar_mode = {
        "tar": "r:",
        "tar_gz": "r:gz",
        "tar_bz2": "r:bz2",
        "tar_xz": "r:xz",
    }.get(archive_type)
    if tar_mode is None:
        return 1
    with tarfile.open(archive_path, tar_mode) as archive:
        return max(1, len(archive.getmembers()))


def _create_archive_from_entries(
    entries: tuple[ArchiveEntry, ...],
    archive_path: str,
    *,
    archive_key: str,
    progress_callback=None,
) -> str:
    archive_format = archive_format_for_key(archive_key) if archive_key else _archive_format_for_path(archive_path)
    target_path = Path(ensure_archive_suffix(archive_path, archive_format))
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = target_path.with_name(f"{target_path.name}.part")
    if temporary_path.exists():
        temporary_path.unlink()

    try:
        if archive_format.key == "zip":
            _write_zip_archive(temporary_path, entries, progress_callback=progress_callback)
        elif archive_format.key == "7z":
            _write_7z_archive(temporary_path, entries, progress_callback=progress_callback)
        elif archive_format.key == "tar_gz":
            _write_tar_archive(temporary_path, entries, mode="w:gz", progress_callback=progress_callback)
        else:
            raise ValueError("Choose a supported archive format.")
        os.replace(temporary_path, target_path)
    except Exception:
        if temporary_path.exists():
            temporary_path.unlink()
        raise
    return str(target_path)


def _write_zip_archive(target_path: Path, entries: tuple[ArchiveEntry, ...], *, progress_callback=None) -> None:
    total = max(1, len(entries))
    with zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for index, entry in enumerate(entries, start=1):
            archive.write(entry.source_path, arcname=entry.archive_name, compress_type=zipfile.ZIP_DEFLATED, compresslevel=6)
            if progress_callback is not None:
                progress_callback(index, total, f"Added {Path(entry.source_path).name}")


def _write_7z_archive(target_path: Path, entries: tuple[ArchiveEntry, ...], *, progress_callback=None) -> None:
    total = max(1, len(entries))
    with py7zr.SevenZipFile(target_path, "w") as archive:
        for index, entry in enumerate(entries, start=1):
            archive.write(entry.source_path, arcname=entry.archive_name)
            if progress_callback is not None:
                progress_callback(index, total, f"Added {Path(entry.source_path).name}")


def _write_tar_archive(target_path: Path, entries: tuple[ArchiveEntry, ...], *, mode: str, progress_callback=None) -> None:
    total = max(1, len(entries))
    with tarfile.open(target_path, mode) as archive:
        for index, entry in enumerate(entries, start=1):
            archive.add(entry.source_path, arcname=entry.archive_name, recursive=False)
            if progress_callback is not None:
                progress_callback(index, total, f"Added {Path(entry.source_path).name}")


def _extract_zip_archive(archive_path: str, destination_dir: Path, *, progress_callback=None) -> tuple[str, ...]:
    with zipfile.ZipFile(archive_path, "r") as archive:
        infos = archive.infolist()
        targets = [(info, _validated_archive_member_path(destination_dir, info.filename)) for info in infos]
        extracted: list[str] = []
        total = max(1, len(targets))
        for index, (info, target_path) in enumerate(targets, start=1):
            if info.is_dir():
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target_path.open("wb") as destination:
                    shutil.copyfileobj(source, destination)
                extracted.append(str(target_path))
            if progress_callback is not None:
                progress_callback(index, total, f"Extracted {Path(info.filename).name or info.filename}")
    return tuple(extracted)


def _extract_tar_archive(archive_path: str, destination_dir: Path, *, mode: str, progress_callback=None) -> tuple[str, ...]:
    with tarfile.open(archive_path, mode) as archive:
        members = archive.getmembers()
        targets: list[tuple[tarfile.TarInfo, Path]] = []
        for member in members:
            if member.issym() or member.islnk():
                raise ValueError("Archives with symbolic links are not supported.")
            targets.append((member, _validated_archive_member_path(destination_dir, member.name)))

        extracted: list[str] = []
        total = max(1, len(targets))
        for index, (member, target_path) in enumerate(targets, start=1):
            if member.isdir():
                target_path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target_path.parent.mkdir(parents=True, exist_ok=True)
                extracted_stream = archive.extractfile(member)
                if extracted_stream is None:
                    raise ValueError(f"Could not extract {member.name}.")
                with extracted_stream, target_path.open("wb") as destination:
                    shutil.copyfileobj(extracted_stream, destination)
                extracted.append(str(target_path))
            if progress_callback is not None:
                progress_callback(index, total, f"Extracted {Path(member.name).name or member.name}")
    return tuple(extracted)


def _extract_7z_archive(archive_path: str, destination_dir: Path, *, progress_callback=None) -> tuple[str, ...]:
    with py7zr.SevenZipFile(archive_path, "r") as archive:
        infos = archive.list()
        targets: list[tuple[object, Path]] = []
        for info in infos:
            if getattr(info, "is_symlink", False):
                raise ValueError("Archives with symbolic links are not supported.")
            targets.append((info, _validated_archive_member_path(destination_dir, info.filename)))

        extracted: list[str] = []
        total = max(1, len(targets))
        for index, (info, target_path) in enumerate(targets, start=1):
            if getattr(info, "is_directory", False):
                target_path.mkdir(parents=True, exist_ok=True)
            else:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                archive.extract(path=destination_dir, targets=[info.filename])
                archive.reset()
                extracted.append(str(target_path))
            if progress_callback is not None:
                progress_callback(index, total, f"Extracted {Path(info.filename).name or info.filename}")
    return tuple(extracted)


def _archive_format_for_path(path: str) -> ArchiveFormat:
    archive_type = _archive_type_for_path(path)
    if archive_type in {"zip", "7z", "tar_gz"}:
        return archive_format_for_key(archive_type)
    raise ValueError("Archive creation supports .zip, .7z, and .tar.gz files.")


def _archive_type_for_path(path: str) -> str:
    normalized = (path or "").strip().casefold()
    if normalized.endswith(".tar.gz") or normalized.endswith(".tgz"):
        return "tar_gz"
    if normalized.endswith(".tar.bz2") or normalized.endswith(".tbz2"):
        return "tar_bz2"
    if normalized.endswith(".tar.xz") or normalized.endswith(".txz"):
        return "tar_xz"
    if normalized.endswith(".tar"):
        return "tar"
    if normalized.endswith(".zip"):
        return "zip"
    if normalized.endswith(".7z"):
        return "7z"
    raise ValueError("Choose a supported archive file.")


def _validated_archive_member_name(name: str) -> str:
    normalized = (name or "").replace("\\", "/").strip("/")
    if not normalized:
        raise ValueError("Archive items need a valid file name.")
    pure_path = PurePosixPath(normalized)
    if pure_path.is_absolute():
        raise ValueError("Archive items cannot use absolute paths.")
    if any(part in {"", ".", ".."} for part in pure_path.parts):
        raise ValueError("Archive items cannot escape the destination folder.")
    if any(":" in part for part in pure_path.parts):
        raise ValueError("Archive items cannot use drive-qualified paths.")
    return pure_path.as_posix()


def _validated_archive_member_path(destination_dir: Path, member_name: str) -> Path:
    normalized_name = _validated_archive_member_name(member_name)
    destination_root = destination_dir.resolve(strict=False)
    relative_path = Path(*PurePosixPath(normalized_name).parts)
    candidate = (destination_root / relative_path).resolve(strict=False)
    try:
        candidate.relative_to(destination_root)
    except ValueError as exc:
        raise ValueError("Archive item would extract outside the destination folder.") from exc
    return candidate


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))
