from __future__ import annotations

import os
import hashlib
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from .ai_manifest import MODEL_BUNDLES, bundle_expected_sha256
from .ai_paths import managed_model_dir


DEFAULT_AI_MODEL_REPO_ID = "Skulleton12/DinoV3"
DEFAULT_AI_MODEL_REVISION = "2372da520e9da0b79430d18c8f038de0e8e3ba68"
DEFAULT_AI_MODEL_SIZE_MB = 1210
DEFAULT_SEMANTIC_MODEL_REPO_ID = "openai/clip-vit-base-patch32"
DEFAULT_SEMANTIC_MODEL_REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
DEFAULT_SEMANTIC_MODEL_SIZE_MB = 610
DEFAULT_SEGMENTATION_MODEL_REPO_ID = "shi-labs/oneformer_ade20k_swin_tiny"
DEFAULT_SEGMENTATION_MODEL_REVISION = "7fdbe8184c22b28aee60168e5635394bb556588e"
DEFAULT_SEGMENTATION_MODEL_SIZE_MB = 196
DEFAULT_BIREFNET_MODEL_REPO_ID = "ZhengPeng7/BiRefNet"
DEFAULT_BIREFNET_MODEL_REVISION = "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4"
DEFAULT_BIREFNET_MODEL_SIZE_MB = 425
DEFAULT_AICULLER_CLIP_REPO_ID = "onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX"
DEFAULT_AICULLER_CLIP_REVISION = "9463a9c508a344c837ffefe9d724f3827bf2dc79"
DEFAULT_AICULLER_CLIP_SIZE_MB = 98
DEFAULT_AICULLER_TOPIQ_REPO_ID = "Skulleton12/TOPIQ"
DEFAULT_AICULLER_TOPIQ_REVISION = "56526fd721537c9abd4ec41b10b2ffcad5166c46"
DEFAULT_AICULLER_TOPIQ_SIZE_MB = 185
DEFAULT_AI_MODEL_SHA256 = bundle_expected_sha256("dino")
DEFAULT_SEMANTIC_MODEL_SHA256 = bundle_expected_sha256("clip")
DEFAULT_SEGMENTATION_MODEL_SHA256 = bundle_expected_sha256("oneformer")
DEFAULT_BIREFNET_MODEL_SHA256 = bundle_expected_sha256("birefnet")
DEFAULT_AICULLER_CLIP_MODEL_SHA256 = bundle_expected_sha256("tinyclip")
DEFAULT_AICULLER_TOPIQ_MODEL_SHA256 = bundle_expected_sha256("topiq")
AI_MODEL_DIR_ENV = "AICULLING_MODEL_DIR"
AI_MODEL_REPO_ENV = "AICULLING_MODEL_REPO_ID"
AI_MODEL_REVISION_ENV = "AICULLING_MODEL_REVISION"
SEMANTIC_MODEL_DIR_ENV = "AICULLING_SEMANTIC_MODEL_DIR"
SEMANTIC_MODEL_REPO_ENV = "AICULLING_SEMANTIC_MODEL_REPO_ID"
SEMANTIC_MODEL_REVISION_ENV = "AICULLING_SEMANTIC_MODEL_REVISION"
SEGMENTATION_MODEL_DIR_ENV = "IMAGE_TRIAGE_SEGMENTATION_MODEL_DIR"
SEGMENTATION_MODEL_REPO_ENV = "IMAGE_TRIAGE_SEGMENTATION_MODEL_REPO_ID"
SEGMENTATION_MODEL_REVISION_ENV = "IMAGE_TRIAGE_SEGMENTATION_MODEL_REVISION"
BIREFNET_MODEL_DIR_ENV = "IMAGE_TRIAGE_BIREFNET_MODEL_DIR"
BIREFNET_MODEL_REPO_ENV = "IMAGE_TRIAGE_BIREFNET_MODEL_REPO_ID"
BIREFNET_MODEL_REVISION_ENV = "IMAGE_TRIAGE_BIREFNET_MODEL_REVISION"
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
# OneFormer loads offline through ``from_pretrained(<local dir>)``; the seven
# files below land flat in the managed model directory (no Hugging Face cache
# layout) so the semantic worker can load with ``local_files_only=True``.
SEGMENTATION_MODEL_REQUIRED_FILENAMES = (
    "config.json",
    "merges.txt",
    "preprocessor_config.json",
    "pytorch_model.bin",
    "special_tokens_map.json",
    "tokenizer_config.json",
    "vocab.json",
)
BIREFNET_MODEL_REQUIRED_FILENAMES = (
    "BiRefNet_config.py",
    "birefnet.py",
    "config.json",
    "model.safetensors",
)
DEFAULT_AICULLER_CLIP_VARIANT = "fp32"
AICULLER_CLIP_VARIANT_KEYS = ("fp32", "fp16")
AICULLER_CLIP_TOKENIZER_FILENAME = "tokenizer.json"


def aiculler_clip_variant_filenames(variant: str | None) -> tuple[str, ...]:
    """Return one combined TinyCLIP graph and its tokenizer."""
    normalized = str(variant or "").strip().lower() or DEFAULT_AICULLER_CLIP_VARIANT
    if normalized not in AICULLER_CLIP_VARIANT_KEYS:
        normalized = DEFAULT_AICULLER_CLIP_VARIANT
    model = "onnx/model.onnx" if normalized == "fp32" else "onnx/model_fp16.onnx"
    return (AICULLER_CLIP_TOKENIZER_FILENAME, model)


AICULLER_CLIP_MODEL_REQUIRED_FILENAMES = (
    AICULLER_CLIP_TOKENIZER_FILENAME,
    "onnx/model.onnx",
)
AICULLER_TOPIQ_MODEL_REQUIRED_FILENAMES = ("topiq_nr.onnx",)
# Face and people-search models (AuraFace, Apache-2.0): detection (SCRFD),
# landmarks, gender/age, and ArcFace-style recognition embeddings. AuraFace is a
# commercially-licensable InsightFace FaceAnalysis pack, loaded via
# FaceAnalysis(name="auraface").
DEFAULT_AICULLER_FACE_REPO_ID = "fal/AuraFace-v1"
DEFAULT_AICULLER_FACE_REVISION = "af6d057c9b0ec4071d4c49c80e3539258798b609"
DEFAULT_AICULLER_FACE_SIZE_MB = 285
DEFAULT_AICULLER_FACE_MODEL_SHA256 = bundle_expected_sha256("faces")
AICULLER_FACE_MODEL_DIR_ENV = "IMAGE_TRIAGE_AICULLER_FACE_MODEL_DIR"
AICULLER_FACE_MODEL_REPO_ENV = "IMAGE_TRIAGE_AICULLER_FACE_MODEL_REPO_ID"
AICULLER_FACE_MODEL_REVISION_ENV = "IMAGE_TRIAGE_AICULLER_FACE_MODEL_REVISION"
AICULLER_FACE_PACK_NAME = "auraface"
# The recognition embedding model within the pack (used to identify the
# identity_model tag stored with each face embedding).
AICULLER_FACE_RECOGNITION_MODEL = "glintr100.onnx"
AICULLER_FACE_MODEL_REQUIRED_FILENAMES = (
    "scrfd_10g_bnkps.onnx",
    "2d106det.onnx",
    "genderage.onnx",
    "glintr100.onnx",
)
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
    # Set when this installation corresponds to a manifest bundle, which routes
    # download and readiness through the transactional store instead of the
    # old "the file exists, so it must be fine" check.
    bundle_key: str = ""

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
        """Whether this model is genuinely usable, not merely present.

        For a manifest bundle this is the verified status: every file present,
        every size matching the published size, and the recorded revision equal
        to the one this build requires.
        """
        if self.bundle_key:
            from .ai_model_store import bundle_status  # local import: avoids a cycle

            if bundle_install_dir_matches(self):
                return bundle_status(self.bundle_key).is_ready
        return not self.missing_files

    def download_url(self, filename: str) -> str:
        normalized = filename.strip().lstrip("/")
        return f"https://huggingface.co/{self.repo_id}/resolve/{self.revision}/{normalized}?download=true"

    def download_filenames(self, filename: str) -> tuple[str, ...]:
        normalized = filename.strip().lstrip("/")
        alternates = self.alternate_download_filenames or {}
        return (normalized, *alternates.get(normalized, ()))


def bundle_install_dir_matches(installation: "AIModelInstallation") -> bool:
    """True when this installation points at the managed bundle directory.

    A caller that overrides ``install_dir`` (tests, support workflows) keeps the
    old existence semantics rather than being judged against the manifest.
    """
    from .ai_model_store import bundle_install_dir  # local import: avoids a cycle

    if not installation.bundle_key:
        return False
    bundle = MODEL_BUNDLES.get(installation.bundle_key)
    if bundle is None:
        return False
    # A caller asking for a different file set than the manifest describes (the
    # fp16 TinyCLIP export, for example) is not this bundle.
    if tuple(installation.required_filenames) != tuple(bundle.filenames):
        return False
    if installation.repo_id != bundle.repo_id or installation.revision != bundle.revision:
        return False
    try:
        return installation.install_dir == bundle_install_dir(installation.bundle_key)
    except (KeyError, OSError):
        return False


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
        bundle_key="dino",
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
        bundle_key="clip",
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
        bundle_key="oneformer",
    )


def resolve_birefnet_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(BIREFNET_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_BIREFNET_MODEL_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(BIREFNET_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_BIREFNET_MODEL_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(BIREFNET_MODEL_DIR_ENV, "") or "").strip()
        or default_birefnet_model_install_dir(repo_id=resolved_repo_id)
    )
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=Path(resolved_dir_value).expanduser().resolve(),
        required_filenames=BIREFNET_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            DEFAULT_BIREFNET_MODEL_SHA256
            if resolved_repo_id == DEFAULT_BIREFNET_MODEL_REPO_ID
            and resolved_revision == DEFAULT_BIREFNET_MODEL_REVISION
            else None
        ),
        bundle_key="birefnet",
    )


def resolve_aiculler_clip_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
    variant: str | None = None,
) -> AIModelInstallation:
    configured_repo_id = repo_id or (os.environ.get(AICULLER_CLIP_MODEL_REPO_ENV, "") or "").strip()
    resolved_repo_id = configured_repo_id or DEFAULT_AICULLER_CLIP_REPO_ID
    configured_revision = revision or (os.environ.get(AICULLER_CLIP_MODEL_REVISION_ENV, "") or "").strip()
    resolved_revision = configured_revision or DEFAULT_AICULLER_CLIP_REVISION
    resolved_dir_value = (
        install_dir
        or (os.environ.get(AICULLER_CLIP_MODEL_DIR_ENV, "") or "").strip()
        or default_aiculler_clip_model_install_dir(repo_id=resolved_repo_id)
    )
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=Path(resolved_dir_value).expanduser().resolve(),
        required_filenames=(
            aiculler_clip_variant_filenames(variant)
            if variant is not None
            else AICULLER_CLIP_MODEL_REQUIRED_FILENAMES
        ),
        expected_sha256=(
            DEFAULT_AICULLER_CLIP_MODEL_SHA256
            if resolved_repo_id == DEFAULT_AICULLER_CLIP_REPO_ID
            and resolved_revision == DEFAULT_AICULLER_CLIP_REVISION
            else None
        ),
        bundle_key="tinyclip",
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
        bundle_key="topiq",
    )


def _managed_bundle_dir(
    bundle_key: str,
    repo_id: str,
    *,
    prefix: tuple[str, ...] = (),
) -> Path:
    """Managed directory for a bundle, or a sibling when the repo is overridden."""
    bundle = MODEL_BUNDLES[bundle_key]
    if repo_id == bundle.repo_id:
        return managed_model_dir(*bundle.install_parts)
    _owner, name = _repo_path_parts(repo_id)
    return managed_model_dir(*prefix, name)


def default_ai_model_install_dir(*, repo_id: str = DEFAULT_AI_MODEL_REPO_ID) -> Path:
    return _managed_bundle_dir("dino", repo_id)


def default_semantic_model_install_dir(*, repo_id: str = DEFAULT_SEMANTIC_MODEL_REPO_ID) -> Path:
    return _managed_bundle_dir("clip", repo_id)


def default_segmentation_model_install_dir(
    *,
    repo_id: str = DEFAULT_SEGMENTATION_MODEL_REPO_ID,
) -> Path:
    return _managed_bundle_dir("oneformer", repo_id)


def default_birefnet_model_install_dir(
    *,
    repo_id: str = DEFAULT_BIREFNET_MODEL_REPO_ID,
) -> Path:
    return _managed_bundle_dir("birefnet", repo_id, prefix=("Editor",))


def default_aiculler_clip_model_install_dir(*, repo_id: str = DEFAULT_AICULLER_CLIP_REPO_ID) -> Path:
    return _managed_bundle_dir("tinyclip", repo_id, prefix=("CLI-Culler", "Clip"))


def default_aiculler_topiq_model_install_dir(*, repo_id: str = DEFAULT_AICULLER_TOPIQ_REPO_ID) -> Path:
    del repo_id  # the TOPIQ bundle always lands in one fixed directory
    return managed_model_dir(*MODEL_BUNDLES["topiq"].install_parts)


def default_aiculler_face_model_install_dir(*, repo_id: str = DEFAULT_AICULLER_FACE_REPO_ID) -> Path:
    # Laid out so InsightFace FaceAnalysis(name=<pack>, root=<.../faces>)
    # finds the ONNX at <root>/models/<pack>/<file>.onnx.
    return managed_model_dir(*MODEL_BUNDLES["faces"].install_parts)


def active_face_identity_model() -> str:
    """The ``identity_model`` tag stored with face embeddings from the active pack.

    Must match ``FaceQualityAnalyzer._identity_model`` (``f"insightface:{name}"``).
    Use it to scope clustering to the current recognizer so embeddings written by
    a different (e.g. older) recognizer are never mixed into the same 512-d space.
    """
    return f"insightface:{AICULLER_FACE_PACK_NAME}"


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

    if bundle_install_dir_matches(resolved):
        # Manifest bundles install transactionally: staged, fully verified and
        # activated atomically, so an interrupted download can never leave a
        # directory that later looks installed.
        from .ai_model_store import install_bundle  # local import: avoids a cycle

        install_bundle(
            resolved.bundle_key,
            force=force,
            progress_callback=progress_callback,
        )
        return resolved

    resolved.install_dir.mkdir(parents=True, exist_ok=True)

    for filename in resolved.required_filenames:
        destination = resolved.install_dir / filename
        expected = (resolved.expected_sha256 or {}).get(filename)
        if destination.exists() and not force and _file_is_trusted(destination, expected):
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


def download_birefnet_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_birefnet_model_installation(),
        force=force,
        progress_callback=progress_callback,
    )


# --- SAM 2.1 promptable segmentation (Editor "click to select") --------------
# facebook/sam2.1-hiera-tiny is Apache-2.0 and loads offline via transformers
# Sam2Model/Sam2Processor from a flat directory, exactly like OneFormer.
DEFAULT_SAM_MODEL_REPO_ID = "facebook/sam2.1-hiera-tiny"
DEFAULT_SAM_MODEL_REVISION = "de431c4043854a71d8101e17995dfe596bf101a5"
DEFAULT_SAM_MODEL_SIZE_MB = 150
SAM_MODEL_DIR_ENV = "IMAGE_TRIAGE_SAM_MODEL_DIR"
SAM_MODEL_REPO_ENV = "IMAGE_TRIAGE_SAM_MODEL_REPO_ID"
SAM_MODEL_REVISION_ENV = "IMAGE_TRIAGE_SAM_MODEL_REVISION"
SAM_MODEL_REQUIRED_FILENAMES = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
    "processor_config.json",
    "video_preprocessor_config.json",
)
DEFAULT_SAM_MODEL_SHA256 = bundle_expected_sha256("sam")


def default_sam_model_install_dir(*, repo_id: str = DEFAULT_SAM_MODEL_REPO_ID) -> Path:
    return _managed_bundle_dir("sam", repo_id, prefix=("Editor",))


def resolve_sam_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(SAM_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_SAM_MODEL_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(SAM_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_SAM_MODEL_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(SAM_MODEL_DIR_ENV, "") or "").strip()
        or default_sam_model_install_dir(repo_id=resolved_repo_id)
    )
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=Path(resolved_dir_value).expanduser().resolve(),
        required_filenames=SAM_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            DEFAULT_SAM_MODEL_SHA256
            if resolved_repo_id == DEFAULT_SAM_MODEL_REPO_ID
            and resolved_revision == DEFAULT_SAM_MODEL_REVISION
            else None
        ),
        bundle_key="sam",
    )


def download_sam_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_sam_model_installation(),
        force=force,
        progress_callback=progress_callback,
    )


DEFAULT_DEPTH_MODEL_REPO_ID = "depth-anything/Depth-Anything-V2-Small-hf"
# Apache-2.0 (the Small variant only; Base/Large are CC-BY-NC).
DEFAULT_DEPTH_MODEL_REVISION = MODEL_BUNDLES["depth"].revision
DEFAULT_DEPTH_MODEL_SIZE_MB = 100
DEPTH_MODEL_DIR_ENV = "IMAGE_TRIAGE_DEPTH_MODEL_DIR"
DEPTH_MODEL_REPO_ENV = "IMAGE_TRIAGE_DEPTH_MODEL_REPO_ID"
DEPTH_MODEL_REVISION_ENV = "IMAGE_TRIAGE_DEPTH_MODEL_REVISION"
DEPTH_MODEL_REQUIRED_FILENAMES = (
    "config.json",
    "model.safetensors",
    "preprocessor_config.json",
)


def default_depth_model_install_dir(*, repo_id: str = DEFAULT_DEPTH_MODEL_REPO_ID) -> Path:
    return _managed_bundle_dir("depth", repo_id, prefix=("Editor",))


def resolve_depth_model_installation(
    *,
    install_dir: str | Path | None = None,
    repo_id: str | None = None,
    revision: str | None = None,
) -> AIModelInstallation:
    resolved_repo_id = (
        repo_id
        or (os.environ.get(DEPTH_MODEL_REPO_ENV, "") or "").strip()
        or DEFAULT_DEPTH_MODEL_REPO_ID
    )
    resolved_revision = (
        revision
        or (os.environ.get(DEPTH_MODEL_REVISION_ENV, "") or "").strip()
        or DEFAULT_DEPTH_MODEL_REVISION
    )
    resolved_dir_value = (
        install_dir
        or (os.environ.get(DEPTH_MODEL_DIR_ENV, "") or "").strip()
        or default_depth_model_install_dir(repo_id=resolved_repo_id)
    )
    return AIModelInstallation(
        repo_id=resolved_repo_id,
        revision=resolved_revision,
        install_dir=Path(resolved_dir_value).expanduser().resolve(),
        required_filenames=DEPTH_MODEL_REQUIRED_FILENAMES,
        expected_sha256=(
            bundle_expected_sha256("depth")
            if resolved_repo_id == DEFAULT_DEPTH_MODEL_REPO_ID
            and resolved_revision == DEFAULT_DEPTH_MODEL_REVISION
            else None
        ),
        bundle_key="depth",
    )


def download_depth_model(
    installation: AIModelInstallation | None = None,
    *,
    force: bool = False,
    progress_callback: AIModelProgressCallback | None = None,
) -> AIModelInstallation:
    return download_ai_model(
        installation or resolve_depth_model_installation(),
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
        bundle_key="faces",
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


def uninstall_ai_model(installation: AIModelInstallation) -> bool:
    """Delete a managed model's install directory and everything under it.

    For the CLIP cache this removes both automatic precision exports. Returns
    True if the directory existed and was removed."""
    target = installation.install_dir
    if not target.exists():
        return False
    shutil.rmtree(target, ignore_errors=True)
    return not target.exists()


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


def _file_is_trusted(path: Path, expected_sha256: str | None) -> bool:
    """Whether an already-present file may be skipped.

    A file with a published hash is only skipped once that hash matches, so a
    truncated or intercepted earlier download is re-fetched instead of being
    accepted forever.
    """
    if not expected_sha256:
        try:
            return path.stat().st_size > 0
        except OSError:
            return False
    try:
        return _sha256_file(path).casefold() == expected_sha256.casefold()
    except OSError:
        return False


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(AI_MODEL_DOWNLOAD_CHUNK_SIZE), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _repo_path_parts(repo_id: str) -> tuple[str, str]:
    parts = [part.strip() for part in repo_id.split("/") if part.strip()]
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    if parts:
        return "model", parts[-1]
    return "model", "unknown"
