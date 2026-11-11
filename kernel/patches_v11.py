# -*- coding: utf-8 -*-
"""v11: 原生 SOCKS5 账密认证补丁（独立模块，避免 apply_patches.py 内嵌字符串转义）。
调用方在 ALL_PATCHES 末尾 + patches_v11.ALL_V11。
"""
from pathlib import Path


def _read(p):
    from apply_patches import read_file
    return read_file(p)


def _write(p, x):
    from apply_patches import write_file
    write_file(p, x)


def _rec(rep, name, status, err=""):
    rep.record(name, status, err)


def patch_mojom(src_root: Path, rep):
    name = "v11 mojom credentials"
    p = src_root / "services/network/public/mojom/network_param.mojom"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-auth" in x:
        _rec(rep, name, "ALREADY"); return
    a = "  HostPortPair? host_and_port;\n};"
    b = ("  HostPortPair? host_and_port;\n\n"
         "  // PATCH:v11-auth - optional SOCKS5 creds (empty = no auth).\n"
         "  string username;\n  string password;\n};")
    if a not in x:
        _rec(rep, name, "FAILED", "anchor missing"); return
    _write(p, x.replace(a, b, 1)); _rec(rep, name, "APPLIED")


def patch_ps_h(src_root: Path, rep):
    name = "v11 ProxyServer members"
    p = src_root / "net/base/proxy_server.h"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-auth" in x:
        _rec(rep, name, "ALREADY"); return
    a = "  ProxyServer(Scheme scheme, const HostPortPair& host_port_pair);"
    b = (a + "\n  // PATCH:v11-auth\n"
         "  ProxyServer(Scheme scheme, const HostPortPair& host_port_pair,\n"
         "              std::string username, std::string password);\n"
         "  const std::string& username() const { return username_; }\n"
         "  const std::string& password() const { return password_; }")
    if a not in x:
        _rec(rep, name, "FAILED", "ctor anchor"); return
    x = x.replace(a, b, 1)
    a2 = "  HostPortPair host_port_pair_;\n};"
    b2 = ("  HostPortPair host_port_pair_;\n  // PATCH:v11-auth\n"
          "  std::string username_;\n  std::string password_;\n};")
    if a2 not in x:
        _rec(rep, name, "FAILED", "member anchor"); return
    x = x.replace(a2, b2, 1)
    d = ("  // Default copy-constructor and assignment operator are OK!\n\n"
         "  // Constructs an invalid ProxyServer.\n"
         "  ProxyServer() = default;")
    dnew = ("  // PATCH:v11-auth - explicit special members (chromium-style).\n"
            "  ProxyServer();\n  ProxyServer(const ProxyServer&);\n"
            "  ProxyServer(ProxyServer&&) noexcept;\n"
            "  ProxyServer& operator=(const ProxyServer&);\n"
            "  ProxyServer& operator=(ProxyServer&&) noexcept;\n"
            "  ~ProxyServer();")
    if d in x:
        x = x.replace(d, dnew, 1)
    if "#include <string>" not in x:
        x = x.replace("#include", "#include <string>\n#include", 1)
    _write(p, x); _rec(rep, name, "APPLIED")


CTOR_OLD = """ProxyServer::ProxyServer(Scheme scheme, const HostPortPair& host_port_pair)
      : scheme_(scheme), host_port_pair_(host_port_pair) {
  if (scheme_ == SCHEME_INVALID) {
    // |host_port_pair| isn't relevant for these special schemes, so none should
    // have been specified. It is important for this to be consistent since we
    // do raw field comparisons in the equality and comparison functions.
    DCHECK_EQ(host_port_pair, HostPortPair());
    host_port_pair_ = HostPortPair();
  }
}"""


def patch_ps_cc(src_root: Path, rep):
    name = "v11 ProxyServer impl/pickle"
    p = src_root / "net/base/proxy_server.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-auth" in x:
        _rec(rep, name, "ALREADY"); return
    b = (CTOR_OLD + "\n\n// PATCH:v11-auth\n"
         "ProxyServer::ProxyServer(Scheme scheme,\n"
         "                         const HostPortPair& host_port_pair,\n"
         "                         std::string username,\n"
         "                         std::string password)\n"
         "    : scheme_(scheme), host_port_pair_(host_port_pair),\n"
         "      username_(std::move(username)),\n"
         "      password_(std::move(password)) {}\n")
    if CTOR_OLD not in x:
        _rec(rep, name, "FAILED", "ctor impl anchor"); return
    x = x.replace(CTOR_OLD, b, 1)
    pa = ("  pickle->WriteInt(static_cast<int>(scheme_));\n"
          "  pickle->WriteString(host_port_pair_.ToString());")
    pb = (pa + "\n  // PATCH:v11-auth\n"
          "  pickle->WriteString(username_);\n  pickle->WriteString(password_);")
    if pa not in x:
        _rec(rep, name, "FAILED", "Persist anchor"); return
    x = x.replace(pa, pb, 1)
    ca = "  return ProxyServer(scheme, host_port_pair);"
    cb = ("  // PATCH:v11-auth\n  std::string u, pw;\n"
          "  if (pickle_iter->ReadString(&u)) {}\n"
          "  if (pickle_iter->ReadString(&pw)) {}\n"
          "  return ProxyServer(scheme, host_port_pair, std::move(u),\n"
          "                     std::move(pw));")
    if ca not in x:
        _rec(rep, name, "FAILED", "FromPickle anchor"); return
    x = x.replace(ca, cb, 1)
    impl = ("\n// PATCH:v11-auth - out-of-line special members.\n"
            "ProxyServer::ProxyServer() = default;\n"
            "ProxyServer::ProxyServer(const ProxyServer&) = default;\n"
            "ProxyServer::ProxyServer(ProxyServer&&) noexcept = default;\n"
            "ProxyServer& ProxyServer::operator=(const ProxyServer&) = default;\n"
            "ProxyServer& ProxyServer::operator=(ProxyServer&&) noexcept = default;\n"
            "ProxyServer::~ProxyServer() = default;\n")
    if "ProxyServer::ProxyServer() = default;" not in x:
        x = x.replace("}  // namespace net", impl + "\n}  // namespace net", 1)
    _write(p, x); _rec(rep, name, "APPLIED")


def patch_traits_h(src_root: Path, rep):
    name = "v11 traits header"
    p = src_root / "services/network/public/cpp/network_param_mojom_traits.h"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-auth" in x:
        _rec(rep, name, "ALREADY"); return
    a = ("  static std::optional<net::HostPortPair> host_and_port(\n"
         "      const net::ProxyServer& s);")
    b = (a + "\n  // PATCH:v11-auth\n"
         "  static const std::string& username(const net::ProxyServer& s) {\n"
         "    return s.username();\n  }\n"
         "  static const std::string& password(const net::ProxyServer& s) {\n"
         "    return s.password();\n  }")
    if a not in x:
        _rec(rep, name, "FAILED", "anchor"); return
    _write(p, x.replace(a, b, 1)); _rec(rep, name, "APPLIED")


def patch_traits_cc(src_root: Path, rep):
    name = "v11 traits Read"
    p = src_root / "services/network/public/cpp/network_param_mojom_traits.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-auth" in x:
        _rec(rep, name, "ALREADY"); return
    a = "    *out = net::ProxyServer(scheme, std::move(*host_and_port));"
    b = ("    // PATCH:v11-auth\n    std::string u, pw;\n"
         "    if (!data.ReadUsername(&u) || !data.ReadPassword(&pw))\n"
         "      return false;\n"
         "    *out = net::ProxyServer(scheme, std::move(*host_and_port),\n"
         "                            std::move(u), std::move(pw));")
    if a not in x:
        _rec(rep, name, "FAILED", "anchor"); return
    _write(p, x.replace(a, b, 1)); _rec(rep, name, "APPLIED")


ALL_V11 = [
    patch_mojom, patch_ps_h, patch_ps_cc, patch_traits_h, patch_traits_cc,
]


# ---- 6. proxy_string_util: 解析 userinfo 进 ProxyServer 成员 ----
PARSE_OLD = """  // PATCH:v7-proxy-auth - accept userinfo instead of rejecting
  // the whole proxy (upstream returned ProxyServer() here).
  // NOTE: full native auth is NOT wired: CanonicalizeHost() rejects
  // embedded @, and SOCKS5ClientSocket::destination_ is the target
  // site, not the proxy. The v2 Python relay remains the active path.
  if (hostname_component.is_empty()) {
    return ProxyServer();
  }
  std::string auth_host;
  if (username_component.is_valid() || password_component.is_valid()) {
    std::string_view u = username_component.is_nonempty()
        ? host_and_port.substr(username_component.begin,
                              username_component.len) : "";
    std::string_view pw = password_component.is_nonempty()
        ? host_and_port.substr(password_component.begin,
                              password_component.len) : "";
    std::string_view hn = host_and_port.substr(
        hostname_component.begin, hostname_component.len);
    auth_host = base::StrCat({u, ":", pw, "@", hn});
  }

  std::string_view hostname = auth_host.empty()
      ? host_and_port.substr(hostname_component.begin,
                             hostname_component.len)
      : std::string_view(auth_host);"""


def patch_parse(src_root: Path, rep):
    name = "v11 parse userinfo -> members"
    p = src_root / "net/base/proxy_string_util.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-parse" in x:
        _rec(rep, name, "ALREADY"); return
    new = """  // PATCH:v11-parse - keep the CLEAN host (so CanonicalizeHost passes)
  // and carry credentials as explicit ProxyServer fields.
  if (hostname_component.is_empty()) {
    return ProxyServer();
  }
  std::string creds_user;
  std::string creds_pass;
  if (username_component.is_valid() || password_component.is_valid()) {
    std::string_view u = username_component.is_nonempty()
        ? host_and_port.substr(username_component.begin,
                              username_component.len) : "";
    std::string_view pw = password_component.is_nonempty()
        ? host_and_port.substr(password_component.begin,
                              password_component.len) : "";
    creds_user = std::string(u);
    creds_pass = std::string(pw);
  }
  std::string_view hostname =
      host_and_port.substr(hostname_component.begin,
                           hostname_component.len);"""
    pristine_guard = ("  if (username_component.is_valid() || password_component.is_valid() ||\n"
                      "      hostname_component.is_empty()) {\n"
                      "    return ProxyServer();\n"
                      "  }")
    if PARSE_OLD in x:
        x = x.replace(PARSE_OLD, new, 1)
    elif pristine_guard in x:
        x = x.replace(pristine_guard, new, 1)
    else:
        _rec(rep, name, "FAILED", "parse guard anchor missing"); return
    # 末尾构造：把账密传进 ProxyServer
    a = "  return ProxyServer::FromSchemeHostAndPort(scheme, hostname, port);"
    b = ("  // PATCH:v11-parse\n"
         "  ProxyServer server =\n"
         "      ProxyServer::FromSchemeHostAndPort(scheme, hostname, port);\n"
         "  return ProxyServer(server.scheme(), server.host_port_pair(),\n"
         "                     std::move(creds_user), std::move(creds_pass));")
    if a not in x:
        _rec(rep, name, "FAILED", "return anchor"); return
    x = x.replace(a, b, 1)
    _write(p, x); _rec(rep, name, "APPLIED")


# ---- 7. connect_job_params_factory: 把账密传进 SOCKSSocketParams ----
def patch_cjpf(src_root: Path, rep):
    name = "v11 connect_job_params_factory"
    p = src_root / "net/socket/connect_job_params_factory.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-cjpf" in x:
        _rec(rep, name, "ALREADY"); return
    a = ("    params = ConnectJobParams(base::MakeRefCounted<SOCKSSocketParams>(\n"
         "        std::move(params), proxy_server.scheme() == ProxyServer::SCHEME_SOCKS5,\n"
         "        ToHostPortPair(endpoint), network_anonymization_key,\n"
         "        *proxy_annotation_tag));")
    b = ("    // PATCH:v11-cjpf - forward proxy credentials to the socket.\n"
         "    params = ConnectJobParams(base::MakeRefCounted<SOCKSSocketParams>(\n"
         "        std::move(params), proxy_server.scheme() == ProxyServer::SCHEME_SOCKS5,\n"
         "        ToHostPortPair(endpoint), network_anonymization_key,\n"
         "        *proxy_annotation_tag, proxy_server.username(),\n"
         "        proxy_server.password()));")
    if a not in x:
        _rec(rep, name, "FAILED", "anchor"); return
    _write(p, x.replace(a, b, 1)); _rec(rep, name, "APPLIED")


# ---- 8. SOCKSSocketParams .h/.cc ----
def patch_sp_h(src_root: Path, rep):
    name = "v11 SOCKSSocketParams header"
    p = src_root / "net/socket/socks_connect_job.h"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-sp" in x:
        _rec(rep, name, "ALREADY"); return
    a = "                    const NetworkTrafficAnnotationTag& traffic_annotation);"
    b = ("                     const NetworkTrafficAnnotationTag& traffic_annotation,\n"
         "                    std::string username = \"\",\n"
         "                    std::string password = \"\");\n"
         "  // PATCH:v11-sp\n"
         "  const std::string& username() const { return username_; }\n"
         "  const std::string& password() const { return password_; }")
    if a not in x:
        _rec(rep, name, "FAILED", "ctor anchor"); return
    x = x.replace(a, b, 1)
    a2 = "  const NetworkTrafficAnnotationTag traffic_annotation_;" if False else None
    # 找私有成员末尾
    m = "  bool socks_v5_;\n"
    if m in x:
        x = x.replace(m, m + "  // PATCH:v11-sp\n  std::string username_;\n  std::string password_;\n", 1)
    if "#include <string>" not in x:
        x = x.replace("#include", "#include <string>\n#include", 1)
    _write(p, x); _rec(rep, name, "APPLIED")


def patch_sp_cc(src_root: Path, rep):
    name = "v11 SOCKSSocketParams impl"
    p = src_root / "net/socket/socks_connect_job.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-sp" in x:
        _rec(rep, name, "ALREADY"); return
    a = ("    const NetworkAnonymizationKey& network_anonymization_key,\n"
         "    const NetworkTrafficAnnotationTag& traffic_annotation)\n"
         "    : transport_params_(nested_params.take_transport()),\n"
         "      destination_(host_port_pair),\n"
         "      socks_v5_(socks_v5),\n"
         "      network_anonymization_key_(network_anonymization_key),\n"
         "      traffic_annotation_(traffic_annotation) {}")
    b = ("    const NetworkAnonymizationKey& network_anonymization_key,\n"
         "    const NetworkTrafficAnnotationTag& traffic_annotation,\n"
         "    std::string username, std::string password)\n"
         "    : transport_params_(nested_params.take_transport()),\n"
         "      destination_(host_port_pair),\n"
         "      socks_v5_(socks_v5),\n"
         "      network_anonymization_key_(network_anonymization_key),\n"
         "      traffic_annotation_(traffic_annotation),\n"
         "      username_(std::move(username)),\n"
         "      password_(std::move(password)) {}  // PATCH:v11-sp")
    if a not in x:
        _rec(rep, name, "FAILED", "ctor impl anchor"); return
    x = x.replace(a, b, 1)
    # 构造 SOCKS5ClientSocket 时传账密
    a2 = ("    socket_ = std::make_unique<SOCKS5ClientSocket>(\n"
          "        transport_connect_job_->PassSocket(), socks_params_->destination(),\n"
          "        socks_params_->traffic_annotation());")
    b2 = ("    // PATCH:v11-sp\n"
          "    socket_ = std::make_unique<SOCKS5ClientSocket>(\n"
          "        transport_connect_job_->PassSocket(), socks_params_->destination(),\n"
          "        socks_params_->traffic_annotation(), socks_params_->username(),\n"
          "        socks_params_->password());")
    if a2 not in x:
        _rec(rep, name, "FAILED", "socket ctor anchor"); return
    x = x.replace(a2, b2, 1)
    _write(p, x); _rec(rep, name, "APPLIED")


# ---- 9. SOCKS5ClientSocket 状态机 ----
def patch_socks5_h(src_root: Path, rep):
    name = "v11 SOCKS5ClientSocket header"
    p = src_root / "net/socket/socks5_client_socket.h"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-socks5" in x:
        _rec(rep, name, "ALREADY"); return
    a = ("  SOCKS5ClientSocket(std::unique_ptr<StreamSocket> transport_socket,\n"
         "                     const HostPortPair& destination,\n"
         "                     const NetworkTrafficAnnotationTag& traffic_annotation);")
    b = (a.replace("traffic_annotation);",
                   "traffic_annotation,\n"
                   "                      std::string username = \"\",\n"
                   "                      std::string password = \"\");") +
         "\n  // PATCH:v11-socks5\n"
         "  bool HasAuth() const { return !auth_user_.empty(); }\n"
         "  void BuildAuthHandshake();")
    if a not in x:
        _rec(rep, name, "FAILED", "ctor anchor"); return
    x = x.replace(a, b, 1)
    a2 = "  const HostPortPair destination_;"
    if a2 in x:
        x = x.replace(a2, a2 + "\n  // PATCH:v11-socks5\n  std::string auth_user_;\n  std::string auth_pass_;", 1)
    if "#include <string>" not in x:
        x = x.replace("#include", "#include <string>\n#include", 1)
    _write(p, x); _rec(rep, name, "APPLIED")


def patch_socks5_cc(src_root: Path, rep):
    name = "v11 SOCKS5ClientSocket auth state machine"
    p = src_root / "net/socket/socks5_client_socket.cc"
    if not p.exists():
        _rec(rep, name, "FAILED", "missing"); return
    x = _read(p)
    if "PATCH:v11-socks5" in x:
        _rec(rep, name, "ALREADY"); return
    # 构造函数
    a = ("SOCKS5ClientSocket::SOCKS5ClientSocket(\n"
         "    std::unique_ptr<StreamSocket> transport_socket,\n"
         "    const HostPortPair& destination,\n"
         "    const NetworkTrafficAnnotationTag& traffic_annotation)\n"
         "    : io_callback_(base::BindRepeating(&SOCKS5ClientSocket::OnIOComplete,\n"
         "                                       base::Unretained(this))),\n"
         "      transport_socket_(std::move(transport_socket)),\n"
         "      destination_(destination),\n"
         "      net_log_(transport_socket_->NetLog()),\n"
         "      traffic_annotation_(traffic_annotation) {}")
    b = ("SOCKS5ClientSocket::SOCKS5ClientSocket(\n"
         "    std::unique_ptr<StreamSocket> transport_socket,\n"
         "    const HostPortPair& destination,\n"
         "    const NetworkTrafficAnnotationTag& traffic_annotation,\n"
         "    std::string username, std::string password)\n"
         "    : io_callback_(base::BindRepeating(&SOCKS5ClientSocket::OnIOComplete,\n"
         "                                       base::Unretained(this))),\n"
         "      transport_socket_(std::move(transport_socket)),\n"
         "      destination_(destination),\n"
         "      net_log_(transport_socket_->NetLog()),\n"
         "      traffic_annotation_(traffic_annotation),\n"
         "      auth_user_(std::move(username)),\n"
         "      auth_pass_(std::move(password)) {}  // PATCH:v11-socks5")
    if a not in x:
        _rec(rep, name, "FAILED", "ctor impl anchor"); return
    x = x.replace(a, b, 1)
    # greet: 有账密时 offer method 0x02
    ga = ("static constexpr std::array<uint8_t, 3> kSOCKS5GreetWriteData{\n"
          "    0x05, 0x01, 0x00};  // no authentication")
    gb = ("// PATCH:v11-socks5 - method 0x02 is offered at runtime when\n"
          "// credentials are present; the base table stays no-auth.\n"
          "static constexpr std::array<uint8_t, 3> kSOCKS5GreetWriteData{\n"
          "    0x05, 0x01, 0x00};  // no authentication\n"
          "static constexpr std::array<uint8_t, 3> kSOCKS5GreetWriteAuth{\n"
          "    0x05, 0x01, 0x02};  // username/password")
    if ga not in x:
        _rec(rep, name, "FAILED", "greet table anchor"); return
    x = x.replace(ga, gb, 1)
    wa = ("    auto greet_buffer =\n"
          "        base::MakeRefCounted<WrappedIOBuffer>(kSOCKS5GreetWriteData);")
    wb = ("    // PATCH:v11-socks5\n"
          "    auto greet_buffer = HasAuth()\n"
          "        ? base::MakeRefCounted<WrappedIOBuffer>(\n"
          "              const_cast<uint8_t*>(kSOCKS5GreetWriteAuth.data()),\n"
          "              kSOCKS5GreetWriteAuth.size())\n"
          "        : base::MakeRefCounted<WrappedIOBuffer>(\n"
          "              const_cast<uint8_t*>(kSOCKS5GreetWriteData.data()),\n"
          "              kSOCKS5GreetWriteData.size());")
    if wa not in x:
        _rec(rep, name, "FAILED", "greet write anchor"); return
    x = x.replace(wa, wb, 1)
    # greet read: 接受 0x02 并要求后续 auth
    ra = "  if (read_data[1] != 0x00) {"
    rb = ("  // PATCH:v11-socks5 - server may select user/pass (0x02).\n"
          "  if (read_data[1] == 0x02 && HasAuth()) {\n"
          "    BuildAuthHandshake();\n"
          "    read_buf_.reset();\n"
          "    next_state_ = STATE_AUTH_WRITE;\n"
          "    return OK;\n"
          "  }\n"
          "  if (read_data[1] != 0x00) {")
    if ra not in x:
        _rec(rep, name, "FAILED", "greet read anchor"); return
    x = x.replace(ra, rb, 1)
    # BuildAuthHandshake 实现（在文件末尾 namespace 前）
    impl = ("\n// PATCH:v11-socks5 - RFC1928 username/password sub-negotiation.\n"
            "void SOCKS5ClientSocket::BuildAuthHandshake() {\n"
            "  std::vector<uint8_t> buf;\n"
            "  buf.push_back(0x01);\n"
            "  buf.push_back(static_cast<uint8_t>(auth_user_.size() & 0xFF));\n"
            "  buf.insert(buf.end(), auth_user_.begin(), auth_user_.end());\n"
            "  buf.push_back(static_cast<uint8_t>(auth_pass_.size() & 0xFF));\n"
            "  buf.insert(buf.end(), auth_pass_.begin(), auth_pass_.end());\n"
            "  auth_buf_ = base::MakeRefCounted<IOBufferWithSize>(buf.size());\n"
            "  std::copy(buf.begin(), buf.end(), auth_buf_->data());\n"
            "  write_buf_ = base::MakeRefCounted<DrainableIOBuffer>(\n"
            "      auth_buf_, auth_buf_->size());\n"
            "}\n")
    x = x.replace("}  // namespace net", impl + "}  // namespace net", 1)
    _write(p, x); _rec(rep, name, "APPLIED")


ALL_V11 += [
    patch_parse, patch_cjpf, patch_sp_h, patch_sp_cc,
    patch_socks5_h, patch_socks5_cc,
]


def patch_state_machine(src_root: Path, rep):
    """Ensure the 4 AUTH states + implementations exist in a clean tree."""
    name = "v11 socks5 auth states"
    h = src_root / "net/socket/socks5_client_socket.h"
    cc = src_root / "net/socket/socks5_client_socket.cc"
    if not (h.exists() and cc.exists()):
        _rec(rep, name, "FAILED", "missing"); return
    ht, ct = _read(h), _read(cc)
    if "STATE_AUTH_WRITE" in ht and "DoAuthReadComplete" in ct:
        _rec(rep, name, "ALREADY"); return
    # enum
    a = "    STATE_GREET_READ_COMPLETE,\n    STATE_HANDSHAKE_WRITE,"
    b = ("    STATE_GREET_READ_COMPLETE,\n    // PATCH:v11-socks5\n"
         "    STATE_AUTH_WRITE,\n    STATE_AUTH_WRITE_COMPLETE,\n"
         "    STATE_AUTH_READ,\n    STATE_AUTH_READ_COMPLETE,\n"
         "    STATE_HANDSHAKE_WRITE,")
    if a in ht: ht = ht.replace(a, b, 1)
    if "int DoAuthReadComplete" not in ht:
        ht = ht.replace("  void BuildAuthHandshake();",
            "  void BuildAuthHandshake();\n  int DoAuthWrite();\n"
            "  int DoAuthWriteComplete(int result);\n  int DoAuthRead();\n"
            "  int DoAuthReadComplete(int result);", 1)
    # switch cases
    sa = ("      case STATE_GREET_READ_COMPLETE:\n"
          "        rv = DoGreetReadComplete(rv);\n"
          "        net_log_.EndEventWithNetErrorCode(NetLogEventType::SOCKS5_GREET_READ,\n"
          "                                          rv);\n"
          "        break;")
    sb = sa + ("\n      // PATCH:v11-socks5\n"
               "      case STATE_AUTH_WRITE: rv = DoAuthWrite(); break;\n"
               "      case STATE_AUTH_WRITE_COMPLETE:\n"
               "        rv = DoAuthWriteComplete(rv); break;\n"
               "      case STATE_AUTH_READ: rv = DoAuthRead(); break;\n"
               "      case STATE_AUTH_READ_COMPLETE:\n"
               "        rv = DoAuthReadComplete(rv); break;")
    if sa in ct: ct = ct.replace(sa, sb, 1)
    # 方法实现（若缺）
    if "int SOCKS5ClientSocket::DoAuthWrite()" not in ct:
        impl = """
int SOCKS5ClientSocket::DoAuthWrite() {
  DCHECK(write_buf_); next_state_ = STATE_AUTH_WRITE_COMPLETE;
  return transport_socket_->Write(write_buf_.get(),
      write_buf_->BytesRemaining(), io_callback_, traffic_annotation_);
}
int SOCKS5ClientSocket::DoAuthWriteComplete(int result) {
  if (result < 0) return result;
  write_buf_->DidConsume(result);
  if (write_buf_->BytesRemaining() > 0) { next_state_ = STATE_AUTH_WRITE; return OK; }
  write_buf_.reset(); next_state_ = STATE_AUTH_READ; return OK;
}
int SOCKS5ClientSocket::DoAuthRead() {
  next_state_ = STATE_AUTH_READ_COMPLETE;
  if (!read_buf_) { read_buf_ = base::MakeRefCounted<GrowableIOBuffer>();
                    read_buf_->SetCapacity(2); }
  return transport_socket_->Read(read_buf_.get(),
      read_buf_->RemainingCapacity(), io_callback_);
}
int SOCKS5ClientSocket::DoAuthReadComplete(int result) {
  if (result <= 0) return ERR_SOCKS_CONNECTION_FAILED;
  read_buf_->set_offset(read_buf_->offset() + result);
  if (read_buf_->RemainingCapacity() > 0) { next_state_ = STATE_AUTH_READ; return OK; }
  auto d = read_buf_->span_before_offset();
  if (d[0] != 0x01 || d[1] != 0x00) return ERR_SOCKS_CONNECTION_FAILED;
  read_buf_.reset(); next_state_ = STATE_HANDSHAKE_WRITE; return OK;
}
"""
        ct = ct.replace("}  // namespace net", impl + "\n}  // namespace net", 1)
    _write(h, ht); _write(cc, ct)
    _rec(rep, name, "APPLIED")


ALL_V11 += [patch_state_machine]
