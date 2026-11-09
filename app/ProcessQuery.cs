using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;

namespace KZManager;

/// <summary>通过 PowerShell 查 chrome 进程（避免额外 NuGet 依赖）。</summary>
public static class ProcessQuery
{
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
        psi.ArgumentList.Add(script);
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
            cmd, "--user-data-dir=\"?([^\"]+)\"?");
        if (!m.Success) return;
        var dir = m.Groups[1].Value.Trim().TrimEnd('\\');
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
                pr.Kill(true);
                pr.WaitForExit(3000);
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
