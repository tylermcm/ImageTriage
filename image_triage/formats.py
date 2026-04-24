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

FITS_SUFFIXES = frozenset(
    {
        ".fit",
        ".fit.fz",
        ".fit.gz",
        ".fits",
        ".fits.fz",
        ".fits.gz",
        ".fts",
        ".fts.fz",
        ".fts.gz",
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
IMAGE_SUFFIXES = frozenset(STANDARD_IMAGE_SUFFIXES | FITS_SUFFIXES | RAW_SUFFIXES | PSD_SUFFIXES | MODEL_SUFFIXES)
PILLOW_FALLBACK_SUFFIXES = frozenset(STANDARD_IMAGE_SUFFIXES | PSD_SUFFIXES)
COMPOSITE_SUFFIXES = tuple(sorted((suffix for suffix in IMAGE_SUFFIXES if suffix.count(".") > 1), key=len, reverse=True))

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
    ".fit": 15,
    ".fits": 16,
    ".fts": 17,
    ".fit.fz": 18,
    ".fits.fz": 19,
    ".fts.fz": 20,
    ".fit.gz": 21,
    ".fits.gz": 22,
    ".fts.gz": 23,
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
    ".fit": 27,
    ".fits": 28,
    ".fts": 29,
    ".fit.fz": 30,
    ".fits.fz": 31,
    ".fts.fz": 32,
    ".fit.gz": 33,
    ".fits.gz": 34,
    ".fts.gz": 35,
    ".stl": 36,
}


def suffix_for_path(path: str) -> str:
    lowered = os.path.basename(path).casefold()
    for suffix in COMPOSITE_SUFFIXES:
        if lowered.endswith(suffix):
            return suffix
    return os.path.splitext(lowered)[1]
