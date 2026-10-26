import requests
from fake_useragent import UserAgent

from watch import query_post_json


GIB = 1024 ** 3
MACHINE_PAGE_SIZE = 100


def bytes_to_gib(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value / GIB


def _required_expandable_bytes(config):
    minimum_gb = getattr(config, 'min_expandable_gb', 0)
    expansion_gb = getattr(config, 'expand_data_disk_gb', 0)
    return max(minimum_gb, expansion_gb) * GIB


def get_machine_info(config, gpu, region_sign_list, gpu_num):
    url = 'https://www.autodl.com/api/v1/user/machine/list'
    headers = {
        'User-Agent': UserAgent().random,
        'Authorization': config.Authorization
    }
    data = {
        'charge_type': 'payg',
        'default_order': True,
        'gpu_idle_num': gpu_num,
        'gpu_type_name': [gpu],
        'page_index': 1,
        'page_size': MACHINE_PAGE_SIZE,
        'region_sign_list': list(region_sign_list),
    }
    
    machine_info = []
    required_expandable_bytes = _required_expandable_bytes(config)
    page_index = 1
    max_page = 1
    while page_index <= max_page:
        data['page_index'] = page_index
        try:
            response = query_post_json(url, headers=headers, data=data)
        except (requests.RequestException, ValueError) as exc:
            print(f"查询可购买机器失败，稍后重试: {exc}")
            return []

        if not isinstance(response, dict) or not isinstance(response.get('data'), dict):
            message = response.get('msg', response) if isinstance(response, dict) else response
            print(f"查询可购买机器返回异常: {message}")
            return []

        response_data = response['data']
        raw_max_page = response_data.get('max_page', 1)
        if isinstance(raw_max_page, int) and raw_max_page > 0:
            max_page = raw_max_page

        for machine in response_data.get('list', []):
            if not isinstance(machine, dict):
                continue
            if machine.get('gpu_idle_num', 0) < gpu_num:
                continue
            if 'machine_id' not in machine or 'payg_price' not in machine:
                continue

            max_expandable_bytes = machine.get('max_data_disk_expand_size')
            if required_expandable_bytes:
                if (
                    isinstance(max_expandable_bytes, bool)
                    or not isinstance(max_expandable_bytes, (int, float))
                    or max_expandable_bytes < required_expandable_bytes
                ):
                    continue

            price_info = machine.get('data_disk_expand_price')
            daily_price = (
                price_info.get('daily_price')
                if isinstance(price_info, dict)
                else None
            )
            machine_info.append({
                'machine_id': machine['machine_id'],
                'machine_price': machine['payg_price'],
                'data_disk_bytes': machine.get('max_instance_disk_size'),
                'max_expandable_bytes': max_expandable_bytes,
                'data_disk_daily_price_per_gb': (
                    daily_price / 1000
                    if isinstance(daily_price, (int, float))
                    and not isinstance(daily_price, bool)
                    else None
                ),
            })
        page_index += 1

    return machine_info


def buy_candidate(
    config,
    candidate,
    gpu_num,
    image,
    private_image_uuid,
    machine_info=None,
):
    gpu = candidate['gpu']
    region_name = candidate['region']
    region_sign_list = candidate['region_sign_list']
    if image is None:
        image = 'hub.kce.ksyun.com/autodl-image/torch:cuda12.4-cudnn-devel-ubuntu22.04-py312-torch2.5.1'
    url = 'https://www.autodl.com/api/v1/order/instance/create/payg'
    headers = {
        'User-Agent': UserAgent().random,
        'Authorization': config.Authorization
    }
    expand_data_disk = getattr(config, 'expand_data_disk_gb', 0) * GIB
    data = {
        'instance_info': {
            'charge_type': 'payg',
            'expand_data_disk': expand_data_disk,
            'image': image,
            'machine_id': '',
            'req_gpu_amount': gpu_num,
            'private_image_uuid': private_image_uuid,
        },
        'price_info': {
            'charge_type': 'payg',
            'expand_data_disk': expand_data_disk,
            'machine_id': '',
            'num': gpu_num
        }
    }
    
    # 购买结果
    result = {
        'success': False,
        'message': '',
        'region': '',
        'machine_id': '',
        'price': '',
        'region_name': '',
        'gpu_info': candidate,
        'fatal': False,
        'expand_data_disk_gb': getattr(config, 'expand_data_disk_gb', 0),
        'data_disk_daily_price': None,
    }

    if machine_info is None:
        machine_info = get_machine_info(config, gpu, region_sign_list, gpu_num)
    last_error = ''
    for info in machine_info:
        max_expandable_bytes = info.get('max_expandable_bytes')
        if expand_data_disk and (
            isinstance(max_expandable_bytes, bool)
            or not isinstance(max_expandable_bytes, (int, float))
            or max_expandable_bytes < expand_data_disk
        ):
            continue
        data['instance_info']['machine_id'] = info['machine_id']
        data['price_info']['machine_id'] = info['machine_id']
        # 创建订单不能自动重试：如果服务端已经创建订单但响应丢失，
        # 重试可能生成重复实例。
        try:
            http_response = requests.post(
                url,
                headers=headers,
                json=data,
                timeout=(10, 30),
            )
            http_response.raise_for_status()
            response = http_response.json()
        except (requests.RequestException, ValueError) as exc:
            result['fatal'] = True
            result['message'] = f"创建订单请求结果未知: {exc}"
            return result

        if not isinstance(response, dict):
            result['fatal'] = True
            result['message'] = f"创建订单返回格式异常，订单结果未知: {response}"
            return result

        if response.get('code') == 'Success':
            raw_price = info.get('machine_price')
            price = raw_price / 1000 if isinstance(raw_price, (int, float)) else raw_price
            result['success'] = True
            result['region'] = region_name
            result['machine_id'] = info['machine_id']
            result['price'] = price
            result['region_name'] = region_name
            disk_price_per_gb = info.get('data_disk_daily_price_per_gb')
            if isinstance(disk_price_per_gb, (int, float)):
                result['data_disk_daily_price'] = (
                    disk_price_per_gb * result['expand_data_disk_gb']
                )
            result['message'] = (
                f"购买成功: {gpu} - {region_name} - "
                f"{info['machine_id']} - {price}元/小时"
            )
            print(result['message'])
            return result

        last_error = response.get('msg') or response.get('message') or str(response.get('code', '未知错误'))

    result['message'] = f"{gpu} 在 {region_name} 购买失败"
    if last_error:
        result['message'] += f": {last_error}"
    print(result['message'])
    return result
