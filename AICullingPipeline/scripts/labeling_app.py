"""CLI entry point for the local PySide6 labeling application."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
_READY_FILE_ENV = "IMAGE_TRIAGE_LABELING_READY_FILE"


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Launch the local labeling app for pairwise and cluster culling annotations."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/labeling_app.json"),
        help="Path to the JSON labeling config file.",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        help="Override the folder containing images.csv, image_ids.json, and clusters.csv.",
    )
    parser.add_argument("--output-dir", type=Path, help="Override the label output folder.")
    parser.add_argument(
        "--annotator-id",
        type=str,
        help="Optional annotator identifier saved with label records.",
    )
    return parser.parse_args()


def main() -> None:
    """Load config and launch the labeling UI."""
    try:
        from app.engine import LabelingConfig

        args = parse_args()
        config = LabelingConfig.from_file(args.config).apply_overrides(
            artifacts_dir=args.artifacts_dir,
            output_dir=args.output_dir,
            annotator_id=args.annotator_id,
        )
        _bootstrap_pyside6_runtime()
        from app.labeling.ui import launch_labeling_app
    except ImportError as exc:
        wrapped = ImportError(
            "PySide6 is required for the labeling app. Install it with "
            "'pip install -r requirements.txt'."
        )
        _write_startup_error(wrapped, traceback.format_exc())
        raise wrapped from exc
    except Exception as exc:
        _write_startup_error(exc, traceback.format_exc())
        raise

    try:
        raise SystemExit(launch_labeling_app(config))
    except BaseException as exc:
        if isinstance(exc, SystemExit) and exc.code in (0, None):
            raise
        _write_startup_error(exc, traceback.format_exc())
        raise


def _bootstrap_pyside6_runtime() -> None:
    """Ensure PySide6 can find its Qt plugins in this Python environment."""

    import PySide6

    pyside_dir = Path(PySide6.__file__).resolve().parent
    plugin_dir = pyside_dir / "plugins"
    platform_dir = plugin_dir / "platforms"

    os.environ.setdefault("QT_PLUGIN_PATH", str(plugin_dir))
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(platform_dir))

    current_path = os.environ.get("PATH", "")
    path_entries = current_path.split(os.pathsep) if current_path else []
    if str(pyside_dir) not in path_entries:
        os.environ["PATH"] = str(pyside_dir) + os.pathsep + current_path


def _write_startup_error(exc: BaseException, details: str) -> None:
    ready_file = os.environ.get(_READY_FILE_ENV, "").strip()
    if not ready_file:
        return
    ready_path = Path(ready_file).expanduser()
    payload = {
        "state": "error",
        "message": str(exc) or exc.__class__.__name__,
        "details": details.strip(),
    }
    try:
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(json.dumps(payload, ensure_ascii=True), encoding="utf-8")
    except OSError:
        pass


if __name__ == "__main__":
    main()
