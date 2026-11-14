"""The numbers behind one photo's AI outcome, for the inspector's AI section and the cull review's detail card.

Only values that a saved report (or the run that just finished) really holds are shown, each with the scale it is on. A value that was
not kept is "not recorded", with the reason when it is known; nothing is derived, rescaled or filled in. No Qt.

* The cascade (report version 2, `cascade.export`) keeps, per frame: the MANIQA and Q-ReAlign scores for the frames those rankers were
  asked about, the eyes-closed score when the check ran, and the preview's sharpness and clipping. Its summary names the device a
  ranker's worker said it ran on. Reports written before version 2 kept none of these.
* The native engine's decisions document keeps, per file, a nullable combined `score`, its individual `scores`, `reasons` and `gates`
  (`FileDecision` in the pinned engine, packaging/cli_culler_wheels). The score names and scales below are the engine's own
  (`aiculler.decide`, `aiculler.scorers.models`): `maniqa` and `appeal` and `qra_iqa` on 0..1, `eyes_closed` on 0..1, `sharpness` and
  `face_sharpness` as a Laplacian variance. Each also has a `<name>_rel` (its standing within the comparison set), which is the
  engine's working value and is not listed. Any other number is shown under the engine's name, without a claim about its scale.
  The device is the one the engine's worker reported for the run, when it was kept.

`details_*` return {"rows": [{"k", "v", "unit", "scale", "recorded"}], "stage": str, "device": str}: `unit` is the scale in a
word or two (for a narrow card), `scale` the sentence that explains it.
"""
from __future__ import annotations

import math
from typing import Mapping

NOT_RECORDED = "not recorded"

_STAGES = {
    "bracket": "Exposure bracket grouping (kept together, not compared)",
    "protected": "Your own Winner mark (never flagged or cut)",
    "eyes": "Eyes-closed check",
    "cleanup": "Cleanup pass (sharpness and highlights against its stack)",
    "duplicate_stack": "Near-duplicate collapse inside its stack (picture hash)",
    "repeat:phash": "Repeat pass across the folder (picture hash)",
    "repeat:ssim": "Repeat pass across the folder (structural similarity)",
    "repeat:orb": "Repeat pass across the folder (feature matching)",
    "quality_ranker": "Quality ranker (MANIQA)",
    "appeal_ranker": "Appeal ranker (Q-ReAlign)",
    "unranked": "Cleanup passes only (no ranker compared it)",
}
# The native engine's score names (aiculler.decide / aiculler.scorers.models): label, unit, scale. The first four are always listed
# (recorded or not); the others only when the engine recorded them. Anything else is shown under the engine's own name.
_VARIANCE = "Variance of the Laplacian. No fixed scale: higher is sharper, and it is only compared within its comparison set."
_NATIVE_ALWAYS = (
    ("maniqa", "Quality (MANIQA)", "0 to 1", "0 to 1, higher is better. Scored for contested frames and for frames about to be kept."),
    ("appeal", "Appeal (Q-ReAlign)", "0 to 1", "0 to 1, higher is better. Scored only for the shortlist of a comparison set."),
    ("eyes_closed", "Eyes closed", "0 open, 1 closed", "0 is open, 1 is closed, for the most closed prominent face."),
    ("sharpness", "Sharpness", "no fixed scale", _VARIANCE),
)
_NATIVE_OPTIONAL = {
    "face_sharpness": ("Face sharpness", "no fixed scale", "For the largest face. " + _VARIANCE),
    "qra_iqa": ("Image quality (Q-ReAlign)", "0 to 1", "0 to 1, higher is better. The signal a Q-ReAlign quality floor reads."),
    "face_count": ("Prominent faces", "count", "Faces that cover at least 1% of the frame."),
    "exposure": ("Exposure", "engine scale", "The engine's exposure score, on the engine's own scale; higher is better."),
}


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def _row(label: str, value: str, scale: str = "", recorded: bool = True, unit: str = "") -> dict:
    return {"k": label, "v": value, "unit": unit if recorded else "", "scale": scale, "recorded": recorded}


def device_text(device: str, name: str = "") -> str:
    """"Graphics card (name)" or "CPU" for what a worker reported ("cuda", "cuda:0", "cpu"); "" for nothing reported."""
    device = str(device or "").strip().lower()
    if not device:
        return ""
    if device.startswith("cuda"):
        return "Graphics card" + (f" ({name})" if name else "")
    return "CPU" if device == "cpu" else device


def details_cascade(measures: Mapping[str, float], decided_by: str, summary: Mapping[str, object] | None) -> dict:
    """One frame of a cascade report. `summary` is the report's `ranked_export_summary.json` (None or {} when it has none)."""
    summary = summary or {}
    try:
        version = int(summary.get("report_version") or 1)
    except (TypeError, ValueError):
        version = 1
    kept = version >= 2 or bool(measures) or bool(decided_by)
    old = "This report was saved before scores were kept. Analyze the folder again to record them."
    ranked_with = [str(name) for name in summary.get("ranked_with") or ()]
    ran = lambda *names: any(any(word in ranker.lower() for word in names) for ranker in ranked_with)
    rows = []

    def ranker(label: str, key: str, used: bool) -> None:
        value = measures.get(key)
        if value is not None:
            rows.append(_row(label, f"{value:.3f}", "0 to 1, higher is better. Only compared with the other frames of its stack.", unit="0 to 1"))
        elif not kept:
            rows.append(_row(label, NOT_RECORDED, old, False))
        elif not used:
            rows.append(_row(label, NOT_RECORDED, "This ranker did not run for this analysis.", False))
        else:
            rows.append(_row(label, NOT_RECORDED, "Not scored: a ranker only reads the frames of a stack that survive the cheaper passes.", False))

    ranker("Quality (MANIQA)", "maniqa_score", ran("maniqa"))
    ranker("Appeal (Q-ReAlign)", "aesthetic_score", ran("realign", "appeal", "aesthetic"))
    blink = measures.get("blink_score")
    threshold = _number(summary.get("blink_threshold"))
    if blink is not None:
        rows.append(_row("Eyes closed", f"{blink:.2f}", "0 is open, 1 is closed, for the most closed prominent face"
                         + (f". Flagged from {threshold:.2f}." if threshold is not None else ". No face scores 0."), unit="0 open, 1 closed"))
    elif not kept:
        rows.append(_row("Eyes closed", NOT_RECORDED, old, False))
    else:
        rows.append(_row("Eyes closed", NOT_RECORDED, "The eyes-closed check did not run for this frame." if summary.get("blink_checked")
                         else "The eyes-closed check did not run for this analysis.", False))
    sharp = measures.get("centre_sharpness")
    if sharp is not None:
        rows.append(_row("Centre sharpness", f"{sharp:,.0f}", "Variance of the Laplacian over the centre of the preview. No fixed scale: "
                                                                "higher is sharper, and it is only compared with its own stack.", unit="no fixed scale"))
    else:
        rows.append(_row("Centre sharpness", NOT_RECORDED, old if not kept else "The preview could not be measured.", False))
    for label, key, scale in (("Clipped highlights", "clipped_fraction", "Share of the preview's pixels with a colour channel at 250 or above (of 255)."),
                              ("Black or white pixels", "extremes_fraction", "Share of the preview's pixels that are nearly black (5 or below) or nearly white (250 or above).")):
        value = measures.get(key)
        if value is not None:
            rows.append(_row(label, f"{value * 100:.1f}%", scale, unit="of pixels"))
    stage = _STAGES.get(decided_by, "")
    if not stage:
        stage = NOT_RECORDED
    device = device_text(str(summary.get("ranking_device") or ""))
    if not device:
        device = "No ranker ran" if kept and not ranked_with else NOT_RECORDED
    return {"rows": rows, "stage": stage, "device": device}


def details_native(item: Mapping[str, object], runtime: Mapping[str, object] | None = None) -> dict:
    """One file of a native engine decisions document. `runtime` is what the engine's worker reported for the run, when it was kept."""
    rows = []
    scores = item.get("scores")
    scores = scores if isinstance(scores, Mapping) else {}
    engine_scale = "As the culling engine recorded it, on the engine's own scale."
    show = lambda value, unit: f"{value:,.0f}" if unit in ("no fixed scale", "count") else f"{value:.3f}"
    for name, label, unit, scale in _NATIVE_ALWAYS:
        value = _number(scores.get(name))
        if value is not None:
            rows.append(_row(label, show(value, unit), scale, unit=unit))
        else:
            rows.append(_row(label, NOT_RECORDED, "The engine recorded no such score for this photo (its model did not run, or did not read this frame).", False))
    always = {name for name, *_rest in _NATIVE_ALWAYS}
    for name, raw in scores.items():
        value = _number(raw)
        name = str(name)
        if value is None or name in always or name.endswith("_rel"):
            continue
        label, unit, scale = _NATIVE_OPTIONAL.get(name, (name, "engine scale", engine_scale))
        rows.append(_row(label, show(value, unit), scale, unit=unit))
    overall = _number(item.get("score"))
    rows.append(_row("Engine score", f"{overall:.3f}", "The engine's overall score for ranking inside its comparison set, on the engine's own scale.", unit="engine scale")
                if overall is not None else _row("Engine score", NOT_RECORDED, "The engine gave this photo no overall score.", False))
    rank, size = item.get("rank_in_set"), item.get("set_size")
    reasons = [str(reason) for reason in item.get("reasons") or () if str(reason).strip()]
    if reasons:
        stage = reasons[0]
    elif isinstance(rank, int) and isinstance(size, int) and size > 1:
        stage = f"Ranked {rank} of {size} in its comparison set"
    else:
        stage = NOT_RECORDED
    runtime = runtime or {}
    device = device_text(str(runtime.get("device") or ""), str(runtime.get("gpu") or "")) or NOT_RECORDED
    return {"rows": rows, "stage": stage, "device": device}


def details_for_result(result, summary: Mapping[str, object] | None) -> dict:
    """The details of a saved report's `AIImageResult`: the native engine's, the cascade's, or an older kind of report (nothing recorded)."""
    if getattr(result, "native_payload", None) is not None:
        return details_native(result.native_payload, (summary or {}).get("host_runtime") if isinstance(summary, Mapping) else None)
    return details_cascade(dict(getattr(result, "measures", ()) or ()), str(getattr(result, "decided_by", "") or ""), summary)
