import io
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing, redirect_stderr
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from unittest.mock import Mock

from tests.publisher_headline_fixture import (
    HeadlineFixtureServer, close_fixture_browser, shutdown_fixture_server,
)
from tests.test_publisher_headlines_browser import HeadlineFixtureHandler


class HoldingSQLiteHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_GET(self):
        try:
            with closing(sqlite3.connect(self.server.db)) as conn:
                self.server.connections.append(conn)
                with closing(conn.execute('SELECT count(*) FROM fixture')) as cursor:
                    cursor.fetchone()
                self.server.request_entered.set()
                if not self.server.release_request.wait(5):
                    raise TimeoutError('Fixture request was not released')
                if self.server.fail_request:
                    raise ValueError('Fixture handler failure')
            self.send_response(204)
            self.end_headers()
        finally:
            self.server.connection_closed.set()


class ObservedFixtureServer(HeadlineFixtureServer):
    def __init__(self, *args, **kwargs):
        self.close_entered = threading.Event()
        super().__init__(*args, **kwargs)

    def server_close(self):
        self.close_entered.set()
        super().server_close()

    def handle_error(self, request, client_address):
        self.handler_failed.set()
        super().handle_error(request, client_address)


class HeadlineFixtureCleanupTests(unittest.TestCase):
    def exercise_cleanup(self, *, fail_request=False, fail_test=False):
        directory = tempfile.TemporaryDirectory(prefix='publisher-http-cleanup-')
        root = Path(directory.name)
        db = root / 'test.db'
        with closing(sqlite3.connect(db)) as conn:
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('CREATE TABLE fixture(value TEXT)')
            conn.commit()
        server = ObservedFixtureServer(('127.0.0.1', 0), HoldingSQLiteHandler)
        server.db = db
        server.connections = []
        server.fail_request = fail_request
        for name in ('request_entered', 'release_request', 'connection_closed',
                     'handler_failed', 'catalog_gate'):
            setattr(server, name, threading.Event())
        serve_thread = threading.Thread(target=server.serve_forever)
        serve_thread.start()
        client_errors = []
        cleanup_errors = []
        cleanup_done = threading.Event()

        def request():
            client = HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            try:
                client.request('GET', '/')
                response = client.getresponse()
                try:
                    response.read()
                finally:
                    response.close()
            except Exception as exc:
                client_errors.append(exc)
            finally:
                client.close()

        def cleanup():
            try:
                shutdown_fixture_server(server, serve_thread)
            except Exception as exc:
                cleanup_errors.append(exc)
            finally:
                cleanup_done.set()

        client_thread = threading.Thread(target=request)
        close_thread = threading.Thread(target=cleanup)
        try:
            client_thread.start()
            self.assertTrue(server.request_entered.wait(5))
            self.assertFalse(server.daemon_threads)
            self.assertTrue(server.block_on_close)
            try:
                if fail_test:
                    raise ValueError('Fixture test failure')
            except ValueError:
                if not fail_test:
                    raise
            finally:
                close_thread.start()
            self.assertTrue(server.close_entered.wait(5))
            self.assertFalse(cleanup_done.is_set())
            self.assertFalse(server.connection_closed.is_set())
            self.assertTrue(server.catalog_gate.is_set())
        finally:
            server.release_request.set()
            if close_thread.ident is None:
                close_thread.start()
            close_thread.join(5)
            if client_thread.ident is not None:
                client_thread.join(5)
            if not close_thread.is_alive() and not client_thread.is_alive():
                directory.cleanup()
        self.assertFalse(close_thread.is_alive())
        self.assertFalse(client_thread.is_alive())
        self.assertFalse(serve_thread.is_alive())
        self.assertTrue(cleanup_done.is_set())
        if fail_request:
            self.assertEqual(len(cleanup_errors), 1, cleanup_errors)
            self.assertIsInstance(cleanup_errors[0], AssertionError)
            self.assertEqual(server.request_errors, [dict(
                event='UNEXPECTED_FIXTURE_SERVER_ERROR', error_type='ValueError', winerror=None)])
        else:
            self.assertFalse(cleanup_errors, cleanup_errors)
            self.assertFalse(server.request_errors)
        self.assertTrue(server.connection_closed.is_set())
        self.assertEqual(server.handler_failed.is_set(), fail_request)
        if not fail_request:
            self.assertFalse(client_errors, client_errors)
        for conn in server.connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                conn.execute('SELECT 1')
        self.assertFalse(root.exists())
        if fail_request:
            with self.assertRaisesRegex(AssertionError, 'Unexpected fixture server errors'):
                shutdown_fixture_server(server, serve_thread)
        else:
            shutdown_fixture_server(server, serve_thread)

    def test_shutdown_waits_for_sqlite_request_before_directory_cleanup(self):
        self.exercise_cleanup()

    def test_handler_failure_still_closes_sqlite_and_removes_directory(self):
        with redirect_stderr(io.StringIO()) as errors:
            self.exercise_cleanup(fail_request=True)
        self.assertIn('ValueError: Fixture handler failure', errors.getvalue())

    def test_test_failure_still_drains_owned_server_resources(self):
        self.exercise_cleanup(fail_test=True)


class HeadlineFixtureDisconnectTests(unittest.TestCase):
    def handler(self, *, winerror=10053, method='GET',
                path='/api/publisher/headline-presets/2/swatch?version=1'):
        server = HeadlineFixtureServer(('127.0.0.1', 0), HeadlineFixtureHandler)
        self.addCleanup(server.server_close)
        handler = HeadlineFixtureHandler.__new__(HeadlineFixtureHandler)
        handler.server = server
        handler.command = method
        handler.path = path
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        error = ConnectionAbortedError('Controlled response-write failure')
        error.winerror = winerror
        handler.wfile = Mock()
        handler.wfile.write.side_effect = error
        return handler

    def test_controlled_browser_close_records_abandoned_read_not_delivery(self):
        for method, path in (
            ('GET', '/api/publisher/headline-presets/2/swatch?version=1'),
            ('POST', '/api/publisher/library-batches/preflight'),
        ):
            with self.subTest(method=method):
                handler = self.handler(method=method, path=path)
                browser = Mock()
                results = []
                browser.close.side_effect = lambda: results.append(handler.reply({'ok': True}))
                with redirect_stderr(io.StringIO()) as diagnostics:
                    close_fixture_browser(browser, handler.server)
                browser.close.assert_called_once_with()
                self.assertEqual(results, [False])
                self.assertFalse(handler.response_delivered)
                self.assertTrue(handler.close_connection)
                self.assertEqual(len(handler.server.expected_client_disconnects), 1)
                event = handler.server.expected_client_disconnects[0]
                self.assertEqual(event['event'], 'EXPECTED_CLIENT_DISCONNECT')
                self.assertEqual(event['phase'], 'EXPLICIT_BROWSER_CLOSE')
                self.assertFalse(event['response_delivered'])
                self.assertIn('EXPECTED_CLIENT_DISCONNECT', diagnostics.getvalue())
                handler.server.assert_no_request_errors()

    def test_required_response_disconnect_before_close_is_not_success(self):
        handler = self.handler()
        with self.assertRaises(ConnectionAbortedError):
            handler.reply({'ok': True})
        self.assertFalse(handler.response_delivered)
        self.assertFalse(handler.server.expected_client_disconnects)

    def test_required_successful_response_still_reports_delivery(self):
        handler = self.handler()
        handler.wfile.write.side_effect = None
        self.assertTrue(handler.reply({'ok': True}))
        self.assertTrue(handler.response_delivered)
        handler.wfile.write.assert_called_once_with(b'{"ok": true}')
        self.assertFalse(handler.server.expected_client_disconnects)

    def test_other_transport_errors_are_not_classified_as_expected(self):
        handler = self.handler(winerror=10054)
        handler.server.client_closing.set()
        with self.assertRaises(ConnectionAbortedError):
            handler.reply({'ok': True})
        self.assertFalse(handler.response_delivered)
        self.assertFalse(handler.server.expected_client_disconnects)

    def test_mutating_route_disconnect_is_not_classified_as_expected(self):
        handler = self.handler(method='POST', path='/api/publisher/library-batches')
        handler.server.client_closing.set()
        with self.assertRaises(ConnectionAbortedError):
            handler.reply({'id': 1})
        self.assertFalse(handler.response_delivered)
        self.assertFalse(handler.server.expected_client_disconnects)
