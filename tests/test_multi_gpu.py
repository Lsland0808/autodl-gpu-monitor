import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import requests

import gpu
import main
import watch
from config_loader import Config


def candidate(model, region, idle=1):
    signs = {
        "内蒙B区": ["nm-B1", "nm-B2"],
        "重庆A区": ["cq-A1"],
    }
    return {
        "gpu": model,
        "region": region,
        "region_sign_list": signs[region],
        "idle_gpu_num": idle,
        "total_gpu_num": 8,
    }


class ConfigTests(unittest.TestCase):
    def make_config(self, gpu_config):
        payload = {
            "authorization": "token",
            "mode": "monitor_only",
            "gpu": gpu_config,
            "image": {
                "private_image_uuid": "",
                "autodl_image": "example-image",
            },
            "notification": {"sendkey": ""},
        }
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "config.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return Config(path)

    def test_new_config_deduplicates_and_preserves_order(self):
        config = self.make_config({
            "models": ["RTX 4090", "RTX 3090", "RTX 4090"],
            "num": 1,
            "regions": ["重庆A区", "内蒙B区", "重庆A区"],
        })
        self.assertEqual(config.gpu_models, ["RTX 4090", "RTX 3090"])
        self.assertEqual(config.gpu_regions, ["重庆A区", "内蒙B区"])
        self.assertTrue(config.regions_explicit)

    def test_legacy_model_uses_default_regions(self):
        config = self.make_config({"model": "RTX 3090", "num": 1})
        self.assertEqual(config.gpu_models, ["RTX 3090"])
        self.assertEqual(config.gpu_regions, ["内蒙B区"])
        self.assertFalse(config.regions_explicit)

    def test_models_take_precedence_over_legacy_model(self):
        with patch("builtins.print") as print_mock:
            config = self.make_config({
                "model": "RTX 3090",
                "models": ["RTX 4090D"],
                "num": 1,
                "regions": ["重庆A区"],
            })
        self.assertEqual(config.gpu_models, ["RTX 4090D"])
        print_mock.assert_called_once()

    def test_invalid_or_empty_values_fail_fast(self):
        invalid_configs = [
            {"models": [], "num": 1, "regions": ["内蒙B区"]},
            {"models": ["不存在的GPU"], "num": 1, "regions": ["内蒙B区"]},
            {"models": ["RTX 3090"], "num": 1, "regions": []},
            {"models": ["RTX 3090"], "num": 1, "regions": ["不存在的区域"]},
            {"models": ["RTX 3090"], "num": 0, "regions": ["内蒙B区"]},
        ]
        for gpu_config in invalid_configs:
            with self.subTest(gpu_config=gpu_config):
                with self.assertRaises(ValueError):
                    self.make_config(gpu_config)


class WatchTests(unittest.TestCase):
    @patch("watch.UserAgent")
    @patch("watch.query_post_json")
    def test_each_region_is_queried_once_and_results_follow_priority(
        self, query_mock, user_agent_mock
    ):
        user_agent_mock.return_value.random = "test-agent"
        query_mock.side_effect = [
            {"data": [
                {"RTX 4090": {"idle_gpu_num": 2}},
                {"RTX 3090": {"idle_gpu_num": 1}},
            ]},
            {"data": [
                {"RTX 3090": {"idle_gpu_num": 3}},
                {"RTX 4090": {"idle_gpu_num": 4}},
            ]},
        ]
        config = SimpleNamespace(
            Authorization="token",
            gpu_models=["RTX 3090", "RTX 4090"],
            gpu_regions=["内蒙B区", "重庆A区"],
        )

        results = watch.watch_gpus(config)

        self.assertEqual(query_mock.call_count, 2)
        self.assertEqual(
            [(item["gpu"], item["region"]) for item in results],
            [
                ("RTX 3090", "内蒙B区"),
                ("RTX 3090", "重庆A区"),
                ("RTX 4090", "内蒙B区"),
                ("RTX 4090", "重庆A区"),
            ],
        )

    @patch("watch.UserAgent")
    @patch("watch.query_post_json")
    def test_failed_or_missing_region_inventory_does_not_block_other_candidates(
        self, query_mock, user_agent_mock
    ):
        user_agent_mock.return_value.random = "test-agent"
        query_mock.side_effect = [
            requests.exceptions.SSLError("temporary failure"),
            {"data": [{"RTX 4090": {"idle_gpu_num": 1}}]},
        ]
        config = SimpleNamespace(
            Authorization="token",
            gpu_models=["RTX 3090", "RTX 4090"],
            gpu_regions=["内蒙B区", "重庆A区"],
        )

        with patch("builtins.print"):
            results = watch.watch_gpus(config)

        self.assertEqual(query_mock.call_count, 2)
        self.assertEqual(
            [(item["gpu"], item["region"]) for item in results],
            [("RTX 4090", "重庆A区")],
        )


class MainFlowTests(unittest.TestCase):
    def make_runtime_config(self, mode):
        return SimpleNamespace(
            mode=mode,
            gpu_models=["RTX 3090", "RTX 4090"],
            gpu_regions=["内蒙B区", "重庆A区"],
            gpu_num=1,
            image="example-image",
            private_image_uuid="",
            sendkey="",
        )

    @patch("main.sc_send", side_effect=RuntimeError("notification unavailable"))
    def test_notification_failure_is_reported_without_raising(self, sc_send_mock):
        config = self.make_runtime_config("monitor_only")
        config.sendkey = "SCT-test"

        with patch("builtins.print"):
            result = main.send_notification(config, "title", "content")

        self.assertEqual(result["status"], "failed")
        sc_send_mock.assert_called_once()

    @patch("main.send_notification")
    @patch("main.watch.watch_gpus")
    def test_monitor_only_aggregates_all_available_candidates(
        self, watch_mock, notify_mock
    ):
        available = [
            candidate("RTX 3090", "内蒙B区", 1),
            candidate("RTX 4090", "重庆A区", 2),
        ]
        watch_mock.return_value = available

        result = main.run(self.make_runtime_config("monitor_only"))

        self.assertEqual(result, 0)
        notified_candidates = notify_mock.call_args.kwargs["gpu_info"]
        self.assertEqual(notified_candidates, available)

    @patch("main.send_notification")
    @patch("main.gpu.buy_candidate")
    @patch("main.watch.watch_gpus")
    def test_purchase_falls_back_in_candidate_order_and_stops_after_success(
        self, watch_mock, buy_mock, notify_mock
    ):
        first = candidate("RTX 3090", "内蒙B区")
        second = candidate("RTX 4090", "重庆A区")
        watch_mock.return_value = [first, second]
        buy_mock.side_effect = [
            {"success": False, "fatal": False, "message": "库存被抢走"},
            {
                "success": True,
                "fatal": False,
                "message": "ok",
                "region_name": "重庆A区",
                "price": 1.5,
                "machine_id": "machine-2",
                "gpu_info": second,
            },
        ]

        result = main.run(self.make_runtime_config("monitor_and_buy"))

        self.assertEqual(result, 0)
        self.assertEqual(buy_mock.call_count, 2)
        self.assertEqual(
            [call.kwargs["candidate"] for call in buy_mock.call_args_list],
            [first, second],
        )
        notify_mock.assert_called_once()

    @patch("main.send_notification")
    @patch("main.gpu.buy_candidate")
    @patch("main.watch.watch_gpus")
    def test_uncertain_order_result_stops_without_trying_next_candidate(
        self, watch_mock, buy_mock, notify_mock
    ):
        first = candidate("RTX 3090", "内蒙B区")
        second = candidate("RTX 4090", "重庆A区")
        watch_mock.return_value = [first, second]
        buy_mock.return_value = {
            "success": False,
            "fatal": True,
            "message": "订单结果未知",
        }

        result = main.run(self.make_runtime_config("monitor_and_buy"))

        self.assertEqual(result, 1)
        buy_mock.assert_called_once()
        notify_mock.assert_not_called()

    @patch("main.send_notification")
    @patch("main.gpu.buy_candidate")
    @patch("main.time.sleep")
    @patch("main.watch.watch_gpus")
    def test_zero_idle_does_not_trigger_purchase(
        self, watch_mock, sleep_mock, buy_mock, notify_mock
    ):
        available = candidate("RTX 3090", "内蒙B区", 1)
        watch_mock.side_effect = [
            [candidate("RTX 3090", "内蒙B区", 0)],
            [available],
        ]
        buy_mock.return_value = {
            "success": True,
            "fatal": False,
            "message": "ok",
            "region_name": "内蒙B区",
            "price": 1.0,
            "machine_id": "machine-1",
            "gpu_info": available,
        }

        result = main.run(self.make_runtime_config("monitor_and_buy"))

        self.assertEqual(result, 0)
        sleep_mock.assert_called_once_with(main.POLL_INTERVAL_SECONDS)
        buy_mock.assert_called_once()


class BuyTests(unittest.TestCase):
    def make_config(self):
        return SimpleNamespace(Authorization="token")

    @patch("gpu.requests.post")
    @patch("gpu.get_machine_info")
    def test_successful_order_is_created_once(self, machine_mock, post_mock):
        machine_mock.return_value = [
            {"machine_id": "machine-1", "machine_price": 1500},
            {"machine_id": "machine-2", "machine_price": 1600},
        ]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": "Success"}
        post_mock.return_value = response

        result = gpu.buy_candidate(
            self.make_config(),
            candidate("RTX 3090", "内蒙B区"),
            gpu_num=1,
            image="example-image",
            private_image_uuid="",
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["price"], 1.5)
        post_mock.assert_called_once()

    @patch("gpu.requests.post")
    @patch("gpu.get_machine_info")
    def test_network_error_marks_order_result_as_fatal(self, machine_mock, post_mock):
        machine_mock.return_value = [
            {"machine_id": "machine-1", "machine_price": 1500},
        ]
        post_mock.side_effect = requests.exceptions.SSLError("connection closed")

        result = gpu.buy_candidate(
            self.make_config(),
            candidate("RTX 3090", "内蒙B区"),
            gpu_num=1,
            image="example-image",
            private_image_uuid="",
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["fatal"])
        post_mock.assert_called_once()

    @patch("gpu.requests.post")
    @patch("gpu.get_machine_info")
    def test_malformed_order_response_is_treated_as_uncertain(self, machine_mock, post_mock):
        machine_mock.return_value = [
            {"machine_id": "machine-1", "machine_price": 1500},
        ]
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = ["unexpected"]
        post_mock.return_value = response

        result = gpu.buy_candidate(
            self.make_config(),
            candidate("RTX 3090", "内蒙B区"),
            gpu_num=1,
            image="example-image",
            private_image_uuid="",
        )

        self.assertFalse(result["success"])
        self.assertTrue(result["fatal"])
        post_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
