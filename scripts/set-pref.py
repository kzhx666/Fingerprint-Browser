# -*- coding: utf-8 -*-
"""往实例的 Preferences 里写一个 pref（点号路径），保留其它设置。

用法:
  python set-pref.py --profile "D:\\KZProfiles\\主控1\\Data\\Default" ^
                     --set webrtc.ip_handling_policy=disable_non_proxied_udp
  python set-pref.py --profile <dir> --get webrtc.ip_handling_policy
  python set-pref.py --profile <dir> --unset webrtc.ip_handling_policy

必须在该实例没运行时调用 —— Chrome 退出时会把内存里的 Preferences 写回磁盘，
运行中改会被覆盖。
"""
import argparse
import json
import os
import sys


def load(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def split_key(key):
    return [p for p in key.split(".") if p]


def set_pref(obj, key, value):
    parts = split_key(key)
    cur = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def get_pref(obj, key):
    cur = obj
    for p in split_key(key):
        if not isinstance(cur, dict) or p not in cur:
            return None
        cur = cur[p]
    return cur


def unset_pref(obj, key):
    parts = split_key(key)
    cur = obj
    for p in parts[:-1]:
        cur = cur.get(p)
        if not isinstance(cur, dict):
            return False
    return cur.pop(parts[-1], None) is not None


def coerce(text):
    low = text.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        pass
    return text


def main():
    ap = argparse.ArgumentParser(description="设置实例 Preferences 里的单个 pref")
    ap.add_argument("--profile", required=True, help="Data\\Default 目录")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    g.add_argument("--get", default="")
    g.add_argument("--unset", default="")
    ap.add_argument("--file", default="Preferences", help="目标文件名，默认 Preferences")
    a = ap.parse_args()

    path = os.path.join(a.profile, a.file)
    if not os.path.isdir(a.profile):
        sys.exit("目录不存在: %s" % a.profile)

    obj = load(path)

    if a.get:
        v = get_pref(obj, a.get)
        print("" if v is None else json.dumps(v, ensure_ascii=False))
        return

    if a.unset:
        if unset_pref(obj, a.unset):
            save(path, obj)
            print("已删除 %s" % a.unset)
        else:
            print("不存在 %s" % a.unset)
        return

    changed = 0
    for item in a.set:
        if "=" not in item:
            sys.exit("格式应为 KEY=VALUE: %s" % item)
        k, v = item.split("=", 1)
        set_pref(obj, k.strip(), coerce(v))
        changed += 1
    save(path, obj)
    print("已写入 %d 项 -> %s" % (changed, path))


if __name__ == "__main__":
    main()
