"""Offline tests for read-only existing-session inspection over the CDP runtime."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from src.publisher.auth import STUDIO_URL
from src.publisher.service import PublisherService, init_publisher
from src.publisher.worker import PublisherWorker
from src.store import connect, init_db
from tests.test_publisher_diagnostics import Context, Page


class ExistingSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "publisher.db"
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.account = PublisherService(self.conn, self.root).save_account({"name": "Fixture"})
        self.profile = self.conn.execute(
            "SELECT browser_profile FROM publisher_accounts WHERE id=?", (self.account,)
        ).fetchone()[0]
        (self.root / self.profile).mkdir(parents=True)
        (self.root / "config").mkdir()
        (self.root / "config/publisher_studio.json").write_text(
            '{"tiktok_actions_paused":false,"experimental_qr_enabled":false}',
            encoding="utf-8",
        )
        self.worker = PublisherWorker(self.root, self.db)
        self.worker.start = MagicMock()
        self.page = Page("about:blank")
        self.page.goto = MagicMock()
        self.page.bring_to_front = MagicMock()
        self.context = Context([self.page])
        self.context.route = MagicMock()
        self.context.unroute = MagicMock()
        self.playwright = MagicMock()
        self.worker.browser_runtime.get_context = MagicMock(return_value=self.context)

    def process(self, navigate=False):
        self.worker.inspect_existing_session(self.account, navigate)
        command, (account, requested_navigation) = self.worker.commands.get_nowait()
        self.assertEqual(command, "inspect-existing")
        self.worker.handle_existing_session(self.playwright, self.conn, account, requested_navigation)

    def test_initial_inspection_uses_existing_cdp_context_read_only(self):
        self.process()
        self.worker.browser_runtime.get_context.assert_called_once_with(
            self.playwright, (self.root / self.profile).resolve()
        )
        self.page.goto.assert_not_called()
        self.context.new_page.assert_not_called()
        self.assertTrue(self.worker.commands.empty())

    def test_navigation_is_one_controlled_read_only_studio_visit(self):
        self.process()
        self.process(True)
        self.page.goto.assert_called_once_with(
            STUDIO_URL, wait_until="domcontentloaded", timeout=30000
        )
        with self.assertRaisesRegex(ValueError, "navegacao unica"):
            self.worker.inspect_existing_session(self.account, True)

    def test_login_and_qr_navigations_are_blocked_by_inspection_guard(self):
        self.process()
        guard = self.context.route.call_args.args[1]
        for url in [
            "https://www.tiktok.com/login/qrcode",
            "https://www.tiktok.com/login",
            "https://www.tiktok.com/passport/web/get_qrcode/",
        ]:
            request = SimpleNamespace(
                url=url,
                is_navigation_request=lambda: True,
                frame=SimpleNamespace(parent_frame=None),
            )
            route = SimpleNamespace(request=request, abort=MagicMock(), continue_=MagicMock())
            guard(route)
            route.abort.assert_called_once_with("blockedbyclient")
            route.continue_.assert_not_called()
        self.assertTrue(self.worker.login_sessions[self.account].blocked_auth_navigation)

    def test_missing_profile_never_creates_one(self):
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_accounts SET browser_profile=? WHERE id=?",
                ("data/publisher/browser_profiles/absent", self.account),
            )
        with self.assertRaises(ValueError):
            self.worker.inspect_existing_session(self.account)
        self.worker.start.assert_not_called()
        self.assertFalse((self.root / "data/publisher/browser_profiles/absent").exists())

    def test_verified_studio_is_not_redirected_to_login(self):
        self.process()
        session = self.worker.login_sessions[self.account]
        session.status = "AUTHENTICATED"
        self.page.url = STUDIO_URL
        self.worker.handle_login(self.playwright, self.conn, self.account)
        self.page.goto.assert_not_called()


if __name__ == "__main__":
    unittest.main()
