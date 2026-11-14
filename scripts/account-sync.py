# -*- coding: utf-8 -*-
"""账号同步客户端：登录后按选择的数据类型与服务器双向同步

用法:
  # 注册（一次）
  python account-sync.py --profile "D:\\KZProfiles\\主控1\\Data\\Default" \\
      --server http://127.0.0.1:8787 --user alice --pass secret --register

  # 同步
  python account-sync.py --profile "..." --server http://... --user alice --pass secret \\
      --types bookmarks,history,passwords

说明:
  * 书签：树结构并集（按 URL / 文件夹名去重）
  * 历史：按 (url, visit_time) 去重并集
  * 密码：按 (origin, username, realm) 去重并集；密文用 DPAPI 加密，**只能同机/同 Windows 账号**解密
  * 插件：暂不同步（目录太大、Secure Preferences 的 MAC 无法迁移）
"""

import argparse
import base64
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request

ENCRYPTED_BOOKMARK_FILES = ("EncryptedBookmarks2", "EncryptedBookmarks2.bak")


# ------------------------------------------------------------------ http
def api(server, path, payload=None, token=None, method=None):
    url = server.rstrip("/") + path
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except Exception:
            body = {"error": str(e)}
        raise RuntimeError(body.get("error") or ("HTTP %d" % e.code))



def _size(data):
    if data is None:
        return "无"
    if "urls" in data:
        return "%d 网址" % len(data["urls"])
    if "logins" in data:
        return "%d 条密码" % len(data["logins"])
    if "roots" in data:
        return "书签树"
    return "数据"

# ------------------------------------------------------------------ bookmarks
def export_bookmarks(prof):
    p = os.path.join(prof, "Bookmarks")
    if not os.path.exists(p):
        return None
    return json.load(open(p, encoding="utf-8"))


def import_bookmarks(prof, data):
    if not data:
        return "跳过"
    p = os.path.join(prof, "Bookmarks")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=3)
    # 关键：清掉加密副本，否则浏览器启动时用旧数据覆盖回来
    for name in ENCRYPTED_BOOKMARK_FILES:
        q = os.path.join(prof, name)
        if os.path.exists(q):
            try:
                os.remove(q)
            except OSError:
                pass
    return "已写入"


# ------------------------------------------------------------------ history
def export_history(prof):
    p = os.path.join(prof, "History")
    if not os.path.exists(p):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    cur = con.cursor()
    rows = []
    for uid, url, title, vc, tc, lvt in cur.execute(
            "SELECT id, url, title, visit_count, typed_count, last_visit_time FROM urls"):
        visits = [[vt, tr, vd] for vt, tr, vd in cur.execute(
            "SELECT visit_time, transition, visit_duration FROM visits WHERE url=?", (uid,))]
        rows.append({"url": url, "title": title or "", "visit_count": vc or 0,
                     "typed_count": tc or 0, "last_visit_time": lvt or 0,
                     "visits": visits})
    con.close()
    return {"urls": rows}


def import_history(prof, data):
    if not data:
        return "跳过"
    p = os.path.join(prof, "History")
    if not os.path.exists(p):
        return "跳过（本机还没有历史库，先启动一次浏览器）"
    con = sqlite3.connect(p)
    cur = con.cursor()
    added_u = added_v = 0
    for row in data.get("urls") or []:
        cur.execute("SELECT id FROM urls WHERE url=?", (row["url"],))
        got = cur.fetchone()
        if got:
            uid = got[0]
        else:
            cur.execute(
                "INSERT INTO urls (url, title, visit_count, typed_count, "
                "last_visit_time, hidden) VALUES (?,?,?,?,?,0)",
                (row["url"], row.get("title", ""), row.get("visit_count", 0),
                 row.get("typed_count", 0), row.get("last_visit_time", 0)))
            uid = cur.lastrowid
            added_u += 1
        for vt, tr, vd in row.get("visits") or []:
            cur.execute("SELECT 1 FROM visits WHERE url=? AND visit_time=?", (uid, vt))
            if cur.fetchone():
                continue
            cur.execute(
                "INSERT INTO visits (url, visit_time, from_visit, transition, "
                "segment_id, visit_duration, incremented_omnibox_typed_score, "
                "opener_visit, originator_cache_guid, originator_visit_id, "
                "originator_from_visit, originator_opener_visit, is_known_to_sync, "
                "consider_for_ntp_most_visited, external_referrer_url, visited_link_id) "
                "VALUES (?,?,0,?,0,?,0,0,'',0,0,0,0,0,'',0)",
                (uid, vt, tr or 0, vd or 0))
            added_v += 1
    con.commit()
    con.close()
    return "新增 %d 网址 / %d 访问" % (added_u, added_v)


# ------------------------------------------------------------------ passwords
def export_passwords(prof):
    p = os.path.join(prof, "Login Data")
    if not os.path.exists(p):
        return None
    con = sqlite3.connect("file:%s?mode=ro" % p.replace("\\", "/"), uri=True)
    con.row_factory = sqlite3.Row
    rows = []
    for r in con.execute(
            "SELECT origin_url, action_url, username_element, username_value, "
            "password_element, signon_realm, date_created, blacklisted_by_user, "
            "scheme, password_value, times_used, display_name, icon_url, "
            "federation_url, skip_zero_click FROM logins"):
        d = dict(r)
        if isinstance(d.get("password_value"), bytes):
            d["password_value"] = base64.b64encode(d["password_value"]).decode("ascii")
        rows.append(d)
    con.close()
    return {"logins": rows}


def import_passwords(prof, data):
    if not data:
        return "跳过"
    p = os.path.join(prof, "Login Data")
    if not os.path.exists(p):
        return "跳过（本机还没有密码库，先启动一次浏览器）"
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(logins)")]
    added = skipped = 0
    for row in data.get("logins") or []:
        cur.execute(
            "SELECT 1 FROM logins WHERE origin_url=? AND username_value=? AND signon_realm=?",
            (row.get("origin_url"), row.get("username_value"), row.get("signon_realm")))
        if cur.fetchone():
            skipped += 1
            continue
        use = {k: v for k, v in row.items() if k in cols}
        if "password_value" in use and isinstance(use["password_value"], str):
            try:
                use["password_value"] = base64.b64decode(use["password_value"])
            except Exception:
                use["password_value"] = b""
        if not use:
            continue
        cur.execute("INSERT INTO logins (%s) VALUES (%s)"
                    % (",".join(use), ",".join("?" * len(use))), list(use.values()))
        added += 1
    con.commit()
    con.close()
    return "新增 %d 条 / 已存在 %d 条" % (added, skipped)


# ------------------------------------------------------------------ main
EXPORTERS = {"bookmarks": export_bookmarks, "history": export_history,
             "passwords": export_passwords}
IMPORTERS = {"bookmarks": import_bookmarks, "history": import_history,
             "passwords": import_passwords}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--server", default="http://127.0.0.1:8787")
    ap.add_argument("--user", required=True)
    ap.add_argument("--pass", dest="password", required=True)
    ap.add_argument("--types", default="bookmarks,history,passwords")
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--token-file", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                        ".sync-token.json"))
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果（给 UI 用）")
    a = ap.parse_args()

    result = {"ok": False, "steps": [], "error": None}
    try:
        if a.register:
            api(a.server, "/api/register", {"user": a.user, "pass": a.password})
            result["steps"].append("注册成功")

        token = None
        if os.path.exists(a.token_file):
            try:
                cache = json.load(open(a.token_file, encoding="utf-8"))
                if cache.get("server") == a.server and cache.get("user") == a.user:
                    token = cache.get("token")
            except Exception:
                token = None
        if not token:
            r = api(a.server, "/api/login", {"user": a.user, "pass": a.password})
            token = r["token"]
            json.dump({"server": a.server, "user": a.user, "token": token},
                      open(a.token_file, "w", encoding="utf-8"))
            result["steps"].append("登录成功")

        types = [t.strip() for t in a.types.split(",") if t.strip()]
        types = [t for t in types if t in EXPORTERS]
        if not types:
            raise RuntimeError("没有可同步的类型")

        remote = api(a.server, "/api/pull?types=" + ",".join(types), token=token)
        push_body = {}
        for t in types:
            try:
                local = EXPORTERS[t](a.profile)
            except Exception as e:  # noqa: BLE001
                result["steps"].append("%s 读取失败：%s" % (t, e))
                continue
            rdata = (remote.get(t) or {}).get("data")
            if local is None and rdata is None:
                continue
            if rdata is None:
                # 只有本地：上传
                push_body[t] = {"version": 0, "data": local}
                result["steps"].append("%s → 上传（本地 %s）"
                                       % (t, _size(local)))
            elif local is None:
                # 只有服务器：下载
                try:
                    msg = IMPORTERS[t](a.profile, rdata)
                    result["steps"].append("%s ← 下载（%s）" % (t, msg))
                except Exception as e:  # noqa: BLE001
                    result["steps"].append("%s 写入失败：%s" % (t, e))
            else:
                # 两边都有：交给服务器并集，再写回本地
                push_body[t] = {"version": (remote.get(t) or {}).get("version", 0),
                                "data": local}

        if push_body:
            pushed = api(a.server, "/api/push", push_body, token=token)
            for t, payload in pushed.items():
                try:
                    msg = IMPORTERS[t](a.profile, payload.get("data"))
                    result["steps"].append("%s ⇄ 合并（%s）" % (t, msg))
                except Exception as e:  # noqa: BLE001
                    result["steps"].append("%s 写入失败：%s" % (t, e))

        result["ok"] = True
    except Exception as e:  # noqa: BLE001
        result["error"] = str(e)

    if a.json:
        print(json.dumps(result, ensure_ascii=False))
    else:
        for s in result["steps"]:
            print("  " + s)
        print("完成" if result["ok"] else "失败: %s" % result["error"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
