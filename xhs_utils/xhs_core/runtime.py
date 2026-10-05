"""Shared Node bridges for the single-source XHS Web algorithms."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Mapping


_JS_DIR = os.path.join(os.path.dirname(__file__), 'js')
_WEBSECTIGA_CLI = os.path.join(_JS_DIR, 'websectiga_cli.js')
_WEBSECTIGA_RE = re.compile(r'^[0-9a-f]{64}$', re.I)

# Windows 上打包为 GUI 程序（PyInstaller --windowed）后自身没有控制台，
# 此时创建子进程若不指定 CREATE_NO_WINDOW，系统会为每个子进程新分配一个
# 控制台窗口，表现为命令行窗口不停闪烁。非 Windows 平台取不到该常量、
# 回退为 0（即 subprocess 默认值），因此可以无条件传入。
_NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def run_node_json(
    argv: list[str],
    *,
    stdin_payload: Any = None,
    timeout: int = 30,
) -> dict:
    input_text = None
    if stdin_payload is not None:
        input_text = json.dumps(stdin_payload, ensure_ascii=False)
    try:
        proc = subprocess.run(
            argv,
            input=input_text,
            capture_output=True,
            text=True,
            cwd=os.path.dirname(argv[1]) if len(argv) > 1 else None,
            timeout=timeout,
            creationflags=_NO_WINDOW,
            # 传了 input 时 subprocess 会自建 stdin 管道，不能再指定。
            stdin=None if input_text is not None else subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f'Node runtime failed to start: {exc}') from exc
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or '').strip()
        # vm prints the entire one-line JSVMP source before the useful stack.
        # Keep the tail so callers see the actual first runtime failure.
        lines = message.splitlines()
        if len(lines) > 12:
            message = '\n'.join(lines[-12:])
        raise RuntimeError(f'Node runtime failed: {message[-2000:]}')
    output = (proc.stdout or '').strip().splitlines()
    if not output:
        raise RuntimeError('Node runtime returned no output')
    try:
        return json.loads(output[-1])
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'Node runtime returned invalid JSON: {output[-1][:300]}') from exc


def generate_websectiga(
    scripting_code: str,
    *,
    profile: Mapping[str, Any] | None = None,
) -> str:
    """Execute the server-issued scripting program in the minimal local host.

    This is browser-independent but is not a zero-host pure algorithm: XHS
    supplies the program at runtime.  The returned 64-hex Cookie is therefore
    classified separately from locally generated request parameters.
    """
    if not os.path.isfile(_WEBSECTIGA_CLI):
        raise RuntimeError(f'websectiga runtime missing: {_WEBSECTIGA_CLI}')
    config = dict(profile or {})
    result = run_node_json(
        ['node', _WEBSECTIGA_CLI],
        stdin_payload={
            'code': str(scripting_code or ''),
            'userAgent': str(config.get('userAgent') or ''),
            'platform': str(config.get('platform') or 'Win32'),
            'pageUrl': str(
                config.get('pageUrl')
                or 'https://creator.xiaohongshu.com/login'
            ),
        },
        timeout=30,
    )
    token = str(result.get('websectiga') or '')
    if not _WEBSECTIGA_RE.fullmatch(token):
        raise RuntimeError('websectiga runtime output invalid')
    return token.lower()


__all__ = ['run_node_json', 'generate_websectiga']
