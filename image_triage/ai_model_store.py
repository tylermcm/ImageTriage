"""Transactional installation and verification of managed model bundles.

The previous implementation downloaded files one at a time straight into the
live model directory and treated "the path exists" as "the model is installed".
An interrupted download therefore left a directory that reported success
forever, and TOPIQ/AuraFace had no integrity check at all.

This module replaces that with a generation model:

* Every bundle downloads into ``<managed root>/staging/<key>-<id>``.
* Every file is verified against the size and SHA-256 in ``ai_manifest``.
* Only a completely verified generation is activated, by moving the previous
  live directory aside and moving the new one in, under a cross-process lock.
* Activation writes ``.bundle.json`` recording the manifest version, repo
  revision and per-file size/hash, so later readiness checks are cheap and a
  mixed-revision directory is impossible to mistake for a good one.

An interrupted download is resumed rather than repeated. The unfinished bytes of
each file are kept in ``<managed root>/staging/.partial/<key>/<file>.part`` (outside
the per-attempt generation, so they survive a cancel, a failure or a restart of
the app), beside a small sidecar recording the URL, the expected size and hash
and the validator the server gave (ETag or Last-Modified). The next attempt asks
for the rest with ``Range`` and ``If-Range``; a file whose validator no longer
matches is started again, and nothing is moved into a generation until its size
and SHA-256 match the manifest.

See ``docs/ai_runtime_failure_map.md`` (root cause B).
"""

from __future__ import annotations

import hashlib
import http.client
import inspect
import json
import os
import shutil
import ssl
import threading
import time
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterator
from urllib.parse import urlparse

from .ai_manifest import (
    AI_MANIFEST_VERSION,
    MODEL_BUNDLES,
    bundle_digests,
)
from .ai_paths import (
    free_bytes,
    managed_model_dir,
    managed_staging_root,
    migrate_managed_assets,
    preflight_storage,
    redact,
)


BUNDLE_METADATA_FILENAME = ".bundle.json"
BUNDLE_METADATA_VERSION = 1
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
DOWNLOAD_USER_AGENT = "ImageTriage/0.1"
DOWNLOAD_TIMEOUT_SECONDS = 120
DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_BACKOFF_SECONDS = 1.5
PARTIAL_DIRNAME = ".partial"  # under the staging root; never a generation (those are "<key>-<id>")
PARTIAL_SUFFIX = ".part"
PARTIAL_SIDECAR_VERSION = 1
# Leave room for the staged copy plus the outgoing generation during activation.
FREE_SPACE_HEADROOM = 1.6

STATE_READY = "ready"
STATE_MISSING = "missing"
STATE_PARTIAL = "partial"
STATE_CORRUPT = "corrupt"
STATE_STALE = "stale"

ProgressCallback = Callable[[str, int, int], None]
CancelCheck = Callable[[], bool]


class ModelInstallCancelled(RuntimeError):
    """Raised when a caller cancels an in-progress bundle install."""


class ModelInstallError(RuntimeError):
    """A bundle could not be installed. ``category`` drives the user message."""

    def __init__(self, message: str, *, category: str = "unknown", detail: str = "") -> None:
        super().__init__(message)
        self.category = category
        self.detail = detail


@dataclass(frozen=True)
class BundleStatus:
    """What the on-disk state of one model bundle actually is."""

    key: str
    name: str
    install_dir: Path
    state: str
    revision: str = ""
    expected_revision: str = ""
    problems: tuple[str, ...] = ()
    verified_deeply: bool = False
    missing_files: tuple[str, ...] = ()

    @property
    def is_ready(self) -> bool:
        return self.state == STATE_READY

    def describe(self) -> str:
        if self.is_ready:
            return f"{self.name} is installed."
        if self.state == STATE_MISSING:
            return f"{self.name} is not downloaded yet."
        if self.state == STATE_STALE:
            return (
                f"{self.name} was downloaded from a different model revision "
                f"({self.revision or 'unknown'}) and needs to be re-downloaded."
            )
        if self.state == STATE_PARTIAL:
            missing = ", ".join(self.missing_files[:3])
            return f"{self.name} is incomplete (missing {missing})."
        return f"{self.name} failed verification: {'; '.join(self.problems[:2])}"


@dataclass(frozen=True)
class _ActivationRecord:
    bundle_key: str
    install_dir: Path
    staged: Path
    retired: Path
    journal: Path


# --------------------------------------------------------------------------
# Location
# --------------------------------------------------------------------------


# Derived-data caches that moved under the managed root alongside the models.
MIGRATED_CACHE_NAMES = ("depth_maps", "prompt_masks", "semantic_masks", "subject_masks")

_MIGRATION_DONE = False
_MIGRATION_LOCK = threading.Lock()


def migrate_ai_assets(*, force: bool = False) -> tuple[str, ...]:
    """Adopt a previous release's model and cache directories, once per process.

    Called explicitly at application startup and before an install. Status
    checks never trigger it, so inspecting readiness cannot move a user's files.
    """
    global _MIGRATION_DONE
    with _MIGRATION_LOCK:
        if _MIGRATION_DONE and not force:
            return ()
        moved = migrate_managed_assets(
            model_parts=[bundle.install_parts for bundle in MODEL_BUNDLES.values()],
            cache_names=MIGRATED_CACHE_NAMES,
        )
        _MIGRATION_DONE = True
    return moved


def bundle_install_dir(bundle_key: str) -> Path:
    """Live directory for a bundle, honouring its documented directory override."""
    bundle = MODEL_BUNDLES[bundle_key]
    override = (os.environ.get(bundle.dir_env, "") or "").strip() if bundle.dir_env else ""
    if override:
        return Path(override).expanduser().resolve()
    return managed_model_dir(*bundle.install_parts)


def bundle_revision(bundle_key: str) -> str:
    bundle = MODEL_BUNDLES[bundle_key]
    override = (os.environ.get(bundle.revision_env, "") or "").strip() if bundle.revision_env else ""
    return override or bundle.revision


def bundle_repo_id(bundle_key: str) -> str:
    bundle = MODEL_BUNDLES[bundle_key]
    override = (os.environ.get(bundle.repo_env, "") or "").strip() if bundle.repo_env else ""
    return override or bundle.repo_id


def _uses_default_source(bundle_key: str) -> bool:
    bundle = MODEL_BUNDLES[bundle_key]
    return bundle_repo_id(bundle_key) == bundle.repo_id and bundle_revision(bundle_key) == bundle.revision


# --------------------------------------------------------------------------
# Verification
# --------------------------------------------------------------------------


def read_bundle_metadata(install_dir: Path) -> dict[str, object]:
    path = install_dir / BUNDLE_METADATA_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def bundle_status(bundle_key: str, *, deep: bool = False) -> BundleStatus:
    """Inspect one bundle. ``deep=True`` re-hashes every file.

    The shallow check compares the recorded manifest version, repo revision and
    each file's size against ``.bundle.json``. That is enough to catch a
    truncated, absent or mixed-revision bundle without hashing a gigabyte on
    every launch; Repair and post-install verification use the deep path.
    """
    bundle = MODEL_BUNDLES[bundle_key]
    install_dir = bundle_install_dir(bundle_key)
    expected_revision = bundle_revision(bundle_key)
    digests = bundle_digests(bundle_key) if _uses_default_source(bundle_key) else {}

    if not install_dir.is_dir():
        return BundleStatus(
            key=bundle_key,
            name=bundle.name,
            install_dir=install_dir,
            state=STATE_MISSING,
            expected_revision=expected_revision,
            missing_files=bundle.filenames,
        )

    missing = tuple(
        filename for filename in bundle.filenames if not (install_dir / filename).is_file()
    )
    if missing:
        return BundleStatus(
            key=bundle_key,
            name=bundle.name,
            install_dir=install_dir,
            state=STATE_PARTIAL,
            expected_revision=expected_revision,
            missing_files=missing,
            problems=(f"{len(missing)} of {len(bundle.filenames)} files are absent",),
        )

    metadata = read_bundle_metadata(install_dir)
    recorded_revision = str(metadata.get("revision") or "")
    if metadata:
        if int(metadata.get("manifest_version") or 0) != AI_MANIFEST_VERSION:
            return BundleStatus(
                key=bundle_key,
                name=bundle.name,
                install_dir=install_dir,
                state=STATE_STALE,
                revision=recorded_revision,
                expected_revision=expected_revision,
                problems=("installed by an older build of Image Triage",),
            )
        if recorded_revision and recorded_revision != expected_revision:
            return BundleStatus(
                key=bundle_key,
                name=bundle.name,
                install_dir=install_dir,
                state=STATE_STALE,
                revision=recorded_revision,
                expected_revision=expected_revision,
                problems=(f"installed revision {recorded_revision} != required {expected_revision}",),
            )

    problems: list[str] = []
    for filename in bundle.filenames:
        path = install_dir / filename
        digest = digests.get(filename)
        try:
            size = path.stat().st_size
        except OSError as exc:
            problems.append(f"{filename}: {exc.strerror or exc}")
            continue
        if size == 0:
            problems.append(f"{filename} is empty")
            continue
        if digest is not None and digest.size and size != digest.size:
            problems.append(f"{filename} is {size} bytes, expected {digest.size}")
            continue
        if deep and digest is not None and digest.sha256:
            actual = sha256_file(path)
            if actual.casefold() != digest.sha256.casefold():
                problems.append(f"{filename} failed SHA-256 verification")

    if problems:
        return BundleStatus(
            key=bundle_key,
            name=bundle.name,
            install_dir=install_dir,
            state=STATE_CORRUPT,
            revision=recorded_revision,
            expected_revision=expected_revision,
            problems=tuple(problems),
            verified_deeply=deep,
        )

    if not metadata:
        # A directory from a build that predates bundle metadata. Every file is
        # present and every size matches the published size, which is what the
        # loop above just established, so adopt it rather than forcing a
        # multi-gigabyte re-download on upgrade. Repair still deep-verifies.
        try:
            _write_bundle_metadata(install_dir, bundle_key, adopted=True)
        except OSError:
            pass

    return BundleStatus(
        key=bundle_key,
        name=bundle.name,
        install_dir=install_dir,
        state=STATE_READY,
        revision=recorded_revision or expected_revision,
        expected_revision=expected_revision,
        verified_deeply=deep,
    )


def all_bundle_statuses(*, deep: bool = False) -> dict[str, BundleStatus]:
    return {key: bundle_status(key, deep=deep) for key in MODEL_BUNDLES}


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(DOWNLOAD_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _write_bundle_metadata(install_dir: Path, bundle_key: str, *, adopted: bool = False) -> None:
    bundle = MODEL_BUNDLES[bundle_key]
    digests = bundle_digests(bundle_key)
    files: dict[str, dict[str, object]] = {}
    for filename in bundle.filenames:
        path = install_dir / filename
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        digest = digests.get(filename)
        files[filename] = {
            "size": size,
            "sha256": digest.sha256 if digest else "",
        }
    payload = {
        "metadata_version": BUNDLE_METADATA_VERSION,
        "manifest_version": AI_MANIFEST_VERSION,
        "bundle": bundle_key,
        "repo_id": bundle_repo_id(bundle_key),
        "revision": bundle_revision(bundle_key),
        "adopted": bool(adopted),
        "installed_at": int(time.time()),
        "files": files,
    }
    _write_json_atomic(install_dir / BUNDLE_METADATA_FILENAME, payload)


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


# --------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------


@contextmanager
def bundle_lock(bundle_key: str, *, timeout_seconds: float = 0.0) -> Iterator[None]:
    """Serialize installs of one bundle across every Image Triage process."""
    lock_root = managed_staging_root()
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / f".{bundle_key}.lock"
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        try:
            with lock_path.open("a+b") as handle:
                _acquire_lock(handle)
                try:
                    yield
                finally:
                    _release_lock(handle)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise ModelInstallError(
                    f"Another Image Triage process is already downloading "
                    f"{MODEL_BUNDLES[bundle_key].name}.",
                    category="locked",
                ) from None
            time.sleep(0.25)


def _acquire_lock(handle) -> None:
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        raise BlockingIOError(str(exc)) from exc


def _release_lock(handle) -> None:
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Installation
# --------------------------------------------------------------------------


def install_bundle(
    bundle_key: str,
    *,
    force: bool = False,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
    downloader: Callable[..., int] | None = None,
) -> BundleStatus:
    """Download, verify and atomically activate one model bundle.

    Returns the post-install status. Raises ``ModelInstallError`` with a
    ``category`` the UI can turn into a specific remediation, or
    ``ModelInstallCancelled`` when the caller cancels.
    """
    if bundle_key not in MODEL_BUNDLES:
        raise ModelInstallError(f"Unknown model bundle {bundle_key!r}.", category="manifest")
    bundle = MODEL_BUNDLES[bundle_key]

    migrate_ai_assets()
    with bundle_lock(bundle_key):
        _recover_bundle_activation(managed_staging_root(), bundle_key)
        if not force:
            existing = bundle_status(bundle_key, deep=False)
            if existing.is_ready:
                return existing

        install_dir = bundle_install_dir(bundle_key)
        staging_root = managed_staging_root()
        required = int(bundle.approx_mb * 1024 * 1024 * FREE_SPACE_HEADROOM)
        preflight = preflight_storage(staging_root, required_bytes=required)
        if not preflight.ok:
            raise ModelInstallError(
                preflight.describe(),
                category="disk" if not preflight.has_free_space else "filesystem",
                detail=redact(str(preflight.root)),
            )

        _clean_abandoned_staging(staging_root, bundle_key)
        staged = staging_root / f"{bundle_key}-{uuid.uuid4().hex[:12]}"
        staged.mkdir(parents=True, exist_ok=False)
        try:
            _download_bundle_files(
                bundle_key,
                staged,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
                downloader=downloader,
            )
            _verify_staged_bundle(bundle_key, staged)
            _write_bundle_metadata(staged, bundle_key)
            _activate(staged, install_dir, staging_root, bundle_key)
        except BaseException:
            # Once activation has written its journal, staging may be one of
            # the only recoverable generations. Recovery owns its cleanup.
            # The unfinished downloads under ``.partial`` are deliberately left:
            # the next attempt continues from them.
            if not activation_journal_path(staging_root, bundle_key).exists():
                _remove_tree(staged)
            raise
        _remove_tree(bundle_partial_dir(bundle_key))

    status = bundle_status(bundle_key, deep=False)
    if not status.is_ready:  # pragma: no cover - defensive
        raise ModelInstallError(
            f"{bundle.name} did not verify after installation: {status.describe()}",
            category="verification",
        )
    return status


def repair_bundle(
    bundle_key: str,
    *,
    progress_callback: ProgressCallback | None = None,
    cancel_check: CancelCheck | None = None,
) -> BundleStatus:
    """Deep-verify a bundle and reinstall it if anything is wrong."""
    status = bundle_status(bundle_key, deep=True)
    if status.is_ready:
        return status
    return install_bundle(
        bundle_key,
        force=True,
        progress_callback=progress_callback,
        cancel_check=cancel_check,
    )


def uninstall_bundle(bundle_key: str) -> bool:
    install_dir = bundle_install_dir(bundle_key)
    if not install_dir.exists():
        _remove_tree(bundle_partial_dir(bundle_key))
        return False
    with bundle_lock(bundle_key):
        _remove_tree(install_dir)
        _remove_tree(bundle_partial_dir(bundle_key))
    return not install_dir.exists()


def bundle_partial_dir(bundle_key: str) -> Path:
    """Where the unfinished downloads of one bundle are kept between attempts."""
    return managed_staging_root() / PARTIAL_DIRNAME / bundle_key


def bundle_partial_bytes(bundle_key: str) -> int:
    """How much of an interrupted download of this bundle is on disk, to be continued by the next install. 0 when none."""
    total = 0
    try:
        for path in bundle_partial_dir(bundle_key).rglob("*" + PARTIAL_SUFFIX):
            if path.is_file() and path.with_name(path.name + ".json").is_file():
                total += path.stat().st_size
    except OSError:
        return 0
    return total


def _download_bundle_files(
    bundle_key: str,
    staged: Path,
    *,
    progress_callback: ProgressCallback | None,
    cancel_check: CancelCheck | None,
    downloader: Callable[..., int] | None,
) -> None:
    bundle = MODEL_BUNDLES[bundle_key]
    repo_id = bundle_repo_id(bundle_key)
    revision = bundle_revision(bundle_key)
    fetch = downloader or _download_file
    # A substitute downloader (the tests', or a caller's own) has the plain five arguments and is called as before.
    resumable = downloader is None and "resume" in inspect.signature(fetch).parameters
    digests = bundle_digests(bundle_key) if _uses_default_source(bundle_key) else {}
    partial_dir = bundle_partial_dir(bundle_key)
    for filename in bundle.filenames:
        _raise_if_cancelled(cancel_check)
        destination = staged / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://huggingface.co/{repo_id}/resolve/{revision}/{filename}?download=true"
        extra: dict[str, object] = {}
        if resumable:
            digest = digests.get(filename)
            extra["resume"] = _ResumePlan(
                partial=partial_dir / (filename + PARTIAL_SUFFIX),
                size=int(digest.size) if digest is not None else 0,
                sha256=str(digest.sha256) if digest is not None else "",
            )
        fetch(
            url=url,
            destination=destination,
            label=filename,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            **extra,
        )


def _verify_staged_bundle(bundle_key: str, staged: Path) -> None:
    bundle = MODEL_BUNDLES[bundle_key]
    digests = bundle_digests(bundle_key) if _uses_default_source(bundle_key) else {}
    for filename in bundle.filenames:
        path = staged / filename
        if not path.is_file():
            raise ModelInstallError(
                f"{bundle.name}: {filename} did not download.",
                category="incomplete",
            )
        size = path.stat().st_size
        if size == 0:
            raise ModelInstallError(
                f"{bundle.name}: {filename} downloaded as an empty file.",
                category="incomplete",
            )
        digest = digests.get(filename)
        if digest is None:
            continue
        if digest.size and size != digest.size:
            raise ModelInstallError(
                f"{bundle.name}: {filename} is {size} bytes but should be {digest.size}. "
                "The download was truncated or intercepted.",
                category="truncated",
            )
        if digest.sha256:
            actual = sha256_file(path)
            if actual.casefold() != digest.sha256.casefold():
                raise ModelInstallError(
                    f"{bundle.name}: {filename} failed integrity verification. "
                    "The file that arrived is not the published model file.",
                    category="hash_mismatch",
                    detail=f"expected {digest.sha256}, got {actual}",
                )


def activation_journal_path(staging_root: Path, bundle_key: str) -> Path:
    return staging_root / f".{bundle_key}.activation.json"


def _activate(staged: Path, install_dir: Path, staging_root: Path, bundle_key: str) -> None:
    """Swap a fully verified staged generation into the live location.

    The swap is two renames with a window between them where the live directory
    does not exist. If the second rename fails the previous generation is put
    back, and the journal is only removed once the outcome is settled, so an
    interrupted process can be recovered on the next run
    (``recover_interrupted_activations``).
    """
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    retired = staging_root / f"{bundle_key}-retired-{uuid.uuid4().hex[:12]}"
    journal = activation_journal_path(staging_root, bundle_key)
    _write_json_atomic(
        journal,
        {
            "bundle": bundle_key,
            "staged": str(staged),
            "install_dir": str(install_dir),
            "retired": str(retired),
            "started_at": int(time.time()),
        },
    )

    had_previous = install_dir.exists()
    if had_previous:
        _replace_with_retry(install_dir, retired)
    try:
        _replace_with_retry(staged, install_dir)
    except BaseException:
        # The live directory is currently absent. Put the previous generation
        # back before surfacing the error; without this the only healthy copy
        # is left in staging and the next cleanup pass deletes it.
        if had_previous and not install_dir.exists():
            try:
                _replace_with_retry(retired, install_dir, attempts=12)
            except Exception:
                # Leave the journal in place: recovery on the next run can
                # still find the retired generation and restore it.
                raise
        _discard_journal(journal)
        raise

    _discard_journal(journal)
    _remove_tree(retired)


def _discard_journal(journal: Path) -> None:
    try:
        journal.unlink(missing_ok=True)
    except OSError:
        pass


def recover_interrupted_activations() -> tuple[str, ...]:
    """Finish or undo any activation a previous process left half-done.

    Runs before installation and at application startup. Returns the bundle
    keys that needed recovery.
    """
    staging_root = managed_staging_root()
    try:
        journals = sorted(staging_root.glob(".*.activation.json"))
    except OSError:
        return ()
    recovered: list[str] = []
    for journal in journals:
        record = _read_activation_record(journal, staging_root)
        if record is None:
            # An invalid journal is never trusted as authority to move or
            # delete anything. Leave it for diagnostics/manual recovery.
            continue
        try:
            with bundle_lock(record.bundle_key):
                if _recover_bundle_activation(staging_root, record.bundle_key):
                    recovered.append(record.bundle_key)
        except ModelInstallError as exc:
            if exc.category != "locked":
                raise
            # Another process still owns this activation. It will either
            # finish the swap or leave the journal for the next startup.
            continue
    return tuple(recovered)


def _recover_bundle_activation(staging_root: Path, bundle_key: str) -> bool:
    """Recover one bundle while its cross-process lock is held."""
    journal = activation_journal_path(staging_root, bundle_key)
    record = _read_activation_record(journal, staging_root)
    if record is None:
        return False

    if record.install_dir.is_dir():
        # Only discard backups after proving the live generation is usable.
        if not bundle_status(bundle_key, deep=False).is_ready:
            return False
        if not _remove_activation_sources(record):
            return False
        _discard_journal(journal)
        return True

    # Prefer the newly verified generation, then the previous healthy copy.
    # If every rename fails, preserve both sources and the journal exactly as
    # they are; a later startup can retry after locks/antivirus release them.
    for source in (record.staged, record.retired):
        if not source.is_dir():
            continue
        try:
            record.install_dir.parent.mkdir(parents=True, exist_ok=True)
            _replace_with_retry(source, record.install_dir, attempts=12)
        except Exception:
            continue
        if not bundle_status(bundle_key, deep=False).is_ready:
            return False
        if not _remove_activation_sources(record):
            return False
        _discard_journal(journal)
        return True
    return False


def _remove_activation_sources(record: _ActivationRecord) -> bool:
    removed = True
    for path in (record.retired, record.staged):
        if path == record.install_dir or not path.exists():
            continue
        removed = _remove_tree(path) and removed
    return removed


def _read_activation_record(journal: Path, staging_root: Path) -> _ActivationRecord | None:
    """Parse a journal only when every path matches the expected layout."""
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    bundle_key = payload.get("bundle")
    install_text = payload.get("install_dir")
    staged_text = payload.get("staged")
    retired_text = payload.get("retired")
    if (
        not isinstance(bundle_key, str)
        or bundle_key not in MODEL_BUNDLES
        or not isinstance(install_text, str)
        or not install_text.strip()
        or not isinstance(staged_text, str)
        or not staged_text.strip()
        or not isinstance(retired_text, str)
        or not retired_text.strip()
    ):
        return None

    root = staging_root.resolve(strict=False)
    expected_journal = activation_journal_path(root, bundle_key).resolve(strict=False)
    install_dir = Path(install_text).expanduser().resolve(strict=False)
    staged = Path(staged_text).expanduser().resolve(strict=False)
    retired = Path(retired_text).expanduser().resolve(strict=False)
    expected_install = bundle_install_dir(bundle_key).resolve(strict=False)
    if journal.resolve(strict=False) != expected_journal or install_dir != expected_install:
        return None
    if not _is_bundle_staging_child(staged, root, bundle_key):
        return None
    if not _is_bundle_staging_child(retired, root, bundle_key, retired=True):
        return None
    if staged == retired:
        return None
    return _ActivationRecord(bundle_key, install_dir, staged, retired, expected_journal)


def _is_bundle_staging_child(
    path: Path,
    staging_root: Path,
    bundle_key: str,
    *,
    retired: bool = False,
) -> bool:
    prefix = f"{bundle_key}-retired-" if retired else f"{bundle_key}-"
    if retired and not path.name.startswith(prefix):
        return False
    if not retired and (
        not path.name.startswith(prefix) or path.name.startswith(f"{bundle_key}-retired-")
    ):
        return False
    return path.parent == staging_root


def _replace_with_retry(source: Path, target: Path, *, attempts: int = 8) -> None:
    """``os.replace`` with retries for Windows file locking.

    Antivirus scans, the search indexer and a worker that has not finished
    exiting can all hold a handle for a moment after the previous operation.
    """
    last: OSError | None = None
    for attempt in range(max(1, attempts)):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last = exc
            time.sleep(0.2 * (attempt + 1))
    raise ModelInstallError(
        f"Image Triage could not finish installing into {redact(str(target))}: "
        f"{last.strerror if last else 'the directory is in use'}. "
        "Close any running AI operation and try Repair AI again.",
        category="locked",
        detail=redact(str(last)) if last else "",
    )


def _clean_abandoned_staging(staging_root: Path, bundle_key: str) -> None:
    """Remove staging generations left behind by an interrupted install.

    Anything an unfinished activation journal still references is preserved:
    that directory may be the only healthy copy of the bundle.
    """
    # A journal means activation or recovery owns every generation for this
    # bundle. Do not infer that an apparently abandoned directory is disposable.
    if activation_journal_path(staging_root, bundle_key).exists():
        return
    protected = _journal_referenced_paths(staging_root)
    try:
        children = tuple(staging_root.iterdir())
    except OSError:
        return
    prefix = f"{bundle_key}-"
    for child in children:
        if not child.is_dir() or not child.name.startswith(prefix):
            continue
        if str(child) in protected:
            continue
        _remove_tree(child)


def _journal_referenced_paths(staging_root: Path) -> set[str]:
    referenced: set[str] = set()
    try:
        journals = tuple(staging_root.glob(".*.activation.json"))
    except OSError:
        return referenced
    for journal in journals:
        record = _read_activation_record(journal, staging_root)
        if record is None:
            continue
        referenced.add(str(record.staged))
        referenced.add(str(record.retired))
    return referenced


def _remove_tree(path: Path, *, attempts: int = 6) -> bool:
    if not path.exists():
        return True
    for attempt in range(max(1, attempts)):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            time.sleep(0.15 * (attempt + 1))
    return not path.exists()


def _raise_if_cancelled(cancel_check: CancelCheck | None) -> None:
    if cancel_check is not None and cancel_check():
        raise ModelInstallCancelled("Model download stopped.")


# --------------------------------------------------------------------------
# Download transport
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _ResumePlan:
    """Where one file's unfinished bytes are kept, and what the finished file must be (0 / "" when the manifest does not say)."""

    partial: Path
    size: int = 0
    sha256: str = ""

    @property
    def sidecar(self) -> Path:
        return self.partial.with_name(self.partial.name + ".json")


@dataclass
class _FetchReport:
    """What one request did, for the caller that decides whether a failed verification is worth another try."""

    appended: bool = False  # the bytes were added to an earlier partial (a 206, or a 416 that said it was already whole)
    notes: list[str] = field(default_factory=list)


class _ResumeMismatch(ModelInstallError):
    """The kept partial cannot be continued (the server's file changed, or its answer did not line up): start again from nothing."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message, category="resume_mismatch", detail=detail)


def _download_file(
    *,
    url: str,
    destination: Path,
    label: str,
    progress_callback: ProgressCallback | None,
    cancel_check: CancelCheck | None,
    resume: _ResumePlan | None = None,
) -> int:
    """Fetch one file with bounded retries, backoff and range resume.

    The unfinished bytes stay in ``resume.partial`` (by default beside the destination) when an attempt is cancelled or fails,
    with a sidecar that says what they are a part of; the next call continues from them. They are thrown away when they cannot
    be continued safely (another URL or expected hash, a changed validator, a range that does not line up) or when the finished
    file does not verify. The file only reaches ``destination`` after its size and SHA-256 match the plan's.
    """
    if urlparse(url).scheme != "https":
        raise ModelInstallError("Model downloads must use https.", category="manifest")

    plan = resume or _ResumePlan(destination.with_name(destination.name + PARTIAL_SUFFIX))
    partial = plan.partial
    last_error: ModelInstallError | None = None
    for attempt in range(DOWNLOAD_ATTEMPTS):
        _raise_if_cancelled(cancel_check)
        resume_from, validator = _resumable_state(plan, url)
        report = _FetchReport(appended=resume_from > 0)
        wait = True
        try:
            if not (plan.size and resume_from == plan.size):  # otherwise every byte is here already: only the check is left
                _fetch(
                    url=url,
                    partial=partial,
                    label=label,
                    resume_from=resume_from,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                    validator=validator,
                    plan=plan,
                    report=report,
                )
            problem = _verification_problem(plan, label)
            if problem is None:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(partial, destination)
                _unlink(plan.sidecar)
                return destination.stat().st_size
            _discard_partial(plan)
            last_error, wait = problem, False
            if not report.appended:
                break  # every byte came in this one request and it is still not the published file: not retried
            # Otherwise a kept partial was at fault (or stale): once more, from nothing.
        except ModelInstallCancelled:
            raise
        except _ResumeMismatch as exc:
            last_error, wait = exc, False
            _discard_partial(plan)
        except ModelInstallError as exc:
            last_error = exc
            if exc.category in {"not_found", "auth", "certificate", "manifest"}:
                if exc.category == "not_found":
                    _discard_partial(plan)
                break
        if wait and attempt + 1 < DOWNLOAD_ATTEMPTS:
            time.sleep(DOWNLOAD_BACKOFF_SECONDS * (2**attempt))
    raise last_error or ModelInstallError(f"Could not download {label}.", category="network")


def _resumable_state(plan: _ResumePlan, url: str) -> tuple[int, str]:
    """(bytes already here, the validator to send with ``If-Range``), or (0, "") after discarding a partial that cannot be continued."""
    try:
        have = plan.partial.stat().st_size if plan.partial.is_file() else 0
    except OSError:
        have = 0
    if have <= 0:
        return 0, ""
    meta = _read_sidecar(plan.sidecar)
    usable = (
        meta is not None
        and meta.get("version") == PARTIAL_SIDECAR_VERSION
        and meta.get("url") == url
        and int(meta.get("size") or 0) == plan.size
        and str(meta.get("sha256") or "").casefold() == plan.sha256.casefold()
        and not (plan.size and have > plan.size)
    )
    validator = _validator(str(meta.get("etag") or ""), str(meta.get("last_modified") or "")) if usable and meta is not None else ""
    complete = bool(plan.size) and have == plan.size
    if not usable or not (validator or complete):
        # Without the server's validator there is no way to ask "only if it is still the same file".
        _discard_partial(plan)
        return 0, ""
    return have, validator


def _validator(etag: str, last_modified: str) -> str:
    """What ``If-Range`` may carry: a strong ETag, else the Last-Modified date. A weak ETag cannot be used for a range."""
    etag = etag.strip()
    if etag and not etag.startswith("W/"):
        return etag
    return last_modified.strip()


def _read_sidecar(path: Path) -> dict[str, object] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _discard_partial(plan: _ResumePlan) -> None:
    _unlink(plan.partial)
    _unlink(plan.sidecar)


def _verification_problem(plan: _ResumePlan, label: str) -> ModelInstallError | None:
    """The finished partial against the manifest's size and SHA-256 (the same check ``_verify_staged_bundle`` repeats on the
    whole generation). None when it is the published file, or when the manifest gives nothing to check it against."""
    try:
        size = plan.partial.stat().st_size
    except OSError as exc:
        return ModelInstallError(f"{label} did not download.", category="incomplete", detail=str(exc))
    if plan.size and size != plan.size:
        return ModelInstallError(
            f"{label} is {size} bytes but should be {plan.size}. The download was truncated or intercepted.",
            category="truncated",
        )
    if plan.sha256:
        actual = sha256_file(plan.partial)
        if actual.casefold() != plan.sha256.casefold():
            return ModelInstallError(
                f"{label} failed integrity verification. The file that arrived is not the published model file.",
                category="hash_mismatch",
                detail=f"expected {plan.sha256}, got {actual}",
            )
    return None


def _content_range(value: str) -> tuple[int | None, int | None]:
    """(first byte, total) from ``bytes 100-999/1000`` or ``bytes */1000``; None for a part that is absent or ``*``."""
    text = (value or "").strip()
    if not text.lower().startswith("bytes"):
        return None, None
    span, _, total_text = text[5:].strip().partition("/")
    first_text = span.partition("-")[0].strip()
    first = int(first_text) if first_text.isdigit() else None
    total = int(total_text.strip()) if total_text.strip().isdigit() else None
    return first, total


def _fetch(
    *,
    url: str,
    partial: Path,
    label: str,
    resume_from: int,
    progress_callback: ProgressCallback | None,
    cancel_check: CancelCheck | None,
    validator: str = "",
    plan: _ResumePlan | None = None,
    report: _FetchReport | None = None,
) -> int:
    """One request. With ``resume_from`` it asks for the rest (``Range``), on condition that the file is still the one the
    partial came from (``If-Range`` with ``validator``):

    * 206: the rest is appended, after checking that it starts where the partial ends;
    * 200: the server sent the whole file (it ignores ranges, or the file changed): the partial is replaced from the first byte;
    * 416: nothing lies beyond the partial. It is whole if the server's length says so, and stale otherwise.
    """
    report = report if report is not None else _FetchReport()
    headers = {"User-Agent": DOWNLOAD_USER_AGENT}
    if resume_from > 0:
        headers["Range"] = f"bytes={resume_from}-"
        if validator:
            headers["If-Range"] = validator
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", 200) or 200)
            append = resume_from > 0 and status == 206
            if resume_from > 0 and status == 200:
                # The server ignored the range request; restart cleanly.
                append = False
            report.appended = append
            content_type = str(response.headers.get("Content-Type") or "").lower()
            if content_type.startswith("text/html"):
                raise ModelInstallError(
                    f"The download of {label} returned a web page instead of the model file. "
                    "A proxy or captive portal is most likely intercepting the connection.",
                    category="intercepted",
                )
            declared = int(response.headers.get("Content-Length") or 0)
            etag = str(response.headers.get("ETag") or "")
            last_modified = str(response.headers.get("Last-Modified") or "")
            total = declared + (resume_from if append else 0)
            if append:
                first, whole = _content_range(str(response.headers.get("Content-Range") or ""))
                if first is not None and first != resume_from:
                    raise _ResumeMismatch(
                        f"The resumed download of {label} did not line up with the server.",
                        detail=f"asked from byte {resume_from}, got from byte {first}",
                    )
                if validator.startswith('"') and etag and etag != validator:  # an ETag was sent (a date is never quoted)
                    raise _ResumeMismatch(
                        f"{label} changed on the server while its download was interrupted.",
                        detail=f"had {validator}, server now has {etag}",
                    )
                if whole:
                    total = whole
            downloaded = resume_from if append else 0
            partial.parent.mkdir(parents=True, exist_ok=True)
            available = free_bytes(partial.parent)
            if total and available and total - downloaded > available:
                raise ModelInstallError(
                    f"Not enough free disk space to download {label} "
                    f"({(total - downloaded) / 1e9:.1f} GB needed).",
                    category="disk",
                )
            if not append and plan is not None:
                # Before the first byte: an interruption at any point then leaves a partial that says what it is a part of.
                _unlink(partial)
                _write_json_atomic(plan.sidecar, {
                    "version": PARTIAL_SIDECAR_VERSION, "url": url, "size": plan.size, "sha256": plan.sha256,
                    "etag": etag, "last_modified": last_modified, "length": declared,
                })
            with partial.open("ab" if append else "wb") as handle:
                try:
                    while True:
                        _raise_if_cancelled(cancel_check)
                        chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback is not None:
                            progress_callback(label, downloaded, total)
                finally:
                    # Whatever arrived is on disk before the error (or the cancel) leaves: it is what the next attempt continues.
                    handle.flush()
                    os.fsync(handle.fileno())
            if total and downloaded != total:
                raise ModelInstallError(
                    f"The download of {label} ended early ({downloaded} of {total} bytes).",
                    category="truncated",
                )
            return downloaded
    except urllib.error.HTTPError as exc:
        if int(exc.code) == 416 and resume_from > 0:
            _first, whole = _content_range(str((exc.headers or {}).get("Content-Range") or ""))
            expected = plan.size if plan is not None and plan.size else whole
            if expected and expected == resume_from and (whole is None or whole == resume_from):
                report.appended = True  # nothing was missing: the partial is the whole file, and is verified like any other
                return resume_from
            raise _ResumeMismatch(
                f"The resumed download of {label} did not line up with the server.",
                detail=f"have {resume_from} bytes, server has {whole if whole is not None else 'an unknown number'}",
            ) from exc
        raise _http_error(label, exc) from exc
    except urllib.error.URLError as exc:
        raise _url_error(label, exc) from exc
    except (TimeoutError, ConnectionError, http.client.HTTPException) as exc:
        raise ModelInstallError(
            f"The connection dropped while downloading {label}.",
            category="network",
            detail=str(exc),
        ) from exc


def _http_error(label: str, exc: urllib.error.HTTPError) -> ModelInstallError:
    code = int(exc.code)
    if code in {401, 403}:
        return ModelInstallError(
            f"Access to {label} was refused by the model host (HTTP {code}).",
            category="auth",
            detail=str(exc.reason),
        )
    if code == 404:
        return ModelInstallError(
            f"{label} is no longer published at the pinned model revision (HTTP 404).",
            category="not_found",
            detail=str(exc.reason),
        )
    if code == 416:
        return ModelInstallError(
            f"The resumed download of {label} did not line up with the server.",
            category="truncated",
            detail=str(exc.reason),
        )
    if code == 429:
        return ModelInstallError(
            f"The model host is rate limiting downloads of {label} (HTTP 429).",
            category="rate_limit",
            detail=str(exc.reason),
        )
    return ModelInstallError(
        f"Downloading {label} failed with HTTP {code}.",
        category="network",
        detail=str(exc.reason),
    )


def _url_error(label: str, exc: urllib.error.URLError) -> ModelInstallError:
    reason = exc.reason
    if isinstance(reason, ssl.SSLCertVerificationError):
        return ModelInstallError(
            f"The secure connection for {label} could not be verified. "
            "A corporate proxy is most likely inspecting HTTPS traffic.",
            category="certificate",
            detail=str(reason),
        )
    text = str(reason)
    lowered = text.lower()
    if "getaddrinfo" in lowered or "name or service" in lowered or "no such host" in lowered:
        return ModelInstallError(
            f"Image Triage could not look up the model host while downloading {label}. "
            "Check the network connection.",
            category="dns",
            detail=text,
        )
    return ModelInstallError(
        f"Downloading {label} failed: {text}",
        category="network",
        detail=text,
    )


__all__ = [
    "BUNDLE_METADATA_FILENAME",
    "BundleStatus",
    "ModelInstallCancelled",
    "ModelInstallError",
    "STATE_CORRUPT",
    "STATE_MISSING",
    "STATE_PARTIAL",
    "STATE_READY",
    "STATE_STALE",
    "all_bundle_statuses",
    "bundle_install_dir",
    "bundle_lock",
    "bundle_partial_bytes",
    "bundle_partial_dir",
    "bundle_repo_id",
    "bundle_revision",
    "bundle_status",
    "MIGRATED_CACHE_NAMES",
    "install_bundle",
    "migrate_ai_assets",
    "activation_journal_path",
    "read_bundle_metadata",
    "recover_interrupted_activations",
    "repair_bundle",
    "sha256_file",
    "uninstall_bundle",
]
