from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import load_env_file
from src.drive_inventory import (
    drive_dashboard_payload,
    scan_drive_inventory,
    set_folder_mapping,
)
from src.store import connect, init_db


ROOT = Path(__file__).resolve().parent


def main() -> None:
    load_env_file(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Inventario somente leitura do Google Drive")
    parser.add_argument("--db", default=str(ROOT / "data" / "agent.db"))
    commands = parser.add_subparsers(dest="command", required=True)
    scan = commands.add_parser("scan", help="Atualiza a arvore e a elegibilidade")
    scan.add_argument("--cached", action="store_true", help="Aceita cache de 5 a 15 minutos")
    commands.add_parser("status", help="Mostra saude, pastas e inventario atual")
    mapping = commands.add_parser("map", help="Confirma o produto de uma pasta")
    mapping.add_argument("folder_id")
    mapping.add_argument("product_id", type=int)

    args = parser.parse_args()
    conn = connect(args.db)
    init_db(conn)
    if args.command == "scan":
        result = scan_drive_inventory(conn, force=not args.cached)
    elif args.command == "map":
        result = set_folder_mapping(conn, args.folder_id, args.product_id)
    else:
        result = drive_dashboard_payload(conn)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
