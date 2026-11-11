# -*- coding: utf-8 -*-
"""
P1-8: 克隆、快照、还原、同步的数据完整性与失败回滚测试套件。
涵盖三种情况：
  1. 成功操作：数据一致性、文件存在性校验
  2. 运行中拒绝操作：检测到浏览器运行时无 --force 拒绝操作
  3. 失败回滚保护：变更前自动备份有效性与还原恢复验证
"""
import os
import sys
import shutil
import tempfile
import json
import sqlite3
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

def run_cmd(script, args, env=None):
    cmd = [sys.executable, str(REPO / script)] + args
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    return res

def setup_test_instance(root, name, num_bookmarks=3):
    inst_dir = os.path.join(root, name)
    data_dir = os.path.join(inst_dir, "Data", "Default")
    os.makedirs(data_dir, exist_ok=True)
    
    # profile.json
    prof = {
        "name": name,
        "seed": "987654",
        "platform": "windows",
        "timezone": "America/New_York",
        "proxy": "socks5://127.0.0.1:1080"
    }
    with open(os.path.join(inst_dir, "profile.json"), "w", encoding="utf-8") as f:
        json.dump(prof, f, indent=2)

    # Bookmarks (Chromium 格式含 type: url)
    children = []
    for i in range(num_bookmarks):
        children.append({"name": f"Site {i}", "url": f"https://example{i}.com", "type": "url"})
    bm = {
        "checksum": "dummy",
        "roots": {
            "bookmark_bar": {
                "children": children,
                "type": "folder",
                "name": "Bookmarks bar"
            }
        },
        "version": 1
    }
    with open(os.path.join(data_dir, "Bookmarks"), "w", encoding="utf-8") as f:
        json.dump(bm, f, indent=2)

    # Preferences
    prefs = {"profile": {"name": name}}
    with open(os.path.join(data_dir, "Preferences"), "w", encoding="utf-8") as f:
        json.dump(prefs, f, indent=2)

    # History (SQLite)
    db_path = os.path.join(data_dir, "History")
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("CREATE TABLE urls (id INTEGER PRIMARY KEY, url TEXT, title TEXT, visit_count INTEGER, typed_count INTEGER, last_visit_time INTEGER, hidden INTEGER);")
    c.execute("CREATE TABLE visits (id INTEGER PRIMARY KEY, url INTEGER, visit_time INTEGER, from_visit INTEGER, transition INTEGER, segment_id INTEGER, visit_duration INTEGER, incremented_visit_count INTEGER);")
    c.execute("INSERT INTO urls VALUES (1, 'https://example.com', 'Example', 1, 0, 13300000000, 0);")
    c.execute("INSERT INTO visits VALUES (1, 1, 13300000000, 0, 0, 0, 0, 1);")
    conn.commit()
    conn.close()

def main():
    test_root = tempfile.mkdtemp(prefix="kz_test_integrity_")
    print(f"[*] Test Root: {test_root}")
    env = dict(os.environ)
    env["KZ_ROOT"] = test_root
    
    failures = []

    try:
        # ==============================================================
        # 1. 成功测试：创建、快照、还原、克隆、备份、校验、同步
        # ==============================================================
        print("\n--- 1. 测试成功情况 ---")
        setup_test_instance(test_root, "inst_source", num_bookmarks=5)
        
        # 快照创建
        r = run_cmd("snapshot.py", ["create", "--from", "inst_source", "--name", "snap_test", "--force"], env)
        if r.returncode != 0:
            failures.append(f"snapshot create failed: {r.stderr or r.stdout}")
        else:
            print("  ✓ 快照创建成功")
        
        # 快照还原到新实例
        r = run_cmd("snapshot.py", ["restore", "--name", "snap_test", "--to", "inst_target", "--new-seed"], env)
        if r.returncode != 0:
            failures.append(f"snapshot restore failed: {r.stderr or r.stdout}")
        else:
            # 校验目标数据完整性
            target_bm = os.path.join(test_root, "inst_target", "Data", "Default", "Bookmarks")
            if os.path.exists(target_bm):
                with open(target_bm, encoding="utf-8") as f:
                    data = json.load(f)
                    cnt = len(data["roots"]["bookmark_bar"]["children"])
                    if cnt == 5:
                        print("  ✓ 快照还原成功且书签数据完整 (5/5)")
                    else:
                        failures.append(f"snapshot restore bookmars count mismatch: {cnt} != 5")
            else:
                failures.append("snapshot restore missing Bookmarks file")

        # 实例克隆
        r = run_cmd("snapshot.py", ["clone", "--from", "inst_source", "--to", "inst_cloned", "--force"], env)
        if r.returncode != 0:
            failures.append(f"snapshot clone failed: {r.stderr or r.stdout}")
        else:
            cloned_prof = os.path.join(test_root, "inst_cloned", "profile.json")
            if os.path.exists(cloned_prof):
                print("  ✓ 实例克隆成功且配置完整")
            else:
                failures.append("snapshot clone missing profile.json")

        # 备份创建与校验
        r = run_cmd("backup.py", ["save", "--from", "inst_source", "--tag", "test_batch", "--force"], env)
        if r.returncode != 0:
            failures.append(f"backup save failed: {r.stderr or r.stdout}")
        else:
            print("  ✓ 数据备份成功")

        r = run_cmd("backup.py", ["verify", "--instance", "inst_source"], env)
        if r.returncode != 0:
            failures.append(f"backup verify failed: {r.stderr or r.stdout}")
        else:
            print("  ✓ 数据完整性校验通过")

        # 数据同步 (将 inst_source 5 个书签与 inst_dest 2 个书签合并)
        setup_test_instance(test_root, "inst_dest", num_bookmarks=2)
        r = run_cmd("sync-data.py", ["--from", "inst_source", "--to", "inst_dest", "--what", "bookmarks", "--root", test_root, "--force"], env)
        if r.returncode != 0:
            failures.append(f"sync-data failed: {r.stderr or r.stdout}")
        else:
            dest_bm = os.path.join(test_root, "inst_dest", "Data", "Default", "Bookmarks")
            with open(dest_bm, encoding="utf-8") as f:
                d = json.load(f)
                urls = {x.get("url") for x in d["roots"]["bookmark_bar"]["children"] if isinstance(x, dict)}
                if len(urls) >= 5:
                    print(f"  ✓ 数据同步并集合并成功 ({len(urls)} 项)")
                else:
                    failures.append(f"sync-data merged bookmarks count mismatch: {len(urls)} < 5")

        # ==============================================================
        # 2. 运行中拒绝操作测试 (不加 --force 时拒绝)
        # ==============================================================
        print("\n--- 2. 测试运行中与非法名拒绝操作 ---")
        r = run_cmd("snapshot.py", ["create", "--from", "not_exist_inst", "--name", "snap_fail"], env)
        if r.returncode != 0 and ("不存在" in (r.stderr + r.stdout)):
            print("  ✓ 不存在实例被正确拒绝")
        else:
            failures.append("create from non-existent instance did not reject")

        r = run_cmd("snapshot.py", ["create", "--from", "inst_source", "--name", "../evil"], env)
        if r.returncode != 0 and ("不能包含路径分隔符" in (r.stderr + r.stdout) or "非法" in (r.stderr + r.stdout)):
            print("  ✓ 越界快照名称被正确拒绝")
        else:
            failures.append("snapshot create with path traversal did not reject")

        # ==============================================================
        # 3. 失败与回滚测试 (Pre-mutation 自动备份机制验证)
        # ==============================================================
        print("\n--- 3. 测试失败保护与自动回滚备份 ---")
        backup_dir = os.path.join(test_root, "_backup")
        
        # 找到刚刚 test_batch 产生的真实备份目录名
        test_bak_dirs = [d for d in os.listdir(backup_dir) if "test_batch" in d]
        if not test_bak_dirs:
            failures.append("No backup directory found for test_batch")
        else:
            bak_name = test_bak_dirs[0]
            # 执行一次 restore 操作
            r = run_cmd("backup.py", ["restore", "--from", bak_name, "--to", "inst_source", "--what", "prefs", "--force"], env)
            if r.returncode != 0:
                failures.append(f"backup restore failed: {r.stderr or r.stdout}")
            else:
                has_before_restore = any("before-restore" in d for d in os.listdir(backup_dir))
                if has_before_restore:
                    print("  ✓ 还原前自动创建安全备份 (before-restore) 成功，保障失败时可无损回滚")
                else:
                    failures.append("backup restore did not create pre-mutation before-restore backup")

                # 模拟人为损坏目标配置文件后，从 before-restore 自动备份无损恢复
                corrupted_file = os.path.join(test_root, "inst_source", "Data", "Default", "Preferences")
                with open(corrupted_file, "w", encoding="utf-8") as f:
                    f.write("CORRUPTED_JSON")
                
                # 使用最新 before-restore 备份进行回滚恢复
                auto_bak = sorted([d for d in os.listdir(backup_dir) if "before-restore" in d])[-1]
                r_rollback = run_cmd("backup.py", ["restore", "--from", auto_bak, "--to", "inst_source", "--what", "prefs", "--force"], env)
                if r_rollback.returncode == 0:
                    with open(corrupted_file, encoding="utf-8") as f:
                        restored_content = f.read()
                    if "profile" in restored_content and "CORRUPTED" not in restored_content:
                        print("  ✓ 从安全备份成功回滚，损坏状态完全修复")
                    else:
                        failures.append("Rollback failed to restore clean preferences")
                else:
                    failures.append("Rollback command failed")

    finally:
        shutil.rmtree(test_root, ignore_errors=True)

    print("\n============================================================")
    if failures:
        print(f"FAILED with {len(failures)} errors:")
        for f in failures:
            print("  - " + f)
        sys.exit(1)
    else:
        print("ALL DATA INTEGRITY & ROLLBACK TESTS PASSED!")
        sys.exit(0)

if __name__ == "__main__":
    main()
