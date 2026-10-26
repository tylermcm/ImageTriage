"""Line protocol between the app (client) and a ranker worker subprocess.

Both directions are one JSON object per line. Worker lines start with ``PREFIX`` so that anything else a library prints to stdout
(progress bars, warnings) is ignored instead of corrupting the stream. No torch import here: the client side uses this module.

client -> worker   {"op": "score", "id": 1, "items": [{"key": "...", "path": "..."}]}      {"op": "quit"}
worker -> client   {"event": "ready", "device": "cuda", "precision": "fp16", "seconds": 4.2}
                   {"event": "score", "id": 1, "key": "...", "value": 0.61}
                   {"event": "frame_error", "id": 1, "key": "...", "message": "..."}      (that frame only; the batch goes on)
                   {"event": "done", "id": 1}
                   {"event": "error", "category": "...", "message": "..."}                (fatal; the worker exits)
"""

from __future__ import annotations

import json

PREFIX = "RANKER "
PROTOCOL_VERSION = 1


def encode(message: dict[str, object]) -> str:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=True) + "\n"


def encode_event(message: dict[str, object]) -> str:
    return PREFIX + json.dumps(message, separators=(",", ":"), ensure_ascii=True)


def decode_event(line: str) -> dict[str, object] | None:
    """The event on a worker stdout line, or None for lines that are not ours or are malformed."""
    if not line.startswith(PREFIX):
        return None
    try:
        payload = json.loads(line[len(PREFIX):])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None
