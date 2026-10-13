"""Small URI and allowlist regressions; no operational database is opened."""

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tests.sqlite_isolation_harness import (
    SQLiteIsolationGuard, SQLiteIsolationViolation, canonical_sqlite_path,
)


class SQLiteIsolationHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='sqlite-uri-guard-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.qa = self.root / 'allowed'
        self.qa.mkdir()
        self.operational = self.root / 'data/agent.db'
        self.guard = SQLiteIsolationGuard([self.qa], protected_paths=[self.operational])

    def test_valid_temporary_uri_roundtrips_and_preserves_readonly_mode(self):
        db = self.qa / 'temporário com espaço %.db'
        with self.guard.installed():
            with closing(sqlite3.connect(db)) as conn:
                conn.execute('CREATE TABLE fixture(value TEXT)')
                conn.execute("INSERT INTO fixture VALUES ('temporary')")
                conn.commit()
            uri = db.as_uri() + '?mode=ro'
            self.assertEqual(self.guard.require_allowed(uri), db.resolve())
            with closing(sqlite3.connect(uri, uri=True)) as conn:
                self.assertEqual(Path(conn.execute('PRAGMA database_list').fetchone()[2]).resolve(), db.resolve())
                self.assertEqual(conn.execute('SELECT value FROM fixture').fetchone()[0], 'temporary')
                with self.assertRaisesRegex(sqlite3.OperationalError, 'readonly'):
                    conn.execute("INSERT INTO fixture VALUES ('forbidden')")
        self.assertEqual(self.guard.accepted_connections, 2)
        self.assertEqual(self.guard.blocked_connections, 0)
        # On Windows, unlink also proves that both SQLite handles were closed.
        db.unlink()
        self.assertFalse(db.exists())

    def test_operational_path_and_uri_are_blocked_before_open(self):
        with self.guard.installed():
            for argument, kwargs in [(self.operational, {}), (self.operational.as_uri() + '?mode=ro', {'uri': True})]:
                with self.subTest(argument=argument):
                    with self.assertRaises(SQLiteIsolationViolation):
                        sqlite3.connect(argument, **kwargs)
        self.assertEqual(self.guard.blocked_connections, 2)
        self.assertFalse(self.operational.exists())

    def test_protected_path_remains_blocked_even_inside_approved_parent(self):
        guard = SQLiteIsolationGuard([self.root], protected_paths=[self.operational])
        with self.assertRaises(SQLiteIsolationViolation):
            guard.require_allowed(self.operational.as_uri() + '?mode=rw')

    def test_malformed_uri_is_rejected(self):
        for uri in ['file:', 'file:relative.db?mode=ro', 'file:///tmp/bad%ZZ.db',
                    'file:///tmp/bad%00.db', 'file:///tmp/test.db?mode=ro&mode=rw',
                    'file:///tmp/test.db?mode', 'file:///tmp/test.db#fragment',
                    'file://remote-host/share/test.db', 'file://[malformed/database', 'https://example.test/database',
                    'file:///tmp/test.db?vfs=arbitrary']:
            with self.subTest(uri=uri), self.assertRaises(SQLiteIsolationViolation):
                canonical_sqlite_path(uri)

    def test_parent_traversal_and_prefix_sibling_do_not_bypass_allowlist(self):
        escaped = self.qa / '..' / 'data' / 'agent.db'
        sibling = self.root / 'allowed-evil' / 'fixture.db'
        for value in [str(escaped), escaped.as_uri() + '?mode=ro',
                      str(sibling), sibling.as_uri() + '?mode=ro',
                      escaped.as_uri().replace('/../', '/%2e%2e/') + '?mode=ro']:
            with self.subTest(value=value), self.assertRaises(SQLiteIsolationViolation):
                self.guard.require_allowed(value)

    def test_mode_memory_is_not_a_substring_escape_hatch(self):
        self.assertIsNone(self.guard.require_allowed(':memory:'))
        for value in [self.operational.as_uri() + '?mode=memory',
                      self.operational.as_uri() + '?ignored=mode=memory', 'file::memory:?cache=shared']:
            with self.subTest(value=value), self.assertRaises(SQLiteIsolationViolation):
                self.guard.require_allowed(value)

    def test_windows_drive_and_separator_forms_preserve_canonical_identity(self):
        if os.name != 'nt':
            self.skipTest('Windows filesystem contract')
        db = self.qa / 'fixture.db'
        for value in [str(db), db.as_posix(), db.as_uri() + '?mode=ro',
                      'file:' + db.as_posix() + '?mode=ro',
                      db.as_uri().replace('file:///', 'file://localhost/') + '?mode=ro']:
            with self.subTest(value=value):
                self.assertEqual(self.guard.require_allowed(value), db.resolve())
        with self.assertRaises(SQLiteIsolationViolation):
            self.guard.require_allowed(str(db)[0:2] + str(db)[3:])


if __name__ == '__main__':
    unittest.main()
