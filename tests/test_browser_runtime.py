import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.publisher.browser_runtime import BrowserRuntimeManager


class FakeConnection:
    def __init__(self):
        self.stopped = False

    def stop_sync(self):
        self.stopped = True


class FakeBrowser:
    def __init__(self):
        self.contexts = [SimpleNamespace(pages=[])]
        self._impl_obj = SimpleNamespace(_connection=FakeConnection())
        self.close = MagicMock()


class FakeOwnership:
    def __init__(self):
        self.calls = []
        self.token = 'runtime-token'

    def acquire(self, profile, mode):
        self.calls.append(('acquire', str(profile), mode))
        return self.token

    def adopt_existing(self, profile, *, pid, cdp_port):
        self.calls.append(('adopt', str(profile), pid, cdp_port))
        return self.token

    def activate_runtime(self, profile, token, state, *, pid, cdp_port, browser_owned):
        self.calls.append(('activate', str(profile), state, pid, cdp_port, browser_owned))

    def detach(self, profile, token, *, keep_browser):
        self.calls.append(('detach', str(profile), keep_browser))
        return True


class BrowserRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.ownership = FakeOwnership()
        self.browser = FakeBrowser()
        self.playwright = MagicMock()
        self.playwright.chromium.connect_over_cdp.return_value = self.browser

    def manager(self, processes, endpoint=None, launcher=None):
        endpoint = endpoint or {'address': '127.0.0.1', 'port': 9230, 'endpoint': 'http://127.0.0.1:9230'}
        return BrowserRuntimeManager(
            self.root, self.root / 'runtime.db', ownership=self.ownership,
            process_finder=lambda profile: processes,
            launcher=launcher or MagicMock(return_value=SimpleNamespace(pid=777, poll=lambda: None)),
            executable=lambda: Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            endpoint_probe=lambda address, port: endpoint if (address, port) == (endpoint['address'], endpoint['port']) else None,
            port_start=9230,
        )

    def test_existing_valid_chrome_is_attached_without_launch(self):
        processes = [{'pid': 18168, 'cdp_address': '127.0.0.1', 'cdp_port': 9230}]
        launcher = MagicMock()
        runtime = self.manager(processes, launcher=launcher)
        context = runtime.get_context(self.playwright, self.profile)
        self.assertIs(context, self.browser.contexts[0])
        launcher.assert_not_called()
        self.assertIn(('adopt', str(self.profile.resolve()), 18168, 9230), self.ownership.calls)
        runtime.detach(self.profile, keep_browser=True)
        self.assertTrue(self.browser._impl_obj._connection.stopped)
        self.browser.close.assert_not_called()

    def test_absent_chrome_launches_once_with_safe_argument_list(self):
        process = SimpleNamespace(pid=777, poll=lambda: None)
        launcher = MagicMock(return_value=process)
        probe_calls = {'value': 0}
        endpoint = {'address': '127.0.0.1', 'port': 9230, 'endpoint': 'http://127.0.0.1:9230'}
        def probe(address, port):
            if port != 9230:
                return None
            probe_calls['value'] += 1
            return endpoint if probe_calls['value'] >= 2 else None
        runtime = BrowserRuntimeManager(
            self.root, self.root / 'runtime.db', ownership=self.ownership,
            process_finder=lambda profile: [], launcher=launcher,
            executable=lambda: Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            endpoint_probe=probe, port_start=9230,
        )
        runtime.get_context(self.playwright, self.profile)
        launcher.assert_called_once()
        args = launcher.call_args.args[0]
        self.assertEqual(args[1], f'--user-data-dir={self.profile.resolve()}')
        self.assertIn('--remote-debugging-address=127.0.0.1', args)
        self.assertIn('--remote-debugging-port=9230', args)
        self.assertFalse(launcher.call_args.kwargs['shell'])
        self.assertIn(('activate', str(self.profile.resolve()), 'CDP_ATTACHED', 777, 9230, True), self.ownership.calls)

    def test_live_process_without_cdp_is_blocked_and_not_relaunched(self):
        processes = [{'pid': 18880, 'cdp_address': '127.0.0.1', 'cdp_port': 9231}]
        launcher = MagicMock()
        runtime = self.manager(processes, endpoint=None, launcher=launcher)
        with self.assertRaisesRegex(ValueError, 'CDP_ENDPOINT_UNAVAILABLE'):
            runtime.get_context(self.playwright, self.profile)
        launcher.assert_not_called()

    def test_auth_gate_blocks_needs_login_or_mismatch(self):
        runtime = self.manager([{'pid': 18168, 'cdp_address': '127.0.0.1', 'cdp_port': 9230}])
        runtime.get_context(self.playwright, self.profile)
        with self.assertRaisesRegex(ValueError, 'MANUAL_LOGIN_REQUIRED'):
            runtime.verify_auth(self.profile, lambda context: {
                'auth_result': 'NEEDS_LOGIN', 'account_identity': 'ACCOUNT_IDENTITY_UNKNOWN'})
        with self.assertRaisesRegex(ValueError, 'MANUAL_LOGIN_REQUIRED'):
            runtime.verify_auth(self.profile, lambda context: {
                'auth_result': 'AUTHENTICATED', 'account_identity': 'ACCOUNT_MISMATCH'})

    def test_runtime_owned_shutdown_is_graceful(self):
        process = SimpleNamespace(pid=777, poll=lambda: None)
        launcher = MagicMock(return_value=process)
        calls = []
        alive = {'value': True}
        probe_calls = {'value': 0}
        endpoint = {'address': '127.0.0.1', 'port': 9230, 'endpoint': 'http://127.0.0.1:9230'}
        def probe(address, port):
            if port != 9230:
                return None
            probe_calls['value'] += 1
            return endpoint if probe_calls['value'] >= 2 else None
        runtime = BrowserRuntimeManager(
            self.root, self.root / 'runtime.db', ownership=self.ownership,
            process_finder=lambda profile: [], launcher=launcher,
            executable=lambda: Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            endpoint_probe=probe, closer=lambda pid: (calls.append(pid), alive.__setitem__('value', False)),
            alive=lambda pid: alive['value'], sleep_fn=lambda delay: None, port_start=9230,
        )
        runtime.get_context(self.playwright, self.profile)
        runtime.detach(self.profile, keep_browser=False)
        self.assertEqual(calls, [777])
        self.assertTrue(self.browser._impl_obj._connection.stopped)
        self.browser.close.assert_not_called()
        self.assertIn(('detach', str(self.profile.resolve()), False), self.ownership.calls)

    def test_duplicate_runtime_is_blocked(self):
        processes = [
            {'pid': 1, 'cdp_address': '127.0.0.1', 'cdp_port': 9230},
            {'pid': 2, 'cdp_address': '127.0.0.1', 'cdp_port': 9231},
        ]
        runtime = BrowserRuntimeManager(
            self.root, self.root / 'runtime.db', ownership=self.ownership,
            process_finder=lambda profile: processes,
            launcher=MagicMock(),
            executable=lambda: Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            endpoint_probe=lambda address, port: {
                'address': address, 'port': port, 'endpoint': f'http://{address}:{port}'
            },
        )
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_PROFILE_RUNTIME'):
            runtime.get_context(self.playwright, self.profile)


if __name__ == '__main__':
    unittest.main()
