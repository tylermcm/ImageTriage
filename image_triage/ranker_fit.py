from __future__ import annotations

"""Training-fit diagnosis helpers for AI ranker runs."""

from dataclasses import dataclass


@dataclass(slots=True, frozen=True)
class RankerFitDiagnosis:
    code: str
    label: str
    summary: str
    remedy: str


def diagnose_ranker_fit(
    *,
    metrics: dict[str, object] | None = None,
    history_rows: list[dict[str, object]] | None = None,
    num_epochs: int | None = None,
) -> RankerFitDiagnosis:
    """Summarize training health in plain language for non-ML users."""
    metrics = metrics or {}
    history = history_rows or []
    best_epoch = _nested_int(metrics, "best_epoch")
    best_validation_accuracy = _nested_float(metrics, "best_validation_pairwise_accuracy")
    best_validation_loss = _nested_float(metrics, "best_validation_loss")
    final_train_loss = _nested_float(metrics, "final_train_loss")
    final_validation_loss = _nested_float(metrics, "final_validation_loss")
    final_validation_accuracy = _nested_float(metrics, "final_validation_pairwise_accuracy")

    if history:
        last_row = history[-1]
        final_train_loss = final_train_loss if final_train_loss is not None else _coerce_float(last_row.get("train_loss"))
        final_validation_loss = final_validation_loss if final_validation_loss is not None else _coerce_float(last_row.get("validation_loss"))
        final_validation_accuracy = (
            final_validation_accuracy
            if final_validation_accuracy is not None
            else _coerce_float(last_row.get("validation_pairwise_accuracy"))
        )
        if num_epochs is None:
            num_epochs = _coerce_int(last_row.get("epoch")) or len(history)
        if best_epoch is None:
            best_epoch = _best_epoch_from_history(history)
        if best_validation_loss is None:
            best_validation_loss = _history_best_float(history, "validation_loss", prefer="min")
        if best_validation_accuracy is None:
            best_validation_accuracy = _history_best_float(history, "validation_pairwise_accuracy", prefer="max")

    total_epochs = max(0, int(num_epochs or best_epoch or len(history) or 0))
    if best_epoch is None or total_epochs <= 0:
        return RankerFitDiagnosis(
            code="unknown",
            label="Too Early To Tell",
            summary="This run does not have enough validation history yet to judge training health.",
            remedy="Finish training and evaluation, then check again.",
        )

    early_best_threshold = max(2, int(round(total_epochs * 0.45)))
    late_best_threshold = max(1, int(round(total_epochs * 0.8)))
    loss_gap = _positive_gap(final_validation_loss, best_validation_loss)
    accuracy_drop = _positive_gap(best_validation_accuracy, final_validation_accuracy)
    train_validation_gap = _positive_gap(final_validation_loss, final_train_loss)

    if (
        best_epoch <= early_best_threshold
        and (
            _gap_exceeds(loss_gap, baseline=best_validation_loss, floor=0.03, ratio=0.12)
            or _gap_exceeds(accuracy_drop, baseline=best_validation_accuracy, floor=0.03, ratio=0.08)
            or _gap_exceeds(train_validation_gap, baseline=final_train_loss, floor=0.05, ratio=0.25)
        )
    ):
        return RankerFitDiagnosis(
            code="overfit",
            label="May Be Overfit",
            summary="The model peaked early and then got worse on held-out examples, so it may be memorizing the training labels.",
            remedy="Try fewer epochs, add more varied labels, or use a broader profile such as General Use for mixed folders.",
        )

    if (
        best_epoch >= late_best_threshold
        and (best_validation_accuracy is None or best_validation_accuracy < 0.78)
        and not _gap_exceeds(loss_gap, baseline=best_validation_loss, floor=0.02, ratio=0.05)
        and not _gap_exceeds(accuracy_drop, baseline=best_validation_accuracy, floor=0.02, ratio=0.05)
    ):
        return RankerFitDiagnosis(
            code="underfit",
            label="May Be Underfit",
            summary="The model was still improving near the end, so it probably has not learned enough yet.",
            remedy="Try more labels, a few more epochs, or a more focused profile. Use General Use when the folder mixes subjects.",
        )

    return RankerFitDiagnosis(
        code="healthy",
        label="Looks Healthy",
        summary="The model improved and settled without a clear late drop. This run looks reasonably balanced.",
        remedy="If the ranking still misses edge cases, label a few more of those cases and train another version.",
    )


def _nested_float(payload: dict[str, object], *keys: str) -> float | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        if current is None:
            return None
        return float(current)
    except (TypeError, ValueError):
        return None


def _nested_int(payload: dict[str, object], *keys: str) -> int | None:
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    try:
        if current is None:
            return None
        return int(current)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: object) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_int(value: object) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _history_best_float(rows: list[dict[str, object]], key: str, *, prefer: str) -> float | None:
    values = [value for value in (_coerce_float(row.get(key)) for row in rows) if value is not None]
    if not values:
        return None
    return min(values) if prefer == "min" else max(values)


def _best_epoch_from_history(rows: list[dict[str, object]]) -> int | None:
    best_row: dict[str, object] | None = None
    best_loss: float | None = None
    for row in rows:
        candidate_loss = _coerce_float(row.get("validation_loss"))
        if candidate_loss is None:
            continue
        if best_loss is None or candidate_loss < best_loss:
            best_loss = candidate_loss
            best_row = row
    if best_row is not None:
        return _coerce_int(best_row.get("epoch"))
    best_accuracy_row: dict[str, object] | None = None
    best_accuracy: float | None = None
    for row in rows:
        candidate_accuracy = _coerce_float(row.get("validation_pairwise_accuracy"))
        if candidate_accuracy is None:
            continue
        if best_accuracy is None or candidate_accuracy > best_accuracy:
            best_accuracy = candidate_accuracy
            best_accuracy_row = row
    if best_accuracy_row is not None:
        return _coerce_int(best_accuracy_row.get("epoch"))
    return None


def _positive_gap(later_value: float | None, earlier_value: float | None) -> float | None:
    if later_value is None or earlier_value is None:
        return None
    return max(0.0, later_value - earlier_value)


def _gap_exceeds(gap: float | None, *, baseline: float | None, floor: float, ratio: float) -> bool:
    if gap is None:
        return False
    tolerance = max(floor, abs(float(baseline or 0.0)) * ratio)
    return gap > tolerance


__all__ = ["RankerFitDiagnosis", "diagnose_ranker_fit"]
