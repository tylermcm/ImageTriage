"""The RAW editor's own AI models, as the AI Center shows them: AI Denoise (RawNIND, ``ai_model.rawnind_installation``) and Super
Resolution (Real-ESRGAN x2 and x4, ``upscale_core.MODEL_SPECS``).

Both are converted to ONNX from the authors' checkpoints (``scripts/export_rawnind_onnx.py``, ``scripts/export_realesrgan_onnx.py``)
and hosted as the assets of one GitHub release of Image Triage (``DOWNLOAD_BASE``; the authors' credits and licenses are in its notes).
``download`` fetches the files a model lacks through the model store's downloader (https only, resumable, and nothing is put in place
until its size and SHA-256 match). A file can also be installed from disk (``install_file``): it is recognised by its SHA-256 (never by
its name), copied into the managed model folder under a temporary name, checked again, and only then put in place. Remove deletes the
files.

A file's digest is remembered by its path, size and modification time, so looking at the models again does not read 170 MB again.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ModelFile:
    folder: Path
    filename: str
    sha256: str
    size: int = 0                       # 0: not known in advance (checked by digest only)

    @property
    def path(self) -> Path:
        return self.folder / self.filename


@dataclass(frozen=True)
class EditorModel:
    key: str
    name: str
    mb: int
    files: tuple[ModelFile, ...]


def models() -> dict[str, EditorModel]:
    from .ai_model import RAWNIND_MODEL_SIZE_MB, rawnind_installation
    from .upscale_core import MODEL_SPECS
    from .upscale_service import upscale_model_dir

    denoise = rawnind_installation()
    sr_dir = Path(upscale_model_dir())
    sr_files = tuple(ModelFile(sr_dir, spec.filename, spec.sha256, spec.size) for spec in MODEL_SPECS.values())
    return {
        "denoise": EditorModel("denoise", "AI Denoise", RAWNIND_MODEL_SIZE_MB,
                               tuple(ModelFile(denoise.install_dir, name, sha) for name, sha in (denoise.expected_sha256 or {}).items())),
        "superres": EditorModel("superres", "Super Resolution", max(1, round(sum(f.size for f in sr_files) / 1e6)), sr_files),
    }


KEYS = ("denoise", "superres")

# The release whose assets are the files, one per name. A new conversion is a new release (new digests), never a replaced asset.
DOWNLOAD_BASE = "https://github.com/tylermcm/ImageTriage/releases/download/editor-models-1/"
# Sizes the code that names a file does not give (ai_model.rawnind_installation knows only digests).
_SIZES = {"rawnind_bayer.onnx": 31053428}


def file_size(file: ModelFile) -> int:
    return file.size or _SIZES.get(file.filename, 0)


def sizes(key: str) -> dict[str, int]:
    """File name -> bytes, for the AI Center's progress."""
    return {file.filename: file_size(file) for file in models()[key].files}


def download(key: str, progress_callback=None, cancel_check=None) -> None:
    """Fetch every file of ``key`` that is missing or damaged. Raises the model store's ModelInstallError when one fails."""
    from .ai_model_store import PARTIAL_SUFFIX, _download_file, _ResumePlan

    for file in models()[key].files:
        if not _file_ok(file):
            continue
        file.folder.mkdir(parents=True, exist_ok=True)
        _download_file(url=DOWNLOAD_BASE + file.filename, destination=file.path, label=file.filename,
                       progress_callback=progress_callback, cancel_check=cancel_check,
                       resume=_ResumePlan(partial=file.path.with_name(file.filename + PARTIAL_SUFFIX + "dl"),
                                          size=file_size(file), sha256=file.sha256))

_digests: dict[tuple[str, int, int], str] = {}
_lock = threading.Lock()


def _digest(path: Path) -> str:
    stat = path.stat()
    key = (os.path.normcase(str(path)), stat.st_size, stat.st_mtime_ns)
    with _lock:
        known = _digests.get(key)
    if known is not None:
        return known
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            hasher.update(chunk)
    value = hasher.hexdigest()
    with _lock:
        _digests[key] = value
    return value


def _file_ok(file: ModelFile) -> str:
    """"" when the file is there and is the expected one, "missing", or "damaged"."""
    try:
        size = file.path.stat().st_size
    except OSError:
        return "missing"
    if file.size and size != file.size:
        return "damaged"
    try:
        return "" if _digest(file.path).casefold() == file.sha256.casefold() else "damaged"
    except OSError:
        return "damaged"


def status(key: str) -> dict:
    """``{"state": "ready" | "missing" | "partial" | "corrupt", "mb", "problem"}`` in the shape the AI Center's bundles have."""
    model = models()[key]
    found = {file.filename: _file_ok(file) for file in model.files}
    damaged = [name for name, what in found.items() if what == "damaged"]
    missing = [name for name, what in found.items() if what == "missing"]
    if not damaged and not missing:
        return {"state": "ready", "mb": model.mb, "problem": ""}
    if damaged:
        return {"state": "corrupt", "mb": model.mb, "problem": f"{', '.join(damaged)} is not the expected file."}
    if len(missing) == len(model.files):
        return {"state": "missing", "mb": model.mb, "problem": ""}
    return {"state": "partial", "mb": model.mb, "problem": f"{', '.join(missing)} is missing."}


def identify(path: str | Path) -> tuple[str, ModelFile] | None:
    """Which editor model file ``path`` is, by its digest; None when it is none of them."""
    path = Path(path)
    try:
        size = path.stat().st_size
        candidates = [(model.key, file) for model in models().values() for file in model.files if not file.size or file.size == size]
        if not candidates:
            return None
        digest = _digest(path).casefold()
    except OSError:
        return None
    return next(((key, file) for key, file in candidates if file.sha256.casefold() == digest), None)


def expected_names() -> str:
    return ", ".join(f"{file.filename} ({model.name})" for model in models().values() for file in model.files)


def install_file(path: str | Path) -> tuple[str, str]:
    """Copy the model file ``path`` into place. Returns ``(model key, file name)``. Raises ValueError when it is not one of them."""
    source = Path(path)
    found = identify(source)
    if found is None:
        raise ValueError(f"{source.name} is not a model file the editor uses. It takes {expected_names()}.")
    key, file = found
    file.folder.mkdir(parents=True, exist_ok=True)
    if os.path.normcase(os.path.abspath(source)) == os.path.normcase(os.path.abspath(file.path)):
        return key, file.filename                 # it is already where it belongs
    part = file.path.with_name(file.filename + ".part")
    shutil.copyfile(source, part)
    if _digest(part).casefold() != file.sha256.casefold():
        part.unlink(missing_ok=True)
        raise ValueError(f"{source.name} changed while it was being copied. Try again.")
    os.replace(part, file.path)
    return key, file.filename


def remove(key: str) -> None:
    for file in models()[key].files:
        file.path.unlink(missing_ok=True)
        for suffix in (".part", ".partdl", ".partdl.json"):
            file.path.with_name(file.filename + suffix).unlink(missing_ok=True)
