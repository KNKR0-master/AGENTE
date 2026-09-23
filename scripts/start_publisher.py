"""Official Windows launcher for the local Publisher Desk.

The launcher owns only process orchestration. Chrome/CDP, profile ownership,
authentication detection, and worker lifecycle remain in the application.
"""

from __future__ import annotations

import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from ctypes import wintypes
from pathlib import Path
from threading import Event, Lock, Thread


HOST = "127.0.0.1"
PORT = 8765
LOCAL_URL = f"http://{HOST}:{PORT}/publisher.html"
API_URL = f"http://{HOST}:{PORT}/api/publisher"
RUNTIME_INFO_URL = f"http://{HOST}:{PORT}/api/publisher/runtime-info"
ACCOUNT_ID = 1
READY_TIMEOUT_SECONDS = 45
AUTH_TIMEOUT_SECONDS = 90
SHUTDOWN_TIMEOUT_SECONDS = 20
CONSOLE_CLOSE_WAIT_SECONDS = 4
CONSOLE_SIGNAL_TIMEOUT_SECONDS = 1

CTRL_C_EVENT = 0
CTRL_BREAK_EVENT = 1
CTRL_CLOSE_EVENT = 2
CTRL_LOGOFF_EVENT = 5
CTRL_SHUTDOWN_EVENT = 6

CURRENT_COMPATIBLE_INSTANCE = "CURRENT_COMPATIBLE_INSTANCE"
OLD_OR_INCOMPATIBLE_PUBLISHER = "OLD_OR_INCOMPATIBLE_PUBLISHER"
FOREIGN_PROCESS_ON_PORT = "FOREIGN_PROCESS_ON_PORT"
NO_INSTANCE = "NO_INSTANCE"
EXPECTED_API_VERSION = "publisher-api-v1"
EXPECTED_COMPATIBILITY = "publisher-ui-2a.3.2"


class ShutdownCoordinator:
    """Run the launcher's shutdown callback once, regardless of the trigger."""

    def __init__(self, callback, *, timeout: float = SHUTDOWN_TIMEOUT_SECONDS, event_logger=None):
        self.callback = callback
        self.timeout = timeout
        self.event_logger = event_logger or (lambda event, **fields: None)
        self.requested = Event()
        self.completed = Event()
        self._lock = Lock()
        self._thread = None

    def request(self, reason: str) -> bool:
        with self._lock:
            if self.requested.is_set():
                return False
            self.requested.set()
            self.event_logger("SHUTDOWN_REQUESTED", reason=reason)
            self._thread = Thread(
                target=self._run,
                args=(reason,),
                name="publisher-shutdown",
                daemon=True,
            )
            self._thread.start()
            return True

    def _run(self, reason: str) -> None:
        self.event_logger("SHUTDOWN_ROUTINE_STARTED", reason=reason)
        try:
            self.callback(reason)
        finally:
            self.event_logger("SHUTDOWN_ROUTINE_FINISHED", reason=reason)
            self.completed.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.completed.wait(self.timeout if timeout is None else timeout)


class WindowsConsoleHandler:
    """Route Windows console close/control events into ShutdownCoordinator."""

    _EVENT_NAMES = {
        CTRL_C_EVENT: "CTRL_C_EVENT",
        CTRL_BREAK_EVENT: "CTRL_BREAK_EVENT",
        CTRL_CLOSE_EVENT: "CTRL_CLOSE_EVENT",
        CTRL_LOGOFF_EVENT: "CTRL_LOGOFF_EVENT",
        CTRL_SHUTDOWN_EVENT: "CTRL_SHUTDOWN_EVENT",
    }
    _HANDLED_EVENTS = set(_EVENT_NAMES)

    def __init__(self, coordinator: ShutdownCoordinator, *, api=None, before_request=None, event_logger=None):
        self.coordinator = coordinator
        self.api = api
        self.before_request = before_request
        self.event_logger = event_logger or (lambda event, **fields: None)
        self._callback = None

    def install(self) -> bool:
        if self.api is None and os.name != "nt":
            return False
        if self.api is None:
            self.api = ctypes.WinDLL("Kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.DWORD)
        self._callback = callback_type(self._handle)
        if not self.api.SetConsoleCtrlHandler(self._callback, True):
            raise ctypes.WinError(ctypes.get_last_error())
        return True

    def uninstall(self) -> None:
        if self.api is not None and self._callback is not None:
            self.api.SetConsoleCtrlHandler(self._callback, False)
            self._callback = None

    def _handle(self, event_code: int) -> bool:
        if event_code not in self._HANDLED_EVENTS:
            return False
        reason = self._EVENT_NAMES[event_code]
        self.event_logger(f"{reason}_RECEIVED")
        if event_code == CTRL_CLOSE_EVENT and self.before_request is not None:
            # Deliver the short cooperative signal before Windows can tear down
            # the console process. The full shutdown remains outside the callback.
            try:
                accepted = self.before_request()
                self.event_logger("COOPERATIVE_SERVER_SHUTDOWN_CALLED", accepted=bool(accepted))
            except Exception as exc:
                # A close-event callback must still arm the idempotent local
                # coordinator even if the short HTTP handoff is unavailable.
                self.event_logger("COOPERATIVE_SERVER_SHUTDOWN_ERROR", error_type=type(exc).__name__)
        self.coordinator.request(reason)
        # Windows gives a console process only a short close-event window.
        # Wait briefly so normal cleanup can finish without blocking forever.
        if event_code == CTRL_CLOSE_EVENT:
            self.coordinator.wait(CONSOLE_CLOSE_WAIT_SECONDS)
            self.event_logger(
                "HANDLER_RETURNED_BEFORE_CHILD_EXIT",
                value=not self.coordinator.completed.is_set(),
            )
        return True


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def launcher_event_logger(root: Path):
    path = root / "logs" / "publisher-launcher.log"
    lock = Lock()
    path.parent.mkdir(parents=True, exist_ok=True)

    def write(event: str, **fields) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        suffix = " ".join(
            f"{key}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')[:200]}"
            for key, value in fields.items()
        )
        with lock:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"{timestamp} event={event}{(' ' + suffix) if suffix else ''}\n")

    return write


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


def _runtime_info_probe() -> tuple[dict | None, int | None]:
    request = urllib.request.Request(RUNTIME_INFO_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=1) as response:
            return json.loads(response.read().decode("utf-8")), response.status
    except urllib.error.HTTPError as exc:
        return None, exc.code
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None, None


def _port_is_open() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=0.5):
            return True
    except OSError:
        return False


def classify_runtime_instance() -> tuple[str, dict | None]:
    """Identify the process on the Publisher port without trusting the port alone."""
    payload, status = _runtime_info_probe()
    if isinstance(payload, dict):
        compatible = (
            payload.get("app") == "publisher-desk"
            and payload.get("api_version") == EXPECTED_API_VERSION
            and payload.get("compatibility") == EXPECTED_COMPATIBILITY
        )
        return (CURRENT_COMPATIBLE_INSTANCE if compatible else OLD_OR_INCOMPATIBLE_PUBLISHER), payload
    if _port_is_open():
        return (OLD_OR_INCOMPATIBLE_PUBLISHER if status is not None else FOREIGN_PROCESS_ON_PORT), None
    return NO_INSTANCE, None


def publisher_is_running() -> bool:
    status, _ = classify_runtime_instance()
    return status == CURRENT_COMPATIBLE_INSTANCE


def publisher_snapshot() -> dict | None:
    payload = request_json(API_URL)
    return payload if isinstance(payload, dict) else None


def account_snapshot(payload: dict | None) -> dict:
    accounts = (payload or {}).get("accounts") or []
    return next((row for row in accounts if int(row.get("id", 0)) == ACCOUNT_ID), {})


def wait_for_server(process: subprocess.Popen[bytes] | None, stop_event: Event | None = None) -> bool:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        if publisher_is_running():
            return True
        if process is not None and process.poll() is not None:
            return False
        if stop_event is not None:
            stop_event.wait(0.25)
        else:
            time.sleep(0.25)
    return False


def wait_for_authentication(stop_event: Event | None = None) -> tuple[bool, str, bool, str, str]:
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
        if stop_event is not None:
            if stop_event.wait(1):
                return False, "SHUTDOWN_REQUESTED", False, "ACCOUNT_IDENTITY_UNKNOWN", ""
        else:
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
    if os.name == "nt":
        # Keep the server alive long enough to receive the cooperative
        # shutdown request when the launcher's console window is closed.
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0)
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


def stop_server(process: subprocess.Popen, *, timeout: float = SHUTDOWN_TIMEOUT_SECONDS, event_logger=None) -> bool:
    event_logger = event_logger or (lambda event, **fields: None)
    if process.poll() is not None:
        event_logger("CHILD_PROCESS_EXIT_OBSERVED", value=True, already_exited=True)
        return True
    shutdown_requested = request_server_shutdown()
    event_logger("COOPERATIVE_SERVER_SHUTDOWN_CALLED", accepted=shutdown_requested)
    if not shutdown_requested:
        if os.name == "nt":
            process.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=timeout)
        event_logger("CHILD_PROCESS_EXIT_OBSERVED", value=True)
        return True
    except subprocess.TimeoutExpired:
        if shutdown_requested:
            # The cooperative endpoint may be unavailable during a race with
            # server startup/teardown; use the existing console signal once.
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
        event_logger("CHILD_PROCESS_EXIT_OBSERVED", value=False, timeout_seconds=timeout)
        print(f"Servidor nao encerrou em {timeout:g}s; nenhuma finalizacao forcada foi executada.")
        return False


def request_server_shutdown(*, timeout: float = 2) -> bool:
    return bool(request_json(
        f"http://{HOST}:{PORT}/api/publisher/shutdown",
        method="POST",
        payload={},
        timeout=timeout,
    ))


def request_shutdown(coordinator: ShutdownCoordinator, reason: str) -> bool:
    """Small seam used by the main loop and by offline tests."""
    return coordinator.request(reason)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inicia o Publisher Desk sem duplicar a instancia local.")
    parser.add_argument("--root", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()
    root = (args.root or project_root()).resolve()
    if not (root / "dashboard_server.py").is_file():
        print(f"PROJECT_ROOT invalido: {root}")
        return 2

    instance_status, _runtime_info = classify_runtime_instance()
    if instance_status == CURRENT_COMPATIBLE_INSTANCE:
        print("Publisher ja esta em execucao; abrindo a interface existente.")
        webbrowser.open(LOCAL_URL)
        return 0
    if instance_status == OLD_OR_INCOMPATIBLE_PUBLISHER:
        print("Existe um Publisher antigo ou incompativel na porta 8765; encerre-o graciosamente antes de iniciar.")
        return 3
    if instance_status == FOREIGN_PROCESS_ON_PORT:
        print("A porta 8765 esta ocupada por outro processo; nenhuma instancia foi encerrada.")
        return 4

    process = start_server(root)
    log_event = launcher_event_logger(root)
    coordinator = ShutdownCoordinator(
        lambda reason: stop_server(process, timeout=SHUTDOWN_TIMEOUT_SECONDS, event_logger=log_event),
        timeout=SHUTDOWN_TIMEOUT_SECONDS,
        event_logger=log_event,
    )
    console_handler = WindowsConsoleHandler(
        coordinator,
        before_request=lambda: request_server_shutdown(timeout=CONSOLE_SIGNAL_TIMEOUT_SECONDS),
        event_logger=log_event,
    )
    console_handler.install()
    print(f"Publisher iniciado; log: {root / 'logs' / 'publisher-server.log'}")
    try:
        if not wait_for_server(process, coordinator.requested):
            if not coordinator.requested.is_set():
                print("Falha ao iniciar o Publisher; consulte o log.")
                return 1
            return 0

        runtime_ready, auth_result, session_verified, identity, handle = wait_for_authentication(coordinator.requested)
        if coordinator.requested.is_set():
            return 0
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

        while process.poll() is None:
            if coordinator.requested.is_set():
                break
            try:
                process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                continue
    except KeyboardInterrupt:
        print("Encerrando Publisher graciosamente...")
        request_shutdown(coordinator, "KEYBOARD_INTERRUPT")
    finally:
        if not coordinator.requested.is_set():
            request_shutdown(coordinator, "MAIN_EXIT")
        if not coordinator.wait():
            print(f"Shutdown nao concluiu em {SHUTDOWN_TIMEOUT_SECONDS:g}s; nenhuma finalizacao forcada foi executada.")
        console_handler.uninstall()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
