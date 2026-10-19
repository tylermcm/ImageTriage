"""Camera Raw's Detail > Super Resolution, as a host-side step after the editor's full-size render.

Super Resolution is a setting in the Camera Raw edit stack, like any other: the editor only records it (``superResolution`` in the
recipe) and keeps working on the raw as before. When the finished photo is output (an export, a convert, a hand-off), the host takes
the ``RenderResult`` ``full_render`` produced and ``apply_super_resolution`` returns a new one, 2x larger, made by the AI upscale
backend (``upscale_service``). Like ``full_render`` it never substitutes: a photo whose Super Resolution cannot run is reported as
``failed`` rather than exported without it. The backend works on 8-bit RGB, so a 16- or 32-bit render is reduced to 8 bits first
and the result is 8-bit.
"""
from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Callable

from . import upscale_core as core
from . import upscale_service as service
from .full_render import RENDERED, RenderResult, failed

SUPER_RESOLUTION_MODEL = "realesrgan-x2plus"
SUPPORTED_DEPTH = 8  # the backend works on 8-bit RGB


def wants_super_resolution(recipe) -> bool:
    """Whether a Camera Raw recipe (the stored form: ``{"version": .., "cameraRaw": {..}}``) asks for it."""
    settings = recipe.get("cameraRaw") if isinstance(recipe, dict) else None
    return isinstance(settings, dict) and settings.get("superResolution") is True


def _to_8bit_png(path: Path, out: Path) -> None:
    """A render of more than 8 bits (PIL cannot read 16-bit RGB) as an 8-bit PNG, through Qt; the colour profile is kept."""
    from PySide6.QtGui import QImage

    image = QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"could not read the rendered picture {path.name}")
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888 if image.hasAlphaChannel() else QImage.Format.Format_RGB888)
    if not converted.save(str(out), "PNG"):
        raise RuntimeError("could not write the 8-bit picture for Super Resolution")


def apply_super_resolution(
    result: RenderResult,
    *,
    model_key: str = SUPER_RESOLUTION_MODEL,
    cancel_event: threading.Event | None = None,
    progress: Callable[[service.UpscaleProgress], None] | None = None,
    run: Callable[..., service.UpscaleResult] = service.run_upscale,
    availability: Callable[[], service.UpscaleAvailability] = service.check_availability,
    to_8bit: Callable[[Path, Path], None] = _to_8bit_png,
) -> RenderResult:
    if result.outcome != RENDERED or result.path is None:
        return result  # nothing was rendered, so there is nothing to upsize
    reduced: Path | None = None
    try:
        state = availability()
        reason = state.models.get(model_key, f"unknown model {model_key}")
        if not state.runtime_ok:
            raise service.UpscaleServiceError("runtime_missing", state.runtime_reason or "the AI runtime is not installed")
        if reason:
            raise service.UpscaleServiceError("model_unavailable", reason)
        suffix = ".tif" if result.format == "tiff" else ".png"
        source = Path(result.path)
        if result.depth != SUPPORTED_DEPTH:
            reduced = source.with_name(source.stem + ".8bit.png")
            to_8bit(source, reduced)
            source = reduced
        request = service.UpscaleRequest(
            input_path=source,
            model_key=model_key,
            output_path=core.upscale_output_path(result.path, core.get_spec(model_key).scale, suffix),
            apply_orientation=False,  # the editor's render already has its orientation applied
            keep_metadata=True,
        )
        upscaled = run(request, progress, cancel_event)
    except service.UpscaleServiceError as error:
        result.release()
        text = str(error)
        hint = f" ({error.remediation})" if error.remediation else ""
        return failed(f"Super Resolution could not run: {text}{hint}" if error.category != "cancelled" else "Super Resolution was cancelled")
    except Exception as error:  # a backend that broke is a failure for this photo, never the un-upsized render
        result.release()
        return failed(f"Super Resolution failed: {error.__class__.__name__}: {error}")
    finally:
        if reduced is not None:
            try:
                reduced.unlink()
            except OSError:
                pass
    result.release()
    width, height = upscaled.output_size
    return replace(
        result,
        path=upscaled.output_path,
        width=int(width),
        height=int(height),
        depth=SUPPORTED_DEPTH,
        temporary=True,
    )
