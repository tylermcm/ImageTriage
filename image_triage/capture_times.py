"""When a photo was taken, as one number that orders photos correctly.

Exif stores the capture time as local wall-clock text with whole seconds, the fraction of a second in a second tag and
the camera's time zone in a third. Bursts need the fraction (several frames share a second) and two cameras set to
different zones need the offset, so all three are read. Without an offset the time is taken as it stands, which still
orders one camera's photos correctly.
"""
from __future__ import annotations

import calendar
import re
import time
from pathlib import Path

_STAMP = re.compile(r"^\s*(\d{4})[:\-](\d{2})[:\-](\d{2})[ T](\d{2}):(\d{2}):(\d{2})")
_OFFSET = re.compile(r"^\s*([+-])(\d{2}):?(\d{2})\s*$")
_TAGS = (("EXIF DateTimeOriginal", "EXIF SubSecTimeOriginal", "EXIF OffsetTimeOriginal"),
         ("EXIF DateTimeDigitized", "EXIF SubSecTimeDigitized", "EXIF OffsetTimeDigitized"),
         ("Image DateTime", "EXIF SubSecTime", "EXIF OffsetTime"))


def parse_capture_time(stamp, subsecond="", offset="") -> float | None:
    """Seconds on a common clock for an Exif time, or None when ``stamp`` is not a real date. With an offset the result is
    UTC; without one it is the wall-clock time read as if it were UTC."""
    match = _STAMP.match(str(stamp or ""))
    if not match:
        return None
    year, month, day, hour, minute, second = (int(part) for part in match.groups())
    if not (1 <= month <= 12 and 1 <= day <= 31 and hour < 24 and minute < 60 and second < 61 and year >= 1900):
        return None
    try:
        seconds = float(calendar.timegm((year, month, day, hour, minute, second, 0, 0, 0)))
    except (ValueError, OverflowError):
        return None
    digits = str(subsecond or "").strip()
    if digits.isdigit():
        seconds += int(digits) / 10 ** len(digits)
    zone = _OFFSET.match(str(offset or ""))
    if zone:
        shift = (int(zone.group(2)) * 60 + int(zone.group(3))) * 60
        seconds -= shift if zone.group(1) == "+" else -shift
    return seconds


def read_capture_time(path) -> float | None:
    """The capture time of the file at ``path`` from its Exif data, or None when it has none that can be read."""
    try:
        import exifread

        with open(path, "rb") as handle:
            tags = exifread.process_file(handle, details=False)
    except Exception:  # an unreadable or unsupported file simply has no capture time
        return None
    for stamp, subsecond, offset in _TAGS:
        found = parse_capture_time(tags.get(stamp), tags.get(subsecond, ""), tags.get(offset, ""))
        if found is not None:
            return found
    return None


def capture_or_modified(path, modified_ns: int = 0) -> float:
    """The capture time, or when the file was last changed for a file with no Exif date. The fallback is local time read
    the same way an Exif time without an offset is, so the two kinds sort together sensibly."""
    found = read_capture_time(path)
    if found is not None:
        return found
    if not modified_ns:
        try:
            modified_ns = Path(path).stat().st_mtime_ns
        except OSError:
            return 0.0
    return float(calendar.timegm(time.localtime(modified_ns / 1e9))) + (modified_ns % 1_000_000_000) / 1e9


_CHUNK = re.compile(r"(\d+)")


def natural_key(name: str):
    """Orders names the way people read them: ``IMG_2`` before ``IMG_10``."""
    parts = _CHUNK.split(str(name).casefold())
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in parts if part != ""]
