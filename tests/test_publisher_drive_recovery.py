import re
import sqlite3
import unittest
from unittest.mock import patch

from src.drive_inventory import DriveEntry, DriveInventoryConfig, DriveInventoryScanner, FOLDER_MIME_TYPE
from src.publisher.drive_recovery import reconcile_drive_inventory_and_project_media
from src.publisher.drive_sync import project_drive_inventory_to_publisher_media
from src.publisher.library import get_library_items
from src.publisher.service import init_publisher
from src.store import init_db


class InventoryProvider:
    def __init__(self):
        self.fail = False
        self.root = DriveEntry("root", "Root", FOLDER_MIME_TYPE)
        self.video = DriveEntry("video", "G1C1A1.mp4", "video/mp4", ("root",), size=100)

    def get_folder(self, _folder):
        return self.root

    def list_children(self, _folder):
        if self.fail:
            raise RuntimeError("isolated failure")
        return [self.video]


class PublisherDriveRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        init_publisher(self.conn)
        self.conn.execute("INSERT INTO products(id,product_key,name,created_at,updated_at) VALUES (1,'p','Product','old','old')")
        self.conn.execute("INSERT INTO publisher_products(product_id,name) VALUES (1,'Product')")
        self.conn.execute("INSERT INTO publisher_accounts(id,name,browser_profile,created_at,updated_at) VALUES (1,'Test','test','old','old')")
        self.config = DriveInventoryConfig("root", "ceciliafigueiredo.shop@gmail.com", None)
        self.provider = InventoryProvider()
        self.conn.execute("""INSERT INTO drive_folders
          (drive_folder_id,name,relative_path,folder_type,mapping_status,operational_status,
           role_source,role_review_status,created_at,updated_at)
          VALUES ('root','Root','Root','PRODUCT','NEEDS_REVIEW','OUT_OF_SCOPE','MANUAL','APPLIED','old','old')""")
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def file(self, file_id="video", *, missing=None, classification="NEEDS_REVIEW"):
        self.conn.execute("""INSERT INTO drive_files
          (drive_file_id,drive_folder_id,name,mime_type,size,file_kind,classification,
           missing_since,last_seen_at,created_at,updated_at)
          VALUES (?,'root','G1C1A1.mp4','video/mp4',100,'VIDEO',? ,?,'observed','old','old')""",
          (file_id, classification, missing))
        self.conn.execute("UPDATE drive_folders SET operational_status='ACTIVE'")
        self.conn.commit()

    def asset(self, file_id="video", status="MISSING", deleted=None):
        cursor = self.conn.execute("""INSERT INTO publisher_media_assets
          (remote_file_id,filename,mime_type,status,remote_deleted_at,created_at,updated_at)
          VALUES (?,'historic','video/mp4',?,?,'old','old')""", (file_id, status, deleted))
        self.conn.commit()
        return cursor.lastrowid

    def status(self, file_id="video"):
        return self.conn.execute("SELECT status FROM publisher_media_assets WHERE remote_file_id=?", (file_id,)).fetchone()[0]

    def project(self):
        with self.conn:
            return project_drive_inventory_to_publisher_media(self.conn, root_folder_id="root")

    def test_pure_scan_does_not_call_catalog_or_products(self):
        with patch("src.drive_inventory.sync_affiliate_product_catalog") as catalog, patch(
            "src.drive_inventory.sync_drive_product_name"
        ) as names, patch("src.drive_inventory.sync_folder_prompts") as prompts:
            result = DriveInventoryScanner(self.provider, self.config).scan_inventory(self.conn, force=True)
        self.assertEqual(result["status"], "COMPLETE")
        catalog.assert_not_called(); names.assert_not_called(); prompts.assert_not_called()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets").fetchone()[0], 0)

    def test_legacy_scan_keeps_catalog_orchestration(self):
        with patch("src.drive_inventory.sync_affiliate_product_catalog") as catalog:
            DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        catalog.assert_called_once()

    def test_orchestrator_recovers_incident_and_write_boundary(self):
        self.file(missing="incident"); self.asset()
        self.conn.execute("UPDATE drive_folders SET operational_status='OUT_OF_SCOPE'")
        self.conn.commit()
        writes = []
        def trace(sql):
            match = re.match(r"\s*(?:INSERT(?:\s+OR\s+\w+)?\s+INTO|UPDATE|DELETE\s+FROM)\s+(\w+)", sql, re.I)
            if match:
                writes.append(match.group(1))
        self.conn.set_trace_callback(trace)
        with patch("src.drive_inventory.sync_affiliate_product_catalog") as catalog:
            result = reconcile_drive_inventory_and_project_media(self.conn, self.provider, self.config)
        self.conn.set_trace_callback(None)
        catalog.assert_not_called()
        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(self.status(), "AVAILABLE")
        self.assertTrue(set(writes) <= {"drive_folders", "drive_files", "drive_scan_runs", "publisher_media_assets"}, writes)

    def test_projection_requires_no_provider(self):
        self.file()
        with patch("src.publisher.media.GoogleDriveMediaProvider", side_effect=AssertionError("remote read")):
            self.assertEqual(self.project(), 1)

    def test_projection_creates_new_asset(self):
        self.file(); self.project()
        self.assertEqual(self.status(), "AVAILABLE")

    def test_projection_restores_missing_preserves_id(self):
        self.file(); asset_id = self.asset(); self.project()
        self.assertEqual(self.status(), "AVAILABLE")
        self.assertEqual(self.conn.execute("SELECT id FROM publisher_media_assets").fetchone()[0], asset_id)

    def test_trashed_file_not_revived(self):
        self.file(missing="now", classification="MISSING_FROM_DRIVE"); self.asset(); self.project()
        self.assertEqual(self.status(), "MISSING")

    def test_missing_file_not_revived(self):
        self.asset(); self.project()
        self.assertEqual(self.status(), "MISSING")

    def test_explicit_missing_classification_is_authoritative(self):
        self.file(classification="MISSING_FROM_DRIVE"); self.asset(); self.project()
        self.assertEqual(self.status(), "MISSING")

    def test_unavailable_parent_blocks_projection(self):
        self.file(); self.asset()
        self.conn.execute("UPDATE drive_folders SET operational_status='OUT_OF_SCOPE'")
        self.conn.commit(); self.project()
        self.assertEqual(self.status(), "MISSING")

    def test_active_folder_outside_root_is_not_available(self):
        self.file(); self.asset()
        with self.conn:
            project_drive_inventory_to_publisher_media(self.conn, root_folder_id="other-root")
        self.assertEqual(self.status(), "MISSING")

    def test_consumed_remains_consumed(self):
        self.file(); self.asset(status="CONSUMED", deleted="confirmed"); self.project()
        self.assertEqual(self.status(), "CONSUMED")

    def test_deleted_missing_not_revived(self):
        self.file(); self.asset(deleted="confirmed"); self.project()
        self.assertEqual(self.status(), "MISSING")

    def test_reserved_state_preserved(self):
        self.file(); self.asset(status="RESERVED"); self.project()
        self.assertEqual(self.status(), "RESERVED")

    def test_draft_relation_preserved_without_reviving(self):
        self.file(); asset_id = self.asset()
        self.conn.execute("""INSERT INTO publisher_drafts(product_id,account_id,video_path,filename,caption,status,media_asset_id,created_at,updated_at)
          VALUES (1,1,'x','x','caption','MANUAL_ACTION_REQUIRED',?,'old','old')""", (asset_id,))
        self.conn.commit(); before = tuple(self.conn.execute("SELECT * FROM publisher_drafts").fetchone())
        self.project()
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM publisher_drafts").fetchone()), before)
        self.assertEqual(self.status(), "MISSING")

    def test_valid_and_ignored_decisions_preserved(self):
        self.file(); self.file("ignored")
        self.conn.execute("INSERT INTO publisher_media_decisions(drive_file_id,decision,decided_at,updated_at) VALUES ('video','VALID','old','old'),('ignored','IGNORED','old','old')")
        self.conn.commit(); before = [tuple(r) for r in self.conn.execute("SELECT * FROM publisher_media_decisions ORDER BY drive_file_id")]
        self.project()
        self.assertEqual([tuple(r) for r in self.conn.execute("SELECT * FROM publisher_media_decisions ORDER BY drive_file_id")], before)

    def test_product_override_preserved(self):
        self.file()
        self.conn.execute("INSERT INTO publisher_media_overrides(drive_file_id,product_id,updated_at) VALUES ('video',1,'old')")
        self.conn.commit(); before = tuple(self.conn.execute("SELECT * FROM publisher_media_overrides").fetchone())
        self.project()
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM publisher_media_overrides").fetchone()), before)

    def test_unassigned_product_is_not_drive_unavailable(self):
        self.file(); self.project()
        item = get_library_items(self.conn, ["video"])[0]
        self.assertIsNone(item["product_id"])
        self.assertNotEqual(item["status"], "REMOTE_UNAVAILABLE")

    def test_second_projection_has_no_writes_or_timestamp_churn(self):
        self.file(); self.project()
        before = tuple(self.conn.execute("SELECT * FROM publisher_media_assets").fetchone())
        changes = self.conn.total_changes
        self.assertEqual(self.project(), 0)
        self.assertEqual(self.conn.total_changes, changes)
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM publisher_media_assets").fetchone()), before)

    def test_incomplete_scan_fails_closed(self):
        self.provider.fail = True
        with patch("src.publisher.drive_recovery.project_drive_inventory_to_publisher_media") as projection:
            result = reconcile_drive_inventory_and_project_media(self.conn, self.provider, self.config)
        self.assertEqual(result["status"], "RECOVERY_INCOMPLETE")
        projection.assert_not_called()

    def test_observation_update_does_not_touch_semantic_timestamp(self):
        self.file(); self.project()
        before = self.conn.execute("SELECT updated_at FROM publisher_media_assets").fetchone()[0]
        self.conn.execute("UPDATE drive_files SET last_seen_at='later'"); self.conn.commit(); self.project()
        self.assertEqual(self.conn.execute("SELECT updated_at FROM publisher_media_assets").fetchone()[0], before)

    def test_recovery_preserves_product_mapping_and_content(self):
        self.conn.execute("UPDATE drive_folders SET product_id=1,mapping_status='MAPPED_TO_PRODUCT',mapping_source='MANUAL'")
        self.conn.execute("UPDATE publisher_products SET name_source='DRIVE',source_drive_folder_id='root'")
        self.conn.commit()
        before = tuple(self.conn.execute("SELECT * FROM publisher_products").fetchone())
        reconcile_drive_inventory_and_project_media(self.conn, self.provider, self.config)
        self.assertEqual(tuple(self.conn.execute("SELECT * FROM publisher_products").fetchone()), before)
        row = self.conn.execute("SELECT product_id,mapping_status,mapping_source FROM drive_folders WHERE drive_folder_id='root'").fetchone()
        self.assertEqual(tuple(row), (1, "MAPPED_TO_PRODUCT", "MANUAL"))

    def test_pending_remote_effect_not_revived(self):
        self.file(); self.asset(status="NEEDS_RECONCILIATION"); self.project()
        self.assertEqual(self.status(), "NEEDS_RECONCILIATION")

    def test_changed_metadata_updates_semantic_timestamp_once(self):
        self.file(); self.project()
        self.conn.execute("UPDATE publisher_media_assets SET updated_at='old'")
        self.conn.execute("UPDATE drive_files SET name='G2C2A2.mp4'")
        self.conn.commit()
        self.assertEqual(self.project(), 1)
        row = self.conn.execute("SELECT filename,updated_at FROM publisher_media_assets").fetchone()
        self.assertEqual(row["filename"], "G2C2A2.mp4")
        self.assertNotEqual(row["updated_at"], "old")
        self.assertEqual(self.project(), 0)


if __name__ == "__main__":
    unittest.main()
