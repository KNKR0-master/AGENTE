import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService, init_publisher
from src.store import connect, init_db, now_iso, upsert_product


class PublisherUi2ATests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.conn = connect(self.root / "test.db")
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        self.product = upsert_product(self.conn, "Produto sem historico")
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, self.root)
        self.account = self.service.save_account({"name": "Conta Publisher"})

    def test_health_validates_user_oauth_and_read_only_drive_access(self):
        store_path = self.root / "cecilia-drive.dpapi"
        store_path.write_text("dpapi:v1:fixture", encoding="utf-8")
        config = SimpleNamespace(
            auth_mode="user_oauth",
            expected_google_account="ceciliafigueiredo.shop@gmail.com",
            oauth_credential_store=str(store_path),
        )
        service = SimpleNamespace(
            files=lambda: SimpleNamespace(
                list=lambda **kwargs: SimpleNamespace(
                    execute=lambda **execute_kwargs: {"files": []}
                )
            )
        )
        diagnostic = {
            "file_exists": True,
            "file_readable": True,
            "dpapi_load_ok": True,
            "json_valid": True,
            "exception_type": None,
            "winerror": None,
        }
        api = PublisherAPI(self.root, self.root / "test.db")
        with (
            patch("src.publisher.http.DriveInventoryConfig.from_env", return_value=config),
            patch("src.publisher.http.DriveCredentialStore.diagnostic", return_value=diagnostic),
            patch("src.publisher.http.user_drive_service", return_value=service) as load_service,
        ):
            result = api._drive_health(self.conn)

        self.assertTrue(result["dpapi_load_ok"])
        self.assertTrue(result["drive_auth_ok"])
        self.assertTrue(result["drive_read_ok"])
        self.assertEqual(result["diagnosis"], "HEALTHY")
        self.assertTrue(result["available"])
        load_service.assert_called_once_with(str(store_path), config.expected_google_account)

    def test_migration_is_idempotent_and_preserves_preference_and_ids(self):
        first = self.service.save_product({"id": self.product, "name": "Produto sem historico", "flow_prompt": "Frame 1"})
        init_publisher(self.conn)
        init_publisher(self.conn)
        self.assertEqual(first, self.product)
        self.assertEqual(self.service.get_product(self.product)["flow_prompt"], "Frame 1")
        self.assertEqual(self.service.default_account_id(), self.account)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_preferences").fetchone()[0], 1)

    def test_flow_prompt_persists_multiline_content(self):
        value = "Hook 1\nCena 2\nCTA 3"
        self.service.save_product({"id": self.product, "name": "Produto sem historico", "flow_prompt": value})
        self.assertEqual(self.service.get_product(self.product)["flow_prompt"], value)
        self.assertEqual(self.service.catalog()[0]["flow_prompt"], value)

    def test_delete_plan_archives_referenced_products_and_deletes_unreferenced(self):
        protected = self.service.save_product({"name": "Produto com historico"})
        with self.conn:
            self.conn.execute(
                "INSERT INTO production_tasks(product_id,version_number,created_at,updated_at) VALUES (?,?,?,?)",
                (protected, 1, now_iso(), now_iso()),
            )
        plan = self.service.product_bulk_plan([self.product, protected])
        self.assertEqual(plan["will_delete"], 1)
        self.assertEqual(plan["will_archive"], 1)
        result = self.service.bulk_products([self.product, protected], "delete", human_confirmed=True)
        self.assertEqual(result["deleted"], 1)
        self.assertEqual(result["archived"], 1)
        self.assertIsNone(self.conn.execute("SELECT 1 FROM products WHERE id=?", (self.product,)).fetchone())
        self.assertEqual(self.conn.execute("SELECT active FROM publisher_products WHERE product_id=?", (protected,)).fetchone()[0], 0)

    def test_bulk_action_requires_explicit_confirmation(self):
        with self.assertRaisesRegex(ValueError, "CONFIRM_PRODUCT_ACTION"):
            self.service.bulk_products([self.product], "archive")

    def test_multiple_drive_folders_use_existing_mapping_history(self):
        timestamp = now_iso()
        with self.conn:
            for folder_id, name in (("folder-a", "Produto A"), ("folder-b", "Produto B")):
                self.conn.execute(
                    "INSERT INTO drive_folders(drive_folder_id,name,relative_path,created_at,updated_at) VALUES (?,?,?,?,?)",
                    (folder_id, name, f"/Flow/{name}", timestamp, timestamp),
                )
        self.service.set_product_folder(self.product, "folder-a", "add")
        self.service.set_product_folder(self.product, "folder-b", "add")
        self.assertEqual({row[0] for row in self.conn.execute("SELECT drive_folder_id FROM drive_folders WHERE product_id=?", (self.product,))}, {"folder-a", "folder-b"})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_folder_mapping_history WHERE new_product_id=?", (self.product,)).fetchone()[0], 2)
        self.service.set_product_folder(self.product, "folder-a", "remove")
        self.assertIsNone(self.conn.execute("SELECT product_id FROM drive_folders WHERE drive_folder_id='folder-a'").fetchone()[0])

    def test_default_account_falls_back_when_account_is_inactive(self):
        self.assertEqual(self.service.default_account_id(), self.account)
        self.service.save_account({"id": self.account, "name": "Conta Publisher", "active": False})
        self.assertIsNone(self.service.default_account_id())
        self.assertFalse(self.service.get_preferences()["default_account_active"])

    def test_ffprobe_health_uses_official_resolver(self):
        api = PublisherAPI(self.root, self.root / "test.db")
        executable = self.root / "ffprobe.exe"
        with patch("src.publisher.http.resolve_ffprobe_path", return_value=executable) as resolver, \
             patch("src.publisher.http.ffprobe_version", return_value="ffprobe version fixture") as version:
            health = api._ffprobe_health()
        resolver.assert_called_once_with(api.root)
        version.assert_called_once_with(executable)
        self.assertTrue(health["executable_valid"])
        self.assertEqual(health["version"], "ffprobe version fixture")

    def test_ffprobe_health_exposes_sanitized_runtime_metadata(self):
        api = PublisherAPI(self.root, self.root / "test.db")
        executable = self.root / "ffprobe.exe"
        with patch("src.publisher.http.resolve_ffprobe_path", return_value=executable), \
             patch("src.publisher.http.ffprobe_version", return_value="ffprobe version fixture"):
            health = api._ffprobe_health()
        self.assertIn("path_exists", health)
        self.assertIn("process_started", health)
        self.assertIn("media_test", health)

    def test_bulk_preview_route_accepts_api_publisher_prefix(self):
        api = PublisherAPI(self.root, self.root / "test.db")
        payload = json.dumps({"action": "preview", "ids": [self.product]}).encode()
        response = {}
        handler = SimpleNamespace(
            headers={
                "Host": "127.0.0.1:8765",
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
            rfile=io.BytesIO(payload),
            _json=lambda value, **_: response.update(value),
        )
        api.post(handler, "/api/publisher/products/bulk")
        self.assertEqual(response["selected"], 1)
        self.assertIn("will_delete", response)


if __name__ == "__main__":
    unittest.main()
