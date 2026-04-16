from __future__ import annotations

import os
import shutil
import stat
import sysconfig
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
APP_ICON_WINDOWS_PATH = ROOT / "build_assets" / "icons" / "image_triage.ico"
APP_ICON_LINUX_PATH = ROOT / "build_assets" / "icons" / "image_triage.png"
AI_STAGE_ROOT = ROOT / "build_assets" / "ai_runtime" / "AICullingPipeline"
AI_SITE_PACKAGES_STAGE_ROOT = ROOT / "build_assets" / "ai_site_packages"
AI_STDLIB_STAGE_ROOT = ROOT / "build_assets" / "ai_stdlib"
AI_DLLS_STAGE_ROOT = ROOT / "build_assets" / "ai_python_dlls"
STAGE_SCRIPT_NAMES = (
    "extract_embeddings.py",
    "cluster_embeddings.py",
    "export_ranked_report.py",
)
AI_SITE_PACKAGES_ENV = "IMAGE_TRIAGE_AI_SITE_PACKAGES"
AI_STDLIB_ENV = "IMAGE_TRIAGE_AI_STDLIB"
AI_BINARY_MODULES_ENV_NAMES = ("IMAGE_TRIAGE_AI_DLLS", "IMAGE_TRIAGE_AI_BINARY_MODULES")
AI_SOURCE_ENV_NAMES = ("IMAGE_TRIAGE_AI_SOURCE", "AICULLING_ENGINE_ROOT")


def _env_flag(name: str, default: str = "0") -> bool:
    value = os.environ.get(name, default)
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


INCLUDE_LOCAL_BACKBONE = _env_flag("IMAGE_TRIAGE_INCLUDE_LOCAL_MODEL")
INCLUDE_DEFAULT_RANKER = _env_flag("IMAGE_TRIAGE_INCLUDE_DEFAULT_RANKER")

AI_SITE_PACKAGES_ENTRIES = (
    "numpy",
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
)
SCRIPT_BOOTSTRAP_MARKER = "# image_triage-bootstrap: ensure bundled AI pipeline imports resolve"
SCRIPT_BOOTSTRAP = f"""{SCRIPT_BOOTSTRAP_MARKER}
import sys
from pathlib import Path as _ImageTriageBootstrapPath


def _image_triage_add_engine_root_to_path() -> None:
    cwd = _ImageTriageBootstrapPath.cwd()
    if (cwd / "app").exists():
        cwd_text = str(cwd)
        if cwd_text not in sys.path:
            sys.path.insert(0, cwd_text)
        return

    script_dir = _ImageTriageBootstrapPath(__file__).resolve().parent
    engine_root = script_dir.parent
    if (engine_root / "app").exists():
        engine_root_text = str(engine_root)
        if engine_root_text not in sys.path:
            sys.path.insert(0, engine_root_text)


_image_triage_add_engine_root_to_path()
del _image_triage_add_engine_root_to_path
"""


@dataclass(frozen=True)
class FreezeAssetLayout:
    ai_source: Path
    ai_site_packages_source: Path
    ai_stdlib_source: Path
    ai_binary_modules_source: Path
    ai_stage_root: Path = AI_STAGE_ROOT
    ai_site_packages_stage_root: Path = AI_SITE_PACKAGES_STAGE_ROOT
    ai_stdlib_stage_root: Path = AI_STDLIB_STAGE_ROOT
    ai_binary_modules_stage_root: Path = AI_DLLS_STAGE_ROOT

    @property
    def include_files(self) -> list[tuple[str, str]]:
        return [
            (str(self.ai_stage_root.parent), "ai_runtime"),
            (str(self.ai_site_packages_stage_root), "ai_site_packages"),
            (str(self.ai_stdlib_stage_root), "ai_stdlib"),
            (str(self.ai_binary_modules_stage_root), "lib"),
        ]


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
    ai_source = _first_configured_path(AI_SOURCE_ENV_NAMES) or _discover_ai_source_root()
    ai_site_packages_source = _configured_path(AI_SITE_PACKAGES_ENV) or _default_ai_site_packages_source()
    ai_stdlib_source = _configured_path(AI_STDLIB_ENV) or _default_ai_stdlib_source()
    ai_binary_modules_source = _first_configured_path(
        AI_BINARY_MODULES_ENV_NAMES
    ) or _default_ai_binary_modules_source()
    return FreezeAssetLayout(
        ai_source=ai_source,
        ai_site_packages_source=ai_site_packages_source,
        ai_stdlib_source=ai_stdlib_source,
        ai_binary_modules_source=ai_binary_modules_source,
    )


def prepare_ai_build_assets(layout: FreezeAssetLayout | None = None) -> FreezeAssetLayout:
    resolved = layout or resolve_freeze_asset_layout()
    stage_ai_runtime(resolved)
    stage_ai_site_packages(resolved)
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


def _discover_ai_source_root() -> Path:
    candidates = [
        ROOT / "AICullingPipeline",
        Path.home() / "Documents" / "GitHub" / "AICullingPipeline",
        Path.home() / "GitHub" / "AICullingPipeline",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


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


def _inject_stage_script_bootstrap(script_path: Path) -> None:
    source_text = script_path.read_text(encoding="utf-8")
    if SCRIPT_BOOTSTRAP_MARKER in source_text:
        return

    lines = source_text.splitlines(keepends=True)
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1

    future_import_indexes = [
        index for index, line in enumerate(lines) if line.startswith("from __future__ import ")
    ]
    if future_import_indexes:
        insert_at = max(future_import_indexes) + 1

    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1

    updated_lines = list(lines[:insert_at])
    if updated_lines and not updated_lines[-1].endswith("\n"):
        updated_lines[-1] = updated_lines[-1] + "\n"
    updated_lines.extend([SCRIPT_BOOTSTRAP, "\n\n"])
    updated_lines.extend(lines[insert_at:])
    script_path.write_text("".join(updated_lines), encoding="utf-8")


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


def stage_ai_runtime(layout: FreezeAssetLayout) -> None:
    if not layout.ai_source.exists():
        raise FileNotFoundError(
            f"AI source root not found: {layout.ai_source}\n"
            "Set IMAGE_TRIAGE_AI_SOURCE to the AICullingPipeline path before building."
        )
    _reset_directory(layout.ai_stage_root)

    for relative_dir in (
        Path("app"),
        Path("configs"),
        Path("scripts"),
    ):
        _copy_tree(layout.ai_source / relative_dir, layout.ai_stage_root / relative_dir)

    if INCLUDE_LOCAL_BACKBONE:
        _copy_tree(
            layout.ai_source / "vit_base_patch14_dinov2.lvd142m",
            layout.ai_stage_root / "vit_base_patch14_dinov2.lvd142m",
        )
    else:
        print(
            "Skipping bundled DINOv2 backbone directory; the packaged app will download "
            "the model on demand."
        )

    if INCLUDE_DEFAULT_RANKER:
        for relative_file in (
            Path("outputs/china26_full/ranker_run_mlp_100ep/best_ranker.pt"),
            Path("outputs/china26_full/ranker_run_mlp_100ep/last_ranker.pt"),
        ):
            _copy_file(layout.ai_source / relative_file, layout.ai_stage_root / relative_file)
    else:
        print(
            "Skipping bundled default ranker checkpoint; set IMAGE_TRIAGE_INCLUDE_DEFAULT_RANKER=1 "
            "to include a local checkpoint for private builds."
        )

    scripts_dir = layout.ai_stage_root / "scripts"
    for script_name in STAGE_SCRIPT_NAMES:
        _inject_stage_script_bootstrap(scripts_dir / script_name)


def stage_ai_site_packages(layout: FreezeAssetLayout) -> None:
    if not layout.ai_site_packages_source.exists():
        raise FileNotFoundError(
            f"AI site-packages source not found: {layout.ai_site_packages_source}\n"
            "Set IMAGE_TRIAGE_AI_SITE_PACKAGES to a site-packages directory before building."
        )
    _reset_directory(layout.ai_site_packages_stage_root)

    ordered_entries = list(AI_SITE_PACKAGES_ENTRIES) + [
        entry for entry in AI_SITE_PACKAGES_OPTIONAL_ENTRIES if entry not in AI_SITE_PACKAGES_ENTRIES
    ]
    for entry_name in ordered_entries:
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
