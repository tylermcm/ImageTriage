from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape

from .io import IMAGE_EXTENSIONS, open_image, write_text_atomic


SCHEMA_VERSION = 1
SCHEMA_NAME = "photoedit-session-v1"
HASH_ALGORITHM = "blake2b-64"
HASH_MODE = "prefix-plus-size-v1"
HASH_PREFIX_BYTES = 64 * 1024 * 1024
PIPELINE_ORDERING = "fixed-renderer-order-v1"

RENDERER_ORDER = [
    "retouch.heal",
    "retouch.clone",
    "retouch.red_eye",
    "retouch.auto_dust",
    "transform.crop",
    "transform.crop_preset",
    "transform.rotate",
    "transform.perspective",
    "adjust.calibration",
    "adjust.exposure",
    "adjust.levels",
    "adjust.white_balance",
    "adjust.white_balance_kelvin",
    "adjust.contrast",
    "adjust.highlights",
    "adjust.shadows",
    "adjust.whites",
    "adjust.blacks",
    "adjust.vibrance",
    "adjust.saturation",
    "adjust.denoise",
    "adjust.luminance_noise",
    "adjust.color_noise",
    "adjust.clarity",
    "adjust.texture",
    "adjust.dehaze",
    "adjust.sharpen",
    "adjust.hsl_saturation_luminance",
    "adjust.point_color",
    "adjust.color_grading",
    "adjust.tone_curve",
    "adjust.point_curve",
    "adjust.vignette_correction",
    "adjust.vignette",
    "adjust.chromatic_aberration",
    "adjust.defringe",
    "adjust.grain",
]

SUPPORTED_OPERATION_TYPES = set(RENDERER_ORDER)
PIXEL_OPERATION_TYPES = {
    "retouch.heal",
    "retouch.clone",
    "retouch.red_eye",
    "transform.crop",
    "transform.crop_preset",
}
MASK_TYPES = {"radial", "linear-gradient", "bitmap", "subject-select"}
PARAMETRIC_MASK_TYPES = {"radial", "linear-gradient", "subject-select"}

CRS_EXPORT_MAP = {
    "adjust.exposure": ("Exposure2012", "exposure"),
    "adjust.contrast": ("Contrast2012", "contrast"),
    "adjust.highlights": ("Highlights2012", "highlights"),
    "adjust.shadows": ("Shadows2012", "shadows"),
    "adjust.whites": ("Whites2012", "whites"),
    "adjust.blacks": ("Blacks2012", "blacks"),
    "adjust.white_balance": (("Temperature", "Tint"), ("temperature", "tint")),
    "adjust.white_balance_kelvin": ("Temperature", "kelvin"),
    "adjust.vibrance": ("Vibrance", "vibrance"),
    "adjust.saturation": ("Saturation", "saturation"),
    "adjust.sharpen": ("Sharpness", "sharpen"),
}


class SessionError(Exception):
    exit_code = 1


class ValidationError(SessionError):
    exit_code = 2


class RelinkError(SessionError):
    exit_code = 3


class UnsupportedVersionError(SessionError):
    exit_code = 4


class WriteError(SessionError):
    exit_code = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_session_path(image_path: Path) -> Path:
    return image_path.with_name(f"{image_path.stem}.edit.json")


def asset_dir_for_session(session_path: Path) -> Path:
    name = session_path.name
    suffix = ".edit.json"
    stem = name[: -len(suffix)] if name.endswith(suffix) else session_path.stem
    return session_path.with_name(f"{stem}.edit-assets")


def compute_content_hash(path: Path, prefix_bytes: int = HASH_PREFIX_BYTES) -> Dict[str, Any]:
    stat = path.stat()
    hasher = hashlib.blake2b(digest_size=8)
    remaining = min(prefix_bytes, stat.st_size)
    with path.open("rb") as handle:
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            hasher.update(chunk)
            remaining -= len(chunk)
    hasher.update(str(stat.st_size).encode("ascii"))
    return {
        "algorithm": HASH_ALGORITHM,
        "mode": HASH_MODE,
        "prefixBytes": prefix_bytes,
        "value": f"b2b64:{hasher.hexdigest()}",
    }


def image_dimensions(path: Path) -> Tuple[Optional[int], Optional[int]]:
    try:
        with open_image(path) as image:
            return image.width, image.height
    except Exception:
        return None, None


def new_session(image_path: Path, out_path: Optional[Path] = None) -> Tuple[Path, Dict[str, Any]]:
    image_path = image_path.resolve()
    if not image_path.exists():
        raise SessionError(f"source image does not exist: {image_path}")
    out_path = out_path or default_session_path(image_path)
    stat = image_path.stat()
    width, height = image_dimensions(image_path)
    now = utc_now()
    session = {
        "version": SCHEMA_VERSION,
        "schema": SCHEMA_NAME,
        "createdAt": now,
        "updatedAt": now,
        "source": {
            "lastKnownPath": str(image_path),
            "fileName": image_path.name,
            "fileSizeBytes": stat.st_size,
            "mtimeNsAtHash": getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)),
            "contentHash": compute_content_hash(image_path),
            "embeddedImageId": {"kind": "raw-uuid", "value": None},
        },
        "coordinateSpaces": [
            {
                "id": "space-source-full",
                "sourceWidth": width,
                "sourceHeight": height,
                "cropInEffect": None,
            }
        ],
        "assets": {"dir": asset_dir_for_session(out_path).name, "bitmapMasks": []},
        "masks": [],
        "operations": [],
        "pipeline": {
            "ordering": PIPELINE_ORDERING,
            "rule": "Operations are stored as an ordered array. v1 validates operations against the current hardcoded renderer order.",
        },
    }
    save_session(out_path, session)
    return out_path, session


_LOG = logging.getLogger(__name__)

# A session is a list of settings, a few kilobytes; painted masks live in PNG files beside it. Past these sizes the file
# is damaged or not a session, and is refused before it is parsed or walked.
SESSION_MAX_BYTES = 32 * 1024 * 1024
SESSION_MAX_OPERATIONS = 10_000
SESSION_MAX_MASKS = 2_000
SESSION_MAX_SPACES = 2_000
SESSION_MAX_DEPTH = 12
SESSION_MAX_TEXT = 4096
SESSION_KNOWN_KEYS = frozenset((
    "version", "schema", "createdAt", "updatedAt", "source", "pipeline", "coordinateSpaces", "masks", "operations", "assets",
))
_SESSION_OBJECTS = ("source", "pipeline", "assets")
_SESSION_LISTS = (("operations", SESSION_MAX_OPERATIONS), ("masks", SESSION_MAX_MASKS), ("coordinateSpaces", SESSION_MAX_SPACES))


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not a number a session may hold")


def _check_values(value: Any, where: str, depth: int = 0) -> None:
    """Finite numbers, bounded text and bounded nesting all the way down."""
    if isinstance(value, float) and not math.isfinite(value):
        raise ValidationError(f"{where} must be a finite number")
    if isinstance(value, str) and len(value) > SESSION_MAX_TEXT and where.rsplit(".", 1)[-1] not in ("lastKnownPath", "path"):
        raise ValidationError(f"{where} is too long ({len(value)} characters)")
    if isinstance(value, (dict, list)):
        if depth >= SESSION_MAX_DEPTH:
            raise ValidationError(f"{where} is nested too deeply")
        items = value.items() if isinstance(value, dict) else enumerate(value)
        for key, item in items:
            _check_values(item, f"{where}.{key}" if isinstance(value, dict) else f"{where}[{key}]", depth + 1)


def check_session_structure(session: Any) -> Dict[str, Any]:
    """Raise ``ValidationError`` unless ``session`` has the shape every reader of a session relies on: an object whose
    operations, masks and coordinate spaces are bounded lists of objects, whose operations each carry an object of
    parameters, and which holds only finite numbers. Says what is wrong and where. Entries this version does not know
    are kept (another version wrote them) and logged."""
    if not isinstance(session, dict):
        raise ValidationError("a session file must hold a JSON object")
    for key in _SESSION_OBJECTS:
        if key in session and not isinstance(session[key], dict):
            raise ValidationError(f"session {key} must be an object")
    for key, limit in _SESSION_LISTS:
        entries = session.get(key, [])
        if not isinstance(entries, list):
            raise ValidationError(f"session {key} must be a list")
        if len(entries) > limit:
            raise ValidationError(f"session {key} has {len(entries)} entries; at most {limit} are read")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ValidationError(f"session {key}[{index}] must be an object")
    for index, op in enumerate(session.get("operations", [])):
        if "params" in op and not isinstance(op["params"], dict):
            raise ValidationError(f"session operations[{index}].params must be an object")
        if "type" in op and not isinstance(op["type"], str):
            raise ValidationError(f"session operations[{index}].type must be text")
        if "enabled" in op and not isinstance(op["enabled"], bool):
            raise ValidationError(f"session operations[{index}].enabled must be true or false")
        if op.get("id") is not None and not isinstance(op["id"], str):
            raise ValidationError(f"session operations[{index}].id must be text")
        if op.get("maskId") is not None and not isinstance(op["maskId"], str):
            raise ValidationError(f"session operations[{index}].maskId must be text or null")
    content_hash = session.get("source", {}).get("contentHash")
    if content_hash is not None and not isinstance(content_hash, dict):
        raise ValidationError("session source.contentHash must be an object")
    bitmap_masks = session.get("assets", {}).get("bitmapMasks", [])
    if not isinstance(bitmap_masks, list) or not all(isinstance(item, dict) for item in bitmap_masks):
        raise ValidationError("session assets.bitmapMasks must be a list of objects")
    _check_values(session, "session")
    unknown = tuple(sorted(str(key) for key in session if key not in SESSION_KNOWN_KEYS))
    if unknown and unknown not in _WARNED_UNKNOWN:      # once per set of names: sessions are read on every scan
        _WARNED_UNKNOWN.add(unknown)
        _LOG.warning("Session holds entries this version does not use (kept as they are): %s", ", ".join(unknown[:20]))
    return session


_WARNED_UNKNOWN: set = set()


def read_session_file(path: Path) -> Dict[str, Any]:
    """The session file parsed and structurally checked, whatever its version."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError:
        size = 0                     # let the open below report what is wrong with the file
    if size > SESSION_MAX_BYTES:
        raise ValidationError(f"{path.name} is {size} bytes; a session file is at most {SESSION_MAX_BYTES}")
    try:
        with path.open("r", encoding="utf-8") as handle:
            session = json.load(handle, parse_constant=_refuse_constant)
    except RecursionError as exc:
        raise ValidationError(f"{path.name} is nested too deeply to be a session") from exc
    except ValueError as exc:        # not JSON, not UTF-8, or NaN/Infinity
        raise ValidationError(f"{path.name} is not a readable session: {exc}") from exc
    return check_session_structure(session)


def load_session(path: Path) -> Dict[str, Any]:
    """Read a session for use. Raises ``ValidationError`` for a file that is not a session of the expected shape (see
    ``check_session_structure``) and ``UnsupportedVersionError`` for one of another version; whether its operations make
    sense for the renderer is ``validate_session``'s question."""
    session = read_session_file(path)
    version = session.get("version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise UnsupportedVersionError(f"unsupported session version: {version}")
    return session


def save_session(path: Path, session: Dict[str, Any]) -> None:
    session = deepcopy(session)
    session["updatedAt"] = utc_now()
    try:
        # NaN and Infinity are not JSON, and a session holding one could not be loaded again.
        text = json.dumps(session, indent=2, allow_nan=False) + "\n"
    except ValueError as exc:
        raise ValidationError(f"the session holds a number that cannot be saved: {exc}") from exc
    try:
        # Never opened for writing in place: a failure part way must leave the session that was there readable.
        write_text_atomic(path, text)
    except OSError as exc:
        raise WriteError(str(exc)) from exc


def copy_file_atomic(source: Path, target: Path) -> None:
    """Copy ``source`` to ``target`` through a temporary file beside the target, flushed and then moved into place: a
    copy that fails part way leaves what was at ``target`` as it was."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(handle, "wb") as out, Path(source).open("rb") as stream:
            shutil.copyfileobj(stream, out)
            out.flush()
            os.fsync(out.fileno())
        try:
            shutil.copystat(source, temporary)
        except OSError:
            pass
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def print_json(data: Any) -> None:
    print(json.dumps(data, indent=2))


def parse_value(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered == "null":
        return None
    if "," in value:
        return [parse_value(part.strip()) for part in value.split(",")]
    try:
        if any(marker in value for marker in (".", "e", "E")):
            return float(value)
        return int(value)
    except ValueError:
        return value


def parse_param_pairs(values: Optional[List[str]]) -> Dict[str, Any]:
    params: Dict[str, Any] = {}
    for item in values or []:
        if "=" not in item:
            raise SessionError(f"parameter must be key=value: {item}")
        key, value = item.split("=", 1)
        if not key:
            raise SessionError(f"parameter key is empty: {item}")
        params[key] = parse_value(value)
    return params


def op_sort_key(op_type: str) -> int:
    if op_type not in SUPPORTED_OPERATION_TYPES:
        raise ValidationError(f"unsupported operation type: {op_type}")
    return RENDERER_ORDER.index(op_type)


def space_ids(session: Dict[str, Any]) -> set:
    return {space.get("id") for space in session.get("coordinateSpaces", [])}


def mask_ids(session: Dict[str, Any]) -> set:
    return {mask.get("id") for mask in session.get("masks", [])}


def operation_ids(session: Dict[str, Any]) -> set:
    return {op.get("id") for op in session.get("operations", [])}


def validate_session(session: Dict[str, Any], strict: bool = False, session_path: Optional[Path] = None) -> List[str]:
    errors: List[str] = []
    if session.get("version") != SCHEMA_VERSION:
        raise UnsupportedVersionError(f"unsupported session version: {session.get('version')}")
    if session.get("schema") != SCHEMA_NAME:
        errors.append("schema must be photoedit-session-v1")
    if session.get("pipeline", {}).get("ordering") != PIPELINE_ORDERING:
        errors.append("pipeline.ordering must be fixed-renderer-order-v1")
    if not session.get("source", {}).get("contentHash"):
        errors.append("source.contentHash is required")

    spaces = space_ids(session)
    masks = mask_ids(session)
    seen_ops = set()
    previous_key = -1
    for op in session.get("operations", []):
        op_id = op.get("id")
        op_type = op.get("type")
        if not op_id:
            errors.append("operation id is required")
        elif op_id in seen_ops:
            errors.append(f"duplicate operation id: {op_id}")
        seen_ops.add(op_id)
        if op_type not in SUPPORTED_OPERATION_TYPES:
            errors.append(f"unsupported operation type: {op_type}")
            continue
        key = op_sort_key(op_type)
        if key < previous_key:
            errors.append(f"operation order violates {PIPELINE_ORDERING}: {op_id}")
        previous_key = key
        if op.get("maskId") is not None and op.get("maskId") not in masks:
            errors.append(f"operation references missing mask: {op_id}")
        if op_type in PIXEL_OPERATION_TYPES and not op.get("coordinateSpaceId"):
            errors.append(f"pixel operation missing coordinateSpaceId: {op_id}")
        if op.get("coordinateSpaceId") and op.get("coordinateSpaceId") not in spaces:
            errors.append(f"operation references missing coordinate space: {op_id}")
        params = op.get("params", {})
        errors.extend(validate_operation_params(op_id, op_type, params))

    bitmap_assets = {asset.get("id"): asset for asset in session.get("assets", {}).get("bitmapMasks", [])}
    masks_by_id = {mask.get("id"): mask for mask in session.get("masks", [])}
    seen_masks = set()
    for mask in session.get("masks", []):
        mask_id = mask.get("id")
        mask_type = mask.get("type")
        # Mask groups: a child mask carries parentId referencing a top-level
        # mask; the group renders as the union of parent and children. One
        # level deep only.
        parent_id = mask.get("parentId")
        if parent_id is not None:
            parent = masks_by_id.get(parent_id)
            if parent is None or parent_id == mask_id:
                errors.append(f"mask references missing parent: {mask_id}")
            elif parent.get("parentId"):
                errors.append(f"submask parent must be a top-level mask: {mask_id}")
        combine = mask.get("combine")
        if combine is not None:
            if combine not in ("add", "subtract"):
                errors.append(f"mask combine must be add or subtract: {mask_id}")
            elif parent_id is None:
                errors.append(f"combine mode only applies to submasks: {mask_id}")
        if not mask_id:
            errors.append("mask id is required")
        elif mask_id in seen_masks:
            errors.append(f"duplicate mask id: {mask_id}")
        seen_masks.add(mask_id)
        if mask_type not in MASK_TYPES:
            errors.append(f"unsupported mask type: {mask_type}")
            continue
        if mask_type in PARAMETRIC_MASK_TYPES and not mask.get("coordinateSpaceId"):
            errors.append(f"parametric mask missing coordinateSpaceId: {mask_id}")
        if mask.get("coordinateSpaceId") and mask.get("coordinateSpaceId") not in spaces:
            errors.append(f"mask references missing coordinate space: {mask_id}")
        if mask_type == "bitmap" and mask.get("assetId") not in bitmap_assets:
            errors.append(f"bitmap mask references missing asset: {mask_id}")
        if mask_type == "subject-select":
            model = mask.get("model") or {}
            if not (model.get("id") and model.get("version") and model.get("weightsHash")):
                errors.append(f"subject mask must pin model id/version/weightsHash: {mask_id}")
            if not mask.get("cacheAssetId"):
                errors.append(f"subject mask requires cached bitmap asset: {mask_id}")
            params = mask.get("params") or {}
            refinement_ranges = {
                "edgeDetectionRadius": (0.0, 250.0),
                "edgeSmooth": (0.0, 100.0),
                "edgeFeather": (0.0, 1000.0),
                "edgeContrast": (0.0, 100.0),
                "edgeShift": (-100.0, 100.0),
            }
            for key, (minimum, maximum) in refinement_ranges.items():
                if key not in params:
                    continue
                value = params[key]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not minimum <= float(value) <= maximum
                ):
                    errors.append(
                        f"subject mask {key} must be {minimum:g}..{maximum:g}: {mask_id}"
                    )
            for key in ("selectionCleared", "invert"):
                if key in params and not isinstance(params[key], bool):
                    errors.append(f"subject mask {key} must be boolean: {mask_id}")
        for refinement in mask.get("refinements", []):
            if refinement.get("space") and refinement.get("space") not in spaces:
                errors.append(f"mask refinement references missing coordinate space: {mask_id}")

    if strict and session_path:
        asset_root = session_path.parent / session.get("assets", {}).get("dir", "")
        for asset in bitmap_assets.values():
            asset_path = session_path.parent / asset.get("path", "")
            if not asset_path.exists() and not (asset_root / Path(asset.get("path", "")).name).exists():
                errors.append(f"missing bitmap asset file: {asset.get('path')}")
    if errors:
        raise ValidationError("; ".join(errors))
    return errors


def validate_operation_params(op_id: str, op_type: str, params: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    if op_type == "adjust.white_balance_kelvin":
        kelvin = params.get("kelvin")
        if not isinstance(kelvin, (int, float)) or not 1500 <= kelvin <= 50000:
            errors.append(f"{op_id} adjust.white_balance_kelvin requires kelvin 1500..50000")
        tint = params.get("tint", 0)
        if not isinstance(tint, (int, float)) or not -150 <= tint <= 150:
            errors.append(f"{op_id} tint must be -150..150")
    elif op_type == "adjust.levels":
        black = params.get("black")
        midpoint = params.get("midpoint")
        white = params.get("white")
        if not all(isinstance(value, (int, float)) for value in (black, midpoint, white)):
            errors.append(f"{op_id} adjust.levels requires black, midpoint, white")
        elif not (0 <= black < white <= 255 and 0.05 <= midpoint <= 10.0):
            errors.append(f"{op_id} levels must satisfy 0 <= black < white <= 255 and midpoint 0.05..10")
    elif op_type == "adjust.point_curve":
        channel = params.get("channel", "rgb")
        if channel not in {"rgb", "red", "green", "blue"}:
            errors.append(f"{op_id} point_curve channel must be rgb/red/green/blue")
        points = params.get("points")
        if not isinstance(points, list) or len(points) < 2:
            errors.append(f"{op_id} adjust.point_curve requires points=[[x,y],...]")
        else:
            previous_x = -1
            for point in points:
                if not isinstance(point, list) or len(point) != 2:
                    errors.append(f"{op_id} point_curve points must be [x,y] pairs")
                    break
                x, y = point
                if not all(isinstance(value, (int, float)) for value in (x, y)):
                    errors.append(f"{op_id} point_curve point values must be numeric")
                    break
                if not (0 <= x <= 255 and 0 <= y <= 255 and x > previous_x):
                    errors.append(f"{op_id} point_curve x/y must be 0..255 and x values strictly increasing")
                    break
                previous_x = x
    elif op_type == "transform.crop_preset":
        preset = params.get("preset")
        if preset not in {"1:1", "3:2", "4:3", "4:5", "5:4", "16:9", "9:16"}:
            errors.append(f"{op_id} crop preset must be one of 1:1, 3:2, 4:3, 4:5, 5:4, 16:9, 9:16")
        anchor = params.get("anchor", "center")
        if anchor not in {"center", "top", "bottom", "left", "right"}:
            errors.append(f"{op_id} crop preset anchor must be center/top/bottom/left/right")
    elif op_type == "adjust.color_grading":
        for zone in ("shadow", "midtone", "highlight", "global"):
            errors.extend(_range_errors(op_id, params, f"{zone}Hue", 0.0, 360.0))
            errors.extend(_range_errors(op_id, params, f"{zone}Sat", 0.0, 100.0))
            errors.extend(_range_errors(op_id, params, f"{zone}Lum", -100.0, 100.0))
        errors.extend(_range_errors(op_id, params, "blending", 0.0, 100.0))
        errors.extend(_range_errors(op_id, params, "balance", -100.0, 100.0))
    elif op_type == "adjust.calibration":
        errors.extend(_range_errors(op_id, params, "shadowTint", -100.0, 100.0))
        for primary in ("red", "green", "blue"):
            errors.extend(_range_errors(op_id, params, f"{primary}Hue", -100.0, 100.0))
            errors.extend(_range_errors(op_id, params, f"{primary}Saturation", -100.0, 100.0))
    elif op_type == "adjust.defringe":
        for band in ("purple", "green"):
            errors.extend(_range_errors(op_id, params, f"{band}Amount", 0.0, 20.0))
            errors.extend(_range_errors(op_id, params, f"{band}HueLow", 0.0, 100.0))
            errors.extend(_range_errors(op_id, params, f"{band}HueHigh", 0.0, 100.0))
    elif op_type == "adjust.hsl_saturation_luminance":
        for band in ("red", "orange", "yellow", "green", "aqua", "blue", "purple", "magenta"):
            for channel in ("Hue", "Saturation", "Luminance"):
                errors.extend(_range_errors(op_id, params, f"{band}{channel}", -100.0, 100.0))
        errors.extend(_range_errors(op_id, params, "luminance", -100.0, 100.0))
    elif op_type == "adjust.grain":
        errors.extend(_range_errors(op_id, params, "grain", -100.0, 100.0))
        errors.extend(_range_errors(op_id, params, "size", 0.0, 100.0))
        errors.extend(_range_errors(op_id, params, "roughness", 0.0, 100.0))
    elif op_type == "adjust.point_color":
        entries = params.get("colors")
        if not isinstance(entries, list) or not entries:
            errors.append(f"{op_id} adjust.point_color requires a non-empty colors list")
        else:
            for entry in entries:
                if not isinstance(entry, dict):
                    errors.append(f"{op_id} point_color entries must be objects")
                    break
                for field, low, high in (
                    ("h", 0.0, 255.0),
                    ("s", 0.0, 255.0),
                    ("l", 0.0, 255.0),
                    ("range", 1.0, 100.0),
                ):
                    value = entry.get(field)
                    if not isinstance(value, (int, float)) or not low <= value <= high:
                        errors.append(f"{op_id} point_color {field} must be {low:g}..{high:g}")
                        break
    return errors


def _range_errors(
    op_id: str, params: Dict[str, Any], key: str, low: float, high: float
) -> List[str]:
    """Check one optional numeric param. Absent is fine — these ops carry only
    the fields the GUI actually set — but present-and-wrong is not, and an
    unvalidated NaN only surfaces as a crash on the next load."""
    if key not in params:
        return []
    value = params[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return [f"{op_id} {key} must be numeric"]
    if value != value:  # NaN
        return [f"{op_id} {key} must be a finite number"]
    if not low <= value <= high:
        return [f"{op_id} {key} must be {low:g}..{high:g}"]
    return []


def insert_operation(session: Dict[str, Any], op: Dict[str, Any], before: Optional[str], after: Optional[str], first: bool, last: bool) -> None:
    ops = session.setdefault("operations", [])
    choices = [before is not None, after is not None, first, last]
    if sum(1 for choice in choices if choice) > 1:
        raise SessionError("choose only one of --before, --after, --first, --last")
    if op["id"] in operation_ids(session):
        raise SessionError(f"operation id already exists: {op['id']}")
    if first:
        index = 0
    elif before:
        index = find_operation_index(session, before)
    elif after:
        index = find_operation_index(session, after) + 1
    else:
        index = len(ops)
    ops.insert(index, op)
    validate_session(session)


def find_operation_index(session: Dict[str, Any], op_id: str) -> int:
    for index, op in enumerate(session.get("operations", [])):
        if op.get("id") == op_id:
            return index
    raise SessionError(f"operation not found: {op_id}")


def find_mask(session: Dict[str, Any], mask_id: str) -> Dict[str, Any]:
    for mask in session.get("masks", []):
        if mask.get("id") == mask_id:
            return mask
    raise SessionError(f"mask not found: {mask_id}")


def ensure_space(session: Dict[str, Any], space_id: str) -> None:
    if space_id not in space_ids(session):
        raise SessionError(f"coordinate space not found: {space_id}")


def upsert_mask(session: Dict[str, Any], mask: Dict[str, Any]) -> None:
    masks = session.setdefault("masks", [])
    for index, existing in enumerate(masks):
        if existing.get("id") == mask.get("id"):
            masks[index] = mask
            validate_session(session)
            return
    masks.append(mask)
    validate_session(session)


def add_space(session: Dict[str, Any], space: Dict[str, Any]) -> None:
    spaces = session.setdefault("coordinateSpaces", [])
    for index, existing in enumerate(spaces):
        if existing.get("id") == space.get("id"):
            spaces[index] = space
            return
    spaces.append(space)


def copy_bitmap_asset(session_path: Path, session: Dict[str, Any], mask_id: str, space_id: str, png_path: Path) -> Dict[str, Any]:
    ensure_space(session, space_id)
    if not png_path.exists():
        raise SessionError(f"mask PNG does not exist: {png_path}")
    asset_dir = session_path.parent / session.get("assets", {}).get("dir", asset_dir_for_session(session_path).name)
    asset_dir.mkdir(parents=True, exist_ok=True)
    target = asset_dir / f"{mask_id}.png"
    try:
        copy_file_atomic(png_path, target)
    except OSError as exc:
        raise WriteError(str(exc)) from exc
    asset = {
        "id": mask_id,
        "path": f"{asset_dir.name}/{target.name}",
        "coordinateSpaceId": space_id,
    }
    assets = session.setdefault("assets", {}).setdefault("bitmapMasks", [])
    assets[:] = [item for item in assets if item.get("id") != mask_id]
    assets.append(asset)
    return asset


def remove_mask(session: Dict[str, Any], mask_id: str, force: bool = False) -> None:
    references = [op.get("id") for op in session.get("operations", []) if op.get("maskId") == mask_id]
    if references and not force:
        raise SessionError(f"mask is referenced by operations: {', '.join(references)}")
    session["masks"] = [mask for mask in session.get("masks", []) if mask.get("id") != mask_id]


def iter_candidate_images(root: Path) -> Iterable[Path]:
    for child in root.rglob("*"):
        if child.is_file() and child.suffix.lower() in IMAGE_EXTENSIONS:
            yield child


def iter_sessions(root: Path, glob_pattern: str = "*.edit.json") -> Iterable[Path]:
    yield from root.rglob(glob_pattern)


def relink(root: Path, glob_pattern: str = "*.edit.json") -> Dict[str, Any]:
    session_paths = list(iter_sessions(root, glob_pattern))
    candidates_by_size: Dict[int, List[Path]] = {}
    for candidate in iter_candidate_images(root):
        try:
            candidates_by_size.setdefault(candidate.stat().st_size, []).append(candidate)
        except OSError:
            continue

    results = []
    failures = []
    hash_cache: Dict[Tuple[Path, int], str] = {}
    for session_path in session_paths:
        session = load_session(session_path)
        source = session.get("source", {})
        expected_size = source.get("fileSizeBytes")
        expected_hash = source.get("contentHash", {}).get("value")
        prefix_bytes = source.get("contentHash", {}).get("prefixBytes", HASH_PREFIX_BYTES)
        matches = []
        for candidate in candidates_by_size.get(expected_size, []):
            cache_key = (candidate.resolve(), prefix_bytes)
            if cache_key not in hash_cache:
                hash_cache[cache_key] = compute_content_hash(candidate, prefix_bytes).get("value")
            if hash_cache[cache_key] == expected_hash:
                matches.append(str(candidate.resolve()))
        if len(matches) == 1:
            session["source"]["lastKnownPath"] = matches[0]
            save_session(session_path, session)
            results.append({"session": str(session_path), "status": "relinked", "path": matches[0]})
        elif len(matches) == 0:
            failures.append({"session": str(session_path), "status": "missing", "candidates": []})
        else:
            failures.append({"session": str(session_path), "status": "ambiguous", "candidates": sorted(matches)})
    if failures:
        raise RelinkError(json.dumps({"results": results, "failures": failures}, indent=2))
    return {"results": results, "failures": []}


def migrate_session_file(session_path: Path, out_path: Optional[Path] = None, target_version: int = SCHEMA_VERSION) -> Path:
    session = read_session_file(session_path)
    version = session.get("version")
    if target_version != SCHEMA_VERSION:
        raise UnsupportedVersionError(f"unsupported target version: {target_version}")
    if version != SCHEMA_VERSION:
        raise UnsupportedVersionError(f"no migration path from version {version} to {target_version}")
    validate_session(session, session_path=session_path)
    target = out_path or session_path
    save_session(target, session)
    return target


def collect_xmp_crs(session: Dict[str, Any]) -> Dict[str, Any]:
    crs: Dict[str, Any] = {}
    for op in session.get("operations", []):
        if not op.get("enabled", True) or op.get("maskId") is not None:
            continue
        mapping = CRS_EXPORT_MAP.get(op.get("type"))
        if not mapping:
            continue
        crs_key, param_key = mapping
        params = op.get("params", {})
        if isinstance(crs_key, tuple):
            for field, key in zip(crs_key, param_key):
                if key in params:
                    crs[field] = params[key]
        elif param_key in params:
            crs[crs_key] = params[param_key]
    return crs


def export_xmp(session_path: Path, out_path: Optional[Path] = None) -> Path:
    session = load_session(session_path)
    validate_session(session, session_path=session_path)
    if out_path is None:
        name = session_path.name
        out_name = f"{name[:-len('.edit.json')]}.xmp" if name.endswith(".edit.json") else f"{session_path.stem}.xmp"
        out_path = session_path.with_name(out_name)
    crs = collect_xmp_crs(session)
    lines = [
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>',
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">',
        '  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">',
        '    <rdf:Description xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/">',
    ]
    for key in sorted(crs):
        lines.append(f"      <crs:{key}>{escape(str(crs[key]))}</crs:{key}>")
    lines.extend(
        [
            "    </rdf:Description>",
            "  </rdf:RDF>",
            "</x:xmpmeta>",
            "<?xpacket end=\"w\"?>",
        ]
    )
    try:
        write_text_atomic(out_path, "\n".join(lines) + "\n")
    except OSError as exc:
        raise WriteError(str(exc)) from exc
    return out_path
