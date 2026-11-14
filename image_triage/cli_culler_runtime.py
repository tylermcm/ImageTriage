"""Explicit native runtime selection; the host interpreter is never a fallback."""
from __future__ import annotations

import json
import copy
import os
from pathlib import Path
import subprocess

from .ai_paths import managed_ai_root


def _no_window() -> int:
    """On Windows a console program started from the app opens a console window unless it is told not to."""
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0)


def load_native_runtime() -> dict:
    location = Path(os.environ.get("IMAGE_TRIAGE_CLI_CULLER_RUNTIME") or
                    managed_ai_root() / "rt" / "cli-culler" / "runtime.json")
    manifest = json.loads(location.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("schema") != "image-triage-culler-runtime/1":
        raise ValueError("Unsupported native culler runtime manifest")
    for key in ("python", "worker", "app_path"):
        path = Path(manifest[key])
        if not path.is_absolute():
            path = location.resolve().parent / path
        if not path.exists():
            raise ValueError(f"Native runtime is missing {key}")
        manifest[key] = str(path)
    return manifest


def native_settings(settings, manifest: dict) -> dict:
    return native_settings_from_text(settings.value("ai/cli_culler_settings_json", "", type=str), manifest)


def native_settings_from_text(text: str, manifest: dict) -> dict:
    """The profile to run with, from the saved JSON text (read from the settings on the UI thread; this part needs no settings object)."""
    selected = json.loads(text) if text else copy.deepcopy(manifest["default_settings"])
    if not isinstance(selected, dict):
        raise ValueError("Native culler settings must be a JSON object")
    # Existing installed manifests predate the app's search-on default. Saved
    # profiles, including an explicit empty model list, remain authoritative.
    if not text:
        models = selected.get("models", [])
        if isinstance(models, str):
            models = [item.strip() for item in models.split(",") if item.strip()]
        if "all" not in models and "search" not in models:
            selected["models"] = [*models, "search"]
    return selected


def validate_native_settings(python: str, settings: dict):
    result = subprocess.run(
        [python, "-I", "-c",
         "import json,sys; from aiculler import CullSettings; CullSettings.from_dict(json.loads(sys.argv[1]))",
         json.dumps(settings)], capture_output=True, text=True, timeout=30, creationflags=_no_window())
    if result.returncode:
        raise ValueError(result.stderr.strip())


def verify_native_runtime(manifest: dict) -> None:
    """Verify installed wheel identities before a task starts the engine."""
    expected = {"ai-culler": "f2e34b1b57f889f59b336619887d71d4bb701118",
                "bracketeering": "a494be9a92f85bed7f987359139d12a5a83f718e"}
    for name, revision in expected.items():
        if manifest["pins"][name]["commit"] != revision:
            raise ValueError(f"Unvalidated native runtime revision for {name}")
    code = """
import json
from importlib.metadata import distribution
import aiculler, bracketeering
print(json.dumps({name: {'url': json.loads(distribution(name).read_text('direct_url.json')),
                         'version': distribution(name).version}
                  for name in ('ai-culler', 'bracketeering')}))
"""
    probe = subprocess.run([manifest["python"], "-I", "-c", code],
                           capture_output=True, text=True, timeout=30, creationflags=_no_window())
    if probe.returncode:
        raise ValueError(f"Native runtime probe failed: {probe.stderr}")
    installed = json.loads(probe.stdout)
    for name, entry in installed.items():
        hashes = entry["url"].get("archive_info", {}).get("hashes", {})
        if hashes.get("sha256") != manifest["pins"][name]["sha256"]:
            raise ValueError(f"Installed {name} is not the validated wheel")
    if installed["ai-culler"]["version"] != "0.3.0.dev1":
        raise ValueError("Unsupported native engine version")


NATIVE_CUDA_CHECK_SECONDS = 120


def verify_native_cuda(python: str) -> None:
    """Exercise CUDA in the native engine's own interpreter, not the cascade runtime."""
    code = """
import torch
if not torch.cuda.is_available():
    raise RuntimeError('CUDA is unavailable in the native culler runtime')
value = torch.ones((2, 2), device='cuda')
assert (value @ value).sum().item() == 8
torch.cuda.synchronize()
"""
    try:
        # A cold start loads PyTorch and initialises the card's driver; that alone can take most of a minute.
        result = subprocess.run([python, "-I", "-c", code], capture_output=True, text=True,
                                timeout=NATIVE_CUDA_CHECK_SECONDS, creationflags=_no_window())
    except subprocess.TimeoutExpired:
        raise ValueError(f"The native culler did not answer a graphics card check within {NATIVE_CUDA_CHECK_SECONDS} seconds. "
                         "Try again, repair its GPU runtime or explicitly select CPU.") from None
    if result.returncode:
        raise ValueError("The native culler could not use the requested graphics card. "
                         "Repair its GPU runtime or explicitly select CPU. " + result.stderr.strip())
