import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from src.publisher.headline_renderer import HeadlineRenderer, init_artifacts
from src.publisher.headline_tools import run
from src.publisher.headlines import canonical
from src.publisher.http import PublisherAPI
from tests.test_publisher_headline_renderer import FONT, ROOT, snapshot
from tests.test_publisher_headlines_browser import HeadlineFixtureHandler
from tests import test_publisher_remote as remote


class PreviewHandler(HeadlineFixtureHandler):
    def _json(self, value, status=200, **kwargs):
        self.reply(value, status)

    def do_POST(self):
        if self.path.endswith('/headline-preview'):
            self.server.posts.append(self.path)
            try:
                self.server.api.post(self, self.path)
            except ValueError as exc:
                self.reply({'error': str(exc)}, 400)
        else:
            super().do_POST()


class HeadlinePreviewBrowserTests(unittest.TestCase):
    setUp = remote.PublisherRemoteTests.setUp
    batch = remote.PublisherRemoteTests.batch

    def test_explicit_preview_real_renderer_isolated_ui(self):
        from playwright.sync_api import sync_playwright
        with tempfile.TemporaryDirectory(prefix='publisher-h2-ui-') as directory:
            temp = Path(directory)
            inputs = temp / 'inputs'; inputs.mkdir()
            renderer = HeadlineRenderer(ROOT, inputs, temp / 'artifacts', {'fixture': FONT})
            source = inputs / 'local.mp4'
            run([renderer.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
                 'color=c=teal:size=360x640:rate=24', '-t', '1', '-c:v', 'libx264', source])
            draft = self.batch()[0]
            init_artifacts(self.conn)
            self.conn.execute('UPDATE publisher_drafts SET video_path=?,headline_snapshot_json=? WHERE id=?',
                              (str(source), canonical(snapshot()), draft))
            self.conn.commit()
            server = ThreadingHTTPServer(('127.0.0.1', 0), PreviewHandler)
            server.db = self.root / 'test.db'; server.root = self.root; server.posts = []
            api = PublisherAPI.__new__(PublisherAPI)
            api.root = self.root; api.db = server.db; api.headline_renderer = renderer
            server.api = api
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(channel='chrome', headless=True)
                    try:
                        page = browser.new_page(viewport={'width': 1360, 'height': 1000})
                        errors=[]; page.on('pageerror', lambda error: errors.append(str(error)))
                        page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                        page.locator(f'[data-edit="{draft}"]').first.click()
                        page.locator('#draftHeadlineEntry button').click()
                        page.wait_for_timeout(5500)
                        self.assertEqual(server.posts, [])
                        page.get_by_role('button', name='Gerar pr\u00e9via', exact=True).click()
                        page.wait_for_function("document.querySelector('.headline-preview img')?.naturalWidth===360")
                        self.assertEqual(server.posts, [f'/api/publisher/drafts/{draft}/headline-preview'])
                        self.assertFalse(errors, errors)
                        self.assertFalse(self.provider.downloads or self.provider.deleted)
                        self.assertEqual(self.conn.execute("SELECT kind FROM publisher_derived_media_artifacts WHERE state='READY'").fetchone()[0], 'preview')
                    finally:
                        browser.close()
            finally:
                server.shutdown(); server.server_close(); thread.join(5)
