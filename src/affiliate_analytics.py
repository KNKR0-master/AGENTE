from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any

from src.affiliate import PROVIDER_TIKTOK_REPORT
from src.drive_inventory import product_content_inventory
from src.tiktok_reports import WAITING_FOR_VIDEO_ANALYTICS


def affiliate_daily_operations(
    conn: sqlite3.Connection,
    *,
    days: int = 30,
    order_type: str = "ALL",
    settlement_status: str = "ALL",
    as_of: str | None = None,
) -> dict[str, Any]:
    days = max(1, min(int(days), 3650))
    filters, params = _filters(order_type, settlement_status)
    totals = _window_metrics(conn, f"-{days - 1} days", "0 days", filters, params, as_of)
    windows = {
        "today": _window_metrics(conn, "0 days", "0 days", filters, params, as_of),
        "yesterday": _window_metrics(conn, "-1 days", "-1 days", filters, params, as_of),
        "days_7": _window_metrics(conn, "-6 days", "0 days", filters, params, as_of),
        "previous_7": _window_metrics(conn, "-13 days", "-7 days", filters, params, as_of),
        "days_30": _window_metrics(conn, "-29 days", "0 days", filters, params, as_of),
        "previous_30": _window_metrics(conn, "-59 days", "-30 days", filters, params, as_of),
    }
    comparisons = {
        "today_vs_yesterday": _compare(windows["today"], windows["yesterday"]),
        "days_7_vs_previous": _compare(windows["days_7"], windows["previous_7"]),
        "days_30_vs_previous": _compare(windows["days_30"], windows["previous_30"]),
    }
    window = f"-{days - 1} days"
    return {
        "period_days": days,
        "filters": {
            "order_type": order_type,
            "settlement_status": settlement_status,
        },
        "totals": totals,
        "windows": windows,
        "comparisons": comparisons,
        "content_types": _content_type_breakdown(conn, window, filters, params, as_of),
        "top_products": _top_products(conn, window, filters, params, as_of),
        "top_contents": _top_contents(conn, window, filters, params, as_of),
        "top_shops": _top_shops(conn, window, filters, params, as_of),
        "content_curves": _content_curves(conn),
        "pipeline": _pipeline_summary(conn),
        "next_to_publish": _next_to_publish(conn),
        "production_opportunities": content_production_opportunities(conn),
        "video_analytics_status": WAITING_FOR_VIDEO_ANALYTICS,
        "efficiency_metrics": {
            "ctr": WAITING_FOR_VIDEO_ANALYTICS,
            "conversion_rate_by_view": WAITING_FOR_VIDEO_ANALYTICS,
            "gmv_per_1000_views": WAITING_FOR_VIDEO_ANALYTICS,
            "orders_per_1000_views": WAITING_FOR_VIDEO_ANALYTICS,
        },
    }


def content_reconciliation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT e.id, e.external_content_id, e.external_content_type,
                   e.status, e.attribution_type, e.attribution_method,
                   e.confidence, e.content_id, c.content_code,
                   COALESCE(p.name, e.external_product_name, e.external_product_id) AS product_name,
                   COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.items_sold), 0) AS items_sold,
                   COALESCE(SUM(i.items_refunded), 0) AS items_refunded,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
                   COALESCE(SUM(i.estimated_commission_minor), 0) AS estimated_commission_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS actual_commission_minor,
                   COALESCE(SUM(i.final_received_minor), 0) AS final_received_minor,
                   MIN(o.order_created_at) AS first_sale_at,
                   MAX(o.order_created_at) AS last_sale_at
            FROM external_tiktok_contents e
            LEFT JOIN affiliate_order_items i
              ON i.external_content_id = e.external_content_id
             AND i.normalized_content_type = e.external_content_type
            LEFT JOIN affiliate_orders o
              ON o.id = i.affiliate_order_id AND o.provider = ?
            LEFT JOIN content_items c ON c.id = e.content_id
            LEFT JOIN products p ON p.id = COALESCE(c.product_id, e.product_id)
            GROUP BY e.id
            ORDER BY CASE e.status WHEN 'UNMATCHED' THEN 0 WHEN 'MATCHED' THEN 1 ELSE 2 END,
                     gmv_minor DESC, e.external_content_id
            """,
            (PROVIDER_TIKTOK_REPORT,),
        ).fetchall()
    ]


def product_reconciliation(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT i.external_product_id,
                   MAX(i.product_name) AS external_product_name,
                   MAX(o.shop_name) AS shop_name,
                   COUNT(DISTINCT i.external_sku_id) AS skus,
                   COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.items_sold), 0) AS items_sold,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS actual_commission_minor
            FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            WHERE o.provider = ? AND i.product_id IS NULL
            GROUP BY i.external_product_id
            ORDER BY gmv_minor DESC
            """,
            (PROVIDER_TIKTOK_REPORT,),
        ).fetchall()
    ]


def content_production_opportunities(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    opportunities = product_content_inventory(conn)
    for item in opportunities:
        current = int(item["gmv_7d_minor"] or 0)
        previous = int(item["previous_gmv_7d_minor"] or 0)
        item["growth_7d"] = None if previous == 0 else (current - previous) / previous
        if previous == 0 and current > 0:
            item["growth_7d"] = 1.0
        item["id"] = item["product_id"]
        item["name"] = item["product_name"]
        item["selling_contents"] = item["selling_content_count"]
        item["score"] = item["priority_score"]
        item["level"] = item["priority_level"]
        item["reasons"] = item["priority_reasons"]
    return opportunities


def _window_metrics(
    conn: sqlite3.Connection,
    start_modifier: str,
    end_modifier: str,
    filters: str,
    params: tuple[Any, ...],
    as_of: str | None,
) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT COUNT(DISTINCT o.id) AS orders,
               COALESCE(SUM(i.items_sold), 0) AS items_sold,
               COALESCE(SUM(i.items_refunded), 0) AS items_refunded,
               COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
               COALESCE(SUM(i.estimated_commission_minor), 0) AS estimated_commission_minor,
               COALESCE(SUM(i.actual_commission_minor), 0) AS actual_commission_minor,
               COALESCE(SUM(i.final_received_minor), 0) AS final_received_minor,
               COUNT(DISTINCT CASE WHEN i.normalized_content_type = 'VIDEO'
                    THEN i.external_content_id END) AS selling_content_ids
        FROM affiliate_orders o
        JOIN affiliate_order_items i ON i.affiliate_order_id = o.id
        WHERE o.provider = ?
          AND date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', ?)
          AND date(o.order_created_at, '-3 hours') <= date(COALESCE(?, 'now'), '-3 hours', ?)
          {filters}
        """,
        (PROVIDER_TIKTOK_REPORT, as_of, start_modifier, as_of, end_modifier, *params),
    ).fetchone()
    result = dict(row)
    sold = int(result["items_sold"] or 0)
    result["refund_rate"] = (int(result["items_refunded"] or 0) / sold) if sold else 0
    return result


def _filters(order_type: str, settlement_status: str) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    if order_type != "ALL":
        clauses.append("o.normalized_order_type = ?")
        params.append(order_type)
    if settlement_status != "ALL":
        clauses.append("o.settlement_status = ?")
        params.append(settlement_status)
    return (" AND " + " AND ".join(clauses) if clauses else "", tuple(params))


def _compare(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in ("orders", "items_sold", "gmv_minor", "estimated_commission_minor", "actual_commission_minor", "final_received_minor"):
        current_value = int(current.get(key) or 0)
        previous_value = int(previous.get(key) or 0)
        result[key] = {
            "current": current_value,
            "previous": previous_value,
            "change": current_value - previous_value,
            "change_rate": None
            if previous_value == 0
            else (current_value - previous_value) / previous_value,
        }
    return result


def _content_type_breakdown(
    conn: sqlite3.Connection,
    window: str,
    filters: str,
    params: tuple[Any, ...],
    as_of: str | None,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT i.normalized_content_type AS content_type,
                   COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.items_sold), 0) AS items_sold,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
                   COALESCE(SUM(i.estimated_commission_minor), 0) AS estimated_commission_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS actual_commission_minor,
                   COALESCE(SUM(i.final_received_minor), 0) AS final_received_minor
            FROM affiliate_orders o JOIN affiliate_order_items i ON i.affiliate_order_id = o.id
            WHERE o.provider = ? AND date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', ?)
              {filters}
            GROUP BY i.normalized_content_type ORDER BY gmv_minor DESC
            """,
            (PROVIDER_TIKTOK_REPORT, as_of, window, *params),
        ).fetchall()
    ]


def _top_products(
    conn: sqlite3.Connection,
    window: str,
    filters: str,
    params: tuple[Any, ...],
    as_of: str | None,
) -> list[dict[str, Any]]:
    result = [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT i.external_product_id, i.product_id,
                   COALESCE(p.name, i.product_name, i.external_product_id) AS product_name,
                   COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.items_sold), 0) AS items_sold,
                   COALESCE(SUM(i.items_refunded), 0) AS items_refunded,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
                   COALESCE(SUM(i.estimated_commission_minor), 0) AS estimated_commission_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS actual_commission_minor,
                   COALESCE(SUM(i.final_received_minor), 0) AS final_received_minor,
                   COUNT(DISTINCT CASE WHEN i.normalized_content_type = 'VIDEO'
                        THEN i.external_content_id END) AS selling_content_ids,
                   (SELECT COUNT(*) FROM drive_files d WHERE d.product_id = i.product_id
                        AND d.classification = 'ALREADY_PUBLISHED' AND d.missing_since IS NULL) AS published_contents,
                   (SELECT COUNT(*) FROM drive_files d WHERE d.product_id = i.product_id
                        AND d.classification = 'AVAILABLE' AND d.missing_since IS NULL) AS ready_contents,
                   (SELECT COUNT(*) FROM drive_files d WHERE d.product_id = i.product_id
                        AND d.classification = 'ALREADY_QUEUED' AND d.missing_since IS NULL) AS queued_contents,
                   (SELECT MAX(c.published_at) FROM content_items c
                        WHERE c.product_id = i.product_id AND c.status = 'PUBLISHED') AS last_published_at
            FROM affiliate_orders o JOIN affiliate_order_items i ON i.affiliate_order_id = o.id
            LEFT JOIN products p ON p.id = i.product_id
            WHERE o.provider = ? AND date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', ?)
              {filters}
            GROUP BY i.external_product_id, i.product_id, product_name
            ORDER BY gmv_minor DESC LIMIT 30
            """,
            (PROVIDER_TIKTOK_REPORT, as_of, window, *params),
        ).fetchall()
    ]
    windows = {
        str(row["external_product_id"]): dict(row)
        for row in conn.execute(
            f"""
            SELECT i.external_product_id,
                   COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime(COALESCE(?, 'now'), '-3 hours', '-24 hours') THEN i.gmv_minor ELSE 0 END), 0) AS gmv_24h_minor,
                   COALESCE(SUM(CASE WHEN date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', '-6 days') THEN i.gmv_minor ELSE 0 END), 0) AS gmv_7d_minor,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_30d_minor,
                   COUNT(DISTINCT CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime(COALESCE(?, 'now'), '-3 hours', '-24 hours') THEN o.id END) AS orders_24h,
                   COUNT(DISTINCT CASE WHEN date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', '-6 days') THEN o.id END) AS orders_7d,
                   COUNT(DISTINCT o.id) AS orders_30d,
                   COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime(COALESCE(?, 'now'), '-3 hours', '-24 hours') THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_24h_minor,
                   COALESCE(SUM(CASE WHEN date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', '-6 days') THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_7d_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS commission_30d_minor
            FROM affiliate_orders o JOIN affiliate_order_items i ON i.affiliate_order_id = o.id
            WHERE o.provider = ? AND date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', '-29 days')
              {filters}
            GROUP BY i.external_product_id
            """,
            (PROVIDER_TIKTOK_REPORT, as_of, as_of, as_of, as_of, as_of, as_of, as_of, *params),
        ).fetchall()
    }
    for item in result:
        item.update(windows.get(str(item["external_product_id"]), {}))
    return result


def _top_contents(
    conn: sqlite3.Connection,
    window: str,
    filters: str,
    params: tuple[Any, ...],
    as_of: str | None,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT i.external_content_id, i.normalized_content_type AS content_type,
                   i.content_id, c.content_code, c.tiktok_video_id, c.tiktok_url,
                   c.published_at, COALESCE(p.name, i.product_name) AS product_name,
                   COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.items_sold), 0) AS items_sold,
                   COALESCE(SUM(i.items_refunded), 0) AS items_refunded,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
                   COALESCE(SUM(i.estimated_commission_minor), 0) AS estimated_commission_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS actual_commission_minor,
                   COALESCE(SUM(i.final_received_minor), 0) AS final_received_minor,
                   e.status AS reconciliation_status
            FROM affiliate_orders o JOIN affiliate_order_items i ON i.affiliate_order_id = o.id
            LEFT JOIN content_items c ON c.id = i.content_id
            LEFT JOIN products p ON p.id = COALESCE(c.product_id, i.product_id)
            LEFT JOIN external_tiktok_contents e
              ON e.external_content_id = i.external_content_id
             AND e.external_content_type = i.normalized_content_type
            WHERE o.provider = ? AND date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', ?)
              {filters}
            GROUP BY i.external_content_id, i.normalized_content_type
            ORDER BY gmv_minor DESC LIMIT 50
            """,
            (PROVIDER_TIKTOK_REPORT, as_of, window, *params),
        ).fetchall()
    ]


def _top_shops(
    conn: sqlite3.Connection,
    window: str,
    filters: str,
    params: tuple[Any, ...],
    as_of: str | None,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            f"""
            SELECT o.shop_name, o.shop_code, COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
                   COALESCE(SUM(i.estimated_commission_minor), 0) AS estimated_commission_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS actual_commission_minor,
                   COALESCE(SUM(i.final_received_minor), 0) AS final_received_minor
            FROM affiliate_orders o JOIN affiliate_order_items i ON i.affiliate_order_id = o.id
            WHERE o.provider = ? AND date(o.order_created_at, '-3 hours') >= date(COALESCE(?, 'now'), '-3 hours', ?)
              {filters}
            GROUP BY o.shop_name, o.shop_code ORDER BY gmv_minor DESC LIMIT 30
            """,
            (PROVIDER_TIKTOK_REPORT, as_of, window, *params),
        ).fetchall()
    ]


def _content_curves(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.id AS content_id, c.content_code, c.tiktok_video_id,
                   c.published_at,
                   CAST(julianday('now') - julianday(c.published_at) AS INTEGER) AS age_days,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+1 day')
                        THEN i.gmv_minor ELSE 0 END), 0) AS gmv_d0_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+2 days')
                        THEN i.gmv_minor ELSE 0 END), 0) AS gmv_d1_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+4 days')
                        THEN i.gmv_minor ELSE 0 END), 0) AS gmv_d3_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+8 days')
                        THEN i.gmv_minor ELSE 0 END), 0) AS gmv_d7_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+15 days')
                        THEN i.gmv_minor ELSE 0 END), 0) AS gmv_d14_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+31 days')
                        THEN i.gmv_minor ELSE 0 END), 0) AS gmv_d30_minor,
                   COUNT(DISTINCT CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+1 day')
                        THEN o.id END) AS orders_d0,
                   COUNT(DISTINCT CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+2 days')
                        THEN o.id END) AS orders_d1,
                   COUNT(DISTINCT CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+4 days')
                        THEN o.id END) AS orders_d3,
                   COUNT(DISTINCT CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+8 days')
                        THEN o.id END) AS orders_d7,
                   COUNT(DISTINCT CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+15 days')
                        THEN o.id END) AS orders_d14,
                   COUNT(DISTINCT CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+31 days')
                        THEN o.id END) AS orders_d30,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+1 day')
                        THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_d0_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+2 days')
                        THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_d1_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+4 days')
                        THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_d3_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+8 days')
                        THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_d7_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+15 days')
                        THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_d14_minor,
                   COALESCE(SUM(CASE WHEN julianday(o.order_created_at) >= julianday(c.published_at)
                                      AND julianday(o.order_created_at) < julianday(c.published_at, '+31 days')
                        THEN i.actual_commission_minor ELSE 0 END), 0) AS commission_d30_minor
            FROM content_items c
            LEFT JOIN affiliate_order_items i ON i.content_id = c.id
            LEFT JOIN affiliate_orders o ON o.id = i.affiliate_order_id AND o.provider = ?
            WHERE c.published_at IS NOT NULL
            GROUP BY c.id ORDER BY gmv_d30_minor DESC
            """,
            (PROVIDER_TIKTOK_REPORT,),
        ).fetchall()
    ]


def _pipeline_summary(conn: sqlite3.Connection) -> dict[str, int]:
    counts = {
        str(row["status"]): int(row["total"])
        for row in conn.execute(
            "SELECT status, COUNT(*) AS total FROM content_items GROUP BY status"
        )
    }
    published_today = conn.execute(
        "SELECT COUNT(*) FROM content_items WHERE status = 'PUBLISHED' AND date(published_at, '-3 hours') = date('now', '-3 hours')"
    ).fetchone()[0]
    published_7d = conn.execute(
        "SELECT COUNT(*) FROM content_items WHERE status = 'PUBLISHED' AND date(published_at, '-3 hours') >= date('now', '-3 hours', '-6 days')"
    ).fetchone()[0]
    return {
        "ready": counts.get("READY", 0),
        "queued": counts.get("QUEUED", 0),
        "published_today": int(published_today),
        "published_7d": int(published_7d),
    }


def _next_to_publish(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.id, c.content_code, c.status, c.priority, c.created_at,
                   p.id AS product_id, p.name AS product_name,
                   COALESCE(SUM(CASE WHEN datetime(o.order_created_at, '-3 hours') >= datetime('now', '-3 hours', '-24 hours') THEN i.gmv_minor ELSE 0 END), 0) AS product_gmv_24h_minor,
                   COALESCE(SUM(CASE WHEN date(o.order_created_at, '-3 hours') >= date('now', '-3 hours', '-6 days') THEN i.gmv_minor ELSE 0 END), 0) AS product_gmv_7d_minor,
                   COUNT(DISTINCT CASE WHEN date(o.order_created_at, '-3 hours') >= date('now', '-3 hours', '-6 days') THEN o.id END) AS product_orders_7d,
                   COALESCE(SUM(CASE WHEN date(o.order_created_at, '-3 hours') >= date('now', '-3 hours', '-6 days') THEN i.actual_commission_minor ELSE 0 END), 0) AS product_commission_7d_minor
            FROM content_items c
            JOIN products p ON p.id = c.product_id
            LEFT JOIN affiliate_order_items i ON i.product_id = p.id
            LEFT JOIN affiliate_orders o ON o.id = i.affiliate_order_id AND o.provider = ?
            WHERE c.status IN ('READY', 'QUEUED')
            GROUP BY c.id
            ORDER BY CASE c.status WHEN 'QUEUED' THEN 0 ELSE 1 END,
                     c.priority DESC, product_gmv_7d_minor DESC, c.created_at
            LIMIT 20
            """,
            (PROVIDER_TIKTOK_REPORT,),
        ).fetchall()
    ]


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
