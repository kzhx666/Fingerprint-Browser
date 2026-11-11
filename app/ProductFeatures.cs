using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 批次③ 产品层 16/17/19/20/21/22/23 的共用实现。
/// 全部纯管理层，不编译内核。
/// </summary>
public static class ProductFeatures
{
    static string Root => Paths.Root;

    // ---- 17 批量导入/导出 ----
    public sealed class Bundle
    {
        [System.Text.Json.Serialization.JsonInclude]
        public string version { get; set; } = "1.0";
        [System.Text.Json.Serialization.JsonInclude]
        public string exportedAt { get; set; } = "";
        [System.Text.Json.Serialization.JsonInclude]
        public List<BundleItem> instances { get; set; } = new();
    }
    public sealed class BundleItem
    {
        [System.Text.Json.Serialization.JsonInclude]
        public string name { get; set; } = "";
        [System.Text.Json.Serialization.JsonInclude]
        public Dictionary<string, JsonElement> profile { get; set; } = new();
    }

    public static string ExportBundle(IEnumerable<string> names)
    {
        var b = new Bundle { exportedAt = DateTime.Now.ToString("o") };
        foreach (var n in names)
        {
            if (PathGuard.ValidateName(n) is not null) continue;  // P0-3 跳过非法名
            var p = Path.Combine(Root, n, "profile.json");
            if (!File.Exists(p)) continue;
            var doc = JsonDocument.Parse(File.ReadAllText(p));
            var d = new Dictionary<string, JsonElement>();
            foreach (var prop in doc.RootElement.EnumerateObject())
                d[prop.Name] = prop.Value.Clone();
            b.instances.Add(new BundleItem { name = n, profile = d });
        }
        var outDir = Path.Combine(Root, "_exports");
        Directory.CreateDirectory(outDir);
        var file = Path.Combine(outDir, "instances-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".json");
        File.WriteAllText(file, JsonSerializer.Serialize(b, new JsonSerializerOptions { WriteIndented = true }));
        return file;
    }

    /// <summary>P0-4: 导入配置。overwrite=false 时同名实例跳过并明确报出；
    /// 写入用临时文件 + File.Move 原子替换，中断不损坏原文件；
    /// 所有目标路径必须通过 PathGuard 校验，只能落在 Root 下。</summary>
    public static (int ok, List<string> errors) ImportBundle(string file, bool overwrite = false)
    {
        var errors = new List<string>();
        int n = 0;
        try
        {
            // 源文件也必须是普通文件（不跟随符号链接穿越）
            var fullSrc = Path.GetFullPath(file);
            if (!File.Exists(fullSrc)) { errors.Add("源文件不存在"); return (0, errors); }
            var b = JsonSerializer.Deserialize<Bundle>(File.ReadAllText(fullSrc));
            if (b is null) { errors.Add("无法解析配置包"); return (0, errors); }
            foreach (var it in b.instances)
            {
                try
                {
                    if (!PathGuard.TryInstanceDir(it.name ?? "", out var dir, out var ne))
                    { errors.Add((it.name ?? "?") + ": 非法名称 " + ne); continue; }
                    var target = Path.Combine(dir, "profile.json");
                    if (!PathGuard.IsUnder(Root, target))
                    { errors.Add(it.name + ": 越界路径"); continue; }
                    if (File.Exists(target) && !overwrite)
                    { errors.Add(it.name + ": 已存在，未覆盖（用 overwrite=true 明确覆盖）"); continue; }
                    Directory.CreateDirectory(dir);
                    var tmp = target + ".tmp-" + Guid.NewGuid().ToString("N").Substring(0, 8);
                    try
                    {
                        File.WriteAllText(tmp,
                            JsonSerializer.Serialize(it.profile, new JsonSerializerOptions { WriteIndented = true }));
                        // 原子替换
                        if (File.Exists(target)) File.Replace(tmp, target, null);
                        else File.Move(tmp, target);
                        n++;
                    }
                    finally { try { if (File.Exists(tmp)) File.Delete(tmp); } catch { } }
                }
                catch (Exception e) { errors.Add(it.name + ": " + e.Message); }
            }
        }
        catch (Exception e) { errors.Add(e.Message); }
        return (n, errors);
    }

    // ---- 21 日志审计 + Webhook ----
    public static void Log(string action, string detail)
    {
        try
        {
            var dir = Path.Combine(Root, "_audit");
            Directory.CreateDirectory(dir);
            var line = JsonSerializer.Serialize(new {
                at = DateTime.Now.ToString("o"), action, detail });
            File.AppendAllText(Path.Combine(dir,
                "audit-" + DateTime.Now.ToString("yyyyMMdd") + ".jsonl"), line + "\n");
            var hook = Store.LoadConfig().webhookUrl;
            if (!string.IsNullOrEmpty(hook)) _ = PostWebhook(hook, line);
        }
        catch { }
    }

    static async Task PostWebhook(string url, string payload)
    {
        try
        {
            using var hc = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            await hc.PostAsync(url, new StringContent(payload, Encoding.UTF8, "application/json"));
        }
        catch { }
    }

    // ---- 16 代理自动轮换（默认关，由 AppConfig.proxyRotation 控制）----
    /// <summary>从节点库取下一个可用节点写回实例的 proxy。返回新节点串。</summary>
    public static string? RotateProxy(string instanceName)
    {
        // 16: 默认关闭，避免误改真实实例配置
        if (!Store.LoadConfig().proxyRotation) return null;
        try
        {
            var nodesFile = Path.Combine(Root, "nodes.json");
            if (!File.Exists(nodesFile)) return null;
            var doc = JsonDocument.Parse(File.ReadAllText(nodesFile));
            var list = new List<string>();
            if (doc.RootElement.ValueKind == JsonValueKind.Array)
                foreach (var e in doc.RootElement.EnumerateArray())
                    if (e.ValueKind == JsonValueKind.String) list.Add(e.GetString() ?? "");
                    else if (e.TryGetProperty("link", out var l)) list.Add(l.GetString() ?? "");
            if (list.Count == 0) return null;
            var p = Path.Combine(Root, instanceName, "profile.json");
            if (!File.Exists(p)) return null;
            var j = JsonDocument.Parse(File.ReadAllText(p));
            var d = new Dictionary<string, JsonElement>();
            foreach (var pr in j.RootElement.EnumerateObject()) d[pr.Name] = pr.Value.Clone();
            var cur = d.TryGetValue("proxy", out var c) ? c.GetString() ?? "" : "";
            int idx = list.IndexOf(cur);
            var next = list[(idx + 1) % list.Count];
            d["proxy"] = JsonSerializer.SerializeToElement(next);
            File.WriteAllText(p, JsonSerializer.Serialize(d, new JsonSerializerOptions { WriteIndented = true }));
            Log("proxy.rotate", instanceName + " -> " + next.Substring(0, Math.Min(30, next.Length)));
            return next;
        }
        catch { return null; }
    }

    // ---- 19/20 版本/移动端 UA 画像 ----
    public static readonly Dictionary<string, string> UaProfiles = new()
    {
        ["default"] = "",
        ["chrome-157"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36",
        ["chrome-156"] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36",
        ["iOS-safari"] = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
        ["Android-chrome"] = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/158.0.0.0 Mobile Safari/537.36",
    };

    // ---- 18 Cookie 预热 / 23 RPA 基础：种子站点清单 ----
    public static readonly string[] WarmupSites = {
        "https://www.google.com", "https://www.youtube.com",
        "https://www.cloudflare.com", "https://www.wikipedia.org"
    };

    public sealed class WarmupResult
    {
        public bool ok { get; set; }
        public int visited { get; set; }
        public List<string> sites { get; set; } = new();
        public string error { get; set; } = "";
    }

    /// <summary>
    /// P2-12: 真实 Cookie 预热。通过 WebSocket CDP 连接已运行实例的目标页面，
    /// 依次发送 Page.navigate 真实访问各种子站点，让浏览器真正加载并沉淀 Cookie。
    /// </summary>
    public static async Task<WarmupResult> Warmup(int cdpPort, IEnumerable<string>? sites = null)
    {
        var res = new WarmupResult();
        var targetSites = (sites ?? WarmupSites).ToList();
        try
        {
            using var hc = new HttpClient { Timeout = TimeSpan.FromSeconds(8) };
            var json = await hc.GetStringAsync($"http://127.0.0.1:{cdpPort}/json/list");
            using var doc = JsonDocument.Parse(json);
            string? wsUrl = null;
            foreach (var t in doc.RootElement.EnumerateArray())
            {
                if (t.TryGetProperty("type", out var ty) && ty.GetString() == "page" &&
                    t.TryGetProperty("webSocketDebuggerUrl", out var wsProp))
                {
                    wsUrl = wsProp.GetString();
                    if (!string.IsNullOrEmpty(wsUrl)) break;
                }
            }
            if (string.IsNullOrEmpty(wsUrl))
            {
                res.error = "未找到可连接的 CDP 页面，请确保实例已启动远程调试";
                return res;
            }

            using var ws = new System.Net.WebSockets.ClientWebSocket();
            using var cts = new System.Threading.CancellationTokenSource(TimeSpan.FromSeconds(45));
            await ws.ConnectAsync(new Uri(wsUrl), cts.Token);

            int id = 1;
            foreach (var site in targetSites)
            {
                var payload = JsonSerializer.Serialize(new {
                    id = id++,
                    method = "Page.navigate",
                    @params = new { url = site }
                });
                var bytes = Encoding.UTF8.GetBytes(payload);
                await ws.SendAsync(new ArraySegment<byte>(bytes),
                                   System.Net.WebSockets.WebSocketMessageType.Text,
                                   true, cts.Token);
                res.sites.Add(site);
                res.visited++;
                Log("warmup.visited", site);
                await Task.Delay(2000, cts.Token);
            }
            await ws.CloseAsync(System.Net.WebSockets.WebSocketCloseStatus.NormalClosure, "done", System.Threading.CancellationToken.None);
            res.ok = true;
            return res;
        }
        catch (Exception e)
        {
            res.error = "Cookie 预热失败: " + (e.InnerException?.Message ?? e.Message);
            return res;
        }
    }
}
