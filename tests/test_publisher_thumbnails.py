import io
import tempfile
import threading
import time
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.publisher.thumbnails import (
    CACHE_TTL_SECONDS, MAX_IMAGE_BYTES, Thumbnail, ThumbnailCache,
    ThumbnailUnavailable, PublisherThumbnailService, _GoogleThumbnailRedirects,
    _safe_thumbnail_url, fetch_image,
)
from src.publisher.service import init_publisher
from src.publisher.http import PublisherAPI
from src.store import connect, init_db, now_iso


FILE_ID = "valid_video_file_123"
JPEG = b"\xff\xd8\xff\xe0preview"


class FakeResponse:
    def __init__(self, body=JPEG, content_type="image/jpeg", url="https://lh3.googleusercontent.com/preview",
                 length=None):
        self.body, self.url = io.BytesIO(body), url
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def __enter__(self): return self
    def __exit__(self, *_): pass
    def read(self, size): return self.body.read(size)
    def geturl(self): return self.url


class ThumbnailFetchTests(unittest.TestCase):
    def test_rejects_arbitrary_url_and_redirect(self):
        self.assertFalse(_safe_thumbnail_url("http://lh3.googleusercontent.com/x"))
        self.assertFalse(_safe_thumbnail_url("https://evil.example/x"))
        self.assertFalse(_safe_thumbnail_url("https://lh3.googleusercontent.com.evil.example/x"))
        self.assertFalse(_safe_thumbnail_url("https://lh3.googleusercontent.com:invalid/x"))
        with self.assertRaises(ThumbnailUnavailable):
            fetch_image("https://evil.example/x")
        with self.assertRaises(ThumbnailUnavailable):
            _GoogleThumbnailRedirects().redirect_request(
                None, None, 302, "redirect", {}, "https://evil.example/x")

    def test_valid_image_and_non_image(self):
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse()
            result = fetch_image("https://lh3.googleusercontent.com/preview")
            self.assertEqual(result, Thumbnail(JPEG, "image/jpeg"))
            opener.return_value.open.return_value = FakeResponse(b"<html>error</html>", "text/html")
            with self.assertRaisesRegex(ThumbnailUnavailable, "NOT_IMAGE"):
                fetch_image("https://lh3.googleusercontent.com/preview")
            opener.return_value.open.return_value = FakeResponse(b"<html>error</html>", "image/jpeg")
            with self.assertRaisesRegex(ThumbnailUnavailable, "INVALID_IMAGE"):
                fetch_image("https://lh3.googleusercontent.com/preview")

    def test_size_timeout_and_403(self):
        with patch("urllib.request.build_opener") as opener:
            opener.return_value.open.return_value = FakeResponse(length=MAX_IMAGE_BYTES + 1)
            with self.assertRaisesRegex(ThumbnailUnavailable, "TOO_LARGE"):
                fetch_image("https://lh3.googleusercontent.com/x")
            opener.return_value.open.return_value = FakeResponse(body=JPEG + b"x" * MAX_IMAGE_BYTES)
            with self.assertRaisesRegex(ThumbnailUnavailable, "TOO_LARGE"):
                fetch_image("https://lh3.googleusercontent.com/x")
            opener.return_value.open.side_effect = TimeoutError()
            with self.assertRaisesRegex(ThumbnailUnavailable, "FETCH_FAILED"):
                fetch_image("https://lh3.googleusercontent.com/x")
            opener.return_value.open.side_effect = urllib.error.HTTPError(
                "https://lh3.googleusercontent.com/x", 403, "Forbidden", {}, None)
            with self.assertRaisesRegex(ThumbnailUnavailable, "FETCH_FAILED"):
                fetch_image("https://lh3.googleusercontent.com/x")


class ThumbnailCacheTests(unittest.TestCase):
    def test_cache_hit_ttl_and_bounded_eviction(self):
        clock = [100.0]
        calls = []
        cache = ThumbnailCache(clock=lambda: clock[0])
        fetcher = lambda key: (calls.append(key) or Thumbnail(JPEG, "image/jpeg"))
        self.assertEqual(cache.get("one", fetcher).content, JPEG)
        cache.get("one", fetcher)
        self.assertEqual(calls, ["one"])
        self.assertEqual(cache.cache_hits, 1)
        clock[0] += CACHE_TTL_SECONDS + 1
        cache.get("one", fetcher)
        self.assertEqual(calls, ["one", "one"])
        for index in range(150):
            cache.get(str(index), fetcher)
        self.assertLessEqual(cache.size[0], 128)
        self.assertLessEqual(cache.size[1], 16_000_000)

    def test_single_flight_for_same_id(self):
        calls = []
        entered = threading.Event()
        release = threading.Event()
        cache = ThumbnailCache()

        def fetcher(key):
            calls.append(key)
            entered.set()
            release.wait(2)
            return Thumbnail(JPEG, "image/jpeg")

        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(cache.get, FILE_ID, fetcher)
            self.assertTrue(entered.wait(2))
            second = pool.submit(cache.get, FILE_ID, fetcher)
            third = pool.submit(cache.get, FILE_ID, fetcher)
            time.sleep(0.05)
            release.set()
            self.assertEqual([future.result().content for future in (first, second, third)], [JPEG] * 3)
        self.assertEqual(calls, [FILE_ID])


class ThumbnailScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.conn = connect(str(Path(self.temp.name) / "fixture.db"))
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        stamp = now_iso()
        with self.conn:
            for folder_id, parent_id in (("root", None), ("active", "root"), ("outside", None)):
                self.conn.execute("""INSERT INTO drive_folders
                    (drive_folder_id,parent_drive_folder_id,name,relative_path,created_at,updated_at)
                    VALUES (?,?,?,?,?,?)""", (folder_id, parent_id, folder_id, folder_id, stamp, stamp))
            for file_id, folder_id in ((FILE_ID, "active"), ("outside_video_123", "outside")):
                self.conn.execute("""INSERT INTO drive_files
                    (drive_file_id,drive_folder_id,name,mime_type,size,file_kind,thumbnail_link,last_seen_at,created_at,updated_at)
                    VALUES (?,?,?,'video/mp4',1234,'VIDEO','https://lh3.googleusercontent.com/stale',?,?,?)""",
                    (file_id, folder_id, "G1C1A1.mp4", stamp, stamp, stamp))

    def service(self, metadata=None, image_fetcher=None):
        payload = metadata or {"id": FILE_ID, "mimeType": "video/mp4",
                               "thumbnailLink": "https://lh3.googleusercontent.com/fresh"}
        execute = Mock(return_value=payload)
        get = Mock(return_value=SimpleNamespace(execute=execute))
        api = SimpleNamespace(files=lambda: SimpleNamespace(get=get))
        factory = Mock(return_value=SimpleNamespace(_service=api))
        service = PublisherThumbnailService(config=SimpleNamespace(active_profile_folder_id="root"),
                                            provider_factory=factory,
                                            image_fetcher=image_fetcher or Mock(return_value=Thumbnail(JPEG, "image/jpeg")))
        return service, factory, get

    def test_stale_stored_url_is_not_used_and_cache_prevents_second_remote_call(self):
        fetched = []
        service, factory, get = self.service(image_fetcher=lambda url: (fetched.append(url) or Thumbnail(JPEG, "image/jpeg")))
        self.assertEqual(service.get(self.conn, FILE_ID).content, JPEG)
        self.assertEqual(fetched, ["https://lh3.googleusercontent.com/fresh"])
        self.assertEqual(get.call_args.kwargs["fields"], "id,mimeType,thumbnailLink,modifiedTime")
        service.get(self.conn, FILE_ID)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(service.cache.cache_hits, 1)

    def test_missing_thumbnail_and_remote_failure_do_not_change_database(self):
        service, _, _ = self.service(metadata={"id": FILE_ID, "mimeType": "video/mp4"})
        self.assertIsNone(service.get(self.conn, FILE_ID))
        self.assertEqual(self.conn.execute("SELECT thumbnail_link FROM drive_files WHERE drive_file_id=?", (FILE_ID,)).fetchone()[0],
                         "https://lh3.googleusercontent.com/stale")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_media_assets").fetchone()[0], 0)
        failing, _, get = self.service()
        get.return_value.execute.side_effect = RuntimeError("secret remote failure")
        with self.assertRaisesRegex(ThumbnailUnavailable, "THUMBNAIL_REMOTE_FAILURE"):
            failing.get(self.conn, FILE_ID)

    def test_unknown_and_out_of_scope_rejected_before_google(self):
        service, factory, _ = self.service()
        for file_id in ("unknown_file_123", "outside_video_123", "https://evil.example/x", "../secret"):
            with self.assertRaisesRegex(ThumbnailUnavailable, "FILE_NOT_IN_PUBLISHER_SCOPE"):
                service.get(self.conn, file_id)
        factory.assert_not_called()

    def test_http_response_is_image_or_sanitized_404(self):
        api = PublisherAPI.__new__(PublisherAPI)
        api.db = str(Path(self.temp.name) / "fixture.db")
        api.thumbnails, factory, _ = self.service()

        class Handler:
            def __init__(self):
                self.status = None
                self.headers = {}
                self.wfile = io.BytesIO()
            def send_response(self, status): self.status = status
            def send_header(self, key, value): self.headers[key] = value
            def end_headers(self): pass

        good = Handler()
        api.get(good, {}, f"/api/publisher/library/thumbnail/{FILE_ID}")
        self.assertEqual(good.status, 200)
        self.assertEqual(good.headers["Content-Type"], "image/jpeg")
        self.assertEqual(good.wfile.getvalue(), JPEG)
        self.assertEqual(factory.call_count, 1)

        for file_id in ("outside_video_123", "https:%2F%2Fevil.example", "missing_123"):
            missing = Handler()
            api.get(missing, {}, f"/api/publisher/library/thumbnail/{file_id}")
            self.assertEqual(missing.status, 404)
            self.assertEqual(missing.wfile.getvalue(), b"")
            self.assertEqual(missing.headers["Cache-Control"], "no-store")
        self.assertEqual(factory.call_count, 1)
