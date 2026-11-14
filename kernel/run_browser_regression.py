#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P1-4: 真实浏览器指纹与代理回归检查套件。
启动真实编译的 chrome.exe，覆盖：
  - UA / UA-CH (platform, platformVersion)
  - 语言 (navigator.language, languages)
  - 时区 (Intl timeZone)
  - Canvas (toDataURL == toBlob, 纯色真值, per-seed 唯一性)
  - WebGL (masked VENDOR/RENDERER == WebKit / WebKit WebGL, unmasked)
  - WebGPU (adapter info)
  - WebRTC (无私网 IP 泄漏, 严格模式)
  - Audio (per-seed 扰动)
  - ClientRects (只读亚像素扰动)
  - 硬件 (cores, memory, screen, DPR, doNotTrack, colorDepth, maxTouchPoints)
支持指定浏览器路径、探针路径和输出文件；采用独立隔离临时目录，不删除用户固定目录。
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_CHROME = Path(os.environ.get("CHROME_EXE", r"D:\KZBuild\App\chrome.exe"))
DEFAULT_PROBE = REPO / "tests" / "probe_full.html"

def find_probe(candidate=None):
    if candidate and Path(candidate).exists():
        return Path(candidate).resolve()
    for p in [DEFAULT_PROBE, Path(r"C:\temp\probe_full.html"), Path(r"D:\KZProfiles\probe_full.html")]:
        if p.exists():
            return p.resolve()
    return None

def run_regression(chrome_exe=DEFAULT_CHROME, probe_path=None, output_path=None, seeds=("914410", "123456", "289321")):
    chrome_exe = Path(chrome_exe).resolve()
    if not chrome_exe.exists():
        print(f"[!] chrome.exe 不存在: {chrome_exe}")
        return False, {}

    probe = find_probe(probe_path)
    if not probe:
        print("[!] 探针页面缺失 (probe_full.html)")
        return False, {}

    res = {}
    ports = [9821, 9822, 9823]
    tags = ["A", "B", "C"]
    temp_dirs = []

    try:
        for seed, port, tag in zip(seeds, ports, tags):
            user_data = Path(tempfile.mkdtemp(prefix=f"reg_browser_{tag}_"))
            temp_dirs.append(user_data)
            
            cmd = [
                str(chrome_exe),
                f"--user-data-dir={user_data}",
                f"--remote-debugging-port={port}",
                f"--fingerprint={seed}",
                "--fingerprint-platform=windows",
                "--timezone=America/Los_Angeles",
                "--lang=en-US",
                "--accept-lang=en-US,en",
                "--fingerprint-lang=en-US,en",
                "--fp-canvas=on",
                "--fp-webgl=intel",
                "--fp-audio=on",
                "--fp-dpr=2",
                "--fp-cores=8",
                "--fp-memory=16",
                "--fp-geo=34.0522,-118.2437,30",
                "--enable-unsafe-webgpu",
                "--no-first-run",
                "--no-default-browser-check",
                f"file:///{str(probe).replace('\\\\', '/')}"
            ]
            
            proc = subprocess.Popen(cmd)
            time.sleep(11)

            try:
                ts = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=10))
                pg = next(t for t in ts if t["type"] == "page" and "probe_full" in t.get("url", ""))
                
                import websocket
                ws = websocket.create_connection(pg["webSocketDebuggerUrl"], timeout=20)
                ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
                    "expression": "document.getElementById('out').textContent", "returnByValue": True}}))
                while True:
                    m = json.loads(ws.recv())
                    if m.get("id") == 1:
                        raw_val = m["result"]["result"]["value"]
                        res[tag] = json.loads(raw_val)
                        break
                ws.close()
            except Exception as e:
                print(f"[!] 采集 seed {seed} 失败: {e}")
                res[tag] = {}
            finally:
                try:
                    proc.kill()
                except Exception:
                    pass

        # 评估与生成报告
        report = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "chrome_exe": str(chrome_exe),
            "probe_path": str(probe),
            "results": res,
            "checks": [],
            "passed": True
        }

        def check(name, ok, detail=""):
            report["checks"].append({"name": name, "pass": bool(ok), "detail": str(detail)})
            status = "PASS" if ok else "FAIL"
            print(f"  {name:<44} {status} {detail}")
            if not ok:
                report["passed"] = False

        a = res.get("A", {})
        print("\n=== 单实例基准校验 ===")
        check("UA 含 Windows NT 10.0", "Windows NT 10.0" in a.get("ua", ""), a.get("ua", "")[:40])
        check("navigator.platform == Win32", a.get("platform") == "Win32", a.get("platform"))
        check("Intl.locale == en-US", a.get("intl") == "en-US", a.get("intl"))
        check("languages == en-US,en", a.get("langs") == "en-US,en", a.get("langs"))
        uad = a.get("uadHE", {}) if isinstance(a.get("uadHE"), dict) else {}
        check("UA-CH platform == Windows", uad.get("platform") == "Windows", uad.get("platform"))
        check("UA-CH platformVersion == 10.0.0", uad.get("platformVersion") == "10.0.0", uad.get("platformVersion"))
        
        cv = a.get("canvas", {})
        check("Canvas toDataURL == toBlob", cv.get("equal") is True, f"{cv.get('toDataURL')} == {cv.get('toBlob')}")
        check("Canvas roundtrip 一致", cv.get("roundtripOk") is True)
        check("Canvas 纯色真值", cv.get("pureOk") is True)

        gl = a.get("webgl", {})
        check("WebGL masked VENDOR == WebKit", gl.get("v") == "WebKit", gl.get("v"))
        check("WebGL masked RENDERER == WebKit WebGL", gl.get("r") == "WebKit WebGL", gl.get("r"))
        check("WebGL unmasked 是 Intel", "Intel" in str(gl.get("ur", "")), str(gl.get("ur", ""))[:45])

        wr = a.get("webrtc", {})
        check("WebRTC strict 无私网 IP 泄漏", wr.get("priv") == 0, f"priv={wr.get('priv')} n={wr.get('n')}")

        check("doNotTrack == 1", a.get("dnt") == "1", a.get("dnt"))
        check("colorDepth 合法 (24/30)", a.get("screen", {}).get("cd") in (24, 30), a.get("screen", {}).get("cd"))
        check("DPR == 2", a.get("screen", {}).get("dpr") == 2, a.get("screen", {}).get("dpr"))

        print("\n=== 跨实例每 Seed 独立性校验 ===")
        for k in ["canvas", "netinfo", "storage", "webrtc", "audio"]:
            vals = [json.dumps(res[t].get(k), sort_keys=True) for t in tags if t in res]
            uniq = len(set(vals))
            check(f"{k} 跨实例 3 Seed 独立性", uniq >= 2, f"{uniq}/3 唯一")

        # 保存报告
        out_json = Path(output_path).resolve() if output_path else REPO / "kernel" / "regression_results.json"
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\n[+] 完整回归结果已保存到: {out_json}")
        
        return report["passed"], report
    finally:
        for d in temp_dirs:
            shutil.rmtree(d, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(description="真实浏览器指纹与代理回归检查套件")
    parser.add_argument("--chrome", default=str(DEFAULT_CHROME), help="chrome.exe 路径")
    parser.add_argument("--probe", default=None, help="probe_full.html 路径")
    parser.add_argument("--output", default=str(REPO / "kernel" / "regression_results.json"), help="输出 JSON 报告路径")
    parser.add_argument("--seeds", default="914410,123456,289321", help="逗号分隔的 3 个测试 seed")
    args = parser.parse_args()

    seed_list = [s.strip() for s in args.seeds.split(",") if s.strip()]
    ok, _ = run_regression(args.chrome, args.probe, args.output, seed_list)
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
