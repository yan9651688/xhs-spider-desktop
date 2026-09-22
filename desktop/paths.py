# encoding: utf-8
"""桌面端本地存储：配置、软件登录会话、小红书 Cookie、输出目录。"""
from __future__ import annotations

import json
from pathlib import Path

APP_DIR = Path.home() / '.xhs_spider'
CONFIG_FILE = APP_DIR / 'config.json'
SESSION_FILE = APP_DIR / 'session.json'
XHS_COOKIE_FILE = APP_DIR / 'xhs_cookie.txt'
COOKIES_FILE = APP_DIR / 'xhs_cookies.json'
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
