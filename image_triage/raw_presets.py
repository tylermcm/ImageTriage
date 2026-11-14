"""Camera Raw settings that can move between photos: copy and paste, and saved presets.

What moves is the look (tone, colour, curves, detail, effects). What stays is anything tied to one photo's pixels or
framing: masks, retouching, red-eye corrections, the crop, Light Depth and Super Resolution.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PHOTO_SPECIFIC = frozenset(("masks", "retouch", "redEye", "hostCrop", "lightDepth", "superResolution", "seed"))
VERSION = 1
MAX_PRESETS = 200
MAX_NAME = 64


def transferable(settings: dict) -> dict:
    """The part of a photo's settings that makes sense on another photo."""
    return {key: value for key, value in settings.items() if key not in PHOTO_SPECIFIC}


def usable(settings) -> bool:
    """Whether ``settings`` is a look this build can apply: every value passes the same check a saved recipe does."""
    from . import photocraft_bridge as bridge

    if not isinstance(settings, dict) or PHOTO_SPECIFIC & settings.keys():
        return False
    return bridge.classify_recipe({"version": bridge.RECIPE_VERSION, "cameraRaw": settings})[0] != bridge.RECIPE_INVALID


def with_look(settings: dict, look: dict, saved: dict, defaults: dict) -> dict:
    """``settings`` with its look replaced by ``look``. What belongs to the photo is kept. ``saved`` is the edit on disk:
    a setting it has must be stated in the result even at its default, or saving would not change it back."""
    from copy import deepcopy

    result = {key: value for key, value in settings.items() if key in PHOTO_SPECIFIC}
    for key, value in look.items():
        # Settings the panel has no control for (written by the hosted editor) travel too.
        if key not in defaults or value != defaults[key]:
            result[key] = deepcopy(value)
    for key in saved:
        if key not in result and key in defaults and key not in PHOTO_SPECIFIC:
            result[key] = deepcopy(defaults[key])
    return result


def clean_name(name) -> str:
    return " ".join(str(name).split())[:MAX_NAME] if isinstance(name, str) else ""


def default_file() -> Path:
    from .scan_cache import app_data_root

    return app_data_root() / "raw_presets.json"


class PresetStore:
    """Named looks in one small file in the application's data folder. Names are unique without regard to case; saving
    under an existing name replaces it."""

    def __init__(self, path: Path | None = None):
        self._path = path
        self._presets: list[dict] | None = None

    def path(self) -> Path:
        if self._path is None:
            self._path = default_file()
        return self._path

    def presets(self) -> list[dict]:
        if self._presets is None:
            self._presets = self._read()
        return [dict(preset) for preset in self._presets]

    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("presets"), list):
            return []
        found, seen = [], set()
        for item in data["presets"][:MAX_PRESETS]:
            name = clean_name(item.get("name")) if isinstance(item, dict) else ""
            # A preset this build cannot apply is left out of the list, not out of the file: see ``_write``.
            if name and name.casefold() not in seen and usable(item.get("settings")):
                seen.add(name.casefold())
                found.append({"name": name, "settings": item["settings"]})
        return found

    def get(self, name: str):
        wanted = clean_name(name).casefold()
        return next((dict(p["settings"]) for p in self.presets() if p["name"].casefold() == wanted), None)

    def save(self, name: str, settings: dict) -> str:
        """Keep ``settings`` as ``name``. Returns "" or why it could not be kept."""
        name, settings = clean_name(name), transferable(settings)
        if not name:
            return "Give the preset a name."
        if not usable(settings):
            return "These settings cannot be saved as a preset."
        presets = [p for p in self.presets() if p["name"].casefold() != name.casefold()]
        if len(presets) >= MAX_PRESETS:
            return f"Delete a preset first: {MAX_PRESETS} is the most that can be kept."
        presets.append({"name": name, "settings": settings})
        presets.sort(key=lambda p: p["name"].casefold())
        return self._write(presets)

    def delete(self, name: str) -> str:
        wanted = clean_name(name).casefold()
        presets = [p for p in self.presets() if p["name"].casefold() != wanted]
        return "" if len(presets) == len(self.presets()) else self._write(presets)

    def _write(self, presets) -> str:
        target = self.path()
        data = json.dumps({"version": VERSION, "presets": presets}, ensure_ascii=False, indent=1).encode("utf-8")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            handle, scratch = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=target.parent)
            try:
                with os.fdopen(handle, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(scratch, target)
            except BaseException:
                Path(scratch).unlink(missing_ok=True)
                raise
        except OSError as error:
            return f"The preset could not be saved: {error.strerror or error}"
        self._presets = presets
        return ""
