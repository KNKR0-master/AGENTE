import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.drive_inventory import DriveInventoryConfig
from src.publisher.drive_polling import (
    DrivePollCoordinator, configure_polling, polling_status, MIN_INTERVAL, MAX_BACKOFF,
)
from src.publisher.drive_sync import configured_scope
from src.publisher import drive_sync as drive_sync_module
from src.publisher.service import init_publisher
from src.store import connect, init_db


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "publisher.db"
        self.config = DriveInventoryConfig(root_folder_id="root", account_email="owner@example.com",
                                           credentials_file=None)
        self.scope = configured_scope(self.config)
        self.now = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
        self.calls = []
        self.result = {"status": "READY", "changes_seen": 0}
        with closing(connect(self.db)) as conn:
            init_db(conn)
            init_publisher(conn)

    def cursor(self, status="READY", token="C0"):
        with closing(connect(self.db)) as conn:
            conn.execute(
                """INSERT INTO publisher_drive_sync_state
                   (account_identity,provider,root_folder_id,current_cursor,cursor_status,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(account_identity,provider,root_folder_id) DO UPDATE SET
                   current_cursor=excluded.current_cursor,cursor_status=excluded.cursor_status""",
                (self.scope.account_identity, self.scope.provider, self.scope.root_folder_id,
                 token, status, self.now.isoformat(), self.now.isoformat()),
            )
            conn.commit()

    def state(self):
        with closing(connect(self.db)) as conn:
            return polling_status(conn, config=self.config)

    def configure(self, action="enable", interval=60):
        with closing(connect(self.db)) as conn:
            return configure_polling(conn, action, interval, now=self.now, config=self.config)

    def coordinator(self):
        def sync(_conn, *, bootstrap=False):
            self.calls.append(bootstrap)
            return self.result if not isinstance(self.result, Exception) else (_ for _ in ()).throw(self.result)
        return DrivePollCoordinator(self.db, config=self.config, clock=lambda: self.now, sync=sync)

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)

    def wait_for_calls(self, count):
        deadline = time.monotonic() + 5
        while len(self.calls) < count and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(len(self.calls), count)

    def test_default_enabled_and_validation(self):
        self.assertTrue(self.state()["enabled"])
        self.assertEqual(self.state()["interval_seconds"], 300)
        self.assertEqual(self.state()["paused_reason"], "UNINITIALIZED")
        with self.assertRaises(ValueError):
            self.configure(interval=MIN_INTERVAL - 1)
        with self.assertRaises(ValueError):
            self.configure("set_interval", None)
        with closing(connect(self.db)) as conn:
            init_publisher(conn)
            init_publisher(conn)
            self.assertEqual(conn.execute("SELECT count(*) FROM publisher_drive_poll_state").fetchone()[0], 0)

    def test_poll_state_is_scoped_and_status_has_no_cursor(self):
        self.cursor()
        self.configure()
        other = DriveInventoryConfig(root_folder_id="another-root", account_email="owner@example.com",
                                     credentials_file=None)
        with closing(connect(self.db)) as conn:
            self.assertTrue(polling_status(conn, config=other)["enabled"])
            self.assertNotIn("current_cursor", polling_status(conn, config=self.config))

    def test_enable_disable_preserves_cursor_and_history(self):
        self.cursor()
        self.configure()
        self.assertEqual(self.state()["status"], "SCHEDULED")
        poller = self.coordinator()
        self.advance(60)
        self.assertEqual(poller.poll_due_once(), "SUCCESS")
        self.configure("disable")
        self.assertFalse(self.state()["enabled"])
        self.assertIsNotNone(self.state()["last_success_at"])
        with closing(connect(self.db)) as conn:
            row = conn.execute("SELECT current_cursor FROM publisher_drive_sync_state").fetchone()
            self.assertEqual(row[0], "C0")

    def test_explicit_disable_survives_restart(self):
        self.cursor()
        self.configure("disable", 30)
        poller = self.coordinator()
        self.assertTrue(poller.start())
        self.assertTrue(poller.stop())
        self.assertFalse(self.state()["enabled"])
        self.assertEqual(self.state()["interval_seconds"], 30)
        self.assertEqual(self.calls, [])

    def test_fresh_ready_scope_catches_up_at_startup(self):
        self.cursor()
        poller = self.coordinator()
        self.assertTrue(poller.start())
        self.addCleanup(poller.stop)
        self.wait_for_calls(1)
        self.assertEqual(poller.poll_due_once(), "NOT_DUE")
        self.assertEqual(self.calls, [False])
        self.assertEqual(self.state()["interval_seconds"], 300)
        self.advance(300)
        self.assertEqual(poller.poll_due_once(), "SUCCESS")
        self.assertEqual(self.calls, [False, False])

    def test_missing_cursor_never_bootstraps(self):
        self.configure()
        self.assertEqual(self.state()["status"], "PAUSED")
        self.assertEqual(self.coordinator().poll_due_once(), "NOT_SCHEDULED")
        self.assertEqual(self.calls, [])

    def test_invalid_cursor_pauses(self):
        self.cursor("FULL_SCAN_REQUIRED")
        self.configure()
        self.assertEqual(self.state()["paused_reason"], "FULL_SCAN_REQUIRED")
        self.assertEqual(self.calls, [])

    def test_success_zero_changes_and_startup_catchup(self):
        self.cursor()
        self.configure()
        poller = self.coordinator()
        poller.start()
        self.addCleanup(poller.stop)
        self.wait_for_calls(1)
        self.assertEqual(poller.poll_due_once(), "NOT_DUE")
        self.assertEqual(self.calls, [False])
        self.advance(60)
        self.assertEqual(poller.poll_due_once(), "SUCCESS")
        self.assertEqual(self.calls, [False, False])
        poller.stop()
        restarted = self.coordinator()
        restarted.start()
        self.addCleanup(restarted.stop)
        self.wait_for_calls(3)
        self.assertEqual(restarted.poll_due_once(), "NOT_DUE")
        self.assertTrue(self.state()["enabled"])

    def test_simultaneous_due_calls_claim_one_cycle(self):
        self.cursor()
        self.configure()
        self.advance(60)
        entered = threading.Event()
        release = threading.Event()
        results = []

        def sync(_conn, *, bootstrap=False):
            self.calls.append(bootstrap)
            entered.set()
            self.assertTrue(release.wait(5))
            return {"status": "READY", "changes_seen": 0}

        poller = DrivePollCoordinator(self.db, config=self.config, clock=lambda: self.now, sync=sync)
        threads = [threading.Thread(target=lambda: results.append(poller.poll_due_once())) for _ in range(2)]
        threads[0].start()
        self.assertTrue(entered.wait(5))
        threads[1].start()
        release.set()
        for thread in threads:
            thread.join(5)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.calls, [False])
        self.assertCountEqual(results, ["SUCCESS", "NOT_DUE"])

    def test_stop_waits_for_cycle_and_blocks_later_calls(self):
        self.cursor()
        self.configure()
        self.advance(60)
        entered = threading.Event()
        release = threading.Event()
        stopped = threading.Event()

        def sync(_conn, *, bootstrap=False):
            self.calls.append(bootstrap)
            entered.set()
            self.assertTrue(release.wait(5))
            return {"status": "READY", "changes_seen": 0}

        poller = DrivePollCoordinator(self.db, config=self.config, clock=lambda: self.now, sync=sync)
        cycle = threading.Thread(target=poller.poll_due_once)
        cycle.start()
        self.assertTrue(entered.wait(5))
        shutdown = threading.Thread(target=lambda: (poller.stop(), stopped.set()))
        shutdown.start()
        self.assertFalse(stopped.is_set())
        release.set()
        cycle.join(5)
        shutdown.join(5)
        self.assertFalse(cycle.is_alive())
        self.assertFalse(shutdown.is_alive())
        self.assertTrue(stopped.is_set())
        self.advance(60)
        self.assertEqual(poller.poll_due_once(), "STOPPING")
        self.assertEqual(self.calls, [False])

    def test_stopped_coordinator_cannot_leak_into_restarted_scope(self):
        self.cursor()
        self.configure()
        first = self.coordinator()
        self.assertTrue(first.start())
        self.assertTrue(first.stop())
        self.advance(60)
        self.assertEqual(first.poll_due_once(), "STOPPING")
        second = self.coordinator()
        self.assertTrue(second.start())
        self.addCleanup(second.stop)
        expected = len(self.calls) + 1
        self.wait_for_calls(expected)
        self.assertEqual(second.poll_due_once(), "NOT_DUE")
        self.advance(60)
        self.assertEqual(second.poll_due_once(), "SUCCESS")
        self.assertEqual(self.calls, [False] * (expected + 1))

    def test_status_exposes_external_sync_lock_without_acquiring_it(self):
        lock = threading.Lock()
        with drive_sync_module._scope_locks_guard:
            drive_sync_module._scope_locks[self.scope] = lock
        self.addCleanup(lambda: drive_sync_module._scope_locks.pop(self.scope, None))
        with patch.object(drive_sync_module.DriveInventoryConfig, "from_env", return_value=self.config):
            with closing(connect(self.db)) as conn:
                self.assertFalse(drive_sync_module.drive_sync_status(conn)["sync_in_progress"])
                lock.acquire()
                try:
                    self.assertTrue(drive_sync_module.drive_sync_status(conn)["sync_in_progress"])
                finally:
                    lock.release()
                self.assertFalse(drive_sync_module.drive_sync_status(conn)["sync_in_progress"])

    def test_transient_backoff_then_success(self):
        self.cursor()
        self.configure()
        poller = self.coordinator()
        self.advance(60)
        self.result = {"status": "TRANSIENT_ERROR"}
        self.assertEqual(poller.poll_due_once(), "BACKOFF")
        self.assertEqual(self.state()["consecutive_failures"], 1)
        self.assertEqual(datetime.fromisoformat(self.state()["next_attempt_at"]), self.now + timedelta(seconds=120))
        for _ in range(5):
            self.now = datetime.fromisoformat(self.state()["next_attempt_at"])
            self.assertEqual(poller.poll_due_once(), "BACKOFF")
        self.assertLessEqual((datetime.fromisoformat(self.state()["next_attempt_at"]) - self.now).total_seconds(), MAX_BACKOFF)
        self.result = {"status": "READY", "changes_seen": 2}
        self.now = datetime.fromisoformat(self.state()["next_attempt_at"])
        self.assertEqual(poller.poll_due_once(), "SUCCESS")
        self.assertEqual(self.state()["consecutive_failures"], 0)
        self.assertEqual(self.state()["last_changes_seen"], 2)

    def test_restart_does_not_bypass_backoff(self):
        self.cursor()
        self.configure()
        self.result = {"status": "TRANSIENT_ERROR"}
        self.advance(60)
        poller = self.coordinator()
        self.assertEqual(poller.poll_due_once(), "BACKOFF")
        due = self.state()["next_attempt_at"]
        restarted = self.coordinator()
        self.assertTrue(restarted.start())
        self.addCleanup(restarted.stop)
        self.assertEqual(self.state()["status"], "BACKOFF")
        self.assertEqual(self.state()["next_attempt_at"], due)
        self.assertEqual(restarted.poll_due_once(), "NOT_DUE")

    def test_busy_lock_skips_without_failure(self):
        self.cursor()
        self.configure()
        self.result = {"status": "SYNC_IN_PROGRESS"}
        self.advance(60)
        self.assertEqual(self.coordinator().poll_due_once(), "POLL_SKIPPED_SYNC_ALREADY_RUNNING")
        self.assertEqual(self.state()["consecutive_failures"], 0)

    def test_auth_and_account_mismatch_pause(self):
        for error, reason in (("GOOGLE_USER_CREDENTIAL_REFRESH_FAILED", "AUTH_REQUIRED"),
                              ("GOOGLE_ACCOUNT_MISMATCH", "ACCOUNT_MISMATCH")):
            with self.subTest(error=error):
                self.cursor()
                self.configure()
                self.result = RuntimeError(error)
                self.advance(60)
                self.assertEqual(self.coordinator().poll_due_once(), reason)
                self.assertEqual(self.state()["paused_reason"], reason)

    def test_result_requiring_scan_pauses(self):
        self.cursor()
        self.configure()
        self.result = {"status": "FULL_SCAN_REQUIRED"}
        self.advance(60)
        self.assertEqual(self.coordinator().poll_due_once(), "FULL_SCAN_REQUIRED")
        self.assertEqual(self.state()["status"], "PAUSED")

    def test_duplicate_start_and_shutdown(self):
        self.cursor()
        self.configure()
        poller = self.coordinator()
        self.assertTrue(poller.start())
        self.assertFalse(poller.start())
        self.assertTrue(poller.stop())
        self.advance(60)
        self.assertEqual(poller.poll_due_once(), "STOPPING")
        self.assertEqual(self.calls, [])

    def test_second_coordinator_same_scope_does_not_start(self):
        self.cursor()
        self.configure()
        first, second = self.coordinator(), self.coordinator()
        self.assertTrue(first.start())
        try:
            self.assertFalse(second.start())
        finally:
            self.assertTrue(first.stop())

    def test_disable_during_cycle_cannot_reschedule(self):
        self.cursor()
        self.configure()
        self.advance(60)
        def sync(_conn, *, bootstrap=False):
            self.calls.append(bootstrap)
            self.configure("disable")
            return {"status": "READY", "changes_seen": 0}
        poller = DrivePollCoordinator(self.db, config=self.config, clock=lambda: self.now, sync=sync)
        self.assertEqual(poller.poll_due_once(), "DISABLED_DURING_CYCLE")
        self.assertEqual(self.state()["status"], "DISABLED")
        self.assertIsNone(self.state()["next_attempt_at"])

    def test_interval_change_does_not_clear_auth_pause(self):
        self.cursor()
        self.configure()
        self.advance(60)
        self.result = RuntimeError("GOOGLE_USER_CREDENTIAL_REFRESH_FAILED")
        self.assertEqual(self.coordinator().poll_due_once(), "AUTH_REQUIRED")
        self.configure("set_interval", 300)
        self.assertEqual(self.state()["status"], "PAUSED")
        self.assertEqual(self.state()["paused_reason"], "AUTH_REQUIRED")
        restarted = self.coordinator()
        self.assertTrue(restarted.start())
        self.assertEqual(restarted.poll_due_once(), "NOT_SCHEDULED")
        self.assertTrue(restarted.stop())

    def test_polling_does_not_mutate_account_safety_or_credentials(self):
        self.cursor()
        self.configure()
        self.advance(60)
        credential = Path(self.temp.name) / "credential.dpapi"
        credential.write_bytes(b"fixed-dpapi-fixture")
        before = credential.read_bytes()
        poller = self.coordinator()
        for _ in range(3):
            self.assertEqual(poller.poll_due_once(), "SUCCESS")
            self.advance(60)
        self.assertEqual(credential.read_bytes(), before)
        with closing(connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM account_protection_events").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT count(*) FROM account_mutation_ledger").fetchone()[0], 0)
