"""Bounded, local-only previews for canonical Drive video records."""

from __future__ import annotations

import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass

from src.drive_inventory import DriveInventoryConfig, GoogleApiDriveInventoryProvider


MAX_IMAGE_BYTES = 1_000_000
MAX_CACHE_BYTES = 16_000_000
MAX_CACHE_ENTRIES = 128
CACHE_TTL_SECONDS = 600
FAILURE_TTL_SECONDS = 30
STALE_TTL_SECONDS = 1800
TRANSIENT_FAILURES = {'THUMBNAIL_FETCH_FAILED', 'THUMBNAIL_REMOTE_FAILURE', 'THUMBNAIL_INFLIGHT_TIMEOUT'}
FETCH_TIMEOUT_SECONDS = 8
FILE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")


class ThumbnailUnavailable(Exception):
    pass


@dataclass(frozen=True)
class Thumbnail:
    content: bytes
    content_type: str


def _safe_thumbnail_url(url: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
        host = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return False
    return (parsed.scheme == "https" and parsed.username is None and parsed.password is None
            and port in (None, 443) and
            (host == "drive.google.com" or host.endswith(".googleusercontent.com")))


class _GoogleThumbnailRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not _safe_thumbnail_url(newurl):
            raise ThumbnailUnavailable("UNSAFE_THUMBNAIL_REDIRECT")
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch_image(url: str) -> Thumbnail:
    if not _safe_thumbnail_url(url):
        raise ThumbnailUnavailable("UNSAFE_THUMBNAIL_URL")
    opener = urllib.request.build_opener(_GoogleThumbnailRedirects)
    request = urllib.request.Request(url, headers={"User-Agent": "PublisherDesk/thumbnail"})
    try:
        with opener.open(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            if not _safe_thumbnail_url(response.geturl()):
                raise ThumbnailUnavailable("UNSAFE_THUMBNAIL_REDIRECT")
            content_type = response.headers.get_content_type().lower()
            if content_type not in {"image/jpeg", "image/png", "image/webp", "image/gif"}:
                raise ThumbnailUnavailable("THUMBNAIL_NOT_IMAGE")
            if int(response.headers.get("Content-Length") or 0) > MAX_IMAGE_BYTES:
                raise ThumbnailUnavailable("THUMBNAIL_TOO_LARGE")
            content = response.read(MAX_IMAGE_BYTES + 1)
            if len(content) > MAX_IMAGE_BYTES:
                raise ThumbnailUnavailable("THUMBNAIL_TOO_LARGE")
            signatures = {"image/jpeg": content.startswith(b"\xff\xd8"),
                          "image/png": content.startswith(b"\x89PNG\r\n\x1a\n"),
                          "image/webp": content.startswith(b"RIFF") and content[8:12] == b"WEBP",
                          "image/gif": content.startswith((b"GIF87a", b"GIF89a"))}
            if not signatures[content_type]:
                raise ThumbnailUnavailable("THUMBNAIL_INVALID_IMAGE")
            return Thumbnail(content, content_type)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ThumbnailUnavailable("THUMBNAIL_FETCH_FAILED") from exc


class ThumbnailCache:
    def __init__(self, *, clock=time.monotonic, fetcher=None):
        self.clock = clock
        self.fetcher = fetcher
        self._lock = threading.Lock()
        self._entries = OrderedDict()
        self._inflight = {}
        self._failures = OrderedDict()
        self._bytes = 0
        self.cache_hits = 0
        self.metadata_reads = 0
        self.image_fetches = 0

    def _cached(self, file_id):
        record = self._entries.get(file_id)
        if record and record[1] > self.clock():
            self._entries.move_to_end(file_id)
            self.cache_hits += 1
            return record[0]
        if record and record[1] + STALE_TTL_SECONDS <= self.clock():
            self._bytes -= len(record[0].content)
            del self._entries[file_id]
        return None

    def _fallback(self, file_id):
        record = self._entries.get(file_id)
        if record and record[1] + STALE_TTL_SECONDS > self.clock():
            return record[0]
        return None

    def _failed(self, file_id):
        record = self._failures.get(file_id)
        if record and record[0] > self.clock():
            value = self._fallback(file_id) if record[1] is None or record[1] in TRANSIENT_FAILURES else None
            if value:
                return True, value
            if record[1]:
                raise ThumbnailUnavailable(record[1])
            return True, None
        self._failures.pop(file_id, None)
        return False, None

    def _remember_failure(self, file_id, code=None):
        self._failures[file_id] = (self.clock() + FAILURE_TTL_SECONDS, code)
        self._failures.move_to_end(file_id)
        while len(self._failures) > MAX_CACHE_ENTRIES:
            self._failures.popitem(last=False)

    def get(self, file_id, fetcher=None):
        fetcher = fetcher or self.fetcher
        if fetcher is None:
            raise ValueError("thumbnail fetcher missing")
        with self._lock:
            cached = self._cached(file_id)
            if cached:
                return cached
            failed, value = self._failed(file_id)
            if failed:
                return value
            event = self._inflight.get(file_id)
            leader = event is None
            if leader:
                event = threading.Event()
                self._inflight[file_id] = event
        if not leader:
            if not event.wait(FETCH_TIMEOUT_SECONDS + 7):
                raise ThumbnailUnavailable("THUMBNAIL_INFLIGHT_TIMEOUT")
            with self._lock:
                cached = self._cached(file_id)
                failed, value = self._failed(file_id)
            if cached:
                return cached
            if failed:
                return value
            raise ThumbnailUnavailable("THUMBNAIL_NOT_AVAILABLE")
        try:
            value = fetcher(file_id)
            if value is None:
                with self._lock:
                    self._remember_failure(file_id)
                    return self._fallback(file_id)
            if value and len(value.content) <= MAX_IMAGE_BYTES:
                with self._lock:
                    self._failures.pop(file_id, None)
                    old = self._entries.pop(file_id, None)
                    if old:
                        self._bytes -= len(old[0].content)
                    self._entries[file_id] = (value, self.clock() + CACHE_TTL_SECONDS)
                    self._bytes += len(value.content)
                    while len(self._entries) > MAX_CACHE_ENTRIES or self._bytes > MAX_CACHE_BYTES:
                        _, evicted = self._entries.popitem(last=False)
                        self._bytes -= len(evicted[0].content)
            return value
        except Exception as exc:
            code = str(exc) if isinstance(exc, ThumbnailUnavailable) else 'THUMBNAIL_REMOTE_FAILURE'
            with self._lock:
                self._remember_failure(file_id, code)
                fallback = self._fallback(file_id) if code in TRANSIENT_FAILURES else None
            if fallback:
                return fallback
            raise ThumbnailUnavailable(code) from exc
        finally:
            with self._lock:
                self._inflight.pop(file_id).set()

    @property
    def size(self):
        with self._lock:
            return len(self._entries), self._bytes


class PublisherThumbnailService:
    def __init__(self, *, config=None, provider_factory=None, image_fetcher=fetch_image, cache=None):
        self.config = config or DriveInventoryConfig.from_env()
        self.provider_factory = provider_factory or GoogleApiDriveInventoryProvider.from_config
        self.image_fetcher = image_fetcher
        self.cache = cache or ThumbnailCache()
        self._provider = None
        self._provider_lock = threading.Lock()
        self._metadata_lock = threading.Lock()

    def _service(self):
        with self._provider_lock:
            if self._provider is None:
                self._provider = self.provider_factory(self.config, scopes=("https://www.googleapis.com/auth/drive",))
            return self._provider._service

    def _local_video(self, conn, file_id):
        if not FILE_ID_PATTERN.fullmatch(file_id) or not self.config.active_profile_folder_id:
            return False
        row = conn.execute("""SELECT df.file_kind,df.mime_type,df.name FROM drive_files df
            JOIN drive_folders f ON f.drive_folder_id=df.drive_folder_id
            WHERE df.drive_file_id=? AND df.missing_since IS NULL AND f.operational_status='ACTIVE'
              AND EXISTS (WITH RECURSIVE ancestors(id,parent_id) AS (
                SELECT drive_folder_id,parent_drive_folder_id FROM drive_folders WHERE drive_folder_id=f.drive_folder_id
                UNION ALL SELECT parent.drive_folder_id,parent.parent_drive_folder_id
                  FROM drive_folders parent JOIN ancestors a ON parent.drive_folder_id=a.parent_id
              ) SELECT 1 FROM ancestors WHERE id=?)""", (file_id, self.config.active_profile_folder_id)).fetchone()
        return bool(row and (row[0] == "VIDEO" or str(row[1] or "").lower().startswith("video/")))

    def get(self, conn, file_id: str) -> Thumbnail | None:
        if not self._local_video(conn, file_id):
            raise ThumbnailUnavailable("FILE_NOT_IN_PUBLISHER_SCOPE")

        def fetch(key):
            self.cache.metadata_reads += 1
            with self._metadata_lock:
                metadata = self._service().files().get(
                    fileId=key, fields="id,mimeType,thumbnailLink,modifiedTime",
                    supportsAllDrives=True).execute(num_retries=0)
            if metadata.get("id") != key or not str(metadata.get("mimeType") or "").startswith("video/"):
                raise ThumbnailUnavailable("REMOTE_MEDIA_IDENTITY_MISMATCH")
            url = metadata.get("thumbnailLink")
            if not url:
                return None
            self.cache.image_fetches += 1
            return self.image_fetcher(url)

        try:
            return self.cache.get(file_id, fetch)
        except ThumbnailUnavailable:
            raise
        except Exception as exc:
            raise ThumbnailUnavailable("THUMBNAIL_REMOTE_FAILURE") from exc
