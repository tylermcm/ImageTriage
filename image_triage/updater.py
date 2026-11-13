from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

from ._version import __version__


DEFAULT_UPDATE_FEED_URL = "https://api.github.com/repos/tylermcm/ImageTriage/releases/latest"
UPDATE_FEED_URL_ENV = "IMAGE_TRIAGE_UPDATE_FEED_URL"
UPDATER_USER_AGENT = f"ImageTriage/{__version__} Updater"
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}")

DownloadProgressCallback = Callable[[int, int, str], None]

SIGNATURE_VALID = "valid"                    # signed, and Windows trusts the certificate chain
SIGNATURE_UNSIGNED = "unsigned"              # the file carries no Authenticode signature
SIGNATURE_INVALID = "invalid"                # signed, but the signature or its chain did not verify
SIGNATURE_NOT_APPLICABLE = "not_applicable"  # not Windows: there is no MSI to start and nothing that can check one


@dataclass(frozen=True)
class InstallerSignerPin:
    """Who may have signed an installer this application starts.

    ``subjects`` are publisher names as Windows shows them (the signing certificate's display name, compared
    without regard to case); ``thumbprints`` are certificate hashes in hex, SHA-1 (40 digits, what Windows and
    ``Get-AuthenticodeSignature`` show) or SHA-256 (64 digits). Every kind that is filled in must match, and
    within a kind any one entry is enough (so a renewed certificate can be listed beside the old one).
    """

    subjects: tuple[str, ...] = ()
    thumbprints: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self._subjects() or self._thumbprints())

    def _subjects(self) -> frozenset[str]:
        return frozenset(text.strip().casefold() for text in self.subjects if text and text.strip())

    def _thumbprints(self) -> frozenset[str]:
        return frozenset(_normalize_thumbprint(text) for text in self.thumbprints) - {""}

    def matches(self, signature: "InstallerSignature") -> bool:
        subjects, thumbprints = self._subjects(), self._thumbprints()
        if not subjects and not thumbprints:
            return False
        if subjects and signature.subject.strip().casefold() not in subjects:
            return False
        found = {_normalize_thumbprint(signature.thumbprint_sha1), _normalize_thumbprint(signature.thumbprint_sha256)} - {""}
        if thumbprints and not thumbprints & found:
            return False
        return True


# The one place that says who signs Image Triage's installers.
#
# It is empty because the release build does not sign the MSI today (.github/workflows/build-windows-msi-release.yml
# has no signing step and setup_msi.py configures none). While it is empty the publisher of an update cannot be
# verified: the update is still held to its published SHA-256, an installer whose signature is present but broken
# is still refused, and the About page says that the publisher was not verified. Once releases are signed, put the
# publisher name and/or the certificate thumbprints here; from then on an unsigned installer, or one signed by
# anyone else, is refused.
TRUSTED_INSTALLER_SIGNERS = InstallerSignerPin(subjects=(), thumbprints=())


@dataclass(frozen=True)
class InstallerSignature:
    """What the Authenticode check found on one file."""

    status: str
    subject: str = ""
    thumbprint_sha1: str = ""
    thumbprint_sha256: str = ""
    detail: str = ""


@dataclass(frozen=True)
class PublisherCheck:
    """The outcome of the signer policy for an installer that may be started.

    ``verified`` is true only when the file is validly signed by the pinned publisher. ``notice`` is the sentence
    the About page shows whenever it is not.
    """

    verified: bool
    notice: str = ""
    signature: InstallerSignature = field(default_factory=lambda: InstallerSignature(SIGNATURE_NOT_APPLICABLE))


class InstallerSignerError(RuntimeError):
    """The installer's signature is broken, or it is not from the pinned publisher. It must not be started."""


SignatureVerifier = Callable[[Path], InstallerSignature]


@dataclass(frozen=True)
class UpdateInfo:
    version: str
    installer_url: str
    release_notes_url: str = ""
    sha256: str = ""
    title: str = ""
    summary: str = ""

    @property
    def is_verifiable(self) -> bool:
        """Only an update with a well-formed published SHA-256 may be installed."""
        return bool(_SHA256_PATTERN.fullmatch(_normalize_sha256(self.sha256)))

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
    _require_https(resolved_feed_url, "The update feed")
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
    if not update.is_verifiable:
        raise RuntimeError(
            "This update does not publish a valid SHA-256 checksum, so it cannot be verified "
            "and will not be downloaded or installed."
        )
    _require_https(update.installer_url, "The update installer")
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
            _require_https_response(response, "The update installer")
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
    except http.client.HTTPException as exc:
        # A connection that drops part way raises IncompleteRead, which is not an OSError: without this the
        # half-written file stayed behind and the caller saw a bare traceback.
        _remove_partial_download(temp_destination)
        raise RuntimeError("Update download was interrupted before it finished. Try again.") from exc
    except BaseException:
        _remove_partial_download(temp_destination)
        raise

    digest = _sha256_file(temp_destination)
    expected = _normalize_sha256(update.sha256)
    if digest.casefold() != expected.casefold():
        _remove_partial_download(temp_destination)
        raise RuntimeError(f"Update checksum mismatch. Expected {expected}, got {digest}.")

    temp_destination.replace(destination)
    return destination


def verify_installer_checksum(installer_path: str | Path, expected_sha256: str) -> None:
    """Hash the installer again. Raises when it is not the file that was verified after the download.

    The file sits in a temporary folder between the download and the launch, so the check made when it was
    downloaded says nothing about what is there now.
    """
    expected = _normalize_sha256(expected_sha256)
    if not _SHA256_PATTERN.fullmatch(expected):
        raise RuntimeError("The update has no valid SHA-256 checksum, so the installer cannot be verified.")
    digest = _sha256_file(Path(installer_path))
    if digest.casefold() != expected.casefold():
        raise RuntimeError("The downloaded installer changed after it was verified. It was not started.")


def check_installer_publisher(
    installer_path: str | Path,
    *,
    verifier: SignatureVerifier | None = None,
    pin: InstallerSignerPin | None = None,
) -> PublisherCheck:
    """Apply the signer policy to one installer file. Raises InstallerSignerError when it must not be started.

    - A signature that is present but does not verify is always refused.
    - With a pinned publisher (TRUSTED_INSTALLER_SIGNERS): only a valid signature from that publisher passes; an
      unsigned file, another signer, or a system that cannot check signatures is refused.
    - With no pinned publisher (today's unsigned releases): the file is allowed, and the result says that its
      publisher was not verified. The caller must already have held it to the published SHA-256.

    ``verifier`` reads the signature (Windows' own check by default) and exists so the policy can be tested.
    """
    path = Path(installer_path)
    pin = TRUSTED_INSTALLER_SIGNERS if pin is None else pin
    signature = (verifier or read_authenticode_signature)(path)
    status = signature.status
    if status == SIGNATURE_INVALID:
        reason = f" ({signature.detail})" if signature.detail else ""
        raise InstallerSignerError(f"The update installer's digital signature is not valid{reason}. It was not started.")
    if status == SIGNATURE_VALID:
        signer = signature.subject or "an unnamed publisher"
        if not pin.configured:
            return PublisherCheck(
                False,
                f"The installer is signed by {signer}, but this version of Image Triage has no publisher to compare "
                "that with, so the publisher was not verified. Its checksum matched the published one.",
                signature,
            )
        if pin.matches(signature):
            return PublisherCheck(True, "", signature)
        raise InstallerSignerError(
            f"The update installer is signed by {signer}, not by the publisher of Image Triage. It was not started."
        )
    if status == SIGNATURE_UNSIGNED:
        if pin.configured:
            raise InstallerSignerError(
                "The update installer is not digitally signed, and Image Triage's installers are. It was not started."
            )
        return PublisherCheck(
            False,
            "The installer is not digitally signed, so its publisher could not be verified. "
            "Its checksum matched the published one.",
            signature,
        )
    if status == SIGNATURE_NOT_APPLICABLE:
        if pin.configured:
            raise InstallerSignerError("The update installer's publisher cannot be checked on this system. It was not started.")
        return PublisherCheck(False, "Installer signatures are only checked on Windows.", signature)
    raise InstallerSignerError("The update installer's digital signature could not be checked. It was not started.")


def launch_update_installer(
    installer_path: str | Path,
    *,
    passive: bool = True,
    expected_sha256: str | None = None,
    signature_verifier: SignatureVerifier | None = None,
    signer_pin: InstallerSignerPin | None = None,
) -> subprocess.Popen:
    path = Path(installer_path)
    if not path.exists():
        raise FileNotFoundError(f"Installer not found: {path}")
    if path.suffix.casefold() != ".msi":
        raise ValueError(f"Update installer must be an MSI file: {path}")
    if os.name != "nt" and sys.platform != "win32":
        raise OSError("MSI updates are only supported on Windows.")
    if expected_sha256 is not None:
        verify_installer_checksum(path, expected_sha256)
    # the same file that was just hashed, and the one handed to msiexec below: nothing is fetched again in between
    check_installer_publisher(path, verifier=signature_verifier, pin=signer_pin)

    command = ["msiexec.exe", "/i", str(path)]
    if passive:
        command.extend(["/passive", "/norestart"])
    return subprocess.Popen(command)


def launch_update_installer_and_restart(
    installer_path: str | Path,
    *,
    app_executable: str | Path | None = None,
    current_pid: int | None = None,
    expected_sha256: str | None = None,
    signature_verifier: SignatureVerifier | None = None,
    signer_pin: InstallerSignerPin | None = None,
) -> subprocess.Popen:
    path = Path(installer_path)
    if not path.exists():
        raise FileNotFoundError(f"Installer not found: {path}")
    if path.suffix.casefold() != ".msi":
        raise ValueError(f"Update installer must be an MSI file: {path}")
    if os.name != "nt" and sys.platform != "win32":
        raise OSError("MSI updates are only supported on Windows.")

    executable = Path(app_executable) if app_executable is not None else Path(sys.executable)
    if not executable.exists():
        raise FileNotFoundError(f"Application executable not found: {executable}")
    if expected_sha256 is not None:
        verify_installer_checksum(path, expected_sha256)
    # the same file that was just hashed, and the one the handoff installs: nothing is fetched again in between
    check_installer_publisher(path, verifier=signature_verifier, pin=signer_pin)

    command = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        _build_update_handoff_command(path, executable, int(current_pid or os.getpid())),
    ]
    kwargs: dict[str, object] = {}
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(command, **kwargs)


def _build_update_handoff_command(installer_path: Path, app_executable: Path, current_pid: int) -> str:
    installer = _powershell_quote(str(installer_path))
    executable = _powershell_quote(str(app_executable))
    pid = max(0, int(current_pid))
    return "\n".join(
        [
            "$ErrorActionPreference = 'Stop'",
            f"$imageTriagePid = {pid}",
            f"$installer = {installer}",
            f"$app = {executable}",
            "if ($imageTriagePid -gt 0) {",
            "    try { Wait-Process -Id $imageTriagePid -Timeout 180 -ErrorAction SilentlyContinue } catch {}",
            "}",
            "$msiArgs = @('/i', ('\"' + $installer + '\"'), '/qn', '/norestart') -join ' '",
            "$msi = Start-Process -FilePath 'msiexec.exe' -ArgumentList $msiArgs -Wait -PassThru",
            "Start-Process -FilePath $app",
            "exit $msi.ExitCode",
        ]
    )


def _powershell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _require_https(url: str, what: str) -> None:
    """Refuse anything but https: the feed names the installer and its checksum, so a plain-http answer could name both."""
    if urlparse(str(url or "").strip()).scheme.casefold() != "https":
        raise ValueError(f"{what} must use https.")


def _require_https_response(response: object, what: str) -> None:
    """The address that finally answered must be https too (a redirect can leave it)."""
    geturl = getattr(response, "geturl", None)
    final_url = geturl() if callable(geturl) else ""
    if final_url:
        _require_https(final_url, what)


def _fetch_json(url: str) -> object:
    _require_https(url, "The update feed")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json, application/json",
            "User-Agent": UPDATER_USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            _require_https_response(response, "The update feed")
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Update check failed with HTTP {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Update check failed: {exc.reason}") from exc
    except http.client.HTTPException as exc:
        raise RuntimeError("Update check was interrupted before it finished.") from exc
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


def _normalize_thumbprint(value: str) -> str:
    """Hex digits only, lower case; "" for anything that is not a SHA-1 or SHA-256 hash."""
    digits = re.sub(r"[\s:]", "", str(value or "")).casefold()
    return digits if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", digits) else ""


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


# ---- reading the Authenticode signature (Windows only) ----
_TRUST_E_NOSIGNATURE = 0x800B0100
_TRUST_E_PROVIDER_UNKNOWN = 0x800B0001
_TRUST_E_SUBJECT_FORM_UNKNOWN = 0x800B0003
_TRUST_FAILURES = {
    0x80096010: "the file was changed after it was signed",
    0x800B0101: "the signing certificate has expired",
    0x800B0109: "the signing certificate does not lead to a trusted root",
    0x800B010A: "the signing certificate chain could not be built",
    0x800B010C: "the signing certificate was revoked",
    0x800B010E: "the revocation status of the signing certificate could not be checked",
    0x800B0111: "the signing certificate is explicitly distrusted on this PC",
    0x800B0004: "the signature does not belong to this file",
}


def _classify_trust_result(result: int, last_error: int) -> tuple[str, str]:
    """Turn WinVerifyTrust's answer into (status, detail).

    Only "there is no signature at all" counts as unsigned. WinVerifyTrust also answers TRUST_E_NOSIGNATURE for a
    signature it could not read or verify, and says which through the last error, so anything else is invalid.
    """
    result &= 0xFFFFFFFF
    last_error &= 0xFFFFFFFF
    if result == 0:
        return SIGNATURE_VALID, ""
    if result == _TRUST_E_NOSIGNATURE and last_error in (
        _TRUST_E_NOSIGNATURE,
        _TRUST_E_SUBJECT_FORM_UNKNOWN,
        _TRUST_E_PROVIDER_UNKNOWN,
    ):
        return SIGNATURE_UNSIGNED, ""
    code = last_error if result == _TRUST_E_NOSIGNATURE and last_error else result
    return SIGNATURE_INVALID, _TRUST_FAILURES.get(code, f"Windows error 0x{code:08X}")


def read_authenticode_signature(installer_path: str | Path) -> InstallerSignature:
    """The Authenticode signature of a file, as Windows judges it. Not applicable on any other system."""
    if os.name != "nt":
        return InstallerSignature(SIGNATURE_NOT_APPLICABLE)
    try:
        return _windows_authenticode_signature(Path(installer_path))
    except Exception as exc:  # a check that could not be made is not a pass
        return InstallerSignature(SIGNATURE_INVALID, detail=f"the signature could not be checked: {exc}")


def _windows_authenticode_signature(path: Path) -> InstallerSignature:  # pragma: no cover - Windows only
    """WinVerifyTrust for the signature and its chain, then the signing certificate out of the verified state."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class WINTRUST_FILE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pcwszFilePath", wintypes.LPCWSTR),
            ("hFile", wintypes.HANDLE),
            ("pgKnownSubject", ctypes.c_void_p),
        ]

    class WINTRUST_DATA(ctypes.Structure):
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pPolicyCallbackData", ctypes.c_void_p),
            ("pSIPClientData", ctypes.c_void_p),
            ("dwUIChoice", wintypes.DWORD),
            ("fdwRevocationChecks", wintypes.DWORD),
            ("dwUnionChoice", wintypes.DWORD),
            ("pFile", ctypes.POINTER(WINTRUST_FILE_INFO)),
            ("dwStateAction", wintypes.DWORD),
            ("hWVTStateData", wintypes.HANDLE),
            ("pwszURLReference", wintypes.LPCWSTR),
            ("dwProvFlags", wintypes.DWORD),
            ("dwUIContext", wintypes.DWORD),
            ("pSignatureSettings", ctypes.c_void_p),
        ]

    class CERT_CONTEXT(ctypes.Structure):
        _fields_ = [
            ("dwCertEncodingType", wintypes.DWORD),
            ("pbCertEncoded", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbCertEncoded", wintypes.DWORD),
            ("pCertInfo", ctypes.c_void_p),
            ("hCertStore", ctypes.c_void_p),
        ]

    class CRYPT_PROVIDER_CERT(ctypes.Structure):  # only the leading fields; the rest is not read
        _fields_ = [
            ("cbStruct", wintypes.DWORD),
            ("pCert", ctypes.POINTER(CERT_CONTEXT)),
        ]

    WTD_UI_NONE, WTD_REVOKE_WHOLECHAIN, WTD_CHOICE_FILE = 2, 1, 1
    WTD_STATEACTION_VERIFY, WTD_STATEACTION_CLOSE = 1, 2
    WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT = 0x80
    CERT_NAME_SIMPLE_DISPLAY_TYPE = 4

    wintrust = ctypes.WinDLL("wintrust", use_last_error=True)
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    wintrust.WinVerifyTrust.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), ctypes.POINTER(WINTRUST_DATA)]
    wintrust.WinVerifyTrust.restype = wintypes.LONG
    wintrust.WTHelperProvDataFromStateData.argtypes = [wintypes.HANDLE]
    wintrust.WTHelperProvDataFromStateData.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvSignerFromChain.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    wintrust.WTHelperGetProvSignerFromChain.restype = ctypes.c_void_p
    wintrust.WTHelperGetProvCertFromChain.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    wintrust.WTHelperGetProvCertFromChain.restype = ctypes.POINTER(CRYPT_PROVIDER_CERT)
    crypt32.CertGetNameStringW.argtypes = [
        ctypes.POINTER(CERT_CONTEXT), wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.LPWSTR, wintypes.DWORD,
    ]
    crypt32.CertGetNameStringW.restype = wintypes.DWORD

    # WINTRUST_ACTION_GENERIC_VERIFY_V2: the Authenticode policy
    action = GUID(0x00AAC56B, 0xCD44, 0x11D0, (ctypes.c_ubyte * 8)(0x8C, 0xC2, 0x00, 0xC0, 0x4F, 0xC2, 0x95, 0xEE))
    file_info = WINTRUST_FILE_INFO(ctypes.sizeof(WINTRUST_FILE_INFO), str(path), None, None)
    data = WINTRUST_DATA()
    data.cbStruct = ctypes.sizeof(WINTRUST_DATA)
    data.dwUIChoice = WTD_UI_NONE
    data.fdwRevocationChecks = WTD_REVOKE_WHOLECHAIN
    data.dwUnionChoice = WTD_CHOICE_FILE
    data.pFile = ctypes.pointer(file_info)
    data.dwStateAction = WTD_STATEACTION_VERIFY
    data.dwProvFlags = WTD_REVOCATION_CHECK_CHAIN_EXCLUDE_ROOT
    no_window = wintypes.HANDLE(-1)  # INVALID_HANDLE_VALUE: there is nobody to show a dialog to

    ctypes.set_last_error(0)
    result = wintrust.WinVerifyTrust(no_window, ctypes.byref(action), ctypes.byref(data))
    last_error = ctypes.get_last_error()
    try:
        status, detail = _classify_trust_result(result, last_error)
        if status != SIGNATURE_VALID:
            return InstallerSignature(status, detail=detail)
        provider = wintrust.WTHelperProvDataFromStateData(data.hWVTStateData)
        signer = wintrust.WTHelperGetProvSignerFromChain(provider, 0, False, 0) if provider else None
        provider_cert = wintrust.WTHelperGetProvCertFromChain(signer, 0) if signer else None
        if not provider_cert or not provider_cert.contents.pCert:
            return InstallerSignature(SIGNATURE_INVALID, detail="the signing certificate could not be read")
        certificate = provider_cert.contents.pCert
        encoded = ctypes.string_at(certificate.contents.pbCertEncoded, certificate.contents.cbCertEncoded)
        length = crypt32.CertGetNameStringW(certificate, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, None, 0)
        name = ctypes.create_unicode_buffer(max(1, length))
        crypt32.CertGetNameStringW(certificate, CERT_NAME_SIMPLE_DISPLAY_TYPE, 0, None, name, max(1, length))
        return InstallerSignature(
            SIGNATURE_VALID,
            subject=name.value.strip(),
            thumbprint_sha1=hashlib.sha1(encoded).hexdigest(),  # an identifier Windows shows, not relied on for integrity
            thumbprint_sha256=hashlib.sha256(encoded).hexdigest(),
        )
    finally:
        data.dwStateAction = WTD_STATEACTION_CLOSE
        wintrust.WinVerifyTrust(no_window, ctypes.byref(action), ctypes.byref(data))
