"""The official server must not capture Publisher Drive config before startup."""

import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.publisher.service import init_publisher
from src.publisher.thumbnails import Thumbnail
from src.store import connect, init_db, now_iso


ROOT = Path(__file__).resolve().parents[1]
FILE_ID = "valid_video_file_123"
OUTSIDE_ID = "outside_video_file_123"
JPEG = b"\xff\xd8\xff\xe0preview"


class RuntimeInitializationTests(unittest.TestCase):
    def test_official_import_then_config_then_thumbnail_request(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ):
            os.environ.pop("GOOGLE_DRIVE_ACTIVE_PROFILE_FOLDER_ID", None)
            os.environ.pop("GOOGLE_DRIVE_TIKTOK_ROOT_FOLDER_ID", None)
            db = Path(folder) / "fixture.db"
            with closing(connect(db)) as conn:
                init_db(conn)
                init_publisher(conn)
                stamp = now_iso()
                with conn:
                    for folder_id, parent_id in (("root", None), ("active", "root"), ("outside", None)):
                        conn.execute("""INSERT INTO drive_folders
                            (drive_folder_id,parent_drive_folder_id,name,relative_path,created_at,updated_at)
                            VALUES (?,?,?,?,?,?)""", (folder_id, parent_id, folder_id, folder_id, stamp, stamp))
                    for file_id, parent in ((FILE_ID, "active"), (OUTSIDE_ID, "outside")):
                        conn.execute("""INSERT INTO drive_files
                            (drive_file_id,drive_folder_id,name,mime_type,size,file_kind,created_at,updated_at,last_seen_at)
                            VALUES (?,?,?,'video/mp4',123,'VIDEO',?,?,?)""",
                            (file_id, parent, file_id + ".mp4", stamp, stamp, stamp))

            spec = importlib.util.spec_from_file_location("dashboard_server_runtime_fixture", ROOT / "dashboard_server.py")
            server_module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(server_module)
            self.assertFalse(server_module.PUBLISHER and server_module.PUBLISHER.thumbnails.config.active_profile_folder_id)

            # This is the launcher's module-import -> main() configuration boundary.
            os.environ["GOOGLE_DRIVE_ACTIVE_PROFILE_FOLDER_ID"] = "root"
            server_module.ROOT = Path(folder)
            server_module.DB_PATH = db
            if hasattr(server_module, "initialize_publisher_api"):
                server_module.initialize_publisher_api()
            else:
                server_module.PUBLISHER.db = db
            publisher = server_module.PUBLISHER
            self.assertIsNotNone(publisher)

            execute = Mock(return_value={"id": FILE_ID, "mimeType": "video/mp4",
                                         "thumbnailLink": "https://lh3.googleusercontent.com/fresh"})
            get = Mock(return_value=SimpleNamespace(execute=execute))
            api = SimpleNamespace(files=lambda: SimpleNamespace(get=get))
            publisher.thumbnails.provider_factory = Mock(return_value=SimpleNamespace(_service=api))
            publisher.thumbnails.image_fetcher = Mock(return_value=Thumbnail(JPEG, "image/jpeg"))
            http = ThreadingHTTPServer(("127.0.0.1", 0), server_module.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True)
            thread.start()
            try:
                base = f"http://127.0.0.1:{http.server_port}/api/publisher/library/thumbnail/"
                with urllib.request.urlopen(base + FILE_ID, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers.get_content_type(), "image/jpeg")
                    self.assertEqual(response.read(), JPEG)
                with urllib.request.urlopen(base + FILE_ID, timeout=5) as response:
                    self.assertEqual(response.read(), JPEG)
                self.assertEqual(get.call_count, 1)
                for file_id in (OUTSIDE_ID, "unknown_video_file_123"):
                    with self.assertRaises(urllib.error.HTTPError) as failure:
                        urllib.request.urlopen(base + file_id, timeout=5)
                    self.assertEqual(failure.exception.code, 404)
                self.assertEqual(get.call_count, 1)
                if hasattr(server_module, "initialize_publisher_api"):
                    self.assertIs(server_module.initialize_publisher_api(), publisher)
            finally:
                http.shutdown()
                http.server_close()
