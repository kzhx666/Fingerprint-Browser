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

    static string Get(JsonElement e, string k)
        => e.TryGetProperty(k, out var v) && v.ValueKind == JsonValueKind.String
           ? v.GetString() ?? "" : "";
}
