from __future__ import annotations

import json
import sqlite3
import cgi
import ctypes
import os
import re
import secrets
import signal
import shutil
import threading
import time
from ctypes import wintypes
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.content_pipeline import (
    ContentStatus,
    add_performance_snapshot,
    enqueue_content,
    import_content_from_path,
    mark_published,
    mark_sent_to_phone,
    next_publishable_content,
    remove_from_queue,
    set_content_status,
)
from src.affiliate import (
    CsvAffiliateProvider,
    ManualAffiliateProvider,
    affiliate_dashboard,
    product_affiliate_performance,
)
from src.affiliate_reports import (
    AffiliateReportConfig,
    AffiliateReportImporter,
    report_import_status,
    start_report_import_worker,
)
from src.affiliate_analytics import (
    affiliate_daily_operations,
    content_reconciliation,
    product_reconciliation,
)
from src.config import load_env_file
from src.google_services import GoogleDriveService, GoogleSheetsQueueService
from src.drive_inventory import (
    create_and_map_folder_product,
    drive_dashboard_payload,
    product_content_inventory,
    refresh_content_supply_alerts,
    scan_drive_inventory,
    set_folder_mapping,
)
from src.product_assets import (
    apply_caption_to_content,
    archive_product_caption,
    duplicate_product_caption,
    list_product_captions,
    list_product_prompts,
    save_product_caption,
)
from src.product_catalog import sync_affiliate_product_catalog
from src.store import connect, ensure_two_versions, init_db, now_iso, product_key
from src.tiktok_affiliate_sync import TikTokAffiliateSyncService
from src.tiktok_shop_api import (
    OAuthStateService,
    TikTokShopApiError,
    TikTokShopConfig,
    TikTokShopError,
    TikTokShopOAuthService,
    disconnect_creator_account,
    integration_status_payload,
)
from src.tiktok_reports import (
    classify_external_content,
    reconcile_external_content_id,
    reconcile_external_product,
)
from src.tiktok_analytics import (
    add_experiment_video,
    create_experiment,
    recompute_tiktok_analytics,
    save_creative_tags,
    tiktok_analytics_dashboard,
)
from src.publisher.http import PublisherAPI


ROOT = Path(__file__).resolve().parent
DASHBOARD = ROOT / "dashboard"
DB_PATH = ROOT / "data" / "agent.db"
UPLOADS = ROOT / "data" / "uploads"
PUBLISHER = PublisherAPI(ROOT, DB_PATH)
HTTP_SERVER = None


def rows(conn: sqlite3.Connection, query: str, params: tuple = ()) -> list[dict]:
    return [dict(row) for row in conn.execute(query, params).fetchall()]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(DASHBOARD), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            try:
                self._api_get(parsed.path, parse_qs(parsed.query))
            except (TikTokShopError, ValueError, KeyError) as exc:
                self._integration_error(exc)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/publisher/shutdown":
            if self.headers.get("Origin") and urlparse(self.headers["Origin"]).netloc != self.headers.get("Host"):
                self._integration_error(ValueError("Origem nao autorizada"))
                return
            self._json({"ok": True, "code": "SHUTDOWN_REQUESTED"}, no_store=True)
            if HTTP_SERVER is not None:
                threading.Thread(
                    target=HTTP_SERVER.shutdown,
                    name="publisher-server-api-stop",
                    daemon=True,
                ).start()
            return
        if parsed.path.startswith("/api/publisher/"):
            try:
                PUBLISHER.post(self, parsed.path)
            except (ValueError, KeyError, sqlite3.IntegrityError) as exc:
                self._integration_error(exc)
            return
        if not parsed.path.startswith("/api/"):
            self.send_error(404)
            return
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            payload = self._multipart_payload()
        else:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        try:
            self._api_post(parsed.path, payload)
        except (TikTokShopError, ValueError, KeyError) as exc:
            self._integration_error(exc)

    def _api_get(self, path: str, query: dict) -> None:
        if path.startswith('/api/publisher/accounts/') and path.endswith('/auth-diagnostics'):
            PUBLISHER.get_auth_diagnostics(self, path)
            return
        if path == "/api/publisher/runtime-info":
            PUBLISHER.runtime_info(self)
            return
        if path in {"/api/publisher", "/api/publisher/library", "/api/publisher/library/groups", "/api/publisher/library/group-items", "/api/publisher/products/workspace", "/api/publisher/folder-catalog", "/api/publisher/drive-sync/status"} or path.startswith("/api/publisher/library/thumbnail/") or re.fullmatch(r'/api/publisher/batches/\d+/preflight', path):
            PUBLISHER.get(self, query, path)
            return
        conn = connect(DB_PATH)
        init_db(conn)

        if path == "/api/integrations/tiktok-shop/status":
            payload = integration_status_payload(conn, TikTokShopConfig.from_env())
            report_config = AffiliateReportConfig.from_env(ROOT)
            payload["report_import"] = report_import_status(conn, report_config)
            self._json(payload)
            return

        if path == "/api/integrations/tiktok-shop/affiliate-summary":
            days = int(query.get("days", ["30"])[0])
            order_type = str(query.get("order_type", ["ALL"])[0]).upper()
            settlement_status = str(query.get("settlement_status", ["ALL"])[0]).upper()
            self._json(
                affiliate_daily_operations(
                    conn,
                    days=days,
                    order_type=order_type,
                    settlement_status=settlement_status,
                )
            )
            return

        if path == "/api/integrations/tiktok-shop/reconciliation":
            self._json(
                {
                    "contents": content_reconciliation(conn),
                    "products": product_reconciliation(conn),
                    "local_contents": rows(
                        conn,
                        """
                        SELECT c.id, c.content_code, c.tiktok_video_id, p.name AS product_name
                        FROM content_items c JOIN products p ON p.id = c.product_id
                        ORDER BY c.created_at DESC
                        """,
                    ),
                    "local_products": rows(
                        conn, "SELECT id, name FROM products ORDER BY name"
                    ),
                }
            )
            return

        if path == "/api/tiktok-analytics":
            self._json(tiktok_analytics_dashboard(conn))
            return

        if path == "/api/integrations/tiktok-shop/creator/authorize":
            config = TikTokShopConfig.from_env()
            config.validate_for_oauth()
            session_id, is_new = self._session_id()
            state = OAuthStateService(config.oauth_state_ttl_seconds).create(conn, session_id)
            location = TikTokShopOAuthService(config).authorization_url(state)
            self._redirect(location, set_session=session_id if is_new else None)
            return

        if path == "/api/integrations/tiktok-shop/creator/callback":
            config = TikTokShopConfig.from_env()
            config.validate_for_oauth()
            session_id, _ = self._session_id()
            state = query.get("state", [""])[0]
            OAuthStateService(config.oauth_state_ttl_seconds).validate(conn, state, session_id)
            if query.get("error"):
                self._redirect("/?tiktok=denied")
                return
            code = query.get("code", [""])[0]
            if not code:
                raise TikTokShopError("Codigo OAuth ausente")
            TikTokShopOAuthService(config).complete_authorization(conn, code)
            self._redirect("/?tiktok=connected")
            return

        if path == "/api/summary":
            products = rows(
                conn,
                """
                SELECT p.*, s.revenue_total, s.revenue_video, s.revenue_live,
                       s.sales_total, s.creator_count, s.video_count, s.avg_price,
                       s.gpm_video_export, s.cpa_ads, s.roas, s.collected_at, s.date_range
                FROM products p
                LEFT JOIN product_snapshots s ON s.id = (
                    SELECT id FROM product_snapshots
                    WHERE product_id = p.id
                    ORDER BY collected_at DESC, id DESC
                    LIMIT 1
                )
                ORDER BY COALESCE(s.revenue_total, 0) DESC
                """,
            )
            tasks = rows(conn, "SELECT * FROM production_tasks ORDER BY product_id, version_number")
            self._json({"products": products, "tasks": tasks})
            return

        if path == "/api/content/summary":
            self._json(content_summary(conn))
            return

        if path == "/api/drive/inventory":
            self._json(drive_dashboard_payload(conn))
            return

        if path == "/api/content":
            product_id = query.get("product_id", [None])[0]
            params: tuple = ()
            where = ""
            if product_id:
                where = "WHERE c.product_id = ?"
                params = (int(product_id),)
            contents = rows(
                conn,
                f"""
                SELECT c.*, p.name AS product_name, q.queue_position, q.scheduled_date,
                       COALESCE(general_perf.views, sales_perf.views, 0) AS views,
                       COALESCE(sales_perf.orders, general_perf.orders, 0) AS orders,
                       COALESCE(sales_perf.gmv_minor / 100.0, sales_perf.gmv, general_perf.gmv, 0) AS gmv,
                       COALESCE(sales_perf.actual_commission_minor / 100.0, sales_perf.commission, general_perf.commission, 0) AS commission,
                       COALESCE(sales_perf.estimated_commission_minor / 100.0, sales_perf.estimated_commission, general_perf.estimated_commission) AS estimated_commission,
                       COALESCE(sales_perf.actual_commission_minor / 100.0, sales_perf.actual_commission, general_perf.actual_commission) AS actual_commission,
                       COALESCE(sales_perf.refunds, general_perf.refunds, 0) AS refunds,
                       COALESCE(sales_perf.provider, general_perf.provider, 'manual') AS performance_provider,
                       COALESCE(sales_perf.attribution_status, general_perf.attribution_status, 'UNAVAILABLE') AS attribution_status,
                       COALESCE(sales_perf.source_timestamp, general_perf.source_timestamp) AS source_timestamp,
                       COALESCE(sales_perf.synced_at, general_perf.synced_at) AS synced_at
                FROM content_items c
                JOIN products p ON p.id = c.product_id
                LEFT JOIN publishing_queue q ON q.content_id = c.id
                LEFT JOIN content_performance general_perf ON general_perf.id = (
                    SELECT id FROM content_performance
                    WHERE content_id = c.id AND attribution_status = 'UNAVAILABLE'
                    ORDER BY captured_at DESC, id DESC LIMIT 1
                )
                LEFT JOIN content_performance sales_perf ON sales_perf.id = (
                    SELECT id FROM content_performance
                    WHERE content_id = c.id AND attribution_status <> 'UNAVAILABLE'
                    ORDER BY captured_at DESC, id DESC LIMIT 1
                )
                {where}
                ORDER BY c.created_at DESC, c.id DESC
                """,
                params,
            )
            self._json({"contents": [content_payload(item) for item in contents]})
            return

        if path == "/api/content/file":
            content_id = int(query.get("id", ["0"])[0])
            self._send_content_file(conn, content_id)
            return

        if path == "/api/publishing/next":
            item = next_publishable_content(conn)
            payload = next_payload(item) if item else None
            if payload and item:
                product_id = int(item["product_id"])
                payload["prompts"] = list_product_prompts(conn, product_id)
                payload["selected_caption"] = next(
                    (
                        caption
                        for caption in list_product_captions(conn, product_id, include_archived=True)
                        if int(caption["id"]) == int(item.get("caption_id") or 0)
                    ),
                    None,
                )
            self._json({"content": payload})
            return

        if path == "/api/product":
            product_id = int(query.get("id", ["0"])[0])
            product = rows(conn, "SELECT * FROM products WHERE id = ?", (product_id,))
            snapshots = rows(
                conn,
                "SELECT * FROM product_snapshots WHERE product_id = ? ORDER BY collected_at DESC",
                (product_id,),
            )
            videos = rows(
                conn,
                """
                SELECT * FROM video_metrics
                WHERE product_id = ?
                ORDER BY revenue DESC
                LIMIT 20
                """,
                (product_id,),
            )
            creators = rows(
                conn,
                """
                SELECT * FROM creator_metrics
                WHERE product_id = ?
                ORDER BY revenue DESC
                LIMIT 20
                """,
                (product_id,),
            )
            tasks = rows(
                conn,
                "SELECT * FROM production_tasks WHERE product_id = ? ORDER BY version_number",
                (product_id,),
            )
            contents = rows(
                conn,
                """
                SELECT c.*, q.queue_position,
                       COALESCE(general_perf.views, sales_perf.views, 0) AS views,
                       COALESCE(sales_perf.orders, general_perf.orders, 0) AS orders,
                       COALESCE(sales_perf.gmv_minor / 100.0, sales_perf.gmv, general_perf.gmv, 0) AS gmv,
                       COALESCE(sales_perf.actual_commission_minor / 100.0, sales_perf.commission, general_perf.commission, 0) AS commission,
                       COALESCE(sales_perf.estimated_commission_minor / 100.0, sales_perf.estimated_commission, general_perf.estimated_commission) AS estimated_commission,
                       COALESCE(sales_perf.actual_commission_minor / 100.0, sales_perf.actual_commission, general_perf.actual_commission) AS actual_commission,
                       COALESCE(sales_perf.refunds, general_perf.refunds, 0) AS refunds,
                       COALESCE(sales_perf.provider, general_perf.provider, 'manual') AS performance_provider,
                       COALESCE(sales_perf.attribution_status, general_perf.attribution_status, 'UNAVAILABLE') AS attribution_status,
                       COALESCE(sales_perf.source_timestamp, general_perf.source_timestamp) AS source_timestamp,
                       COALESCE(sales_perf.synced_at, general_perf.synced_at) AS synced_at
                FROM content_items c
                LEFT JOIN publishing_queue q ON q.content_id = c.id
                LEFT JOIN content_performance general_perf ON general_perf.id = (
                    SELECT id FROM content_performance
                    WHERE content_id = c.id AND attribution_status = 'UNAVAILABLE'
                    ORDER BY captured_at DESC, id DESC LIMIT 1
                )
                LEFT JOIN content_performance sales_perf ON sales_perf.id = (
                    SELECT id FROM content_performance
                    WHERE content_id = c.id AND attribution_status <> 'UNAVAILABLE'
                    ORDER BY captured_at DESC, id DESC LIMIT 1
                )
                WHERE c.product_id = ?
                ORDER BY c.created_at DESC, c.id DESC
                """,
                (product_id,),
            )
            self._json(
                {
                    "product": product[0] if product else None,
                    "snapshots": snapshots,
                    "videos": videos,
                    "creators": creators,
                    "tasks": tasks,
                    "contents": [content_payload(item) for item in contents],
                    "affiliate_performance": product_affiliate_performance(conn, product_id),
                    "prompts": list_product_prompts(
                        conn, product_id, query.get("prompt_query", [None])[0]
                    ),
                    "captions": list_product_captions(
                        conn,
                        product_id,
                        include_archived=query.get("include_archived", ["0"])[0] == "1",
                    ),
                    "drive_inventory": next(
                        (
                            item
                            for item in product_content_inventory(conn)
                            if int(item["product_id"]) == product_id
                        ),
                        None,
                    ),
                }
            )
            return

        self.send_error(404)

    def _api_post(self, path: str, payload: dict) -> None:
        conn = connect(DB_PATH)
        init_db(conn)
        timestamp = now_iso()

        if path == "/api/drive/scan":
            result = scan_drive_inventory(conn, force=bool(payload.get("force", True)))
            self._json({"ok": result.get("status") in {"COMPLETE", "PARTIAL"}, "scan": result})
            return

        if path == "/api/tiktok-analytics/recompute":
            result = recompute_tiktok_analytics(conn)
            self._json({"ok": True, **result})
            return

        if path == "/api/tiktok-analytics/creative-tags":
            save_creative_tags(conn, str(payload["video_id"]), payload)
            self._json({"ok": True})
            return

        if path == "/api/tiktok-analytics/experiments":
            experiment_id = create_experiment(conn, payload)
            self._json({"ok": True, "experiment_id": experiment_id})
            return

        if path == "/api/tiktok-analytics/experiment-videos":
            add_experiment_video(conn, int(payload["experiment_id"]), payload)
            self._json({"ok": True})
            return

        if path == "/api/drive/folder-mapping":
            folder_id = str(payload["folder_id"])
            if payload.get("create_product_name"):
                result = create_and_map_folder_product(
                    conn, folder_id, str(payload["create_product_name"])
                )
            else:
                product_value = payload.get("product_id")
                result = {
                    "folder": set_folder_mapping(
                        conn,
                        folder_id,
                        int(product_value) if product_value not in (None, "") else None,
                        mapping_status=payload.get("mapping_status"),
                        reason=payload.get("reason") or "Atualizado no painel Drive Product Mapping",
                    )
                }
            inventories = product_content_inventory(conn)
            refresh_content_supply_alerts(conn, inventories)
            folder_result = result.get("folder") or {}
            mapped_product_id = folder_result.get("product_id")
            result["inventory"] = next(
                (item for item in inventories if item["product_id"] == mapped_product_id),
                None,
            )
            self._json({"ok": True, **result})
            return

        if path == "/api/product/caption":
            action = str(payload.get("action") or "save").lower()
            if action == "duplicate":
                caption = duplicate_product_caption(conn, int(payload["caption_id"]))
            elif action == "archive":
                caption = archive_product_caption(conn, int(payload["caption_id"]))
            else:
                caption = save_product_caption(
                    conn,
                    int(payload["product_id"]),
                    caption_id=int(payload["caption_id"]) if payload.get("caption_id") else None,
                    title=str(payload["title"]),
                    caption_text=str(payload["caption_text"]),
                    hashtags=payload.get("hashtags"),
                    status=str(payload.get("status") or "DRAFT"),
                )
            self._json({"ok": True, "caption": caption})
            return

        if path == "/api/integrations/tiktok-shop/sync":
            config = TikTokShopConfig.from_env()
            account_id = self._account_id(conn, payload.get("account_id"))
            full = bool(payload.get("full"))
            if full and payload.get("confirm_full_sync") is not True:
                self._json({"ok": False, "error": "Confirme a sincronizacao completa"}, status=400)
                return
            result = TikTokAffiliateSyncService(config).sync(conn, account_id, full=full)
            self._json({"ok": True, **result})
            return

        if path == "/api/integrations/tiktok-shop/disconnect":
            account_id = self._account_id(conn, payload.get("account_id"))
            disconnect_creator_account(conn, account_id)
            self._json({"ok": True})
            return

        if path == "/api/integrations/tiktok-shop/csv/import":
            source_path = payload.get("uploaded_path") or payload.get("path")
            if not source_path:
                raise ValueError("Envie um arquivo CSV")
            provider = CsvAffiliateProvider()
            result = provider.ingest_orders(conn, provider.read_file(source_path))
            self._json({"ok": True, "provider": provider.provider_name, **result})
            return

        if path in {
            "/api/integrations/tiktok-shop/reports/upload",
            "/api/integrations/tiktok-shop/reports/preview",
        }:
            source_path = payload.get("uploaded_path") or payload.get("path")
            if not source_path:
                raise ValueError("Envie um relatorio CSV ou XLSX")
            result = AffiliateReportImporter(
                AffiliateReportConfig.from_env(ROOT)
            ).preview_file(
                conn,
                source_path,
                import_mode=str(payload.get("import_mode") or "DAILY_UPDATE"),
            )
            self._json({"ok": True, "provider": "tiktok_report", **result})
            return

        if path == "/api/integrations/tiktok-shop/reports/process":
            result = AffiliateReportImporter(
                AffiliateReportConfig.from_env(ROOT)
            ).process_batch(
                conn,
                int(payload["batch_id"]),
                reprocess=bool(payload.get("reprocess")),
            )
            self._json({"ok": True, "provider": "tiktok_report", **result})
            return

        if path == "/api/integrations/tiktok-shop/reports/import":
            result = AffiliateReportImporter(
                AffiliateReportConfig.from_env(ROOT)
            ).scan(conn)
            self._json({"ok": True, "provider": "csv", **result})
            return

        if path == "/api/integrations/tiktok-shop/manual/order":
            provider = ManualAffiliateProvider()
            result = provider.ingest_orders(conn, [payload])
            self._json({"ok": True, "provider": provider.provider_name, **result})
            return

        if path == "/api/integrations/tiktok-shop/product-match":
            product_id = int(payload["product_id"])
            external_product_id = str(payload.get("tiktok_product_id") or "").strip() or None
            external_sku_id = str(payload.get("tiktok_sku_id") or "").strip() or None
            if not external_product_id and not external_sku_id:
                raise ValueError("Informe tiktok_product_id ou tiktok_sku_id")
            self._ensure_external_product_ids_available(
                conn, product_id, external_product_id, external_sku_id
            )
            conn.execute(
                """
                UPDATE products SET tiktok_product_id = COALESCE(?, tiktok_product_id),
                    tiktok_sku_id = COALESCE(?, tiktok_sku_id), updated_at = ?
                WHERE id = ?
                """,
                (external_product_id, external_sku_id, timestamp, product_id),
            )
            matched = conn.execute(
                """
                UPDATE affiliate_order_items SET product_id = ?, updated_at = ?
                WHERE product_id IS NULL AND (
                    (? IS NOT NULL AND external_product_id = ?) OR
                    (? IS NOT NULL AND external_sku_id = ?)
                )
                """,
                (
                    product_id,
                    timestamp,
                    external_product_id,
                    external_product_id,
                    external_sku_id,
                    external_sku_id,
                ),
            ).rowcount
            conn.commit()
            self._json({"ok": True, "matched_items": matched})
            return

        if path == "/api/integrations/tiktok-shop/reconciliation/content":
            matched = reconcile_external_content_id(
                conn,
                str(payload["external_content_id"]),
                int(payload["content_id"]),
                manual=True,
            )
            self._json({"ok": True, "matched_items": matched})
            return

        if path == "/api/integrations/tiktok-shop/reconciliation/content-status":
            classify_external_content(
                conn,
                str(payload["external_content_id"]),
                str(payload["status"]).upper(),
                payload.get("notes"),
            )
            self._json({"ok": True})
            return

        if path == "/api/integrations/tiktok-shop/reconciliation/product":
            matched = reconcile_external_product(
                conn,
                int(payload["product_id"]),
                str(payload["external_product_id"]),
            )
            self._json({"ok": True, "matched_items": matched})
            return

        if path == "/api/product/create":
            name = str(payload["name"]).strip()
            if not name:
                self._json({"ok": False, "error": "Nome do produto e obrigatorio"}, status=400)
                return
            cursor = conn.execute(
                """
                INSERT INTO products (
                    product_key, name, sku_internal_code, tiktok_product_name,
                    tiktok_product_id, tiktok_product_url, category, status,
                    decision, default_caption, default_hashtags, notes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'monitoring', 'monitorar', ?, ?, ?, ?, ?)
                ON CONFLICT(product_key) DO UPDATE SET
                    sku_internal_code = COALESCE(excluded.sku_internal_code, products.sku_internal_code),
                    tiktok_product_name = COALESCE(excluded.tiktok_product_name, products.tiktok_product_name),
                    tiktok_product_id = COALESCE(excluded.tiktok_product_id, products.tiktok_product_id),
                    tiktok_product_url = COALESCE(excluded.tiktok_product_url, products.tiktok_product_url),
                    category = COALESCE(excluded.category, products.category),
                    default_caption = COALESCE(excluded.default_caption, products.default_caption),
                    default_hashtags = COALESCE(excluded.default_hashtags, products.default_hashtags),
                    notes = COALESCE(excluded.notes, products.notes),
                    updated_at = excluded.updated_at
                """,
                (
                    product_key(name),
                    name,
                    payload.get("sku_internal_code"),
                    payload.get("tiktok_product_name"),
                    payload.get("tiktok_product_id"),
                    payload.get("tiktok_product_url"),
                    payload.get("category"),
                    payload.get("default_caption"),
                    payload.get("default_hashtags"),
                    payload.get("notes"),
                    timestamp,
                    timestamp,
                ),
            )
            row = conn.execute("SELECT id FROM products WHERE product_key = ?", (product_key(name),)).fetchone()
            conn.commit()
            self._json({"ok": True, "product_id": int(row["id"]), "inserted_id": cursor.lastrowid})
            return

        if path == "/api/product/status":
            product_id = int(payload["product_id"])
            status = payload["status"]
            decision = payload.get("decision", status)
            conn.execute(
                "UPDATE products SET status = ?, decision = ?, updated_at = ? WHERE id = ?",
                (status, decision, timestamp, product_id),
            )
            if status == "validated":
                ensure_two_versions(conn, product_id)
            conn.commit()
            self._json({"ok": True})
            return

        if path in {"/api/content/import", "/api/content/upload"}:
            source_path = payload.get("path") or payload.get("uploaded_path")
            if not source_path:
                self._json({"ok": False, "error": "Envie path ou arquivo MP4"}, status=400)
                return
            product_id = int(payload["product_id"])
            caption_id = int(payload["caption_id"]) if payload.get("caption_id") else None
            selected_caption = None
            if caption_id:
                selected_caption = conn.execute(
                    """
                    SELECT * FROM product_captions
                    WHERE id = ? AND product_id = ? AND status <> 'ARCHIVED'
                    """,
                    (caption_id, product_id),
                ).fetchone()
                if not selected_caption:
                    raise ValueError("Legenda indisponivel para este produto")
            result = import_content_from_path(
                conn,
                product_id=product_id,
                source_path=source_path,
                caption=selected_caption["caption_text"] if selected_caption else payload.get("caption"),
                hashtags=selected_caption["hashtags"] if selected_caption else payload.get("hashtags"),
                priority=int(payload.get("priority") or 50),
                generation_source=payload.get("generation_source"),
                campaign=payload.get("campaign"),
                batch=payload.get("batch"),
                storage_root=ROOT / "data" / "content",
            )
            if caption_id:
                current = conn.execute(
                    "SELECT caption_id FROM content_items WHERE id = ?", (result["content_id"],)
                ).fetchone()
                if current and int(current["caption_id"] or 0) != caption_id:
                    apply_caption_to_content(conn, product_id, caption_id)
                    conn.execute(
                        """
                        UPDATE content_items SET caption_id = ?, caption = ?, hashtags = ?,
                            updated_at = ? WHERE id = ?
                        """,
                        (
                            caption_id,
                            selected_caption["caption_text"],
                            selected_caption["hashtags"],
                            timestamp,
                            result["content_id"],
                        ),
                    )
                    conn.commit()
            self._json({"ok": True, **result})
            return

        if path == "/api/content/status":
            set_content_status(conn, int(payload["content_id"]), payload["status"], notes=payload.get("notes"))
            conn.commit()
            self._json({"ok": True})
            return

        if path == "/api/content/queue":
            enqueue_content(
                conn,
                int(payload["content_id"]),
                priority=int(payload.get("priority") or 50),
                scheduled_date=payload.get("scheduled_date"),
            )
            GoogleSheetsQueueService().sync_content(conn, int(payload["content_id"]))
            self._json({"ok": True})
            return

        if path == "/api/content/unqueue":
            remove_from_queue(conn, int(payload["content_id"]))
            self._json({"ok": True})
            return

        if path == "/api/content/drive/upload":
            reference = GoogleDriveService().upload_content(conn, int(payload["content_id"]))
            conn.execute(
                """
                UPDATE content_items
                SET google_drive_file_id = COALESCE(?, google_drive_file_id),
                    google_drive_url = COALESCE(?, google_drive_url),
                    updated_at = ?
                WHERE id = ?
                """,
                (reference.file_id, reference.url, timestamp, int(payload["content_id"])),
            )
            conn.commit()
            self._json({"ok": True, "drive": reference.__dict__})
            return

        if path.startswith("/api/publishing/") and path.endswith("/sent-to-phone"):
            content_id = int(path.split("/")[3])
            mark_sent_to_phone(conn, content_id)
            self._json({"ok": True})
            return

        if path.startswith("/api/publishing/") and path.endswith("/published"):
            content_id = int(path.split("/")[3])
            mark_published(
                conn,
                content_id,
                tiktok_url=payload.get("tiktok_url"),
                tiktok_video_id=payload.get("tiktok_video_id"),
                published_at=payload.get("published_at"),
                notes=payload.get("notes"),
            )
            published = conn.execute(
                "SELECT tiktok_video_id FROM content_items WHERE id = ?", (content_id,)
            ).fetchone()
            if published and published["tiktok_video_id"]:
                reconcile_external_content_id(
                    conn,
                    str(published["tiktok_video_id"]),
                    content_id,
                    manual=False,
                )
            GoogleDriveService().move_content(conn, content_id, "Published")
            GoogleSheetsQueueService().sync_content(conn, content_id)
            self._json({"ok": True})
            return

        if path == "/api/content/performance":
            content_id = int(payload["content_id"])
            performance_id = add_performance_snapshot(conn, content_id, payload, source=payload.get("source", "manual"))
            self._json({"ok": True, "performance_id": performance_id})
            return

        if path == "/api/task":
            task_id = int(payload["task_id"])
            fields = {
                "status": payload.get("status"),
                "hook": payload.get("hook"),
                "format": payload.get("format"),
                "posted_at": payload.get("posted_at"),
                "scheduled_at": payload.get("scheduled_at"),
                "views": payload.get("views"),
                "clicks": payload.get("clicks"),
                "orders_count": payload.get("orders_count"),
                "gmv": payload.get("gmv"),
                "commission": payload.get("commission"),
                "notes": payload.get("notes"),
            }
            clean = {key: value for key, value in fields.items() if value is not None}
            if clean:
                assignments = ", ".join(f"{key} = ?" for key in clean)
                values = list(clean.values()) + [timestamp, task_id]
                conn.execute(
                    f"UPDATE production_tasks SET {assignments}, updated_at = ? WHERE id = ?",
                    values,
                )
                conn.commit()
            self._json({"ok": True})
            return

        self.send_error(404)

    def _multipart_payload(self) -> dict:
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": self.headers.get("Content-Type"),
            },
        )
        payload: dict = {}
        for key in form.keys():
            field = form[key]
            if getattr(field, "filename", None):
                UPLOADS.mkdir(parents=True, exist_ok=True)
                filename = Path(field.filename).name
                target = UPLOADS / f"{int(time.time())}_{filename}"
                with target.open("wb") as handle:
                    shutil.copyfileobj(field.file, handle)
                payload["uploaded_path"] = str(target)
            else:
                payload[key] = field.value
        return payload

    def _send_content_file(self, conn: sqlite3.Connection, content_id: int) -> None:
        row = conn.execute("SELECT local_path, original_filename FROM content_items WHERE id = ?", (content_id,)).fetchone()
        if not row:
            self.send_error(404)
            return
        path = Path(row["local_path"] or "")
        if not path.is_absolute():
            path = ROOT / path
        if not path.exists():
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Content-Disposition", f'inline; filename="{row["original_filename"]}"')
        self.end_headers()
        self.wfile.write(body)

    def _session_id(self) -> tuple[str, bool]:
        cookie = SimpleCookie(self.headers.get("Cookie") or "")
        morsel = cookie.get("tts_session")
        if morsel and morsel.value:
            return morsel.value, False
        return secrets.token_urlsafe(32), True

    def _redirect(self, location: str, set_session: str | None = None) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        if set_session:
            self.send_header(
                "Set-Cookie",
                f"tts_session={set_session}; Path=/; HttpOnly; SameSite=Lax; Max-Age=3600",
            )
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _account_id(self, conn: sqlite3.Connection, requested: object) -> int:
        if requested not in (None, ""):
            return int(requested)
        row = conn.execute(
            "SELECT id FROM tiktok_creator_accounts ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise TikTokShopError("Conta Creator nao conectada")
        return int(row["id"])

    def _ensure_external_product_ids_available(
        self,
        conn: sqlite3.Connection,
        product_id: int,
        external_product_id: str | None,
        external_sku_id: str | None,
    ) -> None:
        if not conn.execute("SELECT 1 FROM products WHERE id = ?", (product_id,)).fetchone():
            raise ValueError("Produto local nao encontrado")
        duplicate = conn.execute(
            """
            SELECT id FROM products WHERE id <> ? AND (
                (? IS NOT NULL AND tiktok_product_id = ?) OR
                (? IS NOT NULL AND tiktok_sku_id = ?)
            ) LIMIT 1
            """,
            (
                product_id,
                external_product_id,
                external_product_id,
                external_sku_id,
                external_sku_id,
            ),
        ).fetchone()
        if duplicate:
            raise ValueError("ID TikTok ja associado a outro produto local")

    def _integration_error(self, exc: Exception) -> None:
        status = 400
        if isinstance(exc, TikTokShopApiError):
            status = 502 if (exc.http_status or 0) >= 500 else 409
        self._json(
            {
                "ok": False,
                "error": str(exc),
                "integration_status": getattr(exc, "integration_status", "ERROR"),
                "code": getattr(exc, "code", None),
                "request_id": getattr(exc, "request_id", None),
            },
            status=status,
        )

    def _json(self, data: dict, status: int = 200, *, no_store: bool = False) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if no_store:
            self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)


def content_payload(item: dict) -> dict:
    payload = dict(item)
    payload["video_url"] = f"/api/content/file?id={item['id']}"
    payload["validation_results"] = _json_field(payload.get("validation_results"))
    return payload


def next_payload(item: dict | None) -> dict | None:
    if not item:
        return None
    return {
        "id": item["id"],
        "content_code": item["content_code"],
        "product": {
            "id": item["product_id"],
            "name": item["product_name"],
            "tiktok_product_id": item.get("tiktok_product_id"),
            "tiktok_product_url": item.get("tiktok_product_url"),
        },
        "caption": item.get("caption") or "",
        "hashtags": item.get("hashtags") or "",
        "drive_url": item.get("google_drive_url"),
        "video_url": f"/api/content/file?id={item['id']}",
        "status": item["status"],
        "queue_position": item.get("queue_position"),
        "scheduled_date": item.get("scheduled_date"),
    }


def content_summary(conn: sqlite3.Connection) -> dict:
    counts = rows(
        conn,
        """
        SELECT status, COUNT(*) AS total
        FROM content_items
        GROUP BY status
        """,
    )
    status_counts = {row["status"]: row["total"] for row in counts}
    today = rows(
        conn,
        """
        SELECT COUNT(*) AS total
        FROM content_items
        WHERE status = 'PUBLISHED'
          AND date(published_at, '-3 hours') = date('now', '-3 hours')
        """,
    )[0]["total"]
    last_7 = rows(
        conn,
        """
        SELECT COUNT(*) AS total
        FROM content_items
        WHERE status = 'PUBLISHED'
          AND date(published_at, '-3 hours') >= date('now', '-3 hours', '-7 day')
        """,
    )[0]["total"]
    totals = rows(
        conn,
        """
        SELECT COALESCE(SUM(COALESCE(general_perf.views, sales_perf.views, 0)), 0) AS views,
               COALESCE(SUM(COALESCE(sales_perf.orders, general_perf.orders, 0)), 0) AS orders,
               COALESCE(SUM(COALESCE(sales_perf.gmv_minor / 100.0, sales_perf.gmv, general_perf.gmv, 0)), 0) AS gmv,
               COALESCE(SUM(COALESCE(sales_perf.actual_commission_minor / 100.0, sales_perf.commission, general_perf.commission, 0)), 0) AS commission
        FROM content_items c
        LEFT JOIN content_performance general_perf ON general_perf.id = (
            SELECT id FROM content_performance
            WHERE content_id = c.id AND attribution_status = 'UNAVAILABLE'
            ORDER BY captured_at DESC, id DESC LIMIT 1
        )
        LEFT JOIN content_performance sales_perf ON sales_perf.id = (
            SELECT id FROM content_performance
            WHERE content_id = c.id AND attribution_status <> 'UNAVAILABLE'
            ORDER BY captured_at DESC, id DESC LIMIT 1
        )
        """,
    )[0]
    drive_available = rows(
        conn,
        """
        SELECT COUNT(*) AS total FROM drive_files df
        JOIN drive_folders f ON f.drive_folder_id = df.drive_folder_id
        WHERE df.classification = 'AVAILABLE' AND df.missing_since IS NULL
          AND df.file_kind = 'VIDEO' AND f.operational_status = 'ACTIVE'
        """,
    )[0]["total"]
    drive_scan = rows(conn, "SELECT status, finished_at FROM drive_scan_runs ORDER BY id DESC LIMIT 1")
    return {
        "ready": status_counts.get(ContentStatus.READY.value, 0),
        "drive_available": drive_available,
        "drive_scan_status": drive_scan[0]["status"] if drive_scan else "NEVER_SCANNED",
        "drive_scanned_at": drive_scan[0]["finished_at"] if drive_scan else None,
        "queued": status_counts.get(ContentStatus.QUEUED.value, 0),
        "published_today": today,
        "published_last_7_days": last_7,
        "needs_review": status_counts.get(ContentStatus.NEEDS_REVIEW.value, 0),
        "performance": dict(totals),
    }


def _json_field(value):
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {}


def install_server_shutdown_handlers(server):
    """Make console shutdown events leave the server through its normal finally."""
    previous = {}

    def request_shutdown(_signum, _frame):
        # serve_forever must be stopped from another thread when a signal
        # arrives on the thread currently running the loop.
        threading.Thread(target=server.shutdown, name="publisher-server-stop", daemon=True).start()

    for signal_name in ("SIGINT", "SIGBREAK"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is not None:
            previous[signal_value] = signal.getsignal(signal_value)
            signal.signal(signal_value, request_shutdown)
    return previous


def restore_server_shutdown_handlers(previous):
    for signal_value, handler in previous.items():
        signal.signal(signal_value, handler)


def install_windows_console_shutdown_handler(server):
    """Handle CTRL_CLOSE_EVENT before Windows tears down the console process."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.WinDLL("Kernel32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
    handled_events = {0, 1, 2, 5, 6}

    def handle(event_code):
        if event_code not in handled_events:
            return False
        threading.Thread(
            target=server.shutdown,
            name="publisher-server-console-stop",
            daemon=True,
        ).start()
        return True

    callback = callback_type(handle)
    if not kernel32.SetConsoleCtrlHandler(callback, True):
        # CREATE_NO_WINDOW is the normal launcher mode; the local shutdown
        # endpoint remains available when there is no console to hook.
        return None

    def uninstall():
        kernel32.SetConsoleCtrlHandler(callback, False)

    return uninstall


def main() -> None:
    load_env_file(ROOT / ".env")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect(DB_PATH) as conn:
        init_db(conn)
        sync_affiliate_product_catalog(conn)
    report_config = AffiliateReportConfig.from_env(ROOT)
    PUBLISHER.initialize()
    report_config.import_dir.mkdir(parents=True, exist_ok=True)
    start_report_import_worker(DB_PATH, report_config)
    server = ThreadingHTTPServer(("127.0.0.1", 8765), Handler)
    global HTTP_SERVER
    HTTP_SERVER = server
    previous_signal_handlers = install_server_shutdown_handlers(server)
    uninstall_console_handler = install_windows_console_shutdown_handler(server)
    print("Dashboard local: http://127.0.0.1:8765")
    try:
        PUBLISHER.drive_poller.start()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if not PUBLISHER.drive_poller.stop():
            print("Publisher Drive poller did not stop within grace period", flush=True)
        try:
            PUBLISHER.worker.shutdown()
        except Exception as exc:
            print(f"Publisher worker shutdown warning: {type(exc).__name__}: {str(exc)[:500]}", flush=True)
        finally:
            # Covers a worker thread that was interrupted while waiting on a
            # browser operation; BrowserRuntimeManager remains idempotent.
            try:
                PUBLISHER.worker.release_owned_profiles()
            except Exception as exc:
                print(f"Publisher runtime shutdown warning: {type(exc).__name__}: {str(exc)[:500]}", flush=True)
            finally:
                server.server_close()
                if uninstall_console_handler:
                    uninstall_console_handler()
                restore_server_shutdown_handlers(previous_signal_handlers)
                HTTP_SERVER = None


if __name__ == "__main__":
    main()
