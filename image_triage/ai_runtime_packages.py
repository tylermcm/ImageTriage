from __future__ import annotations

import csv
import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator


AI_RUNTIME_INSTALL_ROOT_ENV = "IMAGE_TRIAGE_AI_RUNTIME_ROOT"
AI_RUNTIME_ACTIVE_VARIANT_ENV = "IMAGE_TRIAGE_AI_TORCH_VARIANT"
AI_RUNTIME_METADATA_FILENAME = "runtime_installation.json"
AI_RUNTIME_PROFILES_DIRNAME = "profiles"
AI_RUNTIME_SITE_PACKAGES_DIRNAME = "site-packages"
AI_RUNTIME_INSTALL_LOCK_FILENAME = ".install.lock"
AI_RUNTIME_METADATA_VERSION = 2
AI_RUNTIME_CPU_VARIANT = "cpu"
AI_RUNTIME_GPU_VARIANT = "gpu"
AI_RUNTIME_BOTH_VARIANT = "both"
AI_RUNTIME_VARIANTS = (AI_RUNTIME_CPU_VARIANT, AI_RUNTIME_GPU_VARIANT)
AI_RUNTIME_ONNX_CPU_REQUIREMENT = "onnxruntime>=1.16"
# ONNX Runtime 1.27+ PyPI GPU wheels target CUDA 13. Keep the managed
# CUDA-12.8 PyTorch and ONNX runtimes on the same CUDA generation.
AI_RUNTIME_ONNX_GPU_REQUIREMENT = "onnxruntime-gpu>=1.26,<1.27"
AI_RUNTIME_TRANSFORMERS_REQUIREMENT = "transformers==5.14.1"
AI_RUNTIME_INSTALL_CHOICES = (*AI_RUNTIME_VARIANTS, AI_RUNTIME_BOTH_VARIANT)
DEFAULT_CPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cpu"
DEFAULT_GPU_TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu128"
AI_RUNTIME_BASE_PIP_REQUIREMENTS = (
    "numpy>=1.26",
    "onnx>=1.16",
    AI_RUNTIME_ONNX_CPU_REQUIREMENT,
    "Pillow>=10.4",
    "opencv-python-headless>=4.10",
    "scikit-learn>=1.5",
    "tqdm>=4.66",
    "PyYAML>=6.0",
    # Face-quality pass (detection + landmarks + gender/age via the AuraFace ONNX pack).
    # Recognition/face-sort is a separate, opt-in path and not pulled here.
    "insightface>=0.7",
)
AI_RUNTIME_DINO_PIP_REQUIREMENTS = (
    "torch",
    "torchvision",
    "timm>=1.0",
    AI_RUNTIME_TRANSFORMERS_REQUIREMENT,
    "safetensors>=0.4",
    "tokenizers>=0.15",
)
AI_RUNTIME_PIP_REQUIREMENTS = AI_RUNTIME_BASE_PIP_REQUIREMENTS + AI_RUNTIME_DINO_PIP_REQUIREMENTS
AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES = (
    "numpy",
    "onnx",
    "onnxruntime",
    "cv2",
    "sklearn",
    "PIL",
    "yaml",
    "tqdm",
    "insightface",
)
AI_RUNTIME_DINO_REQUIRED_MODULE_NAMES = (
    "torch",
    "torchvision",
    "timm",
    "transformers",
    "safetensors",
    "tokenizers",
)
AI_RUNTIME_BASE_REQUIRED_FILES = (
    (Path("insightface/model_zoo/model_store.py"), "insightface package files"),
)
AI_RUNTIME_DINO_REQUIRED_FILES = (
    (
        Path("transformers/models/audio_spectrogram_transformer/configuration_audio_spectrogram_transformer.py"),
        "transformers package files",
    ),
    (Path("transformers/models/oneformer/configuration_oneformer.py"), "transformers package files"),
    (Path("transformers/models/sam2/configuration_sam2.py"), "transformers package files"),
    (
        Path("transformers/models/depth_anything/configuration_depth_anything.py"),
        "transformers package files",
    ),
)
AI_RUNTIME_REQUIRED_MODULE_NAMES = AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES + AI_RUNTIME_DINO_REQUIRED_MODULE_NAMES
AI_RUNTIME_REQUIRED_VERSION_FLOORS = {
    "transformers": (4, 56),
}
AI_RUNTIME_GPU_TORCH_MINIMUM_VERSION = (2, 9, 0)
AI_RUNTIME_ESTIMATED_DOWNLOAD_MB = {
    AI_RUNTIME_CPU_VARIANT: 2600,
    AI_RUNTIME_GPU_VARIANT: 6600,
}
AI_RUNTIME_ESTIMATED_INSTALLED_MB = {
    AI_RUNTIME_CPU_VARIANT: 4300,
    AI_RUNTIME_GPU_VARIANT: 9300,
}
# The active CLI-Culler workflow is ONNX-based and does not need the legacy
# PyTorch/DINO stack. These estimates cover the compact base runtime only.
AI_RUNTIME_BASE_ESTIMATED_DOWNLOAD_MB = {
    AI_RUNTIME_CPU_VARIANT: 350,
    AI_RUNTIME_GPU_VARIANT: 650,
}
AI_RUNTIME_BASE_ESTIMATED_INSTALLED_MB = {
    AI_RUNTIME_CPU_VARIANT: 1050,
    AI_RUNTIME_GPU_VARIANT: 1750,
}

PipRunner = Callable[[list[str], Path], int]
ProfileValidator = Callable[[Path, str, bool], None]


@dataclass(frozen=True)
class AIRuntimeDirectories:
    root: Path
    metadata_path: Path
    profiles_root: Path

    def site_packages_dir(self, variant: str) -> Path:
        normalized = normalize_ai_runtime_variant(variant)
        metadata = _load_ai_runtime_metadata(self.metadata_path)
        generation = _profile_generation(metadata, normalized)
        profile_name = generation or normalized
        return self.profiles_root / profile_name / AI_RUNTIME_SITE_PACKAGES_DIRNAME

    def generated_site_packages_dir(self, generation: str) -> Path:
        return self.profiles_root / generation / AI_RUNTIME_SITE_PACKAGES_DIRNAME


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
    dino_installed_variants: tuple[str, ...] = ()
    onnx_gpu_installed_variants: tuple[str, ...] = ()

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


def estimate_ai_runtime_download_size_mb(
    variant_choice: str,
    *,
    include_dino: bool = True,
) -> int:
    normalized_choice = normalize_ai_runtime_variant(variant_choice, allow_both=True)
    estimates = (
        AI_RUNTIME_ESTIMATED_DOWNLOAD_MB
        if include_dino
        else AI_RUNTIME_BASE_ESTIMATED_DOWNLOAD_MB
    )
    if normalized_choice == AI_RUNTIME_BOTH_VARIANT:
        return sum(estimates.values())
    return estimates[normalize_ai_runtime_variant(normalized_choice)]


def estimate_ai_runtime_installed_size_mb(
    variant_choice: str,
    *,
    include_dino: bool = True,
) -> int:
    normalized_choice = normalize_ai_runtime_variant(variant_choice, allow_both=True)
    estimates = (
        AI_RUNTIME_ESTIMATED_INSTALLED_MB
        if include_dino
        else AI_RUNTIME_BASE_ESTIMATED_INSTALLED_MB
    )
    if normalized_choice == AI_RUNTIME_BOTH_VARIANT:
        return sum(estimates.values())
    return estimates[normalize_ai_runtime_variant(normalized_choice)]


def directory_size_bytes(path: str | Path) -> int:
    root = Path(path)
    if not root.exists():
        return 0
    total = 0
    for child in root.rglob("*"):
        if not child.is_file():
            continue
        try:
            total += child.stat().st_size
        except OSError:
            continue
    return total


def default_ai_runtime_install_root() -> Path:
    if os.name == "nt":
        # Store Python redirects LocalAppData writes back into its package cache,
        # recreating the very long path this location is meant to avoid.
        user_profile = os.environ.get("USERPROFILE")
        cache_root = (
            Path(user_profile) if user_profile else Path.home()
        ) / ".image-triage" / "AI" / "rt"
    else:
        cache_root = _default_user_cache_root() / "ImageTriage" / "AI" / "rt"
    root = cache_root / _python_runtime_tag()
    _migrate_legacy_runtime(root)
    return root


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
    dino_enabled_value = metadata.get("dino_enabled_variants")
    if isinstance(dino_enabled_value, list):
        dino_enabled_variants = {
            normalize_ai_runtime_variant(str(variant))
            for variant in dino_enabled_value
        }
    else:
        installed_metadata = metadata.get("installed_variants")
        dino_enabled_variants = {
            normalize_ai_runtime_variant(str(variant))
            for variant in installed_metadata
        } if isinstance(installed_metadata, list) else set(AI_RUNTIME_VARIANTS)
    profiles = {}
    for variant in AI_RUNTIME_VARIANTS:
        generation = _profile_generation(metadata, variant)
        target_dir = (
            directories.generated_site_packages_dir(generation)
            if generation
            else directories.profiles_root / variant / AI_RUNTIME_SITE_PACKAGES_DIRNAME
        )
        profiles[variant] = _profile_status(
            directories,
            variant,
            include_dino=variant in dino_enabled_variants,
            site_packages_dir=target_dir,
        )
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
    dino_installed_variants = tuple(
        variant
        for variant in AI_RUNTIME_VARIANTS
        if variant in dino_enabled_variants
        and _profile_status(directories, variant, include_dino=True).is_installed
    )
    onnx_gpu_installed_variants = tuple(
        variant
        for variant in AI_RUNTIME_VARIANTS
        if variant == AI_RUNTIME_GPU_VARIANT
        and bool(_installed_distribution_version(profiles[variant].site_packages_dir, "onnxruntime-gpu"))
    )
    return AIRuntimeInstallationStatus(
        directories=directories,
        profiles=profiles,
        installed_variants=installed_variants,
        preferred_variant=preferred_variant,
        dino_installed_variants=dino_installed_variants,
        onnx_gpu_installed_variants=onnx_gpu_installed_variants,
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
    return (status.profiles[variant].site_packages_dir,)


def install_ai_runtime(
    variant_choice: str,
    *,
    force: bool = False,
    include_dino: bool = True,
    install_root: str | Path | None = None,
    output_callback: Callable[[str], None] | None = None,
    pip_runner: PipRunner | None = None,
    profile_validator: ProfileValidator | None = None,
) -> AIRuntimeInstallationStatus:
    normalized_choice = normalize_ai_runtime_variant(variant_choice, allow_both=True)
    target_variants = (
        AI_RUNTIME_VARIANTS if normalized_choice == AI_RUNTIME_BOTH_VARIANT else (normalized_choice,)
    )
    directories = resolve_ai_runtime_directories(install_root=install_root)
    directories.root.mkdir(parents=True, exist_ok=True)
    directories.profiles_root.mkdir(parents=True, exist_ok=True)
    runner = pip_runner or _default_pip_runner
    validator = profile_validator or _validate_profile_in_subprocess

    with _ai_runtime_install_lock(directories.root):
        current_status = load_ai_runtime_installation_status(install_root=install_root)
        current_metadata = _load_ai_runtime_metadata(directories.metadata_path)
        installed_variants = set(current_status.installed_variants)
        staged_profiles: dict[str, tuple[str, Path]] = {}
        try:
            for variant in target_variants:
                generation = f"{variant}-{uuid.uuid4().hex[:12]}"
                target_dir = directories.generated_site_packages_dir(generation)
                target_dir.mkdir(parents=True, exist_ok=False)
                staged_profiles[variant] = (generation, target_dir)
                if output_callback is not None:
                    output_callback(
                        f"Installing {ai_runtime_variant_label(variant)} AI runtime packages to a new profile"
                    )
                args = build_ai_runtime_pip_install_args(
                    variant=variant,
                    target_dir=target_dir,
                    force=force,
                    include_dino=include_dino,
                )
                exit_code = runner(args, directories.root)
                if exit_code != 0:
                    raise RuntimeError(
                        f"AI runtime install failed for {ai_runtime_variant_label(variant)} "
                        f"(exit code {exit_code}). The existing runtime was not changed."
                    )
                profile_status = _profile_status(
                    directories,
                    variant,
                    include_dino=include_dino,
                    site_packages_dir=target_dir,
                )
                if not profile_status.is_installed:
                    missing = ", ".join(profile_status.missing_modules)
                    raise RuntimeError(
                        f"AI runtime validation failed for {ai_runtime_variant_label(variant)}: "
                        f"required modules are missing: {missing}. The existing runtime was not changed."
                    )
                if pip_runner is None:
                    _validate_distribution_records(target_dir)
                    validator(target_dir, variant, include_dino)
                elif profile_validator is not None:
                    validator(target_dir, variant, include_dino)
                installed_variants.add(variant)

            preferred_variant = (
                AI_RUNTIME_GPU_VARIANT
                if normalized_choice in {AI_RUNTIME_GPU_VARIANT, AI_RUNTIME_BOTH_VARIANT}
                else AI_RUNTIME_CPU_VARIANT
            )
            generations = {
                variant: generation
                for variant, generation in _profile_generations(current_metadata).items()
                if variant in installed_variants
            }
            generations.update(
                {variant: generation for variant, (generation, _path) in staged_profiles.items()}
            )
            dino_enabled = set(current_status.dino_installed_variants) - set(target_variants)
            if include_dino:
                dino_enabled.update(target_variants)
            metadata = {
                "metadata_version": AI_RUNTIME_METADATA_VERSION,
                "installed_variants": sorted(installed_variants),
                "preferred_variant": preferred_variant,
                "dino_enabled_variants": sorted(dino_enabled),
                "profile_generations": generations,
                "runtime_tag": _python_runtime_tag(),
            }
            _write_json_atomic(directories.metadata_path, metadata)
        except Exception:
            for generation, _target_dir in staged_profiles.values():
                _remove_tree_with_retry(directories.profiles_root / generation)
            raise

        active_profile_names = set(generations.values()) | {
            variant for variant in installed_variants if variant not in generations
        }
        _cleanup_inactive_profiles(
            directories,
            active_profile_names=active_profile_names,
            output_callback=output_callback,
        )
    return load_ai_runtime_installation_status(install_root=install_root)


def uninstall_ai_runtime(*, install_root: str | Path | None = None) -> bool:
    """Remove the entire installed AI runtime directory (all profiles + metadata).

    Returns True if a runtime directory existed and was removed."""
    directories = resolve_ai_runtime_directories(install_root=install_root)
    if not directories.root.exists():
        return False
    _remove_tree_with_retry(directories.root)
    return not directories.root.exists()


def build_ai_runtime_pip_install_args(
    *,
    variant: str,
    target_dir: str | Path,
    force: bool = False,
    include_dino: bool = True,
) -> list[str]:
    normalized = normalize_ai_runtime_variant(variant)
    args = [
        "install",
        "--disable-pip-version-check",
        "--no-input",
        "--no-warn-script-location",
        "--progress-bar",
        "raw",
        "--target",
        str(Path(target_dir)),
    ]
    args.append("--prefer-binary")
    if normalized == AI_RUNTIME_GPU_VARIANT:
        args.extend(["--extra-index-url", _torch_index_url_for_variant(normalized)])
        args.extend(["--only-binary=:all:"])
    else:
        args.extend(["--extra-index-url", _torch_index_url_for_variant(normalized)])
        args.extend(["--only-binary=:all:"])
    # Every install targets a fresh generation, so pip never has to replace a
    # live package tree. Ignore global packages to ensure the target is complete.
    del force
    args.extend(["--ignore-installed", "--no-compile"])
    args.extend(_ai_runtime_pip_requirements_for_variant(normalized, include_dino=include_dino))
    return args


def _ai_runtime_pip_requirements_for_variant(variant: str, *, include_dino: bool = True) -> tuple[str, ...]:
    normalized = normalize_ai_runtime_variant(variant)
    requirements = AI_RUNTIME_PIP_REQUIREMENTS if include_dino else AI_RUNTIME_BASE_PIP_REQUIREMENTS
    if normalized == AI_RUNTIME_GPU_VARIANT:
        requirements = tuple(
            AI_RUNTIME_ONNX_GPU_REQUIREMENT
            if requirement.partition(">=")[0] == "onnxruntime"
            else requirement
            for requirement in requirements
        )
    if not include_dino:
        return requirements
    pinned_requirements = tuple(
        requirement
        for requirement in requirements
        if not requirement.partition("==")[0] in {"torch", "torchvision"}
    )
    if normalized == AI_RUNTIME_GPU_VARIANT:
        return pinned_requirements + (
            f"torch=={_gpu_torch_version_spec()}",
            f"torchvision=={_gpu_torchvision_version_spec()}",
        )
    return pinned_requirements + (
        f"torch=={_cpu_torch_version_spec()}",
        f"torchvision=={_cpu_torchvision_version_spec()}",
    )


def _cpu_torch_version_spec() -> str:
    return os.environ.get("IMAGE_TRIAGE_TORCH_CPU_VERSION", "2.9.0")


def _cpu_torchvision_version_spec() -> str:
    return os.environ.get("IMAGE_TRIAGE_TORCHVISION_CPU_VERSION", "0.24.0")


def _gpu_torch_version_spec() -> str:
    return os.environ.get("IMAGE_TRIAGE_TORCH_GPU_VERSION", "2.9.0+cu128")


def _gpu_torchvision_version_spec() -> str:
    return os.environ.get("IMAGE_TRIAGE_TORCHVISION_GPU_VERSION", "0.24.0+cu128")


def _default_pip_runner(args: list[str], cwd: Path) -> int:
    if getattr(sys, "frozen", False):
        return _run_embedded_pip(args, cwd)
    process = subprocess.run(
        [sys.executable, "-m", "pip", *args],
        cwd=str(cwd),
        text=True,
    )
    return int(process.returncode)


def _run_embedded_pip(args: list[str], cwd: Path) -> int:
    """Run pip inside a frozen helper executable.

    In cx_Freeze builds, sys.executable is ai_runtime_installer.exe rather than
    a python.exe. Spawning ``sys.executable -m pip`` re-enters this installer and
    sends pip's argv to our argparse parser. Importing pip directly avoids that
    recursion while keeping source runs on the normal subprocess path.
    """

    previous_cwd = Path.cwd()
    try:
        os.chdir(cwd)
        try:
            from pip._internal.cli.main import main as pip_main
        except Exception as exc:
            print(f"Could not import bundled pip: {exc}", file=sys.stderr)
            return 2
        try:
            return int(pip_main(list(args)))
        except SystemExit as exc:
            code = exc.code
            return int(code) if isinstance(code, int) else 1
    finally:
        os.chdir(previous_cwd)


def _profile_status(
    directories: AIRuntimeDirectories,
    variant: str,
    *,
    include_dino: bool = True,
    site_packages_dir: Path | None = None,
) -> AIRuntimeProfileStatus:
    target_dir = site_packages_dir or directories.site_packages_dir(variant)
    missing_items: list[str] = []
    module_names = AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES + (
        AI_RUNTIME_DINO_REQUIRED_MODULE_NAMES if include_dino else ()
    )
    for module_name in module_names:
        if not _module_present(target_dir, module_name):
            missing_items.append(module_name)
            continue
        minimum_version = AI_RUNTIME_REQUIRED_VERSION_FLOORS.get(module_name)
        if minimum_version and not _module_version_at_least(target_dir, module_name, minimum_version):
            missing_items.append(f"{module_name}>={'.'.join(str(part) for part in minimum_version)}")
    required_files = AI_RUNTIME_BASE_REQUIRED_FILES + (
        AI_RUNTIME_DINO_REQUIRED_FILES if include_dino else ()
    )
    for relative_path, missing_label in required_files:
        if not (target_dir / relative_path).is_file() and missing_label not in missing_items:
            missing_items.append(missing_label)
    if include_dino and normalize_ai_runtime_variant(variant) == AI_RUNTIME_GPU_VARIANT:
        if not _torch_cuda_binaries_present(target_dir):
            missing_items.append("torch CUDA binaries")
        if not _torch_runtime_version_at_least(target_dir, AI_RUNTIME_GPU_TORCH_MINIMUM_VERSION):
            missing_items.append("torch>=2.9.0+cu128")
    missing = tuple(missing_items)
    return AIRuntimeProfileStatus(
        variant=variant,
        site_packages_dir=target_dir,
        missing_modules=missing,
    )


def _torch_cuda_binaries_present(site_packages_dir: Path) -> bool:
    torch_lib = site_packages_dir / "torch" / "lib"
    if not torch_lib.exists():
        return False
    cuda_dll_names = {
        "c10_cuda.dll",
        "torch_cuda.dll",
        "torch_cuda_cu.dll",
        "torch_cuda_cpp.dll",
    }
    try:
        return any((torch_lib / name).exists() for name in cuda_dll_names)
    except OSError:
        return False


def _torch_runtime_version_at_least(site_packages_dir: Path, minimum_version: tuple[int, ...]) -> bool:
    version = _torch_import_version(site_packages_dir) or _installed_distribution_version(site_packages_dir, "torch")
    if not version:
        return False
    parsed = _parse_version_prefix(version, parts=len(minimum_version))
    if parsed is None:
        return False
    return parsed >= minimum_version


def _torch_import_version(site_packages_dir: Path) -> str:
    version_path = site_packages_dir / "torch" / "version.py"
    if not version_path.exists():
        return ""
    try:
        text = version_path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    match = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", text)
    return match.group(1).strip() if match else ""


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


def _profile_generations(metadata: dict[str, object]) -> dict[str, str]:
    value = metadata.get("profile_generations")
    if not isinstance(value, dict):
        return {}
    generations: dict[str, str] = {}
    for variant in AI_RUNTIME_VARIANTS:
        generation = value.get(variant)
        if not isinstance(generation, str):
            continue
        generation = generation.strip()
        if (
            generation
            and Path(generation).name == generation
            and generation.startswith(f"{variant}-")
        ):
            generations[variant] = generation
    return generations


def _profile_generation(metadata: dict[str, object], variant: str) -> str:
    normalized = normalize_ai_runtime_variant(variant)
    return _profile_generations(metadata).get(normalized, "")


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


@contextmanager
def _ai_runtime_install_lock(root: Path) -> Iterator[None]:
    lock_path = root.parent / f".{root.name}{AI_RUNTIME_INSTALL_LOCK_FILENAME}"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another AI runtime installation is already running.") from exc
        try:
            yield
        finally:
            lock_file.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass


def _remove_tree_with_retry(path: Path, *, attempts: int = 6) -> bool:
    if not path.exists():
        return True
    for attempt in range(max(1, attempts)):
        try:
            shutil.rmtree(path)
            return True
        except FileNotFoundError:
            return True
        except OSError:
            if attempt + 1 >= attempts:
                break
            time.sleep(0.15 * (attempt + 1))
    return not path.exists()


def _cleanup_inactive_profiles(
    directories: AIRuntimeDirectories,
    *,
    active_profile_names: set[str],
    output_callback: Callable[[str], None] | None,
) -> None:
    try:
        children = tuple(directories.profiles_root.iterdir())
    except OSError:
        return
    for child in children:
        if not child.is_dir() or child.name in active_profile_names:
            continue
        if child.name not in AI_RUNTIME_VARIANTS and not any(
            child.name.startswith(f"{variant}-") for variant in AI_RUNTIME_VARIANTS
        ):
            continue
        if not _remove_tree_with_retry(child) and output_callback is not None:
            output_callback(
                f"The previous inactive AI profile could not be removed yet: {child.name}"
            )


def _validate_distribution_records(site_packages_dir: Path) -> None:
    missing: list[str] = []
    records = tuple(site_packages_dir.glob("*.dist-info/RECORD"))
    if not records:
        raise RuntimeError(
            "AI runtime validation found no installed-package records. "
            "The existing runtime was not changed."
        )
    root = site_packages_dir.resolve()
    for record_path in records:
        try:
            with record_path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
                rows = tuple(csv.reader(stream))
        except OSError as exc:
            raise RuntimeError(
                f"AI runtime validation could not read {record_path.name}: {exc}. "
                "The existing runtime was not changed."
            ) from exc
        for row in rows:
            if not row or not row[0]:
                continue
            candidate = (site_packages_dir / row[0]).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if not candidate.exists():
                missing.append(row[0])
                if len(missing) >= 12:
                    break
        if len(missing) >= 12:
            break
    if missing:
        examples = ", ".join(missing[:3])
        raise RuntimeError(
            f"AI runtime validation found {len(missing)} missing installed files "
            f"(for example: {examples}). The existing runtime was not changed."
        )


def validate_ai_runtime_imports(
    site_packages_dir: str | Path,
    *,
    variant: str,
    include_dino: bool,
) -> None:
    """Import the installed AI surface from an isolated validator process."""

    target = Path(site_packages_dir).resolve()
    target_text = str(target)
    if target_text in sys.path:
        sys.path.remove(target_text)
    sys.path.insert(0, target_text)
    importlib.invalidate_caches()
    dll_handles: list[object] = []
    if os.name == "nt" and hasattr(os, "add_dll_directory"):
        for dll_dir in (target, target / "torch" / "lib", target / "onnxruntime" / "capi"):
            if dll_dir.exists():
                dll_handles.append(os.add_dll_directory(str(dll_dir)))

    modules = AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES + (
        AI_RUNTIME_DINO_REQUIRED_MODULE_NAMES if include_dino else ()
    )
    for module_name in modules:
        module = importlib.import_module(module_name)
        module_path = Path(getattr(module, "__file__", "") or "").resolve()
        try:
            module_path.relative_to(target)
        except ValueError as exc:
            raise RuntimeError(
                f"{module_name} was loaded outside the managed AI runtime: {module_path}"
            ) from exc

    if include_dino:
        transformers = importlib.import_module("transformers")
        required_symbols = (
            "AutoImageProcessor",
            "AutoModelForDepthEstimation",
            "AutoModelForImageSegmentation",
            "OneFormerForUniversalSegmentation",
            "OneFormerProcessor",
            "Sam2Model",
            "Sam2Processor",
        )
        for symbol in required_symbols:
            getattr(transformers, symbol)
        if normalize_ai_runtime_variant(variant) == AI_RUNTIME_GPU_VARIANT:
            torch = importlib.import_module("torch")
            if not str(getattr(torch, "__version__", "")).lower().endswith("cu128"):
                raise RuntimeError("The GPU profile did not load the required CUDA 12.8 PyTorch build.")


def _validate_profile_in_subprocess(
    site_packages_dir: Path,
    variant: str,
    include_dino: bool,
) -> None:
    if getattr(sys, "frozen", False):
        command = [sys.executable, "validate-profile"]
    else:
        installer = Path(__file__).resolve().parents[1] / "packaging" / "ai_runtime_installer.py"
        command = [sys.executable, str(installer), "validate-profile"]
    command.extend(["--site-packages", str(site_packages_dir), "--variant", variant])
    if not include_dino:
        command.append("--no-dino")
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "1"
    env["TRANSFORMERS_OFFLINE"] = "1"
    env["HF_HUB_OFFLINE"] = "1"
    process = subprocess.run(
        command,
        cwd=str(site_packages_dir.parent),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if process.returncode == 0:
        return
    detail = (process.stderr or process.stdout or "unknown import error").strip().splitlines()
    tail = " | ".join(detail[-4:])
    raise RuntimeError(
        f"AI runtime import validation failed for {ai_runtime_variant_label(variant)}: {tail}. "
        "The existing runtime was not changed."
    )


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


def _module_version_at_least(
    site_packages_dir: Path,
    module_name: str,
    minimum_version: tuple[int, ...],
) -> bool:
    version = _installed_distribution_version(site_packages_dir, module_name)
    if not version:
        return False
    parsed = _parse_version_prefix(version, parts=len(minimum_version))
    if parsed is None:
        return False
    return parsed >= minimum_version


def _installed_distribution_version(site_packages_dir: Path, package_name: str) -> str:
    normalized = package_name.replace("_", "-").lower()
    for metadata_dir in site_packages_dir.glob("*.dist-info"):
        metadata_path = metadata_dir / "METADATA"
        if metadata_path.exists():
            try:
                metadata = metadata_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                metadata = ""
            name = ""
            version = ""
            for line in metadata.splitlines():
                if line.lower().startswith("name:"):
                    name = line.split(":", 1)[1].strip().replace("_", "-").lower()
                elif line.lower().startswith("version:"):
                    version = line.split(":", 1)[1].strip()
                if name and version:
                    break
            if name == normalized and version:
                return version

        stem = metadata_dir.name.removesuffix(".dist-info")
        if "-" not in stem:
            continue
        name_part, version_part = stem.rsplit("-", 1)
        if name_part.replace("_", "-").lower() == normalized:
            return version_part
    return ""


def _parse_version_prefix(version: str, *, parts: int) -> tuple[int, ...] | None:
    tokens = [token for token in re.split(r"[^\d]+", version) if token]
    if len(tokens) < parts:
        return None
    try:
        return tuple(int(token) for token in tokens[:parts])
    except ValueError:
        return None


def _default_user_cache_root() -> Path:
    if os.name == "nt":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidate = Path(local_appdata)
            normalized = str(candidate).replace("/", "\\").lower()
            is_store_virtualized = (
                "\\packages\\pythonsoftwarefoundation.python." in normalized
                and "\\localcache\\local" in normalized
            )
            user_profile = os.environ.get("USERPROFILE")
            if is_store_virtualized and user_profile:
                return Path(user_profile) / "AppData" / "Local"
            return candidate
        user_profile = os.environ.get("USERPROFILE")
        return Path(user_profile) / "AppData" / "Local" if user_profile else Path.home() / "AppData" / "Local"
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"


def _legacy_ai_runtime_roots() -> tuple[Path, ...]:
    candidates: list[Path] = []
    raw_local_appdata = os.environ.get("LOCALAPPDATA") if os.name == "nt" else os.environ.get("XDG_CACHE_HOME")
    if raw_local_appdata:
        raw_cache = Path(raw_local_appdata)
        candidates.extend(
            (
                raw_cache / "image_triage_ai_cache" / "runtime" / _python_runtime_tag(),
                raw_cache / "ImageTriage" / "AI" / "rt" / _python_runtime_tag(),
            )
        )
    candidates.append(
        _default_user_cache_root() / "image_triage_ai_cache" / "runtime" / _python_runtime_tag()
    )
    candidates.append(
        _default_user_cache_root() / "ImageTriage" / "AI" / "rt" / _python_runtime_tag()
    )
    unique: list[Path] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _migrate_legacy_runtime(new_root: Path) -> None:
    if new_root.exists():
        return
    for legacy_root in _legacy_ai_runtime_roots():
        if legacy_root == new_root or not legacy_root.exists():
            continue
        try:
            new_root.parent.mkdir(parents=True, exist_ok=True)
            os.replace(legacy_root, new_root)
        except OSError:
            continue
        return


def _python_runtime_tag() -> str:
    machine = (platform.machine() or "").replace(" ", "_").lower()
    if not machine and os.name == "nt":
        machine = (
            os.environ.get("PROCESSOR_ARCHITEW6432")
            or os.environ.get("PROCESSOR_ARCHITECTURE")
            or ""
        ).replace(" ", "_").lower()
    if machine in {"amd64", "x86_64"}:
        machine = "amd64"
    elif not machine and platform.architecture()[0] == "64bit":
        machine = "amd64"
    elif not machine:
        machine = "unknown"
    system = (platform.system() or "unknown").replace(" ", "_").lower()
    return f"py{sys.version_info.major}{sys.version_info.minor}-{system}-{machine}"


def _torch_index_url_for_variant(variant: str) -> str:
    if normalize_ai_runtime_variant(variant) == AI_RUNTIME_GPU_VARIANT:
        return os.environ.get("IMAGE_TRIAGE_TORCH_GPU_INDEX_URL", DEFAULT_GPU_TORCH_INDEX_URL)
    return os.environ.get("IMAGE_TRIAGE_TORCH_CPU_INDEX_URL", DEFAULT_CPU_TORCH_INDEX_URL)
