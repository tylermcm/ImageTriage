"""AI Denoise: the sensor-sample arithmetic, the denoised raw file, the recipe rules and the editor's Detail controls."""
import json
import struct
import threading
import time

import numpy as np
import pytest

from image_triage import ai_denoise, photocraft_bridge as bridge, raw_denoise, raw_presets
from image_triage.full_render import _without_host_settings


def identity_model(packed):
    """A stand-in network: camera RGB at full resolution from the packed planes, at an arbitrary gain like the real one."""
    p = packed[0]
    up = lambda c: np.repeat(np.repeat(c, 2, 0), 2, 1)  # noqa: E731
    return (np.stack([up(p[0]), up((p[1] + p[2]) / 2), up(p[3])]) * 3000.0)[None]


def test_alignment_starts_on_red_with_even_sides():
    assert raw_denoise.aligned((10, 20, 101, 99), "RGGB") == (10, 20, 100, 98, 0, 0)
    assert raw_denoise.aligned((10, 20, 101, 99), "GBRG") == (10, 21, 100, 98, 0, 1)
    assert raw_denoise.aligned((10, 20, 101, 99), "BGGR") == (11, 21, 100, 98, 1, 1)
    with pytest.raises(ValueError):
        raw_denoise.aligned((0, 0, 100, 100), "XTRANS")


def test_a_flat_plane_comes_back_unchanged_through_tiles_and_gain():
    plane = np.full((300, 520), 0.2, np.float32)
    region, out = raw_denoise.denoise_plane(plane, (4, 6, 520, 300), "RGGB", [2.0, 1.0, 1.5], identity_model)
    assert region == (4, 6, 520, 300)
    assert out.shape == (300, 520)
    assert np.allclose(out, 0.2, atol=1e-4)


def test_tiles_join_without_seams():
    rng = np.random.default_rng(1)
    packed = rng.random((4, 300, 420)).astype(np.float32)
    whole = identity_model(packed[None])[0]
    tiled = raw_denoise.run_tiles(packed, identity_model, tile=128, overlap=16)
    assert np.allclose(tiled, whole, atol=1e-3)


def test_a_bias_that_repeats_every_2x2_pixels_does_not_split_the_greens():
    """The real network's output differs per 2x2 phase; read back at the photosites, G1 and G2 came apart (a maze after demosaic)."""
    rng = np.random.default_rng(3)
    plane = (0.2 + 0.02 * rng.standard_normal((256, 384))).astype(np.float32)        # flat grey with noise
    bias = np.array([[1.0, 1.08], [0.9, 1.0]], np.float32)

    def biased_smoothing_model(packed):
        flat = np.full_like(identity_model(packed), float(identity_model(packed).mean()))   # all noise removed
        return flat * np.tile(bias, (flat.shape[2] // 2, flat.shape[3] // 2))[None, None]

    _, out = raw_denoise.denoise_plane(plane, (0, 0, 384, 256), "RGGB", [1.0, 1.0, 1.0], biased_smoothing_model)
    g1, g2 = out[0::2, 1::2].mean(), out[1::2, 0::2].mean()
    assert abs(g1 - g2) / g2 < 0.01                                             # 18% apart without the per-site levels
    assert out.std() < plane.std() / 2                                         # and the noise is still gone


def test_highlights_near_white_keep_their_own_value():
    plane = np.full((64, 64), 0.3, np.float32)
    plane[10, 10] = 1.02
    _, out = raw_denoise.denoise_plane(plane, (0, 0, 64, 64), "RGGB", [1.0, 1.0, 1.0], lambda p: identity_model(p) * 0 + 0.5)
    assert out[10, 10] == pytest.approx(1.02)


def test_the_trailer_matches_photocrafts_layout(tmp_path):
    samples = np.full((2, 4), 0.5, np.float32)
    tail = raw_denoise.trailer(samples, (1, 2, 4, 2), (10, 8), 75)
    assert len(tail) == 8 * 2 + raw_denoise.TRAILER_LEN and tail.endswith(b"PCDENOI1")
    version, amount, *fields, payload = struct.unpack("<If6IQ", tail[16:16 + 40])
    assert (version, amount, fields, payload) == (1, 75.0, [1, 2, 4, 2, 10, 8], 16)
    raw = tmp_path / "a.ARW"
    raw.write_bytes(b"original")
    out = raw_denoise.write_denoised(raw, tmp_path / "out" / "a.denoised.ARW", samples, (1, 2, 4, 2), (10, 8), 75)
    assert out.read_bytes().startswith(b"original") and raw_denoise.has_trailer(out)
    raw_denoise.set_amount(out, 20)
    assert raw_denoise.read_amount(out) == 20
    assert not raw_denoise.has_trailer(raw)


def fake_input(photo, out):
    np.full((40, 64), 0.25, np.float32).tofile(out)
    return {"region": [0, 0, 64, 40], "sensor": [64, 40], "pattern": "RGGB", "multipliers": [2.0, 1.0, 1.5]}


def fake_engine(source, out, meta, progress):
    plane = np.fromfile(source, dtype="<f4").reshape(40, 64)
    region, samples = raw_denoise.denoise_plane(plane, meta["region"], meta["pattern"], meta["multipliers"], identity_model)
    np.asarray(samples, "<f2").tofile(out)
    return {"region": list(region)}


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "shoot" / "DSC0001.ARW"
    path.parent.mkdir()
    path.write_bytes(b"pretend raw " * 100)
    return str(path)


def test_make_writes_the_denoised_raw_once_and_follows_the_amount(photo):
    target = ai_denoise.make(photo, 40, sensor_input=fake_input, engine=fake_engine)
    assert target == ai_denoise.denoised_path(photo) and ai_denoise.ready(photo)
    assert target.read_bytes().startswith(open(photo, "rb").read())
    assert raw_denoise.read_amount(target) == 40
    calls = []
    again = ai_denoise.make(photo, 40, sensor_input=lambda *a: calls.append(a), engine=fake_engine)
    assert again == target and calls == []                               # kept, not made again
    on = {ai_denoise.KEY: {"enabled": True, "amount": 70}}
    assert ai_denoise.render_source(photo, on) == str(target)
    assert raw_denoise.read_amount(target) == 70 and ai_denoise.ready(photo)  # the amount is written in place
    assert ai_denoise.render_source(photo, {ai_denoise.KEY: {"enabled": False, "amount": 70}}) == photo
    assert ai_denoise.render_source(photo, {}) == photo


def test_a_changed_photo_is_denoised_again(photo):
    ai_denoise.make(photo, 50, sensor_input=fake_input, engine=fake_engine)
    time.sleep(0.01)
    with open(photo, "ab") as f:
        f.write(b"edited")
    assert not ai_denoise.ready(photo)
    assert ai_denoise.render_source(photo, {ai_denoise.KEY: {"enabled": True, "amount": 50}}) == photo
    ai_denoise.forget(photo)
    assert not ai_denoise.denoised_path(photo).exists()


def test_cancel_stops_before_writing(photo):
    cancel = threading.Event()

    def engine(*args):
        cancel.set()
        return fake_engine(*args)

    with pytest.raises(InterruptedError):
        ai_denoise.make(photo, 50, sensor_input=fake_input, engine=engine, cancel=cancel)
    assert not ai_denoise.denoised_path(photo).exists()


def test_recipe_rules():
    on = {"aiDenoise": {"enabled": True, "amount": 30}}
    assert ai_denoise.valid(on["aiDenoise"]) and ai_denoise.valid({})
    for bad in ({"enabled": 1}, {"amount": 101}, {"amount": float("nan")}, {"other": True}, "on"):
        assert not ai_denoise.valid(bad), bad
    recipe = {"version": bridge.RECIPE_VERSION, "cameraRaw": on}
    assert bridge.classify_recipe(recipe)[0] == bridge.RECIPE_EDITED
    off = {"version": bridge.RECIPE_VERSION, "cameraRaw": {"aiDenoise": {"enabled": False, "amount": 30}}}
    assert bridge.classify_recipe(off)[0] == bridge.RECIPE_IDENTITY
    assert bridge.recipe_problems({"aiDenoise": {"amount": 500}})[0][0] == "aiDenoise"
    assert _without_host_settings(recipe)["cameraRaw"] == {}               # PhotoCraft never sees it
    assert raw_presets.transferable({**on, "exposure": 1}) == {"exposure": 1}


def test_the_host_worker_writes_float16_samples_for_its_region(tmp_path):
    import importlib
    import sys

    sys.path.insert(0, str(__import__("pathlib").Path(raw_denoise.__file__).parent))
    worker = importlib.import_module("denoise_worker")

    class Engine:
        np = np
        device = "cpu"

        def load_model(self, _dir):
            return "cpu"

        def infer(self, packed):
            return identity_model(packed)

    source, out = tmp_path / "in.f32", tmp_path / "out.f16"
    np.full((40, 66), 0.1, np.float32).tofile(source)
    result = worker.generate_denoise(model_dir=tmp_path, input_path=source, output_path=out, region=[2, 0, 66, 40],
                                     pattern="GRBG", multipliers=[2, 1, 1.5], requested_device="cpu", engine=Engine(),
                                     emit_result=False)
    assert result["region"] == [3, 0, 64, 40]
    samples = np.fromfile(out, dtype="<f2")
    assert samples.size == 64 * 40 and np.allclose(samples, 0.1, atol=1e-3)
