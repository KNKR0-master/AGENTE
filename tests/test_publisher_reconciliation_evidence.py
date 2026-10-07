import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from src.publisher.queue_policy import draft_queue_capabilities
from src.publisher.reconciliation_evidence import (
    evaluate_reconciliation_evidence_validity,
    get_latest_reconciliation_evidence,
    record_reconciliation_evidence,
)
from src.publisher.service import get_row, init_publisher, log
from tests import test_publisher_remote as remote_fixture


class ReconciliationEvidenceTests(unittest.TestCase):
    setUp = remote_fixture.PublisherRemoteTests.setUp
    batch = remote_fixture.PublisherRemoteTests.batch

    def fixture(self):
        first, second = self.batch(2)
        now = datetime.now(timezone.utc)
        with self.conn:
            execution = self.conn.execute(
                "INSERT INTO publisher_batch_executions(batch_id,total,started_at,status) VALUES (?,?,?,'PAUSED')",
                (None, 2, (now - timedelta(minutes=2)).isoformat()),
            ).lastrowid
            for draft_id in (first, second):
                self.conn.execute(
                    'INSERT INTO publisher_execution_items(execution_id,draft_id,status) VALUES (?,?,?)',
                    (execution, draft_id, 'WAITING_MANUAL'),
                )
                self.conn.execute(
                    "UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED' WHERE id=?", (draft_id,)
                )
                self.conn.execute(
                    "UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' "
                    'WHERE id=(SELECT media_asset_id FROM publisher_drafts WHERE id=?)', (draft_id,)
                )
                log(self.conn, draft_id, 'UPLOAD_STARTED', 'OK', 'fixture')
        return first, second, execution

    def record(self, draft_id, execution_id, **changes):
        draft = get_row(self.conn, 'publisher_drafts', draft_id)
        account = get_row(self.conn, 'publisher_accounts', draft['account_id'])
        checkpoint = self.conn.execute(
            "SELECT id FROM publisher_execution_logs WHERE draft_id=? AND step='UPLOAD_STARTED' "
            'ORDER BY id DESC LIMIT 1', (draft_id,),
        ).fetchone()
        now = datetime.now(timezone.utc)
        values = dict(
            draft_id=draft_id, media_asset_id=draft['media_asset_id'],
            execution_id=execution_id, account_id=draft['account_id'],
            expected_account=account['tiktok_username'] or 'cecilia.figueired65',
            observed_account=account['tiktok_username'] or 'cecilia.figueired65',
            outcome='REMOTE_STATE_UNKNOWN', coverage_status='PARTIAL',
            source_surface='TIKTOK_STUDIO_DRAFT_LIST', candidate_count=0,
            matcher_version='canonical-v1', remote_effect_checkpoint_id=checkpoint['id'],
            verification_started_at=now.isoformat(),
            verification_completed_at=(now + timedelta(seconds=1)).isoformat(),
            valid_until=(now + timedelta(minutes=5)).isoformat(),
            evidence_payload={'pagination_observations': {'complete': False}},
        )
        values.update(changes)
        return record_reconciliation_evidence(self.conn, **values)

    def valid(self, evidence_id, draft_id, execution_id, **changes):
        draft = get_row(self.conn, 'publisher_drafts', draft_id)
        values = dict(draft_id=draft_id, media_asset_id=draft['media_asset_id'],
                      execution_id=execution_id, account_id=draft['account_id'])
        values.update(changes)
        return evaluate_reconciliation_evidence_validity(self.conn, evidence_id, **values)

    def test_migration_is_idempotent_and_has_composite_execution_item_fk(self):
        init_publisher(self.conn)
        columns = {row['name'] for row in self.conn.execute('PRAGMA table_info(publisher_reconciliation_evidence)')}
        self.assertIn('remote_effect_checkpoint_id', columns)
        self.assertIn('execution_item_draft_id', columns)
        keys = list(self.conn.execute('PRAGMA foreign_key_list(publisher_reconciliation_evidence)'))
        self.assertTrue(any(row['table'] == 'publisher_execution_items' for row in keys))

    def test_append_only_latest_and_no_lifecycle_effect(self):
        first, _, execution = self.fixture()
        before = get_row(self.conn, 'publisher_drafts', first)
        media_before = get_row(self.conn, 'publisher_media_assets', before['media_asset_id'])
        old = self.record(first, execution)
        new = self.record(first, execution, outcome='REMOTE_EXISTS_CONFIRMED',
                          coverage_status='COMPLETE', candidate_count=1,
                          matched_candidate_fingerprint='sha256:fixture')
        self.assertEqual(get_latest_reconciliation_evidence(self.conn, draft_id=first)['id'], new)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_reconciliation_evidence').fetchone()[0], 2)
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'APPEND_ONLY'):
            self.conn.execute('DELETE FROM publisher_reconciliation_evidence WHERE id=?', (old,))
        self.conn.rollback()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'APPEND_ONLY'):
            self.conn.execute('UPDATE publisher_reconciliation_evidence SET outcome=? WHERE id=?',
                              ('REMOTE_ABSENCE_CONFIRMED', old))
        self.conn.rollback()
        self.assertEqual(get_row(self.conn, 'publisher_drafts', first)['status'], before['status'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', before['media_asset_id'])['status'], media_before['status'])
        self.assertFalse(draft_queue_capabilities(self.conn, before, media_before)['can_start'])

    def test_exact_draft_media_account_and_execution_item_isolation(self):
        first, second, execution = self.fixture()
        evidence = self.record(first, execution)
        self.assertIsNone(get_latest_reconciliation_evidence(self.conn, draft_id=second))
        self.assertTrue(self.valid(evidence, first, execution)['valid'])
        self.assertFalse(self.valid(evidence, second, execution)['valid'])
        self.assertEqual(self.valid(evidence, first, execution, media_asset_id=999)['reason'], 'EVIDENCE_IDENTITY_MISMATCH')
        self.assertEqual(self.valid(evidence, first, execution, account_id=999)['reason'], 'EVIDENCE_IDENTITY_MISMATCH')
        self.assertEqual(self.valid(evidence, first, execution + 1)['reason'], 'EVIDENCE_EXECUTION_ITEM_MISMATCH')
        with self.assertRaisesRegex(ValueError, 'EVIDENCE_IDENTITY_MISMATCH'):
            self.record(first, execution, media_asset_id=get_row(self.conn, 'publisher_drafts', second)['media_asset_id'])

    def test_new_remote_checkpoint_stales_old_evidence(self):
        first, _, execution = self.fixture()
        evidence = self.record(first, execution)
        with self.conn:
            log(self.conn, first, 'SAVE_DRAFT', 'OK', 'new remote effect')
        self.assertEqual(self.valid(evidence, first, execution)['reason'], 'REMOTE_EFFECT_CHECKPOINT_STALE')
        with self.assertRaisesRegex(ValueError, 'REMOTE_EFFECT_CHECKPOINT_MISMATCH'):
            self.record(first, execution)

    def test_new_execution_and_lifecycle_change_stale_old_evidence(self):
        first, _, execution = self.fixture()
        evidence = self.record(first, execution)
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_media_assets SET status='RESERVED' WHERE id="
                '(SELECT media_asset_id FROM publisher_drafts WHERE id=?)', (first,)
            )
        self.assertEqual(self.valid(evidence, first, execution)['reason'], 'LIFECYCLE_CHANGED')
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id="
                '(SELECT media_asset_id FROM publisher_drafts WHERE id=?)', (first,)
            )
            newer = self.conn.execute(
                'INSERT INTO publisher_batch_executions(batch_id,total,started_at) VALUES (?,?,?)',
                (None, 1, datetime.now(timezone.utc).isoformat()),
            ).lastrowid
            self.conn.execute(
                'INSERT INTO publisher_execution_items(execution_id,draft_id,status) VALUES (?,?,?)',
                (newer, first, 'WAITING_MANUAL'),
            )
        self.assertEqual(self.valid(evidence, first, execution)['reason'], 'EVIDENCE_EXECUTION_ITEM_STALE')

    def test_unbounded_evidence_is_not_fresh_for_sensitive_actions(self):
        first, _, execution = self.fixture()
        evidence = self.record(first, execution, valid_until=None)
        self.assertEqual(self.valid(evidence, first, execution)['reason'], 'EVIDENCE_EXPIRED_OR_UNBOUNDED')

    def test_coverage_and_candidate_validation(self):
        first, _, execution = self.fixture()
        for coverage in ('PARTIAL', 'UNKNOWN'):
            with self.subTest(coverage=coverage), self.assertRaisesRegex(ValueError, 'ABSENCE_REQUIRES_COMPLETE'):
                self.record(first, execution, outcome='REMOTE_ABSENCE_CONFIRMED', coverage_status=coverage)
        self.record(first, execution, outcome='REMOTE_ABSENCE_CONFIRMED', coverage_status='COMPLETE')
        with self.assertRaisesRegex(ValueError, 'EXISTENCE_REQUIRES_UNIQUE_STRONG_MATCH'):
            self.record(first, execution, outcome='REMOTE_EXISTS_CONFIRMED', coverage_status='COMPLETE')
        with self.assertRaisesRegex(ValueError, 'AMBIGUITY_REQUIRES_MULTIPLE_CANDIDATES'):
            self.record(first, execution, outcome='REMOTE_MATCH_AMBIGUOUS', candidate_count=1)
        self.record(first, execution, outcome='REMOTE_MATCH_AMBIGUOUS', candidate_count=2)

    def test_expiry_account_and_payload_safety(self):
        first, _, execution = self.fixture()
        evidence = self.record(first, execution)
        self.assertEqual(self.valid(evidence, first, execution,
                                    now=(datetime.now(timezone.utc) + timedelta(minutes=6)).isoformat())['reason'],
                         'EVIDENCE_EXPIRED_OR_UNBOUNDED')
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_IDENTITY_NOT_CONFIRMED'):
            self.record(first, execution, outcome='REMOTE_ABSENCE_CONFIRMED',
                        coverage_status='COMPLETE', observed_account='wrong')
        with self.assertRaisesRegex(ValueError, 'UNSAFE_EVIDENCE_PAYLOAD'):
            self.record(first, execution, evidence_payload={'candidate_summaries': [{'access_token': 'x'}]})

    def test_unknown_ambiguous_and_absence_do_not_enable_retry(self):
        first, _, execution = self.fixture()
        draft = get_row(self.conn, 'publisher_drafts', first)
        media = get_row(self.conn, 'publisher_media_assets', draft['media_asset_id'])
        for outcome, coverage, count in (
            ('REMOTE_STATE_UNKNOWN', 'UNKNOWN', 0),
            ('REMOTE_MATCH_AMBIGUOUS', 'PARTIAL', 2),
            ('REMOTE_ABSENCE_CONFIRMED', 'COMPLETE', 0),
        ):
            self.record(first, execution, outcome=outcome, coverage_status=coverage, candidate_count=count)
            caps = draft_queue_capabilities(self.conn, draft, media)
            self.assertFalse(caps['can_start'])
            self.assertFalse(caps['can_remove'])
            self.assertFalse(caps['can_edit'])


if __name__ == '__main__':
    unittest.main()
