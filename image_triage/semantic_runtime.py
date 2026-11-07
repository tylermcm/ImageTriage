"""Resolve the installed TinyCLIP bundle for host search and native imports."""
from dataclasses import replace
import os


def default_search_runtime():
    from .aiculler_workflow import default_aiculler_runtime
    from .ai_model import resolve_aiculler_clip_model_installation

    runtime = default_aiculler_runtime()
    # Explicit low-level paths remain authoritative, including diagnostics
    # configured to point at a missing file. Otherwise prefer the installed
    # canonical bundle over a legacy cache directory containing a different CLIP.
    if any(os.environ.get(key, "").strip() for key in (
        "IMAGE_TRIAGE_AICULLER_CLIP_TEXT", "IMAGE_TRIAGE_AICULLER_CLIP_VISION",
        "IMAGE_TRIAGE_AICULLER_TOKENIZER", "IMAGE_TRIAGE_AICULLER_MODEL_ROOT")):
        return runtime
    root = resolve_aiculler_clip_model_installation().install_dir
    model, tokenizer = root / "onnx/model.onnx", root / "tokenizer.json"
    if model.is_file() and tokenizer.is_file():
        return replace(runtime, clip_vision_model=model, clip_text_model=model, tokenizer=tokenizer)
    return runtime
