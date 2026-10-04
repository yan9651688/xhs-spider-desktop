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


# ---------- 0. 账号巡检纯逻辑（无 Qt / 无网络 / 无 Node） ----------

STUB_ME = {
    'nickname': '金饰小铺',
    'red_id': 'abc123',
    'user_id': 'u1',
    'imageb': 'http://cdn/avatar.jpg',
    'guest': False,
    'gender': 1,
    'desc': '珠宝饰品',
}

STUB_INFO = {
    'basic_info': {
        'nickname': '金饰小铺',
        'red_id': 'abc123',
        'imageb': 'http://cdn/avatar.jpg',
        'images': 'http://cdn/avatar.jpg',
        'ip_location': '上海',
        'desc': '珠宝饰品',
        'gender': 1,
    },
    # 故意乱序：台账必须按 type/name 定位，不能按下标硬取
    'interactions': [
        {'name': '获赞与收藏', 'count': '1801', 'type': 'interaction'},
        {'type': 'fans', 'name': '粉丝', 'count': '203'},
        {'type': 'follows', 'name': '关注', 'count': '92'},
    ],
    'posted': 150,
    'liked': 1529,
    'collected': 272,
    'tags': [{'name': '珠宝'}, {'name': ''}, '翡翠'],
}


class _StubAuth:
    def __init__(self, fail=None):
        self.fail = fail
        self.user_id = 'u1'

    def close(self):
        pass


class _StubApi:
    def __init__(self, me=None, info=None, fail=None, guest=False):
        self.me = me
        self.info = info
        self.fail = fail
        self.guest = guest

    def get_user_me(self, proxies=None):
        if self.fail == 'me':
            raise TimeoutError('ReadTimeout')
        data = dict(self.me or {})
        data['guest'] = self.guest
        return True, '成功', {'data': data}

    def get_user_info(self, user_id, proxies=None):
        if self.fail == 'info':
            return False, '461 风控拦截', None
        return True, '成功', {'data': self.info}


def _patch_probe(me=None, info=None, fail=None, guest=False, auth_exc=None):
    """猴子补丁巡检依赖的两个类，返回还原用的原值。"""
    import apis.xhs_pc_apis as api_mod
    import xhs_utils.xhs_pc as pc_mod

    def _from_cookie(_cookie):
        if auth_exc is not None:
            raise auth_exc
        return _StubAuth(fail)

    originals = (pc_mod.XHSPcAuth, api_mod.XHS_Apis)
    pc_mod.XHSPcAuth = type('XHSPcAuth', (), {'from_cookie': staticmethod(_from_cookie)})
    api_mod.XHS_Apis = lambda auth: _StubApi(me, info, fail, guest)
    return originals


def test_account_probe():
    from desktop import account_probe as probe
    from desktop import paths

    originals = _patch_probe(STUB_ME, STUB_INFO)
    try:
        result = probe.probe_account('web_session=x', full=True, download_avatar=False)
        check('巡检正常态判为 ok', result['health'] == paths.HEALTH_OK,
              f"health={result['health']}")
        check('巡检解析粉丝/关注/获赞（乱序 interactions）',
              result.get('fans') == 203 and result.get('follows') == 92
              and result.get('interaction') == 1801, str(result)[:200])
        check('巡检解析发布/赞过/收藏',
              result.get('posted') == 150 and result.get('liked') == 1529
              and result.get('collected') == 272)
        check('巡检解析 IP 属地与标签',
              result.get('ip_location') == '上海' and result.get('tags') == ['珠宝', '翡翠'],
              str(result.get('tags')))
        check('巡检解析性别与小红书号',
              result.get('gender') == '女' and result.get('red_id') == 'abc123')
        check('巡检带出 user_id 与昵称',
              result.get('user_id') == 'u1' and result.get('nickname') == '金饰小铺')
    finally:
        import apis.xhs_pc_apis as api_mod
        import xhs_utils.xhs_pc as pc_mod
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = originals

    originals = _patch_probe(STUB_ME, STUB_INFO, guest=True)
    try:
        result = probe.probe_account('web_session=x', full=True, download_avatar=False)
        check('guest=True 判为登录失效（关键：success 仍为 true）',
              result['health'] == paths.HEALTH_EXPIRED, f"health={result['health']}")
    finally:
        import apis.xhs_pc_apis as api_mod
        import xhs_utils.xhs_pc as pc_mod
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = originals

    originals = _patch_probe(STUB_ME, STUB_INFO, fail='me')
    try:
        result = probe.probe_account('web_session=x', full=True, download_avatar=False)
        check('请求超时判为网络异常', result['health'] == paths.HEALTH_NETWORK,
              f"health={result['health']}")
    finally:
        import apis.xhs_pc_apis as api_mod
        import xhs_utils.xhs_pc as pc_mod
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = originals

    originals = _patch_probe(STUB_ME, STUB_INFO, auth_exc=ValueError(
        'XHSPcAuth.cookies must contain a1; use a saved local login Cookie'))
    try:
        result = probe.probe_account('bad', full=True, download_avatar=False)
        check('Cookie 缺 a1 判为登录失效', result['health'] == paths.HEALTH_EXPIRED,
              f"health={result['health']}")
    finally:
        import apis.xhs_pc_apis as api_mod
        import xhs_utils.xhs_pc as pc_mod
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = originals

    originals = _patch_probe(STUB_ME, STUB_INFO, fail='info')
    try:
        result = probe.probe_account('web_session=x', full=True, download_avatar=False)
        check('资产接口失败不改健康状态（身份已确认）',
              result['health'] == paths.HEALTH_OK, f"health={result['health']}")
        check('资产接口失败留下备注', '资产数据获取失败' in result.get('health_note', ''))
    finally:
        import apis.xhs_pc_apis as api_mod
        import xhs_utils.xhs_pc as pc_mod
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = originals

    originals = _patch_probe(STUB_ME, {})
    try:
        result = probe.probe_account('web_session=x', full=True, download_avatar=False)
        check('资产接口返回空 dict 不抛 KeyError',
              result['health'] == paths.HEALTH_OK and result.get('fans') == 0
              and result.get('posted') == 0)
    finally:
        import apis.xhs_pc_apis as api_mod
        import xhs_utils.xhs_pc as pc_mod
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = originals

    originals = _patch_probe(STUB_ME, STUB_INFO)
    try:
        result = probe.probe_account('web_session=x', full=False, download_avatar=False)
        check('快速模式只验会话、不带资产',
              result['health'] == paths.HEALTH_OK and 'fans' not in result)
        check('空 Cookie 直接判失效',
              probe.probe_account('', full=True)['health'] == paths.HEALTH_EXPIRED)
    finally:
        import apis.xhs_pc_apis as api_mod
        import xhs_utils.xhs_pc as pc_mod
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = originals

    # parse_user_info 纯函数：接口结构变化时的容错
    parsed = probe.parse_user_info({}, 'u9')
    check('parse_user_info 空输入不抛异常',
          parsed['fans'] == 0 and parsed['nickname'] == '' and parsed['user_id'] == 'u9')
    check('_as_int 解析 K/万 后缀',
          probe._as_int('1.8K') == 1800 and probe._as_int('2万') == 20000
          and probe._as_int(None) == 0 and probe._as_int(True) == 0,
          f"{probe._as_int('1.8K')}/{probe._as_int('2万')}")
    check('classify_message 区分风控与网络',
          probe.classify_message('461 风控') == paths.HEALTH_LIMITED
          and probe.classify_message('connection reset') == paths.HEALTH_NETWORK)


def test_account_ledger(tmp_dir=None):
    """台账读写：用临时目录替换路径常量，绝不碰真实 ~/.xhs_spider。"""
    import tempfile
    from pathlib import Path

    from desktop import paths

    saved = (paths.ACCOUNTS_FILE, paths.AVATAR_DIR, paths.COOKIES_FILE)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths.ACCOUNTS_FILE = root / 'xhs_accounts.json'
        paths.AVATAR_DIR = root / 'avatars'
        paths.COOKIES_FILE = root / 'xhs_cookies.json'
        try:
            check('台账文件不存在时返回空结构',
                  paths.load_accounts() == {'version': 1, 'accounts': {}, 'history': {}})

            paths.save_xhs_cookies([
                {'cookie': 'a1=1; web_session=s1', 'nickname': '甲'},
                {'cookie': 'a1=1; web_session=s2', 'nickname': '乙'},
            ])
            ledger = paths.sync_accounts_from_pool()
            check('同步池 -> 台账补空条目', len(ledger['accounts']) == 2)
            check('新条目健康状态为未巡检',
                  ledger['accounts']['s1']['health'] == paths.HEALTH_UNKNOWN)

            record = paths.upsert_account(
                's1', health=paths.HEALTH_OK, user_id='u1', nickname='甲',
                fans=100, follows=5, interaction=999, posted=7)
            check('upsert 写入资产字段', record['fans'] == 100 and record['posted'] == 7)
            check('upsert 记录巡检时间', bool(record['checked_at']))
            check('upsert 写入历史快照',
                  paths.load_accounts()['history'].get('u1', [{}])[0].get('fans') == 100)

            # 空值不覆盖已有值（快速模式不刷新资产，不该把粉丝清零）
            record = paths.upsert_account('s1', health=paths.HEALTH_OK, nickname='')
            check('空字段不覆盖已有值', record['fans'] == 100 and record['nickname'] == '甲')

            # 同一天重复巡检只留一条历史
            paths.upsert_account('s1', health=paths.HEALTH_OK, user_id='u1', fans=120)
            history = paths.load_accounts()['history']['u1']
            check('同一天历史只留一条且取最新值',
                  len(history) == 1 and history[0]['fans'] == 120, str(history))

            record = paths.upsert_account('s2', health=paths.HEALTH_NETWORK)
            check('失败累计次数据递增', record['consecutive_failures'] == 1)
            record = paths.upsert_account('s2', health=paths.HEALTH_OK)
            check('恢复后失败计数归零', record['consecutive_failures'] == 0)

            paths.save_xhs_cookies([{'cookie': 'a1=1; web_session=s2', 'nickname': '乙'}])
            ledger = paths.sync_accounts_from_pool()
            check('池里删掉的账号从台账移除', 's1' not in ledger['accounts'])
            check('移除账号连带清历史', 'u1' not in paths.load_accounts()['history'])

            check('按主键取回 cookie',
                  paths.account_cookie('s2') == 'a1=1; web_session=s2')
            check('主键回退到 Cookie 池',
                  paths.account_cookie('s2') != '' and paths.account_key_of(
                      {'cookie': 'a1=1; web_session=s2'}) == 's2')

            saved_path = paths.save_avatar('u2', b'\xff\xd8\xff\xe0fake')
            check('头像写入缓存目录', bool(saved_path) and paths.avatar_path('u2').exists())
            paths.upsert_account('s2', health=paths.HEALTH_OK, user_id='u2')
            paths.remove_accounts(['s2'])
            check('删除台账条目连带删头像', not paths.avatar_path('u2').exists())
            check('删除台账条目连带清历史',
                  'u2' not in paths.load_accounts()['history'])

            paths.save_accounts({'version': 1, 'accounts': 'garbage', 'history': None})
            check('台账结构损坏时不抛异常', paths.load_accounts()['accounts'] == {})
        finally:
            paths.ACCOUNTS_FILE, paths.AVATAR_DIR, paths.COOKIES_FILE = saved


# ---------- 0b. 对标账号清单（纯数据层） ----------

def test_watchlist(tmp_dir=None):
    """对标清单读写：临时目录替换路径常量，绝不碰真实 ~/.xhs_spider。"""
    import tempfile
    from pathlib import Path

    from desktop import paths, watchlist

    saved = (paths.WATCHLIST_FILE, paths.AVATAR_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        paths.WATCHLIST_FILE = root / 'xhs_watchlist.json'
        paths.AVATAR_DIR = root / 'avatars'
        try:
            check('清单文件不存在时返回空结构',
                  watchlist.load_watchlist() == {'version': 1, 'targets': {}})

            # --- parse_target：主页链接 / 裸 id / 空串 ---
            check('解析主页链接（带 query）',
                  watchlist.parse_target(
                      'https://www.xiaohongshu.com/user/profile/665c7da900000000030308ce'
                      '?xsec_token=AB&xsec_source=pc_search')
                  == '665c7da900000000030308ce')
            check('解析主页链接（尾斜杠）',
                  watchlist.parse_target(
                      'https://www.xiaohongshu.com/user/profile/665c7da900000000030308ce/')
                  == '665c7da900000000030308ce')
            check('解析裸 user_id', watchlist.parse_target('665c7da900000000030308ce')
                  == '665c7da900000000030308ce')
            check('解析空串返回空', watchlist.parse_target('') == ''
                  and watchlist.parse_target('   ') == '')
            check('解析非 user_id 文本返回空',
                  watchlist.parse_target('一棠耳饰') == ''
                  and watchlist.parse_target('https://www.xiaohongshu.com/explore/abc') == '')

            # --- upsert：空值不覆盖 ---
            watchlist.upsert_target('u1', nickname='一棠耳饰', fans='4.2万',
                                    health=paths.HEALTH_OK)
            record = watchlist.upsert_target('u1', nickname='')
            check('对标号空字段不覆盖已有值',
                  record['nickname'] == '一棠耳饰' and record['fans'] == '4.2万')
            check('对标号主键是 user_id 而非 web_session',
                  'u1' in watchlist.load_watchlist()['targets'])

            # --- diff_new_notes：剔除置顶 + 按 seen 过滤 ---
            notes = [
                {'note_id': 'sticky', 'time': 1000,
                 'interact_info': {'sticky': True}},      # 置顶：永远不算新
                {'note_id': 'n1', 'time': 3000, 'interact_info': {}},
                {'note_id': 'n2', 'time': 2000, 'interact_info': {}},
                {'note_id': 'seen1', 'time': 1500, 'interact_info': {}},
            ]
            fresh = watchlist.diff_new_notes(notes, {'seen1'}, 0)
            check('置顶笔记不被当成新笔记',
                  'sticky' not in [n['note_id'] for n in fresh], str(fresh))
            check('已见过的笔记不被当成新笔记',
                  [n['note_id'] for n in fresh] == ['n1', 'n2'], str(fresh))
            fresh = watchlist.diff_new_notes(notes, {'seen1'}, 2500)
            check('早于基线的笔记被过滤',
                  [n['note_id'] for n in fresh] == ['n1'], str(fresh))
            check('空列表返回空', watchlist.diff_new_notes([], set(), 0) == [])

            # --- add_note 幂等 ---
            ok = watchlist.add_note('u1', {'note_id': 'n1', 'title': 'A', 'time': 3000})
            check('登记新笔记成功', ok is True)
            ok = watchlist.add_note('u1', {'note_id': 'n1', 'title': 'A', 'time': 3000})
            check('重复登记同一笔记返回 False',
                  ok is False and watchlist.pending_count(
                      watchlist.target_of('u1')) == 1)

            # --- mark_seen / 裁剪 ---
            watchlist.mark_seen('u2', [f'x{i}' for i in range(350)])
            record = watchlist.target_of('u2')
            check('seen 裁到上限', len(record['seen']) == watchlist.SEEN_LIMIT,
                  str(len(record['seen'])))
            check('裁剪丢弃数被统计', record['seen_overflow'] == 50,
                  str(record.get('seen_overflow')))
            check('裁剪保留最新（列表头部）', record['seen'][0] == 'x0',
                  record['seen'][0])

            # --- drop_notes：采集后移除，但 seen 保留防重复登记 ---
            watchlist.drop_notes('u1', ['n1'])
            check('移除待处理笔记后 pending 归零',
                  watchlist.pending_count(watchlist.target_of('u1')) == 0)
            check('移除待处理笔记后 seen 仍保留（防重复登记）',
                  watchlist.add_note('u1', {'note_id': 'n1', 'time': 3000}) is False)

            # --- remove_targets ---
            watchlist.remove_targets(['u1'])
            check('移除对标号', 'u1' not in watchlist.load_watchlist()['targets'])
            watchlist.remove_targets([])
            check('移除空列表不报错', True)

            # --- 结构损坏 ---
            paths.WATCHLIST_FILE.write_text('{"targets": "garbage"}', encoding='utf-8')
            check('清单结构损坏时不抛异常',
                  watchlist.load_watchlist()['targets'] == {})
        finally:
            paths.WATCHLIST_FILE, paths.AVATAR_DIR = saved


# ---------- 0c. 对标账号探测（桩 XHS_Apis，无网络） ----------

class _StubWatchApi:
    """桩：按预设返回 get_user_note_info / get_user_info，并记录调用次数。"""

    def __init__(self, pages=None, user_info=None, fail=None):
        self.pages = list(pages or [])
        self.user_info = user_info
        self.fail = fail                 # ('msg', 值) 或 ('exc', 异常)
        self.calls = []

    def get_user_note_info(self, user_id, cursor='', xsec_token='', xsec_source=''):
        self.calls.append(('notes', cursor))
        if self.fail and self.fail[0] == 'notes':
            value = self.fail[1]
            if isinstance(value, Exception):
                raise value
            return False, value, None
        index = 0 if not cursor else 1
        page = self.pages[index] if index < len(self.pages) else {'notes': [], 'has_more': False}
        return True, '成功', {'data': page}

    def get_user_info(self, user_id):
        self.calls.append(('info', ''))
        if self.fail and self.fail[0] == 'info':
            value = self.fail[1]
            if isinstance(value, Exception):
                raise value
            return False, value, None
        return True, '成功', {'data': self.user_info or {}}


def _note(note_id, time_ms, sticky=False, title='标题'):
    return {'note_id': note_id, 'display_title': title, 'type': 'normal',
            'time': time_ms, 'xsec_token': 'tok-' + note_id,
            'interact_info': {'liked_count': '12', 'sticky': sticky}}


def test_watch_probe():
    from desktop import paths, watch_probe

    check('列表条目解析缺字段不抛异常',
          watch_probe.parse_note_list_item({}) == {}
          and watch_probe.parse_note_list_item(
              {'note_id': 'n1'})['liked'] == 0)
    check('列表条目解析点赞数（字符串）',
          watch_probe.parse_note_list_item(_note('n1', 1000))['liked'] == 12)
    check('笔记 URL 带 xsec_token',
          watch_probe.note_url({'note_id': 'n1', 'xsec_token': 'tk'})
          == 'https://www.xiaohongshu.com/explore/n1?xsec_token=tk&xsec_source=pc_user')
    check('笔记 URL 缺 token 时退化为裸链接',
          watch_probe.note_url({'note_id': 'n1'})
          == 'https://www.xiaohongshu.com/explore/n1')

    # --- 首次：建立基线，不报新笔记 ---
    api = _StubWatchApi(pages=[{
        'notes': [_note(f'n{i}', 5000 - i, sticky=(i == 0)) for i in range(30)],
        'has_more': True, 'cursor': 'c1',
    }])
    result = watch_probe.probe_target(api, 'u1', {})
    check('首次检查判为正常', result['health'] == paths.HEALTH_OK, str(result)[:160])
    check('首次检查不把历史当新笔记', result['new_notes'] == [])
    check('首次检查建立基线（30 条进入 seen）', len(result['scanned_ids']) == 30)
    check('首次检查不补翻第二页', len([c for c in api.calls if c[0] == 'notes']) == 1,
          str(api.calls))
    check('基线取首个非置顶笔记', result['baseline_note_id'] == 'n1',
          result.get('baseline_note_id'))
    check('首次检查备注说明已建基线', '基线' in result['health_note'])

    # --- 非首次：只报基线之后的新笔记 ---
    record = {'baseline_note_id': 'n9', 'baseline_time': 4991, 'seen': ['n1', 'n2', 'n9']}
    api = _StubWatchApi(pages=[{
        'notes': [_note('new2', 6000), _note('new1', 5500), _note('n9', 4991)],
        'has_more': False, 'cursor': '',
    }])
    result = watch_probe.probe_target(api, 'u2', record)
    check('非首次只报新笔记',
          [n['note_id'] for n in result['new_notes']] == ['new2', 'new1'],
          str(result['new_notes']))
    check('非首次 pending 数与新笔记一致', result['pending'] == 2)

    # --- 补页：一页装不下时带 cursor 再请求一次 ---
    record = {'baseline_note_id': 'old', 'baseline_time': 100, 'seen': []}
    api = _StubWatchApi(pages=[
        {'notes': [_note(f'a{i}', 9000 - i) for i in range(30)],
         'has_more': True, 'cursor': 'c1'},
        {'notes': [_note('old', 100)], 'has_more': False, 'cursor': ''},
    ])
    result = watch_probe.probe_target(api, 'u3', record)
    check('本页未触及基线时补翻第二页',
          len([c for c in api.calls if c[0] == 'notes']) == 2, str(api.calls))
    check('补页后命中基线即停止', result['health'] == paths.HEALTH_OK)

    # --- 失败：绝不推进 seen / baseline ---
    api = _StubWatchApi(fail=('notes', '461 风控'))
    result = watch_probe.probe_target(api, 'u4', {})
    check('列表返回风控判为限流', result['health'] == paths.HEALTH_LIMITED,
          str(result)[:160])
    check('失败时不回传 seen（防止把「没拿到」当「没有新笔记」）',
          'scanned_ids' not in result or result['scanned_ids'] == [])
    check('失败时不回传基线',
          not result.get('baseline_note_id') and not result.get('baseline_time'))

    api = _StubWatchApi(fail=('notes', TimeoutError('ReadTimeout')))
    result = watch_probe.probe_target(api, 'u5', {})
    check('列表异常判为网络异常', result['health'] == paths.HEALTH_NETWORK,
          str(result)[:160])

    # --- 列表成功、资料失败：health 仍为 OK ---
    api = _StubWatchApi(
        pages=[{'notes': [_note('n1', 1000)], 'has_more': False, 'cursor': ''}],
        fail=('info', '接口超时'),
    )
    result = watch_probe.probe_target(api, 'u6', {})
    check('资料失败不改变健康状态',
          result['health'] == paths.HEALTH_OK, str(result)[:160])
    check('资料失败留下备注', '资料获取失败' in result.get('health_note', ''),
          result.get('health_note'))

    # --- 资料成功：解析出昵称与粉丝 ---
    api = _StubWatchApi(
        pages=[{'notes': [], 'has_more': False, 'cursor': ''}],
        user_info={
            'basic_info': {'nickname': '一棠耳饰', 'imageb': 'http://a/x.jpg',
                           'red_id': '123', 'ip_location': '上海'},
            'interactions': [{'type': 'fans', 'count': '4.2万'}],
            'posted': 399,
        },
    )
    result = watch_probe.probe_target(api, 'u7', {})
    check('资料解析出昵称', result.get('nickname') == '一棠耳饰', str(result)[:160])
    check('资料解析出粉丝数（万）', result.get('fans') == 42000, str(result.get('fans')))
    check('资料解析出作品数', result.get('posted') == 399)

    # --- apply_result：写回清单 ---
    import tempfile
    from pathlib import Path
    saved = (paths.WATCHLIST_FILE, paths.AVATAR_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        paths.WATCHLIST_FILE = Path(tmp) / 'w.json'
        paths.AVATAR_DIR = Path(tmp) / 'avatars'
        try:
            watch_probe.apply_result('u8', {
                'health': paths.HEALTH_OK, 'health_note': '',
                'last_checked_at': '2026-10-04 18:00:00',
                'scanned_ids': ['n2', 'n1'],
                'baseline_note_id': 'n2', 'baseline_time': 2000,
                'new_notes': [{'note_id': 'n1', 'title': 'A', 'time': 1000}],
                'pending': 1,
            })
            from desktop import watchlist as wl
            record = wl.target_of('u8')
            check('apply_result 登记新笔记', wl.pending_count(record) == 1)
            check('apply_result 写入 seen', set(record['seen']) >= {'n1', 'n2'})
            check('apply_result 写入基线', record['baseline_time'] == 2000)
            check('apply_result 成功时失败计数归零',
                  record.get('consecutive_failures') == 0)
            # 上轮的备注不该一直挂着：本轮正常且无话可说时清掉
            import desktop.watchlist as _wl
            saved_clear = _wl.clear_health_note
            cleared = []
            _wl.clear_health_note = lambda uid: cleared.append(uid)
            try:
                watch_probe.apply_result('u8', {
                    'health': paths.HEALTH_OK, 'health_note': '',
                    'last_checked_at': '2026-10-04 19:00:00',
                    'scanned_ids': ['n3'], 'new_notes': [], 'pending': 0,
                })
            finally:
                _wl.clear_health_note = saved_clear
            check('本轮正常时清掉上一轮的备注', cleared == ['u8'], str(cleared))
        finally:
            paths.WATCHLIST_FILE, paths.AVATAR_DIR = saved



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
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QTabWidget

    from desktop import paths as dpaths
    from desktop import main_window as mw
    from desktop import watchlist as dwatch
    from desktop.login_dialog import LoginDialog
    from desktop.main_window import NAV_MATRIX, NAV_WATCH, MainWindow

    dpaths.load_xhs_cookies = lambda: [{'cookie': 'web_session=selftest', 'nickname': '测试号'}]
    # 台账相关一律走内存：自检绝不能写真实的 ~/.xhs_spider
    fake_ledger = {'version': 1, 'accounts': {}, 'history': {}}

    def _load_accounts():
        return {
            'version': 1,
            'accounts': {k: dict(v) for k, v in fake_ledger['accounts'].items()},
            'history': {k: list(v) for k, v in fake_ledger['history'].items()},
        }

    dpaths.load_accounts = _load_accounts
    dpaths.sync_accounts_from_pool = lambda: {'sync': True}
    dpaths.upsert_account = lambda key, **fields: fake_ledger['accounts'].setdefault(key, fields)
    dpaths.remove_accounts = lambda keys: None
    dpaths.clear_avatars = lambda: None
    # 配置落盘也必须走内存：save_config 会真写 ~/.xhs_spider/config.json
    saved_configs = []
    dpaths.save_config = lambda cfg: saved_configs.append({
        'output_dir': cfg.get('output_dir'), 'probe_auto': cfg.get('probe_auto')})

    # 对标清单同样走内存：不能写真实 ~/.xhs_spider/xhs_watchlist.json
    fake_watch = {'version': 1, 'targets': {}}

    def _load_watch():
        return {'version': 1,
                'targets': {k: dict(v) for k, v in fake_watch['targets'].items()}}

    def _save_watch(data):
        fake_watch['targets'] = {k: dict(v) for k, v in (data or {}).get('targets', {}).items()}

    def _upsert_target(user_id, **fields):
        record = fake_watch['targets'].setdefault(str(user_id), {'user_id': str(user_id)})
        for name, value in fields.items():
            if value is None or value == '':
                continue
            record[name] = value
        return record

    def _add_note(user_id, note):
        record = fake_watch['targets'].setdefault(str(user_id), {'user_id': str(user_id)})
        seen = [str(n) for n in (record.get('seen') or [])]
        note_id = str((note or {}).get('note_id') or '')
        if not note_id or note_id in seen:
            return False
        record.setdefault('notes', []).insert(0, dict(note))
        record.setdefault('seen', []).insert(0, note_id)
        return True

    def _drop_notes(user_id, note_ids):
        record = fake_watch['targets'].get(str(user_id))
        if not record:
            return
        drop = {str(n) for n in (note_ids or [])}
        record['notes'] = [n for n in (record.get('notes') or [])
                           if str(n.get('note_id') or '') not in drop]

    dwatch.load_watchlist = _load_watch
    dwatch.save_watchlist = _save_watch
    dwatch.upsert_target = _upsert_target
    dwatch.remove_targets = lambda user_ids: [fake_watch['targets'].pop(str(u), None)
                                              for u in (user_ids or [])]
    dwatch.add_note = _add_note
    dwatch.drop_notes = _drop_notes
    dwatch.mark_seen = lambda user_id, note_ids: fake_watch['targets'].setdefault(
        str(user_id), {'user_id': str(user_id)})
    dwatch.prune_target = lambda user_id: fake_watch['targets'].get(str(user_id), {})
    dwatch.clear_health_note = lambda user_id: fake_watch['targets'].get(
        str(user_id), {}).__setitem__('health_note', '')

    app = QApplication.instance() or QApplication([])
    login = LoginDialog({'server': 'http://demo', 'username': 'alice'})
    check('登录框构建', login.windowTitle() != '')
    login.deleteLater()

    window = MainWindow(
        {'server': 'http://demo', 'username': 'alice', 'output_dir': os.getcwd()},
        {'token': 't', 'username': 'alice', 'name': 'Alice', 'xhsExpireTime': '永久'},
    )
    check('主窗口构建（6 个采集页签）', window.tabs.count() == 6)

    # 输出目录两处输入框必须双向同步（曾经设置页那个从没被读过）
    window.output_edit.setText('/tmp/selftest-out-a')
    check('改采集页目录同步到设置页',
          window.settings_output_edit.text() == '/tmp/selftest-out-a')
    window.settings_output_edit.setText('/tmp/selftest-out-b')
    check('改设置页目录同步回采集页',
          window.output_edit.text() == '/tmp/selftest-out-b')
    check('设置页改目录能进采集 spec',
          window._current_spec().output_dir == '/tmp/selftest-out-b')
    saved_configs.clear()
    window._persist_output_dir()
    check('编辑结束落盘输出目录',
          any(c.get('output_dir') == '/tmp/selftest-out-b' for c in saved_configs))
    window.output_edit.setText(os.getcwd())
    window._persist_output_dir()

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
          and window.no_water_check.isHidden()
          and not window.excel_check.isHidden())
    window.excel_check.setChecked(False)
    check('评论模式未勾 Excel 被拦截',
          '仅支持导出 Excel' in window._validate_spec(window._current_spec()))
    window.excel_check.setChecked(True)
    window.tabs.setCurrentIndex(0)
    check('切回搜索页恢复媒体选项',
          not window.img_check.isHidden() and not window.zip_check.isHidden()
          and not window.ai_check.isHidden() and not window.no_water_check.isHidden())
    check('无水印开关默认不勾选且能进 spec',
          window.no_water_check.isChecked() is False
          and window._current_spec().no_watermark is False)
    window.no_water_check.setChecked(True)
    check('勾选后 spec.no_watermark 为 True', window._current_spec().no_watermark is True)
    window.no_water_check.setChecked(False)

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

    # 用户搜索页签：模式解析、校验、选项显隐
    window.tabs.setCurrentIndex(5)
    spec = window._current_spec()
    check('用户搜索模式解析', spec.mode == 'usersearch' and spec.user_query == '')
    check('用户搜索空关键词被拦截',
          '关键词' in window._validate_spec(spec))
    window.user_query_edit.setText('黄金首饰')
    window.user_num_spin.setValue(30)
    spec = window._current_spec()
    check('用户搜索解析数量与关键词',
          spec.require_num == 30 and spec.user_query == '黄金首饰')
    window.excel_check.setChecked(True)
    check('用户搜索校验通过', window._validate_spec(spec) == '')
    check('用户搜索任务名带关键词', spec.task_name == '用户搜索黄金首饰')
    check('用户搜索隐藏媒体选项',
          window.img_check.isHidden() and not window.excel_check.isHidden())
    window.excel_check.setChecked(False)
    check('用户搜索未勾 Excel 被拦截',
          '仅支持导出 Excel' in window._validate_spec(window._current_spec()))
    window.excel_check.setChecked(True)
    window.tabs.setCurrentIndex(0)
    # 账号矩阵页：台账表格、统计卡、池同步
    check('矩阵页已替换为台账页', hasattr(window, 'matrix_table'))
    check('台账表格列数正确', window.matrix_table.columnCount() == 9,
          str(window.matrix_table.columnCount()))
    check('账号矩阵导航已移除 soon 徽标',
          window.nav_buttons[NAV_MATRIX].text() == '账号矩阵')

    fake_ledger['accounts'] = {
        's1': {'key': 's1', 'cookie': 'web_session=s1', 'nickname': '甲',
               'red_id': 'r1', 'health': 'ok', 'fans': 100, 'follows': 5,
               'interaction': 999, 'posted': 7, 'ip_location': '上海',
               'checked_at': '2026-10-03 12:00:00'},
        's2': {'key': 's2', 'cookie': 'web_session=s2', 'nickname': '乙',
               'health': 'expired', 'checked_at': '2026-10-03 12:30:00'},
        's3': {'key': 's3', 'cookie': 'web_session=s3', 'nickname': '丙',
               'health': 'unknown', 'checked_at': ''},
    }
    window.refresh_matrix_page(sync=False)
    check('台账渲染 3 行', window.matrix_table.rowCount() == 3,
          str(window.matrix_table.rowCount()))
    check('统计卡：账号总数', window.stat_accounts.text() == '3 个',
          window.stat_accounts.text())
    check('统计卡：健康 1 个', window.stat_healthy.text() == '1 个',
          window.stat_healthy.text())
    check('统计卡：异常 1 个', window.stat_broken.text() == '1 个',
          window.stat_broken.text())
    check('统计卡：总粉丝只算正常账号', window.stat_fans.text() == '100',
          window.stat_fans.text())
    check('异常账号排在正常账号之前',
          window.matrix_table.cellWidget(0, 2).text() == '登录失效',
          window.matrix_table.cellWidget(0, 2).text())
    check('未巡检账号显示占位而非 0',
          any(window.matrix_table.item(r, 3).text() == '—'
              for r in range(window.matrix_table.rowCount())),
          str([window.matrix_table.item(r, 3).text()
               for r in range(window.matrix_table.rowCount())]))
    check('巡检按钮可用（无巡检在跑）',
          window.probe_fast_btn.isEnabled() and window.probe_full_btn.isEnabled())
    check('台账行主键可从表格取回（用于双击打开主页）',
          window.matrix_table.item(0, 1).data(Qt.UserRole) == 's2',
          str(window.matrix_table.item(0, 1).data(Qt.UserRole)))
    # 双击打开主页：第 0 列是 cellWidget，若误从第 0 列取 item 会拿不到主键
    opened = []
    original_open = mw.QDesktopServices

    class _FakeDesktopServices:
        @staticmethod
        def openUrl(url):
            opened.append(url.toString())

    mw.QDesktopServices = _FakeDesktopServices
    try:
        fake_ledger['accounts']['s1']['user_id'] = 'u-abc'
        window.refresh_matrix_page(sync=False)
        row_s1 = next(r for r in range(window.matrix_table.rowCount())
                      if window.matrix_table.item(r, 1).data(Qt.UserRole) == 's1')
        window.open_current_account(window.matrix_table.model().index(row_s1, 0))
        check('双击台账行打开主页', opened == [
            'https://www.xiaohongshu.com/user/profile/u-abc'], str(opened))
    finally:
        mw.QDesktopServices = original_open
    check('设置页有自动巡检开关', window.probe_auto_check.isChecked())
    window.probe_auto_check.setChecked(False)
    check('关闭自动巡检会停止定时器', not window._probe_timer.isActive())
    window.probe_auto_check.setChecked(True)
    window.switch_page(NAV_MATRIX)
    check('切到矩阵页不改动采集页签', window.pages.currentIndex() == NAV_MATRIX)

    # 对标监控页：导航装配、空态、清单渲染、勾选与采集
    check('导航共 4 项', len(window.nav_buttons) == 4, str(sorted(window.nav_buttons)))
    check('对标监控导航存在',
          window.nav_buttons[NAV_WATCH].text() == '对标监控')
    check('对标监控页已装配', hasattr(window, 'watch_tree'))
    check('对标树列数正确', window.watch_tree.columnCount() == 7,
          str(window.watch_tree.columnCount()))
    window.switch_page(NAV_WATCH)
    check('切到对标页', window.pages.currentIndex() == NAV_WATCH)
    check('空清单时统计卡为 0',
          window.stat_watch_total.text() == '0 个', window.stat_watch_total.text())
    check('空清单时给出添加指引', '添加对标号' in window.watch_hint.text(),
          window.watch_hint.text())

    fake_watch['targets'] = {
        'w1': {'user_id': 'w1', 'nickname': '一棠耳饰', 'fans': 42000, 'posted': 399,
               'ip_location': '上海', 'health': 'ok', 'last_checked_at': '2026-10-04 18:00:00',
               'notes': [
                   {'note_id': 'n1', 'title': '新笔记A', 'type': 'normal', 'time': 1759000000000,
                    'liked': 128, 'xsec_token': 'tk1'},
                   {'note_id': 'n2', 'title': '新笔记B', 'type': 'video', 'time': 1758900000000,
                    'liked': 96, 'xsec_token': 'tk2'},
               ]},
        'w2': {'user_id': 'w2', 'nickname': '珠宝阁耳饰', 'health': 'limited',
               'notes': [{'note_id': 'n3', 'title': 'C', 'type': 'normal',
                          'time': 1758800000000, 'liked': 5, 'xsec_token': 'tk3'}]},
    }
    window.refresh_watch_page(sync=False)
    check('对标树渲染 2 个顶级项', window.watch_tree.topLevelItemCount() == 2,
          str(window.watch_tree.topLevelItemCount()))
    check('子项合计 3 条新笔记',
          sum(window.watch_tree.topLevelItem(i).childCount() for i in range(2)) == 3)
    check('统计卡：待采集新笔记 3 篇', window.stat_watch_new.text() == '3 篇',
          window.stat_watch_new.text())
    check('统计卡：对标账号 2 个', window.stat_watch_total.text() == '2 个',
          window.stat_watch_total.text())
    check('芯片配色：限流号显示为限流风控',
          any(window.watch_tree.topLevelItem(i).text(4) == '限流风控'
              for i in range(2)))
    check('新笔记默认全部勾选（开箱即可采）',
          len(window.selected_watch_notes()) == 3,
          str(len(window.selected_watch_notes())))

    # 父项取消勾选 → 子项跟着取消；重新勾上 → 子项全选
    top0 = window.watch_tree.topLevelItem(0)
    top0.setCheckState(0, Qt.Unchecked)
    app.processEvents()
    check('父项取消勾选递归到子项',
          all(top0.child(i).checkState(0) == Qt.Unchecked
              for i in range(top0.childCount())))
    top0.setCheckState(0, Qt.Checked)
    app.processEvents()
    check('父项勾选递归到子项',
          all(top0.child(i).checkState(0) == Qt.Checked
              for i in range(top0.childCount())))
    check('全选后选中 3 条', len(window.selected_watch_notes()) == 3)

    # 「采集选中的新笔记」构造的 spec
    captured = {}

    def _fake_start(spec, from_watch=False):
        captured['spec'] = spec
        return True

    real_start = window.start_collection_with_spec
    window.start_collection_with_spec = _fake_start
    try:
        window.collect_watch_notes()
    finally:
        window.start_collection_with_spec = real_start
    spec = captured.get('spec')
    check('对标采集走链接模式',
          spec is not None and spec.mode == 'urls', str(spec))
    check('对标采集 URL 数正确', spec is not None and len(spec.note_urls) == 3)
    check('对标采集 URL 带 xsec_token',
          spec is not None and 'xsec_token=tk1' in spec.note_urls[0], str(spec.note_urls[:1]))
    check('对标采集默认任务名为「对标采集」',
          spec is not None and spec.task_name == '对标采集', getattr(spec, 'task_name', ''))

    # 采集完成后把本次选中的新笔记从待处理移除
    window._watch_collect_notes = [{'user_id': 'w1', 'note_id': 'n1'},
                                   {'user_id': 'w1', 'note_id': 'n2'}]
    window._after_watch_collection()
    check('对标采集完成后待处理笔记被清除',
          len(fake_watch['targets']['w1'].get('notes') or []) == 0,
          str(fake_watch['targets']['w1'].get('notes')))
    check('清空待采集后统计卡归零', window.stat_watch_new.text() == '1 篇',
          window.stat_watch_new.text())

    # 三向互斥（QMessageBox 在 offscreen 下会阻塞，临时换成不弹窗的桩）
    class _NoBox:
        warnings = []

        @classmethod
        def warning(cls, parent, title, text, *a, **kw):
            cls.warnings.append((title, text))

        information = warning
        critical = warning
        question = warning

    real_box = mw.QMessageBox
    mw.QMessageBox = _NoBox
    try:
        window._probe_running = True
        window.start_watch(silent=True)
        check('巡检运行中拒绝启动对标检查', window._watch_running is False)
        window._probe_running = False
        window._watch_running = True
        spec_ok = window._start_collection_with_spec(
            __import__('desktop.spider_service', fromlist=['TaskSpec']).TaskSpec(
                mode='urls', note_urls=['https://www.xiaohongshu.com/explore/abc'],
                save_excel=True, output_dir=os.getcwd()))
        check('对标检查运行中拒绝启动采集', spec_ok is False)
        check('拒绝时给出提示而非静默失败',
              any('对标检查' in w[1] for w in _NoBox.warnings), str(_NoBox.warnings))
    finally:
        mw.QMessageBox = real_box
    window._watch_running = False

    fake_watch['targets'] = {}
    window.refresh_watch_page(sync=False)
    window.switch_page(0)

    # 粉丝增长曲线：数据来自台账 history，无历史时显示占位而不是假曲线
    check('曲线卡片已装配', hasattr(window, 'chart_account_combo')
          and hasattr(window, 'chart_metric_combo'))
    window.refresh_chart()
    check('无历史时显示占位文案', window.chart_placeholder.isVisible()
          or not window.chart_placeholder.isHidden())
    check('无历史时只有合计一个选项',
          window.chart_account_combo.count() == 1
          and window.chart_account_combo.currentData() == '__all__',
          str(window.chart_account_combo.count()))

    fake_ledger['history'] = {
        'u1': [{'date': '2026-10-01', 'fans': 100, 'interaction': 10, 'posted': 5},
               {'date': '2026-10-03', 'fans': 130, 'interaction': 40, 'posted': 6}],
        'u2': [{'date': '2026-10-01', 'fans': 50, 'interaction': 5, 'posted': 2},
               {'date': '2026-10-04', 'fans': 70, 'interaction': 9, 'posted': 3}],
    }
    fake_ledger['accounts']['s1']['user_id'] = 'u1'
    fake_ledger['accounts']['s2']['user_id'] = 'u2'
    window.refresh_chart()
    check('有历史后列出合计 + 两个账号',
          window.chart_account_combo.count() == 3,
          str([window.chart_account_combo.itemData(i)
               for i in range(window.chart_account_combo.count())]))
    check('账号选项用昵称而非 user_id',
          window.chart_account_combo.itemText(1) in ('甲', '乙'),
          window.chart_account_combo.itemText(1))
    check('曲线摘要含天数与增量',
          '3 天' in window.chart_summary.text() and '+50' in window.chart_summary.text(),
          window.chart_summary.text())
    if mw.HAS_QTCHARTS:
        check('有数据时显示折线图并隐藏占位',
              window.chart_view.isHidden() is False
              and window.chart_placeholder.isHidden() is True)
        chart = window.chart_view.chart()
        check('折线图渲染 2 个账号的曲线',
              len(chart.series()) >= 1,
              str(len(chart.series())))
    # 切到单账号 + 换指标：合计 150 -> 单账号 100 起
    window.chart_account_combo.setCurrentIndex(
        window.chart_account_combo.findData('u1'))
    window.chart_metric_combo.setCurrentIndex(
        window.chart_metric_combo.findData('interaction'))
    check('切账号切指标后摘要跟着变',
          '10' in window.chart_summary.text() and '40' in window.chart_summary.text(),
          window.chart_summary.text())

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
    check('无水印默认关闭', TaskSpec().no_watermark is False)
    check('任务名清洗非法字符', sanitize_name('a/b:c*?') == 'a_b_c_')
    check('空任务名回退时间戳', len(sanitize_name('   ')) >= 8)


def test_media_pickers():
    """图片/视频直链选择：字段顺序变化与旧字段漂移的回归防护。"""
    from xhs_utils.data_util import _original_image_url, _pick_image_url, _pick_video_addr

    scene = {'info_list': [
        {'image_scene': 'WB_PRV', 'url': 'http://cdn/small!nd_prv'},
        {'image_scene': 'WB_DFT', 'url': 'http://cdn/202601010000/abcdef/notes_pre_post/big!nd_dft_wlteh_webp_3'},
    ]}
    check('优先取 WB_DFT 直链，返回可下载的默认直链',
          _pick_image_url(scene) == 'http://cdn/202601010000/abcdef/notes_pre_post/big!nd_dft_wlteh_webp_3')
    check('原图改写走 ci.xiaohongshu.com（去掉 /{date}/{hash}/ 前缀）',
          _original_image_url(_pick_image_url(scene))
          == 'https://ci.xiaohongshu.com/notes_pre_post/big?imageView2/format/jpeg')
    check('原图改写覆盖 oss-ae 前缀',
          _original_image_url('http://cdn/202601010000/abcdef/oss-ae/notes/tok!nd_dft')
          == 'https://ci.xiaohongshu.com/oss-ae/notes/tok?imageView2/format/jpeg')
    check('原图改写覆盖裸 token（无资产目录）',
          _original_image_url('http://cdn/202601010000/abcdef/tok!nd_dft')
          == 'https://ci.xiaohongshu.com/tok?imageView2/format/jpeg')
    check('解析不出路径时返回空串（由调用方兜底）', _original_image_url('') == '')
    check('缺 image_scene 时回退下标 1',
          _pick_image_url({'info_list': [{'url': 'a'}, {'url': 'b'}]}) == 'b')
    check('只有一条时取该条', _pick_image_url({'info_list': [{'url': 'only'}]}) == 'only')
    check('info_list 缺失返回空串', _pick_image_url({}) == '')

    streams = {'media': {'stream': {
        'EF5': [{'width': 720, 'height': 1280, 'master_url': 'ef5-small'},
                {'width': 1080, 'height': 1920, 'master_url': 'ef5-hd'}],
        'EF4': [{'width': 720, 'height': 1280, 'master_url': 'ef4'}],
        'EF6': [], 'EF7': [],
    }}}
    check('视频优先 EF5 且取同键内最大分辨率', _pick_video_addr(streams) == 'ef5-hd')
    check('仅 EF4 时回退 EF4',
          _pick_video_addr({'media': {'stream': {'EF4': [{'url': 'only-ef4'}]}}}) == 'only-ef4')
    check('兼容旧 h264 字段',
          _pick_video_addr({'media': {'stream': {'h264': [{'url': 'old'}]}}}) == 'old')
    check('已废弃的 consumer 不再当兜底', _pick_video_addr({'consumer': {'origin_video_key': 'k'}}) is None)
    check('视频字段全空返回 None', _pick_video_addr({}) is None)


def test_account_history():
    """粉丝增长曲线的纯数据层：前向填充、缺日、空台账。"""
    from desktop.account_history import account_options, build_series, growth_summary

    empty = {'version': 1, 'accounts': {}, 'history': {}}
    check('空台账：只有合计选项', account_options(empty) == [('__all__', '全部账号合计')])
    check('空台账：序列为空且摘要不编造增长',
          build_series(empty) == [] and growth_summary([])['delta'] is None)

    ledger = {
        'accounts': {
            'k1': {'key': 'k1', 'user_id': 'u1', 'nickname': '甲'},
            'k2': {'key': 'k2', 'user_id': 'u2'},   # 无昵称 -> 退回 user_id 前缀
        },
        'history': {
            'u1': [{'date': '2026-10-01', 'fans': 100, 'interaction': 10, 'posted': 5},
                   {'date': '2026-10-03', 'fans': 130, 'interaction': 40, 'posted': 6}],
            'u2': [{'date': '2026-10-01', 'fans': 50, 'interaction': 5, 'posted': 2},
                   {'date': '2026-10-04', 'fans': 70, 'interaction': 9}],
        },
    }
    options = dict(account_options(ledger))
    check('选项用昵称，缺昵称退回 user_id 前缀',
          options.get('u1') == '甲' and options.get('u2', '').startswith('账号 u2'))

    single = build_series(ledger, 'u1')
    check('单账号序列只含自己的点',
          single == [('2026-10-01', 100), ('2026-10-03', 130)], str(single))
    check('单账号摘要增量 = 末 - 首', growth_summary(single)['delta'] == 30)

    total = build_series(ledger)   # 合计
    check('合计对缺日账号做前向填充（不凭空掉粉）',
          total == [('2026-10-01', 150), ('2026-10-03', 180), ('2026-10-04', 200)],
          str(total))
    fill_edge = {'accounts': {}, 'history': {
        # u2 首条快照晚于 u1：在 10-01 这天不该被算成 0 后仍计入
        'u1': [{'date': '2026-10-01', 'fans': 10}, {'date': '2026-10-02', 'fans': 12}],
        'u2': [{'date': '2026-10-02', 'fans': 7}],
    }}
    check('账号首条快照之前不计入合计',
          build_series(fill_edge) == [('2026-10-01', 10), ('2026-10-02', 19)],
          str(build_series(fill_edge)))
    check('缺字段按 0 处理且不抛异常',
          build_series(ledger, 'u2', 'posted')[-1] == ('2026-10-04', 0))
    check('未知指标回退为粉丝数',
          build_series(ledger, 'u1', 'nope') == single)


def test_xhs_login_dialog():
    """Cookie 导入的本地校验：字段整理与必填判断（不联网）。"""
    from desktop.xhs_login_dialog import (
        is_valid_cookie,
        missing_cookie_fields,
        normalize_cookie_text,
    )

    check('整行 Cookie 前缀被剥掉',
          normalize_cookie_text('Cookie: a1=x; web_session=y') == 'a1=x; web_session=y')
    check('多行粘贴整理为分号分隔',
          normalize_cookie_text('a1=x\nweb_session=y') == 'a1=x; web_session=y')
    check('空输入返回空串', normalize_cookie_text('   ') == '')

    check('完整 Cookie 通过校验',
          is_valid_cookie('a1=x; web_session=y') and missing_cookie_fields('a1=x; web_session=y') == [])
    check('缺 web_session 被识别',
          missing_cookie_fields('a1=x; gid=z') == ['web_session'])
    check('缺 a1 被识别', missing_cookie_fields('web_session=y') == ['a1'])
    check('空 Cookie 两个字段都缺',
          missing_cookie_fields('') == ['a1', 'web_session'])
    check('空值不算命中（a1= 视为缺失）',
          'a1' in missing_cookie_fields('a1=; web_session=y'))
    check('Cookie 值里含 = 也能正确切分',
          is_valid_cookie('a1=x=y; web_session=a=b'))


def test_no_watermark_download():
    """无水印下载：原图直链优先、失败回退默认直链（不联网，桩掉 requests）。"""
    import tempfile

    import xhs_utils.data_util as du

    IMG = 'http://cdn/202601010000/abcdef/notes_pre_post/tok!nd_dft_wlteh_webp_3'
    note = {
        'note_id': 'n1', 'note_url': 'https://www.xiaohongshu.com/explore/n1',
        'user_id': 'u1', 'title': '标题', 'nickname': '作者',
        'note_type': '图集', 'image_list': [IMG], 'tags': [], 'upload_time': '',
        'ip_location': '', 'desc': '', 'home_url': '', 'avatar': '',
        'liked_count': '', 'collected_count': '', 'comment_count': '',
        'share_count': '', 'video_cover': None, 'video_addr': None,
        'image_list_original': ['https://ci.xiaohongshu.com/notes_pre_post/tok?imageView2/format/jpeg'],
    }

    calls = []

    class _Resp:
        status_code = 200
        content = b'x'
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=0): return [b'x']

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        if 'ci.xiaohongshu.com' in url:
            raise RuntimeError('404')
        return _Resp()

    orig_get, orig_media = du.requests.get, du.download_media
    du.requests.get = fake_get
    try:
        tmp = tempfile.mkdtemp()
        # download_media 走真实实现（只被桩掉 requests.get）
        du.download_note(note, tmp, 'media-image', no_watermark=True)
        check('原图失败时回退默认直链', calls[0].startswith('https://ci.xiaohongshu.com/')
              and calls[-1] == IMG, str(calls))
        calls.clear()
        du.download_note(note, tmp, 'media-image', no_watermark=False)
        check('未开无水印只请求默认直链', calls == [IMG], str(calls))
    finally:
        du.requests.get, du.download_media = orig_get, orig_media

    # 新老数据都要能跑：缺 image_list_original 键不报错
    legacy = dict(note); legacy.pop('image_list_original')
    tmp2 = tempfile.mkdtemp()
    du.download_note(legacy, tmp2, '', no_watermark=True)
    check('旧数据缺 image_list_original 不报错', True)


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


# ---------- 6c. 用户搜索 ----------

def test_user_search():
    import tempfile

    from xhs_utils.data_util import handle_search_user_info

    # 实测的搜索结果结构（2026-09-24 探测所得，含各字段的真实类型/取值）
    RAW = {
        'id': 'u123', 'name': '金饰小铺', 'image': 'http://a/x.jpg',
        'red_id': 'abc123', 'sub_title': '小红书号：abc123',
        'profession': '珠宝商',
        'note_count': 128, 'fans': '5600',
        'followed': False,                       # 布尔，不是关注数
        'red_official_verified': True,
        'live_info': {'status': 0, 'start_time': 0},  # 字典且永远非空
        'link': 'xhsdiscover://1/user/user.u123',     # App 深链
        'xsec_token': 'tk', 'is_self': False,
    }
    parsed = handle_search_user_info(RAW)
    check('用户搜索字段映射正确',
          parsed['nickname'] == '金饰小铺' and parsed['red_id'] == 'abc123'
          and parsed['fans'] == '5600' and parsed['note_count'] == 128
          and parsed['profession'] == '珠宝商')
    check('用户搜索标注官方认证', parsed['verified'] == '是')

    # 回归：live_info 是字典且永远非空，必须看 status 而非判空
    check('live_info.status=0 时不算直播中', parsed['live'] == '')
    live = dict(RAW, live_info={'status': 1, 'start_time': 123})
    check('live_info.status=1 时标为直播中',
          handle_search_user_info(live)['live'] == '直播中')

    # 回归：主页 URL 必须是网页地址，不能是 App 深链
    check('主页 URL 用 id 拼网页地址而非深链',
          parsed['home_url'] == 'https://www.xiaohongshu.com/user/profile/u123')

    # 回归：red_id 为空时从 sub_title「小红书号：xxx」解析
    no_red = dict(RAW, red_id='', sub_title='小红书号：from_sub')
    check('red_id 为空时从 sub_title 解析',
          handle_search_user_info(no_red)['red_id'] == 'from_sub')

    # 关注数/简介列已移除（搜索结果无此数据）
    check('搜索结果不含关注数与简介字段',
          'followed' not in parsed and 'sub_title' not in parsed)

    # 缺字段不抛异常（搜索结果字段可能不全）
    sparse = handle_search_user_info({'id': 'u9'})
    check('用户搜索缺字段不抛异常',
          sparse['nickname'] == '' and sparse['fans'] == '' and sparse['live'] == '')

    # 兼容 handle_user_info 用的旧结构会 KeyError —— 固化这个差异
    from xhs_utils.data_util import handle_user_info
    try:
        handle_user_info(RAW, 'u123')
        old_raises = False
    except KeyError:
        old_raises = True
    check('搜索结果不能喂给 handle_user_info（已知差异）', old_raises)

    # 端到端：桩 api -> Excel
    import apis.xhs_pc_apis as api_mod
    import xhs_utils.xhs_pc as pc_mod
    from desktop.spider_service import TaskSpec, run_user_search

    class _StubApi:
        """记录收到的 query —— 参数为空时返回 0 条，与真实接口行为一致。

        这里刻意不屏蔽「关键词传空」这类 bug：真实接口收到空关键词会返回
        0 个用户（不报错），如果桩永远返回固定数据，字段名写错就测不出来。
        """

        def __init__(self):
            self.received = []

        def bootstrap(self):
            return self

        def search_some_user(self, query, require_num, proxies=None):
            self.received.append(query)
            if not query:
                return True, '成功', []
            return True, '成功', [RAW, {'id': 'u2', 'name': '二号'}]

    class _Emit:
        def __init__(self):
            self.logs = []

        def log(self, text):
            self.logs.append(str(text))

        def progress(self, done, total, stage):
            pass

    tmp = tempfile.mkdtemp()
    spec = TaskSpec(mode='usersearch', user_query='黄金首饰', require_num=10,
                    output_dir=tmp, task_name='us1', save_excel=True)
    emit = _Emit()
    stub = _StubApi()
    orig_auth, orig_api = pc_mod.XHSPcAuth, api_mod.XHS_Apis
    pc_mod.XHSPcAuth = type('A', (), {'from_cookie': staticmethod(lambda c: object())})
    api_mod.XHS_Apis = lambda auth: stub
    try:
        summary = run_user_search([{'cookie': 'x'}], spec, lambda: False, emit)
    finally:
        pc_mod.XHSPcAuth, api_mod.XHS_Apis = orig_auth, orig_api

    # 回归：关键词必须取 spec.user_query（曾误写成 spec.query，导致静默 0 结果）
    check('用户搜索把关键词传给接口（防字段名笔误）',
          stub.received == ['黄金首饰'])

    check('用户搜索生成 Excel',
          summary['users'] == 2 and summary['excel'].endswith('us1.xlsx')
          and os.path.exists(summary['excel']))
    check('用户搜索 summary 带 users 字段',
          'users' in summary and 'zip' not in summary)

    # 表头应为搜索专用 12 列，而非 handle_user_info 的旧表头
    import openpyxl
    wb = openpyxl.load_workbook(summary['excel'])
    header = [c.value for c in wb.active[1]]
    check('用户搜索 Excel 表头为搜索专用列',
          '职业/认证' in header and '作品数量' in header
          and '性别' not in header and '关注数量' not in header
          and '简介' not in header)


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
    test_account_probe()
    test_account_ledger()
    test_watchlist()
    test_watch_probe()
    test_gui()
    test_task_spec()
    test_media_pickers()
    test_account_history()
    test_xhs_login_dialog()
    test_no_watermark_download()
    test_xiaolvsu_zip()
    test_ai_parse()
    test_comment_collection()
    test_collect_urls()
    test_user_search()
    test_should_collect()
    print()
    if FAILURES:
        print(f'自检失败 {len(FAILURES)} 项：{FAILURES}')
        sys.exit(1)
    print('全部自检通过')
