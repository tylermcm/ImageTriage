"""Image loading helpers for model inference."""

from __future__ import annotations

from pathlib import Path

from PIL import Image


DEFAULT_INFERENCE_DECODE_SCALE = 3
DEFAULT_INFERENCE_LONG_EDGE_MULTIPLIER = 4


def load_rgb_for_inference(
    path: str | Path,
    *,
    target_short_edge: int = 224,
    decode_scale: int = DEFAULT_INFERENCE_DECODE_SCALE,
    long_edge_multiplier: int = DEFAULT_INFERENCE_LONG_EDGE_MULTIPLIER,
) -> Image.Image:
    """Load an image as RGB after reducing oversized sources for model inference."""

    target_short = max(1, int(target_short_edge or 224))
    decode_short = max(target_short, target_short * max(1, int(decode_scale)))
    max_long_edge = decode_short * max(1, int(long_edge_multiplier))

    source = Image.open(path)
    prepared = source
    try:
        _apply_decoder_draft(prepared, decode_short)
        prepared = _resize_for_inference(
            prepared,
            decode_short=decode_short,
            max_long_edge=max_long_edge,
        )
        rgb_image = prepared.convert("RGB")
        if rgb_image is prepared:
            rgb_image = prepared.copy()
        rgb_image.load()
        return rgb_image
    finally:
        if prepared is not source:
            prepared.close()
        source.close()


def _apply_decoder_draft(image: Image.Image, decode_short: int) -> None:
    try:
        image.draft("RGB", (decode_short, decode_short))
    except (AttributeError, OSError, ValueError):
        return


def _resize_for_inference(
    image: Image.Image,
    *,
    decode_short: int,
    max_long_edge: int,
) -> Image.Image:
    width, height = image.size
    if width <= 0 or height <= 0:
        return image

    short_edge = min(width, height)
    scale = 1.0
    if short_edge > decode_short:
        scale = min(scale, float(decode_short) / float(short_edge))
    if max(width, height) * scale > max_long_edge:
        scale = min(scale, float(max_long_edge) / float(max(width, height)))

    if scale >= 1.0:
        return image

    new_size = (
        max(1, int(round(width * scale))),
        max(1, int(round(height * scale))),
    )
    return image.resize(new_size, Image.Resampling.BOX)
