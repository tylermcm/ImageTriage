from __future__ import annotations

import os
from pathlib import Path


def app_data_root() -> Path:
    candidates = [
        os.environ.get("IMAGE_TRIAGE_APPDATA", ""),
        os.environ.get("APPDATA", ""),
    ]
    for value in candidates:
        if value:
            root = Path(value) / "ImageTriage"
            root.mkdir(parents=True, exist_ok=True)
            return root
    root = Path.home() / ".image-triage"
    root.mkdir(parents=True, exist_ok=True)
    return root
