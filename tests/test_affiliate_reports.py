from __future__ import annotations

import html
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.affiliate_analytics import affiliate_daily_operations, content_reconciliation
from src.affiliate_reports import AffiliateReportConfig, AffiliateReportImporter
from src.store import connect, init_db, now_iso
from src.tiktok_reports import (
    COMPATIBLE,
    COMPATIBLE_WITH_NEW_COLUMNS,
    CONTENT_LIVE,
    CONTENT_SHOWCASE,
    CONTENT_VIDEO,
    REPORT_AFFILIATE_ORDERS,
    TikTokAffiliateOrdersImporter,
    TikTokVideoPerformanceImporter,
    classify_external_content,
    detect_report_file,
    normalize_settlement_status,
    parse_decimal_br,
    reconcile_external_content_id,
)


HEADERS = [
    "ID do pedido",
    "ID do SKU",
    "Nome do produto",
    "ID do produto",
    "Preço",
    "Itens vendidos",
    "Itens reembolsados",
    "Nome da loja",
    "Código da loja",
    "Agência parceira",
    "Agência",
    "Moeda",
    "Tipo de pedido",
    "Status de liquidação do pedido",
    "Indireta",
    "Tipo de comissão",
    "Tipo de conteúdo",
    "ID do conteúdo",
    "Padrão",
    "Anúncios da loja",
    "Bônus do TikTok",
    "Bônus do parceiro",
    "Participação na receita\n\n",
    "GMV",
    "Base de comissão estimada",
    "Comissão padrão estimada",
    "Comissão estimada de anúncios da loja",
    "Bônus estimado",
    "Bônus estimado da agência parceira",
    "IVA estimado",
    "ISR estimado",
    "Est. CedularTax",
    "PIT estimado",
    "Estimativa da participação na receita\n\n",
    "Base de comissão real",
    "Comissão padrão",
    "Comissão de anúncios da loja",
    "Bônus",
    "Bônus da agência parceira",
    "Imposto - ISR",
    "Imposto - IVA",
    "cedular_tax",
    "Imposto - PIT",
    "Compartilhado com parceiro",
    "Valor total final recebido",
    "Data do pedido",
    "Data de liquidação da comissão",
]

ENGLISH_HEADERS = [
    "Order ID", "SKU ID", "Product name", "Product ID", "Price", "Items sold",
    "Items refunded", "Shop name", "Shop code", "Affiliate partner", "Agency",
    "Currency", "Order type", "Order settlement status", "Indirect", "Commission type",
    "Content Type", "Carousel", "Content ID", "Standard", "Shop ads", "TikTok bonus",
    "Partner bonus", "Revenue sharing portion", "GMV", "Est. commission base",
    "Est. standard commission", "Est. Shop Ads commission", "Est. Bonus",
    "Est. Affiliate partner bonus", "Est. IVA", "Est. ISR", "Est. CedularTax",
    "Est. PIT", "Est. revenue sharing portion", "Actual commission base",
    "Standard commission", "Shop Ads commission", "Bonus", "Affiliate partner bonus",
    "Tax - ISR", "Tax - IVA", "cedular_tax", "Tax - PIT", "Shared with partner",
    "Total final earned amount", "Order date", "Commission settlement date",
]


def report_row(**changes: str) -> dict[str, str]:
    row = {
        "ID do pedido": "7670243375139802375",
        "ID do SKU": "1770243375139802375",
        "Nome do produto": "Produto sanitizado",
        "ID do produto": "1720243375139802375",
        "Preço": "63,36",
        "Itens vendidos": "1",
        "Itens reembolsados": "0",
        "Nome da loja": "Loja de teste",
        "Código da loja": "7495123456789012345",
        "Agência parceira": "/",
        "Agência": "/",
        "Moeda": "BRL",
        "Tipo de pedido": "Pedido de anúncios da loja",
        "Status de liquidação do pedido": "Pendente",
        "Indireta": "Não",
        "Tipo de comissão": "Comissão padrão",
        "Tipo de conteúdo": "Vídeo",
        "ID do conteúdo": "7699243375139802375",
        "Padrão": "15%",
        "Anúncios da loja": "0%",
        "Bônus do TikTok": "0%",
        "Bônus do parceiro": "0%",
        "Participação na receita\n\n": "0%",
        "GMV": "63,36",
        "Base de comissão estimada": "63,36",
        "Comissão padrão estimada": "9,50",
        "Comissão estimada de anúncios da loja": "0,00",
        "Bônus estimado": "0,00",
        "Bônus estimado da agência parceira": "0,00",
        "IVA estimado": "0,00",
        "ISR estimado": "0,00",
        "Est. CedularTax": "0,00",
        "PIT estimado": "0,00",
        "Estimativa da participação na receita\n\n": "0,00",
        "Base de comissão real": "/",
        "Comissão padrão": "/",
        "Comissão de anúncios da loja": "/",
        "Bônus": "/",
        "Bônus da agência parceira": "/",
        "Imposto - ISR": "/",
        "Imposto - IVA": "/",
        "cedular_tax": "/",
        "Imposto - PIT": "/",
        "Compartilhado com parceiro": "/",
        "Valor total final recebido": "/",
        "Data do pedido": "08/08/2026 10:11:12",
        "Data de liquidação da comissão": "/",
    }
    row.update(changes)
    return row


class TikTokAffiliateReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.config = AffiliateReportConfig(
            import_dir=self.root,
            auto_import_enabled=True,
            interval_seconds=300,
            min_age_seconds=0,
            daily_lookback_days=30,
        )

    def test_detector_recognizes_real_47_column_format_and_normalized_headers(self) -> None:
        path = self.root / "real.xlsx"
        write_xlsx_report(path, [report_row()])
        result = detect_report_file(path)
        self.assertEqual(len(HEADERS), 47)
        self.assertEqual(result.report_type, REPORT_AFFILIATE_ORDERS)
        self.assertEqual(result.compatibility, COMPATIBLE)
        self.assertEqual(result.missing_required, ())

    def test_detector_accepts_official_english_format_with_carousel_column(self) -> None:
        path = self.root / "english.xlsx"
        source_values = list(report_row().values())
        translated = {
            header: "" if header == "Carousel" else source_values[index if index < 17 else index - 1]
            for index, header in enumerate(ENGLISH_HEADERS)
        }
        translated.update({
            "Carousel": "",
            "Order type": "Shop ads order",
            "Order settlement status": "Settled",
            "Content Type": "Video",
            "Order date": "08/08/2026 10:11:12",
        })
        write_xlsx_report(path, [translated], headers=ENGLISH_HEADERS)

        result = detect_report_file(path)
        parsed = TikTokAffiliateOrdersImporter().parse_file(path)

        self.assertEqual(result.report_type, REPORT_AFFILIATE_ORDERS)
        self.assertEqual(result.compatibility, COMPATIBLE_WITH_NEW_COLUMNS)
        self.assertEqual(result.missing_required, ())
        self.assertEqual(parsed.orders[0].settlement_status, "SETTLED")
        self.assertEqual(parsed.orders[0].normalized_order_type, "SHOP_AD_ORDER")
        self.assertEqual(normalize_settlement_status("Pending"), "PENDING")

    def test_reordered_headers_and_added_column_are_adaptive(self) -> None:
        path = self.root / "changed.xlsx"
        headers = list(reversed(HEADERS)) + ["Nova coluna TikTok"]
        row = report_row(**{"Nova coluna TikTok": "novo"})
        write_xlsx_report(path, [row], headers=headers)
        result = detect_report_file(path)
        parsed = TikTokAffiliateOrdersImporter().parse_file(path)
        self.assertEqual(result.compatibility, COMPATIBLE_WITH_NEW_COLUMNS)
        self.assertEqual(len(parsed.orders), 1)

    def test_brazilian_decimals_use_decimal_without_float(self) -> None:
        self.assertEqual(str(parse_decimal_br("R$ 1.234,56")), "1234.56")
        self.assertEqual(str(parse_decimal_br("1.234,56")), "1234.56")
        self.assertEqual(str(parse_decimal_br("1234,56")), "1234.56")
        self.assertEqual(str(parse_decimal_br("1234.56")), "1234.56")

    def test_large_ids_remain_strings_and_slash_settlement_is_null(self) -> None:
        path = self.root / "ids.xlsx"
        write_xlsx_report(path, [report_row()])
        order = TikTokAffiliateOrdersImporter().parse_file(path).orders[0]
        self.assertIsInstance(order.external_order_id, str)
        self.assertEqual(order.external_order_id, "7670243375139802375")
        self.assertEqual(order.items[0].external_content_id, "7699243375139802375")
        self.assertIsNone(order.commission_settled_at)

    def test_multisku_order_upserts_one_order_and_two_items_exactly(self) -> None:
        path = self.root / "multi.xlsx"
        second = report_row(
            **{
                "ID do SKU": "1770243375139802376",
                "Preço": "20,00",
                "GMV": "40,00",
                "Itens vendidos": "2",
                "Comissão padrão estimada": "6,00",
            }
        )
        write_xlsx_report(path, [report_row(), second])
        result = AffiliateReportImporter(self.config).import_file(
            self.conn, path, import_mode="FULL_HISTORY"
        )
        self.assertEqual(result["orders_new"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_orders").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_order_items").fetchone()[0], 2)
        order = self.conn.execute("SELECT * FROM affiliate_orders").fetchone()
        self.assertEqual(order["external_order_id"], "7670243375139802375")
        self.assertEqual(order["gmv_minor"], 10336)
        self.assertEqual(order["quantity"], 3)

    def test_video_matches_directly_but_showcase_and_live_never_match_video(self) -> None:
        product_id, content_id = self._local_product_and_content()
        rows = [
            report_row(),
            report_row(
                **{
                    "ID do pedido": "7670243375139802376",
                    "ID do SKU": "1770243375139802376",
                    "Tipo de conteúdo": "Vitrine",
                }
            ),
            report_row(
                **{
                    "ID do pedido": "7670243375139802377",
                    "ID do SKU": "1770243375139802377",
                    "Tipo de conteúdo": "Live",
                }
            ),
        ]
        path = self.root / "types.xlsx"
        write_xlsx_report(path, rows)
        AffiliateReportImporter(self.config).import_file(self.conn, path, import_mode="FULL_HISTORY")
        items = self.conn.execute(
            "SELECT normalized_content_type, content_id, attribution_type FROM affiliate_order_items ORDER BY id"
        ).fetchall()
        self.assertEqual([row["normalized_content_type"] for row in items], [CONTENT_VIDEO, CONTENT_SHOWCASE, CONTENT_LIVE])
        self.assertEqual(items[0]["content_id"], content_id)
        self.assertEqual(items[0]["attribution_type"], "OFFICIAL")
        self.assertIsNone(items[1]["content_id"])
        self.assertIsNone(items[2]["content_id"])
        self.assertEqual(product_id, 1)

    def test_pending_updates_to_settled_then_ineligible_with_history(self) -> None:
        importer = AffiliateReportImporter(self.config)
        pending = self.root / "pending.xlsx"
        settled = self.root / "settled.xlsx"
        ineligible = self.root / "ineligible.xlsx"
        write_xlsx_report(pending, [report_row()])
        write_xlsx_report(
            settled,
            [report_row(**{
                "Status de liquidação do pedido": "Liquidado",
                "Comissão padrão": "9,50",
                "Valor total final recebido": "9,50",
                "Data de liquidação da comissão": "09/08/2026 12:00:00",
            })],
        )
        write_xlsx_report(
            ineligible,
            [report_row(**{"Status de liquidação do pedido": "Inelegível"})],
        )
        importer.import_file(self.conn, pending, import_mode="FULL_HISTORY")
        importer.import_file(self.conn, settled, import_mode="FULL_HISTORY")
        row = self.conn.execute("SELECT * FROM affiliate_orders").fetchone()
        self.assertEqual(row["settlement_status"], "SETTLED")
        self.assertEqual(row["actual_commission_minor"], 950)
        self.assertEqual(row["final_received_minor"], 950)
        self.assertIsNotNone(row["commission_settled_at"])
        importer.import_file(self.conn, ineligible, import_mode="FULL_HISTORY")
        self.assertEqual(self.conn.execute("SELECT settlement_status FROM affiliate_orders").fetchone()[0], "INELIGIBLE")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_order_status_history").fetchone()[0], 3)

    def test_refunds_and_invalid_row_are_recorded_without_stopping_import(self) -> None:
        path = self.root / "quality.xlsx"
        invalid = report_row(**{"ID do pedido": "", "GMV": "inválido"})
        valid = report_row(**{"Itens vendidos": "3", "Itens reembolsados": "1"})
        write_xlsx_report(path, [invalid, valid])
        result = AffiliateReportImporter(self.config).import_file(self.conn, path, import_mode="FULL_HISTORY")
        self.assertEqual(result["rows_failed"], 1)
        self.assertEqual(self.conn.execute("SELECT items_refunded FROM affiliate_order_items").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_import_errors").fetchone()[0], 1)

    def test_duplicate_hash_requires_explicit_reprocess_and_remains_idempotent(self) -> None:
        path = self.root / "duplicate.xlsx"
        write_xlsx_report(path, [report_row()])
        importer = AffiliateReportImporter(self.config)
        importer.import_file(self.conn, path, import_mode="FULL_HISTORY")
        preview = importer.preview_file(self.conn, path, import_mode="FULL_HISTORY")
        self.assertTrue(preview["duplicate"])
        skipped = importer.process_batch(self.conn, preview["batch_id"])
        self.assertEqual(skipped["status"], "SKIPPED")
        processed = importer.process_batch(self.conn, preview["batch_id"], reprocess=True)
        self.assertEqual(processed["orders_new"], 0)
        self.assertEqual(processed["rows_unchanged"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_orders").fetchone()[0], 1)

    def test_retroactive_manual_match_and_legacy_classification(self) -> None:
        product_id, content_id = self._local_product_and_content(video_id=None)
        path = self.root / "retroactive.xlsx"
        write_xlsx_report(path, [report_row()])
        AffiliateReportImporter(self.config).import_file(self.conn, path, import_mode="FULL_HISTORY")
        item = self.conn.execute("SELECT content_id FROM affiliate_order_items").fetchone()
        self.assertIsNone(item["content_id"])
        matched = reconcile_external_content_id(
            self.conn, "7699243375139802375", content_id, manual=True
        )
        self.assertEqual(matched, 1)
        item = self.conn.execute("SELECT content_id, attribution_type FROM affiliate_order_items").fetchone()
        self.assertEqual(item["content_id"], content_id)
        self.assertEqual(item["attribution_type"], "MANUAL")

        second = self.root / "legacy.xlsx"
        write_xlsx_report(
            second,
            [report_row(**{
                "ID do pedido": "7670243375139802380",
                "ID do SKU": "1770243375139802380",
                "ID do conteúdo": "7699243375139802380",
            })],
        )
        AffiliateReportImporter(self.config).import_file(self.conn, second, import_mode="FULL_HISTORY")
        classify_external_content(self.conn, "7699243375139802380", "LEGACY")
        statuses = {row["external_content_id"]: row["status"] for row in content_reconciliation(self.conn)}
        self.assertEqual(statuses["7699243375139802380"], "LEGACY")
        self.assertEqual(product_id, 1)

    def test_daily_analytics_separates_gmv_commissions_received_and_content_type(self) -> None:
        path = self.root / "analytics.xlsx"
        settled = report_row(**{
            "Status de liquidação do pedido": "Liquidado",
            "Comissão padrão": "9,50",
            "Valor total final recebido": "9,10",
            "Data de liquidação da comissão": "08/08/2026 15:00:00",
        })
        showcase = report_row(**{
            "ID do pedido": "7670243375139802381",
            "ID do SKU": "1770243375139802381",
            "Tipo de conteúdo": "Vitrine",
            "ID do conteúdo": "showcase-1",
        })
        write_xlsx_report(path, [settled, showcase])
        AffiliateReportImporter(self.config).import_file(self.conn, path, import_mode="FULL_HISTORY")
        metrics = affiliate_daily_operations(
            self.conn,
            days=30,
            as_of="2026-08-31T12:00:00-03:00",
        )
        self.assertEqual(metrics["totals"]["gmv_minor"], 12672)
        self.assertEqual(metrics["totals"]["estimated_commission_minor"], 1900)
        self.assertEqual(metrics["totals"]["actual_commission_minor"], 950)
        self.assertEqual(metrics["totals"]["final_received_minor"], 910)
        self.assertEqual({row["content_type"] for row in metrics["content_types"]}, {"VIDEO", "SHOWCASE"})
        self.assertEqual(metrics["efficiency_metrics"]["ctr"], "WAITING_FOR_VIDEO_ANALYTICS")

    def test_video_analytics_importer_is_interface_only(self) -> None:
        with self.assertRaisesRegex(NotImplementedError, "WAITING_FOR_VIDEO_ANALYTICS"):
            TikTokVideoPerformanceImporter().parse_file(self.root / "not-created.xlsx")

    def _local_product_and_content(self, video_id: str | None = "7699243375139802375") -> tuple[int, int]:
        timestamp = now_iso()
        product = self.conn.execute(
            """
            INSERT INTO products (product_key, name, tiktok_product_id, tiktok_sku_id, created_at, updated_at)
            VALUES ('local-product', 'Produto sanitizado', '1720243375139802375', '1770243375139802375', ?, ?)
            """,
            (timestamp, timestamp),
        )
        content = self.conn.execute(
            """
            INSERT INTO content_items (
                content_code, product_id, original_filename, file_hash,
                tiktok_video_id, status, published_at, created_at, updated_at
            ) VALUES ('PROD-0001', ?, 'video.mp4', 'hash-video', ?, 'PUBLISHED', ?, ?, ?)
            """,
            (product.lastrowid, video_id, timestamp, timestamp, timestamp),
        )
        self.conn.commit()
        return int(product.lastrowid), int(content.lastrowid)


def write_xlsx_report(
    path: Path,
    rows: list[dict[str, str]],
    *,
    headers: list[str] | None = None,
) -> None:
    columns = headers or HEADERS
    values = [columns] + [[row.get(header, "") for header in columns] for row in rows]
    sheet_rows = []
    for row_index, row_values in enumerate(values, start=1):
        cells = []
        for column_index, value in enumerate(row_values, start=1):
            reference = f"{column_name(column_index)}{row_index}"
            safe = html.escape(str(value), quote=False)
            cells.append(f'<c r="{reference}" t="inlineStr"><is><t xml:space="preserve">{safe}</t></is></c>')
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="worksheets/sheet1.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
            '</Relationships>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>',
        )


def column_name(index: int) -> str:
    value = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        value = chr(65 + remainder) + value
    return value


if __name__ == "__main__":
    unittest.main()
