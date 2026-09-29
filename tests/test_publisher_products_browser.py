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


class ProductCreateFixtureHandler(ProductFixtureHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/publisher':
            body = fixture()
            body['drive_folders'] = [
                dict(id=folder_id, name=folder_id, relative_path=f'CECI/FEMININO/{folder_id}')
                for folder_id in ('folder-a', 'folder-b')
            ]
            body['products'] = [*body.get('products', []), *self.server.saved_products]
        elif parsed.path == '/api/publisher/products/workspace':
            query = parse_qs(parsed.query)
            scope, search = query.get('scope', ['current'])[0], query.get('search', [''])[0]
            body = workspace(scope, search)
            if scope == 'current':
                body['products'].extend(product for product in self.server.saved_products if search.casefold() in product['name'].casefold())
        else:
            return super().do_GET()
        data = json.dumps(body).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if urlparse(self.path).path != '/api/publisher/products':
            return self.send_error(404)
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        self.server.product_posts.append(body)
        if body['name'] == 'Erro':
            response, status = {'error': 'Pasta indisponível; escolha outra pasta'}, 400
        else:
            product_id = 9
            folders = [dict(id=folder_id, name=folder_id, relative_path=f'CECI/FEMININO/{folder_id}')
                       for folder_id in body.get('folder_ids', [])]
            self.server.saved_products.append(dict(
                id=product_id, name=body['name'], short_name=body.get('short_name', ''),
                default_caption=body.get('default_caption', ''), flow_prompt=body.get('flow_prompt', ''),
                active=True, aliases=[], folders=folders, folder_count=len(folders), current_media_count=0,
                has_default_caption=bool(body.get('default_caption')), has_historical_references=True,
                reference_count=len(folders), can_archive=True, can_restore=False, can_hard_delete=False,
                possible_duplicate_name=False, catalog_presentation_state='current',
            ))
            response, status = {'id': product_id}, 200
        data = json.dumps(response).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(shutil.which("chrome") or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists(),
                     "Chrome Stable is required")
class ProductBrowserTests(unittest.TestCase):
    def test_create_product_with_pending_folders_cancel_refresh_error_and_double_submit(self):
        from playwright.sync_api import sync_playwright

        server = ThreadingHTTPServer(('127.0.0.1', 0), ProductCreateFixtureHandler)
        server.saved_products = []
        server.product_posts = []
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-view="products"]').click()
                    page.locator('#products .heading summary').click()
                    page.locator('#newProduct').click()
                    page.locator('#productFolderSelect').select_option('folder-a')
                    page.locator('#addProductFolder').click()
                    page.locator('#productFolderSelect').select_option('folder-b')
                    page.locator('#addProductFolder').click()
                    page.locator('#productForm [name=name]').fill('Erro')
                    page.locator('#productForm [name=default_caption]').fill('Legenda de teste')
                    page.wait_for_timeout(11500)
                    self.assertEqual(page.locator('#productForm [name=name]').input_value(), 'Erro')
                    self.assertEqual(page.locator('#productForm [name=default_caption]').input_value(), 'Legenda de teste')
                    self.assertEqual(page.locator('#productFolders .folder-association').count(), 2)
                    self.assertEqual(server.product_posts, [])
                    page.locator('#cancelProductCreate').click()
                    self.assertEqual(page.locator('#productFolders .folder-association').count(), 0)
                    self.assertEqual(server.product_posts, [])

                    page.locator('#newProduct').click()
                    page.locator('#productFolderSelect').select_option('folder-a')
                    page.locator('#addProductFolder').click()
                    page.locator('#productFolderSelect').select_option('folder-b')
                    page.locator('#addProductFolder').click()
                    page.locator('#productForm [name=name]').fill('Erro')
                    page.locator('#productForm [name=default_caption]').fill('Legenda de teste')
                    page.locator('#productForm [type=submit]').click()
                    page.get_by_text('Pasta indisponível; escolha outra pasta').wait_for()
                    self.assertEqual(page.locator('#productFolders .folder-association').count(), 2)
                    self.assertEqual(page.locator('#productForm [name=name]').input_value(), 'Erro')
                    self.assertFalse(page.locator('#productForm [type=submit]').is_disabled())

                    page.locator('#productForm [name=name]').fill('Novo produto')
                    page.evaluate("document.querySelector('#productForm [type=submit]').click(); document.querySelector('#productForm [type=submit]').click()")
                    try:
                        page.get_by_text('Produto e pastas salvos. Nenhum arquivo foi movido.').wait_for(timeout=5000)
                    except Exception:
                        self.fail(f'Posts: {server.product_posts!r}; notices: {page.locator("[role=status]").all_inner_texts()!r}')
                    self.assertEqual(len(server.saved_products), 1)
                    self.assertEqual(len(server.product_posts), 2)
                    self.assertEqual(server.product_posts[-1]['folder_ids'], ['folder-a', 'folder-b'])
                    self.assertEqual(page.locator('#productFolders .folder-association').count(), 2)
                    self.assertEqual(page.locator('#productForm [name=id]').input_value(), '9')
                    self.assertIn('Novo produto', page.locator('#productList').inner_text())
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

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
