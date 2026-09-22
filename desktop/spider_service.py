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


@dataclass
class TaskSpec:
    mode: str = 'search'            # search / urls / user
    query: str = ''
    require_num: int = 20
    sort_type: int = 0              # 0 综合 1 最新 2 最多点赞 3 最多评论 4 最多收藏
    note_type: int = 0              # 0 不限 1 视频 2 图文
    note_time: int = 0              # 0 不限 1 一天内 2 一周内 3 半年内
    note_urls: list = field(default_factory=list)
    user_url: str = ''
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
    if spec.mode == 'urls':
        urls = [u.strip() for u in spec.note_urls or [] if u.strip()]
        if not urls:
            raise RuntimeError('请至少填写一个笔记链接')
        return urls
    raise RuntimeError(f'未知任务模式：{spec.mode}')


RISK_KEYWORDS = ('461', '-629', '频繁', '风控', '验证码', '异常流量')


def _throttle(delay: float, should_stop) -> None:
    """带随机抖动的限速等待，0.2s 粒度响应停止请求。"""
    if delay <= 0:
        return
    target = time.monotonic() + random.uniform(delay * 0.7, delay * 1.3)
    while time.monotonic() < target and not should_stop():
        time.sleep(0.2)


def run_collection(auth, spec: TaskSpec, should_stop, emit):
    """执行一次采集任务，返回摘要 dict。

    emit 需要提供：log(str) / note(dict) / progress(done, total, stage)
    should_stop() 返回 True 时在安全点中断。
    """
    from spider.spider import Data_Spider
    from xhs_utils.data_util import download_note, save_to_xlsx

    started = time.time()
    spider = Data_Spider(auth)
    api = spider.xhs_apis.bootstrap()
    emit.log('已建立小红书会话，开始收集笔记链接…')

    urls = collect_note_urls(api, spec)
    total = len(urls)
    emit.progress(0, total, '抓取')
    emit.log(f'共找到 {total} 篇笔记，开始逐条抓取')

    ai_client = None
    if spec.ai_cfg and spec.ai_cfg.get('key'):
        from desktop.ai_client import AIClient
        ai_client = AIClient(
            spec.ai_cfg.get('base') or '', spec.ai_cfg.get('key') or '',
            spec.ai_cfg.get('model') or '', spec.ai_cfg.get('prompt') or '',
        )
        emit.log(f"AI 改写已开启（模型：{ai_client.model}）")
    if spec.delay_seconds > 0:
        emit.log(f'防风控限速已开启：每篇间隔约 {spec.delay_seconds:g} 秒')
    else:
        emit.log('警告：未开启限速，高频采集容易触发小红书风控')

    note_list = []
    for index, url in enumerate(urls, start=1):
        if should_stop():
            emit.log('任务已被用户停止')
            break
        try:
            success, msg, note_info = spider.spider_note(url)
        except Exception as exc:
            success, msg, note_info = False, exc, None
        if success and note_info:
            if ai_client is not None:
                try:
                    new_title, new_content = ai_client.rewrite(
                        note_info.get('title') or '', note_info.get('desc') or '',
                    )
                    note_info['title_ai'] = new_title
                    note_info['desc_ai'] = new_content
                    emit.log(f"AI 改写完成（{index}/{total}）：{new_title[:24]}")
                except Exception as exc:
                    emit.log(f'AI 改写失败（{index}/{total}），保留原文：{exc}')
            note_list.append(note_info)
            emit.note(note_info)
        else:
            detail = str(msg)
            emit.log(f'抓取失败（{index}/{total}）：{msg}')
            if any(k in detail for k in RISK_KEYWORDS):
                emit.log('疑似触发风控限流：建议停止任务，等待 10-30 分钟，'
                         '并调大采集间隔或减少数量后再试')
        emit.progress(index, total, '抓取')
        if index < total:
            _throttle(spec.delay_seconds, should_stop)

    task_name = sanitize_name(spec.task_name)
    base_dir = os.path.join(spec.output_dir, task_name)
    media_dir = os.path.join(base_dir, 'media')
    excel_dir = os.path.join(base_dir, 'excel')
    os.makedirs(media_dir, exist_ok=True)
    os.makedirs(excel_dir, exist_ok=True)

    zip_path = ''
    if note_list and spec.zip_export:
        from desktop.xhs_export import export_xiaolvsu_zip
        zip_path = export_xiaolvsu_zip(
            note_list, base_dir, spec.task_name, should_stop, emit,
        ) or ''

    if note_list and spec.want_media:
        emit.progress(0, len(note_list), '保存媒体')
        for index, note_info in enumerate(note_list, start=1):
            if should_stop():
                emit.log('保存媒体被停止')
                break
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
