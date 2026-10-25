import json
from pathlib import Path

from regions import DEFAULT_GPU_REGIONS, REGION_CATALOG, default_regions_for_models


def _ordered_string_list(value, field_name):
    if not isinstance(value, list) or not value:
        raise ValueError(f"配置项 {field_name} 必须是非空字符串数组")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"配置项 {field_name} 只能包含非空字符串")
    return list(dict.fromkeys(item.strip() for item in value))

class Config:
    def __init__(self, config_file='config.json'):
        config_path = Path(config_file)
        self.config_path = (
            config_path
            if config_path.is_absolute()
            else Path(__file__).parent / config_path
        )
        
        if not self.config_path.exists():
            raise FileNotFoundError(f"配置文件 {config_file} 不存在")
        
        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config_data = json.load(f)
        
        # 认证信息 - 允许为空或不存在
        self.Authorization = self.config_data.get('authorization', '')
        
        # 运行模式配置
        self.mode = self.config_data.get('mode', 'monitor_only')  # monitor_only 或 monitor_and_buy
        
        # GPU 配置
        gpu_config = self.config_data.get('gpu', {})
        if 'models' in gpu_config:
            self.gpu_models = _ordered_string_list(gpu_config['models'], 'gpu.models')
            if 'model' in gpu_config:
                print("配置提示: 同时存在 gpu.models 和 gpu.model，已优先使用 gpu.models")
        else:
            model = gpu_config.get('model')
            if not isinstance(model, str) or not model.strip():
                raise ValueError("请配置非空的 gpu.models，或使用兼容字段 gpu.model")
            self.gpu_models = [model.strip()]

        unknown_models = [
            model for model in self.gpu_models
            if model not in DEFAULT_GPU_REGIONS
        ]
        if unknown_models:
            raise ValueError(f"未知 GPU 型号: {', '.join(unknown_models)}")

        # 保留旧属性，方便已有的外部调用继续读取首选型号。
        self.gpu_model = self.gpu_models[0]

        self.gpu_num = gpu_config.get('num')
        if isinstance(self.gpu_num, bool) or not isinstance(self.gpu_num, int) or self.gpu_num < 1:
            raise ValueError("配置项 gpu.num 必须是大于等于 1 的整数")

        if 'regions' in gpu_config:
            self.gpu_regions = _ordered_string_list(gpu_config['regions'], 'gpu.regions')
            unknown_regions = [
                region for region in self.gpu_regions
                if region not in REGION_CATALOG
            ]
            if unknown_regions:
                raise ValueError(f"未知区域: {', '.join(unknown_regions)}")
            self.regions_explicit = True
        else:
            self.gpu_regions = default_regions_for_models(self.gpu_models)
            self.regions_explicit = False
        
        # 镜像配置
        self.private_image_uuid = self.config_data['image']['private_image_uuid']
        self.image = self.config_data['image']['autodl_image']

        # 通知配置
        self.sendkey = self.config_data.get('notification', {}).get('sendkey', '')

    def has_authorization(self):
        """检查是否有有效的 authorization"""
        return bool(self.Authorization and self.Authorization.strip())
    
    def update_authorization(self, authorization):
        """更新 authorization 并保存到配置文件"""
        self.Authorization = authorization
        self.config_data['authorization'] = authorization
        
        with open(self.config_path, 'w', encoding='utf-8') as f:
            json.dump(self.config_data, f, indent=2, ensure_ascii=False)
