from __future__ import annotations

import unittest

from src.store import connect, init_db
from src.tiktok_analytics import begin_extraction_run, finish_extraction_run, persist_video_snapshot, upsert_tiktok_video
from src.tiktok_readiness import (
    ReadinessSample,
    TikTokReadinessTracker,
    authenticated_login_handoff_target,
    data_completeness_score,
    is_placeholder,
    percentile,
    should_guard_transient_zero,
)


class TikTokReadinessTrackerTests(unittest.TestCase):
    def test_placeholders_are_not_real_zeroes(self) -> None:
        self.assertTrue(is_placeholder("--"))
        self.assertTrue(is_placeholder("Loading"))
        self.assertFalse(is_placeholder("0"))
        self.assertFalse(is_placeholder(0))

    def test_transient_zero_must_stabilize_after_value_changes(self) -> None:
        tracker = TikTokReadinessTracker(required_stable_readings=3, dom_quiet_ms=1800)
        zero = ReadinessSample(signature=(("views", 0),), data_detected=True)
        real = ReadinessSample(signature=(("views", 18),), data_detected=True)
        self.assertFalse(tracker.observe(zero, 0).ready)
        self.assertFalse(tracker.observe(zero, 1000).ready)
        self.assertFalse(tracker.observe(real, 1500).ready)
        self.assertFalse(tracker.observe(real, 2500).ready)
        decision = tracker.observe(real, 3500)
        self.assertTrue(decision.ready)
        self.assertEqual(decision.state, "READY")

    def test_loader_pending_request_and_wrong_video_block_readiness(self) -> None:
        tracker = TikTokReadinessTracker(required_stable_readings=2, dom_quiet_ms=500)
        blocked_samples = (
            ReadinessSample(signature=(100,), data_detected=True, loaders_visible=1),
            ReadinessSample(signature=(100,), data_detected=True, pending_requests=1),
            ReadinessSample(signature=(100,), data_detected=True, correct_video=False),
        )
        for index, sample in enumerate(blocked_samples):
            self.assertFalse(tracker.observe(sample, index * 1000).ready)
        clear = ReadinessSample(signature=(100,), data_detected=True)
        self.assertFalse(tracker.observe(clear, 4000).ready)
        self.assertTrue(tracker.observe(clear, 5000).ready)

    def test_terminal_unavailable_state_can_stabilize_without_inventing_metrics(self) -> None:
        tracker = TikTokReadinessTracker(required_stable_readings=3, dom_quiet_ms=1000)
        unavailable = ReadinessSample(signature=(("reach", None),), data_detected=True)
        self.assertFalse(tracker.observe(unavailable, 0).ready)
        self.assertFalse(tracker.observe(unavailable, 500).ready)
        self.assertTrue(tracker.observe(unavailable, 1000).ready)

    def test_completeness_and_percentile_are_deterministic(self) -> None:
        self.assertEqual(data_completeness_score({"views": 0, "likes": 1}, ["views", "likes", "shares"]), 66.67)
        self.assertEqual(percentile([1000, 3000, 2000], 0.95), 2900)

    def test_zero_without_network_response_is_guarded_but_real_zero_can_pass(self) -> None:
        self.assertTrue(should_guard_transient_zero(
            {"views": 0}, ["views"], relevant_response_received=False, elapsed_ms=1500, guard_ms=4000,
        ))
        self.assertFalse(should_guard_transient_zero(
            {"views": 0}, ["views"], relevant_response_received=True, elapsed_ms=1500, guard_ms=4000,
        ))
        self.assertFalse(should_guard_transient_zero(
            {"views": 0}, ["views"], relevant_response_received=False, elapsed_ms=4500, guard_ms=4000,
        ))

    def test_authenticated_login_bridge_preserves_official_redirect(self) -> None:
        target = "https://www.tiktok.com/tiktokstudio/analytics/123/overview"
        url = "https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Ftiktokstudio%2Fanalytics%2F123%2Foverview"
        self.assertEqual(
            authenticated_login_handoff_target(url, "Voc\u00ea est\u00e1 conectado, n\u00e3o \u00e9 preciso repetir"),
            target,
        )
        self.assertIsNone(authenticated_login_handoff_target(url, "Entrar no TikTok"))


class TikTokReadinessPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_snapshot_and_run_preserve_readiness_telemetry(self) -> None:
        run = begin_extraction_run(self.conn, "readiness-run")
        upsert_tiktok_video(self.conn, {"video_id": "7674453719018245384"})
        snapshot = persist_video_snapshot(
            self.conn,
            run["id"],
            "7674453719018245384",
            {"views": 67, "likes": 0},
            extraction_status="COMPLETE",
            data_completeness_score=83.33,
            load_duration_ms=7400,
            retry_count=1,
            readiness=[{"state": "READY", "stable_readings": 3}],
        )
        self.assertEqual(snapshot["extraction_status"], "COMPLETE")
        self.assertEqual(snapshot["data_completeness_score"], 83.33)
        self.assertEqual(snapshot["load_duration_ms"], 7400)
        self.assertEqual(snapshot["retry_count"], 1)
        self.assertIn("stable_readings", snapshot["readiness_json"])

        finish_extraction_run(
            self.conn,
            run["id"],
            "SUCCESS",
            counts={"videos_discovered": 1, "videos_collected": 1, "snapshots_created": 1},
            coverage={"videos_expected": 1, "collection_coverage": 1, "coverage_confidence": "HIGH"},
            valid=True,
            quality_summary={
                "videos_complete": 1,
                "videos_partial": 0,
                "videos_timeout": 0,
                "retries_total": 1,
                "average_load_ms": 7400,
                "p95_load_ms": 7400,
                "average_completeness": 83.33,
            },
        )
        saved_run = self.conn.execute("SELECT * FROM tiktok_analytics_runs WHERE id = ?", (run["id"],)).fetchone()
        self.assertEqual(saved_run["videos_complete"], 1)
        self.assertEqual(saved_run["retries_total"], 1)
        self.assertEqual(saved_run["average_completeness"], 83.33)


if __name__ == "__main__":
    unittest.main()
