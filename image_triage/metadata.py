from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from queue import Empty, SimpleQueue

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from .models import ImageRecord

try:
    import exifread
except ImportError:  # pragma: no cover - optional dependency at runtime
    exifread = None


@dataclass(slots=True, frozen=True)
class CaptureMetadata:
    path: str
    exposure: str = ""
    aperture: str = ""
    iso: str = ""
    focal_length: str = ""
    lens: str = ""
    captured_at: str = ""
    exposure_seconds: float | None = None
    aperture_value: float | None = None
    iso_value: float | None = None
    focal_length_value: float | None = None
    captured_at_value: datetime | None = None
    width: int = 0
    height: int = 0

    @property
    def summary(self) -> str:
        parts = [self.exposure, self.aperture, self.iso, self.focal_length]
        return "  |  ".join(part for part in parts if part)

    @property
    def detail(self) -> str:
        parts = [self.lens, self.captured_at]
        return "  |  ".join(part for part in parts if part)

    @property
    def display_text(self) -> str:
        parts = [self.summary, self.detail]
        return "\n".join(part for part in parts if part)


EMPTY_METADATA = CaptureMetadata(path="")


@dataclass(slots=True, frozen=True)
class MetadataKey:
    path: str
    modified_ns: int
    file_size: int


def load_capture_metadata(path: str) -> CaptureMetadata:
    if exifread is None:
        return EMPTY_METADATA if not path else CaptureMetadata(path=path)

    try:
        with open(path, "rb") as stream:
            tags = exifread.process_file(stream, details=False)
    except OSError:
        return CaptureMetadata(path=path)
    except Exception:  # pragma: no cover - parser/runtime path
        return CaptureMetadata(path=path)

    return CaptureMetadata(
        path=path,
        exposure=_format_exposure(exposure_value := _tag_value(tags, "EXIF ExposureTime")),
        aperture=_format_aperture(aperture_value := _tag_value(tags, "EXIF FNumber")),
        iso=_format_iso(
            iso_value := (
                _tag_value(tags, "EXIF ISOSpeedRatings")
                or _tag_value(tags, "EXIF PhotographicSensitivity")
            )
        ),
        focal_length=_format_focal_length(focal_value := _tag_value(tags, "EXIF FocalLength")),
        lens=_format_text(
            _tag_value(tags, "EXIF LensModel")
            or _tag_value(tags, "MakerNote LensType")
            or _tag_value(tags, "MakerNote Lens")
        ),
        captured_at=_format_datetime(captured_at_value := _tag_value(tags, "EXIF DateTimeOriginal")),
        exposure_seconds=_to_float(exposure_value),
        aperture_value=_to_float(aperture_value),
        iso_value=_first_numeric(iso_value),
        focal_length_value=_to_float(focal_value),
        captured_at_value=_parse_datetime_value(captured_at_value),
    )


class MetadataTask(QRunnable):
    def __init__(self, key: MetadataKey, result_queue: SimpleQueue) -> None:
        super().__init__()
        self.key = key
        self.result_queue = result_queue
        self.setAutoDelete(True)

    def run(self) -> None:
        metadata = load_capture_metadata(self.key.path)
        self.result_queue.put((self.key, metadata))


class MetadataManager(QObject):
    metadata_ready = Signal(object, object)

    def __init__(self, max_workers: int = 1, parent=None) -> None:
        super().__init__(parent)
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max_workers)
        self._cache: dict[MetadataKey, CaptureMetadata] = {}
        self._pending: set[MetadataKey] = set()
        self._result_queue: SimpleQueue = SimpleQueue()
        self._drain_timer = QTimer(self)
        self._drain_timer.setInterval(20)
        self._drain_timer.timeout.connect(self._drain_results)

    def make_key(self, record: ImageRecord) -> MetadataKey:
        return MetadataKey(path=record.path, modified_ns=record.modified_ns, file_size=record.size)

    def get_cached(self, record: ImageRecord) -> CaptureMetadata | None:
        return self._cache.get(self.make_key(record))

    def request_metadata(self, record: ImageRecord, priority: int = 0) -> MetadataKey:
        key = self.make_key(record)
        if key in self._cache or key in self._pending:
            return key
        self._pending.add(key)
        self.pool.start(MetadataTask(key, self._result_queue), priority)
        if not self._drain_timer.isActive():
            self._drain_timer.start()
        return key

    def _drain_results(self) -> None:
        processed = 0
        while processed < 24:
            try:
                key, metadata = self._result_queue.get_nowait()
            except Empty:
                break
            self._pending.discard(key)
            self._cache[key] = metadata
            self.metadata_ready.emit(key, metadata)
            processed += 1

        if not self._pending and processed == 0:
            self._drain_timer.stop()


def _tag_value(tags: dict, key: str):
    tag = tags.get(key)
    if tag is None:
        return None
    values = getattr(tag, "values", None)
    if values:
        if len(values) == 1:
            return values[0]
        return values
    printable = getattr(tag, "printable", "")
    return printable or None


def _to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if hasattr(value, "num") and hasattr(value, "den"):
        denominator = getattr(value, "den", 0) or 0
        if denominator == 0:
            return None
        return float(value.num) / float(denominator)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _format_exposure(value) -> str:
    exposure = _to_float(value)
    if exposure is None or exposure <= 0:
        return ""
    if exposure >= 1:
        if exposure.is_integer():
            return f"{int(exposure)}s"
        return f"{exposure:.1f}s"
    denominator = max(1, round(1 / exposure))
    return f"1/{denominator}s"


def _format_aperture(value) -> str:
    aperture = _to_float(value)
    if aperture is None or aperture <= 0:
        return ""
    return f"f/{aperture:.1f}".rstrip("0").rstrip(".")


def _format_iso(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list) and value:
        value = value[0]
    iso = _to_float(value)
    if iso is None or iso <= 0:
        return ""
    return f"ISO {int(round(iso))}"


def _format_focal_length(value) -> str:
    focal_length = _to_float(value)
    if focal_length is None or focal_length <= 0:
        return ""
    whole = round(focal_length)
    if abs(focal_length - whole) < 0.05:
        return f"{whole}mm"
    return f"{focal_length:.1f}mm".rstrip("0").rstrip(".")


def _format_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        value = ", ".join(str(part) for part in value if str(part))
    text = str(value).strip()
    return text if text else ""


def _format_datetime(value) -> str:
    text = _format_text(value)
    if not text:
        return ""
    parsed = _parse_datetime_text(text)
    if parsed is not None:
        return parsed.strftime("%Y-%m-%d %H:%M")
    return text


def _parse_datetime_value(value) -> datetime | None:
    text = _format_text(value)
    if not text:
        return None
    return _parse_datetime_text(text)


def _parse_datetime_text(text: str) -> datetime | None:
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _first_numeric(value) -> float | None:
    if isinstance(value, list) and value:
        return _to_float(value[0])
    return _to_float(value)
