"""Standalone subject-mask worker: BiRefNet on one image. Run as ``python subject_mask_worker.py --job job.json``.

Runs under the managed AI runtime (torch), where the application package is not importable, so ``birefnet_worker`` is imported as a
sibling module. The same BiRefNet model and pre/post-processing the built-in editor used for its subject masks. Protocol on stdout:

    PROGRESS {"stage": ..., "message": ...}
    RESULT {"width": w, "height": h, "device": ...}      exactly once on success; the 8-bit coverage map is the PNG at ``outputPath``
    ERROR {"category": ..., "message": ..., "remediation": ...}   exit code 1
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROGRESS_PREFIX = "PROGRESS "
RESULT_PREFIX = "RESULT "
ERROR_PREFIX = "ERROR "


def _say(prefix: str, payload: dict) -> None:
    print(prefix + json.dumps(payload, sort_keys=True, default=str), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args(argv)
    try:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import birefnet_worker as worker
        except ImportError:  # pragma: no cover - package import for tests
            from image_triage import birefnet_worker as worker
        result = worker.generate_subject_mask(
            model_dir=Path(job["modelDir"]),
            input_path=Path(job["inputPath"]),
            output_path=Path(job["outputPath"]),
            components_dir=None,
            requested_device=str(job.get("device", "auto")),
            emit_result=False,
        )
    except Exception as exc:  # reported to the host, which shows it in the editor's popup
        _say(ERROR_PREFIX, {"category": "worker_failed", "message": f"{exc.__class__.__name__}: {exc}", "remediation": ""})
        return 1
    _say(RESULT_PREFIX, {"width": result["width"], "height": result["height"], "device": result["device"]})
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
