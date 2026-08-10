from __future__ import annotations

import json
import sqlite3
import unittest

from src.drive_inventory import (
    DriveEntry,
    DriveInventoryConfig,
    DriveInventoryScanner,
    FOLDER_MIME_TYPE,
    product_content_inventory,
    set_folder_mapping,
)
from src.product_assets import (
    archive_product_caption,
    duplicate_product_caption,
    list_product_captions,
    list_product_prompts,
    save_product_caption,
)
from src.store import init_db, now_iso, upsert_product


class ProfileDriveProvider:
    def __init__(self) -> None:
        self.root = folder("flow", "FLOW")
        self.text = "Gancho inicial"
        self.prompt_present = True
        self.calls: list[str] = []

    def get_folder(self, folder_id: str) -> DriveEntry:
        if folder_id != "flow":
            raise ValueError("root inesperada")
        return self.root

    def list_children(self, folder_id: str) -> list[DriveEntry]:
        self.calls.append(folder_id)
        tree = {
            "flow": [
                folder("ceci", "CECI FIGUEIREDO", "flow"),
                folder("fe", "FE FIGUEIREDO", "flow"),
                folder("old", "VISTAS LINDAS", "flow"),
            ],
            "ceci": [folder("category", "FEMININO", "ceci")],
            "category": [folder("product", "Produto Alfa", "category")],
            "product": [video("video", "Produto_Alfa_FINAL.mp4", "product")],
        }
        if folder_id == "product" and self.prompt_present:
            tree["product"].append(text_file("prompt", "prompt_loja.txt", "product"))
        return tree.get(folder_id, [])

    def read_text(self, entry: DriveEntry) -> str:
        if entry.file_id != "prompt":
            raise ValueError("arquivo inesperado")
        return self.text


class DriveProfilesAndAssetsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        init_db(self.conn)
        self.product_id = upsert_product(self.conn, "Produto Alfa")
        self.provider = ProfileDriveProvider()
        self.config = DriveInventoryConfig(
            root_folder_id="flow",
            account_email="ceciliafigueiredo.shop@gmail.com",
            credentials_file=None,
            active_profile_folder_id="ceci",
            reserve_profile_folder_ids=("fe",),
            archived_profile_folder_ids=("old",),
        )
        insert_ready_content(self.conn, self.product_id)

    def tearDown(self) -> None:
        self.conn.close()

    def test_only_active_profile_is_scanned_and_counted(self) -> None:
        result = DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        profiles = {
            row["drive_folder_id"]: row["status"]
            for row in self.conn.execute("SELECT drive_folder_id, status FROM creator_drive_profiles")
        }
        inventory = product_content_inventory(self.conn, self.config)[0]

        self.assertEqual(result["operational_root_folder_id"], "ceci")
        self.assertEqual(profiles, {"flow": "SYSTEM", "ceci": "ACTIVE", "fe": "RESERVE", "old": "ARCHIVED"})
        self.assertNotIn("fe", self.provider.calls)
        self.assertNotIn("old", self.provider.calls)
        self.assertEqual(inventory["available_count"], 1)
        self.assertEqual(inventory["drive_stock_count"], 1)
        self.assertEqual(inventory["prompt_count"], 1)
        self.assertEqual(
            self.conn.execute("SELECT folder_type FROM drive_folders WHERE drive_folder_id = 'flow'").fetchone()[0],
            "SYSTEM_DATA",
        )
        self.assertEqual(
            self.conn.execute("SELECT folder_type FROM drive_folders WHERE drive_folder_id = 'category'").fetchone()[0],
            "CATEGORY",
        )

    def test_running_scan_prevents_second_process_from_reconciling_files(self) -> None:
        timestamp = now_iso()
        self.conn.execute(
            """
            INSERT INTO drive_scan_runs (account_email, root_folder_id, status, started_at)
            VALUES (?, 'flow', 'RUNNING', ?)
            """,
            (self.config.account_email, timestamp),
        )
        self.conn.commit()
        result = DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        self.assertEqual(result["status"], "RUNNING")
        self.assertTrue(result["in_progress"])
        self.assertEqual(self.provider.calls, [])

    def test_prompt_updates_idempotently_and_missing_source_is_preserved(self) -> None:
        scanner = DriveInventoryScanner(self.provider, self.config)
        scanner.scan(self.conn, force=True)
        self.provider.text = "Gancho atualizado"
        scanner.scan(self.conn, force=True)
        prompts = list_product_prompts(self.conn, self.product_id)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(prompts[0]["prompt_text"], "Gancho atualizado")

        self.provider.prompt_present = False
        scanner.scan(self.conn, force=True)
        prompt = list_product_prompts(self.conn, self.product_id)[0]
        self.assertEqual(prompt["status"], "SOURCE_MISSING")
        self.assertEqual(prompt["source_missing"], 1)

    def test_manual_mapping_supports_multiple_folders_for_one_product(self) -> None:
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        timestamp = now_iso()
        self.conn.execute(
            """
            INSERT INTO drive_folders (
                drive_folder_id, parent_drive_folder_id, name, relative_path, depth,
                folder_type, mapping_status, operational_status, scan_status,
                created_at, updated_at
            ) VALUES ('second', 'category', 'Produto Alfa UGC',
                'CECI FIGUEIREDO/FEMININO/Produto Alfa UGC', 2,
                'PRODUCT', 'NEEDS_REVIEW', 'ACTIVE', 'SUCCESS', ?, ?)
            """,
            (timestamp, timestamp),
        )
        self.conn.commit()
        set_folder_mapping(self.conn, "product", self.product_id)
        set_folder_mapping(self.conn, "second", self.product_id)
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM drive_folders WHERE product_id = ? AND mapping_source = 'MANUAL'",
                (self.product_id,),
            ).fetchone()[0],
            2,
        )

    def test_caption_crud_ready_count_and_alert_context(self) -> None:
        caption = save_product_caption(
            self.conn,
            self.product_id,
            title="Beneficios",
            caption_text="Texto pronto",
            hashtags="#produto",
            status="READY",
        )
        duplicate = duplicate_product_caption(self.conn, int(caption["id"]))
        archive_product_caption(self.conn, int(duplicate["id"]))
        DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)

        inventory = product_content_inventory(self.conn, self.config)[0]
        event = self.conn.execute(
            "SELECT reasons FROM content_supply_alert_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(inventory["ready_caption_count"], 1)
        self.assertEqual(len(list_product_captions(self.conn, self.product_id)), 1)
        self.assertIn("1 prompts e 1 legendas prontas", " ".join(json.loads(event["reasons"])))


def folder(file_id: str, name: str, parent: str | None = None) -> DriveEntry:
    return DriveEntry(file_id, name, FOLDER_MIME_TYPE, (parent,) if parent else ())


def video(file_id: str, name: str, parent: str) -> DriveEntry:
    return DriveEntry(
        file_id,
        name,
        "video/mp4",
        (parent,),
        100,
        f"md5-{file_id}",
        "2026-08-08T10:00:00+00:00",
        "2026-08-08T10:00:00+00:00",
        f"https://drive.google.com/file/d/{file_id}/view",
    )


def text_file(file_id: str, name: str, parent: str) -> DriveEntry:
    return DriveEntry(file_id, name, "text/plain", (parent,), modified_time="2026-08-08T10:00:00+00:00")


def insert_ready_content(conn: sqlite3.Connection, product_id: int) -> None:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO content_items (
            content_code, product_id, original_filename, file_hash,
            google_drive_file_id, file_size, validation_status,
            validation_results, status, created_at, updated_at
        ) VALUES ('ALFA-0001', ?, 'Produto_Alfa_FINAL.mp4', 'hash-video',
                  'video', 100, 'PASS', '{}', 'READY', ?, ?)
        """,
        (product_id, timestamp, timestamp),
    )
    conn.commit()


if __name__ == "__main__":
    unittest.main()
