from __future__ import annotations

import argparse
from pathlib import Path

from src.kalodata_xlsx import parse_creator_export, parse_video_export
from src.store import connect, create_snapshot, ensure_two_versions, init_db, upsert_product


def main() -> None:
    parser = argparse.ArgumentParser(description="Importa exportações Kalodata para o banco local.")
    parser.add_argument("--video-xlsx", required=True, help="Arquivo Kalodata_Video_*.xlsx")
    parser.add_argument("--creator-xlsx", required=True, help="Arquivo Kalodata_Creator_*.xlsx")
    parser.add_argument("--product-name", help="Nome do produto. Se omitido, usa o Intro do XLSX.")
    parser.add_argument("--source-url", help="Link do produto no Kalodata/TikTok Shop.")
    parser.add_argument("--price", type=float, help="Preço do produto em reais.")
    parser.add_argument("--commission-rate", type=float, help="Comissão em percentual, ex: 15.")
    parser.add_argument("--shop-name", help="Nome da loja.")
    parser.add_argument("--db", default="data/agent.db", help="Caminho do banco SQLite.")
    parser.add_argument(
        "--validated",
        action="store_true",
        help="Marca o produto como validado e cria versões 1 e 2 na fila de produção.",
    )
    args = parser.parse_args()

    video_intro, video_records = parse_video_export(Path(args.video_xlsx))
    creator_intro, creator_records = parse_creator_export(Path(args.creator_xlsx))

    product_name = (
        args.product_name
        or video_intro.get("Produto")
        or creator_intro.get("Produto")
        or (video_records[0]["product_title"] if video_records else "")
    )
    if not product_name:
        raise SystemExit("Não consegui identificar o produto. Envie --product-name.")

    category = video_records[0]["product_category"] if video_records else None
    date_range = (
        video_records[0]["date_range"]
        if video_records
        else creator_records[0]["date_range"]
        if creator_records
        else ""
    )

    conn = connect(args.db)
    init_db(conn)
    product_id = upsert_product(
        conn,
        name=product_name,
        category=category,
        source_url=args.source_url,
        price=args.price,
        commission_rate=args.commission_rate,
        shop_name=args.shop_name,
    )
    snapshot_id = create_snapshot(
        conn,
        product_id=product_id,
        source="kalodata_xlsx",
        date_range=date_range,
        video_records=video_records,
        creator_records=creator_records,
        raw_meta={"video_intro": video_intro, "creator_intro": creator_intro},
    )

    if args.validated:
        conn.execute(
            "UPDATE products SET status = 'validated', decision = 'produzir', updated_at = datetime('now') WHERE id = ?",
            (product_id,),
        )
        ensure_two_versions(conn, product_id)

    conn.commit()
    print(f"Produto importado: {product_name}")
    print(f"product_id={product_id} snapshot_id={snapshot_id}")
    print(f"videos={len(video_records)} creators={len(creator_records)}")


if __name__ == "__main__":
    main()
