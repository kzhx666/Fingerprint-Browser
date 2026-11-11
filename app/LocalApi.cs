using System;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 15: 本地 automation API。只监听 127.0.0.1，供 Playwright/脚本/外部工具
/// 批量控制实例。--api [port] 启动（默认 8790），不弹主窗口。
/// 端点：GET /instances, /health, /score；POST /launch?name=, /stop?name=
/// </summary>
public static class LocalApi
{
    static HttpListener? _listener;
    static CancellationTokenSource? _cts;

    public static void Start(int port = 8790)
    {
        _cts = new CancellationTokenSource();
        _listener = new HttpListener();
        _listener.Prefixes.Add($"http://127.0.0.1:{port}/");
        try { _listener.Start(); }
        catch (Exception e)
        {
            Console.Error.WriteLine("LocalApi 启动失败: " + e.Message);
            return;
        }
        Console.Error.WriteLine($"LocalApi listening on 127.0.0.1:{port}");
        _ = Task.Run(() => Loop(_cts.Token));
    }

    public static void Stop()
    {
        try { _cts?.Cancel(); _listener?.Stop(); _listener?.Close(); }
        catch { }
    }

    static async Task Loop(CancellationToken ct)
    {
        while (!ct.IsCancellationRequested && _listener is not null && _listener.IsListening)
        {
            HttpListenerContext ctx;
            try { ctx = await _listener.GetContextAsync(); }
            catch { break; }
            _ = Task.Run(() => Handle(ctx));
        }
    }

    static void Handle(HttpListenerContext ctx)
    {
        var req = ctx.Request;
        var res = ctx.Response;
        res.ContentType = "application/json; charset=utf-8";
        res.AddHeader("Access-Control-Allow-Origin", "*");
        object payload;
        int code = 200;
        try
        {
            var path = req.Url?.AbsolutePath ?? "/";
            var q = req.QueryString;
            var name = q["name"] ?? "";
            switch (path)
            {
                case "/instances":
                    var list = new List<object>();
                    foreach (var n in Store.ListInstances())
                    {
                        var p = Store.LoadProfile(n);
                        list.Add(new { name = n, seed = p.seed, platform = p.platform,
                                       timezone = p.timezone, proxy = Mask(p.proxy),
                                       running = ProcessQuery.ChromeDataDirs().ContainsKey(Paths.DataDir(n).TrimEnd('\\')) });
                    }
                    payload = new { ok = true, instances = list };
                    break;
                case "/launch":
                    if (string.IsNullOrEmpty(name)) { code = 400; payload = new { ok = false, error = "missing name" }; break; }
                    var lr = Launcher.Run(name, Store.LoadConfig(), _ => { });
                    payload = new { ok = lr.Ok, name, pid = lr.Pid, error = lr.Ok ? "" : (lr.Message ?? "launch failed") };
                    break;
                case "/stop":
                    if (string.IsNullOrEmpty(name)) { code = 400; payload = new { ok = false, error = "missing name" }; break; }
                    var killed = ProcessQuery.KillChromeByDataDir(Paths.DataDir(name).TrimEnd('\\'));
                    payload = new { ok = true, name, killed };
                    break;
                case "/health":
                    payload = new { ok = true, kernel = KernelSelfCheck.Check().Note,
                                    time = DateTime.Now.ToString("o") };
                    break;
                case "/score":
                    var rep = HealthScore.Evaluate(Store.LoadConfig());
                    payload = new { ok = true, average = rep.Average,
                                    items = rep.Items };
                    break;
                case "/export":   // 17 批量导出
                    var names = q["names"];
                    var sel = string.IsNullOrEmpty(names)
                        ? Store.ListInstances()
                        : names.Split(',').Select(x => x.Trim()).Where(x => x.Length > 0);
                    var file = ProductFeatures.ExportBundle(sel);
                    ProductFeatures.Log("export", string.Join(",", sel));
                    payload = new { ok = true, file, count = sel.Count() };
                    break;
                case "/import":   // 17 批量导入
                    var src = q["file"] ?? "";
                    if (string.IsNullOrEmpty(src) || !System.IO.File.Exists(src))
                    { code = 400; payload = new { ok = false, error = "missing/invalid file" }; break; }
                    var (cnt, errs) = ProductFeatures.ImportBundle(src);
                    ProductFeatures.Log("import", src);
                    payload = new { ok = errs.Count == 0, count = cnt, errors = errs };
                    break;
                case "/rotate":   // 16 代理轮换
                    if (string.IsNullOrEmpty(name)) { code = 400; payload = new { ok = false, error = "missing name" }; break; }
                    var np = ProductFeatures.RotateProxy(name);
                    payload = new { ok = np is not null, name, rotated = np is not null };
                    break;
                case "/warmup":   // 18 Cookie 预热
                    int wp = 8791;
                    if (int.TryParse(q["cdp"] ?? "", out var wpp)) wp = wpp;
                    var wn = ProductFeatures.Warmup(wp).GetAwaiter().GetResult();
                    payload = new { ok = true, queued = wn };
                    break;
                case "/audit":    // 21 日志审计
                    var ad = System.IO.Path.Combine(Paths.Root, "_audit");
                    var lines = new List<string>();
                    if (System.IO.Directory.Exists(ad))
                        foreach (var f in System.IO.Directory.GetFiles(ad, "*.jsonl").OrderByDescending(x => x).Take(3))
                            lines.AddRange(System.IO.File.ReadAllLines(f).TakeLast(50));
                    payload = new { ok = true, recent = lines.TakeLast(50).ToList() };
                    break;
                case "/ua":       // 19/20 UA 画像
                    payload = new { ok = true, profiles = ProductFeatures.UaProfiles.Keys.ToList(),
                                    current = Store.LoadConfig().uaProfile };
                    break;
                default:
                    code = 404;
                    payload = new { ok = false, error = "unknown endpoint",
                                    endpoints = new[] { "/instances", "/launch?name=", "/stop?name=", "/health", "/score" } };
                    break;
            }
        }
        catch (Exception e)
        {
            code = 500;
            payload = new { ok = false, error = e.Message };
        }
        var json = JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true });
        var buf = Encoding.UTF8.GetBytes(json);
        res.StatusCode = code;
        res.ContentLength64 = buf.Length;
        try { res.OutputStream.Write(buf, 0, buf.Length); res.OutputStream.Close(); }
        catch { }
    }

    static string Mask(string? proxy)
    {
        if (string.IsNullOrEmpty(proxy)) return "";
        // 永不通过 API 暴露账密
        var at = proxy.IndexOf('@');
        return at >= 0 ? proxy.Substring(0, proxy.IndexOf("://") + 3) + "***@" + proxy.Substring(at + 1)
                       : proxy;
    }
}
