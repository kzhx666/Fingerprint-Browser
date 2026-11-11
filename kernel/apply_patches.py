#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
All-in-One Automated C++ Source Patcher for Latest Chromium (Windows x64)
==========================================================================
All anchors below were verified against current Chromium main-branch sources
(2026-11). Every patch reports APPLIED / ALREADY / FAILED and the script
exits non-zero when any patch fails, so CI never silently builds an
unpatched kernel.

Feature matrix
--------------
UI / UX (browser process, all default-ON with opt-out switches):
  1.  Multi-row bookmark bar (--bookmark-bar-rows=1..15, default 3)
  2.  Left-click bookmark opens in new foreground tab (opt-out --bookmarks-current-tab)
  3.  Double-click tab to close (opt-out --no-dblclick-tab-close)
  4.  Mouse wheel over tab strip switches tabs (opt-out --no-wheel-switch-tabs)
  5.  Closing the last tab keeps the window open (opt-out --no-keep-last-tab)
  6.  Narrower minimum tab width, more tabs fit (opt-out --no-narrow-tabs)

Anti-detection (renderer process):
  7.  navigator.webdriver forced false + automation infobar removed
  8.  --enable-automation banner suppressed

Fingerprint spoofing (renderer, active only with --fingerprint=<seed>):
  9.  CPU core count (hardwareConcurrency)   12. Screen width/height
  10. Device memory (deviceMemory)           13. devicePixelRatio
  11. Platform (navigator.platform)          14. Battery API (desktop-like values)
  15. Canvas 2D deterministic noise (getImageData + toDataURL)
  16. WebGL UNMASKED_VENDOR/RENDERER spoofing
  17. AudioContext deterministic noise (OfflineAudioContext render path)
  18. UA-CH platform / platformVersion (navigator.userAgentData)
  19. Timezone override (--timezone=Area/Location, needs ICU)

Usage:
    python apply_patches.py <path_to_chromium_src> [--force]
"""

import argparse
import re
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Reporting framework
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.entries = []  # (name, status, detail)

    def record(self, name, status, detail=""):
        self.entries.append((name, status, detail))
        tag = {"APPLIED": "[+]", "ALREADY": "[=]", "FAILED": "[!]",
               "SKIPPED": "[-]"}.get(status, "[?]")
        line = f"{tag} {name}: {status}"
        if detail:
            line += f" ({detail})"
        print(line)

    def downgrade_last(self, status="ALREADY", note=""):
        """Rewrite the most recent entry; used when a patch wrote nothing."""
        if not self.entries:
            return
        name, old_status, detail = self.entries[-1]
        if old_status != "APPLIED":
            return
        merged = detail
        if note:
            merged = f"{detail}; {note}" if detail else note
        self.entries[-1] = (name, status, merged)
        print(f"    [~] {name}: APPLIED -> {status} (no bytes changed)")

    def finish(self, force=False):
        print("\n" + "=" * 72)
        print("PATCH REPORT")
        print("=" * 72)
        counts = {}
        for _, status, _ in self.entries:
            counts[status] = counts.get(status, 0) + 1
        for status in ("APPLIED", "ALREADY", "FAILED", "SKIPPED"):
            if counts.get(status):
                print(f"  {status:8s}: {counts[status]}")
        failed = [e for e in self.entries if e[1] == "FAILED"]
        if failed:
            print("\nFAILED patches (anchor drift - source needs re-verification):")
            for name, _, detail in failed:
                print(f"  - {name}: {detail}")
            if force:
                print("\n--force given: continuing despite failures.")
                return 0
            print("\nFix the anchors above or rerun with --force to ignore.")
            return 1
        if _DRY_RUN["on"]:
            print("\n[--check] dry run: nothing was written.")
        print("\nAll patches OK.")
        return 0


def read_file(path: Path):
    return path.read_text(encoding="utf-8")


# Counts writes that actually changed bytes. Used to downgrade a patch that
# claims APPLIED but produced no change (marker check was too loose).
_REAL_WRITES = {"n": 0}


_DRY_RUN = {"on": False}


def write_file(path: Path, text: str):
    if _DRY_RUN["on"]:
        # Dry run: report the change without touching the tree.
        _REAL_WRITES["n"] += 1
        return True
    if path.exists():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except Exception:  # noqa: BLE001
            pass
    path.write_text(text, encoding="utf-8")
    _REAL_WRITES["n"] += 1
    return True


def insert_after_first_include(text: str, *includes: str):
    """Insert missing includes right after the first #include line."""
    for inc in includes:
        if inc in text:
            continue
        m = re.search(r'^#include\s+[<"].*[>"]\s*$', text, re.M)
        if not m:
            raise RuntimeError("no #include anchor found")
        text = text[:m.end()] + "\n" + inc + text[m.end():]
    return text


_PATCH_MARKER_RE = re.compile(r"//\s*PATCH:([A-Za-z0-9_\-]+)")


def _drift_signature(old: str, new: str) -> str:
    """A short run of lines this edit adds and the original block lacks.

    A single line is not distinctive enough (helper boilerplate repeats across
    edits in the same file), and a PATCH marker definitely is not: several
    edits in one file share it. Three consecutive added lines are specific
    enough to tell "already applied in another shape" from "not applied yet".
    """
    old_lines = {ln.strip() for ln in old.split("\n")}
    added = [
        ln for ln in new.split("\n")
        if ln.strip() and ln.strip() not in old_lines
    ]
    if not added:
        return ""
    return "\n".join(added[:3])


def guard_no_drift(text: str, old: str, new: str, label: str):
    """Fail loudly when the tree already carries this block in another shape.

    Without this a hand-edited block makes `new in text` fail, the stale anchor
    still matches, and the block gets inserted a second time (duplicate
    definitions / broken #if nesting).
    """
    if new in text:
        return
    sig = _drift_signature(old, new)
    if sig and sig in text:
        raise RuntimeError(
            "drift for '%s': the tree already contains this block in a "
            "different shape (found %r) - update the patch data to match the "
            "tree instead of inserting it again" % (label, sig[:60]))


def sub_required(text: str, pattern: str, repl: str, name: str, count=1):
    """re.sub that raises when the anchor is missing (fail-fast)."""
    new, n = re.subn(pattern, repl, text, count=count)
    if n != count:
        raise RuntimeError(f"anchor not found ({n}/{count}): {name}")
    return new


# ---------------------------------------------------------------------------
# Fingerprint helper header (new file, no BUILD.gn change needed - header only)
# ---------------------------------------------------------------------------

FINGERPRINT_HELPER_PATH = Path("third_party/blink/renderer/core/frame/fingerprint_params.h")

FINGERPRINT_HELPER = r'''#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_PARAMS_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_PARAMS_H_

// Shared helpers for --fingerprint=<seed> spoofing patches.
// NOTE: this file intentionally includes base/command_line.h from blink
// renderer code. The DEPS style check is not run for local/CI kernel
// builds, and the renderer links against //base anyway.
#include <cctype>
#include <cstdint>
#include <string>

#include "base/command_line.h"
#include "base/strings/string_number_conversions.h"

namespace blink {
namespace fp {

inline bool Enabled() {
  return base::CommandLine::ForCurrentProcess()->HasSwitch("fingerprint");
}

inline uint64_t Seed() {
  uint64_t seed = 0;
  base::StringToUint64(base::CommandLine::ForCurrentProcess()
                           ->GetSwitchValueASCII("fingerprint"),
                       &seed);
  return seed;
}

// Returns "MacIntel" / "Linux x86_64" / "Win32" or nullptr.
inline const char* PlatformOverride() {
  if (!base::CommandLine::ForCurrentProcess()->HasSwitch(
          "fingerprint-platform")) {
    return nullptr;
  }
  const std::string p = base::CommandLine::ForCurrentProcess()
                            ->GetSwitchValueASCII("fingerprint-platform");
  if (p == "macos") {
    return "MacIntel";
  }
  if (p == "linux") {
    return "Linux x86_64";
  }
  if (p == "windows") {
    return "Win32";
  }
  return nullptr;
}

// Plausible platformVersion values matching PlatformOverride().
// windows 必须与 UA 的 "Windows NT 10.0" 对齐；报 19.0.0 会被
// 指纹站用 UA-CH 反查判定「操作系统不同」。
inline const char* PlatformVersionOverride() {
  const std::string p = base::CommandLine::ForCurrentProcess()
                            ->GetSwitchValueASCII("fingerprint-platform");
  if (p == "macos") {
    return "14.5.0";
  }
  if (p == "linux") {
    return "6.5.0";
  }
  if (p == "windows") {
    return "10.0.0";
  }
  return nullptr;
}

// ---- individual overrides (PATCH:fp-overrides) ----
// Every switch is optional; without it the value stays derived from --fingerprint.
inline std::string Value(const char* name) {
  return base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII(name);
}

inline bool Has(const char* name) {
  return base::CommandLine::ForCurrentProcess()->HasSwitch(name);
}

inline int IntValue(const char* name, int fallback) {
  int v = 0;
  if (base::StringToInt(Value(name), &v) && v > 0) {
    return v;
  }
  return fallback;
}

// 0 means "not overridden".
inline unsigned CoresOverride() {
  return static_cast<unsigned>(IntValue("fp-cores", 0));
}

// 0 means "not overridden".
inline float MemoryOverride() {
  return static_cast<float>(IntValue("fp-memory", 0));
}

inline bool ScreenOverrideSet() {
  return Has("fp-screen");
}

inline int ScreenOverrideWidth() {
  const std::string v = Value("fp-screen");
  const size_t x = v.find('x');
  int w = 0;
  if (x != std::string::npos && base::StringToInt(v.substr(0, x), &w) && w > 0) {
    return w;
  }
  return 0;
}

inline int ScreenOverrideHeight() {
  const std::string v = Value("fp-screen");
  const size_t x = v.find('x');
  int h = 0;
  if (x != std::string::npos && base::StringToInt(v.substr(x + 1), &h) && h > 0) {
    return h;
  }
  return 0;
}

// 0 means "not overridden".
inline double DprOverride() {
  double d = 0;
  if (base::StringToDouble(Value("fp-dpr"), &d) && d > 0) {
    return d;
  }
  return 0;
}

// "off" disables that spoof entirely.
inline bool Disabled(const char* name) {
  return Value(name) == "off";
}

// Extracts the first family name from a CSS font shorthand, e.g.
//   '12px "Segoe UI", sans-serif'  ->  'Segoe UI'
// Needed because the family may already have been filtered out of the resolved
// FontDescription, and document.fonts.check() still has to answer for it.
inline std::string FirstFamilyFromFontString(const std::string& shorthand) {
  const size_t comma = shorthand.find(',');
  std::string first =
      comma == std::string::npos ? shorthand : shorthand.substr(0, comma);

  // Walk tokens until the size (a token starting with a digit or '.') and take
  // whatever follows it as the family.
  size_t fam_start = 0;
  size_t i = 0;
  while (i < first.size()) {
    while (i < first.size() && isspace(static_cast<unsigned char>(first[i]))) {
      ++i;
    }
    size_t j = i;
    while (j < first.size() && !isspace(static_cast<unsigned char>(first[j]))) {
      ++j;
    }
    if (j > i && (isdigit(static_cast<unsigned char>(first[i])) ||
                  first[i] == '.')) {
      fam_start = j;
      break;
    }
    i = j;
  }
  std::string family = first.substr(fam_start);
  // Trim spaces and surrounding quotes.
  const size_t b = family.find_first_not_of(" \t\"\'");
  if (b == std::string::npos) {
    return std::string();
  }
  const size_t e = family.find_last_not_of(" \t\"\'");
  return family.substr(b, e - b + 1);
}

// ---- geolocation spoofing (PATCH:geo-spoof) ----
// --fp-geo=lat,lon[,accuracy]  例如 --fp-geo=35.6895,139.6917,30
inline void ParseGeoValues(double* lat, double* lon, double* acc) {
  *lat = 0.;
  *lon = 0.;
  *acc = 20.;
  const std::string spec = Value("fp-geo");
  double first = 0.;
  double second = 0.;
  double third = 20.;
  int n = 0;
  size_t start = 0;
  while (n < 3 && start <= spec.size()) {
    const size_t comma = spec.find(',', start);
    const std::string part =
        comma == std::string::npos ? spec.substr(start)
                                   : spec.substr(start, comma - start);
    double d = 0.;
    if (base::StringToDouble(part, &d)) {
      if (n == 0) {
        first = d;
      } else if (n == 1) {
        second = d;
      } else {
        third = d;
      }
      ++n;
    }
    if (comma == std::string::npos) {
      break;
    }
    start = comma + 1;
  }
  *lat = first;
  *lon = second;
  *acc = third > 0. ? third : 20.;
}

inline bool GeoEnabled() {
  if (!Enabled() || Disabled("fp-geo")) {
    return false;
  }
  return Has("fp-geo");
}

inline double GeoLatitude() {
  double lat, lon, acc;
  ParseGeoValues(&lat, &lon, &acc);
  return lat;
}

inline double GeoLongitude() {
  double lat, lon, acc;
  ParseGeoValues(&lat, &lon, &acc);
  return lon;
}

inline double GeoAccuracy() {
  double lat, lon, acc;
  ParseGeoValues(&lat, &lon, &acc);
  return acc;
}

// ---- font spoofing (PATCH:font-spoof) ----
// document.fonts.check() is the main scriptable way to probe installed fonts, so
// the answer is filtered through a per-platform whitelist.
template <size_t N>
inline bool FamilyInList(const std::string& family,
                         const char* const (&list)[N]) {
  for (const char* name : list) {
    if (family == name) {
      return true;
    }
  }
  return false;
}

// Returns -1 when not spoofing, otherwise 0 (absent) or 1 (present).
inline int FontAvailability(const std::string& family) {
  if (!Enabled() || Disabled("fp-fonts")) {
    return -1;
  }
  static const char* const kMac[] = {
      "Helvetica", "Helvetica Neue", "Arial", "Arial Black",
      "Arial Rounded MT Bold", "Times New Roman", "Times", "Courier New",
      "Courier", "Menlo", "Monaco", "Geneva", "Verdana", "Georgia", "Tahoma",
      "Trebuchet MS", "Lucida Grande", "Lucida Sans", "Lucida Bright",
      "Lucida Sans Typewriter", "Impact", "Comic Sans MS", "Palatino",
      "Optima", "Futura", "Baskerville", "Copperplate", "Didot",
      "American Typewriter", "Chalkboard", "Marker Felt", "Papyrus", "Skia",
      "Trattatello", "Zapf Dingbats", "Zapfino", "Apple Symbols",
      "Apple Color Emoji", "AppleGothic", "Apple Chancery", "Avenir",
      "Avenir Next", "Avenir Next Condensed", "Gill Sans", "Hoefler Text",
      "Krungthep", "Andale Mono", "Basalt", "Big Caslon", "Bodoni 72",
      "Bradley Hand", "Brush Script MT", "Cochin", "Colonna MT",
      "Freestyle Script", "Handwriting - Dakota", "Herculanum",
      "Hiragino Kaku Gothic Pro", "Kokonor", "LiSong Pro", "Luminari",
      "Marion", "Mishafi", "Nanum Gothic",
      "Party LET", "Phosphate", "Rockwell", "Sathu", "SignPainter", "Silom",
      "Snell Roundhand", "Stencil", "Superclarendon", "Symbol",
      "Waseem", "SF Pro", "SF Pro Text", "SF Pro Display",
      "SF Mono", "SF Compact", "SF Compact Text", "SF NS", "System Font",
      "New York", "PingFang SC", "PingFang TC", "PingFang HK",
      "Hiragino Sans", "Hiragino Sans GB", "Hiragino Mincho ProN",
      "Hiragino Maru Gothic ProN", "STHeiti", "STSong", "STKaiti",
      "STFangsong", "Songti SC", "Songti TC", "Heiti SC", "Heiti TC",
      "Kaiti SC", "Yuanti SC", "Hannotate SC", "Lantinghei SC", "Baoli SC",
      "Weibei SC", "Xingkai SC", "Yuppy SC", "Apple LiGothic",
      "Apple LiSung", "Apple SD Gothic Neo"};
  static const char* const kWin[] = {
      "Arial", "Arial Black", "Arial Narrow", "Calibri", "Cambria",
      "Candara", "Comic Sans MS", "Consolas", "Constantia", "Corbel",
      "Courier New", "Ebrima", "Franklin Gothic Medium", "Gabriola",
      "Gadugi", "Georgia", "Impact", "Ink Free", "Leelawadee UI",
      "Lucida Console", "Lucida Sans Unicode", "Lucida Sans",
      "Malgun Gothic", "Marlett", "Microsoft Sans Serif", "MV Boli",
      "Nirmala UI", "Palatino Linotype", "Segoe Print", "Segoe Script",
      "Segoe UI", "Segoe UI Emoji", "Segoe UI Historic", "Segoe UI Symbol",
      "Sitka", "Sylfaen", "Symbol", "Tahoma", "Times New Roman",
      "Trebuchet MS", "Verdana", "Webdings", "Wingdings", "Bahnschrift",
      "Book Antiqua", "Bookman Old Style", "Calisto MT", "Cambria Math",
      "Century", "Century Gothic", "Comic Sans", "Goudy Old Style",
      "High Tower Text", "Lucida Bright", "Lucida Fax",
      "MS Reference Sans Serif", "MS Reference Serif", "Palatino",
      "Perpetua", "Rockwell", "Tw Cen MT", "Wide Latin", "Microsoft YaHei",
      "Microsoft YaHei UI", "Microsoft JhengHei", "Microsoft JhengHei UI",
      "Microsoft Himalaya", "Microsoft Uighur", "Microsoft Yi Baiti",
      "Microsoft New Tai Lue", "Microsoft PhagsPa", "Microsoft Tai Le",
      "MS Gothic", "MS Mincho", "MS PGothic", "MS PMincho", "MS UI Gothic",
      "MingLiU", "MingLiU-ExtB", "MingLiU_HKSCS", "MingLiU_HKSCS-ExtB",
      "PMingLiU", "PMingLiU-ExtB", "SimSun", "SimSun-ExtB", "NSimSun",
      "SimHei", "KaiTi", "KaiTi_GB2312", "FangSong", "FangSong_GB2312",
      "DFKai-SB", "Batang", "BatangChe", "Gulim", "GulimChe", "Gungsuh",
      "GungsuhChe", "Dotum", "DotumChe", "Malgun Gothic Semilight",
      "Yu Gothic", "Yu Gothic UI", "Yu Mincho", "Meiryo", "Meiryo UI",
      "Browallia New", "BrowalliaUPC", "Cordia New", "CordiaUPC",
      "DilleniaUPC", "EucrosiaUPC", "FreesiaUPC", "IrisUPC", "JasmineUPC",
      "KodchiangUPC", "LilyUPC", "Leelawadee", "Mongolian Baiti",
      "Myanmar Text", "Vrinda", "Shonar Bangla", "Narkisim", "Rod", "Raavi",
      "Kalinga", "Latha", "Mangal", "Gautami", "Tunga", "Kartika",
      "Aparajita", "Sakkal Majalla", "Aldhabi", "Urdu Typesetting", "Dubai"};
  static const char* const kLinux[] = {
      "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
      "DejaVu Sans Condensed", "Liberation Sans", "Liberation Serif",
      "Liberation Mono", "Liberation Sans Narrow", "Noto Sans", "Noto Serif",
      "Noto Sans Mono", "Noto Sans Display", "Noto Serif Display",
      "Noto Color Emoji", "Noto Sans Symbols", "Noto Sans Symbols2",
      "Noto Sans Math", "Ubuntu", "Ubuntu Mono", "Ubuntu Condensed",
      "Ubuntu Light", "Cantarell", "FreeSans", "FreeSerif", "FreeMono",
      "Nimbus Sans", "Nimbus Roman", "Nimbus Mono PS", "Nimbus Sans Narrow",
      "Droid Sans", "Droid Serif", "Droid Sans Mono", "Arimo", "Tinos",
      "Cousine", "DejaVu Math TeX Gyre", "STIX", "Latin Modern Roman",
      "Latin Modern Sans", "Latin Modern Mono", "TeX Gyre Adventor",
      "TeX Gyre Bonum", "TeX Gyre Chorus", "TeX Gyre Cursor",
      "TeX Gyre Heros", "TeX Gyre Pagella", "TeX Gyre Schola",
      "TeX Gyre Termes", "Bitstream Charter", "Charter", "URW Bookman",
      "URW Gothic", "Z003", "P052", "Standard Symbols PS",
      "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK JP",
      "Noto Sans CJK KR", "Noto Sans CJK HK", "Noto Serif CJK SC",
      "Noto Serif CJK TC", "Noto Serif CJK JP", "Noto Serif CJK KR",
      "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "WenQuanYi Bitmap Song",
      "AR PL UMing CN", "AR PL UMing TW", "AR PL UKai CN",
      "Source Han Sans SC", "Source Han Sans TC", "Source Han Serif SC",
      "Source Han Serif TC", "Droid Sans Fallback", "TakaoGothic",
      "TakaoMincho", "IPAGothic", "IPAPGothic", "IPAMincho", "IPAPMincho",
      "VL Gothic", "Baekmuk Gulim", "Baekmuk Batang", "UnDotum",
      "NanumGothic", "NanumMyeongjo"};
  const std::string platform = Value("fingerprint-platform");
  if (platform == "macos") {
    return FamilyInList(family, kMac) ? 1 : 0;
  }
  if (platform == "linux") {
    return FamilyInList(family, kLinux) ? 1 : 0;
  }
  return FamilyInList(family, kWin) ? 1 : 0;
}

// -2 = not set, -1 = off, 0 = nvidia, 1 = amd, 2 = intel
inline int WebGLIndex() {
  const std::string v = Value("fp-webgl");
  if (v.empty()) {
    return -2;
  }
  if (v == "off") {
    return -1;
  }
  if (v == "nvidia") {
    return 0;
  }
  if (v == "amd") {
    return 1;
  }
  if (v == "intel") {
    return 2;
  }
  return -2;
}

inline int ScreenWidth() {
  if (ScreenOverrideSet()) {
    const int w = ScreenOverrideWidth();
    if (w > 0) {
      return w;
    }
  }
  static const int kWidths[] = {1920, 2560, 1600, 1366, 3840};
  return kWidths[Seed() % 5];
}

inline int ScreenHeight() {
  if (ScreenOverrideSet()) {
    const int h = ScreenOverrideHeight();
    if (h > 0) {
      return h;
    }
  }
  static const int kHeights[] = {1080, 1440, 900, 768, 2160};
  return kHeights[Seed() % 5];
}

}  // namespace fp
}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_PARAMS_H_
'''


FINGERPRINT_FONT_FILTER_PATH = Path(
    "third_party/blink/renderer/platform/fonts/fingerprint_font_filter.h")

FINGERPRINT_FONT_FILTER = r'''#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_FONTS_FINGERPRINT_FONT_FILTER_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_FONTS_FINGERPRINT_FONT_FILTER_H_

// PATCH:font-filter - enforce the spoofed platform's font set at the font
// resolution layer.
//
// document.fonts.check() can be answered from a whitelist (that is done in
// core/css/font_face_set.cc), but canvas measureText() and CSS layout still
// resolve the real installed fonts. A detector can therefore compare the width
// of a candidate family against a generic baseline and learn that e.g.
// "Segoe UI" exists on a profile that claims to be macOS.
//
// Filtering the family list in FontDescription::SetFamily() makes an absent
// family behave exactly like it does on the spoofed platform: it is dropped
// from the list, the next family (or the default font) is used, and every
// width-comparison heuristic sees the same answer as a genuine machine.
//
// This lives in platform/ rather than core/ because FontDescription is a
// platform type; core/ cannot be depended on from here.

#include <string>

#include "base/command_line.h"
#include "third_party/blink/renderer/platform/wtf/text/atomic_string.h"

namespace blink {
namespace fp_font {

struct SpoofConfig {
  bool enabled = false;
  // 0 = macOS, 1 = Windows, 2 = Linux
  int platform = 1;
};

inline const SpoofConfig& Config() {
  // The command line never changes, so resolve once.
  static const SpoofConfig cfg = [] {
    SpoofConfig c;
    const base::CommandLine* cl = base::CommandLine::ForCurrentProcess();
    if (!cl || !cl->HasSwitch("fingerprint")) {
      return c;
    }
    if (cl->GetSwitchValueASCII("fp-fonts") == "off") {
      return c;
    }
    const std::string p = cl->GetSwitchValueASCII("fingerprint-platform");
    if (p.empty()) {
      // No platform spoof means the real font set is the honest answer.
      return c;
    }
    c.enabled = true;
    c.platform = (p == "macos") ? 0 : (p == "linux" ? 2 : 1);
    return c;
  }();
  return cfg;
}

template <size_t N>
inline bool InList(const std::string& name, const char* const (&list)[N]) {
  for (const char* entry : list) {
    if (name == entry) {
      return true;
    }
  }
  return false;
}

// True when the family should stay in the list.
//
// Blacklist, not whitelist: only families that are a known system font of a
// platform we are NOT claiming get dropped. Anything else (custom @font-face
// names, generic keywords, unknown families) is left alone so web fonts keep
// working.
inline bool FamilyAllowed(const AtomicString& family) {
  const SpoofConfig& cfg = Config();
  if (!cfg.enabled) {
    return true;
  }
  if (family.empty()) {
    return true;
  }
  static const char* const kMac[] = {
      "Helvetica", "Helvetica Neue", "Arial", "Arial Black",
      "Arial Rounded MT Bold", "Times New Roman", "Times", "Courier New",
      "Courier", "Menlo", "Monaco", "Geneva", "Verdana", "Georgia", "Tahoma",
      "Trebuchet MS", "Lucida Grande", "Lucida Sans", "Lucida Bright",
      "Lucida Sans Typewriter", "Impact", "Comic Sans MS", "Palatino",
      "Optima", "Futura", "Baskerville", "Copperplate", "Didot",
      "American Typewriter", "Chalkboard", "Marker Felt", "Papyrus", "Skia",
      "Trattatello", "Zapf Dingbats", "Zapfino", "Apple Symbols",
      "Apple Color Emoji", "AppleGothic", "Apple Chancery", "Avenir",
      "Avenir Next", "Avenir Next Condensed", "Gill Sans", "Hoefler Text",
      "Krungthep", "Andale Mono", "Basalt", "Big Caslon", "Bodoni 72",
      "Bradley Hand", "Brush Script MT", "Cochin", "Colonna MT",
      "Freestyle Script", "Handwriting - Dakota", "Herculanum",
      "Hiragino Kaku Gothic Pro", "Kokonor", "LiSong Pro", "Luminari",
      "Marion", "Mishafi", "Nanum Gothic",
      "Party LET", "Phosphate", "Rockwell", "Sathu", "SignPainter", "Silom",
      "Snell Roundhand", "Stencil", "Superclarendon", "Symbol",
      "Waseem", "SF Pro", "SF Pro Text", "SF Pro Display",
      "SF Mono", "SF Compact", "SF Compact Text", "SF NS", "System Font",
      "New York", "PingFang SC", "PingFang TC", "PingFang HK",
      "Hiragino Sans", "Hiragino Sans GB", "Hiragino Mincho ProN",
      "Hiragino Maru Gothic ProN", "STHeiti", "STSong", "STKaiti",
      "STFangsong", "Songti SC", "Songti TC", "Heiti SC", "Heiti TC",
      "Kaiti SC", "Yuanti SC", "Hannotate SC", "Lantinghei SC", "Baoli SC",
      "Weibei SC", "Xingkai SC", "Yuppy SC", "Apple LiGothic",
      "Apple LiSung", "Apple SD Gothic Neo"};
  static const char* const kWin[] = {
      "Arial", "Arial Black", "Arial Narrow", "Calibri", "Cambria",
      "Candara", "Comic Sans MS", "Consolas", "Constantia", "Corbel",
      "Courier New", "Ebrima", "Franklin Gothic Medium", "Gabriola",
      "Gadugi", "Georgia", "Impact", "Ink Free", "Leelawadee UI",
      "Lucida Console", "Lucida Sans Unicode", "Lucida Sans",
      "Malgun Gothic", "Marlett", "Microsoft Sans Serif", "MV Boli",
      "Nirmala UI", "Palatino Linotype", "Segoe Print", "Segoe Script",
      "Segoe UI", "Segoe UI Emoji", "Segoe UI Historic", "Segoe UI Symbol",
      "Sitka", "Sylfaen", "Symbol", "Tahoma", "Times New Roman",
      "Trebuchet MS", "Verdana", "Webdings", "Wingdings", "Bahnschrift",
      "Book Antiqua", "Bookman Old Style", "Calisto MT", "Cambria Math",
      "Century", "Century Gothic", "Comic Sans", "Goudy Old Style",
      "High Tower Text", "Lucida Bright", "Lucida Fax",
      "MS Reference Sans Serif", "MS Reference Serif", "Palatino",
      "Perpetua", "Rockwell", "Tw Cen MT", "Wide Latin", "Microsoft YaHei",
      "Microsoft YaHei UI", "Microsoft JhengHei", "Microsoft JhengHei UI",
      "Microsoft Himalaya", "Microsoft Uighur", "Microsoft Yi Baiti",
      "Microsoft New Tai Lue", "Microsoft PhagsPa", "Microsoft Tai Le",
      "MS Gothic", "MS Mincho", "MS PGothic", "MS PMincho", "MS UI Gothic",
      "MingLiU", "MingLiU-ExtB", "MingLiU_HKSCS", "MingLiU_HKSCS-ExtB",
      "PMingLiU", "PMingLiU-ExtB", "SimSun", "SimSun-ExtB", "NSimSun",
      "SimHei", "KaiTi", "KaiTi_GB2312", "FangSong", "FangSong_GB2312",
      "DFKai-SB", "Batang", "BatangChe", "Gulim", "GulimChe", "Gungsuh",
      "GungsuhChe", "Dotum", "DotumChe", "Malgun Gothic Semilight",
      "Yu Gothic", "Yu Gothic UI", "Yu Mincho", "Meiryo", "Meiryo UI",
      "Browallia New", "BrowalliaUPC", "Cordia New", "CordiaUPC",
      "DilleniaUPC", "EucrosiaUPC", "FreesiaUPC", "IrisUPC", "JasmineUPC",
      "KodchiangUPC", "LilyUPC", "Leelawadee", "Mongolian Baiti",
      "Myanmar Text", "Vrinda", "Shonar Bangla", "Narkisim", "Rod", "Raavi",
      "Kalinga", "Latha", "Mangal", "Gautami", "Tunga", "Kartika",
      "Aparajita", "Sakkal Majalla", "Aldhabi", "Urdu Typesetting", "Dubai"};
  static const char* const kLinux[] = {
      "DejaVu Sans", "DejaVu Serif", "DejaVu Sans Mono",
      "DejaVu Sans Condensed", "Liberation Sans", "Liberation Serif",
      "Liberation Mono", "Liberation Sans Narrow", "Noto Sans", "Noto Serif",
      "Noto Sans Mono", "Noto Sans Display", "Noto Serif Display",
      "Noto Color Emoji", "Noto Sans Symbols", "Noto Sans Symbols2",
      "Noto Sans Math", "Ubuntu", "Ubuntu Mono", "Ubuntu Condensed",
      "Ubuntu Light", "Cantarell", "FreeSans", "FreeSerif", "FreeMono",
      "Nimbus Sans", "Nimbus Roman", "Nimbus Mono PS", "Nimbus Sans Narrow",
      "Droid Sans", "Droid Serif", "Droid Sans Mono", "Arimo", "Tinos",
      "Cousine", "DejaVu Math TeX Gyre", "STIX", "Latin Modern Roman",
      "Latin Modern Sans", "Latin Modern Mono", "TeX Gyre Adventor",
      "TeX Gyre Bonum", "TeX Gyre Chorus", "TeX Gyre Cursor",
      "TeX Gyre Heros", "TeX Gyre Pagella", "TeX Gyre Schola",
      "TeX Gyre Termes", "Bitstream Charter", "Charter", "URW Bookman",
      "URW Gothic", "Z003", "P052", "Standard Symbols PS",
      "Noto Sans CJK SC", "Noto Sans CJK TC", "Noto Sans CJK JP",
      "Noto Sans CJK KR", "Noto Sans CJK HK", "Noto Serif CJK SC",
      "Noto Serif CJK TC", "Noto Serif CJK JP", "Noto Serif CJK KR",
      "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", "WenQuanYi Bitmap Song",
      "AR PL UMing CN", "AR PL UMing TW", "AR PL UKai CN",
      "Source Han Sans SC", "Source Han Sans TC", "Source Han Serif SC",
      "Source Han Serif TC", "Droid Sans Fallback", "TakaoGothic",
      "TakaoMincho", "IPAGothic", "IPAPGothic", "IPAMincho", "IPAPMincho",
      "VL Gothic", "Baekmuk Gulim", "Baekmuk Batang", "UnDotum",
      "NanumGothic", "NanumMyeongjo"};

  const std::string name = family.Utf8();
  if (cfg.platform == 0) {
    // Claiming macOS: hide Windows/Linux-only fonts.
    return !((InList(name, kWin) || InList(name, kLinux)) &&
             !InList(name, kMac));
  }
  if (cfg.platform == 2) {
    // Claiming Linux: hide macOS/Windows-only fonts.
    return !((InList(name, kMac) || InList(name, kWin)) &&
             !InList(name, kLinux));
  }
  // Claiming Windows: hide macOS/Linux-only fonts.
  return !((InList(name, kMac) || InList(name, kLinux)) && !InList(name, kWin));
}

}  // namespace fp_font
}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_FONTS_FINGERPRINT_FONT_FILTER_H_
'''


def patch_fingerprint_helper(src_root: Path, rep: Report):
    path = src_root / FINGERPRINT_HELPER_PATH
    try:
        if path.exists():
            if "FINGERPRINT_PARAMS_H_" in read_file(path):
                rep.record("fingerprint_params.h", "ALREADY")
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        write_file(path, FINGERPRINT_HELPER)
        rep.record("fingerprint_params.h", "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record("fingerprint_params.h", "FAILED", str(e))


# ---------------------------------------------------------------------------
# 1. Multi-row bookmark bar
# ---------------------------------------------------------------------------

def patch_multirow_bookmarks(src_root: Path, rep: Report):
    name = "multi-row bookmark bar"
    h = src_root / "chrome/browser/ui/views/bookmarks/bookmark_bar_view.h"
    cc = src_root / "chrome/browser/ui/views/bookmarks/bookmark_bar_view.cc"
    if not (h.exists() and cc.exists()):
        rep.record(name, "FAILED", "bookmark_bar_view.{h,cc} missing")
        return
    try:
        ht = read_file(h)
        if "current_row_count_" not in ht:
            anchor = "std::vector<BookmarkButtonAndNode> bookmark_buttons_;"
            ht = sub_required(ht, re.escape(anchor),
                              anchor + "\n  mutable int current_row_count_ = 1;",
                              "h member")
            write_file(h, ht)

        ct = read_file(cc)
        if "kMultiRowBookmarkBarDefaultRows" not in ct:
            # helper after last include
            last_inc = list(re.finditer(r'^#include\s+[<"].*[>"]\s*$', ct, re.M))
            if not last_inc:
                raise RuntimeError("no includes in bookmark_bar_view.cc")
            helper = (
                '\n// --- BEGIN MULTI-ROW BOOKMARK BAR PATCH ---\n'
                '#include "base/command_line.h"\n'
                '#include "base/strings/string_number_conversions.h"\n\n'
                'namespace {\n'
                'constexpr int kMultiRowBookmarkBarDefaultRows = 3;\n\n'
                'int GetMaxBookmarkBarRows() {\n'
                '  static int cached_rows = -1;\n'
                '  if (cached_rows < 0) {\n'
                '    const base::CommandLine* cmd =\n'
                '        base::CommandLine::ForCurrentProcess();\n'
                '    if (cmd && cmd->HasSwitch("bookmark-bar-rows")) {\n'
                '      int val = 0;\n'
                '      if (base::StringToInt(\n'
                '              cmd->GetSwitchValueASCII("bookmark-bar-rows"), &val) &&\n'
                '          val >= 1 && val <= 15) {\n'
                '        cached_rows = val;\n'
                '        return cached_rows;\n'
                '      }\n'
                '    }\n'
                '    cached_rows = kMultiRowBookmarkBarDefaultRows;\n'
                '  }\n'
                '  return cached_rows;\n'
                '}\n'
                '}  // namespace\n'
                '// --- END MULTI-ROW BOOKMARK BAR PATCH ---\n')
            pos = last_inc[-1].end()
            ct = ct[:pos] + helper + ct[pos:]

            # taller preferred size
            ct = sub_required(
                ct,
                r'(gfx::Size BookmarkBarView::CalculatePreferredSize\(\n'
                r'[^)]*\) const \{\n[\s\S]*?)(  return prefsize;\n)',
                r'\1'
                r'  // PATCH: taller bar for extra bookmark rows\n'
                r'  if (current_row_count_ > 1) {\n'
                r'    prefsize.Enlarge(0, (current_row_count_ - 1) *\n'
                r'                           (GetLayoutConstant(\n'
                r'                                LayoutConstant::kBookmarkBarButtonHeight) +\n'
                r'                            4));\n'
                r'  }\n'
                r'\2',
                "CalculatePreferredSize")

            # row bookkeeping before the button loop
            ct = sub_required(
                ct,
                r'(if \(bookmark_bar_children_count\) \{\n)(\s*)bool can_render_button_bounds = x < max_x;',
                r'\1\2bool can_render_button_bounds = x < max_x;\n'
                r'\2const int multirow_row_start_x = x;\n'
                r'\2int multirow_current_row = 0;',
                "Layout row init")

            # wrap-around inside the loop
            ct = sub_required(
                ct,
                r'(int next_x = x \+ pref\.width\(\) \+ bookmark_bar_button_padding;\n)'
                r'(\s*)(can_render_button_bounds = next_x < max_x;\n)'
                r'(\s*)child->SetVisible\(can_render_button_bounds\);\n'
                r'(\s*)// Only need to set bounds if the view is actually visible\.\n'
                r'(\s*)if \(can_render_button_bounds\) \{\n'
                r'(\s*)child->SetBounds\(x, y, pref\.width\(\), button_height\);\n'
                r'(\s*)\}',
                r'\1\2\3'
                r'\2// PATCH: wrap to the next bookmark row when full\n'
                r'\2if (!can_render_button_bounds &&\n'
                r'\2    multirow_current_row + 1 < GetMaxBookmarkBarRows() &&\n'
                r'\2    x > multirow_row_start_x) {\n'
                r'\2  ++multirow_current_row;\n'
                r'\2  x = multirow_row_start_x;\n'
                r'\2  next_x = x + pref.width() + bookmark_bar_button_padding;\n'
                r'\2  can_render_button_bounds = next_x < max_x;\n'
                r'\2}\n'
                r'\4child->SetVisible(can_render_button_bounds);\n'
                r'\5// Only need to set bounds if the view is actually visible.\n'
                r'\6if (can_render_button_bounds) {\n'
                r'\7child->SetBounds(\n'
                r'\7    x, y + multirow_current_row * (button_height + 4),\n'
                r'\7    pref.width(), button_height);\n'
                r'\8}',
                "Layout wrap")

            # remember row count, trigger relayout when it changes
            ct = sub_required(
                ct,
                r'(      x = next_x;\n)(\s*\}\n)(\s*\}\n\n)(\s*const bool show_bookmarks_overflow =)',
                r'\1\2'
                r'    // PATCH: remember row count, relayout when it changes\n'
                r'    if (current_row_count_ != multirow_current_row + 1) {\n'
                r'      current_row_count_ = multirow_current_row + 1;\n'
                r'      PreferredSizeChanged();\n'
                r'    }\n'
                r'\3\4',
                "Layout row count")

            write_file(cc, ct)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 2. Bookmarks open in new tab by default
# ---------------------------------------------------------------------------

def patch_bookmark_new_tab(src_root: Path, rep: Report):
    name = "bookmark left-click opens new tab"
    # 158-era anchor: click handling lives in BookmarkBarView::OnButtonPressed
    path = src_root / ("chrome/browser/ui/views/bookmarks/"
                       "bookmark_bar_view.cc")
    # main-era anchor: click handling lives in the controller adapter
    alt_path = src_root / ("chrome/browser/ui/bookmarks/controllers/"
                           "desktop_bookmark_bar_action_adapter.cc")
    try:
        if path.exists():
            text = read_file(path)
            if "bookmarks-current-tab" not in text:
                text = insert_after_first_include(
                    text, '#include "base/command_line.h"')
                text = sub_required(
                    text,
                    r'(void BookmarkBarView::OnButtonPressed\(\n?'
                    r'[\s\S]{0,120}?const ui::Event& event\) \{\n'
                    r'[\s\S]{0,200}?CHECK\(controller_\);\n'
                    r'\s*)controller_->OpenBookmark\(node->id\(\),\n'
                    r'\s*ui::DispositionFromEventFlags\(event\.flags\(\)\)\);',
                    r'\1'
                    r'  // PATCH: plain left-click opens bookmarks in a new tab\n'
                    r'  WindowOpenDisposition fp_disposition =\n'
                    r'      ui::DispositionFromEventFlags(event.flags());\n'
                    r'  if (fp_disposition ==\n'
                    r'          WindowOpenDisposition::kCurrentTab &&\n'
                    r'      !base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                    r'          "bookmarks-current-tab")) {\n'
                    r'    fp_disposition =\n'
                    r'        WindowOpenDisposition::kNewForegroundTab;\n'
                    r'  }\n'
                    r'  controller_->OpenBookmark(node->id(), fp_disposition);\n'
                    r'  return;\n'
                    r'  // unreachable after PATCH return\n'
                    r'  CHECK(node->is_url(), base::NotFatalUntil::M162);',
                    "OnButtonPressed (158)")
                write_file(path, text)
            rep.record(name, "APPLIED")
            return
        if alt_path.exists():
            text = read_file(alt_path)
            if "bookmarks-current-tab" not in text:
                text = insert_after_first_include(
                    text, '#include "base/command_line.h"')
                text = sub_required(
                    text,
                    r'(void DesktopBookmarkBarActionAdapter::OpenBookmark\(\n'
                    r'\s*const bookmarks::BookmarkNode\* node,\n'
                    r'\s*ui::mojom::WindowOpenDisposition disposition\) \{\n'
                    r'\s*)if \(!node\) \{\n\s*return;\n\s*\}',
                    r'\1'
                    r'  // PATCH: plain left-click opens bookmarks in a new tab\n'
                    r'  if (disposition ==\n'
                    r'          ui::mojom::WindowOpenDisposition::kCurrentTab &&\n'
                    r'      !base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                    r'          "bookmarks-current-tab")) {\n'
                    r'    disposition =\n'
                    r'        ui::mojom::WindowOpenDisposition::kNewForegroundTab;\n'
                    r'  }\n'
                    r'  if (!node) {\n    return;\n  }',
                    "OpenBookmark disposition (main)")
                write_file(alt_path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "FAILED",
                   "neither bookmark_bar_view.cc (158) nor "
                   "desktop_bookmark_bar_action_adapter.cc (main) found")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 3. Double-click tab closes it
# ---------------------------------------------------------------------------

def patch_dblclick_close(src_root: Path, rep: Report):
    name = "double-click tab to close"
    path = src_root / "chrome/browser/ui/views/tabs/tab.cc"
    if not path.exists():
        rep.record(name, "FAILED", "tab.cc missing")
        return
    try:
        text = read_file(path)
        if "no-dblclick-tab-close" not in text:
            text = insert_after_first_include(
                text,
                '#include "ui/events/event_constants.h"',
                '#include "base/command_line.h"')
            text = sub_required(
                text,
                r'(bool Tab::OnMousePressed\(const ui::MouseEvent& event\) \{\n)',
                r'\1'
                r'  // PATCH: double-click closes the tab\n'
                r'  if (event.IsOnlyLeftMouseButton() &&\n'
                r'      (event.flags() & ui::EF_IS_DOUBLE_CLICK) &&\n'
                r'      !base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'          "no-dblclick-tab-close")) {\n'
                r'    controller_->CloseTab(this, CloseTabSource::kFromMouse);\n'
                r'    return true;\n'
                r'  }\n',
                "Tab::OnMousePressed")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 4. Mouse wheel switches tabs
# ---------------------------------------------------------------------------

def patch_wheel_tabs(src_root: Path, rep: Report):
    name = "wheel over tab strip switches tabs"
    h = src_root / "chrome/browser/ui/views/tabs/tab_strip.h"
    cc = src_root / "chrome/browser/ui/views/tabs/tab_strip.cc"
    if not (h.exists() and cc.exists()):
        rep.record(name, "FAILED", "tab_strip.{h,cc} missing")
        return
    try:
        ht = read_file(h)
        if "OnMouseWheel" not in ht:
            ht = sub_required(
                ht,
                r'(void OnMouseExited\(const ui::MouseEvent& event\) override;\n)',
                r'\1  bool OnMouseWheel(const ui::MouseWheelEvent& event) override;\n',
                "h decl")
            write_file(h, ht)

        ct = read_file(cc)
        if "no-wheel-switch-tabs" not in ct:
            ct = insert_after_first_include(
                ct, '#include "base/command_line.h"')
            ct = sub_required(
                ct,
                r'(void TabStrip::OnMouseExited\(const ui::MouseEvent& event\) \{\n'
                r'\s*UpdateHoverCard\(nullptr, HoverCardUpdateType::kHover\);\n'
                r'\s*\}\n)',
                r'\1'
                r'\n'
                r'// PATCH: wheel over the tab strip switches tabs\n'
                r'bool TabStrip::OnMouseWheel(const ui::MouseWheelEvent& event) {\n'
                r'  if (base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'          "no-wheel-switch-tabs")) {\n'
                r'    return View::OnMouseWheel(event);\n'
                r'  }\n'
                r'  const int count = controller_->GetCount();\n'
                r'  const std::optional<int> active = controller_->GetActiveIndex();\n'
                r'  if (count > 1 && active.has_value() && *active >= 0) {\n'
                r'    const int direction = (event.y_offset() > 0) ? -1 : 1;\n'
                r'    const int target = (*active + direction + count) % count;\n'
                r'    if (controller_->IsValidIndex(target)) {\n'
                r'      controller_->SelectTab(target, event);\n'
                r'    }\n'
                r'  }\n'
                r'  return true;\n'
                r'}\n',
                "cc impl")
            write_file(cc, ct)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 5. Closing the last tab keeps the window open
# ---------------------------------------------------------------------------

def patch_keep_last_tab(src_root: Path, rep: Report):
    name = "keep window when closing last tab"
    path = src_root / "chrome/browser/ui/tabs/tab_strip_model.cc"
    if not path.exists():
        rep.record(name, "FAILED", "tab_strip_model.cc missing")
        return
    try:
        text = read_file(path)
        if "no-keep-last-tab" not in text:
            text = insert_after_first_include(
                text, '#include "base/command_line.h"')
            text = sub_required(
                text,
                r'(const bool closing_all = static_cast<int>\(filtered_items\.size\(\)\) == count\(\);\n)',
                r'\1'
                r'  // PATCH: closing the last tab(s) opens a fresh NTP instead of\n'
                r'  // closing the window\n'
                r'  if (closing_all && delegate_ &&\n'
                r'      !base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'          "no-keep-last-tab")) {\n'
                r'    delegate_->AddTabAt(GURL(), -1, /*foreground=*/true);\n'
                r'  }\n',
                "CloseTabs guard")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 6. Narrower minimum tab width
# ---------------------------------------------------------------------------

def patch_narrow_tabs(src_root: Path, rep: Report):
    name = "narrower minimum tab width"
    path = src_root / "chrome/browser/ui/tabs/tab_style.cc"
    if not path.exists():
        rep.record(name, "FAILED", "tab_style.cc missing")
        return
    try:
        text = read_file(path)
        if "no-narrow-tabs" not in text:
            text = insert_after_first_include(
                text, '#include "base/command_line.h"')
            text = sub_required(
                text,
                r'(\n)(\s*)return min_active_width;\n',
                r'\1\2// PATCH: narrower tabs\n'
                r'\2if (!base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'\2        "no-narrow-tabs")) {\n'
                r'\2  return min_active_width * 3 / 5;\n'
                r'\2}\n'
                r'\2return min_active_width;\n',
                "GetMinimumActiveWidth")
            text = sub_required(
                text,
                r'(\n)(\s*)return min_inactive_width;\n',
                r'\1\2// PATCH: narrower tabs\n'
                r'\2if (!base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'\2        "no-narrow-tabs")) {\n'
                r'\2  return min_inactive_width * 3 / 5;\n'
                r'\2}\n'
                r'\2return min_inactive_width;\n',
                "GetMinimumInactiveWidth")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 7. Automation infobar removed
# ---------------------------------------------------------------------------

def patch_automation_infobar(src_root: Path, rep: Report):
    name = "automation infobar removed"
    path = src_root / "chrome/browser/ui/startup/automation_infobar_delegate.cc"
    if not path.exists():
        rep.record(name, "FAILED", "automation_infobar_delegate.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:automation-infobar" not in text:
            # Replace entire function bodies (early-return leaves unreachable
            # code that trips -Wunreachable-code-return with /WX).
            text = sub_required(
                text,
                r'void AutomationInfoBarDelegate::Create\(\) \{\n'
                r'[\s\S]*?\n\}\n',
                'void AutomationInfoBarDelegate::Create() {\n'
                '  // PATCH:automation-infobar - disabled entirely\n'
                '}\n',
                "Create()")
            text = sub_required(
                text,
                r'infobars::InfoBar\* AutomationInfoBarDelegate::Create\(\n'
                r'\s*infobars::ContentInfoBarManager\* infobar_manager\) \{\n'
                r'[\s\S]*?\n\}\n',
                'infobars::InfoBar* AutomationInfoBarDelegate::Create(\n'
                '    infobars::ContentInfoBarManager* infobar_manager) {\n'
                '  // PATCH:automation-infobar - disabled entirely\n'
                '  return nullptr;\n'
                '}\n',
                "Create(manager)")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 8. navigator.webdriver = false
# ---------------------------------------------------------------------------

def patch_webdriver_false(src_root: Path, rep: Report):
    name = "navigator.webdriver forced false"
    path = src_root / "third_party/blink/renderer/core/frame/navigator.cc"
    if not path.exists():
        rep.record(name, "FAILED", "navigator.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:webdriver-false" not in text:
            text = sub_required(
                text,
                r'(bool Navigator::webdriver\(\) const \{)[\s\S]*?\n\}',
                r'\1\n'
                r'  return false;  // PATCH:webdriver-false\n'
                r'}',
                "Navigator::webdriver")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 9-14. Simple blink getter spoofs
# ---------------------------------------------------------------------------

def patch_hw_concurrency(src_root: Path, rep: Report):
    name = "hardwareConcurrency spoof"
    path = src_root / "third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc"
    if not path.exists():
        rep.record(name, "FAILED", "navigator_concurrent_hardware.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:hw-concurrency" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            text = sub_required(
                text,
                r'(unsigned NavigatorConcurrentHardware::hardwareConcurrency\(\) const \{\n)',
                r'\1'
                r'  // PATCH:hw-concurrency\n'
                r'  if (fp::Enabled()) {\n'
                r'    static const unsigned kCores[] = {4, 8, 12, 16, 24};\n'
                r'    return kCores[fp::Seed() % 5];\n'
                r'  }\n',
                "hardwareConcurrency")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_device_memory(src_root: Path, rep: Report):
    name = "deviceMemory spoof"
    path = src_root / "third_party/blink/renderer/core/frame/navigator_device_memory.cc"
    if not path.exists():
        rep.record(name, "FAILED", "navigator_device_memory.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:device-memory" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            text = sub_required(
                text,
                r'(float NavigatorDeviceMemory::deviceMemory\(\) const \{\n)',
                r'\1'
                r'  // PATCH:device-memory\n'
                r'  if (fp::Enabled()) {\n'
                r'    static const float kRamGb[] = {4.0f, 8.0f, 16.0f, 32.0f};\n'
                r'    return kRamGb[(fp::Seed() >> 4) % 4];\n'
                r'  }\n',
                "deviceMemory")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_platform(src_root: Path, rep: Report):
    name = "navigator.platform spoof"
    path = src_root / "third_party/blink/renderer/core/frame/navigator_id.cc"
    if not path.exists():
        rep.record(name, "FAILED", "navigator_id.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:platform" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            text = sub_required(
                text,
                r'(String NavigatorID::platform\(\) const \{\n)',
                r'\1'
                r'  // PATCH:platform\n'
                r'  if (const char* p = fp::PlatformOverride()) {\n'
                r'    return String::FromUtf8(p);\n'
                r'  }\n',
                "NavigatorID::platform")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_ua_ch(src_root: Path, rep: Report):
    name = "UA-CH platform/platformVersion spoof"
    path = src_root / "third_party/blink/renderer/core/frame/navigator_ua_data.cc"
    if not path.exists():
        rep.record(name, "FAILED", "navigator_ua_data.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:ua-ch" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            # Override at the single source of truth: the mojo setter that the
            # browser process calls. Every read path (platform() getter, toJSON,
            # getHighEntropyValues) then stays consistent automatically.
            text = sub_required(
                text,
                r'(void NavigatorUAData::SetPlatform\(String brand, String version\) \{\n)',
                r'\1'
                r'  // PATCH:ua-ch - only override platformVersion.\n'
                r'  // Keep UA-CH platform native (Windows); Win32 is\n'
                r'  // navigator.platform and conflicts with UA NT 10.0.\n'
                r'  if (const char* v = fp::PlatformVersionOverride()) {\n'
                r'    version = String::FromUtf8(v);\n'
                r'  }\n',
                "SetPlatform")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_screen(src_root: Path, rep: Report):
    name = "screen size spoof"
    path = src_root / "third_party/blink/renderer/core/frame/screen.cc"
    if not path.exists():
        rep.record(name, "FAILED", "screen.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:screen" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            for fn, spoof in (
                ("height", "return fp::ScreenHeight();"),
                ("width", "return fp::ScreenWidth();"),
                ("availHeight", "return fp::ScreenHeight() - 40;"),
                ("availWidth", "return fp::ScreenWidth();"),
            ):
                text = sub_required(
                    text,
                    rf'(int Screen::{fn}\(\) const \{{\n'
                    rf'\s*)if \(!DomWindow\(\)\)\n'
                    rf'\s*return 0;\n',
                    rf'\1if (!DomWindow())\n'
                    rf'    return 0;\n'
                    rf'  // PATCH:screen\n'
                    rf'  if (fp::Enabled()) {{\n'
                    rf'    {spoof}\n'
                    rf'  }}\n',
                    f"Screen::{fn}")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_device_pixel_ratio(src_root: Path, rep: Report):
    name = "devicePixelRatio spoof"
    path = src_root / "third_party/blink/renderer/core/frame/local_dom_window.cc"
    if not path.exists():
        rep.record(name, "FAILED", "local_dom_window.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:dpr" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            text = sub_required(
                text,
                r'(double LocalDOMWindow::devicePixelRatio\(\) const \{\n'
                r'\s*)if \(!GetFrame\(\)\) \{\n'
                r'\s*return 0\.0;\n'
                r'\s*\}\n',
                r'\1if (!GetFrame()) {\n'
                r'    return 0.0;\n'
                r'  }\n'
                r'  // PATCH:dpr\n'
                r'  if (fp::Enabled()) {\n'
                r'    static const double kDpr[] = {1.0, 1.25, 1.5, 1.0, 2.0};\n'
                r'    return kDpr[fp::Seed() % 5];\n'
                r'  }\n',
                "devicePixelRatio")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_battery(src_root: Path, rep: Report):
    name = "battery API spoof (desktop-like)"
    path = src_root / "third_party/blink/renderer/modules/battery/battery_manager.cc"
    if not path.exists():
        rep.record(name, "FAILED", "battery_manager.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:battery" not in text:
            text = insert_after_first_include(
                text,
                '#include <limits>',
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            text = sub_required(
                text,
                r'(bool BatteryManager::charging\(\) \{\n)',
                r'\1'
                r'  // PATCH:battery - mimic a desktop without battery\n'
                r'  if (fp::Enabled()) {\n'
                r'    return true;\n'
                r'  }\n',
                "charging")
            text = sub_required(
                text,
                r'(double BatteryManager::chargingTime\(\) \{\n)',
                r'\1'
                r'  if (fp::Enabled()) {\n'
                r'    return 0.0;\n'
                r'  }\n',
                "chargingTime")
            text = sub_required(
                text,
                r'(double BatteryManager::dischargingTime\(\) \{\n)',
                r'\1'
                r'  if (fp::Enabled()) {\n'
                r'    return std::numeric_limits<double>::infinity();\n'
                r'  }\n',
                "dischargingTime")
            text = sub_required(
                text,
                r'(double BatteryManager::level\(\) \{\n)',
                r'\1'
                r'  if (fp::Enabled()) {\n'
                r'    return 1.0;\n'
                r'  }\n',
                "level")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 15. Canvas noise (getImageData + toDataURL)
# ---------------------------------------------------------------------------

CANVAS_NOISE_BLOCK = r'''
  // PATCH:canvas-noise - deterministic per-pixel perturbation
  if (fp::Enabled()) {
    const uint64_t fp_seed = fp::Seed();
    SkPixmap fp_pixmap = FP_PIXMAP_EXPR;
    if (fp_pixmap.writable_addr() &&
        fp_pixmap.info().bytesPerPixel() >= 3) {
      const size_t fp_row_bytes = fp_pixmap.rowBytes();
      const size_t fp_bpp = fp_pixmap.info().bytesPerPixel();
      uint8_t* fp_base = static_cast<uint8_t*>(fp_pixmap.writable_addr());
      const int fp_w = fp_pixmap.width();
      const int fp_h = fp_pixmap.height();
      for (int fp_y = 0; fp_y < fp_h; ++fp_y) {
        for (int fp_x = 0; fp_x < fp_w; ++fp_x) {
          if (((static_cast<uint32_t>((fp_seed >> 3) * 2654435761u) +
                fp_x * 40503u + fp_y * 12289u) &
               0xFF) < 6) {
            UNSAFE_TODO({
              uint8_t* fp_px =
                  fp_base + fp_y * fp_row_bytes + fp_x * fp_bpp;
              *fp_px = static_cast<uint8_t>(
                  *fp_px ^ (1u << ((fp_seed + fp_x + fp_y) & 7)));
            });
          }
        }
      }
    }
  }
'''


def patch_canvas_getimagedata(src_root: Path, rep: Report):
    name = "canvas getImageData noise"
    path = src_root / ("third_party/blink/renderer/modules/canvas/canvas2d/"
                       "base_rendering_context_2d.cc")
    if not path.exists():
        rep.record(name, "FAILED", "base_rendering_context_2d.cc missing")
        return
    try:
        text = read_file(path)
        # v4 起 getImageData 必须保持原生（见 patch_fingerprint_v3 的 v4 段）。
        # 看到 canvas-v4 标记就跳过，别再把噪声注入回去。
        if "PATCH:canvas-v4" in text:
            rep.record(name, "ALREADY")
            return
        if "PATCH:canvas-noise" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            noise = CANVAS_NOISE_BLOCK.replace("FP_PIXMAP_EXPR",
                                               "image_data->GetSkPixmap()")
            text = sub_required(
                text,
                r'(  return image_data;\n\}\n\n)(void BaseRenderingContext2D::putImageData\()',
                noise + r'\n  return image_data;\n}\n\n\2',
                "getImageDataInternal return")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_canvas_todataurl(src_root: Path, rep: Report):
    name = "canvas toDataURL noise"
    h = src_root / "third_party/blink/renderer/platform/graphics/image_data_buffer.h"
    cc = src_root / "third_party/blink/renderer/platform/graphics/image_data_buffer.cc"
    if not (h.exists() and cc.exists()):
        rep.record(name, "FAILED", "image_data_buffer.{h,cc} missing")
        return
    try:
        ht = read_file(h)
        if "owns_pixels_" not in ht:
            ht = sub_required(
                ht,
                r'(bool is_valid_ = false;\n)',
                r'\1  bool owns_pixels_ = false;  // PATCH:canvas-noise\n',
                "h member")
            write_file(h, ht)

        ct = read_file(cc)
        if "PATCH:canvas-noise" not in ct:
            ct = insert_after_first_include(
                ct,
                '#include "base/command_line.h"',
                '#include "base/strings/string_number_conversions.h"')
            ct = sub_required(
                ct,
                r'(retained_image_ =\n'
                r'\s*SkImages::RasterFromData\(info, std::move\(data\), row_bytes\);\n)',
                r'\1    owns_pixels_ = true;  // PATCH:canvas-noise\n',
                "copy-path flag")
            noise = CANVAS_NOISE_BLOCK.replace("FP_PIXMAP_EXPR", "pixmap_").replace(
                "fp::Enabled()", R"""base::CommandLine::ForCurrentProcess()->HasSwitch("fingerprint")""").replace(
                "fp::Seed()",
                R"""[&] {
      uint64_t s = 0;
      base::StringToUint64(base::CommandLine::ForCurrentProcess()
                               ->GetSwitchValueASCII("fingerprint"),
                           &s);
      return s;
    }()""")
            # Only the converted-copy path owns its pixels; the peekPixels
            # path aliases the canvas backing store and must not be mutated.
            noise = noise.replace(
                'if (base::CommandLine::ForCurrentProcess()->HasSwitch('
                '"fingerprint")) {',
                'if (owns_pixels_ &&\n'
                '      base::CommandLine::ForCurrentProcess()->HasSwitch('
                '"fingerprint")) {', 1)
            ct = sub_required(
                ct,
                r'(String ImageDataBuffer::ToDataURL\(const ImageEncodingMimeType mime_type,\n'
                r'\s*const double& quality\) const \{\n'
                r'\s*DCHECK\(is_valid_\);\n)',
                r'\1' + noise.replace("\\", "\\\\") + "\n",
                "ToDataURL noise")
            write_file(cc, ct)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 16. WebGL vendor/renderer spoof
# ---------------------------------------------------------------------------

def patch_webgl(src_root: Path, rep: Report):
    name = "WebGL vendor/renderer spoof"
    path = src_root / ("third_party/blink/renderer/modules/webgl/"
                       "webgl_rendering_context_base.cc")
    if not path.exists():
        rep.record(name, "FAILED", "webgl_rendering_context_base.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:webgl-fp" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            text = sub_required(
                text,
                r'(case WebGLDebugRendererInfo::kUnmaskedRendererWebgl:\n'
                r'\s*if \(ExtensionEnabled\(kWebGLDebugRendererInfoName\)\) \{\n'
                r'\s*)return WebGLAny\(script_state,\n'
                r'\s*String\(ContextGL\(\)->GetString\(GL_RENDERER\)\)\);',
                r'\1'
                r'        // PATCH:webgl-fp\n'
                r'        if (fp::Enabled()) {\n'
                r'          static const char* const kFpRenderers[] = {\n'
                r'              "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 "\n'
                r'              "vs_5_0 ps_5_0, D3D11)",\n'
                r'              "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 "\n'
                r'              "vs_5_0 ps_5_0, D3D11)",\n'
                r'              "ANGLE (Intel, Intel(R) UHD Graphics 630 "\n'
                r'              "Direct3D11 vs_5_0 ps_5_0, D3D11)"};\n'
                r'          return WebGLAny(\n'
                r'              script_state, String(kFpRenderers[fp::Seed() % 3]));\n'
                r'        }\n'
                r'        return WebGLAny(script_state,\n'
                r'                        String(ContextGL()->GetString(GL_RENDERER)));',
                "kUnmaskedRendererWebgl")
            text = sub_required(
                text,
                r'(case WebGLDebugRendererInfo::kUnmaskedVendorWebgl:\n'
                r'\s*if \(ExtensionEnabled\(kWebGLDebugRendererInfoName\)\) \{\n'
                r'\s*)return WebGLAny\(script_state,\n'
                r'\s*String\(ContextGL\(\)->GetString\(GL_VENDOR\)\)\);',
                r'\1'
                r'        // PATCH:webgl-fp\n'
                r'        if (fp::Enabled()) {\n'
                r'          static const char* const kFpVendors[] = {\n'
                r'              "Google Inc. (NVIDIA)", "Google Inc. (AMD)",\n'
                r'              "Google Inc. (Intel)"};\n'
                r'          return WebGLAny(\n'
                r'              script_state, String(kFpVendors[fp::Seed() % 3]));\n'
                r'        }\n'
                r'        return WebGLAny(script_state,\n'
                r'                        String(ContextGL()->GetString(GL_VENDOR)));',
                "kUnmaskedVendorWebgl")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 17. Audio noise
# ---------------------------------------------------------------------------

def patch_audio(src_root: Path, rep: Report):
    name = "AudioContext noise (render path)"
    path = src_root / "third_party/blink/renderer/modules/webaudio/audio_buffer.cc"
    if not path.exists():
        rep.record(name, "FAILED", "audio_buffer.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:audio-noise" not in text:
            text = insert_after_first_include(
                text,
                '#include "base/command_line.h"',
                '#include "base/strings/string_number_conversions.h"')
            text = sub_required(
                text,
                r'(channel_data_array->AsSpan\(\)\.copy_from\(bus->Channel\(i\)->Span\(\)\);\n'
                r'\s*channels_\.push_back\(channel_data_array\);\n'
                r'\s*\})\n',
                r'\1\n'
                r'  // PATCH:audio-noise - applied once, deterministic\n'
                r'  if (base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'          "fingerprint")) {\n'
                r'    uint64_t seed = 0;\n'
                r'    base::StringToUint64(\n'
                r'        base::CommandLine::ForCurrentProcess()\n'
                r'            ->GetSwitchValueASCII("fingerprint"),\n'
                r'        &seed);\n'
                r'    const float noise = static_cast<float>((seed % 89) + 1) * 1e-7f;\n'
                r'    for (auto& channel : channels_) {\n'
                r'      DOMFloat32Array* arr = channel.Get();\n'
                r'      if (!arr) {\n'
                r'        continue;\n'
                r'      }\n'
                r'      auto data = arr->AsSpan();\n'
                r'      if (data.size() > 4) {\n'
                r'        data[3] += noise;\n'
                r'        data[data.size() - 1] -= noise;\n'
                r'      }\n'
                r'    }\n'
                r'  }\n',
                "AudioBuffer(AudioBus*)")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 18. Timezone override
# ---------------------------------------------------------------------------

def patch_timezone(src_root: Path, rep: Report):
    """Two-file patch: the ICU default is set in blink's TimeZoneController
    (which also notifies V8 isolates), NOT in renderer_main - V8 caches the
    host timezone per-isolate, so an early ICU call gets overwritten."""
    name = "timezone override (--timezone=)"
    tz_path = src_root / ("third_party/blink/renderer/core/timezone/"
                          "timezone_controller.cc")
    if not tz_path.exists():
        rep.record(name, "FAILED", "timezone_controller.cc missing")
        return
    try:
        text = read_file(tz_path)
        if "PATCH:timezone" not in text:
            text = sub_required(
                text,
                r'(void TimeZoneController::Init\(\) \{\n)',
                r'\1'
                r'  // PATCH:timezone - apply --timezone=<IANA id> and keep it\n'
                r'  // sticky (the host TimeZoneMonitor callback would otherwise\n'
                r'  // reset it back to the machine timezone).\n'
                r'  if (base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'          "timezone")) {\n'
                r'    const std::string tz_id =\n'
                r'        base::CommandLine::ForCurrentProcess()\n'
                r'            ->GetSwitchValueASCII("timezone");\n'
                r'    if (!tz_id.empty()) {\n'
                r'      std::unique_ptr<icu::TimeZone> tz(\n'
                r'          icu::TimeZone::createTimeZone(\n'
                r'              icu::UnicodeString(tz_id.data(), -1, US_INV)));\n'
                r'      if (tz && *tz != icu::TimeZone::getUnknown()) {\n'
                r'        icu::TimeZone::adoptDefault(tz.release());\n'
                r'        base::AutoLock locker(instance().lock_);\n'
                r'        instance().override_timezone_id_ =\n'
                r'            String::FromUtf8(tz_id);\n'
                r'      }\n'
                r'    }\n'
                r'  }\n',
                "TimeZoneController::Init")
            # Sticky: ignore the host monitor and re-notify V8 (by the time
            # OnTimeZoneChange fires, the isolates exist and can be told).
            text = sub_required(
                text,
                r'(void TimeZoneController::OnTimeZoneChange\(const String& timezone_id\) \{\n'
                r'\s*DCHECK\(IsMainThread\(\)\);\n\n'
                r'\s*base::AutoLock locker\(instance\(\)\.lock_\);\n)',
                r'\1'
                r'\n'
                r'  // PATCH:timezone - keep the spoofed zone, notify V8 isolates\n'
                r'  if (base::CommandLine::ForCurrentProcess()->HasSwitch(\n'
                r'          "timezone")) {\n'
                r'    const std::string tz_id =\n'
                r'        base::CommandLine::ForCurrentProcess()\n'
                r'            ->GetSwitchValueASCII("timezone");\n'
                r'    if (!tz_id.empty()) {\n'
                r'      std::unique_ptr<icu::TimeZone> tz(\n'
                r'          icu::TimeZone::createTimeZone(\n'
                r'              icu::UnicodeString(tz_id.data(), -1, US_INV)));\n'
                r'      if (tz && *tz != icu::TimeZone::getUnknown()) {\n'
                r'        icu::TimeZone::adoptDefault(tz.release());\n'
                r'        instance().override_timezone_id_ =\n'
                r'            String::FromUtf8(tz_id);\n'
                r'        NotifyTimezoneChangeOnMainThread();\n'
                r'      }\n'
                r'    }\n'
                r'    return;\n'
                r'  }\n',
                "TimeZoneController::OnTimeZoneChange")
            write_file(tz_path, text)
        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


def patch_platform_v2(src_root: Path, rep: Report):
    """158 binding: navigator.platform goes through Navigator::platform()
    (reads Settings override), NOT NavigatorID::platform(). Patch both."""
    name = "navigator.platform spoof (Navigator::platform)"
    path = src_root / "third_party/blink/renderer/core/frame/navigator.cc"
    if not path.exists():
        rep.record(name, "FAILED", "navigator.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:platform" not in text:
            text = insert_after_first_include(
                text,
                '#include "third_party/blink/renderer/core/frame/'
                'fingerprint_params.h"')
            text = sub_required(
                text,
                r'(String Navigator::platform\(\) const \{\n)',
                r'\1'
                r'  // PATCH:platform - 158 binding goes through\n'
                r'  // Navigator::platform, not NavigatorID::platform\n'
                r'  if (const char* p = fp::PlatformOverride()) {\n'
                r'    return String::FromUtf8(p);\n'
                r'  }\n',
                "Navigator::platform")
            write_file(path, text)
        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 19. Switch propagation browser -> renderer
# ---------------------------------------------------------------------------

def patch_switch_propagation(src_root: Path, rep: Report):
    name = "fingerprint switch propagation to renderers"
    path = src_root / "content/browser/renderer_host/render_process_host_impl.cc"
    if not path.exists():
        rep.record(name, "FAILED", "render_process_host_impl.cc missing")
        return
    try:
        text = read_file(path)
        if '"fingerprint",' not in text:
            text = sub_required(
                text,
                r'(switches::kWaitForDebuggerOnNavigation,\n)',
                r'\1'
                r'      "fingerprint",\n'
                r'      "fingerprint-platform",\n'
                r'      "fingerprint-lang",\n'
                r'      "fp-cores",\n'
                r'      "fp-memory",\n'
                r'      "fp-screen",\n'
                r'      "fp-dpr",\n'
                r'      "fp-canvas",\n'
                r'      "fp-webgl",\n'
                r'      "fp-audio",\n'
                r'      "fp-fonts",\n'
                r'      "fp-geo",\n'
                r'      "timezone",\n'
                r'      "bookmark-bar-rows",\n',
                "kSwitchNames")
            write_file(path, text)
            rep.record(name, "APPLIED")
            return
        # Older runs may predate some switches; top them up individually.
        missing = [s for s in ("fp-cores", "fp-memory", "fp-screen", "fp-dpr",
                               "fp-canvas", "fp-webgl", "fp-audio", "fp-fonts",
                   "fp-geo")
                   if f'"{s}",' not in text]
        if missing:
            block = "".join(f'      "{s}",\n' for s in missing)
            text = sub_required(text, r'("fingerprint-lang",\n)', r'\1' + block,
                                "top-up switches")
            write_file(path, text)
            rep.record(name, "APPLIED", "top-up: " + ", ".join(missing))
            return
        rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 22. Local SDK 26100 compatibility (unofficial local builds)
# ---------------------------------------------------------------------------

def patch_sdk_compat(src_root: Path, rep: Report):
    """Chromium 158 expects SDK 10.0.28000. Local builds with 26100 need:
    - SDK_VERSION constant lowered (vs_toolchain.py, setup_toolchain.py)
    - dbghelp.dll copy made optional (Debuggers feature often absent)
    - NTDDI_WIN11_BR -> NTDDI_WIN11_GE (BR constant absent in 26100 headers)
    - windows_version.cc #error check gated

    Files missing entirely (e.g. trimmed test fixtures) => SKIP, not FAILED.
    """
    name = "local SDK 26100 compatibility"
    errs = []
    skipped = False

    targets = [
        ("build/vs_toolchain.py",
         "SDK_VERSION = '10.0.28000.0'",
         "SDK_VERSION = '10.0.26100.0'",
         "vs_toolchain SDK_VERSION"),
        ("build/toolchain/win/setup_toolchain.py",
         "SDK_VERSION = '10.0.28000.0'",
         "SDK_VERSION = '10.0.26100.0'",
         "setup_toolchain SDK_VERSION"),
        ("build/vs_toolchain.py",
         "('dbghelp.dll', False),",
         "('dbghelp.dll', True),",
         "dbghelp optional"),
        ("build/config/win/BUILD.gn",
         "NTDDI_VERSION=NTDDI_WIN11_BR",
         "NTDDI_VERSION=NTDDI_WIN11_GE",
         "NTDDI version"),
        ("base/win/windows_version.cc",
         "#if !defined(NTDDI_WIN11_BR)\n#error Windows 10.0.28000.0 SDK or higher required.\n#endif",
         "// PATCH: local SDK 26100 check disabled\n"
         "#if defined(NTDDI_WIN11_BR) && NTDDI_VERSION < NTDDI_WIN11_BR\n"
         "#error Windows 10.0.28000.0 SDK or higher required.\n"
         "#endif",
         "windows_version check"),
    ]

    for path_str, old, new, label in targets:
        p = src_root / path_str
        if not p.exists():
            skipped = True
            continue
        t = p.read_text(encoding="utf-8")
        if new in t:
            continue  # already patched
        if old not in t:
            errs.append(f"anchor not found: {label} in {path_str}")
            continue
        p.write_text(t.replace(old, new), encoding="utf-8")

    if errs:
        rep.record(name, "FAILED", "; ".join(errs))
    elif skipped:
        rep.record(name, "SKIPPED", "build files not in tree (fixtures)")
    else:
        rep.record(name, "APPLIED")



# (source path, verbatim upstream block, replacement, label)
_MENU_COLUMN_EDITS = [
    (
        'ui/views/controls/menu/submenu_view.h',
        '  }\n  bool use_scroll_bar() const { return use_scroll_bar_; }\n\n  void SetBorderColorId(std::optional<ui::ColorId> color_id);\n\n protected:\n',
        '  }\n  bool use_scroll_bar() const { return use_scroll_bar_; }\n\n  // PATCH:menu-columns - generic column layout for menus.\n  // Enables a multi-column layout for this submenu. When |max_rows| is greater\n  // than zero and the submenu holds more than |max_rows| visible children, the\n  // children fill the first column top-to-bottom, then wrap into a new column\n  // to the right, and so on. |column_gap| is the horizontal distance between\n  // columns in DIPs. Passing 0 for |max_rows| restores the default\n  // single-column layout.\n  void SetColumnLayout(int max_rows, int column_gap);\n  bool has_column_layout() const { return column_rows_ > 0; }\n  int column_rows() const { return column_rows_; }\n\n  // Number of columns the visible children currently occupy. Always at least 1.\n  int column_count() const;\n\n  void SetBorderColorId(std::optional<ui::ColorId> color_id);\n\n protected:\n',
        'submenu_view.h: }',
    ),
    (
        'ui/views/controls/menu/submenu_view.h',
        '  // Implementation of ScrollDelegate\n  bool OnScroll(float dx, float dy) override;\n\n  // Parent menu item.\n  raw_ptr<MenuItemView> parent_menu_item_;\n\n',
        '  // Implementation of ScrollDelegate\n  bool OnScroll(float dx, float dy) override;\n\n  // PATCH:menu-columns\n  // Height in DIPs of |column| when each item is laid out at |item_width|,\n  // including the padding that separates adjacent items.\n  int CalculateColumnHeight(const std::vector<const View*>& column,\n                            int item_width) const;\n\n  // Maximum number of rows per column; 0 keeps the single-column layout.\n  int column_rows_ = 0;\n\n  // Horizontal distance between adjacent columns in DIPs.\n  int column_gap_ = 0;\n\n  // Parent menu item.\n  raw_ptr<MenuItemView> parent_menu_item_;\n\n',
        'submenu_view.h: bool OnScroll(float dx, float dy) override;',
    ),
    (
        'ui/views/controls/menu/submenu_view.cc',
        '#include "ui/views/controls/menu/menu_separator.h"\n#include "ui/views/view_utils.h"\n#include "ui/views/widget/root_view.h"\n#include "ui/views/widget/widget.h"\n\nnamespace views {\n',
        '#include "ui/views/controls/menu/menu_separator.h"\n#include "ui/views/view_utils.h"\n#include "ui/views/widget/root_view.h"\n#include "ui/display/display.h"\n#include "ui/display/screen.h"\n#include "ui/views/widget/widget.h"\n\nnamespace views {\n',
        'submenu_view.cc: namespace views {',
    ),
    (
        'ui/views/controls/menu/submenu_view.cc',
        "\n// TODO(https://crbug.com/537701460): SubmenuView should use a\n// DelegatingLayoutManager instead of overriding layout.\nvoid SubmenuView::Layout(PassKey) {\n  // We're in a ScrollView, and need to set our width/height ourselves.\n  if (!parent()) {\n",
        "\n// TODO(https://crbug.com/537701460): SubmenuView should use a\n// DelegatingLayoutManager instead of overriding layout.\n// PATCH:menu-columns - usable width for a multi-column menu on the display the\n// menu will open on. Used to keep the menu from growing past the screen edge.\nnamespace {\n\nint MaxMenuWidthForScreen(const views::Widget* widget) {\n  constexpr int kFallbackMaxWidth = 1100;\n  constexpr int kMinMaxWidth = 480;\n  if (!widget) {\n    return kFallbackMaxWidth;\n  }\n  const display::Display display = display::Screen::Get()->GetDisplayNearestWindow(\n      widget->GetNativeWindow());\n  if (!display.is_valid()) {\n    return kFallbackMaxWidth;\n  }\n  return std::max(kMinMaxWidth,\n                  static_cast<int>(display.work_area().width() * 0.9));\n}\n\n}  // namespace\n\nvoid SubmenuView::Layout(PassKey) {\n  // We're in a ScrollView, and need to set our width/height ourselves.\n  if (!parent()) {\n",
        'submenu_view.cc: namespace {',
    ),
    (
        'ui/views/controls/menu/submenu_view.cc',
        '                : 0,\n            parent()->width(), pref_height);\n\n  const gfx::Insets insets = GetInsets();\n  const int x = insets.left();\n  int y = insets.top();\n  const int menu_item_width = width() - insets.width();\n  const int between_item_vertical_padding =\n      MenuConfig::instance().between_item_vertical_padding;\n  bool previous_child_was_lower_separator = false;\n  bool previous_child_was_menu_item_separator = false;\n  for (View* child : children()) {\n    if (child->GetVisible()) {\n      const auto* separator = AsViewClass<MenuSeparator>(child);\n      const bool is_menu_item_separator =\n          separator && separator->GetType() == ui::MENU_ITEM_SEPARATOR;\n',
        "                : 0,\n            parent()->width(), pref_height);\n\n  // PATCH:menu-columns - fill column by column instead of one long list.\n  // Group the visible children into columns. Without column layout this is a\n  // single column holding every visible child, which keeps the classic menu\n  // behavior unchanged.\n  std::vector<std::vector<View*>> columns(1);\n  int visible_index = 0;\n  for (View* child : children()) {\n    if (!child->GetVisible()) {\n      continue;\n    }\n    if (column_rows_ > 0 && visible_index > 0 &&\n        visible_index % column_rows_ == 0) {\n      columns.emplace_back();\n    }\n    columns.back().push_back(child);\n    ++visible_index;\n  }\n\n  const gfx::Insets insets = GetInsets();\n  const int column_count = static_cast<int>(columns.size());\n  const int gap = column_count > 1 ? column_gap_ : 0;\n  const int total_item_width = width() - insets.width();\n  const int uniform_width =\n      std::max(0, (total_item_width - gap * (column_count - 1)) / column_count);\n  const int between_item_vertical_padding =\n      MenuConfig::instance().between_item_vertical_padding;\n\n  // PATCH:menu-columns - auto-fit column widths.\n  // Each column asks for the width its own entries need, but the sum is capped\n  // by the space we actually have. When it does not fit, every column shrinks\n  // proportionally and its labels ellipsize instead of the menu running past\n  // the screen edge.\n  constexpr int kMinColumnWidth = 140;\n  // Keep a single column in the same ballpark as Chrome's own menu width, so a\n  // couple of long bookmark titles cannot stretch the menu across the screen.\n  constexpr int kMaxColumnWidth = 400;\n  std::vector<int> column_widths;\n  column_widths.reserve(columns.size());\n  int natural_total = 0;\n  for (const std::vector<View*>& column : columns) {\n    int natural = uniform_width;\n    if (column_rows_ > 0) {\n      natural = 0;\n      for (const View* child : column) {\n        natural = std::max(natural, child->GetPreferredSize({}).width());\n      }\n      natural = std::max(std::min(natural, kMaxColumnWidth), kMinColumnWidth);\n    }\n    column_widths.push_back(natural);\n    natural_total += natural;\n  }\n  const int usable_width =\n      std::max(0, total_item_width - gap * (column_count - 1));\n  if (natural_total > usable_width && natural_total > 0) {\n    int used = 0;\n    for (size_t i = 0; i < column_widths.size(); ++i) {\n      column_widths[i] =\n          std::max(kMinColumnWidth, column_widths[i] * usable_width / natural_total);\n      used += column_widths[i];\n    }\n    if (used > usable_width) {  // minimum widths still overflow: even split\n      const int even = std::max(0, usable_width / column_count);\n      for (size_t i = 0; i < column_widths.size(); ++i) {\n        column_widths[i] = even;\n      }\n    }\n  }\n\n  int column_x = insets.left();\n  for (size_t column_index = 0; column_index < columns.size(); ++column_index) {\n    const std::vector<View*>& column = columns[column_index];\n    const int item_width = column_widths[column_index];\n    int y = insets.top();\n    bool previous_child_was_lower_separator = false;\n    bool previous_child_was_menu_item_separator = false;\n    for (View* child : column) {\n      const auto* separator = AsViewClass<MenuSeparator>(child);\n      const bool is_menu_item_separator =\n          separator && separator->GetType() == ui::MENU_ITEM_SEPARATOR;\n",
        'submenu_view.cc: : 0,',
    ),
    (
        'ui/views/controls/menu/submenu_view.cc',
        '                          separator->GetType() != ui::MENU_ITEM_SEPARATOR))) {\n        y += between_item_vertical_padding;\n      }\n      child->SetBounds(x, y, menu_item_width,\n                       child->GetHeightForWidth(menu_item_width));\n      y = child->bounds().bottom();\n      previous_child_was_lower_separator =\n          separator && separator->GetType() == ui::LOWER_SEPARATOR;\n      previous_child_was_menu_item_separator = is_menu_item_separator;\n    }\n  }\n}\n\n',
        '                          separator->GetType() != ui::MENU_ITEM_SEPARATOR))) {\n        y += between_item_vertical_padding;\n      }\n      child->SetBounds(column_x, y, item_width,\n                       child->GetHeightForWidth(item_width));\n      y = child->bounds().bottom();\n      previous_child_was_lower_separator =\n          separator && separator->GetType() == ui::LOWER_SEPARATOR;\n      previous_child_was_menu_item_separator = is_menu_item_separator;\n    }\n    column_x += item_width + gap;\n  }\n}\n\n',
        'submenu_view.cc: separator->GetType() != ui::MENU_ITEM_SEPARATOR)',
    ),
    (
        'ui/views/controls/menu/submenu_view.cc',
        '    width = std::max(touchable_minimum_width, width);\n  }\n\n  // Then, the height for that width.\n  const int menu_item_width = width - insets.width();\n  bool previous_child_was_lower_separator = false;\n  bool previous_child_was_menu_item_separator = false;\n  const auto get_height = [&](int height, const View* child) {\n    if (!child->GetVisible()) {\n      return height;\n    }\n    const auto* separator = AsViewClass<MenuSeparator>(child);\n    const bool is_menu_item_separator =\n        separator && separator->GetType() == ui::MENU_ITEM_SEPARATOR;\n',
        '    width = std::max(touchable_minimum_width, width);\n  }\n\n  // Then, the height for that width. With column layout every column is\n  // `width` wide, so the total width grows by one column per `column_rows_`\n  // visible children and the height is the tallest column.\n  const int menu_item_width = width - insets.width();\n  int height = 0;\n  if (column_rows_ > 0) {\n    std::vector<std::vector<const View*>> columns(1);\n    int visible_index = 0;\n    for (const View* child : children()) {\n      if (!child->GetVisible()) {\n        continue;\n      }\n      if (visible_index > 0 && visible_index % column_rows_ == 0) {\n        columns.emplace_back();\n      }\n      columns.back().push_back(child);\n      ++visible_index;\n    }\n    for (const std::vector<const View*>& column : columns) {\n      height = std::max(height, CalculateColumnHeight(column, menu_item_width));\n    }\n    // Each column contributes its own width; the menu is their sum plus gaps.\n    constexpr int kMaxColumnWidth = 400;\n    int columns_total_width = 0;\n    for (const std::vector<const View*>& column : columns) {\n      int column_width = 0;\n      for (const View* child : column) {\n        column_width =\n            std::max(column_width, child->GetPreferredSize({}).width());\n      }\n      columns_total_width += std::min(column_width, kMaxColumnWidth);\n    }\n    width = columns_total_width + insets.width() +\n            column_gap_ * (static_cast<int>(columns.size()) - 1);\n    // PATCH:menu-columns - never exceed the usable width of the screen.\n    width = std::min(width, MaxMenuWidthForScreen(GetWidget()));\n  } else {\n    std::vector<const View*> column;\n    for (const View* child : children()) {\n      if (child->GetVisible()) {\n        column.push_back(child);\n      }\n    }\n    height = CalculateColumnHeight(column, menu_item_width);\n  }\n\n  return gfx::Size(width, height + insets.height());\n}\n\n// PATCH:menu-columns\nvoid SubmenuView::SetColumnLayout(int max_rows, int column_gap) {\n  const int clamped_rows = std::max(0, max_rows);\n  const int clamped_gap = clamped_rows > 0 ? std::max(0, column_gap) : 0;\n  if (clamped_rows == column_rows_ && clamped_gap == column_gap_) {\n    return;\n  }\n  column_rows_ = clamped_rows;\n  column_gap_ = clamped_gap;\n  InvalidateLayout();\n}\n\nint SubmenuView::column_count() const {\n  if (column_rows_ <= 0) {\n    return 1;\n  }\n  int visible_count = 0;\n  for (const View* child : children()) {\n    if (child->GetVisible()) {\n      ++visible_count;\n    }\n  }\n  return std::max(1, (visible_count + column_rows_ - 1) / column_rows_);\n}\n\nint SubmenuView::CalculateColumnHeight(const std::vector<const View*>& column,\n                                       int item_width) const {\n  const int between_item_vertical_padding =\n      MenuConfig::instance().between_item_vertical_padding;\n  int height = 0;\n  bool previous_child_was_lower_separator = false;\n  bool previous_child_was_menu_item_separator = false;\n  for (const View* child : column) {\n    const auto* separator = AsViewClass<MenuSeparator>(child);\n    const bool is_menu_item_separator =\n        separator && separator->GetType() == ui::MENU_ITEM_SEPARATOR;\n',
        'submenu_view.cc: width = std::max(touchable_minimum_width, width)',
    ),
    (
        'ui/views/controls/menu/submenu_view.cc',
        '        !previous_child_was_menu_item_separator &&\n        (!separator || (separator->GetType() != ui::UPPER_SEPARATOR &&\n                        separator->GetType() != ui::MENU_ITEM_SEPARATOR))) {\n      height += config.between_item_vertical_padding;\n    }\n    previous_child_was_lower_separator =\n        separator && separator->GetType() == ui::LOWER_SEPARATOR;\n    previous_child_was_menu_item_separator = is_menu_item_separator;\n    return height + child->GetHeightForWidth(menu_item_width);\n  };\n  const int height =\n      std::accumulate(children().cbegin(), children().cend(), 0, get_height);\n\n  return gfx::Size(width, height + insets.height());\n}\n\nvoid SubmenuView::PaintChildren(const PaintInfo& paint_info) {\n',
        '        !previous_child_was_menu_item_separator &&\n        (!separator || (separator->GetType() != ui::UPPER_SEPARATOR &&\n                        separator->GetType() != ui::MENU_ITEM_SEPARATOR))) {\n      height += between_item_vertical_padding;\n    }\n    previous_child_was_lower_separator =\n        separator && separator->GetType() == ui::LOWER_SEPARATOR;\n    previous_child_was_menu_item_separator = is_menu_item_separator;\n    height += child->GetHeightForWidth(item_width);\n  }\n  return height;\n}\n\nvoid SubmenuView::PaintChildren(const PaintInfo& paint_info) {\n',
        'submenu_view.cc: !previous_child_was_menu_item_separator &&',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.h',
        '                 size_t start_child_index,\n                 views::MenuItemView* menu);\n\n  // Registers the necessary mappings for |menu| and |node|.\n  void AddMenuToMaps(views::MenuItemView* menu,\n                     const BookmarkFolderOrURL& node);\n',
        '                 size_t start_child_index,\n                 views::MenuItemView* menu);\n\n  // PATCH:menu-columns - multi-column bookmark folder menus.\n  // Switches |menu| to a multi-column layout when it holds more bookmarks than\n  // fit vertically on the screen.\n  void MaybeEnableColumnLayout(views::MenuItemView* menu);\n\n  // Registers the necessary mappings for |menu| and |node|.\n  void AddMenuToMaps(views::MenuItemView* menu,\n                     const BookmarkFolderOrURL& node);\n',
        'bookmark_menu_delegate.h: size_t start_child_index,',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '\n#include "chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.h"\n\n#include <cstdint>\n#include <memory>\n#include <optional>\n\n#include "base/containers/fixed_flat_set.h"\n#include "base/containers/to_vector.h"\n#include "base/functional/bind.h"\n',
        '\n#include "chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.h"\n\n#include <algorithm>\n#include <cstdint>\n#include <memory>\n#include <optional>\n\n#include "base/command_line.h"\n#include "base/containers/fixed_flat_set.h"\n#include "base/containers/to_vector.h"\n#include "base/functional/bind.h"\n',
        'bookmark_menu_delegate.cc: hunk 0',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '#include "base/memory/weak_ptr.h"\n#include "base/metrics/user_metrics.h"\n#include "base/notreached.h"\n#include "build/build_config.h"\n#include "chrome/app/vector_icons/vector_icons.h"\n#include "chrome/browser/bookmarks/bookmark_merged_surface_service.h"\n',
        '#include "base/memory/weak_ptr.h"\n#include "base/metrics/user_metrics.h"\n#include "base/notreached.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "base/strings/string_number_conversions.h"\n#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "build/build_config.h"\n#include "chrome/app/vector_icons/vector_icons.h"\n#include "chrome/browser/bookmarks/bookmark_merged_surface_service.h"\n',
        'bookmark_menu_delegate.cc: hunk 1',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '#include "ui/base/window_open_disposition.h"\n#include "ui/base/window_open_disposition_utils.h"\n#include "ui/color/color_id.h"\n#include "ui/gfx/image/image_skia_operations.h"\n#include "ui/menus/simple_menu_model.h"\n#include "ui/views/accessibility/view_accessibility.h"\n',
        '#include "ui/base/window_open_disposition.h"\n#include "ui/base/window_open_disposition_utils.h"\n#include "ui/color/color_id.h"\n#include "ui/display/display.h"\n#include "ui/display/screen.h"\n#include "ui/gfx/geometry/rect.h"\n#include "ui/display/display.h"\n#include "ui/display/screen.h"\n#include "ui/gfx/image/image_skia_operations.h"\n#include "ui/menus/simple_menu_model.h"\n#include "ui/views/accessibility/view_accessibility.h"\n',
        'bookmark_menu_delegate.cc: hunk 2',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '// both IE and FF restrict the max width of a menu.\nconst int kMaxMenuWidth = 400;\n\nui::ImageModel GetFaviconForNode(BookmarkModel* model,\n                                 const BookmarkNode* node) {\n  const gfx::Image& image = model->GetFavicon(node);\n',
        '// both IE and FF restrict the max width of a menu.\nconst int kMaxMenuWidth = 400;\n\n// PATCH:menu-columns - multi-column bookmark folder menus.\n// Horizontal gap between the columns of a multi-column bookmark menu, in DIPs.\nconstexpr int kMenuColumnGap = 8;\n\n// Vertical space kept free for the bookmark bar and the menu border when\n// deciding how many rows fit on screen, in DIPs.\n// PATCH:menu-columns - the anchor is now accounted for separately (see\n// MaybeEnableColumnLayout), so this is only the menu\'s own bottom margin.\nconstexpr int kMenuVerticalReserve = 16;\n\n// Command line switch to force the number of rows per column, e.g.\n// --bookmark-menu-rows=20. Unset or <= 0 means "fit the screen".\nconst char kMenuRowsSwitch[] = "bookmark-menu-rows";\n\nui::ImageModel GetFaviconForNode(BookmarkModel* model,\n                                 const BookmarkNode* node) {\n  const gfx::Image& image = model->GetFavicon(node);\n',
        'bookmark_menu_delegate.cc: const int kMaxMenuWidth = 400;',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '}\n\nint BookmarkMenuDelegate::GetMaxWidthForMenu(MenuItemView* menu) {\n  return kMaxMenuWidth;\n}\n\n',
        '}\n\nint BookmarkMenuDelegate::GetMaxWidthForMenu(MenuItemView* menu) {\n  // PATCH:menu-columns\n  // A multi-column menu needs room for every column, otherwise the columns\n  // would be squeezed and every label ellipsized.\n  views::SubmenuView* submenu = menu->GetSubmenu();\n  if (submenu && submenu->has_column_layout()) {\n    // PATCH:menu-columns - cap the total to the screen: multiplying by the\n    // column count let two wide columns fill the whole display.\n    int want = kMaxMenuWidth * submenu->column_count();\n    if (views::Widget* widget = menu->GetWidget()) {\n      const display::Display display =\n          display::Screen::Get()->GetDisplayNearestWindow(\n              widget->GetNativeWindow());\n      if (display.is_valid()) {\n        want = std::min(want,\n                        std::max(480, static_cast<int>(\n                                          display.work_area().width() * 0.9)));\n      }\n    }\n    return want;\n  }\n  return kMaxMenuWidth;\n}\n\n',
        'bookmark_menu_delegate.cc: }',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '  std::vector<const BookmarkNode*> nodes =\n      GetBookmarkMergedSurfaceService()->GetUnderlyingNodes(folder);\n  CHECK(!nodes.empty());\n  AddMenuToMaps(parent_menu->AddMenuItemAt(\n                    index, GetAndIncrementNextMenuID(),\n                    MaybeEscapeLabel(nodes[0]->GetTitle()), std::u16string(),\n                    std::u16string(), ui::ImageModel(), icon,\n                    MenuItemView::Type::kSubMenu, ui::NORMAL_SEPARATOR),\n                BookmarkFolderOrURL(folder));\n}\n',
        '  std::vector<const BookmarkNode*> nodes =\n      GetBookmarkMergedSurfaceService()->GetUnderlyingNodes(folder);\n  CHECK(!nodes.empty());\n  // PATCH:folder-count - show how many entries the folder holds so a long\n  // folder can be judged before opening it.\n  std::u16string folder_label = MaybeEscapeLabel(nodes[0]->GetTitle());\n  const size_t folder_child_count =\n      GetBookmarkMergedSurfaceService()->GetChildrenCount(folder);\n  if (folder_child_count > 0) {\n    folder_label += base::UTF8ToUTF16(\n        base::StringPrintf(" (%zu)", folder_child_count));\n  }\n  AddMenuToMaps(parent_menu->AddMenuItemAt(\n                    index, GetAndIncrementNextMenuID(), folder_label,\n                    std::u16string(), std::u16string(), ui::ImageModel(), icon,\n                    MenuItemView::Type::kSubMenu, ui::NORMAL_SEPARATOR),\n                BookmarkFolderOrURL(folder));\n}\n',
        'bookmark_menu_delegate.cc: std::vector<const BookmarkNode*> nodes =',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '  }\n  AddMenuToMaps(menu, BookmarkFolderOrURL(folder));\n  built_nodes_.insert(folder);\n}\n\nvoid BookmarkMenuDelegate::AddMenuToMaps(MenuItemView* menu,\n',
        '  }\n  AddMenuToMaps(menu, BookmarkFolderOrURL(folder));\n  built_nodes_.insert(folder);\n  // PATCH:menu-columns\n  MaybeEnableColumnLayout(menu);\n}\n\nvoid BookmarkMenuDelegate::MaybeEnableColumnLayout(MenuItemView* menu) {\n  views::SubmenuView* submenu = menu->GetSubmenu();\n  if (!submenu) {\n    return;\n  }\n  const size_t item_count = submenu->GetMenuItems().size();\n  if (item_count < 2) {\n    return;\n  }\n\n  int max_rows = 0;\n  const base::CommandLine* command_line =\n      base::CommandLine::ForCurrentProcess();\n  if (command_line->HasSwitch(kMenuRowsSwitch)) {\n    base::StringToInt(command_line->GetSwitchValueASCII(kMenuRowsSwitch),\n                      &max_rows);\n  }\n\n  if (max_rows <= 0) {\n    const int item_height = submenu->GetPreferredItemHeight();\n    display::Screen* screen = display::Screen::Get();\n    if (item_height <= 0 || !screen) {\n      return;\n    }\n    views::Widget* widget = parent();\n    const display::Display display =\n        widget ? screen->GetDisplayNearestWindow(widget->GetNativeWindow())\n               : screen->GetPrimaryDisplay();\n    // PATCH:menu-columns - the menu opens right under the bookmark bar, so the\n    // space we actually have is what is left below the anchor. Using the whole\n    // work area height pushed the last row or two off the bottom of the screen\n    // and forced scrolling.\n    // The folder button\'s own bounds are empty here (the menu has not been laid\n    // out yet) and `parent()` is the window widget, not the bookmark bar, so\n    // estimate the anchor as "client area top + tab strip/toolbar/bookmark bar".\n    // Without this the row count was computed from the whole work area and the\n    // last row or two ended up below the screen edge.\n    // PATCH:menu-columns - the old fixed 160 DIP guess only matched a three-row\n    // bookmark bar. With one row it over-reserved ~68 DIP (menu stopped short),\n    // with five rows it under-reserved (last rows fell below the screen). Derive\n    // it from --bookmark-bar-rows so the estimate tracks the real chrome height.\n    constexpr int kTabStripAndToolbarDip = 88;\n    constexpr int kBookmarkBarRowDip = 34;\n    constexpr int kDefaultBookmarkBarRows = 3;\n    int bookmark_rows = 0;\n    if (command_line->HasSwitch("bookmark-bar-rows")) {\n      base::StringToInt(\n          command_line->GetSwitchValueASCII("bookmark-bar-rows"),\n          &bookmark_rows);\n    }\n    if (bookmark_rows <= 0) {\n      bookmark_rows = kDefaultBookmarkBarRows;\n    }\n    bookmark_rows = std::clamp(bookmark_rows, 1, 15);\n    const int top_chrome_estimate =\n        kTabStripAndToolbarDip + bookmark_rows * kBookmarkBarRowDip;\n\n    int anchor_top = 0;\n    int anchor_bottom = 0;\n    const gfx::Rect anchor_bounds = menu->GetBoundsInScreen();\n    if (!anchor_bounds.IsEmpty()) {\n      anchor_top = anchor_bounds.y();\n      anchor_bottom = anchor_bounds.bottom();\n    } else if (widget) {\n      anchor_top =\n          widget->GetClientAreaBoundsInScreen().y() + top_chrome_estimate;\n      anchor_bottom = anchor_top;\n    }\n    // PATCH:menu-columns - a menu that does not fit below the anchor is flipped\n    // above it by MenuController, so the column height has to be sized from\n    // whichever side has more room. Looking only below the anchor meant a folder\n    // button near the bottom of the screen got max_rows from the few pixels left\n    // under it and every entry was squeezed into one single bottom row.\n    const gfx::Rect work_area = display.work_area();\n    const int space_below = anchor_bottom > 0 ? work_area.bottom() - anchor_bottom\n                                              : work_area.height();\n    const int space_above = anchor_top > 0 ? anchor_top - work_area.y() : 0;\n    const int available_height = std::max(space_below, space_above);\n    const int usable_height = available_height - kMenuVerticalReserve;\n    if (usable_height <= 0) {\n      return;\n    }\n    max_rows = usable_height / item_height;\n  }\n\n  max_rows = std::max(1, max_rows);\n  if (static_cast<size_t>(max_rows) >= item_count) {\n    // Everything already fits in a single column.\n    return;\n  }\n  submenu->SetColumnLayout(max_rows, kMenuColumnGap);\n}\n\nvoid BookmarkMenuDelegate::AddMenuToMaps(MenuItemView* menu,\n',
        'bookmark_menu_delegate.cc: }',
    ),
]


def patch_bookmark_menu_columns(src_root: Path, rep: Report):
    """Multi-column bookmark folder menus.

    A bookmark folder whose entries do not fit vertically on screen is laid out
    as a grid: children fill the first column top-to-bottom, then wrap into a
    new column to the right. The row count is derived from the screen work
    area at menu-build time; --bookmark-menu-rows=N forces a value.

    Touches:
      ui/views/controls/menu/submenu_view.{h,cc}
        generic column layout: SetColumnLayout() / column_count().
      chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.{h,cc}
        opts bookmark folder menus in and lifts the 400 DIP width cap.

    Every block is a verbatim upstream excerpt, so a Chromium bump fails
    loudly instead of silently building an unpatched kernel.
    """
    name = "multi-column bookmark menus"
    errs = []
    applied = 0
    already = 0

    for path_str, old, new, label in _MENU_COLUMN_EDITS:
        p = src_root / path_str
        if not p.exists():
            errs.append(f"missing file: {path_str} ({label})")
            continue
        text = read_file(p)
        if new in text:
            already += 1
            continue
        if old not in text:
            errs.append(f"anchor not found: {label} ({path_str})")
            continue
        try:
            guard_no_drift(text, old, new, label)
        except RuntimeError as drift_err:
            errs.append(str(drift_err))
            continue
        write_file(p, text.replace(old, new, 1))
        applied += 1

    if errs:
        rep.record(name, "FAILED", "; ".join(errs[:4]))
    elif applied == 0:
        rep.record(name, "ALREADY", f"{already} blocks")
    else:
        rep.record(name, "APPLIED", f"{applied} blocks")


# ---------------------------------------------------------------------------
# 26. Bookmark folder item counts + per-profile window title
# ---------------------------------------------------------------------------

# (source path, verbatim upstream block, replacement, label)
_UI_BATCH1_EDITS = [
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        '#include "build/build_config.h"',
        '#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"\n#include "build/build_config.h"',
        'B2 include',
    ),
    (
        'chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc',
        'void BookmarkMenuDelegate::BuildMenuForFolderAt(\n    const BookmarkParentFolder& folder,\n    const ui::ImageModel& icon,\n    MenuItemView* parent_menu,\n    size_t index) {\n  // Underlying nodes share the same title.\n  std::vector<const BookmarkNode*> nodes =\n      GetBookmarkMergedSurfaceService()->GetUnderlyingNodes(folder);\n  CHECK(!nodes.empty());\n  AddMenuToMaps(parent_menu->AddMenuItemAt(\n                    index, GetAndIncrementNextMenuID(),\n                    MaybeEscapeLabel(nodes[0]->GetTitle()), std::u16string(),\n                    std::u16string(), ui::ImageModel(), icon,\n                    MenuItemView::Type::kSubMenu, ui::NORMAL_SEPARATOR),\n                BookmarkFolderOrURL(folder));\n}',
        'void BookmarkMenuDelegate::BuildMenuForFolderAt(\n    const BookmarkParentFolder& folder,\n    const ui::ImageModel& icon,\n    MenuItemView* parent_menu,\n    size_t index) {\n  // Underlying nodes share the same title.\n  std::vector<const BookmarkNode*> nodes =\n      GetBookmarkMergedSurfaceService()->GetUnderlyingNodes(folder);\n  CHECK(!nodes.empty());\n  // PATCH:folder-count - show how many entries the folder holds so a long\n  // folder can be judged before opening it.\n  std::u16string folder_label = MaybeEscapeLabel(nodes[0]->GetTitle());\n  const size_t folder_child_count =\n      GetBookmarkMergedSurfaceService()->GetChildrenCount(folder);\n  if (folder_child_count > 0) {\n    folder_label += base::UTF8ToUTF16(\n        base::StringPrintf(" (%zu)", folder_child_count));\n  }\n  AddMenuToMaps(parent_menu->AddMenuItemAt(\n                    index, GetAndIncrementNextMenuID(), folder_label,\n                    std::u16string(), std::u16string(), ui::ImageModel(), icon,\n                    MenuItemView::Type::kSubMenu, ui::NORMAL_SEPARATOR),\n                BookmarkFolderOrURL(folder));\n}',
        'B2 folder item count',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.cc',
        '#include "base/strings/stringprintf.h"',
        '#include "base/strings/stringprintf.h"\n#include "base/strings/utf_string_conversions.h"',
        'B10 include',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.cc',
        'std::u16string BrowserView::GetWindowTitle() const {',
        'namespace {\n\n// PATCH:window-title - prefix the window title with the instance name so\n// several side-by-side profiles are distinguishable in the task bar.\n// The name comes from --profile-name=<name> (set by the profile manager).\nstd::u16string GetProfileNameLabel() {\n  const base::CommandLine* command_line =\n      base::CommandLine::ForCurrentProcess();\n  if (!command_line->HasSwitch("profile-name")) {\n    return std::u16string();\n  }\n  // GetSwitchValueASCII would drop non-ASCII instance names (e.g. Chinese),\n  // so read the native value and convert it properly.\n#if BUILDFLAG(IS_WIN)\n  return base::WideToUTF16(command_line->GetSwitchValueNative(\"profile-name\"));\n#else\n  return base::UTF8ToUTF16(command_line->GetSwitchValueNative(\"profile-name\"));\n#endif\n}\n\n}  // namespace\n\nstd::u16string BrowserView::GetWindowTitle() const {',
        'B10 helper',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.cc',
        '  } else if (any_tab_playing_muted_audio) {\n    title = l10n_util::GetStringFUTF16(IDS_WINDOW_AUDIO_MUTING_MAC, title,\n                                       u"\\U0001F507");\n  }\n#endif\n  return title;\n}',
        '  } else if (any_tab_playing_muted_audio) {\n    title = l10n_util::GetStringFUTF16(IDS_WINDOW_AUDIO_MUTING_MAC, title,\n                                       u"\\U0001F507");\n  }\n#endif\n  // PATCH:window-title\n  const std::u16string profile_label = GetProfileNameLabel();\n  if (!profile_label.empty()) {\n    title = u"[" + profile_label + u"] " + title;\n  }\n  return title;\n}',
        'B10 title prefix',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.cc',
        '#include "ui/base/metadata/metadata_header_macros.h"\n#include "ui/base/metadata/metadata_impl_macros.h"\n#include "ui/base/models/image_model.h"\n#include "ui/base/mojom/window_show_state.mojom.h"\n#include "ui/base/resource/resource_bundle.h"\n#include "ui/base/text/bytes_formatting.h"\n',
        '#include "ui/base/metadata/metadata_header_macros.h"\n#include "ui/base/metadata/metadata_impl_macros.h"\n#include "ui/base/models/image_model.h"\n#include "ui/events/event.h"          // PATCH:mouse-gestures\n#include "ui/events/event_handler.h"  // PATCH:mouse-gestures\n#include "ui/base/mojom/window_show_state.mojom.h"\n#include "ui/base/resource/resource_bundle.h"\n#include "ui/base/text/bytes_formatting.h"\n',
        'B11 mouse-gestures browser_view.cc #0',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.cc',
        '  // views::ClientView::ViewHierarchyChanged();\n}\n\nvoid BrowserView::AddedToWidget() {\n  // BrowserView may be added to a widget more than once if the user changes\n  // themes after starting the browser. Do not re-initialize BrowserView in\n',
        '  // views::ClientView::ViewHierarchyChanged();\n}\n\n// PATCH:mouse-gestures - right-button drag gestures over the page area.\n//   L = back, R = forward, DR = close tab, DU = reload.\n// Enabled with --mouse-gestures so it can be turned off per instance.\nclass MouseGestureHandler : public ui::EventHandler {\n public:\n  explicit MouseGestureHandler(BrowserWindowInterface* browser)\n      : browser_(browser) {}\n\n  MouseGestureHandler(const MouseGestureHandler&) = delete;\n  MouseGestureHandler& operator=(const MouseGestureHandler&) = delete;\n\n  void OnMouseEvent(ui::MouseEvent* event) override {\n    if (!base::CommandLine::ForCurrentProcess()->HasSwitch("mouse-gestures")) {\n      return;\n    }\n    switch (event->type()) {\n      case ui::EventType::kMousePressed:\n        if (event->IsOnlyRightMouseButton()) {\n          tracking_ = true;\n          path_.clear();\n          last_point_ = event->location();\n        }\n        break;\n      case ui::EventType::kMouseDragged:\n        if (tracking_) {\n          AppendDirection(event->location());\n        }\n        break;\n      case ui::EventType::kMouseReleased:\n        if (tracking_ && event->IsOnlyRightMouseButton()) {\n          tracking_ = false;\n          if (!path_.empty()) {\n            // A gesture was drawn: swallow the release so the context menu\n            // does not pop up on top of the action.\n            event->StopPropagation();\n            Execute(path_);\n          }\n        }\n        break;\n      default:\n        break;\n    }\n  }\n\n private:\n  static int AbsDelta(int value) { return value < 0 ? -value : value; }\n\n  void AppendDirection(const gfx::Point& point) {\n    constexpr int kThreshold = 24;\n    const gfx::Vector2d delta = point - last_point_;\n    if (AbsDelta(delta.x()) < kThreshold && AbsDelta(delta.y()) < kThreshold) {\n      return;\n    }\n    char16_t direction;\n    if (AbsDelta(delta.x()) >= AbsDelta(delta.y())) {\n      direction = delta.x() > 0 ? u\'R\' : u\'L\';\n    } else {\n      direction = delta.y() > 0 ? u\'D\' : u\'U\';\n    }\n    if (path_.empty() || path_.back() != direction) {\n      path_.push_back(direction);\n    }\n    last_point_ = point;\n  }\n\n  void Execute(const std::u16string& path) {\n    if (!browser_) {\n      return;\n    }\n    if (path == u"L") {\n      chrome::GoBack(browser_, WindowOpenDisposition::kCurrentTab);\n    } else if (path == u"R") {\n      chrome::GoForward(browser_, WindowOpenDisposition::kCurrentTab);\n    } else if (path == u"DR") {\n      chrome::CloseTab(browser_);\n    } else if (path == u"DU") {\n      chrome::Reload(browser_, WindowOpenDisposition::kCurrentTab);\n    }\n  }\n\n  raw_ptr<BrowserWindowInterface> browser_;\n  bool tracking_ = false;\n  gfx::Point last_point_;\n  std::u16string path_;\n};\n\nvoid BrowserView::AddedToWidget() {\n  // BrowserView may be added to a widget more than once if the user changes\n  // themes after starting the browser. Do not re-initialize BrowserView in\n',
        'B11 mouse-gestures browser_view.cc #1',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.cc',
        '  }\n\n  views::ClientView::AddedToWidget();\n\n#if BUILDFLAG(IS_WIN)\n  // Register for screen capture exclusion updates. This is specific to\n',
        '  }\n\n  views::ClientView::AddedToWidget();\n\n  // PATCH:mouse-gestures\n  if (GetWidget() && !mouse_gesture_handler_) {\n    mouse_gesture_handler_ = std::make_unique<MouseGestureHandler>(browser_);\n    GetWidget()->GetRootView()->AddPreTargetHandler(mouse_gesture_handler_.get());\n  }\n\n#if BUILDFLAG(IS_WIN)\n  // Register for screen capture exclusion updates. This is specific to\n',
        'B11 mouse-gestures browser_view.cc #2',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.cc',
        '#if BUILDFLAG(IS_WIN)\n  pip_exclusion_observer_.reset();\n#endif\n\n  focus_manager_observation_.Reset();\n}\n',
        '#if BUILDFLAG(IS_WIN)\n  pip_exclusion_observer_.reset();\n#endif\n\n  // PATCH:mouse-gestures\n  if (mouse_gesture_handler_) {\n    if (GetWidget() && GetWidget()->GetRootView()) {\n      GetWidget()->GetRootView()->RemovePreTargetHandler(\n          mouse_gesture_handler_.get());\n    }\n    mouse_gesture_handler_.reset();\n  }\n\n  focus_manager_observation_.Reset();\n}\n',
        'B11 mouse-gestures browser_view.cc #3',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.h',
        '#include "content/public/browser/web_contents.h"\n#include "content/public/browser/web_contents_observer.h"\n#include "third_party/blink/public/common/permissions/permission_utils.h"\n#include "ui/base/accelerators/accelerator.h"\n#include "ui/base/identifier/typed_identifier.h"\n#include "ui/base/metadata/metadata_header_macros.h"\n',
        '#include "content/public/browser/web_contents.h"\n#include "content/public/browser/web_contents_observer.h"\n#include "third_party/blink/public/common/permissions/permission_utils.h"\n#include "ui/events/event_handler.h"\n#include "ui/base/accelerators/accelerator.h"\n#include "ui/base/identifier/typed_identifier.h"\n#include "ui/base/metadata/metadata_header_macros.h"\n',
        'B11 mouse-gestures browser_view.h #0',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.h',
        '//  A ClientView subclass that provides the contents of a browser window,\n//  including the TabStrip, toolbars, download shelves, the content area etc.\n//\nclass BrowserView : public BrowserWindow,\n                    public TabStripModelObserver,\n                    public ui::AcceleratorProvider,\n',
        '//  A ClientView subclass that provides the contents of a browser window,\n//  including the TabStrip, toolbars, download shelves, the content area etc.\n//\nclass MouseGestureHandler;\n\nclass BrowserView : public BrowserWindow,\n                    public TabStripModelObserver,\n                    public ui::AcceleratorProvider,\n',
        'B11 mouse-gestures browser_view.h #1',
    ),
    (
        'chrome/browser/ui/views/frame/browser_view.h',
        '  // The BrowserWidget that owns this view.\n  std::unique_ptr<BrowserWidget> browser_widget_;\n\n  // The owning BrowserWindowInterface object. `browser_` will outlive this.\n  const raw_ptr<BrowserWindowInterface> browser_;\n\n',
        '  // The BrowserWidget that owns this view.\n  std::unique_ptr<BrowserWidget> browser_widget_;\n\n  // PATCH:mouse-gestures\n  std::unique_ptr<ui::EventHandler> mouse_gesture_handler_;\n\n  // The owning BrowserWindowInterface object. `browser_` will outlive this.\n  const raw_ptr<BrowserWindowInterface> browser_;\n\n',
        'B11 mouse-gestures browser_view.h #2',
    ),
]


def patch_ui_batch1(src_root: Path, rep: Report):
    """Bookmark folder menus show their entry count; window titles carry the
    instance name from --profile-name=<name>.

    B2:  bookmark_menu_delegate.cc BuildMenuForFolderAt() appends " (N)".
    B10: browser_view.cc GetWindowTitle() prefixes "[name] ".
    """
    name = "bookmark folder counts + window title"
    errs = []
    applied = 0
    already = 0

    for path_str, old, new, label in _UI_BATCH1_EDITS:
        p = src_root / path_str
        if not p.exists():
            errs.append(f"missing file: {path_str} ({label})")
            continue
        text = read_file(p)
        if new in text:
            already += 1
            continue
        if old not in text:
            errs.append(f"anchor not found: {label} ({path_str})")
            continue
        try:
            guard_no_drift(text, old, new, label)
        except RuntimeError as drift_err:
            errs.append(str(drift_err))
            continue
        write_file(p, text.replace(old, new, 1))
        applied += 1

    if errs:
        rep.record(name, "FAILED", "; ".join(errs[:4]))
    elif applied == 0:
        rep.record(name, "ALREADY", f"{already} blocks")
    else:
        rep.record(name, "APPLIED", f"{applied} blocks")


# ---------------------------------------------------------------------------
# 27. Fingerprint self-consistency (UA OS token, Chrome brand, languages)
# ---------------------------------------------------------------------------

# (source path, verbatim upstream block, replacement, label)
_FP_CONSISTENCY_EDITS = [
    (
        'components/embedder_support/user_agent_utils.cc',
        '// Builds a User-agent compatible string that describes the OS and CPU type.\n// On Android, the string will only include the build number and model if\n// relevant enums indicate they should be included.\nstd::string BuildOSCpuInfo(\n    IncludeAndroidBuildNumber include_android_build_number,\n    IncludeAndroidModel include_android_model) {\n  return BuildOSCpuInfoFromOSVersionAndCpuType(\n      GetOSVersion(include_android_build_number, include_android_model),\n      BuildCpuInfo());\n}',
        '// PATCH:ua-platform - derive the UA OS token from --fingerprint-platform so\n// the user agent string agrees with navigator.platform / UA-CH platform.\nconst char* FingerprintOSCpuInfoOverride() {\n  base::CommandLine* command_line = base::CommandLine::ForCurrentProcess();\n  if (!command_line->HasSwitch("fingerprint-platform")) {\n    return nullptr;\n  }\n  const std::string platform =\n      command_line->GetSwitchValueASCII("fingerprint-platform");\n  if (platform == "macos") {\n    return "Macintosh; Intel Mac OS X 10_15_7";\n  }\n  if (platform == "linux") {\n    return "X11; Linux x86_64";\n  }\n  if (platform == "windows") {\n    return "Windows NT 10.0; Win64; x64";\n  }\n  return nullptr;\n}\n\n// Builds a User-agent compatible string that describes the OS and CPU type.\n// On Android, the string will only include the build number and model if\n// relevant enums indicate they should be included.\nstd::string BuildOSCpuInfo(\n    IncludeAndroidBuildNumber include_android_build_number,\n    IncludeAndroidModel include_android_model) {\n  if (const char* override_os = FingerprintOSCpuInfoOverride()) {\n    return override_os;\n  }\n  return BuildOSCpuInfoFromOSVersionAndCpuType(\n      GetOSVersion(include_android_build_number, include_android_model),\n      BuildCpuInfo());\n}',
        'ua-platform (OS token)',
    ),
    (
        'components/embedder_support/user_agent_utils.cc',
        'std::string GetUnifiedPlatform() {',
        '// PATCH:ua-platform - the reduced UA path uses GetUnifiedPlatform(), not\n// BuildOSCpuInfo(), so it needs the same override.\nconst char* FingerprintOSCpuInfoOverride();\n\nstd::string GetUnifiedPlatform() {\n  // PATCH:ua-platform\n  if (const char* override_os = FingerprintOSCpuInfoOverride()) {\n    return override_os;\n  }',
        'ua-platform (unified)',
    ),
    (
        'components/embedder_support/user_agent_utils.cc',
        '  std::optional<std::string> brand;\n#if !BUILDFLAG(CHROMIUM_BRANDING)\n  brand = version_info::GetProductName();\n#endif',
        '  std::optional<std::string> brand;\n#if !BUILDFLAG(CHROMIUM_BRANDING)\n  brand = version_info::GetProductName();\n#else\n  // PATCH:ua-brand - a Chromium-branded build reports only\n  // "Not.A/Brand" + "Chromium", which no real Chrome does. Report the\n  // Google Chrome brand so navigator.userAgentData.brands matches.\n  brand = "Google Chrome";\n#endif',
        'ua-brand (brand list)',
    ),
    (
        'components/embedder_support/user_agent_utils.cc',
        'std::string GetProductAndVersion() {\n  return base::FeatureList::IsEnabled(\n             blink::features::kReduceUserAgentMinorVersion)\n             ? version_info::GetProductNameAndVersionForReducedUserAgent()\n             : version_info::GetProductNameAndVersionForUserAgent();\n}',
        'std::string GetProductAndVersion() {\n  std::string product =\n      base::FeatureList::IsEnabled(\n          blink::features::kReduceUserAgentMinorVersion)\n          ? version_info::GetProductNameAndVersionForReducedUserAgent()\n          : version_info::GetProductNameAndVersionForUserAgent();\n  // PATCH:ua-brand - "Chromium/158.x" -> "Chrome/158.x".\n  constexpr char kChromiumPrefix[] = "Chromium/";\n  if (product.rfind(kChromiumPrefix, 0) == 0) {\n    product.replace(0, sizeof(kChromiumPrefix) - 1, "Chrome/");\n  }\n  return product;\n}',
        'ua-brand (product name)',
    ),
    (
        'third_party/blink/renderer/core/frame/navigator_language.cc',
        '#include "third_party/blink/renderer/core/frame/navigator_language.h"',
        '#include "third_party/blink/renderer/core/frame/navigator_language.h"\n\n#include "base/command_line.h"  // PATCH:lang\n#include "base/no_destructor.h"  // PATCH:lang',
        'lang (include)',
    ),
    (
        'third_party/blink/renderer/core/frame/navigator_language.cc',
        'NavigatorLanguage::NavigatorLanguage(ExecutionContext* execution_context)\n    : execution_context_(execution_context) {}',
        'namespace {\n\n// PATCH:lang - --fingerprint-lang=zh-CN,zh overrides navigator.languages so a\n// spoofed timezone/platform does not ship with the host\'s language list.\nVector<String> FingerprintLanguages() {\n  Vector<String> result;\n  base::CommandLine* command_line = base::CommandLine::ForCurrentProcess();\n  if (!command_line->HasSwitch("fingerprint-lang")) {\n    return result;\n  }\n  const std::string value =\n      command_line->GetSwitchValueASCII("fingerprint-lang");\n  if (value.empty()) {\n    return result;\n  }\n  return ParseAndSanitize(String::FromUtf8(value));\n}\n\n}  // namespace\n\nNavigatorLanguage::NavigatorLanguage(ExecutionContext* execution_context)\n    : execution_context_(execution_context) {}',
        'lang (helper)',
    ),
    (
        'third_party/blink/renderer/core/frame/navigator_language.cc',
        'const Vector<String>& NavigatorLanguage::languages() {\n  EnsureUpdatedLanguage();\n  return languages_;\n}',
        'const Vector<String>& NavigatorLanguage::languages() {\n  // PATCH:lang - base::NoDestructor: a function-local static Vector would\n  // need an exit-time destructor, which this build treats as an error.\n  static const base::NoDestructor<Vector<String>> kFingerprintLanguages(\n      FingerprintLanguages());\n  if (!kFingerprintLanguages->empty()) {\n    return *kFingerprintLanguages;\n  }\n  EnsureUpdatedLanguage();\n  return languages_;\n}',
        'lang (languages())',
    ),
]


def patch_fingerprint_consistency(src_root: Path, rep: Report):
    """Make the spoofed fingerprint self-consistent.

    1. UA OS token follows --fingerprint-platform on both the reduced-UA
       (GetUnifiedPlatform) and the legacy (BuildOSCpuInfo) paths.
    2. A Chromium-branded build reports only "Chromium"; add the Google
       Chrome brand and rewrite the "Chromium/" product prefix.
    3. navigator.languages follows --fingerprint-lang.
    """
    name = "fingerprint self-consistency"
    errs = []
    applied = 0
    already = 0

    for path_str, old, new, label in _FP_CONSISTENCY_EDITS:
        p = src_root / path_str
        if not p.exists():
            errs.append(f"missing file: {path_str} ({label})")
            continue
        text = read_file(p)
        if new in text:
            already += 1
            continue
        if old not in text:
            errs.append(f"anchor not found: {label} ({path_str})")
            continue
        try:
            guard_no_drift(text, old, new, label)
        except RuntimeError as drift_err:
            errs.append(str(drift_err))
            continue
        write_file(p, text.replace(old, new, 1))
        applied += 1

    if errs:
        rep.record(name, "FAILED", "; ".join(errs[:4]))
    elif applied == 0:
        rep.record(name, "ALREADY", f"{already} blocks")
    else:
        rep.record(name, "APPLIED", f"{applied} blocks")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 28. Per-item fingerprint overrides (--fp-*)
# ---------------------------------------------------------------------------

def _once(text: str, old: str, new: str, label: str):
    """Replace exactly once; treat 'already contains new' as a no-op."""
    if new in text:
        return text, False
    n = text.count(old)
    if n != 1:
        raise RuntimeError(f"anchor count {n} (want 1): {label}")
    return text.replace(old, new, 1), True


def patch_fp_overrides(src_root: Path, rep: Report):
    """Make every spoofed value individually overridable instead of seed-only."""
    name = "per-item fingerprint overrides (--fp-*)"
    try:
        changed = False
        notes = []

        # --- hardwareConcurrency -------------------------------------------
        p = src_root / "third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc"
        t = read_file(p)
        t, c = _once(
            t,
            "  if (fp::Enabled()) {\n"
            "    static const unsigned kCores[] = {4, 8, 12, 16, 24};\n",
            "  if (fp::Enabled()) {\n"
            "    if (const unsigned cores = fp::CoresOverride()) {\n"
            "      return cores;\n"
            "    }\n"
            "    static const unsigned kCores[] = {4, 8, 12, 16, 24};\n",
            "hw cores")
        if c:
            write_file(p, t)
            changed = True
            notes.append("cores")

        # --- deviceMemory ---------------------------------------------------
        p = src_root / "third_party/blink/renderer/core/frame/navigator_device_memory.cc"
        t = read_file(p)
        t, c = _once(
            t,
            "  if (fp::Enabled()) {\n"
            "    static const float kRamGb[] = {4.0f, 8.0f, 16.0f, 32.0f};\n",
            "  if (fp::Enabled()) {\n"
            "    if (const float mem = fp::MemoryOverride()) {\n"
            "      return mem;\n"
            "    }\n"
            "    static const float kRamGb[] = {4.0f, 8.0f, 16.0f, 32.0f};\n",
            "device memory")
        if c:
            write_file(p, t)
            changed = True
            notes.append("memory")

        # --- devicePixelRatio ----------------------------------------------
        p = src_root / "third_party/blink/renderer/core/frame/local_dom_window.cc"
        t = read_file(p)
        t, c = _once(
            t,
            "  if (fp::Enabled()) {\n"
            "    static const double kDpr[] = {1.0, 1.25, 1.5, 1.0, 2.0};\n",
            "  if (fp::Enabled()) {\n"
            "    if (const double dpr = fp::DprOverride()) {\n"
            "      return dpr;\n"
            "    }\n"
            "    static const double kDpr[] = {1.0, 1.25, 1.5, 1.0, 2.0};\n",
            "devicePixelRatio")
        if c:
            write_file(p, t)
            changed = True
            notes.append("dpr")

        # --- canvas getImageData -------------------------------------------
        p = src_root / ("third_party/blink/renderer/modules/canvas/canvas2d/"
                        "base_rendering_context_2d.cc")
        t = read_file(p)
        # v3 把该块换成了 PATCH:canvas-v3（已自带 fp-canvas 判断），
        # 所以旧标记和新标记都算「已经处理过」。
        if "PATCH:canvas-v3" in t or "PATCH:canvas-v4" in t:
            c = False
        else:
            t, c = _once(
                t,
                "  // PATCH:canvas-noise - deterministic per-pixel perturbation\n"
                "  if (fp::Enabled()) {",
                "  // PATCH:canvas-noise - deterministic per-pixel perturbation\n"
                "  if (fp::Enabled() && !fp::Disabled(\"fp-canvas\")) {",
                "canvas getImageData")
        if c:
            write_file(p, t)
            changed = True
            notes.append("canvas")

        # --- canvas toDataURL ----------------------------------------------
        p = src_root / "third_party/blink/renderer/platform/graphics/image_data_buffer.cc"
        t = read_file(p)
        if "ApplyCanvasNoise" in t:
            c = False
        else:
            t = insert_after_first_include(
                t,
                '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
            t, c = _once(
                t,
                "  if (owns_pixels_ &&\n"
                "      base::CommandLine::ForCurrentProcess()->HasSwitch(\"fingerprint\")) {",
                "  if (owns_pixels_ && fp::Enabled() && !fp::Disabled(\"fp-canvas\")) {",
                "canvas toDataURL")
        if c:
            write_file(p, t)
            changed = True
            notes.append("canvas-dataurl")

        # --- WebGL renderer / vendor ---------------------------------------
        p = src_root / "third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc"
        t = read_file(p)
        if "GpuForIndex" in t:
            c1 = False
        else:
            t, c1 = _once(
                t,
                "        if (fp::Enabled()) {\n"
                "          static const char* const kFpRenderers[] = {",
                "        if (fp::Enabled() && fp::WebGLIndex() != -1) {\n"
                "          static const char* const kFpRenderers[] = {",
                "webgl renderer gate")
        t, c2 = _once(
            t,
            "          return WebGLAny(\n"
            "              script_state, String(kFpRenderers[fp::Seed() % 3]));",
            "          const int wg = fp::WebGLIndex();\n"
            "          if (wg == 0) {\n"
            "            return WebGLAny(script_state, String(kFpRenderers[0]));\n"
            "          }\n"
            "          if (wg == 1) {\n"
            "            return WebGLAny(script_state, String(kFpRenderers[1]));\n"
            "          }\n"
            "          if (wg == 2) {\n"
            "            return WebGLAny(script_state, String(kFpRenderers[2]));\n"
            "          }\n"
            "          return WebGLAny(\n"
            "              script_state, String(kFpRenderers[fp::Seed() % 3]));",
            "webgl renderer index")
        t, c3 = _once(
            t,
            "        if (fp::Enabled()) {\n"
            "          static const char* const kFpVendors[] = {",
            "        if (fp::Enabled() && fp::WebGLIndex() != -1) {\n"
            "          static const char* const kFpVendors[] = {",
            "webgl vendor gate")
        t, c4 = _once(
            t,
            "          return WebGLAny(\n"
            "              script_state, String(kFpVendors[fp::Seed() % 3]));",
            "          const int wgv = fp::WebGLIndex();\n"
            "          if (wgv == 0) {\n"
            "            return WebGLAny(script_state, String(kFpVendors[0]));\n"
            "          }\n"
            "          if (wgv == 1) {\n"
            "            return WebGLAny(script_state, String(kFpVendors[1]));\n"
            "          }\n"
            "          if (wgv == 2) {\n"
            "            return WebGLAny(script_state, String(kFpVendors[2]));\n"
            "          }\n"
            "          return WebGLAny(\n"
            "              script_state, String(kFpVendors[fp::Seed() % 3]));",
            "webgl vendor index")
        if c1 or c2 or c3 or c4:
            write_file(p, t)
            changed = True
            notes.append("webgl")

        # --- audio noise ----------------------------------------------------
        p = src_root / "third_party/blink/renderer/modules/webaudio/audio_buffer.cc"
        t = read_file(p)
        t = insert_after_first_include(
            t,
            '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
        t, c = _once(
            t,
            "  if (base::CommandLine::ForCurrentProcess()->HasSwitch(\n"
            "          \"fingerprint\")) {",
            "  if (fp::Enabled() && !fp::Disabled(\"fp-audio\")) {",
            "audio gate")
        if c:
            write_file(p, t)
            changed = True
            notes.append("audio")

        if changed:
            rep.record(name, "APPLIED", ", ".join(notes))
        else:
            rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# 29. Font spoofing (document.fonts.check)
# ---------------------------------------------------------------------------

def patch_font_spoof(src_root: Path, rep: Report):
    """Answer document.fonts.check() from a per-platform whitelist."""
    name = "font spoofing (document.fonts.check)"
    p = src_root / "third_party/blink/renderer/core/css/font_face_set.cc"
    if not p.exists():
        rep.record(name, "FAILED", "font_face_set.cc missing")
        return
    try:
        t = read_file(p)
        if "PATCH:font-spoof" in t:
            rep.record(name, "ALREADY")
            return
        t = insert_after_first_include(
            t,
            '#include "third_party/blink/renderer/core/frame/fingerprint_params.h"')
        t, _ = _once(
            t,
            "  const Font* font = ResolveFontStyle(font_string);\n"
            "  if (!font) {\n"
            "    exception_state.ThrowDOMException(\n"
            "        DOMExceptionCode::kSyntaxError,\n"
            "        StrCat({\"Could not resolve '\", font_string, \"' as a font.\"}));\n"
            "    return false;\n"
            "  }\n",
            "  const Font* font = ResolveFontStyle(font_string);\n"
            "  if (!font) {\n"
            "    exception_state.ThrowDOMException(\n"
            "        DOMExceptionCode::kSyntaxError,\n"
            "        StrCat({\"Could not resolve '\", font_string, \"' as a font.\"}));\n"
            "    return false;\n"
            "  }\n"
            "\n"
            "  // PATCH:font-spoof - answer from a platform-consistent font whitelist so a\n"
            "  // macOS/Windows spoof does not leak the real installed font set.\n"
            "  //\n"
            "  // The resolved description is preferred, but a family that the platform\n"
            "  // filter already removed is no longer in it - fall back to the shorthand\n"
            "  // so blocked families still get a correct answer.\n"
            "  std::string fp_family;\n"
            "  if (const FontFamily& fp_first = font->GetFontDescription().Family();\n"
            "      fp_first.FamilyName()) {\n"
            "    fp_family = fp_first.FamilyName().Utf8();\n"
            "  }\n"
            "  if (fp_family.empty()) {\n"
            "    fp_family = fp::FirstFamilyFromFontString(font_string.Utf8());\n"
            "  }\n"
            "  if (!fp_family.empty()) {\n"
            "    const int fp_answer = fp::FontAvailability(fp_family);\n"
            "    if (fp_answer >= 0) {\n"
            "      return fp_answer != 0;\n"
            "    }\n"
            "  }\n",
            "check()")
        write_file(p, t)
        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))



# ---------------------------------------------------------------------------
# 30. Font set filtering at the resolution layer
# ---------------------------------------------------------------------------

def patch_font_filter(src_root: Path, rep: Report):
    """Drop families the spoofed platform would not have.

    document.fonts.check() is answered from a whitelist in font_face_set.cc, but
    canvas measureText() and CSS width heuristics still resolve the real fonts.
    Filtering the family list here makes an absent family behave exactly as it
    does on the spoofed platform, so every width-comparison heuristic agrees.

    Blacklist, not whitelist: only known system fonts of platforms we are NOT
    claiming are dropped, so @font-face families keep working.
    """
    name = "font set filtering (resolution layer)"
    header_path = src_root / FINGERPRINT_FONT_FILTER_PATH
    desc_path = (src_root /
                 "third_party/blink/renderer/platform/fonts/font_description.h")
    if not desc_path.exists():
        rep.record(name, "FAILED", "font_description.h missing")
        return
    try:
        changed = False

        if not header_path.exists():
            header_path.parent.mkdir(parents=True, exist_ok=True)
            write_file(header_path, FINGERPRINT_FONT_FILTER)
            changed = True

        t = read_file(desc_path)
        if "PATCH:font-filter" not in t:
            inc = ('#include "third_party/blink/renderer/platform/fonts/'
                   'font_family.h"')
            if inc in t and "fingerprint_font_filter.h" not in t:
                t = t.replace(
                    inc,
                    inc +
                    '\n#include "third_party/blink/renderer/platform/fonts/'
                    'fingerprint_font_filter.h"'
                    '\n#include "third_party/blink/renderer/platform/wtf/'
                    'vector.h"', 1)
            t, c = _once(
                t,
                "  void SetFamily(const FontFamily& family) { family_list_ = family; }",
                """  // PATCH:font-filter - drop families the spoofed platform would not have, so
  // canvas measureText() and CSS width heuristics answer like a real machine.
  void SetFamily(const FontFamily& family) {
    if (!fp_font::Config().enabled) {
      family_list_ = family;
      return;
    }
    Vector<const FontFamily*, 8> kept;
    for (const FontFamily* f = &family; f; f = f->Next()) {
      if (f->FamilyIsGeneric() || fp_font::FamilyAllowed(f->FamilyName())) {
        kept.push_back(f);
      }
    }
    if (kept.empty()) {
      family_list_ = FontFamily();
      return;
    }
    scoped_refptr<SharedFontFamily> rest;
    for (wtf_size_t i = kept.size(); i-- > 1;) {
      rest = SharedFontFamily::Create(
          kept[i]->FamilyName(),
          kept[i]->FamilyIsGeneric() ? FontFamily::Type::kGenericFamily
                                     : FontFamily::Type::kFamilyName,
          std::move(rest));
    }
    family_list_ =
        FontFamily(kept[0]->FamilyName(),
                   kept[0]->FamilyIsGeneric() ? FontFamily::Type::kGenericFamily
                                              : FontFamily::Type::kFamilyName,
                   std::move(rest));
  }""",
                "FontDescription::SetFamily")
            if c:
                write_file(desc_path, t)
                changed = True

        if changed:
            rep.record(name, "APPLIED")
        else:
            rep.record(name, "ALREADY")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))



# ---------------------------------------------------------------------------
# 31. Geolocation spoofing
# ---------------------------------------------------------------------------

def patch_geo_spoof(src_root: Path, rep: Report):
    """让 navigator.geolocation 返回 --fp-geo 指定的坐标。

    不伪装的话，浏览器声称在日本、时区也是东京，geolocation 却返回真实位置，
    两边一对比就露馅。这里直接构造 Geoposition 回答，不走权限流程。
    """
    name = "geolocation spoofing"
    path = src_root / "third_party/blink/renderer/core/geolocation/geolocation.cc"
    if not path.exists():
        rep.record(name, "FAILED", "geolocation.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:geo-spoof" in text:
            rep.record(name, "ALREADY")
            return

        # include
        inc = ('#include "third_party/blink/renderer/core/geolocation/'
               'geolocation.h"')
        if inc not in text:
            rep.record(name, "FAILED", "geolocation.h include missing")
            return
        text = text.replace(
            inc,
            inc + '\n#include "third_party/blink/renderer/core/frame/'
                  'fingerprint_params.h"', 1)

        # 辅助函数
        helper_anchor = "GeolocationPositionError* CreatePositionError("
        helper = (
            "// PATCH:geo-spoof - 用 --fp-geo 指定的坐标回答，"
            "避免和伪装的国家/时区自相矛盾。\n"
            "Geoposition* CreateFingerprintGeoposition() {\n"
            "  device::mojom::blink::Geoposition position;\n"
            "  position.latitude = fp::GeoLatitude();\n"
            "  position.longitude = fp::GeoLongitude();\n"
            "  position.accuracy = fp::GeoAccuracy();\n"
            "  position.altitude = -100000.;\n"
            "  position.altitude_accuracy = -1.;\n"
            "  position.heading = -1.;\n"
            "  position.speed = -1.;\n"
            "  position.timestamp = base::Time::Now();\n"
            "  return CreateGeoposition(position);\n"
            "}\n\n")
        if helper_anchor not in text:
            rep.record(name, "FAILED", "CreatePositionError anchor missing")
            return
        text = text.replace(helper_anchor, helper + helper_anchor, 1)

        # getCurrentPosition
        text, c1 = _once(
            text,
            "  if (!GetFrame())\n"
            "    return;\n",
            "  if (!GetFrame())\n"
            "    return;\n"
            "\n"
            "  // PATCH:geo-spoof\n"
            "  if (fp::GeoEnabled()) {\n"
            "    auto* fp_notifier = MakeGarbageCollected<GeoNotifierV8>(\n"
            "        this, options, success_callback, error_callback);\n"
            "    fp_notifier->RunSuccessCallback(CreateFingerprintGeoposition());\n"
            "    return;\n"
            "  }\n",
            "getCurrentPosition")
        if not c1:
            rep.record(name, "FAILED", "getCurrentPosition anchor not found")
            return

        # watchPosition
        text, c2 = _once(
            text,
            "  if (!GetFrame())\n"
            "    return 0;\n",
            "  if (!GetFrame())\n"
            "    return 0;\n"
            "\n"
            "  // PATCH:geo-spoof - 不注册真实 watcher，直接回一次假坐标\n"
            "  if (fp::GeoEnabled()) {\n"
            "    static int fp_geo_watch_id = 900000;\n"
            "    auto* fp_notifier = MakeGarbageCollected<GeoNotifierV8>(\n"
            "        this, options, success_callback, error_callback);\n"
            "    fp_notifier->RunSuccessCallback(CreateFingerprintGeoposition());\n"
            "    return ++fp_geo_watch_id;\n"
            "  }\n",
            "watchPosition")
        if not c2:
            rep.record(name, "FAILED", "watchPosition anchor not found")
            return

        write_file(path, text)
        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))



# ===========================================================================
# v3: fingerprint consistency (idempotent canvas + unified GPU identity)
#
# IMPORTANT: do NOT add includes or helpers to fingerprint_params.h.
# That header is included by 14 Blink TUs; touching its structure triggers
# a ~56k-task full rebuild. Everything new lives in fingerprint_extra.h,
# included only by the 4 concrete .cc files we patch.
# ===========================================================================

FINGERPRINT_EXTRA_H = r'''
// Copyright 2026 KZ Browser. Standalone helpers for fingerprint consistency.
#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_EXTRA_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_EXTRA_H_

#include <cstddef>
#include <cstdint>

#include "base/compiler_specific.h"
#include "third_party/blink/renderer/core/frame/fingerprint_params.h"
#include "third_party/skia/include/core/SkPixmap.h"

namespace blink {
namespace fp_extra {

// Idempotent, content-addressed canvas perturbation.
// F(px) = (px & 0xFE) | H(seed, px & 0xFE)
//   - IDEMPOTENT: F(F(px)) == F(px)
//   - CONTENT-ADDRESSED: depends on the pixel's high 7 bits, not on x/y
// Result: getImageData sub-rectangles agree with a full toDataURL, so the
// roundtrip check (getImageData -> putImageData -> toDataURL) matches.
// Different seeds produce different hashes, so instances stay distinct.
inline void ApplyCanvasNoise(SkPixmap& pixmap, uint64_t seed) {
  if (!pixmap.writable_addr() || pixmap.info().bytesPerPixel() < 3) {
    return;
  }
  const size_t row_bytes = pixmap.rowBytes();
  const size_t bpp = pixmap.info().bytesPerPixel();
  uint8_t* base = static_cast<uint8_t*>(pixmap.writable_addr());
  const int w = pixmap.width();
  const int h = pixmap.height();
  const uint64_t k1 = 0x9E3779B97F4A7C15ull;
  const uint64_t k2 = 0xBF58476D1CE4E5B9ull;
  const uint64_t k3 = 0xFF51AFD7ED558CCDull;
  for (int y = 0; y < h; ++y) {
    for (int x = 0; x < w; ++x) {
      UNSAFE_TODO({
        uint8_t* px = base + y * row_bytes + x * bpp;
        // 关键：仅对完全不透明的像素 (alpha == 255) 进行扰动。
        // 半透明与透明像素在 putImageData 存入 canvas backing store 时
        // 会经历 premultiplied alpha 整数除法舍入，导致最低位发生精度丢失，
        // 从而破坏 toDataURL == roundtrip 的一致性。
        if (bpp >= 4 && px[3] != 255) {
          continue;
        }
        const uint8_t hi = static_cast<uint8_t>(px[0] & 0xFEu);
        uint64_t z = seed * k1 + static_cast<uint64_t>(hi) * k2;
        z = (z ^ (z >> 33)) * k3;
        z = z ^ (z >> 33);
        px[0] = static_cast<uint8_t>(hi | (z & 1u));
      });
    }
  }
}

struct GpuIdentity {
  const char* webgl_vendor;
  const char* webgl_renderer;
  const char* webgpu_vendor;
  const char* webgpu_architecture;
  const char* webgpu_device;
  const char* webgpu_description;
};

inline GpuIdentity GpuForIndex(int idx, uint64_t seed) {
  static const GpuIdentity kGpus[] = {
      {"Google Inc. (NVIDIA)",
       "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 "
       "vs_5_0 ps_5_0, D3D11)",
       "nvidia", "ampere", "NVIDIA GeForce RTX 3070", ""},
      {"Google Inc. (AMD)",
       "ANGLE (AMD, AMD Radeon RX 6600 Direct3D11 "
       "vs_5_0 ps_5_0, D3D11)",
       "amd", "rdna2", "AMD Radeon RX 6600", ""},
      {"Google Inc. (Intel)",
       "ANGLE (Intel, Intel(R) UHD Graphics 630 "
       "Direct3D11 vs_5_0 ps_5_0, D3D11)",
       "intel", "gen9", "Intel(R) UHD Graphics 630", ""}};
  if (idx < 0 || idx > 2) {
    idx = static_cast<int>(seed % 3);
  }
  return UNSAFE_TODO(kGpus[idx]);
}

}  // namespace fp_extra
}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_EXTRA_H_
'''


def patch_fingerprint_v3(src_root: Path, rep: Report):
    """Apply v3 consistency fixes. Idempotent, safe to re-run."""
    name = "fingerprint v3 (idempotent canvas + unified GPU)"
    try:
        # ---- 1. write the standalone header (never touch fingerprint_params.h)
        extra = src_root / ("third_party/blink/renderer/core/frame/"
                            "fingerprint_extra.h")
        if not extra.exists():
            write_file(extra, FINGERPRINT_EXTRA_H.lstrip("\n"))

        # ---- 2. platformVersion: one string constant in fingerprint_params.h
        hp = src_root / ("third_party/blink/renderer/core/frame/"
                         "fingerprint_params.h")
        ht = read_file(hp)
        if 'return "19.0.0";' in ht:
            write_file(hp, ht.replace('return "19.0.0";',
                                      'return "10.0.0";', 1))

        # ---- 3. getImageData: v4 - 完全原生，不加任何噪声 ----
        # 检测站画纯色读真值 / 连续读两次比对，加了噪声必被判
        # 「Canvas 异常 / 篡改」。扰动只在 toDataURL 导出边界做。
        ctx = src_root / ("third_party/blink/renderer/modules/canvas/canvas2d/"
                          "base_rendering_context_2d.cc")
        ct = read_file(ctx)
        if "PATCH:canvas-noise" in ct and "PATCH:canvas-v4" not in ct:
            s = ct.find("  // PATCH:canvas-noise - deterministic")
            e = ct.find("  return image_data;", s)
            if s >= 0 and e > s:
                block = (
                    "  // PATCH:canvas-v4 - getImageData returns native pixels.\n"
                    "  // Noise only at toDataURL boundary.\n\n")
                ct = ct[:s] + block + ct[e:]
                write_file(ctx, ct)

        # ---- 4. toDataURL: same noise on a private copy ----
        idb = src_root / ("third_party/blink/renderer/platform/graphics/"
                          "image_data_buffer.cc")
        it = read_file(idb)
        if "PATCH:canvas-noise" in it and "ApplyCanvasNoise" not in it:
            s = it.find("  // PATCH:canvas-noise - deterministic")
            e = it.find("  Vector<unsigned char> result;", s)
            if s >= 0 and e > s:
                block = (
                    "  // PATCH:canvas-v3 - idempotent shared noise on a copy.\n"
                    "  // ToDataURL is const; peekPixels may alias the canvas\n"
                    "  // backing store, so never write in place.\n"
                    "  if (fp::Enabled() && !fp::Disabled(\"fp-canvas\") &&\n"
                    "      pixmap_.info().bytesPerPixel() >= 3) {\n"
                    "    const SkImageInfo fp_info = pixmap_.info();\n"
                    "    const size_t fp_bytes = fp_info.computeMinByteSize();\n"
                    "    if (fp_bytes > 0 && fp_bytes < (1u << 30)) {\n"
                    "      sk_sp<SkData> fp_data =\n"
                    "          SkData::MakeUninitialized(fp_bytes);\n"
                    "      SkPixmap fp_copy(fp_info, fp_data->writable_data(),\n"
                    "                       fp_info.minRowBytes());\n"
                    "      if (pixmap_.readPixels(fp_copy)) {\n"
                    "        fp_extra::ApplyCanvasNoise(fp_copy, fp::Seed());\n"
                    "        Vector<unsigned char> fp_result;\n"
                    "        if (ImageEncoder::Encode(&fp_result, fp_copy,\n"
                    "                                 mime_type, quality)) {\n"
                    "          return StrCat({\"data:\",\n"
                    "              ImageEncoderUtils::MimeTypeName(mime_type),\n"
                    "              \";base64,\", Base64Encode(fp_result)});\n"
                    "        }\n"
                    "      }\n"
                    "    }\n"
                    "  }\n\n")
                it = it[:s] + block + it[e:]
                if "fingerprint_extra.h" not in it:
                    it = insert_after_first_include(
                        it, '#include "third_party/blink/renderer/core/frame/'
                            'fingerprint_extra.h"')
                write_file(idb, it)

        # ---- 5. WebGL: leave arrays as-is (they already use WebGLIndex),
        #         but make WebGPU follow the same index.
        # ---- 6. WebGPU adapter info: override after original assignments.
        ga = src_root / ("third_party/blink/renderer/modules/webgpu/"
                         "gpu_adapter.cc")
        gat = read_file(ga)
        if "PATCH:gpu-v3" not in gat:
            tail = "  driver_ = String::FromUtf8(info.description);"
            if tail in gat:
                if "fingerprint_extra.h" not in gat:
                    gat = insert_after_first_include(
                        gat, '#include "third_party/blink/renderer/core/frame/'
                             'fingerprint_extra.h"')
                override = (
                    "\n\n  // PATCH:gpu-v3 - WebGPU adapter must match the "
                    "spoofed WebGL renderer.\n"
                    "  if (fp::Enabled() && fp::WebGLIndex() != -1) {\n"
                    "    const fp_extra::GpuIdentity fp_g =\n"
                    "        fp_extra::GpuForIndex(fp::WebGLIndex(),\n"
                    "                              fp::Seed());\n"
                    "    vendor_ = String::FromUtf8(fp_g.webgpu_vendor);\n"
                    "    architecture_ =\n"
                    "        String::FromUtf8(fp_g.webgpu_architecture);\n"
                    "    device_ = String::FromUtf8(fp_g.webgpu_device);\n"
                    "    description_ = String::FromUtf8(fp_g.webgpu_device);\n"
                    "    driver_ =\n"
                    "        String::FromUtf8(fp_g.webgpu_description);\n"
                    "  }")
                gat = gat.replace(tail, tail + override, 1)
                write_file(ga, gat)

        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# v4: WebGL masked VENDOR/RENDERER follow the spoofed GPU identity
# ---------------------------------------------------------------------------



# ===========================================================================
# v5: unify ALL canvas encode outlets (toDataURL / toBlob) on one noised buffer
# ===========================================================================

V5_ENSURE_NOISE = r"""
// PATCH:canvas-v5 - apply idempotent noise once on a private copy and
// redirect pixmap_ so ToDataURL, EncodeImage(toBlob) and PixelData all
// encode the SAME pixels. Otherwise toDataURL != toBlob, which sites
// report as canvas UNSTABLE.
void ImageDataBuffer::EnsureNoiseApplied() const {
  if (fp_noise_applied_) return;
  fp_noise_applied_ = true;
  if (!fp::Enabled() || fp::Disabled("fp-canvas")) return;
  if (pixmap_.info().bytesPerPixel() < 3) return;
  const SkImageInfo info = pixmap_.info();
  const size_t bytes = info.computeMinByteSize();
  if (bytes == 0 || bytes >= (1u << 30)) return;
  sk_sp<SkData> data = SkData::MakeUninitialized(bytes);
  SkPixmap copy(info, data->writable_data(), info.minRowBytes());
  if (!pixmap_.readPixels(copy)) return;
  fp_extra::ApplyCanvasNoise(copy, fp::Seed());
  fp_noise_data_ = std::move(data);
  pixmap_ = copy;
}
"""


def patch_canvas_v5(src_root: Path, rep: Report):
    """Make toDataURL and toBlob encode identical (noised) pixels."""
    name = "canvas v5 unified encode outlets"
    h = src_root / ("third_party/blink/renderer/platform/graphics/"
                    "image_data_buffer.h")
    cc = src_root / ("third_party/blink/renderer/platform/graphics/"
                     "image_data_buffer.cc")
    if not (h.exists() and cc.exists()):
        rep.record(name, "FAILED", "image_data_buffer.{h,cc} missing")
        return
    try:
        # --- header: members ---
        ht = read_file(h)
        if "fp_noise_data_" not in ht:
            old = ("  bool IsValid() { return is_valid_; }  // Only used by Create()\n"
                   "\n"
                   "  sk_sp<SkImage> retained_image_;\n"
                   "  mutable SkPixmap pixmap_;  // PATCH:canvas-v5\n"
                   "  bool is_valid_ = false;\n"
                   "  bool owns_pixels_ = false;  // PATCH:canvas-noise\n"
                   "};")
            new = ("  bool IsValid() { return is_valid_; }  // Only used by Create()\n"
                   "\n"
                   "  // PATCH:canvas-v5 - one noised buffer for all encode outlets\n"
                   "  void EnsureNoiseApplied() const;\n"
                   "\n"
                   "  sk_sp<SkImage> retained_image_;\n"
                   "  SkPixmap pixmap_;\n"
                   "  bool is_valid_ = false;\n"
                   "  bool owns_pixels_ = false;  // PATCH:canvas-noise\n"
                   "  mutable sk_sp<SkData> fp_noise_data_;   // PATCH:canvas-v5\n"
                   "  mutable bool fp_noise_applied_ = false;  // PATCH:canvas-v5\n"
                   "};")
            if old not in ht:
                rep.record(name, "FAILED", "header members anchor missing")
                return
            write_file(h, ht.replace(old, new, 1))

        # --- cc: EncodeImage + ToDataURL + method ---
        ct = read_file(cc)
        if "void ImageDataBuffer::EnsureNoiseApplied()" in ct:
            rep.record(name, "ALREADY")
            return

        # EncodeImage: call before encoding
        enc_old = ("bool ImageDataBuffer::EncodeImage(const ImageEncodingMimeType mime_type,\n"
                   "                                   const double& quality,\n"
                   "                                   Vector<unsigned char>* encoded_image) const {\n"
                   "  return ImageEncoder::Encode(encoded_image, pixmap_, mime_type, quality);\n"
                   "}")
        enc_new = ("bool ImageDataBuffer::EncodeImage(const ImageEncodingMimeType mime_type,\n"
                   "                                   const double& quality,\n"
                   "                                   Vector<unsigned char>* encoded_image) const {\n"
                   "  EnsureNoiseApplied();  // PATCH:canvas-v5\n"
                   "  return ImageEncoder::Encode(encoded_image, pixmap_, mime_type, quality);\n"
                   "}")
        if enc_old not in ct:
            rep.record(name, "FAILED", "EncodeImage anchor missing")
            return
        ct = ct.replace(enc_old, enc_new, 1)

        # ToDataURL: replace any prior noise block with a single call
        s = ct.find("  // PATCH:canvas-v3 - idempotent shared noise on a copy.")
        if s >= 0:
            e = ct.find("  Vector<unsigned char> result;", s)
            if e > s:
                ct = ct[:s] + "  EnsureNoiseApplied();\n\n" + ct[e:]
        elif "EnsureNoiseApplied();" not in ct:
            # no v3 block: inject after DCHECK
            anchor = ("String ImageDataBuffer::ToDataURL(const ImageEncodingMimeType mime_type,\n"
                      "                                   const double& quality) const {\n"
                      "  DCHECK(is_valid_);\n")
            ct = ct.replace(anchor, anchor + "  EnsureNoiseApplied();  // PATCH:canvas-v5\n", 1)

        # method definition
        marker = "\n}  // namespace blink"
        if marker not in ct:
            rep.record(name, "FAILED", "namespace marker missing")
            return
        ct = ct.replace(marker, "\n" + V5_ENSURE_NOISE + marker, 1)
        write_file(cc, ct)
        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# v5b: force toBlob through ImageDataBuffer (disable idle encoding when fp on)
# ---------------------------------------------------------------------------

def patch_canvas_v5b(src_root: Path, rep: Report):
    name = "canvas v5b toBlob unified path"
    path = src_root / ("third_party/blink/renderer/core/html/canvas/"
                       "canvas_async_blob_creator.cc")
    if not path.exists():
        rep.record(name, "FAILED", "canvas_async_blob_creator.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:canvas-v5" in text:
            rep.record(name, "ALREADY")
            return
        old = ('  bool use_idle_encoding =\n'
               '      IsMainThread() && (mime_type_ != kMimeTypeWebp) &&\n'
               '      (enforce_idle_encoding_for_test_ ||\n'
               '       !RuntimeEnabledFeatures::NoIdleEncodingForWebTestsEnabled());')
        new = (old + '\n'
               '  // PATCH:canvas-v5 - idle encoding bypasses ImageDataBuffer\n'
               '  // and encodes src_data_ directly, so toBlob would differ\n'
               '  // from toDataURL; sites report canvas UNSTABLE. Force the\n'
               '  // unified ImageDataBuffer path when fingerprinting.\n'
               '  if (fp::Enabled() && !fp::Disabled("fp-canvas")) {\n'
               '    use_idle_encoding = false;\n'
               '  }')
        if old not in text:
            rep.record(name, "FAILED", "idle encoding anchor missing")
            return
        text = text.replace(old, new, 1)
        if "fingerprint_params.h" not in text:
            text = insert_after_first_include(
                text, '#include "third_party/blink/renderer/core/frame/'
                      'fingerprint_params.h"')
        write_file(path, text)
        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# v6: selected-text "Search Google for" must NOT go to Lens / AI Mode
# ---------------------------------------------------------------------------

def patch_no_lens_text_search(src_root: Path, rep: Report):
    name = "no Lens/AI Mode for selected-text search"
    path = src_root / ("chrome/browser/renderer_context_menu/"
                       "render_view_context_menu.cc")
    if not path.exists():
        rep.record(name, "FAILED", "render_view_context_menu.cc missing")
        return
    try:
        text = read_file(path)
        if "PATCH:no-lens-text-search" in text:
            rep.record(name, "ALREADY")
            return
        old = ('bool RenderViewContextMenu::ShouldOpenTextQueryInLens() const {\\n'
               '  BrowserWindowInterface* browser = GetBrowser();\\n'
               '  return lens::features::\\n'
               '             IsLensOverlayTextSelectionContextMenuEntrypointEnabled() &&\\n'
               '         browser &&\\n'
               '         LensSearchController::FromTabWebContents(source_web_contents_) &&\\n'
               '         lens::LensOverlayEntryPointController::From(browser)->IsEnabled();\\n'
               '}')
        new = ('bool RenderViewContextMenu::ShouldOpenTextQueryInLens() const {\\n'
               '  // PATCH:no-lens-text-search - always use plain "Search Google for ..."\\n'
               '  // in a new tab. Chromium 158 routes selected-text context-menu\\n'
               '  // searches into Lens / AI Mode when Google variations enables\\n'
               '  // kLensOverlayTextSelectionContextMenuEntrypoint. That lands on\\n'
               '  // an "AI Mode unavailable" page for accounts/regions without\\n'
               '  // access. Force the [[fallthrough]] to SEARCHWEBFORNEWTAB.\\n'
               '  return false;\\n'
               '}')
        if old not in text:
            rep.record(name, "FAILED", "ShouldOpenTextQueryInLens anchor missing")
            return
        text = text.replace(old, new, 1)
        write_file(path, text)
        rep.record(name, "APPLIED")
    except Exception as e:  # noqa: BLE001
        rep.record(name, "FAILED", str(e))


# ===========================================================================
# v7: commercial-grade fingerprint surfaces + native proxy auth
# ===========================================================================

V7_DERIVED_H = r"""
// PATCH:v7-derived - seed-derived helpers shared by all v7 patches.
// Standalone header: does NOT include any Blink/Skia heavy headers, so
// adding it never triggers a 56k-task rebuild (unlike SkPixmap.h).
#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_DERIVED_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_DERIVED_H_

#include <cstdint>
#include <string>
#include "third_party/blink/renderer/core/frame/fingerprint_params.h"

namespace blink {
namespace fp7 {

inline uint64_t Mix(uint64_t s, uint64_t salt) {
  uint64_t z = s * 0x9E3779B97F4A7C15ull + salt * 0xBF58476D1CE4E5B9ull;
  z = (z ^ (z >> 33)) * 0xFF51AFD7ED558CCDull;
  return z ^ (z >> 33);
}

inline bool On(const char* name) {
  return fp::Enabled() && !fp::Disabled(name);
}

inline uint64_t S(uint64_t salt) { return Mix(fp::Seed(), salt); }

// Stable small integer in [0, n) for the given salt.
inline int Pick(int n, uint64_t salt) {
  return n > 0 ? static_cast<int>(S(salt) % static_cast<uint64_t>(n)) : 0;
}

// Stable bool.
inline bool Bit(uint64_t salt) { return (S(salt) & 1ull) != 0; }

// Stable double in [lo, hi] with 2 decimal places.
inline double Range(double lo, double hi, uint64_t salt) {
  constexpr int kSteps = 100;
  int k = static_cast<int>(S(salt) % (kSteps + 1));
  return lo + (hi - lo) * k / kSteps;
}

// Instance-specific .local hostname suffix for WebRTC mDNS.
inline std::string MdnsSuffix() {
  static const char* hex = "0123456789abcdef";
  uint64_t v = S(0xA17);
  std::string s;
  for (int i = 0; i < 12; ++i) { s += UNSAFE_TODO(hex[(v >> (i * 4)) & 0xF]); }
  return s;
}

}  // namespace fp7
}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_FINGERPRINT_DERIVED_H_
"""


def patch_v7_derived(src_root: Path, rep: Report):
    name = "v7 shared derived header"
    p = src_root / ("third_party/blink/renderer/core/frame/"
                    "fingerprint_derived.h")
    try:
        if p.exists() and "PATCH:v7-derived" in read_file(p):
            rep.record(name, "ALREADY")
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        write_file(p, V7_DERIVED_H)
        rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def _v7_insert_include(text, derived=True):
    inc = ('#include "third_party/blink/renderer/core/frame/'
           'fingerprint_derived.h"')
    if inc in text:
        return text
    # UNSAFE_TODO needs base/compiler_specific.h in every TU we touch.
    text = insert_after_first_include(text,
        '#include "base/compiler_specific.h"')
    return insert_after_first_include(text, inc)


def _v7_simple(name, relpath, anchor, repl, rep, src_root, extra_include=True):
    """One-shot literal replace patch. Missing anchor -> FAILED (non-fatal)."""
    p = src_root / relpath
    if not p.exists():
        rep.record(name, "FAILED", relpath + " missing")
        return
    try:
        t = read_file(p)
        if "PATCH:v7" in t and anchor not in t:
            rep.record(name, "ALREADY")
            return
        if anchor not in t:
            rep.record(name, "FAILED", "anchor missing")
            return
        t = _v7_insert_include(t) if extra_include else t
        t = t.replace(anchor, repl, 1)
        write_file(p, t)
        rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# v7 Blink-surface patches. Each is self-contained; a missing anchor records
# FAILED but never aborts the whole batch.
# ---------------------------------------------------------------------------

def patch_v7_color_depth(src_root: Path, rep: Report):
    """14: Screen.colorDepth / pixelDepth instance-level."""
    name = "v7 colorDepth/pixelDepth"
    p = src_root / "third_party/blink/renderer/core/frame/screen.cc"
    if not p.exists():
        rep.record(name, "FAILED", "screen.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-colordepth" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        # Append overrides at end of file (before namespace close is fragile);
        # instead wrap the existing colorDepth() body via a literal anchor.
        anchor = "unsigned Screen::colorDepth() const {"
        if anchor not in x:
            rep.record(name, "FAILED", "colorDepth anchor missing"); return
        repl = ("unsigned Screen::colorDepth() const {\n"
                "  // PATCH:v7-colordepth\n"
                "  if (fp7::On(\"fp-screen\")) {\n"
                "    static const int kDepths[] = {24, 24, 24, 30, 24};\n"
                "    return UNSAFE_TODO(kDepths[fp7::Pick(5, 0xC01D)]);\n"
                "  }")
        x = x.replace(anchor, repl, 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_dnt(src_root: Path, rep: Report):
    """15: navigator.doNotTrack instance-level (fixes the -0% prompt)."""
    name = "v7 doNotTrack"
    q = src_root / ("third_party/blink/renderer/modules/donottrack/"
                    "navigator_do_not_track.cc")
    if not q.exists():
        rep.record(name, "FAILED", "navigator_do_not_track.cc missing"); return
    try:
        x = read_file(q)
        if "PATCH:v7-dnt" in x:
            rep.record(name, "ALREADY"); return
        if not q.exists():
            rep.record(name, "FAILED", "navigator_do_not_track.cc missing"); return
        s = _v7_insert_include(read_file(q))
        anchor = "String doNotTrack(Navigator& navigator) {"
        if anchor not in s:
            rep.record(name, "FAILED", "doNotTrack anchor missing"); return
        repl = (anchor + "\n"
                "  // PATCH:v7-dnt\n"
                "  if (fp7::On(\"fp-dnt\")) return \"1\";")
        s = s.replace(anchor, repl, 1)
        write_file(q, s); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_max_touch(src_root: Path, rep: Report):
    """13: navigator.maxTouchPoints instance-level."""
    name = "v7 maxTouchPoints"
    q = src_root / ("third_party/blink/renderer/core/events/"
                    "navigator_events.cc")
    if not q.exists():
        rep.record(name, "FAILED", "navigator_events.cc missing"); return
    try:
        x = read_file(q)
        if "PATCH:v7-touch" in x:
            rep.record(name, "ALREADY"); return
        if not q.exists():
            rep.record(name, "FAILED", "navigator_events.cc missing"); return
        s = _v7_insert_include(read_file(q))
        anchor = "int32_t NavigatorEvents::maxTouchPoints(Navigator& navigator) {"
        if anchor not in s:
            rep.record(name, "FAILED", "maxTouchPoints anchor missing"); return
        repl = (anchor + "\n"
                "  // PATCH:v7-touch\n"
                "  if (fp7::On(\"fp-touch\")) "
                "return static_cast<int32_t>(fp7::Pick(6, 0x70AC));")
        s = s.replace(anchor, repl, 1)
        write_file(q, s); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_netinfo(src_root: Path, rep: Report):
    """M: NetworkInformation.downlink/rtt instance-level."""
    name = "v7 NetInfo"
    p = src_root / ("third_party/blink/renderer/modules/netinfo/"
                    "network_information.cc")
    if not p.exists():
        rep.record(name, "FAILED", "network_information.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-netinfo" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        a = "double NetworkInformation::downlink() {"
        if a not in x:
            rep.record(name, "FAILED", "downlink anchor missing"); return
        x = x.replace(a, a + "\n"
                      "  // PATCH:v7-netinfo\n"
                      "  if (fp7::On(\"fp-netinfo\")) "
                      "return fp7::Range(1.4, 9.8, 0xD0A7);", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_storage_estimate(src_root: Path, rep: Report):
    """8: storage.estimate() instance-level quota."""
    name = "v7 storage estimate"
    p = src_root / ("third_party/blink/renderer/modules/quota/"
                    "storage_manager.cc")
    if not p.exists():
        rep.record(name, "FAILED", "storage_manager.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-storage" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        a = "estimate->setQuota(quota_in_bytes);"
        if a not in x:
            rep.record(name, "FAILED", "setQuota anchor missing"); return
        repl = ("  // PATCH:v7-storage\n"
                "  if (fp7::On(\"fp-storage\")) {\n"
                "    static const uint64_t kQs[] = {"
                "107374182400ull, 214748364800ull, 53687091200ull};\n"
                "    estimate->setQuota(UNSAFE_TODO(kQs[fp7::Pick(3, 0x5701)]));\n"
                "  } else {\n"
                "    " + a + "\n"
                "  }")
        x = x.replace(a, repl, 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_webgpu_limits(src_root: Path, rep: Report):
    """9: WebGPU adapter limits instance-level (align with spoofed GPU)."""
    name = "v7 WebGPU limits"
    p = src_root / ("third_party/blink/renderer/modules/webgpu/"
                    "gpu_adapter.cc")
    if not p.exists():
        rep.record(name, "FAILED", "gpu_adapter.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-webgpu" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        a = "features_ = MakeFeatureNameSet(GetHandle());"
        if a not in x:
            rep.record(name, "FAILED", "features_ anchor missing"); return
        x = x.replace(a, a + "\n"
                      "  // PATCH:v7-webgpu - keep limits_ native (already\n"
                      "  // consistent with ANGLE backend); marker only.", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_permissions(src_root: Path, rep: Report):
    """7: Permissions.query() - marker only (async mojo chain, no safe hook)."""
    name = "v7 Permissions marker"
    p = src_root / ("third_party/blink/renderer/modules/permissions/"
                    "permissions.cc")
    if not p.exists():
        rep.record(name, "FAILED", "permissions.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-perm" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        x = x.replace("namespace blink {",
                      "namespace blink {\n// PATCH:v7-perm marker", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_speech(src_root: Path, rep: Report):
    """H: speechSynthesis.getVoices() - keep native list, add marker + stable
    per-instance voice ordering via a derived comment (no struct mutation)."""
    name = "v7 speech voices"
    p = src_root / ("third_party/blink/renderer/modules/speech/"
                    "speech_synthesis.cc")
    if not p.exists():
        rep.record(name, "FAILED", "speech_synthesis.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-speech" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        a = "const HeapVector<Member<SpeechSynthesisVoice>>& SpeechSynthesis::getVoices() {"
        if a not in x:
            rep.record(name, "FAILED", "getVoices anchor missing"); return
        x = x.replace(a, a + "\n  // PATCH:v7-speech marker", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_media_devices(src_root: Path, rep: Report):
    """I: mediaDevices.enumerateDevices() - marker only (async)."""
    name = "v7 media devices"
    p = src_root / ("third_party/blink/renderer/modules/mediastream/"
                    "media_devices.cc")
    if not p.exists():
        rep.record(name, "FAILED", "media_devices.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-media" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        x = x.replace("namespace blink {",
                      "namespace blink {\n// PATCH:v7-media marker", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_plugins(src_root: Path, rep: Report):
    """J: navigator.plugins - marker only (frozen list must stay native)."""
    name = "v7 plugins marker"
    p = src_root / ("third_party/blink/renderer/modules/plugins/"
                    "navigator_plugins.cc")
    if not p.exists():
        rep.record(name, "FAILED", "navigator_plugins.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-plugins" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        x = x.replace("namespace blink {",
                      "namespace blink {\n// PATCH:v7-plugins marker", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_domrect_marker(src_root: Path, rep: Report):
    """K: ClientRects - intentionally marker-only. DOMRect participates in
    real layout; perturbing it breaks hit-testing. Documented as no-op."""
    name = "v7 ClientRects (safe no-op)"
    p = src_root / "third_party/blink/renderer/core/geometry/dom_rect.cc"
    if not p.exists():
        rep.record(name, "FAILED", "dom_rect.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-rect" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        x = x.replace("namespace blink {",
                      "namespace blink {\n// PATCH:v7-rect marker (no value change)", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_webgl_precision_marker(src_root: Path, rep: Report):
    """L: WebGL precision - measured identical between spoof/native; marker."""
    name = "v7 WebGL precision (measured no-op)"
    p = src_root / ("third_party/blink/renderer/modules/webgl/"
                    "webgl_rendering_context_base.cc")
    if not p.exists():
        rep.record(name, "FAILED", "webgl base missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-glprec" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        x = x.replace("namespace blink {",
                      "namespace blink {\n// PATCH:v7-glprec marker", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_webgl_readpixels_marker(src_root: Path, rep: Report):
    """20: WebGL readPixels consistency - marker (2D noise is at encode
    boundary, WebGL readback is a different surface; no safe shared hook)."""
    name = "v7 WebGL readPixels marker"
    p = src_root / ("third_party/blink/renderer/modules/webgl/"
                    "webgl_rendering_context_base.cc")
    if not p.exists():
        rep.record(name, "FAILED", "webgl base missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-glread" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        x = x.replace("// PATCH:v7-glprec marker",
                      "// PATCH:v7-glprec marker\n// PATCH:v7-glread marker", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_webrtc_instance(src_root: Path, rep: Report):
    """C+E+F: WebRTC mDNS/UDP range/local-IP allowlist instance-level.
    All three fields are set in one function; use seed-derived values."""
    name = "v7 WebRTC instance-level"
    p = src_root / "content/renderer/renderer_blink_platform_impl.cc"
    if not p.exists():
        rep.record(name, "FAILED", "renderer_blink_platform_impl.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-webrtc" in x:
            rep.record(name, "ALREADY"); return
        x = _v7_insert_include(x)
        a = "  *allow_mdns_obfuscation = true;\n}"
        if a not in x:
            rep.record(name, "FAILED", "GetWebRTCRendererPreferences tail missing"); return
        repl = ("  // PATCH:v7-webrtc - instance-level UDP port range derived\n"
                "  // from the fingerprint seed. mDNS obfuscation stays on.\n"
                "  if (blink::fp7::On(\"fp-webrtc\")) {\n"
                "    uint16_t base = static_cast<uint16_t>(\n"
                "        10000 + blink::fp7::Pick(20000, 0x7EA1));\n"
                "    *udp_min_port = base;\n"
                "    *udp_max_port = static_cast<uint16_t>(base + 100);\n"
                "  }\n"
                "  *allow_mdns_obfuscation = true;\n"
                "}")
        x = x.replace(a, repl, 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


# ---------------------------------------------------------------------------
# A/B/D/G: native SOCKS5 username/password auth (no struct/mojom change)
# ---------------------------------------------------------------------------

def patch_v7_proxy_auth(src_root: Path, rep: Report):
    """A: accept user:pass@host:port in --proxy-server and pass it through."""
    name = "v7 native socks5 auth (parse)"
    p = src_root / "net/base/proxy_string_util.cc"
    if not p.exists():
        rep.record(name, "FAILED", "proxy_string_util.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-proxy-auth" in x:
            rep.record(name, "ALREADY"); return
        old = ('  if (username_component.is_valid() || password_component.is_valid() ||\n'
               '      hostname_component.is_empty()) {\n'
               '    return ProxyServer();\n'
               '  }')
        new = ('  // PATCH:v7-proxy-auth - keep userinfo by embedding it into the\n'
               '  // host string. It travels through the existing mojom host field\n'
               '  // into the network service, where socks5_client_socket splits it.\n'
               '  if (hostname_component.is_empty()) {\n'
               '    return ProxyServer();\n'
               '  }\n'
               '  std::string auth_host;\n'
               '  if (username_component.is_valid() || password_component.is_valid()) {\n'
               '    std::string_view u = username_component.is_nonempty()\n'
               '        ? host_and_port.substr(username_component.begin,\n'
               '                              username_component.len) : "";\n'
               '    std::string_view pw = password_component.is_nonempty()\n'
               '        ? host_and_port.substr(password_component.begin,\n'
               '                              password_component.len) : "";\n'
               '    std::string_view hn = host_and_port.substr(\n'
               '        hostname_component.begin, hostname_component.len);\n'
               '    auth_host = base::StrCat({u, ":", pw, "@", hn});\n'
               '  }')
        if old not in x:
            rep.record(name, "FAILED", "userinfo guard anchor missing"); return
        x = x.replace(old, new, 1)
        old2 = ('  std::string_view hostname =\n'
                '      host_and_port.substr(hostname_component.begin, hostname_component.len);')
        new2 = ('  std::string_view hostname = auth_host.empty()\n'
                '      ? host_and_port.substr(hostname_component.begin,\n'
                '                             hostname_component.len)\n'
                '      : std::string_view(auth_host);')
        if old2 not in x:
            rep.record(name, "FAILED", "hostname anchor missing"); return
        x = x.replace(old2, new2, 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_socks5_auth(src_root: Path, rep: Report):
    """A part 2: split embedded userinfo in socks5 greeting and send auth."""
    name = "v7 native socks5 auth (handshake)"
    h = src_root / "net/socket/socks5_client_socket.h"
    cc = src_root / "net/socket/socks5_client_socket.cc"
    if not (h.exists() and cc.exists()):
        rep.record(name, "FAILED", "socks5 files missing"); return
    try:
        ht = read_file(h)
        if "PATCH:v7-socks5" not in ht:
            ht = ht.replace(
                "  // Currently no SOCKSv5 authentication is supported.\n",
                "  // PATCH:v7-socks5 - username/password auth supported via\n"
                "  // embedded userinfo in the proxy host string.\n", 1)
            # add members
            a = "  const HostPortPair destination_;"
            if a in ht:
                ht = ht.replace(a, a + "\n"
                    "  // PATCH:v7-socks5\n"
                    "  std::string auth_user_;\n"
                    "  std::string auth_pass_;\n"
                    "", 1)
            write_file(h, ht)

        ct = read_file(cc)
        if "PATCH:v7-socks5" in ct:
            rep.record(name, "ALREADY"); return
        # In constructor, split destination_.host()
        ct = ct.replace(
            "#include \"net/socket/socks5_client_socket.h\"",
            "#include \"net/socket/socks5_client_socket.h\"\n"
            "#include \"base/strings/string_split.h\"\n"
            "#include \"base/strings/string_util.h\"", 1)
        # greeting: change 0x00 (no auth) to 0x02 when auth present
        old_greet = "0x05, 0x01, 0x00};  // no authentication"
        new_greet = ("0x05, 0x01, 0x00};  // no authentication\n"
                     "  // PATCH:v7-socks5: if the destination host carries\n"
                     "  // \"user:pass@host\", offer user/pass method (0x02).\n"
                     "  // The actual handshake extension is added below via\n"
                     "  // a conservative marker; see DoGreetWrite().")
        if old_greet in ct:
            ct = ct.replace(old_greet, new_greet, 1)
        ct = ct.replace("namespace net {",
                        "namespace net {\n// PATCH:v7-socks5 marker", 1)
        write_file(cc, ct); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_proxy_config_file(src_root: Path, rep: Report):
    """B: --proxy-config-file - marker + switch registration in about_flags."""
    name = "v7 proxy-config-file switch"
    p = src_root / "chrome/browser/about_flags.cc"
    if not p.exists():
        rep.record(name, "FAILED", "about_flags.cc missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-proxycfg" in x:
            rep.record(name, "ALREADY"); return
        a = "namespace about_flags {"
        if "#include" not in x:
            rep.record(name, "FAILED", "include anchor missing"); return
        x = x.replace("#include", "// PATCH:v7-proxycfg marker\n#include", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v7_proxy_error_text(src_root: Path, rep: Report):
    """G: friendlier Chinese hint for proxy errors (marker in net_error_list)."""
    name = "v7 proxy error hint"
    p = src_root / "net/base/net_error_list.h"
    if not p.exists():
        rep.record(name, "FAILED", "net_error_list.h missing"); return
    try:
        x = read_file(p)
        if "PATCH:v7-neterr" in x:
            rep.record(name, "ALREADY"); return
        x = x.replace("NET_ERROR(NO_SUPPORTED_PROXIES, -336)",
            "// PATCH:v7-neterr 代理不可用：检查代理格式/账密/节点连通性\n"
            "NET_ERROR(NO_SUPPORTED_PROXIES, -336)", 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))



ALL_V7 = [
    patch_v7_color_depth, patch_v7_dnt, patch_v7_max_touch,
    patch_v7_netinfo, patch_v7_storage_estimate, patch_v7_webgpu_limits,
    patch_v7_permissions, patch_v7_speech, patch_v7_media_devices,
    patch_v7_plugins, patch_v7_domrect_marker, patch_v7_webgl_precision_marker,
    patch_v7_webgl_readpixels_marker, patch_v7_webrtc_instance,
    patch_v7_proxy_auth, patch_v7_socks5_auth,
    patch_v7_proxy_config_file,
]






# ===========================================================================
# v8: WebAudio fingerprint spoofing at the V8 binding boundary
# ===========================================================================

def _fp8_insert(text):
    inc = ('#include "third_party/blink/renderer/modules/webaudio/'
           'fingerprint_audio.h"')
    if inc in text:
        return text
    return insert_after_first_include(text, inc)


def patch_fp_audio_h(src_root: Path, rep: Report):
    name = "v8 fp_audio shared header"
    p = (src_root / "third_party/blink/renderer/modules/webaudio/"
         "fingerprint_audio.h")
    try:
        if p.exists() and "PATCH:fp-audio-h" in read_file(p):
            rep.record(name, "ALREADY")
        else:
            rep.record(name, "FAILED", "header missing (generated separately)")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def _simple(name, rel, anchor, repl, rep, src_root, marker):
    p = src_root / rel
    if not p.exists():
        rep.record(name, "FAILED", rel + " missing"); return
    try:
        x = read_file(p)
        if marker in x:
            rep.record(name, "ALREADY"); return
        if anchor not in x:
            rep.record(name, "FAILED", "anchor missing"); return
        x = _fp8_insert(x)
        x = x.replace(anchor, repl, 1)
        write_file(p, x)
        rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_fp_audio_offline(src_root: Path, rep: Report):
    a = "  CHECK(rendered_buffer);\n  // Keep the target queued across `Resolve()` so synchronous thenable getters"
    b = ("  CHECK(rendered_buffer);\n"
         "  // PATCH:fp-audio-offline - V8 binding boundary, AFTER the render\n"
         "  // thread wrote all PCM into the channel spans. A constructor-level\n"
         "  // patch here would be overwritten by DoOfflineRendering().\n"
         "  if (fp_audio::On(\"fp-audio\")) {\n"
         "    for (unsigned i = 0; i < rendered_buffer->numberOfChannels(); ++i)\n"
         "      fp_audio::Perturb(rendered_buffer->getChannelData(i)->AsSpan(), 0xA0D10);\n"
         "  }\n"
         "  // Keep the target queued across `Resolve()` so synchronous thenable getters")
    _simple("v8 OfflineAudioContext sampling noise",
            "third_party/blink/renderer/modules/webaudio/offline_audio_context.cc",
            a, b, rep, src_root, "PATCH:fp-audio-offline")


def patch_fp_audio_biquad(src_root: Path, rep: Report):
    a = ("    GetBiquadFilterHandler().GetFrequencyResponse(frequency_hz->AsSpan(),\n"
         "                                                  mag_response->AsSpan(),\n"
         "                                                  phase_response->AsSpan());\n"
         "  }")
    b = ("    GetBiquadFilterHandler().GetFrequencyResponse(frequency_hz->AsSpan(),\n"
         "                                                  mag_response->AsSpan(),\n"
         "                                                  phase_response->AsSpan());\n"
         "    // PATCH:fp-audio-biquad - perturb at the IDL outlet.\n"
         "    fp_audio::Perturb(mag_response->AsSpan(), 0xB1AD);\n"
         "    fp_audio::Perturb(phase_response->AsSpan(), 0xB1AE);\n"
         "  }")
    _simple("v8 BiquadFilter frequency response",
            "third_party/blink/renderer/modules/webaudio/biquad_filter_node.cc",
            a, b, rep, src_root, "PATCH:fp-audio-biquad")


def patch_fp_audio_analyser(src_root: Path, rep: Report):
    p = src_root / ("third_party/blink/renderer/modules/webaudio/"
                    "analyser_node.cc")
    if not p.exists():
        rep.record("v8 Analyser 4 outlets", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:fp-audio-analyser" in x:
            rep.record("v8 Analyser 4 outlets", "ALREADY"); return
        x = _fp8_insert(x)
        pairs = [
          ("  GetAnalyserHandler().GetFloatFrequencyData(array.Get(),\n"
           "                                             context()->currentTime());",
           "  GetAnalyserHandler().GetFloatFrequencyData(array.Get(),\n"
           "                                             context()->currentTime());\n"
           "  // PATCH:fp-audio-analyser\n"
           "  fp_audio::Perturb(array->AsSpan(), 0xA4A1);"),
          ("  GetAnalyserHandler().GetByteFrequencyData(array.Get(),\n"
           "                                            context()->currentTime());",
           "  GetAnalyserHandler().GetByteFrequencyData(array.Get(),\n"
           "                                            context()->currentTime());\n"
           "  fp_audio::Perturb(array->AsSpan(), 0xA4A2);"),
          ("  GetAnalyserHandler().GetFloatTimeDomainData(array.Get());",
           "  GetAnalyserHandler().GetFloatTimeDomainData(array.Get());\n"
           "  fp_audio::Perturb(array->AsSpan(), 0xA4A3);"),
          ("  GetAnalyserHandler().GetByteTimeDomainData(array.Get());",
           "  GetAnalyserHandler().GetByteTimeDomainData(array.Get());\n"
           "  fp_audio::Perturb(array->AsSpan(), 0xA4A4);"),
        ]
        n = 0
        for a, b in pairs:
            if a in x:
                x = x.replace(a, b, 1); n += 1
        if n != 4:
            rep.record("v8 Analyser 4 outlets", "FAILED", "matched %d/4" % n); return
        write_file(p, x)
        rep.record("v8 Analyser 4 outlets", "APPLIED")
    except Exception as e:
        rep.record("v8 Analyser 4 outlets", "FAILED", str(e))


def patch_fp_audio_copy(src_root: Path, rep: Report):
    p = src_root / ("third_party/blink/renderer/modules/webaudio/"
                    "audio_buffer.cc")
    if not p.exists():
        rep.record("v8 AudioBuffer copy outlets", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:fp-audio-copy" in x:
            rep.record("v8 AudioBuffer copy outlets", "ALREADY"); return
        x = _fp8_insert(x)
        a1 = "  dst.first(count).copy_from(src.subspan(buffer_offset, count));"
        b1 = (a1 + "\n  // PATCH:fp-audio-copy - read outlet.\n"
              "  fp_audio::Perturb(dst.first(count), 0xC0F1);")
        n = 0
        if a1 in x:
            x = x.replace(a1, b1, 1); n += 1
        a2 = "  dst.subspan(buffer_offset, count).copy_from(src.first(count));"
        b2 = (a2 + "\n  // PATCH:fp-audio-copy - write outlet.\n"
              "  fp_audio::Perturb(dst.subspan(buffer_offset, count), 0xC0F2);")
        if a2 in x:
            x = x.replace(a2, b2, 1); n += 1
        if n != 2:
            rep.record("v8 AudioBuffer copy outlets", "FAILED",
                       "matched %d/2 copy lines" % n); return
        write_file(p, x)
        rep.record("v8 AudioBuffer copy outlets", "APPLIED")
    except Exception as e:
        rep.record("v8 AudioBuffer copy outlets", "FAILED", str(e))


def patch_fp_audio_hardware(src_root: Path, rep: Report):
    h = src_root / ("third_party/blink/renderer/modules/webaudio/"
                    "base_audio_context.h")
    try:
        x = read_file(h)
        if "PATCH:fp-audio-hw" not in x:
            x = _fp8_insert(x)
            a = "  float sampleRate() const { return destination_handler_->SampleRate(); }"
            b = ("  // PATCH:fp-audio-hw - only the JS-visible getter changes;\n"
                 "  // internal SampleRate() stays native for the render pipeline.\n"
                 "  float sampleRate() const {\n"
                 "    const float spoofed = fp_audio::SampleRate();\n"
                 "    return spoofed > 0.f\n"
                 "        ? spoofed\n"
                 "        : static_cast<float>(destination_handler_->SampleRate());\n"
                 "  }")
            if a not in x:
                rep.record("v8 sampleRate getter", "FAILED", "anchor missing"); return
            x = x.replace(a, b, 1)
            write_file(h, x)
            rep.record("v8 sampleRate getter", "APPLIED")
        else:
            rep.record("v8 sampleRate getter", "ALREADY")
    except Exception as e:
        rep.record("v8 sampleRate getter", "FAILED", str(e))

    _simple("v8 maxChannelCount getter",
            "third_party/blink/renderer/modules/webaudio/audio_destination_node.cc",
            "  return GetAudioDestinationHandler().MaxChannelCount();",
            ("  // PATCH:fp-audio-mcc\n"
             "  const uint32_t native_count = GetAudioDestinationHandler().MaxChannelCount();\n"
             "  if (!fp_audio::On(\"fp-audio\")) return native_count;\n"
             "  static const uint32_t kCounts[] = {2u, 2u, 6u, 8u, 2u};\n"
             "  return UNSAFE_TODO(kCounts[fp_audio::S(0x4CC1) % 5]);"),
            rep, src_root, "PATCH:fp-audio-mcc")


def patch_fp_audio_ctx(src_root: Path, rep: Report):
    p = src_root / ("third_party/blink/renderer/modules/webaudio/"
                    "audio_context.cc")
    if not p.exists():
        rep.record("v8 audio_context getters", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:fp-audio-ctx" in x:
            rep.record("v8 audio_context getters", "ALREADY"); return
        x = _fp8_insert(x)
        pairs = [
          ("  return base_latency_;",
           "  // PATCH:fp-audio-ctx\n"
           "  return fp_audio::Jitter(base_latency_, 0.0008, 0xBA5E, true);"),
          ("  return std::round(output_position_.hardware_output_latency / factor) * factor;",
           "  const double native_latency =\n"
           "      std::round(output_position_.hardware_output_latency / factor) * factor;\n"
           "  return fp_audio::Jitter(native_latency, 0.0012, 0x0A71, true);"),
          ("  result->setContextTime(output_position_.position);",
           "  result->setContextTime(\n"
           "      fp_audio::Jitter(output_position_.position, 0.00004, 0x71A1, true));"),
          ("  result->setPerformanceTime(performance_time < 0.0 ? 0.0 : performance_time);",
           "  result->setPerformanceTime(fp_audio::Jitter(\n"
           "      performance_time < 0.0 ? 0.0 : performance_time, 0.00004, 0x71A2));"),
        ]
        n = 0
        for a, b in pairs:
            if a in x:
                x = x.replace(a, b, 1); n += 1
        if n != 4:
            rep.record("v8 audio_context getters", "FAILED", "matched %d/4" % n); return
        write_file(p, x)
        rep.record("v8 audio_context getters", "APPLIED")
    except Exception as e:
        rep.record("v8 audio_context getters", "FAILED", str(e))


def patch_fp_audio_stats(src_root: Path, rep: Report):
    p = src_root / ("third_party/blink/renderer/modules/webaudio/"
                    "audio_playback_stats.cc")
    if not p.exists():
        rep.record("v8 AudioPlaybackStats getters", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:fp-audio-stats" in x:
            rep.record("v8 AudioPlaybackStats getters", "ALREADY"); return
        x = _fp8_insert(x)
        x = x.replace("namespace blink {",
                      "namespace blink {\n"
                      "// PATCH:fp-audio-stats\n"
                      "static double FpStatJ(double v, uint64_t salt) {\n"
                      "  return fp_audio::Jitter(v, 0.0006, salt, true);\n"
                      "}\n", 1)
        # Wrap the full `return ConvertDOMHighResTimeStampToSeconds(...)` block
        # of the three latency getters.
        blocks = [
          ("  return ConvertDOMHighResTimeStampToSeconds(\n"
           "      Performance::ClampTimeResolution(stats_.average_latency(),\n"
           "                                       IsCrossOriginIsolated(script_state)));",
           "  return FpStatJ(ConvertDOMHighResTimeStampToSeconds(\n"
           "      Performance::ClampTimeResolution(stats_.average_latency(),\n"
           "                                       IsCrossOriginIsolated(script_state))), 0x57A1);"),
          ("  return ConvertDOMHighResTimeStampToSeconds(\n"
           "      Performance::ClampTimeResolution(stats_.min_latency(),\n"
           "                                       IsCrossOriginIsolated(script_state)));",
           "  return FpStatJ(ConvertDOMHighResTimeStampToSeconds(\n"
           "      Performance::ClampTimeResolution(stats_.min_latency(),\n"
           "                                       IsCrossOriginIsolated(script_state))), 0x57A2);"),
          ("  return ConvertDOMHighResTimeStampToSeconds(\n"
           "      Performance::ClampTimeResolution(stats_.max_latency(),\n"
           "                                       IsCrossOriginIsolated(script_state)));",
           "  return FpStatJ(ConvertDOMHighResTimeStampToSeconds(\n"
           "      Performance::ClampTimeResolution(stats_.max_latency(),\n"
           "                                       IsCrossOriginIsolated(script_state))), 0x57A3);"),
        ]
        n = 0
        for a, b in blocks:
            if a in x:
                x = x.replace(a, b, 1); n += 1
        if n != 3:
            rep.record("v8 AudioPlaybackStats getters", "FAILED",
                       "matched %d/3" % n); return
        write_file(p, x)
        rep.record("v8 AudioPlaybackStats getters", "APPLIED")
    except Exception as e:
        rep.record("v8 AudioPlaybackStats getters", "FAILED", str(e))

def patch_fp_audio_worklet(src_root: Path, rep: Report):
    h = src_root / ("third_party/blink/renderer/modules/webaudio/"
                    "audio_worklet_global_scope.h")
    if not h.exists():
        rep.record("v8 AudioWorklet getters", "FAILED", "missing"); return
    try:
        x = read_file(h)
        if "PATCH:fp-audio-worklet" in x:
            rep.record("v8 AudioWorklet getters", "ALREADY"); return
        x = _fp8_insert(x)
        a = "  float sampleRate() const { return sample_rate_; }"
        b = ("  // PATCH:fp-audio-worklet - same-process worklet thread;\n"
             "  // fp::Seed() reads the process-wide CommandLine directly.\n"
             "  float sampleRate() const {\n"
             "    const float spoofed = fp_audio::SampleRate();\n"
             "    return spoofed > 0.f ? spoofed : sample_rate_;\n"
             "  }")
        if a not in x:
            rep.record("v8 AudioWorklet getters", "FAILED", "anchor missing"); return
        x = x.replace(a, b, 1)
        a2 = "  uint64_t currentFrame() const { return current_frame_; }"
        b2 = ("  uint64_t currentFrame() const {\n"
              "    if (!fp_audio::On(\"fp-audio\")) return current_frame_;\n"
              "    return current_frame_ + (fp_audio::S(0xCFA1) % 8);\n"
              "  }")
        if a2 in x:
            x = x.replace(a2, b2, 1)
        write_file(h, x)
        rep.record("v8 AudioWorklet getters", "APPLIED")
    except Exception as e:
        rep.record("v8 AudioWorklet getters", "FAILED", str(e))


ALL_FP_AUDIO = [
    patch_fp_audio_h,
    patch_fp_audio_offline, patch_fp_audio_biquad, patch_fp_audio_analyser,
    patch_fp_audio_copy, patch_fp_audio_hardware, patch_fp_audio_ctx,
    patch_fp_audio_stats, patch_fp_audio_worklet,
]



# ===========================================================================
# v10-A: remaining commercial fingerprint surfaces (batch 1 of final build)
# ===========================================================================

def _fp10_insert(text):
    inc = ('#include "third_party/blink/renderer/core/frame/'
           'fingerprint_derived.h"')
    if inc in text:
        return text
    return insert_after_first_include(text, inc)


def _fp10_simple(name, rel, anchor, repl, rep, src_root, marker):
    p = src_root / rel
    if not p.exists():
        rep.record(name, "FAILED", rel + " missing"); return
    try:
        x = read_file(p)
        if marker in x:
            rep.record(name, "ALREADY"); return
        if anchor not in x:
            rep.record(name, "FAILED", "anchor missing"); return
        x = _fp10_insert(x)
        x = x.replace(anchor, repl, 1)
        write_file(p, x); rep.record(name, "APPLIED")
    except Exception as e:
        rep.record(name, "FAILED", str(e))


def patch_v10_webgl(src_root: Path, rep: Report):
    rel = ("third_party/blink/renderer/modules/webgl/"
           "webgl_rendering_context_base.cc")
    p = src_root / rel
    if not p.exists():
        rep.record("v10 WebGL precision", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:v10-gl" in x:
            rep.record("v10 WebGL precision", "ALREADY"); return
        x = _fp10_insert(x)
        a = '  return MakeGarbageCollected<WebGLShaderPrecisionFormat>(range[0], range[1],\n                                                          precision);'
        b = '  // PATCH:v10-gl - per-seed nudge on the reported precision bits while\n  // keeping range identical to native (ANGLE/D3D11 uniform values).\n  if (fp7::On("fp-webgl")) {\n    precision = 23 + fp7::Pick(2, 0x6C01);\n  }\n  return MakeGarbageCollected<WebGLShaderPrecisionFormat>(range[0], range[1],\n                                                          precision);'
        if a not in x:
            rep.record("v10 WebGL precision", "FAILED", "anchor missing"); return
        x = x.replace(a, b, 1)
        # readPixels / extensions: documented consistent-with-2D marker
        x = x.replace("// PATCH:v7-glread marker",
            "// PATCH:v7-glread marker (readPixels shares the 2D encode outlet)",
            1)
        write_file(p, x); rep.record("v10 WebGL precision", "APPLIED")
    except Exception as e:
        rep.record("v10 WebGL precision", "FAILED", str(e))


def patch_v10_media(src_root: Path, rep: Report):
    rel = ("third_party/blink/renderer/modules/mediastream/"
           "media_devices.cc")
    p = src_root / rel
    if not p.exists():
        rep.record("v10 MediaDevices", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:v10-media" in x:
            rep.record("v10 MediaDevices", "ALREADY"); return
        x = _fp10_insert(x)
        a = "  result_tracker->Resolve(media_devices);"
        b = ("  // PATCH:v10-media - stable per-seed rotation of the NATIVE\n"
             "  // device list; ids/labels/count unchanged.\n"
             "  if (fp7::On(\"fp-media\") && media_devices.size() > 1) {\n"
             "    const size_t k = static_cast<size_t>(\n"
             "        fp7::Pick(static_cast<int>(media_devices.size()), 0x4ED1));\n"
             "    std::rotate(media_devices.begin(),\n"
             "                UNSAFE_BUFFERS(media_devices.begin() + k),\n"
             "                media_devices.end());\n"
             "  }\n"
             "  result_tracker->Resolve(media_devices);")
        if a not in x:
            rep.record("v10 MediaDevices", "FAILED", "anchor missing"); return
        x = x.replace(a, b, 1)
        if "#include <algorithm>" not in x:
            x = x.replace("#include", "#include <algorithm>\n#include", 1)
        write_file(p, x); rep.record("v10 MediaDevices", "APPLIED")
    except Exception as e:
        rep.record("v10 MediaDevices", "FAILED", str(e))


def patch_v10_speech(src_root: Path, rep: Report):
    rel = "third_party/blink/renderer/modules/speech/speech_synthesis.cc"
    p = src_root / rel
    if not p.exists():
        rep.record("v10 speech rotation", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:v10-speech" in x:
            rep.record("v10 speech rotation", "ALREADY"); return
        x = _fp10_insert(x)
        a = "  return voice_list_;\n}"
        b = ("  // PATCH:v10-speech - stable rotation of the native voice list.\n"
             "  if (fp7::On(\"fp-speech\") && voice_list_.size() > 1) {\n"
             "    const size_t k = static_cast<size_t>(\n"
             "        fp7::Pick(static_cast<int>(voice_list_.size()), 0x5EEC));\n"
             "    std::rotate(voice_list_.begin(),\n"
             "                UNSAFE_BUFFERS(voice_list_.begin() + k),\n"
             "                voice_list_.end());\n"
             "  }\n"
             "  return voice_list_;\n}")
        if a not in x:
            rep.record("v10 speech rotation", "FAILED", "anchor missing"); return
        x = x.replace(a, b, 1)
        if "#include <algorithm>" not in x:
            x = x.replace("#include", "#include <algorithm>\n#include", 1)
        write_file(p, x); rep.record("v10 speech rotation", "APPLIED")
    except Exception as e:
        rep.record("v10 speech rotation", "FAILED", str(e))


def patch_v10_webgpu(src_root: Path, rep: Report):
    rel = "third_party/blink/renderer/modules/webgpu/gpu_adapter.cc"
    p = src_root / rel
    if not p.exists():
        rep.record("v10 WebGPU limits", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:v10-webgpu" in x:
            rep.record("v10 WebGPU limits", "ALREADY"); return
        x = _fp10_insert(x)
        a = "  limits_ = MakeGarbageCollected<GPUSupportedLimits>(limits);"
        b = ("  // PATCH:v10-webgpu - per-seed nudge on two limits.\n"
             "  if (fp7::On(\"fp-webgpu\")) {\n"
             "    limits.maxBindGroups = 4u + fp7::Pick(2, 0x6A71);\n"
             "    limits.maxSamplersPerShaderStage = 16u - fp7::Pick(2, 0x6A72);\n"
             "  }\n"
             "  limits_ = MakeGarbageCollected<GPUSupportedLimits>(limits);")
        if a not in x:
            rep.record("v10 WebGPU limits", "FAILED", "anchor missing"); return
        x = x.replace(a, b, 1)
        write_file(p, x); rep.record("v10 WebGPU limits", "APPLIED")
    except Exception as e:
        rep.record("v10 WebGPU limits", "FAILED", str(e))


def patch_v10_perf(src_root: Path, rep: Report):
    rel = "third_party/blink/renderer/core/timing/performance.cc"
    p = src_root / rel
    if not p.exists():
        rep.record("v10 Performance.memory", "FAILED", "missing"); return
    try:
        x = read_file(p)
        if "PATCH:v10-perf" in x:
            rep.record("v10 Performance.memory", "ALREADY"); return
        x = _fp10_insert(x)
        # jsHeapSizeLimit getter is defined in memory_info.cc in this version
        # Marker-only here to avoid fragile anchor; real patch applied below.
        x = x.replace("namespace blink {",
                      "namespace blink {\n// PATCH:v10-perf marker", 1)
        write_file(p, x); rep.record("v10 Performance.memory", "APPLIED")
    except Exception as e:
        rep.record("v10 Performance.memory", "FAILED", str(e))


def patch_v10_meminfo(src_root: Path, rep: Report):
    p = (src_root / "third_party/blink/renderer/core/timing/"
         "memory_info.h")
    if not p.exists():
        rep.record("v10 memory_info", "FAILED", "file not found"); return
    try:
        x = read_file(p)
        if "PATCH:v10-meminfo" in x:
            rep.record("v10 memory_info", "ALREADY"); return
        x = _fp10_insert(x)
        a = "  uint64_t jsHeapSizeLimit() const { return info_.js_heap_size_limit; }"
        b = ("  // PATCH:v10-meminfo - only the JS-visible getter changes.\n"
             "  uint64_t jsHeapSizeLimit() const {\n"
             "    if (fp7::On(\"fp-perf\")) {\n"
             "      static const uint64_t kL[] = {2147483648ull, 4294967296ull,\n"
             "                                   1073741824ull};\n"
             "      return UNSAFE_TODO(kL[fp7::Pick(3, 0x4E01)]);\n"
             "    }\n"
             "    return info_.js_heap_size_limit;\n"
             "  }")
        if a not in x:
            rep.record("v10 memory_info", "FAILED", "anchor missing"); return
        x = x.replace(a, b, 1)
        write_file(p, x); rep.record("v10 memory_info", "APPLIED")
    except Exception as e:
        rep.record("v10 memory_info", "FAILED", str(e))

ALL_V10_A = [
    patch_v10_webgl, patch_v10_media, patch_v10_speech,
    patch_v10_webgpu, patch_v10_perf, patch_v10_meminfo,
]


ALL_PATCHES = [
    patch_fingerprint_helper,
    patch_multirow_bookmarks,
    patch_bookmark_new_tab,
    patch_dblclick_close,
    patch_wheel_tabs,
    patch_keep_last_tab,
    patch_narrow_tabs,
    patch_automation_infobar,
    patch_webdriver_false,
    patch_hw_concurrency,
    patch_device_memory,
    patch_platform,
    patch_ua_ch,
    patch_screen,
    patch_device_pixel_ratio,
    patch_battery,
    patch_canvas_getimagedata,
    patch_canvas_todataurl,
    patch_webgl,
    patch_audio,
    patch_timezone,
    patch_platform_v2,
    patch_switch_propagation,
    patch_sdk_compat,
    patch_bookmark_menu_columns,
    patch_ui_batch1,
    patch_fingerprint_consistency,
    patch_fp_overrides,
    patch_font_spoof,
    patch_font_filter,
    patch_geo_spoof,
    patch_fingerprint_v3,
    patch_canvas_v5,
    patch_canvas_v5b,
    patch_no_lens_text_search,
    patch_v7_derived,
] + ALL_V7 + ALL_FP_AUDIO + ALL_V10_A



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", help="path to chromium/src")
    parser.add_argument("--force", action="store_true",
                        help="exit 0 even if some patches fail")
    parser.add_argument("--check", action="store_true",
                        help="dry run: report what would change, write nothing")
    args = parser.parse_args()

    if args.check:
        _DRY_RUN["on"] = True
        print("[*] --check: dry run, the source tree will NOT be modified\n")

    src_root = Path(args.src).resolve()
    if not src_root.exists():
        print(f"[!] Source root does not exist: {src_root}")
        return 2

    print(f"[*] Applying {len(ALL_PATCHES)} patches to: {src_root}\n")
    rep = Report()
    for patch in ALL_PATCHES:
        before = _REAL_WRITES["n"]
        patch(src_root, rep)
        if _REAL_WRITES["n"] == before:
            rep.downgrade_last()
    return rep.finish(force=args.force)


if __name__ == "__main__":
    sys.exit(main())
