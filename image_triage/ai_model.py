from __future__ import annotations

import os
import hashlib
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


DEFAULT_AI_MODEL_REPO_ID = "Skulleton12/DinoV3"
DEFAULT_AI_MODEL_REVISION = "2372da520e9da0b79430d18c8f038de0e8e3ba68"
DEFAULT_AI_MODEL_SIZE_MB = 1210
DEFAULT_SEMANTIC_MODEL_REPO_ID = "openai/clip-vit-base-patch32"
DEFAULT_SEMANTIC_MODEL_REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
DEFAULT_SEMANTIC_MODEL_SIZE_MB = 610
DEFAULT_SEGMENTATION_MODEL_REPO_ID = "nvidia/segformer-b0-finetuned-ade-512-512"
DEFAULT_SEGMENTATION_MODEL_REVISION = "b9175de73a0a34f7843135853d27629aa6987b2f"
DEFAULT_SEGMENTATION_MODEL_SIZE_MB = 15
DEFAULT_AICULLER_CLIP_REPO_ID = "Skulleton12/Clip"
DEFAULT_AICULLER_CLIP_REVISION = "581ad0eebb9540a2ec865c6a84d188bfec81dc15"
DEFAULT_AICULLER_CLIP_SIZE_MB = 1865
DEFAULT_AICULLER_TOPIQ_REPO_ID = "Skulleton12/TOPIQ"
DEFAULT_AICULLER_TOPIQ_REVISION = "56526fd721537c9abd4ec41b10b2ffcad5166c46"
DEFAULT_AICULLER_TOPIQ_SIZE_MB = 185
DEFAULT_AI_MODEL_SHA256 = {
    "config.json": "135ecd23e34a70b6fbed8b083fdecb319b7e3a54e3d849258bbe4ddcf1783bb5",
    "model.safetensors": "dcb2e45127cccbf1601e5f42fef165eea275c8e5213197e8dcf3f48822718179",
}
DEFAULT_SEMANTIC_MODEL_SHA256 = {
    "config.json": "b575ef3c36f2a057fa19e221650105052d61cc9c1a972ec15019c6261ec98770",
    "preprocessor_config.json": "910e70b3956ac9879ebc90b22fb3bc8a75b6a0677814500101a4c072bd7857bd",
    "tokenizer_config.json": "34b7336e4bee12e0a9730eaf5189f582ef3c3eea5027f65730e5717256755aad",
    "vocab.json": "5047b556ce86ccaf6aa22b3ffccfc52d391ea4accdab9c2f2407da5b742d4363",
    "merges.txt": "f526393189112391ce6f9795d4695f704121ce452c3aad1f5335cc41337eba85",
    "special_tokens_map.json": "f8c0d6c39aee3f8431078ef6646567b0aba7f2246e9c54b8b99d55c22b707cbf",
    "pytorch_model.bin": "a63082132ba4f97a80bea76823f544493bffa8082296d62d71581a4feff1576f",
}
DEFAULT_SEGMENTATION_MODEL_SHA256 = {
    "onnx/model.onnx": "f6520c8c7a414b9b17b6ccdf099fe1c357371d25fca090a021c9e6d0ce49bbed",
    "onnx/config.json": "4a7813fc7e89fa581278e5db3ffb25967bf02b36a980f2445dc94755062031cd",
    "onnx/preprocessor_config.json": "dbabd93c735c8a5c39ef207c6c4459bf2d261a5dcc55e1ba1c1b982e5947f518",
}
DEFAULT_AICULLER_CLIP_MODEL_SHA256: dict[str, str] = {}
DEFAULT_AICULLER_TOPIQ_MODEL_SHA256: dict[str, str] = {}
AI_MODEL_DIR_ENV = "AICULLING_MODEL_DIR"
AI_MODEL_REPO_ENV = "AICULLING_MODEL_REPO_ID"
AI_MODEL_REVISION_ENV = "AICULLING_MODEL_REVISION"
SEMANTIC_MODEL_DIR_ENV = "AICULLING_SEMANTIC_MODEL_DIR"
SEMANTIC_MODEL_REPO_ENV = "AICULLING_SEMANTIC_MODEL_REPO_ID"
SEMANTIC_MODEL_REVISION_ENV = "AICULLING_SEMANTIC_MODEL_REVISION"
SEGMENTATION_MODEL_DIR_ENV = "IMAGE_TRIAGE_SEGMENTATION_MODEL_DIR"
SEGMENTATION_MODEL_REPO_ENV = "IMAGE_TRIAGE_SEGMENTATION_MODEL_REPO_ID"
SEGMENTATION_MODEL_REVISION_ENV = "IMAGE_TRIAGE_SEGMENTATION_MODEL_REVISION"
AICULLER_CLIP_MODEL_DIR_ENV = "IMAGE_TRIAGE_AICULLER_CLIP_MODEL_DIR"
AICULLER_CLIP_MODEL_REPO_ENV = "IMAGE_TRIAGE_AICULLER_CLIP_MODEL_REPO_ID"
AICULLER_CLIP_MODEL_REVISION_ENV = "IMAGE_TRIAGE_AICULLER_CLIP_MODEL_REVISION"
AICULLER_TOPIQ_MODEL_DIR_ENV = "IMAGE_TRIAGE_AICULLER_TOPIQ_MODEL_DIR"
AICULLER_TOPIQ_MODEL_REPO_ENV = "IMAGE_TRIAGE_AICULLER_TOPIQ_MODEL_REPO_ID"
AICULLER_TOPIQ_MODEL_REVISION_ENV = "IMAGE_TRIAGE_AICULLER_TOPIQ_MODEL_REVISION"
AI_MODEL_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
AI_MODEL_REQUIRED_FILENAMES = ("config.json", "model.safetensors")
SEMANTIC_MODEL_REQUIRED_FILENAMES = (
    "config.json",
    "preprocessor_config.json",
    "tokenizer_config.json",
    "vocab.json",
    "merges.txt",
    "special_tokens_map.json",
    "pytorch_model.bin",
)
SEGMENTATION_MODEL_REQUIRED_FILENAMES = (
    "onnx/model.onnx",
    "onnx/config.json",
    "onnx/preprocessor_config.json",
)
AICULLER_CLIP_MODEL_REQUIRED_FILENAMES = (
    "tokenizer.json",
    "onnx/vision_model_uint8.onnx",
    "onnx/text_model_uint8.onnx",
    "onnx/vision_model_int8.onnx",
    "onnx/text_model_int8.onnx",
    "onnx/vision_model_quantized.onnx",
    "onnx/text_model_quantized.onnx",
    "onnx/vision_model_q4.onnx",
    "onnx/text_model_q4.onnx",
    "onnx/vision_model_bnb4.onnx",
    "onnx/text_model_bnb4.onnx",
)
AICULLER_TOPIQ_MODEL_REQUIRED_FILENAMES = ("topiq_nr.onnx",)
# Face-quality models (InsightFace buffalo_l): detection + landmarks + gender/age.
# Recognition (w600k_r50.onnx) is intentionally EXCLUDED here — the face-sort /
# "who is in this photo" workflow ships on its own separate download path.
DEFAULT_AICULLER_FACE_REPO_ID = "Skulleton12/insightface"
DEFAULT_AICULLER_FACE_REVISION = "df17665542088a2ba27cd6e534f7608e98fd9ea0"
DEFAULT_AICULLER_FACE_SIZE_MB = 23
DEFAULT_AICULLER_FACE_MODEL_SHA256: dict[str, str] = {}
AICULLER_FACE_MODEL_DIR_ENV = "IMAGE_TRIAGE_AICULLER_FACE_MODEL_DIR"
AICULLER_FACE_MODEL_REPO_ENV = "IMAGE_TRIAGE_AICULLER_FACE_MODEL_REPO_ID"
AICULLER_FACE_MODEL_REVISION_ENV = "IMAGE_TRIAGE_AICULLER_FACE_MODEL_REVISION"
INSIGHTFACE_PACK_NAME = "buffalo_l"
AICULLER_FACE_MODEL_REQUIRED_FILENAMES = ("det_10g.onnx", "2d106det.onnx", "genderage.onnx")
AI_MODEL_USER_AGENT = "ImageTriage/0.1"

AIModelProgressCallback = Callable[[str, int, int], None]


@dataclass(frozen=True)
class AIModelInstallation:
    repo_id: str
    revision: str
    install_dir: Path
    required_filenames: tuple[str, ...] = AI_MODEL_REQUIRED_FILENAMES
    expected_sha256: dict[str, str] | None = None
    alternate_download_filenames: dict[str, tuple[str, ...]] | None = None

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

    def download_filenames(self, filename: str) -> tuple[str, ...]:
        normalized = filename.strip().lstrip("/")
        alternates = self.alternate_download_filenames or {}
        return (normalized, *alternates.get(normalized, ()))


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
        expected_sha256=DEFAULT_AI_MODEL_SHA256 if resolved_repo_id == DEFAULT_AI_MODEL_REPO_ID and resolved_revision == DEFAULT_AI_MODEL_REVISION else None,
    )


def resolve_semantic_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(SEMANTIC_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_SEMANTIC_MODEL_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(SEMANTIC_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_SEMANTIC_MODEL_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(SEMANTIC_MODEL_DIR_ENV, "") or "").strip()
        or default_semantic_model_install_dir(repo_id=resolved_repo_id)
    )
    resolved_dir = Path(resolved_dir_value).expanduser().resolve()
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=resolved_dir,
        required_filenames=SEMANTIC_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            DEFAULT_SEMANTIC_MODEL_SHA256
            if resolved_repo_id == DEFAULT_SEMANTIC_MODEL_REPO_ID and resolved_revision == DEFAULT_SEMANTIC_MODEL_REVISION
            else None
        ),
    )


def resolve_segmentation_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(SEGMENTATION_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_SEGMENTATION_MODEL_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(SEGMENTATION_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_SEGMENTATION_MODEL_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(SEGMENTATION_MODEL_DIR_ENV, "") or "").strip()
        or default_segmentation_model_install_dir(repo_id=resolved_repo_id)
    )
    resolved_dir = Path(resolved_dir_value).expanduser().resolve()
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=resolved_dir,
        required_filenames=SEGMENTATION_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            DEFAULT_SEGMENTATION_MODEL_SHA256
            if resolved_repo_id == DEFAULT_SEGMENTATION_MODEL_REPO_ID
            and resolved_revision == DEFAULT_SEGMENTATION_MODEL_REVISION
            else None
        ),
    )


def resolve_aiculler_clip_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(AICULLER_CLIP_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_AICULLER_CLIP_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(AICULLER_CLIP_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_AICULLER_CLIP_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(AICULLER_CLIP_MODEL_DIR_ENV, "") or "").strip()
        or default_aiculler_clip_model_install_dir(repo_id=resolved_repo_id)
    )
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=Path(resolved_dir_value).expanduser().resolve(),
        required_filenames=AICULLER_CLIP_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            DEFAULT_AICULLER_CLIP_MODEL_SHA256
            if resolved_repo_id == DEFAULT_AICULLER_CLIP_REPO_ID and resolved_revision == DEFAULT_AICULLER_CLIP_REVISION
            else None
        ),
        alternate_download_filenames=(
            {"tokenizer.json": ("onnx/tokenizer.json", "clip-vit-large-patch14/tokenizer.json")}
            if resolved_repo_id == DEFAULT_AICULLER_CLIP_REPO_ID
            else None
        ),
    )


def resolve_aiculler_topiq_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(AICULLER_TOPIQ_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_AICULLER_TOPIQ_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(AICULLER_TOPIQ_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_AICULLER_TOPIQ_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(AICULLER_TOPIQ_MODEL_DIR_ENV, "") or "").strip()
        or default_aiculler_topiq_model_install_dir(repo_id=resolved_repo_id)
    )
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=Path(resolved_dir_value).expanduser().resolve(),
        required_filenames=AICULLER_TOPIQ_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            DEFAULT_AICULLER_TOPIQ_MODEL_SHA256
            if resolved_repo_id == DEFAULT_AICULLER_TOPIQ_REPO_ID and resolved_revision == DEFAULT_AICULLER_TOPIQ_REVISION
            else None
        ),
    )


def default_ai_model_install_dir(*, repo_id: str = DEFAULT_AI_MODEL_REPO_ID) -> Path:
    _owner, name = _repo_path_parts(repo_id)
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / name


def default_semantic_model_install_dir(*, repo_id: str = DEFAULT_SEMANTIC_MODEL_REPO_ID) -> Path:
    _owner, name = _repo_path_parts(repo_id)
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / name


def default_segmentation_model_install_dir(
    *,
    repo_id: str = DEFAULT_SEGMENTATION_MODEL_REPO_ID,
) -> Path:
    _owner, name = _repo_path_parts(repo_id)
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / name


def default_aiculler_clip_model_install_dir(*, repo_id: str = DEFAULT_AICULLER_CLIP_REPO_ID) -> Path:
    return (
        _default_user_cache_root()
        / "image_triage_ai_cache"
        / "models"
        / "CLI-Culler"
        / "Clip"
        / "clip-vit-large-patch14"
    )


def default_aiculler_topiq_model_install_dir(*, repo_id: str = DEFAULT_AICULLER_TOPIQ_REPO_ID) -> Path:
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / "CLI-Culler" / "TOPIQ"


def default_aiculler_face_model_install_dir(*, repo_id: str = DEFAULT_AICULLER_FACE_REPO_ID) -> Path:
    # Laid out so InsightFace FaceAnalysis(name="buffalo_l", root=<.../insightface>)
    # finds the ONNX at <root>/models/buffalo_l/<file>.onnx.
    return (
        _default_user_cache_root()
        / "image_triage_ai_cache"
        / "models"
        / "CLI-Culler"
        / "insightface"
        / "models"
        / INSIGHTFACE_PACK_NAME
    )


def aiculler_face_model_root(*, install_dir: str | Path | None = None) -> Path:
    """Directory to pass to InsightFace ``FaceAnalysis(root=...)`` — the parent of
    ``models/<pack>/``."""
    base = (
        Path(install_dir).expanduser().resolve()
        if install_dir
        else default_aiculler_face_model_install_dir()
    )
    return base.parent.parent


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
        errors: list[str] = []
        for source_filename in resolved.download_filenames(filename):
            try:
                _download_file(
                    source_url=resolved.download_url(source_filename),
                    destination=destination,
                    filename=source_filename,
                    expected_sha256=(resolved.expected_sha256 or {}).get(filename),
                    progress_callback=progress_callback,
                )
                break
            except RuntimeError as exc:
                errors.append(str(exc))
        else:
            joined = "; ".join(errors)
            raise RuntimeError(f"Failed to download {filename} from {resolved.repo_id}: {joined}")

    return resolved


def download_semantic_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_semantic_model_installation(),
        force=force,
        progress_callback=progress_callback,
    )


def download_segmentation_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_segmentation_model_installation(),
        force=force,
        progress_callback=progress_callback,
    )


def download_aiculler_clip_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_aiculler_clip_model_installation(),
        force=force,
        progress_callback=progress_callback,
    )


def download_aiculler_topiq_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_aiculler_topiq_model_installation(),
        force=force,
        progress_callback=progress_callback,
    )


def resolve_aiculler_face_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(AICULLER_FACE_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_AICULLER_FACE_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(AICULLER_FACE_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_AICULLER_FACE_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(AICULLER_FACE_MODEL_DIR_ENV, "") or "").strip()
        or default_aiculler_face_model_install_dir(repo_id=resolved_repo_id)
    )
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=Path(resolved_dir_value).expanduser().resolve(),
        required_filenames=AICULLER_FACE_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            DEFAULT_AICULLER_FACE_MODEL_SHA256
            if resolved_repo_id == DEFAULT_AICULLER_FACE_REPO_ID
            and resolved_revision == DEFAULT_AICULLER_FACE_REVISION
            else None
        ),
    )


def download_aiculler_face_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_aiculler_face_model_installation(),
        force=force,
        progress_callback=progress_callback,
    )


def _download_file(
    *,
    source_url: str,
    destination: Path,
    filename: str,
    expected_sha256: str | None,
    progress_callback: AIModelProgressCallback | None,
) -> None:
    parsed = urlparse(source_url)
    if parsed.scheme != "https":
        raise ValueError("Model download URL must use https://.")

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
        if expected_sha256:
            digest = _sha256_file(temp_destination)
            if digest.casefold() != expected_sha256.casefold():
                raise ValueError(
                    f"Downloaded model file {filename} failed SHA256 verification. "
                    f"Expected {expected_sha256}, got {digest}."
                )
        temp_destination.replace(destination)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Failed to download {filename}: HTTP {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Failed to download {filename}: {exc.reason}") from exc
    except Exception:
        temp_destination.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(AI_MODEL_DOWNLOAD_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _default_user_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata)
        try:
            return Path.home() / "AppData" / "Local"
        except RuntimeError:
            return Path.cwd() / ".image-triage-cache"
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home)
    try:
        return Path.home() / ".cache"
    except RuntimeError:
        return Path.cwd() / ".cache"


def _repo_path_parts(repo_id: str) -> tuple[str, str]:
    parts = [part.strip() for part in repo_id.split("/") if part.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if parts:
        return "model", parts[-1]
    return "model", "unknown"
