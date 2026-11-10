# -*- coding: utf-8 -*-
"""
把生成的图标写进 chrome.exe / chrome.dll 的 PE 资源里。

策略：原位替换，不改变任何资源目录结构。
- 每张图的数据长度保持不变，新数据后面补零
- DIB 格式由头部决定大小，PNG 由 IEND 决定，尾部补零都被忽略
- 只改 .rsrc 段的数据，不碰代码
"""
import io
import os
import struct
import sys

import pefile
from PIL import Image

APP_DIR = r"D:\KZBuild\App"
ICON_DIR = r"C:\KZPublish\kz-profile-manager\assets"

# chrome.exe / chrome.dll 里 IDR_MAINFRAME 的 7 张图
# (尺寸, 位深, 槽位字节数)
LAYOUT = [
    (16, 8, 1384),
    (32, 8, 2216),
    (48, 8, 3752),
    (16, 32, 1128),
    (32, 32, 4264),
    (48, 48, 9640),
    (256, 32, 31619),
]


def load_icon(size: int) -> Image.Image:
    p = os.path.join(ICON_DIR, "icon-%d.png" % size)
    return Image.open(p).convert("RGBA")


def dib_32bpp(img: Image.Image) -> bytes:
    """32 位 DIB（BITMAPINFOHEADER + BGRA 像素 + AND 掩码）。"""
    w, h = img.size
    px = img.load()

    header = struct.pack(
        "<IiiHHIIiiII",
        40, w, h * 2, 1, 32, 0, w * h * 4, 0, 0, 0, 0)

    xor = bytearray()
    for y in range(h - 1, -1, -1):              # 自下而上
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))

    mask_row = ((w + 31) // 32) * 4
    mask = bytes(mask_row * h)                  # 全 0 = 不透明，靠 alpha 通道

    return header + bytes(xor) + mask


def dib_8bpp(img: Image.Image) -> bytes:
    """8 位 DIB（带 256 色调色板）。"""
    w, h = img.size

    # 量化到 256 色（保留 alpha）
    q = img.convert("RGBA").quantize(colors=256, method=Image.FASTOCTREE)
    pal = q.getpalette() or []
    idx = q.load()

    # 调色板补齐到 256 项
    palette = bytearray()
    for i in range(256):
        if i * 3 + 2 < len(pal):
            r, g, b = pal[i * 3], pal[i * 3 + 1], pal[i * 3 + 2]
        else:
            r = g = b = 0
        palette += bytes((b, g, r, 0))

    header = struct.pack(
        "<IiiHHIIiiII",
        40, w, h * 2, 1, 8, 0, w * h, 0, 0, 0, 0)

    xor = bytearray()
    for y in range(h - 1, -1, -1):
        for x in range(w):
            xor.append(idx[x, y] & 0xFF)

    mask_row = ((w + 31) // 32) * 4
    mask = bytearray(mask_row * h)
    # 透明像素在掩码里置 1
    src = img.load()
    for y in range(h):
        row = h - 1 - y
        for x in range(w):
            if src[x, y][3] < 128:
                mask[row * mask_row + (x // 8)] |= (0x80 >> (x % 8))

    return header + bytes(palette) + bytes(xor) + bytes(mask)


def png_bytes(img: Image.Image, budget: int) -> bytes:
    """PNG，超出预算就逐步量化。"""
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    data = buf.getvalue()
    if len(data) <= budget:
        return data

    for colors in (192, 128, 96, 64, 48, 32, 24, 16):
        q = img.quantize(colors=colors, method=Image.FASTOCTREE)
        buf = io.BytesIO()
        q.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= budget:
            print("      量化到 %d 色后 %d 字节（预算 %d）" % (colors, len(data), budget))
            return data

    raise RuntimeError("256x256 PNG 压不到 %d 字节以内（最小 %d）" % (budget, len(data)))


def build_images():
    out = []
    for size, bpp, slot in LAYOUT:
        img = load_icon(size)
        if size == 256:
            data = png_bytes(img, slot)
        elif bpp == 8:
            data = dib_8bpp(img)
        else:
            data = dib_32bpp(img)

        if len(data) > slot:
            raise RuntimeError("%dx%d %dbpp 需要 %d 字节，槽位只有 %d"
                               % (size, size, bpp, len(data), slot))
        padded = data + b"\x00" * (slot - len(data))
        out.append((size, bpp, slot, padded))
        print("    %3dx%-3d %2dbpp  %6d/%6d 字节" % (size, size, bpp, len(data), slot))
    return out


def find_group(pe, key=None):
    """按 id 或名字找图标组。返回 [(id, name, offset, size), ...]

    chrome.exe 里的组是按名字存的（IDR_MAINFRAME），
    chrome.dll 里是按数字 id（101）存的，两种都要支持。
    """
    res = []
    for e in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if e.id != 14:                          # RT_GROUP_ICON
            continue
        for sub in e.directory.entries:
            gid = sub.id
            gname = None
            raw_name = getattr(sub, "name", None)
            if raw_name is not None:
                try:
                    gname = str(raw_name)
                except Exception:
                    gname = None
            d = sub.directory.entries[0].data.struct
            res.append((gid, gname, d.OffsetToData, d.Size))

    if key is not None:
        if isinstance(key, str):
            res = [r for r in res if r[1] == key]
        else:
            res = [r for r in res if r[0] == key]
    return res


def group_entries(pe, off, size):
    """解析 GRPICONDIR，返回 [(nID, bytesInRes), ...]"""
    data = pe.get_data(off, size)
    _r, _t, cnt = struct.unpack_from("<HHH", data, 0)
    out = []
    for i in range(cnt):
        base = 6 + i * 14
        _w, _h, _c, _r2, _p, _b, bir, nid = struct.unpack_from("<BBBBHHIH", data, base)
        out.append((nid, bir))
    return out


def icon_data_offsets(pe):
    """RT_ICON id -> (文件偏移, 大小)

    注意：资源数据目录里的 OffsetToData 是 RVA，不是文件偏移。
    直接用会写到完全错误的位置（甚至把文件撑大），必须转换。
    """
    out = {}
    for e in pe.DIRECTORY_ENTRY_RESOURCE.entries:
        if e.id != 3:                           # RT_ICON
            continue
        for sub in e.directory.entries:
            iid = sub.id if sub.id is not None else sub.name
            d = sub.directory.entries[0].data.struct
            foff = pe.get_offset_from_rva(d.OffsetToData)
            out[iid] = (foff, d.Size)
    return out


def patch_file(path: str, group_id, label: str):
    print("\n=== %s  (组 %s) ===" % (os.path.basename(path), group_id))
    pe = pefile.PE(path)
    groups = find_group(pe, group_id)
    if not groups:
        pe.close()
        raise RuntimeError("找不到图标组 %s" % group_id)

    gid, gname, goff, gsize = groups[0]
    entries = group_entries(pe, goff, gsize)
    print("  组里有 %d 张图" % len(entries))

    icons = icon_data_offsets(pe)
    images = build_images()

    if len(entries) != len(images):
        pe.close()
        raise RuntimeError("组内图数 %d 与预期 %d 不符" % (len(entries), len(images)))

    raw = bytearray(open(path, "rb").read())
    orig_len = len(raw)

    for (nid, bir), (size, bpp, slot, data) in zip(entries, images):
        if nid not in icons:
            pe.close()
            raise RuntimeError("RT_ICON %s 不存在" % nid)
        off, dsize = icons[nid]
        if off + dsize > len(raw):
            pe.close()
            raise RuntimeError("RT_ICON %s 偏移 0x%X + %d 超出文件大小 %d"
                               % (nid, off, dsize, len(raw)))
        if dsize != slot:
            print("  ! RT_ICON %s 实际 %d 字节，预期 %d，按实际长度写" % (nid, dsize, slot))
        n = min(len(data), dsize)
        raw[off:off + n] = data[:n]
        # 剩余补零
        if n < dsize:
            raw[off + n:off + dsize] = b"\x00" * (dsize - n)
        print("    RT_ICON %-3s @0x%08X  %d 字节" % (nid, off, dsize))

    pe.close()

    if len(raw) != orig_len:
        pe.close()
        raise RuntimeError("内部错误：写入后大小变化 %d -> %d" % (orig_len, len(raw)))

    with open(path, "wb") as f:
        f.write(raw)
    print("  ✓ 已写入 %s（%d 字节，大小未变）" % (label, len(raw)))


if __name__ == "__main__":
    exe = os.path.join(APP_DIR, "chrome.exe")
    dll = os.path.join(APP_DIR, "chrome.dll")

    # chrome.exe 的 IDR_MAINFRAME 组 id 是 1
    patch_file(exe, "IDR_MAINFRAME", "chrome.exe 图标")
    # chrome.dll 的 IDR_MAINFRAME 是组 101
    patch_file(dll, 101, "chrome.dll 图标")

    print("\n全部完成。")
