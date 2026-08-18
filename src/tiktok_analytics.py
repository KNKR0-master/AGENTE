from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.store import now_iso


try:
    TIMEZONE = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:
    TIMEZONE = timezone(timedelta(hours=-3), name="America/Sao_Paulo")
RUN_STATUSES = {
    "RUNNING",
    "SUCCESS",
    "PARTIAL_SUCCESS",
    "AUTH_REQUIRED",
    "INTERACTION_REQUIRED",
    "UI_CHANGED",
    "COVERAGE_CRITICAL",
    "FAILED_VALIDATION",
    "RATE_LIMITED",
    "FAILED",
}

SNAPSHOT_METRICS = (
    "views",
    "impressions",
    "reach",
    "traffic_for_you",
    "traffic_following",
    "traffic_search",
    "traffic_profile",
    "traffic_other",
    "traffic_sound",
    "new_viewers",
    "returning_viewers",
    "new_viewer_rate",
    "returning_viewer_rate",
    "likes",
    "comments",
    "shares",
    "saves",
    "profile_visits",
    "followers_generated",
    "average_watch_time_seconds",
    "total_watch_time_seconds",
    "watched_full_rate",
    "completion_rate",
    "product_impressions",
    "product_clicks",
    "product_ctr",
    "add_to_cart",
    "orders",
    "items_sold",
    "gmv_minor",
    "estimated_commission_minor",
    "conversion_rate",
    "average_order_value_minor",
    "commission_rate",
    "shop_ads_orders",
    "shop_ads_gmv_minor",
    "organic_orders",
    "organic_gmv_minor",
)

CUMULATIVE_METRICS = {
    "views",
    "impressions",
    "reach",
    "new_viewers",
    "returning_viewers",
    "likes",
    "comments",
    "shares",
    "saves",
    "profile_visits",
    "followers_generated",
    "total_watch_time_seconds",
    "product_impressions",
    "product_clicks",
    "add_to_cart",
    "orders",
    "items_sold",
    "gmv_minor",
    "estimated_commission_minor",
    "shop_ads_orders",
    "shop_ads_gmv_minor",
    "organic_orders",
    "organic_gmv_minor",
}

MISSING_VALUES = {"", "-", "--", "/", "n/a", "na", "null", "none", "indisponivel"}


def parse_localized_number(value: Any, locale: str = "pt-BR") -> Decimal | None:
    """Parse a UI number while keeping BR and EN separators explicit."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().lower().replace("\u00a0", " ")
    if text in MISSING_VALUES:
        return None

    multiplier = Decimal("1")
    suffixes = (
        (r"\s*(bilhoes|bilhao|bi|b)$", Decimal("1000000000")),
        (r"\s*(milhoes|milhao|mi|m)$", Decimal("1000000")),
        (r"\s*(mil|k)$", Decimal("1000")),
    )
    for pattern, factor in suffixes:
        if re.search(pattern, text, flags=re.IGNORECASE):
            text = re.sub(pattern, "", text, flags=re.IGNORECASE).strip()
            multiplier = factor
            break

    negative = text.startswith("-") or (text.startswith("(") and text.endswith(")"))
    text = re.sub(r"[^0-9,.-]", "", text).strip("()")
    if not text or text in {"-", ".", ","}:
        return None

    locale_lower = locale.lower()
    if locale_lower.startswith("pt"):
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"-?\d{1,3}(\.\d{3})+", text):
            text = text.replace(".", "")
    elif locale_lower.startswith("en"):
        if "." in text:
            text = text.replace(",", "")
        elif re.fullmatch(r"-?\d{1,3}(,\d{3})+", text):
            text = text.replace(",", "")
        else:
            text = text.replace(",", ".")
    else:
        text = _normalize_auto_separators(text)

    try:
        parsed = Decimal(text) * multiplier
    except InvalidOperation as exc:
        raise ValueError(f"Numero localizado invalido: {value}") from exc
    return -abs(parsed) if negative else parsed


def _normalize_auto_separators(text: str) -> str:
    if "," in text and "." in text:
        decimal_separator = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        return text.replace(thousands_separator, "").replace(decimal_separator, ".")
    separator = "," if "," in text else "." if "." in text else None
    if separator and re.fullmatch(rf"-?\d{{1,3}}({re.escape(separator)}\d{{3}})+", text):
        return text.replace(separator, "")
    return text.replace(",", ".")


def parse_currency_minor(value: Any, locale: str = "pt-BR") -> int | None:
    parsed = parse_localized_number(value, locale)
    if parsed is None:
        return None
    return int((parsed * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def parse_percentage(value: Any, locale: str = "pt-BR") -> float | None:
    if isinstance(value, str) and value.strip().startswith(("<", ">")):
        return None
    parsed = parse_localized_number(value, locale)
    return float(parsed) if parsed is not None else None


def parse_tiktok_datetime(value: Any, locale: str = "pt-BR") -> str | None:
    if value is None or str(value).strip().lower() in MISSING_VALUES:
        return None
    text = str(value).strip()
    text = re.sub(r"^Publicado em\s+", "", text, flags=re.IGNORECASE)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        relative = _parse_portuguese_short_date(text) if locale.lower().startswith("pt") else None
        if relative is not None:
            parsed = relative
            formats = []
        else:
            parsed = None
            formats = ["%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"]
        if locale.lower().startswith("en"):
            formats = ["%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%m/%d/%Y"]
        for date_format in formats:
            try:
                parsed = datetime.strptime(text, date_format)
                break
            except ValueError:
                continue
        if parsed is None:
            raise ValueError(f"Data TikTok invalida: {value}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TIMEZONE)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_duration_seconds(value: Any) -> float | None:
    if value is None or str(value).strip().lower() in MISSING_VALUES:
        return None
    text = str(value).strip().lower()
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        minutes, seconds = text.split(":")
        return float(int(minutes) * 60 + int(seconds))
    if re.fullmatch(r"\d{1,3}:\d{2}:\d{2}", text):
        hours, minutes, seconds = text.split(":")
        return float(int(hours) * 3600 + int(minutes) * 60 + int(seconds))
    hours = re.search(r"([\d.,]+)\s*h", text)
    minutes = re.search(r"([\d.,]+)\s*m", text)
    seconds = re.search(r"([\d.,]+)\s*s", text)
    if hours or minutes or seconds:
        return (
            float(parse_localized_number(hours.group(1), "auto") or 0) * 3600 if hours else 0
        ) + (
            float(parse_localized_number(minutes.group(1), "auto") or 0) * 60 if minutes else 0
        ) + (
            float(parse_localized_number(seconds.group(1), "auto") or 0) if seconds else 0
        )
    parsed = parse_localized_number(text, "auto")
    return float(parsed) if parsed is not None else None


def _parse_portuguese_short_date(text: str) -> datetime | None:
    months = {
        "jan": 1,
        "fev": 2,
        "mar": 3,
        "abr": 4,
        "mai": 5,
        "jun": 6,
        "jul": 7,
        "ago": 8,
        "set": 9,
        "out": 10,
        "nov": 11,
        "dez": 12,
    }
    match = re.fullmatch(r"(\d{1,2})\s+de\s+([a-z]{3})[a-z]*,?\s+(\d{1,2}):(\d{2})", text.lower())
    if not match or match.group(2) not in months:
        return None
    now = datetime.now(TIMEZONE)
    parsed = datetime(
        now.year,
        months[match.group(2)],
        int(match.group(1)),
        int(match.group(3)),
        int(match.group(4)),
        tzinfo=TIMEZONE,
    )
    if parsed > now + timedelta(days=1):
        parsed = parsed.replace(year=parsed.year - 1)
    return parsed


def begin_extraction_run(
    conn: sqlite3.Connection,
    run_key: str,
    *,
    trigger_type: str = "MANUAL",
    source: str = "tiktok_studio_browser",
    account_reference: str | None = None,
    page_url: str | None = None,
    last_n_days: int | None = None,
    full_backfill: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    key = run_key
    if not force:
        completed = conn.execute(
            """
            SELECT * FROM tiktok_analytics_runs
            WHERE (run_key = ? OR run_key LIKE ?)
              AND valid = 1
              AND status IN ('SUCCESS', 'PARTIAL_SUCCESS')
            ORDER BY id DESC
            LIMIT 1
            """,
            (run_key, f"{run_key}:%"),
        ).fetchone()
        if completed:
            return dict(completed)
        previous = conn.execute(
            "SELECT status FROM tiktok_analytics_runs WHERE run_key = ?",
            (run_key,),
        ).fetchone()
        if previous and previous["status"] != "RUNNING":
            key = f"{run_key}:retry-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    else:
        key = f"{run_key}:{datetime.now(timezone.utc).strftime('%H%M%S')}"
    timestamp = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO tiktok_analytics_runs (
            run_key, source, trigger_type, account_reference, status, current_step,
            page_url, last_n_days, full_backfill, started_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'RUNNING', 'AUTH_CHECK', ?, ?, ?, ?, ?, ?)
        """,
        (
            key, source, trigger_type, account_reference, page_url,
            last_n_days, int(full_backfill), timestamp, timestamp, timestamp,
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tiktok_analytics_runs WHERE run_key = ?", (key,)).fetchone()
    return dict(row)


def finish_extraction_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    *,
    current_step: str | None = None,
    page_url: str | None = None,
    screenshot_path: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    counts: dict[str, int] | None = None,
    coverage: dict[str, Any] | None = None,
    discovery_method: str | None = None,
    valid: bool = False,
    debug_report_path: str | None = None,
    metadata: dict[str, Any] | None = None,
    quality_summary: dict[str, Any] | None = None,
) -> None:
    if status not in RUN_STATUSES - {"RUNNING"}:
        raise ValueError(f"Status de coleta invalido: {status}")
    counts = counts or {}
    coverage = coverage or {}
    quality_summary = quality_summary or {}
    timestamp = now_iso()
    started = conn.execute("SELECT started_at FROM tiktok_analytics_runs WHERE id = ?", (run_id,)).fetchone()
    duration = _hours_between(started["started_at"], timestamp) * 3600 if started else None
    conn.execute(
        """
        UPDATE tiktok_analytics_runs
        SET status = ?, current_step = COALESCE(?, current_step),
            videos_discovered = ?, videos_collected = ?, snapshots_created = ?,
            videos_failed = ?, videos_complete = ?, videos_partial = ?, videos_timeout = ?,
            retries_total = ?, average_load_ms = ?, p95_load_ms = ?, average_completeness = ?,
            videos_expected = ?, new_videos_discovered = ?,
            collection_coverage = ?, coverage_confidence = ?, discovery_method = ?,
            valid = ?, duration_seconds = ?, page_url = COALESCE(?, page_url),
            screenshot_path = ?, debug_report_path = ?, error_code = ?, error_message = ?,
            error_summary = ?,
            metadata_json = ?, finished_at = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            status,
            current_step,
            int(counts.get("videos_discovered", 0)),
            int(counts.get("videos_collected", 0)),
            int(counts.get("snapshots_created", 0)),
            int(counts.get("videos_failed", 0)),
            int(quality_summary.get("videos_complete", 0)),
            int(quality_summary.get("videos_partial", 0)),
            int(quality_summary.get("videos_timeout", 0)),
            int(quality_summary.get("retries_total", 0)),
            _float_or_none(quality_summary.get("average_load_ms")),
            _float_or_none(quality_summary.get("p95_load_ms")),
            _float_or_none(quality_summary.get("average_completeness")),
            _int_or_none(coverage.get("videos_expected")),
            int(counts.get("new_videos_discovered", 0)),
            _float_or_none(coverage.get("collection_coverage")),
            str(coverage.get("coverage_confidence") or "LOW"),
            discovery_method,
            int(valid),
            duration,
            page_url,
            screenshot_path,
            debug_report_path,
            error_code,
            error_message,
            error_message,
            json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True),
            timestamp,
            timestamp,
            run_id,
        ),
    )
    conn.commit()


def upsert_tiktok_video(conn: sqlite3.Connection, video: dict[str, Any]) -> str:
    video_id = str(video.get("video_id") or video.get("external_content_id") or "").strip()
    if not video_id:
        raise ValueError("video_id e obrigatorio")
    timestamp = now_iso()
    published_at = parse_tiktok_datetime(video.get("published_at")) if video.get("published_at") else None
    conn.execute(
        """
        INSERT INTO external_tiktok_contents (
            external_content_id, external_content_type, product_id,
            external_product_id, external_product_name, status, video_url,
            published_at, caption, duration_seconds, thumbnail_url,
            platform_status, seller_name, catalog_source, discovered_at,
            last_collected_at, metadata_json, created_at, updated_at
        ) VALUES (?, 'VIDEO', ?, ?, ?, 'UNMATCHED', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_content_id, external_content_type) DO UPDATE SET
            product_id = COALESCE(excluded.product_id, external_tiktok_contents.product_id),
            external_product_id = COALESCE(excluded.external_product_id, external_tiktok_contents.external_product_id),
            external_product_name = COALESCE(excluded.external_product_name, external_tiktok_contents.external_product_name),
            video_url = COALESCE(excluded.video_url, external_tiktok_contents.video_url),
            published_at = COALESCE(excluded.published_at, external_tiktok_contents.published_at),
            caption = COALESCE(excluded.caption, external_tiktok_contents.caption),
            duration_seconds = COALESCE(excluded.duration_seconds, external_tiktok_contents.duration_seconds),
            thumbnail_url = COALESCE(excluded.thumbnail_url, external_tiktok_contents.thumbnail_url),
            platform_status = COALESCE(excluded.platform_status, external_tiktok_contents.platform_status),
            seller_name = COALESCE(excluded.seller_name, external_tiktok_contents.seller_name),
            catalog_source = excluded.catalog_source,
            last_collected_at = excluded.last_collected_at,
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        (
            video_id,
            video.get("product_id"),
            _text_or_none(video.get("external_product_id")),
            _text_or_none(video.get("product_name")),
            _text_or_none(video.get("video_url")),
            published_at,
            _text_or_none(video.get("caption")),
            _float_or_none(video.get("duration_seconds")),
            _text_or_none(video.get("thumbnail_url")),
            _text_or_none(video.get("status")),
            _text_or_none(video.get("seller_name")),
            _text_or_none(video.get("source")) or "tiktok_studio_browser",
            timestamp,
            timestamp,
            json.dumps(video.get("metadata") or {}, ensure_ascii=False, sort_keys=True),
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return video_id


def persist_video_snapshot(
    conn: sqlite3.Connection,
    run_id: int,
    video_id: str,
    metrics: dict[str, Any],
    *,
    captured_at: str | None = None,
    source: str = "tiktok_studio_video_analytics",
    page_url: str | None = None,
    raw_values: dict[str, Any] | None = None,
    metric_sources: dict[str, str] | None = None,
    quality_status_override: str | None = None,
    extraction_status: str = "COMPLETE",
    data_completeness_score: float | None = None,
    load_duration_ms: int | None = None,
    retry_count: int = 0,
    readiness: list[dict[str, Any]] | dict[str, Any] | None = None,
) -> dict[str, Any]:
    video_id = str(video_id).strip()
    if not video_id:
        raise ValueError("video_id e obrigatorio")
    if not conn.execute(
        "SELECT 1 FROM external_tiktok_contents WHERE external_content_id = ? AND external_content_type = 'VIDEO'",
        (video_id,),
    ).fetchone():
        upsert_tiktok_video(conn, {"video_id": video_id, "source": source})

    captured = parse_tiktok_datetime(captured_at) if captured_at else now_iso()
    values = {name: _numeric_or_none(metrics.get(name)) for name in SNAPSHOT_METRICS}
    published = conn.execute(
        "SELECT published_at FROM external_tiktok_contents WHERE external_content_id = ? AND external_content_type = 'VIDEO'",
        (video_id,),
    ).fetchone()
    age_hours = _hours_between(published["published_at"], captured) if published and published["published_at"] else None
    quality, anomaly = _snapshot_quality(conn, video_id, values)
    if not any(value is not None for value in values.values()):
        quality = "PARTIAL"
        anomaly = anomaly or "Nenhuma metrica numerica disponivel na tela"
    if quality_status_override:
        quality = quality_status_override
    elif extraction_status != "COMPLETE":
        quality = "PARTIAL"
    sources = metric_sources or {key: source for key, value in values.items() if value is not None}
    timestamp = now_iso()
    columns = ", ".join(SNAPSHOT_METRICS)
    placeholders = ", ".join("?" for _ in SNAPSHOT_METRICS)
    conn.execute(
        f"""
        INSERT INTO tiktok_video_snapshots (
            external_content_id, extraction_run_id, captured_at, video_age_hours,
            {columns}, source, page_url, raw_values_json, metric_sources_json,
            quality_status, data_quality_status, extraction_status,
            data_completeness_score, load_duration_ms, retry_count, readiness_json,
            anomaly_reason, created_at
        ) VALUES (?, ?, ?, ?, {placeholders}, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(external_content_id, extraction_run_id) DO NOTHING
        """,
        (
            video_id,
            run_id,
            captured,
            age_hours,
            *(values[name] for name in SNAPSHOT_METRICS),
            source,
            page_url,
            json.dumps(raw_values or {}, ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(sources, ensure_ascii=False, sort_keys=True),
            quality,
            quality,
            extraction_status,
            _float_or_none(data_completeness_score),
            _int_or_none(load_duration_ms),
            int(retry_count or 0),
            json.dumps(readiness or {}, ensure_ascii=False, sort_keys=True, default=str),
            anomaly,
            timestamp,
        ),
    )
    conn.execute(
        "UPDATE external_tiktok_contents SET last_collected_at = ?, updated_at = ? WHERE external_content_id = ? AND external_content_type = 'VIDEO'",
        (captured, timestamp, video_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM tiktok_video_snapshots WHERE external_content_id = ? AND extraction_run_id = ?",
        (video_id, run_id),
    ).fetchone()
    return dict(row)


def cohort_for_age(age_hours: float | None) -> str:
    if age_hours is None:
        return "UNKNOWN"
    boundaries = (
        (2, "0-2h"),
        (6, "2-6h"),
        (12, "6-12h"),
        (24, "12-24h"),
        (48, "24-48h"),
        (72, "2-3d"),
        (168, "4-7d"),
        (336, "8-14d"),
        (720, "15-30d"),
    )
    for upper, label in boundaries:
        if age_hours <= upper:
            return label
    return ">30d"


def percentile_rank(value: float | int | None, population: Iterable[float | int | None]) -> float | None:
    if value is None:
        return None
    clean = sorted(float(item) for item in population if item is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return 50.0
    below = sum(item < float(value) for item in clean)
    equal = sum(item == float(value) for item in clean)
    return round(100.0 * (below + 0.5 * equal) / len(clean), 2)


def recompute_tiktok_analytics(conn: sqlite3.Connection, *, computed_at: str | None = None) -> dict[str, Any]:
    timestamp = parse_tiktok_datetime(computed_at) if computed_at else now_iso()
    latest_rows = conn.execute(
        """
        SELECT s.*, e.product_id, e.published_at,
               r.collection_coverage AS run_coverage,
               r.coverage_confidence AS run_coverage_confidence,
               r.status AS run_status
        FROM tiktok_video_snapshots s
        JOIN tiktok_analytics_runs r ON r.id = s.extraction_run_id
        JOIN external_tiktok_contents e
          ON e.external_content_id = s.external_content_id AND e.external_content_type = 'VIDEO'
        WHERE s.quality_status IN ('VALID', 'PARTIAL')
          AND r.valid = 1 AND r.collection_coverage >= 0.70
          AND s.id = (
              SELECT s2.id FROM tiktok_video_snapshots s2
              JOIN tiktok_analytics_runs r2 ON r2.id = s2.extraction_run_id
              WHERE s2.external_content_id = s.external_content_id
                AND s2.quality_status IN ('VALID', 'PARTIAL')
                AND r2.valid = 1 AND r2.collection_coverage >= 0.70
              ORDER BY s2.captured_at DESC, s2.id DESC LIMIT 1
          )
        """
    ).fetchall()
    commercial = _commercial_by_video(conn)
    candidates: list[dict[str, Any]] = []
    for row in latest_rows:
        item = dict(row)
        item["snapshot_count"] = conn.execute(
            """
            SELECT COUNT(*) FROM tiktok_video_snapshots s
            JOIN tiktok_analytics_runs r ON r.id = s.extraction_run_id
            WHERE s.external_content_id = ? AND s.quality_status IN ('VALID', 'PARTIAL')
              AND r.valid = 1 AND r.collection_coverage >= 0.70
            """,
            (item["external_content_id"],),
        ).fetchone()[0]
        item.update(_derive_snapshot_metrics(conn, item, commercial.get(item["external_content_id"], {})))
        candidates.append(item)

    for item in candidates:
        peers = [candidate for candidate in candidates if candidate["cohort"] == item["cohort"]]
        product_peers = [candidate for candidate in peers if candidate.get("product_id") == item.get("product_id")]
        item["account_percentile"] = _business_percentile(item, peers)
        item["product_percentile"] = _business_percentile(item, product_peers)
        _score_candidate(item, peers, product_peers)
        _persist_score(conn, item, timestamp)

    health = calculate_distribution_health(conn, computed_at=timestamp)
    alerts = generate_tiktok_alerts(conn, health, timestamp)
    conn.commit()
    return {"videos_scored": len(candidates), "health": health, "alerts_created": alerts}


def _derive_snapshot_metrics(
    conn: sqlite3.Connection, snapshot: dict[str, Any], commercial: dict[str, Any]
) -> dict[str, Any]:
    age = snapshot.get("video_age_hours")
    if age is None and snapshot.get("published_at"):
        age = _hours_between(snapshot["published_at"], snapshot["captured_at"])
    previous_24 = _snapshot_near_offset(conn, snapshot, 24)
    previous_48 = _snapshot_near_offset(conn, snapshot, 48)
    views = snapshot.get("views")
    orders = snapshot.get("orders") if snapshot.get("orders") is not None else commercial.get("orders_total")
    gmv = snapshot.get("gmv_minor") if snapshot.get("gmv_minor") is not None else commercial.get("gmv_total_minor")
    commission = (
        snapshot.get("estimated_commission_minor")
        if snapshot.get("estimated_commission_minor") is not None
        else commercial.get("commission_total_minor")
    )
    shop_ads_gmv = (
        snapshot.get("shop_ads_gmv_minor")
        if snapshot.get("shop_ads_gmv_minor") is not None
        else commercial.get("shop_ads_gmv_minor")
    )
    organic_gmv = (
        snapshot.get("organic_gmv_minor")
        if snapshot.get("organic_gmv_minor") is not None
        else commercial.get("organic_gmv_minor")
    )
    views_24 = _delta(snapshot.get("views"), previous_24.get("views") if previous_24 else None)
    views_48 = _delta(snapshot.get("views"), previous_48.get("views") if previous_48 else None)
    orders_24 = _delta(snapshot.get("orders"), previous_24.get("orders") if previous_24 else None)
    gmv_24 = _delta(snapshot.get("gmv_minor"), previous_24.get("gmv_minor") if previous_24 else None)
    commission_24 = _delta(
        snapshot.get("estimated_commission_minor"),
        previous_24.get("estimated_commission_minor") if previous_24 else None,
    )
    clicks_24 = _delta(snapshot.get("product_clicks"), previous_24.get("product_clicks") if previous_24 else None)
    if orders_24 is None:
        orders_24 = commercial.get("orders_24h")
    if gmv_24 is None:
        gmv_24 = commercial.get("gmv_24h_minor")
    if commission_24 is None:
        commission_24 = commercial.get("commission_24h_minor")
    result = {
        "cohort": cohort_for_age(age),
        "video_age_hours": age,
        "views_delta_24h": views_24,
        "views_delta_48h": views_48,
        "orders_delta_24h": orders_24,
        "gmv_delta_24h_minor": _int_or_none(gmv_24),
        "commission_delta_24h_minor": _int_or_none(commission_24),
        "product_clicks_delta_24h": clicks_24,
        "views_per_hour": _safe_div(views, age),
        "orders_per_1000_views": _per_thousand(orders, views),
        "gmv_per_1000_views_minor": _int_or_none(_per_thousand(gmv, views)),
        "commission_per_1000_views_minor": _int_or_none(_per_thousand(commission, views)),
        "product_ctr": snapshot.get("product_ctr")
        if snapshot.get("product_ctr") is not None
        else _percent(snapshot.get("product_clicks"), snapshot.get("product_impressions")),
        "click_to_order_cvr": _percent(orders, snapshot.get("product_clicks")),
        "engagement_rate": _percent(
            _sum_known(snapshot.get("likes"), snapshot.get("comments"), snapshot.get("shares"), snapshot.get("saves")),
            views,
        ),
        "share_rate": _percent(snapshot.get("shares"), views),
        "comment_rate": _percent(snapshot.get("comments"), views),
        "follow_rate": _percent(snapshot.get("followers_generated"), views),
        "paid_gmv_share": _percent(shop_ads_gmv, gmv),
        "organic_gmv_share": _percent(organic_gmv, gmv),
        "orders_total": orders,
        "gmv_total_minor": gmv,
        "commission_total_minor": commission,
        "shop_ads_orders": commercial.get("shop_ads_orders"),
        "normal_orders": commercial.get("normal_orders"),
    }
    decay_score, decay_status = _decay(conn, snapshot)
    result["decay_score"] = decay_score
    result["decay_status"] = decay_status
    return result


def _business_percentile(item: dict[str, Any], peers: list[dict[str, Any]]) -> float | None:
    metrics = ("gmv_delta_24h_minor", "orders_delta_24h", "gmv_per_1000_views_minor", "orders_per_1000_views")
    ranks = [percentile_rank(item.get(metric), [peer.get(metric) for peer in peers]) for metric in metrics]
    clean = [rank for rank in ranks if rank is not None]
    return round(sum(clean) / len(clean), 2) if clean else None


def _score_candidate(item: dict[str, Any], peers: list[dict[str, Any]], product_peers: list[dict[str, Any]]) -> None:
    def rank(metric: str) -> float | None:
        account = percentile_rank(item.get(metric), [peer.get(metric) for peer in peers])
        product = percentile_rank(item.get(metric), [peer.get(metric) for peer in product_peers])
        if account is None:
            return product
        if product is None:
            return account
        return round(account * 0.6 + product * 0.4, 2)

    components = {
        "commercial_efficiency": _average_known(
            rank("gmv_per_1000_views_minor"),
            rank("orders_per_1000_views"),
            rank("commission_per_1000_views_minor"),
        ),
        "gmv_velocity": rank("gmv_delta_24h_minor"),
        "order_velocity": rank("orders_delta_24h"),
        "product_ctr_cvr": _average_known(rank("product_ctr"), rank("click_to_order_cvr")),
        "retention": _average_known(
            rank("completion_rate"), rank("watched_full_rate"), rank("average_watch_time_seconds")
        ),
        "initial_distribution": _average_known(rank("views_per_hour"), rank("views_delta_24h")),
        "engagement": _average_known(rank("engagement_rate"), rank("share_rate")),
    }
    weights = {
        "commercial_efficiency": 25,
        "gmv_velocity": 20,
        "order_velocity": 15,
        "product_ctr_cvr": 15,
        "retention": 10,
        "initial_distribution": 10,
        "engagement": 5,
    }
    available_weight = sum(weights[key] for key, value in components.items() if value is not None)
    investment = (
        sum(float(components[key]) * weights[key] for key in components if components[key] is not None)
        / available_weight
        if available_weight
        else None
    )
    coverage = available_weight / sum(weights.values())
    confidence = _confidence(item, coverage)
    account_percentile = item.get("account_percentile")
    winner = bool(account_percentile is not None and account_percentile >= 90 and item.get("gmv_total_minor", 0) > 0)
    early_winner = bool(winner and item.get("video_age_hours") is not None and item["video_age_hours"] <= 48)
    run_coverage = float(item.get("run_coverage") or 0)
    completeness = item.get("data_completeness_score")
    readiness_incomplete = (
        item.get("extraction_status") not in {None, "COMPLETE"}
        or (completeness is not None and float(completeness) < 60)
    )
    if run_coverage < 0.90 or item.get("quality_status") != "VALID" or readiness_incomplete:
        recommendation = "OBSERVAR"
    elif item.get("decay_status") in {"DECAYING", "DEAD"}:
        recommendation = "EM_DECAY"
    elif investment is None or confidence < 35:
        recommendation = "OBSERVAR"
    elif investment >= 80 and confidence >= 65:
        recommendation = "ESCALAR"
    elif investment >= 62:
        recommendation = "TESTAR_COM_BAIXO_ORCAMENTO"
    elif investment < 40 and confidence >= 50:
        recommendation = "NAO_ESCALAR"
    else:
        recommendation = "OBSERVAR"
    reasons = _recommendation_reasons(item, components, investment, confidence)
    item.update(
        {
            "investment_score": round(investment, 2) if investment is not None else None,
            "confidence_score": round(confidence, 2),
            "confidence_level": "HIGH" if confidence >= 70 else "MEDIUM" if confidence >= 40 else "LOW",
            "winner": int(winner),
            "early_winner": int(early_winner),
            "recommendation": recommendation,
            "reasons": reasons,
            "component_scores": components,
        }
    )


def _confidence(item: dict[str, Any], coverage: float) -> float:
    views = float(item.get("views") or 0)
    orders = float(item.get("orders_total") or 0)
    clicks = float(item.get("product_clicks") or 0)
    age = float(item.get("video_age_hours") or 0)
    snapshots = int(item.get("snapshot_count") or 0)
    run_coverage = max(0.0, min(1.0, float(item.get("run_coverage") or 0)))
    missing_critical = sum(
        item.get(metric) is None
        for metric in ("views", "gmv_total_minor", "orders_total", "product_ctr", "watched_full_rate")
    )
    raw = min(
        100.0,
        min(35.0, math.log10(max(1.0, views)) / 5 * 35)
        + min(25.0, math.log10(max(1.0, orders + 1)) / 2 * 25)
        + min(15.0, math.log10(max(1.0, clicks + 1)) / 3 * 15)
        + min(10.0, age / 48 * 10)
        + coverage * 10
        + min(15.0, snapshots * 5.0)
        + run_coverage * 10,
    )
    history_cap = 55.0 if snapshots < 2 else 75.0 if snapshots < 3 else 100.0
    return max(0.0, min(raw - missing_critical * 4.0, history_cap) * run_coverage)


def _recommendation_reasons(
    item: dict[str, Any], components: dict[str, float | None], investment: float | None, confidence: float
) -> list[str]:
    reasons: list[str] = []
    if item.get("gmv_per_1000_views_minor") is not None:
        reasons.append(f"GMV/1k views R$ {item['gmv_per_1000_views_minor'] / 100:.2f}")
    if item.get("account_percentile") is not None:
        reasons.append(f"percentil comercial da conta {item['account_percentile']:.0f}")
    if item.get("gmv_delta_24h_minor") is not None:
        reasons.append(f"GMV 24h R$ {item['gmv_delta_24h_minor'] / 100:.2f}")
    if item.get("decay_status") not in {"NO_HISTORY", "INSUFFICIENT_DATA"}:
        reasons.append(f"momentum {item['decay_status']}")
    if float(item.get("run_coverage") or 0) < 0.90:
        reasons.append("coleta parcial: decisao limitada a OBSERVAR")
    if item.get("extraction_status") not in {None, "COMPLETE"}:
        reasons.append("readiness incompleto: decisao limitada a OBSERVAR")
    if item.get("data_completeness_score") is not None and float(item["data_completeness_score"]) < 60:
        reasons.append("completude abaixo de 60%")
    if investment is None:
        reasons.append("metricas insuficientes para score de investimento")
    if confidence < 40:
        reasons.append("amostra ainda pequena ou incompleta")
    missing = [name for name, value in components.items() if value is None]
    if missing:
        reasons.append("sem " + ", ".join(missing))
    return reasons[:5]


def _persist_score(conn: sqlite3.Connection, item: dict[str, Any], timestamp: str) -> None:
    fields = (
        "views_delta_24h",
        "views_delta_48h",
        "orders_delta_24h",
        "gmv_delta_24h_minor",
        "commission_delta_24h_minor",
        "product_clicks_delta_24h",
        "views_per_hour",
        "orders_per_1000_views",
        "gmv_per_1000_views_minor",
        "commission_per_1000_views_minor",
        "product_ctr",
        "click_to_order_cvr",
        "engagement_rate",
        "share_rate",
        "comment_rate",
        "follow_rate",
        "paid_gmv_share",
        "organic_gmv_share",
        "account_percentile",
        "product_percentile",
        "winner",
        "early_winner",
        "decay_score",
        "decay_status",
        "investment_score",
        "confidence_score",
        "confidence_level",
        "recommendation",
    )
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{field}=excluded.{field}" for field in fields)
    conn.execute(
        f"""
        INSERT INTO tiktok_video_scores (
            external_content_id, snapshot_id, cohort, {columns}, reasons_json,
            component_scores_json, computed_at
        ) VALUES (?, ?, ?, {placeholders}, ?, ?, ?)
        ON CONFLICT(snapshot_id) DO UPDATE SET
            cohort=excluded.cohort, {updates}, reasons_json=excluded.reasons_json,
            component_scores_json=excluded.component_scores_json,
            computed_at=excluded.computed_at
        """,
        (
            item["external_content_id"],
            item["id"],
            item["cohort"],
            *(item.get(field) for field in fields),
            json.dumps(item.get("reasons") or [], ensure_ascii=False),
            json.dumps(item.get("component_scores") or {}, ensure_ascii=False),
            timestamp,
        ),
    )


def _latest_collection_gate(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM tiktok_analytics_runs ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {"allowed": False, "full": False, "reason": "NEVER_RUN", "run": None}
    run = dict(row)
    coverage = _float_or_none(run.get("collection_coverage"))
    finished_at = run.get("finished_at")
    stale = False
    if finished_at:
        try:
            finished = datetime.fromisoformat(str(finished_at).replace("Z", "+00:00"))
            if finished.tzinfo is None:
                finished = finished.replace(tzinfo=timezone.utc)
            stale = finished < datetime.now(timezone.utc) - timedelta(hours=36)
        except ValueError:
            stale = True
    allowed = bool(run.get("valid")) and coverage is not None and coverage >= 0.70 and not stale
    full = allowed and coverage >= 0.90 and run.get("status") == "SUCCESS"
    return {
        "allowed": allowed,
        "full": full,
        "reason": "FULL" if full else "PARTIAL_DATA" if allowed else "STALE" if stale else "COLLECTION_INCOMPLETE",
        "coverage": coverage,
        "run": run,
    }


def calculate_distribution_health(conn: sqlite3.Connection, *, computed_at: str | None = None) -> dict[str, Any]:
    timestamp = parse_tiktok_datetime(computed_at) if computed_at else now_iso()
    now = datetime.fromisoformat(timestamp)
    gate = _latest_collection_gate(conn)
    points = _distribution_points(conn)
    current = [point for point in points if 0 <= (now - point["published"]).days < 7]
    previous = [point for point in points if 7 <= (now - point["published"]).days < 14]
    current_24 = _median_known(point.get("views_24h") for point in current)
    previous_24 = _median_known(point.get("views_24h") for point in previous)
    current_48 = _median_known(point.get("views_48h") for point in current)
    previous_48 = _median_known(point.get("views_48h") for point in previous)
    current_eff = _median_known(point.get("gmv_per_1000_views_minor") for point in current)
    previous_eff = _median_known(point.get("gmv_per_1000_views_minor") for point in previous)
    current_orders_eff = _median_known(point.get("orders_per_1000_views") for point in current)
    previous_orders_eff = _median_known(point.get("orders_per_1000_views") for point in previous)
    product_ratios: list[dict[str, Any]] = []
    product_keys = {point.get("product_key") for point in current + previous if point.get("product_key")}
    for product_key in product_keys:
        product_current = [point for point in current if point.get("product_key") == product_key]
        product_previous = [point for point in previous if point.get("product_key") == product_key]
        if len(product_current) < 2 or len(product_previous) < 2:
            continue
        ratio = _safe_div(
            _median_known(point.get("views_24h") for point in product_current),
            _median_known(point.get("views_24h") for point in product_previous),
        )
        if ratio is not None:
            product_ratios.append(
                {
                    "product_key": product_key,
                    "product_name": (product_current or product_previous)[0].get("product_name"),
                    "views_24h_ratio": ratio,
                }
            )
    view_ratio = _safe_div(current_24, previous_24)
    conversion_ratio = _safe_div(current_eff, previous_eff)
    if not gate["full"]:
        diagnosis = "NO_DATA" if not gate["allowed"] else "INSUFFICIENT_HISTORY"
        health_state = diagnosis
        score = None
    elif len(current) < 3 or len(previous) < 3 or view_ratio is None:
        diagnosis = "INSUFFICIENT_HISTORY"
        health_state = "INSUFFICIENT_HISTORY"
        score = None
    else:
        score = round(min(100.0, view_ratio * 100), 2)
        if view_ratio < 0.75 and (conversion_ratio is None or conversion_ratio >= 0.85):
            diagnosis = "DISTRIBUTION_DROP"
        elif view_ratio >= 0.85 and conversion_ratio is not None and conversion_ratio < 0.75:
            diagnosis = "CONVERSION_DROP"
        elif view_ratio < 0.75 and conversion_ratio is not None and conversion_ratio < 0.75:
            diagnosis = "ACCOUNT_WIDE_DROP"
        else:
            diagnosis = "HEALTHY"
        declining_products = [item for item in product_ratios if item["views_24h_ratio"] < 0.75]
        if diagnosis == "HEALTHY" and declining_products and len(declining_products) < len(product_ratios):
            diagnosis = "PRODUCT_DROP"
        health_state = "HEALTHY" if diagnosis == "HEALTHY" else "CRITICAL" if diagnosis == "ACCOUNT_WIDE_DROP" else "WARNING"
    if gate["full"] and len(current) >= 3 and len(previous) >= 3:
        created, lost = _winner_replacement(conn, now)
        ratio = _safe_div(created, lost)
    else:
        created, lost, ratio = 0, 0, None
    comparison_windows = {}
    for name, minimum_days, maximum_days in (
        ("last_3d", 0, 3),
        ("last_7d", 0, 7),
        ("previous_7d", 7, 14),
        ("previous_14d", 14, 28),
        ("last_30d", 0, 30),
    ):
        window = [
            point for point in points
            if minimum_days <= (now - point["published"]).total_seconds() / 86400 < maximum_days
        ]
        comparison_windows[name] = {
            "videos": len(window),
            "median_views_24h": _median_known(point.get("views_24h") for point in window),
            "median_views_48h": _median_known(point.get("views_48h") for point in window),
        }
    evidence = {
        "current_videos": len(current),
        "previous_videos": len(previous),
        "views_24h_ratio": view_ratio,
        "commercial_efficiency_ratio": conversion_ratio,
        "periods": ["ultimos_7_dias", "7_dias_anteriores"],
        "product_ratios": product_ratios,
        "comparison_windows": comparison_windows,
        "collection_gate": {key: value for key, value in gate.items() if key != "run"},
    }
    period_end = now.astimezone(TIMEZONE).date().isoformat()
    conn.execute(
        """
        INSERT INTO tiktok_account_health (
            computed_at, period_end, distribution_health_score, diagnosis, health_state,
            median_views_24h, median_views_48h, previous_median_views_24h,
            previous_median_views_48h, gmv_per_1000_views_minor,
            previous_gmv_per_1000_views_minor, orders_per_1000_views,
            previous_orders_per_1000_views, new_winners_created,
            old_winners_lost, replacement_ratio, evidence_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(period_end) DO UPDATE SET
            computed_at=excluded.computed_at,
            distribution_health_score=excluded.distribution_health_score,
            diagnosis=excluded.diagnosis,
            health_state=excluded.health_state,
            median_views_24h=excluded.median_views_24h,
            median_views_48h=excluded.median_views_48h,
            previous_median_views_24h=excluded.previous_median_views_24h,
            previous_median_views_48h=excluded.previous_median_views_48h,
            gmv_per_1000_views_minor=excluded.gmv_per_1000_views_minor,
            previous_gmv_per_1000_views_minor=excluded.previous_gmv_per_1000_views_minor,
            orders_per_1000_views=excluded.orders_per_1000_views,
            previous_orders_per_1000_views=excluded.previous_orders_per_1000_views,
            new_winners_created=excluded.new_winners_created,
            old_winners_lost=excluded.old_winners_lost,
            replacement_ratio=excluded.replacement_ratio,
            evidence_json=excluded.evidence_json
        """,
        (
            timestamp,
            period_end,
            score,
            diagnosis,
            health_state,
            current_24,
            current_48,
            previous_24,
            previous_48,
            _int_or_none(current_eff),
            _int_or_none(previous_eff),
            current_orders_eff,
            previous_orders_eff,
            created,
            lost,
            ratio,
            json.dumps(evidence, ensure_ascii=False, sort_keys=True),
        ),
    )
    return {
        "computed_at": timestamp,
        "period_end": period_end,
        "distribution_health_score": score,
        "diagnosis": diagnosis,
        "health_state": health_state,
        "median_views_24h": current_24,
        "previous_median_views_24h": previous_24,
        "median_views_48h": current_48,
        "previous_median_views_48h": previous_48,
        "gmv_per_1000_views_minor": _int_or_none(current_eff),
        "previous_gmv_per_1000_views_minor": _int_or_none(previous_eff),
        "orders_per_1000_views": current_orders_eff,
        "previous_orders_per_1000_views": previous_orders_eff,
        "new_winners_created": created,
        "old_winners_lost": lost,
        "replacement_ratio": ratio,
        "evidence": evidence,
    }


def generate_tiktok_alerts(conn: sqlite3.Connection, health: dict[str, Any], timestamp: str) -> int:
    created = 0
    day = timestamp[:10]
    if health["diagnosis"] == "DISTRIBUTION_DROP":
        current = health.get("median_views_24h") or 0
        previous = health.get("previous_median_views_24h") or 0
        drop = round((1 - current / previous) * 100) if previous else 0
        created += _insert_alert(
            conn,
            f"DISTRIBUTION_DROP:{day}",
            "DISTRIBUTION_DROP",
            "HIGH",
            "Queda de distribuicao",
            f"Mediana de views nas primeiras 24h caiu {drop}% contra a semana anterior, com eficiencia comercial relativamente estavel.",
            health,
            timestamp,
        )
    if health.get("old_winners_lost", 0) and (health.get("replacement_ratio") or 0) < 1:
        created += _insert_alert(
            conn,
            f"REPLACEMENT_GAP:{day}",
            "REPLACEMENT_GAP",
            "HIGH",
            "Reposicao de criativos abaixo do necessario",
            f"{health['new_winners_created']} novos vencedores para {health['old_winners_lost']} vencedores perdidos.",
            health,
            timestamp,
        )
    rows = conn.execute(
        """
        SELECT s.external_content_id, s.recommendation, s.investment_score,
               s.confidence_level, s.decay_status, s.winner, s.early_winner,
               s.account_percentile, s.gmv_per_1000_views_minor, e.product_id, e.published_at,
               COALESCE(p.name, e.external_product_name, e.external_content_id) AS product_name
        FROM tiktok_video_scores s
        JOIN tiktok_video_snapshots snap ON snap.id = s.snapshot_id
        JOIN external_tiktok_contents e ON e.external_content_id = s.external_content_id
          AND e.external_content_type = 'VIDEO'
        LEFT JOIN products p ON p.id = e.product_id
        WHERE snap.id = (
          SELECT s2.id FROM tiktok_video_snapshots s2
          WHERE s2.external_content_id = snap.external_content_id
          ORDER BY s2.captured_at DESC, s2.id DESC LIMIT 1
        )
        """
    ).fetchall()
    for row in rows:
        if row["winner"]:
            percentile_text = (
                f"percentil {row['account_percentile']:.0f}"
                if row["account_percentile"] is not None
                else "faixa vencedora"
            )
            created += _insert_alert(
                conn,
                f"NEW_WINNER:{row['external_content_id']}:{day}",
                "NEW_WINNER",
                "HIGH",
                "Novo vencedor",
                f"Video {row['external_content_id']} de {row['product_name']} entrou na {percentile_text} comercial da sua coorte.",
                dict(row),
                timestamp,
                row["external_content_id"],
                row["product_id"],
            )
        if row["recommendation"] == "ESCALAR":
            created += _insert_alert(
                conn,
                f"INVESTMENT:{row['external_content_id']}:{day}",
                "INVESTMENT_CANDIDATE",
                "HIGH",
                "Candidato a investimento",
                f"Video {row['external_content_id']} de {row['product_name']} atingiu score {row['investment_score']:.0f} com confianca {row['confidence_level']}.",
                dict(row),
                timestamp,
                row["external_content_id"],
                row["product_id"],
            )
        if row["decay_status"] in {"DECAYING", "DEAD"}:
            created += _insert_alert(
                conn,
                f"DECAY:{row['external_content_id']}:{row['decay_status']}:{day}",
                "WINNER_DECAY",
                "MEDIUM",
                "Criativo em decay",
                f"Video {row['external_content_id']} de {row['product_name']} esta {row['decay_status']}.",
                dict(row),
                timestamp,
                row["external_content_id"],
                row["product_id"],
            )
    by_product: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = row["product_id"] or f"external:{row['product_name']}"
        item = by_product.setdefault(
            key,
            {"product_id": row["product_id"], "product_name": row["product_name"], "decaying": 0, "new_winners": 0},
        )
        item["decaying"] += int(row["decay_status"] in {"DECAYING", "DEAD"})
        item["new_winners"] += int(bool(row["winner"]) and _published_within(row["published_at"], 5))
    for item in by_product.values():
        if item["decaying"] and not item["new_winners"]:
            created += _insert_alert(
                conn,
                f"PRODUCT_REPLACEMENT:{item['product_id'] or item['product_name']}:{day}",
                "PRODUCT_REPLACEMENT_GAP",
                "HIGH",
                "Produto sem reposicao criativa",
                f"{item['product_name']} possui {item['decaying']} criativo(s) em decay e nenhum novo vencedor recente.",
                item,
                timestamp,
                product_id=item["product_id"],
            )
    return created


def tiktok_analytics_dashboard(conn: sqlite3.Connection) -> dict[str, Any]:
    latest_run = conn.execute("SELECT * FROM tiktok_analytics_runs ORDER BY id DESC LIMIT 1").fetchone()
    health = conn.execute("SELECT * FROM tiktok_account_health ORDER BY computed_at DESC, id DESC LIMIT 1").fetchone()
    gate = _latest_collection_gate(conn)
    videos = _dashboard_videos(conn)
    products = _dashboard_products(conn, videos)
    families = _dashboard_families(conn, videos)
    valid_videos = [video for video in videos if video.get("extraction_run_id") is not None]
    recent_videos = [video for video in valid_videos if _published_within(video.get("published_at"), 7)]
    matched_videos = [
        video for video in videos
        if video.get("views") is not None and video.get("gmv_total_minor") is not None
    ]
    matched_views = _sum_if_any(video.get("views") for video in matched_videos)
    matched_gmv = _sum_if_any(video.get("gmv_total_minor") for video in matched_videos)
    matched_orders = _sum_if_any(video.get("orders_total") for video in matched_videos)
    new_viewer_estimates = [
        video.get("new_viewers")
        if video.get("new_viewers") is not None
        else (
            float(video["reach"]) * float(video["new_viewer_rate"]) / 100
            if video.get("reach") is not None and video.get("new_viewer_rate") is not None
            else None
        )
        for video in valid_videos
    ]
    unavailable = [
        metric
        for metric in SNAPSHOT_METRICS
        if not valid_videos or all(video.get(metric) is None for video in valid_videos)
    ]
    collection = _json_row(latest_run) if latest_run else {"status": "NEVER_RUN"}
    if collection:
        collection["analytics_allowed"] = gate["allowed"]
        collection["full_analytics_allowed"] = gate["full"]
        collection["gate_reason"] = gate["reason"]
    if not gate["full"]:
        for product in products:
            product["winners"] = None
            product["decaying"] = None
            product["best_video"] = None
            product["best_recent_video"] = None
            product["new_videos_needed"] = None
        families = []
    health_payload = _json_row(health)
    if not gate["full"]:
        health_payload = {
            "diagnosis": "NO_DATA" if not gate["allowed"] else "INSUFFICIENT_HISTORY",
            "health_state": "NO_DATA" if not gate["allowed"] else "INSUFFICIENT_HISTORY",
            "distribution_health_score": None,
            "replacement_ratio": None,
            "computed_at": collection.get("finished_at") if collection else None,
        }
    commercial_gmv_24h = _sum_if_any(video.get("gmv_24h_minor") for video in videos)
    commercial_orders_24h = _sum_if_any(video.get("orders_24h") for video in videos)
    investment = {
        "scale_now": [], "test": [], "observe": [], "do_not_scale": [], "decay": [],
        "blocked": not gate["allowed"],
        "partial": gate["allowed"] and not gate["full"],
        "message": (
            "Dados insuficientes - coleta incompleta."
            if not gate["allowed"] else
            "Dados parciais - somente observacao permitida."
            if not gate["full"] else None
        ),
    }
    if gate["allowed"]:
        investment.update({
            "scale_now": [video for video in valid_videos if gate["full"] and video.get("recommendation") == "ESCALAR"],
            "test": [video for video in valid_videos if gate["full"] and video.get("recommendation") == "TESTAR_COM_BAIXO_ORCAMENTO"],
            "observe": [video for video in valid_videos if video.get("recommendation") == "OBSERVAR"],
            "do_not_scale": [video for video in valid_videos if gate["full"] and video.get("recommendation") == "NAO_ESCALAR"],
            "decay": [video for video in valid_videos if gate["full"] and video.get("recommendation") == "EM_DECAY"],
        })
    return {
        "collection": collection,
        "health": health_payload,
        "summary": {
            "catalog_videos": conn.execute(
                "SELECT COUNT(*) FROM external_tiktok_contents WHERE external_content_type = 'VIDEO'"
            ).fetchone()[0],
            "videos_with_snapshots": conn.execute(
                """SELECT COUNT(DISTINCT s.external_content_id)
                   FROM tiktok_video_snapshots s
                   JOIN tiktok_analytics_runs r ON r.id = s.extraction_run_id
                   WHERE r.valid = 1 AND r.collection_coverage >= 0.70"""
            ).fetchone()[0],
            "snapshots": conn.execute(
                """SELECT COUNT(*) FROM tiktok_video_snapshots s
                   JOIN tiktok_analytics_runs r ON r.id = s.extraction_run_id
                   WHERE r.valid = 1 AND r.collection_coverage >= 0.70"""
            ).fetchone()[0],
            "early_winners": sum(bool(video.get("early_winner")) for video in valid_videos) if gate["full"] else None,
            "decaying": sum(video.get("decay_status") in {"DECAYING", "DEAD"} for video in valid_videos) if gate["full"] else None,
            "published_7d": sum(_published_within(video.get("published_at"), 7) for video in valid_videos) if gate["full"] else None,
            "views_7d": _sum_if_any(video.get("views") for video in recent_videos) if gate["full"] else None,
            "gmv_24h_minor": commercial_gmv_24h,
            "orders_24h": commercial_orders_24h,
            "views_24h": _sum_if_any(video.get("views_delta_24h") for video in valid_videos) if gate["full"] else None,
            "gmv_total_minor": _sum_if_any(video.get("gmv_total_minor") for video in videos),
            "orders_total": _sum_if_any(video.get("orders_total") for video in videos),
            "gmv_per_1000_views_minor": _int_or_none(_per_thousand(matched_gmv, matched_views)) if gate["full"] else None,
            "orders_per_1000_views": _per_thousand(matched_orders, matched_views) if gate["full"] else None,
            "new_viewers_estimated": _sum_if_any(new_viewer_estimates) if gate["full"] else None,
            "new_winners_created": int(health["new_winners_created"] or 0) if health and gate["full"] else None,
        },
        "videos": videos,
        "investment": investment,
        "early_winners": [video for video in valid_videos if gate["full"] and video.get("early_winner")],
        "creative_decay": [video for video in valid_videos if gate["full"] and video.get("decay_status") in {"DECAYING", "DEAD"}],
        "products": products,
        "creative_families": families,
        "distribution_curve": _distribution_curve(conn) if gate["full"] else [],
        "alerts": [_json_row(row) for row in conn.execute(
            "SELECT * FROM tiktok_analytics_alerts WHERE status = 'OPEN' ORDER BY created_at DESC LIMIT 50"
        ).fetchall()],
        "unavailable_metrics": unavailable,
        "metric_definitions": {
            "views_7d": {"definition": "Soma das reproducoes acumuladas dos videos publicados nos ultimos 7 dias.", "source": "STUDIO_VIDEO_CRAWLER", "window": "7 dias", "updated_at": collection.get("finished_at") if collection else None},
            "new_viewers_estimated": {"definition": "Estimativa de usuarios unicos novos; nao e numero de reproducoes.", "source": "STUDIO_VIEWERS_VIDEO", "window": "snapshot mais recente", "updated_at": collection.get("finished_at") if collection else None},
            "gmv_24h_minor": {"definition": "GMV de pedidos com Content ID de video nas ultimas 24 horas do relatorio importado.", "source": "AFFILIATE_ORDERS", "window": "24 horas", "updated_at": collection.get("finished_at") if collection else None},
            "gmv_total_minor": {"definition": "GMV conciliado dos pedidos Affiliate Orders importados.", "source": "AFFILIATE_ORDERS", "window": "historico importado", "updated_at": collection.get("finished_at") if collection else None},
            "published_7d": {"definition": "Videos com published_at nos ultimos 7 dias, somente com coleta completa.", "source": "STUDIO_VIDEO_CRAWLER", "window": "7 dias", "updated_at": collection.get("finished_at") if collection else None},
        },
        "data_quality": {
            "status": "VALID" if gate["full"] else "PARTIAL" if gate["allowed"] else "STALE" if gate["reason"] == "STALE" else "MISSING",
            "gate_reason": gate["reason"],
        },
        "decision_support_only": True,
    }


def save_creative_tags(conn: sqlite3.Connection, video_id: str, tags: dict[str, Any]) -> None:
    fields = (
        "creative_family",
        "hook_family",
        "body_family",
        "cta_family",
        "actor_type",
        "scenario",
        "production_method",
        "ai_generated",
        "ai_tool",
        "gera_viral",
        "creative_similarity_cluster",
        "saturation_score",
        "classification_source",
        "confidence",
        "manually_confirmed",
    )
    timestamp = now_iso()
    values = [tags.get(field) for field in fields]
    conn.execute(
        f"""
        INSERT INTO tiktok_creative_tags (external_content_id, {', '.join(fields)}, created_at, updated_at)
        VALUES (?, {', '.join('?' for _ in fields)}, ?, ?)
        ON CONFLICT(external_content_id) DO UPDATE SET
          {', '.join(f'{field}=excluded.{field}' for field in fields)}, updated_at=excluded.updated_at
        """,
        (video_id, *values, timestamp, timestamp),
    )
    conn.commit()


def create_experiment(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    timestamp = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO tiktok_experiments (name, hypothesis, product_id, status, starts_at, ends_at, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(payload["name"]).strip(),
            _text_or_none(payload.get("hypothesis")),
            payload.get("product_id"),
            payload.get("status") or "DRAFT",
            parse_tiktok_datetime(payload.get("starts_at")) if payload.get("starts_at") else None,
            parse_tiktok_datetime(payload.get("ends_at")) if payload.get("ends_at") else None,
            timestamp,
            timestamp,
        ),
    )
    conn.commit()
    return int(cursor.lastrowid)


def add_experiment_video(conn: sqlite3.Connection, experiment_id: int, payload: dict[str, Any]) -> None:
    video_id = str(payload["video_id"]).strip()
    if not conn.execute(
        "SELECT 1 FROM external_tiktok_contents WHERE external_content_id = ? AND external_content_type = 'VIDEO'",
        (video_id,),
    ).fetchone():
        raise ValueError("Video TikTok nao encontrado")
    conn.execute(
        """
        INSERT INTO tiktok_experiment_videos (
            experiment_id, external_content_id, variant, published_at, notes, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(experiment_id, external_content_id) DO UPDATE SET
            variant=excluded.variant, published_at=excluded.published_at, notes=excluded.notes
        """,
        (
            experiment_id,
            video_id,
            str(payload["variant"]).strip(),
            parse_tiktok_datetime(payload.get("published_at")) if payload.get("published_at") else None,
            _text_or_none(payload.get("notes")),
            now_iso(),
        ),
    )
    conn.commit()


def _commercial_by_video(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    latest_order = conn.execute("SELECT MAX(order_created_at) FROM affiliate_orders").fetchone()[0]
    report_is_current = False
    if latest_order:
        latest_order_at = datetime.fromisoformat(str(latest_order).replace("Z", "+00:00"))
        if latest_order_at.tzinfo is None:
            latest_order_at = latest_order_at.replace(tzinfo=timezone.utc)
        report_is_current = latest_order_at >= datetime.now(timezone.utc) - timedelta(hours=36)
    rows = conn.execute(
        """
        SELECT i.external_content_id,
               COUNT(DISTINCT o.id) AS orders_total,
               COALESCE(SUM(i.gmv_minor), 0) AS gmv_total_minor,
               COALESCE(SUM(COALESCE(i.actual_commission_minor, i.estimated_commission_minor)), 0) AS commission_total_minor,
               COUNT(DISTINCT CASE WHEN datetime(o.order_created_at) >= datetime('now', '-24 hours') THEN o.id END) AS orders_24h,
               COALESCE(SUM(CASE WHEN datetime(o.order_created_at) >= datetime('now', '-24 hours') THEN i.gmv_minor ELSE 0 END), 0) AS gmv_24h_minor,
               COALESCE(SUM(CASE WHEN datetime(o.order_created_at) >= datetime('now', '-24 hours') THEN COALESCE(i.actual_commission_minor, i.estimated_commission_minor) ELSE 0 END), 0) AS commission_24h_minor,
               COUNT(DISTINCT CASE WHEN o.normalized_order_type = 'SHOP_AD_ORDER' THEN o.id END) AS shop_ads_orders,
               COUNT(DISTINCT CASE WHEN o.normalized_order_type <> 'SHOP_AD_ORDER' THEN o.id END) AS normal_orders,
               COALESCE(SUM(CASE WHEN o.normalized_order_type = 'SHOP_AD_ORDER' THEN i.gmv_minor ELSE 0 END), 0) AS shop_ads_gmv_minor,
               COALESCE(SUM(CASE WHEN o.normalized_order_type <> 'SHOP_AD_ORDER' THEN i.gmv_minor ELSE 0 END), 0) AS organic_gmv_minor
        FROM affiliate_order_items i
        JOIN affiliate_orders o ON o.id = i.affiliate_order_id
        WHERE i.normalized_content_type = 'VIDEO' AND i.external_content_id IS NOT NULL
        GROUP BY i.external_content_id
        """
    ).fetchall()
    result = {row["external_content_id"]: dict(row) for row in rows}
    if not report_is_current:
        for item in result.values():
            item["orders_24h"] = None
            item["gmv_24h_minor"] = None
            item["commission_24h_minor"] = None
    return result


def _dashboard_videos(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    commercial = _commercial_by_video(conn)
    rows = conn.execute(
        """
        SELECT e.external_content_id AS video_id, e.video_url, e.published_at,
               e.caption, e.duration_seconds, e.thumbnail_url, e.platform_status,
               e.seller_name, e.product_id,
               COALESCE(p.name, e.external_product_name, e.external_product_id, 'Produto nao associado') AS product_name,
               s.*, sc.cohort, sc.views_delta_24h, sc.views_delta_48h,
               sc.orders_delta_24h, sc.gmv_delta_24h_minor,
               sc.commission_delta_24h_minor, sc.product_clicks_delta_24h,
               sc.views_per_hour, sc.orders_per_1000_views,
               sc.gmv_per_1000_views_minor, sc.commission_per_1000_views_minor,
               sc.product_ctr AS derived_product_ctr, sc.click_to_order_cvr,
               sc.engagement_rate, sc.share_rate, sc.comment_rate, sc.follow_rate,
               sc.paid_gmv_share, sc.organic_gmv_share, sc.account_percentile,
               sc.product_percentile, sc.winner, sc.early_winner,
               sc.decay_score, sc.decay_status, sc.investment_score,
               sc.confidence_score, sc.confidence_level, sc.recommendation,
               sc.reasons_json, tags.creative_family, tags.hook_family,
               tags.body_family, tags.cta_family, tags.actor_type, tags.scenario,
               tags.production_method, tags.ai_generated, tags.ai_tool,
               tags.gera_viral, tags.creative_similarity_cluster, tags.saturation_score,
               sr.collection_coverage AS snapshot_run_coverage,
               sr.coverage_confidence AS snapshot_run_coverage_confidence,
               sr.discovery_method AS snapshot_discovery_method
        FROM external_tiktok_contents e
        LEFT JOIN products p ON p.id = e.product_id
        LEFT JOIN tiktok_video_snapshots s ON s.id = (
            SELECT s2.id FROM tiktok_video_snapshots s2
            JOIN tiktok_analytics_runs r2 ON r2.id = s2.extraction_run_id
            WHERE s2.external_content_id = e.external_content_id
              AND r2.valid = 1 AND r2.collection_coverage >= 0.70
              AND s2.quality_status IN ('VALID', 'PARTIAL')
            ORDER BY s2.captured_at DESC, s2.id DESC LIMIT 1
        )
        LEFT JOIN tiktok_analytics_runs sr ON sr.id = s.extraction_run_id
        LEFT JOIN tiktok_video_scores sc ON sc.snapshot_id = s.id
        LEFT JOIN tiktok_creative_tags tags ON tags.external_content_id = e.external_content_id
        WHERE e.external_content_type = 'VIDEO'
        ORDER BY COALESCE(sc.investment_score, -1) DESC, e.last_sale_at DESC, e.id DESC
        """
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["reasons"] = _json_value(item.pop("reasons_json", None), [])
        derived_ctr = item.pop("derived_product_ctr", None)
        item["product_ctr"] = derived_ctr if derived_ctr is not None else item.get("product_ctr")
        item["data_quality_status"] = item.get("data_quality_status") or "MISSING"
        item.update(commercial.get(item["video_id"], {}))
        result.append(item)
    return result


def _dashboard_products(conn: sqlite3.Connection, videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    commercial_7d = _commercial_by_product_window(conn, days=7)
    inventory = {
        row["product_id"]: dict(row)
        for row in conn.execute(
            """
            SELECT p.id AS product_id,
                   SUM(CASE WHEN df.classification = 'AVAILABLE' AND df.missing_since IS NULL THEN 1 ELSE 0 END) AS ready_count,
                   SUM(CASE WHEN df.classification = 'ALREADY_QUEUED' AND df.missing_since IS NULL THEN 1 ELSE 0 END) AS queued_count
            FROM products p LEFT JOIN drive_files df ON df.product_id = p.id AND df.file_kind = 'VIDEO'
            GROUP BY p.id
            """
        ).fetchall()
    }
    grouped: dict[Any, dict[str, Any]] = {}
    for video in videos:
        key = video.get("product_id") or f"external:{video.get('product_name')}"
        item = grouped.setdefault(
            key,
            {
                "product_id": video.get("product_id"),
                "product_name": video.get("product_name"),
                "active_videos": 0,
                "winners": 0,
                "decaying": 0,
                "gmv_24h_values": [],
                "views_values": [],
                "gmv_values": [],
                "gmv_per_1000_values": [],
                "orders_per_1000_values": [],
                "commission_per_1000_values": [],
                "product_ctr_values": [],
                "cvr_values": [],
                "ready_count": 0,
                "queued_count": 0,
                "best_video": None,
                "best_recent_video": None,
                "new_videos_needed": 0,
            },
        )
        item["active_videos"] += 1
        item["winners"] += int(bool(video.get("winner")))
        item["decaying"] += int(video.get("decay_status") in {"DECAYING", "DEAD"})
        item["gmv_24h_values"].append(video.get("gmv_delta_24h_minor"))
        for target, field in (
            ("views_values", "views"),
            ("gmv_values", "gmv_total_minor"),
            ("gmv_per_1000_values", "gmv_per_1000_views_minor"),
            ("orders_per_1000_values", "orders_per_1000_views"),
            ("commission_per_1000_values", "commission_per_1000_views_minor"),
            ("product_ctr_values", "product_ctr"),
            ("cvr_values", "click_to_order_cvr"),
        ):
            if video.get(field) is not None:
                item[target].append(float(video[field]))
        if item["best_video"] is None or (video.get("investment_score") or -1) > (
            item["best_video"].get("investment_score") or -1
        ):
            item["best_video"] = {
                "video_id": video["video_id"],
                "investment_score": video.get("investment_score"),
            }
        if _published_within(video.get("published_at"), 7) and (
            item["best_recent_video"] is None
            or (video.get("investment_score") or -1) > (item["best_recent_video"].get("investment_score") or -1)
        ):
            item["best_recent_video"] = {
                "video_id": video["video_id"],
                "investment_score": video.get("investment_score"),
            }
    for item in grouped.values():
        stock = inventory.get(item.get("product_id"), {})
        commercial_key = item.get("product_id") or f"external:{item.get('product_name')}"
        recent = commercial_7d.get(commercial_key, {})
        item["ready_count"] = int(stock.get("ready_count") or 0)
        item["queued_count"] = int(stock.get("queued_count") or 0)
        item["new_videos_needed"] = max(0, item["decaying"] * 2 - item["ready_count"] - item["queued_count"])
        item["gmv_24h_minor"] = _sum_if_any(item.pop("gmv_24h_values"))
        item["gmv_7d_minor"] = recent.get("gmv_7d_minor")
        item["orders_7d"] = recent.get("orders_7d")
        for output, values_key in (
            ("median_views", "views_values"),
            ("median_gmv_minor", "gmv_values"),
            ("median_gmv_per_1000_views_minor", "gmv_per_1000_values"),
            ("median_orders_per_1000_views", "orders_per_1000_values"),
            ("median_commission_per_1000_views_minor", "commission_per_1000_values"),
            ("median_product_ctr", "product_ctr_values"),
            ("median_cvr", "cvr_values"),
        ):
            values = item.pop(values_key)
            item[output] = median(values) if values else None
    return sorted(
        grouped.values(),
        key=lambda item: (item.get("gmv_24h_minor") or -1, item.get("gmv_7d_minor") or -1),
        reverse=True,
    )


def _commercial_by_product_window(conn: sqlite3.Connection, *, days: int) -> dict[Any, dict[str, Any]]:
    latest_order = conn.execute("SELECT MAX(order_created_at) FROM affiliate_orders").fetchone()[0]
    if not latest_order:
        return {}
    latest_order_at = datetime.fromisoformat(str(latest_order).replace("Z", "+00:00"))
    if latest_order_at.tzinfo is None:
        latest_order_at = latest_order_at.replace(tzinfo=timezone.utc)
    if latest_order_at < datetime.now(timezone.utc) - timedelta(hours=36):
        return {}
    rows = conn.execute(
        """
        SELECT i.product_id, i.product_name,
               COUNT(DISTINCT o.id) AS orders_7d,
               COALESCE(SUM(i.gmv_minor), 0) AS gmv_7d_minor
        FROM affiliate_order_items i
        JOIN affiliate_orders o ON o.id = i.affiliate_order_id
        WHERE datetime(o.order_created_at) >= datetime('now', ?)
        GROUP BY i.product_id, i.product_name
        """,
        (f"-{int(days)} days",),
    ).fetchall()
    result: dict[Any, dict[str, Any]] = {}
    for row in rows:
        key = row["product_id"] or f"external:{row['product_name']}"
        current = result.setdefault(key, {"orders_7d": 0, "gmv_7d_minor": 0})
        current["orders_7d"] += int(row["orders_7d"] or 0)
        current["gmv_7d_minor"] += int(row["gmv_7d_minor"] or 0)
    return result


def _dashboard_families(conn: sqlite3.Connection, videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for video in videos:
        family = video.get("creative_family")
        if not family:
            continue
        item = grouped.setdefault(
            family,
            {"creative_family": family, "videos": 0, "gmv_minor": 0, "views": 0, "winners": 0, "scores": []},
        )
        item["videos"] += 1
        item["gmv_minor"] += int(video.get("gmv_total_minor") or 0)
        item["views"] += float(video.get("views") or 0)
        item["winners"] += int(bool(video.get("winner")))
        if video.get("investment_score") is not None:
            item["scores"].append(float(video["investment_score"]))
    result = []
    for item in grouped.values():
        item["median_investment_score"] = median(item.pop("scores")) if item["scores"] else None
        item["gmv_per_1000_views_minor"] = _int_or_none(_per_thousand(item["gmv_minor"], item["views"]))
        result.append(item)
    return sorted(result, key=lambda item: item["gmv_minor"], reverse=True)


def _distribution_points(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT e.external_content_id, e.published_at, e.product_id,
               COALESCE(p.name, e.external_product_name, e.external_product_id, 'Produto nao associado') AS product_name,
               s.video_age_hours, s.views,
               sc.gmv_per_1000_views_minor, sc.orders_per_1000_views
        FROM external_tiktok_contents e
        LEFT JOIN products p ON p.id = e.product_id
        JOIN tiktok_video_snapshots s ON s.external_content_id = e.external_content_id
        JOIN tiktok_analytics_runs r ON r.id = s.extraction_run_id
        LEFT JOIN tiktok_video_scores sc ON sc.snapshot_id = s.id
        WHERE e.external_content_type = 'VIDEO' AND e.published_at IS NOT NULL
          AND s.quality_status = 'VALID' AND r.valid = 1
          AND r.collection_coverage >= 0.90
        ORDER BY e.external_content_id, s.captured_at
        """
    ).fetchall()
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        published = datetime.fromisoformat(row["published_at"])
        item = grouped.setdefault(
            row["external_content_id"],
            {
                "video_id": row["external_content_id"],
                "published": published,
                "product_key": row["product_id"] or f"external:{row['product_name']}",
                "product_name": row["product_name"],
                "views_24h_candidates": [],
                "views_48h_candidates": [],
                "gmv_per_1000_views_minor": row["gmv_per_1000_views_minor"],
                "orders_per_1000_views": row["orders_per_1000_views"],
            },
        )
        age = row["video_age_hours"]
        if age is not None and 12 <= age <= 36 and row["views"] is not None:
            item["views_24h_candidates"].append((abs(age - 24), row["views"]))
        if age is not None and 36 <= age <= 60 and row["views"] is not None:
            item["views_48h_candidates"].append((abs(age - 48), row["views"]))
        if row["gmv_per_1000_views_minor"] is not None:
            item["gmv_per_1000_views_minor"] = row["gmv_per_1000_views_minor"]
        if row["orders_per_1000_views"] is not None:
            item["orders_per_1000_views"] = row["orders_per_1000_views"]
    result = []
    for item in grouped.values():
        item["views_24h"] = min(item.pop("views_24h_candidates"), default=(None, None))[1]
        item["views_48h"] = min(item.pop("views_48h_candidates"), default=(None, None))[1]
        result.append(item)
    return result


def _distribution_curve(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, list[float]]] = {}
    for point in _distribution_points(conn):
        day = point["published"].astimezone(TIMEZONE).date().isoformat()
        item = grouped.setdefault(day, {"views_24h": [], "views_48h": []})
        for metric in ("views_24h", "views_48h"):
            if point.get(metric) is not None:
                item[metric].append(float(point[metric]))
    return [
        {
            "publication_date": day,
            "median_views_24h": median(values["views_24h"]) if values["views_24h"] else None,
            "median_views_48h": median(values["views_48h"]) if values["views_48h"] else None,
            "videos": max(len(values["views_24h"]), len(values["views_48h"])),
        }
        for day, values in sorted(grouped.items())
    ]


def _winner_replacement(conn: sqlite3.Connection, now: datetime) -> tuple[int, int]:
    rows = conn.execute(
        """
        SELECT external_content_id, winner, computed_at,
               ROW_NUMBER() OVER (PARTITION BY external_content_id ORDER BY computed_at DESC, id DESC) AS position
        FROM tiktok_video_scores
        WHERE datetime(computed_at) >= datetime(?, '-8 days')
        """,
        (now.isoformat(),),
    ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["external_content_id"], []).append(row)
    created = 0
    lost = 0
    for history in grouped.values():
        history.sort(key=lambda row: row["computed_at"], reverse=True)
        if len(history) < 2:
            continue
        current, previous = bool(history[0]["winner"]), bool(history[1]["winner"])
        created += int(current and not previous)
        lost += int(previous and not current)
    return created, lost


def _snapshot_quality(
    conn: sqlite3.Connection, video_id: str, values: dict[str, float | int | None]
) -> tuple[str, str | None]:
    previous = conn.execute(
        """
        SELECT * FROM tiktok_video_snapshots
        WHERE external_content_id = ? AND quality_status = 'VALID'
        ORDER BY captured_at DESC, id DESC LIMIT 1
        """,
        (video_id,),
    ).fetchone()
    if not previous:
        return "VALID", None
    anomalies = []
    for metric in CUMULATIVE_METRICS:
        current = values.get(metric)
        before = previous[metric]
        if current is None or before is None or float(before) <= 0:
            continue
        if float(current) < float(before) * 0.95:
            anomalies.append(f"{metric} caiu de {before} para {current}")
    if anomalies:
        return "SUSPICIOUS", "; ".join(anomalies[:4])
    return "VALID", None


def _snapshot_near_offset(
    conn: sqlite3.Connection, current: dict[str, Any], offset_hours: int
) -> dict[str, Any] | None:
    captured = datetime.fromisoformat(current["captured_at"])
    target = captured - timedelta(hours=offset_hours)
    tolerance = 12 if offset_hours == 24 else 18
    rows = conn.execute(
        """
        SELECT s.* FROM tiktok_video_snapshots s
        JOIN tiktok_analytics_runs r ON r.id = s.extraction_run_id
        WHERE s.external_content_id = ? AND s.id <> ? AND s.quality_status = 'VALID'
          AND r.valid = 1 AND r.collection_coverage >= 0.70
          AND datetime(captured_at) < datetime(?)
        ORDER BY captured_at DESC LIMIT 5
        """,
        (current["external_content_id"], current["id"], current["captured_at"]),
    ).fetchall()
    candidates = [
        (abs((datetime.fromisoformat(row["captured_at"]) - target).total_seconds()), dict(row))
        for row in rows
    ]
    if not candidates:
        return None
    distance, selected = min(candidates, key=lambda item: item[0])
    return selected if distance <= tolerance * 3600 else None


def _decay(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> tuple[float | None, str]:
    rows = conn.execute(
        """
        SELECT s.captured_at, s.views, s.orders, s.gmv_minor
        FROM tiktok_video_snapshots s
        JOIN tiktok_analytics_runs r ON r.id = s.extraction_run_id
        WHERE s.external_content_id = ? AND s.quality_status = 'VALID'
          AND r.valid = 1 AND r.collection_coverage >= 0.70
        ORDER BY captured_at
        """,
        (snapshot["external_content_id"],),
    ).fetchall()
    velocities = []
    for before, after in zip(rows, rows[1:]):
        hours = _hours_between(before["captured_at"], after["captured_at"])
        if not hours or hours <= 0:
            continue
        signal = None
        for metric in ("gmv_minor", "orders", "views"):
            delta = _delta(after[metric], before[metric])
            if delta is not None:
                signal = float(delta) * 24 / hours
                if signal > 0:
                    break
        if signal is not None:
            velocities.append(signal)
    if len(rows) < 3 or len(velocities) < 2 or max(velocities[:-1], default=0) <= 0:
        return None, "NO_HISTORY"
    ratio = velocities[-1] / max(velocities[:-1])
    decay_score = round(max(0.0, min(100.0, (1 - ratio) * 100)), 2)
    if ratio >= 1.2:
        status = "ACCELERATING"
    elif ratio >= 0.5:
        status = "STABLE"
    elif ratio >= 0.1:
        status = "DECAYING"
    else:
        status = "DEAD"
    return decay_score, status


def _insert_alert(
    conn: sqlite3.Connection,
    key: str,
    alert_type: str,
    severity: str,
    title: str,
    message: str,
    evidence: dict[str, Any],
    timestamp: str,
    video_id: str | None = None,
    product_id: int | None = None,
) -> int:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO tiktok_analytics_alerts (
            alert_key, alert_type, severity, external_content_id, product_id,
            title, message, evidence_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            alert_type,
            severity,
            video_id,
            product_id,
            title,
            message,
            json.dumps(evidence, ensure_ascii=False, sort_keys=True, default=str),
            timestamp,
        ),
    )
    return int(cursor.rowcount)


def _json_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    for key in ("evidence_json", "metadata_json"):
        if key in result:
            result[key.removesuffix("_json")] = _json_value(result.pop(key), {})
    return result


def _json_value(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _published_within(value: str | None, days: int) -> bool:
    if not value:
        return False
    return datetime.fromisoformat(value) >= datetime.now(timezone.utc) - timedelta(days=days)


def _hours_between(start: str, end: str) -> float:
    start_dt = datetime.fromisoformat(start)
    end_dt = datetime.fromisoformat(end)
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)
    return round((end_dt - start_dt).total_seconds() / 3600, 4)


def _delta(current: Any, previous: Any) -> float | None:
    if current is None or previous is None:
        return None
    return max(0.0, float(current) - float(previous))


def _safe_div(numerator: Any, denominator: Any) -> float | None:
    if numerator is None or denominator is None or float(denominator) == 0:
        return None
    return float(numerator) / float(denominator)


def _per_thousand(value: Any, views: Any) -> float | None:
    ratio = _safe_div(value, views)
    return ratio * 1000 if ratio is not None else None


def _percent(value: Any, base: Any) -> float | None:
    ratio = _safe_div(value, base)
    return ratio * 100 if ratio is not None else None


def _sum_known(*values: Any) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) if clean else None


def _sum_if_any(values: Iterable[Any]) -> float | int | None:
    clean = [value for value in values if value is not None]
    return sum(clean) if clean else None


def _average_known(*values: float | None) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def _median_known(values: Iterable[Any]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return median(clean) if clean else None


def _numeric_or_none(value: Any) -> int | float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return value
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Metrica numerica invalida: {value}") from exc


def _float_or_none(value: Any) -> float | None:
    return float(value) if value not in (None, "") else None


def _int_or_none(value: Any) -> int | None:
    return int(round(float(value))) if value is not None else None


def _text_or_none(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None
