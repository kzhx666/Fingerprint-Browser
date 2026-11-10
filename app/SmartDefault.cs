using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 「智能默认浏览器」：当前哪个实例窗口在前台，点外部链接就落到哪个实例。
///
/// 原理：Chrome++ 每次进程启动都重新读 chrome++.ini 的 data_dir，
/// 且只在没有显式 --user-data-dir 时才注入。外部链接走的是无参数启动，
/// 所以只要把 data_dir 持续同步成「前台窗口所属实例的 Data 目录」即可。
///
/// 前台不是 chrome 时保持上一次选择（从微信/QQ 点链接时落最后用的那个）。
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
    public static bool Enabled { get; set; } = true;

    // ------------------------------------------------------------ ini 路径

    static string IniPath(string chromeExe)
    {
        var dir = Path.GetDirectoryName(chromeExe);
        return string.IsNullOrEmpty(dir) ? "" : Path.Combine(dir, "chrome++.ini");
    }

    /// <summary>原子写 data_dir，保持 UTF-16LE BOM。仅在值变化时写。</summary>
    static void WriteDataDir(string ini, string dataDir)
    {
        var txt = File.ReadAllText(ini, Encoding.Unicode);
        var want = "data_dir=\"" + dataDir + "\"";
        var m = Regex.Match(txt, @"(?m)^data_dir=.*$");
        string next;
        if (m.Success)
        {
            if (m.Value.TrimEnd('\r') == want) return;   // 已经是目标，不写
            next = txt.Substring(0, m.Index) + want + txt.Substring(m.Index + m.Length);
        }
        else
        {
            next = txt.Replace("[general]", "[general]\r\n" + want);
        }
        // 写临时文件再原子替换，避免 Chrome++ 读到半截
        var tmp = ini + ".kz.tmp";
        File.WriteAllText(tmp, next, new UnicodeEncoding(false, true));
        File.Copy(tmp, ini, true);
        try { File.Delete(tmp); } catch { }
    }

    // ------------------------------------------------------------ 前台窗口解析

    // PID -> data dir 缓存。chrome 主进程生命周期内命令行不变，
    // 避免每 700ms 起一次 PowerShell。
    static readonly Dictionary<uint, string> _pidCache = new();
    static readonly object _cacheLock = new();

    static void Log(string s)
    {
        try
        {
            File.AppendAllText(@"C:\temp\smart-default.log",
                DateTime.Now.ToString("HH:mm:ss") + " " + s + "\r\n",
                new UTF8Encoding(false));
        }
        catch { }
    }

    /// <summary>拿前台窗口所属 chrome 主进程的 --user-data-dir。</summary>
    static string ForegroundDataDir()
    {
        var h = GetForegroundWindow();
        if (h == IntPtr.Zero || !IsWindowVisible(h)) return "";
        GetWindowThreadProcessId(h, out var pid);
        if (pid == 0) return "";

        // 快速判断是不是 chrome（不启子进程）
        string procName;
        try
        {
            using var p = System.Diagnostics.Process.GetProcessById((int)pid);
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
            // 单引号包 Filter，避开嵌套双引号转义
            var psi = new System.Diagnostics.ProcessStartInfo
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
            using var ps = System.Diagnostics.Process.Start(psi);
            if (ps is null) return "";
            var cl = ps.StandardOutput.ReadToEnd();
            ps.WaitForExit(2000);
            var m = DataDirRe.Match(cl);
            if (!m.Success)
            {
                Log("pid=" + pid + " 无 data-dir, cl=" + cl.Substring(0, Math.Min(120, cl.Length)));
                return "";
            }
            var dd = (m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value)
                     .TrimEnd('\\');
            lock (_cacheLock) { _pidCache[pid] = dd; }
            Log("pid=" + pid + " -> " + dd);
            return dd;
        }
        catch (Exception ex) { Log("查询异常: " + ex.Message); return ""; }
    }

    /// <summary>把 data dir 反查成实例名（用于状态显示）。</summary>
    static string InstanceOf(string dataDir)
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

    // ------------------------------------------------------------ 循环

    public static void Run(CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                if (Enabled)
                {
                    var cfg = Store.LoadConfig();
                    var ini = IniPath(cfg.chromeExe);
                    if (ini.Length > 0 && File.Exists(ini))
                    {
                        var dd = ForegroundDataDir();
                        if (dd.Length > 0 && !string.Equals(dd, _lastDataDir,
                                                            StringComparison.OrdinalIgnoreCase))
                        {
                            var inst = InstanceOf(dd);
                            if (inst.Length > 0)   // 只认管理器的实例，别认野路子 profile
                            {
                                WriteDataDir(ini, dd);
                                _lastDataDir = dd;
                                _lastInstance = inst;
                                Log("WRITE ini -> " + inst);
                            }
                        }
                    }
                }
            }
            catch { }
            try { token.WaitHandle.WaitOne(700); } catch { break; }
        }
    }

    /// <summary>一次性把当前前台实例写入 ini，供状态显示/菜单调用。</summary>
    public static string ApplyNow()
    {
        try
        {
            var cfg = Store.LoadConfig();
            var ini = IniPath(cfg.chromeExe);
            var dd = ForegroundDataDir();
            if (dd.Length > 0 && File.Exists(ini))
            {
                var inst = InstanceOf(dd);
                if (inst.Length > 0)
                {
                    WriteDataDir(ini, dd);
                    _lastDataDir = dd; _lastInstance = inst;
                    return inst;
                }
            }
        }
        catch { }
        return _lastInstance;
    }
}
