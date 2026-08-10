from __future__ import annotations

import sqlite3
import unittest

from src.drive_inventory import (
    DriveEntry,
    DriveInventoryConfig,
    DriveInventoryError,
    DriveInventoryScanner,
    FOLDER_MIME_TYPE,
    VideoPublicationEligibilityService,
    is_stock_video_name,
    product_content_inventory,
    refresh_content_supply_alerts,
    set_folder_mapping,
)
from src.store import init_db, now_iso, upsert_product


class MockDriveProvider:
    def __init__(self, root: DriveEntry, children: dict[str, list[DriveEntry]]) -> None:
        self.root = root
        self.children = children
        self.failures: set[str] = set()

    def get_folder(self, folder_id: str) -> DriveEntry:
        if folder_id != self.root.file_id:
            raise DriveInventoryError("raiz incorreta")
        return self.root

    def list_children(self, folder_id: str) -> list[DriveEntry]:
        if folder_id in self.failures:
            raise DriveInventoryError("sem permissao")
        return list(self.children.get(folder_id, []))


class DriveInventoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        init_db(self.conn)
        self.product_id = upsert_product(self.conn, "Produto Alfa")
        self.root = folder("root", "FLOW")
        self.product_folder = folder("product-folder", "Produto Alfa", "root")
        self.provider = MockDriveProvider(
            self.root,
            {"root": [self.product_folder], "product-folder": []},
        )
        self.config = DriveInventoryConfig(
            root_folder_id="root", account_email="ceciliafigueiredo.shop@gmail.com", credentials_file=None
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_stock_name_rule_accepts_only_gera_pattern_or_final(self) -> None:
        self.assertTrue(is_stock_video_name("CREME_G2C10A3_0808.mp4"))
        self.assertTrue(is_stock_video_name("versao final.mp4"))
        self.assertFalse(is_stock_video_name("preview_aprovado.mp4"))

    def test_ready_content_counts_only_when_real_drive_file_exists(self) -> None:
        video = drive_video("video-1", "Produto_Alfa_G1C1A1.mp4", "product-folder")
        self.provider.children["product-folder"] = [video]
        insert_content(self.conn, self.product_id, video, "READY", "PASS")
        insert_content(
            self.conn,
            self.product_id,
            drive_video("absent", "Produto_Alfa_G2C1A1.mp4", "product-folder"),
            "READY",
            "PASS",
        )

        result = DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        inventory = product_content_inventory(self.conn, self.config)[0]

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(inventory["available_count"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM content_items WHERE status='READY'").fetchone()[0], 2)

    def test_published_and_queued_files_are_excluded_from_available(self) -> None:
        published = drive_video("published", "Produto_Alfa_G1C1A1.mp4", "product-folder")
        queued = drive_video("queued", "Produto_Alfa_G1C2A1.mp4", "product-folder")
        self.provider.children["product-folder"] = [published, queued]
        insert_content(self.conn, self.product_id, published, "PUBLISHED", "PASS")
        insert_content(self.conn, self.product_id, queued, "SENT_TO_PHONE", "PASS")

        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        classes = {
            row["drive_file_id"]: row["classification"]
            for row in self.conn.execute("SELECT drive_file_id, classification FROM drive_files")
        }
        self.assertEqual(classes, {"published": "ALREADY_PUBLISHED", "queued": "ALREADY_QUEUED"})

    def test_failed_technical_validation_is_invalid(self) -> None:
        video = drive_video("invalid", "Produto_Alfa_FINAL.mp4", "product-folder")
        self.provider.children["product-folder"] = [video]
        insert_content(self.conn, self.product_id, video, "NEEDS_REVIEW", "FAIL")
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        row = self.conn.execute("SELECT classification FROM drive_files").fetchone()
        self.assertEqual(row["classification"], "INVALID")

    def test_new_unvalidated_file_needs_review(self) -> None:
        self.provider.children["product-folder"] = [
            drive_video("new", "Produto_Alfa_G1C1A1.mp4", "product-folder")
        ]
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        row = self.conn.execute("SELECT classification, classification_reason FROM drive_files").fetchone()
        self.assertEqual(row["classification"], "NEEDS_REVIEW")
        self.assertIn("ffprobe", row["classification_reason"])

    def test_same_hash_is_marked_duplicate(self) -> None:
        first = drive_video("first", "Produto_Alfa_G1C1A1.mp4", "product-folder", md5="same")
        second = drive_video("second", "Produto_Alfa_G2C1A1.mp4", "product-folder", md5="same")
        self.provider.children["product-folder"] = [first, second]
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        duplicate = self.conn.execute(
            "SELECT * FROM drive_files WHERE drive_file_id = 'second'"
        ).fetchone()
        self.assertEqual(duplicate["classification"], "DUPLICATE")
        self.assertEqual(duplicate["duplicate_reason"], "SAME_HASH")
        self.assertEqual(duplicate["canonical_drive_file_id"], "first")

    def test_same_drive_file_seen_in_two_folders_keeps_one_canonical_record(self) -> None:
        second_folder = folder("second-folder", "Produto Alfa", "root")
        self.provider.children["root"].append(second_folder)
        video = drive_video("same-id", "Produto_Alfa_G1C1A1.mp4", "product-folder")
        self.provider.children["product-folder"] = [video]
        self.provider.children["second-folder"] = [drive_video(
            "same-id", "Produto_Alfa_G1C1A1.mp4", "second-folder"
        )]
        insert_content(self.conn, self.product_id, video, "READY", "PASS")
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        rows = self.conn.execute("SELECT classification FROM drive_files").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["classification"], "AVAILABLE")

    def test_numbered_posted_folder_is_administrative(self) -> None:
        posted = folder("posted", "05_postados", "root")
        self.provider.children["root"] = [posted]
        self.provider.children["posted"] = [drive_video("old", "FINAL.mp4", "posted")]
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        folder_row = self.conn.execute(
            "SELECT folder_type, mapping_status FROM drive_folders WHERE drive_folder_id = 'posted'"
        ).fetchone()
        file_row = self.conn.execute("SELECT classification FROM drive_files").fetchone()
        self.assertEqual(folder_row["folder_type"], "ADMINISTRATIVE")
        self.assertEqual(folder_row["mapping_status"], "ADMINISTRATIVE")
        self.assertEqual(file_row["classification"], "IGNORED")

    def test_moved_file_updates_folder_without_duplication(self) -> None:
        second_folder = folder("second-folder", "Produto Alfa - variacoes", "root")
        self.provider.children["root"].append(second_folder)
        video = drive_video("moving", "Produto_Alfa_G1C1A1.mp4", "product-folder")
        self.provider.children["product-folder"] = [video]
        self.provider.children["second-folder"] = []
        insert_content(self.conn, self.product_id, video, "READY", "PASS")
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)

        self.provider.children["product-folder"] = []
        self.provider.children["second-folder"] = [drive_video(
            "moving", "Produto_Alfa_G1C1A1.mp4", "second-folder"
        )]
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)

        rows = self.conn.execute("SELECT drive_folder_id, missing_since FROM drive_files").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["drive_folder_id"], "second-folder")
        self.assertIsNone(rows[0]["missing_since"])

    def test_removed_file_becomes_missing_from_drive(self) -> None:
        video = drive_video("removed", "Produto_Alfa_G1C1A1.mp4", "product-folder")
        self.provider.children["product-folder"] = [video]
        insert_content(self.conn, self.product_id, video, "READY", "PASS")
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        self.provider.children["product-folder"] = []
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        row = self.conn.execute("SELECT classification, missing_since FROM drive_files").fetchone()
        self.assertEqual(row["classification"], "MISSING_FROM_DRIVE")
        self.assertIsNotNone(row["missing_since"])

    def test_inaccessible_folder_makes_partial_scan_without_losing_other_results(self) -> None:
        inaccessible = folder("blocked", "Bloqueada", "root")
        self.provider.children["root"].append(inaccessible)
        self.provider.failures.add("blocked")
        result = DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        self.assertEqual(result["status"], "PARTIAL")
        self.assertEqual(result["folders_failed"], 1)
        blocked = self.conn.execute(
            "SELECT scan_status FROM drive_folders WHERE drive_folder_id = 'blocked'"
        ).fetchone()
        self.assertEqual(blocked["scan_status"], "ERROR")

    def test_unmapped_folder_never_counts_for_a_product(self) -> None:
        unknown = folder("unknown", "Nome sem correspondencia", "root")
        self.provider.children["root"] = [unknown]
        self.provider.children["unknown"] = [drive_video("loose", "FINAL.mp4", "unknown")]
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        file_row = self.conn.execute("SELECT product_id, classification FROM drive_files").fetchone()
        self.assertIsNone(file_row["product_id"])
        self.assertEqual(file_row["classification"], "NEEDS_REVIEW")

    def test_manual_mapping_is_audited_and_reprocesses_folder(self) -> None:
        unknown = folder("unknown", "Nome sem correspondencia", "root")
        self.provider.children["root"] = [unknown]
        video = drive_video("manual", "FINAL.mp4", "unknown")
        self.provider.children["unknown"] = [video]
        insert_content(self.conn, self.product_id, video, "READY", "PASS")
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)

        set_folder_mapping(self.conn, "unknown", self.product_id)

        file_row = self.conn.execute("SELECT product_id, classification FROM drive_files").fetchone()
        history = self.conn.execute("SELECT * FROM drive_folder_mapping_history").fetchall()
        self.assertEqual(file_row["product_id"], self.product_id)
        self.assertEqual(file_row["classification"], "AVAILABLE")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["source"], "MANUAL")

    def test_alert_is_deduplicated_until_material_change(self) -> None:
        video = drive_video("one", "Produto_Alfa_G1C1A1.mp4", "product-folder")
        self.provider.children["product-folder"] = [video]
        insert_content(self.conn, self.product_id, video, "READY", "PASS")
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        inventory = product_content_inventory(self.conn, self.config)
        before = self.conn.execute("SELECT COUNT(*) FROM content_supply_alert_events").fetchone()[0]
        refresh_content_supply_alerts(self.conn, inventory, self.config)
        after = self.conn.execute("SELECT COUNT(*) FROM content_supply_alert_events").fetchone()[0]
        self.assertEqual(before, 1)
        self.assertEqual(after, 1)

    def test_direct_eligibility_ignores_non_stock_video(self) -> None:
        folder_data = {
            "mapping_status": "MAPPED_TO_PRODUCT",
            "product_id": self.product_id,
        }
        result = VideoPublicationEligibilityService().classify(
            self.conn,
            drive_video("preview", "preview.mp4", "product-folder"),
            folder_data,
        )
        self.assertEqual(result["classification"], "IGNORED")


def folder(file_id: str, name: str, parent: str | None = None) -> DriveEntry:
    return DriveEntry(file_id, name, FOLDER_MIME_TYPE, (parent,) if parent else ())


def drive_video(
    file_id: str,
    name: str,
    parent: str,
    md5: str | None = None,
    size: int = 100,
) -> DriveEntry:
    return DriveEntry(
        file_id=file_id,
        name=name,
        mime_type="video/mp4",
        parent_ids=(parent,),
        size=size,
        md5_checksum=md5 or f"md5-{file_id}",
        created_time="2026-08-08T10:00:00+00:00",
        modified_time="2026-08-08T10:00:00+00:00",
        web_view_link=f"https://drive.google.com/file/d/{file_id}/view",
    )


def insert_content(
    conn: sqlite3.Connection,
    product_id: int,
    video: DriveEntry,
    status: str,
    validation: str,
) -> int:
    cursor = conn.execute(
        """
        INSERT INTO content_items (
            content_code, product_id, original_filename, file_hash,
            google_drive_file_id, google_drive_url, file_size,
            validation_status, validation_results, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?)
        """,
        (
            f"CONTENT-{video.file_id}",
            product_id,
            video.name,
            f"hash-{video.file_id}",
            video.file_id,
            video.web_view_link,
            video.size,
            validation,
            status,
            now_iso(),
            now_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


if __name__ == "__main__":
    unittest.main()
