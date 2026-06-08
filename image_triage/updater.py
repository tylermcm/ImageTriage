from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from ._version import __version__


DEFAULT_UPDATE_FEED_URL = "https://api.github.com/repos/tylermcm/ImageTriage/releases/latest"
UPDATE_FEED_URL_ENV = "IMAGE_TRIAGE_UPDATE_FEED_URL"
UPDATER_USER_AGENT = f"ImageTriage/{__version__} Updater"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024

DownloadProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    installer_url: str
    release_notes_url: str = ""
    sha256: str = ""
    title: str = ""
    summary: str = ""

    @property
    def installer_filename(self) -> str:
        parsed = urlparse(self.installer_url)
        name = unquote(Path(parsed.path).name)
        return name or f"ImageTriage-{self.version}.msi"


@dataclass(frozen=True)
class UpdateCheckResult:
    current_version: str
    latest: UpdateInfo
    update_available: bool
    feed_url: str


def current_app_version() -> str:
    try:
        return importlib_metadata.version("image-triage")
    except importlib_metadata.PackageNotFoundError:
        return __version__


def resolve_update_feed_url(feed_url: str | None = None) -> str:
    if feed_url is not None:
        return feed_url.strip()
    return os.environ.get(UPDATE_FEED_URL_ENV, "").strip() or DEFAULT_UPDATE_FEED_URL


def check_for_update(*, current_version: str | None = None, feed_url: str | None = None) -> UpdateCheckResult:
    resolved_feed_url = resolve_update_feed_url(feed_url)
    if not resolved_feed_url:
        raise ValueError(
            f"No update feed is configured. Set {UPDATE_FEED_URL_ENV} or configure DEFAULT_UPDATE_FEED_URL."
        )
    latest = fetch_update_info(resolved_feed_url)
    resolved_current = current_version or current_app_version()
    return UpdateCheckResult(
        current_version=resolved_current,
        latest=latest,
        update_available=is_newer_version(latest.version, resolved_current),
        feed_url=resolved_feed_url,
    )


def fetch_update_info(feed_url: str | None = None) -> UpdateInfo:
    resolved_feed_url = resolve_update_feed_url(feed_url)
    payload = _fetch_json(resolved_feed_url)
    if isinstance(payload, dict) and "assets" in payload and "tag_name" in payload:
        return _update_info_from_github_release(payload)
    if isinstance(payload, dict):
        return _update_info_from_manifest(payload)
    raise ValueError("Update feed did not return a JSON object.")


def is_newer_version(candidate: str, current: str) -> bool:
    candidate_parts = _version_parts(candidate)
    current_parts = _version_parts(current)
    width = max(len(candidate_parts), len(current_parts))
    candidate_parts = candidate_parts + (0,) * (width - len(candidate_parts))
    current_parts = current_parts + (0,) * (width - len(current_parts))
    return candidate_parts > current_parts


def download_update_installer(
    update: UpdateInfo,
    *,
    destination_dir: str | Path | None = None,
    progress_callback: DownloadProgressCallback | None = None,
) -> Path:
    destination_root = Path(destination_dir) if destination_dir is not None else Path(tempfile.gettempdir()) / "ImageTriageUpdates"
    destination_root.mkdir(parents=True, exist_ok=True)
    filename = _safe_filename(update.installer_filename)
    if not filename.lower().endswith(".msi"):
        filename = f"ImageTriage-{update.version}.msi"
    destination = destination_root / filename
    temp_destination = destination.with_name(f"{destination.name}.download")

    request = urllib.request.Request(update.installer_url, headers={"User-Agent": UPDATER_USER_AGENT})
    bytes_read = 0
    try:
        with urllib.request.urlopen(request, timeout=120) as response, temp_destination.open("wb") as handle:
            total_bytes = _response_content_length(response)
            while True:
                chunk = response.read(DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                bytes_read += len(chunk)
                if progress_callback is not None:
                    progress_callback(bytes_read, total_bytes, filename)
    except urllib.error.HTTPError as exc:
        _remove_partial_download(temp_destination)
        raise RuntimeError(f"Update download failed with HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        _remove_partial_download(temp_destination)
        raise RuntimeError(f"Update download failed: {exc.reason}") from exc
    except OSError:
        _remove_partial_download(temp_destination)
        raise

    if update.sha256:
        digest = _sha256_file(temp_destination)
        expected = _normalize_sha256(update.sha256)
        if digest.casefold() != expected.casefold():
            _remove_partial_download(temp_destination)
            raise RuntimeError(f"Update checksum mismatch. Expected {expected}, got {digest}.")

    temp_destination.replace(destination)
    return destination


def launch_update_installer(installer_path: str | Path, *, passive: bool = True) -> subprocess.Popen:
    path = Path(installer_path)
    if not path.exists():
        raise FileNotFoundError(f"Installer not found: {path}")
    if path.suffix.casefold() != ".msi":
        raise ValueError(f"Update installer must be an MSI file: {path}")
    if os.name != "nt" and sys.platform != "win32":
        raise OSError("MSI updates are only supported on Windows.")

    command = ["msiexec.exe", "/i", str(path)]
    if passive:
        command.extend(["/passive", "/norestart"])
    return subprocess.Popen(command)


def _fetch_json(url: str) -> object:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json, application/json",
            "User-Agent": UPDATER_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Update check failed with HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Update check failed: {exc.reason}") from exc
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Update feed returned invalid JSON.") from exc


def _update_info_from_manifest(payload: dict[str, object]) -> UpdateInfo:
    installer_payload = payload.get("installer")
    installer = installer_payload if isinstance(installer_payload, dict) else {}
    version = str(payload.get("version") or payload.get("tag_name") or "").strip().lstrip("v")
    installer_url = str(
        payload.get("installer_url")
        or payload.get("msi_url")
        or installer.get("url")
        or payload.get("url")
        or ""
    ).strip()
    if not version:
        raise ValueError("Update feed is missing a version.")
    if not installer_url:
        raise ValueError("Update feed is missing an installer URL.")
    return UpdateInfo(
        version=version,
        installer_url=installer_url,
        release_notes_url=str(payload.get("release_notes_url") or payload.get("html_url") or "").strip(),
        sha256=_normalize_sha256(str(payload.get("sha256") or payload.get("installer_sha256") or installer.get("sha256") or "")),
        title=str(payload.get("title") or payload.get("name") or "").strip(),
        summary=str(payload.get("summary") or payload.get("body") or "").strip(),
    )


def _update_info_from_github_release(payload: dict[str, object]) -> UpdateInfo:
    version = str(payload.get("tag_name") or "").strip().lstrip("v")
    if not version:
        raise ValueError("GitHub release is missing a tag name.")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise ValueError("GitHub release is missing release assets.")
    msi_asset = _select_msi_asset(assets)
    if msi_asset is None:
        raise ValueError("GitHub release does not include an MSI asset.")
    installer_url = str(msi_asset.get("browser_download_url") or "").strip()
    if not installer_url:
        raise ValueError("GitHub release MSI asset is missing a download URL.")
    return UpdateInfo(
        version=version,
        installer_url=installer_url,
        release_notes_url=str(payload.get("html_url") or "").strip(),
        sha256=_asset_sha256(msi_asset),
        title=str(payload.get("name") or "").strip(),
        summary=str(payload.get("body") or "").strip(),
    )


def _select_msi_asset(assets: list[object]) -> dict[str, object] | None:
    candidates: list[dict[str, object]] = []
    for raw_asset in assets:
        if not isinstance(raw_asset, dict):
            continue
        name = str(raw_asset.get("name") or "").strip()
        if name.casefold().endswith(".msi"):
            candidates.append(raw_asset)
    if not candidates:
        return None
    candidates.sort(key=lambda item: ("image" not in str(item.get("name") or "").casefold(), str(item.get("name") or "")))
    return candidates[0]


def _asset_sha256(asset: dict[str, object]) -> str:
    digest = str(asset.get("digest") or "").strip()
    if digest.casefold().startswith("sha256:"):
        return _normalize_sha256(digest)
    return ""


def _version_parts(version: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", str(version)))
    return parts or (0,)


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", filename).strip(" .")
    return cleaned or "ImageTriage.msi"


def _normalize_sha256(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized.casefold().startswith("sha256:"):
        normalized = normalized.split(":", 1)[1].strip()
    return normalized


def _response_content_length(response: object) -> int:
    headers = getattr(response, "headers", None)
    if headers is None:
        return 0
    try:
        value = headers.get("Content-Length", "0")
    except TypeError:
        value = headers.get("Content-Length") or "0"
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _remove_partial_download(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
