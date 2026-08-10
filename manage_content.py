from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.content_pipeline import (
    add_performance_snapshot,
    enqueue_content,
    import_content_from_path,
    mark_published,
    mark_sent_to_phone,
    next_publishable_content,
)
from src.store import connect, init_db


DB_PATH = Path("data") / "agent.db"


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline local de conteudo TikTok Shop")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser("import", help="Importa um video para o estoque local")
    import_parser.add_argument("--product-id", type=int, required=True)
    import_parser.add_argument("--path", required=True)
    import_parser.add_argument("--caption")
    import_parser.add_argument("--hashtags")
    import_parser.add_argument("--priority", type=int, default=50)
    import_parser.add_argument("--source")
    import_parser.add_argument("--campaign")
    import_parser.add_argument("--batch")

    queue_parser = subparsers.add_parser("queue", help="Coloca um conteudo na fila")
    queue_parser.add_argument("--content-id", type=int, required=True)
    queue_parser.add_argument("--priority", type=int)
    queue_parser.add_argument("--scheduled-date")

    sent_parser = subparsers.add_parser("sent", help="Marca um conteudo como enviado ao iPhone")
    sent_parser.add_argument("--content-id", type=int, required=True)

    published_parser = subparsers.add_parser("published", help="Marca um conteudo como publicado")
    published_parser.add_argument("--content-id", type=int, required=True)
    published_parser.add_argument("--tiktok-url")
    published_parser.add_argument("--published-at")
    published_parser.add_argument("--notes")

    perf_parser = subparsers.add_parser("performance", help="Registra performance manual")
    perf_parser.add_argument("--content-id", type=int, required=True)
    perf_parser.add_argument("--views", type=float, default=0)
    perf_parser.add_argument("--orders", type=float, default=0)
    perf_parser.add_argument("--gmv", type=float, default=0)
    perf_parser.add_argument("--commission", type=float, default=0)
    perf_parser.add_argument("--source", default="manual")

    subparsers.add_parser("next", help="Mostra o proximo conteudo publicavel")

    args = parser.parse_args()
    with connect(DB_PATH) as conn:
        init_db(conn)
        if args.command == "import":
            result = import_content_from_path(
                conn,
                product_id=args.product_id,
                source_path=args.path,
                caption=args.caption,
                hashtags=args.hashtags,
                priority=args.priority,
                generation_source=args.source,
                campaign=args.campaign,
                batch=args.batch,
            )
        elif args.command == "queue":
            enqueue_content(conn, args.content_id, priority=args.priority, scheduled_date=args.scheduled_date)
            result = {"ok": True, "content_id": args.content_id}
        elif args.command == "sent":
            mark_sent_to_phone(conn, args.content_id)
            result = {"ok": True, "content_id": args.content_id}
        elif args.command == "published":
            mark_published(
                conn,
                args.content_id,
                tiktok_url=args.tiktok_url,
                published_at=args.published_at,
                notes=args.notes,
            )
            result = {"ok": True, "content_id": args.content_id}
        elif args.command == "performance":
            performance_id = add_performance_snapshot(conn, args.content_id, vars(args), source=args.source)
            result = {"ok": True, "performance_id": performance_id}
        else:
            result = {"content": next_publishable_content(conn)}

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
