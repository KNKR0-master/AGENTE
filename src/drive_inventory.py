from __future__ import annotations

import json
import hashlib
import logging
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol

from src.store import now_iso, upsert_product
from src.product_assets import sync_folder_prompts
from src.product_catalog import product_catalog_options, sync_affiliate_product_catalog
from src.publisher.folder_catalog import sync_drive_product_name


LOGGER = logging.getLogger(__name__)
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
MAX_MISSING_FOLDER_METADATA_PER_SCAN = 200
STOCK_NAME_PATTERN = re.compile(r"(?:G\d+C\d+A\d+|FINAL)", re.IGNORECASE)
VIDEO_CLASSES = (
    "AVAILABLE",
    "ALREADY_PUBLISHED",
    "ALREADY_QUEUED",
    "INVALID",
    "DUPLICATE",
    "NEEDS_REVIEW",
    "IGNORED",
    "MISSING_FROM_DRIVE",
)
MAPPING_STATES = (
    "MAPPED_TO_PRODUCT",
    "CATEGORY_ONLY",
    "ADMINISTRATIVE",
    "IGNORED",
    "NEEDS_REVIEW",
)


class DriveInventoryError(RuntimeError):
    pass


class DriveProviderUnavailable(DriveInventoryError):
    pass


@dataclass(frozen=True)
class DriveEntry:
    file_id: str
    name: str
    mime_type: str
    parent_ids: tuple[str, ...] = ()
    size: int | None = None
    md5_checksum: str | None = None
    created_time: str | None = None
    modified_time: str | None = None
    web_view_link: str | None = None
    thumbnail_link: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == FOLDER_MIME_TYPE

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "DriveEntry":
        size = payload.get("size")
        return cls(
            file_id=str(payload.get("id") or payload.get("file_id") or ""),
            name=str(payload.get("name") or ""),
            mime_type=str(payload.get("mimeType") or payload.get("mime_type") or ""),
            parent_ids=tuple(payload.get("parents") or payload.get("parent_ids") or ()),
            size=int(size) if size not in (None, "") else None,
            md5_checksum=payload.get("md5Checksum") or payload.get("md5_checksum"),
            created_time=payload.get("createdTime") or payload.get("created_time"),
            modified_time=payload.get("modifiedTime") or payload.get("modified_time"),
            web_view_link=payload.get("webViewLink") or payload.get("web_view_link"),
            thumbnail_link=payload.get("thumbnailLink") or payload.get("thumbnail_link"),
        )


class DriveInventoryProvider(Protocol):
    def get_folder(self, folder_id: str) -> DriveEntry: ...

    def get_item_metadata(self, file_id: str) -> dict[str, Any] | None: ...

    def is_within_root(self, file_id: str, root_id: str) -> bool | None: ...

    def list_children(self, folder_id: str) -> list[DriveEntry]: ...

    def read_text(self, entry: DriveEntry) -> str: ...


@dataclass(frozen=True)
class DriveInventoryConfig:
    root_folder_id: str
    account_email: str | None
    credentials_file: str | None
    active_profile_folder_id: str | None = None
    reserve_profile_folder_ids: tuple[str, ...] = ()
    archived_profile_folder_ids: tuple[str, ...] = ()
    auth_mode: str = 'service_account'
    expected_google_account: str | None = None
    oauth_credential_store: str | None = None
    cache_seconds: int = 600
    publish_target_per_day: float = 2.0
    watch_threshold: int = 8
    low_threshold: int = 4
    alert_count_delta: int = 2
    alert_cooldown_hours: int = 24
    ignored_folder_ids: tuple[str, ...] = ()
    administrative_names: tuple[str, ...] = (
        "postados",
        "descartados",
        "arquivo",
        "arquivados",
        "backup",
        "prompts",
        "imagens",
    )

    @classmethod
    def from_env(cls) -> "DriveInventoryConfig":
        root = os.getenv("GOOGLE_DRIVE_TIKTOK_ROOT_FOLDER_ID", "").strip()
        ignored = _csv_env("GOOGLE_DRIVE_IGNORED_FOLDER_IDS")
        administrative = _csv_env("GOOGLE_DRIVE_ADMINISTRATIVE_NAMES")
        auth = {}
        if (os.getenv('GOOGLE_DRIVE_ACCOUNT_EMAIL') or '').strip().lower() == 'ceciliafigueiredo.shop@gmail.com':
            settings = Path(__file__).resolve().parents[1] / 'config/cecilia_drive_auth.json'
            if settings.is_file():
                auth = json.loads(settings.read_text(encoding='utf-8'))
        return cls(
            root_folder_id=root,
            account_email=os.getenv("GOOGLE_DRIVE_ACCOUNT_EMAIL") or None,
            credentials_file=os.getenv("GOOGLE_APPLICATION_CREDENTIALS") or None,
            auth_mode=auth.get('auth_mode', 'service_account'),
            expected_google_account=auth.get('expected_google_account'),
            oauth_credential_store=str(Path(__file__).resolve().parents[1] / auth['credential_store']) if auth.get('credential_store') else None,
            active_profile_folder_id=os.getenv("GOOGLE_DRIVE_ACTIVE_PROFILE_FOLDER_ID") or None,
            reserve_profile_folder_ids=tuple(_csv_env("GOOGLE_DRIVE_RESERVE_PROFILE_FOLDER_IDS")),
            archived_profile_folder_ids=tuple(_csv_env("GOOGLE_DRIVE_ARCHIVED_PROFILE_FOLDER_IDS")),
            cache_seconds=max(300, min(900, _int_env("DRIVE_INVENTORY_CACHE_SECONDS", 600))),
            publish_target_per_day=max(0.1, _float_env("CONTENT_PUBLICATION_TARGET_PER_DAY", 2.0)),
            watch_threshold=max(1, _int_env("CONTENT_STOCK_WATCH_THRESHOLD", 8)),
            low_threshold=max(1, _int_env("CONTENT_STOCK_LOW_THRESHOLD", 4)),
            alert_count_delta=max(1, _int_env("CONTENT_ALERT_COUNT_DELTA", 2)),
            alert_cooldown_hours=max(1, _int_env("CONTENT_ALERT_COOLDOWN_HOURS", 24)),
            ignored_folder_ids=tuple(ignored),
            administrative_names=tuple(administrative) if administrative else cls.administrative_names,
        )


class GoogleApiDriveInventoryProvider:
    """Read-only Google Drive v3 provider using a shared service-account folder."""

    SCOPES = ("https://www.googleapis.com/auth/drive.readonly",)

    @classmethod
    def from_config(cls, config, *, scopes=None):
        if config.auth_mode == 'service_account':
            return cls(config.credentials_file, scopes=scopes)
        if config.auth_mode != 'user_oauth':
            raise DriveProviderUnavailable('GOOGLE_AUTH_MODE_INVALID')
        from src.google_drive_auth import user_drive_service, DriveAuthError, CECILIA
        if config.account_email != CECILIA or config.expected_google_account != CECILIA:
            raise DriveProviderUnavailable('GOOGLE_ACCOUNT_MISMATCH')
        if not config.oauth_credential_store:
            raise DriveProviderUnavailable('GOOGLE_USER_CONSENT_REQUIRED')
        try:
            instance = cls.__new__(cls)
            instance._service = user_drive_service(config.oauth_credential_store, config.expected_google_account)
            return instance
        except DriveAuthError as exc:
            raise DriveProviderUnavailable(str(exc)) from None

    def __init__(self, credentials_file: str | None, *, scopes=None) -> None:
        if not credentials_file or not Path(credentials_file).exists():
            raise DriveProviderUnavailable(
                "Credenciais server-side ausentes. Configure GOOGLE_APPLICATION_CREDENTIALS "
                "e compartilhe a pasta raiz com essa conta de servico."
            )
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise DriveProviderUnavailable(
                "Dependencias Google ausentes. Instale requirements.txt antes do scan."
            ) from exc
        credentials = service_account.Credentials.from_service_account_file(
            credentials_file, scopes=scopes or self.SCOPES
        )
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def get_folder(self, folder_id: str) -> DriveEntry:
        try:
            payload = (
                self._service.files()
                .get(fileId=folder_id, fields=_drive_fields(), supportsAllDrives=True)
                .execute()
            )
        except Exception as exc:
            raise DriveInventoryError(f"Pasta raiz inacessivel: {_safe_provider_error(exc)}") from exc
        entry = DriveEntry.from_api(payload)
        if not entry.is_folder:
            raise DriveInventoryError("O ID configurado nao representa uma pasta do Google Drive.")
        return entry

    def get_item_metadata(self, file_id: str) -> dict[str, Any] | None:
        try:
            return self._service.files().get(
                fileId=file_id, fields="id,name,mimeType,parents,trashed",
                supportsAllDrives=True,
            ).execute(num_retries=0)
        except Exception as exc:
            if getattr(getattr(exc, "resp", None), "status", None) == 404:
                return None
            raise DriveInventoryError(f"Falha ao consultar metadata: {_safe_provider_error(exc)}") from exc

    def list_children(self, folder_id: str) -> list[DriveEntry]:
        entries: list[DriveEntry] = []
        page_token: str | None = None
        try:
            while True:
                payload = (
                    self._service.files()
                    .list(
                        q=f"'{folder_id}' in parents and trashed = false",
                        fields=f"nextPageToken,files({_drive_fields()})",
                        pageSize=1000,
                        pageToken=page_token,
                        supportsAllDrives=True,
                        includeItemsFromAllDrives=True,
                    )
                    .execute()
                )
                entries.extend(DriveEntry.from_api(item) for item in payload.get("files", []))
                page_token = payload.get("nextPageToken")
                if not page_token:
                    return entries
        except Exception as exc:
            raise DriveInventoryError(f"Falha ao listar pasta: {_safe_provider_error(exc)}") from exc

    def get_start_page_token(self) -> str:
        try:
            payload = self._service.changes().getStartPageToken(
                supportsAllDrives=True,
            ).execute(num_retries=0)
            token = str(payload.get("startPageToken") or "")
            if not token:
                raise DriveInventoryError("Google Drive nao retornou startPageToken")
            return token
        except DriveInventoryError:
            raise
        except Exception as exc:
            raise DriveInventoryError(f"Falha ao obter cursor do Drive: {_safe_provider_error(exc)}") from exc

    def list_changes(self, page_token: str) -> dict[str, Any]:
        fields = (
            "nextPageToken,newStartPageToken,changes(removed,fileId,"
            "file(id,name,mimeType,parents,trashed,modifiedTime,createdTime,size,"
            "md5Checksum,webViewLink,thumbnailLink))"
        )
        try:
            payload = self._service.changes().list(
                pageToken=page_token,
                spaces="drive",
                includeRemoved=True,
                includeItemsFromAllDrives=True,
                supportsAllDrives=True,
                pageSize=1000,
                fields=fields,
            ).execute(num_retries=0)
        except Exception as exc:
            error = DriveInventoryError(f"Falha ao consultar changes do Drive: {_safe_provider_error(exc)}")
            error.__cause__ = exc
            raise error
        changes = []
        for raw in payload.get("changes", []):
            item = raw.get("file") or {}
            changes.append({
                "file_id": str(raw.get("fileId") or item.get("id") or ""),
                "removed": bool(raw.get("removed")),
                "file": item or None,
            })
        return {
            "changes": changes,
            "next_page_token": payload.get("nextPageToken"),
            "new_start_page_token": payload.get("newStartPageToken"),
        }

    def is_within_root(self, file_id: str, root_id: str) -> bool | None:
        pending, seen = [file_id], set()
        while pending:
            current = pending.pop()
            if current == root_id:
                return True
            if current in seen or len(seen) > 200:
                continue
            seen.add(current)
            try:
                payload = self._service.files().get(
                    fileId=current,
                    fields="id,parents,trashed",
                    supportsAllDrives=True,
                ).execute(num_retries=0)
            except Exception:
                return None
            if payload.get("trashed"):
                return False
            pending.extend(payload.get("parents") or [])
        return False

    def read_text(self, entry: DriveEntry) -> str:
        try:
            if entry.mime_type == "application/vnd.google-apps.document":
                request = self._service.files().export_media(
                    fileId=entry.file_id, mimeType="text/plain"
                )
            else:
                request = self._service.files().get_media(
                    fileId=entry.file_id, supportsAllDrives=True
                )
            payload = request.execute()
            if len(payload) > 2_000_000:
                raise DriveInventoryError("arquivo textual excede 2 MB")
            for encoding in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    return payload.decode(encoding)
                except UnicodeDecodeError:
                    continue
            raise DriveInventoryError("codificacao textual nao suportada")
        except DriveInventoryError:
            raise
        except Exception as exc:
            raise DriveInventoryError(f"Falha ao ler prompt: {_safe_provider_error(exc)}") from exc


class VideoPublicationEligibilityService:
    """Single source of truth for publication availability."""

    def classify(
        self,
        conn,
        entry: DriveEntry,
        folder: dict[str, Any],
        duplicate_reason: str | None = None,
        canonical_drive_file_id: str | None = None,
    ) -> dict[str, Any]:
        if duplicate_reason:
            return _eligibility("DUPLICATE", duplicate_reason, duplicate_reason, canonical_drive_file_id)
        if folder.get("operational_status", "ACTIVE") != "ACTIVE":
            return _eligibility("IGNORED", "pasta fora do perfil operacional ativo")
        if folder["mapping_status"] in {"ADMINISTRATIVE", "IGNORED"}:
            return _eligibility("IGNORED", f"pasta {folder['mapping_status'].lower()}")
        if not _is_mp4(entry):
            return _eligibility("IGNORED", "arquivo nao e MP4")
        if not is_stock_video_name(entry.name):
            return _eligibility("IGNORED", "nome fora do padrao G<n>C<n>A<n> ou FINAL")
        product_id = folder.get("product_id")
        if not product_id or folder["mapping_status"] != "MAPPED_TO_PRODUCT":
            return _eligibility("NEEDS_REVIEW", "pasta sem produto confirmado")

        content, match_method = _match_content(conn, entry, int(product_id))
        if not content:
            possible = _possible_content_duplicate(conn, entry, int(product_id))
            if possible:
                return _eligibility(
                    "DUPLICATE",
                    "nome e tamanho coincidem com outro conteudo",
                    "POSSIBLE_DUPLICATE",
                    possible.get("google_drive_file_id"),
                    possible.get("id"),
                )
            return _eligibility(
                "NEEDS_REVIEW",
                "arquivo novo requer importacao e validacao tecnica por ffprobe",
            )

        content_id = int(content["id"])
        if match_method != "DRIVE_FILE_ID" and not content.get("google_drive_file_id"):
            conn.execute(
                "UPDATE content_items SET google_drive_file_id = ?, google_drive_url = ?, updated_at = ? WHERE id = ?",
                (entry.file_id, entry.web_view_link, now_iso(), content_id),
            )
        status = str(content.get("status") or "")
        validation = str(content.get("validation_status") or "NEEDS_REVIEW")
        if status == "PUBLISHED":
            return _eligibility("ALREADY_PUBLISHED", "conteudo ja publicado", content_id=content_id)
        if status in {"QUEUED", "SENT_TO_PHONE"}:
            return _eligibility("ALREADY_QUEUED", f"conteudo em {status}", content_id=content_id)
        if status in {"FAILED", "ARCHIVED"} or validation == "FAIL":
            return _eligibility("INVALID", f"conteudo {status or validation}", content_id=content_id)
        if status == "READY" and validation == "PASS":
            return _eligibility("AVAILABLE", "presente no Drive e validado tecnicamente", content_id=content_id)
        return _eligibility(
            "NEEDS_REVIEW",
            f"validacao/status pendente ({validation}/{status or 'SEM_STATUS'})",
            content_id=content_id,
        )


def mark_remote_unavailable(conn, file_id: str, *, is_folder: bool, reason: str,
                            stop_at_folder_id: str | None = None) -> int:
    """Tombstone inventory rows while retaining Drive IDs and human mappings."""
    if reason not in {"TRASHED", "REMOVED", "OUTSIDE_ROOT", "NOT_FOUND"}:
        raise ValueError("INVALID_REMOTE_UNAVAILABLE_REASON")
    timestamp = now_iso()
    if not is_folder:
        cursor = conn.execute(
            """UPDATE drive_files SET classification='MISSING_FROM_DRIVE',
               classification_reason=?,missing_since=COALESCE(missing_since,?),updated_at=?
               WHERE drive_file_id=?""",
            (f"REMOTE_UNAVAILABLE:{reason}", timestamp, timestamp, file_id),
        )
        return max(0, cursor.rowcount)
    ids = [row[0] for row in conn.execute(
        """WITH RECURSIVE tree(id) AS (
           SELECT drive_folder_id FROM drive_folders
            WHERE drive_folder_id=? AND (? IS NULL OR drive_folder_id<>?)
           UNION ALL
           SELECT f.drive_folder_id FROM drive_folders f JOIN tree t ON f.parent_drive_folder_id=t.id
            WHERE ? IS NULL OR f.drive_folder_id<>?)
           SELECT id FROM tree""",
        (file_id, stop_at_folder_id, stop_at_folder_id,
         stop_at_folder_id, stop_at_folder_id),
    )]
    if not ids:
        return 0
    marks = ",".join("?" for _ in ids)
    conn.execute(
        f"UPDATE drive_folders SET operational_status='OUT_OF_SCOPE',updated_at=? WHERE drive_folder_id IN ({marks})",
        (timestamp, *ids),
    )
    cursor = conn.execute(
        f"""UPDATE drive_files SET classification='MISSING_FROM_DRIVE',classification_reason=?,
            missing_since=COALESCE(missing_since,?),updated_at=? WHERE drive_folder_id IN ({marks})""",
        (f"REMOTE_UNAVAILABLE:{reason}", timestamp, timestamp, *ids),
    )
    return len(ids) + max(0, cursor.rowcount)


class DriveInventoryScanner:
    def __init__(
        self,
        provider: DriveInventoryProvider,
        config: DriveInventoryConfig,
        eligibility: VideoPublicationEligibilityService | None = None,
    ) -> None:
        self.provider = provider
        self.config = config
        self.eligibility = eligibility or VideoPublicationEligibilityService()

    def scan(self, conn, force: bool = False) -> dict[str, Any]:
        if not self.config.root_folder_id:
            return record_scan_error(conn, self.config, "GOOGLE_DRIVE_TIKTOK_ROOT_FOLDER_ID nao configurado")
        cached = _fresh_scan(conn, self.config) if not force else None
        if cached:
            result = dict(cached)
            result["cached"] = True
            return result

        started = now_iso()
        conn.execute("BEGIN IMMEDIATE")
        running = conn.execute(
            """
            SELECT * FROM drive_scan_runs
            WHERE root_folder_id = ? AND status = 'RUNNING'
            ORDER BY id DESC LIMIT 1
            """,
            (self.config.root_folder_id,),
        ).fetchone()
        if running:
            running_started = _parse_iso(running["started_at"])
            if running_started and datetime.now(timezone.utc) - running_started < timedelta(minutes=30):
                conn.rollback()
                result = dict(running)
                result["cached"] = True
                result["in_progress"] = True
                return result
            conn.execute(
                """
                UPDATE drive_scan_runs SET status = 'ERROR',
                    error_message = 'scan interrompido antes da conclusao', finished_at = ?
                WHERE id = ?
                """,
                (started, running["id"]),
            )
        cursor = conn.execute(
            """
            INSERT INTO drive_scan_runs (account_email, root_folder_id, status, started_at)
            VALUES (?, ?, 'RUNNING', ?)
            """,
            (self.config.account_email, self.config.root_folder_id, started),
        )
        scan_id = int(cursor.lastrowid)
        conn.commit()
        try:
            root = self.provider.get_folder(self.config.root_folder_id)
        except Exception as exc:
            return _finish_scan_error(conn, scan_id, str(exc))

        sync_affiliate_product_catalog(conn)
        products = _product_candidates(conn)
        operational_root = root
        creator_profile_id: int | None = None
        if self.config.active_profile_folder_id:
            try:
                root_children = self.provider.list_children(root.file_id)
                operational_root, creator_profile_id = _sync_creator_drive_profiles(
                    conn, self.config, root, root_children, scan_id
                )
            except Exception as exc:
                return _finish_scan_error(conn, scan_id, str(exc))
        queue: list[tuple[DriveEntry, str | None, str, int, int | None]] = [
            (
                operational_root,
                root.file_id if operational_root.file_id != root.file_id else None,
                operational_root.name,
                0,
                creator_profile_id,
            )
        ]
        successful_folders: set[str] = set()
        seen_folder_ids: set[str] = set()
        seen_file_ids: set[str] = set()
        md5_canonical: dict[str, str] = {}
        signature_canonical: dict[tuple[int | None, str, int | None], str] = {}
        counters = {
            "folders_seen": 0,
            "folders_succeeded": 0,
            "folders_failed": 0,
            "files_seen": 0,
            "videos_seen": 0,
            "available_videos": 0,
        }

        while queue:
            folder_entry, parent_id, path, depth, profile_id = queue.pop(0)
            if folder_entry.file_id in seen_folder_ids:
                continue
            seen_folder_ids.add(folder_entry.file_id)
            counters["folders_seen"] += 1
            try:
                children = self.provider.list_children(folder_entry.file_id)
                child_folders = [item for item in children if item.is_folder]
                files = [item for item in children if not item.is_folder]
                direct_videos = sum(1 for item in files if _is_mp4(item))
                direct_prompts = sum(1 for item in files if _is_prompt_text(item))
                folder = _upsert_scanned_folder(
                    conn,
                    config=self.config,
                    entry=folder_entry,
                    parent_id=parent_id,
                    relative_path=path,
                    depth=depth,
                    direct_video_count=direct_videos,
                    direct_prompt_count=direct_prompts,
                    direct_other_file_count=max(0, len(files) - direct_videos - direct_prompts),
                    subfolder_count=len(child_folders),
                    scan_id=scan_id,
                    products=products,
                    creator_profile_id=profile_id,
                    operational_status="ACTIVE",
                )
                counters["folders_succeeded"] += 1
                successful_folders.add(folder_entry.file_id)
                for child in child_folders:
                    queue.append(
                        (child, folder_entry.file_id, f"{path}/{child.name}", depth + 1, profile_id)
                    )
                for entry in files:
                    counters["files_seen"] += 1
                    if _is_mp4(entry):
                        counters["videos_seen"] += 1
                    if entry.file_id in seen_file_ids:
                        LOGGER.info(
                            "drive_same_file_observed_twice",
                            extra={"drive_file_id": entry.file_id, "duplicate_reason": "SAME_DRIVE_FILE"},
                        )
                        continue
                    seen_file_ids.add(entry.file_id)
                    duplicate_reason = None
                    canonical = None
                    if entry.md5_checksum:
                        canonical = md5_canonical.get(entry.md5_checksum)
                        if canonical and canonical != entry.file_id:
                            duplicate_reason = "SAME_HASH"
                        else:
                            md5_canonical[entry.md5_checksum] = entry.file_id
                    signature = (
                        folder.get("product_id"),
                        _normalized_filename(entry.name),
                        entry.size,
                    )
                    if not duplicate_reason and signature[0] and signature[1] and signature[2] is not None:
                        canonical = signature_canonical.get(signature)
                        if canonical and canonical != entry.file_id:
                            duplicate_reason = "SAME_CONTENT"
                        else:
                            signature_canonical[signature] = entry.file_id
                    classification = self.eligibility.classify(
                        conn, entry, folder, duplicate_reason, canonical
                    )
                    text_content = None
                    text_error = None
                    file_kind = _file_kind(entry)
                    if file_kind == "PROMPT_TEXT":
                        try:
                            reader = getattr(self.provider, "read_text", None)
                            if not reader:
                                raise DriveInventoryError("provider nao suporta leitura de texto")
                            text_content = reader(entry).strip()
                        except Exception as exc:
                            text_error = _safe_provider_error(exc)
                    _upsert_drive_file(
                        conn,
                        entry,
                        folder,
                        classification,
                        scan_id,
                        file_kind=file_kind,
                        text_content=text_content,
                        text_read_error=text_error,
                    )
                    if classification["classification"] == "AVAILABLE":
                        counters["available_videos"] += 1
                sync_folder_prompts(conn, folder_entry.file_id)
                conn.commit()
            except Exception as exc:
                counters["folders_failed"] += 1
                _upsert_folder_error(
                    conn, folder_entry, parent_id, path, depth, scan_id, _safe_provider_error(exc)
                )
                conn.commit()
                LOGGER.warning(
                    "drive_folder_scan_failed",
                    extra={"folder_id": folder_entry.file_id, "scan_id": scan_id},
                )

        if not counters["folders_failed"]:
            missing_children = [row["drive_folder_id"] for row in conn.execute(
                """SELECT drive_folder_id FROM drive_folders
                   WHERE parent_drive_folder_id IN (SELECT drive_folder_id FROM drive_folders
                                                    WHERE drive_folder_id IN (%s))
                     AND operational_status='ACTIVE'""" % ",".join("?" for _ in successful_folders),
                tuple(successful_folders),
            ).fetchall() if row["drive_folder_id"] not in seen_folder_ids] if successful_folders else []
            unavailable_children = []
            if len(missing_children) > MAX_MISSING_FOLDER_METADATA_PER_SCAN:
                counters["folders_failed"] += 1
                LOGGER.warning("drive_missing_folder_metadata_limit_exceeded", extra={"count": len(missing_children)})
            for child_id in missing_children:
                if counters["folders_failed"]:
                    break
                try:
                    metadata = self.provider.get_item_metadata(child_id)
                    if metadata is None:
                        reason = "NOT_FOUND"
                    elif metadata.get("trashed"):
                        reason = "TRASHED"
                    else:
                        membership = self.provider.is_within_root(child_id, operational_root.file_id)
                        if membership is not False:
                            raise DriveInventoryError("Pasta ausente da listagem mas ainda dentro da raiz ou indeterminada")
                        reason = "OUTSIDE_ROOT"
                    if reason:
                        unavailable_children.append((child_id, reason))
                except Exception:
                    counters["folders_failed"] += 1
                    LOGGER.warning("drive_missing_folder_metadata_unavailable", extra={"folder_id": child_id})
                    break
            if not counters["folders_failed"]:
                conn.execute("SAVEPOINT unavailable_subtrees")
                try:
                    for child_id, reason in unavailable_children:
                        mark_remote_unavailable(conn, child_id, is_folder=True, reason=reason)
                    conn.execute("RELEASE SAVEPOINT unavailable_subtrees")
                except Exception:
                    conn.execute("ROLLBACK TO SAVEPOINT unavailable_subtrees")
                    conn.execute("RELEASE SAVEPOINT unavailable_subtrees")
                    counters["folders_failed"] += 1
                    LOGGER.warning("drive_missing_folder_reconciliation_failed")
        for folder_id in successful_folders:
            conn.execute(
                """
                UPDATE drive_files
                SET classification = 'MISSING_FROM_DRIVE',
                    classification_reason = 'arquivo nao localizado no ultimo scan bem-sucedido da pasta',
                    missing_since = COALESCE(missing_since, ?), updated_at = ?
                WHERE drive_folder_id = ? AND COALESCE(last_scan_id, 0) <> ?
                """,
                (now_iso(), now_iso(), folder_id, scan_id),
            )
            sync_folder_prompts(conn, folder_id)
        status = "PARTIAL" if counters["folders_failed"] else "COMPLETE"
        finished = now_iso()
        conn.execute(
            """
            UPDATE drive_scan_runs
            SET root_folder_name = ?, operational_root_folder_id = ?,
                operational_root_folder_name = ?, status = ?, folders_seen = ?, folders_succeeded = ?,
                folders_failed = ?, files_seen = ?, videos_seen = ?, available_videos = ?, finished_at = ?
            WHERE id = ?
            """,
            (
                root.name,
                operational_root.file_id,
                operational_root.name,
                status,
                counters["folders_seen"],
                counters["folders_succeeded"],
                counters["folders_failed"],
                counters["files_seen"],
                counters["videos_seen"],
                counters["available_videos"],
                finished,
                scan_id,
            ),
        )
        conn.commit()
        inventories = product_content_inventory(conn, self.config)
        refresh_content_supply_alerts(conn, inventories, self.config)
        result = dict(conn.execute("SELECT * FROM drive_scan_runs WHERE id = ?", (scan_id,)).fetchone())
        result["cached"] = False
        return result


_SCAN_LOCK = threading.Lock()


def scan_drive_inventory(conn, force: bool = False) -> dict[str, Any]:
    config = DriveInventoryConfig.from_env()
    with _SCAN_LOCK:
        try:
            provider = GoogleApiDriveInventoryProvider.from_config(config)
        except DriveInventoryError as exc:
            return record_scan_error(conn, config, str(exc))
        return DriveInventoryScanner(provider, config).scan(conn, force=force)


def record_scan_error(conn, config: DriveInventoryConfig, message: str) -> dict[str, Any]:
    if not force_error_record_needed(conn, config, message):
        row = conn.execute("SELECT * FROM drive_scan_runs ORDER BY id DESC LIMIT 1").fetchone()
        result = dict(row)
        result["cached"] = True
        return result
    timestamp = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO drive_scan_runs (
            account_email, root_folder_id, status, error_message, started_at, finished_at
        ) VALUES (?, ?, 'ERROR', ?, ?, ?)
        """,
        (config.account_email, config.root_folder_id or "NOT_CONFIGURED", message, timestamp, timestamp),
    )
    conn.commit()
    result = dict(conn.execute("SELECT * FROM drive_scan_runs WHERE id = ?", (cursor.lastrowid,)).fetchone())
    result["cached"] = False
    return result


def force_error_record_needed(conn, config: DriveInventoryConfig, message: str) -> bool:
    row = conn.execute("SELECT * FROM drive_scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row or row["status"] != "ERROR" or row["error_message"] != message:
        return True
    finished = _parse_iso(row["finished_at"])
    return not finished or datetime.now(timezone.utc) - finished >= timedelta(seconds=config.cache_seconds)


def set_folder_mapping(
    conn,
    folder_id: str,
    product_id: int | None,
    mapping_status: str | None = None,
    actor: str = "local-dashboard",
    reason: str | None = None,
    *,
    commit: bool = True,
) -> dict[str, Any]:
    folder = conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id = ?", (folder_id,)).fetchone()
    if not folder:
        raise ValueError("Pasta do Drive nao encontrada")
    if folder["operational_status"] != "ACTIVE":
        raise ValueError("Somente pastas do perfil ativo podem ser mapeadas")
    if product_id is not None and not conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
        raise ValueError("Produto nao encontrado")
    target_status = mapping_status or ("MAPPED_TO_PRODUCT" if product_id else "NEEDS_REVIEW")
    if target_status not in MAPPING_STATES:
        raise ValueError("Estado de mapeamento invalido")
    if target_status == "MAPPED_TO_PRODUCT" and product_id is None:
        raise ValueError("Mapeamento de produto exige product_id")
    timestamp = now_iso()
    conn.execute(
        """
        UPDATE drive_folders
        SET product_id = ?, mapping_status = ?, mapping_source = 'MANUAL',
            mapping_confidence = ?, suggested_product_id = NULL,
            suggestion_confidence = NULL, suggestion_reason = NULL, updated_at = ?
        WHERE drive_folder_id = ?
        """,
        (product_id, target_status, 1.0 if product_id else None, timestamp, folder_id),
    )
    conn.execute(
        """
        INSERT INTO drive_folder_mapping_history (
            drive_folder_id, previous_product_id, new_product_id, previous_status,
            new_status, source, actor, reason, changed_at
        ) VALUES (?, ?, ?, ?, ?, 'MANUAL', ?, ?, ?)
        """,
        (
            folder_id,
            folder["product_id"],
            product_id,
            folder["mapping_status"],
            target_status,
            actor,
            reason,
            timestamp,
        ),
    )
    if product_id and target_status == "MAPPED_TO_PRODUCT":
        descendants = conn.execute(
            """
            WITH RECURSIVE tree(folder_id) AS (
                SELECT drive_folder_id FROM drive_folders WHERE drive_folder_id = ?
                UNION ALL
                SELECT f.drive_folder_id FROM drive_folders f JOIN tree t
                  ON f.parent_drive_folder_id = t.folder_id
            ) SELECT folder_id FROM tree
            """,
            (folder_id,),
        ).fetchall()
        descendant_ids = [str(item["folder_id"]) for item in descendants]
        for descendant_id in descendant_ids[1:]:
            conn.execute(
                """
                UPDATE drive_folders SET product_id = ?, mapping_status = 'MAPPED_TO_PRODUCT',
                    mapping_source = 'PARENT_MANUAL', mapping_confidence = 0.99, updated_at = ?
                WHERE drive_folder_id = ? AND COALESCE(mapping_source, '') <> 'MANUAL'
                """,
                (product_id, timestamp, descendant_id),
            )
    if commit:
        conn.commit()
    reprocess_folder_eligibility(conn, folder_id, include_descendants=True, commit=commit)
    sync_folder_prompts(conn, folder_id, commit=commit)
    return dict(conn.execute("SELECT * FROM drive_folders WHERE drive_folder_id = ?", (folder_id,)).fetchone())


def create_and_map_folder_product(conn, folder_id: str, product_name: str) -> dict[str, Any]:
    name = product_name.strip()
    if not name:
        raise ValueError("Nome do produto obrigatorio")
    product_id = upsert_product(conn, name)
    folder = set_folder_mapping(conn, folder_id, product_id, reason="Produto criado pelo mapeamento do Drive")
    return {"product_id": product_id, "folder": folder}


def reprocess_folder_eligibility(conn, folder_id: str, include_descendants: bool = False, *, commit: bool = True) -> int:
    folder_ids = [folder_id]
    if include_descendants:
        folder_ids = [
            str(row["folder_id"])
            for row in conn.execute(
                """
                WITH RECURSIVE tree(folder_id) AS (
                    SELECT drive_folder_id FROM drive_folders WHERE drive_folder_id = ?
                    UNION ALL SELECT f.drive_folder_id FROM drive_folders f JOIN tree t
                      ON f.parent_drive_folder_id = t.folder_id
                ) SELECT folder_id FROM tree
                """,
                (folder_id,),
            ).fetchall()
        ]
    service = VideoPublicationEligibilityService()
    updated = 0
    for current_id in folder_ids:
        folder_row = conn.execute(
            "SELECT * FROM drive_folders WHERE drive_folder_id = ?", (current_id,)
        ).fetchone()
        if not folder_row:
            continue
        folder = dict(folder_row)
        files = conn.execute(
            "SELECT * FROM drive_files WHERE drive_folder_id = ? AND missing_since IS NULL",
            (current_id,),
        ).fetchall()
        for row in files:
            entry = DriveEntry(
                file_id=row["drive_file_id"],
                name=row["name"],
                mime_type=row["mime_type"] or "",
                size=row["size"],
                md5_checksum=row["md5_checksum"],
                created_time=row["created_time"],
                modified_time=row["modified_time"],
                web_view_link=row["web_view_link"],
            )
            classification = service.classify(conn, entry, folder)
            conn.execute(
                """
                UPDATE drive_files SET product_id = ?, content_id = ?, classification = ?,
                    classification_reason = ?, duplicate_reason = ?, canonical_drive_file_id = ?,
                    updated_at = ? WHERE drive_file_id = ?
                """,
                (
                    folder.get("product_id"),
                    classification.get("content_id"),
                    classification["classification"],
                    classification["classification_reason"],
                    classification.get("duplicate_reason"),
                    classification.get("canonical_drive_file_id"),
                    now_iso(),
                    entry.file_id,
                ),
            )
            updated += 1
    if commit:
        conn.commit()
    return updated


def product_content_inventory(
    conn, config: DriveInventoryConfig | None = None
) -> list[dict[str, Any]]:
    config = config or DriveInventoryConfig.from_env()
    products = conn.execute("SELECT id, name FROM products ORDER BY name").fetchall()
    latest_scan = conn.execute("SELECT * FROM drive_scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    scan_status = str(latest_scan["status"]) if latest_scan else "NEVER_SCANNED"
    scan_fresh = bool(latest_scan and _scan_is_recent(latest_scan, config.cache_seconds))
    inventories: list[dict[str, Any]] = []
    for product in products:
        product_id = int(product["id"])
        counts = {
            row["classification"]: int(row["total"])
            for row in conn.execute(
                """
                SELECT df.classification, COUNT(*) AS total
                FROM drive_files df
                JOIN drive_folders f ON f.drive_folder_id = df.drive_folder_id
                WHERE df.product_id = ? AND df.missing_since IS NULL
                  AND df.file_kind = 'VIDEO' AND f.operational_status = 'ACTIVE'
                GROUP BY df.classification
                """,
                (product_id,),
            ).fetchall()
        }
        folders = [
            dict(row)
            for row in conn.execute(
                """
                SELECT drive_folder_id, name, relative_path, folder_type, mapping_source,
                       mapping_confidence, scan_status, scan_error, direct_video_count,
                       direct_prompt_count, last_scanned_at
                FROM drive_folders WHERE product_id = ? AND operational_status = 'ACTIVE'
                ORDER BY relative_path
                """,
                (product_id,),
            ).fetchall()
        ]
        commercial = _commercial_product_metrics(conn, product_id)
        available = counts.get("AVAILABLE", 0)
        queued = counts.get("ALREADY_QUEUED", 0)
        published = counts.get("ALREADY_PUBLISHED", 0)
        recent_publishing_rate = _recent_publishing_rate(conn, product_id)
        rate = recent_publishing_rate or config.publish_target_per_day
        coverage = round(available / rate, 2) if rate else None
        momentum = _sales_momentum(commercial["gmv_7d_minor"], commercial["previous_gmv_7d_minor"])
        supply = _supply_status(available, coverage, config)
        last_video = conn.execute(
            """
            SELECT MAX(df.created_time) AS value FROM drive_files df
            JOIN drive_folders f ON f.drive_folder_id = df.drive_folder_id
            WHERE df.product_id = ? AND df.missing_since IS NULL
              AND df.file_kind = 'VIDEO' AND f.operational_status = 'ACTIVE'
            """,
            (product_id,),
        ).fetchone()["value"]
        last_published = conn.execute(
            "SELECT MAX(published_at) AS value FROM content_items WHERE product_id = ? AND status = 'PUBLISHED'",
            (product_id,),
        ).fetchone()["value"]
        prompt_count = int(conn.execute(
            """
            SELECT COUNT(*) AS total FROM product_prompts
            WHERE product_id = ? AND status = 'ACTIVE' AND source_missing = 0
            """,
            (product_id,),
        ).fetchone()["total"])
        ready_caption_count = int(conn.execute(
            "SELECT COUNT(*) AS total FROM product_captions WHERE product_id = ? AND status = 'READY'",
            (product_id,),
        ).fetchone()["total"])
        inventory = {
            "product_id": product_id,
            "product_name": product["name"],
            "folders": folders,
            "folder_count": len(folders),
            "files_total": sum(counts.values()),
            "drive_stock_count": available + counts.get("NEEDS_REVIEW", 0),
            "available_count": available,
            "queued_count": queued,
            "published_count": published,
            "invalid_count": counts.get("INVALID", 0),
            "duplicate_count": counts.get("DUPLICATE", 0),
            "needs_review_count": counts.get("NEEDS_REVIEW", 0),
            "ignored_count": counts.get("IGNORED", 0),
            "missing_count": counts.get("MISSING_FROM_DRIVE", 0),
            "prompt_count": prompt_count,
            "ready_caption_count": ready_caption_count,
            "last_video_created_at": last_video,
            "last_published_at": last_published,
            "publication_rate_per_day": round(rate, 2),
            "publication_rate_source": "LAST_30_DAYS" if recent_publishing_rate else "CONFIGURED_TARGET",
            "coverage_days": coverage,
            "momentum": momentum,
            "content_supply_status": supply,
            "scan_status": scan_status,
            "scan_fresh": scan_fresh,
            **commercial,
        }
        inventory.update(_score_opportunity(inventory, config))
        inventories.append(inventory)
    return sorted(inventories, key=lambda item: (-item["priority_score"], -item["gmv_7d_minor"]))


def drive_dashboard_payload(conn) -> dict[str, Any]:
    config = DriveInventoryConfig.from_env()
    latest = conn.execute("SELECT * FROM drive_scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    inventory = product_content_inventory(conn, config)
    folders = [
        dict(row)
        for row in conn.execute(
            """
            SELECT f.*, p.name AS product_name, s.name AS suggested_product_name
            FROM drive_folders f
            LEFT JOIN products p ON p.id = f.product_id
            LEFT JOIN products s ON s.id = f.suggested_product_id
            WHERE f.operational_status = 'ACTIVE'
            ORDER BY f.relative_path
            """
        ).fetchall()
    ]
    class_totals = {
        row["classification"]: int(row["total"])
        for row in conn.execute(
            """
            SELECT df.classification, COUNT(*) AS total FROM drive_files df
            JOIN drive_folders f ON f.drive_folder_id = df.drive_folder_id
            WHERE f.operational_status = 'ACTIVE' AND df.file_kind = 'VIDEO'
            GROUP BY df.classification
            """
        ).fetchall()
    }
    mapping_totals = {
        row["mapping_status"]: int(row["total"])
        for row in conn.execute(
            """
            SELECT mapping_status, COUNT(*) AS total FROM drive_folders
            WHERE operational_status = 'ACTIVE' GROUP BY mapping_status
            """
        ).fetchall()
    }
    mapped_confidences = [
        float(row["mapping_confidence"])
        for row in conn.execute(
            """
            SELECT mapping_confidence FROM drive_folders
            WHERE operational_status = 'ACTIVE' AND mapping_confidence IS NOT NULL
            """
        ).fetchall()
    ]
    product_folder_health = dict(conn.execute(
        """
        SELECT
          COUNT(*) AS product_folders,
          SUM(CASE WHEN mapping_status = 'MAPPED_TO_PRODUCT' THEN 1 ELSE 0 END) AS mapped,
          SUM(CASE WHEN mapping_status = 'NEEDS_REVIEW' THEN 1 ELSE 0 END) AS needs_review,
          SUM(CASE WHEN direct_video_count = 0 THEN 1 ELSE 0 END) AS without_videos,
          SUM(CASE WHEN direct_prompt_count > 0 THEN 1 ELSE 0 END) AS with_prompts,
          SUM(CASE WHEN direct_prompt_count = 0 THEN 1 ELSE 0 END) AS without_prompts
        FROM drive_folders
        WHERE operational_status = 'ACTIVE' AND folder_type = 'PRODUCT'
        """
    ).fetchone())
    type_totals = {
        str(row["folder_type"]): int(row["total"])
        for row in conn.execute(
            """
            SELECT folder_type, COUNT(*) AS total FROM drive_folders
            WHERE operational_status = 'ACTIVE' GROUP BY folder_type
            """
        ).fetchall()
    }
    latest_payload = dict(latest) if latest else None
    return {
        "configured": bool(config.root_folder_id and config.credentials_file),
        "account_email": config.account_email,
        "root_folder_id": config.root_folder_id,
        "cache_seconds": config.cache_seconds,
        "latest_scan": latest_payload,
        "scan_fresh": bool(latest and _scan_is_recent(latest, config.cache_seconds)),
        "classification_totals": class_totals,
        "mapping_health": {
            **mapping_totals,
            **{key: int(value or 0) for key, value in product_folder_health.items()},
            "folder_types": type_totals,
            "total": sum(mapping_totals.values()),
            "average_confidence": round(sum(mapped_confidences) / len(mapped_confidences), 3)
            if mapped_confidences
            else None,
        },
        "profiles": [dict(row) for row in conn.execute(
            """
            SELECT * FROM creator_drive_profiles
            WHERE drive_account_email = ?
            ORDER BY CASE status WHEN 'ACTIVE' THEN 0 WHEN 'RESERVE' THEN 1
                     WHEN 'SYSTEM' THEN 2 WHEN 'ARCHIVED' THEN 3 ELSE 4 END, name
            """,
            (config.account_email or "UNCONFIGURED",),
        ).fetchall()],
        "folders": folders,
        "products": product_catalog_options(conn),
        "inventory": inventory,
        "alerts": [dict(row) for row in conn.execute(
            """
            SELECT e.*, p.name AS product_name FROM content_supply_alert_events e
            JOIN products p ON p.id = e.product_id ORDER BY e.created_at DESC, e.id DESC LIMIT 100
            """
        ).fetchall()],
    }


def refresh_content_supply_alerts(
    conn, inventories: list[dict[str, Any]], config: DriveInventoryConfig | None = None
) -> list[dict[str, Any]]:
    config = config or DriveInventoryConfig.from_env()
    events: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for item in inventories:
        product_id = int(item["product_id"])
        active = item["available_count"] < config.watch_threshold and bool(item["folder_count"])
        state_name = "ACTIVE" if active else "RECOVERED"
        severity = item["content_supply_status"]
        previous = conn.execute(
            "SELECT * FROM content_supply_alert_states WHERE product_id = ?", (product_id,)
        ).fetchone()
        should_emit = False
        event_type = "CONTENT_SUPPLY_ALERT" if active else "CONTENT_SUPPLY_RECOVERY"
        if active:
            if not previous or previous["alert_state"] != "ACTIVE":
                should_emit = True
            elif abs(int(previous["available_count"]) - int(item["available_count"])) >= config.alert_count_delta:
                should_emit = True
            elif previous["momentum"] != item["momentum"]:
                should_emit = True
            else:
                alerted = _parse_iso(previous["last_alerted_at"])
                should_emit = not alerted or now - alerted >= timedelta(hours=config.alert_cooldown_hours)
        elif previous and previous["alert_state"] == "ACTIVE":
            should_emit = True
        timestamp = now_iso()
        last_alerted = timestamp if should_emit else (previous["last_alerted_at"] if previous else None)
        conn.execute(
            """
            INSERT INTO content_supply_alert_states (
                product_id, alert_state, severity, available_count, momentum,
                last_alerted_at, last_evaluated_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(product_id) DO UPDATE SET
                alert_state = excluded.alert_state, severity = excluded.severity,
                available_count = excluded.available_count, momentum = excluded.momentum,
                last_alerted_at = excluded.last_alerted_at,
                last_evaluated_at = excluded.last_evaluated_at, updated_at = excluded.updated_at
            """,
            (
                product_id,
                state_name,
                severity,
                item["available_count"],
                item["momentum"],
                last_alerted,
                timestamp,
                timestamp,
            ),
        )
        if should_emit:
            alert_reasons = list(item["priority_reasons"])
            alert_reasons.append(
                f"contexto operacional: {item['available_count']} videos, "
                f"{item['prompt_count']} prompts e {item['ready_caption_count']} legendas prontas"
            )
            cursor = conn.execute(
                """
                INSERT INTO content_supply_alert_events (
                    product_id, event_type, severity, available_count, momentum,
                    score, reasons, data_confidence, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    product_id,
                    event_type,
                    severity,
                    item["available_count"],
                    item["momentum"],
                    item["priority_score"],
                    json.dumps(alert_reasons, ensure_ascii=False),
                    item["data_confidence"],
                    timestamp,
                ),
            )
            events.append(dict(conn.execute(
                "SELECT * FROM content_supply_alert_events WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()))
    conn.commit()
    return events


def is_stock_video_name(name: str) -> bool:
    return bool(STOCK_NAME_PATTERN.search(name or ""))


def _sync_creator_drive_profiles(
    conn,
    config: DriveInventoryConfig,
    root: DriveEntry,
    root_children: list[DriveEntry],
    scan_id: int,
) -> tuple[DriveEntry, int]:
    """Persist the account topology while returning only the active operational root."""
    timestamp = now_iso()
    account_email = config.account_email or "UNCONFIGURED"
    active_id = str(config.active_profile_folder_id or "")
    reserve_ids = set(config.reserve_profile_folder_ids)
    archived_ids = set(config.archived_profile_folder_ids)

    def upsert_profile(entry: DriveEntry, status: str) -> int:
        operational = 1 if status == "ACTIVE" else 0
        conn.execute(
            """
            INSERT INTO creator_drive_profiles (
                drive_account_email, drive_folder_id, name, status, is_operational,
                discovered_at, last_scanned_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drive_folder_id) DO UPDATE SET
                drive_account_email = excluded.drive_account_email,
                name = excluded.name, status = excluded.status,
                is_operational = excluded.is_operational,
                last_scanned_at = excluded.last_scanned_at,
                updated_at = excluded.updated_at
            """,
            (
                account_email,
                entry.file_id,
                entry.name,
                status,
                operational,
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        return int(conn.execute(
            "SELECT id FROM creator_drive_profiles WHERE drive_folder_id = ?", (entry.file_id,)
        ).fetchone()["id"])

    def upsert_shell(
        entry: DriveEntry,
        profile_id: int,
        status: str,
        parent_id: str | None,
        folder_type: str,
    ) -> None:
        mapping_status = "CATEGORY_ONLY" if status == "ACTIVE" else "IGNORED"
        if status == "SYSTEM":
            mapping_status = "ADMINISTRATIVE"
        conn.execute(
            """
            INSERT INTO drive_folders (
                drive_folder_id, parent_drive_folder_id, name, relative_path, depth,
                folder_type, mapping_status, mapping_source, creator_profile_id,
                operational_status, scan_status, last_scan_id, last_scanned_at,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, 'CONFIG', ?, ?, 'SUCCESS', ?, ?, ?, ?)
            ON CONFLICT(drive_folder_id) DO UPDATE SET
                parent_drive_folder_id = excluded.parent_drive_folder_id,
                name = excluded.name, relative_path = excluded.relative_path,
                depth = 0, folder_type = CASE
                    WHEN drive_folders.role_source IN ('MANUAL', 'MIGRATION_HIGH_CONFIDENCE')
                    THEN drive_folders.folder_type ELSE excluded.folder_type END,
                mapping_status = excluded.mapping_status,
                mapping_source = CASE
                    WHEN drive_folders.mapping_source IN ('MANUAL', 'MANUAL_MAPPING', 'MIGRATION_HIGH_CONFIDENCE')
                         AND excluded.operational_status = 'ACTIVE'
                    THEN drive_folders.mapping_source ELSE excluded.mapping_source END,
                product_id = CASE
                    WHEN drive_folders.mapping_source IN ('MANUAL', 'MANUAL_MAPPING', 'MIGRATION_HIGH_CONFIDENCE')
                         AND excluded.operational_status = 'ACTIVE'
                    THEN drive_folders.product_id ELSE NULL END,
                creator_profile_id = excluded.creator_profile_id,
                operational_status = excluded.operational_status,
                scan_status = 'SUCCESS', scan_error = NULL,
                last_scan_id = excluded.last_scan_id,
                last_scanned_at = excluded.last_scanned_at,
                updated_at = excluded.updated_at
            """,
            (
                entry.file_id,
                parent_id,
                entry.name,
                entry.name,
                folder_type,
                mapping_status,
                profile_id,
                status,
                scan_id,
                timestamp,
                timestamp,
                timestamp,
            ),
        )

    system_profile_id = upsert_profile(root, "SYSTEM")
    upsert_shell(root, system_profile_id, "SYSTEM", None, "SYSTEM_DATA")
    active_entry: DriveEntry | None = None
    active_profile_id: int | None = None
    for entry in root_children:
        if not entry.is_folder:
            continue
        if entry.file_id == active_id:
            status = "ACTIVE"
        elif entry.file_id in reserve_ids:
            status = "RESERVE"
        elif entry.file_id in archived_ids:
            status = "ARCHIVED"
        else:
            status = "ARCHIVED"
        profile_id = upsert_profile(entry, status)
        upsert_shell(entry, profile_id, status, root.file_id, "PROFILE")
        conn.execute(
            """
            WITH RECURSIVE tree(folder_id) AS (
                SELECT drive_folder_id FROM drive_folders WHERE drive_folder_id = ?
                UNION ALL
                SELECT f.drive_folder_id FROM drive_folders f
                JOIN tree t ON f.parent_drive_folder_id = t.folder_id
            )
            UPDATE drive_folders SET creator_profile_id = ?,
                operational_status = CASE WHEN ? = 'ACTIVE' THEN operational_status ELSE ? END,
                updated_at = ? WHERE drive_folder_id IN (SELECT folder_id FROM tree)
            """,
            (entry.file_id, profile_id, status, status, timestamp),
        )
        if status != "ACTIVE":
            conn.execute(
                """
                UPDATE drive_files SET classification = 'IGNORED',
                    classification_reason = 'arquivo fora do perfil operacional ativo',
                    updated_at = ? WHERE drive_folder_id IN (
                        WITH RECURSIVE tree(folder_id) AS (
                            SELECT drive_folder_id FROM drive_folders WHERE drive_folder_id = ?
                            UNION ALL
                            SELECT f.drive_folder_id FROM drive_folders f
                            JOIN tree t ON f.parent_drive_folder_id = t.folder_id
                        ) SELECT folder_id FROM tree
                    )
                """,
                (timestamp, entry.file_id),
            )
        else:
            active_entry = entry
            active_profile_id = profile_id

    conn.commit()
    if not active_entry or active_profile_id is None:
        raise DriveInventoryError(
            "Pasta do perfil ativo configurado nao foi encontrada dentro da raiz FLOW."
        )
    return active_entry, active_profile_id


def _upsert_scanned_folder(
    conn,
    config: DriveInventoryConfig,
    entry: DriveEntry,
    parent_id: str | None,
    relative_path: str,
    depth: int,
    direct_video_count: int,
    direct_prompt_count: int,
    direct_other_file_count: int,
    subfolder_count: int,
    scan_id: int,
    products: list[dict[str, Any]],
    creator_profile_id: int | None = None,
    operational_status: str = "ACTIVE",
) -> dict[str, Any]:
    existing_row = conn.execute(
        "SELECT * FROM drive_folders WHERE drive_folder_id = ?", (entry.file_id,)
    ).fetchone()
    existing = dict(existing_row) if existing_row else None
    parent_row = conn.execute(
        "SELECT * FROM drive_folders WHERE drive_folder_id = ?", (parent_id,)
    ).fetchone() if parent_id else None
    folder_type = _classify_folder_type(
        config,
        entry.file_id,
        entry.name,
        depth,
        direct_video_count,
        direct_prompt_count,
        direct_other_file_count,
        subfolder_count,
        parent_row,
    )
    mapping = _resolve_folder_mapping(
        conn, config, existing, parent_row, entry, relative_path, folder_type, products
    )
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO drive_folders (
            drive_folder_id, parent_drive_folder_id, name, relative_path, depth,
            folder_type, mapping_status, product_id, mapping_source, mapping_confidence,
            suggested_product_id, suggestion_confidence, suggestion_reason,
            creator_profile_id, operational_status, direct_video_count,
            direct_prompt_count, direct_other_file_count, direct_subfolder_count,
            scan_status, scan_error,
            last_scan_id, last_scanned_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                  'SUCCESS', NULL, ?, ?, ?, ?)
        ON CONFLICT(drive_folder_id) DO UPDATE SET
            parent_drive_folder_id = excluded.parent_drive_folder_id,
            name = excluded.name, relative_path = excluded.relative_path, depth = excluded.depth,
            folder_type = CASE
                WHEN drive_folders.role_source IN ('MANUAL', 'MIGRATION_HIGH_CONFIDENCE')
                THEN drive_folders.folder_type ELSE excluded.folder_type END,
            mapping_status = excluded.mapping_status,
            product_id = excluded.product_id, mapping_source = excluded.mapping_source,
            mapping_confidence = excluded.mapping_confidence,
            suggested_product_id = excluded.suggested_product_id,
            suggestion_confidence = excluded.suggestion_confidence,
            suggestion_reason = excluded.suggestion_reason,
            creator_profile_id = excluded.creator_profile_id,
            operational_status = excluded.operational_status,
            direct_video_count = excluded.direct_video_count,
            direct_prompt_count = excluded.direct_prompt_count,
            direct_other_file_count = excluded.direct_other_file_count,
            direct_subfolder_count = excluded.direct_subfolder_count,
            scan_status = 'SUCCESS', scan_error = NULL, last_scan_id = excluded.last_scan_id,
            last_scanned_at = excluded.last_scanned_at, updated_at = excluded.updated_at
        """,
        (
            entry.file_id,
            parent_id,
            entry.name,
            relative_path,
            depth,
            folder_type,
            mapping["mapping_status"],
            mapping.get("product_id"),
            mapping.get("mapping_source"),
            mapping.get("mapping_confidence"),
            mapping.get("suggested_product_id"),
            mapping.get("suggestion_confidence"),
            mapping.get("suggestion_reason"),
            creator_profile_id,
            operational_status,
            direct_video_count,
            direct_prompt_count,
            direct_other_file_count,
            subfolder_count,
            scan_id,
            timestamp,
            existing.get("created_at") if existing else timestamp,
            timestamp,
        ),
    )
    sync_drive_product_name(conn, entry.file_id, entry.name)
    return dict(conn.execute(
        "SELECT * FROM drive_folders WHERE drive_folder_id = ?", (entry.file_id,)
    ).fetchone())


def _upsert_folder_error(
    conn, entry: DriveEntry, parent_id: str | None, path: str, depth: int, scan_id: int, error: str
) -> None:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO drive_folders (
            drive_folder_id, parent_drive_folder_id, name, relative_path, depth,
            scan_status, scan_error, last_scan_id, last_scanned_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'ERROR', ?, ?, ?, ?, ?)
        ON CONFLICT(drive_folder_id) DO UPDATE SET
            parent_drive_folder_id = excluded.parent_drive_folder_id, name = excluded.name,
            relative_path = excluded.relative_path, depth = excluded.depth,
            scan_status = 'ERROR', scan_error = excluded.scan_error,
            last_scan_id = excluded.last_scan_id, last_scanned_at = excluded.last_scanned_at,
            updated_at = excluded.updated_at
        """,
        (entry.file_id, parent_id, entry.name, path, depth, error, scan_id, timestamp, timestamp, timestamp),
    )


def _upsert_drive_file(
    conn,
    entry: DriveEntry,
    folder: dict[str, Any],
    classification: dict[str, Any],
    scan_id: int,
    *,
    file_kind: str,
    text_content: str | None = None,
    text_read_error: str | None = None,
) -> None:
    timestamp = now_iso()
    text_hash = (
        hashlib.sha256(text_content.encode("utf-8")).hexdigest() if text_content is not None else None
    )
    conn.execute(
        """
        INSERT INTO drive_files (
            drive_file_id, drive_folder_id, name, mime_type, size, md5_checksum,
            created_time, modified_time, web_view_link, thumbnail_link, product_id, content_id,
            classification, classification_reason, duplicate_reason,
            canonical_drive_file_id, last_scan_id, last_seen_at, missing_since,
            file_kind, text_content, text_content_hash, text_read_error,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(drive_file_id) DO UPDATE SET
            drive_folder_id = excluded.drive_folder_id, name = excluded.name,
            mime_type = excluded.mime_type, size = excluded.size,
            md5_checksum = excluded.md5_checksum, created_time = excluded.created_time,
            modified_time = excluded.modified_time, web_view_link = excluded.web_view_link,
            thumbnail_link = excluded.thumbnail_link,
            product_id = excluded.product_id, content_id = excluded.content_id,
            classification = excluded.classification,
            classification_reason = excluded.classification_reason,
            duplicate_reason = excluded.duplicate_reason,
            canonical_drive_file_id = excluded.canonical_drive_file_id,
            last_scan_id = excluded.last_scan_id, last_seen_at = excluded.last_seen_at,
            missing_since = NULL, file_kind = excluded.file_kind,
            text_content = excluded.text_content,
            text_content_hash = excluded.text_content_hash,
            text_read_error = excluded.text_read_error,
            updated_at = excluded.updated_at
        """,
        (
            entry.file_id,
            folder["drive_folder_id"],
            entry.name,
            entry.mime_type,
            entry.size,
            entry.md5_checksum,
            entry.created_time,
            entry.modified_time,
            entry.web_view_link,
            entry.thumbnail_link,
            folder.get("product_id"),
            classification.get("content_id"),
            classification["classification"],
            classification["classification_reason"],
            classification.get("duplicate_reason"),
            classification.get("canonical_drive_file_id"),
            scan_id,
            timestamp,
            file_kind,
            text_content,
            text_hash,
            text_read_error,
            timestamp,
            timestamp,
        ),
    )


def _resolve_folder_mapping(
    conn,
    config: DriveInventoryConfig,
    existing: dict[str, Any] | None,
    parent,
    entry: DriveEntry,
    path: str,
    folder_type: str,
    products: list[dict[str, Any]],
) -> dict[str, Any]:
    if existing and existing.get("mapping_source") in {
        "MANUAL", "MANUAL_MAPPING", "MIGRATION_HIGH_CONFIDENCE"
    }:
        return {
            "mapping_status": existing["mapping_status"],
            "product_id": existing.get("product_id"),
            "mapping_source": existing["mapping_source"],
            "mapping_confidence": existing.get("mapping_confidence"),
        }
    if entry.file_id in config.ignored_folder_ids:
        return {"mapping_status": "IGNORED", "mapping_source": "CONFIG"}
    if folder_type == "ADMINISTRATIVE":
        return {"mapping_status": "ADMINISTRATIVE", "mapping_source": "CONFIG"}
    if parent and parent["mapping_status"] == "MAPPED_TO_PRODUCT" and parent["product_id"]:
        return {
            "mapping_status": "MAPPED_TO_PRODUCT",
            "product_id": int(parent["product_id"]),
            "mapping_source": "PARENT_MAPPING",
            "mapping_confidence": 0.98,
        }
    if folder_type in {"PROFILE", "CATEGORY", "SUBCATEGORY"}:
        return {"mapping_status": "CATEGORY_ONLY", "mapping_source": "DETERMINISTIC"}
    suggestion = _best_product_match(entry.name, path, products)
    if suggestion and suggestion["confidence"] >= 0.9 and not suggestion["ambiguous"]:
        return {
            "mapping_status": "MAPPED_TO_PRODUCT",
            "product_id": suggestion["product_id"],
            "mapping_source": suggestion["method"],
            "mapping_confidence": suggestion["confidence"],
        }
    if suggestion:
        return {
            "mapping_status": "NEEDS_REVIEW",
            "mapping_source": "DETERMINISTIC",
            "suggested_product_id": suggestion["product_id"],
            "suggestion_confidence": suggestion["confidence"],
            "suggestion_reason": suggestion["reason"],
        }
    return {"mapping_status": "NEEDS_REVIEW", "mapping_source": "DETERMINISTIC"}


def _product_candidates(conn) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT p.id, p.name, p.tiktok_product_name,
               GROUP_CONCAT(DISTINCT a.alias) AS aliases,
               GROUP_CONCAT(DISTINCT r.external_product_name) AS external_names
        FROM products p
        LEFT JOIN product_aliases a ON a.product_id = p.id
        LEFT JOIN product_external_references r ON r.product_id = p.id
        GROUP BY p.id
        """
    ).fetchall():
        names = [row["name"], row["tiktok_product_name"]]
        names.extend(str(row["aliases"] or "").split(","))
        names.extend(str(row["external_names"] or "").split(","))
        normalized = sorted({_normalize(value) for value in names if value and _normalize(value)})
        candidates.append({"product_id": int(row["id"]), "name": row["name"], "names": normalized})
    return candidates


def _best_product_match(
    folder_name: str, path: str, products: list[dict[str, Any]]
) -> dict[str, Any] | None:
    folder_normalized = _normalize(folder_name)
    path_normalized = _normalize(path)
    scored: list[dict[str, Any]] = []
    for product in products:
        best = 0.0
        method = "PATH_TOKENS"
        reason = "tokens do caminho e do produto"
        for candidate in product["names"]:
            if folder_normalized == candidate:
                best, method, reason = 1.0, "EXACT_NORMALIZED_NAME", "nome normalizado exato"
                break
            folder_tokens = set(folder_normalized.split())
            candidate_tokens = set(candidate.split())
            if folder_tokens and candidate_tokens:
                overlap = len(folder_tokens & candidate_tokens) / len(candidate_tokens)
                union = len(folder_tokens | candidate_tokens)
                jaccard = len(folder_tokens & candidate_tokens) / union if union else 0
                best = max(best, round(0.65 * overlap + 0.35 * jaccard, 3))
            if candidate and candidate in path_normalized:
                best = max(best, 0.92)
                method, reason = "EXACT_PATH_SEGMENT", "nome do produto presente no caminho"
        if best >= 0.55:
            scored.append({
                "product_id": product["product_id"],
                "confidence": best,
                "method": method,
                "reason": reason,
            })
    if not scored:
        return None
    scored.sort(key=lambda item: (-item["confidence"], item["product_id"]))
    winner = scored[0]
    winner["ambiguous"] = len(scored) > 1 and scored[1]["confidence"] >= winner["confidence"] - 0.05
    if winner["ambiguous"]:
        winner["reason"] += "; mais de um produto com confianca semelhante"
    return winner


def _classify_folder_type(
    config: DriveInventoryConfig,
    folder_id: str,
    name: str,
    depth: int,
    videos: int,
    prompts: int,
    other_files: int,
    subfolders: int,
    parent,
) -> str:
    normalized = _normalize(name)
    administrative = {_normalize(item) for item in config.administrative_names}
    if folder_id in config.ignored_folder_ids or any(
        normalized == item or normalized.endswith(f" {item}") for item in administrative
    ):
        return "ADMINISTRATIVE"
    content_tokens = {"videos gerados", "prontos para postar", "gera viral", "gera frames", "outputs"}
    if any(normalized == item or normalized.endswith(f" {item}") for item in content_tokens):
        return "CONTENT"
    if folder_id == config.active_profile_folder_id:
        return "PROFILE"
    if parent and parent["mapping_status"] == "MAPPED_TO_PRODUCT":
        return "VARIANT"
    if videos or prompts or other_files:
        return "PRODUCT"
    if depth <= 1:
        return "CATEGORY"
    if subfolders:
        return "SUBCATEGORY"
    return "UNKNOWN"


def _match_content(conn, entry: DriveEntry, product_id: int) -> tuple[dict[str, Any] | None, str | None]:
    row = conn.execute(
        "SELECT * FROM content_items WHERE google_drive_file_id = ?", (entry.file_id,)
    ).fetchone()
    if row:
        return dict(row), "DRIVE_FILE_ID"
    if entry.md5_checksum:
        row = conn.execute(
            "SELECT * FROM content_items WHERE product_id = ? AND file_hash = ?",
            (product_id, entry.md5_checksum),
        ).fetchone()
        if row:
            return dict(row), "SAME_HASH"
    rows = conn.execute(
        """
        SELECT * FROM content_items
        WHERE product_id = ? AND lower(original_filename) = lower(?)
          AND (google_drive_file_id IS NULL OR google_drive_file_id = '')
        """,
        (product_id, entry.name),
    ).fetchall()
    if len(rows) == 1:
        return dict(rows[0]), "FILENAME_PRODUCT"
    return None, None


def _possible_content_duplicate(conn, entry: DriveEntry, product_id: int) -> dict[str, Any] | None:
    if entry.size is None:
        return None
    row = conn.execute(
        """
        SELECT * FROM content_items WHERE product_id = ? AND lower(original_filename) = lower(?)
          AND file_size = ? LIMIT 1
        """,
        (product_id, entry.name, entry.size),
    ).fetchone()
    return dict(row) if row else None


def _eligibility(
    classification: str,
    reason: str,
    duplicate_reason: str | None = None,
    canonical_drive_file_id: str | None = None,
    content_id: int | None = None,
) -> dict[str, Any]:
    return {
        "classification": classification,
        "classification_reason": reason,
        "duplicate_reason": duplicate_reason,
        "canonical_drive_file_id": canonical_drive_file_id,
        "content_id": content_id,
    }


def _commercial_product_metrics(conn, product_id: int) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT
          COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-24 hours') THEN i.gmv_minor ELSE 0 END), 0) AS gmv_24h_minor,
          COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-7 days') THEN i.gmv_minor ELSE 0 END), 0) AS gmv_7d_minor,
          COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-14 days') AND datetime(o.order_created_at, '-3 hours') < datetime('now', '-3 hours', '-7 days') THEN i.gmv_minor ELSE 0 END), 0) AS previous_gmv_7d_minor,
          COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-30 days') THEN i.gmv_minor ELSE 0 END), 0) AS gmv_30d_minor,
          COUNT(DISTINCT CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-24 hours') THEN o.id END) AS orders_24h,
          COUNT(DISTINCT CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-7 days') THEN o.id END) AS orders_7d,
          COUNT(DISTINCT CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-30 days') THEN o.id END) AS orders_30d,
          COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-7 days') THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_7d_minor,
          COUNT(DISTINCT CASE WHEN i.content_id IS NOT NULL THEN i.content_id END) AS selling_content_count
        FROM affiliate_order_items i JOIN affiliate_orders o ON o.id = i.affiliate_order_id
        WHERE i.product_id = ? AND o.provider = 'tiktok_report'
        """,
        (product_id,),
    ).fetchone()
    return dict(row)


def _recent_publishing_rate(conn, product_id: int) -> float:
    count = int(conn.execute(
        """
        SELECT COUNT(*) AS total FROM content_items
        WHERE product_id = ? AND status = 'PUBLISHED'
          AND datetime(published_at) >= datetime('now', '-30 days')
        """,
        (product_id,),
    ).fetchone()["total"])
    return count / 30 if count else 0.0


def _sales_momentum(current: int, previous: int) -> str:
    current = int(current or 0)
    previous = int(previous or 0)
    if current == 0 and previous == 0:
        return "INSUFFICIENT_DATA"
    if previous == 0 and current > 0:
        return "ACCELERATING"
    ratio = current / previous if previous else 0
    if ratio >= 1.25:
        return "ACCELERATING"
    if ratio <= 0.75:
        return "DECLINING"
    return "STABLE"


def _supply_status(available: int, coverage: float | None, config: DriveInventoryConfig) -> str:
    if available == 0 or (coverage is not None and coverage < 1):
        return "CRITICAL"
    if available < config.low_threshold or (coverage is not None and coverage < 2):
        return "LOW"
    if available < config.watch_threshold or (coverage is not None and coverage < 4):
        return "WATCH"
    return "HEALTHY"


def _score_opportunity(item: dict[str, Any], config: DriveInventoryConfig) -> dict[str, Any]:
    score = 0
    reasons: list[str] = []
    gmv = int(item["gmv_7d_minor"] or 0)
    orders = int(item["orders_7d"] or 0)
    available = int(item["available_count"])
    if gmv >= 50_000:
        score += 30
        reasons.append("GMV 7d >= R$ 500")
    elif gmv >= 10_000:
        score += 20
        reasons.append("GMV 7d >= R$ 100")
    elif gmv > 0:
        score += 10
        reasons.append("GMV recente positivo")
    if orders >= 10:
        score += 20
        reasons.append("10+ pedidos em 7d")
    elif orders >= 3:
        score += 10
        reasons.append("3+ pedidos em 7d")
    if item["momentum"] == "ACCELERATING":
        score += 20
        reasons.append("vendas acelerando")
    elif item["momentum"] == "STABLE":
        score += 5
        reasons.append("vendas estaveis")
    elif item["momentum"] == "DECLINING":
        score -= 5
        reasons.append("vendas em queda")
    if available == 0:
        score += 30
        reasons.append("nenhum video elegivel no Drive")
    elif available < config.low_threshold:
        score += 20
        reasons.append(f"menos de {config.low_threshold} videos elegiveis")
    elif available < config.watch_threshold:
        score += 10
        reasons.append(f"menos de {config.watch_threshold} videos elegiveis")
    coverage = item.get("coverage_days")
    if coverage is not None and coverage < 2:
        score += 20
        reasons.append("cobertura menor que 2 dias")
    elif coverage is not None and coverage < 4:
        score += 10
        reasons.append("cobertura menor que 4 dias")
    if not item["queued_count"]:
        score += 5
        reasons.append("fila sem video deste produto")
    days_since = _days_since(item.get("last_published_at"))
    if days_since is None or days_since >= 3:
        score += 10
        reasons.append("sem publicacao recente")
    score = max(0, min(100, score))
    confidence = "HIGH" if gmv > 0 and item["scan_status"] == "COMPLETE" and item["scan_fresh"] else "MEDIUM"
    if gmv == 0 or not item["folder_count"] or item["scan_status"] in {"ERROR", "NEVER_SCANNED"}:
        confidence = "LOW"
    return {
        "priority_score": score,
        "priority_level": "HIGH" if score >= 60 else "MEDIUM" if score >= 35 else "LOW",
        "priority_reasons": reasons,
        "data_confidence": confidence,
        "days_since_last_publication": days_since,
        "ready_contents": available,
        "queued_contents": item["queued_count"],
    }


def _fresh_scan(conn, config: DriveInventoryConfig):
    row = conn.execute(
        "SELECT * FROM drive_scan_runs WHERE root_folder_id = ? ORDER BY id DESC LIMIT 1",
        (config.root_folder_id,),
    ).fetchone()
    if row and row["status"] in {"COMPLETE", "PARTIAL"} and _scan_is_recent(row, config.cache_seconds):
        return row
    return None


def _scan_is_recent(row, cache_seconds: int) -> bool:
    finished = _parse_iso(row["finished_at"])
    return bool(finished and datetime.now(timezone.utc) - finished <= timedelta(seconds=cache_seconds))


def _finish_scan_error(conn, scan_id: int, message: str) -> dict[str, Any]:
    conn.execute(
        "UPDATE drive_scan_runs SET status = 'ERROR', error_message = ?, finished_at = ? WHERE id = ?",
        (message, now_iso(), scan_id),
    )
    conn.commit()
    result = dict(conn.execute("SELECT * FROM drive_scan_runs WHERE id = ?", (scan_id,)).fetchone())
    result["cached"] = False
    return result


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _days_since(value: str | None) -> int | None:
    parsed = _parse_iso(value)
    return (datetime.now(timezone.utc) - parsed).days if parsed else None


def _normalize(value: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(value or ""))
    ascii_value = raw.encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.findall(r"[a-z0-9]+", ascii_value))


def _normalized_filename(value: str) -> str:
    return _normalize(Path(value).stem)


def _is_mp4(entry: DriveEntry) -> bool:
    return entry.mime_type == "video/mp4" or entry.name.lower().endswith(".mp4")


def _is_prompt_text(entry: DriveEntry) -> bool:
    name = entry.name.lower()
    return (
        entry.mime_type in {
            "text/plain",
            "text/markdown",
            "application/markdown",
            "application/vnd.google-apps.document",
        }
        or name.endswith(".txt")
        or name.endswith(".md")
        or name.endswith(".markdown")
    )


def _file_kind(entry: DriveEntry) -> str:
    if _is_mp4(entry):
        return "VIDEO"
    if _is_prompt_text(entry):
        return "PROMPT_TEXT"
    if entry.name.startswith(".") or entry.mime_type in {
        "application/vnd.google-apps.shortcut",
        "application/vnd.google-apps.form",
    }:
        return "IGNORED"
    return "OTHER"


def _drive_fields() -> str:
    return "id,name,mimeType,parents,size,md5Checksum,createdTime,modifiedTime,webViewLink,thumbnailLink"


def _safe_provider_error(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    return text[:500] or exc.__class__.__name__


def _csv_env(name: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, "").split(",") if item.strip()]


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default
