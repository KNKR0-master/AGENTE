import unittest
import json
from unittest.mock import patch

from tests import test_publisher_remote as remote
from src.publisher.queue_policy import draft_queue_presentation, queue_preparation_context, queue_preparation_diagnostic
from src.publisher.service import get_row, log
from src.publisher.worker import PublisherWorker


class LocalPreparationResumeTests(unittest.TestCase):
    setUp = remote.PublisherRemoteTests.setUp
    batch = remote.PublisherRemoteTests.batch

    def paused(self, auto_cleanup=False, count=1, failure=True):
        ids = self.batch(count)
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start'):
            execution = worker.enqueue(self.conn, ids, auto_cleanup=auto_cleanup)
        while not worker.commands.empty():
            worker.commands.get_nowait()
        with self.conn:
            self.conn.execute("UPDATE publisher_batch_executions SET status='PAUSED' WHERE id=?", (execution,))
            self.conn.execute("UPDATE publisher_execution_items SET status='WAITING_MANUAL',error_code='MANUAL_ACTION_REQUIRED' WHERE execution_id=?", (execution,))
            for draft_id in ids:
                draft = get_row(self.conn, 'publisher_drafts', draft_id)
                self.conn.execute("UPDATE publisher_drafts SET status='DRAFT' WHERE id=?", (draft_id,))
                self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=?", (draft['media_asset_id'],))
            if failure:
                log(self.conn, ids[0], 'HEADLINE_PREPARATION_FAILED', 'BLOCKED', 'HEADLINE_OUTPUT_CADENCE_INVALID')
        return worker, execution, ids

    def test_local_failure_can_resume_without_cleanup_and_keeps_audit(self):
        worker, execution, ids = self.paused(count=2)
        with patch.object(worker, 'start'):
            self.assertEqual(worker.resume_execution(self.conn, execution), execution)
        self.assertEqual([worker.commands.get_nowait() for _ in ids], [('prepare', (i, execution)) for i in ids])
        self.assertFalse(self.conn.execute('SELECT auto_cleanup FROM publisher_batch_executions WHERE id=?', (execution,)).fetchone()[0])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='HEADLINE_PREPARATION_FAILED'").fetchone()[0], 1)
        self.assertFalse(self.provider.downloads)
        self.assertFalse(self.provider.deleted)

    def test_local_failure_can_resume_with_cleanup(self):
        with self.assertRaisesRegex(ValueError,'AUTO_CLEANUP_DISABLED_USE_EXPLICIT_CONSUMPTION'):
            self.paused(auto_cleanup=True)
        self.assertFalse(self.provider.downloads or self.provider.deleted)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0],0)

    def test_no_local_failure_does_not_bypass_cleanup_guard(self):
        worker, execution, _ = self.paused(failure=False)
        with self.assertRaisesRegex(ValueError, 'PREUPLOAD_RESUME_NOT_PROVEN_SAFE'):
            worker.resume_execution(self.conn, execution)
        self.assertTrue(worker.commands.empty())

    def test_prior_remote_checkpoint_blocks_local_resume(self):
        worker, execution, ids = self.paused()
        with self.conn:
            log(self.conn, ids[0], 'UPLOAD_STARTED', 'OK', 'fixture')
        with self.assertRaisesRegex(ValueError, 'PREUPLOAD_RESUME_NOT_PROVEN_SAFE'):
            worker.resume_execution(self.conn, execution)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[0])['status'], 'DRAFT')

    def test_remote_mutation_ledger_blocks_even_without_upload_log(self):
        worker, execution, ids = self.paused()
        with self.conn:
            self.conn.execute("INSERT INTO account_mutation_ledger(account_id,mutation_key,mutation_type,draft_id,started_at) VALUES (?,'fixture','VIDEO_UPLOAD',?,'now')", (self.account, ids[0]))
        with self.assertRaisesRegex(ValueError, 'PREUPLOAD_RESUME_NOT_PROVEN_SAFE'):
            worker.resume_execution(self.conn, execution)

    def test_ambiguous_postupload_stays_blocked(self):
        worker, execution, ids = self.paused()
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?", (ids[0],))
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=1")
        with self.assertRaisesRegex(ValueError, 'PREUPLOAD_RESUME_NOT_PROVEN_SAFE'):
            worker.resume_execution(self.conn, execution)
        with self.conn:
            self.conn.execute('UPDATE publisher_batch_executions SET auto_cleanup=1 WHERE id=?', (execution,))
        with self.assertRaisesRegex(ValueError, 'AUTO_CLEANUP_DISABLED_USE_EXPLICIT_CONSUMPTION'):
            worker.resume_execution(self.conn, execution)

    def test_stale_failure_from_another_execution_is_not_permission(self):
        worker, execution, ids = self.paused()
        with self.conn:
            self.conn.execute("UPDATE publisher_execution_logs SET created_at='2000-01-01T00:00:00+00:00' WHERE step='HEADLINE_PREPARATION_FAILED'")
        with self.assertRaisesRegex(ValueError, 'PREUPLOAD_RESUME_NOT_PROVEN_SAFE'):
            worker.resume_execution(self.conn, execution)

    def test_new_local_ready_artifact_does_not_destroy_resume_evidence(self):
        worker, execution, ids = self.paused()
        with self.conn:
            log(self.conn, ids[0], 'HEADLINE_ARTIFACT_READY', 'OK', 'fixture')
        self.assertEqual(queue_preparation_context(self.conn, ids[0])['local_preparation_error'], '')
        with patch.object(worker, 'start'):
            worker.resume_execution(self.conn, execution)

    def test_snapshot_and_history_distinguish_local_failure_from_remote_persistence(self):
        _, execution, ids = self.paused()
        draft = next(d for d in self.service.snapshot()['drafts'] if d['id'] == ids[0])
        display = draft['queue_display']
        self.assertEqual(display['reason'], 'LOCAL_PREPARATION_FAILURE')
        self.assertEqual(display['technical_code'], 'HEADLINE_OUTPUT_CADENCE_INVALID')
        self.assertEqual(display['persistence_label'], '')
        self.assertIn('ainda n\u00e3o foi enviado', display['message'])
        with self.conn:
            self.conn.execute("UPDATE publisher_batch_executions SET status='COMPLETED' WHERE id=?", (execution,))
        item = self.service.execution_history()['executions'][0]['items'][0]
        self.assertEqual(item['queue_display'], display)
        with self.conn:
            log(self.conn, ids[0], 'UPLOAD_STARTED', 'OK', 'fixture')
        draft = next(d for d in self.service.snapshot()['drafts'] if d['id'] == ids[0])
        self.assertEqual(draft['queue_display']['persistence_label'], 'Persist\u00eancia n\u00e3o confirmada')
        self.assertNotEqual(draft['queue_display'].get('reason'), 'LOCAL_PREPARATION_FAILURE')

    def test_presentation_requires_effect_for_remote_warning(self):
        base = {'status': 'DRAFT', 'upload_status': 'NOT_STARTED', 'persistence_status': 'UNCONFIRMED'}
        self.assertEqual(draft_queue_presentation(base)['persistence_label'], '')
        self.assertEqual(draft_queue_presentation(base, remote_checkpoint=True)['persistence_label'], 'Persist\u00eancia n\u00e3o confirmada')
        unknown = draft_queue_presentation({**base, 'upload_status': 'TRANSFER_UNKNOWN'}, local_preparation_error='HEADLINE_OUTPUT_CADENCE_INVALID')
        self.assertEqual(unknown['label'], 'Envio a verificar')
        self.assertNotIn('LOCAL_PREPARATION_FAILURE', unknown.values())

    def test_attempt_diagnostic_and_batch_attention_do_not_rewrite_draft(self):
        _, execution, ids = self.paused()
        draft_id = ids[0]
        with self.conn:
            log(self.conn, draft_id, 'HEADLINE_RENDER_STARTED', 'OK', 'fixture')
            log(self.conn, draft_id, 'HEADLINE_STAGE', 'BLOCKED', json.dumps(dict(
                stage='encode', seconds=120.01, timeout_seconds=120, error_code='HEADLINE_PROCESS_TIMEOUT')))
            log(self.conn, draft_id, 'HEADLINE_PREPARATION_FAILED', 'BLOCKED', 'HEADLINE_PROCESS_TIMEOUT')
        before = get_row(self.conn, 'publisher_drafts', draft_id)
        changes = self.conn.total_changes
        snapshot = self.service.snapshot()
        draft = next(d for d in snapshot['drafts'] if d['id']==draft_id)
        diagnostic = draft['preparation_diagnostic']
        self.assertEqual(draft['status'], 'DRAFT')
        self.assertIsNone(draft['error_code'])
        self.assertEqual(diagnostic['error_code'], 'HEADLINE_PROCESS_TIMEOUT')
        self.assertEqual(diagnostic['failed_stage'], 'encode')
        self.assertEqual(diagnostic['latest_execution']['execution_id'], execution)
        self.assertEqual(diagnostic['latest_execution']['item_status'], 'WAITING_MANUAL')
        self.assertFalse(diagnostic['remote_effect_possible'])
        self.assertFalse(diagnostic['upload_started_recorded'])
        self.assertTrue(diagnostic['failure_log_id'])
        summary = snapshot['batches'][0]['review_summary']
        self.assertEqual((summary['attention'], summary['queued']), (1, 0))
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id), before)
        self.assertEqual(self.conn.total_changes, changes)
        self.assertTrue(queue_preparation_diagnostic(self.conn,
            {**before, 'media_status':'NEEDS_RECONCILIATION'})['remote_effect_possible'])
        with self.conn:
            log(self.conn, draft_id, 'TRANSFER_CONFIRMED', 'OK', 'fixture')
        draft = next(d for d in self.service.snapshot()['drafts'] if d['id']==draft_id)
        self.assertTrue(draft['preparation_diagnostic']['remote_effect_possible'])

    def test_new_attempt_clears_previous_failure_projection_without_deleting_audit(self):
        _, _, ids = self.paused()
        with self.conn:
            log(self.conn, ids[0], 'HEADLINE_RENDER_STARTED', 'OK', 'fixture resumed')
        draft = next(d for d in self.service.snapshot()['drafts'] if d['id']==ids[0])
        self.assertIsNone(draft['preparation_diagnostic']['error_code'])
        self.assertFalse(queue_preparation_context(self.conn, ids[0])['local_preparation_error'])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='HEADLINE_PREPARATION_FAILED'").fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
