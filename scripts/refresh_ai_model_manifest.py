"""Regenerate the expected size/SHA-256 table in ``image_triage/ai_manifest.py``.

Every managed model file must carry an expected size and cryptographic hash so
that installation can be verified rather than assumed. This script derives those
values from the Hugging Face metadata API and prints a Python literal that can
be pasted into ``MODEL_FILE_DIGESTS``.

For LFS files the hash is taken from the LFS ``oid`` (already SHA-256). Small
non-LFS files carry a git blob SHA-1 instead, so they are downloaded and hashed
locally.

Usage::

    python scripts/refresh_ai_model_manifest.py                # every bundle
    python scripts/refresh_ai_model_manifest.py depth faces    # selected bundles

Requires network access. It is a maintenance tool, never imported at runtime.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_triage.ai_manifest import MODEL_BUNDLES  # noqa: E402

_LFS_HASH_SIZE_THRESHOLD = 1024 * 1024
_USER_AGENT = "ImageTriage-manifest-refresh/1"


def _get_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _tree(repo_id: str, revision: str) -> dict[str, dict[str, object]]:
    entries: dict[str, dict[str, object]] = {}
    cursor = f"https://huggingface.co/api/models/{repo_id}/tree/{revision}?recursive=true"
    payload = _get_json(cursor)
    if not isinstance(payload, list):
        raise RuntimeError(f"Unexpected tree payload for {repo_id}@{revision}")
    for entry in payload:
        if isinstance(entry, dict) and entry.get("type") == "file":
            entries[str(entry.get("path"))] = entry
    return entries


def _download_sha256(repo_id: str, revision: str, filename: str) -> tuple[str, int]:
    url = f"https://huggingface.co/{repo_id}/resolve/{revision}/{filename}"
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    hasher = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=120) as response:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
            total += len(chunk)
    return hasher.hexdigest(), total


def _describe(bundle_key: str) -> dict[str, tuple[int, str]]:
    bundle = MODEL_BUNDLES[bundle_key]
    entries = _tree(bundle.repo_id, bundle.revision)
    result: dict[str, tuple[int, str]] = {}
    for filename in bundle.filenames:
        entry = entries.get(filename)
        if entry is None:
            print(f"  !! {filename} is absent from {bundle.repo_id}@{bundle.revision}", file=sys.stderr)
            continue
        size = int(entry.get("size") or 0)
        lfs = entry.get("lfs")
        if isinstance(lfs, dict) and isinstance(lfs.get("oid"), str):
            result[filename] = (int(lfs.get("size") or size), str(lfs["oid"]))
            continue
        if size > _LFS_HASH_SIZE_THRESHOLD:
            print(f"  .. hashing large non-LFS file {filename} ({size} bytes)", file=sys.stderr)
        digest, downloaded = _download_sha256(bundle.repo_id, bundle.revision, filename)
        result[filename] = (downloaded or size, digest)
    return result


def main(argv: list[str] | None = None) -> int:
    selected = list(argv or sys.argv[1:]) or sorted(MODEL_BUNDLES)
    unknown = [key for key in selected if key not in MODEL_BUNDLES]
    if unknown:
        print(f"Unknown bundle(s): {', '.join(unknown)}", file=sys.stderr)
        print(f"Known: {', '.join(sorted(MODEL_BUNDLES))}", file=sys.stderr)
        return 2

    print("MODEL_FILE_DIGESTS: dict[str, dict[str, FileDigest]] = {")
    for key in selected:
        bundle = MODEL_BUNDLES[key]
        print(f"    # {bundle.repo_id}@{bundle.revision}", flush=True)
        print(f"    {key!r}: {{")
        for filename, (size, digest) in _describe(key).items():
            print(f"        {filename!r}: FileDigest({size}, {digest!r}),")
        print("    },")
    print("}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
