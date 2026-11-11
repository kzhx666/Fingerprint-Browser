# -*- coding: utf-8 -*-
r"""多开实例数据同步：书签 / 历史 / 密码 / 插件

用法:
  python sync-data.py --list
  python sync-data.py --from A --to B,C --what all
  python sync-data.py --from A --to B --what bookmarks,history --dry-run

设计要点:
  * 书签   -> JSON 递归并集（跨机器可用）
  * 历史   -> SQLite urls/visits 按 URL + visit_time 去重合并（跨机器可用）
  * 密码   -> SQLite logins 按 origin+username 合并，密文原样搬（同一 Windows 用户下可解，跨机器不行）
  * 插件   -> 复制 Extensions/<id> 目录 + 合并 Preferences 里的 extensions.settings

安全: 改动前会把目标实例的相关文件备份到 C:\KZProfiles\_backup\<时间戳>\<实例>\
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

def _detect_root():
    r"""自动挑一个真正有实例的根目录（用户可能把实例放在 D:\KZProfiles）"""
    cands = [os.environ.get("KZ_ROOT"), r"D:\KZProfiles", r"C:\KZProfiles"]
    for c in cands:
        if not c or not os.path.isdir(c):
            continue
        try:
            names = os.listdir(c)
        except OSError:
            continue
        for n in names:
            if os.path.isdir(os.path.join(c, n, "Data")):
                return c
    return r"C:\KZProfiles"


ROOT = _detect_root()
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



# ------------------------------------------------------------------ helpers
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
        p = os.path.join(ROOT, n)
        if os.path.isdir(p) and os.path.isdir(os.path.join(p, "Data")):
            out.append(n)
    return out


def is_running(name):
    """该实例的浏览器是否在跑（按命令行里的 user-data-dir 判断）"""
    data = os.path.join(instance_dir(name), "Data").lower()
    try:
        r = subprocess.run(
            ["wmic", "process", "where", "name='chrome.exe'", "get", "CommandLine", "/format:list"],
            capture_output=True, text=True, timeout=20,
        )
        for line in r.stdout.splitlines():
            if "CommandLine=" in line and data in line.lower():
                return True
    except Exception:
        pass
    return False


def backup(name, files):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUP_ROOT, stamp, name)
    os.makedirs(dst, exist_ok=True)
    for f in files:
        src = os.path.join(profile_dir(name), f)
        if os.path.exists(src):
            target = os.path.join(dst, f)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if os.path.isdir(src):
                shutil.copytree(src, target, dirs_exist_ok=True)
            else:
                shutil.copy2(src, target)
    return dst


def copy_db_with_wal(src, tmpdir):
    """把 SQLite 主库连同 -wal 一起复制到临时目录，保证读到一致快照"""
    base = os.path.basename(src)
    dst = os.path.join(tmpdir, base)
    shutil.copy2(src, dst)
    for suffix in ("-wal", "-shm"):
        if os.path.exists(src + suffix):
            shutil.copy2(src + suffix, dst + suffix)
    return dst


# Chromium 158+ 把书签另存一份加密副本 EncryptedBookmarks2，且优先从它加载。
# 只改明文 Bookmarks 不会生效：启动时旧数据会从加密副本恢复并覆盖回来。
ENCRYPTED_BOOKMARK_FILES = (
    "EncryptedBookmarks2",
    "EncryptedBookmarks2.bak",
)


def _count_bookmark_urls(path):
    """数一下明文 Bookmarks 里到底有多少条网址。"""
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

    return sum(walk(r) for r in (d.get("roots") or {}).values() if isinstance(r, dict))


def drop_encrypted_bookmarks(profile_dir):
    """删掉加密书签副本，让浏览器下次从明文 Bookmarks 重新生成。

    Chromium 158 以 EncryptedBookmarks2 为准，明文只是副本。删之前必须确认明文
    真的有书签 —— 否则浏览器会把"空明文"重新加密成"空书签"，等于把用户的书签
    抹掉。这是之前丢书签的原因。
    """
    plain = os.path.join(profile_dir, "Bookmarks")
    n = _count_bookmark_urls(plain)
    if n <= 0:
        return []  # 明文没内容（或读不了），绝不动加密副本

    removed = []
    for name in ENCRYPTED_BOOKMARK_FILES:
        p = os.path.join(profile_dir, name)
        if os.path.exists(p):
            # 先留个备份，万一判断失误还能捞回来
            try:
                bak = p + ".before-drop"
                if not os.path.exists(bak):
                    shutil.copy2(p, bak)
            except Exception:
                pass
            try:
                os.remove(p)
                removed.append(name)
            except OSError:
                pass
    return removed


# ------------------------------------------------------------------ bookmarks
def merge_bookmarks(src_path, dst_path):
    with open(src_path, encoding="utf-8") as f:
        src = json.load(f)
    if os.path.exists(dst_path):
        with open(dst_path, encoding="utf-8") as f:
            dst = json.load(f)
    else:
        dst = {"roots": {}, "version": src.get("version", 1)}

    added = 0

    def key_of(node):
        if node.get("type") == "url":
            return ("url", node.get("url", ""))
        return ("folder", node.get("name", ""))

    def merge_into(dst_node, src_node):
        nonlocal added
        dst_children = dst_node.setdefault("children", [])
        src_children = src_node.get("children") or []
        index = {}
        for c in dst_children:
            index.setdefault(key_of(c), []).append(c)
        for sc in src_children:
            k = key_of(sc)
            if k in index and index[k]:
                # 已有：url 跳过；folder 递归合并
                if sc.get("type") == "folder":
                    merge_into(index[k][0], sc)
                continue
            clone = json.loads(json.dumps(sc))
            dst_children.append(clone)
            index.setdefault(k, []).append(clone)
            added += 1
            if sc.get("type") == "folder":
                pass

    src_roots = src.get("roots", {})
    dst_roots = dst.setdefault("roots", {})
    for root_name, src_root in src_roots.items():
        if not isinstance(src_root, dict):
            continue
        dst_root = dst_roots.get(root_name)
        if not isinstance(dst_root, dict):
            dst_roots[root_name] = json.loads(json.dumps(src_root))
            added += count_nodes(src_root)
            continue
        if src_root.get("type") == "folder":
            merge_into(dst_root, src_root)

    # 没有变化就不写：避免反复改文件时间戳/校验和，让浏览器以为书签被动过
    if added or not os.path.exists(dst_path):
        with open(dst_path, "w", encoding="utf-8") as f:
            json.dump(dst, f, ensure_ascii=False, indent=3)
    return added


def count_nodes(node):
    n = 1
    for c in node.get("children") or []:
        n += count_nodes(c)
    return n


# ------------------------------------------------------------------ history
def merge_history(src_path, dst_path):
    tmp = tempfile.mkdtemp(prefix="kzh_")
    try:
        snap = copy_db_with_wal(src_path, tmp)
        con = sqlite3.connect(dst_path)
        con.execute("ATTACH DATABASE ? AS s", (snap,))
        cur = con.cursor()

        new_urls = 0
        new_visits = 0
        cur.execute("SELECT id, url, title, visit_count, typed_count, last_visit_time, hidden FROM s.urls")
        src_urls = cur.fetchall()
        for sid, url, title, vc, tc, lvt, hidden in src_urls:
            cur.execute("SELECT id, visit_count, last_visit_time FROM urls WHERE url = ?", (url,))
            row = cur.fetchone()
            if row:
                did, dvc, dlvt = row
                if (lvt or 0) > (dlvt or 0) or (vc or 0) > (dvc or 0):
                    cur.execute(
                        "UPDATE urls SET visit_count=?, typed_count=?, last_visit_time=?, title=? WHERE id=?",
                        (max(vc or 0, dvc or 0), tc or 0, max(lvt or 0, dlvt or 0),
                         title or "", did),
                    )
            else:
                cur.execute(
                    "INSERT INTO urls (url, title, visit_count, typed_count, last_visit_time, hidden) "
                    "VALUES (?,?,?,?,?,?)",
                    (url, title or "", vc or 0, tc or 0, lvt or 0, hidden or 0),
                )
                did = cur.lastrowid
                new_urls += 1

            cur.execute(
                "SELECT visit_time, transition, visit_duration FROM s.visits WHERE url = ?", (sid,)
            )
            for vt, tr, vd in cur.fetchall():
                cur.execute("SELECT 1 FROM visits WHERE url=? AND visit_time=?", (did, vt))
                if cur.fetchone():
                    continue
                cur.execute(
                    "INSERT INTO visits (url, visit_time, from_visit, transition, segment_id, visit_duration, "
                    "incremented_omnibox_typed_score, opener_visit, originator_cache_guid, "
                    "originator_visit_id, originator_from_visit, originator_opener_visit, is_known_to_sync, "
                    "consider_for_ntp_most_visited, external_referrer_url, visited_link_id) "
                    "VALUES (?,?,0,?,0,?,0,0,'',0,0,0,0,0,'',0)",
                    (did, vt, tr or 0, vd or 0),
                )
                new_visits += 1

        con.commit()
        con.execute("DETACH DATABASE s")
        con.close()
        return new_urls, new_visits
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ passwords
def merge_passwords(src_path, dst_path):
    tmp = tempfile.mkdtemp(prefix="kzp_")
    try:
        snap = copy_db_with_wal(src_path, tmp)
        con = sqlite3.connect(dst_path)
        con.execute("ATTACH DATABASE ? AS s", (snap,))
        cur = con.cursor()

        cols = [r[1] for r in cur.execute("PRAGMA table_info(logins)")]
        src_cols = [r[1] for r in cur.execute("PRAGMA s.table_info(logins)")]
        shared = [c for c in cols if c in src_cols and c != "id"]

        added = 0
        skipped = 0
        cur.execute("SELECT %s FROM s.logins" % ",".join(shared))
        for row in cur.fetchall():
            rec = dict(zip(shared, row))
            cur.execute(
                "SELECT 1 FROM logins WHERE origin_url=? AND username_value=? AND signon_realm=?",
                (rec.get("origin_url"), rec.get("username_value"), rec.get("signon_realm")),
            )
            if cur.fetchone():
                skipped += 1
                continue
            cur.execute(
                "INSERT INTO logins (%s) VALUES (%s)"
                % (",".join(shared), ",".join("?" * len(shared))),
                row,
            )
            added += 1

        con.commit()
        con.execute("DETACH DATABASE s")
        con.close()
        return added, skipped
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ extensions
def merge_extensions(src_profile, dst_profile):
    """整份复制 Secure Preferences + Extensions/。

    MAC = HMAC-SHA256(seed, device_id + path + value)。本构建非品牌，seed 为空；
    同一台机器 device_id 相同 —— 所以源 profile 的 MAC 在目标 profile 依然有效，
    前提是"设置和它对应的 MAC 一起搬"。合并（只挑 extensions.settings）会破坏这种
    一致性，Chrome 就把这些项当成被篡改并重置，表现为"扩展已损坏"。
    整份复制能保证 protection.macs / super_mac 和内容始终配套。
    """
    copied_dirs = 0
    src_ext = os.path.join(src_profile, "Extensions")
    dst_ext = os.path.join(dst_profile, "Extensions")
    if os.path.isdir(src_ext):
        if os.path.isdir(dst_ext):
            shutil.rmtree(dst_ext)
        shutil.copytree(src_ext, dst_ext)
        copied_dirs = len([d for d in os.listdir(dst_ext)
                           if os.path.isdir(os.path.join(dst_ext, d))])

    replaced = 0
    # Secure Preferences 是扩展设置真正所在；整份替换才能保住 MAC 一致性。
    for name in ("Secure Preferences", "Preferences"):
        s = os.path.join(src_profile, name)
        d = os.path.join(dst_profile, name)
        if not os.path.exists(s):
            continue
        try:
            with open(s, encoding="utf-8") as f:
                probe = json.load(f)
        except Exception:
            continue
        if name == "Preferences" and ((probe.get("extensions") or {})
                                      .get("settings") or {}):
            # 只有 Secure Preferences 缺失时才退而用它
            if os.path.exists(os.path.join(dst_profile, "Secure Preferences")):
                continue
        if name == "Secure Preferences" or not os.path.exists(d):
            shutil.copy2(s, d)
            replaced += 1
    return copied_dirs, replaced

# ---------------------------------------------------------------- external profiles
EXTERNAL_ROOTS = [
    ("猫眼 Catsxp", os.path.join(os.environ.get("LOCALAPPDATA", ""), "CatsxpSoftware",
                                 "Catsxp-Browser", "User Data")),
    ("Chrome", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")),
    ("Edge", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data")),
]


def list_external():
    found = []
    for label, root in EXTERNAL_ROOTS:
        if not os.path.isdir(root):
            continue
        for name in sorted(os.listdir(root)):
            if name != "Default" and not name.startswith("Profile"):
                continue
            prof = os.path.join(root, name)
            if not os.path.isdir(prof):
                continue
            marks = []
            for lbl, f in (("书签", "Bookmarks"), ("历史", "History"),
                           ("密码", "Login Data"), ("插件", "Extensions")):
                if os.path.exists(os.path.join(prof, f)):
                    marks.append(lbl)
            if marks:
                found.append((label, name, prof, marks))
    return found

# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--root", help="实例根目录（默认自动检测 D:\\KZProfiles 或 C:\\KZProfiles）")
    ap.add_argument("--from", dest="src")
    ap.add_argument("--to-path", dest="to_path",
                    help="直接写入一个外部 profile 目录（如 Chrome 的 Default）")
    ap.add_argument("--from-path", dest="from_path",
                    help="直接指定一个 profile 目录（如猫眼/Chrome 的 Default 或 Profile 7）")
    ap.add_argument("--clone-profile", action="store_true",
                    help="整份克隆外部 profile（含 Local State，扩展 MAC 也能对上）")
    ap.add_argument("--list-external", action="store_true",
                    help="列出本机其它浏览器的 profile")
    ap.add_argument("--to", dest="dst")
    ap.add_argument("--what", default="all")
    ap.add_argument("--replace-bookmarks", action="store_true",
                    help="书签以源为准整体替换（保留原始文件夹层级，不做并集）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true", help="浏览器在跑也强行同步（不推荐）")
    a = ap.parse_args()

    if a.list_external:
        rows = list_external()
        if not rows:
            print("没找到其它浏览器的 profile。")
        for label, name, prof, marks in rows:
            print("  %-12s %-12s %s" % (label, name, "/".join(marks)))
            print("      %s" % prof)
        return

    global ROOT, BACKUP_ROOT
    if a.root:
        ROOT = os.path.abspath(a.root)
        BACKUP_ROOT = os.path.join(ROOT, "_backup")
        if not os.path.isdir(ROOT):
            sys.exit("根目录不存在: %s" % ROOT)

    insts = list_instances()
    if a.list or (not a.src and not a.from_path):
        print("可用实例:")
        for n in insts:
            p = profile_dir(n)
            marks = []
            for label, f in (("书签", "Bookmarks"), ("历史", "History"), ("密码", "Login Data")):
                if os.path.exists(os.path.join(p, f)):
                    marks.append(label)
            print("  %-20s %s" % (n, ("有: " + "/".join(marks)) if marks else "（还没启动过）"))
        if not a.src:
            return

    if a.from_path:
        src_prof = os.path.abspath(a.from_path)
        if not os.path.isdir(src_prof):
            sys.exit("源 profile 目录不存在: %s" % src_prof)
        src_name = "%s (%s)" % (os.path.basename(os.path.dirname(src_prof)),
                                os.path.basename(src_prof))
    else:
        if a.src not in insts:
            sys.exit("源实例不存在: %s" % a.src)
        src_prof = profile_dir(a.src)
        src_name = a.src
    targets = [t.strip() for t in (a.dst or "").split(",") if t.strip()]
    external_target = None
    if a.to_path:
        external_target = os.path.abspath(a.to_path)
        if not os.path.isdir(external_target):
            sys.exit("目标 profile 目录不存在: %s" % external_target)
        if not targets:
            targets = ["<external>"]
    for t in targets:
        if t != "<external>" and t not in insts:
            sys.exit("目标实例不存在: %s" % t)

    want = {"bookmarks", "history", "passwords", "extensions"}
    if a.what != "all":
        want = {w.strip() for w in a.what.split(",") if w.strip()}
        bad = want - {"bookmarks", "history", "passwords", "extensions"}
        if bad:
            sys.exit("不认识的类型: %s" % ",".join(bad))

    if a.clone_profile:
        if not a.from_path:
            sys.exit("--clone-profile 需要配 --from-path 指定源 profile 目录")
        src_userdata = os.path.dirname(os.path.abspath(a.from_path))
        for t in targets:
            if not a.force and is_running(t):
                print("[跳过] %s 正在运行" % t)
                continue
            dst_data = os.path.join(instance_dir(t), "Data")
            dst_prof = os.path.join(dst_data, "Default")
            print("[克隆] %s -> %s" % (a.from_path, dst_prof))
            os.makedirs(dst_data, exist_ok=True)
            if os.path.isdir(dst_prof):
                bk = os.path.join(BACKUP_ROOT, time.strftime("%Y%m%d-%H%M%S"), t, "Default")
                os.makedirs(os.path.dirname(bk), exist_ok=True)
                shutil.copytree(dst_prof, bk, dirs_exist_ok=True)
                shutil.rmtree(dst_prof)
            shutil.copytree(a.from_path, dst_prof)
            # Local State 必须一起搬，否则扩展 MAC / 加密种子对不上
            ls = os.path.join(src_userdata, "Local State")
            if os.path.exists(ls):
                shutil.copy2(ls, os.path.join(dst_data, "Local State"))
                print("       已带 Local State")
            print("       完成")
        print("")
        print("提示：插件连同 Secure Preferences 一起复制，MAC 保持有效，无需重新启用。")
        return

    print("源: %s" % src_name)
    print("目标: %s" % (external_target if external_target else ", ".join(targets)))
    print("同步: %s" % ", ".join(sorted(want)))
    print("")

    for t in targets:
        src_running = (not a.from_path) and is_running(a.src)
        tgt_running = False if t == "<external>" else is_running(t)
        if not a.force and (tgt_running or src_running):
            print("[跳过] %s 或 %s 正在运行，请先关掉浏览器（或加 --force）" % (a.src, t))
            continue

        if t == "<external>":
            dst_prof = external_target
        else:
            dst_prof = profile_dir(t)
        if not os.path.isdir(dst_prof):
            print("[跳过] %s 还没有 profile（先启动一次）" % t)
            continue

        files = []
        if "bookmarks" in want:
            files.append("Bookmarks")
        if "history" in want:
            files += ["History", "History-wal", "History-shm"]
        if "passwords" in want:
            files += ["Login Data", "Login Data-wal", "Login Data-shm"]
        if "extensions" in want:
            files += ["Extensions", "Preferences", "Secure Preferences"]

        if a.dry_run:
            print("[试运行] %s 将合并: %s" % (t, ", ".join(files)))
            continue

        if t == "<external>":
            stamp = time.strftime("%Y%m%d-%H%M%S")
            bk = os.path.join(BACKUP_ROOT, "export-" + stamp, "external")
            os.makedirs(bk, exist_ok=True)
            for f in files:
                sp = os.path.join(dst_prof, f)
                if os.path.exists(sp):
                    tp = os.path.join(bk, f)
                    os.makedirs(os.path.dirname(tp), exist_ok=True)
                    if os.path.isdir(sp):
                        shutil.copytree(sp, tp, dirs_exist_ok=True)
                    else:
                        shutil.copy2(sp, tp)
            print("[备份] 外部目标 -> %s" % bk)
        else:
            bk = backup(t, files)
            print("[备份] %s -> %s" % (t, bk))

        if "bookmarks" in want:
            sp = os.path.join(src_prof, "Bookmarks")
            dp = os.path.join(dst_prof, "Bookmarks")
            if os.path.exists(sp):
                if a.replace_bookmarks:
                    shutil.copy2(sp, dp)
                    drop_encrypted_bookmarks(dst_prof)
                    print("  [书签] 已整体替换为源的结构（并清掉加密副本）")
                else:
                    n = merge_bookmarks(sp, dp)
                    if n:
                        drop_encrypted_bookmarks(dst_prof)
                    print("  [书签] 新增 %d 项" % n)

        if "history" in want:
            sp = os.path.join(src_prof, "History")
            dp = os.path.join(dst_prof, "History")
            if os.path.exists(sp) and os.path.exists(dp):
                nu, nv = merge_history(sp, dp)
                print("  [历史] 新增 %d 条网址 / %d 次访问" % (nu, nv))
            elif os.path.exists(sp):
                shutil.copy2(sp, dp)
                print("  [历史] 目标没有历史库，整库复制")

        if "passwords" in want:
            sp = os.path.join(src_prof, "Login Data")
            dp = os.path.join(dst_prof, "Login Data")
            if os.path.exists(sp) and os.path.exists(dp):
                na, ns = merge_passwords(sp, dp)
                print("  [密码] 新增 %d 条 / 已存在 %d 条" % (na, ns))
            elif os.path.exists(sp):
                shutil.copy2(sp, dp)
                print("  [密码] 目标没有密码库，整库复制")

        if "extensions" in want:
            nc, nm = merge_extensions(src_prof, dst_prof)
            print("  [插件] 复制 %d 个扩展目录 / 替换 %d 份设置" % (nc, nm))
            if nc or nm:
                print("         插件设置与 MAC 一并复制，无需重新启用")
        print("")

    print("完成。")


if __name__ == "__main__":
    main()
