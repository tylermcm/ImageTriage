from __future__ import annotations

CATALOG_MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS catalog_folders (
            folder_key TEXT PRIMARY KEY,
            folder_path TEXT NOT NULL,
            record_count INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'scan',
            last_indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS catalog_records (
            folder_key TEXT NOT NULL,
            record_path TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER NOT NULL,
            modified_ns INTEGER NOT NULL,
            position INTEGER NOT NULL,
            PRIMARY KEY (folder_key, record_path),
            FOREIGN KEY (folder_key) REFERENCES catalog_folders(folder_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS catalog_record_members (
            folder_key TEXT NOT NULL,
            record_path TEXT NOT NULL,
            member_role TEXT NOT NULL,
            member_path TEXT NOT NULL,
            member_name TEXT NOT NULL DEFAULT '',
            member_size INTEGER NOT NULL DEFAULT 0,
            member_modified_ns INTEGER NOT NULL DEFAULT 0,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (folder_key, record_path, member_role, member_path),
            FOREIGN KEY (folder_key, record_path) REFERENCES catalog_records(folder_key, record_path) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_catalog_records_folder_position
        ON catalog_records(folder_key, position);

        CREATE INDEX IF NOT EXISTS idx_catalog_record_members_folder_record_position
        ON catalog_record_members(folder_key, record_path, member_role, position);
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS catalog_review_scoring_cache (
            folder_key TEXT NOT NULL,
            session_id TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            provider_id TEXT NOT NULL DEFAULT '',
            taste_event_count INTEGER NOT NULL DEFAULT 0,
            taste_detail_bias REAL NOT NULL DEFAULT 0.0,
            taste_ai_alignment_bias REAL NOT NULL DEFAULT 0.0,
            taste_summary_lines_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (folder_key, session_id),
            FOREIGN KEY (folder_key) REFERENCES catalog_folders(folder_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS catalog_review_scoring_recommendations (
            folder_key TEXT NOT NULL,
            session_id TEXT NOT NULL,
            record_path TEXT NOT NULL,
            group_id TEXT NOT NULL DEFAULT '',
            group_label TEXT NOT NULL DEFAULT '',
            group_size INTEGER NOT NULL DEFAULT 0,
            recommended_path TEXT NOT NULL DEFAULT '',
            rank_in_group INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0.0,
            recommended_score REAL NOT NULL DEFAULT 0.0,
            is_recommended INTEGER NOT NULL DEFAULT 0,
            reasons_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (folder_key, session_id, record_path),
            FOREIGN KEY (folder_key, session_id) REFERENCES catalog_review_scoring_cache(folder_key, session_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_catalog_review_scoring_cache_lookup
        ON catalog_review_scoring_cache(folder_key, session_id, cache_key);

        CREATE INDEX IF NOT EXISTS idx_catalog_review_scoring_recommendations_lookup
        ON catalog_review_scoring_recommendations(folder_key, session_id);
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS catalog_review_grouping_cache (
            folder_key TEXT PRIMARY KEY,
            cache_key TEXT NOT NULL,
            provider_id TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_key) REFERENCES catalog_folders(folder_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS catalog_review_groups (
            folder_key TEXT NOT NULL,
            group_id TEXT NOT NULL,
            group_kind TEXT NOT NULL DEFAULT '',
            group_label TEXT NOT NULL DEFAULT '',
            reasons_json TEXT NOT NULL DEFAULT '[]',
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (folder_key, group_id),
            FOREIGN KEY (folder_key) REFERENCES catalog_review_grouping_cache(folder_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS catalog_review_group_members (
            folder_key TEXT NOT NULL,
            group_id TEXT NOT NULL,
            record_path TEXT NOT NULL,
            rank_in_group INTEGER NOT NULL DEFAULT 0,
            detail_score REAL NOT NULL DEFAULT 0.0,
            exposure_score REAL NOT NULL DEFAULT 0.0,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (folder_key, group_id, record_path),
            FOREIGN KEY (folder_key, group_id) REFERENCES catalog_review_groups(folder_key, group_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_catalog_review_grouping_cache_lookup
        ON catalog_review_grouping_cache(folder_key, cache_key);

        CREATE INDEX IF NOT EXISTS idx_catalog_review_groups_position
        ON catalog_review_groups(folder_key, position);

        CREATE INDEX IF NOT EXISTS idx_catalog_review_group_members_position
        ON catalog_review_group_members(folder_key, group_id, position);
        """,
    ),
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS catalog_review_feature_cache (
            folder_key TEXT NOT NULL,
            record_path TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            source_path TEXT NOT NULL DEFAULT '',
            metadata_provider_id TEXT NOT NULL DEFAULT '',
            camera_make TEXT NOT NULL DEFAULT '',
            camera_model TEXT NOT NULL DEFAULT '',
            camera TEXT NOT NULL DEFAULT '',
            exposure TEXT NOT NULL DEFAULT '',
            aperture TEXT NOT NULL DEFAULT '',
            iso TEXT NOT NULL DEFAULT '',
            focal_length TEXT NOT NULL DEFAULT '',
            lens TEXT NOT NULL DEFAULT '',
            captured_at TEXT NOT NULL DEFAULT '',
            captured_at_iso TEXT NOT NULL DEFAULT '',
            orientation TEXT NOT NULL DEFAULT '',
            exposure_seconds REAL,
            aperture_value REAL,
            iso_value REAL,
            focal_length_value REAL,
            width INTEGER NOT NULL DEFAULT 0,
            height INTEGER NOT NULL DEFAULT 0,
            dhash_hex TEXT NOT NULL DEFAULT '',
            avg_luma REAL NOT NULL DEFAULT 0.0,
            detail_score REAL NOT NULL DEFAULT 0.0,
            exposure_score REAL NOT NULL DEFAULT 0.0,
            sha1_digest TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (folder_key, record_path),
            FOREIGN KEY (folder_key, record_path) REFERENCES catalog_records(folder_key, record_path) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_catalog_review_feature_cache_lookup
        ON catalog_review_feature_cache(folder_key, cache_key);
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS catalog_ai_bundle_cache (
            folder_key TEXT PRIMARY KEY,
            cache_key TEXT NOT NULL,
            source_path TEXT NOT NULL DEFAULT '',
            export_csv_path TEXT NOT NULL DEFAULT '',
            summary_json_path TEXT NOT NULL DEFAULT '',
            report_html_path TEXT NOT NULL DEFAULT '',
            summary_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_key) REFERENCES catalog_folders(folder_key) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS catalog_ai_results (
            folder_key TEXT NOT NULL,
            file_path TEXT NOT NULL,
            image_id TEXT NOT NULL DEFAULT '',
            file_name TEXT NOT NULL DEFAULT '',
            group_id TEXT NOT NULL DEFAULT '',
            group_size INTEGER NOT NULL DEFAULT 0,
            rank_in_group INTEGER NOT NULL DEFAULT 0,
            score REAL NOT NULL DEFAULT 0.0,
            cluster_reason TEXT NOT NULL DEFAULT '',
            capture_timestamp TEXT NOT NULL DEFAULT '',
            normalized_score REAL,
            folder_percentile REAL,
            score_gap_to_next REAL,
            score_gap_to_top REAL,
            confidence_bucket TEXT NOT NULL DEFAULT 'needs_review',
            confidence_summary TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (folder_key, file_path),
            FOREIGN KEY (folder_key) REFERENCES catalog_ai_bundle_cache(folder_key) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_catalog_ai_bundle_cache_lookup
        ON catalog_ai_bundle_cache(folder_key, cache_key);

        CREATE INDEX IF NOT EXISTS idx_catalog_ai_results_group
        ON catalog_ai_results(folder_key, group_id, rank_in_group);
        """,
    ),
    (
        6,
        """
        CREATE TABLE IF NOT EXISTS catalog_ai_workflow_cache (
            folder_key TEXT PRIMARY KEY,
            embedding_cache_key TEXT NOT NULL DEFAULT '',
            cluster_cache_key TEXT NOT NULL DEFAULT '',
            report_cache_key TEXT NOT NULL DEFAULT '',
            artifacts_dir TEXT NOT NULL DEFAULT '',
            report_dir TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (folder_key) REFERENCES catalog_folders(folder_key) ON DELETE CASCADE
        );
        """,
    ),
)

LATEST_CATALOG_SCHEMA_VERSION = CATALOG_MIGRATIONS[-1][0]
