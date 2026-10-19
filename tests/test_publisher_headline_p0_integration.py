"""Headline/P0 boundary regressions with synthetic video and disposable SQLite."""
import json
import sqlite3
import unittest
from contextlib import closing
from unittest.mock import Mock, patch

from tests import test_publisher_headline_preparation as preparation
from tests import test_publisher_local_preparation_resume as resume
from tests.publisher_security_fixture import promote_prepared_fake
from src.publisher import headlines, persistence_identity
from src.publisher.cleanup_authorization import authorize_cleanup, cleanup_identity_token
from src.publisher.headline_tools import RenderError, sha256
from src.publisher.lifecycle import cleanup_remote, confirm_received
from src.publisher.service import assert_upload_allowed, get_row, log
from src.publisher.upload_preparation import prepare_upload_source
from src.publisher.worker import PreparationError, restore_pre_upload_failure


class HeadlineP0IntegrationTests(unittest.TestCase):
    setUp_fixture = preparation.HeadlinePreparationTests.setUp_fixture
    setUp = preparation.HeadlinePreparationTests.setUp
    batch = preparation.HeadlinePreparationTests.batch
    configure = preparation.HeadlinePreparationTests.configure
    enqueue = preparation.HeadlinePreparationTests.enqueue
    local_prepare = preparation.HeadlinePreparationTests.local_prepare
    paused = resume.LocalPreparationResumeTests.paused

    @classmethod
    def setUpClass(cls):
        preparation.HeadlinePreparationTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        preparation.HeadlinePreparationTests.tearDownClass.__func__(cls)

    def prepared(self):
        self.configure()
        draft_id = self.batch()[0]
        self.enqueue([draft_id])
        return draft_id, self.local_prepare(draft_id)

    def assert_no_remote_effect(self, draft_id):
        self.assertFalse(self.uploads or self.provider.deleted)
        self.file_input.set_input_files.assert_not_called()
        self.assertFalse(self.conn.execute(
            "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
            "AND step IN ('UPLOAD_STARTED','SAVE_DRAFT','PERSISTENCE_IDENTITY_CONFIRMED',"
            "'DRIVE_CLEANUP_AUTHORIZED')", (draft_id,)).fetchone())
        self.assertFalse(self.conn.execute('SELECT 1 FROM account_mutation_ledger').fetchone())

    def test_timeout_recovery_keeps_snapshot_and_unconfirmed_without_upload(self):
        self.configure()
        draft_id = self.batch()[0]
        execution = self.enqueue([draft_id])
        frozen = get_row(self.conn, 'publisher_drafts', draft_id)['headline_snapshot_json']
        source_hash = sha256(self.source)
        self.renderer.encode_timeout = .001
        with self.assertRaises(PreparationError):
            self.worker.prepare(None, self.conn, draft_id)
        self.assertTrue(restore_pre_upload_failure(self.conn, draft_id))
        self.worker.finish_item(self.conn, draft_id, 'WAITING_MANUAL', 'MANUAL_ACTION_REQUIRED')
        draft = next(d for d in self.service.snapshot()['drafts'] if d['id'] == draft_id)
        self.assertEqual(draft['status'], 'DRAFT')
        self.assertIsNone(draft['error_code'])
        self.assertEqual(draft['preparation_diagnostic']['failed_stage'], 'encode')
        self.assertEqual(draft['preparation_diagnostic']['error_code'], 'HEADLINE_PROCESS_TIMEOUT')
        self.assertEqual(draft['preparation_diagnostic']['latest_execution']['execution_id'], execution)
        self.assertFalse(draft['preparation_diagnostic']['remote_effect_possible'])
        with patch.object(self.worker, 'start'):
            self.worker.resume_execution(self.conn, execution)
        self.renderer.encode_timeout = 600
        prepared = self.local_prepare(draft_id)
        prepared.validate(self.conn, get_row(self.conn, 'publisher_drafts', draft_id))
        row = get_row(self.conn, 'publisher_drafts', draft_id)
        self.assertEqual(prepared.kind, 'HEADLINE_DERIVED')
        self.assertEqual(row['headline_snapshot_json'], frozen)
        self.assertEqual(sha256(self.source), source_hash)
        self.assertEqual(row['persistence_status'], 'UNCONFIRMED')
        self.assertIsNone(row['confirmed_at'])
        with self.assertRaises(ValueError):
            cleanup_remote(self.conn, draft_id, self.provider, self.root)
        self.assert_no_remote_effect(draft_id)

    def test_ready_headline_does_not_bypass_new_remote_history_or_release_media(self):
        draft_id, prepared = self.prepared()
        with self.conn:
            log(self.conn, draft_id, 'UPLOAD_STARTED', 'OK', 'Synthetic uncertain remote effect')
        before = get_row(self.conn, 'publisher_drafts', draft_id)
        media_before = get_row(self.conn, 'publisher_media_assets', before['media_asset_id'])
        factory = Mock(side_effect=AssertionError('Historical guard must precede cache lookup'))
        with self.assertRaisesRegex(ValueError, 'UPLOAD_RECONCILIATION_REQUIRED'):
            prepare_upload_source(self.conn, before, prepared.source_path, factory)
        with self.assertRaisesRegex(ValueError, 'UPLOAD_RECONCILIATION_REQUIRED'):
            self.worker.prepare(None, self.conn, draft_id)
        factory.assert_not_called()
        self.assertFalse(restore_pre_upload_failure(self.conn, draft_id))
        result = self.service.cancel_batch(before['batch_id'], eligible_only=True)
        self.assertEqual(result['blocked_drafts'], 1)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', before['media_asset_id']), media_before)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['persistence_status'], 'UNCONFIRMED')
        self.assertEqual(sha256(prepared.path), prepared.artifact_sha256)
        self.assertFalse(self.uploads or self.provider.deleted)
        self.file_input.set_input_files.assert_not_called()

    def test_valid_cache_cannot_hide_artifact_identity_change(self):
        draft_id, prepared = self.prepared()
        digest = sha256(prepared.path)
        with self.conn:
            self.conn.execute('UPDATE publisher_derived_media_artifacts SET renderer_version=? WHERE id=?',
                              ('incompatible-synthetic-renderer', prepared.artifact_id))
        with self.assertRaisesRegex(RenderError, 'HEADLINE_ARTIFACT_IDENTITY_MISMATCH'):
            prepared.validate(self.conn, get_row(self.conn, 'publisher_drafts', draft_id))
        self.assertEqual(sha256(prepared.path), digest)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['persistence_status'], 'UNCONFIRMED')
        self.assert_no_remote_effect(draft_id)

    def test_cancel_local_headline_failure_never_issues_cleanup_consent(self):
        self.configure()
        _, _, ids = self.paused()
        draft_id = ids[0]
        batch_id = get_row(self.conn, 'publisher_drafts', draft_id)['batch_id']
        with self.assertRaisesRegex(ValueError, 'BATCH_CANCEL_BLOCKED'):
            self.service.cancel_batch(batch_id)
        result = self.service.cancel_batch(batch_id, eligible_only=True)
        self.assertEqual(result['cancelled_drafts'], 1)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['status'], 'ARCHIVED')
        with self.assertRaises(ValueError):
            authorize_cleanup(self.conn, draft_id, human_confirmed=True,
                              identity_token=cleanup_identity_token(self.conn, draft_id))
        self.assert_no_remote_effect(draft_id)

    def test_local_derived_video_and_weak_save_observation_never_confirm_persistence(self):
        draft_id, prepared = self.prepared()
        frozen = headlines.read_snapshot(get_row(self.conn, 'publisher_drafts', draft_id))
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_CONFIRMED',"
                              "processing_status='MEDIA_READY_FOR_EDIT' WHERE id=?", (draft_id,))
            log(self.conn, draft_id, 'UPLOAD_STARTED', 'OK', 'Synthetic transfer checkpoint')
        self.assertFalse(confirm_received(self.conn, draft_id, dict(
            file=True, upload=True, caption=True, settings=True, durable=True,
            tiktok_draft_id='synthetic-untrusted-id',
            tiktok_draft_url='https://www.tiktok.com/tiktokstudio/content')))
        row = get_row(self.conn, 'publisher_drafts', draft_id)
        self.assertEqual(row['persistence_status'], 'UNCONFIRMED')
        self.assertIsNone(row['confirmed_at'])
        self.assertEqual(headlines.read_snapshot(row), frozen)
        self.assertEqual(sha256(prepared.path), prepared.artifact_sha256)
        self.assertEqual(persistence_identity.trusted_receipt_keys(self.conn), {})
        with self.assertRaises(ValueError):
            assert_upload_allowed(self.conn, draft_id)
        self.assertFalse(self.uploads or self.provider.deleted)
        self.file_input.set_input_files.assert_not_called()
        self.assertFalse(self.conn.execute("SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
            "AND step IN ('PERSISTENCE_IDENTITY_CONFIRMED','DRIVE_CLEANUP_AUTHORIZED')", (draft_id,)).fetchone())

    def test_fake_receipt_binds_actual_derived_hash_and_still_requires_cleanup_consent(self):
        draft_id, prepared = self.prepared()
        # Seed only the synthetic observation, never the persistence conclusion.
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',"
                "upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT',"
                "checklist_json=? WHERE id=?", (json.dumps(dict(file=True, upload=True, caption=True,
                    settings=True, durable=False)), draft_id))
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' "
                              "WHERE id=(SELECT media_asset_id FROM publisher_drafts WHERE id=?)", (draft_id,))
            log(self.conn, draft_id, 'UPLOAD_STARTED', 'OK', 'Synthetic transfer checkpoint')
            log(self.conn, draft_id, 'SAVE_DRAFT', 'OK', 'Synthetic save observation without proof')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['persistence_status'], 'UNCONFIRMED')
        promote_prepared_fake(self, draft_id, prepared.path.read_bytes())
        row = get_row(self.conn, 'publisher_drafts', draft_id)
        self.assertTrue(persistence_identity.persistence_identity_verified(self.conn, row))
        proof = json.loads(self.conn.execute(
            "SELECT message FROM publisher_execution_logs WHERE draft_id=? "
            "AND step='PERSISTENCE_IDENTITY_CONFIRMED'", (draft_id,)).fetchone()[0])
        self.assertEqual(proof['identity']['artifact_sha256'], prepared.artifact_sha256)
        self.assertEqual(proof['receipt']['remote_media_sha256'], prepared.artifact_sha256)
        self.assertEqual(proof['identity']['source_sha256'], prepared.source_sha256)
        with closing(sqlite3.connect(':memory:')) as other:
            other.row_factory = sqlite3.Row
            self.assertEqual(persistence_identity.trusted_receipt_keys(other), {})
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_DRIVE_DELETION_AUTHORIZATION_REQUIRED'):
            cleanup_remote(self.conn, draft_id, self.provider, self.root)
        with self.assertRaisesRegex(ValueError, 'DRAFT_ALREADY_PERSISTED'):
            assert_upload_allowed(self.conn, draft_id)
        self.assertFalse(self.uploads or self.provider.deleted)
        self.file_input.set_input_files.assert_not_called()


if __name__ == '__main__':
    unittest.main()
