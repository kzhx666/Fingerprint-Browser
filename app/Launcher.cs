using System.Diagnostics;
using System.IO;

namespace KZManager;

/// <summary>
/// 实例启动流程。管理器的「启动」按钮和命令行 --launch 都走这里，
/// 保证两边行为完全一致。
/// </summary>
public static class Launcher
{
    public sealed class Result
    {
        public bool Ok;
        public string Message = "";
        public string Name = "";
        public int NodePort;
        public bool GoogleInjected;
        public int Pid;

        public static Result Fail(string name, string msg) => new() { Ok = false, Name = name, Message = msg };
        public static Result Done(string name) => new() { Ok = true, Name = name };
    }

    /// <summary>
    /// 启动一个实例。
    /// progress 用于把阶段性状态回传给 UI（命令行模式下传 null）。
    /// </summary>
    public static Result Run(string name, AppConfig cfg, Action<string>? progress = null)
    {
        void Say(string s) => progress?.Invoke(s);

        if (string.IsNullOrWhiteSpace(name))
            return Result.Fail(name, "实例名为空");

        if (!File.Exists(cfg.chromeExe))
            return Result.Fail(name, $"找不到 chrome.exe：\n{cfg.chromeExe}");

        if (!File.Exists(Paths.ProfileJson(name)))
            return Result.Fail(name, $"找不到实例：{name}\n{Paths.ProfileJson(name)}");

        var p = Store.LoadProfile(name);
        var r = Result.Done(name);
        Directory.CreateDirectory(Paths.DataDir(name));

        // 把上次的「崩溃」标记清掉，否则即使加了 --hide-crash-restore-bubble，
        // 某些版本仍会尝试恢复上次的标签页。
        ResetExitType(name);

        // ---- 0. 代理格式校验 ----
        // 之前发现 4 个实例的 proxy 是 "socks5://u:p:" 这种残链，
        // Chromium 会报 ERR_NO_SUPPORTED_PROXIES 且可能裸奔。提前拦住。
        if (!string.IsNullOrEmpty(p.proxy) &&
            !Fingerprint.ValidProxy(p.proxy, out var proxyErr))
        {
            return Result.Fail(name,
                $"代理配置不合法，已阻止启动：{proxyErr}\n\n" +
                $"当前值：{p.proxy}\n\n" +
                "正确格式示例：\n" +
                "  socks5://host:port\n" +
                "  socks5://user:pass@host:port\n" +
                "  http://host:port");
        }

        // ---- 1. 节点：库里存的是名字就解析成链接 ----
        if (!string.IsNullOrEmpty(p.node) && !p.node.Contains("://"))
        {
            var (c, so, _) = Py.Run(Paths.NodeProxy, "resolve", "--spec", p.node);
            var link = so.Trim();
            if (c != 0 || !link.Contains("://"))
                return Result.Fail(name, $"节点「{p.node}」在节点库里找不到，无法解析。\n\n请重新选一个节点。");
            p.node = link;
        }

        // ---- 2. WebRTC pref ----
        var policy = Fingerprint.WebRtcPolicy(p) switch
        {
            "strict" => "disable_non_proxied_udp",
            "public" => "default_public_interface_only",
            "off" => "default",
            _ => ""
        };
        if (policy.Length > 0 && File.Exists(Paths.SetPref))
        {
            Directory.CreateDirectory(Paths.ProfileDir(name));
            Py.Run(Paths.SetPref, "--profile", Paths.ProfileDir(name),
                   "--set", $"webrtc.ip_handling_policy={policy}");
        }

        // ---- 3. 节点代理 ----
        // Chromium 的 --proxy-server 不支持 URI 里嵌 socks5 账号密码
        // (ERR_NO_SUPPORTED_PROXIES)。带认证时先起本地转发器，改成本地 http://。
        if (ProxyRelay.NeedsRelay(p.proxy))
        {
            var relayed = ProxyRelay.Ensure(p.proxy, name, Say);
            if (relayed is null)
                return Result.Fail(name, "带账号密码的 socks5 转发失败，无法启动实例。");
            p.proxy = relayed;
        }

        var args = Fingerprint.BuildArgs(name, p, cfg.chromeExe);
        if (!string.IsNullOrEmpty(p.node))
        {
            var port = 10800 + Math.Abs(name.GetHashCode() % 100);
            r.NodePort = port;
            Say($"启动节点代理（端口 {port}）…");
            var (c, so, se) = Py.Run(Paths.NodeProxy, "start",
                                     "--node", p.node, "--port", port.ToString(),
                                     "--workdir", Paths.InstanceDir(name));
            if (c != 0)
                return Result.Fail(name, $"节点代理启动失败：\n\n{so}\n{se}");

            args = System.Text.RegularExpressions.Regex.Replace(
                args, @"--proxy-server=\S+", "");
            args += $" --proxy-server=socks5://127.0.0.1:{port}";
        }

        // ---- 3.5 出口 IP 预检 ----
        // 必须在 node 覆盖 --proxy-server 之后、真正启动之前，
        // 用最终要传给浏览器的同一代理去探测，失败就拦住裸奔。
        if (cfg.proxyPrecheck)
        {
            var pre = ProxyPrecheck.Check(args, Say);
            if (!pre.Ok)
            {
                return Result.Fail(name,
                    $"出口 IP 预检失败，已阻止启动（避免代理挂了浏览器直连裸奔）。\n\n" +
                    $"代理: {pre.ProxyUsed}\n错误: {pre.Error}\n\n" +
                    "请检查代理是否可用，或到「更多 → 关闭出口预检」临时跳过。");
            }
            if (!pre.Error.StartsWith("未配置"))
                Say($"出口 IP: {pre.Ip}  {pre.Country}/{pre.Region}/{pre.City}  {pre.Org}");

            // ---- 出口 IP 与实例声明地区比对 ----
            if (cfg.regionCheck)
            {
                ProxyPrecheck.CompareRegion(pre, name, p.timezone, p.region);
                if (pre.RegionVerdict == "mismatch")
                {
                    var msg = $"⚠ {pre.RegionNote}\n\n" +
                              $"出口 IP: {pre.Ip}  代理: {pre.ProxyUsed}";
                    if (cfg.regionCheckBlock)
                        return Result.Fail(name, "地区比对未通过，已阻止启动。\n\n" + msg);
                    Say(msg);   // 默认只警告
                }
                else if (pre.RegionVerdict == "match")
                {
                    Say($"✓ {pre.RegionNote}");
                }
            }
            ProxyPrecheck.Save(name, pre);
        }

        // ---- 4. 启动 ----
        try
        {
            var psi = new ProcessStartInfo(cfg.chromeExe, args)
            {
                UseShellExecute = false,
                WorkingDirectory = Path.GetDirectoryName(cfg.chromeExe) ?? ""
            };
            r.GoogleInjected = GoogleOAuth.ApplyTo(psi);

            var proc = Process.Start(psi);
            r.Pid = proc?.Id ?? 0;
            r.Ok = true;

            // 窗口是异步创建的（通常 1~3 秒），轮询等它出现，写入独立 AUMID。
            // 必须同步等：--launch 模式下调用方紧接着就 Shutdown()，
            // 如果丢到 Task.Run 里，进程会在打 AUMID 之前就退出。
            for (int i = 0; i < 16; i++)
            {
                System.Threading.Thread.Sleep(400);
                if (WindowAumid.ApplyOnce(name) > 0)
                {
                    WindowIcon.ApplyOnce(name);
                    break;
                }
            }

            return r;
        }
        catch (Exception ex)
        {
            return Result.Fail(name, $"启动失败：{ex.Message}");
        }
    }

    /// <summary>
    /// 把 Preferences 里的 profile.exit_type 从 Crashed 改回 Normal。
    /// 浏览器正在运行时写这个键不安全（退出时会被覆盖），所以只在能拿到
    /// 独占访问时写，失败就忽略——还有 --hide-crash-restore-bubble 兜底。
    /// </summary>
    static void ResetExitType(string name)
    {
        var pref = Path.Combine(Paths.ProfileDir(name), "Preferences");
        if (!File.Exists(pref)) return;
        try
        {
            // 用独占打开探测浏览器是否在跑
            using (new FileStream(pref, FileMode.Open, FileAccess.ReadWrite, FileShare.None))
            {
            }
        }
        catch
        {
            return;     // 被占用，说明浏览器正在运行，跳过
        }

        try
        {
            var text = File.ReadAllText(pref, System.Text.Encoding.UTF8).TrimStart('\uFEFF');
            using var doc = System.Text.Json.JsonDocument.Parse(text);
            if (!doc.RootElement.TryGetProperty("profile", out var prof)) return;
            if (!prof.TryGetProperty("exit_type", out var et)) return;
            if (et.GetString() == "Normal") return;

            // 简单文本替换，避免整体重写破坏 Chromium 的 pref 结构
            // 逐字字符串：正则里的 \s 和引号都不用再转义
            var fixedText = System.Text.RegularExpressions.Regex.Replace(
                text,
                @"""exit_type""\s*:\s*""(Crashed|SessionEnded)""",
                @"""exit_type"":""Normal""");
            if (fixedText != text)
                File.WriteAllText(pref, fixedText, new System.Text.UTF8Encoding(false));
        }
        catch { }
    }

    /// <summary>生成给任务栏快捷方式用的命令行（指向本 EXE 的 --launch 模式）。</summary>
    public static string ShortcutArgs(string instanceName)
        => $"--launch \"{instanceName}\"";
}
