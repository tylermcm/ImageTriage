"""Shared pair features for the grouping spike (round 2).

A "pair" is two neighbouring frames in one stream (album, photographer and camera), in capture order.
Classical features use only time, EXIF and cheap image statistics; DINOv2 features are cosine similarities of the stored
embeddings. ORB geometry is computed on demand from the review thumbnails (it is the slowest classical feature).
"""
from __future__ import annotations

import os

import cv2
import numpy as np

def load_features(path: str) -> dict:
    """Load every array once (NpzFile re-reads an array from disk on each access, which is very slow in loops)."""
    with np.load(path, allow_pickle=True) as data:
        return {key: data[key] for key in data.files}


POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def hamming64(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    x = np.bitwise_xor(a.astype(np.uint64), b.astype(np.uint64))
    return POPCOUNT[x.view(np.uint8).reshape(-1, 8)].sum(1).astype(float)


def _unit(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9)


def ssim_gray(a: np.ndarray, b: np.ndarray) -> float:
    """Mean structural similarity of two uint8 grey images of the same size (11 x 11 box window, standard constants)."""
    a = a.astype(np.float64)
    b = b.astype(np.float64)
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    ma, mb = cv2.blur(a, (7, 7)), cv2.blur(b, (7, 7))
    saa = cv2.blur(a * a, (7, 7)) - ma * ma
    sbb = cv2.blur(b * b, (7, 7)) - mb * mb
    sab = cv2.blur(a * b, (7, 7)) - ma * mb
    ssim = ((2 * ma * mb + c1) * (2 * sab + c2)) / ((ma * ma + mb * mb + c1) * (saa + sbb + c2))
    return float(ssim.mean())


def neighbour_pairs(f, max_gap: float = 3600.0) -> tuple[np.ndarray, np.ndarray]:
    """Indices (into the feature arrays) of consecutive frames inside one stream, closer than max_gap seconds."""
    stream = f["stream"]
    a = np.arange(len(stream) - 1)
    b = a + 1
    keep = (stream[a] == stream[b]) & ((f["ts"][b] - f["ts"][a]) <= max_gap)
    return a[keep], b[keep]


def pair_features(f, a: np.ndarray, b: np.ndarray) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    out["dt"] = f["ts"][b] - f["ts"][a]
    out["log_dt"] = np.log(out["dt"] + 0.05)
    out["ham_p"] = hamming64(f["phash"][a], f["phash"][b])
    out["ham_pn"] = hamming64(f["phash_norm"][a], f["phash_norm"][b])
    out["ham_d"] = hamming64(f["dhash"][a], f["dhash"][b]) if "dhash" in f else np.zeros(len(a))
    out["same_settings"] = ((f["iso"][a] == f["iso"][b]) & (f["fnumber"][a] == f["fnumber"][b]) & (f["focal"][a] == f["focal"][b])).astype(float)
    bias_a, bias_b = np.nan_to_num(f["bias"][a]), np.nan_to_num(f["bias"][b])
    out["bias_diff"] = np.abs(bias_a - bias_b)
    out["shutter_stops"] = np.abs(np.log2(np.maximum(f["exposure_s"][a], 1e-6) / np.maximum(f["exposure_s"][b], 1e-6)))
    out["portrait_change"] = (f["portrait"][a] != f["portrait"][b]).astype(float)
    if "hist" in f:
        ha, hb = f["hist"][a], f["hist"][b]
        out["hist_dist"] = 1.0 - np.sqrt(ha * hb).sum(1)  # Hellinger-like, 0 identical
        out["ssim"] = np.array([ssim_gray(f["gray64"][i], f["gray64"][j]) for i, j in zip(a, b)])
    for name in ("dino_cls", "dino_mean", "dino_cls_hi", "dino_mean_hi"):
        if name in f:
            e = _unit(f[name])
            out["cos_" + name[5:]] = np.einsum("ij,ij->i", e[a], e[b])
    cat = _unit(np.concatenate([_unit(f["dino_cls"]), _unit(f["dino_mean"])], axis=1))
    out["cos_cat"] = np.einsum("ij,ij->i", cat[a], cat[b])
    return out


def orb_inlier_ratio(thumb_a: str, thumb_b: str) -> float:
    """Share of ORB matches that agree with one homography (a geometry check that survives subject and camera movement)."""
    ia, ib = cv2.imread(thumb_a, cv2.IMREAD_GRAYSCALE), cv2.imread(thumb_b, cv2.IMREAD_GRAYSCALE)
    if ia is None or ib is None:
        return 0.0
    orb = cv2.ORB_create(nfeatures=600)
    ka, da = orb.detectAndCompute(ia, None)
    kb, db = orb.detectAndCompute(ib, None)
    if da is None or db is None or len(ka) < 12 or len(kb) < 12:
        return 0.0
    matches = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True).match(da, db)
    if len(matches) < 12:
        return 0.0
    src = np.float32([ka[m.queryIdx].pt for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([kb[m.trainIdx].pt for m in matches]).reshape(-1, 1, 2)
    _, mask = cv2.findHomography(src, dst, cv2.RANSAC, 6.0)
    if mask is None:
        return 0.0
    return float(mask.sum()) / float(max(len(ka), len(kb)))


def thumb_path(thumb_dir: str, index: int) -> str:
    return os.path.join(thumb_dir, f"{int(index)}.jpg")
