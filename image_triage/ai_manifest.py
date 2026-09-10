"""The versioned description of everything the AI features need.

One declaration each of: which managed model bundles exist, which files they
contain (with expected size and SHA-256), which Python modules a capability must
be able to import, which model bundles it needs, and which worker protocol it
speaks.

Settings, the feature panes, the installer and the repair path all read this
module. Nothing else may define "what AI needs" — see
``docs/ai_runtime_failure_map.md`` (root cause C).
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Bumped when the *shape* of the manifest changes in a way that invalidates
# metadata written by an older build.
AI_MANIFEST_VERSION = 1


@dataclass(frozen=True)
class FileDigest:
    """The exact bytes a managed model file must contain."""

    size: int
    sha256: str


@dataclass(frozen=True)
class ModelBundle:
    """One atomically-installed set of model files from one repo revision."""

    key: str
    name: str
    repo_id: str
    revision: str
    filenames: tuple[str, ...]
    install_parts: tuple[str, ...]
    approx_mb: int
    pinned_revision: bool = True
    dir_env: str = ""
    repo_env: str = ""
    revision_env: str = ""

    @property
    def is_revision_pinned(self) -> bool:
        """Whether ``revision`` names an immutable commit rather than a branch."""
        revision = self.revision.strip().lower()
        return (
            self.pinned_revision
            and len(revision) == 40
            and all(character in "0123456789abcdef" for character in revision)
        )


@dataclass(frozen=True)
class Capability:
    """A user-facing AI feature and everything required to make it work."""

    key: str
    name: str
    summary: str
    modules: tuple[str, ...]
    model_bundles: tuple[str, ...] = ()
    requires_torch: bool = False
    requires_onnx: bool = False
    worker_module: str = ""
    protocol_version: int = 1
    optional: bool = True
    transformers_symbols: tuple[str, ...] = ()
    remediation: str = "Open Settings and run Set Up AI, then use Repair AI."
    probe_kind: str = "import"
    probe_detail: dict[str, str] = field(default_factory=dict)
    # Output names the probe requires the model to expose, and the vector
    # width stored data is already keyed to. A drift in either would give
    # plausible-looking but incomparable results.
    expected_outputs: tuple[str, ...] = ()
    expected_embedding_dim: int = 0


# --------------------------------------------------------------------------
# Model bundles
# --------------------------------------------------------------------------
# ``install_parts`` is relative to ``ai_paths.managed_models_root()``. The layout
# mirrors the previous cache so migration is a directory move, not a rewrite.

MODEL_BUNDLES: dict[str, ModelBundle] = {
    "dino": ModelBundle(
        key="dino",
        name="DINOv3 image features",
        repo_id="Skulleton12/DinoV3",
        revision="2372da520e9da0b79430d18c8f038de0e8e3ba68",
        filenames=("config.json", "model.safetensors"),
        install_parts=("DinoV3",),
        approx_mb=1210,
        dir_env="AICULLING_MODEL_DIR",
        repo_env="AICULLING_MODEL_REPO_ID",
        revision_env="AICULLING_MODEL_REVISION",
    ),
    "clip": ModelBundle(
        key="clip",
        name="CLIP semantic sidecar",
        repo_id="openai/clip-vit-base-patch32",
        revision="3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        filenames=(
            "config.json",
            "preprocessor_config.json",
            "tokenizer_config.json",
            "vocab.json",
            "merges.txt",
            "special_tokens_map.json",
            "pytorch_model.bin",
        ),
        install_parts=("clip-vit-base-patch32",),
        approx_mb=610,
        dir_env="AICULLING_SEMANTIC_MODEL_DIR",
        repo_env="AICULLING_SEMANTIC_MODEL_REPO_ID",
        revision_env="AICULLING_SEMANTIC_MODEL_REVISION",
    ),
    "oneformer": ModelBundle(
        key="oneformer",
        name="OneFormer scene segmentation",
        repo_id="shi-labs/oneformer_ade20k_swin_tiny",
        revision="7fdbe8184c22b28aee60168e5635394bb556588e",
        filenames=(
            "config.json",
            "merges.txt",
            "preprocessor_config.json",
            "pytorch_model.bin",
            "special_tokens_map.json",
            "tokenizer_config.json",
            "vocab.json",
        ),
        install_parts=("oneformer_ade20k_swin_tiny",),
        approx_mb=196,
        dir_env="IMAGE_TRIAGE_SEGMENTATION_MODEL_DIR",
        repo_env="IMAGE_TRIAGE_SEGMENTATION_MODEL_REPO_ID",
        revision_env="IMAGE_TRIAGE_SEGMENTATION_MODEL_REVISION",
    ),
    "birefnet": ModelBundle(
        key="birefnet",
        name="BiRefNet subject matting",
        repo_id="ZhengPeng7/BiRefNet",
        revision="e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4",
        filenames=(
            "BiRefNet_config.py",
            "birefnet.py",
            "config.json",
            "model.safetensors",
        ),
        install_parts=("Editor", "BiRefNet"),
        approx_mb=425,
        dir_env="IMAGE_TRIAGE_BIREFNET_MODEL_DIR",
        repo_env="IMAGE_TRIAGE_BIREFNET_MODEL_REPO_ID",
        revision_env="IMAGE_TRIAGE_BIREFNET_MODEL_REVISION",
    ),
    "sam": ModelBundle(
        key="sam",
        name="SAM 2.1 click selection",
        repo_id="facebook/sam2.1-hiera-tiny",
        revision="de431c4043854a71d8101e17995dfe596bf101a5",
        filenames=(
            "config.json",
            "model.safetensors",
            "preprocessor_config.json",
            "processor_config.json",
            "video_preprocessor_config.json",
        ),
        install_parts=("Editor", "sam2.1-hiera-tiny"),
        approx_mb=150,
        dir_env="IMAGE_TRIAGE_SAM_MODEL_DIR",
        repo_env="IMAGE_TRIAGE_SAM_MODEL_REPO_ID",
        revision_env="IMAGE_TRIAGE_SAM_MODEL_REVISION",
    ),
    "depth": ModelBundle(
        key="depth",
        name="Depth Anything V2 (small)",
        repo_id="depth-anything/Depth-Anything-V2-Small-hf",
        # Pinned to the commit that ``main`` resolved to when the digests below
        # were captured; a moving branch cannot be verified.
        revision="5426e4f0f36572d16453bbda7a8389317b1bef99",
        filenames=("config.json", "model.safetensors", "preprocessor_config.json"),
        install_parts=("Editor", "Depth-Anything-V2-Small-hf"),
        approx_mb=100,
        dir_env="IMAGE_TRIAGE_DEPTH_MODEL_DIR",
        repo_env="IMAGE_TRIAGE_DEPTH_MODEL_REPO_ID",
        revision_env="IMAGE_TRIAGE_DEPTH_MODEL_REVISION",
    ),
    "tinyclip": ModelBundle(
        key="tinyclip",
        name="TinyCLIP text scoring",
        repo_id="onnx-community/TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX",
        revision="9463a9c508a344c837ffefe9d724f3827bf2dc79",
        filenames=("tokenizer.json", "onnx/model.onnx"),
        install_parts=("CLI-Culler", "Clip", "TinyCLIP-ViT-8M-16-Text-3M-YFCC15M-ONNX"),
        approx_mb=98,
        dir_env="IMAGE_TRIAGE_AICULLER_CLIP_MODEL_DIR",
        repo_env="IMAGE_TRIAGE_AICULLER_CLIP_MODEL_REPO_ID",
        revision_env="IMAGE_TRIAGE_AICULLER_CLIP_MODEL_REVISION",
    ),
    "topiq": ModelBundle(
        key="topiq",
        name="TOPIQ quality scoring",
        repo_id="Skulleton12/TOPIQ",
        revision="56526fd721537c9abd4ec41b10b2ffcad5166c46",
        filenames=("topiq_nr.onnx",),
        install_parts=("CLI-Culler", "TOPIQ"),
        approx_mb=185,
        dir_env="IMAGE_TRIAGE_AICULLER_TOPIQ_MODEL_DIR",
        repo_env="IMAGE_TRIAGE_AICULLER_TOPIQ_MODEL_REPO_ID",
        revision_env="IMAGE_TRIAGE_AICULLER_TOPIQ_MODEL_REVISION",
    ),
    "faces": ModelBundle(
        key="faces",
        name="AuraFace detection and recognition",
        repo_id="fal/AuraFace-v1",
        revision="af6d057c9b0ec4071d4c49c80e3539258798b609",
        filenames=(
            "scrfd_10g_bnkps.onnx",
            "2d106det.onnx",
            "genderage.onnx",
            "glintr100.onnx",
        ),
        install_parts=("CLI-Culler", "faces", "models", "auraface"),
        approx_mb=285,
        dir_env="IMAGE_TRIAGE_AICULLER_FACE_MODEL_DIR",
        repo_env="IMAGE_TRIAGE_AICULLER_FACE_MODEL_REPO_ID",
        revision_env="IMAGE_TRIAGE_AICULLER_FACE_MODEL_REVISION",
    ),
}


# --------------------------------------------------------------------------
# Expected bytes for every managed model file
# --------------------------------------------------------------------------
# Regenerate with ``python scripts/refresh_ai_model_manifest.py``. Values come
# from the Hugging Face metadata API: LFS files carry a SHA-256 ``oid``
# directly, smaller files are downloaded once and hashed.

MODEL_FILE_DIGESTS: dict[str, dict[str, FileDigest]] = {
    "dino": {
        "config.json": FileDigest(
            745, "135ecd23e34a70b6fbed8b083fdecb319b7e3a54e3d849258bbe4ddcf1783bb5"
        ),
        "model.safetensors": FileDigest(
            1212559808, "dcb2e45127cccbf1601e5f42fef165eea275c8e5213197e8dcf3f48822718179"
        ),
    },
    "clip": {
        "config.json": FileDigest(
            4186, "b575ef3c36f2a057fa19e221650105052d61cc9c1a972ec15019c6261ec98770"
        ),
        "preprocessor_config.json": FileDigest(
            316, "910e70b3956ac9879ebc90b22fb3bc8a75b6a0677814500101a4c072bd7857bd"
        ),
        "tokenizer_config.json": FileDigest(
            592, "34b7336e4bee12e0a9730eaf5189f582ef3c3eea5027f65730e5717256755aad"
        ),
        "vocab.json": FileDigest(
            862328, "5047b556ce86ccaf6aa22b3ffccfc52d391ea4accdab9c2f2407da5b742d4363"
        ),
        "merges.txt": FileDigest(
            524657, "f526393189112391ce6f9795d4695f704121ce452c3aad1f5335cc41337eba85"
        ),
        "special_tokens_map.json": FileDigest(
            389, "f8c0d6c39aee3f8431078ef6646567b0aba7f2246e9c54b8b99d55c22b707cbf"
        ),
        "pytorch_model.bin": FileDigest(
            605247071, "a63082132ba4f97a80bea76823f544493bffa8082296d62d71581a4feff1576f"
        ),
    },
    "oneformer": {
        "config.json": FileDigest(
            84284, "091cbc7c980128ae63b2a15d882923f326f85926ef163adad00c24bd90228896"
        ),
        "merges.txt": FileDigest(
            524619, "9fd691f7c8039210e0fced15865466c65820d09b63988b0174bfe25de299051a"
        ),
        "preprocessor_config.json": FileDigest(
            8709, "2c3c403d8414263e732996bb2ffeab80dd5ced0068ab11bfe5adf476ef75823c"
        ),
        "pytorch_model.bin": FileDigest(
            203389501, "909b07dbf4129c2bbb8df4498e35dcd46f305e3ec45329d3ff6d4f0360de27f3"
        ),
        "special_tokens_map.json": FileDigest(
            472, "c4864a9376a8401918425bed71fc14fc0e81f9b59ec45c1cf96cccb2df508eac"
        ),
        "tokenizer_config.json": FileDigest(
            807, "64dd88e64d791e3be4d38be62d7e77e0a24df9e79205ac740af505aa2e94c367"
        ),
        "vocab.json": FileDigest(
            1059962, "e089ad92ba36837a0d31433e555c8f45fe601ab5c221d4f607ded32d9f7a4349"
        ),
    },
    "birefnet": {
        "BiRefNet_config.py": FileDigest(
            298, "e7b8c2a74f6cea6a59553d517f71d47f2c1d90e670a13416af17c25fe2f3dc52"
        ),
        "birefnet.py": FileDigest(
            91896, "208771ae626f653d64128fbf2d6ac9f8e645c5cc5e286258a73ec3322bbfe5ef"
        ),
        "config.json": FileDigest(
            405, "c97ea21569daf66b205491a4635147dd3bc42c7c168b89d7d75b53f67ef548ae"
        ),
        "model.safetensors": FileDigest(
            444473596, "9ab37426bf4de0567af6b5d21b16151357149139362e6e8992021b8ce356a154"
        ),
    },
    "sam": {
        "config.json": FileDigest(
            5695, "860aff9751b139d83a4ad7df1e5535416fded533e0ead02625edbefcb9953cce"
        ),
        "model.safetensors": FileDigest(
            155908064, "48c14467e5cf9e51870511feb72c89688e82dd74523142c0538b663e193ac2a7"
        ),
        "preprocessor_config.json": FileDigest(
            683, "6ebf229ee259368ce4a8d4f2fe893a72b053023710853e257253939e601f583d"
        ),
        "processor_config.json": FileDigest(
            95, "f8a68e865cfad115c1c2763f3d93eca7b1c622da06da2a9273eb437fb2389b6d"
        ),
        "video_preprocessor_config.json": FileDigest(
            705, "9fccfe5f464ec38c2f236d0e6a68e95511c80c22132fc2fa4b9f7b65f24fad95"
        ),
    },
    "depth": {
        "config.json": FileDigest(
            950, "c56698d3643dde1f83ea2212759e6b31a22b8f827246a36dd007ee8a22b3ff75"
        ),
        "model.safetensors": FileDigest(
            99173660, "3152477ce0d8d6978d76b995120de97cb5b928701fd0f817769f59e249a16b70"
        ),
        "preprocessor_config.json": FileDigest(
            775, "d41175c0d889477ca8fc67191e540faef14baf6275157b3fdecf78469e6bbf84"
        ),
    },
    "tinyclip": {
        "tokenizer.json": FileDigest(
            3642073, "6d9109cc838977f3ca94a379eec36aecc7c807e1785cd729660ca2fc0171fb35"
        ),
        "onnx/model.onnx": FileDigest(
            94071688, "31d28cb07209533d10fc4fef73ac324ce17de6741a2372e7e1531a4ac8fdaeb2"
        ),
    },
    "topiq": {
        "topiq_nr.onnx": FileDigest(
            176676402, "cb0e2df4633d968c11ea3f1a6a0392dd1482068370788de51d91bf434c453224"
        ),
    },
    "faces": {
        "scrfd_10g_bnkps.onnx": FileDigest(
            16923827, "5838f7fe053675b1c7a08b633df49e7af5495cee0493c7dcf6697200b85b5b91"
        ),
        "2d106det.onnx": FileDigest(
            5030888, "f001b856447c413801ef5c42091ed0cd516fcd21f2d6b79635b1e733a7109dbf"
        ),
        "genderage.onnx": FileDigest(
            1322532, "4fde69b1c810857b88c64a335084f1c3fe8f01246c9a191b48c7bb756d6652fb"
        ),
        "glintr100.onnx": FileDigest(
            260694151, "a7933ea5330113b01c9b60351d8f4c33003f145d8470ac5f0e52ee2effe25c60"
        ),
    },
}


def bundle_digests(bundle_key: str) -> dict[str, FileDigest]:
    """Expected bytes for one bundle, keyed by relative filename."""
    return dict(MODEL_FILE_DIGESTS.get(bundle_key, {}))


def bundle_expected_sha256(bundle_key: str) -> dict[str, str]:
    """SHA-256 map in the shape ``AIModelInstallation.expected_sha256`` wants."""
    return {
        filename: digest.sha256
        for filename, digest in bundle_digests(bundle_key).items()
        if digest.sha256
    }


def unverified_bundles() -> tuple[str, ...]:
    """Bundles with at least one file that carries no expected SHA-256.

    Surfaced in diagnostics so an unverifiable download is a known, reported
    risk rather than a silent one.
    """
    unverified: list[str] = []
    for key, bundle in MODEL_BUNDLES.items():
        digests = bundle_digests(key)
        if any(not digests.get(name, FileDigest(0, "")).sha256 for name in bundle.filenames):
            unverified.append(key)
    return tuple(unverified)


def unpinned_bundles() -> tuple[str, ...]:
    """Bundles whose revision is not an immutable commit id."""
    return tuple(key for key, bundle in MODEL_BUNDLES.items() if not bundle.is_revision_pinned)


# --------------------------------------------------------------------------
# Capabilities
# --------------------------------------------------------------------------

_TORCH_MASK_MODULES = ("torch", "transformers", "safetensors", "PIL", "numpy")

CAPABILITIES: dict[str, Capability] = {
    "culling": Capability(
        key="culling",
        name="AI culling",
        summary="Scores and ranks a folder so keepers surface first.",
        modules=("numpy", "onnxruntime", "cv2", "PIL", "sklearn"),
        requires_onnx=True,
        optional=False,
        probe_kind="onnx_providers",
        remediation="Open Settings and run Set Up AI to install the base AI runtime.",
    ),
    "text_scoring": Capability(
        key="text_scoring",
        name="Text scoring (TinyCLIP)",
        summary="Scores photos against text prompts during culling.",
        modules=("numpy", "onnxruntime", "tokenizers"),
        model_bundles=("tinyclip",),
        requires_onnx=True,
        probe_kind="onnx_model",
        probe_detail={"bundle": "tinyclip", "file": "onnx/model.onnx"},
        expected_outputs=("text_embeds", "image_embeds"),
        expected_embedding_dim=512,
    ),
    "quality_topiq": Capability(
        key="quality_topiq",
        name="Quality scoring (TOPIQ)",
        summary="Perceptual sharpness and quality score per photo.",
        modules=("numpy", "onnx", "onnxruntime"),
        model_bundles=("topiq",),
        requires_onnx=True,
        probe_kind="onnx_model",
        probe_detail={
            "bundle": "topiq",
            "file": "topiq_nr.onnx",
            # TOPIQ's published export has a malformed conv bias that cuDNN
            # rejects; the product rewrites it before use, so the probe must too.
            "prepare": "topiq_cuda",
        },
        expected_outputs=("quality_score",),
    ),
    "faces": Capability(
        key="faces",
        name="Faces and people",
        summary="Face detection, face quality and named-person grouping.",
        modules=("numpy", "onnxruntime", "cv2", "insightface"),
        model_bundles=("faces",),
        requires_onnx=True,
        probe_kind="insightface",
    ),
    "semantic_search": Capability(
        key="semantic_search",
        name="Semantic search",
        summary="Find photos by describing them.",
        modules=("numpy", "onnxruntime", "tokenizers"),
        model_bundles=("tinyclip",),
        requires_onnx=True,
        probe_kind="onnx_model",
        probe_detail={"bundle": "tinyclip", "file": "onnx/model.onnx"},
        expected_outputs=("text_embeds", "image_embeds"),
        expected_embedding_dim=512,
    ),
    "dino": Capability(
        key="dino",
        name="DINO image features",
        summary="Deep image embeddings for grouping near-duplicates.",
        modules=("torch", "torchvision", "timm", "transformers", "safetensors"),
        model_bundles=("dino",),
        requires_torch=True,
        probe_kind="torch",
        transformers_symbols=("AutoImageProcessor", "AutoModel"),
    ),
    "scene_masks": Capability(
        key="scene_masks",
        name="Scene selection (OneFormer)",
        summary="Editor masks for sky, skin, foliage and other scene regions.",
        modules=_TORCH_MASK_MODULES,
        model_bundles=("oneformer",),
        requires_torch=True,
        worker_module="image_triage.oneformer_worker",
        probe_kind="torch",
        transformers_symbols=("OneFormerForUniversalSegmentation", "OneFormerProcessor"),
    ),
    "subject_masks": Capability(
        key="subject_masks",
        name="Subject selection (BiRefNet)",
        summary="Editor masks that isolate the main subject.",
        # BiRefNet loads its own modeling code, which imports einops and
        # kornia directly; without them the model fails at load time.
        modules=(*_TORCH_MASK_MODULES, "timm", "cv2", "einops", "kornia"),
        model_bundles=("birefnet",),
        requires_torch=True,
        worker_module="image_triage.birefnet_worker",
        probe_kind="torch",
        transformers_symbols=("AutoModelForImageSegmentation",),
    ),
    "sam_masks": Capability(
        key="sam_masks",
        name="Click selection (SAM 2.1)",
        summary="Editor masks from a click or box on the photo.",
        modules=_TORCH_MASK_MODULES,
        model_bundles=("sam",),
        requires_torch=True,
        worker_module="image_triage.sam_worker",
        probe_kind="torch",
        transformers_symbols=("Sam2Model", "Sam2Processor"),
    ),
    "depth": Capability(
        key="depth",
        name="Depth estimation",
        summary="Depth maps behind depth-aware editor tools.",
        modules=_TORCH_MASK_MODULES,
        model_bundles=("depth",),
        requires_torch=True,
        worker_module="image_triage.depth_worker",
        probe_kind="torch",
        transformers_symbols=("AutoImageProcessor", "AutoModelForDepthEstimation"),
    ),
}


# Capabilities that need only the compact ONNX runtime.
BASE_CAPABILITIES: tuple[str, ...] = (
    "culling",
    "text_scoring",
    "quality_topiq",
    "faces",
    "semantic_search",
)
# Capabilities the PyTorch runtime adds. DINO is deliberately excluded: the
# active culling pipeline is ONNX-based and the DINO weights are 1.2 GB, so it
# stays opt-in and is not part of what Set Up AI installs or verifies.
TORCH_CAPABILITIES: tuple[str, ...] = (
    "scene_masks",
    "subject_masks",
    "sam_masks",
    "depth",
)
OPT_IN_CAPABILITIES: tuple[str, ...] = ("dino",)


def setup_capabilities(*, include_torch: bool) -> tuple[str, ...]:
    """Exactly what Set Up AI installs, and therefore what it verifies."""
    return BASE_CAPABILITIES + (TORCH_CAPABILITIES if include_torch else ())


# Capabilities the "AI setup" flow installs by default, in report order.
DEFAULT_CAPABILITY_ORDER: tuple[str, ...] = (
    "culling",
    "text_scoring",
    "quality_topiq",
    "faces",
    "semantic_search",
    "dino",
    "scene_masks",
    "subject_masks",
    "sam_masks",
    "depth",
)


def capability(key: str) -> Capability:
    try:
        return CAPABILITIES[key]
    except KeyError as exc:  # pragma: no cover - programming error
        raise KeyError(f"Unknown AI capability {key!r}") from exc


__all__ = [
    "AI_MANIFEST_VERSION",
    "BASE_CAPABILITIES",
    "CAPABILITIES",
    "DEFAULT_CAPABILITY_ORDER",
    "OPT_IN_CAPABILITIES",
    "TORCH_CAPABILITIES",
    "MODEL_BUNDLES",
    "MODEL_FILE_DIGESTS",
    "Capability",
    "FileDigest",
    "ModelBundle",
    "bundle_digests",
    "bundle_expected_sha256",
    "capability",
    "setup_capabilities",
    "unpinned_bundles",
    "unverified_bundles",
]
