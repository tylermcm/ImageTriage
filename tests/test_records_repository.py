"""Unit tests for image_triage.records_repository.RecordsRepository.

This is the first step of WI-4.4's move/copy/delete/undo slice: the
repository now owns MainWindow's `_all_records`/`_all_records_by_path`
state privately (exposed as read-only properties), and the handful of
places that used to reassign those two attributes directly (`_remove_record`,
`_remove_records_by_paths`, `_replace_record`, `_replace_records_after_moves`,
plus two "clear everything" resets and one full-folder reload) now call one
of this class's named operations instead. These tests exercise the
repository in isolation; the existing window-level test suite (untouched)
continues to prove the MainWindow call sites behave the same as before.
"""
from __future__ import annotations

from image_triage.models import ImageRecord
from image_triage.records_repository import RecordsRepository
from tests.harness import make_jpegs, open_folder


def _record(path: str, **overrides) -> ImageRecord:
    defaults = dict(path=path, name=path.rsplit("/", 1)[-1], size=1, modified_ns=1)
    defaults.update(overrides)
    return ImageRecord(**defaults)


def test_starts_empty() -> None:
    repo = RecordsRepository()
    assert repo.all_records == []
    assert repo.all_records_by_path == {}
    assert repo.by_path("C:/shots/a.jpg") is None


def test_reload_replaces_everything_and_rebuilds_the_index() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg"), _record("C:/shots/b.jpg")])

    assert [r.path for r in repo.all_records] == ["C:/shots/a.jpg", "C:/shots/b.jpg"]
    assert repo.by_path("C:/shots/a.jpg") is not None
    assert repo.by_path("C:/shots/b.jpg") is not None

    repo.reload([_record("C:/other/c.jpg")])
    assert [r.path for r in repo.all_records] == ["C:/other/c.jpg"]
    assert repo.by_path("C:/shots/a.jpg") is None


def test_clear_empties_both_structures() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg")])

    repo.clear()

    assert repo.all_records == []
    assert repo.all_records_by_path == {}


def test_remove_paths_drops_matching_records_and_keeps_order() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg"), _record("C:/shots/b.jpg"), _record("C:/shots/c.jpg")])

    repo.remove_paths(("C:/shots/b.jpg",))

    assert [r.path for r in repo.all_records] == ["C:/shots/a.jpg", "C:/shots/c.jpg"]
    assert repo.by_path("C:/shots/b.jpg") is None


def test_remove_paths_handles_a_batch_in_one_call() -> None:
    repo = RecordsRepository()
    repo.reload([_record(f"C:/shots/{name}.jpg") for name in ("a", "b", "c", "d")])

    repo.remove_paths(("C:/shots/b.jpg", "C:/shots/d.jpg"))

    assert [r.path for r in repo.all_records] == ["C:/shots/a.jpg", "C:/shots/c.jpg"]


def test_remove_paths_with_an_unknown_path_is_a_safe_no_op_for_that_path() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg")])

    repo.remove_paths(("C:/shots/does-not-exist.jpg",))

    assert [r.path for r in repo.all_records] == ["C:/shots/a.jpg"]


def test_remove_paths_with_nothing_to_remove_is_a_no_op() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg")])

    repo.remove_paths(())

    assert [r.path for r in repo.all_records] == ["C:/shots/a.jpg"]


def test_replace_by_old_path_swaps_a_single_record_in_place() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg"), _record("C:/shots/b.jpg")])

    renamed = _record("C:/shots/trip_a.jpg")
    repo.replace_by_old_path({"C:/shots/a.jpg": renamed})

    assert [r.path for r in repo.all_records] == ["C:/shots/trip_a.jpg", "C:/shots/b.jpg"]
    assert repo.by_path("C:/shots/a.jpg") is None
    assert repo.by_path("C:/shots/trip_a.jpg") is renamed


def test_replace_by_old_path_handles_a_batch_and_preserves_order() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg"), _record("C:/shots/b.jpg"), _record("C:/shots/c.jpg")])

    moved_a = _record("D:/dest/a.jpg")
    moved_c = _record("D:/dest/c.jpg")
    repo.replace_by_old_path({"C:/shots/a.jpg": moved_a, "C:/shots/c.jpg": moved_c})

    assert [r.path for r in repo.all_records] == ["D:/dest/a.jpg", "C:/shots/b.jpg", "D:/dest/c.jpg"]
    assert repo.by_path("D:/dest/a.jpg") is moved_a
    assert repo.by_path("D:/dest/c.jpg") is moved_c
    assert repo.by_path("C:/shots/a.jpg") is None
    assert repo.by_path("C:/shots/c.jpg") is None


def test_replace_by_old_path_with_an_empty_mapping_is_a_no_op() -> None:
    repo = RecordsRepository()
    original = [_record("C:/shots/a.jpg")]
    repo.reload(original)

    repo.replace_by_old_path({})

    assert repo.all_records is original


def test_all_records_and_by_path_reflect_the_same_underlying_state() -> None:
    repo = RecordsRepository()
    repo.reload([_record("C:/shots/a.jpg")])

    assert repo.all_records[0] is repo.by_path("C:/shots/a.jpg")
    assert repo.all_records_by_path["C:/shots/a.jpg"] is repo.all_records[0]


def test_main_window_all_records_properties_delegate_to_the_repository(main_window, tmp_path) -> None:
    """MainWindow._all_records/_all_records_by_path are now read-only
    properties over window._records_repo, not plain instance attributes -
    this pins that the real window's read path still works and that
    assigning the old way is no longer possible (the property has no
    setter, by design: every write site was migrated to a named repository
    operation instead)."""
    make_jpegs(tmp_path, ["a.jpg", "b.jpg"])
    open_folder(main_window, tmp_path, 2)

    assert main_window._all_records is main_window._records_repo.all_records
    assert main_window._all_records_by_path is main_window._records_repo.all_records_by_path
    assert {record.path for record in main_window._all_records} == set(main_window._all_records_by_path)

    try:
        main_window._all_records = []
    except AttributeError:
        pass
    else:
        raise AssertionError("_all_records should be read-only now that RecordsRepository owns it")
