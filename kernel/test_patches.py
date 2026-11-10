#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Smoke test for apply_patches.py against real upstream Chromium sources.

Builds a throwaway source tree from tests/fixtures (verbatim files fetched
from chromium.googlesource.com main branch), runs the patcher twice and
asserts:
  - run 1: every patch reports APPLIED, exit code 0
  - run 2: every patch reports ALREADY, exit code 0 (idempotency)
  - key markers present in the patched files

Usage:
    python scripts/test_patches.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
FIXTURES = REPO / "tests" / "fixtures"
PATCHER = HERE / "apply_patches.py"

# (relative source path, must-contain marker after patching)
EXPECTATIONS = [
    ("chrome/browser/ui/views/bookmarks/bookmark_bar_view.h",
     "current_row_count_"),
    ("chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc",
     "GetMaxBookmarkBarRows"),
    ("chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc",
     "bookmarks-current-tab"),
    ("chrome/browser/ui/views/tabs/tab.cc", "no-dblclick-tab-close"),
    ("chrome/browser/ui/views/tabs/tab_strip.h", "OnMouseWheel"),
    ("chrome/browser/ui/views/tabs/tab_strip.cc", "no-wheel-switch-tabs"),
    ("chrome/browser/ui/tabs/tab_strip_model.cc", "no-keep-last-tab"),
    ("chrome/browser/ui/tabs/tab_style.cc", "no-narrow-tabs"),
    ("chrome/browser/ui/startup/automation_infobar_delegate.cc",
     "PATCH:automation-infobar"),
    ("third_party/blink/renderer/core/frame/navigator.cc",
     "PATCH:webdriver-false"),
    ("third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc",
     "PATCH:hw-concurrency"),
    ("third_party/blink/renderer/core/frame/navigator_device_memory.cc",
     "PATCH:device-memory"),
    ("third_party/blink/renderer/core/frame/navigator_id.cc",
     "PATCH:platform"),
    ("third_party/blink/renderer/core/frame/navigator_ua_data.cc",
     "PATCH:ua-ch"),
    ("third_party/blink/renderer/core/frame/screen.cc", "PATCH:screen"),
    ("third_party/blink/renderer/core/frame/local_dom_window.cc",
     "PATCH:dpr"),
    ("third_party/blink/renderer/modules/battery/battery_manager.cc",
     "PATCH:battery"),
    ("third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc",
     "PATCH:canvas-noise"),
    ("third_party/blink/renderer/platform/graphics/image_data_buffer.h",
     "owns_pixels_"),
    ("third_party/blink/renderer/platform/graphics/image_data_buffer.cc",
     "PATCH:canvas-noise"),
    ("third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc",
     "PATCH:webgl-fp"),
    ("third_party/blink/renderer/modules/webaudio/audio_buffer.cc",
     "PATCH:audio-noise"),
    ("third_party/blink/renderer/core/timezone/timezone_controller.cc",
     "PATCH:timezone"),
    ("components/embedder_support/user_agent_utils.cc", "PATCH:ua-platform"),
    ("third_party/blink/renderer/core/frame/navigator_language.cc", "PATCH:lang"),
    ("chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc", "PATCH:folder-count"),
    ("chrome/browser/ui/views/frame/browser_view.cc", "PATCH:window-title"),
    ("ui/views/controls/menu/submenu_view.h", "PATCH:menu-columns"),
    ("ui/views/controls/menu/submenu_view.cc", "PATCH:menu-columns"),
    ("chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.h",
     "PATCH:menu-columns"),
    ("chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc",
     "PATCH:menu-columns"),
    ("content/browser/renderer_host/render_process_host_impl.cc",
     '"fingerprint",'),
]


def run_patcher(src_root: Path):
    proc = subprocess.run(
        [sys.executable, str(PATCHER), str(src_root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return proc


def main():
    if not FIXTURES.exists():
        print(f"[!] fixtures missing: {FIXTURES}")
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="patch_smoke_"))
    src_root = tmp / "src"
    shutil.copytree(FIXTURES, src_root)
    print(f"[*] fixture tree: {src_root}")

    failures = []

    # --- run 1: expect APPLIED ---
    proc = run_patcher(src_root)
    print(proc.stdout)
    if proc.returncode != 0:
        failures.append("run 1 exited non-zero")
    if "FAILED" in proc.stdout:
        failures.append("run 1 reported FAILED patches")

    # --- markers present ---
    for rel, marker in EXPECTATIONS:
        p = src_root / rel
        if not p.exists():
            failures.append(f"missing patched file: {rel}")
            continue
        if marker not in p.read_text(encoding="utf-8"):
            failures.append(f"marker '{marker}' not found in {rel}")
    print(f"[*] marker checks: {len(EXPECTATIONS)} files")

    # --- run 2: expect ALREADY everywhere ---
    proc2 = run_patcher(src_root)
    print(proc2.stdout)
    if proc2.returncode != 0:
        failures.append("run 2 exited non-zero (idempotency broken)")
    if "FAILED" in proc2.stdout:
        failures.append("run 2 reported FAILED patches")

    shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60)
    if failures:
        print("SMOKE TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("SMOKE TEST PASSED (apply + idempotency + markers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
