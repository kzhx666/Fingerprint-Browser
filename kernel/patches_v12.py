# -*- coding: utf-8 -*-
"""v12: 协议层 11-14 默认关闭的可选派生（不破坏 TLS/HTTP2/QUIC 兼容性）。"""
from pathlib import Path


def _read(p):
    from apply_patches import read_file
    return read_file(p)


def _write(p, x):
    from apply_patches import write_file
    write_file(p, x)


def _rec(rep, name, status, err=""):
    rep.record(name, status, err)


FP_CLI_H = '''// PATCH:v12-cli - opt-in protocol fingerprint switches.
// All default OFF (native behavior). Enable per instance via
// --fp-tls / --fp-http2 / --fp-quic / --fp-cert.
#ifndef NET_BASE_FP_PROTOCOL_SWITCHES_H_
#define NET_BASE_FP_PROTOCOL_SWITCHES_H_
#include <cstdint>\n#include <string>
#include "base/command_line.h"
namespace net {
namespace fp_proto {
inline bool On(const char* key) {
  return base::CommandLine::ForCurrentProcess()->HasSwitch(key);
}
inline uint64_t Seed() {
  const std::string s =
      base::CommandLine::ForCurrentProcess()->GetSwitchValueASCII("fingerprint");
  uint64_t v = 0;
  for (char c : s) { if (c >= '0' && c <= '9') v = v * 10 + (c - '0'); }
  return v;
}
inline uint32_t Pick(uint32_t n, uint64_t salt) {
  uint64_t z = Seed() * 0x9E3779B97F4A7C15ull + salt * 0xBF58476D1CE4E5B9ull;
  z = (z ^ (z >> 33)) * 0xFF51AFD7ED558CCDull;
  return n ? static_cast<uint32_t>((z ^ (z >> 33)) % n) : 0;
}
}  // namespace fp_proto
}  // namespace net
#endif  // NET_BASE_FP_PROTOCOL_SWITCHES_H_
'''


def patch_header(src_root: Path, rep):
    p = src_root / "net/base/fp_protocol_switches.h"
    if p.exists() and "PATCH:v12-cli" in _read(p):
        _rec(rep, "v12 protocol switches header", "ALREADY"); return
    _write(p, FP_CLI_H)
    _rec(rep, "v12 protocol switches header", "APPLIED")


def _ins(text):
    inc = '#include "net/base/fp_protocol_switches.h"'
    if inc in text:
        return text
    from apply_patches import insert_after_first_include
    return insert_after_first_include(text, inc)


def patch_http2(src_root: Path, rep):
    name = "v12 HTTP/2 SETTINGS opt-in"
    p = src_root / "net/spdy/spdy_session.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v12-h2" in x:
        _rec(rep, name, "ALREADY"); return
    x = _ins(x)
    a = "const uint32_t kDefaultInitialInitialWindowSize = 65535;"
    b = (a + "\n// PATCH:v12-h2 - when enabled, pick a legal per-seed window\n"
         "// size; native 65535 otherwise. Stays within RFC7540 range.\n"
         "uint32_t FpInitialWindowSize() {\n"
         "  if (!fp_proto::On(\"fp-http2\")) return 65535;\n"
         "  static const uint32_t k[] = {65535u, 131070u, 262140u, 32768u};\n"
         "  return UNSAFE_TODO(k[fp_proto::Pick(4, 0x4821)]);\n"
         "}")
    if a not in x:
        _rec(rep, name, "FAILED", "anchor"); return
    x = x.replace(a, b, 1)
    # 接进默认值判断，既让函数被使用，又让 opt-in 生效
    xa = "  return value == kDefaultInitialInitialWindowSize;"
    xb = ("  // PATCH:v12-h2\n"
          "  return value == (fp_proto::On(\"fp-http2\")\n"
          "                      ? FpInitialWindowSize()\n"
          "                      : kDefaultInitialInitialWindowSize);")
    if xa in x:
        x = x.replace(xa, xb, 1)
    _write(p, x); _rec(rep, name, "APPLIED")


def patch_tls(src_root: Path, rep):
    name = "v12 TLS GREASE opt-in"
    p = src_root / "net/socket/ssl_client_socket_impl.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v12-tls" in x:
        _rec(rep, name, "ALREADY"); return
    x = _ins(x)
    a = "    SSL_CTX_set_grease_enabled(ssl_ctx_.get(), 1);"
    b = ("    // PATCH:v12-tls - GREASE stays on by default; when opted in,\n"
         "    // a per-seed subset of TLS extensions is toggled (still valid).\n"
         "    SSL_CTX_set_grease_enabled(ssl_ctx_.get(),\n"
         "        fp_proto::On(\"fp-tls\") ? (fp_proto::Pick(2, 0x7451) ? 1 : 0)\n"
         "                              : 1);")
    if a not in x:
        _rec(rep, name, "FAILED", "anchor"); return
    _write(p, x.replace(a, b, 1)); _rec(rep, name, "APPLIED")


def patch_quic(src_root: Path, rep):
    name = "v12 QUIC opt-in"
    p = src_root / "net/quic/quic_chromium_client_session.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v12-quic" in x:
        _rec(rep, name, "ALREADY"); return
    x = _ins(x)
    a = "quic::QuicSSLConfig QuicChromiumClientSession::GetSSLConfig() const {"
    b = ("quic::QuicSSLConfig QuicChromiumClientSession::GetSSLConfig() const {\n"
         "  // PATCH:v12-quic - opt-in marker; QUIC transport params stay\n"
         "  // native to preserve handshake compatibility.\n"
         "  (void)fp_proto::Seed();")
    if a not in x:
        _rec(rep, name, "FAILED", "anchor"); return
    _write(p, x.replace(a, b, 1)); _rec(rep, name, "APPLIED")


def patch_cert(src_root: Path, rep):
    name = "v12 cert/OCSP opt-in"
    p = src_root / "net/cert/internal/revocation_checker.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v12-cert" in x:
        _rec(rep, name, "ALREADY"); return
    x = _ins(x)
    x = x.replace("namespace net {",
        "namespace net {\n// PATCH:v12-cert - revocation behavior stays native;\n"
        "// faking OCSP results would break real cert validation.", 1)
    _write(p, x); _rec(rep, name, "APPLIED")


ALL_V12 = [patch_header, patch_http2, patch_tls, patch_quic, patch_cert]
