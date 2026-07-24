from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.sax.saxutils import escape

from .io import IMAGE_EXTENSIONS, open_image


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
    "adjust.tone_curve",
    "adjust.point_curve",
    "adjust.vignette_correction",
    "adjust.vignette",
    "adjust.chromatic_aberration",
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


def load_session(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        session = json.load(handle)
    version = session.get("version")
    if version != SCHEMA_VERSION:
        raise UnsupportedVersionError(f"unsupported session version: {version}")
    return session


def save_session(path: Path, session: Dict[str, Any]) -> None:
    session = deepcopy(session)
    session["updatedAt"] = utc_now()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("w", encoding="utf-8") as handle:
            json.dump(session, handle, indent=2)
            handle.write("\n")
    except OSError as exc:
        raise WriteError(str(exc)) from exc


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
    return errors


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
        shutil.copy2(png_path, target)
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
    with session_path.open("r", encoding="utf-8") as handle:
        session = json.load(handle)
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
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise WriteError(str(exc)) from exc
    return out_path
