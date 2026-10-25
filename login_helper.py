import hashlib
import time
import webbrowser

import requests
from fake_useragent import UserAgent

from config_loader import Config
from regions import REGION_CATALOG


REQUEST_TIMEOUT = (10, 20)
BASE_URL = "https://www.autodl.com/api/v1"


class LoginError(RuntimeError):
    pass


def _headers(authorization=None):
    headers = {"User-Agent": UserAgent().random}
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _json_response(response, action):
    try:
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise LoginError(f"{action}失败: {exc}") from exc
    if not isinstance(payload, dict):
        raise LoginError(f"{action}返回格式异常")
    return payload


def _exchange_ticket(ticket, session=requests):
    response = session.post(
        f"{BASE_URL}/passport",
        headers=_headers(),
        json={"ticket": ticket},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _json_response(response, "获取授权令牌")
    data = payload.get("data")
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise LoginError(payload.get("msg") or "未获得有效的授权令牌")
    return token.strip()


def create_qr_session(session=requests):
    response = session.get(
        f"{BASE_URL}/wx/login",
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    payload = _json_response(response, "获取登录二维码")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise LoginError(payload.get("msg") or "登录二维码返回数据异常")
    qrcode_url = data.get("qrcode_url")
    uuid = data.get("uuid")
    if not qrcode_url or not uuid:
        raise LoginError("登录二维码缺少 URL 或会话标识")

    image_response = session.get(
        qrcode_url,
        headers=_headers(),
        timeout=REQUEST_TIMEOUT,
    )
    try:
        image_response.raise_for_status()
    except requests.RequestException as exc:
        raise LoginError(f"下载登录二维码失败: {exc}") from exc
    return {
        "uuid": uuid,
        "qrcode_url": qrcode_url,
        "image": image_response.content,
    }


def poll_qr_login(uuid, session=requests):
    """轮询一次扫码状态，返回 waiting、expired 或 success。"""
    try:
        response = session.post(
            f"{BASE_URL}/wx/polling/query",
            headers=_headers(),
            json={"uuid": uuid},
            timeout=REQUEST_TIMEOUT,
        )
        payload = _json_response(response, "查询扫码状态")
    except LoginError as exc:
        return {"status": "error", "message": str(exc)}

    data = payload.get("data")
    if isinstance(data, dict) and data.get("ticket"):
        try:
            token = _exchange_ticket(data["ticket"], session=session)
        except LoginError as exc:
            return {"status": "error", "message": str(exc)}
        return {"status": "success", "token": token}

    message = str(payload.get("msg") or "等待扫码确认")
    normalized = message.lower()
    if any(word in normalized for word in ("过期", "expired", "失效")):
        return {"status": "expired", "message": message}
    return {"status": "waiting", "message": message}


def login_with_password(phone, password, session=requests):
    phone = str(phone).strip()
    if not phone or not password:
        raise LoginError("手机号和密码不能为空")
    password_hash = hashlib.sha1(str(password).encode("utf-8")).hexdigest()
    response = session.post(
        f"{BASE_URL}/new_login",
        headers=_headers(),
        json={"phone": phone, "password": password_hash, "phone_area": "+86"},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _json_response(response, "账号密码登录")
    data = payload.get("data")
    ticket = data.get("ticket") if isinstance(data, dict) else None
    if not ticket:
        raise LoginError(payload.get("msg") or "登录响应中没有 ticket")
    return _exchange_ticket(ticket, session=session)


def validate_authorization(token, session=requests):
    token = str(token).strip()
    if not token:
        raise LoginError("Authorization 不能为空")
    response = session.post(
        f"{BASE_URL}/machine/region/gpu_type",
        headers=_headers(token),
        json={"region_sign_list": REGION_CATALOG["内蒙B区"]},
        timeout=REQUEST_TIMEOUT,
    )
    payload = _json_response(response, "验证 Authorization")
    if not isinstance(payload.get("data"), list):
        raise LoginError(payload.get("msg") or "Authorization 无效或已过期")
    return True


def update_config_authorization(token, config=None, config_file="config.json"):
    target = config or Config(config_file)
    target.update_authorization(token)


def login_and_update_config(phone=None, password=None, config=None):
    """CLI 登录入口；桌面端调用上面的细粒度接口。"""
    if phone is not None and password is not None:
        token = login_with_password(phone, password)
    else:
        qr_session = create_qr_session()
        print("请在浏览器中扫描 AutoDL 微信登录二维码：")
        print(qr_session["qrcode_url"])
        webbrowser.open(qr_session["qrcode_url"])
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            result = poll_qr_login(qr_session["uuid"])
            if result["status"] == "success":
                token = result["token"]
                break
            if result["status"] in {"expired", "error"}:
                raise LoginError(result.get("message", "扫码登录失败"))
            time.sleep(2)
        else:
            raise LoginError("扫码登录超时，请重新运行程序")

    update_config_authorization(token, config=config)
    print("登录成功，authorization 已更新到配置文件")
    return token


def login(phone=None, password=None):
    return login_and_update_config(phone, password)
