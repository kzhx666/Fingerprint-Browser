using System.Text;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace KZManager;

/// <summary>批次④ 26/27/28：跑分 / 配置模板 / 实例级 DNS。</summary>
public static class FinalFeatures
{
    // ---- 27 指纹配置模板 ----
    public sealed class Template
    {
        public string key = "";
        public string title = "";
        public string timezone = "";
        public string lang = "";
        public string platform = "windows";
        public string fp_webgl = "intel";
        public string geo = "";
        public string region = "";
    }

    public static readonly List<Template> Templates = new()
    {
        new Template { key="us-west", title="美国西部 / 洛杉矶",
            timezone="America/Los_Angeles", lang="en-US,en", geo="34.0522,-118.2437,30", region="US" },
        new Template { key="us-east", title="美国东部 / 纽约",
            timezone="America/New_York", lang="en-US,en", geo="40.7128,-74.0060,30", region="US" },
        new Template { key="uk", title="英国 / 伦敦",
            timezone="Europe/London", lang="en-GB,en", geo="51.5074,-0.1278,30", region="GB" },
        new Template { key="jp", title="日本 / 东京",
            timezone="Asia/Tokyo", lang="ja-JP,ja", geo="35.6762,139.6503,30", region="JP" },
        new Template { key="hk", title="香港",
            timezone="Asia/Hong_Kong", lang="zh-HK,zh", geo="22.3193,114.1694,30", region="HK" },
        new Template { key="de", title="德国 / 柏林",
            timezone="Europe/Berlin", lang="de-DE,de", geo="52.5200,13.4050,30", region="DE" },
        new Template { key="sg", title="新加坡",
            timezone="Asia/Singapore", lang="en-SG,en", geo="1.3521,103.8198,30", region="SG" },
    };

    /// <summary>把模板套用到实例（只改地区相关字段，不动 proxy/seed/icon）。</summary>
    public static bool ApplyTemplate(string instanceName, string key)
    {
        if (PathGuard.ValidateName(instanceName) is not null) return false;  // P0-3
        var t = Templates.FirstOrDefault(x => x.key == key);
        if (t is null) return false;
        var p = Path.Combine(Paths.Root, instanceName, "profile.json");
        if (!File.Exists(p)) return false;
        try
        {
            var doc = JsonDocument.Parse(File.ReadAllText(p));
            var d = new Dictionary<string, JsonElement>();
            foreach (var pr in doc.RootElement.EnumerateObject()) d[pr.Name] = pr.Value.Clone();
            d["timezone"] = JsonSerializer.SerializeToElement(t.timezone);
            d["lang"] = JsonSerializer.SerializeToElement(t.lang);
            d["platform"] = JsonSerializer.SerializeToElement(t.platform);
            d["fp_webgl"] = JsonSerializer.SerializeToElement(t.fp_webgl);
            d["geo"] = JsonSerializer.SerializeToElement(t.geo);
            d["region"] = JsonSerializer.SerializeToElement(t.region);
            File.WriteAllText(p, JsonSerializer.Serialize(d, new JsonSerializerOptions { WriteIndented = true }));
            ProductFeatures.Log("template.apply", instanceName + " <- " + key);
            return true;
        }
        catch { return false; }
    }

    // ---- 28 实例级 DNS（按声明地区选公共 DNS）----
    static readonly Dictionary<string, string> RegionDns = new()
    {
        ["US"] = "8.8.8.8, 1.1.1.1",
        ["GB"] = "1.1.1.1, 8.8.8.8",
        ["DE"] = "1.1.1.1, 9.9.9.9",
        ["JP"] = "1.1.1.1, 8.8.8.8",
        ["HK"] = "1.1.1.1, 8.8.8.8",
        ["SG"] = "1.1.1.1, 8.8.8.8",
    };

    /// <summary>返回 --host-resolver-rules 参数值；无地区或关闭时返回空。</summary>
    public static string DnsArgs(Profile p, AppConfig cfg)
    {
        if (!cfg.perInstanceDns) return "";
        var region = ProxyPrecheck.InferDeclared(p.name, p.timezone, p.region);
        if (region.Length == 0 || !RegionDns.TryGetValue(region, out var dns)) return "";
        // MAP * ~NOTFOUND 会强制所有 DNS 走代理（远程解析），EXCLUDE localhost
        return $"--host-resolver-rules=\"MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1\" --dns-server={dns.Split(',')[0].Trim()}";
    }

    // ---- 26 检测站逐项跑分报告 ----
    public sealed class BenchmarkCheck
    {
        public string name { get; set; } = "";
        public string expected { get; set; } = "";
        public string actual { get; set; } = "";
        public bool pass { get; set; }
    }

    public sealed class BenchmarkReport
    {
        public bool ok { get; set; }
        public string timestamp { get; set; } = "";
        public string seed { get; set; } = "";
        public int totalScore { get; set; }
        public int passedCount { get; set; }
        public int failedCount { get; set; }
        public List<BenchmarkCheck> checks { get; set; } = new();
        public List<string> failedItems { get; set; } = new();
    }

    /// <summary>
    /// P2-13: 真实逐项跑分检测。启动临时无痕浏览器加载探针，通过 WebSocket CDP
    /// 提取真实采集值，对 Canvas、WebGL、UA、Screen、WebRTC 等做逐项判定并计算综合评分。
    /// </summary>
    public static (bool ok, BenchmarkReport report, string reportFile, string error) Benchmark(string chromeExe, string profileSeed = "914410")
    {
        var outDir = Path.Combine(Paths.Root, "_benchmark");
        Directory.CreateDirectory(outDir);
        var reportFile = Path.Combine(outDir, "report-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".json");
        string probe = "";
        string[] candidates = {
            Path.Combine(AppContext.BaseDirectory, "probe_full.html"),
            Path.Combine(Paths.Root, "probe_full.html"),
            Path.Combine(Environment.CurrentDirectory, "probe_full.html"),
            Path.Combine(Path.GetTempPath(), "probe_full.html"),
            @"C:\temp\probe_full.html"
        };
        foreach (var c in candidates)
        {
            if (File.Exists(c)) { probe = c; break; }
        }

        var rep = new BenchmarkReport {
            timestamp = DateTime.Now.ToString("o"),
            seed = profileSeed,
            totalScore = 100
        };

        if (string.IsNullOrEmpty(probe))
        {
            rep.ok = false;
            rep.totalScore = 0;
            return (false, rep, reportFile, "未找到探针页面文件 probe_full.html，请确保其位于应用目录或实例根目录！");
        }

        var port = 9800 + new Random().Next(100);
        var dataDir = Path.Combine(outDir, "data-" + port);
        System.Diagnostics.Process? proc = null;
        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo(chromeExe)
            { UseShellExecute = false, CreateNoWindow = true };
            psi.ArgumentList.Add("--user-data-dir=" + dataDir);
            psi.ArgumentList.Add("--remote-debugging-port=" + port);
            psi.ArgumentList.Add("--fingerprint=" + profileSeed);
            psi.ArgumentList.Add("--fingerprint-platform=windows");
            psi.ArgumentList.Add("--timezone=America/Los_Angeles");
            psi.ArgumentList.Add("--lang=en-US");
            psi.ArgumentList.Add("--accept-lang=en-US,en");
            psi.ArgumentList.Add("--fp-canvas=on");
            psi.ArgumentList.Add("--fp-webgl=intel");
            psi.ArgumentList.Add("--fp-audio=on");
            psi.ArgumentList.Add("--fp-dpr=2");
            psi.ArgumentList.Add("--fp-cores=8");
            psi.ArgumentList.Add("--fp-memory=16");
            psi.ArgumentList.Add("--enable-unsafe-webgpu");
            psi.ArgumentList.Add("--no-first-run");
            psi.ArgumentList.Add("--no-default-browser-check");
            psi.ArgumentList.Add("file:///" + probe.Replace('\\', '/'));

            GoogleOAuth.ApplyTo(psi);
            proc = System.Diagnostics.Process.Start(psi);
            System.Threading.Thread.Sleep(11000);

            // 连接 CDP 读取探针结果
            using var hc = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(8) };
            var listJson = hc.GetStringAsync($"http://127.0.0.1:{port}/json/list").GetAwaiter().GetResult();
            ProductFeatures.Log("benchmark.targets", listJson.Length > 300 ? listJson.Substring(0, 300) : listJson);
            using var listDoc = JsonDocument.Parse(listJson);
            string? wsUrl = null;
            var probeUrl = "file:///" + probe.Replace('\\', '/');
            foreach (var el in listDoc.RootElement.EnumerateArray())
            {
                if (el.TryGetProperty("type", out var ty) && ty.GetString() == "page")
                {
                    var targetUrl = el.TryGetProperty("url", out var uProp) ? uProp.GetString() ?? "" : "";
                    if (targetUrl.Contains("probe_full") && el.TryGetProperty("webSocketDebuggerUrl", out var wsProp))
                    {
                        wsUrl = wsProp.GetString();
                        if (!string.IsNullOrEmpty(wsUrl)) break;
                    }
                }
            }
            // 若没有以 probe_full 命名的目标，挑第一个页面并导航
            if (string.IsNullOrEmpty(wsUrl))
            {
                foreach (var el in listDoc.RootElement.EnumerateArray())
                {
                    if (el.TryGetProperty("type", out var ty) && ty.GetString() == "page" &&
                        el.TryGetProperty("webSocketDebuggerUrl", out var wsProp))
                    {
                        wsUrl = wsProp.GetString();
                        if (!string.IsNullOrEmpty(wsUrl)) break;
                    }
                }
            }
            if (string.IsNullOrEmpty(wsUrl))
                return (false, rep, reportFile, "未获取到 CDP 调试目标");

            using var ws = new System.Net.WebSockets.ClientWebSocket();
            using var cts = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(20));
            ws.ConnectAsync(new Uri(wsUrl), cts.Token).GetAwaiter().GetResult();

                        string rawStr = "";
            var respBuf = new byte[65536];
            var evalDeadline = DateTime.Now.AddSeconds(16);
            int evalId = 10;
            while (DateTime.Now < evalDeadline)
            {
                var expr = "document.getElementById('out') ? document.getElementById('out').textContent : ''";
                var reqBytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new {
                    id = ++evalId,
                    method = "Runtime.evaluate",
                    @params = new { expression = expr, returnByValue = true }
                }));
                ws.SendAsync(new ArraySegment<byte>(reqBytes), System.Net.WebSockets.WebSocketMessageType.Text, true, cts.Token).GetAwaiter().GetResult();

                // 每次请求只接收单条完整响应，避免多余的 ReceiveAsync 阻塞超时
                var respMs = new MemoryStream();
                while (true)
                {
                    var r = ws.ReceiveAsync(new ArraySegment<byte>(respBuf), cts.Token).GetAwaiter().GetResult();
                    respMs.Write(respBuf, 0, r.Count);
                    if (r.EndOfMessage) break;
                }
                var text = Encoding.UTF8.GetString(respMs.ToArray());
                ProductFeatures.Log("benchmark.raw", text.Length > 200 ? text.Substring(0, 200) : text);

                if (text.Contains("\"id\":" + evalId))
                {
                    try
                    {
                        using var d = JsonDocument.Parse(text);
                        if (d.RootElement.TryGetProperty("result", out var resProp) &&
                            resProp.TryGetProperty("result", out var res2Prop) &&
                            res2Prop.TryGetProperty("value", out var valProp))
                        {
                            var v = valProp.GetString() ?? "";
                            if (v.TrimStart().StartsWith("{"))
                            {
                                rawStr = v;
                                break;
                            }
                        }
                    }
                    catch { }
                }
                System.Threading.Thread.Sleep(500);
            }
if (string.IsNullOrEmpty(rawStr))
            {
                return (false, rep, reportFile, "未在规定时间内获取到有效探针输出 (页面可能尚未就绪)");
            }
using var dataDoc = JsonDocument.Parse(rawStr);
            var root = dataDoc.RootElement;

            void AddCheck(string name, string exp, string act, bool pass, int deductOnFail = 10)
            {
                rep.checks.Add(new BenchmarkCheck { name = name, expected = exp, actual = act, pass = pass });
                if (pass) rep.passedCount++;
                else {
                    rep.failedCount++;
                    rep.totalScore = Math.Max(0, rep.totalScore - deductOnFail);
                    rep.failedItems.Add($"{name} (期望: {exp}, 实际: {act})");
                }
            }

            // 逐项判定
            var ua = root.TryGetProperty("ua", out var u) ? u.GetString() ?? "" : "";
            AddCheck("用户代理 (UA)", "含 Windows NT 10.0", ua.Length > 40 ? ua.Substring(0, 40) + "…" : ua, ua.Contains("Windows NT 10.0"));

            var platform = root.TryGetProperty("platform", out var pl) ? pl.GetString() ?? "" : "";
            AddCheck("平台 (navigator.platform)", "Win32", platform, platform == "Win32");

            var tz = root.TryGetProperty("intl", out var inL) ? inL.GetString() ?? "" : "";
            AddCheck("语言时区 (Intl)", "en-US", tz, tz == "en-US");

            var dnt = root.TryGetProperty("dnt", out var dn) ? dn.GetString() ?? "" : "";
            AddCheck("DoNotTrack 跟踪保护", "1", dnt, dnt == "1", 5);

            if (root.TryGetProperty("canvas", out var cv))
            {
                var eq = cv.TryGetProperty("equal", out var eqVal) && eqVal.GetBoolean();
                var pure = cv.TryGetProperty("pureOk", out var pVal) && pVal.GetBoolean();
                AddCheck("Canvas 双出口一致性", "toDataURL == toBlob", eq ? "一致" : "不一致", eq, 15);
                AddCheck("Canvas 纯色真实性", "RGB无杂色扰动", pure ? "真实" : "异常", pure, 15);
            }
            else
            {
                AddCheck("Canvas 双出口一致性", "有效探针输出", "字段缺失", false, 15);
                AddCheck("Canvas 纯色真实性", "有效探针输出", "字段缺失", false, 15);
            }

            if (root.TryGetProperty("webgl", out var gl))
            {
                var v = gl.TryGetProperty("v", out var vVal) ? vVal.GetString() ?? "" : "";
                var r = gl.TryGetProperty("r", out var rVal) ? rVal.GetString() ?? "" : "";
                AddCheck("WebGL 规范标识", "WebKit / WebKit WebGL", $"{v} / {r}", v == "WebKit" && r == "WebKit WebGL", 15);
            }
            else
            {
                AddCheck("WebGL 规范标识", "有效探针输出", "字段缺失", false, 15);
            }

            if (root.TryGetProperty("webrtc", out var wr))
            {
                var priv = wr.TryGetProperty("priv", out var pCnt) ? pCnt.GetInt32() : -1;
                AddCheck("WebRTC 内网泄露防护", "0 私网候选地址", $"{priv} 个私网IP", priv == 0, 15);
            }
            else
            {
                AddCheck("WebRTC 内网泄露防护", "有效探针输出", "字段缺失", false, 15);
            }

            if (root.TryGetProperty("audio", out var au))
            {
                string h = "";
                if (au.ValueKind == JsonValueKind.String)
                {
                    h = au.GetString() ?? "";
                }
                else if (au.ValueKind == JsonValueKind.Object && au.TryGetProperty("hash", out var hVal))
                {
                    h = hVal.GetString() ?? "";
                }
                AddCheck("Audio 硬件音频指纹", "非空音频哈希", string.IsNullOrEmpty(h) ? "空" : h, !string.IsNullOrEmpty(h), 10);
            }
            else
            {
                AddCheck("Audio 硬件音频指纹", "有效探针输出", "字段缺失", false, 10);
            }

            rep.ok = rep.failedCount == 0;
            File.WriteAllText(reportFile, JsonSerializer.Serialize(rep, new JsonSerializerOptions { WriteIndented = true }));
            return (true, rep, reportFile, "");
        }
        catch (Exception e)
        {
            rep.ok = false;
            return (false, rep, reportFile, e.Message);
        }
        finally
        {
            try { proc?.Kill(); } catch { }
            try { if (Directory.Exists(dataDir)) Directory.Delete(dataDir, true); } catch { }
        }
    }

    // ---- 22 本地多配置集（不假称云同步）：列出/切换/打包 ----
    public static List<string> ConfigSets()
    {
        var dir = Path.Combine(Paths.Root, "_exports");
        if (!Directory.Exists(dir)) return new List<string>();
        return Directory.GetFiles(dir, "*.json")
            .OrderByDescending(File.GetLastWriteTime)
            .Select(Path.GetFileName).Where(x => x is not null).ToList()!;
    }

    /// <summary>把一个配置集包还原为当前实例集（团队共享配置包导入）。</summary>
    public static (int count, List<string> errors) LoadConfigSet(string name, bool overwrite = false)
    {
        // P0-3: 配置集名必须是纯文件名，禁止 ../ 与分隔符
        if (PathGuard.ValidateName(name) is not null)
            return (0, new List<string> { "非法配置集名" });
        var f = Path.Combine(Paths.Root, "_exports", Path.GetFileName(name));
        if (!PathGuard.IsUnder(Paths.Root, f) || !File.Exists(f))
            return (0, new List<string> { "配置集不存在或越界" });
        return ProductFeatures.ImportBundle(f, overwrite);
    }

    // ---- 23 屏幕录制基础：headless 截图（不引第三方 WS 库）----
    /// <summary>用 Chrome headless 对指定 URL 截一张图，返回 PNG 路径。</summary>
    public static (bool ok, string path, string error) Screenshot(
        string chromeExe, string url, string? tag = null)
    {
        var outDir = Path.Combine(Paths.Root, "_captures");
        Directory.CreateDirectory(outDir);
        var file = Path.Combine(outDir,
            "cap-" + (tag ?? DateTime.Now.ToString("yyyyMMdd-HHmmss")) + ".png");
        try
        {
            var dataDir = Path.Combine(outDir, "data-" + Guid.NewGuid().ToString("N").Substring(0, 6));
            var psi = new System.Diagnostics.ProcessStartInfo(chromeExe)
            { UseShellExecute = false, CreateNoWindow = true,
              RedirectStandardError = true, RedirectStandardOutput = true };
            psi.ArgumentList.Add("--headless=new");
            psi.ArgumentList.Add("--disable-gpu");
            psi.ArgumentList.Add("--no-sandbox");
            psi.ArgumentList.Add("--hide-scrollbars");
            psi.ArgumentList.Add("--user-data-dir=" + dataDir);
            psi.ArgumentList.Add("--window-size=1280,720");
            psi.ArgumentList.Add("--screenshot=" + file);
            psi.ArgumentList.Add(url);
            using var p = System.Diagnostics.Process.Start(psi);
            if (p is null) return (false, file, "无法启动 chrome");
            // 先等进程真正退出（headless chrome 会异步写 PNG，
            // 过早 ReadToEnd 返回时文件可能尚未落盘）
            p.WaitForExit(30000);
            string stderr = "";
            try { stderr = p.StandardError.ReadToEnd(); } catch { }
            bool ok = false;
            for (int i = 0; i < 20 && !ok; i++)
            {
                ok = File.Exists(file) && new FileInfo(file).Length > 1000;
                if (!ok) System.Threading.Thread.Sleep(150);
            }
            if (!ok) ProductFeatures.Log("capture.stderr",
                stderr.Substring(0, Math.Min(300, stderr.Length)));
            ProductFeatures.Log("capture", url + " -> " + (ok ? "ok" : "fail"));
            return (ok, file, ok ? "" : "截图未生成");
        }
        catch (Exception e) { return (false, file, e.Message); }
    }

}
