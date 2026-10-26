#!/usr/bin/env python3
"""Measure pass 1 of the culling cascade on real folders: keeper recall (how often a frame you KEPT is flagged) and stack diagnostics.

Run it where the original photos live. It uses the app's own extractor (embedded JPEG previews of RAW files, as in production), builds the same
FolderFrame objects as CascadeRunTask, runs ``run_folder`` and never applies winner protection, so the flags are the raw ones.

  python scripts/ai_v5/pass1_audit.py --folders D:\\Photos\\trip1 D:\\Photos\\trip2 --keepers keepers.txt --out audit_out --review-pairs 60

Outputs in --out: report.json, report.md and (with --review-pairs) review.html. See docs/cascade_logic_audit.md, "A sound next measurement".
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import io
import json
import os
import sys
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from image_triage.brackets import BracketDetector  # noqa: E402
from image_triage.cascade import audit  # noqa: E402
from image_triage.cascade.blink import is_blink  # noqa: E402
from image_triage.cascade.extract import FeatureCache, _load_preview, extract_features  # noqa: E402
from image_triage.cascade.folder import FolderFrame, run_folder  # noqa: E402
from image_triage.cascade.model import CascadeSettings, FrameFeatures  # noqa: E402
from image_triage.formats import IMAGE_SUFFIXES, MODEL_SUFFIXES, RAW_SUFFIXES, suffix_for_path  # noqa: E402
from image_triage.metadata import load_capture_metadata  # noqa: E402
from image_triage.models import ImageRecord  # noqa: E402

CONFIDENCE = 0.95
TARGET = 0.995


def norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


# -- inputs ---------------------------------------------------------------------------------------------------------------------------------------


def scan_folders(folders: Sequence[str], recursive: bool) -> dict[str, list[str]]:
    """Folder -> image paths (sorted by name). Non-recursive unless asked; with recursion every directory that holds images is its own folder,
    because the app culls one folder at a time."""
    result: dict[str, list[str]] = {}
    for folder in folders:
        roots = [os.path.abspath(folder)]
        if recursive:
            roots = [dirpath for dirpath, _dirs, _files in os.walk(roots[0])]
        for root in roots:
            try:
                names = os.listdir(root)
            except OSError:
                continue
            found = [
                os.path.join(root, n) for n in names
                if os.path.isfile(os.path.join(root, n)) and suffix_for_path(n) in IMAGE_SUFFIXES and suffix_for_path(n) not in MODEL_SUFFIXES
            ]
            if found:
                result[root] = sorted(found, key=lambda p: os.path.basename(p).casefold())
    return result


def merge_companions(scanned: dict[str, list[str]]) -> tuple[dict[str, list[str]], dict[str, str]]:
    """A RAW and a JPEG with the same name in the same folder are one frame in the app (the RAW is the primary and the JPEG its companion, see ``scanner.py``). Do the same
    here, so a pair is not counted as two frames and a keeper listed by its JPEG still matches. Returns the folders with the companions removed, and companion -> primary."""
    merged: dict[str, list[str]] = {}
    companion_of: dict[str, str] = {}
    for folder, paths in scanned.items():
        raws = {os.path.splitext(os.path.basename(p))[0].casefold(): p for p in paths if suffix_for_path(p) in RAW_SUFFIXES}
        kept: list[str] = []
        for path in paths:
            stem = os.path.splitext(os.path.basename(path))[0].casefold()
            if suffix_for_path(path) not in RAW_SUFFIXES and stem in raws:
                companion_of[norm(path)] = raws[stem]
            else:
                kept.append(path)
        merged[folder] = kept
    return merged, companion_of


def read_keepers(path: str, root: str) -> list[str]:
    text = Path(path).read_text(encoding="utf-8-sig")
    if path.lower().endswith(".json"):
        data = json.loads(text)
        if isinstance(data, dict):
            data = data.get("keepers", list(data))
        items = [str(item) for item in data]
    else:
        items = [line.strip().strip('"') for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return [item if os.path.isabs(item) else os.path.join(root, item) for item in items]


def load_json_map(path: str | None) -> dict[str, float] | None:
    if not path:
        return None
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    return {norm(k): float(v) for k, v in data.items() if v is not None}


# -- frames ---------------------------------------------------------------------------------------------------------------------------------------


class Dataset:
    """Everything read from disk, once: frames per folder, plus the side tables."""

    def __init__(self) -> None:
        self.groups: dict[str, list[FolderFrame]] = {}
        self.scanned: dict[str, list[str]] = {}
        self.unreadable: list[str] = []
        self.missing_time: list[str] = []
        self.metadata_errors: list[str] = []
        self.bracket_keys: list[str] = []
        self.focal: dict[str, float] = {}
        self.features: dict[str, FrameFeatures] = {}
        self.companion_of: dict[str, str] = {}  # a JPEG companion (normalised path) -> its RAW primary

    @property
    def all_paths(self) -> list[str]:
        return [p for paths in self.scanned.values() for p in paths]


def build_dataset(scanned: dict[str, list[str]], cache_path: str | None, faces: dict[str, float] | None, blink: dict[str, float] | None) -> Dataset:
    data = Dataset()
    data.scanned = scanned
    cache = FeatureCache(cache_path) if cache_path else FeatureCache()
    settings_fields = set(FrameFeatures.__dataclass_fields__)
    for folder, paths in scanned.items():
        records = [ImageRecord(p, os.path.basename(p), _size(p), _mtime(p)) for p in paths]
        detector = BracketDetector()
        captured: dict[str, float | None] = {}
        focal: dict[str, float] = {}
        for record in records:
            try:
                metadata = load_capture_metadata(record.path)
            except Exception as error:  # noqa: BLE001 - counted, never fatal
                data.metadata_errors.append(f"{record.path}: {error}")
                captured[record.path] = None
                continue
            detector.remember(metadata)
            value = metadata.captured_at_value
            captured[record.path] = value.timestamp() if value is not None else None
            if metadata.focal_length_value:
                focal[record.path] = float(metadata.focal_length_value)
        bracket_of: dict[str, int] = {}
        next_id = 0
        for index in range(len(records)):
            if records[index].path in bracket_of:
                continue
            group = detector.group_for(records, index)
            if group is None:
                continue
            for member in records[group.start_index: group.end_index]:
                bracket_of[member.path] = next_id
            next_id += 1
        extracted = extract_features([r.path for r in records], cache=cache)
        frames = []
        for record, feature in zip(records, extracted.features):
            if feature is not None:
                extra: dict[str, Any] = {}
                if "focal_mm" in settings_fields and record.path in focal:
                    extra["focal_mm"] = focal[record.path]
                if "face_area" in settings_fields and faces and norm(record.path) in faces:
                    extra["face_area"] = faces[norm(record.path)]
                if blink is not None:
                    extra["blink"] = is_blink(blink.get(norm(record.path), 0.0))
                if extra:
                    feature = replace(feature, **extra)
                data.features[record.path] = feature
            frames.append(FolderFrame(record.path, feature, captured.get(record.path), bracket_of.get(record.path)))
        frames.sort(key=lambda f: (f.captured is None, f.captured or 0.0, f.path.casefold()))  # as CascadeRunTask does
        data.groups[folder] = frames
        data.focal.update(focal)
        for frame in frames:
            if frame.features is None:
                data.unreadable.append(frame.path)
            else:
                if frame.captured is None:
                    data.missing_time.append(frame.path)
                if frame.bracket_id is not None:
                    data.bracket_keys.append(frame.path)
    try:
        cache.save()
    except OSError:
        pass
    return data


def _size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _mtime(path: str) -> int:
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def evaluate(data: Dataset, settings: CascadeSettings) -> list[audit.FrameRow]:
    """Raw flags for every decided frame. run_folder only: the winner-protection step of CascadeRunTask is deliberately NOT applied."""
    rows: list[audit.FrameRow] = []
    for folder, frames in data.groups.items():
        result = run_folder(frames, settings)
        rows.extend(audit.rows_from_outcomes(result.outcomes, [f.path for f in frames], folder))
    return rows


# -- report ---------------------------------------------------------------------------------------------------------------------------------------


def pct(value: float | None, digits: int = 2) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def build_report(args: argparse.Namespace, data: Dataset, settings: CascadeSettings, notes: list[str]) -> tuple[dict[str, Any], list[audit.FrameRow]]:
    keeper_paths = [p for source in (args.keepers or []) for p in read_keepers(source, args.root)]
    scanned_norm = {norm(p): p for p in data.all_paths}
    for companion, primary in data.companion_of.items():
        scanned_norm.setdefault(companion, primary)  # a keeper named by its JPEG companion is the RAW frame
    matched: dict[str, str] = {}
    unmatched: list[str] = []
    for path in keeper_paths:
        hit = scanned_norm.get(norm(path))
        if hit is None:
            unmatched.append(path)
        else:
            matched[norm(path)] = hit
    duplicate_keepers = len(keeper_paths) - len(set(norm(p) for p in keeper_paths))
    keepers = set(matched.values())

    blink_measured = args.blink_scores is not None
    blink_scores = load_json_map(args.blink_scores)
    blink_keys = [p for p in data.all_paths if blink_scores is not None and norm(p) in blink_scores] if blink_measured else None
    rows = evaluate(data, settings)
    decided = {r.key for r in rows if r.role != audit.ROLE_BRACKET}
    unreadable = set(data.unreadable)
    brackets = set(data.bracket_keys)
    keepers_unreadable = sorted(keepers & unreadable)
    keepers_bracket = sorted(keepers & brackets)
    keepers_other = sorted(keepers - decided - unreadable - brackets)
    eligible_keepers = keepers & decided

    per_rule = audit.rule_recall(rows, eligible_keepers, blink_measured=blink_measured, blink_keys=blink_keys)
    outcome = audit.keeper_outcomes(rows, eligible_keepers)
    outcome["union_lower_bound"] = audit.union_lower_bound(outcome, CONFIDENCE)
    rates = audit.flag_rates(rows)

    folders = []
    for folder, frames in data.groups.items():
        sub = [r for r in rows if r.group == folder]
        sub_keepers = {r.key for r in sub} & eligible_keepers
        o = audit.keeper_outcomes(sub, sub_keepers)
        folders.append({
            "folder": folder, "scanned": len(data.scanned[folder]), "readable": sum(1 for f in frames if f.features is not None),
            "unreadable": sum(1 for f in frames if f.features is None), "decided": len([r for r in sub if r.role != audit.ROLE_BRACKET]),
            "keepers": o["eligible"], "union_flagged": o["union_flagged"], "rejected": o["rejected"], "no_pick": o["no_pick"],
            "survived": o["survived"], "flag_rate": audit.flag_rates(sub)["flag_rate"],
        })

    stacks = audit.stacks_from_rows(rows)
    faces = load_json_map(args.faces_json)
    diagnostics = audit.stack_diagnostics(stacks, data.features, focal=data.focal, faces=faces)

    sweep = None
    if args.sweep_json:
        sweep_spec = json.loads(Path(args.sweep_json).read_text(encoding="utf-8-sig"))
        sweep = audit.run_sweep(
            lambda s: evaluate(data, s), settings, sweep_spec, eligible_keepers, full_grid=args.full_grid,
            blink_measured=blink_measured, blink_keys=blink_keys, confidence=CONFIDENCE,
        )

    pairs = []
    if args.review_pairs:
        pairs = audit.select_review_pairs(stacks, data.features, args.review_pairs, focal=data.focal, faces=faces)

    needed = audit.needed_zero_miss(TARGET, CONFIDENCE)
    report = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "settings": dataclasses.asdict(settings),
        "settings_notes": notes,
        "winner_protection_applied": False,
        "coverage": {
            "folders": len(data.groups), "scanned": len(data.all_paths), "readable": len(data.all_paths) - len(unreadable),
            "unreadable": len(unreadable), "unreadable_files": sorted(unreadable), "missing_capture_time": len(data.missing_time),
            "missing_capture_time_files": sorted(data.missing_time), "metadata_errors": data.metadata_errors, "bracket_frames": len(brackets),
            "decided": len(decided), "keepers_listed": len(keeper_paths), "keeper_duplicates_in_list": duplicate_keepers,
            "keepers_matched": len(keepers), "keepers_unmatched": len(unmatched), "keepers_unmatched_paths": unmatched,
            "keepers_unreadable": keepers_unreadable, "keepers_in_brackets": keepers_bracket, "keepers_other_undecided": keepers_other,
            "keepers_eligible": len(eligible_keepers),
        },
        "blink_measured": blink_measured,
        "blink_frames_scored": len(blink_keys) if blink_keys is not None else 0,
        "per_rule": {rule: entry.to_dict(CONFIDENCE) for rule, entry in per_rule.items()},
        "keeper_outcome": outcome,
        "flag_rates": rates,
        "recall_target": {"target": TARGET, "confidence": CONFIDENCE, "needed_zero_miss": needed, "supplied": len(eligible_keepers)},
        "per_folder": folders,
        "stack_diagnostics": {k: v for k, v in diagnostics.items() if k != "stacks"},
        "sweep": sweep,
        "review_pairs": pairs,
        "roles_all_frames": _role_counts(rows),
    }
    return report, rows


def _role_counts(rows: Sequence[audit.FrameRow]) -> dict[str, int]:
    tally: dict[str, int] = {}
    for r in rows:
        tally[r.role] = tally.get(r.role, 0) + 1
    return dict(sorted(tally.items()))


def render_markdown(report: dict[str, Any]) -> str:
    c = report["coverage"]
    out: list[str] = ["# Pass 1 audit", "", f"Generated {report['generated']}. Winner protection was NOT applied: these are the raw flags.", ""]
    if report["settings_notes"]:
        out += [f"- {n}" for n in report["settings_notes"]] + [""]
    out += [
        "## Coverage", "",
        f"- Folders scanned: {c['folders']}",
        f"- Frames scanned: {c['scanned']}",
        f"- Readable: {c['readable']}; unreadable: {c['unreadable']}",
        f"- Missing capture time (readable): {c['missing_capture_time']}",
        f"- Exposure-bracket frames (no pass 1 decision): {c['bracket_frames']}",
        f"- Frames with a decision: {c['decided']}",
        f"- Keepers listed: {c['keepers_listed']} (repeated entries: {c['keeper_duplicates_in_list']}); matched to a scanned file: {c['keepers_matched']}; "
        f"unmatched: {c['keepers_unmatched']}",
        f"- Keepers eligible (matched and decided): {c['keepers_eligible']}; unreadable: {len(c['keepers_unreadable'])}; "
        f"in a bracket: {len(c['keepers_in_brackets'])}; otherwise undecided: {len(c['keepers_other_undecided'])}",
        f"- Metadata read errors: {len(c['metadata_errors'])}",
    ]
    for title, items in (("Unmatched keeper paths", c["keepers_unmatched_paths"]), ("Unreadable files", c["unreadable_files"]),
                         ("Keepers that are unreadable", c["keepers_unreadable"]), ("Keepers inside exposure brackets", c["keepers_in_brackets"])):
        if items:
            out += ["", f"{title}:", ""] + [f"- `{i}`" for i in items]
    out += ["", "## Per-rule keeper recall", "",
            "Recall is the share of eligible keepers the rule did NOT flag. Lower bound: one-sided exact (Clopper-Pearson) 95%. Soft and clipped need a "
            "sibling, so a keeper alone in its stack is not eligible for them.", "",
            "| Rule | Eligible keepers | Flagged | Recall | 95% lower bound |", "|---|---:|---:|---:|---:|"]
    for rule, e in report["per_rule"].items():
        if not e["measured"]:
            out.append(f"| {rule} | not measured (no --blink-scores) | | | |")
        else:
            out.append(f"| {rule} | {e['eligible']} | {e['flagged']} | {pct(e['recall'])} | {pct(e['lower_bound'])} |")
    out += ["", "Keepers each rule flagged (the misses):", ""]
    any_miss = False
    for rule, e in report["per_rule"].items():
        for key in e["misses"]:
            any_miss = True
            out.append(f"- {rule}: `{key}`")
    if not any_miss:
        out.append("- none")
    if not report["blink_measured"]:
        out += ["", "Blink was not measured: no --blink-scores file was supplied, so no frame carries a blink flag and the union below excludes blink."]
    o = report["keeper_outcome"]
    out += [
        "", "## Union, rejection, no-pick and survival", "",
        "Blown out alone is not a rejecting flag. Counts are over eligible keepers.", "",
        "| Measure | Keepers | Share |", "|---|---:|---:|",
        f"| Eligible keepers | {o['eligible']} | |",
        f"| Union of rejecting flags (soft, clipped, blink) | {o['union_flagged']} | {pct(1 - o['union_recall'] if o['union_recall'] is not None else None)} flagged; "
        f"recall {pct(o['union_recall'])}, 95% lower bound {pct(o['union_lower_bound'])} |",
        f"| Actually rejected after the all-flagged fallback | {o['rejected']} | recall {pct(o['rejected_recall'])} |",
        f"| Flagged but kept by the all-flagged fallback | {o['saved_by_all_flagged_fallback']} | |",
        f"| No-pick (whole stack blown out) | {o['no_pick']} | |",
        f"| Final exact-frame survival (pick, final or flagged-kept) | {o['survived']} | {pct(o['survival'])} |",
        f"| Roles | {', '.join(f'{k}: {v}' for k, v in o['roles'].items()) or 'none'} | |",
    ]
    for title, key in (("Union misses", "union_misses"), ("Rejected keepers", "rejected_keys"), ("No-pick keepers", "no_pick_keys"),
                       ("Keepers not surviving to the end (exact frame)", "lost")):
        if o[key]:
            out += ["", f"{title}:", ""] + [f"- `{k}`" for k in o[key]]
    t = report["recall_target"]
    out += [
        "", "## How many keepers are enough", "",
        f"With zero misses, {t['needed_zero_miss']} independent keepers are needed for the one-sided {int(t['confidence'] * 100)}% lower bound to exceed "
        f"{t['target'] * 100:.1f}%; {t['supplied']} eligible keepers were supplied"
        + (f", which is enough only if there are no misses and the keepers are independent." if t["supplied"] >= t["needed_zero_miss"]
           else f", {t['needed_zero_miss'] - t['supplied']} short even with no misses.")
        + " Keepers from one event or burst are not independent, so treat the bound as optimistic.",
    ]
    out += ["", "## Flag rates over every decided frame", "",
            f"- Frames with a rejecting flag: {pct(report['flag_rates']['flag_rate'])}; actually rejected: {pct(report['flag_rates']['reject_rate'])}",
            "- Per rule: " + ", ".join(f"{r} {pct(report['flag_rates'][r + '_rate'])}" for r in audit.RULES),
            f"- Roles over all frames: {', '.join(f'{k}: {v}' for k, v in report['roles_all_frames'].items())}"]
    sweep = report["sweep"]
    if sweep:
        out += ["", "## Threshold sweep", "",
                "Each row re-runs pass 1 with the overrides shown (one at a time unless --full-grid). Recall columns are over the eligible keepers; "
                "flag rate is over all decided frames.", ""]
        if sweep["unknown_fields"]:
            out += [f"Fields this CascadeSettings does not have (skipped): {', '.join(sweep['unknown_fields'])}", ""]
        out += ["| Setting | Union recall | Union lower bound | Flag rate | Reject rate | Survival | " + " | ".join(f"{r} recall" for r in audit.RULES) + " |",
                "|---|---:|---:|---:|---:|---:|" + "---:|" * len(audit.RULES)]
        for e in sweep["entries"]:
            out.append(f"| {e['label']} | {pct(e['union_recall'])} | {pct(e['union_lower_bound'])} | {pct(e['flag_rate'])} | {pct(e['reject_rate'])} | "
                       f"{pct(e['survival'])} | " + " | ".join(pct(e['rule_recall'][r]) for r in audit.RULES) + " |")
    out += ["", "## Per folder", "",
            "| Folder | Scanned | Readable | Unreadable | Decided | Keepers | Keepers flagged (union) | Rejected | No-pick | Survived | Flag rate |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for f in report["per_folder"]:
        out.append(f"| `{f['folder']}` | {f['scanned']} | {f['readable']} | {f['unreadable']} | {f['decided']} | {f['keepers']} | {f['union_flagged']} | "
                   f"{f['rejected']} | {f['no_pick']} | {f['survived']} | {pct(f['flag_rate'])} |")
    d = report["stack_diagnostics"]
    out += ["", "## Stack diagnostics", "",
            f"- Stacks with at least two frames: {d['stacks_with_pairs']}; largest hash diameter: {d['max_diameter']}",
            f"- Stacks with hash diameter >= {d['threshold']} (drifting): {d['drifting_count']}",
            f"- Stacks whose focal-length ratio exceeds {audit.FOCAL_RATIO_LIMIT}: {d['focal_ratio_over_limit']} (needs focal data)",
            f"- Stacks whose face-area ratio exceeds {audit.FACE_RATIO_LIMIT}: {d['face_ratio_over_limit']} (needs --faces-json)", ""]
    if d["drifting"]:
        out += ["| Diameter | Frames | End frame A | End frame B | Focal ratio | Face ratio |", "|---:|---:|---|---|---:|---:|"]
        for e in d["drifting"]:
            out.append(f"| {e['diameter']} | {e['size']} | `{e['endpoints'][0]}` | `{e['endpoints'][1]}` | "
                       f"{'n/a' if e['focal_ratio'] is None else format(e['focal_ratio'], '.2f')} | {'n/a' if e['face_ratio'] is None else format(e['face_ratio'], '.2f')} |")
    else:
        out.append("No drifting stacks.")
    if report["review_pairs"]:
        out += ["", f"{len(report['review_pairs'])} blind review pairs were written to review.html (reasons are in report.json only)."]
    return "\n".join(out) + "\n"


# -- review page ----------------------------------------------------------------------------------------------------------------------------------


def thumbnail_data_uri(path: str, edge: int = 360) -> str:
    try:
        image = _load_preview(path, None)
        if image is None:
            raise OSError("no preview")
        with image:
            image = image.convert("RGB")
            image.thumbnail((edge, edge))
            buffer = io.BytesIO()
            image.save(buffer, "JPEG", quality=72)
    except Exception:  # noqa: BLE001 - a missing thumbnail must not stop the review page
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def render_review(pairs: list[dict[str, Any]]) -> str:
    cache: dict[str, str] = {}
    payload = []
    for pair in pairs:  # the reason is deliberately not included: the labeller must not see what the model or the signals said
        for key in (pair["a"], pair["b"]):
            if key not in cache:
                cache[key] = thumbnail_data_uri(key)
        payload.append({"id": pair["id"], "a": pair["a"], "b": pair["b"], "ia": cache[pair["a"]], "ib": cache[pair["b"]]})
    return REVIEW_PAGE.replace("__PAIRS__", json.dumps(payload).replace("</", "<\\/"))


REVIEW_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Stack review</title>
<style>
  :root{--bg:#0e0f11;--panel:#18191c;--tx:#e7e9ec;--mute:#8a909a;--acc:#4c8dff;--ok:#3fb97a;--no:#e5635b}
  body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,Segoe UI,sans-serif}
  header{padding:12px 18px;border-bottom:1px solid #26282c;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  .mute{color:var(--mute);font-size:13px}
  .bar{flex:1;min-width:160px;height:6px;background:#26282c;border-radius:3px;overflow:hidden} .bar i{display:block;height:100%;background:var(--acc);width:0}
  .pair{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px 18px}
  .pair img{width:100%;border-radius:6px;display:block;background:#222;min-height:120px}
  .keys{display:flex;gap:10px;padding:0 18px 14px;flex-wrap:wrap}
  button{background:var(--panel);color:var(--tx);border:1px solid #26282c;border-radius:8px;padding:10px 18px;font:600 15px system-ui;cursor:pointer}
  button.y{border-color:var(--ok)} button.n{border-color:var(--no)}
  button.sel.y{background:var(--ok);color:#06210f} button.sel.n{background:var(--no);color:#2a0705}
  .help{padding:0 18px 18px;color:var(--mute);font-size:13px;max-width:90ch}
</style></head><body>
<header><b>Would you compare these as alternatives in one stack?</b><span class="mute" id="count"></span><div class="bar"><i id="prog"></i></div>
  <button id="dl">download labels</button></header>
<div class="pair"><img id="L" alt="left"><img id="R" alt="right"></div>
<div class="keys">
  <button class="y" id="by">compare these in one stack</button><button class="n" id="bn">separate stacks</button>
  <button id="prev">&larr; back</button><button id="next">skip &rarr;</button>
</div>
<div class="help">Answer the question "would I look at these two side by side when choosing which to keep?". They need not be duplicates.
Keys: Y = one stack, N = separate stacks, arrow keys move. Progress is kept in this browser; "download labels" saves labels.json.</div>
<script>
const PAIRS=__PAIRS__;let i=0;let labels={};
const store="pass1_review_v1";try{labels=JSON.parse(localStorage.getItem(store)||"{}")}catch(e){}
function persist(){try{localStorage.setItem(store,JSON.stringify(labels))}catch(e){}}
function show(){const p=PAIRS[i];document.getElementById("L").src=p.ia;document.getElementById("R").src=p.ib;
 const done=Object.keys(labels).length;document.getElementById("count").textContent=(i+1)+" of "+PAIRS.length+"   labelled "+done;
 document.getElementById("prog").style.width=(100*done/PAIRS.length)+"%";
 document.getElementById("by").classList.toggle("sel",labels[p.id]==="one_stack");document.getElementById("bn").classList.toggle("sel",labels[p.id]==="separate");}
function set(v){labels[PAIRS[i].id]=v;persist();if(i<PAIRS.length-1)i++;show();}
document.getElementById("by").onclick=()=>set("one_stack");document.getElementById("bn").onclick=()=>set("separate");
document.getElementById("prev").onclick=()=>{if(i>0){i--;show()}};document.getElementById("next").onclick=()=>{if(i<PAIRS.length-1){i++;show()}};
addEventListener("keydown",e=>{const k=e.key.toLowerCase();if(k==="y")set("one_stack");else if(k==="n")set("separate");
 else if(e.key==="ArrowLeft"&&i>0){i--;show()}else if(e.key==="ArrowRight"&&i<PAIRS.length-1){i++;show()}});
document.getElementById("dl").onclick=()=>{const out=PAIRS.map(p=>({pair_id:p.id,a:p.a,b:p.b,label:labels[p.id]||null}));
 const blob=new Blob([JSON.stringify(out,null,1)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="labels.json";a.click();};
if(PAIRS.length)show();else document.getElementById("count").textContent="no pairs";
</script></body></html>
"""


# -- the run, saved ---------------------------------------------------------------------------------------------------------------------------------

FROZEN_SCHEMA = 1


def dataset_to_snapshot(data: Dataset) -> dict[str, Any]:
    """Everything an evaluation needs, so the keepers can be chosen AFTER the run and the same frames re-judged under other settings with no image read."""
    names = [f.name for f in fields(FrameFeatures)]
    folders = []
    for folder, frames in data.groups.items():
        rows = []
        for frame in frames:
            feature = None
            if frame.features is not None:
                feature = {name: getattr(frame.features, name) for name in names}
                feature["phash"] = format(int(frame.features.phash), "x")
            rows.append({"path": frame.path, "features": feature, "captured": frame.captured, "bracket_id": frame.bracket_id})
        folders.append({"folder": folder, "frames": rows})
    return {
        "schema": FROZEN_SCHEMA,
        "folders": folders,
        "focal": data.focal,
        "metadata_errors": data.metadata_errors,
        "companion_of": data.companion_of,
    }


def dataset_from_snapshot(snapshot: dict[str, Any]) -> Dataset:
    if snapshot.get("schema") != FROZEN_SCHEMA:
        raise SystemExit(f"frozen.json has schema {snapshot.get('schema')!r}; this script reads schema {FROZEN_SCHEMA}")
    known = {f.name for f in fields(FrameFeatures)}
    data = Dataset()
    for entry in snapshot["folders"]:
        frames: list[FolderFrame] = []
        for row in entry["frames"]:
            feature = None
            if row["features"] is not None:
                values = {k: v for k, v in row["features"].items() if k in known}
                values["phash"] = int(values["phash"], 16)
                feature = FrameFeatures(**values)
                data.features[row["path"]] = feature
            frame = FolderFrame(row["path"], feature, row["captured"], row["bracket_id"])
            frames.append(frame)
            if feature is None:
                data.unreadable.append(frame.path)
            else:
                if frame.captured is None:
                    data.missing_time.append(frame.path)
                if frame.bracket_id is not None:
                    data.bracket_keys.append(frame.path)
        data.groups[entry["folder"]] = frames
        data.scanned[entry["folder"]] = [f.path for f in frames]
    data.focal = {k: float(v) for k, v in snapshot.get("focal", {}).items()}
    data.metadata_errors = list(snapshot.get("metadata_errors", []))
    data.companion_of = dict(snapshot.get("companion_of", {}))
    return data


def apply_overrides(data: Dataset, faces: dict[str, float] | None, blink: dict[str, float] | None) -> None:
    """Blink scores and face areas can be supplied after the images were read; set them on the frames."""
    known = {f.name for f in fields(FrameFeatures)}
    for folder, frames in data.groups.items():
        updated = []
        for frame in frames:
            feature = frame.features
            if feature is not None:
                extra: dict[str, Any] = {}
                if blink is not None:
                    extra["blink"] = is_blink(blink.get(norm(frame.path), 0.0))
                if faces and "face_area" in known and norm(frame.path) in faces:
                    extra["face_area"] = faces[norm(frame.path)]
                if extra:
                    feature = replace(feature, **extra)
                    data.features[frame.path] = feature
            updated.append(FolderFrame(frame.path, feature, frame.captured, frame.bracket_id))
        data.groups[folder] = updated


PICK_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Pick keepers __TITLE__</title>
<style>
  :root{--bg:#0e0f11;--tx:#e7e9ec;--mute:#8a909a;--ok:#3fb97a}
  body{margin:0;background:var(--bg);color:var(--tx);font:14px/1.4 system-ui,Segoe UI,sans-serif}
  header{position:sticky;top:0;background:#141517;border-bottom:1px solid #26282c;padding:10px 16px;display:flex;gap:16px;align-items:center;flex-wrap:wrap;z-index:2}
  .mute{color:var(--mute);font-size:13px}
  button{background:#1d1f23;color:var(--tx);border:1px solid #33363b;border-radius:8px;padding:7px 12px;font:inherit;cursor:pointer}
  main{display:grid;grid-template-columns:repeat(auto-fill,minmax(__CELL__px,1fr));gap:8px;padding:12px 16px}
  label{position:relative;display:block;border:3px solid transparent;border-radius:8px;cursor:pointer;background:#16171a;overflow:hidden}
  label img{display:block;width:100%;height:auto}
  label input{position:absolute;opacity:0;pointer-events:none}
  label.picked{border-color:var(--ok)}
  label.picked::after{content:"\\2713";position:absolute;top:6px;right:8px;background:var(--ok);color:#06210f;border-radius:50%;width:22px;height:22px;text-align:center;font-weight:700}
  label span{position:absolute;left:0;right:0;bottom:0;background:rgba(0,0,0,.55);padding:2px 6px;font-size:11px;color:#cfd3d9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
</style></head><body>
<header><b>__TITLE__</b><span class="mute" id="count"></span><button id="dl">download keepers</button>
<span class="mute">Frames are in capture order. Click the ones you would keep. Nothing here comes from the culling run. Your picks are kept in this browser; "download keepers" saves a list of file paths.</span></header>
<main id="grid"></main>
<script>
const FRAMES=__FRAMES__;const store="pass1_picks_"+"__KEY__";let picked={};
try{picked=JSON.parse(localStorage.getItem(store)||"{}")}catch(e){}
const grid=document.getElementById("grid");
FRAMES.forEach((f,i)=>{const l=document.createElement("label");const img=document.createElement("img");img.src=f.t;img.loading="lazy";const s=document.createElement("span");s.textContent=f.n;
 l.appendChild(img);l.appendChild(s);if(picked[f.p])l.classList.add("picked");
 l.onclick=ev=>{ev.preventDefault();if(picked[f.p]){delete picked[f.p];l.classList.remove("picked")}else{picked[f.p]=1;l.classList.add("picked")}
  try{localStorage.setItem(store,JSON.stringify(picked))}catch(e){}count()};grid.appendChild(l)});
function count(){document.getElementById("count").textContent=Object.keys(picked).length+" picked of "+FRAMES.length}
count();
document.getElementById("dl").onclick=()=>{const blob=new Blob([JSON.stringify(Object.keys(picked),null,1)],{type:"application/json"});
 const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="keepers___KEY__.json";a.click()};
</script></body></html>
"""


# -- pick sheets (choose keepers after the run, blind to it) ----------------------------------------------------------------------------------------


def write_pick_sheets(data: Dataset, out: Path, page_size: int, cell: int = 220) -> list[dict[str, Any]]:
    """Contact sheets of every readable frame in capture order, with nothing from the run on them: no flags, no stacks, no dimming. Choosing keepers here, after the run, keeps the
    picks independent of what the run flagged."""
    pages: list[dict[str, Any]] = []
    target = out / "pick"
    target.mkdir(parents=True, exist_ok=True)
    for number, (folder, frames) in enumerate(data.groups.items(), start=1):
        readable = [f for f in frames if f.features is not None]
        ordered = sorted(readable, key=lambda f: (f.captured is None, f.captured or 0.0, os.path.basename(f.path).casefold()))
        for part, start in enumerate(range(0, len(ordered), max(1, page_size)), start=1):
            chunk = ordered[start : start + page_size]
            payload = [{"p": f.path, "n": os.path.basename(f.path), "t": thumbnail_data_uri(f.path, edge=cell)} for f in chunk]
            key = f"{number:02d}_{part:02d}"
            title = f"{os.path.basename(folder.rstrip(os.sep)) or folder} ({start + 1} to {start + len(chunk)} of {len(ordered)})"
            html = PICK_PAGE.replace("__FRAMES__", json.dumps(payload).replace("</", "<\\/")).replace("__TITLE__", title).replace("__KEY__", key).replace("__CELL__", str(cell))
            name = f"pick_{key}.html"
            (target / name).write_text(html, encoding="utf-8")
            pages.append({"file": name, "title": title, "frames": len(chunk)})
    items = "".join(f'<li><a href="{p["file"]}">{p["title"]}</a> &middot; {p["frames"]} frames</li>' for p in pages)
    (target / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><title>Pick keepers</title><body style='font:15px system-ui;margin:24px;max-width:760px'>"
        "<h1>Pick keepers</h1><p>Open each page, click the frames you would keep, and press \"download keepers\". "
        "Then run the audit again with <code>--from-frozen frozen.json --keepers keepers_*.json</code>.</p>"
        f"<ol>{items}</ol></body>",
        encoding="utf-8",
    )
    return pages


# -- main -----------------------------------------------------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Measure pass 1 keeper recall and stack diagnostics on real folders.")
    p.add_argument("--folders", nargs="+", help="one or more image folders (non-recursive unless --recursive); not needed with --from-frozen")
    p.add_argument("--recursive", action="store_true")
    p.add_argument("--keepers", nargs="+", help="one or more files, each a text file with one original image path per line or a JSON list (the pick sheet downloads these); relative paths resolve against --root")
    p.add_argument("--root", default=".", help="base for relative keeper paths")
    p.add_argument("--out", required=True, help="output directory")
    p.add_argument("--settings-json", help="JSON object of CascadeSettings overrides")
    p.add_argument("--sweep-json", help="JSON object: setting name -> list of values")
    p.add_argument("--full-grid", action="store_true", help="sweep the full grid instead of one setting at a time")
    p.add_argument("--blink-scores", help="JSON path -> eyes-closed score; without it blink is not measured")
    p.add_argument("--faces-json", help="JSON path -> largest prominent face area fraction")
    p.add_argument("--cache", help="feature cache file (reuse between runs)")
    p.add_argument("--review-pairs", type=int, default=0, help="write review.html with this many blind pairs")
    p.add_argument("--from-frozen", help="frozen.json written by an earlier run: evaluate it again (new keepers, settings, sweeps) without reading any image")
    p.add_argument("--pick-sheet", action="store_true", help="write pick/ with flag-free contact sheets to choose keepers from, after the run, blind to its results")
    p.add_argument("--pick-page-size", type=int, default=240, help="frames per pick-sheet page")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    settings = CascadeSettings()
    if args.settings_json:
        raw = json.loads(Path(args.settings_json).read_text(encoding="utf-8-sig"))
        known, unknown = audit.split_overrides(settings, raw)
        settings = replace(settings, **known)
        if unknown:
            notes.append(f"Settings fields not present in this CascadeSettings were ignored: {', '.join(unknown)}")
    blink_scores = load_json_map(args.blink_scores)
    faces = load_json_map(args.faces_json)
    if args.from_frozen:
        data = dataset_from_snapshot(json.loads(Path(args.from_frozen).read_text(encoding="utf-8")))
        apply_overrides(data, faces, blink_scores)
        notes.append(f"Evaluated from the saved run {args.from_frozen}: no image was read, and the frames, hashes and measures are those of the original run.")
    else:
        if not args.folders:
            print("Give --folders, or --from-frozen with the frozen.json of an earlier run.", file=sys.stderr)
            return 2
        scanned, companion_of = merge_companions(scan_folders(args.folders, args.recursive))
        if not scanned:
            print("No images found in the given folders.", file=sys.stderr)
        data = build_dataset(scanned, args.cache, faces, blink_scores)
        data.companion_of = companion_of
        (out / "frozen.json").write_text(json.dumps(dataset_to_snapshot(data)), encoding="utf-8")
    if args.pick_sheet:
        pages = write_pick_sheets(data, out, args.pick_page_size)
        notes.append(f"{len(pages)} pick-sheet page(s) written to pick/index.html: flag-free, in capture order. Choose keepers there, then evaluate with --from-frozen.")
    report, _rows = build_report(args, data, settings, notes)
    (out / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    if args.review_pairs:
        (out / "review.html").write_text(render_review(report["review_pairs"]), encoding="utf-8")
    c = report["coverage"]
    print(f"scanned {c['scanned']} frames, {c['unreadable']} unreadable, {c['keepers_eligible']} eligible keepers; wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
