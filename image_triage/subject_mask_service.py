"""Host-side subject masks for the editor's Camera Raw Masking tab: BiRefNet, as the built-in editor used it.

``run_subject_mask`` spawns ``subject_mask_worker`` under the managed AI runtime (torch) and blocks until it finishes, so call it from a
worker thread; setting the ``threading.Event`` cancels it. No Qt, no torch here. The editor writes its preview picture to a PNG and
asks for the mask through the host-job channel (``camera_raw_host_jobs``); the 8-bit coverage map comes back as a PNG at ``output_path``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from .ai_env import AIRuntimeUnavailable, build_worker_env, select_runtime

PROTOCOL_VERSION = 1


def _worker_script() -> Path:
    """Source tree: next to this module. Frozen app: ``ai_workers/`` beside the executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "ai_workers" / "subject_mask_worker.py"
    return Path(__file__).with_name("subject_mask_worker.py")


WORKER_SCRIPT = _worker_script()


class SubjectMaskError(Exception):
    def __init__(self, category: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.category = category
        self.remediation = remediation


@dataclass(frozen=True)
class SubjectMaskResult:
    output_path: Path
    width: int
    height: int
    device: str


def model_problem() -> str:
    """Why the subject model cannot run (empty when it can)."""
    try:
        from .ai_model import resolve_birefnet_model_installation

        installation = resolve_birefnet_model_installation()
        if not installation.is_installed:
            return "The subject model (BiRefNet) is not installed. Open Settings and run Set Up AI."
    except Exception as exc:  # a broken install record is a reason, not a crash
        return f"The subject model could not be checked: {exc}"
    try:
        select_runtime("auto", require_torch=True)
    except AIRuntimeUnavailable as exc:
        return f"{exc} {exc.remediation}".strip()
    return ""


def _default_spawn(command: list[str], env: dict[str, str]) -> subprocess.Popen:
    return subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _build_command(job_path: Path) -> list[str]:
    from .ai_workflow import resolve_ai_python_script_command

    return resolve_ai_python_script_command(WORKER_SCRIPT, "--job", str(job_path))


def _terminate(process: Any) -> None:
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _lines(process: Any) -> Iterable[str]:
    stream = process.stdout
    return iter(stream.readline, "") if hasattr(stream, "readline") else iter(stream)


def _json(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def run_subject_mask(
    input_path: Path,
    output_path: Path,
    progress_callback: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    *,
    device: str = "auto",
    spawn: Callable[[list[str], dict[str, str]], Any] = _default_spawn,
    runtime_resolver: Callable[..., Any] = select_runtime,
    command_builder: Callable[[Path], list[str]] = _build_command,
    model_dir: Path | None = None,
) -> SubjectMaskResult:
    source = Path(input_path)
    if not source.is_file():
        raise SubjectMaskError("bad_image", f"The picture for the mask was not found: {source}")
    if model_dir is None:
        from .ai_model import resolve_birefnet_model_installation

        installation = resolve_birefnet_model_installation()
        if not installation.is_installed:
            raise SubjectMaskError("model_missing", "The subject model (BiRefNet) is not installed.", "Open Settings and run Set Up AI.")
        model_dir = installation.install_dir
    try:
        selection = runtime_resolver(device, require_torch=True)
    except AIRuntimeUnavailable as exc:
        raise SubjectMaskError(exc.category, str(exc), exc.remediation) from exc
    job = {"protocolVersion": PROTOCOL_VERSION, "inputPath": str(source), "outputPath": str(output_path), "modelDir": str(model_dir), "device": device}
    handle = tempfile.NamedTemporaryFile("w", prefix="subject_mask_job_", suffix=".json", delete=False, encoding="utf-8")
    job_path = Path(handle.name)
    process = None
    try:
        with handle:
            json.dump(job, handle)
        try:
            command = command_builder(job_path)
        except FileNotFoundError as exc:
            raise SubjectMaskError("worker_missing", f"The subject mask worker is unavailable: {exc}", "Reinstall Image Triage or run Set Up AI.") from exc
        process = spawn(command, build_worker_env(selection, protocol_version=PROTOCOL_VERSION))
        return _stream(process, progress_callback, cancel_event, Path(output_path))
    finally:
        if process is not None and process.poll() is None:
            _terminate(process)
        try:
            job_path.unlink()
        except OSError:
            pass


def _stream(process: Any, progress_callback, cancel_event, output: Path) -> SubjectMaskResult:
    tail: deque[str] = deque(maxlen=12)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    finished = threading.Event()

    def watch() -> None:
        while not finished.is_set():
            if cancel_event.wait(0.05):
                if not finished.is_set() and process.poll() is None:
                    _terminate(process)
                return

    if cancel_event is not None:
        threading.Thread(target=watch, daemon=True).start()
    try:
        for raw in _lines(process):
            line = raw.rstrip("\r\n")
            if cancel_event is not None and cancel_event.is_set():
                break
            if line.startswith("PROGRESS "):
                if progress_callback is not None:
                    progress_callback(line[len("PROGRESS "):].strip())
            elif line.startswith("RESULT "):
                result = _json(line[len("RESULT "):])
            elif line.startswith("ERROR "):
                error = _json(line[len("ERROR "):])
            elif line:
                tail.append(line)
        if cancel_event is not None and cancel_event.is_set():
            _terminate(process)
            raise SubjectMaskError("cancelled", "Cancelled")
        code = process.wait()
    finally:
        finished.set()
    if error is not None:
        raise SubjectMaskError(str(error.get("category", "worker_failed")), str(error.get("message", "")), str(error.get("remediation", "")))
    if code != 0 or result is None or not output.is_file():
        raise SubjectMaskError("worker_failed", "The subject mask worker exited unexpectedly.\n" + "\n".join(tail))
    return SubjectMaskResult(output, int(result.get("width", 0)), int(result.get("height", 0)), str(result.get("device", "")))
