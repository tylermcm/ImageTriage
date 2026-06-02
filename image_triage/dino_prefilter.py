from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Iterable


class DINOPrefilterMode(str, Enum):
    SOFT_QUARANTINE = "soft_quarantine"
    POOL_REMOVAL = "pool_removal"


@dataclass(slots=True, frozen=True)
class DINOPrefilterSettings:
    enabled: bool = False
    mode: DINOPrefilterMode = DINOPrefilterMode.SOFT_QUARANTINE
    aggressiveness_percent: int = 85
    technical_trash_enabled: bool = True
    duplicate_trash_enabled: bool = True
    phash_duplicate_enabled: bool = True
    phash_hamming_threshold: int = 6
    low_information_enabled: bool = False
    rescue_ai_high_score_enabled: bool = True
    rescue_user_keep_enabled: bool = True
    rescue_semantic_unique_enabled: bool = True
    rescue_best_representative_enabled: bool = True
    diagnostics_enabled: bool = True

    def normalized(self) -> "DINOPrefilterSettings":
        mode = self.mode
        if not isinstance(mode, DINOPrefilterMode):
            try:
                mode = DINOPrefilterMode(str(mode))
            except ValueError:
                mode = DINOPrefilterMode.SOFT_QUARANTINE
        return replace(
            self,
            enabled=bool(self.enabled),
            mode=mode,
            aggressiveness_percent=max(1, min(100, int(self.aggressiveness_percent))),
            technical_trash_enabled=bool(self.technical_trash_enabled),
            duplicate_trash_enabled=bool(self.duplicate_trash_enabled),
            phash_duplicate_enabled=bool(self.phash_duplicate_enabled),
            phash_hamming_threshold=max(0, min(64, int(self.phash_hamming_threshold))),
            low_information_enabled=bool(self.low_information_enabled),
            rescue_ai_high_score_enabled=bool(self.rescue_ai_high_score_enabled),
            rescue_user_keep_enabled=bool(self.rescue_user_keep_enabled),
            rescue_semantic_unique_enabled=bool(self.rescue_semantic_unique_enabled),
            rescue_best_representative_enabled=bool(self.rescue_best_representative_enabled),
            diagnostics_enabled=bool(self.diagnostics_enabled),
        )

    def to_cache_payload(self) -> dict[str, object]:
        normalized = self.normalized()
        payload = asdict(normalized)
        payload["mode"] = normalized.mode.value
        payload["schema_version"] = DINO_PREFILTER_SCHEMA_VERSION
        payload["model_policy"] = "base_model_only"
        return payload

    def cache_key(self) -> str:
        encoded = json.dumps(self.to_cache_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(slots=True, frozen=True)
class DINOPrefilterSignals:
    path: str
    technical_trash_score: float = 0.0
    duplicate_trash_score: float = 0.0
    phash_duplicate_trash_score: float = 0.0
    low_information_score: float = 0.0
    ai_high_score: bool = False
    user_keep: bool = False
    semantic_unique: bool = False
    best_representative: bool = False


@dataclass(slots=True, frozen=True)
class DINOPrefilterDecision:
    path: str
    action: str
    reason: str = ""
    score: float = 0.0
    rescue_reasons: tuple[str, ...] = ()

    @property
    def is_candidate(self) -> bool:
        return self.action in {"quarantine", "remove_from_pool"}

    @property
    def is_rescued(self) -> bool:
        return self.action == "rescued"

    def to_row(self) -> dict[str, object]:
        return {
            "path": self.path,
            "action": self.action,
            "reason": self.reason,
            "score": self.score,
            "rescue_reasons": list(self.rescue_reasons),
        }


DINO_PREFILTER_SCHEMA_VERSION = 1
DINO_PREFILTER_ARTIFACT_DIRNAME = "dino_prefilter"
DINO_PREFILTER_REPORT_FILENAME = "dino_prefilter_report.json"
DINO_PREFILTER_ROWS_FILENAME = "dino_prefilter_rows.jsonl"
DINO_PREFILTER_LOG_FILENAME = "dino_prefilter.log"
_HIDDEN_ROOT_NAME = ".image_triage_ai"


@dataclass(slots=True, frozen=True)
class DINOPrefilterPaths:
    hidden_root: Path
    artifact_dir: Path
    report_path: Path
    rows_path: Path
    log_path: Path

    def ensure(self) -> "DINOPrefilterPaths":
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        return self


def build_dino_prefilter_paths(folder_or_workflow_paths: object) -> DINOPrefilterPaths:
    hidden_root = getattr(folder_or_workflow_paths, "hidden_root", None)
    if hidden_root is None:
        folder = Path(folder_or_workflow_paths).expanduser().resolve()
        hidden_root = folder / _HIDDEN_ROOT_NAME
    hidden_root = Path(hidden_root).expanduser().resolve()
    artifact_dir = hidden_root / DINO_PREFILTER_ARTIFACT_DIRNAME
    return DINOPrefilterPaths(
        hidden_root=hidden_root,
        artifact_dir=artifact_dir,
        report_path=artifact_dir / DINO_PREFILTER_REPORT_FILENAME,
        rows_path=artifact_dir / DINO_PREFILTER_ROWS_FILENAME,
        log_path=artifact_dir / DINO_PREFILTER_LOG_FILENAME,
    )


def decide_dino_prefilter_action(
    signals: DINOPrefilterSignals,
    settings: DINOPrefilterSettings,
) -> DINOPrefilterDecision:
    normalized = settings.normalized()
    if not normalized.enabled:
        return DINOPrefilterDecision(path=signals.path, action="pass")

    reason_scores: dict[str, float] = {}
    if normalized.technical_trash_enabled:
        reason_scores["technical_trash"] = _clamped_score(signals.technical_trash_score)
    if normalized.duplicate_trash_enabled:
        reason_scores["duplicate_trash"] = _clamped_score(signals.duplicate_trash_score)
    if normalized.phash_duplicate_enabled:
        reason_scores["phash_duplicate_trash"] = _clamped_score(signals.phash_duplicate_trash_score)
    if normalized.low_information_enabled:
        reason_scores["low_information"] = _clamped_score(signals.low_information_score)
    if not reason_scores:
        return DINOPrefilterDecision(path=signals.path, action="pass")

    reason, score = max(reason_scores.items(), key=lambda item: (item[1], item[0]))
    threshold = normalized.aggressiveness_percent / 100.0
    if score < threshold:
        return DINOPrefilterDecision(path=signals.path, action="pass", reason=reason, score=score)

    rescue_reasons: list[str] = []
    if normalized.rescue_ai_high_score_enabled and signals.ai_high_score:
        rescue_reasons.append("ai_high_score")
    if normalized.rescue_user_keep_enabled and signals.user_keep:
        rescue_reasons.append("user_keep")
    if normalized.rescue_semantic_unique_enabled and signals.semantic_unique:
        rescue_reasons.append("semantic_unique")
    if normalized.rescue_best_representative_enabled and signals.best_representative:
        rescue_reasons.append("best_representative")
    if rescue_reasons:
        return DINOPrefilterDecision(
            path=signals.path,
            action="rescued",
            reason=reason,
            score=score,
            rescue_reasons=tuple(rescue_reasons),
        )

    action = "remove_from_pool" if normalized.mode == DINOPrefilterMode.POOL_REMOVAL else "quarantine"
    return DINOPrefilterDecision(path=signals.path, action=action, reason=reason, score=score)


def dino_prefilter_signals_from_signal_row(row: dict[str, object]) -> DINOPrefilterSignals | None:
    path = str(row.get("file_path") or row.get("path") or "").strip()
    if not path:
        return None
    group_size = max(1, _int_value(row.get("group_size"), 1))
    dino_rank = max(1, _int_value(row.get("dino_rank"), 1))
    detail = _optional_score(row.get("detail"))
    exposure_score = _optional_score(row.get("exposure_score"))
    exposure_status = str(row.get("exposure_status") or "").strip().casefold()
    technical_scores: list[float] = []
    if detail is not None:
        technical_scores.append(1.0 - detail)
    if exposure_score is not None and exposure_status in {"overexposed", "underexposed"}:
        technical_scores.append(1.0 - exposure_score)
    duplicate_score = 0.0
    if group_size > 1 and dino_rank > 1:
        duplicate_score = min(1.0, 0.55 + (dino_rank - 1) / max(1, group_size - 1) * 0.45)
    best_representative = group_size <= 1 or dino_rank == 1
    if "best_representative" in row:
        best_representative = _bool_value(row.get("best_representative"), best_representative)
    return DINOPrefilterSignals(
        path=path,
        technical_trash_score=max(technical_scores) if technical_scores else 0.0,
        duplicate_trash_score=duplicate_score,
        phash_duplicate_trash_score=_optional_score(row.get("phash_duplicate_score")) or 0.0,
        low_information_score=_low_information_score(row),
        best_representative=best_representative,
    )


def run_dino_prefilter_from_signal_rows(
    rows: Iterable[dict[str, object]],
    *,
    settings: DINOPrefilterSettings,
    paths: DINOPrefilterPaths,
    cache_hit: bool = False,
) -> dict[str, DINOPrefilterDecision]:
    decisions: list[DINOPrefilterDecision] = []
    scanned_count = 0
    reason_counts: dict[str, int] = {}
    rescue_counts: dict[str, int] = {}
    quarantined_count = 0
    removed_count = 0
    rescued_count = 0
    append_dino_prefilter_log(
        paths,
        "dino_prefilter.start",
        enabled=settings.normalized().enabled,
        mode=settings.normalized().mode.value,
        settings_cache_key=settings.normalized().cache_key(),
        cache_hit=cache_hit,
    )
    signals_by_path: dict[str, DINOPrefilterSignals] = {}
    for row in rows:
        signals = dino_prefilter_signals_from_signal_row(row)
        if signals is None:
            continue
        existing = signals_by_path.get(signals.path)
        signals_by_path[signals.path] = signals if existing is None else _merge_prefilter_signals(existing, signals)
    for signals in signals_by_path.values():
        scanned_count += 1
        decision = decide_dino_prefilter_action(signals, settings)
        decisions.append(decision)
        if decision.reason and decision.action != "pass":
            reason_counts[decision.reason] = reason_counts.get(decision.reason, 0) + 1
        for rescue_reason in decision.rescue_reasons:
            rescue_counts[rescue_reason] = rescue_counts.get(rescue_reason, 0) + 1
        if decision.action == "quarantine":
            quarantined_count += 1
        elif decision.action == "remove_from_pool":
            removed_count += 1
        elif decision.action == "rescued":
            rescued_count += 1
    write_dino_prefilter_audit(
        paths,
        settings=settings,
        rows=(decision.to_row() for decision in decisions),
        scanned_count=scanned_count,
        quarantined_count=quarantined_count,
        removed_from_pool_count=removed_count,
        rescued_count=rescued_count,
        reason_counts=reason_counts,
        rescue_counts=rescue_counts,
        cache_hit=cache_hit,
    )
    append_dino_prefilter_log(
        paths,
        "dino_prefilter.finished",
        scanned=scanned_count,
        quarantined=quarantined_count,
        removed_from_pool=removed_count,
        rescued=rescued_count,
    )
    return {decision.path: decision for decision in decisions}


def _merge_prefilter_signals(left: DINOPrefilterSignals, right: DINOPrefilterSignals) -> DINOPrefilterSignals:
    return DINOPrefilterSignals(
        path=left.path,
        technical_trash_score=max(left.technical_trash_score, right.technical_trash_score),
        duplicate_trash_score=max(left.duplicate_trash_score, right.duplicate_trash_score),
        phash_duplicate_trash_score=max(left.phash_duplicate_trash_score, right.phash_duplicate_trash_score),
        low_information_score=max(left.low_information_score, right.low_information_score),
        ai_high_score=left.ai_high_score or right.ai_high_score,
        user_keep=left.user_keep or right.user_keep,
        semantic_unique=left.semantic_unique or right.semantic_unique,
        best_representative=left.best_representative and right.best_representative,
    )


def build_dino_prefilter_report_payload(
    *,
    settings: DINOPrefilterSettings,
    scanned_count: int,
    quarantined_count: int = 0,
    removed_from_pool_count: int = 0,
    rescued_count: int = 0,
    reason_counts: dict[str, int] | None = None,
    rescue_counts: dict[str, int] | None = None,
    cache_hit: bool = False,
    rows_path: str = "",
    log_path: str = "",
) -> dict[str, object]:
    normalized = settings.normalized()
    return {
        "schema_version": DINO_PREFILTER_SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "model_policy": "base_model_only",
        "settings": normalized.to_cache_payload(),
        "settings_cache_key": normalized.cache_key(),
        "cache_hit": bool(cache_hit),
        "counts": {
            "scanned": max(0, int(scanned_count)),
            "quarantined": max(0, int(quarantined_count)),
            "removed_from_pool": max(0, int(removed_from_pool_count)),
            "rescued": max(0, int(rescued_count)),
        },
        "reason_counts": dict(sorted((reason_counts or {}).items())),
        "rescue_counts": dict(sorted((rescue_counts or {}).items())),
        "artifacts": {
            "rows": rows_path,
            "log": log_path,
        },
    }


def write_dino_prefilter_audit(
    paths: DINOPrefilterPaths,
    *,
    settings: DINOPrefilterSettings,
    rows: Iterable[dict[str, object]] = (),
    scanned_count: int = 0,
    quarantined_count: int = 0,
    removed_from_pool_count: int = 0,
    rescued_count: int = 0,
    reason_counts: dict[str, int] | None = None,
    rescue_counts: dict[str, int] | None = None,
    cache_hit: bool = False,
) -> dict[str, object]:
    paths.ensure()
    with paths.rows_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n")
    payload = build_dino_prefilter_report_payload(
        settings=settings,
        scanned_count=scanned_count,
        quarantined_count=quarantined_count,
        removed_from_pool_count=removed_from_pool_count,
        rescued_count=rescued_count,
        reason_counts=reason_counts,
        rescue_counts=rescue_counts,
        cache_hit=cache_hit,
        rows_path=str(paths.rows_path),
        log_path=str(paths.log_path),
    )
    paths.report_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return payload


def load_dino_prefilter_decisions(paths: DINOPrefilterPaths) -> dict[str, DINOPrefilterDecision]:
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
            reason=str(row.get("reason") or ""),
            score=_clamped_score(row.get("score")),
            rescue_reasons=tuple(str(reason) for reason in rescue_reasons if str(reason).strip()),
        )
    return decisions


def append_dino_prefilter_log(paths: DINOPrefilterPaths, event: str, **fields: object) -> None:
    paths.ensure()
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    with paths.log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")) + "\n")


def dino_prefilter_mode_label(mode: DINOPrefilterMode | str) -> str:
    resolved = coerce_dino_prefilter_mode(mode)
    if resolved == DINOPrefilterMode.POOL_REMOVAL:
        return "Pool Removal"
    return "Soft Quarantine"


def coerce_dino_prefilter_mode(value: object) -> DINOPrefilterMode:
    if isinstance(value, DINOPrefilterMode):
        return value
    try:
        return DINOPrefilterMode(str(value or ""))
    except ValueError:
        return DINOPrefilterMode.SOFT_QUARANTINE


def default_dino_prefilter_settings() -> DINOPrefilterSettings:
    return DINOPrefilterSettings()


def _clamped_score(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, parsed))


def _optional_score(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return _clamped_score(text)


def _bool_value(value: object, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    if not text:
        return default
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _int_value(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _low_information_score(row: dict[str, object]) -> float:
    clutter = _optional_score(row.get("clutter"))
    composition = _optional_score(row.get("composition"))
    subject_confidence = _optional_score(row.get("subject_confidence"))
    candidates: list[float] = []
    if clutter is not None:
        candidates.append(clutter)
    if composition is not None:
        candidates.append(1.0 - composition)
    if subject_confidence is not None:
        candidates.append(1.0 - subject_confidence)
    return max(candidates) if candidates else 0.0
