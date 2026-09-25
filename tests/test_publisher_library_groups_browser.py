import json
import shutil
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from tests.test_publisher_queue_browser import ROOT, fixture


GROUPS = [
    dict(group_key="product:1", group_type="PRODUCT", product_id=1, display_name="Body Splash",
         product_name="Body Splash", product_archived=False, folder_ids=["a", "b"], folder_count=2,
         folder_paths=["Feminino/Body Splash", "Feminino/Body Splash/Vídeos"],
         representative_folder_path="Feminino/Body Splash", total_media_count=2,
         matching_media_count=2, available_count=2, attention_count=0, reserved_count=0,
         has_default_caption=True, requires_attention=False, review_reason=None),
    dict(group_key="folder:unassigned", group_type="UNASSIGNED_FOLDER", product_id=None,
         display_name="Kit sem produto", product_name=None, product_archived=False,
         folder_ids=["unassigned"], folder_count=1, folder_paths=["Feminino/Kit sem produto"],
         representative_folder_path="Feminino/Kit sem produto", total_media_count=1,
         matching_media_count=1, available_count=0, attention_count=1, reserved_count=0,
         has_default_caption=False, requires_attention=True, review_reason="Produto da pasta ainda não associado"),
]


def media(file_id, group):
    resolved = group == "product:1"
    return dict(drive_file_id=file_id, media_asset_id=None, filename=f"{file_id}.mp4",
                folder_id="a" if resolved else "unassigned", folder_name="Body Splash" if resolved else "Kit sem produto",
                folder_path="Feminino/Body Splash" if resolved else "Feminino/Kit sem produto",
                mime_type="video/mp4", size=1024, file_type="VIDEO", product_id=1 if resolved else None,
                product_name="Body Splash" if resolved else None, product_short_name="Body Splash" if resolved else None,
                caption="Legenda" if resolved else "", status="AVAILABLE" if resolved else "ATTENTION",
                status_label="DISPONÍVEL" if resolved else "REQUER ATENÇÃO",
                reason="Elegível para lote" if resolved else "Produto da pasta ainda não associado",
                classification_label="Reconhecido", classification_reason="", product_resolution="DIRECT_FOLDER" if resolved else "UNASSIGNED",
                eligible=resolved, thumbnail_link=None, web_view_link=None)


class GroupFixtureHandler(SimpleHTTPRequestHandler):
    lazy_requests = 0
    selection_requests = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "dashboard"), **kwargs)

    def log_message(self, *_args):
        pass

    def send_json(self, body):
        data = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/api/publisher/library/groups":
            search = query.get("search", [""])[0].casefold()
            groups = [group for group in GROUPS if not search or search in group["display_name"].casefold()]
            page = int(query.get("page", ["1"])[0])
            return self.send_json(dict(groups=groups[page - 1:page], total_groups=len(groups),
                                       total_matching_media=sum(group["matching_media_count"] for group in groups),
                                       page=page, page_size=1, pages=len(groups)))
        if parsed.path == "/api/publisher/library/group-items":
            type(self).lazy_requests += 1
            key = query.get("group_key", [""])[0]
            items = [media("G1C1A1", key), media("G1C2A1", key)] if key == "product:1" else [media("other", key)]
            return self.send_json(dict(group_key=key, items=items, total=len(items), page=1, page_size=50, pages=1))
        if parsed.path == "/api/publisher/products/workspace":
            product = dict(id=1, name="Body Splash", short_name="Body Splash", active=True,
                           default_caption="Legenda", has_default_caption=True, folder_count=2,
                           current_media_count=2, folders=[])
            return self.send_json(dict(products=[product], counts=dict(current=1, no_current=0, archived=0),
                                       repeated_name_groups=0))
        if parsed.path == "/api/publisher/drive-sync/status":
            return self.send_json(dict(cursor_status="READY"))
        if parsed.path == "/api/publisher/folder-catalog":
            return self.send_json(dict(groups={}, counts={}, current_product_containers=[]))
        if parsed.path == "/api/publisher":
            return self.send_json(fixture())
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/publisher/library/group-select":
            self.send_error(405)
            return
        type(self).selection_requests += 1
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.send_json(dict(group_key=body["group_key"], items=[media("G1C1A1", "product:1"),
                                                               media("G1C2A1", "product:1")],
                            drive_file_ids=["G1C1A1", "G1C2A1"], media_asset_ids=[]))


@unittest.skipUnless(shutil.which("chrome") or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists(),
                     "Chrome Stable is required")
class GroupedLibraryBrowserTests(unittest.TestCase):
    def test_collapsed_lazy_selection_pagination_and_product_navigation(self):
        from playwright.sync_api import sync_playwright

        GroupFixtureHandler.lazy_requests = 0
        GroupFixtureHandler.selection_requests = 0
        server = ThreadingHTTPServer(("127.0.0.1", 0), GroupFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    errors = []
                    page.on("pageerror", lambda error: errors.append(str(error)))
                    page.goto(f"http://127.0.0.1:{server.server_port}/publisher.html")
                    page.locator('nav [data-view="library"]').click()
                    page.locator('[data-library-group-toggle="product:1"]').wait_for(timeout=15000)
                    self.assertEqual(page.locator('[data-library]').count(), 0)
                    self.assertEqual(GroupFixtureHandler.lazy_requests, 0)
                    self.assertIn("2 mídia(s)", page.locator("#libraryRows").inner_text())
                    page.locator('[data-library-group-select="product:1"]').click()
                    page.wait_for_function("document.querySelector('#librarySelectionCount')?.textContent.includes('2 selecionados')")
                    self.assertEqual(GroupFixtureHandler.lazy_requests, 0)
                    page.locator('[data-library-group-toggle="product:1"]').click()
                    page.locator('[data-library="G1C1A1"]').wait_for()
                    self.assertEqual(GroupFixtureHandler.lazy_requests, 1)
                    page.locator('[data-library-group-toggle="product:1"]').click()
                    self.assertEqual(page.locator('[data-library]').count(), 0)
                    page.locator("#libraryNext").click()
                    page.locator('[data-library-group-toggle="folder:unassigned"]').wait_for()
                    self.assertIn("2 selecionados", page.locator("#librarySelectionCount").inner_text())
                    self.assertIn("fora da visualização", page.locator("#librarySelectionCount").inner_text())
                    page.locator("#libraryPrevious").click()
                    page.locator('[data-library-config-product="1"]').click()
                    page.wait_for_function("document.querySelector('#productForm')?.dataset.productId === '1'")
                    self.assertEqual(page.locator("#productForm").get_attribute("data-product-id"), "1")
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
