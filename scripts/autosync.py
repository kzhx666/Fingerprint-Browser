# -*- coding: utf-8 -*-
"""自动双向同步：把多个 profile（我们的实例 + 官方 Chrome/猫眼）互相并集合并。

用法:
  python autosync.py --status              # 看各成员状态
  python autosync.py --once                # 立即同步一轮
  python autosync.py --once --allow-running  # 浏览器开着也强行同步（不推荐）

配置: D:\\KZProfiles\\autosync.json
{
  "members": [
    {"name": "主控1", "type": "instance"},
    {"name": "chrome", "type": "path", "path": "C:\\\\...\\\\User Data\\\\Default"}
  ],
  "types": ["bookmarks", "history", "passwords"],
  "log": "D:\\\\KZProfiles\\\\autosync.log"
}

策略:
  * 并集合并（只增不删），所以反复跑是幂等的，也不会因为一边删了条目而丢掉另一边
  * 配置里给了 "bookmarks_source" 时：书签以该成员为准整体替换（保留原始层级），
    不再做并集——适合"某个浏览器是我的书签真身"的场景
  * 任何正在运行的浏览器都会被跳过（SQLite 被锁 + Chrome 退出时会把内存里的书签写回文件）
  * 每次写入前都备份到 _backup/autosync-<时间戳>/
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "autosync.json")

spec = importlib.util.spec_from_file_location("kz", os.path.join(HERE, "sync-data.py"))
kz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kz)


def load_config(path):
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def member_profile(m):
    if m.get("type") == "instance":
        return kz.profile_dir(m["name"])
    return m.get("path", "")


def member_running(m):
    """判断这个成员的 profile 是否正被某个浏览器占用。

    外部 profile 有两种命中方式：
      1) 命令行里出现了它的 user-data-dir（父目录）
      2) 命令行里出现了 --profile-directory="<profile 名>"
    """
    if m.get("type") == "instance":
        return kz.is_running(m["name"])

    prof = (m.get("path") or "").replace("/", "\\").rstrip("\\")
    if not prof:
        return False
    parts = prof.split("\\")
    pname = parts[-1].lower()
    udata = "\\".join(parts[:-1]).lower()

    try:
        r = subprocess.run(
            ["wmic", "process", "where",
             "name='chrome.exe' or name='catsxp.exe' or name='msedge.exe'",
             "get", "CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=20,
        )
        for line in r.stdout.splitlines():
            if "CommandLine=" not in line:
                continue
            low = line.lower()
            if udata and udata in low:
                return True
            if pname != "default" and ('--profile-directory="%s"' % pname) in low:
                return True
    except Exception:
        pass
    return False


def log_line(cfg, msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line)
    p = cfg.get("log")
    if p:
        try:
            with open(p, "a", encoding="utf-8-sig") as f:
                f.write(line + "\n")
        except Exception:
            pass


def merge_into(src_prof, dst_prof, types, tag):
    """把 src 的指定类型并集合并到 dst，返回摘要文本"""
    out = []
    if "bookmarks" in types:
        sp = os.path.join(src_prof, "Bookmarks")
        dp = os.path.join(dst_prof, "Bookmarks")
        if os.path.exists(sp):
            n = kz.merge_bookmarks(sp, dp)
            if n:
                out.append("书签+%d" % n)
    if "history" in types:
        sp = os.path.join(src_prof, "History")
        dp = os.path.join(dst_prof, "History")
        if os.path.exists(sp) and os.path.exists(dp):
            nu, nv = kz.merge_history(sp, dp)
            if nu or nv:
                out.append("历史+%d/%d" % (nu, nv))
        elif os.path.exists(sp):
            import shutil
            shutil.copy2(sp, dp)
            out.append("历史(整库)")
    if "passwords" in types:
        sp = os.path.join(src_prof, "Login Data")
        dp = os.path.join(dst_prof, "Login Data")
        if os.path.exists(sp) and os.path.exists(dp):
            na, _ = kz.merge_passwords(sp, dp)
            if na:
                out.append("密码+%d" % na)
        elif os.path.exists(sp):
            import shutil
            shutil.copy2(sp, dp)
            out.append("密码(整库)")
    return ", ".join(out) if out else "无变化"


def backup_member(cfg, name, prof, types):
    import shutil
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(kz.BACKUP_ROOT, "autosync-" + stamp, name)
    os.makedirs(dst, exist_ok=True)
    files = []
    if "bookmarks" in types:
        files.append("Bookmarks")
    if "history" in types:
        files += ["History", "History-wal", "History-shm"]
    if "passwords" in types:
        files += ["Login Data", "Login Data-wal", "Login Data-shm"]
    for f in files:
        sp = os.path.join(prof, f)
        if os.path.exists(sp):
            shutil.copy2(sp, os.path.join(dst, f))
    return dst


def cmd_status(cfg):
    print("成员状态：")
    for m in cfg["members"]:
        prof = member_profile(m)
        exists = os.path.isdir(prof)
        running = member_running(m)
        marks = []
        for label, f in (("书签", "Bookmarks"), ("历史", "History"), ("密码", "Login Data")):
            if exists and os.path.exists(os.path.join(prof, f)):
                marks.append(label)
        print("  %-10s %-8s %s  %s" % (
            m["name"],
            "运行中" if running else "已关闭",
            "/".join(marks) if marks else "无数据",
            "" if exists else "(目录不存在)"))


def cmd_once(cfg, allow_running=False):
    """策略：运行的浏览器只读不写。

    * 读：任何存在的 profile 都能读（SQLite 带 WAL 快照，JSON 直接读）
    * 写：只写没在运行的成员，否则 Chrome 退出时会把内存里的书签写回去，
          我们的改动就被覆盖了
    """
    types = list(cfg.get("types") or ["bookmarks", "history", "passwords"])
    members = cfg["members"]
    bookmarks_source = cfg.get("bookmarks_source")
    merge_types = [x for x in types if not (bookmarks_source and x == "bookmarks")]

    readable = []
    writable = []
    for m in members:
        prof = member_profile(m)
        if not os.path.isdir(prof):
            log_line(cfg, "跳过 %s：目录不存在" % m["name"])
            continue
        running = member_running(m) and not allow_running
        readable.append((m, prof))
        if running:
            log_line(cfg, "  %s 正在运行 → 只读不写" % m["name"])
        else:
            writable.append((m, prof))

    if not writable:
        log_line(cfg, "没有可写入的成员（全都在运行），本轮跳过")
        return 0
    if len(readable) < 2:
        log_line(cfg, "可读成员不足 2 个，本轮跳过")
        return 0

    hub_m, hub_p = writable[0]
    log_line(cfg, "本轮汇聚到 %s；类型=%s" % (hub_m["name"], ",".join(types)))
    log_line(cfg, "  备份 %s -> %s" % (hub_m["name"], backup_member(cfg, hub_m["name"], hub_p, types)))

    total = 0
    # 1) 所有可读成员 -> 汇聚点
    for m, prof in readable:
        if prof == hub_p:
            continue
        summary = merge_into(prof, hub_p, merge_types, m["name"])
        if summary != "无变化":
            log_line(cfg, "  %s -> %s: %s" % (m["name"], hub_m["name"], summary))
            total += 1

    # 2) 汇聚点 -> 其它可写成员
    for m, prof in writable:
        if prof == hub_p:
            continue
        log_line(cfg, "  备份 %s -> %s" % (m["name"], backup_member(cfg, m["name"], prof, types)))
        summary = merge_into(hub_p, prof, merge_types, m["name"])
        if summary != "无变化":
            log_line(cfg, "  %s -> %s: %s" % (hub_m["name"], m["name"], summary))
            total += 1

    # 书签：以权威来源为准整体替换（保留文件夹层级，不做并集）
    if bookmarks_source:
        src = next((p for m, p in readable if m["name"] == bookmarks_source), None)
        if not src:
            log_line(cfg, "书签权威来源 %s 不可用，跳过书签" % bookmarks_source)
        else:
            sp = os.path.join(src, "Bookmarks")
            if os.path.exists(sp):
                import filecmp
                import shutil
                for m, prof in writable:
                    dp = os.path.join(prof, "Bookmarks")
                    if os.path.abspath(prof) == os.path.abspath(src):
                        continue
                    if os.path.exists(dp) and filecmp.cmp(sp, dp, shallow=False):
                        continue
                    log_line(cfg, "  备份 %s -> %s" % (m["name"], backup_member(cfg, m["name"], prof, ["bookmarks"])))
                    shutil.copy2(sp, dp)
                    removed = kz.drop_encrypted_bookmarks(prof)
                    log_line(cfg, "  书签以 %s 为准替换 -> %s%s" % (
                        bookmarks_source, m["name"],
                        ("（清掉 %s）" % ",".join(removed)) if removed else ""))
                    total += 1

    log_line(cfg, "本轮完成，%d 处变化" % total)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=CONFIG)
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--allow-running", action="store_true")
    a = ap.parse_args()

    cfg = load_config(a.config)
    if not cfg:
        sys.exit("找不到配置: %s" % a.config)
    if a.status:
        cmd_status(cfg)
        return 0
    if a.once:
        return cmd_once(cfg, a.allow_running)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
