import json
import threading
import unittest
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
from tests.publisher_headline_fixture import (
    HeadlineFixtureServer, close_fixture_browser, shutdown_fixture_server,
)


class WorkflowHandler(HeadlineFixtureHandler):
    def _json(self,value,status=200,**kwargs):self.reply(value,status)

    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/api/publisher/headline-workflow' or path.endswith('/swatch'):
            from urllib.parse import parse_qs
            try:self.server.api.get(self,parse_qs(urlsplit(self.path).query),path)
            except (ValueError,TypeError) as exc:self.reply({'error':str(exc)},400)
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/publisher/headline') or self.path.endswith(('/headline-preview','/headline')) or self.path=='/api/publisher/library-batches/preflight':
            self.server.posts.append(self.path)
            try:self.server.api.post(self,self.path)
            except (ValueError,TypeError) as exc:self.reply({'error':str(exc)},400)
        else:super().do_POST()


class DelayedCatalogHandler(WorkflowHandler):
    def reply(self,value,status=200):
        if urlsplit(self.path).path=='/api/publisher' and not self.server.catalog_started.is_set():
            self.server.catalog_started.set()
            if not self.server.catalog_gate.wait(10) or self.server.catalog_failure:
                return super().reply({'error':'FIXTURE_CATALOG_UNAVAILABLE'},503)
        return super().reply(value,status)


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
        self.service.override_headline(self.draft,{'mode':'product_default'})
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

    def wait_for_headline_catalog(self,page):
        page.wait_for_function("""presetId=>{
            const select=document.querySelector('#productHeadline [name=default_headline_preset_id]');
            return select&&[...select.options].some(option=>option.value===presetId);
        }""",arg=str(self.standard))

    def start_catalog_fixture(self,*,failure=False):
        server=HeadlineFixtureServer(('127.0.0.1',0),DelayedCatalogHandler)
        server.db=self.root/'test.db';server.root=self.root;server.posts=[]
        server.catalog_started=threading.Event();server.catalog_gate=threading.Event();server.catalog_failure=failure
        api=PublisherAPI.__new__(PublisherAPI);api.root=self.root;api.db=server.db;api.headline_renderer=self.renderer
        server.api=api
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(shutdown_fixture_server,server,thread)
        return server

    def open_pre_hydration_panel(self,page):
        page.locator('[data-view=library]').first.click()
        items=[dict(drive_file_id=f'remote-{i}',filename=f'FINAL_{i}.mp4',
                    product_id=product,product_name='Fixture',caption='Legenda',status='AVAILABLE')
               for i,product in ((1,self.product),(2,self.other))]
        page.evaluate("""items=>{for(const item of items)uiState.setLibrarySelection(item.drive_file_id,true,item);
            librarySelectionChanged();openLibraryBatchPanel();}""",items)
        page.locator('#libraryHeadlineSelection > details > summary').click()

    def test_saved_workflow_per_video_mask_and_frozen_ui(self):
        from playwright.sync_api import sync_playwright
        server=HeadlineFixtureServer(('127.0.0.1',0),WorkflowHandler)
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
                    self.wait_for_headline_catalog(page)
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
                finally:close_fixture_browser(browser,server)
        finally:shutdown_fixture_server(server,thread)

    def test_library_opt_in_bulk_confirmation_and_unselected_media(self):
        from playwright.sync_api import sync_playwright
        server=HeadlineFixtureServer(('127.0.0.1',0),WorkflowHandler)
        server.db=self.root/'test.db';server.root=self.root;server.posts=[]
        api=PublisherAPI.__new__(PublisherAPI);api.root=self.root;api.db=server.db;api.headline_renderer=self.renderer
        server.api=api
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            before={table:[tuple(r) for r in self.conn.execute('SELECT * FROM '+table)]
                    for table in ('publisher_drafts','publisher_batches','publisher_products')}
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome',headless=True)
                try:
                    page=browser.new_page(viewport={'width':1360,'height':1000})
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-view=library]').first.click()
                    self.wait_for_headline_catalog(page)
                    self.assertEqual(self.standard,2)
                    items=[dict(drive_file_id=f'remote-{i}',filename=f'FINAL_{i}.mp4',
                                product_id=product,product_name='Fixture',caption='Legenda',status='AVAILABLE')
                           for i,product in ((1,self.product),(2,self.other))]
                    page.evaluate("""items=>{for(const item of items)uiState.setLibrarySelection(item.drive_file_id,true,item);librarySelectionChanged();openLibraryBatchPanel();}""",items)
                    page.locator('#libraryHeadlineSelection > details > summary').click()
                    first=page.locator('[data-headline-media="remote-1"] [data-headline-mode]')
                    second=page.locator('[data-headline-media="remote-2"] [data-headline-mode]')
                    self.assertEqual(first.input_value(),'none');self.assertEqual(second.input_value(),'none')
                    first.select_option('product_default')
                    page.wait_for_timeout(5500)
                    payload=page.evaluate('libraryBatchPayload().headline_selections')
                    self.assertEqual(payload['remote-1']['mode'],'product_default')
                    self.assertEqual(payload.get('remote-2',{'mode':'none'})['mode'],'none')
                    bulk=page.locator('#headlineMediaBulk')
                    bulk.locator('[data-headline-mode]').select_option('custom')
                    bulk.locator('[data-headline-text]').fill('ONLY SELECTED')
                    bulk.locator('[data-headline-style]').select_option(str(self.standard))
                    confirmations=[]
                    def cancel(dialog):
                        confirmations.append(dialog.message);dialog.dismiss()
                    page.once('dialog',cancel)
                    bulk.locator('[data-bulk-text]').click()
                    self.assertIn('Vídeos afetados: 2',confirmations[0])
                    self.assertIn('ONLY SELECTED',confirmations[0])
                    self.assertIn('Classic',confirmations[0])
                    self.assertEqual(second.input_value(),'none')
                    page.once('dialog',lambda dialog:dialog.accept())
                    bulk.locator('[data-bulk-text]').click()
                    payload=page.evaluate('libraryBatchPayload().headline_selections')
                    self.assertEqual(set(payload),{'remote-1','remote-2'})
                    self.assertTrue(all(choice['text']=='ONLY SELECTED' for choice in payload.values()))
                    page.evaluate("""()=>{uiState.setLibrarySelection('remote-2',false);librarySelectionChanged();}""")
                    self.assertNotIn('remote-2',page.evaluate('libraryBatchPayload().headline_selections'))
                    self.assertFalse(any(path.endswith('/library-batches') or '/enqueue' in path for path in server.posts))
                finally:close_fixture_browser(browser,server)
            for table,rows in before.items():
                self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM '+table)],rows)
            self.assertFalse(self.provider.downloads or self.provider.deleted)
        finally:shutdown_fixture_server(server,thread)

    def test_late_catalog_preserves_open_panel_and_explicit_opt_in(self):
        from playwright.sync_api import sync_playwright
        server=self.start_catalog_fixture()
        before={table:[tuple(r) for r in self.conn.execute('SELECT * FROM '+table)]
                for table in ('publisher_drafts','publisher_batches','publisher_products',
                              'publisher_headline_presets','publisher_headline_preset_versions')}
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(viewport={'width':1360,'height':1000})
                page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                self.assertTrue(server.catalog_started.wait(5))
                page.evaluate("""()=>{window.headlineRenderedSnapshots=0;const original=PublisherHeadlines.render;
                    PublisherHeadlines.render=function(state){const result=original(state);window.headlineRenderedSnapshots++;return result;};}""")
                self.open_pre_hydration_panel(page)
                bulk=page.locator('#headlineMediaBulk');style=bulk.locator('[data-headline-style]')
                self.assertTrue(style.is_disabled());self.assertIn('Carregando estilos',style.inner_text())
                self.assertTrue(bulk.locator('[data-bulk-text]').is_disabled())
                bulk.locator('[data-headline-mode]').select_option('custom')
                bulk.locator('[data-headline-size]').select_option('LARGE')
                bulk.locator('[data-headline-text]').fill('ANTES DO CATÁLOGO')
                page.locator('[data-headline-media="remote-1"] [data-headline-mode]').select_option('product_default')
                bulk.locator('[data-headline-text]').focus()
                page.evaluate("""()=>{window.beforeHydration={text:document.querySelector('#headlineMediaBulk [data-headline-text]'),
                    style:document.querySelector('#headlineMediaBulk [data-headline-style]'),
                    mode:document.querySelector('#headlineMediaBulk [data-headline-mode]'),
                    size:document.querySelector('#headlineMediaBulk [data-headline-size]')};
                    window.beforeHydration.text.setSelectionRange(3,8);}""")
                server.catalog_gate.set()
                page.wait_for_function("document.querySelector('#headlineMediaBulk [data-headline-style]').dataset.catalogState==='READY'")
                self.assertTrue(page.evaluate("""()=>Object.entries(window.beforeHydration).every(([key,node])=>
                    node===document.querySelector('#headlineMediaBulk [data-headline-'+key+']'))"""))
                self.assertTrue(page.evaluate('document.activeElement===window.beforeHydration.text'))
                self.assertEqual(page.evaluate('[window.beforeHydration.text.selectionStart,window.beforeHydration.text.selectionEnd]'),[3,8])
                self.assertEqual(bulk.locator('[data-headline-text]').input_value(),'ANTES DO CATÁLOGO')
                self.assertEqual(bulk.locator('[data-headline-mode]').input_value(),'custom')
                self.assertEqual(bulk.locator('[data-headline-size]').input_value(),'LARGE')
                self.assertEqual(style.locator('option').evaluate_all('options=>options.map(option=>option.value)'),['','8','11','2','5'])
                self.assertTrue(page.locator('#libraryBatchDialog').evaluate('dialog=>dialog.open'))
                self.assertTrue(page.locator('#libraryHeadlineSelection details').evaluate('details=>details.open'))
                choices=page.evaluate('libraryBatchPayload().headline_selections')
                self.assertEqual(choices['remote-1']['mode'],'product_default');self.assertNotIn('remote-2',choices)
                self.assertEqual(page.evaluate('librarySelected.size'),2)
                style.select_option(str(self.standard))
                page.evaluate("""()=>{window.originalStyleOptions=[...window.beforeHydration.style.options];
                    window.originalSampleUpdate=window.beforeHydration.style._sampleUpdate;window.styleChangeEvents=0;
                    window.beforeHydration.style.addEventListener('change',()=>window.styleChangeEvents++);}""")
                page.wait_for_function('window.headlineRenderedSnapshots>=2')
                self.assertEqual(style.input_value(),str(self.standard))
                self.assertEqual(style.locator('option').count(),5)
                self.assertTrue(page.evaluate('window.originalStyleOptions.every((option,index)=>option===window.beforeHydration.style.options[index])'))
                self.assertTrue(page.evaluate('window.originalSampleUpdate===window.beforeHydration.style._sampleUpdate'))
                self.assertEqual(page.evaluate('window.styleChangeEvents'),0)
                bulk.locator(f'.headline-style-sample[data-preset-id="{self.standard}"]').click()
                self.assertEqual(page.evaluate('window.styleChangeEvents'),1)
                # A vanished explicit identity must stay visible and blocked, never become a default.
                page.evaluate("""()=>PublisherHeadlines.render({...state,
                    headline_presets:state.headline_presets.filter(p=>p.id!==2),
                    headline_styles:state.headline_styles.filter(s=>s.preset_id!==2)})""")
                self.assertEqual(style.input_value(),'2')
                self.assertTrue(style.locator('option[value="2"]').evaluate('option=>option.disabled===true'))
                self.assertTrue(bulk.locator('[data-bulk-text]').is_disabled())
                self.assertTrue(style.evaluate('select=>!!select.validationMessage'))
                page.evaluate('PublisherHeadlines.render(state)')
                self.assertEqual(style.input_value(),'2');self.assertFalse(bulk.locator('[data-bulk-text]').is_disabled())
                self.assertFalse(any(path.endswith('/library-batches') or '/enqueue' in path for path in server.posts))
            finally:close_fixture_browser(browser,server)
        for table,rows in before.items():
            self.assertEqual([tuple(r) for r in self.conn.execute('SELECT * FROM '+table)],rows)
        self.assertFalse(self.provider.downloads or self.provider.deleted)

    def test_failed_initial_catalog_keeps_bulk_confirmation_blocked(self):
        from playwright.sync_api import sync_playwright
        server=self.start_catalog_fixture(failure=True)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(viewport={'width':1360,'height':1000})
                page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                self.assertTrue(server.catalog_started.wait(5))
                self.open_pre_hydration_panel(page)
                bulk=page.locator('#headlineMediaBulk')
                bulk.locator('[data-headline-mode]').select_option('custom')
                bulk.locator('[data-headline-text]').fill('PRESERVAR TEXTO')
                server.catalog_gate.set()
                page.wait_for_function("document.querySelector('#headlineMediaBulk [data-headline-style]').dataset.catalogState==='ERROR'")
                self.assertIn('Estilos indisponíveis',bulk.locator('[data-headline-style]').inner_text())
                self.assertTrue(bulk.locator('[data-headline-style]').is_disabled())
                self.assertTrue(bulk.locator('[data-bulk-text]').is_disabled())
                self.assertTrue(bulk.locator('[data-bulk-style]').is_disabled())
                self.assertEqual(bulk.locator('[data-headline-text]').input_value(),'PRESERVAR TEXTO')
                self.assertEqual(bulk.locator('[data-headline-mode]').input_value(),'custom')
                self.assertEqual(page.evaluate('librarySelected.size'),2)
                self.assertFalse(any(path.endswith('/library-batches') or '/enqueue' in path for path in server.posts))
            finally:close_fixture_browser(browser,server)

    def test_missing_video_preview_reports_source_without_enabling_or_uploading(self):
        from playwright.sync_api import sync_playwright
        server=self.start_catalog_fixture()
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                page=browser.new_page(viewport={'width':1360,'height':1000})
                page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                self.assertTrue(server.catalog_started.wait(5))
                self.open_pre_hydration_panel(page)
                server.catalog_gate.set()
                self.wait_for_headline_catalog(page)
                row=page.locator('[data-headline-media="remote-2"]')
                row.locator('[data-headline-mode]').select_option('custom')
                row.locator('[data-headline-text]').fill('OFERTA DO DIA')
                row.locator('[data-headline-style]').select_option(str(self.standard))
                row.locator('[data-style-preview]').click()
                page.wait_for_function("document.querySelector('[data-headline-media=remote-2] .headline-style-preview').dataset.previewFailure==='SOURCE'")
                self.assertIn('Nenhum download',row.locator('.headline-style-preview').inner_text())
                self.assertEqual(row.locator('[data-headline-text]').input_value(),'OFERTA DO DIA')
                self.assertEqual(page.evaluate('PublisherHeadlines.selectionPayload()["remote-1"]?.mode||"none"'),'none')
                self.assertTrue(row.get_by_role('button',name='Tentar prévia novamente',exact=True).is_enabled())
                self.assertFalse(self.provider.downloads or self.provider.deleted)
                self.assertFalse(any(path.endswith('/library-batches') or '/enqueue' in path for path in server.posts))
            finally:close_fixture_browser(browser,server)
