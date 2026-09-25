import json
import shutil
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tests.test_publisher_queue_browser import ROOT, fixture


def workspace(scope, search):
    products = [
        dict(id=1, name="Body Splash", short_name="Splash", active=True, aliases=[],
             folders=[dict(id="folder-1", name="BODY SPLASH", relative_path="Feminino/BODY SPLASH")],
             folder_count=1, current_media_count=20, default_caption="Legenda", has_default_caption=True,
             has_historical_references=True, reference_count=21, can_archive=True,
             can_restore=False, can_hard_delete=False, possible_duplicate_name=False,
             catalog_presentation_state="current"),
        dict(id=2, name="Produto legado", short_name="", active=True, aliases=[], folders=[],
             folder_count=0, current_media_count=0, default_caption="", has_default_caption=False,
             has_historical_references=True, reference_count=3, can_archive=True,
             can_restore=False, can_hard_delete=False, possible_duplicate_name=False,
             catalog_presentation_state="no_current"),
        dict(id=3, name="Body Splash", short_name="", active=False, aliases=[], folders=[],
             folder_count=0, current_media_count=0, default_caption="", has_default_caption=False,
             has_historical_references=True, reference_count=2, can_archive=False,
             can_restore=True, can_hard_delete=False, possible_duplicate_name=True,
             catalog_presentation_state="archived"),
    ]
    products[0]["possible_duplicate_name"] = True
    visible = [p for p in products if (p["possible_duplicate_name"] if scope == "repeated" else
               p["catalog_presentation_state"] == scope) and
               search.casefold() in (p["name"] + " " + p["short_name"] + " " + str(p["id"])).casefold()]
    return dict(products=visible, counts=dict(current=1, no_current=1, archived=1),
                repeated_name_groups=1, scope=scope, search=search)


class ProductFixtureHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "dashboard"), **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/publisher/products/workspace":
            body = workspace(query.get("scope", ["current"])[0], query.get("search", [""])[0])
        elif parsed.path in {"/api/publisher/library", "/api/publisher/library/groups"}:
            body = dict(items=[], groups=[], page=1, pages=1, total=0, total_groups=0)
        elif parsed.path == "/api/publisher/drive-sync/status":
            body = dict(cursor_status="READY")
        elif parsed.path == "/api/publisher/folder-catalog":
            body = dict(groups={}, counts={}, current_product_containers=[])
        elif parsed.path == "/api/publisher":
            body = fixture()
        else:
            return super().do_GET()
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(shutil.which("chrome") or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists(),
                     "Chrome Stable is required")
class ProductBrowserTests(unittest.TestCase):
    def test_scoped_workspace_and_duplicate_review(self):
        from playwright.sync_api import sync_playwright

        server = ThreadingHTTPServer(("127.0.0.1", 0), ProductFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/publisher.html")
                    page.locator('[data-view="products"]').click()
                    page.locator('[data-product-edit="1"]').wait_for(timeout=10000)
                    self.assertEqual(page.locator("#productList .product-record").count(), 1)
                    self.assertIn("Feminino/BODY SPLASH", page.locator("#productList").inner_text())
                    self.assertIn("Legenda configurada", page.locator("#productList").inner_text())
                    page.locator('[data-product-scope="no_current"]').click()
                    page.locator('[data-product-edit="2"]').wait_for()
                    self.assertNotIn("#3", page.locator("#productList").inner_text())
                    page.locator('[data-product-scope="archived"]').click()
                    page.locator('[data-product-edit="3"]').wait_for()
                    page.locator('[data-product-select="3"]').check()
                    self.assertFalse(page.locator("#restoreProducts").is_disabled())
                    self.assertTrue(page.locator("#deleteProducts").is_disabled())
                    self.assertIn("1 produto(s) têm histórico", page.locator("#productScopeNote").inner_text())
                    page.locator('[data-product-scope="repeated"]').click()
                    page.wait_for_function("document.querySelectorAll('#productList .product-record').length === 2")
                    self.assertIn("Nomes repetidos", page.locator("#productList").inner_text())
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
