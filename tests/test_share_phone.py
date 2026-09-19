from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from PIL import Image

from image_triage.image_resize import ResizeSourceItem
from image_triage.share_phone import (
    LocalShareServer,
    SharePackageSpec,
    _parse_phone_share_network_diagnostic,
    prepare_share_package,
    qr_png_bytes,
)


class SharePhoneTests(unittest.TestCase):
    def _prepare(self, temp_dir: str):
        source = Path(temp_dir) / "source.png"
        Image.new("RGB", (2400, 1600), (42, 88, 130)).save(source)
        previous = os.environ.get("IMAGE_TRIAGE_APPDATA")
        os.environ["IMAGE_TRIAGE_APPDATA"] = temp_dir
        try:
            return prepare_share_package(
                SharePackageSpec(
                    name="Launch Set",
                    target="Instagram",
                    account="Studio",
                    caption="Evening light #portfolio",
                    preset_key="social_large",
                    sources=(ResizeSourceItem(str(source), source.name),),
                    alt_text=("Blue test image",),
                )
            )
        finally:
            if previous is None:
                os.environ.pop("IMAGE_TRIAGE_APPDATA", None)
            else:
                os.environ["IMAGE_TRIAGE_APPDATA"] = previous

    def test_package_contains_ordered_derivative_and_handoff_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            package = self._prepare(temp_dir)
            output = Path(package.output_paths[0])
            self.assertEqual(output.name, "01-source.jpg")
            with Image.open(output) as prepared:
                self.assertEqual(prepared.size, (2160, 1440))
                self.assertFalse(prepared.getexif())
            self.assertEqual(Path(package.caption_path).read_text(encoding="utf-8"), "Evening light #portfolio")
            manifest = json.loads(Path(package.manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest["images"][0]["alt_text"], "Blue test image")
            with zipfile.ZipFile(package.zip_path) as archive:
                self.assertEqual(
                    set(archive.namelist()),
                    {"01-source.jpg", "caption.txt", "alt-text.txt", "manifest.json"},
                )

    def test_server_is_token_scoped_and_download_marks_transfer(self) -> None:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            probe.close()
        except OSError as exc:
            self.skipTest(f"Sockets are unavailable in this Windows test environment: {exc}")
        with tempfile.TemporaryDirectory() as temp_dir:
            package = self._prepare(temp_dir)
            transferred = threading.Event()
            server = LocalShareServer(package, bind_host="127.0.0.1", on_transfer=transferred.set)
            try:
                server.start()
                with urllib.request.urlopen(server.loopback_url, timeout=3) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("Launch Set", page)
                self.assertIn("Download All", page)
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    urllib.request.urlopen(f"http://127.0.0.1:{server.port}/wrong/", timeout=3)
                self.assertEqual(denied.exception.code, 404)
                with urllib.request.urlopen(f"{server.loopback_url}download-all.zip", timeout=3) as response:
                    self.assertGreater(len(response.read()), 0)
                self.assertTrue(transferred.wait(1))
            finally:
                server.stop()

    def test_qr_encoder_returns_png(self) -> None:
        payload = qr_png_bytes("http://192.168.1.20:12345/token/")
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_public_firewall_profile_produces_actionable_warning(self) -> None:
        diagnostic = _parse_phone_share_network_diagnostic(
            "Public Profile Settings:\nFirewall Policy BlockInbound,AllowOutbound",
            "No rules match the specified criteria.",
        )

        self.assertEqual(diagnostic.profile, "public")
        self.assertTrue(diagnostic.inbound_blocked)
        self.assertFalse(diagnostic.private_rule_present)
        self.assertIn("change it to Private", diagnostic.warning)

    def test_private_rule_clears_generic_firewall_warning(self) -> None:
        diagnostic = _parse_phone_share_network_diagnostic(
            "Private Profile Settings:\nFirewall Policy BlockInbound,AllowOutbound",
            "Rule Name: Image Triage Phone Share\nEnabled: Yes\nAction: Allow",
        )

        self.assertEqual(diagnostic.profile, "private")
        self.assertTrue(diagnostic.private_rule_present)
        self.assertEqual(diagnostic.warning, "")


if __name__ == "__main__":
    unittest.main()
