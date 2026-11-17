"""Export presets: named settings for the whole Batch Export pipeline (``export_pipeline.DEFAULTS``).

Built-in presets cannot be changed or removed; saving under a built-in's name is refused. A person's presets are kept as JSON in the
app data folder. A preset holds every pipeline setting except where the files go when that is a chosen folder (a preset made on one
computer would otherwise point at a folder another does not have).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import export_pipeline as pipeline

VERSION = 1
MAX_PRESETS = 100
MAX_NAME = 60

BUILTIN: tuple[tuple[str, dict], ...] = (
    ("Full size JPEG", {"sizeMode": "original", "format": "jpg", "quality": "92", "profile": "srgb", "meta": "all"}),
    ("Web 2048 px", {"sizeMode": "long", "long": "2048", "format": "jpg", "quality": "85", "profile": "srgb", "meta": "copyright",
                     "sharpen": "screen"}),
    ("Email 1200 px", {"sizeMode": "long", "long": "1200", "format": "jpg", "quality": "80", "profile": "srgb", "meta": "none",
                       "sharpen": "screen"}),
    ("Instagram portrait 1080 x 1350", {"sizeMode": "fit", "fitW": "1080", "fitH": "1350", "format": "jpg", "quality": "90",
                                         "profile": "srgb", "meta": "copyright", "sharpen": "screen"}),
    ("Print TIFF 16-bit Adobe RGB", {"sizeMode": "original", "format": "tif", "depth": "16", "profile": "adobergb", "meta": "all"}),
    ("Archive TIFF 16-bit ProPhoto", {"sizeMode": "original", "format": "tif", "depth": "16", "profile": "prophoto", "meta": "all"}),
)
BUILTIN_NAMES = frozenset(name for name, _ in BUILTIN)
_PORTABLE = tuple(key for key in pipeline.DEFAULTS if key != "folder")


def storage_path() -> Path:
    from .scan_cache import app_data_root

    return app_data_root() / "export_presets.json"


def _portable(values: dict) -> dict:
    clean = pipeline.normalized(values)
    preset = {key: clean[key] for key in _PORTABLE}
    if preset["dest"] == "choose":
        preset["dest"] = "subfolder"
    return preset


class PresetStore:
    def __init__(self, path: Path | None = None) -> None:
        self._path = Path(path) if path else storage_path()

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("presets"), list):
            return []
        presets = []
        for entry in data["presets"][:MAX_PRESETS]:
            if isinstance(entry, dict) and isinstance(entry.get("name"), str) and entry["name"].strip() and entry["name"] not in BUILTIN_NAMES:
                presets.append({"name": entry["name"].strip()[:MAX_NAME], "values": _portable(entry.get("values"))})
        return presets

    def _write(self, presets: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=str(self._path.parent), suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump({"version": VERSION, "presets": presets}, out, indent=1)
            os.replace(temporary, self._path)
        finally:
            if os.path.exists(temporary):
                os.remove(temporary)

    def presets(self) -> list[dict]:
        """Built-ins first, then the person's own, each ``{"name", "values", "builtin"}``."""
        builtin = [{"name": name, "values": _portable({**pipeline.DEFAULTS, **values}), "builtin": True} for name, values in BUILTIN]
        return builtin + [{**entry, "builtin": False} for entry in self._read()]

    def get(self, name: str) -> dict | None:
        return next((p["values"] for p in self.presets() if p["name"] == name), None)

    def save(self, name: str, values: dict) -> str:
        """Saves (or replaces) a preset; returns why it could not be saved, or ""."""
        name = (name or "").strip()
        if not name:
            return "Give the preset a name."
        if len(name) > MAX_NAME:
            return f"Use a name of up to {MAX_NAME} characters."
        if name in BUILTIN_NAMES:
            return "That name belongs to a built-in preset."
        presets = [p for p in self._read() if p["name"] != name]
        if len(presets) >= MAX_PRESETS:
            return f"There can be up to {MAX_PRESETS} presets."
        presets.append({"name": name, "values": _portable(values)})
        try:
            self._write(presets)
        except OSError as error:
            return f"The preset could not be saved: {error}"
        return ""

    def delete(self, name: str) -> bool:
        presets = self._read()
        kept = [p for p in presets if p["name"] != name]
        if len(kept) == len(presets):
            return False
        self._write(kept)
        return True
