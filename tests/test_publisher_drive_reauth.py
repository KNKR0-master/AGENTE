import json
import tempfile
import threading
import unittest
from contextlib import closing
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.google_drive_auth import CECILIA, DRIVE_SCOPE, DriveAuthError, save_authorized_user, user_drive_service
from src.publisher.drive_reauth import DriveReauthManager


PAUSED = {'enabled': True, 'status': 'PAUSED', 'paused_reason': 'AUTH_REQUIRED'}


class MemoryStore:
    def __init__(self):
        self.value = {'refresh_token': 'OLD_TOKEN_SECRET', 'scopes': [DRIVE_SCOPE]}
        self.fail = False

    def save(self, value):
        if self.fail:
            raise OSError('DPAPI_SECRET')
        self.value = value

    def load(self):
        return self.value


class FakePoller:
    def __init__(self):
        self.calls = 0

    def resume_after_reauth(self):
        self.calls += 1
        return True


class DriveReauthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'config').mkdir()
        (self.root / 'data/credentials').mkdir(parents=True)
        (self.root / 'config/cecilia_drive_auth.json').write_text(json.dumps({
            'auth_mode': 'user_oauth', 'expected_google_account': CECILIA,
            'scopes': [DRIVE_SCOPE], 'oauth_client_file': 'data/credentials/client.json',
            'credential_store': 'data/credentials/token.dpapi'}), encoding='utf-8')
        (self.root / 'data/credentials/client.json').write_text(json.dumps({'installed': {
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token'}}), encoding='utf-8')
        self.store = MemoryStore()
        self.poller = FakePoller()
        self.credentials = MagicMock()
        self.credentials.granted_scopes = [DRIVE_SCOPE]
        self.credentials.has_scopes.return_value = True
        self.credentials.refresh_token = 'TEST_REFRESH_TOKEN_SECRET'
        self.credentials.token = 'TEST_ACCESS_TOKEN_SECRET'
        self.credentials.to_json.return_value = json.dumps({
            'token': 'TEST_ACCESS_TOKEN_SECRET', 'refresh_token': 'TEST_REFRESH_TOKEN_SECRET',
            'scopes': [DRIVE_SCOPE]})
        self.service = MagicMock()
        self.service.about.return_value.get.return_value.execute.return_value = {
            'user': {'emailAddress': CECILIA, 'me': True}}
        self.runner_calls = 0
        self.runner_error = None
        self.barrier = None

    def runner(self, flow, **kwargs):
        self.runner_calls += 1
        if self.barrier:
            self.barrier.wait(2)
        if self.runner_error:
            raise self.runner_error
        for stage in ('OAUTH_FLOW_CREATED', 'BROWSER_OPEN_RESULT', 'CALLBACK_RECEIVED',
                      'TOKEN_EXCHANGE_COMPLETED'):
            kwargs['event_sink']({'stage': stage})
        return self.credentials

    def saver(self, credentials, path, expected, **kwargs):
        return save_authorized_user(credentials, path, expected, builder=lambda *_a, **_k: self.service,
                                    store=self.store, **kwargs)

    def manager(self):
        return DriveReauthManager(self.root, self.poller, flow_factory=lambda *_: object(),
                                  runner=self.runner, saver=self.saver,
                                  store_factory=lambda _: self.store, timeout_seconds=1)

    def hydrate(self, manager):
        self.credentials.valid = True
        self.credentials.refresh_token = self.store.value.get('refresh_token')
        return manager.hydrate(service_loader=lambda path, expected, **kw: user_drive_service(
            path, expected, credential_factory=lambda info: self.credentials,
            builder=lambda *_a, **_k: self.service, **kw))

    def test_hydration_valid_credential_ready_without_oauth_or_writes(self):
        manager = self.manager()
        with patch.object(self.store, 'save', wraps=self.store.save) as save:
            result = self.hydrate(manager)
        self.assertEqual(result['state'], 'READY')
        save.assert_not_called()
        self.assertEqual(self.runner_calls, 0)
        self.assertIsNone(manager._thread)
        self.assertEqual(self.poller.calls, 0)
        self.assertNotIn('SECRET', json.dumps(result))

    def test_hydration_missing_refresh_token_requires_auth(self):
        self.store.value = {'scopes': [DRIVE_SCOPE]}
        self.assertEqual(self.hydrate(self.manager())['state'], 'AUTH_REQUIRED')
        self.assertEqual(self.runner_calls, 0)

    def test_hydration_corrupt_or_missing_store_requires_auth(self):
        for error in (FileNotFoundError('SECRET'), ValueError('CORRUPT_SECRET')):
            with self.subTest(error=type(error).__name__), patch.object(self.store, 'load', side_effect=error):
                result = self.hydrate(self.manager())
                self.assertEqual(result['state'], 'AUTH_REQUIRED')
                self.assertNotIn('SECRET', json.dumps(result))

    def test_hydration_wrong_account_not_ready(self):
        self.service.about.return_value.get.return_value.execute.return_value = {
            'user': {'emailAddress': 'wrong@example.com', 'me': True}}
        self.assertEqual(self.hydrate(self.manager())['state'], 'AUTH_REQUIRED')

    def test_hydration_unusable_credential_requires_auth(self):
        self.service.about.return_value.get.return_value.execute.side_effect = RuntimeError('TOKEN_SECRET')
        self.assertEqual(self.hydrate(self.manager())['state'], 'AUTH_REQUIRED')

    def test_hydration_does_not_replace_active_reauth_job(self):
        manager = self.manager()
        self.barrier = threading.Event()
        manager.start(polling=PAUSED)
        loader = MagicMock()
        self.assertEqual(manager.hydrate(service_loader=loader)['state'], 'REAUTHORIZING')
        loader.assert_not_called()
        self.barrier.set()
        self.finish(manager)

    def test_official_dashboard_route_returns_hydrated_ready(self):
        import urllib.request
        from http.server import ThreadingHTTPServer
        import dashboard_server
        from src.publisher.http import PublisherAPI
        from src.publisher.service import init_publisher
        from src.store import connect, init_db
        db = self.root / 'hydration.db'
        with closing(connect(db)) as conn:
            init_db(conn)
            init_publisher(conn)
        api = PublisherAPI(self.root, db)
        api.drive_reauth = self.manager()
        self.hydrate(api.drive_reauth)
        with patch.object(dashboard_server, 'PUBLISHER', api), patch.object(dashboard_server, 'DB_PATH', db):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_server.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{server.server_port}/api/publisher/drive-auth/status', timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    result = json.load(response)
                    self.assertEqual(result['state'], 'READY')
                    self.assertNotIn('SECRET', json.dumps(result))
            finally:
                server.shutdown()
                server.server_close()

    def finish(self, manager):
        manager._thread.join(3)
        self.assertFalse(manager._thread.is_alive(), 'OAuth job did not terminate')
        result = manager.status()
        self.assertNotIn('SECRET', json.dumps(result))
        return result

    def test_explicit_action_single_flight_and_success(self):
        manager = self.manager()
        with self.assertRaises(ValueError):
            manager.start(polling={'enabled': True, 'status': 'SCHEDULED'})
        self.assertEqual(self.runner_calls, 0)
        self.barrier = threading.Event()
        self.assertTrue(manager.start(polling=PAUSED)['started'])
        self.assertFalse(manager.start(polling=PAUSED)['started'])
        self.assertEqual(manager.status()['state'], 'REAUTHORIZING')
        self.assertEqual(self.store.value['refresh_token'], 'OLD_TOKEN_SECRET')
        self.barrier.set()
        self.assertEqual(self.finish(manager)['state'], 'READY')
        self.assertEqual(self.runner_calls, 1)
        self.assertEqual(self.poller.calls, 1)
        self.assertFalse(manager.start(polling=PAUSED)['started'])

    def test_failures_never_replace_existing_token_or_retry(self):
        for code in ('OAUTH_CALLBACK_FAILED', 'TOKEN_EXCHANGE_FAILED', 'BROWSER_OPEN_FAILED'):
            with self.subTest(code=code):
                self.runner_error = DriveAuthError(code, code=code, stage='OAUTH_FLOW')
                manager = self.manager()
                manager.start(polling=PAUSED)
                result = self.finish(manager)
                self.assertEqual((result['state'], result['error_code']), ('AUTH_REQUIRED', code))
                self.assertEqual(self.store.value['refresh_token'], 'OLD_TOKEN_SECRET')
        self.assertEqual(self.poller.calls, 0)

    def test_refresh_missing_account_failure_mismatch_and_dpapi_failure(self):
        cases = (
            ('missing', 'REFRESH_TOKEN_MISSING'),
            ('account_failure', 'ACCOUNT_VALIDATION_FAILED'),
            ('mismatch', 'ACCOUNT_MISMATCH'),
            ('dpapi', 'DPAPI_SAVE_FAILED'),
        )
        for mode, code in cases:
            with self.subTest(mode=mode):
                self.runner_error = None
                self.credentials.refresh_token = None if mode == 'missing' else 'TEST_REFRESH_TOKEN_SECRET'
                self.service.about.return_value.get.return_value.execute.side_effect = (
                    RuntimeError('TEST_AUTH_CODE_SECRET') if mode == 'account_failure' else None)
                self.service.about.return_value.get.return_value.execute.return_value = {
                    'user': {'emailAddress': 'wrong@example.com' if mode == 'mismatch' else CECILIA,
                             'me': True}}
                self.store.fail = mode == 'dpapi'
                manager = self.manager()
                manager.start(polling=PAUSED)
                result = self.finish(manager)
                self.assertEqual((result['state'], result['error_code']), ('AUTH_REQUIRED', code))
                self.assertEqual(self.store.value['refresh_token'], 'OLD_TOKEN_SECRET')
                self.assertEqual(self.poller.calls, 0)

    def test_ui_has_user_action_and_no_automatic_start(self):
        script = (Path(__file__).resolve().parents[1] / 'dashboard/publisher.js').read_text(encoding='utf-8')
        self.assertIn("$('#reconnectDrive').onclick", script)
        self.assertIn("api('/drive-auth/reauthorize'", script)
        self.assertIn("driveAuthRecord.state==='REAUTHORIZING'", script)
        self.assertIn('A conexão com o Google Drive expirou.', script)

    def test_reload_failure_rolls_back_previous_credential_blob(self):
        path = self.root / 'data/credentials/token.dpapi'
        path.write_bytes(b'OLD_DPAPI_BLOB')

        def save_new(_credentials, store_path, _expected, **_kwargs):
            store_path.write_bytes(b'NEW_DPAPI_BLOB')

        manager = DriveReauthManager(self.root, self.poller, flow_factory=lambda *_: object(),
                                     runner=self.runner, saver=save_new,
                                     store_factory=lambda _: MagicMock(load=lambda: {}))
        manager.start(polling=PAUSED)
        result = self.finish(manager)
        self.assertEqual(result['error_code'], 'DRIVE_CREDENTIAL_RELOAD_FAILED')
        self.assertEqual(result['state'], 'AUTH_REQUIRED')
        self.assertEqual(path.read_bytes(), b'OLD_DPAPI_BLOB')
        self.assertEqual(self.poller.calls, 0)

    def test_polling_resume_failure_does_not_trigger_second_oauth(self):
        self.poller.resume_after_reauth = MagicMock(side_effect=OSError('private-path-secret'))
        manager = self.manager()
        manager.start(polling=PAUSED)
        result = self.finish(manager)
        self.assertEqual(result['state'], 'READY')
        self.assertEqual(result['error_code'], 'POLLING_RESUME_FAILED')
        self.assertFalse(manager.start(polling=PAUSED)['started'])
        self.assertEqual(self.runner_calls, 1)

    def test_http_status_is_read_only_and_post_starts_once(self):
        from src.publisher.http import PublisherAPI
        from src.publisher.service import init_publisher
        from src.store import connect, init_db
        db = self.root / 'test.db'
        with closing(connect(db)) as conn:
            init_db(conn)
            init_publisher(conn)
        api = object.__new__(PublisherAPI)
        api.root, api.db = self.root, db
        api.drive_reauth = MagicMock()
        api.drive_reauth.status.return_value = {'state': 'AUTH_REQUIRED', 'error_code': None}
        api.drive_reauth.start.return_value = {'started': True, 'state': 'REAUTHORIZING'}
        handler = SimpleNamespace(_json=MagicMock(), headers={'Content-Length': '2',
                                                               'Content-Type': 'application/json'},
                                  rfile=BytesIO(b'{}'))
        api.get(handler, {}, '/api/publisher/drive-auth/status')
        self.assertEqual(handler._json.call_args.args[0]['state'], 'AUTH_REQUIRED')
        api.drive_reauth.start.assert_not_called()
        with patch('src.publisher.http.polling_status', return_value=PAUSED):
            api.post(handler, '/api/publisher/drive-auth/reauthorize')
        self.assertEqual(handler._json.call_args.args[0]['state'], 'REAUTHORIZING')
        api.drive_reauth.start.assert_called_once_with(polling=PAUSED)
