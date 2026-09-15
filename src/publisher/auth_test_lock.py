"""Durable, fail-closed authorization ledger for one-shot authentication."""
import sqlite3
from contextlib import contextmanager
from time import time
from uuid import uuid4

TERMINAL = {'AUTHENTICATED', 'RATE_LIMITED', 'CHALLENGE', 'LOGIN_REJECTED',
            'AUTH_NOT_RECEIVED', 'AUTH_STATE_PRESENT_BUT_PAGE_STUCK', 'UNKNOWN'}


class AuthTestLock:
    def __init__(self, db):
        self.db = db
        with self.connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS publisher_auth_test_runs (
                run_id TEXT PRIMARY KEY, account_id INTEGER NOT NULL,
                state TEXT NOT NULL, authorized_at REAL NOT NULL,
                started_at REAL, finished_at REAL)''')
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS publisher_auth_test_active ON publisher_auth_test_runs(account_id) WHERE state IN ('AUTHORIZED','RESERVED','RUNNING')")

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.db, timeout=10)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def authorize(self, account_id, *, explicitly_authorized=False):
        if explicitly_authorized is not True:
            raise ValueError('EXPLICIT_AUTHORIZATION_REQUIRED')
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("UPDATE publisher_auth_test_runs SET state='UNKNOWN',finished_at=? WHERE state IN ('RESERVED','RUNNING') AND started_at<=?", (time(), time()-300))
            if conn.execute("SELECT 1 FROM publisher_auth_test_runs WHERE account_id=? AND state IN ('AUTHORIZED','RESERVED','RUNNING')", (account_id,)).fetchone():
                raise ValueError('LOGIN_TEST_ALREADY_RUNNING')
            run_id = str(uuid4())
            conn.execute("INSERT INTO publisher_auth_test_runs VALUES (?,?,'AUTHORIZED',?,NULL,NULL)", (run_id, account_id, time()))
            return run_id

    def consume(self, account_id, run_id):
        with self.connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            changed = conn.execute("UPDATE publisher_auth_test_runs SET state='RESERVED',started_at=? WHERE run_id=? AND account_id=? AND state='AUTHORIZED'", (time(), run_id, account_id)).rowcount
            if changed != 1:
                raise ValueError('LOGIN_TEST_ALREADY_RUNNING_OR_CONSUMED')

    def claim_execution(self, run_id):
        with self.connect() as conn:
            changed = conn.execute("UPDATE publisher_auth_test_runs SET state='RUNNING' WHERE run_id=? AND state='RESERVED' AND started_at>?", (run_id, time()-300)).rowcount
            if changed != 1:
                raise ValueError('LOGIN_TEST_ALREADY_RUNNING_OR_CONSUMED')

    def finish(self, run_id, state):
        if state not in TERMINAL:
            raise ValueError('INVALID_TERMINAL_STATE')
        with self.connect() as conn:
            conn.execute("UPDATE publisher_auth_test_runs SET state=?,finished_at=? WHERE run_id=? AND state IN ('RESERVED','RUNNING')", (state, time(), run_id))

    def has_history(self, account_id):
        with self.connect() as conn:
            return conn.execute('SELECT 1 FROM publisher_auth_test_runs WHERE account_id=?', (account_id,)).fetchone() is not None
