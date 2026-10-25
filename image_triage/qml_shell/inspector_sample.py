"""The mockup's Quality and AI tabs for `--mock`, in the shape the real services (`inspection.py`, `ai_scores.py`) answer in, so the inspector draws both the same way.

Only the mockup's twelve sample photos use these: a photo from a real folder is measured (`inspection.get`) and looked up in the folder's AI report (`aiScores.get`).
"""
from __future__ import annotations

_EXPO_VALUE = {"Balanced": 88, "Bright": 55, "Dark": 48}
_NOISE_VALUE = {"Low": 90, "Medium": 60}


def _tone(value: int) -> int:
    return 0 if value >= 70 else 1 if value >= 50 else 2


def _check(name: str, text: str, value: int) -> dict:
    return {"name": name, "text": text, "value": value, "tone": _tone(value)}


def sample_measured(focus: int, expo: str, noise: str) -> dict:
    """The Quality tab's four checks for a mockup photo (the "Why" list of the sample AI tab carries the sentences)."""
    return {"ready": True, "reasons": [], "summary": "", "checks": [
        _check("Focus", "Sharp" if focus >= 70 else "Slightly soft" if focus >= 50 else "Soft", focus),
        _check("Exposure", expo, _EXPO_VALUE.get(expo, 70)),
        _check("Noise", noise, _NOISE_VALUE.get(noise, 70)),
        _check("Motion blur", "None detected", 96)]}


def sample_ai(score: int, decision: str, focus: int, expo: str, group: list[dict]) -> dict:
    """The AI tab for a mockup photo: its score, call, the three reasons the mockup shows, and the next three photos as its group."""
    call = "Reject" if decision == "rej" else "Winner candidate" if score >= 85 else "Keep" if score >= 65 else "Review"
    line = "High confidence" if score >= 85 else "Medium confidence" if score >= 65 else "Low confidence, take a look"
    return {"has": True, "state": "ready", "score": score, "call": call, "line": line, "good": score >= 65, "size": len(group), "group": group, "reasons": [
        {"good": focus >= 70, "head": "Subject is sharp." if focus >= 70 else "Focus is a little soft.", "detail": f"Focus score {focus} of 100."},
        {"good": expo == "Balanced", "head": f"{expo} exposure.", "detail": "Highlights close to clipping." if expo == "Bright" else "Highlights well protected."},
        {"good": True, "head": "Strong composition.", "detail": "Horizon and subject sit on thirds."}]}
