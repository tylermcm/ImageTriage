"""Transactional model bundle installation, verification and repair.

Every boundary the handoff lists as a failure class is injected here: truncated
downloads, wrong hashes, HTML error pages, locked directories, interrupted
installs, cancellation, missing disk space and concurrent installers.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from image_triage import ai_manifest, ai_model_store, ai_paths
from image_triage.ai_manifest import FileDigest, ModelBundle


PAYLOAD = b"a managed model file" * 8
PAYLOAD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
SECOND = b"a second file"
SECOND_SHA = hashlib.sha256(SECOND).hexdigest()

TEST_BUNDLE = ModelBundle(
    key="testbundle",
    name="Test bundle",
    repo_id="acme/test-bundle",
    revision="a" * 40,
    filenames=("weights.bin", "nested/config.json"),
    install_parts=("Test", "Bundle"),
    approx_mb=1,
)
TEST_DIGESTS = {
    "weights.bin": FileDigest(len(PAYLOAD), PAYLOAD_SHA),
    "nested/config.json": FileDigest(len(SECOND), SECOND_SHA),
}
CONTENT = {"weights.bin": PAYLOAD, "nested/config.json": SECOND}


class _StoreTestCase(unittest.TestCase):
    """Isolates every test in its own managed root with one synthetic bundle."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self._patches = [
            patch.dict(ai_manifest.MODEL_BUNDLES, {TEST_BUNDLE.key: TEST_BUNDLE}, clear=False),
            patch.dict(
                ai_manifest.MODEL_FILE_DIGESTS, {TEST_BUNDLE.key: TEST_DIGESTS}, clear=False
            ),
            patch.dict(os.environ, {ai_paths.AI_ROOT_ENV: str(self.root)}, clear=False),
        ]
        for item in self._patches:
            item.start()
        ai_model_store._MIGRATION_DONE = True
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        for item in reversed(self._patches):
            item.stop()
        ai_model_store._MIGRATION_DONE = False
        self._temp.cleanup()

    def install_dir(self) -> Path:
        return ai_model_store.bundle_install_dir(TEST_BUNDLE.key)

    def fake_downloader(
        self,
        *,
        content: dict[str, bytes] | None = None,
        fail_on: str = "",
        error: Exception | None = None,
    ):
        payloads = content if content is not None else CONTENT

        def downloader(*, url, destination, label, progress_callback, cancel_check):
            if cancel_check is not None and cancel_check():
                raise ai_model_store.ModelInstallCancelled("stopped")
            if fail_on and label == fail_on:
                raise error or ai_model_store.ModelInstallError(
                    "injected failure", category="network"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            data = payloads[label]
            destination.write_bytes(data)
            if progress_callback is not None:
                progress_callback(label, len(data), len(data))
            return len(data)

        return downloader


class InstallTests(_StoreTestCase):
    def test_install_stages_verifies_and_activates(self) -> None:
        status = ai_model_store.install_bundle(
            TEST_BUNDLE.key, downloader=self.fake_downloader()
        )

        self.assertTrue(status.is_ready)
        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), PAYLOAD)
        self.assertEqual(
            (self.install_dir() / "nested" / "config.json").read_bytes(), SECOND
        )
        metadata = ai_model_store.read_bundle_metadata(self.install_dir())
        self.assertEqual(metadata["revision"], TEST_BUNDLE.revision)
        self.assertEqual(metadata["manifest_version"], ai_manifest.AI_MANIFEST_VERSION)

    def test_install_leaves_nothing_staged_behind(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        staging = ai_paths.managed_staging_root()
        leftovers = [
            child for child in staging.iterdir() if child.is_dir() and TEST_BUNDLE.key in child.name
        ]
        self.assertEqual(leftovers, [])

    def test_a_failed_install_never_touches_the_live_directory(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        good = (self.install_dir() / "weights.bin").read_bytes()

        with self.assertRaises(ai_model_store.ModelInstallError):
            ai_model_store.install_bundle(
                TEST_BUNDLE.key,
                force=True,
                downloader=self.fake_downloader(fail_on="nested/config.json"),
            )

        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), good)
        self.assertTrue(ai_model_store.bundle_status(TEST_BUNDLE.key).is_ready)

    def test_install_is_skipped_when_the_bundle_is_already_ready(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        calls: list[str] = []

        def counting(*, url, destination, label, progress_callback, cancel_check):
            calls.append(label)
            raise AssertionError("should not download again")

        status = ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=counting)
        self.assertTrue(status.is_ready)
        self.assertEqual(calls, [])

    def test_cancellation_removes_the_staged_generation(self) -> None:
        with self.assertRaises(ai_model_store.ModelInstallCancelled):
            ai_model_store.install_bundle(
                TEST_BUNDLE.key,
                cancel_check=lambda: True,
                downloader=self.fake_downloader(),
            )

        self.assertFalse(self.install_dir().exists())
        staging = ai_paths.managed_staging_root()
        if staging.exists():
            self.assertEqual([c for c in staging.iterdir() if c.is_dir()], [])

    def test_disk_preflight_refuses_before_downloading(self) -> None:
        huge = ModelBundle(**{**TEST_BUNDLE.__dict__, "approx_mb": 1 << 40})
        with patch.dict(ai_manifest.MODEL_BUNDLES, {TEST_BUNDLE.key: huge}, clear=False):
            with self.assertRaises(ai_model_store.ModelInstallError) as caught:
                ai_model_store.install_bundle(
                    TEST_BUNDLE.key, downloader=self.fake_downloader()
                )

        self.assertEqual(caught.exception.category, "disk")
        self.assertFalse(self.install_dir().exists())

    def test_abandoned_staging_from_an_interrupted_install_is_cleaned_up(self) -> None:
        staging = ai_paths.managed_staging_root()
        abandoned = staging / f"{TEST_BUNDLE.key}-deadbeef0000"
        abandoned.mkdir(parents=True)
        (abandoned / "partial.bin").write_bytes(b"half")

        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())

        self.assertFalse(abandoned.exists())


class ActivationFailureTests(_StoreTestCase):
    """The swap window between the two renames must never lose the bundle."""

    def _install_once(self) -> bytes:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        return (self.install_dir() / "weights.bin").read_bytes()

    def test_a_failure_between_the_two_renames_restores_the_previous_bundle(self) -> None:
        good = self._install_once()
        real_replace = ai_model_store._replace_with_retry
        calls: list[int] = []

        def flaky(source, target, *, attempts=8):
            calls.append(1)
            # First call retires the live bundle; fail the second, which is the
            # one that would move the new generation into place.
            if len(calls) == 2:
                raise ai_model_store.ModelInstallError("injected", category="locked")
            return real_replace(source, target, attempts=attempts)

        with patch.object(ai_model_store, "_replace_with_retry", flaky):
            with self.assertRaises(ai_model_store.ModelInstallError):
                ai_model_store.install_bundle(
                    TEST_BUNDLE.key, force=True, downloader=self.fake_downloader()
                )

        self.assertTrue(self.install_dir().is_dir(), "the live bundle was destroyed")
        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), good)
        self.assertTrue(ai_model_store.bundle_status(TEST_BUNDLE.key).is_ready)

    def test_an_interrupted_activation_is_recovered_on_the_next_run(self) -> None:
        # Simulate a process killed between the two renames: the live directory
        # is gone and only the journal plus the staged/retired copies remain.
        self._install_once()
        staging = ai_paths.managed_staging_root()
        staging.mkdir(parents=True, exist_ok=True)
        retired = staging / f"{TEST_BUNDLE.key}-retired-abc123"
        os.replace(self.install_dir(), retired)
        staged = staging / f"{TEST_BUNDLE.key}-staged1"
        staged.mkdir()
        (staged / "nested").mkdir()
        (staged / "weights.bin").write_bytes(PAYLOAD)
        (staged / "nested" / "config.json").write_bytes(SECOND)
        ai_model_store._write_bundle_metadata(staged, TEST_BUNDLE.key)
        ai_model_store._write_json_atomic(
            ai_model_store.activation_journal_path(staging, TEST_BUNDLE.key),
            {
                "bundle": TEST_BUNDLE.key,
                "staged": str(staged),
                "install_dir": str(self.install_dir()),
                "retired": str(retired),
            },
        )
        self.assertFalse(self.install_dir().exists())

        recovered = ai_model_store.recover_interrupted_activations()

        self.assertIn(TEST_BUNDLE.key, recovered)
        self.assertTrue(ai_model_store.bundle_status(TEST_BUNDLE.key).is_ready)
        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), PAYLOAD)

    def test_recovery_falls_back_to_the_retired_copy_when_staging_is_gone(self) -> None:
        self._install_once()
        staging = ai_paths.managed_staging_root()
        retired = staging / f"{TEST_BUNDLE.key}-retired-def456"
        os.replace(self.install_dir(), retired)
        ai_model_store._write_json_atomic(
            ai_model_store.activation_journal_path(staging, TEST_BUNDLE.key),
            {
                "bundle": TEST_BUNDLE.key,
                "staged": str(staging / f"{TEST_BUNDLE.key}-never-written"),
                "install_dir": str(self.install_dir()),
                "retired": str(retired),
            },
        )

        ai_model_store.recover_interrupted_activations()

        self.assertTrue(self.install_dir().is_dir())
        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), PAYLOAD)

    def test_staging_cleanup_never_deletes_a_journalled_generation(self) -> None:
        # This is what turned a failed swap into data loss: the retired copy was
        # the only healthy one, and the next cleanup removed it.
        staging = ai_paths.managed_staging_root()
        staging.mkdir(parents=True, exist_ok=True)
        retired = staging / f"{TEST_BUNDLE.key}-retired-999"
        retired.mkdir()
        (retired / "weights.bin").write_bytes(PAYLOAD)
        ai_model_store._write_json_atomic(
            ai_model_store.activation_journal_path(staging, TEST_BUNDLE.key),
            {
                "bundle": TEST_BUNDLE.key,
                "staged": "",
                "install_dir": str(self.install_dir()),
                "retired": str(retired),
            },
        )

        ai_model_store._clean_abandoned_staging(staging, TEST_BUNDLE.key)

        self.assertTrue(retired.is_dir(), "cleanup deleted a journalled generation")

    def test_recovery_preserves_every_generation_when_all_renames_fail(self) -> None:
        self._install_once()
        staging = ai_paths.managed_staging_root()
        retired = staging / f"{TEST_BUNDLE.key}-retired-locked"
        os.replace(self.install_dir(), retired)
        staged = staging / f"{TEST_BUNDLE.key}-staged-locked"
        staged.mkdir()
        (staged / "weights.bin").write_bytes(PAYLOAD)
        (staged / "nested").mkdir()
        (staged / "nested" / "config.json").write_bytes(SECOND)
        ai_model_store._write_bundle_metadata(staged, TEST_BUNDLE.key)
        journal = ai_model_store.activation_journal_path(staging, TEST_BUNDLE.key)
        ai_model_store._write_json_atomic(
            journal,
            {
                "bundle": TEST_BUNDLE.key,
                "staged": str(staged),
                "install_dir": str(self.install_dir()),
                "retired": str(retired),
            },
        )

        failure = ai_model_store.ModelInstallError("in use", category="locked")
        with patch.object(ai_model_store, "_replace_with_retry", side_effect=failure):
            recovered = ai_model_store.recover_interrupted_activations()

        self.assertEqual(recovered, ())
        self.assertTrue(staged.is_dir())
        self.assertTrue(retired.is_dir())
        self.assertTrue(journal.is_file())

    def test_invalid_journal_paths_are_never_used_for_cleanup(self) -> None:
        staging = ai_paths.managed_staging_root()
        staging.mkdir(parents=True, exist_ok=True)
        journal = ai_model_store.activation_journal_path(staging, TEST_BUNDLE.key)
        ai_model_store._write_json_atomic(
            journal,
            {
                "bundle": TEST_BUNDLE.key,
                "staged": "",
                "install_dir": str(self.install_dir()),
                "retired": ".",
            },
        )

        with patch.object(ai_model_store, "_remove_tree") as remove_tree:
            recovered = ai_model_store.recover_interrupted_activations()
            ai_model_store._clean_abandoned_staging(staging, TEST_BUNDLE.key)

        self.assertEqual(recovered, ())
        remove_tree.assert_not_called()
        self.assertTrue(journal.is_file())


class VerificationTests(_StoreTestCase):
    def test_truncated_download_is_rejected_by_size(self) -> None:
        truncated = dict(CONTENT, **{"weights.bin": PAYLOAD[:-4]})
        with self.assertRaises(ai_model_store.ModelInstallError) as caught:
            ai_model_store.install_bundle(
                TEST_BUNDLE.key, downloader=self.fake_downloader(content=truncated)
            )

        self.assertEqual(caught.exception.category, "truncated")
        self.assertFalse(self.install_dir().exists())

    def test_wrong_content_at_the_right_size_is_rejected_by_hash(self) -> None:
        swapped = dict(CONTENT, **{"weights.bin": b"X" * len(PAYLOAD)})
        with self.assertRaises(ai_model_store.ModelInstallError) as caught:
            ai_model_store.install_bundle(
                TEST_BUNDLE.key, downloader=self.fake_downloader(content=swapped)
            )

        self.assertEqual(caught.exception.category, "hash_mismatch")

    def test_empty_file_is_rejected(self) -> None:
        empty = dict(CONTENT, **{"nested/config.json": b""})
        with self.assertRaises(ai_model_store.ModelInstallError) as caught:
            ai_model_store.install_bundle(
                TEST_BUNDLE.key, downloader=self.fake_downloader(content=empty)
            )

        self.assertIn(caught.exception.category, {"incomplete", "truncated"})

    def test_a_missing_file_makes_the_bundle_partial_not_ready(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        (self.install_dir() / "weights.bin").unlink()

        status = ai_model_store.bundle_status(TEST_BUNDLE.key)
        self.assertEqual(status.state, ai_model_store.STATE_PARTIAL)
        self.assertFalse(status.is_ready)
        self.assertIn("weights.bin", status.missing_files)

    def test_a_shrunk_file_is_corrupt_even_though_it_exists(self) -> None:
        # This is the exact defect the old existence check missed.
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        (self.install_dir() / "weights.bin").write_bytes(PAYLOAD[:5])

        status = ai_model_store.bundle_status(TEST_BUNDLE.key)
        self.assertEqual(status.state, ai_model_store.STATE_CORRUPT)
        self.assertFalse(status.is_ready)

    def test_deep_verification_catches_a_same_size_substitution(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        (self.install_dir() / "weights.bin").write_bytes(b"Y" * len(PAYLOAD))

        self.assertTrue(ai_model_store.bundle_status(TEST_BUNDLE.key).is_ready)
        deep = ai_model_store.bundle_status(TEST_BUNDLE.key, deep=True)
        self.assertEqual(deep.state, ai_model_store.STATE_CORRUPT)

    def test_a_different_revision_is_stale_not_ready(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        moved = ModelBundle(**{**TEST_BUNDLE.__dict__, "revision": "b" * 40})
        with patch.dict(ai_manifest.MODEL_BUNDLES, {TEST_BUNDLE.key: moved}, clear=False):
            status = ai_model_store.bundle_status(TEST_BUNDLE.key)

        self.assertEqual(status.state, ai_model_store.STATE_STALE)
        self.assertFalse(status.is_ready)

    def test_metadata_from_a_newer_manifest_is_stale(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        with patch.object(ai_model_store, "AI_MANIFEST_VERSION", 999):
            status = ai_model_store.bundle_status(TEST_BUNDLE.key)

        self.assertEqual(status.state, ai_model_store.STATE_STALE)

    def test_a_legacy_directory_with_correct_sizes_is_adopted(self) -> None:
        # An upgrade must not force a multi-gigabyte re-download of files that
        # are already correct.
        target = self.install_dir()
        (target / "nested").mkdir(parents=True)
        (target / "weights.bin").write_bytes(PAYLOAD)
        (target / "nested" / "config.json").write_bytes(SECOND)

        status = ai_model_store.bundle_status(TEST_BUNDLE.key)

        self.assertTrue(status.is_ready)
        self.assertTrue(ai_model_store.read_bundle_metadata(target)["adopted"])


class RepairTests(_StoreTestCase):
    def test_repair_reinstalls_a_corrupt_bundle(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        (self.install_dir() / "weights.bin").write_bytes(b"Z" * len(PAYLOAD))

        with patch.object(ai_model_store, "_download_file", self.fake_downloader()):
            status = ai_model_store.repair_bundle(TEST_BUNDLE.key)

        self.assertTrue(status.is_ready)
        self.assertEqual((self.install_dir() / "weights.bin").read_bytes(), PAYLOAD)

    def test_repair_leaves_a_healthy_bundle_untouched(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())

        def refuse(**_kwargs):
            raise AssertionError("a healthy bundle must not be re-downloaded")

        with patch.object(ai_model_store, "_download_file", refuse):
            status = ai_model_store.repair_bundle(TEST_BUNDLE.key)

        self.assertTrue(status.is_ready)

    def test_uninstall_removes_the_bundle(self) -> None:
        ai_model_store.install_bundle(TEST_BUNDLE.key, downloader=self.fake_downloader())
        self.assertTrue(ai_model_store.uninstall_bundle(TEST_BUNDLE.key))
        self.assertEqual(
            ai_model_store.bundle_status(TEST_BUNDLE.key).state, ai_model_store.STATE_MISSING
        )


class LockingTests(_StoreTestCase):
    def test_a_second_installer_is_rejected_while_one_holds_the_lock(self) -> None:
        started = threading.Event()
        release = threading.Event()
        errors: list[Exception] = []

        def holder() -> None:
            with ai_model_store.bundle_lock(TEST_BUNDLE.key):
                started.set()
                release.wait(5)

        thread = threading.Thread(target=holder)
        thread.start()
        try:
            self.assertTrue(started.wait(5))
            with self.assertRaises(ai_model_store.ModelInstallError) as caught:
                ai_model_store.install_bundle(
                    TEST_BUNDLE.key, downloader=self.fake_downloader()
                )
            self.assertEqual(caught.exception.category, "locked")
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(errors, [])

    def test_the_lock_is_reusable_after_release(self) -> None:
        with ai_model_store.bundle_lock(TEST_BUNDLE.key):
            pass
        status = ai_model_store.install_bundle(
            TEST_BUNDLE.key, downloader=self.fake_downloader()
        )
        self.assertTrue(status.is_ready)


class TransportTests(unittest.TestCase):
    """Error classification for the download transport."""

    def test_html_response_is_reported_as_interception(self) -> None:
        class _Response:
            status = 200
            headers = {"Content-Type": "text/html; charset=utf-8", "Content-Length": "12"}

            def read(self, _size):
                return b""

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("urllib.request.urlopen", return_value=_Response()):
                with self.assertRaises(ai_model_store.ModelInstallError) as caught:
                    ai_model_store._fetch(
                        url="https://example.invalid/model.bin",
                        partial=Path(temp_dir) / "model.bin.part",
                        label="model.bin",
                        resume_from=0,
                        progress_callback=None,
                        cancel_check=None,
                    )

        self.assertEqual(caught.exception.category, "intercepted")

    def test_certificate_failure_is_named(self) -> None:
        import ssl

        error = urllib.error.URLError(ssl.SSLCertVerificationError("self signed certificate"))
        result = ai_model_store._url_error("model.bin", error)
        self.assertEqual(result.category, "certificate")
        self.assertIn("proxy", str(result).lower())

    def test_dns_failure_is_named(self) -> None:
        error = urllib.error.URLError("[Errno 11001] getaddrinfo failed")
        self.assertEqual(ai_model_store._url_error("model.bin", error).category, "dns")

    def test_http_status_codes_map_to_categories(self) -> None:
        cases = {401: "auth", 403: "auth", 404: "not_found", 429: "rate_limit", 500: "network"}
        for code, expected in cases.items():
            error = urllib.error.HTTPError(
                "https://example.invalid", code, "reason", {}, None
            )
            with self.subTest(code=code):
                self.assertEqual(ai_model_store._http_error("f", error).category, expected)

    def test_non_https_urls_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaises(ai_model_store.ModelInstallError) as caught:
                ai_model_store._download_file(
                    url="http://example.invalid/model.bin",
                    destination=Path(temp_dir) / "model.bin",
                    label="model.bin",
                    progress_callback=None,
                    cancel_check=None,
                )
        self.assertEqual(caught.exception.category, "manifest")

    def test_unrecoverable_errors_are_not_retried(self) -> None:
        attempts: list[int] = []

        def failing(**_kwargs):
            attempts.append(1)
            raise ai_model_store.ModelInstallError("gone", category="not_found")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(ai_model_store, "_fetch", failing):
                with self.assertRaises(ai_model_store.ModelInstallError):
                    ai_model_store._download_file(
                        url="https://example.invalid/model.bin",
                        destination=Path(temp_dir) / "model.bin",
                        label="model.bin",
                        progress_callback=None,
                        cancel_check=None,
                    )

        self.assertEqual(len(attempts), 1)

    def test_transient_errors_are_retried_then_give_up(self) -> None:
        attempts: list[int] = []

        def failing(**_kwargs):
            attempts.append(1)
            raise ai_model_store.ModelInstallError("reset", category="network")

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(ai_model_store, "_fetch", failing), patch.object(
                ai_model_store, "DOWNLOAD_BACKOFF_SECONDS", 0
            ):
                with self.assertRaises(ai_model_store.ModelInstallError):
                    ai_model_store._download_file(
                        url="https://example.invalid/model.bin",
                        destination=Path(temp_dir) / "model.bin",
                        label="model.bin",
                        progress_callback=None,
                        cancel_check=None,
                    )

        self.assertEqual(len(attempts), ai_model_store.DOWNLOAD_ATTEMPTS)


class ManifestIntegrityTests(unittest.TestCase):
    """The shipped manifest itself must stay verifiable."""

    def test_every_bundle_file_has_a_size_and_hash(self) -> None:
        for key, bundle in ai_manifest.MODEL_BUNDLES.items():
            digests = ai_manifest.bundle_digests(key)
            for filename in bundle.filenames:
                with self.subTest(bundle=key, file=filename):
                    digest = digests.get(filename)
                    self.assertIsNotNone(digest, f"{key}/{filename} has no digest")
                    self.assertGreater(digest.size, 0)
                    self.assertEqual(len(digest.sha256), 64)

    def test_no_bundle_is_left_unverified(self) -> None:
        self.assertEqual(ai_manifest.unverified_bundles(), ())

    def test_every_revision_is_an_immutable_commit(self) -> None:
        self.assertEqual(ai_manifest.unpinned_bundles(), ())

    def test_every_capability_references_known_bundles(self) -> None:
        for key, capability in ai_manifest.CAPABILITIES.items():
            for bundle_key in capability.model_bundles:
                with self.subTest(capability=key):
                    self.assertIn(bundle_key, ai_manifest.MODEL_BUNDLES)

    def test_install_parts_are_unique_per_bundle(self) -> None:
        seen: dict[tuple[str, ...], str] = {}
        for key, bundle in ai_manifest.MODEL_BUNDLES.items():
            self.assertNotIn(
                bundle.install_parts,
                seen,
                msg=f"{key} shares a directory with {seen.get(bundle.install_parts)}",
            )
            seen[bundle.install_parts] = key


if __name__ == "__main__":
    unittest.main()
