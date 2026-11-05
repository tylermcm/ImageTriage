"""Resolve and install a core worker runtime from pinned wheels, never in the host."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from freeze_support import worker_import_closure  # noqa: E402


def prepare(wheelhouse: Path, output: Path, models: bool = False, portable: bool = False, runtime_python: Path | None = None, backend: str = "cpu"):
    if backend not in ("cpu", "cuda"):
        raise ValueError("backend must be cpu or cuda")
    if backend == "cuda" and not models:
        raise ValueError("CUDA packaging requires --models")
    output.mkdir(parents=True, exist_ok=True)
    provenance = json.loads((wheelhouse / "provenance.json").read_text())
    for entry in provenance.values():
        wheel = wheelhouse / entry["wheel"]
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError(f"Wheel digest mismatch: {wheel.name}")
    requirements = output / "core.in"
    overrides = output / "overrides.in"
    requirements.write_text(("ai-culler[models] @ " if models else "ai-culler @ ") +
                           (wheelhouse / provenance["ai-culler"]["wheel"]).as_uri() + "\n")
    overrides.write_text("bracketeering @ " + (wheelhouse / provenance["bracketeering"]["wheel"]).as_uri() + "\n")
    lock = output / "core.lock"
    extra = []
    if models:
        extra = ["--constraint", str(ROOT / f"packaging/cli_culler_models_{backend}.in"),
                 "--extra-index-url", "https://download.pytorch.org/whl/" + ("cu128" if backend == "cuda" else "cpu"),
                 "--index-strategy", "unsafe-best-match", "--emit-index-url"]
    subprocess.run(["uv", "--system-certs", "pip", "compile", str(requirements),
                    "--python", str(runtime_python if portable else sys.executable),
                    "--overrides", str(overrides), "--generate-hashes", "-o", str(lock), *extra], check=True)
    environment = output / "python"
    python = environment / ("python.exe" if portable and sys.platform == "win32" else
                            "Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        if portable:
            if runtime_python is None:
                raise ValueError("--portable requires --runtime-python pointing to a clean standalone Python")
            prefix = Path(subprocess.check_output(
                [str(runtime_python), "-I", "-c", "import sys; print(sys.base_prefix)"], text=True).strip())
            # A fresh standalone base has no host app or legacy aiculler installed.
            subprocess.run([str(runtime_python), "-I", "-c",
                            "import importlib.util; assert all(importlib.util.find_spec(name) is None for name in ('aiculler','image_triage'))"], check=True)
            shutil.copytree(prefix, environment, symlinks=False)
        else:
            subprocess.run(["uv", "--system-certs", "venv", "--python", sys.executable, "--seed", str(environment)], check=True)
    if portable:
        # Only the private copied interpreter is modified, never uv's base.
        subprocess.run(["uv", "--system-certs", "pip", "install", "--python", str(python),
                        "--break-system-packages", "pip"], check=True)
    with tempfile.TemporaryDirectory(prefix="culler-resolved-wheels-") as directory:
        resolved = Path(directory)
        subprocess.run([str(python), "-m", "pip", "download", "--require-hashes", "--no-deps",
                        "--only-binary=:all:", "-r", str(lock), "-d", str(resolved)], check=True)
        subprocess.run([str(python), "-m", "pip", "install", "--no-index", "--no-deps",
                        *(["--break-system-packages"] if portable else []),
                        *map(str, sorted(resolved.glob("*.whl")))], check=True)
    subprocess.run([str(python), "-m", "pip", "check"], check=True)
    closure = {}
    for start in ("image_triage.cli_culler_worker", "image_triage.cli_culler_process", "image_triage.ai_manifest",
                  "image_triage.ai_model_store", "image_triage.ai_paths"):
        closure.update(worker_import_closure(start=start, root=ROOT))
    worker_root = output / "worker"
    for source in closure.values():
        target = worker_root / source.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    manifest = {"schema": "image-triage-culler-runtime/1",
                "pins": provenance, "python": python.relative_to(output).as_posix() if portable else str(python),
                "worker": "worker/image_triage/cli_culler_worker.py" if portable else str(worker_root / "image_triage/cli_culler_worker.py"),
                "app_path": "worker" if portable else str(worker_root),
                "default_settings": json.loads((ROOT / "packaging/cli_culler_default_settings.json").read_text()),
                "model_runtime": models, "backend": backend,
                "namespace": hashlib.sha256(lock.read_bytes()).hexdigest()[:24]}
    (output / "runtime.json").write_text(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--models", action="store_true", help="Include the CPU model runtime; no weights are downloaded")
    parser.add_argument("--backend", choices=("cpu", "cuda"), default="cpu", help="Model-library backend; CUDA accelerates PyTorch rankers, with the engine's plain onnxruntime retained")
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--runtime-python", type=Path, help="Fresh standalone interpreter base to copy into the portable runtime")
    args = parser.parse_args()
    prepare(args.wheelhouse.resolve(), args.out_dir.resolve(), models=args.models,
            portable=args.portable, runtime_python=args.runtime_python, backend=args.backend)
