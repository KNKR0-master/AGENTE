"""Exercise the official dashboard HTTP dispatcher with an isolated database."""

import json
import tempfile
import threading
import unittest
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import dashboard_server
from src.publisher.http import PublisherAPI
from src.publisher.service import init_publisher
from src.store import connect, init_db


class ProductWorkspaceRouteTests(unittest.TestCase):
    def test_official_dispatcher_preserves_product_workspace_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            db = root / "fixture.db"
            with closing(connect(db)) as conn:
                init_db(conn)
                init_publisher(conn)
            api = PublisherAPI(root, db)
            with patch.object(dashboard_server, "PUBLISHER", api), patch.object(dashboard_server, "DB_PATH", db):
                server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard_server.Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for scope in ("current", "archived"):
                        url = f"http://127.0.0.1:{server.server_port}/api/publisher/products/workspace?scope={scope}"
                        with urllib.request.urlopen(url, timeout=5) as response:
                            self.assertEqual(response.status, 200)
                            self.assertIn("application/json", response.headers["Content-Type"])
                            data = json.load(response)
                        self.assertEqual(data["scope"], scope)
                        self.assertEqual(data["products"], [])
                        self.assertEqual(set(data["counts"]), {"current", "no_current", "archived"})
                finally:
                    server.shutdown()
                    server.server_close()
