"""Characterization of annotation persistence: DecisionStore, XMP sidecars, and their precedence (WI-0.5)."""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from image_triage.decision_store import DecisionStore
from image_triage.models import SessionAnnotation
from image_triage.scanner import scan_folder
from image_triage.window import AnnotationHydrationTask
from image_triage.xmp import existing_sidecar_paths, load_sidecar_annotation, sync_sidecar_annotation
from tests.harness import make_jpegs


@pytest.fixture
def store():
    return DecisionStore()


@pytest.fixture
def session():
    return f"test-{uuid.uuid4().hex[:8]}"


def _record(folder, name="a.jpg"):
    make_jpegs(folder, [name])
    (record,) = [r for r in scan_folder(str(folder)) if r.name == name]
    return record


# ---- DecisionStore ---------------------------------------------------------

def test_store_round_trips_all_annotation_fields(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    annotation = SessionAnnotation(winner=True, rating=4, tags=("sharp", "portrait"), review_round="r2")
    store.save_annotation(session, record, annotation)

    loaded = store.load_annotations_for_paths(session, {record.path: record}, [record.path])

    assert loaded[record.path] == annotation


def test_store_is_scoped_by_session(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    store.save_annotation(session, record, SessionAnnotation(winner=True))

    other = store.load_annotations_for_paths(f"{session}-other", {record.path: record}, [record.path])

    assert other == {}


def test_store_ignores_a_row_when_the_file_changed_on_disk(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    store.save_annotation(session, record, SessionAnnotation(winner=True))
    stat = os.stat(record.path)
    os.utime(record.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    (changed,) = [r for r in scan_folder(str(tmp_path)) if r.name == record.name]

    assert store.load_annotations_for_paths(session, {changed.path: changed}, [changed.path]) == {}


def test_saving_an_empty_annotation_removes_the_row(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    store.save_annotation(session, record, SessionAnnotation(winner=True))
    store.save_annotation(session, record, SessionAnnotation())

    assert store.load_annotations_for_paths(session, {record.path: record}, [record.path]) == {}


def test_delete_annotation_removes_it(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    store.save_annotation(session, record, SessionAnnotation(reject=True))
    store.delete_annotation(session, record.path)

    assert store.load_annotations_for_paths(session, {record.path: record}, [record.path]) == {}


def test_move_annotation_rekeys_the_row(store, session, tmp_path) -> None:
    record = _record(tmp_path / "one")
    annotation = SessionAnnotation(winner=True, rating=3)
    store.save_annotation(session, record, annotation)
    moved_dir = tmp_path / "two"
    moved_dir.mkdir()
    new_path = moved_dir / record.name
    os.replace(record.path, new_path)
    (moved,) = scan_folder(str(moved_dir))

    store.move_annotation(session, record.path, moved, annotation)

    assert store.load_annotations_for_paths(session, {record.path: record}, [record.path]) == {}
    assert store.load_annotations_for_paths(session, {moved.path: moved}, [moved.path])[moved.path] == annotation


# ---- XMP sidecars -----------------------------------------------------------

def test_sidecar_round_trips_winner_rating_and_tags(tmp_path) -> None:
    record = _record(tmp_path)
    annotation = SessionAnnotation(winner=True, rating=5, tags=("keeper",))
    sync_sidecar_annotation(record, annotation)

    assert existing_sidecar_paths(record.path)
    loaded = load_sidecar_annotation(record.path)
    assert loaded.winner and loaded.rating == 5 and "keeper" in loaded.tags


def test_sidecar_round_trips_reject(tmp_path) -> None:
    record = _record(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(reject=True))

    assert load_sidecar_annotation(record.path).reject


def test_syncing_an_empty_annotation_creates_no_sidecar(tmp_path) -> None:
    record = _record(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation())

    assert existing_sidecar_paths(record.path) == ()


def test_clearing_an_annotation_empties_the_existing_sidecar(tmp_path) -> None:
    record = _record(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(winner=True))
    sync_sidecar_annotation(record, None)

    assert load_sidecar_annotation(record.path).is_empty


def test_lightroom_style_label_is_read_as_a_winner(tmp_path) -> None:
    record = _record(tmp_path)
    xmp = Path(record.path).with_suffix(".xmp")
    xmp.write_text(
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description xmlns:xmp="http://ns.adobe.com/xap/1.0/" xmp:Label="Winner" xmp:Rating="3"/>'
        "</rdf:RDF></x:xmpmeta>",
        encoding="utf-8",
    )

    loaded = load_sidecar_annotation(record.path)

    assert loaded.winner and loaded.rating == 3


# ---- precedence -------------------------------------------------------------

def _hydrate(store, session, records):
    task = AnnotationHydrationTask(scope_key="s", token=1, session_id=session, records=tuple(records))
    return task._hydrate_records_batch(store, list(records))


def test_precedence_the_decision_store_overrides_the_sidecar(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(winner=True, rating=5))
    store.save_annotation(session, record, SessionAnnotation(reject=True, rating=1))

    hydrated = _hydrate(store, session, [record])

    assert hydrated[record.path].reject and not hydrated[record.path].winner
    assert hydrated[record.path].rating == 1


def test_precedence_sidecar_is_used_when_the_store_has_nothing(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(winner=True))

    hydrated = _hydrate(store, session, [record])

    assert hydrated[record.path].winner


def test_precedence_a_stale_store_row_falls_back_to_the_sidecar(store, session, tmp_path) -> None:
    record = _record(tmp_path)
    sync_sidecar_annotation(record, SessionAnnotation(winner=True))
    store.save_annotation(session, record, SessionAnnotation(reject=True))
    stat = os.stat(record.path)
    os.utime(record.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
    (changed,) = [r for r in scan_folder(str(tmp_path)) if r.name == record.name]

    hydrated = _hydrate(store, session, [changed])

    assert hydrated[changed.path].winner and not hydrated[changed.path].reject
