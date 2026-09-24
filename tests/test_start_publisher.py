import subprocess
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts.start_publisher import (
    CTRL_BREAK_EVENT,
    CTRL_C_EVENT,
    CTRL_CLOSE_EVENT,
    CURRENT_COMPATIBLE_INSTANCE,
    FOREIGN_PROCESS_ON_PORT,
    NO_INSTANCE,
    OLD_OR_INCOMPATIBLE_PUBLISHER,
    ShutdownCoordinator,
    WindowsConsoleHandler,
    classify_runtime_instance,
    request_server_shutdown,
    stop_server,
)


class StartPublisherShutdownTests(unittest.TestCase):
    def test_runtime_info_accepts_only_current_compatibility(self):
        current = {
            "app": "publisher-desk",
            "api_version": "publisher-api-v1",
            "compatibility": "publisher-ui-2a.3.3",
            "process_id": 123,
            "instance_id": "instance",
            "started_at": "2026-09-16T00:00:00+00:00",
        }
        with patch("scripts.start_publisher._runtime_info_probe", return_value=(current, 200)):
            status, payload = classify_runtime_instance()
        self.assertEqual(status, CURRENT_COMPATIBLE_INSTANCE)
        self.assertEqual(payload, current)

    def test_old_publisher_is_not_reported_as_compatible(self):
        with patch("scripts.start_publisher._runtime_info_probe", return_value=(None, 404)), patch(
            "scripts.start_publisher._port_is_open", return_value=True
        ):
            status, payload = classify_runtime_instance()
        self.assertEqual(status, OLD_OR_INCOMPATIBLE_PUBLISHER)
        self.assertIsNone(payload)

    def test_previous_ui_version_is_not_reused(self):
        previous = {
            "app": "publisher-desk", "api_version": "publisher-api-v1",
            "compatibility": "publisher-ui-2a.2", "process_id": 123,
            "instance_id": "previous", "started_at": "2026-09-16T00:00:00+00:00",
        }
        with patch("scripts.start_publisher._runtime_info_probe", return_value=(previous, 200)):
            status, _ = classify_runtime_instance()
        self.assertEqual(status, OLD_OR_INCOMPATIBLE_PUBLISHER)

    def test_foreign_port_and_free_port_are_distinguished(self):
        with patch("scripts.start_publisher._runtime_info_probe", return_value=(None, None)), patch(
            "scripts.start_publisher._port_is_open", return_value=True
        ):
            status, _ = classify_runtime_instance()
        self.assertEqual(status, FOREIGN_PROCESS_ON_PORT)

        with patch("scripts.start_publisher._runtime_info_probe", return_value=(None, None)), patch(
            "scripts.start_publisher._port_is_open", return_value=False
        ):
            status, _ = classify_runtime_instance()
        self.assertEqual(status, NO_INSTANCE)

    def test_windows_handler_registers_and_unregisters_console_callback(self):
        api = MagicMock()
        api.SetConsoleCtrlHandler.return_value = 1
        coordinator = ShutdownCoordinator(lambda reason: None)
        handler = WindowsConsoleHandler(coordinator, api=api)

        self.assertTrue(handler.install())
        callback = handler._callback
        self.assertIsNotNone(callback)
        api.SetConsoleCtrlHandler.assert_called_once_with(callback, True)
        handler.uninstall()
        self.assertEqual(api.SetConsoleCtrlHandler.call_count, 2)
        self.assertEqual(api.SetConsoleCtrlHandler.call_args.args, (callback, False))

    def test_shutdown_is_idempotent_across_control_events(self):
        calls = []
        started = threading.Event()
        release = threading.Event()

        def shutdown(reason):
            calls.append(reason)
            started.set()
            release.wait(1)

        coordinator = ShutdownCoordinator(shutdown, timeout=1)
        self.assertTrue(coordinator.request("CTRL_C_EVENT"))
        self.assertTrue(started.wait(1))
        self.assertFalse(coordinator.request("CTRL_BREAK_EVENT"))
        self.assertFalse(coordinator.request("CTRL_CLOSE_EVENT"))
        release.set()
        self.assertTrue(coordinator.wait(1))
        self.assertEqual(calls, ["CTRL_C_EVENT"])

    def test_console_events_share_one_shutdown_path(self):
        calls = []
        coordinator = ShutdownCoordinator(lambda reason: calls.append(reason), timeout=1)
        handler = WindowsConsoleHandler(coordinator)

        for event_code in (CTRL_C_EVENT, CTRL_BREAK_EVENT, CTRL_CLOSE_EVENT):
            self.assertTrue(handler._handle(event_code))
        self.assertTrue(coordinator.wait(1))
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], "CTRL_C_EVENT")

    def test_close_event_hands_off_cooperatively_before_requesting_shutdown(self):
        calls = []
        events = []
        coordinator = ShutdownCoordinator(lambda reason: calls.append(reason), timeout=1)
        handler = WindowsConsoleHandler(
            coordinator,
            before_request=lambda: True,
            event_logger=lambda event, **fields: events.append((event, fields)),
        )

        self.assertTrue(handler._handle(CTRL_CLOSE_EVENT))
        self.assertTrue(coordinator.wait(1))
        self.assertEqual(calls, ["CTRL_CLOSE_EVENT"])
        self.assertEqual(events[0][0], "CTRL_CLOSE_EVENT_RECEIVED")
        self.assertIn("COOPERATIVE_SERVER_SHUTDOWN_CALLED", [event for event, _ in events])
        self.assertIn("HANDLER_RETURNED_BEFORE_CHILD_EXIT", [event for event, _ in events])

    def test_close_event_continues_when_cooperative_handoff_fails(self):
        calls = []
        coordinator = ShutdownCoordinator(lambda reason: calls.append(reason), timeout=1)
        handler = WindowsConsoleHandler(coordinator, before_request=lambda: (_ for _ in ()).throw(OSError("offline")))

        self.assertTrue(handler._handle(CTRL_CLOSE_EVENT))
        self.assertTrue(coordinator.wait(1))
        self.assertEqual(calls, ["CTRL_CLOSE_EVENT"])

    def test_unknown_console_event_is_not_claimed(self):
        coordinator = ShutdownCoordinator(lambda reason: None)
        handler = WindowsConsoleHandler(coordinator)
        self.assertFalse(handler._handle(99))
        self.assertFalse(coordinator.requested.is_set())

    def test_finished_server_does_not_receive_a_second_signal(self):
        process = SimpleNamespace(poll=lambda: 0)
        process.send_signal = MagicMock()
        process.wait = MagicMock()

        self.assertTrue(stop_server(process, timeout=0.01))
        process.send_signal.assert_not_called()
        process.wait.assert_not_called()

    def test_timeout_reports_without_force_kill(self):
        process = SimpleNamespace(poll=lambda: None)
        process.send_signal = MagicMock()
        process.wait = MagicMock(side_effect=subprocess.TimeoutExpired("publisher", 0.01))

        with patch("scripts.start_publisher.request_server_shutdown", return_value=False):
            self.assertFalse(stop_server(process, timeout=0.01))
        process.send_signal.assert_called_once()
        process.wait.assert_called_once_with(timeout=0.01)


if __name__ == "__main__":
    unittest.main()
