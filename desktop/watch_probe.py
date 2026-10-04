# encoding: utf-8
"""对标账号探测：「看看对方有没有发新笔记」（纯函数，无 Qt / 无网络以外依赖）。

刻意做成无 Qt 的纯函数模块，自检可以直接猴子补丁 XHS_Apis 后测判定逻辑。

与 `account_probe` 的分工：那边探测的是**本机自己的账号**（要验登录态、拉资产、
写台账），这边探测的是**别人的账号**（只关心有没有新笔记，用本机的 cookie 去打）。

已实测确认（2026-10-04，真实接口）：

- 拉别人的作品列表**不需要 xsec_token**：``get_user_note_info(user_id, '')`` 即可，
  单页 30 条约 0.36s。全量版 ``get_user_all_notes`` 会对几百篇逐页翻，太贵。
- 无需 ``bootstrap()``：``_XY_PATH_MARKERS``（apis/xhs_pc_apis.py:64）只覆盖
  homefeed/feed，``user_posted`` 与 ``user/otherinfo`` 不在其中。
- 列表顺序 = **置顶在前，其余严格按时间倒序**。置顶项排最前但不是最新的，
  判定新笔记时必须先剔掉（见 watchlist.diff_new_notes）。
"""
from __future__ import annotations

import time

from loguru import logger

from desktop import paths, watchlist
from desktop.account_probe import (
    _as_int,
    _cache_avatar,
    classify_error,
    classify_message,
    parse_user_info,
)

# 单号最多补翻几页：6 小时一轮，普通对标号发不出 30 篇；只有极端情况才走补页分支
MAX_PAGES = 3
# 对标号之间的间隔，降风控
WATCH_GAP_SECONDS = 0.6
# 连续失败这么多次后本轮不再补页，不给已风控的号死磕
MAX_CONSECUTIVE_FAIL = 3
# 资料（粉丝/作品数/IP）刷新间隔：不必每轮拉，省一次请求
PROFILE_REFRESH_SECONDS = 24 * 3600


def build_api(cookie: str):
    """本机某个可用 cookie -> (auth, api)。不调 bootstrap()（见模块注释）。

    调用方负责 auth.close()。
    """
    from apis.xhs_pc_apis import XHS_Apis
    from xhs_utils.xhs_pc import XHSPcAuth

    auth = XHSPcAuth.from_cookie(str(cookie or ''))
    return auth, XHS_Apis(auth)


def parse_note_list_item(item: dict) -> dict:
    """列表一条 -> 登记用的扁平 dict。全程 .get()，缺字段降级，不抛 KeyError。"""
    item = item or {}
    if not isinstance(item, dict):
        return {}
    interact = item.get('interact_info') or {}
    note_id = str(item.get('note_id') or item.get('id') or '')
    if not note_id:
        return {}
    return {
        'note_id': note_id,
        'title': str(item.get('display_title') or item.get('title') or ''),
        'type': str(item.get('type') or ''),
        'time': int(item.get('time') or 0),
        'liked': _as_int(interact.get('liked_count')),
        'xsec_token': str(item.get('xsec_token') or ''),
        'first_seen': time.strftime('%Y-%m-%d %H:%M:%S'),
    }


def note_url(note: dict) -> str:
    """登记的笔记 -> 可采集的完整 URL（与 spider_service.collect_note_urls 同款拼法）。"""
    note = note or {}
    note_id = str(note.get('note_id') or '')
    if not note_id:
        return ''
    token = str(note.get('xsec_token') or '')
    url = f'https://www.xiaohongshu.com/explore/{note_id}'
    if token:
        url += f'?xsec_token={token}&xsec_source=pc_user'
    return url


def home_url(user_id: str) -> str:
    return f'https://www.xiaohongshu.com/user/profile/{user_id}'


def _needs_profile(record: dict, is_first: bool) -> bool:
    """要不要顺带刷一下对标号资料（粉丝/作品数/IP）。"""
    if is_first or not record.get('profile_checked_at'):
        return True
    try:
        last = time.mktime(time.strptime(
            str(record['profile_checked_at']), '%Y-%m-%d %H:%M:%S'))
    except (ValueError, TypeError):
        return True
    return (time.time() - last) >= PROFILE_REFRESH_SECONDS


def probe_target(api, user_id: str, record: dict | None = None) -> dict:
    """检查单个对标号，返回可直接喂给 watchlist.upsert_target 的字段 dict。

    返回键：health / health_note / last_checked_at / scanned_ids /
            baseline_note_id / baseline_time / new_notes / pending（新登记数）
    可选键（该刷资料时才出现）：profile_checked_at / nickname / avatar / fans /
            posted / ip_location / desc / avatar_file

    关键约定：**失败时绝不回传 seen / baseline 相关字段**——否则
    「没拿到」会被上层当成「没有新笔记」，或把历史误判成新笔记。
    """
    user_id = str(user_id or '')
    record = record or watchlist.target_of(user_id) or {}
    now = time.strftime('%Y-%m-%d %H:%M:%S')
    result = {
        'health': paths.HEALTH_UNKNOWN,
        'health_note': '',
        'last_checked_at': now,
        'scanned_ids': [],
        'new_notes': [],
        'pending': 0,
    }
    if not user_id or api is None:
        result.update(health=paths.HEALTH_NETWORK, health_note='没有可用的探测会话')
        return result

    seen = {str(n) for n in (record.get('seen') or []) if n}
    is_first = not int(record.get('baseline_time') or 0)

    # ---- 第一步：拉第一页作品列表（唯一的必需请求）----
    try:
        success, msg, res = api.get_user_note_info(user_id, '', '', '')
    except Exception as exc:
        result.update(health=classify_error(exc), health_note=str(exc)[:200])
        return result
    if not success:
        result.update(health=classify_message(msg), health_note=str(msg)[:200])
        return result

    page = ((res or {}).get('data') or {})
    notes = page.get('notes') or []
    has_more = bool(page.get('has_more'))
    cursor = str(page.get('cursor') or '')

    if is_first:
        # 首次：只建基线，**不把几百条历史当新笔记**
        scanned = [str(n.get('note_id') or '') for n in notes if isinstance(n, dict)]
        scanned = [n for n in scanned if n]
        newest = next((n for n in notes
                       if isinstance(n, dict) and not (n.get('interact_info') or {}).get('sticky')),
                      None)
        result.update(
            health=paths.HEALTH_OK,
            health_note=f'首次收录，已建立基线（{len(scanned)} 篇）',
            scanned_ids=scanned,
            baseline_note_id=str((newest or {}).get('note_id') or (scanned[0] if scanned else '')),
            baseline_time=int((newest or {}).get('time') or 0),
        )
        _maybe_profile(api, user_id, record, result, force=True)
        return result

    baseline_time = int(record.get('baseline_time') or 0)
    baseline_note_id = str(record.get('baseline_note_id') or '')

    # ---- 第二步：找新笔记；一页装不下就带 cursor 补翻（封顶 MAX_PAGES）----
    new_items = []
    scanned = []
    pages = 1
    consecutive_failures = int(record.get('consecutive_failures') or 0)
    while True:
        fresh = watchlist.diff_new_notes(notes, seen, baseline_time)
        scanned.extend(str(n.get('note_id') or '') for n in notes if isinstance(n, dict))
        # 停：本页里出现了基线笔记（说明基线之后的全拿到了），或到页数上限
        page_ids = {str(n.get('note_id') or '') for n in notes if isinstance(n, dict)}
        reached_baseline = bool(baseline_note_id) and baseline_note_id in page_ids
        oldest_time = min((int(n.get('time') or 0) for n in notes
                           if isinstance(n, dict)), default=0)
        # 本页最旧的一条还在基线之后 → 基线不在这页，可能还有更新的没取到
        may_have_more = has_more and not reached_baseline and oldest_time > baseline_time
        new_items.extend(fresh)
        if not may_have_more or pages >= MAX_PAGES:
            break
        if consecutive_failures >= MAX_CONSECUTIVE_FAIL:
            result['health_note'] = '该对标号近期多次失败，本轮不再补翻'
            break
        try:
            more_ok, more_msg, more_res = api.get_user_note_info(user_id, cursor, '', '')
        except Exception as exc:
            logger.debug(f'补翻对标号 {user_id} 第 {pages + 1} 页失败：{str(exc)[:80]}')
            break
        if not more_ok:
            break
        more_page = ((more_res or {}).get('data') or {})
        notes = more_page.get('notes') or []
        has_more = bool(more_page.get('has_more'))
        cursor = str(more_page.get('cursor') or '')
        pages += 1

    result.update(
        health=paths.HEALTH_OK,
        scanned_ids=[n for n in scanned if n],
        new_notes=[parse_note_list_item(n) for n in new_items if isinstance(n, dict)],
    )
    result['new_notes'] = [n for n in result['new_notes'] if n.get('note_id')]
    result['pending'] = len(result['new_notes'])

    _maybe_profile(api, user_id, record, result, force=False)
    return result


def _maybe_profile(api, user_id: str, record: dict, result: dict, *, force: bool) -> None:
    """需要时刷新对标号资料。**失败不改变 health**，只追加一句备注（对齐 account_probe 约定）。"""
    if not force and not _needs_profile(record, is_first=False):
        return
    try:
        ok, msg, res = api.get_user_info(user_id)
    except Exception as exc:
        ok, msg, res = False, str(exc), None
    if not (ok and isinstance((res or {}).get('data'), dict)):
        note = str(msg or '')[:80]
        if note:
            existing = result.get('health_note') or ''
            result['health_note'] = (existing + '；' if existing else '') + f'资料获取失败：{note}'
        return
    info = parse_user_info(res['data'], user_id)
    result.update(
        profile_checked_at=time.strftime('%Y-%m-%d %H:%M:%S'),
        nickname=info.get('nickname') or '',
        avatar=info.get('avatar') or '',
        fans=info.get('fans'),
        posted=info.get('posted'),
        ip_location=info.get('ip_location') or '',
        desc=info.get('desc') or '',
        red_id=info.get('red_id') or '',
    )
    result['avatar_file'] = ''
    # _cache_avatar 是「把 avatar_file 写回传入的 dict」，必须传 result 本身；
    # 早先传的是 {**result} 副本，头像下好了却写不回结果里（外部看不到 avatar_file）。
    result['user_id'] = user_id
    _cache_avatar(result)


def apply_result(user_id: str, result: dict) -> dict:
    """把探测结果写回清单：登记新笔记 → 标 seen → 更新资料与健康状态。

    顺序很重要：先 add_note（幂等），再 mark_seen（把本页全部 note_id 记为已见），
    这样即便登记中途出错，seen 也不会漏掉导致下轮重复。
    """
    user_id = str(user_id or '')
    if not user_id:
        return {}
    new_notes = result.get('new_notes') or []
    for note in new_notes:
        watchlist.add_note(user_id, note)

    fields = {
        'health': result.get('health'),
        'health_note': result.get('health_note') or '',
        'last_checked_at': result.get('last_checked_at'),
    }
    for key in ('profile_checked_at', 'nickname', 'avatar', 'fans', 'posted',
                'ip_location', 'desc', 'red_id', 'avatar_file'):
        if key in result:
            fields[key] = result.get(key)
    if result.get('baseline_note_id'):
        fields['baseline_note_id'] = result['baseline_note_id']
        fields['baseline_time'] = result['baseline_time']
    watchlist.upsert_target(user_id, **fields)
    # upsert 的空值不覆盖是为了保住旧资产，但 health_note 是**本轮结论**：
    # 上轮写的「首次收录…」不该一直挂着，本轮正常就清掉。
    if result.get('health') == paths.HEALTH_OK and not result.get('health_note'):
        watchlist.clear_health_note(user_id)

    # 只有真的拉到了列表才推进 seen / baseline；失败时保持原值
    if result.get('health') == paths.HEALTH_OK and result.get('scanned_ids'):
        record = watchlist.mark_seen(user_id, result['scanned_ids'])
        watchlist.upsert_target(user_id, baseline_note_id=result.get('baseline_note_id') or
                                record.get('baseline_note_id'),
                                baseline_time=result.get('baseline_time') or
                                record.get('baseline_time'))
    if result.get('health') == paths.HEALTH_OK:
        watchlist.upsert_target(user_id, consecutive_failures=0)
    elif result.get('health') not in (None, paths.HEALTH_UNKNOWN):
        record = watchlist.target_of(user_id)
        watchlist.upsert_target(
            user_id, consecutive_failures=int(record.get('consecutive_failures') or 0) + 1)
    return watchlist.target_of(user_id)
