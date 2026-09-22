# encoding: utf-8
"""桌面端本地存储：配置、软件登录会话、小红书 Cookie、输出目录。"""
from __future__ import annotations

import json
from pathlib import Path

APP_DIR = Path.home() / '.xhs_spider'
CONFIG_FILE = APP_DIR / 'config.json'
SESSION_FILE = APP_DIR / 'session.json'
XHS_COOKIE_FILE = APP_DIR / 'xhs_cookie.txt'
DEFAULT_OUTPUT_DIR = Path.home() / 'Documents' / 'XHS采集'

# 线上服务内置地址：客户无需填写，登录框保留输入框仅供开发联调用
DEFAULT_SERVER = 'https://yushu.yituohub.com'

DEFAULT_CONFIG = {
    'server': DEFAULT_SERVER,
    'username': '',
    'output_dir': str(DEFAULT_OUTPUT_DIR),
    # AI 改写（OpenAI Responses API 兼容）
    'ai_base': 'https://api.openai.com/v1',
    'ai_key': '',
    'ai_model': 'gpt-4o-mini',
    'ai_prompt': '',   # 空=使用 ai_client.DEFAULT_PROMPT 预设
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
