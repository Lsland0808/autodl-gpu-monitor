import json
import time

import gpu
import watch
from config_loader import Config
from login_helper import login_and_update_config
from serverchan_sdk import sc_send


POLL_INTERVAL_SECONDS = 10


def get_available_candidates(gpu_info, required_num):
    """保持原顺序，筛选单一区域空闲卡数满足要求的候选。"""
    return [
        candidate for candidate in gpu_info
        if int(candidate.get('idle_gpu_num', 0)) >= required_num
    ]


def format_available_summary(candidates):
    return "\n".join(
        f"- {candidate['gpu']} / {candidate['region']}: "
        f"空闲 {candidate.get('idle_gpu_num', 0)} 张"
        for candidate in candidates
    )


def send_notification(config, title, content, gpu_info=None):
    """发送 Server酱 通知。"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    desp = f"\n时间: {timestamp}\n内容: {content}\n"

    if gpu_info:
        desp += f"\nGPU信息:\n{json.dumps(gpu_info, indent=2, ensure_ascii=False)}"

    if config.sendkey and config.sendkey != "...":
        options = {"tags": "GPU监控|autogpu"}
        try:
            return sc_send(config.sendkey, title, desp, options)
        except Exception as exc:
            # 通知失败不能让已成功下单的主流程异常退出，否则用户重跑后可能重复下单。
            print(f"通知发送失败: {exc}")
            return {"status": "failed", "message": str(exc)}

    print("未配置sendkey，跳过通知发送")
    return {"status": "skipped", "message": "未配置sendkey"}


def run(config):
    if config.mode not in {"monitor_only", "monitor_and_buy"}:
        print(f"未知的运行模式: {config.mode}")
        print("支持的模式: monitor_only, monitor_and_buy")
        return 2

    mode_name = "仅监控" if config.mode == "monitor_only" else "监控并购买"
    print(f"运行模式: {mode_name}")
    print(f"目标GPU（按优先级）: {', '.join(config.gpu_models)}")
    print(f"目标区域（按优先级）: {', '.join(config.gpu_regions)}")
    print("开始监控GPU...")

    while True:
        gpu_info = watch.watch_gpus(config)
        print("GPU监控结果:")
        print(json.dumps(gpu_info, indent=2, ensure_ascii=False))

        available = get_available_candidates(gpu_info, config.gpu_num)
        if not available:
            print("暂无可用GPU，继续监控...")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        summary = format_available_summary(available)
        print(f"发现可用GPU:\n{summary}")

        if config.mode == "monitor_only":
            response = send_notification(
                config,
                title="GPU可用通知",
                content=summary,
                gpu_info=available,
            )
            print("通知发送结果:")
            print(response)
            return 0

        for candidate in available:
            result = gpu.buy_candidate(
                config=config,
                candidate=candidate,
                gpu_num=config.gpu_num,
                image=config.image,
                private_image_uuid=config.private_image_uuid,
            )

            if result['success']:
                response = send_notification(
                    config,
                    title=f"GPU购买成功 - {candidate['gpu']}",
                    content=(
                        f"成功购买 {config.gpu_num} 张 {candidate['gpu']}\n"
                        f"区域: {result['region_name']}\n"
                        f"价格: {result['price']}元/小时\n"
                        f"机器ID: {result['machine_id']}"
                    ),
                    gpu_info=result['gpu_info'],
                )
                print("购买成功通知发送结果:")
                print(response)
                return 0

            print(f"购买失败: {result['message']}")
            if result.get('fatal'):
                print("订单结果可能不确定，已停止自动购买。请先到 AutoDL 控制台核对实例列表。")
                return 1

        print("本轮可用候选均购买失败，继续监控...")
        time.sleep(POLL_INTERVAL_SECONDS)


def main():
    try:
        config = Config()
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"配置错误: {exc}")
        return 2

    if not config.has_authorization():
        print("检测到配置文件中没有 authorization，开始登录...")
        token = login_and_update_config()
        config.Authorization = token
        print("登录完成，继续执行后续操作...")

    print("检测到有效的 authorization，开始执行主要功能...")
    return run(config)


if __name__ == "__main__":
    raise SystemExit(main())
