"""Real dashboard dispatch, temporary SQLite and Fake Drive only.

Compile the unchanged Handler methods from their source AST to avoid importing
operational server globals or initializing its Publisher worker/database.
"""
import ast
import json
import re
import sqlite3
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from urllib.error import HTTPError

from tests import test_publisher_batch_consumption as consumption
from tests import test_publisher_p0_integration as integration
from src.publisher.http import PublisherAPI
from src.publisher.service import get_row

ROOT = Path(__file__).resolve().parents[1]


def handler_method(name, environment):
    source = ROOT / 'dashboard_server.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    handler = next(node for node in tree.body
                   if isinstance(node, ast.ClassDef) and node.name == 'Handler')
    method = next(node for node in handler.body
                  if isinstance(node, ast.FunctionDef) and node.name == name)
    namespace = dict(environment)
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


class DispatcherConsumptionTests(unittest.TestCase):
    def fixture(self, historical=False):
        case = (integration.PublisherP0IntegrationTests(
            'test_D_archived_unreconciled_history_blocks_published_successor_consumption')
            if historical else consumption.BatchConsumptionTests(
                'test_four_published_one_abandoned_preserved'))
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def consumption_state(self, case):
        tables = [row[0] for row in case.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND "
            "(name LIKE 'publisher_%' OR name='account_mutation_ledger') ORDER BY name")]
        return {table: [tuple(row) for row in case.conn.execute(
            'SELECT * FROM "' + table + '" ORDER BY rowid')] for table in tables}

    @contextmanager
    def server(self, case, drive):
        api = PublisherAPI.__new__(PublisherAPI)
        api.root = case.root
        api.db = case.db if hasattr(case, 'db') else case.root / 'test.db'
        api.worker = SimpleNamespace(provider_factory=Mock(return_value=drive))
        api.get = Mock(wraps=api.get)
        unexpected_fallback = Mock(side_effect=AssertionError('NON_PUBLISHER_DISPATCH'))
        environment = dict(re=re, PUBLISHER=api, sqlite3=sqlite3, urlparse=urlparse,
                           threading=threading, HTTP_SERVER=None,
                           connect=unexpected_fallback, DB_PATH=api.db,
                           init_db=unexpected_fallback)
        get_method = handler_method('_api_get', environment)
        post_method = handler_method('do_POST', environment)

        class Handler(BaseHTTPRequestHandler):
            _api_get = get_method
            do_POST = post_method

            def do_GET(self):
                parsed = urlparse(self.path)
                try:
                    self._api_get(parsed.path, parse_qs(parsed.query))
                except (ValueError, KeyError) as error:
                    self._integration_error(error)

            def _json(self, value, status=200, **kwargs):
                data = json.dumps(value).encode('utf-8')
                self.send_response(status)
                self.send_header('Content-Type', 'application/json; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _integration_error(self, error):
                self._json({'error': str(error)}, status=400)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}', api=api)
            unexpected_fallback.assert_not_called()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)

    def request(self, server, path, body=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if origin:
            headers['Origin'] = origin
        request = Request(server.url + path,
                          data=None if body is None else json.dumps(body).encode(),
                          headers=headers)
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as error:
            response = error
        with response:
            self.assertIn('application/json', response.headers['Content-Type'])
            return response.status, json.load(response)

    def test_eligible_evaluation_has_frontend_identity_payload_and_is_read_only(self):
        case = self.fixture()
        ids = case.prepare(1)
        before = self.consumption_state(case)
        with self.server(case, case.drive) as server:
            for _ in range(2):
                status, body = self.request(server,
                    f'/api/publisher/batches/{case.batch_id}/consumption-evaluation?probe=synthetic')
                self.assertEqual(status, 200)
                self.assertTrue(body['remote_read_only'])
                self.assertEqual(body['batch_id'], case.batch_id)
                self.assertEqual(body['counts']['eligible'], 1)
                item = body['items'][0]
                self.assertEqual(item['draft_id'], ids[0])
                self.assertEqual(item['category'], 'eligible')
                self.assertEqual(item['identity']['account']['id'], case.account)
                self.assertEqual(item['identity']['draft']['batch_id'], case.batch_id)
                self.assertEqual(item['identity']['media']['remote_file_id'], 'remote-0')
                self.assertEqual(item['identity']['media']['source_account'], 'cecilia')
                self.assertTrue(re.fullmatch('[a-f0-9]{64}', item['identity_token']))
                self.assertEqual(server.api.get.call_args.args[1], {'probe': ['synthetic']})
                self.assertEqual(self.consumption_state(case), before)
                self.assertFalse(case.drive.deleted)

    def test_historical_block_is_forwarded_without_provider_or_state_mutation(self):
        case = self.fixture(historical=True)
        old, batch, drive = case.unsafe_successor()
        before = self.consumption_state(case)
        with self.server(case, drive) as server:
            status, body = self.request(server,
                f'/api/publisher/batches/{batch["batch_id"]}/consumption-evaluation')
            self.assertEqual(status, 200)
            self.assertTrue(body['remote_read_only'])
            self.assertEqual(body['counts']['blocked'], 1)
            item = body['items'][0]
            self.assertEqual(item['blocker'], 'MEDIA_HISTORY_RECONCILIATION_REQUIRED')
            self.assertFalse(item['can_evaluate'])
            self.assertEqual(item['identity']['account']['id'], case.account)
            server.api.worker.provider_factory.assert_not_called()
        self.assertFalse(drive.deleted)
        self.assertEqual(self.consumption_state(case), before)
        self.assertNotEqual(get_row(case.conn, 'publisher_media_assets', 1)['status'], 'CONSUMED')

    def test_mixed_batch_keeps_eligible_blocked_and_abandoned_identities(self):
        case = self.fixture()
        ids = case.prepare(5, published=3)
        with case.conn:
            case.conn.execute("UPDATE publisher_drafts SET status='ARCHIVED' WHERE id=?", (ids[4],))
        before = self.consumption_state(case)
        with self.server(case, case.drive) as server:
            status, body = self.request(server,
                f'/api/publisher/batches/{case.batch_id}/consumption-evaluation')
            self.assertEqual(status, 200)
            self.assertEqual((body['counts']['eligible'], body['counts']['blocked'],
                              body['counts']['abandoned']), (3, 1, 1))
            self.assertEqual([item['draft_id'] for item in body['items']], ids)
            self.assertEqual(body['items'][3]['blocker'], 'PUBLICATION_NOT_REGISTERED')
            self.assertEqual(body['items'][4]['blocker'], 'ABANDONED_MEDIA_PROTECTED')
            self.assertEqual(server.api.worker.provider_factory.call_count, 3)
        self.assertFalse(case.drive.deleted)
        self.assertEqual(self.consumption_state(case), before)

    def test_preflight_and_review_still_dispatch_to_existing_api(self):
        case = self.fixture()
        case.prepare(1)
        before = self.consumption_state(case)
        with self.server(case, case.drive) as server:
            for suffix in ('preflight', 'review'):
                status, body = self.request(server, f'/api/publisher/batches/{case.batch_id}/{suffix}')
                self.assertEqual(status, 200)
                self.assertEqual(body['batch_id'], case.batch_id)
            self.assertEqual(server.api.get.call_count, 2)
        self.assertFalse(case.drive.deleted)
        self.assertEqual(self.consumption_state(case), before)

    def test_post_routes_preserve_separate_authorization_and_origin_checks(self):
        case = self.fixture()
        case.drive = consumption.FakeIdentityDrive()
        ids = case.batch(1)
        case.confirm(ids[0])
        batch_id = get_row(case.conn, 'publisher_drafts', ids[0])['batch_id']
        with self.server(case, case.drive) as server:
            status, body = self.request(server,
                f'/api/publisher/batches/{batch_id}/register-published',
                {'draft_ids': ids, 'human_confirmed': True})
            self.assertEqual(status, 200)
            self.assertFalse(case.drive.deleted)
            status, review = self.request(server,
                f'/api/publisher/batches/{batch_id}/consumption-evaluation')
            self.assertEqual(status, 200)
            payload = dict(request_id='dispatcher-consumption-0001',
                items=[dict(draft_id=ids[0],identity_token=review['items'][0]['identity_token'])],
                confirmed_count=1,drive_delete_confirmed=False)
            status, body = self.request(server,
                f'/api/publisher/batches/{batch_id}/finalize-consumption', payload)
            self.assertEqual(status, 400)
            self.assertEqual(body['error'], 'SEPARATE_DRIVE_DELETION_AUTHORIZATION_REQUIRED')
            self.assertFalse(case.drive.deleted)
            payload['drive_delete_confirmed'] = True
            status, body = self.request(server,
                f'/api/publisher/batches/{batch_id}/finalize-consumption', payload,
                origin='http://untrusted.invalid')
            self.assertEqual(status, 400)
            self.assertFalse(case.drive.deleted)
            status, body = self.request(server,
                f'/api/publisher/batches/{batch_id}/finalize-consumption', payload,
                origin=server.url)
            self.assertEqual(status, 200)
            self.assertEqual(body['status'], 'COMPLETE')
            self.assertEqual(body['counts']['CONSUMED'], 1)
        self.assertEqual(case.drive.deleted, ['remote-0'])
