"""Editable native settings; untouched fields survive every UI edit."""
from __future__ import annotations

import copy
import json
import subprocess
import threading

FIELDS = (
    ("brackets", "Exposure brackets", "keepers", "choice", "keep_set", 0, 0, 0),
    ("models.faces", "Face and eyes check", "models", "toggle", False, 0, 1, 1),
    ("models.search", "Semantic search", "models", "toggle", False, 0, 1, 1),
    ("models.maniqa", "Technical quality (MANIQA)", "models", "toggle", False, 0, 1, 1),
    ("models.appeal", "Appeal ranking", "models", "toggle", False, 0, 1, 1),
    ("bursts_keep", "Keep per burst · 0 keeps all", "keepers", "slider", 2, 0, 10, 1),
    ("scene_keep", "Keep per scene · 0 keeps all", "keepers", "slider", 1, 0, 10, 1),
    ("review_below", "Review grouping below this confidence", "keepers", "slider", .7, 0, 1, .01),
    ("require_eyes_open", "Use closed-eye checks in decisions", "eyes", "toggle", True, 0, 1, 1),
    ("blink_threshold", "Closed-eye threshold", "eyes", "slider", .6, 0, 1, .01),
    ("blink_penalty", "Closed-eye score penalty", "eyes", "slider", .3, 0, 1, .01),
    ("gates.blur_ratio", "Severe blur · sharpness relative to its set", "quality", "slider", .35, 0, 1, .01),
    ("gates.maniqa_bottom", "Severe blur · bottom MANIQA fraction", "quality", "slider", .02, 0, 1, .01),
    ("gates.clip_region_reject", "Reject clipped regions above", "quality", "slider", .35, .06, 1, .01),
    ("gates.quality_reject", "MANIQA reject level · 0 is off", "floor", "number", 0, 0, 100, .01),
    ("gates.quality_signal", "Quality signal", "floor", "choice", "maniqa", 0, 0, 0),
    ("gates.quality_mode", "Quality scale", "floor", "choice", "raw", 0, 0, 0),
    ("gates.quality_unique", "Best or unique frame below the floor", "floor", "choice", "review", 0, 0, 0),
    ("gates.quality_review", "MANIQA review level · 0 is off", "floor", "number", 0, 0, 100, .01),
    ("gates.qra_reject", "Q-ReAlign reject level · 0 is off", "floor", "slider", 0, 0, 1, .01),
    ("gates.qra_review", "Q-ReAlign review level · 0 is off", "floor", "slider", 0, 0, 1, .01),
    ("refine_scenes", "Refine scenes by visual similarity", "scenes", "toggle", True, 0, 1, 1),
    ("split_cos", "Split scenes below this similarity", "scenes", "slider", .8, -1, 1, .01),
    ("merge_cos", "Merge scenes above this similarity", "scenes", "slider", .92, -1, 1, .01),
    ("merge_window", "Merge window · seconds", "scenes", "slider", 120, 0, 600, 5),
)
CHOICES = {"brackets": ["keep_set", "pick_one", "ignore"],
           "gates.quality_signal": ["maniqa", "qra_iqa", "either", "both", "agree"],
           "gates.quality_mode": ["raw", "shoot", "category", "technique"],
           "gates.quality_unique": ["review", "reject"]}
PRESETS = {
    "careful": (.50, .25, .75),
    "balanced": (.35, .35, .60),
    "strict": (.25, .50, .45),
}
GROUPS = (("models", "What will run"), ("keepers", "Keepers and review"),
          ("eyes", "Closed eyes"), ("quality", "Technical quality"),
          ("floor", "Quality floor · disabled at zero"), ("scenes", "Scene grouping"))


DEFAULTS_CODE = "import json; from dataclasses import asdict; from aiculler import CullSettings; print(json.dumps(asdict(CullSettings())))"


class NativeDefaultsRead:
    """One read of the engine's default settings, which starts the engine's Python. It is slow (seconds, 30 at worst), so the
    window runs it on a worker thread; `cancel` may be called from any thread and ends the child process at once."""

    def __init__(self):
        self._lock = threading.Lock()
        self._process = None
        self.cancelled = False

    def cancel(self):
        with self._lock:
            self.cancelled = True
            process = self._process
        if process is not None:
            try:
                process.kill()
            except OSError:
                pass                                # it had already ended

    def read(self, python, timeout=30):
        return json.loads(self.run(python, DEFAULTS_CODE, timeout=timeout))

    def validate(self, python, value, timeout=30):
        self.run(python, "import json,sys; from aiculler import CullSettings; CullSettings.from_dict(json.loads(sys.argv[1]))",
                 [json.dumps(value)], timeout=timeout)

    def run(self, python, code, arguments=(), timeout=30):
        with self._lock:
            if self.cancelled:
                raise ValueError("Reading the native setting defaults was cancelled")
            process = self._process = subprocess.Popen(
                [python, "-I", "-c", code, *arguments], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0))   # no console window flashing up on Windows
        try:
            out, err = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise ValueError(f"The native engine did not report its setting defaults within {timeout:g} seconds") from None
        if self.cancelled:
            raise ValueError("Reading the native setting defaults was cancelled")
        if process.returncode:
            raise ValueError(err.strip() or "Could not read native setting defaults")
        return out


class NativeCullControls:
    def __init__(self, selected, defaults):
        self.draft = copy.deepcopy(selected)
        self.defaults = copy.deepcopy(defaults)
        self.dirty = False

    @classmethod
    def load(cls, runtime, selected):
        return cls(selected, NativeDefaultsRead().read(runtime["python"]))

    def value(self, key, fallback=None):
        if key.startswith("models."):
            return key.split(".")[1] in self._models()
        parts = key.split(".")
        def read(source):
            for part in parts:
                if not isinstance(source, dict) or part not in source:
                    return missing
                source = source[part]
            return source
        missing = object()
        default = read(self.defaults)
        value = read(self.draft)
        return (fallback if default is missing else default) if value is missing else value

    def _models(self):
        models = self.draft.get("models", self.defaults.get("models", []))
        if isinstance(models, str):
            models = [m.strip() for m in models.split(",") if m.strip()]
        return ["faces", "maniqa", "appeal", "search", "people"] if "all" in models else list(models)

    def set(self, key, value):
        spec = next((s for s in FIELDS if s[0] == key), None)
        if spec is None:
            raise ValueError("Unknown culling control")
        if spec[3] == "toggle":
            if not isinstance(value, bool):
                raise ValueError("Expected an on/off value")
        elif spec[3] == "choice":
            if value not in CHOICES[key]:
                raise ValueError("Unknown native option")
        else:
            value = float(value)
            if not spec[5] <= value <= spec[6]:
                raise ValueError("Value is outside the control range; use Advanced to preserve a different valid native value")
            value = round(value, 6)
            if key in ("bursts_keep", "scene_keep"):
                if not value.is_integer():
                    raise ValueError("Keeper counts must be whole numbers")
                value = int(value)
        if key.startswith("models."):
            models = self._models()
            model = key.split(".")[1]
            if "all" in models:
                models = ["faces", "maniqa", "appeal", "search", "people"]
            if value and model not in models:
                models.append(model)
            if not value:
                models = [m for m in models if m != model]
            self.draft["models"] = models
        else:
            parts = key.split(".")
            target = self.draft
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        self.dirty = True

    def preset(self, name):
        values = PRESETS[name]
        for key, value in zip(("gates.clip_region_reject", "gates.blur_ratio", "blink_threshold"), values):
            self.set(key, value)

    def groups(self):
        return [{"title": title, "fields": [
            {"key": key, "label": label, "kind": kind, "value": self.value(key, default),
             "min": low, "max": high, "step": step, "choices": CHOICES.get(key, [])}
            for key, label, group, kind, default, low, high, step in FIELDS if group == group_key]}
            for group_key, title in GROUPS]
