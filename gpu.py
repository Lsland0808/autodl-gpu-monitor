import ast

import requests
from fake_useragent import UserAgent

from watch import query_post_json, watch_gpu


region2name = {
    "['nm-B1', 'nm-B2']": '内蒙B区',
    "['cq-A1']": '重庆A区',
    "['west-B', 'west-C']": '西北B区',
    "['bj-B1']": '北京B区',
    "['beijing-A', 'beijing-B', 'beijing-D', 'beijing-E']": '北京A区',
    "['foshan-A']": '佛山区',
    "['neimeng-A', 'neimeng-C', 'neimeng-D']": '内蒙A区',
    "['bj-C1']": 'L20专区',
    "['yz-A1']": '3090专区',
    "['beijing-C']": 'V100专区',
    "['nm-A1']": 'A800专区',
    "['gd-A1']": '华为昇腾专区',
}

def get_machine_info(config, gpu, region,gpu_num):
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
        'region_sign_list': ast.literal_eval(region),
    }
    
    machine_info = []
    try:
        response = query_post_json(url, headers=headers, data=data)
    except (requests.RequestException, ValueError) as exc:
        print(f"查询可购买机器失败，稍后重试: {exc}")
        return []

    if not isinstance(response.get('data'), dict):
        print(f"查询可购买机器返回异常: {response.get('msg', response)}")
        return []
  
    for v in response['data'].get('list', []):
        
        if v['gpu_idle_num']<gpu_num:
            continue
      
        machine_info.append({
            'machine_id': v['machine_id'],
            'machine_price': v['payg_price']
        })
    
    # exit()
    return machine_info


def buy(config, gpu, gpu_num,image,private_image_uuid):
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
        'gpu_info': {},
        'fatal': False,
    }
    
    gpu_info = watch_gpu(config, gpu)
    result['gpu_info'] = gpu_info
    
    for region in gpu_info.keys():
        code = ''
        machine_info = get_machine_info(config, gpu, region, gpu_num)
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

            if response.get('code') == 'Success':
                info['machine_price'] = info['machine_price']/1000  # 转换为元/小时
                code = 'Success'
                result['success'] = True
                result['region'] = region
                result['machine_id'] = info['machine_id']
                result['price'] = info['machine_price']
                result['region_name'] = region2name[str(region)]
                result['message'] = f"购买成功: {region2name[str(region)]} - {info['machine_id']} - {info['machine_price']}元/小时"
                print(result['message'])
                return result
            else:
                continue
        if code == 'Success':
            break
        else:
            continue
    
    # 如果没有购买成功
    result['message'] = f"购买失败: 没有找到可用的 {gpu} GPU"
    print(result['message'])
    return result
