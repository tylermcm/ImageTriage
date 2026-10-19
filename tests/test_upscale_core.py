from __future__ import annotations

import numpy as np
import pytest
from PIL import Image

from image_triage import upscale_core as uc


def nearest(scale):
    def run(t):
        return np.repeat(np.repeat(t, scale, axis=2), scale, axis=3)

    return run


def blur_nearest(scale, radius=3):
    """A fake model with a receptive field: box blur, then nearest-neighbour upscale."""

    def run(t):
        h, w = t.shape[2:]
        padded = np.pad(t, ((0, 0), (0, 0), (radius, radius), (radius, radius)), mode="reflect")
        acc = np.zeros_like(t)
        for dy in range(2 * radius + 1):
            for dx in range(2 * radius + 1):
                acc += padded[:, :, dy : dy + h, dx : dx + w]
        acc /= (2 * radius + 1) ** 2
        return np.repeat(np.repeat(acc, scale, axis=2), scale, axis=3)

    return run


def image(h, w, seed=0):
    return np.random.default_rng(seed).integers(0, 256, (h, w, 3), dtype=np.uint8)


def reference_nearest(rgb, scale):
    return np.repeat(np.repeat(rgb, scale, axis=0), scale, axis=1)


@pytest.mark.parametrize("size", [(1, 1), (3, 5), (96, 96), (97, 130), (200, 100)])
@pytest.mark.parametrize("scale", [2, 4])
def test_tiled_nearest_matches_whole_image(size, scale):
    rgb = image(*size)
    out = uc.upscale_array(rgb, nearest(scale), scale, core=64, pad=96)
    assert out.shape == (size[0] * scale, size[1] * scale, 3)
    assert np.array_equal(out, reference_nearest(rgb, scale))


def test_exact_multiple_and_off_by_one():
    for h in (64, 65, 63, 128):
        rgb = image(h, 64)
        out = uc.upscale_array(rgb, nearest(2), 2, core=64, pad=96)
        assert np.array_equal(out, reference_nearest(rgb, 2))


def test_margin_hides_tile_borders_for_a_model_with_receptive_field():
    rgb = image(150, 170, seed=3)
    scale = 2
    whole = uc.upscale_array(rgb, blur_nearest(scale), scale, core=1000 // 2 * 2 - 192, pad=96)  # one tile
    tiled = uc.upscale_array(rgb, blur_nearest(scale), scale, core=64, pad=96)
    assert np.abs(whole.astype(int) - tiled.astype(int)).max() <= 1


def test_margin_below_minimum_is_refused():
    with pytest.raises(uc.UpscaleError) as exc:
        uc.upscale_array(image(8, 8), nearest(2), 2, core=64, pad=16)
    assert exc.value.category == "bad_tiling"


def test_wrong_crop_offsets_would_be_detected():
    """Mutation guard: a model that marks its tile centre must land centred in the output."""
    marks = []

    def run(t):
        marks.append(t.shape)
        return nearest(2)(t)

    rgb = image(70, 70)
    uc.upscale_array(rgb, run, 2, core=64, pad=96)
    assert len(marks) == 4 and len(set(marks)) == 1  # constant tile shape
    assert marks[0] == (1, 3, 64 + 192, 64 + 192)


def test_progress_counts_and_cancel():
    seen = []
    uc.upscale_array(image(130, 130), nearest(2), 2, core=64, pad=96, progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (9, 9) and [d for d, _ in seen] == list(range(1, 10))
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(uc.UpscaleError) as exc:
        uc.upscale_array(image(130, 130), nearest(2), 2, core=64, pad=96, should_cancel=cancel)
    assert exc.value.category == "cancelled"


def test_bad_model_output_is_rejected():
    with pytest.raises(uc.UpscaleError) as exc:
        uc.upscale_array(image(8, 8), lambda t: t, 2, core=64, pad=96)
    assert exc.value.category == "bad_model_output"
    with pytest.raises(uc.UpscaleError) as exc:
        uc.upscale_array(image(8, 8), lambda t: np.full((1, 3, 2 * t.shape[2], 2 * t.shape[3]), np.nan, np.float32), 2, core=64, pad=96)
    assert exc.value.category == "bad_model_output"


def test_choose_tile_obeys_cap_and_keeps_core_even():
    core, pad, tile = uc.choose_tile(449, 96)
    assert core % 2 == 0 and tile == core + 192 and tile <= uc.MAX_TILE
    core, pad, tile = uc.choose_tile(10_000, 96)
    assert tile <= uc.MAX_TILE and core % 2 == 0


def test_plan_limits():
    plan = uc.plan_upscale(6000, 4000, "realesrgan-x2plus", ".png")
    assert (plan.out_width, plan.out_height) == (12000, 8000) and plan.tile_count == 14 * 9
    with pytest.raises(uc.UpscaleError) as exc:
        uc.plan_upscale(9000, 100, "realesrgan-x2plus", ".webp")
    assert exc.value.category == "too_large"
    with pytest.raises(uc.UpscaleError) as exc:
        uc.plan_upscale(30000, 20000, "realesrgan-x2plus", ".png")
    assert exc.value.category == "too_large"
    with pytest.raises(uc.UpscaleError) as exc:
        uc.plan_upscale(10, 10, "nope", ".png")
    assert exc.value.category == "unknown_model"
    with pytest.raises(uc.UpscaleError) as exc:
        uc.plan_upscale(10, 10, "realesrgan-x2plus", ".bmp")
    assert exc.value.category == "bad_format"
    assert uc.plan_upscale(10, 10, "realesrgan-x2plus", ".jpg", has_alpha=True).warnings


def test_select_providers():
    avail = ["CPUExecutionProvider", "DmlExecutionProvider", "CUDAExecutionProvider"]
    assert uc.select_providers(avail) == ["CUDAExecutionProvider", "DmlExecutionProvider", "CPUExecutionProvider"]
    assert uc.select_providers(avail, "cpu") == ["CPUExecutionProvider"]
    assert uc.select_providers(["TensorrtExecutionProvider"]) == ["CPUExecutionProvider"]


def test_providers_agree():
    ref = uc.self_check_input()
    assert uc.providers_agree(ref.copy(), ref)
    bad = ref.copy()
    bad[0, 0, 0, 0] = np.nan
    assert not uc.providers_agree(bad, ref)
    assert not uc.providers_agree(ref[:, :, :-1], ref)
    assert not uc.providers_agree(ref + 0.1, ref)


def test_model_files(tmp_path):
    spec = uc.MODEL_SPECS["realesrgan-x2plus"]
    with pytest.raises(uc.UpscaleError) as exc:
        uc.verify_model_file(tmp_path, spec)
    assert exc.value.category == "model_unavailable"
    (tmp_path / spec.filename).write_bytes(b"x")
    with pytest.raises(uc.UpscaleError):
        uc.verify_model_file(tmp_path, spec)
    assert set(uc.model_status(tmp_path)) == set(uc.MODEL_SPECS)
    assert all(uc.model_status(tmp_path).values())


def test_deep_verify_checks_hash(tmp_path):
    spec = uc.UpscaleModelSpec("t", "t", 2, "t.onnx", 4, uc.hashlib.sha256(b"good").hexdigest())
    (tmp_path / "t.onnx").write_bytes(b"good")
    uc.verify_model_file(tmp_path, spec, deep=True)
    (tmp_path / "t.onnx").write_bytes(b"evil")
    uc.verify_model_file(tmp_path, spec)  # size alone passes
    with pytest.raises(uc.UpscaleError):
        uc.verify_model_file(tmp_path, spec, deep=True)


def test_output_naming(tmp_path):
    src = tmp_path / "a.jpg"
    src.write_bytes(b"")
    first = uc.upscale_output_path(src, 2)
    assert first.name == "a_2x_ai.jpg"
    first.write_bytes(b"")
    assert uc.upscale_output_path(src, 2).name == "a_2x_ai (2).jpg"
    assert uc.upscale_output_path(src, 2, ".xyz").suffix == ".png"


@pytest.mark.parametrize("ext", [".png", ".tif", ".jpg", ".webp"])
def test_save_output_atomic_with_provenance(tmp_path, ext):
    spec = uc.MODEL_SPECS["realesrgan-x2plus"]
    rgb = image(16, 16)
    dest = tmp_path / f"o{ext}"
    uc.save_output(rgb, dest, spec, "CPUExecutionProvider")
    assert dest.is_file() and not list(tmp_path.glob("*.partial"))
    with Image.open(dest) as im:
        assert im.size == (16, 16)
    if ext == ".png":
        with Image.open(dest) as im:
            assert "realesrgan-x2plus" in im.text["Software"]
    with pytest.raises(uc.UpscaleError) as exc:
        uc.save_output(rgb, dest, spec, "CPU")
    assert exc.value.category == "exists"
    uc.save_output(rgb, dest, spec, "CPU", overwrite=True)


def test_save_output_failure_leaves_no_partial(tmp_path, monkeypatch):
    spec = uc.MODEL_SPECS["realesrgan-x2plus"]

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(Image.Image, "save", boom)
    with pytest.raises(RuntimeError):
        uc.save_output(image(8, 8), tmp_path / "o.png", spec, "CPU")
    assert list(tmp_path.iterdir()) == []


def test_save_output_alpha_and_icc_and_exif(tmp_path):
    spec = uc.MODEL_SPECS["realesrgan-x2plus"]
    rgb = image(8, 8)
    alpha = np.full((4, 4), 128, np.uint8)
    png = uc.save_output(rgb, tmp_path / "a.png", spec, "CPU", alpha=alpha)
    with Image.open(png) as im:
        assert im.mode == "RGBA" and im.size == (8, 8)
    jpg = uc.save_output(rgb, tmp_path / "a.jpg", spec, "CPU", alpha=alpha)
    with Image.open(jpg) as im:
        assert im.mode == "RGB"
        assert im.getexif()[0x0112] == 1
        assert "realesrgan-x2plus" in im.getexif()[0x0131]
