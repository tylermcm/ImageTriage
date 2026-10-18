#!/usr/bin/env bash
# Version 2: broader, balanced training set (all of Photography), 90-degree rotation augmentation.
# Survives disconnects when started with nohup or tmux. Writes under data_v2 and runs/v2_mnv3.
set -euo pipefail
BASE=/media/dixie/ColossalBoi/imagetriage-scratch/iqa_synth
cd "$BASE"
if [ ! -f data_v2/manifest.json ]; then
  echo "$(date) preparing dataset"
  nice -n 10 python3 -u prepare_dataset_v2.py --root /media/dixie/ColossalBoi/Photography --out data_v2 --total 14000 --exclude "Astrophotography" 2>&1 | grep -v -i -E 'warn' | tee prep_v2.log
fi
mkdir -p runs
echo "$(date) training v2"
nice -n 5 python3 -u train.py --data data_v2 --out runs/v2_mnv3 --model mobilenet_v3_large --epochs 100 --eval-every 10 --workers 32 2>&1 | grep -v -i -E 'optree|warnings.warn' | tee runs/v2_mnv3.log
echo "$(date) report"
nice -n 5 python3 -u eval_real.py --data data_v2 --run runs/v2_mnv3 --out runs/v2_mnv3/report 2>&1 | grep -v -i -E 'optree|warn|whole =' | tee runs/v2_report.log
echo "$(date) done"
