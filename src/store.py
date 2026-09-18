from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_DB = Path("data/agent.db")


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_key TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            price REAL,
            commission_rate REAL,
            shop_name TEXT,
            source_url TEXT,
            status TEXT NOT NULL DEFAULT 'monitoring',
            decision TEXT NOT NULL DEFAULT 'monitorar',
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            date_range TEXT,
            collected_at TEXT NOT NULL,
            revenue_total REAL NOT NULL DEFAULT 0,
            revenue_video REAL NOT NULL DEFAULT 0,
            revenue_live REAL NOT NULL DEFAULT 0,
            sales_total REAL NOT NULL DEFAULT 0,
            video_count REAL NOT NULL DEFAULT 0,
            creator_count REAL NOT NULL DEFAULT 0,
            views_video_export REAL NOT NULL DEFAULT 0,
            views_creator_export REAL NOT NULL DEFAULT 0,
            ad_cost REAL NOT NULL DEFAULT 0,
            avg_price REAL NOT NULL DEFAULT 0,
            gpm_video_export REAL NOT NULL DEFAULT 0,
            cpa_ads REAL NOT NULL DEFAULT 0,
            roas REAL NOT NULL DEFAULT 0,
            confidence TEXT NOT NULL DEFAULT 'estimado',
            raw_meta TEXT,
            UNIQUE(product_id, source, date_range, collected_at),
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS video_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            creator_username TEXT,
            published_at TEXT,
            description TEXT,
            duration TEXT,
            revenue REAL NOT NULL DEFAULT 0,
            sales REAL NOT NULL DEFAULT 0,
            views REAL NOT NULL DEFAULT 0,
            gpm REAL NOT NULL DEFAULT 0,
            cpa REAL NOT NULL DEFAULT 0,
            ads_view_ratio TEXT,
            ad_cost REAL NOT NULL DEFAULT 0,
            roas REAL NOT NULL DEFAULT 0,
            kalodata_url TEXT,
            tiktok_url TEXT,
            row_hash TEXT NOT NULL UNIQUE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY(snapshot_id) REFERENCES product_snapshots(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS creator_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            snapshot_id INTEGER,
            creator_name TEXT,
            creator_username TEXT,
            followers REAL NOT NULL DEFAULT 0,
            revenue REAL NOT NULL DEFAULT 0,
            sales REAL NOT NULL DEFAULT 0,
            avg_price REAL NOT NULL DEFAULT 0,
            engagement_rate TEXT,
            new_followers REAL NOT NULL DEFAULT 0,
            product_count REAL NOT NULL DEFAULT 0,
            live_count REAL NOT NULL DEFAULT 0,
            live_gmv REAL NOT NULL DEFAULT 0,
            video_count REAL NOT NULL DEFAULT 0,
            video_gmv REAL NOT NULL DEFAULT 0,
            views REAL NOT NULL DEFAULT 0,
            creator_first_seen TEXT,
            kalodata_url TEXT,
            tiktok_url TEXT,
            row_hash TEXT NOT NULL UNIQUE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY(snapshot_id) REFERENCES product_snapshots(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS production_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            version_number INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'planejado',
            hook TEXT,
            format TEXT,
            posted_at TEXT,
            scheduled_at TEXT,
            views REAL NOT NULL DEFAULT 0,
            clicks REAL NOT NULL DEFAULT 0,
            orders_count REAL NOT NULL DEFAULT 0,
            gmv REAL NOT NULL DEFAULT 0,
            commission REAL NOT NULL DEFAULT 0,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(product_id, version_number),
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS content_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_code TEXT NOT NULL UNIQUE,
            product_id INTEGER NOT NULL,
            original_filename TEXT NOT NULL,
            local_path TEXT,
            storage_reference TEXT,
            file_hash TEXT NOT NULL UNIQUE,
            google_drive_file_id TEXT,
            google_drive_url TEXT,
            thumbnail TEXT,
            duration REAL,
            width INTEGER,
            height INTEGER,
            resolution TEXT,
            aspect_ratio REAL,
            file_size INTEGER,
            codec TEXT,
            audio_present INTEGER NOT NULL DEFAULT 0,
            validation_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
            validation_results TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'DRAFT',
            caption TEXT,
            hashtags TEXT,
            hook TEXT,
            body TEXT,
            cta TEXT,
            notes TEXT,
            priority INTEGER NOT NULL DEFAULT 50,
            generation_source TEXT,
            variation_number INTEGER,
            campaign TEXT,
            batch TEXT,
            queued_at TEXT,
            sent_to_phone_at TEXT,
            published_at TEXT,
            tiktok_url TEXT,
            tiktok_video_id TEXT,
            archived_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS publishing_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL UNIQUE,
            queue_position INTEGER NOT NULL,
            priority INTEGER NOT NULL DEFAULT 50,
            scheduled_date TEXT,
            status TEXT NOT NULL DEFAULT 'QUEUED',
            queued_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(content_id) REFERENCES content_items(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS content_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            views REAL NOT NULL DEFAULT 0,
            likes REAL NOT NULL DEFAULT 0,
            comments REAL NOT NULL DEFAULT 0,
            shares REAL NOT NULL DEFAULT 0,
            saves REAL NOT NULL DEFAULT 0,
            orders REAL NOT NULL DEFAULT 0,
            units_sold REAL NOT NULL DEFAULT 0,
            gmv REAL NOT NULL DEFAULT 0,
            commission REAL NOT NULL DEFAULT 0,
            ctr REAL,
            conversion_rate REAL,
            source TEXT NOT NULL DEFAULT 'manual',
            source_row_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(content_id) REFERENCES content_items(id) ON DELETE CASCADE,
            UNIQUE(content_id, captured_at, source_row_hash)
        );

        CREATE TABLE IF NOT EXISTS external_sync_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_id INTEGER NOT NULL,
            operation TEXT NOT NULL,
            idempotency_key TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL,
            response_reference TEXT,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS oauth_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            state_hash TEXT NOT NULL UNIQUE,
            session_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tiktok_creator_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'local',
            creator_id TEXT,
            creator_open_id TEXT NOT NULL UNIQUE,
            creator_username TEXT,
            display_name TEXT,
            market TEXT NOT NULL DEFAULT 'BR',
            status TEXT NOT NULL DEFAULT 'NOT_CONNECTED',
            granted_scopes TEXT NOT NULL DEFAULT '[]',
            access_token_encrypted TEXT,
            refresh_token_encrypted TEXT,
            access_token_expires_at TEXT,
            refresh_token_expires_at TEXT,
            last_sync_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS affiliate_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            external_order_id TEXT NOT NULL,
            creator_account_id INTEGER,
            product_id INTEGER,
            content_id INTEGER,
            external_product_id TEXT,
            order_status TEXT,
            order_created_at TEXT,
            order_updated_at TEXT,
            quantity REAL NOT NULL DEFAULT 0,
            currency TEXT,
            gmv REAL,
            estimated_commission REAL,
            actual_commission REAL,
            commission_status TEXT,
            refund_amount REAL,
            raw_payload TEXT NOT NULL DEFAULT '{}',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(provider, external_order_id),
            FOREIGN KEY(creator_account_id) REFERENCES tiktok_creator_accounts(id) ON DELETE SET NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL,
            FOREIGN KEY(content_id) REFERENCES content_items(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS affiliate_order_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_order_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            external_sku_id TEXT,
            product_id INTEGER,
            external_product_id TEXT,
            content_id INTEGER,
            external_content_id TEXT,
            content_type TEXT,
            product_name TEXT,
            quantity REAL NOT NULL DEFAULT 0,
            returned_quantity REAL NOT NULL DEFAULT 0,
            refunded_quantity REAL NOT NULL DEFAULT 0,
            currency TEXT,
            unit_price REAL,
            gmv REAL,
            estimated_commission REAL,
            actual_commission REAL,
            refund_amount REAL,
            raw_payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(affiliate_order_id, item_key),
            FOREIGN KEY(affiliate_order_id) REFERENCES affiliate_orders(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL,
            FOREIGN KEY(content_id) REFERENCES content_items(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS affiliate_order_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            affiliate_order_id INTEGER NOT NULL,
            previous_status TEXT,
            new_status TEXT,
            changed_at TEXT NOT NULL,
            raw_payload TEXT NOT NULL DEFAULT '{}',
            FOREIGN KEY(affiliate_order_id) REFERENCES affiliate_orders(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS affiliate_sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            creator_account_id INTEGER,
            sync_mode TEXT NOT NULL,
            window_start TEXT,
            window_end TEXT,
            status TEXT NOT NULL,
            pages_processed INTEGER NOT NULL DEFAULT 0,
            orders_seen INTEGER NOT NULL DEFAULT 0,
            orders_upserted INTEGER NOT NULL DEFAULT 0,
            error_code TEXT,
            error_message TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(creator_account_id) REFERENCES tiktok_creator_accounts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS tiktok_api_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            creator_account_id INTEGER,
            endpoint TEXT NOT NULL,
            request_id TEXT,
            http_status INTEGER,
            api_code TEXT,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            result_count INTEGER,
            status TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(creator_account_id) REFERENCES tiktok_creator_accounts(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS affiliate_report_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL UNIQUE,
            file_size INTEGER NOT NULL DEFAULT 0,
            provider TEXT NOT NULL DEFAULT 'csv',
            status TEXT NOT NULL,
            rows_seen INTEGER NOT NULL DEFAULT 0,
            orders_upserted INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            imported_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS affiliate_import_batches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,
            compatibility TEXT NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            import_mode TEXT NOT NULL DEFAULT 'DAILY_UPDATE',
            period_start TEXT,
            period_end TEXT,
            status TEXT NOT NULL,
            rows_total INTEGER NOT NULL DEFAULT 0,
            rows_new INTEGER NOT NULL DEFAULT 0,
            rows_updated INTEGER NOT NULL DEFAULT 0,
            rows_unchanged INTEGER NOT NULL DEFAULT 0,
            rows_failed INTEGER NOT NULL DEFAULT 0,
            orders_new INTEGER NOT NULL DEFAULT 0,
            orders_updated INTEGER NOT NULL DEFAULT 0,
            products_total INTEGER NOT NULL DEFAULT 0,
            products_matched INTEGER NOT NULL DEFAULT 0,
            products_unmatched INTEGER NOT NULL DEFAULT 0,
            skus_total INTEGER NOT NULL DEFAULT 0,
            contents_total INTEGER NOT NULL DEFAULT 0,
            contents_new INTEGER NOT NULL DEFAULT 0,
            contents_matched INTEGER NOT NULL DEFAULT 0,
            contents_unmatched INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS affiliate_import_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id INTEGER NOT NULL,
            row_number INTEGER,
            error_code TEXT NOT NULL,
            error_message TEXT NOT NULL,
            raw_payload TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(batch_id) REFERENCES affiliate_import_batches(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_external_references (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            provider TEXT NOT NULL,
            external_product_id TEXT,
            external_sku_id TEXT,
            external_product_name TEXT,
            shop_name TEXT,
            shop_code TEXT,
            match_method TEXT NOT NULL DEFAULT 'MANUAL',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            UNIQUE(provider, external_product_id, external_sku_id)
        );

        CREATE TABLE IF NOT EXISTS external_tiktok_contents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_content_id TEXT NOT NULL,
            external_content_type TEXT NOT NULL,
            content_id INTEGER,
            product_id INTEGER,
            external_product_id TEXT,
            external_product_name TEXT,
            status TEXT NOT NULL DEFAULT 'UNMATCHED',
            attribution_type TEXT NOT NULL DEFAULT 'UNATTRIBUTED',
            attribution_method TEXT,
            confidence TEXT NOT NULL DEFAULT '0',
            first_sale_at TEXT,
            last_sale_at TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(external_content_id, external_content_type),
            FOREIGN KEY(content_id) REFERENCES content_items(id) ON DELETE SET NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS product_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            normalized_alias TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'MANUAL',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(product_id, normalized_alias),
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS drive_scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_email TEXT,
            root_folder_id TEXT NOT NULL,
            root_folder_name TEXT,
            status TEXT NOT NULL,
            folders_seen INTEGER NOT NULL DEFAULT 0,
            folders_succeeded INTEGER NOT NULL DEFAULT 0,
            folders_failed INTEGER NOT NULL DEFAULT 0,
            files_seen INTEGER NOT NULL DEFAULT 0,
            videos_seen INTEGER NOT NULL DEFAULT 0,
            available_videos INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE TABLE IF NOT EXISTS creator_drive_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_account_email TEXT NOT NULL,
            drive_folder_id TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            status TEXT NOT NULL,
            is_operational INTEGER NOT NULL DEFAULT 0,
            discovered_at TEXT NOT NULL,
            last_scanned_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS drive_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_folder_id TEXT NOT NULL UNIQUE,
            parent_drive_folder_id TEXT,
            name TEXT NOT NULL,
            relative_path TEXT NOT NULL,
            depth INTEGER NOT NULL DEFAULT 0,
            folder_type TEXT NOT NULL DEFAULT 'UNKNOWN',
            mapping_status TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
            product_id INTEGER,
            mapping_source TEXT,
            mapping_confidence REAL,
            suggested_product_id INTEGER,
            suggestion_confidence REAL,
            suggestion_reason TEXT,
            direct_video_count INTEGER NOT NULL DEFAULT 0,
            direct_subfolder_count INTEGER NOT NULL DEFAULT 0,
            scan_status TEXT NOT NULL DEFAULT 'PENDING',
            scan_error TEXT,
            last_scan_id INTEGER,
            last_scanned_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL,
            FOREIGN KEY(suggested_product_id) REFERENCES products(id) ON DELETE SET NULL,
            FOREIGN KEY(last_scan_id) REFERENCES drive_scan_runs(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS drive_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_file_id TEXT NOT NULL UNIQUE,
            drive_folder_id TEXT NOT NULL,
            name TEXT NOT NULL,
            mime_type TEXT,
            size INTEGER,
            md5_checksum TEXT,
            created_time TEXT,
            modified_time TEXT,
            web_view_link TEXT,
            product_id INTEGER,
            content_id INTEGER,
            classification TEXT NOT NULL DEFAULT 'NEEDS_REVIEW',
            classification_reason TEXT,
            duplicate_reason TEXT,
            canonical_drive_file_id TEXT,
            last_scan_id INTEGER,
            last_seen_at TEXT NOT NULL,
            missing_since TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(drive_folder_id) REFERENCES drive_folders(drive_folder_id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL,
            FOREIGN KEY(content_id) REFERENCES content_items(id) ON DELETE SET NULL,
            FOREIGN KEY(last_scan_id) REFERENCES drive_scan_runs(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS product_prompts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            drive_folder_id TEXT NOT NULL,
            source_file_id TEXT NOT NULL UNIQUE,
            source_filename TEXT NOT NULL,
            title TEXT NOT NULL,
            prompt_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            source_missing INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_synced_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY(drive_folder_id) REFERENCES drive_folders(drive_folder_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_captions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            caption_text TEXT NOT NULL,
            hashtags TEXT,
            status TEXT NOT NULL DEFAULT 'DRAFT',
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS drive_folder_mapping_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            drive_folder_id TEXT NOT NULL,
            previous_product_id INTEGER,
            new_product_id INTEGER,
            previous_status TEXT,
            new_status TEXT NOT NULL,
            source TEXT NOT NULL,
            actor TEXT NOT NULL DEFAULT 'local-dashboard',
            reason TEXT,
            changed_at TEXT NOT NULL,
            FOREIGN KEY(previous_product_id) REFERENCES products(id) ON DELETE SET NULL,
            FOREIGN KEY(new_product_id) REFERENCES products(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS content_supply_alert_states (
            product_id INTEGER PRIMARY KEY,
            alert_state TEXT NOT NULL,
            severity TEXT NOT NULL,
            available_count INTEGER NOT NULL DEFAULT 0,
            momentum TEXT NOT NULL DEFAULT 'INSUFFICIENT_DATA',
            last_alerted_at TEXT,
            last_evaluated_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS content_supply_alert_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            available_count INTEGER NOT NULL DEFAULT 0,
            momentum TEXT NOT NULL DEFAULT 'INSUFFICIENT_DATA',
            score INTEGER NOT NULL DEFAULT 0,
            reasons TEXT NOT NULL DEFAULT '[]',
            data_confidence TEXT NOT NULL DEFAULT 'LOW',
            delivery_status TEXT NOT NULL DEFAULT 'PENDING',
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tiktok_analytics_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_key TEXT NOT NULL UNIQUE,
            source TEXT NOT NULL DEFAULT 'tiktok_studio_browser',
            trigger_type TEXT NOT NULL DEFAULT 'MANUAL',
            account_reference TEXT,
            status TEXT NOT NULL DEFAULT 'RUNNING',
            current_step TEXT,
            videos_discovered INTEGER NOT NULL DEFAULT 0,
            videos_expected INTEGER,
            videos_collected INTEGER NOT NULL DEFAULT 0,
            snapshots_created INTEGER NOT NULL DEFAULT 0,
            videos_failed INTEGER NOT NULL DEFAULT 0,
            videos_complete INTEGER NOT NULL DEFAULT 0,
            videos_partial INTEGER NOT NULL DEFAULT 0,
            videos_timeout INTEGER NOT NULL DEFAULT 0,
            retries_total INTEGER NOT NULL DEFAULT 0,
            average_load_ms REAL,
            p95_load_ms REAL,
            average_completeness REAL,
            new_videos_discovered INTEGER NOT NULL DEFAULT 0,
            collection_coverage REAL,
            coverage_confidence TEXT NOT NULL DEFAULT 'LOW',
            discovery_method TEXT,
            valid INTEGER NOT NULL DEFAULT 0,
            last_n_days INTEGER,
            full_backfill INTEGER NOT NULL DEFAULT 0,
            duration_seconds REAL,
            page_url TEXT,
            screenshot_path TEXT,
            debug_report_path TEXT,
            error_code TEXT,
            error_message TEXT,
            error_summary TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            started_at TEXT NOT NULL,
            finished_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tiktok_video_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_content_id TEXT NOT NULL,
            extraction_run_id INTEGER NOT NULL,
            captured_at TEXT NOT NULL,
            video_age_hours REAL,
            views REAL,
            impressions REAL,
            reach REAL,
            traffic_for_you REAL,
            traffic_following REAL,
            traffic_search REAL,
            traffic_profile REAL,
            traffic_other REAL,
            traffic_sound REAL,
            new_viewers REAL,
            returning_viewers REAL,
            new_viewer_rate REAL,
            returning_viewer_rate REAL,
            likes REAL,
            comments REAL,
            shares REAL,
            saves REAL,
            profile_visits REAL,
            followers_generated REAL,
            average_watch_time_seconds REAL,
            total_watch_time_seconds REAL,
            watched_full_rate REAL,
            completion_rate REAL,
            product_impressions REAL,
            product_clicks REAL,
            product_ctr REAL,
            add_to_cart REAL,
            orders REAL,
            items_sold REAL,
            gmv_minor INTEGER,
            estimated_commission_minor INTEGER,
            conversion_rate REAL,
            average_order_value_minor INTEGER,
            commission_rate REAL,
            shop_ads_orders REAL,
            shop_ads_gmv_minor INTEGER,
            organic_orders REAL,
            organic_gmv_minor INTEGER,
            source TEXT NOT NULL,
            page_url TEXT,
            raw_values_json TEXT NOT NULL DEFAULT '{}',
            metric_sources_json TEXT NOT NULL DEFAULT '{}',
            quality_status TEXT NOT NULL DEFAULT 'VALID',
            data_quality_status TEXT NOT NULL DEFAULT 'VALID',
            extraction_status TEXT NOT NULL DEFAULT 'COMPLETE',
            data_completeness_score REAL,
            load_duration_ms INTEGER,
            retry_count INTEGER NOT NULL DEFAULT 0,
            readiness_json TEXT NOT NULL DEFAULT '{}',
            anomaly_reason TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(external_content_id, extraction_run_id),
            FOREIGN KEY(extraction_run_id) REFERENCES tiktok_analytics_runs(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tiktok_video_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            external_content_id TEXT NOT NULL,
            snapshot_id INTEGER NOT NULL UNIQUE,
            cohort TEXT NOT NULL,
            views_delta_24h REAL,
            views_delta_48h REAL,
            orders_delta_24h REAL,
            gmv_delta_24h_minor INTEGER,
            commission_delta_24h_minor INTEGER,
            product_clicks_delta_24h REAL,
            views_per_hour REAL,
            orders_per_1000_views REAL,
            gmv_per_1000_views_minor INTEGER,
            commission_per_1000_views_minor INTEGER,
            product_ctr REAL,
            click_to_order_cvr REAL,
            engagement_rate REAL,
            share_rate REAL,
            comment_rate REAL,
            follow_rate REAL,
            paid_gmv_share REAL,
            organic_gmv_share REAL,
            account_percentile REAL,
            product_percentile REAL,
            winner INTEGER NOT NULL DEFAULT 0,
            early_winner INTEGER NOT NULL DEFAULT 0,
            decay_score REAL,
            decay_status TEXT NOT NULL DEFAULT 'INSUFFICIENT_DATA',
            investment_score REAL,
            confidence_score REAL NOT NULL DEFAULT 0,
            confidence_level TEXT NOT NULL DEFAULT 'LOW',
            recommendation TEXT NOT NULL DEFAULT 'OBSERVAR',
            reasons_json TEXT NOT NULL DEFAULT '[]',
            component_scores_json TEXT NOT NULL DEFAULT '{}',
            computed_at TEXT NOT NULL,
            FOREIGN KEY(snapshot_id) REFERENCES tiktok_video_snapshots(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tiktok_account_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            computed_at TEXT NOT NULL,
            period_end TEXT NOT NULL,
            distribution_health_score REAL,
            diagnosis TEXT NOT NULL DEFAULT 'INSUFFICIENT_DATA',
            health_state TEXT NOT NULL DEFAULT 'INSUFFICIENT_HISTORY',
            median_views_24h REAL,
            median_views_48h REAL,
            previous_median_views_24h REAL,
            previous_median_views_48h REAL,
            gmv_per_1000_views_minor INTEGER,
            previous_gmv_per_1000_views_minor INTEGER,
            orders_per_1000_views REAL,
            previous_orders_per_1000_views REAL,
            new_winners_created INTEGER NOT NULL DEFAULT 0,
            old_winners_lost INTEGER NOT NULL DEFAULT 0,
            replacement_ratio REAL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(period_end)
        );

        CREATE TABLE IF NOT EXISTS tiktok_creative_tags (
            external_content_id TEXT PRIMARY KEY,
            creative_family TEXT,
            hook_family TEXT,
            body_family TEXT,
            cta_family TEXT,
            actor_type TEXT,
            scenario TEXT,
            production_method TEXT,
            ai_generated INTEGER,
            ai_tool TEXT,
            gera_viral INTEGER,
            creative_similarity_cluster TEXT,
            saturation_score REAL,
            classification_source TEXT NOT NULL DEFAULT 'MANUAL',
            confidence REAL,
            manually_confirmed INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS tiktok_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            hypothesis TEXT,
            product_id INTEGER,
            status TEXT NOT NULL DEFAULT 'DRAFT',
            starts_at TEXT,
            ends_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
        );

        CREATE TABLE IF NOT EXISTS tiktok_experiment_videos (
            experiment_id INTEGER NOT NULL,
            external_content_id TEXT NOT NULL,
            variant TEXT NOT NULL,
            published_at TEXT,
            notes TEXT,
            created_at TEXT NOT NULL,
            PRIMARY KEY(experiment_id, external_content_id),
            FOREIGN KEY(experiment_id) REFERENCES tiktok_experiments(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS tiktok_analytics_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_key TEXT NOT NULL UNIQUE,
            alert_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            external_content_id TEXT,
            product_id INTEGER,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'OPEN',
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_affiliate_orders_created
            ON affiliate_orders(order_created_at);
        CREATE INDEX IF NOT EXISTS idx_affiliate_orders_product
            ON affiliate_orders(product_id);
        CREATE INDEX IF NOT EXISTS idx_affiliate_order_items_product
            ON affiliate_order_items(product_id);
        CREATE INDEX IF NOT EXISTS idx_affiliate_order_items_content
            ON affiliate_order_items(content_id);
        CREATE INDEX IF NOT EXISTS idx_oauth_states_expiry
            ON oauth_states(expires_at);
        CREATE INDEX IF NOT EXISTS idx_affiliate_report_imports_status
            ON affiliate_report_imports(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_affiliate_import_batches_hash
            ON affiliate_import_batches(file_hash, status);
        CREATE INDEX IF NOT EXISTS idx_product_external_reference_product
            ON product_external_references(product_id);
        CREATE INDEX IF NOT EXISTS idx_external_tiktok_content_status
            ON external_tiktok_contents(status, external_content_type);
        CREATE INDEX IF NOT EXISTS idx_product_alias_normalized
            ON product_aliases(normalized_alias);
        CREATE INDEX IF NOT EXISTS idx_drive_folders_product
            ON drive_folders(product_id, mapping_status);
        CREATE INDEX IF NOT EXISTS idx_drive_folders_parent
            ON drive_folders(parent_drive_folder_id);
        CREATE INDEX IF NOT EXISTS idx_drive_files_folder
            ON drive_files(drive_folder_id);
        CREATE INDEX IF NOT EXISTS idx_drive_files_product_classification
            ON drive_files(product_id, classification);
        CREATE INDEX IF NOT EXISTS idx_drive_files_scan
            ON drive_files(last_scan_id, missing_since);
        CREATE INDEX IF NOT EXISTS idx_drive_alert_events_status
            ON content_supply_alert_events(delivery_status, created_at);
        CREATE INDEX IF NOT EXISTS idx_creator_drive_profiles_status
            ON creator_drive_profiles(status, is_operational);
        CREATE INDEX IF NOT EXISTS idx_product_prompts_product
            ON product_prompts(product_id, status, source_missing);
        CREATE INDEX IF NOT EXISTS idx_product_captions_product
            ON product_captions(product_id, status);
        CREATE INDEX IF NOT EXISTS idx_tiktok_snapshots_content_time
            ON tiktok_video_snapshots(external_content_id, captured_at);
        CREATE INDEX IF NOT EXISTS idx_tiktok_snapshots_run
            ON tiktok_video_snapshots(extraction_run_id, quality_status);
        CREATE INDEX IF NOT EXISTS idx_tiktok_scores_recommendation
            ON tiktok_video_scores(recommendation, investment_score);
        CREATE INDEX IF NOT EXISTS idx_tiktok_scores_content
            ON tiktok_video_scores(external_content_id, computed_at);
        CREATE INDEX IF NOT EXISTS idx_tiktok_alerts_status
            ON tiktok_analytics_alerts(status, created_at);
        """
    )
    _migrate_products(conn)
    _migrate_content_performance(conn)
    _migrate_affiliate_orders(conn)
    _migrate_affiliate_order_items(conn)
    _migrate_drive_inventory(conn)
    _migrate_content_items(conn)
    _migrate_external_tiktok_contents(conn)
    _migrate_tiktok_analytics(conn)
    conn.commit()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, name: str, definition: str) -> None:
    if name not in _column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _migrate_products(conn: sqlite3.Connection) -> None:
    _add_column(conn, "products", "sku_internal_code", "TEXT")
    _add_column(conn, "products", "tiktok_product_name", "TEXT")
    _add_column(conn, "products", "tiktok_product_id", "TEXT")
    _add_column(conn, "products", "tiktok_sku_id", "TEXT")
    _add_column(conn, "products", "tiktok_product_url", "TEXT")
    _add_column(conn, "products", "default_caption", "TEXT")
    _add_column(conn, "products", "default_hashtags", "TEXT")


def _migrate_content_performance(conn: sqlite3.Connection) -> None:
    _add_column(conn, "content_performance", "provider", "TEXT NOT NULL DEFAULT 'manual'")
    _add_column(conn, "content_performance", "source_timestamp", "TEXT")
    _add_column(conn, "content_performance", "synced_at", "TEXT")
    _add_column(conn, "content_performance", "estimated_commission", "REAL")
    _add_column(conn, "content_performance", "actual_commission", "REAL")
    _add_column(conn, "content_performance", "refunds", "REAL NOT NULL DEFAULT 0")
    _add_column(conn, "content_performance", "attribution_status", "TEXT NOT NULL DEFAULT 'UNAVAILABLE'")
    _add_column(conn, "content_performance", "gmv_minor", "INTEGER")
    _add_column(conn, "content_performance", "estimated_commission_minor", "INTEGER")
    _add_column(conn, "content_performance", "actual_commission_minor", "INTEGER")
    _add_column(conn, "content_performance", "final_received_minor", "INTEGER")
    _add_column(conn, "content_performance", "refunds_minor", "INTEGER")


def _migrate_affiliate_orders(conn: sqlite3.Connection) -> None:
    columns = {
        "external_order_type": "TEXT",
        "normalized_order_type": "TEXT NOT NULL DEFAULT 'OTHER'",
        "external_settlement_status": "TEXT",
        "settlement_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "commission_settled_at": "TEXT",
        "indirect": "TEXT",
        "commission_type": "TEXT",
        "shop_name": "TEXT",
        "shop_code": "TEXT",
        "partner_agency": "TEXT",
        "agency": "TEXT",
        "gmv_minor": "INTEGER",
        "estimated_commission_minor": "INTEGER",
        "actual_commission_minor": "INTEGER",
        "final_received_minor": "INTEGER",
        "row_fingerprint": "TEXT",
        "last_import_batch_id": "INTEGER",
    }
    for name, definition in columns.items():
        _add_column(conn, "affiliate_orders", name, definition)


def _migrate_affiliate_order_items(conn: sqlite3.Connection) -> None:
    columns = {
        "items_sold": "INTEGER NOT NULL DEFAULT 0",
        "items_refunded": "INTEGER NOT NULL DEFAULT 0",
        "normalized_content_type": "TEXT NOT NULL DEFAULT 'UNKNOWN'",
        "product_match_method": "TEXT",
        "attribution_type": "TEXT NOT NULL DEFAULT 'UNATTRIBUTED'",
        "attribution_method": "TEXT",
        "attribution_confidence": "TEXT NOT NULL DEFAULT '0'",
        "unit_price_minor": "INTEGER",
        "gmv_minor": "INTEGER",
        "standard_rate": "TEXT",
        "shop_ads_rate": "TEXT",
        "tiktok_bonus_rate": "TEXT",
        "partner_bonus_rate": "TEXT",
        "revenue_share_rate": "TEXT",
        "estimated_commission_base_minor": "INTEGER",
        "estimated_standard_commission_minor": "INTEGER",
        "estimated_shop_ads_commission_minor": "INTEGER",
        "estimated_tiktok_bonus_minor": "INTEGER",
        "estimated_partner_bonus_minor": "INTEGER",
        "estimated_partner_agency_bonus_minor": "INTEGER",
        "estimated_revenue_share_minor": "INTEGER",
        "estimated_tax_vat_minor": "INTEGER",
        "estimated_tax_isr_minor": "INTEGER",
        "estimated_tax_cedular_minor": "INTEGER",
        "estimated_tax_pit_minor": "INTEGER",
        "actual_commission_base_minor": "INTEGER",
        "actual_standard_commission_minor": "INTEGER",
        "actual_shop_ads_commission_minor": "INTEGER",
        "actual_bonus_minor": "INTEGER",
        "actual_partner_agency_bonus_minor": "INTEGER",
        "actual_tax_isr_minor": "INTEGER",
        "actual_tax_vat_minor": "INTEGER",
        "actual_tax_cedular_minor": "INTEGER",
        "actual_tax_pit_minor": "INTEGER",
        "actual_partner_share_minor": "INTEGER",
        "estimated_commission_minor": "INTEGER",
        "actual_commission_minor": "INTEGER",
        "final_received_minor": "INTEGER",
        "row_fingerprint": "TEXT",
    }
    for name, definition in columns.items():
        _add_column(conn, "affiliate_order_items", name, definition)


def _migrate_drive_inventory(conn: sqlite3.Connection) -> None:
    folder_columns = {
        "creator_profile_id": "INTEGER",
        "operational_status": "TEXT NOT NULL DEFAULT 'ACTIVE'",
        "direct_prompt_count": "INTEGER NOT NULL DEFAULT 0",
        "direct_other_file_count": "INTEGER NOT NULL DEFAULT 0",
        "role_source": "TEXT NOT NULL DEFAULT 'HEURISTIC'",
        "role_confidence": "TEXT",
        "role_reason": "TEXT",
        "role_review_status": "TEXT NOT NULL DEFAULT 'PENDING'",
        "role_decided_at": "TEXT",
        "role_review_metadata": "TEXT NOT NULL DEFAULT '{}'",
        "auto_create_new_direct_children": "INTEGER NOT NULL DEFAULT 0",
    }
    for name, definition in folder_columns.items():
        _add_column(conn, "drive_folders", name, definition)
    file_columns = {
        "file_kind": "TEXT NOT NULL DEFAULT 'OTHER'",
        "thumbnail_link": "TEXT",
        "text_content": "TEXT",
        "text_content_hash": "TEXT",
        "text_read_error": "TEXT",
    }
    for name, definition in file_columns.items():
        _add_column(conn, "drive_files", name, definition)
    _add_column(conn, "drive_scan_runs", "operational_root_folder_id", "TEXT")
    _add_column(conn, "drive_scan_runs", "operational_root_folder_name", "TEXT")


def _migrate_content_items(conn: sqlite3.Connection) -> None:
    _add_column(conn, "content_items", "caption_id", "INTEGER")


def _migrate_external_tiktok_contents(conn: sqlite3.Connection) -> None:
    columns = {
        "video_url": "TEXT",
        "published_at": "TEXT",
        "caption": "TEXT",
        "duration_seconds": "REAL",
        "thumbnail_url": "TEXT",
        "platform_status": "TEXT",
        "seller_name": "TEXT",
        "catalog_source": "TEXT",
        "discovered_at": "TEXT",
        "last_collected_at": "TEXT",
        "metadata_json": "TEXT NOT NULL DEFAULT '{}'",
    }
    for name, definition in columns.items():
        _add_column(conn, "external_tiktok_contents", name, definition)


def _migrate_tiktok_analytics(conn: sqlite3.Connection) -> None:
    run_columns = {
        "videos_expected": "INTEGER",
        "new_videos_discovered": "INTEGER NOT NULL DEFAULT 0",
        "collection_coverage": "REAL",
        "coverage_confidence": "TEXT NOT NULL DEFAULT 'LOW'",
        "discovery_method": "TEXT",
        "valid": "INTEGER NOT NULL DEFAULT 0",
        "last_n_days": "INTEGER",
        "full_backfill": "INTEGER NOT NULL DEFAULT 0",
        "duration_seconds": "REAL",
        "debug_report_path": "TEXT",
        "error_summary": "TEXT",
        "videos_complete": "INTEGER NOT NULL DEFAULT 0",
        "videos_partial": "INTEGER NOT NULL DEFAULT 0",
        "videos_timeout": "INTEGER NOT NULL DEFAULT 0",
        "retries_total": "INTEGER NOT NULL DEFAULT 0",
        "average_load_ms": "REAL",
        "p95_load_ms": "REAL",
        "average_completeness": "REAL",
    }
    for name, definition in run_columns.items():
        _add_column(conn, "tiktok_analytics_runs", name, definition)
    _add_column(conn, "tiktok_video_snapshots", "new_viewer_rate", "REAL")
    _add_column(conn, "tiktok_video_snapshots", "returning_viewer_rate", "REAL")
    _add_column(conn, "tiktok_video_snapshots", "traffic_other", "REAL")
    _add_column(conn, "tiktok_video_snapshots", "traffic_sound", "REAL")
    _add_column(conn, "tiktok_video_snapshots", "data_quality_status", "TEXT NOT NULL DEFAULT 'VALID'")
    _add_column(conn, "tiktok_video_snapshots", "extraction_status", "TEXT NOT NULL DEFAULT 'COMPLETE'")
    _add_column(conn, "tiktok_video_snapshots", "data_completeness_score", "REAL")
    _add_column(conn, "tiktok_video_snapshots", "load_duration_ms", "INTEGER")
    _add_column(conn, "tiktok_video_snapshots", "retry_count", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "tiktok_video_snapshots", "readiness_json", "TEXT NOT NULL DEFAULT '{}'")
    _add_column(conn, "tiktok_account_health", "health_state", "TEXT NOT NULL DEFAULT 'INSUFFICIENT_HISTORY'")


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def product_key(name: str) -> str:
    normalized = " ".join(name.lower().strip().split())
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:16]


def stable_hash(parts: list[Any]) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def upsert_product(
    conn: sqlite3.Connection,
    name: str,
    category: str | None = None,
    source_url: str | None = None,
    price: float | None = None,
    commission_rate: float | None = None,
    shop_name: str | None = None,
) -> int:
    timestamp = now_iso()
    key = product_key(name)
    conn.execute(
        """
        INSERT INTO products (
            product_key, name, category, price, commission_rate, shop_name,
            source_url, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(product_key) DO UPDATE SET
            category = COALESCE(excluded.category, products.category),
            price = COALESCE(excluded.price, products.price),
            commission_rate = COALESCE(excluded.commission_rate, products.commission_rate),
            shop_name = COALESCE(excluded.shop_name, products.shop_name),
            source_url = COALESCE(excluded.source_url, products.source_url),
            updated_at = excluded.updated_at
        """,
        (key, name, category, price, commission_rate, shop_name, source_url, timestamp, timestamp),
    )
    row = conn.execute("SELECT id FROM products WHERE product_key = ?", (key,)).fetchone()
    conn.commit()
    return int(row["id"])


def create_snapshot(
    conn: sqlite3.Connection,
    product_id: int,
    source: str,
    date_range: str,
    video_records: list[dict[str, Any]],
    creator_records: list[dict[str, Any]],
    raw_meta: dict[str, Any] | None = None,
) -> int:
    revenue_video = sum(float(row["revenue"]) for row in video_records)
    video_sales = sum(float(row["sales"]) for row in video_records)
    video_views = sum(float(row["views"]) for row in video_records)
    ad_cost = sum(float(row["ad_cost"]) for row in video_records)

    revenue_total = sum(float(row["revenue"]) for row in creator_records) or revenue_video
    sales_total = sum(float(row["sales"]) for row in creator_records) or video_sales
    creator_views = sum(float(row["views"]) for row in creator_records)
    revenue_live = sum(float(row["live_gmv"]) for row in creator_records)

    avg_price = revenue_total / sales_total if sales_total else 0
    gpm_video = revenue_video / video_views * 1000 if video_views else 0
    cpa_ads = ad_cost / video_sales if video_sales else 0
    roas = revenue_video / ad_cost if ad_cost else 0
    timestamp = now_iso()

    cursor = conn.execute(
        """
        INSERT INTO product_snapshots (
            product_id, source, date_range, collected_at, revenue_total,
            revenue_video, revenue_live, sales_total, video_count, creator_count,
            views_video_export, views_creator_export, ad_cost, avg_price,
            gpm_video_export, cpa_ads, roas, raw_meta
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            product_id,
            source,
            date_range,
            timestamp,
            revenue_total,
            revenue_video,
            revenue_live,
            sales_total,
            len(video_records),
            len(creator_records),
            video_views,
            creator_views,
            ad_cost,
            avg_price,
            gpm_video,
            cpa_ads,
            roas,
            json.dumps(raw_meta or {}, ensure_ascii=False),
        ),
    )
    snapshot_id = int(cursor.lastrowid)

    _insert_videos(conn, product_id, snapshot_id, video_records)
    _insert_creators(conn, product_id, snapshot_id, creator_records)
    conn.commit()
    return snapshot_id


def ensure_two_versions(conn: sqlite3.Connection, product_id: int) -> None:
    timestamp = now_iso()
    for version in (1, 2):
        conn.execute(
            """
            INSERT INTO production_tasks (
                product_id, version_number, status, created_at, updated_at
            )
            VALUES (?, ?, 'planejado', ?, ?)
            ON CONFLICT(product_id, version_number) DO NOTHING
            """,
            (product_id, version, timestamp, timestamp),
        )
    conn.commit()


def _insert_videos(
    conn: sqlite3.Connection,
    product_id: int,
    snapshot_id: int,
    records: list[dict[str, Any]],
) -> None:
    for row in records:
        row_hash = stable_hash(
            [product_id, row.get("creator_username"), row.get("published_at"), row.get("tiktok_url")]
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO video_metrics (
                product_id, snapshot_id, creator_username, published_at,
                description, duration, revenue, sales, views, gpm, cpa,
                ads_view_ratio, ad_cost, roas, kalodata_url, tiktok_url, row_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                snapshot_id,
                row.get("creator_username"),
                row.get("published_at"),
                row.get("description"),
                row.get("duration"),
                row.get("revenue"),
                row.get("sales"),
                row.get("views"),
                row.get("gpm"),
                row.get("cpa"),
                row.get("ads_view_ratio"),
                row.get("ad_cost"),
                row.get("roas"),
                row.get("kalodata_url"),
                row.get("tiktok_url"),
                row_hash,
            ),
        )


def _insert_creators(
    conn: sqlite3.Connection,
    product_id: int,
    snapshot_id: int,
    records: list[dict[str, Any]],
) -> None:
    for row in records:
        row_hash = stable_hash(
            [product_id, row.get("creator_username"), row.get("kalodata_url"), row.get("revenue")]
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO creator_metrics (
                product_id, snapshot_id, creator_name, creator_username,
                followers, revenue, sales, avg_price, engagement_rate,
                new_followers, product_count, live_count, live_gmv,
                video_count, video_gmv, views, creator_first_seen,
                kalodata_url, tiktok_url, row_hash
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                product_id,
                snapshot_id,
                row.get("creator_name"),
                row.get("creator_username"),
                row.get("followers"),
                row.get("revenue"),
                row.get("sales"),
                row.get("avg_price"),
                row.get("engagement_rate"),
                row.get("new_followers"),
                row.get("product_count"),
                row.get("live_count"),
                row.get("live_gmv"),
                row.get("video_count"),
                row.get("video_gmv"),
                row.get("views"),
                row.get("creator_first_seen"),
                row.get("kalodata_url"),
                row.get("tiktok_url"),
                row_hash,
            ),
        )
