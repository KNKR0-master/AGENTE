from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from src.store import connect, init_db, now_iso
from src.tiktok_analytics import (
    add_experiment_video,
    begin_extraction_run,
    calculate_distribution_health,
    cohort_for_age,
    create_experiment,
    finish_extraction_run,
    parse_currency_minor,
    parse_duration_seconds,
    parse_localized_number,
    parse_percentage,
    parse_tiktok_datetime,
    percentile_rank,
    persist_video_snapshot,
    recompute_tiktok_analytics,
    save_creative_tags,
    tiktok_analytics_dashboard,
    upsert_tiktok_video,
)
from src.tiktok_studio_collector import (
    AuthenticationRequired,
    InteractionRequired,
    TikTokStudioBrowserCollector,
    TikTokStudioCollectorConfig,
    UiChanged,
    _extract_network_videos,
    _video_id_from_url,
)


class TikTokAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def finish_valid_run(self, run, videos: int = 1, coverage: float = 1.0) -> None:
        finish_extraction_run(
            self.conn,
            run["id"],
            "SUCCESS" if coverage >= 0.9 else "PARTIAL_SUCCESS",
            counts={"videos_discovered": videos, "videos_collected": videos, "snapshots_created": videos},
            coverage={"videos_expected": videos, "collection_coverage": coverage, "coverage_confidence": "HIGH"},
            discovery_method="TEST",
            valid=coverage >= 0.7,
        )

    def test_parses_br_en_numbers_currency_and_dates(self) -> None:
        self.assertEqual(parse_localized_number("1.234,56", "pt-BR"), Decimal("1234.56"))
        self.assertEqual(parse_localized_number("1,2 mil", "pt-BR"), Decimal("1200.0"))
        self.assertEqual(parse_localized_number("1,234.56", "en-US"), Decimal("1234.56"))
        self.assertEqual(parse_localized_number("1.2K", "en-US"), Decimal("1200.0"))
        self.assertEqual(parse_currency_minor("R$ 1.234,56", "pt-BR"), 123456)
        self.assertEqual(parse_currency_minor("$1,234.56", "en-US"), 123456)
        self.assertIsNone(parse_percentage("<0,1%", "pt-BR"))
        self.assertEqual(parse_tiktok_datetime("16/08/2026 07:30", "pt-BR"), "2026-08-16T10:30:00+00:00")
        self.assertEqual(parse_tiktok_datetime("15 de ago, 23:39", "pt-BR"), "2026-08-16T02:39:00+00:00")
        self.assertEqual(parse_duration_seconds("0h:04m:28s"), 268.0)
        self.assertEqual(parse_duration_seconds("00:22"), 22.0)
        self.assertEqual(parse_duration_seconds("5.47s"), 5.47)
        self.assertEqual(parse_duration_seconds("4m:06s"), 246.0)

    def test_cohorts_and_percentiles(self) -> None:
        self.assertEqual(cohort_for_age(1), "0-2h")
        self.assertEqual(cohort_for_age(30), "24-48h")
        self.assertEqual(cohort_for_age(800), ">30d")
        self.assertEqual(percentile_rank(20, [10, 20, 30]), 50.0)

    def test_snapshot_is_immutable_idempotent_and_preserves_null(self) -> None:
        upsert_tiktok_video(
            self.conn,
            {"video_id": "video-1", "published_at": "15/08/2026 07:00", "product_name": "Produto"},
        )
        run = begin_extraction_run(self.conn, "run-1")
        first = persist_video_snapshot(
            self.conn,
            run["id"],
            "video-1",
            {"views": 1000, "orders": 4, "gmv_minor": 20000, "product_clicks": None},
            captured_at="16/08/2026 07:00",
            raw_values={"views": "1.000", "product_clicks": "--"},
        )
        second = persist_video_snapshot(
            self.conn,
            run["id"],
            "video-1",
            {"views": 9999},
            captured_at="16/08/2026 08:00",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["views"], 1000)
        self.assertIsNone(second["product_clicks"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM tiktok_video_snapshots").fetchone()[0], 1)

    def test_scheduled_run_reuses_successful_forced_run_in_same_scope(self) -> None:
        run_key = "tiktok-studio-daily:2026-08-17:30d"
        forced = begin_extraction_run(self.conn, run_key, trigger_type="MANUAL", force=True)
        self.finish_valid_run(forced)

        scheduled = begin_extraction_run(self.conn, run_key, trigger_type="SCHEDULED")

        self.assertEqual(scheduled["id"], forced["id"])
        self.assertEqual(scheduled["status"], "SUCCESS")
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM tiktok_analytics_runs").fetchone()[0],
            1,
        )

    def test_large_cumulative_drop_is_marked_suspect(self) -> None:
        upsert_tiktok_video(self.conn, {"video_id": "video-drop"})
        first_run = begin_extraction_run(self.conn, "drop-1")
        second_run = begin_extraction_run(self.conn, "drop-2")
        persist_video_snapshot(self.conn, first_run["id"], "video-drop", {"views": 100000})
        suspect = persist_video_snapshot(self.conn, second_run["id"], "video-drop", {"views": 1000})
        self.assertEqual(suspect["quality_status"], "SUSPICIOUS")
        self.assertIn("views caiu", suspect["anomaly_reason"])

    def test_deltas_and_decay_use_historical_snapshots(self) -> None:
        upsert_tiktok_video(
            self.conn,
            {"video_id": "video-decay", "published_at": "2026-08-10T10:00:00+00:00"},
        )
        snapshots = [
            ("decay-1", "2026-08-11T10:00:00+00:00", 1000, 10000, 2),
            ("decay-2", "2026-08-12T10:00:00+00:00", 5000, 50000, 10),
            ("decay-3", "2026-08-13T10:00:00+00:00", 5500, 55000, 11),
        ]
        for key, captured, views, gmv, orders in snapshots:
            run = begin_extraction_run(self.conn, key)
            persist_video_snapshot(
                self.conn,
                run["id"],
                "video-decay",
                {"views": views, "gmv_minor": gmv, "orders": orders, "estimated_commission_minor": gmv // 10},
                captured_at=captured,
            )
            self.finish_valid_run(run)
        recompute_tiktok_analytics(self.conn, computed_at="2026-08-13T11:00:00+00:00")
        score = self.conn.execute("SELECT * FROM tiktok_video_scores ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(score["views_delta_24h"], 500)
        self.assertEqual(score["gmv_delta_24h_minor"], 5000)
        self.assertEqual(score["decay_status"], "DECAYING")
        self.assertGreater(score["decay_score"], 80)

    def test_distribution_drop_requires_distribution_and_stable_efficiency(self) -> None:
        now = datetime(2026, 8, 16, 12, tzinfo=timezone.utc)
        for group, offsets, views in (("current", (1, 2, 3), 1000), ("previous", (8, 9, 10), 2000)):
            for index, days in enumerate(offsets):
                published = now - timedelta(days=days, hours=24)
                video_id = f"{group}-{index}"
                upsert_tiktok_video(self.conn, {"video_id": video_id, "published_at": published.isoformat()})
                run = begin_extraction_run(self.conn, f"distribution-{video_id}")
                persist_video_snapshot(
                    self.conn,
                    run["id"],
                    video_id,
                    {"views": views, "gmv_minor": views * 10, "orders": views / 100},
                    captured_at=(published + timedelta(hours=24)).isoformat(),
                )
                self.finish_valid_run(run)
        recompute_tiktok_analytics(self.conn, computed_at=now.isoformat())
        health = calculate_distribution_health(self.conn, computed_at=now.isoformat())
        self.assertEqual(health["diagnosis"], "DISTRIBUTION_DROP")
        self.assertEqual(health["distribution_health_score"], 50.0)

    def test_score_is_explainable_and_creative_metadata_is_editable(self) -> None:
        published = "2026-08-15T12:00:00+00:00"
        for index, views in enumerate((100, 500, 1000, 5000, 10000), start=1):
            video_id = f"score-{index}"
            upsert_tiktok_video(self.conn, {"video_id": video_id, "published_at": published})
            run = begin_extraction_run(self.conn, f"score-run-{index}")
            persist_video_snapshot(
                self.conn,
                run["id"],
                video_id,
                {
                    "views": views,
                    "orders": index * 3,
                    "gmv_minor": index * 25000,
                    "estimated_commission_minor": index * 2500,
                    "average_watch_time_seconds": index,
                    "watched_full_rate": index * 3,
                    "likes": views / 10,
                    "shares": views / 100,
                },
                captured_at="2026-08-16T12:00:00+00:00",
            )
            self.finish_valid_run(run)
        recompute_tiktok_analytics(self.conn, computed_at="2026-08-16T12:05:00+00:00")
        best = self.conn.execute(
            "SELECT * FROM tiktok_video_scores WHERE external_content_id = 'score-5'"
        ).fetchone()
        self.assertIsNotNone(best["investment_score"])
        self.assertIn(best["confidence_level"], {"LOW", "MEDIUM", "HIGH"})
        self.assertTrue(best["reasons_json"])

        save_creative_tags(
            self.conn,
            "score-5",
            {
                "creative_family": "demonstracao",
                "hook_family": "dor-promessa",
                "ai_generated": False,
                "gera_viral": True,
                "classification_source": "MANUAL",
                "confidence": 1,
                "manually_confirmed": True,
            },
        )
        tag = self.conn.execute(
            "SELECT * FROM tiktok_creative_tags WHERE external_content_id = 'score-5'"
        ).fetchone()
        self.assertEqual(tag["creative_family"], "demonstracao")
        self.assertEqual(tag["manually_confirmed"], 1)

        experiment_id = create_experiment(
            self.conn, {"name": "Hook A/B", "hypothesis": "Hook altera distribuicao"}
        )
        add_experiment_video(
            self.conn,
            experiment_id,
            {"video_id": "score-5", "variant": "B", "published_at": published},
        )
        participant = self.conn.execute(
            "SELECT * FROM tiktok_experiment_videos WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        self.assertEqual(participant["variant"], "B")

    def test_critical_latest_run_blocks_rankings_and_incomplete_7d_kpis(self) -> None:
        upsert_tiktok_video(
            self.conn,
            {"video_id": "7659880710307597576", "published_at": datetime.now(timezone.utc).isoformat()},
        )
        valid = begin_extraction_run(self.conn, "valid")
        persist_video_snapshot(self.conn, valid["id"], "7659880710307597576", {"views": 18421})
        self.finish_valid_run(valid)
        recompute_tiktok_analytics(self.conn)
        critical = begin_extraction_run(self.conn, "critical")
        finish_extraction_run(
            self.conn,
            critical["id"],
            "COVERAGE_CRITICAL",
            counts={"videos_discovered": 4, "videos_collected": 4},
            coverage={"videos_expected": 280, "collection_coverage": 4 / 280, "coverage_confidence": "HIGH"},
            discovery_method="VIDEO_LINKS",
            valid=False,
        )
        dashboard = tiktok_analytics_dashboard(self.conn)
        self.assertFalse(dashboard["collection"]["analytics_allowed"])
        self.assertTrue(dashboard["investment"]["blocked"])
        self.assertIsNone(dashboard["summary"]["published_7d"])
        self.assertIsNone(dashboard["summary"]["views_7d"])
        self.assertEqual(dashboard["investment"]["scale_now"], [])

    def test_views_7d_is_sum_of_views_not_viewers(self) -> None:
        run = begin_extraction_run(self.conn, "views-7d")
        for index, views in enumerate((100, 250), start=1):
            video_id = f"76744535136650232{index}"
            upsert_tiktok_video(
                self.conn,
                {"video_id": video_id, "published_at": datetime.now(timezone.utc).isoformat()},
            )
            persist_video_snapshot(
                self.conn, run["id"], video_id,
                {"views": views, "reach": 9999, "new_viewer_rate": 90},
            )
        self.finish_valid_run(run, videos=2)
        recompute_tiktok_analytics(self.conn)
        dashboard = tiktok_analytics_dashboard(self.conn)
        self.assertEqual(dashboard["summary"]["views_7d"], 350)
        self.assertNotEqual(dashboard["summary"]["views_7d"], dashboard["summary"]["new_viewers_estimated"])

    def test_decay_without_three_valid_snapshots_is_no_history(self) -> None:
        upsert_tiktok_video(self.conn, {"video_id": "short-history", "published_at": "2026-08-16T10:00:00+00:00"})
        run = begin_extraction_run(self.conn, "short-history-run")
        persist_video_snapshot(self.conn, run["id"], "short-history", {"views": 500})
        self.finish_valid_run(run)
        recompute_tiktok_analytics(self.conn)
        score = self.conn.execute("SELECT decay_score, decay_status FROM tiktok_video_scores").fetchone()
        self.assertIsNone(score["decay_score"])
        self.assertEqual(score["decay_status"], "NO_HISTORY")


class TikTokCollectorStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        root = Path(self.folder.name)
        self.config = TikTokStudioCollectorConfig(
            start_url="https://www.tiktok.com/tiktokstudio/analytics",
            profile_dir=root / "profile",
            mapping_file=root / "missing.json",
            artifacts_dir=root / "artifacts",
            headless=False,
            timeout_seconds=10,
            max_retries=1,
            locale="pt-BR",
            account_reference=None,
        )
        self.collector = TikTokStudioBrowserCollector(self.config)

    def test_expired_session_is_detected(self) -> None:
        page = FakePage("https://www.tiktok.com/login", "Entrar no TikTok")
        with self.assertRaises(AuthenticationRequired):
            self.collector._check_page_state(page)

    def test_captcha_requires_manual_interaction(self) -> None:
        page = FakePage("https://www.tiktok.com/tiktokstudio/content", "Verify you are human CAPTCHA")
        with self.assertRaises(InteractionRequired):
            self.collector._check_page_state(page)

    def test_network_parser_extracts_video_context_and_ignores_order_ids(self) -> None:
        target = {}
        _extract_network_videos(
            {
                "orders": [{"id": "7674453513665000000", "amount": 99}],
                "item_list": [{
                    "item_id": "7674453513665023250",
                    "create_time": 1786900000,
                    "desc": "Teste",
                    "stats": {"play_count": 18421, "digg_count": 620},
                }],
            },
            target,
            path="response",
        )
        self.assertEqual(set(target), {"7674453513665023250"})
        self.assertEqual(target["7674453513665023250"]["views"], 18421)
        self.assertEqual(target["7674453513665023250"]["likes"], 620)

    def test_video_id_is_confirmed_on_studio_analytics_route(self) -> None:
        self.assertEqual(
            _video_id_from_url("https://www.tiktok.com/tiktokstudio/analytics/7674453719018245384/overview"),
            "7674453719018245384",
        )

    def test_missing_authenticated_mapping_is_ui_changed(self) -> None:
        with self.assertRaises(UiChanged):
            self.collector._load_mapping()

    def test_missing_metric_in_ui_is_preserved_as_null(self) -> None:
        original = self.collector._extract_field
        self.collector._extract_field = lambda _page, _spec: (_ for _ in ()).throw(RuntimeError("missing"))
        try:
            values, raw, sources = self.collector._extract_metrics(
                object(), {"product_clicks": {"selector": "[data-missing]", "type": "number"}}
            )
        finally:
            self.collector._extract_field = original
        self.assertIsNone(values["product_clicks"])
        self.assertIsNone(raw["product_clicks"])
        self.assertEqual(sources, {})


class FakePage:
    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self.body = body

    def locator(self, selector: str):
        self.selector = selector
        return self

    def inner_text(self, timeout: int):
        return self.body


if __name__ == "__main__":
    unittest.main()
