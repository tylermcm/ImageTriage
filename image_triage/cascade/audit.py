"""Measurement logic for the pass 1 audit (docs/cascade_logic_audit.md, "A sound next measurement"). Pure: no Qt, no files, no models.

Two questions are measured:

* keeper recall: of the frames the owner KEPT, how many does pass 1 flag (per rule, as a union, after the all-flagged fallback, and what
  survives to the end). "Recall" here is the share of keepers that are NOT flagged, so a high number is good.
* false stack merges: how often a stack holds frames that an independent signal (focal length, face size, or a long hash drift) says a person
  would not compare as alternatives.

Raw flags come from ``FrameOutcome.flags`` of ``run_folder`` run WITHOUT winner protection.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Mapping, Sequence

from .flags import is_rejecting
from .model import FLAG_BLINK, FLAG_BLOWN_OUT, FLAG_CLIPPED, FLAG_SOFT, CascadeSettings
from .stacking import hamming

RULES = (FLAG_BLINK, FLAG_SOFT, FLAG_CLIPPED, FLAG_BLOWN_OUT)
RELATIVE_RULES = (FLAG_SOFT, FLAG_CLIPPED)  # judged against siblings, so a frame alone in its stack cannot trigger them
SURVIVING_ROLES = ("pick", "final", "flagged")  # roles whose exact frame is still offered after every pass
ROLE_BRACKET = "bracket"
DRIFT_DIAMETER = 18  # the split threshold: a stack whose ends differ by this much has drifted
FOCAL_RATIO_LIMIT = 1.1
FACE_RATIO_LIMIT = 1.5


# -- exact binomial -----------------------------------------------------------------------------------------------------------------------------


def _log_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 0.0 if k == 0 else -math.inf
    if p >= 1.0:
        return 0.0 if k == n else -math.inf
    return math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1) + k * math.log(p) + (n - k) * math.log1p(-p)


def binomial_cdf(k: int, n: int, p: float) -> float:
    """P(X <= k) for X ~ Binomial(n, p)."""
    if k < 0:
        return 0.0
    if k >= n:
        return 1.0
    return min(1.0, sum(math.exp(_log_pmf(i, n, p)) for i in range(k + 1)))


def clopper_pearson_lower(successes: int, trials: int, confidence: float = 0.95) -> float:
    """One-sided exact lower confidence bound for a proportion: the smallest p for which seeing at least ``successes`` of ``trials`` still has
    probability at least 1 - confidence. 0 trials gives 0.0 (nothing is known)."""
    if trials <= 0:
        return 0.0
    if not 0 <= successes <= trials:
        raise ValueError("successes must be between 0 and trials")
    if successes == 0:
        return 0.0
    alpha = 1.0 - confidence
    if successes == trials:
        return alpha ** (1.0 / trials)
    low, high = 0.0, 1.0
    for _ in range(100):
        mid = (low + high) / 2.0
        upper_tail = 1.0 - binomial_cdf(successes - 1, trials, mid)  # P(X >= successes | mid), increasing in mid
        if upper_tail > alpha:
            high = mid
        else:
            low = mid
    return (low + high) / 2.0


def needed_zero_miss(target: float = 0.995, confidence: float = 0.95) -> int:
    """Fewest independent keepers, all unflagged, for the one-sided lower bound to exceed ``target`` (598 for 99.5% at 95%)."""
    if not 0.0 < target < 1.0:
        raise ValueError("target must be between 0 and 1")
    alpha = 1.0 - confidence
    n = max(1, int(math.log(alpha) / math.log(target)) - 2)
    while alpha ** (1.0 / n) <= target:
        n += 1
    return n


# -- per-frame rows and recall tables ----------------------------------------------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class FrameRow:
    """One decided frame, as ``run_folder`` reported it (before any winner protection)."""

    key: str
    role: str
    flags: tuple[str, ...]
    stack_size: int = 1
    burst_index: int = 0
    stack_index: int = 0
    group: str = ""  # the scanned folder


def rows_from_outcomes(outcomes: Mapping[str, Any], order: Sequence[str], group: str = "") -> list[FrameRow]:
    """FrameRows from a ``FolderResult.outcomes`` mapping, in ``order`` (capture order); frames without an outcome are left out."""
    rows = []
    for key in order:
        outcome = outcomes.get(key)
        if outcome is None:
            continue
        rows.append(FrameRow(key, outcome.role, tuple(outcome.flags), outcome.stack_size, outcome.burst_index, outcome.stack_index, group))
    return rows


@dataclass(slots=True)
class RuleRecall:
    rule: str
    eligible: int = 0  # keepers the rule could have flagged
    flagged: int = 0  # ... that it did flag (the misses)
    misses: list[str] = field(default_factory=list)
    measured: bool = True

    @property
    def kept(self) -> int:
        return self.eligible - self.flagged

    @property
    def recall(self) -> float | None:
        return self.kept / self.eligible if self.eligible else None

    def lower_bound(self, confidence: float = 0.95) -> float | None:
        return clopper_pearson_lower(self.kept, self.eligible, confidence) if self.eligible else None

    def to_dict(self, confidence: float = 0.95) -> dict[str, Any]:
        return {
            "rule": self.rule, "measured": self.measured, "eligible": self.eligible, "flagged": self.flagged, "recall": self.recall,
            "lower_bound": self.lower_bound(confidence), "misses": list(self.misses),
        }


def _rule_applies(rule: str, row: FrameRow, blink_keys: set[str] | None) -> bool:
    if row.role == ROLE_BRACKET:
        return False
    if rule in RELATIVE_RULES:
        return row.stack_size > 1
    if rule == FLAG_BLINK:
        return blink_keys is None or row.key in blink_keys
    return True


def rule_recall(
    rows: Iterable[FrameRow],
    keepers: Iterable[str],
    *,
    blink_measured: bool = True,
    blink_keys: Iterable[str] | None = None,
) -> dict[str, RuleRecall]:
    """Eligible and flagged keeper counts per rule. Soft and clipped need a sibling, so a keeper alone in its stack is not eligible for them.
    Blink is eligible only for keepers with a blink score (all of them when ``blink_keys`` is None); with ``blink_measured`` False it is
    reported as not measured. Brackets have no decision and are not eligible."""
    keeper_set = set(keepers)
    blink_set = set(blink_keys) if blink_keys is not None else None
    table = {rule: RuleRecall(rule, measured=(blink_measured if rule == FLAG_BLINK else True)) for rule in RULES}
    for row in rows:
        if row.key not in keeper_set or row.role == ROLE_BRACKET:
            continue
        for rule in RULES:
            if rule == FLAG_BLINK and not blink_measured:
                continue
            if not _rule_applies(rule, row, blink_set):
                continue
            entry = table[rule]
            entry.eligible += 1
            if rule in row.flags:
                entry.flagged += 1
                entry.misses.append(row.key)
    return table


def keeper_outcomes(rows: Iterable[FrameRow], keepers: Iterable[str]) -> dict[str, Any]:
    """Union of rejecting flags, what pass 1 actually did after the all-flagged fallback, no-pick and final exact-frame survival, over the
    keepers that have a decision. Blown out alone is not rejecting."""
    keeper_set = set(keepers)
    eligible = [r for r in rows if r.key in keeper_set and r.role != ROLE_BRACKET]
    roles: dict[str, int] = {}
    for row in eligible:
        roles[row.role] = roles.get(row.role, 0) + 1
    union = [r for r in eligible if is_rejecting(r.flags)]
    rejected = [r for r in eligible if r.role == "rejected"]
    saved_by_fallback = [r for r in union if r.role != "rejected" and r.role != "no_pick"]
    no_pick = [r for r in eligible if r.role == "no_pick"]
    survived = [r for r in eligible if r.role in SURVIVING_ROLES]
    n = len(eligible)
    return {
        "eligible": n,
        "roles": dict(sorted(roles.items())),
        "union_flagged": len(union),
        "union_misses": [r.key for r in union],
        "rejected": len(rejected),
        "rejected_keys": [r.key for r in rejected],
        "saved_by_all_flagged_fallback": len(saved_by_fallback),
        "saved_by_all_flagged_fallback_keys": [r.key for r in saved_by_fallback],
        "no_pick": len(no_pick),
        "no_pick_keys": [r.key for r in no_pick],
        "survived": len(survived),
        "lost": [r.key for r in eligible if r.role not in SURVIVING_ROLES],
        "union_recall": (n - len(union)) / n if n else None,
        "rejected_recall": (n - len(rejected)) / n if n else None,
        "survival": len(survived) / n if n else None,
    }


def union_lower_bound(outcome: Mapping[str, Any], confidence: float = 0.95) -> float | None:
    n = outcome["eligible"]
    return clopper_pearson_lower(n - outcome["union_flagged"], n, confidence) if n else None


def flag_rates(rows: Sequence[FrameRow]) -> dict[str, Any]:
    """How much of the whole scanned set pass 1 touches: share with a rejecting flag, share actually rejected, per-rule share."""
    decided = [r for r in rows if r.role != ROLE_BRACKET]
    n = len(decided)
    out: dict[str, Any] = {
        "decided": n,
        "flag_rate": (sum(1 for r in decided if is_rejecting(r.flags)) / n) if n else None,
        "reject_rate": (sum(1 for r in decided if r.role == "rejected") / n) if n else None,
    }
    for rule in RULES:
        out[f"{rule}_rate"] = (sum(1 for r in decided if rule in r.flags) / n) if n else None
    return out


# -- threshold sweeps --------------------------------------------------------------------------------------------------------------------------


def split_overrides(settings: CascadeSettings, overrides: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """(overrides this CascadeSettings has a field for, names it does not know). Lets the audit run on a CascadeSettings with fewer fields."""
    known = {name for name in getattr(settings, "__dataclass_fields__", {})}
    return ({k: v for k, v in overrides.items() if k in known}, [k for k in overrides if k not in known])


def sweep_plan(sweep: Mapping[str, Sequence[Any]], *, full_grid: bool = False) -> list[dict[str, Any]]:
    """Override dicts: one field at a time (default), or the full cartesian grid."""
    if not sweep:
        return []
    if full_grid:
        names = list(sweep)
        return [dict(zip(names, values)) for values in itertools.product(*(sweep[n] for n in names))]
    return [{name: value} for name, values in sweep.items() for value in values]


def run_sweep(
    evaluate: Callable[[CascadeSettings], Sequence[FrameRow]],
    base: CascadeSettings,
    sweep: Mapping[str, Sequence[Any]],
    keepers: Iterable[str],
    *,
    full_grid: bool = False,
    blink_measured: bool = True,
    blink_keys: Iterable[str] | None = None,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """Recall and overall flag rate per setting. ``evaluate(settings)`` returns the rows for one run (the baseline is included first)."""
    keeper_set = set(keepers)
    unknown = sorted({name for name in sweep if split_overrides(base, {name: 0})[1]})
    usable = {name: values for name, values in sweep.items() if name not in unknown}
    entries = []
    for overrides in [{}] + sweep_plan(usable, full_grid=full_grid):
        settings = replace(base, **overrides) if overrides else base
        rows = list(evaluate(settings))
        outcome = keeper_outcomes(rows, keeper_set)
        rules = rule_recall(rows, keeper_set, blink_measured=blink_measured, blink_keys=blink_keys)
        entries.append({
            "overrides": dict(overrides),
            "label": ", ".join(f"{k}={v}" for k, v in overrides.items()) or "baseline",
            "eligible": outcome["eligible"],
            "union_recall": outcome["union_recall"],
            "union_lower_bound": union_lower_bound(outcome, confidence),
            "union_flagged": outcome["union_flagged"],
            "survival": outcome["survival"],
            "rule_recall": {rule: rules[rule].recall for rule in RULES},
            **flag_rates(rows),
        })
    return {"entries": entries, "unknown_fields": unknown, "full_grid": full_grid}


# -- stack diagnostics -------------------------------------------------------------------------------------------------------------------------


def stacks_from_rows(rows: Sequence[FrameRow]) -> list[list[str]]:
    """Stack member keys, in the order the rows were given (capture order), for decided non-bracket frames. A stack is one (group, burst, stack)."""
    stacks: dict[tuple[str, int, int], list[str]] = {}
    for row in rows:
        if row.role == ROLE_BRACKET or row.burst_index < 0:
            continue
        stacks.setdefault((row.group, row.burst_index, row.stack_index), []).append(row.key)
    return list(stacks.values())


def _ratio(values: Sequence[float | None]) -> float | None:
    present = [float(v) for v in values if v is not None and v > 0]
    if len(present) < 2:
        return None
    return max(present) / min(present)


def feature_value(features: Mapping[str, Any], key: str, name: str, side: Mapping[str, float] | None = None) -> float | None:
    """focal_mm / face_area for a frame: the FrameFeatures attribute when it exists (read with getattr), else the side table."""
    feature = features.get(key)
    value = getattr(feature, name, None) if feature is not None else None
    if value is None and side is not None:
        value = side.get(key)
    return None if value is None else float(value)


def stack_diagnostics(
    stacks: Sequence[Sequence[str]],
    features: Mapping[str, Any],
    *,
    focal: Mapping[str, float] | None = None,
    faces: Mapping[str, float] | None = None,
    diameter_threshold: int = DRIFT_DIAMETER,
) -> dict[str, Any]:
    """Per stack: hash diameter (largest pairwise Hamming distance) and the two frames that realise it; focal-length and face-area ratio spread
    when that data exists. ``features`` maps key -> FrameFeatures."""
    entries = []
    for members in stacks:
        members = [k for k in members if k in features]
        if len(members) < 2:
            continue
        best = (0, members[0], members[-1])
        for a, b in itertools.combinations(members, 2):
            d = hamming(features[a].phash, features[b].phash)
            if d > best[0]:
                best = (d, a, b)
        entries.append({
            "size": len(members),
            "diameter": best[0],
            "endpoints": [best[1], best[2]],
            "members": list(members),
            "focal_ratio": _ratio([feature_value(features, k, "focal_mm", focal) for k in members]),
            "face_ratio": _ratio([feature_value(features, k, "face_area", faces) for k in members]),
        })
    drifting = [e for e in entries if e["diameter"] >= diameter_threshold]
    diameters = [e["diameter"] for e in entries]
    return {
        "stacks_with_pairs": len(entries),
        "threshold": diameter_threshold,
        "drifting": drifting,
        "drifting_count": len(drifting),
        "max_diameter": max(diameters) if diameters else 0,
        "focal_ratio_over_limit": sum(1 for e in entries if e["focal_ratio"] is not None and e["focal_ratio"] > FOCAL_RATIO_LIMIT),
        "face_ratio_over_limit": sum(1 for e in entries if e["face_ratio"] is not None and e["face_ratio"] > FACE_RATIO_LIMIT),
        "stacks": entries,
    }


# -- review pairs ------------------------------------------------------------------------------------------------------------------------------


def select_review_pairs(
    stacks: Sequence[Sequence[str]],
    features: Mapping[str, Any],
    count: int,
    *,
    focal: Mapping[str, float] | None = None,
    faces: Mapping[str, float] | None = None,
    seed: int = 7,
    diameter_threshold: int = DRIFT_DIAMETER,
) -> list[dict[str, Any]]:
    """Blind pairs from the SAME stack. Suspect pairs: adjacent frames whose focal ratio is over 1.1 or face-area ratio over 1.5, or the two end
    frames of a stack whose hash diameter is at least 18. Controls (about a fifth, at least one when possible): adjacent pairs where nothing
    disagrees. Shuffled; ``reason`` is kept for the analysis and must never be shown to the labeller."""
    rng = random.Random(seed)
    diag = {tuple(e["members"]): e for e in stack_diagnostics(stacks, features, focal=focal, faces=faces, diameter_threshold=diameter_threshold)["stacks"]}
    suspect: list[dict[str, Any]] = []
    control: list[dict[str, Any]] = []
    for members in stacks:
        members = [k for k in members if k in features]
        if len(members) < 2:
            continue
        entry = diag.get(tuple(members))
        if entry is not None and entry["diameter"] >= diameter_threshold:
            suspect.append({"a": entry["endpoints"][0], "b": entry["endpoints"][1], "reason": f"drift: stack hash diameter {entry['diameter']}"})
        for a, b in zip(members, members[1:]):
            reasons = []
            focal_ratio = _ratio([feature_value(features, a, "focal_mm", focal), feature_value(features, b, "focal_mm", focal)])
            face_ratio = _ratio([feature_value(features, a, "face_area", faces), feature_value(features, b, "face_area", faces)])
            if focal_ratio is not None and focal_ratio > FOCAL_RATIO_LIMIT:
                reasons.append(f"focal ratio {focal_ratio:.2f}")
            if face_ratio is not None and face_ratio > FACE_RATIO_LIMIT:
                reasons.append(f"face ratio {face_ratio:.2f}")
            if reasons:
                suspect.append({"a": a, "b": b, "reason": "; ".join(reasons)})
            elif not (entry is not None and entry["diameter"] >= diameter_threshold):
                control.append({"a": a, "b": b, "reason": "control"})
    rng.shuffle(suspect)
    rng.shuffle(control)
    controls_wanted = min(len(control), max(1, count // 5)) if count > 1 else 0
    take_suspect = suspect[: max(0, count - controls_wanted)]
    take_control = control[: count - len(take_suspect)]
    chosen = take_suspect + take_control
    rng.shuffle(chosen)
    seen: set[frozenset[str]] = set()
    pairs = []
    for item in chosen:
        pair_key = frozenset((item["a"], item["b"]))
        if pair_key in seen:
            continue
        seen.add(pair_key)
        pairs.append({"id": len(pairs), **item})
    return pairs
