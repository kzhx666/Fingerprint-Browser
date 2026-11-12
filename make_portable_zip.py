# -*- coding: utf-8 -*-
"""一键便携版绿色打包脚本 (make_portable_zip.py)
用途:
  将定制 Chromium 内核 (精简去重)、KZManager 多开管理器、纯净便携配置、
  常用扩展与离线探针整合打包为一个立即可用的便携绿色 ZIP 压缩包。
"""

import os
import sys
import shutil
import zipfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
APP_SOURCE = Path(r"D:\KZBuild\App")
MANAGER_EXE = Path(r"D:\KZProfiles\KZManager.exe")
OUTPUT_DIR = Path(r"D:\KZDist")
STAGE_DIR = Path(r"C:\temp\KZBrowser-Portable")

# App 目录中需要排除的冗余文件 (精简瘦身)
EXCLUDE_APP_FILES = {
    "mini_installer.exe", # 131 MB 冗余安装包
    "debug.log",
}

EXCLUDE_APP_EXTENSIONS = {
    ".bak",
    ".tmp",
}

def clean_stage():
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR, ignore_errors=True)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)

def copy_kernel():
    print("[1/5] 正在精简打包定制 Chromium 内核 (App/)...")
    dest_app = STAGE_DIR / "App"
    dest_app.mkdir(parents=True, exist_ok=True)

    copied = 0
    total_bytes = 0

    for root, dirs, files in os.walk(APP_SOURCE):
        rel_root = Path(root).relative_to(APP_SOURCE)
        target_dir = dest_app / rel_root
        target_dir.mkdir(parents=True, exist_ok=True)

        for f in files:
            f_lower = f.lower()
            if f_lower in EXCLUDE_APP_FILES:
                continue
            if any(f_lower.endswith(ext) for ext in EXCLUDE_APP_EXTENSIONS):
                continue
            
            src_file = Path(root) / f
            dst_file = target_dir / f
            shutil.copy2(src_file, dst_file)
            copied += 1
            total_bytes += src_file.stat().st_size

    mb = total_bytes / 1024 / 1024
    print(f"  ✓ 内核复制完成: 共 {copied} 个文件, 约 {mb:.2f} MB (已剔除 mini_installer 等冗余)")

def copy_manager():
    print("[2/5] 正在复制 KZManager 控制台与核心支持库...")
    if not MANAGER_EXE.exists():
        raise FileNotFoundError(f"找不到管理器程序: {MANAGER_EXE}")
    shutil.copy2(MANAGER_EXE, STAGE_DIR / "KZManager.exe")
    print("  ✓ KZManager.exe 复制成功")

def setup_portable_structure():
    print("[3/5] 正在构建便携版目录与自适应初始配置...")
    
    # 建立便携版目录
    (STAGE_DIR / "_extensions").mkdir(exist_ok=True)
    (STAGE_DIR / "_proxies").mkdir(exist_ok=True)
    (STAGE_DIR / "_exports").mkdir(exist_ok=True)
    (STAGE_DIR / "_backup").mkdir(exist_ok=True)
    (STAGE_DIR / "_snapshots").mkdir(exist_ok=True)
    
    # 复制探针与工具
    for item in ["probe_full.html", "指纹自检.html", "node-proxy.py", "snapshot.py", "backup.py", "sync-data.py"]:
        src = REPO_ROOT / item
        if src.exists():
            shutil.copy2(src, STAGE_DIR / item)

    # 便携版默认 manager.json (相对路径优先)
    manager_cfg = {
        "chromeExe": "App\\chrome.exe",
        "defaultRows": 1,
        "order": [],
        "theme": "dark",
        "proxyPrecheck": True,
        "regionCheck": True,
        "regionCheckBlock": False,
        "nativeProxyAuth": True,
        "proxyRotation": False,
        "uaProfile": "default",
        "perInstanceDns": False,
        "globalKillSwitch": False
    }
    import json
    with open(STAGE_DIR / "manager.json", "w", encoding="utf-8") as f:
        json.dump(manager_cfg, f, indent=2, ensure_ascii=False)

    # 简要使用指南说明文件
    readme_txt = """========================================================================
  KZ 指纹浏览器便携绿色版 (Portable Edition)
========================================================================

【使用说明】
1. 本程序为完全绿色的便携版，解压到电脑任意目录即可直接使用，无需安装。
2. 双击运行目录下的「KZManager.exe」即可启动多开管理中心。
3. 内核自动绑定本目录 App\\chrome.exe，所有环境数据保存在当前目录，不写系统注册表。
4. 插上 U 盘即可随身携带，换电脑后登录态与 Cookie 均完整保留。

【内置核心能力】
- 91 项硬核底层内核防关联补丁 (Canvas/WebGL/WebAudio/WebRTC/ClientRects)
- 智能指纹生成、全域国内分流 (B站/百度/阿里/腾讯国内网站秒开，海外走代理)
- 代理管理中心、多窗口操作同步器、窗口网格平铺器、完整本地自动化 API

技术支持与源码: https://github.com/kzhx666/Fingerprint-Browser
========================================================================
"""
    with open(STAGE_DIR / "使用说明.txt", "w", encoding="gbk", errors="replace") as f:
        f.write(readme_txt)
    print("  ✓ 便携版结构初始化完毕")

def compress_zip():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d")
    zip_name = f"KZBrowser-Portable-v1.0-{stamp}.zip"
    zip_path = OUTPUT_DIR / zip_name

    print(f"[4/5] 正在生成便携压缩包: {zip_path} (请稍候)...")
    
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for root, dirs, files in os.walk(STAGE_DIR):
            for f in files:
                full_path = Path(root) / f
                rel_path = full_path.relative_to(STAGE_DIR.parent)
                zf.write(full_path, arcname=str(rel_path))

    zip_mb = zip_path.stat().st_size / 1024 / 1024
    print(f"  ✓ 打包成功！便携压缩包大小: {zip_mb:.2f} MB")
    return zip_path

def main():
    print("========================================================================")
    print("  KZ 指纹浏览器一键便携版绿色打包流程")
    print("========================================================================")
    clean_stage()
    copy_kernel()
    copy_manager()
    setup_portable_structure()
    zip_path = compress_zip()
    print("========================================================================")
    print(f"[5/5] 便携版制作完成！产物路径:\n  -> {zip_path}")
    print("========================================================================")

if __name__ == "__main__":
    main()
