"""Background tasks for the AI features: result loading, model and runtime install/uninstall, folder probe, prefilter decisions."""
from __future__ import annotations

import logging
import shutil
import subprocess
import time

from PySide6.QtCore import QObject, QRunnable, Signal
from dataclasses import dataclass
from pathlib import Path

from ..ai_model import AIModelInstallation, download_ai_model as download_managed_ai_model
from ..ai_results import AIBundle, inspect_ai_bundle_source, load_ai_bundle
from ..ai_workflow import ai_report_artifacts_ready, ai_semantic_artifacts_ready, existing_hidden_ai_report_dir
from ..aiculler_workflow import aiculler_db_path, aiculler_rerank_readiness, build_aiculler_workflow_paths, latest_adapter_model_version
from ..catalog import CatalogRepository
from ..perf import perf_logger
from ..phash_prefilter import build_phash_prefilter_paths, load_phash_prefilter_decisions
from ..proc_utils import _headless_background_popen_kwargs
from ..scanner import normalized_path_key

_logger = logging.getLogger(__name__)


@dataclass(slots=True, frozen=True)
class AISetupSelection:
    """Captures the optional AI components the user chose to install."""
    install_runtime: bool
    runtime_variant: str
    include_torch_runtime: bool
    download_aiculler_clip_model: bool
    download_aiculler_topiq_model: bool
    download_aiculler_face_model: bool
    download_semantic_model: bool

    @property
    def download_model(self) -> bool:
        return (
            self.download_aiculler_clip_model
            or self.download_aiculler_topiq_model
            or self.download_aiculler_face_model
            or self.download_semantic_model
        )


class PostAIRunBundleLoadSignals(QObject):
    """Signals emitted by the post-AI-run bundle loader."""
    finished = Signal(str, str, str, object, object)  # folder, report_dir, html_report_path, bundle, source_details
    failed = Signal(str, str, str, str)  # folder, report_dir, html_report_path, error


class PostAIRunBundleLoadTask(QRunnable):
    """Loads the freshly written AI bundle off the UI thread so the post-AI
    flow doesn't freeze the GUI on slow/UNC paths."""

    def __init__(
        self,
        *,
        folder: str,
        report_dir: str,
        html_report_path: str,
        catalog_db_path: str | Path | None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.report_dir = report_dir
        self.html_report_path = html_report_path
        self.catalog_db_path = Path(catalog_db_path) if catalog_db_path else None
        self.signals = PostAIRunBundleLoadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        try:
            source_details = inspect_ai_bundle_source(self.report_dir)
            bundle: AIBundle | None = None
            repository = (
                CatalogRepository(self.catalog_db_path)
                if self.catalog_db_path is not None
                else CatalogRepository()
            )
            if self.folder and source_details.cache_key:
                cached_entry = repository.load_ai_bundle(self.folder, cache_key=source_details.cache_key)
                if cached_entry is not None:
                    bundle = cached_entry.bundle
            if bundle is None:
                bundle = load_ai_bundle(self.report_dir)
                if source_details.cache_key and bundle.results_by_path:
                    repository.save_ai_bundle(
                        self.folder,
                        cache_key=source_details.cache_key,
                        bundle=bundle,
                    )
            if logger.enabled:
                logger.duration(
                    "post_ai_run.bundle_load",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    report_dir=self.report_dir,
                    results=len(bundle.results_by_path or {}),
                )
            self.signals.finished.emit(self.folder, self.report_dir, self.html_report_path, bundle, source_details)
        except (FileNotFoundError, ValueError, OSError) as exc:
            if logger.enabled:
                logger.duration(
                    "post_ai_run.bundle_load.failed",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    error=str(exc),
                )
            self.signals.failed.emit(self.folder, self.report_dir, self.html_report_path, str(exc))


class HiddenAIResultsLoadSignals(QObject):
    """Signals emitted by the hidden AI-result autoload worker."""
    finished = Signal(str, int, object, object, str)
    missing = Signal(str, int)
    failed = Signal(str, int, str)


class HiddenAIResultsLoadTask(QRunnable):
    """Loads saved AI results without blocking folder display."""
    def __init__(
        self,
        *,
        folder: str,
        token: int,
        catalog_db_path: str | Path | None,
    ) -> None:
        super().__init__()
        self.folder = folder
        self.token = token
        self.catalog_db_path = Path(catalog_db_path) if catalog_db_path else None
        self.signals = HiddenAIResultsLoadSignals()
        self._cancelled = False
        self.setAutoDelete(True)

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        try:
            report_dir = existing_hidden_ai_report_dir(self.folder)
            if self._cancelled:
                return
            if report_dir is None:
                if logger.enabled:
                    logger.duration("hidden_ai.load", (time.perf_counter() - start) * 1000.0, folder=self.folder, state="missing")
                self.signals.missing.emit(self.folder, self.token)
                return

            source_details = inspect_ai_bundle_source(report_dir)
            if self._cancelled:
                return

            bundle: AIBundle | None = None
            cache_source = "file"
            repository = CatalogRepository(self.catalog_db_path) if self.catalog_db_path is not None else CatalogRepository()
            if source_details.cache_key:
                cached_entry = repository.load_ai_bundle(self.folder, cache_key=source_details.cache_key)
                if self._cancelled:
                    return
                if cached_entry is not None:
                    bundle = cached_entry.bundle
                    cache_source = "catalog"

            if bundle is None:
                bundle = load_ai_bundle(report_dir)
                if source_details.cache_key and bundle.results_by_path:
                    repository.save_ai_bundle(self.folder, cache_key=source_details.cache_key, bundle=bundle)

            if self._cancelled:
                return
            if logger.enabled:
                logger.duration(
                    "hidden_ai.load",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    state=cache_source,
                    results=len(bundle.results_by_path or {}),
                )
            self.signals.finished.emit(self.folder, self.token, bundle, source_details, cache_source)
        except (FileNotFoundError, ValueError, OSError) as exc:
            if logger.enabled:
                logger.duration(
                    "hidden_ai.load.failed",
                    (time.perf_counter() - start) * 1000.0,
                    folder=self.folder,
                    error=str(exc),
                )
            self.signals.failed.emit(self.folder, self.token, str(exc))


class AIModelDownloadSignals(QObject):
    """Signals for the managed AI model download worker."""
    started = Signal(str)
    progress = Signal(str, int, int)
    finished = Signal(str)
    failed = Signal(str)


@dataclass(slots=True, frozen=True)
class AIModelDownloadRequest:
    """One managed Hugging Face model to fetch through the shared installer."""
    label: str
    installation: AIModelInstallation
    force: bool = False


class AIModelDownloadTask(QRunnable):
    """Downloads selected managed AI model bundles on a background worker thread."""
    def __init__(
        self,
        *,
        requests: tuple[AIModelDownloadRequest, ...],
    ) -> None:
        super().__init__()
        self.requests = requests
        self.signals = AIModelDownloadSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        completed: list[str] = []
        failures: list[str] = []
        for request in self.requests:
            self.signals.started.emit(f"{request.label}: {request.installation.install_dir}")

            def emit_progress(filename: str, current: int, total: int, *, label: str = request.label) -> None:
                self.signals.progress.emit(f"{label}: {filename}", current, total)

            try:
                download_managed_ai_model(
                    request.installation,
                    force=request.force,
                    progress_callback=emit_progress,
                )
                completed.append(f"{request.label}: {request.installation.install_dir}")
            except Exception as exc:
                failures.append(f"{request.label}: {exc}")
        if failures:
            if logger.enabled:
                logger.duration(
                    "ai.model_download.failed",
                    (time.perf_counter() - start) * 1000.0,
                    requests=len(self.requests),
                    completed=len(completed),
                    failures=len(failures),
                )
            message = "Some AI model downloads failed:\n" + "\n".join(failures)
            if completed:
                message += "\n\nCompleted:\n" + "\n".join(completed)
            self.signals.failed.emit(message)
            return
        if logger.enabled:
            logger.duration(
                "ai.model_download",
                (time.perf_counter() - start) * 1000.0,
                requests=len(self.requests),
                completed=len(completed),
            )
        self.signals.finished.emit("\n".join(completed))


class AIUninstallSignals(QObject):
    """Signals for the AI component uninstall worker."""
    finished = Signal(object)  # (freed_bytes: int, removed: list[str], failures: list[str])


class AIUninstallTask(QRunnable):
    """Deletes selected AI runtime / model directories on a background thread."""
    def __init__(self, *, targets: tuple[tuple[str, Path, int], ...]) -> None:
        super().__init__()
        self.targets = targets
        self.signals = AIUninstallSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        freed = 0
        removed: list[str] = []
        failures: list[str] = []
        for label, path, size_bytes in self.targets:
            try:
                if path.exists():
                    shutil.rmtree(path, ignore_errors=True)
                if path.exists():
                    failures.append(f"{label}: could not fully remove {path}")
                else:
                    freed += int(size_bytes)
                    removed.append(label)
            except Exception as exc:
                failures.append(f"{label}: {exc}")
        self.signals.finished.emit((freed, removed, failures))


class AIRuntimeInstallSignals(QObject):
    """Signals for the on-demand AI runtime package installer."""
    started = Signal(str, str)
    progress = Signal(str)
    finished = Signal(str, str)
    failed = Signal(str)


class AIRuntimeInstallTask(QRunnable):
    """Installs the optional AI runtime profile in a background subprocess."""
    def __init__(
        self,
        *,
        command: list[str],
        cwd: Path,
        install_root: Path,
        variant_choice: str,
    ) -> None:
        super().__init__()
        self.command = command
        self.cwd = cwd
        self.install_root = install_root
        self.variant_choice = variant_choice
        self.signals = AIRuntimeInstallSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        logger = perf_logger()
        start = time.perf_counter() if logger.enabled else 0.0
        output_lines: list[str] = []
        try:
            self.signals.started.emit(str(self.install_root), self.variant_choice)
            process = subprocess.Popen(
                self.command,
                cwd=str(self.cwd),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **_headless_background_popen_kwargs(),
            )
        except Exception as exc:
            if logger.enabled:
                logger.duration(
                    "ai.runtime_install.failed",
                    (time.perf_counter() - start) * 1000.0,
                    install_root=str(self.install_root),
                    variant=self.variant_choice,
                    error=str(exc),
                )
            self.signals.failed.emit(str(exc))
            return

        assert process.stdout is not None
        with process.stdout:
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                output_lines.append(text)
                self.signals.progress.emit(text)
        return_code = process.wait()
        if return_code != 0:
            if logger.enabled:
                logger.duration(
                    "ai.runtime_install.failed",
                    (time.perf_counter() - start) * 1000.0,
                    install_root=str(self.install_root),
                    variant=self.variant_choice,
                    return_code=return_code,
                    output_lines=len(output_lines),
                )
            tail = "\n".join(output_lines[-30:])
            if tail:
                self.signals.failed.emit(
                    f"Could not install the AI runtime.\n\n{tail}"
                )
            else:
                self.signals.failed.emit(
                    f"Could not install the AI runtime (exit code {return_code})."
                )
            return
        if logger.enabled:
            logger.duration(
                "ai.runtime_install",
                (time.perf_counter() - start) * 1000.0,
                install_root=str(self.install_root),
                variant=self.variant_choice,
                output_lines=len(output_lines),
            )
        self.signals.finished.emit(str(self.install_root), self.variant_choice)


class _PrefilterDecisionsSignals(QObject):
    ready = Signal(int, str, object)


class _PrefilterDecisionsTask(QRunnable):
    """Loads a folder's saved pHash prefilter decisions off the GUI thread (for a folder on a share)."""

    def __init__(self, token: int, folder: str) -> None:
        super().__init__()
        self.token = token
        self.folder = folder
        self.signals = _PrefilterDecisionsSignals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            decisions = load_phash_prefilter_decisions(build_phash_prefilter_paths(self.folder))
            keyed = {key: decision for path, decision in decisions.items() if (key := normalized_path_key(path))}
        except Exception:
            _logger.exception("Failed to load phash prefilter decisions for %s", self.folder)
            keyed = {}
        self.signals.ready.emit(self.token, self.folder, keyed)


def _unknown_ai_folder_probe(folder: str) -> dict:
    """What the AI toolbar assumes about a folder it has not been able to look at yet: nothing found."""
    return {
        "folder": folder,
        "at": 0.0,
        "ranked_export_exists": False,
        "semantic_ready": False,
        "report_ready": False,
        "adapter_version": "",
        "rerank_ready": False,
        "adapter_db_exists": False,
        "aiculler_available": False,
        "phash_available": False,
    }


def _compute_ai_folder_probe(ai_paths, folder: str) -> dict:
    """Look inside a folder's hidden AI directory: existence checks and a few SQLite opens.

    It touches the disk, so for a folder on a network / removable drive it only runs on a worker thread."""
    db_path = aiculler_db_path(ai_paths) if ai_paths is not None else None
    probe = _unknown_ai_folder_probe(folder)
    probe["ranked_export_exists"] = bool(ai_paths is not None and ai_paths.ranked_export_path.exists())
    probe["semantic_ready"] = bool(ai_paths is not None and ai_semantic_artifacts_ready(ai_paths))
    probe["report_ready"] = bool(ai_paths is not None and ai_report_artifacts_ready(ai_paths))
    probe["adapter_version"] = latest_adapter_model_version(db_path) if db_path is not None else ""
    probe["rerank_ready"] = bool(db_path is not None and aiculler_rerank_readiness(db_path).get("can_rerank"))
    probe["adapter_db_exists"] = bool(db_path is not None and db_path.exists())
    try:
        probe["aiculler_available"] = bool(folder and aiculler_db_path(build_aiculler_workflow_paths(folder)).exists())
    except Exception:
        _logger.exception("Failed to probe aiculler availability for %s", folder)
        probe["aiculler_available"] = False
    try:
        probe["phash_available"] = bool(folder and build_phash_prefilter_paths(folder).rows_path.exists())
    except Exception:
        _logger.exception("Failed to probe phash prefilter availability for %s", folder)
        probe["phash_available"] = False
    return probe


class _AIFolderProbeSignals(QObject):
    ready = Signal(int, str, object)


class _AIFolderProbeTask(QRunnable):
    """One AI-folder probe for a folder on a share, run off the GUI thread."""

    def __init__(self, generation: int, folder: str, ai_paths) -> None:
        super().__init__()
        self.generation = generation
        self.folder = folder
        self._ai_paths = ai_paths
        self.signals = _AIFolderProbeSignals()
        self.setAutoDelete(False)

    def run(self) -> None:
        try:
            probe = _compute_ai_folder_probe(self._ai_paths, self.folder)
        except Exception:
            _logger.exception("Failed to probe the AI data of %s", self.folder)
            probe = _unknown_ai_folder_probe(self.folder)
        self.signals.ready.emit(self.generation, self.folder, probe)
