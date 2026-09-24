# encoding: utf-8
"""桌面端采集任务编排：收集笔记 URL -> 逐条抓取 -> 下载媒体/导出 Excel。

复用 apis/xhs_pc_apis.XHS_Apis 与 spider.spider.Data_Spider.spider_note，
保存逻辑复用 xhs_utils.data_util 的 download_note / save_to_xlsx。
"""
from __future__ import annotations

import os
import random
import re
import time
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class TaskSpec:
    mode: str = 'search'            # search / urls / user / comments / collect
    query: str = ''
    require_num: int = 20
    sort_type: int = 0              # 0 综合 1 最新 2 最多点赞 3 最多评论 4 最多收藏
    note_type: int = 0              # 0 不限 1 视频 2 图文
    note_time: int = 0              # 0 不限 1 一天内 2 一周内 3 半年内
    note_urls: list = field(default_factory=list)
    user_url: str = ''
    comment_urls: list = field(default_factory=list)   # 评论采集：笔记链接列表
    collect_kind: str = 'collect'   # 收藏采集：collect=收藏 / like=赞过
    save_images: bool = True
    save_videos: bool = True
    save_excel: bool = True
    zip_export: bool = True         # 导出小绿书 zip（xiao 赛道上传格式）
    ai_cfg: dict = field(default_factory=dict)   # 非空时抓取后逐篇 AI 改写
    delay_seconds: float = 2.0      # 每篇间隔（防风控限速），0=不限速
    task_name: str = ''
    output_dir: str = ''

    @property
    def save_choice(self) -> str:
        if self.save_images and self.save_videos:
            return 'media'
        if self.save_images:
            return 'media-image'
        if self.save_videos:
            return 'media-video'
        return ''

    @property
    def want_media(self) -> bool:
        return bool(self.save_choice)


def sanitize_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', '_', (name or '').strip()) or time.strftime('%Y%m%d_%H%M%S')


def _note_urls_from_list(notes: list, source_label: str):
    """笔记对象列表 -> 完整笔记 URL 列表。

    赞过/收藏接口返回的 note 结构与「用户作品」接口未必一致（前者走
    /api/sns/web/v1/note/like/page 与 /v2/note/collect/page），字段可能缺失。
    这里逐条判键，缺 id/xsec_token 的跳过并记日志，避免整批 KeyError 中断。
    """
    urls = []
    skipped = 0
    for note in notes or []:
        note_id = note.get('note_id') or note.get('id')
        token = note.get('xsec_token')
        if not note_id or not token:
            skipped += 1
            continue
        source = 'pc_search' if source_label == '赞过' else 'pc_user'
        urls.append(
            f'https://www.xiaohongshu.com/explore/{note_id}'
            f'?xsec_token={token}&xsec_source={source}'
        )
    if skipped:
        logger.warning(f'{source_label}列表有 {skipped} 条缺 note_id/xsec_token，已跳过')
    return urls


def collect_note_urls(api, spec: TaskSpec):
    """按任务模式取笔记 URL 列表。"""
    if spec.mode == 'search':
        success, msg, notes = api.search_some_note(
            spec.query, spec.require_num,
            spec.sort_type, spec.note_type, spec.note_time,
        )
        if not success:
            raise RuntimeError(f'搜索失败：{msg}')
        notes = [n for n in notes if n.get('model_type') == 'note']
        return [
            f"https://www.xiaohongshu.com/explore/{n['id']}"
            f"?xsec_token={n['xsec_token']}&xsec_source=pc_search"
            for n in notes
        ]
    if spec.mode == 'user':
        success, msg, notes = api.get_user_all_notes(spec.user_url)
        if not success:
            raise RuntimeError(f'获取用户笔记失败：{msg}')
        return [
            f"https://www.xiaohongshu.com/explore/{n['note_id']}"
            f"?xsec_token={n['xsec_token']}&xsec_source=pc_user"
            for n in notes
        ]
    if spec.mode == 'collect':
        if spec.collect_kind == 'like':
            success, msg, notes = api.get_user_all_like_note_info(spec.user_url)
            label = '赞过'
        else:
            success, msg, notes = api.get_user_all_collect_note_info(spec.user_url)
            label = '收藏'
        if not success:
            raise RuntimeError(f'获取用户{label}笔记失败：{msg}')
        return _note_urls_from_list(notes, label)
    if spec.mode == 'urls':
        urls = [u.strip() for u in spec.note_urls or [] if u.strip()]
        if not urls:
            raise RuntimeError('请至少填写一个笔记链接')
        return urls
    raise RuntimeError(f'未知任务模式：{spec.mode}')


RISK_KEYWORDS = ('461', '-629', '频繁', '风控', '验证码', '异常流量')
RISK_COOLDOWN_SECONDS = 60
RISK_MAX_CONSECUTIVE = 3


def _throttle(delay: float, should_stop) -> None:
    """带随机抖动的限速等待，0.2s 粒度响应停止请求。"""
    if delay <= 0:
        return
    target = time.monotonic() + random.uniform(delay * 0.7, delay * 1.3)
    while time.monotonic() < target and not should_stop():
        time.sleep(0.2)


def _spider_note(api, url: str):
    """单篇抓取（复用 Data_Spider.spider_note 的逻辑，支持指定 api 实例做账号轮询）。"""
    from xhs_utils.data_util import handle_note_info

    try:
        success, msg, data = api.get_note_info(url)
        if success:
            item = data['data']['items'][0]
            item['url'] = url
            return True, msg, handle_note_info(item)
        return success, msg, None
    except Exception as exc:
        return False, exc, None


def _spider_comments(api, url: str):
    """单篇笔记的一级评论抓取（不展开楼中楼，减少请求数）。"""
    from xhs_utils.data_util import handle_comment_info

    success, msg, raw_list = api.get_note_all_comment(url, with_inner=False)
    if not success:
        return False, msg, None
    comments = []
    for raw in raw_list:
        try:
            comments.append(handle_comment_info(raw))
        except Exception as exc:
            logger.debug(f'评论字段解析跳过一条：{exc}')
    return True, msg, comments


def run_comment_collection(cookies: list, spec: TaskSpec, should_stop, emit):
    """评论采集：逐篇笔记抓一级评论 -> 导出 Excel。

    与 run_collection 分开实现：评论没有媒体文件、不进小绿书 zip，
    因此导出阶段差异过大，不适合复用。
    多账号轮询、限速抖动、风控冷却/熔断的写法与 run_collection 保持一致。
    """
    from xhs_utils.data_util import save_to_xlsx
    from xhs_utils.xhs_pc import XHSPcAuth
    from apis.xhs_pc_apis import XHS_Apis

    started = time.time()

    emit.log(f'正在建立 {len(cookies)} 个小红书账号会话…')
    apis_list = []
    for i, cookie in enumerate(cookies, start=1):
        if should_stop():
            return {'total': 0, 'got': 0, 'comments': 0, 'excel': '',
                    'base_dir': '', 'seconds': 0, 'stopped': True}
        try:
            auth = XHSPcAuth.from_cookie(cookie)
            apis_list.append(XHS_Apis(auth))
            emit.log(f'小红书账号 {i} 会话有效')
        except Exception as exc:
            emit.log(f'小红书账号 {i} Cookie 已失效，跳过：{str(exc)[:60]}')
        _throttle(1.0, should_stop)
    if not apis_list:
        raise RuntimeError('没有可用的小红书账号，请重新扫码登录')
    emit.log(f'{len(apis_list)} 个账号参与轮询采集，分摊请求频率')

    urls = [u.strip() for u in spec.comment_urls if u.strip()]
    total = len(urls)
    emit.progress(0, total, '抓取评论')
    emit.log(f'共 {total} 篇笔记，开始逐篇抓取一级评论')
    if spec.delay_seconds > 0:
        emit.log(f'防风控限速已开启：每篇间隔约 {spec.delay_seconds:g} 秒')
    else:
        emit.log('警告：未开启限速，高频采集容易触发小红书风控')

    all_comments = []
    ok_notes = 0
    consecutive_risk = 0
    for index, url in enumerate(urls, start=1):
        if should_stop():
            emit.log('任务已被用户停止，开始保存已采集的结果…')
            break
        api_i = apis_list[(index - 1) % len(apis_list)]
        try:
            success, msg, comments = _spider_comments(api_i, url)
        except Exception as exc:
            success, msg, comments = False, exc, None
        if success:
            consecutive_risk = 0
            ok_notes += 1
            all_comments.extend(comments or [])
            emit.log(f'评论抓取完成（{index}/{total}）：{len(comments or [])} 条')
        else:
            detail = str(msg)
            emit.log(f'评论抓取失败（{index}/{total}）：{msg}')
            if any(k in detail for k in RISK_KEYWORDS):
                consecutive_risk += 1
                if consecutive_risk >= RISK_MAX_CONSECUTIVE:
                    emit.log(
                        f'连续 {RISK_MAX_CONSECUTIVE} 次触发风控，任务已自动停止。'
                        '建议等待 30 分钟、调大采集间隔或增加小红书账号后再试'
                    )
                    break
                emit.log(
                    f'疑似触发风控限流，冷却 {RISK_COOLDOWN_SECONDS} 秒减少调用后继续'
                    f'（连续第 {consecutive_risk} 次，累计 {RISK_MAX_CONSECUTIVE} 次自动停止）'
                )
                _throttle(RISK_COOLDOWN_SECONDS, should_stop)
            else:
                consecutive_risk = 0
        emit.progress(index, total, '抓取评论')
        if index < total:
            _throttle(spec.delay_seconds, should_stop)

    task_name = sanitize_name(spec.task_name)
    base_dir = os.path.join(spec.output_dir, task_name)
    excel_dir = os.path.join(base_dir, 'excel')
    os.makedirs(excel_dir, exist_ok=True)

    excel_path = ''
    if all_comments:
        excel_path = os.path.join(excel_dir, f'{task_name}_评论.xlsx')
        save_to_xlsx(all_comments, excel_path, 'comment')
        emit.log(f'评论 Excel 已保存：{excel_path}（{len(all_comments)} 条）')
    else:
        emit.log('未抓到任何评论，未生成 Excel')

    return {
        'total': total,
        'got': ok_notes,
        'comments': len(all_comments),
        'excel': excel_path,
        'base_dir': base_dir,
        'seconds': round(time.time() - started, 1),
        'stopped': should_stop(),
    }


def _should_collect(note_info: dict, spec: TaskSpec) -> bool:
    """按保存选项过滤笔记类型：没勾视频=跳过视频笔记，没勾图片=跳过图文笔记。"""
    is_video = note_info.get('note_type') == '视频'
    if is_video and not spec.save_videos:
        return False
    if not is_video and not spec.save_images:
        return False
    return True


def run_collection(cookies: list, spec: TaskSpec, should_stop, emit):
    """执行一次采集任务，返回摘要 dict。

    cookies: 小红书 Cookie 列表，逐篇轮询使用（多账号分摊请求）。
    emit 需要提供：log(str) / note(dict) / progress(done, total, stage)
    should_stop() 返回 True 时在安全点中断。
    """
    from spider.spider import Data_Spider
    from xhs_utils.data_util import download_note, save_to_xlsx
    from xhs_utils.xhs_pc import XHSPcAuth
    from apis.xhs_pc_apis import XHS_Apis

    started = time.time()

    emit.log(f'正在建立 {len(cookies)} 个小红书账号会话…')
    apis_list = []
    for i, cookie in enumerate(cookies, start=1):
        if should_stop():
            return {'total': 0, 'got': 0, 'excel': '', 'zip': '', 'media_dir': '',
                    'base_dir': '', 'seconds': 0, 'stopped': True}
        try:
            auth = XHSPcAuth.from_cookie(cookie)
            apis_list.append(XHS_Apis(auth))
            emit.log(f'小红书账号 {i} 会话有效')
        except Exception as exc:
            emit.log(f'小红书账号 {i} Cookie 已失效，跳过：{str(exc)[:60]}')
        _throttle(1.0, should_stop)
    if not apis_list:
        raise RuntimeError('没有可用的小红书账号，请重新扫码登录')
    emit.log(f'{len(apis_list)} 个账号参与轮询采集，分摊请求频率')

    api = apis_list[0].bootstrap()
    emit.log('开始收集笔记链接…')

    urls = collect_note_urls(api, spec)
    total = len(urls)
    emit.progress(0, total, '抓取')
    emit.log(f'共找到 {total} 篇笔记，开始逐条抓取')

    ai_client = None
    if spec.ai_cfg and spec.ai_cfg.get('key'):
        from desktop.ai_client import AIClient
        ai_client = AIClient(
            spec.ai_cfg.get('base') or '', spec.ai_cfg.get('key') or '',
            spec.ai_cfg.get('model') or '',
            spec.ai_cfg.get('title_prompt') or '',
            spec.ai_cfg.get('content_prompt') or '',
        )
        emit.log(f"AI 改写已开启（模型：{ai_client.model}，标题/文案独立改写）")
    if spec.delay_seconds > 0:
        emit.log(f'防风控限速已开启：每篇间隔约 {spec.delay_seconds:g} 秒')
    else:
        emit.log('警告：未开启限速，高频采集容易触发小红书风控')

    note_list = []
    skipped = 0
    consecutive_risk = 0
    for index, url in enumerate(urls, start=1):
        if should_stop():
            emit.log('任务已被用户停止，开始保存已采集的结果…')
            break
        api_i = apis_list[(index - 1) % len(apis_list)]
        try:
            success, msg, note_info = _spider_note(api_i, url)
        except Exception as exc:
            success, msg, note_info = False, exc, None
        if success and note_info:
            if not _should_collect(note_info, spec):
                skipped += 1
                emit.progress(index, total, '抓取')
                if index < total:
                    _throttle(spec.delay_seconds, should_stop)
                continue
            consecutive_risk = 0
            if ai_client is not None:
                title_done = content_done = False
                try:
                    note_info['title_ai'] = ai_client.rewrite_title(
                        note_info.get('title') or '')
                    title_done = True
                except Exception as exc:
                    emit.log(f'AI 标题改写失败（{index}/{total}），保留原文：{exc}')
                try:
                    note_info['desc_ai'] = ai_client.rewrite_content(
                        note_info.get('desc') or '')
                    content_done = True
                except Exception as exc:
                    emit.log(f'AI 文案改写失败（{index}/{total}），保留原文：{exc}')
                if title_done or content_done:
                    emit.log(
                        f"AI 改写完成（{index}/{total}，"
                        f"{'标题' if title_done else ''}{'+' if title_done and content_done else ''}"
                        f"{'文案' if content_done else ''}）：{str(note_info.get('title_ai') or note_info.get('title'))[:24]}"
                    )
            note_list.append(note_info)
            emit.note(note_info)
        else:
            detail = str(msg)
            emit.log(f'抓取失败（{index}/{total}）：{msg}')
            if any(k in detail for k in RISK_KEYWORDS):
                consecutive_risk += 1
                if consecutive_risk >= RISK_MAX_CONSECUTIVE:
                    emit.log(
                        f'连续 {RISK_MAX_CONSECUTIVE} 次触发风控，任务已自动停止。'
                        '建议等待 30 分钟、调大采集间隔或增加小红书账号后再试'
                    )
                    break
                emit.log(
                    f'疑似触发风控限流，冷却 {RISK_COOLDOWN_SECONDS} 秒减少调用后继续'
                    f'（连续第 {consecutive_risk} 次，累计 {RISK_MAX_CONSECUTIVE} 次自动停止）'
                )
                _throttle(RISK_COOLDOWN_SECONDS, should_stop)
            else:
                consecutive_risk = 0
        emit.progress(index, total, '抓取')
        if index < total:
            _throttle(spec.delay_seconds, should_stop)
    if skipped:
        emit.log(f'已按保存选项跳过 {skipped} 篇笔记（未勾选对应类型）')

    # 停止 = 停止抓取新笔记；已抓到的结果必须完整导出，导出阶段不再响应停止
    never_stop = lambda: False  # noqa: E731

    task_name = sanitize_name(spec.task_name)
    base_dir = os.path.join(spec.output_dir, task_name)
    media_dir = os.path.join(base_dir, 'media')
    excel_dir = os.path.join(base_dir, 'excel')
    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(excel_dir, exist_ok=True)

    zip_path = ''
    if note_list and spec.zip_export:
        zip_notes = [n for n in note_list if n.get('note_type') != '视频']
        video_in_zip = len(note_list) - len(zip_notes)
        if video_in_zip:
            emit.log(f'打包跳过 {video_in_zip} 篇视频笔记（小绿书为图文格式，仅支持图文）')
        if zip_notes:
            from desktop.xhs_export import export_xiaolvsu_zip
            zip_path = export_xiaolvsu_zip(
                zip_notes, base_dir, spec.task_name, never_stop, emit,
            ) or ''
        else:
            emit.log('勾选的笔记均为视频，无图文可打包')

    if note_list and spec.want_media:
        emit.progress(0, len(note_list), '保存媒体')
        for index, note_info in enumerate(note_list, start=1):
            try:
                download_note(note_info, media_dir, spec.save_choice)
            except Exception as exc:
                emit.log(f'媒体保存失败（{index}/{len(note_list)}）：{exc}')
            emit.progress(index, len(note_list), '保存媒体')

    excel_path = ''
    if note_list and spec.save_excel:
        excel_path = os.path.join(excel_dir, f'{task_name}.xlsx')
        save_to_xlsx(note_list, excel_path)
        emit.log(f'Excel 已保存：{excel_path}')

    if note_list and not (spec.zip_export or spec.want_media or spec.save_excel):
        emit.log('未选择任何保存方式，仅完成抓取')

    return {
        'total': total,
        'got': len(note_list),
        'excel': excel_path,
        'zip': zip_path,
        'media_dir': media_dir if spec.want_media else '',
        'base_dir': base_dir,
        'seconds': round(time.time() - started, 1),
        'stopped': should_stop(),
    }
