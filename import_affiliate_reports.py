from __future__ import annotations

import json
from pathlib import Path

from src.affiliate_reports import AffiliateReportConfig, AffiliateReportImporter
from src.config import load_env_file
from src.store import connect, init_db


ROOT = Path(__file__).resolve().parent


def main() -> None:
    load_env_file(ROOT / ".env")
    conn = connect(ROOT / "data" / "agent.db")
    init_db(conn)
    result = AffiliateReportImporter(AffiliateReportConfig.from_env(ROOT)).scan(conn)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
