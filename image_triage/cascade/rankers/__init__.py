"""AI v5 rankers: MANIQA and Q-ReAlign Mini as ``Scorer`` callables backed by worker subprocesses (see docs/ai_v5_implementation.md).

Importing this package never imports torch; the models run only inside ``worker.py`` in the managed AI runtime.
"""

from __future__ import annotations

from .bundles import ensure_weights, register_bundles
from .client import WorkerLaunch, WorkerRanker, default_launcher, maniqa_ranker, qrealign_ranker
from .costs import RankingCostEstimate, estimate_cascade_cost, estimate_cost
from .errors import RankerCancelled, RankerError, RankerUnavailable, RankerWeightsMissing, RankerWorkerError
from .spec import MANIQA, QREALIGN, SPECS, RankerSpec

__all__ = [
    "MANIQA",
    "QREALIGN",
    "SPECS",
    "RankerCancelled",
    "RankerError",
    "RankerSpec",
    "RankerUnavailable",
    "RankerWeightsMissing",
    "RankerWorkerError",
    "RankingCostEstimate",
    "WorkerLaunch",
    "WorkerRanker",
    "default_launcher",
    "ensure_weights",
    "estimate_cascade_cost",
    "estimate_cost",
    "maniqa_ranker",
    "qrealign_ranker",
    "register_bundles",
]
