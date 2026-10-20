"""Local MP4 jobs. No Draft, publication worker, browser or remote mutation API.

The additive job catalog lives beside Publisher tables. Renderer catalogs and
files live in a separate, scoped TEMP tree and never use remote Draft IDs.
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import queue
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid

from src.store import connect, now_iso
from src.publisher.headlines import canonical
from src.publisher.headline_tools import RenderError, probe, run, sha256
from src.publisher.media_history import assert_media_history_allowed

RETENTION_DAYS = 7
MAX_ITEMS = 20
MAX_BYTES = 2 * 1024**3
ACTIVE = ('QUEUED', 'PROCESSING')


def init_local_preparation(conn):
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS publisher_local_preparations (
      id TEXT PRIMARY KEY, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
      drive_file_id TEXT NOT NULL, identity_json TEXT NOT NULL,
      snapshot_json TEXT NOT NULL, filename TEXT NOT NULL,
      state TEXT NOT NULL, stage TEXT NOT NULL, error_code TEXT,
      events_json TEXT NOT NULL DEFAULT '[]', source_sha256 TEXT,
      output_sha256 TEXT, renderer_version TEXT, metadata_json TEXT,
      created_at TEXT NOT NULL, finished_at TEXT, expires_at TEXT,
      UNIQUE(request_id,drive_file_id));
    CREATE UNIQUE INDEX IF NOT EXISTS publisher_local_active_media
      ON publisher_local_preparations(drive_file_id)
      WHERE state IN ('QUEUED','PROCESSING');
    CREATE TRIGGER IF NOT EXISTS local_preparation_snapshot_immutable
      BEFORE UPDATE ON publisher_local_preparations
      WHEN NEW.id<>OLD.id OR NEW.request_id<>OLD.request_id
        OR NEW.request_hash<>OLD.request_hash OR NEW.drive_file_id<>OLD.drive_file_id
        OR NEW.identity_json<>OLD.identity_json OR NEW.snapshot_json<>OLD.snapshot_json
        OR NEW.filename<>OLD.filename OR NEW.created_at<>OLD.created_at
      BEGIN SELECT RAISE(ABORT,'LOCAL_PREPARATION_SNAPSHOT_IMMUTABLE'); END;
    ''')


def active_local_preparation(conn, file_id):
    return conn.execute("SELECT id FROM publisher_local_preparations WHERE drive_file_id=? "
                        "AND state IN ('QUEUED','PROCESSING') LIMIT 1", (file_id,)).fetchone()


def eligible_source(conn, file_id):
    from src.publisher.library import get_library_items
    items = get_library_items(conn, [file_id])
    if len(items) != 1:
        raise ValueError('LOCAL_MEDIA_IDENTITY_UNPROVEN')
    item = items[0]
    # Check canonical history even when a reservation or archive hides eligibility.
    assets = conn.execute("SELECT * FROM publisher_media_assets WHERE provider='google_drive' "
                          "AND source_account='cecilia' AND remote_file_id=?", (file_id,)).fetchall()
    for row in assets:
        assert_media_history_allowed(conn, dict(row), purpose='reserve')
        if row['status'] not in {'AVAILABLE', 'FAILED', 'RELEASED'}:
            raise ValueError('LOCAL_MEDIA_RESERVED_OR_UNAVAILABLE')
    if not item['eligible'] or item.get('product_archived') or item.get('mime_type') != 'video/mp4':
        raise ValueError('LOCAL_MEDIA_NOT_ELIGIBLE')
    inventory = conn.execute('SELECT modified_time FROM drive_files WHERE drive_file_id=?', (file_id,)).fetchone()
    identity = dict(provider='google_drive', source_account='cecilia', remote_file_id=file_id,
                    remote_folder_id=item['folder_id'], filename=item['filename'],
                    size_bytes=int(item.get('size') or 0), checksum=item.get('md5_checksum'),
                    remote_modified_at=inventory[0] if inventory else None)
    if not 0 < identity['size_bytes'] <= MAX_BYTES or not identity['remote_folder_id']:
        raise ValueError('LOCAL_SOURCE_SIZE_OR_IDENTITY_INVALID')
    return item, identity


def review(conn, data):
    from src.publisher.headline_workflow import selection_snapshot
    if not isinstance(data, dict) or set(data) - {'items', 'request_id', 'review_token'}:
        raise ValueError('LOCAL_REQUEST_INVALID')
    items = data.get('items')
    if not isinstance(items, list) or not 0 < len(items) <= MAX_ITEMS:
        raise ValueError('LOCAL_SELECTION_REQUIRED_OR_LIMIT_EXCEEDED')
    result, seen = [], set()
    for value in items:
        if not isinstance(value, dict) or set(value) - {'drive_file_id', 'headline'}:
            raise ValueError('LOCAL_ITEM_INVALID')
        file_id = value.get('drive_file_id')
        if not isinstance(file_id, str) or not file_id or file_id in seen:
            raise ValueError('LOCAL_MEDIA_SELECTION_INVALID')
        seen.add(file_id)
        item, identity = eligible_source(conn, file_id)
        row = conn.execute('SELECT * FROM publisher_products WHERE product_id=?', (item['product_id'],)).fetchone()
        if not row:
            raise ValueError('LOCAL_PRODUCT_UNAVAILABLE')
        product = {**dict(row), 'id': row['product_id']}
        snapshot = selection_snapshot(conn, product, value.get('headline'))
        result.append(dict(drive_file_id=file_id, filename=item['filename'],
                           identity=identity, snapshot=snapshot))
    token = hashlib.sha256(canonical(result).encode()).hexdigest()
    return dict(items=result, review_token=token, no_tiktok_upload=True,
                retention_days=RETENTION_DAYS)


def public_job(row):
    item = dict(row)
    snapshot = json.loads(item['snapshot_json'])
    expired = bool(item['expires_at'] and item['expires_at'] <= now_iso())
    return {key: item[key] for key in ('id', 'drive_file_id', 'filename', 'state', 'stage',
            'error_code', 'source_sha256', 'output_sha256', 'renderer_version', 'created_at',
            'finished_at', 'expires_at')} | dict(snapshot=snapshot,
            metadata=json.loads(item['metadata_json'] or '{}'), events=json.loads(item['events_json']),
            headline_applied=snapshot['enabled'], no_tiktok_upload=True, expired=expired,
            can_retry=item['state'] in {'FAILED', 'CANCELLED'},
            video_url=f"/api/publisher/local-preparations/{item['id']}/video"
                      if item['state'] == 'READY' and not expired else None)


def recover_local_preparations(root, db):
    """Startup recovery clears local claims without running or resuming work."""
    with closing(connect(db)) as conn:
        if not conn.execute("SELECT 1 FROM publisher_local_preparations WHERE state IN ('QUEUED','PROCESSING') LIMIT 1").fetchone():
            return
    manager = LocalPreparationManager(root, db)
    try:
        manager._own()
    finally:
        manager.shutdown()


class LocalPreparationManager:
    def __init__(self, root, db, *, reader_factory=None, renderer_factory=None):
        self.root, self.db = Path(root), Path(db)
        scope = hashlib.sha256(str(self.db.resolve()).encode()).hexdigest()[:24]
        self.files = Path(tempfile.gettempdir()).resolve() / 'PublisherDesk' / 'local_preparation' / scope
        self.reader_factory = reader_factory
        self.renderer_factory = renderer_factory
        self.lock = threading.RLock()
        self.commands = queue.Queue()
        self.cancels = {}
        self.stopping = threading.Event()
        self.thread = None
        self.lease = None

    def _own(self):
        from src.publisher.media import lock_lease
        if self.lease:
            return
        self.files.mkdir(parents=True, exist_ok=True)
        lease = (self.files / '.owner').open('a+b')
        if lease.seek(0, 2) == 0:
            lease.write(b'0'); lease.flush()
        try:
            lock_lease(lease)
        except OSError as exc:
            lease.close()
            raise ValueError('LOCAL_PREPARATION_BUSY') from exc
        self.lease = lease
        # Only the exclusive owner recovers interrupted local jobs. Never resume.
        with closing(connect(self.db)) as conn, conn:
            conn.execute("UPDATE publisher_local_preparations SET state='FAILED',error_code='LOCAL_SERVER_RESTARTED',"
                         "finished_at=? WHERE state IN ('QUEUED','PROCESSING')", (now_iso(),))

    def list(self):
        with self.lock:
            self._own()
        with closing(connect(self.db)) as conn:
            return {'jobs': [public_job(r) for r in conn.execute(
                "SELECT * FROM publisher_local_preparations ORDER BY (state='REMOVED'),created_at DESC,id DESC LIMIT 100")],
                'retention_days': RETENTION_DAYS, 'no_tiktok_upload': True}

    def enqueue(self, data):
        request_id = data.get('request_id')
        if not isinstance(request_id, str) or not re.fullmatch('[a-f0-9]{32}', request_id):
            raise ValueError('LOCAL_REQUEST_ID_REQUIRED')
        with self.lock:
            if self.stopping.is_set():
                raise ValueError('LOCAL_PREPARATION_STOPPING')
            self._own()
            with closing(connect(self.db)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                previous = conn.execute('SELECT * FROM publisher_local_preparations WHERE request_id=?', (request_id,)).fetchall()
                checked = review(conn, data)
                if data.get('review_token') != checked['review_token']:
                    raise ValueError('LOCAL_REVIEW_CHANGED')
                if previous:
                    if any(r['request_hash'] != checked['review_token'] for r in previous):
                        raise ValueError('LOCAL_REQUEST_ID_CONFLICT')
                    return {'jobs': [public_job(r) for r in previous], 'reused': True}
                ids = []
                for item in checked['items']:
                    if active_local_preparation(conn, item['drive_file_id']):
                        raise ValueError('LOCAL_MEDIA_BUSY')
                    job_id = uuid.uuid4().hex
                    conn.execute('INSERT INTO publisher_local_preparations '
                        '(id,request_id,request_hash,drive_file_id,identity_json,snapshot_json,filename,state,stage,created_at) '
                        "VALUES (?,?,?,?,?,?,?,'QUEUED','PREPARING_SOURCE',?)", (job_id, request_id,
                        checked['review_token'], item['drive_file_id'], canonical(item['identity']),
                        canonical(item['snapshot']), item['filename'], now_iso()))
                    ids.append(job_id)
            for job_id in ids:
                self.cancels[job_id] = threading.Event()
                self.commands.put(job_id)
            self._start()
            return {'jobs': [j for j in self.list()['jobs'] if j['id'] in ids], 'reused': False}

    def _start(self):
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, name='publisher-local-mp4', daemon=True)
            self.thread.start()

    def _run(self):
        while not self.stopping.is_set():
            try:
                job_id = self.commands.get(timeout=.1)
            except queue.Empty:
                continue
            event = self.cancels.get(job_id)
            if event is None:
                continue
            try:
                self.process(job_id, event)
            finally:
                with self.lock:
                    # A retry can be submitted after FAILED is committed but
                    # before this attempt returns. Retain the new attempt's event.
                    if self.cancels.get(job_id) is event:
                        self.cancels.pop(job_id, None)

    def _workspace(self, job_id):
        if not re.fullmatch('[a-f0-9]{32}', job_id):
            raise ValueError('LOCAL_JOB_ID_INVALID')
        base = self.files.resolve()
        path = self.files / job_id
        if self.files.is_symlink() or path.is_symlink() or path.resolve().parent != base:
            raise ValueError('LOCAL_OUTPUT_PATH_INVALID')
        return path

    def _job(self, conn, job_id):
        row = conn.execute('SELECT * FROM publisher_local_preparations WHERE id=?', (job_id,)).fetchone()
        if not row:
            raise ValueError('LOCAL_JOB_NOT_FOUND')
        return dict(row)

    def _identity(self, conn, job):
        _, identity = eligible_source(conn, job['drive_file_id'])
        if canonical(identity) != job['identity_json']:
            raise ValueError('LOCAL_SOURCE_IDENTITY_CHANGED')
        return identity

    def process(self, job_id, cancel):
        started, stage = time.monotonic(), 'PREPARING_SOURCE'
        def check():
            if cancel.is_set() or self.stopping.is_set():
                raise RenderError('LOCAL_PREPARATION_CANCELLED')
        def progress(name, detail=None):
            nonlocal stage
            stage = name
            with closing(connect(self.db)) as c, c:
                job = self._job(c, job_id)
                events = json.loads(job['events_json'])[-47:]
                events.append(dict(stage=name, seconds=round(time.monotonic()-started, 3), detail=detail))
                c.execute('UPDATE publisher_local_preparations SET stage=?,events_json=? WHERE id=?',
                          (name, canonical(events), job_id))
        try:
            with closing(connect(self.db)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                job = self._job(conn, job_id)
                if job['state'] != 'QUEUED':
                    return
                identity = self._identity(conn, job)
                conn.execute("UPDATE publisher_local_preparations SET state='PROCESSING',error_code=NULL WHERE id=?", (job_id,))
            check(); progress(stage)
            workspace = self._workspace(job_id)
            inputs = workspace / 'inputs'
            if inputs.is_symlink() or (workspace/'artifacts').is_symlink():
                raise ValueError('LOCAL_OUTPUT_PATH_INVALID')
            used = sum(p.stat().st_size for p in self.files.rglob('*') if p.is_file() and not p.is_symlink())
            if used + identity['size_bytes']*4 > 8*MAX_BYTES:
                raise ValueError('LOCAL_STORAGE_LIMIT_REACHED')
            inputs.mkdir(parents=True, exist_ok=True)
            from src.publisher.media import media_provider, verify_identity
            reader = (self.reader_factory or media_provider)(identity)
            def verify_remote():
                metadata = reader.get_metadata(identity['remote_file_id'])
                try:
                    verify_identity(identity, metadata)
                except (ValueError, KeyError, TypeError) as exc:
                    raise ValueError('LOCAL_SOURCE_IDENTITY_CHANGED') from exc
                if (metadata.get('trashed') or identity['remote_folder_id'] not in metadata.get('parents', [])
                        or identity['remote_modified_at'] and metadata.get('modifiedTime') != identity['remote_modified_at']):
                    raise ValueError('LOCAL_SOURCE_IDENTITY_CHANGED')
            verify_remote()
            check()
            source = Path(reader.download_temporary(identity, inputs, cancel_event=cancel)).resolve()
            if source.parent != inputs.resolve() or source.is_symlink():
                raise ValueError('LOCAL_SOURCE_PATH_INVALID')
            if source.stat().st_size != identity['size_bytes']:
                raise ValueError('LOCAL_SOURCE_SIZE_CHANGED')
            if identity['checksum']:
                digest = hashlib.md5()
                with source.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024*1024), b''):
                        check(); digest.update(chunk)
                if digest.hexdigest() != identity['checksum']:
                    raise ValueError('LOCAL_SOURCE_CHECKSUM_CHANGED')
            verify_remote()
            check(); progress('VALIDATING_MEDIA')
            from src.publisher.headline_configuration import create_renderer
            if self.renderer_factory:
                renderer = self.renderer_factory(self.root, inputs, cancel)
            elif json.loads(job['snapshot_json'])['enabled']:
                renderer = create_renderer(self.root, inputs, cancel)
            else:
                # Original validation needs no headline font/configuration.
                from src.publisher.headline_renderer import HeadlineRenderer
                renderer = HeadlineRenderer(self.root, inputs, workspace/'artifacts', {}, cancel_event=cancel)
            # Entirely separate from the renderer catalog/output used by remote Drafts.
            renderer.artifact_root = workspace / 'artifacts'
            renderer.stage_observer = lambda event: progress(
                'RENDERING_HEADLINE' if event['stage'] in {'layout','encode'} else 'VALIDATING_VIDEO', event)
            source_hash = sha256(source)
            metadata = probe(renderer.ffprobe, source, cancel_event=cancel)
            if metadata['duration'] <= 0:
                raise ValueError('LOCAL_MEDIA_INVALID')
            container = json.loads(run([renderer.ffprobe, '-v', 'error', '-show_entries',
                'format=format_name:format_tags=major_brand', '-of', 'json', source],
                timeout=renderer.timeout, cancel_event=cancel)).get('format', {})
            if ('mp4' not in container.get('format_name', '').split(',')
                    or container.get('tags', {}).get('major_brand', '').strip() == 'qt'):
                raise ValueError('LOCAL_MP4_REQUIRED')
            snapshot = json.loads(job['snapshot_json'])
            if snapshot['enabled']:
                progress('RENDERING_HEADLINE')
                from src.publisher.headline_renderer import init_artifacts
                with closing(sqlite3.connect(workspace/'render.sqlite3')) as render_conn:
                    render_conn.row_factory = sqlite3.Row
                    init_artifacts(render_conn)
                    artifact = renderer.render(render_conn, 1, snapshot, source, kind='video')
                prepared = Path(artifact['local_path'])
                metadata = json.loads(artifact['probe_json'])
            else:
                progress('VALIDATING_VIDEO')
                run([renderer.ffmpeg, '-v', 'error', '-xerror', '-i', source, '-f', 'null', '-'],
                    timeout=renderer.timeout, cancel_event=cancel)
                from src.publisher.headline_timing import validate_frame_timeline
                timeline = renderer.presentation_timeline(source, metadata['video']['time_base'])
                validate_frame_timeline(timeline, timeline)
                prepared = source
            check(); progress('FINALIZING_LOCAL_FILE')
            if sha256(source) != source_hash:
                raise ValueError('LOCAL_SOURCE_CHANGED')
            partial, output = workspace/'video.partial', workspace/'video.mp4'
            shutil.copyfile(prepared, partial)
            output_hash = sha256(partial)
            if output_hash != sha256(prepared):
                raise ValueError('LOCAL_OUTPUT_CHANGED')
            check()
            with closing(connect(self.db)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                self._identity(conn, job)
                partial.replace(output)
                conn.execute("UPDATE publisher_local_preparations SET state='READY',stage='READY',source_sha256=?,"
                             'output_sha256=?,renderer_version=?,metadata_json=?,finished_at=?,expires_at=? WHERE id=?',
                             (source_hash, output_hash, renderer.version, canonical(metadata), now_iso(),
                              (datetime.now(timezone.utc)+timedelta(days=RETENTION_DAYS)).isoformat(timespec='seconds'), job_id))
            progress('READY')
        except Exception as exc:
            code = getattr(exc, 'code', None) or (str(exc) if isinstance(exc, ValueError) else 'LOCAL_PREPARATION_FAILED')
            if not re.fullmatch('[A-Z0-9_]{1,100}', code):
                code = 'LOCAL_PREPARATION_FAILED'
            with closing(connect(self.db)) as conn, conn:
                job = self._job(conn, job_id)
                events = json.loads(job['events_json'])[-47:]+[dict(stage=stage,
                    seconds=round(time.monotonic()-started, 3), error_code=code)]
                conn.execute('UPDATE publisher_local_preparations SET state=?,error_code=?,events_json=?,finished_at=? WHERE id=?',
                             ('CANCELLED' if code.endswith('CANCELLED') else 'FAILED', code, canonical(events), now_iso(), job_id))

    def action(self, job_id, action, data):
        with self.lock:
            self._own()
            with closing(connect(self.db)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                job = self._job(conn, job_id)
                if action == 'cancel':
                    if job['state'] in ACTIVE:
                        self.cancels.setdefault(job_id, threading.Event()).set()
                        if job['state'] == 'QUEUED':
                            conn.execute("UPDATE publisher_local_preparations SET state='CANCELLED',error_code='LOCAL_PREPARATION_CANCELLED' WHERE id=?", (job_id,))
                elif action == 'retry':
                    if job['state'] not in {'FAILED', 'CANCELLED'} or self.stopping.is_set():
                        raise ValueError('LOCAL_RETRY_NOT_ALLOWED')
                    self._identity(conn, job)
                    if active_local_preparation(conn, job['drive_file_id']):
                        raise ValueError('LOCAL_MEDIA_BUSY')
                    conn.execute("UPDATE publisher_local_preparations SET state='QUEUED',stage='PREPARING_SOURCE',error_code=NULL WHERE id=?", (job_id,))
                elif action == 'remove':
                    if job['state'] in ACTIVE or data.get('human_confirmed') is not True:
                        raise ValueError('LOCAL_REMOVE_CONFIRMATION_REQUIRED_OR_ACTIVE')
                    path = self._workspace(job_id)
                    # Exact owned UUID directory only; no paths are accepted from clients.
                    if path.exists():
                        shutil.rmtree(path)
                    conn.execute("UPDATE publisher_local_preparations SET state='REMOVED',stage='REMOVED' WHERE id=?", (job_id,))
                else:
                    raise ValueError('LOCAL_ACTION_INVALID')
            if action == 'retry':
                self.cancels[job_id] = threading.Event()
                self.commands.put(job_id); self._start()
            return self.list()

    def serve_video(self, handler, job_id, download=False):
        with self.lock, closing(connect(self.db)) as conn:
            job = self._job(conn, job_id)
            if job['state'] != 'READY' or public_job(job)['expired']:
                raise ValueError('LOCAL_OUTPUT_NOT_AVAILABLE')
            path = self._workspace(job_id)/'video.mp4'
            if path.is_symlink() or not path.is_file() or sha256(path) != job['output_sha256']:
                raise ValueError('LOCAL_OUTPUT_IDENTITY_CHANGED')
            size = path.stat().st_size
            start, end = 0, size-1
            header = handler.headers.get('Range')
            if header:
                match = re.fullmatch(r'bytes=(\d+)-(\d*)', header)
                if not match:
                    handler.send_error(416); return
                start, end = int(match[1]), int(match[2]) if match[2] else end
                if not 0 <= start <= end < size:
                    handler.send_error(416); return
            handler.send_response(206 if header else 200)
            handler.send_header('Content-Type', 'video/mp4')
            handler.send_header('Cache-Control', 'no-store')
            handler.send_header('X-Content-Type-Options', 'nosniff')
            handler.send_header('Accept-Ranges', 'bytes')
            handler.send_header('Content-Length', str(end-start+1))
            if header:
                handler.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            handler.send_header('Content-Disposition', f'{"attachment" if download else "inline"}; filename="prepared-{job_id}.mp4"')
            handler.end_headers()
            try:
                with path.open('rb') as stream:
                    stream.seek(start)
                    remaining = end-start+1
                    while remaining:
                        chunk = stream.read(min(remaining, 1024*1024))
                        if not chunk: break
                        handler.wfile.write(chunk); remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Video players intentionally abandon range responses on seeking/close.
                handler.close_connection = True

    def shutdown(self):
        with self.lock:
            self.stopping.set()
            for event in list(self.cancels.values()): event.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise RuntimeError('LOCAL_WORKER_SHUTDOWN_PENDING')
        if self.lease:
            from src.publisher.media import unlock_lease
            unlock_lease(self.lease); self.lease.close(); self.lease = None
