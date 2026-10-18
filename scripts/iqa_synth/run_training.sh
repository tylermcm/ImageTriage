#!/usr/bin/env bash
# Runs on the server and survives disconnects when started with nohup or inside tmux.
# 1. waits for prepare_dataset.py to finish (data/manifest.json appears),
# 2. sets the holdout folders (kept out of training, used only for validation),
# 3. trains a short smoke run, then the full run. Logs and checkpoints go under $BASE/runs.
set -euo pipefail
BASE=/media/dixie/ColossalBoi/imagetriage-scratch/iqa_synth
cd "$BASE"
echo "$(date) waiting for dataset"
until [ -f data/manifest.json ]; do sleep 20; done
sleep 5
python3 - <<'EOF'
import json
path = "data/manifest.json"
manifest = json.load(open(path, encoding="utf-8"))
holdout = {"Japan 4-24", "Iceland 10-24"}
for item in manifest:
    item["split"] = "holdout" if item["folder"] in holdout else "train"
json.dump(manifest, open(path, "w", encoding="utf-8"))
print("train", sum(i["split"] == "train" for i in manifest), "holdout", sum(i["split"] == "holdout" for i in manifest))
EOF
mkdir -p runs
echo "$(date) smoke run"
nice -n 5 python3 -u train.py --data data --out runs/smoke --model mobilenet_v3_large --epochs 20 --eval-every 5 --workers 32 2>&1 | grep -v -i -E 'optree|warnings.warn' | tee runs/smoke.log
echo "$(date) full run"
nice -n 5 python3 -u train.py --data data --out runs/full_mnv3 --model mobilenet_v3_large --epochs 150 --eval-every 10 --workers 32 2>&1 | grep -v -i -E 'optree|warnings.warn' | tee runs/full_mnv3.log
echo "$(date) done"
