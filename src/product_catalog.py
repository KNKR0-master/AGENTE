from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from typing import Any

from src.store import now_iso


def sync_affiliate_product_catalog(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        """
        SELECT i.external_product_id, i.product_name, i.external_sku_id,
               o.shop_name, i.updated_at
        FROM affiliate_order_items i
        JOIN affiliate_orders o ON o.id = i.affiliate_order_id
        WHERE i.external_product_id IS NOT NULL AND i.external_product_id <> ''
        """
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[str(row["external_product_id"])].append(row)

    created = 0
    linked_products = 0
    linked_items = 0
    timestamp = now_iso()
    for external_id, items in grouped.items():
        names = Counter(str(row["product_name"]).strip() for row in items if row["product_name"])
        name = names.most_common(1)[0][0] if names else f"Produto TikTok {external_id}"
        shop_names = Counter(str(row["shop_name"]).strip() for row in items if row["shop_name"])
        shop_name = shop_names.most_common(1)[0][0] if shop_names else None
        existing = conn.execute(
            """
            SELECT p.id FROM products p
            LEFT JOIN product_external_references r ON r.product_id = p.id
            WHERE p.tiktok_product_id = ?
               OR (r.provider = 'tiktok_report' AND r.external_product_id = ?)
            ORDER BY p.id LIMIT 1
            """,
            (external_id, external_id),
        ).fetchone()
        if existing:
            product_id = int(existing["id"])
            conn.execute(
                """
                UPDATE products SET tiktok_product_id = COALESCE(tiktok_product_id, ?),
                    tiktok_product_name = COALESCE(tiktok_product_name, ?),
                    shop_name = COALESCE(shop_name, ?), updated_at = ? WHERE id = ?
                """,
                (external_id, name, shop_name, timestamp, product_id),
            )
        else:
            cursor = conn.execute(
                """
                INSERT INTO products (
                    product_key, name, tiktok_product_name, tiktok_product_id,
                    shop_name, status, decision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'monitoring', 'monitorar', ?, ?)
                """,
                (f"tiktok:{external_id}", name, name, external_id, shop_name, timestamp, timestamp),
            )
            product_id = int(cursor.lastrowid)
            created += 1

        conn.execute(
            """
            INSERT INTO product_external_references (
                product_id, provider, external_product_id, external_sku_id,
                external_product_name, shop_name, match_method, created_at, updated_at
            ) VALUES (?, 'tiktok_report', ?, '', ?, ?, 'EXTERNAL_PRODUCT_ID', ?, ?)
            ON CONFLICT(provider, external_product_id, external_sku_id) DO UPDATE SET
                product_id = excluded.product_id,
                external_product_name = excluded.external_product_name,
                shop_name = COALESCE(excluded.shop_name, product_external_references.shop_name),
                updated_at = excluded.updated_at
            """,
            (product_id, external_id, name, shop_name, timestamp, timestamp),
        )
        for alias in names:
            normalized = normalize_product_text(alias)
            if normalized:
                conn.execute(
                    """
                    INSERT INTO product_aliases (
                        product_id, alias, normalized_alias, source, created_at, updated_at
                    ) VALUES (?, ?, ?, 'TIKTOK_REPORT', ?, ?)
                    ON CONFLICT(product_id, normalized_alias) DO UPDATE SET
                        alias = excluded.alias, updated_at = excluded.updated_at
                    """,
                    (product_id, alias, normalized, timestamp, timestamp),
                )
        matched = conn.execute(
            """
            UPDATE affiliate_order_items SET product_id = ?,
                product_match_method = 'EXTERNAL_PRODUCT_ID', updated_at = ?
            WHERE external_product_id = ? AND (product_id IS NULL OR product_id <> ?)
            """,
            (product_id, timestamp, external_id, product_id),
        ).rowcount
        conn.execute(
            """
            UPDATE external_tiktok_contents SET product_id = ?,
                status = CASE WHEN status = 'UNMATCHED' THEN 'PRODUCT_MATCHED' ELSE status END,
                updated_at = ? WHERE external_product_id = ?
            """,
            (product_id, timestamp, external_id),
        )
        linked_items += matched
        linked_products += 1
    conn.commit()
    return {
        "catalog_products": len(grouped),
        "products_created": created,
        "products_linked": linked_products,
        "items_linked": linked_items,
    }


def product_catalog_options(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id, p.name, p.tiktok_product_name, p.tiktok_product_id,
               p.tiktok_sku_id, p.sku_internal_code, p.shop_name,
               GROUP_CONCAT(DISTINCT a.alias) AS aliases,
               COALESCE(sales.gmv_minor, 0) AS gmv_minor,
               COALESCE(sales.items_sold, 0) AS items_sold
        FROM products p
        LEFT JOIN product_aliases a ON a.product_id = p.id
        LEFT JOIN (
            SELECT product_id,
                   SUM(COALESCE(gmv_minor, 0)) AS gmv_minor,
                   SUM(COALESCE(quantity, 0) - COALESCE(refunded_quantity, 0)) AS items_sold
            FROM affiliate_order_items
            WHERE product_id IS NOT NULL
            GROUP BY product_id
        ) sales ON sales.product_id = p.id
        GROUP BY p.id
        ORDER BY gmv_minor DESC, p.name
        """
    ).fetchall()
    return [
        {
            **dict(row),
            "aliases": [value for value in str(row["aliases"] or "").split(",") if value],
        }
        for row in rows
    ]


def normalize_product_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(char for char in text if not unicodedata.combining(char)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())
