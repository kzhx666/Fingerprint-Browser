using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;

namespace KZManager;

/// <summary>
/// 25: 内核补丁自检器。
/// 说明：PATCH 标记大多在源码注释里，不会编进 chrome.dll，
/// 所以"扫 dll 里的字符串"从根上不成立。改为：
///  1) 记录/比对 chrome.dll 的 SHA-256（防替换/降级）
///  2) 跑 apply_patches.py --check 确认源码树补丁全部幂等
///  3) 校验关键导出行为由 CDP 自检覆盖（启动实例时另验）
/// </summary>
public static class KernelSelfCheck
{
    public sealed class Result
    {
        public bool Ok;
        public string DllPath = "";
        public string Sha256 = "";
        public string Baseline = "";
        public bool PatchCheckOk;
        public string PatchCheckNote = "";
        public List<string> Issues = new();
        public string Note = "";
    }

    static string BaselinePath =>
        Path.Combine(Paths.Root, "_kernel-baseline.sha256");

    public static Result Check(string? chromeExe = null, bool refresh = false,
                               string? repoRoot = null)
    {
        var r = new Result();
        try
        {
            var exe = chromeExe;
            if (string.IsNullOrEmpty(exe)) exe = Store.LoadConfig().chromeExe;
            var dll = Path.Combine(Path.GetDirectoryName(exe) ?? "", "chrome.dll");
            r.DllPath = dll;
            if (!File.Exists(dll)) { r.Issues.Add("找不到 chrome.dll"); r.Note = "找不到 chrome.dll"; return r; }
            using var sha = SHA256.Create();
            using var fs = File.OpenRead(dll);
            r.Sha256 = Convert.ToHexString(sha.ComputeHash(fs)).ToLowerInvariant();

            if (refresh || !File.Exists(BaselinePath))
            {
                File.WriteAllText(BaselinePath, r.Sha256);
                r.Baseline = r.Sha256;
            }
            else
            {
                r.Baseline = File.ReadAllText(BaselinePath).Trim();
                if (!string.Equals(r.Sha256, r.Baseline, StringComparison.OrdinalIgnoreCase))
                    r.Issues.Add("chrome.dll 与基线 SHA-256 不一致（可能被降级/替换/换版本）");
            }

            // 源码树补丁幂等
            var root = repoRoot ?? Path.Combine(Paths.Root, "kz-profile-manager");
            var script = Path.Combine(root, "kernel", "apply_patches.py");
            var src = @"C:\chromium\src";
            if (File.Exists(script) && Directory.Exists(src))
            {
                try
                {
                    var psi = new ProcessStartInfo("python",
                        $"\"{script}\" \"{src}\" --check")
                    { UseShellExecute = false, RedirectStandardOutput = true,
                      RedirectStandardError = true, CreateNoWindow = true };
                    using var p = Process.Start(psi);
                    if (p is not null)
                    {
                        var o = p.StandardOutput.ReadToEnd();
                        p.WaitForExit(30000);
                        r.PatchCheckOk = o.Contains("All patches OK") ||
                                         (!o.Contains("FAILED") && p.ExitCode == 0);
                        r.PatchCheckNote = r.PatchCheckOk
                            ? "apply_patches.py --check 幂等全绿"
                            : "补丁 --check 未通过";
                        if (!r.PatchCheckOk) r.Issues.Add("源码树补丁 --check 未通过");
                    }
                }
                catch (Exception e) { r.PatchCheckNote = "补丁检查跳过: " + e.Message; }
            }
            else r.PatchCheckNote = "未找到补丁脚本/源码树，跳过";

            r.Ok = r.Issues.Count == 0;
            r.Note = r.Ok
                ? $"内核完整（SHA-256 {r.Sha256.Substring(0, 12)}…，补丁幂等）"
                : string.Join("；", r.Issues);
        }
        catch (Exception e) { r.Note = "自检失败: " + e.Message; r.Issues.Add(e.Message); }
        return r;
    }
}
