from __future__ import annotations

import os
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


DEFAULT_AI_MODEL_REPO_ID = "Skulleton12/DinoV2"
DEFAULT_AI_MODEL_REVISION = "main"
DEFAULT_AI_MODEL_SIZE_MB = 346
AI_MODEL_DIR_ENV = "AICULLING_MODEL_DIR"
AI_MODEL_REPO_ENV = "AICULLING_MODEL_REPO_ID"
AI_MODEL_REVISION_ENV = "AICULLING_MODEL_REVISION"
AI_MODEL_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
AI_MODEL_REQUIRED_FILENAMES = ("config.json", "model.safetensors")
AI_MODEL_USER_AGENT = "ImageTriage/0.1"

AIModelProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class AIModelInstallation:
    repo_id: str
    revision: str
    install_dir: Path
    required_filenames: tuple[str, ...] = AI_MODEL_REQUIRED_FILENAMES

    @property
    def model_name(self) -> str:
        return str(self.install_dir)

    @property
    def missing_files(self) -> tuple[Path, ...]:
        return tuple(
            self.install_dir / filename
            for filename in self.required_filenames
            if not (self.install_dir / filename).exists()
        )

    @property
    def is_installed(self) -> bool:
        return not self.missing_files

    def download_url(self, filename: str) -> str:
        normalized = filename.strip().lstrip("/")
        return f"https://huggingface.co/{self.repo_id}/resolve/{self.revision}/{normalized}?download=true"


def resolve_ai_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(AI_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_AI_MODEL_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(AI_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_AI_MODEL_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(AI_MODEL_DIR_ENV, "") or "").strip()
        or default_ai_model_install_dir(repo_id=resolved_repo_id)
    )
    resolved_dir = Path(resolved_dir_value).expanduser().resolve()
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=resolved_dir,
    )


def default_ai_model_install_dir(*, repo_id: str = DEFAULT_AI_MODEL_REPO_ID) -> Path:
    owner, name = _repo_path_parts(repo_id)
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / owner / name


def download_ai_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    resolved = installation or resolve_ai_model_installation()
    resolved.install_dir.mkdir(parents=True, exist_ok=True)

    for filename in resolved.required_filenames:
        destination = resolved.install_dir / filename
        if destination.exists() and not force:
            continue
        _download_file(
            source_url=resolved.download_url(filename),
            destination=destination,
            filename=filename,
            progress_callback=progress_callback,
        )

    return resolved


def remove_ai_model(installation: AIModelInstallation | None = None) -> None:
    resolved = installation or resolve_ai_model_installation()
    if not resolved.install_dir.exists():
        return
    shutil.rmtree(resolved.install_dir)


def _download_file(
    *,
    source_url: str,
    destination: Path,
    filename: str,
    progress_callback: AIModelProgressCallback | None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_destination = destination.with_suffix(destination.suffix + ".download")
    if temp_destination.exists():
        temp_destination.unlink(missing_ok=True)

    request = urllib.request.Request(source_url, headers={"User-Agent": AI_MODEL_USER_AGENT})
    try:
        with urllib.request.urlopen(request) as response, temp_destination.open("wb") as handle:
            total_bytes = int(response.headers.get("Content-Length") or 0)
            downloaded = 0
            while True:
                chunk = response.read(AI_MODEL_DOWNLOAD_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                downloaded += len(chunk)
                if progress_callback is not None:
                    progress_callback(filename, downloaded, total_bytes)
        temp_destination.replace(destination)
    except Exception:
        temp_destination.unlink(missing_ok=True)
        raise


def _default_user_cache_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))


def _repo_path_parts(repo_id: str) -> tuple[str, str]:
    parts = [part.strip() for part in repo_id.split("/") if part.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if parts:
        return "model", parts[-1]
    return "model", "unknown"
