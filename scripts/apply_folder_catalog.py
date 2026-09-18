from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.publisher.folder_catalog import apply_catalog_migration, load_plan, preview_catalog_migration
from src.publisher.service import init_publisher
from src.store import connect, init_db


def main() -> int:
    parser = argparse.ArgumentParser(description="Preview or apply the approved Drive folder catalog plan")
    parser.add_argument("--db", default="data/agent.db")
    parser.add_argument("--plan", default="data/publisher/diagnostics/folder-catalog-dry-run-20260917.json")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    db = Path(args.db).resolve()
    plan = load_plan(Path(args.plan).resolve())
    with connect(db) as conn:
        init_db(conn)
        init_publisher(conn)
        result = apply_catalog_migration(conn, plan) if args.apply else preview_catalog_migration(conn, plan)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
