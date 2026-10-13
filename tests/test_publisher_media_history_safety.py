"""Prepared P0 regressions. Temporary SQLite only; no operational credentials.

Functional execution is intentionally deferred to the separate validation task.
"""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from src.store import connect, init_db, now_iso
from src.publisher.account_safety import AccountSafetyGuard
from src.publisher.library import _classify
from src.publisher.manual_resolution import resolve_draft
from src.publisher.media_history import assert_media_history_allowed, media_history_blocker
from src.publisher.queue_policy import (
    batch_queue_capabilities, draft_queue_capabilities, local_cancel_blocker,
    draft_queue_presentation, queue_preparation_context,
)
from src.publisher.reconciliation_evidence import record_reconciliation_evidence
from src.publisher.service import PublisherService, assert_upload_allowed, get_row, init_publisher, log


class MediaSafetyFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / 'isolated.db'
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, self.root)
        self.account = self.service.save_account({'name': 'Fixture account'})
        self.other_account = self.service.save_account({'name': 'Distinct account'})
        self.product = self.service.save_product({'name': 'Fixture', 'default_caption': 'Caption'})
        self.provider = Mock()
        with self.conn:
            for asset_id in range(1, 5):
                self.conn.execute(
                    'INSERT INTO publisher_media_assets(id,provider,source_account,remote_file_id,filename,'
                    'mime_type,checksum,size_bytes,status,created_at,updated_at) '
                    "VALUES (?,'google_drive','fixture',?,?,'video/mp4',?,100,'AVAILABLE',?,?)",
                    (asset_id, f'file-{asset_id}', f'FINAL_{asset_id}.mp4', f'hash-{asset_id}', now_iso(), now_iso()),
                )

    def batch(self, ids=(1,), account=None, service=None):
        return (service or self.service).create_remote_batch({
            'name': 'Isolated batch', 'media_asset_ids': list(ids), 'product_id': self.product,
            'account_id': account or self.account,
        })

    def execution(self, batch, *, failed=False):
        moment = datetime.now(timezone.utc)
        with self.conn:
            execution_id = self.conn.execute(
                'INSERT INTO publisher_batch_executions(batch_id,total,status,started_at,finished_at) '
                "VALUES (?,?,'PAUSED',?,?)",
                (batch['batch_id'], len(batch['draft_ids']), (moment - timedelta(minutes=1)).isoformat(),
                 (moment + timedelta(seconds=5)).isoformat()),
            ).lastrowid
            for position, draft_id in enumerate(batch['draft_ids'], 1):
                self.conn.execute(
                    'INSERT INTO publisher_execution_items(execution_id,draft_id,status,error_code,execution_position) '
                    'VALUES (?,?,?,?,?)',
                    (execution_id, draft_id, 'FAILED' if failed else 'WAITING_MANUAL',
                     'FAILED' if failed else 'MANUAL_ACTION_REQUIRED', position),
                )
                if failed:
                    self.conn.execute(
                        "UPDATE publisher_drafts SET status='FAILED',error_code='GOOGLE_USER_CREDENTIAL_REFRESH_FAILED' WHERE id=?",
                        (draft_id,),
                    )
                    log(self.conn, draft_id, 'prepare', 'FAILED', 'GOOGLE_USER_CREDENTIAL_REFRESH_FAILED')
        return execution_id

    def execution_state(self, execution_id):
        return dict(self.conn.execute('SELECT * FROM publisher_batch_executions WHERE id=?',
                                      (execution_id,)).fetchone())

    def remote_attempt(self, *, archived=False, ledger=True):
        batch = self.batch()
        draft_id = batch['draft_ids'][0]
        execution_id = self.execution(batch)
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drafts SET status=?,upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT',"
                "checklist_json=? WHERE id=?",
                ('ARCHIVED' if archived else 'MANUAL_ACTION_REQUIRED',
                 json.dumps(dict(file=True, upload=True, caption=True, settings=True)), draft_id),
            )
            self.conn.execute('UPDATE publisher_media_assets SET status=? WHERE id=1',
                              ('AVAILABLE' if archived else 'NEEDS_RECONCILIATION',))
            for step in ('UPLOAD_STARTED', 'TRANSFER_CONFIRMED', 'UPLOAD_CONFIRMED'):
                log(self.conn, draft_id, step, 'OK', 'Isolated remote-effect fixture')
        if ledger:
            guard = AccountSafetyGuard(self.conn)
            guard.start_mutation(self.account, f'draft:{draft_id}:VIDEO_UPLOAD', 'VIDEO_UPLOAD', draft_id=draft_id)
            guard.complete_mutation(self.account, f'draft:{draft_id}:VIDEO_UPLOAD')
        return batch, draft_id, execution_id

    def observe(self, draft_id, execution_id, *, outcome='REMOTE_STATE_UNKNOWN'):
        checkpoint = self.conn.execute(
            "SELECT MAX(id) FROM publisher_execution_logs WHERE draft_id=? AND step='UPLOAD_STARTED'", (draft_id,),
        ).fetchone()[0]
        moment = datetime.now(timezone.utc)
        return record_reconciliation_evidence(
            self.conn, draft_id=draft_id, media_asset_id=1, account_id=self.account,
            execution_id=execution_id, expected_account='fixture.handle', observed_account='fixture.handle',
            outcome=outcome, coverage_status='COMPLETE', source_surface='TIKTOK_STUDIO_DRAFT_LIST',
            candidate_count=1 if outcome == 'REMOTE_EXISTS_CONFIRMED' else 0,
            matched_candidate_fingerprint='strong-match' if outcome == 'REMOTE_EXISTS_CONFIRMED' else None,
            matcher_version='fixture-v1', remote_effect_checkpoint_id=checkpoint,
            verification_started_at=moment.isoformat(), verification_completed_at=moment.isoformat(),
            valid_until=(moment + timedelta(minutes=5)).isoformat(),
            evidence_payload={'current_listing': {'complete': True, 'count': 0, 'match_result': 'NO_CURRENT_MATCH'}}
                if outcome != 'REMOTE_EXISTS_CONFIRMED' else {},
        )

    def resolution(self, draft_id, decision='ABANDON', evidence_id=None):
        checkpoint = self.conn.execute(
            "SELECT MAX(id) FROM publisher_execution_logs WHERE draft_id=? AND step='UPLOAD_STARTED'", (draft_id,),
        ).fetchone()[0]
        return resolve_draft(self.conn, draft_id, dict(
            decision=decision, human_confirmed=True, reason='Explicit isolated fixture decision',
            acknowledge_remote_may_exist=True, media_asset_id=1, account_id=self.account,
            checkpoint_id=checkpoint, evidence_id=evidence_id,
        ))

    def legacy_successor(self, old_draft_id, *, account=None):
        # Simulate a reservation that existed before deployment; deliberately do
        # not call the new reservation guard to create this isolated legacy state.
        with self.conn:
            batch_id = self.conn.execute(
                "INSERT INTO publisher_batches(name,source,created_at) VALUES ('Legacy successor','MANUAL',?)", (now_iso(),),
            ).lastrowid
            draft_id = self.conn.execute(
                'INSERT INTO publisher_drafts(batch_id,product_id,account_id,media_asset_id,video_path,filename,caption,'
                'queue_position,headline_snapshot_json,created_at,updated_at) '
                "SELECT ?,product_id,?,media_asset_id,'',filename,caption,1,headline_snapshot_json,?,? "
                'FROM publisher_drafts WHERE id=?',
                (batch_id, account or self.account, now_iso(), now_iso(), old_draft_id),
            ).lastrowid
            self.conn.execute("UPDATE publisher_media_assets SET status='RESERVED' WHERE id=1")
        return {'batch_id': batch_id, 'draft_ids': [draft_id]}


class HistoricalMediaGuardTests(MediaSafetyFixture):
    def test_archived_confirmed_transfer_blocks_new_reservation_atomically(self):
        _, old, _ = self.remote_attempt(archived=True)
        before = get_row(self.conn, 'publisher_drafts', old)
        batches = self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0]
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.batch()
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0], batches)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', old), before)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'AVAILABLE')

    def test_abandon_releases_local_reservation_but_never_authorizes_reuse(self):
        _, old, execution = self.remote_attempt()
        self.observe(old, execution)
        before = get_row(self.conn, 'publisher_drafts', old)['headline_snapshot_json']
        result = self.resolution(old)
        self.assertEqual((result['status'], result['media_status']), ('ARCHIVED', 'AVAILABLE'))
        self.assertTrue(result['reuse_blocked'])
        self.assertFalse(result['retry_authorized'])
        self.assertEqual(get_row(self.conn, 'publisher_drafts', old)['headline_snapshot_json'], before)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.batch()
        self.assertFalse(self.provider.mock_calls)

    def test_legacy_reserved_successor_is_blocked_before_upload_and_cancellation(self):
        _, old, _ = self.remote_attempt(archived=True)
        successor = self.legacy_successor(old)
        draft_id = successor['draft_ids'][0]
        self.execution(successor, failed=True)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            assert_upload_allowed(self.conn, draft_id)
        self.assertEqual(local_cancel_blocker(self.conn, get_row(self.conn, 'publisher_drafts', draft_id)),
                         'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        caps = draft_queue_capabilities(self.conn, get_row(self.conn, 'publisher_drafts', draft_id),
                                        get_row(self.conn, 'publisher_media_assets', 1))
        self.assertFalse(caps['can_start'])
        self.assertEqual(caps['media_history_blocker']['draft_id'], old)
        self.assertEqual(self.service.cancel_batch(successor['batch_id'], eligible_only=True)['cancelled_drafts'], 0)

    def test_ledger_alone_blocks_even_if_old_flags_were_pristine(self):
        batch = self.batch()
        old = batch['draft_ids'][0]
        self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        AccountSafetyGuard(self.conn).start_mutation(self.account, 'old-intent', 'VIDEO_UPLOAD', draft_id=old)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.batch()

    def test_same_file_cannot_escape_history_by_switching_tiktok_account(self):
        _, old, _ = self.remote_attempt(archived=True)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.batch(account=self.other_account)
        successor = self.legacy_successor(old, account=self.other_account)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            assert_upload_allowed(self.conn, successor['draft_ids'][0])

    def test_distinct_source_identity_and_account_are_not_mixed(self):
        self.remote_attempt(archived=True)
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET remote_file_id='file-1',source_account='other-source' WHERE id=2")
        batch = self.batch((2,), account=self.other_account)
        assert_upload_allowed(self.conn, batch['draft_ids'][0])
        self.assertFalse(self.provider.mock_calls)

    def test_ledger_bound_to_wrong_account_fails_closed(self):
        _, old, _ = self.remote_attempt(archived=True)
        with self.conn:
            self.conn.execute('UPDATE account_mutation_ledger SET account_id=? WHERE draft_id=?', (self.other_account, old))
        blocker = media_history_blocker(self.conn, get_row(self.conn, 'publisher_media_assets', 1))
        self.assertEqual(blocker['code'], 'MEDIA_HISTORY_IDENTITY_UNPROVEN')

    def test_missing_or_stale_canonical_identity_fails_closed(self):
        asset = get_row(self.conn, 'publisher_media_assets', 1)
        for changes in ({'remote_file_id': ''}, {'source_account': 'wrong'}, {'provider': None}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'MEDIA_IDENTITY_UNPROVEN'):
                self.service.assert_media_available({**asset, **changes})

    def test_prepared_source_and_browser_account_must_still_match_current_draft(self):
        draft_id = self.batch()['draft_ids'][0]
        asset = get_row(self.conn, 'publisher_media_assets', 1)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_IDENTITY_UNPROVEN'):
            assert_media_history_allowed(self.conn, asset, draft_id=draft_id, account_id=self.other_account)
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET remote_file_id='changed-file' WHERE id=1")
        with self.assertRaisesRegex(ValueError, 'MEDIA_IDENTITY_UNPROVEN'):
            assert_media_history_allowed(self.conn, asset, draft_id=draft_id, account_id=self.account)

    def test_listing_absence_observation_and_acknowledgement_do_not_grant_upload(self):
        _, old, execution = self.remote_attempt()
        self.observe(old, execution, outcome='REMOTE_ABSENCE_CONFIRMED')
        result = self.resolution(old)
        self.assertTrue(result['reuse_blocked'])
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.batch()

    def test_reconciled_persistence_preserves_existing_review_but_not_second_upload(self):
        _, old, execution = self.remote_attempt()
        evidence_id = self.observe(old, execution, outcome='REMOTE_EXISTS_CONFIRMED')
        result = self.resolution(old, 'CONFIRM_EXISTS', evidence_id)
        self.assertEqual(result['status'], 'READY_FOR_REVIEW')
        asset = get_row(self.conn, 'publisher_media_assets', 1)
        self.assertIsNone(media_history_blocker(self.conn, asset, draft_id=old))
        self.assertIsNone(media_history_blocker(self.conn, asset, purpose='release'))
        with self.assertRaisesRegex(ValueError, 'DRAFT_ALREADY_PERSISTED'):
            assert_upload_allowed(self.conn, old)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_REUSE_NOT_AUTHORIZED'):
            self.service.assert_media_available(asset)
        self.assertFalse(self.provider.mock_calls)

    def test_cancelled_local_only_history_permits_new_draft(self):
        old = self.batch()
        self.execution(old, failed=True)
        self.service.cancel_batch(old['batch_id'], eligible_only=True)
        new = self.batch()
        assert_upload_allowed(self.conn, new['draft_ids'][0])

    def test_archiving_unknown_execution_and_pristine_flags_is_not_local_proof(self):
        old = self.batch()
        self.execution(old, failed=True)
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='ARCHIVED' WHERE id=?", (old['draft_ids'][0],))
            self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=1")
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            self.batch()

    def test_library_marks_archived_remote_history_as_attention(self):
        _, old, _ = self.remote_attempt(archived=True)
        asset = get_row(self.conn, 'publisher_media_assets', 1)
        blocker = media_history_blocker(self.conn, asset, purpose='reserve')
        item = _classify(dict(drive_file_id='file-1', name='FINAL_1.mp4', file_kind='VIDEO',
                             folder_operational_status='ACTIVE', media_asset_id=1,
                             media_status='AVAILABLE', media_history_blocker=blocker))
        self.assertEqual(item['status'], 'ATTENTION')
        self.assertIn(f'Draft #{old}', item['reason'])

    def test_mutation_guard_blocks_before_intent_or_fake_upload(self):
        _, old, _ = self.remote_attempt(archived=True)
        new = self.legacy_successor(old)['draft_ids'][0]
        guard = AccountSafetyGuard(self.conn)
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            guard.start_mutation(self.account, f'draft:{new}:VIDEO_UPLOAD', 'VIDEO_UPLOAD', draft_id=new,
                                 pre_mutation_guard=lambda: assert_upload_allowed(self.conn, new))
        self.assertIsNone(self.conn.execute('SELECT 1 FROM account_mutation_ledger WHERE draft_id=?', (new,)).fetchone())
        self.assertFalse(self.provider.mock_calls)

    def test_worker_enqueue_rechecks_a_legacy_successor_without_starting_worker(self):
        from src.publisher.worker import PublisherWorker
        _, old, _ = self.remote_attempt(archived=True)
        new = self.legacy_successor(old)['draft_ids'][0]
        worker = PublisherWorker.__new__(PublisherWorker)
        worker.assert_actions_allowed = Mock()
        worker.login_traces = {}
        worker.start = Mock()
        worker.provider_factory = self.provider
        with self.assertRaisesRegex(ValueError, 'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):
            worker.enqueue(self.conn, [new])
        worker.start.assert_not_called()
        self.assertFalse(self.provider.mock_calls)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', new)['status'], 'DRAFT')

    def test_successful_intent_immediately_protects_media_without_upload_checkpoint(self):
        draft_id = self.batch()['draft_ids'][0]
        AccountSafetyGuard(self.conn).start_mutation(
            self.account, 'guarded-intent', 'VIDEO_UPLOAD', draft_id=draft_id,
            pre_mutation_guard=lambda: assert_upload_allowed(self.conn, draft_id))
        blocker = media_history_blocker(self.conn, get_row(self.conn, 'publisher_media_assets', 1))
        self.assertEqual(blocker['code'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
        self.assertFalse(self.provider.mock_calls)


class LocalFailureCancellationTests(MediaSafetyFixture):
    def test_draft52_shape_cancels_and_terminalizes_without_retry_or_remote_delete(self):
        batch = self.batch()
        draft_id = batch['draft_ids'][0]
        execution_id = self.execution(batch, failed=True)
        before = get_row(self.conn, 'publisher_drafts', draft_id)['headline_snapshot_json']
        self.assertTrue(batch_queue_capabilities(self.conn, batch['batch_id'])['can_cancel_eligible'])
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual(result['cancelled_drafts'], 1)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'AVAILABLE')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['headline_snapshot_json'], before)
        item = self.conn.execute('SELECT status,error_code FROM publisher_execution_items WHERE execution_id=?',
                                 (execution_id,)).fetchone()
        self.assertEqual(tuple(item), ('FAILED', 'LOCAL_BATCH_CANCELLED'))
        self.assertEqual(self.execution_state(execution_id)['status'], 'COMPLETED')
        audit = json.loads(self.conn.execute("SELECT message FROM publisher_execution_logs WHERE draft_id=? AND step='BATCH_CANCELLED'",
                                            (draft_id,)).fetchone()[0])
        self.assertEqual(audit['previous_state'], 'FAILED')
        self.assertFalse(audit['retry_authorized'])
        self.assertFalse(self.provider.mock_calls)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0], 1)

    def test_failed_upload_or_transfer_unknown_never_releases(self):
        batch = self.batch((1, 2))
        self.execution(batch, failed=True)
        with self.conn:
            log(self.conn, batch['draft_ids'][0], 'UPLOAD_STARTED', 'OK', 'Uncertain upload')
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?", (batch['draft_ids'][1],))
        before = [get_row(self.conn, 'publisher_drafts', draft) for draft in batch['draft_ids']]
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual((result['cancelled_drafts'], result['blocked_drafts']), (0, 2))
        self.assertEqual([get_row(self.conn, 'publisher_drafts', draft) for draft in batch['draft_ids']], before)

    def test_mixed_execution_only_cancels_proven_local_item_and_remains_paused(self):
        batch = self.batch((1, 2))
        execution_id = self.execution(batch, failed=True)
        with self.conn:
            log(self.conn, batch['draft_ids'][1], 'UPLOAD_STARTED', 'OK', 'Unknown remote effect')
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual((result['cancelled_drafts'], result['blocked_drafts']), (1, 1))
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 2)['status'], 'RESERVED')
        self.assertEqual(self.execution_state(execution_id)['status'], 'PAUSED')

    def test_unknown_failure_or_missing_audit_is_not_proof(self):
        batch = self.batch((1, 2))
        self.execution(batch, failed=True)
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET error_code='UNKNOWN_FAILURE' WHERE id=?", (batch['draft_ids'][0],))
            self.conn.execute("DELETE FROM publisher_execution_logs WHERE draft_id=? AND step='prepare'", (batch['draft_ids'][1],))
        self.assertEqual(self.service.cancel_batch(batch['batch_id'], eligible_only=True)['cancelled_drafts'], 0)

    def test_incoherent_execution_or_active_worker_blocks_release(self):
        for asset_id, change in enumerate(("status='RUNNING'", 'total=2', 'finished_at=NULL'), 1):
            batch = self.batch((asset_id,))
            execution_id = self.execution(batch, failed=True)
            with self.conn:
                self.conn.execute(f'UPDATE publisher_batch_executions SET {change} WHERE id=?', (execution_id,))
            self.assertEqual(self.service.cancel_batch(batch['batch_id'], eligible_only=True)['cancelled_drafts'], 0)

    def test_audit_failure_rolls_back_release_and_item_terminalization(self):
        batch = self.batch()
        execution_id = self.execution(batch, failed=True)
        with patch('src.publisher.service.log', side_effect=RuntimeError('Audit failed')):
            with self.assertRaises(RuntimeError):
                self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', batch['draft_ids'][0])['status'], 'FAILED')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertEqual(self.execution_state(execution_id)['status'], 'PAUSED')

    def test_hidden_local_failure_has_guidance_without_restoring_or_cancelling(self):
        batch = self.batch()
        self.execution(batch, failed=True)
        draft_id = batch['draft_ids'][0]
        self.service.set_queue_visibility(dict(action='hide', target_type='BATCH', target_id=batch['batch_id']))
        draft = get_row(self.conn, 'publisher_drafts', draft_id)
        display = draft_queue_presentation(draft, get_row(self.conn, 'publisher_media_assets', 1),
                                           **queue_preparation_context(self.conn, draft_id))
        self.assertEqual(display['reason'], 'LOCAL_PREUPLOAD_FAILURE')
        self.assertTrue(batch_queue_capabilities(self.conn, batch['batch_id'])['can_cancel_eligible'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def test_queue_uses_existing_local_cancel_action_and_history_explanations(self):
        # Static UI contract; browser behavior is deferred alongside backend tests.
        repository = Path(__file__).resolve().parents[1]
        source = (repository / 'dashboard/publisher.js').read_text(encoding='utf-8')
        self.assertIn('Cancelar trabalho local e liberar mídia', source)
        self.assertIn('MEDIA_HISTORY_RECONCILIATION_REQUIRED', source)
        self.assertIn('eligible_only:true', source)
        self.assertIn("action==='hide'", source)


class MediaSafetyConcurrencyTests(MediaSafetyFixture):
    def parallel_services(self, action):
        ready = threading.Barrier(2)
        def run():
            conn = connect(self.db)
            try:
                ready.wait(timeout=5)
                return action(PublisherService(conn, self.root))
            finally:
                conn.close()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run) for _ in range(2)]
            return [future.result(timeout=10) for future in futures]

    def test_concurrent_reservations_have_one_winner_and_no_empty_batch(self):
        def reserve(service):
            try:
                return 'reserved', self.batch(service=service)
            except ValueError:
                return 'blocked', None
        results = self.parallel_services(reserve)
        self.assertEqual(sorted(result[0] for result in results), ['blocked', 'reserved'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_drafts WHERE status<>'ARCHIVED'").fetchone()[0], 1)

    def test_concurrent_local_cancellations_are_idempotent(self):
        batch = self.batch()
        self.execution(batch, failed=True)
        results = self.parallel_services(lambda service: service.cancel_batch(batch['batch_id'], eligible_only=True))
        self.assertEqual(sorted(result['cancelled_drafts'] for result in results), [0, 1])
        self.assertEqual(sum(result['already_cancelled'] for result in results), 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='BATCH_CANCELLED'").fetchone()[0], 1)

    def test_final_guard_and_ledger_intent_share_write_lock(self):
        draft_id = self.batch()['draft_ids'][0]
        other = sqlite3.connect(self.db, timeout=0.01)
        self.addCleanup(other.close)
        def guard():
            assert_upload_allowed(self.conn, draft_id)
            with self.assertRaises(sqlite3.OperationalError):
                other.execute('BEGIN IMMEDIATE')
            other.rollback()
        AccountSafetyGuard(self.conn).start_mutation(self.account, 'atomic-intent', 'VIDEO_UPLOAD',
                                                     draft_id=draft_id, pre_mutation_guard=guard)
        self.assertEqual(self.conn.execute("SELECT status FROM account_mutation_ledger WHERE mutation_key='atomic-intent'").fetchone()[0], 'STARTED')
        self.assertFalse(self.provider.mock_calls)


if __name__ == '__main__':
    unittest.main()
