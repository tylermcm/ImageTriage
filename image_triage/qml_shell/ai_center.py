"""What the AI Center knows and does: is the AI runtime installed and does it start, is there a graphics card, is the Visual C++ runtime new enough,
and which of the three models the cull uses (the face landmarker for the eyes-closed check, the MANIQA quality ranker and the Q-ReAlign appeal ranker)
are on disk. It installs, repairs and removes them with the Widgets app's own code:

* detection: `ai_runtime_packages.load_ai_runtime_installation_status`, `ai_platform.nvidia_gpu_name` and `vc_runtime_version`, and
  `ai_model_store.bundle_status` (size, pinned revision and file digests, not just "the folder exists");
* the runtime: the same installer process the Widgets "Set Up AI" runs (`tasks.ai_tasks.AIRuntimeInstallTask`, `packaging/ai_runtime_installer.py`);
* the models: `ai_model_store.install_bundle`, `repair_bundle` and `uninstall_bundle` (hash checked, resumable, atomic), with cancel;
* progress: `ai_setup_progress.SetupProgress`, one honest percent for the whole run.

Everything slow runs on a worker thread; the GUI only ever reads `state()`. `ops` is what touches the machine, so the tests hand in a fake and
nothing is downloaded. See docs/qml_unwired.md, section "Cull window and AI Center".
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..ai_setup_plan import format_mb

# the three models the cull shows, and the bundle each one is (cascade/rankers/bundles.py)
MODELS = {"face": "faces_blink", "quality": "maniqa", "appeal": "qrealign"}
STATE_WORDS = {"ready": "Installed", "missing": "Not installed", "partial": "Incomplete", "corrupt": "Damaged", "stale": "Out of date"}


class RealOps:
    """Everything that looks at or changes this PC. Slow: only ever called from a worker thread."""

    def __init__(self) -> None:
        self._task = None

    # -- looking --------------------------------------------------------------------------------------------------------------
    def detect(self) -> dict:
        from .. import ai_platform
        from ..ai_manifest import MODEL_BUNDLES
        from ..ai_model_store import bundle_status
        from ..ai_runtime_packages import AI_RUNTIME_GPU_VARIANT, load_ai_runtime_installation_status
        from ..cascade.rankers import register_bundles

        register_bundles()
        status = load_ai_runtime_installation_status()
        version = ai_platform.vc_runtime_version()
        models = {}
        for key, bundle in MODELS.items():
            found = bundle_status(bundle)
            models[key] = {"state": found.state, "mb": MODEL_BUNDLES[bundle].approx_mb, "problem": "" if found.is_ready else found.describe()}
        return {
            "installed": list(status.installed_variants), "torch": list(status.torch_installed_variants), "gpuBuild": AI_RUNTIME_GPU_VARIANT in status.installed_variants,
            "gpu": ai_platform.nvidia_gpu_name() or "", "vc": ".".join(str(part) for part in version) if version else "",
            "vcOld": bool(version and version < ai_platform.RECOMMENDED_VC_RUNTIME), "models": models, "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        }

    def runtime_mb(self, variant: str) -> int:
        from ..ai_runtime_packages import estimate_ai_runtime_download_size_mb

        return estimate_ai_runtime_download_size_mb(variant, include_torch=True)

    # -- changing -------------------------------------------------------------------------------------------------------------
    def install_runtime(self, variant: str, force: bool, on_line, cancelled) -> None:
        """Run the installer process (the one Set Up AI runs) and pass its output on line by line. Raises with its message when it fails."""
        from ..ai_runtime_packages import load_ai_runtime_installation_status
        from ..tasks.ai_tasks import AIRuntimeInstallTask

        install_root = load_ai_runtime_installation_status().directories.root
        workspace_root = Path(__file__).resolve().parents[2]
        if getattr(sys, "frozen", False):
            runtime_root = Path(sys.executable).resolve().parent
            command = [str(runtime_root / ("ai_runtime_installer.exe" if sys.platform == "win32" else "ai_runtime_installer")), "install", "--variant", variant]
            cwd = runtime_root
        else:
            command = [sys.executable, str(workspace_root / "packaging" / "ai_runtime_installer.py"), "install", "--variant", variant]
            cwd = workspace_root
        command += ["--install-root", str(install_root)]
        if force:
            command.append("--force")
        task = AIRuntimeInstallTask(command=command, cwd=cwd, install_root=install_root, variant_choice=variant)
        failure: list[str] = []
        task.signals.progress.connect(on_line)
        task.signals.failed.connect(failure.append)
        self._task = task
        if cancelled():
            task.cancel()
        task.run()                                   # synchronously: this is already a worker thread
        self._task = None
        if failure:
            raise RuntimeError(failure[0])

    def cancel_runtime(self) -> None:
        task = self._task
        if task is not None:
            task.cancel()

    def install_bundle(self, key: str, progress, cancelled) -> None:
        from ..ai_model_store import install_bundle

        install_bundle(MODELS.get(key, key), progress_callback=progress, cancel_check=cancelled)

    def repair_bundle(self, key: str, progress, cancelled) -> None:
        from ..ai_model_store import repair_bundle

        repair_bundle(MODELS.get(key, key), progress_callback=progress, cancel_check=cancelled)

    def remove_bundle(self, key: str) -> None:
        from ..ai_model_store import uninstall_bundle

        uninstall_bundle(MODELS.get(key, key))

    def bundle_sizes(self, key: str) -> dict[str, int]:
        """File name -> bytes, for turning a download's progress into one fraction of the whole bundle."""
        from ..ai_manifest import bundle_digests

        return {name: digest.size for name, digest in bundle_digests(MODELS.get(key, key)).items()}

    def bundle_mb(self, key: str) -> int:
        from ..ai_manifest import MODEL_BUNDLES

        return MODEL_BUNDLES[MODELS.get(key, key)].approx_mb

    def friendly(self, message: str) -> str:
        """A failure told in plain words: a Windows DLL failure gets the Visual C++ instruction."""
        from ..ai_platform import dll_failure_hint

        return dll_failure_hint(message) or message


class AiCenterBackend(QObject):
    changed = Signal()
    _detected = Signal(dict)
    _event = Signal(str, object)                     # from a worker thread: ("progress", {...}) or ("end", {...})

    def __init__(self, ops=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._ops = ops or RealOps()
        self._raw: dict | None = None                # the last look at the machine
        self._job: dict | None = None                # the one install, repair or removal running now
        self._errors: dict[str, str] = {}            # "runtime" or a model key -> why it failed, until the next try
        self._cancel = threading.Event()
        self._detecting = False
        self._detected.connect(self._apply_detected)
        self._event.connect(self._on_event)

    # -- looking at the machine -----------------------------------------------------------------------------------------------
    @Slot()
    def refresh(self) -> None:
        """Look again, on a worker thread (it asks `nvidia-smi` and reads every model's files)."""
        if self._detecting:
            return
        self._detecting = True

        def work() -> None:
            try:
                raw = self._ops.detect()
            except Exception as exc:                # a look that fails is shown as "could not check", never a crash
                raw = {"failed": str(exc)}
            self._detected.emit(raw)

        threading.Thread(target=work, name="ai-center-detect", daemon=True).start()

    def _apply_detected(self, raw: dict) -> None:
        self._detecting = False
        self._raw = raw
        self.changed.emit()

    # -- what the window shows ------------------------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._job is not None

    def state(self) -> dict:
        raw, job = self._raw, self._job
        if raw is None or "failed" in raw:
            checked = raw is not None
            models = {key: {"state": "missing", "mb": 0, "problem": ""} for key in MODELS}
            raw = {"installed": [], "torch": [], "gpuBuild": False, "gpu": "", "vc": "", "vcOld": False, "models": models, "python": f"{sys.version_info.major}.{sys.version_info.minor}"}
            failed = self._raw["failed"] if checked else ""
        else:
            checked, failed = True, ""
        installed, torch = raw["installed"], raw["torch"]
        runtime_job = job is not None and job["kind"] == "runtime"
        error = self._errors.get("runtime", "")
        if runtime_job:
            rt = "installing"
        elif not checked:
            rt = "checking"
        elif failed:
            rt, error = "error", f"Could not look at the AI runtime: {failed}"
        elif error:
            rt = "error"
        elif installed and torch and raw["vcOld"]:
            rt = "error"
        elif installed and not torch:
            rt, error = "error", "The runtime is installed without PyTorch, which ranking needs. Repair adds it."
        elif installed:
            rt = "ready"
        else:
            rt = "missing"
        models = {}
        for key, found in raw["models"].items():
            working = job is not None and job["kind"] == "model" and job["key"] == key
            models[key] = {"state": found["state"], "mb": found["mb"], "problem": found.get("problem", ""), "busy": working,
                           "pct": job["pct"] if working else 0, "step": job["step"] if working else "", "verb": job["verb"] if working else "",
                           "error": self._errors.get(key, "")}
        return {
            "checked": checked, "rt": rt, "pct": job["pct"] if runtime_job else 0, "step": job["step"] if runtime_job else "", "error": error,
            "variant": "gpu" if raw["gpuBuild"] else ("cpu" if installed else ""), "python": raw["python"],
            "gpuName": raw["gpu"], "gpuFound": bool(raw["gpu"]), "gpuUsable": bool(raw["gpu"]) and raw["gpuBuild"],
            "vcOld": bool(raw["vcOld"]), "vcFound": raw["vc"], "models": models, "busy": job is not None,
        }

    # -- doing things ---------------------------------------------------------------------------------------------------------
    @Slot()
    def cancel(self) -> None:
        """Stop what is running: the installer process is ended, a download stops at its next chunk, and what was already downloaded is kept."""
        if self._job is not None:
            self._cancel.set()
            cancel_runtime = getattr(self._ops, "cancel_runtime", None)
            if callable(cancel_runtime):
                cancel_runtime()
            self._job["step"] = "Stopping…"
            self.changed.emit()

    @Slot()
    def recheck(self) -> None:
        """"Check again": forget the last failure and look at the machine again (a Visual C++ update, say)."""
        self._errors.pop("runtime", None)
        self.refresh()

    def _start(self, kind: str, key: str, verb: str, work) -> bool:
        if self._job is not None:
            return False                              # one install at a time, as the Widgets app does
        self._cancel.clear()
        self._errors.pop(key, None)
        job = {"kind": kind, "key": key, "verb": verb, "pct": 0, "step": "Starting…"}
        self._job = job
        self.changed.emit()

        def run() -> None:
            try:
                work(job)
                self._event.emit("end", {})
            except Exception as exc:
                if type(exc).__name__ == "ModelInstallCancelled" or self._cancel.is_set():
                    self._event.emit("end", {"cancelled": True})
                else:
                    self._event.emit("end", {"error": self._ops.friendly(str(exc)) if hasattr(self._ops, "friendly") else str(exc)})

        threading.Thread(target=run, name=f"ai-center-{kind}", daemon=True).start()
        return True

    def _on_event(self, kind: str, payload: dict) -> None:
        job = self._job
        if kind == "progress" and job is not None:
            job["pct"], job["step"] = int(payload.get("pct", job["pct"])), str(payload.get("step", job["step"]))
        elif kind == "end" and job is not None:
            self._job = None
            if payload.get("error"):
                self._errors[job["key"]] = payload["error"]
        self.changed.emit()
        if kind == "end":
            self.refresh()

    def _progress(self, progress, word: str = "") -> None:
        text = f"{word or progress.step_label()} · {progress.percent()}%"
        eta = progress.eta_text()
        self._event.emit("progress", {"pct": progress.percent(), "step": text + (f" · {eta}" if eta else "")})

    # -- the runtime ----------------------------------------------------------------------------------------------------------
    def variant(self) -> str:
        """The runtime build to install: what is installed decides, else a graphics card does (as the Widgets `ai_setup_hardware`)."""
        raw = self._raw or {}
        installed = list(raw.get("installed", []))
        if raw.get("gpuBuild"):
            return "gpu"
        if installed == ["cpu"]:
            return "cpu"
        return "gpu" if raw.get("gpu") else "cpu"

    def runtime_plan(self) -> dict:
        """What Set up AI would download: the build, and its size in MB with the face model."""
        variant = self.variant()
        return {"variant": variant, "mb": self._ops.runtime_mb(variant) + self._ops.bundle_mb("face")}

    @Slot()
    def setupRuntime(self) -> None:
        self._setup(False)

    @Slot()
    def repairRuntime(self) -> None:
        self._setup(True)

    def _setup(self, force: bool) -> None:
        """Install (or, with `force`, repair) the AI runtime, then the face model the eyes check needs."""
        from ..ai_setup_progress import SetupProgress

        raw = self._raw or {}
        variant = self.variant()
        runtime_missing = not raw.get("installed") or not raw.get("torch") or force
        face_missing = (raw.get("models", {}).get("face", {}).get("state", "missing")) != "ready"

        def work(job: dict) -> None:
            progress = SetupProgress(runtime_mb=self._ops.runtime_mb(variant) if runtime_missing else 0,
                                     bundles={"face": self._ops.bundle_mb("face")} if face_missing else {})
            self._progress(progress)

            def on_line(text: str) -> None:
                progress.runtime_line(text)
                self._progress(progress)

            if runtime_missing:
                self._ops.install_runtime(variant, force, on_line, self._cancel.is_set)
                progress.runtime_done()
                self._progress(progress)
            if face_missing and not self._cancel.is_set():
                self._ops.install_bundle("face", self._bundle_progress("face", progress), self._cancel.is_set)
                progress.bundle_done("face")
            progress.finish()
            self._progress(progress)

        self._start("runtime", "runtime", "Setting up", work)

    # -- the models -----------------------------------------------------------------------------------------------------------
    def _bundle_progress(self, key: str, progress, word: str = ""):
        sizes = self._ops.bundle_sizes(key)
        total = sum(sizes.values()) or 1
        done_files: dict[str, int] = {}

        def callback(filename: str, current: int, size: int) -> None:
            done_files[filename] = current
            progress.bundle_fraction(key, min(1.0, sum(done_files.values()) / total))
            self._progress(progress, word)

        return callback

    def _model_job(self, key: str, verb: str, action: str) -> None:
        from ..ai_setup_progress import SetupProgress

        def work(job: dict) -> None:
            if action == "remove":
                self._event.emit("progress", {"pct": 50, "step": "Removing the files…"})
                self._ops.remove_bundle(key)
                return
            progress = SetupProgress(runtime_mb=0, bundles={key: self._ops.bundle_mb(key)})
            self._progress(progress, "Downloading")
            callback = self._bundle_progress(key, progress, "Downloading")
            if action == "repair":
                self._ops.repair_bundle(key, callback, self._cancel.is_set)
            else:
                self._ops.install_bundle(key, callback, self._cancel.is_set)
            progress.bundle_done(key)
            progress.finish()
            self._progress(progress, "Checking that it works")

        self._start("model", key, verb, work)

    @Slot(str)
    def installModel(self, key: str) -> None:
        if key in MODELS:
            self._model_job(key, "Installing", "install")

    @Slot(str)
    def repairModel(self, key: str) -> None:
        if key in MODELS:
            self._model_job(key, "Repairing", "repair")

    @Slot(str)
    def removeModel(self, key: str) -> None:
        if key in MODELS:
            self._model_job(key, "Removing", "remove")

    @Slot(str)
    def modelAction(self, key: str) -> None:
        """The model row's button: install what is missing, repair what is damaged, and remove a ranker (the face model is only repaired)."""
        found = (self._raw or {}).get("models", {}).get(key, {}).get("state", "missing")
        if found == "ready":
            self.repairModel(key) if key == "face" else self.removeModel(key)
        elif found == "missing":
            self.installModel(key)
        else:
            self.repairModel(key)

    def size_text(self, mb: int) -> str:
        return format_mb(mb)
