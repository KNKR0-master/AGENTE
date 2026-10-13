"""Test-only SQLite allowlist for disposable databases, including Windows URIs.

The audit hook sees the database argument, not SQLite's ``uri`` keyword. Decode
file URIs exactly once with the platform's url2pathname before checking the
canonical path. The normalizer never opens a database or changes its URI/mode.
"""

import os
import re
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from urllib.request import url2pathname


class SQLiteIsolationViolation(AssertionError):
    pass


def canonical_sqlite_path(database):
    """Return an absolute filesystem identity, or None for exact :memory:."""
    value = os.fspath(database)
    if not isinstance(value, str) or not value or '\x00' in value:
        raise SQLiteIsolationViolation('INVALID_SQLITE_DATABASE_ARGUMENT')
    if value == ':memory:':
        return None
    if value.startswith('file:'):
        if re.search(r'%(?![0-9a-fA-F]{2})', value):
            raise SQLiteIsolationViolation('MALFORMED_SQLITE_URI_ESCAPE')
        try:
            uri = urlsplit(value)
        except ValueError as exc:
            raise SQLiteIsolationViolation('MALFORMED_SQLITE_URI') from exc
        if uri.netloc not in ('', 'localhost') or uri.fragment or not uri.path or '\\' in uri.path:
            raise SQLiteIsolationViolation('MALFORMED_OR_NONLOCAL_SQLITE_URI')
        try:
            options = parse_qsl(uri.query, keep_blank_values=True, strict_parsing=True)
        except ValueError as exc:
            raise SQLiteIsolationViolation('MALFORMED_SQLITE_URI_QUERY') from exc
        allowed = {'mode': {'ro', 'rw', 'rwc'}, 'cache': {'private', 'shared'}, 'immutable': {'0', '1'}}
        if len({key for key, _ in options}) != len(options) or any(
            key not in allowed or option not in allowed[key] for key, option in options
        ):
            raise SQLiteIsolationViolation('UNSUPPORTED_SQLITE_URI_OPTIONS')
        # On Windows, unquote(uri.path) retains /C:/ and Path.resolve() treats
        # it incorrectly. url2pathname preserves the absolute drive identity.
        try:
            value = url2pathname(uri.path)
        except (OSError, ValueError) as exc:
            raise SQLiteIsolationViolation('MALFORMED_SQLITE_URI_PATH') from exc
        if '\x00' in value:
            raise SQLiteIsolationViolation('MALFORMED_SQLITE_URI_PATH')
    elif re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', value) and not re.match(r'^[a-zA-Z]:[\\/]', value):
        raise SQLiteIsolationViolation('UNSUPPORTED_SQLITE_DATABASE_SCHEME')
    path = Path(value)
    if not path.is_absolute():
        raise SQLiteIsolationViolation('ABSOLUTE_SQLITE_DATABASE_REQUIRED')
    if os.name == 'nt' and any(':' in part for part in path.parts[1:]):
        raise SQLiteIsolationViolation('SQLITE_ALTERNATE_STREAM_FORBIDDEN')
    try:
        return path.resolve()
    except (OSError, ValueError) as exc:
        raise SQLiteIsolationViolation('INVALID_SQLITE_DATABASE_PATH') from exc


class SQLiteIsolationGuard:
    """Permit memory and canonical descendants of explicitly approved QA roots."""

    def __init__(self, approved_roots, *, protected_paths=()):
        self.roots = tuple(Path(root).resolve() for root in approved_roots)
        if not self.roots:
            raise ValueError('EXPLICIT_TEMPORARY_ROOT_REQUIRED')
        self.protected = frozenset(Path(path).resolve() for path in protected_paths)
        self.accepted_connections = 0
        self.blocked_connections = 0
        self._active = False

    def require_allowed(self, database):
        path = canonical_sqlite_path(database)
        if path is None:
            return None
        if path in self.protected or not any(path != root and path.is_relative_to(root) for root in self.roots):
            raise SQLiteIsolationViolation('SQLITE_DATABASE_OUTSIDE_APPROVED_TEMPORARY_ROOT')
        return path

    def _audit(self, event, args):
        if self._active and event == 'sqlite3.connect':
            try:
                self.require_allowed(args[0])
            except SQLiteIsolationViolation:
                self.blocked_connections += 1
                raise
            self.accepted_connections += 1

    @contextmanager
    def installed(self):
        """Activate during a gate; leave the permanent audit hook inert after it."""
        if self._active:
            raise RuntimeError('SQLITE_ISOLATION_GUARD_ALREADY_ACTIVE')
        sys.addaudithook(self._audit)
        self._active = True
        try:
            yield self
        finally:
            self._active = False
