#!/usr/bin/env bash
# Run after run_training.sh has printed "done". Installs nothing; writes only under runs/full_mnv3/report.
set -uo pipefail
BASE=/media/dixie/ColossalBoi/imagetriage-scratch/iqa_synth
RUN=runs/full_mnv3
cd "$BASE"
if [ ! -f "$RUN/model.pt" ]; then echo "no $RUN/model.pt yet: has run_training.sh finished?"; exit 1; fi

echo "== training history (last 3 epochs) =="
python3 - <<'EOF'
import json
h = json.load(open("runs/full_mnv3/history.json"))
for r in h[-3:]:
    print(json.dumps(r))
EOF

echo; echo "== real-photo check on held-out folders =="
nice -n 5 python3 -u eval_real.py --data data --run "$RUN" --out "$RUN/report" 2>&1 | grep -v -i -E 'optree|warnings.warn'

echo; echo "== ONNX export =="
if python3 -c "import onnx" 2>/dev/null; then
  python3 - <<'EOF'
import torch
from train import QualityNet, ScoreOnly
net = QualityNet("mobilenet_v3_large")
net.load_state_dict(torch.load("runs/full_mnv3/model.pt", map_location="cpu"))
net.eval()
torch.onnx.export(ScoreOnly(net), torch.zeros(1, 3, 384, 384), "runs/full_mnv3/report/quality.onnx",
                  input_names=["image_bgr"], output_names=["quality"],
                  dynamic_axes={"image_bgr": {0: "n", 2: "h", 3: "w"}}, opset_version=17)
print("wrote runs/full_mnv3/report/quality.onnx")
EOF
else
  echo "the onnx package is not installed, so no export. The checkpoint runs/full_mnv3/model.pt is saved."
fi

echo; echo "files:"; ls -la "$RUN/report"
echo "done. Tell Claude it finished; the summary is in $RUN/report/summary.json"
