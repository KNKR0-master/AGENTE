from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from src.store import connect, init_db
from src.tiktok_shop_api import (
    CREATOR_ORDERS_PATH,
    OAuthStateService,
    TikTokShopApiError,
    TikTokShopClient,
    TikTokShopConfig,
    TikTokShopError,
    TikTokShopSignatureService,
    TikTokShopTokenService,
)


class FakeCipher:
    def encrypt(self, value: str) -> str:
        return "cipher:" + value[::-1]

    def decrypt(self, value: str) -> str:
        return value.removeprefix("cipher:")[::-1]


class QueueTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers=None, body=None, timeout=30):
        self.calls.append({"method": method, "url": url, "headers": headers, "body": body})
        return self.responses.pop(0)


class TikTokShopApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = connect(":memory:")
        init_db(self.conn)
        self.config = config()

    def test_official_signature_example(self) -> None:
        signature = TikTokShopSignatureService.sign(
            "/authorization/202309/shops",
            {"app_key": "29a39d", "timestamp": 1623812664},
            "e59af819cc",
        )
        self.assertEqual(
            signature,
            "b596b73e0cc6de07ac26f036364178ab16b0a907af13d43f0a0cd2345f582dc8",
        )

    def test_signature_excludes_access_token_and_sign(self) -> None:
        base = TikTokShopSignatureService.sign("/x", {"app_key": "a"}, "secret")
        with_secrets = TikTokShopSignatureService.sign(
            "/x", {"app_key": "a", "access_token": "token", "sign": "old"}, "secret"
        )
        self.assertEqual(base, with_secrets)

    def test_oauth_state_is_session_bound_expiring_and_single_use(self) -> None:
        service = OAuthStateService(600)
        state = service.create(self.conn, "session-a")
        with self.assertRaises(TikTokShopError):
            service.validate(self.conn, state, "session-b")
        service.validate(self.conn, state, "session-a")
        with self.assertRaises(TikTokShopError):
            service.validate(self.conn, state, "session-a")

        expired = service.create(self.conn, "session-c")
        self.conn.execute("UPDATE oauth_states SET expires_at = '2000-01-01T00:00:00+00:00'")
        self.conn.commit()
        with self.assertRaises(TikTokShopError):
            service.validate(self.conn, expired, "session-c")

    def test_tokens_are_encrypted_at_rest_and_refresh_before_expiry(self) -> None:
        refresh_data = {
            "access_token": "new-access",
            "refresh_token": "new-refresh",
            "open_id": "creator-open",
            "user_type": 1,
            "granted_scopes": list(self.config.creator_scopes),
            "access_token_expire_in": 2_000_000_000,
            "refresh_token_expire_in": 2_100_000_000,
        }
        transport = QueueTransport([(200, {"code": 0, "data": refresh_data}, {})])
        service = TikTokShopTokenService(
            self.config, cipher=FakeCipher(), transport=transport, clock=lambda: 1_800_000_000
        )
        account_id = service.save_creator_authorization(
            self.conn,
            {
                "access_token": "old-access",
                "refresh_token": "old-refresh",
                "open_id": "creator-open",
                "user_type": 1,
                "granted_scopes": list(self.config.creator_scopes),
                "access_token_expire_in": 1_700_000_000,
                "refresh_token_expire_in": 2_100_000_000,
            },
        )
        raw = self.conn.execute(
            "SELECT access_token_encrypted, refresh_token_encrypted FROM tiktok_creator_accounts"
        ).fetchone()
        self.assertNotIn("old-access", raw["access_token_encrypted"])
        self.assertNotIn("old-refresh", raw["refresh_token_encrypted"])
        self.assertEqual(service.get_access_token(self.conn, account_id), "new-access")
        query = parse_qs(urlparse(transport.calls[0]["url"]).query)
        self.assertEqual(query["grant_type"], ["refresh_token"])

    def test_client_retries_rate_limit_and_observes_success(self) -> None:
        transport = QueueTransport(
            [
                (429, {"code": 36009002, "message": "rate limited"}, {}),
                (200, {"code": 0, "request_id": "req-1", "data": {"orders": []}}, {}),
            ]
        )
        sleeps = []
        client = TikTokShopClient(
            self.config,
            transport=transport,
            sleep=sleeps.append,
            clock=lambda: 1_786_183_200,
            max_retries=2,
        )
        result = client.search_creator_affiliate_orders(
            "access", 1, 2, conn=self.conn, account_id=None
        )
        self.assertEqual(result, {"orders": []})
        self.assertEqual(sleeps, [1])
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(urlparse(transport.calls[1]["url"]).path, CREATOR_ORDERS_PATH)
        body = transport.calls[1]["body"].decode("utf-8")
        self.assertEqual(json.loads(body), {"create_time_ge": 1, "create_time_lt": 2})
        self.assertEqual(
            self.conn.execute(
                "SELECT status FROM tiktok_api_requests ORDER BY id DESC LIMIT 1"
            ).fetchone()[0],
            "SUCCESS",
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM tiktok_api_requests ORDER BY id LIMIT 1").fetchone()[0],
            "RETRY",
        )

    def test_missing_scope_is_exposed_as_integration_status(self) -> None:
        transport = QueueTransport(
            [(200, {"code": 105005, "message": "missing permission", "request_id": "r"}, {})]
        )
        client = TikTokShopClient(self.config, transport=transport, max_retries=0)
        with self.assertRaises(TikTokShopApiError) as caught:
            client.get_creator_profile("access")
        self.assertEqual(caught.exception.integration_status, "MISSING_SCOPE")

    def test_unapproved_affiliate_api_is_exposed_without_retry(self) -> None:
        transport = QueueTransport(
            [(200, {"code": 16501011, "message": "app not approved", "request_id": "r"}, {})]
        )
        client = TikTokShopClient(self.config, transport=transport, max_retries=2)
        with self.assertRaises(TikTokShopApiError) as caught:
            client.search_creator_affiliate_orders("access", 1, 2)
        self.assertEqual(caught.exception.integration_status, "API_NOT_APPROVED")
        self.assertEqual(len(transport.calls), 1)


def config() -> TikTokShopConfig:
    return TikTokShopConfig(
        enabled=True,
        app_key="app-key",
        app_secret="app-secret",
        redirect_url="http://127.0.0.1:8765/api/integrations/tiktok-shop/creator/callback",
        market="BR",
        api_base_url="https://open-api.tiktokglobalshop.com",
        auth_base_url="https://auth.tiktok-shops.com",
        creator_scopes=("creator.affiliate.info", "creator.affiliate_collaboration.read"),
        oauth_state_ttl_seconds=600,
        refresh_leeway_seconds=900,
        sync_safety_window_seconds=21600,
        full_sync_days=30,
    )


if __name__ == "__main__":
    unittest.main()
