import unittest
import io
import json
from unittest.mock import patch, Mock

from src.publisher.posting_review import batch_review, register_batch_published
from src.publisher.service import get_row
from src.publisher.queue_policy import draft_queue_presentation
from tests import test_publisher_remote as fixtures


class BatchPostingReviewTests(unittest.TestCase):
    setUp = fixtures.PublisherRemoteTests.setUp
    batch = fixtures.PublisherRemoteTests.batch
    confirm = fixtures.PublisherRemoteTests.confirm

    def test_mixed_items_revalidated_and_unresolved_stays_visible(self):
        first, second, third = self.batch(3)
        self.confirm(first)
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',upload_status='TRANSFER_UNKNOWN' WHERE id=?", (second,))
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=(SELECT media_asset_id FROM publisher_drafts WHERE id=?)", (second,))
        batch_id = get_row(self.conn, 'publisher_drafts', first)['batch_id']
        before = batch_review(self.conn, batch_id)
        self.assertEqual((before['prepared'], before['attention'], before['queued']), (1, 1, 1))
        self.assertLess(before['prepared_percent'], 100)
        result = register_batch_published(self.conn, batch_id, dict(draft_ids=[first, second, third], human_confirmed=True))
        self.assertEqual((result['registered'], result['skipped'], result['requires_review']), (1, 2, 1))
        self.assertFalse(result['review']['review_complete'])
        self.assertEqual(get_row(self.conn, 'publisher_drafts', second)['status'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertFalse(self.provider.deleted)
        self.assertFalse(self.provider.downloads)

    def test_confirm_required_and_duplicate_submission_does_not_reapply(self):
        draft_id = self.batch()[0]
        self.confirm(draft_id)
        batch_id = get_row(self.conn, 'publisher_drafts', draft_id)['batch_id']
        with self.assertRaisesRegex(ValueError, 'CONFIRMATION_REQUIRED'):
            register_batch_published(self.conn, batch_id, dict(draft_ids=[draft_id]))
        data = dict(draft_ids=[draft_id], human_confirmed=True)
        self.assertEqual(register_batch_published(self.conn, batch_id, data)['registered'], 1)
        self.assertEqual(register_batch_published(self.conn, batch_id, data)['registered'], 0)
        self.assertTrue(batch_review(self.conn, batch_id)['review_complete'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='MANUAL_POSTING_REVIEW'").fetchone()[0], 1)

    def test_state_changed_since_preview_is_skipped_and_other_batch_not_touched(self):
        draft_id = self.batch()[0]
        self.confirm(draft_id)
        batch_id = get_row(self.conn, 'publisher_drafts', draft_id)['batch_id']
        self.assertEqual(batch_review(self.conn, batch_id)['eligible_ids'], [draft_id])
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=1")
        result = register_batch_published(self.conn, batch_id, dict(draft_ids=[draft_id, 999], human_confirmed=True))
        self.assertEqual(result['registered'], 0)
        self.assertEqual(result['skipped'], 2)

    def test_execution_truth_preserved_and_running_blocks(self):
        draft_id = self.batch()[0]
        self.confirm(draft_id)
        batch_id = get_row(self.conn, 'publisher_drafts', draft_id)['batch_id']
        with self.conn:
            execution = self.conn.execute("INSERT INTO publisher_batch_executions(batch_id,total,started_at,status) VALUES (?,1,'2026-10-06T00:00:00+00:00','RUNNING')", (batch_id,)).lastrowid
            self.conn.execute("INSERT INTO publisher_execution_items(execution_id,draft_id,status) VALUES (?,?,'SUCCEEDED')", (execution, draft_id))
        data = dict(draft_ids=[draft_id], human_confirmed=True)
        self.assertEqual(register_batch_published(self.conn, batch_id, data)['registered'], 0)
        with self.conn:
            self.conn.execute("UPDATE publisher_batch_executions SET status='PAUSED' WHERE id=?", (execution,))
        before = tuple(self.conn.execute('SELECT * FROM publisher_execution_items').fetchone())
        self.assertEqual(register_batch_published(self.conn, batch_id, data)['registered'], 1)
        self.assertEqual(tuple(self.conn.execute('SELECT * FROM publisher_execution_items').fetchone()), before)
        display = draft_queue_presentation(get_row(self.conn, 'publisher_drafts', draft_id), item_status='FAILED')
        self.assertEqual(display['label'], 'Publicado no Studio')

    def test_old_single_review_route_cannot_bypass_persistence_guard(self):
        draft_id = self.batch()[0]
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW' WHERE id=?", (draft_id,))
        with self.assertRaisesRegex(ValueError, 'PERSISTENCE_UNCONFIRMED'):
            self.service.bulk([draft_id], {}, 'published')

    def test_audit_failure_rolls_back_entire_local_registration(self):
        draft_id = self.batch()[0]
        self.confirm(draft_id)
        batch_id = get_row(self.conn, 'publisher_drafts', draft_id)['batch_id']
        with patch('src.publisher.posting_review.log', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                register_batch_published(self.conn, batch_id, dict(draft_ids=[draft_id], human_confirmed=True))
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['status'], 'READY_FOR_REVIEW')

    def test_http_review_and_registration_never_dispatch_worker_commands(self):
        from src.publisher.http import PublisherAPI
        draft_id = self.batch()[0]
        self.confirm(draft_id)
        batch_id = get_row(self.conn, 'publisher_drafts', draft_id)['batch_id']
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.root / 'test.db', self.root, Mock()
        handler = Mock()
        api.get(handler, {}, f'/api/publisher/batches/{batch_id}/review')
        self.assertEqual(handler._json.call_args.args[0]['eligible_ids'], [draft_id])
        body = json.dumps({'draft_ids': [draft_id], 'human_confirmed': True}).encode()
        handler.headers = {'Content-Length': str(len(body)), 'Content-Type': 'application/json'}
        handler.rfile = io.BytesIO(body)
        api.post(handler, f'/api/publisher/batches/{batch_id}/register-published')
        self.assertEqual(handler._json.call_args.args[0]['registered'], 1)
        self.assertFalse(api.worker.mock_calls)


if __name__ == '__main__':
    unittest.main()
