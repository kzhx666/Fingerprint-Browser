# -*- coding: utf-8 -*-
"""
本地 HTTP 代理 → 远程带认证 SOCKS5 转发器（工程化版）。

改进:
- 账密从 --config 指向的 JSON 文件读取，读完即删；不进命令行/进程列表
- --idle-timeout: 长时间无活动自动退出，不留孤儿进程
- 每个节点用独立端口，由管理器按节点串分配

浏览器代理填:  http://127.0.0.1:<listen-port>
"""
import argparse
import json
import os
import select
import socket
import socketserver
import threading
import time
import urllib.parse

import socks

REMOTE = {}
ACTIVE = 0
ACTIVE_LOCK = threading.Lock()
LAST = time.time()
IDLE_TIMEOUT = 300


def make_socket(host, port):
    s = socks.socksocket()
    s.set_proxy(socks.SOCKS5, REMOTE["host"], REMOTE["port"],
                username=REMOTE["user"], password=REMOTE["passwd"])
    s.settimeout(20)
    s.connect((host, port))
    s.settimeout(None)
    return s


def pump(a, b, initial=b""):
    global ACTIVE, LAST
    with ACTIVE_LOCK:
        ACTIVE += 1
    if initial:
        try:
            b.sendall(initial)
        except Exception:
            with ACTIVE_LOCK:
                ACTIVE -= 1
            return
    pair = [a, b]
    try:
        while True:
            rd, _, _ = select.select(pair, [], [], 60)
            if not rd:
                break
            for s in rd:
                d = s.recv(65536)
                if not d:
                    return
                (b if s is a else a).sendall(d)
                with ACTIVE_LOCK:
                    LAST = time.time()
    except Exception:
        pass
    finally:
        with ACTIVE_LOCK:
            ACTIVE -= 1
            LAST = time.time()
        for s in pair:
            try:
                s.close()
            except Exception:
                pass


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        global LAST
        with ACTIVE_LOCK:
            LAST = time.time()
        c = self.request
        try:
            c.settimeout(20)
            buf = b""
            while b"\r\n\r\n" not in buf:
                d = c.recv(8192)
                if not d:
                    return
                buf += d
                if len(buf) > 65536:
                    return
            head, rest = buf.split(b"\r\n\r\n", 1)
            lines = head.decode("latin-1").split("\r\n")
            parts = lines[0].split(" ")
            if len(parts) < 3:
                return
            method, target = parts[0].upper(), parts[1]

            if method == "CONNECT":
                host, port = target.rsplit(":", 1)
                try:
                    r = make_socket(host, int(port))
                except Exception as e:
                    c.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n" + str(e).encode())
                    return
                c.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                c.settimeout(None)
                pump(c, r, rest)
            else:
                u = urllib.parse.urlsplit(target)
                host = u.hostname
                port = u.port or 80
                path = u.path or "/"
                if u.query:
                    path += "?" + u.query
                try:
                    r = make_socket(host, port)
                except Exception as e:
                    c.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n" + str(e).encode())
                    return
                req = lines[0].split(" ")
                req[1] = path
                first = " ".join(req) + "\r\n"
                hdrs = [l for l in lines[1:]
                        if not l.lower().startswith("proxy-connection")]
                payload = (first + "\r\n".join(hdrs) + "\r\n\r\n").encode("latin-1") + rest
                r.sendall(payload)
                c.settimeout(None)
                pump(c, r)
        except Exception:
            try:
                c.close()
            except Exception:
                pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main():
    global IDLE_TIMEOUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="含账密的 JSON，读完即删")
    ap.add_argument("--listen-host", default="127.0.0.1")
    ap.add_argument("--listen-port", type=int, required=True)
    ap.add_argument("--idle-timeout", type=int, default=300)
    a = ap.parse_args()

    with open(a.config, "r", encoding="utf-8-sig") as f:
        cfg = json.load(f)
    try:
        os.remove(a.config)
    except OSError:
        pass

    REMOTE.update(host=cfg["host"], port=int(cfg["port"]),
                  user=cfg["user"], passwd=cfg["passwd"])
    IDLE_TIMEOUT = a.idle_timeout

    srv = Server((a.listen_host, a.listen_port), Handler)
    print("http-proxy %s:%d -> socks5 %s:%s (auth) idle=%ds" %
          (a.listen_host, a.listen_port, REMOTE["host"], REMOTE["port"],
           IDLE_TIMEOUT), flush=True)
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 1},
                         daemon=True)
    t.start()
    try:
        while True:
            time.sleep(5)
            with ACTIVE_LOCK:
                idle = ACTIVE == 0 and (time.time() - LAST) > IDLE_TIMEOUT
            if idle:
                print("idle timeout, exit", flush=True)
                break
    except KeyboardInterrupt:
        pass
    srv.shutdown()
    try:
        os.remove(_port_file(a.listen_port))
    except OSError:
        pass


def _port_file(port):
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_relay")
    return os.path.join(d, "port-%d.json" % port)


if __name__ == "__main__":
    main()
