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

        try
        {
            var handler = new HttpClientHandler
            {
                Proxy = new WebProxy(r.ProxyUsed),
                UseProxy = true,
                ServerCertificateCustomValidationCallback = (_, _, _, _) => true
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

    static string Get(JsonElement e, string k)
        => e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String
           ? v.GetString() ?? "" : "";
}
