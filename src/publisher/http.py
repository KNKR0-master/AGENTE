import json
import getpass
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from src.content_pipeline import probe_video
from src.drive_inventory import DriveInventoryConfig
from src.google_drive_auth import DriveAuthError, DriveCredentialStore, user_drive_service
from src.media_tools import FFProbeError, ffprobe_version, resolve_ffprobe_path
from src.store import connect
from src.publisher.service import PublisherService, get_row, init_publisher
from src.publisher.worker import PublisherWorker, recover_sessions
from src.publisher.media import media_provider, TemporaryMediaCache
from src.publisher.account_safety import AccountSafetyGuard
from src.publisher.drive_sync import drive_sync_status, incremental_drive_sync
from src.publisher.drive_polling import DrivePollCoordinator, configure_polling, polling_status
from src.publisher.thumbnails import PublisherThumbnailService, ThumbnailUnavailable


PUBLISHER_API_VERSION = "publisher-api-v1"
PUBLISHER_COMPATIBILITY = "publisher-ui-2a.3.3"


class PublisherAPI:
    def __init__(self, root, db):
        self.root, self.db = Path(root), db
        self.worker = PublisherWorker(root, db)
        self.drive_poller = DrivePollCoordinator(db)
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.thumbnails = PublisherThumbnailService()

    def runtime_info(self, handler):
        handler._json(
            {
                "app": "publisher-desk",
                "api_version": PUBLISHER_API_VERSION,
                "compatibility": PUBLISHER_COMPATIBILITY,
                "process_id": os.getpid(),
                "instance_id": self.worker.profile_ownership.instance_id,
                "started_at": self.started_at,
            },
            no_store=True,
        )

    def initialize(self):
        self.worker.profile_ownership.reconcile_all()
        self.worker.cache = TemporaryMediaCache(self.root)
        with closing(connect(self.db)) as conn:
            init_publisher(conn)
            recover_sessions(conn)
        self.worker.cache.clean_orphans()

    def _ffprobe_health(self):
        result = {
            "available": False,
            "executable_valid": False,
            "path": None,
            "version": None,
            "error": None,
            "path_exists": False,
            "is_file": False,
            "file_readable": False,
            "file_executable_by_process": False,
            "process_started": False,
            "exit_code": None,
            "exception_type": None,
            "exception_winerror": None,
            "media_test": False,
            "media_test_path": None,
            "media_test_metadata": None,
        }
        try:
            path = resolve_ffprobe_path(self.root)
            result["path"] = str(path) if path else None
            if path:
                try:
                    result["path_exists"] = path.exists()
                    result["is_file"] = path.is_file()
                    if result["is_file"]:
                        with path.open("rb"):
                            result["file_readable"] = True
                except OSError as exc:
                    result["exception_type"] = type(exc).__name__
                    result["exception_winerror"] = getattr(exc, "winerror", None)
            version = ffprobe_version(path)
            result.update(
                available=True,
                executable_valid=True,
                file_executable_by_process=True,
                process_started=True,
                exit_code=0,
                version=version,
                error=None,
            )
            test_candidates = (
                self.root / "data" / "content" / "KIT5" / "KIT5-0001.mp4",
                self.root / "data" / "uploads" / "1786300701_CUECAPOLO_G1C1A1_2607_1785103599470.mp4",
            )
            test_path = next((candidate for candidate in test_candidates if candidate.is_file()), None)
            if test_path:
                probe = probe_video(test_path, str(path))
                result.update(
                    media_test=bool(
                        probe.results.get("ffprobe_available")
                        and probe.results.get("valid_file")
                    ),
                    media_test_path=str(test_path),
                    media_test_metadata={
                        key: probe.results.get(key)
                        for key in ("duration", "codec", "width", "height", "resolution")
                    },
                )
            return result
        except FFProbeError as exc:
            result.update(
                error=exc.exception_type or exc.code,
                exception_type=exc.exception_type,
                exception_winerror=exc.winerror,
                process_started=bool(exc.process_started),
            )
            return result
        except OSError as exc:
            result.update(
                error=type(exc).__name__,
                exception_type=type(exc).__name__,
                exception_winerror=getattr(exc, "winerror", None),
            )
            return result

    def _drive_health(self, conn):
        latest = conn.execute("SELECT * FROM drive_scan_runs ORDER BY id DESC LIMIT 1").fetchone()
        inventory_count = int(conn.execute("SELECT COUNT(*) FROM drive_files").fetchone()[0])
        try:
            config = DriveInventoryConfig.from_env()
        except (OSError, ValueError, KeyError, TypeError):
            config = None
        result = {
            "status": "UNKNOWN", "diagnosis": "NO_SCAN", "available": bool(inventory_count),
            "latest_scan_id": latest["id"] if latest else None,
            "latest_scan_status": latest["status"] if latest else "NEVER_SCANNED",
            "latest_error": latest["error_message"] if latest else None,
            "account": (config.expected_google_account or config.account_email) if config else None,
            "credential_store_exists": bool(config and config.oauth_credential_store and Path(config.oauth_credential_store).is_file()),
            "credential_store_readable": None,
            "dpapi_file_exists": False,
            "dpapi_load_ok": False,
            "credential_error_type": None,
            "credential_error_winerror": None,
            "drive_auth_ok": False,
            "drive_read_ok": False,
            "process_user": getpass.getuser(),
        }
        if config and config.oauth_credential_store:
            diagnostic = DriveCredentialStore(config.oauth_credential_store).diagnostic()
            result.update(
                dpapi_file_exists=diagnostic["file_exists"],
                credential_store_readable=diagnostic["file_readable"],
                dpapi_load_ok=diagnostic["dpapi_load_ok"],
                credential_error_type=diagnostic["exception_type"],
                credential_error_winerror=diagnostic["winerror"],
            )
        if config and config.auth_mode == "user_oauth" and not result["dpapi_load_ok"]:
            if result["credential_error_winerror"] == -2146893813:
                diagnosis = "DPAPI_PROFILE_MISMATCH"
            elif not result["dpapi_file_exists"]:
                diagnosis = "GOOGLE_USER_CONSENT_REQUIRED"
            else:
                diagnosis = "ACTUAL_CREDENTIAL_FAILURE"
            result.update(status="ATTENTION", diagnosis=diagnosis, available=False)
            return result
        if config and config.auth_mode == "user_oauth" and result["dpapi_load_ok"]:
            try:
                service = user_drive_service(
                    config.oauth_credential_store,
                    config.expected_google_account,
                )
                result["drive_auth_ok"] = True
                service.files().list(
                    q="'root' in parents and trashed = false",
                    spaces="drive",
                    pageSize=1,
                    fields="files(id)",
                ).execute(num_retries=0)
                result.update(
                    status="AVAILABLE",
                    diagnosis="HEALTHY",
                    available=True,
                    drive_read_ok=True,
                )
            except DriveAuthError as exc:
                result.update(
                    status="ATTENTION",
                    diagnosis=str(exc),
                    available=False,
                )
            except Exception as exc:
                result.update(
                    status="ATTENTION",
                    diagnosis="DRIVE_READ_FAILURE",
                    available=False,
                    credential_error_type=type(exc).__name__,
                )
            return result
        if not latest:
            return result
        if latest["status"] == "COMPLETE":
            result.update(status="AVAILABLE", diagnosis="HEALTHY", available=True)
            return result
        if latest["status"] != "ERROR":
            result.update(status="CHECKING", diagnosis="SCAN_IN_PROGRESS")
            return result
        if latest["error_message"] == "GOOGLE_CREDENTIAL_STORE_UNREADABLE":
            if not config or not config.oauth_credential_store or not Path(config.oauth_credential_store).is_file():
                result.update(status="ATTENTION", diagnosis="ACTUAL_CREDENTIAL_FAILURE", available=bool(inventory_count))
                return result
            try:
                DriveCredentialStore(config.oauth_credential_store).load()
            except DriveAuthError:
                result.update(status="ATTENTION", diagnosis="ACTUAL_CREDENTIAL_FAILURE", credential_store_readable=False)
            except OSError:
                result.update(status="ATTENTION", diagnosis="WRONG_PROCESS_CONTEXT", credential_store_readable=False)
            else:
                result.update(status="AVAILABLE", diagnosis="STALE_ERROR", credential_store_readable=True, available=True, dpapi_load_ok=True)
            return result
        if latest["error_message"] == "GOOGLE_USER_CONSENT_REQUIRED":
            result.update(status="ATTENTION", diagnosis="ACTUAL_CREDENTIAL_FAILURE")
        else:
            result.update(status="ATTENTION", diagnosis="ACTUAL_CREDENTIAL_FAILURE")
        return result

    def get(self, handler, query, path="/api/publisher"):
        with closing(connect(self.db)) as conn:
            thumbnail_match = re.fullmatch(r"/api/publisher/library/thumbnail/([A-Za-z0-9_-]{1,256})", path)
            if path.startswith("/api/publisher/library/thumbnail/"):
                try:
                    thumbnail = self.thumbnails.get(conn, thumbnail_match[1]) if thumbnail_match else None
                except ThumbnailUnavailable:
                    thumbnail = None
                if thumbnail is None:
                    handler.send_response(404)
                    handler.send_header("Cache-Control", "no-store")
                    handler.send_header("Content-Length", "0")
                    handler.end_headers()
                    return
                handler.send_response(200)
                handler.send_header("Content-Type", thumbnail.content_type)
                handler.send_header("Content-Length", str(len(thumbnail.content)))
                handler.send_header("Cache-Control", "private, max-age=300")
                handler.send_header("X-Content-Type-Options", "nosniff")
                handler.end_headers()
                handler.wfile.write(thumbnail.content)
                return
            preflight_match = re.fullmatch(r'/api/publisher/batches/(\d+)/preflight', path)
            if preflight_match:
                handler._json(PublisherService(conn, self.root).queue_preflight(int(preflight_match[1])), no_store=True)
                return
            if path == "/api/publisher/drive-sync/status":
                handler._json({**drive_sync_status(conn), "polling": polling_status(conn)}, no_store=True)
                return
            if path == "/api/publisher/library":
                result = PublisherService(conn, self.root).media_library({
                    key: values[0] for key, values in query.items() if values
                })
                handler._json(result, no_store=True)
                return
            if path == "/api/publisher/library/groups":
                handler._json(PublisherService(conn, self.root).media_library_groups({
                    key: values[0] for key, values in query.items() if values
                }), no_store=True)
                return
            if path == "/api/publisher/library/group-items":
                handler._json(PublisherService(conn, self.root).media_library_group_items({
                    key: values[0] for key, values in query.items() if values
                }), no_store=True)
                return
            if path == "/api/publisher/products/workspace":
                handler._json(PublisherService(conn, self.root).product_workspace(
                    scope=query.get("scope", ["current"])[0],
                    search=query.get("search", [""])[0]), no_store=True)
                return
            if path == "/api/publisher/folder-catalog":
                result = PublisherService(conn, self.root).folder_catalog_reviews({
                    key: values[0] for key, values in query.items() if values
                })
                handler._json(result, no_store=True)
                return
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
            ffprobe = self._ffprobe_health()
            drive_health = self._drive_health(conn)
            authenticated_accounts = [
                account.get("session_verified") and account.get("account_identity") == "ACCOUNT_CONFIRMED"
                for account in result["accounts"]
            ]
            authenticated = any(authenticated_accounts)
            connected = next((account.get("name") for account in result["accounts"] if account.get("session_verified") and account.get("account_identity") == "ACCOUNT_CONFIRMED"), None)
            result["worker_config"] = {
                "tiktok_actions_paused": self.worker.actions_paused(),
                "studio_validated": bool(json.loads(mapping.read_text(encoding="utf-8")).get("validated")) if mapping.exists() else False,
                "ffprobe_available": ffprobe["available"],
                "ffprobe_executable_valid": ffprobe["executable_valid"],
                "ffprobe_path": ffprobe["path"],
                "ffprobe_version": ffprobe["version"],
                "ffprobe_error": ffprobe["error"],
                "ffprobe_media_test": ffprobe["media_test"],
                "durable_confirmation_validated": bool(json.loads(mapping.read_text(encoding='utf-8')).get('durable_confirmation_validated')) if mapping.exists() else False,
                "publisher_concurrency": 1,
                "auth_diagnostics_available": True,
                "manual_profile_available": True,
                "persistence_workflow_version": 1,
            }
            result["drive_health"] = drive_health
            result["system_status"] = {
                "ready": bool(ffprobe["executable_valid"] and ffprobe["media_test"] and authenticated and drive_health["available"]),
                "system_label": "Sistema pronto" if ffprobe["executable_valid"] and ffprobe["media_test"] and authenticated and drive_health["available"] else "Atenção necessária",
                "account_label": f"{connected} conectada" if connected else "Sessão TikTok pendente",
                "drive_label": "Drive disponível" if drive_health["available"] else "Drive aguardando verificação",
                "technical": {"ffprobe": ffprobe, "drive": drive_health},
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
                    reorder_match = re.fullmatch(r'/api/publisher/batches/(\d+)/reorder', path)
                    if reorder_match:
                        result = service.reorder_batch(int(reorder_match[1]), data.get('ordered_draft_ids') or [])
                    elif path in {"/products/bulk", "/api/publisher/products/bulk"}:
                        result = service.product_bulk_plan(data.get("ids", [])) if data.get("action") == "preview" else service.bulk_products(
                            data.get("ids", []), data.get("action", "delete"), human_confirmed=data.get("human_confirmed") is True
                        )
                    elif action == "product-folder":
                        result = service.set_product_folder(data.get("product_id"), data.get("folder_id"), data.get("action", "add"))
                    elif action == "preferences":
                        result = {"default_publisher_account_id": service.set_default_account(data.get("default_publisher_account_id"))}
                    elif action == "account-protection-clear":
                        result = AccountSafetyGuard(conn).clear_manual(
                            data.get("account_id"), human_confirmed=data.get("human_confirmed"),
                            reason=data.get("reason"),
                        )
                    elif action in {"products", "accounts", "presets"}:
                        result = {"id": getattr(service, f"save_{action[:-1]}")(data)}
                    elif path in {'/api/publisher/sync-media', '/api/publisher/drive-sync/incremental'}:
                        bootstrap = data.get('bootstrap') is True
                        if bootstrap and data.get('human_confirmed') is not True:
                            raise ValueError('FULL_DRIVE_RESCAN_REQUIRES_CONFIRMATION')
                        result = incremental_drive_sync(conn, bootstrap=bootstrap)
                    elif path == '/api/publisher/drive-sync/polling':
                        result = configure_polling(conn, data.get('action'), data.get('interval_seconds'))
                        self.drive_poller.wake()
                    elif action == 'batches':
                        result = service.create_remote_batch(data)
                    elif action == 'library-batches':
                        result = service.create_library_batch(data)
                    elif action == 'group-select' and path == '/api/publisher/library/group-select':
                        result = service.select_media_library_group(data)
                    elif action == 'media-decision':
                        result = service.decide_media(data)
                    elif action == 'media-product':
                        result = service.assign_media_product(data)
                    elif action == 'folder-catalog-decision':
                        result = service.decide_folder_catalog(data)
                    elif action == 'folder-catalog-bulk':
                        result = service.bulk_folder_catalog(data)
                    elif action == 'folder-container-settings':
                        result = service.configure_product_container(data)
                    elif action == 'media-delete':
                        result = service.delete_library_media(data, media_provider())
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
