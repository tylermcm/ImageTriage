"""The Cull window and the AI Center (mockup: scripts/qml_parity/cull.dc.html).

What is real here: the wording and the rules the cull follows (reason names, thresholds and their measured defaults from
`cascade.model.CascadeSettings`, the CPU-ranking warning from `cascade.launch`), the ranking-on-CPU setting (`ai/cascade_rank_on_cpu`),
rejecting frames (the grid's own decision), and everything the AI Center shows about this PC: whether the AI runtime is installed and starts, the
graphics card, the Visual C++ runtime and the three models, and the buttons that install, repair and remove them (`ai_center.py`). What is **Sample**:
the analysis itself (a scripted run that flags frames by a hash of their names, nothing is measured). In `--mock` and the tests the AI Center is
the mockup's scripted one.
See docs/qml_unwired.md, section "Cull window and AI Center".
"""
from __future__ import annotations

import threading
import time
import zlib

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

from ..ai_setup_plan import format_mb
from ..app_identity import user_settings
from ..cascade.launch import CPU_SECONDS_PER_FRAME_FAST, CPU_SECONDS_PER_FRAME_LAPTOP
from ..cascade.model import CascadeSettings
from ..cascade.rankers.spec import MANIQA, QREALIGN
from .ai_center import STATE_WORDS, AiCenterBackend
from .cull_real import CullRun, outcomes_from_bundle, outcomes_from_result, reason_of, stacks_of

CPU_KEY = "ai/cascade_rank_on_cpu"
SENS_KEY = "ai/cascade_default_strictness"          # new, the shell's: how strict the Cull window starts (the Widgets app has no such choice)
_D = CascadeSettings()
# Measured defaults ("Balanced"); the other two are presets around them.
PRESETS = {
    "careful": {"split": _D.split_jump, "dup": _D.duplicate_distance, "soft": 30, "clip": 15, "blown": 50, "blink": 70},
    "balanced": {"split": _D.split_jump, "dup": _D.duplicate_distance, "soft": round(_D.soft_ratio * 100), "clip": round(_D.clip_floor * 100),
                 "blown": round(_D.blown_out * 100), "blink": 60},
    "strict": {"split": _D.split_jump, "dup": _D.duplicate_distance, "soft": 50, "clip": 7, "blown": 35, "blink": 50},
}
SENS_TEXT = {
    "careful": "Flags only the clearest problems. Fewer frames are flagged, so fewer good ones get caught by mistake.",
    "balanced": "The settings measured on two reviewed albums. Some frames you would have kept still get flagged, which is why nothing is ever hidden.",
    "strict": "Flags borderline frames too. Expect to restore a few.",
}
REASONS = {
    "blink": ("Eyes closed", "#c79bea", True),
    "soft": ("Soft", "#d9b650", True),
    "clipped": ("Clipped", "#e08a4a", True),
    "blown_out": ("Blown out", "#e0614f", True),
    "duplicate": ("Near-duplicate", "#6fb1d8", False),
}
REJECTS = ("blink", "soft", "clipped", "blown_out")
APPLIABLE = REJECTS + ("duplicate",)      # what the Apply page can act on: the problems, and the near-duplicates (off until chosen, they are only suggestions)
GROUP_WHY = {"blink": "A blink on the main face", "soft": "Much softer than the best frame of the stack",
             "clipped": "Many more clipped pixels than its stack-mates", "blown_out": "Mostly pure white or black",
             "duplicate": "Almost identical to a better frame in its stack. Only a suggestion, so it is off until you turn it on"}
# What --mock flags (the mockup's names); other folders are flagged by a hash of the name.
_MOCK_FLAGS = {"DSC_4207": "clipped", "DSC_4210": "soft", "DSC_4219": "blown_out", "DSC_4226": "blink", "DSC_4236": "duplicate"}
_STACK_SIZES = (1, 2, 3, 2, 2, 2)
TUNES = (("split", "Stack split", "A new stack starts when two neighbouring frames differ by this much (out of 64).", 8, 32),
         ("dup", "Near-duplicate distance", "Frames this close are suggested as duplicates.", 2, 16),
         ("soft", "Soft below", "Flag a frame whose sharpness is under this share of the sharpest frame in its stack.", 20, 70),
         ("clip", "Clipped above", "Flag a frame with more than this share of clipped pixels, and more than twice its stack’s cleanest.", 5, 30),
         ("blown", "Blown out from", "A frame this clipped counts as blown out. A stack of them gets no pick.", 25, 70),
         ("blink", "Eyes closed from", "Flag a frame when the most closed face scores at or above this.", 40, 90))
STAGES = ("Reading capture times and brackets", "Reading previews", "Checking for closed eyes", "Deciding")


def _fmt(key: str, v: int) -> str:
    return f"{v / 100:.2f}" if key == "blink" else (f"{v}%" if key in ("soft", "clip", "blown") else str(v))


class CullModel(QObject):
    changed = Signal()
    gridChanged = Signal()
    runFinished = Signal()              # a real analysis has written its report: the inspector's AI tab reads it again
    _savedLoaded = Signal(int, object)
    _timesLoaded = Signal(int, object)

    def __init__(self, photos, memory: bool = False, parent: QObject | None = None, ai: AiCenterBackend | None = None, toasts=None, files=None) -> None:
        super().__init__(parent)
        self._photos = photos
        self._memory = memory
        self._toasts = toasts
        self._files = files                       # file_actions.FileActions: "move them to the recycle bin"
        self._real = False                        # the results on screen are the cascade's own (a run, or a report saved with the folder), not the script's
        self._outcomes: dict = {}
        self._picks: set[str] = set()
        self._t0 = 0.0
        self._token = 0
        self._run = None
        self._settings = None if memory else user_settings()
        cpu = False if memory else bool(self._settings.value(CPU_KEY, False, type=bool))
        self._s = self._defaults(cpu)
        sens = "balanced" if memory else str(self._settings.value(SENS_KEY, "balanced", str))
        if sens in PRESETS:
            self._s.update(sens=sens, vals=dict(PRESETS[sens]))
        self._ai = None if memory else (ai or AiCenterBackend(parent=self))     # what is really installed; None means the mockup's scripted AI Center
        self._ai_state: dict | None = None
        if self._ai is not None:                  # until the first look at the machine nothing is assumed (the scripted defaults say "installed")
            self._ai_state = self._ai.state()
            self._s.update(rt="checking", faceOk=False, gpu=False, vcOld=False, models={"quality": False, "appeal": False})
        self._flags: dict[str, str] = {}
        self._stacks: list[list[dict]] = []
        self._times: dict[str, str] = {}
        self._times_started = False                  # a real folder's capture times are being read (or have been): once per set of stacks
        self._times_gen = 0
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)
        self._install = QTimer(self)
        self._install.setInterval(200)
        self._install.timeout.connect(self._install_tick)
        self._view: dict = {}
        self._rev = 0
        self._rebuild()
        if self._ai is not None:
            self._ai.changed.connect(self._on_ai)
            self._ai.refresh()
        if not memory:
            self._run = CullRun(self)
            self._run.stage.connect(self._run_stage)
            self._run.finished.connect(self._run_finished)
            self._run.failed.connect(self._run_failed)
            self._run.cancelled.connect(self._run_cancelled)
            self._savedLoaded.connect(self._saved_arrived)
            self._timesLoaded.connect(self._times_arrived)
            photos.folderChanged.connect(self._folder_changed)

    def _on_ai(self) -> None:
        """What the machine really has changed (or an install moved on): the Cull window and the AI Center follow."""
        a = self._ai.state()
        self._ai_state = a
        m = a["models"]
        self._set(rt=a["rt"], rtp=a["pct"] / 100, vcOld=a["vcOld"], gpu=a["gpuUsable"], faceOk=m["face"]["state"] == "ready",
                  models={"quality": m["quality"]["state"] == "ready", "appeal": m["appeal"]["state"] == "ready"})
        if a["gpuUsable"] and not self._s["rankTouched"] and not self._s["rank"]:
            self._set(rank=True)                        # a graphics card was found: ranking is cheap, so it starts on (until the person has chosen)

    @staticmethod
    def _defaults(cpu: bool = False) -> dict:
        return {"window": "", "page": "prep", "sens": "balanced", "vals": dict(PRESETS["balanced"]), "tune": False, "eyes": True, "semantic": True, "rank": False, "rankTouched": False,
                "cpuAsk": False, "cpuDefault": cpu, "rt": "ready", "rtp": 0.0, "vcOld": False, "gpu": False, "faceOk": True, "models": {"quality": True, "appeal": False},
                "stage": 0, "p": 0.0, "running": False, "done": False, "hidden": False, "ranked": False, "filter": "all", "sel": "", "restored": {},
                "act": "reject", "inc": {**{r: True for r in REJECTS}, "duplicate": False}, "applied": None, "runs": True, "back": False, "eyesRan": False}

    # -- helpers -----------------------------------------------------------------------------------------------------------------
    def _rows(self) -> list[dict]:
        return self._photos.collect()

    def _frame_time(self, path: str) -> str:
        """When a frame was taken, "HH:MM": the sample page's invented times, or (for a real folder) the capture times read from the files
        on a worker thread, which the page picks up when they have arrived (blank until then)."""
        if self._memory:
            if not self._times:
                for i in range(self._photos.shown):
                    d = self._photos.get(i)
                    self._times[d["path"]] = str(d.get("capture", {}).get("captured", ""))[11:]
            return self._times.get(path, "")
        if not self._times_started:
            self._times_started = True
            self._times_gen += 1
            generation, paths = self._times_gen, [r["path"] for r in self._rows()]

            def work() -> None:
                from ..metadata import load_capture_metadata

                found: dict[str, str] = {}
                for p in paths:
                    try:
                        found[p] = load_capture_metadata(p).captured_at[11:]
                    except Exception:
                        found[p] = ""
                self._timesLoaded.emit(generation, found)

            threading.Thread(target=work, name="qml-cull-times", daemon=True).start()
        return self._times.get(path, "")

    def _times_arrived(self, generation: int, found: dict) -> None:
        if generation == self._times_gen:
            self._times = found
            self._rebuild()

    def _build_stacks(self) -> None:
        rows = self._rows()
        self._times = {}
        self._times_started = False
        stacks, i, n = [], 0, 0
        while i < len(rows):
            size = _STACK_SIZES[n % len(_STACK_SIZES)]
            stacks.append(rows[i:i + size])
            i += size
            n += 1
        self._stacks = stacks

    def _assign_flags(self) -> None:
        s = self._s
        out: dict[str, str] = {}
        for r in self._rows():
            if r["decision"] == "win":                  # frames you marked as winners are never flagged
                continue
            reason = _MOCK_FLAGS.get(r["name"]) if self._memory else None
            if not self._memory:
                h = zlib.crc32(r["name"].encode("utf-8", "replace")) % 14
                reason = ("soft", "clipped", "blown_out", "blink", "duplicate")[h] if h < 5 else None
            if reason == "blink" and not s["eyesRan"]:
                reason = None
            if reason:
                out[r["path"]] = reason
        self._flags = out

    def _eta_text(self, s: dict, secs: int) -> str:
        if not self._real:
            return f"about {max(1, round((4 - s['stage'] - s['p']) * secs / 4))} s left"
        done = (s["stage"] + s["p"]) / 4
        if done < 0.05:
            return "working out the time left"
        left = (time.monotonic() - self._t0) * (1 - done) / done
        return f"about {int(left // 60)} min {int(left % 60):02d} s left" if left >= 90 else f"about {max(1, round(left))} s left"

    def _active_flags(self) -> list[tuple[dict, str]]:
        s = self._s
        by_path = {r["path"]: r for r in self._rows()}
        return [(by_path[p], r) for p, r in self._flags.items() if p in by_path and not s["restored"].get(p)]

    def _apply_ids(self) -> list[str]:
        return [r["path"] for r, reason in self._active_flags() if reason in APPLIABLE and self._s["inc"].get(reason)]

    def flagFor(self, path: str) -> dict:
        reason = self._flags.get(path)
        if not self._s["done"] or not reason or self._s["restored"].get(path):
            return {}
        label, color, _rej = REASONS[reason]
        return {"label": label, "color": color}

    def flagInfo(self, path: str) -> dict:
        """The reason this frame is flagged for (label, colour) while the flag stands, else nothing."""
        reason = self._flags.get(path)
        if not self._s["done"] or not reason or self._s["restored"].get(path):
            return {}
        row = next((r for r in self._rows() if r["path"] == path), None)
        if row is None or row["decision"]:
            return {}
        return {"label": REASONS[reason][0], "color": REASONS[reason][1]}

    @Slot(str, bool)
    def restorePath(self, path: str, on: bool) -> None:
        if self._flags.get(path):
            r = dict(self._s["restored"])
            r[path] = on
            self._set(restored=r)

    @Slot(str)
    def reviewFrame(self, path: str) -> None:
        self._set(window="cull", hidden=False, page="rev", sel=path, filter="all")

    @Slot(str, result="QVariantMap")
    def gridFlag(self, path: str) -> dict:
        """What the grid draws on a flagged frame: its reason and colour (nothing for a frame that was decided, restored or not flagged)."""
        row = next((r for r in self._rows() if r["path"] == path), None)
        if row is None or row["decision"]:
            return {}
        return self.flagFor(path)

    def _reason_text(self, path: str, reason: str) -> dict:
        if self._real:
            o = self._outcomes.get(path)
            rows = [("Stack", f"{o.stack_size} frame{'s' if o.stack_size != 1 else ''}"), ("Place in its stack", str(o.stack_rank))] if o else []
            return {"why": o.summary if o else "", "rows": [{"k": k, "v": x} for k, x in rows]}
        v = self._s["vals"]
        why = {"blink": f"Eyes closed on the main face. Its blink score is 0.82 and frames are flagged from {v['blink'] / 100:.2f}. Dimmed, not removed.",
               "soft": f"Centre sharpness is 31% of the sharpest frame in its stack; frames are flagged below {v['soft']}%. Dimmed, not removed.",
               "clipped": f"14% of its pixels are clipped, against 4% in the cleanest frame of its stack. Flagged above twice that and above {v['clip']}%.",
               "blown_out": f"47% of its pixels are at maximum. From {v['blown']}% a frame counts as blown out.",
               "duplicate": f"Near-identical to its neighbour: 6 of 64 hash bits differ (suggested from {v['dup']}). The other frame is the more evenly exposed. This is a suggestion, not a rejection."}[reason]
        rows = {"blink": [("Blink score", "0.82"), ("Flagged from", f"{v['blink'] / 100:.2f}")], "soft": [("Sharpness vs best", "31%"), ("Flagged below", f"{v['soft']}%")],
                "clipped": [("Clipped pixels", "14%"), ("Cleanest in stack", "4%")], "blown_out": [("Pixels at maximum", "47%"), ("Blown out from", f"{v['blown']}%")],
                "duplicate": [("Hash distance", "6 of 64"), ("Suggested from", str(v["dup"]))]}[reason]
        return {"why": why, "rows": [{"k": k, "v": x} for k, x in rows]}

    # -- the view the QML draws --------------------------------------------------------------------------------------------------
    def _rebuild(self) -> None:
        s = self._s
        rows = self._rows()
        n = len(rows)
        flagged = self._active_flags()
        cnt = lambda r: sum(1 for _row, x in flagged if x == r)
        apply_ids = self._apply_ids()
        rt_ready = s["rt"] == "ready"
        eyes_on = rt_ready and s["faceOk"] and s["eyes"]
        rank_has = s["models"]["quality"] and s["models"]["appeal"]
        secs = max(2, round(n * 0.25))
        v = s["vals"]
        page = s["page"]
        idx = {"prep": 0, "run": 1, "rev": 2, "apply": 3, "done": 3}[page]
        folder = self._photos.folderName or "this folder"
        steps = [("Prepare", "Cleanup and eyes check" if (s["running"] or s["done"]) and eyes_on else ("Cleanup only" if s["running"] or s["done"] else "Choose what runs")),
                 ("Analyze", "Running…" if s["running"] else ("Finished" if s["done"] else "Not started")),
                 ("Review", f"{len(flagged)} flagged" if s["done"] else "After the analysis"),
                 ("Apply", f"{len(s['applied']['ids'])} applied" if s["applied"] else "Your decision")]
        titles = {"prep": ("Cull this folder", f"{n} photos in {folder}. Finds the frames nobody would keep, groups near-identical shots into stacks, and tells you why. You make every choice."),
                  "run": ("Analyzing…", "Reading previews only. Your photos are not changed."),
                  "rev": ("What it found", "Flagged frames are dimmed in the grid, never hidden or removed. Pick a frame to see why, and restore any you want to keep."),
                  "apply": ("Apply the flags", "Nothing has changed yet. Choose what should happen to the frames below."), "done": ("Applied", "")}
        rank_desc = ("Suggests the strongest one or two frames in each stack, using two AI models for quality and appeal. They are suggestions only: nothing is selected for you. "
                     if rank_has else "Suggests the strongest frames in each stack. The Appeal model is not installed yet. ") + \
                    ("Graphics card found: about 1 second per frame." if s["gpu"] else "No usable graphics card, so it is slow: 7 seconds to over a minute per frame.")
        rt_desc = {"ready": "Finds the faces in each frame and checks whether anyone has their eyes closed, with a face model that runs on this PC, about 40 ms per frame. Cheap, so it runs whenever the AI runtime is set up.",
                   "missing": "Needs the AI runtime, a one-time download. The cleanup works without it.", "installing": "Setting up the AI runtime…",
                   "error": "The AI runtime could not be set up.", "checking": "Looking at what is installed…"}[s["rt"]]
        if rt_ready and not s["faceOk"]:
            rt_desc = "The face model is not installed yet (about 4 MB). Install it in the AI Center and the eyes check runs."
        rtp = round(s["rtp"] * 100)
        rt_step = (self._ai_state["step"] if self._ai_state is not None else
                   ("Downloading the Python runtime" if s["rtp"] < 0.3 else "Installing packages" if s["rtp"] < 0.75 else "Checking that it starts") + f" · {rtp}%")
        est = secs + (2 if eyes_on else 0) + (round(n * (0.8 if s["gpu"] else 7) * 0.4) if s["rank"] and (s["gpu"] or s["cpuDefault"]) and rank_has else 0)
        est_text = f"About {est} seconds for {n} photos" + (", eyes check included" if eyes_on else "") + ("" if s["rank"] else ". Ranking is off")
        stage_right = lambda i: "Done" if s["stage"] > i else (f"{round(s['p'] * n)} of {n}" if s["stage"] == i else ("Skipped" if i == 2 and not eyes_on else ""))
        stages = [{"label": lab, "right": stage_right(i), "state": "done" if s["stage"] > i else ("cur" if s["stage"] == i and s["running"] else ("skip" if i == 2 and not eyes_on else "")),
                   "pp": round(s["p"] * 100) if s["stage"] == i else 0} for i, lab in enumerate(STAGES)]
        run_pct = round(min(1.0, (s["stage"] + s["p"]) / 4) * 100)
        cur_stage = STAGES[min(3, s["stage"])]
        notes = []
        if not eyes_on:
            notes.append("The eyes-closed check is switched off for this run." if rt_ready and s["faceOk"] else
                         "The eyes-closed check was skipped: the face model is not installed. The cleanup runs without it." if rt_ready else
                         "The eyes-closed check was skipped: the AI runtime is not set up. The cleanup runs without it.")
        if not s["rank"]:
            notes.append("Ranking is off. Cleanup and stacks only.")
        # review
        chips = [{"key": "all", "label": "All flags", "n": len(flagged), "color": "", "dot": False}]
        chips += [{"key": r, "label": REASONS[r][0], "n": cnt(r), "color": REASONS[r][1], "dot": True} for r in REASONS if cnt(r)]
        for c in chips:
            c["on"] = s["filter"] == c["key"]
        stacks_out = []
        if page in ("rev", "apply"):
            shown = 0
            for si, st in enumerate(self._stacks):
                have = [x for x in st if self._flags.get(x["path"]) and not s["restored"].get(x["path"])]
                if s["filter"] != "all" and not any(self._flags.get(x["path"]) == s["filter"] and not s["restored"].get(x["path"]) for x in st):
                    continue
                if len(self._stacks) > 40 and not have and s["filter"] == "all":
                    continue
                frames = []
                firsts = [x["path"] for x in st if not (self._flags.get(x["path"]) and not s["restored"].get(x["path"]))]
                for x in st:
                    reason = self._flags.get(x["path"])
                    on = bool(reason) and not s["restored"].get(x["path"])
                    frames.append({"path": x["path"], "name": x["name"], "flagged": on, "badge": REASONS[reason][0] if on else "", "color": REASONS[reason][1] if on else "",
                                   "restored": bool(reason) and not on, "sel": s["sel"] == x["path"],
                                   "rank": "1st choice" if ((x["path"] in self._picks and not on) if self._real else (s["ranked"] and len(st) > 1 and not on and bool(firsts) and x["path"] == firsts[0])) else ""})
                stacks_out.append({"title": f"Stack {si + 1}", "meta": f"{len(st)} {'frame' if len(st) == 1 else 'frames'}" + (f" · {self._frame_time(st[0]['path'])}" if self._frame_time(st[0]['path']) else ""), "frames": frames})
                shown += 1
                if shown >= 300:
                    break
        det = {"has": False, "path": "", "flagged": False, "color": "", "name": "", "title": "", "why": "", "rows": [], "canRestore": False, "restoreLabel": ""}
        sel = next((r for r in rows if r["path"] == s["sel"]), None)
        if sel is not None:
            reason = self._flags.get(sel["path"])
            on = bool(reason) and not s["restored"].get(sel["path"])
            si = next((i for i, st in enumerate(self._stacks) if any(x["path"] == sel["path"] for x in st)), 0)
            info = self._reason_text(sel["path"], reason) if reason else {"why": "", "rows": []}
            lab = REASONS[reason][0] if reason else ""
            det = {"has": True, "path": sel["path"], "flagged": on, "color": REASONS[reason][1] if on else "", "name": sel["name"],
                   "title": sel["name"] + " · " + (lab if on else ("Restored" if reason else "Looks fine")),
                   "why": info["why"] if on else ("You restored this frame. It is no longer flagged or dimmed." if reason else
                                                  (self._outcomes[sel["path"]].summary if self._real and sel["path"] in self._outcomes else "No rule flagged this frame.")
                                                  + (" It is the suggested first choice of its stack. Nothing is selected for you." if (sel["path"] in self._picks if self._real else s["ranked"]) else "")),
                   "rows": info["rows"] + [{"k": "Stack", "v": f"{si + 1} of {len(self._stacks)}"}, {"k": "Taken", "v": self._frame_time(sel["path"])}],
                   "canRestore": bool(reason), "restoreLabel": "Flag again" if s["restored"].get(sel["path"]) else ("Keep both" if reason == "duplicate" else "Restore")}
        groups = [{"key": r, "label": REASONS[r][0], "color": REASONS[r][1], "n": cnt(r), "why": GROUP_WHY[r], "on": bool(s["inc"].get(r)),
                   "thumbs": [row["path"] for row, x in flagged if x == r][:4]} for r in APPLIABLE if cnt(r)]
        applied = s["applied"]
        done_title = "Nothing was changed" if not applied else (f"{len(applied['ids'])} frames moved to the recycle bin" if applied["act"] == "bin" and self._real and self._files is not None else f"{len(applied['ids'])} frames marked as Rejects")
        binned = bool(applied) and applied["act"] == "bin" and self._real and self._files is not None
        done_body = ("They are in the program’s recycle bin; Undo brings them back. Your winners were not touched." if binned else "They are marked red in the grid and listed under Rejected. Your winners were not touched."
                     + (" Moving files to the recycle bin is not connected yet, so they were rejected instead." if applied and applied["act"] == "bin" and not (self._real and self._files is not None) else ""))
        opts = [{"key": "reject", "label": "Mark them as Rejects", "desc": "Sets their decision to Reject. The files stay where they are, and Ctrl+Z undoes it.", "rec": True},
                {"key": "bin", "label": "Move them to the recycle bin", "desc": "Moves each file, with its paired RAW or JPG and sidecars, to the program’s recycle bin. You can restore them from there." + ("" if self._real and self._files is not None else " (Not connected yet: they are marked as Rejects.)"), "rec": False},
                {"key": "none", "label": "Keep them as flags only", "desc": "Nothing changes on disk or in your decisions. They stay dimmed in the grid.", "rec": False}]
        for o in opts:
            o["on"] = s["act"] == o["key"]
        n_ap = len(apply_ids)
        self._view = {
            "window": s["window"], "page": page, "title": titles[page][0], "sub": titles[page][1], "folder": folder,
            "steps": [{"n": i + 1, "label": lab, "sub": sub, "state": "done" if (page == "done" and i == 3) or i < idx else ("cur" if i == idx else "")} for i, (lab, sub) in enumerate(steps)],
            "centerDot": "ok" if rt_ready else ("bad" if s["rt"] == "error" else "warn"),
            "centerText": {"ready": "Ready", "missing": "Not set up", "installing": "Installing…", "error": "Needs attention", "checking": "Checking…"}[s["rt"]],
            "eyes": {"on": eyes_on, "desc": rt_desc},
            "semantic": {"on": s["semantic"] and rt_ready, "desc": (
                "Reads every photo once with a small image model on this PC, so you can search the folder in plain words afterwards "
                "(\"red lanterns\", \"a dog on a beach\"). It runs in the background after the cleanup and does not hold up the review."
                if rt_ready else "Needs the AI runtime, a one-time download.")},
            "rt": {"state": s["rt"], "pct": rtp, "step": rt_step,
                **self._rt_error()},
            "rank": {"on": s["rank"], "desc": rank_desc, "canSetup": not rank_has, "ask": s["cpuAsk"]},
            "sens": [{"key": k, "label": k.capitalize(), "on": s["sens"] == k} for k in ("careful", "balanced", "strict")], "sensText": SENS_TEXT[s["sens"]],
            "tuneOpen": s["tune"], "tuneLabel": "Hide fine-tuning" if s["tune"] else "Fine-tune",
            "tunes": [{"key": k, "label": lab, "help": h, "min": lo, "max": hi, "raw": v[k], "val": _fmt(k, v[k])} for k, lab, h, lo, hi in TUNES],
            "estimate": est_text, "stages": stages, "runLine": f"{cur_stage} · {round(s['p'] * n)} of {n}", "runPct": run_pct,
            "runEta": self._eta_text(s, secs), "notes": notes,
            "tiles": [{"n": n, "l": "Frames"}, {"n": len(self._stacks), "l": "Stacks"}, {"n": sum(cnt(r) for r in REJECTS), "l": "Flagged"}, {"n": cnt("duplicate"), "l": "Near-duplicates"},
                      self._people_tile()],
            "chips": chips, "stacks": stacks_out, "noRows": s["filter"] != "all" and cnt(s["filter"]) == 0, "det": det,
            "revNote": "Ranking suggestions are marked 1st choice. Nothing is selected for you." if s["ranked"] else "Cleanup only. Turn on ranking in Run again to get suggestions.",
            "applyCount": n_ap, "opts": opts, "groups": groups,
            "applyLabel": (f"Move {n_ap} to the recycle bin" if s["act"] == "bin" and self._real and self._files is not None else f"Mark {n_ap} as Rejects") if s["act"] in ("reject", "bin") else "Keep as flags",
            "applyNote": "Nothing is deleted. You can undo this." if s["act"] != "none" else "Nothing will change.",
            "doneTitle": done_title, "doneBody": done_body, "canUndo": bool(applied),
            "mini": s["running"] and s["hidden"] and s["window"] == "", "miniLine": f"{cur_stage} · {round(s['p'] * n)} of {n}",
            "center": self._center(s, rt_ready, rtp, rt_step, flagged, applied) if self._ai_state is not None else {"rtDot": "ok" if rt_ready else ("bad" if s["rt"] == "error" else "warn"), "rtState": {"ready": "Installed", "missing": "Not set up", "installing": "Installing…", "error": "Needs attention", "checking": "Checking…"}[s["rt"]],
                       "rtInstalling": s["rt"] == "installing", "rtPct": rtp, "rtStep": rt_step,
                       "rtDesc": {"ready": "Python 3.13 with the packages the cull needs. It lives in Image Triage’s own folder and can be removed at any time.",
                                  "missing": "The cleanup works without it. The eyes-closed check and ranking need it: a one-time download of about a minute.", "installing": "Setting up…",
                                  "error": "It was installed but does not start on this PC."}[s["rt"]],
                       "rtBtn": "Cancel" if s["rt"] == "installing" else ("Set up AI" if s["rt"] == "missing" else "Repair"), "rtPrimary": s["rt"] == "missing", "rtBtnShow": True,
                       "gpuDot": "ok" if s["gpu"] else "warn", "gpuState": "Found" if s["gpu"] else "Not found",
                       "gpuDesc": "NVIDIA GeForce RTX 4070. Ranking takes about 1 second per frame." if s["gpu"] else "Ranking would run on the CPU: 7 seconds to over a minute per frame. The cleanup and the eyes check do not need a graphics card.",
                       "vcShow": s["vcOld"], "vcFound": "Visual C++ 14.36 (2023)", "vcNeeded": "14.40",
                       "models": [self._sample_model(k, name, on, desc) for k, name, on, desc in (
                           ("face", "Face model (eyes-closed check)", rt_ready, "MediaPipe face landmarks. Runs on the CPU."),
                           ("quality", "Quality ranker", s["models"]["quality"], "Scores technical quality. Downloaded on first use."),
                           ("appeal", "Appeal ranker", s["models"]["appeal"], "Scores how appealing a frame is. Downloaded on first use; best with a graphics card."))],
                       "cpu": s["cpuDefault"], "defSens": s["sens"], "runs": self._runs(flagged, applied) if s["runs"] else [], "noRuns": not s["runs"]},
        }
        self._rev += 1
        self.changed.emit()
        self.gridChanged.emit()

    def _people_tile(self) -> dict:
        """How many people the folder's face index holds, and how many faces they come from: the way to tell whether face detection did anything.
        Read from the same database the People page uses (the Widgets app's folder database); 0 when the folder has no face index yet."""
        folder = self._photos.folder_dir() if self._real else ""
        people = faces = 0
        if folder and self._s["done"]:
            try:
                import sqlite3

                from ..aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths
                from ..people_search import list_person_clusters

                db = aiculler_db_path(build_aiculler_workflow_paths(folder, resolve=False))
                stamp = (str(db), db.stat().st_mtime_ns) if db.exists() else None
                cached = getattr(self, "_people_cache", None)
                if stamp is not None and cached is not None and cached[0] == stamp:
                    people, faces = cached[1]
                elif stamp is not None:
                    connection = sqlite3.connect(str(db), timeout=2)
                    try:
                        clusters = list(list_person_clusters(connection))
                    finally:
                        connection.close()
                    people, faces = len(clusters), sum(c.face_count for c in clusters)
                    self._people_cache = (stamp, (people, faces))
            except Exception:
                people = faces = 0
        looking = getattr(self, "_face_task", None) is not None or getattr(self, "_semantic_task", None) is not None
        return {"n": people, "l": "People" + (f" · {faces} faces" if faces else (" · looking…" if looking else ""))}

    @staticmethod
    def _clip_files():
        """The search model's graph and tokenizer where the model store put them (the managed install), or (None, None) when it is not installed.
        (`default_aiculler_runtime` points at an older cache folder and cannot be used to find them.)"""
        try:
            from ..ai_model import resolve_aiculler_clip_model_installation

            folder = resolve_aiculler_clip_model_installation().install_dir
            graph, tokenizer = folder / "onnx" / "model.onnx", folder / "tokenizer.json"
            return (graph, tokenizer) if graph.exists() and tokenizer.exists() else (None, None)
        except Exception:
            return None, None

    def _semantic_finished(self, folder: str, token: int, indexed: int, ready_total: int) -> None:
        """The folder is indexed: the face pass reuses those embeddings (a cheap person pre-filter), then finds and clusters the faces."""
        if token != getattr(self, "_semantic_token", -1):
            return
        self._semantic_task = None
        if getattr(self, "_face_wanted", False):
            self._start_face(folder)

    def _start_face(self, folder: str) -> None:
        try:
            from pathlib import Path

            from PySide6.QtCore import QThreadPool

            from ..aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths
            from ..face_index import FaceFolderIndexTask
            from ..face_stack import face_cache_path

            paths = build_aiculler_workflow_paths(folder, resolve=False)
            db = aiculler_db_path(paths)
            text_model, tokenizer = self._clip_files()
            if not db.exists() or text_model is None:
                return
            previous = getattr(self, "_face_task", None)
            if previous is not None:
                previous.cancel()
            self._face_token = getattr(self, "_face_token", 0) + 1
            task = FaceFolderIndexTask(
                folder=folder, token=self._face_token, db_path=db, clip_text_model=text_model, clip_tokenizer=tokenizer,
                clip_fallback_text_model=None, device="auto", face_cache_path=face_cache_path(paths.artifacts_dir),
            )
            task.signals.finished.connect(self._face_finished)
            task.signals.failed.connect(self._extras_failed)
            self._face_task = task
            if getattr(self, "_face_pool", None) is None:
                self._face_pool = QThreadPool()
                self._face_pool.setMaxThreadCount(1)
            self._face_pool.start(task)
            self._rebuild()                         # the People tile says it is looking
        except Exception:
            self._face_task = None

    def _face_finished(self, folder: str, token: int, faces_indexed: int, people_count: int) -> None:
        if token != getattr(self, "_face_token", -1):
            return
        self._face_task = None
        self._people_cache = None
        self._rebuild()                             # the People tile now counts them

    def _extras_failed(self, folder: str, token: int, message: str) -> None:
        self._semantic_task = None
        self._face_task = None
        self._rebuild()

    def _rt_error(self) -> dict:
        """The runtime's failure card. On a real PC it says what this PC has (the Visual C++ complaint only when the Visual C++ runtime really is
        too old, and the runtime's own error otherwise); the scripted sample keeps its fixed wording."""
        a = self._ai_state
        if a is None:
            return {"errVc": True, "errTitle": "Windows needs a newer Visual C++ runtime",
                    "errBody": "The AI runtime could not start. This PC has Visual C++ 14.36 (2023); it needs 14.40 or newer. Install the latest x64 Redistributable, then check again."}
        if a["vcOld"]:
            return {"errVc": True, "errTitle": "Windows needs a newer Visual C++ runtime",
                    "errBody": f"The AI runtime could not start. This PC has Visual C++ {a['vcFound']}; it needs 14.40 or newer. Install the latest x64 Redistributable, then check again."}
        return {"errVc": False, "errTitle": "The AI runtime could not start",
                "errBody": a["error"] or "It was installed but does not start on this PC. Repair reinstalls it; the AI Center shows what failed."}

    def _center(self, s: dict, rt_ready: bool, rtp: int, rt_step: str, flagged, applied) -> dict:
        """The AI Center for this PC: every state, size and sentence comes from `ai_center.AiCenterBackend.state()`."""
        a = self._ai_state
        rt = a["rt"]
        gpu_s = round(MANIQA.seconds_per_frame_gpu + QREALIGN.seconds_per_frame_gpu)
        if a["gpuUsable"]:
            gpu_desc = f"{a['gpuName']}. Ranking takes about {gpu_s} second{'s' if gpu_s != 1 else ''} per frame."
            gpu_tone = "ok"
        elif a["gpuFound"] and a["variant"] == "cpu":
            gpu_desc = f"{a['gpuName']} was found, but the installed AI runtime is the CPU build, so ranking runs on the CPU. Repair the runtime to add the graphics card build."
            gpu_tone = "warn"
        elif a["gpuFound"]:
            gpu_desc = f"{a['gpuName']}. Set up AI installs the build that uses it, and ranking takes about {gpu_s} second{'s' if gpu_s != 1 else ''} per frame."
            gpu_tone = "ok"
        else:
            gpu_desc = (f"Ranking would run on the CPU: {CPU_SECONDS_PER_FRAME_FAST} seconds to over a minute per frame. "
                        "The cleanup and the eyes check do not need a graphics card.")
            gpu_tone = "warn"
        python = a["python"]
        rt_desc = {"ready": f"Python {python} with the packages the cull needs. It lives in Image Triage’s own folder and can be removed at any time.",
                   "missing": "The cleanup works without it. The eyes-closed check and ranking need it: a one-time download that Set up AI shows the size of first.",
                   "installing": "Setting up. What was already downloaded is kept if you cancel.",
                   "error": a["error"] or "It was installed but does not start on this PC." if not a["vcOld"] else
                            f"It was installed but does not start: this PC has Visual C++ {a['vcFound']} and the runtime needs 14.40 or newer.",
                   "checking": "Looking at what is installed…"}[rt]
        models = []
        for key, name, desc in (
                ("face", "Face model (eyes-closed check)", "MediaPipe face landmarks. Runs on the CPU."),
                ("quality", "Quality ranker", "Scores technical quality (MANIQA)."),
                ("appeal", "Appeal ranker", "Scores how appealing a frame is (Q-ReAlign). Best with a graphics card.")):
            m = a["models"][key]
            size = format_mb(m["mb"]) if m["mb"] else ""
            ready = m["state"] == "ready"
            if m["busy"]:
                state, tone = m["verb"] + "…", "warn"
            elif m["error"]:
                state, tone = "Failed", "bad"
            else:
                state, tone = STATE_WORDS.get(m["state"], "Not installed"), "ok" if ready else ("warn" if m["state"] == "missing" else "bad")
            text = desc + (f" {size}." if size and key == "face" else (f" {size}, downloaded when you install it." if size else ""))
            if m["error"]:
                text = m["error"]
            elif not ready and m["state"] not in ("missing",) and m["problem"]:
                text = m["problem"] + " Repair downloads it again."
            elif key == "face" and rt != "ready" and ready:
                text += " It runs once the AI runtime is set up."
            models.append({"key": key, "name": name, "on": ready, "desc": text, "state": state, "tone": tone, "size": size,
                           "btn": "Cancel" if m["busy"] else ("Try again" if m["error"] else ("Install" if m["state"] == "missing" else ("Repair" if (not ready or key == "face") else "Remove"))),
                           "primary": not m["busy"] and (m["state"] == "missing" or bool(m["error"])), "busy": m["busy"], "pct": m["pct"], "step": m["step"],
                           "confirm": ready and key != "face" and not m["busy"], "locked": a["busy"] and not m["busy"]})
        return {"rtDot": {"ready": "ok", "error": "bad"}.get(rt, "warn"),
                "rtState": {"ready": "Installed", "missing": "Not set up", "installing": "Installing…", "error": "Needs attention", "checking": "Checking…"}[rt],
                "rtInstalling": rt == "installing", "rtPct": a["pct"], "rtStep": a["step"], "rtDesc": rt_desc,
                "rtBtn": "Cancel" if rt == "installing" else ("Set up AI" if rt == "missing" else "Repair"), "rtPrimary": rt == "missing", "rtBtnShow": rt != "checking" and not (a["busy"] and rt != "installing"),
                "gpuDot": "ok" if gpu_tone == "ok" else "warn", "gpuState": "Found" if a["gpuFound"] else "Not found", "gpuDesc": gpu_desc,
                "vcShow": a["vcOld"], "vcFound": f"Visual C++ {a['vcFound']}", "vcNeeded": "14.40",
                "models": models, "cpu": s["cpuDefault"], "defSens": s["sens"] if self._settings is None else str(self._settings.value(SENS_KEY, "balanced", str)),
                "runs": self._runs(flagged, applied) if s["runs"] else [], "noRuns": not s["runs"] or not self._runs(flagged, applied)}

    @staticmethod
    def _sample_model(key: str, name: str, on: bool, desc: str) -> dict:
        """A model row of the scripted AI Center, in the shape the real one has."""
        return {"key": key, "name": name, "on": on, "desc": desc, "state": "Installed" if on else "Not installed", "tone": "ok" if on else "warn",
                "btn": ("Repair" if key == "face" else "Remove") if on else "Install", "primary": not on, "busy": False, "pct": 0, "step": "", "confirm": on and key != "face"}

    def _runs(self, flagged, applied) -> list[dict]:
        cur = {"folder": self._photos.folderName or "This folder", "when": "Today", "frames": len(self._rows()), "flagged": len(flagged) if self._s["done"] else 0,
               "did": (f"Applied: {len(applied['ids'])} marked Rejects" if applied else ("Waiting for you" if self._s["done"] else "Not run yet")), "current": True}
        if self._ai is not None:                  # the real AI Center: only the run made in this window, not invented earlier ones
            return [cur] if self._s["done"] or applied else []
        return [cur, {"folder": "Georgia trip / Batumi", "when": "Yesterday", "frames": 188, "flagged": 23, "did": "Restored 4, rejected 19", "current": False},
                {"folder": "Wedding 2026", "when": "Mon 12 Oct", "frames": 1284, "flagged": 142, "did": "Marked 131 as Rejects", "current": False}]

    view = Property("QVariantMap", lambda self: self._view, notify=changed)
    flagRev = Property(int, lambda self: self._rev, notify=gridChanged)

    def _set(self, **kw) -> None:
        self._s.update(kw)
        self._rebuild()

    # -- windows -----------------------------------------------------------------------------------------------------------------
    @Slot()
    def openCull(self) -> None:
        s = self._s
        page = "run" if s["running"] else ("rev" if s["done"] else "prep")
        self._set(window="cull", hidden=False, page=page, back=False)

    @Slot()
    def openReview(self) -> None:
        self._set(window="cull", hidden=False, page="rev" if self._s["done"] else "prep")

    @Slot()
    def openCenter(self) -> None:
        self._set(window="center", back=self._s["window"] == "cull")

    @Slot()
    def close(self) -> None:
        self._install.stop()
        if self._s["window"] == "center" and self._s["back"]:
            self._set(window="cull", back=False)
        else:
            self._set(window="", cpuAsk=False, back=False)

    @Slot()
    def hide(self) -> None:
        self._set(window="", hidden=True)

    # -- prepare ----------------------------------------------------------------------------------------------------------------
    @Slot()
    def toggleEyes(self) -> None:
        self._set(eyes=not self._s["eyes"])

    @Slot()
    def toggleRank(self) -> None:
        s = self._s
        s["rankTouched"] = True
        if s["rank"]:
            self._set(rank=False)
        elif not s["gpu"] and not s["cpuDefault"]:
            self._set(cpuAsk=True)
        else:
            self._set(rank=True)

    @Slot()
    def toggleSemantic(self) -> None:
        self._set(semantic=not self._s["semantic"])

    @Slot(bool)
    def answerCpu(self, yes: bool) -> None:
        self._s["rankTouched"] = True
        self._set(rank=yes, cpuAsk=False)

    @Slot(str)
    def setSens(self, key: str) -> None:
        if key in PRESETS:
            self._set(sens=key, vals=dict(PRESETS[key]))

    @Slot()
    def toggleTune(self) -> None:
        self._set(tune=not self._s["tune"])

    @Slot(str, int)
    def setVal(self, key: str, value: int) -> None:
        vals = dict(self._s["vals"])
        vals[key] = int(value)
        self._set(vals=vals)

    @Slot()
    def resetTune(self) -> None:
        self._set(sens="balanced", vals=dict(PRESETS["balanced"]))

    @Slot(bool)
    def setCpuDefault(self, on: bool) -> None:
        if self._settings is not None:
            self._settings.setValue(CPU_KEY, on)
        self._set(cpuDefault=on)

    @Slot(str)
    def setDefaultSens(self, key: str) -> None:
        """The AI Center's "How strict by default": kept for the next run, and the Cull window moves to it too."""
        if key in PRESETS:
            if self._settings is not None:
                self._settings.setValue(SENS_KEY, key)
            self.setSens(key)

    # -- the AI runtime and the models: real in the app (ai_center.py), scripted in --mock and the tests -------------------------------
    @Slot(result="QVariantMap")
    def setupPlan(self) -> dict:
        """The words of the card that asks before the AI runtime is downloaded: what it is, and how big."""
        repair = self._s["rt"] in ("ready", "error")
        if self._ai is None:
            mb, gpu = 2900, False
        else:
            plan = self._ai.runtime_plan()
            mb, gpu = plan["mb"], plan["variant"] == "gpu"
        size = format_mb(mb)
        build = "the build that uses your graphics card" if gpu else "the CPU build"
        if repair:
            return {"title": "Repair the AI runtime?", "confirm": "Repair", "body": f"This installs it again from scratch: a download of about {size} ({build}, with PyTorch). Your photos, "
                                                                                    "decisions and results are not touched, and the runtime you have now stays until the new one works."}
        return {"title": "Set up AI?", "confirm": "Download and set up", "body": f"This downloads about {size} ({build}, with PyTorch for the ranking models, and the face model for the eyes check) "
                                                                               "and installs it in Image Triage’s own folder. Nothing is uploaded, and you can remove it at any time."}

    @Slot()
    def setupRuntime(self) -> None:
        if self._ai is not None:
            if self._s["rt"] in ("ready", "error"):
                self._ai.repairRuntime()
            else:
                self._ai.setupRuntime()
            return
        self._set(rt="installing", rtp=0.0)
        self._install.start()

    @Slot(str)
    def modelAction(self, key: str) -> None:
        """A model row's button: install, repair or remove (the real ones), or the mockup's scripted toggle."""
        if self._ai is not None:
            self._ai.modelAction(key)
        else:
            self.toggleModel(key)

    def _model_removal_text(self, key: str) -> dict:
        """What the confirm card says before a ranker's files are deleted."""
        row = next((m for m in self._view.get("center", {}).get("models", []) if m["key"] == key), None)
        return {"title": f"Remove the {row['name'] if row else 'model'}?", "body": f"This deletes its files from this PC{' and frees ' + row['size'] if row and row.get('size') else ''}. Your photos are not touched, and you can install it again any time."}

    @Slot(str, result="QVariantMap")
    def removeText(self, key: str) -> dict:
        return self._model_removal_text(key)

    def _install_tick(self) -> None:
        p = self._s["rtp"] + 0.06
        if p >= 1:
            self._install.stop()
            self._set(rt="error" if self._s["vcOld"] else "ready", rtp=1.0)
        else:
            self._set(rtp=p)

    @Slot()
    def cancelSetup(self) -> None:
        if self._ai is not None:
            self._ai.cancel()
            return
        self._install.stop()
        self._set(rt="missing", rtp=0.0)

    @Slot()
    def recheckRuntime(self) -> None:
        if self._ai is not None:
            self._ai.recheck()
            return
        self._set(rt="ready", vcOld=False)

    @Slot(str)
    def toggleModel(self, key: str) -> None:
        if key == "face":
            self.setupRuntime()
            return
        m = dict(self._s["models"])
        m[key] = not m[key]
        self._set(models=m)

    @Slot()
    def clearRuns(self) -> None:
        """Clear all results. In the app that forgets what this window found and applied (the frames you rejected stay rejected: your decisions are not results)."""
        if self._ai is not None:
            self._timer.stop()
            self._flags, self._outcomes, self._picks, self._stacks, self._real = {}, {}, set(), [], False
            self._set(runs=False, done=False, running=False, applied=None, restored={}, sel="", stage=0, p=0.0, page="prep", ranked=False)
            return
        self._set(runs=False)

    # -- the run (scripted) ------------------------------------------------------------------------------------------------------
    @Slot()
    def start(self) -> None:
        s = self._s
        if self._run is not None and any(r.get("record") is not None for r in self._photos.all_rows()):
            self._start_real()
            return
        self._real = False
        self._build_stacks()
        self._set(page="run", running=True, done=False, stage=0, p=0.0, hidden=False, restored={}, applied=None, ranked=s["rank"], eyesRan=s["rt"] == "ready" and s["faceOk"] and s["eyes"], sel="", filter="all")
        self._timer.start()

    def _tick(self) -> None:
        s = self._s
        if not s["running"]:
            self._timer.stop()
            return
        p, st = s["p"] + 0.09, s["stage"]
        if p >= 1:
            p, st = 0.0, st + 1
            if st == 2 and not (s["eyesRan"]):
                st = 3
        if st >= 4:
            self._timer.stop()
            self._assign_flags()
            first = next((r for r, _x in self._active_flags()), None)
            self._set(running=False, done=True, stage=4, p=1.0, page=s["page"] if s["hidden"] else "rev", sel=first["path"] if first else "")
            return
        self._set(stage=st, p=p)


    # -- the real run: the Widgets app's cascade ---------------------------------------------------------------------------------
    def _ranking_decision(self):
        """Which of the eyes check and the two rankers run: the Widgets' own rule (`cascade.launch.decide_ranking`) with what this window's switches say."""
        from dataclasses import replace

        from ..cascade.launch import decide_ranking

        s = self._s
        a = self._ai_state or {}
        device = ("cuda" if s["gpu"] else "cpu") if s["rt"] == "ready" else None
        decision = decide_ranking(device, bool(s["rank"] and not s["gpu"]), quality_installed=bool(s["models"]["quality"]), appeal_installed=bool(s["models"]["appeal"]))
        if not (s["rank"] and decision.use_rankers):
            decision = decision.without_ranking(decision.note if s["rank"] else "")
        if not (s["eyes"] and s["faceOk"]):
            decision = replace(decision, use_blink=False)
        return replace(decision, ask_cpu_confirmation=False)          # this window asked already

    def _protected_paths(self) -> list[str]:
        """Frames already marked as winners are never flagged (the cascade keeps them, with all their files)."""
        out = []
        for r in self._photos.all_rows():
            if r["decision"] == "win":
                out.append(r["path"])
                out.extend(r.get("companions", ()))
        return out

    def _start_real(self) -> None:
        s = self._s
        v = s["vals"]
        cascade = CascadeSettings(split_jump=v["split"], duplicate_distance=v["dup"], soft_ratio=v["soft"] / 100, clip_floor=v["clip"] / 100, blown_out=v["blown"] / 100)
        records = [r["record"] for r in self._photos.all_rows() if r.get("record") is not None]
        decision = self._ranking_decision()
        self._real, self._outcomes, self._picks, self._flags, self._stacks = True, {}, set(), {}, []
        self._token += 1
        self._t0 = time.monotonic()
        self._set(page="run", running=True, done=False, stage=0, p=0.0, hidden=False, restored={}, applied=None, ranked=decision.use_rankers,
                  eyesRan=decision.use_blink, sel="", filter="all")
        try:
            self._run.start(folder=self._photos.folder_dir(), records=records, cascade=cascade, blink_threshold=v["blink"] / 100, decision=decision,
                            protected=self._protected_paths())
        except Exception as exc:
            self._run_failed(str(exc))
            return
        if (s["semantic"] or s["eyes"]) and s["rt"] == "ready":
            self._face_wanted = bool(s["eyes"] and s["faceOk"])
            self._start_semantic(records)

    def _start_semantic(self, records: list) -> None:
        """Semantic search for the folder: the Widgets app's own background indexer (TinyCLIP embeddings into the folder's aiculler database), started
        beside the cull. Best effort: without the runtime or the image model it stays quiet, and filename search remains the fallback."""
        folder = self._photos.folder_dir()
        if not folder or not records:
            return
        try:
            from pathlib import Path

            from PySide6.QtCore import QThreadPool

            from ..aiculler_workflow import aiculler_db_path, build_aiculler_workflow_paths
            from ..semantic_index import SEMANTIC_EMBEDDING_DIM, SemanticFolderIndexTask, compute_semantic_model_identity

            model, _tokenizer = self._clip_files()
            if model is None:
                return
            fallback = None
            identity = compute_semantic_model_identity(model, embedding_dim=SEMANTIC_EMBEDDING_DIM, fallback_model=fallback)
            previous = getattr(self, "_semantic_task", None)
            if previous is not None:
                previous.cancel()
            self._semantic_token = getattr(self, "_semantic_token", 0) + 1
            task = SemanticFolderIndexTask(
                folder=folder, token=self._semantic_token, records=tuple(records),
                db_path=aiculler_db_path(build_aiculler_workflow_paths(folder, resolve=False)),
                clip_vision_model=model, clip_fallback_model=fallback, model_identity=identity,
                device="auto", expected_dim=SEMANTIC_EMBEDDING_DIM,
            )
            self._semantic_task = task
            task.signals.finished.connect(self._semantic_finished)
            task.signals.failed.connect(self._extras_failed)
            if getattr(self, "_semantic_pool", None) is None:
                self._semantic_pool = QThreadPool()
                self._semantic_pool.setMaxThreadCount(1)
            self._semantic_pool.start(task)
        except Exception:
            pass                                    # the cull itself is not affected

    def _run_stage(self, stage: int, fraction: float) -> None:
        if self._s["running"]:
            self._set(stage=stage, p=fraction)

    def _run_finished(self, result, eyes: bool, ranked: bool) -> None:
        if result is None:
            self._run_failed("The analysis gave no result.")
            return
        s = self._s
        self._apply_outcomes(outcomes_from_result(result))
        first = next((r for r, _x in self._active_flags()), None)
        self._set(running=False, done=True, stage=4, p=1.0, page=s["page"] if s["hidden"] else "rev", sel=first["path"] if first else "", ranked=ranked, eyesRan=eyes)
        self.runFinished.emit()
        if self._toasts is not None and s["hidden"]:
            self._toasts.push("success", "The analysis has finished", f"{len(self._active_flags())} frames are flagged.", "Review", self.openCull)

    def _run_failed(self, message: str) -> None:
        self._real = False
        self._set(running=False, done=False, page="prep", hidden=False, stage=0, p=0.0)
        if self._toasts is not None:
            self._toasts.push("error", "The analysis could not finish", message)

    def _run_cancelled(self) -> None:
        self._real = False
        self._set(running=False, done=False, page="prep", hidden=False, stage=0, p=0.0)

    def _apply_outcomes(self, outcomes: dict) -> None:
        """What the cascade decided becomes the window's flags, stacks and suggestions."""
        rows = self._rows()
        self._outcomes = outcomes
        self._flags = {path: reason for path, o in outcomes.items() if (reason := reason_of(o))}
        self._picks = {path for path, o in outcomes.items() if o.role == "pick"}
        self._stacks = stacks_of(rows, outcomes)
        self._times = {}
        self._times_started = False
        self._real = True

    # -- results saved with the folder -------------------------------------------------------------------------------------------
    def _folder_changed(self) -> None:
        """A folder was opened: forget the last folder's results and look for ones saved beside this folder."""
        if self._s["running"]:
            self._run.cancel()
        self._token += 1
        token = self._token
        self._real, self._outcomes, self._picks, self._flags, self._stacks = False, {}, set(), {}, []
        self._set(done=False, running=False, page="prep", restored={}, applied=None, sel="", filter="all", ranked=False, stage=0, p=0.0)
        folder = self._photos.folder_dir()
        rows = list(self._photos.all_rows())
        if not folder or not rows:
            return

        import threading

        def work() -> None:
            from .ai_scores import load_bundle

            bundle = load_bundle(folder)
            self._savedLoaded.emit(token, outcomes_from_bundle(bundle, rows) if bundle is not None else {})

        threading.Thread(target=work, name="qml-cull-saved", daemon=True).start()

    def _saved_arrived(self, token: int, outcomes: dict) -> None:
        if token != self._token or self._s["running"] or not outcomes:
            return
        self._apply_outcomes(outcomes)
        self._set(done=True, page="prep", stage=4, p=1.0, ranked=any(o.ranked for o in outcomes.values()), eyesRan=any("blink" in o.flags for o in outcomes.values()))

    @Slot()
    def cancelRun(self) -> None:
        if self._run is not None and self._run.running:
            self._run.cancel()                          # the window goes back to Prepare when the run says it has stopped
            return
        self._timer.stop()
        self._set(running=False, page="prep", hidden=False, stage=0, p=0.0)

    @property
    def analyzing(self) -> bool:
        return bool(self._s["running"])

    aiState = Property(int, lambda self: 1 if self._s["running"] else (2 if self._s["done"] else 0), notify=gridChanged)

    # -- review ------------------------------------------------------------------------------------------------------------------
    @Slot(str)
    def setFilter(self, key: str) -> None:
        self._set(filter=key)

    @Slot(str)
    def pick(self, path: str) -> None:
        self._set(sel=path)

    @Slot()
    def toggleRestore(self) -> None:
        path = self._s["sel"]
        if path and self._flags.get(path):
            r = dict(self._s["restored"])
            r[path] = not r.get(path)
            self._set(restored=r)

    @Slot(str, result=int)
    def rowOf(self, path: str) -> int:
        for i in range(self._photos.shown):
            if self._photos.get(i).get("path") == path:
                return i
        return -1

    @Slot()
    def toApply(self) -> None:
        self._set(page="apply")

    @Slot()
    def toReview(self) -> None:
        self._set(page="rev")

    @Slot()
    def rerun(self) -> None:
        self._set(page="prep", done=False)

    @Slot(str)
    def setAct(self, key: str) -> None:
        self._set(act=key)

    @Slot(str)
    def toggleInc(self, reason: str) -> None:
        inc = dict(self._s["inc"])
        inc[reason] = not inc.get(reason)
        self._set(inc=inc)

    @Slot()
    def apply(self) -> None:
        s = self._s
        ids = self._apply_ids()
        if s["act"] == "none":
            self._set(page="rev")
            return
        if s["act"] == "bin" and self._files is not None and self._real:
            entry = self._files.trashPaths(ids, "the analysis's flagged frames")      # one step on the undo list, with its toast
            self._set(applied={"ids": ids, "prev": {}, "act": "bin", "entry": entry}, page="done")
            return
        prev = {}
        for i in range(self._photos.shown):
            d = self._photos.get(i)
            if d.get("path") in ids:
                prev[d["path"]] = d.get("decision", "")
                if d.get("decision") != "rej":
                    self._photos.setDecision(i, "rej")
        self._set(applied={"ids": ids, "prev": prev, "act": s["act"]}, page="done")

    @Slot()
    def undo(self) -> None:
        a = self._s["applied"]
        if not a:
            return
        if a.get("act") == "bin" and a.get("entry"):
            self._photos.undo_entry(a["entry"])
            self._set(applied=None, page="rev")
            return
        for i in range(self._photos.shown):
            d = self._photos.get(i)
            path = d.get("path")
            if path in a["ids"] and d.get("decision") == "rej" and a["prev"].get(path, "") != "rej":
                self._photos.setDecision(i, "rej")           # toggles it off
                if a["prev"].get(path):
                    self._photos.setDecision(i, a["prev"][path])
        self._set(applied=None, page="rev")

    # -- test hook: the mockup's scenes -------------------------------------------------------------------------------------------
    @Slot(str)
    def scene(self, name: str) -> None:
        self._timer.stop()
        self._install.stop()
        cpu = self._s["cpuDefault"]
        self._s = self._defaults(cpu)
        self._build_stacks()
        gpu = {"gpu": True, "models": {"quality": True, "appeal": True}}
        base = {"window": "cull", "page": "rev", "done": True, "stage": 4, "p": 1.0, "eyesRan": True}
        scenes = {
            "cu-prep": {"window": "cull"}, "cu-missing": {"window": "cull", "rt": "missing"}, "cu-installing": {"window": "cull", "rt": "installing", "rtp": 0.46},
            "cu-error": {"window": "cull", "rt": "error", "vcOld": True}, "cu-gpu": dict({"window": "cull", "rank": True}, **gpu), "cu-cpu": {"window": "cull", "cpuAsk": True},
            "cu-tune": {"window": "cull", "tune": True}, "cu-run": {"window": "cull", "page": "run", "running": True, "stage": 1, "p": 0.62},
            "cu-mini": {"running": True, "hidden": True, "stage": 1, "p": 0.62}, "cu-review": base, "cu-eyes": dict(base, filter="blink"),
            "cu-ranked": dict(base, ranked=True, rank=True, **gpu), "cu-restored": dict(base), "cu-apply": dict(base, page="apply"), "cu-bin": dict(base, page="apply", act="bin"),
            "cu-done": dict(base, page="done"), "cu-grid": {"done": True, "eyesRan": True}, "ac-ready": dict({"window": "center"}, **gpu),
            "ac-fresh": {"window": "center", "rt": "missing", "runs": False, "models": {"quality": False, "appeal": False}}, "ac-error": {"window": "center", "rt": "error", "vcOld": True},
        }
        self._s.update(scenes.get(name, {}))
        if self._memory:                       # the mockup's scenes start with these frames undecided
            for i in range(self._photos.shown):
                d = self._photos.get(i)
                if d.get("name") in _MOCK_FLAGS and d.get("decision"):
                    self._photos.setDecision(i, d["decision"])
        if self._s["done"]:
            self._assign_flags()
            flagged = [r for r, _x in self._active_flags()]
            pick = {"cu-eyes": "DSC_4226", "cu-ranked": "DSC_4203", "cu-restored": "DSC_4210"}.get(name)
            first = next((r for r in self._rows() if r["name"] == pick), None) if pick else (flagged[0] if flagged else None)
            if first:
                self._s["sel"] = first["path"]
            if name == "cu-restored" and first:
                self._s["restored"] = {first["path"]: True}
            if name == "cu-done":
                ids = [r["path"] for r in flagged if self._flags[r["path"]] in REJECTS]
                self._s["applied"] = {"ids": ids, "prev": {}, "act": "reject"}
                for i in range(self._photos.shown):
                    if self._photos.get(i).get("path") in ids:
                        self._photos.setDecision(i, "rej")
        self._rebuild()
