from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit


READINESS_STATES = {
    "DONE",
    "NAVIGATING",
    "DOM_READY",
    "WAITING_FOR_DATA",
    "DATA_DETECTED",
    "WAITING_FOR_STABILITY",
    "READY",
    "EXTRACTING",
    "VALIDATING",
    "TIMEOUT",
    "AUTH_REQUIRED",
    "INTERACTION_REQUIRED",
    "DATA_INCOMPLETE",
    "UI_CHANGED",
}

PLACEHOLDER_VALUES = {
    "",
    "-",
    "--",
    "---",
    "- %",
    "-%",
    "--%",
    "n/a",
    "na",
    "null",
    "none",
    "loading",
    "carregando",
    "processando",
}


def is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = " ".join(str(value).strip().lower().split())
    return text in PLACEHOLDER_VALUES or "loading" in text or "carregando" in text


def data_completeness_score(metrics: dict[str, Any], expected_metrics: list[str] | tuple[str, ...]) -> float:
    expected = list(dict.fromkeys(expected_metrics))
    if not expected:
        return 100.0
    present = sum(metrics.get(metric) is not None for metric in expected)
    return round(present / len(expected) * 100, 2)


def should_guard_transient_zero(
    metrics: dict[str, Any],
    metric_names: list[str] | tuple[str, ...],
    *,
    relevant_response_received: bool,
    elapsed_ms: int,
    guard_ms: int,
) -> bool:
    return (
        not relevant_response_received
        and elapsed_ms < guard_ms
        and any(metrics.get(metric) == 0 for metric in metric_names)
    )


def authenticated_login_handoff_target(url: str, body: str) -> str | None:
    """Return TikTok's own redirect target when its login bridge confirms an active session."""
    lowered_url = str(url or "").lower()
    lowered_body = " ".join(str(body or "").lower().split())
    if "/login" not in lowered_url:
        return None
    connected_markers = (
        "n\u00e3o \u00e9 preciso repetir",
        "voc\u00ea est\u00e1 conectado",
        "already logged in",
        "no need to log in again",
    )
    if not any(marker in lowered_body for marker in connected_markers):
        return None
    query = dict(parse_qsl(urlsplit(str(url)).query, keep_blank_values=False))
    target = str(query.get("redirect_url") or "")
    return target if target.startswith("https://www.tiktok.com/") else None


def percentile(values: list[float], ratio: float) -> float | None:
    clean = sorted(float(value) for value in values if value is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    position = max(0.0, min(1.0, ratio)) * (len(clean) - 1)
    lower = int(position)
    upper = min(len(clean) - 1, lower + 1)
    fraction = position - lower
    return clean[lower] + (clean[upper] - clean[lower]) * fraction


@dataclass(frozen=True)
class ReadinessSample:
    signature: tuple[Any, ...]
    data_detected: bool
    loaders_visible: int = 0
    pending_requests: int = 0
    correct_video: bool = True


@dataclass(frozen=True)
class ReadinessDecision:
    state: str
    ready: bool
    stable_readings: int
    changed: bool
    quiet_for_ms: int


class TikTokReadinessTracker:
    """Pure stability evaluator used by the Playwright readiness loop."""

    def __init__(self, *, required_stable_readings: int, dom_quiet_ms: int) -> None:
        self.required_stable_readings = max(2, int(required_stable_readings))
        self.dom_quiet_ms = max(0, int(dom_quiet_ms))
        self._last_signature: tuple[Any, ...] | None = None
        self._last_change_ms: int | None = None
        self._stable_readings = 0

    def observe(self, sample: ReadinessSample, now_ms: int) -> ReadinessDecision:
        blockers = sample.loaders_visible > 0 or sample.pending_requests > 0 or not sample.correct_video
        if blockers or not sample.data_detected:
            changed = sample.signature != self._last_signature
            self._stable_readings = 0
            self._last_signature = sample.signature
            self._last_change_ms = now_ms
            return ReadinessDecision("WAITING_FOR_DATA", False, 0, changed, 0)

        changed = sample.signature != self._last_signature
        if changed:
            self._last_signature = sample.signature
            self._last_change_ms = now_ms
            self._stable_readings = 1
        else:
            self._stable_readings += 1
        last_change_ms = self._last_change_ms if self._last_change_ms is not None else now_ms
        quiet_for_ms = max(0, now_ms - last_change_ms)
        ready = self._stable_readings >= self.required_stable_readings and quiet_for_ms >= self.dom_quiet_ms
        return ReadinessDecision(
            "READY" if ready else "WAITING_FOR_STABILITY",
            ready,
            self._stable_readings,
            changed,
            quiet_for_ms,
        )
