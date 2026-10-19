"""What distinguishes one ranker from another. Swapping a ranker (the weights' licences are unverified) means adding a RankerSpec."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RankerSpec:
    kind: str  # worker ``--kind`` argument
    name: str  # for messages
    bundle_key: str  # key in ai_manifest.MODEL_BUNDLES (see bundles.register_bundles)
    batch_size: int  # frames sent to the worker per request; also the granularity of cancellation checks between requests
    seconds_per_frame_gpu: float  # measured on a V100
    seconds_per_frame_cpu: float  # measured on an 8-thread CPU


MANIQA = RankerSpec("maniqa", "MANIQA", "maniqa", batch_size=8, seconds_per_frame_gpu=0.54, seconds_per_frame_cpu=3.6)
QREALIGN = RankerSpec("qrealign", "Q-ReAlign", "qrealign", batch_size=4, seconds_per_frame_gpu=0.59, seconds_per_frame_cpu=3.4)

SPECS = {spec.kind: spec for spec in (MANIQA, QREALIGN)}
