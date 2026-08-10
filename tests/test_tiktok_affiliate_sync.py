from __future__ import annotations

import unittest
from datetime import datetime, timezone

from src.affiliate import TikTokShopCreatorProvider
from src.store import connect, init_db, now_iso
from src.tiktok_affiliate_sync import TikTokAffiliateSyncService
from tests.test_affiliate import official_order
from tests.test_tiktok_shop_api import config


class FakeTokenService:
    def get_access_token(self, conn, account_id):
        return "access-token"


class FakeClient:
    def __init__(self):
        self.calls = []

    def search_creator_affiliate_orders(self, **kwargs):
        self.calls.append(kwargs)
        if not kwargs.get("page_token"):
            return {"orders": [official_order("UNPAID")], "next_page_token": "page-2"}
        payload = official_order("COMPLETED")
        payload["id"] = "order-2"
        return {"orders": [payload]}


class SyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)
        self.conn.execute(
            """
            INSERT INTO tiktok_creator_accounts (
                creator_open_id, status, granted_scopes, access_token_encrypted,
                refresh_token_encrypted, created_at, updated_at
            ) VALUES ('creator', 'CONNECTED', '[]', 'x', 'y', ?, ?)
            """,
            (now_iso(), now_iso()),
        )
        self.conn.commit()

    def test_pagination_and_incremental_safety_window(self) -> None:
        now = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc).timestamp()
        client = FakeClient()
        service = TikTokAffiliateSyncService(
            config(),
            client=client,
            token_service=FakeTokenService(),
            provider=TikTokShopCreatorProvider(),
            clock=lambda: now,
        )
        initial = service.sync(self.conn, 1)
        self.assertEqual(initial["mode"], "INITIAL")
        self.assertEqual(initial["pages"], 2)
        self.assertEqual(initial["orders_seen"], 2)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_orders").fetchone()[0], 2)

        previous_sync = "2026-08-08T12:00:00+00:00"
        self.conn.execute(
            "UPDATE tiktok_creator_accounts SET last_sync_at = ? WHERE id = 1", (previous_sync,)
        )
        self.conn.commit()
        client.calls.clear()
        incremental = service.sync(self.conn, 1)
        expected_start = datetime(2026, 8, 8, 6, 0, tzinfo=timezone.utc).timestamp()
        self.assertEqual(incremental["mode"], "INCREMENTAL")
        self.assertEqual(client.calls[0]["create_time_ge"], int(expected_start))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM affiliate_orders").fetchone()[0], 2)


if __name__ == "__main__":
    unittest.main()
