# encoding: utf-8
"""AI 改写客户端：OpenAI Responses API（/v1/responses）格式，兼容各类 OpenAI 兼容网关。"""
from __future__ import annotations

import json
import re

import requests

DEFAULT_PROMPT = (
    '你是小红书爆款文案专家。我会给你一篇笔记的原标题和原文，请改写成全新的小红书风格文案：\n'
    '1. 标题：20字以内，有网感、带悬念或利益点，可加1-2个emoji；\n'
    '2. 正文：150-250字，口语化、分段清晰、结尾带4个 #话题标签；\n'
    '3. 保留原文核心信息与卖点，不得虚构事实。\n'
    '只输出JSON，格式：{"title":"改写后的标题","content":"改写后的正文"}，不要输出任何其他内容。'
)


class AIError(RuntimeError):
    pass


def parse_rewrite_json(text: str) -> dict:
    """从模型输出中稳健地解析 {"title","content"}；失败返回空 dict。"""
    if not text:
        return {}
    try:
        data = json.loads(text)
        if isinstance(data, dict) and (data.get('title') or data.get('content')):
            return data
    except (ValueError, TypeError):
        pass
    match = re.search(r'\{[^{}]*"title"[^{}]*\}', text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, dict):
                return data
        except (ValueError, TypeError):
            pass
    return {}


def _extract_text(data: dict) -> str:
    """兼容 Responses API 的 output_text 快捷字段与 output[].content[].text 结构。"""
    if not isinstance(data, dict):
        return ''
    if data.get('output_text'):
        return str(data['output_text'])
    chunks = []
    for item in data.get('output') or []:
        if not isinstance(item, dict) or item.get('type') not in ('message', None):
            continue
        for part in item.get('content') or []:
            if isinstance(part, dict) and part.get('type') in ('output_text', 'text'):
                chunks.append(str(part.get('text') or ''))
    return ''.join(chunks)


class AIClient:
    def __init__(self, base: str, api_key: str, model: str, prompt: str):
        self.base = (base or 'https://api.openai.com/v1').strip().rstrip('/')
        self.api_key = (api_key or '').strip()
        self.model = (model or 'deepseek-v4.1-flash').strip()
        self.prompt = (prompt or DEFAULT_PROMPT).strip()

    def rewrite(self, title: str, content: str, timeout: int = 60) -> tuple[str, str]:
        """改写标题与正文，返回 (new_title, new_content)；原样返回当失败。"""
        if not self.api_key:
            raise AIError('未配置 API Key')
        try:
            resp = requests.post(
                f'{self.base}/responses',
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                json={
                    'model': self.model,
                    'instructions': self.prompt,
                    'input': f'原标题：{title}\n原文：{content}',
                    'temperature': 0.8,
                },
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise AIError(f'AI 接口连接失败：{exc}') from exc
        if resp.status_code in (401, 403):
            raise AIError('API Key 无效或无权限（HTTP %d）' % resp.status_code)
        if resp.status_code != 200:
            raise AIError(f'AI 接口返回 HTTP {resp.status_code}：{resp.text[:200]}')
        try:
            data = resp.json()
        except ValueError as exc:
            raise AIError('AI 接口响应不是 JSON') from exc
        if isinstance(data, dict) and data.get('error'):
            message = data['error']
            message = message.get('message') if isinstance(message, dict) else message
            raise AIError(f'AI 接口报错：{str(message)[:200]}')

        text = _extract_text(data)
        parsed = parse_rewrite_json(text)
        if not parsed:
            raise AIError('AI 输出无法解析为 JSON')
        new_title = str(parsed.get('title') or '').strip() or title
        new_content = str(parsed.get('content') or '').strip() or content
        return new_title, new_content

    def test(self) -> str:
        """连通性测试，返回模型回复摘要。"""
        title, content = self.rewrite('测试标题', '测试正文', timeout=30)
        return (content or title)[:50]
