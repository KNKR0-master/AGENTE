"""Official Windows launcher for the local Publisher Desk.

The launcher owns only process orchestration. Chrome/CDP, profile ownership,
authentication detection, and worker lifecycle remain in the application.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
PORT = 8765
LOCAL_URL = f"http://{HOST}:{PORT}/publisher.html"
API_URL = f"http://{HOST}:{PORT}/api/publisher"
ACCOUNT_ID = 1
READY_TIMEOUT_SECONDS = 45
AUTH_TIMEOUT_SECONDS = 90


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def request_json(url: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 3) -> dict | None:
    body = None
    headers = {}
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def publisher_is_running() -> bool:
    payload = request_json(API_URL)
    return isinstance(payload, dict) and isinstance(payload.get("accounts"), list) and "worker_config" in payload


def publisher_snapshot() -> dict | None:
    payload = request_json(API_URL)
    return payload if isinstance(payload, dict) else None


def account_snapshot(payload: dict | None) -> dict:
    accounts = (payload or {}).get("accounts") or []
    return next((row for row in accounts if int(row.get("id", 0)) == ACCOUNT_ID), {})


def wait_for_server(process: subprocess.Popen[bytes] | None) -> bool:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if publisher_is_running():
            return True
        if process is not None and process.poll() is not None:
            return False
        time.sleep(0.25)
    return False


def wait_for_authentication() -> tuple[bool, str, bool, str, str]:
    """Run the existing read-only profile inspection once and poll its result."""
    queued = request_json(
        f"http://{HOST}:{PORT}/api/publisher/inspect-existing-session",
        method="POST",
        payload={"id": ACCOUNT_ID, "navigate": True},
        timeout=10,
    )
    if not queued or queued.get("code") != "EXISTING_SESSION_INSPECTION_QUEUED":
        return False, "UNKNOWN", False, "ACCOUNT_IDENTITY_UNKNOWN", ""

    deadline = time.monotonic() + AUTH_TIMEOUT_SECONDS
    last = {}
    while time.monotonic() < deadline:
        time.sleep(1)
        last = account_snapshot(publisher_snapshot())
        runtime_auth = last.get("runtime_auth_state")
        auth_status = last.get("auth_status")
        identity = last.get("account_identity", "ACCOUNT_IDENTITY_UNKNOWN")
        handle = last.get("tiktok_username", "")
        if runtime_auth == "AUTHENTICATED" or (auth_status == "AUTHENTICATED" and identity == "ACCOUNT_CONFIRMED"):
            return True, "AUTHENTICATED", True, identity, handle
        if runtime_auth == "NEEDS_LOGIN" or auth_status == "NEEDS_LOGIN":
            return True, "NEEDS_LOGIN", False, identity, handle
    return (
        True,
        str(last.get("runtime_auth_state") or "UNKNOWN"),
        False,
        str(last.get("account_identity") or "ACCOUNT_IDENTITY_UNKNOWN"),
        str(last.get("tiktok_username") or ""),
    )


def start_server(root: Path) -> subprocess.Popen:
    log_dir = root / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "publisher-server.log"
    log = log_path.open("a", encoding="utf-8", buffering=1)
    flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = subprocess.Popen(
        [sys.executable, str(root / "dashboard_server.py")],
        cwd=str(root),
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        shell=False,
        creationflags=flags,
    )
    # The child owns the file descriptor for its lifetime; the supervisor can close its handle.
    log.close()
    return process


def stop_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        print("Servidor nao encerrou em 30s; nenhuma finalizacao forcada foi executada.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia o Publisher Desk sem duplicar a instancia local.")
    parser.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = (args.root or project_root()).resolve()
    if not (root / "dashboard_server.py").is_file():
        print(f"PROJECT_ROOT invalido: {root}")
        return 2

    if publisher_is_running():
        print("Publisher ja esta em execucao; abrindo a interface existente.")
        webbrowser.open(LOCAL_URL)
        return 0

    process = start_server(root)
    print(f"Publisher iniciado; log: {root / 'logs' / 'publisher-server.log'}")
    if not wait_for_server(process):
        print("Falha ao iniciar o Publisher; consulte o log.")
        return 1

    runtime_ready, auth_result, session_verified, identity, handle = wait_for_authentication()
    print("SERVER_STARTED=true")
    print(f"BROWSER_RUNTIME_READY={str(runtime_ready).lower()}")
    print(f"AUTH_RESULT={auth_result}")
    print(f"SESSION_VERIFIED={str(session_verified).lower()}")
    print(f"ACCOUNT_IDENTITY={identity}")
    print(f"OBSERVED_TIKTOK_ACCOUNT={handle}")
    if auth_result == "NEEDS_LOGIN":
        print("MANUAL_LOGIN_REQUIRED=true")
    elif auth_result == "UNKNOWN":
        print("MANUAL_LOGIN_REQUIRED=unknown")
    print("PUBLISHER_UI_AVAILABLE=true")
    print(f"Publisher disponivel em {LOCAL_URL}")
    webbrowser.open(LOCAL_URL)

    try:
        process.wait()
    except KeyboardInterrupt:
        print("Encerrando Publisher graciosamente...")
        stop_server(process)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
