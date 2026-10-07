"""WI-5.3 stage B: resize / convert / workflow-export must apply a real
built-in editor session's edits to the exported pixels, while leaving
unedited sources and corrupt/unreadable sidecars byte-for-byte on the
existing (pre-WI-5.3) code path."""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from image_triage.edit_storage import editor_session_path
from image_triage.image_convert import ConvertOptions, ConvertSourceItem, apply_convert_plan, build_convert_plan
from image_triage.image_resize import ResizeOptions, ResizeSourceItem, apply_resize_plan, build_resize_plan
from image_triage.photo_terminal.session import SCHEMA_NAME, SCHEMA_VERSION
from image_triage.workflows import WorkflowRecipe, apply_workflow_export_plan, build_workflow_export_plan
from tests.harness import make_jpegs

IMAGE_SIZE = (80, 60)


def _write_session(image_path: Path, *, operations: list[dict] | None = None, corrupt: bool = False) -> Path:
    session_path = editor_session_path(image_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    if corrupt:
        session_path.write_text("{not valid json")
        return session_path
    session_path.write_text(
        json.dumps(
            {
                "version": SCHEMA_VERSION,
                "schema": SCHEMA_NAME,
                "coordinateSpaces": [
                    {
                        "id": "space-source-full",
                        "sourceWidth": IMAGE_SIZE[0],
                        "sourceHeight": IMAGE_SIZE[1],
                        "cropInEffect": None,
                    }
                ],
                "assets": {"dir": "assets", "bitmapMasks": []},
                "operations": operations or [],
                "masks": [],
            }
        )
    )
    return session_path


def _exposure_op() -> list[dict]:
    return [{"id": "op-1", "type": "adjust.exposure", "enabled": True, "params": {"exposure": 1.8}}]


def _pixel(path: str) -> tuple:
    with Image.open(path) as img:
        return img.convert("RGB").getpixel((0, 0))


# ---- resize -----------------------------------------------------------


def test_resize_with_real_sidecar_renders_edits(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=IMAGE_SIZE)
    original_pixel = _pixel(src)
    _write_session(Path(src), operations=_exposure_op())

    options = ResizeOptions(preset_key="custom", custom_width=IMAGE_SIZE[0], custom_height=IMAGE_SIZE[1])
    plan = build_resize_plan([ResizeSourceItem(src, "a.jpg")], options)
    (written,) = apply_resize_plan(plan, options)

    assert _pixel(written) != original_pixel


def test_resize_without_sidecar_is_unaffected(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=(400, 200))
    options = ResizeOptions(preset_key="custom", custom_width=100, custom_height=100)
    plan = build_resize_plan([ResizeSourceItem(src, "a.jpg")], options)
    (written,) = apply_resize_plan(plan, options)

    assert written != src
    with Image.open(written) as out:
        assert max(out.size) <= 100


def test_resize_with_corrupt_sidecar_falls_back_to_original(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=IMAGE_SIZE)
    original_pixel = _pixel(src)
    _write_session(Path(src), corrupt=True)

    options = ResizeOptions(preset_key="custom", custom_width=IMAGE_SIZE[0], custom_height=IMAGE_SIZE[1])
    plan = build_resize_plan([ResizeSourceItem(src, "a.jpg")], options)
    (written,) = apply_resize_plan(plan, options)

    assert _pixel(written) == original_pixel


# ---- convert ------------------------------------------------------------


def test_convert_with_real_sidecar_renders_edits(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=IMAGE_SIZE)
    original_pixel = _pixel(src)
    _write_session(Path(src), operations=_exposure_op())

    options = ConvertOptions(output_suffix=".png")
    plan = build_convert_plan([ConvertSourceItem(src, "a.jpg")], options)
    (written,) = apply_convert_plan(plan, options)

    assert _pixel(written) != original_pixel


def test_convert_without_sidecar_is_unaffected(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"])
    options = ConvertOptions(output_suffix=".png")
    plan = build_convert_plan([ConvertSourceItem(src, "a.jpg")], options)
    (written,) = apply_convert_plan(plan, options)

    assert written.lower().endswith(".png")
    with Image.open(written) as out:
        assert out.format == "PNG"


def test_convert_with_corrupt_sidecar_falls_back_to_original(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=IMAGE_SIZE)
    original_pixel = _pixel(src)
    _write_session(Path(src), corrupt=True)

    options = ConvertOptions(output_suffix=".png")
    plan = build_convert_plan([ConvertSourceItem(src, "a.jpg")], options)
    (written,) = apply_convert_plan(plan, options)

    assert _pixel(written) == original_pixel


# ---- workflow export ------------------------------------------------------


def test_workflow_export_with_real_sidecar_renders_edits(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=IMAGE_SIZE)
    original_pixel = _pixel(src)
    _write_session(Path(src), operations=_exposure_op())
    destination_dir = tmp_path / "Delivery"

    plan = build_workflow_export_plan(
        [ResizeSourceItem(source_path=src, source_name="a.jpg")],
        WorkflowRecipe(key="k", name="K", destination_subfolder="Delivery"),
        destination_dir=str(destination_dir),
    )
    (written,) = apply_workflow_export_plan(plan)

    assert _pixel(written) != original_pixel


def test_workflow_export_without_sidecar_is_unaffected(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=IMAGE_SIZE)
    original_pixel = _pixel(src)
    destination_dir = tmp_path / "Delivery"

    plan = build_workflow_export_plan(
        [ResizeSourceItem(source_path=src, source_name="a.jpg")],
        WorkflowRecipe(key="k", name="K", destination_subfolder="Delivery"),
        destination_dir=str(destination_dir),
    )
    (written,) = apply_workflow_export_plan(plan)

    assert _pixel(written) == original_pixel


def test_workflow_export_with_corrupt_sidecar_falls_back_to_original(tmp_path) -> None:
    (src,) = make_jpegs(tmp_path, ["a.jpg"], size=IMAGE_SIZE)
    original_pixel = _pixel(src)
    _write_session(Path(src), corrupt=True)
    destination_dir = tmp_path / "Delivery"

    plan = build_workflow_export_plan(
        [ResizeSourceItem(source_path=src, source_name="a.jpg")],
        WorkflowRecipe(key="k", name="K", destination_subfolder="Delivery"),
        destination_dir=str(destination_dir),
    )
    (written,) = apply_workflow_export_plan(plan)

    assert _pixel(written) == original_pixel
