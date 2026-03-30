from __future__ import annotations

import os


JPEG_SUFFIXES = frozenset(
    {
        ".jpe",
        ".jpeg",
        ".jfif",
        ".jpg",
    }
)

STANDARD_IMAGE_SUFFIXES = frozenset(
    {
        ".avif",
        ".bmp",
        ".dib",
        ".gif",
        ".heic",
        ".heif",
        ".icns",
        ".ico",
        ".jpe",
        ".jpeg",
        ".jfif",
        ".jpg",
        ".jxl",
        ".pbm",
        ".pgm",
        ".png",
        ".pnm",
        ".ppm",
        ".tga",
        ".tif",
        ".tiff",
        ".wbmp",
        ".webp",
        ".xbm",
        ".xpm",
    }
)

RAW_SUFFIXES = frozenset(
    {
        ".3fr",
        ".ari",
        ".arw",
        ".bay",
        ".bmq",
        ".cap",
        ".cr2",
        ".cr3",
        ".crw",
        ".cs1",
        ".dc2",
        ".dcr",
        ".dng",
        ".drf",
        ".erf",
        ".fff",
        ".gpr",
        ".iiq",
        ".k25",
        ".kdc",
        ".mdc",
        ".mef",
        ".mos",
        ".mrw",
        ".nef",
        ".nrw",
        ".obm",
        ".orf",
        ".pef",
        ".ptx",
        ".pxn",
        ".raf",
        ".raw",
        ".rdc",
        ".rw2",
        ".rwl",
        ".sr2",
        ".srf",
        ".srw",
        ".x3f",
    }
)

PSD_SUFFIXES = frozenset({".psb", ".psd"})
MODEL_SUFFIXES = frozenset({".stl"})

EDIT_SUFFIXES = frozenset((STANDARD_IMAGE_SUFFIXES | PSD_SUFFIXES | {".dng"}))
IMAGE_SUFFIXES = frozenset(STANDARD_IMAGE_SUFFIXES | RAW_SUFFIXES | PSD_SUFFIXES | MODEL_SUFFIXES)
PILLOW_FALLBACK_SUFFIXES = frozenset(STANDARD_IMAGE_SUFFIXES | PSD_SUFFIXES)

EDIT_PRIORITY = {
    ".jpg": 0,
    ".jpeg": 1,
    ".jpe": 2,
    ".jfif": 3,
    ".png": 4,
    ".webp": 5,
    ".avif": 6,
    ".heif": 7,
    ".heic": 8,
    ".jxl": 9,
    ".tif": 10,
    ".tiff": 11,
    ".dng": 12,
    ".psd": 13,
    ".psb": 14,
}

ROOT_PRIMARY_PRIORITY = {
    ".jpg": 0,
    ".jpeg": 1,
    ".jpe": 2,
    ".jfif": 3,
    ".png": 4,
    ".tif": 5,
    ".tiff": 6,
    ".webp": 7,
    ".avif": 8,
    ".heif": 9,
    ".heic": 10,
    ".bmp": 11,
    ".dib": 12,
    ".gif": 13,
    ".tga": 14,
    ".ico": 15,
    ".icns": 16,
    ".jxl": 17,
    ".pbm": 18,
    ".pgm": 19,
    ".pnm": 20,
    ".ppm": 21,
    ".wbmp": 22,
    ".xbm": 23,
    ".xpm": 24,
    ".psd": 25,
    ".psb": 26,
    ".stl": 27,
}


def suffix_for_path(path: str) -> str:
    return os.path.splitext(path)[1].lower()

