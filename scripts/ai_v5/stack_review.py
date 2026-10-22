#!/usr/bin/env python3
"""A stack review page: does the cascade put frames into the right stacks?

Reads the frozen.json of a pass1_audit run (frame measures are not re-measured; thumbnails are read from the originals), runs the cascade's stacking with its current
settings, and writes ``stack_review.html``. Each card is a short strip of neighbouring frames in capture order, with a line between every two of them: green where the app
put them in the same stack, red where it split them. The person clicks every line that is wrong, so a stack of three, four or more frames is marked by turning each line
inside it green; lines left alone count as agreement. "download labels" saves ``stack_labels.json``: one row per line, with what the app said and what the person says.
A green line turned red is a false merge, a red one turned green is a false split.

Cards are sampled, not exhaustive: each is built around one "asked" seam, chosen weighted toward the ones the rules are closest to deciding (hash distance between 4 and
30, long time gaps, focal length changes), plus a random control set of easy ones, so errors are found quickly and the control shows the page is not just agreeing with itself.

  python scripts/ai_v5/stack_review.py --from-frozen run_a/frozen.json --out run_a --seams 120
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pass1_audit as audit_script  # noqa: E402
from image_triage.cascade.folder import ROLE_BRACKET, run_folder  # noqa: E402
from image_triage.cascade.model import CascadeSettings  # noqa: E402

CONTEXT = 3  # frames shown on each side of the asked seam


def hamming(a: int, b: int) -> int:
    return bin(int(a) ^ int(b)).count("1")


def stack_key(outcome: Any, bracket_id: int | None) -> tuple:
    if outcome.role == ROLE_BRACKET:
        return ("bracket", bracket_id)
    return ("stack", outcome.burst_index, outcome.stack_index)


def build_seams(data: audit_script.Dataset, settings: CascadeSettings) -> list[dict[str, Any]]:
    """Every seam of every folder with what the app decided, and the measures used to sample them."""
    seams: list[dict[str, Any]] = []
    for folder, frames in data.groups.items():
        result = run_folder(frames, settings)
        ordered = [f for f in frames if f.features is not None and f.path in result.outcomes]
        ordered.sort(key=lambda f: (f.captured is None, f.captured or 0.0, os.path.basename(f.path).casefold()))
        keys = [stack_key(result.outcomes[f.path], f.bracket_id) for f in ordered]
        for i in range(len(ordered) - 1):
            a, b = ordered[i], ordered[i + 1]
            gap = None if a.captured is None or b.captured is None else round(b.captured - a.captured, 2)
            focal_a, focal_b = data.focal.get(a.path), data.focal.get(b.path)
            first = max(0, i - CONTEXT + 1)
            last = min(len(ordered) - 1, i + CONTEXT)  # the window is ordered[first:last + 1]
            seams.append({
                "folder": folder,
                "index": i,
                "app_says": "same" if keys[i] == keys[i + 1] else "different",
                "distance": hamming(a.features.phash, b.features.phash),
                "gap": gap,
                "focal_ratio": None if not focal_a or not focal_b else round(max(focal_a, focal_b) / min(focal_a, focal_b), 3),
                "window": [f.path for f in ordered[first:last + 1]],
                "window_app": ["same" if keys[k] == keys[k + 1] else "different" for k in range(first, last)],
                "left_count": i - first + 1,
                "bracket": keys[i][0] == "bracket" or keys[i + 1][0] == "bracket",
            })
    return seams


def sample_seams(seams: list[dict[str, Any]], count: int, seed: int = 11) -> list[dict[str, Any]]:
    """Weighted toward seams near a decision, with a random control third. Brackets are skipped: they are kept together on purpose and not compared."""
    rng = random.Random(seed)
    pool = [s for s in seams if not s["bracket"]]
    close = [s for s in pool if 4 <= s["distance"] <= 30 or (s["gap"] is not None and s["gap"] > 20 and s["app_says"] == "same") or (s["focal_ratio"] or 1.0) > 1.1]
    close_ids = {id(s) for s in close}
    easy = [s for s in pool if id(s) not in close_ids]
    n_close = min(len(close), round(count * 2 / 3))
    picked = rng.sample(close, n_close) + rng.sample(easy, min(len(easy), count - n_close))
    rng.shuffle(picked)
    for number, seam in enumerate(picked, start=1):
        seam["id"] = number
    return picked


PAGE = """<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Stack review</title>
<style>
:root{--bg:#f3f4f7;--panel:#fff;--ink:#171a21;--mut:#667085;--line:#dde1e8;--ok:#1f9d63;--bad:#d1423f;--chg:#c27c0e}
@media (prefers-color-scheme:dark){:root{--bg:#14161b;--panel:#1c1f26;--ink:#eceef3;--mut:#9aa3b2;--line:#2d323c}}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 system-ui,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--panel);border-bottom:1px solid var(--line);padding:10px 18px;display:flex;gap:14px;align-items:center;flex-wrap:wrap;z-index:2}
button{font:inherit;border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:9px;padding:8px 14px;cursor:pointer}
.wrap{max-width:1300px;margin:0 auto;padding:18px}
.q{font-size:18px;font-weight:650;margin:6px 0 4px}.sub{color:var(--mut);margin-bottom:14px}
.strip{display:flex;align-items:stretch;overflow-x:auto;padding-bottom:6px}
.frame{flex:0 0 auto;margin:0 2px}.frame img{height:200px;display:block;border-radius:6px;border:1px solid var(--line)}
.frame small{display:block;color:var(--mut);font-size:11.5px;text-align:center;margin-top:3px}
.seam{flex:0 0 auto;width:40px;display:flex;flex-direction:column;align-items:center;justify-content:center;cursor:pointer;border-radius:8px;gap:6px}
.seam:hover{background:rgba(127,127,127,.12)}
.seam i{display:block;width:5px;height:200px;border-radius:3px}
.seam.same i{background:var(--ok)}.seam.diff i{background:var(--bad);width:9px}
.seam b{font-size:10.5px;color:var(--mut);font-weight:600}
.seam.changed{outline:2px dashed var(--chg)}.seam.main b{color:var(--ink)}
.actions{display:flex;gap:10px;margin-top:14px;flex-wrap:wrap}
.hint{color:var(--mut);font-size:13px;margin-top:12px}
.legend span{display:inline-block;margin-right:16px}
</style>
<header><b>Stack review</b><span id="count"></span><button id="dl">download labels</button><span class="sub" style="margin:0">Enter or Y = these lines are right, next. Click any line to flip it. Arrows move.</span></header>
<div class="wrap" id="app"></div>
<script>
const CARDS=__SEAMS__;let i=0;let marks={};let seen={};const store="stack_review_v2";
try{const d=JSON.parse(localStorage.getItem(store)||"{}");marks=d.marks||{};seen=d.seen||{}}catch(e){}
function persist(){try{localStorage.setItem(store,JSON.stringify({marks:marks,seen:seen}))}catch(e){}}
function human(c,k){const m=marks[c.id];return m&&m[k]!==undefined?m[k]:c.app[k]}
function show(){const c=CARDS[i];document.getElementById("count").textContent=(i+1)+" of "+CARDS.length+"   reviewed "+Object.keys(seen).length;
const main=c.left_count-1;
let h='<div class="q">Which of these frames belong in the same stack?</div>';
h+='<div class="sub">A stack is the frames of one moment or pose that you would compare when choosing one. The lines show what the app decided: <b style="color:var(--ok)">green = same stack</b>, <b style="color:var(--bad)">red = split</b>. <b>Click any line that is wrong to flip it</b>, so a stack of three, four or more is marked by turning each line inside it green. Leave a line alone if it is right. The line marked <b>ASKED</b> is the one sampled for this card.</div><div class="strip">';
c.frames.forEach((f,k)=>{if(k>0){const s=human(c,k-1);const ch=s!==c.app[k-1];h+='<div class="seam '+(s==="same"?"same":"diff")+(ch?" changed":"")+(k-1===main?" main":"")+'" data-k="'+(k-1)+'"><i></i>'+(k-1===main?"<b>ASKED</b>":"")+'</div>'}
h+='<div class="frame"><img src="'+f.t+'"><small>'+f.n+'</small></div>'});
h+='</div><div class="actions"><button id="ok">These lines are right (Enter)</button><button id="flip">Flip the asked line (N)</button></div>';
h+='<div class="hint legend"><span>Dashed outline = you changed it.</span><span>Every line you leave alone counts as agreement, so flip only the ones you are sure about.</span></div>';
document.getElementById("app").innerHTML=h;
document.querySelectorAll(".seam").forEach(el=>el.onclick=()=>flip(+el.dataset.k));
document.getElementById("ok").onclick=next;document.getElementById("flip").onclick=()=>flip(main);}
function flip(k){const c=CARDS[i];const m=marks[c.id]=marks[c.id]||{};m[k]=human(c,k)==="same"?"different":"same";persist();show();}
function next(){seen[CARDS[i].id]=1;persist();if(i<CARDS.length-1)i++;show();}
document.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key==="y"||e.key==="Y")next();else if(e.key==="n"||e.key==="N")flip(CARDS[i].left_count-1);else if(e.key==="ArrowRight"&&i<CARDS.length-1){i++;show()}else if(e.key==="ArrowLeft"&&i>0){i--;show()}});
document.getElementById("dl").onclick=()=>{const out=[];CARDS.forEach(c=>{if(!seen[c.id])return;for(let k=0;k<c.app.length;k++){out.push({card:c.id,folder:c.folder,asked:k===c.left_count-1,a:c.paths[k],b:c.paths[k+1],app_says:c.app[k],human:human(c,k)})}});
const blob=new Blob([JSON.stringify(out,null,1)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="stack_labels.json";a.click();};
show();
</script>"""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--from-frozen", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--seams", type=int, default=120)
    p.add_argument("--settings-json", help="JSON object of CascadeSettings overrides")
    args = p.parse_args(argv)
    data = audit_script.dataset_from_snapshot(json.loads(Path(args.from_frozen).read_text(encoding="utf-8")))
    settings = CascadeSettings(**json.loads(args.settings_json)) if args.settings_json else CascadeSettings()
    seams = sample_seams(build_seams(data, settings), args.seams)
    thumbs: dict[str, str] = {}
    cards = []
    for seam in seams:
        frames = []
        for path in seam["window"]:
            if path not in thumbs:
                thumbs[path] = audit_script.thumbnail_data_uri(path, edge=300)
            frames.append({"n": os.path.basename(path), "t": thumbs[path]})
        cards.append({"id": seam["id"], "folder": seam["folder"], "index": seam["index"], "app_says": seam["app_says"], "frames": frames,
                      "left_count": seam["left_count"], "paths": seam["window"], "app": seam["window_app"]})
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "stack_review.html").write_text(PAGE.replace("__SEAMS__", json.dumps(cards).replace("</", "<\\/")), encoding="utf-8")
    (out / "stack_review_seams.json").write_text(json.dumps([{k: v for k, v in s.items() if k not in ("window", "window_app")} for s in seams], indent=1), encoding="utf-8")
    same = sum(1 for s in seams if s["app_says"] == "same")
    print(f"{len(seams)} seams sampled ({same} the app joined, {len(seams) - same} the app split) -> {out / 'stack_review.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
