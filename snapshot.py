# -*- coding: utf-8 -*-
"""实例快照：把一个实例的完整状态存成模板，随时还原到新实例。

用法:
  python snapshot.py create  --from 主控1 --name 基线模板
  python snapshot.py create  --from 主控1 --name 完整备份 --mode full
  python snapshot.py list
  python snapshot.py info    --name 基线模板
  python snapshot.py restore --name 基线模板 --to 实例2
  python snapshot.py restore --name 基线模板 --to 实例3 --new-seed
  python snapshot.py delete  --name 基线模板

lean 模式（默认）只带"有意义的"数据：书签 / 密码 / 历史 / 扩展 / 登录态，
跳过各种缓存。full 模式复制整个 Data 目录（仍然跳过纯缓存目录，除非 --with-cache）。

还原时会自动把 Secure Preferences 里扩展的绝对 path 改写成目标实例的路径，
否则扩展会从源实例目录加载 —— 源实例一旦删除，还原出来的实例就废了。
"""
import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.environ.get("KZ_ROOT") or os.path.dirname(os.path.abspath(__file__))
SNAP_ROOT = os.path.join(ROOT, "_snapshots")
BACKUP_ROOT = os.path.join(ROOT, "_backup")

RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}

def validate_name(name, desc="名称"):
    if not name or not str(name).strip():
        sys.exit("%s不能为空" % desc)
    name = str(name).strip()
    if len(name) > 120:
        sys.exit("%s过长（最大 120 字符）" % desc)
    for c in name:
        if ord(c) < 32:
            sys.exit("%s包含非法控制字符" % desc)
    if any(c in name for c in ('/', '\\', ':')):
        sys.exit("%s不能包含路径分隔符或冒号" % desc)
    if ".." in name:
        sys.exit("%s不能包含 .." % desc)
    if os.path.isabs(name):
        sys.exit("%s不能是绝对路径" % desc)
    stem = name.split('.')[0].strip().upper()
    if stem in RESERVED_NAMES:
        sys.exit("%s是 Windows 系统保留名称（如 CON, NUL 等）" % desc)
    if name != name.rstrip(" ."):
        sys.exit("%s不能以空格或点结尾" % desc)
    invalid_chars = set('<>"/\\|?*:\0')
    if any(c in invalid_chars for c in name):
        sys.exit("%s包含非法字符" % desc)
    return name

def is_under(root, path):
    try:
        r = os.path.abspath(root).rstrip(os.sep) + os.sep
        p = os.path.abspath(path)
        return p.startswith(r) or p == os.path.abspath(root)
    except Exception:
        return False


# lean 模式在 Data/ 与 Data/Default/ 下要带上的条目
LEAN_DATA = ["Local State"]
LEAN_PROFILE = [
    "Bookmarks",
    "History",
    "Login Data",
    "Web Data",
    "Preferences",
    "Secure Preferences",
    "Extensions",
    "Favicons",
    "Top Sites",
    "Shortcuts",
    "Network",
    "Local Storage",
    "Session Storage",
    "Extension State",
    "Local Extension Settings",
    "Sync Data",
]

# 永远跳过的纯缓存目录
CACHE_DIRS = {
    "Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache",
    "GraphiteDawnCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache",
    "CacheStorage", "ScriptCache", "Crashpad", "blob_storage",
    "component_crx_cache", "extensions_crx_cache", "optimization_guide_hint_cache",
    "optimization_guide_model_store", "Safe Browsing", "Safe Browsing Network",
    "AutofillStrikeDatabase", "BudgetDatabase", "Feature Engagement Tracker",
    "PersistentOriginTrials", "shared_proto_db", "Site Characteristics Database",
    "GCM Store", "Platform Notifications", "Segmentation Platform",
    "Media Device Salts", "ClientCertificates", "chrome_cart_db",
}


def instance_dir(name):
    validate_name(name, "实例名")
    p = os.path.join(ROOT, name)
    if not is_under(ROOT, p):
        sys.exit("实例目录越界: %s" % p)
    return p


def profile_dir(name):
    return os.path.join(instance_dir(name), "Data", "Default")


def snap_dir(name):
    validate_name(name, "快照名")
    p = os.path.join(SNAP_ROOT, name)
    if not is_under(SNAP_ROOT, p):
        sys.exit("快照目录越界: %s" % p)
    return p


def list_instances():
    out = []
    if not os.path.isdir(ROOT):
        return out
    for n in sorted(os.listdir(ROOT)):
        d = os.path.join(ROOT, n)
        if os.path.isdir(d) and os.path.exists(os.path.join(d, "profile.json")):
            out.append(n)
    return out


def is_running(name):
    target = os.path.join(instance_dir(name), "Data").lower()
    try:
        import subprocess
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        for line in (r.stdout or "").splitlines():
            if target in line.lower():
                return True
    except Exception:
        pass
    return False


def dir_size(path):
    total = 0
    for base, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(base, f))
            except Exception:
                pass
    return total


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0


LINK_STATS = {"link": 0, "copy": 0}


def _link_or_copy(src_file, dst_file, base_file=None):
    """内容没变就和旧快照做硬链接，否则真复制。

    快照里最大的是 Extensions/（几百 MB），但它几乎不变。硬链接让第二个快照
    只占新增部分的空间，创建也快得多。硬链接共享数据，删掉任一个快照都不会
    影响另一个（链接计数），还原时 copytree 会复制真实内容。
    """
    if base_file and os.path.exists(base_file) and not os.path.exists(dst_file):
        try:
            a = os.stat(src_file)
            b = os.stat(base_file)
            if a.st_size == b.st_size and int(a.st_mtime) == int(b.st_mtime):
                os.link(base_file, dst_file)
                LINK_STATS["link"] += 1
                return
        except Exception:
            pass
    shutil.copy2(src_file, dst_file)
    LINK_STATS["copy"] += 1


def copy_skip_cache(src, dst, base=None):
    """复制目录，跳过纯缓存子目录；base 是旧快照的同名目录，用于硬链接去重。"""
    os.makedirs(dst, exist_ok=True)
    for cur, dirs, files in os.walk(src):
        rel = os.path.relpath(cur, src)
        dirs[:] = [d for d in dirs if d not in CACHE_DIRS]
        out_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out_dir, exist_ok=True)
        base_dir = os.path.join(base, rel) if base else None
        for f in files:
            s = os.path.join(cur, f)
            try:
                if os.path.getsize(s) > 2 * 1024 * 1024 * 1024:
                    continue
                b = os.path.join(base_dir, f) if base_dir else None
                _link_or_copy(s, os.path.join(out_dir, f), b)
            except Exception:
                pass


def pick_base(source, exclude_name):
    """挑一个同源的最近快照作为硬链接基准。"""
    if not os.path.isdir(SNAP_ROOT):
        return None
    best = None
    for n in os.listdir(SNAP_ROOT):
        if n == exclude_name:
            continue
        m = read_meta(n)
        if not m or m.get("source") != source:
            continue
        d = os.path.join(SNAP_ROOT, n, "data")
        if not os.path.isdir(d):
            continue
        if best is None or (m.get("created", "") > best[1]):
            best = (d, m.get("created", ""))
    return best[0] if best else None


# ------------------------------------------------------------------ create

def cmd_create(a):
    src = a.source
    if src not in list_instances():
        sys.exit("实例不存在: %s（现有：%s）" % (src, ", ".join(list_instances()) or "无"))
    if is_running(src) and not a.force:
        sys.exit("实例「%s」正在运行，先关掉再快照（或加 --force）" % src)

    sd = snap_dir(a.name)
    if os.path.exists(sd):
        if not a.force:
            sys.exit("快照「%s」已存在，加 --force 覆盖" % a.name)
        shutil.rmtree(sd)
    data_out = os.path.join(sd, "data")
    os.makedirs(data_out, exist_ok=True)

    src_data = os.path.join(instance_dir(src), "Data")
    src_prof = os.path.join(src_data, "Default")

    LINK_STATS["link"] = 0
    LINK_STATS["copy"] = 0
    base_data = None
    if not a.no_incremental:
        if a.base:
            base_data = os.path.join(snap_dir(a.base), "data")
            if not os.path.isdir(base_data):
                sys.exit("基准快照不存在或缺少 data: %s" % a.base)
        else:
            base_data = pick_base(src, a.name)
    if base_data:
        print("[增量] 基准快照数据: %s" % base_data)

    if a.mode == "full":
        copy_skip_cache(src_data, data_out, base_data)
    else:
        for item in LEAN_DATA:
            s = os.path.join(src_data, item)
            if os.path.isfile(s):
                b = os.path.join(base_data, item) if base_data else None
                _link_or_copy(s, os.path.join(data_out, item), b)
        prof_out = os.path.join(data_out, "Default")
        os.makedirs(prof_out, exist_ok=True)
        base_prof = os.path.join(base_data, "Default") if base_data else None
        for item in LEAN_PROFILE:
            s = os.path.join(src_prof, item)
            if os.path.isfile(s):
                b = os.path.join(base_prof, item) if base_prof else None
                _link_or_copy(s, os.path.join(prof_out, item), b)
            elif os.path.isdir(s):
                # 基准必须是"该目录在旧快照里的对应目录"，不是 base/Default
                b_dir = os.path.join(base_prof, item) if base_prof else None
                copy_skip_cache(s, os.path.join(prof_out, item), b_dir)

    # profile.json 模板（seed 留原值，还原时可选换新）
    tpl = {}
    pj = os.path.join(instance_dir(src), "profile.json")
    if os.path.exists(pj):
        try:
            with open(pj, encoding="utf-8-sig") as f:
                tpl = json.load(f)
        except Exception:
            tpl = {}
    with open(os.path.join(sd, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(tpl, f, ensure_ascii=False, indent=2)

    size = dir_size(sd)
    meta = {
        "name": a.name,
        "source": src,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "mode": a.mode,
        "size_bytes": size,
        "seed": tpl.get("seed", ""),
        "platform": tpl.get("platform", ""),
        "timezone": tpl.get("timezone", ""),
        "extensions": 0,
        "linked_files": LINK_STATS["link"],
        "copied_files": LINK_STATS["copy"],
    }
    ext_dir = os.path.join(data_out, "Default", "Extensions")
    if os.path.isdir(ext_dir):
        meta["extensions"] = len([d for d in os.listdir(ext_dir)
                                  if os.path.isdir(os.path.join(ext_dir, d))])
    with open(os.path.join(sd, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("快照已创建: %s" % a.name)
    print("  来源     %s" % src)
    print("  模式     %s" % a.mode)
    print("  大小     %s" % human(size))
    print("  文件     复用 %d / 新复制 %d" % (LINK_STATS["link"], LINK_STATS["copy"]))
    print("  扩展     %d 个" % meta["extensions"])
    print("  seed     %s / %s / %s" % (meta["seed"], meta["platform"], meta["timezone"]))
    print("  位置     %s" % sd)


# ------------------------------------------------------------------ list / info

def read_meta(name):
    p = os.path.join(snap_dir(name), "meta.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


def cmd_list(a):
    if not os.path.isdir(SNAP_ROOT):
        print("还没有任何快照。")
        return
    names = [d for d in sorted(os.listdir(SNAP_ROOT))
             if os.path.isdir(os.path.join(SNAP_ROOT, d))]
    if not names:
        print("还没有任何快照。")
        return
    print("%-20s %-8s %-9s %-5s %-16s %s" %
          ("名称", "模式", "大小", "扩展", "seed", "创建时间"))
    print("-" * 92)
    for n in names:
        m = read_meta(n)
        if not m:
            print("%-20s (缺少 meta.json)" % n)
            continue
        print("%-20s %-8s %-9s %-5s %-16s %s" % (
            n, m.get("mode", "?"), human(m.get("size_bytes", 0)),
            m.get("extensions", 0), str(m.get("seed", "")), m.get("created", "")))


def cmd_info(a):
    m = read_meta(a.name)
    if not m:
        sys.exit("快照不存在: %s" % a.name)
    for k in ("name", "source", "created", "mode", "size_bytes", "seed",
              "platform", "timezone", "extensions"):
        v = m.get(k)
        if k == "size_bytes":
            v = human(v or 0)
        print("%-12s %s" % (k, v))
    sd = snap_dir(a.name)
    prof = os.path.join(sd, "data", "Default")
    if os.path.isdir(prof):
        print("\n包含的条目:")
        for item in sorted(os.listdir(prof)):
            p = os.path.join(prof, item)
            if os.path.isdir(p):
                print("  [目录] %-28s %s" % (item, human(dir_size(p))))
            else:
                print("  [文件] %-28s %s" % (item, human(os.path.getsize(p))))


# ------------------------------------------------------------------ restore

def rewrite_extension_paths(profile_path, target_profile):
    """只修正绝对路径，相对路径原样保留。

    Chrome 对普通扩展存的是**相对 Extensions/ 的路径**（例如
    "<id>\\<version>_0"），本身就可移植 —— 之前把相对路径改写成绝对路径，
    反而让 Chrome 找不到扩展并把设置整批重置。
    需要处理的只有指向源实例的绝对路径（组件扩展、加载解包的扩展）。
    """
    if not os.path.exists(profile_path):
        return 0
    try:
        with open(profile_path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return 0
    settings = ((data.get("extensions") or {}).get("settings") or {})
    if not settings:
        return 0
    ext_root = os.path.join(target_profile, "Extensions")
    fixed = 0
    for eid, v in settings.items():
        if not isinstance(v, dict):
            continue
        cur = v.get("path")
        if not cur:
            continue
        if not os.path.isabs(cur):
            continue  # 相对路径：已经可移植，不动
        d = os.path.join(ext_root, eid)
        if not os.path.isdir(d):
            continue
        vers = [x for x in os.listdir(d) if os.path.isdir(os.path.join(d, x))]
        if not vers:
            continue
        vers.sort()
        want = os.path.join(d, vers[-1])
        if os.path.normcase(cur) != os.path.normcase(want):
            v["path"] = want
            fixed += 1
    if fixed:
        with open(profile_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    return fixed


def cmd_restore(a):
    sd = snap_dir(a.name)
    if not os.path.isdir(sd):
        sys.exit("快照不存在: %s" % a.name)
    meta = read_meta(a.name) or {}
    tgt = a.to
    if is_running(tgt) and not a.force:
        sys.exit("目标实例「%s」正在运行，先关掉（或加 --force）" % tgt)

    dst_data = os.path.join(instance_dir(tgt), "Data")
    dst_prof = os.path.join(dst_data, "Default")

    if os.path.isdir(dst_prof):
        bk = os.path.join(BACKUP_ROOT, time.strftime("%Y%m%d-%H%M%S"), tgt, "Default")
        os.makedirs(os.path.dirname(bk), exist_ok=True)
        try:
            shutil.copytree(dst_prof, bk, dirs_exist_ok=True)
            print("[备份] %s -> %s" % (tgt, bk))
        except Exception as e:  # noqa: BLE001
            print("[警告] 备份失败：%s" % e)
        shutil.rmtree(dst_prof, ignore_errors=True)
    os.makedirs(dst_data, exist_ok=True)

    src_data = os.path.join(sd, "data")
    if not os.path.isdir(src_data):
        sys.exit("快照数据缺失: %s" % src_data)
    shutil.copytree(src_data, dst_data, dirs_exist_ok=True)

    # profile.json
    tpl = {}
    tp = os.path.join(sd, "profile.json")
    if os.path.exists(tp):
        try:
            with open(tp, encoding="utf-8-sig") as f:
                tpl = json.load(f)
        except Exception:
            tpl = {}
    tpl["name"] = tgt
    if a.new_seed:
        import random
        tpl["seed"] = random.randint(10000, 999999)
        print("[seed] 已换新：%s" % tpl["seed"])
    with open(os.path.join(instance_dir(tgt), "profile.json"), "w",
              encoding="utf-8") as f:
        json.dump(tpl, f, ensure_ascii=False, indent=2)

    fixed = rewrite_extension_paths(os.path.join(dst_prof, "Secure Preferences"),
                                    dst_prof)
    if fixed:
        print("[扩展] 重写了 %d 条 path 指向目标实例" % fixed)

    print("已还原: %s -> %s" % (a.name, tgt))
    print("  模式     %s（快照创建于 %s）" % (meta.get("mode", "?"),
                                             meta.get("created", "?")))
    print("  seed     %s" % tpl.get("seed", ""))
    print("  下一步   管理器里点「启动」")


def cmd_clone(a):
    """直接克隆实例：复制配置，可选连数据目录（跳过缓存）。"""
    src = a.source
    if src not in list_instances():
        sys.exit("源实例不存在: %s" % src)
    if a.to in list_instances() and not a.force:
        sys.exit("目标实例已存在: %s（加 --force 覆盖）" % a.to)
    if is_running(src):
        sys.exit("源实例「%s」正在运行，先关掉" % src)

    dst_dir = instance_dir(a.to)
    os.makedirs(dst_dir, exist_ok=True)

    src_pj = os.path.join(instance_dir(src), "profile.json")
    tpl = {}
    if os.path.exists(src_pj):
        try:
            with open(src_pj, encoding="utf-8-sig") as f:
                tpl = json.load(f)
        except Exception:
            tpl = {}
    tpl["name"] = a.to
    if not a.keep_seed:
        import random
        tpl["seed"] = random.randint(10000, 999999)
    with open(os.path.join(dst_dir, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(tpl, f, ensure_ascii=False, indent=2)
    print("配置已复制（seed=%s）" % tpl.get("seed"))

    if not a.with_data:
        print("只复制了配置，数据目录留空。")
        return 0

    src_data = os.path.join(instance_dir(src), "Data")
    dst_data = os.path.join(dst_dir, "Data")
    if not os.path.isdir(src_data):
        print("源实例没有数据目录，跳过。")
        return 0
    if os.path.isdir(dst_data):
        bak = os.path.join(BACKUP_ROOT, time.strftime("%Y%m%d-%H%M%S"), a.to, "Data")
        os.makedirs(os.path.dirname(bak), exist_ok=True)
        try:
            shutil.copytree(dst_data, bak, dirs_exist_ok=True)
            print("[备份] 目标原有数据 -> %s" % bak)
        except Exception:
            pass
        shutil.rmtree(dst_data, ignore_errors=True)
    print("复制数据目录（跳过缓存）…")
    copy_skip_cache(src_data, dst_data)
    print("数据已复制: %s（复用 %d / 新复制 %d 个文件）"
          % (human(dir_size(dst_data)),
             LINK_STATS.get("link", 0), LINK_STATS.get("copy", 0)))
    return 0


def cmd_delete(a):
    sd = snap_dir(a.name)
    if not os.path.isdir(sd):
        sys.exit("快照不存在: %s" % a.name)
    shutil.rmtree(sd)
    print("已删除快照: %s" % a.name)


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="实例快照")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="从实例创建快照")
    c.add_argument("--from", dest="source", required=True)
    c.add_argument("--name", required=True)
    c.add_argument("--mode", choices=["lean", "full"], default="lean")
    c.add_argument("--force", action="store_true")
    c.add_argument("--base", default="", help="硬链接基准快照名（默认自动选同源的最近一个）")
    c.add_argument("--no-incremental", action="store_true", help="完全独立复制，不做硬链接")

    sub.add_parser("list", help="列出快照")

    i = sub.add_parser("info", help="查看快照内容")
    i.add_argument("--name", required=True)

    r = sub.add_parser("restore", help="把快照还原到实例")
    r.add_argument("--name", required=True)
    r.add_argument("--to", required=True)
    r.add_argument("--new-seed", action="store_true", help="还原时换一个新 seed")
    r.add_argument("--force", action="store_true")

    cl = sub.add_parser("clone", help="克隆实例")
    cl.add_argument("--from", dest="source", required=True)
    cl.add_argument("--to", required=True)
    cl.add_argument("--with-data", action="store_true")
    cl.add_argument("--keep-seed", action="store_true")
    cl.add_argument("--force", action="store_true")

    d = sub.add_parser("delete", help="删除快照")
    d.add_argument("--name", required=True)

    a = ap.parse_args()
    {"create": cmd_create, "list": cmd_list, "info": cmd_info,
     "restore": cmd_restore, "delete": cmd_delete,
     "clone": cmd_clone}[a.cmd](a)


if __name__ == "__main__":
    main()
