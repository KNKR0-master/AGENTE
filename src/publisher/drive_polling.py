"""Opt-in, scoped scheduler for the existing incremental Drive sync."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.drive_inventory import DriveInventoryConfig
from src.publisher.drive_sync import configured_scope, incremental_drive_sync
from src.store import connect

MIN_INTERVAL = 30
DEFAULT_INTERVAL = 300
MAX_BACKOFF = 900


def _utcnow():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.isoformat()


def _scope(config=None):
    return configured_scope(config or DriveInventoryConfig.from_env())


def _row(conn, scope):
    return conn.execute(
        """SELECT * FROM publisher_drive_poll_state
           WHERE account_identity=? AND provider=? AND root_folder_id=?""",
        (scope.account_identity, scope.provider, scope.root_folder_id),
    ).fetchone()


def polling_status(conn, *, config=None):
    scope = _scope(config)
    row = _row(conn, scope)
    fields = ("enabled", "interval_seconds", "status", "last_attempt_at", "last_success_at",
              "next_attempt_at", "consecutive_failures", "paused_reason", "last_error_category",
              "last_changes_seen")
    if row is None:
        cursor = _cursor_status(conn, scope)
        return {"enabled": True, "interval_seconds": DEFAULT_INTERVAL,
                "status": "SCHEDULED" if cursor == "READY" else "PAUSED",
                "last_attempt_at": None, "last_success_at": None, "next_attempt_at": None,
                "consecutive_failures": 0, "paused_reason": None if cursor == "READY" else cursor,
                "last_error_category": None,
                "last_changes_seen": 0}
    result = {key: row[key] for key in fields}
    result["enabled"] = bool(result["enabled"])
    return result


def _cursor_status(conn, scope):
    row = conn.execute(
        """SELECT cursor_status,current_cursor FROM publisher_drive_sync_state
           WHERE account_identity=? AND provider=? AND root_folder_id=?""",
        (scope.account_identity, scope.provider, scope.root_folder_id),
    ).fetchone()
    return "READY" if row and row["cursor_status"] == "READY" and row["current_cursor"] else (
        "UNINITIALIZED" if row and row["cursor_status"] == "READY" else row["cursor_status"] if row else "UNINITIALIZED"
    )


def configure_polling(conn, action, interval_seconds=None, *, now=None, config=None):
    if action not in {"enable", "disable", "set_interval"}:
        raise ValueError("INVALID_POLL_ACTION")
    if action == "set_interval" and interval_seconds is None:
        raise ValueError("POLL_INTERVAL_REQUIRED")
    if interval_seconds is not None and (type(interval_seconds) is not int or interval_seconds < MIN_INTERVAL):
        raise ValueError("POLL_INTERVAL_TOO_SHORT")
    scope = _scope(config)
    current = polling_status(conn, config=config)
    interval = interval_seconds if interval_seconds is not None else current["interval_seconds"]
    timestamp = _stamp(now or _utcnow())
    enabled = action != "disable" if action != "set_interval" else current["enabled"]
    cursor = _cursor_status(conn, scope) if enabled else None
    status = "SCHEDULED" if enabled and cursor == "READY" else "PAUSED" if enabled else "DISABLED"
    reason = None if status != "PAUSED" else cursor
    if action == "set_interval" and current["status"] == "PAUSED" and current["paused_reason"] in {
        "AUTH_REQUIRED", "ACCOUNT_MISMATCH", "DATABASE_ERROR", "SYNC_ERROR"
    }:
        status, reason = "PAUSED", current["paused_reason"]
    next_at = _stamp((now or _utcnow()) + timedelta(seconds=interval)) if status == "SCHEDULED" else None
    with conn:
        conn.execute(
            """INSERT INTO publisher_drive_poll_state
               (account_identity,provider,root_folder_id,enabled,interval_seconds,status,next_attempt_at,
                paused_reason,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(account_identity,provider,root_folder_id) DO UPDATE SET
               enabled=excluded.enabled,interval_seconds=excluded.interval_seconds,status=excluded.status,
               next_attempt_at=excluded.next_attempt_at,paused_reason=excluded.paused_reason,
               consecutive_failures=0,last_error_category=NULL,updated_at=excluded.updated_at""",
            (scope.account_identity, scope.provider, scope.root_folder_id, int(enabled), interval,
             status, next_at, reason, timestamp, timestamp),
        )
    return polling_status(conn, config=config)


class DrivePollCoordinator:
    _active = set()
    _active_guard = threading.Lock()

    def __init__(self, db, *, config=None, clock=_utcnow, sync=incremental_drive_sync):
        self.db = db
        self.config = config
        self.clock = clock
        self.sync = sync
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._guard = threading.Lock()
        self._cycle_guard = threading.Lock()
        self._key = None

    def start(self):
        with self._guard:
            if self._thread and self._thread.is_alive():
                return False
            if self.config is None:
                self.config = DriveInventoryConfig.from_env()
            scope = _scope(self.config)
            key = (str(Path(self.db).resolve()), scope)
            with self._active_guard:
                if key in self._active:
                    return False
                self._active.add(key)
                self._key = key
            self._stop.clear()
            try:
                with closing(connect(self.db)) as conn:
                    state = polling_status(conn, config=self.config)
                    if state["enabled"] and state["status"] not in {"PAUSED", "BACKOFF"}:
                        configure_polling(conn, "enable", state["interval_seconds"],
                                          now=self.clock(), config=self.config)
                        # The same scheduled cycle performs startup catch-up before the 5-minute cadence.
                        self._update(conn, scope, next_attempt_at=_stamp(self.clock()))
                self._thread = threading.Thread(target=self._run, name="publisher-drive-poll", daemon=True)
                self._thread.start()
            except Exception:
                with self._active_guard:
                    self._active.discard(key)
                self._key = None
                raise
            return True

    def wake(self):
        self._wake.set()

    def stop(self, timeout=20):
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        stopped = not thread or not thread.is_alive()
        if stopped:
            if self._cycle_guard.acquire(timeout=timeout):
                self._cycle_guard.release()
            else:
                stopped = False
        if stopped and self._key is not None:
            with self._active_guard:
                self._active.discard(self._key)
            self._key = None
        return stopped

    def _run(self):
        while not self._stop.is_set():
            try:
                with closing(connect(self.db)) as conn:
                    state = polling_status(conn, config=self.config)
                due = datetime.fromisoformat(state["next_attempt_at"]) if state["next_attempt_at"] else None
                if state["enabled"] and due and state["status"] in {"SCHEDULED", "BACKOFF"}:
                    if due <= self.clock() and not self._stop.is_set():
                        self.poll_due_once()
                        continue
                    delay = max(0.1, min(30, (due - self.clock()).total_seconds()))
                else:
                    delay = 30
                self._wake.wait(delay)
                self._wake.clear()
            except sqlite3.Error:
                self._pause_database_error()
                self._wake.wait(30)
                self._wake.clear()
            except ValueError:
                self._pause_local_error()
                self._wake.wait(30)
                self._wake.clear()

    def _pause_database_error(self):
        try:
            with closing(connect(self.db)) as conn:
                self._update(conn, _scope(self.config), status="PAUSED",
                             paused_reason="DATABASE_ERROR", last_error_category="DATABASE_ERROR",
                             next_attempt_at=None)
        except sqlite3.Error:
            pass

    def _pause_local_error(self):
        try:
            with closing(connect(self.db)) as conn:
                self._update(conn, _scope(self.config), status="PAUSED",
                             paused_reason="SYNC_ERROR", last_error_category="SYNC_ERROR",
                             next_attempt_at=None)
        except sqlite3.Error:
            pass

    def poll_due_once(self):
        with self._cycle_guard:
            if self._stop.is_set():
                return "STOPPING"
            return self._poll_due_once_locked()

    def _poll_due_once_locked(self):
        now = self.clock()
        scope = _scope(self.config)
        with closing(connect(self.db)) as conn:
            state = polling_status(conn, config=self.config)
            if not state["enabled"] or state["status"] not in {"SCHEDULED", "BACKOFF"}:
                return "NOT_SCHEDULED"
            if not state["next_attempt_at"] or datetime.fromisoformat(state["next_attempt_at"]) > now:
                return "NOT_DUE"
            try:
                cursor = _cursor_status(conn, scope)
                if cursor != "READY":
                    self._update(conn, scope, status="PAUSED", paused_reason=cursor,
                                 next_attempt_at=None)
                    return "CURSOR_NOT_READY"
                if self._stop.is_set():
                    return "STOPPING"
                self._update(conn, scope, status="RUNNING", last_attempt_at=_stamp(now))
                result = self.sync(conn, bootstrap=False)
            except Exception as exc:
                category = self._error_category(exc)
                if not polling_status(conn, config=self.config)["enabled"]:
                    return "DISABLED_DURING_CYCLE"
                self._update(conn, scope, status="PAUSED", paused_reason=category,
                             last_error_category=category, next_attempt_at=None)
                return category
            if not polling_status(conn, config=self.config)["enabled"]:
                return "DISABLED_DURING_CYCLE"
            status = result.get("status")
            if status == "SYNC_IN_PROGRESS":
                self._update(conn, scope, status="SCHEDULED", paused_reason=None,
                             next_attempt_at=_stamp(now + timedelta(seconds=state["interval_seconds"])))
                return "POLL_SKIPPED_SYNC_ALREADY_RUNNING"
            if status == "READY":
                self._update(conn, scope, status="SCHEDULED", last_success_at=_stamp(now),
                             next_attempt_at=_stamp(now + timedelta(seconds=state["interval_seconds"])),
                             consecutive_failures=0, paused_reason=None, last_error_category=None,
                             last_changes_seen=int(result.get("changes_seen") or 0))
                return "SUCCESS"
            if status == "TRANSIENT_ERROR":
                failures = state["consecutive_failures"] + 1
                backoff = min(MAX_BACKOFF, state["interval_seconds"] * (2 ** min(failures, 10)))
                self._update(conn, scope, status="BACKOFF", consecutive_failures=failures,
                             next_attempt_at=_stamp(now + timedelta(seconds=backoff)),
                             last_error_category="TRANSIENT_DRIVE_ERROR")
                return "BACKOFF"
            if status == "AUTH_REQUIRED":
                self._update(conn, scope, status="PAUSED", paused_reason="AUTH_REQUIRED",
                             last_error_category="AUTH_REQUIRED", next_attempt_at=None)
                return "AUTH_REQUIRED"
            reason = "FULL_SCAN_REQUIRED" if status == "FULL_SCAN_REQUIRED" else "SYNC_ERROR"
            self._update(conn, scope, status="PAUSED", paused_reason=reason,
                         last_error_category=reason, next_attempt_at=None)
            return reason

    @staticmethod
    def _error_category(exc):
        text = str(exc).upper()
        if "ACCOUNT_MISMATCH" in text:
            return "ACCOUNT_MISMATCH"
        if "CREDENTIAL" in text or "CONSENT" in text or "INVALID_GRANT" in text:
            return "AUTH_REQUIRED"
        if isinstance(exc, sqlite3.Error):
            return "DATABASE_ERROR"
        return "SYNC_ERROR"

    @staticmethod
    def _update(conn, scope, **fields):
        fields["updated_at"] = _stamp(_utcnow())
        with conn:
            conn.execute(
                f"UPDATE publisher_drive_poll_state SET {','.join(key + '=?' for key in fields)} "
                "WHERE account_identity=? AND provider=? AND root_folder_id=?",
                (*fields.values(), scope.account_identity, scope.provider, scope.root_folder_id),
            )
