from __future__ import annotations

import queue
import sqlite3
import threading
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


@dataclass(frozen=True)
class TelemetryEvent:
    image_id: str
    folder_id: str | None
    cluster_id: str | None
    category_id: str | None
    ai_initial_bucket: str
    user_final_bucket: str
    previous_bucket: str | None
    override_type: str
    action_source: str | None
    ai_initial_score: float | None
    base_score: float | None
    adapter_score: float | None
    topiq_score: float | None
    adapter_version: str | None
    model_version: str | None
    is_final: int
    ignored_for_training: int
    created_at: str


def normalize_bucket(value: str | None) -> str:
    text = str(value or "").strip().lower().replace("_", " ")
    aliases = {
        "obvious winner": "ai pick",
        "winner": "ai pick",
        "likely keeper": "keeper",
        "likely reject": "reject",
        "review": "needs review",
    }
    return aliases.get(text, text)


def classify_override(ai_initial_bucket: str, user_final_bucket: str) -> str:
    ai = normalize_bucket(ai_initial_bucket)
    user = normalize_bucket(user_final_bucket)

    if ai == "reject" and user in {"keeper", "ai pick"}:
        return "reject_rescue"
    if ai == "needs review" and user in {"keeper", "ai pick"}:
        return "review_promotion"
    if ai in {"keeper", "ai pick"} and user == "reject":
        return "keeper_demotion"
    if ai == "ai pick" and user in {"needs review", "keeper", "reject"}:
        return "pick_demotion"
    if ai in {"reject", "needs review"} and user in {"reject", "needs review"}:
        return "low_bucket_confirmation"
    if ai in {"keeper", "ai pick"} and user in {"keeper", "ai pick"}:
        return "high_bucket_confirmation"
    return "bucket_change"


def ensure_user_overrides_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS user_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id TEXT NOT NULL,
            folder_id TEXT,
            cluster_id TEXT,
            category_id TEXT,
            ai_initial_bucket TEXT NOT NULL,
            user_final_bucket TEXT NOT NULL,
            previous_bucket TEXT,
            override_type TEXT NOT NULL,
            action_source TEXT,
            ai_initial_score REAL,
            base_score REAL,
            adapter_score REAL,
            topiq_score REAL,
            adapter_version TEXT,
            model_version TEXT,
            is_final INTEGER DEFAULT 1,
            ignored_for_training INTEGER DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_user_overrides_image_id
          ON user_overrides(image_id);
        CREATE INDEX IF NOT EXISTS idx_user_overrides_folder_id
          ON user_overrides(folder_id);
        CREATE INDEX IF NOT EXISTS idx_user_overrides_cluster_id
          ON user_overrides(cluster_id);
        CREATE INDEX IF NOT EXISTS idx_user_overrides_category_id
          ON user_overrides(category_id);
        CREATE INDEX IF NOT EXISTS idx_user_overrides_override_type
          ON user_overrides(override_type);
        CREATE INDEX IF NOT EXISTS idx_user_overrides_created_at
          ON user_overrides(created_at);
        CREATE INDEX IF NOT EXISTS idx_user_overrides_training
          ON user_overrides(ignored_for_training, is_final);
        """
    )
    connection.commit()


def build_override_report(rows: Sequence[Mapping[str, object]], *, top_n: int = 10) -> dict[str, object]:
    """Summarize user override telemetry without changing training behavior."""

    records = [dict(row) for row in rows]
    final_training = [
        row
        for row in records
        if _int_value(row.get("is_final")) == 1 and _int_value(row.get("ignored_for_training")) == 0
    ]
    report = {
        "override_count": len(records),
        "training_eligible_count": len(final_training),
        "intermediate_or_ignored_count": max(0, len(records) - len(final_training)),
        "counts_by_type": _count_rows(records, "override_type"),
        "training_counts_by_type": _count_rows(final_training, "override_type"),
        "counts_by_category": _count_rows(final_training, "category_id"),
        "counts_by_folder": _count_rows(final_training, "folder_id"),
        "counts_by_model_version": _count_rows(final_training, "model_version"),
        "counts_by_action_source": _count_rows(records, "action_source"),
        "reject_rescues_by_folder": _count_rows(
            [row for row in final_training if row.get("override_type") == "reject_rescue"],
            "folder_id",
        ),
        "pick_demotions_by_folder": _count_rows(
            [row for row in final_training if row.get("override_type") == "pick_demotion"],
            "folder_id",
        ),
        "worst_categories_by_reject_rescue": _top_counts(
            [row for row in final_training if row.get("override_type") == "reject_rescue"],
            "category_id",
            top_n=top_n,
        ),
        "worst_folders_by_overrides": _top_counts(final_training, "folder_id", top_n=top_n),
        "top_clusters_with_winner_swaps": _top_counts(
            [row for row in final_training if row.get("override_type") == "winner_swap"],
            "cluster_id",
            top_n=top_n,
        ),
    }
    return report


def format_override_report(report: Mapping[str, object]) -> str:
    lines = [
        "Override Report",
        "",
        f"Overrides: {int(report.get('override_count') or 0)}",
        f"Training eligible: {int(report.get('training_eligible_count') or 0)}",
        f"Intermediate/ignored: {int(report.get('intermediate_or_ignored_count') or 0)}",
    ]
    for title, key in (
        ("By type", "training_counts_by_type"),
        ("By category", "counts_by_category"),
        ("By folder", "counts_by_folder"),
        ("By model version", "counts_by_model_version"),
        ("Reject rescues by folder", "reject_rescues_by_folder"),
        ("Pick demotions by folder", "pick_demotions_by_folder"),
    ):
        rows = report.get(key)
        if not isinstance(rows, dict) or not rows:
            continue
        lines.extend(["", f"{title}:"])
        for name, count in rows.items():
            lines.append(f"  {name}: {count}")
    return "\n".join(lines)


def _count_rows(rows: Sequence[Mapping[str, object]], key: str) -> dict[str, int]:
    counts = Counter(_label_value(row.get(key)) for row in rows)
    counts.pop("", None)
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _top_counts(rows: Sequence[Mapping[str, object]], key: str, *, top_n: int) -> list[dict[str, object]]:
    counts = _count_rows(rows, key)
    return [
        {"key": name, "count": count}
        for name, count in list(counts.items())[: max(0, int(top_n))]
    ]


def _label_value(value: object) -> str:
    text = str(value or "").strip()
    return text or "unknown"


def _int_value(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class ThreadedTelemetryLogger:
    def __init__(self, db_path: str | Path, batch_size: int = 50, flush_interval_sec: float = 0.5):
        self.db_path = Path(db_path)
        self.batch_size = max(1, int(batch_size))
        self.flush_interval_sec = max(0.05, float(flush_interval_sec))
        self.queue: queue.Queue[TelemetryEvent | None] = queue.Queue()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="TelemetryLogger", daemon=True)
        self._thread.start()

    def log_event(self, event: TelemetryEvent) -> None:
        if self._closed:
            return
        self.queue.put(event)

    def shutdown(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self.queue.put(None)
        self._thread.join(timeout=timeout)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=NORMAL;")
        connection.execute("PRAGMA busy_timeout=5000;")
        ensure_user_overrides_schema(connection)
        return connection

    def _run(self) -> None:
        connection = self._connect()
        buffer: list[TelemetryEvent] = []
        last_flush = time.monotonic()
        try:
            while True:
                timeout = max(0.0, self.flush_interval_sec - (time.monotonic() - last_flush))
                try:
                    event = self.queue.get(timeout=timeout)
                except queue.Empty:
                    event = None
                    timed_flush = True
                else:
                    timed_flush = False

                if event is None:
                    if buffer:
                        self._flush(connection, buffer)
                        buffer.clear()
                        last_flush = time.monotonic()
                    if not timed_flush:
                        self.queue.task_done()
                        break
                    continue

                buffer.append(event)
                self.queue.task_done()
                if len(buffer) >= self.batch_size or (time.monotonic() - last_flush) >= self.flush_interval_sec:
                    self._flush(connection, buffer)
                    buffer.clear()
                    last_flush = time.monotonic()
        finally:
            if buffer:
                self._flush(connection, buffer)
            connection.close()

    def _flush(self, connection: sqlite3.Connection, events: list[TelemetryEvent]) -> None:
        rows = [asdict(event) for event in events]
        connection.executemany(
            """
            INSERT INTO user_overrides (
                image_id, folder_id, cluster_id, category_id,
                ai_initial_bucket, user_final_bucket, previous_bucket,
                override_type, action_source,
                ai_initial_score, base_score, adapter_score, topiq_score,
                adapter_version, model_version,
                is_final, ignored_for_training, created_at
            )
            VALUES (
                :image_id, :folder_id, :cluster_id, :category_id,
                :ai_initial_bucket, :user_final_bucket, :previous_bucket,
                :override_type, :action_source,
                :ai_initial_score, :base_score, :adapter_score, :topiq_score,
                :adapter_version, :model_version,
                :is_final, :ignored_for_training, :created_at
            )
            """,
            rows,
        )
        connection.commit()
