from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import time
import unicodedata
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from src.store import now_iso, stable_hash


LOGGER = logging.getLogger(__name__)

CONTENT_STORAGE = Path("data") / "content"


class ContentStatus(str, Enum):
    DRAFT = "DRAFT"
    PROCESSING = "PROCESSING"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    READY = "READY"
    QUEUED = "QUEUED"
    SENT_TO_PHONE = "SENT_TO_PHONE"
    PUBLISHED = "PUBLISHED"
    FAILED = "FAILED"
    ARCHIVED = "ARCHIVED"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    WARNING = "WARNING"
    FAIL = "FAIL"


VALID_TRANSITIONS: dict[ContentStatus, set[ContentStatus]] = {
    ContentStatus.DRAFT: {ContentStatus.PROCESSING, ContentStatus.NEEDS_REVIEW, ContentStatus.ARCHIVED},
    ContentStatus.PROCESSING: {
        ContentStatus.NEEDS_REVIEW,
        ContentStatus.READY,
        ContentStatus.FAILED,
        ContentStatus.ARCHIVED,
    },
    ContentStatus.NEEDS_REVIEW: {ContentStatus.READY, ContentStatus.FAILED, ContentStatus.ARCHIVED},
    ContentStatus.READY: {ContentStatus.QUEUED, ContentStatus.NEEDS_REVIEW, ContentStatus.ARCHIVED},
    ContentStatus.QUEUED: {
        ContentStatus.SENT_TO_PHONE,
        ContentStatus.PUBLISHED,
        ContentStatus.READY,
        ContentStatus.FAILED,
        ContentStatus.ARCHIVED,
    },
    ContentStatus.SENT_TO_PHONE: {ContentStatus.PUBLISHED, ContentStatus.QUEUED, ContentStatus.FAILED},
    ContentStatus.PUBLISHED: {ContentStatus.ARCHIVED},
    ContentStatus.FAILED: {ContentStatus.PROCESSING, ContentStatus.NEEDS_REVIEW, ContentStatus.ARCHIVED},
    ContentStatus.ARCHIVED: set(),
}


class InvalidTransition(ValueError):
    pass


@dataclass(frozen=True)
class VideoProbeResult:
    validation_status: ValidationStatus
    results: dict[str, Any]


def assert_transition(current: str, target: str) -> None:
    current_status = ContentStatus(current)
    target_status = ContentStatus(target)
    if target_status == current_status:
        return
    if target_status not in VALID_TRANSITIONS[current_status]:
        raise InvalidTransition(f"Transicao invalida: {current_status.value} -> {target_status.value}")


def set_content_status(conn, content_id: int, status: str, **fields: Any) -> None:
    row = conn.execute("SELECT status FROM content_items WHERE id = ?", (content_id,)).fetchone()
    if not row:
        raise ValueError("Conteudo nao encontrado")
    assert_transition(str(row["status"]), status)
    payload = {"status": status, "updated_at": now_iso(), **fields}
    assignments = ", ".join(f"{key} = ?" for key in payload)
    conn.execute(
        f"UPDATE content_items SET {assignments} WHERE id = ?",
        tuple(payload.values()) + (content_id,),
    )


def product_prefix(name: str, sku: str | None = None) -> str:
    raw = (sku or name or "CONTENT").strip()
    normalized = unicodedata.normalize("NFKD", raw)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    tokens = re.findall(r"[A-Za-z0-9]+", ascii_only.upper())
    if not tokens:
        return "CONTENT"
    prefix = "".join(tokens[:2])
    return prefix[:12] or "CONTENT"


def next_content_code(conn, product_id: int) -> str:
    product = conn.execute(
        "SELECT name, sku_internal_code FROM products WHERE id = ?",
        (product_id,),
    ).fetchone()
    if not product:
        raise ValueError("Produto nao encontrado")
    prefix = product_prefix(product["name"], product["sku_internal_code"])
    rows = conn.execute(
        "SELECT content_code FROM content_items WHERE content_code LIKE ?",
        (f"{prefix}-%",),
    ).fetchall()
    max_number = 0
    for row in rows:
        match = re.search(r"-(\d+)$", str(row["content_code"]))
        if match:
            max_number = max(max_number, int(match.group(1)))
    return f"{prefix}-{max_number + 1:04d}"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wait_for_stable_file(path: Path, checks: int = 3, delay: float = 0.4) -> None:
    last_size = -1
    stable_count = 0
    while stable_count < checks:
        size = path.stat().st_size
        if size == last_size:
            stable_count += 1
        else:
            stable_count = 0
            last_size = size
        time.sleep(delay)


def probe_video(path: Path, ffprobe_bin: str = "ffprobe") -> VideoProbeResult:
    if not path.exists() or not path.is_file():
        return VideoProbeResult(ValidationStatus.FAIL, {"valid_file": False, "error": "Arquivo nao encontrado"})

    command = [
        ffprobe_bin,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, check=True, timeout=30)
        payload = json.loads(completed.stdout or "{}")
    except FileNotFoundError:
        return VideoProbeResult(
            ValidationStatus.WARNING,
            {
                "valid_file": True,
                "ffprobe_available": False,
                "file_size": path.stat().st_size,
                "error": "ffprobe nao encontrado; metadata tecnica nao verificada",
            },
        )
    except (subprocess.SubprocessError, json.JSONDecodeError) as exc:
        return VideoProbeResult(
            ValidationStatus.FAIL,
            {"valid_file": False, "ffprobe_available": True, "error": str(exc), "file_size": path.stat().st_size},
        )

    streams = payload.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    audio_present = any(stream.get("codec_type") == "audio" for stream in streams)
    if not video_stream:
        return VideoProbeResult(
            ValidationStatus.FAIL,
            {"valid_file": False, "ffprobe_available": True, "error": "Stream de video nao encontrado"},
        )

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = _float(video_stream.get("duration")) or _float(payload.get("format", {}).get("duration"))
    aspect_ratio = width / height if height else 0
    vertical = height > width
    near_9_16 = abs(aspect_ratio - 9 / 16) <= 0.05
    file_size = int(payload.get("format", {}).get("size") or path.stat().st_size)
    codec = video_stream.get("codec_name")
    rate = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    try:
        numerator, denominator = str(rate).split("/", 1)
        fps = float(numerator) / float(denominator) if float(denominator) else None
    except (TypeError, ValueError, ZeroDivisionError):
        fps = None

    result = {
        "valid_file": True,
        "ffprobe_available": True,
        "codec": codec,
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "resolution": f"{width}x{height}" if width and height else None,
        "aspect_ratio": aspect_ratio,
        "vertical": vertical,
        "near_9_16": near_9_16,
        "audio_present": audio_present,
        "file_size": file_size,
    }

    if not duration or width <= 0 or height <= 0:
        status = ValidationStatus.FAIL
    elif vertical and near_9_16 and audio_present:
        status = ValidationStatus.PASS
    else:
        status = ValidationStatus.WARNING
    return VideoProbeResult(status, result)


def import_content_from_path(
    conn,
    product_id: int,
    source_path: str | Path,
    caption: str | None = None,
    hashtags: str | None = None,
    priority: int = 50,
    generation_source: str | None = None,
    campaign: str | None = None,
    batch: str | None = None,
    storage_root: Path = CONTENT_STORAGE,
) -> dict[str, Any]:
    path = Path(source_path)
    wait_for_stable_file(path)
    file_hash = file_sha256(path)
    existing = conn.execute("SELECT * FROM content_items WHERE file_hash = ?", (file_hash,)).fetchone()
    if existing:
        LOGGER.info("content_import_duplicate", extra={"content_id": existing["id"], "file_hash": file_hash})
        return {"content_id": int(existing["id"]), "content_code": existing["content_code"], "duplicate": True}

    timestamp = now_iso()
    code = next_content_code(conn, product_id)
    product_dir = storage_root / code.split("-")[0]
    product_dir.mkdir(parents=True, exist_ok=True)
    extension = path.suffix.lower() or ".mp4"
    destination = product_dir / f"{code}{extension}"
    shutil.copy2(path, destination)

    probe = probe_video(destination, os.getenv("FFPROBE_BIN", "ffprobe"))
    results = probe.results
    status = ContentStatus.READY if probe.validation_status == ValidationStatus.PASS else ContentStatus.NEEDS_REVIEW
    cursor = conn.execute(
        """
        INSERT INTO content_items (
            content_code, product_id, original_filename, local_path,
            storage_reference, file_hash, duration, width, height,
            resolution, aspect_ratio, file_size, codec, audio_present,
            validation_status, validation_results, status, caption, hashtags,
            priority, generation_source, campaign, batch, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            code,
            product_id,
            path.name,
            str(destination),
            str(destination),
            file_hash,
            results.get("duration"),
            results.get("width"),
            results.get("height"),
            results.get("resolution"),
            results.get("aspect_ratio"),
            results.get("file_size"),
            results.get("codec"),
            1 if results.get("audio_present") else 0,
            probe.validation_status.value,
            json.dumps(results, ensure_ascii=False),
            status.value,
            caption,
            hashtags,
            priority,
            generation_source,
            campaign,
            batch,
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    content_id = int(cursor.lastrowid)
    LOGGER.info(
        "content_imported",
        extra={"content_id": content_id, "content_code": code, "validation_status": probe.validation_status.value},
    )
    return {"content_id": content_id, "content_code": code, "duplicate": False}


def enqueue_content(conn, content_id: int, priority: int | None = None, scheduled_date: str | None = None) -> None:
    row = conn.execute("SELECT status, priority FROM content_items WHERE id = ?", (content_id,)).fetchone()
    if not row:
        raise ValueError("Conteudo nao encontrado")
    if row["status"] != ContentStatus.QUEUED.value:
        assert_transition(row["status"], ContentStatus.QUEUED.value)
    queue_row = conn.execute("SELECT id FROM publishing_queue WHERE content_id = ?", (content_id,)).fetchone()
    timestamp = now_iso()
    queue_priority = int(priority if priority is not None else row["priority"])
    if queue_row:
        conn.execute(
            """
            UPDATE publishing_queue
            SET priority = ?, scheduled_date = ?, status = 'QUEUED', updated_at = ?
            WHERE content_id = ?
            """,
            (queue_priority, scheduled_date, timestamp, content_id),
        )
    else:
        next_position = int(
            conn.execute("SELECT COALESCE(MAX(queue_position), 0) + 1 AS next_pos FROM publishing_queue").fetchone()[
                "next_pos"
            ]
        )
        conn.execute(
            """
            INSERT INTO publishing_queue (
                content_id, queue_position, priority, scheduled_date, status, queued_at, updated_at
            )
            VALUES (?, ?, ?, ?, 'QUEUED', ?, ?)
            """,
            (content_id, next_position, queue_priority, scheduled_date, timestamp, timestamp),
        )
    set_content_status(conn, content_id, ContentStatus.QUEUED.value, queued_at=timestamp, priority=queue_priority)
    conn.commit()


def remove_from_queue(conn, content_id: int) -> None:
    row = conn.execute("SELECT status FROM content_items WHERE id = ?", (content_id,)).fetchone()
    if not row:
        raise ValueError("Conteudo nao encontrado")
    conn.execute("DELETE FROM publishing_queue WHERE content_id = ?", (content_id,))
    if row["status"] == ContentStatus.QUEUED.value:
        set_content_status(conn, content_id, ContentStatus.READY.value)
    compact_queue(conn)
    conn.commit()


def compact_queue(conn) -> None:
    queued = conn.execute(
        "SELECT id FROM publishing_queue WHERE status = 'QUEUED' ORDER BY queue_position, id"
    ).fetchall()
    for index, row in enumerate(queued, start=1):
        conn.execute("UPDATE publishing_queue SET queue_position = ? WHERE id = ?", (index, row["id"]))


def next_publishable_content(conn) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT c.*, p.name AS product_name, p.tiktok_product_id, p.tiktok_product_url,
               q.queue_position, q.scheduled_date
        FROM publishing_queue q
        JOIN content_items c ON c.id = q.content_id
        JOIN products p ON p.id = c.product_id
        WHERE q.status = 'QUEUED'
          AND c.status = 'QUEUED'
          AND (q.scheduled_date IS NULL OR q.scheduled_date <= date('now'))
        ORDER BY q.priority DESC, q.queue_position ASC, q.id ASC
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def mark_sent_to_phone(conn, content_id: int) -> None:
    timestamp = now_iso()
    set_content_status(conn, content_id, ContentStatus.SENT_TO_PHONE.value, sent_to_phone_at=timestamp)
    conn.execute(
        "UPDATE publishing_queue SET status = 'SENT_TO_PHONE', updated_at = ? WHERE content_id = ?",
        (timestamp, content_id),
    )
    conn.commit()


def mark_published(
    conn,
    content_id: int,
    tiktok_url: str | None = None,
    tiktok_video_id: str | None = None,
    published_at: str | None = None,
    notes: str | None = None,
) -> None:
    timestamp = now_iso()
    published_timestamp = published_at or timestamp
    set_content_status(
        conn,
        content_id,
        ContentStatus.PUBLISHED.value,
        published_at=published_timestamp,
        tiktok_url=tiktok_url,
        tiktok_video_id=tiktok_video_id or extract_tiktok_video_id(tiktok_url),
        notes=notes,
    )
    conn.execute(
        "UPDATE publishing_queue SET status = 'PUBLISHED', updated_at = ? WHERE content_id = ?",
        (timestamp, content_id),
    )
    conn.commit()


def add_performance_snapshot(conn, content_id: int, metrics: dict[str, Any], source: str = "manual") -> int:
    timestamp = metrics.get("captured_at") or now_iso()
    provider = metrics.get("provider") or source or "manual"
    row_hash = stable_hash([content_id, timestamp, source, metrics])
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO content_performance (
            content_id, captured_at, views, likes, comments, shares, saves,
            orders, units_sold, gmv, commission, ctr, conversion_rate,
            source, source_row_hash, created_at, provider, source_timestamp,
            synced_at, estimated_commission, actual_commission, refunds,
            attribution_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            content_id,
            timestamp,
            _float(metrics.get("views")),
            _float(metrics.get("likes")),
            _float(metrics.get("comments")),
            _float(metrics.get("shares")),
            _float(metrics.get("saves")),
            _float(metrics.get("orders")),
            _float(metrics.get("units_sold")),
            _float(metrics.get("gmv")),
            _float(metrics.get("commission")),
            _float(metrics.get("ctr")),
            _float(metrics.get("conversion_rate")),
            source,
            row_hash,
            now_iso(),
            provider,
            metrics.get("source_timestamp") or timestamp,
            metrics.get("synced_at") or now_iso(),
            _float(metrics.get("estimated_commission")),
            _float(metrics.get("actual_commission")),
            _float(metrics.get("refunds")),
            metrics.get("attribution_status") or "UNAVAILABLE",
        ),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def extract_tiktok_video_id(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"/video/(\d+)", url)
    return match.group(1) if match else None


def _float(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
