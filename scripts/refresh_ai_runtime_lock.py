"""Regenerate the pinned, hash-verified AI runtime locks.

Open-ended requirements (``insightface>=0.7``, ``numpy>=1.26``, …) let two
machines install different package sets from the same Image Triage build. That
is not a hypothetical: at the time of writing ``insightface>=0.7`` resolves to
2.0 while the runtime this project was validated against holds 1.0.1.

This script asks pip to resolve each variant *without installing*, and writes a
lock in pip's requirements-with-hashes format. The installer then runs with
``--require-hashes``, so an install either matches the locked set exactly or
fails loudly.

Locks are per interpreter tag and variant::

    packaging/ai_runtime_locks/py313-windows-amd64-gpu.txt
    packaging/ai_runtime_locks/py313-windows-amd64-cpu-base.txt

Run on the target platform with the interpreter the build ships against::

    python scripts/refresh_ai_runtime_lock.py            # every combination
    python scripts/refresh_ai_runtime_lock.py --variant cpu --no-torch

Requires network access. A maintenance tool, never imported at runtime.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from image_triage.ai_paths import runtime_tag  # noqa: E402
from image_triage.ai_runtime_packages import (  # noqa: E402
    AI_RUNTIME_VARIANTS,
    LOCK_DIRECTORY,
    AI_RUNTIME_LOCK_EXCLUDED_DISTRIBUTIONS,
    AI_RUNTIME_GPU_VARIANT,
    AI_RUNTIME_PLATFORM_REQUIREMENT_OVERRIDES,
    _ai_runtime_pip_requirements_for_variant,
    _torch_index_url_for_variant,
    runtime_platform,
    torch_supported,
)

# The wheels pip may pick for a platform other than this one (``--target-platform``): the macOS version is the oldest the app runs on
# (PySide6 6.12 needs macOS 14), and pip takes wheels built for older ones too.
CROSS_PLATFORM_TAGS = {
    "darwin-arm64": "macosx_14_0_arm64",
    "darwin-amd64": "macosx_14_0_x86_64",
}


def _constraints(lock: Path, skip: frozenset[str] = frozenset()) -> str:
    """``name==version`` for every distribution in another platform's lock (hashes dropped: they are per wheel, so per platform), but
    those in ``skip``: the ones this platform must take another version of (AI_RUNTIME_PLATFORM_REQUIREMENT_OVERRIDES)."""
    lines = []
    for line in lock.read_text(encoding="utf-8").splitlines():
        line = line.strip().rstrip("\\").strip()
        if "==" in line and not line.startswith(("#", "--")) and line.split("==", 1)[0].strip().lower() not in skip:
            # Without a local version (torch==2.9.0+cpu): a macOS wheel has none, and ==2.9.0 still matches 2.9.0+cpu elsewhere.
            lines.append(line.split("+", 1)[0])
    return "\n".join(lines) + "\n"


def _resolve(variant: str, include_torch: bool, match: Path | None = None, platform: str | None = None) -> list[dict]:
    """Run pip's resolver in report mode and return the resolved distributions. With ``match`` (another platform's lock for the
    same variant), every distribution both platforms install is held to that lock's version. With ``platform`` (another machine's, as
    ``CROSS_PLATFORM_TAGS`` names them), pip resolves for that machine instead of this one."""
    requirements = _ai_runtime_pip_requirements_for_variant(variant, include_torch=include_torch, platform=platform)
    platform_args: list[str] = []
    if platform is not None and platform != runtime_platform():
        platform_args = ["--platform", CROSS_PLATFORM_TAGS[platform], "--python-version", f"{sys.version_info.major}.{sys.version_info.minor}",
                         "--implementation", "cp"]
    with tempfile.TemporaryDirectory() as temp_dir:
        report_path = Path(temp_dir) / "report.json"
        constraint_args: list[str] = []
        if match is not None:
            constraints = Path(temp_dir) / "constraints.txt"
            skip = frozenset(AI_RUNTIME_PLATFORM_REQUIREMENT_OVERRIDES.get(platform or runtime_platform(), {}))
            constraints.write_text(_constraints(match, skip), encoding="utf-8")
            constraint_args = ["--constraint", str(constraints)]
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--dry-run",
            "--quiet",
            "--ignore-installed",
            "--only-binary=:all:",
            "--extra-index-url",
            _torch_index_url_for_variant(variant),
            "--report",
            str(report_path),
            *platform_args,
            *constraint_args,
            *requirements,
        ]
        print(f"  resolving {len(requirements)} requirement(s)...", file=sys.stderr, flush=True)
        process = subprocess.run(command, text=True, capture_output=True)
        if process.returncode != 0:
            raise SystemExit(
                f"pip could not resolve {variant} (torch={include_torch}):\n"
                + (process.stderr or process.stdout)
            )
        payload = json.loads(report_path.read_text(encoding="utf-8"))
    return list(payload.get("install", []))


def _pypi_sha256(name: str, version: str, url: str) -> str:
    """The SHA-256 PyPI publishes for the wheel ``url`` names (its file name), or "" when PyPI has no file of that name."""
    import urllib.request

    filename = url.rsplit("/", 1)[-1].split("#", 1)[0]
    if not filename:
        return ""
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=30) as response:
        release = json.load(response)
    for file in release.get("urls", []):
        if file.get("filename") == filename:
            digest = str((file.get("digests") or {}).get("sha256") or "")
            if digest:
                print(f"  {filename}: no hash on its index, using PyPI's for the same file", file=sys.stderr)
            return digest
    return ""


def _canonical(entry: dict) -> str:
    name = str((entry.get("metadata") or {}).get("name") or "")
    return name.replace("_", "-").strip().lower()


def _render(entries: list[dict], variant: str, include_torch: bool, tag: str | None = None) -> str:
    lines = [
        "# Generated by scripts/refresh_ai_runtime_lock.py -- do not edit by hand.",
        f"# interpreter: {tag or runtime_tag()}",
        f"# variant: {variant}   pytorch: {'yes' if include_torch else 'no'}",
        "#",
        "# Every distribution the managed AI runtime installs, pinned to an exact",
        "# version and wheel hash so two machines cannot resolve differently.",
        "",
    ]
    rows: list[tuple[str, str, str]] = []
    for entry in entries:
        canonical = _canonical(entry)
        if variant == "gpu" and canonical == "onnxruntime":
            # onnxruntime and onnxruntime-gpu install the same package
            # directory; keeping both leaves the provider set decided by
            # whichever wheel was unpacked last. Some dependencies pull the CPU one
            # in transitively, so drop it from the GPU closure.
            print("  dropping transitive onnxruntime from the GPU lock", file=sys.stderr)
            continue
        if canonical in AI_RUNTIME_LOCK_EXCLUDED_DISTRIBUTIONS:
            # The application declares the headless wheel. Some dependencies
            # pull a GUI or contrib wheel too (mediapipe asks for
            # opencv-contrib-python and sounddevice), but those distributions own
            # the same cv2 package tree and cannot safely coexist in one target
            # directory, and nothing the app imports needs them.
            print(f"  dropping transitive {canonical} from the lock", file=sys.stderr)
            continue
        metadata = entry.get("metadata") or {}
        name = str(metadata.get("name") or "").strip()
        version = str(metadata.get("version") or "").strip()
        archive = (entry.get("download_info") or {}).get("archive_info") or {}
        hashes = archive.get("hashes") or {}
        digest = str(hashes.get("sha256") or "")
        if not digest:
            legacy = str(archive.get("hash") or "")
            if legacy.startswith("sha256="):
                digest = legacy.partition("=")[2]
        if not digest and name and version:
            # The PyTorch index mirrors some PyPI wheels (Jinja2, for one) without a hash in its links. The file is the same one PyPI
            # publishes, under the same name: take PyPI's hash for it. The install checks every file against the lock, so a mirror
            # that served a different file would fail there, not be trusted.
            digest = _pypi_sha256(name, version, str((entry.get("download_info") or {}).get("url") or ""))
        if not (name and version and digest):
            raise SystemExit(f"pip reported no usable hash for {name or '<unknown>'} {version}")
        rows.append((name, version, digest))

    for name, version, digest in sorted(rows, key=lambda row: row[0].lower()):
        lines.append(f"{name}=={version} \\")
        lines.append(f"    --hash=sha256:{digest}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", choices=AI_RUNTIME_VARIANTS, action="append")
    parser.add_argument(
        "--no-torch",
        action="store_true",
        help="Only regenerate the compact base locks.",
    )
    parser.add_argument(
        "--torch-only",
        action="store_true",
        help="Only regenerate the full PyTorch locks.",
    )
    parser.add_argument(
        "--match-platform",
        metavar="TAG",
        help="Hold every distribution shared with another platform's locks (e.g. windows-amd64) to the versions they pin, so both "
        "platforms run the set that was validated. Platform-only distributions resolve as usual.",
    )
    parser.add_argument(
        "--target-platform",
        choices=sorted(CROSS_PLATFORM_TAGS),
        help="Write the locks of another machine (resolved here, for its wheels): the Macs, which have no CUDA build and, on Intel, "
        "no PyTorch. Only the locks that machine can use are written.",
    )
    args = parser.parse_args(argv)

    platform = args.target_platform or runtime_platform()
    tag = f"py{sys.version_info.major}{sys.version_info.minor}-{platform}"
    variants = tuple(args.variant) if args.variant else AI_RUNTIME_VARIANTS
    if platform.startswith("darwin-"):
        variants = tuple(variant for variant in variants if variant != AI_RUNTIME_GPU_VARIANT)   # no CUDA on a Mac
    if args.no_torch and args.torch_only:
        parser.error("--no-torch and --torch-only are mutually exclusive")
    if args.no_torch:
        torch_choices = (False,)
    elif args.torch_only:
        torch_choices = (True,)
    else:
        torch_choices = (True, False)
    if not torch_supported(platform):
        torch_choices = tuple(choice for choice in torch_choices if not choice)                # no PyTorch build for this machine

    directory = ROOT / LOCK_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    for variant in variants:
        for include_torch in torch_choices:
            label = f"{variant} ({'pytorch' if include_torch else 'base'})"
            print(f"Resolving {label}...", file=sys.stderr, flush=True)
            suffix = "" if include_torch else "-base"
            target = directory / f"{tag}-{variant}{suffix}.txt"
            match = None
            if args.match_platform:
                match = directory / target.name.replace(tag, f"{tag.split('-')[0]}-{args.match_platform}", 1)
                if not match.is_file():
                    raise SystemExit(f"--match-platform: there is no {match.name} to match")
                print(f"  matching the versions in {match.name}", file=sys.stderr)
            entries = _resolve(variant, include_torch, match, platform)
            target.write_text(_render(entries, variant, include_torch, tag), encoding="utf-8")
            print(f"  wrote {target.relative_to(ROOT)} ({len(entries)} distributions)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
