"""Chrome ownership and Playwright CDP attachment for the Publisher runtime."""
from dataclasses import dataclass
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

from src.publisher.manual_profile import ProfileOwnership, windows_arguments, chrome_executable


LOCALHOST = '127.0.0.1'


def chrome_process_details(profile):
    """Return only Chrome processes that explicitly reference this profile."""
    command = "@(Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" -ErrorAction Stop | Select-Object ProcessId,ParentProcessId,CreationDate,CommandLine) | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
            capture_output=True, text=True, check=True, timeout=20,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.SubprocessError):
        # Fail closed when Chrome's own singleton markers indicate ownership;
        # a clean profile can be considered free when process inventory is
        # unavailable, matching ProfileOwnership's existing policy.
        markers = ('SingletonLock', 'SingletonCookie', 'SingletonSocket')
        if any((Path(profile) / marker).exists() for marker in markers):
            return [{'pid': -1, 'parent_pid': None, 'creation_date': None,
                     'command_line': '', 'args': [], 'user_data_dir': str(Path(profile).resolve()),
                     'cdp_address': None, 'cdp_port': None}]
        return []
    records = json.loads(result.stdout or '[]')
    if isinstance(records, dict):
        records = [records]
    expected = os.path.normcase(str(Path(profile).resolve()))
    details = []
    for record in records:
        command_line = record.get('CommandLine') or ''
        args = windows_arguments(command_line)
        user_data_dir = None
        cdp_address = None
        cdp_port = None
        for index, arg in enumerate(args):
            if arg.startswith('--user-data-dir='):
                user_data_dir = arg.split('=', 1)[1]
            elif arg == '--user-data-dir' and index + 1 < len(args):
                user_data_dir = args[index + 1]
            elif arg.startswith('--remote-debugging-address='):
                cdp_address = arg.split('=', 1)[1]
            elif arg.startswith('--remote-debugging-port='):
                try:
                    cdp_port = int(arg.split('=', 1)[1])
                except ValueError:
                    cdp_port = None
        if user_data_dir and os.path.normcase(str(Path(user_data_dir).resolve())) == expected:
            details.append({
                'pid': int(record['ProcessId']),
                'parent_pid': int(record['ParentProcessId']) if record.get('ParentProcessId') else None,
                'creation_date': record.get('CreationDate'),
                'command_line': command_line,
                'args': args,
                'user_data_dir': str(Path(user_data_dir).resolve()),
                'cdp_address': cdp_address,
                'cdp_port': cdp_port,
            })
    return details


def endpoint_metadata(address, port):
    if address != LOCALHOST or not isinstance(port, int) or not 1 <= port <= 65535:
        return None
    try:
        with urlopen(f'http://{address}:{port}/json/version', timeout=2) as response:
            payload = json.load(response)
        if not payload.get('webSocketDebuggerUrl'):
            return None
        return {'address': address, 'port': port, 'endpoint': f'http://{address}:{port}',
                'browser': payload.get('Browser', '')}
    except Exception:
        return None


def close_process_gracefully(pid):
    command = f"$p=Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue; if($p){{$p.CloseMainWindow() | Out-Null; 'SIGNAL_SENT'}}"
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
        capture_output=True, text=True, check=False, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return 'SIGNAL_SENT' in result.stdout


def process_is_alive(pid):
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         f"if(Get-Process -Id {int(pid)} -ErrorAction SilentlyContinue){{exit 1}}else{{exit 0}}"],
        capture_output=True, check=False, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode != 0


@dataclass
class RuntimeSession:
    profile: Path
    token: str
    browser: object
    context: object
    chrome_pid: int
    cdp_port: int
    browser_owned_by_runtime: bool


class BrowserRuntimeManager:
    """Own or attach to exactly one Chrome runtime per persistent profile."""

    def __init__(self, root, db, *, ownership=None, process_finder=chrome_process_details,
                 launcher=subprocess.Popen, executable=chrome_executable,
                 endpoint_probe=endpoint_metadata, closer=close_process_gracefully,
                 alive=process_is_alive, sleep_fn=time.sleep, port_start=9222):
        self.root = Path(root).resolve()
        self.db = db
        self.ownership = ownership or ProfileOwnership(db)
        self.process_finder = process_finder
        self.launcher = launcher
        self.executable = executable
        self.endpoint_probe = endpoint_probe
        self.closer = closer
        self.alive = alive
        self.sleep_fn = sleep_fn
        self.port_start = port_start
        self.sessions = {}

    @staticmethod
    def _key(profile):
        return os.path.normcase(str(Path(profile).resolve()))

    def _existing_runtime(self, profile):
        details = self.process_finder(profile)
        if not details:
            # WMI can be unavailable to a non-elevated Windows process. Reuse
            # only the exact runtime already persisted as CDP_ATTACHED, and
            # only after both its PID and localhost endpoint are verified.
            metadata = getattr(self.ownership, 'runtime_metadata', lambda _profile: None)(profile)
            if metadata and metadata.get('state') == 'CDP_ATTACHED':
                pid = metadata.get('browser_pid')
                port = metadata.get('cdp_port')
                if pid and port and self.alive(int(pid)):
                    endpoint = self.endpoint_probe(LOCALHOST, int(port))
                    if endpoint:
                        return ({
                            'pid': int(pid), 'parent_pid': None, 'creation_date': None,
                            'command_line': '', 'args': [],
                            'user_data_dir': str(Path(profile).resolve()),
                            'cdp_address': LOCALHOST, 'cdp_port': int(port),
                        }, endpoint)
        if not details:
            return None
        valid = []
        for detail in details:
            endpoint = self.endpoint_probe(detail.get('cdp_address'), detail.get('cdp_port'))
            if endpoint:
                valid.append((detail, endpoint))
        if len(valid) > 1:
            raise ValueError('DUPLICATE_PROFILE_RUNTIME')
        if not valid:
            raise ValueError('CDP_ENDPOINT_UNAVAILABLE')
        return valid[0]

    def _new_port(self):
        port = self.port_start
        while port < self.port_start + 100:
            if not self.endpoint_probe(LOCALHOST, port):
                return port
            port += 1
        raise RuntimeError('CDP_PORT_UNAVAILABLE')

    def _connect(self, playwright, endpoint):
        browser = playwright.chromium.connect_over_cdp(endpoint['endpoint'])
        contexts = list(browser.contexts)
        if not contexts:
            raise RuntimeError('CDP_CONTEXT_NOT_FOUND')
        return browser, contexts[0]

    def get_context(self, playwright, profile):
        profile = Path(profile).resolve()
        key = self._key(profile)
        if key in self.sessions:
            return self.sessions[key].context

        existing = self._existing_runtime(profile)
        browser_owned = False
        process = None
        if existing:
            detail, endpoint = existing
            token = self.ownership.adopt_existing(profile, pid=detail['pid'], cdp_port=endpoint['port'])
            chrome_pid, cdp_port = detail['pid'], endpoint['port']
        else:
            token = self.ownership.acquire(profile, 'PLAYWRIGHT')
            cdp_port = self._new_port()
            args = [str(self.executable()), f'--user-data-dir={profile}',
                    f'--remote-debugging-address={LOCALHOST}',
                    f'--remote-debugging-port={cdp_port}', 'about:blank']
            try:
                process = self.launcher(args, shell=False)
                browser_owned = True
                deadline = time.monotonic() + 30
                endpoint = None
                while time.monotonic() < deadline:
                    endpoint = self.endpoint_probe(LOCALHOST, cdp_port)
                    if endpoint:
                        break
                    if hasattr(process, 'poll') and process.poll() is not None:
                        raise RuntimeError('CHROME_EXITED_BEFORE_CDP')
                    time.sleep(.25)
                if not endpoint:
                    raise TimeoutError('CDP_ENDPOINT_TIMEOUT')
                chrome_pid = process.pid
            except Exception:
                try:
                    self.ownership.detach(profile, token, keep_browser=False)
                except Exception:
                    pass
                raise

        try:
            browser, context = self._connect(playwright, endpoint)
            self.ownership.activate_runtime(profile, token, 'CDP_ATTACHED', pid=chrome_pid,
                                             cdp_port=cdp_port, browser_owned=browser_owned)
        except Exception:
            if browser_owned and process is not None and self.alive(chrome_pid):
                self.closer(chrome_pid)
            try:
                self.ownership.detach(profile, token, keep_browser=False)
            except Exception:
                pass
            raise
        session = RuntimeSession(profile, token, browser, context, chrome_pid, cdp_port, browser_owned)
        self.sessions[key] = session
        return context

    def session(self, profile):
        return self.sessions.get(self._key(profile))

    def verify_auth(self, profile, verifier):
        session = self.session(profile)
        if not session:
            raise ValueError('CDP_SESSION_NOT_ATTACHED')
        result = verifier(session.context)
        if result.get('auth_result') != 'AUTHENTICATED' or result.get('account_identity') != 'ACCOUNT_CONFIRMED':
            raise ValueError('MANUAL_LOGIN_REQUIRED')
        return result

    def detach(self, profile, *, keep_browser=True):
        key = self._key(profile)
        session = self.sessions.pop(key, None)
        if not session:
            return False
        if session.browser_owned_by_runtime and not keep_browser:
            # Detach the CDP transport before asking the owned process to close.
            # Browser.close can wait on a closing CDP transport, while
            # CloseMainWindow remains a graceful, process-scoped fallback.
            try:
                session.browser._impl_obj._connection.stop_sync()
            except Exception:
                pass
            close_requested = False
            deadline = time.monotonic() + 20
            while self.alive(session.chrome_pid) and time.monotonic() < deadline:
                if not close_requested or int((deadline - time.monotonic()) * 4) % 4 == 0:
                    # Repeated graceful requests handle a busy Chrome window;
                    # no force-kill is used on the normal shutdown path.
                    close_requested = self.closer(session.chrome_pid) or close_requested
                self.sleep_fn(.25)
            self.ownership.detach(session.profile, session.token, keep_browser=False)
        else:
            # Playwright's private transport stop detaches without closing an
            # external browser that remains owned by the user.
            session.browser._impl_obj._connection.stop_sync()
            self.ownership.detach(session.profile, session.token, keep_browser=True)
        return True

    def shutdown(self):
        for profile in [session.profile for session in list(self.sessions.values())]:
            self.detach(profile, keep_browser=False)
