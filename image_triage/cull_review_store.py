"""Where a cull review was left, kept per folder so it is still there when the window is opened again or the app restarts.

One small JSON file per folder in the app's data folder (`cull_review/<hash of the folder's path key>.json`), never beside the photos:
the active filter and sort, the frames the person restored (un-flagged), the selected frame, how far the list was scrolled, and
whether the result was applied. The folder is keyed with the shared `path_keys.path_key`.

A saved review belongs to one report. `report_identity` names the report by its content (the digest of the cascade's CSV and of its
summary, which says when the run was made; or of the native engine's decisions), so a new analysis of the folder is a different report
and never inherits an old review, while copying or syncing the folder (which changes file dates, not content) keeps it. `load` answers
only for the identity it is asked about.

No Qt apart from finding the app's data folder. Reading and writing touch the disk: call them from a worker thread.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .path_keys import path_key

VERSION = 1
DIRECTORY = "cull_review"
FILTER_ALL = "all"


def store_dir() -> Path:
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    return Path(base) / DIRECTORY


def state_path(folder: str, root: Path | None = None) -> Path:
    key = path_key(folder)
    return (root or store_dir()) / (hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".json")


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def report_identity(folder: str) -> str:
    """What the folder's saved report is, by content: "" when it has none or it cannot be read."""
    from .ai_results import inspect_ai_bundle_source
    from .ai_workflow import existing_hidden_ai_report_dir

    try:
        report = existing_hidden_ai_report_dir(folder)
        if report is None:
            return ""
        report = Path(report)
        if report.is_file() and report.name == "active.json":
            descriptor = json.loads(report.read_text(encoding="utf-8"))
            digest = str(descriptor["outputs"]["decisions"]["sha256"])
            return "native:" + digest if digest else ""
        if report.is_file():
            return "native:" + _digest(report)
        source = inspect_ai_bundle_source(report)
        digest = _digest(Path(source.export_csv_path))
        if source.summary_json_path:                # the summary carries when the run was made: the same decisions from a later run are a new report
            digest += _digest(Path(source.summary_json_path))
        return "cascade:" + hashlib.sha256(digest.encode("ascii")).hexdigest()
    except Exception:
        return ""


def clean(state: dict) -> dict:
    """A review state reduced to what is kept, with the types it must have. Unknown or malformed parts are dropped, not guessed."""
    text = lambda value, default="": value if isinstance(value, str) else default
    applied = state.get("applied") if isinstance(state.get("applied"), dict) else None
    out = {
        "filter": text(state.get("filter"), FILTER_ALL) or FILTER_ALL,
        "sort": text(state.get("sort"), "stack") if state.get("sort") in ("stack", "reason") else "stack",
        "restored": sorted({path for path in (state.get("restored") if isinstance(state.get("restored"), (list, tuple)) else ()) if isinstance(path, str) and path}),
        "sel": text(state.get("sel")),
        "top": max(0, int(state["top"])) if isinstance(state.get("top"), int) and not isinstance(state.get("top"), bool) else 0,
        "applied": None,
    }
    if applied is not None and applied.get("act") in ("reject", "bin"):
        ids = [path for path in (applied.get("ids") if isinstance(applied.get("ids"), (list, tuple)) else ()) if isinstance(path, str) and path]
        out["applied"] = {"act": applied["act"], "ids": ids, "when": text(applied.get("when"))}
    return out


def is_default(state: dict) -> bool:
    """Nothing worth keeping: the review is as a fresh report shows it."""
    kept = clean(state)
    return kept["filter"] == FILTER_ALL and kept["sort"] == "stack" and not kept["restored"] and not kept["top"] and kept["applied"] is None and not kept["sel"]


def save(folder: str, identity: str, state: dict, root: Path | None = None) -> bool:
    """Keep `state` for this folder's report. Without an identity nothing is kept (there is no report to tie it to)."""
    if not folder or not identity:
        return False
    target = state_path(folder, root)
    payload = {"version": VERSION, "folder": path_key(folder), "report": identity, **clean(state)}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, target)
        return True
    except OSError:
        return False


def load(folder: str, identity: str, root: Path | None = None) -> dict | None:
    """The review kept for this folder, only if it was kept for this very report; otherwise None."""
    if not folder or not identity:
        return None
    try:
        payload = json.loads(state_path(folder, root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("version") != VERSION:
        return None
    if payload.get("report") != identity or payload.get("folder") != path_key(folder):
        return None                                 # another report (a new analysis), or another folder whose hash collided: never restored
    return clean(payload)


def forget(folder: str, root: Path | None = None) -> None:
    """Drop what was kept for this folder (its results were cleared or its AI data deleted)."""
    if not folder:
        return
    try:
        state_path(folder, root).unlink()
    except OSError:
        pass
