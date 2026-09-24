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
    check('主窗口构建（5 个采集页签）', window.tabs.count() == 5)
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

    # 评论采集页签：模式解析、校验、选项显隐
    window.tabs.setCurrentIndex(3)
    check('评论模式解析 URL 列表',
          window._current_spec().mode == 'comments'
          and window._current_spec().comment_urls == [])
    check('评论模式空链接被拦截',
          '笔记链接' in window._validate_spec(window._current_spec()))
    window.comments_edit.setPlainText('https://www.xiaohongshu.com/explore/abc?xsec_token=x')
    window.excel_check.setChecked(True)
    check('评论模式校验通过', window._validate_spec(window._current_spec()) == '')
    # 窗口未 show()，isVisible() 恒为 False，这里用 isHidden() 判断显式显隐
    check('评论模式隐藏媒体/打包/AI 选项',
          window.img_check.isHidden() and window.video_check.isHidden()
          and window.zip_check.isHidden() and window.ai_check.isHidden()
          and not window.excel_check.isHidden())
    window.excel_check.setChecked(False)
    check('评论模式未勾 Excel 被拦截',
          '仅支持导出 Excel' in window._validate_spec(window._current_spec()))
    window.excel_check.setChecked(True)
    window.tabs.setCurrentIndex(0)
    check('切回搜索页恢复媒体选项',
          not window.img_check.isHidden() and not window.zip_check.isHidden()
          and not window.ai_check.isHidden())

    # 收藏采集页签：模式解析、类型切换、校验
    window.tabs.setCurrentIndex(4)
    spec = window._current_spec()
    check('收藏模式默认采集收藏',
          spec.mode == 'collect' and spec.collect_kind == 'collect')
    check('收藏模式空主页被拦截',
          '主页链接' in window._validate_spec(spec))
    window.collect_kind_combo.setCurrentIndex(1)
    check('收藏模式可切到赞过',
          window._current_spec().collect_kind == 'like')
    window.collect_user_edit.setText(
        'https://www.xiaohongshu.com/user/profile/abc123?xsec_token=t')
    spec = window._current_spec()
    check('收藏模式校验通过', window._validate_spec(spec) == '')
    check('收藏模式任务名带类型前缀', spec.task_name == '赞过abc123')
    check('收藏模式保留媒体选项', not window.img_check.isHidden())
    window.collect_kind_combo.setCurrentIndex(0)
    check('收藏模式任务名随类型变化',
          window._current_spec().task_name == '收藏abc123')
    window.tabs.setCurrentIndex(0)
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
    folder = '测试_笔记_A标题'
    check('文件夹名为纯标题（无ID前缀）', f'{folder}/文案.txt' in names, str(names))
    check('图片按 1.jpg/2.jpg 顺序命名', f'{folder}/1.jpg' in names and f'{folder}/2.jpg' in names)
    with zipfile_mod.ZipFile(zip_path) as zf:
        txt = zf.read(f'{folder}/文案.txt').decode('utf-8')
    check('文案.txt 内容为 desc', txt == '这是文案内容')

    # 同名标题自动加序号，避免 zip 内互相覆盖
    dup = [
        {'note_id': 'a' * 24, 'title': '同题', 'desc': '一', 'image_list': ['x']},
        {'note_id': 'b' * 24, 'title': '同题', 'desc': '二', 'image_list': ['x']},
    ]
    tmp2 = tempfile.mkdtemp()
    zip2 = export_mod.export_xiaolvsu_zip(dup, tmp2, '重名任务')
    with zipfile_mod.ZipFile(zip2) as zf:
        names2 = set(zf.namelist())
    check('重名标题第二个自动加 _2',
          f'同题/文案.txt' in names2 and f'同题_2/文案.txt' in names2, str(names2))
    with zipfile_mod.ZipFile(zip2) as zf:
        c1 = zf.read('同题/文案.txt').decode('utf-8')
        c2 = zf.read('同题_2/文案.txt').decode('utf-8')
    check('重名笔记内容各自保留', c1 == '一' and c2 == '二')

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


# ---------- 6b. 评论采集 ----------

def test_comment_collection():
    import tempfile

    from desktop.spider_service import TaskSpec, run_comment_collection

    RAW_COMMENT = {
        'id': 'cmt1',
        'note_id': 'note1',
        'user_info': {'user_id': 'u1', 'nickname': '小红', 'image': 'http://a/x.jpg'},
        'content': '这个真好看',
        'show_tags': [],
        'like_count': '3',
        'create_time': 1715518180000,
        'ip_location': '上海',
    }

    class _StubApi:
        """模拟 XHS_Apis：只实现评论采集用到的方法。"""

        def __init__(self, fail=False):
            self.fail = fail
            self.calls = []

        def get_note_all_comment(self, url, proxies=None, with_inner=True):
            self.calls.append((url, with_inner))
            if self.fail:
                return False, '触发风控', None
            raw = dict(RAW_COMMENT)
            raw['note_url'] = url
            return True, 'success', [raw]

    class _Emit:
        def __init__(self):
            self.logs = []
            self.steps = []

        def log(self, text):
            self.logs.append(str(text))

        def progress(self, done, total, stage):
            self.steps.append((done, total, stage))

    # 1) 解析器要求 note_url，未补键时必须抛错（这正是接线前会炸的原因）
    from xhs_utils.data_util import handle_comment_info
    try:
        handle_comment_info(dict(RAW_COMMENT))
        missing_raises = False
    except KeyError:
        missing_raises = True
    check('评论解析器缺 note_url 会抛 KeyError（已知坑）', missing_raises)

    # 2) api 层补键后可通过，且字段映射正确
    raw = dict(RAW_COMMENT)
    raw['note_url'] = 'https://www.xiaohongshu.com/explore/note1?xsec_token=t'
    parsed = handle_comment_info(raw)
    check('评论字段映射正确',
          parsed['comment_id'] == 'cmt1' and parsed['nickname'] == '小红'
          and parsed['content'] == '这个真好看' and parsed['ip_location'] == '上海'
          and parsed['note_url'].endswith('xsec_token=t'))

    # 3) 一级评论模式：不展开楼中楼
    import desktop.spider_service as svc
    orig = svc._spider_comments
    # 直接验证 with_inner=False 被传递：替换 XHS_Apis 构造
    tmp = tempfile.mkdtemp()
    spec = TaskSpec(mode='comments', comment_urls=['https://a/1'], output_dir=tmp,
                    task_name='t1', save_excel=True, delay_seconds=0)
    emit = _Emit()

    import apis.xhs_pc_apis as api_mod
    import xhs_utils.xhs_pc as pc_mod
    orig_auth, orig_api = pc_mod.XHSPcAuth, api_mod.XHS_Apis

    stub = _StubApi()
    pc_mod.XHSPcAuth = type('A', (), {'from_cookie': staticmethod(lambda c: object())})
    api_mod.XHS_Apis = lambda auth: stub
    try:
        summary = run_comment_collection([{'cookie': 'x'}], spec, lambda: False, emit)
    finally:
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = orig_auth, orig_api

    check('评论采集只取一级（with_inner=False）',
          stub.calls and all(c[1] is False for c in stub.calls))
    check('评论采集生成 Excel',
          summary['comments'] == 1 and summary['excel'].endswith('t1_评论.xlsx')
          and os.path.exists(summary['excel']))
    check('评论采集 summary 带 comments 字段', 'comments' in summary and 'zip' not in summary)

    # 4) 风控关键词触发冷却与熔断
    stub_fail = _StubApi(fail=True)
    spec_fail = TaskSpec(mode='comments', comment_urls=['https://a/1', 'https://a/2'],
                         output_dir=tempfile.mkdtemp(), task_name='t2', delay_seconds=0)
    emit_fail = _Emit()
    pc_mod.XHSPcAuth = type('A', (), {'from_cookie': staticmethod(lambda c: object())})
    api_mod.XHS_Apis = lambda auth: stub_fail
    try:
        # 把冷却时间压到 0，避免自检等 60 秒
        orig_cool = svc.RISK_COOLDOWN_SECONDS
        svc.RISK_COOLDOWN_SECONDS = 0
        try:
            run_comment_collection([{'cookie': 'x'}], spec_fail, lambda: False, emit_fail)
        finally:
            svc.RISK_COOLDOWN_SECONDS = orig_cool
    finally:
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = orig_auth, orig_api
    check('评论采集识别风控关键词',
          any('风控' in msg for msg in emit_fail.logs))
    check('评论采集无评论时不建 Excel',
          not any('Excel 已保存' in msg for msg in emit_fail.logs))


# ---------- 6b. 收藏采集 ----------

def test_collect_urls():
    from desktop.spider_service import TaskSpec, _note_urls_from_list, collect_note_urls

    # 字段齐全
    urls = _note_urls_from_list(
        [{'note_id': 'n1', 'xsec_token': 'tk1'}], '收藏')
    check('收藏列表拼出完整 URL',
          len(urls) == 1 and '/explore/n1' in urls[0] and 'xsec_token=tk1' in urls[0])

    # 缺字段跳过而非中断（赞过/收藏接口返回结构与用户作品接口不同）
    urls = _note_urls_from_list(
        [{'note_id': 'n1', 'xsec_token': 'tk1'},
         {'note_id': 'n2'},
         {'xsec_token': 'tk3'},
         {}], '赞过')
    check('缺 note_id/xsec_token 的条目被跳过', len(urls) == 1)

    # 兼容 id 字段命名差异
    urls = _note_urls_from_list([{'id': 'n9', 'xsec_token': 'tk9'}], '收藏')
    check('兼容 id 命名的笔记对象', len(urls) == 1 and '/explore/n9' in urls[0])

    # 空列表不报错
    check('空列表返回空', _note_urls_from_list([], '收藏') == [])

    # 模式分发
    class _StubApi:
        def __init__(self):
            self.called = None

        def get_user_all_collect_note_info(self, user_url, proxies=None):
            self.called = 'collect'
            return True, 'ok', [{'note_id': 'c1', 'xsec_token': 't1'}]

        def get_user_all_like_note_info(self, user_url, proxies=None):
            self.called = 'like'
            return True, 'ok', [{'note_id': 'l1', 'xsec_token': 't1'}]

    api = _StubApi()
    spec = TaskSpec(mode='collect', user_url='https://x/user/profile/u1?xsec_token=t',
                    collect_kind='collect')
    urls = collect_note_urls(api, spec)
    check('collect 模式调用收藏接口', api.called == 'collect' and '/explore/c1' in urls[0])

    spec.collect_kind = 'like'
    urls = collect_note_urls(api, spec)
    check('like 模式调用赞过接口', api.called == 'like' and '/explore/l1' in urls[0])


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


# ---------- 4b. 重名/ID 相关已并入 test_xiaolvsu_zip ----------


if __name__ == '__main__':
    test_auth_client()
    test_gui()
    test_task_spec()
    test_xiaolvsu_zip()
    test_ai_parse()
    test_comment_collection()
    test_collect_urls()
    test_should_collect()
    print()
    if FAILURES:
        print(f'自检失败 {len(FAILURES)} 项：{FAILURES}')
        sys.exit(1)
    print('全部自检通过')
