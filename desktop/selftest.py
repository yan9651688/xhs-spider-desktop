# encoding: utf-8
"""桌面端自检：不依赖真实小红书/真实 xiao 服务器。

    .venv/bin/python -m desktop.selftest

内容：
1. 用本地桩服务器验证 AuthClient 登录/校验/过期/登出全链路（R 协议格式）。
2. QT_QPA_PLATFORM=offscreen 构建登录框与主窗口，验证 UI 装配无误。
3. TaskSpec 保存选项映射与任务名清洗。
"""
from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

FAILURES = []


def check(name: str, condition: bool, detail: str = ''):
    mark = 'PASS' if condition else 'FAIL'
    print(f'[{mark}] {name}' + (f'  {detail}' if detail and not condition else ''))
    if not condition:
        FAILURES.append(name)


# ---------- 1. 桩服务器 + AuthClient ----------

STUB_USERS = {
    'alice': {'password': 'pw-a', 'expire': None},
    'bob': {'password': 'pw-b', 'expire': '2000-01-01 00:00:00'},
}
STUB_TOKENS = {}


class StubHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def _send(self, payload: dict):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        data = json.loads(self.rfile.read(length) or b'{}') if length else {}
        if self.path == '/api/xhs/login':
            user = STUB_USERS.get(data.get('username'))
            if not user or data.get('password') != user['password']:
                return self._send({'code': 500, 'msg': '账号或密码错误'})
            if user['expire'] and user['expire'] < '2999-01-01':
                return self._send({'code': 500, 'msg': f"账号已过期，有效期至 {user['expire']}"})
            token = 'tok-' + data['username']
            STUB_TOKENS[token] = data['username']
            return self._send({
                'code': 0, 'msg': 'ok', 'token': token,
                'userId': 1, 'username': data['username'],
                'name': '用户' + data['username'],
                'xhsExpireTime': user['expire'] or '永久',
            })
        if self.path == '/api/xhs/logout':
            STUB_TOKENS.pop(self.headers.get('X-Xhs-Token', ''), None)
            return self._send({'code': 0, 'msg': 'ok'})
        self._send({'code': 404, 'msg': 'not found'})

    def do_GET(self):
        if self.path == '/api/xhs/check':
            token = self.headers.get('X-Xhs-Token', '')
            if token not in STUB_TOKENS:
                return self._send({'code': 401, 'msg': '登录已失效，请重新登录'})
            return self._send({'code': 0, 'msg': 'ok', 'xhsExpireTime': '永久'})
        self._send({'code': 404, 'msg': 'not found'})


def test_auth_client():
    from desktop.auth_client import AuthClient, AuthError

    server = ThreadingHTTPServer(('127.0.0.1', 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    client = AuthClient(base)

    session = client.login('alice', 'pw-a')
    check('登录成功返回 token', session['token'] == 'tok-alice')
    check('登录返回有效期(永久)', session['xhsExpireTime'] == '永久')

    data = client.check(session['token'])
    check('check 通过', data.get('code') == 0 and data.get('xhsExpireTime') == '永久')

    try:
        client.login('alice', 'wrong')
        check('错误密码被拒绝', False)
    except AuthError as exc:
        check('错误密码被拒绝', '账号或密码错误' in str(exc), str(exc))

    try:
        client.login('bob', 'pw-b')
        check('过期账号被拒绝', False)
    except AuthError as exc:
        check('过期账号被拒绝', '已过期' in str(exc), str(exc))

    try:
        client.check('tok-unknown')
        check('无效 token check 被拒', False)
    except AuthError as exc:
        check('无效 token check 被拒', exc.code == 401, str(exc))

    client.logout(session['token'])
    try:
        client.check(session['token'])
        check('登出后 token 失效', False)
    except AuthError as exc:
        check('登出后 token 失效', exc.code == 401, str(exc))

    try:
        AuthClient('http://127.0.0.1:1').login('a', 'b')
        check('服务器不可达报 AuthError', False)
    except AuthError as exc:
        check('服务器不可达报 AuthError', '无法连接服务器' in str(exc))
    server.shutdown()


# ---------- 2. 无头 GUI 装配 ----------

def test_gui():
    from PySide6.QtWidgets import QApplication, QTabWidget

    from desktop import paths as dpaths
    from desktop.login_dialog import LoginDialog
    from desktop.main_window import MainWindow

    dpaths.load_xhs_cookies = lambda: [{'cookie': 'web_session=selftest', 'nickname': '测试号'}]

    app = QApplication.instance() or QApplication([])
    login = LoginDialog({'server': 'http://demo', 'username': 'alice'})
    check('登录框构建', login.windowTitle() != '')
    login.deleteLater()

    window = MainWindow(
        {'server': 'http://demo', 'username': 'alice', 'output_dir': os.getcwd()},
        {'token': 't', 'username': 'alice', 'name': 'Alice', 'xhsExpireTime': '永久'},
    )
    check('主窗口构建（3 个采集页签）', window.tabs.count() == 3)
    spec = window._current_spec()
    check('默认任务模式为 search', spec.mode == 'search' and spec.query == '')
    window.tabs.setCurrentIndex(1)
    window.urls_edit.setPlainText('https://www.xiaohongshu.com/explore/abc?xsec_token=x')
    spec = window._current_spec()
    check('链接模式解析 URL', spec.mode == 'urls' and len(spec.note_urls) == 1)
    window.auth = object()  # 模拟已登录小红书，校验后续规则
    check('链接模式校验通过', window._validate_spec(spec) == '')
    window.tabs.setCurrentIndex(0)
    check('搜索模式缺关键词被拦截', '关键词' in window._validate_spec(window._current_spec()))
    window.deleteLater()
    app.processEvents()


# ---------- 3. 任务参数 ----------

def test_task_spec():
    from desktop.spider_service import TaskSpec, sanitize_name

    spec = TaskSpec(save_images=True, save_videos=True)
    check('双选保存映射为 media', spec.save_choice == 'media')
    spec = TaskSpec(save_images=True, save_videos=False)
    check('仅图片映射为 media-image', spec.save_choice == 'media-image')
    spec = TaskSpec(save_images=False, save_videos=False, save_excel=True)
    check('不保存媒体时 save_choice 为空', spec.save_choice == '' and not spec.want_media)
    check('小绿书打包默认开启', TaskSpec().zip_export is True)
    check('任务名清洗非法字符', sanitize_name('a/b:c*?') == 'a_b_c_')
    check('空任务名回退时间戳', len(sanitize_name('   ')) >= 8)


# ---------- 4. 小绿书 zip 导出 ----------

def test_xiaolvsu_zip():
    import io
    import tempfile
    import zipfile as zipfile_mod

    from PIL import Image

    from desktop.xhs_export import export_xiaolvsu_zip

    tmp = tempfile.mkdtemp()
    img_buf = io.BytesIO()
    Image.new('RGB', (30, 30), (200, 60, 60)).save(img_buf, 'PNG')

    notes = [
        {
            'note_id': '683fe17f0000000023017c6a',
            'title': '测试 笔记/A标题',
            'desc': '这是文案内容',
            'image_list': [f'file://{img_buf.name}' if False else None],
        },
    ]
    # 用本地文件 URL 不可行，改为 monkeypatch 下载函数
    import desktop.xhs_export as export_mod

    def fake_download(url, dest_dir, index):
        with open(f'{dest_dir}/{index}.jpg', 'wb') as f:
            f.write(img_buf.getvalue())
        return True

    export_mod._download_as_jpg = fake_download
    notes[0]['image_list'] = ['x://a', 'x://b']

    zip_path = export_mod.export_xiaolvsu_zip(notes, tmp, '测试任务')
    check('zip 已生成', bool(zip_path) and zip_path.endswith('.zip'))
    with zipfile_mod.ZipFile(zip_path) as zf:
        names = zf.namelist()
    folder = '683fe17f0000000023017c6a测试_笔记_A标题'
    check('zip 内文件夹名为 note_id+清洗标题', f'{folder}/文案.txt' in names, str(names))
    check('图片按 1.jpg/2.jpg 顺序命名', f'{folder}/1.jpg' in names and f'{folder}/2.jpg' in names)
    with zipfile_mod.ZipFile(zip_path) as zf:
        txt = zf.read(f'{folder}/文案.txt').decode('utf-8')
    check('文案.txt 内容为 desc', txt == '这是文案内容')

    # 无图笔记应被跳过
    no_img = [{'note_id': 'a' * 24, 'title': '无图', 'desc': 'x', 'image_list': []}]
    check('无图笔记返回 None', export_mod.export_note_folder(no_img[0], tmp) is None)


# ---------- 5. AI 改写解析 ----------

def test_ai_parse():
    from desktop.ai_client import parse_rewrite_json

    data = parse_rewrite_json('{"title": "新标题", "content": "新内容"}')
    check('解析纯 JSON', data.get('title') == '新标题' and data.get('content') == '新内容')
    data = parse_rewrite_json('好的，以下是改写结果：\n{"title": "T2", "content": "C2"}\n完毕')
    check('解析夹带文字的 JSON', data.get('title') == 'T2')
    data = parse_rewrite_json('模型抽风输出无 JSON')
    check('解析失败返回空', data == {})


# ---------- 6. 类型过滤 ----------

def test_should_collect():
    from desktop.spider_service import TaskSpec, _should_collect

    both = TaskSpec(save_images=True, save_videos=True)
    no_video = TaskSpec(save_images=True, save_videos=False)
    no_image = TaskSpec(save_images=False, save_videos=True)

    check('双选：图文/视频都采集',
          _should_collect({'note_type': '图集'}, both)
          and _should_collect({'note_type': '视频'}, both))
    check('未勾视频：视频笔记被排除',
          _should_collect({'note_type': '视频'}, no_video) is False)
    check('未勾视频：图文笔记仍采集', _should_collect({'note_type': '图集'}, no_video))
    check('未勾图片：图文笔记被排除',
          _should_collect({'note_type': '图集'}, no_image) is False)
    check('未勾图片：视频笔记仍采集', _should_collect({'note_type': '视频'}, no_image))


if __name__ == '__main__':
    test_auth_client()
    test_gui()
    test_task_spec()
    test_xiaolvsu_zip()
    test_ai_parse()
    test_should_collect()
    print()
    if FAILURES:
        print(f'自检失败 {len(FAILURES)} 项：{FAILURES}')
        sys.exit(1)
    print('全部自检通过')
