from __future__ import annotations

import os
import shutil
import stat
import sysconfig
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_ICON_WINDOWS_PATH = ROOT / "build_assets" / "icons" / "image_triage-v2.ico"
APP_ICON_LINUX_PATH = ROOT / "build_assets" / "icons" / "image_triage-v2.png"
CLI_CULLER_PACKAGE_ROOT = ROOT / "aiculler"
CLI_EDITOR_PACKAGE_ROOT = ROOT / "cli_editor" / "photo_terminal"
AI_SITE_PACKAGES_STAGE_ROOT = ROOT / "build_assets" / "ai_site_packages"
AI_STDLIB_STAGE_ROOT = ROOT / "build_assets" / "ai_stdlib"
AI_DLLS_STAGE_ROOT = ROOT / "build_assets" / "ai_python_dlls"
QT_WINDOWS_BINARY_EXCLUDES = ("icu.dll", "icuin.dll", "icuuc.dll", "icudt78.dll")
AI_SITE_PACKAGES_ENV = "IMAGE_TRIAGE_AI_SITE_PACKAGES"
AI_STDLIB_ENV = "IMAGE_TRIAGE_AI_STDLIB"
AI_BINARY_MODULES_ENV_NAMES = ("IMAGE_TRIAGE_AI_DLLS", "IMAGE_TRIAGE_AI_BINARY_MODULES")

def _env_flag(name: str, default: str = "0") -> bool:
    value = os.environ.get(name, default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


BUNDLE_AI_RUNTIME_SITE_PACKAGES = _env_flag("IMAGE_TRIAGE_BUNDLE_AI_RUNTIME_SITE_PACKAGES")
AI_SITE_PACKAGES_ENTRIES = (
    "numpy",
    "onnxruntime",
    "torch",
    "torchgen",
    "torchvision",
    "timm",
    "scipy",
    "sklearn",
    "cv2",
    "PIL",
    "tqdm",
    "safetensors",
    "transformers",
    "tokenizers",
    "regex",
    "yaml",
    "fsspec",
    "filelock",
    "packaging",
    "networkx",
    "sympy",
    "mpmath",
    "jinja2",
    "markupsafe",
    "huggingface_hub",
    "requests",
    "urllib3",
    "certifi",
    "charset_normalizer",
    "idna",
    "joblib",
    "threadpoolctl.py",
    "typing_extensions.py",
)
AI_SITE_PACKAGES_OPTIONAL_ENTRIES = (
    "numpy.libs",
    "scipy.libs",
    "scikit_learn.libs",
    "opencv_python_headless.libs",
)
AI_FREEZE_EXCLUDES = (
    "torch",
    "torchgen",
    "torchvision",
    "timm",
    "scipy",
    "sklearn",
    "sympy",
)

@dataclass(frozen=True)
class FreezeAssetLayout:
    ai_site_packages_source: Path
    ai_stdlib_source: Path
    ai_binary_modules_source: Path
    bundle_ai_site_packages: bool = BUNDLE_AI_RUNTIME_SITE_PACKAGES
    ai_site_packages_stage_root: Path = AI_SITE_PACKAGES_STAGE_ROOT
    ai_stdlib_stage_root: Path = AI_STDLIB_STAGE_ROOT
    ai_binary_modules_stage_root: Path = AI_DLLS_STAGE_ROOT

    @property
    def include_files(self) -> list[tuple[str, str]]:
        include_files = [
            (str(ROOT / "packaging" / "ai_runtime_locks"), "packaging/ai_runtime_locks"),
            (
                str(ROOT / "image_triage" / "ui" / "assets" / "splash_background-v7.png"),
                "lib/image_triage/ui/assets/splash_background-v7.png",
            ),
            (
                str(ROOT / "image_triage" / "ui" / "assets" / "app_icon-v2.ico"),
                "lib/image_triage/ui/assets/app_icon-v2.ico",
            ),
            (
                str(ROOT / "image_triage" / "ui" / "assets" / "checkbox_check.png"),
                "lib/image_triage/ui/assets/checkbox_check.png",
            ),
            *_pocketdrop_include_files(),
            (str(CLI_CULLER_PACKAGE_ROOT), "aiculler"),
            (str(CLI_EDITOR_PACKAGE_ROOT), "lib/photo_terminal"),
            (str(ROOT / "image_triage" / "birefnet_worker.py"), "ai_workers/birefnet_worker.py"),
            (str(ROOT / "image_triage" / "oneformer_worker.py"), "ai_workers/oneformer_worker.py"),
            (str(ROOT / "image_triage" / "sam_worker.py"), "ai_workers/sam_worker.py"),
            (str(ROOT / "image_triage" / "depth_worker.py"), "ai_workers/depth_worker.py"),
            (str(ROOT / "image_triage" / "mask_engine_worker.py"), "ai_workers/mask_engine_worker.py"),
            (str(self.ai_stdlib_stage_root), "ai_stdlib"),
            (str(self.ai_binary_modules_stage_root), "lib"),
        ]
        if self.bundle_ai_site_packages:
            include_files.append((str(self.ai_site_packages_stage_root), "ai_site_packages"))
        return include_files


def _pocketdrop_include_files() -> list[tuple[str, str]]:
    """PocketDrop's native library, beside the package that loads it
    (image_triage/pocketdrop/_bridge.py). Build it first with
    native/pocketdrop/build_windows.bat."""
    if os.name != "nt":
        return []
    dll = ROOT / "image_triage" / "pocketdrop" / "pocketdrop.dll"
    if not dll.is_file():
        raise FileNotFoundError(f"{dll} is missing. Run native\\pocketdrop\\build_windows.bat before freezing.")
    return [(str(dll), "lib/image_triage/pocketdrop/pocketdrop.dll")]


def read_project_version() -> str:
    pyproject_path = ROOT / "pyproject.toml"
    if not pyproject_path.exists():
        return "0.1.0"
    for line in pyproject_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("version ="):
            value = stripped.split("=", 1)[1].strip().strip('"').strip("'")
            if value:
                return value
    return "0.1.0"


def resolve_freeze_asset_layout() -> FreezeAssetLayout:
    ai_site_packages_source = _configured_path(AI_SITE_PACKAGES_ENV) or _default_ai_site_packages_source()
    ai_stdlib_source = _configured_path(AI_STDLIB_ENV) or _default_ai_stdlib_source()
    ai_binary_modules_source = _first_configured_path(
        AI_BINARY_MODULES_ENV_NAMES
    ) or _default_ai_binary_modules_source()
    return FreezeAssetLayout(
        ai_site_packages_source=ai_site_packages_source,
        ai_stdlib_source=ai_stdlib_source,
        ai_binary_modules_source=ai_binary_modules_source,
    )


def prepare_ai_build_assets(layout: FreezeAssetLayout | None = None) -> FreezeAssetLayout:
    resolved = layout or resolve_freeze_asset_layout()
    if resolved.bundle_ai_site_packages:
        stage_ai_site_packages(resolved)
    else:
        print(
            "Skipping bundled AI site-packages; the packaged app will install "
            "PyTorch and other large AI dependencies on demand."
        )
    stage_ai_stdlib(resolved)
    stage_ai_binary_modules(resolved)
    return resolved


def _configured_path(env_name: str) -> Path | None:
    raw_value = os.environ.get(env_name)
    if not raw_value:
        return None
    return Path(raw_value).expanduser().resolve()


def _first_configured_path(env_names: tuple[str, ...]) -> Path | None:
    for env_name in env_names:
        candidate = _configured_path(env_name)
        if candidate is not None:
            return candidate
    return None



def _default_ai_site_packages_source() -> Path:
    if os.name == "nt":
        return (ROOT / ".msi_build_venv" / "Lib" / "site-packages").resolve()
    return Path(sysconfig.get_paths()["purelib"]).expanduser().resolve()


def _default_ai_stdlib_source() -> Path:
    if os.name == "nt":
        return Path(os.__file__).resolve().parent
    return Path(sysconfig.get_paths()["stdlib"]).expanduser().resolve()


def _default_ai_binary_modules_source() -> Path:
    if os.name == "nt":
        return _default_ai_stdlib_source().parent / "DLLs"
    destshared = sysconfig.get_config_var("DESTSHARED")
    if destshared:
        candidate = Path(destshared).expanduser()
        if candidate.exists():
            return candidate.resolve()
    return (Path(sysconfig.get_paths()["platstdlib"]) / "lib-dynload").expanduser().resolve()


def _reset_directory(path: Path) -> None:
    def _handle_remove_readonly(function, target_path, excinfo):
        _ = excinfo
        os.chmod(target_path, stat.S_IWRITE)
        function(target_path)

    if path.exists():
        shutil.rmtree(path, onexc=_handle_remove_readonly)
    path.mkdir(parents=True, exist_ok=True)


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Missing AI source directory: {source}")
    shutil.copytree(
        source,
        target,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )


def _copy_file(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Missing AI source file: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)




def _patch_sklearn_distributor_init(site_packages_root: Path) -> None:
    target = site_packages_root / "sklearn" / "_distributor_init.py"
    if not target.exists():
        return
    target.write_text(
        "import os\n"
        "import os.path as op\n"
        "from ctypes import WinDLL\n\n"
        "if os.name == \"nt\":\n"
        "    libs_path = op.join(op.dirname(__file__), \".libs\")\n"
        "    for dll_name in (\"vcomp140.dll\", \"msvcp140.dll\"):\n"
        "        dll_path = op.abspath(op.join(libs_path, dll_name))\n"
        "        if not op.exists(dll_path):\n"
        "            continue\n"
        "        try:\n"
        "            WinDLL(dll_path)\n"
        "        except OSError:\n"
        "            pass\n",
        encoding="utf-8",
    )



def stage_ai_site_packages(layout: FreezeAssetLayout) -> None:
    if not layout.ai_site_packages_source.exists():
        raise FileNotFoundError(
            f"AI site-packages source not found: {layout.ai_site_packages_source}\n"
            "Set IMAGE_TRIAGE_AI_SITE_PACKAGES to a site-packages directory before building."
        )
    _reset_directory(layout.ai_site_packages_stage_root)

    for entry_name in AI_SITE_PACKAGES_ENTRIES:
        source = layout.ai_site_packages_source / entry_name
        target = layout.ai_site_packages_stage_root / entry_name
        if source.is_dir():
            _copy_tree(source, target)
        elif source.is_file():
            _copy_file(source, target)
        else:
            raise FileNotFoundError(
                f"Required bundled AI dependency entry not found: {source}\n"
                f"Install the dependency into {layout.ai_site_packages_source} or set IMAGE_TRIAGE_BUNDLE_AI_RUNTIME_SITE_PACKAGES=0."
            )

    for entry_name in AI_SITE_PACKAGES_OPTIONAL_ENTRIES:
        if entry_name in AI_SITE_PACKAGES_ENTRIES:
            continue
        source = layout.ai_site_packages_source / entry_name
        target = layout.ai_site_packages_stage_root / entry_name
        if source.is_dir():
            _copy_tree(source, target)
        elif source.is_file():
            _copy_file(source, target)
        else:
            print(f"Skipping missing optional AI dependency entry: {source}")

    if os.name == "nt":
        _patch_sklearn_distributor_init(layout.ai_site_packages_stage_root)


def stage_ai_stdlib(layout: FreezeAssetLayout) -> None:
    if not layout.ai_stdlib_source.exists():
        raise FileNotFoundError(
            f"AI stdlib source not found: {layout.ai_stdlib_source}\n"
            "Set IMAGE_TRIAGE_AI_STDLIB to a Python stdlib directory before building."
        )
    _reset_directory(layout.ai_stdlib_stage_root)
    shutil.copytree(
        layout.ai_stdlib_source,
        layout.ai_stdlib_stage_root,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "test",
            "tkinter",
            "turtledemo",
            "idlelib",
            "lib2to3",
            "ensurepip",
            "venv",
        ),
    )


def stage_ai_binary_modules(layout: FreezeAssetLayout) -> None:
    if not layout.ai_binary_modules_source.exists():
        raise FileNotFoundError(
            f"AI binary modules source not found: {layout.ai_binary_modules_source}\n"
            "Set IMAGE_TRIAGE_AI_DLLS or IMAGE_TRIAGE_AI_BINARY_MODULES before building."
        )
    _reset_directory(layout.ai_binary_modules_stage_root)
    shutil.copytree(
        layout.ai_binary_modules_source,
        layout.ai_binary_modules_stage_root,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
