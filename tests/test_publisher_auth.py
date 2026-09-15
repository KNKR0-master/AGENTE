"""Offline coverage for the current manual-authentication architecture."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.publisher.auth import (
    AUTHENTICATED,
    LOGIN_IN_PROGRESS,
    LOGIN_URL,
    NEEDS_LOGIN,
    NOT_AUTHENTICATED,
    STUDIO_URL,
    AuthObservation,
    LoginSession,
    TikTokAuthDetector,
)
from src.publisher.service import PublisherService, get_row, init_publisher
from src.publisher.worker import PublisherWorker
from src.store import connect, init_db


class FakeContext:
    def __init__(self):
        self.pages = []
        self.jar = []

    def cookies(self, urls=None):
        return self.jar


class FakePage:
    def __init__(self, context, url="about:blank"):
        self.context = context
        self.url = url
        self.signals = {}

    def evaluate(self, script):
        return {"url": self.url, **self.signals}


class PublisherAuthTests(unittest.TestCase):
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
        (self.root / "config").mkdir()
        (self.root / "config/publisher_studio.json").write_text(
            json.dumps({"tiktok_actions_paused": False, "experimental_qr_enabled": False}),
            encoding="utf-8",
        )
        self.worker = PublisherWorker(self.root, self.db)
        self.start = patch.object(self.worker, "start").start()
        self.addCleanup(patch.stopall)

    def test_ordinary_login_requires_explicit_one_shot_authorization(self):
        with self.assertRaisesRegex(ValueError, "EXPLICIT_ONE_SHOT_AUTHORIZATION_REQUIRED"):
            self.worker.submit("login", self.account)
        self.start.assert_not_called()
        self.assertTrue(self.worker.commands.empty())

    def test_qr_authorization_remains_disabled(self):
        with self.assertRaisesRegex(ValueError, "EXPERIMENTAL_QR_DISABLED"):
            self.worker.authorize_controlled_login(self.account, explicitly_authorized=True)
        with self.assertRaisesRegex(ValueError, "EXPERIMENTAL_QR_DISABLED"):
            self.worker.request_controlled_login(self.account, explicitly_authorized=True, run_id="unused")
        self.start.assert_not_called()
        self.assertTrue(self.worker.commands.empty())

    def test_concurrent_login_button_requests_cannot_enqueue_work(self):
        def request(_):
            try:
                self.worker.submit("login", self.account)
            except ValueError as exc:
                return str(exc)
            return "unexpected success"

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(request, range(16)))
        self.assertEqual(set(results), {"EXPLICIT_ONE_SHOT_AUTHORIZATION_REQUIRED"})
        self.start.assert_not_called()
        self.assertTrue(self.worker.commands.empty())

    def test_restart_does_not_resume_or_start_authentication(self):
        account = get_row(self.conn, "publisher_accounts", self.account)
        snapshot = self.worker.login_snapshot(account)
        self.assertFalse(snapshot["login_active"])
        self.assertIsNone(self.worker.thread)
        self.assertTrue(self.worker.commands.empty())

        from src.publisher.auth import recover_auth
        recover_auth(self.conn)
        restarted = PublisherWorker(self.root, self.db)
        self.addCleanup(restarted.shutdown)
        self.assertIsNone(restarted.thread)
        self.assertTrue(restarted.commands.empty())
        self.assertFalse(
            restarted.login_snapshot(
                get_row(self.conn, "publisher_accounts", self.account)
            )["login_active"]
        )


class DetectorTests(unittest.TestCase):
    COOKIE = {"name": "sessionid", "value": "fixture", "domain": ".tiktok.com", "expires": -1}

    def setUp(self):
        self.context = FakeContext()
        self.page = FakePage(self.context, STUDIO_URL)
        self.context.pages.append(self.page)
        self.detector = TikTokAuthDetector()

    @staticmethod
    def authenticated_inventory():
        return [
            {"tag": "button", "text": "Métricas principais"},
            {"tag": "button", "text": "Visualizações de vídeo"},
            {"tag": "button", "text": "Publicações recentes"},
        ]

    def test_url_alone_and_qr_disappearance_are_not_success(self):
        for url in [LOGIN_URL, STUDIO_URL, "https://www.tiktok.com/@someone"]:
            self.page.url = url
            self.page.signals = {}
            self.assertFalse(self.detector.observe(self.page).positive)

    def test_studio_requires_cookie_and_authenticated_dom_marker(self):
        self.page.signals = {"inventory": self.authenticated_inventory(), "login": False}
        self.assertFalse(self.detector.observe(self.page).positive)
        self.context.jar = [self.COOKIE]
        self.assertTrue(self.detector.observe(self.page).positive)

    def test_login_url_never_bridges_to_authenticated_state(self):
        self.page.url = LOGIN_URL
        self.page.signals = {
            "connected": True,
            "inventory": self.authenticated_inventory(),
            "login": False,
        }
        self.context.jar = [self.COOKIE]
        self.assertFalse(self.detector.observe(self.page).positive)

    def test_login_control_challenge_and_invalid_cookie_fail_closed(self):
        self.page.signals = {"inventory": self.authenticated_inventory(), "login": True}
        self.context.jar = [self.COOKIE]
        self.assertFalse(self.detector.observe(self.page).positive)
        self.page.signals = {"inventory": self.authenticated_inventory(), "challenge": True}
        self.assertFalse(self.detector.observe(self.page).positive)
        self.page.signals = {"inventory": self.authenticated_inventory(), "login": False}
        for change in [{"expires": 1}, {"domain": ".evil.example"}, {"value": ""}]:
            self.context.jar = [{**self.COOKIE, **change}]
            self.assertFalse(self.detector.observe(self.page).positive)

    def test_state_requires_two_consistent_positive_readings_and_no_timeout(self):
        session = LoginSession(status=LOGIN_IN_PROGRESS, manual_pending=True)
        positive = AuthObservation(True, location="/tiktokstudio")
        self.assertEqual(session.detect(positive), LOGIN_IN_PROGRESS)
        self.assertEqual(session.detect(AuthObservation()), LOGIN_IN_PROGRESS)
        self.assertEqual(session.detect(positive), LOGIN_IN_PROGRESS)
        self.assertEqual(session.detect(positive), AUTHENTICATED)
        self.assertEqual(session.detect(AuthObservation(login_required=True)), NEEDS_LOGIN)
        self.assertEqual(session.detect(AuthObservation()), NOT_AUTHENTICATED)


if __name__ == "__main__":
    unittest.main()
