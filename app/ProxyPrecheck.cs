using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Text.Json;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 启动 chrome 之前的出口 IP 预检。
/// 用即将传给浏览器的同一个 --proxy-server 去请求 ipinfo，
/// 拿到出口 IP/国家，失败就拦住启动，避免代理挂了浏览器用直连裸奔。
/// 纯管理器进程内实现，不需要改/编译 chrome.dll。
/// </summary>
public static class ProxyPrecheck
{
    public sealed class Result
    {
        public bool Ok;
        public string Ip = "";
        public string Country = "";
        public string Region = "";
        public string City = "";
        public string Org = "";
        public string Error = "";
        public string ProxyUsed = "";
        public string DeclaredRegion = "";
        public string RegionVerdict = "";   // match / mismatch / unknown
        public string RegionNote = "";
    }

    static readonly Regex ProxyRe = new(@"--proxy-server=(\S+)", RegexOptions.Compiled);

    public static Result Check(string args, Action<string>? say = null)
    {
        var r = new Result();
        var m = ProxyRe.Match(args);
        if (!m.Success)
        {
            // 没配代理 = 直连，也算一种"出口"，但明确提示
            r.Ok = true;
            r.ProxyUsed = "(直连 / 系统代理)";
            r.Error = "未配置 --proxy-server";
            return r;
        }
        r.ProxyUsed = m.Groups[1].Value.Trim('"');
        say?.Invoke($"预检出口 IP（经 {r.ProxyUsed}）…");

        // P1-6: 带账密 socks5 预检必须真的执行认证并出网探测，
        // 只做 TCP 连通会让"密码错误/上游不可用"漏过。
        if (r.ProxyUsed.StartsWith("socks5://", StringComparison.OrdinalIgnoreCase)
            && r.ProxyUsed.Contains("@"))
        {
            return CheckSocks5Auth(r, say);
        }

        try
        {
            var handler = new HttpClientHandler
            {
                Proxy = new WebProxy(r.ProxyUsed),
                UseProxy = true
                // P0-5: 不再设置 ServerCertificateCustomValidationCallback，
                // 使用 .NET 默认严格证书链校验；证书异常会抛异常使预检失败。
            };
            using var hc = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(12) };
            using var cts = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(12));
            var json = hc.GetStringAsync("https://ipinfo.io/json", cts.Token)
                         .GetAwaiter().GetResult();
            using var doc = JsonDocument.Parse(json);
            var root = doc.RootElement;
            r.Ip = Get(root, "ip");
            r.Country = Get(root, "country");
            r.Region = Get(root, "region");
            r.City = Get(root, "city");
            r.Org = Get(root, "org");
            r.Ok = !string.IsNullOrEmpty(r.Ip);
            if (!r.Ok) r.Error = "出口探测返回空 IP";
        }
        catch (Exception e)
        {
            r.Ok = false;
            r.Error = e.InnerException?.Message ?? e.Message;
        }
        return r;
    }

    /// <summary>
    /// 把本次预检结果持久化到 _exit-status.json（仅本机运行时数据，
    /// 不进仓库；不存完整 IP，只留国家/地区）。管理界面据此显示每个实例的出口与地区比对状态。
    /// </summary>
    public static void Save(string instanceName, Result r)
    {
        try
        {
            var path = Path.Combine(Paths.Root, "_exit-status.json");
            var all = LoadAll();
            all[instanceName] = new ExitRecord {
                ip = "", country = r.Country, region = r.Region,
                city = r.City, declared = r.DeclaredRegion,
                verdict = r.RegionVerdict, note = r.RegionNote,
                at = DateTime.Now.ToString("MM-dd HH:mm")
            };
            var opts = new JsonSerializerOptions { WriteIndented = true };
            File.WriteAllText(path, JsonSerializer.Serialize(all, opts));
        }
        catch { }   // 状态文件写失败不影响启动
    }

    public sealed class ExitRecord
    {
        public string ip { get; set; } = "";
        public string country { get; set; } = "";
        public string region { get; set; } = "";
        public string city { get; set; } = "";
        public string declared { get; set; } = "";
        public string verdict { get; set; } = "";
        public string note { get; set; } = "";
        public string at { get; set; } = "";
    }

    public static Dictionary<string, ExitRecord> LoadAll()
    {
        try
        {
            var path = Path.Combine(Paths.Root, "_exit-status.json");
            if (!File.Exists(path)) return new();
            return JsonSerializer.Deserialize<Dictionary<string, ExitRecord>>(
                       File.ReadAllText(path)) ?? new();
        }
        catch { return new(); }
    }

    /// <summary>给界面用的一行摘要：US ✓ / HK ⚠ / 未检。</summary>
    public static string Display(string instanceName)
    {
        var all = LoadAll();
        if (!all.TryGetValue(instanceName, out var e)) return "未检";
        if (string.IsNullOrEmpty(e.country)) return "未检";
        var badge = e.verdict switch {
            "match" => "✓", "mismatch" => "⚠", _ => "·"
        };
        return $"{e.country} {badge}";
    }

    /// <summary>
    /// 从实例名称前缀 + 时区推断声明地区，返回 ISO 两位国家码或空。
    /// 显式 region 字段优先；支持中文名/英文名/ISO 码。
    /// </summary>
    public static string InferDeclared(string name, string timezone,
                                       string explicitRegion)
    {
        var r = (explicitRegion ?? "").Trim();
        if (r.Length > 0) return NormalizeRegion(r);
        var n = name ?? "";
        // 名称前缀：美国/日本/香港/英国/德国/新加坡...
        var zh = new (string key, string code)[] {
            ("美国","US"),("日本","JP"),("香港","HK"),("台湾","TW"),
            ("英国","GB"),("德国","DE"),("法国","FR"),("新加坡","SG"),
            ("韩国","KR"),("加拿大","CA"),("澳大利亚","AU"),("荷兰","NL")};
        foreach (var (k, c) in zh) if (n.StartsWith(k)) return c;
        var en = new (string key, string code)[] {
            ("US ","US"),("USA","US"),("UK ","GB"),("JP ","JP"),("HK ","HK"),
            ("DE ","DE"),("SG ","SG"),("CA ","CA")};
        foreach (var (k, c) in en)
            if (n.StartsWith(k, StringComparison.OrdinalIgnoreCase)) return c;
        // 时区兜底：America/* -> 按城市粗判，Asia/Tokyo -> JP ...
        var tz = timezone ?? "";
        if (tz.StartsWith("America/Los_Angeles") ||
            tz.StartsWith("America/New_York") ||
            tz.StartsWith("America/Chicago")) return "US";
        if (tz.StartsWith("Asia/Tokyo")) return "JP";
        if (tz.StartsWith("Asia/Hong_Kong")) return "HK";
        if (tz.StartsWith("Asia/Taipei")) return "TW";
        if (tz.StartsWith("Europe/London")) return "GB";
        if (tz.StartsWith("Europe/Berlin")) return "DE";
        if (tz.StartsWith("Asia/Shanghai")) return "CN";
        if (tz.StartsWith("Asia/Singapore")) return "SG";
        return "";
    }

    static string NormalizeRegion(string s)
    {
        s = s.Trim().ToUpperInvariant();
        if (s.Length == 2) return s;
        return s switch {
            "美国" or "USA" or "UNITED STATES" => "US",
            "日本" or "JAPAN" => "JP",
            "香港" or "HONG KONG" => "HK",
            "台湾" or "TAIWAN" => "TW",
            "英国" or "UNITED KINGDOM" => "GB",
            "德国" or "GERMANY" => "DE",
            "法国" or "FRANCE" => "FR",
            "新加坡" or "SINGAPORE" => "SG",
            "韩国" or "KOREA" => "KR",
            "加拿大" or "CANADA" => "CA",
            "澳大利亚" or "AUSTRALIA" => "AU",
            "中国" or "CHINA" => "CN",
            "荷兰" or "NETHERLANDS" => "NL",
            _ => s
        };
    }

    /// <summary>把 ipinfo 的 Country 与声明地区比对，写回 Result。</summary>
    public static void CompareRegion(Result r, string name, string timezone,
                                     string explicitRegion)
    {
        r.DeclaredRegion = InferDeclared(name, timezone, explicitRegion);
        if (r.DeclaredRegion.Length == 0 || r.Country.Length == 0)
        {
            r.RegionVerdict = "unknown";
            r.RegionNote = "未声明地区或出口国家未知，跳过比对";
            return;
        }
        var actual = r.Country.Trim().ToUpperInvariant();
        if (actual == r.DeclaredRegion)
        {
            r.RegionVerdict = "match";
            r.RegionNote = $"地区一致：声明 {r.DeclaredRegion} / 出口 {actual}";
        }
        else
        {
            r.RegionVerdict = "mismatch";
            r.RegionNote = $"地区不符：声明 {r.DeclaredRegion}，但出口 {actual}" +
                           $" ({r.Region}/{r.City})。Google 等服务可能据此判定地区异常。";
        }
    }

    /// <summary>
    /// P1-6: RFC1928 SOCKS5 用户名/密码认证 + CONNECT + TLS 出网探测。
    /// 端口通但密码错 / 上游不通 -> 预检失败，不会误放行裸奔。
    /// P0-5: SslStream 使用默认严格证书校验（不传回调）。
    /// </summary>
    static Result CheckSocks5Auth(Result r, Action<string>? say)
    {
        System.Net.Sockets.TcpClient? tcp = null;
        try
        {
            var u = new Uri(r.ProxyUsed);
            var user = Uri.UnescapeDataString(u.UserInfo.Split(':')[0]);
            var pass = u.UserInfo.Contains(':')
                ? Uri.UnescapeDataString(u.UserInfo.Substring(u.UserInfo.IndexOf(':') + 1)) : "";
            tcp = new System.Net.Sockets.TcpClient();
            var ct = tcp.ConnectAsync(u.Host, u.Port);
            if (!ct.Wait(TimeSpan.FromSeconds(8)) || !tcp.Connected)
            { r.Ok = false; r.Error = "socks5 节点连不上"; return r; }
            var s = tcp.GetStream();
            s.ReadTimeout = s.WriteTimeout = 8000;

            // greet: VER NMETHODS METHODS(0x02 user/pass)
            s.Write(new byte[] { 0x05, 0x01, 0x02 }, 0, 3);
            int m0 = s.ReadByte(), m1 = s.ReadByte();
            if (m0 != 0x05 || m1 < 0) { r.Ok = false; r.Error = "socks5 greet 失败"; return r; }
            if (m1 == 0x02)
            {
                var ub = System.Text.Encoding.UTF8.GetBytes(user);
                var pb = System.Text.Encoding.UTF8.GetBytes(pass);
                if (ub.Length > 255 || pb.Length > 255)
                { r.Ok = false; r.Error = "socks5 账密过长"; return r; }
                var auth = new System.IO.MemoryStream();
                auth.WriteByte(0x01);
                auth.WriteByte((byte)ub.Length); auth.Write(ub, 0, ub.Length);
                auth.WriteByte((byte)pb.Length); auth.Write(pb, 0, pb.Length);
                var ab = auth.ToArray(); s.Write(ab, 0, ab.Length);
                int a0 = s.ReadByte(), a1 = s.ReadByte();
                if (a0 != 0x01 || a1 != 0x00)
                { r.Ok = false; r.Error = "socks5 认证失败（账密错误或被拒）"; return r; }
            }
            else if (m1 != 0x00)
            { r.Ok = false; r.Error = "socks5 不支持的认证方法 " + m1; return r; }

            // CONNECT ipinfo.io:443（域名交由代理远程解析）
            var host = System.Text.Encoding.ASCII.GetBytes("ipinfo.io");
            var conn = new System.IO.MemoryStream();
            conn.WriteByte(0x05); conn.WriteByte(0x01); conn.WriteByte(0x00);
            conn.WriteByte(0x03); conn.WriteByte((byte)host.Length); conn.Write(host, 0, host.Length);
            conn.WriteByte(0x01); conn.WriteByte(0xBB);   // 443
            var cb = conn.ToArray(); s.Write(cb, 0, cb.Length);
            int c0 = s.ReadByte(), c1 = s.ReadByte();
            if (c0 != 0x05 || c1 != 0x00)
            { r.Ok = false; r.Error = "socks5 CONNECT 被拒(上游不可用) " + c1; return r; }
            s.ReadByte();                 // RSV
            int atyp = s.ReadByte();     // ATYP
            if (atyp == 1) { for (int i = 0; i < 4; i++) s.ReadByte(); }
            else if (atyp == 3) { int l = s.ReadByte(); for (int i = 0; i < l; i++) s.ReadByte(); }
            else if (atyp == 4) { for (int i = 0; i < 16; i++) s.ReadByte(); }
            s.ReadByte(); s.ReadByte();  // port

            // TLS（默认严格证书校验）+ HTTP GET 出网
            using var ssl = new System.Net.Security.SslStream(s, false);
            ssl.AuthenticateAsClient("ipinfo.io");
            var req = System.Text.Encoding.ASCII.GetBytes(
                "GET /json HTTP/1.1\r\nHost: ipinfo.io\r\nConnection: close\r\n\r\n");
            ssl.Write(req, 0, req.Length); ssl.Flush();
            var ms = new System.IO.MemoryStream();
            var buf = new byte[4096]; int n; var deadline = DateTime.Now.AddSeconds(10);
            while (DateTime.Now < deadline && (n = ssl.Read(buf, 0, buf.Length)) > 0)
                ms.Write(buf, 0, n);
            var text = System.Text.Encoding.UTF8.GetString(ms.ToArray());
            var bodyStart = text.IndexOf("\r\n\r\n");
            if (bodyStart < 0) { r.Ok = false; r.Error = "socks5 出网无有效响应"; return r; }
            var body = text.Substring(bodyStart + 4);
            // 处理 chunked
            if (text.Contains("Transfer-Encoding: chunked", StringComparison.OrdinalIgnoreCase))
            {
                var sb = new System.Text.StringBuilder(); var pos = 0;
                while (pos < body.Length)
                {
                    var nl = body.IndexOf("\r\n", pos); if (nl < 0) break;
                    var sizeHex = body.Substring(pos, nl - pos).Trim();
                    if (!int.TryParse(sizeHex, System.Globalization.NumberStyles.HexNumber, null, out var sz) || sz == 0) break;
                    pos = nl + 2; if (pos + sz > body.Length) break;
                    sb.Append(body, pos, sz); pos += sz + 2;
                }
                body = sb.ToString();
            }
            using var doc = JsonDocument.Parse(body);
            var root = doc.RootElement;
            r.Ip = Get(root, "ip"); r.Country = Get(root, "country");
            r.Region = Get(root, "region"); r.City = Get(root, "city"); r.Org = Get(root, "org");
            r.Ok = !string.IsNullOrEmpty(r.Ip);
            if (!r.Ok) r.Error = "socks5 出网探测返回空 IP";
            say?.Invoke($"socks5 认证+出网探测: {r.Ok} {r.Ip} {r.Country}");
            return r;
        }
        catch (Exception e)
        {
            r.Ok = false;
            r.Error = "socks5 认证/出网探测失败: " + (e.InnerException?.Message ?? e.Message);
            return r;
        }
        finally { try { tcp?.Close(); } catch { } }
    }

    static string Get(JsonElement e, string k)
        => e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String
           ? v.GetString() ?? "" : "";
}
