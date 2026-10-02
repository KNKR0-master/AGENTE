from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Callable

from src.drive_inventory import (
    DriveEntry,
    DriveInventoryConfig,
    DriveInventoryError,
    DriveInventoryScanner,
    FOLDER_MIME_TYPE,
    GoogleApiDriveInventoryProvider,
    VideoPublicationEligibilityService,
    _file_kind,
    _upsert_drive_file,
    mark_remote_unavailable,
)
from src.publisher.folder_catalog import sync_drive_product_name
from src.store import now_iso


PROVIDER = "google_drive"
READY = "READY"
FULL_SCAN_REQUIRED = "FULL_SCAN_REQUIRED"


@dataclass(frozen=True)
class SyncScope:
    account_identity: str
    provider: str
    root_folder_id: str


_scope_locks: dict[SyncScope, Lock] = {}
_scope_locks_guard = Lock()


def configured_scope(config: DriveInventoryConfig) -> SyncScope:
    account = (config.expected_google_account or config.account_email or "").strip().lower()
    root = config.active_profile_folder_id or config.root_folder_id
    if not account or not root:
        raise ValueError("DRIVE_SYNC_SCOPE_INCOMPLETE")
    return SyncScope(account, PROVIDER, root)


def get_sync_state(conn, scope: SyncScope) -> dict[str, Any]:
    row = conn.execute(
        """SELECT * FROM publisher_drive_sync_state
           WHERE account_identity=? AND provider=? AND root_folder_id=?""",
        (scope.account_identity, scope.provider, scope.root_folder_id),
    ).fetchone()
    if row:
        return dict(row)
    timestamp = now_iso()
    with conn:
        conn.execute(
            """INSERT OR IGNORE INTO publisher_drive_sync_state
               (account_identity,provider,root_folder_id,cursor_status,created_at,updated_at)
               VALUES (?,?,?,'UNINITIALIZED',?,?)""",
            (scope.account_identity, scope.provider, scope.root_folder_id, timestamp, timestamp),
        )
    return dict(conn.execute(
        """SELECT * FROM publisher_drive_sync_state
           WHERE account_identity=? AND provider=? AND root_folder_id=?""",
        (scope.account_identity, scope.provider, scope.root_folder_id),
    ).fetchone())


def project_drive_inventory_to_publisher_media(conn, account_identity: str = "cecilia") -> int:
    timestamp = now_iso()
    rows = conn.execute(
        """SELECT df.*,f.relative_path FROM drive_files df
           JOIN drive_folders f ON f.drive_folder_id=df.drive_folder_id
           WHERE df.missing_since IS NULL AND f.operational_status='ACTIVE'
             AND (df.file_kind='VIDEO' OR LOWER(COALESCE(df.mime_type,'')) LIKE 'video/%')"""
    ).fetchall()
    updates = 0
    present: set[str] = set()
    for source in rows:
        item = dict(source)
        present.add(item["drive_file_id"])
        before = conn.execute(
            """SELECT id,status FROM publisher_media_assets
               WHERE provider=? AND source_account=? AND remote_file_id=?""",
            (PROVIDER, account_identity, item["drive_file_id"]),
        ).fetchone()
        conn.execute(
            """INSERT INTO publisher_media_assets
               (provider,source_account,remote_file_id,remote_folder_id,filename,mime_type,
                size_bytes,checksum,folder_path,web_view_link,remote_created_at,
                remote_modified_at,last_seen_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(provider,source_account,remote_file_id) DO UPDATE SET
                 remote_folder_id=excluded.remote_folder_id,
                 filename=CASE WHEN publisher_media_assets.status IN ('AVAILABLE','MISSING')
                               THEN excluded.filename ELSE publisher_media_assets.filename END,
                 mime_type=excluded.mime_type,
                 size_bytes=CASE WHEN publisher_media_assets.status IN ('AVAILABLE','MISSING')
                                 THEN excluded.size_bytes ELSE publisher_media_assets.size_bytes END,
                 checksum=CASE WHEN publisher_media_assets.status IN ('AVAILABLE','MISSING')
                               THEN excluded.checksum ELSE publisher_media_assets.checksum END,
                 folder_path=excluded.folder_path,web_view_link=excluded.web_view_link,
                 remote_modified_at=excluded.remote_modified_at,last_seen_at=excluded.last_seen_at,
                 status=CASE WHEN publisher_media_assets.status='MISSING' THEN 'AVAILABLE'
                             ELSE publisher_media_assets.status END,
                 updated_at=excluded.updated_at""",
            (PROVIDER, account_identity, item["drive_file_id"], item["drive_folder_id"],
             item["name"], item.get("mime_type") or "application/octet-stream", item.get("size"),
             item.get("md5_checksum"), item.get("relative_path"), item.get("web_view_link"),
             item.get("created_time"), item.get("modified_time"), timestamp, timestamp, timestamp),
        )
        updates += int(before is None or before["status"] == "MISSING")
    if present:
        marks = ",".join("?" for _ in present)
        cursor = conn.execute(
            f"""UPDATE publisher_media_assets SET status='MISSING',updated_at=?
                WHERE provider=? AND source_account=? AND status='AVAILABLE'
                  AND remote_file_id NOT IN ({marks})""",
            (timestamp, PROVIDER, account_identity, *sorted(present)),
        )
    else:
        cursor = conn.execute(
            """UPDATE publisher_media_assets SET status='MISSING',updated_at=?
               WHERE provider=? AND source_account=? AND status='AVAILABLE'""",
            (timestamp, PROVIDER, account_identity),
        )
    return updates + max(0, cursor.rowcount)


class IncrementalDriveSync:
    def __init__(self, conn, provider, config: DriveInventoryConfig):
        self.conn = conn
        self.provider = provider
        self.config = config
        self.scope = configured_scope(config)
        self.eligibility = VideoPublicationEligibilityService()

    def cycle(self) -> dict[str, Any]:
        state = get_sync_state(self.conn, self.scope)
        if state["cursor_status"] != READY or not state.get("current_cursor"):
            return self._summary(state["cursor_status"], full_scan_required=True)
        return self._consume(state["current_cursor"], mode="INCREMENTAL")

    def bootstrap(self, scanner: Callable[[], dict[str, Any]] | None = None) -> dict[str, Any]:
        start_token = self.provider.get_start_page_token()
        state = get_sync_state(self.conn, self.scope)
        timestamp = now_iso()
        with self.conn:
            self.conn.execute(
                """UPDATE publisher_drive_sync_state SET current_cursor=?,cursor_status='UNINITIALIZED',
                   last_error=NULL,updated_at=? WHERE id=?""",
                (start_token, timestamp, state["id"]),
            )
        result = scanner() if scanner else DriveInventoryScanner(self.provider, self.config).scan(self.conn, force=True)
        if result.get("status") != "COMPLETE":
            self._set_failure(FULL_SCAN_REQUIRED, "FULL_SCAN_INCOMPLETE")
            return self._summary(FULL_SCAN_REQUIRED, full_scan_required=True)
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drive_sync_state SET last_full_scan_at=?,updated_at=? WHERE id=?",
                (now_iso(), now_iso(), state["id"]),
            )
        return self._consume(start_token, mode="FULL")

    def _consume(self, cursor: str, *, mode: str) -> dict[str, Any]:
        totals = self._summary(READY)
        while cursor:
            try:
                page = self.provider.list_changes(cursor)
            except Exception as exc:
                if self._auth_provider_error(exc):
                    return {**totals, "status": "AUTH_REQUIRED", "cursor_status": READY,
                            "full_scan_required": False}
                if self._transient_provider_error(exc):
                    with self.conn:
                        self.conn.execute(
                            """UPDATE publisher_drive_sync_state SET last_error='TRANSIENT_DRIVE_ERROR',updated_at=?
                               WHERE account_identity=? AND provider=? AND root_folder_id=?""",
                            (now_iso(), self.scope.account_identity, self.scope.provider, self.scope.root_folder_id),
                        )
                    return {**totals, "status": "TRANSIENT_ERROR", "cursor_status": READY,
                            "full_scan_required": False}
                status = FULL_SCAN_REQUIRED if self._invalid_cursor(exc) else "ERROR"
                self._set_failure(status, "CURSOR_INVALID" if status == FULL_SCAN_REQUIRED else type(exc).__name__)
                return {**totals, "status": status, "cursor_status": status,
                        "full_scan_required": status == FULL_SCAN_REQUIRED}
            next_cursor = page.get("next_page_token") or page.get("new_start_page_token")
            if not next_cursor:
                self._set_failure("ERROR", "CHANGE_PAGE_WITHOUT_CURSOR")
                return {**totals, "status": "ERROR", "cursor_status": "ERROR"}
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                for change in page.get("changes") or []:
                    applied = self._apply_change(change)
                    totals["changes_seen"] += 1
                    for key, value in applied.items():
                        totals[key] += value
                totals["projection_updates"] += project_drive_inventory_to_publisher_media(self.conn)
                finished = now_iso()
                self.conn.execute(
                    """UPDATE publisher_drive_sync_state SET current_cursor=?,cursor_status='READY',
                       last_successful_sync_at=?,last_sync_mode=?,last_error=NULL,updated_at=?
                       WHERE account_identity=? AND provider=? AND root_folder_id=?""",
                    (next_cursor, finished, mode, finished, self.scope.account_identity,
                     self.scope.provider, self.scope.root_folder_id),
                )
                self.conn.commit()
            except Exception as exc:
                self.conn.rollback()
                if isinstance(exc, DriveInventoryError) and str(exc) in {"MEMBERSHIP_UNKNOWN", "CHANGE_METADATA_MISSING"}:
                    with self.conn:
                        self.conn.execute(
                            """UPDATE publisher_drive_sync_state SET last_error=?,updated_at=?
                               WHERE account_identity=? AND provider=? AND root_folder_id=?""",
                            (str(exc), now_iso(), self.scope.account_identity,
                             self.scope.provider, self.scope.root_folder_id),
                        )
                    return {**totals, "status": "TRANSIENT_ERROR", "cursor_status": READY,
                            "full_scan_required": False}
                self._set_failure("ERROR", type(exc).__name__)
                return {**totals, "status": "ERROR", "cursor_status": "ERROR"}
            totals["pages_processed"] += 1
            cursor = page.get("next_page_token")
        totals.update(status="READY", cursor_status="READY", full_scan_required=False)
        return totals

    def _apply_change(self, change: dict[str, Any]) -> dict[str, int]:
        counters = {"changes_in_scope": 0, "files_upserted": 0, "folders_upserted": 0,
                    "items_marked_unavailable": 0}
        file_id = str(change.get("file_id") or "")
        if not file_id:
            raise ValueError("MALFORMED_CHANGE")
        existing_file = self.conn.execute("SELECT * FROM drive_files WHERE drive_file_id=?", (file_id,)).fetchone()
        existing_folder = self.conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id=?", (file_id,)).fetchone()
        if change.get("removed"):
            if existing_file or existing_folder:
                counters["changes_in_scope"] = 1
                counters["items_marked_unavailable"] = self._mark_unavailable(file_id, bool(existing_folder), "REMOVED")
            return counters
        if not change.get("file"):
            raise DriveInventoryError("CHANGE_METADATA_MISSING")
        payload = change["file"]
        if payload.get("trashed"):
            if existing_file or existing_folder:
                counters["changes_in_scope"] = 1
                counters["items_marked_unavailable"] = self._mark_unavailable(file_id, bool(existing_folder), "TRASHED")
            return counters
        membership = self._membership(file_id, payload, bool(existing_file or existing_folder))
        if membership == "MEMBERSHIP_UNKNOWN":
            raise DriveInventoryError("MEMBERSHIP_UNKNOWN")
        if membership in {"OUT_OF_SCOPE", "PREVIOUSLY_IN_SCOPE"}:
            if existing_file or existing_folder:
                counters["changes_in_scope"] = 1
                counters["items_marked_unavailable"] = self._mark_unavailable(file_id, bool(existing_folder), "OUTSIDE_ROOT")
            return counters
        counters["changes_in_scope"] = 1
        entry = DriveEntry.from_api(payload)
        if entry.mime_type == FOLDER_MIME_TYPE:
            self._upsert_folder(entry)
            counters["folders_upserted"] = 1
        else:
            self._upsert_file(entry)
            counters["files_upserted"] = 1
        return counters

    def _membership(self, file_id: str, payload: dict[str, Any], previously_known: bool) -> str:
        if file_id == self.scope.root_folder_id or any(
            self._folder_in_scope(parent) for parent in payload.get("parents") or []
        ):
            return "IN_SCOPE"
        if hasattr(self.provider, "is_within_root"):
            resolved = self.provider.is_within_root(file_id, self.scope.root_folder_id)
            if resolved is True:
                return "IN_SCOPE"
            if resolved is None:
                return "MEMBERSHIP_UNKNOWN"
        return "PREVIOUSLY_IN_SCOPE" if previously_known else "OUT_OF_SCOPE"

    def _folder_in_scope(self, folder_id: str) -> bool:
        current, seen = folder_id, set()
        while current and current not in seen:
            if current == self.scope.root_folder_id:
                return True
            seen.add(current)
            row = self.conn.execute(
                "SELECT parent_drive_folder_id,operational_status FROM drive_folders WHERE drive_folder_id=?",
                (current,),
            ).fetchone()
            if not row or row["operational_status"] != "ACTIVE":
                return False
            current = row["parent_drive_folder_id"]
        return False

    def _upsert_folder(self, entry: DriveEntry) -> None:
        parent = entry.parent_ids[0] if entry.parent_ids else None
        parent_row = self.conn.execute("SELECT relative_path,depth FROM drive_folders WHERE drive_folder_id=?", (parent,)).fetchone()
        prior = self.conn.execute("SELECT name FROM drive_folders WHERE drive_folder_id=?", (entry.file_id,)).fetchone()
        path = f"{parent_row['relative_path']}/{entry.name}" if parent_row else entry.name
        depth = int(parent_row["depth"] + 1) if parent_row else 0
        timestamp = now_iso()
        self.conn.execute(
            """INSERT INTO drive_folders
               (drive_folder_id,parent_drive_folder_id,name,relative_path,depth,folder_type,
                mapping_status,operational_status,role_source,role_review_status,
                auto_create_new_direct_children,scan_status,last_scanned_at,created_at,updated_at)
               VALUES (?,?,?,?,?,'UNCLASSIFIED','NEEDS_REVIEW','ACTIVE','HEURISTIC','PENDING',0,'SUCCESS',?,?,?)
               ON CONFLICT(drive_folder_id) DO UPDATE SET
                 parent_drive_folder_id=excluded.parent_drive_folder_id,name=excluded.name,
                 relative_path=excluded.relative_path,depth=excluded.depth,operational_status='ACTIVE',
                 scan_status='SUCCESS',scan_error=NULL,last_scanned_at=excluded.last_scanned_at,
                 updated_at=excluded.updated_at""",
            (entry.file_id, parent, entry.name, path, depth, timestamp, timestamp, timestamp),
        )
        if prior and prior["name"] != entry.name:
            sync_drive_product_name(self.conn, entry.file_id, entry.name)
        # Restoring a parent does not prove its previously unavailable children are back.
        self._refresh_descendant_paths(entry.file_id)

    def _upsert_file(self, entry: DriveEntry) -> None:
        parent = entry.parent_ids[0] if entry.parent_ids else None
        folder = self.conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id=?", (parent,)).fetchone()
        if not folder:
            raise ValueError("MEMBERSHIP_UNKNOWN")
        folder = dict(folder)
        classification = self.eligibility.classify(self.conn, entry, folder)
        _upsert_drive_file(self.conn, entry, folder, classification, None, file_kind=_file_kind(entry))

    def _mark_unavailable(self, file_id: str, is_folder: bool, reason: str) -> int:
        return mark_remote_unavailable(
            self.conn, file_id, is_folder=is_folder, reason=reason,
            stop_at_folder_id=self.scope.root_folder_id if is_folder and reason == "OUTSIDE_ROOT" else None,
        )

    def _refresh_descendant_paths(self, folder_id: str) -> None:
        queue = [folder_id]
        while queue:
            parent_id = queue.pop(0)
            parent = self.conn.execute("SELECT relative_path,depth FROM drive_folders WHERE drive_folder_id=?", (parent_id,)).fetchone()
            children = self.conn.execute("SELECT drive_folder_id,name FROM drive_folders WHERE parent_drive_folder_id=?", (parent_id,)).fetchall()
            for child in children:
                self.conn.execute(
                    "UPDATE drive_folders SET relative_path=?,depth=?,updated_at=? WHERE drive_folder_id=?",
                    (f"{parent['relative_path']}/{child['name']}", parent["depth"] + 1, now_iso(), child["drive_folder_id"]),
                )
                queue.append(child["drive_folder_id"])

    def _set_failure(self, status: str, error: str) -> None:
        with self.conn:
            self.conn.execute(
                """UPDATE publisher_drive_sync_state SET cursor_status=?,last_error=?,updated_at=?
                   WHERE account_identity=? AND provider=? AND root_folder_id=?""",
                (status, error, now_iso(), self.scope.account_identity,
                 self.scope.provider, self.scope.root_folder_id),
            )

    @staticmethod
    def _invalid_cursor(exc: Exception) -> bool:
        cause = getattr(exc, "__cause__", None)
        status = getattr(getattr(cause, "resp", None), "status", None)
        text = str(exc).lower()
        return status == 410 or "410 invalid page token" in text or "invalid change token" in text

    @staticmethod
    def _auth_provider_error(exc: Exception) -> bool:
        cause = getattr(exc, "__cause__", None) or exc
        status = getattr(getattr(cause, "resp", None), "status", None)
        text = str(exc).upper()
        return status == 401 or any(reason in text for reason in (
            "INVALID_GRANT", "CREDENTIAL_INVALID", "CREDENTIAL_REFRESH_FAILED", "CONSENT_REQUIRED"
        ))

    @staticmethod
    def _transient_provider_error(exc: Exception) -> bool:
        cause = getattr(exc, "__cause__", None) or exc
        status = getattr(getattr(cause, "resp", None), "status", None)
        if status in {429, 500, 502, 503, 504}:
            return True
        if status == 403:
            text = str(cause).lower()
            return any(reason in text for reason in ("ratelimitexceeded", "userratelimitexceeded", "quotaexceeded"))
        return isinstance(cause, (TimeoutError, ConnectionError))

    @staticmethod
    def _summary(status: str, *, full_scan_required: bool = False) -> dict[str, Any]:
        return {"status": status, "pages_processed": 0, "changes_seen": 0,
                "changes_in_scope": 0, "files_upserted": 0, "folders_upserted": 0,
                "items_marked_unavailable": 0, "projection_updates": 0,
                "cursor_status": status, "full_scan_required": full_scan_required}


def incremental_drive_sync(conn, *, bootstrap: bool = False) -> dict[str, Any]:
    config = DriveInventoryConfig.from_env()
    scope = configured_scope(config)
    with _scope_locks_guard:
        lock = _scope_locks.setdefault(scope, Lock())
    if not lock.acquire(blocking=False):
        return {"status": "SYNC_IN_PROGRESS", "cursor_status": "SYNC_IN_PROGRESS"}
    try:
        provider = GoogleApiDriveInventoryProvider.from_config(config)
        sync = IncrementalDriveSync(conn, provider, config)
        return sync.bootstrap() if bootstrap else sync.cycle()
    finally:
        lock.release()


def drive_sync_status(conn) -> dict[str, Any]:
    scope = configured_scope(DriveInventoryConfig.from_env())
    with _scope_locks_guard:
        lock = _scope_locks.get(scope)
        sync_in_progress = bool(lock and lock.locked())
    row = conn.execute(
        """SELECT cursor_status,last_successful_sync_at,last_full_scan_at,last_sync_mode,last_error
           FROM publisher_drive_sync_state
           WHERE account_identity=? AND provider=? AND root_folder_id=?""",
        (scope.account_identity, scope.provider, scope.root_folder_id),
    ).fetchone()
    result = dict(row) if row else {"cursor_status": "UNINITIALIZED", "last_successful_sync_at": None,
                                    "last_full_scan_at": None, "last_sync_mode": None, "last_error": None}
    return {**result, "sync_in_progress": sync_in_progress}
