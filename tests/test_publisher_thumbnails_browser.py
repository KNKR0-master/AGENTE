"""Grouped Library thumbnail behavior in an isolated Chrome profile."""

import base64
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from tests.test_publisher_library_groups_browser import GroupFixtureHandler


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l7sAAAAASUVORK5CYII="
)


class ThumbnailFixtureHandler(GroupFixtureHandler):
    thumbnail_requests = []

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/publisher/library/thumbnail/"):
            file_id = path.rsplit("/", 1)[-1]
            type(self).thumbnail_requests.append(file_id)
            if file_id != "G1C1A1":
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)
            return
        return super().do_GET()


@unittest.skipUnless(shutil.which("chrome") or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists(),
                     "Chrome Stable is required")
class ThumbnailBrowserTests(unittest.TestCase):
    def test_lazy_group_success_and_fallback(self):
        from playwright.sync_api import sync_playwright

        ThumbnailFixtureHandler.thumbnail_requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), ThumbnailFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/publisher.html")
                    page.locator('nav [data-view="library"]').click()
                    page.locator('[data-library-group-toggle="product:1"]').wait_for()
                    self.assertEqual(ThumbnailFixtureHandler.thumbnail_requests, [])
                    page.locator('[data-library-group-toggle="product:1"]').click()
                    page.locator('[data-library="G1C2A1"]').wait_for()
                    page.wait_for_function("['G1C1A1','G1C2A1'].every(id => document.querySelector(`[data-library=${id}]`)?.closest('tr')?.querySelector('img')?.complete)")
                    self.assertCountEqual(ThumbnailFixtureHandler.thumbnail_requests, ["G1C1A1", "G1C2A1"])
                    success = page.locator('[data-library="G1C1A1"]').locator("xpath=ancestor::tr")
                    failure = page.locator('[data-library="G1C2A1"]').locator("xpath=ancestor::tr")
                    self.assertTrue(success.locator("img").is_visible())
                    self.assertEqual(success.locator(".media-thumbnail.placeholder").evaluate("e => getComputedStyle(e).display"), "none")
                    self.assertFalse(failure.locator("img").is_visible())
                    self.assertTrue(failure.locator(".media-thumbnail.placeholder").is_visible())
                    self.assertEqual(success.locator("img").get_attribute("loading"), "lazy")
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
