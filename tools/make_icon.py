# -*- coding: utf-8 -*-
"""生成 EarShot 的图标（Pillow 画，不依赖任何美术资源）。

图形：深色圆角底 + 五条声波 —— "顺风耳"就是听 + 给词，
声波比耳朵在 16px 下更认得出（耳朵的细节一缩小就糊成一团）。

用法：
    python tools/make_icon.py            # 写到 assets/icon.ico 和 assets/icon.png
"""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT_DIR = os.path.join(ROOT, 'assets')
S = 1024                      # 先用大画布画，再缩 —— 边缘才不毛糙

BARS = [0.30, 0.56, 0.88, 0.56, 0.30]     # 每条声波占画布高度的比例
COL_A = (0x3b, 0x82, 0xe0)                # 蓝
COL_B = (0x5a, 0xd8, 0xc0)                # 青


def _bg(size):
    """圆角矩形底 —— 竖向渐变，比纯色有质感。"""
    grad = Image.new('RGB', (1, size))
    top, bot = (0x1c, 0x20, 0x28), (0x0d, 0x0f, 0x14)
    for y in range(size):
        t = y / max(1, size - 1)
        grad.putpixel((0, y), tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))
    bg = grad.resize((size, size))
    mask = Image.new('L', (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=int(size * 0.22), fill=255)
    out = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    out.paste(bg, (0, 0), mask)
    return out


def build(size=S):
    img = _bg(size)
    d = ImageDraw.Draw(img)
    n = len(BARS)
    bw = size * 0.100                       # 条宽
    gap = size * 0.052
    total = n * bw + (n - 1) * gap
    x0 = (size - total) / 2.0
    cy = size / 2.0
    for i, h in enumerate(BARS):
        hh = size * h
        x = x0 + i * (bw + gap)
        t = i / float(n - 1) if n > 1 else 0.0
        col = tuple(int(COL_A[k] + (COL_B[k] - COL_A[k]) * t) for k in range(3)) + (255,)
        d.rounded_rectangle([x, cy - hh / 2, x + bw, cy + hh / 2],
                            radius=bw / 2.0, fill=col)
    return img


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    img = build()
    png = os.path.join(OUT_DIR, 'icon.png')
    img.resize((512, 512), Image.LANCZOS).save(png)
    ico = os.path.join(OUT_DIR, 'icon.ico')
    img.save(ico, format='ICO',
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print('已生成:')
    print('  %s  (%d 字节)' % (png, os.path.getsize(png)))
    print('  %s  (%d 字节, 含 16/24/32/48/64/128/256 七种尺寸)' % (ico, os.path.getsize(ico)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
