from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from src.affiliate import CsvAffiliateProvider, TikTokShopCreatorProvider, affiliate_dashboard
from src.store import connect, init_db, now_iso


class AffiliateProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)
        self.conn.execute(
            """
            INSERT INTO products (
                product_key, name, tiktok_product_id, tiktok_sku_id, created_at, updated_at
            ) VALUES ('p1', 'Produto A', 'product-1', 'sku-1', ?, ?)
            """,
            (now_iso(), now_iso()),
        )
        self.conn.execute(
            """
            INSERT INTO content_items (
                content_code, product_id, original_filename, file_hash,
                status, tiktok_video_id, created_at, updated_at
            ) VALUES ('VID-1', 1, 'video.mp4', 'hash-1', 'PUBLISHED', 'video-1', ?, ?)
            """,
            (now_iso(), now_iso()),
        )
        self.conn.commit()

    def test_official_order_is_idempotent_and_records_status_history(self) -> None:
        provider = TikTokShopCreatorProvider()
        payload = official_order("UNPAID")
        first = provider.ingest_orders(self.conn, [payload])
        second = provider.ingest_orders(self.conn, [payload])
        payload["status"] = "COMPLETED"
        third = provider.ingest_orders(self.conn, [payload])

        self.assertEqual(first, {"seen": 1, "upserted": 1})
        self.assertEqual(second, {"seen": 1, "upserted": 1})
        self.assertEqual(third, {"seen": 1, "upserted": 1})
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_orders").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_order_items").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM affiliate_order_status_history").fetchone()[0],
            2,
        )
        order = self.conn.execute("SELECT * FROM affiliate_orders").fetchone()
        self.assertEqual(order["product_id"], 1)
        self.assertEqual(order["content_id"], 1)
        self.assertEqual(order["actual_commission"], 4.0)
        performance = self.conn.execute(
            "SELECT * FROM content_performance ORDER BY id DESC LIMIT 1"
        ).fetchone()
        self.assertEqual(performance["provider"], "tiktok_shop_creator")
        self.assertEqual(performance["attribution_status"], "OFFICIAL")

    def test_unknown_content_is_not_attributed(self) -> None:
        payload = official_order("COMPLETED")
        payload["skus"][0]["content_id"] = "unknown-video"
        TikTokShopCreatorProvider().ingest_orders(self.conn, [payload])
        item = self.conn.execute("SELECT * FROM affiliate_order_items").fetchone()
        self.assertIsNone(item["content_id"])
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM content_performance").fetchone()[0], 0)

    def test_csv_fallback_accepts_localized_values(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "orders.csv"
            source.write_text(
                "external_order_id;order_status;order_created_at;tiktok_product_id;quantity;currency;gmv;actual_commission\n"
                "csv-1;COMPLETED;2026-08-08T10:00:00-03:00;product-1;2;BRL;129,90;19,48\n",
                encoding="utf-8",
            )
            provider = CsvAffiliateProvider()
            result = provider.ingest_orders(self.conn, provider.read_file(source))
        self.assertEqual(result["seen"], 1)
        row = self.conn.execute(
            "SELECT * FROM affiliate_orders WHERE external_order_id = 'csv-1'"
        ).fetchone()
        self.assertAlmostEqual(row["gmv"], 129.90)
        self.assertEqual(row["provider"], "csv")

    def test_dashboard_uses_requested_period(self) -> None:
        TikTokShopCreatorProvider().ingest_orders(self.conn, [official_order("COMPLETED")])
        summary = affiliate_dashboard(self.conn, 30)
        self.assertEqual(summary["period_days"], 30)
        self.assertEqual(summary["totals"]["orders"], 1)
        self.assertEqual(summary["top_products"][0]["name"], "Produto A")


def official_order(status: str) -> dict:
    return {
        "id": "order-1",
        "status": status,
        "create_time": 1786183200,
        "update_time": 1786186800,
        "skus": [
            {
                "id": "sku-1",
                "product_id": "product-1",
                "product_name": "Produto A",
                "content_type": "VIDEO",
                "content_id": "video-1",
                "quantity": 2,
                "price": {"amount": "64.95", "currency": "BRL"},
                "estimated_commission": {"amount": "5.00", "currency": "BRL"},
                "actual_creator_commission": {"amount": "3.00", "currency": "BRL"},
                "actual_bonus_commission": {"amount": "1.00", "currency": "BRL"},
            }
        ],
    }


if __name__ == "__main__":
    unittest.main()
