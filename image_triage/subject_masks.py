from __future__ import annotations

"""Subject-mask cache path resolution.

The BiRefNet inference and editor-facing tasks this module used to hold
(``ensure_subject_masks``, ``SubjectMaskTask``, ``SubjectMaskWarmTask``,
``combine_subject_components``, ...) were only ever used by the now-removed
built-in manual editor. This thinned-down module keeps just the
deterministic cache-path lookup, which ``edit_render_headless`` still needs
so a headless render can pick up an already-computed subject mask without
ever triggering BiRefNet inference itself.
"""

import hashlib
import json
import threading
import os
from pathlib import Path

from .ai_model import AIModelInstallation, resolve_birefnet_model_installation
from .ai_paths import managed_cache_dir

SUBJECT_MASK_REFINEMENT_VERSION = "birefnet-soft-mask-components-2"

_WEIGHTS_HASH_CACHE: dict[tuple[str, int, int], str] = {}
_WEIGHTS_HASH_LOCK = threading.Lock()


def default_subject_mask_cache_root() -> Path:
    """Managed cache directory for subject_masks.

    Resolves through the one canonical managed root so it cannot land inside
    Store Python's virtualized package cache (docs/ai_runtime_failure_map.md,
    root cause A). Migration of a previous release's directory happens once,
    explicitly, in ``ai_model_store.migrate_ai_assets``.
    """
    return managed_cache_dir("subject_masks")


def _source_cache_key(source: Path, size: int, mtime_ns: int, weights_hash: str) -> str:
    identity = "\0".join(
        (
            os.path.normcase(str(source)),
            str(size),
            str(mtime_ns),
            weights_hash,
            SUBJECT_MASK_REFINEMENT_VERSION,
        )
    )
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached_sha256_file(path: Path) -> tuple[str, bool]:
    resolved = path.resolve()
    stat = resolved.stat()
    key = (os.path.normcase(str(resolved)), stat.st_size, stat.st_mtime_ns)
    with _WEIGHTS_HASH_LOCK:
        cached = _WEIGHTS_HASH_CACHE.get(key)
        if cached is not None:
            return cached, True
        digest = _sha256_file(resolved)
        _WEIGHTS_HASH_CACHE.clear()
        _WEIGHTS_HASH_CACHE[key] = digest
        return digest, False


def subject_mask_cache_path(
    source_path: str | Path,
    request: str = "subject",
    *,
    installation: AIModelInstallation | None = None,
    cache_root: str | Path | None = None,
) -> Path | None:
    """The cached mask PNG for ``source_path`` if one is already on disk, else
    None. Never runs BiRefNet inference -- only checks the deterministic,
    content-addressed cache path that the (removed) editor's
    ``ensure_subject_masks`` also used, so this is safe to call from a
    headless render path (thumbnail/export) without triggering a synchronous
    model run. Any failure (missing model install, unreadable file, ...) is
    treated as "not cached yet" and returns None rather than raising.
    """

    try:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            return None
        model_installation = installation or resolve_birefnet_model_installation()
        if not model_installation.is_installed:
            return None
        model_path = model_installation.install_dir / "model.safetensors"
        if not model_path.is_file():
            return None
        weights_hash, _ = _cached_sha256_file(model_path)
        stat = source.stat()
        cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns, weights_hash)
        cache_dir = Path(cache_root or default_subject_mask_cache_root()) / cache_key
        mask_path = cache_dir / f"{request}.png"
        metadata_path = cache_dir / "metadata.json"
        cached_metadata = _load_json(metadata_path)
        if (
            cached_metadata.get("sourceSizeBytes") == stat.st_size
            and cached_metadata.get("sourceMtimeNs") == stat.st_mtime_ns
            and cached_metadata.get("weightsHash") == weights_hash
            and cached_metadata.get("refinementVersion") == SUBJECT_MASK_REFINEMENT_VERSION
            and mask_path.is_file()
        ):
            return mask_path
        return None
    except Exception:
        return None
