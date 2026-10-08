import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from src.publisher import headlines, headline_workflow as workflow
from src.publisher.headline_configuration import machine_readiness
from src.publisher.headline_layout import layout
from src.publisher.headline_tools import RenderError, sha256
from src.publisher.service import get_row, init_publisher
from tests import test_publisher_remote as remote
from tests.test_publisher_headline_renderer import FONT, ROOT
from tests import test_publisher_library_batch_preflight as library_fixture


class HeadlineWorkflowTests(unittest.TestCase):
    setUp_fixture=remote.PublisherRemoteTests.setUp
    def batch(self, count=1):
        ids = remote.PublisherRemoteTests.batch(self, count)
        if self.service.get_product(self.product)['default_headline_enabled']:
            self.service.bulk_headline({'ids':ids,'patch':{'mode':'product_default'}})
        return ids

    def setUp(self):
        self.setUp_fixture()
        font=dict(font_ref='classic',font_sha256=sha256(FONT),font_face_index=0,font_weight=400)
        with patch('src.publisher.headline_configuration.machine_readiness',return_value={'ready':True,'classic_font':font}):
            self.styles=workflow.initialize_styles(self.conn,self.root)
        self.standard=next(s['preset_id'] for s in self.styles if s['style_key']=='WHITE' and s['size_key']=='STANDARD')

    def template(self,product=None,text='OFERTA DO DIA',name='Oferta'):
        return workflow.save_template(self.conn,dict(name=name,text=text,product_id=product))['id']

    def product_config(self,template=None,text='OFERTA DO DIA'):
        self.service.save_product(dict(id=self.product,default_headline_enabled=True,default_headline_text=text,
            default_headline_template_id=template,default_headline_preset_id=self.standard,default_caption='Legenda'))

    def test_global_template(self):
        template=self.template()
        self.assertIsNone(workflow.get_template(self.conn,template,self.product)['product_id'])

    def test_product_template_and_scope_guard(self):
        template=self.template(self.product)
        self.assertEqual(workflow.get_template(self.conn,template,self.product)['product_id'],self.product)
        with self.assertRaises(ValueError):workflow.get_template(self.conn,template,self.product+1)

    def test_edit_and_deactivate(self):
        template=self.template()
        workflow.save_template(self.conn,dict(id=template,name='Outro',text='OUTRA OFERTA',active=False))
        self.assertEqual(workflow.list_templates(self.conn,active=False)[0]['text'],'OUTRA OFERTA')
        self.assertFalse(workflow.list_templates(self.conn,active=True))

    def test_search_and_order(self):
        self.template(name='Oferta');workflow.save_template(self.conn,dict(name='Preço',text='PREÇO NOVO',sort_order=-1))
        self.assertEqual(workflow.list_templates(self.conn)[0]['name'],'Preço')
        self.assertEqual(len(workflow.list_templates(self.conn,search='oferta')),1)

    def test_duplicate_same_scope_rejected(self):
        self.template()
        with self.assertRaisesRegex(ValueError,'DUPLICATE'):self.template(name='Outro nome')

    def test_same_text_different_scope_allowed(self):
        self.template();self.template(self.product)
        self.assertEqual(len(workflow.list_templates(self.conn)),2)

    def test_snapshot_survives_template_edit(self):
        template=self.template();self.product_config(template)
        draft=self.batch()[0];before=headlines.read_snapshot(get_row(self.conn,'publisher_drafts',draft))
        workflow.save_template(self.conn,dict(id=template,name='Oferta',text='ALTERADO'))
        self.assertEqual(headlines.read_snapshot(get_row(self.conn,'publisher_drafts',draft)),before)
        self.assertEqual(before['text'],'OFERTA DO DIA')

    def test_no_headline(self):
        self.product_config()
        self.assertFalse(workflow.selection_snapshot(self.conn,self.service.get_product(self.product),{'mode':'none'})['enabled'])

    def test_product_default(self):
        self.product_config(self.template())
        snapshot=workflow.selection_snapshot(self.conn,self.service.get_product(self.product),{'mode':'product_default'})
        self.assertEqual(snapshot['template']['name'],'Oferta')

    def test_disabled_default_survives_inactive_template(self):
        template=self.template();self.product_config(template)
        self.service.save_product(dict(id=self.product,default_headline_enabled=False,default_headline_template_id=template))
        workflow.save_template(self.conn,dict(id=template,name='Oferta',text='OFERTA DO DIA',active=False))
        product=self.service.get_product(self.product)
        self.assertFalse(workflow.selection_snapshot(self.conn,product)['enabled'])
        draft=self.batch()[0]
        self.assertFalse(headlines.read_snapshot(get_row(self.conn,'publisher_drafts',draft))['enabled'])

    def test_missing_style_with_size_fails_with_blocker(self):
        product=self.service.get_product(self.product)
        product.update(default_headline_enabled=True,default_headline_text='OFERTA')
        with self.assertRaisesRegex(ValueError,'HEADLINE_CONFIG_INCOMPLETE'):
            workflow.selection_snapshot(self.conn,product,{'mode':'product_default','size':'LARGE'})

    def test_global_saved_selection(self):
        template=self.template();self.product_config()
        result=workflow.selection_snapshot(self.conn,self.service.get_product(self.product),dict(mode='template',template_id=template,preset_id=self.standard))
        self.assertEqual(result['text'],'OFERTA DO DIA')

    def test_product_saved_selection(self):
        template=self.template(self.product);self.product_config()
        result=workflow.selection_snapshot(self.conn,self.service.get_product(self.product),dict(mode='template',template_id=template,preset_id=self.standard))
        self.assertEqual(result['template']['id'],template)

    def test_custom_selection(self):
        result=workflow.selection_snapshot(self.conn,self.service.get_product(self.product),dict(mode='custom',text='TEXTO NOVO',preset_id=self.standard,size='LARGE'))
        self.assertEqual(result['text'],'TEXTO NOVO');self.assertEqual(result['size'],'LARGE')

    def test_product_required_does_not_override_per_video_opt_in(self):
        self.product_config();self.service.save_product(dict(id=self.product,headline_required=True))
        snapshot=workflow.selection_snapshot(self.conn,self.service.get_product(self.product),{'mode':'none'})
        self.assertFalse(snapshot['enabled']);self.assertFalse(snapshot['required'])

    def test_initial_styles_distinct_and_supported(self):
        specs=[headlines.get_preset(self.conn,s['preset_id'])['spec'] for s in self.styles if s['size_key']=='STANDARD']
        self.assertEqual(len({headlines.canonical(s) for s in specs}),4)
        self.assertEqual({s['max_lines'] for s in specs},{2})
        self.assertTrue(all(s['shadow_color'] is None for s in specs))

    def test_sizes_explicit_variants(self):
        ids=[workflow.variant(self.conn,self.standard,size) for size in workflow.SIZES]
        self.assertEqual(len(set(ids)),3)
        versions=[headlines.get_preset(self.conn,p)['version'] for p in ids]
        self.assertEqual(versions,[1,1,1])

    def test_safe_area_720_and_1080_all_styles_and_sizes(self):
        for display in ([720,1280],[1080,1920]):
            for style in self.styles:
                spec=headlines.get_preset(self.conn,style['preset_id'])['spec']
                with tempfile.TemporaryDirectory() as directory:
                    plan=layout('OFERTA DO DIA',spec,display,{'classic':FONT},directory)
                x,y,w,h=plan['box'];self.assertLessEqual(y+h,display[1]*.32)
                self.assertLessEqual(h,display[1]*.16);self.assertGreaterEqual(x,display[0]*.14)

    def test_long_text_rejected_without_shrinking(self):
        spec=headlines.get_preset(self.conn,self.standard)['spec']
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(RenderError):layout('OFERTA MUITO LONGA '*20,spec,[720,1280],{'classic':FONT},directory)

    def test_bulk_style_five_drafts(self):
        self.product_config();ids=self.batch(5)
        yellow=next(s['preset_id'] for s in self.styles if s['style_key']=='YELLOW' and s['size_key']=='STANDARD')
        self.service.bulk_headline({'ids':ids,'patch':{'preset_id':yellow}})
        self.assertEqual({headlines.read_snapshot(get_row(self.conn,'publisher_drafts',d))['preset']['id'] for d in ids},{yellow})

    def test_bulk_explicit_text_five_drafts(self):
        ids=self.batch(5)
        self.service.bulk_headline({'ids':ids,'patch':{'mode':'custom','text':'MESMO TEXTO','preset_id':self.standard}})
        self.assertEqual({headlines.read_snapshot(get_row(self.conn,'publisher_drafts',d))['text'] for d in ids},{'MESMO TEXTO'})

    def test_bulk_default_each_product(self):
        self.product_config(text='PRIMEIRO');first=self.batch()[0]
        second_product=self.service.save_product(dict(name='Outro',default_caption='Legenda',default_headline_enabled=True,default_headline_text='SEGUNDO',default_headline_preset_id=self.standard))
        second=self.service.create_remote_batch(dict(product_id=second_product,account_id=self.account,media_asset_ids=[2]))['draft_ids'][0]
        self.service.bulk_headline({'ids':[first,second],'patch':{'mode':'product_default'}})
        self.assertEqual([headlines.read_snapshot(get_row(self.conn,'publisher_drafts',d))['text'] for d in (first,second)],['PRIMEIRO','SEGUNDO'])

    def test_bulk_frozen_atomic_rejection(self):
        self.product_config();ids=self.batch(2)
        before=get_row(self.conn,'publisher_drafts',ids[0])['headline_snapshot_json']
        with self.conn:self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?",(ids[1],))
        with self.assertRaisesRegex(ValueError,'QUEUE_EDIT_BLOCKED'):self.service.bulk_headline({'ids':ids,'patch':{'mode':'none'}})
        self.assertEqual(get_row(self.conn,'publisher_drafts',ids[0])['headline_snapshot_json'],before)

    def test_bulk_size_changes_no_shared_preset(self):
        self.product_config();ids=self.batch(2);before=headlines.get_preset(self.conn,self.standard)
        self.service.bulk_headline({'ids':ids,'patch':{'size':'SMALL'}})
        self.assertEqual(headlines.get_preset(self.conn,self.standard),before)
        self.assertTrue(all(headlines.read_snapshot(get_row(self.conn,'publisher_drafts',d))['size']=='SMALL' for d in ids))

    def test_old_products_disabled_and_additive_migration(self):
        before=self.service.get_product(self.product)
        init_publisher(self.conn)
        self.assertEqual(before,self.service.get_product(self.product));self.assertFalse(before['default_headline_enabled'])


class HeadlineReadinessTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        (self.root/'config').mkdir()
        self.config=dict(enabled=True,classic_font_ref='classic',fonts={'classic':dict(path=str(FONT),sha256=sha256(FONT))})

    def save(self):
        (self.root/'config/headline_renderer.json').write_text(json.dumps(self.config),encoding='utf-8')

    def test_ready_machine(self):
        from src.publisher.headline_tools import resolve_ffmpeg
        from src.media_tools import resolve_ffprobe_path
        self.config.update(ffmpeg_path=str(resolve_ffmpeg(ROOT)),ffprobe_path=str(resolve_ffprobe_path(ROOT)))
        self.save();self.assertTrue(machine_readiness(self.root)['ready'])

    def test_missing_ffmpeg(self):
        self.config['ffmpeg_path']='missing ffmpeg.exe';self.save()
        self.assertFalse(machine_readiness(self.root)['ready'])

    def test_missing_ffprobe(self):
        from src.publisher.headline_tools import resolve_ffmpeg
        self.config.update(ffmpeg_path=str(resolve_ffmpeg(ROOT)),ffprobe_path='missing ffprobe.exe');self.save()
        self.assertFalse(machine_readiness(self.root)['ready'])

    def test_missing_classic_font(self):
        self.config['fonts']['classic']['path']='missing.ttf';self.save()
        self.assertEqual(machine_readiness(self.root)['code'],'HEADLINE_CLASSIC_FONT_UNAVAILABLE')

    def test_font_hash_mismatch(self):
        self.config['fonts']['classic']['sha256']='a'*64;self.save()
        self.assertEqual(machine_readiness(self.root)['code'],'HEADLINE_FONT_HASH_MISMATCH')

    def test_disabled_configuration(self):
        self.config['enabled']=False;self.save();self.assertFalse(machine_readiness(self.root)['ready'])


class HeadlineLibrarySnapshotTests(unittest.TestCase):
    setUp=library_fixture.LibraryBatchPreflightTests.setUp
    _folder=library_fixture.LibraryBatchPreflightTests._folder
    _file=library_fixture.LibraryBatchPreflightTests._file
    def test_per_media_selection_captured_atomically(self):
        self._file('one','folder-a');self._file('two','folder-b')
        preset=headlines.save_preset(self.conn,{'name':'Fixture','spec':{**headlines.DEFAULT_SPEC,'font_ref':'classic','font_sha256':sha256(FONT)}})['id']
        payload=dict(name='Fixture',account_id=self.account_id,drive_file_ids=['one','two'],headline_selections={'one':dict(mode='custom',text='UM',preset_id=preset),'two':{'mode':'none'}})
        with patch('src.publisher.headline_configuration.machine_readiness',return_value={'ready':True}):
            result=self.service.create_library_batch(payload)
        snapshots=[headlines.read_snapshot(get_row(self.conn,'publisher_drafts',d)) for d in result['draft_ids']]
        self.assertEqual(snapshots[0]['text'],'UM');self.assertFalse(snapshots[1]['enabled'])

    def test_readiness_blocks_library_without_reservation(self):
        self._file('one','folder-a')
        preset=headlines.save_preset(self.conn,{'name':'Fixture','spec':{**headlines.DEFAULT_SPEC,'font_ref':'classic','font_sha256':sha256(FONT)}})['id']
        payload=dict(name='Fixture',account_id=self.account_id,drive_file_ids=['one'],headline_selections={'one':dict(mode='custom',text='OFERTA',preset_id=preset)})
        with patch('src.publisher.headline_configuration.machine_readiness',return_value={'ready':False,'code':'HEADLINE_RENDERER_UNAVAILABLE'}):
            preview=self.service.preflight_library_batch(payload)
        self.assertFalse(preview['can_create']);self.assertEqual(preview['items'][0]['blockers'][0]['code'],'HEADLINE_RENDERER_UNAVAILABLE')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_batches').fetchone()[0],0)
