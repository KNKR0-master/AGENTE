import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.publisher import headlines, headline_workflow as workflow
from src.publisher.headline_configuration import machine_readiness
from src.publisher.headline_renderer import HeadlineRenderer
from src.publisher.headline_tools import run, sha256, resolve_ffmpeg
from src.publisher.http import PublisherAPI
from src.publisher.service import get_row
from src.publisher.upload_preparation import artifact_root
from src.media_tools import resolve_ffprobe_path
from src.store import now_iso
from tests import test_publisher_remote as remote
from tests.test_publisher_headlines_browser import HeadlineFixtureHandler
from tests.test_publisher_headline_renderer import FONT, ROOT


class WorkflowHandler(HeadlineFixtureHandler):
    def _json(self,value,status=200,**kwargs):self.reply(value,status)

    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/publisher/headline-workflow':
            self.server.api.get(self,{},path);return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/publisher/headline') or self.path.endswith(('/headline-preview','/headline')) or self.path=='/api/publisher/library-batches/preflight':
            self.server.posts.append(self.path)
            try:self.server.api.post(self,self.path)
            except (ValueError,TypeError) as exc:self.reply({'error':str(exc)},400)
        else:super().do_POST()


class HeadlineWorkflowBrowserTests(unittest.TestCase):
    setUp_fixture=remote.PublisherRemoteTests.setUp
    batch=remote.PublisherRemoteTests.batch

    def setUp(self):
        self.setUp_fixture()
        config=dict(enabled=True,ffmpeg_path=str(resolve_ffmpeg(ROOT)),ffprobe_path=str(resolve_ffprobe_path(ROOT)),
                    classic_font_ref='classic',fonts={'classic':dict(path=str(FONT),sha256=sha256(FONT))})
        (self.root/'config/headline_renderer.json').write_text(json.dumps(config),encoding='utf-8')
        self.styles=workflow.initialize_styles(self.conn,self.root)
        self.standard=next(s['preset_id'] for s in self.styles if s['style_key']=='WHITE' and s['size_key']=='STANDARD')
        self.template=workflow.save_template(self.conn,{'name':'Oferta global','text':'OFERTA DO DIA'})['id']
        self.service.save_product(dict(id=self.product,default_headline_enabled=True,default_headline_template_id=self.template,
                                      default_headline_preset_id=self.standard,default_caption='Legenda'))
        self.other=self.service.save_product(dict(name='Outro produto',default_caption='Outra legenda',default_headline_enabled=True,
                                                 default_headline_text='OUTRA OFERTA',default_headline_preset_id=self.standard))
        self.draft=self.batch()[0]
        inputs=self.root/'data/publisher/temp';inputs.mkdir(parents=True,exist_ok=True)
        self.renderer=HeadlineRenderer(ROOT,inputs,artifact_root(self.root),{'classic':FONT})
        self.addCleanup(lambda:__import__('shutil').rmtree(artifact_root(self.root),ignore_errors=True))
        source=inputs/'local.mp4'
        run([self.renderer.ffmpeg,'-v','error','-y','-f','lavfi','-i','color=c=teal:size=360x640:rate=24','-t','1','-c:v','libx264',source])
        with self.conn:
            self.conn.execute('UPDATE publisher_drafts SET video_path=? WHERE id=?',(str(source),self.draft))
            for index,product in ((1,self.product),(2,self.other)):
                stamp=now_iso();folder=f'folder-{index}';file=f'remote-{index}'
                self.conn.execute("INSERT INTO drive_folders(drive_folder_id,name,relative_path,product_id,mapping_status,mapping_source,created_at,updated_at) VALUES (?,?,?,?,'MAPPED_TO_PRODUCT','MANUAL',?,?)",(folder,f'Produto {index}',f'/Inbox/Produto {index}',product,stamp,stamp))
                self.conn.execute("INSERT INTO drive_files(drive_file_id,drive_folder_id,name,mime_type,size,md5_checksum,file_kind,classification,last_seen_at,created_at,updated_at) VALUES (?,?,?,'video/mp4',?,?,'VIDEO','NEEDS_REVIEW',?,?,?)",(file,folder,f'FINAL_{index}.mp4',10+index,f'hash-{index}',stamp,stamp,stamp))
                self.conn.execute('UPDATE publisher_media_assets SET remote_folder_id=? WHERE remote_file_id=?',(folder,file))

    def test_saved_workflow_per_video_mask_and_frozen_ui(self):
        from playwright.sync_api import sync_playwright
        server=ThreadingHTTPServer(('127.0.0.1',0),WorkflowHandler)
        server.db=self.root/'test.db';server.root=self.root;server.posts=[]
        api=PublisherAPI.__new__(PublisherAPI);api.root=self.root;api.db=server.db;api.headline_renderer=self.renderer
        server.api=api
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome',headless=True)
                try:
                    page=browser.new_page(viewport={'width':1360,'height':1000});errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-view=presets]').click()
                    page.get_by_role('button',name='Verificar renderer',exact=True).click()
                    page.wait_for_function("document.querySelector('#headlineMachineStatus').dataset.ready==='true'")
                    page.get_by_role('button',name='Criar headline',exact=True).click()
                    form=page.locator('#headlineTemplateForm');form.locator('[name=name]').fill('Nova global');form.locator('[name=text]').fill('ACHADO DO DIA');form.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#headlineTemplateList').textContent.includes('Nova global')")
                    page.locator('#headlineTemplateSearch').fill('Nova global')
                    self.assertEqual(page.locator('#headlineTemplateList .record').count(),1)
                    page.locator('#headlineTemplateList [data-template-edit]').click();form.locator('[name=text]').fill('NOVO ACHADO');form.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#headlineTemplateList').textContent.includes('NOVO ACHADO')")
                    page.locator('#headlineTemplateList [data-template-deactivate]').click()
                    page.wait_for_function("document.querySelector('#headlineTemplateList .record')===null")
                    page.locator('#headlineTemplateFilter').select_option('inactive');self.assertEqual(page.locator('#headlineTemplateList .record').count(),1)
                    page.locator('[data-view=products]').click();page.locator(f'[data-product-edit="{self.product}"]').click()
                    product=page.locator('#productForm');self.assertEqual(product.locator('[name=default_headline_template_id]').input_value(),str(self.template))
                    product.locator('[data-new-product-headline]').click()
                    form.locator('[name=name]').fill('Default contextual');form.locator('[name=text]').fill('DEFAULT LOCAL');form.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#productForm [name=default_headline_template_id]').selectedOptions[0].textContent.includes('Default contextual')")
                    saved=workflow.list_templates(self.conn)
                    self.assertEqual(len(saved),3)
                    previous=next(t for t in saved if t['name']=='Nova global')
                    self.assertFalse(previous['active']);self.assertIsNone(previous['product_id']);self.assertEqual(previous['text'],'NOVO ACHADO')
                    product.locator('[name=default_headline_template_id]').select_option(str(self.template))
                    product.locator('[name=default_headline_size]').select_option('SMALL');product.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#notice').textContent.includes('salvos')")
                    self.assertEqual(self.service.get_product(self.product)['default_headline_size'],'SMALL')
                    page.locator('[data-view=library]').first.click()
                    page.evaluate("""items=>{for(const item of items)uiState.setLibrarySelection(item.drive_file_id,true,item);librarySelectionChanged();openLibraryBatchPanel();}""",[
                        dict(drive_file_id='remote-1',filename='FINAL_1.mp4',product_id=self.product,product_name='Produto',caption='Legenda',status='AVAILABLE'),
                        dict(drive_file_id='remote-2',filename='FINAL_2.mp4',product_id=self.other,product_name='Outro produto',caption='Outra legenda',status='AVAILABLE')])
                    page.locator('#libraryHeadlineSelection > details > summary').click()
                    first=page.locator('[data-headline-media="remote-1"]');second=page.locator('[data-headline-media="remote-2"]')
                    first.locator('[data-headline-mode]').select_option(f'template:{self.template}')
                    self.assertFalse(first.locator('[data-headline-custom]').is_visible())
                    second.locator('[data-headline-mode]').select_option('custom');second.locator('[data-headline-text]').fill('OFERTA ESPECIAL')
                    yellow=next(s['preset_id'] for s in self.styles if s['style_key']=='YELLOW' and s['size_key']=='STANDARD')
                    second.locator('[data-headline-style]').select_option(str(yellow));second.locator('[data-headline-size]').select_option('LARGE')
                    page.wait_for_timeout(5500)
                    self.assertEqual(second.locator('[data-headline-text]').input_value(),'OFERTA ESPECIAL')
                    payload=page.evaluate('libraryBatchPayload()');self.assertEqual(payload['headline_selections']['remote-2']['size'],'LARGE')
                    self.assertEqual(payload['headline_selections']['remote-1']['template_id'],self.template)
                    page.screenshot(path=str(ROOT/'data/publisher/qa/headline-h25-library-desktop.png'),full_page=True)
                    page.locator('#libraryBatchClose').click()
                    page.locator('[data-view=queue]').first.click();page.locator(f'[data-edit="{self.draft}"]').first.click();page.locator('#draftHeadlineEntry button').click()
                    page.locator('#draftHeadlineForm [data-save-template]').click()
                    form.locator('[name=name]').fill('Draft contextual');form.locator('[name=text]').fill('OFERTA LOCAL');form.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#draftHeadlineForm [name=mode]').selectedOptions[0].textContent==='Draft contextual'")
                    self.assertEqual(page.locator('#draftHeadlineForm [name=text]').input_value(),'OFERTA LOCAL')
                    headline=page.locator('#draftHeadlineForm');headline.locator('[name=mode]').select_option(f'template:{self.template}');headline.locator('[name=headline_preset_id]').select_option(str(self.standard));headline.locator('[name=size]').select_option('STANDARD')
                    self.assertTrue(page.get_by_role('button',name='Gerar prévia',exact=True).is_disabled())
                    headline.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#draftHeadline p').textContent==='OFERTA DO DIA'")
                    before=headlines.read_snapshot(get_row(self.conn,'publisher_drafts',self.draft))
                    video=self.renderer.render(self.conn,self.draft,before,get_row(self.conn,'publisher_drafts',self.draft)['video_path'])
                    video_hash=sha256(video['local_path'])
                    page.get_by_role('button',name='Gerar prévia',exact=True).click()
                    page.wait_for_function("document.querySelector('.headline-preview img')?.naturalWidth===360")
                    self.assertTrue(page.locator('.headline-platform-mask').is_visible())
                    page.screenshot(path=str(ROOT/'data/publisher/qa/headline-h25-preview-desktop.png'),full_page=True)
                    page.locator('.headline-preview-stage').evaluate("node=>node.scrollIntoView({block:'center'})")
                    page.locator('#headlineDialog').screenshot(path=str(ROOT/'data/publisher/qa/headline-h25-mask-desktop.png'))
                    requests=len(server.posts);page.get_by_label('Mostrar área segura do TikTok').uncheck()
                    self.assertFalse(page.locator('.headline-platform-mask').is_visible());self.assertEqual(len(server.posts),requests)
                    self.assertEqual(sha256(video['local_path']),video_hash)
                    self.assertEqual(headlines.read_snapshot(get_row(self.conn,'publisher_drafts',self.draft))['render_spec_hash'],before['render_spec_hash'])
                    page.set_viewport_size({'width':390,'height':844});page.get_by_label('Mostrar área segura do TikTok').check()
                    page.screenshot(path=str(ROOT/'data/publisher/qa/headline-h25-preview-mobile.png'),full_page=True)
                    page.locator('.headline-preview-stage').evaluate("node=>node.scrollIntoView({block:'center'})")
                    page.locator('#headlineDialog').screenshot(path=str(ROOT/'data/publisher/qa/headline-h25-mask-mobile.png'))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                    page.evaluate("""()=>{window.previewApi=api;window.previewImage=document.querySelector('.headline-preview img').src;api=()=>new Promise(resolve=>window.resolveOldPreview=resolve);}""")
                    page.get_by_role('button',name='Gerar prévia',exact=True).click()
                    page.locator('#draftHeadlineForm [name=text]').fill('EDIÇÃO PENDENTE')
                    page.evaluate("""()=>{api=window.previewApi;window.resolveOldPreview({state:'READY',image:window.previewImage});}""")
                    page.wait_for_function("document.querySelector('.headline-preview').textContent==='Prévia desatualizada'")
                    self.assertTrue(page.get_by_role('button',name='Gerar prévia',exact=True).is_disabled())
                    self.assertEqual(headlines.read_snapshot(get_row(self.conn,'publisher_drafts',self.draft))['render_spec_hash'],before['render_spec_hash'])
                    page.locator('#headlineDialog [title=Fechar]').click();page.locator('[data-close=editDialog]').click()
                    with self.conn:self.conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED' WHERE id=?",(self.draft,))
                    page.reload();page.locator(f'[data-review="{self.draft}"]').first.click()
                    self.assertEqual(page.locator('#draftHeadlineForm').count(),0)
                    self.assertFalse(errors,errors);self.assertFalse(self.provider.downloads or self.provider.deleted)
                    self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_batches').fetchone()[0],1)
                    self.assertTrue(machine_readiness(self.root)['ready'])
                finally:browser.close()
        finally:server.shutdown();server.server_close();thread.join(5)
