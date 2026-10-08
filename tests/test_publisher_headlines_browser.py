import json
import shutil
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from src.store import connect
from src.publisher import headlines
from src.publisher.service import PublisherService, get_row
from tests import test_publisher_remote as remote
from tests.test_publisher_queue_browser import QueueFixtureHandler, fixture


class HeadlineFixtureHandler(QueueFixtureHandler):
    def reply(self, data, status=200):
        body=json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        path=urlsplit(self.path).path
        if path not in ('/api/publisher','/api/publisher/products/workspace'):
            return super().do_GET()
        with closing(connect(self.server.db)) as conn:
            service=PublisherService(conn,self.server.root)
            if path.endswith('/workspace'):
                products=service.catalog(include_archived=True)
                return self.reply({'products':products,'counts':{'current':len(products)},'scope':'current'})
            body=fixture()
            body.update(service.snapshot())
            self.reply(body)

    def do_POST(self):
        path=urlsplit(self.path).path
        data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.server.posts.append(path)
        with closing(connect(self.server.db)) as conn:
            service=PublisherService(conn,self.server.root)
            try:
                if path=='/api/publisher/headline-presets': result=headlines.save_preset(conn,data)
                elif path=='/api/publisher/products': result={'id':service.save_product(data)}
                elif path.endswith('/headline'): result=service.override_headline(int(path.split('/')[-2]),data)
                else: return self.reply({'error':'FIXTURE_ROUTE_NOT_ALLOWED'},400)
                self.reply(result)
            except ValueError as exc: self.reply({'error':str(exc)},400)


@unittest.skipUnless(shutil.which('chrome') or Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe').exists(), 'Chrome Stable required')
class HeadlineBrowserTests(unittest.TestCase):
    setUp=remote.PublisherRemoteTests.setUp
    batch=remote.PublisherRemoteTests.batch

    def test_product_preset_and_draft_configuration_in_isolated_db(self):
        from playwright.sync_api import sync_playwright
        server=ThreadingHTTPServer(('127.0.0.1',0),HeadlineFixtureHandler)
        server.db=self.root/'test.db'; server.root=self.root; server.posts=[]
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        errors=[]
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome',headless=True)
                try:
                    page=browser.new_page(viewport={'width':1360,'height':1000})
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-view="presets"]').click()
                    page.locator('[data-headline-advanced] > summary').click()
                    form=page.locator('#headlinePresetForm')
                    form.locator('[name=name]').fill('Fixture Visual')
                    form.locator('[name=font_ref]').fill('fixture-font')
                    form.locator('summary').click()
                    form.locator('[name=font_sha256]').fill('a'*64)
                    form.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#headlinePresetList').textContent.includes('Fixture Visual')")
                    page.locator('[data-view="products"]').click()
                    page.locator(f'[data-product-edit="{self.product}"]').click()
                    product=page.locator('#productForm')
                    product.locator('[name=default_headline_enabled]').check()
                    product.locator('[name=default_headline_text]').fill('x'*501)
                    self.assertEqual(product.locator('[name=default_headline_text]').input_value(),'x'*501)
                    product.locator('[name=default_headline_text]').fill('A\u00e7\u00e3o 25% \U0001f31f')
                    product.locator('[name=default_headline_preset_id]').select_option('1')
                    page.wait_for_timeout(5500)
                    self.assertEqual(product.locator('[name=default_headline_text]').input_value(),'A\u00e7\u00e3o 25% \U0001f31f')
                    product.locator('[type=submit]').click()
                    page.wait_for_function("document.querySelector('#notice').textContent.includes('salvos')")
                    draft=self.batch()[0]
                    self.service.override_headline(draft,{'mode':'product_default'})
                    page.reload()
                    page.locator(f'[data-edit="{draft}"]').first.click()
                    page.locator('#draftHeadlineEntry button').click()
                    self.assertIn('A\u00e7\u00e3o 25%',page.locator('#draftHeadline').inner_text())
                    self.assertIn('Fixture Visual / v1',page.locator('#draftHeadline').inner_text())
                    page.locator('#draftHeadlineForm [name=text]').fill('Draft override')
                    page.locator('#draftHeadlineForm [type=submit]').click()
                    page.wait_for_function("document.querySelector('#draftHeadline p')?.textContent==='Draft override'")
                    self.assertEqual(headlines.read_snapshot(get_row(self.conn,'publisher_drafts',draft))['provenance'],'DRAFT_OVERRIDE')
                    self.assertEqual(self.service.get_product(self.product)['default_headline_text'],'A\u00e7\u00e3o 25% \U0001f31f')
                    self.assertFalse(errors,errors)
                    self.assertEqual(server.posts,['/api/publisher/headline-presets','/api/publisher/products',f'/api/publisher/drafts/{draft}/headline'])
                    self.assertFalse(self.provider.deleted or self.provider.downloads)
                finally: browser.close()
        finally:
            server.shutdown();server.server_close();thread.join(5)
