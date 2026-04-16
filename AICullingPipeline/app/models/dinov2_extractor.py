"""Frozen DINOv2 embedding extractor backed by timm."""

from __future__ import annotations

from dataclasses import dataclass
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
        try:
            import timm
        except ImportError as exc:
            raise ImportError(
                "timm is required for DINOv2 extraction. Install it with "
                "'pip install -r requirements.txt'."
            ) from exc

        self._timm = timm
        self.device = _resolve_device(device)
        self.model_name, self.model = self._load_model(
            model_name=model_name,
            fallback_model_name=fallback_model_name,
            allow_fallback=allow_fallback,
        )
        self.model = self.model.to(self.device)
        self.model.eval()
        self.model.requires_grad_(False)

        data_config = self._timm.data.resolve_model_data_config(self.model)
        self.transform, self.preprocessing = build_eval_transform(
            data_config,
            image_size=image_size,
        )
        self.feature_dim = int(getattr(self.model, "num_features"))

        LOGGER.info(
            "Loaded %s on %s with feature_dim=%s and input_size=%sx%s.",
            self.model_name,
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
    ) -> tuple[str, torch.nn.Module]:
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

        try:
            model = self._timm.create_model(
                resolved_model_name,
                pretrained=True,
                num_classes=0,
            )
            return resolved_model_name, model
        except Exception as exc:
            if not allow_fallback or not fallback_model_name:
                raise RuntimeError(
                    f"Failed to load requested model '{model_name}'."
                ) from exc

            resolved_fallback_name = _normalize_model_source(fallback_model_name)
            LOGGER.warning(
                "Failed to load %s (%s). Falling back to %s.",
                model_name,
                exc,
                resolved_fallback_name,
            )
            fallback_model = self._timm.create_model(
                resolved_fallback_name,
                pretrained=True,
                num_classes=0,
            )
            return resolved_fallback_name, fallback_model

    @torch.inference_mode()
    def encode_batch(self, pixel_values: torch.Tensor) -> torch.Tensor:
        """Encode a batch of images into one embedding per image."""

        batch = pixel_values.to(self.device, non_blocking=self.device.type == "cuda")
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
