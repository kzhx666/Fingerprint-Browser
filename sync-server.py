# -*- coding: utf-8 -*-
"""同步服务器（纯标准库，可跑在任意 Windows/Linux 机器或 VPS 上）

启动:  python sync-server.py --port 8787 --data sync-server.db
       python sync-server.py --port 8787 --data sync-server.db --host 0.0.0.0

接口:
  POST /api/register  {"user": "...", "pass": "..."}          -> {"ok": true}
  POST /api/login     {"user": "...", "pass": "..."}          -> {"token": "..."}
  GET  /api/pull?types=bookmarks,history&since=0              -> {"bookmarks": {"version": N, "data": ...}, ...}
  POST /api/push      {"bookmarks": {"version": N, "data": ...}} -> {"bookmarks": {"version": M}}
  GET  /api/whoami                                            -> {"user": "..."}

鉴权: Authorization: Bearer <token>

存储: SQLite
  users(username PK, salt, pwhash, created)
  tokens(token PK, username, created)
  blobs(username, type, version, data, updated, PRIMARY KEY(username, type))
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DB_LOCK = threading.Lock()
DB_PATH = "sync-server.db"
TYPES = ("bookmarks", "history", "passwords", "extensions")


# ------------------------------------------------------------------ storage
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with DB_LOCK, db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            username TEXT PRIMARY KEY,
            salt BLOB NOT NULL,
            pwhash BLOB NOT NULL,
            created INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tokens(
            token TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            created INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS blobs(
            username TEXT NOT NULL,
            type TEXT NOT NULL,
            version INTEGER NOT NULL,
            data TEXT NOT NULL,
            updated INTEGER NOT NULL,
            PRIMARY KEY(username, type)
        );
        """)


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)


# ------------------------------------------------------------------ merge
def merge_json_list(a, b, key_fields):
    """按 key_fields 去重的并集，a 在前"""
    seen = set()
    out = []
    for item in list(a or []) + list(b or []):
        k = tuple(str(item.get(f, "")) for f in key_fields)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def merge_tree(local, remote):
    """书签树并集：按 url / 文件夹名 递归合并"""
    if not isinstance(local, dict) or not isinstance(remote, dict):
        return local if local else remote

    def key_of(node):
        if node.get("type") == "url":
            return ("url", node.get("url", ""))
        return ("folder", node.get("name", ""))

    def merge_into(dst, src):
        dst_kids = dst.setdefault("children", [])
        index = {}
        for c in dst_kids:
            index.setdefault(key_of(c), []).append(c)
        for sc in src.get("children") or []:
            k = key_of(sc)
            if k in index and index[k]:
                if sc.get("type") == "folder":
                    merge_into(index[k][0], sc)
                continue
            clone = json.loads(json.dumps(sc))
            dst_kids.append(clone)
            index.setdefault(k, []).append(clone)

    out = json.loads(json.dumps(local))
    out_roots = out.setdefault("roots", {})
    for name, rroot in (remote.get("roots") or {}).items():
        if not isinstance(rroot, dict):
            continue
        droot = out_roots.get(name)
        if not isinstance(droot, dict):
            out_roots[name] = json.loads(json.dumps(rroot))
            continue
        if rroot.get("type") == "folder":
            merge_into(droot, rroot)
    return out


def merge_payload(kind, local, remote):
    if local is None:
        return remote
    if remote is None:
        return local
    if kind == "bookmarks":
        return merge_tree(local, remote)
    if kind == "history":
        urls = merge_json_list(local.get("urls"), remote.get("urls"),
                               ("url", "visit_time"))
        return {"urls": urls}
    if kind == "passwords":
        rows = merge_json_list(local.get("logins"), remote.get("logins"),
                               ("origin_url", "username_value", "signon_realm"))
        return {"logins": rows}
    return local


# ------------------------------------------------------------------ http
class Handler(BaseHTTPRequestHandler):
    server_version = "KZSync/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    # -- helpers
    def send_json(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        return json.loads(self.rfile.read(n).decode("utf-8"))

    def auth_user(self):
        h = self.headers.get("Authorization") or ""
        if not h.startswith("Bearer "):
            return None
        token = h[7:].strip()
        with DB_LOCK, db() as conn:
            row = conn.execute("SELECT username FROM tokens WHERE token=?",
                               (token,)).fetchone()
        return row["username"] if row else None

    # -- routes
    def do_GET(self):
        path, _, query = self.path.partition("?")
        params = {}
        for part in query.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v

        if path == "/api/ping":
            self.send_json(200, {"ok": True, "server": "kz-sync", "time": int(time.time())})
            return

        user = self.auth_user()
        if not user:
            self.send_json(401, {"error": "unauthorized"})
            return

        if path == "/api/whoami":
            self.send_json(200, {"user": user})
            return

        if path == "/api/pull":
            wanted = [t for t in (params.get("types") or "").split(",") if t in TYPES]
            out = {}
            with DB_LOCK, db() as conn:
                for t in wanted:
                    row = conn.execute(
                        "SELECT version, data FROM blobs WHERE username=? AND type=?",
                        (user, t)).fetchone()
                    if row:
                        out[t] = {"version": row["version"],
                                  "data": json.loads(row["data"])}
            self.send_json(200, out)
            return

        self.send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]

        if path == "/api/register":
            body = self.read_json()
            user = (body.get("user") or "").strip()
            pw = body.get("pass") or ""
            if len(user) < 2 or len(pw) < 4:
                self.send_json(400, {"error": "账号至少2位、密码至少4位"})
                return
            salt = secrets.token_bytes(16)
            with DB_LOCK, db() as conn:
                if conn.execute("SELECT 1 FROM users WHERE username=?",
                                (user,)).fetchone():
                    self.send_json(409, {"error": "账号已存在"})
                    return
                conn.execute(
                    "INSERT INTO users(username, salt, pwhash, created) VALUES(?,?,?,?)",
                    (user, salt, hash_password(pw, salt), int(time.time())))
            self.send_json(200, {"ok": True})
            return

        if path == "/api/login":
            body = self.read_json()
            user = (body.get("user") or "").strip()
            pw = body.get("pass") or ""
            with DB_LOCK, db() as conn:
                row = conn.execute("SELECT salt, pwhash FROM users WHERE username=?",
                                   (user,)).fetchone()
                if not row or not hmac.compare_digest(
                        hash_password(pw, row["salt"]), row["pwhash"]):
                    self.send_json(403, {"error": "账号或密码错误"})
                    return
                token = secrets.token_urlsafe(32)
                conn.execute("INSERT INTO tokens(token, username, created) VALUES(?,?,?)",
                             (token, user, int(time.time())))
            self.send_json(200, {"token": token, "user": user})
            return

        user = self.auth_user()
        if not user:
            self.send_json(401, {"error": "unauthorized"})
            return

        if path == "/api/push":
            body = self.read_json()
            result = {}
            with DB_LOCK, db() as conn:
                for t, payload in body.items():
                    if t not in TYPES or not isinstance(payload, dict):
                        continue
                    row = conn.execute(
                        "SELECT version, data FROM blobs WHERE username=? AND type=?",
                        (user, t)).fetchone()
                    if row:
                        merged = merge_payload(t, payload.get("data"),
                                               json.loads(row["data"]))
                        version = row["version"] + 1
                        conn.execute(
                            "UPDATE blobs SET version=?, data=?, updated=? "
                            "WHERE username=? AND type=?",
                            (version, json.dumps(merged, ensure_ascii=False),
                             int(time.time()), user, t))
                    else:
                        merged = payload.get("data")
                        version = 1
                        conn.execute(
                            "INSERT INTO blobs(username, type, version, data, updated) "
                            "VALUES(?,?,?,?,?)",
                            (user, t, version,
                             json.dumps(merged, ensure_ascii=False), int(time.time())))
                    result[t] = {"version": version, "data": merged}
            self.send_json(200, result)
            return

        self.send_json(404, {"error": "not found"})


def main():
    global DB_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--data", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "sync-server.db"))
    a = ap.parse_args()
    DB_PATH = a.data
    init_db()
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print("KZ sync server on http://%s:%d  (db: %s)" % (a.host, a.port, DB_PATH))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
