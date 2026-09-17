import unittest
import tempfile
from types import SimpleNamespace
from pathlib import Path

from src.publisher.library import list_library
from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService, init_publisher
from src.store import connect, init_db, now_iso


class FakeMediaProvider:
    def __init__(self):
        self.metadata = {"id": "high", "name": "PRODUTO_G1C1A1_0101.mp4", "size": 1000}
        self.deleted = []

    def get_remote_metadata(self, file_id):
        return self.metadata if self.metadata and self.metadata.get("id") == file_id else None

    def delete_remote(self, asset):
        self.deleted.append(asset["remote_file_id"])
        self.metadata = None


class PublisherMediaLibraryTests(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        self.addCleanup(lambda: Path(self.db_file.name).unlink(missing_ok=True))
        self.conn = connect(self.db_file.name)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, ".")
        self.account = self.service.save_account({"name": "Cecilia Figueiredo"})
        self.product_a = self._product("Produto A", "Legenda A")
        self.product_b = self._product("Produto B", "Legenda B")
        self.product_empty = self._product("Produto sem legenda", "")
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                """INSERT INTO drive_folders
                   (drive_folder_id,parent_drive_folder_id,name,relative_path,product_id,
                    mapping_status,mapping_source,created_at,updated_at)
                   VALUES (?,?,?,?,?,'MAPPED_TO_PRODUCT','MANUAL',?,?)""",
                ("folder-a", None, "Produto A", "/Flow/Produto A", self.product_a, stamp, stamp),
            )
            self.conn.execute(
                """INSERT INTO drive_folders
                   (drive_folder_id,parent_drive_folder_id,name,relative_path,created_at,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                ("folder-a-child", "folder-a", "Videos", "/Flow/Produto A/Videos", stamp, stamp),
            )
            self.conn.execute(
                """INSERT INTO drive_folders
                   (drive_folder_id,parent_drive_folder_id,name,relative_path,created_at,updated_at)
                   VALUES (?,?,?,?,?,?)""",
                ("folder-unknown", None, "Sem mapeamento", "/Flow/Sem mapeamento", stamp, stamp),
            )
        self._file("high", "PRODUTO_G1C1A1_0101.mp4", "folder-a", "video/mp4", 1000)
        self._file("medium", "PRODUTO_VIDEO 2.mp4", "folder-a-child", "video/mp4", 1100)
        self._file("low", "produto-sem-padrao.mp4", "folder-unknown", "video/mp4", 1200)
        self._file("suspicious", "NOTFINALIZED.mp4", "folder-a", "video/mp4", 1250)
        self._file("image", "capa.png", "folder-a", "image/png", 300)
        self._file("empty-caption", "OUTRO_G1C1A1_0101.mp4", "folder-unknown", "video/mp4", 1300)
        self.service.assign_media_product({"drive_file_ids": ["empty-caption"], "product_id": self.product_empty})

    def _product(self, name, caption):
        return self.service.save_product({"name": name, "default_caption": caption})

    def _file(self, file_id, name, folder_id, mime, size):
        stamp = now_iso()
        with self.conn:
            self.conn.execute(
                """INSERT INTO drive_files
                   (drive_file_id,drive_folder_id,name,mime_type,size,file_kind,
                    classification,last_seen_at,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,'NEEDS_REVIEW',?,?,?)""",
                (file_id, folder_id, name, mime, size, "VIDEO" if mime.startswith("video/") else "IMAGE", stamp, stamp, stamp),
            )

    def test_classification_is_conservative_and_inherits_product_folder(self):
        result = list_library(self.conn, file_type="ALL", page_size=20)
        by_id = {item["drive_file_id"]: item for item in result["items"]}

        self.assertEqual(by_id["high"]["status"], "AVAILABLE")
        self.assertEqual(by_id["high"]["classification_confidence"], "HIGH")
        self.assertEqual(by_id["high"]["product_id"], self.product_a)
        self.assertEqual(by_id["medium"]["status"], "AVAILABLE")
        self.assertEqual(by_id["medium"]["classification_confidence"], "MEDIUM")
        self.assertEqual(by_id["medium"]["product_id"], self.product_a)
        self.assertEqual(by_id["medium"]["product_resolution"], "ANCESTOR_FOLDER")
        self.assertEqual(by_id["low"]["status"], "ATTENTION")
        self.assertEqual(by_id["low"]["reason"], "Produto da pasta ainda não associado")
        self.assertEqual(by_id["suspicious"]["classification_confidence"], "LOW")
        self.assertEqual(by_id["image"]["status"], "OUT_OF_SCOPE")
        self.assertEqual(by_id["empty-caption"]["status"], "ATTENTION")
        self.assertEqual(by_id["empty-caption"]["reason"], "Legenda do produto não configurada")
        self.assertEqual(by_id["high"]["classification_label"], "Reconhecido")
        self.assertEqual(by_id["medium"]["classification_label"], "Provável")
        self.assertEqual(by_id["suspicious"]["classification_label"], "Revisar")

    def test_decision_and_product_override_are_persistent(self):
        self.service.decide_media({"drive_file_id": "low", "decision": "VALID"})
        self.service.assign_media_product({"drive_file_ids": ["low"], "product_id": self.product_b})
        item = {x["drive_file_id"]: x for x in list_library(self.conn, file_type="VIDEO", page_size=20)["items"]}["low"]
        self.assertEqual(item["decision"], "VALID")
        self.assertEqual(item["product_id"], self.product_b)
        self.assertEqual(item["status"], "AVAILABLE")

        self.service.decide_media({"drive_file_id": "low", "decision": "IGNORED", "reason": "revisado"})
        item = {x["drive_file_id"]: x for x in list_library(self.conn, file_type="VIDEO", page_size=20)["items"]}["low"]
        self.assertEqual(item["status"], "IGNORED")
        self.assertEqual(item["reason"], "revisado")

    def test_filters_and_pagination_are_server_side_contract(self):
        page = list_library(self.conn, file_type="VIDEO", page=1, page_size=2)
        self.assertEqual(page["page_size"], 2)
        self.assertEqual(page["total"], 5)
        self.assertEqual(page["pages"], 3)
        filtered = list_library(self.conn, search="VIDEO 2", file_type="VIDEO")
        self.assertEqual([item["drive_file_id"] for item in filtered["items"]], ["medium"])
        unassigned = list_library(self.conn, product_id="UNASSIGNED", file_type="VIDEO")
        self.assertEqual({item["drive_file_id"] for item in unassigned["items"]}, {"low"})

    def test_library_batch_preflight_is_atomic_and_preserves_per_media_product(self):
        with self.assertRaisesRegex(ValueError, "BATCH_PREFLIGHT_FAILED"):
            self.service.create_library_batch({"drive_file_ids": ["high", "low"], "account_id": self.account})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_batches").fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_drafts").fetchone()[0], 0)

        self.service.decide_media({"drive_file_id": "low", "decision": "VALID"})
        self.service.assign_media_product({"drive_file_ids": ["low"], "product_id": self.product_b})
        result = self.service.create_library_batch({"name": "Lote multi-produto", "drive_file_ids": ["high", "low"], "account_id": self.account})
        self.assertEqual(len(result["draft_ids"]), 2)
        drafts = self.conn.execute(
            "SELECT product_id,caption,status FROM publisher_drafts ORDER BY id"
        ).fetchall()
        self.assertEqual([(row[0], row[1], row[2]) for row in drafts], [
            (self.product_a, "Legenda A", "DRAFT"),
            (self.product_b, "Legenda B", "DRAFT"),
        ])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets WHERE status='RESERVED'").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='CREATE'").fetchone()[0], 2)

    def test_batch_rejects_missing_caption_before_reserving_media(self):
        with self.assertRaisesRegex(ValueError, "Legenda do produto n.o configurada"):
            self.service.create_library_batch({"drive_file_ids": ["empty-caption"], "account_id": self.account})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets").fetchone()[0], 0)

    def test_folder_assignment_applies_to_existing_and_future_files(self):
        before = list_library(self.conn, product_id="UNASSIGNED", file_type="VIDEO")
        summary = next(row for row in before["folder_summaries"] if row["folder_id"] == "folder-unknown")
        self.assertGreaterEqual(summary["video_count"], 1)

        result = self.service.assign_media_product({
            "scope": "folder", "folder_id": "folder-unknown", "product_id": self.product_b,
        })
        self.assertEqual(result["scope"], "folder")
        current = {row["drive_file_id"]: row for row in list_library(self.conn, file_type="VIDEO", page_size=20)["items"]}
        self.assertEqual(current["low"]["product_id"], self.product_b)
        self.assertEqual(current["low"]["product_resolution"], "DIRECT_FOLDER")

        self._file("future", "NOVO_G4C2A1.mp4", "folder-unknown", "video/mp4", 1400)
        future = {row["drive_file_id"]: row for row in list_library(self.conn, file_type="VIDEO", page_size=20)["items"]}["future"]
        self.assertEqual(future["product_id"], self.product_b)
        self.assertEqual(future["status"], "AVAILABLE")
        history = self.conn.execute(
            "SELECT new_product_id FROM drive_folder_mapping_history WHERE drive_folder_id='folder-unknown' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(history[0], self.product_b)

    def test_media_override_wins_over_direct_and_ancestor_folder(self):
        self.service.assign_media_product({"drive_file_ids": ["high", "medium"], "product_id": self.product_b})
        rows = {row["drive_file_id"]: row for row in list_library(self.conn, file_type="VIDEO", page_size=20)["items"]}
        self.assertEqual(rows["high"]["product_id"], self.product_b)
        self.assertEqual(rows["high"]["product_resolution"], "MEDIA_OVERRIDE")
        self.assertEqual(rows["medium"]["product_id"], self.product_b)
        self.assertEqual(rows["medium"]["product_resolution"], "MEDIA_OVERRIDE")

    def test_embedded_gca_patterns_are_recognized_without_weakening_suspicious_names(self):
        recognized = [
            "KIT_5_BS_COROA_SEREIA_BRILHO_G1C3A1_0101.mp4",
            "KIT_5_BS_COROA_SEREIA_BRILHO_G2C4A2_FINAL.mp4",
        ]
        suspicious = ["c1.mp4", "cta1.mp4", "VD1.mp4", "vd2.mp4", "SEMAUDIO.mp4", "REF MOTION.mp4"]
        for index, name in enumerate(recognized):
            self._file(f"recognized-{index}", name, "folder-a", "video/mp4", 1500 + index)
        for index, name in enumerate(suspicious):
            self._file(f"review-{index}", name, "folder-a", "video/mp4", 1600 + index)
        rows = {row["drive_file_id"]: row for row in list_library(self.conn, file_type="VIDEO", page_size=50)["items"]}
        self.assertTrue(all(rows[f"recognized-{index}"]["classification_label"] == "Reconhecido" for index in range(len(recognized))))
        self.assertTrue(all(rows[f"review-{index}"]["classification_label"] == "Revisar" for index in range(len(suspicious))))

    def test_drive_delete_requires_confirmation_and_post_delete_verification(self):
        provider = FakeMediaProvider()
        with self.assertRaisesRegex(ValueError, "CONFIRM_MEDIA_DELETE"):
            self.service.delete_library_media({"drive_file_id": "high", "filename": "PRODUTO_G1C1A1_0101.mp4"}, provider)

        result = self.service.delete_library_media({
            "drive_file_id": "high", "filename": "PRODUTO_G1C1A1_0101.mp4", "size": 1000, "human_confirmed": True,
        }, provider)
        self.assertEqual(result["remote_exists_after"], False)
        self.assertEqual(provider.deleted, ["high"])
        self.assertIsNotNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='high'").fetchone()[0])

    def test_library_endpoint_returns_paginated_inventory_without_side_effects(self):
        api = PublisherAPI(".", self.db_file.name)
        response = {}
        handler = SimpleNamespace(_json=lambda value, **_: response.update(value))
        api.get(handler, {"page": ["1"], "page_size": ["2"], "type": ["VIDEO"]}, "/api/publisher/library")
        self.assertEqual(response["page_size"], 2)
        self.assertEqual(response["total"], 5)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_drafts").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
