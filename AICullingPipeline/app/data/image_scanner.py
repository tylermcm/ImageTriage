"""Filesystem scanning and metadata collection for image inputs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
import itertools
import logging
from pathlib import Path
from typing import Optional

from PIL import ExifTags, Image, UnidentifiedImageError
from tqdm.auto import tqdm

from app.utils.identity_utils import build_stable_image_id


LOGGER = logging.getLogger(__name__)
EXIF_TAG_IDS = {name: tag_id for tag_id, name in ExifTags.TAGS.items()}
EXIF_TIME_TAGS = (
    ("DateTimeOriginal", "exif_datetimeoriginal"),
    ("DateTimeDigitized", "exif_datetimedigitized"),
    ("DateTime", "exif_datetime"),
)


@dataclass
class ImageRecord:
    """Metadata row describing a scanned image and its embedding status."""

    image_id: str
    file_path: str
    relative_path: str
    file_name: str
    width: Optional[int]
    height: Optional[int]
    status: str
    error: str
    capture_timestamp: str = ""
    capture_time_source: str = "missing"
    timestamp_available: bool = False
    embedding_index: Optional[int] = None


def scan_image_directory(
    input_dir: Path,
    supported_extensions: tuple[str, ...],
    *,
    scan_workers: int = 1,
) -> tuple[list[ImageRecord], list[ImageRecord]]:
    """Scan a directory recursively and collect image metadata."""

    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")

    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

    candidate_paths = sorted(
        (
            path
            for path in input_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in supported_extensions
        ),
        key=lambda item: item.relative_to(input_dir).as_posix().casefold(),
    )

    all_records: list[ImageRecord] = []
    valid_records: list[ImageRecord] = []

    if scan_workers <= 1 or len(candidate_paths) <= 1:
        scan_results = (
            _scan_single_image(path, input_dir=input_dir)
            for path in candidate_paths
        )
        progress = tqdm(scan_results, total=len(candidate_paths), desc="Scanning images", unit="image")
        for record, is_valid in progress:
            all_records.append(record)
            if is_valid:
                valid_records.append(record)
    else:
        with ThreadPoolExecutor(max_workers=scan_workers) as executor:
            scan_results = executor.map(
                _scan_single_image,
                candidate_paths,
                itertools.repeat(input_dir),
            )
            progress = tqdm(scan_results, total=len(candidate_paths), desc="Scanning images", unit="image")
            for record, is_valid in progress:
                all_records.append(record)
                if is_valid:
                    valid_records.append(record)

    LOGGER.info(
        "Discovered %s supported files, %s readable and %s skipped.",
        len(all_records),
        len(valid_records),
        len(all_records) - len(valid_records),
    )

    return all_records, valid_records


def _build_image_id(relative_path: str) -> str:
    """Create a deterministic image ID from the relative path."""

    return build_stable_image_id(relative_path)


def _scan_single_image(
    path: Path,
    input_dir: Path,
) -> tuple[ImageRecord, bool]:
    """Inspect one image without fully decoding pixel data."""

    relative_path = path.relative_to(input_dir).as_posix()
    record = ImageRecord(
        image_id=_build_image_id(relative_path),
        file_path=str(path.resolve()),
        relative_path=relative_path,
        file_name=path.name,
        width=None,
        height=None,
        capture_timestamp="",
        capture_time_source="missing",
        timestamp_available=False,
        status="scan_error",
        error="",
        embedding_index=None,
    )

    try:
        # verify() checks integrity without forcing a full pixel decode.
        with Image.open(path) as verification_image:
            verification_image.verify()

        with Image.open(path) as image:
            record.width, record.height = image.size
            capture_time = extract_capture_time_from_image(image)
            if capture_time is not None:
                record.capture_timestamp = capture_time["timestamp"]
                record.capture_time_source = capture_time["source"]
                record.timestamp_available = True
        record.status = "ready"
        return record, True
    except (OSError, UnidentifiedImageError, ValueError, SyntaxError) as exc:
        record.error = str(exc)
        LOGGER.warning("Skipping unreadable image %s: %s", path, exc)
        return record, False


def read_capture_time_from_file(path: Path) -> Optional[dict[str, str]]:
    """Read capture time metadata directly from an image file."""

    try:
        with Image.open(path) as image:
            return extract_capture_time_from_image(image)
    except (OSError, UnidentifiedImageError, ValueError):
        return None


def extract_capture_time_from_image(image: Image.Image) -> Optional[dict[str, str]]:
    """Extract a normalized capture timestamp from EXIF metadata when available."""

    exif = image.getexif()
    if not exif:
        return None

    for tag_name, source_name in EXIF_TIME_TAGS:
        tag_id = EXIF_TAG_IDS.get(tag_name)
        if tag_id is None:
            continue

        raw_value = exif.get(tag_id)
        normalized = _normalize_exif_timestamp(raw_value)
        if normalized is None:
            continue

        return {
            "timestamp": normalized.strftime("%Y-%m-%d %H:%M:%S"),
            "source": source_name,
        }

    return None


def _normalize_exif_timestamp(raw_value: object) -> Optional[datetime]:
    """Parse EXIF datetime strings into normalized datetimes."""

    if raw_value is None:
        return None

    if isinstance(raw_value, bytes):
        raw_text = raw_value.decode("utf-8", errors="ignore").strip("\x00 ").strip()
    else:
        raw_text = str(raw_value).strip()

    if not raw_text:
        return None

    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw_text, fmt)
        except ValueError:
            continue

    LOGGER.debug("Unable to parse EXIF timestamp value: %s", raw_text)
    return None
