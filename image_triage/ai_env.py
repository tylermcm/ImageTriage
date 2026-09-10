"""One runtime selection contract and one worker environment builder.

Previously each service re-resolved the managed runtime for itself
(``mask_engine_service``, ``subject_masks``, ``semantic_mask_service``,
``ai_workflow``), each with a slightly different module list and each free to
pick a different profile than its parent. ``ai_python_runner`` additionally
swallowed every resolution error, so a broken runtime surfaced much later as a
misleading ``ModuleNotFoundError`` inside a worker.

This module fixes the selection once per job and hands the same immutable
identity to every process involved. See ``docs/ai_runtime_failure_map.md``
(root causes E and F).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .ai_paths import redact
from .ai_runtime_packages import (
    AI_RUNTIME_CPU_VARIANT,
    AI_RUNTIME_GPU_VARIANT,
    _load_ai_runtime_metadata,
    _profile_generation,
    load_ai_runtime_installation_status,
    normalize_ai_runtime_variant,
)


# Passed to every worker so it can report the profile it actually loaded rather
# than the one its parent intended.
AI_PROFILE_ENV = "IMAGE_TRIAGE_AI_PROFILE"
AI_DEVICE_ENV = "IMAGE_TRIAGE_AI_SELECTED_DEVICE"
AI_PROTOCOL_ENV = "IMAGE_TRIAGE_AI_PROTOCOL"


class AIRuntimeUnavailable(RuntimeError):
    """The managed AI runtime cannot serve a request, with a specific reason."""

    def __init__(self, message: str, *, category: str = "runtime_missing", remediation: str = "") -> None:
        super().__init__(message)
        self.category = category
        self.remediation = remediation or "Open Settings and run Set Up AI."


@dataclass(frozen=True)
class RuntimeSelection:
    """An immutable, fully-resolved choice of managed runtime for one job."""

    variant: str
    device: str
    site_packages: tuple[Path, ...]
    profile_generation: str
    root: Path

    @property
    def primary_site_packages(self) -> Path:
        return self.site_packages[0]

    @property
    def profile_id(self) -> str:
        return self.profile_generation or self.variant

    def describe(self) -> str:
        return f"{self.variant} profile {self.profile_id} on {self.device}"

    def describe_redacted(self) -> str:
        return f"{self.describe()} ({redact(str(self.root))})"


def resolve_device(requested: str | None) -> str:
    """Normalize a requested device to ``cpu``, ``cuda``, ``cuda:N`` or ``auto``.

    An explicit GPU index must survive: ``AICULLING_DEVICE=cuda:1`` is a
    supported override (``ai_workflow.ai_device_environment_override``) and
    collapsing it to ``auto`` silently moved multi-GPU users onto device 0.
    """
    value = (requested or "auto").strip().lower()
    if value in {"cuda", "gpu"}:
        return "cuda"
    if value == "cpu":
        return "cpu"
    index = _cuda_device_index(value)
    if index is not None:
        return f"cuda:{index}"
    return "auto"


def _cuda_device_index(value: str) -> int | None:
    """The N in ``cuda:N`` / ``gpu:N``, or None when the value is not one."""
    prefix, separator, suffix = value.partition(":")
    if not separator or prefix not in {"cuda", "gpu"}:
        return None
    try:
        index = int(suffix)
    except ValueError:
        return None
    return index if index >= 0 else None


def device_family(device: str) -> str:
    """``cuda`` for any CUDA device including ``cuda:N``; otherwise the value."""
    value = (device or "").strip().lower()
    return "cuda" if value.startswith("cuda") else value


def select_runtime(
    device: str = "auto",
    *,
    install_root: str | Path | None = None,
    require_torch: bool = False,
) -> RuntimeSelection:
    """Pin one managed runtime profile for the whole of one job.

    Raises ``AIRuntimeUnavailable`` rather than returning an empty result, so a
    resolution failure is reported at the boundary where it happened.
    """
    requested = resolve_device(device)
    status = load_ai_runtime_installation_status(install_root=install_root)
    installed = tuple(status.installed_variants)
    if not installed:
        raise AIRuntimeUnavailable(
            "The AI runtime is not installed yet.",
            category="runtime_missing",
            remediation="Open Settings and run Set Up AI to install the AI runtime.",
        )

    if require_torch:
        candidates = tuple(
            variant for variant in installed if variant in status.dino_installed_variants
        )
        if not candidates:
            raise AIRuntimeUnavailable(
                "The installed AI runtime does not include the PyTorch components "
                "that editor masking and DINO features need.",
                category="runtime_incomplete",
                remediation=(
                    "Open Settings, run Set Up AI again and keep the PyTorch option "
                    "selected, then use Repair AI."
                ),
            )
    else:
        candidates = installed

    variant = _choose_variant(candidates, status.preferred_variant, requested)
    if not variant:
        raise AIRuntimeUnavailable(
            f"No installed AI runtime profile can serve a {requested} request.",
            category="profile_missing",
            remediation="Open Settings and run Set Up AI for the device you want to use.",
        )

    profile = status.profiles[variant]
    site_packages = profile.site_packages_dir
    if not site_packages.is_dir():
        raise AIRuntimeUnavailable(
            f"The {variant.upper()} AI runtime is recorded as installed but its files are gone.",
            category="runtime_corrupt",
            remediation="Use Repair AI in Settings to reinstall the runtime.",
        )

    metadata = _load_ai_runtime_metadata(status.directories.metadata_path)
    if variant == AI_RUNTIME_GPU_VARIANT:
        # Preserve an explicit GPU index so multi-GPU selection survives.
        index = _cuda_device_index(requested)
        resolved_device = f"cuda:{index}" if index is not None else "cuda"
    else:
        resolved_device = "cpu"
    if requested == "cpu":
        resolved_device = "cpu"
    return RuntimeSelection(
        variant=variant,
        device=resolved_device,
        site_packages=(site_packages,),
        profile_generation=_profile_generation(metadata, variant),
        root=status.directories.root,
    )


def _choose_variant(installed: tuple[str, ...], preferred: str, requested: str) -> str:
    available = {normalize_ai_runtime_variant(variant) for variant in installed}
    if not available:
        return ""
    requested = device_family(requested) or requested
    if requested == "cpu":
        # An explicit CPU request must never load the GPU profile. Falling back
        # to the GPU profile here is what made "Switch to CPU" ineffective.
        return AI_RUNTIME_CPU_VARIANT if AI_RUNTIME_CPU_VARIANT in available else ""
    if requested == "cuda":
        return AI_RUNTIME_GPU_VARIANT if AI_RUNTIME_GPU_VARIANT in available else ""
    normalized_preference = normalize_ai_runtime_variant(preferred)
    if normalized_preference in available:
        return normalized_preference
    if AI_RUNTIME_GPU_VARIANT in available:
        return AI_RUNTIME_GPU_VARIANT
    return AI_RUNTIME_CPU_VARIANT if AI_RUNTIME_CPU_VARIANT in available else next(iter(available))


def build_worker_env(
    selection: RuntimeSelection,
    *,
    base_env: dict[str, str] | None = None,
    metrics_enabled: bool = False,
    protocol_version: int = 1,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """The one environment every AI worker is launched with.

    Managed site-packages go first on ``PYTHONPATH``; ``PYTHONNOUSERSITE``
    keeps a user's own Python out; the selected profile and device travel with
    the process so the worker can confirm what it loaded.
    """
    from .ai_workflow import AI_METRICS_ENV_VAR  # local import: avoids a cycle

    env = dict(base_env if base_env is not None else os.environ)
    managed = [str(path) for path in selection.site_packages]
    existing = [part for part in env.get("PYTHONPATH", "").split(os.pathsep) if part]
    # Drop any stale managed entry so a second launch cannot stack profiles.
    filtered = [part for part in existing if part not in managed]
    env["PYTHONPATH"] = os.pathsep.join([*managed, *filtered])
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env[AI_PROFILE_ENV] = selection.profile_id
    env[AI_DEVICE_ENV] = selection.device
    env[AI_PROTOCOL_ENV] = str(int(protocol_version))
    env[AI_METRICS_ENV_VAR] = "1" if metrics_enabled else "0"
    if extra:
        env.update(extra)
    return env


def worker_reported_profile() -> str:
    """Called *inside* a worker: the profile its parent selected."""
    return (os.environ.get(AI_PROFILE_ENV, "") or "").strip()


def worker_selected_device() -> str:
    """Called *inside* a worker: the device its parent pinned."""
    return (os.environ.get(AI_DEVICE_ENV, "") or "").strip() or "auto"


def worker_protocol_version() -> int:
    try:
        return int(os.environ.get(AI_PROTOCOL_ENV, "") or 1)
    except ValueError:
        return 1


def assert_worker_protocol(expected: int) -> None:
    """Fail loudly when a stale worker outlives an application upgrade."""
    actual = worker_protocol_version()
    if actual != expected:
        raise AIRuntimeUnavailable(
            f"This AI helper speaks protocol {expected} but was started by "
            f"protocol {actual}. Restart Image Triage.",
            category="protocol_mismatch",
            remediation="Restart Image Triage so every AI helper is rebuilt.",
        )


__all__ = [
    "AI_DEVICE_ENV",
    "AI_PROFILE_ENV",
    "AI_PROTOCOL_ENV",
    "AIRuntimeUnavailable",
    "RuntimeSelection",
    "assert_worker_protocol",
    "build_worker_env",
    "device_family",
    "resolve_device",
    "select_runtime",
    "worker_protocol_version",
    "worker_reported_profile",
    "worker_selected_device",
]
