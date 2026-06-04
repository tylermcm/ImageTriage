from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Iterable

from .dino_prefilter import DINOPrefilterDecision, DINOPrefilterMode, _bool_value, _clamped_score


class PHashExecutionMode(str, Enum):
    BEFORE_AI = "before_ai"
    PARALLEL_WITH_DINO = "parallel_with_dino"
    PARALLEL_WITH_MAIN = "parallel_with_main"


@dataclass(slots=True, frozen=True)
class PHashPrefilterSettings:
    enabled: bool = True
    mode: DINOPrefilterMode = DINOPrefilterMode.SOFT_QUARANTINE
    execution_mode: PHashExecutionMode = PHashExecutionMode.BEFORE_AI
    hamming_threshold: int = 6
    cache_enabled: bool = True
    diagnostics_enabled: bool = True

    def normalized(self) -> "PHashPrefilterSettings":
        mode = self.mode
        if not isinstance(mode, DINOPrefilterMode):
            try:
                mode = DINOPrefilterMode(str(mode))
            except ValueError:
                mode = DINOPrefilterMode.SOFT_QUARANTINE
        execution_mode = self.execution_mode
        if not isinstance(execution_mode, PHashExecutionMode):
            execution_mode = coerce_phash_execution_mode(execution_mode)
        return replace(
            self,
            enabled=bool(self.enabled),
            mode=mode,
            execution_mode=execution_mode,
            hamming_threshold=max(0, min(64, int(self.hamming_threshold))),
            cache_enabled=bool(self.cache_enabled),
            diagnostics_enabled=bool(self.diagnostics_enabled),
        )

    def to_cache_payload(self) -> dict[str, object]:
        normalized = self.normalized()
        payload = asdict(normalized)
        payload["mode"] = normalized.mode.value
        payload["execution_mode"] = normalized.execution_mode.value
        payload["schema_version"] = PHASH_PREFILTER_SCHEMA_VERSION
        return payload


PHASH_PREFILTER_SCHEMA_VERSION = 1
PHASH_PREFILTER_ARTIFACT_DIRNAME = "phash_prefilter"
PHASH_PREFILTER_REPORT_FILENAME = "phash_prefilter_report.json"
PHASH_PREFILTER_ROWS_FILENAME = "phash_prefilter_rows.jsonl"
PHASH_PREFILTER_LOG_FILENAME = "phash_prefilter.log"
PHASH_PREFILTER_CACHE_FILENAME = "phash_cache.json"
_HIDDEN_ROOT_NAME = ".image_triage_ai"


@dataclass(slots=True, frozen=True)
class PHashPrefilterPaths:
    hidden_root: Path
    artifact_dir: Path
    report_path: Path
    rows_path: Path
    log_path: Path
    cache_path: Path

    def ensure(self) -> "PHashPrefilterPaths":
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        return self


def build_phash_prefilter_paths(folder_or_workflow_paths: object) -> PHashPrefilterPaths:
    hidden_root = getattr(folder_or_workflow_paths, "hidden_root", None)
    if hidden_root is None:
        folder = Path(folder_or_workflow_paths).expanduser().resolve()
        hidden_root = folder / _HIDDEN_ROOT_NAME
    hidden_root = Path(hidden_root).expanduser().resolve()
    artifact_dir = hidden_root / PHASH_PREFILTER_ARTIFACT_DIRNAME
    return PHashPrefilterPaths(
        hidden_root=hidden_root,
        artifact_dir=artifact_dir,
        report_path=artifact_dir / PHASH_PREFILTER_REPORT_FILENAME,
        rows_path=artifact_dir / PHASH_PREFILTER_ROWS_FILENAME,
        log_path=artifact_dir / PHASH_PREFILTER_LOG_FILENAME,
        cache_path=artifact_dir / PHASH_PREFILTER_CACHE_FILENAME,
    )


def run_phash_prefilter_from_signal_rows(
    rows: Iterable[dict[str, object]],
    *,
    settings: PHashPrefilterSettings,
    paths: PHashPrefilterPaths,
) -> dict[str, DINOPrefilterDecision]:
    normalized = settings.normalized()
    decisions: list[DINOPrefilterDecision] = []
    scanned_count = 0
    quarantined_count = 0
    removed_count = 0
    append_phash_prefilter_log(
        paths,
        "phash_prefilter.start",
        enabled=normalized.enabled,
        mode=normalized.mode.value,
        execution_mode=normalized.execution_mode.value,
        hamming_threshold=normalized.hamming_threshold,
    )
    for row in rows:
        path = str(row.get("file_path") or row.get("path") or "").strip()
        if not path:
            continue
        scanned_count += 1
        score = _clamped_score(row.get("phash_duplicate_score"))
        best_representative = _bool_value(row.get("best_representative"), score <= 0.0)
        if not normalized.enabled or score < 1.0 or best_representative:
            decisions.append(DINOPrefilterDecision(path=path, action="pass", reason="phash_duplicate_trash", score=score))
            continue
        action = "remove_from_pool" if normalized.mode == DINOPrefilterMode.POOL_REMOVAL else "quarantine"
        if action == "remove_from_pool":
            removed_count += 1
        else:
            quarantined_count += 1
        decisions.append(DINOPrefilterDecision(path=path, action=action, reason="phash_duplicate_trash", score=score))

    write_phash_prefilter_audit(
        paths,
        settings=normalized,
        rows=(decision.to_row() for decision in decisions),
        scanned_count=scanned_count,
        quarantined_count=quarantined_count,
        removed_from_pool_count=removed_count,
    )
    append_phash_prefilter_log(
        paths,
        "phash_prefilter.finished",
        scanned=scanned_count,
        quarantined=quarantined_count,
        removed_from_pool=removed_count,
    )
    return {decision.path: decision for decision in decisions}


def write_phash_prefilter_audit(
    paths: PHashPrefilterPaths,
    *,
    settings: PHashPrefilterSettings,
    rows: Iterable[dict[str, object]],
    scanned_count: int = 0,
    quarantined_count: int = 0,
    removed_from_pool_count: int = 0,
) -> dict[str, object]:
    paths.ensure()
    with paths.rows_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n")
    payload = {
        "schema_version": PHASH_PREFILTER_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "settings": settings.normalized().to_cache_payload(),
        "counts": {
            "scanned": max(0, int(scanned_count)),
            "quarantined": max(0, int(quarantined_count)),
            "removed_from_pool": max(0, int(removed_from_pool_count)),
        },
        "artifacts": {
            "rows": str(paths.rows_path),
            "log": str(paths.log_path),
            "cache": str(paths.cache_path),
        },
    }
    paths.report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def load_phash_prefilter_decisions(paths: PHashPrefilterPaths) -> dict[str, DINOPrefilterDecision]:
    if not paths.rows_path.exists():
        return {}
    decisions: dict[str, DINOPrefilterDecision] = {}
    for line in paths.rows_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        path = str(row.get("path") or "").strip()
        if not path:
            continue
        rescue_reasons = row.get("rescue_reasons")
        if not isinstance(rescue_reasons, list):
            rescue_reasons = []
        decisions[path] = DINOPrefilterDecision(
            path=path,
            action=str(row.get("action") or "pass"),
            reason=str(row.get("reason") or "phash_duplicate_trash"),
            score=_clamped_score(row.get("score")),
            rescue_reasons=tuple(str(reason) for reason in rescue_reasons if str(reason).strip()),
        )
    return decisions


def append_phash_prefilter_log(paths: PHashPrefilterPaths, event: str, **fields: object) -> None:
    paths.ensure()
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    with paths.log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n")


def phash_execution_mode_label(mode: PHashExecutionMode | str) -> str:
    resolved = coerce_phash_execution_mode(mode)
    if resolved == PHashExecutionMode.PARALLEL_WITH_DINO:
        return "Async with DINO"
    if resolved == PHashExecutionMode.PARALLEL_WITH_MAIN:
        return "Async with main AI"
    return "Before AI scoring"


def coerce_phash_execution_mode(value: object) -> PHashExecutionMode:
    if isinstance(value, PHashExecutionMode):
        return value
    try:
        return PHashExecutionMode(str(value or ""))
    except ValueError:
        return PHashExecutionMode.BEFORE_AI


def default_phash_prefilter_settings() -> PHashPrefilterSettings:
    return PHashPrefilterSettings()
