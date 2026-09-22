# encoding: utf-8
"""小绿书导出：每篇笔记 = {24位note_id}{标题}/ 文件夹（1.jpg.. + 文案.txt），打包为单个 zip。

与 xiao 赛道管理「小绿书上传」的解析逻辑（UserTrackService.processNewFormatFolders）对齐：
- 文件夹名前 24 位十六进制会被服务端剥离作为小红书ID，剩余部分作为标题；
- 文案.txt 为 UTF-8 正文；图片按 1.jpg、2.jpg… 命名（全部规范化为 JPEG）。
"""
from __future__ import annotations

import io
import os
import re
import zipfile

import requests
from loguru import logger
from PIL import Image

from desktop.spider_service import sanitize_name

IMAGE_TIMEOUT = 20


def _download_as_jpg(url: str, dest_dir: str, index: int) -> bool:
    """下载图片并规范化保存为 {index}.jpg（webp/png 统一转 JPEG，保证 xiao 端扩展名过滤通过）。"""
    try:
        resp = requests.get(url, timeout=IMAGE_TIMEOUT, stream=True)
        resp.raise_for_status()
        data = resp.raw.read()
        image = Image.open(io.BytesIO(data))
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        image.save(os.path.join(dest_dir, f'{index}.jpg'), 'JPEG', quality=90)
        return True
    except Exception as exc:
        logger.warning(f'图片下载失败 {url[:80]}: {exc}')
        return False


def export_note_folder(note: dict, base_dir: str, should_stop=None,
                       progress=None) -> str | None:
    """把一篇笔记导出为 {note_id}{标题}/ 文件夹，返回文件夹路径；无图返回 None。"""
    title = note.get('title_ai') or note.get('title') or '无标题'
    folder_name = f"{note.get('note_id', '')}{sanitize_name(title)}"[:120]
    folder = os.path.join(base_dir, folder_name)
    os.makedirs(folder, exist_ok=True)

    images = [u for u in (note.get('image_list') or []) if u]
    saved = 0
    for index, url in enumerate(images, start=1):
        if should_stop is not None and should_stop():
            break
        if _download_as_jpg(url, folder, index):
            saved += 1
        if progress is not None:
            progress(index, len(images))
    if saved == 0:
        os.rmdir(folder)
        return None

    content = note.get('desc_ai') or note.get('desc') or ''
    with open(os.path.join(folder, '文案.txt'), 'w', encoding='utf-8') as f:
        f.write(content)
    return folder


def export_xiaolvsu_zip(note_list: list, out_dir: str, task_name: str,
                        should_stop=None, emit=None) -> str | None:
    """把整批笔记打包成一个小绿书 zip，返回 zip 路径。"""
    import time

    if emit:
        emit.log('开始导出小绿书压缩包…')
    root = os.path.join(out_dir, sanitize_name(task_name) or time.strftime('%Y%m%d_%H%M%S'))
    folders_dir = os.path.join(root, 'xiaolvsu')
    os.makedirs(folders_dir, exist_ok=True)

    packed = 0
    for note in note_list:
        if should_stop is not None and should_stop():
            break
        folder = export_note_folder(note, folders_dir, should_stop)
        if folder is None:
            if emit:
                emit.log(f"跳过无图片笔记：{(note.get('title_ai') or note.get('title') or '')[:20]}")
            continue
        packed += 1
        if emit:
            emit.progress(packed, len(note_list), '打包')

    if packed == 0:
        if emit:
            emit.log('没有可打包的笔记（均无图片）')
        return None

    zip_path = os.path.join(root, f'{sanitize_name(task_name)}_小绿书.zip')
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        for folder_name in sorted(os.listdir(folders_dir)):
            folder = os.path.join(folders_dir, folder_name)
            if not os.path.isdir(folder):
                continue
            for file_name in sorted(os.listdir(folder)):
                file_path = os.path.join(folder, file_name)
                zf.write(file_path, arcname=os.path.join(folder_name, file_name))
    if emit:
        emit.log(f'小绿书压缩包已生成（{packed} 篇）：{zip_path}')
    return zip_path
