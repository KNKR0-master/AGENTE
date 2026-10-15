"""P0 security regressions using temporary SQLite and Fake adapters only."""
import io
import json
import unittest
from datetime import timedelta
from unittest.mock import Mock, patch
from tests import test_publisher_remote as fixtures
from tests.test_publisher_batch_consumption import FakeIdentityDrive
from tests.publisher_security_fixture import CHECKS, uncertain, observation, confirm_independent_fake_identity
from src.publisher.cleanup_authorization import authorize_cleanup, cleanup_identity_token, assert_cleanup_authorized, STEP
from src.publisher.lifecycle import cleanup_remote, confirm_received, reconcile_received
from src.publisher.service import get_row, log, assert_upload_allowed
from src.publisher.worker import PublisherWorker, StudioPage, PreparationError, recover_sessions
from src.publisher.manual_resolution import resolve_draft
from src.publisher.persistence_identity import persistence_identity_verified
from src.publisher.batch_consumption import batch_consumption_review, finalize_batch_consumption
from src.publisher.posting_review import register_batch_published
from src.publisher.queue_presentation import change_visibility
from src.publisher.http import PublisherAPI

class SecurityHardeningTests(unittest.TestCase):
    setUp=fixtures.PublisherRemoteTests.setUp
    batch=fixtures.PublisherRemoteTests.batch

    def worker(self):
        return PublisherWorker(self.root,self.root/'test.db',provider_factory=Mock(side_effect=AssertionError('Unexpected adapter construction')))

    def verified(self):
        draft=self.batch()[0]
        confirm_independent_fake_identity(self,draft)
        self.drive=FakeIdentityDrive()
        return draft

    def consent(self,draft,**overrides):
        args=dict(human_confirmed=True,identity_token=cleanup_identity_token(self.conn,draft))
        args.update(overrides)
        return authorize_cleanup(self.conn,draft,**args)

    def assert_preserved(self,draft):
        self.assertNotEqual(get_row(self.conn,'publisher_media_assets',get_row(self.conn,'publisher_drafts',draft)['media_asset_id'])['status'],'CONSUMED')
        self.assertFalse(self.drive.deleted)
        self.assertFalse(self.drive.lookups)

    def http(self,path,data,worker=None):
        api=PublisherAPI.__new__(PublisherAPI)
        api.db,api.root,api.worker=self.root/'test.db',self.root,worker or Mock()
        handler=Mock();body=json.dumps(data).encode()
        handler.headers={'Content-Length':str(len(body)),'Content-Type':'application/json'}
        handler.rfile=io.BytesIO(body)
        api.post(handler,'/api/publisher/'+path)
        return handler,api.worker

    def decision(self,draft,ctx,evidence=None,decision='CONFIRM_EXISTS'):
        row=get_row(self.conn,'publisher_drafts',draft)
        return dict(decision=decision,human_confirmed=True,reason='Synthetic supervised inspection',account_id=row['account_id'],media_asset_id=row['media_asset_id'],checkpoint_id=ctx['checkpoint'],evidence_id=evidence)

    def test_auto_cleanup_false_queues_prepare_only(self):
        draft=self.batch()[0];worker=self.worker()
        with patch.object(worker,'start'):
            execution=worker.enqueue(self.conn,[draft],auto_cleanup=False)
        self.assertEqual(self.conn.execute('SELECT auto_cleanup FROM publisher_batch_executions WHERE id=?',(execution,)).fetchone()[0],0)
        self.assertEqual(worker.commands.get_nowait(),('prepare',(draft,execution)))
        self.assertFalse(self.provider.deleted)
        self.assertNotEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'CONSUMED')
        self.assertFalse(self.conn.execute('SELECT 1 FROM publisher_execution_logs WHERE step=?',(STEP,)).fetchone())

    def test_auto_cleanup_true_rejected_before_any_queue_mutation(self):
        draft=self.batch()[0];worker=self.worker();before=self.conn.total_changes
        with patch.object(worker,'start') as start:
            for value in (True,1,'true',None):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError,'AUTO_CLEANUP_DISABLED'):
                    worker.enqueue(self.conn,[draft],auto_cleanup=value)
            start.assert_not_called()
        self.assertEqual(self.conn.total_changes,before)
        self.assertFalse(self.provider.deleted)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'RESERVED')

    def test_direct_prepare_http_rejects_auto_cleanup(self):
        draft=self.batch()[0];worker=Mock()
        with self.assertRaisesRegex(ValueError,'AUTO_CLEANUP_DISABLED'):
            self.http('prepare',dict(ids=[draft],auto_cleanup=True,human_confirmed=True),worker)
        worker.enqueue.assert_not_called()
        self.assertFalse(self.provider.deleted)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'RESERVED')

    def test_legacy_auto_cleanup_worker_dispatch_is_blocked(self):
        draft=self.batch()[0];ctx=uncertain(self,draft,auto_cleanup=1);worker=self.worker()
        with self.assertRaisesRegex(PreparationError,'AUTO_CLEANUP_DISABLED'):
            worker.assert_execution_cleanup_disabled(self.conn,'prepare',ctx['execution'])
        worker.provider_factory.assert_not_called()
        self.assertFalse(self.provider.deleted)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'NEEDS_RECONCILIATION')

    def test_legacy_paused_execution_cannot_resume(self):
        draft=self.batch()[0];ctx=uncertain(self,draft,auto_cleanup=1);worker=self.worker()
        with patch.object(worker,'start') as start, self.assertRaisesRegex(ValueError,'AUTO_CLEANUP_DISABLED'):
            worker.resume_execution(self.conn,ctx['execution'])
        start.assert_not_called();self.assertTrue(worker.commands.empty());self.assertFalse(self.provider.deleted)

    def test_legacy_reconcile_commands_cannot_authorize_cleanup(self):
        worker=self.worker()
        for command in ('reconcile','cleanup-reconciled'):
            with self.subTest(command=command),self.assertRaisesRegex(PreparationError,'AUTO_CLEANUP_DISABLED'):
                worker.assert_execution_cleanup_disabled(self.conn,command,None)
        worker.provider_factory.assert_not_called()

    def test_worker_cleanup_requires_explicit_authorization_handle(self):
        draft=self.verified();worker=self.worker()
        with patch.object(worker,'start') as start,self.assertRaisesRegex(ValueError,'EXPLICIT_DRIVE_DELETION'):
            worker.submit('cleanup',draft)
        start.assert_not_called();self.assert_preserved(draft)

    def test_domain_cleanup_without_consent_never_reads_or_deletes(self):
        draft=self.verified()
        with self.assertRaisesRegex(ValueError,'EXPLICIT_DRIVE_DELETION'):
            cleanup_remote(self.conn,draft,self.drive,self.root)
        self.assert_preserved(draft)

    def test_cleanup_http_requires_human_confirmation(self):
        draft=self.verified();worker=Mock()
        for value in (None,False,1,'true'):
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'EXPLICIT_MEDIA_CONSUMPTION'):
                self.http('cleanup',dict(id=draft,human_confirmed=value),worker)
        worker.submit.assert_not_called();self.assert_preserved(draft)

    def test_cleanup_http_requires_current_media_token(self):
        draft=self.verified();worker=Mock()
        with self.assertRaisesRegex(ValueError,'CLEANUP_IDENTITY_CHANGED'):
            self.http('cleanup',dict(id=draft,human_confirmed=True),worker)
        worker.submit.assert_not_called();self.assert_preserved(draft)

    def test_http_authorization_is_audited_and_forwarded(self):
        draft=self.verified();worker=Mock();worker.submit.return_value={'ok':True}
        self.http('cleanup',dict(id=draft,human_confirmed=True,identity_token=cleanup_identity_token(self.conn,draft)),worker)
        consent=worker.submit.call_args.kwargs['authorization_id']
        self.assertEqual(assert_cleanup_authorized(self.conn,draft,consent)['identity']['operation'],'DRIVE_MEDIA_CONSUMPTION')
        self.assert_preserved(draft)

    def test_authorization_is_scoped_to_draft_and_media_identity(self):
        draft=self.verified();consent=self.consent(draft)
        with self.conn:self.conn.execute("UPDATE publisher_media_assets SET remote_file_id='changed' WHERE id=1")
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent)
        self.assert_preserved(draft)

    def test_authorization_rechecked_after_entering_cleanup_lock(self):
        draft=self.verified();consent=self.consent(draft);calls=[]
        def mutate():
            calls.append(True)
            if len(calls)==2:self.conn.execute("UPDATE publisher_media_assets SET filename='changed.mp4' WHERE id=1")
        with self.assertRaises(ValueError):
            cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent,identity_validator=mutate)
        self.assertGreaterEqual(len(calls),2);self.assert_preserved(draft)

    def test_authorized_cleanup_idempotent_with_audited_proof(self):
        draft=self.verified();consent=self.consent(draft)
        self.assertTrue(persistence_identity_verified(self.conn,get_row(self.conn,'publisher_drafts',draft)))
        self.assertTrue(cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent))
        self.assertTrue(cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent))
        self.assertEqual(self.drive.deleted,['remote-0'])

    def test_fake_timeout_preserves_pending_delete_until_authorized_retry(self):
        draft=self.verified();consent=self.consent(draft)
        with patch.object(self.drive,'delete_remote',side_effect=TimeoutError('Fake timeout')):
            self.assertFalse(cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent))
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'CONSUMED_PENDING_DELETE')
        self.assertFalse(self.drive.deleted)
        self.assertTrue(cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent))
        self.assertEqual(self.drive.deleted,['remote-0'])

    def test_publication_and_hiding_do_not_issue_cleanup_consent(self):
        draft=self.verified();batch=get_row(self.conn,'publisher_drafts',draft)['batch_id']
        register_batch_published(self.conn,batch,dict(draft_ids=[draft],human_confirmed=True))
        change_visibility(self.conn,'hide','DRAFT',draft)
        self.assertFalse(self.conn.execute('SELECT 1 FROM publisher_execution_logs WHERE step=?',(STEP,)).fetchone())
        with self.assertRaisesRegex(ValueError,'EXPLICIT_DRIVE_DELETION'):
            cleanup_remote(self.conn,draft,self.drive,self.root)
        self.assert_preserved(draft)

    def test_batch_missing_distinct_authorization_preserves_media(self):
        draft=self.verified();batch=get_row(self.conn,'publisher_drafts',draft)['batch_id']
        item=batch_consumption_review(self.conn,batch)['items'][0]
        payload=dict(request_id='security-request-0001',items=[dict(draft_id=draft,identity_token=item['identity_token'])],confirmed_count=1,human_confirmed=True)
        with self.assertRaisesRegex(ValueError,'SEPARATE_DRIVE_DELETION'):
            finalize_batch_consumption(self.conn,batch,payload,lambda _:self.drive,self.root)
        self.assert_preserved(draft)

    def test_unreconciled_history_blocks_even_explicit_consent(self):
        draft=self.verified();consent=self.consent(draft)
        with self.conn:self.conn.execute("UPDATE publisher_drafts SET persistence_status='UNCONFIRMED' WHERE id=?",(draft,))
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent)
        self.assert_preserved(draft)

    def test_legacy_persisted_flags_are_not_remote_identity_proof(self):
        draft=self.batch()[0];self.drive=FakeIdentityDrive()
        with self.conn:self.conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED',confirmed_at='legacy',tiktok_draft_persisted_at='legacy',tiktok_draft_url=? WHERE id=?",(CHECKS['tiktok_draft_url'],draft))
        self.assertFalse(confirm_received(self.conn,draft,CHECKS))
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=self.consent(draft))
        self.assert_preserved(draft)

    def test_caption_generic_url_and_fake_id_never_promote_automatic_save(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);before=get_row(self.conn,'publisher_drafts',draft)
        for extra in ({},{'tiktok_draft_id':'untrusted-id'},{'tiktok_draft_url':'https://www.tiktok.com/editor'}):
            with self.subTest(extra=extra):self.assertFalse(confirm_received(self.conn,draft,dict(CHECKS,**extra)))
        after=get_row(self.conn,'publisher_drafts',draft)
        self.assertEqual(after['persistence_status'],'UNCONFIRMED');self.assertIsNone(after['confirmed_at']);self.assertIsNone(after['tiktok_draft_id'])
        self.assertEqual(after['upload_status'],'TRANSFER_CONFIRMED');self.assertEqual(after['headline_snapshot_json'],before['headline_snapshot_json'])

    def test_single_composite_candidate_still_lacks_remote_media_identity(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);evidence=observation(self,draft,ctx)
        with self.assertRaisesRegex(ValueError,'PERSISTENCE_REMOTE_MEDIA_IDENTITY_UNAVAILABLE'):
            resolve_draft(self.conn,draft,self.decision(draft,ctx,evidence))
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_duplicate_caption_candidates_never_confirm(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);evidence=observation(self,draft,ctx,rows=[ctx['row'],dict(ctx['row'])])
        with self.assertRaises(ValueError):resolve_draft(self.conn,draft,self.decision(draft,ctx,evidence))
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_wrong_account_invalidates_evidence(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);evidence=observation(self,draft,ctx)
        with self.conn:self.conn.execute("UPDATE publisher_accounts SET tiktok_username='wrong_account' WHERE id=?",(self.account,))
        with self.assertRaises(ValueError):resolve_draft(self.conn,draft,self.decision(draft,ctx,evidence))
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_old_candidate_before_save_cannot_be_confirmed(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);old=dict(ctx['row'],updated_text='13 de set de 2026, 19:32');evidence=observation(self,draft,ctx,rows=[old])
        with self.assertRaises(ValueError):resolve_draft(self.conn,draft,self.decision(draft,ctx,evidence))
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_transfer_unknown_cannot_confirm_even_with_evidence(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);evidence=observation(self,draft,ctx)
        with self.conn:self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?",(draft,))
        with self.assertRaisesRegex(ValueError,'TRANSFER_CHECKPOINT_REQUIRED'):
            resolve_draft(self.conn,draft,self.decision(draft,ctx,evidence))

    def test_generic_url_without_evidence_rejected_in_domain(self):
        draft=self.batch()[0];uncertain(self,draft)
        with self.assertRaisesRegex(ValueError,'PERSISTENCE_IDENTITY_EVIDENCE_REQUIRED'):
            reconcile_received(self.conn,draft,CHECKS)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_save_checkpoint_changes_invalidate_previous_observation(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);evidence=observation(self,draft,ctx)
        with self.conn:log(self.conn,draft,'SAVE_DRAFT','OK','later checkpoint')
        with self.assertRaisesRegex(ValueError,'RESOLUTION_CHECKPOINT_CHANGED'):
            resolve_draft(self.conn,draft,self.decision(draft,ctx,evidence))

    def test_missing_or_mismatched_upload_source_fails_closed(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);evidence=observation(self,draft,ctx,payload_override=dict(upload_source_identity=dict(upload_source_id=ctx['source'],source_sha256='wrong')))
        with self.assertRaises(ValueError):resolve_draft(self.conn,draft,self.decision(draft,ctx,evidence))
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_reconciliation_validation_runs_inside_write_transaction(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);evidence=observation(self,draft,ctx);transactions=[]
        from src.publisher.persistence_identity import verify_persistence_evidence
        def observed(conn,*args):
            transactions.append(conn.in_transaction)
            return verify_persistence_evidence(conn,*args)
        with patch('src.publisher.persistence_identity.verify_persistence_evidence',side_effect=observed),self.assertRaises(ValueError):
            reconcile_received(self.conn,draft,CHECKS,evidence_id=evidence)
        self.assertEqual(transactions,[True])

    def test_restart_during_reconciliation_preserves_snapshot_and_transfer(self):
        draft=self.batch()[0];uncertain(self,draft);before=get_row(self.conn,'publisher_drafts',draft)
        recover_sessions(self.conn);after=get_row(self.conn,'publisher_drafts',draft)
        for key in ('headline_snapshot_json','upload_status','prepared_upload_source_id','persistence_status'):
            self.assertEqual(after[key],before[key])
        self.assertEqual(after['persistence_status'],'UNCONFIRMED')
        with self.assertRaises(ValueError):assert_upload_allowed(self.conn,draft)

    def test_unknown_remote_result_never_authorizes_reupload_or_resume(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);worker=self.worker()
        for attempt in range(2):
            with patch.object(worker,'start') as start,self.assertRaises(ValueError):worker.enqueue(self.conn,[draft])
            start.assert_not_called()
        with self.assertRaisesRegex(ValueError,'PREUPLOAD_RESUME_NOT_PROVEN_SAFE'):worker.resume_execution(self.conn,ctx['execution'])
        self.assertFalse(self.provider.downloads);self.assertTrue(worker.commands.empty())

    def test_supervised_unknown_resolution_remains_available(self):
        draft=self.batch()[0];ctx=uncertain(self,draft)
        result=resolve_draft(self.conn,draft,self.decision(draft,ctx,decision='UNKNOWN'))
        self.assertEqual(result['remote_actions'],0);self.assertFalse(result['retry_authorized'])
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_readonly_verification_does_not_upgrade_unique_composite_match(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);worker=self.worker()
        observed=dict(source_surface='TIKTOK_STUDIO_DRAFT_LIST',observed_account='synthetic_account',rows=[ctx['row']],listing_complete=True,blocker_code=None)
        with patch.object(worker,'_inspect_draft_list_read_only',return_value=observed):
            result=worker._verify_remote_draft_record(None,self.conn,draft,ctx['execution'])
        self.assertEqual(result['outcome'],'REMOTE_STATE_UNKNOWN')
        self.assertEqual(result['blocker_code'],'MATCH_EVIDENCE_INSUFFICIENT')
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_readonly_observation_failure_remains_unknown(self):
        draft=self.batch()[0];ctx=uncertain(self,draft);worker=self.worker()
        with patch.object(worker,'_inspect_draft_list_read_only',side_effect=RuntimeError('Fake observation failure')):
            result=worker._verify_remote_draft_record(None,self.conn,draft,ctx['execution'])
        self.assertEqual(result['outcome'],'REMOTE_STATE_UNKNOWN');self.assertEqual(result['blocker_code'],'LISTING_ERROR')
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def studio(self):
        page=Mock();page.url=CHECKS['tiktok_draft_url']
        save=Mock();save.count.return_value=1;save.is_visible.return_value=True;save.is_enabled.return_value=True
        absent=Mock();absent.count.return_value=0
        page.get_by_role.side_effect=lambda role,**kw:save if kw.get('name')=='Save draft' else absent
        page.locator.return_value.inner_text.return_value='Rascunhos Todos os computadores'
        page.get_by_text.return_value.count.return_value=1
        adapter=StudioPage(page,dict(durable_confirmation_validated=True));adapter.interruptions=Mock();adapter.challenge=Mock()
        return adapter,page,save

    def test_save_observation_is_unconfirmed_even_when_caption_visible(self):
        adapter,page,save=self.studio();events=[]
        with self.assertRaisesRegex(PreparationError,'PERSISTENCE_IDENTITY_REQUIRED'):
            adapter.confirm_durable(dict(caption='Legenda'),lambda *args:events.append(args[0]))
        self.assertTrue(adapter.save_attempted);save.click.assert_called_once()
        self.assertNotIn('TIKTOK_DRAFT_CONFIRMED',events);self.assertIn('SAVE_RESULT_UNCONFIRMED',events)

    def test_timeout_after_save_preserves_unknown_effect(self):
        adapter,page,save=self.studio()
        with patch('src.publisher.worker.monotonic',side_effect=[0,61]),self.assertRaisesRegex(PreparationError,'nao repetir upload'):
            adapter.confirm_durable(dict(caption='Legenda'),Mock())
        self.assertTrue(adapter.save_attempted);save.click.assert_called_once()

    def test_observation_failure_after_save_cannot_confirm(self):
        adapter,page,save=self.studio();page.locator.return_value.inner_text.side_effect=RuntimeError('Fake observation failure')
        with self.assertRaises(RuntimeError):adapter.confirm_durable(dict(caption='Legenda'),Mock())
        self.assertTrue(adapter.save_attempted);save.click.assert_called_once()

    def test_caption_only_consumption_attempt_is_blocked(self):
        draft=self.batch()[0];uncertain(self,draft);self.drive=FakeIdentityDrive()
        self.assertFalse(confirm_received(self.conn,draft,CHECKS))
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=self.consent(draft))
        self.assert_preserved(draft)

    def test_later_unknown_save_invalidates_prior_identity_proof_under_lock(self):
        draft=self.verified();consent=self.consent(draft);calls=[]
        def mutate():
            calls.append(True)
            if len(calls)==2:log(self.conn,draft,'SAVE_DRAFT','OK','later unknown remote result')
        with self.assertRaises(ValueError):
            cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent,identity_validator=mutate)
        self.assert_preserved(draft)

    def test_transfer_downgrade_invalidates_prior_proof_for_individual_cleanup(self):
        draft=self.verified();consent=self.consent(draft)
        with self.conn:self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?",(draft,))
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent)
        self.assert_preserved(draft)

    def test_snapshot_change_invalidates_prior_media_proof(self):
        draft=self.verified();consent=self.consent(draft)
        # Caption is mutable legacy input; the frozen snapshot is never edited.
        with self.conn:self.conn.execute("UPDATE publisher_drafts SET caption='Different caption' WHERE id=?",(draft,))
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent)
        self.assert_preserved(draft)

    def test_corrupt_authorization_fails_closed(self):
        draft=self.verified();consent=self.consent(draft)
        with self.conn:self.conn.execute("UPDATE publisher_execution_logs SET message='[]' WHERE id=?",(consent,))
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent)
        self.assert_preserved(draft)

    def test_unknown_mutation_ledger_invalidates_prior_identity_proof(self):
        draft=self.verified();consent=self.consent(draft)
        with self.conn:self.conn.execute("INSERT INTO account_mutation_ledger(account_id,mutation_key,mutation_type,draft_id,status,started_at) VALUES (?,'security-unknown-save','SAVE_DRAFT',?,'STARTED','2026-10-12T00:00:00+00:00')",(self.account,draft))
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent)
        self.assert_preserved(draft)

    def test_archived_remote_history_still_blocks_reservation_and_consumption(self):
        draft=self.batch()[0];uncertain(self,draft);self.drive=FakeIdentityDrive()
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='ARCHIVED' WHERE id=?",(draft,))
            self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=1")
        with self.assertRaisesRegex(ValueError,'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):self.batch()
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.drive,self.root)
        self.assert_preserved(draft)

    def test_history_rechecked_immediately_after_metadata_before_delete(self):
        draft=self.verified();consent=self.consent(draft);lookup=self.drive.get_remote_metadata;calls=[]
        def changed(file_id):
            result=lookup(file_id);calls.append(True)
            if len(calls)==2:log(self.conn,draft,'SAVE_DRAFT','OK','late unknown remote save')
            return result
        with patch.object(self.drive,'get_remote_metadata',side_effect=changed),self.assertRaises(ValueError):
            cleanup_remote(self.conn,draft,self.drive,self.root,authorization_id=consent)
        self.assertFalse(self.drive.deleted)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'CONSUMED_PENDING_DELETE')
