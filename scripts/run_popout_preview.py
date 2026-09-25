r"""Open the live Image Triage popout without constructing MainWindow.

From the repository root::

    .\.msi_build_venv\Scripts\python.exe scripts\run_popout_preview.py
    .\.msi_build_venv\Scripts\python.exe scripts\run_popout_preview.py C:\photos\frame.jpg

This is a UI preview harness. It reads neighboring photos for filmstrip
navigation, but does not open the library database or persist annotations.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QCoreApplication, QSize, QTimer  # noqa: E402
from PySide6.QtGui import QImageReader, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from image_triage.formats import is_image_file_candidate  # noqa: E402
from image_triage.models import ImageRecord  # noqa: E402
from image_triage.preview import FullScreenPreview, PreviewEntry  # noqa: E402


DEFAULT_IMAGE = REPO_ROOT / "scripts" / "assets" / "mountain_scape.png"


def image_paths(target: Path) -> tuple[list[Path], int]:
    """Return the selected photo and readable sibling candidates in name order."""
    target = target.expanduser().resolve()
    if not target.is_file() or not is_image_file_candidate(target):
        raise ValueError(f"Not an image file: {target}")
    siblings = sorted(
        (path for path in target.parent.iterdir() if path.is_file() and is_image_file_candidate(path)),
        key=lambda path: path.name.casefold(),
    )
    return siblings, siblings.index(target)


def parse_size(value: str) -> tuple[int, int]:
    try:
        width_text, height_text = value.lower().split("x", 1)
        width, height = int(width_text), int(height_text)
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError("Size must be WIDTHxHEIGHT, e.g. 1440x900") from error
    if width < 640 or height < 480:
        raise argparse.ArgumentTypeError("Size must be at least 640x480")
    return width, height


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open only the live Image Triage popout preview.")
    parser.add_argument("image", nargs="?", type=Path, default=DEFAULT_IMAGE,
                        help="Image to open; defaults to the bundled landscape sample.")
    parser.add_argument("--size", type=parse_size, metavar="WIDTHxHEIGHT",
                        help="Open at this logical window size instead of maximized.")
    parser.add_argument("--capture", type=Path, metavar="PNG",
                        help="Save a screenshot once the preview loads, then exit.")
    args = parser.parse_args(argv)
    try:
        paths, current = image_paths(args.image)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    QCoreApplication.setOrganizationName("Codex")
    QCoreApplication.setApplicationName("Image Triage")
    app = QApplication([sys.argv[0]])
    preview = FullScreenPreview()
    thumb_cache: dict[int, QPixmap | None] = {}

    def thumbnail(index: int) -> QPixmap | None:
        if index not in thumb_cache:
            reader = QImageReader(str(paths[index]))
            reader.setAutoTransform(True)
            reader.setScaledSize(QSize(160, 110))
            image = reader.read()
            thumb_cache[index] = None if image.isNull() else QPixmap.fromImage(image)
        return thumb_cache[index]

    def open_index(index: int) -> None:
        nonlocal current
        current = max(0, min(len(paths) - 1, index))
        path = paths[current]
        stat = path.stat()
        record = ImageRecord(str(path), path.name, stat.st_size, stat.st_mtime_ns)
        preview.show_entries([PreviewEntry(record, str(path))])
        preview.set_browse_context(len(paths), current, thumbnail)

    preview.navigation_requested.connect(lambda delta: open_index(current + delta))
    preview.closed.connect(app.quit)
    open_index(current)
    if args.size is not None:
        preview.showNormal()
        preview.resize(*args.size)

    capture_status = 0
    if args.capture is not None:
        deadline = time.monotonic() + 20.0

        def save_capture() -> None:
            nonlocal capture_status
            if not preview.grab().save(str(args.capture), "PNG"):
                print(f"Could not save screenshot: {args.capture}", file=sys.stderr)
                capture_status = 1
            app.quit()

        def capture_when_ready() -> None:
            nonlocal capture_status
            loaded = bool(preview._current_images and not preview._current_images[0].isNull())
            if loaded:
                # Histogram and status updates are debounced after decode.
                QTimer.singleShot(200, save_capture)
            elif time.monotonic() >= deadline:
                print("Timed out waiting for the preview image", file=sys.stderr)
                capture_status = 1
                app.quit()
            else:
                QTimer.singleShot(50, capture_when_ready)

        QTimer.singleShot(50, capture_when_ready)
    return app.exec() or capture_status


if __name__ == "__main__":
    raise SystemExit(main())
