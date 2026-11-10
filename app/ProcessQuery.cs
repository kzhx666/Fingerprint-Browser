using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace KZManager;

/// <summary>通过 PowerShell 查 chrome 进程（避免额外 NuGet 依赖）。</summary>
public static class ProcessQuery
{
    [System.Runtime.InteropServices.DllImport("user32.dll", SetLastError = true)]
    static extern bool PostMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

    const uint WM_CLOSE = 0x0010;

    static (int code, string stdout) Ps(string script)
    {
        var psi = new ProcessStartInfo("powershell")
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        psi.ArgumentList.Add("-NoProfile");
        psi.ArgumentList.Add("-NonInteractive");
        psi.ArgumentList.Add("-Command");
        // 子进程默认按控制台代码页（GBK）输出，我们按 UTF-8 读，
        // 中文路径会变成 ????，导致实例名匹配失败。强制子进程输出 UTF-8。
        psi.ArgumentList.Add(
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); " + script);
        try
        {
            using var p = Process.Start(psi)!;
            var so = p.StandardOutput.ReadToEnd();
            p.StandardError.ReadToEnd();
            p.WaitForExit(15000);
            return (p.HasExited ? p.ExitCode : -1, so);
        }
        catch { return (-1, ""); }
    }

    /// <summary>返回 user-data-dir → PID 的映射（只取主进程，忽略 --type= 的子进程）。</summary>
    public static Dictionary<string, int> ChromeDataDirs()
    {
        var map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var (code, so) = Ps(
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | " +
            "Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' } | " +
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress");
        if (code != 0 || string.IsNullOrWhiteSpace(so)) return map;

        try
        {
            using var doc = JsonDocument.Parse(so);
            var root = doc.RootElement;
            if (root.ValueKind == JsonValueKind.Array)
            {
                foreach (var el in root.EnumerateArray()) AddOne(el, map);
            }
            else if (root.ValueKind == JsonValueKind.Object)
            {
                AddOne(root, map);
            }
        }
        catch { }
        return map;
    }

    static void AddOne(JsonElement el, Dictionary<string, int> map)
    {
        if (!el.TryGetProperty("CommandLine", out var cmdEl)) return;
        if (!el.TryGetProperty("ProcessId", out var pidEl)) return;
        var cmd = cmdEl.GetString() ?? "";
        var pid = pidEl.GetInt32();
        var m = System.Text.RegularExpressions.Regex.Match(
            // 路径可能带引号也可能不带。不带引号时必须用 \S+ 停下来，
            // 否则 [^"]+ 会一路吃到下一个引号，把后面的参数也吞进去。
            cmd, @"--user-data-dir=(?:""([^""]+)""|(\S+))");
        if (!m.Success) return;
        var dir = (m.Groups[1].Success ? m.Groups[1].Value : m.Groups[2].Value)
                  .Trim().TrimEnd('\\');
        map[dir] = pid;
    }

    /// <summary>杀掉指定 user-data-dir 的所有 chrome 进程（含子进程）。返回结束数量。</summary>
    public static int KillChromeByDataDir(string dataDir)
    {
        var target = dataDir.TrimEnd('\\').ToLowerInvariant();
        var (code, so) = Ps(
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | " +
            "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress");
        if (code != 0 || string.IsNullOrWhiteSpace(so)) return 0;

        var pids = new List<int>();
        try
        {
            using var doc = JsonDocument.Parse(so);
            var root = doc.RootElement;
            IEnumerable<JsonElement> items = root.ValueKind == JsonValueKind.Array
                ? root.EnumerateArray()
                : new[] { root };
            foreach (var el in items)
            {
                if (!el.TryGetProperty("CommandLine", out var c)) continue;
                if (!el.TryGetProperty("ProcessId", out var p)) continue;
                var cmd = (c.GetString() ?? "").ToLowerInvariant();
                if (cmd.Contains(target)) pids.Add(p.GetInt32());
            }
        }
        catch { }

        int killed = 0;
        foreach (var pid in pids.Distinct())
        {
            try
            {
                using var pr = Process.GetProcessById(pid);

                // 先发 WM_CLOSE，让 Chrome 自己收尾。
                // 直接 TerminateProcess 会丢掉还没写盘的 Preferences，
                // 登录态（account_info）就是这样没的。
                pr.Refresh();
                var hwnd = pr.MainWindowHandle;
                bool graceful = false;
                if (hwnd != IntPtr.Zero)
                {
                    PostMessage(hwnd, WM_CLOSE, IntPtr.Zero, IntPtr.Zero);
                    graceful = pr.WaitForExit(8000);
                }

                if (!graceful && !pr.HasExited)
                {
                    // 8 秒还没退（有对话框卡着之类），才强杀
                    pr.Kill(true);
                    pr.WaitForExit(3000);
                }
                killed++;
            }
            catch { }
        }
        return killed;
    }

    public static bool IsPythonAvailable()
    {
        try
        {
            var psi = new ProcessStartInfo("python", "--version")
            {
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                UseShellExecute = false,
                CreateNoWindow = true
            };
            using var p = Process.Start(psi);
            if (p is null) return false;
            p.WaitForExit(5000);
            return p.ExitCode == 0;
        }
        catch { return false; }
    }
}
