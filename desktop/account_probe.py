# encoding: utf-8
"""账号健康巡检：探测单个小红书账号的会话状态与资产数据。

刻意做成**无 Qt、无 UI 依赖的纯函数模块**，这样自检可以直接猴子补丁
XHSPcAuth / XHS_Apis 后测判定逻辑，不需要 Node、不需要网络。

判定依据（已用真实接口核对，2026-10-03）：

- ``get_user_me()``  -> ``res['data']`` 含 ``guest`` / ``nickname`` / ``user_id`` /
  ``red_id`` / ``images`` / ``imageb`` / ``gender`` / ``desc``；``guest=False`` 表示已登录。
  **web_session 失效时该接口可能返回 success=true + guest=true（HTTP 200）**，
  所以 guest 判据不能省。
- ``get_user_info(user_id)`` -> ``res['data']`` 含 ``basic_info`` / ``interactions`` /
  ``posted`` / ``liked`` / ``collected`` / ``tags``。interactions 是列表，
  必须按 type/name 定位，不能按下标硬取（顺序不保证）。
"""
from __future__ import annotations

import time

from loguru import logger

from desktop import paths

# 与 spider_service.RISK_KEYWORDS 同一套语义；单独定义避免 desktop 内循环 import
LIMIT_KEYWORDS = ('461', '-629', '频繁', '风控', '验证码', '异常流量')

_AVATAR_TIMEOUT = 10


def _as_int(value) -> int:
    """'1801' / 1801 / '1.8K' / None -> int；解析失败返回 0。"""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value or '').strip().replace(',', '')
    if not text:
        return 0
    suffix = 1
    if text and text[-1] in 'Kk万':
        suffix = 1000 if text[-1] in 'Kk' else 10000
        text = text[:-1]
    else:
        for unit, factor in (('W', 10000), ('亿', 100000000)):
            if text.endswith(unit):
                suffix = factor
                text = text[: -len(unit)]
                break
    try:
        return int(float(text) * suffix)
    except (TypeError, ValueError):
        return 0


def _parse_gender(value) -> str:
    """沿用项目既有约定（xhs_utils/data_util.py:handle_user_info）：0=男 1=女 其余未知。"""
    if value == 0 or value == '0':
        return '男'
    if value == 1 or value == '1':
        return '女'
    return '未知'


def _find_interaction(interactions, *names) -> int:
    """按 type 或 name 定位互动数，不依赖列表顺序。"""
    for item in interactions or []:
        if not isinstance(item, dict):
            continue
        keys = (item.get('type'), item.get('name'))
        if any(name in keys for name in names):
            return _as_int(item.get('count'))
    return 0


def parse_user_info(data: dict, user_id: str = '') -> dict:
    """get_user_info 的 data -> 台账扁平字段。

    全程 .get()，缺字段返回 0/空串，**不抛 KeyError**（接口结构会变）。
    不复用 xhs_utils/data_util.handle_user_info：它直接下标访问且缺 posted/liked/collected。
    """
    data = data or {}
    basic = data.get('basic_info') or {}
    interactions = data.get('interactions') or []
    tags = []
    for tag in data.get('tags') or []:
        if isinstance(tag, dict):
            name = tag.get('name')
        else:
            name = tag
        if name:
            tags.append(str(name))
    return {
        'user_id': str(user_id or ''),
        'nickname': str(basic.get('nickname') or ''),
        'avatar': str(basic.get('imageb') or basic.get('images') or ''),
        'red_id': str(basic.get('red_id') or ''),
        'gender': _parse_gender(basic.get('gender')),
        'ip_location': str(basic.get('ip_location') or ''),
        'desc': str(basic.get('desc') or ''),
        'follows': _find_interaction(interactions, 'follows', '关注'),
        'fans': _find_interaction(interactions, 'fans', '粉丝'),
        'interaction': _find_interaction(interactions, 'interaction', '获赞与收藏'),
        'posted': _as_int(data.get('posted')),
        'liked': _as_int(data.get('liked')),
        'collected': _as_int(data.get('collected')),
        'tags': tags,
    }


def classify_message(message: str) -> str:
    """错误文本 -> 健康状态。"""
    text = str(message or '')
    if any(keyword in text for keyword in LIMIT_KEYWORDS):
        return paths.HEALTH_LIMITED
    return paths.HEALTH_NETWORK


def classify_error(error: Exception) -> str:
    """异常 -> 健康状态。"""
    text = str(error or '')
    # from_cookie 会校验 a1 / web_session；缺了就是本地 Cookie 不完整，等同于失效
    if isinstance(error, ValueError) and ('a1' in text or 'web_session' in text):
        return paths.HEALTH_EXPIRED
    return classify_message(text)


def probe_account(cookie: str, *, full: bool = True, download_avatar: bool = True) -> dict:
    """探测单个账号，返回可直接写入台账的字段 dict。

    full=True  完整模式：get_user_me + get_user_info（资产全量）
    full=False 快速模式：只验会话（get_user_me）

    注意 XHSPcAuth.from_cookie 内部已调一次 bootstrap()（即一次 get_user_me），
    第一版为正确性保留这次重复请求。
    """
    result = {
        'health': paths.HEALTH_UNKNOWN,
        'health_note': '',
        'checked_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    cookie = str(cookie or '')
    if not cookie:
        result.update(health=paths.HEALTH_EXPIRED, health_note='Cookie 为空')
        return result

    from apis.xhs_pc_apis import XHS_Apis
    from xhs_utils.xhs_pc import XHSPcAuth

    auth = None
    try:
        try:
            auth = XHSPcAuth.from_cookie(cookie)
        except Exception as exc:
            result.update(health=classify_error(exc), health_note=str(exc)[:200])
            logger.warning(f'账号巡检：Cookie 不可用：{str(exc)[:120]}')
            return result

        api = XHS_Apis(auth)
        try:
            success, msg, res = api.get_user_me()
        except Exception as exc:
            result.update(health=classify_error(exc), health_note=str(exc)[:200])
            return result

        data = (res or {}).get('data') or {}
        if data.get('guest') is True:
            result.update(health=paths.HEALTH_EXPIRED, health_note='登录态已失效，需重新扫码')
            return result
        if not success:
            health = classify_message(msg)
            result.update(health=health, health_note=str(msg)[:200])
            return result

        result.update(
            health=paths.HEALTH_OK,
            health_note='',
            user_id=str(data.get('user_id') or ''),
            nickname=str(data.get('nickname') or ''),
            avatar=str(data.get('imageb') or data.get('images') or ''),
            red_id=str(data.get('red_id') or ''),
            gender=_parse_gender(data.get('gender')),
            desc=str(data.get('desc') or ''),
        )
        if not full:
            if download_avatar:
                _cache_avatar(result)
            return result

        user_id = result.get('user_id') or ''
        if not user_id:
            result['health_note'] = '接口未返回 user_id，资产数据未刷新'
            return result
        try:
            ok, info_msg, info_res = api.get_user_info(user_id)
        except Exception as exc:
            ok, info_msg, info_res = False, str(exc), None
        if ok and isinstance((info_res or {}).get('data'), dict):
            assets = parse_user_info(info_res['data'], user_id)
            # basic_info 的信息更全（IP 属地只在详情接口），但 me 的昵称是权威值，
            # 详情接口偶发返回空字符串时不要覆盖掉已确认的身份字段
            for name, value in assets.items():
                if value not in ('', [], 0, None) or name in ('follows', 'fans', 'posted'):
                    result[name] = value
            result['user_id'] = user_id
        else:
            # 身份已确认，资产取不到不改变健康状态，只记一句备注
            result['health_note'] = f'资产数据获取失败：{str(info_msg)[:80]}'
        if download_avatar:
            _cache_avatar(result)
        return result
    finally:
        if auth is not None:
            try:
                auth.close()
            except Exception:
                pass


def _cache_avatar(result: dict) -> None:
    """头像下载并落到本地缓存，路径写回 result['avatar_file']。失败静默（不影响健康判定）。"""
    user_id = str(result.get('user_id') or '')
    url = str(result.get('avatar') or '')
    if not user_id or not url:
        return
    target = paths.avatar_path(user_id)
    if target.exists() and target.stat().st_size > 0:
        result['avatar_file'] = str(target)
        return
    try:
        import requests

        response = requests.get(url, timeout=_AVATAR_TIMEOUT)
        response.raise_for_status()
        saved = paths.save_avatar(user_id, response.content)
        if saved:
            result['avatar_file'] = saved
    except Exception as exc:
        logger.debug(f'头像下载失败（{user_id}）：{str(exc)[:80]}')
