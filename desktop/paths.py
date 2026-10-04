# encoding: utf-8
"""桌面端本地存储：配置、软件登录会话、小红书 Cookie、输出目录。"""
from __future__ import annotations

import json
import time
from pathlib import Path

APP_DIR = Path.home() / '.xhs_spider'
CONFIG_FILE = APP_DIR / 'config.json'
SESSION_FILE = APP_DIR / 'session.json'
XHS_COOKIE_FILE = APP_DIR / 'xhs_cookie.txt'
COOKIES_FILE = APP_DIR / 'xhs_cookies.json'
ACCOUNTS_FILE = APP_DIR / 'xhs_accounts.json'
WATCHLIST_FILE = APP_DIR / 'xhs_watchlist.json'
AVATAR_DIR = APP_DIR / 'avatars'
DEFAULT_OUTPUT_DIR = Path.home() / 'Documents' / 'XHS采集'

# 线上服务内置地址：客户无需填写，登录框保留输入框仅供开发联调用
DEFAULT_SERVER = 'https://yushu.yituohub.com'

# AI 接口统一走平台网关，锁死不开放修改；模型在模型广场自选复制
AI_BASE_FIXED = 'https://api.yituohub.com/v1'
AI_PRICING_URL = 'https://api.yituohub.com/pricing'
AI_MODEL_DEFAULT = 'deepseek-v4.1-flash'

DEFAULT_CONFIG = {
    'server': DEFAULT_SERVER,
    'username': '',
    'output_dir': str(DEFAULT_OUTPUT_DIR),
    # AI 改写（OpenAI Responses API 兼容，接口地址固定走平台网关）
    'ai_base': AI_BASE_FIXED,
    'ai_key': '',
    'ai_model': AI_MODEL_DEFAULT,
    'ai_title_prompt': '',    # 空=使用 ai_client.TITLE_PROMPT_PRESET 预设
    'ai_content_prompt': '',  # 空=使用 ai_client.CONTENT_PROMPT_PRESET 预设
    # 账号矩阵：每 6 小时自动巡检账号健康
    'probe_auto': True,
}


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def _load_json(path: Path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save_json(path: Path, data) -> None:
    ensure_app_dir()
    tmp = path.with_suffix(path.suffix + '.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    config.update(_load_json(CONFIG_FILE, {}) or {})
    if not config.get('server'):
        config['server'] = DEFAULT_SERVER
    return config


def save_config(config: dict) -> None:
    _save_json(CONFIG_FILE, config)


def load_session():
    session = _load_json(SESSION_FILE, None)
    if not isinstance(session, dict) or not session.get('token'):
        return None
    return session


def save_session(session: dict) -> None:
    _save_json(SESSION_FILE, session)


def clear_session() -> None:
    try:
        SESSION_FILE.unlink()
    except FileNotFoundError:
        pass


def load_xhs_cookie() -> str:
    try:
        return XHS_COOKIE_FILE.read_text(encoding='utf-8').strip()
    except OSError:
        return ''


def save_xhs_cookie(cookie: str) -> None:
    ensure_app_dir()
    XHS_COOKIE_FILE.write_text(cookie, encoding='utf-8')


def clear_xhs_cookie() -> None:
    try:
        XHS_COOKIE_FILE.unlink()
    except FileNotFoundError:
        pass


# ---------- 多小红书账号 Cookie 池 ----------

def _cookie_key(cookie: str) -> str:
    """以 web_session 作为账号唯一标识。"""
    for part in str(cookie or '').split(';'):
        key, _, value = part.strip().partition('=')
        if key == 'web_session':
            return value
    return str(cookie or '')[:64]


def load_xhs_cookies() -> list:
    """Cookie 池：[{cookie, nickname}]；仅池文件不存在时迁移旧的单 Cookie 文件。"""
    if COOKIES_FILE.exists():
        data = _load_json(COOKIES_FILE, [])
        if isinstance(data, list):
            return [dict(c) for c in data if isinstance(c, dict) and c.get('cookie')]
        return []
    legacy = load_xhs_cookie()
    if legacy:
        items = [{'cookie': legacy, 'nickname': ''}]
        save_xhs_cookies(items)
        clear_xhs_cookie()
        return items
    return []


def save_xhs_cookies(items: list) -> None:
    _save_json(COOKIES_FILE, [
        {'cookie': c.get('cookie', ''), 'nickname': c.get('nickname', '')}
        for c in items if isinstance(c, dict) and c.get('cookie')
    ])


def add_xhs_cookie(cookie: str, nickname: str = '') -> None:
    items = load_xhs_cookies()
    key = _cookie_key(cookie)
    for item in items:
        if _cookie_key(item.get('cookie', '')) == key:
            if nickname:
                item['nickname'] = nickname
            save_xhs_cookies(items)
            return
    items.append({'cookie': cookie, 'nickname': nickname})
    save_xhs_cookies(items)


def set_xhs_nickname(cookie: str, nickname: str) -> None:
    items = load_xhs_cookies()
    key = _cookie_key(cookie)
    for item in items:
        if _cookie_key(item.get('cookie', '')) == key and nickname:
            item['nickname'] = nickname
            save_xhs_cookies(items)
            return


def remove_xhs_cookie(index: int) -> None:
    items = load_xhs_cookies()
    if 0 <= index < len(items):
        items.pop(index)
        save_xhs_cookies(items)


# ---------- 账号资产台账（账号矩阵） ----------
#
# 为什么不复用 xhs_cookies.json：save_xhs_cookies 是白名单序列化，
# 只保留 cookie / nickname 两个字段，扩展字段会被静默抹掉。
# 台账另存 xhs_accounts.json，主键统一走 account_key(cookie)。

HEALTH_OK = 'ok'            # 会话有效，且资产已刷新
HEALTH_EXPIRED = 'expired'  # 登录态失效，需重新扫码
HEALTH_LIMITED = 'limited'  # 命中风控/限流
HEALTH_NETWORK = 'network'  # 网络异常（超时等），会话本身未必失效
HEALTH_UNKNOWN = 'unknown'  # 尚未巡检

HEALTH_LABELS = {
    HEALTH_OK: '正常',
    HEALTH_EXPIRED: '登录失效',
    HEALTH_LIMITED: '限流风控',
    HEALTH_NETWORK: '网络异常',
    HEALTH_UNKNOWN: '未巡检',
}

LEDGER_VERSION = 1
HISTORY_KEEP_DAYS = 90


def account_key(cookie: str) -> str:
    """台账主键：与 Cookie 池保持同一套唯一标识（web_session）。"""
    return _cookie_key(cookie)


def account_key_of(item: dict) -> str:
    """从台账记录取主键；兼容记录缺 key 时回算。"""
    return str((item or {}).get('key') or '') or _cookie_key((item or {}).get('cookie', ''))


def empty_ledger() -> dict:
    return {'version': LEDGER_VERSION, 'accounts': {}, 'history': {}}


def load_accounts() -> dict:
    """读台账。结构损坏或缺字段时补全，不抛异常（界面初始化不能被文件问题打断）。"""
    data = _load_json(ACCOUNTS_FILE, None)
    if not isinstance(data, dict):
        return empty_ledger()
    ledger = empty_ledger()
    accounts = data.get('accounts')
    if isinstance(accounts, dict):
        ledger['accounts'] = {
            str(key): dict(value)
            for key, value in accounts.items()
            if isinstance(value, dict)
        }
    history = data.get('history')
    if isinstance(history, dict):
        ledger['history'] = {
            str(key): [h for h in value if isinstance(h, dict)]
            for key, value in history.items()
            if isinstance(value, list)
        }
    return ledger


def save_accounts(ledger: dict) -> None:
    _save_json(ACCOUNTS_FILE, ledger or empty_ledger())


def _prune_history(entries: list) -> list:
    """按天裁剪：同一天只留最后一条，仅保留最近 HISTORY_KEEP_DAYS 天。"""
    by_date = {}
    for entry in entries:
        date = str(entry.get('date') or '')
        if date:
            by_date[date] = entry
    cutoff = time.strftime('%Y-%m-%d', time.localtime(time.time() - HISTORY_KEEP_DAYS * 86400))
    return [by_date[d] for d in sorted(by_date) if d >= cutoff]


def upsert_account(key: str, **fields) -> dict:
    """合并写入单个账号。字段为空/None 时不覆盖已有值；成功的资产探测顺带写历史快照。"""
    key = str(key or '')
    if not key:
        return {}
    ledger = load_accounts()
    record = ledger['accounts'].get(key) or {'key': key}
    health = fields.get('health')
    for name, value in fields.items():
        if value is None or value == '':
            continue
        if name == 'tags' and not value:
            continue
        record[name] = value
    record['key'] = key
    if health:
        record['health'] = health
        failures = int(record.get('consecutive_failures') or 0)
        record['consecutive_failures'] = 0 if health == HEALTH_OK else failures + 1
    record['checked_at'] = fields.get('checked_at') or time.strftime('%Y-%m-%d %H:%M:%S')
    ledger['accounts'][key] = record

    if health == HEALTH_OK and record.get('user_id'):
        user_id = str(record['user_id'])
        today = time.strftime('%Y-%m-%d')
        entries = [h for h in ledger['history'].get(user_id, [])
                   if str(h.get('date') or '') != today]
        entries.append({
            'date': today,
            'fans': int(record.get('fans') or 0),
            'interaction': int(record.get('interaction') or 0),
            'posted': int(record.get('posted') or 0),
        })
        ledger['history'][user_id] = _prune_history(entries)

    save_accounts(ledger)
    return record


def remove_accounts(keys) -> None:
    """从台账移除若干账号（连带历史快照）。keys 为空则不做任何事。"""
    keys = [str(k) for k in (keys or []) if k]
    if not keys:
        return
    ledger = load_accounts()
    for key in keys:
        record = ledger['accounts'].pop(key, None)
        user_id = str((record or {}).get('user_id') or '')
        if user_id:
            ledger['history'].pop(user_id, None)
        _remove_avatar(user_id)
    save_accounts(ledger)


def sync_accounts_from_pool() -> dict:
    """Cookie 池 -> 台账对齐：池里新增的补空条目，池里删掉的从台账移除。

    以池为准（客户在侧边栏点 ✕ 就该消失），但不动已有条目的巡检结果。
    """
    ledger = load_accounts()
    pool = load_xhs_cookies()
    pool_keys = {}
    for item in pool:
        cookie = (item or {}).get('cookie') or ''
        if not cookie:
            continue
        key = _cookie_key(cookie)
        pool_keys[key] = item
        record = ledger['accounts'].get(key)
        if record is None:
            ledger['accounts'][key] = {
                'key': key,
                'cookie': cookie,
                'nickname': (item or {}).get('nickname') or '',
                'added_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                'checked_at': '',
                'health': HEALTH_UNKNOWN,
                'consecutive_failures': 0,
            }
        else:
            # 池里的 cookie 可能续期过（同一个 web_session 换了其他字段），以池为准
            record['cookie'] = cookie
            if (item or {}).get('nickname') and not record.get('nickname'):
                record['nickname'] = item['nickname']
    dropped = [k for k in ledger['accounts'] if k not in pool_keys]
    for key in dropped:
        record = ledger['accounts'].pop(key)
        user_id = str(record.get('user_id') or '')
        if user_id:
            ledger['history'].pop(user_id, None)
        _remove_avatar(user_id)
    save_accounts(ledger)
    return ledger


def account_cookie(key: str) -> str:
    """按台账主键取 cookie；台账缺该条目时回退到 Cookie 池。"""
    record = load_accounts()['accounts'].get(str(key or ''))
    if record and record.get('cookie'):
        return record['cookie']
    for item in load_xhs_cookies():
        cookie = (item or {}).get('cookie') or ''
        if cookie and _cookie_key(cookie) == key:
            return cookie
    return ''


# ---------- 头像缓存 ----------
#
# 小红书头像 CDN 走 imageView2/.../format/webp，返回的是 webp 而非 jpg，
# 所以用中性扩展名 .img —— QPixmap 按内容嗅探格式，不看扩展名。

def avatar_path(user_id: str) -> Path:
    return AVATAR_DIR / f'{user_id}.img'


def save_avatar(user_id: str, data: bytes) -> str:
    """写入 avatars/{user_id}.img，返回绝对路径（失败返回空串）。"""
    user_id = str(user_id or '')
    if not user_id or not data:
        return ''
    try:
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        target = avatar_path(user_id)
        tmp = target.with_suffix('.img.tmp')
        tmp.write_bytes(data)
        tmp.replace(target)
        return str(target)
    except OSError:
        return ''


def _remove_avatar(user_id: str) -> None:
    if not user_id:
        return
    try:
        avatar_path(user_id).unlink()
    except OSError:
        pass


def clear_avatars() -> None:
    try:
        for path in AVATAR_DIR.glob('*.img'):
            path.unlink()
    except OSError:
        pass
