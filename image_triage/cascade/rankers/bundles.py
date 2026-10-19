"""Managed model bundles for the rankers, installed through ``ai_model_store`` (hash verified, atomic, on demand; never bundled).

``ai_manifest`` is the app's one list of model bundles. Until these two entries move into it, ``register_bundles`` adds them to its
registries at runtime (idempotent) so the model store's install, status and repair paths work unchanged.

Open items (see docs/ai_v5_implementation.md): the MANIQA KonIQ checkpoint has no pinned download source and no recorded SHA-256 yet.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...ai_manifest import MODEL_BUNDLES, MODEL_FILE_DIGESTS, FileDigest, ModelBundle
from .errors import RankerWeightsMissing
from .spec import MANIQA, QREALIGN, RankerSpec

MANIQA_CHECKPOINT = "ckpt_koniq10k.pt"
# Fill in once the checkpoint is hosted somewhere pinned: (size in bytes, sha256). Empty means "present and non-empty" is all we can check.
MANIQA_DIGEST: tuple[int, str] | None = None

QREALIGN_REPO = "q-future/Q-ReAlign-Mini-0.8B"
QREALIGN_REVISION = "fe1f45a7574c9e9d908875af9f7e90cb946aa19f"

_QREALIGN_DIGESTS = {
    "chat_template.jinja": FileDigest(7755, "273d8e0e683b885071fb17e08d71e5f2a5ddfb5309756181681de4f5a1822d80"),
    "config.json": FileDigest(2715, "9f42bdada5ef7d81dc665b7ea431f79aee008d0a29381d5558d46a81bb847f16"),
    "generation_config.json": FileDigest(137, "bffe96ad14b0ef7e35234d384f4474d66317ddc84c87394de26e8f6fef69cbc1"),
    "model.safetensors": FileDigest(2214590296, "bde34df0375fff90d2dee716a127039c57d310c0c868b9f52f4fc2d1ead34aac"),
    "preprocessor_config.json": FileDigest(390, "27225450ac9c6529872ee1924fcb0962ff5634834f817040f444118116f4e516"),
    "processor_config.json": FileDigest(1300, "14932921ca485d458a04dafd8069fbb0a4505622a48208d19ed247115801385b"),
    "tokenizer.json": FileDigest(19989343, "87a7830d63fcf43bf241c3c5242e96e62dd3fdc29224ca26fed8ea333db72de4"),
    "tokenizer_config.json": FileDigest(1139, "e98f1901ac6f0adff67b1d540bfa0c36ac1a0cf59eb72ed78146ef89aafa1182"),
}


def register_bundles() -> None:
    MODEL_BUNDLES.setdefault(
        MANIQA.bundle_key,
        ModelBundle(
            key=MANIQA.bundle_key,
            name="MANIQA image quality ranker",
            # No pinned source yet; set these env vars to a Hugging Face repo and commit that hold ckpt_koniq10k.pt.
            repo_id="",
            revision="",
            filenames=(MANIQA_CHECKPOINT,),
            install_parts=("Rankers", "MANIQA"),
            approx_mb=543,
            pinned_revision=False,
            dir_env="IMAGE_TRIAGE_MANIQA_MODEL_DIR",
            repo_env="IMAGE_TRIAGE_MANIQA_MODEL_REPO_ID",
            revision_env="IMAGE_TRIAGE_MANIQA_MODEL_REVISION",
        ),
    )
    MODEL_BUNDLES.setdefault(
        QREALIGN.bundle_key,
        ModelBundle(
            key=QREALIGN.bundle_key,
            name="Q-ReAlign aesthetic ranker",
            repo_id=QREALIGN_REPO,
            revision=QREALIGN_REVISION,
            filenames=tuple(sorted(_QREALIGN_DIGESTS)),
            install_parts=("Rankers", "Q-ReAlign-Mini-0.8B"),
            approx_mb=2130,
            dir_env="IMAGE_TRIAGE_QREALIGN_MODEL_DIR",
            repo_env="IMAGE_TRIAGE_QREALIGN_MODEL_REPO_ID",
            revision_env="IMAGE_TRIAGE_QREALIGN_MODEL_REVISION",
        ),
    )
    MODEL_FILE_DIGESTS.setdefault(QREALIGN.bundle_key, dict(_QREALIGN_DIGESTS))
    if MANIQA_DIGEST is not None:
        MODEL_FILE_DIGESTS.setdefault(MANIQA.bundle_key, {MANIQA_CHECKPOINT: FileDigest(*MANIQA_DIGEST)})


def ensure_weights(
    spec: RankerSpec,
    *,
    download: bool = True,
    progress_callback: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path:
    """The verified install directory for ``spec``. Downloads when allowed and needed.

    Raises ``RankerWeightsMissing`` with a plain message when the weights are absent and cannot be fetched.
    Download cancellation raises ``ai_model_store.ModelInstallCancelled``; callers map it to ``RankerCancelled``.
    """
    from ... import ai_model_store

    register_bundles()
    status = ai_model_store.bundle_status(spec.bundle_key)
    if status.is_ready:
        return status.install_dir
    if not download:
        raise RankerWeightsMissing(
            f"{spec.name}: {status.describe()}",
            remediation=f"Download the {spec.name} model, then run the analysis again.",
        )
    if not ai_model_store.bundle_repo_id(spec.bundle_key):
        raise RankerWeightsMissing(
            f"{spec.name} has no download source configured and is not installed ({status.describe()}).",
            remediation=(
                f"Copy {MANIQA_CHECKPOINT} into {status.install_dir}, or set "
                "IMAGE_TRIAGE_MANIQA_MODEL_REPO_ID and IMAGE_TRIAGE_MANIQA_MODEL_REVISION to a repository that hosts it."
            ),
        )
    try:
        installed = ai_model_store.install_bundle(
            spec.bundle_key, progress_callback=progress_callback, cancel_check=cancel_check
        )
    except ai_model_store.ModelInstallError as exc:
        raise RankerWeightsMissing(
            f"{spec.name} could not be downloaded: {exc}",
            remediation="Check the network connection and free disk space, then try again.",
        ) from exc
    return installed.install_dir


def maniqa_checkpoint_path(model_dir: Path) -> Path:
    return model_dir / MANIQA_CHECKPOINT


