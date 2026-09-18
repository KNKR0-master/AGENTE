from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from src.store import now_iso
from src.publisher.library import get_library_items, list_library, save_decision, set_product_overrides
from src.publisher.folder_catalog import (
    apply_bulk_role,
    decide_folder_role,
    grouped_folder_reviews,
    list_folder_reviews,
    reject_suggested_match,
    set_container_auto_create,
)


LEGACY_ERRORS = {"PRODUCT_NOT_FOUND", "PRODUCT_NOT_CONFIRMED"}
ERRORS = {"NEEDS_LOGIN", "CAPTION_MISSING", "UPLOAD_FAILED",
          "STUDIO_CHANGED", "MANUAL_ACTION_REQUIRED", "FAILED"}
EDITABLE = {"DRAFT", *ERRORS, *LEGACY_ERRORS}
STATUSES = ["DRAFT", "PREPARING", "READY_FOR_REVIEW", "SCHEDULED", "PUBLISHED", *sorted(ERRORS), *sorted(LEGACY_ERRORS), "ARCHIVED"]


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
    CREATE TABLE IF NOT EXISTS publisher_batch_executions (
      id INTEGER PRIMARY KEY, batch_id INTEGER, total INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'QUEUED', started_at TEXT NOT NULL, finished_at TEXT,
      auto_cleanup INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS publisher_execution_items (
      execution_id INTEGER NOT NULL REFERENCES publisher_batch_executions(id),
      draft_id INTEGER NOT NULL REFERENCES publisher_drafts(id),
      status TEXT NOT NULL DEFAULT 'QUEUED', error_code TEXT,
      PRIMARY KEY(execution_id,draft_id));
    """)
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
    }.items():
        if name not in columns:
            conn.execute(f'ALTER TABLE publisher_drafts ADD COLUMN {name} {definition}')
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_batch_executions)')}
    if 'auto_cleanup' not in columns:
        conn.execute("ALTER TABLE publisher_batch_executions ADD COLUMN auto_cleanup INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS publisher_media_reservation ON publisher_drafts(media_asset_id) WHERE media_asset_id IS NOT NULL AND status<>'ARCHIVED'")
    conn.execute("CREATE INDEX IF NOT EXISTS publisher_media_decisions_decision ON publisher_media_decisions(decision, updated_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS publisher_media_overrides_product ON publisher_media_overrides(product_id, updated_at)")
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
    if table not in {"products", "publisher_accounts", "publisher_presets", "publisher_drafts", "publisher_media_assets"}:
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


class PublisherService:
    def __init__(self, conn, root):
        self.conn = conn
        self.root = Path(root).resolve()

    def catalog(self):
        result = []
        for row in self.conn.execute("""SELECT x.product_id id,x.name,x.short_name,x.default_caption,
          x.flow_prompt,x.active,x.account_id,x.legacy_source,x.legacy_product_id,x.legacy_shop_url,
          x.name_source,x.source_drive_folder_id,x.created_at,x.updated_at
          FROM publisher_products x ORDER BY x.name"""):
            item = dict(row)
            item["aliases"] = [r[0] for r in self.conn.execute("SELECT alias FROM product_aliases WHERE product_id=?", (item["id"],))]
            item["folders"] = self.list_drive_folders(item["id"])
            item["reference_count"], item["reference_types"] = self.product_reference_summary(item["id"])
            result.append(item)
        return result

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
                "can_delete": count == 0, "will_archive": count > 0,
            })
        return {
            "items": items,
            "selected": len(items),
            "will_delete": sum(item["can_delete"] for item in items),
            "will_archive": sum(item["will_archive"] for item in items),
        }

    def bulk_products(self, product_ids, action, *, human_confirmed=False):
        if action not in {"archive", "delete"}:
            raise ValueError("Acao de produto invalida")
        plan = self.product_bulk_plan(product_ids)
        if human_confirmed is not True:
            raise ValueError("CONFIRM_PRODUCT_ACTION")
        timestamp = now_iso()
        with self.conn:
            for item in plan["items"]:
                if action == "archive" or not item["can_delete"]:
                    self.conn.execute(
                        "UPDATE publisher_products SET active=0,updated_at=? WHERE product_id=?",
                        (timestamp, item["id"]),
                    )
                else:
                    self.conn.execute("DELETE FROM publisher_products WHERE product_id=?", (item["id"],))
                    self.conn.execute("DELETE FROM products WHERE id=?", (item["id"],))
        return {**plan, "action": action, "archived": plan["selected"] if action == "archive" else plan["will_archive"],
                "deleted": 0 if action == "archive" else plan["will_delete"]}

    def set_product_folder(self, product_id, folder_id, action="add"):
        product_id = int(product_id)
        self.get_product(product_id)
        folder_id = str(folder_id or "").strip()
        if not folder_id:
            raise ValueError("Pasta do Drive obrigatoria")
        folder = self.conn.execute(
            "SELECT product_id FROM drive_folders WHERE drive_folder_id=? AND operational_status='ACTIVE'",
            (folder_id,),
        ).fetchone()
        if not folder:
            raise ValueError("Pasta do Drive nao encontrada")
        if action == "remove":
            target = None
        elif action == "add":
            target = product_id
        else:
            raise ValueError("Acao de pasta invalida")
        from src.drive_inventory import set_folder_mapping
        set_folder_mapping(self.conn, folder_id, target, reason=f"Publisher product folder {action}")
        return {"product_id": product_id, "folder_id": folder_id, "action": action}

    def snapshot(self):
        drafts = [dict(r) for r in self.conn.execute("""SELECT d.*,p.name product_name,a.name account_name,
          m.status media_status,m.remote_file_id,m.cleanup_error
          FROM publisher_drafts d JOIN publisher_products p ON p.product_id=d.product_id
          LEFT JOIN publisher_media_assets m ON m.id=d.media_asset_id
          JOIN publisher_accounts a ON a.id=d.account_id ORDER BY d.id DESC""")]
        for draft in drafts:
            draft["checklist"] = json.loads(draft.pop("checklist_json"))
        return {"products": self.catalog(), "preferences": self.get_preferences(),
                "drive_folders": self.list_drive_folders(), "drafts": drafts, "statuses": STATUSES,
                "media_assets": [dict(r) for r in self.conn.execute("SELECT * FROM publisher_media_assets ORDER BY filename")],
                "executions": [dict(r) for r in self.conn.execute("""SELECT e.*,
                  SUM(i.status='QUEUED') queued,SUM(i.status IN ('PROCESSING','CLEANUP')) processing,
                  SUM(i.status='SUCCEEDED') succeeded,SUM(i.status='FAILED') failed,
                  SUM(i.status='WAITING_MANUAL') waiting_manual,
                  SUM(m.status='CONSUMED') remote_deleted,
                  SUM(m.status='CONSUMED_PENDING_DELETE') remote_delete_pending,
                  SUM(m.status NOT IN ('CONSUMED','CONSUMED_PENDING_DELETE')) remote_preserved
                  FROM publisher_batch_executions e JOIN publisher_execution_items i ON i.execution_id=e.id
                  JOIN publisher_drafts d ON d.id=i.draft_id
                  LEFT JOIN publisher_media_assets m ON m.id=d.media_asset_id
                  GROUP BY e.id ORDER BY e.id DESC LIMIT 30""")],
                **{name: [dict(r) for r in self.conn.execute(f"SELECT * FROM publisher_{name} ORDER BY id DESC")]
                   for name in ("accounts", "presets", "batches")},
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

    def assert_media_available(self, asset, draft_id=None):
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
        if not ids or len(ids) > 100:
            raise ValueError('Selecione entre 1 e 100 videos remotos')
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
            for asset_id in ids:
                asset = get_row(self.conn, 'publisher_media_assets', asset_id)
                self.assert_media_available(asset)
                row_id = self.conn.execute("""INSERT INTO publisher_drafts
                  (batch_id,product_id,account_id,preset_id,media_asset_id,video_path,filename,caption,scheduled_at,created_at,updated_at)
                  VALUES (?,?,?,?,?,'',?,?,?,?,?)""",
                  (batch, fields['product_id'],fields['account_id'],fields['preset_id'],asset_id,asset['filename'],
                   fields['caption'],fields['scheduled_at'],timestamp,timestamp)).lastrowid
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

    def delete_library_media(self, data, provider):
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
        if item["filename"] != filename or (data.get("size") is not None and int(data["size"]) != int(item.get("size") or 0)):
            raise ValueError("MEDIA_IDENTITY_MISMATCH")
        if item.get("media_status") in {"RESERVED", "PROCESSING", "CONSUMED_PENDING_DELETE", "CONSUMED"} or item.get("draft_id"):
            raise ValueError("Mídia vinculada a draft; finalize o lifecycle pelo Publisher")
        asset = {
            "provider": "google_drive", "source_account": "cecilia",
            "remote_file_id": drive_file_id, "filename": item["filename"],
            "size_bytes": int(item.get("size") or 0), "checksum": item.get("md5_checksum"),
        }
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

    def create_library_batch(self, data):
        ids = list(dict.fromkeys(str(value) for value in (data.get("drive_file_ids") or []) if str(value).strip()))
        if not ids or len(ids) > 100:
            raise ValueError("Selecione entre 1 e 100 vídeos")
        if data.get("scheduled_at"):
            raise ValueError("A Biblioteca não agenda lotes; horário desejado não é definido")
        account_id = data.get("account_id") or self.default_account_id()
        if not account_id:
            raise ValueError("Selecione uma conta Publisher ativa")
        preset_id = data.get("preset_id") or None
        items = get_library_items(self.conn, ids)
        by_id = {item["drive_file_id"]: item for item in items}
        missing = [file_id for file_id in ids if file_id not in by_id]
        problems = [{"drive_file_id": file_id, "reason": "Arquivo não encontrado no inventário"} for file_id in missing]
        for file_id in ids:
            item = by_id.get(file_id)
            if item and not item.get("eligible"):
                problems.append({"drive_file_id": file_id, "filename": item["filename"], "reason": item["reason"] or item["status_label"]})
            if item and item.get("eligible"):
                try:
                    self.validate_fields({"product_id": item["product_id"], "account_id": account_id,
                                          "preset_id": preset_id, "caption": item["caption"]})
                except ValueError as exc:
                    problems.append({"drive_file_id": file_id, "filename": item["filename"], "reason": str(exc)})
        if problems:
            raise ValueError("BATCH_PREFLIGHT_FAILED: " + json.dumps(problems, ensure_ascii=False))
        timestamp = now_iso()
        with self.conn:
            self.conn.execute("BEGIN IMMEDIATE")
            batch_id = self.conn.execute(
                "INSERT INTO publisher_batches(name,source,created_at) VALUES (?, 'MEDIA_LIBRARY', ?)",
                (str(data.get("name") or f"Lote {timestamp[:16]}"), timestamp),
            ).lastrowid
            draft_ids = []
            for file_id in ids:
                item = by_id[file_id]
                asset = self.conn.execute("SELECT * FROM publisher_media_assets WHERE remote_file_id=?", (file_id,)).fetchone()
                if asset:
                    asset = dict(asset)
                    self.assert_media_available(asset)
                    asset_id = asset["id"]
                else:
                    asset_id = self.conn.execute("""INSERT INTO publisher_media_assets
                      (provider,source_account,remote_file_id,remote_folder_id,filename,mime_type,size_bytes,checksum,
                       folder_path,web_view_link,remote_created_at,remote_modified_at,last_seen_at,created_at,updated_at)
                      VALUES ('google_drive','cecilia',?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (file_id, item["folder_id"], item["filename"], item["mime_type"] or "video/mp4", item.get("size"),
                       item.get("md5_checksum"), item.get("folder_path"), item.get("web_view_link"), item.get("created_time"),
                       item.get("modified_time"), timestamp, timestamp, timestamp)).lastrowid
                    asset = dict(self.conn.execute("SELECT * FROM publisher_media_assets WHERE id=?", (asset_id,)).fetchone())
                draft_id = self.conn.execute("""INSERT INTO publisher_drafts
                  (batch_id,product_id,account_id,preset_id,media_asset_id,video_path,filename,caption,scheduled_at,created_at,updated_at)
                  VALUES (?,?,?,?,?,'',?,?,?,?,?)""",
                  (batch_id, item["product_id"], int(account_id), preset_id, asset_id, item["filename"], item["caption"], None, timestamp, timestamp)).lastrowid
                self.conn.execute("UPDATE publisher_media_assets SET status='RESERVED',updated_at=? WHERE id=?", (timestamp, asset_id))
                log(self.conn, draft_id, "CREATE", "DRAFT", "Biblioteca: referência Drive reservada; nenhum download realizado")
                draft_ids.append(draft_id)
        return {"batch_id": batch_id, "draft_ids": draft_ids, "items": [{"drive_file_id": i["drive_file_id"], "product_id": i["product_id"], "filename": i["filename"]} for i in (by_id[file_id] for file_id in ids)]}

    def save_product(self, data):
        if data.get('id'):
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
        if account_id:
            get_row(self.conn, "publisher_accounts", account_id)
        timestamp = now_iso()
        with self.conn:
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

    def assert_config_editable(self, key, row_id):
        if key not in {"product_id", "account_id", "preset_id"}:
            raise ValueError("Campo invalido")
        if self.conn.execute(f"SELECT 1 FROM publisher_drafts WHERE {key}=? AND status IN ('PREPARING','READY_FOR_REVIEW') LIMIT 1", (row_id,)).fetchone():
            raise ValueError("Configuracao em uso por uma preparacao ou revisao aberta")

    def validate_fields(self, fields):
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
        if not files or len(files) > 100:
            raise ValueError("Selecione entre 1 e 100 videos")
        paths = []
        for filename, path in files:
            resolved = Path(path).resolve()
            if not resolved.is_relative_to(self.root / "data" / "publisher" / "uploads") or resolved.suffix.lower() != ".mp4" or not resolved.is_file() or not resolved.stat().st_size:
                raise ValueError("Arquivo MP4 local invalido")
            paths.append((Path(filename).name, str(resolved.relative_to(self.root))))
        timestamp = now_iso()
        with self.conn:
            batch = self.conn.execute("INSERT INTO publisher_batches(name,source,created_at) VALUES (?,?,?)", (data.get("name") or f"Lote {timestamp[:16]}", source, timestamp)).lastrowid
            ids = []
            for filename, path in paths:
                draft = self.conn.execute("INSERT INTO publisher_drafts(batch_id,product_id,account_id,preset_id,video_path,filename,caption,scheduled_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                          (batch, fields["product_id"], fields["account_id"], fields["preset_id"], path, filename, fields["caption"], fields["scheduled_at"], timestamp, timestamp)).lastrowid
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
                        self.conn.execute("INSERT INTO publisher_drafts(batch_id,product_id,account_id,preset_id,video_path,filename,caption,scheduled_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                          (*[merged[k] for k in ("batch_id", "product_id", "account_id", "preset_id", "video_path", "filename", "caption", "scheduled_at")], now_iso(), now_iso()))
                else:
                    target = {"archive": "ARCHIVED", "reset": "DRAFT", "published": "PUBLISHED", "scheduled": "SCHEDULED"}[action]
                    transition(self.conn, row_id, target, explicit_reset=action=='reset')
                    if action == 'archive' and row.get('media_asset_id'):
                        self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE',updated_at=? WHERE id=? AND status IN ('RESERVED','FAILED')", (now_iso(),row['media_asset_id']))
                    if action == 'reset' and row.get('media_asset_id'):
                        self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE',cleanup_error=NULL,updated_at=? WHERE id=? AND status='FAILED'", (now_iso(),row['media_asset_id']))
                        self.conn.execute("UPDATE publisher_drafts SET temp_path=NULL,upload_status='NOT_STARTED',processing_status='NOT_STARTED' WHERE id=?", (row_id,))
                        log(self.conn, row_id, 'RETRY_RECONCILED_BEFORE_UPLOAD', 'OK',
                            'Falha STUDIO_CHANGED antes de qualquer checkpoint de upload; nova tentativa JIT autorizada')
