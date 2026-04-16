from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

from cx_Freeze import Executable, setup


ROOT = Path(__file__).resolve().parent
APP_ICON_PATH = ROOT / "build_assets" / "icons" / "image_triage.ico"


def _discover_ai_source_root() -> Path:
    candidates = [
        ROOT / "AICullingPipeline",
        Path(r"C:\Users\user0\Documents\GitHub\AICullingPipeline"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


AI_SOURCE = Path(
    os.environ.get("IMAGE_TRIAGE_AI_SOURCE", str(_discover_ai_source_root()))
).expanduser().resolve()
AI_STAGE_ROOT = ROOT / "build_assets" / "ai_runtime" / "AICullingPipeline"
DEFAULT_AI_SITE_PACKAGES_SOURCE = ROOT / ".msi_build_venv" / "Lib" / "site-packages"
AI_SITE_PACKAGES_SOURCE = Path(
    os.environ.get("IMAGE_TRIAGE_AI_SITE_PACKAGES", str(DEFAULT_AI_SITE_PACKAGES_SOURCE))
).expanduser().resolve()
AI_SITE_PACKAGES_STAGE_ROOT = ROOT / "build_assets" / "ai_site_packages"
DEFAULT_AI_STDLIB_SOURCE = Path(os.__file__).resolve().parent
AI_STDLIB_SOURCE = Path(
    os.environ.get("IMAGE_TRIAGE_AI_STDLIB", str(DEFAULT_AI_STDLIB_SOURCE))
).expanduser().resolve()
AI_STDLIB_STAGE_ROOT = ROOT / "build_assets" / "ai_stdlib"
DEFAULT_AI_DLLS_SOURCE = DEFAULT_AI_STDLIB_SOURCE.parent / "DLLs"
AI_DLLS_SOURCE = Path(
    os.environ.get("IMAGE_TRIAGE_AI_DLLS", str(DEFAULT_AI_DLLS_SOURCE))
).expanduser().resolve()
AI_DLLS_STAGE_ROOT = ROOT / "build_assets" / "ai_python_dlls"
STAGE_SCRIPT_NAMES = (
    "extract_embeddings.py",
    "cluster_embeddings.py",
    "export_ranked_report.py",
)


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


def _read_project_version() -> str:
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


def stage_ai_runtime() -> None:
    if not AI_SOURCE.exists():
        raise FileNotFoundError(
            f"AI source root not found: {AI_SOURCE}\n"
            "Set IMAGE_TRIAGE_AI_SOURCE to the AICullingPipeline path before building."
        )
    _reset_directory(AI_STAGE_ROOT)

    for relative_dir in (
        Path("app"),
        Path("configs"),
        Path("scripts"),
    ):
        _copy_tree(AI_SOURCE / relative_dir, AI_STAGE_ROOT / relative_dir)

    if INCLUDE_LOCAL_BACKBONE:
        _copy_tree(
            AI_SOURCE / "vit_base_patch14_dinov2.lvd142m",
            AI_STAGE_ROOT / "vit_base_patch14_dinov2.lvd142m",
        )
    else:
        print(
            "Skipping bundled DINOv2 backbone directory; the packaged app will download "
            "the timm model on demand."
        )

    if INCLUDE_DEFAULT_RANKER:
        for relative_file in (
            Path("outputs/china26_full/ranker_run_mlp_100ep/best_ranker.pt"),
            Path("outputs/china26_full/ranker_run_mlp_100ep/last_ranker.pt"),
        ):
            _copy_file(AI_SOURCE / relative_file, AI_STAGE_ROOT / relative_file)
    else:
        print(
            "Skipping bundled default ranker checkpoint; set IMAGE_TRIAGE_INCLUDE_DEFAULT_RANKER=1 "
            "to include a local checkpoint for private builds."
        )

    scripts_dir = AI_STAGE_ROOT / "scripts"
    for script_name in STAGE_SCRIPT_NAMES:
        _inject_stage_script_bootstrap(scripts_dir / script_name)


def stage_ai_site_packages() -> None:
    if not AI_SITE_PACKAGES_SOURCE.exists():
        raise FileNotFoundError(
            f"AI site-packages source not found: {AI_SITE_PACKAGES_SOURCE}\n"
            "Set IMAGE_TRIAGE_AI_SITE_PACKAGES to a site-packages directory before building."
        )
    _reset_directory(AI_SITE_PACKAGES_STAGE_ROOT)

    ordered_entries = list(AI_SITE_PACKAGES_ENTRIES) + [
        entry for entry in AI_SITE_PACKAGES_OPTIONAL_ENTRIES if entry not in AI_SITE_PACKAGES_ENTRIES
    ]
    for entry_name in ordered_entries:
        source = AI_SITE_PACKAGES_SOURCE / entry_name
        target = AI_SITE_PACKAGES_STAGE_ROOT / entry_name
        if source.is_dir():
            _copy_tree(source, target)
        elif source.is_file():
            _copy_file(source, target)
        else:
            print(f"Skipping missing optional AI dependency entry: {source}")

    if os.name == "nt":
        _patch_sklearn_distributor_init(AI_SITE_PACKAGES_STAGE_ROOT)


def stage_ai_stdlib() -> None:
    if not AI_STDLIB_SOURCE.exists():
        raise FileNotFoundError(
            f"AI stdlib source not found: {AI_STDLIB_SOURCE}\n"
            "Set IMAGE_TRIAGE_AI_STDLIB to a Python Lib directory before building."
        )
    _reset_directory(AI_STDLIB_STAGE_ROOT)
    shutil.copytree(
        AI_STDLIB_SOURCE,
        AI_STDLIB_STAGE_ROOT,
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


def stage_ai_python_dlls() -> None:
    if not AI_DLLS_SOURCE.exists():
        raise FileNotFoundError(
            f"AI Python DLLs source not found: {AI_DLLS_SOURCE}\n"
            "Set IMAGE_TRIAGE_AI_DLLS to a Python DLLs directory before building."
        )
    _reset_directory(AI_DLLS_STAGE_ROOT)
    shutil.copytree(
        AI_DLLS_SOURCE,
        AI_DLLS_STAGE_ROOT,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


stage_ai_runtime()
stage_ai_site_packages()
stage_ai_stdlib()
stage_ai_python_dlls()

build_exe_options = {
    "include_msvcr": True,
    "includes": [
        "uuid",
    ],
    "include_files": [
        (str(AI_STAGE_ROOT.parent), "ai_runtime"),
        (str(AI_SITE_PACKAGES_STAGE_ROOT), "ai_site_packages"),
        (str(AI_STDLIB_STAGE_ROOT), "ai_stdlib"),
        (str(AI_DLLS_STAGE_ROOT), "lib"),
    ],
    "excludes": [
        "tkinter",
        "test",
        "tests",
        "unittest",
        "benchmarks",
        "pip",
        "setuptools",
        "wheel",
        *AI_FREEZE_EXCLUDES,
    ],
}

bdist_msi_options = {
    "add_to_path": False,
    "all_users": True,
    "initial_target_dir": r"[ProgramFiles64Folder]\ImageTriage",
    "install_icon": str(APP_ICON_PATH),
    "launch_on_finish": True,
    "upgrade_code": "{D2ED08E1-991F-42CE-94A3-E95CD4D624AB}",
}

executables = [
    Executable(
        script="image_triage/main.py",
        base="gui",
        target_name="ImageTriage.exe",
        icon=str(APP_ICON_PATH),
    ),
    Executable(
        script="packaging/ai_python_runner.py",
        base=None,
        target_name="ai_python_runner.exe",
        icon=str(APP_ICON_PATH),
    ),
]

if __name__ == "__main__":
    setup(
        name="ImageTriage",
        version=_read_project_version(),
        description="Image Triage desktop installer",
        options={
            "build_exe": build_exe_options,
            "bdist_msi": bdist_msi_options,
        },
        executables=executables,
    )
