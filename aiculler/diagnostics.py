from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Sequence

from aiculler.metrics import (
    CullingMetricRecord,
    MAYBE_LABELS,
    compute_culling_metrics,
    is_keeper_label,
    is_reject_label,
)
from aiculler.storage import SQLiteFeatureStore


FEATURE_NAMES = (
    "technical_score",
    "prompt_score",
    "learned_user_score",
    "profile_score",
    "tag_base_score",
    "tag_penalty",
    "final_score",
    "base_score",
    "adapter_score",
    "adapter_final_score",
)

WINNER_LABELS = {"hero", "portfolio"}
TOP_IMAGE_COUNTS = (20, 50)
TOP_PERCENT_FRACTIONS = (0.10, 0.20, 0.30)


def adapter_feasibility_report(
    store: SQLiteFeatureStore,
    model_version: str,
    *,
    folder_root: str | Path | None = None,
) -> dict[str, object]:
    """Build a read-only report describing whether adapter data can generalize."""

    rows = _load_labeled_rows(store, model_version)
    scored_rows = _load_scored_rows(store, model_version)
    duplicate_rejects = _duplicate_reject_inference(rows, folder_root=folder_root)
    feasibility = _feasibility_section(rows, duplicate_rejects)
    correlations = _feature_correlations(rows)
    base_vs_adapter = _base_vs_adapter(rows)
    winner_metrics = _winner_metrics(rows, scored_rows)
    health = _health_section(feasibility, base_vs_adapter, correlations)
    return {
        "schema_version": 1,
        "model_version": str(model_version),
        "feasibility": feasibility,
        "duplicate_reject_inference": duplicate_rejects,
        "feature_correlations": correlations,
        "base_vs_adapter": base_vs_adapter,
        "winner_metrics": winner_metrics,
        "health": health,
    }


def _load_labeled_rows(store: SQLiteFeatureStore, model_version: str) -> list[dict[str, object]]:
    config = _adapter_model_config(store, model_version)
    base_weight = _float_or(config.get("base_weight"), 0.0)
    adapter_weight = _float_or(config.get("adapter_weight"), 1.0)
    with store.lock:
        sql_rows = store.connection.execute(
            """
            SELECT
                ratings.id AS rating_id,
                ratings.image_id AS image_id,
                ratings.label AS label,
                ratings.numeric_score AS numeric_score,
                ratings.primary_category AS rating_primary_category,
                ratings.cluster_id AS rating_cluster_id,
                ratings.label_origin AS label_origin,
                ratings.metadata_json AS rating_metadata_json,
                images.source_path AS source_path,
                images.technical_score AS technical_score,
                images.prompt_score AS prompt_score,
                images.learned_user_score AS learned_user_score,
                images.profile_score AS profile_score,
                images.tag_base_score AS tag_base_score,
                images.tag_penalty AS tag_penalty,
                images.final_score AS final_score,
                images.metadata_json AS image_metadata_json,
                adapter_scores.adapter_score AS adapter_score,
                adapter_scores.confidence AS adapter_confidence,
                adapter_scores.primary_category AS adapter_primary_category,
                adapter_scores.cluster_id AS adapter_cluster_id
            FROM ratings
            INNER JOIN images ON images.id = ratings.image_id
            LEFT JOIN adapter_scores
                ON adapter_scores.image_id = ratings.image_id
                AND adapter_scores.model_version = ?
            ORDER BY ratings.created_at ASC, ratings.id ASC
            """,
            (str(model_version),),
        ).fetchall()

    rows: list[dict[str, object]] = []
    for row in sql_rows:
        rating_metadata = _json_object(row["rating_metadata_json"])
        image_metadata = _json_object(row["image_metadata_json"])
        source_path = str(row["source_path"] or "")
        folder_id = str(
            rating_metadata.get("folder_id")
            or rating_metadata.get("folder")
            or image_metadata.get("folder_id")
            or Path(source_path).parent
        )
        base_score = _base_score(row)
        adapter_score = _safe_float(row["adapter_score"])
        adapter_final_score = (
            base_weight * base_score + adapter_weight * adapter_score
            if adapter_score is not None
            else None
        )
        cluster_id = row["adapter_cluster_id"] if row["adapter_cluster_id"] is not None else row["rating_cluster_id"]
        rows.append(
            {
                "rating_id": int(row["rating_id"]),
                "image_id": int(row["image_id"]),
                "label": str(row["label"] or ""),
                "numeric_score": float(row["numeric_score"]),
                "label_origin": str(row["label_origin"] or "legacy"),
                "source_path": source_path,
                "folder_id": folder_id,
                "reason_tags": tuple(str(value) for value in rating_metadata.get("reason_tags", ()) if str(value).strip())
                if isinstance(rating_metadata.get("reason_tags"), list)
                else (),
                "primary_category": row["adapter_primary_category"] or row["rating_primary_category"],
                "cluster_id": int(cluster_id) if cluster_id is not None else None,
                "technical_score": _safe_float(row["technical_score"]),
                "prompt_score": _safe_float(row["prompt_score"]),
                "learned_user_score": _safe_float(row["learned_user_score"]),
                "profile_score": _safe_float(row["profile_score"]),
                "tag_base_score": _safe_float(row["tag_base_score"]),
                "tag_penalty": _safe_float(row["tag_penalty"]),
                "final_score": _safe_float(row["final_score"]),
                "base_score": base_score,
                "adapter_score": adapter_score,
                "adapter_final_score": adapter_final_score,
                "adapter_confidence": _safe_float(row["adapter_confidence"]),
            }
        )
    return rows


def _adapter_model_config(store: SQLiteFeatureStore, model_version: str) -> dict[str, object]:
    with store.lock:
        row = store.connection.execute(
            "SELECT training_config_json FROM adapter_models WHERE model_version = ?",
            (str(model_version),),
        ).fetchone()
    if row is None:
        return {}
    return _json_object(row["training_config_json"])


def _load_scored_rows(store: SQLiteFeatureStore, model_version: str) -> list[dict[str, object]]:
    config = _adapter_model_config(store, model_version)
    base_weight = _float_or(config.get("base_weight"), 0.0)
    adapter_weight = _float_or(config.get("adapter_weight"), 1.0)
    with store.lock:
        sql_rows = store.connection.execute(
            """
            SELECT
                images.id AS image_id,
                images.source_path AS source_path,
                images.technical_score AS technical_score,
                images.final_score AS final_score,
                images.metadata_json AS image_metadata_json,
                adapter_scores.adapter_score AS adapter_score
            FROM images
            LEFT JOIN adapter_scores
                ON adapter_scores.image_id = images.id
                AND adapter_scores.model_version = ?
            ORDER BY images.id ASC
            """,
            (str(model_version),),
        ).fetchall()
    rows: list[dict[str, object]] = []
    for row in sql_rows:
        image_metadata = _json_object(row["image_metadata_json"])
        source_path = str(row["source_path"] or "")
        base_score = _base_score(row)
        adapter_score = _safe_float(row["adapter_score"])
        adapter_final_score = (
            base_weight * base_score + adapter_weight * adapter_score
            if adapter_score is not None
            else None
        )
        rows.append(
            {
                "image_id": int(row["image_id"]),
                "source_path": source_path,
                "folder_id": str(image_metadata.get("folder_id") or Path(source_path).parent),
                "technical_score": _safe_float(row["technical_score"]),
                "final_score": _safe_float(row["final_score"]),
                "base_score": base_score,
                "adapter_score": adapter_score,
                "adapter_final_score": adapter_final_score,
            }
        )
    return rows


def _base_score(row: object) -> float:
    final_score = _safe_float(row["final_score"])
    if final_score is not None:
        return final_score
    technical_score = _safe_float(row["technical_score"])
    if technical_score is not None:
        return technical_score
    return 0.0


def _feasibility_section(rows: Sequence[dict[str, object]], duplicate_rejects: dict[str, object]) -> dict[str, object]:
    labels = Counter(_label_text(row) for row in rows)
    reason_tag_counts = Counter(
        str(tag)
        for row in rows
        for tag in (row.get("reason_tags") if isinstance(row.get("reason_tags"), tuple) else ())
        if str(tag).strip()
    )
    folders: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        folders[str(row.get("folder_id") or "")].append(row)
    keeper_count = sum(1 for row in rows if is_keeper_label(_label_text(row)))
    reject_count = sum(1 for row in rows if is_reject_label(_label_text(row)))
    maybe_count = sum(1 for row in rows if _label_text(row) in MAYBE_LABELS)
    inferred_duplicate_reject_count = int(duplicate_rejects.get("inferred_duplicate_reject_count") or 0)
    total_labels = len(rows)
    effective_labels = max(0, total_labels - inferred_duplicate_reject_count)
    balance_denominator = keeper_count + reject_count
    label_balance = None
    if balance_denominator > 0:
        label_balance = {
            "keeper_fraction": _round(keeper_count / balance_denominator),
            "reject_fraction": _round(reject_count / balance_denominator),
        }
    return {
        "folder_count": len(folders),
        "total_label_count": total_labels,
        "effective_label_count": effective_labels,
        "keeper_count": keeper_count,
        "reject_count": reject_count,
        "maybe_count": maybe_count,
        "label_counts": dict(sorted(labels.items())),
        "reason_tag_counts": dict(sorted(reason_tag_counts.items())),
        "reason_tagged_label_count": sum(1 for row in rows if row.get("reason_tags")),
        "label_balance": label_balance,
        "labels_per_folder": {
            folder_id: {
                "count": len(folder_rows),
                "keeper_count": sum(1 for row in folder_rows if is_keeper_label(_label_text(row))),
                "reject_count": sum(1 for row in folder_rows if is_reject_label(_label_text(row))),
                "maybe_count": sum(1 for row in folder_rows if _label_text(row) in MAYBE_LABELS),
            }
            for folder_id, folder_rows in sorted(folders.items())
        },
        "adapter_scored_label_count": sum(1 for row in rows if row.get("adapter_score") is not None),
        "inferred_duplicate_reject_count": inferred_duplicate_reject_count,
    }


def _duplicate_reject_inference(
    rows: Sequence[dict[str, object]],
    *,
    folder_root: str | Path | None,
) -> dict[str, object]:
    total_rejects = sum(1 for row in rows if is_reject_label(_label_text(row)))
    phash_data = _load_phash_hashes(folder_root)
    if not phash_data["available"]:
        return {
            "available": False,
            "method": "phash",
            "reason": phash_data["reason"],
            "folder_root": str(Path(folder_root)) if folder_root is not None else None,
            "total_reject_count": total_rejects,
            "inferred_duplicate_reject_count": 0,
            "duplicate_reject_fraction": None,
            "matched_labeled_image_count": 0,
            "phash_group_count": 0,
            "excluded_image_ids": [],
        }

    hash_by_image_id: dict[int, int] = {}
    for row in rows:
        phash = _lookup_phash(row, phash_data)
        if phash is not None:
            hash_by_image_id[int(row["image_id"])] = phash
    groups = _phash_groups(rows, hash_by_image_id, int(phash_data["hamming_threshold"]))
    excluded_ids: list[int] = []
    for group_rows in groups:
        keeper_rows = [row for row in group_rows if is_keeper_label(_label_text(row))]
        reject_rows = [row for row in group_rows if is_reject_label(_label_text(row))]
        if not keeper_rows or not reject_rows:
            continue
        best_keeper_score = max(_diagnostic_rank_score(row) for row in keeper_rows)
        for row in reject_rows:
            if best_keeper_score > _diagnostic_rank_score(row):
                excluded_ids.append(int(row["image_id"]))
    duplicate_count = len(excluded_ids)
    return {
        "available": True,
        "method": "phash",
        "reason": None,
        "folder_root": str(Path(folder_root)) if folder_root is not None else None,
        "hamming_threshold": int(phash_data["hamming_threshold"]),
        "total_reject_count": total_rejects,
        "inferred_duplicate_reject_count": duplicate_count,
        "duplicate_reject_fraction": _round(duplicate_count / total_rejects) if total_rejects else None,
        "matched_labeled_image_count": len(hash_by_image_id),
        "phash_group_count": len(groups),
        "excluded_image_ids": sorted(excluded_ids),
    }


def _load_phash_hashes(folder_root: str | Path | None) -> dict[str, object]:
    if folder_root is None:
        return {"available": False, "reason": "folder_root was not provided"}
    root = Path(folder_root)
    artifact_dir = root / ".image_triage_ai" / "phash_prefilter"
    cache_path = artifact_dir / "phash_cache.json"
    report_path = artifact_dir / "phash_prefilter_report.json"
    if not cache_path.exists():
        return {"available": False, "reason": f"pHash cache not found at {cache_path}"}
    payload = _json_object(cache_path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {"available": False, "reason": "pHash cache does not contain entries"}
    by_path: dict[str, int] = {}
    by_parent_stem: dict[str, int | None] = {}
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        phash = entry.get("hash")
        if not isinstance(phash, int):
            continue
        path_key = _path_key(str(key))
        by_path[path_key] = int(phash)
        parent_stem_key = _parent_stem_key(str(key))
        if parent_stem_key:
            if parent_stem_key in by_parent_stem and by_parent_stem[parent_stem_key] != int(phash):
                by_parent_stem[parent_stem_key] = None
            else:
                by_parent_stem[parent_stem_key] = int(phash)
    if not by_path:
        return {"available": False, "reason": "pHash cache contains no usable hashes"}
    threshold = 6
    if report_path.exists():
        report = _json_object(report_path.read_text(encoding="utf-8"))
        settings = report.get("settings") if isinstance(report.get("settings"), dict) else {}
        threshold = int(_float_or(settings.get("hamming_threshold"), threshold))
    return {
        "available": True,
        "reason": None,
        "by_path": by_path,
        "by_parent_stem": {key: value for key, value in by_parent_stem.items() if value is not None},
        "hamming_threshold": max(0, min(64, threshold)),
    }


def _lookup_phash(row: dict[str, object], phash_data: dict[str, object]) -> int | None:
    source_path = str(row.get("source_path") or "")
    by_path = phash_data.get("by_path") if isinstance(phash_data.get("by_path"), dict) else {}
    by_parent_stem = phash_data.get("by_parent_stem") if isinstance(phash_data.get("by_parent_stem"), dict) else {}
    value = by_path.get(_path_key(source_path))
    if isinstance(value, int):
        return value
    value = by_parent_stem.get(_parent_stem_key(source_path))
    return int(value) if isinstance(value, int) else None


def _phash_groups(
    rows: Sequence[dict[str, object]],
    hash_by_image_id: dict[int, int],
    threshold: int,
) -> list[list[dict[str, object]]]:
    matched = [row for row in rows if int(row["image_id"]) in hash_by_image_id]
    if len(matched) < 2:
        return []
    parent = list(range(len(matched)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for left_index, left_row in enumerate(matched):
        left_hash = hash_by_image_id[int(left_row["image_id"])]
        for right_index in range(left_index + 1, len(matched)):
            right_hash = hash_by_image_id[int(matched[right_index]["image_id"])]
            if _hamming_distance(left_hash, right_hash) <= threshold:
                union(left_index, right_index)
    grouped: dict[int, list[dict[str, object]]] = {}
    for index, row in enumerate(matched):
        grouped.setdefault(find(index), []).append(row)
    return [group_rows for group_rows in grouped.values() if len(group_rows) >= 2]


def _diagnostic_rank_score(row: dict[str, object]) -> float:
    adapter_final = _safe_float(row.get("adapter_final_score"))
    if adapter_final is not None:
        return adapter_final
    return _float_or(row.get("base_score"), 0.0)


def _hamming_distance(left: int, right: int) -> int:
    return bin(int(left) ^ int(right)).count("1")


def _path_key(value: str) -> str:
    try:
        return str(Path(value).expanduser().resolve()).casefold()
    except OSError:
        return str(Path(value).expanduser()).casefold()


def _parent_stem_key(value: str) -> str:
    path = Path(value)
    stem = path.stem.strip().casefold()
    if not stem:
        return ""
    return f"{_path_key(str(path.parent))}::{stem}"


def _feature_correlations(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    enriched = _with_folder_relative_features(rows)
    names = list(FEATURE_NAMES)
    for name in FEATURE_NAMES:
        names.append(f"{name}_folder_zscore")
        names.append(f"{name}_folder_percentile")
    features: dict[str, object] = {}
    for feature in names:
        summary = _feature_summary(enriched, feature)
        if summary["global_label_corr"] is not None or summary["folder_corr_count"]:
            features[feature] = summary
    return {
        "feature_count": len(features),
        "features": features,
    }


def _with_folder_relative_features(rows: Sequence[dict[str, object]]) -> list[dict[str, object]]:
    enriched = [dict(row) for row in rows]
    by_folder: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in enriched:
        by_folder[str(row.get("folder_id") or "")].append(row)
    for folder_rows in by_folder.values():
        for feature in FEATURE_NAMES:
            values = [
                (index, _safe_float(row.get(feature)))
                for index, row in enumerate(folder_rows)
                if _safe_float(row.get(feature)) is not None
            ]
            if len(values) < 2:
                continue
            numeric = [float(value) for _index, value in values]
            mean = sum(numeric) / len(numeric)
            variance = sum((value - mean) ** 2 for value in numeric) / len(numeric)
            std = math.sqrt(variance)
            sorted_values = sorted((value, index) for index, value in values)
            percentile_by_index: dict[int, float] = {}
            if len(sorted_values) > 1:
                position = 0
                while position < len(sorted_values):
                    end = position + 1
                    while end < len(sorted_values) and sorted_values[end][0] == sorted_values[position][0]:
                        end += 1
                    percentile = ((position + end - 1) / 2.0) / (len(sorted_values) - 1)
                    for _value, original_index in sorted_values[position:end]:
                        percentile_by_index[original_index] = percentile
                    position = end
            for index, value in values:
                row = folder_rows[index]
                if std > 0.0:
                    row[f"{feature}_folder_zscore"] = (float(value) - mean) / std
                if index in percentile_by_index:
                    row[f"{feature}_folder_percentile"] = percentile_by_index[index]
    return enriched


def _feature_summary(rows: Sequence[dict[str, object]], feature: str) -> dict[str, object]:
    global_pairs = _correlation_pairs(rows, feature)
    global_label_corr = _pearson([value for value, _label, _keeper in global_pairs], [label for _value, label, _keeper in global_pairs])
    global_keeper_corr = _pearson([value for value, _label, _keeper in global_pairs], [keeper for _value, _label, keeper in global_pairs])

    per_folder = []
    by_folder: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        by_folder[str(row.get("folder_id") or "")].append(row)
    for folder_id, folder_rows in sorted(by_folder.items()):
        pairs = _correlation_pairs(folder_rows, feature)
        corr = _pearson([value for value, _label, _keeper in pairs], [label for _value, label, _keeper in pairs])
        if corr is None:
            continue
        per_folder.append({"folder_id": folder_id, "count": len(pairs), "label_corr": _round(corr)})

    folder_corrs = [float(item["label_corr"]) for item in per_folder]
    folder_mean = sum(folder_corrs) / len(folder_corrs) if folder_corrs else None
    folder_std = _std(folder_corrs) if len(folder_corrs) >= 2 else None
    sign_flips = 0
    if global_label_corr is not None and abs(global_label_corr) >= 0.1:
        global_sign = 1 if global_label_corr > 0 else -1
        sign_flips = sum(
            1
            for value in folder_corrs
            if abs(value) >= 0.1 and (1 if value > 0 else -1) != global_sign
        )
    if len(folder_corrs) < 2:
        stability_state = "undetermined"
        stability_reason = "fewer than 2 folders with computable correlations"
    elif sign_flips == 0 and (folder_std is not None and folder_std <= 0.30):
        stability_state = "stable"
        stability_reason = None
    else:
        stability_state = "unstable"
        stability_reason = "per-folder correlations vary or flip sign"
    is_stable = stability_state == "stable"
    return {
        "global_label_corr": _round(global_label_corr),
        "global_keeper_corr": _round(global_keeper_corr),
        "folder_corr_count": len(folder_corrs),
        "folder_corr_mean": _round(folder_mean),
        "folder_corr_std": _round(folder_std),
        "sign_flips": sign_flips,
        "stability_state": stability_state,
        "stability_reason": stability_reason,
        "is_stable": is_stable,
        "per_folder": per_folder,
    }


def _correlation_pairs(rows: Sequence[dict[str, object]], feature: str) -> list[tuple[float, float, float]]:
    pairs = []
    for row in rows:
        value = _safe_float(row.get(feature))
        if value is None:
            continue
        label = _safe_float(row.get("numeric_score"))
        if label is None:
            continue
        pairs.append((value, label, 1.0 if is_keeper_label(_label_text(row)) else 0.0))
    if len(pairs) < 3:
        return []
    feature_values = {value for value, _label, _keeper in pairs}
    label_values = {label for _value, label, _keeper in pairs}
    if len(feature_values) < 2 or len(label_values) < 2:
        return []
    return pairs


def _base_vs_adapter(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    base_records = _metric_records(rows, "base_score")
    adapter_records = _metric_records(rows, "adapter_final_score")
    base = _select_metric_keys(compute_culling_metrics(base_records))
    adapter = _select_metric_keys(compute_culling_metrics(adapter_records))
    folder_count = len({str(row.get("folder_id") or "") for row in rows})
    return {
        "evaluation_scope": "in_sample",
        "generalization_claim": "unmeasurable" if folder_count < 3 else "limited",
        "caveat": (
            "Base-vs-adapter metrics are computed against stored labels for this model, not a leave-one-folder-out retrain. "
            "Use them as fit diagnostics, not proof of cross-folder generalization."
        ),
        "overall": {
            "base": base,
            "adapter": adapter,
            "delta": _metric_delta(base, adapter),
        },
        "per_folder": _per_folder_base_vs_adapter(rows),
    }


def _winner_metrics(
    labeled_rows: Sequence[dict[str, object]],
    scored_rows: Sequence[dict[str, object]],
) -> dict[str, object]:
    winner_rows = [row for row in labeled_rows if _is_winner_label(_label_text(row))]
    keeper_rows = [row for row in labeled_rows if is_keeper_label(_label_text(row))]
    winner_count = len(winner_rows)
    keeper_count = len(keeper_rows)
    return {
        "evaluation_scope": "known_labels_in_scored_pool",
        "caveat": (
            "Winner metrics only evaluate images that already have labels. Unlabeled images in the top-N may still be true winners."
        ),
        "winner_labels": sorted(WINNER_LABELS),
        "winner_count": winner_count,
        "keeper_count": keeper_count,
        "sample_warning": _winner_sample_warning(winner_count),
        "random": {
            "winner": _target_random_metrics(winner_count, len(labeled_rows), _scored_count(scored_rows, "base_score")),
            "keeper": _target_random_metrics(keeper_count, len(labeled_rows), _scored_count(scored_rows, "base_score")),
        },
        "base": {
            "winner": _target_metrics(labeled_rows, scored_rows, "base_score", target="winner"),
            "keeper": _target_metrics(labeled_rows, scored_rows, "base_score", target="keeper"),
        },
        "adapter": {
            "winner": _target_metrics(labeled_rows, scored_rows, "adapter_final_score", target="winner"),
            "keeper": _target_metrics(labeled_rows, scored_rows, "adapter_final_score", target="keeper"),
        },
    }


def _winner_sample_warning(winner_count: int) -> dict[str, object]:
    if winner_count < 3:
        state = "too_few_to_measure"
        reason = "fewer than 3 winner labels; winner metrics are highly unstable"
    elif winner_count < 10:
        state = "very_noisy"
        reason = "fewer than 10 winner labels; use winner metrics as directional only"
    elif winner_count < 30:
        state = "limited"
        reason = "fewer than 30 winner labels; do not optimize model changes on this alone"
    else:
        state = "usable"
        reason = None
    return {"state": state, "reason": reason}


def _target_random_metrics(target_count: int, labeled_pool_count: int, scored_pool_count: int) -> dict[str, object]:
    metrics: dict[str, object] = {"target_count": target_count}
    for fraction in TOP_PERCENT_FRACTIONS:
        metrics[f"top_{int(fraction * 100)}_percent_recall"] = _round(fraction) if target_count and labeled_pool_count else None
    for top_n in TOP_IMAGE_COUNTS:
        metrics[f"top_{top_n}_image_hit_rate"] = (
            _round(min(1.0, top_n / scored_pool_count)) if target_count and scored_pool_count else None
        )
    return metrics


def _target_metrics(
    labeled_rows: Sequence[dict[str, object]],
    scored_rows: Sequence[dict[str, object]],
    score_key: str,
    *,
    target: str,
) -> dict[str, object]:
    target_ids = {
        int(row["image_id"])
        for row in labeled_rows
        if (_is_winner_label(_label_text(row)) if target == "winner" else is_keeper_label(_label_text(row)))
    }
    metrics: dict[str, object] = {
        "target_count": len(target_ids),
        "labeled_pool_count": len(labeled_rows),
        "scored_pool_count": _scored_count(scored_rows, score_key),
    }
    labeled_ranked = _rank_rows(labeled_rows, score_key)
    for fraction in TOP_PERCENT_FRACTIONS:
        metrics[f"top_{int(fraction * 100)}_percent_recall"] = _target_recall_in_ranked(
            target_ids,
            labeled_ranked,
            max(1, int(math.ceil(len(labeled_ranked) * fraction))) if labeled_ranked else 0,
        )
    scored_ranked = _rank_rows(scored_rows, score_key)
    for top_n in TOP_IMAGE_COUNTS:
        metrics[f"top_{top_n}_image_hit_rate"] = _target_recall_in_ranked(target_ids, scored_ranked, top_n)
        metrics[f"top_{top_n}_image_hits"] = _target_hits_in_ranked(target_ids, scored_ranked, top_n)
    return metrics


def _rank_rows(rows: Sequence[dict[str, object]], score_key: str) -> list[dict[str, object]]:
    scored = [row for row in rows if _safe_float(row.get(score_key)) is not None]
    return sorted(scored, key=lambda row: (-float(_safe_float(row.get(score_key)) or 0.0), int(row["image_id"])))


def _target_recall_in_ranked(target_ids: set[int], ranked_rows: Sequence[dict[str, object]], cutoff: int) -> float | None:
    if not target_ids or not ranked_rows or cutoff <= 0:
        return None
    hits = _target_hits_in_ranked(target_ids, ranked_rows, cutoff)
    return _round(hits / len(target_ids))


def _target_hits_in_ranked(target_ids: set[int], ranked_rows: Sequence[dict[str, object]], cutoff: int) -> int:
    if not target_ids or cutoff <= 0:
        return 0
    top_ids = {int(row["image_id"]) for row in ranked_rows[: max(0, int(cutoff))]}
    return len(target_ids.intersection(top_ids))


def _scored_count(rows: Sequence[dict[str, object]], score_key: str) -> int:
    return sum(1 for row in rows if _safe_float(row.get(score_key)) is not None)


def _per_folder_base_vs_adapter(rows: Sequence[dict[str, object]]) -> dict[str, object]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get("folder_id") or "")].append(row)
    result: dict[str, object] = {}
    for folder_id, folder_rows in sorted(grouped.items()):
        base = _select_metric_keys(compute_culling_metrics(_metric_records(folder_rows, "base_score")))
        adapter = _select_metric_keys(compute_culling_metrics(_metric_records(folder_rows, "adapter_final_score")))
        result[folder_id] = {
            "count": len(folder_rows),
            "base": base,
            "adapter": adapter,
            "delta": _metric_delta(base, adapter),
        }
    return result


def _metric_records(rows: Sequence[dict[str, object]], score_key: str) -> list[CullingMetricRecord]:
    records = []
    for row in rows:
        score = _safe_float(row.get(score_key))
        if score is None:
            continue
        records.append(
            CullingMetricRecord(
                image_id=int(row["image_id"]),
                label=_label_text(row),
                score=score,
                cluster_id=int(row["cluster_id"]) if row.get("cluster_id") is not None else None,
                folder_id=str(row.get("folder_id") or ""),
            )
        )
    return records


def _select_metric_keys(metrics: dict[str, object]) -> dict[str, object]:
    keys = ("top_10_recall", "top_20_recall", "top_30_recall", "false_reject_rate", "rank_correlation")
    return {key: _round(_safe_float(metrics.get(key))) for key in keys}


def _metric_delta(base: dict[str, object], adapter: dict[str, object]) -> dict[str, object]:
    result = {}
    for key in ("top_10_recall", "top_20_recall", "top_30_recall", "false_reject_rate", "rank_correlation"):
        base_value = _safe_float(base.get(key))
        adapter_value = _safe_float(adapter.get(key))
        result[f"{key}_delta"] = _round(adapter_value - base_value) if base_value is not None and adapter_value is not None else None
    return result


def _health_section(
    feasibility: dict[str, object],
    base_vs_adapter: dict[str, object],
    correlations: dict[str, object],
) -> dict[str, object]:
    reasons: list[str] = []
    folder_count = int(feasibility.get("folder_count") or 0)
    effective_label_count = int(feasibility.get("effective_label_count") or 0)
    keeper_count = int(feasibility.get("keeper_count") or 0)
    reject_count = int(feasibility.get("reject_count") or 0)
    if folder_count < 3:
        reasons.append("fewer than 3 labeled folders")
    if effective_label_count < 50:
        reasons.append("fewer than 50 effective labels")
    if keeper_count < 5:
        reasons.append("too few keeper examples")
    if reject_count < 5:
        reasons.append("too few reject examples")
    if reasons:
        return {
            "state": "insufficient_data",
            "confidence": "low",
            "reasons": reasons,
            "recommendation": "Collect more diverse folder-level labels before trusting adapter changes.",
        }

    overall = base_vs_adapter.get("overall") if isinstance(base_vs_adapter.get("overall"), dict) else {}
    delta = overall.get("delta") if isinstance(overall.get("delta"), dict) else {}
    top30_delta = _safe_float(delta.get("top_30_recall_delta"))
    false_reject_delta = _safe_float(delta.get("false_reject_rate_delta"))
    if top30_delta is not None and top30_delta <= -0.05:
        reasons.append("adapter top-30 recall is at least 5 points worse than base")
    if false_reject_delta is not None and false_reject_delta >= 0.05:
        reasons.append("adapter false reject rate is at least 5 points worse than base")
    if reasons:
        return {
            "state": "weak",
            "confidence": _data_confidence(folder_count, effective_label_count, keeper_count, reject_count),
            "reasons": reasons,
            "recommendation": "Keep base ranking primary; use the adapter only for investigation until the data or features improve.",
        }

    consistency = _per_folder_consistency(base_vs_adapter)
    confidence = _data_confidence(folder_count, effective_label_count, keeper_count, reject_count)
    if (
        confidence == "high"
        and top30_delta is not None
        and top30_delta >= 0.05
        and (false_reject_delta is None or false_reject_delta <= 0.0)
        and consistency["is_consistent"]
    ):
        return {
            "state": "healthy",
            "confidence": confidence,
            "reasons": ["adapter improves top-30 recall without worsening false rejects and is consistent across folders"],
            "recommendation": "Adapter can be treated as a ranking signal for this label population.",
            "per_folder_consistency": consistency,
        }

    reasons.append("adapter signal is not strong enough for a healthy verdict")
    if not consistency["is_consistent"]:
        reasons.append("per-folder adapter deltas are inconsistent")
    return {
        "state": "advisory",
        "confidence": confidence,
        "reasons": reasons,
        "recommendation": "Keep base ranking primary and use adapter output as advisory until additional diagnostics or labels support it.",
        "per_folder_consistency": consistency,
    }


def _data_confidence(folder_count: int, label_count: int, keeper_count: int, reject_count: int) -> str:
    if folder_count >= 5 and label_count >= 200 and keeper_count >= 20 and reject_count >= 20:
        return "high"
    if folder_count >= 3 and label_count >= 50 and keeper_count >= 5 and reject_count >= 5:
        return "medium"
    return "low"


def _per_folder_consistency(base_vs_adapter: dict[str, object]) -> dict[str, object]:
    per_folder = base_vs_adapter.get("per_folder") if isinstance(base_vs_adapter.get("per_folder"), dict) else {}
    deltas = []
    improved = 0
    for value in per_folder.values():
        if not isinstance(value, dict):
            continue
        delta = value.get("delta") if isinstance(value.get("delta"), dict) else {}
        top30_delta = _safe_float(delta.get("top_30_recall_delta"))
        if top30_delta is None:
            continue
        deltas.append(top30_delta)
        if top30_delta >= 0.0:
            improved += 1
    improvement_fraction = improved / len(deltas) if deltas else None
    delta_std = _std(deltas) if len(deltas) >= 2 else None
    is_consistent = bool(deltas and (improvement_fraction or 0.0) >= 0.60 and (delta_std is None or delta_std <= 0.20))
    return {
        "eligible_folder_count": len(deltas),
        "non_worse_folder_fraction": _round(improvement_fraction),
        "top_30_delta_std": _round(delta_std),
        "is_consistent": is_consistent,
    }


def _label_text(row: dict[str, object]) -> str:
    return str(row.get("label") or "").strip().lower()


def _is_winner_label(label: str | None) -> bool:
    return str(label or "").strip().lower() in WINNER_LABELS


def _json_object(value: object) -> dict[str, object]:
    if not value:
        return {}
    try:
        data = json.loads(str(value))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _safe_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def _float_or(value: object, default: float) -> float:
    result = _safe_float(value)
    return float(default) if result is None else result


def _pearson(left: Sequence[float], right: Sequence[float]) -> float | None:
    if len(left) != len(right) or len(left) < 3:
        return None
    if len(set(left)) < 2 or len(set(right)) < 2:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    numerator = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right))
    left_sq = sum((a - mean_left) ** 2 for a in left)
    right_sq = sum((b - mean_right) ** 2 for b in right)
    denominator = math.sqrt(left_sq * right_sq)
    if denominator == 0.0:
        return None
    return numerator / denominator


def _std(values: Sequence[float]) -> float | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _round(value: object) -> float | None:
    number = _safe_float(value)
    if number is None:
        return None
    return round(number, 6)
