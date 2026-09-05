from __future__ import annotations

import os
import tempfile
import unittest
import hashlib
import urllib.error
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from image_triage.ai_model import (
    AICULLER_CLIP_MODEL_REQUIRED_FILENAMES,
    AICULLER_FACE_MODEL_REQUIRED_FILENAMES,
    AICULLER_TOPIQ_MODEL_REQUIRED_FILENAMES,
    BIREFNET_MODEL_REQUIRED_FILENAMES,
    SAM_MODEL_REQUIRED_FILENAMES,
    SEGMENTATION_MODEL_REQUIRED_FILENAMES,
    SEMANTIC_MODEL_REQUIRED_FILENAMES,
    download_ai_model,
    download_aiculler_clip_model,
    download_aiculler_face_model,
    download_aiculler_topiq_model,
    download_semantic_model,
    resolve_aiculler_clip_model_installation,
    resolve_aiculler_face_model_installation,
    resolve_aiculler_topiq_model_installation,
    resolve_ai_model_installation,
    resolve_birefnet_model_installation,
    resolve_sam_model_installation,
    resolve_segmentation_model_installation,
    resolve_semantic_model_installation,
)


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

    def test_default_ai_model_installation_uses_model_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_ai_model_installation(repo_id="owner/DinoV2")

        self.assertEqual(installation.install_dir.name, "DinoV2")
        self.assertEqual(installation.install_dir.parent.name, "models")

    def test_default_ai_model_installation_uses_local_appdata_without_home_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                with patch("image_triage.ai_model.Path.home", side_effect=RuntimeError("no home")):
                    installation = resolve_ai_model_installation(repo_id="owner/DinoV2")

        self.assertTrue(str(installation.install_dir).startswith(temp_dir))

    def test_default_semantic_model_installation_uses_model_name_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_semantic_model_installation(repo_id="owner/clip-vit-base-patch32")

        self.assertEqual(installation.install_dir.name, "clip-vit-base-patch32")
        self.assertEqual(installation.install_dir.parent.name, "models")
        self.assertEqual(installation.required_filenames, SEMANTIC_MODEL_REQUIRED_FILENAMES)

    def test_default_segmentation_model_installation_uses_oneformer(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_segmentation_model_installation()

        self.assertEqual(
            installation.repo_id,
            "shi-labs/oneformer_ade20k_swin_tiny",
        )
        self.assertEqual(
            installation.install_dir.name,
            "oneformer_ade20k_swin_tiny",
        )
        self.assertEqual(
            installation.required_filenames,
            SEGMENTATION_MODEL_REQUIRED_FILENAMES,
        )
        # OneFormer's seven files land flat in the managed directory (no onnx/).
        self.assertIn("pytorch_model.bin", SEGMENTATION_MODEL_REQUIRED_FILENAMES)
        self.assertNotIn("onnx/model.onnx", SEGMENTATION_MODEL_REQUIRED_FILENAMES)
        self.assertTrue(
            installation.download_url("pytorch_model.bin").endswith(
                "/pytorch_model.bin?download=true"
            )
        )

    def test_segmentation_download_verifies_pinned_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_segmentation_model_installation()
        # Default repo/revision carry the pinned checksums for offline verification.
        self.assertIsNotNone(installation.expected_sha256)
        self.assertEqual(
            set(installation.expected_sha256 or {}),
            set(SEGMENTATION_MODEL_REQUIRED_FILENAMES),
        )

    def test_default_birefnet_installation_is_an_optional_editor_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_birefnet_model_installation()

        self.assertEqual("ZhengPeng7/BiRefNet", installation.repo_id)
        self.assertEqual("BiRefNet", installation.install_dir.name)
        self.assertEqual("Editor", installation.install_dir.parent.name)
        self.assertEqual(BIREFNET_MODEL_REQUIRED_FILENAMES, installation.required_filenames)
        self.assertIn(
            "/ZhengPeng7/BiRefNet/resolve/",
            installation.download_url("model.safetensors"),
        )

    def test_default_sam_installation_is_an_optional_editor_model(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_sam_model_installation()

        self.assertEqual("facebook/sam2.1-hiera-tiny", installation.repo_id)
        self.assertEqual("sam2.1-hiera-tiny", installation.install_dir.name)
        self.assertEqual("Editor", installation.install_dir.parent.name)
        self.assertEqual(SAM_MODEL_REQUIRED_FILENAMES, installation.required_filenames)
        self.assertIn("model.safetensors", SAM_MODEL_REQUIRED_FILENAMES)
        # Default repo/revision carry pinned hashes for offline verification.
        self.assertIsNotNone(installation.expected_sha256)
        self.assertEqual(set(installation.expected_sha256 or {}), set(SAM_MODEL_REQUIRED_FILENAMES))

    def test_default_aiculler_clip_model_installation_uses_runtime_cache_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_aiculler_clip_model_installation()

        self.assertEqual("onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX", installation.repo_id)
        self.assertEqual(installation.install_dir.name, "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX")
        self.assertEqual(installation.install_dir.parent.name, "Clip")
        self.assertEqual(installation.required_filenames, AICULLER_CLIP_MODEL_REQUIRED_FILENAMES)
        self.assertIn("onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX", installation.download_url("tokenizer.json"))
        self.assertIn("onnx/model.onnx", installation.download_url("onnx/model.onnx"))
        self.assertEqual(("tokenizer.json", "onnx/model.onnx"), installation.required_filenames)

    def test_fp16_aiculler_clip_model_selects_combined_export(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_aiculler_clip_model_installation(variant="fp16")

        self.assertEqual("onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX", installation.repo_id)
        self.assertEqual(
            (
                "tokenizer.json",
                "onnx/model_fp16.onnx",
            ),
            installation.required_filenames,
        )
        self.assertIn("onnx/model_fp16.onnx", installation.download_url("onnx/model_fp16.onnx"))

    def test_default_aiculler_topiq_model_installation_uses_runtime_cache_layout(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {"LOCALAPPDATA": temp_dir}
            with patch.dict(os.environ, env, clear=False):
                installation = resolve_aiculler_topiq_model_installation()

        self.assertEqual("Skulleton12/TOPIQ", installation.repo_id)
        self.assertEqual(installation.install_dir.name, "TOPIQ")
        self.assertEqual(installation.required_filenames, AICULLER_TOPIQ_MODEL_REQUIRED_FILENAMES)

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

    def test_download_semantic_model_fetches_required_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = resolve_semantic_model_installation(
                install_dir=Path(temp_dir) / "downloaded-semantic-model",
                repo_id="owner/clip",
                revision="main",
            )
            payloads = {filename: filename.encode("utf-8") for filename in SEMANTIC_MODEL_REQUIRED_FILENAMES}
            seen_progress: list[tuple[str, int, int]] = []

            def fake_urlopen(request):
                url = getattr(request, "full_url", str(request))
                filename = url.split("?", 1)[0].rsplit("/", 1)[-1]
                return _FakeResponse(payloads[filename])

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                download_semantic_model(
                    installation,
                    progress_callback=lambda filename, current, total: seen_progress.append(
                        (filename, current, total)
                    ),
                )

            self.assertTrue(installation.is_installed)
            self.assertEqual((installation.install_dir / "pytorch_model.bin").read_bytes(), b"pytorch_model.bin")
            self.assertTrue(any(filename == "pytorch_model.bin" for filename, _, _ in seen_progress))

    def test_download_ai_model_verifies_expected_sha256(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            payload = b"weights"
            installation = replace(
                resolve_ai_model_installation(
                    install_dir=Path(temp_dir) / "downloaded-model",
                    repo_id="owner/repo",
                    revision="main",
                ),
                required_filenames=("model.safetensors",),
                expected_sha256={"model.safetensors": hashlib.sha256(payload).hexdigest()},
            )

            with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
                download_ai_model(installation)

            self.assertEqual((installation.install_dir / "model.safetensors").read_bytes(), payload)

    def test_download_ai_model_rejects_sha256_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = replace(
                resolve_ai_model_installation(
                    install_dir=Path(temp_dir) / "downloaded-model",
                    repo_id="owner/repo",
                    revision="main",
                ),
                required_filenames=("model.safetensors",),
                expected_sha256={"model.safetensors": "0" * 64},
            )

            with patch("urllib.request.urlopen", return_value=_FakeResponse(b"weights")):
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    download_ai_model(installation)

            self.assertFalse((installation.install_dir / "model.safetensors").exists())

    def test_download_ai_model_rejects_non_https_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = replace(
                resolve_ai_model_installation(
                    install_dir=Path(temp_dir) / "downloaded-model",
                    repo_id="owner/repo",
                    revision="main",
                ),
                required_filenames=("model.safetensors",),
            )

            with patch("image_triage.ai_model.AIModelInstallation.download_url", return_value="file:///tmp/model.safetensors"):
                with self.assertRaisesRegex(ValueError, "https"):
                    download_ai_model(installation)

    def test_download_ai_model_reports_http_error_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = replace(
                resolve_ai_model_installation(
                    install_dir=Path(temp_dir) / "downloaded-model",
                    repo_id="owner/repo",
                    revision="main",
                ),
                required_filenames=("missing.bin",),
            )

            error = urllib.error.HTTPError(
                url=installation.download_url("missing.bin"),
                code=404,
                msg="Not Found",
                hdrs={},
                fp=None,
            )
            with patch("urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "missing.bin.*HTTP 404"):
                    download_ai_model(installation)

    def test_download_ai_model_tries_alternate_source_filenames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = replace(
                resolve_ai_model_installation(
                    install_dir=Path(temp_dir) / "downloaded-model",
                    repo_id="owner/repo",
                    revision="main",
                ),
                required_filenames=("tokenizer.json",),
                alternate_download_filenames={"tokenizer.json": ("onnx/tokenizer.json",)},
            )
            seen_urls: list[str] = []

            def fake_urlopen(request):
                url = getattr(request, "full_url", str(request))
                seen_urls.append(url)
                if "/tokenizer.json" in url and "/onnx/tokenizer.json" not in url:
                    raise urllib.error.HTTPError(url=url, code=404, msg="Not Found", hdrs={}, fp=None)
                return _FakeResponse(b"tokenizer")

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                download_ai_model(installation)

            self.assertEqual((installation.install_dir / "tokenizer.json").read_bytes(), b"tokenizer")
            self.assertEqual(len(seen_urls), 2)
            self.assertIn("/onnx/tokenizer.json", seen_urls[-1])

    def test_download_aiculler_clip_model_fetches_nested_onnx_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = resolve_aiculler_clip_model_installation(
                install_dir=Path(temp_dir) / "clip",
                repo_id="owner/clip",
                revision="main",
            )
            payloads = {filename: filename.encode("utf-8") for filename in AICULLER_CLIP_MODEL_REQUIRED_FILENAMES}

            def fake_urlopen(request):
                url = getattr(request, "full_url", str(request)).split("?", 1)[0]
                filename = url.split("/resolve/main/", 1)[1]
                return _FakeResponse(payloads[filename])

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                download_aiculler_clip_model(installation)

            self.assertTrue(installation.is_installed)
            self.assertTrue((installation.install_dir / "onnx" / "model.onnx").exists())

    def test_download_aiculler_topiq_model_fetches_required_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = resolve_aiculler_topiq_model_installation(
                install_dir=Path(temp_dir) / "topiq",
                repo_id="owner/topiq",
                revision="main",
            )
            payloads = {"topiq_nr.onnx": b"topiq"}

            def fake_urlopen(request):
                url = getattr(request, "full_url", str(request))
                filename = url.split("?", 1)[0].rsplit("/", 1)[-1]
                return _FakeResponse(payloads[filename])

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                download_aiculler_topiq_model(installation)

            self.assertTrue(installation.is_installed)
            self.assertEqual((installation.install_dir / "topiq_nr.onnx").read_bytes(), b"topiq")

    def test_download_aiculler_face_model_fetches_quality_models_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installation = resolve_aiculler_face_model_installation(
                install_dir=Path(temp_dir) / "faces" / "models" / "auraface",
                repo_id="owner/auraface",
                revision="main",
            )
            payloads = {filename: filename.encode("utf-8") for filename in AICULLER_FACE_MODEL_REQUIRED_FILENAMES}
            seen: list[str] = []

            def fake_urlopen(request):
                url = getattr(request, "full_url", str(request))
                filename = url.split("?", 1)[0].rsplit("/", 1)[-1]
                seen.append(filename)
                return _FakeResponse(payloads[filename])

            with patch("urllib.request.urlopen", side_effect=fake_urlopen):
                download_aiculler_face_model(installation)

            self.assertTrue(installation.is_installed)
            self.assertEqual(tuple(seen), AICULLER_FACE_MODEL_REQUIRED_FILENAMES)
            self.assertIn("glintr100.onnx", seen)


if __name__ == "__main__":
    unittest.main()
