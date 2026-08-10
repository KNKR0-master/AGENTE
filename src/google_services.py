from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.store import now_iso


LOGGER = logging.getLogger(__name__)


class ExternalServiceUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class DriveReference:
    file_id: str | None
    url: str | None
    local_mirror_path: str | None = None


class GoogleDriveService:
    """Provider isolado.

    Nesta aplicacao local, credenciais Google server-side ainda nao existem.
    Para manter o source of truth local seguro, o modo padrao grava um espelho
    local em `data/google_drive_mirror` e registra o sync como pendente.
    """

    def __init__(self, root_folder_id: str | None = None, mirror_root: Path | None = None) -> None:
        self.root_folder_id = root_folder_id or os.getenv("GOOGLE_DRIVE_TIKTOK_ROOT_FOLDER_ID")
        self.credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        self.mirror_root = mirror_root or Path(os.getenv("GOOGLE_DRIVE_LOCAL_MIRROR", "data/google_drive_mirror"))

    @property
    def configured(self) -> bool:
        return bool(self.root_folder_id and self.credentials_file and Path(self.credentials_file).exists())

    def upload_content(self, conn, content_id: int) -> DriveReference:
        row = conn.execute("SELECT id, content_code, local_path FROM content_items WHERE id = ?", (content_id,)).fetchone()
        if not row:
            raise ValueError("Conteudo nao encontrado")
        source = Path(row["local_path"] or "")
        if not source.exists():
            raise FileNotFoundError(f"Arquivo local nao encontrado: {source}")

        key = f"drive-upload-content-{content_id}-{source.stat().st_size}-{source.name}"
        previous = _sync_event(conn, key)
        if previous and previous["status"] == "SUCCESS":
            payload = json.loads(previous["response_reference"] or "{}")
            return DriveReference(payload.get("file_id"), payload.get("url"), payload.get("local_mirror_path"))

        timestamp = now_iso()
        if not self.configured:
            self.mirror_root.mkdir(parents=True, exist_ok=True)
            destination = self.mirror_root / "Ready" / str(row["content_code"])
            destination.mkdir(parents=True, exist_ok=True)
            target = destination / source.name
            shutil.copy2(source, target)
            response = {"file_id": None, "url": None, "local_mirror_path": str(target)}
            _record_sync(
                conn,
                provider="google_drive",
                object_type="content",
                object_id=content_id,
                operation="upload_content",
                idempotency_key=key,
                status="PENDING_CONFIG",
                response_reference=response,
                error_message="Configure GOOGLE_APPLICATION_CREDENTIALS e GOOGLE_DRIVE_TIKTOK_ROOT_FOLDER_ID para upload real.",
            )
            LOGGER.warning("drive_upload_pending_config", extra={"content_id": content_id})
            return DriveReference(None, None, str(target))

        raise ExternalServiceUnavailable(
            "Upload real para Google Drive ainda depende da biblioteca Google server-side configurada no ambiente."
        )

    def move_content(self, conn, content_id: int, target_state: str) -> DriveReference:
        row = conn.execute("SELECT google_drive_file_id, google_drive_url, local_path FROM content_items WHERE id = ?", (content_id,)).fetchone()
        if not row:
            raise ValueError("Conteudo nao encontrado")
        key = f"drive-move-content-{content_id}-{target_state}"
        _record_sync(
            conn,
            provider="google_drive",
            object_type="content",
            object_id=content_id,
            operation=f"move_content:{target_state}",
            idempotency_key=key,
            status="PENDING_CONFIG" if not self.configured else "PENDING_PROVIDER",
            response_reference={"target_state": target_state, "url": row["google_drive_url"]},
            error_message=None if self.configured else "Movimentacao real no Drive pendente de credenciais.",
        )
        return DriveReference(row["google_drive_file_id"], row["google_drive_url"], row["local_path"])

    def create_folder(self, name: str) -> str:
        if not self.configured:
            path = self.mirror_root / name
            path.mkdir(parents=True, exist_ok=True)
            return str(path)
        raise ExternalServiceUnavailable("Criacao real de pasta no Drive pendente de provider Google configurado.")

    def get_shareable_reference(self, file_id: str | None) -> str | None:
        if not file_id:
            return None
        return f"https://drive.google.com/file/d/{file_id}/view"


class GoogleSheetsQueueService:
    def __init__(self, spreadsheet_id: str | None = None) -> None:
        self.spreadsheet_id = spreadsheet_id or os.getenv("GOOGLE_SHEETS_PUBLISHING_QUEUE_ID")
        self.credentials_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

    @property
    def configured(self) -> bool:
        return bool(self.spreadsheet_id and self.credentials_file and Path(self.credentials_file).exists())

    def sync_content(self, conn, content_id: int) -> None:
        key = f"sheets-sync-content-{content_id}"
        status = "PENDING_PROVIDER" if self.configured else "PENDING_CONFIG"
        _record_sync(
            conn,
            provider="google_sheets",
            object_type="content",
            object_id=content_id,
            operation="sync_publishing_queue",
            idempotency_key=key,
            status=status,
            response_reference={"spreadsheet_id": self.spreadsheet_id},
            error_message=None if self.configured else "Configure GOOGLE_SHEETS_PUBLISHING_QUEUE_ID e credenciais Google.",
        )


def _sync_event(conn, idempotency_key: str):
    return conn.execute(
        "SELECT * FROM external_sync_events WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()


def _record_sync(
    conn,
    provider: str,
    object_type: str,
    object_id: int,
    operation: str,
    idempotency_key: str,
    status: str,
    response_reference: dict[str, Any] | None = None,
    error_message: str | None = None,
) -> None:
    timestamp = now_iso()
    conn.execute(
        """
        INSERT INTO external_sync_events (
            provider, object_type, object_id, operation, idempotency_key,
            status, response_reference, error_message, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(idempotency_key) DO UPDATE SET
            status = excluded.status,
            response_reference = excluded.response_reference,
            error_message = excluded.error_message,
            updated_at = excluded.updated_at
        """,
        (
            provider,
            object_type,
            object_id,
            operation,
            idempotency_key,
            status,
            json.dumps(response_reference or {}, ensure_ascii=False),
            error_message,
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
