from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from .models import ImageRecord, ImageVariant


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


class FolderScanCache:
    def __init__(self) -> None:
        self.root = app_data_root() / "scan-cache"
        self.root.mkdir(parents=True, exist_ok=True)

    def load(self, folder: str) -> list[ImageRecord] | None:
        cache_path = self._cache_path(folder)
        if not cache_path.exists():
            return None
        try:
            payload = json.loads(cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if payload.get("folder") != folder:
            return None

        records: list[ImageRecord] = []
        for item in payload.get("records", []):
            try:
                records.append(
                    ImageRecord(
                        path=item["path"],
                        name=item["name"],
                        size=int(item["size"]),
                        modified_ns=int(item["modified_ns"]),
                        companion_paths=tuple(item.get("companion_paths", [])),
                        edited_paths=tuple(item.get("edited_paths", [])),
                        variants=tuple(
                            ImageVariant(
                                path=variant["path"],
                                name=variant["name"],
                                size=int(variant["size"]),
                                modified_ns=int(variant["modified_ns"]),
                            )
                            for variant in item.get("variants", [])
                        ),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return records

    def save(self, folder: str, records: list[ImageRecord]) -> None:
        payload = {
            "folder": folder,
            "records": [
                {
                    "path": record.path,
                    "name": record.name,
                    "size": record.size,
                    "modified_ns": record.modified_ns,
                    "companion_paths": list(record.companion_paths),
                    "edited_paths": list(record.edited_paths),
                    "variants": [
                        {
                            "path": variant.path,
                            "name": variant.name,
                            "size": variant.size,
                            "modified_ns": variant.modified_ns,
                        }
                        for variant in record.display_variants
                    ],
                }
                for record in records
            ],
        }
        cache_path = self._cache_path(folder)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _cache_path(self, folder: str) -> Path:
        digest = hashlib.sha1(folder.encode("utf-8"), usedforsecurity=False).hexdigest()
        return self.root / f"{digest}.json"
