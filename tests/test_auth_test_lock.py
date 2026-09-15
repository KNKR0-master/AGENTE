import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from src.publisher.auth_test_lock import AuthTestLock, TERMINAL


class OneShotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'offline.db'
        self.lock = AuthTestLock(self.db)

    def authorize(self):
        return self.lock.authorize(1, explicitly_authorized=True)

    def test_requires_explicit_authorization(self):
        with self.assertRaises(ValueError): self.lock.authorize(1)

    def test_concurrent_consumption_one_winner(self):
        run = self.authorize()
        def consume(_):
            try:
                AuthTestLock(self.db).consume(1, run)
                return True
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(consume, range(16))), 1)

    def test_duplicate_dispatch_cannot_execute(self):
        run = self.authorize()
        self.lock.consume(1, run)
        self.lock.claim_execution(run)
        with self.assertRaises(ValueError): self.lock.claim_execution(run)

    def test_restart_refresh_cannot_consume_again(self):
        run = self.authorize()
        self.lock.consume(1, run)
        restarted = AuthTestLock(self.db)
        with self.assertRaises(ValueError): restarted.consume(1, run)
        self.assertTrue(restarted.has_history(1))

    def test_all_terminal_states_are_immutable(self):
        for state in TERMINAL:
            run = self.authorize()
            self.lock.consume(1, run)
            self.lock.claim_execution(run)
            self.lock.finish(run, state)
            self.lock.finish(run, 'UNKNOWN')
            with self.lock.connect() as conn:
                self.assertEqual(conn.execute('SELECT state FROM publisher_auth_test_runs WHERE run_id=?', (run,)).fetchone()[0], state)
            with self.assertRaises(ValueError): self.lock.consume(1, run)
            with self.assertRaises(ValueError): self.lock.claim_execution(run)

    def test_new_authorization_new_id_only_after_terminal(self):
        run = self.authorize()
        with self.assertRaises(ValueError): self.authorize()
        self.lock.consume(1, run)
        self.lock.finish(run, 'UNKNOWN')
        self.assertNotEqual(run, self.authorize())

    def test_timeout_cannot_dispatch_or_retry(self):
        run = self.authorize()
        self.lock.consume(1, run)
        with patch('src.publisher.auth_test_lock.time', return_value=10**12):
            with self.assertRaises(ValueError): self.lock.claim_execution(run)
            new = self.authorize()
        self.assertNotEqual(new, run)
        with self.assertRaises(ValueError): self.lock.consume(1, run)

    def test_exception_terminal_consumes_authorization(self):
        run = self.authorize()
        self.lock.consume(1, run)
        self.lock.finish(run, 'UNKNOWN')
        with self.assertRaises(ValueError): self.lock.claim_execution(run)


if __name__ == '__main__': unittest.main()
