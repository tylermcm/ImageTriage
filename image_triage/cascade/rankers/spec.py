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
    needs_torch: bool = True  # False: runs in the compact base runtime (no PyTorch), e.g. the blink detector
    aux_bundle_key: str = ""  # a second model bundle the worker also needs (passed as --aux-dir), e.g. the face recognizer next to the landmarker


MANIQA = RankerSpec("maniqa", "MANIQA", "maniqa", batch_size=8, seconds_per_frame_gpu=0.54, seconds_per_frame_cpu=3.6)
QREALIGN = RankerSpec("qrealign", "Q-ReAlign", "qrealign", batch_size=4, seconds_per_frame_gpu=0.59, seconds_per_frame_cpu=3.4)

# Not a ranker but the same worker machinery: scores how closed the eyes are (see ../blink.py). CPU only; timing is measured, see docs/ai_v5_implementation.md.
BLINK = RankerSpec("blink", "Blink detector", "faces_blink", batch_size=16, seconds_per_frame_gpu=0.1, seconds_per_frame_cpu=0.1, needs_torch=False)

# The People index's face finder: the blink landmarker finds each face, a recognizer (the second bundle) embeds it. See ../faces.py. CPU only, no torch.
FACES = RankerSpec("faces", "Face finder", "faces_blink", batch_size=8, seconds_per_frame_gpu=0.2, seconds_per_frame_cpu=0.2, needs_torch=False, aux_bundle_key="faces_identity")

SPECS = {spec.kind: spec for spec in (MANIQA, QREALIGN, BLINK, FACES)}
