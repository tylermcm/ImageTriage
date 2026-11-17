"""Batch Export: one pipeline for the result preview and for the files written.

Per photo, in order: the source (the full-size render of its edits, or the file itself), the output size, the output colour profile,
the format and its quality, the metadata kept, then the name and the place. ``prepare`` and ``encode`` are the same calls for the
preview and for the export, so the preview is what is written (only smaller).

Settings are a flat dict of strings, numbers and booleans (``DEFAULTS``), the form the Batch window edits and export presets store.
"""
from __future__ import annotations

import io
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, Signal
from PySide6.QtGui import QColorSpace, QImage

from .edited_export import BatchOutcome, EditedRenderFailed, bit_depth, save_preserving_depth
from .formats import suffix_for_path

DEFAULTS: dict[str, object] = {
    "sizeMode": "original", "long": "2048", "short": "1080", "fitW": "1920", "fitH": "1080", "mp": "12", "pct": "50",
    "noEnlarge": True,
    "format": "jpg", "quality": "90", "depth": "8",
    "profile": "srgb", "intent": "perceptual",
    "meta": "all",
    "xPrefix": "", "xSuffix": "",
    "dest": "subfolder", "sub": "Export", "folder": "", "exists": "unique",
}

SIZE_MODES = (("original", "Original size"), ("long", "Long edge"), ("short", "Short edge"), ("fit", "Fit within"),
              ("mp", "Megapixels"), ("pct", "Percentage"))
FORMATS = (("jpg", "JPEG", ".jpg"), ("png", "PNG", ".png"), ("tif", "TIFF", ".tif"), ("webp", "WebP", ".webp"))
PROFILES = (("srgb", "sRGB"), ("p3", "Display P3"), ("adobergb", "Adobe RGB (1998)"), ("prophoto", "ProPhoto RGB"),
            ("source", "Same as the photo"))
INTENTS = (("perceptual", "Perceptual"), ("relative", "Relative colorimetric"))
METADATA = (("all", "Keep all"), ("nogps", "All but location"), ("copyright", "Copyright and contact only"), ("none", "None"))
DESTINATIONS = (("subfolder", "A folder next to the photos"), ("choose", "A folder I choose"))
EXISTS = (("unique", "Add a number"), ("overwrite", "Overwrite"), ("skip", "Skip the photo"))

_NAMED_SPACES = {"srgb": QColorSpace.NamedColorSpace.SRgb, "p3": QColorSpace.NamedColorSpace.DisplayP3,
                 "adobergb": QColorSpace.NamedColorSpace.AdobeRgb, "prophoto": QColorSpace.NamedColorSpace.ProPhotoRgb}
_DEEP = ("png", "tif")
_GPS_IFD = 0x8825
_COPYRIGHT_TAGS = (0x013B, 0x8298, 0x010F, 0x0110, 0x0131, 0x0132)   # artist, copyright, make, model, software, date


def _number(value, default: float) -> float:
    try:
        number = float(str(value).replace(",", "").strip())
    except ValueError:
        return default
    return number if number == number and abs(number) != float("inf") else default


def normalized(values: dict | None) -> dict:
    """Every setting present, of the right type, inside its range; unknown keys dropped."""
    result = dict(DEFAULTS)
    for key, default in DEFAULTS.items():
        if not isinstance(values, dict) or key not in values:
            continue
        value = values[key]
        if isinstance(default, bool):
            result[key] = bool(value)
        else:
            result[key] = str(value)[:260]
    choices = {"sizeMode": SIZE_MODES, "profile": PROFILES, "intent": INTENTS, "meta": METADATA, "dest": DESTINATIONS,
               "exists": EXISTS}
    for key, options in choices.items():
        if result[key] not in {v for v, _ in options}:
            result[key] = DEFAULTS[key]
    if result["format"] not in {f for f, _, _ in FORMATS}:
        result["format"] = DEFAULTS["format"]
    if result["depth"] not in ("8", "16"):
        result["depth"] = "8"
    return result


def suffix_of(values: dict) -> str:
    return next(s for f, _, s in FORMATS if f == values["format"])


def output_size(width: int, height: int, values: dict) -> tuple[int, int]:
    """The written picture's size for a ``width`` x ``height`` source."""
    if width <= 0 or height <= 0:
        return max(1, width), max(1, height)
    mode = values["sizeMode"]
    if mode == "long":
        scale = _number(values["long"], 2048) / max(width, height)
    elif mode == "short":
        scale = _number(values["short"], 1080) / min(width, height)
    elif mode == "fit":
        scale = min(_number(values["fitW"], 1920) / width, _number(values["fitH"], 1080) / height)
    elif mode == "mp":
        scale = (_number(values["mp"], 12) * 1_000_000 / (width * height)) ** 0.5
    elif mode == "pct":
        scale = _number(values["pct"], 100) / 100
    else:
        scale = 1.0
    if scale <= 0:
        scale = 1.0
    if values["noEnlarge"]:
        scale = min(scale, 1.0)
    return max(1, round(width * scale)), max(1, round(height * scale))


def size_problem(values: dict) -> str:
    """Why the size settings cannot be used, or ""."""
    mode = values["sizeMode"]
    keys = {"long": ("long",), "short": ("short",), "fit": ("fitW", "fitH"), "mp": ("mp",), "pct": ("pct",)}.get(mode, ())
    for key in keys:
        number = _number(values[key], -1)
        if number <= 0:
            return "Enter a size above zero."
        if mode in ("long", "short", "fit") and number > 65000:
            return "That size is larger than any format can hold."
        if mode == "pct" and number > 1000:
            return "Enter a percentage up to 1000."
        if mode == "mp" and number > 1000:
            return "Enter up to 1000 megapixels."
    if values["format"] in ("jpg", "webp"):
        quality = _number(values["quality"], -1)
        if not 1 <= quality <= 100:
            return "Quality goes from 1 to 100."
    return ""


def target_space(values: dict, source_icc: bytes | None) -> QColorSpace:
    if values["profile"] == "source":
        space = QColorSpace.fromIccProfile(source_icc) if source_icc else QColorSpace()
        return space if space.isValid() else QColorSpace(QColorSpace.NamedColorSpace.SRgb)
    return QColorSpace(_NAMED_SPACES[values["profile"]])


def prepare(image: QImage, source_icc: bytes | None, values: dict, *, size: tuple[int, int] | None = None) -> QImage:
    """``image`` at the output size (or ``size``, for a preview of it) in the output profile, its colour space set to that profile.
    A picture without a profile is taken to be sRGB."""
    if image.isNull():
        return image
    if not image.colorSpace().isValid():
        space = QColorSpace.fromIccProfile(source_icc) if source_icc else QColorSpace()
        image = QImage(image)
        image.setColorSpace(space if space.isValid() else QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    width, height = size or output_size(image.width(), image.height(), values)
    if (width, height) != (image.width(), image.height()):
        image = image.scaled(width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    target = target_space(values, source_icc)
    if image.colorSpace() != target:
        if bit_depth(image) <= 8 and image.format() not in (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32):
            image = image.convertToFormat(QImage.Format.Format_ARGB32)
        image = image.convertedToColorSpace(target)
    return image


def filtered_exif(exif_bytes: bytes | None, mode: str) -> bytes | None:
    """The EXIF block to write for metadata ``mode``."""
    if not exif_bytes or mode == "none":
        return None
    if mode == "all":
        return exif_bytes
    from PIL import Image

    exif = Image.Exif()
    try:
        exif.load(exif_bytes)
    except Exception:
        return None
    if mode == "nogps":
        exif.pop(_GPS_IFD, None)
        return exif.tobytes()
    kept = Image.Exif()
    for tag in _COPYRIGHT_TAGS:
        if tag in exif:
            kept[tag] = exif[tag]
    return kept.tobytes() if len(kept) else None


def encode(image: QImage, values: dict, exif_bytes: bytes | None) -> bytes:
    """The file's bytes for a prepared picture. JPEG and WebP are 8-bit; PNG and TIFF are 8- or 16-bit as asked."""
    fmt = values["format"]
    icc = bytes(image.colorSpace().iccProfile()) if image.colorSpace().isValid() else None
    exif = filtered_exif(exif_bytes, values["meta"])
    if fmt in _DEEP and values["depth"] == "16":
        deep = image if bit_depth(image) > 8 else image.convertToFormat(QImage.Format.Format_RGBA64)
        fd, temporary = tempfile.mkstemp(suffix=suffix_of(values))
        os.close(fd)
        try:
            save_preserving_depth(deep, target_path=temporary, target_suffix=suffix_of(values), exif_bytes=exif, icc_profile=icc)
            return Path(temporary).read_bytes()
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)
    from PIL import Image

    rgb = image.convertToFormat(QImage.Format.Format_RGB888)
    picture = Image.frombuffer("RGB", (rgb.width(), rgb.height()), bytes(rgb.constBits()), "raw", "RGB", rgb.bytesPerLine(), 1)
    out = io.BytesIO()
    options: dict = {}
    if icc:
        options["icc_profile"] = icc
    if exif:
        options["exif"] = exif
    if fmt == "jpg":
        picture.save(out, "JPEG", quality=int(_number(values["quality"], 90)), optimize=True, **options)
    elif fmt == "webp":
        picture.save(out, "WEBP", quality=int(_number(values["quality"], 90)), **options)
    elif fmt == "png":
        picture.save(out, "PNG", **options)
    else:
        picture.save(out, "TIFF", compression="tiff_adobe_deflate", **options)
    return out.getvalue()


# ---- the plan ------------------------------------------------------------------------------------------------------------------
@dataclass(slots=True, frozen=True)
class ExportSource:
    path: str
    name: str


@dataclass(slots=True, frozen=True)
class ExportItem:
    source: ExportSource
    target_path: str
    target_name: str
    status: str          # "Export", "Overwrite", "Skip", "Error"
    message: str = ""


@dataclass(slots=True, frozen=True)
class ExportPlan:
    values: dict
    items: tuple[ExportItem, ...]
    executable: tuple[ExportItem, ...]
    error_count: int
    skip_count: int
    can_apply: bool
    general_error: str = ""


def destination_for(source_path: str, values: dict) -> Path | None:
    if values["dest"] == "choose":
        folder = values["folder"].strip()
        return Path(folder) if folder else None
    sub = values["sub"].strip() or "Export"
    return Path(source_path).parent / sub


def _clean(text: str) -> str:
    return "".join("_" if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in text)


def build_plan(sources: list[ExportSource], values: dict) -> ExportPlan:
    values = normalized(values)
    general = size_problem(values)
    if not general and values["dest"] == "choose" and not values["folder"].strip():
        general = "Choose the folder to export to."
    if not general and any(c in values["sub"] for c in '<>:"/\\|?*'):
        general = "The folder name cannot hold < > : \" / \\ | ? *"
    suffix = suffix_of(values)
    reserved: set[str] = set()
    items: list[ExportItem] = []
    for source in sources:
        folder = destination_for(source.path, values)
        name = _clean(values["xPrefix"]) + Path(source.name).stem + _clean(values["xSuffix"]) + suffix
        if folder is None:
            items.append(ExportItem(source, "", name, "Error", "No destination folder."))
            continue
        target = folder / name
        key = os.path.normcase(str(target.resolve()) if target.parent.exists() else str(target))
        status, message = "Export", ""
        itself = os.path.normcase(os.path.abspath(target)) == os.path.normcase(os.path.abspath(source.path))
        if itself or key in reserved or target.exists():
            policy = "unique" if itself else values["exists"]       # a photo is never written over by its own export
            if policy == "skip" and key not in reserved:
                status, message = "Skip", "A file with this name is already there."
            elif policy == "overwrite" and key not in reserved:
                status, message = "Overwrite", "Replaces the file that is there."
            else:
                stem, counter = Path(name).stem, 1
                while True:
                    candidate = folder / f"{stem}_{counter}{suffix}"
                    ckey = os.path.normcase(str(candidate))
                    if ckey not in reserved and not candidate.exists():
                        target, key, name = candidate, ckey, candidate.name
                        break
                    counter += 1
        reserved.add(key)
        items.append(ExportItem(source, str(target), name, status, message))
    errors = sum(1 for i in items if i.status == "Error")
    skips = sum(1 for i in items if i.status == "Skip")
    executable = tuple(i for i in items if i.status in ("Export", "Overwrite"))
    return ExportPlan(values, tuple(items), executable, errors, skips,
                      can_apply=not general and errors == 0 and bool(executable), general_error=general)


# ---- loading and writing -------------------------------------------------------------------------------------------------------
def load_source(path: str):
    """``(image, exif, icc)``: the photo's full-size picture (its edits rendered, if it has any). Raises ``EditedRenderFailed`` for
    edits that cannot be rendered and ``OSError`` for a file that cannot be read."""
    from .image_resize import load_image_for_export

    loaded = load_image_for_export(path, target_size=QSize(), ignore_orientation=False, strip_metadata=False)
    if loaded.image.isNull():
        raise OSError(f"Could not read {Path(path).name}.")
    return loaded.image, loaded.exif_bytes, loaded.icc_profile


def export_item(item: ExportItem, values: dict, loader=load_source) -> None:
    image, exif, icc = loader(item.source.path)
    data = encode(prepare(image, icc, values), values, exif)
    target = Path(item.target_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=str(target.parent), suffix=target.suffix)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)


def apply_plan(plan: ExportPlan, progress=None, loader=load_source) -> BatchOutcome:
    written, failed = [], []
    total = len(plan.executable)
    for index, item in enumerate(plan.executable, start=1):
        if progress is not None:
            progress(index, total, f"Exporting {item.source.name}")
        try:
            export_item(item, plan.values, loader)
            written.append(item.target_path)
        except EditedRenderFailed as error:
            failed.append((item.source.path, error.reason))
        except Exception as error:
            failed.append((item.source.path, str(error) or error.__class__.__name__))
    return BatchOutcome(written, failed)


class ExportApplySignals(QObject):
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class ExportApplyTask(QRunnable):
    def __init__(self, plan: ExportPlan) -> None:
        super().__init__()
        self.plan = plan
        self.signals = ExportApplySignals()

    def run(self) -> None:
        try:
            outcome = apply_plan(self.plan, lambda i, n, m: self.signals.progress.emit(i, n, m))
        except Exception as error:  # pragma: no cover - apply_plan reports per photo
            self.signals.failed.emit(str(error))
            return
        self.signals.finished.emit(outcome)


# ---- the preview ---------------------------------------------------------------------------------------------------------------
PREVIEW_SIDE = 900


@dataclass(slots=True)
class Preview:
    before: QImage          # the source as it is, at preview size, for display
    after: QImage           # the export at preview size, converted back to sRGB for display
    width: int              # the written picture's size
    height: int
    estimated_bytes: int


def make_preview(image: QImage, icc: bytes | None, values: dict, side: int = PREVIEW_SIDE) -> Preview:
    """What the export of ``image`` looks like, at most ``side`` pixels on its long edge, with its final size and an estimate of the
    file's size (the preview encoded the same way, scaled by the pixel count)."""
    values = normalized(values)
    width, height = output_size(image.width(), image.height(), values)
    scale = min(1.0, side / max(width, height))
    shown = (max(1, round(width * scale)), max(1, round(height * scale)))
    after = prepare(image, icc, values, size=shown)
    estimate = len(encode(after, values, None)) / (shown[0] * shown[1]) * (width * height)
    srgb = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
    display = after.convertedToColorSpace(srgb) if after.colorSpace().isValid() and after.colorSpace() != srgb else after
    before = image.scaled(shown[0], shown[1], Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    if before.colorSpace().isValid() and before.colorSpace() != srgb:
        before = before.convertedToColorSpace(srgb)
    return Preview(before, display, width, height, int(estimate))


def human_bytes(count: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if count < 1024 or unit == "GB":
            return f"{count} {unit}" if unit == "bytes" else f"{count:.1f} {unit}"
        count /= 1024
    return ""
