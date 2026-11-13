from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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

    def test_update_without_a_checksum_is_refused_before_any_download(self) -> None:
        info = updater.UpdateInfo(version="1.2.0", installer_url="https://example.test/ImageTriage-1.2.0.msi")

        with tempfile.TemporaryDirectory() as temp_dir, patch.object(
            updater.urllib.request, "urlopen", side_effect=AssertionError("must not touch the network")
        ):
            with self.assertRaisesRegex(RuntimeError, "cannot be verified"):
                updater.download_update_installer(info, destination_dir=temp_dir)
            self.assertEqual([], list(Path(temp_dir).iterdir()))

    def test_a_malformed_checksum_counts_as_unverifiable(self) -> None:
        for bad in ("", "abc", "z" * 64, "a" * 63, "sha256:" + "a" * 65):
            info = updater.UpdateInfo(version="1", installer_url="https://example.test/a.msi", sha256=bad)
            self.assertFalse(info.is_verifiable, bad)
        good = updater.UpdateInfo(version="1", installer_url="https://example.test/a.msi", sha256="sha256:" + "A" * 64)
        self.assertTrue(good.is_verifiable)

    def test_github_release_without_a_digest_is_not_verifiable(self) -> None:
        info = updater._update_info_from_github_release(
            {
                "tag_name": "v2.0.0",
                "assets": [
                    {"name": "Image Triage-2.0.0-win64.msi", "browser_download_url": "https://example.test/a.msi"}
                ],
            }
        )

        self.assertEqual("", info.sha256)
        self.assertFalse(info.is_verifiable)

    def test_a_feed_or_installer_that_is_not_https_is_refused_before_the_network(self) -> None:
        with patch.object(updater.urllib.request, "urlopen", side_effect=AssertionError("must not touch the network")):
            for feed in ("http://example.test/update.json", "file:///tmp/update.json", "ftp://example.test/u.json"):
                with self.assertRaisesRegex(ValueError, "must use https"):
                    updater.check_for_update(current_version="1.0.0", feed_url=feed)
                with self.assertRaisesRegex(ValueError, "must use https"):
                    updater.fetch_update_info(feed)
            info = updater.UpdateInfo(version="1.2.0", installer_url="http://example.test/a.msi", sha256="a" * 64)
            with tempfile.TemporaryDirectory() as temp_dir:
                with self.assertRaisesRegex(ValueError, "must use https"):
                    updater.download_update_installer(info, destination_dir=temp_dir)
                self.assertEqual([], list(Path(temp_dir).iterdir()))

    def test_a_redirect_that_leaves_https_is_refused_and_leaves_no_file(self) -> None:
        payload = b"msi bytes"

        class Redirected(_FakeResponse):
            def geturl(self) -> str:
                return "http://mirror.example.test/a.msi"

        info = updater.UpdateInfo(
            version="1.2.0", installer_url="https://example.test/a.msi", sha256=hashlib.sha256(payload).hexdigest()
        )
        with tempfile.TemporaryDirectory() as temp_dir, _urlopen_returning(Redirected(payload)):
            with self.assertRaisesRegex(ValueError, "must use https"):
                updater.download_update_installer(info, destination_dir=temp_dir)
            self.assertEqual([], list(Path(temp_dir).iterdir()))
        with _urlopen_returning(Redirected(b"{}")):
            with self.assertRaisesRegex(ValueError, "must use https"):
                updater.fetch_update_info("https://example.test/update.json")

    def test_a_download_cut_off_part_way_is_reported_and_removed(self) -> None:
        import http.client

        class CutOff(_FakeResponse):
            def read(self, size: int = -1) -> bytes:
                if self._offset:
                    raise http.client.IncompleteRead(b"", 10)
                return super().read(4)

        info = updater.UpdateInfo(version="1.2.0", installer_url="https://example.test/a.msi", sha256="a" * 64)
        with tempfile.TemporaryDirectory() as temp_dir, _urlopen_returning(CutOff(b"msi bytes")):
            with self.assertRaisesRegex(RuntimeError, "interrupted"):
                updater.download_update_installer(info, destination_dir=temp_dir)
            self.assertEqual([], list(Path(temp_dir).iterdir()))

    def test_the_installer_is_hashed_again_before_it_is_started(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "ImageTriage-1.2.0.msi"
            installer.write_bytes(b"msi bytes")
            expected = hashlib.sha256(b"msi bytes").hexdigest()
            updater.verify_installer_checksum(installer, "sha256:" + expected.upper())
            installer.write_bytes(b"swapped")
            with self.assertRaisesRegex(RuntimeError, "changed after it was verified"):
                updater.verify_installer_checksum(installer, expected)
            with self.assertRaisesRegex(RuntimeError, "no valid SHA-256"):
                updater.verify_installer_checksum(installer, "")
            with patch.object(updater.sys, "platform", "win32"), patch.object(updater.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(RuntimeError, "changed after it was verified"):
                    updater.launch_update_installer(installer, expected_sha256=expected)
                with self.assertRaisesRegex(RuntimeError, "changed after it was verified"):
                    updater.launch_update_installer_and_restart(
                        installer, app_executable=installer, current_pid=1, expected_sha256=expected
                    )
                popen.assert_not_called()
                installer.write_bytes(b"msi bytes")
                updater.launch_update_installer_and_restart(
                    installer, app_executable=installer, current_pid=1, expected_sha256=expected
                )
                popen.assert_called_once()

    # ---- the installer's signer (audit R6). Nothing here runs Windows' own check: the verifier is a fake. ----
    PIN = updater.InstallerSignerPin(subjects=("Example Publisher",), thumbprints=("AB" * 20,))

    @staticmethod
    def _signed(subject: str = "Example Publisher", sha1: str = "ab" * 20) -> updater.InstallerSignature:
        return updater.InstallerSignature(updater.SIGNATURE_VALID, subject=subject, thumbprint_sha1=sha1, thumbprint_sha256="cd" * 32)

    def test_releases_are_not_signed_today_so_no_publisher_is_pinned(self) -> None:
        # The pin and the release build must change together: pinning a publisher while the build does not sign
        # would refuse every update, and signing without pinning would leave the signer unchecked.
        workflow = (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "build-windows-msi-release.yml").read_text(encoding="utf-8")
        signs = any(word in workflow.casefold() for word in ("signtool", "azuresigntool", "trusted-signing", "set-authenticodesignature"))
        self.assertEqual(signs, updater.TRUSTED_INSTALLER_SIGNERS.configured)

    def test_a_valid_signature_from_the_pinned_publisher_is_verified(self) -> None:
        seen: list[Path] = []

        def verifier(path: Path) -> updater.InstallerSignature:
            seen.append(path)
            return self._signed()

        check = updater.check_installer_publisher("a.msi", verifier=verifier, pin=self.PIN)
        self.assertTrue(check.verified)
        self.assertEqual("", check.notice)
        self.assertEqual([Path("a.msi")], seen)
        # either kind of thumbprint may be pinned, written the way Windows shows it
        by_sha256 = updater.InstallerSignerPin(thumbprints=("CD " * 32,))
        self.assertTrue(updater.check_installer_publisher("a.msi", verifier=verifier, pin=by_sha256).verified)

    def test_a_valid_signature_from_anyone_else_is_refused(self) -> None:
        for signature in (
            self._signed(subject="Somebody Else"),              # right certificate hash claimed, wrong name
            self._signed(sha1="ef" * 20),                       # right name, another certificate
            self._signed(subject="", sha1=""),
        ):
            with self.assertRaisesRegex(updater.InstallerSignerError, "not by the publisher of Image Triage"):
                updater.check_installer_publisher("a.msi", verifier=lambda path: signature, pin=self.PIN)

    def test_an_invalid_signature_is_refused_whether_or_not_a_publisher_is_pinned(self) -> None:
        broken = updater.InstallerSignature(updater.SIGNATURE_INVALID, detail="the file was changed after it was signed")
        for pin in (self.PIN, updater.InstallerSignerPin()):
            with self.assertRaisesRegex(updater.InstallerSignerError, "not valid .*changed after it was signed"):
                updater.check_installer_publisher("a.msi", verifier=lambda path: broken, pin=pin)
        unknown = updater.InstallerSignature("something new")
        with self.assertRaises(updater.InstallerSignerError):
            updater.check_installer_publisher("a.msi", verifier=lambda path: unknown, pin=updater.InstallerSignerPin())

    def test_an_unsigned_installer_is_refused_once_a_publisher_is_pinned(self) -> None:
        unsigned = updater.InstallerSignature(updater.SIGNATURE_UNSIGNED)
        with self.assertRaisesRegex(updater.InstallerSignerError, "not digitally signed"):
            updater.check_installer_publisher("a.msi", verifier=lambda path: unsigned, pin=self.PIN)
        # a system that cannot check signatures cannot satisfy a pin either
        nowhere = updater.InstallerSignature(updater.SIGNATURE_NOT_APPLICABLE)
        with self.assertRaisesRegex(updater.InstallerSignerError, "cannot be checked"):
            updater.check_installer_publisher("a.msi", verifier=lambda path: nowhere, pin=self.PIN)

    def test_an_unsigned_installer_with_no_pin_is_allowed_and_says_the_publisher_is_unverified(self) -> None:
        unsigned = updater.InstallerSignature(updater.SIGNATURE_UNSIGNED)
        check = updater.check_installer_publisher("a.msi", verifier=lambda path: unsigned, pin=updater.InstallerSignerPin())
        self.assertFalse(check.verified)
        self.assertIn("publisher could not be verified", check.notice)
        # signed, but there is nothing to compare the signer with: still not "verified"
        check = updater.check_installer_publisher("a.msi", verifier=lambda path: self._signed(), pin=updater.InstallerSignerPin())
        self.assertFalse(check.verified)
        self.assertIn("Example Publisher", check.notice)
        self.assertIn("not verified", check.notice)

    def test_the_signature_check_is_not_applicable_off_windows(self) -> None:
        if updater.os.name == "nt":
            self.skipTest("this is the answer every other system gives")
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "a.msi"
            installer.write_bytes(b"msi bytes")
            self.assertEqual(updater.SIGNATURE_NOT_APPLICABLE, updater.read_authenticode_signature(installer).status)
            check = updater.check_installer_publisher(installer, pin=updater.InstallerSignerPin())
            self.assertFalse(check.verified)
            self.assertIn("only checked on Windows", check.notice)
            with self.assertRaisesRegex(OSError, "only supported on Windows"):
                updater.launch_update_installer(installer)

    def test_only_a_missing_signature_counts_as_unsigned(self) -> None:
        no_signature = 0x800B0100
        self.assertEqual((updater.SIGNATURE_VALID, ""), updater._classify_trust_result(0, 0))
        for last_error in (0x800B0100, 0x800B0003, 0x800B0001):
            self.assertEqual(updater.SIGNATURE_UNSIGNED, updater._classify_trust_result(no_signature, last_error)[0])
        # the same answer as a signed 32-bit number, which is how the call returns it
        self.assertEqual(updater.SIGNATURE_UNSIGNED, updater._classify_trust_result(no_signature - (1 << 32), no_signature)[0])
        # "no signature" with any other reason is a signature that could not be read or checked
        self.assertEqual(updater.SIGNATURE_INVALID, updater._classify_trust_result(no_signature, 0x80096010)[0])
        self.assertEqual(updater.SIGNATURE_INVALID, updater._classify_trust_result(no_signature, 0)[0])
        for failure in (0x80096010, 0x800B0101, 0x800B0109, 0x800B010C, 0x800B0004, 0x80070005):
            status, detail = updater._classify_trust_result(failure, 0)
            self.assertEqual(updater.SIGNATURE_INVALID, status)
            self.assertTrue(detail)

    def test_the_signer_is_checked_on_the_hashed_file_and_that_file_is_what_is_started(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "ImageTriage-1.2.0.msi"
            installer.write_bytes(b"msi bytes")
            expected = hashlib.sha256(b"msi bytes").hexdigest()
            events: list[tuple[str, str]] = []
            real_hash = updater._sha256_file

            def hashing(path: Path) -> str:
                events.append(("hash", str(path)))
                return real_hash(path)

            def verifier(path: Path) -> updater.InstallerSignature:
                events.append(("signer", str(path)))
                self.assertEqual(b"msi bytes", Path(path).read_bytes())
                return self._signed()

            def started(command, **kwargs):
                events.append(("start", " ".join(command)))

            with patch.object(updater.sys, "platform", "win32"), patch.object(updater, "_sha256_file", hashing), patch.object(
                updater.subprocess, "Popen", side_effect=started
            ), patch.object(updater.urllib.request, "urlopen", side_effect=AssertionError("nothing is downloaded again")):
                updater.launch_update_installer(installer, expected_sha256=expected, signature_verifier=verifier, signer_pin=self.PIN)
                updater.launch_update_installer_and_restart(
                    installer, app_executable=installer, current_pid=1, expected_sha256=expected,
                    signature_verifier=verifier, signer_pin=self.PIN,
                )
            self.assertEqual(["hash", "signer", "start"] * 2, [kind for kind, _ in events])
            self.assertEqual({str(installer)}, {text for kind, text in events if kind != "start"})
            for text in (text for kind, text in events if kind == "start"):
                self.assertIn(str(installer), text)

    def test_a_refused_signer_starts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            installer = Path(temp_dir) / "ImageTriage-1.2.0.msi"
            installer.write_bytes(b"msi bytes")
            expected = hashlib.sha256(b"msi bytes").hexdigest()
            verdicts = {
                "wrong signer": (self._signed(subject="Somebody Else"), self.PIN),
                "broken": (updater.InstallerSignature(updater.SIGNATURE_INVALID), updater.InstallerSignerPin()),
                "unsigned, pinned": (updater.InstallerSignature(updater.SIGNATURE_UNSIGNED), self.PIN),
            }
            with patch.object(updater.sys, "platform", "win32"), patch.object(updater.subprocess, "Popen") as popen:
                for name, (signature, pin) in verdicts.items():
                    with self.subTest(name), self.assertRaises(updater.InstallerSignerError):
                        updater.launch_update_installer(
                            installer, expected_sha256=expected, signature_verifier=lambda path, s=signature: s, signer_pin=pin
                        )
                    with self.subTest(name), self.assertRaises(updater.InstallerSignerError):
                        updater.launch_update_installer_and_restart(
                            installer, app_executable=installer, current_pid=1, expected_sha256=expected,
                            signature_verifier=lambda path, s=signature: s, signer_pin=pin,
                        )
                popen.assert_not_called()
                # the signer is not asked about a file that already failed its checksum
                installer.write_bytes(b"swapped")
                with self.assertRaisesRegex(RuntimeError, "changed after it was verified"):
                    updater.launch_update_installer(
                        installer, expected_sha256=expected,
                        signature_verifier=lambda path: self.fail("the signer was checked on a file that failed its checksum"),
                    )
                popen.assert_not_called()
                # unsigned and nothing pinned: today's releases still install
                installer.write_bytes(b"msi bytes")
                updater.launch_update_installer(
                    installer, expected_sha256=expected,
                    signature_verifier=lambda path: updater.InstallerSignature(updater.SIGNATURE_UNSIGNED),
                    signer_pin=updater.InstallerSignerPin(),
                )
                popen.assert_called_once()

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
