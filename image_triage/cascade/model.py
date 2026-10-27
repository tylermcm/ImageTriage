"""Data types and settings for the stack-first culling cascade (docs/ai_v5_plan.md, docs/ai_v5_implementation.md).

Pure data, no Qt and no models. The extractor supplies ``FrameFeatures``; the cascade returns one ``FrameDecision`` per frame and one
``StackResult`` per stack. Thresholds were set from the owner's reviews of two photo sets and live in ``CascadeSettings`` so they can be exposed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FrameRole(str, Enum):
    FINAL = "final"  # left by the cascade: the frames to look at first
    REJECTED = "rejected"  # removed by pass 1 (flagged and dimmed, never hidden)
    DUPLICATE = "duplicate"  # collapsed into a better-exposed near-identical frame
    CUT_BY_MANIQA = "cut_by_maniqa"
    CUT_BY_RANKING = "cut_by_ranking"  # cut by the final (aesthetic) ranking
    NO_PICK = "no_pick"  # the whole stack is blown out, so no winner is offered


FLAG_BLINK = "blink"
FLAG_SOFT = "soft"
FLAG_CLIPPED = "clipped"
FLAG_BLOWN_OUT = "blown_out"


@dataclass(slots=True, frozen=True)
class FrameFeatures:
    """What the cascade needs to know about one frame, in capture order within its burst."""

    key: str
    phash: int  # 63 or 64 bit perceptual hash; only Hamming distances are used
    centre_sharpness: float  # variance of the Laplacian over the central half of the preview
    clipped: float  # fraction of pixels with any channel at 250 or above
    extremes: float  # fraction of luma pixels at or below 5 or at or above 250
    blink: bool = False
    focal_mm: float | None = None  # EXIF focal length; only used when CascadeSettings.split_focal_ratio is set
    face_area: float | None = None  # fraction (0..1) of the frame covered by the largest prominent face; only used with split_face_ratio


@dataclass(slots=True, frozen=True)
class CascadeSettings:
    split_jump: int = 18  # a consecutive pHash distance at or above this starts a new stack
    soft_ratio: float = 0.4  # soft if centre sharpness is below this fraction of the stack's best
    clip_ratio: float = 2.0  # clipped if above this multiple of the stack's lowest clipped fraction
    clip_floor: float = 0.10  # ... and above this absolute fraction
    blown_out: float = 0.40  # a frame this clipped is blown out; a stack of them gets no pick
    duplicate_distance: int = 8  # complete-linkage pHash distance for collapsing near-duplicates
    repeat_distance: int = 10  # pHash distance within which a survivor repeats an earlier scene of the folder (0 turns the repeat pass off)
    repeat_window: int = 10  # ... compared only with this many of the units (single frames or exposure brackets) before it, in capture order
    repeat_rounds: int = 1  # how many times the repeat passes run over what the previous run left (stops early when a round folds nothing)
    repeat_gate: int = 32  # the pixel passes (SSIM, ORB) only look at pairs whose pHash distance is at or below this
    ssim_threshold: float = 0.65  # aligned, brightness-normalised SSIM at or above which two frames are the same scene (0 turns the SSIM pass off)
    orb_inliers: int = 0  # ORB matches that agree on one near-identity transform, at least this many (0, the default, leaves the ORB pass off: ...
    orb_share: float = 0.6  # ... it matches the same place from a slightly different spot as often as a true repeat, and needs OpenCV) and at least this share of the good matches
    maniqa_keep: int = 3  # the first ranker keeps this many representatives
    final_keep: int = 2  # the last ranker keeps this many
    # Optional behaviours; the defaults reproduce the original cascade exactly.
    linkage: str = "first_fit"  # "first_fit" (first mergeable pair in index order) or "closest" (closest-pair complete linkage)
    max_stack_span: int | None = None  # also start a new stack if any pair inside the stack would be at or above this distance
    split_focal_ratio: float | None = None  # start a new stack if consecutive focal lengths differ by more than this ratio
    split_face_ratio: float | None = None  # the same for the largest-face area (both present and >= 0.01)
    all_flagged_skips_collapse: bool = False  # when every frame of a stack is rejecting-flagged, skip near-duplicate collapse


@dataclass(slots=True, frozen=True)
class FrameDecision:
    key: str
    stack_index: int
    role: FrameRole
    flags: tuple[str, ...] = ()
    rank: int | None = None  # 1-based, only for FINAL frames
    duplicate_of: str | None = None


@dataclass(slots=True, frozen=True)
class StackResult:
    index: int
    keys: tuple[str, ...]
    split_jump: int | None  # pHash distance to the previous stack's last frame; None for the first stack
    no_pick: bool = False


@dataclass(slots=True, frozen=True)
class CascadeResult:
    stacks: tuple[StackResult, ...]
    decisions: tuple[FrameDecision, ...]
    # How many frames each ranker was asked to score, so callers and tests can see the saving.
    scored_by_maniqa: int = 0
    scored_by_aesthetic: int = 0
    decisions_by_key: dict[str, FrameDecision] = field(default_factory=dict, compare=False)
