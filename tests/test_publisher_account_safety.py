import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from src.publisher.account_safety import (
    AccountProtectionBlocked,
    AccountSafetyGuard,
    init_account_safety,
    map_risk_signal,
)


class AccountSafetyTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('CREATE TABLE publisher_accounts(id INTEGER PRIMARY KEY, name TEXT)')
        self.conn.execute("INSERT INTO publisher_accounts VALUES (1,'CECILIA FIGUEIREDO')")
        init_account_safety(self.conn)
        self.guard = AccountSafetyGuard(self.conn)

    def tearDown(self):
        self.conn.close()

    def test_default_is_normal_and_limits_disabled(self):
        row = self.guard.snapshot(1)
        self.assertEqual(row['state'], 'NORMAL')
        self.assertIsNone(row['max_mutations_per_day'])
        self.assertIsNone(row['max_duration_seconds'])

    def test_each_protection_state_blocks(self):
        for state in ('RATE_LIMITED','POSTING_LIMIT_REACHED','SPAM_RISK','POSTING_BANNED','MANUAL_REVIEW_REQUIRED'):
            self.conn.execute("UPDATE account_protection SET state='NORMAL' WHERE account_id=1")
            self.guard.activate(1, state, 'KNOWN_SIGNAL', 'OBSERVED_TIKTOK_UI')
            with self.assertRaises(AccountProtectionBlocked):
                self.guard.assert_allowed(1)

    def test_events_are_append_only(self):
        self.guard.activate(1, 'SPAM_RISK', 'SPAM_RISK', 'OBSERVED_TIKTOK_UI')
        self.guard.clear_manual(1, human_confirmed=True, reason='revisado')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM account_protection_events').fetchone()[0], 2)

    def test_manual_clear_requires_confirmation_and_reason(self):
        with self.assertRaises(ValueError):
            self.guard.clear_manual(1, human_confirmed=False, reason='x')
        with self.assertRaises(ValueError):
            self.guard.clear_manual(1, human_confirmed=True, reason='')

    def test_trusted_cooldown_expires(self):
        past = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        self.guard.activate(1, 'RATE_LIMITED', 'RATE_LIMIT_EXCEEDED', 'OBSERVED_TIKTOK_UI', cooldown_until=past)
        self.guard.assert_allowed(1)
        self.assertEqual(self.guard.snapshot(1)['state'], 'NORMAL')

    def test_operator_state_does_not_auto_expire(self):
        past = (datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()
        self.guard.activate(1, 'MANUAL_REVIEW_REQUIRED', 'OPERATOR_HOLD', 'OPERATOR', cooldown_until=past)
        with self.assertRaises(AccountProtectionBlocked):
            self.guard.assert_allowed(1)

    def test_mutation_key_is_idempotent_and_blocks_duplicate(self):
        self.guard.start_mutation(1, 'draft:1:VIDEO_UPLOAD', 'VIDEO_UPLOAD', draft_id=1)
        with self.assertRaises(AccountProtectionBlocked) as raised:
            self.guard.start_mutation(1, 'draft:1:VIDEO_UPLOAD', 'VIDEO_UPLOAD', draft_id=1)
        self.assertEqual(raised.exception.reason_code, 'DUPLICATE_MUTATION_KEY')

    def test_complete_mutation_is_idempotent(self):
        self.guard.start_mutation(1, 'one', 'SAVE_DRAFT')
        self.assertTrue(self.guard.complete_mutation(1, 'one'))
        self.assertFalse(self.guard.complete_mutation(1, 'one'))

    def test_daily_budget_blocks_before_next_mutation(self):
        self.conn.execute('UPDATE account_protection SET max_mutations_per_day=1 WHERE account_id=1')
        self.guard.start_mutation(1, 'one', 'VIDEO_UPLOAD')
        with self.assertRaises(AccountProtectionBlocked):
            self.guard.start_mutation(1, 'two', 'SAVE_DRAFT')
        self.assertEqual(self.guard.snapshot(1)['state'], 'POSTING_LIMIT_REACHED')

    def test_rolling_budget_blocks(self):
        self.conn.execute('UPDATE account_protection SET max_mutations_per_window=1,window_minutes=30 WHERE account_id=1')
        self.guard.start_mutation(1, 'one', 'VIDEO_UPLOAD')
        with self.assertRaises(AccountProtectionBlocked):
            self.guard.assert_allowed(1)

    def test_media_limits_are_nullable(self):
        self.assertTrue(self.guard.validate_media(1, {}))

    def test_media_duration_limit(self):
        self.conn.execute('UPDATE account_protection SET max_duration_seconds=10 WHERE account_id=1')
        with self.assertRaisesRegex(ValueError, 'MEDIA_DURATION_LIMIT'):
            self.guard.validate_media(1, {'duration': 11})

    def test_media_size_limit(self):
        self.conn.execute('UPDATE account_protection SET max_file_size_bytes=10 WHERE account_id=1')
        with self.assertRaisesRegex(ValueError, 'MEDIA_SIZE_LIMIT'):
            self.guard.validate_media(1, {'file_size': 11})

    def test_media_fps_limits(self):
        self.conn.execute('UPDATE account_protection SET min_fps=24,max_fps=60 WHERE account_id=1')
        self.assertTrue(self.guard.validate_media(1, {'fps': 30}))
        with self.assertRaisesRegex(ValueError, 'MEDIA_FPS_BELOW_MINIMUM'):
            self.guard.validate_media(1, {'fps': 20})
        with self.assertRaisesRegex(ValueError, 'MEDIA_FPS_ABOVE_MAXIMUM'):
            self.guard.validate_media(1, {'fps': 90})

    def test_known_risk_signals(self):
        expected = {
            'rate_limit_exceeded': 'RATE_LIMITED',
            'spam_risk_too_many_posts': 'POSTING_LIMIT_REACHED',
            'spam_risk_too_many_pending_share': 'SPAM_RISK',
            'spam_risk_text': 'SPAM_RISK',
            'account posting ban': 'POSTING_BANNED',
        }
        for message, state in expected.items():
            with self.subTest(message=message):
                self.assertEqual(map_risk_signal(message).state, state)

    def test_technical_failures_never_map_to_risk(self):
        for message in ('TargetClosedError', 'TimeoutError', 'ffprobe unavailable', 'drive_folder_scan_failed', 'random error'):
            with self.subTest(message=message):
                self.assertIsNone(map_risk_signal(message))

    def test_evidence_is_sanitized(self):
        self.guard.activate(1, 'SPAM_RISK', 'SPAM_RISK', 'OBSERVED_TIKTOK_UI', evidence={
            'ui_marker': 'spam_risk', 'access_token': 'secret', 'cookie': 'secret'
        })
        evidence = self.conn.execute('SELECT evidence_json FROM account_protection_events').fetchone()[0]
        self.assertIn('spam_risk', evidence)
        self.assertNotIn('secret', evidence)

    def test_schema_migration_is_idempotent(self):
        init_account_safety(self.conn)
        init_account_safety(self.conn)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM account_protection').fetchone()[0], 1)

    def test_state_survives_new_guard_instance(self):
        self.guard.activate(1, 'POSTING_BANNED', 'ACCOUNT_POSTING_BANNED', 'OBSERVED_TIKTOK_UI')
        self.assertEqual(AccountSafetyGuard(self.conn).snapshot(1)['state'], 'POSTING_BANNED')

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            self.guard.activate(1, 'SPAM_RISK', 'X', 'UNKNOWN')

    def test_normal_cannot_be_activated_as_incident(self):
        with self.assertRaises(ValueError):
            self.guard.activate(1, 'NORMAL', 'X', 'OPERATOR')

    def test_missing_state_fails_closed(self):
        self.conn.execute('DELETE FROM account_protection WHERE account_id=1')
        with self.assertRaises(AccountProtectionBlocked):
            self.guard.snapshot(1)


if __name__ == '__main__':
    unittest.main()
