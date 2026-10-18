#!/usr/bin/env python3
"""Round 2 sampler and blind review page for the grouping spike.

Part A: pairs chosen where a time/EXIF/pHash model and a DINOv2 model (both trained on the round 1 labels) disagree or are
unsure, plus random pairs and 20 repeated round 1 pairs to measure the judge's own consistency.
Part B: whole sequences of 10 consecutive frames, judged one neighbouring pair at a time with the strip in view, so a method
can be scored on the groups it builds (including chaining), not only on single pairs.

Nothing about the models, groups or time gaps is shown on the page.

  python3 grouping_spike2_review.py --features spike2/features.npz --thumbs spike2/thumbs \
      --round1 spike1 --out spike2/review
"""
from __future__ import annotations

import argparse
import json
import os
import shutil

import numpy as np
from sklearn.linear_model import LogisticRegression

from spike_common import load_features, neighbour_pairs, pair_features


def weighted_pick(rng, candidates: np.ndarray, cell: np.ndarray, count: int) -> list[int]:
    """Pick `count` of `candidates` at random, balancing the cells (album + photographer) they come from."""
    if len(candidates) == 0:
        return []
    cells, counts = np.unique(cell[candidates], return_counts=True)
    weight = np.array([1.0 / counts[np.searchsorted(cells, cell[i])] for i in candidates])
    weight /= weight.sum()
    return list(rng.choice(candidates, size=min(count, len(candidates)), replace=False, p=weight))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", required=True)
    parser.add_argument("--thumbs", required=True)
    parser.add_argument("--round1", required=True, help="round 1 directory (spike1) with review/pairs_meta.json and labels.json")
    parser.add_argument("--out", required=True)
    parser.add_argument("--window", type=float, default=90.0)
    parser.add_argument("--disagree", type=int, default=110)
    parser.add_argument("--uncertain", type=int, default=100)
    parser.add_argument("--random", type=int, default=40)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--strips", type=int, default=30)
    parser.add_argument("--strip-len", type=int, default=10)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    rng = np.random.default_rng(args.seed)

    f = load_features(args.features)
    a, b = neighbour_pairs(f, max_gap=args.window)
    feats = pair_features(f, a, b)
    cell = np.array([f"{f['folder'][i]}|{f['photographer'][i]}" for i in a])
    print(f"{len(a)} neighbouring pairs within {args.window:.0f}s across {len(np.unique(cell))} cells", flush=True)

    # two simple models trained on the round 1 labels
    meta1 = json.load(open(os.path.join(args.round1, "review", "pairs_meta.json"), encoding="utf-8"))
    samp1 = json.load(open(os.path.join(args.round1, "review", "sampling.json"), encoding="utf-8"))
    lab1 = json.load(open(os.path.join(args.round1, "labels.json"), encoding="utf-8"))
    rows = [m for m in meta1 if lab1.get(str(m["id"])) in ("y", "n")]
    y1 = np.array([1 if lab1[str(m["id"])] == "y" else 0 for m in rows])
    w1 = np.array([samp1[m["group"]]["population"] / max(1, samp1[m["group"]]["sampled"]) for m in rows], float)
    X_c = np.column_stack([np.log(np.array([m["dt"] for m in rows]) + 0.05), [m["ham"] for m in rows], [float(m["same_settings"]) for m in rows],
                           [abs((m["bias_a"] or 0.0) - (m["bias_b"] or 0.0)) for m in rows]])
    X_d = np.column_stack([np.log(np.array([m["dt"] for m in rows]) + 0.05), [m["cos"] for m in rows]])
    mu_c, sd_c, mu_d, sd_d = X_c.mean(0), X_c.std(0) + 1e-9, X_d.mean(0), X_d.std(0) + 1e-9
    lr_c = LogisticRegression(max_iter=2000).fit((X_c - mu_c) / sd_c, y1, sample_weight=w1 / w1.mean())
    lr_d = LogisticRegression(max_iter=2000).fit((X_d - mu_d) / sd_d, y1, sample_weight=w1 / w1.mean())
    new_c = np.column_stack([feats["log_dt"], feats["ham_pn"], feats["same_settings"], feats["bias_diff"]])
    new_d = np.column_stack([feats["log_dt"], feats["cos_cls"]])
    p_c = lr_c.predict_proba((new_c - mu_c) / sd_c)[:, 1]
    p_d = lr_d.predict_proba((new_d - mu_d) / sd_d)[:, 1]
    print(f"model agreement: corr {np.corrcoef(p_c, p_d)[0, 1]:.2f}; mean p classical {p_c.mean():.2f}, dino {p_d.mean():.2f}", flush=True)

    chosen: dict[int, str] = {}

    def add(indices: list[int], group: str) -> None:
        for i in indices:
            if int(i) not in chosen:
                chosen[int(i)] = group

    gap = p_c - p_d
    big = np.flatnonzero(np.abs(gap) >= 0.2)
    add(weighted_pick(rng, big[gap[big] > 0], cell, args.disagree // 2), "classical_more_same")
    add(weighted_pick(rng, big[gap[big] < 0], cell, args.disagree // 2), "dino_more_same")
    mid = np.flatnonzero((np.minimum(p_c, p_d) < 0.8) & (np.maximum(p_c, p_d) > 0.2) & (np.abs(gap) < 0.2))
    add(weighted_pick(rng, mid, cell, args.uncertain), "both_unsure")
    add(weighted_pick(rng, np.arange(len(a)), cell, args.random), "random")
    print("pair sample:", {g: sum(1 for v in chosen.values() if v == g) for g in set(chosen.values())}, flush=True)

    # strips: windows of consecutive frames inside one stream, short in time, balanced across streams
    stream = f["stream"]
    ts = f["ts"]
    streams = [s for s in np.unique(stream)]
    per_stream = max(1, args.strips // len(streams))
    strips = []
    for s_name in streams:
        idx = np.flatnonzero(stream == s_name)
        starts = [i for i in idx[: -args.strip_len] if stream[i + args.strip_len - 1] == s_name and ts[i + args.strip_len - 1] - ts[i] <= 240.0]
        rng.shuffle(starts)
        taken = []
        for st in starts:
            if all(abs(st - t) >= args.strip_len for t in taken):
                taken.append(st)
            if len(taken) >= per_stream:
                break
        strips += [list(range(st, st + args.strip_len)) for st in taken]
    print(f"{len(strips)} strips of {args.strip_len} frames from {len(streams)} streams", flush=True)

    out_img = os.path.join(args.out, "img")
    os.makedirs(out_img, exist_ok=True)

    def copy_thumb(src_dir: str, index: int, prefix: str = "") -> str:
        name = f"{prefix}{int(index)}.jpg"
        shutil.copy(os.path.join(src_dir, f"{int(index)}.jpg"), os.path.join(out_img, name))
        return f"img/{name}"

    items, meta = [], []
    pair_order = list(chosen.items())
    rep_pool = [m for m in meta1 if lab1.get(str(m["id"])) in ("y", "n")]
    rep_y = [m for m in rep_pool if lab1[str(m["id"])] == "y"]
    rep_n = [m for m in rep_pool if lab1[str(m["id"])] == "n"]
    reps = list(rng.choice(rep_y, size=args.repeats // 2, replace=False)) + list(rng.choice(rep_n, size=args.repeats - args.repeats // 2, replace=False))
    r1_thumbs = os.path.join(args.round1, "thumbs")
    r1_features = load_features(os.path.join(args.round1, "features.npz"))
    r1_index = {str(p): int(i) for p, i in zip(r1_features["path"], r1_features["index"])}
    entries = [("pair", i, g) for i, g in pair_order] + [("repeat", m, "repeat") for m in reps]
    rng.shuffle(entries)
    for n, (kind, obj, group) in enumerate(entries):
        item_id = f"p{n}"
        if kind == "pair":
            i = int(obj)
            left, right = int(f["index"][a[i]]), int(f["index"][b[i]])
            items.append({"id": item_id, "type": "pair", "left": copy_thumb(args.thumbs, left), "right": copy_thumb(args.thumbs, right)})
            meta.append({"id": item_id, "kind": "pair", "group": group, "a": int(a[i]), "b": int(b[i]), "stream": str(stream[a[i]]),
                         "album": str(f["folder"][a[i]]), "photographer": str(f["photographer"][a[i]]), "p_classical": float(p_c[i]), "p_dino": float(p_d[i])})
        else:
            m = obj
            la, lb = r1_index[m["a"]], r1_index[m["b"]]
            items.append({"id": item_id, "type": "pair", "left": copy_thumb(r1_thumbs, la, "r1_"), "right": copy_thumb(r1_thumbs, lb, "r1_")})
            meta.append({"id": item_id, "kind": "repeat", "group": "repeat", "round1_id": m["id"], "round1_label": lab1[str(m["id"])]})
    for s_id, frames in enumerate(strips):
        thumbs = [copy_thumb(args.thumbs, int(f["index"][k])) for k in frames]
        for step in range(len(frames) - 1):
            item_id = f"s{s_id}_{step}"
            items.append({"id": item_id, "type": "step", "strip": thumbs, "at": step})
            meta.append({"id": item_id, "kind": "step", "strip": s_id, "step": step, "a": int(frames[step]), "b": int(frames[step + 1]),
                         "stream": str(stream[frames[step]]), "album": str(f["folder"][frames[step]]), "photographer": str(f["photographer"][frames[step]])})
    json.dump(meta, open(os.path.join(args.out, "items_meta.json"), "w", encoding="utf-8"))
    json.dump({"strips": strips}, open(os.path.join(args.out, "strips.json"), "w"))
    pairs_part = sum(1 for it in items if it["type"] == "pair")
    html = PAGE.replace("__ITEMS__", json.dumps(items)).replace("__PAIRS__", str(pairs_part))
    open(os.path.join(args.out, "index.html"), "w", encoding="utf-8").write(html)
    print(f"wrote {len(items)} judgments ({pairs_part} pairs, {len(items) - pairs_part} sequence steps) to {args.out}", flush=True)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Same moment? Round 2</title>
<style>
  :root{--bg:#0e0f11;--panel:#18191c;--tx:#e7e9ec;--mute:#8a909a;--acc:#4c8dff;--ok:#3fb97a;--no:#e5635b;--maybe:#e0b04a}
  body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,Segoe UI,sans-serif}
  header{padding:10px 18px;border-bottom:1px solid #26282c;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  header b{font-size:16px} .mute{color:var(--mute);font-size:13px}
  .bar{flex:1;min-width:160px;height:6px;background:#26282c;border-radius:3px;overflow:hidden} .bar i{display:block;height:100%;background:var(--acc);width:0}
  .strip{display:flex;gap:6px;padding:10px 18px 0;overflow-x:auto}
  .strip img{height:78px;border-radius:4px;border:3px solid transparent;opacity:.55;flex:none}
  .strip img.cur{opacity:1;border-color:var(--acc)}
  .strip .cut{align-self:center;width:14px;text-align:center;color:var(--mute);flex:none}
  .pair{display:grid;grid-template-columns:1fr 1fr;gap:10px;padding:12px 18px}
  .pair img{width:100%;border-radius:6px;display:block;background:#000}
  .keys{display:flex;gap:10px;padding:0 18px 12px;flex-wrap:wrap;align-items:center}
  button{background:var(--panel);color:var(--tx);border:1px solid #26282c;border-radius:8px;padding:10px 18px;font:600 15px system-ui;cursor:pointer}
  button.y{border-color:var(--ok)} button.n{border-color:var(--no)} button.u{border-color:var(--maybe)}
  button.sel.y{background:var(--ok);color:#06210f} button.sel.n{background:var(--no);color:#2a0705} button.sel.u{background:var(--maybe);color:#2a2005}
  .help{padding:0 18px 18px;color:var(--mute);font-size:13px;max-width:100ch}
</style></head><body>
<header><b id="part"></b><span class="mute" id="count"></span><div class="bar"><i id="prog"></i></div><button id="dl">Save labels (download)</button></header>
<div class="strip" id="strip" style="display:none"></div>
<div class="pair"><img id="L" alt="left"><img id="R" alt="right"></div>
<div class="keys">
  <button class="y" id="by">Y  same moment (alternates)</button><button class="n" id="bn">N  different moments</button><button class="u" id="bu">?  can't tell</button>
  <button id="prev">&larr; back</button><button id="next">skip &rarr;</button>
</div>
<div class="help"><b>Y</b>: if you kept one, the other would be redundant (same shot attempt: burst, bracket, re-take with the same framing).
<b>N</b>: you might want both (different composition, different moment, someone or something has moved to a different place).
<b>?</b>: you can't decide. Keys: Y, N, ? and the arrow keys. Progress saves in this browser; "Save labels" downloads labels2.json.
In the second half you see a strip of ten frames in order: judge the highlighted frame against the one after it, in context.</div>
<script>
const ITEMS=__ITEMS__;const PAIRS=__PAIRS__;let i=0;
const key="pairlabels_v2";let labels={};try{labels=JSON.parse(localStorage.getItem(key)||"{}")}catch(e){}
function persist(){try{localStorage.setItem(key,JSON.stringify(labels))}catch(e){}}
function show(){const it=ITEMS[i];const strip=document.getElementById("strip");
 if(it.type==="pair"){strip.style.display="none";document.getElementById("L").src=it.left;document.getElementById("R").src=it.right;
  document.getElementById("part").textContent="Part A: pairs"}
 else{strip.style.display="flex";strip.innerHTML="";it.strip.forEach((src,k)=>{const im=document.createElement("img");im.src=src;if(k===it.at||k===it.at+1)im.classList.add("cur");strip.appendChild(im)});
  document.getElementById("L").src=it.strip[it.at];document.getElementById("R").src=it.strip[it.at+1];document.getElementById("part").textContent="Part B: sequences"}
 const done=Object.keys(labels).length;document.getElementById("count").textContent=(i+1)+" of "+ITEMS.length+"   ·   labelled "+done;
 document.getElementById("prog").style.width=(100*done/ITEMS.length)+"%";
 for(const [id,v] of [["by","y"],["bn","n"],["bu","u"]])document.getElementById(id).classList.toggle("sel",labels[it.id]===v);}
function set(v){labels[ITEMS[i].id]=v;persist();if(i<ITEMS.length-1)i++;show();}
document.getElementById("by").onclick=()=>set("y");document.getElementById("bn").onclick=()=>set("n");document.getElementById("bu").onclick=()=>set("u");
document.getElementById("prev").onclick=()=>{if(i>0){i--;show()}};document.getElementById("next").onclick=()=>{if(i<ITEMS.length-1){i++;show()}};
addEventListener("keydown",e=>{const k=e.key.toLowerCase();if(k==="y")set("y");else if(k==="n")set("n");else if(k==="u"||k==="?")set("u");
 else if(e.key==="ArrowLeft"&&i>0){i--;show()}else if(e.key==="ArrowRight"&&i<ITEMS.length-1){i++;show()}});
document.getElementById("dl").onclick=()=>{const blob=new Blob([JSON.stringify(labels,null,1)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="labels2.json";a.click();};
// start at the first unlabelled item
for(let k=0;k<ITEMS.length;k++){if(!(ITEMS[k].id in labels)){i=k;break}}
show();
</script></body></html>
"""

if __name__ == "__main__":
    main()
