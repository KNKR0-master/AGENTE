from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.product_catalog import sync_affiliate_product_catalog
from src.store import connect, init_db, now_iso, stable_hash
from src.tiktok_reports import (
    MISSING_REQUIRED_COLUMNS,
    PROVIDER_TIKTOK_REPORT,
    REPORT_AFFILIATE_ORDERS,
    ParsedTikTokReport,
    TikTokReportProvider,
    detect_report_file,
)


@dataclass(frozen=True)
class AffiliateReportConfig:
    import_dir: Path
    auto_import_enabled: bool
    interval_seconds: int
    min_age_seconds: int
    daily_lookback_days: int = 30

    @classmethod
    def from_env(cls, root: str | Path) -> "AffiliateReportConfig":
        project_root = Path(root).resolve()
        configured = Path(os.getenv("AFFILIATE_REPORT_IMPORT_DIR", "data/affiliate_reports/inbox"))
        import_dir = configured if configured.is_absolute() else project_root / configured
        return cls(
            import_dir=import_dir.resolve(),
            auto_import_enabled=_env_bool("AFFILIATE_REPORT_AUTO_IMPORT_ENABLED", True),
            interval_seconds=max(
                30, int(os.getenv("AFFILIATE_REPORT_IMPORT_INTERVAL_SECONDS", "300"))
            ),
            min_age_seconds=max(
                0, int(os.getenv("AFFILIATE_REPORT_MIN_AGE_SECONDS", "30"))
            ),
            daily_lookback_days=max(
                1, int(os.getenv("DAILY_IMPORT_LOOKBACK_DAYS", "30"))
            ),
        )


class AffiliateReportImporter:
    def __init__(
        self,
        config: AffiliateReportConfig,
        provider: TikTokReportProvider | None = None,
        clock=time.time,
    ) -> None:
        self.config = config
        self.provider = provider or TikTokReportProvider()
        self.clock = clock

    def scan(self, conn: sqlite3.Connection) -> dict[str, Any]:
        self.config.import_dir.mkdir(parents=True, exist_ok=True)
        result: dict[str, Any] = {
            "directory": str(self.config.import_dir),
            "files_found": 0,
            "imported": 0,
            "skipped": 0,
            "failed": 0,
            "rows_seen": 0,
            "orders_upserted": 0,
            "errors": [],
        }
        files = sorted(
            path
            for path in self.config.import_dir.rglob("*")
            if path.is_file() and path.suffix.lower() in {".csv", ".xlsx"}
        )
        result["files_found"] = len(files)
        for path in files:
            if self.clock() - path.stat().st_mtime < self.config.min_age_seconds:
                result["skipped"] += 1
                continue
            if _completed_file_hash(conn, _file_hash(path)):
                result["skipped"] += 1
                continue
            try:
                imported = self.import_file(conn, path, import_mode="DAILY_UPDATE")
                if imported["status"] == "SKIPPED":
                    result["skipped"] += 1
                    continue
                result["imported"] += 1
                result["rows_seen"] += int(imported.get("rows_total") or 0)
                result["orders_upserted"] += int(imported.get("orders_new") or 0) + int(
                    imported.get("orders_updated") or 0
                )
            except Exception as exc:
                result["failed"] += 1
                result["errors"].append({"file": path.name, "error": str(exc)[:500]})
        return result

    def preview_file(
        self,
        conn: sqlite3.Connection,
        path: str | Path,
        *,
        import_mode: str = "DAILY_UPDATE",
    ) -> dict[str, Any]:
        source = Path(path).resolve()
        if source.suffix.lower() not in {".csv", ".xlsx"}:
            raise ValueError("Relatorio deve ser CSV ou XLSX")
        mode = _validate_import_mode(import_mode)
        digest = _file_hash(source)
        detection = detect_report_file(source)
        started = now_iso()
        duplicate = conn.execute(
            """
            SELECT id FROM affiliate_import_batches
            WHERE file_hash = ? AND status IN ('COMPLETED', 'COMPLETED_WITH_ERRORS')
            ORDER BY id DESC LIMIT 1
            """,
            (digest,),
        ).fetchone()
        if detection.report_type != REPORT_AFFILIATE_ORDERS or detection.missing_required:
            message = "Campos obrigatorios ausentes: " + ", ".join(detection.missing_required)
            batch_id = self._create_batch(
                conn,
                source,
                digest,
                detection.report_type,
                detection.compatibility,
                mode,
                "FAILED",
                started,
                error_message=message,
            )
            raise ValueError(f"Batch {batch_id}: {message}")

        report = self.provider.parse_file(source)
        effective = _apply_import_mode(report, mode, self.config.daily_lookback_days)
        preview = effective.preview()
        status = "DUPLICATE" if duplicate else "PREVIEWED"
        batch_id = self._create_batch(
            conn,
            source,
            digest,
            report.report_type,
            report.compatibility,
            mode,
            status,
            started,
            preview=preview,
        )
        matching = _preview_matching(conn, effective)
        changes = _preview_order_changes(conn, effective)
        payload = {
            "batch_id": batch_id,
            "filename": source.name,
            "file_hash": digest,
            "duplicate": bool(duplicate),
            "previous_batch_id": int(duplicate["id"]) if duplicate else None,
            "can_reprocess": bool(duplicate),
            **preview,
            **matching,
            **changes,
        }
        return payload

    def process_batch(
        self,
        conn: sqlite3.Connection,
        batch_id: int,
        *,
        reprocess: bool = False,
    ) -> dict[str, Any]:
        batch = conn.execute(
            "SELECT * FROM affiliate_import_batches WHERE id = ?", (batch_id,)
        ).fetchone()
        if not batch:
            raise ValueError("Batch de importacao nao encontrado")
        if batch["status"] == "DUPLICATE" and not reprocess:
            return {
                "batch_id": batch_id,
                "status": "SKIPPED",
                "message": "Este arquivo ja foi importado.",
            }
        if batch["status"] not in {"PREVIEWED", "DUPLICATE", "FAILED"} and not reprocess:
            raise ValueError(f"Batch nao processavel no status {batch['status']}")
        source = Path(batch["file_path"])
        if not source.exists():
            raise ValueError("Arquivo do batch nao esta mais disponivel")
        if _file_hash(source) != batch["file_hash"]:
            raise ValueError("Arquivo alterado depois do preview")

        conn.execute(
            "UPDATE affiliate_import_batches SET status = 'PROCESSING', started_at = ?, updated_at = ? WHERE id = ?",
            (now_iso(), now_iso(), batch_id),
        )
        conn.commit()
        try:
            report = self.provider.parse_file(source)
            effective = _apply_import_mode(
                report, batch["import_mode"], self.config.daily_lookback_days
            )
            result = self.provider.ingest_report(conn, effective, batch_id)
            catalog = sync_affiliate_product_catalog(conn)
            for error in effective.errors:
                conn.execute(
                    """
                    INSERT INTO affiliate_import_errors (
                        batch_id, row_number, error_code, error_message,
                        raw_payload, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        batch_id,
                        error.row_number,
                        error.code,
                        error.message,
                        json.dumps(error.raw_payload, ensure_ascii=False),
                        now_iso(),
                    ),
                )
            matching = _current_matching_counts(conn, effective)
            status = "COMPLETED_WITH_ERRORS" if effective.errors else "COMPLETED"
            finished = now_iso()
            conn.execute(
                """
                UPDATE affiliate_import_batches SET
                    status = ?, rows_total = ?, rows_new = ?, rows_updated = ?,
                    rows_unchanged = ?, rows_failed = ?, orders_new = ?,
                    orders_updated = ?, products_total = ?, products_matched = ?,
                    products_unmatched = ?, skus_total = ?, contents_total = ?,
                    contents_new = ?, contents_matched = ?, contents_unmatched = ?,
                    period_start = ?, period_end = ?, finished_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    effective.rows_total,
                    result["rows_new"],
                    result["rows_updated"],
                    result["rows_unchanged"],
                    len(effective.errors),
                    result["orders_new"],
                    result["orders_updated"],
                    len(effective.product_ids),
                    matching["products_matched"],
                    matching["products_unmatched"],
                    len(effective.sku_ids),
                    len(effective.content_ids),
                    result["contents_new"],
                    matching["contents_matched"],
                    matching["contents_unmatched"],
                    effective.period_start,
                    effective.period_end,
                    finished,
                    finished,
                    batch_id,
                ),
            )
            conn.commit()
            return {
                "batch_id": batch_id,
                "status": status,
                "rows_total": effective.rows_total,
                "rows_failed": len(effective.errors),
                **result,
                **matching,
                **catalog,
            }
        except Exception as exc:
            conn.execute(
                """
                UPDATE affiliate_import_batches SET status = 'FAILED',
                    error_message = ?, finished_at = ?, updated_at = ? WHERE id = ?
                """,
                (str(exc)[:1000], now_iso(), now_iso(), batch_id),
            )
            conn.commit()
            raise

    def import_file(
        self,
        conn: sqlite3.Connection,
        path: str | Path,
        *,
        import_mode: str = "DAILY_UPDATE",
        reprocess: bool = False,
    ) -> dict[str, Any]:
        preview = self.preview_file(conn, path, import_mode=import_mode)
        result = self.process_batch(conn, int(preview["batch_id"]), reprocess=reprocess)
        return {**preview, **result}

    def _create_batch(
        self,
        conn: sqlite3.Connection,
        source: Path,
        digest: str,
        report_type: str,
        compatibility: str,
        import_mode: str,
        status: str,
        started: str,
        *,
        preview: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> int:
        preview = preview or {}
        cursor = conn.execute(
            """
            INSERT INTO affiliate_import_batches (
                report_type, compatibility, filename, file_path, file_hash,
                import_mode, period_start, period_end, status, rows_total,
                rows_failed, products_total, skus_total, contents_total,
                error_message, started_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_type,
                compatibility,
                source.name,
                str(source),
                digest,
                import_mode,
                preview.get("period_start"),
                preview.get("period_end"),
                status,
                int(preview.get("rows_total") or 0),
                int(preview.get("rows_failed") or 0),
                int(preview.get("products") or 0),
                int(preview.get("skus") or 0),
                int(preview.get("contents") or 0),
                error_message,
                started,
                started,
                started,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def report_import_status(
    conn: sqlite3.Connection, config: AffiliateReportConfig
) -> dict[str, Any]:
    last = conn.execute(
        """
        SELECT id, filename AS file_name, report_type, compatibility,
               import_mode, status, rows_total, rows_new, rows_updated,
               rows_unchanged, rows_failed, orders_new, orders_updated,
               products_matched, products_unmatched, contents_matched,
               contents_unmatched, period_start, period_end, error_message,
               finished_at AS imported_at, updated_at
        FROM affiliate_import_batches
        WHERE status IN ('COMPLETED', 'COMPLETED_WITH_ERRORS')
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()
    return {
        "auto_import_enabled": config.auto_import_enabled,
        "directory": str(config.import_dir),
        "interval_seconds": config.interval_seconds,
        "min_age_seconds": config.min_age_seconds,
        "daily_lookback_days": config.daily_lookback_days,
        "last_import": dict(last) if last else None,
    }


def start_report_import_worker(
    db_path: str | Path, config: AffiliateReportConfig
) -> threading.Thread | None:
    if not config.auto_import_enabled:
        return None

    def run() -> None:
        while True:
            conn = None
            try:
                conn = connect(db_path)
                init_db(conn)
                AffiliateReportImporter(config).scan(conn)
            except Exception:
                pass
            finally:
                if conn is not None:
                    conn.close()
            time.sleep(config.interval_seconds)

    worker = threading.Thread(target=run, name="affiliate-report-import", daemon=True)
    worker.start()
    return worker


def _apply_import_mode(
    report: ParsedTikTokReport,
    import_mode: str,
    lookback_days: int,
) -> ParsedTikTokReport:
    if import_mode == "FULL_HISTORY" or not report.period_end:
        return report
    period_end = datetime.fromisoformat(report.period_end)
    cutoff = period_end - timedelta(days=lookback_days)
    orders = [
        order
        for order in report.orders
        if order.order_created_at and datetime.fromisoformat(order.order_created_at) >= cutoff
    ]
    items = [item for order in orders for item in order.items]
    return ParsedTikTokReport(
        report_type=report.report_type,
        compatibility=report.compatibility,
        orders=orders,
        errors=report.errors,
        rows_total=sum(len(order.items) for order in orders),
        period_start=min((order.order_created_at for order in orders if order.order_created_at), default=None),
        period_end=max((order.order_created_at for order in orders if order.order_created_at), default=None),
        product_ids={item.external_product_id for item in items if item.external_product_id},
        sku_ids={item.external_sku_id for item in items if item.external_sku_id},
        content_ids={item.external_content_id for item in items if item.external_content_id},
        new_columns=report.new_columns,
    )


def _preview_matching(conn: sqlite3.Connection, report: ParsedTikTokReport) -> dict[str, int]:
    product_matches = 0
    for external_id in report.product_ids:
        matched = conn.execute(
            """
            SELECT 1 FROM products WHERE tiktok_product_id = ?
            UNION SELECT 1 FROM product_external_references
            WHERE provider = 'TIKTOK_SHOP' AND external_product_id = ? LIMIT 1
            """,
            (external_id, external_id),
        ).fetchone()
        product_matches += bool(matched)
    content_matches = 0
    new_contents = 0
    video_ids = {
        item.external_content_id
        for order in report.orders
        for item in order.items
        if item.normalized_content_type == "VIDEO" and item.external_content_id
    }
    for external_id in video_ids:
        matched = conn.execute(
            """
            SELECT 1 FROM content_items WHERE tiktok_video_id = ?
            UNION SELECT 1 FROM external_tiktok_contents
            WHERE external_content_id = ? AND external_content_type = 'VIDEO'
              AND content_id IS NOT NULL LIMIT 1
            """,
            (external_id, external_id),
        ).fetchone()
        known = conn.execute(
            "SELECT 1 FROM external_tiktok_contents WHERE external_content_id = ? AND external_content_type = 'VIDEO'",
            (external_id,),
        ).fetchone()
        content_matches += bool(matched)
        new_contents += not bool(known)
    return {
        "products_matched": product_matches,
        "products_unmatched": len(report.product_ids) - product_matches,
        "contents_new": new_contents,
        "contents_matched": content_matches,
        "contents_unmatched": len(video_ids) - content_matches,
    }


def _current_matching_counts(
    conn: sqlite3.Connection, report: ParsedTikTokReport
) -> dict[str, int]:
    product_ids = {
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT i.external_product_id FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            WHERE o.provider = ? AND i.product_id IS NOT NULL
            """,
            (PROVIDER_TIKTOK_REPORT,),
        )
    }
    content_ids = {
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT i.external_content_id FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            WHERE o.provider = ? AND i.normalized_content_type = 'VIDEO'
              AND i.content_id IS NOT NULL
            """,
            (PROVIDER_TIKTOK_REPORT,),
        )
    }
    video_ids = {
        item.external_content_id
        for order in report.orders
        for item in order.items
        if item.normalized_content_type == "VIDEO" and item.external_content_id
    }
    return {
        "products_matched": len(report.product_ids & product_ids),
        "products_unmatched": len(report.product_ids - product_ids),
        "contents_matched": len(video_ids & content_ids),
        "contents_unmatched": len(video_ids - content_ids),
    }


def _preview_order_changes(
    conn: sqlite3.Connection, report: ParsedTikTokReport
) -> dict[str, int]:
    counts = {"orders_new": 0, "orders_updated": 0, "orders_unchanged": 0}
    for order in report.orders:
        existing = conn.execute(
            "SELECT row_fingerprint FROM affiliate_orders WHERE provider = ? AND external_order_id = ?",
            (PROVIDER_TIKTOK_REPORT, order.external_order_id),
        ).fetchone()
        if not existing:
            counts["orders_new"] += 1
            continue
        fingerprint = stable_hash(
            [
                order.external_order_id,
                order.settlement_status,
                order.commission_settled_at,
                sorted(item.row_fingerprint or "" for item in order.items),
            ]
        )
        key = "orders_unchanged" if existing["row_fingerprint"] == fingerprint else "orders_updated"
        counts[key] += 1
    return counts


def _completed_file_hash(conn: sqlite3.Connection, digest: str) -> bool:
    return bool(
        conn.execute(
            """
            SELECT 1 FROM affiliate_import_batches
            WHERE file_hash = ? AND status IN ('COMPLETED', 'COMPLETED_WITH_ERRORS')
            LIMIT 1
            """,
            (digest,),
        ).fetchone()
    )


def _validate_import_mode(value: str) -> str:
    mode = str(value or "DAILY_UPDATE").upper()
    if mode not in {"DAILY_UPDATE", "FULL_HISTORY"}:
        raise ValueError("Modo de importacao invalido")
    return mode


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
