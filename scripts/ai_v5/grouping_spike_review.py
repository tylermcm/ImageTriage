#!/usr/bin/env python3
"""Step 2 of the grouping spike: pick neighbouring-frame pairs to judge and build a blind review page.

Pairs are neighbours in capture order inside one album. The sample over-represents the cases where a time-and-hash rule and
a DINOv2 rule disagree (they decide the comparison), plus agreeing controls and a few far-apart pairs. The page shows only
two thumbnails; the group, scores and time gap are kept in pairs_meta.json and never displayed, so the judge is not biased.

  python3 grouping_spike_review.py --features spike1/features.npz --thumbs spike1/thumbs --out spike1/review
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

import numpy as np


def hamming(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    x = np.bitwise_xor(a, b)
    return np.array([bin(int(v)).count("1") for v in x])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--thumbs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--window", type=float, default=60.0, help="seconds; only pairs this close are in the comparison")
    parser.add_argument("--disagree", type=int, default=160)
    parser.add_argument("--agree", type=int, default=50)
    parser.add_argument("--far", type=int, default=12)
    parser.add_argument("--seed", type=int, default=5)
    args = parser.parse_args()
    rng = np.random.default_rng(args.seed)

    f = np.load(args.features, allow_pickle=True)
    n = len(f["ts"])
    cls = f["dino_cls"] / np.linalg.norm(f["dino_cls"], axis=1, keepdims=True)
    a, b = np.arange(n - 1), np.arange(1, n)
    same_album = f["folder"][a] == f["folder"][b]
    a, b = a[same_album], b[same_album]
    dt = f["ts"][b] - f["ts"][a]
    cos = np.einsum("ij,ij->i", cls[a], cls[b])
    ham = hamming(f["phash_norm"][a], f["phash_norm"][b])
    same_settings = (f["iso"][a] == f["iso"][b]) & (f["fnumber"][a] == f["fnumber"][b]) & (f["focal"][a] == f["focal"][b])
    print(f"{len(a)} neighbouring pairs; {(dt <= args.window).sum()} within {args.window:.0f}s")

    # Rough operating points for the two ideas, used only to find where they disagree (not the final comparison).
    classical = (dt <= 3.0) & (ham <= 12)
    dino = cos >= 0.93
    near = dt <= args.window
    groups = {
        "classical_only": near & classical & ~dino,
        "dino_only": near & ~classical & dino,
        "both_same": near & classical & dino,
        "both_different": near & ~classical & ~dino,
        "far": ~near,
    }
    for name, mask in groups.items():
        print(f"  {name}: {mask.sum()}")

    def take(mask: np.ndarray, count: int) -> list[int]:
        idx = np.flatnonzero(mask)
        return list(rng.choice(idx, size=min(count, len(idx)), replace=False))

    half = args.disagree // 2
    chosen = (
        [(i, "classical_only") for i in take(groups["classical_only"], half)]
        + [(i, "dino_only") for i in take(groups["dino_only"], half)]
        + [(i, "both_same") for i in take(groups["both_same"], args.agree)]
        + [(i, "both_different") for i in take(groups["both_different"], args.agree)]
        + [(i, "far") for i in take(groups["far"], args.far)]
    )
    order = rng.permutation(len(chosen))
    chosen = [chosen[i] for i in order]

    os.makedirs(os.path.join(args.out, "img"), exist_ok=True)
    pairs, meta = [], []
    for k, (i, group) in enumerate(chosen):
        left, right = int(f["index"][a[i]]), int(f["index"][b[i]])
        for idx in (left, right):
            shutil.copy(os.path.join(args.thumbs, f"{idx}.jpg"), os.path.join(args.out, "img", f"{idx}.jpg"))
        pairs.append({"id": k, "left": f"img/{left}.jpg", "right": f"img/{right}.jpg"})
        meta.append({
            "id": k, "group": group, "folder": str(f["folder"][a[i]]), "a": str(f["path"][a[i]]), "b": str(f["path"][b[i]]),
            "dt": float(dt[i]), "cos": float(cos[i]), "ham": int(ham[i]), "same_settings": bool(same_settings[i]),
            "bias_a": None if np.isnan(f["bias"][a[i]]) else float(f["bias"][a[i]]), "bias_b": None if np.isnan(f["bias"][b[i]]) else float(f["bias"][b[i]]),
            "mode_a": int(f["mode"][a[i]]), "mode_b": int(f["mode"][b[i]]),
        })
    with open(os.path.join(args.out, "pairs_meta.json"), "w", encoding="utf-8") as handle:
        json.dump(meta, handle)
    sampled = {g: sum(1 for _, x in chosen if x == g) for g in groups}
    with open(os.path.join(args.out, "sampling.json"), "w", encoding="utf-8") as handle:
        json.dump({g: {"population": int(m.sum()), "sampled": sampled[g]} for g, m in groups.items()}, handle, indent=1)
    html = PAGE.replace("__PAIRS__", json.dumps(pairs))
    with open(os.path.join(args.out, "index.html"), "w", encoding="utf-8") as handle:
        handle.write(html)
    print(f"wrote {len(pairs)} pairs to {args.out}")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Same moment? Pair review</title>
<style>
  :root{--bg:#0e0f11;--panel:#18191c;--tx:#e7e9ec;--mute:#8a909a;--acc:#4c8dff;--ok:#3fb97a;--no:#e5635b;--maybe:#e0b04a}
  body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,Segoe UI,sans-serif}
  header{padding:12px 18px;border-bottom:1px solid #26282c;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  header b{font-size:16px} .mute{color:var(--mute);font-size:13px}
  .bar{flex:1;min-width:160px;height:6px;background:#26282c;border-radius:3px;overflow:hidden} .bar i{display:block;height:100%;background:var(--acc);width:0}
  .pair{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:14px 18px}
  .pair img{width:100%;border-radius:6px;display:block;background:#000}
  .keys{display:flex;gap:10px;padding:0 18px 14px;flex-wrap:wrap;align-items:center}
  button{background:var(--panel);color:var(--tx);border:1px solid #26282c;border-radius:8px;padding:10px 18px;font:600 15px system-ui;cursor:pointer}
  button.y{border-color:var(--ok)} button.n{border-color:var(--no)} button.u{border-color:var(--maybe)}
  button.sel.y{background:var(--ok);color:#06210f} button.sel.n{background:var(--no);color:#2a0705} button.sel.u{background:var(--maybe);color:#2a2005}
  .help{padding:0 18px 18px;color:var(--mute);font-size:13px;max-width:90ch}
</style></head><body>
<header><b>Are these the same moment?</b><span class="mute" id="count"></span><div class="bar"><i id="prog"></i></div>
  <button id="dl">Save labels (download)</button></header>
<div class="pair"><img id="L" alt="left"><img id="R" alt="right"></div>
<div class="keys">
  <button class="y" id="by">Y  same moment (alternates)</button><button class="n" id="bn">N  different moments</button><button class="u" id="bu">?  can't tell</button>
  <button id="prev">&larr; back</button><button id="next">skip &rarr;</button>
</div>
<div class="help"><b>Y</b>: you would treat these as alternatives of one shot, keeping one over the other, or stacking them as a burst or bracket.
<b>N</b>: different moments, even if close in time or similar in content. <b>?</b>: genuinely can't decide.
Keys: Y, N, ? (or U), arrow keys. Progress saves in this browser automatically; "Save labels" downloads labels.json to send back.</div>
<script>
const PAIRS=__PAIRS__;let i=0;
const key="pairlabels_v1";let labels={};try{labels=JSON.parse(localStorage.getItem(key)||"{}")}catch(e){}
function persist(){try{localStorage.setItem(key,JSON.stringify(labels))}catch(e){}}
function show(){const p=PAIRS[i];document.getElementById("L").src=p.left;document.getElementById("R").src=p.right;
 const done=Object.keys(labels).length;document.getElementById("count").textContent=(i+1)+" of "+PAIRS.length+"   ·   labelled "+done;
 document.getElementById("prog").style.width=(100*done/PAIRS.length)+"%";
 for(const [id,v] of [["by","y"],["bn","n"],["bu","u"]])document.getElementById(id).classList.toggle("sel",labels[p.id]===v);}
function set(v){labels[PAIRS[i].id]=v;persist();if(i<PAIRS.length-1)i++;show();}
document.getElementById("by").onclick=()=>set("y");document.getElementById("bn").onclick=()=>set("n");document.getElementById("bu").onclick=()=>set("u");
document.getElementById("prev").onclick=()=>{if(i>0){i--;show()}};document.getElementById("next").onclick=()=>{if(i<PAIRS.length-1){i++;show()}};
addEventListener("keydown",e=>{const k=e.key.toLowerCase();if(k==="y")set("y");else if(k==="n")set("n");else if(k==="u"||k==="?")set("u");
 else if(e.key==="ArrowLeft"&&i>0){i--;show()}else if(e.key==="ArrowRight"&&i<PAIRS.length-1){i++;show()}});
document.getElementById("dl").onclick=()=>{const blob=new Blob([JSON.stringify(labels,null,1)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="labels.json";a.click();};
show();
</script></body></html>
"""

if __name__ == "__main__":
    main()
