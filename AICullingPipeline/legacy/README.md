# Legacy code parked from the pairwise-neural-ranker era

Image Triage moved from a PyTorch pairwise ranker to a **transparent linear
combiner** trained on per-image Accept/Reject decisions surfaced through the
Speed Cull UI. The files in this directory drove the older path and are
retained here for reference, not part of the active pipeline.

## What lives here

```
legacy/
├── scripts/
│   ├── train_ranker.py        # CLI: train the old PyTorch pairwise ranker
│   ├── evaluate_ranker.py     # CLI: evaluate a trained ranker checkpoint
│   └── score_clusters.py      # CLI: score saved clusters with a trained ranker
└── configs/
    ├── train_ranker.json
    ├── evaluate_ranker.json
    └── score_clusters.json
```

## What the active pipeline uses instead

| Concern | Replacement |
|---|---|
| Label collection | Speed Cull (`Review → Speed Cull…`) writes Accept/Reject directly into the host `DecisionStore` (`decisions.sqlite3`). |
| Bridge to trainer input | `scripts/harvest_decisions.py` reads `DecisionStore` and emits `decision_labels.jsonl` plus `cluster_labels.jsonl` (best/reject buckets, acceptable always empty). |
| Model training | `app.engine.signals.training.train_signal_combiner` — fits a transparent linear combiner over technical / face / aesthetic / DINO signals using preference pairs derived from `cluster_labels.jsonl`. |
| Scoring future shoots | Combiner weights apply at the `signals` layer; no separate ranker checkpoint is loaded. |

## Engine code that's still wired up

`app/engine/ranking/` was not moved. Some files there (notably
`exports.py`, `reference_bank.py`, `reporting.py`) are still imported by the
live combiner / adapter code. The pairwise-ranker-specific modules in that
package (`trainer.py`, `inference.py`, `service.py`, `datasets.py`,
`models.py`, `preference_sampling.py`) are effectively dead but left in place
to avoid breaking imports from `app/engine/integration.py`. They can be moved
here in a follow-up once the integration adapter is either rewired through the
combiner or itself retired.

## Reactivating any of this (don't, but if you must)

Each script is self-contained and can run directly from this directory:

```
python AICullingPipeline\legacy\scripts\train_ranker.py --config AICullingPipeline\legacy\configs\train_ranker.json
```

They still expect the original `pairwise_labels.jsonl` format (no longer
produced by Speed Cull). To recreate that input you would need to revive the
pairwise labeling surface that was removed during the Speed Cull pivot — see
prior commits if needed.
