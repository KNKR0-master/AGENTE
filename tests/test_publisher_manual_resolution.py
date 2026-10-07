import json
import io
import unittest
from unittest.mock import patch, Mock

from src.publisher.manual_resolution import resolve_draft
from src.publisher.queue_policy import draft_queue_capabilities
from src.publisher.service import assert_upload_allowed, get_row
from tests import test_publisher_reconciliation_evidence as evidence_fixture


class ManualResolutionTests(unittest.TestCase):
    setUp = evidence_fixture.ReconciliationEvidenceTests.setUp
    batch = evidence_fixture.ReconciliationEvidenceTests.batch
    fixture = evidence_fixture.ReconciliationEvidenceTests.fixture
    record = evidence_fixture.ReconciliationEvidenceTests.record

    def prepare_resolution(self, **changes):
        draft_id, _, execution_id = self.fixture()
        with self.conn:
            self.conn.execute("UPDATE publisher_batch_executions SET status='PAUSED' WHERE id=?", (execution_id,))
        checkpoint = self.conn.execute("SELECT MAX(id) FROM publisher_execution_logs WHERE draft_id=? AND step='UPLOAD_STARTED'", (draft_id,)).fetchone()[0]
        data = dict(decision='ABANDON', human_confirmed=True, reason='Conferido manualmente no Studio',
                    checkpoint_id=checkpoint, acknowledge_remote_may_exist=True,
                    media_asset_id=get_row(self.conn, 'publisher_drafts', draft_id)['media_asset_id'],
                    account_id=self.account)
        data.update(changes)
        return draft_id, execution_id, data

    def test_release_is_explicit_atomic_audited_and_old_identity_cannot_retry(self):
        draft_id, execution_id, data = self.prepare_resolution()
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?", (draft_id,))
        before = get_row(self.conn, 'publisher_drafts', draft_id)
        result = resolve_draft(self.conn, draft_id, data)
        self.assertEqual((result['status'], result['media_status']), ('ARCHIVED', 'AVAILABLE'))
        self.assertFalse(result['retry_authorized'])
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['upload_status'], 'TRANSFER_UNKNOWN')
        with self.assertRaisesRegex(ValueError, 'RECONCILIATION'):
            assert_upload_allowed(self.conn, draft_id)
        self.service.assert_media_available(get_row(self.conn, 'publisher_media_assets', before['media_asset_id']))
        audit = json.loads(self.conn.execute("SELECT message FROM publisher_execution_logs WHERE draft_id=? AND step='MANUAL_RESOLUTION'", (draft_id,)).fetchone()[0])
        self.assertEqual(audit['previous_media_state'], 'NEEDS_RECONCILIATION')
        self.assertEqual(audit['execution_id'], execution_id)
        self.assertTrue(audit['human_confirmed'])
        self.assertFalse(self.provider.deleted)
        self.assertFalse(self.provider.downloads)
        self.assertEqual(self.conn.execute('SELECT status FROM publisher_batch_executions WHERE id=?', (execution_id,)).fetchone()[0], 'PAUSED')

    def test_missing_confirmation_ack_or_stale_checkpoint_cannot_release(self):
        draft_id, _, data = self.prepare_resolution()
        for key, value in [('human_confirmed', False), ('acknowledge_remote_may_exist', False), ('checkpoint_id', -1), ('media_asset_id', -1), ('account_id', -1)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                resolve_draft(self.conn, draft_id, {**data, key: value})
            self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['status'], 'MANUAL_ACTION_REQUIRED')

    def test_failed_terminal_execution_can_verify_but_running_cannot(self):
        draft_id, execution_id, data = self.prepare_resolution()
        with self.conn:
            self.conn.execute("UPDATE publisher_execution_items SET status='FAILED' WHERE draft_id=?", (draft_id,))
            self.conn.execute("UPDATE publisher_batch_executions SET status='COMPLETED' WHERE id=?", (execution_id,))
        draft = get_row(self.conn, 'publisher_drafts', draft_id)
        media = get_row(self.conn, 'publisher_media_assets', draft['media_asset_id'])
        self.assertTrue(draft_queue_capabilities(self.conn, draft, media)['can_verify'])
        with self.conn:
            self.conn.execute("UPDATE publisher_batch_executions SET status='RUNNING' WHERE id=?", (execution_id,))
        self.assertFalse(draft_queue_capabilities(self.conn, draft, media)['can_verify'])
        with self.assertRaisesRegex(ValueError, 'NOT_RESOLUTION_ELIGIBLE'):
            resolve_draft(self.conn, draft_id, data)

    def test_unknown_preserves_reservation_and_records_human_evidence(self):
        draft_id, _, data = self.prepare_resolution(decision='UNKNOWN')
        result = resolve_draft(self.conn, draft_id, data)
        self.assertEqual(result['media_status'], 'NEEDS_RECONCILIATION')
        self.assertEqual(result['status'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='MANUAL_RESOLUTION'").fetchone()[0], 1)

    def test_strong_fresh_evidence_reconciles_without_cleanup(self):
        draft_id, execution_id, data = self.prepare_resolution(decision='CONFIRM_EXISTS')
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT',checklist_json=? WHERE id=?",
                              (json.dumps(dict.fromkeys(('file', 'upload', 'caption', 'settings'), True)), draft_id))
        data['evidence_id'] = self.record(draft_id, execution_id, outcome='REMOTE_EXISTS_CONFIRMED',
                                        coverage_status='COMPLETE', candidate_count=1, matched_candidate_fingerprint='unique-fixture')
        result = resolve_draft(self.conn, draft_id, data)
        self.assertEqual(result['status'], 'READY_FOR_REVIEW')
        self.assertEqual(result['media_status'], 'RESERVED')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['persistence_status'], 'PERSISTED')
        self.assertFalse(self.provider.deleted)

    def test_unknown_ambiguous_and_absence_cannot_be_promoted(self):
        draft_id, execution_id, data = self.prepare_resolution(decision='CONFIRM_EXISTS')
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT',checklist_json=? WHERE id=?",
                              (json.dumps(dict.fromkeys(('file', 'upload', 'caption', 'settings'), True)), draft_id))
        for outcome, count in [('REMOTE_STATE_UNKNOWN', 0), ('REMOTE_MATCH_AMBIGUOUS', 2), ('REMOTE_ABSENCE_CONFIRMED', 0)]:
            data['evidence_id'] = self.record(draft_id, execution_id, outcome=outcome, candidate_count=count, coverage_status='COMPLETE')
            with self.subTest(outcome=outcome), self.assertRaisesRegex(ValueError, 'EVIDENCE_REQUIRED'):
                resolve_draft(self.conn, draft_id, data)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['persistence_status'], 'UNCONFIRMED')

    def test_audit_failure_rolls_back_release(self):
        draft_id, _, data = self.prepare_resolution()
        with patch('src.publisher.manual_resolution.log', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                resolve_draft(self.conn, draft_id, data)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['status'], 'MANUAL_ACTION_REQUIRED')

    def test_http_resolution_is_local_and_confirmation_is_mandatory(self):
        from src.publisher.http import PublisherAPI
        draft_id, _, data = self.prepare_resolution()
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.root / 'test.db', self.root, Mock()
        for confirmed in (False, True):
            payload = json.dumps({**data, 'id': draft_id, 'human_confirmed': confirmed}).encode()
            handler = Mock()
            handler.headers = {'Content-Length': str(len(payload)), 'Content-Type': 'application/json'}
            handler.rfile = io.BytesIO(payload)
            if confirmed:
                api.post(handler, '/api/publisher/resolve-draft')
                self.assertEqual(handler._json.call_args.args[0]['status'], 'ARCHIVED')
            else:
                with self.assertRaisesRegex(ValueError, 'CONFIRMATION_REQUIRED'):
                    api.post(handler, '/api/publisher/resolve-draft')
        self.assertFalse(api.worker.mock_calls)


if __name__ == '__main__':
    unittest.main()
