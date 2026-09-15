import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.publisher.auth import recover_auth, persist_auth, TikTokAuthDetector, LoginSession
from src.publisher.manual_profile import ProfileOwnership
from src.publisher.worker import PublisherWorker
from src.publisher.service import draft_retry_safe, transition


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.db=self.root/'test.db'
        self.profile=self.root/'profile';self.profile.mkdir()
        self.conn=sqlite3.connect(self.db);self.addCleanup(self.conn.close)
        self.conn.row_factory=sqlite3.Row
        self.conn.executescript("""CREATE TABLE publisher_accounts(id INTEGER PRIMARY KEY,auth_status TEXT,session_verified INTEGER,auth_detail TEXT,session_verified_at TEXT,updated_at TEXT);
        INSERT INTO publisher_accounts VALUES(1,'AUTHENTICATED',1,'verified','original-time','original-time');""")
        self.inventory=MagicMock(return_value=[])
        self.identity=MagicMock(return_value='process-created-1')
        self.owner=ProfileOwnership(self.db,inventory=self.inventory,identity=self.identity)

    def test_restart_preserves_verified_history(self):
        recover_auth(self.conn)
        self.assertEqual(tuple(self.conn.execute('SELECT auth_status,session_verified,session_verified_at FROM publisher_accounts').fetchone()),('AUTHENTICATED',1,'original-time'))

    def test_no_context_or_closed_page_does_not_invalidate(self):
        worker=PublisherWorker(self.root,self.db)
        worker.login_sessions[1]=LoginSession(status='NOT_AUTHENTICATED',page=object())
        worker.login_page_closed(1,worker.login_sessions[1].page)
        self.assertEqual(self.conn.execute('SELECT session_verified FROM publisher_accounts').fetchone()[0],1)
        self.assertEqual(worker.runtime_auth[1],'SESSION_REQUIRES_VERIFICATION')

    def test_unknown_does_not_destroy_verification(self):
        persist_auth(self.conn,1,'UNKNOWN','detector error')
        self.assertEqual(self.conn.execute('SELECT session_verified FROM publisher_accounts').fetchone()[0],1)

    def test_real_login_page_is_invalid_evidence(self):
        page=SimpleNamespace(evaluate=lambda _: {'url':'https://www.tiktok.com/login','login':True},
            context=SimpleNamespace(cookies=lambda _: []))
        observation=TikTokAuthDetector().observe(page)
        self.assertTrue(observation.login_required)
        persist_auth(self.conn,1,'NEEDS_LOGIN','observed login UI')
        self.assertEqual(self.conn.execute('SELECT session_verified FROM publisher_accounts').fetchone()[0],0)

    def test_shutdown_release_waits_for_chrome(self):
        token=self.owner.acquire(self.profile,'PLAYWRIGHT')
        self.owner.activate(self.profile,token,'PLAYWRIGHT_ACTIVE')
        self.inventory.return_value=[99]
        self.assertFalse(self.owner.release(self.profile,token))
        self.inventory.return_value=[]
        self.assertTrue(self.owner.release(self.profile,token))
        self.assertEqual(self.owner.state(self.profile),'SESSION_REQUIRES_VERIFICATION')

    def test_dead_owner_stale_reconciles_then_can_acquire(self):
        token=self.owner.acquire(self.profile,'PLAYWRIGHT')
        self.identity.return_value=None
        self.assertTrue(self.owner.reconcile(self.profile))
        self.identity.return_value='new-process'
        self.assertNotEqual(self.owner.acquire(self.profile,'PLAYWRIGHT'),token)

    def test_live_owner_respected(self):
        self.owner.acquire(self.profile,'PLAYWRIGHT')
        self.assertFalse(self.owner.reconcile(self.profile))

    def test_pid_reuse_detected(self):
        self.owner.acquire(self.profile,'PLAYWRIGHT')
        self.identity.return_value='new-process-same-pid'
        self.assertTrue(self.owner.reconcile(self.profile))

    def test_chrome_blocks_stale_recovery(self):
        self.owner.acquire(self.profile,'PLAYWRIGHT')
        self.identity.return_value=None;self.inventory.return_value=[99]
        self.assertFalse(self.owner.reconcile(self.profile))

    def test_retry_requires_explicit_action_and_no_checkpoints(self):
        self.conn.executescript("""CREATE TABLE publisher_drafts(id INTEGER PRIMARY KEY,status TEXT,confirmed_at TEXT,tiktok_draft_id TEXT,tiktok_draft_url TEXT,checklist_json TEXT,media_asset_id INTEGER,error_code TEXT,updated_at TEXT);
        CREATE TABLE publisher_execution_logs(id INTEGER PRIMARY KEY,draft_id INTEGER,step TEXT,status TEXT,message TEXT,screenshot_path TEXT,created_at TEXT);
        INSERT INTO publisher_drafts VALUES(1,'STUDIO_CHANGED',NULL,NULL,NULL,'{}',NULL,NULL,NULL);""")
        self.assertTrue(draft_retry_safe(self.conn,1))
        with self.assertRaises(ValueError):transition(self.conn,1,'DRAFT')
        self.conn.execute("UPDATE publisher_drafts SET checklist_json='{"+'"upload":true'+"}'")
        self.assertFalse(draft_retry_safe(self.conn,1))
        with self.assertRaises(ValueError):transition(self.conn,1,'DRAFT',explicit_reset=True)


if __name__=='__main__':unittest.main()
