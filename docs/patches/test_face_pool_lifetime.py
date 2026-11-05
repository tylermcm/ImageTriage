"""Regression accompanying cli_culler_face_pool_lifetime.patch (CLI-Culler repo)."""
from pathlib import Path
from types import SimpleNamespace
import time

import pytest


def test_face_sessions_stay_bounded_across_batches(monkeypatch):
    pytest.importorskip("mediapipe")
    from mediapipe.tasks.python import vision
    from PIL import Image
    from aiculler.models.engines import FacesEngine
    from aiculler.scorers.models import FacesScorer

    sessions = []

    class Session:
        def __init__(self):
            self.closed = False
            sessions.append(self)

        def detect(self, image):
            assert not self.closed
            time.sleep(0.003)  # let the real executor use all eight threads
            return SimpleNamespace(face_landmarks=[], face_blendshapes=[])

        def close(self):
            assert not self.closed
            self.closed = True

    monkeypatch.setattr(vision.FaceLandmarker, "create_from_options", staticmethod(lambda options: Session()))
    engine = FacesEngine(Path("not-a-real-model-directory"), threads=8)

    class Registry:
        def available(self, key):
            return key != "adaface"

        def revision(self, key):
            return "test"

        def engine(self, key):
            return engine

    image = Image.new("RGB", (240, 240), (40, 100, 160))

    class Candidate:
        def model_image(self, edge):
            return image

    try:
        scorer = FacesScorer(Registry())
        for _ in range(6):
            assert scorer.score([Candidate() for _ in range(64)]) == [{"face_count": 0, "faces": []}] * 64
        # One constructor-thread session plus at most one per executor thread.
        assert len(sessions) <= 9
    finally:
        engine.close()
    assert all(session.closed for session in sessions)
