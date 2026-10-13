from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from contextlib import nullcontext

from src.store import now_iso


NORMAL = "NORMAL"
PROTECTION_STATES = {
    NORMAL,
    "RATE_LIMITED",
    "POSTING_LIMIT_REACHED",
    "SPAM_RISK",
    "POSTING_BANNED",
    "MANUAL_REVIEW_REQUIRED",
}
BLOCKED_BY_ACCOUNT_PROTECTION = "BLOCKED_BY_ACCOUNT_PROTECTION"

# These rules intentionally match known provider messages only. Generic technical
# failures must never be promoted to account-risk evidence.
_SIGNALS = (
    ("RATE_LIMITED", "RATE_LIMIT_EXCEEDED", ("rate_limit_exceeded", "too many requests")),
    ("POSTING_LIMIT_REACHED", "POSTING_LIMIT_REACHED", ("spam_risk_too_many_posts",)),
    ("SPAM_RISK", "TOO_MANY_PENDING_SHARES", ("spam_risk_too_many_pending_share",)),
    ("SPAM_RISK", "SPAM_RISK", ("spam_risk", "spam_risk_text")),
    ("POSTING_BANNED", "ACCOUNT_POSTING_BANNED", ("account posting ban", "posting banned")),
)


class AccountProtectionBlocked(RuntimeError):
    def __init__(self, state, reason_code, message="Conta protegida; revise antes de continuar"):
        super().__init__(message)
        self.state = state
        self.reason_code = reason_code
        self.code = BLOCKED_BY_ACCOUNT_PROTECTION


@dataclass(frozen=True)
class RiskSignal:
    state: str
    reason_code: str


def map_risk_signal(value) -> RiskSignal | None:
    text = " ".join(str(value or "").casefold().split())
    if not text or any(term in text for term in (
        "targetclosed", "target closed", "timeout", "ffprobe", "google drive",
        "drive_folder_scan_failed", "networkerror", "connection reset",
    )):
        return None
    for state, reason, markers in _SIGNALS:
        if any(marker in text for marker in markers):
            return RiskSignal(state, reason)
    return None


def init_account_safety(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS account_protection (
      account_id INTEGER PRIMARY KEY REFERENCES publisher_accounts(id),
      state TEXT NOT NULL DEFAULT 'NORMAL', reason_code TEXT,
      source TEXT NOT NULL DEFAULT 'SYSTEM_RECONCILIATION',
      activated_at TEXT, cooldown_until TEXT, reviewed_at TEXT,
      max_mutations_per_day INTEGER, max_mutations_per_window INTEGER,
      window_minutes INTEGER, max_duration_seconds REAL,
      max_file_size_bytes INTEGER, min_fps REAL, max_fps REAL,
      updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS account_protection_events (
      id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES publisher_accounts(id),
      from_state TEXT NOT NULL, to_state TEXT NOT NULL, reason_code TEXT NOT NULL,
      source TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS account_mutation_ledger (
      id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES publisher_accounts(id),
      mutation_key TEXT NOT NULL, mutation_type TEXT NOT NULL, draft_id INTEGER,
      status TEXT NOT NULL DEFAULT 'STARTED', started_at TEXT NOT NULL, completed_at TEXT,
      UNIQUE(account_id, mutation_key));
    CREATE INDEX IF NOT EXISTS account_protection_events_account
      ON account_protection_events(account_id, created_at);
    CREATE INDEX IF NOT EXISTS account_mutation_ledger_account
      ON account_mutation_ledger(account_id, started_at);
    CREATE TRIGGER IF NOT EXISTS account_protection_after_account_insert
    AFTER INSERT ON publisher_accounts
    BEGIN
      INSERT OR IGNORE INTO account_protection(account_id,state,source,updated_at)
      VALUES (NEW.id,'NORMAL','SYSTEM_RECONCILIATION',CURRENT_TIMESTAMP);
    END;
    """)
    timestamp = now_iso()
    conn.execute("""INSERT OR IGNORE INTO account_protection(account_id,state,source,updated_at)
      SELECT id,'NORMAL','SYSTEM_RECONCILIATION',? FROM publisher_accounts""", (timestamp,))


def _parse_time(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _safe_evidence(evidence):
    if not evidence:
        return "{}"
    allowed = {}
    for key in ("http_status", "provider_code", "ui_marker", "operation", "draft_id"):
        if key in evidence and evidence[key] is not None:
            allowed[key] = re.sub(r"[^\w .:/-]", "?", str(evidence[key]))[:240]
    return json.dumps(allowed, ensure_ascii=True, sort_keys=True)


class AccountSafetyGuard:
    def __init__(self, conn):
        self.conn = conn

    def snapshot(self, account_id):
        row = self.conn.execute("SELECT * FROM account_protection WHERE account_id=?", (int(account_id),)).fetchone()
        if not row:
            raise AccountProtectionBlocked("MANUAL_REVIEW_REQUIRED", "PROTECTION_STATE_UNAVAILABLE")
        return dict(row)

    def activate(self, account_id, state, reason_code, source, *, evidence=None, cooldown_until=None):
        if state not in PROTECTION_STATES - {NORMAL}:
            raise ValueError("Estado de protecao invalido")
        if source not in {"OBSERVED_TIKTOK_UI", "LOCAL_ACTIVITY_BUDGET", "OPERATOR", "SYSTEM_RECONCILIATION"}:
            raise ValueError("Fonte de protecao invalida")
        current = self.snapshot(account_id)
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("""UPDATE account_protection SET state=?,reason_code=?,source=?,activated_at=?,
              cooldown_until=?,reviewed_at=NULL,updated_at=? WHERE account_id=?""",
              (state, reason_code, source, timestamp, cooldown_until, timestamp, int(account_id)))
            self.conn.execute("""INSERT INTO account_protection_events
              (account_id,from_state,to_state,reason_code,source,evidence_json,created_at)
              VALUES (?,?,?,?,?,?,?)""", (int(account_id), current["state"], state, reason_code,
              source, _safe_evidence(evidence), timestamp))
        return self.snapshot(account_id)

    def clear_manual(self, account_id, *, human_confirmed, reason):
        if human_confirmed is not True or not str(reason or "").strip():
            raise ValueError("Confirmacao humana e motivo sao obrigatorios")
        current = self.snapshot(account_id)
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("""UPDATE account_protection SET state='NORMAL',reason_code=NULL,
              source='OPERATOR',activated_at=NULL,cooldown_until=NULL,reviewed_at=?,updated_at=? WHERE account_id=?""",
              (timestamp, timestamp, int(account_id)))
            self.conn.execute("""INSERT INTO account_protection_events
              (account_id,from_state,to_state,reason_code,source,evidence_json,created_at)
              VALUES (?,?,?,?,?,?,?)""", (int(account_id), current["state"], NORMAL, "MANUAL_CLEAR",
              "OPERATOR", _safe_evidence({"operation": reason}), timestamp))
        return self.snapshot(account_id)

    def assert_allowed(self, account_id, *, now=None):
        current = self.snapshot(account_id)
        moment = now or datetime.now(timezone.utc)
        cooldown = _parse_time(current.get("cooldown_until"))
        if current["state"] != NORMAL:
            if cooldown and cooldown <= moment and current["source"] in {"OBSERVED_TIKTOK_UI", "LOCAL_ACTIVITY_BUDGET"}:
                self.clear_expired(account_id, current)
                current = self.snapshot(account_id)
            else:
                raise AccountProtectionBlocked(current["state"], current.get("reason_code") or "ACCOUNT_PROTECTED")
        self._assert_budget(account_id, current, moment)
        return current

    def clear_expired(self, account_id, current=None):
        current = current or self.snapshot(account_id)
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("""UPDATE account_protection SET state='NORMAL',reason_code=NULL,
              activated_at=NULL,cooldown_until=NULL,updated_at=? WHERE account_id=?""", (timestamp, int(account_id)))
            self.conn.execute("""INSERT INTO account_protection_events
              (account_id,from_state,to_state,reason_code,source,evidence_json,created_at)
              VALUES (?,?,?,?,?,'{}',?)""", (int(account_id), current["state"], NORMAL,
              "COOLDOWN_EXPIRED", "SYSTEM_RECONCILIATION", timestamp))

    def _assert_budget(self, account_id, config, moment):
        checks = []
        if config.get("max_mutations_per_day") is not None:
            day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
            checks.append((int(config["max_mutations_per_day"]), day_start, "DAILY_MUTATION_BUDGET_REACHED"))
        if config.get("max_mutations_per_window") is not None and config.get("window_minutes"):
            start = (moment - timedelta(minutes=int(config["window_minutes"]))).isoformat()
            checks.append((int(config["max_mutations_per_window"]), start, "ROLLING_MUTATION_BUDGET_REACHED"))
        for limit, since, reason in checks:
            count = self.conn.execute("""SELECT COUNT(*) FROM account_mutation_ledger
              WHERE account_id=? AND started_at>=?""", (int(account_id), since)).fetchone()[0]
            if count >= limit:
                self.activate(account_id, "POSTING_LIMIT_REACHED", reason, "LOCAL_ACTIVITY_BUDGET")
                raise AccountProtectionBlocked("POSTING_LIMIT_REACHED", reason)

    def start_mutation(self, account_id, mutation_key, mutation_type, *, draft_id=None, pre_mutation_guard=None):
        self.assert_allowed(account_id)
        owns_transaction = not self.conn.in_transaction
        with self.conn if owns_transaction else nullcontext():
            if owns_transaction:
                self.conn.execute('BEGIN IMMEDIATE')
            existing = self.conn.execute("""SELECT * FROM account_mutation_ledger
              WHERE account_id=? AND mutation_key=?""", (int(account_id), str(mutation_key))).fetchone()
            if existing:
                raise AccountProtectionBlocked("MANUAL_REVIEW_REQUIRED", "DUPLICATE_MUTATION_KEY",
                                               "Mutacao ja registrada; reconciliacao obrigatoria")
            if pre_mutation_guard is not None:
                pre_mutation_guard()
            self.conn.execute("""INSERT INTO account_mutation_ledger
              (account_id,mutation_key,mutation_type,draft_id,status,started_at)
              VALUES (?,?,?,?, 'STARTED',?)""", (int(account_id), str(mutation_key), mutation_type,
              int(draft_id) if draft_id is not None else None, now_iso()))

    def complete_mutation(self, account_id, mutation_key, status="CONFIRMED"):
        with self.conn:
            changed = self.conn.execute("""UPDATE account_mutation_ledger SET status=?,completed_at=?
              WHERE account_id=? AND mutation_key=? AND status='STARTED'""",
              (status, now_iso(), int(account_id), str(mutation_key))).rowcount
        return bool(changed)

    def validate_media(self, account_id, metadata):
        config = self.snapshot(account_id)
        checks = (
            ("max_duration_seconds", metadata.get("duration"), lambda value, limit: value <= limit, "MEDIA_DURATION_LIMIT"),
            ("max_file_size_bytes", metadata.get("file_size"), lambda value, limit: value <= limit, "MEDIA_SIZE_LIMIT"),
            ("min_fps", metadata.get("fps"), lambda value, limit: value >= limit, "MEDIA_FPS_BELOW_MINIMUM"),
            ("max_fps", metadata.get("fps"), lambda value, limit: value <= limit, "MEDIA_FPS_ABOVE_MAXIMUM"),
        )
        for field, value, predicate, code in checks:
            limit = config.get(field)
            if limit is not None and (value is None or not predicate(float(value), float(limit))):
                raise ValueError(code)
        return True
