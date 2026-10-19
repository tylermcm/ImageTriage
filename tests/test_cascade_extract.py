from __future__ import annotations

import io
import os
import threading

import numpy as np
from PIL import Image

from image_triage.cascade import FrameFeatures
from image_triage.cascade import extract as ex
from image_triage.perceptual_hash import hamming_distance_int


def _noise(width: int = 640, height: int = 480, seed: int = 1, scale: float = 1.0) -> Image.Image:
    rng = np.random.default_rng(seed)
    base = rng.integers(60, 190, size=(height // 8, width // 8, 3), dtype=np.uint8)
    img = Image.fromarray(base).resize((width, height), Image.Resampling.BICUBIC)
    if scale != 1.0:
        arr = np.asarray(img).astype(np.float32)
        img = Image.fromarray(np.clip((arr - 128) * scale + 128, 0, 255).astype(np.uint8))
    return img


def _asymmetric(width: int = 640, height: int = 480) -> Image.Image:
    arr = np.zeros((height, width, 3), dtype=np.uint8) + 100
    arr[: height // 2, : width // 3] = (230, 60, 40)  # a bright block in the top-left
    arr[height // 2 :, width // 2 :] = (20, 40, 200)
    return Image.fromarray(arr)


def _save(img: Image.Image, path, **kwargs) -> str:
    img.save(path, "JPEG", quality=95, **kwargs)
    return str(path)


def _one(path: str) -> FrameFeatures:
    result = ex.extract_features([path], max_workers=1)
    assert result.features[0] is not None
    return result.features[0]


def test_returns_frame_features_in_order(tmp_path):
    paths = [_save(_noise(seed=i), tmp_path / f"{i}.jpg") for i in range(4)]
    result = ex.extract_features(paths)
    assert [f.key for f in result.features] == paths
    assert all(isinstance(f, FrameFeatures) for f in result.features)
    assert result.stats.computed == 4 and result.stats.cached == 0 and not result.stats.cancelled


def test_sharpness_ranks_sharp_over_blurred(tmp_path):
    sharp = _noise(1000, 700)
    sharp_arr = np.asarray(sharp).copy()
    sharp_arr[::2, ::2] = 255 - sharp_arr[::2, ::2]  # strong pixel-level detail
    sharp = Image.fromarray(sharp_arr)
    blurred = sharp.resize((100, 70), Image.Resampling.BOX).resize((1000, 700), Image.Resampling.BICUBIC)
    a = _one(_save(sharp, tmp_path / "sharp.png".replace("png", "jpg")))
    b = _one(_save(blurred, tmp_path / "blur.jpg"))
    assert a.centre_sharpness > 20 * b.centre_sharpness


def test_sharpness_uses_only_the_centre(tmp_path):
    arr = np.full((400, 600, 3), 128, dtype=np.uint8)
    arr[:, :100] = np.random.default_rng(0).integers(0, 255, size=(400, 100, 3), dtype=np.uint8)  # texture only in the outer left strip
    edge_only = _one(_save(Image.fromarray(arr), tmp_path / "edge.jpg"))
    assert edge_only.centre_sharpness < 1.0


def test_clipped_and_extremes_fractions(tmp_path):
    arr = np.full((400, 400, 3), 128, dtype=np.uint8)
    arr[:100] = (255, 128, 128)  # top quarter: one channel clipped, luma not extreme
    arr[100:200] = (255, 255, 255)  # second quarter: clipped and luma extreme
    arr[200:240] = (0, 0, 0)  # ten percent black: luma extreme only
    path = str(tmp_path / "c.png")
    Image.fromarray(arr).save(path)
    f = _one(path)
    assert abs(f.clipped - 0.5) < 0.01
    assert abs(f.extremes - (0.25 + 0.10)) < 0.01


def test_flat_mid_grey_has_no_clipping(tmp_path):
    path = str(tmp_path / "g.png")
    Image.new("RGB", (300, 200), (120, 120, 120)).save(path)
    f = _one(path)
    assert f.clipped == 0.0 and f.extremes == 0.0 and f.centre_sharpness == 0.0


def test_exif_orientation_is_applied_to_plain_jpegs(tmp_path):
    base = _asymmetric()
    exif = Image.Exif()
    exif[0x0112] = 6  # stored sideways; display needs a 90 degree turn
    tagged = _one(_save(base, tmp_path / "t.jpg", exif=exif))
    upright = _one(_save(base.transpose(Image.Transpose.ROTATE_270), tmp_path / "u.jpg"))
    untagged = _one(_save(base, tmp_path / "n.jpg"))
    assert hamming_distance_int(tagged.phash, upright.phash) <= 2
    assert hamming_distance_int(tagged.phash, untagged.phash) > 12


def test_raw_uses_embedded_jpeg_and_container_orientation(tmp_path, monkeypatch):
    base = _asymmetric()
    buffer = io.BytesIO()
    base.save(buffer, "JPEG", quality=95)
    raw_path = tmp_path / "frame.nef"
    raw_path.write_bytes(b"\x00" * 64 + buffer.getvalue() + b"\x00" * 64)

    plain = _one(str(raw_path))  # marker-scan fallback, orientation 1
    upright = _one(_save(base, tmp_path / "ref.jpg"))
    assert plain.phash == upright.phash

    real = ex.extract_embedded_jpeg(str(raw_path))
    assert real is not None
    monkeypatch.setattr(
        ex, "extract_embedded_jpeg", lambda p: type(real)(payload=real.payload, source="t", offset=0, byte_count=real.byte_count, orientation=6)
    )
    rotated = _one(str(raw_path))
    expected = _one(_save(base.transpose(Image.Transpose.ROTATE_270), tmp_path / "exp.jpg"))
    assert hamming_distance_int(rotated.phash, expected.phash) <= 2


def test_raw_never_falls_back_to_a_decoder(tmp_path):
    path = tmp_path / "broken.nef"
    path.write_bytes(os.urandom(4096))
    result = ex.extract_features([str(path)])
    assert result.features == [None] and result.stats.failed == 1


def test_phash_matches_perceptual_hash_layout(tmp_path):
    from image_triage.perceptual_hash import compute_phash

    paths = [_save(_noise(800, 600, seed=s), tmp_path / f"{s}.jpg") for s in range(3)]
    for path in paths:
        ours = _one(path).phash
        theirs = compute_phash(path)
        assert theirs is not None
        assert ours < 1 << 63 and theirs < 1 << 63  # DC bit is zero in both, so the integers line up bit for bit
        assert hamming_distance_int(ours, theirs) <= 6


def test_phash_same_scene_close_different_scene_far(tmp_path):
    a = _one(_save(_asymmetric(), tmp_path / "a.jpg"))
    a2 = _one(_save(_asymmetric(), tmp_path / "a2.jpg", ))
    shifted = _one(_save(_asymmetric(660, 500), tmp_path / "a3.jpg"))
    other = _one(_save(_noise(seed=9), tmp_path / "o.jpg"))
    assert a.phash == a2.phash
    assert hamming_distance_int(a.phash, shifted.phash) <= 8
    assert hamming_distance_int(a.phash, other.phash) > 18


def test_large_images_are_reduced(tmp_path):
    path = _save(_noise(3000, 2000), tmp_path / "big.jpg")
    img = ex._load_preview(path, None)
    assert img is not None and max(img.size) <= 2 * ex.PREVIEW_LONG_EDGE  # draft decode at 1/2 scale
    assert max(ex._reduce(img).size) == ex.PREVIEW_LONG_EDGE


def test_cache_hit_skips_decode_and_follows_signature(tmp_path, monkeypatch):
    path = _save(_noise(), tmp_path / "x.jpg")
    cache = ex.FeatureCache(tmp_path / "cache.json")
    first = ex.extract_features([path], cache=cache)
    assert first.stats.computed == 1

    calls = []
    real = ex._measure_path
    monkeypatch.setattr(ex, "_measure_path", lambda *a, **k: calls.append(a) or real(*a, **k))
    again = ex.extract_features([path], cache=ex.FeatureCache(tmp_path / "cache.json"))  # reloaded from disk
    assert calls == [] and again.stats.cached == 1 and again.features == first.features

    _save(_noise(seed=5), tmp_path / "x.jpg")  # changed file, new signature
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000_000))
    third = ex.extract_features([path], cache=ex.FeatureCache(tmp_path / "cache.json"))
    assert len(calls) == 1 and third.stats.computed == 1 and third.features != first.features


def test_blink_comes_from_lookup_and_is_not_cached(tmp_path):
    path = _save(_noise(), tmp_path / "b.jpg")
    cache = ex.FeatureCache()
    assert ex.extract_features([path], cache=cache).features[0].blink is False
    assert ex.extract_features([path], cache=cache, blink_lookup=lambda p: True).features[0].blink is True
    assert ex.extract_features([path], cache=cache, blink_lookup=lambda p: None).features[0].blink is False

    def broken(_path):
        raise RuntimeError("face index offline")

    assert ex.extract_features([path], cache=cache, blink_lookup=broken).features[0].blink is False


def test_unreadable_and_missing_files_give_none_in_place(tmp_path):
    good = _save(_noise(), tmp_path / "ok.jpg")
    bad = tmp_path / "bad.jpg"
    bad.write_bytes(b"not an image")
    result = ex.extract_features([good, str(bad), str(tmp_path / "missing.jpg")])
    assert result.features[0] is not None and result.features[1] is None and result.features[2] is None
    assert result.stats.failed == 2


def test_progress_reports_every_frame(tmp_path):
    paths = [_save(_noise(seed=i), tmp_path / f"{i}.jpg") for i in range(5)]
    seen: list[tuple[int, int]] = []
    ex.extract_features(paths, progress=lambda done, total: seen.append((done, total)))
    assert [d for d, _t in seen] == [1, 2, 3, 4, 5] and {t for _d, t in seen} == {5}


def test_cancel_stops_remaining_frames(tmp_path):
    paths = [_save(_noise(seed=i), tmp_path / f"{i}.jpg") for i in range(12)]
    stop = threading.Event()
    seen: list[int] = []

    def progress(done: int, _total: int) -> None:
        seen.append(done)
        if done >= 3:
            stop.set()

    result = ex.extract_features(paths, progress=progress, cancelled=stop.is_set, max_workers=1)
    assert result.stats.cancelled
    assert 3 <= sum(f is not None for f in result.features) < 12
    assert result.features[0] is not None and result.features[-1] is None


def test_cancel_before_start_does_no_work(tmp_path):
    paths = [_save(_noise(seed=i), tmp_path / f"{i}.jpg") for i in range(3)]
    result = ex.extract_features(paths, cancelled=lambda: True)
    assert result.features == [None, None, None] and result.stats.cancelled and result.stats.computed == 0
