from __future__ import annotations

import csv
import json
import re
import sqlite3
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from src.affiliate import (
    AffiliateDataProvider,
    NormalizedAffiliateItem,
    NormalizedAffiliateOrder,
    PROVIDER_TIKTOK_REPORT,
)
from src.kalodata_xlsx import read_xlsx
from src.store import now_iso, product_key, stable_hash


REPORT_AFFILIATE_ORDERS = "TIKTOK_AFFILIATE_ORDERS"
REPORT_VIDEO_ANALYTICS = "TIKTOK_VIDEO_ANALYTICS"
WAITING_FOR_VIDEO_ANALYTICS = "WAITING_FOR_VIDEO_ANALYTICS"

COMPATIBLE = "COMPATIBLE"
COMPATIBLE_WITH_NEW_COLUMNS = "COMPATIBLE_WITH_NEW_COLUMNS"
MISSING_REQUIRED_COLUMNS = "MISSING_REQUIRED_COLUMNS"
UNKNOWN_FORMAT = "UNKNOWN_FORMAT"

CONTENT_VIDEO = "VIDEO"
CONTENT_SHOWCASE = "SHOWCASE"
CONTENT_LIVE = "LIVE"
CONTENT_UNKNOWN = "UNKNOWN"

SETTLED = "SETTLED"
INELIGIBLE = "INELIGIBLE"
PENDING = "PENDING"
AWAITING_PAYMENT = "AWAITING_PAYMENT"
SETTLEMENT_UNKNOWN = "UNKNOWN"

SHOP_AD_ORDER = "SHOP_AD_ORDER"
AFFILIATE_ORDER = "AFFILIATE_ORDER"
OTHER_ORDER = "OTHER"

REQUIRED_FIELDS = {
    "external_order_id",
    "external_product_id",
    "external_content_type",
    "external_content_id",
    "gmv",
    "order_created_at",
}

CURRENT_HEADERS = {
    "external_order_id",
    "external_sku_id",
    "external_product_name",
    "external_product_id",
    "price",
    "items_sold",
    "items_refunded",
    "shop_name",
    "shop_code",
    "partner_agency",
    "agency",
    "currency",
    "external_order_type",
    "external_settlement_status",
    "indirect",
    "commission_type",
    "external_content_type",
    "external_content_id",
    "standard_rate",
    "shop_ads_rate",
    "tiktok_bonus_rate",
    "partner_bonus_rate",
    "revenue_share_rate",
    "gmv",
    "estimated_commission_base",
    "estimated_standard_commission",
    "estimated_shop_ads_commission",
    "estimated_tiktok_bonus",
    "estimated_partner_agency_bonus",
    "estimated_tax_vat",
    "estimated_tax_isr",
    "estimated_tax_cedular",
    "estimated_tax_pit",
    "estimated_revenue_share",
    "actual_commission_base",
    "actual_standard_commission",
    "actual_shop_ads_commission",
    "actual_bonus",
    "actual_partner_agency_bonus",
    "actual_tax_isr",
    "actual_tax_vat",
    "actual_tax_cedular",
    "actual_tax_pit",
    "actual_partner_share",
    "final_received_amount",
    "order_created_at",
    "commission_settled_at",
}

HEADER_ALIASES = {
    "id do pedido": "external_order_id",
    "id do sku": "external_sku_id",
    "nome do produto": "external_product_name",
    "id do produto": "external_product_id",
    "preco": "price",
    "itens vendidos": "items_sold",
    "itens reembolsados": "items_refunded",
    "nome da loja": "shop_name",
    "codigo da loja": "shop_code",
    "agencia parceira": "partner_agency",
    "agencia": "agency",
    "moeda": "currency",
    "tipo de pedido": "external_order_type",
    "status de liquidacao do pedido": "external_settlement_status",
    "indireta": "indirect",
    "tipo de comissao": "commission_type",
    "tipo de conteudo": "external_content_type",
    "id do conteudo": "external_content_id",
    "padrao": "standard_rate",
    "anuncios da loja": "shop_ads_rate",
    "bonus do tiktok": "tiktok_bonus_rate",
    "bonus do parceiro": "partner_bonus_rate",
    "participacao na receita": "revenue_share_rate",
    "gmv": "gmv",
    "base de comissao estimada": "estimated_commission_base",
    "comissao padrao estimada": "estimated_standard_commission",
    "comissao estimada de anuncios da loja": "estimated_shop_ads_commission",
    "bonus estimado": "estimated_tiktok_bonus",
    "bonus estimado da agencia parceira": "estimated_partner_agency_bonus",
    "iva estimado": "estimated_tax_vat",
    "isr estimado": "estimated_tax_isr",
    "est cedulartax": "estimated_tax_cedular",
    "pit estimado": "estimated_tax_pit",
    "estimativa da participacao na receita": "estimated_revenue_share",
    "base de comissao real": "actual_commission_base",
    "comissao padrao": "actual_standard_commission",
    "comissao de anuncios da loja": "actual_shop_ads_commission",
    "bonus": "actual_bonus",
    "bonus da agencia parceira": "actual_partner_agency_bonus",
    "imposto isr": "actual_tax_isr",
    "imposto iva": "actual_tax_vat",
    "cedular tax": "actual_tax_cedular",
    "imposto pit": "actual_tax_pit",
    "compartilhado com parceiro": "actual_partner_share",
    "valor total final recebido": "final_received_amount",
    "data do pedido": "order_created_at",
    "data de liquidacao da comissao": "commission_settled_at",
    "order id": "external_order_id",
    "sku id": "external_sku_id",
    "product name": "external_product_name",
    "product id": "external_product_id",
    "price": "price",
    "items sold": "items_sold",
    "items refunded": "items_refunded",
    "shop name": "shop_name",
    "shop code": "shop_code",
    "affiliate partner": "partner_agency",
    "agency": "agency",
    "currency": "currency",
    "order type": "external_order_type",
    "order settlement status": "external_settlement_status",
    "indirect": "indirect",
    "commission type": "commission_type",
    "content type": "external_content_type",
    "content id": "external_content_id",
    "standard": "standard_rate",
    "shop ads": "shop_ads_rate",
    "tiktok bonus": "tiktok_bonus_rate",
    "partner bonus": "partner_bonus_rate",
    "revenue sharing portion": "revenue_share_rate",
    "est commission base": "estimated_commission_base",
    "est standard commission": "estimated_standard_commission",
    "est shop ads commission": "estimated_shop_ads_commission",
    "est bonus": "estimated_tiktok_bonus",
    "est affiliate partner bonus": "estimated_partner_agency_bonus",
    "est iva": "estimated_tax_vat",
    "est isr": "estimated_tax_isr",
    "est pit": "estimated_tax_pit",
    "est revenue sharing portion": "estimated_revenue_share",
    "actual commission base": "actual_commission_base",
    "standard commission": "actual_standard_commission",
    "shop ads commission": "actual_shop_ads_commission",
    "affiliate partner bonus": "actual_partner_agency_bonus",
    "tax isr": "actual_tax_isr",
    "tax iva": "actual_tax_vat",
    "tax pit": "actual_tax_pit",
    "shared with partner": "actual_partner_share",
    "total final earned amount": "final_received_amount",
    "order date": "order_created_at",
    "commission settlement date": "commission_settled_at",
}


@dataclass(frozen=True)
class TikTokReportDetection:
    report_type: str
    compatibility: str
    mapped_headers: dict[str, str]
    missing_required: tuple[str, ...]
    new_columns: tuple[str, ...]


@dataclass(frozen=True)
class ReportRowError:
    row_number: int
    code: str
    message: str
    raw_payload: dict[str, Any]


@dataclass
class ParsedTikTokReport:
    report_type: str
    compatibility: str
    orders: list[NormalizedAffiliateOrder]
    errors: list[ReportRowError]
    rows_total: int
    period_start: str | None
    period_end: str | None
    product_ids: set[str] = field(default_factory=set)
    sku_ids: set[str] = field(default_factory=set)
    content_ids: set[str] = field(default_factory=set)
    new_columns: tuple[str, ...] = ()

    def preview(self) -> dict[str, Any]:
        return {
            "report_type": self.report_type,
            "compatibility": self.compatibility,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "rows_total": self.rows_total,
            "rows_valid": self.rows_total - len(self.errors),
            "rows_failed": len(self.errors),
            "orders": len(self.orders),
            "products": len(self.product_ids),
            "skus": len(self.sku_ids),
            "contents": len(self.content_ids),
            "new_columns": list(self.new_columns),
        }


class TikTokReportDetector:
    def detect(self, headers: Iterable[Any]) -> TikTokReportDetection:
        mapped: dict[str, str] = {}
        new_columns: list[str] = []
        for header in headers:
            original = str(header or "").strip()
            normalized = normalize_header(original)
            canonical = HEADER_ALIASES.get(normalized)
            if canonical:
                mapped[original] = canonical
            elif original:
                new_columns.append(original)
        present = set(mapped.values())
        missing = tuple(sorted(REQUIRED_FIELDS - present))
        if missing:
            compatibility = MISSING_REQUIRED_COLUMNS if present else UNKNOWN_FORMAT
            report_type = REPORT_AFFILIATE_ORDERS if present else UNKNOWN_FORMAT
        else:
            report_type = REPORT_AFFILIATE_ORDERS
            compatibility = COMPATIBLE_WITH_NEW_COLUMNS if new_columns else COMPATIBLE
        return TikTokReportDetection(
            report_type=report_type,
            compatibility=compatibility,
            mapped_headers=mapped,
            missing_required=missing,
            new_columns=tuple(new_columns),
        )


class TikTokReportImporter(ABC):
    report_type: str

    @abstractmethod
    def parse_file(self, path: str | Path) -> ParsedTikTokReport:
        raise NotImplementedError


class TikTokAffiliateOrdersImporter(TikTokReportImporter):
    report_type = REPORT_AFFILIATE_ORDERS

    def __init__(self, account_timezone: str = "America/Sao_Paulo") -> None:
        try:
            self.timezone = ZoneInfo(account_timezone)
        except Exception:
            if account_timezone != "America/Sao_Paulo":
                raise
            self.timezone = timezone(timedelta(hours=-3), name="America/Sao_Paulo")
        self.detector = TikTokReportDetector()

    def parse_file(self, path: str | Path) -> ParsedTikTokReport:
        source = Path(path)
        headers, raw_rows = _read_report_rows(source)
        detection = self.detector.detect(headers)
        if detection.report_type != self.report_type or detection.missing_required:
            missing = ", ".join(detection.missing_required)
            raise ValueError(f"Relatorio Affiliate Orders incompativel; campos ausentes: {missing}")

        parsed_rows: list[tuple[int, NormalizedAffiliateOrder]] = []
        errors: list[ReportRowError] = []
        dates: list[str] = []
        for row_number, raw in raw_rows:
            canonical = {
                detection.mapped_headers[key]: value
                for key, value in raw.items()
                if key in detection.mapped_headers
            }
            try:
                order = self._normalize_row(canonical, raw, row_number)
                parsed_rows.append((row_number, order))
                if order.order_created_at:
                    dates.append(order.order_created_at)
            except ReportValidationError as exc:
                errors.append(
                    ReportRowError(
                        row_number=row_number,
                        code=exc.code,
                        message=str(exc),
                        raw_payload=_json_safe_row(raw),
                    )
                )

        orders = _group_report_orders(parsed_rows)
        items = [item for order in orders for item in order.items]
        return ParsedTikTokReport(
            report_type=self.report_type,
            compatibility=detection.compatibility,
            orders=orders,
            errors=errors,
            rows_total=len(raw_rows),
            period_start=min(dates) if dates else None,
            period_end=max(dates) if dates else None,
            product_ids={item.external_product_id for item in items if item.external_product_id},
            sku_ids={item.external_sku_id for item in items if item.external_sku_id},
            content_ids={item.external_content_id for item in items if item.external_content_id},
            new_columns=detection.new_columns,
        )

    def _normalize_row(
        self,
        row: dict[str, Any],
        raw: dict[str, Any],
        row_number: int,
    ) -> NormalizedAffiliateOrder:
        order_id = _required_id(row.get("external_order_id"), "EMPTY_ORDER_ID")
        product_id = _required_id(row.get("external_product_id"), "EMPTY_PRODUCT_ID")
        content_id = _required_id(row.get("external_content_id"), "EMPTY_CONTENT_ID")
        content_type = normalize_content_type(row.get("external_content_type"))
        gmv = parse_decimal_br(row.get("gmv"), required=True, error_code="INVALID_GMV")
        order_created_at = parse_report_datetime(
            row.get("order_created_at"), self.timezone, required=True
        )
        currency = _text(row.get("currency")) or "BRL"
        if currency != "BRL":
            raise ReportValidationError("UNKNOWN_CURRENCY", f"Moeda desconhecida: {currency}")

        estimated_components = {
            "standard": parse_decimal_br(row.get("estimated_standard_commission")),
            "shop_ads": parse_decimal_br(row.get("estimated_shop_ads_commission")),
            "tiktok_bonus": parse_decimal_br(row.get("estimated_tiktok_bonus")),
            "partner_agency_bonus": parse_decimal_br(row.get("estimated_partner_agency_bonus")),
            "revenue_share": parse_decimal_br(row.get("estimated_revenue_share")),
        }
        actual_components = {
            "standard": parse_decimal_br(row.get("actual_standard_commission")),
            "shop_ads": parse_decimal_br(row.get("actual_shop_ads_commission")),
            "bonus": parse_decimal_br(row.get("actual_bonus")),
            "partner_agency_bonus": parse_decimal_br(row.get("actual_partner_agency_bonus")),
        }
        sku_id = _required_id(row.get("external_sku_id"), "EMPTY_SKU_ID")
        raw_safe = _json_safe_row(raw)
        row_fingerprint = stable_hash([raw_safe])
        item = NormalizedAffiliateItem(
            item_key=sku_id,
            external_sku_id=sku_id,
            external_product_id=product_id,
            external_content_id=content_id,
            content_type=_text(row.get("external_content_type")),
            normalized_content_type=content_type,
            product_name=_text(row.get("external_product_name")),
            quantity=parse_integer(row.get("items_sold"), "INVALID_ITEMS_SOLD"),
            refunded_quantity=parse_integer(
                row.get("items_refunded"), "INVALID_ITEMS_REFUNDED"
            ),
            currency=currency,
            unit_price=parse_decimal_br(row.get("price")),
            gmv=gmv,
            estimated_commission=_sum_components(estimated_components.values()),
            actual_commission=_sum_components(actual_components.values()),
            standard_rate=_text(row.get("standard_rate")),
            shop_ads_rate=_text(row.get("shop_ads_rate")),
            tiktok_bonus_rate=_text(row.get("tiktok_bonus_rate")),
            partner_bonus_rate=_text(row.get("partner_bonus_rate")),
            revenue_share_rate=_text(row.get("revenue_share_rate")),
            estimated_commission_base=parse_decimal_br(row.get("estimated_commission_base")),
            estimated_standard_commission=estimated_components["standard"],
            estimated_shop_ads_commission=estimated_components["shop_ads"],
            estimated_tiktok_bonus=estimated_components["tiktok_bonus"],
            estimated_partner_agency_bonus=estimated_components["partner_agency_bonus"],
            estimated_revenue_share=estimated_components["revenue_share"],
            estimated_tax_vat=parse_decimal_br(row.get("estimated_tax_vat")),
            estimated_tax_isr=parse_decimal_br(row.get("estimated_tax_isr")),
            estimated_tax_cedular=parse_decimal_br(row.get("estimated_tax_cedular")),
            estimated_tax_pit=parse_decimal_br(row.get("estimated_tax_pit")),
            actual_commission_base=parse_decimal_br(row.get("actual_commission_base")),
            actual_standard_commission=actual_components["standard"],
            actual_shop_ads_commission=actual_components["shop_ads"],
            actual_bonus=actual_components["bonus"],
            actual_partner_agency_bonus=actual_components["partner_agency_bonus"],
            actual_tax_isr=parse_decimal_br(row.get("actual_tax_isr")),
            actual_tax_vat=parse_decimal_br(row.get("actual_tax_vat")),
            actual_tax_cedular=parse_decimal_br(row.get("actual_tax_cedular")),
            actual_tax_pit=parse_decimal_br(row.get("actual_tax_pit")),
            actual_partner_share=parse_decimal_br(row.get("actual_partner_share")),
            final_received_amount=parse_decimal_br(row.get("final_received_amount")),
            row_fingerprint=row_fingerprint,
            raw_payload=raw_safe,
        )
        settlement_external = _text(row.get("external_settlement_status"))
        order = NormalizedAffiliateOrder(
            external_order_id=order_id,
            order_status=normalize_settlement_status(settlement_external),
            order_created_at=order_created_at,
            order_updated_at=order_created_at,
            commission_status=normalize_settlement_status(settlement_external),
            external_order_type=_text(row.get("external_order_type")),
            normalized_order_type=normalize_order_type(row.get("external_order_type")),
            external_settlement_status=settlement_external,
            settlement_status=normalize_settlement_status(settlement_external),
            commission_settled_at=parse_report_datetime(
                row.get("commission_settled_at"), self.timezone
            ),
            indirect=_text(row.get("indirect")),
            commission_type=_text(row.get("commission_type")),
            shop_name=_text(row.get("shop_name")),
            shop_code=_text(row.get("shop_code")),
            partner_agency=_text(row.get("partner_agency")),
            agency=_text(row.get("agency")),
            row_fingerprint=row_fingerprint,
            items=[item],
            raw_payload={"source_row": row_number},
        )
        return order


class TikTokVideoPerformanceImporter(TikTokReportImporter):
    report_type = REPORT_VIDEO_ANALYTICS
    status = WAITING_FOR_VIDEO_ANALYTICS

    def parse_file(self, path: str | Path) -> ParsedTikTokReport:
        raise NotImplementedError(WAITING_FOR_VIDEO_ANALYTICS)


class TikTokProductPerformanceImporter(TikTokReportImporter):
    report_type = "TIKTOK_PRODUCT_PERFORMANCE"

    def parse_file(self, path: str | Path) -> ParsedTikTokReport:
        raise NotImplementedError("Formato oficial ainda nao fornecido")


class TikTokEarningsImporter(TikTokReportImporter):
    report_type = "TIKTOK_EARNINGS"

    def parse_file(self, path: str | Path) -> ParsedTikTokReport:
        raise NotImplementedError("Formato oficial ainda nao fornecido")


class TikTokReportProvider(AffiliateDataProvider):
    provider_name = PROVIDER_TIKTOK_REPORT

    def __init__(self, importer: TikTokAffiliateOrdersImporter | None = None) -> None:
        self.importer = importer or TikTokAffiliateOrdersImporter()

    def normalize_order(self, payload: dict[str, Any]) -> NormalizedAffiliateOrder:
        return self.importer._normalize_row(payload, payload, 0)

    def parse_file(self, path: str | Path) -> ParsedTikTokReport:
        return self.importer.parse_file(path)

    def ingest_report(
        self,
        conn: sqlite3.Connection,
        report: ParsedTikTokReport,
        batch_id: int,
    ) -> dict[str, int]:
        return persist_report_orders(conn, report.orders, batch_id)

    def ingest_orders(
        self,
        conn: sqlite3.Connection,
        payloads: Iterable[dict[str, Any]],
        creator_account_id: int | None = None,
    ) -> dict[str, int]:
        orders = [self.normalize_order(payload) for payload in payloads]
        return persist_report_orders(conn, orders, batch_id=0)


class ReportValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def persist_report_orders(
    conn: sqlite3.Connection,
    orders: Iterable[NormalizedAffiliateOrder],
    batch_id: int,
) -> dict[str, int]:
    stats = {
        "rows_new": 0,
        "rows_updated": 0,
        "rows_unchanged": 0,
        "orders_new": 0,
        "orders_updated": 0,
        "products_matched": 0,
        "products_unmatched": 0,
        "contents_new": 0,
        "contents_matched": 0,
        "contents_unmatched": 0,
    }
    affected_content_ids: set[int] = set()
    for order in orders:
        timestamp = now_iso()
        existing = conn.execute(
            "SELECT * FROM affiliate_orders WHERE provider = ? AND external_order_id = ?",
            (PROVIDER_TIKTOK_REPORT, order.external_order_id),
        ).fetchone()
        resolved_items: list[dict[str, Any]] = []
        for item in order.items:
            resolved = _resolve_report_links(conn, item, order)
            resolved_items.append(resolved)
            if resolved["product_id"]:
                stats["products_matched"] += 1
            else:
                stats["products_unmatched"] += 1
            content_result = _upsert_external_content(conn, resolved, order, timestamp)
            stats["contents_new"] += content_result["new"]
            if item.normalized_content_type == CONTENT_VIDEO:
                if resolved["content_id"]:
                    stats["contents_matched"] += 1
                    affected_content_ids.add(int(resolved["content_id"]))
                else:
                    stats["contents_unmatched"] += 1

        order_fingerprint = stable_hash(
            [
                order.external_order_id,
                order.settlement_status,
                order.commission_settled_at,
                sorted(item["row_fingerprint"] or "" for item in resolved_items),
            ]
        )
        is_new = existing is None
        unchanged = bool(existing and existing["row_fingerprint"] == order_fingerprint)
        if is_new:
            stats["orders_new"] += 1
        elif not unchanged:
            stats["orders_updated"] += 1
        stats["rows_unchanged" if unchanged else ("rows_new" if is_new else "rows_updated")] += len(
            resolved_items
        )
        totals = _exact_order_totals(resolved_items)
        product_ids = {item["product_id"] for item in resolved_items if item["product_id"]}
        content_ids = {item["content_id"] for item in resolved_items if item["content_id"]}
        conn.execute(
            """
            INSERT INTO affiliate_orders (
                provider, external_order_id, product_id, content_id,
                external_product_id, order_status, order_created_at,
                order_updated_at, quantity, currency, commission_status,
                raw_payload, first_seen_at, last_seen_at, created_at, updated_at,
                external_order_type, normalized_order_type,
                external_settlement_status, settlement_status,
                commission_settled_at, indirect, commission_type,
                shop_name, shop_code, partner_agency, agency,
                gmv_minor, estimated_commission_minor, actual_commission_minor,
                final_received_minor, row_fingerprint, last_import_batch_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, external_order_id) DO UPDATE SET
                product_id = excluded.product_id, content_id = excluded.content_id,
                external_product_id = excluded.external_product_id,
                order_status = excluded.order_status,
                order_created_at = excluded.order_created_at,
                order_updated_at = excluded.order_updated_at,
                quantity = excluded.quantity, currency = excluded.currency,
                commission_status = excluded.commission_status,
                raw_payload = excluded.raw_payload, last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at,
                external_order_type = excluded.external_order_type,
                normalized_order_type = excluded.normalized_order_type,
                external_settlement_status = excluded.external_settlement_status,
                settlement_status = excluded.settlement_status,
                commission_settled_at = excluded.commission_settled_at,
                indirect = excluded.indirect, commission_type = excluded.commission_type,
                shop_name = excluded.shop_name, shop_code = excluded.shop_code,
                partner_agency = excluded.partner_agency, agency = excluded.agency,
                gmv_minor = excluded.gmv_minor,
                estimated_commission_minor = excluded.estimated_commission_minor,
                actual_commission_minor = excluded.actual_commission_minor,
                final_received_minor = excluded.final_received_minor,
                row_fingerprint = excluded.row_fingerprint,
                last_import_batch_id = excluded.last_import_batch_id
            """,
            (
                PROVIDER_TIKTOK_REPORT,
                order.external_order_id,
                next(iter(product_ids)) if len(product_ids) == 1 else None,
                next(iter(content_ids)) if len(content_ids) == 1 else None,
                _single_value(item["external_product_id"] for item in resolved_items),
                order.settlement_status,
                order.order_created_at,
                order.commission_settled_at or order.order_created_at,
                totals["items_sold"],
                _single_value(item["currency"] for item in resolved_items) or "BRL",
                order.settlement_status,
                json.dumps(order.raw_payload, ensure_ascii=False),
                timestamp,
                timestamp,
                timestamp,
                timestamp,
                order.external_order_type,
                order.normalized_order_type,
                order.external_settlement_status,
                order.settlement_status,
                order.commission_settled_at,
                order.indirect,
                order.commission_type,
                order.shop_name,
                order.shop_code,
                order.partner_agency,
                order.agency,
                totals["gmv_minor"],
                totals["estimated_commission_minor"],
                totals["actual_commission_minor"],
                totals["final_received_minor"],
                order_fingerprint,
                batch_id or None,
            ),
        )
        order_row = conn.execute(
            "SELECT id FROM affiliate_orders WHERE provider = ? AND external_order_id = ?",
            (PROVIDER_TIKTOK_REPORT, order.external_order_id),
        ).fetchone()
        order_db_id = int(order_row["id"])
        if is_new or existing["settlement_status"] != order.settlement_status:
            conn.execute(
                """
                INSERT INTO affiliate_order_status_history (
                    affiliate_order_id, previous_status, new_status, changed_at, raw_payload
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    order_db_id,
                    existing["settlement_status"] if existing else None,
                    order.settlement_status,
                    timestamp,
                    json.dumps(
                        {"external_status": order.external_settlement_status}, ensure_ascii=False
                    ),
                ),
            )
        _upsert_report_items(conn, order_db_id, resolved_items, timestamp)

    refresh_report_content_performance(conn, affected_content_ids)
    conn.commit()
    return stats


def reconcile_external_content_id(
    conn: sqlite3.Connection,
    external_content_id: str,
    content_id: int,
    *,
    manual: bool = True,
) -> int:
    external_id = _required_id(external_content_id, "EMPTY_CONTENT_ID")
    content = conn.execute(
        "SELECT id, product_id FROM content_items WHERE id = ?", (content_id,)
    ).fetchone()
    if not content:
        raise ValueError("Content interno nao encontrado")
    timestamp = now_iso()
    attribution_type = "MANUAL" if manual else "OFFICIAL"
    attribution_method = "MANUAL_ASSOCIATION" if manual else "DIRECT_CONTENT_ID"
    conn.execute(
        """
        INSERT INTO external_tiktok_contents (
            external_content_id, external_content_type, content_id, product_id,
            status, attribution_type, attribution_method, confidence,
            created_at, updated_at
        ) VALUES (?, 'VIDEO', ?, ?, 'MATCHED', ?, ?, '1.0', ?, ?)
        ON CONFLICT(external_content_id, external_content_type) DO UPDATE SET
            content_id = excluded.content_id, product_id = excluded.product_id,
            status = 'MATCHED', attribution_type = excluded.attribution_type,
            attribution_method = excluded.attribution_method,
            confidence = '1.0', updated_at = excluded.updated_at
        """,
        (
            external_id,
            content_id,
            content["product_id"],
            attribution_type,
            attribution_method,
            timestamp,
            timestamp,
        ),
    )
    cursor = conn.execute(
        """
        UPDATE affiliate_order_items
        SET content_id = ?, attribution_type = ?, attribution_method = ?,
            attribution_confidence = '1.0', updated_at = ?
        WHERE external_content_id = ? AND normalized_content_type = 'VIDEO'
        """,
        (content_id, attribution_type, attribution_method, timestamp, external_id),
    )
    conn.commit()
    refresh_report_content_performance(conn, {content_id})
    conn.commit()
    return int(cursor.rowcount)


def classify_external_content(
    conn: sqlite3.Connection,
    external_content_id: str,
    status: str,
    notes: str | None = None,
) -> None:
    if status not in {"LEGACY", "IGNORED"}:
        raise ValueError("Status externo invalido")
    conn.execute(
        """
        UPDATE external_tiktok_contents
        SET status = ?, notes = ?, updated_at = ?
        WHERE external_content_id = ? AND external_content_type = 'VIDEO'
        """,
        (status, notes, now_iso(), str(external_content_id)),
    )
    conn.commit()


def reconcile_external_product(
    conn: sqlite3.Connection,
    product_id: int,
    external_product_id: str,
) -> int:
    external_id = _required_id(external_product_id, "EMPTY_PRODUCT_ID")
    product = conn.execute("SELECT id FROM products WHERE id = ?", (product_id,)).fetchone()
    if not product:
        raise ValueError("Produto local nao encontrado")
    conflicts = conn.execute(
        """
        SELECT DISTINCT product_id FROM product_external_references
        WHERE provider = 'TIKTOK_SHOP' AND external_product_id = ?
          AND product_id <> ?
        """,
        (external_id, product_id),
    ).fetchall()
    if conflicts:
        raise ValueError("Produto TikTok ja associado a outro produto local")

    timestamp = now_iso()
    references = conn.execute(
        """
        SELECT DISTINCT i.external_sku_id, i.product_name, o.shop_name, o.shop_code
        FROM affiliate_order_items i
        JOIN affiliate_orders o ON o.id = i.affiliate_order_id
        WHERE o.provider = ? AND i.external_product_id = ?
        """,
        (PROVIDER_TIKTOK_REPORT, external_id),
    ).fetchall()
    for reference in references:
        conn.execute(
            """
            INSERT INTO product_external_references (
                product_id, provider, external_product_id, external_sku_id,
                external_product_name, shop_name, shop_code, match_method,
                created_at, updated_at
            ) VALUES (?, 'TIKTOK_SHOP', ?, ?, ?, ?, ?, 'MANUAL', ?, ?)
            ON CONFLICT(provider, external_product_id, external_sku_id) DO UPDATE SET
                product_id = excluded.product_id,
                external_product_name = excluded.external_product_name,
                shop_name = excluded.shop_name, shop_code = excluded.shop_code,
                match_method = 'MANUAL', updated_at = excluded.updated_at
            """,
            (
                product_id,
                external_id,
                reference["external_sku_id"],
                reference["product_name"],
                reference["shop_name"],
                reference["shop_code"],
                timestamp,
                timestamp,
            ),
        )
    cursor = conn.execute(
        """
        UPDATE affiliate_order_items
        SET product_id = ?, product_match_method = 'MANUAL', updated_at = ?
        WHERE external_product_id = ?
        """,
        (product_id, timestamp, external_id),
    )
    conn.execute(
        """
        UPDATE external_tiktok_contents
        SET product_id = ?, updated_at = ? WHERE external_product_id = ?
        """,
        (product_id, timestamp, external_id),
    )
    conn.commit()
    return int(cursor.rowcount)


def refresh_report_content_performance(
    conn: sqlite3.Connection, content_ids: Iterable[int]
) -> None:
    for content_id in set(int(value) for value in content_ids):
        aggregate = conn.execute(
            """
            SELECT COUNT(DISTINCT o.id) AS orders,
                   COALESCE(SUM(i.items_sold), 0) AS units_sold,
                   COALESCE(SUM(i.items_refunded), 0) AS items_refunded,
                   COALESCE(SUM(i.gmv_minor), 0) AS gmv_minor,
                   COALESCE(SUM(i.estimated_commission_minor), 0) AS estimated_minor,
                   COALESCE(SUM(i.actual_commission_minor), 0) AS actual_minor,
                   COALESCE(SUM(i.final_received_minor), 0) AS final_minor,
                   MAX(COALESCE(o.commission_settled_at, o.order_created_at)) AS source_timestamp,
                   MIN(CASE WHEN i.attribution_type = 'MANUAL' THEN 0 ELSE 1 END) AS all_official
            FROM affiliate_order_items i
            JOIN affiliate_orders o ON o.id = i.affiliate_order_id
            WHERE o.provider = ? AND i.content_id = ?
            """,
            (PROVIDER_TIKTOK_REPORT, content_id),
        ).fetchone()
        source_hash = stable_hash(
            [
                PROVIDER_TIKTOK_REPORT,
                content_id,
                aggregate["orders"],
                aggregate["units_sold"],
                aggregate["items_refunded"],
                aggregate["gmv_minor"],
                aggregate["estimated_minor"],
                aggregate["actual_minor"],
                aggregate["final_minor"],
                aggregate["source_timestamp"],
            ]
        )
        if conn.execute(
            "SELECT 1 FROM content_performance WHERE provider = ? AND source_row_hash = ?",
            (PROVIDER_TIKTOK_REPORT, source_hash),
        ).fetchone():
            continue
        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO content_performance (
                content_id, captured_at, orders, units_sold, gmv, commission,
                source, source_row_hash, created_at, provider, source_timestamp,
                synced_at, estimated_commission, actual_commission, refunds,
                attribution_status, gmv_minor, estimated_commission_minor,
                actual_commission_minor, final_received_minor, refunds_minor
            ) VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                content_id,
                timestamp,
                aggregate["orders"],
                aggregate["units_sold"],
                PROVIDER_TIKTOK_REPORT,
                source_hash,
                timestamp,
                PROVIDER_TIKTOK_REPORT,
                aggregate["source_timestamp"],
                timestamp,
                aggregate["items_refunded"],
                "OFFICIAL" if aggregate["all_official"] else "MANUAL",
                aggregate["gmv_minor"],
                aggregate["estimated_minor"],
                aggregate["actual_minor"],
                aggregate["final_minor"],
            ),
        )


def parse_decimal_br(
    value: Any,
    *,
    required: bool = False,
    error_code: str = "INVALID_DECIMAL",
) -> Decimal | None:
    text = _text(value)
    if not text or text == "/":
        if required:
            raise ReportValidationError(error_code, "Valor financeiro obrigatorio ausente")
        return None
    normalized = text.replace("R$", "").replace(" ", "")
    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation as exc:
        raise ReportValidationError(error_code, f"Decimal invalido: {text}") from exc


def parse_integer(value: Any, error_code: str) -> int:
    decimal = parse_decimal_br(value)
    if decimal is None:
        return 0
    if decimal != decimal.to_integral_value():
        raise ReportValidationError(error_code, f"Inteiro invalido: {value}")
    return int(decimal)


def parse_report_datetime(
    value: Any,
    account_timezone: Any,
    *,
    required: bool = False,
) -> str | None:
    text = _text(value)
    if not text or text == "/":
        if required:
            raise ReportValidationError("INVALID_ORDER_DATE", "Data obrigatoria ausente")
        return None
    for pattern in ("%d/%m/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            parsed = datetime.strptime(text, pattern).replace(tzinfo=account_timezone)
            return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()
        except ValueError:
            continue
    raise ReportValidationError("INVALID_ORDER_DATE", f"Data invalida: {text}")


def normalize_content_type(value: Any) -> str:
    normalized = normalize_header(value)
    return {
        "video": CONTENT_VIDEO,
        "vitrine": CONTENT_SHOWCASE,
        "showcase": CONTENT_SHOWCASE,
        "live": CONTENT_LIVE,
    }.get(normalized, CONTENT_UNKNOWN)


def normalize_settlement_status(value: Any) -> str:
    normalized = normalize_header(value)
    return {
        "liquidado": SETTLED,
        "settled": SETTLED,
        "inelegivel": INELIGIBLE,
        "ineligible": INELIGIBLE,
        "pendente": PENDING,
        "pending": PENDING,
        "awaitingpayment": AWAITING_PAYMENT,
        "awaiting payment": AWAITING_PAYMENT,
    }.get(normalized, SETTLEMENT_UNKNOWN)


def normalize_order_type(value: Any) -> str:
    normalized = normalize_header(value)
    if normalized in {"pedido de anuncios da loja", "shop ads order"}:
        return SHOP_AD_ORDER
    if normalized in {"pedido de afiliados", "affiliate order"}:
        return AFFILIATE_ORDER
    return OTHER_ORDER


def normalize_header(value: Any) -> str:
    text = str(value or "").replace("\ufeff", " ")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-zA-Z0-9]+", " ", text).lower().split())


def detect_report_file(path: str | Path) -> TikTokReportDetection:
    headers, _ = _read_report_rows(Path(path))
    return TikTokReportDetector().detect(headers)


def _read_report_rows(path: Path) -> tuple[list[str], list[tuple[int, dict[str, Any]]]]:
    if path.suffix.lower() == ".xlsx":
        for sheet in read_xlsx(path).values():
            if not sheet.rows:
                continue
            header_index = _best_header_index(sheet.rows)
            if header_index is None:
                continue
            headers = [str(value or "").strip() for value in sheet.rows[header_index]]
            rows = []
            for offset, values in enumerate(sheet.rows[header_index + 1 :], start=header_index + 2):
                raw = {
                    header: values[index] if index < len(values) else ""
                    for index, header in enumerate(headers)
                    if header
                }
                if any(_text(value) for value in raw.values()):
                    rows.append((offset, raw))
            return headers, rows
        raise ValueError("Nenhuma tabela reconhecida no XLSX")
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(handle, dialect=dialect)
            headers = [str(value or "").strip() for value in (reader.fieldnames or [])]
            return headers, [(index, dict(row)) for index, row in enumerate(reader, start=2)]
    raise ValueError("Formato suportado: XLSX ou CSV")


def _best_header_index(rows: list[list[Any]]) -> int | None:
    detector = TikTokReportDetector()
    candidates: list[tuple[int, int]] = []
    for index, row in enumerate(rows[:25]):
        detection = detector.detect(row)
        score = len(detection.mapped_headers) - len(detection.missing_required) * 10
        candidates.append((score, index))
    score, index = max(candidates, default=(-1, 0))
    return index if score > 0 else None


def _group_report_orders(
    parsed_rows: list[tuple[int, NormalizedAffiliateOrder]],
) -> list[NormalizedAffiliateOrder]:
    grouped: dict[str, NormalizedAffiliateOrder] = {}
    item_keys: dict[str, set[str]] = {}
    for row_number, order in parsed_rows:
        current = grouped.get(order.external_order_id)
        if current is None:
            grouped[order.external_order_id] = order
            item_keys[order.external_order_id] = set()
            current = order
        item = order.items[0]
        key = item.item_key
        if key in item_keys[order.external_order_id]:
            key = f"{key}:{item.row_fingerprint[:12]}"
            item.item_key = key
        item_keys[order.external_order_id].add(key)
        if current is not order:
            current.items.append(item)
        current.raw_payload = {
            "source_rows": len(current.items),
            "first_row": min(row_number, int(current.raw_payload.get("first_row") or row_number)),
        }
    return list(grouped.values())


def _resolve_report_links(
    conn: sqlite3.Connection,
    item: NormalizedAffiliateItem,
    order: NormalizedAffiliateOrder,
) -> dict[str, Any]:
    result = item.__dict__.copy()
    product = None
    method = None
    if item.external_product_id:
        product = conn.execute(
            "SELECT id FROM products WHERE tiktok_product_id = ?", (item.external_product_id,)
        ).fetchone()
        method = "TIKTOK_PRODUCT_ID" if product else None
    if product is None and item.external_sku_id:
        product = conn.execute(
            "SELECT id FROM products WHERE tiktok_sku_id = ?", (item.external_sku_id,)
        ).fetchone()
        method = "TIKTOK_SKU_ID" if product else None
    if product is None:
        reference = conn.execute(
            """
            SELECT product_id, match_method FROM product_external_references
            WHERE provider = 'TIKTOK_SHOP' AND (
                (? IS NOT NULL AND external_product_id = ?) OR
                (? IS NOT NULL AND external_sku_id = ?)
            ) ORDER BY CASE WHEN external_product_id = ? THEN 0 ELSE 1 END LIMIT 1
            """,
            (
                item.external_product_id,
                item.external_product_id,
                item.external_sku_id,
                item.external_sku_id,
                item.external_product_id,
            ),
        ).fetchone()
        if reference:
            product = {"id": reference["product_id"]}
            method = reference["match_method"] or "EXTERNAL_REFERENCE"
    if product is None and item.product_name:
        matches = conn.execute(
            "SELECT id FROM products WHERE product_key = ?", (product_key(item.product_name),)
        ).fetchall()
        if len(matches) == 1:
            product = matches[0]
            method = "NORMALIZED_NAME_FALLBACK"

    product_id = int(product["id"]) if product else None
    if product_id:
        conn.execute(
            """
            INSERT INTO product_external_references (
                product_id, provider, external_product_id, external_sku_id,
                external_product_name, shop_name, shop_code, match_method,
                created_at, updated_at
            ) VALUES (?, 'TIKTOK_SHOP', ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, external_product_id, external_sku_id) DO UPDATE SET
                external_product_name = excluded.external_product_name,
                shop_name = excluded.shop_name, shop_code = excluded.shop_code,
                updated_at = excluded.updated_at
            """,
            (
                product_id,
                item.external_product_id,
                item.external_sku_id,
                item.product_name,
                order.shop_name,
                order.shop_code,
                method,
                now_iso(),
                now_iso(),
            ),
        )

    content_id = None
    attribution_type = "UNATTRIBUTED"
    attribution_method = None
    confidence = "0"
    if item.normalized_content_type == CONTENT_VIDEO and item.external_content_id:
        mapping = conn.execute(
            """
            SELECT content_id, attribution_type, attribution_method, confidence
            FROM external_tiktok_contents
            WHERE external_content_id = ? AND external_content_type = 'VIDEO'
              AND content_id IS NOT NULL
            """,
            (item.external_content_id,),
        ).fetchone()
        if mapping:
            content_id = int(mapping["content_id"])
            attribution_type = mapping["attribution_type"]
            attribution_method = mapping["attribution_method"]
            confidence = mapping["confidence"]
        else:
            content = conn.execute(
                "SELECT id FROM content_items WHERE tiktok_video_id = ?",
                (item.external_content_id,),
            ).fetchone()
            if content:
                content_id = int(content["id"])
                attribution_type = "OFFICIAL"
                attribution_method = "DIRECT_CONTENT_ID"
                confidence = "1.0"
    result.update(
        {
            "product_id": product_id,
            "product_match_method": method,
            "content_id": content_id,
            "attribution_type": attribution_type,
            "attribution_method": attribution_method,
            "attribution_confidence": confidence,
        }
    )
    return result


def _upsert_external_content(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    order: NormalizedAffiliateOrder,
    timestamp: str,
) -> dict[str, int]:
    external_id = item.get("external_content_id")
    if not external_id:
        return {"new": 0}
    content_type = item.get("normalized_content_type") or CONTENT_UNKNOWN
    existing = conn.execute(
        "SELECT * FROM external_tiktok_contents WHERE external_content_id = ? AND external_content_type = ?",
        (external_id, content_type),
    ).fetchone()
    status = (
        "MATCHED"
        if item.get("content_id")
        else "SHOWCASE"
        if content_type == CONTENT_SHOWCASE
        else "LIVE"
        if content_type == CONTENT_LIVE
        else "UNMATCHED"
    )
    if existing and existing["status"] in {"LEGACY", "IGNORED"}:
        status = existing["status"]
    conn.execute(
        """
        INSERT INTO external_tiktok_contents (
            external_content_id, external_content_type, content_id, product_id,
            external_product_id, external_product_name, status, attribution_type,
            attribution_method, confidence, first_sale_at, last_sale_at,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_content_id, external_content_type) DO UPDATE SET
            content_id = CASE
                WHEN external_tiktok_contents.attribution_type = 'MANUAL'
                THEN external_tiktok_contents.content_id ELSE excluded.content_id END,
            product_id = COALESCE(external_tiktok_contents.product_id, excluded.product_id),
            external_product_id = excluded.external_product_id,
            external_product_name = excluded.external_product_name,
            status = CASE WHEN external_tiktok_contents.status IN ('LEGACY', 'IGNORED')
                THEN external_tiktok_contents.status ELSE excluded.status END,
            attribution_type = CASE WHEN external_tiktok_contents.attribution_type = 'MANUAL'
                THEN 'MANUAL' ELSE excluded.attribution_type END,
            attribution_method = CASE WHEN external_tiktok_contents.attribution_type = 'MANUAL'
                THEN external_tiktok_contents.attribution_method ELSE excluded.attribution_method END,
            confidence = CASE WHEN external_tiktok_contents.attribution_type = 'MANUAL'
                THEN external_tiktok_contents.confidence ELSE excluded.confidence END,
            first_sale_at = MIN(external_tiktok_contents.first_sale_at, excluded.first_sale_at),
            last_sale_at = MAX(external_tiktok_contents.last_sale_at, excluded.last_sale_at),
            updated_at = excluded.updated_at
        """,
        (
            external_id,
            content_type,
            item.get("content_id"),
            item.get("product_id"),
            item.get("external_product_id"),
            item.get("product_name"),
            status,
            item.get("attribution_type") or "UNATTRIBUTED",
            item.get("attribution_method"),
            str(item.get("attribution_confidence") or "0"),
            order.order_created_at,
            order.order_created_at,
            timestamp,
            timestamp,
        ),
    )
    return {"new": 0 if existing else 1}


def _upsert_report_items(
    conn: sqlite3.Connection,
    order_id: int,
    items: list[dict[str, Any]],
    timestamp: str,
) -> None:
    keys: list[str] = []
    for item in items:
        keys.append(item["item_key"])
        conn.execute(
            """
            INSERT INTO affiliate_order_items (
                affiliate_order_id, item_key, external_sku_id, product_id,
                external_product_id, content_id, external_content_id, content_type,
                product_name, quantity, returned_quantity, refunded_quantity,
                currency, raw_payload, created_at, updated_at,
                items_sold, items_refunded, normalized_content_type,
                product_match_method, attribution_type, attribution_method,
                attribution_confidence, unit_price_minor, gmv_minor,
                standard_rate, shop_ads_rate, tiktok_bonus_rate,
                partner_bonus_rate, revenue_share_rate,
                estimated_commission_base_minor,
                estimated_standard_commission_minor,
                estimated_shop_ads_commission_minor, estimated_tiktok_bonus_minor,
                estimated_partner_bonus_minor, estimated_partner_agency_bonus_minor,
                estimated_revenue_share_minor, estimated_tax_vat_minor,
                estimated_tax_isr_minor, estimated_tax_cedular_minor,
                estimated_tax_pit_minor, actual_commission_base_minor,
                actual_standard_commission_minor, actual_shop_ads_commission_minor,
                actual_bonus_minor, actual_partner_agency_bonus_minor,
                actual_tax_isr_minor, actual_tax_vat_minor,
                actual_tax_cedular_minor, actual_tax_pit_minor,
                actual_partner_share_minor, estimated_commission_minor,
                actual_commission_minor, final_received_minor, row_fingerprint
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(affiliate_order_id, item_key) DO UPDATE SET
                external_sku_id = excluded.external_sku_id,
                product_id = CASE WHEN affiliate_order_items.product_match_method = 'MANUAL'
                    THEN affiliate_order_items.product_id ELSE excluded.product_id END,
                external_product_id = excluded.external_product_id,
                content_id = CASE WHEN affiliate_order_items.attribution_type = 'MANUAL'
                    THEN affiliate_order_items.content_id ELSE excluded.content_id END,
                external_content_id = excluded.external_content_id,
                content_type = excluded.content_type,
                product_name = excluded.product_name, quantity = excluded.quantity,
                returned_quantity = excluded.returned_quantity,
                refunded_quantity = excluded.refunded_quantity,
                currency = excluded.currency, raw_payload = excluded.raw_payload,
                items_sold = excluded.items_sold,
                items_refunded = excluded.items_refunded,
                normalized_content_type = excluded.normalized_content_type,
                product_match_method = CASE WHEN affiliate_order_items.product_match_method = 'MANUAL'
                    THEN 'MANUAL' ELSE excluded.product_match_method END,
                attribution_type = CASE WHEN affiliate_order_items.attribution_type = 'MANUAL'
                    THEN 'MANUAL' ELSE excluded.attribution_type END,
                attribution_method = CASE WHEN affiliate_order_items.attribution_type = 'MANUAL'
                    THEN affiliate_order_items.attribution_method ELSE excluded.attribution_method END,
                attribution_confidence = CASE WHEN affiliate_order_items.attribution_type = 'MANUAL'
                    THEN affiliate_order_items.attribution_confidence ELSE excluded.attribution_confidence END,
                unit_price_minor = excluded.unit_price_minor, gmv_minor = excluded.gmv_minor,
                standard_rate = excluded.standard_rate, shop_ads_rate = excluded.shop_ads_rate,
                tiktok_bonus_rate = excluded.tiktok_bonus_rate,
                partner_bonus_rate = excluded.partner_bonus_rate,
                revenue_share_rate = excluded.revenue_share_rate,
                estimated_commission_base_minor = excluded.estimated_commission_base_minor,
                estimated_standard_commission_minor = excluded.estimated_standard_commission_minor,
                estimated_shop_ads_commission_minor = excluded.estimated_shop_ads_commission_minor,
                estimated_tiktok_bonus_minor = excluded.estimated_tiktok_bonus_minor,
                estimated_partner_bonus_minor = excluded.estimated_partner_bonus_minor,
                estimated_partner_agency_bonus_minor = excluded.estimated_partner_agency_bonus_minor,
                estimated_revenue_share_minor = excluded.estimated_revenue_share_minor,
                estimated_tax_vat_minor = excluded.estimated_tax_vat_minor,
                estimated_tax_isr_minor = excluded.estimated_tax_isr_minor,
                estimated_tax_cedular_minor = excluded.estimated_tax_cedular_minor,
                estimated_tax_pit_minor = excluded.estimated_tax_pit_minor,
                actual_commission_base_minor = excluded.actual_commission_base_minor,
                actual_standard_commission_minor = excluded.actual_standard_commission_minor,
                actual_shop_ads_commission_minor = excluded.actual_shop_ads_commission_minor,
                actual_bonus_minor = excluded.actual_bonus_minor,
                actual_partner_agency_bonus_minor = excluded.actual_partner_agency_bonus_minor,
                actual_tax_isr_minor = excluded.actual_tax_isr_minor,
                actual_tax_vat_minor = excluded.actual_tax_vat_minor,
                actual_tax_cedular_minor = excluded.actual_tax_cedular_minor,
                actual_tax_pit_minor = excluded.actual_tax_pit_minor,
                actual_partner_share_minor = excluded.actual_partner_share_minor,
                estimated_commission_minor = excluded.estimated_commission_minor,
                actual_commission_minor = excluded.actual_commission_minor,
                final_received_minor = excluded.final_received_minor,
                row_fingerprint = excluded.row_fingerprint, updated_at = excluded.updated_at
            """,
            (
                order_id,
                item["item_key"],
                item.get("external_sku_id"),
                item.get("product_id"),
                item.get("external_product_id"),
                item.get("content_id"),
                item.get("external_content_id"),
                item.get("content_type"),
                item.get("product_name"),
                item.get("quantity") or 0,
                item.get("returned_quantity") or 0,
                item.get("refunded_quantity") or 0,
                item.get("currency") or "BRL",
                json.dumps(item.get("raw_payload") or {}, ensure_ascii=False),
                timestamp,
                timestamp,
                int(item.get("quantity") or 0),
                int(item.get("refunded_quantity") or 0),
                item.get("normalized_content_type") or CONTENT_UNKNOWN,
                item.get("product_match_method"),
                item.get("attribution_type") or "UNATTRIBUTED",
                item.get("attribution_method"),
                str(item.get("attribution_confidence") or "0"),
                to_minor(item.get("unit_price")),
                to_minor(item.get("gmv")),
                item.get("standard_rate"),
                item.get("shop_ads_rate"),
                item.get("tiktok_bonus_rate"),
                item.get("partner_bonus_rate"),
                item.get("revenue_share_rate"),
                to_minor(item.get("estimated_commission_base")),
                to_minor(item.get("estimated_standard_commission")),
                to_minor(item.get("estimated_shop_ads_commission")),
                to_minor(item.get("estimated_tiktok_bonus")),
                to_minor(item.get("estimated_partner_bonus")),
                to_minor(item.get("estimated_partner_agency_bonus")),
                to_minor(item.get("estimated_revenue_share")),
                to_minor(item.get("estimated_tax_vat")),
                to_minor(item.get("estimated_tax_isr")),
                to_minor(item.get("estimated_tax_cedular")),
                to_minor(item.get("estimated_tax_pit")),
                to_minor(item.get("actual_commission_base")),
                to_minor(item.get("actual_standard_commission")),
                to_minor(item.get("actual_shop_ads_commission")),
                to_minor(item.get("actual_bonus")),
                to_minor(item.get("actual_partner_agency_bonus")),
                to_minor(item.get("actual_tax_isr")),
                to_minor(item.get("actual_tax_vat")),
                to_minor(item.get("actual_tax_cedular")),
                to_minor(item.get("actual_tax_pit")),
                to_minor(item.get("actual_partner_share")),
                to_minor(item.get("estimated_commission")),
                to_minor(item.get("actual_commission")),
                to_minor(item.get("final_received_amount")),
                item.get("row_fingerprint"),
            ),
        )
    if keys:
        placeholders = ",".join("?" for _ in keys)
        conn.execute(
            f"DELETE FROM affiliate_order_items WHERE affiliate_order_id = ? AND item_key NOT IN ({placeholders})",
            (order_id, *keys),
        )


def _exact_order_totals(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "items_sold": sum(int(item.get("quantity") or 0) for item in items),
        "gmv_minor": sum(to_minor(item.get("gmv")) or 0 for item in items),
        "estimated_commission_minor": sum(
            to_minor(item.get("estimated_commission")) or 0 for item in items
        ),
        "actual_commission_minor": sum(
            to_minor(item.get("actual_commission")) or 0 for item in items
        ),
        "final_received_minor": sum(
            to_minor(item.get("final_received_amount")) or 0 for item in items
        ),
    }


def to_minor(value: Decimal | None) -> int | None:
    if value is None:
        return None
    return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _sum_components(values: Iterable[Decimal | None]) -> Decimal | None:
    present = [value for value in values if value is not None]
    return sum(present, Decimal("0")) if present else None


def _single_value(values: Iterable[Any]) -> Any | None:
    distinct = {value for value in values if value not in (None, "")}
    return next(iter(distinct)) if len(distinct) == 1 else None


def _required_id(value: Any, code: str) -> str:
    text = _text(value)
    if not text:
        raise ReportValidationError(code, "Identificador obrigatorio ausente")
    return text


def _text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value).strip()


def _json_safe_row(raw: dict[str, Any]) -> dict[str, Any]:
    return {str(key): None if value is None else str(value) for key, value in raw.items()}
