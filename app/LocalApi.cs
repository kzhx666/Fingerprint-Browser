using System;
using System.Collections.Generic;
using System.Net;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;
using System.Security.Cryptography;
using System.Linq;

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
    static string _token = "";

    // P0-1: 每次启动生成随机令牌，写入 _api-token（仅本机可读），
    // 所有请求必须带 ?token= 或 X-KZ-Token 头，否则 401。
    static string TokenPath => System.IO.Path.Combine(Paths.Root, "_api-token");

    static string EnsureToken()
    {
        if (_token.Length > 0) return _token;
        try
        {
            if (System.IO.File.Exists(TokenPath))
            {
                _token = System.IO.File.ReadAllText(TokenPath).Trim();
                ProtectTokenFile(TokenPath);
            }
        }
        catch { }
        if (_token.Length < 16)
        {
            _token = Convert.ToHexString(RandomNumberGenerator.GetBytes(24)).ToLowerInvariant();
            try
            {
                System.IO.File.WriteAllText(TokenPath, _token);
                ProtectTokenFile(TokenPath);
            }
            catch { }
        }
        return _token;
    }

    /// <summary>P2-9: 限制令牌文件的 Windows ACL 权限，仅允许当前用户读取，其他用户无权访问。</summary>
    static void ProtectTokenFile(string path)
    {
        if (!OperatingSystem.IsWindows() || !System.IO.File.Exists(path)) return;
        try
        {
            var user = Environment.UserName;
            var psi = new System.Diagnostics.ProcessStartInfo("icacls.exe",
                $"\"{path}\" /inheritance:r /grant:r \"{user}\":(F)")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };
            using var p = System.Diagnostics.Process.Start(psi);
            p?.WaitForExit(3000);
        }
        catch { }
    }

    // P0-2: 会改变状态的端点，必须 POST
    static readonly HashSet<string> Mutating = new(StringComparer.OrdinalIgnoreCase)
    {
        "/launch", "/stop", "/import", "/rotate", "/template", "/loadset",
        "/warmup", "/screenshot", "/benchmark", "/export"
    };

    public static void Start(int port = 8790)
    {
        var tok = EnsureToken();
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
        Console.Error.WriteLine($"LocalApi token loaded from {TokenPath} (fingerprint: {(tok.Length > 6 ? tok.Substring(0, 6) : tok)}...)");
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
        // P0-2: 不再返回 Access-Control-Allow-Origin: *（避免任意网页跨域调用本地 API）
        object payload;
        int code = 200;
        try
        {
            // P2-9: 优先从请求头提取令牌 (X-KZ-Token 或 Authorization: Bearer <token>)，
            // 避免令牌在 URL、代理日志、浏览器历史中泄露。
            string supplied = req.Headers["X-KZ-Token"] ?? "";
            if (string.IsNullOrEmpty(supplied))
            {
                var authHdr = req.Headers["Authorization"] ?? "";
                if (authHdr.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase))
                    supplied = authHdr.Substring(7).Trim();
            }
            if (string.IsNullOrEmpty(supplied))
            {
                supplied = req.QueryString["token"] ?? "";
            }
            if (!FixedTimeEquals(supplied, EnsureToken()))
            {
                res.StatusCode = 401;
                var b = Encoding.UTF8.GetBytes("{\"ok\":false,\"error\":\"unauthorized: token required\"}");
                res.ContentLength64 = b.Length;
                try { res.OutputStream.Write(b, 0, b.Length); res.OutputStream.Close(); } catch { }
                return;
            }
            var path0 = req.Url?.AbsolutePath ?? "/";
            // P0-2: 状态变更端点必须 POST，GET 不能改状态
            if (Mutating.Contains(path0) &&
                !string.Equals(req.HttpMethod, "POST", StringComparison.OrdinalIgnoreCase))
            {
                res.StatusCode = 405;
                var b = Encoding.UTF8.GetBytes("{\"ok\":false,\"error\":\"method not allowed: use POST\"}");
                res.ContentLength64 = b.Length;
                try { res.OutputStream.Write(b, 0, b.Length); res.OutputStream.Close(); } catch { }
                return;
            }
            var path = req.Url?.AbsolutePath ?? "/";
            var q = req.QueryString;
            var name = q["name"] ?? "";
            // P0-3: 任何带 name 的端点先校验，拒绝 ../ 绝对路径等
            if (name.Length > 0 && PathGuard.ValidateName(name) is string nameErr)
            {
                code = 400;
                payload = new { ok = false, error = "invalid name: " + nameErr };
                Write(res, payload, code);
                return;
            }
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
                    var ow = string.Equals(q["overwrite"], "true", StringComparison.OrdinalIgnoreCase);
                    var (cnt, errs) = ProductFeatures.ImportBundle(src, ow);
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
                    var wr = ProductFeatures.Warmup(wp).GetAwaiter().GetResult();
                    payload = new { ok = wr.ok, visited = wr.visited, sites = wr.sites, error = wr.error };
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
                case "/templates":  // 27 配置模板
                    payload = new { ok = true, templates = FinalFeatures.Templates };
                    break;
                case "/template":   // 27 套用模板
                    var tk = q["key"] ?? "";
                    var applied = !string.IsNullOrEmpty(name) && FinalFeatures.ApplyTemplate(name, tk);
                    payload = new { ok = applied, name, key = tk };
                    break;
                case "/benchmark":  // 26 逐项跑分检测报告
                    var cfgB = Store.LoadConfig();
                    var (bok, brep, bfile, berr) = FinalFeatures.Benchmark(cfgB.chromeExe, q["seed"] ?? "914410");
                    payload = new { ok = bok && brep.ok, score = brep.totalScore, passed = brep.passedCount, failed = brep.failedCount, file = bfile, failedItems = brep.failedItems, checks = brep.checks, error = berr };
                    break;
                case "/configsets":   // 22 本地配置集
                    payload = new { ok = true, sets = FinalFeatures.ConfigSets() };
                    break;
                case "/loadset":      // 22 载入配置集
                    var sn = q["name"] ?? "";
                    var (lc, le) = FinalFeatures.LoadConfigSet(sn);
                    payload = new { ok = le.Count == 0, count = lc, errors = le };
                    break;
                case "/screenshot":  // 23 屏幕截图
                    var cfgS = Store.LoadConfig();
                    var surl = q["url"] ?? "https://example.com";
                    var sc = FinalFeatures.Screenshot(cfgS.chromeExe, surl, q["tag"]);
                    payload = new { ok = sc.ok, path = sc.path, error = sc.error };
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
        Write(res, payload, code);
    }

    static void Write(HttpListenerResponse res, object payload, int code)
    {
        var json = JsonSerializer.Serialize(payload, new JsonSerializerOptions { WriteIndented = true });
        var buf = Encoding.UTF8.GetBytes(json);
        res.StatusCode = code;
        res.ContentLength64 = buf.Length;
        try { res.OutputStream.Write(buf, 0, buf.Length); res.OutputStream.Close(); }
        catch { }
    }

    static bool FixedTimeEquals(string a, string b)
    {
        if (a.Length != b.Length) return false;
        int diff = 0;
        for (int i = 0; i < a.Length; i++) diff |= a[i] ^ b[i];
        return diff == 0;
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
