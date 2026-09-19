from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_TEXT = str(ROOT)
if ROOT_TEXT not in sys.path:
    sys.path.insert(0, ROOT_TEXT)

from image_triage.frozen_bootstrap import configure_frozen_stdlib  # noqa: E402


configure_frozen_stdlib()

from image_triage.ai_runtime_packages import (  # noqa: E402
    AI_RUNTIME_INSTALL_CHOICES,
    install_ai_runtime,
    load_ai_runtime_installation_status,
    validate_ai_runtime_imports,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Install Image Triage AI runtime packages.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser("install", help="Install AI runtime packages")
    install_parser.add_argument(
        "--variant",
        choices=AI_RUNTIME_INSTALL_CHOICES,
        default="gpu",
        help="Which PyTorch runtime profile to install.",
    )
    install_parser.add_argument(
        "--force",
        action="store_true",
        help="Reinstall the selected runtime profile even if it already exists.",
    )
    install_parser.add_argument(
        "--no-dino",
        action="store_true",
        help="Skip optional DINO/PyTorch/transformers dependencies.",
    )
    install_parser.add_argument(
        "--install-root",
        type=Path,
        help="Exact managed runtime root selected by the parent application.",
    )

    status_parser = subparsers.add_parser("status", help="Print current AI runtime installation status")
    status_parser.add_argument("--json", action="store_true", help="Emit status as JSON")
    validate_parser = subparsers.add_parser(
        "validate-profile",
        help=argparse.SUPPRESS,
    )
    validate_parser.add_argument("--site-packages", type=Path, required=True)
    validate_parser.add_argument("--variant", choices=("cpu", "gpu"), required=True)
    validate_parser.add_argument("--no-dino", action="store_true")

    # Runs one capability probe inside a managed profile and prints a single
    # JSON line. Launched by image_triage.ai_health so that the probe uses the
    # exact frozen executable the installed application ships.
    probe_parser = subparsers.add_parser("probe", help=argparse.SUPPRESS)
    probe_parser.add_argument("--capability", required=True)
    probe_parser.add_argument("--site-packages", type=Path, required=True)
    probe_parser.add_argument("--device", default="auto")
    probe_parser.add_argument("--profile", default="")
    probe_parser.add_argument(
        "--level",
        choices=("quick", "full"),
        default="quick",
        help="quick: config-level checks. full: load model weights and run a forward pass.",
    )
    probe_parser.add_argument(
        "--model-dir",
        action="append",
        default=[],
        metavar="BUNDLE=PATH",
        help="Installed directory for one model bundle this capability needs.",
    )
    return parser


def _run_capability_probe(args: argparse.Namespace) -> int:
    from image_triage.ai_manifest import CAPABILITIES
    from image_triage.ai_probe import ProbeResult, run_probe

    capability = CAPABILITIES.get(args.capability)
    if capability is None:
        print(
            ProbeResult(
                capability=args.capability,
                category="manifest",
                message=f"Unknown AI capability {args.capability!r}.",
            ).to_json()
        )
        return 1

    model_dirs: dict[str, Path] = {}
    for entry in args.model_dir:
        bundle_key, _, path_text = str(entry).partition("=")
        if bundle_key and path_text:
            model_dirs[bundle_key] = Path(path_text)
    # Fall back to the managed location for any bundle the caller did not name,
    # so the probe is usable standalone (the clean-machine harness does this).
    if any(key not in model_dirs for key in capability.model_bundles):
        from image_triage.ai_model_store import bundle_install_dir

        for bundle_key in capability.model_bundles:
            model_dirs.setdefault(bundle_key, bundle_install_dir(bundle_key))

    model_path: Path | None = None
    model_dir: Path | None = None
    detail = capability.probe_detail or {}
    if capability.probe_kind == "onnx_model":
        bundle_key = detail.get("bundle", "")
        filename = detail.get("file", "")
        base = model_dirs.get(bundle_key)
        if base is not None and filename:
            model_path = base / filename
    elif capability.probe_kind in {"insightface", "torch"}:
        for bundle_key in capability.model_bundles:
            model_dir = model_dirs.get(bundle_key)
            if model_dir is not None:
                break

    result = run_probe(
        capability_key=capability.key,
        site_packages=args.site_packages,
        modules=capability.modules,
        probe_kind=capability.probe_kind,
        requested_device=args.device,
        profile=args.profile,
        model_path=model_path,
        model_dir=model_dir,
        transformers_symbols=capability.transformers_symbols,
        expected_outputs=capability.expected_outputs,
        expected_embedding_dim=capability.expected_embedding_dim,
        prepare_hook=detail.get("prepare", ""),
        probe_level=args.level,
    )
    print(result.to_json())
    return 0 if result.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "probe":
        return _run_capability_probe(args)
    if args.command == "validate-profile":
        try:
            validate_ai_runtime_imports(
                args.site_packages,
                variant=args.variant,
                include_dino=not bool(args.no_dino),
            )
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print("AI runtime imports validated.")
        return 0
    if args.command == "status":
        status = load_ai_runtime_installation_status()
        if args.json:
            preferred = status.profiles.get(status.preferred_variant)
            payload = {
                "root": str(status.directories.root),
                "installed_variants": list(status.installed_variants),
                "preferred_variant": status.preferred_variant,
                "torch_variants": list(status.dino_installed_variants),
                # The clean-machine harness probes this exact directory, so the
                # installed build reports it rather than letting the harness guess.
                "site_packages": str(preferred.site_packages_dir) if preferred else "",
            }
            print(json.dumps(payload, indent=2))
        else:
            installed = ", ".join(status.installed_variants) if status.installed_variants else "none"
            print(f"Install root: {status.directories.root}")
            print(f"Installed variants: {installed}")
            print(f"Preferred variant: {status.preferred_variant}")
        return 0

    try:
        status = install_ai_runtime(
            args.variant,
            force=bool(args.force),
            include_dino=not bool(args.no_dino),
            install_root=args.install_root,
            output_callback=print,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    installed = ", ".join(status.installed_variants) if status.installed_variants else "none"
    print(f"AI runtime installation complete.")
    print(f"Install root: {status.directories.root}")
    print(f"Installed variants: {installed}")
    print(f"Preferred variant: {status.preferred_variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
