"""Historical trace format plus the current QR-disabled authentication gate."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.publisher.diagnostics import AuthDiagnostics
from src.publisher.login_trace import LoginTrace
from src.publisher.worker import PublisherWorker


def snapshot(state="QR_WAITING_SCAN", auth="AUTH_NOT_RECEIVED"):
    return {
        "pages": [{
            "is_auth_page": True,
            "is_login_page": True,
            "login_state": state,
            "url": "https://www.tiktok.com/login",
            "localStorageKeys": [],
            "sessionStorageKeys": [],
        }],
        "cookies": [],
        "diagnostic_state": auth,
        "context_count": 1,
        "page_count": 1,
    }


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock_value = 0
        self.trace = LoginTrace(Path(self.temp.name) / "trace.jsonl", clock=lambda: self.clock_value)

    def test_snapshots_and_bounded_confirmation(self):
        self.trace.observe(snapshot())
        self.trace.phone_confirmed()
        self.clock_value = 61
        self.assertEqual(self.trace.observe(snapshot()), "AUTH_NOT_RECEIVED")
        records = [
            json.loads(line)
            for line in self.trace.path.read_text(encoding="utf-8").splitlines()
        ]
        reasons = [record.get("reason") for record in records]
        for reason in ["LOGIN_PAGE_OPENED", "QR_AVAILABLE", "QR_STATE_CHANGED", "AUTH_STATE_CHANGED", "FINAL_STATE"]:
            self.assertIn(reason, reasons)
        self.assertEqual(self.trace.positive_samples, 0)

    def test_terminal_protection_for_rate_limit_challenge_success_and_timeout(self):
        self.trace.event({"status": 429, "event": "HTTP_RESPONSE"})
        self.assertEqual(
            self.trace.observe(snapshot("AUTHENTICATED_PAGE", "AUTHENTICATED")),
            "RATE_LIMITED",
        )

        challenge = LoginTrace(Path(self.temp.name) / "challenge.jsonl", clock=lambda: 0)
        self.assertEqual(challenge.observe(snapshot("CHALLENGE", "UNKNOWN")), "CHALLENGE")

        success = LoginTrace(Path(self.temp.name) / "success.jsonl", clock=lambda: 0)
        self.assertIsNone(success.observe(snapshot("AUTHENTICATED_PAGE", "AUTHENTICATED")))
        self.assertEqual(
            success.observe(snapshot("AUTHENTICATED_PAGE", "AUTHENTICATED")),
            "AUTHENTICATED",
        )

        clock_value = [0]
        timeout = LoginTrace(
            Path(self.temp.name) / "timeout.jsonl", clock=lambda: clock_value[0]
        )
        timeout.observe(snapshot("UNKNOWN", "UNKNOWN"))
        clock_value[0] = 301
        self.assertEqual(timeout.observe(snapshot("UNKNOWN", "UNKNOWN")), "UNKNOWN")
        before = timeout.path.read_text(encoding="utf-8")
        timeout.observe(snapshot("AUTHENTICATED_PAGE", "AUTHENTICATED"))
        self.assertEqual(before, timeout.path.read_text(encoding="utf-8"))

    def test_http_metadata_never_contains_query_or_body(self):
        request = SimpleNamespace(
            url="https://www.tiktok.com/passport/web/check_qrconnect/?token=SECRET",
            method="POST",
            resource_type="xhr",
            redirected_from=SimpleNamespace(url="https://www.tiktok.com/login?secret=SECRET"),
            redirected_to=None,
            post_data="SECRET",
            headers={"Authorization": "SECRET"},
        )
        result = AuthDiagnostics.http_metadata(request)
        self.assertNotIn("SECRET", json.dumps(result))
        self.assertEqual(result["method"], "POST")
        self.assertEqual(result["redirected_from"], "https://www.tiktok.com/login")


class CurrentArchitectureGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "config").mkdir()
        (self.root / "config/publisher_studio.json").write_text(
            '{"tiktok_actions_paused":false,"experimental_qr_enabled":false}',
            encoding="utf-8",
        )
        self.worker = PublisherWorker(self.root, self.root / "publisher.db")
        self.worker.start = lambda: (_ for _ in ()).throw(AssertionError("START FORBIDDEN"))

    def test_qr_automation_is_disabled_without_browser_side_effects(self):
        with self.assertRaisesRegex(ValueError, "EXPERIMENTAL_QR_DISABLED"):
            self.worker.authorize_controlled_login(1, explicitly_authorized=True)
        with self.assertRaisesRegex(ValueError, "EXPERIMENTAL_QR_DISABLED"):
            self.worker.request_controlled_login(1, explicitly_authorized=True, run_id="unused")
        self.assertTrue(self.worker.commands.empty())


if __name__ == "__main__":
    unittest.main()
