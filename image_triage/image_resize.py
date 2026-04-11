from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSize, Signal, Qt
from PySide6.QtGui import QColor, QImage, QImageWriter, QPainter

from .formats import JPEG_SUFFIXES, MODEL_SUFFIXES, PSD_SUFFIXES, RAW_SUFFIXES, suffix_for_path
from .imaging import load_image_for_resize

try:
    from PIL import Image, ImageOps
except ImportError:  # pragma: no cover - depends on local environment
    Image = None
    ImageOps = None


WRITABLE_IMAGE_SUFFIXES = frozenset(
    {
        ".bmp",
        ".jpe",
        ".jpeg",
        ".jfif",
        ".jpg",
        ".png",
        ".tif",
        ".tiff",
        ".webp",
    }
)

OUTPUT_FORMAT_NAMES = {
    ".bmp": "BMP",
    ".jpe": "JPEG",
    ".jpeg": "JPEG",
    ".jfif": "JPEG",
    ".jpg": "JPEG",
    ".png": "PNG",
    ".tif": "TIFF",
    ".tiff": "TIFF",
    ".webp": "WEBP",
}

EXIF_ORIENTATION_TAG = 274


@dataclass(slots=True, frozen=True)
class ResizePreset:
    key: str
    name: str
    width: int
    height: int

    @property
    def description(self) -> str:
        return f"fits within {self.width} x {self.height} pixels"


@dataclass(slots=True, frozen=True)
class ResizeSourceItem:
    source_path: str
    source_name: str


@dataclass(slots=True)
class ResizeOptions:
    preset_key: str = "small"
    custom_width: int = 1920
    custom_height: int = 1080
    shrink_only: bool = False
    ignore_orientation: bool = False
    overwrite: bool = False
    strip_metadata: bool = False


@dataclass(slots=True)
class ResizePlanItem:
    source: ResizeSourceItem
    target_path: str
    target_name: str
    status: str
    message: str = ""


@dataclass(slots=True)
class ResizePlan:
    items: tuple[ResizePlanItem, ...]
    executable_items: tuple[ResizePlanItem, ...]
    target_width: int
    target_height: int
    label: str
    copy_mode: str
    executable_count: int
    error_count: int
    can_apply: bool
    general_error: str = ""


@dataclass(slots=True)
class _LoadedResizeImage:
    image: QImage
    exif_bytes: bytes | None = None
    icc_profile: bytes | None = None


PRESETS: tuple[ResizePreset, ...] = (
    ResizePreset("small", "Small", 854, 480),
    ResizePreset("medium", "Medium", 1366, 768),
    ResizePreset("large", "Large", 1920, 1080),
    ResizePreset("phone", "Phone", 320, 568),
    ResizePreset("2k", "2K", 2560, 1440),
    ResizePreset("custom", "Custom", 0, 0),
)


def resize_presets() -> tuple[ResizePreset, ...]:
    return PRESETS


def default_resize_preset() -> ResizePreset:
    return PRESETS[0]


def preset_for_key(key: str) -> ResizePreset:
    for preset in PRESETS:
        if preset.key == key:
            return preset
    return default_resize_preset()


def build_resize_plan(sources: list[ResizeSourceItem], options: ResizeOptions) -> ResizePlan:
    width, height, label, general_error = _resolve_bounds(options)
    if general_error:
        items = tuple(
            ResizePlanItem(
                source=source,
                target_path=source.source_path,
                target_name=source.source_name,
                status="Error",
                message=general_error,
            )
            for source in sources
        )
        return ResizePlan(
            items=items,
            executable_items=(),
            target_width=0,
            target_height=0,
            label="",
            copy_mode="Overwrite" if options.overwrite else "Copy",
            executable_count=0,
            error_count=len(items),
            can_apply=False,
            general_error=general_error,
        )

    items: list[ResizePlanItem] = []
    executable_items: list[ResizePlanItem] = []
    reserved_target_keys: set[str] = set()
    error_count = 0
    copy_mode = "Overwrite" if options.overwrite else "Copy"

    for source in sources:
        source_path = source.source_path
        source_name = source.source_name
        if not source_path or not os.path.exists(source_path):
            item = ResizePlanItem(
                source=source,
                target_path=source_path,
                target_name=source_name,
                status="Error",
                message="Source file is missing.",
            )
            items.append(item)
            error_count += 1
            continue

        source_suffix = suffix_for_path(source_path)
        target_suffix = _target_suffix(source_suffix)
        message = ""
        status = copy_mode
        if options.overwrite:
            if target_suffix != source_suffix or source_suffix not in WRITABLE_IMAGE_SUFFIXES:
                item = ResizePlanItem(
                    source=source,
                    target_path=source_path,
                    target_name=source_name,
                    status="Error",
                    message="Overwrite is only available for JPG, PNG, WebP, TIFF, and BMP files.",
                )
                items.append(item)
                error_count += 1
                continue
            target_path = source_path
            target_name = source_name
        else:
            if target_suffix != source_suffix:
                message = "Exports as a JPG copy."
            requested_name = _copy_target_name(source_name, target_suffix, label)
            target_path = _unique_copy_target_path(source_path, requested_name, reserved_target_keys)
            target_name = Path(target_path).name

        item = ResizePlanItem(
            source=source,
            target_path=target_path,
            target_name=target_name,
            status=status,
            message=message,
        )
        items.append(item)
        executable_items.append(item)
        reserved_target_keys.add(_path_key(target_path))

    return ResizePlan(
        items=tuple(items),
        executable_items=tuple(executable_items),
        target_width=width,
        target_height=height,
        label=label,
        copy_mode=copy_mode,
        executable_count=len(executable_items),
        error_count=error_count,
        can_apply=bool(executable_items) and error_count == 0,
        general_error=general_error,
    )


def apply_resize_plan(
    plan: ResizePlan,
    options: ResizeOptions,
    *,
    progress_callback=None,
) -> tuple[str, ...]:
    if not plan.executable_items:
        return ()

    total_steps = len(plan.executable_items)
    written_paths: list[str] = []
    for index, item in enumerate(plan.executable_items, start=1):
        _resize_item(item, plan, options)
        written_paths.append(item.target_path)
        if progress_callback is not None:
            progress_callback(index, total_steps, f"Saved {item.target_name}")
    return tuple(written_paths)


class ResizeApplySignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class ResizeApplyTask(QRunnable):
    def __init__(self, plan: ResizePlan, options: ResizeOptions) -> None:
        super().__init__()
        self.plan = plan
        self.options = options
        self.signals = ResizeApplySignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        total = max(1, len(self.plan.executable_items))
        self.signals.started.emit(total)
        try:
            written_paths = apply_resize_plan(
                self.plan,
                self.options,
                progress_callback=lambda current, total_steps, message: self.signals.progress.emit(current, total_steps, message),
            )
        except Exception as exc:  # pragma: no cover - worker UI error path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(written_paths)


def _resolve_bounds(options: ResizeOptions) -> tuple[int, int, str, str]:
    preset = preset_for_key(options.preset_key)
    width = preset.width
    height = preset.height
    label = preset.name
    if preset.key == "custom":
        width = max(0, int(options.custom_width))
        height = max(0, int(options.custom_height))
        label = f"{width}x{height}"
    if width <= 0 or height <= 0:
        return 0, 0, "", "Enter a width and height greater than zero."
    return width, height, label, ""


def _target_suffix(source_suffix: str) -> str:
    if source_suffix in WRITABLE_IMAGE_SUFFIXES:
        return source_suffix
    return ".jpg"


def _copy_target_name(source_name: str, target_suffix: str, label: str) -> str:
    source_path = Path(source_name)
    return f"{source_path.stem} ({label}){target_suffix}"


def _unique_copy_target_path(source_path: str, requested_name: str, reserved_target_keys: set[str]) -> str:
    directory = Path(source_path).parent
    requested = Path(requested_name)
    stem = requested.stem
    suffix = requested.suffix
    counter = 0
    while True:
        candidate_name = requested.name if counter == 0 else f"{stem}_{counter}{suffix}"
        candidate_path = directory / candidate_name
        candidate_key = _path_key(candidate_path)
        if candidate_key not in reserved_target_keys and not candidate_path.exists():
            return str(candidate_path)
        counter += 1


def _resize_item(item: ResizePlanItem, plan: ResizePlan, options: ResizeOptions) -> None:
    target_size = QSize(plan.target_width, plan.target_height)
    loaded = _load_resize_image(
        item.source.source_path,
        target_size=target_size,
        ignore_orientation=options.ignore_orientation,
        strip_metadata=options.strip_metadata,
    )
    resized = _scaled_image(
        loaded.image,
        target_size=target_size,
        shrink_only=options.shrink_only,
    )
    if resized.isNull():
        raise OSError(f"Could not resize {item.source.source_name}.")

    target_suffix = suffix_for_path(item.target_path)
    _save_resized_image(
        resized,
        target_path=item.target_path,
        target_suffix=target_suffix,
        exif_bytes=None if options.strip_metadata else loaded.exif_bytes,
        icc_profile=None if options.strip_metadata else loaded.icc_profile,
    )


def _load_resize_image(source_path: str, *, target_size: QSize, ignore_orientation: bool, strip_metadata: bool) -> _LoadedResizeImage:
    suffix = suffix_for_path(source_path)
    uses_specialized_loader = suffix in RAW_SUFFIXES or suffix in PSD_SUFFIXES or suffix in MODEL_SUFFIXES

    if Image is not None and not uses_specialized_loader:
        try:
            with Image.open(source_path) as opened:
                working = opened
                if getattr(working, "is_animated", False):
                    working.seek(0)
                exif_bytes = None if strip_metadata else _exif_bytes_for_output(opened)
                icc_profile = None if strip_metadata else opened.info.get("icc_profile")
                if not ignore_orientation and ImageOps is not None:
                    working = ImageOps.exif_transpose(working)
                else:
                    working = working.copy()
                working.load()
                return _LoadedResizeImage(
                    image=_qimage_from_pillow(working, target_size=target_size),
                    exif_bytes=exif_bytes,
                    icc_profile=icc_profile,
                )
        except Exception:
            pass

    image, error = load_image_for_resize(
        source_path,
        target_size=target_size,
        ignore_orientation=ignore_orientation,
    )
    if image.isNull():
        raise OSError(error or f"Could not load {Path(source_path).name}.")
    return _LoadedResizeImage(image=image)


def _scaled_image(image: QImage, *, target_size: QSize, shrink_only: bool) -> QImage:
    if image.isNull():
        return image
    if image.width() <= 0 or image.height() <= 0:
        return QImage()
    width, height = _fit_dimensions(image.width(), image.height(), target_size.width(), target_size.height(), shrink_only=shrink_only)
    if width == image.width() and height == image.height():
        return image
    return image.scaled(
        width,
        height,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )


def _fit_dimensions(source_width: int, source_height: int, target_width: int, target_height: int, *, shrink_only: bool) -> tuple[int, int]:
    if source_width <= 0 or source_height <= 0 or target_width <= 0 or target_height <= 0:
        return max(1, source_width), max(1, source_height)
    scale = min(target_width / source_width, target_height / source_height)
    if shrink_only:
        scale = min(1.0, scale)
    width = max(1, int(round(source_width * scale)))
    height = max(1, int(round(source_height * scale)))
    return width, height


def _save_resized_image(
    image: QImage,
    *,
    target_path: str,
    target_suffix: str,
    exif_bytes: bytes | None,
    icc_profile: bytes | None,
) -> None:
    if Image is not None:
        try:
            _save_qimage_with_pillow(
                image,
                target_path=target_path,
                target_suffix=target_suffix,
                exif_bytes=exif_bytes,
                icc_profile=icc_profile,
            )
            return
        except Exception:
            pass
    _save_qimage_with_writer(image, target_path=target_path, target_suffix=target_suffix)


def _save_qimage_with_pillow(
    image: QImage,
    *,
    target_path: str,
    target_suffix: str,
    exif_bytes: bytes | None,
    icc_profile: bytes | None,
) -> None:
    if Image is None:
        raise OSError("Pillow is unavailable.")
    pillow_image = _pillow_from_qimage(image)
    if target_suffix in JPEG_SUFFIXES:
        pillow_image = _flatten_pillow_for_jpeg(pillow_image)
    elif target_suffix == ".bmp" and pillow_image.mode == "RGBA":
        pillow_image = pillow_image.convert("RGB")

    save_kwargs: dict[str, object] = {}
    if target_suffix in JPEG_SUFFIXES:
        save_kwargs["quality"] = 92
        save_kwargs["optimize"] = True
    elif target_suffix == ".webp":
        save_kwargs["quality"] = 92
        save_kwargs["method"] = 6
    if exif_bytes:
        save_kwargs["exif"] = exif_bytes
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile

    temp_path = _temporary_output_path(target_path)
    try:
        pillow_image.save(temp_path, format=OUTPUT_FORMAT_NAMES[target_suffix], **save_kwargs)
        os.replace(temp_path, target_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _save_qimage_with_writer(image: QImage, *, target_path: str, target_suffix: str) -> None:
    target_image = image
    if target_suffix in JPEG_SUFFIXES:
        target_image = _flatten_qimage_for_jpeg(image)

    temp_path = _temporary_output_path(target_path)
    writer = QImageWriter(temp_path)
    writer.setFormat(OUTPUT_FORMAT_NAMES[target_suffix].encode("ascii"))
    if target_suffix in JPEG_SUFFIXES or target_suffix == ".webp":
        writer.setQuality(92)
    try:
        if not writer.write(target_image):
            raise OSError(writer.errorString() or f"Could not save {Path(target_path).name}.")
    finally:
        del writer
    try:
        os.replace(temp_path, target_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)


def _qimage_from_pillow(image, *, target_size: QSize) -> QImage:
    working = image
    if target_size.isValid() and target_size.width() > 0 and target_size.height() > 0:
        width = max(1, target_size.width())
        height = max(1, target_size.height())
        resampling = getattr(Image, "Resampling", None)
        lanczos = resampling.LANCZOS if resampling is not None else Image.LANCZOS
        working.thumbnail((width, height), lanczos)
    if working.mode != "RGBA":
        working = working.convert("RGBA")
    else:
        working = working.copy()
    qimage = QImage(
        working.tobytes("raw", "RGBA"),
        working.width,
        working.height,
        working.width * 4,
        QImage.Format.Format_RGBA8888,
    )
    return qimage.copy()


def _pillow_from_qimage(image: QImage):
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buffer = converted.bits()
    buffer.setsize(converted.sizeInBytes())
    return Image.frombuffer(
        "RGBA",
        (converted.width(), converted.height()),
        bytes(buffer),
        "raw",
        "RGBA",
        converted.bytesPerLine(),
        1,
    ).copy()


def _flatten_pillow_for_jpeg(image):
    background = Image.new("RGB", image.size, color=(255, 255, 255))
    if image.mode == "RGBA":
        background.paste(image, mask=image.getchannel("A"))
        return background
    return image.convert("RGB")


def _flatten_qimage_for_jpeg(image: QImage) -> QImage:
    if image.format() != QImage.Format.Format_RGBA8888 and image.format() != QImage.Format.Format_ARGB32 and image.format() != QImage.Format.Format_ARGB32_Premultiplied:
        return image.convertToFormat(QImage.Format.Format_RGB32)

    flattened = QImage(image.size(), QImage.Format.Format_RGB32)
    flattened.fill(QColor("#ffffff"))
    painter = QPainter(flattened)
    painter.drawImage(0, 0, image)
    painter.end()
    return flattened


def _temporary_output_path(target_path: str) -> str:
    target = Path(target_path)
    counter = 0
    while True:
        candidate = target.with_name(f".image-triage-resize-{counter}-{target.name}")
        if not candidate.exists():
            return str(candidate)
        counter += 1


def _exif_bytes_for_output(opened) -> bytes | None:
    try:
        exif = opened.getexif()
    except Exception:
        exif = None
    if exif:
        try:
            exif[EXIF_ORIENTATION_TAG] = 1
            return exif.tobytes()
        except Exception:
            pass
    payload = opened.info.get("exif")
    if isinstance(payload, bytes):
        return payload
    return None


def _path_key(path: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(path)))
