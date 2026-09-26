from __future__ import annotations

from unittest.mock import patch

from image_triage.updater import UpdateCheckResult, UpdateInfo


def _result(sha256: str) -> UpdateCheckResult:
    info = UpdateInfo(
        version="9.9.9",
        installer_url="https://example.test/ImageTriage-9.9.9.msi",
        release_notes_url="https://example.test/notes",
        sha256=sha256,
    )
    return UpdateCheckResult(current_version="1.0.0", latest=info, update_available=True, feed_url="x")


def test_an_update_without_a_checksum_is_refused_with_an_explanation(main_window, dialogs) -> None:
    with patch.object(main_window, "_download_update_installer") as download:
        main_window._prompt_for_update_download(_result(""))

    download.assert_not_called()
    assert any(title == "Update Cannot Be Verified" for _, title, _ in dialogs.messages)


def test_a_verifiable_update_is_offered_and_downloads_when_accepted(main_window, dialogs) -> None:
    with patch.object(main_window, "_download_update_installer") as download:
        main_window._prompt_for_update_download(_result("a" * 64))

    download.assert_called_once()
    assert any(title == "Update Available" for _, title, _ in dialogs.messages)
