from __future__ import annotations

import json
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path

from src.store import now_iso
from src.publisher.library import _classify, _raw_rows, get_library_items, ignore_library_group, list_library, list_library_groups, list_group_items, select_group_items, save_decision, set_product_overrides
from src.publisher.folder_catalog import (
    apply_bulk_role,
    decide_folder_role,
    grouped_folder_reviews,
    list_folder_reviews,
    reject_suggested_match,
    set_container_auto_create,
)
from src.publisher.account_safety import AccountSafetyGuard, AccountProtectionBlocked, init_account_safety
from src.publisher.queue_policy import batch_queue_capabilities, draft_queue_capabilities, draft_queue_presentation, queue_preparation_context, queue_preparation_diagnostic, local_cancel_blocker
from src.publisher.media_history import assert_media_history_allowed, MediaHistoryBlocked
from src.publisher.queue_presentation import (
    HIDDEN_ITEM_SQL, change_visibility, history_visibility_counts,
    init_queue_presentation, presentation_targets,
)
from src.publisher.reconciliation_evidence import (
    evaluate_reconciliation_evidence_validity, get_latest_reconciliation_evidence,
)
from src.publisher.batch_limits import MAX_DRAFTS_PER_BATCH, require_batch_size
from src.publisher.draft_listing import current_listing_summary, resolution_safety_summary
from src.publisher import headlines, headline_workflow


LEGACY_ERRORS = {"PRODUCT_NOT_FOUND", "PRODUCT_NOT_CONFIRMED"}
ERRORS = {"NEEDS_LOGIN", "CAPTION_MISSING", "UPLOAD_FAILED",
          "STUDIO_CHANGED", "MANUAL_ACTION_REQUIRED", "FAILED"}
EDITABLE = {"DRAFT", *ERRORS, *LEGACY_ERRORS}
STATUSES = ["DRAFT", "PREPARING", "READY_FOR_REVIEW", "SCHEDULED", "PUBLISHED", *sorted(ERRORS), *sorted(LEGACY_ERRORS), "ARCHIVED"]


class ProductFolderError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def init_publisher(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS publisher_accounts (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, browser_profile TEXT NOT NULL UNIQUE,
      active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_products (
      product_id INTEGER PRIMARY KEY REFERENCES products(id),
      account_id INTEGER REFERENCES publisher_accounts(id), active INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS publisher_preferences (
      id INTEGER PRIMARY KEY CHECK (id = 1),
      default_publisher_account_id INTEGER REFERENCES publisher_accounts(id),
      updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_presets (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, account_id INTEGER REFERENCES publisher_accounts(id),
      visibility TEXT NOT NULL DEFAULT 'PUBLIC', comments INTEGER NOT NULL DEFAULT 1,
      product_required INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_batches (
      id INTEGER PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'MANUAL', created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_drafts (
      id INTEGER PRIMARY KEY, batch_id INTEGER REFERENCES publisher_batches(id),
      product_id INTEGER NOT NULL REFERENCES products(id), account_id INTEGER NOT NULL REFERENCES publisher_accounts(id),
      preset_id INTEGER REFERENCES publisher_presets(id), video_path TEXT NOT NULL, filename TEXT NOT NULL,
      caption TEXT NOT NULL DEFAULT '', scheduled_at TEXT, status TEXT NOT NULL DEFAULT 'DRAFT',
      error_code TEXT, checklist_json TEXT NOT NULL DEFAULT '{}', last_execution_at TEXT,
      created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_execution_logs (
      id INTEGER PRIMARY KEY, draft_id INTEGER REFERENCES publisher_drafts(id), step TEXT NOT NULL,
      status TEXT NOT NULL, message TEXT NOT NULL, screenshot_path TEXT, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS publisher_drafts_queue ON publisher_drafts(status, account_id, batch_id);
    CREATE TABLE IF NOT EXISTS publisher_media_assets (
      id INTEGER PRIMARY KEY, provider TEXT NOT NULL DEFAULT 'google_drive',
      source_account TEXT NOT NULL DEFAULT 'cecilia', remote_file_id TEXT NOT NULL,
      remote_folder_id TEXT, filename TEXT NOT NULL, mime_type TEXT NOT NULL,
      size_bytes INTEGER, checksum TEXT, folder_path TEXT, web_view_link TEXT,
      remote_created_at TEXT, remote_modified_at TEXT, status TEXT NOT NULL DEFAULT 'AVAILABLE',
      consumed_by_draft_id INTEGER, remote_deleted_at TEXT, cleanup_error TEXT,
      last_seen_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
      UNIQUE(provider,source_account,remote_file_id));
    CREATE TABLE IF NOT EXISTS publisher_media_decisions (
      drive_file_id TEXT PRIMARY KEY, decision TEXT NOT NULL CHECK(decision IN ('VALID','IGNORED')),
      decided_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS publisher_media_overrides (
      drive_file_id TEXT PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES publisher_products(product_id),
      updated_at TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '');
    CREATE TABLE IF NOT EXISTS publisher_drive_sync_state (
      id INTEGER PRIMARY KEY,
      account_identity TEXT NOT NULL,
      provider TEXT NOT NULL,
      root_folder_id TEXT NOT NULL,
      current_cursor TEXT,
      cursor_status TEXT NOT NULL DEFAULT 'UNINITIALIZED'
        CHECK(cursor_status IN ('UNINITIALIZED','READY','INVALID','FULL_SCAN_REQUIRED','ERROR')),
      last_successful_sync_at TEXT,
      last_full_scan_at TEXT,
      last_error TEXT,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(account_identity,provider,root_folder_id));
    CREATE TABLE IF NOT EXISTS publisher_drive_poll_state (
      id INTEGER PRIMARY KEY,
      account_identity TEXT NOT NULL,
      provider TEXT NOT NULL,
      root_folder_id TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
      interval_seconds INTEGER NOT NULL DEFAULT 60 CHECK(interval_seconds >= 30),
      status TEXT NOT NULL DEFAULT 'DISABLED',
      last_attempt_at TEXT,
      last_success_at TEXT,
      next_attempt_at TEXT,
      consecutive_failures INTEGER NOT NULL DEFAULT 0,
      paused_reason TEXT,
      last_error_category TEXT,
      last_changes_seen INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL,
      UNIQUE(account_identity,provider,root_folder_id));
    CREATE TABLE IF NOT EXISTS publisher_batch_executions (
      id INTEGER PRIMARY KEY, batch_id INTEGER, total INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'QUEUED', started_at TEXT NOT NULL, finished_at TEXT,
      auto_cleanup INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS publisher_execution_items (
      execution_id INTEGER NOT NULL REFERENCES publisher_batch_executions(id),
      draft_id INTEGER NOT NULL REFERENCES publisher_drafts(id),
      status TEXT NOT NULL DEFAULT 'QUEUED', error_code TEXT,
      PRIMARY KEY(execution_id,draft_id));
    CREATE TABLE IF NOT EXISTS publisher_reconciliation_evidence (
      id INTEGER PRIMARY KEY,
      draft_id INTEGER NOT NULL REFERENCES publisher_drafts(id),
      media_asset_id INTEGER NOT NULL REFERENCES publisher_media_assets(id),
      execution_id INTEGER REFERENCES publisher_batch_executions(id),
      execution_item_draft_id INTEGER,
      account_id INTEGER NOT NULL REFERENCES publisher_accounts(id),
      expected_account TEXT NOT NULL,
      observed_account TEXT,
      outcome TEXT NOT NULL CHECK(outcome IN (
        'REMOTE_EXISTS_CONFIRMED','REMOTE_ABSENCE_CONFIRMED',
        'REMOTE_MATCH_AMBIGUOUS','REMOTE_STATE_UNKNOWN')),
      coverage_status TEXT NOT NULL CHECK(coverage_status IN ('COMPLETE','PARTIAL','UNKNOWN')),
      blocker_code TEXT,
      source_surface TEXT NOT NULL,
      candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
      matched_candidate_fingerprint TEXT,
      matcher_version TEXT NOT NULL,
      remote_effect_checkpoint_id INTEGER NOT NULL REFERENCES publisher_execution_logs(id),
      verification_started_at TEXT NOT NULL,
      verification_completed_at TEXT NOT NULL,
      valid_until TEXT,
      evidence_payload_json TEXT NOT NULL,
      CHECK ((execution_id IS NULL AND execution_item_draft_id IS NULL) OR
             (execution_id IS NOT NULL AND execution_item_draft_id = draft_id)),
      FOREIGN KEY (execution_id,execution_item_draft_id)
        REFERENCES publisher_execution_items(execution_id,draft_id));
    CREATE INDEX IF NOT EXISTS publisher_reconciliation_by_draft
      ON publisher_reconciliation_evidence(draft_id,id DESC);
    CREATE INDEX IF NOT EXISTS publisher_reconciliation_by_item
      ON publisher_reconciliation_evidence(execution_id,execution_item_draft_id,id DESC);
    CREATE INDEX IF NOT EXISTS publisher_reconciliation_by_media
      ON publisher_reconciliation_evidence(media_asset_id,id DESC);
    CREATE TRIGGER IF NOT EXISTS publisher_reconciliation_no_update
      BEFORE UPDATE ON publisher_reconciliation_evidence
      BEGIN SELECT RAISE(ABORT,'RECONCILIATION_EVIDENCE_APPEND_ONLY'); END;
    CREATE TRIGGER IF NOT EXISTS publisher_reconciliation_no_delete
      BEFORE DELETE ON publisher_reconciliation_evidence
      BEGIN SELECT RAISE(ABORT,'RECONCILIATION_EVIDENCE_APPEND_ONLY'); END;
    """)
    sync_columns = {row[1] for row in conn.execute('PRAGMA table_info(publisher_drive_sync_state)')}
    if 'last_sync_mode' not in sync_columns:
        conn.execute('ALTER TABLE publisher_drive_sync_state ADD COLUMN last_sync_mode TEXT')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_accounts)')}
    for name, definition in {
        'auth_status': "TEXT NOT NULL DEFAULT 'NOT_AUTHENTICATED'",
        'session_verified': 'INTEGER NOT NULL DEFAULT 0',
        'session_verified_at': 'TEXT',
        'auth_detail': "TEXT NOT NULL DEFAULT ''",
        'tiktok_username': "TEXT NOT NULL DEFAULT ''",
        'account_identity': "TEXT NOT NULL DEFAULT 'ACCOUNT_IDENTITY_UNKNOWN'",
        'identity_confirmed_profile': "TEXT NOT NULL DEFAULT ''",
        'identity_confirmed_handle': "TEXT NOT NULL DEFAULT ''",
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE publisher_accounts ADD COLUMN {name} {definition}')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_drafts)')}
    for name, definition in {
        'media_asset_id': 'INTEGER REFERENCES publisher_media_assets(id)',
        'tiktok_draft_id': 'TEXT', 'tiktok_draft_url': 'TEXT', 'confirmed_at': 'TEXT',
        'persistence_status': "TEXT NOT NULL DEFAULT 'UNCONFIRMED'",
        'upload_confirmed_at': 'TEXT', 'caption_applied_at': 'TEXT',
        'tiktok_draft_persisted_at': 'TEXT', 'temp_path': 'TEXT',
        'upload_status': "TEXT NOT NULL DEFAULT 'NOT_STARTED'",
        'processing_status': "TEXT NOT NULL DEFAULT 'NOT_STARTED'",
        'queue_position': 'INTEGER',
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE publisher_drafts ADD COLUMN {name} {definition}')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_batch_executions)')}
    if 'auto_cleanup' not in columns:
        conn.execute("ALTER TABLE publisher_batch_executions ADD COLUMN auto_cleanup INTEGER NOT NULL DEFAULT 0")
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_execution_items)')}
    if 'execution_position' not in columns:
        conn.execute('ALTER TABLE publisher_execution_items ADD COLUMN execution_position INTEGER')
    # Historical resume used draft_id ASC; retain that deterministic order for unmigrated rows.
    for batch in conn.execute('SELECT id FROM publisher_batches ORDER BY id').fetchall():
        rows = conn.execute('SELECT id,queue_position FROM publisher_drafts WHERE batch_id=? ORDER BY id', (batch['id'],)).fetchall()
        if any(row['queue_position'] is None for row in rows):
            if any(row['queue_position'] is not None for row in rows):
                raise ValueError('PARTIAL_QUEUE_POSITION_MIGRATION')
            for position, row in enumerate(rows, 1):
                conn.execute('UPDATE publisher_drafts SET queue_position=? WHERE id=?', (position, row['id']))
    for execution in conn.execute('SELECT id FROM publisher_batch_executions ORDER BY id').fetchall():
        rows = conn.execute('SELECT draft_id,execution_position FROM publisher_execution_items WHERE execution_id=? ORDER BY draft_id', (execution['id'],)).fetchall()
        if any(row['execution_position'] is None for row in rows):
            if any(row['execution_position'] is not None for row in rows):
                raise ValueError('PARTIAL_EXECUTION_POSITION_MIGRATION')
            for position, row in enumerate(rows, 1):
                conn.execute('UPDATE publisher_execution_items SET execution_position=? WHERE execution_id=? AND draft_id=?', (position, execution['id'], row['draft_id']))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS publisher_draft_position ON publisher_drafts(batch_id,queue_position) WHERE batch_id IS NOT NULL AND queue_position IS NOT NULL AND status<>'ARCHIVED'")
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS publisher_execution_position ON publisher_execution_items(execution_id,execution_position) WHERE execution_position IS NOT NULL')
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS publisher_media_reservation ON publisher_drafts(media_asset_id) WHERE media_asset_id IS NOT NULL AND status<>'ARCHIVED'")
    conn.execute("CREATE INDEX IF NOT EXISTS publisher_media_decisions_decision ON publisher_media_decisions(decision, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS publisher_media_overrides_product ON publisher_media_overrides(product_id, updated_at)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS publisher_drive_sync_scope ON publisher_drive_sync_state(account_identity,provider,root_folder_id)")
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_products)')}
    for name, definition in {
        'name': 'TEXT', 'short_name': 'TEXT', 'default_caption': 'TEXT',
        'flow_prompt': "TEXT NOT NULL DEFAULT ''", 'legacy_source': 'TEXT',
        'legacy_product_id': 'TEXT', 'legacy_shop_url': 'TEXT',
        'created_at': 'TEXT', 'updated_at': 'TEXT',
        'name_source': "TEXT NOT NULL DEFAULT 'MANUAL'",
        'source_drive_folder_id': 'TEXT',
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE publisher_products ADD COLUMN {name} {definition}')
    conn.execute('CREATE TABLE IF NOT EXISTS publisher_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)')
    headlines.init_headlines(conn)
    from src.publisher.upload_preparation import init_upload_preparation
    init_upload_preparation(conn)
    from src.publisher.local_preparation import init_local_preparation
    init_local_preparation(conn)
    init_queue_presentation(conn)
    conn.execute("INSERT OR IGNORE INTO publisher_migrations(name,applied_at) VALUES ('queue_presentation_v1',?)", (now_iso(),))
    init_account_safety(conn)
    if not conn.execute("SELECT 1 FROM publisher_migrations WHERE name='internal_catalog_v2'").fetchone():
        # Snapshot once: later Analytics imports must not change the editorial catalog.
        timestamp = now_iso()
        for product in conn.execute('SELECT * FROM products').fetchall():
            source = 'tiktok_report' if conn.execute("SELECT 1 FROM product_external_references WHERE product_id=? AND provider='tiktok_report'",(product['id'],)).fetchone() else 'legacy_products'
            conn.execute("INSERT OR IGNORE INTO publisher_products(product_id) VALUES (?)",(product['id'],))
            conn.execute("""UPDATE publisher_products SET name=?,default_caption=?,legacy_source=?,
              legacy_product_id=?,legacy_shop_url=?,created_at=?,updated_at=? WHERE product_id=?""",
              (product['name'],product['default_caption'] or '',source,product['tiktok_product_id'],product['tiktok_product_url'],timestamp,timestamp,product['id']))
        conn.execute("INSERT INTO publisher_migrations VALUES ('internal_catalog_v2',?)",(timestamp,))
    preference = conn.execute('SELECT id FROM publisher_preferences WHERE id=1').fetchone()
    if not preference:
        active_accounts = conn.execute(
            'SELECT id FROM publisher_accounts WHERE active=1 ORDER BY id'
        ).fetchall()
        default_account_id = active_accounts[0][0] if len(active_accounts) == 1 else None
        conn.execute(
            'INSERT INTO publisher_preferences(id,default_publisher_account_id,updated_at) VALUES (1,?,?)',
            (default_account_id, now_iso()),
        )
    conn.commit()


def get_row(conn, table, row_id):
    if table not in {"products", "publisher_accounts", "publisher_presets", "publisher_batches", "publisher_drafts", "publisher_media_assets"}:
        raise ValueError("Tabela invalida")
    row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (int(row_id),)).fetchone()
    if not row:
        raise ValueError("Registro nao encontrado")
    return dict(row)


def log(conn, draft_id, step, status, message, screenshot=None):
    conn.execute("INSERT INTO publisher_execution_logs (draft_id,step,status,message,screenshot_path,created_at) VALUES (?,?,?,?,?,?)",
                 (draft_id, step, status, message, str(screenshot) if screenshot else None, now_iso()))


def draft_retry_safe(conn, draft_id):
    row=get_row(conn,'publisher_drafts',draft_id)
    if row['status']!='STUDIO_CHANGED': return False
    if any(row.get(k) for k in ('confirmed_at','tiktok_draft_id','tiktok_draft_url')): return False
    if any(json.loads(row.get('checklist_json') or '{}').values()): return False
    if row.get('upload_status') not in {None, 'NOT_STARTED'}: return False
    if row.get('processing_status') not in {None, 'NOT_STARTED'}: return False
    if row.get('media_asset_id'):
        asset=get_row(conn,'publisher_media_assets',row['media_asset_id'])
        if asset.get('consumed_by_draft_id') or asset.get('remote_deleted_at') or asset['status'] not in {'RESERVED','AVAILABLE','FAILED'}: return False
    logs=conn.execute('SELECT step FROM publisher_execution_logs WHERE draft_id=?',(draft_id,)).fetchall()
    # A failed JIT download is retryable only while no upload-side checkpoint
    # exists. The download itself has no TikTok side effect.
    return not any(r[0].upper() in {'UPLOAD','UPLOADED','UPLOAD_STARTED','UPLOAD_CONFIRMED','CAPTION','SAVE_DRAFT','TIKTOK_DRAFT_CONFIRMED','REMOTE_DELETED'} for r in logs)


def transition(conn, draft_id, target, *, error=None, checklist=None, explicit_reset=False):
    row = get_row(conn, "publisher_drafts", draft_id)
    current = row["status"]
    if target in {'DRAFT', 'PREPARING', 'ARCHIVED'} and row.get('persistence_status') == 'PERSISTED':
        raise ValueError('DRAFT_ALREADY_PERSISTED')
    if target in {'DRAFT', 'PREPARING', 'ARCHIVED'} and row.get('upload_status') not in {None, 'NOT_STARTED'}:
        raise ValueError('UPLOAD_RECONCILIATION_REQUIRED')
    if target == 'PREPARING':
        assert_upload_allowed(conn, draft_id)
    if current=='STUDIO_CHANGED' and target=='DRAFT' and (not explicit_reset or not draft_retry_safe(conn,draft_id)):
        raise ValueError('EXPLICIT_SAFE_RETRY_REQUIRED')
    if row.get("confirmed_at") and target in {"DRAFT", "PREPARING", "ARCHIVED"}:
        raise ValueError("Midia ja recebida pelo TikTok; novo upload bloqueado")
    allowed = ({"DRAFT", "PREPARING", "ARCHIVED"} if current in EDITABLE else
               {"READY_FOR_REVIEW", *ERRORS} if current == "PREPARING" else
               {"DRAFT", "PUBLISHED", "SCHEDULED", "MANUAL_ACTION_REQUIRED"} if current == "READY_FOR_REVIEW" else
               {"PUBLISHED", "DRAFT"} if current == "SCHEDULED" else set())
    if target not in allowed:
        raise ValueError(f"Transicao invalida: {current} -> {target}")
    if target == "READY_FOR_REVIEW":
        required = {"file", "upload", "caption", "settings"}
        if not checklist or not all(checklist.get(k) is True for k in required):
            raise ValueError("Checklist incompleto")
        if row.get("media_asset_id") and (not row.get("confirmed_at") or not checklist.get("durable")):
            raise ValueError("Persistencia do draft TikTok nao confirmada")
    conn.execute("UPDATE publisher_drafts SET status=?,error_code=?,checklist_json=?,updated_at=? WHERE id=?",
                 (target, error, json.dumps(checklist or {}), now_iso(), draft_id))
    log(conn, draft_id, "STATUS", target, f"{current} -> {target}")


def assert_upload_allowed(conn, draft_id):
    row = get_row(conn, 'publisher_drafts', draft_id)
    if row.get('persistence_status') == 'PERSISTED' or row.get('confirmed_at'):
        raise ValueError('DRAFT_ALREADY_PERSISTED')
    if row.get('upload_status') not in {None, 'NOT_STARTED'}:
        raise ValueError('UPLOAD_RECONCILIATION_REQUIRED')
    if conn.execute("SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND step IN ('UPLOAD_STARTED','SAVE_DRAFT','TIKTOK_DRAFT_CONFIRMED','TEST_FINAL_RESULT') LIMIT 1", (draft_id,)).fetchone():
        raise ValueError('UPLOAD_RECONCILIATION_REQUIRED')
    if row.get('media_asset_id'):
        asset = get_row(conn, 'publisher_media_assets', row['media_asset_id'])
        assert_media_history_allowed(conn, asset, draft_id=draft_id, account_id=row['account_id'], purpose='upload')


class PublisherService:
    def __init__(self, conn, root):
        self.conn = conn
        self.root = Path(root).resolve()

    def catalog(self, *, include_archived=False):
        result = []
        for row in self.conn.execute("""SELECT x.product_id id,x.name,x.short_name,x.default_caption,
          x.flow_prompt,x.active,x.account_id,x.legacy_source,x.legacy_product_id,x.legacy_shop_url,
          x.default_headline_enabled,x.default_headline_text,x.default_headline_preset_id,x.headline_required,
          x.default_headline_template_id,x.default_headline_size,
          x.name_source,x.source_drive_folder_id,x.created_at,x.updated_at
          FROM publisher_products x WHERE (?=1 OR x.active=1) ORDER BY x.name""", (int(include_archived),)):
            item = dict(row)
            item["aliases"] = [r[0] for r in self.conn.execute("SELECT alias FROM product_aliases WHERE product_id=?", (item["id"],))]
            item["folders"] = self.list_drive_folders(item["id"])
            item["reference_count"], item["reference_types"] = self.product_reference_summary(item["id"])
            result.append(item)
        return result

    def product_workspace(self, *, scope="current", search=""):
        if scope not in {"current", "no_current", "archived", "repeated"}:
            raise ValueError("Filtro de produtos invalido")
        products = self.catalog(include_archived=True)
        media_ids = {}
        for row in _raw_rows(self.conn, file_type="VIDEO"):
            item = _classify(row)
            if item["product_id"] and item["status"] not in {"CONSUMED", "IGNORED"}:
                media_ids.setdefault(item["product_id"], set()).add(item["drive_file_id"])
        names = {}
        for product in products:
            key = re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", product["name"].casefold()).encode("ascii", "ignore").decode()).strip()
            names[key] = names.get(key, 0) + 1
            product["_normalized_name"] = key
        counts = {"current": 0, "no_current": 0, "archived": 0}
        for product in products:
            product["folder_count"] = len(product["folders"])
            product["current_media_count"] = len(media_ids.get(product["id"], ()))
            product["has_default_caption"] = bool((product["default_caption"] or "").strip())
            product["can_hard_delete"] = product["reference_count"] == 0
            product["can_archive"] = bool(product["active"])
            product["can_restore"] = not bool(product["active"])
            product["has_historical_references"] = product["reference_count"] > 0
            product["duplicate_group"] = product.pop("_normalized_name")
            product["possible_duplicate_name"] = names[product["duplicate_group"]] > 1
            product["catalog_presentation_state"] = ("archived" if not product["active"] else
                "current" if product["folder_count"] or product["current_media_count"] else "no_current")
            counts[product["catalog_presentation_state"]] += 1
        query = search.strip().casefold()
        visible = [product for product in products if (product["possible_duplicate_name"] if scope == "repeated" else product["catalog_presentation_state"] == scope) and
            (not query or query in " ".join((product["name"], product["short_name"] or "",
                str(product["id"]), product["legacy_product_id"] or "", " ".join(product["aliases"]))).casefold())]
        visible.sort(key=lambda product: (product["name"].casefold(), product["id"]))
        return {"products": visible, "counts": counts, "scope": scope, "search": search,
                "repeated_name_groups": sum(1 for count in names.values() if count > 1)}

    def get_product(self, product_id):
        row = self.conn.execute('SELECT *,product_id AS id FROM publisher_products WHERE product_id=?',(int(product_id),)).fetchone()
        if not row:
            raise ValueError('Produto interno nao encontrado')
        return dict(row)

    def get_preferences(self):
        row = self.conn.execute(
            "SELECT default_publisher_account_id,updated_at FROM publisher_preferences WHERE id=1"
        ).fetchone()
        if not row:
            return {"default_publisher_account_id": None, "updated_at": None}
        default_id = row["default_publisher_account_id"]
        account = self.conn.execute(
            "SELECT id,name,active FROM publisher_accounts WHERE id=?", (default_id,)
        ).fetchone() if default_id else None
        return {
            "default_publisher_account_id": int(default_id) if default_id else None,
            "default_publisher_account_name": account["name"] if account and account["active"] else None,
            "default_account_active": bool(account and account["active"]),
            "updated_at": row["updated_at"],
        }

    def default_account_id(self):
        preferences = self.get_preferences()
        account_id = preferences.get("default_publisher_account_id")
        if not account_id:
            return None
        row = self.conn.execute(
            "SELECT id FROM publisher_accounts WHERE id=? AND active=1", (account_id,)
        ).fetchone()
        return int(row[0]) if row else None

    def set_default_account(self, account_id):
        account_id = int(account_id) if account_id else None
        if account_id:
            account = get_row(self.conn, "publisher_accounts", account_id)
            if not account["active"]:
                raise ValueError("A conta padrao precisa estar ativa")
        with self.conn:
            self.conn.execute(
                "INSERT INTO publisher_preferences(id,default_publisher_account_id,updated_at) VALUES (1,?,?) "
                "ON CONFLICT(id) DO UPDATE SET default_publisher_account_id=excluded.default_publisher_account_id,updated_at=excluded.updated_at",
                (account_id, now_iso()),
            )
        return account_id

    def list_drive_folders(self, product_id=None):
        query = """SELECT drive_folder_id id,name,relative_path,parent_drive_folder_id,product_id,
                    mapping_status,mapping_source,operational_status
                   FROM drive_folders WHERE operational_status='ACTIVE'"""
        params = []
        if product_id is not None:
            query += " AND product_id=?"
            params.append(int(product_id))
        query += " ORDER BY relative_path,name"
        return [dict(row) for row in self.conn.execute(query, params)]

    def product_reference_summary(self, product_id):
        references = {}
        checks = (
            ("drafts", "publisher_drafts"),
            ("batches", "publisher_batches", "id IN (SELECT batch_id FROM publisher_drafts WHERE product_id=?)"),
            ("executions", "publisher_execution_items", "draft_id IN (SELECT id FROM publisher_drafts WHERE product_id=?)"),
            ("production_tasks", "production_tasks"),
            ("snapshots", "product_snapshots"),
            ("videos", "video_metrics"),
            ("creators", "creator_metrics"),
            ("content", "content_items"),
            ("order_items", "affiliate_order_items"),
            ("external_products", "product_external_references"),
            ("external_contents", "external_tiktok_contents"),
            ("aliases", "product_aliases"),
            ("prompts", "product_prompts"),
            ("captions", "product_captions"),
            ("drive_folders", "drive_folders"),
            ("drive_files", "drive_files"),
        )
        total = 0
        for item in checks:
            label, table = item[:2]
            if not self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone():
                continue
            if len(item) == 3:
                where, params = item[2], (int(product_id),)
            else:
                where, params = "product_id=?", (int(product_id),)
            count = int(self.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", params).fetchone()[0])
            if count:
                references[label] = count
                total += count
        return total, references

    def product_bulk_plan(self, product_ids):
        ids = list(dict.fromkeys(int(item) for item in product_ids))
        if not ids or len(ids) > 100:
            raise ValueError("Selecione entre 1 e 100 produtos")
        items = []
        for product_id in ids:
            product = self.get_product(product_id)
            count, types = self.product_reference_summary(product_id)
            items.append({
                "id": product_id, "name": product["name"],
                "reference_count": count, "reference_types": types,
                "can_delete": count == 0, "can_hard_delete": count == 0, "will_archive": count > 0,
            })
        return {
            "items": items,
            "selected": len(items),
            "will_delete": sum(item["can_delete"] for item in items),
            "will_archive": sum(item["will_archive"] for item in items),
        }

    def bulk_products(self, product_ids, action, *, human_confirmed=False):
        if action not in {"archive", "delete", "restore"}:
            raise ValueError("Acao de produto invalida")
        if human_confirmed is not True:
            raise ValueError("CONFIRM_PRODUCT_ACTION")
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            plan = self.product_bulk_plan(product_ids)
            if action == "delete" and plan["will_archive"]:
                raise ValueError("PRODUCT_HARD_DELETE_BLOCKED_BY_HISTORY")
            for item in plan["items"]:
                if action == "restore":
                    self.conn.execute("UPDATE publisher_products SET active=1,updated_at=? WHERE product_id=?", (timestamp, item["id"]))
                elif action == "archive":
                    self.conn.execute(
                        "UPDATE publisher_products SET active=0,updated_at=? WHERE product_id=?",
                        (timestamp, item["id"]),
                    )
                else:
                    self.conn.execute("DELETE FROM publisher_products WHERE product_id=?", (item["id"],))
                    self.conn.execute("DELETE FROM products WHERE id=?", (item["id"],))
        return {**plan, "action": action, "archived": plan["selected"] if action == "archive" else 0,
                "restored": plan["selected"] if action == "restore" else 0,
                "deleted": plan["selected"] if action == "delete" else 0}

    def set_product_folder(self, product_id, folder_id, action="add"):
        product_id = int(product_id)
        self.get_product(product_id)
        folder_id = str(folder_id or "").strip()
        if not folder_id:
            raise ValueError("Pasta do Drive obrigatoria")
        folder = self.conn.execute(
            "SELECT product_id,name,operational_status FROM drive_folders WHERE drive_folder_id=?",
            (folder_id,),
        ).fetchone()
        if not folder:
            raise ProductFolderError('PRODUCT_FOLDER_NOT_FOUND', 'Pasta do Drive não encontrada')
        if folder['operational_status'] != 'ACTIVE':
            raise ProductFolderError('PRODUCT_FOLDER_UNAVAILABLE', 'Pasta do Drive indisponível neste perfil')
        if folder['product_id'] is not None and folder['product_id'] != product_id:
            raise ProductFolderError('PRODUCT_FOLDER_CONFLICT', f'Pasta {folder["name"]} já pertence a outro produto')
        if action == "remove":
            target = None
        elif action == "add":
            target = product_id
        else:
            raise ValueError("Acao de pasta invalida")
        if folder['product_id'] == target:
            return {"product_id": product_id, "folder_id": folder_id, "action": action}
        from src.drive_inventory import set_folder_mapping
        set_folder_mapping(self.conn, folder_id, target, reason=f"Publisher product folder {action}")
        return {"product_id": product_id, "folder_id": folder_id, "action": action}

    def _execution_summaries(self, where_sql, params=(), limit=None):
        sql = f"""SELECT e.*,
          COALESCE(SUM(CASE WHEN i.status='QUEUED' THEN 1 ELSE 0 END),0) queued,
          COALESCE(SUM(CASE WHEN i.status IN ('PROCESSING','CLEANUP') THEN 1 ELSE 0 END),0) processing,
          COALESCE(SUM(CASE WHEN i.status='SUCCEEDED' THEN 1 ELSE 0 END),0) succeeded,
          COALESCE(SUM(CASE WHEN i.status='FAILED' THEN 1 ELSE 0 END),0) failed,
          COALESCE(SUM(CASE WHEN i.status='WAITING_MANUAL' THEN 1 ELSE 0 END),0) waiting_manual,
          COALESCE(SUM(CASE WHEN m.status='CONSUMED' THEN 1 ELSE 0 END),0) remote_deleted,
          COALESCE(SUM(CASE WHEN m.status='CONSUMED_PENDING_DELETE' THEN 1 ELSE 0 END),0) remote_delete_pending,
          COALESCE(SUM(CASE WHEN m.status NOT IN ('CONSUMED','CONSUMED_PENDING_DELETE') THEN 1 ELSE 0 END),0) remote_preserved
          FROM publisher_batch_executions e
          LEFT JOIN publisher_execution_items i ON i.execution_id=e.id
          LEFT JOIN publisher_drafts d ON d.id=i.draft_id
          LEFT JOIN publisher_media_assets m ON m.id=d.media_asset_id
          WHERE {where_sql} GROUP BY e.id ORDER BY e.id DESC"""
        if limit is not None:
            sql += ' LIMIT ?'
            params = (*params, int(limit))
        return [dict(row) for row in self.conn.execute(sql, params)]

    def set_queue_visibility(self, data):
        if not isinstance(data, dict):
            raise ValueError('INVALID_QUEUE_PRESENTATION_REQUEST')
        return change_visibility(self.conn, data.get('action'), data.get('target_type'), data.get('target_id'))

    def execution_history(self, *, before_id=None, limit=30, hidden_only=False):
        limit = max(1, min(int(limit), 30))
        statuses = ('COMPLETED', 'SUCCESS')
        where = "e.status IN (?,?)"
        params = list(statuses)
        if hidden_only:
            where += f' AND EXISTS (SELECT 1 FROM publisher_execution_items i JOIN publisher_drafts d ON d.id=i.draft_id WHERE i.execution_id=e.id AND {HIDDEN_ITEM_SQL})'
        if before_id is not None:
            where += ' AND e.id<?'
            params.append(int(before_id))
        total = self.conn.execute(
            "SELECT COUNT(*) FROM publisher_batch_executions WHERE status IN (?,?)", statuses,
        ).fetchone()[0]
        executions = self._execution_summaries(where, params, None if hidden_only else limit + 1)
        has_more = not hidden_only and len(executions) > limit
        executions = executions if hidden_only else executions[:limit]
        for execution in executions:
            execution['batch_names'] = [row['name'] for row in self.conn.execute(
                "SELECT DISTINCT b.name FROM publisher_execution_items i "
                "JOIN publisher_drafts d ON d.id=i.draft_id "
                "JOIN publisher_batches b ON b.id=d.batch_id "
                "WHERE i.execution_id=? ORDER BY b.id DESC", (execution['id'],),
            )]
            execution['items'] = [dict(row) for row in self.conn.execute("""SELECT i.execution_id,i.draft_id,
              i.execution_position,i.status item_status,i.error_code item_error_code,
              d.batch_id draft_batch_id,b.name batch_name,d.filename,d.status draft_status,
              d.persistence_status,d.upload_status,d.product_id,d.account_id,d.caption,p.name product_name,
              p.short_name product_short_name,a.name account_name,m.status media_status,
              d.media_asset_id,m.remote_file_id,f.drive_folder_id folder_id,folder.name folder_name
              FROM publisher_execution_items i JOIN publisher_drafts d ON d.id=i.draft_id
              LEFT JOIN publisher_batches b ON b.id=d.batch_id
              LEFT JOIN publisher_products p ON p.product_id=d.product_id
              LEFT JOIN publisher_accounts a ON a.id=d.account_id
              LEFT JOIN publisher_media_assets m ON m.id=d.media_asset_id
              LEFT JOIN drive_files f ON f.drive_file_id=m.remote_file_id
              LEFT JOIN drive_folders folder ON folder.drive_folder_id=f.drive_folder_id
              WHERE i.execution_id=? ORDER BY i.execution_position,i.draft_id""", (execution['id'],))]
            for item in execution['items']:
                item['queue_display'] = draft_queue_presentation(
                    {**item, 'status': item['draft_status']}, {'status': item['media_status']},
                    item_status=item['item_status'],
                    **queue_preparation_context(self.conn, item['draft_id']),
                )
        return {
            'executions': executions,
            'total_count': int(total),
            'page_size': limit,
            'has_more': has_more,
            'next_before_id': executions[-1]['id'] if has_more and executions else None,
            'presentation_counts': history_visibility_counts(self.conn),
        }

    def snapshot(self):
        drafts = [dict(r) for r in self.conn.execute("""SELECT d.*,p.name product_name,p.short_name product_short_name,a.name account_name,
          m.status media_status,m.remote_file_id,m.cleanup_error,f.thumbnail_link,
          b.name batch_name,f.drive_folder_id folder_id,folder.name folder_name
          FROM publisher_drafts d JOIN publisher_products p ON p.product_id=d.product_id
          LEFT JOIN publisher_media_assets m ON m.id=d.media_asset_id
          LEFT JOIN drive_files f ON f.drive_file_id=m.remote_file_id
          LEFT JOIN drive_folders folder ON folder.drive_folder_id=f.drive_folder_id
          LEFT JOIN publisher_batches b ON b.id=d.batch_id
          JOIN publisher_accounts a ON a.id=d.account_id
          ORDER BY d.batch_id DESC, COALESCE(d.queue_position,d.id) ASC,d.id ASC""")]
        protections_by_account = {row['id']: AccountSafetyGuard(self.conn).snapshot(row['id'])['state'] != 'NORMAL'
                                  for row in self.conn.execute('SELECT DISTINCT account_id id FROM publisher_drafts')}
        for draft in drafts:
            from src.publisher.persistence_identity import can_confirm_persistence
            from src.publisher.cleanup_authorization import cleanup_identity_token
            draft['cleanup_identity_token'] = cleanup_identity_token(self.conn,draft['id']) if draft['media_asset_id'] else None
            draft['headline'] = headlines.read_snapshot(draft)
            draft["checklist"] = json.loads(draft.pop("checklist_json"))
            media = dict(self.conn.execute('SELECT * FROM publisher_media_assets WHERE id=?', (draft['media_asset_id'],)).fetchone()) if draft['media_asset_id'] else None
            blocked = protections_by_account[draft['account_id']]
            draft['queue_capabilities'] = draft_queue_capabilities(self.conn, draft, media, account_blocked=blocked)
            draft['queue_display'] = draft_queue_presentation(draft, media, **queue_preparation_context(self.conn, draft['id']))
            draft['preparation_diagnostic'] = queue_preparation_diagnostic(self.conn, draft)
            checkpoint = self.conn.execute(
                "SELECT id FROM publisher_execution_logs WHERE draft_id=? AND step IN "
                "('UPLOAD_STARTED','SAVE_DRAFT','TIKTOK_DRAFT_CONFIRMED') ORDER BY id DESC LIMIT 1",
                (draft['id'],),
            ).fetchone()
            draft['resolution_checkpoint_id'] = checkpoint['id'] if checkpoint else None
            if draft.get('queue_capabilities', {}).get('can_resolve'):
                draft['resolution_safety_summary'] = resolution_safety_summary(draft, self.conn.execute(
                    'SELECT step FROM publisher_execution_logs WHERE draft_id=?', (draft['id'],),
                ).fetchall())
            evidence = get_latest_reconciliation_evidence(self.conn, draft_id=draft['id'])
            if evidence:
                validity = evaluate_reconciliation_evidence_validity(
                    self.conn, evidence['id'], draft_id=draft['id'],
                    media_asset_id=draft['media_asset_id'], account_id=draft['account_id'],
                    execution_id=evidence['execution_id'],
                )
                draft['reconciliation_evidence'] = {
                    'id': evidence['id'],
                    'outcome': evidence['outcome'],
                    'coverage_status': evidence['coverage_status'],
                    'blocker_code': evidence['blocker_code'],
                    'verified_at': evidence['verification_completed_at'],
                    'current': validity['valid'],
                    'can_confirm_persistence': can_confirm_persistence(self.conn, draft, evidence['id']),
                    'stale_reason': None if validity['valid'] else validity['reason'],
                    **current_listing_summary(evidence),
                }
        batch_ids = {draft['batch_id'] for draft in drafts if draft['batch_id'] is not None}
        queue_preflights = {batch_id: self.queue_preflight(batch_id) for batch_id in batch_ids}
        protections = []
        for account in self.conn.execute("SELECT id,name FROM publisher_accounts WHERE active=1 ORDER BY id"):
            item = AccountSafetyGuard(self.conn).snapshot(account['id'])
            item['account_name'] = account['name']
            item['account_handle'] = self.conn.execute(
                "SELECT tiktok_username FROM publisher_accounts WHERE id=?", (account['id'],)
            ).fetchone()[0]
            latest = self.conn.execute("""SELECT created_at,evidence_json FROM account_protection_events
              WHERE account_id=? ORDER BY id DESC LIMIT 1""", (account['id'],)).fetchone()
            evidence = json.loads(latest['evidence_json'] or '{}') if latest else {}
            item['observed_at'] = latest['created_at'] if latest else item.get('activated_at')
            item['evidence_summary'] = evidence.get('ui_marker') or evidence.get('provider_code') or evidence.get('operation') or ''
            item['blocked'] = item['state'] != 'NORMAL'
            protections.append(item)
        batch_rows = [dict(row) for row in self.conn.execute('SELECT * FROM publisher_batches ORDER BY id DESC')]
        for batch in batch_rows:
            batch['queue_capabilities'] = batch_queue_capabilities(self.conn, batch['id'])
            from src.publisher.posting_review import batch_review
            review = batch_review(self.conn, batch['id'])
            batch['review_summary'] = {key: value for key, value in review.items()
                                       if key not in ('items', 'eligible_ids', 'consumption')}
            batch['review_summary']['consumption_counts'] = review['consumption']['counts']
        active_executions = self._execution_summaries("e.status NOT IN ('COMPLETED','SUCCESS')")
        from src.publisher.queue_policy import preupload_preparation_resume_allowed
        for execution in active_executions:
            items = self.conn.execute('SELECT * FROM publisher_execution_items WHERE execution_id=? ORDER BY execution_position',
                                      (execution['id'],)).fetchall()
            execution['can_resume_local_preparation'] = bool(
                execution['status'] == 'PAUSED' and len(items) == execution['total']
                and preupload_preparation_resume_allowed(self.conn, execution, items))
        history = self.execution_history(limit=30)
        queue_presentation = {**presentation_targets(self.conn),
                              'history': self.execution_history(hidden_only=True)['executions']}
        return {"products": self.catalog(include_archived=True), "preferences": self.get_preferences(),
                "headline_presets": headlines.list_presets(self.conn),
                "headline_templates": headline_workflow.list_templates(self.conn),
                "headline_styles": headline_workflow.style_catalog(self.conn),
                "queue_presentation": queue_presentation,
                "drive_folders": self.list_drive_folders(), "drafts": drafts, "statuses": STATUSES,
                "queue_status_labels": {status: draft_queue_presentation({'status': status})['label'] for status in STATUSES},
                "queue_preflights": queue_preflights,
                "execution_items": [dict(r) for r in self.conn.execute("""SELECT i.*,e.batch_id,e.status execution_status
                  ,d.batch_id draft_batch_id,d.status draft_status
                  FROM publisher_execution_items i JOIN publisher_batch_executions e ON e.id=i.execution_id
                  JOIN publisher_drafts d ON d.id=i.draft_id
                  WHERE e.status NOT IN ('COMPLETED','SUCCESS')
                  ORDER BY e.id DESC,i.execution_position ASC""")],
                "media_assets": [dict(r) for r in self.conn.execute("SELECT * FROM publisher_media_assets ORDER BY filename")],
                "executions": active_executions,
                "history": history,
                **{name: [dict(r) for r in self.conn.execute(f"SELECT * FROM publisher_{name} ORDER BY id DESC")]
                   for name in ("accounts", "presets")},
                "batches": batch_rows,
                "account_protection": protections,
                "logs": [dict(r) for r in self.conn.execute("SELECT * FROM publisher_execution_logs ORDER BY id DESC LIMIT 300")]}

    def index_media(self, provider):
        files = provider.list_files()
        timestamp = now_iso()
        with self.conn:
            for item in files:
                self.conn.execute("""INSERT INTO publisher_media_assets
                  (remote_file_id,remote_folder_id,filename,mime_type,size_bytes,checksum,folder_path,
                   web_view_link,remote_created_at,remote_modified_at,last_seen_at,created_at,updated_at)
                  VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(provider,source_account,remote_file_id)
                  DO UPDATE SET last_seen_at=excluded.last_seen_at,
                    filename=CASE WHEN publisher_media_assets.status IN ('AVAILABLE','MISSING') THEN excluded.filename ELSE publisher_media_assets.filename END,
                    size_bytes=CASE WHEN publisher_media_assets.status IN ('AVAILABLE','MISSING') THEN excluded.size_bytes ELSE publisher_media_assets.size_bytes END,
                    checksum=CASE WHEN publisher_media_assets.status IN ('AVAILABLE','MISSING') THEN excluded.checksum ELSE publisher_media_assets.checksum END,
                    folder_path=excluded.folder_path,remote_folder_id=excluded.remote_folder_id,
                    status=CASE WHEN publisher_media_assets.status='MISSING' THEN 'AVAILABLE' ELSE publisher_media_assets.status END,
                    updated_at=excluded.updated_at""",
                  (item['id'], (item.get('parents') or [None])[0], item['name'], item['mimeType'],
                   item.get('size'), item.get('md5Checksum'), item.get('folder_path'), item.get('webViewLink'),
                   item.get('createdTime'), item.get('modifiedTime'), timestamp, timestamp, timestamp))
            self.conn.execute("UPDATE publisher_media_assets SET status='MISSING' WHERE status='AVAILABLE' AND last_seen_at<>?", (timestamp,))
            self.conn.execute("""UPDATE publisher_media_assets SET status='IN_USE_ELSEWHERE' WHERE status='AVAILABLE' AND
              (remote_file_id IN (SELECT drive_file_id FROM drive_files WHERE classification IN ('ALREADY_PUBLISHED','ALREADY_QUEUED','DUPLICATE','INVALID'))
              OR remote_file_id IN (SELECT google_drive_file_id FROM content_items WHERE status IN ('PUBLISHED','QUEUED','SENT_TO_PHONE')))""")
        return {'indexed': len(files)}

    def assert_media_available(self, asset, draft_id=None, *, account_id=None):
        assert_media_history_allowed(self.conn, asset, draft_id=draft_id, account_id=account_id, purpose='reserve')
        from src.publisher.local_preparation import active_local_preparation
        if active_local_preparation(self.conn, asset['remote_file_id']):
            raise ValueError('LOCAL_MEDIA_BUSY')
        if asset['status'] not in {'AVAILABLE', 'RESERVED', 'FAILED'}:
            raise ValueError(f"Midia indisponivel: {asset['status']}")
        if self.conn.execute("SELECT 1 FROM content_items WHERE google_drive_file_id=? AND status IN ('PUBLISHED','QUEUED','SENT_TO_PHONE') LIMIT 1",(asset['remote_file_id'],)).fetchone():
            raise ValueError('Midia ja publicada ou em uso pela fila existente')
        duplicate = self.conn.execute("""SELECT id FROM publisher_media_assets WHERE id<>? AND
          status IN ('RESERVED','PROCESSING','FAILED','RECEIVED_NEEDS_REVIEW','NEEDS_RECONCILIATION','CONSUMED_PENDING_DELETE','CONSUMED') AND
          ((checksum IS NOT NULL AND checksum<>'' AND checksum=?) OR (filename=? AND size_bytes=?)) LIMIT 1""",
          (asset['id'], asset['checksum'], asset['filename'], asset['size_bytes'])).fetchone()
        if duplicate:
            raise ValueError(f"Midia duplicada/reservada: asset {duplicate[0]}")
        existing = self.conn.execute("SELECT id FROM publisher_drafts WHERE media_asset_id=? AND status<>'ARCHIVED' AND id<>?",
                                     (asset['id'], draft_id or 0)).fetchone()
        if existing:
            raise ValueError(f"Midia ja reservada pelo draft {existing[0]}")

    def create_remote_batch(self, data):
        ids = list(dict.fromkeys(int(i) for i in data.get('media_asset_ids', [])))
        require_batch_size(len(ids))
        headline_selections = data.get('headline_selections') or {}
        if not isinstance(headline_selections, dict) or set(headline_selections) - {str(i) for i in ids}:
            raise ValueError('HEADLINE_SELECTION_INVALID')
        fields = {k: data.get(k) or None for k in ('product_id','account_id','preset_id','scheduled_at')}
        fields['account_id'] = fields['account_id'] or self.default_account_id()
        fields['caption'] = ''
        product = self.validate_fields(fields)
        fields['caption'] = product.get('default_caption') or ''
        self.validate_fields(fields)
        timestamp = now_iso()
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            batch = self.conn.execute("INSERT INTO publisher_batches(name,source,created_at) VALUES (?,'CECILIA_DRIVE',?)",
                                      (data.get('name') or f'Lote {timestamp[:16]}', timestamp)).lastrowid
            drafts = []
            for position, asset_id in enumerate(ids, 1):
                asset = get_row(self.conn, 'publisher_media_assets', asset_id)
                self.assert_media_available(asset, account_id=fields['account_id'])
                row_id = self.conn.execute("""INSERT INTO publisher_drafts
                  (batch_id,product_id,account_id,preset_id,media_asset_id,video_path,filename,caption,scheduled_at,created_at,updated_at)
                  VALUES (?,?,?,?,?,'',?,?,?,?,?)""",
                  (batch, fields['product_id'],fields['account_id'],fields['preset_id'],asset_id,asset['filename'],
                   fields['caption'],fields['scheduled_at'],timestamp,timestamp)).lastrowid
                self.conn.execute('UPDATE publisher_drafts SET queue_position=? WHERE id=?', (position, row_id))
                headlines.capture(self.conn, row_id, product, headline_selections.get(str(asset_id)))
                self.conn.execute("UPDATE publisher_media_assets SET status='RESERVED',updated_at=? WHERE id=?", (timestamp,asset_id))
                log(self.conn,row_id,'CREATE','DRAFT','Referencia remota; nenhum download realizado')
                if not fields['caption'].strip():
                    self.conn.execute("UPDATE publisher_drafts SET status='CAPTION_MISSING',error_code='CAPTION_MISSING' WHERE id=?",(row_id,))
                drafts.append(row_id)
        return {'batch_id':batch,'draft_ids':drafts}

    def media_library(self, params=None):
        params = params or {}
        return list_library(
            self.conn,
            page=params.get("page", 1),
            page_size=params.get("page_size", 50),
            search=params.get("search", ""),
            path=params.get("path", ""),
            product_id=params.get("product_id"),
            file_type=params.get("type", params.get("file_type", "VIDEO")),
            status=params.get("status", ""),
            decision=params.get("decision", ""),
        )

    def media_library_groups(self, params=None):
        params = params or {}
        return list_library_groups(self.conn, **self._library_group_filters(params),
                                   page=params.get("page", 1), page_size=params.get("page_size", 20),
                                   group_view=params.get("group_view", "ACTIVE"))

    @staticmethod
    def _library_group_filters(params):
        return {"search": params.get("search", ""), "path": params.get("path", ""),
                "product_id": params.get("product_id"), "file_type": params.get("type", "VIDEO"),
                "status": params.get("status", ""), "decision": params.get("decision", "")}

    def media_library_group_items(self, params):
        return list_group_items(self.conn, str(params.get("group_key") or ""),
                                page=params.get("page", 1), page_size=params.get("page_size", 50),
                                group_view=params.get("group_view", "ACTIVE"),
                                **self._library_group_filters(params))

    def select_media_library_group(self, data):
        return select_group_items(self.conn, str(data.get("group_key") or ""),
                                  selected_drive_file_ids=data.get("selected_drive_file_ids") or [],
                                  **self._library_group_filters(data))

    def folder_catalog_reviews(self, params=None):
        params = params or {}
        if params.get("grouped") in {"1", "true", "True"}:
            return grouped_folder_reviews(self.conn)
        return {"items": list_folder_reviews(self.conn, review_filter=params.get("filter", "REVIEW_REQUIRED"))}

    def decide_folder_catalog(self, data):
        if data.get("action") == "reject_match":
            return reject_suggested_match(self.conn, str(data.get("folder_id") or ""))
        role = str(data.get("role") or "").upper()
        product_id = data.get("product_id")
        if data.get("create_product"):
            folder = self.conn.execute(
                "SELECT name FROM drive_folders WHERE drive_folder_id=?", (str(data.get("folder_id")),)
            ).fetchone()
            if not folder:
                raise ValueError("FOLDER_NOT_FOUND")
            product_id = self.save_product({"name": folder[0], "short_name": folder[0],
                                            "name_source": "DRIVE",
                                            "source_drive_folder_id": str(data.get("folder_id"))})
            role = "PRODUCT"
        return decide_folder_role(self.conn, str(data.get("folder_id") or ""), role,
                                  product_id=product_id)

    def bulk_folder_catalog(self, data):
        return apply_bulk_role(self.conn, data.get("folder_ids") or [], data.get("role"),
                               confirmed=data.get("human_confirmed") is True)

    def configure_product_container(self, data):
        return set_container_auto_create(self.conn, str(data.get("folder_id") or ""),
                                         bool(data.get("auto_create_new_direct_children", False)))

    def decide_media(self, data):
        return save_decision(self.conn, data.get("drive_file_id"), data.get("decision"), data.get("reason", ""))

    def decide_library_group(self, data):
        return ignore_library_group(
            self.conn, data.get("group_key"), human_confirmed=data.get("human_confirmed") is True,
            expected_count=data.get("expected_count"),
        )

    def assign_media_product(self, data):
        ids = data.get("drive_file_ids") or []
        if data.get("scope") == "folder":
            folder_id = str(data.get("folder_id") or "").strip()
            product_id = int(data.get("product_id"))
            from src.drive_inventory import set_folder_mapping
            self.get_product(product_id)
            set_folder_mapping(self.conn, folder_id, product_id, reason="Atribuição em lote na Biblioteca")
            return {"scope": "folder", "folder_id": folder_id, "product_id": product_id}
        return set_product_overrides(self.conn, ids, int(data.get("product_id")))

    def delete_library_media(self, data, provider=None, *, provider_factory=None):
        # Raw Library deletion must not become an escape route for archived
        # Publisher history. Use the same write-lock protocol as cleanup.
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            return self._delete_library_media_locked(data, provider, provider_factory=provider_factory)

    def _delete_library_media_locked(self, data, provider, *, provider_factory=None):
        if data.get("human_confirmed") is not True:
            raise ValueError("CONFIRM_MEDIA_DELETE")
        drive_file_id = str(data.get("drive_file_id") or "").strip()
        filename = str(data.get("filename") or "").strip()
        if not drive_file_id or not filename:
            raise ValueError("IDENTITY_REQUIRED_FOR_MEDIA_DELETE")
        rows = get_library_items(self.conn, [drive_file_id])
        if len(rows) != 1:
            raise ValueError("Arquivo não encontrado no inventário")
        item = rows[0]
        if item.get('media_history_blocker'):
            raise MediaHistoryBlocked(item['media_history_blocker'])
        if item["filename"] != filename or (data.get("size") is not None and int(data["size"]) != int(item.get("size") or 0)):
            raise ValueError("MEDIA_IDENTITY_MISMATCH")
        if item.get("media_status") in {"RESERVED", "PROCESSING", "CONSUMED_PENDING_DELETE", "CONSUMED"} or item.get("draft_id"):
            raise ValueError("Mídia vinculada a draft; finalize o lifecycle pelo Publisher")
        asset = {
            "provider": "google_drive", "source_account": "cecilia",
            "remote_file_id": drive_file_id, "filename": item["filename"],
            "size_bytes": int(item.get("size") or 0), "checksum": item.get("md5_checksum"),
        }
        if provider is None:
            if provider_factory is None:
                raise ValueError('MEDIA_PROVIDER_REQUIRED')
            provider = provider_factory()
        before = provider.get_remote_metadata(drive_file_id)
        if before is None:
            raise ValueError("REMOTE_FILE_NOT_FOUND")
        if before.get("id") != drive_file_id or before.get("name") != filename:
            raise ValueError("MEDIA_IDENTITY_MISMATCH")
        provider.delete_remote(asset)
        after = provider.get_remote_metadata(drive_file_id)
        if after is not None:
            raise ValueError("REMOTE_DELETE_NOT_CONFIRMED")
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("UPDATE drive_files SET missing_since=?,updated_at=? WHERE drive_file_id=?", (timestamp, timestamp, drive_file_id))
        return {"drive_file_id": drive_file_id, "remote_exists_after": False, "deleted_at": timestamp}

    def _library_batch_preflight(self, data):
        raw_ids = data.get("drive_file_ids")
        ids = list(dict.fromkeys(str(value).strip() for value in raw_ids
                                 if str(value).strip())) if isinstance(raw_ids, (list, tuple)) else []
        configuration_blockers = []
        problems = []

        def config_blocker(code, message):
            configuration_blockers.append({"code": code, "message": message})
            problems.append({"code": code, "reason": message})

        if not ids:
            config_blocker("SELECTION_REQUIRED", "Selecione pelo menos um vídeo da Biblioteca")
        elif len(ids) > MAX_DRAFTS_PER_BATCH:
            config_blocker("BATCH_LIMIT_EXCEEDED", f"{len(ids)} selecionados; máximo {MAX_DRAFTS_PER_BATCH} rascunhos por lote")
            configuration_blockers[-1].update(actual=len(ids), max=MAX_DRAFTS_PER_BATCH)

        name = str(data.get("name") or "").strip()
        if not name:
            config_blocker("BATCH_NAME_REQUIRED", "Informe um nome para o lote")
        if data.get("scheduled_at"):
            config_blocker("SCHEDULING_NOT_SUPPORTED", "A Biblioteca não agenda lotes")

        account_id = data.get("account_id") or self.default_account_id()
        account = None
        if not account_id:
            config_blocker("ACCOUNT_REQUIRED", "Selecione uma conta Publisher ativa")
        else:
            try:
                account = get_row(self.conn, "publisher_accounts", account_id)
            except (ValueError, TypeError):
                config_blocker("ACCOUNT_NOT_FOUND", "A conta selecionada não existe")
            else:
                account_id = int(account["id"])
                if not account["active"]:
                    config_blocker("ACCOUNT_INACTIVE", "A conta selecionada está inativa")
                else:
                    try:
                        protection = AccountSafetyGuard(self.conn).snapshot(account_id)
                    except (AccountProtectionBlocked, ValueError):
                        config_blocker("ACCOUNT_PROTECTION_UNAVAILABLE", "Não foi possível confirmar a proteção da conta")
                    else:
                        if protection["state"] != "NORMAL":
                            config_blocker("ACCOUNT_PROTECTED", "A conta está protegida; revise o estado antes de criar um lote")

        preset_id = data.get("preset_id") or None
        preset = None
        if preset_id:
            try:
                preset = get_row(self.conn, "publisher_presets", preset_id)
            except (ValueError, TypeError):
                config_blocker("PRESET_NOT_FOUND", "O preset selecionado não existe")
            else:
                preset_id = int(preset["id"])
                if account and preset.get("account_id") and int(preset["account_id"]) != int(account["id"]):
                    config_blocker("PRESET_ACCOUNT_MISMATCH", "O preset pertence a outra conta")

        within_limit = 0 < len(ids) <= MAX_DRAFTS_PER_BATCH
        source_items = get_library_items(self.conn, ids) if within_limit else []
        renderer_readiness = None
        headline_selections = data.get('headline_selections') or {}
        if not isinstance(headline_selections, dict) or set(headline_selections)-set(ids):
            raise ValueError('HEADLINE_SELECTION_INVALID')
        by_id = {item["drive_file_id"]: item for item in source_items}
        items = []
        for file_id in ids if within_limit else []:
            item = by_id.get(file_id)
            blockers = []
            warnings = []

            def item_blocker(code, message, action=None):
                if any(existing["code"] == code for existing in blockers):
                    return
                blocker = {"code": code, "message": message}
                if action:
                    blocker["safe_fix_action"] = action
                blockers.append(blocker)

            from src.publisher.local_preparation import active_local_preparation
            if active_local_preparation(self.conn, file_id):
                item_blocker('LOCAL_MEDIA_BUSY', 'A preparação local desta mídia ainda está em andamento')

            if item is None:
                item_blocker("MEDIA_NOT_IN_INVENTORY", "Arquivo não encontrado no inventário")
                product_id = None
            else:
                product_id = item.get("product_id")
                if product_id:
                    from src.publisher.headline_workflow import selection_snapshot
                    try:
                        snapshot = selection_snapshot(self.conn, self.get_product(product_id), headline_selections.get(file_id))
                        item['headline_snapshot'] = snapshot
                        if snapshot['enabled']:
                            from src.publisher.headline_configuration import machine_readiness
                            if renderer_readiness is None:
                                renderer_readiness = machine_readiness(self.root)
                            if not renderer_readiness['ready']:
                                item_blocker(renderer_readiness['code'], 'Renderer da headline precisa ser configurado')
                    except (ValueError, TypeError) as exc:
                        item_blocker(str(exc), 'Revise o texto e o estilo da headline')
                if item.get("product_archived"):
                    item_blocker("PRODUCT_ARCHIVED", "Produto arquivado", {
                        "action": "RESTORE_PRODUCT", "product_id": product_id,
                        "label": "Restaurar produto",
                    })
                if not product_id:
                    item_blocker("PRODUCT_UNASSIGNED", "Produto não associado", {
                        "action": "ASSOCIATE_PRODUCT", "folder_id": item.get("folder_id"),
                        "label": "Associar produto",
                    })
                if product_id and not str(item.get("caption") or "").strip():
                    item_blocker("CAPTION_MISSING", "Legenda não configurada", {
                        "action": "CONFIGURE_CAPTION", "product_id": product_id,
                        "label": "Configurar legenda",
                    })
                if item.get("media_status") in {"RESERVED", "PROCESSING", "NEEDS_RECONCILIATION",
                                                "CONSUMED_PENDING_DELETE", "CONSUMED", "MISSING", "IN_USE_ELSEWHERE"}:
                    item_blocker("MEDIA_NOT_AVAILABLE", item.get("reason") or "Mídia indisponível")
                if not item.get("eligible"):
                    known = {"PRODUCT_ARCHIVED", "PRODUCT_UNASSIGNED", "CAPTION_MISSING", "MEDIA_NOT_AVAILABLE"}
                    if not any(blocker["code"] in known for blocker in blockers):
                        reason = item.get("reason") or item.get("status_label") or "Mídia requer revisão"
                        item_blocker("MEDIA_REQUIRES_REVIEW", reason)
                elif not item.get("product_archived") and account and account.get("active") and not any(
                        blocker["code"].startswith("PRESET_") for blocker in configuration_blockers):
                    try:
                        self.validate_fields({"product_id": product_id, "account_id": account_id,
                                              "preset_id": preset_id, "caption": item.get("caption") or ""})
                    except (ValueError, KeyError) as exc:
                        message = str(exc)
                        code = "PRODUCT_ACCOUNT_MISMATCH" if "restrito a outra conta" in message else (
                            "CAPTION_TOO_LONG" if "2200" in message else "BATCH_FIELDS_INVALID")
                        item_blocker(code, message)
                    if not blockers and item.get("media_asset_id"):
                        asset = self.conn.execute("SELECT * FROM publisher_media_assets WHERE id=?",
                                                  (item["media_asset_id"],)).fetchone()
                        if asset:
                            try:
                                self.assert_media_available(dict(asset), account_id=account_id)
                            except ValueError as exc:
                                item_blocker(exc.code if isinstance(exc, MediaHistoryBlocked) else "MEDIA_NOT_AVAILABLE", str(exc))

            product_name = (item.get("product_short_name") or item.get("product_name")) if item else None
            items.append({
                "drive_file_id": file_id,
                "filename": item.get("filename") if item else None,
                "folder_id": item.get("folder_id") if item else None,
                "product_id": product_id,
                "product_display_name": product_name or (f"Produto {product_id}" if product_id else "Sem produto"),
                "product_archived": bool(item and item.get("product_archived")),
                "readiness": "READY" if not blockers else "BLOCKED",
                "blockers": blockers,
                "warnings": warnings,
            })
            for blocker in blockers:
                problems.append({"drive_file_id": file_id, "filename": item.get("filename") if item else None,
                                 "product_id": product_id, "code": blocker["code"],
                                 "reason": item.get("reason") if item and blocker["code"] in {
                                     "PRODUCT_UNASSIGNED", "CAPTION_MISSING", "MEDIA_REQUIRES_REVIEW"
                                 } else blocker["message"],
                                 "message": blocker["message"]})

        products_by_key = {}
        for item in items:
            key = str(item["product_id"]) if item["product_id"] is not None else "unassigned"
            product = products_by_key.setdefault(key, {
                "product_id": item["product_id"], "display_name": item["product_display_name"], "count": 0,
            })
            product["count"] += 1

        can_create = bool(ids) and len(ids) <= MAX_DRAFTS_PER_BATCH and not configuration_blockers and len(items) == len(ids) and all(
            item["readiness"] == "READY" for item in items
        )
        ready_count = sum(item["readiness"] == "READY" for item in items)
        preview = {
            "total": len(ids), "ready": ready_count, "blocked": max(0, len(ids) - ready_count),
            "warning_count": sum(len(item["warnings"]) for item in items),
            "can_create": can_create, "account_id": int(account_id) if account_id and str(account_id).isdigit() else None,
            "account_name": account.get("name") if account else None,
            "preset_id": int(preset_id) if preset_id and str(preset_id).isdigit() else None,
            "configuration_blockers": configuration_blockers,
            "products": list(products_by_key.values()),
            "product_count": sum(product["product_id"] is not None for product in products_by_key.values()),
            "items": items,
            "problems": problems,
        }
        return {"preview": preview, "items_by_id": by_id}

    def preflight_library_batch(self, data):
        """Read-only readiness preview for an explicit set of Drive file IDs."""
        return self._library_batch_preflight(data)["preview"]

    def create_library_batch(self, data):
        raw_ids = data.get("drive_file_ids")
        ids = list(dict.fromkeys(str(value).strip() for value in raw_ids
                                 if str(value).strip())) if isinstance(raw_ids, (list, tuple)) else []
        require_batch_size(len(ids))
        if data.get("scheduled_at"):
            raise ValueError("A Biblioteca não agenda lotes; horário desejado não é definido")
        name = str(data.get("name") or "").strip()
        account_id = data.get("account_id") or self.default_account_id()
        if not account_id:
            raise ValueError("Selecione uma conta Publisher ativa")
        try:
            account = get_row(self.conn, "publisher_accounts", account_id)
        except (ValueError, TypeError) as exc:
            raise ValueError("A conta selecionada não existe") from exc
        if not account["active"]:
            raise ValueError("A conta selecionada está inativa")
        account_id = int(account["id"])
        try:
            AccountSafetyGuard(self.conn).assert_allowed(account_id)
        except AccountProtectionBlocked as exc:
            raise ValueError(f'BLOCKED_BY_ACCOUNT_PROTECTION: {exc.reason_code}') from exc
        preset_id = data.get("preset_id") or None
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            timestamp = now_iso()
            batch_name = name or f"Lote {timestamp[:16]}"
            validation = self._library_batch_preflight({**data, "drive_file_ids": ids, "name": batch_name,
                                                        "account_id": account_id, "preset_id": preset_id})
            preview = validation["preview"]
            if not preview["can_create"]:
                raise ValueError("BATCH_PREFLIGHT_FAILED: " + json.dumps(preview["problems"], ensure_ascii=False))
            by_id = validation["items_by_id"]
            batch_id = self.conn.execute(
                "INSERT INTO publisher_batches(name,source,created_at) VALUES (?, 'MEDIA_LIBRARY', ?)",
                (batch_name, timestamp),
            ).lastrowid
            draft_ids = []
            for position, file_id in enumerate(ids, 1):
                item = by_id[file_id]
                asset = self.conn.execute("SELECT * FROM publisher_media_assets WHERE provider='google_drive' "
                                          "AND source_account='cecilia' AND remote_file_id=?", (file_id,)).fetchone()
                if asset:
                    asset = dict(asset)
                    self.assert_media_available(asset, account_id=account_id)
                    asset_id = asset["id"]
                else:
                    asset_id = self.conn.execute("""INSERT INTO publisher_media_assets
                      (provider,source_account,remote_file_id,remote_folder_id,filename,mime_type,size_bytes,checksum,
                       folder_path,web_view_link,remote_created_at,remote_modified_at,last_seen_at,created_at,updated_at)
                      VALUES ('google_drive','cecilia',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (file_id, item["folder_id"], item["filename"], item["mime_type"] or "video/mp4", item.get("size"),
                       item.get("md5_checksum"), item.get("folder_path"), item.get("web_view_link"), item.get("created_time"),
                       item.get("modified_time"), timestamp, timestamp, timestamp)).lastrowid
                draft_id = self.conn.execute("""INSERT INTO publisher_drafts
                  (batch_id,product_id,account_id,preset_id,media_asset_id,video_path,filename,caption,scheduled_at,created_at,updated_at)
                  VALUES (?,?,?,?,?,'',?,?,?,?,?)""",
                  (batch_id, item["product_id"], account_id, preset_id, asset_id, item["filename"], item["caption"], None, timestamp, timestamp)).lastrowid
                self.conn.execute('UPDATE publisher_drafts SET queue_position=? WHERE id=?', (position, draft_id))
                self.conn.execute('UPDATE publisher_drafts SET headline_snapshot_json=? WHERE id=?',
                                  (headlines.canonical(item['headline_snapshot']), draft_id))
                self.conn.execute("UPDATE publisher_media_assets SET status='RESERVED',updated_at=? WHERE id=?", (timestamp, asset_id))
                log(self.conn, draft_id, "CREATE", "DRAFT", "Biblioteca: referência Drive reservada; nenhum download realizado")
                draft_ids.append(draft_id)
        return {"batch_id": batch_id, "draft_ids": draft_ids, "items": [{"drive_file_id": i["drive_file_id"], "product_id": i["product_id"], "filename": i["filename"]} for i in (by_id[file_id] for file_id in ids)]}

    def reorder_batch(self, batch_id, ordered_draft_ids):
        batch_id = int(batch_id)
        ids = [int(value) for value in ordered_draft_ids]
        if not ids or len(ids) != len(set(ids)):
            raise ValueError('INVALID_QUEUE_ORDER')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            get_row(self.conn, 'publisher_batches', batch_id)
            capabilities = batch_queue_capabilities(self.conn, batch_id)
            if not capabilities['can_reorder']:
                code = 'BATCH_ALREADY_EXECUTED' if capabilities['has_execution'] else 'QUEUE_REORDER_BLOCKED'
                raise ValueError(code)
            rows = self.conn.execute("SELECT * FROM publisher_drafts WHERE batch_id=? AND status<>'ARCHIVED' ORDER BY queue_position,id", (batch_id,)).fetchall()
            if len(rows) != len(ids) or {row['id'] for row in rows} != set(ids):
                raise ValueError('QUEUE_MEMBERSHIP_MISMATCH')
            offset = max((row['queue_position'] or 0 for row in rows), default=0) + len(rows) + 1
            for position, draft_id in enumerate(ids, 1):
                self.conn.execute('UPDATE publisher_drafts SET queue_position=? WHERE id=?', (offset + position, draft_id))
            for position, draft_id in enumerate(ids, 1):
                self.conn.execute('UPDATE publisher_drafts SET queue_position=?,updated_at=? WHERE id=?', (position, now_iso(), draft_id))
        return {'batch_id': batch_id, 'ordered_draft_ids': ids}

    def rename_batch(self, batch_id, name):
        batch_id = int(batch_id)
        name = str(name or '').strip()
        if not name or len(name) > 160:
            raise ValueError('INVALID_BATCH_NAME')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            batch = get_row(self.conn, 'publisher_batches', batch_id)
            if not batch_queue_capabilities(self.conn, batch_id)['can_rename']:
                raise ValueError('BATCH_RENAME_BLOCKED')
            self.conn.execute('UPDATE publisher_batches SET name=? WHERE id=?', (name, batch_id))
            first = self.conn.execute(
                "SELECT id FROM publisher_drafts WHERE batch_id=? AND status<>'ARCHIVED' ORDER BY queue_position,id LIMIT 1",
                (batch_id,),
            ).fetchone()
            if first:
                log(self.conn, first['id'], 'BATCH_RENAMED', 'OK', f'Lote {batch_id} renomeado')
        return {'batch_id': batch_id, 'name': name}

    def edit_batch_configuration(self, batch_id, data):
        batch_id = int(batch_id)
        if not isinstance(data, dict) or set(data) - {'account_id', 'preset_id'}:
            raise ValueError('INVALID_BATCH_CONFIGURATION_FIELDS')
        fields = {}
        for key in ('account_id', 'preset_id'):
            value = data.get(key)
            if value not in (None, ''):
                fields[key] = int(value)
        if not fields:
            raise ValueError('NO_BATCH_CONFIGURATION_CHANGES')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            get_row(self.conn, 'publisher_batches', batch_id)
            capabilities = batch_queue_capabilities(self.conn, batch_id)
            if not capabilities['can_edit'] or any(key not in capabilities['editable_fields'] for key in fields):
                raise ValueError('BATCH_CONFIGURATION_EDIT_BLOCKED')
            rows = self.conn.execute(
                "SELECT * FROM publisher_drafts WHERE batch_id=? AND status<>'ARCHIVED' ORDER BY queue_position,id",
                (batch_id,),
            ).fetchall()
            for row in rows:
                merged = {**dict(row), **fields}
                self.validate_fields(merged)
            timestamp = now_iso()
            assignments = ','.join(f'{key}=?' for key in fields)
            for row in rows:
                self.conn.execute(
                    f'UPDATE publisher_drafts SET {assignments},updated_at=? WHERE id=?',
                    (*fields.values(), timestamp, row['id']),
                )
                log(self.conn, row['id'], 'BATCH_CONFIGURATION_EDIT', 'OK',
                    'Conta/preset atualizados; produto e legenda do draft preservados')
        return {'batch_id': batch_id, 'draft_ids': [row['id'] for row in rows], 'changed_fields': sorted(fields)}

    def cancel_batch(self, batch_id, *, eligible_only=False):
        batch_id = int(batch_id)
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            get_row(self.conn, 'publisher_batches', batch_id)
            capabilities = batch_queue_capabilities(self.conn, batch_id)
            if not eligible_only and not capabilities['can_cancel']:
                raise ValueError('BATCH_CANCEL_BLOCKED')
            rows = self.conn.execute(
                "SELECT * FROM publisher_drafts WHERE batch_id=? AND status<>'ARCHIVED' ORDER BY queue_position,id",
                (batch_id,),
            ).fetchall()
            released = 0
            results = []
            for row in rows:
                reason = local_cancel_blocker(self.conn, dict(row))
                if reason:
                    if not eligible_only:
                        raise ValueError('BATCH_CANCEL_BLOCKED')
                    results.append({'draft_id': row['id'], 'media_asset_id': row['media_asset_id'],
                                    'result': 'BLOCKED', 'reason': reason})
                    continue
                transition(self.conn, row['id'], 'ARCHIVED', checklist=json.loads(row['checklist_json'] or '{}'))
                # A cancelled local item is terminal for the old execution, never queued again on resume.
                self.conn.execute("UPDATE publisher_execution_items SET status='FAILED',error_code='LOCAL_BATCH_CANCELLED' "
                                  "WHERE draft_id=? AND status IN ('WAITING_MANUAL','FAILED') "
                                  "AND execution_id IN (SELECT id FROM publisher_batch_executions WHERE status='PAUSED')",
                                  (row['id'],))
                if row['media_asset_id']:
                    self.conn.execute(
                        "UPDATE publisher_media_assets SET status='AVAILABLE',updated_at=? "
                        "WHERE id=? AND status IN ('RESERVED','FAILED')",
                        (now_iso(), row['media_asset_id']),
                    )
                    released += 1
                log(self.conn, row['id'], 'BATCH_CANCELLED', 'OK', json.dumps({
                    'batch_id': batch_id, 'draft_id': row['id'], 'media_asset_id': row['media_asset_id'],
                    'previous_state': row['status'], 'previous_error_code': row['error_code'],
                    'resulting_state': 'ARCHIVED', 'reservation_released': True,
                    'remote_actions': 0, 'retry_authorized': False,
                }, ensure_ascii=True, sort_keys=True))
                results.append({'draft_id': row['id'], 'media_asset_id': row['media_asset_id'],
                                'result': 'CANCELLED', 'reservation_released': True})
            self.conn.execute("UPDATE publisher_batch_executions SET status='COMPLETED',finished_at=? "
                              "WHERE status='PAUSED' AND total>0 "
                              "AND total=(SELECT COUNT(*) FROM publisher_execution_items i WHERE i.execution_id=publisher_batch_executions.id) "
                              "AND EXISTS (SELECT 1 FROM publisher_execution_items i JOIN publisher_drafts d ON d.id=i.draft_id "
                              "WHERE i.execution_id=publisher_batch_executions.id AND d.batch_id=?) "
                              "AND NOT EXISTS (SELECT 1 FROM publisher_execution_items i WHERE i.execution_id=publisher_batch_executions.id "
                              "AND (i.status<>'FAILED' OR COALESCE(i.error_code,'')<>'LOCAL_BATCH_CANCELLED'))",
                              (now_iso(), batch_id))
        return {'batch_id': batch_id, 'cancelled_drafts': sum(r['result'] == 'CANCELLED' for r in results),
                'blocked_drafts': sum(r['result'] == 'BLOCKED' for r in results),
                'reservations_released': released, 'items': results, 'audit_preserved': True,
                'already_cancelled': not rows}

    def queue_preflight(self, batch_id):
        rows = self.conn.execute("SELECT * FROM publisher_drafts WHERE batch_id=? AND status<>'ARCHIVED' ORDER BY queue_position,id", (int(batch_id),)).fetchall()
        get_row(self.conn, 'publisher_batches', int(batch_id))
        batch_size = self.conn.execute('SELECT COUNT(*) FROM publisher_drafts WHERE batch_id=?',
                                       (int(batch_id),)).fetchone()[0]
        oversized = batch_size > MAX_DRAFTS_PER_BATCH
        items = []
        renderer_readiness = None
        for source in rows:
            draft = dict(source)
            media = dict(get_row(self.conn, 'publisher_media_assets', draft['media_asset_id'])) if draft['media_asset_id'] else None
            protection = AccountSafetyGuard(self.conn).snapshot(draft['account_id'])
            blocked = protection['state'] != 'NORMAL'
            actions = draft_queue_capabilities(self.conn, draft, media, account_blocked=blocked)
            blockers = []
            warnings = []
            if oversized:
                blockers.append('BATCH_LIMIT_EXCEEDED')
            if not actions['can_start']:
                blockers.append('DRAFT_NOT_STARTABLE')
            if actions.get('media_history_blocker'):
                blockers.append(actions['media_history_blocker']['code'])
            if not str(draft['caption'] or '').strip():
                blockers.append('CAPTION_MISSING')
            if media is None or media['status'] not in ('AVAILABLE', 'RESERVED'):
                blockers.append('MEDIA_NOT_AVAILABLE')
            if blocked:
                blockers.append('BLOCKED_BY_ACCOUNT_PROTECTION')
            try:
                self.validate_fields(draft)
            except (ValueError, KeyError) as exc:
                blockers.append(str(exc))
            if headlines.read_snapshot(draft)['enabled']:
                from src.publisher.headline_configuration import machine_readiness
                if renderer_readiness is None:
                    renderer_readiness=machine_readiness(self.root)
                if not renderer_readiness['ready']:
                    blockers.append(renderer_readiness['code'])
            if media and not blockers:
                try:
                    self.assert_media_available(media, draft['id'])
                except ValueError as exc:
                    blockers.append(str(exc))
            items.append({'draft_id': draft['id'], 'queue_position': draft['queue_position'],
                          'ready': not blockers, 'blockers': blockers, 'warnings': warnings,
                          'safe_actions': [key for key, allowed in actions.items() if key.startswith('can_') and allowed]})
        return {'batch_id': int(batch_id), 'total_items': len(items),
                'ready_items': sum(item['ready'] for item in items),
                'blocked_items': sum(not item['ready'] for item in items),
                'warning_items': sum(bool(item['warnings']) for item in items), 'items': items}

    def save_product(self, data):
        requested_folders = data.get('folder_ids', [])
        if not isinstance(requested_folders, list):
            raise ValueError('PRODUCT_FOLDER_IDS_INVALID')
        folder_ids = [str(folder_id).strip() for folder_id in requested_folders]
        if any(not folder_id for folder_id in folder_ids):
            raise ValueError('PRODUCT_FOLDER_IDS_INVALID')
        if len(folder_ids) != len(set(folder_ids)):
            raise ProductFolderError('PRODUCT_FOLDER_DUPLICATE', 'A mesma pasta foi selecionada mais de uma vez')
        if data.get('id'):
            if folder_ids:
                raise ValueError('Associe pastas ao produto existente pela acao de pasta')
            explicit = dict(data)
            data = {**self.get_product(data['id']), **explicit}
            if "name" in explicit and "name_source" not in explicit:
                data["name_source"] = "MANUAL"
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Nome do produto obrigatorio")
        product_id = int(data["id"]) if data.get("id") else None
        if product_id:
            self.get_product(product_id)
        caption = str(data.get('default_caption') or '')
        if len(caption)>2200:
            raise ValueError('Legenda excede 2200 caracteres')
        flow_prompt = str(data.get('flow_prompt') or '')
        if len(flow_prompt) > 100000:
            raise ValueError('Prompt do Flow excede 100000 caracteres')
        account_id = data.get("account_id") or None
        headline_config = headlines.product_config(self.conn, data)
        from src.publisher.headline_workflow import get_template, variant
        template_id = int(data['default_headline_template_id']) if data.get('default_headline_template_id') else None
        size = data.get('default_headline_size') or 'STANDARD'
        if size not in {'SMALL','STANDARD','LARGE'}:
            raise ValueError('HEADLINE_SIZE_INVALID')
        if template_id:
            template = get_template(self.conn, template_id, product_id)
            headline_config = (headline_config[0], template['text'], headline_config[2], headline_config[3])
        if headline_config[2]: variant(self.conn, headline_config[2], size)
        if account_id:
            get_row(self.conn, "publisher_accounts", account_id)
        timestamp = now_iso()
        with self.conn:
            if folder_ids:
                self.conn.execute('BEGIN IMMEDIATE')
                folders = self.conn.execute(
                    f"SELECT drive_folder_id,name,operational_status,product_id FROM drive_folders WHERE drive_folder_id IN ({','.join('?' for _ in folder_ids)})",
                    folder_ids,
                ).fetchall()
                by_id = {folder['drive_folder_id']: folder for folder in folders}
                for folder_id in folder_ids:
                    folder = by_id.get(folder_id)
                    if not folder:
                        raise ProductFolderError('PRODUCT_FOLDER_NOT_FOUND', f'Pasta {folder_id} não encontrada no inventário')
                    if folder['operational_status'] != 'ACTIVE':
                        raise ProductFolderError('PRODUCT_FOLDER_UNAVAILABLE', f'Pasta {folder["name"]} não está disponível neste perfil')
                    if folder['product_id'] is not None:
                        existing = self.conn.execute('SELECT name FROM publisher_products WHERE product_id=?', (folder['product_id'],)).fetchone()
                        name = existing['name'] if existing else f'ID {folder["product_id"]}'
                        raise ProductFolderError('PRODUCT_FOLDER_CONFLICT', f'Pasta {folder["name"]} já pertence ao produto {name}')
            if not product_id:
                # Keep existing foreign keys while isolating editorial fields from Analytics.
                product_id = self.conn.execute("INSERT INTO products(product_key,name,created_at,updated_at) VALUES (?,?,?,?)",
                                               (f'publisher:{uuid.uuid4().hex}',name,timestamp,timestamp)).lastrowid
            self.conn.execute("""INSERT INTO publisher_products
              (product_id,name,short_name,default_caption,flow_prompt,account_id,active,created_at,updated_at,
               name_source,source_drive_folder_id)
              VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET
              name=excluded.name,short_name=excluded.short_name,default_caption=excluded.default_caption,flow_prompt=excluded.flow_prompt,
              account_id=excluded.account_id,active=excluded.active,updated_at=excluded.updated_at,
              name_source=CASE WHEN excluded.name_source='DRIVE' THEN publisher_products.name_source ELSE 'MANUAL' END,
              source_drive_folder_id=COALESCE(publisher_products.source_drive_folder_id,excluded.source_drive_folder_id)""",
              (product_id,name,str(data.get('short_name') or '').strip(),caption,flow_prompt,account_id,int(data.get('active',True)),timestamp,timestamp,
               str(data.get('name_source') or 'MANUAL'),data.get('source_drive_folder_id')))
            self.conn.execute('UPDATE publisher_products SET default_headline_enabled=?,default_headline_text=?,default_headline_preset_id=?,headline_required=? WHERE product_id=?',
                              (*headline_config, product_id))
            self.conn.execute('UPDATE publisher_products SET default_headline_template_id=?,default_headline_size=? WHERE product_id=?',
                              (template_id,size,product_id))
            if folder_ids:
                from src.drive_inventory import set_folder_mapping
                for folder_id in folder_ids:
                    set_folder_mapping(self.conn, folder_id, product_id, reason='Pasta selecionada ao criar produto manual', commit=False)
        return product_id

    def save_account(self, data):
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Nome da conta obrigatorio")
        row_id = int(data["id"]) if data.get("id") else None
        timestamp = now_iso()
        with self.conn:
            if row_id:
                get_row(self.conn, "publisher_accounts", row_id)
                self.assert_config_editable("account_id", row_id)
                self.conn.execute("UPDATE publisher_accounts SET name=?,active=?,updated_at=? WHERE id=?", (name, int(data.get("active", True)), timestamp, row_id))
            else:
                profile = uuid.uuid4().hex
                row_id = self.conn.execute("INSERT INTO publisher_accounts(name,browser_profile,created_at,updated_at) VALUES (?,?,?,?)", (name, profile, timestamp, timestamp)).lastrowid
            preference = self.conn.execute(
                "SELECT default_publisher_account_id FROM publisher_preferences WHERE id=1"
            ).fetchone()
            active_accounts = self.conn.execute(
                "SELECT id FROM publisher_accounts WHERE active=1 ORDER BY id"
            ).fetchall()
            if preference and preference[0] is None and len(active_accounts) == 1:
                self.conn.execute(
                    "UPDATE publisher_preferences SET default_publisher_account_id=?,updated_at=? WHERE id=1",
                    (active_accounts[0][0], timestamp),
                )
        return row_id

    def save_preset(self, data):
        name = str(data.get("name", "")).strip()
        if not name or data.get("visibility", "PUBLIC") not in {"PUBLIC", "FRIENDS", "PRIVATE"}:
            raise ValueError("Preset invalido")
        account = data.get("account_id") or None
        if account:
            get_row(self.conn, "publisher_accounts", account)
        legacy_required = get_row(self.conn,'publisher_presets',data['id'])['product_required'] if data.get('id') else 0
        values = (name, account, data.get("visibility", "PUBLIC"), int(data.get("comments", True)), legacy_required, now_iso())
        with self.conn:
            if data.get("id"):
                get_row(self.conn, "publisher_presets", data["id"])
                self.assert_config_editable("preset_id", data["id"])
                self.conn.execute("UPDATE publisher_presets SET name=?,account_id=?,visibility=?,comments=?,product_required=?,updated_at=? WHERE id=?", (*values, data["id"]))
                return int(data["id"])
            return self.conn.execute("INSERT INTO publisher_presets(name,account_id,visibility,comments,product_required,updated_at,created_at) VALUES (?,?,?,?,?,?,?)", (*values, now_iso())).lastrowid

    def override_headline(self, draft_id, data):
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            draft = get_row(self.conn, 'publisher_drafts', draft_id)
            media = get_row(self.conn, 'publisher_media_assets', draft['media_asset_id']) if draft['media_asset_id'] else None
            if not draft_queue_capabilities(self.conn, draft, media)['can_edit']:
                raise ValueError('QUEUE_EDIT_BLOCKED')
            previous = headlines.read_snapshot(draft)
            if 'mode' in data:
                from src.publisher.headline_workflow import selection_snapshot
                product = self.get_product(draft['product_id'])
                if previous['required'] and data['mode'] == 'none':
                    raise ValueError('HEADLINE_REQUIRED')
                product['headline_required'] = previous['required']
                snapshot = selection_snapshot(self.conn, product, data)
                snapshot['required'] = previous['required']
                self.conn.execute('UPDATE publisher_drafts SET headline_snapshot_json=?,updated_at=? WHERE id=?',
                                  (headlines.canonical(snapshot),now_iso(),draft_id))
                log(self.conn,draft_id,'HEADLINE_OVERRIDE',draft['status'],'Headline selecionada e copiada para o Draft')
                return snapshot
            data = dict(data)
            size = data.pop('size', None)
            if size and previous['enabled']:
                from src.publisher.headline_workflow import variant
                data['preset_id'] = variant(self.conn,data.get('preset_id') or previous['preset']['id'],size)
            # Policy and effective text belong to the Draft, never re-read current defaults.
            product = dict(default_headline_enabled=previous['enabled'], default_headline_text=previous['text'],
                           default_headline_preset_id=None, headline_required=previous['required'])
            snapshot = headlines.make_snapshot(self.conn, product, data)
            if size: snapshot['size'] = size
            if 'preset_id' not in data and snapshot['enabled']:
                snapshot['preset'] = previous['preset']
                snapshot['render_spec_hash'] = headlines.render_hash(snapshot)
            blockers = headlines.readiness(snapshot)
            if blockers:
                raise ValueError(blockers[0])
            self.conn.execute('UPDATE publisher_drafts SET headline_snapshot_json=?,updated_at=? WHERE id=?',
                              (headlines.canonical(snapshot), now_iso(), draft_id))
            log(self.conn, draft_id, 'HEADLINE_OVERRIDE', draft['status'], 'Configuracao local de headline atualizada')
            return snapshot

    def bulk_headline(self, data):
        from src.publisher.headline_workflow import selection_snapshot, variant
        ids = list(dict.fromkeys(int(value) for value in data.get('ids',[])))
        if not ids or len(ids)>MAX_DRAFTS_PER_BATCH:
            raise ValueError('HEADLINE_BULK_SELECTION_INVALID')
        patch = data.get('patch') or {}
        if not patch or set(patch)-{'mode','template_id','text','preset_id','size'}:
            raise ValueError('HEADLINE_SELECTION_INVALID')
        with self.conn:
            self.conn.execute('BEGIN IMMEDIATE')
            changes=[]
            for draft_id in ids:
                draft=get_row(self.conn,'publisher_drafts',draft_id)
                media=get_row(self.conn,'publisher_media_assets',draft['media_asset_id']) if draft['media_asset_id'] else None
                if not draft_queue_capabilities(self.conn,draft,media)['can_edit']:
                    raise ValueError('QUEUE_EDIT_BLOCKED')
                previous=headlines.read_snapshot(draft)
                if 'mode' in patch:
                    if previous['required'] and patch['mode'] == 'none':
                        raise ValueError('HEADLINE_REQUIRED')
                    product=self.get_product(draft['product_id']);product['headline_required']=previous['required']
                    snapshot=selection_snapshot(self.conn,product,patch)
                    snapshot['required']=previous['required']
                else:
                    snapshot=previous
                    if snapshot['enabled']:
                        chosen=patch.get('preset_id') or snapshot['preset']['id']
                        snapshot['preset']=headlines.get_preset(self.conn,variant(self.conn,chosen,patch.get('size',snapshot.get('size','STANDARD'))))
                        snapshot['size']=patch.get('size',snapshot.get('size','STANDARD'))
                        snapshot['render_spec_hash']=headlines.render_hash(snapshot)
                        errors=headlines.readiness(snapshot)
                        if errors:raise ValueError(errors[0])
                changes.append((draft_id,snapshot))
            for draft_id,snapshot in changes:
                self.conn.execute('UPDATE publisher_drafts SET headline_snapshot_json=?,updated_at=? WHERE id=?',
                                  (headlines.canonical(snapshot),now_iso(),draft_id))
                log(self.conn,draft_id,'HEADLINE_OVERRIDE','DRAFT','Ajuste explicito local em lote')
        return {'draft_ids':ids}

    def assert_config_editable(self, key, row_id):
        if key not in {"product_id", "account_id", "preset_id"}:
            raise ValueError("Campo invalido")
        if self.conn.execute(f"SELECT 1 FROM publisher_drafts WHERE {key}=? AND status IN ('PREPARING','READY_FOR_REVIEW') LIMIT 1", (row_id,)).fetchone():
            raise ValueError("Configuracao em uso por uma preparacao ou revisao aberta")

    def validate_fields(self, fields):
        if 'headline_snapshot_json' in fields:
            blockers = headlines.readiness(headlines.read_snapshot(fields))
            if blockers:
                raise ValueError(blockers[0])
        product = self.get_product(fields['product_id'])
        account = get_row(self.conn, "publisher_accounts", fields["account_id"])
        active = self.conn.execute("SELECT active,account_id FROM publisher_products WHERE product_id=?", (product["id"],)).fetchone()
        if not account["active"] or (active and not active["active"]):
            raise ValueError("Produto ou conta inativo")
        if active and active["account_id"] and active["account_id"] != account["id"]:
            raise ValueError("Produto restrito a outra conta")
        if fields.get("preset_id"):
            preset = get_row(self.conn, "publisher_presets", fields["preset_id"])
            if preset["account_id"] and preset["account_id"] != account["id"]:
                raise ValueError("Preset pertence a outra conta")
        if len(str(fields.get("caption") or "")) > 2200:
            raise ValueError("Legenda excede 2200 caracteres")
        if fields.get("scheduled_at"):
            when = datetime.fromisoformat(fields["scheduled_at"])
            if when.tzinfo is None:
                raise ValueError("Horario precisa incluir fuso")
        return product

    def create_batch(self, data, files, source="MANUAL"):
        fields = {k: data.get(k) or None for k in ("product_id", "account_id", "preset_id", "scheduled_at")}
        fields["account_id"] = fields["account_id"] or self.default_account_id()
        fields["caption"] = data.get("caption", "")
        product = self.validate_fields(fields)
        if not fields["caption"]:
            fields["caption"] = product.get("default_caption") or ""
        self.validate_fields(fields)
        require_batch_size(len(files))
        paths = []
        for filename, path in files:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(self.root / "data" / "publisher" / "uploads") or resolved.suffix.lower() != ".mp4" or not resolved.is_file() or not resolved.stat().st_size:
                raise ValueError("Arquivo MP4 local invalido")
            paths.append((Path(filename).name, str(resolved.relative_to(self.root))))
        headline_selections = data.get('headline_selections') or {}
        filenames = [filename for filename, _ in paths]
        if (not isinstance(headline_selections, dict) or set(headline_selections) - set(filenames) or
                any(filenames.count(filename) != 1 for filename in headline_selections)):
            raise ValueError('HEADLINE_SELECTION_INVALID')
        timestamp = now_iso()
        with self.conn:
            batch = self.conn.execute("INSERT INTO publisher_batches(name,source,created_at) VALUES (?,?,?)", (data.get("name") or f"Lote {timestamp[:16]}", source, timestamp)).lastrowid
            ids = []
            for position, (filename, path) in enumerate(paths, 1):
                draft = self.conn.execute("INSERT INTO publisher_drafts(batch_id,product_id,account_id,preset_id,video_path,filename,caption,scheduled_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                          (batch, fields["product_id"], fields["account_id"], fields["preset_id"], path, filename, fields["caption"], fields["scheduled_at"], timestamp, timestamp)).lastrowid
                self.conn.execute('UPDATE publisher_drafts SET queue_position=? WHERE id=?', (position, draft))
                headlines.capture(self.conn, draft, product, headline_selections.get(filename))
                log(self.conn, draft, "CREATE", "DRAFT", f"Entrada: {source}")
                ids.append(draft)
        return {"batch_id": batch, "draft_ids": ids}

    def bulk(self, ids, fields, action="edit"):
        ids = list(dict.fromkeys(int(i) for i in ids))
        if not ids or len(ids) > 100:
            raise ValueError("Selecione entre 1 e 100 drafts")
        if action not in {"edit", "use_product_caption", "archive", "duplicate", "reset", "published", "scheduled"}:
            raise ValueError("Acao invalida")
        allowed = {"product_id", "account_id", "preset_id", "caption", "scheduled_at"}
        if set(fields) - allowed:
            raise ValueError("Campos invalidos")
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            for row_id in ids:
                row = get_row(self.conn, "publisher_drafts", row_id)
                media = get_row(self.conn, 'publisher_media_assets', row['media_asset_id']) if row['media_asset_id'] else None
                capabilities = draft_queue_capabilities(self.conn, row, media)
                if action in {'published', 'scheduled'}:
                    from src.publisher.posting_review import posting_eligibility
                    eligible, reason = posting_eligibility(self.conn, row, media)
                    if not eligible:
                        raise ValueError(reason)
                if action == 'archive' and not capabilities['can_remove']:
                    raise ValueError('QUEUE_REMOVE_BLOCKED')
                if action in {'edit', 'use_product_caption', 'duplicate'} and not capabilities['can_edit']:
                    raise ValueError('QUEUE_EDIT_BLOCKED')
                if row.get('persistence_status') == 'PERSISTED' and action in {'edit','duplicate','use_product_caption','reset','archive'}:
                    raise ValueError('DRAFT_ALREADY_PERSISTED')
                if row.get('media_asset_id') and action == 'duplicate':
                    raise ValueError('Selecione novas midias na caixa do Drive; nao e permitido duplicar o mesmo video')
                if action in {"edit", "duplicate", "use_product_caption"}:
                    if row['status']=='STUDIO_CHANGED':
                        raise ValueError('Use reset explicito apos validacao dos checkpoints')
                    if row["status"] not in EDITABLE:
                        raise ValueError("Encerre a revisao antes de editar ou duplicar")
                    merged = {**row, **fields}
                    if action=='use_product_caption':
                        merged['caption']=self.get_product(row['product_id'])['default_caption'] or ''
                    self.validate_fields(merged)
                    if action != "duplicate":
                        self.conn.execute("UPDATE publisher_drafts SET product_id=?,account_id=?,preset_id=?,caption=?,scheduled_at=?,status='DRAFT',error_code=NULL,checklist_json='{}',updated_at=? WHERE id=?",
                                          (*[merged[k] for k in ("product_id", "account_id", "preset_id", "caption", "scheduled_at")], now_iso(), row_id))
                        log(self.conn, row_id, "EDIT", "DRAFT", "Configuracao atualizada")
                        if not str(merged['caption'] or '').strip():
                            self.conn.execute("UPDATE publisher_drafts SET status='CAPTION_MISSING',error_code='CAPTION_MISSING' WHERE id=?",(row_id,))
                    else:
                        position = self.conn.execute('SELECT COALESCE(MAX(queue_position),0)+1 FROM publisher_drafts WHERE batch_id=?', (merged['batch_id'],)).fetchone()[0]
                        self.conn.execute("INSERT INTO publisher_drafts(batch_id,product_id,account_id,preset_id,video_path,filename,caption,scheduled_at,queue_position,created_at,updated_at,headline_snapshot_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                          (*[merged[k] for k in ("batch_id", "product_id", "account_id", "preset_id", "video_path", "filename", "caption", "scheduled_at")], position, now_iso(), now_iso(), row.get('headline_snapshot_json')))
                else:
                    target = {"archive": "ARCHIVED", "reset": "DRAFT", "published": "PUBLISHED", "scheduled": "SCHEDULED"}[action]
                    transition(self.conn, row_id, target, explicit_reset=action=='reset',
                               checklist=json.loads(row['checklist_json'] or '{}') if action in {'published', 'scheduled'} else None)
                    if action in {'published', 'scheduled'}:
                        log(self.conn, row_id, 'MANUAL_POSTING_REVIEW', target,
                            json.dumps({'human_confirmed': True, 'draft_id': row_id,
                                        'media_asset_id': row.get('media_asset_id'),
                                        'previous_state': row['status'], 'resulting_state': target, 'remote_actions': 0}))
                    if action == 'archive' and row.get('media_asset_id'):
                        self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE',updated_at=? WHERE id=? AND status IN ('RESERVED','FAILED')", (now_iso(),row['media_asset_id']))
                    if action == 'reset' and row.get('media_asset_id'):
                        self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE',cleanup_error=NULL,updated_at=? WHERE id=? AND status='FAILED'", (now_iso(),row['media_asset_id']))
                        self.conn.execute("UPDATE publisher_drafts SET temp_path=NULL,upload_status='NOT_STARTED',processing_status='NOT_STARTED' WHERE id=?", (row_id,))
                        log(self.conn, row_id, 'RETRY_RECONCILED_BEFORE_UPLOAD', 'OK',
                            'Falha STUDIO_CHANGED antes de qualquer checkpoint de upload; nova tentativa JIT autorizada')
