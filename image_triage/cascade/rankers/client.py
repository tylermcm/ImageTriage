"""The main-process side of a ranker: a ``Scorer`` (see ``cascade.pipeline``) backed by a worker subprocess.

Never imports torch. The worker (``worker.py``) is started lazily in the managed AI runtime, kept alive between calls (loading the
model costs seconds) and stopped by ``close()``. Cancellation kills the worker, because a GPU forward pass cannot be interrupted.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from . import protocol
from .bundles import ensure_aux_weights, ensure_weights
from .errors import RankerCancelled, RankerError, RankerUnavailable, RankerWeightsMissing, RankerWorkerError
from .spec import BLINK, FACES, MANIQA, QREALIGN, RankerSpec

CancelCheck = Callable[[], bool]
PathResolver = Callable[[str], Path]

POLL_SECONDS = 0.05
READY_TIMEOUT_SECONDS = 900.0  # first start may import torch and read a 2 GB model from a cold disk
BATCH_TIMEOUT_SECONDS = 600.0  # silence from the worker during a request; every scored frame resets it
WORKER_MODULE = "image_triage.cascade.rankers.worker"


@dataclass(frozen=True)
class WorkerLaunch:
    spec: RankerSpec
    model_dir: Path
    device: str  # "auto", "cpu", "cuda" or "cuda:N"
    aux_dir: Path | None = None  # the second bundle's directory, for a spec that has one


# A launcher turns a WorkerLaunch into a running process with text pipes (subprocess.Popen shaped). Tests substitute their own.
Launcher = Callable[[WorkerLaunch], "subprocess.Popen[str]"]


def default_launcher(launch: WorkerLaunch) -> "subprocess.Popen[str]":
    """Start the worker in the managed AI runtime that matches the requested device."""
    from ...ai_env import AIRuntimeUnavailable, build_worker_env, select_runtime

    try:
        selection = select_runtime(launch.device, require_torch=launch.spec.needs_torch)
    except AIRuntimeUnavailable as exc:
        raise RankerUnavailable(str(exc), remediation=exc.remediation) from exc
    package_root = _worker_package_root()
    env = build_worker_env(selection)
    # Managed site-packages first (build_worker_env did that), then the app's own source: the checkout, or in a frozen build the
    # plain-file tree installed beside the executable (the package itself sits in library.zip, which the managed runtime cannot read).
    parts = [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join([*parts, package_root])
    command = [
        _python_executable(), "-m", WORKER_MODULE,
        "--kind", launch.spec.kind, "--model-dir", str(launch.model_dir), "--device", selection.device,
    ]
    if launch.aux_dir is not None:
        command += ["--aux-dir", str(launch.aux_dir)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    return subprocess.Popen(
        command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags,
    )


def _worker_package_root() -> str:
    if getattr(sys, "frozen", False):
        return str(Path(sys.executable).resolve().parent / "ai_workers" / "py")
    return str(Path(__file__).resolve().parents[3])


def _python_executable() -> str:
    override = (os.environ.get("AICULLING_PYTHON", "") or "").strip()
    if override and Path(override).exists():
        return override
    runner = Path(sys.executable).resolve().parent / ("ai_python_runner.exe" if os.name == "nt" else "ai_python_runner")
    return str(runner) if runner.exists() else sys.executable


@dataclass
class _Session:
    process: "subprocess.Popen[str]"
    events: "queue.Queue[dict[str, object] | None]" = field(default_factory=queue.Queue)
    stderr_tail: list[str] = field(default_factory=list)
    device: str = ""
    precision: str = ""


class WorkerRanker:
    """A ``Scorer``: ``ranker(keys) -> {key: score}``, higher is better. Reusable across calls; call ``close()`` when done.

    ``resolve_path`` maps a frame key to the image file the worker reads (default: the key is the path).
    ``cancel_check`` is polled while waiting; when it returns True the worker is killed and ``RankerCancelled`` is raised.
    A frame the worker cannot read scores ``-inf`` (ranked last, so the pipeline still gets a score for every key) and is recorded
    in ``failures``.
    """

    def __init__(
        self,
        spec: RankerSpec,
        *,
        device: str = "auto",
        resolve_path: PathResolver | None = None,
        cancel_check: CancelCheck | None = None,
        launcher: Launcher | None = None,
        model_dir: Path | None = None,
        download: bool = True,
        download_progress: Callable[[str, int, int], None] | None = None,
        progress: Callable[[int, int], None] | None = None,
        batch_size: int | None = None,
        ready_timeout: float = READY_TIMEOUT_SECONDS,
        batch_timeout: float = BATCH_TIMEOUT_SECONDS,
    ) -> None:
        self.spec = spec
        self.device = device
        self._resolve = resolve_path or Path
        self._cancel_check = cancel_check
        self._launcher = launcher or default_launcher
        self._model_dir = model_dir
        self._download = download
        self._download_progress = download_progress
        self._progress = progress
        self._batch_size = max(1, int(spec.batch_size if batch_size is None else batch_size))
        self._ready_timeout = ready_timeout
        self._batch_timeout = batch_timeout
        self._session: _Session | None = None
        self._request_id = 0
        self.failures: dict[str, str] = {}
        self.details: dict[str, dict] = {}  # what an engine reported beyond the number (the face finder's faces), by key; cleared at the start of each call
        self.frames_scored = 0
        self.seconds_scoring = 0.0

    @property
    def seconds_per_frame(self) -> float | None:
        """Measured on this machine so far (excludes model load); feed to ``costs.estimate_cost(measured=...)``."""
        return self.seconds_scoring / self.frames_scored if self.frames_scored else None

    @property
    def active_device(self) -> str:
        return self._session.device if self._session else ""

    def __call__(self, keys: Sequence[str]) -> Mapping[str, float]:
        keys = list(keys)
        if len(set(keys)) != len(keys):
            raise ValueError("frame keys must be unique within a call")
        scores: dict[str, float] = {}
        self.details = {}
        if not keys:
            return scores
        self._raise_if_cancelled()
        session = self._ensure_session()
        started = time.perf_counter()
        try:
            for start in range(0, len(keys), self._batch_size):
                self._raise_if_cancelled()
                self._score_batch(session, keys[start:start + self._batch_size], scores, total=len(keys))
        except BaseException:
            self.close()  # never leave a half-finished request on a live worker
            raise
        self.frames_scored += len(keys)
        self.seconds_scoring += time.perf_counter() - started
        return scores

    # -- lifecycle ---------------------------------------------------------------------------------------------------

    def close(self) -> None:
        session, self._session = self._session, None
        if session is None:
            return
        process = session.process
        try:
            if process.poll() is None and process.stdin is not None:
                try:
                    process.stdin.write(protocol.encode({"op": "quit"}))
                    process.stdin.flush()
                    process.wait(timeout=2.0)
                except (OSError, ValueError, subprocess.TimeoutExpired):
                    pass
        finally:
            _kill(process)

    def __enter__(self) -> "WorkerRanker":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- internals ---------------------------------------------------------------------------------------------------

    def _raise_if_cancelled(self) -> None:
        if self._cancel_check is not None and self._cancel_check():
            self.close()
            raise RankerCancelled(f"{self.spec.name} ranking was cancelled.")

    def _ensure_session(self) -> _Session:
        if self._session is not None and self._session.process.poll() is None:
            return self._session
        self.close()
        model_dir = self._model_dir
        if model_dir is None:
            from ...ai_model_store import ModelInstallCancelled

            try:
                model_dir = ensure_weights(
                    self.spec, download=self._download,
                    progress_callback=self._download_progress, cancel_check=self._cancel_check,
                )
            except ModelInstallCancelled as exc:
                raise RankerCancelled(str(exc)) from exc
        aux_dir: Path | None = None
        if self.spec.aux_bundle_key and self._model_dir is None:
            from ...ai_model_store import ModelInstallCancelled

            try:
                aux_dir = ensure_aux_weights(
                    self.spec, download=self._download,
                    progress_callback=self._download_progress, cancel_check=self._cancel_check,
                )
            except ModelInstallCancelled as exc:
                raise RankerCancelled(str(exc)) from exc
        try:
            process = self._launcher(WorkerLaunch(self.spec, Path(model_dir), self.device, aux_dir))
        except RankerError:
            raise
        except OSError as exc:
            raise RankerUnavailable(f"The {self.spec.name} helper could not be started: {exc}") from exc
        session = _Session(process)
        self._session = session
        threading.Thread(target=_pump_stdout, args=(process, session), daemon=True).start()
        threading.Thread(target=_pump_stderr, args=(process, session), daemon=True).start()
        event = self._next_event(session, self._ready_timeout, "to start")
        if event.get("event") == "error":
            self.close()
            raise _worker_failure(self.spec, event)
        if event.get("event") != "ready":
            self.close()
            raise RankerWorkerError(f"{self.spec.name} helper sent {event!r} instead of ready.")
        session.device = str(event.get("device", ""))
        session.precision = str(event.get("precision", ""))
        return session

    def _score_batch(self, session: _Session, batch: list[str], scores: dict[str, float], *, total: int) -> None:
        self._request_id += 1
        request_id = self._request_id
        items = [{"key": key, "path": str(self._resolve(key))} for key in batch]
        try:
            assert session.process.stdin is not None
            session.process.stdin.write(protocol.encode({"op": "score", "id": request_id, "items": items}))
            session.process.stdin.flush()
        except (OSError, ValueError) as exc:
            raise self._died(session, f"The {self.spec.name} helper stopped responding ({exc}).") from exc
        pending = set(batch)
        while True:
            event = self._next_event(session, self._batch_timeout, "to score a frame")
            kind = event.get("event")
            if kind == "error":
                self.close()
                raise _worker_failure(self.spec, event)
            if event.get("id") != request_id:
                continue
            key = str(event.get("key", ""))
            if kind == "score" and key in pending:
                scores[key] = float(event["value"])  # type: ignore[arg-type]
                if isinstance(event.get("detail"), dict):
                    self.details[key] = event["detail"]  # type: ignore[assignment]
                pending.discard(key)
                self.failures.pop(key, None)
                if self._progress is not None:
                    self._progress(len(scores), total)
            elif kind == "frame_error" and key in pending:
                scores[key] = float("-inf")
                self.failures[key] = str(event.get("message", "unreadable image"))
                pending.discard(key)
                if self._progress is not None:
                    self._progress(len(scores), total)
            elif kind == "done":
                if pending:
                    self.close()
                    raise RankerWorkerError(f"{self.spec.name} returned no score for {len(pending)} frame(s).")
                return

    def _next_event(self, session: _Session, timeout: float, what: str) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        while True:
            self._raise_if_cancelled()
            try:
                event = session.events.get(timeout=POLL_SECONDS)
            except queue.Empty:
                if time.monotonic() >= deadline:
                    self.close()
                    raise RankerWorkerError(
                        f"The {self.spec.name} helper took longer than {timeout:.0f} s {what}.",
                        remediation="Try again; if it keeps happening, switch ranking off or use the GPU.",
                    )
                continue
            if event is None:
                raise self._died(session, f"The {self.spec.name} helper exited unexpectedly.")
            return event

    def _died(self, session: _Session, message: str) -> RankerWorkerError:
        process = session.process
        try:
            process.wait(timeout=2.0)  # let the stderr pump catch the last lines
        except (subprocess.TimeoutExpired, OSError):
            pass
        time.sleep(0.1)
        tail = " | ".join(line for line in session.stderr_tail[-6:] if line.strip())
        code = process.poll()
        self.close()
        detail = f" (exit code {code})" if code is not None else ""
        return RankerWorkerError(f"{message}{detail}{': ' + tail if tail else ''}")


def _worker_failure(spec: RankerSpec, event: dict[str, object]) -> RankerError:
    message = str(event.get("message", "unknown failure"))
    category = str(event.get("category", ""))
    if category in {"runtime_unavailable", "missing_module"}:
        return RankerUnavailable(f"{spec.name} cannot run: {message}", remediation="Open Settings, run Set Up AI, then Repair AI.")
    if category == "weights_missing":
        return RankerWeightsMissing(f"{spec.name}: {message}", remediation="Re-download the model.")
    from ...ai_platform import dll_failure_hint

    hint = dll_failure_hint(message)
    return RankerWorkerError(f"{spec.name} failed: {message}" + (f" {hint}" if hint else ""), remediation=hint)


def _pump_stdout(process: "subprocess.Popen[str]", session: _Session) -> None:
    try:
        assert process.stdout is not None
        for line in process.stdout:
            event = protocol.decode_event(line.rstrip("\r\n"))
            if event is not None:
                session.events.put(event)
    except (OSError, ValueError):
        pass
    finally:
        session.events.put(None)


def _pump_stderr(process: "subprocess.Popen[str]", session: _Session) -> None:
    try:
        assert process.stderr is not None
        for line in process.stderr:
            session.stderr_tail.append(line.rstrip())
            del session.stderr_tail[:-50]
    except (OSError, ValueError):
        pass


def _kill(process: "subprocess.Popen[str]") -> None:
    if process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=5.0)
    except (subprocess.TimeoutExpired, OSError):
        pass
    for stream in (process.stdin, process.stdout, process.stderr):
        try:
            if stream is not None:
                stream.close()
        except (OSError, ValueError):
            pass


def maniqa_ranker(**kwargs: object) -> WorkerRanker:
    return WorkerRanker(MANIQA, **kwargs)  # type: ignore[arg-type]


def qrealign_ranker(**kwargs: object) -> WorkerRanker:
    return WorkerRanker(QREALIGN, **kwargs)  # type: ignore[arg-type]


def blink_ranker(**kwargs: object) -> WorkerRanker:
    """Scores how closed the eyes are per frame (0 open .. 1 closed); see ../blink.py. Same worker machinery, CPU only."""
    return WorkerRanker(BLINK, **kwargs)  # type: ignore[arg-type]


def faces_ranker(**kwargs: object) -> WorkerRanker:
    """Finds and embeds the prominent faces per frame (the score is the face count; ``details[key]`` holds the faces); see ../faces.py. CPU only."""
    return WorkerRanker(FACES, **kwargs)  # type: ignore[arg-type]
