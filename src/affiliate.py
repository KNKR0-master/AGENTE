from __future__ import annotations

import csv
import json
import re
import sqlite3
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from src.store import now_iso, stable_hash


PROVIDER_TIKTOK = "tiktok_shop_creator"
PROVIDER_CSV = "csv"
PROVIDER_MANUAL = "manual"
PROVIDER_TIKTOK_REPORT = "tiktok_report"


@dataclass
class NormalizedAffiliateItem:
    item_key: str
    external_sku_id: str | None = None
    external_product_id: str | None = None
    external_content_id: str | None = None
    content_type: str | None = None
    product_name: str | None = None
    quantity: float = 0
    returned_quantity: float = 0
    refunded_quantity: float = 0
    currency: str | None = None
    unit_price: float | None = None
    gmv: float | None = None
    estimated_commission: float | None = None
    actual_commission: float | None = None
    refund_amount: float | None = None
    normalized_content_type: str = "UNKNOWN"
    product_match_method: str | None = None
    attribution_type: str = "UNATTRIBUTED"
    attribution_method: str | None = None
    attribution_confidence: Decimal = Decimal("0")
    standard_rate: str | None = None
    shop_ads_rate: str | None = None
    tiktok_bonus_rate: str | None = None
    partner_bonus_rate: str | None = None
    revenue_share_rate: str | None = None
    estimated_commission_base: Decimal | None = None
    estimated_standard_commission: Decimal | None = None
    estimated_shop_ads_commission: Decimal | None = None
    estimated_tiktok_bonus: Decimal | None = None
    estimated_partner_bonus: Decimal | None = None
    estimated_partner_agency_bonus: Decimal | None = None
    estimated_revenue_share: Decimal | None = None
    estimated_tax_vat: Decimal | None = None
    estimated_tax_isr: Decimal | None = None
    estimated_tax_cedular: Decimal | None = None
    estimated_tax_pit: Decimal | None = None
    actual_commission_base: Decimal | None = None
    actual_standard_commission: Decimal | None = None
    actual_shop_ads_commission: Decimal | None = None
    actual_bonus: Decimal | None = None
    actual_partner_agency_bonus: Decimal | None = None
    actual_tax_isr: Decimal | None = None
    actual_tax_vat: Decimal | None = None
    actual_tax_cedular: Decimal | None = None
    actual_tax_pit: Decimal | None = None
    actual_partner_share: Decimal | None = None
    final_received_amount: Decimal | None = None
    row_fingerprint: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedAffiliateOrder:
    external_order_id: str
    order_status: str | None = None
    order_created_at: str | None = None
    order_updated_at: str | None = None
    commission_status: str | None = None
    external_order_type: str | None = None
    normalized_order_type: str = "OTHER"
    external_settlement_status: str | None = None
    settlement_status: str = "UNKNOWN"
    commission_settled_at: str | None = None
    indirect: str | None = None
    commission_type: str | None = None
    shop_name: str | None = None
    shop_code: str | None = None
    partner_agency: str | None = None
    agency: str | None = None
    row_fingerprint: str | None = None
    items: list[NormalizedAffiliateItem] = field(default_factory=list)
    raw_payload: dict[str, Any] = field(default_factory=dict)


class AffiliateDataProvider(ABC):
    provider_name: str

    @abstractmethod
    def normalize_order(self, payload: dict[str, Any]) -> NormalizedAffiliateOrder:
        raise NotImplementedError

    def ingest_orders(
        self,
        conn: sqlite3.Connection,
        payloads: Iterable[dict[str, Any]],
        creator_account_id: int | None = None,
    ) -> dict[str, int]:
        orders = [self.normalize_order(payload) for payload in payloads]
        return persist_normalized_orders(conn, self.provider_name, orders, creator_account_id)


class TikTokShopCreatorProvider(AffiliateDataProvider):
    provider_name = PROVIDER_TIKTOK

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def fetch_orders_page(
        self,
        access_token: str,
        start_timestamp: int,
        end_timestamp: int,
        page_token: str | None = None,
        page_size: int = 100,
    ) -> dict[str, Any]:
        if self.client is None:
            raise RuntimeError("TikTokShopClient nao configurado")
        return self.client.search_creator_affiliate_orders(
            access_token=access_token,
            create_time_ge=start_timestamp,
            create_time_lt=end_timestamp,
            page_token=page_token,
            page_size=page_size,
        )

    def normalize_order(self, payload: dict[str, Any]) -> NormalizedAffiliateOrder:
        external_order_id = _required_text(payload.get("id"), "id")
        order_status = _optional_text(payload.get("status"))
        items: list[NormalizedAffiliateItem] = []
        for index, sku in enumerate(payload.get("skus") or []):
            if not isinstance(sku, dict):
                continue
            price, currency = _money(sku.get("price"))
            estimated, estimated_currency = _money(sku.get("estimated_commission"))
            actual = _sum_money_fields(
                sku,
                (
                    "actual_commission",
                    "actual_creator_commission",
                    "actual_bonus_commission",
                    "actual_shop_ads_commission",
                    "actual_creator_commission_reward_fee",
                ),
            )
            quantity = _number(sku.get("quantity")) or 0
            returned_quantity = _number(sku.get("returned_quantity")) or 0
            refunded_quantity = _number(sku.get("refunded_quantity")) or 0
            gmv = price * quantity if price is not None else None
            external_sku_id = _optional_text(sku.get("id"))
            external_product_id = _optional_text(sku.get("product_id"))
            external_content_id = _optional_text(sku.get("content_id"))
            item_key = stable_hash(
                [external_sku_id, external_product_id, external_content_id, index]
            )
            items.append(
                NormalizedAffiliateItem(
                    item_key=item_key,
                    external_sku_id=external_sku_id,
                    external_product_id=external_product_id,
                    external_content_id=external_content_id,
                    content_type=_optional_text(sku.get("content_type")),
                    product_name=_optional_text(sku.get("product_name")),
                    quantity=quantity,
                    returned_quantity=returned_quantity,
                    refunded_quantity=refunded_quantity,
                    currency=currency or estimated_currency,
                    unit_price=price,
                    gmv=gmv,
                    estimated_commission=estimated,
                    actual_commission=actual,
                    raw_payload=_redact_payload(sku),
                )
            )
        return NormalizedAffiliateOrder(
            external_order_id=external_order_id,
            order_status=order_status,
            order_created_at=_timestamp_iso(payload.get("create_time")),
            order_updated_at=_timestamp_iso(
                payload.get("update_time") or payload.get("delivery_time") or payload.get("create_time")
            ),
            commission_status=order_status,
            items=items,
            raw_payload=_redact_payload(payload),
        )


class ManualAffiliateProvider(AffiliateDataProvider):
    provider_name = PROVIDER_MANUAL

    def normalize_order(self, payload: dict[str, Any]) -> NormalizedAffiliateOrder:
        return _normalize_flat_order(payload)


class CsvAffiliateProvider(AffiliateDataProvider):
    provider_name = PROVIDER_CSV

    def normalize_order(self, payload: dict[str, Any]) -> NormalizedAffiliateOrder:
        return _normalize_flat_order(payload)

    def ingest_orders(
        self,
        conn: sqlite3.Connection,
        payloads: Iterable[dict[str, Any]],
        creator_account_id: int | None = None,
    ) -> dict[str, int]:
        grouped: dict[str, NormalizedAffiliateOrder] = {}
        for payload in payloads:
            order = self.normalize_order(payload)
            existing = grouped.get(order.external_order_id)
            if existing is None:
                grouped[order.external_order_id] = order
                continue
            items = {item.item_key: item for item in existing.items}
            items.update({item.item_key: item for item in order.items})
            existing.items = list(items.values())
            existing.order_status = order.order_status or existing.order_status
            existing.order_created_at = order.order_created_at or existing.order_created_at
            existing.order_updated_at = order.order_updated_at or existing.order_updated_at
            existing.commission_status = order.commission_status or existing.commission_status
        return persist_normalized_orders(
            conn, self.provider_name, grouped.values(), creator_account_id
        )

    def read_file(self, path: str | Path) -> list[dict[str, Any]]:
        source = Path(path)
        if source.suffix.lower() != ".csv":
            raise ValueError("O fallback aceita somente arquivo CSV")
        with source.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            return [canonicalize_report_row(dict(row)) for row in csv.DictReader(handle, dialect=dialect)]


REPORT_COLUMN_ALIASES = {
    "external_order_id": {
        "external order id", "order id", "orderid", "id do pedido", "pedido id",
        "numero do pedido", "numero pedido", "n do pedido",
    },
    "order_status": {"order status", "status", "status do pedido", "situacao do pedido"},
    "order_created_at": {
        "order created at", "order created time", "created at", "created time",
        "data do pedido", "hora do pedido", "data de criacao", "data criacao",
    },
    "order_updated_at": {
        "order updated at", "order updated time", "updated at", "updated time",
        "data de atualizacao", "ultima atualizacao",
    },
    "tiktok_product_id": {
        "tiktok product id", "product id", "id do produto", "produto id",
    },
    "tiktok_sku_id": {"tiktok sku id", "sku id", "seller sku id", "id do sku"},
    "tiktok_video_id": {
        "tiktok video id", "video id", "content id", "id do video", "id do conteudo",
    },
    "content_type": {"content type", "tipo de conteudo"},
    "product_name": {"product name", "nome do produto", "produto"},
    "quantity": {"quantity", "qty", "quantidade", "itens vendidos", "units sold"},
    "returned_quantity": {"returned quantity", "quantidade devolvida"},
    "refunded_quantity": {"refunded quantity", "quantidade reembolsada"},
    "currency": {"currency", "moeda"},
    "unit_price": {"unit price", "price", "preco unitario", "preco"},
    "gmv": {"gmv", "gross merchandise value", "valor gmv", "receita"},
    "estimated_commission": {
        "estimated commission", "commission estimate", "comissao estimada",
    },
    "actual_commission": {
        "actual commission", "commission", "creator commission", "comissao real",
        "comissao", "comissao do criador",
    },
    "refund_amount": {
        "refund amount", "refunded amount", "valor reembolsado", "reembolso",
    },
    "commission_status": {"commission status", "status da comissao"},
}


def canonicalize_report_row(row: dict[str, Any]) -> dict[str, Any]:
    aliases = {
        _normalize_column_name(alias): canonical
        for canonical, names in REPORT_COLUMN_ALIASES.items()
        for alias in ({canonical.replace("_", " ")} | names)
    }
    result: dict[str, Any] = {}
    for key, value in row.items():
        canonical = aliases.get(_normalize_column_name(key))
        if canonical:
            result[canonical] = value
    return result


def report_header_score(headers: Iterable[Any]) -> int:
    mapped = canonicalize_report_row({str(header): "" for header in headers})
    if "external_order_id" not in mapped:
        return 0
    return len(mapped)


def _normalize_column_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().split())


def persist_normalized_orders(
    conn: sqlite3.Connection,
    provider: str,
    orders: Iterable[NormalizedAffiliateOrder],
    creator_account_id: int | None = None,
) -> dict[str, int]:
    seen = 0
    upserted = 0
    affected_content_ids: set[int] = set()
    for order in orders:
        seen += 1
        timestamp = now_iso()
        existing = conn.execute(
            "SELECT id, order_status FROM affiliate_orders WHERE provider = ? AND external_order_id = ?",
            (provider, order.external_order_id),
        ).fetchone()
        if existing:
            affected_content_ids.update(
                int(row["content_id"])
                for row in conn.execute(
                    "SELECT DISTINCT content_id FROM affiliate_order_items WHERE affiliate_order_id = ? AND content_id IS NOT NULL",
                    (existing["id"],),
                )
            )
        resolved_items = [_resolve_item_links(conn, item) for item in order.items]
        totals = _order_totals(resolved_items)
        product_ids = {item["product_id"] for item in resolved_items if item["product_id"] is not None}
        content_ids = {item["content_id"] for item in resolved_items if item["content_id"] is not None}
        primary_product_id = next(iter(product_ids)) if len(product_ids) == 1 else None
        primary_content_id = next(iter(content_ids)) if len(content_ids) == 1 else None
        affected_content_ids.update(content_ids)
        conn.execute(
            """
            INSERT INTO affiliate_orders (
                provider, external_order_id, creator_account_id, product_id, content_id,
                external_product_id, order_status, order_created_at, order_updated_at,
                quantity, currency, gmv, estimated_commission, actual_commission,
                commission_status, refund_amount, raw_payload, first_seen_at,
                last_seen_at, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, external_order_id) DO UPDATE SET
                creator_account_id = COALESCE(excluded.creator_account_id, affiliate_orders.creator_account_id),
                product_id = excluded.product_id,
                content_id = excluded.content_id,
                external_product_id = excluded.external_product_id,
                order_status = excluded.order_status,
                order_created_at = COALESCE(excluded.order_created_at, affiliate_orders.order_created_at),
                order_updated_at = COALESCE(excluded.order_updated_at, affiliate_orders.order_updated_at),
                quantity = excluded.quantity,
                currency = COALESCE(excluded.currency, affiliate_orders.currency),
                gmv = excluded.gmv,
                estimated_commission = excluded.estimated_commission,
                actual_commission = excluded.actual_commission,
                commission_status = excluded.commission_status,
                refund_amount = excluded.refund_amount,
                raw_payload = excluded.raw_payload,
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
            """,
            (
                provider,
                order.external_order_id,
                creator_account_id,
                primary_product_id,
                primary_content_id,
                _single_value([item["external_product_id"] for item in resolved_items]),
                order.order_status,
                order.order_created_at,
                order.order_updated_at,
                totals["quantity"],
                totals["currency"],
                totals["gmv"],
                totals["estimated_commission"],
                totals["actual_commission"],
                order.commission_status,
                totals["refund_amount"],
                json.dumps(_redact_payload(order.raw_payload), ensure_ascii=False),
                timestamp,
                timestamp,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT id FROM affiliate_orders WHERE provider = ? AND external_order_id = ?",
            (provider, order.external_order_id),
        ).fetchone()
        order_id = int(row["id"])
        if not existing or existing["order_status"] != order.order_status:
            conn.execute(
                """
                INSERT INTO affiliate_order_status_history (
                    affiliate_order_id, previous_status, new_status, changed_at, raw_payload
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    existing["order_status"] if existing else None,
                    order.order_status,
                    timestamp,
                    json.dumps(_redact_payload(order.raw_payload), ensure_ascii=False),
                ),
            )
        _upsert_items(conn, order_id, resolved_items, timestamp)
        upserted += 1
    refresh_content_performance(conn, provider, affected_content_ids)
    conn.commit()
    return {"seen": seen, "upserted": upserted}


def refresh_content_performance(
    conn: sqlite3.Connection,
    provider: str,
    content_ids: Iterable[int],
) -> None:
    for content_id in set(content_ids):
        aggregate = conn.execute(
            """
            SELECT COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.quantity), 0) AS units_sold,
                   SUM(i.gmv) AS gmv,
                   SUM(i.estimated_commission) AS estimated_commission,
                   SUM(i.actual_commission) AS actual_commission,
                   SUM(i.refund_amount) AS refunds,
                   MAX(COALESCE(o.order_updated_at, o.last_seen_at)) AS source_timestamp
            FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            WHERE o.provider = ? AND i.content_id = ?
            """,
            (provider, content_id),
        ).fetchone()
        source_hash = stable_hash(
            [
                provider,
                content_id,
                aggregate["orders"],
                aggregate["units_sold"],
                aggregate["gmv"],
                aggregate["estimated_commission"],
                aggregate["actual_commission"],
                aggregate["refunds"],
                aggregate["source_timestamp"],
            ]
        )
        if conn.execute(
            "SELECT 1 FROM content_performance WHERE provider = ? AND source_row_hash = ?",
            (provider, source_hash),
        ).fetchone():
            continue
        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO content_performance (
                content_id, captured_at, orders, units_sold, gmv, commission,
                source, source_row_hash, created_at, provider, source_timestamp,
                synced_at, estimated_commission, actual_commission, refunds,
                attribution_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                content_id,
                timestamp,
                aggregate["orders"] or 0,
                aggregate["units_sold"] or 0,
                aggregate["gmv"] or 0,
                aggregate["actual_commission"] or aggregate["estimated_commission"] or 0,
                provider,
                source_hash,
                timestamp,
                provider,
                aggregate["source_timestamp"],
                timestamp,
                aggregate["estimated_commission"],
                aggregate["actual_commission"],
                aggregate["refunds"] or 0,
                "OFFICIAL" if provider == PROVIDER_TIKTOK else "EXTERNAL_ID",
            ),
        )


def affiliate_dashboard(
    conn: sqlite3.Connection,
    days: int = 30,
    *,
    as_of: str | None = None,
) -> dict[str, Any]:
    days = max(1, min(int(days), 3650))
    window = f"-{days - 1} days"
    totals = conn.execute(
        """
        SELECT COUNT(DISTINCT id) AS orders,
               COALESCE(SUM(quantity), 0) AS units_sold,
               COALESCE(SUM(gmv), 0) AS gmv,
               COALESCE(SUM(estimated_commission), 0) AS estimated_commission,
               COALESCE(SUM(actual_commission), 0) AS actual_commission,
               COALESCE(SUM(refund_amount), 0) AS refunds
        FROM affiliate_orders
        WHERE date(order_created_at) >= date(COALESCE(?, 'now'), ?)
        """,
        (as_of, window),
    ).fetchone()
    windows: dict[str, Any] = {}
    for label, offset in (("today", "0 days"), ("days_7", "-6 days"), ("days_30", "-29 days")):
        row = conn.execute(
            """
            SELECT COUNT(DISTINCT id) AS orders, COALESCE(SUM(gmv), 0) AS gmv
            FROM affiliate_orders
            WHERE date(order_created_at) >= date(COALESCE(?, 'now'), ?)
            """,
            (as_of, offset),
        ).fetchone()
        windows[label] = dict(row)
    top_products = [
        dict(row)
        for row in conn.execute(
            """
            SELECT COALESCE(p.name, i.product_name, i.external_product_id, 'Nao mapeado') AS name,
                   i.product_id, COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.quantity), 0) AS units_sold,
                   COALESCE(SUM(i.gmv), 0) AS gmv,
                   COALESCE(SUM(i.actual_commission), 0) AS actual_commission
            FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            LEFT JOIN products p ON p.id = i.product_id
            WHERE date(o.order_created_at) >= date(COALESCE(?, 'now'), ?)
            GROUP BY COALESCE(p.name, i.product_name, i.external_product_id, 'Nao mapeado'), i.product_id
            ORDER BY gmv DESC
            LIMIT 10
            """,
            (as_of, window),
        ).fetchall()
    ]
    top_content = [
        dict(row)
        for row in conn.execute(
            """
            SELECT c.id, c.content_code, c.tiktok_url, c.tiktok_video_id,
                   COUNT(DISTINCT o.id) AS orders, COALESCE(SUM(i.gmv), 0) AS gmv,
                   COALESCE(SUM(i.actual_commission), 0) AS actual_commission
            FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            JOIN content_items c ON c.id = i.content_id
            WHERE date(o.order_created_at) >= date(COALESCE(?, 'now'), ?)
            GROUP BY c.id
            ORDER BY gmv DESC
            LIMIT 10
            """,
            (as_of, window),
        ).fetchall()
    ]
    return {
        "period_days": days,
        "totals": dict(totals),
        "windows": windows,
        "top_products": top_products,
        "top_content": top_content,
    }


def product_affiliate_performance(conn: sqlite3.Connection, product_id: int) -> dict[str, Any]:
    totals = conn.execute(
        """
        SELECT COUNT(DISTINCT o.id) AS orders,
               COALESCE(SUM(CASE WHEN o.provider = 'tiktok_report' THEN i.items_sold ELSE i.quantity END), 0) AS units_sold,
               COALESCE(SUM(COALESCE(i.gmv_minor / 100.0, i.gmv, 0)), 0) AS gmv,
               COALESCE(SUM(COALESCE(i.estimated_commission_minor / 100.0, i.estimated_commission, 0)), 0) AS estimated_commission,
               COALESCE(SUM(COALESCE(i.actual_commission_minor / 100.0, i.actual_commission, 0)), 0) AS actual_commission,
               COALESCE(SUM(CASE WHEN o.provider = 'tiktok_report' THEN i.items_refunded ELSE COALESCE(i.refund_amount, 0) END), 0) AS refunds
        FROM affiliate_order_items i
        JOIN affiliate_orders o ON o.id = i.affiliate_order_id
        WHERE i.product_id = ?
        """,
        (product_id,),
    ).fetchone()
    daily = [
        dict(row)
        for row in conn.execute(
            """
            SELECT date(o.order_created_at) AS date, COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(CASE WHEN o.provider = 'tiktok_report' THEN i.items_sold ELSE i.quantity END), 0) AS units_sold,
                   COALESCE(SUM(COALESCE(i.gmv_minor / 100.0, i.gmv, 0)), 0) AS gmv,
                   COALESCE(SUM(COALESCE(i.actual_commission_minor / 100.0, i.actual_commission, 0)), 0) AS actual_commission,
                   COALESCE(SUM(CASE WHEN o.provider = 'tiktok_report' THEN i.items_refunded ELSE COALESCE(i.refund_amount, 0) END), 0) AS refunds
            FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            WHERE i.product_id = ?
            GROUP BY date(o.order_created_at)
            ORDER BY date DESC
            LIMIT 90
            """,
            (product_id,),
        ).fetchall()
    ]
    return {"totals": dict(totals), "daily": daily}


def _normalize_flat_order(payload: dict[str, Any]) -> NormalizedAffiliateOrder:
    external_order_id = _required_text(
        payload.get("external_order_id") or payload.get("order_id") or payload.get("id"),
        "external_order_id",
    )
    external_product_id = _optional_text(
        payload.get("external_product_id") or payload.get("tiktok_product_id") or payload.get("product_id")
    )
    external_sku_id = _optional_text(payload.get("external_sku_id") or payload.get("tiktok_sku_id") or payload.get("sku_id"))
    external_content_id = _optional_text(
        payload.get("external_content_id") or payload.get("tiktok_video_id")
    )
    item = NormalizedAffiliateItem(
        item_key=stable_hash([external_sku_id, external_product_id, external_content_id, 0]),
        external_sku_id=external_sku_id,
        external_product_id=external_product_id,
        external_content_id=external_content_id,
        content_type=_optional_text(payload.get("content_type")),
        product_name=_optional_text(payload.get("product_name")),
        quantity=_number(payload.get("quantity")) or 0,
        returned_quantity=_number(payload.get("returned_quantity")) or 0,
        refunded_quantity=_number(payload.get("refunded_quantity")) or 0,
        currency=_optional_text(payload.get("currency")),
        unit_price=_localized_number(payload.get("unit_price")),
        gmv=_localized_number(payload.get("gmv")),
        estimated_commission=_localized_number(payload.get("estimated_commission")),
        actual_commission=_localized_number(payload.get("actual_commission")),
        refund_amount=_localized_number(payload.get("refund_amount")),
        raw_payload=_redact_payload(payload),
    )
    return NormalizedAffiliateOrder(
        external_order_id=external_order_id,
        order_status=_optional_text(payload.get("order_status") or payload.get("status")),
        order_created_at=_coerce_iso(payload.get("order_created_at") or payload.get("created_at")),
        order_updated_at=_coerce_iso(payload.get("order_updated_at") or payload.get("updated_at")),
        commission_status=_optional_text(payload.get("commission_status")),
        items=[item],
        raw_payload=_redact_payload(payload),
    )


def _resolve_item_links(conn: sqlite3.Connection, item: NormalizedAffiliateItem) -> dict[str, Any]:
    product = None
    if item.external_product_id:
        product = conn.execute(
            "SELECT id FROM products WHERE tiktok_product_id = ?",
            (item.external_product_id,),
        ).fetchone()
    if product is None and item.external_sku_id:
        product = conn.execute(
            "SELECT id FROM products WHERE tiktok_sku_id = ?",
            (item.external_sku_id,),
        ).fetchone()
    content = None
    if item.external_content_id:
        content = conn.execute(
            "SELECT id FROM content_items WHERE tiktok_video_id = ?",
            (item.external_content_id,),
        ).fetchone()
    result = item.__dict__.copy()
    result["product_id"] = int(product["id"]) if product else None
    result["content_id"] = int(content["id"]) if content else None
    return result


def _upsert_items(conn: sqlite3.Connection, order_id: int, items: list[dict[str, Any]], timestamp: str) -> None:
    current_keys: list[str] = []
    for item in items:
        current_keys.append(item["item_key"])
        conn.execute(
            """
            INSERT INTO affiliate_order_items (
                affiliate_order_id, item_key, external_sku_id, product_id,
                external_product_id, content_id, external_content_id, content_type,
                product_name, quantity, returned_quantity, refunded_quantity,
                currency, unit_price, gmv, estimated_commission, actual_commission,
                refund_amount, raw_payload, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(affiliate_order_id, item_key) DO UPDATE SET
                external_sku_id = excluded.external_sku_id,
                product_id = excluded.product_id,
                external_product_id = excluded.external_product_id,
                content_id = excluded.content_id,
                external_content_id = excluded.external_content_id,
                content_type = excluded.content_type,
                product_name = excluded.product_name,
                quantity = excluded.quantity,
                returned_quantity = excluded.returned_quantity,
                refunded_quantity = excluded.refunded_quantity,
                currency = excluded.currency,
                unit_price = excluded.unit_price,
                gmv = excluded.gmv,
                estimated_commission = excluded.estimated_commission,
                actual_commission = excluded.actual_commission,
                refund_amount = excluded.refund_amount,
                raw_payload = excluded.raw_payload,
                updated_at = excluded.updated_at
            """,
            (
                order_id,
                item["item_key"],
                item["external_sku_id"],
                item["product_id"],
                item["external_product_id"],
                item["content_id"],
                item["external_content_id"],
                item["content_type"],
                item["product_name"],
                item["quantity"],
                item["returned_quantity"],
                item["refunded_quantity"],
                item["currency"],
                item["unit_price"],
                item["gmv"],
                item["estimated_commission"],
                item["actual_commission"],
                item["refund_amount"],
                json.dumps(_redact_payload(item["raw_payload"]), ensure_ascii=False),
                timestamp,
                timestamp,
            ),
        )
    if current_keys:
        placeholders = ",".join("?" for _ in current_keys)
        conn.execute(
            f"DELETE FROM affiliate_order_items WHERE affiliate_order_id = ? AND item_key NOT IN ({placeholders})",
            (order_id, *current_keys),
        )
    else:
        conn.execute("DELETE FROM affiliate_order_items WHERE affiliate_order_id = ?", (order_id,))


def _order_totals(items: list[dict[str, Any]]) -> dict[str, Any]:
    currency = _single_value([item["currency"] for item in items])
    mixed_currencies = currency is None and len(
        {item["currency"] for item in items if item["currency"] not in (None, "")}
    ) > 1
    return {
        "quantity": sum(item["quantity"] for item in items),
        "currency": currency,
        "gmv": None if mixed_currencies else _sum_nullable(item["gmv"] for item in items),
        "estimated_commission": None
        if mixed_currencies
        else _sum_nullable(item["estimated_commission"] for item in items),
        "actual_commission": None
        if mixed_currencies
        else _sum_nullable(item["actual_commission"] for item in items),
        "refund_amount": None
        if mixed_currencies
        else _sum_nullable(item["refund_amount"] for item in items),
    }


def _single_value(values: Iterable[Any]) -> Any | None:
    distinct = {value for value in values if value not in (None, "")}
    return next(iter(distinct)) if len(distinct) == 1 else None


def _sum_nullable(values: Iterable[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) if present else None


def _money(value: Any) -> tuple[float | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    return _strict_number(value.get("amount")), _optional_text(value.get("currency"))


def _sum_money_fields(payload: dict[str, Any], keys: Iterable[str]) -> float | None:
    values = [_money(payload.get(key))[0] for key in keys]
    return _sum_nullable(values)


def _strict_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    return float(text)


def _localized_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None or str(value).strip() == "":
        return None
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _number(value: Any) -> float | None:
    return _strict_number(value)


def _timestamp_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if 20_000 <= numeric <= 80_000:
        return (
            datetime(1899, 12, 30, tzinfo=timezone.utc) + timedelta(days=numeric)
        ).replace(microsecond=0).isoformat()
    if numeric > 10_000_000_000:
        numeric /= 1000
    return datetime.fromtimestamp(numeric, tz=timezone.utc).replace(microsecond=0).isoformat()


def _coerce_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    timestamp = _timestamp_iso(value)
    if timestamp:
        return timestamp
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    except ValueError:
        return None


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise ValueError(f"Campo obrigatorio ausente: {field_name}")
    return text


def _optional_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()


def _redact_payload(value: Any) -> Any:
    sensitive = {
        "access_token",
        "refresh_token",
        "app_secret",
        "authorization",
        "x-tts-access-token",
    }
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if str(key).lower() in sensitive else _redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value
