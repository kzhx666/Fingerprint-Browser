# -*- coding: utf-8 -*-
"""节点链接 → 本地 SOCKS5

支持节点: vless://  hysteria2://  hy2://  tuic://  ss://

内核优先级（自动探测，任一可用即可）:
  1. sing-box  (JSON 配置)
  2. mihomo    (Clash Meta, YAML 配置)
  3. xray      (JSON 配置)
若都找不到，`ensure-core` 会自动下载 sing-box 到 <root>\\tools\\

用法:
  python node-proxy.py ensure-core
  python node-proxy.py start --node "vless://..." --port 10801 --workdir D:\\KZProfiles\\主控1
  python node-proxy.py stop  --workdir D:\\KZProfiles\\主控1
  python node-proxy.py which
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(ROOT, "tools")

SEARCH = {
    "sing-box": [
        os.path.join(TOOLS, "sing-box.exe"),
        r"E:\v2rayN-windows-64\bin\sing_box\sing-box.exe",
        r"C:\Program Files\sing-box\sing-box.exe",
        r"C:\Program Files (x86)\sing-box\sing-box.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "sing-box", "sing-box.exe"),
    ],
    "mihomo": [
        os.path.join(TOOLS, "mihomo.exe"),
        r"E:\v2rayN-windows-64\bin\mihomo\mihomo.exe",
        r"E:\FlClash\FlClashCore.exe",
        r"C:\Program Files\mihomo\mihomo.exe",
    ],
    "xray": [
        os.path.join(TOOLS, "xray.exe"),
        r"E:\v2rayN-windows-64\bin\xray\xray.exe",
        r"C:\Program Files\Xray\xray.exe",
    ],
}

NO_WINDOW = 0x08000000


# ------------------------------------------------------------------ 内核探测

def find_core(kind=None):
    kinds = [kind] if kind else ["sing-box", "mihomo", "xray"]
    for k in kinds:
        for p in SEARCH[k]:
            if p and os.path.exists(p):
                return k, p
        hit = shutil.which(k)
        if hit:
            return k, hit
    return None, None


def which():
    k, p = find_core()
    if k:
        print("%s\t%s" % (k, p))
        return 0
    print("NONE")
    return 1


# ------------------------------------------------------------------ 自动下载

SINGBOX_API = "https://api.github.com/repos/SagerNet/sing-box/releases/latest"


def ensure_core(quiet=False):
    k, p = find_core()
    if k:
        if not quiet:
            print("OK %s %s" % (k, p))
        return k, p

    os.makedirs(TOOLS, exist_ok=True)
    target = os.path.join(TOOLS, "sing-box.exe")
    if not quiet:
        print("未找到内核，尝试下载 sing-box …")

    try:
        req = urllib.request.Request(SINGBOX_API, headers={"User-Agent": "node-proxy/1.0"})
        with urllib.request.urlopen(req, timeout=25) as r:
            info = json.load(r)
        tag = info["tag_name"].lstrip("v")
        asset = None
        for a in info.get("assets", []):
            n = a["name"].lower()
            if "windows-amd64" in n and n.endswith(".zip"):
                asset = a["browser_download_url"]
                break
        if not asset:
            raise RuntimeError("发布页里没有 windows-amd64 压缩包")
    except Exception as e:
        raise RuntimeError(
            "自动下载失败（%s）。\n"
            "请手动任选其一：\n"
            "  1) 装 v2rayN / FlClash / sing-box，程序会自动探测\n"
            "  2) 把 sing-box.exe 放到 %s" % (e, TOOLS))

    zpath = os.path.join(TOOLS, "_singbox.zip")
    try:
        req = urllib.request.Request(asset, headers={"User-Agent": "node-proxy/1.0"})
        with urllib.request.urlopen(req, timeout=180) as r, open(zpath, "wb") as f:
            shutil.copyfileobj(r, f)
        with zipfile.ZipFile(zpath) as z:
            exe = None
            for n in z.namelist():
                if n.lower().endswith("sing-box.exe"):
                    exe = n
                    break
            if not exe:
                raise RuntimeError("压缩包里没有 sing-box.exe")
            with z.open(exe) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    except Exception as e:
        raise RuntimeError("下载 sing-box 失败：%s" % e)
    finally:
        try:
            os.remove(zpath)
        except Exception:
            pass

    if not os.path.exists(target):
        raise RuntimeError("下载后仍找不到 %s" % target)
    if not quiet:
        print("OK sing-box %s" % target)
    return "sing-box", target


# ------------------------------------------------------------------ 节点解析

def qs(q):
    return dict(urllib.parse.parse_qsl(q or ""))


def b64decode_maybe(s):
    s = s.strip().replace("-", "+").replace("_", "/")
    pad = "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s + pad).decode("utf-8", "replace")
    except Exception:
        return s


def build_outbound(node):
    if node.startswith("vless://"):
        u = urllib.parse.urlparse(node)
        q = qs(u.query)
        ob = {"type": "vless", "tag": "out", "server": u.hostname,
              "server_port": u.port or 443, "uuid": u.username or ""}
        if q.get("flow"):
            ob["flow"] = q["flow"]
        if q.get("security", "none") in ("tls", "reality", "xtls"):
            tls = {"enabled": True}
            if q.get("sni"):
                tls["server_name"] = q["sni"]
            elif q.get("host"):
                tls["server_name"] = q["host"]
            if q.get("allowInsecure") == "1" or q.get("insecure") == "1":
                tls["insecure"] = True
            if q["security"] == "reality":
                tls["reality"] = {"enabled": True, "public_key": q.get("pbk", ""),
                                  "short_id": q.get("sid", "")}
                if q.get("fp"):
                    tls["utls"] = {"enabled": True, "fingerprint": q["fp"]}
            ob["tls"] = tls
        t = q.get("type", "tcp")
        if t == "ws":
            tr = {"type": "ws", "path": q.get("path", "/")}
            if q.get("host"):
                tr["headers"] = {"Host": q["host"]}
            ob["transport"] = tr
        elif t == "grpc":
            ob["transport"] = {"type": "grpc", "service_name": q.get("serviceName", "")}
        elif t == "http":
            ob["transport"] = {"type": "http", "host": [q.get("host", "")],
                               "path": q.get("path", "/")}
        return ob

    if node.startswith(("hysteria2://", "hy2://")):
        u = urllib.parse.urlparse(node)
        q = qs(u.query)
        pw = urllib.parse.unquote(u.password or u.username or "")
        ob = {"type": "hysteria2", "tag": "out", "server": u.hostname,
              "server_port": u.port or 443, "password": pw}
        tls = {"enabled": True}
        if q.get("sni"):
            tls["server_name"] = q["sni"]
        if q.get("insecure") == "1":
            tls["insecure"] = True
        ob["tls"] = tls
        if q.get("obfs"):
            ob["obfs"] = {"type": q["obfs"], "password": q.get("obfs-password", "")}
        return ob

    if node.startswith("tuic://"):
        u = urllib.parse.urlparse(node)
        q = qs(u.query)
        ob = {"type": "tuic", "tag": "out", "server": u.hostname,
              "server_port": u.port or 443, "uuid": u.username or "",
              "password": urllib.parse.unquote(u.password or "")}
        tls = {"enabled": True}
        if q.get("sni"):
            tls["server_name"] = q["sni"]
        if q.get("alpn"):
            tls["alpn"] = q["alpn"].split(",")
        if q.get("allow_insecure") == "1" or q.get("insecure") == "1":
            tls["insecure"] = True
        ob["tls"] = tls
        return ob

    if node.startswith("ss://"):
        body = node[5:].split("#", 1)[0]
        if "@" in body:
            userinfo, hostport = body.rsplit("@", 1)
            dec = b64decode_maybe(urllib.parse.unquote(userinfo))
            if ":" not in dec:
                dec = urllib.parse.unquote(userinfo)
            method, _, pw = dec.partition(":")
            host, _, port = hostport.partition(":")
            return {"type": "shadowsocks", "tag": "out", "server": host,
                    "server_port": int(port or 8388), "method": method, "password": pw}
        dec = b64decode_maybe(body)
        m = re.match(r"(.+?):(.+)@(.+?):(\d+)", dec)
        if not m:
            raise ValueError("无法解析 ss 链接")
        return {"type": "shadowsocks", "tag": "out", "server": m.group(3),
                "server_port": int(m.group(4)), "method": m.group(1),
                "password": m.group(2)}

    raise ValueError("不支持的节点类型（支持 vless/hysteria2/hy2/tuic/ss）")


# ------------------------------------------------------------------ 配置生成

def write_singbox(workdir, ob, port):
    cfg = {
        "log": {"level": "warn", "timestamp": True},
        "inbounds": [{"type": "socks", "tag": "socks-in",
                      "listen": "127.0.0.1", "listen_port": port}],
        "outbounds": [ob],
    }
    path = os.path.join(workdir, "singbox.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


def yq(v):
    """YAML 标量"""
    s = str(v)
    if s == "" or any(c in s for c in ":#{}[],&*?|-<>=!%@`'\"\\") or s.strip() != s:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def write_mihomo(workdir, ob, port):
    t = ob["type"]
    lines = ["mixed-port: %d" % port, "allow-lan: false", "mode: global",
             "log-level: warning", "ipv6: false", "proxies:", "  - name: node"]
    if t == "vless":
        lines += ["    type: vless", "    server: " + yq(ob["server"]),
                  "    port: %d" % ob["server_port"], "    uuid: " + yq(ob["uuid"]),
                  "    udp: true"]
        if ob.get("flow"):
            lines.append("    flow: " + yq(ob["flow"]))
        tls = ob.get("tls") or {}
        if tls.get("enabled"):
            lines += ["    tls: true"]
            if tls.get("server_name"):
                lines.append("    servername: " + yq(tls["server_name"]))
            if tls.get("insecure"):
                lines.append("    skip-cert-verify: true")
            if tls.get("reality"):
                lines += ["    reality-opts:",
                          "      public-key: " + yq(tls["reality"].get("public_key", "")),
                          "      short-id: " + yq(tls["reality"].get("short_id", ""))]
                fp = (tls.get("utls") or {}).get("fingerprint")
                if fp:
                    lines.append("    client-fingerprint: " + yq(fp))
        tr = ob.get("transport") or {}
        if tr.get("type") == "ws":
            lines += ["    network: ws", "    ws-opts:",
                      "      path: " + yq(tr.get("path", "/"))]
            h = (tr.get("headers") or {}).get("Host")
            if h:
                lines += ["      headers:", "        Host: " + yq(h)]
        elif tr.get("type") == "grpc":
            lines += ["    network: grpc", "    grpc-opts:",
                      "      grpc-service-name: " + yq(tr.get("service_name", ""))]
    elif t == "hysteria2":
        lines += ["    type: hysteria2", "    server: " + yq(ob["server"]),
                  "    port: %d" % ob["server_port"], "    password: " + yq(ob["password"])]
        tls = ob.get("tls") or {}
        if tls.get("server_name"):
            lines.append("    sni: " + yq(tls["server_name"]))
        if tls.get("insecure"):
            lines.append("    skip-cert-verify: true")
    elif t == "tuic":
        lines += ["    type: tuic", "    server: " + yq(ob["server"]),
                  "    port: %d" % ob["server_port"], "    uuid: " + yq(ob["uuid"]),
                  "    password: " + yq(ob["password"]),
                  "    udp-relay-mode: native", "    congestion-controller: bbr"]
        tls = ob.get("tls") or {}
        if tls.get("server_name"):
            lines.append("    sni: " + yq(tls["server_name"]))
        if tls.get("alpn"):
            lines.append("    alpn: [" + ", ".join(yq(a) for a in tls["alpn"]) + "]")
        if tls.get("insecure"):
            lines.append("    skip-cert-verify: true")
    elif t == "shadowsocks":
        lines += ["    type: ss", "    server: " + yq(ob["server"]),
                  "    port: %d" % ob["server_port"], "    cipher: " + yq(ob["method"]),
                  "    password: " + yq(ob["password"]), "    udp: true"]
    else:
        raise ValueError("mihomo 不支持该节点类型: " + t)

    lines += ["proxy-groups:", "  - name: PROXY", "    type: select", "    proxies: [node]",
              "rules:", "  - MATCH,PROXY", ""]
    path = os.path.join(workdir, "mihomo.yaml")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def write_xray(workdir, ob, port):
    """xray 只覆盖 vless / ss / trojan 等常见类型"""
    t = ob["type"]
    if t == "vless":
        out = {"protocol": "vless", "settings": {"vnext": [{
            "address": ob["server"], "port": ob["server_port"],
            "users": [{"id": ob["uuid"], "encryption": "none",
                       "flow": ob.get("flow", "")}]}]}}
        tls = ob.get("tls") or {}
        if tls.get("enabled"):
            out["streamSettings"] = {"security": "tls", "tlsSettings": {
                "serverName": tls.get("server_name", ""),
                "allowInsecure": bool(tls.get("insecure"))}}
            tr = ob.get("transport") or {}
            if tr.get("type") == "ws":
                out["streamSettings"]["network"] = "ws"
                out["streamSettings"]["wsSettings"] = {
                    "path": tr.get("path", "/"),
                    "headers": tr.get("headers", {})}
    elif t == "shadowsocks":
        out = {"protocol": "shadowsocks", "settings": {"servers": [{
            "address": ob["server"], "port": ob["server_port"],
            "method": ob["method"], "password": ob["password"]}]}}
    else:
        raise ValueError("xray 不支持该节点类型（请装 sing-box 或 mihomo）: " + t)

    cfg = {"log": {"loglevel": "warning"},
           "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks",
                         "settings": {"udp": True}}],
           "outbounds": [out]}
    path = os.path.join(workdir, "xray.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return path


# ------------------------------------------------------------------ 生命周期

def stop(workdir, quiet=False):
    pidfile = os.path.join(workdir, "proxy.pid")
    if not os.path.exists(pidfile):
        return
    try:
        pid = int(open(pidfile).read().strip())
    except Exception:
        try:
            os.remove(pidfile)
        except Exception:
            pass
        return
    subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                   capture_output=True, creationflags=NO_WINDOW)
    try:
        os.remove(pidfile)
    except Exception:
        pass
    try:
        os.remove(active_node_path(workdir))
    except Exception:
        pass
    if not quiet:
        print("stopped %d" % pid)


def _socks_probe(port, host="www.gstatic.com", dest_port=80, timeout=6.0):
    """通过本地 SOCKS5 做一次 CONNECT，确认节点真的能出网。

    sing-box 即使上游服务器不可达也会正常监听端口，所以"端口起来了"不等于
    "节点可用"。节点池要靠这一步才能发现坏节点并切到下一个。
    """
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return False, "连不上本地代理: %s" % e
    try:
        s.settimeout(timeout)
        s.sendall(b"\x05\x01\x00")
        if s.recv(2) != b"\x05\x00":
            return False, "SOCKS5 握手失败"
        hb = host.encode("idna") if not host.replace(".", "").isdigit() else host.encode()
        req = b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + dest_port.to_bytes(2, "big")
        s.sendall(req)
        resp = s.recv(4)
        if len(resp) < 2 or resp[1] != 0:
            return False, "CONNECT 被拒绝 (code=%s)" % (resp[1] if len(resp) > 1 else "?")
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, "探测失败: %s" % e
    finally:
        try:
            s.close()
        except Exception:
            pass


def _node_stats_path():
    return os.path.join(ROOT, "_node-stats.json")


def _node_key(link):
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def _load_stats():
    try:
        with open(_node_stats_path(), encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_stats(stats):
    try:
        with open(_node_stats_path(), "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        pass


def _record_result(link, ok):
    stats = _load_stats()
    k = _node_key(link)
    e = stats.setdefault(k, {"ok": 0, "fail": 0, "label": "", "last_ok": 0,
                             "last_fail": 0})
    # 记个可读标签，方便人看
    try:
        ob = build_outbound(link)
        e["label"] = "%s %s:%s" % (ob.get("type"), ob.get("server"),
                                   ob.get("server_port"))
    except Exception:
        e["label"] = link[:40]
    if ok:
        e["ok"] = e.get("ok", 0) + 1
        e["last_ok"] = int(time.time())
    else:
        e["fail"] = e.get("fail", 0) + 1
        e["last_fail"] = int(time.time())
    _save_stats(stats)


def _sort_by_health(nodes):
    """成功率高的排前面；没记录的保持原顺序排在已知好节点之后。"""
    if len(nodes) <= 1:
        return nodes
    stats = _load_stats()

    def score(item):
        idx, link = item
        e = stats.get(_node_key(link))
        if not e:
            return (1, 0.0, 0, -idx)  # 未知：排在已知可用之后
        ok, fail = e.get("ok", 0), e.get("fail", 0)
        total = ok + fail
        rate = (ok / total) if total else 0.0
        return (0, -rate, -e.get("last_ok", 0), -idx)

    ordered = sorted(enumerate(nodes), key=score)
    return [n for _, n in ordered]


SUPPORTED_SCHEMES = ("vless://", "hysteria2://", "hy2://", "tuic://",
                     "ss://")


def _b64_try(text):
    """订阅常见是 base64；失败就原样返回。"""
    s = "".join(text.split())
    for pad in ("", "=", "==", "==="):
        try:
            return base64.b64decode(s + pad).decode("utf-8", "replace")
        except Exception:
            continue
    return text


def parse_subscription(body):
    """从订阅内容里挑出我们支持的节点链接。"""
    text = body
    # 先试着整体 base64 解一次
    decoded = _b64_try(body)
    if "://" in decoded:
        text = decoded
    found = []
    for line in re.split(r"[\r\n]+", text):
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if any(low.startswith(s) for s in SUPPORTED_SCHEMES):
            found.append(line)
    # 去重保序
    seen = set()
    out = []
    for n in found:
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def _http_get(url, timeout, use_proxy):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "KZProfileManager/1.0 (v2rayN compatible)"})
    if use_proxy:
        opener = urllib.request.build_opener()
    else:
        # 不走系统代理直连（很多订阅站要直连才通）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        return resp.read()


def fetch_subscription(url, timeout=20):
    """拉订阅并解析。返回 (节点列表, 原始字节数)。

    订阅站有时要直连、有时要走系统代理，所以两种都试一遍，
    把两次的失败原因都带出来，方便判断是网络问题还是地址问题。
    """
    if not url.lower().startswith(("http://", "https://")):
        raise RuntimeError("订阅地址必须是 http(s)://")
    errors = []
    body = None
    for use_proxy in (False, True):
        try:
            body = _http_get(url, timeout, use_proxy)
            break
        except Exception as e:  # noqa: BLE001
            tag = "走系统代理" if use_proxy else "直连"
            errors.append("%s: %s" % (tag, e))
    if body is None:
        raise RuntimeError("拉取失败（直连和代理都试过了）\n" + "\n".join(errors))
    text = body.decode("utf-8", "replace")
    nodes = parse_subscription(text)
    return nodes, len(body)


# ---------------------------------------------------------------- 节点库
def node_lib_path():
    return os.path.join(ROOT, "nodes.json")


def load_node_lib():
    try:
        with open(node_lib_path(), encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return data


def save_node_lib(items):
    tmp = node_lib_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)
    os.replace(tmp, node_lib_path())


def node_label(link, name=""):
    """给节点起个可读名字：名字 · 类型 主机:端口"""
    try:
        ob = build_outbound(link)
        base = "%s %s:%s" % (ob.get("type"), ob.get("server"),
                             ob.get("server_port"))
    except Exception:
        base = link.split("://", 1)[0] + " (无法解析)"
    return (name + " · " + base) if name else base


def node_add(link, name="", source=""):
    """加入节点库，已存在则跳过。返回 (是否新增, 条目)。"""
    items = load_node_lib()
    for it in items:
        if it.get("link") == link:
            return False, it
    entry = {
        "name": name or "",
        "link": link,
        "label": node_label(link, name),
        "type": link.split("://", 1)[0],
        "source": source,
        "added": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    items.append(entry)
    save_node_lib(items)
    return True, entry


def node_remove(index):
    items = load_node_lib()
    if index < 1 or index > len(items):
        return None
    gone = items.pop(index - 1)
    save_node_lib(items)
    return gone


def resolve_node_spec(spec):
    """把管理器传来的文本变成真正的节点链接。

    - 空 -> 空
    - 以协议开头 -> 原样
    - 含 | 的池 -> 逐段解析后重新拼
    - 否则当成节点库里的名字（label 或 name）去匹配
    """
    spec = (spec or "").strip()
    if not spec:
        return ""
    if "://" in spec:
        parts = split_nodes(spec)
        resolved = []
        for p_ in parts:
            resolved.append(resolve_node_spec(p_) if "://" not in p_ else p_)
        return "|".join([r for r in resolved if r])
    items = load_node_lib()
    low = spec.lower()
    for it in items:
        if it.get("label", "").lower() == low or it.get("name", "").lower() == low:
            return it["link"]
    for it in items:
        if low in it.get("label", "").lower():
            return it["link"]
    return spec


def split_nodes(spec):
    """节点池：支持 | 或换行分隔，例如 "vless://...|hy2://..."。"""
    parts = re.split(r"[|\r\n]+", spec or "")
    return [p.strip() for p in parts if p.strip()]


def active_node_path(workdir):
    return os.path.join(workdir, "proxy-active.txt")


def _start_one(workdir, node, port, quiet=False):
    kind, exe = ensure_core(quiet=True)
    os.makedirs(workdir, exist_ok=True)
    ob = build_outbound(node)
    stop(workdir, quiet=True)

    if kind == "sing-box":
        cfg = write_singbox(workdir, ob, port)
        cmd = [exe, "run", "-c", cfg]
    elif kind == "mihomo":
        cfg = write_mihomo(workdir, ob, port)
        cmd = [exe, "-f", cfg, "-d", workdir]
    else:
        cfg = write_xray(workdir, ob, port)
        cmd = [exe, "run", "-c", cfg]

    log_path = os.path.join(workdir, "proxy.log")
    with open(log_path, "w", encoding="utf-8") as logf:
        proc = subprocess.Popen(cmd, stdout=logf, stderr=subprocess.STDOUT,
                                cwd=workdir, creationflags=NO_WINDOW)
    with open(os.path.join(workdir, "proxy.pid"), "w") as f:
        f.write(str(proc.pid))

    for _ in range(60):
        if proc.poll() is not None:
            tail = ""
            try:
                tail = open(log_path, encoding="utf-8",
                            errors="replace").read()[-400:]
            except Exception:
                pass
            raise RuntimeError("%s 启动失败：%s" % (kind, tail))
        s = socket.socket()
        s.settimeout(0.3)
        try:
            s.connect(("127.0.0.1", port))
            s.close()
            if not quiet:
                print("OK %d %s %s" % (port, kind, ob.get("type")))
            return kind, ob
        except Exception:
            time.sleep(0.2)
        finally:
            try:
                s.close()
            except Exception:
                pass
    raise RuntimeError("%s 端口未就绪，日志：%s" % (kind, log_path))


def start(workdir, node, port, quiet=False):
    """节点池：按顺序尝试，第一个能用的就用；全部失败才报错。"""
    node = resolve_node_spec(node)
    nodes = _sort_by_health(split_nodes(node))
    if not nodes:
        raise RuntimeError("没有提供节点链接")
    if len(nodes) == 1:
        kind, ob = _start_one(workdir, nodes[0], port, quiet)
        _record_result(nodes[0], True)
        with open(active_node_path(workdir), "w", encoding="utf-8") as f:
            f.write("1\t%s\t%s\n" % (ob.get("type"), nodes[0]))
        return port

    errors = []
    for idx, n in enumerate(nodes, 1):
        try:
            kind, ob = _start_one(workdir, n, port, quiet=True)
        except Exception as e:  # noqa: BLE001
            errors.append("节点 %d/%d: %s" % (idx, len(nodes), str(e)[:200]))
            _record_result(n, False)
            stop(workdir, quiet=True)
            continue
        # 端口起来了不代表节点能用，实测一次出网
        ok, why = _socks_probe(port)
        if not ok:
            errors.append("节点 %d/%d: %s" % (idx, len(nodes), why))
            _record_result(n, False)
            stop(workdir, quiet=True)
            continue
        _record_result(n, True)
        with open(active_node_path(workdir), "w", encoding="utf-8") as f:
            f.write("%d\t%s\t%s\n" % (idx, ob.get("type"), n))
        if not quiet:
            print("OK %d %s %s (节点 %d/%d)"
                  % (port, kind, ob.get("type"), idx, len(nodes)))
        return port
    raise RuntimeError("节点池里 %d 个节点全部失败：\n%s"
                       % (len(nodes), "\n".join(errors)))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("start", "test"):
        p = sub.add_parser(name)
        p.add_argument("--node", required=True)
        p.add_argument("--port", type=int, default=10801)
        p.add_argument("--workdir", default=os.getcwd())
    p2 = sub.add_parser("stop")
    p2.add_argument("--workdir", default=os.getcwd())
    p3 = sub.add_parser("check")
    p3.add_argument("--node", required=True)
    sub.add_parser("which")
    sub.add_parser("ensure-core")
    pr = sub.add_parser("resolve")
    pr.add_argument("--spec", required=True)
    pn = sub.add_parser("nodes")
    pn.add_argument("--action", required=True,
                    choices=["list", "add", "remove", "import", "pool"])
    pn.add_argument("--link", default="")
    pn.add_argument("--name", default="")
    pn.add_argument("--index", type=int, default=0)
    pn.add_argument("--indexes", default="")
    pn.add_argument("--url", default="")
    pn.add_argument("--write", default="")
    p5 = sub.add_parser("subscribe")
    p5.add_argument("--url", required=True)
    p5.add_argument("--write", default="", help="把节点池写进这个文件的 profile.json")
    p4 = sub.add_parser("stats")
    p4.add_argument("--reset", action="store_true")
    a = ap.parse_args()

    try:
        if a.cmd == "check":
            nodes = split_nodes(resolve_node_spec(a.node))
            if not nodes:
                print("ERROR: 没有节点", file=sys.stderr)
                return 1
            bad = 0
            for idx, n in enumerate(nodes, 1):
                try:
                    ob = build_outbound(n)
                except Exception as e:  # noqa: BLE001
                    bad += 1
                    print("BAD  %d/%d %s" % (idx, len(nodes), str(e)[:120]))
                    continue
                print("OK   %d/%d %s %s:%s" % (idx, len(nodes),
                                               ob.get("type"), ob.get("server"),
                                               ob.get("server_port")))
            return 1 if bad else 0
        if a.cmd == "which":
            return which()
        if a.cmd == "ensure-core":
            ensure_core()
            return 0
        if a.cmd == "resolve":
            r = resolve_node_spec(a.spec)
            if not r:
                print("ERROR: 解析结果为空", file=sys.stderr)
                return 1
            if "://" not in r:
                print("ERROR: 无法解析：%s" % a.spec, file=sys.stderr)
                return 1
            print(r)
            return 0
        if a.cmd == "nodes":
            if a.action == "list":
                items = load_node_lib()
                if not items:
                    print("节点库是空的。")
                    return 0
                for i, it in enumerate(items, 1):
                    print("%2d. %s" % (i, it.get("label") or it.get("link", "")[:60]))
                return 0
            if a.action == "add":
                if not a.link:
                    print("ERROR: 需要 --link", file=sys.stderr)
                    return 1
                new, entry = node_add(a.link, a.name, "manual")
                print(("已加入: " if new else "已存在: ") + entry.get("label", ""))
                return 0
            if a.action == "remove":
                gone = node_remove(a.index)
                if not gone:
                    print("ERROR: 编号超出范围", file=sys.stderr)
                    return 1
                print("已删除: " + gone.get("label", ""))
                return 0
            if a.action == "import":
                if not a.url:
                    print("ERROR: 需要 --url", file=sys.stderr)
                    return 1
                try:
                    found, size = fetch_subscription(a.url)
                except Exception as e:  # noqa: BLE001
                    print("ERROR: 拉取失败：%s" % e, file=sys.stderr)
                    return 1
                added = 0
                for n in found:
                    ok, _ = node_add(n, "", a.url)
                    if ok:
                        added += 1
                print("订阅 %d 个节点，新增 %d 个（其余已存在）" % (len(found), added))
                return 0
            if a.action == "pool":
                idxs = [x.strip() for x in (a.indexes or "").split(",") if x.strip()]
                items = load_node_lib()
                picked = []
                for x in idxs:
                    try:
                        i = int(x)
                    except ValueError:
                        continue
                    if 1 <= i <= len(items):
                        picked.append(items[i - 1]["link"])
                if not picked:
                    print("ERROR: 没有有效的编号", file=sys.stderr)
                    return 1
                pool = "|".join(picked)
                if a.write:
                    pj = os.path.join(a.write, "profile.json")
                    prof = {}
                    if os.path.exists(pj):
                        try:
                            with open(pj, encoding="utf-8-sig") as f:
                                prof = json.load(f)
                        except Exception:
                            prof = {}
                    prof["node"] = pool
                    with open(pj, "w", encoding="utf-8") as f:
                        json.dump(prof, f, ensure_ascii=False, indent=2)
                    print("已写入 %d 个节点的池 -> %s" % (len(picked), pj))
                else:
                    print(pool)
                return 0
            return 1
        if a.cmd == "subscribe":
            try:
                nodes, size = fetch_subscription(a.url)
            except Exception as e:  # noqa: BLE001
                print("ERROR: 拉取订阅失败：%s" % e, file=sys.stderr)
                return 1
            if not nodes:
                print("ERROR: 订阅里没有找到支持的节点"
                      "（vless/hysteria2/hy2/tuic/ss）", file=sys.stderr)
                return 1
            print("拉取成功：%d 字节，解析出 %d 个可用节点" % (size, len(nodes)))
            for i, n in enumerate(nodes, 1):
                kind = n.split("://", 1)[0]
                print("  %2d. %-10s %s" % (i, kind, n[:70]))
            added = 0
            for n in nodes:
                ok, _ = node_add(n, "", a.url)
                if ok:
                    added += 1
            if added:
                print("已入库 %d 个新节点（节点库共 %d 个）"
                      % (added, len(load_node_lib())))
            if a.write:
                pj = os.path.join(a.write, "profile.json")
                prof = {}
                if os.path.exists(pj):
                    try:
                        with open(pj, encoding="utf-8-sig") as f:
                            prof = json.load(f)
                    except Exception:
                        prof = {}
                prof["node"] = "|".join(nodes)
                with open(pj, "w", encoding="utf-8") as f:
                    json.dump(prof, f, ensure_ascii=False, indent=2)
                print("已写入节点池 -> %s（%d 个节点）" % (pj, len(nodes)))
            else:
                print("\n节点池（可直接粘进「节点/节点池」框）：")
                print("|".join(nodes))
            return 0
        if a.cmd == "stats":
            if a.reset:
                _save_stats({})
                print("统计已清空")
                return 0
            stats = _load_stats()
            if not stats:
                print("还没有节点使用记录。")
                return 0
            rows = []
            for k, e in stats.items():
                ok, fail = e.get("ok", 0), e.get("fail", 0)
                total = ok + fail
                rate = (100.0 * ok / total) if total else 0.0
                rows.append((rate, ok, fail, e.get("label") or k,
                             e.get("last_ok", 0)))
            rows.sort(key=lambda r: (-r[0], -r[1]))
            print("%-6s %-5s %-5s %s" % ("成功率", "成功", "失败", "节点"))
            print("-" * 70)
            for rate, ok, fail, label, last in rows:
                when = time.strftime("%m-%d %H:%M",
                                     time.localtime(last)) if last else "-"
                print("%5.1f%% %-5d %-5d %s  (上次成功 %s)"
                      % (rate, ok, fail, label, when))
            return 0
        if a.cmd == "start":
            start(a.workdir, a.node, a.port)
        elif a.cmd == "test":
            start(a.workdir, a.node, a.port, quiet=True)
            ok, why = _socks_probe(a.port)
            ap = active_node_path(a.workdir)
            act = ""
            try:
                act = open(ap, encoding="utf-8").read().strip().split("\t")[0]
            except Exception:
                pass
            if ok:
                print("代理可用: socks5://127.0.0.1:%d (节点 %s)" % (a.port, act))
            else:
                print("端口已监听但出网失败: %s" % why, file=sys.stderr)
                return 1
        elif a.cmd == "stop":
            stop(a.workdir)
        return 0
    except Exception as e:
        print("ERROR: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
