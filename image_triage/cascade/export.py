"""Write a cascade run as the ranked-export CSV (plus summary and a small HTML report) that ``ai_results.load_ai_bundle`` already reads,
so everything downstream of an Analyze run (buckets, dots, inspector, filters) works on cascade results unchanged."""

from __future__ import annotations

import csv
import html
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence

from .folder import (
    ROLE_BRACKET,
    ROLE_CUT,
    ROLE_DUPLICATE,
    ROLE_FINAL,
    ROLE_FLAGGED,
    ROLE_NO_PICK,
    ROLE_PICK,
    ROLE_REJECTED,
    FolderResult,
    FrameOutcome,
)

EXPORT_FILENAME = "ranked_clusters_export.csv"
SUMMARY_FILENAME = "ranked_export_summary.json"
HTML_FILENAME = "ranked_clusters_report.html"

CSV_COLUMNS = (
    "image_id",
    "file_path",
    "file_name",
    "cluster_id",
    "cluster_size",
    "rank_in_cluster",
    "score",
    "triggered_tags",
    "primary_category",
    "cluster_reason",
    "capture_timestamp",
    "cascade_role",
    "cascade_summary",
)

# Folder-wide ordering for "AI Rank" sorting: picks first, flagged frames last.
_ROLE_SCORE = {ROLE_PICK: 1.0, ROLE_FINAL: 0.8, ROLE_DUPLICATE: 0.5, ROLE_BRACKET: 0.5, ROLE_FLAGGED: 0.45, ROLE_CUT: 0.4, ROLE_NO_PICK: 0.3, ROLE_REJECTED: 0.1}
_ROLE_LABEL = {
    ROLE_PICK: "Winner",
    ROLE_FINAL: "Kept",
    ROLE_DUPLICATE: "Duplicate",
    ROLE_BRACKET: "Bracket",
    ROLE_CUT: "Cut",
    ROLE_NO_PICK: "No pick",
    ROLE_REJECTED: "Flagged",
    ROLE_FLAGGED: "Flagged (kept)",
}


def frame_score(outcome: FrameOutcome) -> float:
    score = _ROLE_SCORE.get(outcome.role, 0.5)
    if outcome.role == ROLE_FINAL and outcome.rank == 2:
        score = 0.9
    return round(score - 0.001 * outcome.stack_rank, 4)


def cluster_id(outcome: FrameOutcome) -> str:
    if outcome.burst_index < 0:
        return f"bracket-{-outcome.burst_index:04d}"
    return f"burst-{outcome.burst_index + 1:04d}-stack-{outcome.stack_index + 1}"


def cluster_reason(outcome: FrameOutcome) -> str:
    if outcome.burst_index < 0:
        return f"Exposure bracket of {outcome.stack_size} frames."
    if outcome.stack_count > 1:
        text = f"Stack {outcome.stack_index + 1} of {outcome.stack_count} in a burst of {outcome.burst_size} frames"
        if outcome.split_jump is not None:
            text += f" (a different pose or framing from the stack before it, picture difference {outcome.split_jump})"
        return text + "."
    if outcome.burst_size > 1:
        return f"One stack: a burst of {outcome.burst_size} similar frames."
    return "A single frame."


def build_rows(outcomes: Mapping[str, FrameOutcome], order: Sequence[str], captured: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    """One CSV row per decided frame, in folder ``order``. ``captured`` maps path to a capture-time string."""
    rows: list[dict[str, object]] = []
    for path in order:
        outcome = outcomes.get(path)
        if outcome is None:
            continue
        rows.append(
            {
                "image_id": path,
                "file_path": path,
                "file_name": Path(path).name,
                "cluster_id": cluster_id(outcome),
                "cluster_size": outcome.stack_size,
                "rank_in_cluster": outcome.stack_rank,
                "score": frame_score(outcome),
                "triggered_tags": ",".join(outcome.flags),
                "primary_category": "",
                "cluster_reason": cluster_reason(outcome),
                "capture_timestamp": (captured or {}).get(path, ""),
                "cascade_role": outcome.role,
                "cascade_summary": outcome.summary,
            }
        )
    return rows


def write_export(
    report_dir: str | Path,
    rows: Sequence[Mapping[str, object]],
    result: FolderResult,
    *,
    ranked_with: Sequence[str] = (),
    note: str = "",
    blink_checked: bool = False,
) -> dict[str, Path]:
    """Write the CSV, summary JSON and HTML report into ``report_dir`` (created if needed) and return their paths."""
    folder = Path(report_dir)
    folder.mkdir(parents=True, exist_ok=True)
    csv_path = folder / EXPORT_FILENAME
    _write_atomic(csv_path, _csv_text(rows))
    summary = {
        "engine": "stack-first cascade",
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "frames": len(rows),
        "bursts": result.bursts,
        "stacks": result.stacks,
        "brackets": result.brackets,
        "unreadable": result.unreadable,
        "roles": result.counts(),
        "ranked_with": list(ranked_with),
        "blink_checked": bool(blink_checked),
        "scored_by_maniqa": result.scored_by_maniqa,
        "scored_by_aesthetic": result.scored_by_aesthetic,
        "note": note,
    }
    summary_path = folder / SUMMARY_FILENAME
    _write_atomic(summary_path, json.dumps(summary, indent=2))
    html_path = folder / HTML_FILENAME
    _write_atomic(html_path, _html_text(rows, summary))
    return {"csv": csv_path, "summary": summary_path, "html": html_path}


def _csv_text(rows: Sequence[Mapping[str, object]]) -> str:
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    for row in rows:
        writer.writerow({column: row.get(column, "") for column in CSV_COLUMNS})
    return buffer.getvalue()


def _html_text(rows: Sequence[Mapping[str, object]], summary: Mapping[str, object]) -> str:
    roles = summary.get("roles") or {}
    tally = "".join(f"<li>{html.escape(_ROLE_LABEL.get(str(k), str(k)))}: {v}</li>" for k, v in sorted(dict(roles).items()))  # type: ignore[call-overload]
    body = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            html.escape(str(row["file_name"])),
            html.escape(_ROLE_LABEL.get(str(row["cascade_role"]), str(row["cascade_role"]))),
            html.escape(str(row["cluster_id"])),
            html.escape(str(row["cascade_summary"])),
        )
        for row in rows
    )
    note = f"<p>{html.escape(str(summary.get('note')))}</p>" if summary.get("note") else ""
    return (
        "<!doctype html><meta charset=utf-8><title>Image Triage cleanup report</title>"
        "<style>body{font:14px system-ui;margin:24px}td,th{padding:4px 10px;text-align:left}</style>"
        f"<h1>Image Triage cleanup report</h1><p>{summary.get('frames')} frames in {summary.get('bursts')} bursts "
        f"and {summary.get('stacks')} stacks.</p>{note}<ul>{tally}</ul>"
        f"<table><tr><th>File</th><th>Decision</th><th>Stack</th><th>Why</th></tr>{body}</table>"
    )


def _write_atomic(path: Path, text: str) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)
