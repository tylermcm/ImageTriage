"""Bounded click corrections shared with PhotoCraft's native RAW recipe."""
import math


def valid(items):
    if not isinstance(items, list) or len(items) > 64:
        return False
    for item in items:
        if not isinstance(item, dict) or set(item) != {"point", "pupilSize", "darken"}:
            return False
        point = item["point"]
        if not isinstance(point, list) or len(point) != 2:
            return False
        values = [(value, 0, 1) for value in point]
        values += [(item["pupilSize"], 1, 100), (item["darken"], 0, 100)]
        if any(type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi for v, lo, hi in values):
            return False
    return True
