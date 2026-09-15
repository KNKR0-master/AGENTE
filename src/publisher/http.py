import json
import shutil
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

from src.store import connect
from src.publisher.service import PublisherService, get_row, init_publisher
from src.publisher.worker import PublisherWorker, recover_sessions
from src.publisher.media import media_provider, TemporaryMediaCache


class PublisherAPI:
    def __init__(self, root, db):
        self.root, self.db = Path(root), db
        self.worker = PublisherWorker(root, db)

    def initialize(self):
        self.worker.profile_ownership.reconcile_all()
        self.worker.cache = TemporaryMediaCache(self.root)
        with closing(connect(self.db)) as conn:
            init_publisher(conn)
            recover_sessions(conn)
        self.worker.cache.clean_orphans()

    def get(self, handler, query):
        with closing(connect(self.db)) as conn:
            if "media" in query or "evidence" in query:
                if "media" in query:
                    row = get_row(conn, "publisher_drafts", query["media"][0])
                    if row.get('media_asset_id'):
                        asset = get_row(conn,'publisher_media_assets',row['media_asset_id'])
                        handler.send_response(302)
                        handler.send_header('Location',f"https://drive.google.com/file/d/{asset['remote_file_id']}/view")
                        handler.end_headers()
                        return
                    relative, kind = row["video_path"], "video/mp4"
                    base = self.root / "data" / "publisher" / "uploads"
                else:
                    row = conn.execute("SELECT screenshot_path FROM publisher_execution_logs WHERE id=?", (int(query["evidence"][0]),)).fetchone()
                    if not row or not row[0]:
                        raise ValueError("Evidencia nao encontrada")
                    relative, kind = row[0], "image/png"
                    base = self.root / "data" / "publisher" / "evidence"
                path = (self.root / relative).resolve()
                if not path.is_relative_to(base.resolve()) or not path.is_file():
                    raise ValueError("Arquivo nao encontrado")
                size = path.stat().st_size
                start, end = 0, size - 1
                range_value = handler.headers.get("Range", "")
                if range_value:
                    import re
                    match = re.fullmatch(r"bytes=(\d+)-(\d*)", range_value)
                    if not match:
                        handler.send_error(416)
                        return
                    start = int(match[1]); end = min(int(match[2]) if match[2] else end, end)
                    if start > end:
                        handler.send_error(416)
                        return
                handler.send_response(206 if range_value else 200)
                handler.send_header("Content-Type", kind)
                handler.send_header("Accept-Ranges", "bytes")
                handler.send_header("Content-Length", str(end-start+1))
                if range_value:
                    handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                handler.end_headers()
                with path.open("rb") as stream:
                    stream.seek(start)
                    remaining = end-start+1
                    while remaining:
                        chunk = stream.read(min(remaining, 1024*1024))
                        if not chunk:
                            break
                        handler.wfile.write(chunk)
                        remaining -= len(chunk)
                return
            result = PublisherService(conn, self.root).snapshot()
            result['accounts'] = [self.worker.login_snapshot(a) for a in result['accounts']]
            result["review_sessions"] = self.worker.session_ids()
            result['temporary_files'] = sum(1 for p in self.worker.cache.root.rglob('*.mp4') if p.is_file())
            mapping = self.root / "config" / "publisher_studio.json"
            result["worker_config"] = {
                "tiktok_actions_paused": self.worker.actions_paused(),
                "studio_validated": bool(json.loads(mapping.read_text(encoding="utf-8")).get("validated")) if mapping.exists() else False,
                "ffprobe_available": bool(shutil.which("ffprobe")),
                "durable_confirmation_validated": bool(json.loads(mapping.read_text(encoding='utf-8')).get('durable_confirmation_validated')) if mapping.exists() else False,
                "publisher_concurrency": 1,
                "auth_diagnostics_available": True,
                "manual_profile_available": True,
                "persistence_workflow_version": 1,
            }
            handler._json(result)

    def get_auth_diagnostics(self, handler, path):
        match = re.fullmatch(r'/api/publisher/accounts/(\d+)/auth-diagnostics', path)
        if not match:
            raise ValueError('Rota de diagnostico invalida')
        # Reject cross-origin browser reads; keep this endpoint on the local server.
        origin = handler.headers.get('Origin')
        if origin and urlparse(origin).netloc != handler.headers.get('Host'):
            raise ValueError('Origem nao autorizada')
        with closing(sqlite3.connect(Path(self.db).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            account = get_row(conn, 'publisher_accounts', int(match[1]))
        handler._json(self.worker.get_auth_diagnostic_snapshot(account['id']), no_store=True)

    def post(self, handler, path):
        origin = handler.headers.get("Origin")
        if origin and urlparse(origin).netloc != handler.headers.get("Host"):
            raise ValueError("Origem nao autorizada")
        if handler.headers.get("Content-Type", "").startswith("multipart/form-data"):
            raise ValueError('Use referencias do Drive; upload local nao e utilizado nesta V1')
        else:
                length = int(handler.headers.get("Content-Length", 0))
                if length > 1024*1024:
                    raise ValueError("Requisicao muito grande")
                data = json.loads(handler.rfile.read(length) or b"{}")
                with closing(connect(self.db)) as conn:
                    service = PublisherService(conn, self.root)
                    action = path.rsplit("/", 1)[-1]
                    if action in {"products", "accounts", "presets"}:
                        result = {"id": getattr(service, f"save_{action[:-1]}")(data)}
                    elif action == 'sync-media':
                        try:
                            result = service.index_media(media_provider())
                        except Exception as exc:
                            raise ValueError(f'Falha ao sincronizar Drive ({type(exc).__name__}). Verifique acesso da Cecilia e conexao.') from exc
                    elif action == 'batches':
                        result = service.create_remote_batch(data)
                    elif action == "bulk":
                        ids = data.get("ids", [])
                        if data.get("action") in {"reset", "published", "scheduled"} and not data.get("human_confirmed"):
                            raise ValueError("Confirme a acao realizada manualmente no Studio")
                        service.bulk(ids, data.get("fields", {}), data.get("action", "edit"))
                        if data.get("action") in {"reset", "published", "scheduled"}:
                            for row_id in ids:
                                self.worker.submit("release", row_id)
                        result = {"ok": True}
                    elif action == "prepare":
                        ids = data.get('ids', [])
                        if data.get('batch_id') and not ids:
                            ids = [r[0] for r in conn.execute("SELECT id FROM publisher_drafts WHERE batch_id=? AND status='DRAFT'",(int(data['batch_id']),))]
                        result = {"execution_id": self.worker.enqueue(conn, ids, bool(data.get('auto_cleanup', False)))}
                    elif action == 'resume-batch':
                        result = {"execution_id": self.worker.resume_execution(conn, data['execution_id'])}
                    elif action == 'inspect-existing-session':
                        if not isinstance(data.get('navigate', False), bool):
                            raise ValueError('navigate deve ser booleano')
                        result = self.worker.inspect_existing_session(data['id'], data.get('navigate', False))
                    elif action == 'open-manual-profile':
                        result = self.worker.open_manual_profile(int(data['id']))
                    elif action == 'draft-one-probe':
                        result = self.worker.probe_draft_one(data['operation'],data)
                    elif action == 'verify-manual-profile':
                        result = self.worker.verify_manual_profile(int(data['id']))
                    elif action in {"login", "review", "cleanup"}:
                        if action == 'cleanup' and data.get('human_confirmed') is not True:
                            raise ValueError('EXPLICIT_MEDIA_CONSUMPTION_CONFIRMATION_REQUIRED')
                        get_row(conn, "publisher_accounts" if action == "login" else "publisher_drafts", data["id"])
                        result = self.worker.submit(action, data["id"]) or {"ok": True}
                    else:
                        raise ValueError("Rota Publisher desconhecida")
        handler._json(result)
