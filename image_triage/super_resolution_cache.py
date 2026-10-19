"""A photo's finished Super Resolution render, kept beside its other edit data so a second output does not repeat a minute of AI work.

One entry per photo, in the edits folder: ``<name>.superres.png`` (or ``.tif``) and ``<name>.superres.json``. The entry is for exactly one
state of the photo: the key covers the Camera Raw settings, the source file's signature and the model, so any change to any of them is a
miss and the render is made again (and replaces the entry). A miss is never an error; callers fall back to making the render.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

from . import photocraft_bridge as bridge
from . import upscale_core as core
from .full_render import RENDERED, RenderResult

SUPER_RESOLUTION_MODEL = "realesrgan-x2plus"
log = logging.getLogger(__name__)


def cache_key(image_path: str, recipe: dict) -> str | None:
    """The state the render is for; ``None`` when the photo cannot be identified (no cache then)."""
    settings = recipe.get("cameraRaw") if isinstance(recipe, dict) else None
    signature = bridge.file_signature(image_path)
    if not isinstance(settings, dict) or signature is None:
        return None
    payload = {"settings": settings, "source": signature, "model": core.get_spec(SUPER_RESOLUTION_MODEL).sha256}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _paths(image_path: str, suffix: str = ".png") -> tuple[Path, Path]:
    root = bridge.edit_root_for(Path(image_path).parent)
    name = Path(image_path).name
    return root / f"{name}.superres{suffix}", root / f"{name}.superres.json"


def lookup(image_path: str, recipe: dict) -> RenderResult | None:
    """The cached render for this exact state, or ``None``."""
    key = cache_key(image_path, recipe)
    if key is None:
        return None
    _, manifest_path = _paths(image_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        picture = manifest_path.with_name(str(manifest["file"]))
        if manifest.get("key") != key or picture.parent != manifest_path.parent or not picture.is_file() or picture.stat().st_size != manifest["size"]:
            return None
        return RenderResult(
            RENDERED, path=picture, format=str(manifest["format"]), width=int(manifest["width"]), height=int(manifest["height"]),
            depth=8, icc=bool(manifest["icc"]), temporary=False,
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(image_path: str, recipe: dict, result: RenderResult) -> RenderResult:
    """Keep a freshly made Super Resolution render (a temporary file) as the photo's cache entry, replacing any older one, and return the
    result to use in its place (the cached file, which the caller must not delete). Anything that goes wrong leaves ``result`` as it was."""
    key = cache_key(image_path, recipe)
    if key is None or result.outcome != RENDERED or result.path is None or not result.temporary:
        return result
    suffix = ".tif" if result.format == "tiff" else ".png"
    picture, manifest_path = _paths(image_path, suffix)
    try:
        picture.parent.mkdir(parents=True, exist_ok=True)
        staging = picture.with_name(picture.name + ".partial")
        shutil.copyfile(result.path, staging)
        # The manifest goes first (to a name no reader trusts) and the picture last, so a half-finished store is never a hit.
        for other in (".png", ".tif"):
            if other != suffix:
                _paths(image_path, other)[0].unlink(missing_ok=True)
        os.replace(staging, picture)
        manifest = {"key": key, "file": picture.name, "size": picture.stat().st_size, "format": result.format or suffix.lstrip("."),
                    "width": result.width, "height": result.height, "icc": bool(result.icc)}
        temporary_manifest = manifest_path.with_name(manifest_path.name + ".partial")
        temporary_manifest.write_text(json.dumps(manifest), encoding="utf-8")
        os.replace(temporary_manifest, manifest_path)
    except OSError as error:
        log.warning("Could not keep the Super Resolution render of %s: %s", image_path, error)
        return result
    result.release()
    return RenderResult(RENDERED, path=picture, format=manifest["format"], width=result.width, height=result.height, depth=8,
                        icc=bool(result.icc), temporary=False)
