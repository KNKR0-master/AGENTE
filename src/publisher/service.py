from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

from src.store import now_iso


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
    columns = {r[1] for r in conn.execute('PRAGMA table_info(publisher_products)')}
    for name in ('name','short_name','default_caption','legacy_source','legacy_product_id','legacy_shop_url','created_at','updated_at'):
        if name not in columns:
            conn.execute(f'ALTER TABLE publisher_products ADD COLUMN {name} TEXT')
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
          x.active,x.account_id,x.legacy_source,x.legacy_product_id,x.legacy_shop_url,x.created_at,x.updated_at
          FROM publisher_products x ORDER BY x.name"""):
            item = dict(row)
            item["aliases"] = [r[0] for r in self.conn.execute("SELECT alias FROM product_aliases WHERE product_id=?", (item["id"],))]
            result.append(item)
        return result

    def get_product(self, product_id):
        row = self.conn.execute('SELECT *,product_id AS id FROM publisher_products WHERE product_id=?',(int(product_id),)).fetchone()
        if not row:
            raise ValueError('Produto interno nao encontrado')
        return dict(row)

    def snapshot(self):
        drafts = [dict(r) for r in self.conn.execute("""SELECT d.*,p.name product_name,a.name account_name,
          m.status media_status,m.remote_file_id,m.cleanup_error
          FROM publisher_drafts d JOIN publisher_products p ON p.product_id=d.product_id
          LEFT JOIN publisher_media_assets m ON m.id=d.media_asset_id
          JOIN publisher_accounts a ON a.id=d.account_id ORDER BY d.id DESC""")]
        for draft in drafts:
            draft["checklist"] = json.loads(draft.pop("checklist_json"))
        return {"products": self.catalog(), "drafts": drafts, "statuses": STATUSES,
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

    def save_product(self, data):
        if data.get('id'):
            data = {**self.get_product(data['id']), **data}
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Nome do produto obrigatorio")
        product_id = int(data["id"]) if data.get("id") else None
        if product_id:
            self.get_product(product_id)
        caption = str(data.get('default_caption') or '')
        if len(caption)>2200:
            raise ValueError('Legenda excede 2200 caracteres')
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
              (product_id,name,short_name,default_caption,account_id,active,created_at,updated_at)
              VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(product_id) DO UPDATE SET
              name=excluded.name,short_name=excluded.short_name,default_caption=excluded.default_caption,
              account_id=excluded.account_id,active=excluded.active,updated_at=excluded.updated_at""",
              (product_id,name,str(data.get('short_name') or '').strip(),caption,account_id,int(data.get('active',True)),timestamp,timestamp))
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
                profile = f"data/publisher/browser_profiles/{uuid.uuid4().hex}"
                row_id = self.conn.execute("INSERT INTO publisher_accounts(name,browser_profile,created_at,updated_at) VALUES (?,?,?,?)", (name, profile, timestamp, timestamp)).lastrowid
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
