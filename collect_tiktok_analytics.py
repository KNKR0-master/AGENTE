from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import load_env_file
from src.store import connect, init_db
from src.tiktok_analytics import recompute_tiktok_analytics
from src.tiktok_studio_collector import TikTokStudioBrowserCollector, TikTokStudioCollectorConfig


ROOT = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description="Coleta diaria de analytics video a video do TikTok Studio")
    parser.add_argument("--scheduled", action="store_true", help="Registra o gatilho como agendado")
    parser.add_argument("--force", action="store_true", help="Permite uma nova coleta no mesmo dia")
    parser.add_argument("--recompute-only", action="store_true", help="Recalcula scores sem abrir o navegador")
    parser.add_argument("--setup-session", action="store_true", help="Abre o perfil local para login manual inicial")
    parser.add_argument("--remap", action="store_true", help="Inspeciona UI/rede sem alterar dados de producao")
    parser.add_argument("--last-n-days", type=int, help="Janela de videos a coletar (padrao: 30 dias)")
    parser.add_argument("--full-backfill", action="store_true", help="Descobre todo o historico disponivel")
    parser.add_argument(
        "--validate-video-id", action="append", default=[],
        help="Valida um Video ID sem gravar no banco; repita para ate 10 IDs",
    )
    parser.add_argument("--db", default=str(ROOT / "data" / "agent.db"))
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    config = TikTokStudioCollectorConfig.from_env(ROOT)
    if args.setup_session:
        result = TikTokStudioBrowserCollector(config).setup_session()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") == "SUCCESS" else 2
    if args.remap:
        result = TikTokStudioBrowserCollector(config).remap(last_n_days=args.last_n_days)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") == "SUCCESS" else 2
    if args.validate_video_id:
        result = TikTokStudioBrowserCollector(config).validate_videos(args.validate_video_id)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0 if result.get("status") == "SUCCESS" else 2

    with connect(args.db) as conn:
        init_db(conn)
        if args.recompute_only:
            result = recompute_tiktok_analytics(conn)
        else:
            result = TikTokStudioBrowserCollector(config).collect(
                conn,
                trigger_type="SCHEDULED" if args.scheduled else "MANUAL",
                force=args.force,
                last_n_days=args.last_n_days,
                full_backfill=args.full_backfill or None,
            )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result.get("status") in {None, "SUCCESS", "PARTIAL_SUCCESS"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
