"""AutoDL 区域目录和各 GPU 型号的默认监控区域。"""


REGION_CATALOG = {
    "内蒙B区": ["nm-B1", "nm-B2"],
    "重庆A区": ["cq-A1"],
    "西北B区": ["west-B", "west-C"],
    "北京B区": ["bj-B1"],
    "北京A区": ["beijing-A", "beijing-B", "beijing-D", "beijing-E"],
    "佛山区": ["foshan-A"],
    "内蒙A区": ["neimeng-A", "neimeng-C", "neimeng-D"],
    "L20专区": ["bj-C1"],
    "3090专区": ["yz-A1"],
    "V100专区": ["beijing-C"],
    "A800专区": ["nm-A1"],
    "华为昇腾专区": ["gd-A1"],
}


DEFAULT_GPU_REGIONS = {
    "RTX 5090": ["西北B区"],
    "RTX 4090": ["内蒙B区", "重庆A区", "西北B区", "北京B区"],
    "RTX 4090D": ["重庆A区", "西北B区"],
    "RTX 3090": ["内蒙B区"],
    "RTX 3080*2": ["内蒙B区", "西北B区"],
    "RTX 3080 Ti": ["西北B区"],
    "RTX 3080": ["北京A区"],
    "RTX 3060": ["西北B区"],
    "RTX 2080 Ti *2": ["内蒙B区", "重庆A区"],
    "RTX 2080 Ti": ["北京A区"],
    "GTX 1080 Ti": ["西北B区"],
    "vGPU-32GB": ["内蒙B区", "西北B区"],
    "vGPU-48GB": ["西北B区"],
    "V100-32GB": ["佛山区", "V100专区"],
    "V100-SXM2-32GB": ["北京A区"],
    "A100-PCIE-40GB": ["佛山区"],
    "A100-SXM4-80GB": ["内蒙A区"],
    "L20": ["L20专区"],
    "L40": ["西北B区"],
    "A40": ["内蒙A区"],
    "H800": ["西北B区"],
    "A800-80GB": ["A800专区"],
    "H20-NVLink": ["北京A区", "L20专区"],
    "TITAN Xp": ["内蒙B区"],
    "Tesla T4": ["佛山区"],
    "RTX A4000": ["西北B区"],
    "CPU": ["重庆A区", "西北B区"],
    "CPU-close-HT": ["西北B区"],
    "910B2x鲲鹏920": ["华为昇腾专区"],
}


def unique_in_order(values):
    """去重且保持原始顺序。"""
    return list(dict.fromkeys(values))


def default_regions_for_models(models):
    """合并多个 GPU 型号的默认区域并保持型号/区域优先级。"""
    return unique_in_order(
        region
        for model in models
        for region in DEFAULT_GPU_REGIONS[model]
    )
