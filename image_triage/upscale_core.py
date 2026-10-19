"""Pure helpers for AI upscaling (Real-ESRGAN class models). numpy + PIL only.

No onnxruntime, no Qt, no torch: the worker supplies a ``run_tile`` callable, so every piece here
is testable with fake models. See ``docs/ai_upscale_plan.md``.
"""

from __future__ import annotations

import hashlib
import math
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

# Camera Raw's own ceilings, plus per-format caps.
MAX_LONG_SIDE = 65_000
MAX_PIXELS = 500_000_000
FORMAT_LONG_SIDE_CAPS = {".jpg": 65_500, ".jpeg": 65_500, ".webp": 16_383}
OUTPUT_SUFFIXES = (".png", ".tif", ".tiff", ".jpg", ".jpeg", ".webp")

# Tiling: a 96 px margin matches a single pass; 16 px leaves visible seams (measured).
DEFAULT_CORE = 448
MIN_PAD = 96
DEFAULT_PAD = 96
MAX_TILE = 640  # the DirectML-verified limit; applied to every provider until others are measured


class UpscaleError(Exception):
    """A failure with a stable ``category`` the caller can branch on."""

    def __init__(self, category: str, message: str, remediation: str = "") -> None:
        super().__init__(message)
        self.category = category
        self.remediation = remediation


@dataclass(frozen=True)
class UpscaleModelSpec:
    key: str
    name: str
    scale: int
    filename: str
    size: int
    sha256: str
    source_filename: str = ""
    source_size: int = 0
    source_sha256: str = ""
    source_url: str = ""


MODEL_SPECS: dict[str, UpscaleModelSpec] = {
    "realesrgan-x2plus": UpscaleModelSpec(
        key="realesrgan-x2plus",
        name="Real-ESRGAN x2plus",
        scale=2,
        filename="RealESRGAN_x2plus.onnx",
        size=67_073_436,
        sha256="52692d3ff5e8ac73dc2a82581c17df5b9c89373f02b6680a1b79f9cbf063e53d",
        source_filename="RealESRGAN_x2plus.pth",
        source_size=67_061_725,
        source_sha256="49fafd45f8fd7aa8d31ab2a22d14d91b536c34494a5cfe31eb5d89c2fa266abb",
        source_url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.1/RealESRGAN_x2plus.pth",
    ),
    "realesrgan-x4plus": UpscaleModelSpec(
        key="realesrgan-x4plus",
        name="Real-ESRGAN x4plus",
        scale=4,
        filename="RealESRGAN_x4plus.onnx",
        size=67_051_618,
        sha256="937dfde41305f6dc71b786db16df9334a5415df163387a9719924e8a2665324e",
        source_filename="RealESRGAN_x4plus.pth",
        source_size=67_040_989,
        source_sha256="4fa0d38905f75ac06eb49a7951b426670021be3018265fd191d2125df9d682f1",
        source_url="https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth",
    ),
}
DEFAULT_MODEL_KEY = "realesrgan-x2plus"


def get_spec(key: str) -> UpscaleModelSpec:
    try:
        return MODEL_SPECS[key]
    except KeyError:
        raise UpscaleError("unknown_model", f"Unknown upscale model: {key!r}") from None


# ---------------------------------------------------------------- model files


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def verify_model_file(model_dir: Path | str, spec: UpscaleModelSpec, *, deep: bool = False) -> None:
    """Raise ``model_unavailable`` unless the pinned file is present, the right size (and hash if deep)."""
    path = Path(model_dir) / spec.filename
    remediation = f"Install {spec.filename} ({spec.size:,} bytes) into {Path(model_dir)}."
    if not path.is_file():
        raise UpscaleError("model_unavailable", f"Model file missing: {path}", remediation)
    actual = path.stat().st_size
    if actual != spec.size:
        raise UpscaleError(
            "model_unavailable", f"Model file {path.name} is {actual:,} bytes, expected {spec.size:,}.", remediation
        )
    if deep and sha256_file(path) != spec.sha256:
        raise UpscaleError("model_unavailable", f"Model file {path.name} failed its SHA-256 check.", remediation)


def model_status(model_dir: Path | str, *, deep: bool = False) -> dict[str, str]:
    """Per model key: ``""`` when usable, else the reason it is not."""
    status: dict[str, str] = {}
    for key, spec in MODEL_SPECS.items():
        try:
            verify_model_file(model_dir, spec, deep=deep)
            status[key] = ""
        except UpscaleError as exc:
            status[key] = str(exc)
    return status


# ------------------------------------------------------------------- planning


@dataclass(frozen=True)
class UpscalePlan:
    in_width: int
    in_height: int
    out_width: int
    out_height: int
    scale: int
    core: int
    pad: int
    tile: int
    tiles_x: int
    tiles_y: int
    est_peak_bytes: int
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def tile_count(self) -> int:
        return self.tiles_x * self.tiles_y


def choose_tile(core: int = DEFAULT_CORE, pad: int = DEFAULT_PAD) -> tuple[int, int, int]:
    """Validate and return ``(core, pad, tile)``; ``core`` is kept even, ``tile = core + 2*pad``."""
    if pad < MIN_PAD:
        raise UpscaleError("bad_tiling", f"Tile margin {pad} px is below the {MIN_PAD} px minimum (visible seams).")
    core = int(core)
    if core < 2:
        raise UpscaleError("bad_tiling", "Tile core must be at least 2 px.")
    core -= core % 2
    tile = core + 2 * pad
    if tile > MAX_TILE:
        core = (MAX_TILE - 2 * pad) // 2 * 2
        if core < 2:
            raise UpscaleError("bad_tiling", f"Margin {pad} px leaves no room inside the {MAX_TILE} px tile cap.")
        tile = core + 2 * pad
    return core, pad, tile


def plan_upscale(
    width: int,
    height: int,
    model_key: str,
    output_suffix: str = ".png",
    *,
    core: int = DEFAULT_CORE,
    pad: int = DEFAULT_PAD,
    has_alpha: bool = False,
) -> UpscalePlan:
    spec = get_spec(model_key)
    if width < 1 or height < 1:
        raise UpscaleError("bad_image", "The image has no pixels.")
    suffix = output_suffix.lower()
    if suffix not in OUTPUT_SUFFIXES:
        raise UpscaleError("bad_format", f"Unsupported output format {output_suffix!r}.")
    out_w, out_h = width * spec.scale, height * spec.scale
    long_side = max(out_w, out_h)
    cap = min(MAX_LONG_SIDE, FORMAT_LONG_SIDE_CAPS.get(suffix, MAX_LONG_SIDE))
    if long_side > cap:
        raise UpscaleError(
            "too_large",
            f"The {spec.scale}x result would be {out_w} x {out_h} px; {suffix} allows at most {cap} px on the long side.",
            "Choose a smaller scale, a different output format, or crop the image first.",
        )
    if out_w * out_h > MAX_PIXELS:
        raise UpscaleError(
            "too_large",
            f"The {spec.scale}x result would be {out_w * out_h / 1e6:.0f} MP; the limit is {MAX_PIXELS // 1_000_000} MP.",
            "Choose a smaller scale or crop the image first.",
        )
    core, pad, tile = choose_tile(core, pad)
    tiles_x, tiles_y = math.ceil(width / core), math.ceil(height / core)
    channels = 3
    padded = (tiles_x * core + 2 * pad) * (tiles_y * core + 2 * pad) * channels
    est = width * height * channels + padded + out_w * out_h * channels
    if has_alpha:
        est += out_w * out_h
    warnings: list[str] = []
    if has_alpha and suffix in {".jpg", ".jpeg"}:
        warnings.append("JPEG has no alpha channel; transparency will be dropped.")
    if tiles_x * tiles_y == 1 and (width < core or height < core):
        warnings.append("Image is smaller than one tile; edges use reflected padding.")
    return UpscalePlan(width, height, out_w, out_h, spec.scale, core, pad, tile, tiles_x, tiles_y, est, tuple(warnings))


# --------------------------------------------------------------------- tiling

ProgressFn = Callable[[int, int], None]


def upscale_array(
    rgb: np.ndarray,
    run_tile: Callable[[np.ndarray], np.ndarray],
    scale: int,
    *,
    core: int = DEFAULT_CORE,
    pad: int = DEFAULT_PAD,
    progress: ProgressFn | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> np.ndarray:
    """Upscale an ``HxWx3`` uint8 array tile by tile.

    Every ``run_tile`` call receives the same float32 ``1x3xTxT`` shape (avoids provider recompiles);
    only each tile's centre is kept, so tile borders never reach the output.
    """
    if rgb.ndim != 3 or rgb.shape[2] != 3 or rgb.dtype != np.uint8:
        raise UpscaleError("bad_image", "Expected an HxWx3 uint8 array.")
    core, pad, tile = choose_tile(core, pad)
    h, w = rgb.shape[:2]
    tiles_x, tiles_y = math.ceil(w / core), math.ceil(h / core)
    padded_h, padded_w = tiles_y * core, tiles_x * core
    # Reflect-pad so every tile is a full ``tile`` square; NumPy needs pad <= dim-1 per pass, so loop.
    extra_b, extra_r = padded_h - h, padded_w - w
    canvas = _reflect_pad(rgb, pad, pad + extra_b, pad, pad + extra_r)
    out = np.empty((h * scale, w * scale, 3), dtype=np.uint8)
    full = np.empty((padded_h * scale, padded_w * scale, 3), dtype=np.uint8) if (extra_b or extra_r) else out
    total = tiles_x * tiles_y
    done = 0
    for ty in range(tiles_y):
        for tx in range(tiles_x):
            if should_cancel is not None and should_cancel():
                raise UpscaleError("cancelled", "Upscaling was cancelled.")
            y0, x0 = ty * core, tx * core
            patch = canvas[y0 : y0 + tile, x0 : x0 + tile]
            tensor = np.ascontiguousarray(patch.transpose(2, 0, 1)[None], dtype=np.float32) / 255.0
            result = run_tile(tensor)
            expect = (1, 3, tile * scale, tile * scale)
            if getattr(result, "shape", None) != expect:
                raise UpscaleError("bad_model_output", f"Model returned shape {getattr(result, 'shape', None)}, expected {expect}.")
            if not np.isfinite(result).all():
                raise UpscaleError("bad_model_output", "Model returned non-finite values.")
            centre = result[0, :, pad * scale : (pad + core) * scale, pad * scale : (pad + core) * scale]
            pixels = np.clip(np.rint(np.clip(centre, 0.0, 1.0) * 255.0), 0, 255).astype(np.uint8).transpose(1, 2, 0)
            full[y0 * scale : (y0 + core) * scale, x0 * scale : (x0 + core) * scale] = pixels
            done += 1
            if progress is not None:
                progress(done, total)
    if full is not out:
        out[:] = full[: h * scale, : w * scale]
    return out


def _reflect_pad(a: np.ndarray, top: int, bottom: int, left: int, right: int) -> np.ndarray:
    """``np.pad(mode='reflect')`` that tolerates pads larger than the image (repeated reflection)."""
    h, w = a.shape[:2]
    if h == 1 or w == 1:
        return np.pad(a, ((top, bottom), (left, right), (0, 0)), mode="edge")
    out = a
    for t, b, l, r in _split_pad(h, w, top, bottom, left, right):
        out = np.pad(out, ((t, b), (l, r), (0, 0)), mode="reflect")
    return out


def _split_pad(h: int, w: int, top: int, bottom: int, left: int, right: int):
    while top or bottom or left or right:
        t, b = min(top, h - 1), min(bottom, h - 1)
        l, r = min(left, w - 1), min(right, w - 1)
        yield t, b, l, r
        top, bottom, left, right = top - t, bottom - b, left - l, right - r
        h, w = h + t + b, w + l + r


# ------------------------------------------------------------------ providers


def select_providers(available: Sequence[str], device: str = "auto") -> list[str]:
    """CUDA, then DirectML, then CPU; always ends in CPU. ``cpu`` means CPU only."""
    if device.strip().lower() == "cpu":
        return ["CPUExecutionProvider"]
    order = ["CUDAExecutionProvider", "DmlExecutionProvider"]
    chosen = [p for p in order if p in available]
    chosen.append("CPUExecutionProvider")
    return chosen


def self_check_input(size: int = 96) -> np.ndarray:
    """Deterministic textured float32 ``1x3xSxS`` tile for comparing a provider with the CPU."""
    rng = np.random.default_rng(1234)
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32) / size
    base = np.stack([xs, ys, (xs + ys) / 2.0])
    noise = rng.normal(0.0, 0.05, base.shape).astype(np.float32)
    return np.clip(base + noise, 0.0, 1.0)[None].astype(np.float32)


def providers_agree(candidate: np.ndarray, reference: np.ndarray, tolerance: float = 5e-3) -> bool:
    if candidate.shape != reference.shape or not np.isfinite(candidate).all():
        return False
    return float(np.abs(candidate - reference).mean()) <= tolerance


# --------------------------------------------------------------------- output


def upscale_output_path(source: Path | str, scale: int, suffix: str = "", folder: Path | str | None = None) -> Path:
    """``<stem>_<scale>x_ai<.ext>`` beside the source; never an existing file (``" (2)"`` suffixing)."""
    src = Path(source)
    ext = (suffix or src.suffix).lower()
    if ext not in OUTPUT_SUFFIXES:
        ext = ".png"
    base = Path(folder) if folder else src.parent
    candidate = base / f"{src.stem}_{scale}x_ai{ext}"
    n = 2
    while candidate.exists():
        candidate = base / f"{src.stem}_{scale}x_ai ({n}){ext}"
        n += 1
    return candidate


def provenance_text(spec: UpscaleModelSpec, provider: str) -> str:
    return f"Image Triage AI upscale {spec.scale}x ({spec.key}, model sha256 {spec.sha256[:8]}, {provider})"


def save_output(
    rgb: np.ndarray,
    path: Path | str,
    spec: UpscaleModelSpec,
    provider: str,
    *,
    alpha: np.ndarray | None = None,
    icc_profile: bytes | None = None,
    exif: bytes | None = None,
    jpeg_quality: int = 95,
    overwrite: bool = False,
) -> Path:
    """Write ``<name>.partial`` then rename, so a killed job never leaves a half-written output."""
    from PIL import Image

    dest = Path(path)
    if dest.exists() and not overwrite:
        raise UpscaleError("exists", f"Output already exists: {dest}")
    ext = dest.suffix.lower()
    if ext not in OUTPUT_SUFFIXES:
        raise UpscaleError("bad_format", f"Unsupported output format {dest.suffix!r}.")
    image = Image.fromarray(rgb, "RGB")
    if alpha is not None and ext not in {".jpg", ".jpeg"}:
        alpha_img = Image.fromarray(alpha, "L").resize(image.size, Image.Resampling.BICUBIC)
        image.putalpha(alpha_img)
    note = provenance_text(spec, provider)
    kwargs: dict[str, object] = {}
    if icc_profile:
        kwargs["icc_profile"] = icc_profile
    partial = dest.with_name(dest.name + ".partial")
    try:
        if ext in {".jpg", ".jpeg"}:
            fmt = "JPEG"
            kwargs.update(quality=int(jpeg_quality), subsampling=0)
            kwargs["exif"] = _exif_with_software(exif, note)
        elif ext == ".png":
            from PIL.PngImagePlugin import PngInfo

            info = PngInfo()
            info.add_text("Software", note)
            fmt = "PNG"
            kwargs["pnginfo"] = info
        elif ext in {".tif", ".tiff"}:
            fmt = "TIFF"
            kwargs["compression"] = "tiff_lzw"
            kwargs["description"] = note
        else:
            fmt = "WEBP"
            kwargs.update(quality=int(jpeg_quality))
            kwargs["exif"] = _exif_with_software(exif, note)
        with open(partial, "wb") as handle:
            image.save(handle, format=fmt, **kwargs)
        os.replace(partial, dest)
    except BaseException:
        try:
            partial.unlink()
        except OSError:
            pass
        raise
    return dest


def _exif_with_software(exif: bytes | None, note: str) -> bytes:
    from PIL import Image

    data = Image.Exif()
    if exif:
        try:
            data.load(exif)
        except Exception:
            data = Image.Exif()
    data[0x0131] = note  # Software
    data[0x0112] = 1  # orientation already applied to the pixels
    return data.tobytes()
