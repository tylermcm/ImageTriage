"""macOS and Linux: the photo types Image Triage declares, opening a photo Finder hands over, and making Image Triage the default app."""
from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QUrl
from PySide6.QtGui import QFileOpenEvent, QGuiApplication

from image_triage import desktop_associations as da
from image_triage import document_types as dt
from image_triage.formats import RAW_SUFFIXES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def qapp():
    return QCoreApplication.instance() or QGuiApplication([])


# ---- the declared types -------------------------------------------------------------------------------------------------------------
def test_the_mac_app_declares_photos_and_every_raw_as_a_viewer():
    types = dt.mac_document_types()
    content = {uti for t in types for uti in t.get("LSItemContentTypes", [])}
    assert {"public.jpeg", "public.png", "public.tiff", "public.heic", "public.camera-raw-image"} <= content
    assert all(t["CFBundleTypeRole"] == "Viewer" and t["LSHandlerRank"] == "Alternate" for t in types), "never claims a type on install"
    by_extension = next(t for t in types if "CFBundleTypeExtensions" in t)
    assert set(by_extension["CFBundleTypeExtensions"]) == {s.lstrip(".") for s in RAW_SUFFIXES}
    assert "CFBundleDocumentTypes" in (ROOT / "setup_macos.py").read_text(encoding="utf-8")


def test_the_linux_desktop_entry_lists_the_mime_types():
    line = dt.linux_mime_type_line()
    assert line in (ROOT / "packaging" / "install_linux.sh").read_text(encoding="utf-8"), "the installer's entry is the module's list"
    assert "image/jpeg;" in line and "image/x-nikon-nef;" in line and line.endswith(";")
    assert dt.linux_mime_for_suffix(".NEF") == "image/x-nikon-nef" and dt.linux_mime_for_suffix(".jpeg") == "image/jpeg"
    assert dt.linux_mime_for_suffix(".bay") == "image/x-dcraw", "a RAW without its own MIME type gets the generic one"


# ---- Linux ----------------------------------------------------------------------------------------------------------------------------
class FakeXdg:
    def __init__(self, defaults=None):
        self.defaults = dict(defaults or {})
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("query", "default"):
            return self.defaults.get(args[2], "")
        if args[0] == "default":
            for mime in args[2:]:
                self.defaults[mime] = args[1]
        return ""


@pytest.fixture
def linux(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path / "system"))
    monkeypatch.setattr(da, "_refresh_desktop_database", lambda folder: None)
    other = tmp_path / "system" / "applications" / "org.gnome.Loupe.desktop"
    other.parent.mkdir(parents=True)
    other.write_text("[Desktop Entry]\nName=Image Viewer\n", encoding="utf-8")
    xdg = FakeXdg({"image/jpeg": "org.gnome.Loupe.desktop"})
    return da.LinuxAssociations(run=xdg), xdg, tmp_path


def test_linux_without_a_desktop_entry_offers_to_add_one(linux, monkeypatch):
    backend, xdg, tmp_path = linux
    states = backend.states([".jpg", ".nef"])
    assert states[".jpg"] == da.TypeState("other", "Image Viewer") and states[".nef"].state == "none"
    monkeypatch.setenv("APPIMAGE", "/home/me/Apps/ImageTriage.AppImage")
    assert backend.register() == ""
    entry = backend.user_desktop_file().read_text(encoding="utf-8")
    assert 'Exec="/home/me/Apps/ImageTriage.AppImage" %F' in entry and dt.linux_mime_type_line() in entry
    assert backend.states([".nef"])[".nef"].state == "available" and backend.can_remove()
    assert backend.remove() == "" and backend.desktop_file() is None


def test_linux_makes_every_type_default_in_one_call(linux):
    backend, xdg, tmp_path = linux
    assert backend.make_default([".jpg", ".jpeg", ".nef", ".cr2"]) == ""
    sets = [c for c in xdg.calls if c[0] == "default"]
    assert sets == [("default", dt.LINUX_DESKTOP_FILE, "image/jpeg", "image/x-nikon-nef", "image/x-canon-cr2")], "one call, each type once"
    assert {s.state for s in backend.states([".jpg", ".nef", ".cr2"]).values()} == {"default"}
    assert backend.desktop_file() is not None, "a desktop entry was written first, so the desktop knows the app it is told about"


def test_an_installed_desktop_entry_is_not_removed_from_the_app(linux):
    backend, xdg, tmp_path = linux
    installed = tmp_path / "home" / "applications" / dt.LINUX_DESKTOP_FILE
    installed.parent.mkdir(parents=True)
    installed.write_text("[Desktop Entry]\nName=Image Triage\n" + dt.linux_mime_type_line() + "\n", encoding="utf-8")
    assert not backend.can_remove() and backend.remove() and installed.is_file()


# ---- macOS ----------------------------------------------------------------------------------------------------------------------------
class FakeLaunchServices:
    def __init__(self, bundle="com.tylermcm.imagetriage"):
        self.bundle = bundle
        self.handlers = {"public.jpeg": "com.apple.Preview", "com.nikon.raw-image": "com.adobe.Photoshop"}
        self.set = []

    def bundle_id(self):
        return self.bundle

    def uti_for(self, ext):
        return {"jpg": "public.jpeg", "jpeg": "public.jpeg", "nef": "com.nikon.raw-image", "png": "public.png"}.get(ext, "")

    def handler(self, uti):
        return self.handlers.get(uti, "")

    def set_handler(self, uti, bundle):
        self.set.append((uti, bundle))
        self.handlers[uti] = bundle
        return 0


def test_mac_reads_and_sets_the_default_per_content_type():
    services = FakeLaunchServices()
    mac = da.MacAssociations(services)
    states = mac.states([".jpg", ".nef", ".png"])
    assert states[".jpg"] == da.TypeState("other", "Preview") and states[".png"].state == "available"
    assert mac.make_default([".jpg", ".jpeg", ".nef"]) == ""
    assert services.set == [("public.jpeg", services.bundle), ("com.nikon.raw-image", services.bundle)], "a content type once"
    assert {s.state for s in mac.states([".jpg", ".nef"]).values()} == {"default"}


def test_mac_without_a_bundle_cannot_be_made_default():
    mac = da.MacAssociations(FakeLaunchServices(bundle=""))
    assert "installed app" in mac.make_default([".jpg"])


# ---- the File associations window -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("platform", ["mac", "linux"])
def test_the_window_offers_default_for_all_on_mac_and_linux(qapp, platform, monkeypatch):
    from image_triage import file_associations as fa
    from image_triage.qml_shell.reference_model import ReferenceModel

    class Backend:
        can_register = platform == "linux"

        def __init__(self):
            self.platform = platform
            self.made = []

        def states(self, suffixes):
            return {s: da.TypeState("default" if s in self.made else "available") for s in suffixes}

        def make_default(self, suffixes):
            self.made.extend(suffixes)
            return ""

        def can_remove(self):
            return False

    model = ReferenceModel(memory=False)
    backend = Backend()
    model._fa_desktop, model._fa_platform, model._fa_os = backend, platform, True
    view = model._fa_view()
    assert view["supported"] and view["platform"] == platform and view["canAllDefault"] and not view["canRegister"]
    assert view["explorer"] == ("Finder" if platform == "mac" else "your file manager")
    model.faMakeDefault(".nef")
    assert backend.made == [".nef"]
    model.faMakeAllDefault()
    assert set(backend.made) == set(fa.supported_file_association_suffixes())
    view = model._fa_view()
    assert view["pct"] == 100 and not view["canAllDefault"]


# ---- Finder's open events -------------------------------------------------------------------------------------------------------------
def test_a_file_finder_opens_is_heard_and_a_plain_launch_waits_briefly(qapp, tmp_path):
    from image_triage.file_open_events import install, wait_for_opened_file

    listener = install(qapp)
    try:
        start = time.monotonic()
        assert wait_for_opened_file(qapp, listener, timeout_s=0.1) == ""
        assert time.monotonic() - start < 0.5
        photo = tmp_path / "a.jpg"
        QCoreApplication.sendEvent(qapp, QFileOpenEvent(QUrl.fromLocalFile(str(photo))))
        assert wait_for_opened_file(qapp, listener, timeout_s=0.1) == str(photo)
    finally:
        qapp.removeEventFilter(listener)


def test_a_mac_launch_with_no_arguments_becomes_the_file_finder_opened(qapp, tmp_path, monkeypatch):
    from image_triage import file_open_events, main as entry

    photo = tmp_path / "b.jpg"
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(file_open_events, "wait_for_opened_file", lambda app, listener, timeout_s=0: str(photo))
    assert entry.mac_finder_launch(["/Applications/Image Triage.app/Contents/MacOS/ImageTriage", "-psn_0_12345"]) == [
        "/Applications/Image Triage.app/Contents/MacOS/ImageTriage", str(photo)]
    monkeypatch.setattr(file_open_events, "wait_for_opened_file", lambda app, listener, timeout_s=0: "")
    assert entry.mac_finder_launch(["ImageTriage"]) == ["ImageTriage"], "started on its own: the library"
    assert entry.mac_finder_launch(["ImageTriage", "--folder", "/x"]) == ["ImageTriage", "--folder", "/x"], "arguments: as they are"
    monkeypatch.setattr(sys, "platform", "linux")
    assert entry.mac_finder_launch(["ImageTriage"]) == ["ImageTriage"]


def test_the_quick_viewer_takes_a_photo_opened_while_it_is_up(qapp, tmp_path):
    from PIL import Image

    from image_triage.quick_view.viewer import QuickViewer

    for folder in ("one", "two"):
        (tmp_path / folder).mkdir()
        for name in ("a.jpg", "b.jpg"):
            Image.new("RGB", (40, 30), "teal").save(tmp_path / folder / name)
    viewer = QuickViewer(str(tmp_path / "one" / "a.jpg"), None, longest=100)
    try:
        viewer.start()
        viewer.openPhoto(str(tmp_path / "two" / "b.jpg"))
        assert viewer.path == str(tmp_path / "two" / "b.jpg")
        viewer._listed = False                         # as before its own folder is read (a small folder can be read at once)
        viewer._take_list([str(tmp_path / "one" / "a.jpg"), str(tmp_path / "one" / "a.jpg"), str(tmp_path / "one" / "b.jpg")])
        assert not viewer.listed, "the first photo's folder, read late, is not taken for the new one"
        from image_triage.quick_view import decode

        viewer._take_list([str(tmp_path / "two" / "b.jpg"), *decode.folder_photos(str(tmp_path / "two" / "b.jpg"))])
        assert viewer.listed and viewer.count == 2 and viewer.name == "b.jpg"
        viewer.openPhoto(str(tmp_path / "notes.txt"))
        assert viewer.name == "b.jpg", "not a photo: left as it is"
    finally:
        viewer.shutdown()
