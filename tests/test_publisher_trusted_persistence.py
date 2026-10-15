"""Trusted receipts and supervised closure on temporary SQLite only."""
from contextlib import closing
import copy
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from tests import test_publisher_remote as remote
from tests.publisher_security_fixture import (uncertain, trusted_observation, FakeIndependentMediaReader,
                                             CHECKS, confirm_independent_fake_identity)
from src.publisher.persistence_identity import (persistence_binding, record_trusted_persistence_evidence,
                                              persistence_identity_verified, verify_persistence_evidence)
from src.publisher.lifecycle import reconcile_received, cleanup_remote
from src.publisher.manual_resolution import resolve_draft
from src.publisher.service import get_row, assert_upload_allowed
from src.store import connect


class TrustedPersistenceTests(unittest.TestCase):
    setUp = remote.PublisherRemoteTests.setUp
    batch = remote.PublisherRemoteTests.batch

    def pending(self):
        draft=self.batch()[0]
        context=uncertain(self,draft)
        return draft,context

    def resolve(self,draft,context,evidence,decision='CONFIRM_EXISTS'):
        row=get_row(self.conn,'publisher_drafts',draft)
        return resolve_draft(self.conn,draft,dict(decision=decision,human_confirmed=True,
            reason='Independent isolated receipt validation',account_id=row['account_id'],
            media_asset_id=row['media_asset_id'],checkpoint_id=context['checkpoint'],
            evidence_id=evidence,acknowledge_remote_may_exist=True))

    def test_fresh_receipt_promotes_atomically_and_separate_consent_is_still_required(self):
        draft,context=self.pending();evidence=trusted_observation(self,draft,context)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')
        self.assertEqual(self.resolve(draft,context,evidence)['status'],'READY_FOR_REVIEW')
        row=get_row(self.conn,'publisher_drafts',draft)
        self.assertTrue(persistence_identity_verified(self.conn,row))
        self.assertEqual(self.conn.execute('SELECT status FROM publisher_execution_items WHERE draft_id=?',(draft,)).fetchone()[0],'SUCCEEDED')
        self.assertTrue(reconcile_received(self.conn,draft,CHECKS,evidence_id=evidence))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='PERSISTENCE_IDENTITY_CONFIRMED'").fetchone()[0],1)
        with self.assertRaisesRegex(ValueError,'EXPLICIT_DRIVE_DELETION_AUTHORIZATION_REQUIRED'):
            cleanup_remote(self.conn,draft,self.provider,self.root)
        self.assertFalse(self.provider.downloads or self.provider.deleted)

    def test_signed_wrong_bindings_generic_identity_and_ambiguity_are_rejected(self):
        draft,context=self.pending();binding=persistence_binding(self.conn,draft)
        reader=FakeIndependentMediaReader(self)
        changes=[]
        for key in ('account_id','draft_id','media_asset_id','execution_id','remote_checkpoint_id','upload_source_id'):
            wrong=copy.deepcopy(binding);wrong[key]=-123
            changes.append({'identity':wrong})
        changes.extend([{'remote_draft_id':'content'},{'remote_draft_id':'0'*64},{'unique':False},
                        {'remote_media_sha256':'0'*64},{'operation':'TRANSFER'},{'version':True},
                        {'identity':dict(binding,account_id=True)},
                        {'identity':dict(binding,account_handle='another_account')}])
        for change in changes:
            with self.subTest(change=list(change)), self.assertRaises(ValueError):
                record_trusted_persistence_evidence(self.conn,draft,reader.attest(binding,**change))
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_reconciliation_evidence').fetchone()[0],0)
        self.assertFalse(self.provider.deleted)

    def test_tampered_receipt_and_client_claim_cannot_establish_authority(self):
        draft,context=self.pending();binding=persistence_binding(self.conn,draft)
        receipt=FakeIndependentMediaReader(self).attest(binding)
        tampered=dict(receipt,remote_draft_id='a'*64)
        with self.assertRaisesRegex(ValueError,'PERSISTENCE_REMOTE_PROOF_INVALID'):
            record_trusted_persistence_evidence(self.conn,draft,tampered)
        # Production has no Fake authority even when client fields say verified.
        with patch('src.publisher.persistence_identity.trusted_receipt_keys',return_value={}):
            with self.assertRaisesRegex(ValueError,'PERSISTENCE_REMOTE_MEDIA_IDENTITY_UNAVAILABLE'):
                record_trusted_persistence_evidence(self.conn,draft,receipt)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_previous_operation_and_late_checkpoint_invalidate_receipt(self):
        draft,context=self.pending();binding=persistence_binding(self.conn,draft)
        receipt=FakeIndependentMediaReader(self).attest(binding)
        new=uncertain(self,draft)
        with self.assertRaisesRegex(ValueError,'PERSISTENCE_REMOTE_PROOF_INVALID'):
            record_trusted_persistence_evidence(self.conn,draft,receipt)
        evidence=trusted_observation(self,draft,new)
        with self.conn:
            self.conn.execute("INSERT INTO publisher_execution_logs(draft_id,step,status,message,created_at) VALUES (?,'SAVE_DRAFT','STARTED','late checkpoint',?)",(draft,datetime.now(timezone.utc).isoformat()))
        with self.assertRaises(ValueError):self.resolve(draft,new,evidence)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['persistence_status'],'UNCONFIRMED')

    def test_expired_unbounded_or_future_receipt_is_rejected(self):
        draft,context=self.pending();binding=persistence_binding(self.conn,draft);reader=FakeIndependentMediaReader(self)
        now=datetime.now(timezone.utc)
        for issued,expires in [(now-timedelta(seconds=30),now-timedelta(seconds=1)),
                               (now,now+timedelta(hours=1)),(now+timedelta(seconds=10),now+timedelta(seconds=20))]:
            receipt=reader.attest(binding,issued_at=issued.isoformat(),expires_at=expires.isoformat())
            with self.subTest(issued=issued),self.assertRaises(ValueError):
                record_trusted_persistence_evidence(self.conn,draft,receipt)

    def test_remote_identity_and_nonce_cannot_confirm_another_draft(self):
        ids=self.batch(2);a=uncertain(self,ids[0]);b=uncertain(self,ids[1]);reader=FakeIndependentMediaReader(self)
        receipt=reader.attest(persistence_binding(self.conn,ids[0]))
        evidence=record_trusted_persistence_evidence(self.conn,ids[0],receipt)
        self.resolve(ids[0],a,evidence)
        binding=persistence_binding(self.conn,ids[1])
        for change in ({'remote_draft_id':receipt['remote_draft_id']},{'nonce':receipt['nonce']}):
            with self.subTest(change=change),self.assertRaisesRegex(ValueError,'PERSISTENCE_REMOTE_PROOF_REPLAYED'):
                record_trusted_persistence_evidence(self.conn,ids[1],reader.attest(binding,**change))
        with self.assertRaisesRegex(ValueError,'PERSISTENCE_REMOTE_PROOF_REPLAYED'):
            reconcile_received(self.conn,ids[0],CHECKS,evidence_id=-1)
        self.assertEqual(get_row(self.conn,'publisher_drafts',ids[1])['persistence_status'],'UNCONFIRMED')
        # Reusing a remote identity/challenge for a later operation on the same
        # Draft is also prohibited, even if the issuer signs the new binding.
        uncertain(self,ids[0])
        binding=persistence_binding(self.conn,ids[0])
        for change in ({'remote_draft_id':receipt['remote_draft_id']},{'nonce':receipt['nonce']}):
            with self.subTest(later_operation=change),self.assertRaisesRegex(ValueError,'PERSISTENCE_REMOTE_PROOF_REPLAYED'):
                record_trusted_persistence_evidence(self.conn,ids[0],reader.attest(binding,**change))

    def test_audit_failure_rolls_back_promotion_media_and_execution(self):
        draft,context=self.pending();evidence=trusted_observation(self,draft,context)
        before=get_row(self.conn,'publisher_drafts',draft)
        with patch('src.publisher.manual_resolution.log',side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):self.resolve(draft,context,evidence)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft),before)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'NEEDS_RECONCILIATION')
        self.assertEqual(self.conn.execute('SELECT status FROM publisher_execution_items WHERE draft_id=?',(draft,)).fetchone()[0],'WAITING_MANUAL')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='PERSISTENCE_IDENTITY_CONFIRMED'").fetchone()[0],0)

    def test_proof_validation_is_under_write_lock(self):
        draft,context=self.pending();evidence=trusted_observation(self,draft,context)
        other=sqlite3.connect(self.root/'test.db',timeout=0.01);self.addCleanup(other.close)
        checks=[]
        def verified(conn,*args):
            self.assertTrue(conn.in_transaction)
            with self.assertRaises(sqlite3.OperationalError):other.execute('BEGIN IMMEDIATE')
            other.rollback();checks.append(True)
            return verify_persistence_evidence(conn,*args)
        with patch('src.publisher.persistence_identity.verify_persistence_evidence',side_effect=verified):
            self.resolve(draft,context,evidence)
        self.assertEqual(checks,[True])

    def test_concurrent_promotion_is_idempotent_and_survives_connection_restart(self):
        draft,context=self.pending();evidence=trusted_observation(self,draft,context)
        def promote():
            conn=connect(self.root/'test.db')
            try:return reconcile_received(conn,draft,CHECKS,evidence_id=evidence)
            finally:conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(lambda _:promote(),range(2))),[True,True])
        with closing(connect(self.root/'test.db')) as restarted:
            self.assertTrue(persistence_identity_verified(restarted,get_row(restarted,'publisher_drafts',draft)))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='PERSISTENCE_IDENTITY_CONFIRMED'").fetchone()[0],1)

    def test_supervised_closure_finishes_local_task_without_remote_permissions(self):
        draft,context=self.pending()
        result=self.resolve(draft,context,None,'ABANDON')
        self.assertTrue(result['reuse_blocked']);self.assertFalse(result['retry_authorized'])
        row=get_row(self.conn,'publisher_drafts',draft)
        self.assertEqual(row['status'],'ARCHIVED');self.assertEqual(row['persistence_status'],'UNCONFIRMED')
        self.assertEqual(self.conn.execute('SELECT status,error_code FROM publisher_execution_items WHERE draft_id=?',(draft,)).fetchone()[1],'ADMINISTRATIVELY_CLOSED_REMOTE_UNCONFIRMED')
        self.assertEqual(self.conn.execute('SELECT status FROM publisher_batch_executions WHERE id=?',(context['execution'],)).fetchone()[0],'COMPLETED')
        audit=json.loads(self.conn.execute("SELECT message FROM publisher_execution_logs WHERE step='MANUAL_RESOLUTION'").fetchone()[0])
        self.assertTrue(audit['local_task_closed']);self.assertFalse(audit['persistence_confirmed'] or audit['drive_deletion_authorized'] or audit['reuse_authorized'])
        with self.assertRaises(ValueError):assert_upload_allowed(self.conn,draft)
        with self.assertRaises(ValueError):self.batch()
        with self.assertRaises(ValueError):cleanup_remote(self.conn,draft,self.provider,self.root)
        self.assertFalse(self.provider.downloads or self.provider.deleted)
