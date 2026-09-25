"""Opt-in read-only thumbnail smoke against four canonical Drive records."""

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from src.config import load_env_file
from src.drive_inventory import DriveInventoryConfig
from src.publisher.library import list_group_items, list_library_groups
from src.publisher.thumbnails import PublisherThumbnailService, ThumbnailUnavailable
from tests.test_publisher_library_groups_browser import GroupFixtureHandler


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "agent.db"


def readonly_db():
    conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class OperationalThumbnailHandler(GroupFixtureHandler):
    group = None
    items = None
    service = None
    requests = []
    outcomes = []

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/publisher/library/groups":
            return self.send_json(dict(groups=[type(self).group], total_groups=1, total_matching_media=4,
                                       page=1, page_size=20, pages=1))
        if path == "/api/publisher/library/group-items":
            return self.send_json(dict(group_key=type(self).group["group_key"], items=type(self).items,
                                       total=4, page=1, page_size=50, pages=1))
        if path.startswith("/api/publisher/library/thumbnail/"):
            file_id = path.rsplit("/", 1)[-1]
            type(self).requests.append(file_id)
            with readonly_db() as conn:
                try:
                    thumbnail = type(self).service.get(conn, file_id)
                except ThumbnailUnavailable as exc:
                    type(self).outcomes.append(str(exc))
                    thumbnail = None
                else:
                    type(self).outcomes.append("IMAGE_OK" if thumbnail else "NO_THUMBNAIL")
            if thumbnail is None:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", thumbnail.content_type)
            self.send_header("Content-Length", str(len(thumbnail.content)))
            self.end_headers()
            self.wfile.write(thumbnail.content)
            return
        return super().do_GET()


@unittest.skipUnless(os.getenv("PUBLISHER_THUMBNAIL_OPERATIONAL_SMOKE") == "1",
                     "Opt-in real Drive thumbnail read-only smoke")
class OperationalThumbnailTests(unittest.TestCase):
    def test_four_real_items_in_temporary_chrome(self):
        from playwright.sync_api import sync_playwright

        self.assertTrue(shutil.which("chrome") or Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe").exists())
        load_env_file(ROOT / ".env")
        config = DriveInventoryConfig.from_env()
        self.assertEqual(config.auth_mode, "user_oauth")
        credential = Path(config.oauth_credential_store)
        before = hashlib.sha256(credential.read_bytes()).hexdigest()
        service = PublisherThumbnailService(config=config)
        with readonly_db() as conn:
            groups = list_library_groups(conn, page=1, page_size=1000, file_type="VIDEO")["groups"]
            sample = None
            for group in groups:
                items = list_group_items(conn, group["group_key"], page_size=100)["items"]
                yes = [item for item in items if item.get("thumbnail_link") and service._local_video(conn, item["drive_file_id"])]
                no = [item for item in items if not item.get("thumbnail_link") and service._local_video(conn, item["drive_file_id"])]
                if len(yes) >= 3 and no:
                    sample = (group, yes[:3] + no[:1])
                    break
        self.assertIsNotNone(sample, "No 3+1 operational thumbnail sample found")
        group, items = sample
        OperationalThumbnailHandler.group = {**group, "matching_media_count": 4, "total_media_count": 4}
        OperationalThumbnailHandler.items = items
        OperationalThumbnailHandler.service = service
        OperationalThumbnailHandler.requests = []
        OperationalThumbnailHandler.outcomes = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), OperationalThumbnailHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel="chrome", headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f"http://127.0.0.1:{server.server_port}/publisher.html")
                    page.locator('nav [data-view="library"]').click()
                    toggle = page.locator('[data-library-group-toggle]').first
                    toggle.wait_for()
                    self.assertEqual(OperationalThumbnailHandler.requests, [])
                    toggle.click()
                    page.wait_for_function("document.querySelectorAll('#libraryRows [data-thumbnail]').length === 4")
                    page.wait_for_function("[...document.querySelectorAll('#libraryRows [data-thumbnail]')].every(img => img.complete && (img.naturalWidth > 0 || img.hidden))", timeout=30000)
                    rendered = page.locator('#libraryRows [data-thumbnail]:visible').count()
                    fallbacks = page.locator('#libraryRows .media-thumbnail.placeholder:visible').count()
                    self.assertEqual(rendered + fallbacks, 4)
                    self.assertEqual(set(OperationalThumbnailHandler.requests), {item["drive_file_id"] for item in items})
                    self.assertGreaterEqual(service.cache.metadata_reads, 4)
                    print("THUMBNAIL_OPERATIONAL=" + json.dumps(dict(sample=4, rendered=rendered,
                        fallbacks=fallbacks, browser_requests=len(OperationalThumbnailHandler.requests),
                        metadata_reads=service.cache.metadata_reads,
                        image_fetches=service.cache.image_fetches, cache_hits=service.cache.cache_hits,
                        outcomes=OperationalThumbnailHandler.outcomes,
                        credential_file_changed=hashlib.sha256(credential.read_bytes()).hexdigest() != before)))
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(hashlib.sha256(credential.read_bytes()).hexdigest(), before)
