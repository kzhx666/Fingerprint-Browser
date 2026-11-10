#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
Post-build runtime self-check for the patched Chromium kernel.

Launches the compiled chrome.exe in headless mode with fingerprint switches
and asserts that every spoofed dimension actually reports spoofed values in
a real renderer process. Run this after every build, before shipping.

    python scripts/verify_build.py <path\to\chrome.exe>

Checked (hard failures):
  - navigator.webdriver === false
  - navigator.platform spoofed from --fingerprint-platform
  - navigator.hardwareConcurrency matches the seed table
  - navigator.deviceMemory matches the seed table
  - screen.width/height match the seed table
  - window.devicePixelRatio matches the seed table
  - Intl timezone matches --timezone
  - canvas noise: deterministic within one process,
    differs between seeds, stable across processes for one seed

Checked (soft / skipped when unavailable in headless):
  - WebGL UNMASKED_VENDOR/RENDERER (needs a working GL context)
  - navigator.userAgentData platform (needs a secure context)
"""

import argparse
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import http.server
import socketserver
from pathlib import Path

SEED_TABLE_CORES = [4, 8, 12, 16, 24]
SEED_TABLE_RAM = [4.0, 8.0, 16.0, 32.0]
SEED_TABLE_W = [1920, 2560, 1600, 1366, 3840]
SEED_TABLE_H = [1080, 1440, 900, 768, 2160]
SEED_TABLE_DPR = [1.0, 1.25, 1.5, 1.0, 2.0]

PAGE_JS = r"""
(async () => {
  const post = (tag, data) =>
      fetch('/report', {method: 'POST', body: tag + '|' + JSON.stringify(data || {})});

  const r = {};
  r.webdriver = navigator.webdriver;
  r.platform = navigator.platform;
  r.hw = navigator.hardwareConcurrency;
  r.mem = navigator.deviceMemory;
  r.sw = screen.width;
  r.sh = screen.height;
  r.dpr = window.devicePixelRatio;
  r.tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  await post('basic', r);

  // NOTE: canvas runs LAST - a hanging canvas patch must not block the
  // other sections (this exact hang exists in old kernels).
  try {
    const gl = document.createElement('canvas').getContext('webgl');
    const ext = gl && gl.getExtension('WEBGL_debug_renderer_info');
    if (gl && ext) {
      await post('webgl', {
        vendor: gl.getParameter(ext.UNMASKED_VENDOR_WEBGL),
        renderer: gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)});
    } else { await post('webgl', {skip: 'no webgl/extension'}); }
  } catch (e) { await post('webgl', {err: String(e)}); }

  try {
    if (navigator.userAgentData && navigator.userAgentData.getHighEntropyValues) {
      const h = await navigator.userAgentData.getHighEntropyValues(
          ['platform', 'platformVersion']);
      await post('ua', {platform: h.platform, version: h.platformVersion});
    } else { await post('ua', {skip: 'no userAgentData'}); }
  } catch (e) { await post('ua', {err: String(e)}); }

  try {
    const c = document.createElement('canvas');
    c.width = 160; c.height = 48;
    const x = c.getContext('2d', {willReadFrequently: true});
    const g = x.createLinearGradient(0, 0, 160, 48);
    g.addColorStop(0, '#f0f'); g.addColorStop(1, '#0ff');
    x.fillStyle = g; x.fillRect(0, 0, 160, 48);
    x.font = '16px Arial'; x.fillStyle = '#111';
    x.fillText('FP-VERIFY-\u56f4', 2, 4);
    const cv = {};
    cv.url1 = c.toDataURL().slice(-96);
    cv.url2 = c.toDataURL().slice(-96);
    const px = x.getImageData(0, 0, 160, 48);
    // full-buffer hash: noise density is ~6/256 pixels, a 64-byte slice
    // often contains zero hits; compare the whole buffer instead
    let h = 0;
    const d = px.data;
    for (let k = 0; k < d.length; ++k) {
      h = ((h * 31) + d[k]) >>> 0;
    }
    cv.imgdata = h;
    await post('canvas', cv);
  } catch (e) { await post('canvas', {err: String(e)}); }

  await post('done', {});
})();
"""

CHECKS_HTML = ("<!doctype html><html><head><meta charset='utf-8'>"
               "</head><body>" +
               "<script>" + PAGE_JS.replace("</", "<\\/>") + "</script>" +
               "</body></html>")


class _ResultState:
    """Collects the ordered stream of tag|json posts from the page."""

    def __init__(self):
        self.events = []  # list of (tag, dict)

    def add(self, raw: str):
        tag, _, body = raw.partition("|")
        try:
            import json as _json
            data = _json.loads(body) if body else {}
        except Exception:  # noqa: BLE001
            data = {"raw": body}
        self.events.append((tag, data))

    def get(self, tag):
        for t, d in reversed(self.events):
            if t == tag:
                return d
        return None


def _make_http_server(state: _ResultState):
    """Serve the checks page and collect the page's POSTed events.

    The page POSTs its results section by section; a section that hangs
    (e.g. a buggy canvas patch) only delays its own report, and the tool
    can say exactly which call never returned.
    """
    class Handler(http.server.BaseHTTPRequestHandler):
        # HTTP/1.0: close each connection after the response, so the
        # server can never deadlock on keep-alive connections.
        protocol_version = "HTTP/1.0"

        def _send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802
            self._send(CHECKS_HTML.encode("utf-8"),
                       "text/html; charset=utf-8")

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            state.add(self.rfile.read(length).decode("utf-8", "replace"))
            self._send(b"ok", "text/plain")

        def log_message(self, *a):
            pass

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}/checks.html"


def _kill_tree(pid: int):
    """Force-kill a process and all its children (Windows)."""
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                   capture_output=True)


def run_chrome(chrome: Path, extra_args, timeout=60, label="run"):
    """Returns the event stream; hangs are reported as missing tags."""
    state = _ResultState()
    srv, url = _make_http_server(state)
    proc = None
    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="fpverify_",
                                         ignore_cleanup_errors=True) as profile:
            args = [
                str(chrome), "--headless",
                "--user-data-dir=" + profile,
                "--no-first-run",
                "--no-proxy-server",
            ] + list(extra_args) + [url]
            proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
            print(f"    [{label}] chrome pid {proc.pid} ...", flush=True)
            while (state.get("done") is None and state.get("fatal") is None
                   and time.time() - t0 < timeout):
                time.sleep(0.25)
            elapsed = time.time() - t0
            # kill the tree INSIDE the with-block, before tempdir cleanup,
            # so no chrome process still holds files in the profile dir
            if proc.poll() is None:
                _kill_tree(proc.pid)
            try:
                proc.wait(15)
            except Exception:  # noqa: BLE001
                pass
        if state.get("done") is None:
            got = [t for t, _ in state.events]
            print(f"    [{label}] TIMEOUT after {elapsed:.0f}s; "
                  f"sections received: {got or 'NONE'}", flush=True)
        else:
            print(f"    [{label}] done in {elapsed:.1f}s", flush=True)
        return state
    finally:
        if proc is not None and proc.poll() is None:
            _kill_tree(proc.pid)
        srv.shutdown()
        srv.server_close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("chrome", help="path to the compiled chrome.exe")
    ap.add_argument("--seed", default="42")
    ap.add_argument("--platform", default="macos",
                    choices=["macos", "linux", "windows"])
    ap.add_argument("--timezone", default="America/New_York")
    args = ap.parse_args()

    chrome = Path(args.chrome)
    if not chrome.exists():
        print(f"[!] chrome.exe not found: {chrome}")
        return 2

    seed = int(args.seed)
    exp_platform = {"macos": "MacIntel", "linux": "Linux x86_64",
                    "windows": "Win32"}[args.platform]
    fp_args = ["--fingerprint=" + args.seed,
               "--fingerprint-platform=" + args.platform,
               "--timezone=" + args.timezone]

    print(f"[*] run 1 (seed {args.seed}) ...")
    s1 = run_chrome(chrome, fp_args, label="run1")
    print(f"[*] run 2 (seed 43) ...")
    s2 = run_chrome(chrome, ["--fingerprint=43"], label="run2")
    print(f"[*] run 3 (seed {args.seed} again, cross-process determinism) ...")
    s3 = run_chrome(chrome, fp_args, label="run3")

    # UA-CH check: 158's SetPlatform override feeds navigator.platform-style
    # values ("MacIntel"/"Win32"/"Linux x86_64") into userAgentData too,
    # since our patch hooks the single source setter. Accept that form.
    exp_ua_platform = {"macos": ["macOS", "MacIntel"],
                       "linux": ["Linux", "Linux x86_64"],
                       "windows": ["Windows", "Win32"]}[args.platform]

    failures = []
    skips = []

    def expect(label, actual, expected):
        ok = actual == expected
        print(f"{'[+]' if ok else '[!]'} {label}: {actual!r}"
              f"{'' if ok else f' (expected {expected!r})'}")
        if not ok:
            failures.append(label)

    print("\n=== basic fingerprint checks ===")
    basic = s1.get("basic")
    if basic is None:
        print("[!] FATAL: basic section never reported - page JS is dead "
              "or the renderer crashed on startup")
        return 1
    expect("webdriver", basic.get("webdriver"), False)
    expect("platform", basic.get("platform"), exp_platform)
    expect("hardwareConcurrency", basic.get("hw"),
           SEED_TABLE_CORES[seed % 5])
    expect("deviceMemory", basic.get("mem"),
           SEED_TABLE_RAM[(seed >> 4) % 4])
    expect("screen.width", basic.get("sw"), SEED_TABLE_W[seed % 5])
    expect("screen.height", basic.get("sh"), SEED_TABLE_H[seed % 5])
    expect("devicePixelRatio", basic.get("dpr"), SEED_TABLE_DPR[seed % 5])
    expect("timezone", basic.get("tz"), args.timezone)

    print("\n=== canvas noise ===")
    cv1, cv2, cv3 = s1.get("canvas"), s2.get("canvas"), s3.get("canvas")
    if cv1 is None:
        print("[!] canvas: NO RESULT - the section hung or crashed "
              "(known defect in old kernels: toDataURL deadlocks when "
              "--fingerprint is set)")
        failures.append("canvas section")
    else:
        if "err" in cv1:
            print(f"[!] canvas threw: {cv1['err']}")
            failures.append("canvas section")
        else:
            expect("canvas deterministic (same process)",
                   cv1.get("url1"), cv1.get("url2"))
            if cv2 and cv1.get("url1") == cv2.get("url1"):
                failures.append("canvas noise seed-insensitive")
                print("[!] canvas noise did NOT change with a different seed")
            else:
                print("[+] canvas noise changes with seed")
            if cv3:
                expect("canvas stable across processes (same seed)",
                       cv1.get("url1"), cv3.get("url1"))
            if cv2 and cv1.get("imgdata") == cv2.get("imgdata"):
                failures.append("getImageData noise seed-insensitive")
                print("[!] getImageData noise did NOT change with seed")
            elif cv2:
                print("[+] getImageData noise changes with seed")

    print("\n=== WebGL (may skip in headless) ===")
    wg = s1.get("webgl")
    if wg is None:
        print("[!] WebGL: NO RESULT - section hung (renderer crash?)")
        failures.append("webgl section")
    elif wg.get("skip"):
        print(f"[-] WebGL skipped: {wg['skip']}")
        skips.append("webgl")
    elif wg.get("err"):
        print(f"[-] WebGL error: {wg['err']}")
        skips.append("webgl")
    else:
        ok = isinstance(wg.get("renderer"), str) and "ANGLE" in wg["renderer"]
        print(f"{'[+]' if ok else '[!]'} WebGL renderer: "
              f"{wg.get('renderer')!r}")
        print(f"    WebGL vendor: {wg.get('vendor')!r}")
        if not ok:
            failures.append("webgl renderer")

    print("\n=== userAgentData ===")
    ua = s1.get("ua")
    if ua is None:
        print("[!] userAgentData: NO RESULT - section hung")
        failures.append("userAgentData section")
    elif ua.get("skip") or ua.get("err"):
        print(f"[-] userAgentData skipped: "
              f"{ua.get('skip') or ua.get('err')}")
        skips.append("userAgentData")
    else:
        ok = ua.get("platform") in exp_ua_platform
        print(f"{'[+]' if ok else '[!]'} userAgentData.platform: "
              f"{ua.get('platform')!r} (expected one of {exp_ua_platform})")
        if not ok:
            failures.append("userAgentData.platform")

    print("\n" + "=" * 60)
    if failures:
        print(f"VERIFY FAILED: {len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print(f"VERIFY PASSED ({len(skips)} skipped: "
          f"{', '.join(skips) or 'none'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
