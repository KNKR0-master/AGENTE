import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.store import connect, init_db
from src.publisher.service import PublisherService, get_row, init_publisher, transition, log
from src.publisher.lifecycle import confirm_received, cleanup_remote
from src.publisher.media import TemporaryMediaCache, verify_identity
from src.publisher.worker import PublisherWorker, recover_sessions, restore_pre_upload_failure


class FakeMedia:
    def __init__(self,n=20):
        self.files = [{'id':f'remote-{i}','name':f'FINAL_{i}.mp4','mimeType':'video/mp4',
                       'size':10+i,'md5Checksum':f'hash-{i}','parents':['ceci']} for i in range(n)]
        self.deleted=[]
        self.fail_delete=False
        self.downloads=[]

    def list_files(self): return self.files
    def exists(self,file_id): return file_id not in self.deleted
    def delete_remote(self,asset):
        if self.fail_delete: raise RuntimeError('Permission denied')
        self.deleted.append(asset['remote_file_id'])
    def download_temporary(self,asset,directory):
        self.downloads.append(asset['remote_file_id'])
        path=Path(directory)/'video.mp4'
        path.write_bytes(b'x'*asset['size_bytes'])
        return path


class PublisherRemoteTests(unittest.TestCase):
    def setUp(self):
        qa=Path(__file__).resolve().parents[1]/'data/publisher/qa'
        qa.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=qa)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.profile_root=self.root/'browser-profiles'
        environment=patch.dict(os.environ,{'PUBLISHER_BROWSER_PROFILE_ROOT':str(self.profile_root)})
        environment.start();self.addCleanup(environment.stop)
        self.conn=connect(self.root/'test.db')
        self.addCleanup(self.conn.close)
        init_db(self.conn); init_publisher(self.conn)
        self.service=PublisherService(self.conn,self.root)
        self.product=self.service.save_product({'name':'Produto','default_caption':'Legenda'})
        self.account=self.service.save_account({'name':'Ceci'})
        account=get_row(self.conn,'publisher_accounts',self.account)
        (self.profile_root/Path(account['browser_profile']).name).mkdir(parents=True)
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET session_verified=1,account_identity='ACCOUNT_CONFIRMED' WHERE id=?",(self.account,))
        ownership=patch('src.publisher.worker.ProfileOwnership.state',return_value='SESSION_REQUIRES_VERIFICATION')
        ownership.start();self.addCleanup(ownership.stop)
        self.provider=FakeMedia()
        self.service.index_media(self.provider)
        config=self.root/'config'; config.mkdir()
        (config/'publisher_studio.json').write_text(json.dumps({'tiktok_actions_paused':False}))

    def batch(self,n=1):
        return self.service.create_remote_batch({'product_id':self.product,'account_id':self.account,
            'caption':'Legenda','media_asset_ids':list(range(1,n+1))})['draft_ids']

    def confirm(self,draft):
        with self.conn:
            transition(self.conn,draft,'PREPARING')
            log(self.conn,draft,'TIKTOK_DRAFT_CONFIRMED','OK','fixture','data/publisher/evidence/fixture.png')
        checks=dict.fromkeys(['file','upload','caption','settings','durable'],True)
        checks.update(tiktok_draft_id='tt-123',tiktok_draft_url='https://www.tiktok.com/fixture/123')
        confirm_received(self.conn,draft,checks)

    def test_twenty_references_no_download_and_atomic_reservation(self):
        ids=self.batch(20)
        self.assertEqual(len(ids),20)
        self.assertFalse(self.provider.downloads)
        self.assertEqual({d['video_path'] for d in self.service.snapshot()['drafts']},{''})
        with self.assertRaises(ValueError): self.batch(2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0],1)

    def test_checksum_and_filename_size_duplicate_protection(self):
        self.batch()
        for key,value in [('checksum','hash-0'),('filename','FINAL_0.mp4')]:
            with self.conn:
                self.conn.execute(f'UPDATE publisher_media_assets SET {key}=?,size_bytes=10 WHERE id=2',(value,))
            with self.assertRaises(ValueError):
                self.service.create_remote_batch({'product_id':self.product,'account_id':self.account,'media_asset_ids':[2]})

    def test_no_delete_without_durable_confirmation(self):
        draft=self.batch()[0]
        with self.assertRaises(ValueError): cleanup_remote(self.conn,draft,self.provider)
        self.assertFalse(self.provider.deleted)

    def test_missing_evidence_blocks_confirmation(self):
        draft=self.batch()[0]
        with self.conn: transition(self.conn,draft,'PREPARING')
        checks=dict.fromkeys(['file','upload','caption','product','settings','durable'],True)
        checks.update(tiktok_draft_id='id',tiktok_draft_url='https://www.tiktok.com/draft/id')
        self.assertFalse(confirm_received(self.conn,draft,checks))
        self.assertIsNotNone(get_row(self.conn,'publisher_drafts',draft)['confirmed_at'])
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'RECEIVED_NEEDS_REVIEW')
        with self.assertRaises(ValueError): cleanup_remote(self.conn,draft,self.provider)

    def test_delete_failure_restart_retry_only_cleanup(self):
        draft=self.batch()[0]; self.confirm(draft)
        self.provider.fail_delete=True
        self.assertFalse(cleanup_remote(self.conn,draft,self.provider))
        recover_sessions(self.conn)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['status'],'READY_FOR_REVIEW')
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'CONSUMED_PENDING_DELETE')
        worker=PublisherWorker(self.root,self.root/'test.db')
        with patch.object(worker,'start'):
            with self.assertRaises(ValueError): worker.enqueue(self.conn,[draft])
        with self.assertRaises(ValueError): self.service.bulk([draft],{},'reset')
        self.provider.fail_delete=False
        self.assertTrue(cleanup_remote(self.conn,draft,self.provider))
        self.assertTrue(cleanup_remote(self.conn,draft,self.provider))
        self.assertEqual(len(self.provider.deleted),1)
        self.assertFalse(self.provider.downloads)

    def test_reindex_preserves_consumed_history(self):
        draft=self.batch()[0]; self.confirm(draft)
        cleanup_remote(self.conn,draft,self.provider)
        self.service.index_media(self.provider)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'CONSUMED')
        with self.assertRaises(ValueError): self.batch()

    def test_archive_releases_unconsumed_remote_reference(self):
        draft=self.batch()[0]
        self.service.bulk([draft],{},'archive')
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'AVAILABLE')
        self.batch()
        self.assertFalse(self.provider.deleted)

    def test_cache_exception_cleanup_and_active_lease(self):
        cache=TemporaryMediaCache(self.root)
        with self.assertRaises(RuntimeError):
            with cache.job(1,20) as folder:
                (folder/'video.mp4').write_bytes(b'fixture')
                cache.clean_orphans()
                self.assertTrue(folder.exists())
                raise RuntimeError('upload failed')
        self.assertFalse(list(cache.root.glob('job_*')))
        orphan=cache.root/'job_orphan'; orphan.mkdir(); (orphan/'.lease').write_bytes(b'1')
        cache.clean_orphans()
        self.assertFalse(orphan.exists())
        with self.assertRaises(ValueError):
            with cache.job(2,cache.max_size+1): pass

    def test_restart_quarantines_uncertain_media(self):
        draft=self.batch()[0]
        with self.conn:
            transition(self.conn,draft,'PREPARING')
            self.conn.execute("UPDATE publisher_media_assets SET status='PROCESSING' WHERE id=1")
        recover_sessions(self.conn)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'NEEDS_RECONCILIATION')

    def test_batch_global_error_stops_remaining(self):
        ids=self.batch(3)
        worker=PublisherWorker(self.root,self.root/'test.db')
        with patch.object(worker,'start'): execution=worker.enqueue(self.conn,ids)
        self.conn.execute("UPDATE publisher_execution_items SET status='PROCESSING' WHERE draft_id=?",(ids[0],))
        worker.finish_item(self.conn,ids[0],'WAITING_MANUAL','NEEDS_LOGIN')
        summary=self.service.snapshot()['executions'][0]
        self.assertEqual(summary['id'],execution)
        self.assertEqual(summary['waiting_manual'],3)
        self.assertEqual(summary['status'],'PAUSED')

    def test_content_error_allows_next_item(self):
        ids=self.batch(2)
        worker=PublisherWorker(self.root,self.root/'test.db')
        with patch.object(worker,'start'): worker.enqueue(self.conn,ids)
        self.conn.execute("UPDATE publisher_execution_items SET status='PROCESSING' WHERE draft_id=?",(ids[0],))
        worker.finish_item(self.conn,ids[0],'FAILED','UPLOAD_FAILED')
        self.assertEqual(self.service.snapshot()['executions'][0]['queued'],1)

    def test_jit_success_preserves_media_until_explicit_cleanup(self):
        draft=self.batch()[0]
        worker=PublisherWorker(self.root,self.root/'test.db')
        with patch.object(worker,'start'): worker.enqueue(self.conn,[draft])
        config=self.root/'config'
        (config/'publisher_studio.json').write_text(json.dumps({'validated':True,'durable_confirmation_validated':True,'tiktok_actions_paused':False}))
        page=MagicMock()
        page.screenshot.side_effect=lambda **kw:Path(kw['path']).write_bytes(b'fixture-png')
        context=MagicMock(); context.pages=[page]
        def prepared(adapter,data,preset,path,evidence,**kwargs):
            self.assertTrue(path.exists())
            self.assertFalse(self.provider.deleted)
            evidence('TIKTOK_DRAFT_CONFIRMED','Persistent fixture')
            checks=dict.fromkeys(['file','upload','caption','settings','durable'],True)
            return {**checks,'tiktok_draft_id':'id','tiktok_draft_url':'https://www.tiktok.com/draft/id'}
        with patch.object(worker,'context',return_value=context), \
             patch.object(worker.browser_runtime,'verify_auth',return_value={'auth_result':'AUTHENTICATED','account_identity':'ACCOUNT_CONFIRMED'}), \
             patch('src.publisher.worker.ensure_ffprobe_available',return_value='ffprobe'), \
             patch.object(worker,'provider_factory',return_value=self.provider), \
             patch('src.publisher.worker.prepare_job',side_effect=prepared):
            worker.prepare(None,self.conn,draft)
        self.assertEqual(self.provider.downloads,['remote-0'])
        self.assertEqual(self.provider.deleted,[])
        self.assertTrue(list(worker.cache.root.glob('job_*')))
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['status'],'READY_FOR_REVIEW')

    def test_resume_reconciles_first_item_before_queuing_tail(self):
        ids = self.batch(3)
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start'):
            execution = worker.enqueue(self.conn, ids, auto_cleanup=True)
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT' WHERE id=?",
                (ids[0],),
            )
            self.conn.execute(
                "UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=1"
            )
            for draft_id, asset_id in zip(ids[1:], (2, 3)):
                self.conn.execute(
                    "UPDATE publisher_drafts SET status='DRAFT',upload_status='NOT_STARTED',processing_status='NOT_STARTED' WHERE id=?",
                    (draft_id,),
                )
                self.conn.execute(
                    "UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=?",
                    (asset_id,),
                )
            self.conn.execute(
                "UPDATE publisher_execution_items SET status='WAITING_MANUAL',error_code='MANUAL_ACTION_REQUIRED' WHERE execution_id=?",
                (execution,),
            )
            self.conn.execute(
                "UPDATE publisher_batch_executions SET status='PAUSED',finished_at=? WHERE id=?",
                ('2026-01-01T00:00:00+00:00', execution),
            )
        with patch.object(worker, 'start'):
            self.assertEqual(worker.resume_execution(self.conn, execution), execution)
        items = self.conn.execute(
            "SELECT draft_id,status FROM publisher_execution_items WHERE execution_id=? ORDER BY draft_id",
            (execution,),
        ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in items], [(ids[0], 'PROCESSING'), (ids[1], 'QUEUED'), (ids[2], 'QUEUED')])
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[1])['status'], 'PREPARING')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[2])['status'], 'PREPARING')

    def test_resume_cleans_up_already_reconciled_first_item_before_queuing_tail(self):
        ids = self.batch(3)
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start'):
            execution = worker.enqueue(self.conn, ids, auto_cleanup=True)
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED',"
                "upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT',confirmed_at=? WHERE id=?",
                ('2026-01-01T00:00:00+00:00', ids[0]),
            )
            self.conn.execute("UPDATE publisher_media_assets SET status='RESERVED' WHERE id=1")
            log(self.conn, ids[0], 'TIKTOK_DRAFT_RECONCILED', 'OK', 'fixture', 'evidence/reconciled.png')
            for draft_id, asset_id in zip(ids[1:], (2, 3)):
                self.conn.execute(
                    "UPDATE publisher_drafts SET status='DRAFT',upload_status='NOT_STARTED',processing_status='NOT_STARTED' WHERE id=?",
                    (draft_id,),
                )
                self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=?", (asset_id,))
            self.conn.execute(
                "UPDATE publisher_execution_items SET status='WAITING_MANUAL',error_code='MANUAL_ACTION_REQUIRED' WHERE execution_id=?",
                (execution,),
            )
            self.conn.execute(
                "UPDATE publisher_batch_executions SET status='PAUSED',finished_at=? WHERE id=?",
                ('2026-01-01T00:00:00+00:00', execution),
            )
        with patch.object(worker, 'start'):
            self.assertEqual(worker.resume_execution(self.conn, execution), execution)
        items = self.conn.execute(
            "SELECT draft_id,status FROM publisher_execution_items WHERE execution_id=? ORDER BY draft_id",
            (execution,),
        ).fetchall()
        self.assertEqual([(row[0], row[1]) for row in items], [(ids[0], 'PROCESSING'), (ids[1], 'QUEUED'), (ids[2], 'QUEUED')])

    def test_pre_upload_failure_restores_draft_and_media_for_later_retry(self):
        draft = self.batch()[0]
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',"
                "upload_status='NOT_STARTED',processing_status='NOT_STARTED',error_code=? WHERE id=?",
                ('ffprobe missing', draft),
            )
            self.conn.execute("UPDATE publisher_media_assets SET status='FAILED' WHERE id=1")
            self.assertTrue(restore_pre_upload_failure(self.conn, draft))
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft)['status'], 'DRAFT')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'AVAILABLE')
        self.assertFalse(restore_pre_upload_failure(self.conn, draft))

    def test_upload_started_checkpoint_blocks_pre_upload_reset(self):
        draft = self.batch()[0]
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',"
                "upload_status='NOT_STARTED',processing_status='NOT_STARTED' WHERE id=?",
                (draft,),
            )
            self.conn.execute("UPDATE publisher_media_assets SET status='PROCESSING' WHERE id=1")
            log(self.conn, draft, 'UPLOAD_STARTED', 'OK', 'fixture')
        self.assertFalse(restore_pre_upload_failure(self.conn, draft))

    def test_failed_terminal_item_pauses_batch(self):
        draft = self.batch()[0]
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start'):
            execution = worker.enqueue(self.conn, [draft], auto_cleanup=True)
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_execution_items SET status='PROCESSING' WHERE execution_id=? AND draft_id=?",
                (execution, draft),
            )
        worker.finish_item(self.conn, draft, 'FAILED', 'FAILED')
        self.assertEqual(self.conn.execute(
            'SELECT status FROM publisher_batch_executions WHERE id=?', (execution,)
        ).fetchone()[0], 'PAUSED')

    def test_identity_change_prevents_delete(self):
        asset=get_row(self.conn,'publisher_media_assets',1)
        with self.assertRaises(ValueError): verify_identity(asset,{**self.provider.files[0],'size':999})

    def test_migration_is_idempotent_and_retains_data(self):
        ids=self.batch(2)
        init_publisher(self.conn); init_publisher(self.conn)
        self.assertEqual(len(self.service.snapshot()['drafts']),len(ids))

    def test_caption_snapshot_ignores_batch_override_and_product_changes(self):
        ids=self.batch(2)
        self.service.save_product({'id':self.product,'name':'Produto','default_caption':'Nova legenda'})
        self.assertEqual({get_row(self.conn,'publisher_drafts',i)['caption'] for i in ids},{'Legenda'})
        self.service.bulk([ids[0]],{'caption':'Individual'})
        self.assertEqual(get_row(self.conn,'publisher_drafts',ids[1])['caption'],'Legenda')
        self.service.bulk([ids[1]],{},'use_product_caption')
        self.assertEqual(get_row(self.conn,'publisher_drafts',ids[1])['caption'],'Nova legenda')
        result=self.service.create_remote_batch({'product_id':self.product,'account_id':self.account,'caption':'Nao usar','media_asset_ids':[3]})
        self.assertEqual(get_row(self.conn,'publisher_drafts',result['draft_ids'][0])['caption'],'Nova legenda')

    def test_missing_caption_blocks_before_browser(self):
        self.service.save_product({'id':self.product,'name':'Produto','default_caption':''})
        draft=self.batch()[0]
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['status'],'CAPTION_MISSING')
        worker=PublisherWorker(self.root,self.root/'test.db')
        with patch.object(worker,'start') as start:
            with self.assertRaisesRegex(ValueError,'CAPTION_MISSING'): worker.enqueue(self.conn,[draft])
            start.assert_not_called()
        self.assertFalse(self.provider.downloads)

    def test_contact_pause_blocks_login_review_prepare(self):
        worker=PublisherWorker(self.root,self.root/'test.db')
        (self.root/'config/publisher_studio.json').write_text(json.dumps({'tiktok_actions_paused':True}))
        ids=self.batch()
        with patch.object(worker,'start') as start:
            for command in ('login','review'):
                with self.assertRaises(ValueError): worker.submit(command,1)
            with self.assertRaises(ValueError): worker.enqueue(self.conn,ids)
            start.assert_not_called()

    def test_later_analytics_products_do_not_enter_publisher(self):
        before=len(self.service.catalog())
        with self.conn:
            self.conn.execute("INSERT INTO products(product_key,name,created_at,updated_at) VALUES ('tiktok:new','Novo Analytics','2026','2026')")
        init_publisher(self.conn)
        self.assertEqual(len(self.service.catalog()),before)


if __name__=='__main__': unittest.main()
