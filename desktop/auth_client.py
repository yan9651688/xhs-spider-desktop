# encoding: utf-8
"""xiao 后端 /api/xhs/* 登录与有效期校验客户端。

对应 xiao 仓库 xhs-auth 分支的 XhsAuthController：
- POST /api/xhs/login  {username, password} -> {code:0, token, xhsExpireTime, ...}
- GET  /api/xhs/check  (X-Xhs-Token)        -> {code:0, xhsExpireTime}
- POST /api/xhs/logout (X-Xhs-Token)
code!=0 时 msg 为失败原因；401=登录失效，403=账号已过期。
"""
from __future__ import annotations

import requests

TOKEN_HEADER = 'X-Xhs-Token'


class AuthError(RuntimeError):
    def __init__(self, message: str, code=None):
        super().__init__(message)
        self.code = code


class AuthClient:
    def __init__(self, server: str):
        self.base = (server or '').strip().rstrip('/')

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f'{self.base}{path}'
        try:
            resp = requests.request(method, url, timeout=12, allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise AuthError(f'无法连接服务器（{self.base}），请检查网络') from exc
        if 300 <= resp.status_code < 400:
            raise AuthError('服务端尚未部署小红书采集接口，请联系管理员升级服务端')
        try:
            data = resp.json()
        except ValueError as exc:
            raise AuthError(
                f'服务器响应异常（HTTP {resp.status_code}），请联系管理员检查服务端'
            ) from exc
        if not isinstance(data, dict) or data.get('code') != 0:
            code = data.get('code') if isinstance(data, dict) else None
            msg = data.get('msg') if isinstance(data, dict) else '请求失败'
            raise AuthError(msg or '请求失败', code)
        return data

    def login(self, username: str, password: str) -> dict:
        data = self._request(
            'POST', '/api/xhs/login',
            json={'username': username, 'password': password},
        )
        return {
            'token': data.get('token'),
            'userId': data.get('userId'),
            'username': data.get('username') or username,
            'name': data.get('name'),
            'xhsExpireTime': data.get('xhsExpireTime'),
            'server': self.base,
        }

    def check(self, token: str) -> dict:
        return self._request('GET', '/api/xhs/check', headers={TOKEN_HEADER: token})

    def logout(self, token: str) -> None:
        try:
            self._request('POST', '/api/xhs/logout', headers={TOKEN_HEADER: token})
        except AuthError:
            pass
