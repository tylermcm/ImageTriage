#!/usr/bin/env python3
"""Scene-stacking spike: can a method find the other photos of the same scene taken at another time?

For each query photo, three systems rank every other photo in the same album (excluding the query's own burst, i.e. frames in
the same stream within 60 s):
  dino      DINOv2 ViT-B/14 patch-mean cosine (the best single DINO score in the grouping spike)
  orb       ORB feature matching with a homography check (inlier count), exhaustive over the album
  lowlevel  rank fusion of colour histogram, tiny-image correlation and pHash (global appearance, no geometry)
The top 4 of each are pooled (up to 12 photos), shuffled, and shown blind on a review page where the owner ticks the ones that
are the same scene. Reads photos' stored thumbnails only.

  python3 scene_spike_build.py --spike spike2 --out spike2/scene --queries 80
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from multiprocessing import Pool

import cv2
import numpy as np

from spike_common import hamming64, load_features, thumb_path

TOP = 4
EXCLUDE_SECONDS = 60.0
_ORB = None


def _init() -> None:
    global _ORB
    cv2.setNumThreads(1)
    _ORB = cv2.ORB_create(nfeatures=500)


def describe(path: str):
    img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    kp, des = _ORB.detectAndCompute(img, None)
    pts = np.array([k.pt for k in kp], np.float32) if kp else np.zeros((0, 2), np.float32)
    return pts, des if des is not None else np.zeros((0, 32), np.uint8)


_DESC: list | None = None


def _set_desc(desc) -> None:
    global _DESC
    cv2.setNumThreads(1)
    _DESC = desc


def inliers(job: tuple[int, int]) -> int:
    qi, ci = job
    pq, dq = _DESC[qi]
    pc, dc = _DESC[ci]
    if len(dq) < 12 or len(dc) < 12:
        return 0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(dq, dc, k=2)
    good = [m[0] for m in matches if len(m) == 2 and m[0].distance < 0.75 * m[1].distance]
    if len(good) < 10:
        return 0
    src = pq[[m.queryIdx for m in good]].reshape(-1, 1, 2)
    dst = pc[[m.trainIdx for m in good]].reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
    return int(mask.sum()) if mask is not None else 0


def rank_of(score: np.ndarray) -> np.ndarray:
    order = np.argsort(-score, kind="stable")
    rank = np.empty(len(score))
    rank[order] = np.arange(len(score))
    return rank


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spike", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--queries", type=int, default=80)
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--seed", type=int, default=21)
    args = parser.parse_args()
    rng = np.random.default_rng(args.seed)
    f = load_features(os.path.join(args.spike, "features.npz"))
    thumbs = os.path.join(args.spike, "thumbs")
    n = len(f["ts"])
    album, stream, ts = f["folder"], f["stream"], f["ts"]

    t0 = time.time()
    with Pool(args.workers, initializer=_init) as pool:
        desc = pool.map(describe, [thumb_path(thumbs, f["index"][i]) for i in range(n)], chunksize=64)
    print(f"ORB descriptors for {n} photos in {time.time() - t0:.0f}s", flush=True)

    dino = f["dino_mean"] / np.maximum(np.linalg.norm(f["dino_mean"], axis=1, keepdims=True), 1e-9)
    hist = f["hist"]
    tiny = np.stack([cv2.resize(g, (16, 11), interpolation=cv2.INTER_AREA).astype(np.float32).ravel() for g in f["gray64"]])
    tiny = (tiny - tiny.mean(1, keepdims=True)) / np.maximum(tiny.std(1, keepdims=True), 1e-6)

    # queries: balanced over albums and photographers
    cells = sorted({(str(a), str(p)) for a, p in zip(album, f["photographer"])})
    per_cell = max(1, args.queries // len(cells))
    queries = []
    for a, p in cells:
        pool_idx = np.flatnonzero((album == a) & (f["photographer"] == p))
        queries += list(rng.choice(pool_idx, size=min(per_cell, len(pool_idx)), replace=False))
    rng.shuffle(queries)
    print(f"{len(queries)} queries across {len(cells)} cells: {cells}", flush=True)

    out_img = os.path.join(args.out, "img")
    os.makedirs(out_img, exist_ok=True)
    items, meta = [], []
    with Pool(args.workers, initializer=_set_desc, initargs=(desc,)) as pool:
        for qn, q in enumerate(queries):
            cand = np.flatnonzero((album == album[q]) & ~((stream == stream[q]) & (np.abs(ts - ts[q]) <= EXCLUDE_SECONDS)))
            cand = cand[cand != q]
            s_dino = dino[cand] @ dino[q]
            s_orb = np.array(pool.map(inliers, [(q, c) for c in cand], chunksize=64), float)
            hist_sim = np.sqrt(hist[cand] * hist[q]).sum(1)
            tiny_sim = tiny[cand] @ tiny[q] / tiny.shape[1]
            ham = hamming64(f["phash"][cand], np.full(len(cand), f["phash"][q], dtype=np.uint64))
            low_rank = (rank_of(hist_sim) + rank_of(tiny_sim) + rank_of(-ham)) / 3.0
            s_low = -low_rank
            tops = {"dino": cand[np.argsort(-s_dino)[:TOP]], "orb": cand[np.argsort(-s_orb, kind="stable")[:TOP]], "lowlevel": cand[np.argsort(-s_low)[:TOP]]}
            ranks = {}
            for name, score in (("dino", s_dino), ("orb", s_orb), ("lowlevel", s_low)):
                order = cand[np.argsort(-score, kind="stable")]
                ranks[name] = {int(c): int(r) for r, c in enumerate(order[:50])}
            pooled = list(dict.fromkeys(int(c) for name in tops for c in tops[name]))
            rng.shuffle(pooled)
            copy = lambda i: (shutil.copy(thumb_path(thumbs, f["index"][i]), os.path.join(out_img, f"{int(f['index'][i])}.jpg")), f"img/{int(f['index'][i])}.jpg")[1]
            items.append({"id": f"q{qn}", "query": copy(q), "cands": [{"id": int(c), "src": copy(c)} for c in pooled]})
            meta.append({"id": f"q{qn}", "query": int(q), "album": str(album[q]), "photographer": str(f["photographer"][q]),
                         "pool": pooled, "top": {k: [int(c) for c in v] for k, v in tops.items()}, "ranks": ranks,
                         "orb_inliers": {int(c): float(s) for c, s in zip(cand, s_orb) if int(c) in pooled}})
            if qn % 10 == 0:
                print(f"  query {qn} done ({time.time() - t0:.0f}s), pool {len(pooled)}", flush=True)
    json.dump(meta, open(os.path.join(args.out, "scene_meta.json"), "w", encoding="utf-8"))
    open(os.path.join(args.out, "index.html"), "w", encoding="utf-8").write(PAGE.replace("__ITEMS__", json.dumps(items)))
    print(f"wrote {len(items)} queries, {sum(len(i['cands']) for i in items)} candidates to {args.out}", flush=True)


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Same scene? Review</title>
<style>
  :root{--bg:#0e0f11;--panel:#18191c;--tx:#e7e9ec;--mute:#8a909a;--acc:#4c8dff;--ok:#3fb97a}
  body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.45 system-ui,Segoe UI,sans-serif}
  header{padding:10px 18px;border-bottom:1px solid #26282c;display:flex;gap:16px;align-items:center;flex-wrap:wrap}
  header b{font-size:16px} .mute{color:var(--mute);font-size:13px}
  .bar{flex:1;min-width:160px;height:6px;background:#26282c;border-radius:3px;overflow:hidden} .bar i{display:block;height:100%;background:var(--acc);width:0}
  main{display:grid;grid-template-columns:minmax(260px,32%) 1fr;gap:14px;padding:14px 18px}
  .q img{width:100%;border-radius:8px;border:3px solid var(--acc);display:block}
  .grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;align-content:start}
  .grid .c{position:relative;cursor:pointer;border-radius:6px;overflow:hidden;border:3px solid transparent}
  .grid .c img{width:100%;display:block;opacity:.8} .grid .c.on{border-color:var(--ok)} .grid .c.on img{opacity:1}
  .grid .c.on::after{content:"same scene";position:absolute;left:6px;top:6px;background:var(--ok);color:#06210f;font:700 12px system-ui;padding:2px 7px;border-radius:4px}
  .keys{display:flex;gap:10px;padding:0 18px 12px;flex-wrap:wrap}
  button{background:var(--panel);color:var(--tx);border:1px solid #26282c;border-radius:8px;padding:10px 18px;font:600 15px system-ui;cursor:pointer}
  .help{padding:0 18px 18px;color:var(--mute);font-size:13px;max-width:105ch}
</style></head><body>
<header><b>Which of these are the same scene as the photo on the left?</b><span class="mute" id="count"></span><div class="bar"><i id="prog"></i></div><button id="dl">Save labels (download)</button></header>
<main><div class="q"><img id="Q" alt="query"></div><div class="grid" id="G"></div></main>
<div class="keys"><button id="prev">&larr; back</button><button id="next">Done with this one &rarr; (Enter)</button></div>
<div class="help"><b>Click every photo that is the same scene</b> as the one on the left (the same location and subject setup, even from another angle, framing, or time: the same landmark from a few positions counts).
Leave a photo unclicked if it is a different place or a different subject, even in the same city or trip. If none match, just press Enter. Keys: 1 to 9 toggle photos, Enter or &rarr; next, &larr; back.
Progress saves in this browser; "Save labels" downloads labels3.json.</div>
<script>
const ITEMS=__ITEMS__;let i=0;const key="scenelabels_v1";let labels={};try{labels=JSON.parse(localStorage.getItem(key)||"{}")}catch(e){}
function persist(){try{localStorage.setItem(key,JSON.stringify(labels))}catch(e){}}
function show(){const it=ITEMS[i];document.getElementById("Q").src=it.query;const g=document.getElementById("G");g.innerHTML="";
 const sel=new Set(labels[it.id]||[]);it.cands.forEach((c,k)=>{const d=document.createElement("div");d.className="c"+(sel.has(c.id)?" on":"");d.innerHTML='<img src="'+c.src+'">';
  d.onclick=()=>toggle(c.id);g.appendChild(d)});
 const done=Object.keys(labels).length;document.getElementById("count").textContent="query "+(i+1)+" of "+ITEMS.length+"   ·   done "+done;
 document.getElementById("prog").style.width=(100*done/ITEMS.length)+"%";}
function toggle(id){const it=ITEMS[i];const s=new Set(labels[it.id]||[]);s.has(id)?s.delete(id):s.add(id);labels[it.id]=[...s];persist();show()}
function next(){const it=ITEMS[i];if(!(it.id in labels)){labels[it.id]=[];persist()}if(i<ITEMS.length-1)i++;show()}
document.getElementById("next").onclick=next;document.getElementById("prev").onclick=()=>{if(i>0){i--;show()}};
addEventListener("keydown",e=>{if(e.key==="Enter"||e.key==="ArrowRight")next();else if(e.key==="ArrowLeft"&&i>0){i--;show()}
 else if(/^[1-9]$/.test(e.key)){const c=ITEMS[i].cands[+e.key-1];if(c)toggle(c.id)}});
document.getElementById("dl").onclick=()=>{const blob=new Blob([JSON.stringify(labels,null,1)],{type:"application/json"});const a=document.createElement("a");a.href=URL.createObjectURL(blob);a.download="labels3.json";a.click();};
for(let k=0;k<ITEMS.length;k++){if(!(ITEMS[k].id in labels)){i=k;break}}show();
</script></body></html>
"""

if __name__ == "__main__":
    main()
