import json
import threading
import time

import gpu
import watch
from serverchan_sdk import sc_send


def get_available_candidates(gpu_info, required_num):
    """保持原顺序，筛选单一区域空闲卡数满足要求的候选。"""
    return [
        candidate
        for candidate in gpu_info
        if int(candidate.get("idle_gpu_num", 0)) >= required_num
    ]


def format_available_summary(candidates):
    return "\n".join(
        f"- {candidate['gpu']} / {candidate['region']}: "
        f"空闲 {candidate.get('idle_gpu_num', 0)} 张"
        for candidate in candidates
    )


def send_notification(config, title, content, gpu_info=None, sender=None):
    """发送 Server酱通知；失败不会改变监控或购买结果。"""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    desp = f"\n时间: {timestamp}\n内容: {content}\n"
    if gpu_info:
        desp += (
            "\nGPU信息:\n"
            + json.dumps(gpu_info, indent=2, ensure_ascii=False)
        )

    if config.sendkey and config.sendkey != "...":
        options = {"tags": "GPU监控|autogpu"}
        try:
            return (sender or sc_send)(config.sendkey, title, desp, options)
        except Exception as exc:  # 通知 SDK 的异常类型不稳定
            return {"status": "failed", "message": str(exc)}
    return {"status": "skipped", "message": "未配置sendkey"}


class MonitorService:
    """与界面无关、可安全停止的监控及购买循环。"""

    def __init__(
        self,
        config,
        *,
        stop_event=None,
        event_callback=None,
        watch_fn=None,
        buy_fn=None,
        notify_fn=None,
    ):
        self.config = config
        self.stop_event = stop_event or threading.Event()
        self.event_callback = event_callback
        self.watch_fn = watch_fn or watch.watch_gpus
        self.buy_fn = buy_fn or gpu.buy_candidate
        self.notify_fn = notify_fn or send_notification
        self.poll_interval_seconds = getattr(config, "poll_interval_seconds", 10)
        self._region_results = 0
        self._region_errors = 0

    def stop(self):
        self.stop_event.set()

    def _emit(self, event_type, **payload):
        if self.event_callback is not None:
            self.event_callback({"type": event_type, **payload})

    def _log(self, message, level="info"):
        self._emit("log", level=level, message=message)

    def _watch_event(self, event):
        if event.get("type") == "region_result":
            self._region_results += 1
        elif event.get("type") == "region_error":
            self._region_errors += 1
        self._emit("watch", event=event)

    def run(self):
        if self.config.mode not in {"monitor_only", "monitor_and_buy"}:
            self._log(f"未知的运行模式: {self.config.mode}", "error")
            self._emit("finished", code=2, reason="invalid_config")
            return 2

        mode_name = "仅监控" if self.config.mode == "monitor_only" else "监控并购买"
        self._emit("state", state="running", message=f"{mode_name}已启动")
        self._log(f"运行模式: {mode_name}")
        self._log(f"目标 GPU: {', '.join(self.config.gpu_models)}")
        self._log(f"目标区域: {', '.join(self.config.gpu_regions)}")

        while not self.stop_event.is_set():
            self._region_results = 0
            self._region_errors = 0
            self._emit("state", state="querying", message="正在查询库存")
            try:
                gpu_info = self.watch_fn(
                    self.config,
                    event_callback=self._watch_event,
                )
            except Exception as exc:
                self._log(f"库存查询发生未处理错误: {exc}", "error")
                self._emit("state", state="error", message="库存查询失败")
                if self.stop_event.wait(self.poll_interval_seconds):
                    break
                continue

            queried_at = time.strftime("%Y-%m-%d %H:%M:%S")
            self._emit("inventory", candidates=gpu_info, queried_at=queried_at)
            if self.stop_event.is_set():
                break

            if self._region_errors and not self._region_results:
                self._log("所有区域本轮查询失败，将在下一轮重试", "error")
                self._emit("state", state="error", message="所有区域查询失败")
            else:
                available = get_available_candidates(gpu_info, self.config.gpu_num)
                if available:
                    summary = format_available_summary(available)
                    self._log(f"发现可用 GPU:\n{summary}", "success")
                    if self.config.mode == "monitor_only":
                        response = self.notify_fn(
                            self.config,
                            title="GPU可用通知",
                            content=summary,
                            gpu_info=available,
                        )
                        if response.get("status") == "failed":
                            self._log(
                                f"通知发送失败: {response.get('message', '未知错误')}",
                                "warning",
                            )
                        self._emit(
                            "finished",
                            code=0,
                            reason="available",
                            candidates=available,
                        )
                        return 0

                    purchase_result = self._try_purchase(available)
                    if purchase_result is not None:
                        return purchase_result
                else:
                    self._log("暂无满足数量要求的 GPU")

            self._emit(
                "state",
                state="waiting",
                message=f"{self.poll_interval_seconds} 秒后再次查询",
                seconds=self.poll_interval_seconds,
            )
            if self.stop_event.wait(self.poll_interval_seconds):
                break

        self._log("监控已停止")
        self._emit("finished", code=0, reason="stopped")
        return 0

    def _try_purchase(self, available):
        for candidate in available:
            if self.stop_event.is_set():
                return None
            self._log(f"尝试购买 {candidate['gpu']} / {candidate['region']}")
            result = self.buy_fn(
                config=self.config,
                candidate=candidate,
                gpu_num=self.config.gpu_num,
                image=self.config.image,
                private_image_uuid=self.config.private_image_uuid,
            )
            if result["success"]:
                content = (
                    f"成功购买 {self.config.gpu_num} 张 {candidate['gpu']}\n"
                    f"区域: {result['region_name']}\n"
                    f"价格: {result['price']}元/小时\n"
                    f"机器ID: {result['machine_id']}"
                )
                response = self.notify_fn(
                    self.config,
                    title=f"GPU购买成功 - {candidate['gpu']}",
                    content=content,
                    gpu_info=result["gpu_info"],
                )
                self._log(content, "success")
                if response.get("status") == "failed":
                    self._log(
                        f"购买成功，但通知发送失败: {response.get('message', '未知错误')}",
                        "warning",
                    )
                self._emit("purchase_success", result=result)
                self._emit("finished", code=0, reason="purchased", result=result)
                return 0

            self._log(f"购买失败: {result['message']}", "warning")
            if result.get("fatal"):
                self._log(
                    "订单结果可能不确定，请先到 AutoDL 控制台核对实例列表",
                    "error",
                )
                self._emit("order_uncertain", result=result)
                self._emit("finished", code=1, reason="order_uncertain", result=result)
                return 1
        return None
