import json

import gpu
import watch
from serverchan_sdk import sc_send
from config_loader import Config
from login_helper import LoginError, login_and_update_config
from monitor_service import (
    MonitorService,
    format_available_summary,
    get_available_candidates,
    send_notification as _send_notification,
)


def send_notification(config, title, content, gpu_info=None):
    """兼容原有公开函数，并允许调用方替换 main.sc_send。"""
    result = _send_notification(
        config,
        title,
        content,
        gpu_info,
        sender=sc_send,
    )
    if result.get("status") == "failed":
        print(f"通知发送失败: {result.get('message', '未知错误')}")
    elif result.get("status") == "skipped":
        print("未配置sendkey，跳过通知发送")
    return result


def _print_event(event):
    event_type = event.get("type")
    if event_type == "log":
        print(event["message"])
    elif event_type == "inventory":
        print("GPU监控结果:")
        print(json.dumps(event["candidates"], indent=2, ensure_ascii=False))
    elif event_type == "order_uncertain":
        print("订单结果可能不确定，已停止自动购买。请先到 AutoDL 控制台核对实例列表。")


def run(config):
    """保留 CLI 公共入口，并与 GUI 共用 MonitorService。"""
    service = MonitorService(
        config,
        event_callback=_print_event,
        watch_fn=watch.watch_gpus,
        buy_fn=gpu.buy_candidate,
        notify_fn=send_notification,
    )
    return service.run()


def main():
    try:
        config = Config()
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"配置错误: {exc}")
        return 2

    if not config.has_authorization():
        print("检测到配置文件中没有 authorization，开始登录...")
        try:
            token = login_and_update_config(config=config)
        except LoginError as exc:
            print(f"登录失败: {exc}")
            return 1
        config.Authorization = token
        print("登录完成，继续执行后续操作...")

    print("检测到有效的 authorization，开始执行主要功能...")
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
