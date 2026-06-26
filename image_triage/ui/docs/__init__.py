"""In-app documentation wiki for Image Triage.

The content layer (``model``, ``registry``, ``content``) is Qt-free and can be
imported and tested on its own. The ``browser`` module adds the PySide6 reader
window and is imported lazily so headless tooling does not require Qt.
"""

from __future__ import annotations

from .model import DocArticle, DocCategory
from .registry import DocRegistry, DocSearchHit, get_registry

__all__ = [
    "DocArticle",
    "DocCategory",
    "DocRegistry",
    "DocSearchHit",
    "get_registry",
    "DocsBrowserDialog",
    "open_documentation",
]


def __getattr__(name: str):
    # Lazy Qt import: only pull in the browser widgets when actually requested.
    if name in ("DocsBrowserDialog", "open_documentation"):
        from . import browser

        return getattr(browser, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
