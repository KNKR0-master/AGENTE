import tempfile
import unittest
from pathlib import Path

from src.publisher.folder_catalog import (
    apply_catalog_migration,
    decide_folder_role,
    preview_catalog_migration,
    sync_drive_product_name,
)
from src.publisher.library import list_library
from src.publisher.service import PublisherService, init_publisher
from src.store import connect, init_db, now_iso
from src.drive_inventory import DriveEntry, _resolve_folder_mapping


class PublisherFolderCatalogTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        temp.close()
        self.path = Path(temp.name)
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))
        self.conn = connect(self.path)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, ".")
        stamp = now_iso()
        with self.conn:
            for folder_id, name, parent in [
                ("category", "Feminino", None),
                ("safe", "Produto seguro", "category"),
                ("medium", "Produto medio", "category"),
                ("duplicate", "MEIA BICHINHO", "category"),
                ("container", "Body Splash", "category"),
            ]:
                self.conn.execute(
                    """INSERT INTO drive_folders
                       (drive_folder_id,parent_drive_folder_id,name,relative_path,operational_status,
                        created_at,updated_at) VALUES (?,?,?,?,'ACTIVE',?,?)""",
                    (folder_id, parent, name, f"/{name}", stamp, stamp),
                )

    def plan(self):
        def row(folder_id, role, confidence, *, dedup="NO_MATCH", matches=None):
            name = self.conn.execute("SELECT name FROM drive_folders WHERE drive_folder_id=?", (folder_id,)).fetchone()[0]
            return {"folder_id": folder_id, "folder_name": name, "proposed_role": role,
                    "confidence": confidence, "reason": "test", "deduplication": dedup,
                    "possible_existing_product_matches": matches or [], "existing_product_id": None,
                    "suggested_product_name": name, "suggested_short_name": name}
        rows = [
            row("category", "CATEGORY", "HIGH"), row("safe", "PRODUCT", "HIGH"),
            row("medium", "PRODUCT", "MEDIUM"), row("duplicate", "PRODUCT", "HIGH"),
            row("container", "PRODUCT_CONTAINER", "HIGH"),
        ]
        return {"summary": {"total_relevant_folders": len(rows)}, "folders": rows}

    def test_safe_high_creation_roles_and_idempotent_rerun(self):
        preview = preview_catalog_migration(self.conn, self.plan())
        self.assertEqual(preview["safe_high_products_to_create"], 1)
        first = apply_catalog_migration(self.conn, self.plan())
        second = apply_catalog_migration(self.conn, self.plan())
        self.assertEqual(first["products_created"], 1)
        self.assertEqual(second["products_created"], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_products").fetchone()[0], 1)
        roles = dict(self.conn.execute("SELECT drive_folder_id,folder_type FROM drive_folders"))
        self.assertEqual(roles["category"], "CATEGORY")
        self.assertEqual(roles["container"], "PRODUCT_CONTAINER")
        self.assertEqual(roles["medium"], "UNCLASSIFIED")
        self.assertEqual(roles["duplicate"], "UNCLASSIFIED")

    def test_manual_decision_precedence_and_manual_products_preserved(self):
        manual_product = self.service.save_product({"name": "Manual"})
        decide_folder_role(self.conn, "safe", "CATEGORY")
        apply_catalog_migration(self.conn, self.plan())
        row = self.conn.execute("SELECT folder_type,role_source FROM drive_folders WHERE drive_folder_id='safe'").fetchone()
        self.assertEqual(tuple(row), ("CATEGORY", "MANUAL"))
        self.assertEqual(self.service.get_product(manual_product)["name"], "Manual")

    def test_drive_and_manual_name_sources(self):
        apply_catalog_migration(self.conn, self.plan())
        product = self.conn.execute("SELECT product_id FROM publisher_products WHERE source_drive_folder_id='safe'").fetchone()[0]
        sync_drive_product_name(self.conn, "safe", "Nome Drive Novo")
        self.assertEqual(self.service.get_product(product)["name"], "Nome Drive Novo")
        self.service.save_product({"id": product, "name": "Nome Humano", "short_name": "Humano"})
        sync_drive_product_name(self.conn, "safe", "Outro nome Drive")
        self.assertEqual(self.service.get_product(product)["name"], "Nome Humano")

    def test_product_inheritance_and_media_override_priority(self):
        apply_catalog_migration(self.conn, self.plan())
        product = self.conn.execute("SELECT product_id FROM drive_folders WHERE drive_folder_id='safe'").fetchone()[0]
        other = self.service.save_product({"name": "Override", "default_caption": "Legenda"})
        self.service.save_product({"id": product, "name": "Produto seguro", "default_caption": "Legenda"})
        stamp = now_iso()
        with self.conn:
            self.conn.execute("INSERT INTO drive_folders(drive_folder_id,parent_drive_folder_id,name,relative_path,created_at,updated_at) VALUES ('child','safe','Videos','/Produto seguro/Videos',?,?)", (stamp, stamp))
            self.conn.execute("""INSERT INTO drive_files(drive_file_id,drive_folder_id,name,mime_type,size,file_kind,
                              last_seen_at,created_at,updated_at) VALUES ('video','child','X_G1C1A1.mp4','video/mp4',10,'VIDEO',?,?,?)""", (stamp, stamp, stamp))
        item = list_library(self.conn, page_size=10)["items"][0]
        self.assertEqual(item["product_id"], product)
        self.service.assign_media_product({"drive_file_ids": ["video"], "product_id": other})
        item = list_library(self.conn, page_size=10)["items"][0]
        self.assertEqual(item["product_id"], other)
        self.assertEqual(item["product_resolution"], "MEDIA_OVERRIDE")

    def test_rescan_preserves_high_confidence_migration_mapping(self):
        existing = {
            "mapping_status": "MAPPED_TO_PRODUCT", "product_id": 42,
            "mapping_source": "MIGRATION_HIGH_CONFIDENCE", "mapping_confidence": None,
        }
        entry = DriveEntry(file_id="folder", name="Renamed", mime_type="application/vnd.google-apps.folder")
        result = _resolve_folder_mapping(self.conn, object(), existing, None, entry, "/Renamed", "UNKNOWN", [])
        self.assertEqual(result["product_id"], 42)
        self.assertEqual(result["mapping_source"], "MIGRATION_HIGH_CONFIDENCE")


if __name__ == "__main__":
    unittest.main()
