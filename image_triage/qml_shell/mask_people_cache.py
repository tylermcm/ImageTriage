"""Private, source- and model-versioned person coverage cache; culling face hints stay shared."""
import hashlib
import json
import os
from pathlib import Path

from ..camera_raw_recipe_masks import masks_valid


def cache_path(path, signature):
    from ..ai_paths import managed_cache_dir
    from ..ai_manifest import MODEL_BUNDLES
    versions = [MODEL_BUNDLES[k].revision for k in ("oneformer","sam","birefnet")]
    key = json.dumps([os.path.normcase(os.path.abspath(path)),signature,versions,"people-v1"])
    return managed_cache_dir("editor_people")/(hashlib.sha256(key.encode()).hexdigest()+".json")


def load(path, signature):
    if not signature:return None
    try:
        target=cache_path(path,signature)
        if target.stat().st_size>32*1024*1024:return None
        data=json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data,list) or len(data)>16:return None
        if any(not isinstance(r,dict) or not isinstance(r.get("label"),str) or not masks_valid([{"shape":r.get("shape")}]) for r in data):return None
        return data
    except (OSError,ValueError,TypeError):return None


def save(path, signature, results):
    if not signature:return
    from ..raw_crop import atomic_write
    target=cache_path(path,signature)
    target.parent.mkdir(parents=True,exist_ok=True)
    atomic_write(target,json.dumps(results[:16]).encode())


def culling_faces(path):
    from ..aiculler_workflow import build_aiculler_workflow_paths
    from ..cascade.faces import FaceCache
    try:
        paths=build_aiculler_workflow_paths(str(Path(path).parent),resolve=False)
        found=FaceCache(paths.artifacts_dir/"faces_cache.json").get(path)
        return None if found is None else len(found)
    except (OSError,ValueError,TypeError):return None
