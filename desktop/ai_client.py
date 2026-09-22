# encoding: utf-8
"""AI 改写客户端：OpenAI Responses API（/v1/responses）格式，兼容各类 OpenAI 兼容网关。

标题与文案各自独立提示词、独立调用，互不影响；单个失败保留该项原文。
"""
from __future__ import annotations

import json
import re

import requests

TITLE_PROMPT_PRESET = (
    '你是小红书爆款标题写手。基于给出的原标题，改写一个全新的小红书风格标题：'
    '20字以内，有网感、带悬念或利益点，可加1-2个emoji，保留核心卖点，不虚构事实。'
    '只输出改写后的标题文字本身，不要解释、不要引号、不要JSON。'
)

CONTENT_PROMPT_PRESET = (
    '你是小红书爆款文案写手。基于给出的原文案，改写出全新的小红书风格正文：'
    '150-250字，口语化、分段清晰（用换行分隔），结尾加4个#话题标签，'
    '保留原文核心信息与卖点，不虚构事实。'
    '只输出改写后的正文文字本身，不要解释、不要JSON。'
)


class AIError(RuntimeError):
    pass


class _EndpointUnsupported(Exception):
    """网关不支持 /responses 端点，需降级 chat/completions。"""


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


def _clean_plain(text: str) -> str:
    """清理模型输出的包裹符号。"""
    return (text or '').strip().strip('"').strip('“”').strip()


class AIClient:
    def __init__(self, base: str, api_key: str, model: str,
                 title_prompt: str = '', content_prompt: str = ''):
        self.base = (base or 'https://api.openai.com/v1').strip().rstrip('/')
        self.api_key = (api_key or '').strip()
        self.model = (model or 'deepseek-v4.1-flash').strip()
        self.title_prompt = (title_prompt or '').strip() or TITLE_PROMPT_PRESET
        self.content_prompt = (content_prompt or '').strip() or CONTENT_PROMPT_PRESET

    def _call(self, instructions: str, user_input: str, timeout: int = 60) -> str:
        if not self.api_key:
            raise AIError('未配置 API Key')
        try:
            return self._call_responses(instructions, user_input, timeout)
        except _EndpointUnsupported:
            return self._call_chat(instructions, user_input, timeout)

    def _headers(self) -> dict:
        return {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
        }

    def _check_common_error(self, resp) -> None:
        if resp.status_code in (401, 403):
            raise AIError('API Key 无效或无权限（HTTP %d）' % resp.status_code)
        if resp.status_code >= 500:
            raise AIError(
                f'AI 网关故障（HTTP {resp.status_code}），请稍后再试；'
                f'若持续出现请联系管理员检查网关渠道。详情：{resp.text[:120]}'
            )

    def _call_responses(self, instructions: str, user_input: str, timeout: int) -> str:
        resp = requests.post(
            f'{self.base}/responses',
            headers=self._headers(),
            json={
                'model': self.model,
                'instructions': instructions,
                'input': user_input,
                'temperature': 0.8,
            },
            timeout=timeout,
        )
        if resp.status_code in (404, 405, 501) or (
                resp.status_code == 400 and
                ('support' in resp.text.lower() or '不支持' in resp.text)):
            raise _EndpointUnsupported()
        self._check_common_error(resp)
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
        return _extract_text(data)

    def _call_chat(self, instructions: str, user_input: str, timeout: int) -> str:
        """网关不支持 /responses 时自动降级 chat/completions。"""
        resp = requests.post(
            f'{self.base}/chat/completions',
            headers=self._headers(),
            json={
                'model': self.model,
                'messages': [
                    {'role': 'system', 'content': instructions},
                    {'role': 'user', 'content': user_input},
                ],
                'temperature': 0.8,
            },
            timeout=timeout,
        )
        self._check_common_error(resp)
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
        try:
            return str(data['choices'][0]['message']['content'] or '')
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError('AI 接口响应缺少正文') from exc

    def rewrite_title(self, title: str, timeout: int = 60) -> str:
        """改写标题；返回改写文本，失败抛 AIError。"""
        text = self._call(self.title_prompt, f'原标题：{title}', timeout)
        data = parse_rewrite_json(text)
        result = _clean_plain(str(data.get('title') or '')) or _clean_plain(text)
        if not result:
            raise AIError('AI 标题输出为空')
        return result

    def rewrite_content(self, content: str, timeout: int = 60) -> str:
        """改写正文；返回改写文本，失败抛 AIError。"""
        text = self._call(self.content_prompt, f'原文案：{content}', timeout)
        data = parse_rewrite_json(text)
        result = _clean_plain(str(data.get('content') or '')) or _clean_plain(text)
        if not result:
            raise AIError('AI 文案输出为空')
        return result

    def test(self) -> str:
        """连通性测试：跑一次标题改写，返回模型输出摘要。"""
        sample = self.rewrite_title('手工轻奢黄金小众耳环', timeout=30)
        return sample[:30]
