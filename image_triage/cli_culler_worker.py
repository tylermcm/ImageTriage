"""Plain-file, headless bridge. Run with the isolated engine Python, never the host.

Protocol: one request JSON filename argument; newline-delimited JSON events on
stdout. Engine/library stdout is redirected to stderr so diagnostics cannot
corrupt the protocol. The caller owns process-tree cancellation and activation;
completion here only identifies a fully exported, unique staging generation.
"""
from __future__ import annotations

from contextlib import redirect_stdout
import json
import importlib
from pathlib import Path
import sys
import traceback
import uuid

PROTOCOL = "image-triage-cli-culler/1"


def run(request: dict, emit) -> None:
    if request.get("protocol") != PROTOCOL:
        raise ValueError(f"Expected request protocol {PROTOCOL}")
    folders = request["folders"]
    if not isinstance(folders, list) or not folders or not all(isinstance(p, str) and p for p in folders):
        raise ValueError("folders must be a nonempty list of folder paths")
    # Import the engine before resolving the app manifest: the app contains an
    # incompatible legacy aiculler package with the very same import name.
    import aiculler
    import bracketeering

    if not callable(getattr(aiculler, "cull", None)):
        raise RuntimeError("Legacy aiculler loaded; use the isolated CLI-Culler runtime")
    # This module belongs to the separately installed engine, not the host's
    # incompatible package of the same name.
    outputs = importlib.import_module("aiculler.outputs")

    settings = aiculler.CullSettings.from_dict(request["settings"])
    emit({"event": "runtime", "engine_path": str(Path(aiculler.__file__).resolve()),
          "bracketeering_path": str(Path(bracketeering.__file__).resolve()),
          "engine_version": aiculler.__version__})
    # The caller passes a dedicated engine cache namespace, never the legacy DB.
    cache_root = Path(request["cache_root"])
    cache_root.mkdir(parents=True, exist_ok=True)
    generation = Path(request["staging_root"]) / uuid.uuid4().hex
    generation.mkdir(parents=True)
    registry = aiculler.ModelRegistry.from_app_manifest(request["app_path"], device=request.get("device", "auto"))
    try:
        result = aiculler.cull(
            folders, settings, models=registry,
            cache_db=cache_root / "scores.sqlite",
            cache_dir=cache_root / "previews",
            on_progress=lambda stage, done, total: emit(
                {"event": "progress", "stage": stage, "done": done, "total": total}),
            workers=request.get("workers"),
        )
        decisions = outputs.write_decisions(result, generation / "decisions.json")
        index = outputs.write_index(result, generation / "index.npz") if result.index else None
    finally:
        registry.close()
    emit({"event": "complete", "decisions": str(decisions),
          "index": str(index) if index else None,
          "warnings": list(result.warnings), "models": dict(result.models)})


def main() -> int:
    protocol_out = sys.stdout

    def emit(event):
        protocol_out.write(json.dumps(event, ensure_ascii=True) + "\n")
        protocol_out.flush()

    try:
        arguments = sys.argv[1:]
        if arguments[:1] == ["--await-start"]:
            arguments = arguments[1:]
            if sys.stdin.readline() != "start\n":
                raise ValueError("Worker startup was not authorized by its parent")
        if len(arguments) != 1:
            raise ValueError("Usage: cli_culler_worker.py REQUEST.json")
        request = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
        with redirect_stdout(sys.stderr):
            run(request, emit)
        return 0
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        emit({"event": "error", "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
