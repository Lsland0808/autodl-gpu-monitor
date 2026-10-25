import requests
from fake_useragent import UserAgent

from watch import query_post_json


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
        'page_size': 10,
        'region_sign_list': list(region_sign_list),
    }
    
    machine_info = []
    try:
        response = query_post_json(url, headers=headers, data=data)
    except (requests.RequestException, ValueError) as exc:
        print(f"查询可购买机器失败，稍后重试: {exc}")
        return []

    if not isinstance(response, dict) or not isinstance(response.get('data'), dict):
        message = response.get('msg', response) if isinstance(response, dict) else response
        print(f"查询可购买机器返回异常: {message}")
        return []
  
    for v in response['data'].get('list', []):
        if not isinstance(v, dict):
            continue
        if v.get('gpu_idle_num', 0) < gpu_num:
            continue
        if 'machine_id' not in v or 'payg_price' not in v:
            continue

        machine_info.append({
            'machine_id': v['machine_id'],
            'machine_price': v['payg_price']
        })
    
    # exit()
    return machine_info


def buy_candidate(config, candidate, gpu_num, image, private_image_uuid):
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
    data = {
        'instance_info': {
            'charge_type': 'payg',
            'expand_data_disk': 0,
            'image': image,
            'machine_id': '',
            'req_gpu_amount': gpu_num,
            'private_image_uuid': private_image_uuid,
        },
        'price_info': {
            'charge_type': 'payg',
            'expand_data_disk': 0,
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
    }

    machine_info = get_machine_info(config, gpu, region_sign_list, gpu_num)
    last_error = ''
    for info in machine_info:
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
