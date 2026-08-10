from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from src.store import now_iso


CREATOR_PROFILE_PATH = "/affiliate_creator/202508/profiles"
CREATOR_ORDERS_PATH = "/affiliate_creator/202410/orders/search"
TOKEN_GET_PATH = "/api/v2/token/get"
TOKEN_REFRESH_PATH = "/api/v2/token/refresh"
CREATOR_AUTH_URL = "https://shop.tiktok.com/alliance/creator/auth"


class TikTokShopError(RuntimeError):
    pass


class TikTokShopConfigError(TikTokShopError):
    pass


class TikTokShopApiError(TikTokShopError):
    def __init__(
        self,
        message: str,
        *,
        code: str | int | None = None,
        http_status: int | None = None,
        request_id: str | None = None,
        integration_status: str = "ERROR",
    ) -> None:
        super().__init__(message)
        self.code = str(code) if code is not None else None
        self.http_status = http_status
        self.request_id = request_id
        self.integration_status = integration_status


class TikTokShopTransportError(TikTokShopError):
    pass


@dataclass(frozen=True)
class TikTokShopConfig:
    enabled: bool
    app_key: str
    app_secret: str
    redirect_url: str
    market: str
    api_base_url: str
    auth_base_url: str
    creator_scopes: tuple[str, ...]
    oauth_state_ttl_seconds: int
    refresh_leeway_seconds: int
    sync_safety_window_seconds: int
    full_sync_days: int

    @classmethod
    def from_env(cls) -> "TikTokShopConfig":
        scopes = tuple(
            item.strip()
            for item in os.getenv(
                "TIKTOK_SHOP_CREATOR_SCOPES",
                "creator.affiliate.info,creator.affiliate_collaboration.read",
            ).split(",")
            if item.strip()
        )
        return cls(
            enabled=_env_bool("TIKTOK_SHOP_CREATOR_API_ENABLED", False),
            app_key=os.getenv("TIKTOK_SHOP_APP_KEY", "").strip(),
            app_secret=os.getenv("TIKTOK_SHOP_APP_SECRET", "").strip(),
            redirect_url=os.getenv("TIKTOK_SHOP_REDIRECT_URL", "").strip(),
            market=os.getenv("TIKTOK_SHOP_MARKET", "BR").strip() or "BR",
            api_base_url=os.getenv(
                "TIKTOK_SHOP_API_BASE_URL", "https://open-api.tiktokglobalshop.com"
            ).rstrip("/"),
            auth_base_url=os.getenv(
                "TIKTOK_SHOP_AUTH_BASE_URL", "https://auth.tiktok-shops.com"
            ).rstrip("/"),
            creator_scopes=scopes,
            oauth_state_ttl_seconds=int(os.getenv("TIKTOK_SHOP_OAUTH_STATE_TTL_SECONDS", "600")),
            refresh_leeway_seconds=int(os.getenv("TIKTOK_SHOP_TOKEN_REFRESH_LEEWAY_SECONDS", "900")),
            sync_safety_window_seconds=int(os.getenv("TIKTOK_SHOP_SYNC_SAFETY_WINDOW_SECONDS", "21600")),
            full_sync_days=int(os.getenv("TIKTOK_SHOP_FULL_SYNC_DAYS", "365")),
        )

    def validate_for_oauth(self) -> None:
        if not self.enabled:
            raise TikTokShopConfigError("TikTok Shop Creator API esta desativada")
        missing = [
            name
            for name, value in (
                ("TIKTOK_SHOP_APP_KEY", self.app_key),
                ("TIKTOK_SHOP_APP_SECRET", self.app_secret),
                ("TIKTOK_SHOP_REDIRECT_URL", self.redirect_url),
            )
            if not value
        ]
        if missing:
            raise TikTokShopConfigError("Configuracao ausente: " + ", ".join(missing))


class TikTokShopSignatureService:
    EXCLUDED_QUERY_KEYS = {"access_token", "sign"}

    @classmethod
    def canonical_string(
        cls,
        path: str,
        query: dict[str, Any],
        app_secret: str,
        body: str = "",
        content_type: str = "application/json",
    ) -> str:
        params = "".join(
            f"{key}{query[key]}"
            for key in sorted(query)
            if key not in cls.EXCLUDED_QUERY_KEYS and query[key] is not None
        )
        signing_input = f"{path}{params}"
        if not content_type.lower().startswith("multipart/form-data") and body:
            signing_input += body
        return f"{app_secret}{signing_input}{app_secret}"

    @classmethod
    def sign(
        cls,
        path: str,
        query: dict[str, Any],
        app_secret: str,
        body: str = "",
        content_type: str = "application/json",
    ) -> str:
        canonical = cls.canonical_string(path, query, app_secret, body, content_type)
        return hmac.new(
            app_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
        ).hexdigest()


class OAuthStateService:
    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = ttl_seconds

    def create(self, conn: sqlite3.Connection, session_id: str) -> str:
        state = secrets.token_urlsafe(32)
        created = datetime.now(timezone.utc).replace(microsecond=0)
        expires = created + timedelta(seconds=self.ttl_seconds)
        conn.execute(
            """
            INSERT INTO oauth_states (
                provider, state_hash, session_hash, expires_at, created_at
            ) VALUES ('tiktok_shop_creator', ?, ?, ?, ?)
            """,
            (
                _sha256(state),
                _sha256(session_id),
                expires.isoformat(),
                created.isoformat(),
            ),
        )
        conn.commit()
        return state

    def validate(self, conn: sqlite3.Connection, state: str, session_id: str) -> None:
        if not state or not session_id:
            raise TikTokShopError("OAuth state ausente")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        cursor = conn.execute(
            """
            UPDATE oauth_states
            SET used_at = ?
            WHERE provider = 'tiktok_shop_creator'
              AND state_hash = ?
              AND session_hash = ?
              AND used_at IS NULL
              AND expires_at > ?
            """,
            (now, _sha256(state), _sha256(session_id), now),
        )
        conn.commit()
        if cursor.rowcount != 1:
            raise TikTokShopError("OAuth state invalido, expirado ou ja utilizado")


class TokenCipher:
    PREFIX = "dpapi:v1:"

    def encrypt(self, plaintext: str) -> str:
        if os.name != "nt":
            raise TikTokShopConfigError("A cifra DPAPI requer Windows")
        encrypted = _dpapi_protect(plaintext.encode("utf-8"))
        return self.PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(self.PREFIX):
            raise TikTokShopError("Formato de token cifrado desconhecido")
        encrypted = base64.urlsafe_b64decode(ciphertext[len(self.PREFIX) :])
        return _dpapi_unprotect(encrypted).decode("utf-8")


class UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: int = 30,
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        request = urllib.request.Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
                payload = json.loads(raw or "{}")
                return int(response.status), payload, dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                payload = json.loads(raw or "{}")
            except json.JSONDecodeError:
                payload = {"message": "HTTP error from TikTok Shop"}
            return int(exc.code), payload, dict(exc.headers.items())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise TikTokShopTransportError("Falha de rede ao acessar TikTok Shop") from exc


class TikTokShopTokenService:
    _locks_guard = threading.Lock()
    _locks: dict[int, threading.Lock] = {}

    def __init__(
        self,
        config: TikTokShopConfig,
        cipher: TokenCipher | Any | None = None,
        transport: UrllibTransport | Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.cipher = cipher or TokenCipher()
        self.transport = transport or UrllibTransport()
        self.clock = clock

    def exchange_auth_code(self, auth_code: str) -> dict[str, Any]:
        return self._token_request(
            TOKEN_GET_PATH,
            {
                "app_key": self.config.app_key,
                "app_secret": self.config.app_secret,
                "auth_code": auth_code,
                "grant_type": "authorized_code",
            },
        )

    def save_creator_authorization(
        self, conn: sqlite3.Connection, token_data: dict[str, Any]
    ) -> int:
        if int(token_data.get("user_type", -1)) != 1:
            raise TikTokShopError("O token retornado nao pertence a um Creator")
        open_id = str(token_data.get("open_id") or "").strip()
        access_token = str(token_data.get("access_token") or "").strip()
        refresh_token = str(token_data.get("refresh_token") or "").strip()
        if not open_id or not access_token or not refresh_token:
            raise TikTokShopError("Resposta OAuth Creator incompleta")
        scopes = tuple(str(item) for item in token_data.get("granted_scopes") or [])
        missing = sorted(set(self.config.creator_scopes) - set(scopes))
        status = "MISSING_SCOPE" if missing else "CONNECTED"
        timestamp = now_iso()
        conn.execute(
            """
            INSERT INTO tiktok_creator_accounts (
                creator_open_id, market, status, granted_scopes,
                access_token_encrypted, refresh_token_encrypted,
                access_token_expires_at, refresh_token_expires_at,
                last_error, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(creator_open_id) DO UPDATE SET
                market = excluded.market,
                status = excluded.status,
                granted_scopes = excluded.granted_scopes,
                access_token_encrypted = excluded.access_token_encrypted,
                refresh_token_encrypted = excluded.refresh_token_encrypted,
                access_token_expires_at = excluded.access_token_expires_at,
                refresh_token_expires_at = excluded.refresh_token_expires_at,
                last_error = excluded.last_error,
                updated_at = excluded.updated_at
            """,
            (
                open_id,
                self.config.market,
                status,
                json.dumps(scopes),
                self.cipher.encrypt(access_token),
                self.cipher.encrypt(refresh_token),
                _epoch_iso(token_data.get("access_token_expire_in")),
                _epoch_iso(token_data.get("refresh_token_expire_in")),
                "Scopes ausentes: " + ", ".join(missing) if missing else None,
                timestamp,
                timestamp,
            ),
        )
        row = conn.execute(
            "SELECT id FROM tiktok_creator_accounts WHERE creator_open_id = ?", (open_id,)
        ).fetchone()
        conn.commit()
        return int(row["id"])

    def get_access_token(self, conn: sqlite3.Connection, account_id: int) -> str:
        row = conn.execute(
            "SELECT * FROM tiktok_creator_accounts WHERE id = ?", (account_id,)
        ).fetchone()
        if not row or not row["access_token_encrypted"]:
            raise TikTokShopError("Conta Creator nao conectada")
        expiry = _parse_iso(row["access_token_expires_at"])
        if expiry and expiry.timestamp() > self.clock() + self.config.refresh_leeway_seconds:
            return self.cipher.decrypt(row["access_token_encrypted"])
        lock = self._lock_for(account_id)
        with lock:
            row = conn.execute(
                "SELECT * FROM tiktok_creator_accounts WHERE id = ?", (account_id,)
            ).fetchone()
            expiry = _parse_iso(row["access_token_expires_at"])
            if expiry and expiry.timestamp() > self.clock() + self.config.refresh_leeway_seconds:
                return self.cipher.decrypt(row["access_token_encrypted"])
            return self._refresh(conn, dict(row))

    def _refresh(self, conn: sqlite3.Connection, account: dict[str, Any]) -> str:
        refresh_expiry = _parse_iso(account.get("refresh_token_expires_at"))
        if refresh_expiry and refresh_expiry.timestamp() <= self.clock():
            _set_account_error(conn, int(account["id"]), "TOKEN_EXPIRED", "Refresh token expirado")
            raise TikTokShopApiError("Refresh token expirado", integration_status="TOKEN_EXPIRED")
        refresh_token = self.cipher.decrypt(account["refresh_token_encrypted"])
        token_data = self._token_request(
            TOKEN_REFRESH_PATH,
            {
                "app_key": self.config.app_key,
                "app_secret": self.config.app_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        if int(token_data.get("user_type", -1)) != 1:
            raise TikTokShopError("Refresh retornou identidade nao Creator")
        if token_data.get("open_id") and str(token_data["open_id"]) != account["creator_open_id"]:
            raise TikTokShopError("Refresh retornou outro Creator")
        scopes = tuple(str(item) for item in token_data.get("granted_scopes") or [])
        missing = sorted(set(self.config.creator_scopes) - set(scopes))
        status = "MISSING_SCOPE" if missing else "CONNECTED"
        conn.execute(
            """
            UPDATE tiktok_creator_accounts
            SET status = ?, granted_scopes = ?, access_token_encrypted = ?,
                refresh_token_encrypted = ?, access_token_expires_at = ?,
                refresh_token_expires_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                status,
                json.dumps(scopes),
                self.cipher.encrypt(str(token_data["access_token"])),
                self.cipher.encrypt(str(token_data.get("refresh_token") or refresh_token)),
                _epoch_iso(token_data.get("access_token_expire_in")),
                _epoch_iso(token_data.get("refresh_token_expire_in")),
                "Scopes ausentes: " + ", ".join(missing) if missing else None,
                now_iso(),
                account["id"],
            ),
        )
        conn.commit()
        return str(token_data["access_token"])

    def _token_request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.auth_base_url}{path}?{urllib.parse.urlencode(params)}"
        status, payload, _ = self.transport.request("GET", url, timeout=30)
        if status >= 400 or int(payload.get("code", -1)) != 0:
            raise TikTokShopApiError(
                str(payload.get("message") or "Falha no OAuth TikTok Shop"),
                code=payload.get("code"),
                http_status=status,
                request_id=payload.get("request_id"),
                integration_status=_integration_status(status, payload.get("code"), payload.get("message")),
            )
        return dict(payload.get("data") or {})

    @classmethod
    def _lock_for(cls, account_id: int) -> threading.Lock:
        with cls._locks_guard:
            return cls._locks.setdefault(account_id, threading.Lock())


class TikTokShopClient:
    def __init__(
        self,
        config: TikTokShopConfig,
        transport: UrllibTransport | Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        max_retries: int = 3,
    ) -> None:
        self.config = config
        self.transport = transport or UrllibTransport()
        self.sleep = sleep
        self.clock = clock
        self.max_retries = max_retries

    def get_creator_profile(
        self,
        access_token: str,
        *,
        conn: sqlite3.Connection | None = None,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        return self.request(
            "GET", CREATOR_PROFILE_PATH, access_token, conn=conn, account_id=account_id
        )

    def search_creator_affiliate_orders(
        self,
        access_token: str,
        create_time_ge: int,
        create_time_lt: int,
        page_token: str | None = None,
        page_size: int = 100,
        *,
        conn: sqlite3.Connection | None = None,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"page_size": max(1, min(page_size, 100))}
        if page_token:
            query["page_token"] = page_token
        return self.request(
            "POST",
            CREATOR_ORDERS_PATH,
            access_token,
            query=query,
            body={"create_time_ge": create_time_ge, "create_time_lt": create_time_lt},
            conn=conn,
            account_id=account_id,
        )

    def request(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        conn: sqlite3.Connection | None = None,
        account_id: int | None = None,
    ) -> dict[str, Any]:
        body_text = json.dumps(body, ensure_ascii=False, separators=(",", ":")) if body else ""
        headers = {
            "content-type": "application/json",
            "x-tts-access-token": access_token,
        }
        final_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            params = dict(query or {})
            params.update({"app_key": self.config.app_key, "timestamp": int(self.clock())})
            params["sign"] = TikTokShopSignatureService.sign(
                path, params, self.config.app_secret, body_text, "application/json"
            )
            url = f"{self.config.api_base_url}{path}?{urllib.parse.urlencode(params)}"
            started = time.perf_counter()
            try:
                http_status, payload, _ = self.transport.request(
                    method,
                    url,
                    headers=headers,
                    body=body_text.encode("utf-8") if body_text else None,
                    timeout=30,
                )
                duration_ms = int((time.perf_counter() - started) * 1000)
                code = payload.get("code")
                if http_status < 400 and int(code or 0) == 0:
                    data = dict(payload.get("data") or {})
                    _observe(
                        conn,
                        account_id,
                        path,
                        payload.get("request_id"),
                        http_status,
                        code,
                        duration_ms,
                        _result_count(data),
                        "SUCCESS",
                        None,
                    )
                    return data
                api_error = TikTokShopApiError(
                    str(payload.get("message") or "TikTok Shop API error"),
                    code=code,
                    http_status=http_status,
                    request_id=payload.get("request_id"),
                    integration_status=_integration_status(http_status, code, payload.get("message")),
                )
                if _retryable(http_status, code) and attempt < self.max_retries:
                    final_error = api_error
                    _observe(
                        conn,
                        account_id,
                        path,
                        api_error.request_id,
                        http_status,
                        code,
                        duration_ms,
                        None,
                        "RETRY",
                        str(api_error),
                    )
                    self.sleep(min(2**attempt, 8))
                    continue
                _observe(
                    conn,
                    account_id,
                    path,
                    api_error.request_id,
                    http_status,
                    code,
                    duration_ms,
                    None,
                    "ERROR",
                    str(api_error),
                )
                raise api_error
            except TikTokShopTransportError as exc:
                final_error = exc
                if attempt < self.max_retries:
                    _observe(
                        conn,
                        account_id,
                        path,
                        None,
                        None,
                        None,
                        int((time.perf_counter() - started) * 1000),
                        None,
                        "RETRY",
                        str(exc),
                    )
                    self.sleep(min(2**attempt, 8))
                    continue
                _observe(
                    conn,
                    account_id,
                    path,
                    None,
                    None,
                    None,
                    int((time.perf_counter() - started) * 1000),
                    None,
                    "ERROR",
                    str(exc),
                )
                raise
        raise TikTokShopTransportError("Falha apos retries") from final_error


class TikTokShopOAuthService:
    def __init__(
        self,
        config: TikTokShopConfig,
        token_service: TikTokShopTokenService | None = None,
        client: TikTokShopClient | None = None,
    ) -> None:
        self.config = config
        self.token_service = token_service or TikTokShopTokenService(config)
        self.client = client or TikTokShopClient(config)

    def authorization_url(self, state: str) -> str:
        self.config.validate_for_oauth()
        return CREATOR_AUTH_URL + "?" + urllib.parse.urlencode(
            {"app_key": self.config.app_key, "state": state}
        )

    def complete_authorization(
        self, conn: sqlite3.Connection, auth_code: str
    ) -> dict[str, Any]:
        token_data = self.token_service.exchange_auth_code(auth_code)
        account_id = self.token_service.save_creator_authorization(conn, token_data)
        access_token = str(token_data["access_token"])
        try:
            profile = self.client.get_creator_profile(
                access_token, conn=conn, account_id=account_id
            )
            conn.execute(
                """
                UPDATE tiktok_creator_accounts
                SET creator_id = ?, creator_username = ?, display_name = ?,
                    market = COALESCE(?, market), updated_at = ?
                WHERE id = ?
                """,
                (
                    profile.get("creator_user_open_id"),
                    profile.get("username"),
                    profile.get("nickname") or profile.get("username"),
                    profile.get("selection_region"),
                    now_iso(),
                    account_id,
                ),
            )
            conn.commit()
        except TikTokShopApiError as exc:
            _set_account_error(conn, account_id, exc.integration_status, str(exc))
            profile = {}
        return {"account_id": account_id, "profile": profile}


def integration_status_payload(conn: sqlite3.Connection, config: TikTokShopConfig) -> dict[str, Any]:
    account = conn.execute(
        "SELECT * FROM tiktok_creator_accounts ORDER BY updated_at DESC, id DESC LIMIT 1"
    ).fetchone()
    if not account:
        status = "NOT_CONNECTED" if config.enabled else "DISABLED"
        return {
            "enabled": config.enabled,
            "configured": bool(config.app_key and config.app_secret and config.redirect_url),
            "status": status,
            "availability_reason": "BR_NOT_OPEN" if config.market.upper() == "BR" else None,
            "required_scopes": list(config.creator_scopes),
            "account": None,
            "orders_synced": 0,
            "last_sync_run": None,
            "recent_api_errors": 0,
        }
    orders = conn.execute(
        "SELECT COUNT(*) FROM affiliate_orders WHERE creator_account_id = ?",
        (account["id"],),
    ).fetchone()[0]
    last_sync_run = conn.execute(
        """
        SELECT id, sync_mode, window_start, window_end, status, pages_processed,
               orders_seen, orders_upserted, error_code, error_message,
               started_at, finished_at
        FROM affiliate_sync_runs WHERE creator_account_id = ?
        ORDER BY id DESC LIMIT 1
        """,
        (account["id"],),
    ).fetchone()
    recent_api_errors = conn.execute(
        """
        SELECT COUNT(*) FROM tiktok_api_requests
        WHERE creator_account_id = ? AND status = 'ERROR'
          AND datetime(created_at) >= datetime('now', '-24 hours')
        """,
        (account["id"],),
    ).fetchone()[0]
    return {
        "enabled": config.enabled,
        "configured": bool(config.app_key and config.app_secret and config.redirect_url),
        "status": account["status"] if config.enabled else "DISABLED",
        "availability_reason": "BR_NOT_OPEN"
        if config.market.upper() == "BR" and not config.enabled
        else None,
        "required_scopes": list(config.creator_scopes),
        "account": {
            "id": account["id"],
            "creator_username": account["creator_username"],
            "display_name": account["display_name"],
            "market": account["market"],
            "granted_scopes": json.loads(account["granted_scopes"] or "[]"),
            "last_sync_at": account["last_sync_at"],
            "last_error": account["last_error"],
        },
        "orders_synced": int(orders),
        "last_sync_run": dict(last_sync_run) if last_sync_run else None,
        "recent_api_errors": int(recent_api_errors),
    }


def disconnect_creator_account(conn: sqlite3.Connection, account_id: int) -> None:
    conn.execute(
        """
        UPDATE tiktok_creator_accounts
        SET status = 'NOT_CONNECTED', access_token_encrypted = NULL,
            refresh_token_encrypted = NULL, access_token_expires_at = NULL,
            refresh_token_expires_at = NULL, last_error = NULL, updated_at = ?
        WHERE id = ?
        """,
        (now_iso(), account_id),
    )
    conn.commit()


def _observe(
    conn: sqlite3.Connection | None,
    account_id: int | None,
    endpoint: str,
    request_id: str | None,
    http_status: int | None,
    api_code: Any,
    duration_ms: int,
    result_count: int | None,
    status: str,
    error_message: str | None,
) -> None:
    if conn is None:
        return
    conn.execute(
        """
        INSERT INTO tiktok_api_requests (
            creator_account_id, endpoint, request_id, http_status, api_code,
            duration_ms, result_count, status, error_message, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            account_id,
            endpoint,
            request_id,
            http_status,
            str(api_code) if api_code is not None else None,
            duration_ms,
            result_count,
            status,
            error_message,
            now_iso(),
        ),
    )
    conn.commit()


def _set_account_error(
    conn: sqlite3.Connection, account_id: int, status: str, message: str
) -> None:
    conn.execute(
        "UPDATE tiktok_creator_accounts SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
        (status, message[:1000], now_iso(), account_id),
    )
    conn.commit()


def _integration_status(http_status: int | None, code: Any, message: Any) -> str:
    code_text = str(code) if code is not None else ""
    message_text = str(message or "").lower()
    if code_text == "105002":
        return "TOKEN_EXPIRED"
    if code_text in {"105001", "101000"}:
        return "NOT_CONNECTED"
    if code_text == "16501011" or "allowlist" in message_text or "not approved" in message_text:
        return "API_NOT_APPROVED"
    if code_text == "105005" or "scope" in message_text or "permission" in message_text:
        return "MISSING_SCOPE"
    if http_status == 401:
        return "NOT_CONNECTED"
    return "ERROR"


def _retryable(http_status: int | None, code: Any) -> bool:
    return http_status == 429 or bool(http_status and http_status >= 500) or str(code) in {
        "36009002",
        "36009003",
        "36009007",
    }


def _result_count(data: dict[str, Any]) -> int | None:
    for key in ("orders", "products", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return len(value)
    return None


def _epoch_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _blob(data: bytes) -> tuple[_DataBlob, Any]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    output = _DataBlob()
    result = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(source),
        "TikTok Shop Creator token",
        None,
        None,
        None,
        0x1,
        ctypes.byref(output),
    )
    del source_buffer
    if not result:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    source, source_buffer = _blob(data)
    output = _DataBlob()
    result = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)
    )
    del source_buffer
    if not result:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)
