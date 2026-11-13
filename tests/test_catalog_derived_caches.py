"""The catalog's derived caches can be written by the shell, which never saves a folder listing (audit D19)."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from image_triage.ai_results import AIConfidenceBucket, AIImageResult, build_ai_bundle_from_results
from image_triage.catalog import CatalogRepository
from image_triage.catalog.db import connect_catalog_db
from image_triage.catalog.migrations import CatalogTooNewError, apply_catalog_migrations
from image_triage.catalog.schema import CATALOG_MIGRATIONS, LATEST_CATALOG_SCHEMA_VERSION
from image_triage.metadata import CaptureMetadata
from image_triage.models import ImageRecord
from image_triage.path_keys import folds_case, legacy_path_key, path_key
from image_triage.review_intelligence import ReviewGroup, ReviewInsight, ReviewIntelligenceBundle, _RecordFingerprint
from image_triage.review_workflows import BurstRecommendation, TasteProfile


def _records(folder: str) -> list[ImageRecord]:
    return [
        ImageRecord(path=f"{folder}/frame_01.jpg", name="frame_01.jpg", size=800, modified_ns=21),
        ImageRecord(path=f"{folder}/frame_02.jpg", name="frame_02.jpg", size=801, modified_ns=22),
    ]


def _grouping(records) -> ReviewIntelligenceBundle:
    group = ReviewGroup(id="review-1", kind="similar", label="Similar", member_paths=tuple(r.path for r in records), reasons=("close",))
    insights = {
        record.path: ReviewInsight(
            path=record.path, group_id=group.id, group_kind=group.kind, group_label=group.label, group_size=len(records),
            rank_in_group=index + 1, reasons=group.reasons, detail_score=80.0 - index, exposure_score=70.0,
        )
        for index, record in enumerate(records)
    }
    return ReviewIntelligenceBundle(groups=(group,), insights_by_path=insights)


def _fingerprint(record: ImageRecord) -> _RecordFingerprint:
    return _RecordFingerprint(
        record=record, source_path=record.path,
        metadata=CaptureMetadata(path=record.path, camera="Test Cam", width=6000, height=4000),
        dhash=0xF0F0F0F0F0F0F0F0, avg_luma=97.25, width=6000, height=4000, sha1_digest="abc123", detail_score=88.4, exposure_score=72.1,
    )


def _ai_bundle(folder: str, records):
    results = [
        AIImageResult(image_id=f"id-{index}", file_path=record.path, file_name=record.name, group_id="g1", group_size=len(records),
                      rank_in_group=index + 1, score=0.9 - index / 10, confidence_bucket=AIConfidenceBucket.NEEDS_REVIEW)
        for index, record in enumerate(records)
    ]
    return build_ai_bundle_from_results(results=results, source_path=f"{folder}/.image_triage_ai/report", export_csv_path=f"{folder}/x.csv")


def _write_every_cache(repository: CatalogRepository, folder: str, records) -> dict[str, bool]:
    recommendation = BurstRecommendation(
        path=records[0].path, group_id="g", group_label="Burst", group_size=2, recommended_path=records[0].path,
        rank_in_group=1, score=0.9, recommended_score=0.9, is_recommended=True, reasons=("sharpest",),
    )
    return {
        "grouping": repository.save_review_grouping(folder, cache_key="group-1", provider_id="default", bundle=_grouping(records)),
        "features": repository.save_review_features(folder, cache_keys={records[0].path: "feature-1"}, fingerprints=[_fingerprint(records[0])]),
        "scoring": repository.save_review_scoring(
            folder, session_id="s", cache_key="score-1", provider_id="default", records=records,
            taste_profile=TasteProfile(event_count=3, detail_bias=0.1, ai_alignment_bias=0.2, summary_lines=("likes detail",)),
            recommendations={records[0].path: recommendation},
        ),
        "ai_bundle": repository.save_ai_bundle(folder, cache_key="ai-1", bundle=_ai_bundle(folder, records)),
        "ai_workflow": repository.save_ai_workflow_cache(
            folder, embedding_cache_key="e", cluster_cache_key="c", report_cache_key="r", artifacts_dir=f"{folder}/a", report_dir=f"{folder}/r"),
    }


def test_the_shell_can_write_every_derived_cache_for_a_folder_whose_listing_was_never_saved(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.sqlite3")
    folder = str(tmp_path / "shots")
    records = _records(folder)

    assert _write_every_cache(repository, folder, records) == {
        "grouping": True, "features": True, "scoring": True, "ai_bundle": True, "ai_workflow": True}

    assert repository.load_review_grouping(folder, cache_key="group-1").bundle.groups[0].id == "review-1"
    assert repository.load_review_features(folder, records=records, cache_keys={records[0].path: "feature-1"})[records[0].path] == _fingerprint(records[0])
    assert repository.load_review_scoring(folder, session_id="s", cache_key="score-1").recommendations[records[0].path].is_recommended
    assert repository.load_ai_bundle(folder, cache_key="ai-1") is not None
    assert repository.load_ai_workflow_cache(folder).embedding_cache_key == "e"
    with closing(connect_catalog_db(repository.db_path)) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        row = connection.execute("SELECT folder_key, folder_path, source FROM catalog_folders").fetchone()
        assert (row["folder_key"], row["folder_path"], row["source"]) == (path_key(folder), folder, "derived")


def test_a_folder_row_made_for_a_cache_is_not_a_saved_listing(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.sqlite3")
    folder = str(tmp_path / "shots")
    records = _records(folder)
    assert repository.save_review_grouping(folder, cache_key="group-1", provider_id="default", bundle=_grouping(records))
    assert repository.load_folder_records(folder) is None            # nothing was listed: not "an empty folder"
    assert repository.list_folder_paths() == ()
    assert repository.stats().folder_count == 0 and repository.stats().grouping_cache_count == 1
    # a real listing takes the row over and the caches stay
    assert repository.save_folder_records(folder, records)
    assert [record.name for record in repository.load_folder_records(folder)] == ["frame_01.jpg", "frame_02.jpg"]
    assert repository.list_folder_paths() == (folder,)
    assert repository.load_review_grouping(folder, cache_key="group-1") is not None


def test_saving_the_listing_again_keeps_the_feature_cache(tmp_path):
    repository = CatalogRepository(tmp_path / "catalog.sqlite3")
    folder = str(tmp_path / "shots")
    records = _records(folder)
    repository.save_folder_records(folder, records)
    assert repository.save_review_features(folder, cache_keys={records[0].path: "feature-1"}, fingerprints=[_fingerprint(records[0])])
    repository.save_folder_records(folder, records)                  # every rescan used to wipe the features through the cascade
    assert records[0].path in repository.load_review_features(folder, records=records, cache_keys={records[0].path: "feature-1"})


def _database_at_version_10(db_path: Path) -> None:
    """A database as the previous build left it: migrations 1-10 applied, ``user_version`` never set."""
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        for version, script in CATALOG_MIGRATIONS:
            if version > 10:
                break
            connection.executescript(script)
            connection.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        connection.commit()


def test_an_old_database_is_repaired_in_place_without_losing_rows(tmp_path):
    db_path = tmp_path / "catalog.sqlite3"
    _database_at_version_10(db_path)
    folder = str(tmp_path / "shots")
    orphan_folder = str(tmp_path / "never-listed")
    key, orphan_key = path_key(folder), path_key(orphan_folder)
    with closing(sqlite3.connect(db_path)) as connection:
        # a listed folder with a feature row, as the previous build wrote them
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("INSERT INTO catalog_folders(folder_key, folder_path, record_count) VALUES (?, ?, 1)", (key, folder))
        connection.execute("INSERT INTO catalog_records VALUES (?, ?, 'a.jpg', 1, 1, 0)", (key, f"{folder}/a.jpg"))
        connection.execute(
            "INSERT INTO catalog_review_feature_cache(folder_key, record_path, cache_key, sha1_digest) VALUES (?, ?, 'k', 'kept')",
            (key, f"{folder}/a.jpg"))
        connection.commit()
        # rows written without their parents (foreign keys were not enforced on that connection)
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("INSERT INTO catalog_review_grouping_cache(folder_key, cache_key) VALUES (?, 'g')", (orphan_key,))
        connection.execute("INSERT INTO catalog_review_groups(folder_key, group_id) VALUES (?, 'r1')", (orphan_key,))
        connection.execute(
            "INSERT INTO catalog_review_group_members(folder_key, group_id, record_path) VALUES (?, 'r1', ?)",
            (orphan_key, f"{orphan_folder}/b.jpg"))
        connection.execute("INSERT INTO catalog_ai_results(folder_key, file_path) VALUES (?, ?)", (orphan_key, f"{orphan_folder}/b.jpg"))
        connection.execute(
            "INSERT INTO catalog_review_scoring_recommendations(folder_key, session_id, record_path) VALUES (?, 's', ?)",
            (orphan_key, f"{orphan_folder}/b.jpg"))
        connection.execute(
            "INSERT INTO catalog_review_feature_cache(folder_key, record_path, cache_key) VALUES (?, ?, 'k2')",
            (orphan_key, f"{orphan_folder}/b.jpg"))
        connection.commit()
        tables = ("catalog_folders", "catalog_records", "catalog_review_feature_cache", "catalog_review_grouping_cache",
                  "catalog_review_groups", "catalog_review_group_members", "catalog_ai_results", "catalog_review_scoring_recommendations")
        before = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() != []

    repository = CatalogRepository(db_path)
    records = _records(orphan_folder)
    assert repository.save_review_grouping(orphan_folder, cache_key="group-1", provider_id="default", bundle=_grouping(records))

    with closing(connect_catalog_db(db_path)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_CATALOG_SCHEMA_VERSION
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        after = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
        assert after["catalog_folders"] == before["catalog_folders"] + 1          # the missing folder row was made
        assert after["catalog_records"] == before["catalog_records"]
        assert after["catalog_review_feature_cache"] == before["catalog_review_feature_cache"]
        assert after["catalog_ai_results"] == before["catalog_ai_results"]
        assert after["catalog_review_scoring_recommendations"] == before["catalog_review_scoring_recommendations"]
        assert connection.execute("SELECT COUNT(*) FROM catalog_orphans").fetchone()[0] == 0
        made = connection.execute("SELECT folder_path, source FROM catalog_folders WHERE folder_key = ?", (orphan_key,)).fetchone()
        assert (made["folder_path"], made["source"]) == (orphan_folder, "derived")  # the path is recovered from the rows
        kept = connection.execute("SELECT sha1_digest FROM catalog_review_feature_cache WHERE folder_key = ?", (key,)).fetchone()
        assert kept["sha1_digest"] == "kept"
        # the feature cache now hangs off the folder, so it survives the listing being replaced
        targets = {row["table"] for row in connection.execute("PRAGMA foreign_key_list(catalog_review_feature_cache)")}
        assert targets == {"catalog_folders"}
    assert [record.name for record in repository.load_folder_records(folder)] == ["a.jpg"]


@pytest.mark.skipif(folds_case(), reason="only a case-sensitive platform ever stored a key that differs from the current one")
def test_folder_keys_folded_by_an_earlier_build_are_rewritten_with_their_rows(tmp_path):
    db_path = tmp_path / "catalog.sqlite3"
    _database_at_version_10(db_path)
    folder, twin = str(tmp_path / "Shoot"), str(tmp_path / "SHOOT")
    old_key = legacy_path_key(folder)
    assert old_key != path_key(folder) and old_key == legacy_path_key(twin)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("INSERT INTO catalog_folders(folder_key, folder_path, record_count) VALUES (?, ?, 1)", (old_key, folder))
        connection.execute("INSERT INTO catalog_records VALUES (?, ?, 'a.jpg', 1, 1, 0)", (old_key, f"{folder}/a.jpg"))
        connection.execute("INSERT INTO catalog_ai_workflow_cache(folder_key, embedding_cache_key) VALUES (?, 'e')", (old_key,))
        connection.commit()
    repository = CatalogRepository(db_path)
    assert [record.name for record in repository.load_folder_records(folder)] == ["a.jpg"]
    assert repository.load_ai_workflow_cache(folder).embedding_cache_key == "e"
    assert repository.load_folder_records(twin) is None                 # the folder whose name differs only by case gets nothing
    assert repository.load_ai_workflow_cache(twin) is None
    with closing(connect_catalog_db(db_path)) as connection:
        assert [row[0] for row in connection.execute("SELECT folder_key FROM catalog_folders")] == [path_key(folder)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_a_row_that_cannot_be_given_a_parent_is_set_aside_not_deleted(tmp_path):
    db_path = tmp_path / "catalog.sqlite3"
    _database_at_version_10(db_path)
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(
            "INSERT INTO catalog_record_members(folder_key, record_path, member_role, member_path) VALUES ('k', '/x/a.cr2', 'companion', '/x/a.jpg')")
        connection.commit()
    assert CatalogRepository(db_path).stats().available
    with closing(connect_catalog_db(db_path)) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT COUNT(*) FROM catalog_record_members").fetchone()[0] == 0
        saved = connection.execute("SELECT table_name, row_json FROM catalog_orphans").fetchall()
        assert [row["table_name"] for row in saved] == ["catalog_record_members"]
        assert "/x/a.jpg" in saved[0]["row_json"]


def test_a_database_from_a_newer_build_is_refused_and_left_alone(tmp_path):
    db_path = tmp_path / "catalog.sqlite3"
    repository = CatalogRepository(db_path)
    folder = str(tmp_path / "shots")
    assert repository.save_folder_records(folder, _records(folder))
    with closing(sqlite3.connect(db_path)) as connection:
        connection.execute(f"PRAGMA user_version = {LATEST_CATALOG_SCHEMA_VERSION + 1}")
        connection.commit()
    with closing(connect_catalog_db(db_path)) as connection:
        with pytest.raises(CatalogTooNewError):
            apply_catalog_migrations(connection)
    # the repository treats it as "no cache": nothing is read from it and nothing is written into it
    assert repository.load_folder_records(folder) is None
    assert repository.save_folder_records(folder, []) is False
    assert repository.save_review_grouping(folder, cache_key="g", provider_id="p", bundle=_grouping(_records(folder))) is False
    assert "newer version" in repository.stats().error_message
    with closing(sqlite3.connect(db_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM catalog_records").fetchone()[0] == 2
        assert connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_CATALOG_SCHEMA_VERSION + 1


def test_an_up_to_date_database_is_opened_without_writing_to_it(tmp_path):
    db_path = tmp_path / "catalog.sqlite3"
    repository = CatalogRepository(db_path)
    assert repository.save_folder_records(str(tmp_path / "shots"), [])
    with closing(connect_catalog_db(db_path)) as connection:
        before = connection.total_changes
        apply_catalog_migrations(connection)
        assert connection.total_changes == before
        assert not connection.in_transaction
