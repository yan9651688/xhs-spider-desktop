# encoding: utf-8
"""账号历史快照 -> 粉丝增长曲线数据（纯函数，无 Qt / 无网络，便于自检）。

台账 `~/.xhs_spider/xhs_accounts.json` 的 history 形如：

    {"<user_id>": [{"date": "2026-10-03", "fans": 203, "interaction": 1801, "posted": 150}, ...]}

同一天只留一条，保留 90 天。这里把它整理成绘图需要的序列。
"""
from __future__ import annotations

METRIC_LABELS = {
    'fans': '粉丝数',
    'interaction': '获赞与收藏',
    'posted': '作品数',
}


def account_options(ledger: dict) -> list:
    """可选的曲线对象：[('__all__', '全部账号合计'), ('<user_id>', '昵称'), ...]。

    以 history 里出现过的 user_id 为准（没巡检过的账号没有曲线）；
    昵称从 accounts 里按 user_id 反查，查不到就退回 user_id 前 8 位。
    """
    accounts = (ledger or {}).get('accounts') or {}
    history = (ledger or {}).get('history') or {}
    nickname_by_user = {}
    for record in accounts.values():
        if not isinstance(record, dict):
            continue
        user_id = str(record.get('user_id') or '')
        if user_id and record.get('nickname'):
            nickname_by_user[user_id] = str(record['nickname'])
    options = [('__all__', '全部账号合计')]
    for user_id in sorted(history):
        entries = history.get(user_id) or []
        if not entries:
            continue
        options.append((user_id, nickname_by_user.get(user_id) or f'账号 {user_id[:8]}'))
    return options


def build_series(ledger: dict, user_id: str = '__all__', metric: str = 'fans') -> list:
    """返回 [(date, value), ...]，按日期升序。

    单账号：直接取该账号自己的快照。

    合计（'__all__'）：每个账号对自己最后一条快照做**前向填充**再求和 ——
    否则某天只巡检了部分账号时，合计会因为「少算一个号」而凭空下跌，
    看起来像掉粉。账号在它的第一条快照之前不计入（那时它还没被记录过）。
    """
    history = (ledger or {}).get('history') or {}
    if metric not in METRIC_LABELS:
        metric = 'fans'
    if user_id and user_id != '__all__':
        by_date = {}
        for entry in history.get(str(user_id)) or []:
            if not isinstance(entry, dict):
                continue
            date = str(entry.get('date') or '')
            if date:
                by_date[date] = _as_int(entry.get(metric))
        return [(date, by_date[date]) for date in sorted(by_date)]

    per_account = {}
    dates = set()
    for uid, entries in history.items():
        points = {}
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            date = str(entry.get('date') or '')
            if date:
                points[date] = _as_int(entry.get(metric))
                dates.add(date)
        if points:
            per_account[str(uid)] = points

    series = []
    carried = {}
    for date in sorted(dates):
        total = 0
        for uid, points in per_account.items():
            if date in points:
                carried[uid] = points[date]
            if uid in carried:      # 首条快照之前不计入这个账号
                total += carried[uid]
        series.append((date, total))
    return series


def growth_summary(series: list) -> dict:
    """曲线摘要：首值、末值、增量、天数。点数不足 2 时 delta 为 None（不编造增长）。"""
    if not series:
        return {'points': 0, 'first': None, 'last': None, 'delta': None, 'days': 0}
    first = series[0][1]
    last = series[-1][1]
    return {
        'points': len(series),
        'first': first,
        'last': last,
        'delta': (last - first) if len(series) > 1 else None,
        'days': len(series),
    }


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
