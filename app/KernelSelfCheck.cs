using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;

namespace KZManager;

/// <summary>
/// 25: 内核补丁自检器。启动时扫描 chrome.dll 里的 PATCH 标记，
/// 与 apply_patches.py 声明的期望清单比对，防止内核被降级/替换/篡改。
/// </summary>
public static class KernelSelfCheck
{
    public sealed class Result
    {
        public bool Ok;
        public int Found;
        public int Expected;
        public List<string> Missing = new();
        public string DllPath = "";
        public string Note = "";
    }

    // 与 kernel/apply_patches.py 的关键标记一致（不含注释性 marker）
    static readonly string[] kExpected = {
        "PATCH:canvas-v5", "PATCH:canvas-v4", "PATCH:audio-noise",
        "PATCH:v7-derived", "PATCH:v7-colordepth", "PATCH:v7-dnt",
        "PATCH:v7-touch", "PATCH:v7-netinfo", "PATCH:v7-storage",
        "PATCH:v7-webrtc", "PATCH:fp-audio-offline", "PATCH:fp-audio-biquad",
        "PATCH:fp-audio-analyser", "PATCH:fp-audio-copy", "PATCH:fp-audio-ctx",
        "PATCH:fp-audio-hw", "PATCH:fp-audio-worklet", "PATCH:fp-audio-h",
        "PATCH:v10-gl", "PATCH:v10-media", "PATCH:v10-speech",
        "PATCH:v10-webgpu", "PATCH:v10-meminfo", "PATCH:v10b-rect",
        "PATCH:v11-auth", "PATCH:v11-parse", "PATCH:v11-cjpf",
        "PATCH:v11-sp", "PATCH:v11-socks5", "PATCH:no-lens-text-search"
    };

    public static Result Check(string? chromeExe = null)
    {
        var r = new Result();
        try
        {
            var exe = chromeExe;
            if (string.IsNullOrEmpty(exe))
            {
                var cfg = Store.LoadConfig();
                exe = cfg.chromeExe;
            }
            var dll = Path.Combine(Path.GetDirectoryName(exe) ?? "", "chrome.dll");
            r.DllPath = dll;
            if (!File.Exists(dll))
            {
                r.Note = "找不到 chrome.dll";
                return r;
            }
            // 只扫前 64MB 里的可打印 ASCII，够覆盖所有 PATCH 字符串常量
            var bytes = File.ReadAllBytes(dll);
            var text = Encoding.ASCII.GetString(bytes, 0,
                Math.Min(bytes.Length, 64 * 1024 * 1024));
            foreach (var tag in kExpected)
            {
                r.Expected++;
                if (text.Contains(tag)) r.Found++;
                else r.Missing.Add(tag);
            }
            r.Ok = r.Missing.Count == 0;
            r.Note = r.Ok
                ? $"内核补丁完整（{r.Found}/{r.Expected}）"
                : $"内核补丁缺失 {r.Missing.Count} 项，可能被降级或替换";
        }
        catch (Exception e)
        {
            r.Note = "自检失败: " + e.Message;
        }
        return r;
    }
}
