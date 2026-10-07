"""The "why" behind an AI call, for the hover tooltip on a grid card (audit I3 / WI-6.5).

The AI's call already reaches the user as a badge ("Winner · 87"), and the Inspector explains it for the
*selected* photo, but nothing explained it for the card under the pointer. This module turns what is
already stored with each result into a few plain lines: the AI's own reason, sharpness / exposure /
quality score, and any technical flags and the subject category. It is pure (no Qt) so the wording and
thresholds are testable; ``ThumbnailGridView`` shows it only when "Show AI tags on cards in the grid" is
on, so manual review stays clean by default.

Deliberately NOT here: faces and eyes. They are not in the live result (the per-face data belongs to the
parked AI v4 ``quality/`` code, needs the optional face model pack, and blink detection is deferred
because a wrong blink would cause false rejects).

Sharpness uses the Inspector's own "Focus" cut-offs and the category names are the Inspector's "Subject"
names; ``tests/test_ai_why.py`` pins both so the card and the Inspector cannot drift apart.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

# Same cut-offs as the Inspector's Focus row (InspectorPanel._focus_level).
SHARP_MIN = 70.0
ACCEPTABLE_MIN = 40.0
# ReviewInsight.exposure_score is a 0-100 *balance* score (100 - clipping - distance from mid-grey), so it
# can say "balanced" or not but not which way it errs. review_workflows already treats >= 60 as "clean".
EXPOSURE_BALANCED_MIN = 60.0
EXPOSURE_UNEVEN_MIN = 40.0

MAX_REASON_LINES = 3
MAX_REASON_CHARS = 180

# The technical flags aiculler can trigger (aiculler/resources/tag_penalties.csv).
FLAG_LABELS = {
    "blownout": "Blown highlights",
    "harshlight": "Harsh light",
    "underexposed": "Underexposed",
    "lowcontrast": "Low contrast",
    "outoffocus": "Out of focus",
    "motionblur": "Motion blur",
}

# The subject categories (aiculler/resources/categories.csv), named as in the Inspector's Subject row.
CATEGORY_LABELS = {
    "people_portrait": "Portrait",
    "landscape": "Landscape",
    "wildlife": "Wildlife",
    "travel_built": "Travel/Built",
    "night_astro": "Night/Astro",
    "macro_detail": "Macro/Detail",
    "abstract_texture": "Abstract/Texture",
    "product_still_life": "Product/Still",
    "street_documentary": "Street/Documentary",
    "architecture": "Architecture",
    "sports_action": "Sports/Action",
    "event_stage": "Event/Stage",
    "vehicle_transport": "Vehicle/Transport",
    "interior_space": "Interior",
    "aerial_drone": "Aerial/Drone",
    "water_coastal": "Water/Coastal",
}

_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class AIWhy:
    call: str
    reasons: tuple[str, ...] = ()
    sharpness: str = ""
    exposure: str = ""
    quality: str = ""
    flags: tuple[str, ...] = ()
    category: str = ""


def sharpness_text(detail_score: float | None) -> str:
    if detail_score is None or detail_score <= 0:
        return ""
    if detail_score >= SHARP_MIN:
        verdict = "Sharp"
    elif detail_score >= ACCEPTABLE_MIN:
        verdict = "Acceptable"
    else:
        verdict = "Blur detected"
    return f"{verdict} ({detail_score:.0f})"


def exposure_text(exposure_score: float | None) -> str:
    if exposure_score is None or exposure_score <= 0:
        return ""
    if exposure_score >= EXPOSURE_BALANCED_MIN:
        verdict = "Balanced"
    elif exposure_score >= EXPOSURE_UNEVEN_MIN:
        verdict = "Uneven"
    else:
        verdict = "Poor"
    return f"{verdict} ({exposure_score:.0f})"


def quality_text(technical_score: float | None) -> str:
    """The TOPIQ technical-quality score, 0-1 with higher better."""
    if technical_score is None:
        return ""
    return f"{technical_score:.2f}"


def flag_labels(triggered_tags: str | None) -> tuple[str, ...]:
    labels: list[str] = []
    for raw in str(triggered_tags or "").split(","):
        tag = raw.strip()
        if not tag:
            continue
        label = FLAG_LABELS.get(tag.casefold()) or tag.replace("_", " ").capitalize()
        if label not in labels:
            labels.append(label)
    return tuple(labels)


def category_label(primary_category: str | None) -> str:
    category = str(primary_category or "").strip()
    if not category or category.casefold() == "uncategorized":
        return ""
    return CATEGORY_LABELS.get(category.casefold()) or category.replace("_", " ").title()


def _reason_lines(*texts: str | None) -> tuple[str, ...]:
    lines: list[str] = []
    seen: set[str] = set()
    for text in texts:
        for sentence in _SENTENCE_BREAK.split(str(text or "").strip()):
            sentence = sentence.strip().rstrip(".").strip()
            if not sentence or sentence.casefold() in seen:
                continue
            seen.add(sentence.casefold())
            if len(sentence) > MAX_REASON_CHARS:
                sentence = sentence[: MAX_REASON_CHARS - 1].rstrip() + "…"
            lines.append(sentence)
    return tuple(lines[:MAX_REASON_LINES])


def build_ai_why(result, review_insight=None) -> AIWhy | None:
    """What to say about ``result`` (an ``AIImageResult``); ``review_insight`` adds sharpness/exposure.

    Returns None when there is no AI result: a card with nothing to explain gets no tooltip."""
    if result is None:
        return None
    return AIWhy(
        call=str(result.confidence_bucket_label),
        reasons=_reason_lines(getattr(result, "confidence_summary", ""), getattr(result, "cluster_reason", "")),
        sharpness=sharpness_text(getattr(review_insight, "detail_score", None)),
        exposure=exposure_text(getattr(review_insight, "exposure_score", None)),
        quality=quality_text(getattr(result, "technical_score", None)),
        flags=flag_labels(getattr(result, "triggered_tags", "")),
        category=category_label(getattr(result, "primary_category", "")),
    )


def ai_why_lines(why: AIWhy) -> tuple[str, ...]:
    """The plain-text lines, the AI's call first."""
    lines = [why.call]
    lines.extend(why.reasons)
    measures = [
        part
        for part in (
            f"Sharpness: {why.sharpness}" if why.sharpness else "",
            f"Exposure: {why.exposure}" if why.exposure else "",
        )
        if part
    ]
    if measures:
        lines.append("  ·  ".join(measures))
    if why.quality:
        lines.append(f"Quality score: {why.quality}")
    if why.flags:
        lines.append("Flags: " + ", ".join(why.flags))
    if why.category:
        lines.append(f"Subject: {why.category}")
    return tuple(lines)


def ai_why_tooltip_html(why: AIWhy, *, filename: str = "") -> str:
    """Rich text for a QToolTip: the call in bold, then the supporting lines, then the filename (greyed)
    when the caller passes one."""
    lines = ai_why_lines(why)
    head = f"<b>{html.escape(lines[0])}</b>"
    body = "<br>".join(html.escape(line) for line in lines[1:])
    text = head + (f"<br>{body}" if body else "")
    if filename:
        text += f"<br><span style='color:gray'>{html.escape(filename)}</span>"
    return text
