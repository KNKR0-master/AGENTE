"""Isolated regression fixtures; every Drive operation uses a fake adapter."""

import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from src.publisher.batch_consumption import (
    ITEM, REQUEST, RECEIPT, batch_consumption_review, evaluate_batch_consumption, finalize_batch_consumption,
)
from src.publisher.lifecycle import cleanup_remote
from src.publisher.posting_review import register_batch_published
from src.publisher.queue_presentation import change_visibility
from src.publisher.service import get_row, log
from src.store import connect
from tests import test_publisher_remote as fixtures


class FakeIdentityDrive(fixtures.FakeMedia):
    def __init__(self):
        super().__init__()
        self.fail_ids = set()
        self.overrides = {}
        self.lookups = []

    def get_remote_metadata(self, file_id):
        self.lookups.append(file_id)
        if file_id in self.deleted:
            return None
        item = next((dict(item) for item in self.files if item['id'] == file_id), None)
        if item:
            item.update(self.overrides.get(file_id, {}))
        return item

    def delete_remote(self, asset):
        if asset['remote_file_id'] in self.fail_ids:
            raise RuntimeError('fake Drive failure')
        super().delete_remote(asset)
        return True


class BatchConsumptionTests(unittest.TestCase):
    setUp = fixtures.PublisherRemoteTests.setUp
    batch = fixtures.PublisherRemoteTests.batch
    def confirm(self, draft_id):
        from tests.publisher_security_fixture import confirm_independent_fake_identity
        confirm_independent_fake_identity(self,draft_id)

    def authorize(self, draft_id):
        from src.publisher.cleanup_authorization import authorize_cleanup,cleanup_identity_token
        return authorize_cleanup(self.conn,draft_id,human_confirmed=True,
                                 identity_token=cleanup_identity_token(self.conn,draft_id))

    def prepare(self, count=1, published=None):
        self.drive = FakeIdentityDrive()
        ids = self.batch(count)
        published = count if published is None else published
        for draft_id in ids[:published]:
            self.confirm(draft_id)
        self.batch_id = get_row(self.conn, 'publisher_drafts', ids[0])['batch_id']
        if published:
            register_batch_published(self.conn, self.batch_id,
                                     dict(draft_ids=ids[:published], human_confirmed=True))
        return ids

    def payload(self, ids=None, request='batch-consumption-request-0001'):
        review = batch_consumption_review(self.conn, self.batch_id)
        items = [dict(draft_id=item['draft_id'], identity_token=item['identity_token'])
                 for item in review['items'] if ids is None and item['can_evaluate']
                 or ids is not None and item['draft_id'] in ids]
        return dict(request_id=request, items=items, confirmed_count=len(items), drive_delete_confirmed=True)

    def finish(self, payload=None):
        return finalize_batch_consumption(self.conn, self.batch_id, payload or self.payload(),
                                          lambda _: self.drive, self.root)

    def test_four_published_one_abandoned_preserved(self):
        ids = self.prepare(5, published=4)
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='ARCHIVED' WHERE id=?", (ids[-1],))
        preview = batch_consumption_review(self.conn, self.batch_id)
        self.assertEqual((preview['counts']['eligible'], preview['counts']['abandoned']), (4, 1))
        result = self.finish(self.payload(ids))
        self.assertEqual((result['counts']['CONSUMED'], result['counts']['BLOCKED']), (4, 1))
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertNotIn('remote-4', self.drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 5)['status'], 'RESERVED')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[-1])['status'], 'ARCHIVED')

    def test_all_published_and_per_item_audit(self):
        ids = self.prepare(3)
        result = self.finish()
        self.assertEqual(result['status'], 'COMPLETE')
        self.assertEqual(result['counts']['CONSUMED'], 3)
        self.assertEqual(set(item['draft_id'] for item in result['results']), set(ids))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step=? AND status='CONSUMED'", (ITEM,)).fetchone()[0], 3)
        self.assertFalse(self.drive.downloads)
        self.assertTrue(all(get_row(self.conn, 'publisher_drafts', draft_id)['status'] == 'PUBLISHED' for draft_id in ids))

    def test_no_eligible_media(self):
        ids = self.prepare(2, published=0)
        result = self.finish(self.payload(ids))
        self.assertEqual((result['status'], result['counts']['BLOCKED']), ('PARTIAL', 2))
        self.assertFalse(self.drive.lookups)
        self.assertFalse(self.drive.deleted)

    def test_partially_consumed_batch(self):
        ids = self.prepare(3)
        self.assertTrue(cleanup_remote(self.conn, ids[0], self.drive, self.root, authorization_id=self.authorize(ids[0])))
        result = self.finish(self.payload(ids))
        self.assertEqual((result['counts']['CONSUMED'], result['counts']['ALREADY_CONSUMED']), (2, 1))
        self.assertEqual(len(self.drive.deleted), 3)

    def test_idempotent_request_does_not_repeat_remote_calls(self):
        self.prepare(2)
        data = self.payload()
        first = self.finish(data)
        lookups = list(self.drive.lookups)
        second = self.finish(data)
        self.assertEqual(first['results'], second['results'])
        self.assertEqual(self.drive.lookups, lookups)
        self.assertEqual(len(self.drive.deleted), 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs WHERE step=?', (REQUEST,)).fetchone()[0], 1)

    def test_drive_failure_is_partial_and_new_authorized_retry_reconciles(self):
        self.prepare(3)
        self.drive.fail_ids.add('remote-1')
        result = self.finish()
        self.assertEqual((result['status'], result['counts']['CONSUMED'], result['counts']['FAILED']), ('PARTIAL', 2, 1))
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 2)['status'], 'CONSUMED_PENDING_DELETE')
        self.drive.fail_ids.clear()
        retry = self.finish(self.payload(request='batch-consumption-request-0002'))
        self.assertEqual(retry['counts']['CONSUMED'], 1)
        self.assertEqual(len(self.drive.deleted), 3)

    def test_ambiguous_transfer_never_consumed(self):
        ids = self.prepare(2)
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?", (ids[1],))
        result = self.finish(self.payload(ids))
        self.assertEqual((result['counts']['CONSUMED'], result['counts']['BLOCKED']), (1, 1))
        self.assertNotIn('remote-1', self.drive.deleted)

    def test_local_identity_changed_after_preview_is_blocked(self):
        ids = self.prepare()
        data = self.payload()
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET remote_file_id='another-file' WHERE id=1")
        result = self.finish(data)
        self.assertEqual(result['results'][0]['reason'], 'CONSUMPTION_IDENTITY_CHANGED')
        self.assertFalse(self.drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[0])['status'], 'PUBLISHED')

    def test_account_identity_changed_after_preview_is_blocked(self):
        self.prepare()
        data = self.payload()
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET name='Outra conta' WHERE id=?", (self.account,))
        self.assertEqual(self.finish(data)['counts']['BLOCKED'], 1)
        self.assertFalse(self.drive.lookups)

    def test_remote_identity_mismatch_and_trash_preserve_media(self):
        self.prepare(3)
        self.drive.overrides['remote-0'] = dict(md5Checksum='wrong')
        self.drive.overrides['remote-1'] = dict(trashed=True)
        self.drive.overrides['remote-2'] = dict(parents=['different-source'])
        result = self.finish()
        self.assertEqual(result['counts']['BLOCKED'], 3)
        self.assertFalse(self.drive.deleted)

    def test_double_concurrent_requests_delete_once(self):
        self.prepare(2)
        data = self.payload()
        barrier = threading.Barrier(2)

        def finish():
            conn = connect(self.root / 'test.db')
            try:
                barrier.wait(timeout=5)
                return finalize_batch_consumption(conn, self.batch_id, data, lambda _: self.drive, self.root)
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: finish(), range(2)))
        self.assertTrue(all(result['status'] == 'COMPLETE' for result in results))
        self.assertEqual(sorted(self.drive.deleted), ['remote-0', 'remote-1'])

    def test_publication_confirmation_does_not_authorize_deletion(self):
        ids = self.prepare()
        self.assertFalse(self.drive.deleted)
        data = self.payload(ids)
        data.pop('drive_delete_confirmed')
        data['human_confirmed'] = True
        with self.assertRaisesRegex(ValueError, 'SEPARATE_DRIVE_DELETION_AUTHORIZATION_REQUIRED'):
            self.finish(data)
        self.assertFalse(self.drive.lookups)
        self.assertFalse(self.conn.execute('SELECT 1 FROM publisher_execution_logs WHERE step=?', (REQUEST,)).fetchone())

    def test_distinct_concurrent_authorizations_share_persisted_consumption(self):
        self.prepare(2)
        data = self.payload()
        barrier = threading.Barrier(2)

        def finish(index):
            conn = connect(self.root / 'test.db')
            try:
                barrier.wait(timeout=5)
                payload = dict(data, request_id=f'distinct-consumption-request-{index}')
                return finalize_batch_consumption(conn, self.batch_id, payload, lambda _: self.drive, self.root)
            finally:
                conn.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(finish, range(2)))
        self.assertEqual(sorted(result['counts']['CONSUMED'] for result in results), [0, 2])
        self.assertEqual(sorted(result['counts']['ALREADY_CONSUMED'] for result in results), [0, 2])
        self.assertEqual(sorted(self.drive.deleted), ['remote-0', 'remote-1'])

    def test_hidden_batch_only_changes_presentation(self):
        ids = self.prepare(2)
        before = self.payload()
        change_visibility(self.conn, 'hide', 'BATCH', self.batch_id)
        self.assertEqual(before, self.payload())
        self.assertFalse(self.drive.deleted)
        self.assertEqual(self.finish()['counts']['CONSUMED'], 2)
        self.assertTrue(self.conn.execute("SELECT 1 FROM publisher_queue_presentation_state WHERE target_type='BATCH' AND target_id=?", (self.batch_id,)).fetchone())
        self.assertTrue(all(get_row(self.conn, 'publisher_drafts', draft_id)['status'] == 'PUBLISHED' for draft_id in ids))

    def test_cancel_local_work_independent_of_consumption(self):
        ids = self.prepare(2, published=1)
        self.service.cancel_batch(self.batch_id, eligible_only=True)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[0])['status'], 'PUBLISHED')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[1])['status'], 'ARCHIVED')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 2)['status'], 'AVAILABLE')
        self.assertFalse(self.drive.deleted)
        self.assertEqual(self.finish()['counts']['CONSUMED'], 1)

    def test_404_before_any_delete_and_pending_intent_alone_are_ambiguous(self):
        ids = self.prepare(2)
        self.drive.deleted.extend(['remote-0', 'remote-1'])
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET status='CONSUMED_PENDING_DELETE',consumed_by_draft_id=? WHERE id=2", (ids[1],))
            log(self.conn, ids[1], 'REMOTE_DELETE_PENDING', 'PENDING', 'fake intent without acknowledgement')
        result = self.finish()
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual(result['counts']['CONSUMED'], 0)
        self.assertTrue(all(item['reason'] == 'REMOTE_ABSENCE_REQUIRES_RECONCILIATION' for item in result['results']))
        self.assertEqual(self.drive.deleted, ['remote-0', 'remote-1'])

    def test_receipt_reconciles_local_commit_failure_without_second_delete(self):
        self.prepare()
        with patch('src.publisher.lifecycle._mark_consumed', side_effect=RuntimeError('fake persistence failure')):
            self.assertEqual(self.finish()['counts']['FAILED'], 1)
        self.assertTrue(self.conn.execute('SELECT 1 FROM publisher_execution_logs WHERE step=?', (RECEIPT,)).fetchone())
        result = self.finish(self.payload(request='batch-consumption-request-0002'))
        self.assertEqual(result['counts']['CONSUMED'], 1)
        self.assertEqual(self.drive.deleted, ['remote-0'])

    def test_interrupted_running_request_is_not_replayed_automatically(self):
        ids = self.prepare()
        data = self.payload()
        with self.conn:
            log(self.conn, ids[0], REQUEST, 'AUTHORIZED', json.dumps(dict(data, batch_id=self.batch_id)))
            log(self.conn, ids[0], ITEM, 'RUNNING', json.dumps(dict(request_id=data['request_id'], batch_id=self.batch_id,
                                                                draft_id=ids[0], result='RUNNING')))
        result = self.finish(data)
        self.assertEqual(result['status'], 'PENDING')
        self.assertFalse(self.drive.deleted)
        self.assertEqual(result['consumption']['counts']['pending'], 1)

    def test_wrong_confirmation_count_and_reused_request_id_rejected(self):
        self.prepare(2)
        data = self.payload()
        with self.assertRaisesRegex(ValueError, 'CONFIRMED_MEDIA_COUNT_MISMATCH'):
            self.finish(dict(data, confirmed_count=1))
        self.finish(data)
        with self.assertRaisesRegex(ValueError, 'CONSUMPTION_REQUEST_ID_REUSED'):
            self.finish(dict(data, items=data['items'][:1], confirmed_count=1))

    def test_local_cleanup_failure_is_reported_after_remote_consumption(self):
        self.prepare()
        with patch('src.publisher.lifecycle._finish_local_cleanup', side_effect=PermissionError('fake local file busy')):
            result = self.finish()
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertTrue(result['results'][0]['local_cleanup_pending'])
        self.assertEqual(result['results'][0]['reason'], 'LOCAL_CLEANUP_PENDING')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED')
        self.assertEqual(self.drive.deleted, ['remote-0'])

    def test_http_finalization_uses_pipeline_and_requires_distinct_authorization(self):
        from src.publisher.http import PublisherAPI
        self.prepare(2)
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.root / 'test.db', self.root, Mock()
        api.worker.provider_factory = lambda _: self.drive
        handler = Mock()
        body = json.dumps(self.payload()).encode()
        handler.headers = {'Content-Length': str(len(body)), 'Content-Type': 'application/json'}
        handler.rfile = io.BytesIO(body)
        api.post(handler, f'/api/publisher/batches/{self.batch_id}/finalize-consumption')
        self.assertEqual(handler._json.call_args.args[0]['counts']['CONSUMED'], 2)
        self.assertFalse(api.worker.mock_calls)

    def test_evaluation_reads_identity_without_delete_intent_or_writes(self):
        self.prepare(3)
        self.drive.overrides['remote-1'] = dict(trashed=True)
        self.drive.overrides['remote-2'] = dict(name='different.mp4')
        before = self.conn.total_changes
        result = evaluate_batch_consumption(self.conn, self.batch_id, lambda _: self.drive)
        self.assertEqual((result['counts']['eligible'], result['counts']['blocked']), (1, 2))
        self.assertTrue(result['remote_read_only'])
        self.assertEqual(self.conn.total_changes, before)
        self.assertFalse(self.drive.deleted)
        self.assertFalse(self.conn.execute("SELECT 1 FROM publisher_execution_logs WHERE step='REMOTE_DELETE_PENDING'").fetchone())

    def test_remote_404_noop_is_not_a_delete_acknowledgement(self):
        self.prepare()
        with patch.object(self.drive, 'delete_remote', return_value=False):
            result = self.finish()
        self.assertEqual(result['counts']['CONSUMED'], 0)
        self.assertEqual(result['results'][0]['reason'], 'REMOTE_DELETE_ACKNOWLEDGEMENT_REQUIRED')
        self.assertFalse(self.conn.execute('SELECT 1 FROM publisher_execution_logs WHERE step=?', (RECEIPT,)).fetchone())

    def test_individual_local_cleanup_clears_batch_pending_presentation(self):
        ids = self.prepare()
        with patch('src.publisher.lifecycle._finish_local_cleanup', side_effect=PermissionError('fake busy file')):
            self.finish()
        self.assertEqual(batch_consumption_review(self.conn, self.batch_id)['counts']['local_cleanup_pending'], 1)
        cleanup_remote(self.conn, ids[0], self.drive, self.root, authorization_id=self.authorize(ids[0]))
        self.assertEqual(batch_consumption_review(self.conn, self.batch_id)['counts']['local_cleanup_pending'], 0)

    def test_interrupted_request_with_consumed_checkpoint_recovers_locally(self):
        ids = self.prepare()
        data = self.payload()
        cleanup_remote(self.conn, ids[0], self.drive, self.root, authorization_id=self.authorize(ids[0]))
        with self.conn:
            log(self.conn, ids[0], REQUEST, 'AUTHORIZED', json.dumps(dict(data, batch_id=self.batch_id)))
            log(self.conn, ids[0], ITEM, 'RUNNING', json.dumps(dict(request_id=data['request_id'], batch_id=self.batch_id,
                                                                draft_id=ids[0], result='RUNNING')))
        factory = Mock(side_effect=AssertionError('Consumed recovery must not create a Drive adapter'))
        result = finalize_batch_consumption(self.conn, self.batch_id, data, factory, self.root)
        self.assertEqual(result['status'], 'COMPLETE')
        factory.assert_not_called()
        self.assertEqual(self.drive.deleted, ['remote-0'])


if __name__ == '__main__':
    unittest.main()
