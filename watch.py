import requests
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from regions import DEFAULT_GPU_REGIONS, REGION_CATALOG


# GPU 库存和机器列表查询是只读操作，网络抖动时可以安全重试。
_query_session = requests.Session()
_query_session.mount(
    'https://',
    HTTPAdapter(
        max_retries=Retry(
            total=3,
            connect=3,
            read=3,
            status=3,
            backoff_factor=1,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({'POST'}),
        )
    ),
)


def query_post_json(url, *, headers, data):
    """发送可安全重试的只读 POST 查询。"""
    response = _query_session.post(
        url,
        headers=headers,
        json=data,
        timeout=(10, 20),
    )
    response.raise_for_status()
    return response.json()


def watch_gpus(config, models=None, regions=None, event_callback=None):
    """每个区域查询一次，并按型号优先、区域次优先返回库存记录。"""
    url = 'https://www.autodl.com/api/v1/machine/region/gpu_type'
    headers = {
        'User-Agent': UserAgent().random,
        'Authorization': config.Authorization
    }
    models = list(models or config.gpu_models)
    regions = list(regions or config.gpu_regions)
    region_inventory = {}

    def emit(event_type, **payload):
        if event_callback is not None:
            event_callback({"type": event_type, **payload})

    for region_name in regions:
        region_sign_list = REGION_CATALOG[region_name]
        emit("region_query_started", region=region_name)
        try:
            response = query_post_json(
                url,
                headers=headers,
                data={'region_sign_list': region_sign_list},
            )
        except (requests.RequestException, ValueError) as exc:
            print(f"查询区域 {region_name} 失败，稍后重试: {exc}")
            emit("region_error", region=region_name, message=str(exc))
            continue

        if not isinstance(response, dict) or not isinstance(response.get('data'), list):
            message = response.get('msg', response) if isinstance(response, dict) else response
            print(f"查询区域 {region_name} 返回异常: {message}")
            emit("region_error", region=region_name, message=str(message))
            continue

        inventory = {}
        for item in response['data']:
            if not isinstance(item, dict):
                continue
            for model, details in item.items():
                if model in models and isinstance(details, dict):
                    inventory[model] = details
        region_inventory[region_name] = inventory
        emit(
            "region_result",
            region=region_name,
            inventory=inventory,
        )

    candidates = []
    for model in models:
        for region_name in regions:
            details = region_inventory.get(region_name, {}).get(model)
            if details is None:
                continue
            candidates.append({
                **details,
                'gpu': model,
                'region': region_name,
                'region_sign_list': list(REGION_CATALOG[region_name]),
            })
    return candidates


def watch_gpu(config, gpu):
    """兼容旧调用：监控单个型号并按区域名返回结果。"""
    regions = getattr(config, 'gpu_regions', DEFAULT_GPU_REGIONS[gpu])
    return {
        candidate['region']: candidate
        for candidate in watch_gpus(config, models=[gpu], regions=regions)
    }
