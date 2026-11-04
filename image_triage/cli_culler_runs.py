"""Validate a native export generation and publish a single atomic pointer."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .cli_culler_results import NativeCullResults


@dataclass(frozen=True)
class CullRunPaths:
    root: Path
    cache: Path
    runs: Path
    active: Path


def cull_run_paths(folder: Path, namespace: str) -> CullRunPaths:
    if not namespace or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in namespace):
        raise ValueError("A validated runtime cache namespace is required")
    root = folder.resolve() / ".image_triage_ai" / "artifacts" / "cli_culler"
    return CullRunPaths(root, root / "cache" / namespace, root / "runs", root / "active.json")


def validate_generation(event: dict, run_root: Path) -> dict:
    """Require both exports to belong to this run, preserving their pairing."""
    decisions = Path(event["decisions"]).resolve(strict=True)
    if not decisions.is_relative_to(run_root.resolve()) or decisions.name != "decisions.json":
        raise ValueError("Decisions are outside this run's staging generation")
    document = NativeCullResults.load(decisions).to_document()
    index = Path(event["index"]).resolve(strict=True) if event.get("index") else None
    if index is not None:
        if index.parent != decisions.parent or index.name != "index.npz":
            raise ValueError("Index and decisions must belong to the same generation")
        import numpy as np

        with np.load(index, allow_pickle=False) as arrays:
            meta = json.loads(str(arrays["meta"]))
            if meta.get("schema") != "aiculler-index/1":
                raise ValueError("Unsupported native index schema")
            captures, files, positions = arrays["captures"], arrays["files"], arrays["file_capture"]
            if captures.ndim != 1 or files.ndim != 1 or positions.shape != files.shape:
                raise ValueError("Invalid index capture/file mapping")
            if positions.dtype.kind not in "iu":
                raise ValueError("Index capture positions must be integers")
            for path, position in zip(files.tolist(), positions.tolist()):
                if position < 0 or position >= len(captures):
                    raise ValueError("Index capture position is out of range")
                if document["files"].get(path, {}).get("capture") != str(captures[position]):
                    raise ValueError("Index file/capture mapping differs from decisions")
            # Read every array while the archive is open: corrupt optional
            # arrays must not slip through merely because the host ignores them.
            for name in arrays.files:
                arrays[name]
    elif (decisions.parent / "index.npz").exists():
        raise ValueError("Worker did not declare its generated index")
    outputs = {"decisions": decisions, "index": index}
    return {
        "schema": "image-triage-culler-run/1",
        "outputs": {
            key: {"path": str(path), "sha256": _digest(path)} if path else None
            for key, path in outputs.items()
        },
    }


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def activate_generation(active: Path, descriptor: dict) -> None:
    active.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=active.parent,
                                         prefix="active-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(descriptor, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, active)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_active_generation(active: Path) -> NativeCullResults:
    descriptor = json.loads(active.read_text(encoding="utf-8"))
    if descriptor.get("schema") != "image-triage-culler-run/1":
        raise ValueError("Unsupported active culler generation schema")
    for output in descriptor["outputs"].values():
        if output is not None and _digest(Path(output["path"])) != output["sha256"]:
            raise ValueError("Active culler output failed integrity verification")
    return NativeCullResults.load(descriptor["outputs"]["decisions"]["path"])
