"""Read the largest embedded JPEG preview and basic EXIF from a Nikon NEF (a TIFF container) with the standard library only.

No raw decoding is done: the preview is the camera's own processed JPEG. read_only: the file is only opened for reading.
"""
from __future__ import annotations

import struct

TYPE_SIZES = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 9: 4, 10: 8}
TAG_SUBIFDS, TAG_JPEG_OFFSET, TAG_JPEG_LENGTH, TAG_EXIF_IFD = 0x014A, 0x0201, 0x0202, 0x8769
TAG_MAKE, TAG_MODEL, TAG_ISO, TAG_EXPOSURE, TAG_FNUMBER = 0x010F, 0x0110, 0x8827, 0x829A, 0x829D


def _read_ifd(handle, offset: int, endian: str) -> dict[int, tuple[int, int, bytes]]:
    handle.seek(offset)
    (count,) = struct.unpack(endian + "H", handle.read(2))
    entries = {}
    for _ in range(count):
        tag, typ, n, value = struct.unpack(endian + "HHI4s", handle.read(12))
        entries[tag] = (typ, n, value)
    return entries


def _values(handle, entry, endian: str) -> list[int]:
    typ, n, raw = entry
    size = TYPE_SIZES.get(typ, 1) * n
    data = raw[:size] if size <= 4 else None
    if data is None:
        (offset,) = struct.unpack(endian + "I", raw)
        here = handle.tell()
        handle.seek(offset)
        data = handle.read(size)
        handle.seek(here)
    code = {1: "B", 3: "H", 4: "I", 7: "B", 9: "i"}.get(typ)
    if code is None:
        return []
    return list(struct.unpack(endian + code * n, data[: struct.calcsize(code) * n]))


def _text(handle, entry, endian: str) -> str:
    typ, n, raw = entry
    if typ != 2:
        return ""
    if n <= 4:
        return raw[:n].split(b"\0")[0].decode("ascii", "ignore")
    (offset,) = struct.unpack(endian + "I", raw)
    here = handle.tell()
    handle.seek(offset)
    data = handle.read(n)
    handle.seek(here)
    return data.split(b"\0")[0].decode("ascii", "ignore")


def read_nef(path: str, want_jpeg: bool = True) -> dict | None:
    """Return {'jpeg': bytes, 'make', 'model', 'iso', 'exposure_s', 'fnumber'} or None if no preview is found."""
    with open(path, "rb") as handle:
        header = handle.read(8)
        if header[:2] == b"II":
            endian = "<"
        elif header[:2] == b"MM":
            endian = ">"
        else:
            return None
        (ifd0_offset,) = struct.unpack(endian + "I", header[4:8])
        ifd0 = _read_ifd(handle, ifd0_offset, endian)
        info = {"make": _text(handle, ifd0[TAG_MAKE], endian) if TAG_MAKE in ifd0 else "",
                "model": _text(handle, ifd0[TAG_MODEL], endian) if TAG_MODEL in ifd0 else "",
                "iso": None, "exposure_s": None, "fnumber": None}
        if TAG_EXIF_IFD in ifd0:
            (exif_offset,) = _values(handle, ifd0[TAG_EXIF_IFD], endian)[:1]
            exif = _read_ifd(handle, exif_offset, endian)
            if TAG_ISO in exif:
                values = _values(handle, exif[TAG_ISO], endian)
                info["iso"] = values[0] if values else None
            for tag, key in ((TAG_EXPOSURE, "exposure_s"), (TAG_FNUMBER, "fnumber")):
                if tag in exif and exif[tag][0] == 5:
                    (offset,) = struct.unpack(endian + "I", exif[tag][2])
                    here = handle.tell()
                    handle.seek(offset)
                    num, den = struct.unpack(endian + "II", handle.read(8))
                    handle.seek(here)
                    info[key] = num / den if den else None
        candidates = []
        ifds = [ifd0]
        if TAG_SUBIFDS in ifd0:
            for offset in _values(handle, ifd0[TAG_SUBIFDS], endian):
                try:
                    ifds.append(_read_ifd(handle, offset, endian))
                except Exception:
                    pass
        next_ptr = None
        for ifd in ifds:
            if TAG_JPEG_OFFSET in ifd and TAG_JPEG_LENGTH in ifd:
                start = _values(handle, ifd[TAG_JPEG_OFFSET], endian)[0]
                length = _values(handle, ifd[TAG_JPEG_LENGTH], endian)[0]
                candidates.append((length, start))
        if not candidates:
            return None
        if not want_jpeg:
            return info
        length, start = max(candidates)
        handle.seek(start)
        data = handle.read(length)
        if data[:2] != b"\xff\xd8":
            return None
        info["jpeg"] = data
        return info


def read_nef_meta(path: str) -> dict | None:
    """Camera model, ISO and exposure only; the preview bytes are not read."""
    return read_nef(path, want_jpeg=False)
