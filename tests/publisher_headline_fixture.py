"""HTTP resource ownership for isolated headline fixtures."""
import json
import re
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit


class HeadlineFixtureServer(ThreadingHTTPServer):
    # Swatch requests retain SQLite connections until their local render ends.
    daemon_threads = False
    block_on_close = True

    def __init__(self, *args, **kwargs):
        self.client_closing = threading.Event()
        self.diagnostics_lock = threading.Lock()
        self.expected_client_disconnects = []
        self.request_errors = []
        super().__init__(*args, **kwargs)

    def record_expected_disconnect(self, handler, error, status):
        path = urlsplit(handler.path).path
        read_only = (
            handler.command == 'GET'
            and re.fullmatch(r'/api/publisher/headline-presets/\d+/swatch', path)
        ) or (
            handler.command == 'POST'
            and path == '/api/publisher/library-batches/preflight'
        )
        if not (self.client_closing.is_set() and read_only
                and isinstance(error, ConnectionAbortedError)
                and getattr(error, 'winerror', None) == 10053):
            return False
        event = dict(event='EXPECTED_CLIENT_DISCONNECT', method=handler.command,
                     path=path, status=status, winerror=10053,
                     phase='EXPLICIT_BROWSER_CLOSE', response_delivered=False)
        with self.diagnostics_lock:
            self.expected_client_disconnects.append(event)
            print(json.dumps(event), file=sys.stderr)
        return True

    def handle_error(self, request, client_address):
        error = sys.exc_info()[1]
        with self.diagnostics_lock:
            self.request_errors.append(dict(event='UNEXPECTED_FIXTURE_SERVER_ERROR',
                                            error_type=type(error).__name__,
                                            winerror=getattr(error, 'winerror', None)))
        super().handle_error(request, client_address)

    def assert_no_request_errors(self):
        with self.diagnostics_lock:
            errors = list(self.request_errors)
        if errors:
            raise AssertionError(f'Unexpected fixture server errors: {errors}')


def close_fixture_browser(browser, server):
    # Only this owned-client close authorizes classification of abandoned reads.
    server.client_closing.set()
    browser.close()


def shutdown_fixture_server(server, thread):
    gate = getattr(server, 'catalog_gate', None)
    if gate is not None:
        gate.set()
    try:
        server.shutdown()
    finally:
        try:
            server.server_close()
        finally:
            thread.join()
    server.assert_no_request_errors()
