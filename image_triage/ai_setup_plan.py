"""The Set Up AI plan: what is always installed, which optional models exist and how they group, the defaults for this PC, the sizes, and the choice kept
between runs. Pure (no Qt), so the dialog, the status chip and the readiness check all read the same answers.

Always installed ("core"): the AI runtime (PyTorch included), the face finder and recognizer, and TinyCLIP search.
Optional, in two groups the person sees as one line each: Culling (the two rankers) and Editor tools (subject masks, click selection, depth, scene regions).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable, Protocol

from .ai_manifest import MODEL_BUNDLES
from .ai_runtime_packages import AI_RUNTIME_GPU_VARIANT, estimate_ai_runtime_download_size_mb

SETTINGS_KEY = "ai/setup_models"

GROUP_CULL = "cull"
GROUP_EDIT = "edit"


@dataclass(frozen=True)
class OptionalModel:
    key: str
    label: str
    group: str
    bundle: str  # the ModelBundle that holds its files
    capability: str  # the capability that installs and verifies it


OPTIONAL_MODELS: tuple[OptionalModel, ...] = (
    OptionalModel("maniqa", "Quality", GROUP_CULL, "maniqa", "rank_quality"),
    OptionalModel("qrealign", "Appeal", GROUP_CULL, "qrealign", "rank_aesthetic"),
    OptionalModel("birefnet", "Subject masks", GROUP_EDIT, "birefnet", "editor_subject"),
    OptionalModel("sam", "Click select", GROUP_EDIT, "sam", "editor_select"),
    OptionalModel("depth", "Depth", GROUP_EDIT, "depth", "editor_depth"),
    OptionalModel("oneformer", "Scene regions", GROUP_EDIT, "oneformer", "editor_scene"),
)
GROUPS: dict[str, tuple[str, str]] = {
    GROUP_CULL: ("Culling", "Picks the best photo in each group"),
    GROUP_EDIT: ("Editor tools", "Smart masks and selections in the editor"),
}
# Bundles that are part of the core install, next to the runtime itself.
CORE_BUNDLES: tuple[str, ...] = ("faces_blink", "faces_identity", "tinyclip")

_BY_KEY = {m.key: m for m in OPTIONAL_MODELS}


def model(key: str) -> OptionalModel:
    return _BY_KEY[key]


def all_model_keys() -> frozenset[str]:
    return frozenset(_BY_KEY)


def models_in(group: str) -> tuple[OptionalModel, ...]:
    return tuple(m for m in OPTIONAL_MODELS if m.group == group)


def model_mb(key: str) -> int:
    return MODEL_BUNDLES[_BY_KEY[key].bundle].approx_mb


def group_mb(group: str) -> int:
    return sum(model_mb(m.key) for m in models_in(group))


def core_mb(variant: str) -> int:
    """The core download for a runtime variant: the runtime with PyTorch, the face models and TinyCLIP."""
    return estimate_ai_runtime_download_size_mb(variant, include_torch=True) + sum(MODEL_BUNDLES[key].approx_mb for key in CORE_BUNDLES)


def default_selection(*, gpu: bool, installed: Iterable[str] = ()) -> frozenset[str]:
    """Everything with a GPU; with no GPU nothing extra (the rankers are slow there), but whatever is already installed stays selected."""
    return all_model_keys() if gpu else frozenset(installed) & all_model_keys()


def capabilities_for(selection: Iterable[str]) -> tuple[str, ...]:
    """The optional-model capabilities for a selection, in a stable order."""
    chosen = set(selection)
    return tuple(m.capability for m in OPTIONAL_MODELS if m.key in chosen)


def download_mb(selection: Iterable[str], *, variant: str, core_installed: bool, installed: Iterable[str] = ()) -> int:
    """What pressing Install would download: the core when it is missing, plus every selected model that is not installed yet."""
    have = set(installed)
    total = 0 if core_installed else core_mb(variant)
    return total + sum(model_mb(key) for key in selection if key in _BY_KEY and key not in have)


def is_gpu_variant(variant: str) -> bool:
    return variant == AI_RUNTIME_GPU_VARIANT


def format_mb(mb: float) -> str:
    """"871 MB" below a gigabyte, "2.7 GB" above (decimal, as download sizes are quoted)."""
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{round(mb)} MB"


class _Settings(Protocol):
    def value(self, key: str, default: object = ..., type: object = ...) -> object: ...
    def setValue(self, key: str, value: object) -> None: ...


def load_selection(settings: _Settings) -> frozenset[str] | None:
    """The models the person last chose, or ``None`` when they never chose (then the defaults for this PC apply)."""
    raw = settings.value(SETTINGS_KEY, "")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        keys = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(keys, list):
        return None
    return frozenset(key for key in keys if isinstance(key, str) and key in _BY_KEY)


def save_selection(settings: _Settings, selection: Iterable[str]) -> None:
    settings.setValue(SETTINGS_KEY, json.dumps(sorted(key for key in selection if key in _BY_KEY)))
