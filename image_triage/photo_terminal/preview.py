from __future__ import annotations

from typing import Optional

from PIL import Image, ImageOps


def ansi_preview(image: Image.Image, width: int = 80, height: Optional[int] = None) -> str:
    rgb = ImageOps.exif_transpose(image).convert("RGB")
    aspect = rgb.height / max(1, rgb.width)
    target_height = height or max(1, int(width * aspect * 0.5))
    resized = rgb.resize((width, target_height), Image.Resampling.BILINEAR)

    lines: list[str] = []
    pixels = resized.load()
    for y in range(resized.height):
        parts: list[str] = []
        for x in range(resized.width):
            r, g, b = pixels[x, y]
            parts.append(f"\x1b[48;2;{r};{g};{b}m ")
        parts.append("\x1b[0m")
        lines.append("".join(parts))
    return "\n".join(lines)
