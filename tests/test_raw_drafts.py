"""E8: unsaved RAW drafts are kept on disk and come back after a crash or a restart."""
import json
import time

import pytest

from image_triage import photocraft_bridge as bridge
from image_triage.raw_drafts import DraftStore
from image_triage.qml_shell.raw_editor import RawEditor
from test_qml_shell import qapp, pump  # noqa: F401


def wait(qapp, predicate):
    end = time.monotonic() + 5
    while not predicate() and time.monotonic() < end:
        pump(qapp, .02)
    assert predicate()


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "photos" / "IMG_1.NEF"
    path.parent.mkdir()
    path.write_bytes(b"raw bytes")
    return str(path)


@pytest.fixture
def store(tmp_path):
    store = DraftStore(tmp_path / "drafts")
    yield store
    store.close()


def test_a_draft_round_trips_and_is_tied_to_the_saved_edit_and_the_file(store, photo):
    assert store.read(photo, {}) == (None, "")
    store.put(photo, {}, {"exposure": 1.5})
    assert store.flush()
    assert store.read(photo, {}) == ({"exposure": 1.5}, "")
    assert store.paths() == [photo]
    draft, reason = store.read(photo, {"contrast": 10})  # the saved edit moved on since
    assert draft is None and "saved edit changed" in reason
    with open(photo, "ab") as handle:
        handle.write(b"more")
    draft, reason = store.read(photo, {})
    assert draft is None and "photo changed" in reason
    assert store.file_for(photo).exists()  # refusing a draft does not destroy it


def test_only_the_newest_state_is_written_and_discard_removes_the_file(store, photo):
    for value in range(50):
        store.put(photo, {}, {"exposure": value / 10})
    assert store.flush()
    assert json.loads(store.file_for(photo).read_text())["draft"] == {"exposure": 4.9}
    assert [p.name for p in store.root().iterdir()] == [store.file_for(photo).name]  # no temporary files left
    store.discard(photo)
    assert store.flush() and not store.file_for(photo).exists()


def test_unreadable_or_foreign_files_are_reported_not_applied(store, photo):
    target = store.file_for(photo)
    target.parent.mkdir(parents=True)
    target.write_text("{not json")
    assert store.read(photo, {}) == (None, "Unsaved edits from an earlier session could not be read.")
    target.write_text(json.dumps({"version": 1, "path": "elsewhere.NEF", "signature": bridge.file_signature(photo),
                                  "original": {}, "draft": {"exposure": 1}}))
    assert store.read(photo, {})[0] is None
    assert store.paths() == ["elsewhere.NEF"]


def test_a_write_failure_never_raises_into_the_editor(tmp_path, photo):
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("x")
    store = DraftStore(blocked)
    store.put(photo, {}, {"exposure": 1})
    assert store.flush() and store.last_error
    store.close()


def editor(qapp, monkeypatch, store, results):
    """A RawEditor whose native work is replaced: ``load`` reads the real stored draft, the rest is recorded."""
    calls = []
    saved = results.setdefault("saved", {})

    def work(action, path, original, draft, directory, sample=None):
        calls.append(action)
        if action == "load":
            return dict(saved), "", *(sample.read(path, dict(saved)) if sample else (None, ""))
        if action == "save":
            saved.clear(); saved.update(draft)
            return "rendered.png"
        return ""

    monkeypatch.setattr(RawEditor, "_work", staticmethod(work))
    monkeypatch.setattr("image_triage.qml_shell.raw_preview.executable", lambda: None)
    raw = RawEditor(drafts=store)
    raw.calls = calls
    return raw


def test_a_draft_survives_a_restart_and_one_undo_returns_to_the_saved_edit(qapp, monkeypatch, tmp_path, photo):
    results = {}
    first = editor(qapp, monkeypatch, DraftStore(tmp_path / "drafts"), results)
    first.open(photo)
    wait(qapp, lambda: first.loaded)
    first.adjust("exposure", 1.25)
    first.adjust("contrast", 30)
    first.shutdown()  # the application closes (or crashes after the write): nothing was saved with Done

    second = editor(qapp, monkeypatch, DraftStore(tmp_path / "drafts"), results)
    second.open(photo)
    wait(qapp, lambda: second.loaded)
    assert second.values["exposure"] == 1.25 and second.values["contrast"] == 30
    assert second.dirty and "restored" in second.notice and second.unsavedCount == 1
    second.undo()
    assert not second.dirty and second.values["exposure"] == 0
    second.shutdown()


def test_cancel_and_done_both_forget_the_stored_draft(qapp, monkeypatch, tmp_path, photo):
    results = {}
    store = DraftStore(tmp_path / "drafts")
    raw = editor(qapp, monkeypatch, store, results)
    raw.open(photo)
    wait(qapp, lambda: raw.loaded)
    raw.adjust("exposure", 2)
    assert store.flush() and store.file_for(photo).exists()
    raw.cancel()
    assert store.flush() and not store.file_for(photo).exists()

    raw.open(photo)
    wait(qapp, lambda: raw.loaded)
    assert not raw.dirty and not raw.notice
    raw.adjust("exposure", 2)
    raw.done()
    wait(qapp, lambda: not raw.active)
    assert store.flush() and not store.file_for(photo).exists() and results["saved"] == {"exposure": 2}
    raw.shutdown()


def test_undoing_back_to_the_saved_edit_removes_the_stored_draft(qapp, monkeypatch, tmp_path, photo):
    store = DraftStore(tmp_path / "drafts")
    raw = editor(qapp, monkeypatch, store, {})
    raw.open(photo)
    wait(qapp, lambda: raw.loaded)
    raw.adjust("exposure", 2)
    assert store.flush() and store.file_for(photo).exists()
    raw.undo()
    assert store.flush() and not store.file_for(photo).exists()
    raw.shutdown()


def test_a_draft_for_a_photo_stepped_away_from_is_kept_while_another_is_cancelled(qapp, monkeypatch, tmp_path, photo):
    other = str(tmp_path / "photos" / "IMG_2.NEF")
    open(other, "wb").write(b"second")
    store = DraftStore(tmp_path / "drafts")
    raw = editor(qapp, monkeypatch, store, {})
    raw.open(photo)
    wait(qapp, lambda: raw.loaded)
    raw.adjust("exposure", 1)
    raw.open(other)
    wait(qapp, lambda: raw.loaded)
    raw.adjust("contrast", 5)
    raw.cancel()
    assert store.flush()
    assert store.file_for(photo).exists() and not store.file_for(other).exists()
    raw.shutdown()


def test_a_draft_whose_saved_edit_changed_is_not_applied_and_says_why(qapp, monkeypatch, tmp_path, photo):
    results = {}
    first = editor(qapp, monkeypatch, DraftStore(tmp_path / "drafts"), results)
    first.open(photo)
    wait(qapp, lambda: first.loaded)
    first.adjust("exposure", 1)
    first.shutdown()
    results["saved"].update({"contrast": 40})  # saved by another window or the hosted editor meanwhile
    second = editor(qapp, monkeypatch, DraftStore(tmp_path / "drafts"), results)
    second.open(photo)
    wait(qapp, lambda: second.loaded)
    assert not second.dirty and second.values["contrast"] == 40 and "not restored" in second.notice
    second.shutdown()


def test_the_demo_editor_never_writes_drafts(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("IMAGE_TRIAGE_APPDATA", str(tmp_path))
    raw = RawEditor(memory=True)
    raw.open("sample.NEF")
    raw.adjust("exposure", 1)
    raw.shutdown()
    assert not (tmp_path / "ImageTriage" / "raw_drafts").exists()
