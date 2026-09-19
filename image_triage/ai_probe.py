"""Capability probes that run *inside* the managed AI runtime.

Everything here executes in the isolated validator subprocess launched by
``ai_health``, with the selected profile's ``site-packages`` on ``sys.path`` and
its DLL directories registered. Nothing in this module may import Qt or any
other part of the application, because the frozen probe process does not have
them.

An import-only check is not enough: ONNX Runtime imports cleanly on a machine
with no CUDA provider, and Transformers imports cleanly against a model
directory it cannot actually load. Each probe therefore does the cheapest piece
of *real* work that proves the capability, and reports what it actually got —
provider list, torch CUDA state, ONNX input/output signature — not what was
requested.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path


PROBE_PROTOCOL_VERSION = 1

STAGE_PATH = "path"
STAGE_IMPORT = "import"
STAGE_PROVIDER = "provider"
STAGE_MODEL = "model"
STAGE_INFERENCE = "inference"
STAGE_COMPLETE = "complete"


@dataclass
class ProbeResult:
    """What one capability probe observed. Serialized as JSON to the parent."""

    capability: str
    ok: bool = False
    stage: str = STAGE_PATH
    category: str = ""
    message: str = ""
    detail: str = ""
    protocol_version: int = PROBE_PROTOCOL_VERSION
    profile: str = ""
    site_packages: str = ""
    requested_device: str = "auto"
    selected_device: str = ""
    modules: dict[str, str] = field(default_factory=dict)
    module_paths: dict[str, str] = field(default_factory=dict)
    providers_available: list[str] = field(default_factory=list)
    providers_active: list[str] = field(default_factory=list)
    torch_cuda_available: bool = False
    torch_device_name: str = ""
    model_dir: str = ""
    model_config: str = ""
    model_prepared: str = ""
    inference_ran: bool = False
    probe_level: str = "quick"
    model_inputs: list[str] = field(default_factory=list)
    model_outputs: list[str] = field(default_factory=list)
    embedding_dim: int = 0
    duration_ms: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


class ProbeFailure(Exception):
    """A probe determined the capability is unusable, with a specific reason."""

    def __init__(self, stage: str, category: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.stage = stage
        self.category = category
        self.message = message
        self.detail = detail


def configure_runtime_path(site_packages: Path) -> None:
    """Put one managed profile first on ``sys.path`` and register its DLL dirs.

    Deliberately raises rather than swallowing: a probe that silently ran
    against the wrong packages is worse than no probe at all.
    """
    target = Path(site_packages).resolve()
    if not target.is_dir():
        raise ProbeFailure(
            STAGE_PATH,
            "runtime_missing",
            f"The managed AI runtime directory does not exist: {target}",
        )
    text = str(target)
    while text in sys.path:
        sys.path.remove(text)
    sys.path.insert(0, text)
    importlib.invalidate_caches()

    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        dll_dirs = [target, target / "torch" / "lib", target / "onnxruntime" / "capi"]
        dll_dirs.extend(sorted(target.glob("*.libs")))
        for dll_dir in dll_dirs:
            if not dll_dir.is_dir():
                continue
            try:
                os.add_dll_directory(str(dll_dir))
            except OSError:
                continue
            existing = os.environ.get("PATH", "")
            parts = existing.split(os.pathsep) if existing else []
            if str(dll_dir) not in parts:
                os.environ["PATH"] = os.pathsep.join([str(dll_dir), *parts])


def import_modules(module_names: tuple[str, ...], site_packages: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Import each module and prove it came from the managed profile."""
    target = Path(site_packages).resolve()
    versions: dict[str, str] = {}
    locations: dict[str, str] = {}
    for name in module_names:
        try:
            module = importlib.import_module(name)
        except ImportError as exc:
            raise ProbeFailure(
                STAGE_IMPORT,
                "package_missing",
                f"{name} could not be imported from the installed AI runtime.",
                _short_traceback(exc),
            ) from exc
        except Exception as exc:  # a native extension that loads but crashes
            raise ProbeFailure(
                STAGE_IMPORT,
                "package_broken",
                f"{name} is installed but failed to load: {exc}",
                _short_traceback(exc),
            ) from exc
        location = str(Path(getattr(module, "__file__", "") or "").resolve())
        locations[name] = location
        versions[name] = str(getattr(module, "__version__", "") or "")
        if location:
            try:
                Path(location).relative_to(target)
            except ValueError as exc:
                raise ProbeFailure(
                    STAGE_IMPORT,
                    "package_shadowed",
                    f"{name} was loaded from {location} instead of the managed AI runtime. "
                    "Another Python installation is shadowing it.",
                    f"expected under {target}",
                ) from exc
    return versions, locations


def probe_onnx_providers(result: ProbeResult, requested_device: str) -> None:
    """Prove ONNX Runtime works and that the requested provider is real."""
    import onnxruntime  # noqa: PLC0415 - runs inside the managed profile

    available = list(onnxruntime.get_available_providers())
    result.providers_available = available
    wants_gpu = device_family(requested_device) == "cuda"
    has_cuda = "CUDAExecutionProvider" in available
    if wants_gpu and not has_cuda:
        raise ProbeFailure(
            STAGE_PROVIDER,
            "provider_unavailable",
            "The GPU AI runtime is installed but ONNX Runtime has no CUDA provider. "
            "The NVIDIA driver is missing, too old, or the CUDA runtime files did not install.",
            f"available providers: {', '.join(available)}",
        )
    if requested_device == "cpu" or not has_cuda:
        result.selected_device = "cpu"
    else:
        result.selected_device = (
            requested_device if device_family(requested_device) == "cuda" else "cuda"
        )
    if not available:
        raise ProbeFailure(
            STAGE_PROVIDER,
            "provider_unavailable",
            "ONNX Runtime reported no execution providers at all.",
        )


def probe_onnx_model(
    result: ProbeResult,
    model_path: Path,
    requested_device: str,
    *,
    run_inference: bool = True,
    expected_outputs: tuple[str, ...] = (),
    expected_embedding_dim: int = 0,
) -> None:
    """Load the model, run one real inference, and check its output contract.

    Constructing a session proves the file parses; it does not prove the graph
    executes on the selected provider, and it does not catch a model whose
    input or output signature has drifted from what the application expects.
    """
    import numpy as np  # noqa: PLC0415
    import onnxruntime  # noqa: PLC0415

    probe_onnx_providers(result, requested_device)
    if not model_path.is_file():
        raise ProbeFailure(
            STAGE_MODEL,
            "model_missing",
            f"The model file is not installed: {model_path.name}",
            str(model_path),
        )
    providers = ["CPUExecutionProvider"]
    wants_cuda = device_family(result.selected_device) == "cuda"
    if wants_cuda and "CUDAExecutionProvider" in result.providers_available:
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
    options = onnxruntime.SessionOptions()
    options.log_severity_level = 3
    try:
        session = onnxruntime.InferenceSession(str(model_path), options, providers=providers)
    except Exception as exc:
        raise ProbeFailure(
            STAGE_MODEL,
            "model_unloadable",
            f"{model_path.name} is present but ONNX Runtime could not load it. "
            "The file is corrupt or was produced for a different runtime version.",
            _short_traceback(exc),
        ) from exc
    result.providers_active = list(session.get_providers())
    result.model_inputs = [item.name for item in session.get_inputs()]
    result.model_outputs = [item.name for item in session.get_outputs()]
    result.model_dir = str(model_path.parent)

    if "CUDAExecutionProvider" in providers and "CUDAExecutionProvider" not in result.providers_active:
        raise ProbeFailure(
            STAGE_MODEL,
            "provider_fallback",
            "ONNX Runtime silently fell back to the CPU provider after the GPU provider was requested.",
            f"active: {', '.join(result.providers_active)}",
        )

    missing_outputs = [name for name in expected_outputs if name not in result.model_outputs]
    if missing_outputs:
        raise ProbeFailure(
            STAGE_MODEL,
            "model_contract",
            f"{model_path.name} does not expose {', '.join(missing_outputs)}. "
            "This model file does not match the one Image Triage expects.",
            f"outputs: {', '.join(result.model_outputs)}",
        )

    if not run_inference:
        return

    feeds: dict[str, object] = {}
    for item in session.get_inputs():
        try:
            feeds[item.name] = _synthetic_input(np, item)
        except ProbeFailure:
            raise
        except Exception as exc:
            raise ProbeFailure(
                STAGE_INFERENCE,
                "model_contract",
                f"{model_path.name} declares an input ({item.name}) this build cannot supply.",
                _short_traceback(exc),
            ) from exc

    wanted = list(expected_outputs) or [item.name for item in session.get_outputs()]
    try:
        outputs = session.run(wanted, feeds)
    except Exception as exc:
        raise ProbeFailure(
            STAGE_INFERENCE,
            "model_unloadable",
            f"{model_path.name} loaded but could not run. "
            "The execution provider or the model file is unusable on this machine.",
            _short_traceback(exc),
        ) from exc

    result.inference_ran = True
    for array in outputs:
        shape = tuple(getattr(array, "shape", ()) or ())
        if shape:
            result.embedding_dim = int(shape[-1])
            break
    if expected_embedding_dim and result.embedding_dim != expected_embedding_dim:
        raise ProbeFailure(
            STAGE_INFERENCE,
            "model_contract",
            f"{model_path.name} produced {result.embedding_dim}-dimensional output, "
            f"but Image Triage stores {expected_embedding_dim}-dimensional vectors. "
            "Existing search and face data would not be comparable.",
        )


_ONNX_DTYPES = {
    "tensor(float)": "float32",
    "tensor(float16)": "float16",
    "tensor(double)": "float64",
    "tensor(int64)": "int64",
    "tensor(int32)": "int32",
    "tensor(uint8)": "uint8",
    "tensor(bool)": "bool",
}
# Stand-in extents for symbolic dimensions. Batch and sequence stay tiny so the
# probe costs milliseconds; spatial dimensions use a size every vision model in
# the manifest accepts.
_SYMBOLIC_EXTENTS = {0: 1, 1: 3, 2: 224, 3: 224}
_TEXT_SEQUENCE_LENGTH = 8


def _synthetic_input(np, spec) -> object:
    """A minimal, valid tensor for one declared model input."""
    dtype_name = _ONNX_DTYPES.get(str(spec.type))
    if dtype_name is None:
        raise ProbeFailure(
            STAGE_INFERENCE,
            "model_contract",
            f"Unsupported model input type {spec.type} for {spec.name}.",
        )
    name = str(spec.name).lower()
    declared = list(getattr(spec, "shape", []) or [])
    shape: list[int] = []
    for axis, extent in enumerate(declared):
        if isinstance(extent, int) and extent > 0:
            shape.append(extent)
            continue
        if axis == 0:
            shape.append(1)
        elif "ids" in name or "mask" in name or "token" in name:
            shape.append(_TEXT_SEQUENCE_LENGTH)
        else:
            shape.append(_SYMBOLIC_EXTENTS.get(axis, 1))
    if not shape:
        shape = [1]
    if dtype_name in {"int64", "int32"}:
        # Token ids must stay inside the vocabulary; 1 is safe for every
        # tokenizer in the manifest and attention masks want 1 as well.
        return np.ones(shape, dtype=dtype_name)
    if dtype_name == "bool":
        return np.ones(shape, dtype="bool")
    return np.zeros(shape, dtype=dtype_name)


def probe_torch(result: ProbeResult, requested_device: str) -> None:
    """Prove torch loads its native libraries and report real CUDA state."""
    import torch  # noqa: PLC0415

    result.modules["torch"] = str(getattr(torch, "__version__", ""))
    try:
        cuda_available = bool(torch.cuda.is_available())
    except Exception as exc:
        raise ProbeFailure(
            STAGE_PROVIDER,
            "provider_broken",
            f"PyTorch could not query the GPU: {exc}",
            _short_traceback(exc),
        ) from exc
    result.torch_cuda_available = cuda_available
    if cuda_available:
        try:
            result.torch_device_name = str(torch.cuda.get_device_name(0))
        except Exception:
            result.torch_device_name = "unknown CUDA device"
    if requested_device in {"cuda", "gpu"} and not cuda_available:
        raise ProbeFailure(
            STAGE_PROVIDER,
            "provider_unavailable",
            "The GPU AI runtime is installed but PyTorch cannot see a CUDA device. "
            "The NVIDIA driver is missing or too old for this build.",
            f"torch {getattr(torch, '__version__', '?')}",
        )
    if requested_device == "cpu" or not cuda_available:
        result.selected_device = "cpu"
    else:
        # Keep an explicit GPU index: a worker pinned to cuda:1 must be proven
        # on cuda:1, not on the default device.
        result.selected_device = (
            requested_device if device_family(requested_device) == "cuda" else "cuda"
        )
    index = _cuda_index(result.selected_device)
    if index is not None:
        try:
            count = int(torch.cuda.device_count())
        except Exception:
            count = 0
        if index >= count:
            raise ProbeFailure(
                STAGE_PROVIDER,
                "provider_unavailable",
                f"GPU {index} was requested but this machine has {count} CUDA device(s).",
            )
        result.torch_device_name = str(torch.cuda.get_device_name(index))

    # A tiny real operation on the device the workers will use. Running this on
    # the CPU while the worker is pinned to CUDA proves nothing about CUDA.
    try:
        tensor = torch.ones(8, 8, device=result.selected_device)
        value = float((tensor @ tensor).sum().item())
        if device_family(result.selected_device) == "cuda":
            torch.cuda.synchronize(index or 0)
    except Exception as exc:
        raise ProbeFailure(
            STAGE_INFERENCE,
            "package_broken" if result.selected_device == "cpu" else "provider_broken",
            f"PyTorch imported but could not run a basic operation on "
            f"{result.selected_device}. "
            + (
                "The Microsoft Visual C++ runtime may be missing."
                if result.selected_device == "cpu"
                else "The GPU driver may be too old for this build."
            ),
            _short_traceback(exc),
        ) from exc
    if value != 8 * 8 * 8:
        raise ProbeFailure(
            STAGE_INFERENCE,
            "package_broken",
            f"PyTorch produced an incorrect result on {result.selected_device}.",
            f"expected {8 * 8 * 8}, got {value}",
        )
    result.inference_ran = True


def _cuda_index(device: str) -> int | None:
    _prefix, separator, suffix = (device or "").partition(":")
    if not separator:
        return None
    try:
        return int(suffix)
    except ValueError:
        return None


def device_family(device: str) -> str:
    """``cuda`` for any CUDA device including ``cuda:N``; otherwise the value.

    Duplicated from ``ai_env`` on purpose: this module runs inside the managed
    runtime and must not import the application package.
    """
    value = (device or "").strip().lower()
    return "cuda" if value.startswith("cuda") else value


def probe_transformers_classes(symbols: tuple[str, ...]) -> None:
    """Prove the exact Transformers classes the workers use are importable."""
    transformers = importlib.import_module("transformers")
    for symbol in symbols:
        try:
            getattr(transformers, symbol)
        except AttributeError as exc:
            raise ProbeFailure(
                STAGE_IMPORT,
                "package_incompatible",
                f"The installed Transformers build has no {symbol}. "
                "The AI runtime and this version of Image Triage do not match.",
                f"transformers {getattr(transformers, '__version__', '?')}",
            ) from exc


# The exact loader each editor capability uses, mirroring its worker module.
_TORCH_MODEL_LOADERS: dict[str, tuple[str, str, bool]] = {
    # capability -> (processor class or "", model class, trust_remote_code)
    "scene_masks": ("OneFormerProcessor", "OneFormerForUniversalSegmentation", False),
    "subject_masks": ("", "AutoModelForImageSegmentation", True),
    "sam_masks": ("Sam2Processor", "Sam2Model", False),
    "depth": ("AutoImageProcessor", "AutoModelForDepthEstimation", False),
    "dino": ("AutoImageProcessor", "AutoModel", False),
}


def probe_torch_model(
    result: ProbeResult,
    capability_key: str,
    model_dir: Path,
    requested_device: str,
    *,
    load_model: bool,
) -> None:
    """Prove a torch-backed capability can load and run its own model.

    ``load_model=False`` stops after the configuration, which validates the
    model directory and Transformers compatibility in well under a second and
    is what routine UI gating uses. The full load is what Demo Ready and
    post-repair verification run.
    """
    import torch  # noqa: PLC0415
    import transformers  # noqa: PLC0415

    probe_torch(result, requested_device)
    if not model_dir.is_dir():
        raise ProbeFailure(
            STAGE_MODEL,
            "model_missing",
            f"The {capability_key.replace('_', ' ')} model is not downloaded.",
            str(model_dir),
        )
    result.model_dir = str(model_dir)
    processor_name, model_name, trust_remote_code = _TORCH_MODEL_LOADERS[capability_key]

    try:
        config = transformers.AutoConfig.from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=trust_remote_code
        )
    except Exception as exc:
        raise ProbeFailure(
            STAGE_MODEL,
            "model_unloadable",
            f"The {capability_key.replace('_', ' ')} model configuration could not be read. "
            "The download is incomplete or was produced for a different build.",
            _short_traceback(exc),
        ) from exc
    result.model_config = str(getattr(config, "model_type", "") or "")

    if not load_model:
        return

    if capability_key in {"scene_masks", "subject_masks", "sam_masks", "depth"}:
        _probe_production_torch_worker(
            result, capability_key, model_dir, result.selected_device
        )
        return

    processor = None
    if processor_name:
        try:
            processor = getattr(transformers, processor_name).from_pretrained(
                str(model_dir), local_files_only=True
            )
        except Exception as exc:
            raise ProbeFailure(
                STAGE_MODEL,
                "model_unloadable",
                f"The {capability_key.replace('_', ' ')} preprocessor could not be loaded.",
                _short_traceback(exc),
            ) from exc

    try:
        model = getattr(transformers, model_name).from_pretrained(
            str(model_dir), local_files_only=True, trust_remote_code=trust_remote_code
        )
        model.to(result.selected_device)
        model.eval()
    except Exception as exc:
        raise ProbeFailure(
            STAGE_MODEL,
            "model_unloadable",
            f"The {capability_key.replace('_', ' ')} model is downloaded but could not be "
            f"loaded onto {result.selected_device}.",
            _short_traceback(exc),
        ) from exc

    # DINO is opt-in and has no standalone worker. Exercise its real processor
    # and model contract rather than guessing a tensor shape.
    try:
        from PIL import Image  # noqa: PLC0415

        if processor is None:
            raise RuntimeError("The model has no configured image processor.")
        image = Image.new("RGB", (96, 64), color=(64, 96, 128))
        inputs = processor(images=image, return_tensors="pt")
        inputs = {
            key: value.to(result.selected_device) if hasattr(value, "to") else value
            for key, value in inputs.items()
        }
        with torch.no_grad():
            model(**inputs)
        if device_family(result.selected_device) == "cuda":
            torch.cuda.synchronize(_cuda_index(result.selected_device) or 0)
    except Exception as exc:
        raise ProbeFailure(
            STAGE_INFERENCE,
            "model_unloadable",
            f"The {capability_key.replace('_', ' ')} model loaded but could not run on "
            f"{result.selected_device}.",
            _short_traceback(exc),
        ) from exc
    result.inference_ran = True


def _probe_production_torch_worker(
    result: ProbeResult,
    capability_key: str,
    model_dir: Path,
    selected_device: str,
) -> None:
    """Run the same callable, preprocessing, precision, and output path as the UI."""
    try:
        from PIL import Image  # noqa: PLC0415

        with tempfile.TemporaryDirectory(prefix="image-triage-ai-probe-") as temp_dir:
            root = Path(temp_dir)
            input_path = root / "probe.png"
            Image.new("RGB", (96, 64), color=(64, 96, 128)).save(input_path)

            if capability_key == "scene_masks":
                from image_triage.oneformer_worker import generate_semantic_masks

                output_dir = root / "scene"
                payload = generate_semantic_masks(
                    model_dir=model_dir,
                    input_path=input_path,
                    output_dir=output_dir,
                    categories=("sky",),
                    minimum_coverage=0.0,
                    requested_device=selected_device,
                    emit_result=False,
                )
                expected = output_dir / "sky.png"
            elif capability_key == "subject_masks":
                from image_triage.birefnet_worker import generate_subject_mask

                expected = root / "subject.png"
                payload = generate_subject_mask(
                    model_dir=model_dir,
                    input_path=input_path,
                    output_path=expected,
                    components_dir=None,
                    requested_device=selected_device,
                    emit_result=False,
                )
            elif capability_key == "sam_masks":
                from image_triage.sam_worker import _SamEngine

                expected = root / "sam.png"
                engine = _SamEngine(selected_device)
                engine.load_model(model_dir)
                width, height = engine.embed(input_path, image_key="probe")
                payload = engine.segment(
                    points=[(width / 2.0, height / 2.0)],
                    labels=[1],
                    output_path=expected,
                    image_key="probe",
                )
            elif capability_key == "depth":
                from image_triage.depth_worker import generate_depth

                expected = root / "depth.png"
                payload = generate_depth(
                    model_dir=model_dir,
                    input_path=input_path,
                    output_path=expected,
                    requested_device=selected_device,
                    emit_result=False,
                )
            else:  # pragma: no cover - guarded by the caller
                raise RuntimeError(f"No production probe exists for {capability_key}.")

            if not isinstance(payload, dict) or not expected.is_file() or expected.stat().st_size <= 0:
                raise RuntimeError("The worker did not produce its expected output file.")
    except ProbeFailure:
        raise
    except Exception as exc:
        raise ProbeFailure(
            STAGE_INFERENCE,
            "model_unloadable",
            f"The {capability_key.replace('_', ' ')} production worker failed on "
            f"{selected_device}.",
            _short_traceback(exc),
        ) from exc
    result.inference_ran = True


def probe_insightface(result: ProbeResult, model_dir: Path, requested_device: str) -> None:
    """Load and execute every ONNX graph in the face-analysis model pack."""
    import insightface  # noqa: F401, PLC0415

    model_paths = sorted(model_dir.glob("*.onnx"))
    if not model_paths:
        raise ProbeFailure(
            STAGE_MODEL, "model_missing", "The face-analysis model pack is empty.", str(model_dir)
        )
    for model_path in model_paths:
        expected_dim = 512 if model_path.name.casefold() == "glintr100.onnx" else 0
        probe_onnx_model(
            result,
            model_path,
            requested_device,
            run_inference=True,
            expected_embedding_dim=expected_dim,
        )


def prepare_model_for_probe(
    result: ProbeResult,
    model_path: Path,
    prepare_hook: str,
) -> Path:
    """Apply the same pre-flight the application applies before inference.

    Returning the raw path is always safe; the probe then tests exactly what an
    unprepared run would do.
    """
    if prepare_hook != "topiq_cuda":
        return model_path
    if device_family(result.selected_device) != "cuda":
        return model_path
    try:
        from aiculler.topiq_onnx import prepare_topiq_model_for_providers
    except Exception as exc:  # pragma: no cover - frozen packaging problem
        raise ProbeFailure(
            STAGE_MODEL,
            "package_missing",
            "The TOPIQ CUDA preparation step is missing from this build.",
            _short_traceback(exc),
        ) from exc
    preparation = prepare_topiq_model_for_providers(
        model_path, ["CUDAExecutionProvider", "CPUExecutionProvider"]
    )
    result.model_prepared = str(preparation.detail or "")
    prepared = Path(preparation.path)
    if not prepared.is_file():
        raise ProbeFailure(
            STAGE_MODEL,
            "model_missing",
            "The CUDA-compatible TOPIQ graph could not be produced.",
            result.model_prepared,
        )
    return prepared


def _short_traceback(exc: BaseException) -> str:
    lines = traceback.format_exception_only(type(exc), exc)
    return "".join(lines).strip()[:2000]


def run_probe(
    *,
    capability_key: str,
    site_packages: Path,
    modules: tuple[str, ...],
    probe_kind: str,
    requested_device: str = "auto",
    profile: str = "",
    model_path: Path | None = None,
    model_dir: Path | None = None,
    transformers_symbols: tuple[str, ...] = (),
    expected_outputs: tuple[str, ...] = (),
    expected_embedding_dim: int = 0,
    prepare_hook: str = "",
    probe_level: str = "quick",
) -> ProbeResult:
    """Run one capability probe and return its result, never raising.

    ``probe_level="quick"`` is the routine check used for UI gating: imports,
    providers, device, model configuration, and a real inference for the cheap
    ONNX models. ``probe_level="full"`` additionally loads torch model weights
    and runs a forward pass, which is what Demo Ready and post-repair
    verification use.
    """
    started = time.perf_counter()
    thorough = probe_level == "full"
    result = ProbeResult(
        capability=capability_key,
        profile=profile,
        site_packages=str(site_packages),
        requested_device=requested_device,
        probe_level=probe_level,
    )
    try:
        configure_runtime_path(Path(site_packages))
        result.stage = STAGE_IMPORT
        versions, locations = import_modules(modules, Path(site_packages))
        result.modules.update(versions)
        result.module_paths.update(locations)
        if transformers_symbols:
            probe_transformers_classes(transformers_symbols)

        result.stage = STAGE_PROVIDER
        if probe_kind == "torch":
            if model_dir is None:
                raise ProbeFailure(STAGE_MODEL, "manifest", "No model directory was supplied to the probe.")
            result.stage = STAGE_MODEL
            probe_torch_model(
                result,
                capability_key,
                Path(model_dir),
                requested_device,
                load_model=thorough,
            )
        elif probe_kind == "onnx_providers":
            probe_onnx_providers(result, requested_device)
        elif probe_kind == "onnx_model":
            result.stage = STAGE_MODEL
            if model_path is None:
                raise ProbeFailure(STAGE_MODEL, "manifest", "No model file was supplied to the probe.")
            resolved_model = Path(model_path)
            if prepare_hook:
                probe_onnx_providers(result, requested_device)
                resolved_model = prepare_model_for_probe(result, resolved_model, prepare_hook)
            probe_onnx_model(
                result,
                resolved_model,
                requested_device,
                expected_outputs=expected_outputs,
                expected_embedding_dim=expected_embedding_dim,
            )
        elif probe_kind == "insightface":
            result.stage = STAGE_MODEL
            if model_dir is None:
                raise ProbeFailure(STAGE_MODEL, "manifest", "No model directory was supplied to the probe.")
            probe_insightface(result, Path(model_dir), requested_device)
        else:
            result.selected_device = "cpu" if requested_device == "cpu" else requested_device

        result.stage = STAGE_COMPLETE
        result.ok = True
    except ProbeFailure as exc:
        result.stage = exc.stage
        result.category = exc.category
        result.message = exc.message
        result.detail = exc.detail
    except Exception as exc:  # pragma: no cover - unexpected probe crash
        result.category = "probe_error"
        result.message = f"The {capability_key} check failed unexpectedly: {exc}"
        result.detail = _short_traceback(exc)
    result.duration_ms = int((time.perf_counter() - started) * 1000)
    return result


__all__ = [
    "PROBE_PROTOCOL_VERSION",
    "STAGE_COMPLETE",
    "STAGE_IMPORT",
    "STAGE_INFERENCE",
    "STAGE_MODEL",
    "STAGE_PATH",
    "STAGE_PROVIDER",
    "ProbeFailure",
    "ProbeResult",
    "configure_runtime_path",
    "import_modules",
    "probe_insightface",
    "probe_onnx_model",
    "probe_onnx_providers",
    "probe_torch",
    "probe_torch_model",
    "prepare_model_for_probe",
    "probe_transformers_classes",
    "run_probe",
]
