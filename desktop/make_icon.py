# encoding: utf-8
"""生成应用图标：assets/app.png(1024) / app.icns(mac) / app.ico(win)。

    .venv/bin/python desktop/make_icon.py
"""
from __future__ import annotations

import os
import subprocess

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, 'assets')

# 与应用内 logo 一致的三段对角渐变：橙 → 紫 → 青
STOPS = [(0.0, (255, 138, 92)), (0.55, (108, 92, 231)), (1.0, (78, 201, 212))]


def _lerp(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def _gradient_color(t: float) -> tuple:
    for (t1, c1), (t2, c2) in zip(STOPS, STOPS[1:]):
        if t <= t2:
            return _lerp(c1, c2, (t - t1) / (t2 - t1))
    return STOPS[-1][1]


def make_png(size: int = 1024) -> Image.Image:
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    gradient = Image.new('RGBA', (size, size))
    total = (size - 1) * 2
    data = []
    for y in range(size):
        for x in range(size):
            data.append(_gradient_color((x + y) / total) + (255,))
    gradient.putdata(data)

    mask = Image.new('L', (size, size), 0)
    draw = ImageDraw.Draw(mask)
    radius = int(size * 0.22)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    img.paste(gradient, (0, 0), mask)

    font = None
    for candidate in (
        '/System/Library/Fonts/SFNS.ttf',
        '/System/Library/Fonts/Helvetica.ttc',
        '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
        'C:/Windows/Fonts/arialbd.ttf',
    ):
        try:
            font = ImageFont.truetype(candidate, int(size * 0.42))
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    text = ImageDraw.Draw(img)
    bbox = text.textbbox((0, 0), 'YC', font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    text.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]),
              'YC', font=font, fill=(255, 255, 255, 255))
    return img


def make_icns(png: Image.Image):
    iconset = os.path.join(ASSETS, 'app.iconset')
    os.makedirs(iconset, exist_ok=True)
    sizes = [16, 32, 128, 256, 512]
    for s in sizes:
        png.resize((s, s), Image.LANCZOS).save(os.path.join(iconset, f'icon_{s}x{s}.png'))
        png.resize((s * 2, s * 2), Image.LANCZOS).save(
            os.path.join(iconset, f'icon_{s}x{s}@2x.png'))
    subprocess.run(['iconutil', '-c', 'icns', iconset, '-o',
                    os.path.join(ASSETS, 'app.icns')], check=True)


def make_ico(png: Image.Image):
    png.save(os.path.join(ASSETS, 'app.ico'),
             format='ICO',
             sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == '__main__':
    os.makedirs(ASSETS, exist_ok=True)
    icon = make_png()
    icon.save(os.path.join(ASSETS, 'app.png'))
    try:
        make_icns(icon)
        print('icns ok')
    except Exception as exc:
        print(f'icns 跳过（非 mac 或 iconutil 不可用）：{exc}')
    make_ico(icon)
    print('图标已生成到 assets/')
