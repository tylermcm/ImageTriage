from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from image_triage import updater


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._offset = 0
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._payload) - self._offset
        start = self._offset
        end = min(len(self._payload), start + size)
        self._offset = end
        return self._payload[start:end]


@contextmanager
def _urlopen_returning(response: _FakeResponse):
    original = updater.urllib.request.urlopen
    updater.urllib.request.urlopen = lambda *args, **kwargs: response
    try:
        yield
    finally:
        updater.urllib.request.urlopen = original


class UpdaterTests(unittest.TestCase):
    def test_default_update_feed_points_to_project_release_repo(self) -> None:
        self.assertEqual(
            "https://api.github.com/repos/tylermcm/ImageTriage/releases/latest",
            updater.DEFAULT_UPDATE_FEED_URL,
        )

    def test_check_for_update_uses_manifest_payload(self) -> None:
        payload = {
            "version": "1.2.0",
            "installer_url": "https://example.test/ImageTriage-1.2.0.msi",
            "release_notes_url": "https://example.test/releases/1.2.0",
            "sha256": "sha256:" + "a" * 64,
        }

        with _urlopen_returning(_FakeResponse(json.dumps(payload).encode("utf-8"))):
            result = updater.check_for_update(current_version="1.1.3", feed_url="https://example.test/update.json")

        self.assertTrue(result.update_available)
        self.assertEqual(result.latest.version, "1.2.0")
        self.assertEqual(result.latest.installer_url, "https://example.test/ImageTriage-1.2.0.msi")
        self.assertEqual(result.latest.sha256, "a" * 64)

    def test_check_for_update_reports_current_when_versions_match(self) -> None:
        payload = {
            "version": "1.1.3",
            "msi_url": "https://example.test/ImageTriage-1.1.3.msi",
        }

        with _urlopen_returning(_FakeResponse(json.dumps(payload).encode("utf-8"))):
            result = updater.check_for_update(current_version="1.1.3", feed_url="https://example.test/update.json")

        self.assertFalse(result.update_available)

    def test_fetch_update_info_accepts_github_release_api_payload(self) -> None:
        payload = {
            "tag_name": "v1.2.0",
            "name": "Image Triage 1.2.0",
            "html_url": "https://github.com/tylermcm/ImageTriage/releases/tag/v1.2.0",
            "assets": [
                {
                    "name": "ImageTriage-1.2.0.zip",
                    "browser_download_url": "https://example.test/ImageTriage-1.2.0.zip",
                },
                {
                    "name": "ImageTriage-1.2.0.msi",
                    "browser_download_url": "https://example.test/ImageTriage-1.2.0.msi",
                    "digest": "sha256:" + "b" * 64,
                },
            ],
        }

        with _urlopen_returning(_FakeResponse(json.dumps(payload).encode("utf-8"))):
            info = updater.fetch_update_info("https://api.github.com/repos/tylermcm/ImageTriage/releases/latest")

        self.assertEqual(info.version, "1.2.0")
        self.assertEqual(info.installer_url, "https://example.test/ImageTriage-1.2.0.msi")
        self.assertEqual(info.sha256, "b" * 64)

    def test_is_newer_version_compares_dotted_numbers(self) -> None:
        self.assertTrue(updater.is_newer_version("1.10.0", "1.9.9"))
        self.assertTrue(updater.is_newer_version("v2.0", "1.99.99"))
        self.assertFalse(updater.is_newer_version("1.1.3", "1.1.3"))
        self.assertFalse(updater.is_newer_version("1.1.3", "1.1.4"))

    def test_download_update_installer_verifies_sha256(self) -> None:
        payload = b"msi bytes"
        expected = hashlib.sha256(payload).hexdigest()
        info = updater.UpdateInfo(
            version="1.2.0",
            installer_url="https://example.test/ImageTriage-1.2.0.msi",
            sha256=expected,
        )
        progress: list[tuple[int, int, str]] = []

        with tempfile.TemporaryDirectory() as temp_dir, _urlopen_returning(_FakeResponse(payload)):
            path = updater.download_update_installer(
                info,
                destination_dir=temp_dir,
                progress_callback=lambda current, total, filename: progress.append((current, total, filename)),
            )

            self.assertEqual(path.read_bytes(), payload)
            self.assertEqual(path.name, "ImageTriage-1.2.0.msi")

        self.assertTrue(progress)
        self.assertEqual(progress[-1][0], len(payload))
        self.assertEqual(progress[-1][1], len(payload))

    def test_download_update_installer_rejects_sha256_mismatch(self) -> None:
        info = updater.UpdateInfo(
            version="1.2.0",
            installer_url="https://example.test/ImageTriage-1.2.0.msi",
            sha256="0" * 64,
        )

        with tempfile.TemporaryDirectory() as temp_dir, _urlopen_returning(_FakeResponse(b"not expected")):
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                updater.download_update_installer(info, destination_dir=temp_dir)
            self.assertFalse((Path(temp_dir) / "ImageTriage-1.2.0.msi").exists())

    def test_update_handoff_command_waits_installs_silently_and_restarts(self) -> None:
        command = updater._build_update_handoff_command(
            Path("C:/Users/Test User/Downloads/Image Triage 1.2.0.msi"),
            Path("C:/Program Files/Image Triage/ImageTriage.exe"),
            1234,
        )

        self.assertIn("Wait-Process -Id $imageTriagePid", command)
        self.assertIn("/qn", command)
        self.assertIn("/norestart", command)
        self.assertIn("Start-Process -FilePath $app", command)
        self.assertIn("Image Triage 1.2.0.msi", command)

    def test_powershell_quote_escapes_single_quotes(self) -> None:
        self.assertEqual("'C:/Bob''s App/ImageTriage.exe'", updater._powershell_quote("C:/Bob's App/ImageTriage.exe"))


if __name__ == "__main__":
    unittest.main()
