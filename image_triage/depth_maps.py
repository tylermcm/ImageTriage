from __future__ import annotations

"""Depth-map cache path resolution.

The depth-model inference and editor-facing tasks this module used to hold
(``ensure_depth_map``, ``DepthMapTask``, ``DepthWarmTask``) were only ever
used by the now-removed built-in manual editor. This thinned-down module
keeps just the deterministic cache-path lookup, which ``edit_render_headless``
still needs so a headless render can pick up an already-computed depth map
without ever triggering depth-model inference itself.
"""

import hashlib
import os
from pathlib import Path

from .ai_model import DEFAULT_DEPTH_MODEL_REVISION
from .ai_paths import managed_cache_dir

DEPTH_MODEL_VERSION = DEFAULT_DEPTH_MODEL_REVISION


def default_depth_cache_root() -> Path:
    """Managed cache directory for depth_maps.

    Resolves through the one canonical managed root so it cannot land inside
    Store Python's virtualized package cache (docs/ai_runtime_failure_map.md,
    root cause A). Migration of a previous release's directory happens once,
    explicitly, in ``ai_model_store.migrate_ai_assets``.
    """
    return managed_cache_dir("depth_maps")


def _source_cache_key(source: Path, size: int, mtime_ns: int) -> str:
    identity = "\0".join(
        (os.path.normcase(str(source)), str(size), str(mtime_ns), DEPTH_MODEL_VERSION)
    )
    return hashlib.sha256(identity.encode("utf-8", errors="surrogatepass")).hexdigest()[:24]


def depth_map_cache_path(
    source_path: str | Path,
    *,
    cache_root: str | Path | None = None,
) -> Path | None:
    """The cached depth-map PNG for ``source_path`` if already on disk, else
    None. Never runs depth-model inference -- only checks the deterministic,
    content-addressed cache path that the (removed) editor's ``ensure_depth_map``
    also used, so this is safe to call from a headless render path
    (thumbnail/export). Any failure is treated as "not cached yet" and returns
    None.
    """

    try:
        source = Path(source_path).expanduser().resolve()
        if not source.is_file():
            return None
        stat = source.stat()
        cache_key = _source_cache_key(source, stat.st_size, stat.st_mtime_ns)
        cache_dir = Path(cache_root or default_depth_cache_root()) / cache_key
        depth_path = cache_dir / "depth.png"
        return depth_path if depth_path.is_file() else None
    except Exception:
        return None
