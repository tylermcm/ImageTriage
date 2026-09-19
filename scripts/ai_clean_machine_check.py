"""Validate an *installed* Image Triage against the clean-machine matrix.

Run this on a target machine after installing the MSI. It drives the frozen
executables in the install directory and never imports the source checkout, so
a pass here means the shipped artefacts work — which is the release gate the
handoff requires, and the thing a source-tree test run cannot prove.

**This script itself needs Python.** The product does not: the release gate is
the in-app *Check AI Readiness (Demo Ready)* action plus the workflow pass in
``docs/ai_clean_machine_matrix.md``, both of which run with no Python on the
machine. Use this harness to capture a machine-readable record when a developer
Python happens to be available, and to drive conditions that are tedious by
hand.

What it exercises:

* both installed runtime profiles, not only the preferred one, so a CPU matrix
  row cannot silently probe the GPU profile;
* the frozen ``ai_python_runner`` executable, by running a real worker script
  through it;
* every capability probe at ``--level full``, which loads model weights and
  runs a forward pass.

Usage::

    python scripts/ai_clean_machine_check.py --install-dir "C:\\Program Files\\ImageTriage"
    python scripts/ai_clean_machine_check.py --install-dir <dir> --json report.json
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path


# Kept in step with image_triage.ai_manifest.DEFAULT_CAPABILITY_ORDER. Declared
# literally so this script has no dependency on the source tree.
CAPABILITIES = (
    "culling",
    "text_scoring",
    "quality_topiq",
    "faces",
    "semantic_search",
    "scene_masks",
    "subject_masks",
    "sam_masks",
    "depth",
)

INFERENCE_CAPABILITIES = frozenset(CAPABILITIES) - {"culling"}

PROBE_TIMEOUT_SECONDS = 300
VALIDATION_MODULES = (
    "numpy",
    "onnx",
    "onnxruntime",
    "cv2",
    "sklearn",
    "PIL",
    "yaml",
    "tqdm",
    "insightface",
    "torchvision",
    "timm",
    "safetensors",
    "tokenizers",
    "einops",
    "kornia",
)
TRANSFORMERS_VALIDATION_SYMBOLS = (
    "AutoImageProcessor",
    "AutoModelForDepthEstimation",
    "AutoModelForImageSegmentation",
    "OneFormerForUniversalSegmentation",
    "OneFormerProcessor",
    "Sam2Model",
    "Sam2Processor",
)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    data: dict[str, object] = field(default_factory=dict)


def _installer_path(install_dir: Path) -> Path:
    name = "ai_runtime_installer.exe" if os.name == "nt" else "ai_runtime_installer"
    return install_dir / name


def _runner_path(install_dir: Path) -> Path:
    name = "ai_python_runner.exe" if os.name == "nt" else "ai_python_runner"
    return install_dir / name


def _run(command: list[str], *, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def check_install_layout(install_dir: Path) -> list[CheckResult]:
    """The shipped executables exist and the source checkout is not in play."""
    results: list[CheckResult] = []
    for name in ("ai_runtime_installer", "ai_python_runner"):
        binary = install_dir / (f"{name}.exe" if os.name == "nt" else name)
        results.append(
            CheckResult(
                name=f"executable present: {binary.name}",
                ok=binary.is_file(),
                detail=str(binary),
            )
        )
    main_app = install_dir / ("ImageTriage.exe" if os.name == "nt" else "ImageTriage")
    results.append(
        CheckResult(name="application executable present", ok=main_app.is_file(), detail=str(main_app))
    )
    source_marker = install_dir / "pyproject.toml"
    results.append(
        CheckResult(
            name="install directory is not a source checkout",
            ok=not source_marker.exists(),
            detail="pyproject.toml must not ship in the installed layout",
        )
    )
    leaked = os.environ.get("PYTHONPATH", "")
    results.append(
        CheckResult(
            name="no PYTHONPATH leaking from the environment",
            ok=not leaked,
            detail=leaked or "unset",
        )
    )
    return results


def check_runtime_status(install_dir: Path) -> tuple[CheckResult, dict[str, object]]:
    installer = _installer_path(install_dir)
    try:
        process = _run([str(installer), "status", "--json"], cwd=install_dir)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name="managed runtime status", ok=False, detail=str(exc)), {}
    if process.returncode != 0:
        return (
            CheckResult(
                name="managed runtime status",
                ok=False,
                detail=(process.stderr or process.stdout).strip()[:400],
            ),
            {},
        )
    try:
        payload = json.loads(process.stdout)
    except json.JSONDecodeError:
        return (
            CheckResult(name="managed runtime status", ok=False, detail="status output was not JSON"),
            {},
        )
    installed = payload.get("installed_variants") or []
    return (
        CheckResult(
            name="managed runtime status",
            ok=bool(installed),
            detail=f"root={payload.get('root')} installed={', '.join(installed) or 'none'}",
            data=payload,
        ),
        payload,
    )


def check_installer_stdlib_bootstrap(install_dir: Path) -> CheckResult:
    """Prove the frozen validator can import modules from its bundled stdlib."""
    installer = _installer_path(install_dir)
    if not installer.is_file():
        return CheckResult(name="installer stdlib bootstrap", ok=False, detail="executable missing")
    try:
        with tempfile.TemporaryDirectory(prefix="image-triage-validator-check-") as temp_dir:
            site_packages = Path(temp_dir)
            for module_name in VALIDATION_MODULES:
                body = "import unittest\n__version__ = '1.0'\n" if module_name == "numpy" else "__version__ = '1.0'\n"
                (site_packages / f"{module_name}.py").write_text(body, encoding="utf-8")
            (site_packages / "torch.py").write_text(
                "__version__ = '2.9.0+cu128'\n",
                encoding="utf-8",
            )
            transformers_body = "\n".join(
                f"{symbol} = object()" for symbol in TRANSFORMERS_VALIDATION_SYMBOLS
            )
            (site_packages / "transformers.py").write_text(
                transformers_body + "\n",
                encoding="utf-8",
            )
            process = _run(
                [
                    str(installer),
                    "validate-profile",
                    "--site-packages",
                    str(site_packages),
                    "--variant",
                    "gpu",
                ],
                cwd=install_dir,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(name="installer stdlib bootstrap", ok=False, detail=str(exc))

    output = (process.stderr or process.stdout).strip()
    return CheckResult(
        name="installer stdlib bootstrap",
        ok=process.returncode == 0,
        detail=output[-400:] or f"exit code {process.returncode}",
    )


def check_root_is_short_and_unvirtualized(root: str) -> CheckResult:
    """The managed root must not sit inside Store Python's package cache."""
    lowered = str(root).replace("/", "\\").lower()
    virtualized = "\\packages\\pythonsoftwarefoundation.python." in lowered
    return CheckResult(
        name="managed root avoids Store Python virtualization",
        ok=not virtualized and len(str(root)) < 120,
        detail=f"{root} ({len(str(root))} chars)",
    )


def probe_capability(
    install_dir: Path,
    capability: str,
    site_packages: str,
    device: str,
    *,
    level: str = "full",
    label: str = "",
) -> CheckResult:
    installer = _installer_path(install_dir)
    command = [
        str(installer),
        "probe",
        "--capability",
        capability,
        "--site-packages",
        site_packages,
        "--device",
        device,
        "--level",
        level,
    ]
    try:
        process = _run(command, cwd=install_dir, timeout=PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return CheckResult(
            name=f"capability: {capability}{label}",
            ok=False,
            detail=f"probe timed out after {PROBE_TIMEOUT_SECONDS}s",
        )
    except OSError as exc:
        return CheckResult(name=f"capability: {capability}{label}", ok=False, detail=str(exc))

    payload: dict[str, object] = {}
    for line in reversed(process.stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                candidate = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict) and "capability" in candidate:
                payload = candidate
                break
    if not payload:
        return CheckResult(
            name=f"capability: {capability}{label}",
            ok=False,
            detail=(process.stderr or process.stdout).strip()[-300:] or "no probe result",
        )
    ok = bool(payload.get("ok"))
    if ok and capability in INFERENCE_CAPABILITIES and not payload.get("inference_ran"):
        # A probe that never ran the model proves nothing about the capability.
        return CheckResult(
            name=f"capability: {capability}{label}",
            ok=False,
            detail="probe reported success without running an inference",
            data=payload,
        )
    if ok:
        providers = payload.get("providers_active") or payload.get("providers_available") or []
        detail = (
            f"device={payload.get('selected_device')} "
            f"providers={', '.join(providers) or '-'} "
            f"level={payload.get('probe_level')}"
        )
    else:
        detail = f"[{payload.get('stage')}/{payload.get('category')}] {payload.get('message')}"
    return CheckResult(name=f"capability: {capability}{label}", ok=ok, detail=detail, data=payload)


def check_runner_executes(install_dir: Path, site_packages: str, device: str) -> CheckResult:
    """Run a real script through the frozen ai_python_runner.

    Nothing else in this harness touches that executable, yet every AI worker
    the product launches goes through it. Checking only that the file exists
    would leave its DLL and sys.path handling unproven.
    """
    runner = _runner_path(install_dir)
    if not runner.is_file():
        return CheckResult(name="ai_python_runner executes", ok=False, detail="executable missing")
    environment = dict(os.environ)
    environment["IMAGE_TRIAGE_AI_SELECTED_DEVICE"] = device
    environment["IMAGE_TRIAGE_AI_PROFILE"] = "clean-machine-check"
    try:
        with tempfile.TemporaryDirectory(prefix="image-triage-ai-check-") as temp_dir:
            script = Path(temp_dir) / "runner_probe.py"
            script.write_text(
                "import json, sys\n"
                "import numpy, onnxruntime\n"
                "print(json.dumps({\n"
                "    'numpy': numpy.__file__,\n"
                "    'onnxruntime': onnxruntime.__file__,\n"
                "    'providers': onnxruntime.get_available_providers(),\n"
                "}))\n",
                encoding="utf-8",
            )
            process = subprocess.run(
                [str(runner), str(script)],
                cwd=str(install_dir),
                capture_output=True,
                text=True,
                timeout=180,
                env=environment,
            )
    except OSError as exc:
        return CheckResult(name="ai_python_runner executes", ok=False, detail=str(exc))
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="ai_python_runner executes", ok=False, detail="runner timed out after 180s"
        )

    payload: dict[str, object] = {}
    for line in reversed(process.stdout.splitlines()):
        stripped = line.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            break
    if not payload:
        return CheckResult(
            name="ai_python_runner executes",
            ok=False,
            detail=(process.stderr or process.stdout).strip()[-300:] or "no output",
        )
    loaded = str(payload.get("numpy") or "")
    if site_packages and not loaded.lower().startswith(site_packages.lower()):
        return CheckResult(
            name="ai_python_runner executes",
            ok=False,
            detail=f"numpy loaded from {loaded}, not the managed profile",
            data=payload,
        )
    return CheckResult(
        name="ai_python_runner executes",
        ok=True,
        detail=f"providers={', '.join(payload.get('providers') or []) or '-'}",
        data=payload,
    )


def _installed_profiles(status_payload: dict[str, object], root: str) -> dict[str, str]:
    """Every installed variant mapped to its exact site-packages directory."""
    profiles: dict[str, str] = {}
    if not root:
        return profiles
    metadata_path = Path(root) / "runtime_installation.json"
    generations: dict[str, str] = {}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        raw = metadata.get("profile_generations")
        if isinstance(raw, dict):
            generations = {str(k): str(v) for k, v in raw.items()}
    except (OSError, json.JSONDecodeError, AttributeError):
        generations = {}
    installed = status_payload.get("installed_variants") or []
    for variant in installed:
        name = str(variant)
        generation = generations.get(name, name)
        candidate = Path(root) / "profiles" / generation / "site-packages"
        if candidate.is_dir():
            profiles[name] = str(candidate)
    return profiles


def machine_profile() -> dict[str, object]:
    root = Path(os.environ.get("SystemDrive", "C:") + os.sep)
    try:
        usage = shutil.disk_usage(root)
        free_gb = round(usage.free / 1e9, 1)
    except OSError:
        free_gb = -1.0
    return {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "os": platform.platform(),
        "architecture": platform.machine(),
        "python_on_path": shutil.which("python") or shutil.which("python3") or "none",
        "system_drive_free_gb": free_gb,
        "username_has_space": " " in (os.environ.get("USERNAME", "") or ""),
        "username_is_ascii": (os.environ.get("USERNAME", "") or "").isascii(),
        "userprofile_on_onedrive": "onedrive" in (os.environ.get("USERPROFILE", "") or "").lower(),
    }


def render_markdown(profile: dict[str, object], results: list[CheckResult]) -> str:
    lines = [
        "# Image Triage clean-machine AI check",
        "",
        f"- Ran: {profile['timestamp']}",
        f"- OS: {profile['os']} ({profile['architecture']})",
        f"- Python on PATH: {profile['python_on_path']}",
        f"- Free space: {profile['system_drive_free_gb']} GB",
        "",
        "| Check | Result | Detail |",
        "| --- | --- | --- |",
    ]
    for result in results:
        mark = "PASS" if result.ok else "FAIL"
        detail = result.detail.replace("|", "\\|")
        lines.append(f"| {result.name} | {mark} | {detail} |")
    failures = [item for item in results if not item.ok]
    lines.extend(
        [
            "",
            f"**{len(results) - len(failures)} of {len(results)} checks passed.**",
        ]
    )
    if failures:
        lines.append("")
        lines.append("## Failures")
        for item in failures:
            lines.append(f"- **{item.name}** — {item.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--install-dir",
        type=Path,
        required=True,
        help="Directory the MSI installed into (containing ImageTriage.exe).",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cpu", "cuda"),
        help="Device to request. Run once with cpu and once with cuda on GPU machines.",
    )
    parser.add_argument(
        "--capabilities",
        nargs="*",
        default=list(CAPABILITIES),
        help="Subset of capabilities to probe.",
    )
    parser.add_argument(
        "--level",
        choices=("quick", "full"),
        default="full",
        help="full (default) loads model weights and runs a forward pass.",
    )
    parser.add_argument("--json", type=Path, help="Write the full report here.")
    parser.add_argument("--markdown", type=Path, help="Write the Markdown table here.")
    args = parser.parse_args(argv)

    install_dir = args.install_dir.expanduser().resolve()
    if not install_dir.is_dir():
        print(f"Install directory not found: {install_dir}", file=sys.stderr)
        return 2

    profile = machine_profile()
    results = check_install_layout(install_dir)
    results.append(check_installer_stdlib_bootstrap(install_dir))
    status_result, status_payload = check_runtime_status(install_dir)
    results.append(status_result)

    root = str(status_payload.get("root") or "")
    if root:
        results.append(check_root_is_short_and_unvirtualized(root))

    profiles = _installed_profiles(status_payload, root)
    if not profiles:
        results.append(
            CheckResult(
                name="managed site-packages located",
                ok=False,
                detail="could not determine any runtime profile; run Set Up AI first",
            )
        )
    else:
        for variant, site_packages in sorted(profiles.items()):
            # Probe every installed profile explicitly. Testing only the
            # preferred one lets a CPU matrix row silently exercise the GPU
            # profile and report a pass that says nothing about CPU-only
            # machines.
            device = "cpu" if variant == "cpu" else "cuda"
            if args.device != "auto":
                if args.device != device:
                    continue
            suffix = f" [{variant}]"
            results.append(check_runner_executes(install_dir, site_packages, device))
            for capability in args.capabilities:
                results.append(
                    probe_capability(
                        install_dir,
                        capability,
                        site_packages,
                        device,
                        level=args.level,
                        label=suffix,
                    )
                )

    markdown = render_markdown(profile, results)
    print(markdown)

    if args.markdown:
        args.markdown.write_text(markdown, encoding="utf-8")
    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "machine": profile,
                    "install_dir": str(install_dir),
                    "requested_device": args.device,
                    "checks": [
                        {"name": r.name, "ok": r.ok, "detail": r.detail, "data": r.data}
                        for r in results
                    ],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    return 0 if all(item.ok for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
