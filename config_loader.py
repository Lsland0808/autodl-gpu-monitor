import copy
import json
from pathlib import Path

from regions import DEFAULT_GPU_REGIONS, REGION_CATALOG, default_regions_for_models


DEFAULT_IMAGE = (
    "hub.kce.ksyun.com/autodl-image/torch:"
    "cuda12.4-cudnn-devel-ubuntu22.04-py312-torch2.5.1"
)
DEFAULT_POLL_INTERVAL_SECONDS = 10
MIN_POLL_INTERVAL_SECONDS = 5
MAX_POLL_INTERVAL_SECONDS = 60
VALID_MODES = {"monitor_only", "monitor_and_buy"}
MAX_DISK_GB = 102400


def default_config_data():
    """返回一份可直接保存的新配置，调用方可以安全修改。"""
    return {
        "authorization": "",
        "mode": "monitor_only",
        "gpu": {
            "models": ["RTX 3090", "RTX 4090", "RTX 4090D"],
            "num": 1,
            "regions": ["内蒙B区", "重庆A区"],
        },
        "monitor": {"interval_seconds": DEFAULT_POLL_INTERVAL_SECONDS},
        "disk": {
            "min_expandable_gb": 0,
            "expand_data_disk_gb": 0,
        },
        "image": {
            "private_image_uuid": "",
            "autodl_image": DEFAULT_IMAGE,
        },
        "notification": {"sendkey": ""},
    }


def _ordered_string_list(value, field_name):
    if not isinstance(value, list) or not value:
        raise ValueError(f"配置项 {field_name} 必须是非空字符串数组")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"配置项 {field_name} 只能包含非空字符串")
    return list(dict.fromkeys(item.strip() for item in value))


class Config:
    def __init__(self, config_file="config.json"):
        config_path = Path(config_file)
        self.config_path = (
            config_path
            if config_path.is_absolute()
            else Path(__file__).parent / config_path
        )
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件 {config_file} 不存在")

        with self.config_path.open("r", encoding="utf-8") as file:
            data = json.load(file)
        self._apply(data)

    @classmethod
    def from_data(cls, data, config_file="config.json"):
        """校验内存中的配置，并返回尚未写盘的 Config。"""
        instance = cls.__new__(cls)
        config_path = Path(config_file)
        instance.config_path = (
            config_path
            if config_path.is_absolute()
            else Path(__file__).parent / config_path
        )
        instance._apply(data)
        return instance

    @classmethod
    def create_default(cls, config_file="config.json"):
        return cls.from_data(default_config_data(), config_file)

    @classmethod
    def validate_and_save(cls, data, config_file="config.json"):
        """校验整份配置后原子式地由 Config 统一写盘。"""
        config = cls.from_data(data, config_file)
        config.save()
        return config

    def _apply(self, data):
        if not isinstance(data, dict):
            raise TypeError("配置文件根节点必须是 JSON 对象")
        self.config_data = copy.deepcopy(data)

        authorization = self.config_data.get("authorization", "")
        if authorization is None:
            authorization = ""
        if not isinstance(authorization, str):
            raise TypeError("配置项 authorization 必须是字符串")
        self.Authorization = authorization.strip()

        self.mode = self.config_data.get("mode", "monitor_only")
        if self.mode not in VALID_MODES:
            raise ValueError("配置项 mode 必须是 monitor_only 或 monitor_and_buy")

        gpu_config = self.config_data.get("gpu", {})
        if not isinstance(gpu_config, dict):
            raise TypeError("配置项 gpu 必须是对象")
        if "models" in gpu_config:
            self.gpu_models = _ordered_string_list(
                gpu_config["models"], "gpu.models"
            )
            if "model" in gpu_config:
                print("配置提示: 同时存在 gpu.models 和 gpu.model，已优先使用 gpu.models")
        else:
            model = gpu_config.get("model")
            if not isinstance(model, str) or not model.strip():
                raise ValueError("请配置非空的 gpu.models，或使用兼容字段 gpu.model")
            self.gpu_models = [model.strip()]

        unknown_models = [
            model for model in self.gpu_models if model not in DEFAULT_GPU_REGIONS
        ]
        if unknown_models:
            raise ValueError(f"未知 GPU 型号: {', '.join(unknown_models)}")
        self.gpu_model = self.gpu_models[0]

        self.gpu_num = gpu_config.get("num")
        if (
            isinstance(self.gpu_num, bool)
            or not isinstance(self.gpu_num, int)
            or self.gpu_num < 1
        ):
            raise ValueError("配置项 gpu.num 必须是大于等于 1 的整数")

        if "regions" in gpu_config:
            self.gpu_regions = _ordered_string_list(
                gpu_config["regions"], "gpu.regions"
            )
            unknown_regions = [
                region for region in self.gpu_regions if region not in REGION_CATALOG
            ]
            if unknown_regions:
                raise ValueError(f"未知区域: {', '.join(unknown_regions)}")
            self.regions_explicit = True
        else:
            self.gpu_regions = default_regions_for_models(self.gpu_models)
            self.regions_explicit = False

        monitor_config = self.config_data.get("monitor", {})
        if not isinstance(monitor_config, dict):
            raise TypeError("配置项 monitor 必须是对象")
        self.poll_interval_seconds = monitor_config.get(
            "interval_seconds", DEFAULT_POLL_INTERVAL_SECONDS
        )
        if (
            isinstance(self.poll_interval_seconds, bool)
            or not isinstance(self.poll_interval_seconds, int)
            or not MIN_POLL_INTERVAL_SECONDS
            <= self.poll_interval_seconds
            <= MAX_POLL_INTERVAL_SECONDS
        ):
            raise ValueError(
                "配置项 monitor.interval_seconds 必须是 5 到 60 之间的整数"
            )

        disk_config = self.config_data.get("disk", {})
        if not isinstance(disk_config, dict):
            raise TypeError("配置项 disk 必须是对象")
        self.min_expandable_gb = self._disk_size_gb(
            disk_config.get("min_expandable_gb", 0),
            "disk.min_expandable_gb",
        )
        self.expand_data_disk_gb = self._disk_size_gb(
            disk_config.get("expand_data_disk_gb", 0),
            "disk.expand_data_disk_gb",
        )

        image_config = self.config_data.get("image")
        if not isinstance(image_config, dict):
            raise TypeError("配置项 image 必须是对象")
        self.private_image_uuid = image_config.get("private_image_uuid", "")
        self.image = image_config.get("autodl_image", "")
        if not isinstance(self.private_image_uuid, str):
            raise TypeError("配置项 image.private_image_uuid 必须是字符串")
        if not isinstance(self.image, str):
            raise TypeError("配置项 image.autodl_image 必须是字符串")

        notification = self.config_data.get("notification", {})
        if not isinstance(notification, dict):
            raise TypeError("配置项 notification 必须是对象")
        self.sendkey = notification.get("sendkey", "")
        if not isinstance(self.sendkey, str):
            raise TypeError("配置项 notification.sendkey 必须是字符串")

        # 规范化后再保存，自动迁移旧配置并补齐默认字段。
        self.config_data.update(
            {
                "authorization": self.Authorization,
                "mode": self.mode,
                "gpu": {
                    "models": list(self.gpu_models),
                    "num": self.gpu_num,
                    "regions": list(self.gpu_regions),
                },
                "monitor": {"interval_seconds": self.poll_interval_seconds},
                "disk": {
                    "min_expandable_gb": self.min_expandable_gb,
                    "expand_data_disk_gb": self.expand_data_disk_gb,
                },
                "image": {
                    "private_image_uuid": self.private_image_uuid,
                    "autodl_image": self.image,
                },
                "notification": {"sendkey": self.sendkey},
            }
        )

    @staticmethod
    def _disk_size_gb(value, field_name):
        if (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= MAX_DISK_GB
        ):
            raise ValueError(f"配置项 {field_name} 必须是 0 到 {MAX_DISK_GB} 之间的整数")
        return value

    def to_dict(self):
        return copy.deepcopy(self.config_data)

    def save(self):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(self.config_data, file, indent=2, ensure_ascii=False)
            file.write("\n")
        temporary_path.replace(self.config_path)

    def has_authorization(self):
        return bool(self.Authorization)

    def update_authorization(self, authorization):
        if not isinstance(authorization, str) or not authorization.strip():
            raise ValueError("authorization 不能为空")
        self.Authorization = authorization.strip()
        self.config_data["authorization"] = self.Authorization
        self.save()

    def clear_authorization(self):
        self.Authorization = ""
        self.config_data["authorization"] = ""
        self.save()
