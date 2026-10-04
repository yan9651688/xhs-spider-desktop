# encoding: utf-8
"""对标账号清单：数据读写 + 「哪些是新笔记」的判定（纯函数，无 Qt / 无网络）。

存 `~/.xhs_spider/xhs_watchlist.json`，形如：

    {"version": 1, "targets": {"<user_id>": {"nickname": ..., "seen": [...], "notes": [...]}}}

与账号台账（`xhs_accounts.json`）分开存：台账的主键是本机账号的 web_session，
对标号没有 cookie，主键只能是**对方 user_id**，混在一份文件里主键语义会打架。

为什么不复用 paths.upsert_account 那套：那是账号资产（粉丝/作品/健康巡检），
这里是对标监控（新笔记登记），字段与生命周期都不同；共用的只有「原子写 JSON」
与「空值不覆盖」这两条约定。
"""
from __future__ import annotations

from desktop import paths

WATCHLIST_VERSION = 1

# 单号上限：seen 是唯一的膨胀源（对标号每篇笔记 24 字符），300 条足够判定
# 「新 vs 已有」（对标号两个月也冒不出 300 篇），超出按时间从新到旧裁。
SEEN_LIMIT = 300
NOTES_LIMIT = 200

# 主页链接形态：https://www.xiaohongshu.com/user/profile/<uid>?xsec_token=...
_PROFILE_MARKERS = ('/user/profile/',)


def empty_watchlist() -> dict:
    return {'version': WATCHLIST_VERSION, 'targets': {}}


def load_watchlist() -> dict:
    """读清单。结构损坏或缺字段时补全，不抛异常（界面初始化不能被文件问题打断）。"""
    data = paths._load_json(paths.WATCHLIST_FILE, None)
    if not isinstance(data, dict):
        return empty_watchlist()
    watchlist = empty_watchlist()
    targets = data.get('targets')
    if isinstance(targets, dict):
        watchlist['targets'] = {
            str(key): dict(value)
            for key, value in targets.items()
            if isinstance(value, dict)
        }
    return watchlist


def save_watchlist(watchlist: dict) -> None:
    paths._save_json(paths.WATCHLIST_FILE, watchlist or empty_watchlist())


def parse_target(text: str) -> str:
    """把用户粘贴的内容解析成对标号 user_id；解析不出来返回空串。

    接受两种输入（实测 `get_user_info` 都通）：
      - 主页链接 https://www.xiaohongshu.com/user/profile/<uid>?xsec_token=...
      - 裸 user_id

    刻意**不接受**小红书号或昵称：按小红书号搜用户会返回多个同名/相似结果
    （实测搜 986333818 返回 3 个，含 2 个无关用户），静默误配比让客户重贴更糟。
    """
    text = (text or '').strip()
    if not text:
        return ''
    # 主页链接：取 path 最后一段
    if '://' in text or text.startswith('/') or any(m in text for m in _PROFILE_MARKERS):
        tail = text.split('?', 1)[0].split('#', 1)[0].rstrip('/')
        tail = tail.rsplit('/', 1)[-1]
    else:
        tail = text.split('?', 1)[0].strip().rstrip('/')
    # user_id 是 24 位十六进制；放宽到「长度 >= 8 且只含十六进制字符」，避免误收网址片段
    if len(tail) >= 8 and all(c in '0123456789abcdefABCDEF' for c in tail):
        return tail
    return ''


def target_of(user_id: str) -> dict:
    """取单个对标号记录；不存在返回空 dict。"""
    return load_watchlist()['targets'].get(str(user_id or '')) or {}


def upsert_target(user_id: str, **fields) -> dict:
    """合并写入单个对标号。字段为空/None 时不覆盖已有值（沿用 upsert_account 的语义）。"""
    user_id = str(user_id or '')
    if not user_id:
        return {}
    watchlist = load_watchlist()
    record = watchlist['targets'].get(user_id) or {'user_id': user_id}
    for name, value in fields.items():
        if value is None or value == '':
            continue
        if name in ('seen', 'notes') and not value:
            continue
        record[name] = value
    record['user_id'] = user_id
    watchlist['targets'][user_id] = record
    save_watchlist(watchlist)
    return record


def remove_targets(user_ids) -> None:
    """从清单移除若干对标号。空列表则不做任何事。"""
    user_ids = [str(u) for u in (user_ids or []) if u]
    if not user_ids:
        return
    watchlist = load_watchlist()
    for user_id in user_ids:
        record = watchlist['targets'].pop(user_id, None)
        # 头像缓存与账号台账共用 avatars/ 目录（都按 user_id 命名），一并清掉
        if record is not None:
            paths._remove_avatar(user_id)
    save_watchlist(watchlist)


def _trim_seen(seen: list) -> tuple:
    """seen 裁到 SEEN_LIMIT，按列表顺序保留**最新的**（调用方保证新→旧）。

    返回 (裁剪后的列表, 丢弃条数)。
    """
    seen = [str(n) for n in (seen or []) if n]
    if len(seen) <= SEEN_LIMIT:
        return seen, 0
    return seen[:SEEN_LIMIT], len(seen) - SEEN_LIMIT


def mark_seen(user_id: str, note_ids) -> dict:
    """把一批 note_id 记入 seen（新→旧顺序），裁剪并落盘。返回更新后的记录。"""
    user_id = str(user_id or '')
    if not user_id:
        return {}
    watchlist = load_watchlist()
    record = watchlist['targets'].get(user_id) or {'user_id': user_id}
    merged = [str(n) for n in (note_ids or []) if n]
    # 已有 seen 里但不在本批的，接在后面（保持「新→旧」，本批是最新刷出来的）
    batch = set(merged)
    merged += [n for n in (record.get('seen') or []) if n and n not in batch]
    trimmed, dropped = _trim_seen(merged)
    record['seen'] = trimmed
    if dropped:
        record['seen_overflow'] = int(record.get('seen_overflow') or 0) + dropped
    watchlist['targets'][user_id] = record
    save_watchlist(watchlist)
    return record


def add_note(user_id: str, note: dict) -> bool:
    """登记一条新笔记。幂等：note_id 已登记过返回 False。

    登记时同时把它写进 seen，这样「同一篇笔记」不会因为 notes 被清空而重复入库。
    """
    user_id = str(user_id or '')
    note_id = str((note or {}).get('note_id') or '')
    if not user_id or not note_id:
        return False
    watchlist = load_watchlist()
    record = watchlist['targets'].get(user_id) or {'user_id': user_id}
    seen = [str(n) for n in (record.get('seen') or []) if n]
    if note_id in seen:
        return False
    notes = list(record.get('notes') or [])
    notes.insert(0, dict(note))          # 新的在前
    if len(notes) > NOTES_LIMIT:
        notes = notes[:NOTES_LIMIT]
    record['notes'] = notes
    seen.insert(0, note_id)
    trimmed, dropped = _trim_seen(seen)
    record['seen'] = trimmed
    if dropped:
        record['seen_overflow'] = int(record.get('seen_overflow') or 0) + dropped
    watchlist['targets'][user_id] = record
    save_watchlist(watchlist)
    return True


def drop_notes(user_id: str, note_ids) -> None:
    """把这些笔记从待处理列表移除（采集完视为已处理）；seen 保留，避免重复登记。"""
    user_id = str(user_id or '')
    note_ids = {str(n) for n in (note_ids or []) if n}
    if not user_id or not note_ids:
        return
    watchlist = load_watchlist()
    record = watchlist['targets'].get(user_id)
    if not record:
        return
    record['notes'] = [n for n in (record.get('notes') or [])
                       if str(n.get('note_id') or '') not in note_ids]
    watchlist['targets'][user_id] = record
    save_watchlist(watchlist)


def diff_new_notes(notes: list, seen, baseline_time: int = 0) -> list:
    """从一页列表结果里挑出「没见过的」笔记。

    notes 按接口原序（置顶在前，其余时间倒序）。两条过滤：
      1. `interact_info.sticky` 为真的置顶笔记**永远不算新**
         —— 实测置顶项排在最前但不是最新的，不过滤会被每轮反复当成新笔记；
      2. note_id 不在 seen 且 time 晚于 baseline_time。
    """
    seen = {str(n) for n in (seen or []) if n}
    fresh = []
    for item in notes or []:
        if not isinstance(item, dict):
            continue
        if (item.get('interact_info') or {}).get('sticky'):
            continue
        note_id = str(item.get('note_id') or '')
        if not note_id or note_id in seen:
            continue
        if baseline_time and int(item.get('time') or 0) <= baseline_time:
            continue
        fresh.append(item)
    return fresh


def clear_health_note(user_id: str) -> None:
    """清掉本轮之前的健康备注。

    upsert_target 的空值不覆盖是为了保住旧资产（昵称/粉丝），但 health_note
    是**本轮结论**——上一轮写的「首次收录，已建立基线」不该一直挂在界面上。
    """
    user_id = str(user_id or '')
    if not user_id:
        return
    watchlist = load_watchlist()
    record = watchlist['targets'].get(user_id)
    if not record or not record.get('health_note'):
        return
    record['health_note'] = ''
    watchlist['targets'][user_id] = record
    save_watchlist(watchlist)


def prune_target(user_id: str) -> dict:
    """把单个对标号的 seen / notes 裁到上限并落盘（供检查后统一收口）。"""
    user_id = str(user_id or '')
    if not user_id:
        return {}
    watchlist = load_watchlist()
    record = watchlist['targets'].get(user_id)
    if not record:
        return {}
    trimmed, dropped = _trim_seen(record.get('seen') or [])
    record['seen'] = trimmed
    if dropped:
        record['seen_overflow'] = int(record.get('seen_overflow') or 0) + dropped
    notes = list(record.get('notes') or [])
    if len(notes) > NOTES_LIMIT:
        record['notes'] = notes[:NOTES_LIMIT]
    watchlist['targets'][user_id] = record
    save_watchlist(watchlist)
    return record


# ---------- 纯展示辅助（UI 与自检共用） ----------

HEALTH_LABELS = paths.HEALTH_LABELS


def pending_count(record: dict) -> int:
    """该对标号待处理（未采集）的新笔记数。"""
    return len((record or {}).get('notes') or [])


def total_pending(watchlist: dict) -> int:
    """整个清单的待处理新笔记总数。"""
    return sum(pending_count(r) for r in (watchlist or {}).get('targets', {}).values())
