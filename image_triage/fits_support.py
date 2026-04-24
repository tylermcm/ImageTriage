from __future__ import annotations

import gzip
import math
from dataclasses import dataclass

import numpy as np

from .formats import suffix_for_path

_FITS_BLOCK_SIZE = 2880


@dataclass(slots=True, frozen=True)
class BasicFitsImage:
    data: np.ndarray
    header: dict[str, object]


def load_basic_fits_image(path: str) -> tuple[BasicFitsImage | None, str | None]:
    try:
        with _open_fits_stream(path) as stream:
            header, data_offset = _read_header(stream)
            if not header:
                return None, "FITS header was empty."
            image_shape = _image_shape_from_header(header)
            if not image_shape:
                return None, "The FITS file did not contain a standard image HDU."
            bitpix = _coerce_int(header.get("BITPIX"))
            if bitpix is None:
                return None, "Missing BITPIX in FITS header."
            dtype = _dtype_for_bitpix(bitpix)
            if dtype is None:
                return None, f"Unsupported FITS BITPIX value: {bitpix}."
            element_count = math.prod(image_shape)
            stream.seek(data_offset)
            raw = stream.read(element_count * dtype.itemsize)
            if len(raw) < element_count * dtype.itemsize:
                return None, "FITS image data was truncated."
            array = np.frombuffer(raw, dtype=dtype, count=element_count)
            if array.size != element_count:
                return None, "FITS image data was incomplete."
            shaped = array.reshape(tuple(reversed(image_shape)))
            normalized = _apply_scaling(shaped, header)
            return BasicFitsImage(data=np.asarray(normalized), header=header), None
    except OSError as exc:
        return None, str(exc)
    except Exception as exc:  # pragma: no cover - malformed files are runtime-specific
        return None, str(exc)


def load_basic_fits_header(path: str) -> tuple[dict[str, object] | None, str | None]:
    try:
        with _open_fits_stream(path) as stream:
            header, _ = _read_header(stream)
    except OSError as exc:
        return None, str(exc)
    except Exception as exc:  # pragma: no cover - malformed files are runtime-specific
        return None, str(exc)
    if not header:
        return None, "FITS header was empty."
    return header, None


def is_basic_displayable_fits_array(array: np.ndarray) -> bool:
    if array.size <= 0:
        return False
    if array.dtype.names:
        return False
    return array.dtype.kind in {"b", "i", "u", "f", "c"}


def normalize_basic_fits_array(array: np.ndarray) -> np.ndarray:
    working = np.asarray(array)
    if working.dtype.names:
        raise TypeError("Structured FITS tables are not displayable as images.")
    if working.dtype.kind == "c":
        working = np.abs(working)
    return np.asarray(working)


def _open_fits_stream(path: str):
    suffix = suffix_for_path(path)
    if suffix.endswith(".gz"):
        return gzip.open(path, "rb")
    return open(path, "rb")


def _read_header(stream) -> tuple[dict[str, object], int]:
    header: dict[str, object] = {}
    header_bytes = bytearray()
    while True:
        block = stream.read(_FITS_BLOCK_SIZE)
        if not block:
            break
        header_bytes.extend(block)
        for offset in range(0, len(block), 80):
            card = block[offset : offset + 80]
            if len(card) < 80:
                continue
            keyword = card[:8].decode("ascii", errors="ignore").strip()
            if keyword == "END":
                header_size = len(header_bytes)
                return header, header_size
            parsed = _parse_card(card.decode("ascii", errors="ignore"))
            if parsed is None:
                continue
            key, value = parsed
            header[key] = value
    return header, len(header_bytes)


def _parse_card(card: str) -> tuple[str, object] | None:
    keyword = card[:8].strip()
    if not keyword or keyword in {"COMMENT", "HISTORY"}:
        return None
    if card[8:10] != "= ":
        return None
    value_section = card[10:]
    value_text = _split_value_and_comment(value_section).strip()
    return keyword, _parse_value(value_text)


def _split_value_and_comment(value_section: str) -> str:
    in_string = False
    index = 0
    while index < len(value_section):
        char = value_section[index]
        if char == "'":
            if in_string and index + 1 < len(value_section) and value_section[index + 1] == "'":
                index += 2
                continue
            in_string = not in_string
        elif char == "/" and not in_string:
            return value_section[:index]
        index += 1
    return value_section


def _parse_value(value_text: str):
    if not value_text:
        return ""
    if value_text.startswith("'"):
        text = value_text[1:]
        end_index = text.find("'")
        while end_index >= 0 and end_index + 1 < len(text) and text[end_index + 1] == "'":
            text = text[:end_index] + "'" + text[end_index + 2 :]
            end_index = text.find("'", end_index + 1)
        if end_index >= 0:
            return text[:end_index].rstrip()
        return text.rstrip()
    if value_text == "T":
        return True
    if value_text == "F":
        return False
    try:
        if any(marker in value_text for marker in (".", "E", "e", "D", "d")):
            return float(value_text.replace("D", "E").replace("d", "e"))
        return int(value_text)
    except ValueError:
        return value_text.strip()


def _image_shape_from_header(header: dict[str, object]) -> tuple[int, ...] | None:
    naxis = _coerce_int(header.get("NAXIS"))
    if naxis is None or naxis <= 0:
        return None
    shape: list[int] = []
    for axis in range(1, naxis + 1):
        axis_size = _coerce_int(header.get(f"NAXIS{axis}"))
        if axis_size is None or axis_size <= 0:
            return None
        shape.append(axis_size)
    return tuple(shape)


def _dtype_for_bitpix(bitpix: int) -> np.dtype | None:
    mapping = {
        8: np.dtype(">u1"),
        16: np.dtype(">i2"),
        32: np.dtype(">i4"),
        64: np.dtype(">i8"),
        -32: np.dtype(">f4"),
        -64: np.dtype(">f8"),
    }
    return mapping.get(bitpix)


def _apply_scaling(array: np.ndarray, header: dict[str, object]) -> np.ndarray:
    bscale = _coerce_float(header.get("BSCALE"), default=1.0)
    bzero = _coerce_float(header.get("BZERO"), default=0.0)
    if abs(bscale - 1.0) < 1e-9 and abs(bzero) < 1e-9:
        return array
    return array.astype(np.float32) * bscale + bzero


def _coerce_int(value) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_float(value, *, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default
