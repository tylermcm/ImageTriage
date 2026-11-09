"""Mask display modes extracted from the retired editor; rendering recipes stay native."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

OVERLAY_RED = QColor(255, 64, 64, 128)
OVERLAY_DISPLAY_MODES = frozenset(("color", "color-bw", "image-bw", "image-black", "image-white", "white-black"))

def compose_mask_overlay(
    strength: QImage,
    mode: str,
    color: QColor,
    base_image: QImage | None = None,
) -> QImage:
    """Build the visual overlay for a grayscale mask strength field.

    This changes only how a mask is previewed. The grayscale mask itself stays
    untouched and remains the source of truth for editing and export.
    """
    if strength.isNull():
        return QImage()
    alpha = strength.convertToFormat(QImage.Format.Format_Grayscale8)
    width, height = alpha.width(), alpha.height()
    normalized_mode = mode if mode in OVERLAY_DISPLAY_MODES else "color"
    overlay_color = QColor(color)
    if not overlay_color.isValid():
        overlay_color = QColor(OVERLAY_RED)

    def transparent_canvas() -> QImage:
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        return image

    def solid_layer(fill: QColor, layer_alpha: QImage) -> QImage:
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        opaque = QColor(fill)
        opaque.setAlpha(255)
        image.fill(opaque)
        image.setAlphaChannel(layer_alpha)
        return image

    def draw_tint(target: QImage) -> None:
        tint = solid_layer(overlay_color, alpha)
        painter = QPainter(target)
        painter.setOpacity(overlay_color.alphaF())
        painter.drawImage(0, 0, tint)
        painter.end()

    base: QImage | None = None
    if base_image is not None and not base_image.isNull():
        base = base_image.convertToFormat(QImage.Format.Format_ARGB32)
        if base.size() != alpha.size():
            base = base.scaled(
                alpha.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

    if normalized_mode == "color" or (
        normalized_mode in ("color-bw", "image-bw") and base is None
    ):
        result = transparent_canvas()
        draw_tint(result)
        return result

    if normalized_mode == "color-bw":
        result = base.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        draw_tint(result)
        return result

    if normalized_mode == "image-bw":
        result = base.convertToFormat(QImage.Format.Format_Grayscale8).convertToFormat(
            QImage.Format.Format_ARGB32_Premultiplied
        )
        selected = base.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        selected.setAlphaChannel(alpha)
        painter = QPainter(result)
        painter.drawImage(0, 0, selected)
        painter.end()
        return result

    if normalized_mode in ("image-black", "image-white"):
        outside = alpha.copy()
        outside.invertPixels()
        fill = QColor("#000000" if normalized_mode == "image-black" else "#ffffff")
        return solid_layer(fill, outside)

    # White mask strength on an opaque black field.
    result = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    result.fill(QColor("#000000"))
    selected = solid_layer(QColor("#ffffff"), alpha)
    painter = QPainter(result)
    painter.drawImage(0, 0, selected)
    painter.end()
    return result
