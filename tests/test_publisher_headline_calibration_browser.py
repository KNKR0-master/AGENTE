import json
import threading
import unittest

from src.publisher import headlines
from src.publisher.http import PublisherAPI
from src.publisher.headline_tools import sha256
from tests import test_publisher_headline_workflow_browser as workflow_fixture
from tests.test_publisher_headline_renderer import ROOT
from tests.publisher_headline_fixture import (
    HeadlineFixtureServer, close_fixture_browser, shutdown_fixture_server,
)


class CalibratedStyleBrowserTests(unittest.TestCase):
    setUp=workflow_fixture.HeadlineWorkflowBrowserTests.setUp
    setUp_fixture=workflow_fixture.HeadlineWorkflowBrowserTests.setUp_fixture
    batch=workflow_fixture.HeadlineWorkflowBrowserTests.batch

    def test_swatches_and_readonly_frozen_comparison_desktop_mobile(self):
        from playwright.sync_api import sync_playwright
        server=HeadlineFixtureServer(('127.0.0.1',0),workflow_fixture.WorkflowHandler)
        server.db=self.root/'test.db';server.root=self.root;server.posts=[]
        api=PublisherAPI.__new__(PublisherAPI);api.root=self.root;api.db=server.db;api.headline_renderer=self.renderer
        server.api=api
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with self.conn:self.conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED' WHERE id=?",(self.draft,))
            before=sha256(server.db)
            snapshot=self.conn.execute('SELECT headline_snapshot_json FROM publisher_drafts WHERE id=?',(self.draft,)).fetchone()[0]
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome',headless=True)
                try:
                    page=browser.new_page(viewport={'width':1360,'height':1000});errors=[]
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-view=products]').first.click()
                    page.locator(f'[data-product-edit="{self.product}"]').click()
                    page.locator('#productHeadline .headline-style-sample').first.scroll_into_view_if_needed()
                    page.wait_for_function("[...document.querySelectorAll('#productHeadline .headline-style-sample img')].some(i=>i.naturalWidth===320)")
                    self.assertGreater(page.locator('#productHeadline .headline-style-sample').count(),2)
                    self.assertEqual(page.locator('#productHeadline [data-style-preview]').count(),1)
                    page.locator('[data-view=queue]').first.click()
                    page.locator(f'[data-review="{self.draft}"]').first.click()
                    self.assertEqual(page.locator('#draftHeadlineForm').count(),0)
                    page.locator('.headline-style-comparison>summary').click()
                    yellow=next(s['preset_id'] for s in self.styles if s['style_key']=='YELLOW' and s['size_key']=='STANDARD')
                    page.locator('[data-comparison-style]').select_option(str(yellow))
                    page.get_by_role('button',name='Gerar prévia',exact=True).click()
                    page.wait_for_function("document.querySelector('.headline-style-preview img')?.naturalWidth===360")
                    self.assertIn('Amarelo Forte',page.locator('#draftHeadline .headline-style-preview').inner_text())
                    page.get_by_label('Mostrar área segura do TikTok').uncheck()
                    self.assertFalse(page.locator('.headline-platform-mask').is_visible())
                    page.locator('[data-comparison-style]').select_option(str(self.standard))
                    self.assertEqual(page.locator('#draftHeadline .headline-style-preview').inner_text(),'Prévia desatualizada. Render final pendente.')
                    page.get_by_role('button',name='Gerar prévia',exact=True).click()
                    page.wait_for_function("document.querySelector('.headline-style-preview img')?.naturalWidth===360")
                    qa=ROOT/'data/publisher/qa';qa.mkdir(parents=True,exist_ok=True)
                    page.locator('#reviewDialog').screenshot(path=str(qa/'headline-calibration-desktop.png'))
                    page.set_viewport_size({'width':390,'height':844})
                    page.locator('#reviewDialog').screenshot(path=str(qa/'headline-calibration-mobile.png'))
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'),390)
                    self.assertFalse(errors,errors)
                    self.assertTrue(all(path=='/api/publisher/headline-preview/compare' for path in server.posts),server.posts)
                finally:close_fixture_browser(browser,server)
            self.assertEqual(sha256(server.db),before)
            self.assertEqual(self.conn.execute('SELECT headline_snapshot_json FROM publisher_drafts WHERE id=?',(self.draft,)).fetchone()[0],snapshot)
            self.assertFalse(self.provider.downloads or self.provider.deleted)
        finally:shutdown_fixture_server(server,thread)
