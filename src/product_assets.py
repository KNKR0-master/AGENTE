from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Any

from src.store import now_iso


CAPTION_STATUSES = {"DRAFT", "READY", "USED", "ARCHIVED"}


def sync_folder_prompts(conn: sqlite3.Connection, folder_id: str, *, commit: bool = True) -> int:
    folder = conn.execute(
        "SELECT * FROM drive_folders WHERE drive_folder_id = ?", (folder_id,)
    ).fetchone()
    if not folder:
        return 0
    timestamp = now_iso()
    if (
        folder["operational_status"] != "ACTIVE"
        or folder["mapping_status"] != "MAPPED_TO_PRODUCT"
        or not folder["product_id"]
    ):
        conn.execute(
            "UPDATE product_prompts SET status = 'UNMAPPED', updated_at = ? WHERE drive_folder_id = ? AND status <> 'SOURCE_MISSING'",
            (timestamp, folder_id),
        )
        if commit:
            conn.commit()
        return 0

    sources = conn.execute(
        """
        SELECT drive_file_id, name, text_content, text_content_hash, missing_since
        FROM drive_files
        WHERE drive_folder_id = ? AND file_kind = 'PROMPT_TEXT'
        """,
        (folder_id,),
    ).fetchall()
    seen: set[str] = set()
    synced = 0
    for source in sources:
        file_id = str(source["drive_file_id"])
        seen.add(file_id)
        if source["missing_since"] or not source["text_content"]:
            conn.execute(
                """
                UPDATE product_prompts SET source_missing = 1, status = 'SOURCE_MISSING',
                    updated_at = ?, last_synced_at = ? WHERE source_file_id = ?
                """,
                (timestamp, timestamp, file_id),
            )
            continue
        content_hash = source["text_content_hash"] or hashlib.sha256(
            str(source["text_content"]).encode("utf-8")
        ).hexdigest()
        title = Path(str(source["name"])).stem.strip() or str(source["name"])
        conn.execute(
            """
            INSERT INTO product_prompts (
                product_id, drive_folder_id, source_file_id, source_filename,
                title, prompt_text, content_hash, status, source_missing,
                created_at, updated_at, last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'ACTIVE', 0, ?, ?, ?)
            ON CONFLICT(source_file_id) DO UPDATE SET
                product_id = excluded.product_id,
                drive_folder_id = excluded.drive_folder_id,
                source_filename = excluded.source_filename,
                title = excluded.title,
                prompt_text = excluded.prompt_text,
                content_hash = excluded.content_hash,
                status = 'ACTIVE', source_missing = 0,
                updated_at = excluded.updated_at,
                last_synced_at = excluded.last_synced_at
            """,
            (
                int(folder["product_id"]),
                folder_id,
                file_id,
                source["name"],
                title,
                source["text_content"],
                content_hash,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        synced += 1

    if seen:
        placeholders = ",".join("?" for _ in seen)
        conn.execute(
            f"""
            UPDATE product_prompts SET source_missing = 1, status = 'SOURCE_MISSING',
                updated_at = ?, last_synced_at = ?
            WHERE drive_folder_id = ? AND source_file_id NOT IN ({placeholders})
            """,
            (timestamp, timestamp, folder_id, *sorted(seen)),
        )
    else:
        conn.execute(
            """
            UPDATE product_prompts SET source_missing = 1, status = 'SOURCE_MISSING',
                updated_at = ?, last_synced_at = ? WHERE drive_folder_id = ?
            """,
            (timestamp, timestamp, folder_id),
        )
    if commit:
        conn.commit()
    return synced


def list_product_prompts(
    conn: sqlite3.Connection, product_id: int, query: str | None = None
) -> list[dict[str, Any]]:
    params: list[Any] = [product_id]
    where = "p.product_id = ?"
    if query:
        needle = f"%{query.strip()}%"
        where += " AND (p.title LIKE ? OR p.source_filename LIKE ? OR p.prompt_text LIKE ?)"
        params.extend([needle, needle, needle])
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT p.*, f.relative_path AS folder_path
            FROM product_prompts p
            LEFT JOIN drive_folders f ON f.drive_folder_id = p.drive_folder_id
            WHERE {where}
            ORDER BY CASE p.status WHEN 'ACTIVE' THEN 0 ELSE 1 END,
                     p.source_filename, p.id
            """,
            tuple(params),
        ).fetchall()
    ]


def list_product_captions(
    conn: sqlite3.Connection, product_id: int, include_archived: bool = False
) -> list[dict[str, Any]]:
    archived = "" if include_archived else "AND status <> 'ARCHIVED'"
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT * FROM product_captions
            WHERE product_id = ? {archived}
            ORDER BY CASE status WHEN 'READY' THEN 0 WHEN 'DRAFT' THEN 1 WHEN 'USED' THEN 2 ELSE 3 END,
                     updated_at DESC, id DESC
            """,
            (product_id,),
        ).fetchall()
    ]


def save_product_caption(
    conn: sqlite3.Connection,
    product_id: int,
    *,
    title: str,
    caption_text: str,
    hashtags: str | None = None,
    status: str = "DRAFT",
    caption_id: int | None = None,
) -> dict[str, Any]:
    if not conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
        raise ValueError("Produto nao encontrado")
    title = title.strip()
    caption_text = caption_text.strip()
    status = status.upper()
    if not title or not caption_text:
        raise ValueError("Titulo e legenda sao obrigatorios")
    if status not in CAPTION_STATUSES:
        raise ValueError("Status de legenda invalido")
    timestamp = now_iso()
    if caption_id is None:
        cursor = conn.execute(
            """
            INSERT INTO product_captions (
                product_id, title, caption_text, hashtags, status,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (product_id, title, caption_text, (hashtags or "").strip(), status, timestamp, timestamp),
        )
        caption_id = int(cursor.lastrowid)
    else:
        existing = conn.execute(
            "SELECT product_id FROM product_captions WHERE id = ?", (caption_id,)
        ).fetchone()
        if not existing or int(existing["product_id"]) != product_id:
            raise ValueError("Legenda nao encontrada para este produto")
        conn.execute(
            """
            UPDATE product_captions SET title = ?, caption_text = ?, hashtags = ?,
                status = ?, updated_at = ? WHERE id = ?
            """,
            (title, caption_text, (hashtags or "").strip(), status, timestamp, caption_id),
        )
    conn.commit()
    return dict(conn.execute("SELECT * FROM product_captions WHERE id = ?", (caption_id,)).fetchone())


def duplicate_product_caption(conn: sqlite3.Connection, caption_id: int) -> dict[str, Any]:
    source = conn.execute("SELECT * FROM product_captions WHERE id = ?", (caption_id,)).fetchone()
    if not source:
        raise ValueError("Legenda nao encontrada")
    return save_product_caption(
        conn,
        int(source["product_id"]),
        title=f"{source['title']} - copia",
        caption_text=source["caption_text"],
        hashtags=source["hashtags"],
        status="DRAFT",
    )


def archive_product_caption(conn: sqlite3.Connection, caption_id: int) -> dict[str, Any]:
    timestamp = now_iso()
    conn.execute(
        "UPDATE product_captions SET status = 'ARCHIVED', updated_at = ? WHERE id = ?",
        (timestamp, caption_id),
    )
    row = conn.execute("SELECT * FROM product_captions WHERE id = ?", (caption_id,)).fetchone()
    if not row:
        raise ValueError("Legenda nao encontrada")
    conn.commit()
    return dict(row)


def apply_caption_to_content(
    conn: sqlite3.Connection, product_id: int, caption_id: int
) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT * FROM product_captions
        WHERE id = ? AND product_id = ? AND status <> 'ARCHIVED'
        """,
        (caption_id, product_id),
    ).fetchone()
    if not row:
        raise ValueError("Legenda indisponivel para este produto")
    timestamp = now_iso()
    conn.execute(
        """
        UPDATE product_captions SET usage_count = usage_count + 1,
            last_used_at = ?, status = 'USED', updated_at = ? WHERE id = ?
        """,
        (timestamp, timestamp, caption_id),
    )
    conn.commit()
    return dict(row)
