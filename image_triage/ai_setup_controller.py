"""AI runtime and model setup: status and availability checks, the Set Up AI dialog, runtime install, model download, readiness checks, repair and uninstall. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.1)."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QEventLoop, QObject, QRunnable, QTimer, Qt
from PySide6.QtWidgets import QApplication, QButtonGroup, QCheckBox, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QMessageBox, QRadioButton, QVBoxLayout
from dataclasses import replace
from pathlib import Path

from . import ai_setup_plan as plan
from .ai_manifest import MODEL_BUNDLES
from .ai_model_store import bundle_status
from .ai_model import AIModelInstallation, DEFAULT_AICULLER_CLIP_SIZE_MB, DEFAULT_AICULLER_FACE_SIZE_MB, DEFAULT_AICULLER_TOPIQ_SIZE_MB, resolve_aiculler_clip_model_installation, resolve_aiculler_face_model_installation, resolve_aiculler_topiq_model_installation, resolve_semantic_model_installation
from .ai_runtime_packages import AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT, AIRuntimeInstallationStatus, ai_runtime_variant_label, directory_size_bytes, estimate_ai_runtime_download_size_mb, estimate_ai_runtime_installed_size_mb, load_ai_runtime_installation_status
from .ai_workflow import ai_device_environment_override
from .job_controller import JobSpec
from .tasks.ai_tasks import AIModelDownloadRequest, AIModelDownloadTask, AIRuntimeInstallTask, AISetupSelection, AIUninstallTask

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .window import MainWindow


def _format_bytes(size: int) -> str:
    value = float(max(0, int(size)))
    for unit in ("bytes", "KB", "MB", "GB", "TB"):
        if value < 1024.0 or unit == "TB":
            if unit == "bytes":
                return f"{int(value)} bytes"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


class AiSetupController(QObject):
    """AI runtime and model setup: status and availability checks, the Set Up AI dialog, runtime install, model download, readiness checks, repair and uninstall. Extracted from MainWindow (docs/mainwindow_decomposition_plan.md, DC-4.1)."""

    def __init__(self, window: "MainWindow") -> None:
        super().__init__(window)
        self._window = window
        self._active_ai_bundle_task: QRunnable | None = None
        self._active_ai_readiness_task: QRunnable | None = None
        self._active_ai_repair_task: QRunnable | None = None
        self._aiculler_face_model_installation = resolve_aiculler_face_model_installation()
        self._aiculler_topiq_model_installation = resolve_aiculler_topiq_model_installation()
        self._last_ai_readiness_results: dict[str, object] = {}
        self._pending_ai_aiculler_clip_download_after_runtime = False
        self._pending_ai_aiculler_face_download_after_runtime = False
        self._pending_ai_aiculler_topiq_download_after_runtime = False
        self._pending_ai_semantic_model_download_after_runtime = False
        self._semantic_model_installation = resolve_semantic_model_installation()

    def managed_semantic_model_installation(self) -> AIModelInstallation:
        return self._semantic_model_installation

    def managed_aiculler_clip_model_installation(self) -> AIModelInstallation:
        return resolve_aiculler_clip_model_installation()

    def managed_aiculler_topiq_model_installation(self) -> AIModelInstallation:
        return self._aiculler_topiq_model_installation

    def managed_aiculler_face_model_installation(self) -> AIModelInstallation:
        return self._aiculler_face_model_installation

    def managed_ai_runtime_status(self) -> AIRuntimeInstallationStatus:
        now = time.perf_counter()
        cached = getattr(self, "_ai_runtime_status_cache", None)
        cached_at = getattr(self, "_ai_runtime_status_cache_at", 0.0)
        if cached is not None and (now - cached_at) < self._window._AI_RUNTIME_STATUS_TTL_S:
            return cached
        status = load_ai_runtime_installation_status()
        self._ai_runtime_status_cache = status
        self._ai_runtime_status_cache_at = now
        return status

    def invalidate_ai_runtime_status_cache(self) -> None:
        self._ai_runtime_status_cache = None
        self._ai_runtime_status_cache_at = 0.0

    def refresh_ai_runtime_preferences(self) -> None:
        runtime_status = self.managed_ai_runtime_status()
        semantic_model_name = self._window._ai_runtime.semantic_model_name
        semantic_installation = self.managed_semantic_model_installation()
        if (
            not (os.environ.get("AICULLING_SEMANTIC_MODEL_NAME", "") or "").strip()
            and semantic_installation.is_installed
        ):
            semantic_model_name = semantic_installation.model_name
        device = self._window._ai_runtime.device
        device_override = ai_device_environment_override()
        if device_override is not None:
            device = device_override
        elif AI_RUNTIME_GPU_VARIANT in runtime_status.installed_variants:
            device = "cuda"
        elif runtime_status.installed_variants == (AI_RUNTIME_CPU_VARIANT,):
            device = "cpu"
        self._window._ai_runtime = replace(
            self._window._ai_runtime,
            device=device,
            batch_size=self._window._settings_ctl.configured_ai_embed_batch_size(),
            semantic_model_name=semantic_model_name,
        )

    def ai_runtime_available(self) -> bool:
        return self.managed_ai_runtime_status().is_installed

    def semantic_model_available(self) -> bool:
        explicit_model_name = (os.environ.get("AICULLING_SEMANTIC_MODEL_NAME", "") or "").strip()
        if explicit_model_name:
            path = Path(explicit_model_name).expanduser()
            if path.is_absolute() or "/" in explicit_model_name or "\\" in explicit_model_name or explicit_model_name.startswith("."):
                return path.exists()
            return True
        return self.managed_semantic_model_installation().is_installed

    def aiculler_clip_model_available(self) -> bool:
        return self.managed_aiculler_clip_model_installation().is_installed

    def aiculler_topiq_model_available(self) -> bool:
        return self.managed_aiculler_topiq_model_installation().is_installed

    def aiculler_face_model_available(self) -> bool:
        """The face landmarker and the AdaFace recognizer (the People index and the eyes-closed check) are installed. The old AuraFace pack no longer counts."""
        return self.face_models_ready()

    def face_models_ready(self) -> bool:
        from .ai_model_store import bundle_status
        from .cascade.rankers import register_bundles

        register_bundles()
        return all(bundle_status(key).is_ready for key in ("faces_blink", "faces_identity"))

    def ai_setup_hardware(self) -> tuple[bool, str]:
        """(this PC can use a graphics card, the runtime variant to install). An installed runtime decides; otherwise an NVIDIA card does."""
        from .ai_platform import nvidia_gpu_name

        status = self.managed_ai_runtime_status()
        if AI_RUNTIME_GPU_VARIANT in status.installed_variants:
            return True, AI_RUNTIME_GPU_VARIANT
        if status.installed_variants == (AI_RUNTIME_CPU_VARIANT,):
            return False, AI_RUNTIME_CPU_VARIANT
        gpu = nvidia_gpu_name() is not None
        return gpu, AI_RUNTIME_GPU_VARIANT if gpu else AI_RUNTIME_CPU_VARIANT

    def installed_optional_models(self) -> frozenset[str]:
        """Optional models (see ``ai_setup_plan``) whose files are in place and verified."""
        return frozenset(item.key for item in plan.OPTIONAL_MODELS if bundle_status(item.bundle).is_ready)

    def chosen_optional_models(self) -> frozenset[str]:
        """What the person chose in Set Up AI, plus whatever is installed. Without a choice, only what is installed: never demand a model nobody downloaded."""
        saved = plan.load_selection(self._window._settings)
        return (saved or frozenset()) | self.installed_optional_models()

    def core_installed(self) -> bool:
        status = self.managed_ai_runtime_status()
        runtime_ready = bool(set(status.installed_variants) & set(status.torch_installed_variants))
        return runtime_ready and self.aiculler_clip_model_available() and self.face_models_ready()

    def remove_optional_models(self, keys: frozenset[str]) -> None:
        """Delete the files of the given optional models. They download again when selected."""
        import shutil

        from .ai_model_store import bundle_install_dir

        for key in sorted(keys):
            try:
                shutil.rmtree(bundle_install_dir(plan.model(key).bundle), ignore_errors=False)
            except FileNotFoundError:
                pass
            except OSError as exc:
                self._window.statusBar().showMessage(f"Could not remove {plan.model(key).label}: {exc}")
        self.invalidate_ai_runtime_status_cache()

    def show_ai_setup_dialog(
        self,
        *,
        automatic: bool,
        title: str,
        prompt_text: str,
        allow_runtime: bool,
        allow_model: bool,
        default_install_runtime: bool,
        default_include_torch_runtime: bool,
        default_download_aiculler_clip_model: bool,
        default_download_aiculler_topiq_model: bool,
        default_download_aiculler_face_model: bool,
        default_download_semantic_model: bool,
    ) -> AISetupSelection | None:
        """The Set Up AI dialog. Returns what to install, or None when nothing is to be installed (cancelled, removal only, a check, or a menu action that already ran)."""
        del (
            automatic,
            title,
            prompt_text,
            allow_runtime,
            allow_model,
            default_install_runtime,
            default_include_torch_runtime,
            default_download_aiculler_clip_model,
            default_download_aiculler_topiq_model,
            default_download_aiculler_face_model,
            default_download_semantic_model,
        )
        from shutil import disk_usage

        from .ui.ai_setup_dialog import OUTCOME_CHECK, OUTCOME_INSTALL, OUTCOME_REMOVE, AISetupDialog, SetupDialogInput

        runtime_status = self.managed_ai_runtime_status()
        gpu, variant = self.ai_setup_hardware()
        installed = self.installed_optional_models()
        saved = plan.load_selection(self._window._settings)
        selection = (saved | installed) if saved is not None else plan.default_selection(gpu=gpu, installed=installed)
        try:
            free_gb = disk_usage(str(Path.home())).free / 1e9
        except OSError:
            free_gb = None
        dialog = AISetupDialog(
            SetupDialogInput(
                gpu=gpu,
                variant=variant,
                core_installed=self.core_installed(),
                installed=installed,
                selection=selection,
                free_gb=free_gb,
            ),
            self._window,
            on_check=self.check_ai_readiness,
            on_repair=self.repair_ai_components,
            on_remove_everything=self.uninstall_ai_components,
        )
        dialog.exec()
        action = dialog.pending_menu_action()
        if action is not None:
            QTimer.singleShot(0, action)
            return None
        outcome = dialog.outcome
        if outcome is None:
            return None
        chosen = dialog.selection()
        plan.save_selection(self._window._settings, chosen)
        removed = dialog.to_remove()
        if removed:
            self.remove_optional_models(removed)
        if outcome == OUTCOME_REMOVE:
            self._window.statusBar().showMessage(f"Removed {len(removed)} AI model(s).")
            return None
        if outcome == OUTCOME_CHECK:
            self.check_ai_readiness()
            return None
        # The runtime always carries PyTorch; the face models and TinyCLIP are part of the core and arrive with the capability step.
        runtime_ready = variant in runtime_status.torch_installed_variants
        return AISetupSelection(
            install_runtime=not runtime_ready,
            runtime_variant=variant,
            include_torch_runtime=True,
            download_aiculler_clip_model=not self.aiculler_clip_model_available(),
            download_aiculler_topiq_model=False,
            download_aiculler_face_model=False,
            download_semantic_model=False,
        )

    def migrate_managed_ai_assets(self) -> None:
        from .ai_model_store import migrate_ai_assets, recover_interrupted_activations

        try:
            moved = migrate_ai_assets()
            recovered = recover_interrupted_activations()
        except OSError as exc:
            self._window.statusBar().showMessage(f"Could not move the AI cache to its new location: {exc}")
            return
        if recovered:
            self._window.statusBar().showMessage(
                f"Recovered {len(recovered)} interrupted AI model installation(s)."
            )
        elif moved:
            self._window.statusBar().showMessage(
                f"Moved {len(moved)} AI cache folder(s) to the new managed location."
            )

    def startup_splash_visible(self) -> bool:
        from .ui.splash_screen import StartupSplash

        return any(
            isinstance(widget, StartupSplash) and widget.isVisible()
            for widget in QApplication.topLevelWidgets()
        )

    def maybe_prompt_for_ai_setup(self) -> None:
        """Once the window is up: adopt older model folders and let the AI status chip say whether anything needs setting up. No dialog opens by itself; the chip
        is there, and the first Analyze offers setup (``AiStatusController.show_first_run_prompt``)."""
        if not getattr(sys, "frozen", False):
            return
        # Wait for the window to be on screen and the splash gone.
        if not self._window.isVisible() or self.startup_splash_visible():
            QTimer.singleShot(250, self.maybe_prompt_for_ai_setup)
            return
        # Adopt a previous release's model and cache directories. This is the one deliberate migration point; resolving a managed path never moves files by itself.
        self.migrate_managed_ai_assets()
        self._notify_status()

    def start_ai_setup_selection(
        self,
        selection: AISetupSelection,
        *,
        force_runtime: bool,
    ) -> bool:
        """Run the combined setup as one runtime-then-model sequence."""
        if selection.install_runtime:
            self.start_ai_runtime_install(
                selection.runtime_variant,
                force=force_runtime,
                include_torch=selection.include_torch_runtime,
                download_aiculler_clip_after=selection.download_aiculler_clip_model,
                download_aiculler_topiq_after=selection.download_aiculler_topiq_model,
                download_aiculler_face_after=selection.download_aiculler_face_model,
                download_semantic_model_after=selection.download_semantic_model,
            )
            return True
        if selection.download_model:
            self.start_ai_model_download(
                download_aiculler_clip=selection.download_aiculler_clip_model,
                download_aiculler_topiq=selection.download_aiculler_topiq_model,
                download_aiculler_face=selection.download_aiculler_face_model,
                download_semantic=selection.download_semantic_model,
                force=False,
            )
            return True
        if self.missing_capability_bundles():  # the runtime is in place; the cascade's models are not
            self.start_ai_capability_bundles(title="AI Setup", context="The AI models were installed.")
            return True
        self._window.statusBar().showMessage("AI is already set up for this profile.")
        return False

    def missing_capability_bundles(self) -> tuple[str, ...]:
        """Model bundles that the selected capabilities need and that are not installed yet."""
        from .ai_manifest import CAPABILITIES

        wanted: list[str] = []
        for key in self.selected_ai_capabilities():
            wanted.extend(bundle for bundle in CAPABILITIES[key].model_bundles if bundle not in wanted)
        return tuple(key for key in wanted if not bundle_status(key).is_ready)

    def _notify_status(self, message: str | None = None) -> None:
        """Let the AI status chip catch up once the handler that called this has finished."""
        status = getattr(self._window, "_ai_status", None)
        if status is None:
            return
        if message is not None:
            status._install_message = message
        QTimer.singleShot(0, status.refresh)

    def set_ai_setup_busy(self, message: str | None) -> None:
        """Show AI setup state inside the main window instead of a dialog."""
        overlay = getattr(self._window, "_ai_setup_overlay", None)
        if overlay is not None:
            overlay.set_message(message)
        self._notify_status(message or "")

    def prompt_for_ai_model_install(self, *, automatic: bool) -> None:
        aiculler_clip_missing = not self.aiculler_clip_model_available()
        aiculler_topiq_missing = False  # retired
        aiculler_face_missing = False  # arrives with the capability step
        selection = self.show_ai_setup_dialog(
            automatic=automatic,
            title="Set Up AI",
            prompt_text="Choose an AI runtime profile.",
            allow_runtime=False,
            allow_model=True,
            default_install_runtime=False,
            default_include_torch_runtime=True,
            default_download_aiculler_clip_model=aiculler_clip_missing,
            default_download_aiculler_topiq_model=aiculler_topiq_missing,
            default_download_aiculler_face_model=aiculler_face_missing,
            default_download_semantic_model=False,
        )
        if selection is None:
            self._window.statusBar().showMessage("AI setup skipped for now.")
            return
        self.start_ai_setup_selection(
            selection,
            force_runtime=self.ai_runtime_available(),
        )

    def install_ai_runtime(self) -> None:
        if self._window._active_ai_runtime_task is not None or self._window._active_ai_model_task is not None:
            self._window.statusBar().showMessage("An AI component install is already running.")
            return
        selection = self.show_ai_setup_dialog(
            automatic=False,
            title="Set Up AI",
            prompt_text="Choose an AI runtime profile.",
            allow_runtime=True,
            allow_model=False,
            default_install_runtime=True,
            default_include_torch_runtime=True,
            default_download_aiculler_clip_model=False,
            default_download_aiculler_topiq_model=False,
            default_download_aiculler_face_model=False,
            default_download_semantic_model=False,
        )
        if selection is None:
            self._window.statusBar().showMessage("AI setup skipped for now.")
            return
        self.start_ai_setup_selection(
            selection,
            force_runtime=self.ai_runtime_available(),
        )

    def start_ai_runtime_install(
        self,
        variant_choice: str,
        *,
        force: bool = False,
        include_torch: bool = True,
        download_aiculler_clip_after: bool = False,
        download_aiculler_topiq_after: bool = False,
        download_aiculler_face_after: bool = False,
        download_semantic_model_after: bool = False,
    ) -> None:
        install_root = self.managed_ai_runtime_status().directories.root
        workspace_root = Path(__file__).resolve().parents[1]
        if getattr(sys, "frozen", False):
            runtime_root = Path(sys.executable).resolve().parent
            installer_name = "ai_runtime_installer.exe" if os.name == "nt" else "ai_runtime_installer"
            command = [str(runtime_root / installer_name), "install", "--variant", variant_choice]
            cwd = runtime_root
        else:
            command = [
                sys.executable,
                str(workspace_root / "packaging" / "ai_runtime_installer.py"),
                "install",
                "--variant",
                variant_choice,
            ]
            cwd = workspace_root
        command.extend(["--install-root", str(install_root)])
        if force:
            command.append("--force")
        if not include_torch:
            command.append("--no-torch")
        task = AIRuntimeInstallTask(
            command=command,
            cwd=cwd,
            install_root=install_root,
            variant_choice=variant_choice,
        )
        task.signals.started.connect(self.handle_ai_runtime_install_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_ai_runtime_install_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_ai_runtime_install_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_ai_runtime_install_failed, Qt.ConnectionType.QueuedConnection)
        self._window._active_ai_runtime_task = task
        self._pending_ai_aiculler_clip_download_after_runtime = bool(download_aiculler_clip_after)
        self._pending_ai_aiculler_topiq_download_after_runtime = bool(download_aiculler_topiq_after)
        self._pending_ai_aiculler_face_download_after_runtime = bool(download_aiculler_face_after)
        self._pending_ai_semantic_model_download_after_runtime = bool(download_semantic_model_after)
        self.set_ai_setup_busy("Installing AI runtime...")
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()
        self._window.statusBar().showMessage("Starting AI runtime install...")
        self._window._ai_model_pool.start(task)

    def handle_ai_runtime_install_started(self, install_root: str, variant_choice: str) -> None:
        del install_root, variant_choice
        self.set_ai_setup_busy("Installing AI runtime...")
        self._window.statusBar().showMessage("Installing AI runtime...")

    def handle_ai_runtime_install_progress(self, message: str) -> None:
        del message
        self.set_ai_setup_busy("Installing AI runtime...")

    def handle_ai_runtime_install_finished(self, install_root: str, variant_choice: str) -> None:
        self._notify_status()
        self._window._active_ai_runtime_task = None
        self.invalidate_ai_runtime_status_cache()
        self.refresh_ai_runtime_preferences()
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()
        self._window.statusBar().showMessage("AI runtime installed.")
        download_aiculler_clip = (
            self._pending_ai_aiculler_clip_download_after_runtime
            and not self.aiculler_clip_model_available()
        )
        download_aiculler_topiq = (
            self._pending_ai_aiculler_topiq_download_after_runtime
            and not self.aiculler_topiq_model_available()
        )
        download_aiculler_face = (
            self._pending_ai_aiculler_face_download_after_runtime
            and not self.aiculler_face_model_available()
        )
        download_semantic = (
            self._pending_ai_semantic_model_download_after_runtime and not self.semantic_model_available()
        )
        if download_aiculler_clip or download_aiculler_topiq or download_aiculler_face or download_semantic:
            self._pending_ai_aiculler_clip_download_after_runtime = False
            self._pending_ai_aiculler_topiq_download_after_runtime = False
            self._pending_ai_aiculler_face_download_after_runtime = False
            self._pending_ai_semantic_model_download_after_runtime = False
            self.start_ai_model_download(
                download_aiculler_clip=download_aiculler_clip,
                download_aiculler_topiq=download_aiculler_topiq,
                download_aiculler_face=download_aiculler_face,
                download_semantic=download_semantic,
                force=False,
            )
            return
        self._pending_ai_aiculler_clip_download_after_runtime = False
        self._pending_ai_aiculler_topiq_download_after_runtime = False
        self._pending_ai_aiculler_face_download_after_runtime = False
        self._pending_ai_semantic_model_download_after_runtime = False
        # The installer exiting zero does not prove any capability works, and
        # the runtime alone is not the whole selected feature set: finish the
        # remaining model bundles, then verify what was actually installed.
        self.start_ai_capability_bundles(
            title="AI Setup",
            context=f"The {ai_runtime_variant_label(variant_choice)} AI runtime was installed.",
        )

    def handle_ai_runtime_install_failed(self, message: str) -> None:
        self._notify_status()
        self._window._active_ai_runtime_task = None
        self._pending_ai_aiculler_clip_download_after_runtime = False
        self._pending_ai_aiculler_topiq_download_after_runtime = False
        self._pending_ai_aiculler_face_download_after_runtime = False
        self._pending_ai_semantic_model_download_after_runtime = False
        self.set_ai_setup_busy(None)
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()
        QMessageBox.warning(self._window, "AI Runtime Install", message)
        self._window.statusBar().showMessage("AI runtime install failed.")

    def download_ai_model(self) -> None:
        if self._window._active_ai_model_task is not None or self._window._active_ai_runtime_task is not None:
            self._window.statusBar().showMessage("An AI component install is already running.")
            return
        self.prompt_for_ai_model_install(automatic=False)

    def collect_uninstallable_ai_components(self) -> list[tuple[str, Path, int]]:
        """(label, directory, size_bytes) for every installed AI runtime/model dir.

        Only directories that actually exist with content are returned. The CLIP
        entry points at the shared model dir, so removing it clears every
        downloaded variant, including any extras the user added."""
        components: list[tuple[str, Path, int]] = []

        runtime_status = self.managed_ai_runtime_status()
        runtime_root = runtime_status.directories.root
        runtime_size = directory_size_bytes(runtime_root)
        if runtime_root.exists() and runtime_size > 0:
            profiles = ", ".join(
                ai_runtime_variant_label(variant) for variant in runtime_status.installed_variants
            ) or "installed"
            components.append((f"AI runtime ({profiles})", runtime_root, runtime_size))

        model_specs = (
            (
                "CLI-Culler CLIP model (all downloaded versions)",
                self.managed_aiculler_clip_model_installation().install_dir,
            ),
            ("TOPIQ technical quality model (retired)", self.managed_aiculler_topiq_model_installation().install_dir),
            ("InsightFace quality models (retired)", self.managed_aiculler_face_model_installation().install_dir),
            ("Semantic CLIP model", self.managed_semantic_model_installation().install_dir),
            *self._cascade_model_directories(),
        )
        seen: set[Path] = {runtime_root}
        for label, install_dir in model_specs:
            resolved = Path(install_dir)
            if resolved in seen:
                continue
            size = directory_size_bytes(resolved)
            if resolved.exists() and size > 0:
                components.append((label, resolved, size))
                seen.add(resolved)
        return components

    @staticmethod
    def _cascade_model_directories() -> tuple[tuple[str, Path], ...]:
        from .ai_model_store import bundle_install_dir
        from .cascade.rankers import register_bundles

        register_bundles()
        labels = (
            ("faces_blink", "Face landmarker (eyes-closed check and people)"),
            ("faces_identity", "Face recognizer (people grouping)"),
            ("maniqa", "MANIQA image quality model"),
            ("qrealign", "Q-ReAlign aesthetic model"),
        )
        return tuple((label, Path(bundle_install_dir(key))) for key, label in labels)

    def selected_ai_capabilities(self) -> tuple[str, ...]:
        """Which capabilities this installation is expected to provide.

        This is the same set ``Set Up AI`` installs, so verification can never
        demand something setup never downloaded. Torch-only features drop out
        when the user installed the compact base runtime.
        """
        from .ai_manifest import setup_capabilities

        status = self.managed_ai_runtime_status()
        has_torch = bool(set(status.installed_variants) & set(status.torch_installed_variants))
        return setup_capabilities(include_torch=has_torch, optional=plan.capabilities_for(self.chosen_optional_models()))

    def start_ai_readiness_check(
        self,
        *,
        busy_message: str,
        title: str,
        context: str = "",
        deep_models: bool = False,
        thorough: bool = False,
    ) -> None:
        from .ui.ai_readiness import AIReadinessTask

        if self._active_ai_readiness_task is not None:
            self._window.statusBar().showMessage("An AI readiness check is already running.")
            return
        task = AIReadinessTask(
            capability_keys=self.selected_ai_capabilities(),
            deep_models=deep_models,
            use_cache=False,
            thorough=thorough,
        )
        task.signals.progress.connect(
            self.set_ai_setup_busy, Qt.ConnectionType.QueuedConnection
        )
        task.signals.finished.connect(
            lambda results: self.handle_ai_readiness_finished(results, title, context),
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            self.handle_ai_readiness_failed, Qt.ConnectionType.QueuedConnection
        )
        self._active_ai_readiness_task = task
        self.set_ai_setup_busy(busy_message)
        self._window.statusBar().showMessage(busy_message)
        self._window._ai_model_pool.start(task)

    def start_ai_capability_bundles(self, *, title: str, context: str = "") -> None:
        """Download any model bundle the selected capabilities still need."""
        from .ui.ai_readiness import AIBundleInstallTask

        capabilities = self.selected_ai_capabilities()
        if not capabilities or self._active_ai_bundle_task is not None:
            self.verify_ai_setup(title=title, context=context)
            return
        task = AIBundleInstallTask(capabilities)
        task.signals.progress.connect(
            self.set_ai_setup_busy, Qt.ConnectionType.QueuedConnection
        )
        task.signals.finished.connect(
            lambda _installed: self.handle_ai_bundle_install_finished(title, context),
            Qt.ConnectionType.QueuedConnection,
        )
        task.signals.failed.connect(
            lambda message: self.handle_ai_bundle_install_failed(message, title, context),
            Qt.ConnectionType.QueuedConnection,
        )
        self._active_ai_bundle_task = task
        self.set_ai_setup_busy("Downloading AI models...")
        self._window.statusBar().showMessage("Downloading AI models...")
        self._window._ai_model_pool.start(task)

    def handle_ai_bundle_install_finished(self, title: str, context: str) -> None:
        self._notify_status()
        self._active_ai_bundle_task = None
        self.invalidate_ai_runtime_status_cache()
        self.verify_ai_setup(title=title, context=context)

    def handle_ai_bundle_install_failed(self, message: str, title: str, context: str) -> None:
        self._notify_status()
        self._active_ai_bundle_task = None
        # A download failure still leaves whatever succeeded, so report the
        # real per-capability state rather than a bare error.
        self._window.statusBar().showMessage(f"Some AI models could not be downloaded: {message}")
        self.verify_ai_setup(title=title, context=context)

    def verify_ai_setup(self, *, title: str, context: str = "") -> None:
        self.start_ai_readiness_check(
            busy_message="Verifying AI setup...", title=title, context=context
        )

    def handle_ai_readiness_finished(
        self,
        results: dict,
        title: str,
        context: str,
    ) -> None:
        self._notify_status()
        from .ui.ai_readiness import AIReadinessDialog, summarize

        self._active_ai_readiness_task = None
        self._active_ai_repair_task = None
        self.invalidate_ai_runtime_status_cache()
        self.refresh_ai_runtime_preferences()
        self.set_ai_setup_busy(None)
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()
        self._last_ai_readiness_results = dict(results)
        summary = summarize(results)
        self._window.statusBar().showMessage(summary)
        if results and all(health.ready for health in results.values()) and context:
            QMessageBox.information(self._window, title, f"{context}\n\n{summary}")
            return
        AIReadinessDialog(results, parent=self._window, title=title).exec()

    def handle_ai_readiness_failed(self, message: str) -> None:
        self._notify_status()
        self._active_ai_readiness_task = None
        self.set_ai_setup_busy(None)
        self._window._inspector.update_action_states()
        QMessageBox.warning(self._window, "AI Readiness", message)
        self._window.statusBar().showMessage("The AI readiness check could not run.")

    def check_ai_readiness(self) -> None:
        """Demo Ready: prove every selected AI feature works, right now.

        This is the one place that pays for the full proof — model weights are
        loaded and a forward pass is run for every selected capability, so it
        can take a couple of minutes on a cold machine.
        """
        self.start_ai_readiness_check(
            busy_message="Checking AI readiness (this can take a few minutes)...",
            title="AI Readiness",
            deep_models=True,
            thorough=True,
        )

    def repair_ai_components(self) -> None:
        from .ui.ai_readiness import AIRepairTask

        if self._active_ai_repair_task is not None or self._active_ai_readiness_task is not None:
            self._window.statusBar().showMessage("An AI operation is already running.")
            return
        capabilities = self.selected_ai_capabilities()
        if not capabilities:
            QMessageBox.information(
                self._window,
                "Repair AI",
                "There is no AI runtime installed yet. Run Set Up AI first.",
            )
            return
        confirmed = QMessageBox.question(
            self._window,
            "Repair AI",
            "Image Triage will verify every installed AI model and re-download "
            "anything that is missing or damaged.\n\nThis can take several minutes.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        if confirmed != QMessageBox.StandardButton.Ok:
            return
        task = AIRepairTask(capabilities)
        task.signals.progress.connect(
            self.set_ai_setup_busy, Qt.ConnectionType.QueuedConnection
        )
        task.signals.finished.connect(
            self.handle_ai_repair_finished, Qt.ConnectionType.QueuedConnection
        )
        task.signals.failed.connect(
            self.handle_ai_repair_failed, Qt.ConnectionType.QueuedConnection
        )
        self._active_ai_repair_task = task
        self.set_ai_setup_busy("Repairing AI...")
        self._window.statusBar().showMessage("Repairing AI...")
        self._window._ai_model_pool.start(task)

    def handle_ai_repair_finished(self, results: dict, runtime_required: bool) -> None:
        self._notify_status()
        self._active_ai_repair_task = None
        if runtime_required:
            # Model repair cannot rebuild damaged packages; offer the operation
            # that can instead of leaving the user to guess.
            self.set_ai_setup_busy(None)
            self._window._inspector.update_action_states()
            choice = QMessageBox.question(
                self._window,
                "Repair AI",
                "The downloaded models are now correct, but the AI runtime packages "
                "themselves are damaged and have to be reinstalled.\n\n"
                "Reinstall the AI runtime now?",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Ok,
            )
            if choice == QMessageBox.StandardButton.Ok:
                status = self.managed_ai_runtime_status()
                self.start_ai_runtime_install(
                    status.preferred_variant,
                    force=True,
                    include_torch=bool(status.torch_installed_variants),
                )
                return
        self.handle_ai_readiness_finished(results, "Repair AI", "Repair finished.")

    def handle_ai_repair_failed(self, message: str) -> None:
        self._notify_status()
        self._active_ai_repair_task = None
        self.set_ai_setup_busy(None)
        self._window._inspector.update_action_states()
        QMessageBox.warning(self._window, "Repair AI", message)
        self._window.statusBar().showMessage("AI repair failed.")

    def copy_ai_diagnostics(self) -> None:
        """Put a redacted support bundle on the clipboard and save it to disk."""
        from .ai_health import ai_health

        service = ai_health()
        results = self._last_ai_readiness_results or None
        text = service.diagnostics_text(results)
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text, mode=clipboard.Mode.Clipboard)
        try:
            path = service.write_diagnostics(results)
        except OSError as exc:
            self._window.statusBar().showMessage(f"Diagnostics copied, but could not be saved: {exc}")
            return
        self._window.statusBar().showMessage(f"AI diagnostics copied and saved to {path}")

    def uninstall_ai_components(self) -> None:
        if self._window._active_ai_model_task is not None or self._window._active_ai_runtime_task is not None:
            self._window.statusBar().showMessage("An AI component task is already running.")
            return
        components = self.collect_uninstallable_ai_components()
        if not components:
            QMessageBox.information(
                self._window,
                "Uninstall AI Components",
                "No AI runtime or model files are currently installed.",
            )
            return

        dialog = QDialog(self._window)
        dialog.setWindowTitle("Uninstall AI Components")
        dialog.setModal(True)
        dialog.setMinimumWidth(560)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)

        intro = QLabel(
            "Select the AI runtime and model files to remove from your local cache. "
            "This frees disk space; anything removed can be reinstalled later from "
            "this menu.",
            dialog,
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        checkboxes: list[tuple[QCheckBox, str, Path, int]] = []
        for label, path, size in components:
            checkbox = QCheckBox(f"{label} — {_format_bytes(size)}", dialog)
            checkbox.setChecked(True)
            checkbox.setToolTip(str(path))
            layout.addWidget(checkbox)
            checkboxes.append((checkbox, label, path, size))

        total_label = QLabel("", dialog)
        total_label.setObjectName("mutedText")
        layout.addWidget(total_label)

        def update_total() -> None:
            total = sum(size for cb, _label, _path, size in checkboxes if cb.isChecked())
            total_label.setText(f"Selected: {_format_bytes(total)} to free")

        for checkbox, _label, _path, _size in checkboxes:
            checkbox.toggled.connect(lambda _checked=False: update_total())
        update_total()

        button_box = QDialogButtonBox(dialog)
        button_box.addButton("Uninstall", QDialogButtonBox.ButtonRole.AcceptRole)
        button_box.addButton("Cancel", QDialogButtonBox.ButtonRole.RejectRole)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._window.statusBar().showMessage("AI uninstall cancelled.")
            return

        targets = tuple(
            (label, path, size)
            for checkbox, label, path, size in checkboxes
            if checkbox.isChecked()
        )
        if not targets:
            self._window.statusBar().showMessage("No AI components selected to remove.")
            return

        confirm = QMessageBox.question(
            self._window,
            "Uninstall AI Components",
            "Permanently remove the selected AI files?\n\n"
            + "\n".join(f"• {label}" for label, _path, _size in targets),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            self._window.statusBar().showMessage("AI uninstall cancelled.")
            return

        self.run_ai_uninstall(targets)

    def run_ai_uninstall(self, targets: tuple[tuple[str, Path, int], ...]) -> None:
        progress = self._window._export_jobs.show_job_progress_dialog(
            key="ai_uninstall",
            total_steps=1,
            spec=JobSpec(
                title="Uninstall AI Components",
                preparing_label="Removing AI files…",
                running_label="Removing AI files…",
            ),
        )
        progress.setRange(0, 0)

        loop = QEventLoop()
        result: dict[str, object] = {"freed": 0, "removed": [], "failures": []}

        def on_finished(payload: object) -> None:
            freed, removed, failures = payload
            result["freed"] = freed
            result["removed"] = removed
            result["failures"] = failures
            loop.quit()

        task = AIUninstallTask(targets=targets)
        task.signals.finished.connect(on_finished, Qt.ConnectionType.QueuedConnection)
        self._window._ai_model_pool.start(task)
        loop.exec()
        self._window._export_jobs.close_job_progress_dialog("ai_uninstall")

        # Availability is filesystem-derived, so just drop the cached runtime
        # scan and refresh the action/toolbar enabled states.
        self.invalidate_ai_runtime_status_cache()
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()

        freed = int(result.get("freed", 0) or 0)
        removed = list(result.get("removed", []) or [])
        failures = list(result.get("failures", []) or [])
        if removed:
            self._window.statusBar().showMessage(
                f"Removed {len(removed)} AI component(s); freed {_format_bytes(freed)}."
            )
        if failures:
            QMessageBox.warning(
                self._window,
                "Uninstall AI Components",
                "Some items could not be fully removed:\n" + "\n".join(failures),
            )
        elif removed:
            QMessageBox.information(
                self._window,
                "Uninstall AI Components",
                "Removed:\n"
                + "\n".join(f"• {label}" for label in removed)
                + f"\n\nFreed {_format_bytes(freed)}.",
            )

    def start_ai_model_download(
        self,
        *,
        download_aiculler_clip: bool = False,
        download_aiculler_topiq: bool = False,
        download_aiculler_face: bool = False,
        download_semantic: bool = False,
        force: bool = False,
        force_aiculler_clip: bool | None = None,
        force_aiculler_topiq: bool | None = None,
        force_aiculler_face: bool | None = None,
        force_semantic: bool | None = None,
    ) -> None:
        requests: list[AIModelDownloadRequest] = []
        if download_aiculler_clip:
            requests.append(
                AIModelDownloadRequest(
                    label="CLI-Culler CLIP",
                    installation=self.managed_aiculler_clip_model_installation(),
                    force=force if force_aiculler_clip is None else force_aiculler_clip,
                )
            )
        if download_aiculler_topiq:
            requests.append(
                AIModelDownloadRequest(
                    label="TOPIQ",
                    installation=self.managed_aiculler_topiq_model_installation(),
                    force=force if force_aiculler_topiq is None else force_aiculler_topiq,
                )
            )
        if download_aiculler_face:
            requests.append(
                AIModelDownloadRequest(
                    label="InsightFace Quality",
                    installation=self.managed_aiculler_face_model_installation(),
                    force=force if force_aiculler_face is None else force_aiculler_face,
                )
            )
        if download_semantic:
            requests.append(
                AIModelDownloadRequest(
                    label="Semantic CLIP",
                    installation=self.managed_semantic_model_installation(),
                    force=force if force_semantic is None else force_semantic,
                )
            )
        if not requests:
            self._window.statusBar().showMessage("No AI models selected for download.")
            return

        task = AIModelDownloadTask(requests=tuple(requests))
        task.signals.started.connect(self.handle_ai_model_download_started, Qt.ConnectionType.QueuedConnection)
        task.signals.progress.connect(self.handle_ai_model_download_progress, Qt.ConnectionType.QueuedConnection)
        task.signals.finished.connect(self.handle_ai_model_download_finished, Qt.ConnectionType.QueuedConnection)
        task.signals.failed.connect(self.handle_ai_model_download_failed, Qt.ConnectionType.QueuedConnection)
        self._window._active_ai_model_task = task
        self.set_ai_setup_busy("Downloading AI culling models...")
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()
        self._window.statusBar().showMessage("Starting AI model download...")
        self._window._ai_model_pool.start(task)

    def handle_ai_model_download_started(self, install_dir: str) -> None:
        del install_dir
        self.set_ai_setup_busy("Downloading AI culling models...")
        self._window.statusBar().showMessage("Downloading AI culling models...")

    def handle_ai_model_download_progress(self, filename: str, current: int, total: int) -> None:
        del filename, current, total
        self.set_ai_setup_busy("Downloading AI culling models...")

    def handle_ai_model_download_finished(self, install_dir: str) -> None:
        self._notify_status()
        self._window._active_ai_model_task = None
        self.invalidate_ai_runtime_status_cache()
        self.set_ai_setup_busy(None)
        self.refresh_ai_runtime_preferences()
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()
        self.start_ai_capability_bundles(
            title="AI Setup",
            context="The AI runtime and culling models were installed.",
        )

    def handle_ai_model_download_failed(self, message: str) -> None:
        self._notify_status()
        self._window._active_ai_model_task = None
        self.set_ai_setup_busy(None)
        self.refresh_ai_runtime_preferences()
        self._window._inspector.update_action_states()
        self._window._ai_run.update_ai_toolbar_state()
        QMessageBox.warning(self._window, "AI Model Download", f"Could not download the AI model.\n\n{message}")
        self._window.statusBar().showMessage("AI model download failed.")
