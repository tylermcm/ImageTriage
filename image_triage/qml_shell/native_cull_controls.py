"""Editable native settings; untouched fields survive every UI edit.

Every setting of the pinned engine's `CullSettings` and `GateSettings` (aiculler/api.py in packaging/cli_culler_wheels) has a control
here: `FIELDS` are the ones the first design had, `MORE_FIELDS` the rest, `HELP` one plain line for each. Ranges follow what the
engine's own `validate()` states (a fraction is 0..1, a keeper count or a weight is not negative, `appeal_shortlist` is at least 1);
where it states none, the range is the setting's natural one. Rules between fields (`clip_region_ignore` below `clip_region_reject`,
`split_cos` at most `merge_cos`, the quality levels in order) are the engine's to check, on Save, in its own process.
`tests/test_native_cull_controls.py` compares this list with the engine's dataclasses so a setting cannot be left without a control.
"""
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
# The settings the first design left to the Advanced JSON. (key, label, group, kind, default, min, max, step); kinds "text" (a path,
# empty is none) and "list" (names separated by commas) are typed, the others are the same controls as above.
MORE_FIELDS = (
    ("models.people", "People index", "models", "toggle", False, 0, 1, 1),
    ("moving_brackets", "Brackets shot of a moving subject", "keepers", "choice", "as_burst", 0, 0, 0),
    ("focus_stacks", "Focus stacks", "keepers", "choice", "keep_all", 0, 0, 0),
    ("timelapses", "Timelapses", "keepers", "choice", "keep_all", 0, 0, 0),
    ("review_warnings", "Grouping warnings that ask for review", "keepers", "choice", "doubt", 0, 0, 0),
    ("keep_people_coverage", "Keep one good frame of every person", "people", "toggle", True, 0, 1, 1),
    ("face_cluster_threshold", "Same-person similarity", "people", "slider", .35, 0, 1, .01),
    ("gates.clip_region_ignore", "Ignore clipped regions below", "quality", "slider", .05, 0, 1, .01),
    ("gates.clip_penalty", "Score penalty for clipping", "quality", "slider", .15, 0, 1, .01),
    ("gates.black_mean", "Black frame below this brightness", "exposure", "slider", .02, 0, 1, .01),
    ("gates.sky_blown", "Sky blown out from", "exposure", "slider", .75, 0, 1, .01),
    ("gates.washed_mean", "Washed out · brightness from", "exposure", "slider", .8, 0, 1, .01),
    ("gates.washed_spread", "Washed out · tonal spread up to", "exposure", "slider", .15, 0, 1, .01),
    ("gates.shadows_crushed", "Unusable shadows above", "exposure", "slider", .6, 0, 1, .01),
    ("gates.featureless", "Featureless frames", "exposure", "choice", "review", 0, 0, 0),
    ("gates.quality_baseline", "Library baseline file", "floor", "text", None, 0, 0, 0),
    ("weights.appeal", "Weight · appeal", "ranking", "slider", .4, 0, 1, .01),
    ("weights.maniqa", "Weight · technical quality", "ranking", "slider", .25, 0, 1, .01),
    ("weights.face_sharpness", "Weight · face sharpness", "ranking", "slider", .15, 0, 1, .01),
    ("weights.sharpness", "Weight · frame sharpness", "ranking", "slider", .12, 0, 1, .01),
    ("weights.exposure", "Weight · exposure", "ranking", "slider", .08, 0, 1, .01),
    ("weights.preference", "Weight · learned preference", "ranking", "slider", .1, 0, 1, .01),
    ("normalise", "Compare scores within a set by", "ranking", "choice", "relative", 0, 0, 0),
    ("maniqa_scope", "Frames MANIQA scores", "ranking", "choice", "keepers", 0, 0, 0),
    ("appeal_shortlist", "Appeal shortlist per set", "ranking", "slider", 3, 1, 10, 1),
    ("preference_model", "Learned preference model file", "ranking", "text", None, 0, 0, 0),
    ("backend", "Metadata reader", "shoot", "choice", "auto", 0, 0, 0),
    ("exclude", "Folder names to skip", "shoot", "list", [], 0, 0, 0),
)
ALL_FIELDS = FIELDS + MORE_FIELDS
INTEGERS = ("bursts_keep", "scene_keep", "appeal_shortlist")
CHOICES = {"brackets": ["keep_set", "pick_one", "ignore"],
           "gates.quality_signal": ["maniqa", "qra_iqa", "either", "both", "agree"],
           "gates.quality_mode": ["raw", "shoot", "category", "technique"],
           "gates.quality_unique": ["review", "reject"],
           "moving_brackets": ["as_burst", "bracket"],
           "focus_stacks": ["keep_all", "cull"],
           "timelapses": ["keep_all", "cull"],
           "review_warnings": ["doubt", "all", "none"],
           "gates.featureless": ["review", "reject", "off"],
           "normalise": ["relative", "rank", "zscore"],
           "maniqa_scope": ["keepers", "all"],
           "backend": ["auto", "exiftool", "native"]}
# One plain line per setting, shown under its label.
HELP = {
    "brackets": "Keep a whole exposure bracket, keep its best-exposed frame only, or leave brackets out of culling.",
    "models.faces": "Finds prominent faces, so closed eyes and face sharpness count when frames are ranked.",
    "models.search": "Reads every photo once so the folder can be searched in plain words.",
    "models.maniqa": "Scores technical quality for ranking and for the severe-blur check.",
    "models.appeal": "Scores how appealing a frame is when frames of one set are ranked.",
    "models.people": "Groups the faces of the folder into people and writes them to the index. Uses the face and search models.",
    "bursts_keep": "How many frames of a burst are kept. The rest are suggested as rejects.",
    "scene_keep": "How many shots of one near-identical scene are kept.",
    "review_below": "A group the engine is less sure of than this is sent to review instead of being decided.",
    "moving_brackets": "A bracket whose frames do not line up is ranked like a burst, or treated like any other bracket.",
    "focus_stacks": "Keep every frame of a focus stack, or cull it like a burst.",
    "timelapses": "Keep every frame of a timelapse, or cull it like a burst.",
    "review_warnings": "Send a group to review only when its grouping is in doubt, on any warning, or never.",
    "require_eyes_open": "A blink counts against a frame. In a group shot, any prominent face counts.",
    "blink_threshold": "An eyes-closed score at or above this is a blink (0 open, 1 closed).",
    "blink_penalty": "How many score points a blink costs when frames are ranked.",
    "keep_people_coverage": "Every recognised person keeps at least one acceptable frame, even one a ranking would cut.",
    "face_cluster_threshold": "Two faces at least this similar are the same person. Lower merges more faces into one person.",
    "gates.blur_ratio": "With MANIQA: reject a frame whose sharpness is below this share of its set's median.",
    "gates.maniqa_bottom": "With MANIQA: only frames in this bottom share of the shoot can be rejected as severely blurred.",
    "gates.clip_region_reject": "Reject a frame whose largest blown-out area covers at least this share of it.",
    "gates.clip_region_ignore": "A blown-out area smaller than this share of the frame costs nothing. Must stay below the reject level.",
    "gates.clip_penalty": "Score points lost at the reject level; smaller blown areas lose proportionally less.",
    "gates.black_mean": "A frame whose average brightness is below this is a black frame and is rejected (0 black, 1 white).",
    "gates.sky_blown": "Reject when at least this share of the top third of the frame is blown to white.",
    "gates.washed_mean": "Washed out needs an average brightness at or above this, together with the spread below.",
    "gates.washed_spread": "Washed out needs a tonal spread at or below this, together with the brightness above.",
    "gates.shadows_crushed": "Reject when more than this share of the frame is crushed to black.",
    "gates.featureless": "A frame with no detail (a blank, or a moon in an empty sky) goes to review, is rejected, or is left alone.",
    "gates.quality_reject": "With MANIQA: a frame about to be kept that scores below this is rejected. 0 is off.",
    "gates.quality_review": "With MANIQA: a frame about to be kept that scores below this goes to review. 0 is off.",
    "gates.quality_signal": "Which model decides the floor, or how the two are combined.",
    "gates.quality_mode": "What the MANIQA levels compare with: the score itself, or its percentile in the shoot or a library baseline.",
    "gates.quality_unique": "What happens to the best frame of a moment when even it is below the reject level.",
    "gates.qra_reject": "With appeal ranking: a frame about to be kept whose image quality is below this is rejected. 0 is off.",
    "gates.qra_review": "With appeal ranking: a frame about to be kept whose image quality is below this goes to review. 0 is off.",
    "gates.quality_baseline": "The baseline file the Category and Technique quality scales compare with. Leave empty for none.",
    "refine_scenes": "With semantic search: also split and merge scenes by how similar their pictures are.",
    "split_cos": "Neighbouring groups of one scene less similar than this are split into two scenes.",
    "merge_cos": "Neighbouring scenes at least this similar are merged. Must not be below the split level.",
    "merge_window": "Only scenes taken within this many seconds of each other are merged.",
    "weights.appeal": "How much the appeal score counts when the frames of a set are ranked.",
    "weights.maniqa": "How much the technical quality score counts when the frames of a set are ranked.",
    "weights.face_sharpness": "How much the sharpness of the largest face counts when the frames of a set are ranked.",
    "weights.sharpness": "How much whole-frame sharpness counts when the frames of a set are ranked.",
    "weights.exposure": "How much exposure counts when the frames of a set are ranked.",
    "weights.preference": "How much a learned preference model counts. It has no effect without a model file.",
    "normalise": "Distance from the set's best frame, the frame's rank in the set, or a z-score.",
    "maniqa_scope": "Contested frames and frames about to be kept, or every frame that competes (slower).",
    "appeal_shortlist": "The appeal model reads at most this many frames of a set: it is the slowest model.",
    "preference_model": "A model file trained from your own keep and reject choices. Leave empty for none.",
    "backend": "How capture data is read: ExifTool when it is installed, or the built-in reader.",
    "exclude": "Sub-folder names the analysis does not read, separated by commas.",
}
PRESETS = {
    "careful": (.50, .25, .75),
    "balanced": (.35, .35, .60),
    "strict": (.25, .50, .45),
}
GROUPS = (("models", "What will run"), ("keepers", "Keepers and review"),
          ("eyes", "Closed eyes"), ("people", "People"), ("quality", "Technical quality"), ("exposure", "Exposure failures"),
          ("floor", "Quality floor · disabled at zero"), ("scenes", "Scene grouping"),
          ("ranking", "Ranking weights and scope"), ("shoot", "Reading the shoot"))
ADVANCED_GROUPS = ("ranking", "shoot")              # shown under Advanced; the others under Fine-tune
UNRESETTABLE_GROUPS = ("models",)                   # the engine's default is "no models": the feature cards above decide these


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

    def default(self, key):
        """What the engine uses when the profile does not set `key`: the engine's own default, else the one listed here."""
        spec = next(s for s in ALL_FIELDS if s[0] == key)
        if key.startswith("models."):
            models = self.defaults.get("models", [])
            return key.split(".")[1] in (models if isinstance(models, list) else [])
        source = self.defaults
        for part in key.split("."):
            if not isinstance(source, dict) or part not in source:
                return spec[4]
            source = source[part]
        return source

    def reset_group(self, group):
        """Take every setting of one group out of the profile, so the engine's defaults apply again. Other groups are untouched."""
        if group in UNRESETTABLE_GROUPS or group not in dict(GROUPS):
            raise ValueError("This group cannot be reset")
        for key, _label, field_group, *_rest in ALL_FIELDS:
            if field_group != group:
                continue
            parts = key.split(".")
            target = self.draft
            for part in parts[:-1]:
                target = target.get(part) if isinstance(target, dict) else None
            if isinstance(target, dict) and parts[-1] in target:
                del target[parts[-1]]
                self.dirty = True
            if len(parts) == 2 and isinstance(self.draft.get(parts[0]), dict) and not self.draft[parts[0]]:
                del self.draft[parts[0]]            # an emptied "gates" or "weights" is left out, not sent as {}

    def _models(self):
        models = self.draft.get("models", self.defaults.get("models", []))
        if isinstance(models, str):
            models = [m.strip() for m in models.split(",") if m.strip()]
        return ["faces", "maniqa", "appeal", "search", "people"] if "all" in models else list(models)

    def set(self, key, value):
        spec = next((s for s in ALL_FIELDS if s[0] == key), None)
        if spec is None:
            raise ValueError("Unknown culling control")
        if spec[3] == "toggle":
            if not isinstance(value, bool):
                raise ValueError("Expected an on/off value")
        elif spec[3] == "choice":
            if value not in CHOICES[key]:
                raise ValueError("Unknown native option")
        elif spec[3] == "text":
            if value is not None and not isinstance(value, str):
                raise ValueError("Expected a file path")
            value = (value or "").strip() or None
        elif spec[3] == "list":
            if isinstance(value, str):
                value = value.split(",")
            if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
                raise ValueError("Expected names separated by commas")
            value = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        else:
            value = float(value)
            if not spec[5] <= value <= spec[6]:
                raise ValueError("Value is outside the control range; use Advanced to preserve a different valid native value")
            value = round(value, 6)
            if key in INTEGERS:
                if not value.is_integer():
                    raise ValueError("Keeper counts must be whole numbers" if key != "appeal_shortlist" else "The shortlist must be a whole number")
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
            if parts[0] == "weights" and not isinstance(self.draft.get("weights"), dict):
                # The engine takes `weights` whole: a profile that sets one weight must carry the others too, or they would vanish.
                self.draft["weights"] = dict(self.defaults.get("weights") or {s[0].split(".")[1]: s[4] for s in ALL_FIELDS if s[0].startswith("weights.")})
            target = self.draft
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value
        self.dirty = True

    def preset(self, name):
        values = PRESETS[name]
        for key, value in zip(("gates.clip_region_reject", "gates.blur_ratio", "blink_threshold"), values):
            self.set(key, value)

    @staticmethod
    def _shown(kind, value, step):
        """A value as its control shows it: a typed field holds text, and "none" is said in words for a default."""
        if kind == "text":
            return value or ""
        if kind == "list":
            return ", ".join(value or [])
        return value

    @staticmethod
    def _default_text(kind, value, step):
        if kind == "toggle":
            return "On" if value else "Off"
        if kind == "choice":
            return str(value).replace("_", " ")
        if kind in ("text", "list"):
            return (", ".join(value) if isinstance(value, (list, tuple)) else str(value or "")) or "none"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(int(round(value))) if step >= 1 else f"{value:.2f}"
        return str(value)

    def groups(self):
        out = []
        for group_key, title in GROUPS:
            fields = []
            for key, label, group, kind, default, low, high, step in ALL_FIELDS:
                if group != group_key:
                    continue
                engine_default = self.default(key)
                value = self.value(key, default)
                fields.append({"key": key, "label": label, "kind": kind, "value": self._shown(kind, value, step), "help": HELP.get(key, ""),
                               "min": low, "max": high, "step": step, "choices": CHOICES.get(key, []),
                               "default": self._default_text(kind, engine_default, step),
                               "isDefault": self._shown(kind, value, step) == self._shown(kind, engine_default, step)})
            resettable = group_key not in UNRESETTABLE_GROUPS
            out.append({"key": group_key, "title": title, "tier": "advanced" if group_key in ADVANCED_GROUPS else "fine", "fields": fields,
                        "resettable": resettable, "changed": resettable and any(not f["isDefault"] for f in fields)})
        return out
