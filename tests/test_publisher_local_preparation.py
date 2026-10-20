"""Synthetic local MP4 jobs; no real Drive, browser session or operational DB."""
import hashlib
from contextlib import closing
import io
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch, MagicMock
import uuid

from src.publisher import headlines
from src.publisher.headline_renderer import HeadlineRenderer
from src.publisher.headline_tools import RenderError, run, sha256
from src.publisher.local_preparation import LocalPreparationManager, review
from src.publisher.service import get_row, log
from src.store import now_iso
from tests import test_publisher_remote as remote
from tests.test_publisher_headline_renderer import ROOT, FONT


class FakeReadOnlyDrive:
    def __init__(self, sources, metadata):
        self.sources, self.metadata = sources, metadata
        self.downloads = []
        self.missing = False
        self.on_download = None

    def get_metadata(self, file_id):
        if self.missing: raise ValueError('LOCAL_SOURCE_UNAVAILABLE')
        return dict(self.metadata[file_id])

    def download_temporary(self, asset, directory, *, cancel_event=None):
        self.downloads.append(asset['remote_file_id'])
        if self.on_download: self.on_download()
        path = Path(directory)/'source.mp4'
        shutil.copyfile(self.sources[asset['remote_file_id']], path)
        return path


class LocalPreparationFixture:
    setup_publisher = remote.PublisherRemoteTests.setUp
    batch = remote.PublisherRemoteTests.batch

    @classmethod
    def setUpClass(cls):
        cls.media_temp = tempfile.TemporaryDirectory(prefix='publisher-local-synthetic-')
        cls.media_root = Path(cls.media_temp.name)
        renderer = HeadlineRenderer(ROOT, cls.media_root/'inputs', cls.media_root/'artifacts', {'fixture': FONT})
        cls.ffmpeg = renderer.ffmpeg
        cls.cfr, cls.vfr = cls.media_root/'cfr.mp4', cls.media_root/'vfr.mp4'
        args=[cls.ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=160x288:rate=12',
              '-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-t','1']
        run([*args,'-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',cls.cfr])
        run([*args,'-vf',"select='if(lt(n,6),1,not(mod(n,2)))'",'-vsync','0','-c:v','libx264','-c:a','aac',cls.vfr])

    @classmethod
    def tearDownClass(cls):
        cls.media_temp.cleanup()

    def setUp(self):
        self.setup_publisher()
        self.sources, self.metadata = {}, {}
        for index,source in enumerate((self.cfr,self.vfr)):
            file_id=f'remote-{index}'; folder=f'local-folder-{index}'; stamp=now_iso()
            checksum=hashlib.md5(source.read_bytes()).hexdigest(); size=source.stat().st_size
            self.sources[file_id]=source
            self.metadata[file_id]=dict(id=file_id,name=f'FINAL_{index}.mp4',mimeType='video/mp4',
                                       size=size,md5Checksum=checksum,parents=[folder],modifiedTime=stamp)
            with self.conn:
                self.conn.execute("INSERT INTO drive_folders(drive_folder_id,name,relative_path,product_id,mapping_status,mapping_source,created_at,updated_at) VALUES (?,?,?,?,'MAPPED_TO_PRODUCT','MANUAL',?,?)",(folder,folder,'/'+folder,self.product,stamp,stamp))
                self.conn.execute("INSERT INTO drive_files(drive_file_id,drive_folder_id,name,mime_type,size,md5_checksum,modified_time,file_kind,classification,last_seen_at,created_at,updated_at) VALUES (?,?,?,'video/mp4',?,?,?,'VIDEO','VALID',?,?,?)",(file_id,folder,f'FINAL_{index}.mp4',size,checksum,stamp,stamp,stamp,stamp))
                self.conn.execute('UPDATE publisher_media_assets SET size_bytes=?,checksum=?,remote_folder_id=? WHERE remote_file_id=?',(size,checksum,folder,file_id))
        self.preset=headlines.save_preset(self.conn,{'name':'Local fixture','spec':{
            **headlines.DEFAULT_SPEC,'font_ref':'fixture','font_sha256':sha256(FONT),'font_size_ratio':.04}})['id']
        from src.publisher import headline_workflow
        with self.conn:
            self.conn.execute("INSERT INTO publisher_headline_style_variants VALUES ('WHITE','STANDARD',?)",(self.preset,))
        self.template=headline_workflow.save_template(self.conn,{'name':'Texto salvo local','text':'TEXTO SALVO'})['id']
        self.service.save_product(dict(id=self.product,default_headline_enabled=True,
                                      default_headline_text='SUGESTAO',default_headline_preset_id=self.preset))
        self.reader=FakeReadOnlyDrive(self.sources,self.metadata)
        self.manager=self.make_manager()
        def remove_owned_files():
            target=self.manager.files.resolve()
            base=(Path(tempfile.gettempdir())/'PublisherDesk/local_preparation').resolve()
            if target.parent != base:
                raise AssertionError('Unsafe fixture cleanup target')
            shutil.rmtree(target,ignore_errors=True)
        self.addCleanup(remove_owned_files)
        self.addCleanup(self.manager.shutdown)
        # Remote entry points are forbidden, even if accidentally reached later.
        for name in ('src.publisher.worker.PublisherWorker.submit',
                     'src.publisher.worker.PublisherWorker.enqueue',
                     'src.publisher.worker.BrowserRuntimeManager.__init__',
                     'src.publisher.lifecycle.confirm_received',
                     'src.publisher.lifecycle.cleanup_remote',
                     'src.publisher.media.GoogleDriveMediaProvider.__init__'):
            guard=patch(name,side_effect=AssertionError('REMOTE_ACTION_FORBIDDEN'))
            guard.start();self.addCleanup(guard.stop)

    def make_manager(self, **kwargs):
        def renderer(root,inputs,cancel):
            return HeadlineRenderer(ROOT,inputs,self.media_root/uuid.uuid4().hex,{'fixture':FONT},cancel_event=cancel)
        return LocalPreparationManager(self.root,self.root/'test.db',reader_factory=lambda identity:self.reader,
                                       renderer_factory=kwargs.pop('renderer_factory',renderer),**kwargs)

    def data(self, enabled=False, ids=('remote-0',)):
        selection={'mode':'custom','text':'OFERTA','preset_id':self.preset,'size':'STANDARD'} if enabled else {'mode':'none'}
        return {'items':[{'drive_file_id':file_id,'headline':selection} for file_id in ids]}

    def enqueue(self, data=None):
        data=data or self.data()
        checked=review(self.conn,data)
        payload={**data,'request_id':uuid.uuid4().hex,'review_token':checked['review_token']}
        with patch.object(self.manager,'_start'):
            jobs=self.manager.enqueue(payload)['jobs']
        return jobs,payload

    def process(self, enabled=False, ids=('remote-0',)):
        jobs,_=self.enqueue(self.data(enabled,ids))
        for job in jobs:self.manager.process(job['id'],self.manager.cancels[job['id']])
        return [next(j for j in self.manager.list()['jobs'] if j['id']==job['id']) for job in jobs]

    def remote_state(self):
        tables=['publisher_drafts','publisher_media_assets','publisher_execution_items',
                'publisher_batch_executions','publisher_execution_logs','account_mutation_ledger',
                'publisher_reconciliation_evidence','publisher_upload_sources']
        tables.append('publisher_derived_media_artifacts')
        return {t:[tuple(r) for r in self.conn.execute('SELECT * FROM '+t)] for t in tables}


class LocalPreparationTests(LocalPreparationFixture, unittest.TestCase):
    def test_defaults_and_product_suggestion_never_enable_implicitly(self):
        result=review(self.conn,{'items':[{'drive_file_id':'remote-0'}]})
        self.assertFalse(result['items'][0]['snapshot']['enabled'])
        explicit=review(self.conn,{'items':[{'drive_file_id':'remote-0','headline':{'mode':'product_default'}}]})
        self.assertTrue(explicit['items'][0]['snapshot']['enabled'])
        self.assertEqual(explicit['items'][0]['snapshot']['text'],'SUGESTAO')

    def test_original_cfr_vfr_validated_without_reencode_or_remote_writes(self):
        before=self.remote_state()
        jobs=self.process(ids=('remote-0','remote-1'))
        for job in jobs:
            self.assertEqual(job['state'],'READY',job)
            self.assertFalse(job['headline_applied'])
            self.assertEqual(job['output_sha256'],sha256(self.sources[job['drive_file_id']]))
            self.assertTrue(job['metadata']['audio'])
            self.assertEqual(job['metadata']['display'],[160,288])
        self.assertEqual(before,self.remote_state())

    def test_enabled_cfr_vfr_real_mp4_and_bound_identity(self):
        before=self.remote_state()
        jobs=self.process(True,('remote-0','remote-1'))
        for job in jobs:
            self.assertEqual(job['state'],'READY',job)
            self.assertNotEqual(job['source_sha256'],job['output_sha256'])
            self.assertTrue(job['headline_applied'])
            self.assertEqual(job['metadata']['video']['codec_name'],'h264')
            self.assertEqual(job['snapshot']['text'],'OFERTA')
        self.assertEqual(before,self.remote_state())

    def test_snapshot_immutable_and_product_edits_do_not_change_job(self):
        jobs,_=self.enqueue(self.data(True));job=jobs[0]
        with self.assertRaisesRegex(sqlite3.IntegrityError,'SNAPSHOT_IMMUTABLE'):
            with self.conn:self.conn.execute("UPDATE publisher_local_preparations SET snapshot_json='{}' WHERE id=?",(job['id'],))
        self.service.save_product(dict(id=self.product,default_headline_text='ALTERADO'))
        self.manager.process(job['id'],self.manager.cancels[job['id']])
        saved=self.manager.list()['jobs'][0]
        self.assertEqual(saved['snapshot']['text'],'OFERTA')
        self.assertEqual(saved['state'],'READY',saved)

    def test_idempotency_and_no_duplicate_execution(self):
        jobs,payload=self.enqueue()
        with patch.object(self.manager,'_start'):
            repeated=self.manager.enqueue(payload)
        self.assertTrue(repeated['reused'])
        self.assertEqual([j['id'] for j in repeated['jobs']],[j['id'] for j in jobs])
        self.manager.process(jobs[0]['id'],self.manager.cancels[jobs[0]['id']])
        self.manager.process(jobs[0]['id'],threading.Event())
        self.assertEqual(len(self.reader.downloads),1)

    def test_exclusive_media_claim_and_publication_reservations_preserved(self):
        jobs,_=self.enqueue()
        with self.assertRaisesRegex(ValueError,'LOCAL_MEDIA_BUSY'):self.enqueue()
        asset=get_row(self.conn,'publisher_media_assets',1)
        with self.assertRaisesRegex(ValueError,'LOCAL_MEDIA_BUSY'):
            self.service.assert_media_available(asset)
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'AVAILABLE')
        self.manager.action(jobs[0]['id'],'cancel',{})
        self.assertEqual(get_row(self.conn,'publisher_media_assets',1)['status'],'AVAILABLE')

    def test_existing_remote_reservation_not_touched(self):
        self.batch()
        before=self.remote_state()
        with self.assertRaises(ValueError):review(self.conn,self.data())
        self.assertEqual(before,self.remote_state())

    def test_historical_929_equivalent_archive_never_grants_reuse(self):
        draft=self.batch()[0]
        with self.conn:
            log(self.conn,draft,'UPLOAD_STARTED','OK','Synthetic historical transfer')
            self.conn.execute("UPDATE publisher_drafts SET status='ARCHIVED',upload_status='TRANSFER_UNKNOWN' WHERE id=?",(draft,))
            self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=1")
        before=self.remote_state()
        with self.assertRaisesRegex(ValueError,'MEDIA_HISTORY_RECONCILIATION_REQUIRED'):review(self.conn,self.data())
        self.assertEqual(before,self.remote_state())
        self.assertEqual(self.reader.downloads,[])

    def test_source_unavailable_failure_has_stage_time_and_no_fallback(self):
        jobs,_=self.enqueue(self.data(True));self.reader.missing=True
        self.manager.process(jobs[0]['id'],threading.Event())
        job=self.manager.list()['jobs'][0]
        self.assertEqual(job['state'],'FAILED')
        self.assertEqual(job['error_code'],'LOCAL_SOURCE_UNAVAILABLE')
        self.assertEqual(job['stage'],'PREPARING_SOURCE')
        self.assertIn('seconds',job['events'][-1]);self.assertIsNone(job['video_url'])

    def test_real_encode_timeout_and_safe_retry_reuses_snapshot(self):
        factory=self.manager.renderer_factory
        def slow(*args):
            renderer=factory(*args);renderer.encode_timeout=.001;return renderer
        self.manager.renderer_factory=slow
        job=self.process(True)[0]
        self.assertEqual(job['state'],'FAILED',job)
        self.assertEqual(job['error_code'],'HEADLINE_PROCESS_TIMEOUT')
        self.manager.renderer_factory=factory
        with patch.object(self.manager,'_start'):
            self.manager.action(job['id'],'retry',{})
        self.manager.process(job['id'],self.manager.cancels[job['id']])
        after=self.manager.list()['jobs'][0]
        self.assertEqual(after['state'],'READY',after)
        self.assertEqual(after['snapshot'],job['snapshot'])

    def test_cooperative_cancel_during_download_and_no_ready_output(self):
        jobs,_=self.enqueue()
        event=self.manager.cancels[jobs[0]['id']]
        self.reader.on_download=event.set
        self.manager.process(jobs[0]['id'],event)
        job=self.manager.list()['jobs'][0]
        self.assertEqual(job['state'],'CANCELLED',job)
        self.assertIsNone(job['video_url'])

    def test_restart_never_requeues_or_calls_remote_worker(self):
        jobs,_=self.enqueue()
        self.manager.shutdown()
        replacement=self.make_manager();self.addCleanup(replacement.shutdown)
        recovered=replacement.list()['jobs'][0]
        self.assertEqual(recovered['id'],jobs[0]['id'])
        self.assertEqual(recovered['state'],'FAILED')
        self.assertEqual(recovered['error_code'],'LOCAL_SERVER_RESTARTED')
        self.assertIsNone(replacement.thread)
        self.assertFalse(self.reader.downloads)

    def test_renderer_cache_reused_after_failed_finalization(self):
        jobs,_=self.enqueue(self.data(True));job_id=jobs[0]['id']
        copy=shutil.copyfile
        def fail_once(source,target,*args,**kwargs):
            if Path(target).name=='video.partial':raise OSError('Synthetic finalization failure')
            return copy(source,target,*args,**kwargs)
        with patch('src.publisher.local_preparation.shutil.copyfile',side_effect=fail_once):
            self.manager.process(job_id,threading.Event())
        self.assertEqual(self.manager.list()['jobs'][0]['state'],'FAILED')
        with patch.object(self.manager,'_start'):self.manager.action(job_id,'retry',{})
        with patch('src.publisher.headline_renderer.run',side_effect=run) as calls:
            self.manager.process(job_id,threading.Event())
        self.assertEqual(self.manager.list()['jobs'][0]['state'],'READY')
        self.assertFalse(any('-filter_complex' in [str(a) for a in call.args[0]] for call in calls.call_args_list))

    def test_review_change_rejected_and_no_partial_jobs(self):
        data=self.data(True);checked=review(self.conn,data)
        with self.conn:self.conn.execute("UPDATE drive_files SET md5_checksum='changed' WHERE drive_file_id='remote-0'")
        with self.assertRaisesRegex(ValueError,'LOCAL_REVIEW_CHANGED'):
            self.manager.enqueue({**data,'request_id':uuid.uuid4().hex,'review_token':checked['review_token']})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_local_preparations').fetchone()[0],0)

    def test_original_validation_does_not_require_headline_configuration(self):
        self.manager.renderer_factory=None
        job=self.process()[0]
        self.assertEqual(job['state'],'READY',job)

    def test_output_http_range_and_controlled_removal(self):
        job=self.process()[0]
        class Handler:
            headers={'Range':'bytes=0-31'}
            def __init__(self):self.wfile=io.BytesIO();self.response_headers={}
            def send_response(self,status):self.status=status
            def send_header(self,k,v):self.response_headers[k]=v
            def end_headers(self):pass
            def send_error(self,status):self.status=status
        handler=Handler();self.manager.serve_video(handler,job['id'])
        self.assertEqual(handler.status,206);self.assertEqual(len(handler.wfile.getvalue()),32)
        with self.assertRaisesRegex(ValueError,'JOB_ID_INVALID'):self.manager._workspace('../agent.db')
        with self.assertRaises(ValueError):self.manager.action(job['id'],'remove',{})
        before=self.remote_state()
        self.manager.action(job['id'],'remove',{'human_confirmed':True})
        self.assertFalse(self.manager._workspace(job['id']).exists())
        self.assertEqual(before,self.remote_state())
        with self.assertRaises(ValueError):self.manager.serve_video(Handler(),job['id'])

    def test_tampered_output_rejected(self):
        job=self.process()[0]
        (self.manager._workspace(job['id'])/'video.mp4').write_bytes(b'not-the-output')
        with self.assertRaisesRegex(ValueError,'OUTPUT_IDENTITY_CHANGED'):
            self.manager.serve_video(None,job['id'])

    def test_local_schema_migration_additive_idempotent(self):
        from src.publisher.local_preparation import init_local_preparation
        before=self.remote_state();init_local_preparation(self.conn);init_local_preparation(self.conn)
        self.assertEqual(before,self.remote_state())

    def test_remote_fields_rejected_before_any_job_or_remote_mutation(self):
        for extra in ({'auto_cleanup':True},{'scheduled_at':'tomorrow'},{'action':'published'},
                      {'path':'C:/Windows/anything.mp4'}):
            with self.assertRaisesRegex(ValueError,'LOCAL_REQUEST_INVALID'):
                review(self.conn,{**self.data(),**extra})
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_local_preparations').fetchone()[0],0)

    def test_source_identity_changed_while_queued_is_blocked_before_drive(self):
        jobs,_=self.enqueue()
        with self.conn:self.conn.execute("UPDATE drive_files SET modified_time='changed' WHERE drive_file_id='remote-0'")
        self.manager.process(jobs[0]['id'],threading.Event())
        self.assertEqual(self.manager.list()['jobs'][0]['error_code'],'LOCAL_SOURCE_IDENTITY_CHANGED')
        self.assertFalse(self.reader.downloads)

    def test_cooperative_cancel_at_encoder_boundary(self):
        jobs,_=self.enqueue(self.data(True));event=threading.Event()
        def cancel_encode(args,**kwargs):
            if '-filter_complex' in [str(a) for a in args]:event.set()
            return run(args,**kwargs)
        with patch('src.publisher.headline_renderer.run',side_effect=cancel_encode):
            self.manager.process(jobs[0]['id'],event)
        job=self.manager.list()['jobs'][0]
        self.assertEqual(job['state'],'CANCELLED',job)
        self.assertIsNone(job['video_url'])

    def test_expired_file_not_served_and_removal_requires_explicit_consent(self):
        job=self.process()[0]
        with self.conn:self.conn.execute("UPDATE publisher_local_preparations SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?",(job['id'],))
        self.assertTrue(self.manager.list()['jobs'][0]['expired'])
        with self.assertRaisesRegex(ValueError,'OUTPUT_NOT_AVAILABLE'):
            self.manager.serve_video(None,job['id'])

    def test_cross_manager_exclusive_owner(self):
        self.enqueue()
        other=self.make_manager();self.addCleanup(other.shutdown)
        with self.assertRaisesRegex(ValueError,'LOCAL_PREPARATION_BUSY'):other.list()

    def test_saved_template_and_bulk_snapshots_bound_individually(self):
        data={'items':[{'drive_file_id':file_id,'headline':{'mode':'template','template_id':self.template,
              'preset_id':self.preset,'size':'STANDARD'}} for file_id in ('remote-0','remote-1')]}
        checked=review(self.conn,data)
        self.assertEqual([i['snapshot']['text'] for i in checked['items']],['TEXTO SALVO']*2)
        self.assertNotEqual(checked['items'][0]['identity'],checked['items'][1]['identity'])

    def test_claim_blocks_publication_even_before_mediaasset_created(self):
        with self.conn:self.conn.execute("DELETE FROM publisher_media_assets WHERE remote_file_id='remote-0'")
        self.enqueue()
        result=self.service.preflight_library_batch({'drive_file_ids':['remote-0'],'account_id':self.account})
        self.assertFalse(result['can_create'])
        self.assertIn('LOCAL_MEDIA_BUSY',json.dumps(result))

    def test_local_http_dispatch_never_reaches_studio_worker(self):
        from src.publisher.http import PublisherAPI
        api=PublisherAPI.__new__(PublisherAPI);api.root=self.root;api.db=self.root/'test.db'
        api._local_manager=self.manager
        class Handler:
            headers={'Content-Type':'application/json'}
            def __init__(self,data):self.rfile=io.BytesIO(json.dumps(data).encode());self.headers={**self.headers,'Content-Length':str(len(self.rfile.getvalue()))}
            def _json(self,value,**kwargs):self.value=value
        handler=Handler(self.data())
        api.post(handler,'/api/publisher/local-preparations/review')
        self.assertTrue(handler.value['no_tiktok_upload'])
        for data in ([],{'path':'../agent.db'}):
            with self.assertRaises(ValueError):api.post(Handler(data),'/api/publisher/local-preparations/review')
        with self.assertRaisesRegex(ValueError,'LOCAL_REQUEST_INVALID'):
            api.get(Handler({}),{'path':['../agent.db']},'/api/publisher/local-preparations/'+'a'*32+'/video')

    def test_immediate_retry_does_not_lose_new_attempt_cancel_event(self):
        jobs,_=self.enqueue();job_id=jobs[0]['id'];original=self.manager.process
        attempts=[];finished=threading.Event()
        # The callback uses its own SQLite connection on the coordinator thread.
        def raced(row_id,event):
            if not attempts:
                from src.store import connect
                with closing(connect(self.root/'test.db')) as conn, conn:
                    conn.execute("UPDATE publisher_local_preparations SET state='FAILED' WHERE id=?",(row_id,))
                attempts.append(event);self.manager.action(row_id,'retry',{})
            else:
                attempts.append(event);original(row_id,event);finished.set()
        with patch.object(self.manager,'process',side_effect=raced):
            self.manager._start()
            self.assertTrue(finished.wait(30),'Retry was lost')
        self.assertEqual(len(attempts),2)
        self.assertIsNot(attempts[0],attempts[1])
        self.assertEqual(self.manager.list()['jobs'][0]['state'],'READY')

    def test_startup_clears_only_interrupted_local_claims_without_resuming(self):
        from src.publisher.local_preparation import recover_local_preparations
        jobs,_=self.enqueue();before=self.remote_state();self.manager.shutdown()
        recover_local_preparations(self.root,self.root/'test.db')
        row=self.conn.execute('SELECT * FROM publisher_local_preparations WHERE id=?',(jobs[0]['id'],)).fetchone()
        self.assertEqual(row['state'],'FAILED')
        self.assertEqual(row['error_code'],'LOCAL_SERVER_RESTARTED')
        self.assertEqual(before,self.remote_state());self.assertFalse(self.reader.downloads)

    def test_valid_video_in_non_mp4_container_is_rejected(self):
        other=self.root/'not-mp4.bin'
        run([self.ffmpeg,'-v','error','-y','-i',self.cfr,'-c','copy','-f','matroska',other])
        checksum=hashlib.md5(other.read_bytes()).hexdigest();size=other.stat().st_size
        self.sources['remote-0']=other
        self.metadata['remote-0'].update(size=size,md5Checksum=checksum)
        with self.conn:
            self.conn.execute("UPDATE drive_files SET size=?,md5_checksum=? WHERE drive_file_id='remote-0'",(size,checksum))
            self.conn.execute("UPDATE publisher_media_assets SET size_bytes=?,checksum=? WHERE remote_file_id='remote-0'",(size,checksum))
        job=self.process()[0]
        self.assertEqual(job['state'],'FAILED',job)
        self.assertEqual(job['error_code'],'LOCAL_MP4_REQUIRED')
        self.assertIsNone(job['video_url'])

    def test_drive_reader_cancels_between_chunks_without_deletion(self):
        from src.publisher.media import GoogleDriveMediaProvider
        provider=GoogleDriveMediaProvider.__new__(GoogleDriveMediaProvider)
        provider.service=MagicMock();provider.get_metadata=lambda file_id:self.metadata[file_id]
        event=threading.Event()
        class Download:
            def __init__(self,stream,*args,**kwargs):self.stream=stream
            def next_chunk(self):
                self.stream.write(b'x');event.set();return None,False
        directory=self.root/'chunk-cancel';directory.mkdir()
        identity=review(self.conn,self.data())['items'][0]['identity']
        with patch('googleapiclient.http.MediaIoBaseDownload',Download):
            with self.assertRaisesRegex(ValueError,'LOCAL_PREPARATION_CANCELLED'):
                provider.download_temporary(identity,directory,cancel_event=event)
        provider.service.files.return_value.delete.assert_not_called()
