"""Exercise Drive auth routing through the official dashboard dispatcher."""

import json
import sqlite3
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import dashboard_server
from src.google_drive_auth import CECILIA, DRIVE_SCOPE, DriveAuthError
from src.publisher.drive_reauth import DriveReauthManager
from src.publisher.http import PublisherAPI
from src.publisher.service import init_publisher
from src.store import connect, init_db


class DriveAuthRouteTests(unittest.TestCase):
    def test_official_dispatcher_keeps_status_read_only_and_start_post_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'config').mkdir()
            (root / 'data/credentials').mkdir(parents=True)
            (root / 'config/cecilia_drive_auth.json').write_text(json.dumps({
                'auth_mode': 'user_oauth', 'expected_google_account': CECILIA,
                'scopes': [DRIVE_SCOPE], 'oauth_client_file': 'data/credentials/client.json',
                'credential_store': 'data/credentials/token.dpapi'}), encoding='utf-8')
            (root / 'data/credentials/client.json').write_text(json.dumps({'installed': {
                'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
                'token_uri': 'https://oauth2.googleapis.com/token'}}), encoding='utf-8')
            db = root / 'fixture.db'
            with closing(connect(db)) as conn:
                init_db(conn)
                init_publisher(conn)
            entered, release = threading.Event(), threading.Event()
            calls = []

            def fake_runner(_flow, **_kwargs):
                calls.append('one_flow')
                entered.set()
                release.wait(5)
                raise DriveAuthError('OAUTH_CALLBACK_FAILED', code='OAUTH_CALLBACK_FAILED',
                                     stage='LOCAL_CALLBACK')

            api = object.__new__(PublisherAPI)
            api.root, api.db = root, db
            api.drive_reauth = DriveReauthManager(root, None, flow_factory=lambda *_: object(),
                                                  runner=fake_runner)
            paused = {'enabled': True, 'status': 'PAUSED', 'paused_reason': 'AUTH_REQUIRED'}

            def memory_connect(*_args, **_kwargs):
                conn = sqlite3.connect(':memory:')
                conn.row_factory = sqlite3.Row
                return conn

            with patch.object(dashboard_server, 'PUBLISHER', api), \
                 patch.object(dashboard_server, 'DB_PATH', db), \
                 patch.object(dashboard_server, 'connect', side_effect=memory_connect), \
                 patch('src.publisher.http.polling_status', return_value=paused):
                server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_server.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                base = f'http://127.0.0.1:{server.server_port}'

                def request(path, *, method='GET'):
                    body = b'{}' if method == 'POST' else None
                    req = urllib.request.Request(base + path, data=body,
                                                 headers={'Content-Type': 'application/json'},
                                                 method=method)
                    try:
                        with urllib.request.urlopen(req, timeout=2) as response:
                            return response.status, response.read().decode('utf-8')
                    except urllib.error.HTTPError as exc:
                        return exc.code, exc.read().decode('utf-8')

                try:
                    status, body = request('/api/publisher/drive-auth/status')
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)['state'], 'AUTH_REQUIRED')
                    self.assertEqual(calls, [])
                    self.assertEqual(request('/api/publisher/drive-auth/reauthorize')[0], 404)
                    self.assertEqual(request('/api/publisher/drive-auth/status', method='POST')[0], 400)
                    self.assertEqual(calls, [])
                    start = time.monotonic()
                    status, body = request('/api/publisher/drive-auth/reauthorize', method='POST')
                    self.assertEqual(status, 200)
                    self.assertLess(time.monotonic() - start, 2)
                    self.assertTrue(json.loads(body)['started'])
                    self.assertTrue(entered.wait(2))
                    status, body = request('/api/publisher/drive-auth/reauthorize', method='POST')
                    self.assertEqual(status, 200)
                    self.assertFalse(json.loads(body)['started'])
                    self.assertEqual(calls, ['one_flow'])
                    status, body = request('/api/publisher/drive-auth/status')
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(body)['state'], 'REAUTHORIZING')
                    for secret in ('access_token', 'refresh_token', 'authorization_url',
                                   'client_secret', 'TEST_AUTH_CODE_SECRET', 'dpapi:'):
                        self.assertNotIn(secret, body.lower())
                    self.assertEqual(request('/api/publisher/unknown-route')[0], 404)
                    self.assertEqual(request('/publisher.html')[0], 200)
                finally:
                    release.set()
                    if api.drive_reauth._thread:
                        api.drive_reauth._thread.join(3)
                    server.shutdown()
                    server.server_close()
