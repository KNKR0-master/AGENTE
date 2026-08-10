from __future__ import annotations

import unittest

from dashboard_server import content_summary
from src.affiliate import TikTokShopCreatorProvider
from src.content_pipeline import add_performance_snapshot
from src.store import connect, init_db, now_iso
from tests.test_affiliate import official_order


class DashboardAffiliateTests(unittest.TestCase):
    def test_summary_combines_latest_engagement_and_latest_attributed_sales(self) -> None:
        conn = connect(":memory:")
        init_db(conn)
        conn.execute(
            """
            INSERT INTO products (
                product_key, name, tiktok_product_id, tiktok_sku_id, created_at, updated_at
            ) VALUES ('p', 'Produto', 'product-1', 'sku-1', ?, ?)
            """,
            (now_iso(), now_iso()),
        )
        conn.execute(
            """
            INSERT INTO content_items (
                content_code, product_id, original_filename, file_hash,
                tiktok_video_id, created_at, updated_at
            ) VALUES ('VID', 1, 'video.mp4', 'hash', 'video-1', ?, ?)
            """,
            (now_iso(), now_iso()),
        )
        conn.commit()
        add_performance_snapshot(conn, 1, {"views": 100, "captured_at": "2026-08-08T10:00:00+00:00"})

        payload = official_order("COMPLETED")
        TikTokShopCreatorProvider().ingest_orders(conn, [payload])
        payload["update_time"] += 60
        payload["skus"][0]["actual_bonus_commission"]["amount"] = "2.00"
        TikTokShopCreatorProvider().ingest_orders(conn, [payload])

        summary = content_summary(conn)
        self.assertEqual(summary["performance"]["views"], 100)
        self.assertEqual(summary["performance"]["orders"], 1)
        self.assertEqual(summary["performance"]["commission"], 5)


if __name__ == "__main__":
    unittest.main()
