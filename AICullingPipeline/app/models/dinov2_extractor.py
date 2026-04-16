"""Frozen DINOv2 embedding extractor backed by timm or local Hugging Face weights."""

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
    """Load a frozen DINOv2 backbone and expose batch embedding inference."""

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
        self._transformers_image_size: int | None = None
        self.device = _resolve_device(device)
        (
            self.model_name,
            self.model,
            self._backend,
            self._transformers_image_size,
        ) = self._load_model(
            model_name=model_name,
            fallback_model_name=fallback_model_name,
            allow_fallback=allow_fallback,
        )
        self.model = self.model.to(self.device)
        self.model.eval()
        self.model.requires_grad_(False)

        if self._backend == "transformers":
            resolved_size = int(self._transformers_image_size or 518)
            data_config = {
                "input_size": (3, resolved_size, resolved_size),
                "mean": (0.485, 0.456, 0.406),
                "std": (0.229, 0.224, 0.225),
                "interpolation": "bicubic",
                "crop_pct": 1.0,
            }
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

    def _load_model(
        self,
        *,
        model_name: str,
        fallback_model_name: Optional[str],
        allow_fallback: bool,
    ) -> tuple[str, torch.nn.Module, str, int | None]:
        try:
            return self._load_single_model(model_name)
        except Exception as exc:
            if not _can_fallback(
                model_name=model_name,
                fallback_model_name=fallback_model_name,
                allow_fallback=allow_fallback,
            ):
                raise RuntimeError(f"Failed to load requested model '{model_name}'.") from exc

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
    ) -> tuple[str, torch.nn.Module, str, int | None]:
        local_model_dir = _resolve_local_model_dir(model_name)
        if local_model_dir is not None:
            if not local_model_dir.exists():
                raise FileNotFoundError(f"Local DINOv2 model directory not found: {local_model_dir}")
            if _is_transformers_dinov2_repository(local_model_dir):
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
                    "timm is required for DINOv2 extraction. Install it with "
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
    ) -> tuple[str, torch.nn.Module, str, int | None]:
        try:
            from transformers import Dinov2Model
        except ImportError as exc:
            raise ImportError(
                "transformers is required to load local Hugging Face DINOv2 repositories."
            ) from exc

        model = Dinov2Model.from_pretrained(
            str(model_dir),
            local_files_only=True,
        )
        image_size = int(getattr(model.config, "image_size", 518) or 518)
        return str(model_dir.resolve()), model, "transformers", image_size

    @torch.inference_mode()
    def encode_batch(self, pixel_values: torch.Tensor) -> torch.Tensor:
        """Encode a batch of images into one embedding per image."""

        batch = pixel_values.to(self.device, non_blocking=self.device.type == "cuda")
        if self._backend == "transformers":
            outputs = self.model(pixel_values=batch)
            embeddings = getattr(outputs, "pooler_output", None)
            if embeddings is None:
                last_hidden_state = getattr(outputs, "last_hidden_state", None)
                if last_hidden_state is None:
                    raise TypeError("Transformers DINOv2 model did not return pooled embeddings.")
                embeddings = last_hidden_state[:, 0, :]
        else:
            embeddings = self.model(batch)

        if not isinstance(embeddings, torch.Tensor):
            raise TypeError(
                f"Expected tensor embeddings from model, received {type(embeddings)!r}."
            )

        if embeddings.ndim != 2:
            raise ValueError(
                f"Expected embeddings with shape [N, D], received {tuple(embeddings.shape)}."
            )

        return embeddings.detach().cpu().to(torch.float32)


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


def _is_transformers_dinov2_repository(model_dir: Path) -> bool:
    config_path = model_dir / "config.json"
    if not config_path.exists():
        return False
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    if not isinstance(payload, dict):
        return False
    model_type = str(payload.get("model_type") or "").strip().lower()
    architectures = payload.get("architectures")
    architecture_names = (
        {str(name).strip() for name in architectures}
        if isinstance(architectures, list)
        else set()
    )
    return model_type == "dinov2" or "Dinov2Model" in architecture_names


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
