from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_triage.ai_model import download_ai_model, resolve_ai_model_installation


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        start = self._offset
        end = min(len(self._payload), start + size)
        self._offset = end
        return self._payload[start:end]


class AIModelTests(unittest.TestCase):
    def test_resolve_ai_model_installation_uses_explicit_env_dir(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"AICULLING_MODEL_DIR": str(Path(temp_dir) / "custom-model")}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_ai_model_installation()

        self.assertEqual(installation.install_dir.name, "custom-model")

    def test_ai_model_installation_requires_all_expected_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            install_dir = Path(temp_dir) / "model"
            install_dir.mkdir(parents=True)
            installation = resolve_ai_model_installation(install_dir=install_dir)

            self.assertFalse(installation.is_installed)
            self.assertEqual({path.name for path in installation.missing_files}, {"config.json", "model.safetensors"})

            (install_dir / "config.json").write_text("{}", encoding="utf-8")
            self.assertFalse(installation.is_installed)
            self.assertEqual({path.name for path in installation.missing_files}, {"model.safetensors"})

            (install_dir / "model.safetensors").write_bytes(b"weights")
            self.assertTrue(installation.is_installed)
            self.assertEqual(installation.missing_files, ())

    def test_download_ai_model_fetches_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = resolve_ai_model_installation(
                install_dir=Path(temp_dir) / "downloaded-model",
                repo_id="owner/repo",
                revision="main",
            )
            payloads = {
                "config.json": b'{"model_type":"dinov2"}',
                "model.safetensors": b"weights",
            }
            seen_progress: list[tuple[str, int, int]] = []

            def fake_urlopen(request):
                url = getattr(request, "full_url", str(request))
                filename = url.split("?", 1)[0].rsplit("/", 1)[-1]
                return _FakeResponse(payloads[filename])

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                download_ai_model(
                    installation,
                    progress_callback=lambda filename, current, total: seen_progress.append(
                        (filename, current, total)
                    ),
                )

            self.assertTrue((installation.install_dir / "config.json").exists())
            self.assertTrue((installation.install_dir / "model.safetensors").exists())
            self.assertEqual(
                (installation.install_dir / "config.json").read_text(encoding="utf-8"),
                '{"model_type":"dinov2"}',
            )
            self.assertEqual((installation.install_dir / "model.safetensors").read_bytes(), b"weights")
            self.assertTrue(any(filename == "model.safetensors" for filename, _, _ in seen_progress))


if __name__ == "__main__":
    unittest.main()
