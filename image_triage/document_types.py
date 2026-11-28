"""The photo types Image Triage tells macOS and Linux desktops it opens, so they offer it for them ("Open With", the default app).

Windows reads the same list from the registry (``file_associations``). macOS reads ``mac_document_types()`` from the app's Info.plist
(``setup_macos.py``): content types where macOS has them (every camera RAW conforms to ``public.camera-raw-image``, so one entry covers
them all), and the RAW extensions macOS may not know by name. Linux desktops read the MIME types of ``LINUX_MIME_TYPES`` from the
installed ``image-triage.desktop`` (``packaging/install_linux.sh``).
"""
from __future__ import annotations

from .formats import RAW_SUFFIXES

# (name shown in Finder's Get Info, the content types). Photoshop documents are left to Photoshop: they open, but as their composite.
MAC_CONTENT_TYPES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("JPEG image", ("public.jpeg",)),
    ("PNG image", ("public.png",)),
    ("TIFF image", ("public.tiff",)),
    ("HEIF image", ("public.heic", "public.heif")),
    ("WebP image", ("org.webmproject.webp",)),
    ("AVIF image", ("public.avif",)),
    ("Camera RAW image", ("public.camera-raw-image", "com.adobe.raw-image")),
    ("GIF image", ("com.compuserve.gif",)),
    ("BMP image", ("com.microsoft.bmp",)),
)

# One per suffix the library opens (``formats``), by the names shared-mime-info gives them. A RAW without a registered MIME type gets
# the generic one most desktops map unknown camera files to.
_RAW_MIME = {
    ".3fr": "image/x-hasselblad-3fr", ".arw": "image/x-sony-arw", ".cr2": "image/x-canon-cr2", ".cr3": "image/x-canon-cr3",
    ".crw": "image/x-canon-crw", ".dcr": "image/x-kodak-dcr", ".dng": "image/x-adobe-dng", ".erf": "image/x-epson-erf",
    ".fff": "image/x-hasselblad-fff", ".iiq": "image/x-phaseone-iiq", ".k25": "image/x-kodak-k25", ".kdc": "image/x-kodak-kdc",
    ".mef": "image/x-mamiya-mef", ".mos": "image/x-leaf-mos", ".mrw": "image/x-minolta-mrw", ".nef": "image/x-nikon-nef",
    ".nrw": "image/x-nikon-nrw", ".orf": "image/x-olympus-orf", ".pef": "image/x-pentax-pef", ".raf": "image/x-fuji-raf",
    ".raw": "image/x-panasonic-raw", ".rw2": "image/x-panasonic-rw2", ".rwl": "image/x-panasonic-rwl", ".sr2": "image/x-sony-sr2",
    ".srf": "image/x-sony-srf", ".srw": "image/x-samsung-srw", ".x3f": "image/x-sigma-x3f",
}
LINUX_MIME_TYPES: tuple[str, ...] = (
    "image/jpeg", "image/png", "image/tiff", "image/webp", "image/heic", "image/heif", "image/avif", "image/gif", "image/bmp",
    "image/x-dcraw", *dict.fromkeys(_RAW_MIME.values()),
)
LINUX_DESKTOP_FILE = "image-triage.desktop"


def mac_document_types() -> list[dict]:
    """``CFBundleDocumentTypes`` for the Info.plist: Image Triage as a viewer of these (an alternate handler: it never claims a type
    on install; the person chooses it, in Finder or in File associations)."""
    types = [{"CFBundleTypeName": name, "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate", "LSItemContentTypes": list(content)}
             for name, content in MAC_CONTENT_TYPES]
    # RAW extensions macOS may have no content type for (it then makes up one per extension): matched by extension
    types.append({"CFBundleTypeName": "Camera RAW file", "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate",
                  "CFBundleTypeExtensions": sorted(suffix.lstrip(".") for suffix in RAW_SUFFIXES)})
    return types


def linux_mime_type_line() -> str:
    """The ``MimeType=`` line of the installed .desktop file."""
    return "MimeType=" + ";".join(LINUX_MIME_TYPES) + ";"


def linux_mime_for_suffix(suffix: str) -> str:
    standard = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".jpe": "image/jpeg", ".jfif": "image/jpeg", ".png": "image/png",
                ".tif": "image/tiff", ".tiff": "image/tiff", ".webp": "image/webp", ".heic": "image/heic", ".heif": "image/heif",
                ".avif": "image/avif", ".gif": "image/gif", ".bmp": "image/bmp"}
    suffix = suffix.lower()
    return standard.get(suffix) or _RAW_MIME.get(suffix) or ("image/x-dcraw" if suffix in RAW_SUFFIXES else "")
