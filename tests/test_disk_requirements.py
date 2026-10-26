import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import gpu
from config_loader import Config, default_config_data
from monitor_service import MonitorService


def runtime_config(mode="monitor_and_buy", minimum=400, expansion=0):
    return SimpleNamespace(
        Authorization="token",
        mode=mode,
        gpu_models=["RTX 3090"],
        gpu_regions=["内蒙B区"],
        gpu_num=1,
        image="image",
        private_image_uuid="",
        sendkey="",
        poll_interval_seconds=0,
        min_expandable_gb=minimum,
        expand_data_disk_gb=expansion,
    )


def candidate(idle=1):
    return {
        "gpu": "RTX 3090",
        "region": "内蒙B区",
        "region_sign_list": ["nm-B1", "nm-B2"],
        "idle_gpu_num": idle,
        "total_gpu_num": 10,
    }


def machine(machine_id="machine-1", expandable_gb=500):
    return {
        "machine_id": machine_id,
        "machine_price": 1500,
        "data_disk_bytes": 50 * gpu.GIB,
        "max_expandable_bytes": expandable_gb * gpu.GIB,
        "data_disk_daily_price_per_gb": 0.007,
    }


class DiskConfigTests(unittest.TestCase):
    def test_old_config_defaults_to_no_filter_and_no_expansion(self):
        data = default_config_data()
        data.pop("disk")
        config = Config.from_data(data)
        self.assertEqual(config.min_expandable_gb, 0)
        self.assertEqual(config.expand_data_disk_gb, 0)

    def test_filter_and_expansion_are_independent(self):
        data = default_config_data()
        data["disk"] = {
            "min_expandable_gb": 400,
            "expand_data_disk_gb": 200,
        }
        config = Config.from_data(data)
        self.assertEqual(config.min_expandable_gb, 400)
        self.assertEqual(config.expand_data_disk_gb, 200)

    def test_invalid_disk_values_fail_fast(self):
        for value in (-1, True, 1.5, 102401):
            with self.subTest(value=value):
                data = copy.deepcopy(default_config_data())
                data["disk"]["min_expandable_gb"] = value
                with self.assertRaises(ValueError):
                    Config.from_data(data)


class MachineFilterTests(unittest.TestCase):
    @patch("gpu.UserAgent")
    @patch("gpu.query_post_json")
    def test_filters_by_max_expandable_bytes_and_excludes_missing_field(
        self, query_mock, user_agent_mock
    ):
        user_agent_mock.return_value.random = "agent"
        query_mock.return_value = {
            "data": {
                "list": [
                    {
                        "machine_id": "small",
                        "payg_price": 1000,
                        "gpu_idle_num": 1,
                        "max_data_disk_expand_size": 399 * gpu.GIB,
                    },
                    {
                        "machine_id": "missing",
                        "payg_price": 1000,
                        "gpu_idle_num": 1,
                    },
                    {
                        "machine_id": "eligible",
                        "payg_price": 1500,
                        "gpu_idle_num": 1,
                        "max_instance_disk_size": 50 * gpu.GIB,
                        "max_data_disk_expand_size": 400 * gpu.GIB,
                        "data_disk_expand_price": {"daily_price": 7},
                    },
                ],
                "max_page": 1,
            }
        }
        result = gpu.get_machine_info(
            runtime_config(minimum=400),
            "RTX 3090",
            ["nm-B1", "nm-B2"],
            1,
        )
        self.assertEqual([item["machine_id"] for item in result], ["eligible"])
        self.assertEqual(result[0]["data_disk_daily_price_per_gb"], 0.007)
        self.assertEqual(query_mock.call_args.kwargs["data"]["page_size"], 100)

    @patch("gpu.UserAgent")
    @patch("gpu.query_post_json")
    def test_requested_expansion_also_sets_minimum_host_capacity(
        self, query_mock, user_agent_mock
    ):
        user_agent_mock.return_value.random = "agent"
        query_mock.return_value = {
            "data": {
                "list": [
                    {
                        "machine_id": "too-small",
                        "payg_price": 1000,
                        "gpu_idle_num": 1,
                        "max_data_disk_expand_size": 500 * gpu.GIB,
                    }
                ],
                "max_page": 1,
            }
        }
        result = gpu.get_machine_info(
            runtime_config(minimum=100, expansion=600),
            "RTX 3090",
            ["nm-B1"],
            1,
        )
        self.assertEqual(result, [])

    @patch("gpu.UserAgent")
    @patch("gpu.query_post_json")
    def test_paginates_machine_results(self, query_mock, user_agent_mock):
        user_agent_mock.return_value.random = "agent"
        query_mock.side_effect = [
            {"data": {"list": [], "max_page": 2}},
            {
                "data": {
                    "list": [
                        {
                            "machine_id": "page-2",
                            "payg_price": 1000,
                            "gpu_idle_num": 1,
                            "max_data_disk_expand_size": 500 * gpu.GIB,
                        }
                    ],
                    "max_page": 2,
                }
            },
        ]
        result = gpu.get_machine_info(
            runtime_config(), "RTX 3090", ["nm-B1"], 1
        )
        self.assertEqual([item["machine_id"] for item in result], ["page-2"])
        self.assertEqual(query_mock.call_count, 2)


class ExpandedPurchaseTests(unittest.TestCase):
    @patch("gpu.requests.post")
    def test_order_uses_byte_expansion_in_both_payload_sections(self, post_mock):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"code": "Success"}
        post_mock.return_value = response

        result = gpu.buy_candidate(
            runtime_config(minimum=400, expansion=200),
            candidate(),
            gpu_num=1,
            image="image",
            private_image_uuid="",
            machine_info=[machine(expandable_gb=400)],
        )

        payload = post_mock.call_args.kwargs["json"]
        expected = 200 * gpu.GIB
        self.assertEqual(payload["instance_info"]["expand_data_disk"], expected)
        self.assertEqual(payload["price_info"]["expand_data_disk"], expected)
        self.assertEqual(result["expand_data_disk_gb"], 200)
        self.assertAlmostEqual(result["data_disk_daily_price"], 1.4)

    @patch("gpu.requests.post")
    def test_defensive_check_never_orders_machine_below_expansion(self, post_mock):
        result = gpu.buy_candidate(
            runtime_config(minimum=0, expansion=600),
            candidate(),
            gpu_num=1,
            image="image",
            private_image_uuid="",
            machine_info=[machine(expandable_gb=500)],
        )
        self.assertFalse(result["success"])
        post_mock.assert_not_called()


class DiskMonitorTests(unittest.TestCase):
    def test_monitor_only_notifies_only_after_machine_disk_qualification(self):
        events = []
        notify = Mock(return_value={"status": "skipped"})
        machine_query = Mock(return_value=[machine()])
        service = MonitorService(
            runtime_config(mode="monitor_only"),
            watch_fn=lambda config, event_callback=None: [candidate()],
            machine_fn=machine_query,
            notify_fn=notify,
            event_callback=events.append,
        )
        self.assertEqual(service.run(), 0)
        machine_query.assert_called_once()
        notified = notify.call_args.kwargs["gpu_info"]
        self.assertEqual(notified[0]["eligible_machine_count"], 1)
        self.assertEqual(notified[0]["max_expandable_gb"], 500)
        self.assertTrue(any(e["type"] == "eligible_inventory" for e in events))

    def test_monitor_only_does_not_notify_when_disk_requirement_fails(self):
        class StopAfterWait:
            def is_set(self):
                return False

            def wait(self, seconds):
                return True

            def set(self):
                pass

        notify = Mock(return_value={"status": "skipped"})
        events = []
        result = MonitorService(
            runtime_config(mode="monitor_only"),
            stop_event=StopAfterWait(),
            watch_fn=lambda config, event_callback=None: [candidate()],
            machine_fn=Mock(return_value=[]),
            notify_fn=notify,
            event_callback=events.append,
        ).run()
        self.assertEqual(result, 0)
        notify.assert_not_called()
        self.assertTrue(any(
            "磁盘要求" in event.get("message", "")
            for event in events
            if event["type"] == "log"
        ))

    def test_purchase_reuses_prequalified_machine_list(self):
        buy = Mock(return_value={
            "success": True,
            "fatal": False,
            "message": "ok",
            "region_name": "内蒙B区",
            "price": 1.5,
            "machine_id": "machine-1",
            "gpu_info": candidate(),
            "expand_data_disk_gb": 0,
            "data_disk_daily_price": None,
        })
        selected = [machine()]
        service = MonitorService(
            runtime_config(),
            watch_fn=lambda config, event_callback=None: [candidate()],
            machine_fn=Mock(return_value=selected),
            buy_fn=buy,
            notify_fn=Mock(return_value={"status": "skipped"}),
        )
        self.assertEqual(service.run(), 0)
        self.assertEqual(buy.call_args.kwargs["machine_info"], selected)


if __name__ == "__main__":
    unittest.main()
