"""Find exposure-bracket candidates in an ordered run of photos from metadata alone.

Qt-free. It reads shutter, aperture, ISO, timing and optional maker hints, finds runs whose
physical exposure steps in an even ladder, scores them against the pattern library, and
leaves scene confirmation to a pluggable second pass. Nothing here looks at pixels.

Frames must come in capture order (the caller sorts). A decreasing timestamp splits a run.

Only symmetric ladders of 3, 5, 7 or 9 exposures are reported as exposure brackets. Two-frame
and one-sided brackets are not detected yet (they need a maker tag to be believable).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Callable, Iterable, Sequence

from . import bracket_patterns as bp

KIND_EXPOSURE_BRACKET = "exposure_bracket"
KIND_CONSTANT_EV = "constant_ev_stepping"

STATUS_CONFIRMED = "confirmed"      # ladder plus a passing scene check
STATUS_UNCONFIRMED = "unconfirmed"  # ladder looks right; the scene check has not run
STATUS_UNCERTAIN = "uncertain"      # weak evidence; shown for review, never merged automatically

_ODD_SIZES = (9, 7, 5, 3)

_EPOCH = datetime(1970, 1, 1)

# File stem without its extension, used to pair RAW+JPEG siblings.
_STEM_RE = re.compile(r"^(?P<stem>.*?)(?:\.[^./\\]+)?$")


@dataclass(frozen=True, slots=True)
class Frame:
    path: str
    taken_at: float | None = None  # seconds, subsecond when the camera recorded it
    shutter: float | None = None   # seconds
    aperture: float | None = None  # f-number
    iso: float | None = None
    bias: float | None = None      # EXIF ExposureBiasValue
    exposure_mode: int | None = None
    camera: str = ""
    lens: str = ""
    focal_length: float | None = None
    maker_bracket: bool | None = None  # maker tag says bracket on/off when decoded; None unknown
    siblings: tuple[str, ...] = ()     # RAW+JPEG partners folded into this frame

    @property
    def exposure_stops(self) -> float | None:
        """Light gathered, in stops relative to 1 s, f/1, ISO 100. Higher is brighter."""
        if not self.shutter or not self.aperture or not self.iso:
            return None
        if self.shutter <= 0 or self.aperture <= 0 or self.iso <= 0:
            return None
        return math.log2(self.shutter) - 2.0 * math.log2(self.aperture) + math.log2(self.iso / 100.0)


@dataclass(frozen=True, slots=True)
class DetectorConfig:
    min_step_ev: float = 0.25
    max_step_ev: float = 3.3
    rung_tolerance_ev: float = 0.2
    variation_tolerance_ev: float = 0.15
    focal_tolerance_mm: float = 1.5
    soft_gap_seconds: float = 3.0     # beyond shutter time + this, the gap counts against the group
    hard_gap_seconds: float = 20.0    # beyond shutter time + this, the run is split
    interval_gap_seconds: float = 8.0  # regular gaps this long look like interval shooting
    unconfirmed_threshold: float = 0.65
    scene_reject_below: float = 0.4
    scene_confirm_at: float = 0.6


@dataclass(frozen=True, slots=True)
class SequenceGroup:
    paths: tuple[str, ...]
    kind: str
    status: str
    confidence: float
    exposures: int
    step_ev: float
    varies: tuple[str, ...]
    pattern_ids: tuple[str, ...]
    order: str | None
    order_support: str | None
    evidence: tuple[str, ...]
    warnings: tuple[str, ...] = ()


SceneCheck = Callable[[Sequence[str]], "float | None"]


def frame_from_metadata(metadata) -> Frame:
    """Build a Frame from CaptureMetadata (or anything with the same attribute names)."""
    captured = getattr(metadata, "captured_at_value", None)
    taken_at = None
    if captured is not None:
        taken_at = (captured.replace(tzinfo=None) - _EPOCH).total_seconds() + float(getattr(metadata, "subsec_seconds", None) or 0.0)
    return Frame(
        path=getattr(metadata, "path", ""),
        taken_at=taken_at,
        shutter=getattr(metadata, "exposure_seconds", None),
        aperture=getattr(metadata, "aperture_value", None),
        iso=getattr(metadata, "iso_value", None),
        bias=getattr(metadata, "exposure_bias_value", None),
        exposure_mode=getattr(metadata, "exposure_mode", None),
        camera=getattr(metadata, "camera", "") or "",
        lens=getattr(metadata, "lens", "") or "",
        focal_length=getattr(metadata, "focal_length_value", None),
    )


def detect_sequences(
    frames: Sequence[Frame],
    config: DetectorConfig | None = None,
    scene_check: SceneCheck | None = None,
) -> list[SequenceGroup]:
    """Return bracket candidates in capture order.

    `scene_check(paths)` is the second pass. It returns a 0 to 1 same-scene score, or None when it
    could not decide. A score below `scene_reject_below` drops the candidate.
    """
    cfg = config or DetectorConfig()
    folded = _fold_siblings(frames)
    groups: list[SequenceGroup] = []
    index = 0
    while index < len(folded):
        group = None
        consumed = 1
        for size in _ODD_SIZES:
            window = folded[index : index + size]
            if len(window) < size:
                continue
            candidate = _evaluate_window(window, cfg)
            if candidate is None:
                continue
            candidate = _apply_scene_check(candidate, scene_check, cfg)
            if candidate is None:
                continue
            group, consumed = candidate, size
            break
        if group is None:
            group = _constant_ev_candidate(folded[index : index + 3], cfg)
            if group is not None:
                consumed = 3
        if group is not None:
            groups.append(group)
        index += consumed
    return groups


# --- window evaluation -------------------------------------------------------------------------

def _evaluate_window(window: Sequence[Frame], cfg: DetectorConfig) -> SequenceGroup | None:
    stops = [frame.exposure_stops for frame in window]
    if any(value is None for value in stops):
        return None
    if not _same_setup(window, cfg):
        return None
    gaps = _gaps(window)
    if gaps is None or not _gaps_allowed(window, gaps, cfg):
        return None

    size = len(window)
    ordered = sorted(stops)
    step = (ordered[-1] - ordered[0]) / (size - 1)
    if not cfg.min_step_ev <= step <= cfg.max_step_ev:
        return None
    for position, value in enumerate(ordered):
        if abs(value - (ordered[0] + position * step)) > cfg.rung_tolerance_ev:
            return None

    middle = ordered[size // 2]
    rungs = tuple(int(round((value - middle) / step)) for value in stops)
    if len(set(rungs)) != size:
        return None

    varies = _observed_variation(window, cfg)
    evidence = [f"{size} exposures in an even ladder of {step:.2f} EV"]
    warnings: list[str] = []
    score = 0.55 + {3: 0.0, 5: 0.1, 7: 0.15, 9: 0.2}[size]

    order = _order_label(rungs)
    candidates = [p for p in bp.exposure_ladder_patterns() if set(varies) <= set(p.varies)]
    support = _best_support(candidates, order) if order else None
    if order is None:
        score -= 0.1
        warnings.append("shooting order is not one the library knows")
    elif support == bp.ORDER_CONFIRMED:
        score += 0.1
        evidence.append(f"order {order} is documented")
    else:
        evidence.append(f"order {order} is possible but not documented for these cameras")

    score += _timing_score(window, gaps, cfg, evidence, warnings)
    score += _maker_score(window, evidence, warnings)
    score += _bias_score(window, rungs, step, evidence, warnings)

    score = max(0.05, min(0.95, score))
    status = STATUS_UNCONFIRMED if score >= cfg.unconfirmed_threshold else STATUS_UNCERTAIN
    return SequenceGroup(
        paths=_paths(window),
        kind=KIND_EXPOSURE_BRACKET,
        status=status,
        confidence=round(score, 3),
        exposures=size,
        step_ev=round(step, 3),
        varies=varies,
        pattern_ids=tuple(p.id for p in candidates),
        order=order,
        order_support=support,
        evidence=tuple(evidence),
        warnings=tuple(warnings),
    )


def _constant_ev_candidate(window: Sequence[Frame], cfg: DetectorConfig) -> SequenceGroup | None:
    """Aperture or shutter steps while total exposure stays put (depth-of-field or motion brackets)."""
    if len(window) != 3:
        return None
    stops = [frame.exposure_stops for frame in window]
    if any(value is None for value in stops) or not _same_setup(window, cfg):
        return None
    gaps = _gaps(window)
    if gaps is None or not _gaps_allowed(window, gaps, cfg):
        return None
    if max(stops) - min(stops) > cfg.variation_tolerance_ev:
        return None
    varies = _observed_variation(window, cfg)
    if not varies or set(varies) - {bp.SHUTTER, bp.APERTURE}:
        return None
    if len({frame.shutter for frame in window}) < 3 and len({frame.aperture for frame in window}) < 3:
        return None
    return SequenceGroup(
        paths=_paths(window),
        kind=KIND_CONSTANT_EV,
        status=STATUS_UNCERTAIN,
        confidence=0.4,
        exposures=3,
        step_ev=0.0,
        varies=varies,
        pattern_ids=("aperture_dof_bracket", "motion_shutter_bracket"),
        order=None,
        order_support=None,
        evidence=("exposure stays constant while " + " and ".join(varies) + " change",),
        warnings=("not an exposure bracket; may be a depth-of-field or motion bracket, or metering drift",),
    )


# --- scoring pieces ----------------------------------------------------------------------------

def _timing_score(window, gaps, cfg, evidence, warnings) -> float:
    if gaps == "unknown":
        warnings.append("capture times missing; timing not checked")
        return -0.1
    adjustment = 0.0
    tight = all(gap <= (window[i].shutter or 0.0) + cfg.soft_gap_seconds for i, gap in enumerate(gaps))
    if tight:
        adjustment += 0.1
        evidence.append("frames follow each other closely")
    else:
        adjustment -= 0.1
        warnings.append("some gaps are long for a bracket")
    mean = sum(gaps) / len(gaps)
    if mean >= cfg.interval_gap_seconds and max(gaps) - min(gaps) <= 0.1 * mean:
        adjustment -= 0.25
        warnings.append("regular multi-second cadence looks like interval shooting")
    return adjustment


def _maker_score(window, evidence, warnings) -> float:
    flags = [frame.maker_bracket for frame in window]
    if all(flag is True for flag in flags):
        evidence.append("camera maker tag marks every frame as a bracket")
        return 0.25
    if all(flag is False for flag in flags):
        warnings.append("camera maker tag says bracketing was off")
        return -0.2
    return 0.0


def _bias_score(window, rungs, step, evidence, warnings) -> float:
    biases = [frame.bias for frame in window]
    if any(value is None for value in biases):
        return 0.0
    if max(biases) - min(biases) < 0.1:
        warnings.append("exposure bias is identical across frames; ladder came from shutter, aperture or ISO")
        return 0.0
    base = biases[rungs.index(0)]
    expected = [base + rung * step for rung in rungs]
    if all(abs(have - want) <= 0.25 for have, want in zip(biases, expected)):
        evidence.append("exposure bias follows the same ladder")
        return 0.1
    return 0.0


def _apply_scene_check(group: SequenceGroup, scene_check: SceneCheck | None, cfg: DetectorConfig):
    if scene_check is None:
        return group
    score = scene_check(group.paths)
    if score is None:
        return replace(group, warnings=group.warnings + ("scene check could not decide",))
    if score < cfg.scene_reject_below:
        return None
    if score >= cfg.scene_confirm_at and group.confidence >= cfg.unconfirmed_threshold:
        return replace(
            group,
            status=STATUS_CONFIRMED,
            confidence=round(min(0.99, group.confidence + 0.1), 3),
            evidence=group.evidence + (f"scene check passed ({score:.2f})",),
        )
    return replace(group, status=STATUS_UNCERTAIN, warnings=group.warnings + (f"scene check unsure ({score:.2f})",))


# --- helpers -----------------------------------------------------------------------------------

def _order_label(rungs: tuple[int, ...]) -> str | None:
    m = len(rungs) // 2
    negatives_far_first = list(range(-m, 0))
    negatives_near_first = list(range(-1, -m - 1, -1))
    positives_near_first = list(range(1, m + 1))
    positives_far_first = list(range(m, 0, -1))
    shapes = {
        bp.ORDER_ZERO_MINUS_PLUS: (
            [0] + negatives_far_first + positives_near_first,
            [0] + negatives_near_first + positives_near_first,
        ),
        bp.ORDER_MINUS_ZERO_PLUS: (list(range(-m, m + 1)),),
        bp.ORDER_PLUS_ZERO_MINUS: (list(range(m, -m - 1, -1)),),
        bp.ORDER_ZERO_PLUS_MINUS: (
            [0] + positives_far_first + negatives_near_first,
            [0] + positives_near_first + negatives_near_first,
        ),
    }
    for label, options in shapes.items():
        if list(rungs) in options:
            return label
    return None


def _best_support(patterns: Iterable[bp.BracketPattern], order: str) -> str | None:
    best = None
    for pattern in patterns:
        support = bp.order_support(pattern, order)
        if support == bp.ORDER_CONFIRMED:
            return support
        if support is not None:
            best = support
    return best


def _observed_variation(window: Sequence[Frame], cfg: DetectorConfig) -> tuple[str, ...]:
    varies = []
    for name, values in (
        (bp.SHUTTER, [math.log2(f.shutter) if f.shutter else None for f in window]),
        (bp.APERTURE, [2.0 * math.log2(f.aperture) if f.aperture else None for f in window]),
        (bp.ISO, [math.log2(f.iso / 100.0) if f.iso else None for f in window]),
    ):
        known = [value for value in values if value is not None]
        if known and max(known) - min(known) > cfg.variation_tolerance_ev:
            varies.append(name)
    return tuple(varies)


def _same_setup(window: Sequence[Frame], cfg: DetectorConfig) -> bool:
    cameras = {frame.camera for frame in window if frame.camera}
    lenses = {frame.lens for frame in window if frame.lens}
    if len(cameras) > 1 or len(lenses) > 1:
        return False
    focals = [frame.focal_length for frame in window if frame.focal_length is not None]
    return not focals or max(focals) - min(focals) <= cfg.focal_tolerance_mm


def _gaps(window: Sequence[Frame]):
    times = [frame.taken_at for frame in window]
    if any(value is None for value in times):
        return "unknown"
    gaps = [later - earlier for earlier, later in zip(times, times[1:])]
    return None if any(gap < 0 for gap in gaps) else gaps


def _gaps_allowed(window: Sequence[Frame], gaps, cfg: DetectorConfig) -> bool:
    if gaps == "unknown":
        return True
    return all(gap <= (window[i].shutter or 0.0) + cfg.hard_gap_seconds for i, gap in enumerate(gaps))


def _paths(window: Sequence[Frame]) -> tuple[str, ...]:
    paths: list[str] = []
    for frame in window:
        paths.append(frame.path)
        paths.extend(frame.siblings)
    return tuple(paths)


def _fold_siblings(frames: Sequence[Frame]) -> list[Frame]:
    """Merge RAW+JPEG partners (same stem, adjacent, within a second) into one exposure."""
    folded: list[Frame] = []
    for frame in frames:
        if folded and _is_sibling(folded[-1], frame):
            previous = folded[-1]
            folded[-1] = replace(previous, siblings=previous.siblings + (frame.path,))
        else:
            folded.append(frame)
    return folded


def _is_sibling(previous: Frame, current: Frame) -> bool:
    if _stem(previous.path).casefold() != _stem(current.path).casefold():
        return False
    if previous.taken_at is not None and current.taken_at is not None:
        return abs(current.taken_at - previous.taken_at) <= 1.0
    return True


def _stem(path: str) -> str:
    match = _STEM_RE.match(path)
    return match.group("stem") if match else path

