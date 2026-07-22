"""Full-resolution edited-copy writing for the popout photo editor."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QSize, QThreadPool, Signal

from .formats import suffix_for_path
from .image_ops import (
    load_image_for_transform,
    normalized_output_path_key,
    save_transformed_image,
)
from .image_resize import WRITABLE_IMAGE_SUFFIXES


SAVE_COPY_FILTERS = (
    "JPEG image (*.jpg *.jpeg)",
    "PNG image (*.png)",
    "TIFF image (*.tif *.tiff)",
    "WebP image (*.webp)",
    "BMP image (*.bmp)",
)
SAVE_COPY_FILTER_TEXT = ";;".join(SAVE_COPY_FILTERS)

_FILTER_SUFFIXES = {
    SAVE_COPY_FILTERS[0]: ".jpg",
    SAVE_COPY_FILTERS[1]: ".png",
    SAVE_COPY_FILTERS[2]: ".tif",
    SAVE_COPY_FILTERS[3]: ".webp",
    SAVE_COPY_FILTERS[4]: ".bmp",
}


def default_save_copy_path(source_path: str | Path) -> Path:
    """Return a non-colliding sibling name that remains an edit-stack variant."""
    source = Path(source_path)
    source_suffix = suffix_for_path(source)
    suffix = source_suffix if source_suffix in WRITABLE_IMAGE_SUFFIXES else ".jpg"
    index = 1
    while True:
        candidate = source.with_name(f"{source.stem}_{index}{suffix}")
        if not candidate.exists():
            return candidate
        index += 1


def selected_save_copy_filter(source_path: str | Path) -> str:
    suffix = suffix_for_path(source_path)
    if suffix == ".png":
        return SAVE_COPY_FILTERS[1]
    if suffix in {".tif", ".tiff"}:
        return SAVE_COPY_FILTERS[2]
    if suffix == ".webp":
        return SAVE_COPY_FILTERS[3]
    if suffix == ".bmp":
        return SAVE_COPY_FILTERS[4]
    return SAVE_COPY_FILTERS[0]


def normalize_save_copy_path(path: str | Path, selected_filter: str = "") -> Path:
    target = Path(path)
    if not target.suffix:
        target = target.with_suffix(_FILTER_SUFFIXES.get(selected_filter, ".jpg"))
    return target


def validate_save_copy_paths(source_path: str | Path, target_path: str | Path) -> str:
    source = Path(source_path)
    target = Path(target_path)
    suffix = suffix_for_path(target)
    if suffix not in WRITABLE_IMAGE_SUFFIXES:
        raise ValueError("Choose JPG, PNG, TIFF, WebP, or BMP for the saved copy.")
    source_key = normalized_output_path_key(source.resolve(strict=False))
    target_key = normalized_output_path_key(target.resolve(strict=False))
    if source_key == target_key:
        raise ValueError("Save Copy cannot overwrite the original image.")
    return suffix


def write_edited_copy(
    source_path: str | Path,
    target_path: str | Path,
    recipe: Any,
    masked_adjustments: list,
) -> Path:
    """Decode at full resolution, apply current edits, and atomically write a copy."""
    source = Path(source_path)
    target = Path(target_path)
    target_suffix = validate_save_copy_paths(source, target)
    loaded = load_image_for_transform(
        str(source),
        target_size=QSize(),
        ignore_orientation=False,
        strip_metadata=False,
    )
    if loaded.image.isNull():
        raise OSError(f"Could not decode {source.name} for saving.")

    # Lazy import avoids a UI-package import cycle while the editor panel is
    # being constructed. It also guarantees export and preview share one
    # rendering implementation.
    from .editor_render import CpuEditorRenderBackend

    rendered = CpuEditorRenderBackend().render(
        loaded.image,
        recipe,
        masked_adjustments,
        base_key=("save-copy", str(source.resolve(strict=False))),
    )
    if rendered.isNull():
        raise OSError(f"Could not render {source.name}.")
    target.parent.mkdir(parents=True, exist_ok=True)
    save_transformed_image(
        rendered,
        target_path=str(target),
        target_suffix=target_suffix,
        exif_bytes=loaded.exif_bytes,
        icc_profile=loaded.icc_profile,
    )
    return target


class _EditorCopyRunnable(QRunnable):
    def __init__(
        self,
        service: "EditorCopyService",
        source_path: str,
        target_path: str,
        recipe: Any,
        masked_adjustments: list,
    ) -> None:
        super().__init__()
        self._service = service
        self._source_path = source_path
        self._target_path = target_path
        self._recipe = recipe
        self._masked_adjustments = masked_adjustments

    def run(self) -> None:
        try:
            written = write_edited_copy(
                self._source_path,
                self._target_path,
                self._recipe,
                self._masked_adjustments,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in the editor UI
            self._service._worker_done.emit(self._source_path, self._target_path, str(exc))
            return
        self._service._worker_done.emit(self._source_path, str(written), "")


class EditorCopyService(QObject):
    """Single-flight background writer for full-resolution editor copies."""

    saved = Signal(str, str)  # source path, written copy path
    failed = Signal(str, str)  # requested copy path, error
    _worker_done = Signal(str, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self._active = False
        self._worker_done.connect(self._handle_worker_done)

    @property
    def active(self) -> bool:
        return self._active

    def request(
        self,
        source_path: str,
        target_path: str,
        recipe: Any,
        masked_adjustments: list,
    ) -> bool:
        if self._active:
            return False
        self._active = True
        self._pool.start(
            _EditorCopyRunnable(
                self,
                source_path,
                target_path,
                recipe,
                masked_adjustments,
            )
        )
        return True

    def _handle_worker_done(self, source_path: str, target_path: str, error: str) -> None:
        self._active = False
        if error:
            self.failed.emit(target_path, error)
        else:
            self.saved.emit(source_path, target_path)
