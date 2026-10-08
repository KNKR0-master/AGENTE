import json
from contextlib import closing
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

from src.publisher import headlines
from src.publisher.headline_renderer import HeadlineRenderer
from src.publisher.headline_tools import RenderError, run, resolve_ffmpeg, sha256
from src.publisher.upload_preparation import (prepare_upload_source, artifact_root, reconciliation_source,
    upload_source_record, cleanup_derived_after_consumption)
from src.publisher.worker import PublisherWorker, PreparationError, restore_pre_upload_failure
from src.publisher.service import get_row, init_publisher, assert_upload_allowed
from src.publisher.lifecycle import cleanup_remote
from src.store import connect
from tests import test_publisher_remote as remote
from tests.test_publisher_headline_renderer import FONT, ROOT, snapshot


class HeadlinePreparationTests(unittest.TestCase):
    setUp_fixture = remote.PublisherRemoteTests.setUp
    def batch(self, count=1):
        ids = remote.PublisherRemoteTests.batch(self, count)
        if self.service.get_product(self.product)['default_headline_enabled']:
            self.service.bulk_headline({'ids':ids,'patch':{'mode':'product_default'}})
        return ids

    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='publisher-h3-source-')
        cls.source = Path(cls.temp.name) / 'synthetic.mp4'
        run([resolve_ffmpeg(ROOT),'-v','error','-y','-f','lavfi','-i',
             'color=c=teal:size=360x640:rate=24','-t','0.25','-c:v','libx264',cls.source])

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.setUp_fixture()
        self.worker = PublisherWorker(self.root,self.root/'test.db',provider_factory=lambda asset:self.provider)
        self.renderer = HeadlineRenderer(ROOT,self.worker.cache.root,artifact_root(self.root),{'fixture':FONT},cancel_event=self.worker.stop_event)
        self.addCleanup(lambda: shutil.rmtree(artifact_root(self.root),ignore_errors=True))
        self.worker.headline_renderer_factory = lambda:self.renderer
        readiness=patch('src.publisher.headline_configuration.machine_readiness',return_value={'ready':True})
        readiness.start();self.addCleanup(readiness.stop)
        self.uploads=[];self.ambiguous=False;self.before_upload=None
        self.file_input=MagicMock()
        page=MagicMock(); context=MagicMock();context.pages=[page]
        self.worker.context=MagicMock(return_value=context)
        self.worker.browser_runtime.verify_auth=MagicMock()
        (self.root/'config/publisher_studio.json').write_text(json.dumps({'tiktok_actions_paused':False,'validated':True,'durable_confirmation_validated':True}))
        def download(asset,directory):
            self.provider.downloads.append(asset['remote_file_id'])
            path=Path(directory)/'video.mp4';shutil.copyfile(self.source,path);return path
        self.provider.download_temporary=download
        case=self
        class Adapter:
            transfer_confirmed=False
            save_attempted=False
            def __init__(self,page,mapping,before,after,observer): self.before=before;self.after=after
            def prepare(self,draft,preset,path,evidence):
                if case.before_upload:case.before_upload(path)
                self.before('VIDEO_UPLOAD')
                evidence('UPLOAD_STARTED','fake boundary')
                case.file_input.set_input_files(str(path))
                case.uploads.append(Path(path))
                draft['_upload_status']='TRANSFER_UNKNOWN' if case.ambiguous else 'TRANSFER_CONFIRMED'
                draft['_processing_status']='UNKNOWN' if case.ambiguous else 'MEDIA_READY_FOR_EDIT'
                if case.ambiguous:raise PreparationError('MANUAL_ACTION_REQUIRED','fixture ambiguous')
                self.transfer_confirmed=True;self.after('VIDEO_UPLOAD')
                self.before('SAVE_DRAFT');self.after('SAVE_DRAFT')
                evidence('SAVE_DRAFT','fixture')
                evidence('TIKTOK_DRAFT_CONFIRMED','fixture')
                result=dict.fromkeys(('file','upload','caption','settings','durable'),True)
                result['tiktok_draft_url']='https://www.tiktok.com/tiktokstudio/content'
                return result
            def inspect_current_upload(self,name):return {'media_ready_for_edit':False}
        for target,value in [('src.publisher.worker.StudioPage',Adapter),('src.publisher.worker.ensure_ffprobe_available',lambda root:self.renderer.ffprobe)]:
            mocked=patch(target,value);mocked.start();self.addCleanup(mocked.stop)

    def configure(self,text='OFERTA DO DIA'):
        preset=headlines.save_preset(self.conn,{'name':'Fixture','spec':snapshot()['preset']['spec']})['id']
        self.service.save_product({'id':self.product,'default_headline_enabled':True,'default_headline_text':text,'default_headline_preset_id':preset})

    def enqueue(self,ids):
        with patch.object(self.worker,'start'):
            execution=self.worker.enqueue(self.conn,ids)
        with self.conn:
            self.conn.execute("UPDATE publisher_execution_items SET status='PROCESSING' WHERE execution_id=? AND draft_id=?",(execution,ids[0]))
        return execution

    def local_prepare(self,draft):
        directory=self.worker.cache.root/'manual';directory.mkdir(exist_ok=True)
        source=directory/'original.mp4';shutil.copyfile(self.source,source)
        return prepare_upload_source(self.conn,get_row(self.conn,'publisher_drafts',draft),source,lambda:self.renderer,self.worker.stop_event)

    def test_worker_original_does_not_instantiate_renderer(self):
        draft=self.batch()[0];self.enqueue([draft])
        self.worker.headline_renderer_factory=MagicMock(side_effect=AssertionError('renderer forbidden'))
        self.worker.prepare(None,self.conn,draft)
        row=get_row(self.conn,'publisher_drafts',draft)
        self.assertEqual(self.uploads,[Path(row['temp_path'])])
        self.file_input.set_input_files.assert_called_once_with(row['temp_path'])
        self.assertEqual(upload_source_record(self.conn,row)['kind'],'ORIGINAL')
        self.worker.headline_renderer_factory.assert_not_called()

    def test_per_video_only_authorized_source_is_derived(self):
        self.configure()
        ids=remote.PublisherRemoteTests.batch(self,2)
        self.service.override_headline(ids[0],{'mode':'product_default'})
        sources=[self.local_prepare(draft_id) for draft_id in ids]
        self.assertEqual([source.kind for source in sources],['HEADLINE_DERIVED','ORIGINAL'])
        self.assertFalse(self.uploads)
        self.file_input.set_input_files.assert_not_called()
        self.assertFalse(self.provider.downloads or self.provider.deleted)
        self.assertFalse(self.conn.execute('SELECT 1 FROM account_mutation_ledger').fetchone())

    def test_worker_derived_e2e_persistence_and_cleanup(self):
        self.configure();draft=self.batch()[0];execution=self.enqueue([draft])
        self.worker.prepare(None,self.conn,draft)
        row=get_row(self.conn,'publisher_drafts',draft);record=upload_source_record(self.conn,row)
        self.assertEqual(row['persistence_status'],'PERSISTED')
        self.assertEqual(record['kind'],'HEADLINE_DERIVED')
        self.assertNotEqual(self.uploads[0],Path(row['temp_path']))
        self.file_input.set_input_files.assert_called_once_with(str(self.uploads[0]))
        steps=[r[0] for r in self.conn.execute('SELECT step FROM publisher_execution_logs WHERE draft_id=? ORDER BY id',(draft,))]
        self.assertLess(steps.index('HEADLINE_ARTIFACT_READY'),steps.index('UPLOAD_STARTED'))
        self.assertEqual(record['render_spec_hash'],headlines.read_snapshot(row)['render_spec_hash'])
        self.assertTrue(Path(row['temp_path']).exists())
        with self.conn:self.conn.execute("UPDATE publisher_execution_items SET status='CLEANUP' WHERE execution_id=?",(execution,))
        self.assertTrue(cleanup_remote(self.conn,draft,self.provider,self.root))
        self.assertFalse(self.uploads[0].exists());self.assertFalse(Path(row['temp_path']).exists())
        self.assertEqual(self.provider.deleted,['remote-0'])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_upload_sources').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_derived_media_artifacts').fetchone()[0],1)
        self.assertTrue(cleanup_remote(self.conn,draft,self.provider,self.root))
        self.assertEqual(self.provider.deleted,['remote-0'])

    def test_render_failure_before_upload_pauses_tail_without_side_effect(self):
        self.configure('\U0001fae0');ids=self.batch(3);execution=self.enqueue(ids)
        with self.assertRaises(PreparationError):self.worker.prepare(None,self.conn,ids[0])
        self.assertFalse(self.uploads)
        self.assertFalse(self.conn.execute("SELECT 1 FROM publisher_execution_logs WHERE step='UPLOAD_STARTED'").fetchone())
        self.assertTrue(restore_pre_upload_failure(self.conn,ids[0]))
        self.worker.finish_item(self.conn,ids[0],'WAITING_MANUAL','MANUAL_ACTION_REQUIRED')
        self.assertEqual(self.conn.execute('SELECT status FROM publisher_batch_executions WHERE id=?',(execution,)).fetchone()[0],'PAUSED')
        self.assertEqual(get_row(self.conn,'publisher_drafts',ids[1])['status'],'DRAFT')
        self.assertEqual(get_row(self.conn,'publisher_drafts',ids[0])['upload_status'],'NOT_STARTED')
        self.assertFalse(self.conn.execute('SELECT 1 FROM account_mutation_ledger').fetchone())

    def test_ambiguous_transfer_retains_exact_source_and_blocks_retry(self):
        self.configure();draft=self.batch()[0];self.enqueue([draft]);self.ambiguous=True
        with self.assertRaises(PreparationError):self.worker.prepare(None,self.conn,draft)
        row=get_row(self.conn,'publisher_drafts',draft)
        self.assertEqual(row['upload_status'],'TRANSFER_UNKNOWN')
        self.assertTrue(Path(row['temp_path']).exists());self.assertTrue(self.uploads[0].exists())
        identity=reconciliation_source(self.conn,row)
        self.assertEqual(identity['artifact_sha256'],sha256(self.uploads[0]))
        self.assertEqual(identity['thumbnail_authority'],'ACTUAL_UPLOADED_ARTIFACT')
        self.assertFalse(identity['thumbnail_matching_used'])
        with self.assertRaises(ValueError):self.worker.prepare(None,self.conn,draft)
        self.assertEqual(len(self.uploads),1)
        with self.assertRaises(RenderError):cleanup_derived_after_consumption(self.conn,draft,self.root)

    def test_corruption_at_handoff_blocks_setinputfiles(self):
        self.configure();draft=self.batch()[0];self.enqueue([draft])
        self.before_upload=lambda path:Path(path).write_bytes(b'corrupted')
        with self.assertRaises(PreparationError):self.worker.prepare(None,self.conn,draft)
        self.assertFalse(self.uploads)
        self.assertFalse(self.conn.execute("SELECT 1 FROM publisher_execution_logs WHERE step='UPLOAD_STARTED'").fetchone())

    def test_product_edit_does_not_change_frozen_snapshot(self):
        self.configure();draft=self.batch()[0];self.enqueue([draft])
        before=headlines.read_snapshot(get_row(self.conn,'publisher_drafts',draft))
        self.service.save_product({'id':self.product,'default_headline_text':'Changed product'})
        self.worker.prepare(None,self.conn,draft)
        row=get_row(self.conn,'publisher_drafts',draft)
        self.assertEqual(upload_source_record(self.conn,row)['render_spec_hash'],before['render_spec_hash'])
        self.assertEqual(row['caption'],'Legenda')

    def test_ready_artifact_reused_before_any_upload(self):
        self.configure();draft=self.batch()[0]
        first=self.local_prepare(draft);second=self.local_prepare(draft)
        self.assertEqual(first.artifact_id,second.artifact_id)
        self.assertNotEqual(first.record_id,second.record_id)

    def test_source_changed_rejected_before_handoff(self):
        self.configure();draft=self.batch()[0];prepared=self.local_prepare(draft)
        prepared.source_path.write_bytes(b'changed')
        with self.assertRaisesRegex(RenderError,'SOURCE_IDENTITY'):
            prepared.validate(self.conn,get_row(self.conn,'publisher_drafts',draft))

    def test_preview_cannot_be_upload_candidate(self):
        self.configure();draft=self.batch()[0];prepared=self.local_prepare(draft)
        with self.conn:self.conn.execute("UPDATE publisher_derived_media_artifacts SET kind='preview' WHERE id=?",(prepared.artifact_id,))
        with self.assertRaises(RenderError):prepared.validate(self.conn,get_row(self.conn,'publisher_drafts',draft))

    def test_missing_artifact_never_falls_back(self):
        self.configure();draft=self.batch()[0];prepared=self.local_prepare(draft);prepared.path.unlink()
        with self.assertRaises(RenderError):prepared.validate(self.conn,get_row(self.conn,'publisher_drafts',draft))

    def test_restart_stale_generating_is_reconciled(self):
        self.configure();draft=self.batch()[0];prepared=self.local_prepare(draft)
        with self.conn:self.conn.execute("UPDATE publisher_derived_media_artifacts SET state='GENERATING' WHERE id=?",(prepared.artifact_id,))
        second=self.local_prepare(draft)
        self.assertNotEqual(prepared.artifact_id,second.artifact_id)
        self.assertEqual(self.conn.execute('SELECT error_code FROM publisher_derived_media_artifacts WHERE id=?',(prepared.artifact_id,)).fetchone()[0],'HEADLINE_RENDER_INTERRUPTED')

    def test_cancelled_worker_never_uploads(self):
        self.configure();draft=self.batch()[0];self.enqueue([draft]);self.worker.stop_event.set()
        with self.assertRaises(PreparationError):self.worker.prepare(None,self.conn,draft)
        self.assertFalse(self.uploads)

    def test_cancel_during_child_process(self):
        import sys
        stop=threading.Event();timer=threading.Timer(.15,stop.set);timer.start()
        try:
            with self.assertRaisesRegex(RenderError,'CANCELLED'):
                run([sys.executable,'-c','import time;time.sleep(30)'],cancel_event=stop)
        finally:timer.join()

    def test_cancel_during_render_marks_failed_without_upload(self):
        self.configure();draft=self.batch()[0];self.enqueue([draft])
        from src.publisher import headline_renderer
        original_run=headline_renderer.run
        def cancel_render(args,**kwargs):
            if '-filter_complex' in args:
                self.worker.stop_event.set()
            return original_run(args,**kwargs)
        with patch.object(headline_renderer,'run',side_effect=cancel_render):
            with self.assertRaises(PreparationError):self.worker.prepare(None,self.conn,draft)
        self.file_input.set_input_files.assert_not_called()
        rows=self.conn.execute('SELECT state,error_code FROM publisher_derived_media_artifacts').fetchall()
        self.assertEqual([tuple(r) for r in rows],[('FAILED','HEADLINE_PREPARATION_CANCELLED')])

    def test_renderer_missing_blocks_before_upload(self):
        self.configure();draft=self.batch()[0];self.enqueue([draft])
        self.worker.headline_renderer_factory=None
        with self.assertRaises(PreparationError):self.worker.prepare(None,self.conn,draft)
        self.file_input.set_input_files.assert_not_called()
        self.assertFalse(self.conn.execute('SELECT 1 FROM account_mutation_ledger').fetchone())

    def test_corrupt_cache_is_replaced_only_before_upload(self):
        self.configure();draft=self.batch()[0];prepared=self.local_prepare(draft)
        prepared.path.write_bytes(b'bad cache')
        replacement=self.local_prepare(draft)
        self.assertNotEqual(prepared.artifact_id,replacement.artifact_id)
        replacement.validate(self.conn,get_row(self.conn,'publisher_drafts',draft))
        self.file_input.set_input_files.assert_not_called()

    def test_legacy_null_snapshot_uses_original(self):
        draft=self.batch()[0]
        with self.conn:self.conn.execute('UPDATE publisher_drafts SET headline_snapshot_json=NULL WHERE id=?',(draft,))
        prepared=self.local_prepare(draft)
        self.assertEqual(prepared.kind,'ORIGINAL')

    def test_frozen_headline_override_stays_blocked(self):
        self.configure();draft=self.batch()[0];self.enqueue([draft])
        with self.assertRaises(ValueError):self.service.override_headline(draft,{'text':'changed'})

    def test_migration_idempotent_preserves_drafts(self):
        draft=self.batch()[0];before=dict(get_row(self.conn,'publisher_drafts',draft))
        init_publisher(self.conn);init_publisher(self.conn)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft),before)

    def test_reconciliation_identity_survives_connection_restart(self):
        self.configure();draft=self.batch()[0];prepared=self.local_prepare(draft)
        with closing(connect(self.root/'test.db')) as reopened:
            identity=reconciliation_source(reopened,get_row(reopened,'publisher_drafts',draft))
        self.assertEqual(identity['artifact_id'],prepared.artifact_id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute("UPDATE publisher_upload_sources SET source_sha256='changed'")

    def test_multi_product_source_selection(self):
        self.configure();first=self.batch()[0]
        second_product=self.service.save_product({'name':'Other','default_caption':'Other caption'})
        second=self.service.create_remote_batch({'product_id':second_product,'account_id':self.account,'media_asset_ids':[2]})['draft_ids'][0]
        third_product=self.service.save_product({'name':'Third','default_caption':'Third caption',
            'default_headline_enabled':True,'default_headline_text':'OTHER HEADLINE','default_headline_preset_id':1})
        third=self.service.create_remote_batch({'product_id':third_product,'account_id':self.account,'media_asset_ids':[3],
            'headline_selections':{'3':{'mode':'product_default'}}})['draft_ids'][0]
        with self.conn:
            for position,draft in enumerate((second,third),2):
                self.conn.execute('UPDATE publisher_drafts SET batch_id=?,queue_position=? WHERE id=?',
                    (get_row(self.conn,'publisher_drafts',first)['batch_id'],position,draft))
        sources=[self.local_prepare(d) for d in (first,second,third)]
        self.assertEqual([s.kind for s in sources],['HEADLINE_DERIVED','ORIGINAL','HEADLINE_DERIVED'])
        self.assertNotEqual(sources[0].render_spec_hash,sources[2].render_spec_hash)
