import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from unittest.mock import patch

from src.publisher.diagnostics import safe_message, console_metadata, AuthDiagnostics
from src.publisher.manual_profile import ProfileOwnership
from src.publisher.qr_response_state import project_state
from tests.test_publisher_diagnostics import Page
from src.publisher.worker import PublisherWorker


class SanitizationTests(unittest.TestCase):
    def test_granular_redaction_preserves_useful_message(self):
        text = 'Polling failed status=200 token=abc123; parse error at handler https://www.tiktok.com/login?ticket=SECRET'
        result = safe_message(text)
        self.assertIn('Polling failed status=200', result)
        self.assertIn('parse error at handler', result)
        self.assertNotIn('abc123', result)
        self.assertNotIn('SECRET', result)

    def test_credentials_jwt_keys_and_blobs(self):
        for text, secret in [
            ('password="hello world" failed', 'hello world'),
            ('Authorization: Bearer abcdefghi', 'abcdefghi'),
            ('Cookie: sessionid=abc; other=def', 'abc'),
            ('JWT eyJabcde.abcdefgh.abcdefgh', 'eyJabcde'),
            ('key sk_abcdefghijklmnop', 'sk_abcdefghijklmnop'),
            ('payload ' + 'X'*5000, 'X'*100),
            ('{"ticket":"shortsecret","message":"failed"}', 'shortsecret')]:
            self.assertNotIn(secret, safe_message(text))

    def test_console_location_and_message(self):
        diag = AuthDiagnostics()
        page = Page()
        diag.attach_page(1, page)
        page.emit('console', SimpleNamespace(type='error', text='TypeError: cannot read property token=SECRET',
            location={'url': 'https://www.tiktok.com/login?token=SECRET', 'lineNumber': 12, 'columnNumber': 4}))
        event = diag.recent(1)[0][-1]
        self.assertEqual(event['line'], 12)
        self.assertEqual(event['column'], 4)
        self.assertIn('TypeError', event['message'])
        self.assertNotIn('SECRET', json.dumps(event))
        self.assertIn('timestamp', event)

    def test_no_unverified_polling_schema(self):
        self.assertEqual(project_state('https://www.tiktok.com/passport/web/get_qrcode',
            {'status': 'confirmed', 'token': 'SECRET'})['reason'], 'NO_VERIFIED_SCHEMA')

    def test_polling_listener_does_not_read_body(self):
        diag = AuthDiagnostics()
        page = Page()
        diag.attach_page(1, page)
        request = SimpleNamespace(url='https://web-sg.tiktok.com/passport/web/check_qrconnect?token=SECRET',
            method='GET', resource_type='xhr', redirected_from=None, redirected_to=None)
        response = SimpleNamespace(url=request.url, request=request, status=200,
            json=MagicMock(side_effect=AssertionError('BODY FORBIDDEN')),
            body=MagicMock(side_effect=AssertionError('BODY FORBIDDEN')))
        page.emit('response', response)
        event = diag.recent(1)[0][-1]
        self.assertEqual(event['reason'], 'NO_VERIFIED_SCHEMA')
        self.assertFalse(event['body_read'])
        response.json.assert_not_called()
        response.body.assert_not_called()

    def test_synthetic_allowlist_never_emits_unknown_values(self):
        # Synthetic fixture, NOT a claim about TikTok's actual schema.
        endpoint = ('www.tiktok.com', '/passport/web/get_qrcode')
        reviewed = {endpoint: {('fixture_state',): {7: 'FIXTURE_WAITING'}}}
        result = project_state('https://www.tiktok.com/passport/web/get_qrcode',
            {'fixture_state': 7, 'ticket': 'SECRET'}, verified_fields=reviewed)
        self.assertEqual(result['fields'], {'fixture_state': 'FIXTURE_WAITING'})
        result = project_state('https://www.tiktok.com/passport/web/get_qrcode',
            {'fixture_state': 'SECRET'}, verified_fields=reviewed)
        self.assertEqual(result['fields'], {})


class ManualProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'same-existing-profile'
        self.profile.mkdir()
        self.inventory = MagicMock(return_value=[])
        self.launch = MagicMock(return_value=SimpleNamespace(pid=123))
        self.owner = ProfileOwnership(self.root/'offline.db', inventory=self.inventory,
            launcher=self.launch, executable=lambda: Path('C:/Program Files/Google/Chrome/Application/chrome.exe'))

    def test_dead_pid_identity_is_treated_as_absent(self):
        failed = SimpleNamespace(returncode=1, stdout='', stderr='')
        with patch('src.publisher.manual_profile.subprocess.run', return_value=failed):
            from src.publisher.manual_profile import process_identity
            self.assertIsNone(process_identity(19076))

    def test_exact_command_no_playwright_flags(self):
        result = self.owner.open_manual(self.profile)
        args = self.launch.call_args.args[0]
        self.assertEqual(args[1:], ['--user-data-dir='+str(self.profile.resolve()), 'about:blank'])
        self.assertEqual(len(args), 3)
        self.assertFalse(self.launch.call_args.kwargs['shell'])
        self.assertEqual(result['state'], 'MANUAL_BROWSER_ACTIVE')
        for flag in ['--remote-debugging-pipe', '--no-sandbox', '--disable', '--enable-automation']:
            self.assertNotIn(flag, ' '.join(args))

    def test_manual_blocks_playwright_and_duplicate_manual(self):
        self.owner.open_manual(self.profile)
        with self.assertRaises(ValueError): self.owner.acquire(self.profile, 'PLAYWRIGHT')
        with self.assertRaises(ValueError): self.owner.open_manual(self.profile)
        self.launch.assert_called_once()

    def test_playwright_blocks_manual(self):
        token = self.owner.acquire(self.profile, 'PLAYWRIGHT')
        self.owner.activate(self.profile, token, 'PLAYWRIGHT_ACTIVE')
        with self.assertRaises(ValueError): self.owner.open_manual(self.profile)
        self.launch.assert_not_called()

    def test_external_chrome_blocks_both(self):
        self.inventory.return_value = [18880]
        for mode in ['MANUAL', 'PLAYWRIGHT']:
            with self.assertRaises(ValueError): self.owner.acquire(self.profile, mode)
        self.launch.assert_not_called()

    def test_close_requires_verification_no_auth_write(self):
        self.owner.open_manual(self.profile)
        self.inventory.return_value = [123]
        self.assertEqual(self.owner.check_manual_closed(self.profile), 'MANUAL_BROWSER_ACTIVE')
        self.inventory.return_value = []
        self.assertEqual(self.owner.check_manual_closed(self.profile), 'SESSION_REQUIRES_VERIFICATION')
        with self.owner.connection() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM sqlite_master WHERE name='publisher_accounts'").fetchone()[0], 0)

    def test_mutex_across_instances_concurrent(self):
        def acquire(_):
            manager = ProfileOwnership(self.root/'offline.db', inventory=lambda p: [])
            try: manager.acquire(self.profile, 'MANUAL'); return True
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(acquire, range(8))), 1)

    def test_inventory_failure_fails_closed(self):
        self.inventory.side_effect = RuntimeError('Access denied')
        with self.assertRaises(RuntimeError): self.owner.open_manual(self.profile)
        self.launch.assert_not_called()

    def test_missing_profile_not_created(self):
        with self.assertRaises(ValueError): self.owner.open_manual(self.root/'absent')
        self.assertFalse((self.root/'absent').exists())

    def test_qr_authorization_disabled_before_any_run_is_created(self):
        (self.root/'config').mkdir()
        (self.root/'config/publisher_studio.json').write_text('{"experimental_qr_enabled": false}')
        worker = PublisherWorker(self.root, self.root/'offline.db')
        worker.start = MagicMock()
        with self.assertRaisesRegex(ValueError, 'EXPERIMENTAL_QR_DISABLED'):
            worker.authorize_controlled_login(1, explicitly_authorized=True)
        with worker.auth_test_lock.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM publisher_auth_test_runs').fetchone()[0], 0)
        worker.start.assert_not_called()

    def test_worker_context_rejects_manual_owner_before_playwright_launch(self):
        (self.root/'config').mkdir()
        (self.root/'config/publisher_studio.json').write_text('{"tiktok_actions_paused": false}')
        profile = self.root/'data/publisher/browser_profiles/fixture'
        profile.mkdir(parents=True)
        worker = PublisherWorker(self.root, self.root/'offline.db')
        worker.profile_ownership.inventory = lambda p: []
        token = worker.profile_ownership.acquire(profile, 'MANUAL')
        worker.profile_ownership.activate(profile, token, 'MANUAL_BROWSER_ACTIVE')
        playwright = MagicMock()
        with self.assertRaisesRegex(ValueError, 'BROWSER_PROFILE_BUSY'):
            worker.context(playwright, {'id': 1, 'browser_profile': str(profile.relative_to(self.root))})
        playwright.chromium.launch_persistent_context.assert_not_called()


if __name__ == '__main__': unittest.main()
