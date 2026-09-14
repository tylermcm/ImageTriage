from __future__ import annotations

"""Build and locally serve expiring Share to Phone packages."""

import html
import json
import mimetypes
import os
import socket
import subprocess
import threading
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from typing import Callable
from urllib.parse import quote, unquote, urlsplit
from uuid import uuid4

from PySide6.QtCore import QObject, QRunnable, QSize, Signal

from .image_resize import ResizeSourceItem, _load_resize_image, _save_resized_image, _scaled_image
from .scan_cache import app_data_root


PHONE_SHARE_PORT = 45873
PHONE_SHARE_FIREWALL_RULE = "Image Triage Phone Share"


@dataclass(slots=True, frozen=True)
class SharePreset:
    key: str
    name: str
    width: int
    height: int
    description: str


SHARE_PRESETS: tuple[SharePreset, ...] = (
    SharePreset("social_large", "Social Large", 2160, 2160, "Fits within 2160 px without changing the crop."),
    SharePreset("social_standard", "Social Standard", 1600, 1600, "Fits within 1600 px for a smaller transfer."),
    SharePreset("original", "Original Size", 0, 0, "Converts to JPEG without resizing."),
)


def share_preset_for_key(key: str) -> SharePreset:
    return next((preset for preset in SHARE_PRESETS if preset.key == key), SHARE_PRESETS[0])


@dataclass(slots=True, frozen=True)
class SharePackageSpec:
    name: str
    target: str
    account: str
    caption: str
    preset_key: str
    sources: tuple[ResizeSourceItem, ...]
    alt_text: tuple[str, ...]


@dataclass(slots=True, frozen=True)
class PreparedSharePackage:
    id: str
    name: str
    target: str
    account: str
    caption: str
    preset_key: str
    source_paths: tuple[str, ...]
    output_paths: tuple[str, ...]
    alt_text: tuple[str, ...]
    package_dir: str
    zip_path: str
    caption_path: str
    alt_text_path: str
    manifest_path: str


def prepare_share_package(
    spec: SharePackageSpec,
    *,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> PreparedSharePackage:
    if not spec.sources:
        raise ValueError("Select one or more images to share.")

    preset = share_preset_for_key(spec.preset_key)
    package_id = uuid4().hex
    package_dir = app_data_root() / "share_packages" / package_id
    package_dir.mkdir(parents=True, exist_ok=False)
    outputs: list[str] = []
    total = len(spec.sources)
    try:
        for index, source in enumerate(spec.sources, start=1):
            source_path = Path(source.source_path)
            if not source_path.exists():
                raise OSError(f"Source image is missing: {source.source_name}")
            target_size = QSize(preset.width, preset.height) if preset.width > 0 and preset.height > 0 else QSize()
            loaded = _load_resize_image(
                str(source_path),
                target_size=target_size,
                ignore_orientation=False,
                strip_metadata=True,
            )
            image = loaded.image
            if preset.width > 0 and preset.height > 0:
                image = _scaled_image(image, target_size=target_size, shrink_only=True)
            target_name = _ordered_output_name(index, source.source_name)
            target_path = package_dir / target_name
            _save_resized_image(
                image,
                target_path=str(target_path),
                target_suffix=".jpg",
                exif_bytes=None,
                icc_profile=None,
            )
            outputs.append(str(target_path))
            if progress_callback is not None:
                progress_callback(index, total, f"Prepared {target_name}")

        caption_path = package_dir / "caption.txt"
        caption_path.write_text(spec.caption, encoding="utf-8")
        alt_text_path = package_dir / "alt-text.txt"
        alt_text_path.write_text(_alt_text_document(outputs, spec.alt_text), encoding="utf-8")
        manifest_path = package_dir / "manifest.json"
        manifest_path.write_text(
            json.dumps(
                {
                    "name": _clean_package_name(spec.name),
                    "target": spec.target,
                    "account": spec.account,
                    "caption": spec.caption,
                    "preset": preset.key,
                    "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "images": [
                        {
                            "order": index,
                            "filename": Path(output).name,
                            "source_path": source.source_path,
                            "alt_text": spec.alt_text[index - 1] if index - 1 < len(spec.alt_text) else "",
                        }
                        for index, (source, output) in enumerate(zip(spec.sources, outputs), start=1)
                    ],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        zip_path = package_dir / f"{_safe_filename(spec.name)}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for output in outputs:
                archive.write(output, arcname=Path(output).name)
            archive.write(caption_path, arcname=caption_path.name)
            archive.write(alt_text_path, arcname=alt_text_path.name)
            archive.write(manifest_path, arcname=manifest_path.name)
    except Exception:
        import shutil

        shutil.rmtree(package_dir, ignore_errors=True)
        raise

    return PreparedSharePackage(
        id=package_id,
        name=_clean_package_name(spec.name),
        target=spec.target.strip(),
        account=spec.account.strip(),
        caption=spec.caption,
        preset_key=preset.key,
        source_paths=tuple(source.source_path for source in spec.sources),
        output_paths=tuple(outputs),
        alt_text=tuple(spec.alt_text),
        package_dir=str(package_dir),
        zip_path=str(zip_path),
        caption_path=str(caption_path),
        alt_text_path=str(alt_text_path),
        manifest_path=str(manifest_path),
    )


class SharePackageSignals(QObject):
    started = Signal(int)
    progress = Signal(int, int, str)
    finished = Signal(object)
    failed = Signal(str)


class SharePackageTask(QRunnable):
    def __init__(self, spec: SharePackageSpec) -> None:
        super().__init__()
        self.spec = spec
        self.signals = SharePackageSignals()
        self.setAutoDelete(True)

    def run(self) -> None:
        self.signals.started.emit(len(self.spec.sources))
        try:
            result = prepare_share_package(
                self.spec,
                progress_callback=lambda current, total, message: self.signals.progress.emit(current, total, message),
            )
        except Exception as exc:  # pragma: no cover - worker/runtime path
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


class LocalShareServer:
    """Token-scoped HTTP server that exposes only one prepared package."""

    def __init__(
        self,
        package: PreparedSharePackage,
        *,
        on_transfer: Callable[[], None] | None = None,
        bind_host: str = "0.0.0.0",
        preferred_port: int = PHONE_SHARE_PORT,
    ) -> None:
        self.package = package
        self.on_transfer = on_transfer
        self.bind_host = bind_host
        self.preferred_port = max(0, int(preferred_port))
        self.token = token_urlsafe(24)
        self._httpd = None
        self._thread: threading.Thread | None = None
        self._transfer_notified = False
        self._notification_lock = threading.Lock()

    @property
    def port(self) -> int:
        return int(self._httpd.server_port) if self._httpd is not None else 0

    @property
    def url(self) -> str:
        if self.port <= 0:
            return ""
        return f"http://{local_ipv4_address()}:{self.port}/{self.token}/"

    @property
    def loopback_url(self) -> str:
        if self.port <= 0:
            return ""
        return f"http://127.0.0.1:{self.port}/{self.token}/"

    def start(self) -> str:
        if self._httpd is not None:
            return self.url
        from http.server import ThreadingHTTPServer

        server = self

        class Handler(_ShareRequestHandler):
            share_server = server

        class ShareHTTPServer(ThreadingHTTPServer):
            allow_reuse_address = True

        self._httpd = ShareHTTPServer((self.bind_host, self.preferred_port), Handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="ImageTriagePhoneShare", daemon=True)
        self._thread.start()
        return self.url

    def stop(self) -> None:
        httpd = self._httpd
        self._httpd = None
        if httpd is None:
            return
        httpd.shutdown()
        httpd.server_close()
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)

    def notify_transfer(self) -> None:
        with self._notification_lock:
            if self._transfer_notified:
                return
            self._transfer_notified = True
        if self.on_transfer is not None:
            self.on_transfer()


from http.server import BaseHTTPRequestHandler


class _ShareRequestHandler(BaseHTTPRequestHandler):
    share_server: LocalShareServer

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlsplit(self.path)
        prefix = f"/{self.share_server.token}/"
        if not parsed.path.startswith(prefix):
            self.send_error(404)
            return
        relative = unquote(parsed.path[len(prefix) :])
        package = self.share_server.package
        if relative in {"", "index.html"}:
            self._send_bytes(_phone_page(package, self.share_server.token).encode("utf-8"), "text/html; charset=utf-8")
            return
        if relative == "download-all.zip":
            self.share_server.notify_transfer()
            self._send_file(Path(package.zip_path), download=True)
            return
        if relative == "caption.txt":
            self._send_file(Path(package.caption_path), download=True)
            return
        if relative == "alt-text.txt":
            self._send_file(Path(package.alt_text_path), download=True)
            return
        if relative == "manifest.json":
            self._send_file(Path(package.manifest_path), download=True)
            return
        if relative.startswith("image/"):
            try:
                index = int(relative.removeprefix("image/"))
                image_path = Path(package.output_paths[index])
            except (ValueError, IndexError):
                self.send_error(404)
                return
            if "download=1" in parsed.query:
                self.share_server.notify_transfer()
            self._send_file(image_path, download="download=1" in parsed.query)
            return
        self.send_error(404)

    def _send_file(self, path: Path, *, download: bool) -> None:
        if not path.is_file():
            self.send_error(404)
            return
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        disposition = f'attachment; filename="{path.name}"' if download else f'inline; filename="{path.name}"'
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", disposition)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        with path.open("rb") as handle:
            while chunk := handle.read(128 * 1024):
                self.wfile.write(chunk)

    def _send_bytes(self, payload: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'",
        )
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def qr_png_bytes(value: str) -> bytes:
    try:
        import qrcode
    except ImportError:
        qrcode = None
    if qrcode is not None:
        image = qrcode.make(value)
        stream = BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    # Development and AI-enabled installs already carry OpenCV. Keep this
    # fallback so a temporarily offline packaging environment can still run
    # the feature while qrcode remains the small declared core dependency.
    try:
        import cv2

        matrix = cv2.QRCodeEncoder_create().encode(value)
        matrix = cv2.copyMakeBorder(matrix, 4, 4, 4, 4, cv2.BORDER_CONSTANT, value=255)
        matrix = cv2.resize(matrix, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
        success, encoded = cv2.imencode(".png", matrix)
        if success:
            return bytes(encoded)
    except (ImportError, AttributeError, ValueError):
        pass
    raise RuntimeError("QR code support is not installed.")


def local_ipv4_address() -> str:
    probe = None
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 80))
        address = str(probe.getsockname()[0])
        if address and not address.startswith("127."):
            return address
    except OSError:
        pass
    finally:
        if probe is not None:
            probe.close()
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return "127.0.0.1"


@dataclass(slots=True, frozen=True)
class PhoneShareNetworkDiagnostic:
    profile: str = "unknown"
    inbound_blocked: bool = False
    private_rule_present: bool = False

    @property
    def warning(self) -> str:
        if self.profile == "public":
            return (
                "Windows currently marks this connection as Public, where inbound phone connections are blocked. "
                "If this is your trusted home network, change it to Private, then allow Image Triage on Private networks."
            )
        if self.inbound_blocked and not self.private_rule_present:
            return "Windows Firewall may block the phone connection. Allow Image Triage on Private networks, then scan again."
        if not self.private_rule_present:
            return "If the phone cannot connect, allow Image Triage on Private networks below."
        return ""


def phone_share_network_diagnostic() -> PhoneShareNetworkDiagnostic:
    if os.name != "nt":
        return PhoneShareNetworkDiagnostic()
    profile_output = _run_netsh(("advfirewall", "show", "currentprofile"))
    rule_output = _run_netsh(("advfirewall", "firewall", "show", "rule", f"name={PHONE_SHARE_FIREWALL_RULE}"))
    return _parse_phone_share_network_diagnostic(profile_output, rule_output)


def _parse_phone_share_network_diagnostic(
    profile_output: str,
    rule_output: str,
) -> PhoneShareNetworkDiagnostic:
    normalized = profile_output.casefold()
    profile = "unknown"
    if "public profile" in normalized:
        profile = "public"
    elif "private profile" in normalized:
        profile = "private"
    elif "domain profile" in normalized:
        profile = "domain"
    inbound_blocked = "blockinbound" in normalized or "block inbound" in normalized
    rule_normalized = rule_output.casefold()
    rule_present = bool(rule_output.strip()) and "no rules match" not in rule_normalized
    return PhoneShareNetworkDiagnostic(
        profile=profile,
        inbound_blocked=inbound_blocked,
        private_rule_present=rule_present,
    )


def request_private_firewall_access(port: int = PHONE_SHARE_PORT) -> bool:
    """Request an explicit, local-subnet-only Private-profile firewall rule."""
    if os.name != "nt":
        return False
    import ctypes

    parameters = " ".join(
        (
            "advfirewall firewall add rule",
            f'name="{PHONE_SHARE_FIREWALL_RULE}"',
            "dir=in",
            "action=allow",
            "protocol=TCP",
            f"localport={max(1, int(port))}",
            "remoteip=LocalSubnet",
            "profile=private",
            "enable=yes",
        )
    )
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", "netsh.exe", parameters, None, 0)
    return int(result) > 32


def _run_netsh(arguments: tuple[str, ...]) -> str:
    try:
        completed = subprocess.run(
            ("netsh.exe", *arguments),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return "\n".join(part for part in (completed.stdout, completed.stderr) if part)


def _ordered_output_name(index: int, source_name: str) -> str:
    stem = _safe_filename(Path(source_name).stem)
    return f"{index:02d}-{stem}.jpg"


def _safe_filename(value: str) -> str:
    cleaned = "".join(character if character.isalnum() or character in {"-", "_"} else "-" for character in value.strip())
    cleaned = "-".join(part for part in cleaned.split("-") if part)
    return cleaned[:80] or "phone-share"


def _clean_package_name(value: str) -> str:
    return " ".join(value.split()) or "Phone Share"


def _alt_text_document(output_paths: list[str], alt_text: tuple[str, ...]) -> str:
    blocks = []
    for index, output_path in enumerate(output_paths):
        text = alt_text[index].strip() if index < len(alt_text) else ""
        blocks.append(f"{index + 1}. {Path(output_path).name}\n{text or '(No alt text provided)'}")
    return "\n\n".join(blocks)


def _phone_page(package: PreparedSharePackage, token: str) -> str:
    caption = html.escape(package.caption)
    target = html.escape(package.target or "General")
    account = html.escape(package.account)
    account_line = f" · {account}" if account else ""
    cards: list[str] = []
    for index, output_path in enumerate(package.output_paths):
        alt = package.alt_text[index] if index < len(package.alt_text) else ""
        cards.append(
            f"""
            <article class="card">
              <img src="/{token}/image/{index}" alt="{html.escape(alt or Path(output_path).stem)}" loading="lazy">
              <div class="card-row"><strong>{index + 1}. {html.escape(Path(output_path).name)}</strong>
              <a class="small-button" href="/{token}/image/{index}?download=1">Download</a></div>
              <p>{html.escape(alt) if alt else '<span class="muted">No alt text provided</span>'}</p>
            </article>
            """
        )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(package.name)}</title>
<style>
:root {{ color-scheme: dark; font-family: system-ui, sans-serif; background:#101113; color:#f4f4f5; }}
body {{ margin:0 auto; max-width:760px; padding:20px 16px 48px; }}
h1 {{ font-size:1.45rem; margin:0 0 4px; }} .meta,.muted {{ color:#a7abb2; }}
.actions {{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin:18px 0; }}
a.button,button {{ border:0; border-radius:9px; padding:13px 14px; font-weight:650; text-align:center; text-decoration:none; cursor:pointer; }}
a.button {{ background:#6cb6ff; color:#07111c; }} button {{ background:#2a2d32; color:#fff; }}
textarea {{ box-sizing:border-box; width:100%; min-height:120px; resize:vertical; border:1px solid #454950; border-radius:9px; padding:12px; background:#191b1f; color:#fff; }}
.card {{ background:#191b1f; border:1px solid #30333a; border-radius:12px; padding:10px; margin-top:12px; overflow:hidden; }}
.card img {{ display:block; width:100%; max-height:70vh; object-fit:contain; border-radius:8px; background:#090a0b; }}
.card-row {{ display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:10px; }}
.small-button {{ color:#8fc7ff; text-decoration:none; white-space:nowrap; }} .card p {{ margin:8px 0 2px; line-height:1.4; }}
</style></head><body>
<h1>{html.escape(package.name)}</h1><div class="meta">{target}{account_line} · {len(package.output_paths)} image(s)</div>
<div class="actions"><a class="button" href="/{token}/download-all.zip">Download All</a><button onclick="copyCaption()">Copy Caption</button></div>
<textarea id="caption" readonly>{caption}</textarea>
<p class="meta"><a class="small-button" href="/{token}/alt-text.txt">Download alt text</a> · <a class="small-button" href="/{token}/manifest.json">Manifest</a></p>
{''.join(cards)}
<script>
async function copyCaption() {{
  const field=document.getElementById('caption'); field.select();
  try {{ await navigator.clipboard.writeText(field.value); }} catch (_) {{ document.execCommand('copy'); }}
}}
</script></body></html>"""
