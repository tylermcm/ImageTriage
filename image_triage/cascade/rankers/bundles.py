"""Managed model bundles for the rankers, installed through ``ai_model_store`` (hash verified, atomic, on demand; never bundled).

``ai_manifest`` is the app's one list of model bundles. Until these two entries move into it, ``register_bundles`` adds them to its
registries at runtime (idempotent) so the model store's install, status and repair paths work unchanged.

Both files are hosted on the owner's Hugging Face account and pinned to the commits below; a new upload means a new pin (and a new digest here).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...ai_manifest import MODEL_BUNDLES, MODEL_FILE_DIGESTS, FileDigest, ModelBundle
from .errors import RankerWeightsMissing
from .spec import BLINK, MANIQA, QREALIGN, RankerSpec

# Where the two files that have no upstream home we can pin are hosted. Upload the exact files listed in docs/ai_v5_model_hosting.md to these Hugging Face
# repositories (or change the names here); the digests below make any host safe, because a file that does not match is refused.
MANIQA_REPO = "Skulleton12/maniqa-koniq10k"
MANIQA_REVISION = "18bbbb8a87d06d7066de3fb0412139520fd6ff91"
BLINK_REPO = "Skulleton12/mediapipe-face-landmarker"
BLINK_REVISION = "e8098a34013a130b8d8c88de0772f162b450dfad"

MANIQA_CHECKPOINT = "ckpt_koniq10k.pt"
# The KonIQ-10k checkpoint from the MANIQA GitHub release (https://github.com/IIGROUP/MANIQA/releases/download/Koniq10k/ckpt_koniq10k.pt).
MANIQA_DIGEST: tuple[int, str] | None = (543335435, "a207f8ab57322e6be38ff5c8d019301dc032b454bef21c3c9f9dbf7974eebff6")

BLINK_MODEL_FILE = "face_landmarker.task"
# Google's MediaPipe Face Landmarker bundle (float16), https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task
BLINK_DIGEST: tuple[int, str] = (3758596, "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff")

# The face recognizer for the People index: AdaFace IR-50 (MIT code; the file is UniFace's ONNX export), from the UniFace weights repository at a pinned commit.
FACES_IDENTITY_KEY = "faces_identity"
FACES_IDENTITY_REPO = "yakhyo/uniface-weights"
FACES_IDENTITY_REVISION = "bfcc7d48ea8412d55de406cecf96b65b3ca720b2"
FACES_IDENTITY_FILE = "adaface_ir_50.onnx"
FACES_IDENTITY_DIGEST: tuple[int, str] = (174396180, "9c0ae385d7362323c92d218de598d32e52a9130b78faac6a8a924091aca62c0b")

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
            repo_id=MANIQA_REPO,
            revision=MANIQA_REVISION,
            filenames=(MANIQA_CHECKPOINT,),
            install_parts=("Rankers", "MANIQA"),
            approx_mb=543,
            pinned_revision=True,
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
    MODEL_BUNDLES.setdefault(
        BLINK.bundle_key,
        ModelBundle(
            key=BLINK.bundle_key,
            name="Face landmarks for the blink detector",
            repo_id=BLINK_REPO,
            revision=BLINK_REVISION,
            filenames=(BLINK_MODEL_FILE,),
            install_parts=("Rankers", "Face landmarker"),
            approx_mb=4,
            pinned_revision=True,
            dir_env="IMAGE_TRIAGE_BLINK_MODEL_DIR",
            repo_env="IMAGE_TRIAGE_BLINK_MODEL_REPO_ID",
            revision_env="IMAGE_TRIAGE_BLINK_MODEL_REVISION",
        ),
    )
    MODEL_BUNDLES.setdefault(
        FACES_IDENTITY_KEY,
        ModelBundle(
            key=FACES_IDENTITY_KEY,
            name="Face recognizer for people grouping (AdaFace)",
            repo_id=FACES_IDENTITY_REPO,
            revision=FACES_IDENTITY_REVISION,
            filenames=(FACES_IDENTITY_FILE,),
            install_parts=("Rankers", "AdaFace"),
            approx_mb=166,
            pinned_revision=True,
            dir_env="IMAGE_TRIAGE_FACES_MODEL_DIR",
            repo_env="IMAGE_TRIAGE_FACES_MODEL_REPO_ID",
            revision_env="IMAGE_TRIAGE_FACES_MODEL_REVISION",
        ),
    )
    MODEL_FILE_DIGESTS.setdefault(FACES_IDENTITY_KEY, {FACES_IDENTITY_FILE: FileDigest(*FACES_IDENTITY_DIGEST)})
    MODEL_FILE_DIGESTS.setdefault(BLINK.bundle_key, {BLINK_MODEL_FILE: FileDigest(*BLINK_DIGEST)})
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
    return _ensure_bundle(spec.name, spec.bundle_key, download, progress_callback, cancel_check)


def ensure_aux_weights(
    spec: RankerSpec,
    *,
    download: bool = True,
    progress_callback: Callable[[str, int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> Path | None:
    """The install directory of ``spec``'s second bundle (see ``RankerSpec.aux_bundle_key``), or None when it has none."""
    if not spec.aux_bundle_key:
        return None
    return _ensure_bundle(spec.name, spec.aux_bundle_key, download, progress_callback, cancel_check)


def _ensure_bundle(
    name: str,
    bundle_key: str,
    download: bool,
    progress_callback: Callable[[str, int, int], None] | None,
    cancel_check: Callable[[], bool] | None,
) -> Path:
    from ... import ai_model_store

    register_bundles()
    status = ai_model_store.bundle_status(bundle_key)
    if status.is_ready:
        return status.install_dir
    if not download:
        raise RankerWeightsMissing(
            f"{name}: {status.describe()}",
            remediation=f"Download the {name} model, then run the analysis again.",
        )
    if not ai_model_store.bundle_repo_id(bundle_key):
        raise RankerWeightsMissing(
            f"{name} has no download source configured and is not installed ({status.describe()}).",
            remediation=f"Copy the model file into {status.install_dir}, or set the IMAGE_TRIAGE_*_MODEL_REPO_ID environment variable for it.",
        )
    try:
        installed = ai_model_store.install_bundle(
            bundle_key, progress_callback=progress_callback, cancel_check=cancel_check
        )
    except ai_model_store.ModelInstallError as exc:
        raise RankerWeightsMissing(
            f"{name} could not be downloaded: {exc}",
            remediation="Check the network connection and free disk space, then try again.",
        ) from exc
    return installed.install_dir


def maniqa_checkpoint_path(model_dir: Path) -> Path:
    return model_dir / MANIQA_CHECKPOINT


register_bundles()  # whichever of this module and ai_manifest is imported first, the manifest ends up with every bundle
