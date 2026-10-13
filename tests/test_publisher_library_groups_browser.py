import json
import shutil
import tempfile
import threading
import time
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
    dict(group_key="product:4", group_type="PRODUCT", product_id=4, display_name="Produto arquivado",
         product_name="Produto arquivado", product_archived=True, folder_ids=["archived-folder"], folder_count=1,
         folder_paths=["Feminino/Arquivado"], representative_folder_path="Feminino/Arquivado", total_media_count=1,
         matching_media_count=1, available_count=1, matching_available_count=1, attention_count=0, reserved_count=0,
         has_default_caption=False, requires_attention=True, presentation_state="REQUIRES_ATTENTION",
         display_status="Produto arquivado", review_reason="Produto arquivado", ignoreable_count=1),
    dict(group_key="product:5", group_type="PRODUCT", product_id=5, display_name="Produto sem legenda",
         product_name="Produto sem legenda", product_archived=False, folder_ids=["caption-folder"], folder_count=1,
         folder_paths=["Feminino/Sem legenda"], representative_folder_path="Feminino/Sem legenda", total_media_count=1,
         matching_media_count=1, available_count=0, matching_available_count=0, attention_count=1, reserved_count=0,
         has_default_caption=False, requires_attention=True, presentation_state="REQUIRES_ATTENTION",
         display_status="Legenda não configurada", review_reason="Legenda não configurada", ignoreable_count=1),
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
    groups_requests = 0
    all_groups = False
    rename_product_group = False
    restored_product_ids = set()
    product_restore_requests = []
    group_decision_requests = []
    filter_group_view = False
    update_unassigned_group = False
    delay_active_request_number = None
    delayed_active_started = None
    release_delayed_active = None
    delayed_active_completed = None

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
            type(self).groups_requests += 1
            group_view = query.get("group_view", ["ACTIVE"])[0]
            if (type(self).delay_active_request_number == type(self).groups_requests
                    and group_view == "ACTIVE"):
                type(self).delayed_active_started.set()
                type(self).release_delayed_active.wait(timeout=20)
            search = query.get("search", [""])[0].casefold()
            groups = [group for group in GROUPS if not search or search in group["display_name"].casefold()]
            if type(self).filter_group_view:
                if group_view == "ACTIVE":
                    groups = [group for group in groups if group["group_key"] == "product:1"]
                elif group_view == "REQUIRES_ATTENTION":
                    groups = [group for group in groups if group["group_key"] == "folder:unassigned"]
            groups = [dict(group, product_archived=False, available_count=0, matching_available_count=0,
                           presentation_state="REQUIRES_ATTENTION", requires_attention=True,
                           display_status="Legenda não configurada", review_reason="Legenda não configurada")
                      if group["product_id"] in type(self).restored_product_ids and not group["has_default_caption"]
                      else dict(group) for group in groups]
            if type(self).rename_product_group:
                groups = [dict(group, display_name="Body Splash atualizado") if group["group_key"] == "product:1" else group for group in groups]
            if type(self).update_unassigned_group:
                groups = [dict(group, total_media_count=3, matching_media_count=3,
                               display_status="Contagem atualizada")
                          if group["group_key"] == "folder:unassigned" else group for group in groups]
            page = int(query.get("page", ["1"])[0])
            if type(self).all_groups:
                page_size = int(query.get("page_size", ["20"])[0])
                start = (page - 1) * page_size
                body = dict(groups=groups[start:start + page_size], total_groups=len(groups),
                            total_matching_media=sum(group["matching_media_count"] for group in groups),
                            page=page, page_size=page_size,
                            pages=(len(groups) + page_size - 1) // page_size)
            else:
                body = dict(groups=groups[page - 1:page], total_groups=len(groups),
                            total_matching_media=sum(group["matching_media_count"] for group in groups),
                            page=page, page_size=1, pages=len(groups))
            result = self.send_json(body)
            if type(self).delayed_active_started and type(self).delayed_active_started.is_set() and group_view == "ACTIVE":
                type(self).delayed_active_completed.set()
            return result
        if parsed.path == "/api/publisher/library/group-items":
            type(self).lazy_requests += 1
            key = query.get("group_key", [""])[0]
            items = [media("G1C1A1", key), media("G1C2A1", key)] if key == "product:1" else [media("other", key)]
            return self.send_json(dict(group_key=key, items=items, total=len(items), page=1, page_size=50, pages=1))
        if parsed.path == "/api/publisher/products/workspace":
            product = dict(id=1, name="Body Splash", short_name="Body Splash", active=True,
                           default_caption="Legenda", has_default_caption=True, folder_count=2,
                           current_media_count=2, folders=[])
            products = [product]
            if query.get("scope", ["current"])[0] == "current" and 4 in type(self).restored_product_ids:
                products.append(dict(id=4, name="Produto arquivado", short_name="", active=True, aliases=[],
                                     default_caption="", has_default_caption=False, folder_count=1,
                                     current_media_count=1, folders=[], has_historical_references=False,
                                     reference_count=0, can_archive=True, can_restore=False,
                                     can_hard_delete=True, possible_duplicate_name=False,
                                     catalog_presentation_state="current"))
            return self.send_json(dict(products=products, counts=dict(current=len(products), no_current=0, archived=0),
                                       repeated_name_groups=0))
        if parsed.path == "/api/publisher/drive-sync/status":
            return self.send_json(dict(cursor_status="READY"))
        if parsed.path == "/api/publisher/folder-catalog":
            return self.send_json(dict(groups={}, counts={}, current_product_containers=[]))
        if parsed.path == "/api/publisher":
            body = fixture()
            body["products"].append(dict(id=3, name="Agapis Serum", short_name="Sérum", active=True,
                                          aliases=["Agapis Kit"], default_caption="Legenda", has_default_caption=True))
            return self.send_json(body)
        return super().do_GET()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/publisher/products/bulk":
            type(self).product_restore_requests.append(body)
            if body.get("action") == "restore" and body.get("human_confirmed") is True:
                type(self).restored_product_ids.update(body.get("ids", []))
                return self.send_json(dict(action="restore", selected=len(body.get("ids", []))))
            self.send_error(400)
            return
        if self.path == "/api/publisher/library/group-decision":
            type(self).group_decision_requests.append(body)
            if body.get("human_confirmed") is True:
                return self.send_json(dict(group_key=body["group_key"], ignored_count=body.get("expected_count", 0)))
            return self.send_json(dict(group_key=body["group_key"], eligible_count=1, requires_confirmation=True))
        if self.path != "/api/publisher/library/group-select":
            self.send_error(405)
            return
        type(self).selection_requests += 1
        self.send_json(dict(group_key=body["group_key"], items=[media("G1C1A1", "product:1"),
                                                               media("G1C2A1", "product:1")],
                            drive_file_ids=["G1C1A1", "G1C2A1"], media_asset_ids=[]))


@unittest.skipUnless(shutil.which("chrome") or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists(),
                     "Chrome Stable is required")
class GroupedLibraryBrowserTests(unittest.TestCase):
    @staticmethod
    def wait_for_server_event(page, event, timeout):
        # The isolation route uses Playwright's sync dispatcher. Pump it while
        # waiting for a background browser request to reach the fixture server.
        deadline = time.monotonic() + timeout
        while not event.is_set() and time.monotonic() < deadline:
            page.wait_for_timeout(50)
        return event.is_set()

    def setUp(self):
        GroupFixtureHandler.lazy_requests = 0
        GroupFixtureHandler.selection_requests = 0
        GroupFixtureHandler.groups_requests = 0
        GroupFixtureHandler.all_groups = False
        GroupFixtureHandler.rename_product_group = False
        GroupFixtureHandler.restored_product_ids = set()
        GroupFixtureHandler.product_restore_requests = []
        GroupFixtureHandler.group_decision_requests = []
        GroupFixtureHandler.filter_group_view = False
        GroupFixtureHandler.update_unassigned_group = False
        GroupFixtureHandler.delay_active_request_number = None
        GroupFixtureHandler.delayed_active_started = threading.Event()
        GroupFixtureHandler.release_delayed_active = threading.Event()
        GroupFixtureHandler.delayed_active_completed = threading.Event()

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

    def test_folder_product_picker_survives_periodic_refresh_and_intentional_closes(self):
        from playwright.sync_api import sync_playwright

        GroupFixtureHandler.all_groups = True
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
                    picker = page.locator('[data-product-picker-key="folder:unassigned"] input[type=search]')
                    picker.wait_for(timeout=15000)
                    page.locator('[data-library-group-toggle="folder:unassigned"]').click()
                    picker.fill("agapis")
                    option = page.get_by_role("option", name="Sérum")
                    option.wait_for()
                    page.evaluate("window.__pickerNode = document.querySelector('[data-product-picker-key=\\\"folder:unassigned\\\"] input[type=search]')")
                    initial_requests = GroupFixtureHandler.groups_requests
                    GroupFixtureHandler.rename_product_group = True
                    GroupFixtureHandler.update_unassigned_group = True
                    page.wait_for_function("window.__pickerNode && document.querySelector('[data-product-picker-key=\\\"folder:unassigned\\\"] input[type=search]') === window.__pickerNode && window.__pickerNode.value === 'agapis' && document.activeElement === window.__pickerNode && !document.querySelector('[data-product-picker-key=\\\"folder:unassigned\\\"] .combobox-results').hidden", timeout=10000)
                    page.wait_for_timeout(12000)
                    self.assertGreaterEqual(GroupFixtureHandler.groups_requests, initial_requests + 2)
                    self.assertIn("Body Splash atualizado", page.locator('[data-library-group-row="product:1"]').inner_text())
                    updated_group = page.locator('[data-library-group-row="folder:unassigned"]')
                    self.assertIn("3 mídia(s)", updated_group.inner_text())
                    self.assertIn("Contagem atualizada", updated_group.inner_text())
                    self.assertTrue(page.evaluate("window.__pickerNode === document.querySelector('[data-product-picker-key=\\\"folder:unassigned\\\"] input[type=search]')"))
                    self.assertEqual(picker.input_value(), "agapis")
                    self.assertTrue(picker.evaluate("el => document.activeElement === el"))
                    self.assertTrue(option.is_visible())

                    picker.press("Escape")
                    self.assertEqual(picker.get_attribute("aria-expanded"), "false")
                    picker.fill("agapis")
                    page.locator("#librarySearch").click()
                    self.assertEqual(picker.get_attribute("aria-expanded"), "false")
                    picker.focus()
                    picker.fill("agapis")
                    page.locator('[data-library-group-toggle="folder:unassigned"]').click()
                    self.assertEqual(picker.get_attribute("aria-expanded"), "false")
                    page.locator('[data-library-group-toggle="folder:unassigned"]').click()
                    picker = page.locator('[data-product-picker-key="folder:unassigned"] input[type=search]')
                    picker.focus()
                    picker.fill("agapis")
                    page.get_by_role("option", name="Sérum").click()
                    self.assertEqual(page.locator('[data-folder-product="unassigned"]').input_value(), "3")
                    self.assertEqual(picker.get_attribute("aria-expanded"), "false")
                    self.assertTrue(page.locator('[data-folder-assign="unassigned"]').is_visible())
                    override = page.locator('#libraryAssignProductSearch')
                    override.fill("agapis")
                    page.evaluate("window.__overridePickerNode = document.querySelector('#libraryAssignProductSearch')")
                    page.evaluate("refresh()")
                    self.assertTrue(page.evaluate("window.__overridePickerNode === document.querySelector('#libraryAssignProductSearch')"))
                    self.assertEqual(override.input_value(), "agapis")
                    self.assertTrue(override.evaluate("el => document.activeElement === el"))
                    self.assertTrue(page.get_by_role("option", name="Sérum").is_visible())
                    override.press("Escape")

                    page.locator('[data-library-group-toggle="product:1"]').click()
                    checkbox = page.locator('[data-library="G1C1A1"]')
                    checkbox.wait_for()
                    checkbox.check()
                    page.wait_for_function("document.querySelector('#librarySelectionCount')?.textContent.includes('1 selecionado')")
                    page.wait_for_timeout(12000)
                    self.assertTrue(checkbox.is_checked())
                    self.assertIn("1 selecionado", page.locator("#librarySelectionCount").inner_text())
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_periodic_refresh_cannot_overwrite_newer_library_filter_or_detach_picker(self):
        from playwright.sync_api import sync_playwright

        GroupFixtureHandler.all_groups = True
        GroupFixtureHandler.filter_group_view = True
        GroupFixtureHandler.delay_active_request_number = 2
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
                    page.locator('[data-library-group-row="product:1"]').wait_for(timeout=15000)

                    # Hold the real five-second refresh's old ACTIVE query while the operator changes views.
                    self.assertTrue(self.wait_for_server_event(page, GroupFixtureHandler.delayed_active_started, timeout=12))
                    page.locator("#libraryGroupView").select_option("REQUIRES_ATTENTION")
                    picker = page.locator('[data-product-picker-key="folder:unassigned"] input[type=search]')
                    picker.wait_for(timeout=10000)
                    picker.fill("agapis")
                    option = page.get_by_role("option", name="Sérum")
                    option.wait_for(timeout=10000)
                    page.evaluate("window.__pickerNode = document.querySelector('[data-product-picker-key=\\\"folder:unassigned\\\"] input[type=search]')")

                    GroupFixtureHandler.release_delayed_active.set()
                    self.assertTrue(GroupFixtureHandler.delayed_active_completed.wait(timeout=10))
                    page.wait_for_timeout(500)
                    self.assertTrue(page.evaluate("window.__pickerNode.isConnected"))
                    self.assertTrue(page.evaluate("window.__pickerNode === document.querySelector('[data-product-picker-key=\\\"folder:unassigned\\\"] input[type=search]')"))
                    self.assertEqual(picker.input_value(), "agapis")
                    self.assertTrue(picker.evaluate("el => document.activeElement === el"))
                    self.assertTrue(option.is_visible())
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            GroupFixtureHandler.release_delayed_active.set()
            server.shutdown()
            server.server_close()

    def test_intentional_filter_removal_closes_picker_without_stale_dom(self):
        from playwright.sync_api import sync_playwright

        GroupFixtureHandler.all_groups = True
        GroupFixtureHandler.filter_group_view = True
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
                    page.locator("#libraryGroupView").select_option("REQUIRES_ATTENTION")
                    picker = page.locator('[data-product-picker-key="folder:unassigned"] input[type=search]')
                    picker.wait_for(timeout=10000)
                    picker.fill("agapis")
                    page.evaluate("window.__pickerNode = document.querySelector('[data-product-picker-key=\\\"folder:unassigned\\\"] input[type=search]')")
                    page.locator("#libraryGroupView").select_option("ACTIVE")
                    page.wait_for_function("document.querySelectorAll('[data-library-group-row]').length === 1 && document.querySelector('[data-library-group-row=\\\"product:1\\\"]')")
                    self.assertFalse(page.evaluate("window.__pickerNode.isConnected"))
                    self.assertEqual(page.locator('[data-folder-combobox]').count(), 0)
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_archived_product_restore_and_caption_fix_stay_contextual(self):
        from playwright.sync_api import sync_playwright

        GroupFixtureHandler.all_groups = True
        server = ThreadingHTTPServer(("127.0.0.1", 0), GroupFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/publisher.html")
                    page.locator('nav [data-view="library"]').click()
                    page.locator('[data-library-group-row="product:4"]').wait_for(timeout=15000)
                    ignore_button = page.locator('[data-library-group-row="product:5"] [data-ignore-library-group="product:5"]')
                    self.assertTrue(ignore_button.is_visible())
                    dialog_messages = []
                    page.once("dialog", lambda dialog: (dialog_messages.append(dialog.message), dialog.accept()))
                    ignore_button.click()
                    page.wait_for_function("document.querySelector('#notice')?.textContent.includes('ignorado')")
                    self.assertIn("Ignorar 1 vídeo deste grupo?", dialog_messages[0])
                    self.assertEqual(GroupFixtureHandler.group_decision_requests[0]["human_confirmed"], False)
                    self.assertEqual(GroupFixtureHandler.group_decision_requests[1]["expected_count"], 1)
                    archived = page.locator('[data-library-group-row="product:4"]')
                    self.assertIn("Produto arquivado", archived.inner_text())
                    restore = archived.locator('[data-library-restore-product="4"]')
                    self.assertTrue(restore.is_visible())
                    page.once("dialog", lambda dialog: dialog.accept())
                    restore.click()
                    page.wait_for_function("!document.querySelector('[data-library-restore-product=\\\"4\\\"]') && document.querySelector('[data-library-config-product=\\\"4\\\"]')?.textContent.includes('Configurar legenda')")
                    restored = page.locator('[data-library-group-row="product:4"]')
                    self.assertIn("Legenda não configurada", restored.inner_text())
                    self.assertEqual(GroupFixtureHandler.product_restore_requests[-1]["ids"], [4])
                    self.assertEqual(GroupFixtureHandler.product_restore_requests[-1]["action"], "restore")
                    page.locator('[data-library-config-product="4"]').click()
                    page.wait_for_function("document.querySelector('#productForm')?.dataset.productId === '4'")
                    self.assertEqual(page.locator('#productForm').get_attribute('data-product-id'), '4')
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
