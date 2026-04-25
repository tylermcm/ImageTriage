from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..models import ImageRecord, ImageVariant
from .db import connect_catalog_db, default_catalog_db_path
from .migrations import apply_catalog_migrations

if TYPE_CHECKING:
    from ..ai_results import AIBundle
    from ..review_intelligence import _RecordFingerprint
    from ..review_intelligence import ReviewIntelligenceBundle
    from ..review_workflows import BurstRecommendation, TasteProfile


@dataclass(slots=True, frozen=True)
class CatalogStats:
    db_path: Path
    folder_count: int = 0
    record_count: int = 0
    feature_count: int = 0
    ai_cache_count: int = 0
    grouping_cache_count: int = 0
    scoring_cache_count: int = 0
    last_indexed_at: str = ""
    available: bool = False
    error_message: str = ""


@dataclass(slots=True, frozen=True)
class ReviewScoringCacheEntry:
    cache_key: str
    provider_id: str
    taste_profile: TasteProfile
    recommendations: dict[str, BurstRecommendation]


@dataclass(slots=True, frozen=True)
class ReviewGroupingCacheEntry:
    cache_key: str
    provider_id: str
    bundle: ReviewIntelligenceBundle


@dataclass(slots=True, frozen=True)
class AIBundleCacheEntry:
    cache_key: str
    bundle: AIBundle


@dataclass(slots=True, frozen=True)
class AIWorkflowCacheEntry:
    embedding_cache_key: str
    cluster_cache_key: str
    report_cache_key: str
    artifacts_dir: str
    report_dir: str


def catalog_cache_env_override() -> bool | None:
    value = os.environ.get("IMAGE_TRIAGE_USE_CATALOG_CACHE", "").strip().casefold()
    if not value:
        return None
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return None


def catalog_cache_enabled(default: bool = True) -> bool:
    override = catalog_cache_env_override()
    return default if override is None else override


class CatalogRepository:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else default_catalog_db_path()

    def list_folder_paths(self) -> tuple[str, ...]:
        if not self.db_path.exists():
            return ()
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                rows = connection.execute(
                    """
                    SELECT folder_path
                    FROM catalog_folders
                    ORDER BY last_indexed_at DESC, folder_path
                    """
                ).fetchall()
        except sqlite3.DatabaseError:
            return ()
        return tuple(str(row["folder_path"]) for row in rows if str(row["folder_path"] or "").strip())

    def stats(self) -> CatalogStats:
        if not self.db_path.exists():
            return CatalogStats(db_path=self.db_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                row = connection.execute(
                    """
                    SELECT
                        (SELECT COUNT(*) FROM catalog_folders),
                        (SELECT COALESCE(SUM(record_count), 0) FROM catalog_folders),
                        (SELECT COUNT(*) FROM catalog_review_feature_cache),
                        (SELECT COUNT(*) FROM catalog_ai_bundle_cache),
                        (SELECT COUNT(*) FROM catalog_review_grouping_cache),
                        (SELECT COUNT(*) FROM catalog_review_scoring_cache),
                        COALESCE((SELECT MAX(last_indexed_at) FROM catalog_folders), '')
                    """
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            return CatalogStats(
                db_path=self.db_path,
                error_message=str(exc),
            )
        folder_count = int(row[0]) if row is not None else 0
        record_count = int(row[1]) if row is not None else 0
        feature_count = int(row[2]) if row is not None else 0
        ai_cache_count = int(row[3]) if row is not None else 0
        grouping_cache_count = int(row[4]) if row is not None else 0
        scoring_cache_count = int(row[5]) if row is not None else 0
        last_indexed_at = str(row[6] or "") if row is not None else ""
        return CatalogStats(
            db_path=self.db_path,
            folder_count=folder_count,
            record_count=record_count,
            feature_count=feature_count,
            ai_cache_count=ai_cache_count,
            grouping_cache_count=grouping_cache_count,
            scoring_cache_count=scoring_cache_count,
            last_indexed_at=last_indexed_at,
            available=True,
        )

    def load_folder_records(self, folder: str) -> list[ImageRecord] | None:
        folder_path = _normalize_filesystem_path(folder)
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                folder_row = connection.execute(
                    "SELECT folder_path FROM catalog_folders WHERE folder_key = ?",
                    (folder_key,),
                ).fetchone()
                if folder_row is None:
                    return None
                record_rows = connection.execute(
                    """
                    SELECT record_path, name, size, modified_ns
                    FROM catalog_records
                    WHERE folder_key = ?
                    ORDER BY position
                    """,
                    (folder_key,),
                ).fetchall()
                member_rows = connection.execute(
                    """
                    SELECT record_path, member_role, member_path, member_name, member_size, member_modified_ns
                    FROM catalog_record_members
                    WHERE folder_key = ?
                    ORDER BY record_path, member_role, position
                    """,
                    (folder_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return None

        member_paths: dict[tuple[str, str], list[sqlite3.Row]] = {}
        for row in member_rows:
            member_paths.setdefault((str(row["record_path"]), str(row["member_role"])), []).append(row)

        records: list[ImageRecord] = []
        for row in record_rows:
            record_path = str(row["record_path"])
            companion_rows = member_paths.get((record_path, "companion"), [])
            edit_rows = member_paths.get((record_path, "edit"), [])
            variant_rows = member_paths.get((record_path, "variant"), [])
            records.append(
                ImageRecord(
                    path=record_path,
                    name=str(row["name"]),
                    size=int(row["size"]),
                    modified_ns=int(row["modified_ns"]),
                    companion_paths=tuple(str(member["member_path"]) for member in companion_rows),
                    edited_paths=tuple(str(member["member_path"]) for member in edit_rows),
                    variants=tuple(
                        ImageVariant(
                            path=str(member["member_path"]),
                            name=str(member["member_name"]),
                            size=int(member["member_size"]),
                            modified_ns=int(member["member_modified_ns"]),
                        )
                        for member in variant_rows
                    ),
                )
            )
        return records

    def save_folder_records(
        self,
        folder: str,
        records: list[ImageRecord],
        *,
        source: str = "scan",
    ) -> bool:
        folder_path = _normalize_filesystem_path(folder)
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                with connection:
                    connection.execute(
                        """
                        INSERT INTO catalog_folders(folder_key, folder_path, record_count, source, last_indexed_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(folder_key) DO UPDATE SET
                            folder_path = excluded.folder_path,
                            record_count = excluded.record_count,
                            source = excluded.source,
                            last_indexed_at = CURRENT_TIMESTAMP
                        """,
                        (folder_key, folder_path, len(records), source),
                    )
                    connection.execute("DELETE FROM catalog_record_members WHERE folder_key = ?", (folder_key,))
                    connection.execute("DELETE FROM catalog_records WHERE folder_key = ?", (folder_key,))
                    for position, record in enumerate(records):
                        connection.execute(
                            """
                            INSERT INTO catalog_records(folder_key, record_path, name, size, modified_ns, position)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                folder_key,
                                record.path,
                                record.name,
                                int(record.size),
                                int(record.modified_ns),
                                position,
                            ),
                        )
                        self._insert_path_members(connection, folder_key, record.path, "companion", record.companion_paths)
                        self._insert_path_members(connection, folder_key, record.path, "edit", record.edited_paths)
                        self._insert_variants(connection, folder_key, record.path, record.display_variants if record.variants else ())
        except sqlite3.DatabaseError:
            return False
        return True

    def load_review_scoring(
        self,
        folder: str,
        *,
        session_id: str,
        cache_key: str,
    ) -> ReviewScoringCacheEntry | None:
        from ..review_workflows import BurstRecommendation, TasteProfile

        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return None
        folder_key = _normalized_path_key(folder_path)
        normalized_session = _normalize_session_id(session_id)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                cache_row = connection.execute(
                    """
                    SELECT
                        cache_key,
                        provider_id,
                        taste_event_count,
                        taste_detail_bias,
                        taste_ai_alignment_bias,
                        taste_summary_lines_json
                    FROM catalog_review_scoring_cache
                    WHERE folder_key = ? AND session_id = ? AND cache_key = ?
                    """,
                    (folder_key, normalized_session, cache_key),
                ).fetchone()
                if cache_row is None:
                    return None
                recommendation_rows = connection.execute(
                    """
                    SELECT
                        record_path,
                        group_id,
                        group_label,
                        group_size,
                        recommended_path,
                        rank_in_group,
                        score,
                        recommended_score,
                        is_recommended,
                        reasons_json
                    FROM catalog_review_scoring_recommendations
                    WHERE folder_key = ? AND session_id = ?
                    ORDER BY record_path
                    """,
                    (folder_key, normalized_session),
                ).fetchall()
        except sqlite3.DatabaseError:
            return None

        summary_lines = _json_string_list(cache_row["taste_summary_lines_json"])
        taste_profile = TasteProfile(
            event_count=int(cache_row["taste_event_count"] or 0),
            detail_bias=float(cache_row["taste_detail_bias"] or 0.0),
            ai_alignment_bias=float(cache_row["taste_ai_alignment_bias"] or 0.0),
            summary_lines=tuple(summary_lines),
        )
        recommendations: dict[str, BurstRecommendation] = {}
        for row in recommendation_rows:
            recommendation = BurstRecommendation(
                path=str(row["record_path"] or ""),
                group_id=str(row["group_id"] or ""),
                group_label=str(row["group_label"] or ""),
                group_size=int(row["group_size"] or 0),
                recommended_path=str(row["recommended_path"] or ""),
                rank_in_group=int(row["rank_in_group"] or 0),
                score=float(row["score"] or 0.0),
                recommended_score=float(row["recommended_score"] or 0.0),
                is_recommended=bool(int(row["is_recommended"] or 0)),
                reasons=tuple(_json_string_list(row["reasons_json"])),
            )
            if not recommendation.path:
                continue
            recommendations[recommendation.path] = recommendation
            recommendations[_normalized_path_key(recommendation.path)] = recommendation
        return ReviewScoringCacheEntry(
            cache_key=str(cache_row["cache_key"] or ""),
            provider_id=str(cache_row["provider_id"] or ""),
            taste_profile=taste_profile,
            recommendations=recommendations,
        )

    def save_review_scoring(
        self,
        folder: str,
        *,
        session_id: str,
        cache_key: str,
        provider_id: str,
        records: tuple[ImageRecord, ...] | list[ImageRecord],
        taste_profile: TasteProfile,
        recommendations: dict[str, BurstRecommendation],
    ) -> bool:
        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return False
        folder_key = _normalized_path_key(folder_path)
        normalized_session = _normalize_session_id(session_id)
        summary_lines_json = json.dumps(list(taste_profile.summary_lines), ensure_ascii=True)
        direct_paths = {record.path for record in records}
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                with connection:
                    connection.execute(
                        """
                        INSERT INTO catalog_review_scoring_cache(
                            folder_key,
                            session_id,
                            cache_key,
                            provider_id,
                            taste_event_count,
                            taste_detail_bias,
                            taste_ai_alignment_bias,
                            taste_summary_lines_json,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(folder_key, session_id) DO UPDATE SET
                            cache_key = excluded.cache_key,
                            provider_id = excluded.provider_id,
                            taste_event_count = excluded.taste_event_count,
                            taste_detail_bias = excluded.taste_detail_bias,
                            taste_ai_alignment_bias = excluded.taste_ai_alignment_bias,
                            taste_summary_lines_json = excluded.taste_summary_lines_json,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            folder_key,
                            normalized_session,
                            cache_key,
                            provider_id,
                            int(taste_profile.event_count),
                            float(taste_profile.detail_bias),
                            float(taste_profile.ai_alignment_bias),
                            summary_lines_json,
                        ),
                    )
                    connection.execute(
                        """
                        DELETE FROM catalog_review_scoring_recommendations
                        WHERE folder_key = ? AND session_id = ?
                        """,
                        (folder_key, normalized_session),
                    )
                    for record_path in sorted(direct_paths, key=str.casefold):
                        recommendation = recommendations.get(record_path) or recommendations.get(_normalized_path_key(record_path))
                        if recommendation is None:
                            continue
                        connection.execute(
                            """
                            INSERT INTO catalog_review_scoring_recommendations(
                                folder_key,
                                session_id,
                                record_path,
                                group_id,
                                group_label,
                                group_size,
                                recommended_path,
                                rank_in_group,
                                score,
                                recommended_score,
                                is_recommended,
                                reasons_json
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                folder_key,
                                normalized_session,
                                record_path,
                                recommendation.group_id,
                                recommendation.group_label,
                                int(recommendation.group_size),
                                recommendation.recommended_path,
                                int(recommendation.rank_in_group),
                                float(recommendation.score),
                                float(recommendation.recommended_score),
                                1 if recommendation.is_recommended else 0,
                                json.dumps(list(recommendation.reasons), ensure_ascii=True),
                            ),
                        )
        except sqlite3.DatabaseError:
            return False
        return True

    def load_review_features(
        self,
        folder: str,
        *,
        records: tuple[ImageRecord, ...] | list[ImageRecord],
        cache_keys: dict[str, str],
    ) -> dict[str, _RecordFingerprint]:
        from ..metadata import CaptureMetadata
        from ..review_intelligence import _RecordFingerprint

        folder_path = _normalize_filesystem_path(folder)
        if not folder_path or not records:
            return {}
        folder_key = _normalized_path_key(folder_path)
        record_map = {record.path: record for record in records}
        direct_paths = tuple(record_map.keys())
        if not direct_paths:
            return {}
        placeholders = ", ".join("?" for _ in direct_paths)
        params = [folder_key, *direct_paths]
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                rows = connection.execute(
                    f"""
                    SELECT
                        record_path,
                        cache_key,
                        source_path,
                        camera_make,
                        camera_model,
                        camera,
                        exposure,
                        aperture,
                        iso,
                        focal_length,
                        lens,
                        captured_at,
                        captured_at_iso,
                        orientation,
                        exposure_seconds,
                        aperture_value,
                        iso_value,
                        focal_length_value,
                        width,
                        height,
                        dhash_hex,
                        avg_luma,
                        detail_score,
                        exposure_score,
                        sha1_digest
                    FROM catalog_review_feature_cache
                    WHERE folder_key = ? AND record_path IN ({placeholders})
                    """,
                    params,
                ).fetchall()
        except sqlite3.DatabaseError:
            return {}

        loaded: dict[str, _RecordFingerprint] = {}
        for row in rows:
            record_path = str(row["record_path"] or "")
            record = record_map.get(record_path)
            expected_cache_key = cache_keys.get(record_path)
            if record is None or not expected_cache_key or str(row["cache_key"] or "") != expected_cache_key:
                continue
            source_path = str(row["source_path"] or record.path)
            metadata = CaptureMetadata(
                path=source_path,
                camera_make=str(row["camera_make"] or ""),
                camera_model=str(row["camera_model"] or ""),
                camera=str(row["camera"] or ""),
                exposure=str(row["exposure"] or ""),
                aperture=str(row["aperture"] or ""),
                iso=str(row["iso"] or ""),
                focal_length=str(row["focal_length"] or ""),
                lens=str(row["lens"] or ""),
                captured_at=str(row["captured_at"] or ""),
                orientation=str(row["orientation"] or ""),
                exposure_seconds=_optional_float(row["exposure_seconds"]),
                aperture_value=_optional_float(row["aperture_value"]),
                iso_value=_optional_float(row["iso_value"]),
                focal_length_value=_optional_float(row["focal_length_value"]),
                captured_at_value=_optional_datetime(row["captured_at_iso"]),
                width=int(row["width"] or 0),
                height=int(row["height"] or 0),
            )
            dhash = _decode_hex_int(row["dhash_hex"])
            fingerprint = _RecordFingerprint(
                record=record,
                source_path=source_path,
                metadata=metadata,
                dhash=dhash,
                avg_luma=float(row["avg_luma"] or 0.0),
                width=int(row["width"] or 0),
                height=int(row["height"] or 0),
                sha1_digest=str(row["sha1_digest"] or ""),
                detail_score=float(row["detail_score"] or 0.0),
                exposure_score=float(row["exposure_score"] or 0.0),
            )
            loaded[record_path] = fingerprint
            loaded[_normalized_path_key(record_path)] = fingerprint
        return loaded

    def save_review_features(
        self,
        folder: str,
        *,
        cache_keys: dict[str, str],
        fingerprints: tuple[_RecordFingerprint, ...] | list[_RecordFingerprint],
    ) -> bool:
        from ..metadata import metadata_provider_id_for_path

        folder_path = _normalize_filesystem_path(folder)
        if not folder_path or not fingerprints:
            return False
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                with connection:
                    for fingerprint in fingerprints:
                        record_path = fingerprint.record.path
                        cache_key = cache_keys.get(record_path)
                        if not cache_key:
                            continue
                        metadata = fingerprint.metadata
                        metadata_provider_id = metadata_provider_id_for_path(fingerprint.source_path)
                        connection.execute(
                            """
                            INSERT INTO catalog_review_feature_cache(
                                folder_key,
                                record_path,
                                cache_key,
                                source_path,
                                metadata_provider_id,
                                camera_make,
                                camera_model,
                                camera,
                                exposure,
                                aperture,
                                iso,
                                focal_length,
                                lens,
                                captured_at,
                                captured_at_iso,
                                orientation,
                                exposure_seconds,
                                aperture_value,
                                iso_value,
                                focal_length_value,
                                width,
                                height,
                                dhash_hex,
                                avg_luma,
                                detail_score,
                                exposure_score,
                                sha1_digest,
                                updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                            ON CONFLICT(folder_key, record_path) DO UPDATE SET
                                cache_key = excluded.cache_key,
                                source_path = excluded.source_path,
                                metadata_provider_id = excluded.metadata_provider_id,
                                camera_make = excluded.camera_make,
                                camera_model = excluded.camera_model,
                                camera = excluded.camera,
                                exposure = excluded.exposure,
                                aperture = excluded.aperture,
                                iso = excluded.iso,
                                focal_length = excluded.focal_length,
                                lens = excluded.lens,
                                captured_at = excluded.captured_at,
                                captured_at_iso = excluded.captured_at_iso,
                                orientation = excluded.orientation,
                                exposure_seconds = excluded.exposure_seconds,
                                aperture_value = excluded.aperture_value,
                                iso_value = excluded.iso_value,
                                focal_length_value = excluded.focal_length_value,
                                width = excluded.width,
                                height = excluded.height,
                                dhash_hex = excluded.dhash_hex,
                                avg_luma = excluded.avg_luma,
                                detail_score = excluded.detail_score,
                                exposure_score = excluded.exposure_score,
                                sha1_digest = excluded.sha1_digest,
                                updated_at = CURRENT_TIMESTAMP
                            """,
                            (
                                folder_key,
                                record_path,
                                cache_key,
                                fingerprint.source_path,
                                metadata_provider_id,
                                metadata.camera_make,
                                metadata.camera_model,
                                metadata.camera,
                                metadata.exposure,
                                metadata.aperture,
                                metadata.iso,
                                metadata.focal_length,
                                metadata.lens,
                                metadata.captured_at,
                                _serialize_datetime(metadata.captured_at_value),
                                metadata.orientation,
                                metadata.exposure_seconds,
                                metadata.aperture_value,
                                metadata.iso_value,
                                metadata.focal_length_value,
                                int(fingerprint.width),
                                int(fingerprint.height),
                                _encode_hex_int(fingerprint.dhash),
                                float(fingerprint.avg_luma),
                                float(fingerprint.detail_score),
                                float(fingerprint.exposure_score),
                                fingerprint.sha1_digest,
                            ),
                        )
        except sqlite3.DatabaseError:
            return False
        return True

    def load_ai_bundle(
        self,
        folder: str,
        *,
        cache_key: str,
    ) -> AIBundleCacheEntry | None:
        from ..ai_results import AIConfidenceBucket, AIImageResult, build_ai_bundle_from_results

        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return None
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                cache_row = connection.execute(
                    """
                    SELECT
                        cache_key,
                        source_path,
                        export_csv_path,
                        summary_json_path,
                        report_html_path,
                        summary_json
                    FROM catalog_ai_bundle_cache
                    WHERE folder_key = ? AND cache_key = ?
                    """,
                    (folder_key, cache_key),
                ).fetchone()
                if cache_row is None:
                    return None
                result_rows = connection.execute(
                    """
                    SELECT
                        file_path,
                        image_id,
                        file_name,
                        group_id,
                        group_size,
                        rank_in_group,
                        score,
                        cluster_reason,
                        capture_timestamp,
                        normalized_score,
                        folder_percentile,
                        score_gap_to_next,
                        score_gap_to_top,
                        confidence_bucket,
                        confidence_summary
                    FROM catalog_ai_results
                    WHERE folder_key = ?
                    ORDER BY group_id, rank_in_group, file_name, file_path
                    """,
                    (folder_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return None

        results: list[AIImageResult] = []
        for row in result_rows:
            file_path = str(row["file_path"] or "")
            if not file_path:
                continue
            bucket_value = str(row["confidence_bucket"] or AIConfidenceBucket.NEEDS_REVIEW.value)
            try:
                confidence_bucket = AIConfidenceBucket(bucket_value)
            except ValueError:
                confidence_bucket = AIConfidenceBucket.NEEDS_REVIEW
            results.append(
                AIImageResult(
                    image_id=str(row["image_id"] or ""),
                    file_path=file_path,
                    file_name=str(row["file_name"] or Path(file_path).name),
                    group_id=str(row["group_id"] or ""),
                    group_size=int(row["group_size"] or 0),
                    rank_in_group=int(row["rank_in_group"] or 0),
                    score=float(row["score"] or 0.0),
                    cluster_reason=str(row["cluster_reason"] or ""),
                    capture_timestamp=str(row["capture_timestamp"] or ""),
                    normalized_score=_optional_float(row["normalized_score"]),
                    folder_percentile=_optional_float(row["folder_percentile"]),
                    score_gap_to_next=_optional_float(row["score_gap_to_next"]),
                    score_gap_to_top=_optional_float(row["score_gap_to_top"]),
                    confidence_bucket=confidence_bucket,
                    confidence_summary=str(row["confidence_summary"] or ""),
                )
            )

        summary = _json_object(cache_row["summary_json"])
        bundle = build_ai_bundle_from_results(
            source_path=str(cache_row["source_path"] or folder_path),
            export_csv_path=str(cache_row["export_csv_path"] or ""),
            summary_json_path=str(cache_row["summary_json_path"] or ""),
            report_html_path=str(cache_row["report_html_path"] or ""),
            results=results,
            summary=summary,
        )
        return AIBundleCacheEntry(
            cache_key=str(cache_row["cache_key"] or ""),
            bundle=bundle,
        )

    def load_ai_workflow_cache(self, folder: str) -> AIWorkflowCacheEntry | None:
        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return None
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                row = connection.execute(
                    """
                    SELECT
                        embedding_cache_key,
                        cluster_cache_key,
                        report_cache_key,
                        artifacts_dir,
                        report_dir
                    FROM catalog_ai_workflow_cache
                    WHERE folder_key = ?
                    """,
                    (folder_key,),
                ).fetchone()
        except sqlite3.DatabaseError:
            return None
        if row is None:
            return None
        return AIWorkflowCacheEntry(
            embedding_cache_key=str(row["embedding_cache_key"] or ""),
            cluster_cache_key=str(row["cluster_cache_key"] or ""),
            report_cache_key=str(row["report_cache_key"] or ""),
            artifacts_dir=str(row["artifacts_dir"] or ""),
            report_dir=str(row["report_dir"] or ""),
        )

    def save_ai_workflow_cache(
        self,
        folder: str,
        *,
        embedding_cache_key: str,
        cluster_cache_key: str,
        report_cache_key: str,
        artifacts_dir: str,
        report_dir: str,
    ) -> bool:
        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return False
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                with connection:
                    connection.execute(
                        """
                        INSERT INTO catalog_ai_workflow_cache(
                            folder_key,
                            embedding_cache_key,
                            cluster_cache_key,
                            report_cache_key,
                            artifacts_dir,
                            report_dir,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(folder_key) DO UPDATE SET
                            embedding_cache_key = excluded.embedding_cache_key,
                            cluster_cache_key = excluded.cluster_cache_key,
                            report_cache_key = excluded.report_cache_key,
                            artifacts_dir = excluded.artifacts_dir,
                            report_dir = excluded.report_dir,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            folder_key,
                            embedding_cache_key,
                            cluster_cache_key,
                            report_cache_key,
                            artifacts_dir,
                            report_dir,
                        ),
                    )
        except sqlite3.DatabaseError:
            return False
        return True

    def save_ai_bundle(
        self,
        folder: str,
        *,
        cache_key: str,
        bundle: AIBundle,
    ) -> bool:
        from ..ai_results import iter_ai_bundle_results

        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return False
        folder_key = _normalized_path_key(folder_path)
        results = iter_ai_bundle_results(bundle)
        summary_json = json.dumps(bundle.summary if isinstance(bundle.summary, dict) else {}, ensure_ascii=True, sort_keys=True)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                with connection:
                    connection.execute(
                        """
                        INSERT INTO catalog_ai_bundle_cache(
                            folder_key,
                            cache_key,
                            source_path,
                            export_csv_path,
                            summary_json_path,
                            report_html_path,
                            summary_json,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(folder_key) DO UPDATE SET
                            cache_key = excluded.cache_key,
                            source_path = excluded.source_path,
                            export_csv_path = excluded.export_csv_path,
                            summary_json_path = excluded.summary_json_path,
                            report_html_path = excluded.report_html_path,
                            summary_json = excluded.summary_json,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (
                            folder_key,
                            cache_key,
                            bundle.source_path,
                            bundle.export_csv_path,
                            bundle.summary_json_path,
                            bundle.report_html_path,
                            summary_json,
                        ),
                    )
                    connection.execute(
                        "DELETE FROM catalog_ai_results WHERE folder_key = ?",
                        (folder_key,),
                    )
                    for result in results:
                        connection.execute(
                            """
                            INSERT INTO catalog_ai_results(
                                folder_key,
                                file_path,
                                image_id,
                                file_name,
                                group_id,
                                group_size,
                                rank_in_group,
                                score,
                                cluster_reason,
                                capture_timestamp,
                                normalized_score,
                                folder_percentile,
                                score_gap_to_next,
                                score_gap_to_top,
                                confidence_bucket,
                                confidence_summary
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                folder_key,
                                result.file_path,
                                result.image_id,
                                result.file_name,
                                result.group_id,
                                int(result.group_size),
                                int(result.rank_in_group),
                                float(result.score),
                                result.cluster_reason,
                                result.capture_timestamp,
                                result.normalized_score,
                                result.folder_percentile,
                                result.score_gap_to_next,
                                result.score_gap_to_top,
                                result.confidence_bucket.value if hasattr(result.confidence_bucket, "value") else str(result.confidence_bucket),
                                result.confidence_summary,
                            ),
                        )
        except sqlite3.DatabaseError:
            return False
        return True

    def load_review_grouping(
        self,
        folder: str,
        *,
        cache_key: str,
    ) -> ReviewGroupingCacheEntry | None:
        from ..review_intelligence import ReviewGroup, ReviewInsight, ReviewIntelligenceBundle

        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return None
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                cache_row = connection.execute(
                    """
                    SELECT cache_key, provider_id
                    FROM catalog_review_grouping_cache
                    WHERE folder_key = ? AND cache_key = ?
                    """,
                    (folder_key, cache_key),
                ).fetchone()
                if cache_row is None:
                    return None
                group_rows = connection.execute(
                    """
                    SELECT group_id, group_kind, group_label, reasons_json
                    FROM catalog_review_groups
                    WHERE folder_key = ?
                    ORDER BY position
                    """,
                    (folder_key,),
                ).fetchall()
                member_rows = connection.execute(
                    """
                    SELECT group_id, record_path, rank_in_group, detail_score, exposure_score
                    FROM catalog_review_group_members
                    WHERE folder_key = ?
                    ORDER BY group_id, position
                    """,
                    (folder_key,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return None

        members_by_group: dict[str, list[sqlite3.Row]] = {}
        for row in member_rows:
            members_by_group.setdefault(str(row["group_id"] or ""), []).append(row)

        groups: list[ReviewGroup] = []
        insights_by_path: dict[str, ReviewInsight] = {}
        for group_row in group_rows:
            group_id = str(group_row["group_id"] or "")
            member_rows_for_group = members_by_group.get(group_id, [])
            member_paths = tuple(str(row["record_path"] or "") for row in member_rows_for_group if str(row["record_path"] or ""))
            reasons = tuple(_json_string_list(group_row["reasons_json"]))
            if not group_id or not member_paths:
                continue
            review_group = ReviewGroup(
                id=group_id,
                kind=str(group_row["group_kind"] or ""),
                label=str(group_row["group_label"] or ""),
                member_paths=member_paths,
                reasons=reasons,
            )
            groups.append(review_group)
            group_size = len(member_paths)
            for row in member_rows_for_group:
                path = str(row["record_path"] or "")
                if not path:
                    continue
                insight = ReviewInsight(
                    path=path,
                    group_id=group_id,
                    group_kind=review_group.kind,
                    group_label=review_group.label,
                    group_size=group_size,
                    rank_in_group=int(row["rank_in_group"] or 0),
                    reasons=reasons,
                    detail_score=float(row["detail_score"] or 0.0),
                    exposure_score=float(row["exposure_score"] or 0.0),
                )
                insights_by_path[path] = insight
                insights_by_path[_normalized_path_key(path)] = insight
        bundle = ReviewIntelligenceBundle(groups=tuple(groups), insights_by_path=insights_by_path)
        return ReviewGroupingCacheEntry(
            cache_key=str(cache_row["cache_key"] or ""),
            provider_id=str(cache_row["provider_id"] or ""),
            bundle=bundle,
        )

    def save_review_grouping(
        self,
        folder: str,
        *,
        cache_key: str,
        provider_id: str,
        bundle: ReviewIntelligenceBundle,
    ) -> bool:
        folder_path = _normalize_filesystem_path(folder)
        if not folder_path:
            return False
        folder_key = _normalized_path_key(folder_path)
        try:
            with closing(connect_catalog_db(self.db_path)) as connection:
                apply_catalog_migrations(connection)
                with connection:
                    connection.execute(
                        """
                        INSERT INTO catalog_review_grouping_cache(
                            folder_key,
                            cache_key,
                            provider_id,
                            updated_at
                        )
                        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                        ON CONFLICT(folder_key) DO UPDATE SET
                            cache_key = excluded.cache_key,
                            provider_id = excluded.provider_id,
                            updated_at = CURRENT_TIMESTAMP
                        """,
                        (folder_key, cache_key, provider_id),
                    )
                    connection.execute(
                        "DELETE FROM catalog_review_group_members WHERE folder_key = ?",
                        (folder_key,),
                    )
                    connection.execute(
                        "DELETE FROM catalog_review_groups WHERE folder_key = ?",
                        (folder_key,),
                    )
                    for group_position, group in enumerate(bundle.groups):
                        connection.execute(
                            """
                            INSERT INTO catalog_review_groups(
                                folder_key,
                                group_id,
                                group_kind,
                                group_label,
                                reasons_json,
                                position
                            )
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (
                                folder_key,
                                group.id,
                                group.kind,
                                group.label,
                                json.dumps(list(group.reasons), ensure_ascii=True),
                                group_position,
                            ),
                        )
                        for member_position, member_path in enumerate(group.member_paths):
                            insight = bundle.insights_by_path.get(member_path) or bundle.insights_by_path.get(_normalized_path_key(member_path))
                            connection.execute(
                                """
                                INSERT INTO catalog_review_group_members(
                                    folder_key,
                                    group_id,
                                    record_path,
                                    rank_in_group,
                                    detail_score,
                                    exposure_score,
                                    position
                                )
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    folder_key,
                                    group.id,
                                    member_path,
                                    int(getattr(insight, "rank_in_group", member_position + 1) or (member_position + 1)),
                                    float(getattr(insight, "detail_score", 0.0) or 0.0),
                                    float(getattr(insight, "exposure_score", 0.0) or 0.0),
                                    member_position,
                                ),
                            )
        except sqlite3.DatabaseError:
            return False
        return True

    @staticmethod
    def _insert_path_members(
        connection: sqlite3.Connection,
        folder_key: str,
        record_path: str,
        role: str,
        paths: tuple[str, ...],
    ) -> None:
        for position, member_path in enumerate(paths):
            connection.execute(
                """
                INSERT INTO catalog_record_members(
                    folder_key,
                    record_path,
                    member_role,
                    member_path,
                    member_name,
                    member_size,
                    member_modified_ns,
                    position
                )
                VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    folder_key,
                    record_path,
                    role,
                    member_path,
                    Path(member_path).name,
                    position,
                ),
            )

    @staticmethod
    def _insert_variants(
        connection: sqlite3.Connection,
        folder_key: str,
        record_path: str,
        variants: tuple[ImageVariant, ...],
    ) -> None:
        for position, variant in enumerate(variants):
            connection.execute(
                """
                INSERT INTO catalog_record_members(
                    folder_key,
                    record_path,
                    member_role,
                    member_path,
                    member_name,
                    member_size,
                    member_modified_ns,
                    position
                )
                VALUES (?, ?, 'variant', ?, ?, ?, ?, ?)
                """,
                (
                    folder_key,
                    record_path,
                    variant.path,
                    variant.name,
                    int(variant.size),
                    int(variant.modified_ns),
                    position,
                ),
            )


def _normalize_filesystem_path(path: str | Path) -> str:
    raw = str(path).strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    try:
        candidate = candidate.resolve(strict=False)
    except OSError:
        candidate = candidate.absolute()
    return os.path.normpath(str(candidate))


def _normalized_path_key(path: str | Path) -> str:
    return _normalize_filesystem_path(path).casefold()


def _normalize_session_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    return value or "default"


def _json_string_list(value: object) -> list[str]:
    raw = str(value or "[]")
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return [str(item) for item in decoded if isinstance(item, str)]


def _json_object(value: object) -> dict:
    raw = str(value or "{}")
    try:
        decoded = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _serialize_datetime(value: datetime | None) -> str:
    return value.isoformat() if isinstance(value, datetime) else ""


def _optional_datetime(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _encode_hex_int(value: int | None) -> str:
    if value is None:
        return ""
    return format(int(value), "x")


def _decode_hex_int(value: object) -> int | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return int(raw, 16)
    except ValueError:
        return None
