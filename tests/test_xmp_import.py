"""Reading stars, keywords and rejected marks other programs wrote, and writing a rejected mark they understand."""
import os

import pytest

from image_triage import xmp
from image_triage.decision_store import DecisionStore
from image_triage.models import SessionAnnotation
from image_triage.scanner import scan_folder

LIGHTROOM = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmlns:dc="http://purl.org/dc/elements/1.1/"
    xmlns:crs="http://ns.adobe.com/camera-raw-settings/1.0/" xmp:Rating="{rating}" crs:Exposure2012="+0.35">
   <dc:subject><rdf:Bag><rdf:li>wedding</rdf:li><rdf:li>first dance</rdf:li></rdf:Bag></dc:subject>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""


def shoot(tmp_path, ratings):
    folder = tmp_path / "shoot"
    folder.mkdir()
    for name, rating in ratings.items():
        (folder / name).write_bytes(b"raw")
        if rating is not None:
            (folder / (os.path.splitext(name)[0] + ".xmp")).write_text(LIGHTROOM.format(rating=rating), encoding="utf-8")
    return scan_folder(str(folder))


def test_stars_keywords_and_a_rejected_mark_are_read(tmp_path):
    records = shoot(tmp_path, {"a.NEF": 4, "b.NEF": -1, "c.NEF": None, "d.NEF": 0})
    found = xmp.import_sidecar_annotations(records)
    by_name = {os.path.basename(path): annotation for path, annotation in found.items()}
    assert set(by_name) == {"a.NEF", "b.NEF", "d.NEF"}
    assert by_name["a.NEF"].rating == 4 and by_name["a.NEF"].tags == ("wedding", "first dance") and not by_name["a.NEF"].reject
    assert by_name["b.NEF"].reject and by_name["b.NEF"].rating == 0
    assert by_name["d.NEF"].rating == 0 and by_name["d.NEF"].tags == ("wedding", "first dance")   # keywords alone count


def test_a_folder_without_sidecars_is_listed_once_and_nothing_is_opened(tmp_path, monkeypatch):
    records = shoot(tmp_path, {f"{i}.NEF": None for i in range(40)})
    listed, parsed = [], []
    real = os.scandir
    monkeypatch.setattr(xmp.os, "scandir", lambda path: (listed.append(path), real(path))[1])
    monkeypatch.setattr(xmp, "_read_single_sidecar", lambda path: parsed.append(path) or SessionAnnotation())
    assert xmp.import_sidecar_annotations(records) == {}
    assert len(listed) == 1 and parsed == []


def test_a_rejected_photo_is_written_as_the_rating_other_programs_show_as_rejected(tmp_path):
    record = shoot(tmp_path, {"a.NEF": None})[0]
    sidecar = os.path.splitext(record.path)[0] + ".xmp"
    xmp.sync_sidecar_annotation(record, SessionAnnotation(reject=True))
    assert 'xmp:Rating="-1"' in open(sidecar, encoding="utf-8").read() or ">-1<" in open(sidecar, encoding="utf-8").read()
    assert xmp.load_sidecar_annotation(record.path) == SessionAnnotation(reject=True)
    # Taking the rejection back removes it; a rejected photo that has stars keeps its stars.
    xmp.sync_sidecar_annotation(record, SessionAnnotation(rating=3), SessionAnnotation(reject=True))
    assert xmp.load_sidecar_annotation(record.path) == SessionAnnotation(rating=3)
    xmp.sync_sidecar_annotation(record, SessionAnnotation(reject=True, rating=3), SessionAnnotation(rating=3))
    text = open(sidecar, encoding="utf-8").read()
    assert "-1" not in text and xmp.load_sidecar_annotation(record.path) == SessionAnnotation(reject=True, rating=3)


def test_rejecting_keeps_what_another_program_wrote(tmp_path):
    record = shoot(tmp_path, {"a.NEF": 4})[0]
    sidecar = os.path.splitext(record.path)[0] + ".xmp"
    before = xmp.load_sidecar_annotation(record.path)
    xmp.sync_sidecar_annotation(record, SessionAnnotation(reject=True, rating=4, tags=before.tags), before)
    text = open(sidecar, encoding="utf-8").read()
    assert "Exposure2012" in text and "first dance" in text and "xpacket" in text
    assert xmp.load_sidecar_annotation(record.path).rating == 4


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtCore import QCoreApplication
    import image_triage.annotation_queue  # noqa: F401  (binds DecisionStore before it is replaced below)
    from image_triage.qml_shell import decision_bridge
    from image_triage.qml_shell.photos import PhotoModel

    app = QCoreApplication.instance() or QGuiApplication([])
    store = DecisionStore.__new__(DecisionStore)
    store._db_path = tmp_path / "decisions.sqlite3"
    store._initialize()
    monkeypatch.setattr("image_triage.decision_store.DecisionStore", lambda: store)
    monkeypatch.setattr(decision_bridge, "session_id", lambda: "s")
    said = []
    toasts = type("Toasts", (), {"push": lambda self, *args, **kwargs: said.append(args)})()
    model = PhotoModel(parent=app)
    made = decision_bridge.DecisionBridge(model, toasts=toasts, parent=app)
    yield made, store, said, app
    made.setParent(None)
    model.setParent(None)


def test_opening_a_folder_shows_what_another_program_rated_and_says_so(tmp_path, bridge):
    made, store, said, app = bridge
    records = shoot(tmp_path, {"a.NEF": 5, "b.NEF": -1, "c.NEF": None})
    loaded = made.load(records)                       # what the scan thread calls
    by_name = {os.path.basename(path): value for path, value in loaded.items()}
    assert by_name["a.NEF"]["rating"] == 5 and by_name["a.NEF"]["decision"] == ""
    assert by_name["b.NEF"]["decision"] == "rej" and "c.NEF" not in by_name
    app.processEvents()
    assert [args[1] for args in said] == ["Read ratings from XMP sidecars"] and said[0][2].startswith("2 photos")
    assert len(store.load_annotations("s", records)) == 2     # saved: read once


def test_this_apps_own_record_wins_over_a_sidecar_and_nothing_is_read_twice(tmp_path, bridge, monkeypatch):
    made, store, said, app = bridge
    records = shoot(tmp_path, {"a.NEF": 5, "b.NEF": 2})
    a = next(r for r in records if r.name == "a.NEF")
    store.save_annotation("s", a, SessionAnnotation(rating=1))
    loaded = made.load(records)
    assert loaded[a.path]["rating"] == 1
    monkeypatch.setattr(xmp, "import_sidecar_annotations", lambda records: pytest.fail("everything is already saved"))
    again = made.load(records)
    assert {value["rating"] for value in again.values()} == {1, 2}
