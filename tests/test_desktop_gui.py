import copy
import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from config_loader import Config, default_config_data
from login_helper import (
    LoginError,
    create_qr_session,
    login_with_password,
    poll_qr_login,
    validate_authorization,
)
from monitor_service import MonitorService


class FakeResponse:
    def __init__(self, payload=None, content=b"image", error=None):
        self.payload = payload
        self.content = content
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class ExtendedConfigTests(unittest.TestCase):
    def test_poll_interval_defaults_and_boundaries(self):
        data = default_config_data()
        data.pop("monitor")
        self.assertEqual(Config.from_data(data).poll_interval_seconds, 10)

        for interval in (5, 60):
            candidate = copy.deepcopy(data)
            candidate["monitor"] = {"interval_seconds": interval}
            self.assertEqual(
                Config.from_data(candidate).poll_interval_seconds,
                interval,
            )

        for interval in (4, 61, True, 5.5):
            candidate = copy.deepcopy(data)
            candidate["monitor"] = {"interval_seconds": interval}
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                Config.from_data(candidate)

    def test_validate_save_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = default_config_data()
            data["monitor"]["interval_seconds"] = 17
            saved = Config.validate_and_save(data, path)
            reloaded = Config(path)
            self.assertEqual(saved.poll_interval_seconds, 17)
            self.assertEqual(reloaded.poll_interval_seconds, 17)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["monitor"]["interval_seconds"], 17)


class MonitorServiceTests(unittest.TestCase):
    def make_config(self, mode="monitor_only"):
        return SimpleNamespace(
            mode=mode,
            gpu_models=["RTX 3090"],
            gpu_regions=["内蒙B区"],
            gpu_num=1,
            image="image",
            private_image_uuid="",
            sendkey="",
            poll_interval_seconds=5,
        )

    def test_stop_before_start_performs_no_query(self):
        stop_event = threading.Event()
        stop_event.set()
        watch = Mock()
        events = []
        result = MonitorService(
            self.make_config(),
            stop_event=stop_event,
            watch_fn=watch,
            event_callback=events.append,
        ).run()
        self.assertEqual(result, 0)
        watch.assert_not_called()
        self.assertEqual(events[-1]["reason"], "stopped")

    def test_wait_uses_interruptible_stop_event(self):
        class StopAfterWait:
            def __init__(self):
                self.waited = []

            def is_set(self):
                return False

            def wait(self, seconds):
                self.waited.append(seconds)
                return True

            def set(self):
                pass

        stop_event = StopAfterWait()
        events = []
        service = MonitorService(
            self.make_config(),
            stop_event=stop_event,
            watch_fn=lambda config, event_callback=None: [],
            event_callback=events.append,
        )
        self.assertEqual(service.run(), 0)
        self.assertEqual(stop_event.waited, [5])
        self.assertEqual(events[-1]["reason"], "stopped")

    def test_all_region_errors_are_distinct_from_no_inventory(self):
        events = []

        def failed_watch(config, event_callback=None):
            event_callback({
                "type": "region_error",
                "region": "内蒙B区",
                "message": "SSL error",
            })
            return []

        class StopAfterWait:
            def is_set(self):
                return False

            def wait(self, seconds):
                return True

            def set(self):
                pass

        MonitorService(
            self.make_config(),
            stop_event=StopAfterWait(),
            watch_fn=failed_watch,
            event_callback=events.append,
        ).run()
        messages = [e.get("message", "") for e in events if e["type"] == "log"]
        self.assertTrue(any("所有区域" in message for message in messages))


class LoginServiceTests(unittest.TestCase):
    def test_password_is_hashed_and_only_token_is_returned(self):
        session = Mock()
        session.post.side_effect = [
            FakeResponse({"data": {"ticket": "ticket-1"}}),
            FakeResponse({"data": {"token": "token-1"}}),
        ]
        token = login_with_password("13800000000", "secret", session=session)
        self.assertEqual(token, "token-1")
        sent = session.post.call_args_list[0].kwargs["json"]
        self.assertEqual(sent["password"], hashlib.sha1(b"secret").hexdigest())
        self.assertNotEqual(sent["password"], "secret")

    def test_qr_create_and_poll_success(self):
        session = Mock()
        session.get.side_effect = [
            FakeResponse({"data": {"qrcode_url": "https://qr", "uuid": "u1"}}),
            FakeResponse(content=b"png"),
        ]
        qr = create_qr_session(session=session)
        self.assertEqual(qr["uuid"], "u1")
        self.assertEqual(qr["image"], b"png")

        session.post.side_effect = [
            FakeResponse({"data": {"ticket": "ticket-1"}}),
            FakeResponse({"data": {"token": "token-1"}}),
        ]
        result = poll_qr_login("u1", session=session)
        self.assertEqual(result, {"status": "success", "token": "token-1"})

    def test_qr_waiting_and_expired(self):
        session = Mock()
        session.post.side_effect = [
            FakeResponse({"msg": "等待扫码"}),
            FakeResponse({"msg": "二维码已过期"}),
        ]
        self.assertEqual(poll_qr_login("u1", session=session)["status"], "waiting")
        self.assertEqual(poll_qr_login("u1", session=session)["status"], "expired")

    def test_invalid_authorization_raises_without_exposing_token(self):
        session = Mock()
        session.post.return_value = FakeResponse({"msg": "登录失效", "data": None})
        with self.assertRaises(LoginError) as raised:
            validate_authorization("super-secret-token", session=session)
        self.assertNotIn("super-secret-token", str(raised.exception))


@unittest.skipUnless(os.environ.get("QT_QPA_PLATFORM") == "offscreen", "需要无头 Qt")
class GuiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication

        cls.app = QApplication.instance() or QApplication([])

    def test_window_loads_without_authorization_and_masks_secrets(self):
        from PySide6.QtWidgets import QLineEdit

        from gui import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            Config.validate_and_save(default_config_data(), path)
            window = MainWindow(path)
            self.assertFalse(window.start_button.isEnabled())
            self.assertEqual(window.auth_edit.echoMode(), QLineEdit.Password)
            self.assertEqual(window.sendkey_edit.echoMode(), QLineEdit.Password)
            window.close()

    def test_running_state_locks_configuration_and_login(self):
        from gui import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = default_config_data()
            data["authorization"] = "token"
            Config.validate_and_save(data, path)
            window = MainWindow(path)
            self.assertTrue(window.start_button.isEnabled())
            window._set_running(True)
            self.assertFalse(window.settings_group.isEnabled())
            self.assertFalse(window.login_button.isEnabled())
            self.assertTrue(window.stop_button.isEnabled())
            window._set_running(False)
            self.assertTrue(window.settings_group.isEnabled())
            window.close()

    def test_inventory_events_update_status_without_network(self):
        from gui import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            data = default_config_data()
            Config.validate_and_save(data, path)
            window = MainWindow(path)
            window._initialize_inventory()
            window._handle_event({
                "type": "watch",
                "event": {
                    "type": "region_result",
                    "region": "内蒙B区",
                    "inventory": {
                        "RTX 3090": {
                            "idle_gpu_num": 1,
                            "total_gpu_num": 10,
                        }
                    },
                },
            })
            row = window.row_map[("RTX 3090", "内蒙B区")]
            self.assertEqual(window.inventory.item(row, 4).text(), "有货")
            window.close()

    def test_close_requests_safe_stop(self):
        from gui import MainWindow

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            Config.validate_and_save(default_config_data(), path)
            window = MainWindow(path)
            worker = Mock()
            window.worker = worker
            event = Mock()
            window.closeEvent(event)
            worker.stop.assert_called_once()
            event.ignore.assert_called_once()
            window.worker = None
            window.close()


if __name__ == "__main__":
    unittest.main()
