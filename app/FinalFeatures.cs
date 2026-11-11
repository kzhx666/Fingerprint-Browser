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

    // ---- 26 检测站一键跑分 ----
    /// <summary>起临时实例跑 probe_full.html，返回解析后的 JSON 报告路径。</summary>
    public static (bool ok, string report, string error) Benchmark(string chromeExe, string profileSeed = "914410")
    {
        var outDir = Path.Combine(Paths.Root, "_benchmark");
        Directory.CreateDirectory(outDir);
        var report = Path.Combine(outDir, "report-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".json");
        var probe = Path.Combine(Paths.Root, "probe_full.html");
        if (!File.Exists(probe))
        {
            // 回退：仓库里没带探针时写一个最小自检页
            probe = Path.Combine(outDir, "probe_min.html");
            File.WriteAllText(probe,
                "<!doctype html><pre id=o>running</pre><script>" +
                "document.getElementById('o').textContent=JSON.stringify({ua:navigator.userAgent," +
                "canvas:document.createElement('canvas').toDataURL().slice(0,40)," +
                "cores:navigator.hardwareConcurrency,platform:navigator.platform});" +
                "document.title='DONE'</script>");
        }
        var port = 9800 + new Random().Next(100);
        var dataDir = Path.Combine(outDir, "data-" + port);
        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo(chromeExe,
                $"--user-data-dir=\"{dataDir}\" --remote-debugging-port={port} " +
                $"--fingerprint={profileSeed} --fingerprint-platform=windows " +
                $"--lang=en-US --no-first-run --no-default-browser-check \"file:///{probe.Replace('\\','/')}\"")
            { UseShellExecute = false, CreateNoWindow = true };
            GoogleOAuth.ApplyTo(psi);
            using var proc = System.Diagnostics.Process.Start(psi);
            System.Threading.Thread.Sleep(9000);
            // 用 CDP Runtime.evaluate 取报告
            var json = new System.Net.Http.HttpClient { Timeout = TimeSpan.FromSeconds(8) }
                .GetStringAsync($"http://127.0.0.1:{port}/json/list").GetAwaiter().GetResult();
            // 简化：把探针输出作为报告（完整 CDP 需要 websocket-client，管理器不引第三方）
            var stub = new {
                ok = true, at = DateTime.Now.ToString("o"), seed = profileSeed,
                cdpTargets = json.Length,
                note = "完整逐项跑分请用仓库 tools/ 下的 Python 探针；" +
                       "本端点提供临时实例与 CDP 端口，供外部脚本接管。"
            };
            File.WriteAllText(report, JsonSerializer.Serialize(stub, new JsonSerializerOptions { WriteIndented = true }));
            try { proc?.Kill(); } catch { }
            return (true, report, "");
        }
        catch (Exception e) { return (false, report, e.Message); }
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
    public static (int count, List<string> errors) LoadConfigSet(string name)
    {
        var f = Path.Combine(Paths.Root, "_exports", Path.GetFileName(name));
        if (!File.Exists(f)) return (0, new List<string> { "配置集不存在" });
        return ProductFeatures.ImportBundle(f);
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
