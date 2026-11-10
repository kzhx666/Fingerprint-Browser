# -*- coding: utf-8 -*-
"""
生成 KZ 浏览器图标。

设计：渐变圆角方块（蓝→紫）+ 白色指纹脊线。
小尺寸减少脊线数量，避免糊成一团。
"""
import os

from PIL import Image, ImageDraw

OUT_DIR = r"C:\KZPublish\kz-profile-manager\assets"
os.makedirs(OUT_DIR, exist_ok=True)

C1 = (76, 141, 255)     # #4C8DFF
C2 = (124, 92, 255)     # #7C5CFF
WHITE = (255, 255, 255)


def make_base(size: int) -> Image.Image:
    ss = 4
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    grad = Image.new("RGB", (s, s))
    px = grad.load()
    for y in range(s):
        for x in range(0, s, 2):
            t = max(0.0, min(1.0, (x + y) / (2.0 * s)))
            col = tuple(int(C1[i] + (C2[i] - C1[i]) * t) for i in range(3))
            px[x, y] = col
            if x + 1 < s:
                px[x + 1, y] = col

    mask = Image.new("L", (s, s), 0)
    md = ImageDraw.Draw(mask)
    md.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=255)
    img.paste(grad, (0, 0), mask)

    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.rounded_rectangle(
        [int(s * 0.06), int(s * 0.05), int(s * 0.94), int(s * 0.50)],
        radius=int(s * 0.18), fill=(255, 255, 255, 26))
    img = Image.alpha_composite(img, hl)

    return img.resize((size, size), Image.LANCZOS)


def arc_count(size: int) -> int:
    if size <= 16:
        return 3
    if size <= 24:
        return 3
    if size <= 48:
        return 4
    if size <= 96:
        return 5
    return 6


def line_width(size: int, s: int) -> int:
    """小图标线条要粗，否则缩下去就没了。"""
    ratio = {
        16: 0.085,
        24: 0.070,
        32: 0.058,
        48: 0.046,
        64: 0.040,
        128: 0.034,
        256: 0.030,
    }.get(size, 0.032)
    return max(2, int(s * ratio))


def ridge_range(size: int):
    """小图标脊线范围收窄，避免贴边。"""
    if size <= 16:
        return 0.17, 0.38
    if size <= 24:
        return 0.15, 0.39
    if size <= 32:
        return 0.13, 0.40
    if size <= 48:
        return 0.12, 0.40
    return 0.10, 0.41


def draw_fingerprint(size: int) -> Image.Image:
    ss = 4
    s = size * ss
    layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    cx, cy = s / 2.0, s / 2.0
    n = arc_count(size)

    lw = line_width(size, s)
    r_in, r_out = ridge_range(size)
    for i in range(n):
        frac = r_in + (r_out - r_in) * (i / max(1, n - 1))
        r = s * frac
        # 开口朝下，越外圈开口越大（像真指纹）
        spread = (18 if size <= 24 else 26) + i * 7
        a0 = 90 + spread
        a1 = 90 + 360 - spread
        box = [cx - r, cy - r * 1.02, cx + r, cy + r * 1.02]
        d.arc(box, a0, a1, fill=WHITE + (255,), width=lw)

    # 中心斗形起点：一段短竖线 + 圆头
    top = cy - s * 0.085
    bot = cy + s * 0.075
    d.line([(cx, top), (cx, bot)], fill=WHITE + (255,), width=lw)
    rr = lw / 2.0
    d.ellipse([cx - rr, top - rr, cx + rr, top + rr], fill=WHITE + (255,))
    d.ellipse([cx - rr, bot - rr, cx + rr, bot + rr], fill=WHITE + (255,))

    return layer.resize((size, size), Image.LANCZOS)


def render(size: int) -> Image.Image:
    return Image.alpha_composite(make_base(size), draw_fingerprint(size))


SIZES = [16, 24, 32, 48, 64, 128, 256]

images = {}
for sz in SIZES:
    img = render(sz)
    images[sz] = img
    img.save(os.path.join(OUT_DIR, "icon-%d.png" % sz))

ico_path = os.path.join(OUT_DIR, "app.ico")
images[256].save(ico_path, format="ICO",
                 sizes=[(s, s) for s in SIZES], bitmap_format="bmp")

print("生成完成:", ico_path, os.path.getsize(ico_path), "字节")
for sz in SIZES:
    p = os.path.join(OUT_DIR, "icon-%d.png" % sz)
    print("  icon-%d.png  %d 字节" % (sz, os.path.getsize(p)))
