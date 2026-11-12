"""A requested file must remain selected after every folder-load notification."""
from PIL import Image
import pytest
from PySide6.QtCore import QObject

from image_triage.drive_list_model import DriveListModel
from image_triage.qml_shell.library_nav import LibraryNavModel
from test_qml_shell import _load_main, pump, qapp  # noqa: F401


@pytest.mark.parametrize("already_open", [False, True], ids=["file-launch", "second-launch"])
def test_file_launch_selects_the_requested_photo_in_the_visible_grid(qapp, tmp_path, already_open):
    shoot = tmp_path / "shoot"
    (shoot / "child-folder").mkdir(parents=True)
    for index in range(4):
        Image.new("RGB", (32, 24), "teal").save(shoot / f"frame-{index}.jpg")
    requested = str(shoot / "frame-2.jpg")
    engine, window, warnings = _load_main(qapp, tmp_path)
    try:
        photos = engine.rootContext().contextProperty("photos")
        nav = LibraryNavModel(photos, persist=False,
                              drives=DriveListModel(roots_provider=lambda: [], probe=lambda p: (False, "", None)),
                              drive_root=lambda p: str(tmp_path), parent=engine)
        engine.rootContext().setContextProperty("libraryNav", nav)
        if already_open:
            nav.openFolder(str(shoot))
            for _ in range(100):
                pump(qapp, 0.02)
                if not photos.loading:
                    break
            assert photos.total == 4
        nav.open_file(requested)
        for _ in range(100):
            pump(qapp, 0.02)
            if not photos.loading:
                break
        assert photos.total == 4
        pump(qapp, 0.2)  # include deferred grid layout and model/scope notifications
        grid = window.findChild(QObject, "photoGrid")
        folder_rows = engine.rootContext().contextProperty("gridModel").folders
        assert folder_rows == 1
        assert photos.get(grid.property("currentIndex") - folder_rows)["path"] == requested
        assert not [w for w in warnings if "TypeError" in w or "ReferenceError" in w], warnings
    finally:
        window.close()
        engine.deleteLater()
        pump(qapp, 0.1)
