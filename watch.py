import requests
from fake_useragent import UserAgent
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

gpu2region = {
    'RTX 5090': [["west-B", "west-C"]],
    'RTX 4090': [['nm-B1', 'nm-B2'], ["cq-A1"], ["west-B", "west-C"], ["bj-B1"]],
    'RTX 4090D': [["cq-A1"], ["west-B", "west-C"]],
    'RTX 3090': [['nm-B1', 'nm-B2']],
    # 'RTX 3090': [['nm-B1', 'nm-B2'], ["cq-A1"], ["west-B", "west-C"], ["beijing-A", "beijing-B", "beijing-D", "beijing-E"], ["yz-A1"]],
    'RTX 3080*2': [['nm-B1', 'nm-B2'], ["west-B", "west-C"]],
    'RTX 3080 Ti': [["west-B", "west-C"]],
    'RTX 3080': [["beijing-A", "beijing-B", "beijing-D", "beijing-E"]],
    'RTX 3060': [["west-B", "west-C"]],
    'RTX 2080 Ti *2': [['nm-B1', 'nm-B2'], ["cq-A1"]],
    'RTX 2080 Ti': [["beijing-A", "beijing-B", "beijing-D", "beijing-E"]],
    'GTX 1080 Ti': [["west-B", "west-C"]],
    'vGPU-32GB': [['nm-B1', 'nm-B2'], ["west-B", "west-C"]],
    'vGPU-48GB': [["west-B", "west-C"]],
    'V100-32GB': [["foshan-A"], ["beijing-C"]],
    'V100-SXM2-32GB': [["beijing-A", "beijing-B", "beijing-D", "beijing-E"]],
    'A100-PCIE-40GB': [["foshan-A"]],
    'A100-SXM4-80GB': [["neimeng-A", "neimeng-C", "neimeng-D"]],
    'L20': [["bj-C1"]],
    'L40': [["west-B", "west-C"]],
    'A40': [["neimeng-A", "neimeng-C", "neimeng-D"]],
    'H800': [["west-B", "west-C"]],
    'A800-80GB': [["nm-A1"]],
    'H20-NVLink': [["beijing-A", "beijing-B", "beijing-D", "beijing-E"], ["bj-C1"]],
    'TITAN Xp': [['nm-B1', 'nm-B2']],
    'Tesla T4': [["foshan-A"]],
    'RTX A4000': [["west-B", "west-C"]],
    'CPU': [["cq-A1"], ["west-B", "west-C"]],
    'CPU-close-HT': [["west-B", "west-C"]],
    '910B2x鲲鹏920': [["gd-A1"]]
}


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


def watch_gpu(config, gpu):
    url = 'https://www.autodl.com/api/v1/machine/region/gpu_type'
    headers = {
        'User-Agent': UserAgent().random,
        'Authorization': config.Authorization
    }
    gpu_info = {}
    for region in gpu2region[gpu]:
        try:
            response = query_post_json(
                url,
                headers=headers,
                data={'region_sign_list': region},
            )
        except (requests.RequestException, ValueError) as exc:
            print(f"查询区域 {region} 失败，稍后重试: {exc}")
            continue

        if not isinstance(response.get('data'), list):
            print(f"查询区域 {region} 返回异常: {response.get('msg', response)}")
            continue
 
        for v in response['data']:
            if list(v.keys())[0] == gpu:
                gpu_info[str(region)] = {**{'gpu': gpu}, **v[list(v.keys())[0]]}
     
                break
            else:
                continue
    return gpu_info

