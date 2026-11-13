using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 「默认浏览器路由」。
///
/// 旧实现（已废弃，危险）：每 700ms 把 chrome++.ini 的 data_dir 改写成前台实例的
/// 数据目录。问题是 chrome++.ini 是全局共享文件，任何一次没有显式
/// --user-data-dir 的启动都会读到「上一次前台」的值，导致实例串号，甚至覆盖
/// 别人的登录数据。
///
/// 新实现（安全）：完全不碰 chrome++.ini。外部链接先经过 KZManager
/// (--route-url)，由它判断「当前前台是哪个实例」，然后用显式
/// --user-data-dir 启动 chrome.exe；chrome 会把 URL 转发给该实例已运行的进程。
/// 每一次启动都带明确的 profile 路径，不存在猜错目录的可能。
/// </summary>
public static class SmartDefault
{
    [DllImport("user32.dll")]
    static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);

    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr h);

    static readonly Regex DataDirRe = new(
        @"--user-data-dir=(?:""([^""]+)""|(\S+))",
        RegexOptions.Compiled | RegexOptions.IgnoreCase);

    static string _lastDataDir = "";
    static string _lastInstance = "";
    public static string LastInstance => _lastInstance;

    /// <summary>旧的 ini 改写循环已停用，保留字段以兼容界面绑定。</summary>
    public static bool Enabled { get; set; } = false;

    static void Log(string s)
    {
        try
        {
            var dir = Path.Combine(Path.GetTempPath(), "kz");
            Directory.CreateDirectory(dir);
            File.AppendAllText(Path.Combine(dir, "route.log"),
                DateTime.Now.ToString("HH:mm:ss") + " " + s + "\r\n",
                new UTF8Encoding(false));
        }
        catch { }
    }

    // ------------------------------------------------------------ 前台窗口解析

    static readonly Dictionary<uint, string> _pidCache = new();
    static readonly object _cacheLock = new();

    /// <summary>拿前台窗口所属 chrome 主进程的 --user-data-dir。</summary>
    public static string ForegroundDataDir()
    {
        var h = GetForegroundWindow();
        if (h == IntPtr.Zero || !IsWindowVisible(h)) return "";
        GetWindowThreadProcessId(h, out var pid);
        if (pid == 0) return "";

        string procName;
        try
        {
            using var p = Process.GetProcessById((int)pid);
            procName = p.ProcessName;
        }
        catch { return ""; }
        if (!procName.Equals("chrome", StringComparison.OrdinalIgnoreCase)) return "";

        lock (_cacheLock)
        {
            if (_pidCache.TryGetValue(pid, out var cached)) return cached;
        }

        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = "-NoProfile -NonInteractive -Command \"" +
                    "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);" +
                    "(Get-CimInstance Win32_Process -Filter 'ProcessId=" + pid + "').CommandLine\"",
                UseShellExecute = false,
                RedirectStandardOutput = true,
                StandardOutputEncoding = Encoding.UTF8,
                CreateNoWindow = true
            };
            using var ps = Process.Start(psi);
            if (ps is null) return "";
            var cl = ps.StandardOutput.ReadToEnd();
            ps.WaitForExit(2000);
            var m = DataDirRe.Match(cl);
            if (!m.Success) return "";
            var dd = (m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value).TrimEnd('\\');
            lock (_cacheLock) { _pidCache[pid] = dd; }
            return dd;
        }
        catch (Exception ex) { Log("查询异常: " + ex.Message); return ""; }
    }

    /// <summary>把 data dir 反查成实例名。</summary>
    public static string InstanceOf(string dataDir)
    {
        try
        {
            foreach (var name in Store.ListInstances())
            {
                var d = Paths.DataDir(name).TrimEnd('\\');
                if (string.Equals(d, dataDir, StringComparison.OrdinalIgnoreCase))
                    return name;
            }
        }
        catch { }
        return "";
    }

    // ------------------------------------------------------------ 安全路由

    /// <summary>
    /// 决定这条外部链接该落到哪个实例：
    /// 1) 前台正在使用的 chrome 实例；
    /// 2) 上一次前台用过的实例（前台不是 chrome 时，比如从微信点链接）；
    /// 3) 配置里的默认实例；
    /// 4) 第一个实例。
    /// 返回实例名，找不到返回空串。
    /// </summary>
    public static string PickTargetInstance()
    {
        var dd = ForegroundDataDir();
        if (dd.Length > 0)
        {
            var inst = InstanceOf(dd);
            if (inst.Length > 0)
            {
                _lastDataDir = dd;
                _lastInstance = inst;
                return inst;
            }
        }
        if (_lastInstance.Length > 0 && Store.ListInstances().Contains(_lastInstance))
            return _lastInstance;

        try
        {
            var cfg = Store.LoadConfig();
            if (!string.IsNullOrWhiteSpace(cfg.defaultInstance) &&
                Store.ListInstances().Contains(cfg.defaultInstance))
                return cfg.defaultInstance;
        }
        catch { }

        try
        {
            var all = Store.ListInstances();
            if (all.Count > 0) return all[0];
        }
        catch { }
        return "";
    }

    /// <summary>
    /// 安全路由入口：把 url 交给目标实例。
    /// 关键点——始终显式传 --user-data-dir，绝不依赖任何共享配置文件。
    /// </summary>
    public static bool RouteUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return false;

        var inst = PickTargetInstance();
        if (inst.Length == 0)
        {
            Log("无可用实例，url=" + url);
            return false;
        }

        string chromeExe;
        try { chromeExe = Store.LoadConfig().chromeExe; }
        catch { chromeExe = Path.Combine(Paths.Root, "App", "chrome.exe"); }
        if (!File.Exists(chromeExe))
        {
            Log("chrome.exe 不存在: " + chromeExe);
            return false;
        }

        var dataDir = Paths.DataDir(inst).TrimEnd('\\');
        try
        {
            var psi = new ProcessStartInfo
            {
                FileName = chromeExe,
                Arguments = "--user-data-dir=\"" + dataDir + "\" \"" + url.Replace("\"", "%22") + "\"",
                UseShellExecute = false,
                WorkingDirectory = Path.GetDirectoryName(chromeExe) ?? Paths.Root
            };
            Process.Start(psi);
            Log("route -> " + inst + "  " + url);
            _lastInstance = inst;
            _lastDataDir = dataDir;
            return true;
        }
        catch (Exception ex)
        {
            Log("启动失败: " + ex.Message);
            return false;
        }
    }

    // ------------------------------------------------------------ 兼容旧调用

    /// <summary>旧的 ini 改写循环：已停用，立即返回。</summary>
    public static void Run(CancellationToken token)
    {
        // 不再改写 chrome++.ini —— 见类注释。
    }

    /// <summary>兼容旧界面：只做前台识别，不写任何配置文件。</summary>
    public static string ApplyNow()
    {
        var inst = PickTargetInstance();
        return inst.Length > 0 ? inst : _lastInstance;
    }
}
