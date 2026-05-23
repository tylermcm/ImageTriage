"""Frozen DINO embedding extractor backed by timm or local Hugging Face weights."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
from pathlib import Path
from typing import Any, Optional, Tuple, Union

import torch
from torchvision import transforms
from torchvision.transforms import InterpolationMode


LOGGER = logging.getLogger(__name__)

SUPPORTED_DINOV2_MODELS = {
    "vit_small_patch14_dinov2.lvd142m": "DINOv2 ViT-S/14",
    "vit_base_patch14_dinov2.lvd142m": "DINOv2 ViT-B/14",
    "vit_small_patch14_reg4_dinov2.lvd142m": "DINOv2 ViT-S/14 (registers)",
    "vit_base_patch14_reg4_dinov2.lvd142m": "DINOv2 ViT-B/14 (registers)",
}
SUPPORTED_TRANSFORMERS_MODEL_TYPES = {"dinov2", "dinov3_vit"}
SUPPORTED_TRANSFORMERS_ARCHITECTURES = {"Dinov2Model", "DINOv3ViTModel"}
UNSUPPORTED_TRANSFORMERS_MODEL_TYPES = {"chmv2"}


@dataclass(frozen=True)
class PreprocessingSpec:
    """Resolved preprocessing parameters used for inference."""

    height: int
    width: int
    mean: tuple[float, float, float]
    std: tuple[float, float, float]
    interpolation: str
    crop_pct: float


class DINOv2EmbeddingExtractor:
    """Load a frozen DINO backbone and expose batch embedding inference.

    The class name is kept for compatibility with existing pipeline imports. It
    now supports timm DINOv2 names plus local Hugging Face DINOv2/DINOv3 repos.
    """

    def __init__(
        self,
        model_name: str,
        *,
        device: str = "auto",
        image_size: Optional[int] = None,
        fallback_model_name: Optional[str] = None,
        allow_fallback: bool = True,
    ) -> None:
        self._timm = None
        self._backend = "timm"
        self._transformers_data_config: dict[str, Any] | None = None
        self.device = _resolve_device(device)
        LOGGER.info(
            "Torch runtime for DINO extraction: version=%s cuda=%s cuda_available=%s requested_device=%s resolved_device=%s torch_path=%s",
            getattr(torch, "__version__", "unknown"),
            getattr(torch.version, "cuda", None),
            torch.cuda.is_available(),
            device,
            self.device,
            getattr(torch, "__file__", ""),
        )
        (
            self.model_name,
            self.model,
            self._backend,
            self._transformers_data_config,
        ) = self._load_model(
            model_name=model_name,
            fallback_model_name=fallback_model_name,
            allow_fallback=allow_fallback,
        )
        self.model = self.model.to(self.device)
        self.model.eval()
        self.model.requires_grad_(False)
        if hasattr(torch, "set_float32_matmul_precision"):
            torch.set_float32_matmul_precision("high")
        if self.device.type == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cudnn.benchmark = True

        if self._backend == "transformers":
            data_config = self._transformers_data_config or _default_transformers_data_config()
            self.transform, self.preprocessing = build_eval_transform(
                data_config,
                image_size=image_size,
            )
            self.feature_dim = int(getattr(self.model.config, "hidden_size"))
        else:
            assert self._timm is not None
            data_config = self._timm.data.resolve_model_data_config(self.model)
            self.transform, self.preprocessing = build_eval_transform(
                data_config,
                image_size=image_size,
            )
            self.feature_dim = int(getattr(self.model, "num_features"))

        LOGGER.info(
            "Loaded %s via %s on %s with feature_dim=%s and input_size=%sx%s.",
            self.model_name,
            self._backend,
            self.device,
            self.feature_dim,
            self.preprocessing.height,
            self.preprocessing.width,
        )

    @property
    def backend(self) -> str:
        return self._backend

    def _load_model(
        self,
        *,
        model_name: str,
        fallback_model_name: Optional[str],
        allow_fallback: bool,
    ) -> tuple[str, torch.nn.Module, str, dict[str, Any] | None]:
        try:
            return self._load_single_model(model_name)
        except Exception as exc:
            if "AuxRequest" in str(exc):
                raise RuntimeError(
                    f"Failed to load requested model '{model_name}': the installed transformers "
                    "DINOv3 code requires a newer PyTorch flex-attention API. Reinstall the "
                    "managed GPU AI runtime so torch is upgraded to 2.9.0+cu128 or newer."
                ) from exc
            if not _can_fallback(
                model_name=model_name,
                fallback_model_name=fallback_model_name,
                allow_fallback=allow_fallback,
            ):
                raise RuntimeError(f"Failed to load requested model '{model_name}': {exc}") from exc

            assert fallback_model_name is not None
            LOGGER.warning(
                "Failed to load %s (%s). Falling back to %s.",
                model_name,
                exc,
                fallback_model_name,
            )
            return self._load_single_model(fallback_model_name)

    def _load_single_model(
        self,
        model_name: str,
    ) -> tuple[str, torch.nn.Module, str, dict[str, Any] | None]:
        local_model_dir = _resolve_local_model_dir(model_name)
        if local_model_dir is not None:
            if not local_model_dir.exists():
                raise FileNotFoundError(f"Local DINO model directory not found: {local_model_dir}")
            model_config = _read_model_config(local_model_dir)
            unsupported_reason = _unsupported_transformers_repository_reason(model_config)
            if unsupported_reason:
                raise ValueError(f"Unsupported local Hugging Face DINO model: {unsupported_reason}")
            if _is_transformers_dino_repository(model_config):
                return self._load_transformers_model(local_model_dir)

        resolved_model_name = _normalize_model_source(model_name)
        if (
            not resolved_model_name.startswith(("hf-hub:", "local-dir:"))
            and resolved_model_name not in SUPPORTED_DINOV2_MODELS
        ):
            LOGGER.warning(
                "Model name %s is not in the known DINOv2 registry. "
                "Attempting to load it through timm anyway.",
                resolved_model_name,
            )

        if self._timm is None:
            try:
                import timm
            except ImportError as exc:
                raise ImportError(
                    "timm is required for timm-backed DINO extraction. Install it with "
                    "'pip install -r requirements.txt'."
                ) from exc
            self._timm = timm

        model = self._timm.create_model(
            resolved_model_name,
            pretrained=True,
            num_classes=0,
        )
        return resolved_model_name, model, "timm", None

    def _load_transformers_model(
        self,
        model_dir: Path,
    ) -> tuple[str, torch.nn.Module, str, dict[str, Any] | None]:
        try:
            from transformers import AutoModel
        except ImportError as exc:
            raise ImportError(
                "transformers is required to load local Hugging Face DINO repositories."
            ) from exc

        model = AutoModel.from_pretrained(
            str(model_dir),
            local_files_only=True,
        )
        data_config = _transformers_data_config(model_dir, model.config)
        return str(model_dir.resolve()), model, "transformers", data_config

    @torch.inference_mode()
    def encode_batch(self, pixel_values: torch.Tensor) -> torch.Tensor:
        """Encode a batch of images into one embedding per image."""

        batch = pixel_values.to(self.device, non_blocking=self.device.type == "cuda")
        if self.device.type == "cuda":
            with torch.autocast(device_type="cuda", dtype=torch.float16):
                embeddings = self._forward_batch(batch)
        else:
            embeddings = self._forward_batch(batch)

        if not isinstance(embeddings, torch.Tensor):
            raise TypeError(
                f"Expected tensor embeddings from model, received {type(embeddings)!r}."
            )

        if embeddings.ndim != 2:
            raise ValueError(
                f"Expected embeddings with shape [N, D], received {tuple(embeddings.shape)}."
            )

        return embeddings.detach().cpu().to(torch.float32)

    def _forward_batch(self, batch: torch.Tensor) -> torch.Tensor:
        if self._backend == "transformers":
            outputs = self.model(pixel_values=batch)
            embeddings = getattr(outputs, "pooler_output", None)
            if embeddings is None:
                last_hidden_state = getattr(outputs, "last_hidden_state", None)
                if last_hidden_state is None:
                    raise TypeError("Transformers DINO model did not return pooled embeddings.")
                embeddings = last_hidden_state[:, 0, :]
            return embeddings
        return self.model(batch)


def build_eval_transform(
    data_config: dict[str, Any],
    *,
    image_size: Optional[int] = None,
) -> tuple[transforms.Compose, PreprocessingSpec]:
    """Create an explicit evaluation transform from timm's resolved data config."""

    input_size = data_config.get("input_size", (3, 518, 518))
    _, default_height, default_width = input_size
    height = image_size or int(default_height)
    width = image_size or int(default_width)
    crop_pct = float(data_config.get("crop_pct", 1.0))
    interpolation_name = str(data_config.get("interpolation", "bicubic")).lower()
    interpolation = _resolve_interpolation(interpolation_name)

    if height == width:
        resize_size: Union[int, Tuple[int, int]] = max(1, math.floor(height / crop_pct))
    else:
        resize_size = (
            max(1, math.floor(height / crop_pct)),
            max(1, math.floor(width / crop_pct)),
        )

    mean = tuple(float(value) for value in data_config.get("mean", (0.485, 0.456, 0.406)))
    std = tuple(float(value) for value in data_config.get("std", (0.229, 0.224, 0.225)))

    transform = transforms.Compose(
        [
            transforms.Resize(resize_size, interpolation=interpolation),
            transforms.CenterCrop((height, width)),
            transforms.ToTensor(),
            transforms.Normalize(mean=mean, std=std),
        ]
    )

    spec = PreprocessingSpec(
        height=height,
        width=width,
        mean=mean,
        std=std,
        interpolation=interpolation_name,
        crop_pct=crop_pct,
    )
    return transform, spec


def _resolve_device(device_name: str) -> torch.device:
    """Resolve the configured device string into a torch device."""

    if device_name == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")

    if device_name.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError(
            "CUDA was requested but is not available in the current PyTorch install."
        )

    return torch.device(device_name)


def _resolve_interpolation(name: str) -> InterpolationMode:
    """Map string interpolation names to torchvision modes."""

    mapping = {
        "nearest": InterpolationMode.NEAREST,
        "bilinear": InterpolationMode.BILINEAR,
        "bicubic": InterpolationMode.BICUBIC,
        "box": InterpolationMode.BOX,
        "hamming": InterpolationMode.HAMMING,
        "lanczos": InterpolationMode.LANCZOS,
    }
    return mapping.get(name.lower(), InterpolationMode.BICUBIC)


def _normalize_model_source(model_name: str) -> str:
    """Convert local model directories into timm's local-dir schema."""

    if model_name.startswith(("hf-hub:", "local-dir:")):
        return model_name

    path = Path(model_name).expanduser()
    if path.exists() and path.is_dir():
        return f"local-dir:{path.resolve()}"

    return model_name


def _resolve_local_model_dir(model_name: str) -> Path | None:
    text = str(model_name).strip()
    if not text:
        return None
    if text.startswith("local-dir:"):
        return Path(text.split(":", 1)[1]).expanduser()
    path = Path(text).expanduser()
    if path.is_absolute() or "/" in text or "\\" in text or text.startswith("."):
        return path
    return None


def _read_model_config(model_dir: Path) -> dict[str, Any] | None:
    config_path = model_dir / "config.json"
    if not config_path.exists():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_transformers_dino_repository(payload: dict[str, Any] | None) -> bool:
    if not payload:
        return False
    model_type = str(payload.get("model_type") or "").strip().lower()
    return (
        model_type in SUPPORTED_TRANSFORMERS_MODEL_TYPES
        or bool(_architecture_names(payload) & SUPPORTED_TRANSFORMERS_ARCHITECTURES)
    )


def _unsupported_transformers_repository_reason(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    model_type = str(payload.get("model_type") or "").strip().lower()
    if model_type in UNSUPPORTED_TRANSFORMERS_MODEL_TYPES:
        architectures = ", ".join(sorted(_architecture_names(payload))) or model_type
        return (
            f"{architectures} is a task head, not a bare DINO embedding backbone. "
            "Use a repository whose config model_type is 'dinov2' or 'dinov3_vit'."
        )
    return ""


def _architecture_names(payload: dict[str, Any]) -> set[str]:
    architectures = payload.get("architectures")
    if not isinstance(architectures, list):
        return set()
    return {str(name).strip() for name in architectures if str(name).strip()}


def _transformers_data_config(model_dir: Path, config: Any) -> dict[str, Any]:
    model_size = _coerce_positive_int(getattr(config, "image_size", None)) or 518
    data_config = _default_transformers_data_config(model_size)
    preprocessor_path = model_dir / "preprocessor_config.json"
    if not preprocessor_path.exists():
        return data_config
    try:
        payload = json.loads(preprocessor_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return data_config
    if not isinstance(payload, dict):
        return data_config

    height, width = _preprocessor_size(payload) or (model_size, model_size)
    mean = _float_triplet(payload.get("image_mean")) or data_config["mean"]
    std = _float_triplet(payload.get("image_std")) or data_config["std"]
    interpolation = _preprocessor_interpolation(payload.get("resample")) or data_config["interpolation"]
    return {
        "input_size": (3, height, width),
        "mean": mean,
        "std": std,
        "interpolation": interpolation,
        "crop_pct": 1.0,
    }


def _default_transformers_data_config(image_size: int = 518) -> dict[str, Any]:
    resolved_size = max(1, int(image_size or 518))
    return {
        "input_size": (3, resolved_size, resolved_size),
        "mean": (0.485, 0.456, 0.406),
        "std": (0.229, 0.224, 0.225),
        "interpolation": "bicubic",
        "crop_pct": 1.0,
    }


def _preprocessor_size(payload: dict[str, Any]) -> tuple[int, int] | None:
    size = payload.get("size")
    if isinstance(size, dict):
        height = _coerce_positive_int(size.get("height"))
        width = _coerce_positive_int(size.get("width"))
        shortest_edge = _coerce_positive_int(size.get("shortest_edge"))
        if height and width:
            return height, width
        if shortest_edge:
            return shortest_edge, shortest_edge
    value = _coerce_positive_int(size)
    if value:
        return value, value
    return None


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _float_triplet(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, list) or len(value) != 3:
        return None
    try:
        return tuple(float(item) for item in value)  # type: ignore[return-value]
    except (TypeError, ValueError):
        return None


def _preprocessor_interpolation(value: Any) -> str | None:
    if value is None:
        return None
    mapping = {
        0: "nearest",
        2: "bilinear",
        3: "bicubic",
        4: "box",
        5: "hamming",
        1: "lanczos",
    }
    try:
        return mapping.get(int(value))
    except (TypeError, ValueError):
        text = str(value).strip().lower()
        return text or None


def _is_local_model_reference(model_name: str | None) -> bool:
    if not model_name:
        return False
    text = str(model_name).strip()
    if text.startswith("local-dir:"):
        return True
    path = Path(text).expanduser()
    return path.is_absolute() or "/" in text or "\\" in text or text.startswith(".")


def _can_fallback(
    *,
    model_name: str,
    fallback_model_name: str | None,
    allow_fallback: bool,
) -> bool:
    if not allow_fallback or not fallback_model_name:
        return False
    if not _is_local_model_reference(model_name):
        return True
    return _is_local_model_reference(fallback_model_name)
