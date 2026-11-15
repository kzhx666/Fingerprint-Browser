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
/// 默认浏览器路由。
///
/// 问题背景：Windows 只有一个"默认浏览器"，而本项目有几十个实例，它们共用同一个
/// chrome.exe。系统把链接交给 chrome.exe 时不会带 --user-data-dir，落到哪个实例
/// 取决于启动瞬间的上下文。
///
/// 做法：外部链接先交给 KZManager --route-url，由它决定落到哪个实例，再用
/// **显式 --user-data-dir** 启动 chrome。Chrome 发现该 profile 已有进程在跑，
/// 会把 URL 转发过去开新标签页，不会新开窗口。
///
/// 关键点：点链接的那一刻，前台窗口往往不是浏览器（可能是微信、QQ、邮件客户端），
/// 所以不能只看前台。管理器后台会持续记录「最后用过的实例」并落盘，路由进程读回
/// 这个记录。--route-url 每次都是独立进程，内存状态留不住，必须持久化。
///
/// 安全性：全程不修改 chrome++.ini 等任何共享配置，每次启动都带明确的 profile
/// 路径，不存在猜错目录导致串号的可能。
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

    /// <summary>当前记录的目标实例。</summary>
    public static string LastInstance => _lastInstance.Length > 0 ? _lastInstance : ReadState();

    /// <summary>功能开关（界面菜单绑定）。关闭后路由仍可用，只是不再自动记录。</summary>
    public static bool Enabled { get; set; } = true;

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

    // ------------------------------------------------------------ 状态持久化

    static string StateFile()
    {
        try { return Path.Combine(Paths.Root, "_state", "last-instance.txt"); }
        catch { return ""; }
    }

    static void WriteState(string instance)
    {
        try
        {
            var f = StateFile();
            if (f.Length == 0) return;
            Directory.CreateDirectory(Path.GetDirectoryName(f)!);
            var tmp = f + ".tmp";
            File.WriteAllText(tmp, instance, new UTF8Encoding(false));
            File.Move(tmp, f, overwrite: true);
        }
        catch { }
    }

    static string ReadState()
    {
        try
        {
            var f = StateFile();
            if (f.Length == 0 || !File.Exists(f)) return "";
            return File.ReadAllText(f, Encoding.UTF8).Trim();
        }
        catch { return ""; }
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
        if (string.IsNullOrEmpty(dataDir)) return "";
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

    // ------------------------------------------------------------ 后台记录

    /// <summary>
    /// 后台循环：持续记录「最后用过的实例」。
    /// 只写一个几字节的状态文件，绝不改动任何共享配置。
    /// </summary>
    public static void Run(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                if (Enabled)
                {
                    var dd = ForegroundDataDir();
                    if (dd.Length > 0)
                    {
                        var inst = InstanceOf(dd);
                        if (inst.Length > 0 &&
                            !string.Equals(inst, _lastInstance, StringComparison.Ordinal))
                        {
                            _lastInstance = inst;
                            _lastDataDir = dd;
                            WriteState(inst);
                        }
                    }
                }
            }
            catch { }
            try { token.WaitHandle.WaitOne(700); } catch { break; }
        }
    }

    /// <summary>记下"刚用过这个实例"，供管理器启动实例后调用。</summary>
    public static void NoteInstance(string instance)
    {
        if (string.IsNullOrWhiteSpace(instance)) return;
        _lastInstance = instance;
        try { _lastDataDir = Paths.DataDir(instance).TrimEnd('\\'); } catch { }
        WriteState(instance);
    }

    /// <summary>立即识别一次前台实例并记录，供菜单调用。</summary>
    public static string ApplyNow()
    {
        try
        {
            var dd = ForegroundDataDir();
            if (dd.Length > 0)
            {
                var inst = InstanceOf(dd);
                if (inst.Length > 0)
                {
                    _lastInstance = inst;
                    _lastDataDir = dd;
                    WriteState(inst);
                    return inst;
                }
            }
        }
        catch { }
        return LastInstance;
    }

    // ------------------------------------------------------------ 路由

    /// <summary>
    /// 决定链接落到哪个实例：
    /// 1) 前台就是浏览器 → 用它；
    /// 2) 否则用后台记录的最后实例（从微信/QQ 点链接走这条）；
    /// 3) 再否则用配置里的默认实例；
    /// 4) 最后用列表第一个。
    /// </summary>
    public static string PickTargetInstance(out string reason)
    {
        var dd = ForegroundDataDir();
        if (dd.Length > 0)
        {
            var inst = InstanceOf(dd);
            if (inst.Length > 0)
            {
                _lastInstance = inst;
                _lastDataDir = dd;
                WriteState(inst);
                reason = "前台实例";
                return inst;
            }
            reason = "前台是浏览器但不属于任何实例";
        }
        else
        {
            reason = "前台不是浏览器";
        }

        var recorded = _lastInstance.Length > 0 ? _lastInstance : ReadState();
        try
        {
            if (recorded.Length > 0 && Store.ListInstances().Contains(recorded))
            {
                reason += "，使用最后使用的实例";
                return recorded;
            }
        }
        catch { }

        try
        {
            var cfg = Store.LoadConfig();
            if (!string.IsNullOrWhiteSpace(cfg.defaultInstance) &&
                Store.ListInstances().Contains(cfg.defaultInstance))
            {
                reason += "，使用配置的默认实例";
                return cfg.defaultInstance;
            }
        }
        catch { }

        try
        {
            var all = Store.ListInstances();
            if (all.Count > 0)
            {
                reason += "，回退到第一个实例";
                return all[0];
            }
        }
        catch { }
        reason = "没有可用实例";
        return "";
    }

    public static string PickTargetInstance() => PickTargetInstance(out _);

    /// <summary>
    /// 安全路由：把 url 交给目标实例。
    /// 目标实例已在运行 → chrome 转发 URL，在原窗口开新标签；
    /// 目标实例没运行 → 正常启动它并打开该链接。
    /// </summary>
    public static bool RouteUrl(string url)
    {
        if (string.IsNullOrWhiteSpace(url)) return false;

        var inst = PickTargetInstance(out var reason);
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
            Log("route -> " + inst + " (" + reason + ")  " + url);
            _lastInstance = inst;
            _lastDataDir = dataDir;
            WriteState(inst);
            return true;
        }
        catch (Exception ex)
        {
            Log("启动失败: " + ex.Message);
            return false;
        }
    }

    /// <summary>诊断用：返回本次路由的决策过程。</summary>
    public static string Diagnose()
    {
        var inst = PickTargetInstance(out var reason);
        var dd = ForegroundDataDir();
        var fg = dd.Length > 0 ? (InstanceOf(dd) is var n && n.Length > 0 ? n : "（非实例）" + dd) : "（非浏览器）";
        var sb = new StringBuilder();
        sb.AppendLine("前台窗口所属：" + fg);
        sb.AppendLine("记录的最后实例：" + (ReadState() is var r && r.Length > 0 ? r : "（无）"));
        sb.AppendLine("决策依据：" + reason);
        sb.AppendLine("本次落点：" + (inst.Length > 0 ? inst : "（无）"));
        if (inst.Length > 0)
            sb.AppendLine("数据目录：" + Paths.DataDir(inst));
        return sb.ToString();
    }
}
