from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from PIL import ExifTags

from .adjustments import EditRecipe, heal_spot
from .io import open_image, save_image
from .preview import ansi_preview
from .session import (
    RelinkError,
    SessionError,
    SUPPORTED_OPERATION_TYPES,
    ValidationError,
    WriteError,
    add_space,
    copy_bitmap_asset,
    export_xmp,
    find_operation_index,
    insert_operation,
    load_session,
    mask_ids,
    migrate_session_file,
    new_session,
    operation_ids,
    parse_param_pairs,
    print_json,
    relink,
    remove_mask,
    save_session,
    space_ids,
    upsert_mask,
    validate_session,
)


def add_adjustment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--recipe", type=Path, help="JSON recipe to load before command-line overrides.")
    parser.add_argument("--exposure", type=float, default=0.0, help="Exposure in EV stops.")
    parser.add_argument("--contrast", type=float, default=0.0)
    parser.add_argument("--highlights", type=float, default=0.0)
    parser.add_argument("--shadows", type=float, default=0.0)
    parser.add_argument("--whites", type=float, default=0.0)
    parser.add_argument("--blacks", type=float, default=0.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--tint", type=float, default=0.0)
    parser.add_argument("--vibrance", type=float, default=0.0)
    parser.add_argument("--saturation", type=float, default=0.0)
    parser.add_argument("--clarity", type=float, default=0.0)
    parser.add_argument("--dehaze", type=float, default=0.0)
    parser.add_argument("--sharpen", type=float, default=0.0)
    parser.add_argument("--denoise", type=float, default=0.0)
    parser.add_argument("--vignette", type=float, default=0.0)
    parser.add_argument("--vignette-midpoint", type=float, default=50.0, help="Where the vignette starts, 0..100.")
    parser.add_argument("--vignette-roundness", type=float, default=0.0, help="-100 frame-shaped .. 100 circular.")
    parser.add_argument("--vignette-feather", type=float, default=50.0, help="Falloff softness, 0..100.")
    parser.add_argument("--vignette-highlights", type=float, default=0.0, help="Protect bright pixels, 0..100.")
    parser.add_argument("--rotate", type=float, default=0.0)
    parser.add_argument("--crop", nargs=4, type=int, metavar=("LEFT", "TOP", "RIGHT", "BOTTOM"))


def recipe_from_args(args: argparse.Namespace) -> EditRecipe:
    cli_recipe = EditRecipe(
        exposure=args.exposure,
        contrast=args.contrast,
        highlights=args.highlights,
        shadows=args.shadows,
        whites=args.whites,
        blacks=args.blacks,
        temperature=args.temperature,
        tint=args.tint,
        vibrance=args.vibrance,
        saturation=args.saturation,
        clarity=args.clarity,
        dehaze=args.dehaze,
        sharpen=args.sharpen,
        denoise=args.denoise,
        vignette=args.vignette,
        vignette_midpoint=args.vignette_midpoint,
        vignette_roundness=args.vignette_roundness,
        vignette_feather=args.vignette_feather,
        vignette_highlights=args.vignette_highlights,
        rotate=args.rotate,
        crop=tuple(args.crop) if args.crop else None,
    )
    if args.recipe:
        return EditRecipe.load(args.recipe).merged(cli_recipe)
    return cli_recipe


def command_inspect(args: argparse.Namespace) -> int:
    image = open_image(args.image)
    print(f"path: {Path(args.image).resolve()}")
    print(f"format: {image.format}")
    print(f"mode: {image.mode}")
    print(f"size: {image.width}x{image.height}")

    exif = image.getexif()
    if exif:
        wanted = {"Make", "Model", "LensModel", "DateTimeOriginal", "FNumber", "ExposureTime", "ISOSpeedRatings", "FocalLength"}
        tag_names = {value: key for key, value in ExifTags.TAGS.items()}
        for name in sorted(wanted):
            tag = tag_names.get(name)
            if tag and tag in exif:
                print(f"exif.{name}: {exif[tag]}")

    return 0


def command_preview(args: argparse.Namespace) -> int:
    image = open_image(args.image)
    print(ansi_preview(image, width=args.width, height=args.height))
    return 0


def command_recipe(args: argparse.Namespace) -> int:
    recipe = recipe_from_args(args)
    recipe.save(args.output)
    print(f"saved recipe: {args.output}")
    return 0


def command_render(args: argparse.Namespace) -> int:
    recipe = recipe_from_args(args)
    image = open_image(args.input)
    edited = recipe.apply(image)
    save_image(edited, args.output, quality=args.quality)
    print(f"rendered: {args.output}")
    return 0


def command_spot(args: argparse.Namespace) -> int:
    image = open_image(args.input)
    edited = heal_spot(image, x=args.x, y=args.y, radius=args.radius, strength=args.strength)
    save_image(edited, args.output, quality=args.quality)
    print(f"spot edit saved: {args.output}")
    return 0


def command_session_new(args: argparse.Namespace) -> int:
    path, session = new_session(args.image, args.out)
    if args.json:
        print_json({"path": str(path), "session": session})
    else:
        print(f"created session: {path}")
    return 0


def command_session_info(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    validate_session(session, session_path=args.session if args.strict else None, strict=args.strict)
    summary = {
        "version": session.get("version"),
        "schema": session.get("schema"),
        "source": session.get("source"),
        "coordinateSpaces": len(session.get("coordinateSpaces", [])),
        "masks": len(session.get("masks", [])),
        "operations": len(session.get("operations", [])),
        "pipeline": session.get("pipeline"),
    }
    if args.json:
        print_json(summary)
    else:
        print(f"version: {summary['version']}")
        print(f"source: {summary['source'].get('lastKnownPath')}")
        print(f"masks: {summary['masks']}")
        print(f"operations: {summary['operations']}")
        print(f"pipeline: {summary['pipeline'].get('ordering')}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    validate_session(session, strict=args.strict, session_path=args.session)
    if args.json:
        print_json({"valid": True})
    else:
        print("valid")
    return 0


def command_migrate(args: argparse.Namespace) -> int:
    path = migrate_session_file(args.session, args.out, args.to)
    if args.json:
        print_json({"path": str(path), "version": args.to})
    else:
        print(path)
    return 0


def next_operation_id(session: dict) -> str:
    used = operation_ids(session)
    index = 1
    while True:
        op_id = f"op-{index:03d}"
        if op_id not in used:
            return op_id
        index += 1


def command_op_add(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    params = parse_param_pairs(args.param)
    if args.type not in SUPPORTED_OPERATION_TYPES:
        raise ValidationError(f"unsupported operation type: {args.type}")
    op = {
        "id": args.id or next_operation_id(session),
        "type": args.type,
        "enabled": True,
        "params": params,
    }
    if args.mask:
        if args.mask not in mask_ids(session):
            raise SessionError(f"mask not found: {args.mask}")
        op["maskId"] = args.mask
    else:
        op["maskId"] = None
    if args.space:
        if args.space not in space_ids(session):
            raise SessionError(f"coordinate space not found: {args.space}")
        op["coordinateSpaceId"] = args.space
    insert_operation(session, op, args.before, args.after, args.first, args.last)
    save_session(args.session, session)
    print(op["id"])
    return 0


def command_op_set(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    index = find_operation_index(session, args.op_id)
    op = session["operations"][index]
    op.setdefault("params", {}).update(parse_param_pairs(args.param))
    if args.enabled is not None:
        op["enabled"] = args.enabled.lower() == "true"
    if args.mask:
        if args.mask == "__global__":
            op["maskId"] = None
        else:
            if args.mask not in mask_ids(session):
                raise SessionError(f"mask not found: {args.mask}")
            op["maskId"] = args.mask
    if args.space:
        if args.space not in space_ids(session):
            raise SessionError(f"coordinate space not found: {args.space}")
        op["coordinateSpaceId"] = args.space
    validate_session(session)
    save_session(args.session, session)
    print(args.op_id)
    return 0


def command_op_move(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    ops = session.get("operations", [])
    index = find_operation_index(session, args.op_id)
    op = ops.pop(index)
    insert_operation(session, op, args.before, args.after, args.first, args.last)
    save_session(args.session, session)
    print(args.op_id)
    return 0


def command_op_delete(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    index = find_operation_index(session, args.op_id)
    removed = session["operations"].pop(index)
    save_session(args.session, session)
    print(removed["id"])
    return 0


def add_typed_operation(args: argparse.Namespace, op_type: str, params: dict, space: str = None, mask: str = None) -> str:
    session = load_session(args.session)
    op = {
        "id": args.id or next_operation_id(session),
        "type": op_type,
        "enabled": True,
        "maskId": mask,
        "params": params,
    }
    if space:
        if space not in space_ids(session):
            raise SessionError(f"coordinate space not found: {space}")
        op["coordinateSpaceId"] = space
    if mask and mask not in mask_ids(session):
        raise SessionError(f"mask not found: {mask}")
    insert_operation(session, op, args.before, args.after, args.first, args.last)
    save_session(args.session, session)
    return op["id"]


def command_wb_kelvin(args: argparse.Namespace) -> int:
    op_id = add_typed_operation(
        args,
        "adjust.white_balance_kelvin",
        {"kelvin": args.kelvin, "tint": args.tint},
        mask=args.mask,
    )
    print(op_id)
    return 0


def command_levels(args: argparse.Namespace) -> int:
    op_id = add_typed_operation(
        args,
        "adjust.levels",
        {"black": args.black, "midpoint": args.midpoint, "white": args.white},
        mask=args.mask,
    )
    print(op_id)
    return 0


def parse_curve_point(value: str) -> list:
    parts = value.split(",")
    if len(parts) != 2:
        raise SessionError(f"curve point must be x,y: {value}")
    return [float(parts[0]), float(parts[1])]


def command_point_curve(args: argparse.Namespace) -> int:
    points = [parse_curve_point(point) for point in args.point]
    op_id = add_typed_operation(args, "adjust.point_curve", {"points": points}, mask=args.mask)
    print(op_id)
    return 0


def command_crop_preset(args: argparse.Namespace) -> int:
    op_id = add_typed_operation(
        args,
        "transform.crop_preset",
        {"preset": args.preset, "anchor": args.anchor},
        space=args.space,
    )
    print(op_id)
    return 0


def command_space_add(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    crop = [int(part) for part in args.crop.split(",")] if args.crop else None
    if crop is not None and len(crop) != 4:
        raise SessionError("--crop must be left,top,right,bottom")
    add_space(
        session,
        {
            "id": args.id,
            "sourceWidth": args.source_width,
            "sourceHeight": args.source_height,
            "cropInEffect": crop,
        },
    )
    validate_session(session)
    save_session(args.session, session)
    print(args.id)
    return 0


def command_mask_radial(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    if args.space not in space_ids(session):
        raise SessionError(f"coordinate space not found: {args.space}")
    upsert_mask(
        session,
        {
            "id": args.id,
            "type": "radial",
            "coordinateSpaceId": args.space,
            "params": {
                "cx": args.cx,
                "cy": args.cy,
                "rx": args.rx,
                "ry": args.ry,
                "angle": args.angle,
                "feather": args.feather,
                "density": args.density,
                "invert": args.invert,
            },
        },
    )
    save_session(args.session, session)
    print(args.id)
    return 0


def command_mask_gradient(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    if args.space not in space_ids(session):
        raise SessionError(f"coordinate space not found: {args.space}")
    upsert_mask(
        session,
        {
            "id": args.id,
            "type": "linear-gradient",
            "coordinateSpaceId": args.space,
            "params": {
                "x1": args.x1,
                "y1": args.y1,
                "x2": args.x2,
                "y2": args.y2,
                "feather": args.feather,
                "density": args.density,
                "invert": args.invert,
            },
        },
    )
    save_session(args.session, session)
    print(args.id)
    return 0


def command_mask_painted_add(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    copy_bitmap_asset(args.session, session, args.id, args.space, args.png)
    upsert_mask(session, {"id": args.id, "type": "bitmap", "assetId": args.id})
    save_session(args.session, session)
    print(args.id)
    return 0


def command_mask_subject(args: argparse.Namespace) -> int:
    if not args.cache_png:
        raise SessionError("subject masks require --cache-png until a segmenter is implemented")
    session = load_session(args.session)
    copy_bitmap_asset(args.session, session, f"{args.id}-cache", args.space, args.cache_png)
    upsert_mask(
        session,
        {
            "id": args.id,
            "type": "subject-select",
            "coordinateSpaceId": args.space,
            "model": {
                "id": args.model_id,
                "version": args.model_version,
                "weightsHash": args.weights_hash,
            },
            "cacheAssetId": f"{args.id}-cache",
        },
    )
    save_session(args.session, session)
    print(args.id)
    return 0


def command_mask_refine(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    if getattr(args, "space", None) and args.space not in space_ids(session):
        raise SessionError(f"coordinate space not found: {args.space}")
    for mask in session.get("masks", []):
        if mask.get("id") == args.mask_id:
            refinements = mask.setdefault("refinements", [])
            data = vars(args).copy()
            data.pop("func", None)
            data.pop("session", None)
            data.pop("mask_id", None)
            data["type"] = args.refine_type
            refinements.append(data)
            validate_session(session)
            save_session(args.session, session)
            print(args.mask_id)
            return 0
    raise SessionError(f"mask not found: {args.mask_id}")


def command_mask_delete(args: argparse.Namespace) -> int:
    session = load_session(args.session)
    remove_mask(session, args.mask_id, force=args.force)
    save_session(args.session, session)
    print(args.mask_id)
    return 0


def command_relink(args: argparse.Namespace) -> int:
    try:
        result = relink(args.dir, args.sessions)
    except RelinkError as exc:
        if args.json:
            print(str(exc))
        else:
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    if args.json:
        print_json(result)
    else:
        print(f"relinked {len(result['results'])} session(s)")
    return 0


def command_export_xmp(args: argparse.Namespace) -> int:
    path = export_xmp(args.session, args.out)
    print(path)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="photoedit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Show image metadata.")
    inspect_parser.add_argument("image", type=Path)
    inspect_parser.set_defaults(func=command_inspect)

    preview_parser = subparsers.add_parser("preview", help="Render a truecolor ANSI terminal preview.")
    preview_parser.add_argument("image", type=Path)
    preview_parser.add_argument("--width", type=int, default=80)
    preview_parser.add_argument("--height", type=int)
    preview_parser.set_defaults(func=command_preview)

    recipe_parser = subparsers.add_parser("recipe", help="Save an edit recipe JSON file.")
    recipe_parser.add_argument("output", type=Path)
    add_adjustment_args(recipe_parser)
    recipe_parser.set_defaults(func=command_recipe)

    render_parser = subparsers.add_parser("render", help="Apply edits to one image.")
    render_parser.add_argument("input", type=Path)
    render_parser.add_argument("output", type=Path)
    render_parser.add_argument("--quality", type=int, default=95)
    add_adjustment_args(render_parser)
    render_parser.set_defaults(func=command_render)

    spot_parser = subparsers.add_parser("spot", help="Blend a small median-filtered cleanup patch.")
    spot_parser.add_argument("input", type=Path)
    spot_parser.add_argument("output", type=Path)
    spot_parser.add_argument("--x", type=int, required=True)
    spot_parser.add_argument("--y", type=int, required=True)
    spot_parser.add_argument("--radius", type=int, required=True)
    spot_parser.add_argument("--strength", type=float, default=0.8)
    spot_parser.add_argument("--quality", type=int, default=95)
    spot_parser.set_defaults(func=command_spot)

    session_new = subparsers.add_parser("session-new", help="Create a canonical JSON edit sidecar.")
    session_new.add_argument("image", type=Path)
    session_new.add_argument("--out", type=Path)
    session_new.add_argument("--json", action="store_true")
    session_new.set_defaults(func=command_session_new)

    session_info = subparsers.add_parser("session-info", help="Show JSON edit sidecar summary.")
    session_info.add_argument("session", type=Path)
    session_info.add_argument("--json", action="store_true")
    session_info.add_argument("--strict", action="store_true")
    session_info.set_defaults(func=command_session_info)

    validate = subparsers.add_parser("validate", help="Validate a JSON edit sidecar.")
    validate.add_argument("session", type=Path)
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=command_validate)

    migrate = subparsers.add_parser("migrate", help="Validate and rewrite a session at a target schema version.")
    migrate.add_argument("session", type=Path)
    migrate.add_argument("--to", type=int, default=1)
    migrate.add_argument("--out", type=Path)
    migrate.add_argument("--json", action="store_true")
    migrate.set_defaults(func=command_migrate)

    op_add = subparsers.add_parser("op-add", help="Add an ordered session operation.")
    op_add.add_argument("session", type=Path)
    op_add.add_argument("type")
    op_add.add_argument("--id")
    op_add.add_argument("--param", action="append", help="Operation parameter as key=value.")
    op_add.add_argument("--mask")
    op_add.add_argument("--space")
    op_add.add_argument("--before")
    op_add.add_argument("--after")
    op_add.add_argument("--first", action="store_true")
    op_add.add_argument("--last", action="store_true")
    op_add.set_defaults(func=command_op_add)

    op_set = subparsers.add_parser("op-set", help="Update an existing session operation.")
    op_set.add_argument("session", type=Path)
    op_set.add_argument("op_id")
    op_set.add_argument("--param", action="append", help="Operation parameter as key=value.")
    op_set.add_argument("--enabled", choices=["true", "false"])
    op_set.add_argument("--mask")
    op_set.add_argument("--global", dest="mask", action="store_const", const="__global__")
    op_set.add_argument("--space")
    op_set.set_defaults(func=command_op_set)

    op_move = subparsers.add_parser("op-move", help="Move an operation by op-id.")
    op_move.add_argument("session", type=Path)
    op_move.add_argument("op_id")
    op_move.add_argument("--before")
    op_move.add_argument("--after")
    op_move.add_argument("--first", action="store_true")
    op_move.add_argument("--last", action="store_true")
    op_move.set_defaults(func=command_op_move)

    op_delete = subparsers.add_parser("op-delete", help="Delete an operation by op-id.")
    op_delete.add_argument("session", type=Path)
    op_delete.add_argument("op_id")
    op_delete.set_defaults(func=command_op_delete)

    wb_kelvin = subparsers.add_parser("wb-kelvin", help="Add a Kelvin white balance operation.")
    wb_kelvin.add_argument("session", type=Path)
    wb_kelvin.add_argument("--id")
    wb_kelvin.add_argument("--kelvin", type=float, required=True)
    wb_kelvin.add_argument("--tint", type=float, default=0.0)
    wb_kelvin.add_argument("--mask")
    wb_kelvin.add_argument("--before")
    wb_kelvin.add_argument("--after")
    wb_kelvin.add_argument("--first", action="store_true")
    wb_kelvin.add_argument("--last", action="store_true")
    wb_kelvin.set_defaults(func=command_wb_kelvin)

    levels = subparsers.add_parser("levels", help="Add a levels operation.")
    levels.add_argument("session", type=Path)
    levels.add_argument("--id")
    levels.add_argument("--black", type=float, required=True)
    levels.add_argument("--midpoint", type=float, required=True)
    levels.add_argument("--white", type=float, required=True)
    levels.add_argument("--mask")
    levels.add_argument("--before")
    levels.add_argument("--after")
    levels.add_argument("--first", action="store_true")
    levels.add_argument("--last", action="store_true")
    levels.set_defaults(func=command_levels)

    point_curve = subparsers.add_parser("point-curve", help="Add a point curve operation.")
    point_curve.add_argument("session", type=Path)
    point_curve.add_argument("--id")
    point_curve.add_argument("--point", action="append", required=True, help="Curve point as x,y. Repeat for multiple points.")
    point_curve.add_argument("--mask")
    point_curve.add_argument("--before")
    point_curve.add_argument("--after")
    point_curve.add_argument("--first", action="store_true")
    point_curve.add_argument("--last", action="store_true")
    point_curve.set_defaults(func=command_point_curve)

    crop_preset = subparsers.add_parser("crop-preset", help="Add a crop preset operation.")
    crop_preset.add_argument("session", type=Path)
    crop_preset.add_argument("--id")
    crop_preset.add_argument("--space", required=True)
    crop_preset.add_argument("--preset", required=True, choices=["1:1", "3:2", "4:3", "4:5", "5:4", "16:9", "9:16"])
    crop_preset.add_argument("--anchor", default="center", choices=["center", "top", "bottom", "left", "right"])
    crop_preset.add_argument("--before")
    crop_preset.add_argument("--after")
    crop_preset.add_argument("--first", action="store_true")
    crop_preset.add_argument("--last", action="store_true")
    crop_preset.set_defaults(func=command_crop_preset)

    space_add = subparsers.add_parser("space-add", help="Add or replace a coordinate space.")
    space_add.add_argument("session", type=Path)
    space_add.add_argument("--id", required=True)
    space_add.add_argument("--source-width", type=int, required=True)
    space_add.add_argument("--source-height", type=int, required=True)
    space_add.add_argument("--crop", help="left,top,right,bottom")
    space_add.set_defaults(func=command_space_add)

    mask_radial = subparsers.add_parser("mask-radial", help="Add or replace a radial mask.")
    mask_radial.add_argument("session", type=Path)
    mask_radial.add_argument("--id", required=True)
    mask_radial.add_argument("--space", required=True)
    mask_radial.add_argument("--cx", type=int, required=True)
    mask_radial.add_argument("--cy", type=int, required=True)
    mask_radial.add_argument("--rx", type=int, required=True)
    mask_radial.add_argument("--ry", type=int, required=True)
    mask_radial.add_argument("--angle", type=float, default=0.0)
    mask_radial.add_argument("--feather", type=float, default=65.0)
    mask_radial.add_argument("--density", type=float, default=100.0)
    mask_radial.add_argument("--invert", action="store_true")
    mask_radial.set_defaults(func=command_mask_radial)

    mask_gradient = subparsers.add_parser("mask-gradient", help="Add or replace a linear gradient mask.")
    mask_gradient.add_argument("session", type=Path)
    mask_gradient.add_argument("--id", required=True)
    mask_gradient.add_argument("--space", required=True)
    mask_gradient.add_argument("--x1", type=int, required=True)
    mask_gradient.add_argument("--y1", type=int, required=True)
    mask_gradient.add_argument("--x2", type=int, required=True)
    mask_gradient.add_argument("--y2", type=int, required=True)
    mask_gradient.add_argument("--feather", type=float, default=100.0)
    mask_gradient.add_argument("--density", type=float, default=100.0)
    mask_gradient.add_argument("--invert", action="store_true")
    mask_gradient.set_defaults(func=command_mask_gradient)

    mask_painted = subparsers.add_parser("mask-painted-add", help="Copy a hand-painted bitmap mask asset into the session assets directory.")
    mask_painted.add_argument("session", type=Path)
    mask_painted.add_argument("--id", required=True)
    mask_painted.add_argument("--space", required=True)
    mask_painted.add_argument("--png", type=Path, required=True)
    mask_painted.set_defaults(func=command_mask_painted_add)

    mask_subject = subparsers.add_parser("mask-subject", help="Register a cached subject mask with pinned model identity.")
    mask_subject.add_argument("session", type=Path)
    mask_subject.add_argument("--id", required=True)
    mask_subject.add_argument("--space", required=True)
    mask_subject.add_argument("--model-id", required=True)
    mask_subject.add_argument("--model-version", required=True)
    mask_subject.add_argument("--weights-hash", required=True)
    mask_subject.add_argument("--cache-png", type=Path)
    mask_subject.set_defaults(func=command_mask_subject)

    mask_luma = subparsers.add_parser("mask-refine-luma", help="Append a luminance range refinement to a mask.")
    mask_luma.add_argument("session", type=Path)
    mask_luma.add_argument("mask_id")
    mask_luma.add_argument("--low", type=int, required=True)
    mask_luma.add_argument("--high", type=int, required=True)
    mask_luma.add_argument("--feather", type=int, default=20)
    mask_luma.add_argument("--invert", action="store_true")
    mask_luma.set_defaults(func=command_mask_refine, refine_type="luminance-range")

    mask_color = subparsers.add_parser("mask-refine-color", help="Append a color range refinement to a mask.")
    mask_color.add_argument("session", type=Path)
    mask_color.add_argument("mask_id")
    mask_color.add_argument("--space", required=True)
    mask_color.add_argument("--x", type=int, required=True)
    mask_color.add_argument("--y", type=int, required=True)
    mask_color.add_argument("--tolerance", type=int, default=45)
    mask_color.add_argument("--feather", type=int, default=35)
    mask_color.add_argument("--invert", action="store_true")
    mask_color.set_defaults(func=command_mask_refine, refine_type="color-range")

    mask_bounds = subparsers.add_parser("mask-bounds", help="Append an expand/contract refinement to a mask.")
    mask_bounds.add_argument("session", type=Path)
    mask_bounds.add_argument("mask_id")
    mask_bounds.add_argument("--pixels", type=int, required=True)
    mask_bounds.set_defaults(func=command_mask_refine, refine_type="bounds")

    mask_delete = subparsers.add_parser("mask-delete", help="Delete a mask by id.")
    mask_delete.add_argument("session", type=Path)
    mask_delete.add_argument("mask_id")
    mask_delete.add_argument("--force", action="store_true")
    mask_delete.set_defaults(func=command_mask_delete)

    relink_parser = subparsers.add_parser("relink", help="Repair broken lastKnownPath hints by hash matching.")
    relink_parser.add_argument("dir", type=Path)
    relink_parser.add_argument("--sessions", default="*.edit.json")
    relink_parser.add_argument("--json", action="store_true")
    relink_parser.set_defaults(func=command_relink)

    export_xmp_parser = subparsers.add_parser("export-xmp", help="One-way rough XMP export from JSON session.")
    export_xmp_parser.add_argument("session", type=Path)
    export_xmp_parser.add_argument("--out", type=Path)
    export_xmp_parser.set_defaults(func=command_export_xmp)

    return parser


def main(argv: list[str] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except SessionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
