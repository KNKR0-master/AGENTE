import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.publisher.account_safety import AccountSafetyGuard
from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService, init_publisher
from src.store import connect, init_db, now_iso


class LibraryBatchPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db_path = str(Path(self.temp.name) / "publisher.db")
        self.conn = connect(self.db_path)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, self.temp.name)
        self.account_id = self.service.save_account({"name": "Cecilia"})
        self.product_a = self.service.save_product({"name": "Agapis Kit", "short_name": "Agapis", "default_caption": "Legenda A"})
        self.product_b = self.service.save_product({"name": "Escova", "default_caption": "Legenda B"})
        self.product_empty_caption = self.service.save_product({"name": "Produto sem legenda", "default_caption": ""})
        self._folder("folder-a", "Agapis", self.product_a)
        self._folder("folder-b", "Escova", self.product_b)
        self._folder("folder-empty-caption", "Sem legenda", self.product_empty_caption)
        self._folder("folder-unassigned", "Sem produto", None)

    def _folder(self, folder_id, name, product_id):
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                """INSERT INTO drive_folders
                   (drive_folder_id,name,relative_path,product_id,mapping_status,mapping_source,created_at,updated_at)
                   VALUES (?,?,?,?,'MAPPED_TO_PRODUCT','MANUAL',?,?)""",
                (folder_id, name, f"/Inbox/{name}", product_id, stamp, stamp),
            )

    def _file(self, file_id, folder_id, *, name=None, size=1000):
        stamp = now_iso()
        filename = name or f"{file_id}_G1C1A1.mp4"
        with self.conn:
            self.conn.execute(
                """INSERT INTO drive_files
                   (drive_file_id,drive_folder_id,name,mime_type,size,file_kind,classification,
                    last_seen_at,created_at,updated_at)
                   VALUES (?,?,?,'video/mp4',?,'VIDEO','NEEDS_REVIEW',?,?,?)""",
                (file_id, folder_id, filename, size, stamp, stamp, stamp),
            )
        return file_id

    def _counts(self):
        return {
            table: self.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "publisher_batches", "publisher_drafts", "publisher_media_assets",
                "publisher_execution_logs", "publisher_batch_executions",
                "account_protection_events", "account_mutation_ledger",
            )
        }

    def _payload(self, ids, **overrides):
        data = {"drive_file_ids": ids, "name": "Teste 2C.2", "account_id": self.account_id, "preset_id": None}
        data.update(overrides)
        return data

    def test_preview_is_read_only_and_summarizes_explicit_multi_product_selection(self):
        self._file("media-a1", "folder-a")
        self._file("media-b1", "folder-b")
        self._file("media-a2", "folder-a")
        before = self._counts()

        first = self.service.preflight_library_batch(self._payload(["media-a1", "media-b1", "media-a2"]))
        second = self.service.preflight_library_batch(self._payload(["media-a1", "media-b1", "media-a2"]))

        self.assertTrue(first["can_create"])
        self.assertEqual((first["total"], first["ready"], first["blocked"]), (3, 3, 0))
        self.assertEqual(first["product_count"], 2)
        self.assertEqual({row["product_id"]: row["count"] for row in first["products"]},
                         {self.product_a: 2, self.product_b: 1})
        self.assertEqual([item["drive_file_id"] for item in second["items"]], ["media-a1", "media-b1", "media-a2"])
        self.assertEqual(self._counts(), before)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets WHERE status='RESERVED'").fetchone()[0], 0)

    def test_preview_exposes_safe_fixes_for_unassigned_archived_and_missing_caption(self):
        self._file("unassigned", "folder-unassigned")
        self._file("archived", "folder-a")
        self._file("no-caption", "folder-empty-caption")
        self.service.assign_media_product({"drive_file_ids": ["no-caption"], "product_id": self.product_empty_caption})
        self.service.assign_media_product({"drive_file_ids": ["archived"], "product_id": self.product_b})
        with self.conn:
            self.conn.execute("UPDATE publisher_products SET active=0 WHERE product_id=?", (self.product_b,))
        before = self._counts()

        preview = self.service.preflight_library_batch(self._payload(["unassigned", "archived", "no-caption"]))
        rows = {item["drive_file_id"]: item for item in preview["items"]}
        blockers = {key: {blocker["code"]: blocker for blocker in item["blockers"]} for key, item in rows.items()}

        self.assertFalse(preview["can_create"])
        self.assertEqual(blockers["unassigned"]["PRODUCT_UNASSIGNED"]["safe_fix_action"], {
            "action": "ASSOCIATE_PRODUCT", "folder_id": "folder-unassigned", "label": "Associar produto",
        })
        self.assertEqual(blockers["archived"]["PRODUCT_ARCHIVED"]["safe_fix_action"]["product_id"], self.product_b)
        self.assertEqual(blockers["no-caption"]["CAPTION_MISSING"]["safe_fix_action"]["product_id"], self.product_empty_caption)
        self.assertEqual(self._counts(), before)

    def test_configuration_blockers_and_account_protection_preview_are_read_only(self):
        self._file("ready", "folder-a")
        before = self._counts()
        unnamed = self.service.preflight_library_batch(self._payload(["ready"], name=""))
        self.assertIn("BATCH_NAME_REQUIRED", {row["code"] for row in unnamed["configuration_blockers"]})
        invalid = self.service.preflight_library_batch(self._payload(["ready"], account_id=99999))
        self.assertIn("ACCOUNT_NOT_FOUND", {row["code"] for row in invalid["configuration_blockers"]})
        self.assertEqual(self._counts(), before)

        inactive_id = self.service.save_account({"name": "Conta inativa"})
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET active=0 WHERE id=?", (inactive_id,))
        inactive = self.service.preflight_library_batch(self._payload(["ready"], account_id=inactive_id))
        self.assertIn("ACCOUNT_INACTIVE", {row["code"] for row in inactive["configuration_blockers"]})
        other_account = self.service.save_account({"name": "Outra conta"})
        preset_id = self.service.save_preset({"name": "Preset de outra conta", "account_id": other_account})
        mismatch = self.service.preflight_library_batch(self._payload(["ready"], preset_id=preset_id))
        self.assertIn("PRESET_ACCOUNT_MISMATCH", {row["code"] for row in mismatch["configuration_blockers"]})

        AccountSafetyGuard(self.conn).activate(self.account_id, "SPAM_RISK", "FIXTURE", "OPERATOR")
        protected_before = self._counts()
        protected = self.service.preflight_library_batch(self._payload(["ready"]))
        self.assertIn("ACCOUNT_PROTECTED", {row["code"] for row in protected["configuration_blockers"]})
        self.assertEqual(self._counts(), protected_before)

    def test_preflight_http_endpoint_uses_explicit_ids_without_mutation(self):
        self._file("ready", "folder-a")
        before = self._counts()
        payload = self._payload(["ready"])
        response = {}
        handler = SimpleNamespace(
            headers={"Content-Type": "application/json", "Content-Length": str(len(json.dumps(payload).encode()))},
            rfile=io.BytesIO(json.dumps(payload).encode()),
            _json=lambda body, **_: response.update(body),
        )

        PublisherAPI(self.temp.name, self.db_path).post(handler, "/api/publisher/library-batches/preflight")

        self.assertTrue(response["can_create"])
        self.assertEqual([item["drive_file_id"] for item in response["items"]], ["ready"])
        self.assertEqual(self._counts(), before)

    def test_submit_revalidates_under_transaction_and_rejects_archive_race_atomically(self):
        self._file("ready", "folder-a")
        before = self._counts()
        payload = self._payload(["ready"])
        self.assertTrue(self.service.preflight_library_batch(payload)["can_create"])
        with self.conn:
            self.conn.execute("UPDATE publisher_products SET active=0 WHERE product_id=?", (self.product_a,))
        after_external_change = self._counts()

        with self.assertRaisesRegex(ValueError, "BATCH_PREFLIGHT_FAILED"):
            self.service.create_library_batch(payload)

        self.assertEqual(self._counts(), after_external_change)
        self.assertEqual(self._counts()["publisher_batches"], before["publisher_batches"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets").fetchone()[0], 0)

    def test_success_creates_one_multi_product_batch_and_caption_snapshots_without_execution(self):
        self._file("draft-a", "folder-a")
        self._file("draft-b", "folder-b")
        payload = self._payload(["draft-a", "draft-b"])
        self.assertTrue(self.service.preflight_library_batch(payload)["can_create"])

        result = self.service.create_library_batch(payload)

        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_batches").fetchone()[0], 1)
        drafts = self.conn.execute(
            "SELECT product_id,caption,status,queue_position FROM publisher_drafts ORDER BY queue_position"
        ).fetchall()
        self.assertEqual([tuple(row) for row in drafts], [
            (self.product_a, "Legenda A", "DRAFT", 1),
            (self.product_b, "Legenda B", "DRAFT", 2),
        ])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets WHERE status='RESERVED'").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_batch_executions").fetchone()[0], 0)
        self.assertEqual(len(result["draft_ids"]), 2)
        with self.conn:
            self.conn.execute("UPDATE publisher_products SET default_caption='Legenda alterada' WHERE product_id=?", (self.product_a,))
        self.assertEqual(self.conn.execute("SELECT caption FROM publisher_drafts WHERE product_id=?", (self.product_a,)).fetchone()[0], "Legenda A")

    def test_limit_30_and_31_rejected_without_truncation(self):
        ids = [self._file(f"limit-{index}", "folder-a", name=f"ITEM_G1C1A1_{index:03}.mp4", size=1000 + index)
               for index in range(100)]
        for count in (1, 29, 30):
            with self.subTest(count=count):
                valid = self.service.preflight_library_batch(self._payload(ids[:count]))
                self.assertTrue(valid["can_create"])
                self.assertEqual(valid["total"], count)
        for count in (31, 100):
            with self.subTest(count=count):
                oversized = self.service.preflight_library_batch(self._payload(ids[:count]))
                self.assertFalse(oversized["can_create"])
                self.assertEqual((oversized["total"], oversized["blocked"]), (count, count))
                self.assertEqual(oversized["configuration_blockers"][0], {
                    "code": "BATCH_LIMIT_EXCEEDED",
                    "message": f"{count} selecionados; máximo 30 rascunhos por lote",
                    "actual": count, "max": 30,
                })
                self.assertEqual(oversized["items"], [])
                with self.assertRaisesRegex(ValueError, "BATCH_LIMIT_EXCEEDED"):
                    self.service.create_library_batch(self._payload(ids[:count]))
        self.assertEqual(self._counts()["publisher_batches"], 0)
        self.assertEqual(self._counts()["publisher_drafts"], 0)
        self.assertEqual(self._counts()["publisher_media_assets"], 0)

    def test_preview_30_then_submit_31_is_atomic(self):
        ids = [self._file(f"race-{index}", "folder-a") for index in range(31)]
        self.assertTrue(self.service.preflight_library_batch(self._payload(ids[:30]))["can_create"])
        before = self._counts()
        with self.assertRaisesRegex(ValueError, "BATCH_LIMIT_EXCEEDED"):
            self.service.create_library_batch(self._payload(ids))
        self.assertEqual(self._counts(), before)
        created = self.service.create_library_batch(self._payload(ids[:30]))
        self.assertEqual(len(created["draft_ids"]), 30)
        self.assertEqual(self._counts()["publisher_batches"], 1)
        self.assertEqual(self._counts()["publisher_drafts"], 30)

    def test_multi_product_total_limit(self):
        product_c = self.service.save_product({"name": "Terceiro", "default_caption": "Legenda C"})
        self._folder("folder-c", "Terceiro", product_c)
        ids = [self._file(f"a-{index}", "folder-a") for index in range(10)]
        ids += [self._file(f"b-{index}", "folder-b") for index in range(12)]
        ids += [self._file(f"c-{index}", "folder-c") for index in range(9)]
        self.assertTrue(self.service.preflight_library_batch(self._payload(ids[:30]))["can_create"])
        self.assertEqual(self.service.preflight_library_batch(self._payload(ids[:30]))["product_count"], 3)
        self.assertFalse(self.service.preflight_library_batch(self._payload(ids))["can_create"])
        with self.assertRaisesRegex(ValueError, "BATCH_LIMIT_EXCEEDED"):
            self.service.create_library_batch(self._payload(ids))
        created = self.service.create_library_batch(self._payload(ids[:30]))
        self.assertEqual(len(created["draft_ids"]), 30)
        counts = dict(self.conn.execute(
            'SELECT product_id,COUNT(*) FROM publisher_drafts GROUP BY product_id'
        ).fetchall())
        self.assertEqual(counts, {self.product_a: 10, self.product_b: 12, product_c: 8})


if __name__ == "__main__":
    unittest.main()
