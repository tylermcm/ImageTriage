"""What the AI Center knows and does: is the AI runtime installed, is there a graphics card, is the Visual C++ runtime new enough,
and which of the three models the cull uses (the face landmarker for the eyes-closed check, the MANIQA quality ranker and the Q-ReAlign appeal ranker)
are on disk. It installs, repairs and removes them with the shared code:

* detection: `ai_runtime_packages.load_ai_runtime_installation_status`, `ai_platform.nvidia_gpu_name` and `vc_runtime_version`, and
  `ai_model_store.bundle_status` (size, pinned revision and file digests, not just "the folder exists");
* the runtime: the same installer process the Widgets "Set Up AI" runs (`tasks.ai_tasks.AIRuntimeInstallTask`, `packaging/ai_runtime_installer.py`);
* the models: `ai_model_store.install_bundle`, `repair_bundle` and `uninstall_bundle` (hash checked, atomic), with cancel. A model download
  that is cancelled or interrupted is kept and the next try continues from it (`ai_model_store`: `Range` with `If-Range`, verified before use);
  the runtime's own packages are fetched by the installer process, which starts them again;
* progress: `ai_setup_progress.SetupProgress`, one honest percent for the whole run;
* does it work: `ai_health.probe_runtime` ("Test runtime"). Detection above reads files, so until that has run in this session the
  runtime is "installed, not tested". The test starts the runtime's interpreter, imports its libraries and runs a small tensor
  operation on the device ranking would use, and says which device really ran it. Its result is kept for the session under the
  identity of the runtime it ran in, and dropped when that runtime is installed, repaired or replaced.

Everything slow runs on a worker thread; the GUI only ever reads `state()`. `ops` is what touches the machine, so the tests hand in a fake and
nothing is downloaded. See docs/qml_unwired.md, section "Cull window and AI Center".
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from ..ai_setup_plan import format_mb

# the models the cull shows, and the bundle each one is (cascade/rankers/bundles.py, ai_manifest.py). "search" is what semantic search and the
# People pre-filter read photos with; "people" is the recognizer that groups faces. Both are installed here, never from inside a run.
MODELS = {"face": "faces_blink", "quality": "maniqa", "appeal": "qrealign", "search": "tinyclip", "people": "faces_identity"}
STATE_WORDS = {"ready": "Installed", "missing": "Not installed", "partial": "Incomplete", "corrupt": "Damaged", "stale": "Out of date"}


class RealOps:
    """Everything that looks at or changes this PC. Slow: only ever called from a worker thread."""

    def __init__(self) -> None:
        self._task = None
        self._size: tuple[tuple, int] | None = None  # (what was installed, its bytes on disk): walking the runtime is slow, once per install

    # -- looking --------------------------------------------------------------------------------------------------------------
    def detect(self) -> dict:
        from .. import ai_platform
        from ..ai_manifest import MODEL_BUNDLES
        from ..ai_model_store import bundle_partial_bytes, bundle_status
        from ..ai_runtime_packages import AI_RUNTIME_GPU_VARIANT, load_ai_runtime_installation_status
        from ..cascade.rankers import register_bundles

        register_bundles()
        status = load_ai_runtime_installation_status()
        version = ai_platform.vc_runtime_version()
        models = {}
        for key, bundle in MODELS.items():
            found = bundle_status(bundle)
            models[key] = {"state": found.state, "mb": MODEL_BUNDLES[bundle].approx_mb, "problem": "" if found.is_ready else found.describe(),
                           "kept": 0 if found.is_ready else bundle_partial_bytes(bundle)}     # bytes of an interrupted download, continued by the next try
        try:
            from ..ai_health import runtime_identity

            identity = runtime_identity()
        except Exception:                           # which runtime would start cannot be told: a kept test result is then not shown
            identity = ""
        if status.installed_variants:
            stamp = (identity, tuple(status.installed_variants), str(status.directories.root))
            if self._size is None or self._size[0] != stamp:
                from ..ai_runtime_packages import directory_size_bytes

                self._size = (stamp, directory_size_bytes(status.directories.root))
            runtime_bytes = self._size[1]
        else:
            self._size, runtime_bytes = None, 0
        return {
            "identity": identity, "runtimeBytes": runtime_bytes,
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

    def probe_runtime(self, cancelled) -> dict:
        """Start the managed runtime and make it compute (seconds). `cancelled` is polled; the probe process is ended when it says so."""
        from ..ai_health import probe_runtime

        return probe_runtime("auto", cancel=cancelled)

    def close_workers(self) -> None:
        """End the helper processes this app keeps warm on the managed runtime, so its files are not in use when they are deleted.
        The mask engine host is the one that outlives its job; ranker and face workers end with their run, and a run in progress
        is refused before this is reached (`CullModel.removeRuntime`)."""
        from ..mask_engine_service import shutdown_mask_engine

        shutdown_mask_engine()

    def remove_runtime(self) -> dict:
        """Delete the managed runtime with the installer's own code (`ai_runtime_packages.remove_ai_runtime`). Models are elsewhere
        (`ai_model_store`) and are not touched. Returns what was freed and what could not be deleted."""
        from ..ai_health import invalidate_ai_health
        from ..ai_runtime_packages import remove_ai_runtime

        report = remove_ai_runtime()
        self._size = None
        invalidate_ai_health()                       # readiness answers computed for the removed runtime are not kept
        return {"existed": report.existed, "freed": report.freed_bytes, "remaining": list(report.remaining),
                "remainingCount": report.remaining_count, "remainingBytes": report.remaining_bytes}

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

    def forget_gpu(self) -> None:
        """Ask the driver for the graphics card again on the next look, instead of answering from what was found earlier."""
        from .. import ai_platform

        ai_platform.forget_gpu_name()

    def friendly(self, message: str) -> str:
        """A failure told in plain words: a Windows DLL failure gets the Visual C++ instruction."""
        from ..ai_platform import dll_failure_hint

        hint = dll_failure_hint(message)
        return f"{message}\n\n{hint}" if hint and hint not in message else message


class AiCenterBackend(QObject):
    changed = Signal()
    _detected = Signal(dict)
    _event = Signal(str, object)                     # from a worker thread: ("progress", {...}) or ("end", {...})

    PROBE_FRESH_S = 120.0                            # a test this recent is not repeated before a run

    def __init__(self, ops=None, parent: QObject | None = None, *, auto_test: bool = True) -> None:
        super().__init__(parent)
        self._ops = ops or RealOps()
        # The runtime is tested without being asked: once when it is first seen installed, and again before a run when the last
        # test is no longer recent. Nobody should have to press a button to learn that ranking will not work.
        self._auto_test = auto_test
        self._auto_identity: str | None = None       # the runtime the unasked test was already started for
        self._probe_at = 0.0
        self._after_probe: list = []                 # what waits for the test before a run
        self._raw: dict | None = None                # the last look at the machine
        self._job: dict | None = None                # the one install, repair or removal running now
        self._errors: dict[str, str] = {}            # "runtime" or a model key -> why it failed, until the next try
        self._probe: dict | None = None              # what the last "Test runtime" of this session found, for the runtime it ran in
        self._pending_removed: dict | None = None
        self._removed: dict | None = None            # what the last "Remove runtime" of this session freed, until something is installed
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
        if self._probe is not None and str(raw.get("identity", "")) != str(self._probe.get("identity", "")):
            self._probe = None                       # another runtime is installed now (or none): the test was of the old one
        self.changed.emit()
        identity = str(raw.get("identity", ""))
        if self._auto_test and self._probe is None and self._job is None and self._runtime_installed() and self._auto_identity != identity:
            self._auto_identity = identity           # once per runtime: a cancelled or failed test is not started over and over
            self.testRuntime()

    def _runtime_installed(self) -> bool:
        raw = self._raw
        return (raw is not None and "failed" not in raw and bool(raw.get("installed")) and bool(raw.get("torch"))
                and not self._errors.get("runtime") and callable(getattr(self._ops, "probe_runtime", None)))

    def needs_test(self) -> bool:
        """Whether a run should wait for a test first: the runtime is installed and was not tested recently."""
        if not self._auto_test or not self._runtime_installed():
            return False
        return self._probe is None or time.monotonic() - self._probe_at > self.PROBE_FRESH_S

    def ensure_tested(self, then) -> None:
        """Call ``then()`` once the runtime has a recent test: at once when it has, otherwise after testing it now. ``then`` is
        called whatever the test found (and when it could not be started): the caller reads ``state()`` for the result."""
        if not self.needs_test():
            then()
            return
        self._after_probe.append(then)
        probing = self._job is not None and self._job["kind"] == "probe"
        if not probing and not self._start_probe():
            self._release_waiting()                  # an install is running: nothing can be tested now, and the run reports for itself

    def _release_waiting(self) -> None:
        waiting, self._after_probe = self._after_probe, []
        for then in waiting:
            then()

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
        removing = job is not None and job["kind"] == "remove"
        error = self._errors.get("runtime", "")
        if runtime_job:
            rt = "installing"
        elif not checked or removing:
            rt = "checking"                          # while it is being deleted nothing is offered or claimed; `removing` says why
        elif failed:
            rt, error = "error", f"Could not look at the AI runtime: {failed}"
        elif error:
            rt = "error"
        elif installed and not torch:
            rt, error = "error", "The runtime is installed without PyTorch, which ranking needs. Repair adds it."
        elif installed:
            # Registry age is advisory: it does not establish an import failure.
            rt = "ready"
        else:
            rt = "missing"
        probing = job is not None and job["kind"] == "probe"
        probe = self._probe if rt in ("ready", "error") and not runtime_job and not failed else None
        # Only what a test really showed changes what is offered: a graphics card that PyTorch could not use is not offered for
        # ranking. A test that failed outright is shown, and the workers still report their own failures when they are run.
        gpu_refused = probe is not None and bool(probe.get("ok")) and not probe.get("honoured")
        models = {}
        for key, found in raw["models"].items():
            working = job is not None and job["kind"] == "model" and job["key"] == key
            models[key] = {"state": found["state"], "mb": found["mb"], "problem": found.get("problem", ""), "kept": int(found.get("kept", 0) or 0), "busy": working,
                           "pct": job["pct"] if working else 0, "step": job["step"] if working else "", "verb": job["verb"] if working else "",
                           "error": self._errors.get(key, "")}
        return {
            "checked": checked, "rt": rt, "pct": job["pct"] if runtime_job else 0, "step": job["step"] if runtime_job else "", "error": error,
            "variant": "gpu" if raw["gpuBuild"] else ("cpu" if installed else ""), "python": raw["python"],
            "gpuName": raw["gpu"], "gpuFound": bool(raw["gpu"]), "gpuUsable": bool(raw["gpu"]) and raw["gpuBuild"] and not gpu_refused,
            "gpuBuild": bool(raw["gpuBuild"]), "tested": probe is not None, "probe": dict(probe) if probe is not None else None,
            "probing": probing, "probeStep": job["step"] if probing else "", "canTest": callable(getattr(self._ops, "probe_runtime", None)),
            "vcOld": bool(raw["vcOld"]), "vcFound": raw["vc"], "models": models, "busy": job is not None,
            "removing": removing, "removeStep": job["step"] if removing else "", "removePct": job["pct"] if removing else 0,
            "runtimeBytes": int(raw.get("runtimeBytes", 0) or 0),
            "canRemove": bool(installed) and callable(getattr(self._ops, "remove_runtime", None)), "removed": self._removed,
        }

    # -- doing things ---------------------------------------------------------------------------------------------------------
    @Slot()
    def cancel(self) -> None:
        """Stop what is running: the installer process is ended and a download stops at its next chunk. The part of a model that has
        arrived is kept for the next try; the runtime's packages are not."""
        if self._job is not None:
            if self._job["kind"] == "remove":
                return                               # deleting is not stopped half way: a partly deleted runtime is of no use to anyone
            self._cancel.set()
            cancel_runtime = getattr(self._ops, "cancel_runtime", None)
            if callable(cancel_runtime):
                cancel_runtime()
            self._job["step"] = "Stopping…"
            self.changed.emit()

    @Slot()
    def recheck(self) -> None:
        """"Check again": forget the last failure and look at the machine again (a Visual C++ update, a new driver, a card that was added)."""
        self._errors.pop("runtime", None)
        forget_gpu = getattr(self._ops, "forget_gpu", None)
        if callable(forget_gpu):
            forget_gpu()
        self.refresh()

    def _start(self, kind: str, key: str, verb: str, work) -> bool:
        if self._job is not None:
            return False                              # one install at a time, as the backend does
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
        elif kind == "probe":
            self._probe = dict(payload)
            self._probe_at = time.monotonic()
        elif kind == "removed":
            self._pending_removed = dict(payload)
        elif kind == "end" and job is not None:
            self._job = None
            if job["kind"] in ("runtime", "remove"):
                self._probe = None                   # installed, repaired or removed (or stopped half way): what was tested is not what is there now
                self._auto_identity = None           # and what is there now is tested again without being asked
                self._removed = None
            if job["kind"] == "remove" and not payload.get("error"):
                self._removed = self._pending_removed
            self._pending_removed = None
            if payload.get("error"):
                self._errors[job["key"]] = payload["error"]
        self.changed.emit()
        if kind == "end":
            if job is not None and job["kind"] == "probe":
                self._release_waiting()
            self.refresh()

    def _progress(self, progress, word: str = "") -> None:
        text = f"{word or progress.step_label()} · {progress.percent()}%"
        eta = progress.eta_text()
        self._event.emit("progress", {"pct": progress.percent(), "step": text + (f" · {eta}" if eta else "")})

    # -- does it work -----------------------------------------------------------------------------------------------------------
    @Slot()
    def testRuntime(self) -> None:
        """"Test runtime": run the managed runtime for real, on a worker thread, and keep what it reports. `cancel` stops it."""
        self._start_probe()

    def _start_probe(self) -> bool:
        probe = getattr(self._ops, "probe_runtime", None)
        if not callable(probe):
            return False

        def work(job: dict) -> None:
            self._event.emit("progress", {"pct": 0, "step": "Starting the AI runtime…"})
            try:
                result = probe(self._cancel.is_set)
            except Exception as exc:
                if self._cancel.is_set() or type(exc).__name__ == "ProbeCancelled":
                    raise
                # The test itself could not be run. That is a result too, and is shown as one rather than lost.
                result = {"ok": False, "honoured": False, "requested": "", "device": "", "deviceName": "", "torch": "",
                          "identity": str((self._raw or {}).get("identity", "")), "category": "probe_error",
                          "message": f"The test could not be run: {exc}", "detail": ""}
            if not self._cancel.is_set():
                self._event.emit("probe", result)

        return self._start("probe", "probe", "Testing", work)

    # -- the runtime ----------------------------------------------------------------------------------------------------------
    def variant(self, repair: bool = False) -> str:
        """The runtime build to install: what is installed decides, else a graphics card does (as the Widgets `ai_setup_hardware`).
        A repair of a CPU-only install on a PC that has a graphics card installs the graphics card build: that is what the AI Center
        tells the person Repair is for."""
        raw = self._raw or {}
        installed = list(raw.get("installed", []))
        if raw.get("gpuBuild"):
            return "gpu"
        if installed == ["cpu"]:
            return "gpu" if repair and raw.get("gpu") else "cpu"
        return "gpu" if raw.get("gpu") else "cpu"

    def runtime_plan(self, repair: bool = False) -> dict:
        """What Set up AI (or, with `repair`, Repair) would download: the build, and its size in MB with the face model."""
        variant = self.variant(repair)
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
        variant = self.variant(force)
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

    def can_remove_runtime(self) -> str:
        """"" when the runtime can be removed now, else why not, in words for the person."""
        if not callable(getattr(self._ops, "remove_runtime", None)):
            return "This build cannot remove the AI runtime."
        if self._job is not None:
            what = {"runtime": "The AI runtime is being set up", "remove": "The AI runtime is already being removed",
                    "probe": "The AI runtime is being tested", "model": "A model is being installed or removed"}.get(self._job["kind"], "Something is still running")
            return f"{what}. Wait for it to finish, or cancel it, then remove the runtime."
        if not (self._raw or {}).get("installed"):
            return "There is no AI runtime to remove."
        return ""

    @Slot()
    def removeRuntime(self) -> None:
        """Delete the managed runtime, off the UI thread: the warm helper processes are ended first so their files are free, then
        the runtime's folders go through the installer's own removal. What could not be deleted is reported, not hidden."""
        if self.can_remove_runtime():
            return

        def work(job: dict) -> None:
            self._event.emit("progress", {"pct": 10, "step": "Closing the AI helpers…"})
            close = getattr(self._ops, "close_workers", None)
            if callable(close):
                try:
                    close()
                except Exception:                    # a helper that will not end shows up as files in use, which is reported below
                    pass
            self._event.emit("progress", {"pct": 35, "step": "Removing the files…"})
            report = self._ops.remove_runtime() or {}
            count = int(report.get("remainingCount", 0) or 0)
            if count:
                names = [str(name) for name in report.get("remaining", [])]
                more = count - len(names)
                listed = ", ".join(names) + (f" and {more} more" if more > 0 else "")
                raise RuntimeError(
                    f"The AI runtime was only partly removed: {count} file{'s' if count != 1 else ''} "
                    f"({format_mb(max(1, round(int(report.get('remainingBytes', 0) or 0) / 1e6)))}) could not be deleted, "
                    f"most likely because another program still has {'them' if count != 1 else 'it'} open: {listed}. "
                    "Close other Image Triage windows and anything else using it, then choose Remove again. "
                    "What is left is not used: the runtime counts as not installed.")
            self._event.emit("removed", {"freed": int(report.get("freed", 0) or 0)})
            self._event.emit("progress", {"pct": 100, "step": "Removed"})

        self._start("remove", "runtime", "Removing", work)

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
