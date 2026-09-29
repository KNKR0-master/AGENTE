import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from tests.test_publisher_library_groups_browser import GroupFixtureHandler, GROUPS, media
from tests.test_publisher_queue_browser import ROOT, fixture


class BatchPanelFixtureHandler(GroupFixtureHandler):
    preview_requests = []
    create_requests = []
    prepare_requests = []
    preview_items = []
    created_batch = None
    fail_snapshot_after_create = False
    blocker_kind = None
    folder_associated = False

    def do_GET(self):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(self.path)
        if parsed.path == "/api/publisher":
            if type(self).created_batch and type(self).fail_snapshot_after_create:
                type(self).fail_snapshot_after_create = False
                self.send_error(503)
                return
            body = fixture()
            body["worker_config"]["max_drafts_per_batch"] = 30
            body["preferences"] = {
                "default_publisher_account_id": 1,
                "default_publisher_account_name": "CECILIA FIGUEIREDO",
            }
            body["products"].append({"id": 3, "name": "Agapis Serum", "short_name": "Sérum",
                                     "active": True, "aliases": ["Agapis Kit"],
                                     "default_caption": "Legenda"})
            body["presets"] = [{"id": 9, "name": "Preset de revisão", "account_id": 1,
                                "visibility": "PUBLIC", "comments": True}]
            body["products"].append({"id": 4, "name": "Produto arquivado", "short_name": "",
                                     "active": type(self).restored_product_ids.__contains__(4),
                                     "aliases": [], "default_caption": "Legenda antiga"})
            created = type(self).created_batch
            if created:
                batch_id = created["batch_id"]
                body["batches"].append({"id": batch_id, "name": created["name"]})
                queue_items = []
                for index, item in enumerate(created["items"], 1):
                    draft_id = 100 + index
                    product_id = item["product_id"] or 1
                    product = next(product for product in body["products"] if product["id"] == product_id)
                    draft = {"id": draft_id, "batch_id": batch_id, "queue_position": index,
                             "status": "DRAFT", "filename": item["filename"], "product_id": product_id,
                             "product_name": product["name"], "product_short_name": product.get("short_name") or product["name"],
                             "account_id": 1, "account_name": "CECILIA FIGUEIREDO", "caption": "Legenda de teste",
                             "checklist": {}, "media_status": "RESERVED",
                             "queue_capabilities": {"can_edit": True, "can_reorder": True, "can_remove": True,
                                                     "can_start": True, "can_verify": False,
                                                     "remote_effect_possible": False},
                             "queue_display": {"label": "Pendente", "kind": "neutral", "message": ""},
                             "upload_status": "NOT_STARTED", "persistence_status": "NOT_PERSISTED"}
                    body["drafts"].append(draft)
                    queue_items.append({"draft_id": draft_id, "ready": True, "blockers": [], "warnings": []})
                body["queue_preflights"][batch_id] = {
                    "batch_id": batch_id, "total_items": len(queue_items), "ready_items": len(queue_items),
                    "blocked_items": 0, "warning_items": 0, "items": queue_items,
                }
            self.send_json(body)
            return
        if parsed.path == "/api/publisher/library/groups":
            query = parse_qs(parsed.query)
            page = int(query.get("page", ["1"])[0])
            page_size = int(query.get("page_size", ["20"])[0])
            start = (page - 1) * page_size
            groups = [dict(group) for group in GROUPS]
            self.send_json({"groups": groups[start:start + page_size], "total_groups": len(groups),
                            "total_matching_media": sum(group["matching_media_count"] for group in groups),
                            "page": page, "page_size": page_size,
                            "pages": (len(groups) + page_size - 1) // page_size})
            return
        super().do_GET()

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/publisher/library/group-select":
            type(self).selection_requests += 1
            self.send_json({"group_key": body["group_key"],
                            "items": [media("G1C1A1", "product:1"), media("G1C2A1", "product:1")],
                            "drive_file_ids": ["G1C1A1", "G1C2A1"], "media_asset_ids": []})
            return
        if self.path == "/api/publisher/library-batches/preflight":
            type(self).preview_requests.append(body)
            rows = []
            for index, file_id in enumerate(body.get("drive_file_ids", [])):
                blocker = None
                kind = type(self).blocker_kind
                if kind == "CAPTION_MISSING" and index == 0:
                    blocker = {"code": kind, "message": "Legenda não configurada",
                               "safe_fix_action": {"action": "CONFIGURE_CAPTION", "product_id": 1,
                                                   "label": "Configurar legenda"}}
                elif kind == "PRODUCT_ARCHIVED" and index == 0 and 4 not in type(self).restored_product_ids:
                    blocker = {"code": kind, "message": "Produto arquivado",
                               "safe_fix_action": {"action": "RESTORE_PRODUCT", "product_id": 4,
                                                   "label": "Restaurar produto"}}
                elif kind == "PRODUCT_UNASSIGNED" and file_id == "other" and not type(self).folder_associated:
                    blocker = {"code": kind, "message": "Produto não associado",
                               "safe_fix_action": {"action": "ASSOCIATE_PRODUCT", "folder_id": "unassigned",
                                                   "label": "Associar produto"}}
                rows.append({"drive_file_id": file_id, "filename": f"{file_id}.mp4",
                             "folder_id": "unassigned" if file_id == "other" else "a",
                             "product_id": None if file_id == "other" else (4 if blocker and blocker["code"] == "PRODUCT_ARCHIVED" else 1),
                             "product_display_name": "Sem produto" if file_id == "other" else
                                                    ("Produto arquivado" if blocker and blocker["code"] == "PRODUCT_ARCHIVED" else "Body Splash"),
                             "product_archived": bool(blocker and blocker["code"] == "PRODUCT_ARCHIVED"),
                             "readiness": "BLOCKED" if blocker else "READY",
                             "blockers": [blocker] if blocker else [], "warnings": []})
            type(self).preview_items = rows
            ready = sum(row["readiness"] == "READY" for row in rows)
            return self.send_json({"total": len(rows), "ready": ready, "blocked": len(rows) - ready,
                                   "warning_count": 0, "can_create": bool(rows and body.get("name") and ready == len(rows)),
                                   "account_id": 1, "account_name": "CECILIA FIGUEIREDO",
                                   "preset_id": int(body["preset_id"]) if body.get("preset_id") else None,
                                   "configuration_blockers": [], "products": [
                                       {"product_id": 1, "display_name": "Body Splash", "count": len(rows)}
                                   ] if rows else [], "product_count": 1 if rows else 0,
                                   "items": rows, "problems": []})
        if self.path == "/api/publisher/library-batches":
            type(self).create_requests.append(body)
            preview_by_id = {row["drive_file_id"]: row for row in type(self).preview_items}
            items = [preview_by_id[file_id] for file_id in body.get("drive_file_ids", [])
                     if file_id in preview_by_id]
            type(self).created_batch = {"batch_id": 7, "name": body["name"], "items": items}
            return self.send_json({"batch_id": 7, "draft_ids": [101, 102], "items": []})
        if self.path == "/api/publisher/prepare":
            type(self).prepare_requests.append(body)
            return self.send_json({"started": False})
        if self.path == "/api/publisher/products/bulk":
            type(self).product_restore_requests.append(body)
            if body.get("action") == "restore" and body.get("human_confirmed") is True:
                type(self).restored_product_ids.update(body.get("ids", []))
                return self.send_json({"action": "restore", "selected": len(body.get("ids", []))})
        if self.path == "/api/publisher/media-product":
            type(self).folder_associated = True
            type(self).folder_assign_requests.append(body)
            return self.send_json({"scope": "folder", "folder_id": body.get("folder_id"),
                                   "product_id": body.get("product_id")})
        self.send_error(405)


@unittest.skipUnless(shutil.which("chrome") or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists(),
                     "Chrome Stable is required for visual Library checks")
class LibraryBatchPanelBrowserTests(unittest.TestCase):
    def setUp(self):
        BatchPanelFixtureHandler.lazy_requests = 0
        BatchPanelFixtureHandler.selection_requests = 0
        BatchPanelFixtureHandler.groups_requests = 0
        BatchPanelFixtureHandler.all_groups = True
        BatchPanelFixtureHandler.rename_product_group = False
        BatchPanelFixtureHandler.restored_product_ids = set()
        BatchPanelFixtureHandler.product_restore_requests = []
        BatchPanelFixtureHandler.group_decision_requests = []
        BatchPanelFixtureHandler.preview_requests = []
        BatchPanelFixtureHandler.create_requests = []
        BatchPanelFixtureHandler.prepare_requests = []
        BatchPanelFixtureHandler.preview_items = []
        BatchPanelFixtureHandler.created_batch = None
        BatchPanelFixtureHandler.fail_snapshot_after_create = False
        BatchPanelFixtureHandler.blocker_kind = None
        BatchPanelFixtureHandler.folder_associated = False
        BatchPanelFixtureHandler.folder_assign_requests = []

    def _open(self):
        from playwright.sync_api import sync_playwright

        server = ThreadingHTTPServer(("127.0.0.1", 0), BatchPanelFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.addCleanup(thread.join, 2)
        profile = tempfile.TemporaryDirectory()
        self.addCleanup(profile.cleanup)
        playwright = sync_playwright().start()
        self.addCleanup(playwright.stop)
        context = playwright.chromium.launch_persistent_context(profile.name, channel="chrome", headless=True)
        self.addCleanup(context.close)
        page = context.pages[0] if context.pages else context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{server.server_port}/publisher.html")
        page.locator('nav [data-view="library"]').click()
        page.locator('[data-library-group-select="product:1"]').wait_for(timeout=15000)
        page.locator('[data-library-group-select="product:1"]').click()
        page.wait_for_function("document.querySelector('#librarySelectionCount')?.textContent.includes('2 selecionados')")
        return page, errors

    def test_inline_panel_survives_refresh_cancel_and_success_stays_in_library(self):
        page, errors = self._open()
        self.assertEqual(page.locator('nav [data-view="batch"]').count(), 0)
        self.assertEqual(page.locator("#batchForm").count(), 1)
        page.locator("#createBatchFromLibrary").click()
        dialog = page.locator("#libraryBatchDialog")
        self.assertTrue(dialog.evaluate("el => el.open"))
        self.assertEqual(page.locator("#library").get_attribute("class").find("active") >= 0, True)
        self.assertIn("2 vídeo(s) selecionado(s) · 1 produto(s)", page.locator("#libraryBatchSelectionText").inner_text())
        page.locator('#libraryBatchForm [name="name"]').fill("Teste 2C2")
        page.locator('#libraryBatchForm [name="account_id"]').select_option("1")
        page.locator('#libraryBatchForm [name="preset_id"]').select_option("9")
        page.wait_for_function("document.querySelector('#libraryBatchSubmit')?.disabled === false")
        page.evaluate("window.batchDialogIdentity = document.querySelector('#libraryBatchDialog')")
        page.wait_for_timeout(6500)
        self.assertTrue(dialog.evaluate("el => el.open"))
        self.assertTrue(page.evaluate("window.batchDialogIdentity === document.querySelector('#libraryBatchDialog')"))
        self.assertEqual(page.locator('#libraryBatchForm [name="name"]').input_value(), "Teste 2C2")
        self.assertEqual(page.locator('#libraryBatchForm [name="account_id"]').input_value(), "1")
        self.assertEqual(page.locator('#libraryBatchForm [name="preset_id"]').input_value(), "9")
        self.assertEqual(page.locator("#librarySelectionCount").inner_text().split(" · ")[0], "2 selecionados")
        self.assertGreaterEqual(len(BatchPanelFixtureHandler.preview_requests), 2)
        expected_ids = {"G1C1A1", "G1C2A1"}
        self.assertEqual(set(BatchPanelFixtureHandler.preview_requests[-1]["drive_file_ids"]), expected_ids)
        self.assertEqual(BatchPanelFixtureHandler.create_requests, [])

        page.locator("#libraryBatchCancel").click()
        self.assertFalse(dialog.evaluate("el => el.open"))
        self.assertEqual(page.locator("#librarySelectionCount").inner_text().split(" · ")[0], "2 selecionados")
        self.assertEqual(BatchPanelFixtureHandler.create_requests, [])

        page.locator("#createBatchFromLibrary").click()
        page.wait_for_function("document.querySelector('#libraryBatchSubmit')?.disabled === false")
        page.locator("#libraryBatchSubmit").click()
        page.wait_for_function("document.querySelector('#libraryBatchSuccess')?.hidden === false")
        self.assertEqual(len(BatchPanelFixtureHandler.create_requests), 1)
        self.assertEqual(set(BatchPanelFixtureHandler.create_requests[0]["drive_file_ids"]), expected_ids)
        self.assertTrue(page.locator("#library").get_attribute("class").find("active") >= 0)
        self.assertEqual(page.locator("#libraryBatchSuccessCount").inner_text(),
                         "2 Draft(s) adicionados à Fila. Nenhum worker foi iniciado.")
        self.assertEqual(page.locator('nav [data-view="queue"].selected').count(), 0)
        page.locator("#libraryBatchGoQueue").click()
        batch = page.locator('#queueBatches .queue-batch[aria-label="Teste 2C2"]')
        self.assertEqual(batch.count(), 1)
        self.assertEqual(batch.locator(".queue-item").count(), 2)
        self.assertIn("G1C1A1.mp4", batch.inner_text())
        self.assertIn("G1C2A1.mp4", batch.inner_text())
        self.assertIn("Body Splash", batch.inner_text())
        self.assertIn("Legenda de teste", batch.inner_text())
        self.assertEqual(BatchPanelFixtureHandler.prepare_requests, [])
        self.assertFalse(errors, errors)

    def test_global_selection_limit_survives_refresh_and_rejects_31st(self):
        page, errors = self._open()
        page.locator('[data-library-group-toggle="product:1"]').click()
        page.locator('[data-library="G1C1A1"]').wait_for()
        page.evaluate("""() => {
          for(let index=2;index<30;index++)uiState.setLibrarySelection(`saved-${index}`,true,
            {drive_file_id:`saved-${index}`,filename:`Saved ${index}`,product_name:'Body Splash',status:'AVAILABLE'});
          const group=libraryGroupPages.get('product:1');
          group.items.push({...group.items[0],drive_file_id:'extra-31',filename:'Extra 31.mp4'});
          renderLibrary();renderBatchSummary();
        }""")
        self.assertIn('30 / 30', page.locator('#librarySelectionCount').inner_text())
        page.locator('[data-library="extra-31"]').click()
        self.assertEqual(page.evaluate('librarySelected.size'), 30)
        self.assertFalse(page.locator('[data-library="extra-31"]').is_checked())
        self.assertIn('Limite de 30 rascunhos', page.locator('#notice').inner_text())
        page.locator('#selectAllLibrary').click()
        self.assertEqual(page.evaluate('librarySelected.size'), 30)
        page.wait_for_timeout(11000)
        self.assertEqual(page.evaluate('librarySelected.size'), 30)
        self.assertIn('30 / 30', page.locator('#librarySelectionCount').inner_text())
        page.locator('[data-library="G1C1A1"]').uncheck()
        self.assertEqual(page.evaluate('librarySelected.size'), 29)
        page.evaluate("""() => {const group=libraryGroupPages.get('product:1');
          group.items.push({...group.items[0],drive_file_id:'extra-31',filename:'Extra 31.mp4'});
          renderLibrary();}""")
        page.locator('[data-library="extra-31"]').check()
        self.assertEqual(page.evaluate('librarySelected.size'), 30)
        self.assertFalse(errors, errors)

    def test_archived_product_fix_restores_by_id_and_revalidates_without_losing_selection(self):
        from playwright.sync_api import expect

        BatchPanelFixtureHandler.blocker_kind = "PRODUCT_ARCHIVED"
        page, errors = self._open()
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#createBatchFromLibrary").click()
        page.locator('#libraryBatchForm [name="name"]').fill("Teste restauração")
        restore = page.locator("#libraryBatchDialog").get_by_role("button", name="Restaurar produto", exact=True)
        expect(restore).to_be_visible()
        self.assertTrue(page.locator("#libraryBatchSubmit").is_disabled())

        restore.click()

        page.wait_for_function("document.querySelector('#libraryBatchSubmit')?.disabled === false", timeout=10000)
        self.assertTrue(page.locator("#libraryBatchDialog").evaluate("el => el.open"))
        self.assertEqual(BatchPanelFixtureHandler.product_restore_requests[0]["ids"], [4])
        self.assertEqual(page.locator("#librarySelectionCount").inner_text().split(" · ")[0], "2 selecionados")
        self.assertEqual(page.locator('#libraryBatchForm [name="name"]').input_value(), "Teste restauração")
        self.assertFalse(errors, errors)

    def test_confirmed_create_remains_successful_when_snapshot_refresh_fails(self):
        page, errors = self._open()
        page.locator("#createBatchFromLibrary").click()
        page.locator('#libraryBatchForm [name="name"]').fill("Teste refresh após criar")
        page.wait_for_function("document.querySelector('#libraryBatchSubmit')?.disabled === false")
        BatchPanelFixtureHandler.fail_snapshot_after_create = True

        page.locator("#libraryBatchSubmit").click()

        page.wait_for_function("document.querySelector('#libraryBatchSuccess')?.hidden === false")
        self.assertEqual(len(BatchPanelFixtureHandler.create_requests), 1)
        self.assertEqual(page.locator("#libraryBatchSuccessCount").inner_text(),
                         "2 Draft(s) adicionados à Fila. Nenhum worker foi iniciado.")
        self.assertEqual(page.locator("#libraryBatchDialog").evaluate("el => el.open"), False)
        self.assertIn("Lote criado, mas a tela não pôde ser atualizada", page.locator("#notice").inner_text())
        self.assertEqual(page.locator("#librarySelectionCount").inner_text().split(" · ")[0], "0 selecionados")
        self.assertEqual(BatchPanelFixtureHandler.prepare_requests, [])
        self.assertFalse(errors, errors)

    def test_caption_fix_opens_product_by_id_and_keeps_selected_media(self):
        from playwright.sync_api import expect

        BatchPanelFixtureHandler.blocker_kind = "CAPTION_MISSING"
        page, errors = self._open()
        page.locator("#createBatchFromLibrary").click()
        page.locator('#libraryBatchForm [name="name"]').fill("Teste legenda")
        configure = page.locator("#libraryBatchDialog").get_by_role("button", name="Configurar legenda", exact=True)
        expect(configure).to_be_visible()

        configure.click()

        page.wait_for_function("document.querySelector('#products')?.classList.contains('active')")
        self.assertEqual(page.locator("#productForm").get_attribute("data-product-id"), "1")
        self.assertEqual(page.locator("#librarySelectionCount").inner_text().split(" · ")[0], "2 selecionados")
        self.assertFalse(page.locator("#libraryBatchDialog").evaluate("el => el.open"))
        self.assertFalse(errors, errors)

    def test_unassigned_fix_opens_folder_picker_and_returns_to_panel(self):
        from playwright.sync_api import expect

        BatchPanelFixtureHandler.blocker_kind = "PRODUCT_UNASSIGNED"
        page, errors = self._open()
        page.locator("#libraryGroupView").select_option("REQUIRES_ATTENTION")
        page.locator('[data-library-group-toggle="folder:unassigned"]').click()
        page.locator('[data-library="other"]').check()
        page.locator("#createBatchFromLibrary").click()
        page.locator('#libraryBatchForm [name="name"]').fill("Teste pasta")
        associate = page.locator("#libraryBatchDialog").get_by_role("button", name="Associar produto", exact=True)
        expect(associate).to_be_visible()
        associate.click()

        picker_input = page.locator('[data-folder-combobox="unassigned"] input[type="search"]')
        expect(picker_input).to_be_focused()
        picker_input.fill("agapis")
        page.locator('[data-folder-combobox="unassigned"] [role="option"]').first.click()
        page.locator('[data-folder-assign="unassigned"]').click()

        page.wait_for_function("document.querySelector('#libraryBatchDialog')?.open && document.querySelector('#libraryBatchSubmit')?.disabled === false",
                               timeout=10000)
        self.assertEqual(BatchPanelFixtureHandler.folder_assign_requests[0]["scope"], "folder")
        self.assertEqual(BatchPanelFixtureHandler.folder_assign_requests[0]["folder_id"], "unassigned")
        self.assertEqual(BatchPanelFixtureHandler.folder_assign_requests[0]["product_id"], 3)
        self.assertEqual(page.locator("#librarySelectionCount").inner_text().split(" · ")[0], "3 selecionados")
        self.assertEqual(page.locator('#libraryBatchForm [name="name"]').input_value(), "Teste pasta")
        self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
