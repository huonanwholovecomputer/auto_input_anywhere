# -*- coding: utf-8 -*-
"""生成程序图标（当下不需要重新设计，改配色/键位时重跑本脚本即可）。

用法：
    python tools/make_icon.py

产物：
    assets/icon.ico   多尺寸（16/20/24/32/40/48/64/128/256），给窗口和 exe 用
    assets/icon.png   512x512，给 README / 发布页用

设计：圆角方形渐变底（蓝 → 紫蓝）+ 白色键盘（三排键帽 + 底部空格条）。
16px 时仍能看出「白底蓝键」的键盘轮廓，深色浅色任务栏上都清楚。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"

S = 1024                      # 先在 1024 画，再降采样到各尺寸
CORNER = 0.225                # 圆角半径占边长比例（Win11 风格）
C1 = (61, 123, 255)           # #3D7BFF
C2 = (122, 90, 248)           # #7A5AF8
KEY = (59, 108, 245)          # #3B6CF5
WHITE = (255, 255, 255)

ICO_SIZES = [256, 128, 64, 48, 40, 32, 24, 20, 16]


def gradient(size: int) -> Image.Image:
    """左上到右下的线性渐变"""
    t = (np.meshgrid(np.linspace(0, 1, size), np.linspace(0, 1, size))[0]
         + np.meshgrid(np.linspace(0, 1, size), np.linspace(0, 1, size))[1]) / 2.0
    img = np.zeros((size, size, 3), dtype=np.uint8)
    for i in range(3):
        img[..., i] = (C1[i] * (1 - t) + C2[i] * t).astype(np.uint8)
    return Image.fromarray(img, "RGB")


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1],
                                           radius=radius, fill=255)
    return mask


def draw_icon() -> Image.Image:
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    # 键盘机身
    bx0, by0, bx1, by1 = 0.09 * S, 0.26 * S, 0.91 * S, 0.74 * S
    d.rounded_rectangle([bx0, by0, bx1, by1], radius=0.075 * S, fill=WHITE)

    pad, gap = 0.055 * S, 0.028 * S
    x0, x1 = bx0 + pad, bx1 - pad
    y0, y1 = by0 + pad, by1 - pad
    rows = 3
    rh = (y1 - y0 - gap * (rows - 1)) / rows
    for row in range(rows):
        ry0 = y0 + row * (rh + gap)
        ry1 = ry0 + rh
        if row == rows - 1:                       # 底部空格条
            d.rounded_rectangle([x0, ry0, x1, ry1], radius=rh * 0.32, fill=KEY)
            continue
        cols = 5
        kw = (x1 - x0 - gap * (cols - 1)) / cols
        for i in range(cols):
            kx0 = x0 + i * (kw + gap)
            d.rounded_rectangle([kx0, ry0, kx0 + kw, ry1],
                                radius=min(rh, kw) * 0.30, fill=KEY)

    out = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    out.paste(gradient(S), (0, 0), rounded_mask(S, int(S * CORNER)))
    return Image.alpha_composite(out, layer)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = draw_icon()

    master.resize((512, 512), Image.LANCZOS).save(ASSETS / "icon.png", "PNG",
                                                  optimize=True)
    # 每个尺寸单独降采样，比让 PIL 内部逐级缩放更干净
    frames = [master.resize((s, s), Image.LANCZOS) for s in ICO_SIZES]
    frames[0].save(ASSETS / "icon.ico", format="ICO",
                   sizes=[(s, s) for s in ICO_SIZES],
                   append_images=frames[1:])
    for p in (ASSETS / "icon.ico", ASSETS / "icon.png"):
        print("%-20s %7d bytes" % (p.name, p.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
