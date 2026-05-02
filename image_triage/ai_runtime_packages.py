from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


AI_RUNTIME_INSTALL_ROOT_ENV = "IMAGE_TRIAGE_AI_RUNTIME_ROOT"
AI_RUNTIME_ACTIVE_VARIANT_ENV = "IMAGE_TRIAGE_AI_TORCH_VARIANT"
AI_RUNTIME_METADATA_FILENAME = "runtime_installation.json"
AI_RUNTIME_PROFILES_DIRNAME = "profiles"
AI_RUNTIME_SITE_PACKAGES_DIRNAME = "site-packages"
AI_RUNTIME_CPU_VARIANT = "cpu"
AI_RUNTIME_GPU_VARIANT = "gpu"
AI_RUNTIME_BOTH_VARIANT = "both"
AI_RUNTIME_VARIANTS = (AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT)
AI_RUNTIME_INSTALL_CHOICES = (*AI_RUNTIME_VARIANTS, AI_RUNTIME_BOTH_VARIANT)
DEFAULT_CPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"
DEFAULT_GPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
AI_RUNTIME_PIP_REQUIREMENTS = (
    "torch",
    "torchvision",
    "numpy>=1.26",
    "Pillow>=10.4",
    "opencv-python-headless>=4.10",
    "scikit-learn>=1.5",
    "tqdm>=4.66",
    "PyYAML>=6.0",
    "timm>=1.0",
    "transformers>=4.46",
    "safetensors>=0.4",
)
AI_RUNTIME_REQUIRED_MODULE_NAMES = (
    "torch",
    "torchvision",
    "numpy",
    "cv2",
    "sklearn",
    "timm",
    "transformers",
    "safetensors",
    "PIL",
    "yaml",
    "tqdm",
)

PipRunner = Callable[[list[str], Path], int]


@dataclass(frozen=True)
class AIRuntimeDirectories:
    root: Path
    metadata_path: Path
    profiles_root: Path

    def site_packages_dir(self, variant: str) -> Path:
        normalized = normalize_ai_runtime_variant(variant)
        return self.profiles_root / normalized / AI_RUNTIME_SITE_PACKAGES_DIRNAME


@dataclass(frozen=True)
class AIRuntimeProfileStatus:
    variant: str
    site_packages_dir: Path
    missing_modules: tuple[str, ...]

    @property
    def is_installed(self) -> bool:
        return not self.missing_modules


@dataclass(frozen=True)
class AIRuntimeInstallationStatus:
    directories: AIRuntimeDirectories
    profiles: dict[str, AIRuntimeProfileStatus]
    installed_variants: tuple[str, ...]
    preferred_variant: str

    @property
    def is_installed(self) -> bool:
        return bool(self.installed_variants)


def normalize_ai_runtime_variant(value: str | None, *, allow_both: bool = False) -> str:
    candidate = (value or "").strip().lower()
    if candidate == AI_RUNTIME_GPU_VARIANT:
        return AI_RUNTIME_GPU_VARIANT
    if candidate == AI_RUNTIME_CPU_VARIANT:
        return AI_RUNTIME_CPU_VARIANT
    if allow_both and candidate == AI_RUNTIME_BOTH_VARIANT:
        return AI_RUNTIME_BOTH_VARIANT
    return AI_RUNTIME_GPU_VARIANT if candidate.startswith("cu") or candidate == "cuda" else AI_RUNTIME_CPU_VARIANT


def ai_runtime_variant_label(variant: str) -> str:
    normalized = normalize_ai_runtime_variant(variant)
    if normalized == AI_RUNTIME_GPU_VARIANT:
        return "GPU (CUDA)"
    return "CPU Only"


def default_ai_runtime_install_root() -> Path:
    cache_root = _default_user_cache_root() / "image_triage_ai_cache" / "runtime"
    return cache_root / _python_runtime_tag()


def resolve_ai_runtime_directories(*, install_root: str | Path | None = None) -> AIRuntimeDirectories:
    root_value = (
        install_root
        or (os.environ.get(AI_RUNTIME_INSTALL_ROOT_ENV, "") or "").strip()
        or default_ai_runtime_install_root()
    )
    root = Path(root_value).expanduser().resolve()
    return AIRuntimeDirectories(
        root=root,
        metadata_path=root / AI_RUNTIME_METADATA_FILENAME,
        profiles_root=root / AI_RUNTIME_PROFILES_DIRNAME,
    )


def load_ai_runtime_installation_status(
    *,
    install_root: str | Path | None = None,
) -> AIRuntimeInstallationStatus:
    directories = resolve_ai_runtime_directories(install_root=install_root)
    metadata = _load_ai_runtime_metadata(directories.metadata_path)
    profiles = {
        variant: _profile_status(directories, variant)
        for variant in AI_RUNTIME_VARIANTS
    }
    installed_variants = tuple(
        variant
        for variant in AI_RUNTIME_VARIANTS
        if profiles[variant].is_installed
    )
    preferred_variant = normalize_ai_runtime_variant(
        os.environ.get(AI_RUNTIME_ACTIVE_VARIANT_ENV)
        or metadata.get("preferred_variant")
        or (installed_variants[0] if installed_variants else AI_RUNTIME_GPU_VARIANT)
    )
    return AIRuntimeInstallationStatus(
        directories=directories,
        profiles=profiles,
        installed_variants=installed_variants,
        preferred_variant=preferred_variant,
    )


def resolve_ai_runtime_site_packages(
    *,
    device: str = "auto",
    install_root: str | Path | None = None,
) -> tuple[Path, ...]:
    status = load_ai_runtime_installation_status(install_root=install_root)
    if not status.installed_variants:
        return ()
    variant = _select_runtime_variant(
        installed_variants=status.installed_variants,
        preferred_variant=status.preferred_variant,
        device=device,
    )
    if not variant:
        return ()
    return (status.directories.site_packages_dir(variant),)


def install_ai_runtime(
    variant_choice: str,
    *,
    force: bool = False,
    install_root: str | Path | None = None,
    output_callback: Callable[[str], None] | None = None,
    pip_runner: PipRunner | None = None,
) -> AIRuntimeInstallationStatus:
    normalized_choice = normalize_ai_runtime_variant(variant_choice, allow_both=True)
    target_variants = (
        AI_RUNTIME_VARIANTS if normalized_choice == AI_RUNTIME_BOTH_VARIANT else (normalized_choice,)
    )
    directories = resolve_ai_runtime_directories(install_root=install_root)
    directories.root.mkdir(parents=True, exist_ok=True)
    directories.profiles_root.mkdir(parents=True, exist_ok=True)
    runner = pip_runner or _default_pip_runner
    current_status = load_ai_runtime_installation_status(install_root=install_root)
    installed_variants = set(current_status.installed_variants)

    for variant in target_variants:
        target_dir = directories.site_packages_dir(variant)
        if force and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        if output_callback is not None:
            output_callback(
                f"Installing {ai_runtime_variant_label(variant)} AI runtime packages to {target_dir}"
            )
        args = build_ai_runtime_pip_install_args(variant=variant, target_dir=target_dir, force=force)
        exit_code = runner(args, directories.root)
        if exit_code != 0:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(
                f"AI runtime install failed for {ai_runtime_variant_label(variant)} (exit code {exit_code})."
            )
        profile_status = _profile_status(directories, variant)
        if not profile_status.is_installed:
            missing = ", ".join(profile_status.missing_modules)
            raise RuntimeError(
                f"AI runtime install for {ai_runtime_variant_label(variant)} completed, "
                f"but required modules are still missing: {missing}"
            )
        installed_variants.add(variant)

    preferred_variant = (
        AI_RUNTIME_GPU_VARIANT
        if normalized_choice in {AI_RUNTIME_GPU_VARIANT, AI_RUNTIME_BOTH_VARIANT}
        else AI_RUNTIME_CPU_VARIANT
    )
    metadata = {
        "installed_variants": sorted(installed_variants),
        "preferred_variant": preferred_variant,
        "runtime_tag": _python_runtime_tag(),
    }
    directories.metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return load_ai_runtime_installation_status(install_root=install_root)


def build_ai_runtime_pip_install_args(
    *,
    variant: str,
    target_dir: str | Path,
    force: bool = False,
) -> list[str]:
    normalized = normalize_ai_runtime_variant(variant)
    args = [
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--no-warn-script-location",
        "--prefer-binary",
        "--only-binary=:all:",
        "--target",
        str(Path(target_dir)),
        "--extra-index-url",
        _torch_index_url_for_variant(normalized),
    ]
    if force:
        args.extend(["--upgrade", "--force-reinstall"])
    else:
        args.append("--upgrade")
    args.extend(AI_RUNTIME_PIP_REQUIREMENTS)
    return args


def _default_pip_runner(args: list[str], cwd: Path) -> int:
    from pip._internal.cli.main import main as pip_main

    _ = cwd
    try:
        result = pip_main(args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
        return code
    return int(result or 0)


def _profile_status(directories: AIRuntimeDirectories, variant: str) -> AIRuntimeProfileStatus:
    target_dir = directories.site_packages_dir(variant)
    missing = tuple(
        module_name
        for module_name in AI_RUNTIME_REQUIRED_MODULE_NAMES
        if not _module_present(target_dir, module_name)
    )
    return AIRuntimeProfileStatus(
        variant=variant,
        site_packages_dir=target_dir,
        missing_modules=missing,
    )


def _select_runtime_variant(
    *,
    installed_variants: tuple[str, ...],
    preferred_variant: str,
    device: str,
) -> str:
    normalized_device = (device or "auto").strip().lower()
    installed = {normalize_ai_runtime_variant(variant) for variant in installed_variants}
    if not installed:
        return ""
    if normalized_device == "cpu" and AI_RUNTIME_CPU_VARIANT in installed:
        return AI_RUNTIME_CPU_VARIANT
    if normalized_device in {"cuda", "gpu"} and AI_RUNTIME_GPU_VARIANT in installed:
        return AI_RUNTIME_GPU_VARIANT
    preferred = normalize_ai_runtime_variant(preferred_variant)
    if preferred in installed:
        return preferred
    if AI_RUNTIME_GPU_VARIANT in installed and normalized_device in {"auto", "cuda", "gpu"}:
        return AI_RUNTIME_GPU_VARIANT
    if AI_RUNTIME_CPU_VARIANT in installed:
        return AI_RUNTIME_CPU_VARIANT
    return next(iter(installed))


def _load_ai_runtime_metadata(metadata_path: Path) -> dict[str, object]:
    if not metadata_path.exists():
        return {}
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _module_present(site_packages_dir: Path, module_name: str) -> bool:
    if not site_packages_dir.exists():
        return False
    package_dir = site_packages_dir / module_name
    module_file = site_packages_dir / f"{module_name}.py"
    extension_files = (
        site_packages_dir / f"{module_name}.pyd",
        site_packages_dir / f"{module_name}.so",
    )
    return package_dir.exists() or module_file.exists() or any(path.exists() for path in extension_files)


def _default_user_cache_root() -> Path:
    if os.name == "nt":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    return Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache")))


def _python_runtime_tag() -> str:
    machine = (platform.machine() or "unknown").replace(" ", "_").lower()
    system = (platform.system() or "unknown").replace(" ", "_").lower()
    return f"py{sys.version_info.major}{sys.version_info.minor}-{system}-{machine}"


def _torch_index_url_for_variant(variant: str) -> str:
    if normalize_ai_runtime_variant(variant) == AI_RUNTIME_GPU_VARIANT:
        return os.environ.get("IMAGE_TRIAGE_TORCH_GPU_INDEX_URL", DEFAULT_GPU_TORCH_INDEX_URL)
    return os.environ.get("IMAGE_TRIAGE_TORCH_CPU_INDEX_URL", DEFAULT_CPU_TORCH_INDEX_URL)
