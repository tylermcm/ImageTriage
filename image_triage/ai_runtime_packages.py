from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
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
AI_RUNTIME_BASE_PIP_REQUIREMENTS = (
    "numpy>=1.26",
    "onnxruntime>=1.16",
    "Pillow>=10.4",
    "opencv-python-headless>=4.10",
    "scikit-learn>=1.5",
    "tqdm>=4.66",
    "PyYAML>=6.0",
    # Face-quality pass (detection + landmarks + gender/age via buffalo_l ONNX).
    # Recognition/face-sort is a separate, opt-in path and not pulled here.
    "insightface>=0.7",
)
AI_RUNTIME_DINO_PIP_REQUIREMENTS = (
    "torch",
    "torchvision",
    "timm>=1.0",
    "transformers>=4.56",
    "safetensors>=0.4",
    "tokenizers>=0.15",
)
AI_RUNTIME_PIP_REQUIREMENTS = AI_RUNTIME_BASE_PIP_REQUIREMENTS + AI_RUNTIME_DINO_PIP_REQUIREMENTS
AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES = (
    "numpy",
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
AI_RUNTIME_REQUIRED_MODULE_NAMES = AI_RUNTIME_BASE_REQUIRED_MODULE_NAMES + AI_RUNTIME_DINO_REQUIRED_MODULE_NAMES
AI_RUNTIME_REQUIRED_VERSION_FLOORS = {
    "transformers": (4, 56),
}
AI_RUNTIME_GPU_TORCH_MINIMUM_VERSION = (2, 9, 0)
AI_RUNTIME_ESTIMATED_DOWNLOAD_MB = {
    AI_RUNTIME_CPU_VARIANT: 2600,
    AI_RUNTIME_GPU_VARIANT: 6200,
}
AI_RUNTIME_ESTIMATED_INSTALLED_MB = {
    AI_RUNTIME_CPU_VARIANT: 4300,
    AI_RUNTIME_GPU_VARIANT: 8800,
}

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
    dino_installed_variants: tuple[str, ...] = ()

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


def estimate_ai_runtime_download_size_mb(variant_choice: str) -> int:
    normalized_choice = normalize_ai_runtime_variant(variant_choice, allow_both=True)
    if normalized_choice == AI_RUNTIME_BOTH_VARIANT:
        return sum(AI_RUNTIME_ESTIMATED_DOWNLOAD_MB.values())
    return AI_RUNTIME_ESTIMATED_DOWNLOAD_MB[normalize_ai_runtime_variant(normalized_choice)]


def estimate_ai_runtime_installed_size_mb(variant_choice: str) -> int:
    normalized_choice = normalize_ai_runtime_variant(variant_choice, allow_both=True)
    if normalized_choice == AI_RUNTIME_BOTH_VARIANT:
        return sum(AI_RUNTIME_ESTIMATED_INSTALLED_MB.values())
    return AI_RUNTIME_ESTIMATED_INSTALLED_MB[normalize_ai_runtime_variant(normalized_choice)]


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
    profiles = {
        variant: _profile_status(directories, variant, include_dino=variant in dino_enabled_variants)
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
    dino_installed_variants = tuple(
        variant
        for variant in AI_RUNTIME_VARIANTS
        if variant in dino_enabled_variants
        and _profile_status(directories, variant, include_dino=True).is_installed
    )
    return AIRuntimeInstallationStatus(
        directories=directories,
        profiles=profiles,
        installed_variants=installed_variants,
        preferred_variant=preferred_variant,
        dino_installed_variants=dino_installed_variants,
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
    include_dino: bool = True,
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
        args = build_ai_runtime_pip_install_args(
            variant=variant,
            target_dir=target_dir,
            force=force,
            include_dino=include_dino,
        )
        exit_code = runner(args, directories.root)
        if exit_code != 0:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise RuntimeError(
                f"AI runtime install failed for {ai_runtime_variant_label(variant)} (exit code {exit_code})."
            )
        profile_status = _profile_status(directories, variant, include_dino=include_dino)
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
        "dino_enabled_variants": sorted(
            set(current_status.dino_installed_variants)
            | (set(target_variants) if include_dino else set())
        ),
        "runtime_tag": _python_runtime_tag(),
    }
    directories.metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return load_ai_runtime_installation_status(install_root=install_root)


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
    if force:
        args.extend(["--upgrade", "--force-reinstall"])
    else:
        args.append("--upgrade")
    args.extend(_ai_runtime_pip_requirements_for_variant(normalized, include_dino=include_dino))
    return args


def _ai_runtime_pip_requirements_for_variant(variant: str, *, include_dino: bool = True) -> tuple[str, ...]:
    if not include_dino:
        return AI_RUNTIME_BASE_PIP_REQUIREMENTS
    if normalize_ai_runtime_variant(variant) != AI_RUNTIME_GPU_VARIANT:
        return AI_RUNTIME_PIP_REQUIREMENTS
    return tuple(
        requirement
        for requirement in AI_RUNTIME_PIP_REQUIREMENTS
        if not requirement.partition(">=")[0] in {"torch", "torchvision"}
    ) + (
        f"torch=={_gpu_torch_version_spec()}",
        f"torchvision=={_gpu_torchvision_version_spec()}",
    )


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


def _profile_status(directories: AIRuntimeDirectories, variant: str, *, include_dino: bool = True) -> AIRuntimeProfileStatus:
    target_dir = directories.site_packages_dir(variant)
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
        return Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
    xdg_cache_home = os.environ.get("XDG_CACHE_HOME")
    return Path(xdg_cache_home) if xdg_cache_home else Path.home() / ".cache"


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
