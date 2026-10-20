"""Chrome + real local API/renderer, with synthetic SQLite and Fake Drive only."""
from contextlib import closing
import json
from pathlib import Path
import threading
import unittest
from urllib.parse import parse_qs, urlsplit

from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService
from src.store import connect
from tests import test_publisher_local_preparation as local
from tests.test_publisher_headline_workflow_browser import WorkflowHandler
from tests.publisher_headline_fixture import HeadlineFixtureServer, shutdown_fixture_server, close_fixture_browser


class LocalHandler(WorkflowHandler):
    def do_GET(self):
        path=urlsplit(self.path).path
        if path.startswith('/api/publisher/local-preparations'):
            try:self.server.api.get(self,parse_qs(urlsplit(self.path).query),path)
            except (ValueError,TypeError) as exc:self.reply({'error':str(exc)},400)
            return
        if path=='/api/publisher/headline-workflow':
            return self.reply({'templates':[], 'styles':[], 'readiness':{'ready':True}})
        if path=='/api/publisher/library/groups':
            with closing(connect(self.server.db)) as conn:
                return self.reply(PublisherService(conn,self.server.root).media_library_groups())
        if path=='/api/publisher/library/group-items':
            q=parse_qs(urlsplit(self.path).query)
            with closing(connect(self.server.db)) as conn:
                return self.reply(PublisherService(conn,self.server.root).media_library_group_items({k:v[0] for k,v in q.items()}))
        super().do_GET()

    def do_POST(self):
        path=urlsplit(self.path).path
        if path.startswith('/api/publisher/local-preparations'):
            self.server.posts.append(path)
            try:self.server.api.post(self,path)
            except (ValueError,TypeError) as exc:self.reply({'error':str(exc)},400)
            return
        # Local UI must never use a remote mutation or create a remote batch.
        if path not in {'/api/publisher/library-batches/preflight'} and not path.startswith('/api/publisher/headline'):
            self.server.posts.append(path)
            return self.reply({'error':'REMOTE_ROUTE_FORBIDDEN'},403)
        super().do_POST()


class LocalPreparationBrowserTests(local.LocalPreparationFixture, unittest.TestCase):
    def start_fixture(self):
        server=HeadlineFixtureServer(('127.0.0.1',0),LocalHandler)
        server.db=self.root/'test.db';server.root=self.root;server.posts=[]
        api=PublisherAPI.__new__(PublisherAPI);api.root=self.root;api.db=server.db
        api._local_manager=self.manager
        api.headline_renderer=self.manager.renderer_factory(self.root,self.root/'preview-inputs',threading.Event())
        server.api=api
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(shutdown_fixture_server,server,thread)
        return server

    def open_library(self,page,url):
        errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        page.goto(url+'/publisher.html')
        page.locator('[data-view="library"]').first.click()
        page.locator('[data-library-group-toggle]').first.wait_for()
        page.locator('[data-library-group-toggle]').first.click()
        page.locator('[data-library="remote-0"]').wait_for()
        return errors

    def test_library_individual_bulk_progress_video_download_and_refresh(self):
        from playwright.sync_api import sync_playwright
        server=self.start_fixture();url=f'http://127.0.0.1:{server.server_port}'
        before=self.remote_state()
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context(accept_downloads=True,viewport={'width':1360,'height':1000})
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url+'/') else route.abort())
                page=context.new_page();errors=self.open_library(page,url)
                for file_id in ('remote-0','remote-1'):page.locator(f'[data-library="{file_id}"]').check()
                page.locator('#prepareLocalFromLibrary').click()
                dialog=page.locator('#localPreparationDialog')
                self.assertEqual(dialog.locator('[data-local-config] [data-headline-mode]').evaluate_all('(nodes)=>nodes.map(n=>n.value)'),['none','none'])
                first=dialog.locator('[data-local-file="remote-0"]')
                first.locator('[data-headline-mode]').select_option('custom')
                first.locator('[data-headline-text]').fill('OFERTA LOCAL')
                first.locator('[data-headline-style]').select_option(str(self.preset))
                bulk=dialog.locator('#localPreparationBulk')
                dialog.locator('details > summary').click()
                bulk.locator('[data-headline-mode]').select_option('custom')
                bulk.locator('[data-headline-text]').fill('OFERTA BULK')
                bulk.locator('[data-headline-style]').select_option(str(self.preset))
                dialog.locator('#localPreparationApplyBulk').click()
                self.assertEqual(dialog.locator('[data-local-config] [data-headline-text]').all_text_contents(),['OFERTA BULK','OFERTA BULK'])
                bulk.locator('[data-headline-mode]').select_option('none')
                dialog.locator('#localPreparationApplyBulk').click()
                self.assertEqual(first.locator('[data-headline-mode]').input_value(),'none')
                # Explicitly enable only the first video after the bulk reset.
                first.locator('[data-headline-mode]').select_option('custom')
                first.locator('[data-headline-text]').fill('OFERTA')
                first.locator('[data-headline-style]').select_option(str(self.preset))
                page.locator('#localPreparationReview').click()
                page.locator('#localPreparationStart').wait_for(state='visible')
                page.wait_for_function('!document.querySelector("#localPreparationStart").disabled')
                summary=page.locator('#localPreparationSummary').inner_text()
                self.assertIn('remote-0',summary);self.assertIn('remote-1',summary)
                self.assertIn('Sem headline',summary);self.assertIn('OFERTA',summary)
                page.locator('#localPreparationStart').click()
                page.wait_for_function('document.querySelectorAll("[data-local-preview]").length===2',timeout=90000)
                self.assertIn('Nenhum envio ao TikTok',page.locator('#localPreparationJobs').inner_text())
                ready=self.manager.list()['jobs'];self.assertEqual(len(ready),2)
                self.assertEqual(sum(j['headline_applied'] for j in ready),1)
                self.assertTrue(all(len(j['events'])>=5 for j in ready))
                page.locator('[data-local-preview]').first.click()
                page.wait_for_function('document.querySelector("#localPreparationVideo video").readyState>=1')
                self.assertEqual(page.locator('#localPreparationVideo video').evaluate('(v)=>v.videoWidth'),160)
                page.locator('#localPreparationVideo button').click()
                with page.expect_download() as info:page.get_by_role('link',name='Obter MP4 preparado').first.click()
                download=info.value
                self.assertIsNone(download.failure())
                self.assertTrue(download.suggested_filename.endswith('.mp4'))
                evidence=Path(__file__).resolve().parents[1]/'data/publisher/qa/local-preparation-targeted'
                evidence.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(evidence/'local-ready.png'),full_page=True)
                page.reload();page.locator('[data-view="library"]').first.click()
                page.locator('#localPreparationPanel > summary').click()
                page.wait_for_function('document.querySelectorAll("[data-local-preview]").length===2')
                self.assertEqual(len(self.reader.downloads),2)
                self.assertEqual(errors,[])
                self.assertTrue(server.posts)
                self.assertTrue(all(path.startswith('/api/publisher/local-preparations') for path in server.posts),server.posts)
            finally:close_fixture_browser(browser,server)
        self.assertEqual(before,self.remote_state())

    def test_failure_retry_and_media_identity_in_ui(self):
        from playwright.sync_api import sync_playwright
        self.reader.missing=True
        server=self.start_fixture();url=f'http://127.0.0.1:{server.server_port}'
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome',headless=True)
            try:
                context=browser.new_context()
                context.route('**/*',lambda route:route.continue_() if route.request.url.startswith(url+'/') else route.abort())
                page=context.new_page();errors=self.open_library(page,url)
                page.locator('[data-library="remote-1"]').check();page.locator('#prepareLocalFromLibrary').click()
                page.locator('#localPreparationReview').click()
                page.wait_for_function('!document.querySelector("#localPreparationStart").disabled')
                page.locator('#localPreparationStart').click()
                page.get_by_role('button',name='Tentar novamente localmente').wait_for()
                message=page.locator('#localPreparationJobs').inner_text()
                self.assertIn('remote-1',message);self.assertNotIn('remote-0',message)
                self.assertIn('LOCAL_SOURCE_UNAVAILABLE',message);self.assertIn('Etapa:',message)
                evidence=Path(__file__).resolve().parents[1]/'data/publisher/qa/local-preparation-targeted'
                evidence.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(evidence/'local-failure.png'),full_page=True)
                self.reader.missing=False
                page.get_by_role('button',name='Tentar novamente localmente').click()
                page.locator('[data-local-preview]').wait_for(timeout=60000)
                self.assertEqual(len(self.manager.list()['jobs']),1)
                self.assertEqual(errors,[])
            finally:close_fixture_browser(browser,server)
