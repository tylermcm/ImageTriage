"""Launches a PhotoCraft process, drives it over its JSON-lines control channel
(docs/control-protocol.md in the PhotoCraft repo), and embeds its native window
as a Win32 child so it can sit inside the popout preview's editor pane.

Windows-only native window hosting for the QML shell's "Edit in PhotoCraft"
feature. The QML editor host owns the visible application state.
"""
from __future__ import annotations

from .camera_raw_recipe_masks import masks_valid as _masks_valid, masks_active as _masks_active

import ctypes
import ctypes.wintypes as wintypes
import hashlib
import json
import logging
import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

from .edit_storage import edit_root_for
from .formats import RAW_SUFFIXES
from .shell_actions import companion_photocraft_executables, detect_photocraft_executable

# Formats PhotoCraft can open directly (docs/README "Formats" section). Anything
# else must be decoded first, except RAW, which the editor develops itself.
NATIVE_SUFFIXES = {
    ".psd", ".psb", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".gif",
    ".bmp", ".tga", ".ico", ".qoi", ".pnm", ".exr", ".hdr", ".avif", ".heic", ".pcraft",
}

_GWL_STYLE = -16
_WS_CHILD = 0x40000000
_WS_POPUP = 0x80000000
_WS_CAPTION = 0x00C00000
_WS_THICKFRAME = 0x00040000
_WS_SYSMENU = 0x00080000
_WS_MINIMIZEBOX = 0x00020000
_WS_MAXIMIZEBOX = 0x00010000
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020


class PhotoCraftError(RuntimeError):
    """A PhotoCraft launch, control-channel, or embedding failure."""


class PhotoCraftCompatibilityError(PhotoCraftError):
    """The selected binary cannot implement the hosted handoff protocol."""


class PhotoCraftSuperseded(PhotoCraftError):
    """An open was abandoned because the selection moved on before it started."""


def path_needs_conversion(path: str) -> bool:
    return Path(path).suffix.lower() not in NATIVE_SUFFIXES | RAW_SUFFIXES


def sidecar_pcraft_path(image_path: str) -> Path:
    """Where a photo's non-destructive PhotoCraft project lives: a ``.pcraft``
    file in the same hidden per-folder edit root ``edit_storage`` uses for the
    built-in editor's sidecars, so both conventions stay out of the user's
    photo folders and travel together with the rest of a folder's edit state.
    """
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".pcraft")


def rendered_preview_path(image_path: str) -> Path:
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".photocraft.png")


RECIPE_VERSION = 1


def file_signature(path: str) -> str | None:
    """``"<bytes>:<mtime_ns>"`` for the file at ``path``, the identity the editor checks a prepared raw against."""
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def recipe_path(image_path: str) -> Path:
    """Where a photo's Camera Raw recipe lives: beside its other edit data, keyed by the photo's name."""
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".cameraraw.json")


RECIPE_SHAPE_OK = "ok"
RECIPE_SHAPE_NEWER = "newer"      # written by a later version of the recipe format: good data this build cannot read
RECIPE_SHAPE_DAMAGED = "damaged"  # not a recipe at all


def recipe_shape(recipe) -> str:
    """Whether parsed JSON is a recipe of this version, of a later one, or not a recipe. The one place that decides it:
    ``load_recipe`` and ``classify_recipe`` both ask here, so they cannot disagree about the same file."""
    if not isinstance(recipe, dict):
        return RECIPE_SHAPE_DAMAGED
    version = recipe.get("version")
    if type(version) is not int:
        return RECIPE_SHAPE_DAMAGED
    if version > RECIPE_VERSION:
        return RECIPE_SHAPE_NEWER
    if version != RECIPE_VERSION or not isinstance(recipe.get("cameraRaw"), dict):
        return RECIPE_SHAPE_DAMAGED
    return RECIPE_SHAPE_OK


RECIPE_NEWER_REASON = "the Camera Raw settings were saved by a newer version of Image Triage"


def load_recipe(image_path: str) -> dict | None:
    """The photo's stored recipe for opening it in an editor, or ``None`` when it has none or the file is unusable.

    Decided by the same reading and the same validator as ``recipe_state`` (``_read_recipe``), so the two cannot disagree
    about a file. A recipe that validator refuses (not JSON, not a recipe, or a recipe with a setting this build cannot
    apply) is set aside, never deleted, so a bad write can never make a photo unopenable: the photo then opens from its
    original develop, ``take_quarantine_notice`` says once what happened and where the copy is, and
    ``recover_quarantined_recipe`` can bring back whatever of it is still good. A recipe from a later version of the
    format is not damaged and is left exactly where it is: setting it aside would hide the user's edits from the newer
    build that can read them (``recipe_is_newer`` tells a caller that this is why it got ``None``).
    """
    _state, recipe, reason, problem = _read_recipe(image_path)
    if problem == _READ_REFUSED:
        quarantine_recipe(image_path, reason)
    elif problem == _READ_FAILED:
        logging.getLogger(__name__).warning("Could not read the Camera Raw recipe %s: %s", recipe_path(image_path), reason)
    return recipe


def recipe_is_newer(image_path: str) -> bool:
    """Whether the photo's stored recipe is from a later version of the format than this build reads."""
    try:
        return recipe_shape(json.loads(recipe_path(image_path).read_text(encoding="utf-8"))) == RECIPE_SHAPE_NEWER
    except (OSError, ValueError):
        return False


# What a photo's Camera Raw recipe amounts to (``recipe_state``).
RECIPE_ABSENT = "absent"
RECIPE_IDENTITY = "identity"  # there, but its settings change nothing
RECIPE_EDITED = "edited"
RECIPE_INVALID = "invalid"  # there, but unreadable: edit data we could not read, never "no edits"

# The develop refuses an exposure outside this (photocraft_io::raw::import_raw_tuned); every other setting is clamped where it is
# applied, so the editor has no other ranges to keep in step with.
RECIPE_EXPOSURE_LIMIT = 10.0
RECIPE_ACTIVE_FIELDS = (
    "temperature", "tint", "exposure", "contrast", "highlights", "shadows", "whites", "blacks", "texture", "clarity", "dehaze",
    "vibrance", "saturation", "curveHighlights", "curveLights", "curveDarks", "curveShadows", "sharpenAmount", "noiseLuminance",
    "noiseColor", "grainAmount", "vignetteAmount", "calibShadowTint",
)
RECIPE_SCALARS = frozenset((*RECIPE_ACTIVE_FIELDS, "gradeBlending", "gradeBalance", "sharpenRadius", "sharpenDetail", "sharpenMasking",
                          "noiseLuminanceDetail", "noiseColorDetail", "grainSize", "grainRoughness", "vignetteMidpoint",
                          "vignetteRoundness", "vignetteFeather", "vignetteHighlights", "pixelScale", "encodingGamma"))
RECIPE_ARRAYS = {"curveSplits": 3, "hslHue": 8, "hslSat": 8, "hslLum": 8, "calibHue": 3, "calibSat": 3}
RECIPE_CALIBRATION_FIELDS = frozenset(("calibShadowTint", "calibHue", "calibSat"))
RECIPE_CURVES = ("pointCurve", "pointCurveRed", "pointCurveGreen", "pointCurveBlue")
RECIPE_WHEELS = ("gradeShadows", "gradeMidtones", "gradeHighlights", "gradeGlobal")
RECIPE_FIELDS = RECIPE_SCALARS | frozenset(RECIPE_ARRAYS) | frozenset(RECIPE_CURVES) | frozenset(RECIPE_WHEELS) | {"seed", "vignetteStyle", "glow", "pointColors", "superResolution", "masks", "hostCrop", "retouch", "redEye"}


def _recipe_value_ok(value) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == value and abs(value) <= 3.4028234663852886e38
    return False


def require_finishing_support(camera_raw, supported: bool) -> None:
    """Refuse an older editor before it can silently discard new saved adjustments."""
    recipe = camera_raw.get("recipe") if isinstance(camera_raw, dict) else None
    settings = recipe.get("cameraRaw", {}) if isinstance(recipe, dict) else {}
    if isinstance(settings, dict) and ("glow" in settings or "pointColors" in settings) and not supported:
        raise PhotoCraftCompatibilityError("This photo uses Glow or Point Color. Select the main-adjustments PhotoCraft test build to preserve those edits.")


def require_calibration_support(camera_raw, supported: bool) -> None:
    """Keep older editors from discarding serialized Calibration settings."""
    recipe = camera_raw.get("recipe") if isinstance(camera_raw, dict) else None
    settings = recipe.get("cameraRaw", {}) if isinstance(recipe, dict) else {}
    if isinstance(settings, dict) and RECIPE_CALIBRATION_FIELDS.intersection(settings) and not supported:
        raise PhotoCraftCompatibilityError("This photo uses Calibration settings. Select a PhotoCraft build with Calibration support to preserve those edits.")


def require_red_eye_support(camera_raw, supported: bool) -> None:
    recipe = camera_raw.get("recipe") if isinstance(camera_raw, dict) else None
    settings = recipe.get("cameraRaw", {}) if isinstance(recipe, dict) else {}
    if isinstance(settings, dict) and settings.get("redEye") and not supported:
        raise PhotoCraftCompatibilityError("This photo uses Red Eye corrections. Select the updated PhotoCraft build to preserve them.")


def require_retouch_support(camera_raw, supported: bool) -> None:
    recipe = camera_raw.get("recipe") if isinstance(camera_raw, dict) else None
    settings = recipe.get("cameraRaw", {}) if isinstance(recipe, dict) else {}
    if isinstance(settings,dict) and settings.get("retouch") and not supported:
        raise PhotoCraftCompatibilityError("This photo uses RAW retouch strokes. Select the updated PhotoCraft build to preserve them.")


def require_masks_support(camera_raw, supported: bool) -> None:
    """Reject an older editor before it can discard a photo's local mask settings."""
    recipe = camera_raw.get("recipe") if isinstance(camera_raw, dict) else None
    settings = recipe.get("cameraRaw", {}) if isinstance(recipe, dict) else {}
    if isinstance(settings, dict) and settings.get("masks") and not supported:
        raise PhotoCraftCompatibilityError("This photo uses local masks. Select a PhotoCraft build with local mask support to preserve those edits.")


def require_super_resolution_support(camera_raw, supported: bool) -> None:
    """Keep an editor that does not know Super Resolution from discarding the setting when it saves the recipe back."""
    recipe = camera_raw.get("recipe") if isinstance(camera_raw, dict) else None
    settings = recipe.get("cameraRaw", {}) if isinstance(recipe, dict) else {}
    if isinstance(settings, dict) and settings.get("superResolution") is True and not supported:
        raise PhotoCraftCompatibilityError("This photo uses Super Resolution. Select a PhotoCraft build with Super Resolution support to preserve that setting.")


def _setting_problem(name, value) -> str | None:
    """Why one Camera Raw setting cannot be used, or ``None`` when it can. The only place a setting's name, type and range
    are judged: ``classify_recipe`` (and through it ``load_recipe`` and ``recipe_state``) refuses a recipe on its answer, and
    ``recover_quarantined_recipe`` leaves out exactly the settings it refuses."""
    if name not in RECIPE_FIELDS:
        return f"the recipe's {name} is not a supported setting"
    if name == "vignetteStyle":
        valid = value in ("highlightPriority", "colorPriority", "paintOverlay") if isinstance(value, str) else False
    elif name in RECIPE_CALIBRATION_FIELDS:
        values = [value] if name == "calibShadowTint" else value
        valid = (isinstance(values, list)
                 and len(values) == (1 if name == "calibShadowTint" else 3)
                 and all(_recipe_value_ok(v) and -100 <= v <= 100 for v in values))
    elif name == "hostCrop":
        from .raw_crop import valid as crop_valid
        valid = crop_valid(value)
    elif name == "redEye":
        from .raw_red_eye import valid as red_eye_valid
        valid = red_eye_valid(value)
    elif name == "retouch":
        from .raw_retouch import valid as retouch_valid
        valid = retouch_valid(value)
    elif name == "masks":
        valid = _masks_valid(value)
    elif name == "superResolution":
        valid = isinstance(value, bool)
    elif name == "glow":
        ranges = {"amount": (0, 100), "range": (0, 100), "spread": (0, 100), "warmth": (-100, 100)}
        valid = isinstance(value, dict) and all(
            (k == "style" and isinstance(v, str) and v in ("diffusion", "bloom", "halation"))
            or (k in ranges and _recipe_value_ok(v) and ranges[k][0] <= v <= ranges[k][1])
            for k, v in value.items())
    elif name == "pointColors":
        valid = isinstance(value, list) and len(value) <= 8 and all(
            isinstance(sample, dict) and all(
                (k == "color" and isinstance(v, list) and len(v) == 3 and all(_recipe_value_ok(c) and 0 <= c <= 1 for c in v))
                or (k in ("hue", "saturation", "luminance", "variance") and _recipe_value_ok(v) and -100 <= v <= 100)
                or (k == "range" and _recipe_value_ok(v) and 0 <= v <= 100)
                for k, v in sample.items()) for sample in value)
    elif name == "seed":
        valid = type(value) is int and 0 <= value <= 0xFFFFFFFF
    elif name in RECIPE_CURVES:
        valid = (isinstance(value, list) and (len(value) == 0 or 2 <= len(value) <= 16)
                 and all(isinstance(p, list) and len(p) == 2 and all(_recipe_value_ok(v) and 0 <= v <= 255 for v in p) for p in value)
                 and all(b[0] - a[0] >= 1 for a, b in zip(value, value[1:])))
    elif name in RECIPE_ARRAYS:
        valid = isinstance(value, list) and len(value) == RECIPE_ARRAYS[name] and all(_recipe_value_ok(v) for v in value)
    elif name in RECIPE_WHEELS:
        valid = isinstance(value, dict) and all(k in ("hue", "sat", "lum") and isinstance(v, (int, float)) and _recipe_value_ok(v) for k, v in value.items())
    else:
        valid = isinstance(value, (int, float)) and _recipe_value_ok(value)
    if not valid:
        return f"the recipe's {name} is not a valid setting"
    return None


def recipe_problems(settings: dict) -> list[tuple[str, str]]:
    """``(setting, why)`` for every setting of a recipe's ``cameraRaw`` that cannot be used, in the order they are reported."""
    problems = []
    for name, value in settings.items():
        try:
            why = _setting_problem(name, value)
        except (TypeError, ValueError, KeyError, AttributeError, IndexError):
            why = f"the recipe's {name} is not a valid setting"  # a shape a validator did not expect is not a valid setting
        if why is not None:
            problems.append((name, why))
    refused = {name for name, _why in problems}
    exposure = settings.get("exposure", 0.0)
    if "exposure" not in refused and abs(exposure) > RECIPE_EXPOSURE_LIMIT:
        problems.append(("exposure", f"the recipe's exposure ({exposure} EV) is out of range"))
    return problems


def classify_recipe(recipe) -> tuple[str, str]:
    """``(state, reason)`` for a recipe's content (a parsed recipe file, or the open dialog's settings in the same form)."""
    shape = recipe_shape(recipe)
    if shape == RECIPE_SHAPE_NEWER:
        return RECIPE_INVALID, RECIPE_NEWER_REASON
    if shape == RECIPE_SHAPE_DAMAGED:
        if isinstance(recipe, dict) and recipe.get("version") == RECIPE_VERSION and type(recipe.get("version")) is int:
            return RECIPE_INVALID, "the recipe has no settings"
        return RECIPE_INVALID, "not a Camera Raw recipe this version of Image Triage reads"
    settings = recipe["cameraRaw"]
    problems = recipe_problems(settings)
    if problems:
        return RECIPE_INVALID, problems[0][1]
    identity = recipe.get("identity")
    if identity is not None and not isinstance(identity, bool):
        return RECIPE_INVALID, "the recipe's identity flag is not true or false"
    # Match CameraRaw::is_identity, including controls that are inert on their
    # own. The serialized flag is advisory; a stale flag cannot hide adjustments.
    identity = all(settings.get(name, 0) == 0 for name in RECIPE_ACTIVE_FIELDS)
    identity = identity and settings.get("glow", {}).get("amount", 0) == 0
    identity = identity and settings.get("superResolution") is not True
    from .raw_crop import active as crop_active
    identity = identity and not crop_active(settings.get("hostCrop"))
    identity = identity and not _masks_active(settings.get("masks", []))
    identity = identity and not any(s["opacity"]>0 for s in settings.get("retouch",[]))
    identity = identity and not any(p["darken"] > 0 for p in settings.get("redEye", []))
    identity = identity and all(all(sample.get(k, 0) == 0 for k in ("hue", "saturation", "luminance", "variance"))
                                for sample in settings.get("pointColors", []))
    identity = identity and all(all(v == 0 for v in settings.get(name, [])) for name in ("hslHue", "hslSat", "hslLum", "calibHue", "calibSat"))
    identity = identity and all(all(settings.get(name, {}).get(k, 0) == 0 for k in ("sat", "lum"))
                                for name in ("gradeShadows", "gradeMidtones", "gradeHighlights", "gradeGlobal"))
    identity = identity and all(len(settings.get(name, [])) < 2 or all(abs(x - y) < 1e-3 for x, y in settings[name])
                                for name in ("pointCurve", "pointCurveRed", "pointCurveGreen", "pointCurveBlue"))
    return (RECIPE_IDENTITY if identity else RECIPE_EDITED), ""


_READ_REFUSED = "refused"  # the file is there and is not a recipe this build can use (and not a newer one): may be set aside
_READ_FAILED = "failed"    # the file could not be read at all (an unreachable share): nothing is known about its content


def _read_recipe(image_path: str) -> tuple[str, dict | None, str, str]:
    """``(state, recipe, reason, problem)``: the one reading of a photo's recipe file, through the one validator
    (``classify_recipe``). ``problem`` is ``""``, ``_READ_REFUSED`` or ``_READ_FAILED``. Never changes anything on disk."""
    target = recipe_path(image_path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return RECIPE_ABSENT, None, "", ""
    except UnicodeError:
        return RECIPE_INVALID, None, "the recipe is not valid UTF-8", _READ_REFUSED
    except OSError as error:
        return RECIPE_INVALID, None, f"the recipe could not be read: {error}", _READ_FAILED
    try:
        recipe = json.loads(text)
    except (ValueError, RecursionError):
        return RECIPE_INVALID, None, "the recipe is not valid JSON", _READ_REFUSED
    state, reason = classify_recipe(recipe)
    if state != RECIPE_INVALID:
        return state, recipe, reason, ""
    return state, None, reason, ("" if recipe_shape(recipe) == RECIPE_SHAPE_NEWER else _READ_REFUSED)


def recipe_state(image_path: str, *, set_aside: bool = False) -> tuple[str, dict | None, str]:
    """``(state, recipe, reason)`` for the photo's stored recipe. Read-only unless ``set_aside`` is given: then, as
    ``load_recipe`` does when a photo is opened for editing, a recipe that cannot be used is set aside and the photo reads as
    having none (``take_quarantine_notice`` then says what happened).

    A recipe that was set aside earlier stops counting once the person has been told (``take_quarantine_notice``, which
    opening the photo for editing calls): the photo then has no recipe and starts from its original develop. Until then it
    still reads as edit data that could not be read, so that an export made before anyone was told fails and says why
    instead of quietly exporting the unedited original. The set-aside copy stays where it is either way
    (``quarantined_recipes``)."""
    state, recipe, reason, problem = _read_recipe(image_path)
    if set_aside:
        if problem == _READ_REFUSED and quarantine_recipe(image_path, reason) is not None:
            return RECIPE_ABSENT, None, ""
    elif state == RECIPE_ABSENT and _set_aside_unannounced(image_path):
        return RECIPE_INVALID, None, RECIPE_SET_ASIDE_REASON
    return state, recipe, reason


RECIPE_SET_ASIDE_REASON = ("the saved Camera Raw settings could not be read and were set aside; open the photo for editing "
                           "to start again from the original or to recover them")


def _noted_marker(image_path: str) -> Path:
    """An empty file beside the set-aside copies: the person has been told about them (``take_quarantine_notice``)."""
    target = recipe_path(image_path)
    return target.with_name(target.name + ".bad.noted")


_NOTED_THIS_RUN: set[str] = set()  # for a folder the marker cannot be written to


def _set_aside_unannounced(image_path: str) -> bool:
    """Whether the photo has a set-aside recipe that nobody has been told about yet."""
    target = recipe_path(image_path)
    if not target.with_name(target.name + ".bad").exists():  # the first copy always has this name, and is never deleted by us
        return False
    return _notice_key(image_path) not in _NOTED_THIS_RUN and not _noted_marker(image_path).exists()


def _mark_noted(image_path: str) -> None:
    _NOTED_THIS_RUN.add(_notice_key(image_path))
    try:
        _noted_marker(image_path).touch()
    except OSError:
        pass


@dataclass(frozen=True)
class QuarantineNotice:
    """A recipe was set aside: which photo's, where the copy is, and why it could not be used."""

    image_path: str
    bad_path: Path
    reason: str

    @property
    def message(self) -> str:
        why = f" ({self.reason})" if self.reason else ""
        return (f"The previous Camera Raw edit of {Path(self.image_path).name} could not be read{why}. "
                f"The photo starts again from its original develop. The unreadable settings were kept as "
                f"{self.bad_path.name} in {self.bad_path.parent}.")


_QUARANTINE_NOTICES: dict[str, QuarantineNotice] = {}
_QUARANTINE_LOCK = threading.Lock()


def _notice_key(image_path: str) -> str:
    return os.path.normcase(os.path.abspath(image_path))


def take_quarantine_notice(image_path: str) -> QuarantineNotice | None:
    """The notice for a set-aside recipe of this photo that nobody has been told about, once: after it the photo counts as
    having no recipe and a second call returns ``None``. Remembered beside the copy (an empty ``.bad.noted`` file), so it is
    once across restarts too, and a copy set aside by an earlier build, or just before a crash, is still announced."""
    with _QUARANTINE_LOCK:
        notice = _QUARANTINE_NOTICES.pop(_notice_key(image_path), None)
        if notice is None and _set_aside_unannounced(image_path):
            copies = quarantined_recipes(image_path)
            notice = QuarantineNotice(str(image_path), copies[-1], "") if copies else None
        if notice is not None:
            _mark_noted(image_path)
        return notice


def _bad_number(target: Path, name: str) -> int:
    """0 for ``<recipe>.bad``, N for ``<recipe>.bad.N`` (N from 2), -1 for any other file name."""
    first = target.name + ".bad"
    if name == first:
        return 0
    tail = name[len(first) + 1:] if name.startswith(first + ".") else ""
    return int(tail) if tail.isdigit() and not tail.startswith("0") and int(tail) >= 2 else -1


def quarantined_recipes(image_path: str) -> list[Path]:
    """The photo's set-aside recipes, oldest first: ``<recipe>.bad``, then ``<recipe>.bad.2``, ``.bad.3`` and so on."""
    target = recipe_path(image_path)
    try:
        names = os.listdir(target.parent)
    except OSError:
        return []
    found = [(number, target.with_name(name)) for name in names if (number := _bad_number(target, name)) >= 0]
    return [path for _number, path in sorted(found)]


def _move_without_replacing(source: Path, destination: Path) -> None:
    """A rename that fails with ``FileExistsError`` rather than replace what is at ``destination``."""
    if os.name == "nt":
        os.rename(source, destination)  # Windows refuses to rename onto an existing file
        return
    try:
        os.link(source, destination)
    except FileExistsError:
        raise
    except OSError:
        # A file system without hard links: the check and the move are then two steps.
        if destination.exists():
            raise FileExistsError(str(destination)) from None
        os.rename(source, destination)
        return
    os.unlink(source)


QUARANTINE_NAME_ATTEMPTS = 1000


def quarantine_recipe(image_path: str, reason: str = "") -> Path | None:
    """Set the photo's recipe aside as ``<recipe>.bad`` (or ``.bad.2``, ``.bad.3``... when that name is taken: an earlier
    set-aside copy is never replaced, and Image Triage never deletes one). Returns where it went, or ``None`` when it
    could not be moved. Leaves a notice for ``take_quarantine_notice``."""
    log = logging.getLogger(__name__)
    target = recipe_path(image_path)
    taken = [_bad_number(target, path.name) for path in quarantined_recipes(image_path)]
    number = (max(taken) + 1) if taken else 0
    for _attempt in range(QUARANTINE_NAME_ATTEMPTS):
        number = 2 if number == 1 else number
        destination = target.with_name(target.name + ".bad" + (f".{number}" if number else ""))
        try:
            _move_without_replacing(target, destination)
        except FileExistsError:
            number += 1
            continue
        except OSError as error:
            log.warning("Could not set aside the unusable Camera Raw recipe %s: %s", target, error)
            return None
        log.warning("Set aside an unusable Camera Raw recipe: %s -> %s (%s)", target, destination.name, reason)
        with _QUARANTINE_LOCK:
            _QUARANTINE_NOTICES[_notice_key(image_path)] = QuarantineNotice(str(image_path), destination, reason)
            _NOTED_THIS_RUN.discard(_notice_key(image_path))  # this copy is new: an earlier notice does not cover it
            try:
                _noted_marker(image_path).unlink(missing_ok=True)
            except OSError:
                pass
        return destination
    return None


@dataclass(frozen=True)
class RecipeRecovery:
    """What ``recover_quarantined_recipe`` made of a set-aside recipe."""

    recovered: bool                              # a usable recipe was put back as the photo's recipe
    recipe: dict | None                          # what could be salvaged (also when it was not written)
    dropped: tuple[tuple[str, str], ...]         # (setting, why) for every setting that was left out
    bad_path: Path | None                        # the set-aside file it was read from; always still there
    message: str                                 # for the person: what was recovered, or why nothing was


# A recipe is a few kilobytes (masks and retouch strokes make it tens). Anything far beyond that is not one.
RECOVERY_MAX_BYTES = 16 * 1024 * 1024


def _parse_leniently(text: str):
    """The JSON value in ``text``; failing that, the first JSON object in it (something was written after the recipe).
    Raises ``ValueError`` when there is none."""
    try:
        return json.loads(text)
    except ValueError:
        start = text.find("{")
        if start < 0:
            raise
        return json.JSONDecoder().raw_decode(text[start:])[0]


def salvage_recipe(data) -> tuple[dict | None, tuple[tuple[str, str], ...], str]:
    """``(recipe, dropped, why_not)``: the settings of a damaged recipe that the validator (``recipe_problems``) accepts,
    as a recipe of this version. ``recipe`` is ``None`` (and ``why_not`` says why) when nothing usable is left."""
    if not isinstance(data, dict):
        return None, (), "the set-aside file does not hold a recipe"
    version = data.get("version")
    if type(version) is int and version > RECIPE_VERSION:
        return None, (), RECIPE_NEWER_REASON
    settings = data.get("cameraRaw")
    if not isinstance(settings, dict):
        return None, (), "the set-aside recipe holds no settings"
    kept = dict(settings)
    dropped: list[tuple[str, str]] = []
    while problems := recipe_problems(kept):
        for name, why in problems:
            if name in kept:
                del kept[name]
                dropped.append((name, why))
    if not kept:
        return None, tuple(dropped), "none of its settings could be used"
    recipe = {"version": RECIPE_VERSION, "cameraRaw": kept}
    state, reason = classify_recipe(recipe)
    if state == RECIPE_INVALID:  # cannot happen while classify_recipe judges settings through recipe_problems; never write one
        return None, tuple(dropped), reason
    return recipe, tuple(dropped), ""


def _write_json_atomic(target: Path, data) -> None:
    """A temporary file beside the target, flushed to disk, then moved into place: never a half-written recipe."""
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def recover_quarantined_recipe(image_path: str, bad_path: str | Path | None = None, *, write: bool = True) -> RecipeRecovery:
    """Try to bring back the photo's set-aside recipe ("Try to recover the previous edit").

    Reads the newest set-aside copy (or ``bad_path``, which must be one of the photo's), keeps every setting the validator
    accepts and leaves out the rest (``dropped`` names them). With ``write`` the result becomes the photo's recipe, unless
    the photo has meanwhile been given a recipe with edits of its own, which is never replaced (one that changes nothing
    is). The set-aside file is left where it is whatever happens."""
    candidates = quarantined_recipes(image_path)
    source = Path(bad_path) if bad_path is not None else (candidates[-1] if candidates else None)
    name = Path(image_path).name
    if source is None or source not in candidates:
        return RecipeRecovery(False, None, (), None, f"{name} has no set-aside Camera Raw settings to recover.")
    try:
        if source.stat().st_size > RECOVERY_MAX_BYTES:
            return RecipeRecovery(False, None, (), source, f"{source.name} is too large to be a Camera Raw recipe.")
        text = source.read_bytes().decode("utf-8-sig", errors="replace")
    except OSError as error:
        return RecipeRecovery(False, None, (), source, f"{source.name} could not be read: {error}")
    try:
        data = _parse_leniently(text)
    except (ValueError, RecursionError):
        return RecipeRecovery(False, None, (), source, f"Nothing could be recovered: {source.name} is not readable as settings.")
    recipe, dropped, why_not = salvage_recipe(data)
    if recipe is None:
        return RecipeRecovery(False, None, dropped, source, f"Nothing could be recovered from {source.name}: {why_not}.")
    left_out = (" Left out: " + ", ".join(setting for setting, _why in dropped) + ".") if dropped else ""
    if not write:
        return RecipeRecovery(False, recipe, dropped, source, f"The previous edit of {name} can be recovered.{left_out}")
    state, _current, reason, problem = _read_recipe(image_path)
    if state == RECIPE_EDITED or (state == RECIPE_INVALID and problem != _READ_REFUSED):
        why = "the photo has been edited again since" if state == RECIPE_EDITED else reason
        return RecipeRecovery(False, recipe, dropped, source,
                              f"The previous edit of {name} was not put back: {why}. It is still in {source.name}.")
    if state == RECIPE_INVALID and quarantine_recipe(image_path, reason) is None:
        return RecipeRecovery(False, recipe, dropped, source, f"The previous edit of {name} was not put back: {reason}.")
    try:
        _write_json_atomic(recipe_path(image_path), recipe)
    except OSError as error:
        return RecipeRecovery(False, recipe, dropped, source, f"The recovered edit of {name} could not be saved: {error}")
    with _QUARANTINE_LOCK:
        _QUARANTINE_NOTICES.pop(_notice_key(image_path), None)
        _mark_noted(image_path)
    return RecipeRecovery(True, recipe, dropped, source, f"Recovered the previous edit of {name}.{left_out}")


def display_render_path(image_path: str) -> Path:
    """The screen-sized JPEG of the photo's edits that autostash writes beside the full-size render."""
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".photocraft.display.jpg")


# --- the render manifest -----------------------------------------------------------------------------------------------
#
# An editor that keeps one (PhotoCraft's render_manifest.rs) writes every save's and every recipe render's pictures under names
# of their own and then switches ``<photo>.photocraft.renders.json`` to name them and what they were made from. Pictures are
# current only while those sources are still what is on disk; anything else, a damaged manifest included, is out of date. The
# timestamp rules further down are only for photos whose edits were saved before the manifest existed (no manifest file at all).

RENDER_MANIFEST_VERSION = 1
# PhotoCraft's render_manifest::ALGORITHM. Pictures made by any other render algorithm are out of date. (A test keeps the two in step.)
# v0.20.1 uses upstream Calibration and RAW workflow defaults.
RENDER_ALGORITHM = 3


def render_manifest_path(image_path: str) -> Path:
    path = Path(image_path)
    return edit_root_for(path.parent) / (path.name + ".photocraft.renders.json")


@dataclass(frozen=True)
class RenderOutput:
    path: Path
    format: str
    width: int
    height: int
    depth: int
    icc: bool


@dataclass(frozen=True)
class RenderManifest:
    generation: int
    kind: str  # "project" | "recipe"
    source: str | None
    algorithm: int
    outputs: dict[str, RenderOutput]
    project_size: int | None = None
    project_sha256: str | None = None
    project_depth: int | None = None
    recipe_sha256: str | None = None


class _InvalidManifest:
    def __repr__(self) -> str:
        return "INVALID_MANIFEST"


INVALID_MANIFEST = _InvalidManifest()


def read_render_manifest(image_path: str) -> RenderManifest | _InvalidManifest | None:
    """The photo's render manifest; ``None`` when it has none; ``INVALID_MANIFEST`` when the file is there but unreadable or
    malformed (out of date, never a reason to fall back to timestamps)."""
    target = render_manifest_path(image_path)
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError:
        return INVALID_MANIFEST
    try:
        data = json.loads(text)
        if not isinstance(data, dict) or data.get("version") != RENDER_MANIFEST_VERSION:
            return INVALID_MANIFEST
        kind = data["kind"]
        outputs = {}
        for role, described in dict(data.get("outputs") or {}).items():
            name = str(described["file"])
            if not name or Path(name).name != name:
                return INVALID_MANIFEST  # a picture beside the manifest, never elsewhere
            outputs[str(role)] = RenderOutput(
                target.parent / name, str(described["format"]), int(described["width"]), int(described["height"]),
                int(described["depth"]), bool(described["icc"]),
            )
        renderer = data["renderer"]
        source = data.get("source")
        manifest = RenderManifest(
            generation=int(data["generation"]), kind=str(kind), source=source if isinstance(source, str) else None,
            algorithm=int(renderer["algorithm"]), outputs=outputs,
        )
        if kind == "project":
            project = data["project"]
            return RenderManifest(**{**manifest.__dict__, "project_size": int(project["size"]), "project_sha256": str(project["sha256"]),
                                    "project_depth": int(project["depth"]) if "depth" in project else None})
        if kind == "recipe":
            return RenderManifest(**{**manifest.__dict__, "recipe_sha256": str(data["recipe"]["sha256"])})
        return INVALID_MANIFEST
    except (ValueError, KeyError, TypeError, AttributeError):
        return INVALID_MANIFEST


_HASHES: OrderedDict[tuple[str, int, int], str] = OrderedDict()
_HASHES_LOCK = threading.Lock()


def file_sha256(path: Path) -> str | None:
    """SHA-256 of the file, remembered per (path, size, modified time) so a project is read and hashed once per version.
    Reads the whole file: call it from a worker, never the UI thread."""
    try:
        stat = path.stat()
    except OSError:
        return None
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _HASHES_LOCK:
        if key in _HASHES:
            _HASHES.move_to_end(key)
            return _HASHES[key]
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(4 << 20):
                digest.update(chunk)
    except OSError:
        return None
    value = digest.hexdigest()
    with _HASHES_LOCK:
        _HASHES[key] = value
        while len(_HASHES) > 256:
            _HASHES.popitem(last=False)
    return value


def render_manifest_is_current(image_path: str, manifest: RenderManifest, *, verify_project: bool) -> bool:
    """Whether the manifest's pictures are of the photo's edits as they are on disk now: same original, same project (or recipe,
    when the photo has no project, which takes precedence), same render algorithm, and every picture it names is there.

    ``verify_project`` compares the project's SHA-256 too (reading the whole file the first time; workers only). Without it the
    project is matched by size, which is all the thumbnails and the viewer ask (they never cause a full read)."""
    if manifest.algorithm != RENDER_ALGORITHM or manifest.source is None or manifest.source != file_signature(image_path):
        return False
    project = sidecar_pcraft_path(image_path)
    if manifest.kind == "project":
        try:
            stat = project.stat()
            size = stat.st_size
        except OSError:
            return False
        if size != manifest.project_size:
            return False
        # Project writes finish before the manifest is published. A later write,
        # even of the same size, invalidates the cheap UI check without hashing.
        if not verify_project:
            try:
                if stat.st_mtime_ns > render_manifest_path(image_path).stat().st_mtime_ns:
                    return False
            except OSError:
                return False
        if verify_project and file_sha256(project) != manifest.project_sha256:
            return False
    elif manifest.kind == "recipe":
        if project.exists():
            return False
        try:
            recipe = recipe_path(image_path).read_bytes()
        except OSError:
            return False
        if hashlib.sha256(recipe).hexdigest() != manifest.recipe_sha256:
            return False
    else:
        return False
    for output in manifest.outputs.values():
        try:
            if output.path.stat().st_size <= 0:
                return False
        except OSError:
            return False
    return True


def current_render_manifest(image_path: str, *, verify_project: bool) -> RenderManifest | None | bool:
    """``False`` when the photo has no manifest (edits saved before it existed: the timestamp rules apply); otherwise its manifest
    if current, else ``None``."""
    manifest = read_render_manifest(image_path)
    if manifest is None:
        return False
    if manifest is INVALID_MANIFEST or not render_manifest_is_current(image_path, manifest, verify_project=verify_project):
        return None
    return manifest


# Pictures of a generation no manifest names are left behind by a render that lost to a newer save, or by a save whose manifest
# write failed. They are cleared away once they are this old (a younger one may belong to a save still writing its manifest).
UNREFERENCED_RENDER_GRACE_S = 600.0


def prune_render_generations(image_path: str, *, now: float | None = None) -> int:
    """Deletes the photo's generation pictures (``.photocraft.g<N>.png``, ``.photocraft.display.g<N>.jpg``) that its manifest
    does not name and that are older than ``UNREFERENCED_RENDER_GRACE_S``. Nothing is deleted while the manifest cannot be read.
    Returns how many were deleted."""
    manifest = read_render_manifest(image_path)
    if manifest is INVALID_MANIFEST:
        return 0
    keep = {output.path.name for output in manifest.outputs.values()} if manifest is not None else set()
    path = Path(image_path)
    root = edit_root_for(path.parent)
    prefixes = (path.name + ".photocraft.g", path.name + ".photocraft.display.g")
    now = time.time() if now is None else now
    deleted = 0
    try:
        entries = list(os.scandir(root))
    except OSError:
        return 0
    for entry in entries:
        name = entry.name
        prefix = next((p for p in prefixes if name.startswith(p)), None)
        if prefix is None or name in keep:
            continue
        number, _, suffix = name[len(prefix):].partition(".")
        if not number.isdigit() or suffix not in ("png", "jpg"):
            continue
        try:
            if now - entry.stat().st_mtime < UNREFERENCED_RENDER_GRACE_S:
                continue
            os.remove(entry.path)
            deleted += 1
        except OSError:
            continue
    return deleted


# Stash writes the display JPEG first and the PNG when the whole save lands, which takes seconds and
# up to ~25 s under disk contention. A display JPEG much older than the PNG predates it.
DISPLAY_STALE_AFTER_S = 120.0


def saved_render_path(image_path: str) -> Path | None:
    """The cheapest picture of the photo's saved PhotoCraft edits, or ``None`` when it has none (or only out-of-date ones).

    The screen-sized display JPEG decodes in milliseconds; the full-size PNG (hundreds of MB for a
    45 MP photo) does not, so it is only the fallback for edits saved without a display JPEG.
    Never reads a whole project (thumbnails and the viewer call it).
    """
    manifest = current_render_manifest(image_path, verify_project=False)
    if manifest is not False:
        if manifest is None:
            return None
        chosen = manifest.outputs.get("display") or manifest.outputs.get("png")
        return chosen.path if chosen is not None else None
    png = rendered_preview_path(image_path)
    display = display_render_path(image_path)
    try:
        shown = display.stat()
    except OSError:
        shown = None
    try:
        full = png.stat()
    except OSError:
        full = None
    if shown is not None and shown.st_size > 0:
        if full is None or shown.st_mtime >= full.st_mtime - DISPLAY_STALE_AFTER_S:
            return display
    return png if full is not None else None


# A PNG written within this long before the display JPEG is taken as part of the same save (the JPEG is written first, the PNG when the
# whole save lands); one older than that is from an earlier save.
FULL_RENDER_TOLERANCE_S = 2.0


def full_render_path(image_path: str, *, verify_project: bool = True) -> Path | None:
    """The current full-size PNG, or None when the editor must render one.
    A screen-sized display picture is never a full-size render.

    ``verify_project`` (the default) checks the project's SHA-256 against the manifest: workers only, see ``file_sha256``."""
    manifest = current_render_manifest(image_path, verify_project=verify_project)
    if manifest is not False:
        if manifest is None:
            return None
        chosen = manifest.outputs.get("png")
        return chosen.path if chosen is not None else None
    png = rendered_preview_path(image_path)
    display = display_render_path(image_path)
    try:
        shown = display.stat()
    except OSError:
        shown = None
    try:
        full = png.stat()
    except OSError:
        full = None
    shown_is_usable = shown is not None and shown.st_size > 0
    if full is not None and full.st_size > 0 and (not shown_is_usable or full.st_mtime >= shown.st_mtime - FULL_RENDER_TOLERANCE_S):
        return png
    return None


def render_is_behind(image_path: str) -> bool:
    """Whether the photo has a saved project whose full-size render is missing or older than it: a save that left the render out (an
    autosave does, and so does closing the popout or quitting) has been made since the render was. The project is written before the
    render in a full save, so a render within ``FULL_RENDER_TOLERANCE_S`` of the project (or newer) belongs to it.

    With a render manifest: behind unless the manifest is current and names a full-size render."""
    manifest = current_render_manifest(image_path, verify_project=False)
    if manifest is not False:
        if not sidecar_pcraft_path(image_path).exists():
            return False
        return manifest is None or manifest.kind != "project" or "png" not in manifest.outputs
    try:
        project = sidecar_pcraft_path(image_path).stat().st_mtime
    except OSError:
        return False  # no project: a photo with only a recipe has no full-size render, and nothing is behind
    try:
        render = rendered_preview_path(image_path).stat().st_mtime
    except OSError:
        return True
    return render < project - FULL_RENDER_TOLERANCE_S


def materialize_for_photocraft(path: str, cache_dir: Path) -> str:
    """Decode an unsupported non-RAW file to a TIFF PhotoCraft can open directly.

    Never points PhotoCraft at the original source file for formats it can't
    read natively; the converted copy lives in ``cache_dir``.
    """
    from PySide6.QtCore import QSize

    from .imaging import load_image_for_display

    # Larger than any current camera sensor, so imaging.py's quality-mode
    # selection always picks the full-resolution decode, not a half-size one.
    image, error = load_image_for_display(path, QSize(16384, 16384), prefer_embedded=False)
    if image.isNull():
        raise PhotoCraftError(f"Could not decode {path!r} for PhotoCraft: {error}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / (Path(path).name + "__photocraft_source.tiff")
    if not image.save(str(out_path), "TIFF"):
        raise PhotoCraftError(f"Could not write converted TIFF for {path!r}")
    return str(out_path)


# PhotoCraft's control server closes a connection that has sent nothing for 30 s (``IO_TIMEOUT`` in its automation crate), and its own
# client reconnects after 25 s of quiet. A request sent down a connection the editor has closed fails with "[WinError 10053] An
# established connection was aborted", which is what the first E showed when the editor had been waiting in the background.
IDLE_RECONNECT_S = 20.0
# Interactive commands need a visible failure in reasonable time; full renders
# use their separate render connection. Allow headroom over measured ~25 s saves.
HOSTED_RESPONSE_TIMEOUT_S = 45.0


class PhotoCraftControl:
    """Blocking JSON-lines client for PhotoCraft's control channel. A connection that has been quiet for ``IDLE_RECONNECT_S`` is
    replaced by a fresh one (same token, same editor) before the next request, so a wait of any length is harmless."""

    def __init__(self, port: int, token: str, *, host: str = "127.0.0.1", timeout: float = 120.0) -> None:
        self._address = (host, port)
        self._token = token
        self._timeout = timeout
        self._next_id = 1
        self._exchange_lock = threading.RLock()
        self._open(timeout)

    def _open(self, timeout: float) -> None:
        self._sock = socket.create_connection(self._address, timeout=min(timeout, 2.0))
        self._sock.settimeout(timeout)
        self._file = self._sock.makefile("rwb")
        try:
            self._authenticate(self._token)
        except BaseException:
            self.close()
            raise
        self._last_used = time.monotonic()

    def _reconnect_if_quiet(self, timeout: float | None = None) -> None:
        last_used = getattr(self, "_last_used", None)
        if last_used is None or time.monotonic() - last_used <= IDLE_RECONNECT_S:
            return
        try:
            self.close()
        except OSError:
            pass  # the editor closed its end already
        self._open(self._timeout if timeout is None else timeout)

    def _authenticate(self, token: str) -> None:
        reply = self._request("auth", {"token": token})
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft control auth failed: {reply.get('error')}")

    def _request(self, method: str, params: dict | None = None) -> dict:
        # One request and its reply at a time: the connection is used from the UI thread and from workers (prefetch, the
        # viewport query, saves), and two exchanges interleaved on one socket read each other's replies, or half of one.
        lock = self.__dict__.get("_exchange_lock")
        if lock is None:
            lock = self.__dict__.setdefault("_exchange_lock", threading.RLock())
        if not lock.acquire(timeout=getattr(self, "_timeout", 120.0)):
            raise PhotoCraftError("PhotoCraft is still answering another request. No command was sent.")
        try:
            try:
                return self._exchange(method, params)
            except (OSError, ValueError, PhotoCraftError):
                # A timed-out buffered socket cannot be reused. Never replay the
                # command: a Save may have reached the editor even without a reply.
                self._last_used = float("-inf")
                if hasattr(self, "_sock"):
                    self.close()
                raise
        finally:
            lock.release()

    def _exchange(self, method: str, params: dict | None) -> dict:
        if method != "auth":
            self._reconnect_if_quiet()
        req_id = self._next_id
        self._next_id += 1
        payload: dict = {"id": req_id, "method": method}
        if params is not None:
            payload["params"] = params
        self._file.write((json.dumps(payload) + "\n").encode("utf-8"))
        self._file.flush()
        reply_line = self._file.readline()
        if not reply_line:
            raise PhotoCraftError("PhotoCraft control channel closed unexpectedly")
        try:
            reply = json.loads(reply_line)
        except ValueError as error:
            raise PhotoCraftError(f"PhotoCraft control sent an unreadable reply to {method!r}: {reply_line[:200]!r}") from error
        if reply.get("id") != req_id:
            raise PhotoCraftError(f"PhotoCraft control reply id mismatch: expected {req_id}, got {reply.get('id')}")
        self._last_used = time.monotonic()
        return reply

    def call(self, method: str, params: dict | None = None) -> dict:
        try:
            reply = self._request(method, params)
        except (OSError, ValueError) as error:
            raise PhotoCraftError(f"PhotoCraft control {method!r} failed: {error}") from error
        if not reply.get("ok"):
            raise PhotoCraftError(f"PhotoCraft control {method!r} failed: {reply.get('error')}")
        return reply.get("result") or {}

    def app_open(self, path: str, *, replace: bool = False, should_continue=None, camera_raw: dict | None = None) -> dict:
        require_finishing_support(camera_raw, bool(getattr(self, "camera_raw_finishing_supported", False)))
        require_calibration_support(camera_raw, getattr(self, "camera_raw_calibration_supported", False) is True)
        require_super_resolution_support(camera_raw, getattr(self, "camera_raw_super_resolution_supported", False) is True)
        require_masks_support(camera_raw, getattr(self, "camera_raw_masks_supported", False) is True)
        require_retouch_support(camera_raw, getattr(self,"camera_raw_retouch_supported",False) is True)
        require_red_eye_support(camera_raw, getattr(self, "camera_raw_red_eye_supported", False) is True)
        params = {"path": _automation_path(path)}
        if replace:
            params["replace"] = True
        if camera_raw is not None and Path(path).suffix.lower() in RAW_SUFFIXES and getattr(self, "camera_raw_supported", False):
            # Camera Raw workspace: the editor develops the raw itself and opens it in Camera Raw. A raw it cannot
            # develop from sensor data opens as the camera's embedded preview, with no dialog (``cameraRaw.dialog``
            # is false in the reply).
            if "manifestPath" in camera_raw and getattr(self, "render_manifest_supported", False) is not True:
                camera_raw = {key: value for key, value in camera_raw.items() if key != "manifestPath"}  # an editor without manifests rejects it
            params["cameraRaw"] = camera_raw
        # The caller may have moved on while this request waited its turn: do not start an open that would later
        # replace the photo now on screen.
        if should_continue is not None and not should_continue():
            raise PhotoCraftSuperseded(f"{path} is no longer the selected photo")
        return self.call("app.open", params)

    def app_prefetch(self, path: str, signature: str) -> dict:
        """Ask the editor to develop ``path`` for Camera Raw in the background (``signature`` is the file's
        size and modified time as ``file_signature`` gives them): opening it with that signature is then nearly instant."""
        return self.call("app.prefetch", {"path": _automation_path(path), "signature": signature})

    def require_hosted_handoff(self) -> None:
        """Probe validation without opening a document or writing any files."""
        try:
            capabilities = self.call("app.handoff")
        except PhotoCraftError as error:
            if "unknown method" not in str(error):
                raise
            raise PhotoCraftCompatibilityError("This PhotoCraft build lacks persistent hosted document replacement") from error
        if capabilities.get("version", 0) < 2:
            raise PhotoCraftCompatibilityError("This PhotoCraft build lacks persistent hosted document replacement")
        self.camera_raw_supported = capabilities.get("cameraRaw", 0) >= 1
        self.camera_raw_prefetch_supported = capabilities.get("cameraRawPrefetch", 0) >= 1
        self.camera_raw_switch_supported = capabilities.get("cameraRawSwitch", 0) >= 1
        self.camera_raw_finishing_supported = capabilities.get("cameraRawFinishing", 0) >= 1
        self.camera_raw_calibration_supported = capabilities.get("cameraRawCalibration", 0) >= 1
        self.camera_raw_super_resolution_supported = capabilities.get("cameraRawSuperResolution", 0) >= 1
        self.camera_raw_masks_supported = capabilities.get("cameraRawMasks", 0) >= 1
        self.camera_raw_retouch_supported = capabilities.get("cameraRawRetouch", 0) >= 1
        self.camera_raw_red_eye_supported = capabilities.get("cameraRawRedEye", 0) >= 1
        self.camera_raw_host_job_supported = capabilities.get("cameraRawHostJob", 0) >= 1
        self.camera_raw_host_tools_supported = capabilities.get("cameraRawHostTools", 0) >= 1  # ui.hostJob takes the list of kinds the host can run
        self.filmstrip_supported = capabilities.get("filmstrip", 0) >= 1
        # ``app.stash`` can save the project and the display JPEG without the full-size PNG (``preview`` left out).
        self.stash_without_preview_supported = capabilities.get("stashWithoutPreview", 0) >= 1
        # Saves and recipe renders write generation-named pictures and a render manifest (``render_manifest_path``).
        self.render_manifest_supported = capabilities.get("renderManifest", 0) >= 1
        # ``app.render``: a photo's edits at full size, on request (``full_render``).
        self.full_render_supported = capabilities.get("fullRender", 0) >= 1
        for method, validation in (
            ("app.stash", "stash requires path and preview"),
            ("app.bind", "bind requires an open document, .pcraft path and .png preview"),
        ):
            try:
                self.call(method, {})
            except PhotoCraftError as error:
                if validation in str(error):
                    continue
                if "unknown method" not in str(error):
                    raise
                raise PhotoCraftCompatibilityError(
                    "This PhotoCraft build does not support the hosted editor handoff. "
                    "Use the companion photocraft.exe build or update IMAGE_TRIAGE_PHOTOCRAFT_EXE. "
                    f"Compatibility check: {error}"
                ) from error
            raise PhotoCraftCompatibilityError(f"PhotoCraft returned an unexpected response to the {method} compatibility check")

    def app_save(self, path: str | None = None) -> dict:
        return self.call("app.save", {"path": _automation_path(path)} if path else {})

    def ui_resize(self, width: int, height: int) -> dict:
        return self.call("ui.resize", {"width": width, "height": height})

    def ui_set(self, **fields) -> dict:
        return self.call("ui.set", fields)

    def execute(self, command: str, params: dict | None = None) -> dict:
        return self.call("engine.execute", {"command": command, "params": params or {}})

    def edit_menus(self, *, hide: list[str] | None = None, show: list[str] | None = None) -> dict:
        params: dict = {}
        if hide:
            params["hide"] = hide
        if show:
            params["show"] = show
        return self.execute("edit.menus", params)

    def document_state(self) -> tuple[int, bool | None] | None:
        """The open document's revision and whether the editor holds it as changed since it was last saved or developed
        (``dirty``; ``None`` from an editor that does not say), from one inspection. ``None`` when no document is open."""
        session = self.execute("session.inspect")
        if "active" not in session:
            raise PhotoCraftError("PhotoCraft session inspection did not report an active document")
        active = session["active"]
        if active is None:
            return None
        for document in session.get("documents", []):
            if document.get("index") == active:
                dirty = document.get("dirty")
                return int(document["revision"]), (bool(dirty) if isinstance(dirty, bool) else None)
        raise PhotoCraftError("PhotoCraft session inspection omitted the active document")

    def document_revision(self) -> int | None:
        """The open document's undo/history revision counter: unchanged since
        open means nothing worth saving happened. None means no document is open."""
        # Session inspection also works after the user closes the document tab.
        # It avoids treating an empty editor as a failed save/transport request.
        session = self.execute("session.inspect")
        if "active" not in session:
            raise PhotoCraftError("PhotoCraft session inspection did not report an active document")
        active = session["active"]
        if active is None:
            return None
        for document in session.get("documents", []):
            if document.get("index") == active:
                return int(document["revision"])
        raise PhotoCraftError("PhotoCraft session inspection omitted the active document")

    def close_document(self) -> None:
        """Close the active document. Over the control channel this never
        opens an "unsaved changes?" dialog — unsaved edits are just discarded
        — so callers that want to keep them must app_save first."""
        self.execute("file.close")

    def quit(self) -> None:
        if not self._exchange_lock.acquire(timeout=2.0):
            self.close()  # interrupt the reader; process.shutdown supplies the exit fallback
            return
        try:
            # Short timeouts: an editor that has stopped answering must not hold up closing. A connection it has dropped is replaced
            # by one with the same short limit.
            self._reconnect_if_quiet(timeout=2.0)
            self._sock.settimeout(2.0)
            self.call("app.quit")
        except (PhotoCraftError, OSError):
            pass
        finally:
            self._exchange_lock.release()

    def close(self) -> None:
        # Wake a blocked reader before closing its buffered file (which otherwise
        # waits for that reader's lock, including during application shutdown).
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        # makefile() retains a reference to the socket. Close the native socket
        # first, otherwise Windows may keep a timed select/read alive while
        # file.close waits for the buffered-reader lock.
        descriptor = self._sock.detach()
        if descriptor != -1:
            socket.close(descriptor)
        self._file.close()


@dataclass(slots=True)
class PhotoCraftProcess:
    """A launched PhotoCraft subprocess, its control channel, and its HWND."""

    process: subprocess.Popen
    control: PhotoCraftControl
    hwnd: int
    token_file: Path
    read_root: Path
    write_root: Path | None = None
    executable: str = ""
    attached_parent: int | None = None
    launch_timings: dict[str, float] = field(default_factory=dict)
    current_source: str | None = None
    source_path: str = ""
    pending_stashes: dict[str, tuple[int, str]] = field(default_factory=dict)
    # Which document is open, counted: every open or replacement starts a new one, and its revisions start again. A save is
    # matched to a requirement by (sidecar, document generation), never by revision alone.
    document_generation: int = 0
    # What each save job was sent for: job id -> (sidecar, document generation, revision).
    stash_meta: dict[int, tuple[str, int, int]] = field(default_factory=dict)
    stash_errors: dict[str, str] = field(default_factory=dict)
    source_by_sidecar: dict[str, str] = field(default_factory=dict)
    observed_stashes: set[int] = field(default_factory=set)
    # The sidecar the *currently open* document would be saved to, and its
    # document.inspect() revision right after that open — together these say
    # whether switching away should save first (the editor host owns
    # the actual save-before-close-before-open sequence).
    current_sidecar: Path | None = None
    opened_revision: int = 0
    port: int = 0
    token: str = ""
    # A second control connection for work that must not wait behind a long RAW open on
    # ``control`` (previews, cancelling an obsolete open). PhotoCraft serves each
    # connection on its own thread.
    fast: PhotoCraftControl | None = None
    fast_unavailable: bool = False
    # A third connection for full-size renders (``full_render``): a render takes seconds and must not hold up either of the others,
    # and a shutdown can always cancel it.
    render: PhotoCraftControl | None = None
    preview_source: str | None = None
    # Launched with ``--raw-workspace``: Camera Raw opens by itself on each document, inside the window.
    camera_raw_first: bool = False
    # Started in the background with no photo open, at below-normal priority so that starting it never slows what the user is
    # doing; `raise_priority` puts it back to normal when it is first used.
    idle_priority: bool = False
    # Where the editor's own output goes (see ``_open_editor_log``); None when no file could be made.
    log_path: Path | None = None
    # When the open photo was last found changed (``time.monotonic()``): the autosave writes the full-size render after a pause in editing.
    last_edit_at: float = 0.0
    # The editor's colours have been fetched once (the filmstrip follows them); the autosave tick keeps them current after that.
    theme_synced: bool = False
    # The open photo has been left (the editor key, or the popout closing): it was settled, which in the Camera Raw workspace
    # closes its dialog. Coming back to the same photo must open it again, not show what is left of it.
    left: bool = False

    def exit_note(self) -> str:
        """Whether the editor is still running or has exited (with what code), and where its own output is: for the log line of a
        failed handoff, which otherwise says only that the connection broke."""
        code = self.process.poll()
        state = "the editor is still running" if code is None else f"the editor has exited with code {code}"
        return f"{state}; its own output is in {self.log_path}" if self.log_path is not None else state
    # A raw is open in its Camera Raw dialog and has not been left yet: once the dialog is over (the user
    # pressed Open or Cancel, or we left the photo) the document's revision is taken as the baseline, so
    # developing the photo is not mistaken for editing it.
    camera_raw_rebaseline: bool = False
    # The raw the editor was last asked to develop ahead of time (path as sent, the host's normalised key for it), and the
    # key of the one it reported ready: opening that photo costs the editor next to nothing, so nothing is waited for.
    prefetch_sent: tuple[str, str] | None = None
    prefetch_ready: str | None = None

    def is_running(self) -> bool:
        return self.process.poll() is None

    def fast_lane(self) -> PhotoCraftControl:
        if self.fast is None:
            fast = PhotoCraftControl(self.port, self.token)
            fast.read_root = self.read_root
            self.fast = fast
        return self.fast

    def render_lane(self) -> PhotoCraftControl:
        if self.render is None:
            lane = PhotoCraftControl(self.port, self.token)
            lane.read_root = self.read_root
            self.render = lane
        return self.render

    def cancel_open_jobs(self) -> int:
        """Cancel a RAW (or any) document open that is still running, never a save."""
        fast = self.fast_lane()
        cancelled = 0
        for job in fast.call("jobs.list").get("jobs", []):
            if job.get("command") == "app.open" and job.get("state") == "running":
                try:
                    fast.call("jobs.cancel", {"job": int(job["id"])})
                    cancelled += 1
                except PhotoCraftError:
                    pass  # it finished between the list and the cancel
        return cancelled

    def path_within_root(self, path: str) -> str | None:
        """``path`` relative to this process's automation read root, or ``None``
        if it falls outside it (app.open/app.save refuse paths outside the root)."""
        try:
            return Path(path).resolve().relative_to(self.read_root.resolve()).as_posix()
        except ValueError:
            return None

    def path_within_write_root(self, path: str) -> str | None:
        try:
            return Path(path).resolve().relative_to((self.write_root or self.read_root).resolve()).as_posix()
        except ValueError:
            return None

    def shutdown(self, *, timeout: float = 3.0) -> None:
        """Ask PhotoCraft to quit over the control channel, then fall back to
        terminate()/kill() if it doesn't exit in time."""
        if self.fast is not None:
            try:
                self.fast.close()
            except OSError:
                pass
            self.fast = None
        if self.render is not None:
            try:
                self.render.close()
            except OSError:
                pass
            self.render = None
        try:
            if self.is_running():
                self.control.quit()
                try:
                    self.process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=timeout)
                    except subprocess.TimeoutExpired:
                        self.process.kill()
                        self.process.wait(timeout=timeout)
        finally:
            try:
                self.control.close()
            except OSError:
                pass
            try:
                self.token_file.unlink(missing_ok=True)
            except OSError:
                pass


def _automation_path(path: str) -> str:
    """PhotoCraft's control channel refuses backslashes in automation paths."""
    return path.replace("\\", "/")


# A token file is removed when its editor is shut down. After a crash (of either program) it stays in the temporary folder
# for good, so each start clears the ones that are this old: an editor reads its token once, as it starts, and none runs for a day
# on a file it still needs.
STALE_TOKEN_FILE_S = 24 * 60 * 60.0


def sweep_stale_token_files(*, now: float | None = None, directory: str | Path | None = None) -> int:
    """Deletes ``photocraft_token_*.txt`` files older than ``STALE_TOKEN_FILE_S`` from the temporary folder. Returns how many."""
    root = Path(directory) if directory is not None else Path(tempfile.gettempdir())
    now = time.time() if now is None else now
    removed = 0
    try:
        candidates = list(root.glob("photocraft_token_*.txt"))
    except OSError:
        return 0
    for candidate in candidates:
        try:
            if now - candidate.stat().st_mtime >= STALE_TOKEN_FILE_S:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def _free_tcp_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _connect_with_retry(port: int, token: str, process: subprocess.Popen, *, timeout: float = 20.0) -> PhotoCraftControl:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise PhotoCraftError(f"PhotoCraft exited before its control channel opened (code {process.returncode})")
        try:
            remaining = max(.001, deadline - time.monotonic())
            control = PhotoCraftControl(port, token, timeout=min(2.0, remaining))
            control._timeout = HOSTED_RESPONSE_TIMEOUT_S
            control._sock.settimeout(HOSTED_RESPONSE_TIMEOUT_S)
            return control
        except OSError as exc:
            last_error = exc
            time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    raise PhotoCraftError(f"Timed out connecting to PhotoCraft's control channel: {last_error}")


def _find_window_for_pid(pid: int, *, timeout: float = 10.0) -> int | None:
    user32 = ctypes.windll.user32
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    enum_proc_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    found: list[int] = []

    def _callback(hwnd, _lparam):
        owner_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner_pid))
        if owner_pid.value == pid and (user32.IsWindowVisible(hwnd) or user32.GetWindowTextLengthW(hwnd) > 0):
            found.append(int(hwnd))
            return False
        return True

    callback = enum_proc_type(_callback)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not found:
        user32.EnumWindows(callback, 0)
        if found:
            break
        time.sleep(0.1)
    return found[0] if found else None


def launch_photocraft(
    initial_path: str | None,
    *,
    read_root: str,
    write_root: str,
    executable: str | None = None,
    on_window=None,
    extra_args: tuple[str, ...] = (),
    camera_raw: dict | None = None,
    idle: bool = False,
) -> PhotoCraftProcess:
    """Starts PhotoCraft rooted at ``read_root`` / ``write_root`` and opens ``initial_path`` in it. Without a path (``idle``
    start-ups) nothing is opened: the editor waits, hidden, at below-normal priority, for the first photo."""
    exe = executable or detect_photocraft_executable()
    if not exe:
        raise PhotoCraftError("PhotoCraft executable not found")

    # A launcher/dev restart may inherit an override for an older release.
    # Honor compatible overrides, but recover from protocol incompatibility
    # before any document has opened. Each failed attempt cleans up below.
    candidates = list(dict.fromkeys([exe, *companion_photocraft_executables()]))
    last_error = None
    for candidate in candidates:
        try:
            proc = _launch_photocraft_binary(
                initial_path, read_root=read_root, write_root=write_root, executable=candidate, on_window=on_window,
                extra_args=extra_args, camera_raw=camera_raw, idle=idle,
            )
            proc.executable = candidate
            logging.getLogger(__name__).info("Hosted PhotoCraft executable: %s", candidate)
            return proc
        except PhotoCraftCompatibilityError as error:
            last_error = error
            logging.getLogger(__name__).warning("Rejected incompatible PhotoCraft executable %s: %s", candidate, error)
    raise PhotoCraftCompatibilityError(f"No compatible PhotoCraft build found. {last_error}")


EDITOR_LOGS_KEPT = 5


def _open_editor_log():
    """A file for the editor's own output (the message and backtrace of a panic, which nothing else records), beside Image Triage's other
    logs, with the last ``EDITOR_LOGS_KEPT`` starts kept. Returns (file object or ``subprocess.DEVNULL``, its path or None)."""
    try:
        from .perf import performance_log_dir

        directory = performance_log_dir()
        directory.mkdir(parents=True, exist_ok=True)
        older = sorted(directory.glob("photocraft_editor_*.log"), key=lambda path: path.stat().st_mtime)
        for stale in older[: max(0, len(older) - (EDITOR_LOGS_KEPT - 1))]:
            try:
                stale.unlink()
            except OSError:
                pass
        path = directory / f"photocraft_editor_{time.strftime('%Y%m%d_%H%M%S')}_{os.getpid()}_{secrets.token_hex(2)}.log"
        return open(path, "wb"), path
    except OSError:
        return subprocess.DEVNULL, None


def _launch_photocraft_binary(
    initial_path: str | None, *, read_root: str, write_root: str, executable: str, on_window=None,
    extra_args: tuple[str, ...] = (),
    camera_raw: dict | None = None,
    idle: bool = False,
) -> PhotoCraftProcess:
    exe = executable
    started = time.perf_counter()
    timings = {}

    sweep_stale_token_files()
    port = _free_tcp_port()
    token = secrets.token_hex(32)
    fd, token_path_str = tempfile.mkstemp(prefix="photocraft_token_", suffix=".txt")
    token_file = Path(token_path_str)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token)

    args = [
        exe,
        "--hosted",
        "--control", str(port),
        "--control-token-file", str(token_file),
        "--automation-read-root", read_root,
        "--automation-write-root", write_root,
        *extra_args,
    ]
    process = None
    control = None
    env = dict(os.environ)
    if os.name == "nt":
        # PhotoCraft's automatic backend can pick Vulkan, where game-overlay layers
        # (Epic, Galaxy, ReShade) leave the embedded canvas blank; DX12 renders correctly.
        env.setdefault("WGPU_BACKEND", "dx12")
    env.setdefault("RUST_BACKTRACE", "1")  # only a panic prints it
    log_file, log_path = _open_editor_log()
    try:
        try:
            # CREATE_NO_WINDOW: a debug or test build of the editor is a console program and would otherwise open a terminal window.
            process = subprocess.Popen(
                args,
                stdout=log_file,
                stderr=subprocess.STDOUT if log_path is not None else subprocess.DEVNULL,
                env=env,
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | (_BELOW_NORMAL_PRIORITY_CLASS if idle else 0)) if os.name == "nt" else 0,
            )
        finally:
            if log_path is not None:
                log_file.close()  # the editor has its own copy of the handle
        timings["spawn_ms"] = (time.perf_counter() - started) * 1000
        tie_to_this_process(process)
        control = _connect_with_retry(port, token, process)
        timings["control_ready_ms"] = (time.perf_counter() - started) * 1000
        control.require_hosted_handoff()
        timings["capabilities_ready_ms"] = (time.perf_counter() - started) * 1000
        hwnd = _find_window_for_pid(process.pid)
        if hwnd is None:
            raise PhotoCraftError("Could not find PhotoCraft's window")
        user32 = ctypes.windll.user32
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow(hwnd, 0)  # SW_HIDE until attached to the Qt host.
        # Respect PhotoCraft's persisted theme; the filmstrip follows its colors.
        timings["shell_configured_ms"] = (time.perf_counter() - started) * 1000
        proc = PhotoCraftProcess(process=process, control=control, hwnd=hwnd, token_file=token_file,
                                read_root=Path(read_root), write_root=Path(write_root), launch_timings=timings,
                                port=port, token=token, camera_raw_first="--raw-workspace" in extra_args, idle_priority=idle,
                                log_path=log_path)
        control.read_root = Path(read_root)
        if initial_path is None:
            return proc
        if on_window is not None:
            on_window(proc)
        try:
            relative = Path(initial_path).resolve().relative_to(Path(read_root).resolve()).as_posix()
        except ValueError as error:
            raise PhotoCraftError("Initial photo is outside the automation read root") from error
        # Open through the control channel so readiness includes decoding;
        # command-line opens may still be running when the HWND first appears.
        opened = control.app_open(relative, camera_raw=camera_raw)
        proc.camera_raw_rebaseline = bool((opened.get("cameraRaw") or {}).get("dialog"))
        timings["document_ready_ms"] = (time.perf_counter() - started) * 1000
    except BaseException:
        if control is not None:
            control.close()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3.0)
        token_file.unlink(missing_ok=True)
        raise

    return proc


_BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
_NORMAL_PRIORITY_CLASS = 0x00000020
_PROCESS_SET_INFORMATION = 0x0200


_editor_job = None  # the job object's handle, kept for the life of this process


def tie_to_this_process(process: subprocess.Popen) -> bool:
    """Makes sure the editor cannot outlive Image Triage. The editor now stays up for as long as the app does, and an app that
    crashes, is killed or restarts for development runs no cleanup: the editor is put in a Windows job object that ends its
    processes when the last handle to the job closes, which the system does for us when this process ends, however it ends.
    Returns whether it was tied (False elsewhere than Windows, or if the system refused: the editor then runs as before)."""
    global _editor_job
    if os.name != "nt":
        return False

    class _BasicLimits(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t), ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BasicLimits), ("IoInfo", _IoCounters), ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    try:
        if _editor_job is None:
            job = kernel32.CreateJobObjectW(None, None)
            if not job:
                return False
            limits = _ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel32.SetInformationJobObject(job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):  # JobObjectExtendedLimitInformation
                kernel32.CloseHandle(wintypes.HANDLE(job))
                return False
            _editor_job = job
        return bool(kernel32.AssignProcessToJobObject(_editor_job, wintypes.HANDLE(int(process._handle))))
    except (OSError, AttributeError, TypeError, ValueError):
        return False


def raise_priority(proc: PhotoCraftProcess) -> None:
    """Back to normal priority for an editor that was started in the background: from here on it is what the user is using."""
    if not proc.idle_priority:
        return
    proc.idle_priority = False
    if os.name != "nt":
        return
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.SetPriorityClass.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_PROCESS_SET_INFORMATION, False, proc.process.pid)
    if not handle:
        return
    try:
        kernel32.SetPriorityClass(handle, _NORMAL_PRIORITY_CLASS)
    finally:
        kernel32.CloseHandle(handle)


def embed_in_widget(photocraft_hwnd: int, parent_hwnd: int) -> None:
    """Reparent PhotoCraft's native window as a borderless child of ``parent_hwnd``."""
    user32 = ctypes.windll.user32
    get_style = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_style = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    user32.SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
    user32.SetParent.restype = wintypes.HWND
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]

    style = get_style(photocraft_hwnd, _GWL_STYLE)
    style &= ~(_WS_POPUP | _WS_CAPTION | _WS_THICKFRAME | _WS_SYSMENU | _WS_MINIMIZEBOX | _WS_MAXIMIZEBOX)
    style |= _WS_CHILD
    set_style(photocraft_hwnd, _GWL_STYLE, style)

    user32.SetParent(photocraft_hwnd, parent_hwnd)
    user32.SetWindowPos(photocraft_hwnd, 0, 0, 0, 0, 0, _SWP_NOZORDER | _SWP_NOACTIVATE | _SWP_FRAMECHANGED)
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow(photocraft_hwnd, 4)  # SW_SHOWNOACTIVATE, already a child.


def resize_embedded(photocraft_hwnd: int, width: int, height: int) -> None:
    user32 = ctypes.windll.user32
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos(photocraft_hwnd, 0, 0, 0, max(1, width), max(1, height), _SWP_NOZORDER | _SWP_NOACTIVATE)
