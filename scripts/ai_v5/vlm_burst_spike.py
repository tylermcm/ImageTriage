#!/usr/bin/env python3
"""Can a pretrained, permissively licensed vision-language model pick the frame the owner kept from a burst?

Zero-shot, no training. Three ways of asking, each read from next-token probabilities (not free text) so results are stable:
  listwise  all frames of the burst in one prompt, ask for the number of the best one; averaged over several orderings
  pairwise  every pair of frames in both orders, ask which is better; a frame's score is its average win probability
  rating    each frame alone, rate it 1 to 9 for how good a photograph to keep; the score is the expected rating

The criterion given to the model is deliberately photographic and specific (sharp subject, eyes open, natural expression,
good moment and composition), without telling it which frame the owner chose.

Scored on burst_picks/bursts.json (63 bursts, exactly one known keeper each). Pre-registered rule, set before any result:
a method is promising only if its top-1 pick rate is at least 0.45 AND the paired bootstrap interval of its difference from the
best simple baseline (least clipped highlights, 0.349) lies above zero. The pairwise accuracy (keeper preferred over a
non-keeper) must also have an interval above 0.5.

  venv: vlmenv/bin/python vlm_burst_spike.py --model models/Qwen3-VL-4B-Instruct --bursts burst_picks --out vlm_4b
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import time

import numpy as np
import torch
from PIL import Image
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

CRITERION = ("You are helping a photographer choose which frame of a burst to keep. Prefer the frame where the main subject is sharp, "
             "eyes are open, the expression is natural and flattering, and the moment and composition are strongest. "
             "Ignore small differences in colour or exposure.")


def load_image(path: str, long_side: int) -> Image.Image:
    im = Image.open(path).convert("RGB")
    scale = long_side / max(im.size)
    return im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS) if scale < 1 else im


class Judge:
    def __init__(self, model_path: str, long_side: int, dtype: torch.dtype) -> None:
        self.processor = AutoProcessor.from_pretrained(model_path)
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(model_path, dtype=dtype, device_map="cuda", attn_implementation="sdpa").eval()
        self.long_side = long_side
        tok = self.processor.tokenizer
        self.digit_ids = [tok.encode(str(d), add_special_tokens=False)[0] for d in range(1, 10)]
        self.letter_ids = [tok.encode(c, add_special_tokens=False)[0] for c in "AB"]

    @torch.no_grad()
    def next_token_logits(self, content: list[dict]) -> torch.Tensor:
        messages = [{"role": "user", "content": content}]
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt").to("cuda")
        out = self.model(**inputs)
        return out.logits[0, -1].float()

    def listwise(self, paths: list[str], order: list[int]) -> np.ndarray:
        content: list[dict] = [{"type": "text", "text": CRITERION + f" Below are {len(order)} frames, numbered in order."}]
        side = self.long_side if len(order) <= 4 else self.long_side * 2 // 3  # keep many-frame prompts inside GPU memory
        for k, i in enumerate(order, start=1):
            content += [{"type": "text", "text": f"Frame {k}:"}, {"type": "image", "image": load_image(paths[i], side)}]
        content.append({"type": "text", "text": f"Which frame should be kept? Answer with only the number, 1 to {len(order)}."})
        logits = self.next_token_logits(content)[self.digit_ids[: len(order)]]
        probs = torch.softmax(logits, 0).cpu().numpy()
        scores = np.zeros(len(order))
        for k, i in enumerate(order):
            scores[i] = probs[k]
        return scores

    def pairwise(self, a: str, b: str) -> float:
        content = [{"type": "text", "text": CRITERION + " Two frames follow."}, {"type": "text", "text": "Frame A:"}, {"type": "image", "image": load_image(a, self.long_side)},
                   {"type": "text", "text": "Frame B:"}, {"type": "image", "image": load_image(b, self.long_side)},
                   {"type": "text", "text": "Which frame is better to keep? Answer with only A or B."}]
        logits = self.next_token_logits(content)[self.letter_ids]
        return float(torch.softmax(logits, 0)[0])

    def rating(self, path: str) -> float:
        content = [{"type": "text", "text": CRITERION + " Rate this single frame from 1 (poor, would reject) to 9 (excellent, would definitely keep)."},
                   {"type": "image", "image": load_image(path, self.long_side)}, {"type": "text", "text": "Answer with only a number from 1 to 9."}]
        probs = torch.softmax(self.next_token_logits(content)[self.digit_ids], 0).cpu().numpy()
        return float((probs * np.arange(1, 10)).sum())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--bursts", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--long-side", type=int, default=768)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--orderings", type=int, default=3)
    parser.add_argument("--dtype", default="float16")
    parser.add_argument("--tasks", nargs="+", default=["listwise", "pairwise", "rating"])
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)
    bursts = json.load(open(os.path.join(args.bursts, "bursts.json")))
    bursts = [b for b in bursts if len(b["frames"]) <= 9]  # next-token readout covers digits 1 to 9
    bursts = bursts[: args.limit] if args.limit else bursts
    judge = Judge(args.model, args.long_side, getattr(torch, args.dtype))
    rng = np.random.default_rng(0)
    results = []
    started = time.time()
    for n, b in enumerate(bursts):
        paths, k = b["frames"], len(b["frames"])
        row = {"id": b["id"], "pick": b["pick"], "n": k}
        if "listwise" in args.tasks:
            scores = np.zeros(k)
            for r in range(args.orderings):
                order = list(rng.permutation(k)) if r else list(range(k))
                scores += judge.listwise(paths, order)
            row["listwise"] = (scores / args.orderings).tolist()
        if "pairwise" in args.tasks:
            wins = np.zeros(k)
            matrix = {}
            for i, j in itertools.combinations(range(k), 2):
                p = 0.5 * (judge.pairwise(paths[i], paths[j]) + (1.0 - judge.pairwise(paths[j], paths[i])))
                wins[i] += p; wins[j] += 1.0 - p
                matrix[f"{i},{j}"] = p
            row["pairwise"] = (wins / max(1, k - 1)).tolist()
            row["pair_matrix"] = matrix
        if "rating" in args.tasks:
            row["rating"] = [judge.rating(p) for p in paths]
        results.append(row)
        if n % 5 == 0:
            print(f"burst {n + 1}/{len(bursts)} done, {time.time() - started:.0f}s", flush=True)
            json.dump(results, open(os.path.join(args.out, "results.json"), "w"))
    json.dump(results, open(os.path.join(args.out, "results.json"), "w"))
    print(f"finished {len(results)} bursts in {time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
