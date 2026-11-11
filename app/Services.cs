using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Unicode;

namespace KZManager;

public static class Paths
{
    /// <summary>程序所在目录。EXE 放在 KZProfiles 根目录时，这就是根。</summary>
    public static string Root { get; private set; } =
        AppContext.BaseDirectory.TrimEnd('\\');

    public static void SetRoot(string root) => Root = root.TrimEnd('\\');

    public static string ConfigFile => Path.Combine(Root, "manager.json");
    public static string NodesFile => Path.Combine(Root, "nodes.json");
    public static string NodeProxy => Path.Combine(Root, "node-proxy.py");
    public static string SnapshotPy => Path.Combine(Root, "snapshot.py");
    public static string BackupPy => Path.Combine(Root, "backup.py");
    public static string SyncPy => Path.Combine(Root, "sync-data.py");
    public static string SetPref => Path.Combine(Root, "set-pref.py");
    public static string SelfTestHtml => Path.Combine(Root, "指纹自检.html");
    public static string BackupDir => Path.Combine(Root, "_backup");
    public static string SnapDir => Path.Combine(Root, "_snapshots");

    public static string InstanceDir(string name) => Path.Combine(Root, name);
    public static string DataDir(string name) => Path.Combine(InstanceDir(name), "Data");
    public static string IconsDir => Path.Combine(Root, "icons");
    public static string IconPath(string id)
    {
        if (string.IsNullOrWhiteSpace(id)) return "";
        // 只允许 icon-NN，防路径穿越
        if (!System.Text.RegularExpressions.Regex.IsMatch(id, @"^icon-\d{2}$")) return "";
        var p = Path.Combine(IconsDir, id + ".ico");
        return File.Exists(p) ? p : "";
    }
    public static string IconPng(string id)
    {
        if (string.IsNullOrWhiteSpace(id)) return "";
        if (!System.Text.RegularExpressions.Regex.IsMatch(id, @"^icon-\d{2}$")) return "";
        var p = Path.Combine(IconsDir, id + ".png");
        return File.Exists(p) ? p : "";
    }
    public static string ProfileDir(string name) => Path.Combine(DataDir(name), "Default");
    public static string ProfileJson(string name) => Path.Combine(InstanceDir(name), "profile.json");
}

public static class Json
{
    public static readonly JsonSerializerOptions Pretty = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.Create(UnicodeRanges.All),
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    };

    public static readonly JsonSerializerOptions Loose = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true
    };

    public static T? Read<T>(string path) where T : class
    {
        try
        {
            if (!File.Exists(path)) return null;
            var text = File.ReadAllText(path, Encoding.UTF8).TrimStart('\uFEFF');
            return JsonSerializer.Deserialize<T>(text, Loose);
        }
        catch { return null; }
    }

    public static void Write<T>(string path, T value)
        => File.WriteAllText(path, JsonSerializer.Serialize(value, Pretty),
                              new UTF8Encoding(false));
}

public static class Store
{
    public static AppConfig LoadConfig()
        => Json.Read<AppConfig>(Paths.ConfigFile) ?? new AppConfig();

    public static void SaveConfig(AppConfig cfg) => Json.Write(Paths.ConfigFile, cfg);

    public static Profile LoadProfile(string name)
        => Json.Read<Profile>(Paths.ProfileJson(name)) ?? new Profile { name = name };

    public static void SaveProfile(string name, Profile p)
    {
        var v = PathGuard.ValidateName(name);
        if (v != null) throw new ArgumentException("实例名不合法: " + v);
        var dir = Paths.InstanceDir(name);
        if (!PathGuard.IsUnder(Paths.Root, dir)) throw new ArgumentException("越界路径");
        p.name = name;
        Directory.CreateDirectory(dir);
        Json.Write(Paths.ProfileJson(name), p);
    }

    public static List<string> ListInstances()
    {
        var list = new List<string>();
        if (!Directory.Exists(Paths.Root)) return list;
        foreach (var d in Directory.GetDirectories(Paths.Root))
        {
            if (File.Exists(Path.Combine(d, "profile.json")))
                list.Add(Path.GetFileName(d));
        }
        list.Sort(StringComparer.OrdinalIgnoreCase);
        return list;
    }

    /// <summary>按用户自定义顺序返回；不在顺序表里的追加到末尾（字母序）。</summary>
    public static List<string> ListInstancesOrdered(AppConfig cfg)
    {
        var all = ListInstances();
        if (cfg.order is null || cfg.order.Count == 0) return all;

        var known = new HashSet<string>(all, StringComparer.OrdinalIgnoreCase);
        var result = new List<string>();
        foreach (var n in cfg.order)
            if (known.Contains(n)) result.Add(n);

        foreach (var n in all)
            if (!result.Contains(n, StringComparer.OrdinalIgnoreCase)) result.Add(n);
        return result;
    }

    /// <summary>把当前顺序写回 manager.json。</summary>
    public static void SaveOrder(AppConfig cfg, IEnumerable<string> names)
    {
        cfg.order = names.ToList();
        SaveConfig(cfg);
    }

    /// <summary>重命名实例：改目录、order、备份/快照子目录、快捷方式参数。</summary>
    public static bool RenameInstance(string oldName, string newName, out string error)
    {
        error = "";
        if (string.IsNullOrWhiteSpace(newName)) { error = "新名称为空"; return false; }
        if (oldName == newName) return true;
        var vOld = PathGuard.ValidateName(oldName);
        if (vOld != null) { error = "原实例名称不合法: " + vOld; return false; }
        var vNew = PathGuard.ValidateName(newName);
        if (vNew != null) { error = "新实例名称不合法: " + vNew; return false; }
        var oldDir = Paths.InstanceDir(oldName);
        var newDir = Paths.InstanceDir(newName);
        if (!PathGuard.IsUnder(Paths.Root, oldDir) || !PathGuard.IsUnder(Paths.Root, newDir))
        { error = "越界路径"; return false; }
        if (!Directory.Exists(oldDir)) { error = "原实例目录不存在"; return false; }
        if (Directory.Exists(newDir)) { error = "已存在同名实例：" + newName; return false; }

        // 关掉该实例，避免文件被占用 / 内存态覆盖
        try { ProcessQuery.KillChromeByDataDir(Paths.DataDir(oldName)); }
        catch { }

        try
        {
            Directory.Move(oldDir, newDir);
        }
        catch (Exception e) { error = "重命名目录失败：" + e.Message; return false; }

        // profile.json 里的 name 字段同步（SaveProfile 也会做，这里先写防漏）
        try
        {
            var pj = Paths.ProfileJson(newName);
            if (File.Exists(pj))
            {
                var p = Json.Read<Profile>(pj);
                if (p is not null) { p.name = newName; Json.Write(pj, p); }
            }
        }
        catch { }

        // manager.json 的 order
        try
        {
            var cfg = LoadConfig();
            if (cfg.order is not null)
            {
                for (int i = 0; i < cfg.order.Count; i++)
                    if (string.Equals(cfg.order[i], oldName, StringComparison.OrdinalIgnoreCase))
                        cfg.order[i] = newName;
                SaveConfig(cfg);
            }
        }
        catch { }

        // 备份 / 快照目录里的实例子目录
        foreach (var root in new[] { Paths.BackupDir, Paths.SnapDir })
        {
            try
            {
                if (!Directory.Exists(root)) continue;
                foreach (var batch in Directory.GetDirectories(root))
                {
                    var src = Path.Combine(batch, oldName);
                    var dst = Path.Combine(batch, newName);
                    if (Directory.Exists(src) && !Directory.Exists(dst))
                        try { Directory.Move(src, dst); } catch { }
                }
            }
            catch { }
        }

        // 所有 .lnk：改 --launch "旧名" 和 --user-data-dir 里的旧路径
        try { Shortcut.RenameInAllShortcuts(oldName, newName); }
        catch { }

        return true;
    }

    public static List<NodeEntry> LoadNodes()
        => Json.Read<List<NodeEntry>>(Paths.NodesFile) ?? new List<NodeEntry>();
}

public static class Py
{
    /// <summary>跑 python 脚本，返回 (退出码, 标准输出, 标准错误)。</summary>
    public static (int code, string stdout, string stderr) Run(string script, params string[] args)
    {
        var psi = new ProcessStartInfo("python")
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUTF8"] = "1";
        psi.ArgumentList.Add(script);
        foreach (var a in args) psi.ArgumentList.Add(a);

        try
        {
            using var p = Process.Start(psi)!;
            var so = p.StandardOutput.ReadToEnd();
            var se = p.StandardError.ReadToEnd();
            p.WaitForExit(120_000);
            return (p.HasExited ? p.ExitCode : -1, so, se);
        }
        catch (Exception e)
        {
            return (-1, "", e.Message);
        }
    }
}

public static class Fingerprint
{
    static readonly Dictionary<string, string> PlatformMap = new()
    {
        ["macos"] = "MacIntel",
        ["windows"] = "Win32",
        ["linux"] = "Linux x86_64",
    };

    /// <summary>剥掉 ;q=0.9 这类权重，Chromium 的 --accept-lang 会自己重新生成。</summary>
    public static string PlainLang(string lang)
        => string.Join(",", lang.Split(',')
            .Select(x => x.Split(';')[0].Trim())
            .Where(x => x.Length > 0));

    /// <summary>代理 URI 校验。返回 false 时 err 说明原因。</summary>
    public static bool ValidProxy(string proxy, out string err)
    {
        err = "";
        if (string.IsNullOrWhiteSpace(proxy)) return true;
        if (!Uri.TryCreate(proxy, UriKind.Absolute, out var u))
        { err = "不是合法 URI"; return false; }
        var s = u.Scheme.ToLowerInvariant();
        if (s is not ("http" or "https" or "socks5" or "socks" or "socks4" or "quic"))
        { err = "不支持的 scheme: " + u.Scheme; return false; }
        if (string.IsNullOrEmpty(u.Host))
        { err = "缺少主机地址"; return false; }
        if (u.Port <= 0 || u.Port > 65535)
        { err = "端口无效"; return false; }
        // 带账号密码但没 @host 的残链（如 socks5://u:p:）
        if (proxy.Contains(":") && !proxy.Contains("@") && u.UserInfo.Length == 0
            && s.StartsWith("socks"))
        {
            var tail = proxy.Substring(proxy.IndexOf("//") + 2);
            if (tail.Count(c => c == ':') >= 2)
            { err = "疑似不完整的带认证链接（缺 @host:port）"; return false; }
        }
        return true;
    }

    public static string WebRtcPolicy(Profile p)
    {
        var w = (p.webrtc ?? "").Trim();
        if (w.Length > 0) return w;
        // 默认 strict：即使没绑节点（靠全局链路出国）也不暴露本地接口。
        // 之前直连时返回 public -> default_public_interface_only，
        // WebRTC 会露本机网卡信息，Google 可拿它辅助判地区。
        return "strict";
    }

    public static string BuildArgs(string name, Profile p, string chromeExe)
    {
        var a = new List<string>
        {
            $"--user-data-dir=\"{Paths.DataDir(name)}\""
        };
        // 所有要关的 feature 累积到这里，最后合并成一个
        // --disable-features=...，避免多份开关互相覆盖。
        var _disabledFeatures = new List<string>();
        if (!string.IsNullOrEmpty(p.name)) a.Add($"--profile-name=\"{p.name}\"");

        // 主控浏览器只用来共享数据，不做任何伪装
        bool isMaster = string.Equals(p.role, "master", StringComparison.OrdinalIgnoreCase);
        if (!isMaster)
        {
            if (System.Text.RegularExpressions.Regex.IsMatch(p.seed ?? "", @"^\d+$"))
                a.Add($"--fingerprint={p.seed}");
            if (!string.IsNullOrEmpty(p.platform) && p.platform != "host")
                a.Add($"--fingerprint-platform={p.platform}");
            if (!string.IsNullOrEmpty(p.timezone)) a.Add($"--timezone={p.timezone}");

            if (!string.IsNullOrEmpty(p.lang))
            {
                var plain = PlainLang(p.lang);
                if (plain.Length > 0)
                {
                    a.Add($"--accept-lang={plain}");
                    a.Add($"--fingerprint-lang={plain}");

                    // --lang 决定 Chromium 进程 UI 语言，也决定
                    // Intl.DateTimeFormat/NumberFormat/Collator 的
                    // resolvedOptions().locale。只改 navigator.languages 不改这里，
                    // 网站会读到 navigator.language=en-US 但 Intl locale=zh-CN，
                    // 判定「语言与系统语言不符」扣 10%。
                    var first = plain.Split(',')[0].Trim();
                    if (first.Length > 0) a.Add($"--lang={first}");
                }
            }
            if (!string.IsNullOrEmpty(p.proxy))
        {
            a.Add($"--proxy-server={p.proxy}");
            // ---- 网络隔离默认策略（对标商业指纹浏览器）----
            // (1) Chromium 的 SOCKS5 客户端天生把域名交给代理解析
            //     (net/socket/socks5_client_socket.h:38)，DNS 已在远程。
            //     但内置的 DoH/Secure DNS 客户端可能绕过代理直连 DNS 服务器，
            //     配了代理时一律关掉，堵死唯一的 DNS 泄漏通道。
            _disabledFeatures.Add("DnsOverHttps");
            _disabledFeatures.Add("AsyncDns");
            // (2) WebRTC：Preferences 已写 disable_non_proxied_udp(strict)，
            //     这里再禁本地 UDP 收集作双保险，防止 pref 被覆盖时露网卡。
            a.Add("--webrtc-ip-handling-policy=disable_non_proxied_udp");

            // (3) 全域国内通配分流 (Comprehensive China Direct Bypass):
            // 采用顶级通配规则彻底覆盖所有国内互联网体系 (B站全系/阿里系/腾讯系/百度系/字节系/京东/网易/美团/快手/政企银行及所有.cn域名)
            // 杜绝海外 SOCKS5 代理出向 ACL 规则拦截国内站点 (0x02)，同时保证国外业务平台 100% 走代理。
            var rules = new List<string>
            {
                "*.cn", "*.com.cn", "*.net.cn", "*.org.cn", "*.gov.cn", "*.edu.cn",
                // B站全系 (主站/游戏 biligame/直播/视频CDN/登录/API)
                "*bili*", "*hdslb*", "*acgvideo*", "*acg.tv*",
                // 阿里全系 (淘宝/天猫/支付宝/阿里云/1688/菜鸟/钉钉/高德地图)
                "*taobao*", "*tmall*", "*alipay*", "*alicdn*", "*aliyun*", "*alibaba*",
                "*1688*", "*cainiao*", "*ele.me*", "*fliggy*", "*dingtalk*", "*amap*", "*autonavi*",
                // 腾讯全系 (QQ/微信/腾讯云/财付通/CDN)
                "*qq.com*", "*gtimg*", "*qpic*", "*qlogo*", "*tencent*", "*wechat*",
                "*weixin*", "*tenpay*", "*myqcloud*",
                // 百度全系 (搜索/贴吧/网盘/爱奇艺/地图)
                "*baidu*", "*bdstatic*", "*baidupcs*", "*hao123*", "*tieba*", "*iqiyi*",
                // 字节全系 (抖音/今日头条/西瓜视频/飞书/火山/API)
                "*douyin*", "*bytedance*", "*zijie*", "*toutiao*", "*feishu*", "*pstatp*", "*snssdk*", "*volces*",
                // 快手全系
                "*kuaishou*", "*yximgs*", "*gifshow*",
                // 京东全系
                "*jd.com*", "*360buy*", "*jdcloud*", "*jcloudcache*",
                // 网易全系 (邮箱/云音乐/游戏/有道)
                "*163.com*", "*126.net*", "*netease*", "*youdao*", "*ydstatic*",
                // 新浪 / 微博
                "*weibo*", "*sina*",
                // 知乎
                "*zhihu*", "*zhimg*",
                // 小红书
                "*xiaohongshu*", "*xhscdn*",
                // 美团 / 大众点评
                "*meituan*", "*dianping*",
                // 拼多多
                "*pinduoduo*", "*yangkeduo*",
                // 视频 / 媒体 / 门户
                "*youku*", "*sohu*", "*douyu*", "*huya*", "*mgtv*", "*cctv*", "*ifeng*", "*xinhuanet*", "*people.com*",
                // 开发者 / 技术社区
                "*csdn*", "*gitee*", "*cnblogs*", "*oschina*", "*juejin*", "*v2ex*", "*segmentfault*",
                // 民生 / 银行 / 通信运营商
                "*12306*", "*chinamobile*", "*10010*", "*189.cn*", "*unionpay*", "*icbc*", "*ccb*", "*boc*", "*abchina*", "*cmbchina*",
                // 办公 / 协作
                "*wps*", "*kdocs*",
                // 本地与回环
                "<local>", "127.0.0.1", "localhost"
            };

            // 用户自定义扩展白名单 (D:\KZProfiles\cn_bypass_domains.txt)
            try
            {
                var customFile = Path.Combine(Paths.Root, "cn_bypass_domains.txt");
                if (File.Exists(customFile))
                {
                    foreach (var line in File.ReadAllLines(customFile))
                    {
                        var rule = line.Trim();
                        if (rule.Length > 0 && !rule.StartsWith("#") && !rules.Contains(rule))
                            rules.Add(rule);
                    }
                }
                else
                {
                    File.WriteAllText(customFile,
                        "# 自定义国内直连分流白名单 (每行一条规则，支持 * 通配符)\n" +
                        "# 示例:\n" +
                        "# *mycompany.com*\n" +
                        "# 10.0.0.0/8\n");
                }
            }
            catch { }

            var cnBypass = string.Join(";", rules);
            a.Add($"--proxy-bypass-list=\"{cnBypass}\"");

        }

        // 代理格式校验在 Launcher 里做（写成无效命令行开关 Chromium 会忽略，
        // 拦不住），这里只保留参数生成。

            if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_cores ?? "", @"^\d+$"))
                a.Add($"--fp-cores={p.fp_cores}");
            if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_memory ?? "", @"^\d+$"))
                a.Add($"--fp-memory={p.fp_memory}");
            if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_screen ?? "", @"^\d+x\d+$"))
                a.Add($"--fp-screen={p.fp_screen}");
            if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_dpr ?? "", @"^[\d.]+$"))
                a.Add($"--fp-dpr={p.fp_dpr}");
            if (!string.IsNullOrEmpty(p.fp_canvas)) a.Add($"--fp-canvas={p.fp_canvas}");
            if (!string.IsNullOrEmpty(p.fp_webgl)) a.Add($"--fp-webgl={p.fp_webgl}");
            if (!string.IsNullOrEmpty(p.fp_audio)) a.Add($"--fp-audio={p.fp_audio}");
            if (!string.IsNullOrEmpty(p.fp_fonts) && p.fp_fonts != "on")
                a.Add($"--fp-fonts={p.fp_fonts}");
            if (!string.IsNullOrEmpty(p.geo)) a.Add($"--fp-geo={p.geo}");
        }

        if (System.Text.RegularExpressions.Regex.IsMatch(p.rows ?? "", @"^\d+$"))
            a.Add($"--bookmark-bar-rows={p.rows}");

        _disabledFeatures.Add("DiceHeaderVersion2");
        if (_disabledFeatures.Count > 0)
            a.Add("--disable-features=" + string.Join(",", _disabledFeatures));
        // 28 实例级 DNS（默认关）
        var dnsArgs = FinalFeatures.DnsArgs(p, Store.LoadConfig());
        if (!string.IsNullOrEmpty(dnsArgs)) a.Add(dnsArgs);
        a.Add("--no-first-run");
        a.Add("--no-default-browser-check");

        // 上次没正常退出（exit_type=Crashed）时，Chromium 会弹「恢复页面」气泡。
        // 管理器虽然已经改成优雅关闭，但强杀/断电/任务管理器结束任务还是会留下痕迹。
        a.Add("--hide-crash-restore-bubble");
        if (!string.IsNullOrEmpty(p.extra)) a.Add(p.extra);
        return string.Join(" ", a);
    }

    public static string SelfTestUrl(string name, Profile p)
    {
        var q = new List<string>();
        void Add(string k, string v)
        {
            if (!string.IsNullOrWhiteSpace(v))
                q.Add($"{k}={Uri.EscapeDataString(v.Trim())}");
        }
        Add("seed", p.seed);
        Add("platform", p.platform);
        Add("timezone", p.timezone);
        Add("lang", p.lang);
        Add("screen", p.fp_screen);
        Add("dpr", p.fp_dpr);
        Add("cores", p.fp_cores);
        Add("memory", p.fp_memory);
        Add("geo", p.geo);
        Add("fonts", p.fp_fonts);
        Add("webrtc", WebRtcPolicy(p));
        var url = "file:///" + Paths.SelfTestHtml.Replace('\\', '/');
        return q.Count > 0 ? url + "?" + string.Join("&", q) : url;
    }
}
