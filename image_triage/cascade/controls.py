"""The cascade settings that the Cull window's ten Fine-tune sliders do not cover, as controls. No Qt.

`CascadeSettings` (model.py) has 21 fields. Ten are the Fine-tune sliders (`cull_model.TUNES`: split_jump, duplicate_distance,
soft_ratio, clip_floor, blown_out, repeat_distance, ssim_threshold, repeat_window, repeat_rounds, and the eyes threshold, which is a
run setting). The other eleven are here, each with a label, one plain line, the right kind of control and its range.

`CascadeSettings` does not validate itself, so the ranges are what the code that reads each field can use: a picture-hash distance is
0..64, a share is 0..1, a keep count is at least 1, `linkage` is one of the two names `stacking.complete_linkage` accepts. Three
fields are "off" as `None` (or 0 for `orb_inliers`); their sliders show that end as Off. `tests/test_cascade_controls.py` checks that
every field of `CascadeSettings` is covered by a slider there or a control here, so a new setting cannot be left without one.
"""
from __future__ import annotations

from dataclasses import dataclass, fields

from .model import CascadeSettings

# the CascadeSettings fields the Fine-tune sliders already set (cull_model.TUNES; the tenth slider, "blink", is CascadeRunSettings.blink_threshold)
TUNED_FIELDS = ("split_jump", "duplicate_distance", "soft_ratio", "clip_floor", "blown_out", "repeat_distance", "ssim_threshold", "repeat_window", "repeat_rounds")
LINKAGES = ("first_fit", "closest")


@dataclass(frozen=True)
class Control:
    key: str                # the CascadeSettings field
    label: str
    help: str
    kind: str               # "slider", "switch" or "select"
    low: float = 0
    high: float = 1
    step: float = 1
    off: float | None = None        # the slider position that means "off" (the field is then None, or 0 for a count)
    unit: str = ""                  # "%" shows a share as a percentage, "x" a multiple
    choices: tuple[tuple[str, str], ...] = ()


CONTROLS = (
    Control("clip_ratio", "Clipped against its stack", "A frame is clipped only when it has this many times the clipped pixels of the cleanest frame in its stack.",
            "slider", 1.0, 5.0, 0.1, unit="x"),
    Control("all_flagged_skips_collapse", "Keep every frame of an all-flagged stack", "When every frame of a stack is flagged, do not also fold them as near-duplicates.", "switch"),
    Control("linkage", "Near-duplicate grouping", "How near-identical frames are gathered: the first pair that fits, in order, or always the closest pair.",
            "select", choices=(("first_fit", "First fit"), ("closest", "Closest pair"))),
    Control("max_stack_span", "Widest stack", "Also start a new stack when a frame differs this much (out of 64) from any frame already in it.",
            "slider", 0, 64, 1, off=0),
    Control("split_focal_ratio", "New stack on a zoom change", "Start a new stack when the focal length changes by more than this factor between two frames.",
            "slider", 1.0, 3.0, 0.05, off=1.0, unit="x"),
    Control("split_face_ratio", "New stack on a face-size change", "Start a new stack when the largest face grows or shrinks by more than this factor between two frames.",
            "slider", 1.0, 5.0, 0.1, off=1.0, unit="x"),
    Control("repeat_gate", "Repeat check reach", "The picture-comparison repeat checks only look at pairs whose hashes differ by this much or less (out of 64).",
            "slider", 0, 64, 1),
    Control("orb_inliers", "Feature-match repeats", "Fold a frame into an earlier one when at least this many matched features agree. Needs OpenCV; it also matches the same place seen from a slightly different spot.",
            "slider", 0, 80, 1, off=0),
    Control("orb_share", "Feature-match agreement", "The share of good feature matches that must agree before the feature-match check folds a frame.",
            "slider", 0.1, 1.0, 0.05, unit="%"),
    Control("maniqa_keep", "Quality ranker keeps", "How many frames of a stack the quality ranker passes on to the appeal ranker.", "slider", 1, 10, 1),
    Control("final_keep", "Kept per stack", "How many frames of a stack the last ranker keeps. The rest are cut.", "slider", 1, 5, 1),
)
_BY_KEY = {control.key: control for control in CONTROLS}
_DEFAULT = CascadeSettings()


def uncovered_fields() -> list[str]:
    """Fields of `CascadeSettings` with neither a Fine-tune slider nor a control here (none, or a test fails)."""
    return [f.name for f in fields(CascadeSettings) if f.name not in TUNED_FIELDS and f.name not in _BY_KEY]


def _position(control: Control, value):
    """A field value as its control holds it: None (or the count 0) sits at the slider's Off end."""
    if control.kind == "slider":
        return control.off if value is None and control.off is not None else value
    return value


def defaults() -> dict:
    """{field: control position} for the cascade's own defaults."""
    return {control.key: _position(control, getattr(_DEFAULT, control.key)) for control in CONTROLS}


def checked(key: str, value):
    """`value` as a valid position of the control `key`, or ValueError saying why not."""
    control = _BY_KEY.get(key)
    if control is None:
        raise ValueError("Unknown cascade setting")
    if control.kind == "switch":
        if not isinstance(value, bool):
            raise ValueError("Expected an on/off value")
        return value
    if control.kind == "select":
        if value not in dict(control.choices):
            raise ValueError("Unknown option")
        return value
    if isinstance(value, bool):
        raise ValueError("Expected a number")
    number = float(value)
    if not number == number or not control.low <= number <= control.high:
        raise ValueError(f"{control.label} must be between {control.low:g} and {control.high:g}")
    whole = control.step >= 1
    steps = round((number - control.low) / control.step)
    number = control.low + steps * control.step
    return int(round(number)) if whole else round(number, 4)


def settings_kwargs(values: dict) -> dict:
    """The `CascadeSettings` keyword arguments for these control positions (an Off position becomes the field's off value)."""
    out = {}
    for control in CONTROLS:
        value = values.get(control.key, defaults()[control.key])
        if control.kind == "slider" and control.off is not None and value == control.off:
            value = 0 if control.key == "orb_inliers" else None      # a count is off at 0; the optional splits are off as None
        out[control.key] = value
    return out


def text(control: Control, value) -> str:
    """A position in words: "Off", "2.0x", "60%", "3"."""
    if control.kind == "switch":
        return "On" if value else "Off"
    if control.kind == "select":
        return dict(control.choices).get(value, str(value))
    if control.off is not None and value == control.off:
        return "Off"
    if control.unit == "%":
        return f"{round(float(value) * 100)}%"
    if control.unit == "x":
        return f"{float(value):.2f}x" if control.step < 0.1 else f"{float(value):.1f}x"
    return str(int(round(float(value)))) if control.step >= 1 else f"{float(value):.2f}"


def view(values: dict) -> list[dict]:
    """The rows the Cull window draws: each control with its value, its range, its default and whether it is at it."""
    base = defaults()
    rows = []
    for control in CONTROLS:
        value = values.get(control.key, base[control.key])
        rows.append({"key": control.key, "label": control.label, "help": control.help, "kind": control.kind, "value": value,
                     "min": control.low, "max": control.high, "step": control.step, "text": text(control, value),
                     "choices": [{"label": label, "value": name} for name, label in control.choices],
                     "default": text(control, base[control.key]), "isDefault": value == base[control.key]})
    return rows
