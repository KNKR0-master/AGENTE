from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.affiliate import TikTokShopCreatorProvider
from src.store import now_iso
from src.tiktok_shop_api import (
    TikTokShopApiError,
    TikTokShopClient,
    TikTokShopConfig,
    TikTokShopConfigError,
    TikTokShopError,
    TikTokShopTokenService,
)


class TikTokAffiliateSyncService:
    def __init__(
        self,
        config: TikTokShopConfig,
        *,
        client: TikTokShopClient | Any | None = None,
        token_service: TikTokShopTokenService | Any | None = None,
        provider: TikTokShopCreatorProvider | Any | None = None,
        clock: Callable[[], float] = time.time,
        page_size: int = 100,
        max_pages: int = 10_000,
    ) -> None:
        self.config = config
        self.client = client or TikTokShopClient(config)
        self.token_service = token_service or TikTokShopTokenService(config)
        self.provider = provider or TikTokShopCreatorProvider(self.client)
        self.clock = clock
        self.page_size = max(1, min(page_size, 100))
        self.max_pages = max_pages

    def sync(
        self,
        conn: sqlite3.Connection,
        account_id: int,
        *,
        full: bool = False,
    ) -> dict[str, Any]:
        if not self.config.enabled:
            raise TikTokShopConfigError("TikTok Shop Creator API esta desativada")
        self.config.validate_for_oauth()
        account = conn.execute(
            "SELECT * FROM tiktok_creator_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not account or not account["refresh_token_encrypted"]:
            raise TikTokShopError("Conta Creator nao conectada")

        window_end = datetime.fromtimestamp(self.clock(), timezone.utc).replace(microsecond=0)
        if full or not account["last_sync_at"]:
            window_start = window_end - timedelta(days=self.config.full_sync_days)
            sync_mode = "FULL" if full else "INITIAL"
        else:
            previous = _parse_iso(account["last_sync_at"])
            window_start = previous - timedelta(seconds=self.config.sync_safety_window_seconds)
            sync_mode = "INCREMENTAL"

        started_at = now_iso()
        cursor = conn.execute(
            """
            INSERT INTO affiliate_sync_runs (
                provider, creator_account_id, sync_mode, window_start,
                window_end, status, started_at
            ) VALUES ('tiktok_shop_creator', ?, ?, ?, ?, 'RUNNING', ?)
            """,
            (account_id, sync_mode, window_start.isoformat(), window_end.isoformat(), started_at),
        )
        run_id = int(cursor.lastrowid)
        conn.commit()

        pages = 0
        seen = 0
        upserted = 0
        page_token: str | None = None
        page_tokens_seen: set[str] = set()
        try:
            access_token = self.token_service.get_access_token(conn, account_id)
            while True:
                if pages >= self.max_pages:
                    raise TikTokShopError("Limite de paginas excedido durante a sincronizacao")
                data = self.client.search_creator_affiliate_orders(
                    access_token=access_token,
                    create_time_ge=int(window_start.timestamp()),
                    create_time_lt=int(window_end.timestamp()),
                    page_token=page_token,
                    page_size=self.page_size,
                    conn=conn,
                    account_id=account_id,
                )
                orders = data.get("orders") or []
                if not isinstance(orders, list):
                    raise TikTokShopError("Resposta de pedidos em formato inesperado")
                result = self.provider.ingest_orders(conn, orders, account_id)
                pages += 1
                seen += int(result["seen"])
                upserted += int(result["upserted"])

                next_token = str(data.get("next_page_token") or "").strip() or None
                if not next_token:
                    break
                if next_token in page_tokens_seen:
                    raise TikTokShopError("Paginacao repetida pela TikTok Shop")
                page_tokens_seen.add(next_token)
                page_token = next_token

            finished_at = now_iso()
            conn.execute(
                """
                UPDATE affiliate_sync_runs
                SET status = 'COMPLETED', pages_processed = ?, orders_seen = ?,
                    orders_upserted = ?, finished_at = ?
                WHERE id = ?
                """,
                (pages, seen, upserted, finished_at, run_id),
            )
            conn.execute(
                """
                UPDATE tiktok_creator_accounts
                SET status = 'CONNECTED', last_sync_at = ?, last_error = NULL, updated_at = ?
                WHERE id = ?
                """,
                (window_end.isoformat(), finished_at, account_id),
            )
            conn.commit()
            return {
                "run_id": run_id,
                "mode": sync_mode,
                "window_start": window_start.isoformat(),
                "window_end": window_end.isoformat(),
                "pages": pages,
                "orders_seen": seen,
                "orders_upserted": upserted,
            }
        except Exception as exc:
            status = exc.integration_status if isinstance(exc, TikTokShopApiError) else "ERROR"
            error_code = exc.code if isinstance(exc, TikTokShopApiError) else exc.__class__.__name__
            finished_at = now_iso()
            conn.execute(
                """
                UPDATE affiliate_sync_runs
                SET status = 'FAILED', pages_processed = ?, orders_seen = ?,
                    orders_upserted = ?, error_code = ?, error_message = ?, finished_at = ?
                WHERE id = ?
                """,
                (pages, seen, upserted, error_code, str(exc)[:1000], finished_at, run_id),
            )
            conn.execute(
                """
                UPDATE tiktok_creator_accounts
                SET status = ?, last_error = ?, updated_at = ? WHERE id = ?
                """,
                (status, str(exc)[:1000], finished_at, account_id),
            )
            conn.commit()
            raise


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
