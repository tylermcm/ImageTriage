"""Detail > Denoise: AI noise reduction on the raw sensor data, as Camera Raw's Enhance > Denoise does it.

The setting (``aiDenoise`` in the photo's settings: ``enabled`` and ``amount`` 0..100) is the host's own, like Super Resolution: it is
kept with the recipe but never sent to PhotoCraft. When it is on, the photo is developed from a *denoised raw* instead of the original:
a copy of the raw file with the AI's denoised sensor samples appended (``raw_denoise``), which PhotoCraft's raw decoder blends in by
the amount written in the file. Everything else about the development (white balance, colour, look, every slider) is the original's.

The denoised raw is made once per photo (the model is slow) and kept in the photo's edits folder; changing the amount rewrites four
bytes of it. A changed photo, or a newer model, makes it again.

    1. the preview worker writes the crop's normalised CFA samples (``denoiseInput``),
    2. the model denoises them on the warm AI host (``mask_engine_service.infer_denoise``),
    3. the result is appended to a copy of the raw, and a small file beside it records what it was made from.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Callable

from . import raw_denoise

KEY = "aiDenoise"
DEFAULTS = {"enabled": False, "amount": 50.0}   # Camera Raw's Denoise starts at 50
FOLDER = "denoised"
MODEL_VERSION = "rawnind-bayer-2024-02-21-iter4350000"
WORKER_TIMEOUT_S = 180


def valid(value) -> bool:
    return (isinstance(value, dict) and set(value) <= {"enabled", "amount"} and isinstance(value.get("enabled", False), bool)
            and type(value.get("amount", 50.0)) in (int, float) and math.isfinite(value.get("amount", 50.0))
            and 0 <= value.get("amount", 50.0) <= 100)


def normalized(value) -> dict:
    result = dict(DEFAULTS)
    if isinstance(value, dict):
        if isinstance(value.get("enabled"), bool):
            result["enabled"] = value["enabled"]
        amount = value.get("amount")
        if type(amount) in (int, float) and math.isfinite(amount):
            result["amount"] = float(max(0.0, min(100.0, amount)))
    return result


def wanted(settings) -> bool:
    """Whether a photo's settings (``cameraRaw``) ask for Denoise."""
    value = settings.get(KEY) if isinstance(settings, dict) else None
    return isinstance(value, dict) and value.get("enabled") is True


def denoised_path(photo: str | Path) -> Path:
    """The photo's denoise companion (raw_denoise, "the companion file"): only the denoised samples, beside the photo's edits."""
    from .edit_storage import edit_root_for

    photo = Path(photo)
    return edit_root_for(photo.parent) / FOLDER / f"{photo.name}.denoise"


def legacy_denoised_path(photo: str | Path) -> Path:
    """Where earlier versions kept a denoised copy of the whole raw (converted to a companion by ``migrate_folder``)."""
    from .edit_storage import edit_root_for

    photo = Path(photo)
    return edit_root_for(photo.parent) / FOLDER / f"{photo.stem}.denoised{photo.suffix}"


def _record_path(photo: str | Path) -> Path:
    photo = Path(photo)
    return denoised_path(photo).with_name(f"{photo.stem}.denoised.json")


def _signature(photo) -> str | None:
    from .photocraft_bridge import file_signature

    return file_signature(str(photo))


def ready(photo: str | Path) -> bool:
    """Whether the photo's denoised raw exists and was made from the photo as it is now, by the current model."""
    target = denoised_path(photo)
    try:
        record = json.loads(_record_path(photo).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return (isinstance(record, dict) and record.get("source") == _signature(photo) and record.get("model") == MODEL_VERSION
            and record.get("denoised") == _signature(target) and raw_denoise.is_companion(target))


def render_source(photo: str, settings) -> str:
    """The file PhotoCraft should develop for ``settings``: the denoised raw (set to the settings' amount) when Denoise is on and the
    denoised raw is ready, otherwise the photo itself."""
    if not wanted(settings) or not ready(photo):
        return photo
    target = denoised_path(photo)
    amount = normalized(settings.get(KEY))["amount"]
    if raw_denoise.read_amount(target) != amount:
        raw_denoise.set_amount(target, amount)
        _remember(photo, target)       # writing the amount changes the file's signature
    return str(target)


def _remember(photo, target: Path) -> None:
    _record_path(photo).write_text(json.dumps({"source": _signature(photo), "model": MODEL_VERSION, "denoised": _signature(target)}),
                                   encoding="utf-8")


def model_installation():
    from .ai_model import rawnind_installation

    return rawnind_installation()


def unavailable_reason() -> str:
    """Why Denoise cannot run here, or "" when it can."""
    from .qml_shell.raw_preview import executable

    if executable() is None:
        return "AI Denoise needs the RAW preview worker."
    if not model_installation().is_installed:
        return "The AI Denoise model is not installed. Install it in AI > AI Center."
    return ""


def worker_input(photo: str, out: Path, *, executable: str | None = None) -> dict:
    """The photo's normalised sensor samples, written to ``out`` by a one-off preview worker; returns what the worker says about them."""
    if executable is None:
        from .qml_shell.raw_preview import executable as find

        executable = find()
    if not executable:
        raise RuntimeError("AI Denoise needs the RAW preview worker.")
    proc = subprocess.Popen([executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        hello = _exchange(proc, {"op": "hello"})
        if type(hello.get("denoise")) is not int or hello["denoise"] < 2:      # 2: it reads the companion file this writes
            raise RuntimeError("AI Denoise needs the updated RAW preview worker.")
        reply = _exchange(proc, {"op": "denoiseInput", "path": photo, "out": str(out)})
        if not reply.get("ok"):
            raise RuntimeError(str(reply.get("error") or "The photo's sensor data could not be read"))
        return reply
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def _exchange(proc, request: dict) -> dict:
    proc.stdin.write((json.dumps(request) + "\n").encode())
    proc.stdin.flush()
    line = proc.stdout.readline()
    if not line:
        raise RuntimeError("The RAW preview worker stopped")
    head = json.loads(line)
    if head.get("bytes"):
        proc.stdout.read(int(head["bytes"]))
    return head


def _engine(model_dir: Path):
    from .mask_engine_service import default_mask_engine_service

    service = default_mask_engine_service()
    return lambda source, out, meta, progress: service.infer_denoise(  # noqa: E731
        model_dir=model_dir, input_path=source, output_path=out, region=meta["region"], pattern=meta["pattern"],
        multipliers=meta["multipliers"], progress_callback=progress)


def make(
    photo: str,
    amount: float = DEFAULTS["amount"],
    *,
    progress: Callable[[str], None] | None = None,
    cancel: threading.Event | None = None,
    sensor_input: Callable[[str, Path], dict] = worker_input,
    engine=None,
) -> Path:
    """Make (or reuse) the photo's denoised raw and return its path. Slow: runs the model over the whole sensor."""
    if ready(photo):
        return Path(render_source(photo, {KEY: {"enabled": True, "amount": amount}}))
    say = progress or (lambda _text: None)
    if engine is None:
        installation = model_installation()
        if not installation.is_installed:
            raise RuntimeError("The AI Denoise model is not installed. Install it in AI > AI Center.")
        engine = _engine(installation.install_dir)
    source_signature = _signature(photo)
    with tempfile.TemporaryDirectory(prefix="image-triage-denoise-") as scratch:
        plane, out = Path(scratch) / "samples.f32", Path(scratch) / "denoised.f16"
        say("Reading the sensor…")
        meta = sensor_input(photo, plane)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelled")
        say("Denoising…")
        result = engine(plane, out, meta, say)
        if cancel is not None and cancel.is_set():
            raise InterruptedError("cancelled")
        import numpy as np

        x, y, w, h = (int(v) for v in result["region"])
        samples = np.fromfile(out, dtype="<f2")
        if samples.size != w * h:
            raise RuntimeError("The denoised samples do not match their region")
        if _signature(photo) != source_signature:
            raise RuntimeError("The photo changed while it was being denoised. Try again.")
        say("Saving…")
        target = raw_denoise.write_companion(Path(photo), denoised_path(photo), samples.reshape(h, w), (x, y, w, h),
                                             meta["sensor"], amount)
    _remember(photo, target)
    return target


def forget(photo: str | Path) -> None:
    """Delete the photo's denoised raw (the setting stays; it is made again when needed)."""
    for path in (denoised_path(photo), legacy_denoised_path(photo), _record_path(photo)):
        try:
            path.unlink()
        except OSError:
            pass


def companions_readable(executable: str | None = None) -> bool:
    """Whether the RAW preview worker reads denoise companions (``"denoise": 2`` in its hello). Earlier denoised raw copies are only
    converted when it does, so a photo never loses its denoising to an older worker."""
    if executable is None:
        from .qml_shell.raw_preview import executable as find

        executable = find()
    if not executable:
        return False
    try:
        proc = subprocess.Popen([executable], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError:
        return False
    try:
        hello = _exchange(proc, {"op": "hello"})
        return type(hello.get("denoise")) is int and hello["denoise"] >= 2
    except Exception:
        return False
    finally:
        try:
            proc.stdin.close()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


def migrate_folder(folder: str | Path) -> int:
    """Turn the denoised raw copies earlier versions kept (``<name>.denoised.<ext>``: the whole raw with the samples appended, ~150 MB
    for a 24 MP photo) into companions (~22 MB), keeping the amount and the record. Returns the bytes freed. Slow: a worker's job."""
    import numpy as np

    from .edit_storage import edit_root_for

    root = edit_root_for(Path(folder)) / FOLDER
    freed = 0
    try:
        entries = [entry for entry in os.scandir(root) if ".denoised." in entry.name and not entry.name.endswith((".json", ".partial"))]
    except OSError:
        return 0
    for entry in entries:
        old = Path(entry.path)
        stem, _, suffix = old.name.partition(".denoised")
        photo = Path(folder) / f"{stem}{suffix}"
        if not photo.exists() or not raw_denoise.has_trailer(old):
            continue
        try:
            record = json.loads(_record_path(photo).read_text(encoding="utf-8"))
            data = old.read_bytes()
            tl = raw_denoise.TRAILER_LEN
            version, amount, x, y, w, h, sw, sh, length = raw_denoise.struct.unpack("<If6IQ", data[-tl:-8])
            samples = np.frombuffer(data[-tl - length:-tl], "<f2").astype(np.float32).reshape(h, w)
            del data
            target = raw_denoise.write_companion(photo, denoised_path(photo), samples, (x, y, w, h), (sw, sh), amount)
            if isinstance(record, dict) and record.get("denoised") == _signature(old):
                record["denoised"] = _signature(target)            # still the photo's own denoising, now as a companion
                _record_path(photo).write_text(json.dumps(record), encoding="utf-8")
            size = old.stat().st_size
            old.unlink()
            freed += size - target.stat().st_size
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return freed
