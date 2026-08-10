from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import load_env_file
from src.store import connect, init_db
from src.tiktok_affiliate_sync import TikTokAffiliateSyncService
from src.tiktok_shop_api import TikTokShopConfig


ROOT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser(description="Sincroniza pedidos oficiais do Creator TikTok Shop")
    parser.add_argument("--account-id", type=int)
    parser.add_argument("--full", action="store_true", help="Reprocessa a janela historica configurada")
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    conn = connect(ROOT / "data" / "agent.db")
    init_db(conn)
    account_id = args.account_id
    if account_id is None:
        row = conn.execute(
            "SELECT id FROM tiktok_creator_accounts ORDER BY updated_at DESC, id DESC LIMIT 1"
        ).fetchone()
        if not row:
            raise SystemExit("Nenhuma conta Creator conectada")
        account_id = int(row["id"])
    result = TikTokAffiliateSyncService(TikTokShopConfig.from_env()).sync(
        conn, account_id, full=args.full
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
