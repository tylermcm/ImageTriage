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
DEFAULT_AICULLER_CLIP_REPO_ID = "Xenova/clip-vit-large-patch14"
DEFAULT_AICULLER_CLIP_REVISION = "c307790166907339eed5a9a53a249af534102536"
# The automatic install contains FP32 primary models and FP16 fallback models.
DEFAULT_AICULLER_CLIP_SIZE_MB = 2572
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
    "config.json": "091cbc7c980128ae63b2a15d882923f326f85926ef163adad00c24bd90228896",
    "merges.txt": "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a",
    "preprocessor_config.json": "2c3c403d8414263e732996bb2ffeab80dd5ced0068ab11bfe5adf476ef75823c",
    "pytorch_model.bin": "909b07dbf4129c2bbb8df4498e35dcd46f305e3ec45329d3ff6d4f0360de27f3",
    "special_tokens_map.json": "c4864a9376a8401918425bed71fc14fc0e81f9b59ec45c1cf96cccb2df508eac",
    "tokenizer_config.json": "64dd88e64d791e3be4d38be62d7e77e0a24df9e79205ac740af505aa2e94c367",
    "vocab.json": "e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349",
}
DEFAULT_BIREFNET_MODEL_SHA256 = {
    "model.safetensors": "9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154",
}
DEFAULT_AICULLER_CLIP_MODEL_SHA256 = {
    "onnx/vision_model.onnx": "ff49f8aa57c7abfd26e382eb083e4dbf988505223a9bd3767dbfd4e729206709",
    "onnx/text_model.onnx": "a86051a90491b97e2ea1d0351ef664926735bca7536034384f901915ee91fd69",
    "onnx/vision_model_fp16.onnx": "6e6b9e280b73bdc432b6c3b1c05f33596bbe5570f6825f1174eaa207fc1d22dc",
    "onnx/text_model_fp16.onnx": "643d385d6adbc4b9067f3f94384cc63a8409accb1bfd414496d17df84b161032",
}
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
    """Return one precision pair for diagnostics and compatibility tests."""
    normalized = str(variant or "").strip().lower() or DEFAULT_AICULLER_CLIP_VARIANT
    if normalized not in AICULLER_CLIP_VARIANT_KEYS:
        normalized = DEFAULT_AICULLER_CLIP_VARIANT
    suffix = "" if normalized == "fp32" else f"_{normalized}"
    vision = f"onnx/vision_model{suffix}.onnx"
    text = f"onnx/text_model{suffix}.onnx"
    return (AICULLER_CLIP_TOKENIZER_FILENAME, vision, text)


AICULLER_CLIP_MODEL_REQUIRED_FILENAMES = (
    AICULLER_CLIP_TOKENIZER_FILENAME,
    "onnx/vision_model.onnx",
    "onnx/text_model.onnx",
    "onnx/vision_model_fp16.onnx",
    "onnx/text_model_fp16.onnx",
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


def default_birefnet_model_install_dir(
    *,
    repo_id: str = DEFAULT_BIREFNET_MODEL_REPO_ID,
) -> Path:
    _owner, name = _repo_path_parts(repo_id)
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / "Editor" / name


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
DEFAULT_SAM_MODEL_SHA256 = {
    "config.json": "860aff9751b139d83a4ad7df1e5535416fded533e0ead02625edbefcb9953cce",
    "model.safetensors": "48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7",
    "preprocessor_config.json": "6ebf229ee259368ce4a8d4f2fe893a72b053023710853e257253939e601f583d",
    "processor_config.json": "f8a68e865cfad115c1c2763f3d93eca7b1c622da06da2a9273eb437fb2389b6d",
    "video_preprocessor_config.json": "9fccfe5f464ec38c2f236d0e6a68e95511c80c22132fc2fa4b9f7b65f24fad95",
}


def default_sam_model_install_dir(*, repo_id: str = DEFAULT_SAM_MODEL_REPO_ID) -> Path:
    _owner, name = _repo_path_parts(repo_id)
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / "Editor" / name


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
DEFAULT_DEPTH_MODEL_REVISION = "main"
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
    _owner, name = _repo_path_parts(repo_id)
    return _default_user_cache_root() / "image_triage_ai_cache" / "models" / "Editor" / name


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
        expected_sha256=None,  # TODO: pin a revision + hashes once validated.
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
