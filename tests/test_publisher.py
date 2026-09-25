import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.store import connect, init_db
from src.publisher.service import PublisherService, init_publisher, transition, get_row
from src.publisher.worker import prepare_job, PreparationError, recover_sessions
from src.content_pipeline import VideoProbeResult, ValidationStatus


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.conn = connect(self.root / 'test.db')
        self.addCleanup(self.conn.close)
        init_db(self.conn); init_publisher(self.conn)
        self.service = PublisherService(self.conn, self.root)
        self.account = self.service.save_account({'name':'Ceci'})
        self.product = self.service.save_product({'name':'LatteGo','default_caption':'Legenda'})
        self.path = self.root / 'data/publisher/uploads/video.mp4'
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'fixture-video')

    def batch(self, n=1):
        return self.service.create_batch({'product_id':self.product,'account_id':self.account,'caption':'Legenda'},
                                         [(f'video{i}.mp4',self.path) for i in range(n)])['draft_ids']

    def test_ten_drafts_and_bulk_caption(self):
        ids = self.batch(10)
        self.assertEqual(len(ids),10)
        self.service.bulk(ids,{'caption':'Nova legenda'})
        self.assertEqual({x['caption'] for x in self.service.snapshot()['drafts']},{'Nova legenda'})

    def test_bulk_is_atomic_when_preparing(self):
        ids = self.batch(2)
        with self.conn:
            transition(self.conn,ids[1],'PREPARING')
        with self.assertRaises(ValueError):
            self.service.bulk(ids,{'caption':'Nao deve gravar'})
        self.assertEqual(get_row(self.conn,'publisher_drafts',ids[0])['caption'],'Legenda')

    def test_ready_requires_full_checklist(self):
        draft = self.batch()[0]
        with self.conn:
            transition(self.conn,draft,'PREPARING')
        with self.assertRaises(ValueError):
            transition(self.conn,draft,'READY_FOR_REVIEW',checklist={'upload':True})
        with self.conn:
            transition(self.conn,draft,'READY_FOR_REVIEW',checklist=dict.fromkeys(['file','upload','caption','product','settings'],True))
        recover_sessions(self.conn)
        self.assertEqual(get_row(self.conn,'publisher_drafts',draft)['status'],'MANUAL_ACTION_REQUIRED')

    def test_product_crud_preserves_analytics_fields(self):
        self.conn.execute('UPDATE products SET price=99 WHERE id=?',(self.product,)); self.conn.commit()
        self.service.save_product({'id':self.product,'name':'Novo nome','shop_product_id':'123456789','active':False})
        self.assertEqual(get_row(self.conn,'products',self.product)['price'],99)
        self.assertFalse(self.service.catalog(include_archived=True)[0]['active'])
        self.assertEqual(get_row(self.conn,'products',self.product)['name'],'LatteGo')
        self.assertEqual(self.service.catalog(include_archived=True)[0]['name'],'Novo nome')
        self.assertIsNone(get_row(self.conn,'products',self.product)['tiktok_product_id'])

    def test_account_restriction_and_presets(self):
        other=self.service.save_account({'name':'Outra'})
        preset=self.service.save_preset({'name':'Padrao','account_id':other})
        with self.assertRaises(ValueError):
            self.service.create_batch({'product_id':self.product,'account_id':self.account,'preset_id':preset},[('x.mp4',self.path)])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0],0)

    def test_archive_and_duplicate(self):
        draft=self.batch()[0]
        self.service.bulk([draft],{},'duplicate')
        self.assertEqual(len(self.service.snapshot()['drafts']),2)
        self.service.bulk([draft],{},'archive')
        self.assertTrue(self.path.exists())
        with self.assertRaises(ValueError):
            self.service.bulk([draft],{'caption':'x'})

    def test_invalid_file_does_not_create_partial_batch(self):
        with self.assertRaises(ValueError):
            self.service.create_batch({'product_id':self.product,'account_id':self.account},[('x.mp4',self.path),('y.mp4',self.root/'missing.mp4')])
        self.assertEqual(len(self.service.snapshot()['drafts']),0)

    def test_worker_never_accepts_missing_caption(self):
        with self.assertRaises(PreparationError) as cm:
            prepare_job(object(),{'caption':' '}, {}, self.path, lambda *x:None)
        self.assertEqual(cm.exception.code,'CAPTION_MISSING')

    def test_worker_adapter_prepares_without_publication_method(self):
        class Adapter:
            def prepare(self,*args):
                return dict.fromkeys(['file','upload','caption','settings'],True)
        with patch('src.publisher.worker.probe_video',return_value=VideoProbeResult(ValidationStatus.PASS,{'ffprobe_available':True})):
            checks=prepare_job(Adapter(),{'caption':'x'}, {},self.path,lambda *x:None)
        self.assertTrue(all(checks.values()))


if __name__ == '__main__':
    unittest.main()
