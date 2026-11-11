using System;
using System.Collections.Generic;

namespace KZManager;

/// <summary>
/// 6. 指纹冲突精准诊断器。
/// 跨维度检查语言、时区、出口地区、地理位置、平台与硬件参数的相容性，
/// 明确指出风控判定矛盾原因并给出修改建议。
/// </summary>
public static class FingerprintDiagnostic
{
    public sealed class DiagnosticItem
    {
        public string Level { get; set; } = "info"; // ok / warning / error
        public string Title { get; set; } = "";
        public string Detail { get; set; } = "";
        public string Suggestion { get; set; } = "";
    }

    public sealed class DiagnosticReport
    {
        public int RiskScore { get; set; } // 0=安全, 100=极度高危
        public bool Passed { get; set; }
        public List<DiagnosticItem> Items { get; set; } = new();
    }

    public static DiagnosticReport Diagnose(Profile p, ProxyPrecheck.Result? precheck = null)
    {
        var rep = new DiagnosticReport { RiskScore = 0, Passed = true };

        void Add(string level, string title, string detail, string suggestion, int riskWeight)
        {
            rep.Items.Add(new DiagnosticItem {
                Level = level,
                Title = title,
                Detail = detail,
                Suggestion = suggestion
            });
            if (level == "error") { rep.RiskScore += riskWeight; rep.Passed = false; }
            else if (level == "warning") { rep.RiskScore += (riskWeight / 2); }
        }

        // 1. 平台与 WebGL 显卡一致性
        var plat = (p.platform ?? "windows").ToLowerInvariant();
        var gl = (p.fp_webgl ?? "").ToLowerInvariant();
        if (plat == "macos" && gl.Contains("nvidia"))
        {
            Add("error", "系统与显卡驱动特征致命冲突",
                "当前声明为 macOS 系统，但 WebGL 显卡配置为 NVIDIA GPU（现代 macOS 严禁/无 NVIDIA 驱动支持）",
                "请将 WebGL 显卡改为 Apple 或 Intel Mac 核显", 35);
        }

        // 2. 平台与 DPR 屏幕缩放一致性
        if (double.TryParse(p.fp_dpr, out var dprVal))
        {
            if (plat == "macos" && Math.Abs(dprVal - 2.0) > 0.01 && Math.Abs(dprVal - 1.0) > 0.01)
            {
                Add("warning", "macOS 屏幕缩放率异常",
                    $"macOS Retina 屏幕原生 DPR 通常为 2.0，当前为 {p.fp_dpr}",
                    "建议将 DPR 设置为 2.0 以符合真实 Mac Retina 显示", 10);
            }
        }

        // 3. 时区与声明地区一致性
        var reg = ProxyPrecheck.InferDeclared(p.name, p.timezone, p.region);
        var tz = (p.timezone ?? "").ToLowerInvariant();
        if (reg == "US" && (tz.Contains("asia/") || tz.Contains("europe/")))
        {
            Add("error", "时区与声明地区冲突",
                $"实例地区声明为美国 (US)，但配置时区为 {p.timezone}",
                "请将时区修改为 America/New_York 或 America/Los_Angeles", 25);
        }
        else if (reg == "JP" && !tz.Contains("tokyo"))
        {
            Add("warning", "日本地区时区建议",
                $"声明地区为日本 (JP)，当前时区为 {p.timezone}",
                "建议统一使用 Asia/Tokyo", 15);
        }

        // 4. 语言与声明地区一致性
        var lang = (p.lang ?? "").ToLowerInvariant();
        if ((reg == "US" || reg == "GB") && lang.StartsWith("zh"))
        {
            Add("warning", "首选语言与出口地区不匹配",
                $"声明地区为英语区 ({reg})，但浏览器首选语言设置为中文 ({p.lang})，目标网站容易触发二审风控",
                "建议将首选语言设置为 en-US,en 或在后面追加中文", 15);
        }

        // 5. WebRTC 与代理防泄漏检查
        var webrtc = (p.webrtc ?? "").ToLowerInvariant();
        bool hasProxy = !string.IsNullOrEmpty(p.proxy) || !string.IsNullOrEmpty(p.node);
        if (hasProxy && (webrtc == "direct" || webrtc == "off"))
        {
            Add("warning", "WebRTC 本地私网 IP 泄漏风险",
                "配置了代理，但 WebRTC 处于直连/未拦截模式，可能会向 STUN 服务器暴露真实网卡内网 IP",
                "建议将 WebRTC 策略设为 strict (禁用非代理UDP) 或 proxy", 20);
        }

        // 6. 预检实际出口 IP 联动核验
        if (precheck is not null && precheck.Ok && !string.IsNullOrEmpty(precheck.Country))
        {
            if (!string.IsNullOrEmpty(reg) && !string.Equals(reg, precheck.Country, StringComparison.OrdinalIgnoreCase))
            {
                Add("error", "代理出口国家与实例声明地区不符",
                    $"实例声明地区为 {reg}，但代理实际探测出口国家为 {precheck.Country} (IP: {precheck.Ip})",
                    "请更换匹配地区的代理节点，或使用「地域自动匹配」联动更新实例配置", 30);
            }
        }

        rep.RiskScore = Math.Min(100, rep.RiskScore);
        if (rep.Items.Count == 0)
        {
            Add("ok", "各维度指纹逻辑相容", "未发现软硬件、网络或地区维度的逻辑矛盾。", "继续保持当前方案。", 0);
        }

        return rep;
    }
}
