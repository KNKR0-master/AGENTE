import sqlite3
import threading
import tempfile
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.drive_inventory import DriveInventoryConfig, FOLDER_MIME_TYPE, GoogleApiDriveInventoryProvider
from src.publisher.drive_sync import (
    IncrementalDriveSync,
    SyncScope,
    get_sync_state,
    drive_sync_status,
    incremental_drive_sync,
    project_drive_inventory_to_publisher_media,
)
from src.publisher.service import PublisherService, init_publisher
from src.publisher.library import get_library_items, list_group_items, list_library_groups
from src.store import init_db, now_iso


class FakeChangesProvider:
    def __init__(self, pages=None, token="T0"):
        self.pages = pages or {}
        self.token = token
        self.within = set()

    def get_start_page_token(self):
        return self.token

    def list_changes(self, token):
        value = self.pages[token]
        if isinstance(value, Exception):
            raise value
        return value

    def is_within_root(self, file_id, root_id):
        return file_id in self.within


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    def execute(self, **_kwargs):
        return self.payload


class FakeChangesResource:
    def __init__(self):
        self.list_kwargs = None

    def getStartPageToken(self, **_kwargs):
        return FakeRequest({"startPageToken": "START"})

    def list(self, **kwargs):
        self.list_kwargs = kwargs
        return FakeRequest({"changes": [{"fileId": "x", "removed": False,
                            "file": {"id": "x", "name": "x.mp4", "mimeType": "video/mp4",
                                     "parents": ["root"], "trashed": False, "size": "2"}}],
                            "newStartPageToken": "NEXT"})


def folder_change(file_id, name, parent="root", **extra):
    payload = {"id": file_id, "name": name, "mimeType": FOLDER_MIME_TYPE,
               "parents": [parent] if parent else [], "trashed": False}
    payload.update(extra)
    return {"file_id": file_id, "removed": False, "file": payload}


def file_change(file_id, name="VIDEO_G1C1A1.mp4", parent="product", **extra):
    payload = {"id": file_id, "name": name, "mimeType": "video/mp4", "parents": [parent],
               "trashed": False, "size": "123", "md5Checksum": f"md5-{file_id}",
               "createdTime": "2026-09-20T10:00:00Z", "modifiedTime": "2026-09-20T10:00:00Z"}
    payload.update(extra)
    return {"file_id": file_id, "removed": False, "file": payload}


class PublisherDriveSyncTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        init_db(self.conn)
        init_publisher(self.conn)
        self.config = DriveInventoryConfig(
            root_folder_id="root", account_email="ceciliafigueiredo.shop@gmail.com",
            credentials_file=None,
        )
        stamp = now_iso()
        self.conn.execute(
            """INSERT INTO drive_folders
               (drive_folder_id,name,relative_path,depth,folder_type,mapping_status,
                operational_status,role_source,role_review_status,created_at,updated_at)
               VALUES ('root','Root','Root',0,'CATEGORY','CATEGORY_ONLY','ACTIVE','MANUAL','APPLIED',?,?)""",
            (stamp, stamp),
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def sync(self, pages=None):
        provider = FakeChangesProvider(pages)
        return IncrementalDriveSync(self.conn, provider, self.config), provider

    def ready(self, cursor="C0"):
        state = get_sync_state(self.conn, SyncScope(
            "ceciliafigueiredo.shop@gmail.com", "google_drive", "root"
        ))
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drive_sync_state SET current_cursor=?,cursor_status='READY' WHERE id=?",
                (cursor, state["id"]),
            )

    def add_product_folder(self, *, manual=False):
        product = PublisherService(self.conn, ".").save_product({"name": "Produto", "default_caption": "Legenda"})
        stamp = now_iso()
        self.conn.execute(
            """INSERT INTO drive_folders
               (drive_folder_id,parent_drive_folder_id,name,relative_path,depth,folder_type,
                mapping_status,product_id,mapping_source,operational_status,role_source,
                role_review_status,auto_create_new_direct_children,created_at,updated_at)
               VALUES ('product','root','Produto','Root/Produto',1,?,'MAPPED_TO_PRODUCT',?,
                       'MANUAL','ACTIVE',?,'APPLIED',0,?,?)""",
            ("PRODUCT_CONTAINER" if manual else "PRODUCT", product,
             "MANUAL" if manual else "HEURISTIC", stamp, stamp),
        )
        self.conn.commit()
        return product

    def test_sync_state_migration_is_idempotent_and_scope_is_unique(self):
        init_publisher(self.conn)
        init_publisher(self.conn)
        scope = SyncScope("ceciliafigueiredo.shop@gmail.com", "google_drive", "root")
        first = get_sync_state(self.conn, scope)
        second = get_sync_state(self.conn, scope)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_drive_sync_state").fetchone()[0], 1)

    def test_google_changes_adapter_normalizes_minimal_page(self):
        resource = FakeChangesResource()
        provider = GoogleApiDriveInventoryProvider.__new__(GoogleApiDriveInventoryProvider)
        provider._service = type("Service", (), {"changes": lambda _self: resource})()
        self.assertEqual(provider.get_start_page_token(), "START")
        page = provider.list_changes("START")
        self.assertEqual(page["new_start_page_token"], "NEXT")
        self.assertEqual(page["changes"][0]["file_id"], "x")
        self.assertTrue(resource.list_kwargs["includeRemoved"])

    def test_intermediate_and_final_cursors_commit_in_order(self):
        self.add_product_folder()
        pages = {
            "C0": {"changes": [file_change("a")], "next_page_token": "C1", "new_start_page_token": None},
            "C1": {"changes": [file_change("b")], "next_page_token": None, "new_start_page_token": "C2"},
        }
        sync, _ = self.sync(pages); self.ready()
        result = sync.cycle()
        self.assertEqual((result["pages_processed"], result["files_upserted"]), (2, 2))
        self.assertEqual(self.conn.execute("SELECT current_cursor FROM publisher_drive_sync_state").fetchone()[0], "C2")
        self.assertEqual(self.conn.execute("SELECT last_sync_mode FROM publisher_drive_sync_state").fetchone()[0], "INCREMENTAL")

    def test_transaction_failure_preserves_previous_cursor(self):
        self.add_product_folder(); self.ready()
        sync, _ = self.sync({"C0": {"changes": [{"removed": False}], "new_start_page_token": "C1"}})
        result = sync.cycle()
        row = self.conn.execute("SELECT current_cursor,cursor_status FROM publisher_drive_sync_state").fetchone()
        self.assertEqual(row["current_cursor"], "C0")
        self.assertEqual((result["status"], row["cursor_status"]), ("ERROR", "ERROR"))

    def test_replay_is_idempotent_and_preserves_decision_override(self):
        product = self.add_product_folder(); self.ready()
        page = {"changes": [file_change("same")], "new_start_page_token": "C1"}
        sync, _ = self.sync({"C0": page, "C1": {**page, "new_start_page_token": "C2"}})
        sync.cycle()
        with self.conn:
            self.conn.execute("INSERT INTO publisher_media_decisions VALUES ('same','VALID',?,'human',?)", (now_iso(), now_iso()))
            self.conn.execute("INSERT INTO publisher_media_overrides VALUES ('same',?,?,'human')", (product, now_iso()))
            self.conn.execute("UPDATE publisher_drive_sync_state SET current_cursor='C1'")
        sync.cycle()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_files WHERE drive_file_id='same'").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT decision FROM publisher_media_decisions WHERE drive_file_id='same'").fetchone()[0], "VALID")
        self.assertEqual(self.conn.execute("SELECT product_id FROM publisher_media_overrides WHERE drive_file_id='same'").fetchone()[0], product)

    def test_file_rename_and_move_keep_identity(self):
        self.add_product_folder(); self.ready()
        sync, _ = self.sync({"C0": {"changes": [file_change("moving")], "new_start_page_token": "C1"}})
        sync.cycle()
        stamp = now_iso()
        with self.conn:
            self.conn.execute("INSERT INTO drive_folders(drive_folder_id,parent_drive_folder_id,name,relative_path,depth,created_at,updated_at) VALUES ('other','root','Other','Root/Other',1,?,?)", (stamp, stamp))
            self.conn.execute("UPDATE publisher_drive_sync_state SET current_cursor='C1'")
        sync.provider.pages["C1"] = {"changes": [file_change("moving", "RENAMED_G2C1A1.mp4", "other")], "new_start_page_token": "C2"}
        sync.cycle()
        row = self.conn.execute("SELECT drive_folder_id,name FROM drive_files WHERE drive_file_id='moving'").fetchone()
        self.assertEqual(tuple(row), ("other", "RENAMED_G2C1A1.mp4"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0], 1)

    def test_move_out_trash_and_sparse_remove_mark_unavailable(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("gone")], "new_start_page_token": "C1"}})
        sync.cycle(); self.conn.execute("UPDATE publisher_drive_sync_state SET current_cursor='C1'"); self.conn.commit()
        provider.pages["C1"] = {"changes": [{"file_id": "gone", "removed": True, "file": None}], "new_start_page_token": "C2"}
        sync.cycle()
        self.assertIsNotNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='gone'").fetchone()[0])

    def test_move_outside_root_marks_existing_file_unavailable(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("moving-out")], "new_start_page_token": "C1"}})
        sync.cycle(); self.conn.execute("UPDATE publisher_drive_sync_state SET current_cursor='C1'"); self.conn.commit()
        provider.pages["C1"] = {"changes": [file_change("moving-out", parent="foreign")], "new_start_page_token": "C2"}
        sync.cycle()
        self.assertIsNotNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='moving-out'").fetchone()[0])

    def test_move_into_root_reuses_existing_identity(self):
        self.add_product_folder(); self.ready()
        stamp = now_iso()
        self.conn.execute(
            """INSERT INTO drive_files(drive_file_id,drive_folder_id,name,mime_type,size,last_seen_at,
               missing_since,created_at,updated_at) VALUES ('returning','product','old.mp4','video/mp4',123,?,?,?,?)""",
            (stamp, stamp, stamp, stamp),
        )
        self.conn.commit()
        sync, _ = self.sync({"C0": {"changes": [file_change("returning", "RETURN_G2C1A1.mp4")], "new_start_page_token": "C1"}})
        sync.cycle()
        row = self.conn.execute("SELECT name,missing_since FROM drive_files WHERE drive_file_id='returning'").fetchone()
        self.assertEqual(tuple(row), ("RETURN_G2C1A1.mp4", None))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_files WHERE drive_file_id='returning'").fetchone()[0], 1)

    def test_trashed_file_marks_unavailable_without_deleting_history(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("trash")], "new_start_page_token": "C1"}})
        sync.cycle(); self.conn.execute("UPDATE publisher_drive_sync_state SET current_cursor='C1'"); self.conn.commit()
        provider.pages["C1"] = {"changes": [file_change("trash", trashed=True)], "new_start_page_token": "C2"}
        sync.cycle()
        row = self.conn.execute("SELECT classification,missing_since FROM drive_files WHERE drive_file_id='trash'").fetchone()
        self.assertEqual(row["classification"], "MISSING_FROM_DRIVE")
        self.assertIsNotNone(row["missing_since"])

    def test_trashed_file_does_not_inflate_operational_group_count(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("alive"), file_change("trash")], "new_start_page_token": "C1"}})
        sync.cycle()
        provider.pages["C1"] = {"changes": [file_change("trash", trashed=True)], "new_start_page_token": "C2"}
        sync.cycle()
        groups = list_library_groups(self.conn, group_view="ALL")["groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["total_media_count"], 1)
        self.assertEqual(groups[0]["matching_media_count"], 1)
        self.assertEqual(list_library_groups(self.conn, group_view="REMOTE_UNAVAILABLE")["total_matching_media"], 1)

    def test_trashed_folder_preserves_human_state_and_excludes_descendants(self):
        product = self.add_product_folder(manual=True); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("video")], "new_start_page_token": "C1"}})
        sync.cycle()
        with self.conn:
            self.conn.execute("INSERT INTO publisher_media_decisions VALUES ('video','VALID',?,'human',?)", (now_iso(), now_iso()))
            self.conn.execute("INSERT INTO publisher_media_overrides VALUES ('video',?,?,'human')", (product, now_iso()))
        provider.pages["C1"] = {"changes": [folder_change("product", "Produto", trashed=True)], "new_start_page_token": "C2"}
        result = sync.cycle()
        self.assertEqual(result["cursor_status"], "READY")
        self.assertEqual(self.conn.execute("SELECT current_cursor FROM publisher_drive_sync_state").fetchone()[0], "C2")
        self.assertEqual(self.conn.execute("SELECT operational_status FROM drive_folders WHERE drive_folder_id='product'").fetchone()[0], "OUT_OF_SCOPE")
        self.assertEqual(self.conn.execute("SELECT classification_reason FROM drive_files WHERE drive_file_id='video'").fetchone()[0], "REMOTE_UNAVAILABLE:TRASHED")
        self.assertEqual(get_library_items(self.conn, ["video"]), [])
        self.assertEqual(list_library_groups(self.conn, group_view="ACTIVE")["total_groups"], 0)
        unavailable = list_library_groups(self.conn, group_view="REMOTE_UNAVAILABLE")
        self.assertEqual(unavailable["total_matching_media"], 1)
        self.assertEqual(unavailable["groups"][0]["available_count"], 0)
        unavailable_item = list_group_items(self.conn, unavailable["groups"][0]["group_key"],
                                            group_view="REMOTE_UNAVAILABLE")["items"][0]
        self.assertEqual(unavailable_item["status"], "REMOTE_UNAVAILABLE")
        self.assertEqual(unavailable_item["reason"], "Na lixeira do Drive")
        self.assertFalse(unavailable_item["eligible"])
        self.assertIsNone(unavailable_item["web_view_link"])
        self.assertIsNone(unavailable_item["thumbnail_link"])
        preview = PublisherService(self.conn, ".").preflight_library_batch({
            "name": "Fixture", "drive_file_ids": ["video"],
        })
        self.assertFalse(preview["can_create"])
        self.assertIn("MEDIA_NOT_IN_INVENTORY", {problem["code"] for problem in preview["problems"]})
        self.assertEqual(self.conn.execute("SELECT decision FROM publisher_media_decisions WHERE drive_file_id='video'").fetchone()[0], "VALID")
        self.assertEqual(self.conn.execute("SELECT product_id FROM publisher_media_overrides WHERE drive_file_id='video'").fetchone()[0], product)
        self.assertEqual(self.conn.execute("SELECT product_id FROM drive_folders WHERE drive_folder_id='product'").fetchone()[0], product)
        provider.pages["C2"] = {"changes": [folder_change("product", "Produto", trashed=True)], "new_start_page_token": "C3"}
        sync.cycle()
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM drive_files WHERE drive_file_id='video'").fetchone()[0], 1)

    def test_restored_parent_does_not_resurrect_unverified_files(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("video"), folder_change("product", "Produto", trashed=True)], "new_start_page_token": "C1"}})
        sync.cycle()
        provider.pages["C1"] = {"changes": [folder_change("product", "Produto")], "new_start_page_token": "C2"}
        sync.cycle()
        self.assertEqual(self.conn.execute("SELECT operational_status FROM drive_folders WHERE drive_folder_id='product'").fetchone()[0], "ACTIVE")
        self.assertIsNotNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='video'").fetchone()[0])
        provider.pages["C2"] = {"changes": [file_change("video")], "new_start_page_token": "C3"}
        sync.cycle()
        self.assertIsNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='video'").fetchone()[0])

    def test_removed_folder_tombstones_known_descendants(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("video")], "new_start_page_token": "C1"}})
        sync.cycle()
        provider.pages["C1"] = {"changes": [{"file_id": "product", "removed": True, "file": None}], "new_start_page_token": "C2"}
        sync.cycle()
        self.assertEqual(self.conn.execute("SELECT operational_status FROM drive_folders WHERE drive_folder_id='product'").fetchone()[0], "OUT_OF_SCOPE")
        self.assertEqual(self.conn.execute("SELECT classification_reason FROM drive_files WHERE drive_file_id='video'").fetchone()[0], "REMOTE_UNAVAILABLE:REMOVED")
        self.assertEqual(get_library_items(self.conn, ["video"]), [])

    def test_folder_move_outside_then_inside_keeps_identity_and_product(self):
        product = self.add_product_folder(manual=True); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("video")], "new_start_page_token": "C1"}})
        sync.cycle()
        provider.pages["C1"] = {"changes": [folder_change("product", "Produto", parent="foreign")], "new_start_page_token": "C2"}
        sync.cycle()
        self.assertEqual(self.conn.execute("SELECT operational_status FROM drive_folders WHERE drive_folder_id='product'").fetchone()[0], "OUT_OF_SCOPE")
        provider.pages["C2"] = {"changes": [folder_change("product", "Produto", parent="root")], "new_start_page_token": "C3"}
        sync.cycle()
        row = self.conn.execute("SELECT operational_status,product_id FROM drive_folders WHERE drive_folder_id='product'").fetchone()
        self.assertEqual(tuple(row), ("ACTIVE", product))
        self.assertIsNotNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='video'").fetchone()[0])

    def test_membership_unknown_does_not_create_false_missing(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("protected")], "new_start_page_token": "C1"}})
        sync.cycle(); self.conn.execute("UPDATE publisher_drive_sync_state SET current_cursor='C1'"); self.conn.commit()
        provider.is_within_root = lambda *_: None
        provider.pages["C1"] = {"changes": [file_change("protected", parent="inaccessible")], "new_start_page_token": "C2"}
        result = sync.cycle()
        self.assertEqual(result["status"], "TRANSIENT_ERROR")
        self.assertEqual(self.conn.execute("SELECT current_cursor FROM publisher_drive_sync_state").fetchone()[0], "C1")
        self.assertIsNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='protected'").fetchone()[0])

    def test_sparse_change_without_removed_flag_preserves_cursor(self):
        self.add_product_folder(); self.ready()
        sync, provider = self.sync({"C0": {"changes": [file_change("video")], "new_start_page_token": "C1"}})
        sync.cycle()
        provider.pages["C1"] = {"changes": [{"file_id": "video", "removed": False, "file": None}], "new_start_page_token": "C2"}
        self.assertEqual(sync.cycle()["status"], "TRANSIENT_ERROR")
        self.assertEqual(self.conn.execute("SELECT current_cursor FROM publisher_drive_sync_state").fetchone()[0], "C1")
        self.assertIsNone(self.conn.execute("SELECT missing_since FROM drive_files WHERE drive_file_id='video'").fetchone()[0])

    def test_new_folder_defaults_to_review_without_auto_product(self):
        self.ready()
        sync, _ = self.sync({"C0": {"changes": [folder_change("new-folder", "Nova")], "new_start_page_token": "C1"}})
        sync.cycle()
        row = self.conn.execute("SELECT folder_type,role_review_status,product_id,auto_create_new_direct_children FROM drive_folders WHERE drive_folder_id='new-folder'").fetchone()
        self.assertEqual(tuple(row), ("UNCLASSIFIED", "PENDING", None, 0))

    def test_folder_rename_move_updates_descendants_and_preserves_manual_state(self):
        product = self.add_product_folder(manual=True)
        stamp = now_iso()
        self.conn.execute("INSERT INTO drive_folders(drive_folder_id,parent_drive_folder_id,name,relative_path,depth,created_at,updated_at) VALUES ('child','product','Child','Root/Produto/Child',2,?,?)", (stamp, stamp))
        self.conn.commit(); self.ready()
        sync, _ = self.sync({"C0": {"changes": [folder_change("product", "Produto Novo")], "new_start_page_token": "C1"}})
        sync.cycle()
        row = self.conn.execute("SELECT folder_type,role_source,role_review_status,product_id,auto_create_new_direct_children FROM drive_folders WHERE drive_folder_id='product'").fetchone()
        child = self.conn.execute("SELECT relative_path FROM drive_folders WHERE drive_folder_id='child'").fetchone()[0]
        self.assertEqual(tuple(row), ("PRODUCT_CONTAINER", "MANUAL", "APPLIED", product, 0))
        self.assertEqual(child, "Root/Produto Novo/Child")

    def test_projection_updates_existing_and_never_resurrects_consumed(self):
        self.add_product_folder(); self.ready()
        sync, _ = self.sync({"C0": {"changes": [file_change("asset")], "new_start_page_token": "C1"}})
        sync.cycle()
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET status='CONSUMED',filename='historic' WHERE remote_file_id='asset'")
            self.conn.execute("UPDATE drive_files SET name='renamed.mp4' WHERE drive_file_id='asset'")
            project_drive_inventory_to_publisher_media(self.conn)
        row = self.conn.execute("SELECT status,filename FROM publisher_media_assets WHERE remote_file_id='asset'").fetchone()
        self.assertEqual(tuple(row), ("CONSUMED", "historic"))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets WHERE remote_file_id='asset'").fetchone()[0], 1)

    def test_bootstrap_covers_changes_after_start_token(self):
        self.add_product_folder()
        provider = FakeChangesProvider({"T0": {"changes": [file_change("state-b", "STATE_B_G1C1A1.mp4")], "new_start_page_token": "T1"}})
        sync = IncrementalDriveSync(self.conn, provider, self.config)
        def full_scan():
            entry = file_change("state-b", "STATE_A_G1C1A1.mp4")["file"]
            sync._upsert_file(__import__("src.drive_inventory", fromlist=["DriveEntry"]).DriveEntry.from_api(entry))
            self.conn.commit()
            return {"status": "COMPLETE"}
        result = sync.bootstrap(full_scan)
        row = self.conn.execute("SELECT name FROM drive_files WHERE drive_file_id='state-b'").fetchone()
        self.assertEqual((result["cursor_status"], row["name"]), ("READY", "STATE_B_G1C1A1.mp4"))
        self.assertEqual(self.conn.execute("SELECT last_sync_mode FROM publisher_drive_sync_state").fetchone()[0], "FULL")

    def test_status_read_does_not_create_cursor_or_expose_token(self):
        with patch('src.publisher.drive_sync.DriveInventoryConfig.from_env', return_value=self.config):
            before=drive_sync_status(self.conn)
            self.ready('private-fixture-token')
            after=drive_sync_status(self.conn)
        self.assertEqual(before['cursor_status'], 'UNINITIALIZED')
        self.assertEqual(after['cursor_status'], 'READY')
        self.assertNotIn('current_cursor', after)
        self.assertNotIn('private-fixture-token', str(after))

    def test_scoped_lock_rejects_overlapping_sync(self):
        entered=threading.Event(); release=threading.Event(); results=[]
        fake=Mock()
        def cycle():
            entered.set(); release.wait(5); return {'status':'READY'}
        fake.cycle.side_effect=cycle
        with patch('src.publisher.drive_sync.DriveInventoryConfig.from_env', return_value=self.config), \
             patch('src.publisher.drive_sync.GoogleApiDriveInventoryProvider.from_config'), \
             patch('src.publisher.drive_sync.IncrementalDriveSync', return_value=fake):
            thread=threading.Thread(target=lambda: results.append(incremental_drive_sync(self.conn)))
            thread.start()
            self.assertTrue(entered.wait(2))
            duplicate=incremental_drive_sync(self.conn)
            release.set();thread.join(3)
        self.assertEqual(duplicate['status'],'SYNC_IN_PROGRESS')
        self.assertEqual(results,[{'status':'READY'}])

    def test_legacy_route_delegates_and_bootstrap_requires_confirmation(self):
        from src.publisher.http import PublisherAPI
        from src.store import connect, init_db
        with tempfile.TemporaryDirectory() as directory:
            db=Path(directory)/'test.db'
            with closing(connect(db)) as conn:
                init_db(conn);init_publisher(conn)
            api=object.__new__(PublisherAPI);api.db=db;api.root=Path(directory)
            def request(path,body):
                import json
                payload=json.dumps(body).encode()
                handler=SimpleNamespace(headers={'Content-Length':str(len(payload)),'Content-Type':'application/json'},
                                        rfile=BytesIO(payload),_json=Mock())
                api.post(handler,path)
                return handler._json.call_args.args[0]
            with patch('src.publisher.http.incremental_drive_sync',return_value={'status':'READY'}) as sync, \
                 patch('src.publisher.http.media_provider') as old_provider:
                self.assertEqual(request('/api/publisher/sync-media',{})['status'],'READY')
                sync.assert_called_once()
                self.assertFalse(sync.call_args.kwargs['bootstrap'])
                old_provider.assert_not_called()
                with self.assertRaisesRegex(ValueError,'FULL_DRIVE_RESCAN_REQUIRES_CONFIRMATION'):
                    request('/api/publisher/drive-sync/incremental',{'bootstrap':True})
                self.assertEqual(sync.call_count,1)
                request('/api/publisher/drive-sync/incremental',{'bootstrap':True,'human_confirmed':True})
                self.assertTrue(sync.call_args.kwargs['bootstrap'])

    def test_invalid_cursor_requests_full_scan_without_remote_mutation(self):
        self.ready()
        sync, _ = self.sync({"C0": RuntimeError("410 invalid page token")})
        result = sync.cycle()
        self.assertTrue(result["full_scan_required"])
        self.assertEqual(result["cursor_status"], "FULL_SCAN_REQUIRED")

    def test_transient_change_error_preserves_ready_cursor_for_bounded_retry(self):
        self.ready()
        error = RuntimeError("temporary transport")
        error.__cause__ = TimeoutError("transport timeout")
        sync, _ = self.sync({"C0": error})
        result = sync.cycle()
        self.assertEqual(result["status"], "TRANSIENT_ERROR")
        row = self.conn.execute("SELECT current_cursor,cursor_status,last_error FROM publisher_drive_sync_state").fetchone()
        self.assertEqual(tuple(row), ("C0", "READY", "TRANSIENT_DRIVE_ERROR"))

    def test_auth_change_error_does_not_invalidate_cursor(self):
        self.ready()
        sync, _ = self.sync({"C0": RuntimeError("invalid_grant")})
        result = sync.cycle()
        self.assertEqual(result["status"], "AUTH_REQUIRED")
        row = self.conn.execute("SELECT current_cursor,cursor_status FROM publisher_drive_sync_state").fetchone()
        self.assertEqual(tuple(row), ("C0", "READY"))

    def test_missing_or_corrupt_local_cursor_requires_explicit_full_scan(self):
        sync, _ = self.sync()
        result = sync.cycle()
        self.assertTrue(result["full_scan_required"])
        self.assertEqual(result["cursor_status"], "UNINITIALIZED")


if __name__ == "__main__":
    unittest.main()
