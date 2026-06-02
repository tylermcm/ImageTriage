from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ROOT_TEXT = str(ROOT)
if ROOT_TEXT not in sys.path:
    sys.path.insert(0, ROOT_TEXT)

from image_triage.ai_runtime_packages import (  # noqa: E402
    AI_RUNTIME_INSTALL_CHOICES,
    install_ai_runtime,
    load_ai_runtime_installation_status,
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

    status_parser = subparsers.add_parser("status", help="Print current AI runtime installation status")
    status_parser.add_argument("--json", action="store_true", help="Emit status as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "status":
        status = load_ai_runtime_installation_status()
        if args.json:
            payload = {
                "root": str(status.directories.root),
                "installed_variants": list(status.installed_variants),
                "preferred_variant": status.preferred_variant,
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
