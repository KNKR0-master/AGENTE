"""P0 cross-feature gate: temporary SQLite and fake remote adapters only."""
import io
import json
import sqlite3
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from tests import test_publisher_media_history_safety as safety
from tests import test_publisher_batch_consumption as consumption
from tests.test_publisher_batch_consumption import FakeIdentityDrive
from src.publisher.batch_consumption import batch_consumption_review, finalize_batch_consumption
from src.publisher.lifecycle import cleanup_remote, assert_cleanup_allowed
from src.publisher.media_history import media_history_blocker
from src.publisher.service import get_row, assert_upload_allowed, log
from src.store import connect, now_iso


class PublisherP0IntegrationTests(unittest.TestCase):
    setUp = safety.MediaSafetyFixture.setUp
    batch = safety.MediaSafetyFixture.batch
    execution = safety.MediaSafetyFixture.execution
    remote_attempt = safety.MediaSafetyFixture.remote_attempt
    observe = safety.MediaSafetyFixture.observe
    resolution = safety.MediaSafetyFixture.resolution
    legacy_successor = safety.MediaSafetyFixture.legacy_successor

    def test_D_archived_unreconciled_history_blocks_published_successor_consumption(self):
        # A legacy successor can already exist when this protection is deployed.
        # Persisting that successor does not reconcile a distinct older attempt.
        _, old, execution = self.remote_attempt()
        self.observe(old, execution)
        self.assertTrue(self.resolution(old)['reuse_blocked'])
        successor = self.legacy_successor(old)
        draft_id = successor['draft_ids'][0]
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET source_account='cecilia',remote_folder_id='ceci' WHERE id=1")
            self.conn.execute("UPDATE publisher_drafts SET status='PUBLISHED',persistence_status='PERSISTED',confirmed_at=?,tiktok_draft_persisted_at=?,tiktok_draft_url='https://www.tiktok.com/fixture/published',upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT' WHERE id=?", (timestamp, timestamp, draft_id))
        old_before = get_row(self.conn, 'publisher_drafts', old)
        evidence_before = self.history_evidence(old)
        asset = get_row(self.conn, 'publisher_media_assets', 1)
        blocker = media_history_blocker(self.conn, asset, draft_id=draft_id, account_id=self.account)
        self.assertEqual(blocker['code'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        self.assertEqual(blocker['draft_id'], old)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.batch()
        drive = FakeIdentityDrive()
        drive.files = [dict(id='file-1', name='FINAL_1.mp4', mimeType='video/mp4', size=100, md5Checksum='hash-1', parents=['ceci'])]
        review = batch_consumption_review(self.conn, successor['batch_id'])
        item = next(item for item in review['items'] if item['draft_id'] == draft_id)
        data = dict(request_id='p0-historical-consumption-0001', items=[dict(draft_id=draft_id, identity_token=item['identity_token'])], confirmed_count=1, drive_delete_confirmed=True)
        result = finalize_batch_consumption(self.conn, successor['batch_id'], data, lambda _: drive, self.root)
        actual = dict(category=item['category'], blocker=item['blocker'], result=result['results'][0]['result'], reason=result['results'][0]['reason'], fake_deletes=drive.deleted, media_status=get_row(self.conn, 'publisher_media_assets', 1)['status'], historical_blocker=blocker['code'])
        print('P0_CROSS_FEATURE_OBSERVED=' + json.dumps(actual, sort_keys=True))
        self.assertEqual(get_row(self.conn, 'publisher_drafts', old), old_before)
        self.assertEqual(self.history_evidence(old), evidence_before)
        self.assertEqual((item['category'], item['blocker']), ('blocked', 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'))
        self.assertEqual(result['results'][0]['reason'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        self.assertEqual((result['counts']['CONSUMED'], result['counts']['BLOCKED']), (0, 1), actual)
        self.assertFalse(drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def history_evidence(self, draft_id):
        return {table: [dict(row) for row in self.conn.execute(
            f'SELECT * FROM {table} WHERE draft_id=? ORDER BY id', (draft_id,))]
            for table in ('publisher_execution_logs', 'account_mutation_ledger', 'publisher_reconciliation_evidence')}

    def unsafe_successor(self, *, published=True, abandon=True):
        _, old, execution = self.remote_attempt(archived=not abandon)
        if abandon:
            self.observe(old, execution)
            self.assertTrue(self.resolution(old)['reuse_blocked'])
        successor = self.legacy_successor(old)
        draft_id = successor['draft_ids'][0]
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET source_account='cecilia',remote_folder_id='ceci' WHERE id=1")
            if published:
                timestamp = now_iso()
                self.conn.execute("UPDATE publisher_drafts SET status='PUBLISHED',persistence_status='PERSISTED',confirmed_at=?,tiktok_draft_persisted_at=?,tiktok_draft_url='https://www.tiktok.com/fixture/published',upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT' WHERE id=?", (timestamp, timestamp, draft_id))
        drive = FakeIdentityDrive()
        drive.files = [dict(id='file-1', name='FINAL_1.mp4', mimeType='video/mp4', size=100, md5Checksum='hash-1', parents=['ceci'])]
        return old, successor, drive

    def test_shared_cleanup_blocks_archived_and_abandoned_history_before_adapter(self):
        old, successor, drive = self.unsafe_successor()
        before = self.history_evidence(old)
        asset = get_row(self.conn, 'publisher_media_assets', 1)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            cleanup_remote(self.conn, successor['draft_ids'][0], drive, self.root)
        self.assertEqual(self.history_evidence(old), before)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1), asset)
        self.assertFalse(drive.lookups)
        self.assertFalse(drive.deleted)

    def test_archive_alone_still_blocks_cleanup(self):
        _, successor, drive = self.unsafe_successor(abandon=False)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            cleanup_remote(self.conn, successor['draft_ids'][0], drive, self.root)
        self.assertFalse(drive.lookups)
        self.assertFalse(drive.deleted)

    def test_pending_cleanup_and_remote_absence_cannot_clear_historical_risk(self):
        old, successor, drive = self.unsafe_successor()
        draft_id = successor['draft_ids'][0]
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET status='CONSUMED_PENDING_DELETE',consumed_by_draft_id=? WHERE id=1", (draft_id,))
            log(self.conn, draft_id, 'REMOTE_DELETE_PENDING', 'PENDING', 'Synthetic old cleanup intent')
        drive.deleted.append('file-1')  # Pre-existing fake absence, not a call.
        before = self.history_evidence(old)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            cleanup_remote(self.conn, draft_id, drive, self.root)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED_PENDING_DELETE')
        self.assertFalse(drive.lookups)
        self.assertEqual(drive.deleted, ['file-1'])
        self.assertEqual(self.history_evidence(old), before)

    def test_consumed_local_recovery_does_not_destroy_files_when_history_is_uncertain(self):
        _, successor, drive = self.unsafe_successor()
        draft_id = successor['draft_ids'][0]
        temp = self.root / 'data/publisher/temp/retained.mp4'
        temp.parent.mkdir(parents=True)
        temp.write_bytes(b'synthetic')
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET status='CONSUMED',remote_deleted_at=?,consumed_by_draft_id=? WHERE id=1", (now_iso(), draft_id))
            self.conn.execute('UPDATE publisher_drafts SET temp_path=? WHERE id=?', (str(temp), draft_id))
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            cleanup_remote(self.conn, draft_id, drive, self.root)
        self.assertTrue(temp.exists())
        self.assertFalse(drive.lookups)
        self.assertFalse(drive.deleted)

    def test_individual_http_confirmation_cannot_queue_unsafe_cleanup(self):
        from src.publisher.http import PublisherAPI
        _, successor, drive = self.unsafe_successor()
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.db, self.root, Mock()
        body = json.dumps(dict(id=successor['draft_ids'][0], human_confirmed=True)).encode()
        handler = Mock(headers={'Content-Length': str(len(body)), 'Content-Type': 'application/json'}, rfile=io.BytesIO(body))
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            api.post(handler, '/api/publisher/cleanup')
        api.worker.submit.assert_not_called()
        self.assertFalse(drive.deleted)

    def test_bulk_http_explicit_drive_authorization_cannot_bypass_history(self):
        from src.publisher.http import PublisherAPI
        old, successor, drive = self.unsafe_successor()
        item = batch_consumption_review(self.conn, successor['batch_id'])['items'][0]
        data = dict(request_id='p0-authorized-http-0001', items=[dict(draft_id=item['draft_id'], identity_token=item['identity_token'])], confirmed_count=1, drive_delete_confirmed=True, human_confirmed=True)
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.db, self.root, Mock()
        api.worker.provider_factory = Mock(return_value=drive)
        body = json.dumps(data).encode()
        handler = Mock(headers={'Content-Length': str(len(body)), 'Content-Type': 'application/json'}, rfile=io.BytesIO(body))
        before = self.history_evidence(old)
        api.post(handler, f"/api/publisher/batches/{successor['batch_id']}/finalize-consumption")
        result = handler._json.call_args.args[0]
        self.assertEqual((result['status'], result['counts']['BLOCKED'], result['counts']['CONSUMED']), ('PARTIAL', 1, 0))
        self.assertEqual(result['results'][0]['reason'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        api.worker.provider_factory.assert_not_called()
        self.assertEqual(self.history_evidence(old), before)
        self.assertFalse(drive.deleted)

    def test_legacy_reservation_cannot_reupload_or_cancel_away_historical_risk(self):
        old, successor, drive = self.unsafe_successor(published=False)
        self.execution(successor, failed=True)
        before = self.history_evidence(old)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            assert_upload_allowed(self.conn, successor['draft_ids'][0])
        result = self.service.cancel_batch(successor['batch_id'], eligible_only=True)
        self.assertEqual((result['cancelled_drafts'], result['blocked_drafts']), (0, 1))
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertEqual(self.history_evidence(old), before)
        self.assertFalse(drive.deleted)

    def test_library_delete_cannot_bypass_archived_history(self):
        self.remote_attempt(archived=True)
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET source_account='cecilia',remote_folder_id='ceci' WHERE id=1")
            self.conn.execute("INSERT INTO drive_folders(drive_folder_id,name,relative_path,created_at,updated_at) VALUES ('ceci','Fixture','/Fixture',?,?)", (timestamp, timestamp))
            self.conn.execute("INSERT INTO drive_files(drive_file_id,drive_folder_id,name,mime_type,size,file_kind,last_seen_at,created_at,updated_at) VALUES ('file-1','ceci','FINAL_1.mp4','video/mp4',100,'VIDEO',?,?,?)", (timestamp, timestamp, timestamp))
        provider = Mock()
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.service.delete_library_media(dict(drive_file_id='file-1', filename='FINAL_1.mp4', size=100, human_confirmed=True), provider)
        self.assertFalse(provider.mock_calls)
        from src.publisher.http import PublisherAPI
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.db, self.root, Mock()
        body = json.dumps(dict(drive_file_id='file-1', filename='FINAL_1.mp4', size=100, human_confirmed=True)).encode()
        handler = Mock(headers={'Content-Length': str(len(body)), 'Content-Type': 'application/json'}, rfile=io.BytesIO(body))
        factory = Mock(side_effect=AssertionError('A blocked file must not create a real adapter'))
        with patch('src.publisher.http.media_provider', factory):
            with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
                api.post(handler, '/api/publisher/media-delete')
        factory.assert_not_called()
        self.assertIsNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='file-1'").fetchone()[0])

    def test_shared_guard_blocks_even_adapter_construction_for_unsafe_history(self):
        _, successor, drive = self.unsafe_successor()
        factory = Mock(side_effect=AssertionError('No Drive reads or authentication on blocked media'))
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            cleanup_remote(self.conn, successor['draft_ids'][0], root=self.root, provider_factory=factory)
        factory.assert_not_called()
        self.assertFalse(drive.deleted)


class PublisherP0ConsumptionIntegrationTests(unittest.TestCase):
    setUp = consumption.BatchConsumptionTests.setUp
    batch = consumption.BatchConsumptionTests.batch
    confirm = consumption.BatchConsumptionTests.confirm
    prepare = consumption.BatchConsumptionTests.prepare
    payload = consumption.BatchConsumptionTests.payload
    finish = consumption.BatchConsumptionTests.finish

    def historical_draft(self, draft_id, *, remote=False, reconciled=False):
        timestamp = now_iso()
        with self.conn:
            old = self.conn.execute(
                "INSERT INTO publisher_drafts(batch_id,product_id,account_id,media_asset_id,video_path,filename,caption,status,created_at,updated_at) SELECT batch_id,product_id,account_id,media_asset_id,'',filename,caption,'ARCHIVED',?,? FROM publisher_drafts WHERE id=?",
                (timestamp, timestamp, draft_id)).lastrowid
            if remote:
                self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT' WHERE id=?", (old,))
                log(self.conn, old, 'UPLOAD_STARTED', 'OK', 'Synthetic historical transfer')
            if reconciled:
                self.conn.execute("UPDATE publisher_drafts SET persistence_status='PERSISTED',confirmed_at=?,tiktok_draft_persisted_at=?,tiktok_draft_url='https://www.tiktok.com/fixture/reconciled' WHERE id=?", (timestamp, timestamp, old))
        return old

    def evidence(self, draft_id):
        return {table: [dict(row) for row in self.conn.execute(f'SELECT * FROM {table} WHERE draft_id=? ORDER BY id', (draft_id,))]
                for table in ('publisher_execution_logs', 'account_mutation_ledger', 'publisher_reconciliation_evidence')}

    def test_partial_batch_isolates_blocked_eligible_abandoned_and_consumed_items(self):
        ids = self.prepare(5, published=4)
        old = self.historical_draft(ids[0], remote=True)
        with self.conn:
            self.conn.execute('UPDATE publisher_drafts SET batch_id=NULL WHERE id=?', (old,))
            self.conn.execute("UPDATE publisher_drafts SET status='ARCHIVED' WHERE id=?", (ids[4],))
        self.assertTrue(cleanup_remote(self.conn, ids[3], self.drive, self.root))
        before = self.evidence(old)
        data = self.payload(ids)
        result = self.finish(data)
        self.assertEqual(result['status'], 'PARTIAL')
        self.assertEqual((result['counts']['BLOCKED'], result['counts']['CONSUMED'], result['counts']['ALREADY_CONSUMED']), (2, 2, 1))
        results = {item['draft_id']: item for item in result['results']}
        self.assertEqual(results[ids[0]]['reason'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        self.assertEqual(results[ids[4]]['reason'], 'ABANDONED_MEDIA_PROTECTED')
        self.assertEqual(sorted(self.drive.deleted), ['remote-1', 'remote-2', 'remote-3'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 5)['status'], 'RESERVED')
        self.assertEqual(self.evidence(old), before)
        lookups = list(self.drive.lookups)
        self.assertEqual(self.finish(data)['results'], result['results'])
        self.assertEqual(self.drive.lookups, lookups)
        self.assertEqual(len(self.drive.deleted), 3)

    def test_reconciled_historical_identity_consumes_but_never_grants_reupload(self):
        ids = self.prepare()
        old = self.historical_draft(ids[0], remote=True, reconciled=True)
        before = self.evidence(old)
        data = self.payload(ids)
        self.assertEqual(self.finish(data)['counts']['CONSUMED'], 1)
        self.assertEqual(self.finish(data)['counts']['CONSUMED'], 1)
        self.assertEqual(self.drive.deleted, ['remote-0'])
        self.assertEqual(self.evidence(old), before)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_REUSE_NOT_AUTHORIZED'):
            self.batch()

    def test_historical_guard_requires_bound_consumption_identity(self):
        ids = self.prepare()
        asset = get_row(self.conn, 'publisher_media_assets', 1)
        self.assertEqual(media_history_blocker(self.conn, asset, purpose='consume')['code'], 'MEDIA_HISTORY_IDENTITY_UNPROVEN')
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET source_account='' WHERE id=1")
        self.assertEqual(self.finish(self.payload(ids))['counts']['BLOCKED'], 1)
        self.assertFalse(self.drive.deleted)

    def test_safe_local_cancellation_keeps_legitimate_consumption_available(self):
        ids = self.prepare(2, published=1)
        result = self.service.cancel_batch(self.batch_id, eligible_only=True)
        self.assertEqual(result['cancelled_drafts'], 1)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 2)['status'], 'AVAILABLE')
        self.assertFalse(self.drive.deleted)
        self.assertEqual(self.finish(self.payload([ids[0]]))['counts']['CONSUMED'], 1)
        self.assertEqual(self.drive.deleted, ['remote-0'])

    def test_history_committed_after_preview_is_rechecked_before_any_adapter_call(self):
        ids = self.prepare()
        data = self.payload(ids)
        self.historical_draft(ids[0], remote=True)
        self.assertEqual(self.finish(data)['counts']['BLOCKED'], 1)
        self.assertFalse(self.drive.lookups)
        self.assertFalse(self.drive.deleted)

    def test_history_arriving_between_preflight_and_claim_blocks_individual_cleanup(self):
        ids = self.prepare()
        old = self.historical_draft(ids[0])
        other = connect(self.root / 'test.db')
        self.addCleanup(other.close)
        calls = []
        def preflight(*args, **kwargs):
            result = assert_cleanup_allowed(*args, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                with other:
                    log(other, old, 'UPLOAD_STARTED', 'OK', 'Synthetic competing historical write')
            return result
        factory = Mock(return_value=self.drive)
        with patch('src.publisher.lifecycle.assert_cleanup_allowed', side_effect=preflight):
            with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
                cleanup_remote(self.conn, ids[0], root=self.root, provider_factory=factory)
        factory.assert_not_called()
        self.assertFalse(self.drive.lookups)
        self.assertFalse(self.drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def test_identity_change_between_preflight_and_claim_fails_closed(self):
        ids = self.prepare()
        other = connect(self.root / 'test.db')
        self.addCleanup(other.close)
        calls = []
        def preflight(*args, **kwargs):
            result = assert_cleanup_allowed(*args, **kwargs)
            calls.append(1)
            if len(calls) == 1:
                with other:
                    other.execute("UPDATE publisher_media_assets SET remote_file_id='changed-source' WHERE id=1")
            return result
        with patch('src.publisher.lifecycle.assert_cleanup_allowed', side_effect=preflight):
            with self.assertRaisesRegex(ValueError, 'MEDIA_IDENTITY_UNPROVEN'):
                cleanup_remote(self.conn, ids[0], self.drive, self.root)
        self.assertFalse(self.drive.lookups)
        self.assertFalse(self.drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def test_history_arriving_after_delete_intent_blocks_delete_and_completion(self):
        ids = self.prepare()
        old = self.historical_draft(ids[0])
        other = connect(self.root / 'test.db')
        self.addCleanup(other.close)
        from src.publisher import lifecycle
        original = lifecycle._has_cleanup_intent
        inserted = []
        def after_intent(*args):
            result = original(*args)
            if not inserted and not self.conn.in_transaction:
                with other:
                    log(other, old, 'UPLOAD_STARTED', 'OK', 'Synthetic risk after committed cleanup intent')
                inserted.append(True)
            return result
        with patch('src.publisher.lifecycle._has_cleanup_intent', side_effect=after_intent):
            with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
                cleanup_remote(self.conn, ids[0], self.drive, self.root)
        self.assertEqual(len(self.drive.lookups), 1)
        self.assertFalse(self.drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED_PENDING_DELETE')
        self.assertIsNone(get_row(self.conn, 'publisher_media_assets', 1)['remote_deleted_at'])

    def test_write_lock_protects_remote_delete_and_consumption_checkpoint(self):
        ids = self.prepare()
        old = self.historical_draft(ids[0])
        other = sqlite3.connect(self.root / 'test.db', timeout=0.01)
        self.addCleanup(other.close)
        original = self.drive.delete_remote
        observed = []
        def checked_delete(asset):
            self.assertTrue(self.conn.in_transaction)
            self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED_PENDING_DELETE')
            with self.assertRaises(sqlite3.OperationalError):
                other.execute("INSERT INTO publisher_execution_logs(draft_id,step,status,message,created_at) VALUES (?,'UPLOAD_STARTED','OK','competing write',?)", (old, now_iso()))
            other.rollback()
            observed.append(True)
            return original(asset)
        with patch.object(self.drive, 'delete_remote', side_effect=checked_delete):
            self.assertEqual(self.finish()['counts']['CONSUMED'], 1)
        self.assertEqual(observed, [True])
        self.assertEqual(self.drive.deleted, ['remote-0'])

    def test_distinct_concurrent_requests_preserve_blocked_history_and_delete_safe_once(self):
        ids = self.prepare(2)
        self.historical_draft(ids[0], remote=True)
        data = self.payload(ids)
        barrier = threading.Barrier(2)
        def finish(index):
            conn = connect(self.root / 'test.db')
            try:
                barrier.wait(timeout=5)
                return finalize_batch_consumption(conn, self.batch_id, dict(data, request_id=f'p0-concurrent-request-{index}'), lambda _: self.drive, self.root)
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(finish, range(2)))
        self.assertTrue(all(result['status'] == 'PARTIAL' and result['counts']['BLOCKED'] == 1 for result in results))
        self.assertEqual(self.drive.deleted, ['remote-1'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 2)['status'], 'CONSUMED')

    def test_batch_guard_rechecks_history_after_intent_and_reports_explicit_reason(self):
        ids = self.prepare()
        old = self.historical_draft(ids[0])
        other = connect(self.root / 'test.db')
        self.addCleanup(other.close)
        from src.publisher import lifecycle
        original = lifecycle._has_cleanup_intent
        inserted = []
        def after_intent(*args):
            result = original(*args)
            if not inserted and not self.conn.in_transaction:
                with other:
                    log(other, old, 'UPLOAD_STARTED', 'OK', 'Synthetic risk between batch checkpoints')
                inserted.append(True)
            return result
        with patch('src.publisher.lifecycle._has_cleanup_intent', side_effect=after_intent):
            result = self.finish(self.payload(ids))
        self.assertEqual((result['status'], result['counts']['BLOCKED']), ('PARTIAL', 1))
        self.assertEqual(result['results'][0]['reason'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        self.assertFalse(self.drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED_PENDING_DELETE')

    def test_interrupted_batch_claim_keeps_history_reason_without_retry_or_false_completion(self):
        ids = self.prepare()
        data = self.payload(ids)
        with self.conn:
            log(self.conn, ids[0], consumption.REQUEST, 'AUTHORIZED', json.dumps(dict(data, batch_id=self.batch_id)))
            log(self.conn, ids[0], consumption.ITEM, 'RUNNING', json.dumps(dict(request_id=data['request_id'], batch_id=self.batch_id, draft_id=ids[0], result='RUNNING')))
        self.historical_draft(ids[0], remote=True)
        result = self.finish(data)
        self.assertEqual((result['status'], result['counts']['RUNNING'], result['counts']['CONSUMED']), ('PENDING', 1, 0))
        self.assertEqual(result['results'][0]['reason'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        item = next(item for item in result['consumption']['items'] if item['draft_id'] == ids[0])
        self.assertEqual((item['category'], item['blocker']), ('blocked', 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'))
        self.assertTrue(result['consumption']['pending_requests'])
        self.assertFalse(self.drive.lookups)
        self.assertFalse(self.drive.deleted)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def test_adapter_factory_runs_only_under_validated_write_lock_and_is_skipped_on_replay(self):
        ids = self.prepare()
        other = sqlite3.connect(self.root / 'test.db', timeout=0.01)
        self.addCleanup(other.close)
        created = []
        def factory(asset):
            self.assertTrue(self.conn.in_transaction)
            self.assertIsNone(media_history_blocker(self.conn, asset, draft_id=ids[0], account_id=self.account, purpose='consume'))
            with self.assertRaises(sqlite3.OperationalError):
                other.execute('BEGIN IMMEDIATE')
            other.rollback()
            created.append(asset['id'])
            return self.drive
        self.assertTrue(cleanup_remote(self.conn, ids[0], root=self.root, provider_factory=factory))
        self.assertEqual(created, [1])
        self.assertEqual(self.drive.deleted, ['remote-0'])
        replay_factory = Mock(side_effect=AssertionError('Consumed local recovery must not construct Drive'))
        self.assertTrue(cleanup_remote(self.conn, ids[0], root=self.root, provider_factory=replay_factory))
        replay_factory.assert_not_called()
        self.assertEqual(self.drive.deleted, ['remote-0'])


if __name__ == '__main__':
    unittest.main()
