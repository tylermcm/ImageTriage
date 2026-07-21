from __future__ import annotations

import os
import plistlib
import struct
from dataclasses import dataclass
from pathlib import Path


APPLEDOUBLE_MAGIC = 0x00051607
_APPLEDOUBLE_HEADER_SIZE = 26
_APPLEDOUBLE_ENTRY_SIZE = 12
_MAX_APPLEDOUBLE_ENTRIES = 4096


@dataclass(slots=True, frozen=True)
class AppleDoubleSidecar:
    path: str
    version: int
    entry_ids: tuple[int, ...]


@dataclass(slots=True, frozen=True)
class AppleAdjustmentSidecar:
    path: str
    format_identifier: str = ""
    format_version: str = ""
    editor_bundle_id: str = ""
    adjustment_data_size: int = 0


def existing_mac_sidecar_paths(image_path: str | os.PathLike[str]) -> tuple[str, ...]:
    """Return AppleDouble and Apple Photos sidecars paired with an image."""

    source = Path(image_path)
    candidates = (
        source.with_name(f"._{source.name}"),
        source.with_suffix(".AAE"),
        source.with_suffix(".aae"),
    )
    ordered: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = os.path.normpath(str(candidate))
        key = os.path.normcase(normalized)
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        ordered.append(normalized)
    return tuple(ordered)


def read_appledouble_sidecar(sidecar_path: str | os.PathLike[str]) -> AppleDoubleSidecar | None:
    """Read the portable AppleDouble header without interpreting resource data."""

    path = os.path.normpath(os.fspath(sidecar_path))
    try:
        with open(path, "rb") as stream:
            header = stream.read(_APPLEDOUBLE_HEADER_SIZE)
            if len(header) != _APPLEDOUBLE_HEADER_SIZE:
                return None
            magic, version = struct.unpack(">II", header[:8])
            if magic != APPLEDOUBLE_MAGIC:
                return None
            entry_count = struct.unpack(">H", header[24:26])[0]
            if entry_count > _MAX_APPLEDOUBLE_ENTRIES:
                return None
            descriptors = stream.read(entry_count * _APPLEDOUBLE_ENTRY_SIZE)
    except OSError:
        return None
    if len(descriptors) != entry_count * _APPLEDOUBLE_ENTRY_SIZE:
        return None
    entry_ids = tuple(
        struct.unpack(">I", descriptors[offset : offset + 4])[0]
        for offset in range(0, len(descriptors), _APPLEDOUBLE_ENTRY_SIZE)
    )
    return AppleDoubleSidecar(path=path, version=version, entry_ids=entry_ids)


def read_apple_adjustment_sidecar(image_path: str | os.PathLike[str]) -> AppleAdjustmentSidecar | None:
    """Read the public plist envelope from an Apple Photos AAE sidecar."""

    source = Path(image_path)
    for candidate in (source.with_suffix(".AAE"), source.with_suffix(".aae")):
        if not candidate.is_file():
            continue
        try:
            with candidate.open("rb") as stream:
                payload = plistlib.load(stream)
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        if not isinstance(payload, dict):
            continue
        adjustment_data = payload.get("adjustmentData", b"")
        if isinstance(adjustment_data, (bytes, bytearray, str)):
            adjustment_data_size = len(adjustment_data)
        else:
            adjustment_data_size = 0
        return AppleAdjustmentSidecar(
            path=os.path.normpath(str(candidate)),
            format_identifier=str(payload.get("adjustmentFormatIdentifier", "") or ""),
            format_version=str(payload.get("adjustmentFormatVersion", "") or ""),
            editor_bundle_id=str(payload.get("adjustmentEditorBundleID", "") or ""),
            adjustment_data_size=adjustment_data_size,
        )
    return None
