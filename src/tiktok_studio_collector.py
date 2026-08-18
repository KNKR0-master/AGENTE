from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from src.tiktok_analytics import (
    SNAPSHOT_METRICS,
    TIMEZONE,
    begin_extraction_run,
    finish_extraction_run,
    parse_currency_minor,
    parse_duration_seconds,
    parse_localized_number,
    parse_percentage,
    parse_tiktok_datetime,
    persist_video_snapshot,
    recompute_tiktok_analytics,
    upsert_tiktok_video,
)
from src.tiktok_readiness import (
    ReadinessSample,
    TikTokReadinessTracker,
    authenticated_login_handoff_target,
    data_completeness_score,
    is_placeholder,
    percentile,
    should_guard_transient_zero,
)


LOGGER = logging.getLogger("TikTokCollector")
VIDEO_ID_KEYS = {"video_id", "videoid", "item_id", "itemid", "aweme_id", "awemeid", "content_id", "contentid"}
GENERIC_ID_KEYS = {"id", "key"}
VIDEO_CONTEXT_MARKERS = ("video", "item", "aweme", "post", "content")
METRIC_ALIASES = {
    "views": ("play_count", "playcount", "view_count", "viewcount", "views"),
    "likes": ("digg_count", "diggcount", "like_count", "likecount", "likes"),
    "comments": ("comment_count", "commentcount", "comments"),
    "shares": ("share_count", "sharecount", "shares"),
    "saves": ("collect_count", "collectcount", "save_count", "savecount", "saves"),
}
LOADER_SELECTORS = (
    '[aria-busy="true"]',
    '[aria-label*="loading" i]',
    '[data-loading="true"]',
    '[role="progressbar"]',
    '[class*="skeleton" i]',
    '[class*="shimmer" i]',
    '[class*="spinner" i]',
    '[class*="loading" i]',
)
RELEVANT_NETWORK_MARKERS = (
    "analytics",
    "insight",
    "creator/manage/item_list",
    "video",
    "content",
    "performance",
    "affiliate",
    "product",
    "order",
    "gmv",
)
IGNORED_NETWORK_MARKERS = (
    "monitor",
    "telemetry",
    "tracking",
    "heartbeat",
    "red_dot",
    "popup/display",
    "browser-settings",
    "i18n",
    "mcs-",
    "/list",
)
NAVIGATION_ALLOWED_STATES = {"DONE", "READY", "TIMEOUT", "DATA_INCOMPLETE", "UI_CHANGED"}


class TikTokStudioCollectionError(RuntimeError):
    status = "FAILED"
    code = "COLLECTION_FAILED"


class AuthenticationRequired(TikTokStudioCollectionError):
    status = "AUTH_REQUIRED"
    code = "AUTH_REQUIRED"


class InteractionRequired(TikTokStudioCollectionError):
    status = "INTERACTION_REQUIRED"
    code = "INTERACTION_REQUIRED"


class UiChanged(TikTokStudioCollectionError):
    status = "UI_CHANGED"
    code = "UI_CHANGED"


class RateLimited(TikTokStudioCollectionError):
    status = "RATE_LIMITED"
    code = "RATE_LIMITED"


class CoverageCritical(TikTokStudioCollectionError):
    status = "COVERAGE_CRITICAL"
    code = "COLLECTION_COVERAGE_CRITICAL"


class DataIncomplete(TikTokStudioCollectionError):
    status = "FAILED_VALIDATION"
    code = "DATA_INCOMPLETE"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.details = details or {}


class DataReadyTimeout(DataIncomplete):
    code = "DATA_READY_TIMEOUT"


class PrematureNavigationBlocked(DataIncomplete):
    code = "PREMATURE_NAVIGATION_BLOCKED"


@dataclass(frozen=True)
class TikTokStudioCollectorConfig:
    start_url: str
    profile_dir: Path
    mapping_file: Path
    artifacts_dir: Path
    headless: bool
    timeout_seconds: int
    max_retries: int
    locale: str
    account_reference: str | None
    logs_dir: Path | None = None
    last_n_days: int = 30
    full_backfill: bool = False
    network_debug: bool = False
    max_scroll_cycles: int = 500
    stall_cycles: int = 4
    min_expected_posts_per_day: float = 2.0
    partial_coverage_threshold: float = 0.70
    full_coverage_threshold: float = 0.90
    browser_channel: str | None = "chrome"
    max_detail_videos: int = 60
    login_url: str = "https://www.tiktok.com/login?fpm=true"
    collector_mode: str = "SAFE"
    navigation_timeout_seconds: int = 30
    analytics_timeout_seconds: int = 45
    readiness_poll_ms: int = 1000
    readiness_debounce_ms: int = 750
    stable_readings: int = 3
    dom_quiet_ms: int = 1800
    zero_guard_ms: int = 4000
    debug_screenshot_limit: int = 10

    @classmethod
    def from_env(cls, root: Path) -> "TikTokStudioCollectorConfig":
        mode = os.getenv("TIKTOK_COLLECTOR_MODE", "SAFE").strip().upper()
        if mode not in {"SAFE", "NORMAL", "DEBUG"}:
            mode = "SAFE"
        safe_mode = mode in {"SAFE", "DEBUG"}
        return cls(
            start_url=os.getenv("TIKTOK_STUDIO_URL", "https://www.tiktok.com/tiktokstudio/content"),
            profile_dir=Path(os.getenv("TIKTOK_STUDIO_PROFILE_DIR", root / "data" / "tiktok-browser-profile")),
            mapping_file=Path(os.getenv("TIKTOK_STUDIO_MAPPING_FILE", root / "config" / "tiktok_studio_mapping.json")),
            artifacts_dir=Path(os.getenv("TIKTOK_STUDIO_ARTIFACTS_DIR", root / "logs" / "tiktok-collector-errors")),
            logs_dir=Path(os.getenv("TIKTOK_STUDIO_LOGS_DIR", root / "logs")),
            headless=_env_bool("TIKTOK_STUDIO_HEADLESS", False),
            timeout_seconds=max(10, int(os.getenv("TIKTOK_STUDIO_TIMEOUT_SECONDS", "45"))),
            max_retries=max(0, min(5, int(os.getenv("TIKTOK_STUDIO_MAX_RETRIES", "2")))),
            locale=os.getenv("TIKTOK_STUDIO_LOCALE", "pt-BR"),
            account_reference=os.getenv("TIKTOK_STUDIO_ACCOUNT_REFERENCE") or None,
            last_n_days=max(1, int(os.getenv("TIKTOK_STUDIO_LAST_N_DAYS", "30"))),
            full_backfill=_env_bool("TIKTOK_STUDIO_FULL_BACKFILL", False),
            network_debug=_env_bool("TIKTOK_NETWORK_DEBUG", False),
            max_scroll_cycles=max(10, int(os.getenv("TIKTOK_STUDIO_MAX_SCROLL_CYCLES", "500"))),
            stall_cycles=max(2, int(os.getenv("TIKTOK_STUDIO_STALL_CYCLES", "4"))),
            min_expected_posts_per_day=max(0.0, float(os.getenv("TIKTOK_STUDIO_MIN_EXPECTED_POSTS_PER_DAY", "2"))),
            browser_channel=os.getenv("TIKTOK_STUDIO_BROWSER_CHANNEL", "chrome").strip() or None,
            max_detail_videos=max(0, int(os.getenv("TIKTOK_STUDIO_MAX_DETAIL_VIDEOS", "60"))),
            login_url=os.getenv("TIKTOK_STUDIO_LOGIN_URL", "https://www.tiktok.com/login?fpm=true"),
            collector_mode=mode,
            navigation_timeout_seconds=max(10, int(os.getenv("TIKTOK_NAVIGATION_TIMEOUT_SECONDS", "30"))),
            analytics_timeout_seconds=max(15, int(os.getenv("TIKTOK_ANALYTICS_TIMEOUT_SECONDS", "45"))),
            readiness_poll_ms=max(250, int(os.getenv("TIKTOK_READINESS_POLL_MS", "1000" if safe_mode else "750"))),
            readiness_debounce_ms=max(250, int(os.getenv("TIKTOK_READINESS_DEBOUNCE_MS", "750"))),
            stable_readings=max(2, int(os.getenv("TIKTOK_STABLE_READINGS", "3" if safe_mode else "2"))),
            dom_quiet_ms=max(500, int(os.getenv("TIKTOK_DOM_QUIET_MS", "1800" if safe_mode else "1200"))),
            zero_guard_ms=max(1500, int(os.getenv("TIKTOK_ZERO_GUARD_MS", "4000"))),
            debug_screenshot_limit=max(0, int(os.getenv("TIKTOK_DEBUG_SCREENSHOT_LIMIT", "10"))),
        )


@dataclass
class DiscoveryResult:
    videos: list[dict[str, Any]]
    discovery_method: str
    strategies: dict[str, int]
    expected_videos: int | None
    coverage_confidence: str
    expected_posts_per_day: float | None
    total_publications: int | None
    period_boundary_reached: bool
    observed_rows: int
    scroll_cycles: int


@dataclass
class PageReadinessResult:
    page_name: str
    state: str
    extraction_status: str
    metrics: dict[str, Any]
    raw_values: dict[str, Any]
    metric_sources: dict[str, str]
    expected_metrics: list[str]
    required_metrics: list[str]
    missing_metrics: list[str]
    placeholders: list[str]
    data_completeness_score: float
    load_duration_ms: int
    stable_readings: int
    pending_relevant_requests: int
    relevant_response_received: bool
    loaders_visible: int
    retry_count: int
    state_history: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "page_name": self.page_name,
            "state": self.state,
            "extraction_status": self.extraction_status,
            "expected_metrics": self.expected_metrics,
            "required_metrics": self.required_metrics,
            "missing_metrics": self.missing_metrics,
            "placeholders": self.placeholders,
            "data_completeness_score": self.data_completeness_score,
            "load_duration_ms": self.load_duration_ms,
            "stable_readings": self.stable_readings,
            "pending_relevant_requests": self.pending_relevant_requests,
            "relevant_response_received": self.relevant_response_received,
            "loaders_visible": self.loaders_visible,
            "retry_count": self.retry_count,
            "state_history": self.state_history,
        }


class TikTokStudioBrowserCollector:
    """Collect the authenticated Studio UI without automating login or challenges."""

    def __init__(self, config: TikTokStudioCollectorConfig) -> None:
        self.config = config
        self._network: dict[str, Any] = self._new_network_state()
        self._page_state = "DONE"
        self._state_context = "startup"
        self._state_history: list[dict[str, Any]] = []
        self._navigation_started_monotonic = 0.0
        self._debug_screenshots_remaining = config.debug_screenshot_limit

    def setup_session(self) -> dict[str, Any]:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise TikTokStudioCollectionError(
                "Playwright nao instalado. Execute: pip install playwright && python -m playwright install chromium"
            ) from exc
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = self._launch_context(playwright, headless=False)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                page.goto(self.config.login_url, wait_until="domcontentloaded")
                input("Conclua o login manual no TikTok Studio e pressione ENTER aqui para validar a sessao: ")
                page.goto(self.config.start_url, wait_until="domcontentloaded")
                self._check_page_state(page)
                return {"status": "SUCCESS", "url": page.url, "profile_dir": str(self.config.profile_dir)}
            except AuthenticationRequired:
                return {"status": "AUTH_REQUIRED", "url": page.url}
            finally:
                try:
                    context.close()
                except Exception:
                    pass

    def collect(
        self, conn, *, trigger_type: str = "MANUAL", force: bool = False,
        last_n_days: int | None = None, full_backfill: bool | None = None,
    ) -> dict[str, Any]:
        days = max(1, int(last_n_days or self.config.last_n_days))
        backfill = self.config.full_backfill if full_backfill is None else bool(full_backfill)
        run_date = datetime.now(TIMEZONE).date().isoformat()
        scope_key = "full" if backfill else f"{days}d"
        run = begin_extraction_run(
            conn, f"tiktok-studio-daily:{run_date}:{scope_key}", trigger_type=trigger_type,
            account_reference=self.config.account_reference, page_url=self.config.start_url,
            last_n_days=None if backfill else days, full_backfill=backfill, force=force,
        )
        if run["status"] != "RUNNING":
            return {"run": run, "idempotent_skip": True}
        self._log(f"Starting run {run['id']} ({scope_key})")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            return self._finish_without_browser(conn, run, exc)

        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        self.config.artifacts_dir.mkdir(parents=True, exist_ok=True)
        (self.config.logs_dir or self.config.artifacts_dir.parent).mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = None
            page = None
            try:
                context = self._launch_context(playwright, headless=self.config.headless)
                context.set_default_timeout(self.config.timeout_seconds * 1000)
                page = context.pages[0] if context.pages else context.new_page()
                self._network = self._new_network_state()
                self._page_state = "DONE"
                self._state_history = []
                self._debug_screenshots_remaining = self.config.debug_screenshot_limit
                self._attach_network_observer(page)
                mapping = self._load_mapping()
                self._open_catalog_ready(page, mapping)
                self._log("Authenticated")

                discovery = self._discover_videos(conn, page, mapping, days=days, full_backfill=backfill)
                if not discovery.videos:
                    raise UiChanged("Nenhuma estrategia encontrou video na interface autenticada")
                known = {
                    row[0] for row in conn.execute(
                        "SELECT external_content_id FROM external_tiktok_contents WHERE external_content_type = 'VIDEO'"
                    ).fetchall()
                }
                new_count = sum(video["video_id"] not in known for video in discovery.videos)
                discovery_coverage = _coverage(len(discovery.videos), discovery.expected_videos)
                self._log(f"Discovery method: {discovery.discovery_method}")
                self._log(f"Found {len(discovery.videos)} unique videos")
                if discovery_coverage is not None and discovery_coverage < self.config.partial_coverage_threshold:
                    for video in discovery.videos:
                        upsert_tiktok_video(conn, video)
                    raise CoverageCritical(
                        f"Descoberta cobriu {discovery_coverage:.1%}: {len(discovery.videos)} de "
                        f"{discovery.expected_videos} videos esperados"
                    )

                payloads: list[dict[str, Any]] = []
                failures: list[dict[str, Any]] = []
                detail_enriched = 0
                for index, video in enumerate(self._prioritize_videos(conn, discovery.videos)):
                    try:
                        has_catalog_metrics = any(video.get(metric) is not None for metric in SNAPSHOT_METRICS)
                        if index < self.config.max_detail_videos or not has_catalog_metrics:
                            payloads.append(self._collect_video_data(page, mapping, video))
                            detail_enriched += 1
                        else:
                            payloads.append(self._catalog_payload(video))
                    except (AuthenticationRequired, InteractionRequired, RateLimited):
                        raise
                    except Exception as exc:
                        details = getattr(exc, "details", {})
                        failures.append({
                            "video_id": video["video_id"],
                            "status": getattr(exc, "code", "DATA_INCOMPLETE"),
                            "error": str(exc)[:500],
                            "load_duration_ms": int(details.get("load_duration_ms") or 0),
                            "retry_count": self.config.max_retries,
                            "details": details,
                        })

                collected_coverage = _coverage(len(payloads), discovery.expected_videos)
                if collected_coverage is None:
                    collected_coverage = 1.0 if payloads else 0.0
                counts = {
                    "videos_discovered": len(discovery.videos), "videos_collected": len(payloads),
                    "snapshots_created": 0, "videos_failed": len(failures),
                    "new_videos_discovered": new_count,
                }
                coverage = {
                    "videos_expected": discovery.expected_videos,
                    "collection_coverage": collected_coverage,
                    "coverage_confidence": discovery.coverage_confidence,
                }
                metadata = self._run_metadata(discovery, failures)
                metadata["detail_videos_collected"] = detail_enriched
                metadata["catalog_only_videos"] = len(payloads) - detail_enriched
                quality_summary = self._quality_summary(payloads, failures)
                metadata["quality_summary"] = quality_summary
                if collected_coverage < self.config.partial_coverage_threshold:
                    for video in discovery.videos:
                        upsert_tiktok_video(conn, video)
                    report = self._write_network_report("coverage-critical", page, metadata, force=True)
                    finish_extraction_run(
                        conn, int(run["id"]), "COVERAGE_CRITICAL", current_step="COVERAGE_VALIDATION",
                        page_url=page.url, error_code="COLLECTION_COVERAGE_CRITICAL",
                        error_message=f"Cobertura {collected_coverage:.1%} abaixo do minimo de {self.config.partial_coverage_threshold:.0%}",
                        counts=counts, coverage=coverage, discovery_method=discovery.discovery_method,
                        valid=False, debug_report_path=str(report) if report else None, metadata=metadata,
                        quality_summary=quality_summary,
                    )
                    return {"run_id": run["id"], "status": "COVERAGE_CRITICAL", "counts": counts, "coverage": coverage,
                            "quality": quality_summary}

                complete_denominator = max(1, detail_enriched)
                complete_rate = quality_summary["videos_complete"] / complete_denominator
                timeout_rate = quality_summary["videos_timeout"] / max(1, len(discovery.videos))
                quality_gate_passed = (
                    complete_rate >= self.config.partial_coverage_threshold
                    and quality_summary["average_completeness"] >= 60
                    and timeout_rate <= 0.30
                )
                metadata["quality_gate"] = {
                    "passed": quality_gate_passed,
                    "complete_rate": round(complete_rate, 4),
                    "timeout_rate": round(timeout_rate, 4),
                    "minimum_complete_rate": self.config.partial_coverage_threshold,
                    "minimum_average_completeness": 60,
                    "maximum_timeout_rate": 0.30,
                }
                if not quality_gate_passed:
                    for video in discovery.videos:
                        upsert_tiktok_video(conn, video)
                    report = self._write_network_report("quality-gate-failed", page, metadata, force=True)
                    finish_extraction_run(
                        conn, int(run["id"]), "FAILED_VALIDATION", current_step="DATA_QUALITY_VALIDATION",
                        page_url=page.url, error_code="DATA_QUALITY_GATE_FAILED",
                        error_message=(
                            f"Completos {complete_rate:.1%}; timeouts {timeout_rate:.1%}; "
                            f"completude media {quality_summary['average_completeness']:.1f}%"
                        ),
                        counts=counts, coverage=coverage, discovery_method=discovery.discovery_method,
                        valid=False, debug_report_path=str(report) if report else None, metadata=metadata,
                        quality_summary=quality_summary,
                    )
                    return {"run_id": run["id"], "status": "FAILED_VALIDATION", "counts": counts,
                            "coverage": coverage, "quality": quality_summary}

                overall_quality = "VALID" if collected_coverage >= self.config.full_coverage_threshold else "PARTIAL"
                for payload in payloads:
                    video_id = upsert_tiktok_video(conn, payload["video"])
                    snapshot = persist_video_snapshot(
                        conn, int(run["id"]), video_id, payload["metrics"],
                        captured_at=payload["captured_at"], page_url=payload.get("page_url"),
                        raw_values=payload.get("raw_values"), metric_sources=payload.get("metric_sources"),
                        source="STUDIO_VIDEO_CRAWLER",
                        quality_status_override="PARTIAL" if payload.get("partial") or overall_quality == "PARTIAL" else None,
                        extraction_status=payload.get("extraction_status") or "PARTIAL",
                        data_completeness_score=payload.get("data_completeness_score"),
                        load_duration_ms=payload.get("load_duration_ms"),
                        retry_count=payload.get("retry_count", 0),
                        readiness=payload.get("readiness"),
                    )
                    counts["snapshots_created"] += int(bool(snapshot))
                status = "SUCCESS" if collected_coverage >= self.config.full_coverage_threshold else "PARTIAL_SUCCESS"
                report = self._write_network_report("discovery", page, metadata) if self.config.network_debug else None
                finish_extraction_run(
                    conn, int(run["id"]), status, current_step="ANALYTICS_COMPLETE", page_url=page.url,
                    counts=counts, coverage=coverage, discovery_method=discovery.discovery_method,
                    valid=True, debug_report_path=str(report) if report else None, metadata=metadata,
                    quality_summary=quality_summary,
                )
                analytics = recompute_tiktok_analytics(conn)
                self._log(f"Collected {counts['videos_collected']}")
                self._log(f"Failed {counts['videos_failed']}")
                self._log(f"Coverage {collected_coverage:.1%}")
                self._log(f"Persisted {counts['snapshots_created']} snapshots")
                self._log(
                    f"Complete {quality_summary['videos_complete']}; partial {quality_summary['videos_partial']}; "
                    f"timeouts {quality_summary['videos_timeout']}"
                )
                self._log(
                    f"Average load {quality_summary['average_load_ms'] / 1000:.1f}s; "
                    f"p95 {quality_summary['p95_load_ms'] / 1000:.1f}s; "
                    f"average completeness {quality_summary['average_completeness']:.1f}%"
                )
                return {"run_id": run["id"], "status": status, "counts": counts, "coverage": coverage,
                        "quality": quality_summary, "analytics": analytics}
            except TikTokStudioCollectionError as exc:
                discovery = locals().get("discovery")
                counts = locals().get("counts") or {
                    "videos_discovered": len(discovery.videos) if discovery else 0,
                    "videos_collected": 0, "snapshots_created": 0, "videos_failed": 0,
                    "new_videos_discovered": locals().get("new_count", 0),
                }
                coverage = locals().get("coverage") or {
                    "videos_expected": getattr(discovery, "expected_videos", None),
                    "collection_coverage": locals().get("discovery_coverage"),
                    "coverage_confidence": getattr(discovery, "coverage_confidence", "LOW"),
                }
                artifact = self._failure_artifacts(page, int(run["id"]), exc.code, exc)
                finish_extraction_run(
                    conn, int(run["id"]), exc.status, current_step="BROWSER_COLLECTION",
                    page_url=page.url if page else self.config.start_url,
                    screenshot_path=artifact.get("screenshot"), debug_report_path=artifact.get("report"),
                    error_code=exc.code, error_message=str(exc), counts=counts, coverage=coverage,
                    discovery_method=getattr(discovery, "discovery_method", None), valid=False,
                    metadata=self._run_metadata(discovery, locals().get("failures", [])),
                    quality_summary=locals().get("quality_summary"),
                )
                return {"run_id": run["id"], "status": exc.status, "error": str(exc), "counts": counts, "coverage": coverage}
            except KeyboardInterrupt:
                finish_extraction_run(
                    conn, int(run["id"]), "FAILED", current_step="INTERRUPTED",
                    page_url=page.url if page else self.config.start_url,
                    error_code="INTERRUPTED", error_message="Coleta interrompida antes da persistencia",
                    valid=False, metadata=self._run_metadata(locals().get("discovery"), locals().get("failures", [])),
                    quality_summary=locals().get("quality_summary"),
                )
                return {"run_id": run["id"], "status": "FAILED", "error": "Coleta interrompida"}
            except Exception as exc:
                artifact = self._failure_artifacts(page, int(run["id"]), "FAILED", exc)
                finish_extraction_run(
                    conn, int(run["id"]), "FAILED", current_step="BROWSER_COLLECTION",
                    page_url=page.url if page else self.config.start_url,
                    screenshot_path=artifact.get("screenshot"), debug_report_path=artifact.get("report"),
                    error_code=type(exc).__name__, error_message=str(exc), valid=False,
                )
                return {"run_id": run["id"], "status": "FAILED", "error": str(exc)}
            finally:
                if context:
                    try:
                        context.close()
                    except Exception:
                        pass

    def remap(self, *, last_n_days: int | None = None) -> dict[str, Any]:
        """Inspect the UI and network without modifying production data."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise TikTokStudioCollectionError("Playwright nao instalado") from exc
        days = max(1, int(last_n_days or self.config.last_n_days))
        self.config.profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as playwright:
            context = self._launch_context(playwright, headless=self.config.headless)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._network = self._new_network_state()
                self._page_state = "DONE"
                self._state_history = []
                self._debug_screenshots_remaining = self.config.debug_screenshot_limit
                self._attach_network_observer(page)
                mapping = self._load_mapping()
                self._open_catalog_ready(page, mapping)
                discovery = self._discover_videos(None, page, mapping, days=days, full_backfill=False)
                report = self._write_network_report("remap", page, self._run_metadata(discovery, []), force=True)
                screenshot = self._screenshot(page, "remap")
                return {
                    "status": "SUCCESS" if discovery.videos else "UI_CHANGED",
                    "production_data_modified": False, "videos_discovered": len(discovery.videos),
                    "discovery_method": discovery.discovery_method, "strategies": discovery.strategies,
                    "report": str(report) if report else None,
                    "screenshot": str(screenshot) if screenshot else None,
                }
            except TikTokStudioCollectionError as exc:
                artifact = self._failure_artifacts(page if "page" in locals() else None, 0, f"remap-{exc.code}", exc)
                return {
                    "status": exc.status, "production_data_modified": False,
                    "error": str(exc), "report": artifact.get("report"),
                    "screenshot": artifact.get("screenshot"),
                }
            except Exception as exc:
                artifact = self._failure_artifacts(page if "page" in locals() else None, 0, "remap-failed", exc)
                return {
                    "status": "FAILED", "production_data_modified": False,
                    "error": str(exc), "report": artifact.get("report"),
                    "screenshot": artifact.get("screenshot"),
                }
            finally:
                context.close()

    def validate_videos(self, video_ids: list[str]) -> dict[str, Any]:
        """Collect a small explicit sample without writing runs or snapshots."""
        ids = list(dict.fromkeys(filter(None, (_valid_video_id(value) for value in video_ids))))
        if not ids:
            raise ValueError("Informe ao menos um TikTok Video ID valido")
        if len(ids) > 10:
            raise ValueError("A validacao assistida aceita no maximo 10 videos")
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise TikTokStudioCollectionError("Playwright nao instalado") from exc
        with sync_playwright() as playwright:
            context = self._launch_context(playwright, headless=self.config.headless)
            try:
                page = context.pages[0] if context.pages else context.new_page()
                self._network = self._new_network_state()
                self._page_state = "DONE"
                self._state_history = []
                self._debug_screenshots_remaining = self.config.debug_screenshot_limit
                self._attach_network_observer(page)
                mapping = self._load_mapping()
                self._open_catalog_ready(page, mapping)
                rows = []
                for video_id in ids:
                    payload = self._collect_video_data(
                        page, mapping,
                        {"video_id": video_id, "video_url": f"https://www.tiktok.com/video/{video_id}", "source": "VALIDATION_SAMPLE"},
                    )
                    rows.append({
                        "video_id": video_id, "metrics": payload["metrics"],
                        "metric_sources": payload["metric_sources"], "partial": payload["partial"],
                        "extraction_status": payload.get("extraction_status"),
                        "data_completeness_score": payload.get("data_completeness_score"),
                        "load_duration_ms": payload.get("load_duration_ms"),
                        "retry_count": payload.get("retry_count"),
                        "readiness": payload.get("readiness"),
                        "page_url": _sanitize_url(payload.get("page_url") or ""),
                    })
                stamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d_%H%M%S")
                report = self._write_json(
                    (self.config.logs_dir or self.config.artifacts_dir.parent) / f"tiktok-five-video-validation-{stamp}.json",
                    {"captured_at": now_local_iso(), "production_data_modified": False, "videos": rows},
                )
                return {"status": "SUCCESS", "production_data_modified": False, "videos": rows, "report": str(report) if report else None}
            except TikTokStudioCollectionError as exc:
                artifact = self._failure_artifacts(page if "page" in locals() else None, 0, f"validation-{exc.code}", exc)
                return {"status": exc.status, "production_data_modified": False, "error": str(exc), **artifact}
            finally:
                context.close()

    def _finish_without_browser(self, conn, run: dict[str, Any], exc: Exception) -> dict[str, Any]:
        finish_extraction_run(
            conn, int(run["id"]), "FAILED", current_step="BROWSER_START",
            error_code=type(exc).__name__, error_message=str(exc), valid=False,
        )
        return {"run_id": run["id"], "status": "FAILED", "error": str(exc)}

    def _launch_context(self, playwright, *, headless: bool):
        kwargs = {
            "headless": headless,
            "locale": self.config.locale,
            "timezone_id": "America/Sao_Paulo",
        }
        if self.config.browser_channel:
            kwargs["channel"] = self.config.browser_channel
        return playwright.chromium.launch_persistent_context(str(self.config.profile_dir), **kwargs)

    def _load_mapping(self) -> dict[str, Any]:
        if not self.config.mapping_file.exists():
            raise UiChanged(f"Mapeamento nao encontrado: {self.config.mapping_file}")
        try:
            mapping = json.loads(self.config.mapping_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise UiChanged(f"Arquivo de mapeamento invalido: {exc}") from exc
        if not isinstance(mapping.get("catalog"), dict) or not isinstance(mapping.get("detail"), dict):
            raise UiChanged("Mapeamento deve conter catalog e detail")
        return mapping

    @staticmethod
    def _new_network_state() -> dict[str, Any]:
        return {
            "responses": [],
            "videos": {},
            "pending_relevant": {},
            "relevant_responses": [],
            "request_sequence": 0,
        }

    def _transition(self, state: str, context: str, **details: Any) -> None:
        previous = self._page_state
        self._page_state = state
        self._state_context = context
        event = {
            "at": now_local_iso(),
            "state": state,
            "context": context,
            **{key: value for key, value in details.items() if value is not None},
        }
        self._state_history.append(event)
        self._state_history = self._state_history[-2500:]
        if state != previous:
            self._log(f"State {previous} -> {state} ({context})")

    def _guarded_goto(self, page, url: str, *, context: str, expected_video_id: str | None = None) -> float:
        if self._page_state not in NAVIGATION_ALLOWED_STATES:
            self._log(
                f"PREMATURE_NAVIGATION_BLOCKED state={self._page_state} context={self._state_context} next={context}"
            )
            raise PrematureNavigationBlocked(
                f"Navegacao para {context} bloqueada enquanto a pagina estava em {self._page_state}",
                details={"state": self._page_state, "context": self._state_context, "next_context": context},
            )
        self._transition("NAVIGATING", context, expected_video_id=expected_video_id)
        opened = time.monotonic()
        self._navigation_started_monotonic = opened
        page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.config.navigation_timeout_seconds * 1000,
        )
        self._check_page_state(page)
        elapsed_ms = int((time.monotonic() - opened) * 1000)
        self._transition("DOM_READY", context, dom_ready_ms=elapsed_ms)
        self._log(f"DOM ready in {elapsed_ms / 1000:.1f}s ({context})")
        self._debug_readiness_screenshot(page, context, "opened")
        return opened

    def _guard_interaction(self, context: str) -> None:
        if self._page_state not in NAVIGATION_ALLOWED_STATES:
            self._log(
                f"PREMATURE_NAVIGATION_BLOCKED state={self._page_state} context={self._state_context} next={context}"
            )
            raise PrematureNavigationBlocked(
                f"Interacao {context} bloqueada enquanto a pagina estava em {self._page_state}",
                details={"state": self._page_state, "context": self._state_context, "next_context": context},
            )
        self._transition("WAITING_FOR_DATA", context)

    def _debug_readiness_screenshot(self, page, context: str, phase: str) -> Path | None:
        if self._debug_screenshots_remaining <= 0:
            return None
        if self.config.collector_mode != "DEBUG" and not self.config.network_debug:
            return None
        self._debug_screenshots_remaining -= 1
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "-", context).strip("-")[:80] or "page"
        stamp = datetime.now(TIMEZONE).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        return self._screenshot(page, f"readiness-{stamp}-{safe}-{phase}")

    def _is_relevant_request(self, request) -> bool:
        try:
            if str(request.resource_type).lower() not in {"xhr", "fetch"}:
                return False
            lowered = str(request.url).lower()
            if any(marker in lowered for marker in IGNORED_NETWORK_MARKERS):
                return False
            return any(marker in lowered for marker in RELEVANT_NETWORK_MARKERS)
        except Exception:
            return False

    def _pending_relevant_requests(self, started_at: float) -> int:
        return sum(
            float(record.get("started_monotonic") or 0) >= started_at
            for record in self._network.get("pending_relevant", {}).values()
        )

    def _relevant_response_received(self, started_at: float) -> bool:
        return any(
            float(record.get("received_monotonic") or 0) >= started_at
            for record in self._network.get("relevant_responses", [])
        )

    def _visible_loader_count(self, page, scope_selector: str | None = None) -> int:
        total = 0
        for selector in LOADER_SELECTORS:
            try:
                locator = page.locator(selector)
                for index in range(min(locator.count(), 100)):
                    node = locator.nth(index)
                    if not node.is_visible():
                        continue
                    if scope_selector:
                        inside = node.evaluate(
                            "(el, selector) => { const scope = document.querySelector(selector); return !scope || scope.contains(el); }",
                            scope_selector,
                        )
                        if not inside:
                            continue
                    total += 1
            except Exception:
                continue
        return total

    def _selector_visible(self, page, selector: str | None) -> bool:
        if not selector:
            return True
        try:
            locator = page.locator(selector)
            return any(locator.nth(index).is_visible() for index in range(min(locator.count(), 25)))
        except Exception:
            return False

    def _current_video_ids(self, page, catalog: dict[str, Any]) -> set[str]:
        ids = set(self._network.get("videos", {}))
        ids.update(video["video_id"] for video in self._video_links(page) if video.get("video_id"))
        ids.update(video["video_id"] for video in self._semantic_video_attributes(page) if video.get("video_id"))
        rows, _, _ = self._mapped_rows(page, catalog)
        ids.update(video["video_id"] for video in rows if video.get("video_id"))
        return ids

    def wait_for_tiktok_data_ready(
        self,
        page,
        *,
        page_name: str,
        page_mapping: dict[str, Any],
        expected_video_id: str | None = None,
        kind: str = "metrics",
        attempt: int = 0,
        opened_at: float | None = None,
    ) -> PageReadinessResult:
        """Wait for real, stable TikTok data instead of trusting navigation events."""
        history_start = len(self._state_history)
        started_at = opened_at or self._navigation_started_monotonic or time.monotonic()
        timeout_ladder = (15, 25, 40)
        default_timeout = timeout_ladder[min(attempt, len(timeout_ladder) - 1)]
        timeout_seconds = min(
            self.config.analytics_timeout_seconds,
            int(page_mapping.get("readiness_timeout_seconds") or default_timeout),
        )
        deadline = time.monotonic() + timeout_seconds
        tracker = TikTokReadinessTracker(
            required_stable_readings=self.config.stable_readings,
            dom_quiet_ms=self.config.dom_quiet_ms,
        )
        metric_mapping = page_mapping.get("metrics") or {}
        expected_metrics = list(page_mapping.get("expected_metrics") or metric_mapping.keys())
        required_metrics = list(page_mapping.get("required_metrics") or expected_metrics)
        allow_unavailable = bool(page_mapping.get("allow_terminal_unavailable"))
        terminal_texts = [str(value).lower() for value in page_mapping.get("terminal_unavailable_texts") or []]
        ready_selector = page_mapping.get("ready_selector")
        scope_selector = page_mapping.get("stability_selector") or ready_selector
        last_signature: tuple[Any, ...] | None = None
        detected_logged = False
        latest_metrics: dict[str, Any] = {}
        latest_raw: dict[str, Any] = {}
        latest_sources: dict[str, str] = {}
        latest_missing: list[str] = []
        latest_placeholders: list[str] = []
        latest_loaders = 0
        latest_pending = 0
        latest_decision = None
        terminal_unavailable = False
        zero_guard_logged = False

        page.wait_for_timeout(self.config.readiness_debounce_ms)
        self._transition("WAITING_FOR_DATA", page_name, attempt=attempt + 1)
        self._log(f"Waiting for TikTok data ({page_name}, attempt {attempt + 1})")
        while time.monotonic() < deadline:
            self._check_page_state(page)
            now_ms = int(time.monotonic() * 1000)
            latest_loaders = self._visible_loader_count(page, scope_selector)
            latest_pending = self._pending_relevant_requests(started_at)
            correct_video = not expected_video_id or _video_id_from_url(page.url) == expected_video_id
            terminal_unavailable = False

            if kind == "catalog":
                ids = self._current_video_ids(page, page_mapping)
                body = self._page_body(page).lower()
                empty_texts = [str(value).lower() for value in page_mapping.get("empty_state_texts") or []]
                empty_state = any(text in body for text in empty_texts)
                data_detected = self._selector_visible(page, ready_selector) and (bool(ids) or empty_state)
                signature = tuple(sorted(ids)) + (("EMPTY",) if empty_state else ())
                latest_metrics = {"video_ids": len(ids)}
                latest_raw = {"video_ids": sorted(ids)}
                latest_sources = {"video_ids": "STUDIO_CATALOG_READINESS"}
                latest_missing = [] if data_detected else ["video_ids"]
                latest_placeholders = []
            else:
                latest_metrics, latest_raw, latest_sources = self._extract_metrics(page, metric_mapping)
                latest_missing = [metric for metric in required_metrics if latest_metrics.get(metric) is None]
                latest_placeholders = [
                    metric for metric in expected_metrics
                    if latest_metrics.get(metric) is None and is_placeholder(latest_raw.get(metric))
                ]
                if allow_unavailable and terminal_texts:
                    body = self._page_body(page).lower()
                    terminal_unavailable = any(text in body for text in terminal_texts)
                data_detected = self._selector_visible(page, ready_selector) and (
                    not latest_missing or terminal_unavailable
                )
                signature = tuple((metric, latest_metrics.get(metric), latest_raw.get(metric)) for metric in expected_metrics)

            relevant_response_received = self._relevant_response_received(started_at)
            zero_guard_active = False
            if kind == "metrics" and not relevant_response_received:
                zero_guard_metrics = list(page_mapping.get("zero_guard_metrics") or ["views"])
                zero_guard_active = should_guard_transient_zero(
                    latest_metrics,
                    zero_guard_metrics,
                    relevant_response_received=relevant_response_received,
                    elapsed_ms=int((time.monotonic() - started_at) * 1000),
                    guard_ms=self.config.zero_guard_ms,
                )
                if zero_guard_active and not zero_guard_logged:
                    self._log(f"Transient zero guard active ({page_name})")
                    zero_guard_logged = True

            sample = ReadinessSample(
                signature=signature,
                data_detected=data_detected,
                loaders_visible=latest_loaders,
                pending_requests=latest_pending + int(zero_guard_active),
                correct_video=correct_video,
            )
            decision = tracker.observe(sample, now_ms)
            latest_decision = decision
            if data_detected and not detected_logged:
                self._transition("DATA_DETECTED", page_name, attempt=attempt + 1)
                if kind == "metrics":
                    summary = ", ".join(
                        f"{metric}={latest_metrics.get(metric)}"
                        for metric in required_metrics
                        if latest_metrics.get(metric) is not None
                    )
                    self._log(f"Values detected: {summary or 'terminal availability state'}")
                else:
                    self._log(f"Video IDs detected: {latest_metrics.get('video_ids', 0)}")
                detected_logged = True
            if decision.changed and last_signature is not None and data_detected:
                self._log(f"Values changed; waiting again ({page_name})")
            last_signature = signature
            self._transition(decision.state, page_name, stable_readings=decision.stable_readings)
            if decision.ready:
                completeness = data_completeness_score(latest_metrics, expected_metrics)
                duration_ms = int((time.monotonic() - started_at) * 1000)
                extraction_status = "DATA_UNAVAILABLE" if terminal_unavailable and latest_missing else "COMPLETE"
                self._log(
                    f"Stable after {duration_ms / 1000:.1f}s; completeness {completeness:.1f}% ({page_name})"
                )
                self._debug_readiness_screenshot(page, page_name, "data-ready")
                return PageReadinessResult(
                    page_name=page_name,
                    state="READY",
                    extraction_status=extraction_status,
                    metrics=latest_metrics,
                    raw_values=latest_raw,
                    metric_sources=latest_sources,
                    expected_metrics=expected_metrics,
                    required_metrics=required_metrics,
                    missing_metrics=[metric for metric in expected_metrics if latest_metrics.get(metric) is None],
                    placeholders=latest_placeholders,
                    data_completeness_score=completeness,
                    load_duration_ms=duration_ms,
                    stable_readings=decision.stable_readings,
                    pending_relevant_requests=latest_pending,
                    relevant_response_received=relevant_response_received,
                    loaders_visible=latest_loaders,
                    retry_count=attempt,
                    state_history=self._state_history[history_start:],
                )
            page.wait_for_timeout(self.config.readiness_poll_ms)

        duration_ms = int((time.monotonic() - started_at) * 1000)
        self._transition("TIMEOUT", page_name, attempt=attempt + 1, load_duration_ms=duration_ms)
        details = {
            "page_name": page_name,
            "expected_video_id": expected_video_id,
            "current_video_id": _video_id_from_url(page.url),
            "missing_metrics": latest_missing,
            "placeholders": latest_placeholders,
            "loaders_visible": latest_loaders,
            "pending_relevant_requests": latest_pending,
            "relevant_response_received": self._relevant_response_received(started_at),
            "stable_readings": latest_decision.stable_readings if latest_decision else 0,
            "load_duration_ms": duration_ms,
            "state_history": self._state_history[history_start:],
        }
        raise DataReadyTimeout(
            f"Dados de {page_name} nao ficaram prontos em {timeout_seconds}s",
            details=details,
        )

    def _open_catalog_ready(self, page, mapping: dict[str, Any]) -> PageReadinessResult:
        catalog = dict(mapping["catalog"])
        catalog.setdefault("ready_selector", catalog.get("row_selector"))
        catalog.setdefault("readiness_timeout_seconds", self.config.analytics_timeout_seconds)
        opened = self._guarded_goto(
            page,
            catalog.get("url") or self.config.start_url,
            context="video-catalog",
        )
        result = self.wait_for_tiktok_data_ready(
            page,
            page_name="video-catalog",
            page_mapping=catalog,
            kind="catalog",
            opened_at=opened,
        )
        self._transition("DONE", "video-catalog")
        return result

    def _check_page_state(self, page) -> None:
        url = str(page.url).lower()
        body = self._page_body(page).lower()
        handoff_target = authenticated_login_handoff_target(page.url, body)
        if handoff_target:
            self._log("Authenticated TikTok login handoff detected; waiting for Studio redirect")
            try:
                page.wait_for_url(
                    lambda current: "/login" not in str(current).lower(),
                    timeout=min(15000, self.config.navigation_timeout_seconds * 1000),
                )
            except Exception:
                if "/login" in str(page.url).lower():
                    page.goto(
                        handoff_target,
                        wait_until="domcontentloaded",
                        timeout=self.config.navigation_timeout_seconds * 1000,
                    )
            url = str(page.url).lower()
            body = self._page_body(page).lower()
            if "/login" not in url:
                self._log("Authenticated TikTok login handoff completed")
        if "/login" in url or any(marker in body for marker in (
            "entrar no tiktok", "log in to tiktok", "fazer login", "scan qr code", "session expired",
        )):
            raise AuthenticationRequired("Sessao TikTok ausente ou expirada. Faca login manual no perfil persistente.")
        if any(marker in body for marker in (
            "captcha", "verifique se voce e humano", "verify you are human", "security verification", "arraste o controle",
        )):
            raise InteractionRequired("CAPTCHA ou desafio encontrado. Resolva manualmente; nenhum bypass sera tentado.")
        if any(marker in body for marker in ("muitas solicitacoes", "too many requests", "try again later")):
            raise RateLimited("TikTok limitou temporariamente a coleta")

    def _attach_network_observer(self, page) -> None:
        def request_key(request) -> str:
            implementation = getattr(request, "_impl_obj", None)
            return str(getattr(implementation, "_guid", None) or id(request))

        def started(request) -> None:
            if not self._is_relevant_request(request):
                return
            self._network["request_sequence"] += 1
            self._network["pending_relevant"][request_key(request)] = {
                "url": _sanitize_url(request.url),
                "started_monotonic": time.monotonic(),
                "sequence": self._network["request_sequence"],
            }

        def finished(request) -> None:
            self._network.get("pending_relevant", {}).pop(request_key(request), None)

        def inspect(response) -> None:
            try:
                relevant = self._is_relevant_request(response.request)
                if relevant:
                    self._network["relevant_responses"].append({
                        "url": _sanitize_url(response.url),
                        "status": response.status,
                        "received_monotonic": time.monotonic(),
                    })
                    self._network["relevant_responses"] = self._network["relevant_responses"][-1000:]
                content_type = str(response.headers.get("content-type") or "").lower()
                if "json" not in content_type:
                    return
                length = _int_from_text(response.headers.get("content-length"))
                if length is not None and length > 5_000_000:
                    return
                payload = response.json()
                before = len(self._network["videos"])
                _extract_network_videos(payload, self._network["videos"], path="response")
                found = len(self._network["videos"]) - before
                record = {
                    "url": _sanitize_url(response.url), "method": response.request.method,
                    "status": response.status, "content_type": content_type.split(";", 1)[0],
                    "size": length,
                    "top_level_fields": sorted(payload.keys())[:80] if isinstance(payload, dict) else [],
                    "new_video_ids": found,
                    "relevant_to_readiness": relevant,
                }
                if self.config.network_debug or found:
                    self._network["responses"].append(record)
            except Exception:
                return

        page.on("request", started)
        page.on("requestfinished", finished)
        page.on("requestfailed", finished)
        page.on("response", inspect)

    def _discover_videos(self, conn, page, mapping: dict[str, Any], *, days: int, full_backfill: bool) -> DiscoveryResult:
        catalog = mapping["catalog"]
        discovered: dict[str, dict[str, Any]] = {}
        strategies: dict[str, int] = {}
        observed_signatures: set[str] = set()
        stall = 0
        total_publications = None
        period_boundary_reached = False
        cutoff = None if full_backfill else datetime.now(timezone.utc) - timedelta(days=days)
        cycle = 0
        for cycle in range(self.config.max_scroll_cycles):
            self._check_page_state(page)
            before = len(discovered)
            total_publications = total_publications or _publication_count(self._page_body(page))
            network_videos = list(self._network["videos"].values())
            self._merge_videos(discovered, network_videos, cutoff)
            strategies["NETWORK_RESPONSE"] = len(self._network["videos"])
            links = self._video_links(page)
            self._merge_videos(discovered, links, cutoff)
            strategies["VIDEO_LINKS"] = strategies.get("VIDEO_LINKS", 0) + len(links)
            attributes = self._semantic_video_attributes(page)
            self._merge_videos(discovered, attributes, cutoff)
            strategies["DOM_SEMANTIC"] = strategies.get("DOM_SEMANTIC", 0) + len(attributes)
            rows, signatures, oldest = self._mapped_rows(page, catalog)
            observed_signatures.update(signatures)
            self._merge_videos(discovered, rows, cutoff)
            strategies["DOM_CARDS"] = strategies.get("DOM_CARDS", 0) + len(rows)
            if cutoff and oldest and oldest <= cutoff:
                period_boundary_reached = True
            embedded = self._embedded_video_ids(page)
            self._merge_videos(discovered, embedded, cutoff)
            strategies["EMBEDDED_JSON"] = strategies.get("EMBEDDED_JSON", 0) + len(embedded)
            stall = 0 if len(discovered) > before else stall + 1
            if period_boundary_reached and stall >= 2:
                break
            self._guard_interaction("catalog-scroll")
            scroll_started = time.monotonic()
            advanced = self._advance_virtual_scroll(page, catalog.get("scroll_selector"))
            if not advanced:
                self._transition("DONE", "catalog-scroll-noop")
                if stall >= self.config.stall_cycles:
                    break
            else:
                readiness_mapping = dict(catalog)
                readiness_mapping.setdefault("ready_selector", catalog.get("row_selector"))
                readiness_mapping.setdefault("readiness_timeout_seconds", self.config.analytics_timeout_seconds)
                last_error = None
                for attempt in range(self.config.max_retries + 1):
                    try:
                        self.wait_for_tiktok_data_ready(
                            page,
                            page_name="catalog-scroll",
                            page_mapping=readiness_mapping,
                            kind="catalog",
                            attempt=attempt,
                            opened_at=scroll_started,
                        )
                        last_error = None
                        break
                    except DataReadyTimeout as exc:
                        last_error = exc
                        if attempt < self.config.max_retries:
                            self._log(f"Retrying catalog readiness ({attempt + 2}/{self.config.max_retries + 1})")
                            page.wait_for_timeout(min(2000, 500 * (attempt + 1)))
                if last_error:
                    raise last_error
                self._transition("DONE", "catalog-scroll")
            if stall >= self.config.stall_cycles:
                break

        expected, confidence, baseline = self._estimate_expected(
            conn, len(discovered), len(observed_signatures), total_publications,
            days=days, full_backfill=full_backfill, period_boundary_reached=period_boundary_reached,
        )
        used = [name for name, count in strategies.items() if count]
        return DiscoveryResult(
            videos=list(discovered.values()), discovery_method="+".join(used) if used else "NONE",
            strategies=strategies, expected_videos=expected, coverage_confidence=confidence,
            expected_posts_per_day=baseline, total_publications=total_publications,
            period_boundary_reached=period_boundary_reached, observed_rows=len(observed_signatures),
            scroll_cycles=cycle + 1,
        )

    def _estimate_expected(
        self, conn, discovered: int, observed: int, total_publications: int | None, *,
        days: int, full_backfill: bool, period_boundary_reached: bool,
    ) -> tuple[int | None, str, float | None]:
        if full_backfill and total_publications:
            return max(discovered, total_publications), "HIGH", None
        known = 0
        prior_baseline = None
        if conn is not None:
            known = conn.execute(
                """SELECT COUNT(*) FROM external_tiktok_contents
                   WHERE external_content_type = 'VIDEO' AND published_at IS NOT NULL
                     AND datetime(published_at) >= datetime('now', ?)""",
                (f"-{days} days",),
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT metadata_json FROM tiktok_analytics_runs WHERE valid = 1 ORDER BY id DESC LIMIT 10"
            ).fetchall()
            baselines = []
            for row in rows:
                try:
                    value = json.loads(row[0] or "{}").get("expected_posts_per_day")
                    if value is not None:
                        baselines.append(float(value))
                except (ValueError, TypeError, json.JSONDecodeError):
                    pass
            prior_baseline = max(baselines, default=None)
        baseline = max(self.config.min_expected_posts_per_day, prior_baseline or 0.0)
        sanity_floor = int(math.ceil(days * baseline)) if baseline else 0
        expected = max(discovered, observed, known, sanity_floor)
        confidence = "HIGH" if period_boundary_reached and discovered >= max(observed, known, sanity_floor) else "MEDIUM" if expected else "LOW"
        return expected or None, confidence, baseline or None

    def _video_links(self, page) -> list[dict[str, Any]]:
        result = []
        try:
            links = page.locator('a[href*="/video/"]')
            for index in range(min(links.count(), 2000)):
                link = links.nth(index)
                href = link.get_attribute("href")
                video_id = _video_id_from_url(href)
                if video_id:
                    result.append({"video_id": video_id, "video_url": _absolute_tiktok_url(href),
                                   "caption": _safe_inner_text(link), "source": "STUDIO_VIDEO_LINKS"})
        except Exception:
            pass
        return result

    def _semantic_video_attributes(self, page) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for selector, attribute in (
            ("[data-video-id]", "data-video-id"), ("[data-item-id]", "data-item-id"),
            ("[data-content-id]", "data-content-id"), ("[data-aweme-id]", "data-aweme-id"),
        ):
            try:
                nodes = page.locator(selector)
                for index in range(min(nodes.count(), 2000)):
                    video_id = _valid_video_id(nodes.nth(index).get_attribute(attribute))
                    if video_id:
                        result[video_id] = {"video_id": video_id, "source": "STUDIO_DOM_SEMANTIC"}
            except Exception:
                continue
        return list(result.values())

    def _mapped_rows(self, page, catalog: dict[str, Any]) -> tuple[list[dict[str, Any]], set[str], datetime | None]:
        row_selector = catalog.get("row_selector")
        fields = catalog.get("fields") or {}
        if not row_selector or not fields:
            return [], set(), None
        result = []
        signatures: set[str] = set()
        dates: list[datetime] = []
        try:
            rows = page.locator(row_selector)
            for index in range(min(rows.count(), 1000)):
                row = rows.nth(index)
                video = {}
                raw_values = []
                for name, spec in fields.items():
                    raw = self._extract_field(row, spec)
                    raw_values.append(str(raw or ""))
                    video[name] = self._normalize_catalog_field(name, raw, spec)
                video_id = _valid_video_id(video.get("video_id")) or _video_id_from_url(video.get("video_url"))
                if video.get("published_at"):
                    try:
                        dates.append(datetime.fromisoformat(video["published_at"]))
                    except ValueError:
                        pass
                signature = "|".join(raw_values)
                if signature.strip("|"):
                    signatures.add(signature)
                if video_id:
                    video["video_id"] = video_id
                    video["source"] = "STUDIO_DOM_CARDS"
                    result.append(video)
        except Exception:
            pass
        return result, signatures, min(dates) if dates else None

    def _embedded_video_ids(self, page) -> list[dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        try:
            scripts = page.locator('script[type="application/json"]')
            for index in range(min(scripts.count(), 20)):
                text = scripts.nth(index).text_content() or ""
                if not text or len(text) > 2_000_000:
                    continue
                try:
                    _extract_network_videos(json.loads(text), result, path="embedded")
                except json.JSONDecodeError:
                    continue
        except Exception:
            pass
        for video in result.values():
            video["source"] = "STUDIO_EMBEDDED_JSON"
        return list(result.values())

    def _merge_videos(self, target: dict[str, dict[str, Any]], incoming: list[dict[str, Any]], cutoff: datetime | None) -> None:
        for video in incoming:
            video_id = _valid_video_id(video.get("video_id")) or _video_id_from_url(video.get("video_url"))
            if not video_id:
                continue
            published = _normalized_datetime(video.get("published_at"), self.config.locale)
            if cutoff and published and datetime.fromisoformat(published) < cutoff:
                target.pop(video_id, None)
                continue
            normalized = {**video, "video_id": video_id}
            if published:
                normalized["published_at"] = published
            current = target.setdefault(video_id, {"video_id": video_id})
            for key, value in normalized.items():
                if value is not None and value != "":
                    current[key] = value

    def _collect_video_data(self, page, mapping: dict[str, Any], video: dict[str, Any]) -> dict[str, Any]:
        detail = mapping["detail"]
        pages = detail.get("pages") or [detail]
        metrics = {name: video.get(name) for name in SNAPSHOT_METRICS if video.get(name) is not None}
        raw: dict[str, Any] = {}
        sources = {name: (video.get("metric_sources") or {}).get(name) or "STUDIO_CONTENT_CATALOG" for name in metrics}
        last_url = video.get("video_url")
        page_errors: list[dict[str, Any]] = []
        readiness_pages: list[dict[str, Any]] = []
        expected_metrics: list[str] = []
        required_metrics: list[str] = []
        total_load_duration_ms = 0
        total_retries = 0
        for page_mapping in pages:
            page_name = str(page_mapping.get("name") or "detail")
            page_expected = list(page_mapping.get("expected_metrics") or (page_mapping.get("metrics") or {}).keys())
            page_required = list(page_mapping.get("required_metrics") or page_expected)
            required_page = bool(page_mapping.get("required", page_name == "overview"))
            expected_metrics.extend(page_expected)
            if required_page:
                required_metrics.extend(page_required)
            url = str(page_mapping.get("url_template") or detail.get("url_template") or "").replace("{video_id}", video["video_id"])
            if not url:
                page_errors.append({"page_name": page_name, "status": "DATA_INCOMPLETE", "error": "detail_url_missing"})
                if required_page:
                    raise DataIncomplete(f"URL obrigatoria ausente para {page_name}")
                continue
            success = False
            last_error: Exception | None = None
            for attempt in range(self.config.max_retries + 1):
                if attempt:
                    total_retries += 1
                try:
                    context = f"video-{video['video_id']}-{page_name}"
                    self._log(f"Opening video {video['video_id']} ({page_name}, attempt {attempt + 1})")
                    opened = self._guarded_goto(
                        page,
                        url,
                        context=context,
                        expected_video_id=video["video_id"],
                    )
                    readiness = self.wait_for_tiktok_data_ready(
                        page,
                        page_name=context,
                        page_mapping=page_mapping,
                        expected_video_id=video["video_id"],
                        attempt=attempt,
                        opened_at=opened,
                    )
                    self._transition("EXTRACTING", context)
                    page_metrics = readiness.metrics
                    page_raw = readiness.raw_values
                    page_sources = readiness.metric_sources
                    self._transition("VALIDATING", context)
                    if _video_id_from_url(page.url) != video["video_id"]:
                        raise DataIncomplete(
                            f"Video incorreto aberto para {video['video_id']}",
                            details={"expected": video["video_id"], "current": _video_id_from_url(page.url)},
                        )
                    missing_required = [metric for metric in page_required if page_metrics.get(metric) is None]
                    if required_page and missing_required:
                        raise DataIncomplete(
                            f"Pagina obrigatoria {page_name} incompleta para {video['video_id']}: {', '.join(missing_required)}",
                            details=readiness.as_dict(),
                        )
                    metrics.update({key: value for key, value in page_metrics.items() if value is not None})
                    raw.update(page_raw)
                    sources.update(page_sources)
                    readiness_pages.append(readiness.as_dict())
                    total_load_duration_ms += readiness.load_duration_ms
                    last_url = page.url
                    self._transition("DONE", context)
                    success = True
                    break
                except (AuthenticationRequired, InteractionRequired, RateLimited):
                    raise
                except (DataReadyTimeout, DataIncomplete) as exc:
                    last_error = exc
                    if self._page_state != "TIMEOUT":
                        self._transition("DATA_INCOMPLETE", f"video-{video['video_id']}-{page_name}")
                    self._log(
                        f"{exc.code} video={video['video_id']} page={page_name} attempt={attempt + 1}: {exc}"
                    )
                    if attempt < self.config.max_retries:
                        self._log(f"Retrying {page_name} ({attempt + 2}/{self.config.max_retries + 1})")
                        page.wait_for_timeout(min(2000, 500 * (attempt + 1)))
                except Exception as exc:
                    last_error = exc
                    self._transition("DATA_INCOMPLETE", f"video-{video['video_id']}-{page_name}")
                    self._log(
                        f"DATA_INCOMPLETE video={video['video_id']} page={page_name} attempt={attempt + 1}: {exc}"
                    )
                    if attempt < self.config.max_retries:
                        page.wait_for_timeout(min(2000, 500 * (attempt + 1)))
            if not success:
                details = getattr(last_error, "details", {}) if last_error else {}
                error_status = getattr(last_error, "code", "DATA_INCOMPLETE") if last_error else "DATA_INCOMPLETE"
                page_errors.append({
                    "page_name": page_name,
                    "status": error_status,
                    "error": str(last_error or "Pagina sem dados"),
                    "details": details,
                })
                total_load_duration_ms += int(details.get("load_duration_ms") or 0)
                if required_page:
                    raise DataIncomplete(
                        f"Pagina obrigatoria {page_name} falhou para o video {video['video_id']}",
                        details={"video_id": video["video_id"], "pages": readiness_pages, "errors": page_errors},
                    ) from last_error
                self._transition("DONE", f"video-{video['video_id']}-{page_name}-optional-failed")

        expected_metrics = list(dict.fromkeys(expected_metrics))
        required_metrics = list(dict.fromkeys(required_metrics))
        missing_required = [metric for metric in required_metrics if metrics.get(metric) is None]
        if missing_required:
            raise DataIncomplete(
                f"Video {video['video_id']} sem campos obrigatorios: {', '.join(missing_required)}",
                details={"video_id": video["video_id"], "pages": readiness_pages, "errors": page_errors},
            )
        if not metrics:
            raise DataIncomplete(f"Video {video['video_id']} sem nenhuma metrica coletavel")
        completeness = data_completeness_score(metrics, required_metrics)
        extraction_status = "COMPLETE" if not missing_required else "DATA_INCOMPLETE"
        if page_errors:
            raw["collection_errors"] = page_errors
        raw["readiness"] = readiness_pages
        self._log(f"Data completeness: {completeness:.1f}% (video {video['video_id']})")
        self._log(f"Moving to next video after {video['video_id']}")
        return {"video": video, "metrics": metrics, "raw_values": raw, "metric_sources": sources,
                "page_url": last_url, "captured_at": now_local_iso(),
                "partial": not bool(video.get("published_at")) or extraction_status != "COMPLETE",
                "extraction_status": extraction_status,
                "data_completeness_score": completeness,
                "load_duration_ms": total_load_duration_ms,
                "retry_count": total_retries,
                "readiness": readiness_pages}

    def _catalog_payload(self, video: dict[str, Any]) -> dict[str, Any]:
        metrics = {name: video.get(name) for name in SNAPSHOT_METRICS if video.get(name) is not None}
        if not metrics:
            raise UiChanged(f"Video {video['video_id']} sem metrica no catalogo")
        sources = {
            name: (video.get("metric_sources") or {}).get(name) or "STUDIO_CONTENT_CATALOG"
            for name in metrics
        }
        return {
            "video": video, "metrics": metrics, "raw_values": {}, "metric_sources": sources,
            "page_url": video.get("video_url"), "captured_at": now_local_iso(), "partial": True,
            "extraction_status": "PARTIAL", "data_completeness_score": data_completeness_score(
                metrics, ["views", "likes", "comments", "shares", "saves"]
            ),
            "load_duration_ms": 0, "retry_count": 0, "readiness": [],
        }

    def _prioritize_videos(self, conn, videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
        winners = {row[0] for row in conn.execute(
            "SELECT external_content_id FROM tiktok_video_scores WHERE winner = 1 OR decay_status IN ('DECAYING', 'DEAD')"
        ).fetchall()}
        selling = {str(row[0]) for row in conn.execute(
            "SELECT DISTINCT external_content_id FROM affiliate_order_items WHERE external_content_id IS NOT NULL"
        ).fetchall()}
        now = datetime.now(timezone.utc)

        def key(video: dict[str, Any]) -> tuple[int, float]:
            published = _normalized_datetime(video.get("published_at"), self.config.locale)
            age_days = max(0.0, (now - datetime.fromisoformat(published)).total_seconds() / 86400) if published else 99999.0
            video_id = video["video_id"]
            tier = 1 if age_days < 2 else 2 if age_days <= 7 else 3 if age_days <= 30 and video_id in winners else 4 if video_id in selling else 5
            video["collection_tier"] = tier
            return tier, age_days

        return sorted(videos, key=key)

    def _extract_metrics(self, page, metric_mapping: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
        normalized: dict[str, Any] = {}
        raw: dict[str, Any] = {}
        sources: dict[str, str] = {}
        for metric, spec in metric_mapping.items():
            try:
                raw_value = self._extract_field(page, spec)
                normalized_value = self._normalize_metric(raw_value, spec)
            except Exception:
                raw_value = None
                normalized_value = None
            raw[metric] = raw_value
            normalized[metric] = normalized_value
            if normalized[metric] is not None:
                sources[metric] = spec.get("source") or "STUDIO_VIDEO_CRAWLER"
        return normalized, raw, sources

    def _extract_field(self, scope, spec: Any) -> Any:
        if isinstance(spec, str):
            spec = {"selector": spec}
        if spec.get("text"):
            locator = scope.get_by_text(spec["text"], exact=bool(spec.get("exact", True))).first
        elif spec.get("container_selector"):
            locator = scope.locator(spec["container_selector"])
            if spec.get("has_text"):
                locator = locator.filter(has_text=spec["has_text"])
            locator = locator.first
        else:
            selector = spec.get("selector")
            if not selector:
                return None
            locator = scope.locator(selector).first
        if spec.get("value_selector"):
            locator = locator.locator(spec["value_selector"])
            locator = locator.nth(int(spec["value_index"])) if spec.get("value_index") is not None else locator.first
        elif spec.get("relative_selector"):
            locator = locator.locator(spec["relative_selector"]).first
        if locator.count() == 0:
            return None
        attribute = spec.get("attribute")
        return locator.get_attribute(attribute) if attribute else locator.inner_text()

    def _normalize_metric(self, value: Any, spec: dict[str, Any]) -> Any:
        if value is None:
            return None
        kind = spec.get("type", "number")
        locale = spec.get("locale") or self.config.locale
        if kind == "currency_minor":
            return parse_currency_minor(value, locale)
        if kind == "percentage":
            return parse_percentage(value, locale)
        if kind == "datetime":
            return parse_tiktok_datetime(value, locale)
        if kind == "duration_seconds":
            return parse_duration_seconds(value)
        parsed = parse_localized_number(value, locale)
        return float(parsed) if parsed is not None else None

    def _normalize_catalog_field(self, name: str, value: Any, spec: dict[str, Any]) -> Any:
        if value is None:
            return None
        kind = spec.get("type", "text") if isinstance(spec, dict) else "text"
        if kind in {"number", "currency_minor", "percentage", "datetime", "duration_seconds"}:
            return self._normalize_metric(value, spec)
        if name == "video_url":
            return _absolute_tiktok_url(value)
        return str(value).strip() or None

    def _advance_virtual_scroll(self, page, preferred_selector: str | None) -> bool:
        selectors = [selector for selector in (
            preferred_selector, '[data-tt*="PostTable_Container"]', '[role="grid"]', '[role="table"]', "main",
        ) if selector]
        for selector in selectors:
            try:
                containers = page.locator(selector)
                for index in range(min(containers.count(), 20)):
                    container = containers.nth(index)
                    state = container.evaluate("el => ({top: el.scrollTop, height: el.clientHeight, total: el.scrollHeight})")
                    if state["total"] <= state["height"] + 2:
                        continue
                    next_top = min(state["total"] - state["height"], state["top"] + max(600, state["height"] * 0.85))
                    if next_top <= state["top"] + 1:
                        continue
                    container.evaluate("(el, top) => { el.scrollTop = top; el.dispatchEvent(new Event('scroll')); }", next_top)
                    return True
            except Exception:
                continue
        try:
            before = page.evaluate("() => window.scrollY")
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(200)
            return page.evaluate("() => window.scrollY") > before
        except Exception:
            return False

    def _mapped_row_count(self, page) -> int:
        try:
            selector = self._load_mapping()["catalog"].get("row_selector")
            return page.locator(selector).count() if selector else 0
        except Exception:
            return 0

    def _page_body(self, page) -> str:
        try:
            return page.locator("body").inner_text(timeout=min(5000, self.config.timeout_seconds * 1000))
        except Exception:
            return ""

    def _quality_summary(self, payloads: list[dict[str, Any]], failures: list[dict[str, Any]]) -> dict[str, Any]:
        def timed_out(failure: dict[str, Any]) -> bool:
            if failure.get("status") == "DATA_READY_TIMEOUT":
                return True
            return "DATA_READY_TIMEOUT" in json.dumps(failure.get("details") or {}, default=str)

        complete = sum(payload.get("extraction_status") == "COMPLETE" for payload in payloads)
        timeout_count = sum(timed_out(failure) for failure in failures)
        partial = len(payloads) - complete + len(failures) - timeout_count
        loads = [
            float(value)
            for value in [
                *(payload.get("load_duration_ms") for payload in payloads),
                *(failure.get("load_duration_ms") for failure in failures),
            ]
            if value is not None and float(value) >= 0
        ]
        completeness_values = [float(payload.get("data_completeness_score") or 0) for payload in payloads]
        completeness_values.extend(0.0 for _ in failures)
        retries = sum(int(payload.get("retry_count") or 0) for payload in payloads)
        retries += sum(int(failure.get("retry_count") or 0) for failure in failures)
        return {
            "videos_processed": len(payloads) + len(failures),
            "videos_complete": complete,
            "videos_partial": partial,
            "videos_timeout": timeout_count,
            "retries_total": retries,
            "average_load_ms": round(sum(loads) / len(loads), 2) if loads else 0.0,
            "p95_load_ms": round(percentile(loads, 0.95) or 0.0, 2),
            "average_completeness": (
                round(sum(completeness_values) / len(completeness_values), 2) if completeness_values else 0.0
            ),
        }

    def _run_metadata(self, discovery: DiscoveryResult | None, failures: list[dict[str, Any]]) -> dict[str, Any]:
        if not discovery:
            return {"network_responses_observed": len(self._network.get("responses", [])), "failures": failures[:20]}
        return {
            "strategies": discovery.strategies, "total_publications": discovery.total_publications,
            "observed_rows": discovery.observed_rows, "period_boundary_reached": discovery.period_boundary_reached,
            "scroll_cycles": discovery.scroll_cycles, "expected_posts_per_day": discovery.expected_posts_per_day,
            "network_responses_observed": len(self._network.get("responses", [])),
            "network_video_ids": len(self._network.get("videos", {})), "failures": failures[:50],
            "collector_mode": self.config.collector_mode,
            "stable_readings_required": self.config.stable_readings,
            "dom_quiet_ms": self.config.dom_quiet_ms,
            "zero_guard_ms": self.config.zero_guard_ms,
            "readiness_poll_ms": self.config.readiness_poll_ms,
        }

    def _failure_artifacts(self, page, run_id: int, label: str, exc: Exception) -> dict[str, str | None]:
        stamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d_%H%M%S")
        screenshot = self._screenshot(page, f"{stamp}_run-{run_id}_{label.lower()}")
        details = {
            "captured_at": now_local_iso(), "error_type": type(exc).__name__, "error": str(exc),
            "url": page.url if page else self.config.start_url, "title": _safe_title(page),
            "body_excerpt": self._page_body(page)[:4000] if page else "",
            "links": _safe_count(page, "a") if page else 0, "iframes": _safe_count(page, "iframe") if page else 0,
            "mapped_rows": self._mapped_row_count(page) if page else 0,
            "network": self._network.get("responses", [])[-100:],
            "network_video_ids": len(self._network.get("videos", {})),
            "page_state": self._page_state,
            "state_context": self._state_context,
            "state_history": self._state_history[-100:],
            "readiness_details": getattr(exc, "details", {}),
        }
        report = self._write_json(self.config.artifacts_dir / f"{stamp}_run-{run_id}_{label.lower()}.json", details)
        return {"screenshot": str(screenshot) if screenshot else None, "report": str(report) if report else None}

    def _write_network_report(self, label: str, page, metadata: dict[str, Any], *, force: bool = False) -> Path | None:
        if not force and not self.config.network_debug:
            return None
        stamp = datetime.now(TIMEZONE).strftime("%Y-%m-%d_%H%M%S")
        directory = self.config.logs_dir or self.config.artifacts_dir.parent
        payload = {
            "captured_at": now_local_iso(), "label": label,
            "page_url": _sanitize_url(page.url) if page else None, "metadata": metadata,
            "responses": self._network.get("responses", []),
            "video_ids_found": sorted(self._network.get("videos", {}).keys()),
        }
        return self._write_json(directory / f"tiktok-network-discovery-{stamp}-{label}.json", payload)

    def _write_json(self, path: Path, payload: dict[str, Any]) -> Path | None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            return path
        except OSError:
            return None

    def _screenshot(self, page, label: str) -> Path | None:
        if page is None:
            return None
        path = self.config.artifacts_dir / f"{label}.png"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(path), full_page=True)
            return path
        except Exception:
            return None

    def _log(self, message: str) -> None:
        LOGGER.info("[TikTokCollector] %s", message)
        print(f"[TikTokCollector] {message}", flush=True)


def _extract_network_videos(value: Any, target: dict[str, dict[str, Any]], *, path: str, depth: int = 0) -> None:
    if depth > 14:
        return
    if isinstance(value, list):
        for index, item in enumerate(value[:5000]):
            _extract_network_videos(item, target, path=f"{path}[{index}]", depth=depth + 1)
        return
    if not isinstance(value, dict):
        return
    lowered = {str(key).lower(): item for key, item in value.items()}
    explicit = next((_valid_video_id(lowered.get(key)) for key in VIDEO_ID_KEYS if _valid_video_id(lowered.get(key))), None)
    generic = next((_valid_video_id(lowered.get(key)) for key in GENERIC_ID_KEYS if _valid_video_id(lowered.get(key))), None)
    videoish = any(marker in path.lower() for marker in VIDEO_CONTEXT_MARKERS) or any(
        key in lowered for aliases in METRIC_ALIASES.values() for key in aliases
    ) or any(key in lowered for key in ("create_time", "caption", "desc", "cover", "video"))
    video_id = explicit or (generic if videoish else None)
    if video_id:
        candidate = target.setdefault(video_id, {"video_id": video_id, "source": "STUDIO_NETWORK_RESPONSE", "metric_sources": {}})
        url = _first_value(lowered, ("video_url", "share_url", "url", "web_url"))
        if _video_id_from_url(url) == video_id:
            candidate["video_url"] = _absolute_tiktok_url(url)
        caption = _first_value(lowered, ("caption", "desc", "description", "title"))
        if isinstance(caption, str) and caption.strip():
            candidate["caption"] = caption.strip()
        published = _normalized_datetime(_first_value(lowered, ("create_time", "create_timestamp", "publish_time", "published_at")), "pt-BR")
        if published:
            candidate["published_at"] = published
        cover = _find_url_value(value, ("cover", "cover_url", "thumbnail", "thumbnail_url", "origin_cover"))
        if cover:
            candidate["thumbnail_url"] = cover
        for metric, aliases in METRIC_ALIASES.items():
            number = _network_number(_find_nested_value(value, aliases, max_depth=3))
            if number is not None:
                candidate[metric] = number
                candidate["metric_sources"][metric] = "STUDIO_NETWORK_RESPONSE"
    for key, item in list(value.items())[:5000]:
        if isinstance(item, (dict, list)):
            _extract_network_videos(item, target, path=f"{path}.{key}", depth=depth + 1)


def _first_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    return next((mapping[key] for key in keys if mapping.get(key) is not None), None)


def _find_nested_value(value: Any, aliases: tuple[str, ...], *, max_depth: int, depth: int = 0) -> Any:
    if depth > max_depth:
        return None
    if isinstance(value, dict):
        lowered = {str(key).lower(): item for key, item in value.items()}
        direct = _first_value(lowered, aliases)
        if direct is not None:
            return direct
        for item in value.values():
            found = _find_nested_value(item, aliases, max_depth=max_depth, depth=depth + 1)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value[:100]:
            found = _find_nested_value(item, aliases, max_depth=max_depth, depth=depth + 1)
            if found is not None:
                return found
    return None


def _find_url_value(value: Any, aliases: tuple[str, ...]) -> str | None:
    found = _find_nested_value(value, aliases, max_depth=3)
    if isinstance(found, str) and found.startswith("http"):
        return found
    if isinstance(found, list):
        return next((item for item in found if isinstance(item, str) and item.startswith("http")), None)
    if isinstance(found, dict):
        return _find_url_value(found, ("url", "url_list"))
    return None


def _network_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalized_datetime(value: Any, locale: str) -> str | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
        if number > 1_000_000_000_000:
            number /= 1000
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number, tz=timezone.utc).replace(microsecond=0).isoformat()
    except (TypeError, ValueError):
        pass
    try:
        return parse_tiktok_datetime(value, locale)
    except ValueError:
        return None


def _valid_video_id(value: Any) -> str | None:
    text = str(value or "").strip()
    return text if re.fullmatch(r"\d{15,25}", text) else None


def _video_id_from_url(value: Any) -> str | None:
    if not value:
        return None
    match = re.search(r"(?:/video/|/analytics/|video_id=|item_id=|content_id=)(\d{15,25})", str(value))
    return match.group(1) if match else None


def _absolute_tiktok_url(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    return "https://www.tiktok.com" + text if text.startswith("/") else text


def _sanitize_url(value: str) -> str:
    parts = urlsplit(value)
    query_names = sorted({name for name, _ in parse_qsl(parts.query, keep_blank_values=True)})
    query = "&".join(f"{name}=<redacted>" for name in query_names)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _publication_count(body: str) -> int | None:
    match = re.search(r"(?:publica(?:coes|ções)|posts?)\s*([\d.,]+)", body, flags=re.IGNORECASE)
    if not match:
        match = re.search(r"([\d.,]+)\s*(?:publica(?:coes|ções)|posts?)", body, flags=re.IGNORECASE)
    return _int_from_text(match.group(1)) if match else None


def _int_from_text(value: Any) -> int | None:
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    return int(digits) if digits else None


def _coverage(collected: int, expected: int | None) -> float | None:
    if expected is None or expected <= 0:
        return None
    return min(1.0, collected / expected)


def _safe_inner_text(locator) -> str | None:
    try:
        return locator.inner_text().strip() or None
    except Exception:
        return None


def _safe_count(page, selector: str) -> int:
    try:
        return page.locator(selector).count()
    except Exception:
        return 0


def _safe_title(page) -> str | None:
    try:
        return page.title()
    except Exception:
        return None


def now_local_iso() -> str:
    return datetime.now(TIMEZONE).isoformat(timespec="seconds")


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
