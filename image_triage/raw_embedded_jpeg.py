from __future__ import annotations

from dataclasses import dataclass
import mmap
import os
import struct


@dataclass(slots=True, frozen=True)
class EmbeddedJpeg:
    payload: bytes
    source: str
    offset: int
    byte_count: int


@dataclass(slots=True, frozen=True)
class _Entry:
    value_type: int
    value_count: int
    inline_value: bytes


@dataclass(slots=True, frozen=True)
class _Candidate:
    offset: int
    byte_count: int
    source: str


_TIFF_TYPE_SIZES = {
    1: 1,   # BYTE
    2: 1,   # ASCII
    3: 2,   # SHORT
    4: 4,   # LONG
    7: 1,   # UNDEFINED
    9: 4,   # SLONG
    13: 4,  # IFD
}

_JPEG_INTERCHANGE_FORMAT = 0x0201
_JPEG_INTERCHANGE_FORMAT_LENGTH = 0x0202
_STRIP_OFFSETS = 0x0111
_STRIP_BYTE_COUNTS = 0x0117
_COMPRESSION = 0x0103
_SUB_IFDS = 0x014A
_EXIF_IFD = 0x8769

_JPEG_COMPRESSION_VALUES = {6, 7}
_MAX_IFDS = 48
_MAX_IFD_ENTRIES = 2048
_MAX_ENTRY_VALUES = 64
_MIN_JPEG_BYTES = 256
_MAX_EMBEDDED_JPEG_BYTES = 128 * 1024 * 1024
_MAX_MARKER_SCAN_BYTES = 512 * 1024 * 1024
_MAX_MARKER_SCAN_CANDIDATES = 48


def extract_embedded_jpeg(path: str) -> EmbeddedJpeg | None:
    """Return an embedded preview JPEG from TIFF-style RAW files.

    This is intentionally conservative: it reads TIFF IFD preview offsets only.
    It does not scan the whole RAW file and it does not invoke a RAW decoder.
    """
    try:
        file_size = os.path.getsize(path)
    except OSError:
        return None
    if file_size <= 16:
        return None

    try:
        with open(path, "rb") as stream:
            header = stream.read(16)
            endian = _tiff_endian(header)
            if endian is not None and struct.unpack(endian + "H", header[2:4])[0] == 42:
                first_ifd = struct.unpack(endian + "I", header[4:8])[0]
                candidates = _walk_ifds(stream, file_size, endian, first_ifd)
                for candidate in sorted(candidates, key=lambda item: item.byte_count, reverse=True):
                    payload = _read_jpeg_payload(stream, file_size, candidate.offset, candidate.byte_count)
                    if payload is not None:
                        return EmbeddedJpeg(
                            payload=payload,
                            source=candidate.source,
                            offset=candidate.offset,
                            byte_count=len(payload),
                        )
        marker_candidate = _scan_jpeg_markers(path, file_size)
        if marker_candidate is None:
            return None
        try:
            with open(path, "rb") as stream:
                payload = _read_jpeg_payload(stream, file_size, marker_candidate.offset, marker_candidate.byte_count)
        except OSError:
            return None
        if payload is None:
            return None
        return EmbeddedJpeg(
            payload=payload,
            source=marker_candidate.source,
            offset=marker_candidate.offset,
            byte_count=len(payload),
        )
    except (OSError, struct.error, ValueError):
        return None
    return None


def _tiff_endian(header: bytes) -> str | None:
    if len(header) < 8:
        return None
    if header[:2] == b"II":
        return "<"
    if header[:2] == b"MM":
        return ">"
    return None


def _walk_ifds(stream, file_size: int, endian: str, first_ifd: int) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    pending = [first_ifd]
    seen: set[int] = set()
    while pending and len(seen) < _MAX_IFDS:
        offset = pending.pop(0)
        if offset in seen or offset <= 0 or offset + 2 > file_size:
            continue
        seen.add(offset)
        entries, next_ifd = _read_ifd(stream, file_size, endian, offset)
        if not entries:
            continue
        candidates.extend(_candidates_from_entries(stream, file_size, endian, entries))
        for tag in (_SUB_IFDS, _EXIF_IFD):
            pending.extend(
                child
                for child in _entry_ints(stream, file_size, endian, entries.get(tag), limit=_MAX_ENTRY_VALUES)
                if child not in seen
            )
        if next_ifd and next_ifd not in seen:
            pending.append(next_ifd)
    return candidates


def _read_ifd(stream, file_size: int, endian: str, offset: int) -> tuple[dict[int, _Entry], int]:
    stream.seek(offset)
    count_data = stream.read(2)
    if len(count_data) != 2:
        return {}, 0
    count = struct.unpack(endian + "H", count_data)[0]
    if count <= 0 or count > _MAX_IFD_ENTRIES:
        return {}, 0

    entries_size = count * 12
    entries_start = offset + 2
    next_offset_position = entries_start + entries_size
    if next_offset_position + 4 > file_size:
        return {}, 0

    stream.seek(entries_start)
    data = stream.read(entries_size)
    if len(data) != entries_size:
        return {}, 0

    entries: dict[int, _Entry] = {}
    for index in range(count):
        raw_entry = data[index * 12 : (index + 1) * 12]
        tag, value_type, value_count = struct.unpack(endian + "HHI", raw_entry[:8])
        entries[tag] = _Entry(value_type=value_type, value_count=value_count, inline_value=raw_entry[8:12])

    stream.seek(next_offset_position)
    next_data = stream.read(4)
    next_ifd = struct.unpack(endian + "I", next_data)[0] if len(next_data) == 4 else 0
    return entries, next_ifd


def _candidates_from_entries(stream, file_size: int, endian: str, entries: dict[int, _Entry]) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    offsets = _entry_ints(stream, file_size, endian, entries.get(_JPEG_INTERCHANGE_FORMAT), limit=1)
    lengths = _entry_ints(stream, file_size, endian, entries.get(_JPEG_INTERCHANGE_FORMAT_LENGTH), limit=1)
    if offsets and lengths:
        candidate = _candidate_from_range(file_size, offsets[0], lengths[0], "tiff_jpeg_interchange")
        if candidate is not None:
            candidates.append(candidate)

    compression = _entry_ints(stream, file_size, endian, entries.get(_COMPRESSION), limit=1)
    if compression and compression[0] in _JPEG_COMPRESSION_VALUES:
        strip_offsets = _entry_ints(stream, file_size, endian, entries.get(_STRIP_OFFSETS), limit=_MAX_ENTRY_VALUES)
        strip_lengths = _entry_ints(stream, file_size, endian, entries.get(_STRIP_BYTE_COUNTS), limit=_MAX_ENTRY_VALUES)
        for strip_offset, strip_length in zip(strip_offsets, strip_lengths):
            candidate = _candidate_from_range(file_size, strip_offset, strip_length, "tiff_jpeg_strip")
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _entry_ints(stream, file_size: int, endian: str, entry: _Entry | None, *, limit: int) -> list[int]:
    if entry is None:
        return []
    type_size = _TIFF_TYPE_SIZES.get(entry.value_type)
    if type_size is None or entry.value_count <= 0:
        return []

    count = min(entry.value_count, limit)
    byte_count = entry.value_count * type_size
    if byte_count <= 4:
        data = entry.inline_value[:byte_count]
    else:
        value_offset = struct.unpack(endian + "I", entry.inline_value)[0]
        read_size = count * type_size
        if value_offset <= 0 or value_offset + read_size > file_size:
            return []
        stream.seek(value_offset)
        data = stream.read(read_size)
        if len(data) != read_size:
            return []

    values: list[int] = []
    for index in range(count):
        start = index * type_size
        item = data[start : start + type_size]
        if len(item) != type_size:
            break
        if entry.value_type == 3:
            values.append(struct.unpack(endian + "H", item)[0])
        elif entry.value_type in {4, 13}:
            values.append(struct.unpack(endian + "I", item)[0])
        elif entry.value_type == 9:
            values.append(struct.unpack(endian + "i", item)[0])
        elif entry.value_type == 1:
            values.append(item[0])
    return values


def _candidate_from_range(file_size: int, offset: int, byte_count: int, source: str) -> _Candidate | None:
    if offset <= 0 or byte_count < _MIN_JPEG_BYTES or byte_count > _MAX_EMBEDDED_JPEG_BYTES:
        return None
    if offset >= file_size or offset + byte_count > file_size:
        return None
    return _Candidate(offset=offset, byte_count=byte_count, source=source)


def _read_jpeg_payload(stream, file_size: int, offset: int, byte_count: int) -> bytes | None:
    if offset <= 0 or byte_count <= 0 or offset + byte_count > file_size:
        return None
    stream.seek(offset)
    payload = stream.read(byte_count)
    if len(payload) != byte_count:
        return None
    start = payload.find(b"\xff\xd8\xff")
    if start < 0:
        return None
    if start:
        payload = payload[start:]
    end = payload.find(b"\xff\xd9")
    if end >= 0:
        payload = payload[: end + 2]
    if len(payload) < _MIN_JPEG_BYTES:
        return None
    return payload


def _scan_jpeg_markers(path: str, file_size: int) -> _Candidate | None:
    if file_size <= 0 or file_size > _MAX_MARKER_SCAN_BYTES:
        return None
    candidates: list[_Candidate] = []
    try:
        with open(path, "rb") as stream:
            with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mapped:
                search_from = 0
                while len(candidates) < _MAX_MARKER_SCAN_CANDIDATES:
                    start = mapped.find(b"\xff\xd8\xff", search_from)
                    if start < 0:
                        break
                    end = mapped.find(b"\xff\xd9", start + 4)
                    if end < 0:
                        break
                    end += 2
                    byte_count = end - start
                    if _MIN_JPEG_BYTES <= byte_count <= _MAX_EMBEDDED_JPEG_BYTES:
                        candidates.append(_Candidate(offset=start, byte_count=byte_count, source="jpeg_marker_scan"))
                    search_from = end
    except (OSError, ValueError):
        return None
    if not candidates:
        return None
    return max(candidates, key=lambda candidate: candidate.byte_count)
