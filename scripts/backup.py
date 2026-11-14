# -*- coding: utf-8 -*-
"""实例数据备份 / 还原 / 校验。

用法:
  python backup.py save --from 主控1 [--tag auto]
  python backup.py list [--instance 主控1]
  python backup.py verify --instance 主控1
  python backup.py restore --from 20261109-165000 --to 主控1 --what bookmarks
  python backup.py prune --keep 20

为什么要单独做：书签在 Chromium 158 里以 EncryptedBookmarks2 为准，明文 Bookmarks
只是副本。删掉加密副本前如果不确认明文有内容，浏览器会把"空明文"重新加密成
"空书签"——书签就没了。这里把顺序和校验都固化下来。

备份内容（Data/Default 下）：
  Bookmarks / Bookmarks.bak / EncryptedBookmarks2 / EncryptedBookmarks2.bak
  Login Data / Web Data / History / Preferences / Secure Preferences
"""
import argparse
import json
import os
import shutil
import sys
import time

ROOT = os.environ.get("KZ_ROOT") or os.path.dirname(os.path.abspath(__file__))
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


# 书签相关（加密副本必须一起备，否则还原不完整）
BM_FILES = ["Bookmarks", "Bookmarks.bak",
            "EncryptedBookmarks2", "EncryptedBookmarks2.bak"]
# 其他数据
DATA_FILES = ["Login Data", "Web Data", "History",
              "Preferences", "Secure Preferences"]


def instance_dir(name):
    validate_name(name, "实例名")
    p = os.path.join(ROOT, name)
    if not is_under(ROOT, p):
        sys.exit("实例目录越界: %s" % p)
    return p


def profile_dir(name):
    return os.path.join(instance_dir(name), "Data", "Default")


def list_instances():
    out = []
    if not os.path.isdir(ROOT):
        return out
    for n in sorted(os.listdir(ROOT)):
        if os.path.isdir(os.path.join(ROOT, n)) and \
                os.path.exists(os.path.join(ROOT, n, "profile.json")):
            out.append(n)
    return out


def count_bookmarks(path):
    """数明文书签里的网址数。-1 表示文件不存在或解析失败。"""
    if not os.path.exists(path):
        return -1
    try:
        with open(path, encoding="utf-8-sig") as f:
            d = json.load(f)
    except Exception:
        return -1

    def walk(n):
        c = 1 if n.get("type") == "url" else 0
        for x in n.get("children", []):
            c += walk(x)
        return c

    return sum(walk(r) for r in (d.get("roots") or {}).values()
               if isinstance(r, dict))


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


# ------------------------------------------------------------------ save

def cmd_save(a):
    src = a.source
    if src not in list_instances():
        sys.exit("实例不存在: %s（现有：%s）" % (src, ", ".join(list_instances()) or "无"))
    if is_running(src) and not a.force:
        sys.exit("实例「%s」正在运行，先关掉（或加 --force）" % src)

    prof = profile_dir(src)
    if not os.path.isdir(prof):
        sys.exit("数据目录不存在: %s" % prof)

    stamp = time.strftime("%Y%m%d-%H%M%S") + ("-%03d" % int((time.time() % 1) * 1000))
    dest = os.path.join(BACKUP_ROOT, "%s-%s" % (a.tag, stamp), src)
    os.makedirs(dest, exist_ok=True)

    saved = []
    for name in BM_FILES + DATA_FILES:
        s = os.path.join(prof, name)
        if os.path.exists(s):
            try:
                shutil.copy2(s, os.path.join(dest, name))
                saved.append(name)
            except Exception as e:  # noqa: BLE001
                print("  [跳过] %s: %s" % (name, e))

    n = count_bookmarks(os.path.join(prof, "Bookmarks"))
    meta = {
        "instance": src,
        "stamp": stamp,
        "tag": a.tag,
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "bookmarks": n,
        "files": saved,
    }
    with open(os.path.join(dest, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print("已备份 %s -> %s" % (src, dest))
    print("  书签 %d 条，文件 %d 个" % (n, len(saved)))
    return 0


# ------------------------------------------------------------------ list

def cmd_list(a):
    if not os.path.isdir(BACKUP_ROOT):
        print("还没有备份。")
        return 0
    rows = []
    for d in sorted(os.listdir(BACKUP_ROOT), reverse=True):
        p = os.path.join(BACKUP_ROOT, d)
        if not os.path.isdir(p):
            continue
        for inst in sorted(os.listdir(p)):
            m = os.path.join(p, inst, "meta.json")
            if not os.path.exists(m):
                continue
            try:
                with open(m, encoding="utf-8-sig") as f:
                    meta = json.load(f)
            except Exception:
                continue
            if a.instance and meta.get("instance") != a.instance:
                continue
            rows.append(meta)
    if not rows:
        print("没有匹配的备份。")
        return 0
    print("%-22s %-14s %-8s %s" % ("时间", "实例", "书签", "标签"))
    print("-" * 62)
    for m in rows:
        print("%-22s %-14s %-8s %s" % (m.get("stamp", ""), m.get("instance", ""),
                                       m.get("bookmarks", "?"), m.get("tag", "")))
    return 0


# ------------------------------------------------------------------ verify

def cmd_verify(a):
    """当前书签数对比最近一次备份，掉太多就报警。"""
    inst = a.instance
    if inst not in list_instances():
        sys.exit("实例不存在: %s" % inst)
    cur = count_bookmarks(os.path.join(profile_dir(inst), "Bookmarks"))

    latest = None
    if os.path.isdir(BACKUP_ROOT):
        for d in sorted(os.listdir(BACKUP_ROOT), reverse=True):
            m = os.path.join(BACKUP_ROOT, d, inst, "meta.json")
            if os.path.exists(m):
                try:
                    with open(m, encoding="utf-8-sig") as f:
                        latest = json.load(f)
                except Exception:
                    pass
                break

    print("实例 %s" % inst)
    print("  当前书签: %d 条" % cur)
    if not latest:
        print("  没有历史备份可对比")
        return 0

    old = latest.get("bookmarks", -1)
    print("  最近备份: %d 条（%s）" % (old, latest.get("created", "")))
    if old <= 0:
        return 0
    if cur < 0:
        print("  [警告] 明文 Bookmarks 读不出来")
        return 2
    if cur == 0:
        print("  [危险] 书签被清空了！用 restore 还原")
        return 2
    drop = old - cur
    if drop > 0 and (drop / float(old)) > a.threshold:
        print("  [警告] 少了 %d 条（%.0f%%），超过阈值 %.0f%%"
              % (drop, 100.0 * drop / old, a.threshold * 100))
        return 2
    print("  正常（差异 %+d 条）" % (-drop))
    return 0


# ------------------------------------------------------------------ restore

def cmd_restore(a):
    src_dir = os.path.join(BACKUP_ROOT, a.source, a.to)
    if not os.path.isdir(src_dir):
        # 允许只给时间戳，自动找实例
        cand = os.path.join(BACKUP_ROOT, a.source)
        if os.path.isdir(cand):
            subs = [x for x in os.listdir(cand)
                    if os.path.isdir(os.path.join(cand, x))]
            if len(subs) == 1:
                a.to = subs[0]
                src_dir = os.path.join(cand, subs[0])
    if not os.path.isdir(src_dir):
        sys.exit("备份不存在: %s" % src_dir)
    inst = a.to
    if inst not in list_instances():
        sys.exit("目标实例不存在: %s" % inst)
    if is_running(inst) and not a.force:
        sys.exit("实例「%s」正在运行，先关掉（或加 --force）" % inst)

    prof = profile_dir(inst)
    want = {w.strip() for w in a.what.split(",") if w.strip()}

    # 先给当前状态留一份
    stamp = time.strftime("%Y%m%d-%H%M%S") + ("-%03d" % int((time.time() % 1) * 1000))
    safe = os.path.join(BACKUP_ROOT, "before-restore-%s" % stamp, inst)
    os.makedirs(safe, exist_ok=True)
    for name in BM_FILES + DATA_FILES:
        s = os.path.join(prof, name)
        if os.path.exists(s):
            try:
                shutil.copy2(s, os.path.join(safe, name))
            except Exception:
                pass
    print("[保险] 当前状态已备份 -> %s" % safe)

    restored = []
    if "bookmarks" in want:
        # 先校验备份，确认有内容再动磁盘上的任何东西。
        # 之前是先写后检查，结果把空文件覆盖上去，书签又被清空了。
        src_plain = os.path.join(src_dir, "Bookmarks")
        n = count_bookmarks(src_plain)
        if n <= 0:
            print("[中止] 备份里的明文 Bookmarks 是空的（%d 条），什么都没改" % n)
            return 1
        for name in ("Bookmarks", "Bookmarks.bak"):
            s = os.path.join(src_dir, name)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(prof, name))
                restored.append(name)
        # 写完再确认一次，防止复制过程中出问题
        n = count_bookmarks(os.path.join(prof, "Bookmarks"))
        if n <= 0:
            print("[中止] 写入后明文变成空的，不动加密副本")
            return 1
        dropped = []
        for name in ("EncryptedBookmarks2", "EncryptedBookmarks2.bak"):
            p = os.path.join(prof, name)
            if os.path.exists(p):
                try:
                    os.remove(p)
                    dropped.append(name)
                except OSError:
                    pass
        print("[书签] 明文 %d 条已写入，删除加密副本 %s 让浏览器重建"
              % (n, ", ".join(dropped) or "无"))

    if "passwords" in want or "history" in want or "prefs" in want:
        names = []
        if "passwords" in want:
            names += ["Login Data", "Web Data"]
        if "history" in want:
            names += ["History"]
        if "prefs" in want:
            names += ["Preferences", "Secure Preferences"]
        for name in names:
            s = os.path.join(src_dir, name)
            if os.path.exists(s):
                shutil.copy2(s, os.path.join(prof, name))
                restored.append(name)

    print("已还原 -> %s" % inst)
    print("  文件: %s" % (", ".join(restored) or "无"))
    return 0


# ------------------------------------------------------------------ prune

def cmd_prune(a):
    if not os.path.isdir(BACKUP_ROOT):
        print("没有备份目录。")
        return 0
    dirs = sorted(d for d in os.listdir(BACKUP_ROOT)
                  if os.path.isdir(os.path.join(BACKUP_ROOT, d)))
    # before-restore-* 永远保留
    keep_dirs = [d for d in dirs if d.startswith("before-restore-")]
    auto = [d for d in dirs if d.startswith("auto-")]
    manual = [d for d in dirs if d not in keep_dirs and d not in auto]

    drop = auto[:-a.keep] if len(auto) > a.keep else []
    for d in drop:
        shutil.rmtree(os.path.join(BACKUP_ROOT, d), ignore_errors=True)
    print("保留 %d 个自动备份，删除 %d 个；手动备份 %d 个未动；"
          "before-restore %d 个未动"
          % (len(auto) - len(drop), len(drop), len(manual), len(keep_dirs)))
    return 0


# ------------------------------------------------------------------ main

def main():
    ap = argparse.ArgumentParser(description="实例数据备份 / 还原 / 校验")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("save", help="备份一个实例")
    s.add_argument("--from", dest="source", required=True)
    s.add_argument("--tag", default="auto", help="标签，默认 auto")
    s.add_argument("--force", action="store_true")

    l = sub.add_parser("list", help="列出备份")
    l.add_argument("--instance", default="")

    v = sub.add_parser("verify", help="校验书签数量是否异常")
    v.add_argument("--instance", required=True)
    v.add_argument("--threshold", type=float, default=0.2,
                   help="允许的减少比例，默认 0.2")

    r = sub.add_parser("restore", help="从备份还原")
    r.add_argument("--from", dest="source", required=True, help="备份时间戳")
    r.add_argument("--to", required=True, help="目标实例")
    r.add_argument("--what", default="bookmarks",
                   help="bookmarks,passwords,history,prefs，逗号分隔")
    r.add_argument("--force", action="store_true")

    p = sub.add_parser("prune", help="清理旧的自动备份")
    p.add_argument("--keep", type=int, default=20)

    a = ap.parse_args()
    return {"save": cmd_save, "list": cmd_list, "verify": cmd_verify,
            "restore": cmd_restore, "prune": cmd_prune}[a.cmd](a) or 0


if __name__ == "__main__":
    sys.exit(main())
