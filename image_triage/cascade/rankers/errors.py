"""Errors the ranker layer raises. Each carries a category the UI can turn into a specific message."""

from __future__ import annotations


class RankerError(RuntimeError):
    """Base class. ``category`` is stable and machine readable; ``remediation`` is a sentence for the user."""

    category = "ranker"

    def __init__(self, message: str, *, remediation: str = "") -> None:
        super().__init__(message)
        self.remediation = remediation


class RankerUnavailable(RankerError):
    """The managed AI runtime (Python, torch, transformers) cannot run this ranker."""

    category = "runtime_unavailable"


class RankerWeightsMissing(RankerError):
    """The model weights are not installed (or failed verification) and downloading was not allowed or not possible."""

    category = "weights_missing"


class RankerCancelled(RankerError):
    """The caller cancelled; the worker has been stopped."""

    category = "cancelled"


class RankerWorkerError(RankerError):
    """The worker crashed, timed out, or reported a failure."""

    category = "worker_failed"
