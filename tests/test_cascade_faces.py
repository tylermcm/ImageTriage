"""The face finder shared by the folder cascade and the People index: alignment geometry, preprocessing, the per-file cache and the worker protocol."""

from __future__ import annotations

import io
import json

import numpy as np
import pytest
from PIL import Image

from image_triage.cascade import faces
from image_triage.cascade.faces import FaceCache, FaceFinding, align_crop, five_points, preprocess, similarity_transform
from image_triage.cascade.rankers import protocol, worker
from image_triage.cascade.rankers.spec import FACES, SPECS


def test_the_similarity_transform_recovers_a_known_rotation_scale_and_shift():
    source = np.array([[10.0, 20.0], [40.0, 22.0], [25.0, 40.0], [14.0, 60.0], [38.0, 61.0]])
    angle, scale, shift = np.deg2rad(17.0), 1.8, np.array([5.0, -7.0])
    rotation = scale * np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    target = source @ rotation.T + shift
    matrix = similarity_transform(source, target)
    assert np.allclose(source @ matrix[:, :2].T + matrix[:, 2], target, atol=1e-6)


def test_degenerate_landmarks_are_refused():
    with pytest.raises(ValueError):
        similarity_transform(np.zeros((5, 2)), faces.TEMPLATE)


def _face_image(points: np.ndarray, size: tuple[int, int]) -> Image.Image:
    """A frame with a bright square at each of the five points, so alignment can be checked by where they land."""
    pixels = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    for x, y in points:
        pixels[int(y) - 2:int(y) + 3, int(x) - 2:int(x) + 3] = 255
    return Image.fromarray(pixels)


def test_alignment_puts_the_five_points_on_the_template_whatever_the_pose():
    # The same five points, scaled, shifted and tilted: after alignment the marks sit where the template says.
    angle, scale = np.deg2rad(-12.0), 2.5
    rotation = scale * np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    points = faces.TEMPLATE @ rotation.T + np.array([150.0, 90.0])
    crop = align_crop(_face_image(points, (400, 300)), points)
    assert crop.shape == (112, 112, 3)
    for x, y in faces.TEMPLATE:
        assert crop[int(round(y)) - 3:int(round(y)) + 4, int(round(x)) - 3:int(round(x)) + 4].max() > 200  # a mark (about 2 px after scaling) lands within a pixel or two
    assert crop.mean() < 20  # and nothing else is lit: the marks did not smear


def test_five_points_come_from_the_expected_landmarks():
    landmarks = [(0.0, 0.0)] * 478
    landmarks[33], landmarks[133] = (0.30, 0.40), (0.34, 0.40)
    landmarks[362], landmarks[263] = (0.60, 0.40), (0.66, 0.40)
    landmarks[1], landmarks[61], landmarks[291] = (0.48, 0.55), (0.38, 0.70), (0.62, 0.70)
    points = five_points(landmarks, 1000, 500)
    assert np.allclose(points[0], [320, 200]) and np.allclose(points[1], [630, 200])
    assert np.allclose(points[2], [480, 275]) and np.allclose(points[3], [380, 350]) and np.allclose(points[4], [620, 350])


def test_preprocessing_is_bgr_scaled_and_channels_first():
    rgb = np.zeros((112, 112, 3), dtype=np.uint8)
    rgb[:, :, 0] = 255  # pure red
    out = preprocess(rgb)
    assert out.shape == (1, 3, 112, 112) and out.dtype == np.float32
    assert out[0, 2].min() == pytest.approx(1.0) and out[0, 0].max() == pytest.approx(-1.0)  # red is the last channel in BGR order; blue and green are at -1


def test_a_finding_survives_json():
    finding = FaceFinding(box=(0.1, 0.2, 0.3, 0.4), area=0.04, embedding=(0.5, -0.25), eyes_closed=0.8)
    assert FaceFinding.from_json(json.loads(json.dumps(finding.to_json()))) == finding
    open_eyes = FaceFinding(box=(0.1, 0.2, 0.3, 0.4), area=0.04)
    assert FaceFinding.from_json(open_eyes.to_json()).eyes_closed is None


def test_the_cache_remembers_a_file_until_it_changes(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"one")
    store = tmp_path / "faces.json"
    cache = FaceCache(store)
    assert cache.get(str(photo)) is None
    finding = FaceFinding(box=(0.1, 0.1, 0.2, 0.2), area=0.01, embedding=(1.0, 0.0))
    cache.put(str(photo), [finding])
    cache.save()
    assert FaceCache(store).get(str(photo)) == [finding]
    photo.write_bytes(b"changed contents")
    assert FaceCache(store).get(str(photo)) is None  # a changed file is analysed again


def test_a_file_with_no_face_is_remembered_too(tmp_path):
    photo = tmp_path / "scenery.jpg"
    photo.write_bytes(b"x")
    cache = FaceCache(tmp_path / "faces.json")
    cache.put(str(photo), [])
    cache.save()
    assert FaceCache(tmp_path / "faces.json").get(str(photo)) == []


def test_a_different_recognizer_invalidates_the_cache(tmp_path):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(b"x")
    cache = FaceCache(tmp_path / "faces.json", "adaface:ir50")
    cache.put(str(photo), [])
    cache.save()
    assert FaceCache(tmp_path / "faces.json", "adaface:ir101").get(str(photo)) is None


def test_a_corrupt_cache_file_is_ignored(tmp_path):
    (tmp_path / "faces.json").write_text("{not json", encoding="utf-8")
    assert len(FaceCache(tmp_path / "faces.json")) == 0


def test_the_face_finder_needs_both_bundles_and_no_torch():
    assert SPECS["faces"] is FACES and not FACES.needs_torch and FACES.bundle_key == "faces_blink" and FACES.aux_bundle_key == "faces_identity"


def test_the_worker_reports_what_an_engine_found_beyond_the_count():
    class Engine:
        device, half = "cpu", False

        def detail(self, path):
            if path == "bad":
                raise OSError("unreadable")
            return {"faces": [{"box": [0.1, 0.1, 0.4, 0.4], "area": 0.09, "embedding": [1.0], "eyes_closed": None}], "width": 100, "height": 50}

    events = []
    request = {"op": "score", "id": 7, "items": [{"key": "a", "path": "a.jpg"}, {"key": "b", "path": "bad"}]}
    worker.serve(Engine(), io.StringIO(json.dumps(request) + "\n"), events.append)
    assert events[0]["event"] == "score" and events[0]["value"] == 1.0 and events[0]["detail"]["width"] == 100
    assert events[1]["event"] == "frame_error" and events[2]["event"] == "done"


def test_the_protocol_line_carries_the_detail():
    line = protocol.encode_event({"event": "score", "id": 1, "key": "a", "value": 2.0, "detail": {"faces": []}})
    assert protocol.decode_event(line)["detail"] == {"faces": []}
