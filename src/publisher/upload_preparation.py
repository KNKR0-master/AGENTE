"""Single local upload-source decision. No browser or Drive operations."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile

from src.publisher.headlines import canonical, read_snapshot, readiness
from src.publisher.headline_tools import RenderError, sha256, probe
from src.store import now_iso


def init_upload_preparation(conn):
    from src.publisher.headline_renderer import init_artifacts
    init_artifacts(conn)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS publisher_upload_sources (
      id INTEGER PRIMARY KEY, draft_id INTEGER NOT NULL REFERENCES publisher_drafts(id),
      execution_id INTEGER, kind TEXT NOT NULL CHECK(kind IN ('ORIGINAL','HEADLINE_DERIVED')),
      source_sha256 TEXT NOT NULL, render_spec_hash TEXT, artifact_id TEXT,
      artifact_sha256 TEXT, upload_filename TEXT NOT NULL, probe_json TEXT,
      created_at TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS upload_source_no_update BEFORE UPDATE ON publisher_upload_sources
      BEGIN SELECT RAISE(ABORT,'UPLOAD_SOURCE_IMMUTABLE'); END;
    CREATE TRIGGER IF NOT EXISTS upload_source_no_delete BEFORE DELETE ON publisher_upload_sources
      BEGIN SELECT RAISE(ABORT,'UPLOAD_SOURCE_IMMUTABLE'); END;
    ''')
    if 'prepared_upload_source_id' not in {r[1] for r in conn.execute('PRAGMA table_info(publisher_drafts)')}:
        conn.execute('ALTER TABLE publisher_drafts ADD COLUMN prepared_upload_source_id INTEGER REFERENCES publisher_upload_sources(id)')
    conn.execute("INSERT OR IGNORE INTO publisher_migrations VALUES ('headline_upload_preparation_v1',?)", (now_iso(),))


def artifact_root(root):
    scope = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:24]
    return Path(tempfile.gettempdir()).resolve() / 'PublisherDesk' / 'headline_artifacts' / scope


def configured_renderer(root, input_root, cancel_event):
    from src.publisher.headline_configuration import create_renderer
    return create_renderer(root, input_root, cancel_event)


def check_cancel(cancel_event):
    if cancel_event is not None and cancel_event.is_set():
        raise RenderError('HEADLINE_PREPARATION_CANCELLED')


@dataclass(frozen=True)
class PreparedUploadSource:
    path: Path
    kind: str
    source_path: Path
    source_sha256: str
    record_id: int
    artifact_id: str | None = None
    artifact_sha256: str | None = None
    render_spec_hash: str | None = None
    renderer: object = None

    def validate(self, conn, draft, cancel_event=None):
        check_cancel(cancel_event)
        if not self.source_path.is_file() or sha256(self.source_path) != self.source_sha256:
            raise RenderError('HEADLINE_SOURCE_IDENTITY_MISMATCH')
        if self.kind == 'ORIGINAL':
            if read_snapshot(draft)['enabled']:
                raise RenderError('HEADLINE_ORIGINAL_FALLBACK_FORBIDDEN')
            return
        from src.publisher.headline_renderer import inside
        row = conn.execute('SELECT * FROM publisher_derived_media_artifacts WHERE id=?', (self.artifact_id,)).fetchone()
        snapshot = read_snapshot(draft)
        if readiness(snapshot):
            raise RenderError('HEADLINE_CONFIG_INVALID')
        if (not row or row['kind'] != 'video' or row['state'] != 'READY' or row['draft_id'] != draft['id'] or
                row['renderer_version'] != self.renderer.version or
                row['source_sha256'] != self.source_sha256 or row['render_spec_hash'] != snapshot['render_spec_hash'] or
                row['render_spec_hash'] != self.render_spec_hash or row['output_sha256'] != self.artifact_sha256 or
                Path(row['local_path']).resolve() != self.path.resolve()):
            raise RenderError('HEADLINE_ARTIFACT_IDENTITY_MISMATCH')
        inside(self.path, self.renderer.artifact_root)
        inside(self.source_path, self.renderer.input_root)
        if not self.path.is_file() or sha256(self.path) != self.artifact_sha256 or self.path == self.source_path:
            raise RenderError('HEADLINE_ARTIFACT_INVALID')
        source = probe(self.renderer.ffprobe, self.source_path, cancel_event=cancel_event)
        self.renderer.validate_file(source, self.source_path, self.path, 'video')
        check_cancel(cancel_event)


def prepare_upload_source(conn, draft, source_path, renderer_factory, cancel_event=None):
    from src.publisher.service import assert_upload_allowed, log
    assert_upload_allowed(conn, draft['id'])
    check_cancel(cancel_event)
    snapshot = read_snapshot(draft)
    errors = readiness(snapshot)
    if errors:
        raise RenderError(errors[0])
    source_path = Path(source_path).resolve()
    digest = sha256(source_path)
    artifact, renderer = None, None
    if snapshot['enabled']:
        renderer = renderer_factory()
        def record_stage(event):
            with conn:
                log(conn, draft['id'], 'HEADLINE_STAGE',
                    'BLOCKED' if event['error_code'] else 'OK', canonical(event))
        renderer.stage_observer = record_stage
        # The owning worker serializes preparations. This is reached only before
        # any upload effect, never from an ambiguous transfer/reconciliation path.
        with conn:
            conn.execute("UPDATE publisher_derived_media_artifacts SET state='FAILED',error_code='HEADLINE_RENDER_INTERRUPTED' WHERE draft_id=? AND kind='video' AND state='GENERATING'", (draft['id'],))
            log(conn, draft['id'], 'HEADLINE_RENDER_STARTED', 'OK', 'Preparacao local; nenhum upload iniciado')
        artifact = renderer.render(conn, draft['id'], snapshot, source_path, kind='video')
        if artifact.get('kind') != 'video' or artifact.get('state') != 'READY':
            raise RenderError('HEADLINE_ARTIFACT_INVALID')
    check_cancel(cancel_event)
    path = Path(artifact['local_path']) if artifact else source_path
    kind = 'HEADLINE_DERIVED' if artifact else 'ORIGINAL'
    execution = conn.execute("SELECT execution_id FROM publisher_execution_items WHERE draft_id=? AND status IN ('QUEUED','PROCESSING') ORDER BY execution_id DESC LIMIT 1", (draft['id'],)).fetchone()
    with conn:
        record_id = conn.execute('INSERT INTO publisher_upload_sources(draft_id,execution_id,kind,source_sha256,render_spec_hash,artifact_id,artifact_sha256,upload_filename,probe_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (draft['id'], execution[0] if execution else None, kind, digest,
             snapshot['render_spec_hash'] if artifact else None, artifact['id'] if artifact else None,
             artifact['output_sha256'] if artifact else None, path.name, artifact['probe_json'] if artifact else None, now_iso())).lastrowid
    prepared = PreparedUploadSource(path, kind, source_path, digest, record_id,
        artifact['id'] if artifact else None, artifact['output_sha256'] if artifact else None,
        snapshot['render_spec_hash'] if artifact else None, renderer)
    prepared.validate(conn, draft, cancel_event)
    with conn:
        conn.execute('UPDATE publisher_drafts SET prepared_upload_source_id=? WHERE id=?', (record_id, draft['id']))
        log(conn, draft['id'], 'HEADLINE_ARTIFACT_READY' if artifact else 'UPLOAD_SOURCE_ORIGINAL', 'OK',
            canonical(dict(kind=kind, artifact_id=prepared.artifact_id, source_hash=digest[:12],
                           render_hash=(prepared.render_spec_hash or '')[:12], artifact_hash=(prepared.artifact_sha256 or '')[:12])))
    return prepared


def upload_source_record(conn, draft):
    record_id = draft.get('prepared_upload_source_id')
    if not record_id:
        return None
    row = conn.execute('SELECT * FROM publisher_upload_sources WHERE id=? AND draft_id=?', (record_id, draft['id'])).fetchone()
    return dict(row) if row else None


def reconciliation_source(conn, draft):
    """Durable description of what crossed the upload boundary, never Product data."""
    record = upload_source_record(conn, draft)
    if not record or record['kind'] != 'HEADLINE_DERIVED':
        return None
    media = json.loads(record['probe_json'])
    return dict(upload_source_id=record['id'], artifact_id=record['artifact_id'],
                source_sha256=record['source_sha256'], artifact_sha256=record['artifact_sha256'],
                render_spec_hash=record['render_spec_hash'], duration=media['duration'],
                thumbnail_authority='ACTUAL_UPLOADED_ARTIFACT', thumbnail_matching_used=False)


def cleanup_derived_after_consumption(conn, draft_id, root):
    row = conn.execute("SELECT d.persistence_status,m.status FROM publisher_drafts d JOIN publisher_media_assets m ON m.id=d.media_asset_id WHERE d.id=?", (draft_id,)).fetchone()
    if not row or row[0] != 'PERSISTED' or row[1] != 'CONSUMED':
        raise RenderError('HEADLINE_CLEANUP_NOT_ELIGIBLE')
    if conn.execute("SELECT 1 FROM publisher_execution_items WHERE draft_id=? AND status IN ('QUEUED','PROCESSING')", (draft_id,)).fetchone():
        raise RenderError('HEADLINE_CLEANUP_ACTIVE')
    items = conn.execute('SELECT id,local_path FROM publisher_derived_media_artifacts WHERE draft_id=?', (draft_id,)).fetchall()
    if not items:
        return True
    if root is None:
        raise RenderError('HEADLINE_CLEANUP_ROOT_REQUIRED')
    base = artifact_root(root).resolve()
    for item in items:
        if not re.fullmatch('[0-9a-f]{32}', item['id']):
            raise RenderError('HEADLINE_CLEANUP_PATH_INVALID')
        directory = (base / item['id']).resolve()
        if directory.parent != base or directory == base:
            raise RenderError('HEADLINE_CLEANUP_PATH_INVALID')
        if item['local_path'] and Path(item['local_path']).resolve().parent != directory:
            raise RenderError('HEADLINE_CLEANUP_PATH_INVALID')
        if directory.exists():
            shutil.rmtree(directory)
        with conn:
            conn.execute("UPDATE publisher_derived_media_artifacts SET state='FAILED',error_code='LOCAL_CLEANED_AFTER_CONSUMPTION' WHERE id=?", (item['id'],))
    return True
