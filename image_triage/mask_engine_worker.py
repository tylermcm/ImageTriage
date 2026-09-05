from __future__ import annotations

"""Unified editor inference host — the "MaskEngine".

Hosts every editor mask model in ONE persistent subprocess so they share a
single torch import and a single CUDA context, instead of one subprocess (and
one ~7 s CUDA init + one CUDA context) per model. See
``docs/editor_inference_host_decision.md``.

It deliberately reuses the existing per-model workers rather than duplicating
their logic: ``birefnet_worker`` and ``oneformer_worker`` are torch-only,
Qt-free, and import torch lazily, so instantiating both engines here means they
initialize CUDA once and stay resident together. This module is executed by the
managed AI runtime Python (never the Qt process) and speaks the same JSON-lines
protocol as the individual workers, with an added ``engine`` field for routing.
"""

import argparse
import json
import sys
import traceback
from pathlib import Path


# The individual workers ship as sibling scripts (image_triage/ in source,
# ai_workers/ when frozen); make them importable as top-level modules.
_WORKER_DIR = Path(__file__).resolve().parent
if str(_WORKER_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKER_DIR))

import birefnet_worker  # noqa: E402
import depth_worker  # noqa: E402
import oneformer_worker  # noqa: E402
import sam_worker  # noqa: E402


ENGINE_SUBJECT = "subject"
ENGINE_SEMANTIC = "semantic"
ENGINE_PROMPT = "prompt"
ENGINE_DEPTH = "depth"
ENGINE_NAMES = (ENGINE_SUBJECT, ENGINE_SEMANTIC, ENGINE_PROMPT, ENGINE_DEPTH)


class MaskEngineHost:
    """Own one process's worth of torch and route requests to per-model engines.

    Engines are the existing worker engine classes; constructing them is cheap
    (no torch until ``warm_imports``), and because they live in one process the
    first ``warm_imports`` pays torch import + CUDA init once for all of them.
    """

    def __init__(
        self,
        requested_device: str,
        *,
        subject_engine=None,
        semantic_engine=None,
        prompt_engine=None,
        depth_engine=None,
    ) -> None:
        self.requested_device = requested_device
        self._subject = subject_engine or birefnet_worker._BiRefNetEngine(requested_device)
        self._semantic = semantic_engine or oneformer_worker._OneFormerEngine(requested_device)
        self._prompt = prompt_engine or sam_worker._SamEngine(requested_device)
        self._depth = depth_engine or depth_worker._DepthEngine(requested_device)

    def _engine(self, name: str):
        if name == ENGINE_SUBJECT:
            return self._subject
        if name == ENGINE_SEMANTIC:
            return self._semantic
        if name == ENGINE_PROMPT:
            return self._prompt
        if name == ENGINE_DEPTH:
            return self._depth
        raise ValueError(f"Unknown engine: {name or '<empty>'}")

    def warm_imports(self, engine: str | None = None) -> str:
        # No engine -> warm the whole host (torch/CUDA shared; later warms are
        # cheap because torch is already imported process-wide).
        if not engine:
            device = self._subject.warm_imports()
            self._semantic.warm_imports()
            self._prompt.warm_imports()
            self._depth.warm_imports()
            return device
        return self._engine(engine).warm_imports()

    def embed(self, request: dict[str, object]) -> dict[str, object]:
        from pathlib import Path as _Path

        width, height = self._prompt.embed(
            _Path(str(request.get("inputPath") or "")).resolve(),
            image_key=str(request.get("imageKey") or "") or None,
        )
        return {"device": self._prompt.device, "sourceSize": [width, height]}

    def segment(self, request: dict[str, object]) -> dict[str, object]:
        from pathlib import Path as _Path

        raw_points = request.get("points") or []
        points = [(float(p[0]), float(p[1])) for p in raw_points if len(p) == 2]
        raw_labels = request.get("labels")
        if isinstance(raw_labels, list) and len(raw_labels) == len(points):
            labels = [int(v) for v in raw_labels]
        else:
            labels = [1] * len(points)
        return self._prompt.segment(
            points=points,
            labels=labels,
            output_path=_Path(str(request.get("outputPath") or "")).resolve(),
            minimum_area=float(request.get("minimumArea") or 0.0),
            image_key=str(request.get("imageKey") or "") or None,
        )

    def segment_many(self, request: dict[str, object]) -> dict[str, object]:
        from pathlib import Path as _Path

        raw_groups = request.get("pointGroups") or []
        point_groups = [
            [(float(point[0]), float(point[1])) for point in group if len(point) == 2]
            for group in raw_groups
        ]
        raw_label_groups = request.get("labelGroups") or []
        label_groups = [
            [int(value) for value in labels]
            for labels in raw_label_groups
        ]
        output_paths = [
            _Path(str(path)).resolve() for path in (request.get("outputPaths") or [])
        ]
        results = self._prompt.segment_many(
            point_groups=point_groups,
            label_groups=label_groups,
            output_paths=output_paths,
            minimum_area=float(request.get("minimumArea") or 0.0),
            image_key=str(request.get("imageKey") or "") or None,
        )
        return {"device": self._prompt.device, "results": results}

    def load_model(self, engine: str, model_dir: Path) -> str:
        return self._engine(engine).load_model(model_dir)

    def infer(self, engine: str, request: dict[str, object]) -> dict[str, object]:
        if engine == ENGINE_SUBJECT:
            return birefnet_worker.generate_subject_mask(
                model_dir=Path(str(request.get("modelDir") or "")).resolve(),
                input_path=Path(str(request.get("input") or "")).resolve(),
                output_path=Path(str(request.get("output") or "")).resolve(),
                components_dir=(
                    Path(str(request["componentsDir"])).resolve()
                    if request.get("componentsDir")
                    else None
                ),
                requested_device=self.requested_device,
                engine=self._subject,
                emit_result=False,
            )
        if engine == ENGINE_SEMANTIC:
            return oneformer_worker.generate_semantic_masks(
                model_dir=Path(str(request.get("modelDir") or "")).resolve(),
                input_path=Path(str(request.get("inputPath") or "")).resolve(),
                output_dir=Path(str(request.get("outputDir") or "")).resolve(),
                categories=oneformer_worker._parse_categories(request.get("categories")),
                minimum_coverage=float(request.get("minimumCoverage") or 0.0),
                requested_device=self.requested_device,
                engine=self._semantic,
                emit_result=False,
            )
        if engine == ENGINE_DEPTH:
            return depth_worker.generate_depth(
                model_dir=Path(str(request.get("modelDir") or "")).resolve(),
                input_path=Path(str(request.get("inputPath") or "")).resolve(),
                output_path=Path(str(request.get("outputPath") or "")).resolve(),
                requested_device=self.requested_device,
                engine=self._depth,
                emit_result=False,
            )
        raise ValueError(f"Unknown engine: {engine or '<empty>'}")


def _server_response(
    request_id: object,
    *,
    result: dict[str, object] | None = None,
    error: str = "",
) -> None:
    print(
        "RESPONSE "
        + json.dumps(
            {
                "id": request_id,
                "ok": not error,
                "result": result or {},
                "error": error,
            },
            default=str,
        ),
        flush=True,
    )


def run_server(requested_device: str, *, host: MaskEngineHost | None = None) -> int:
    host = host or MaskEngineHost(requested_device)
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request_id: object = None
        try:
            request = json.loads(line)
            if not isinstance(request, dict):
                raise ValueError("Worker request must be a JSON object.")
            request_id = request.get("id")
            command = str(request.get("command") or "").strip().casefold()
            engine = str(request.get("engine") or "").strip().casefold() or None
            if command == "warm-imports":
                device = host.warm_imports(engine)
                _server_response(
                    request_id, result={"device": device, "stage": "imports", "engine": engine or "all"}
                )
            elif command == "load-model":
                if engine is None:
                    raise ValueError("load-model requires an 'engine'.")
                model_dir = Path(str(request.get("modelDir") or "")).resolve()
                device = host.load_model(engine, model_dir)
                _server_response(
                    request_id, result={"device": device, "stage": "model", "engine": engine}
                )
            elif command == "infer":
                if engine is None:
                    raise ValueError("infer requires an 'engine'.")
                result = host.infer(engine, request)
                _server_response(request_id, result=result)
            elif command == "embed":
                _server_response(request_id, result=host.embed(request))
            elif command == "segment":
                _server_response(request_id, result=host.segment(request))
            elif command == "segment-many":
                _server_response(request_id, result=host.segment_many(request))
            elif command == "shutdown":
                _server_response(request_id, result={"stage": "shutdown"})
                return 0
            else:
                raise ValueError(f"Unknown worker command: {command or '<empty>'}")
        except Exception as exc:
            detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
            _server_response(request_id, error=detail or str(exc))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Unified editor mask inference host.")
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--device", default="auto")
    args = parser.parse_args(argv)
    if not args.server:
        parser.error("mask_engine_worker only runs in --server mode")
    return run_server(args.device)


if __name__ == "__main__":
    raise SystemExit(main())
