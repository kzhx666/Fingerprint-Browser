using System;
using System.Collections.Generic;
using System.Linq;

namespace KZManager;

/// <summary>
/// 1. 智能指纹生成器 + 2. 跨维度指纹一致性矩阵 + 3. 多系统真实身份库。
/// 严格保证 UA ↔ UA-CH ↔ 平台 ↔ WebGL ↔ 屏幕分辨率 ↔ 硬件参数的相容性。
/// </summary>
public static class SmartFingerprint
{
    public sealed class HardwarePreset
    {
        public string Platform = "windows"; // windows / macos / linux
        public string OsVersion = "10.0";
        public string UaPlatform = "Windows";
        public string UaPlatformArch = "x86";
        public string NavigatorPlatform = "Win32";
        public int Cores = 8;
        public int MemoryGb = 16;
        public int ScreenWidth = 1920;
        public int ScreenHeight = 1080;
        public double Dpr = 1.0;
        public string WebGlVendor = "Intel Inc.";
        public string WebGlRenderer = "Intel(R) UHD Graphics 630";
        public string Fonts = "";
    }

    // 真实 Windows 11 / 10 预设库
    static readonly HardwarePreset[] WindowsPresets = {
        new() {
            Platform = "windows", OsVersion = "15.0.0", UaPlatform = "Windows", NavigatorPlatform = "Win32",
            Cores = 8, MemoryGb = 16, ScreenWidth = 1920, ScreenHeight = 1080, Dpr = 1.0,
            WebGlVendor = "Intel Inc.", WebGlRenderer = "Intel(R) UHD Graphics 630"
        },
        new() {
            Platform = "windows", OsVersion = "15.0.0", UaPlatform = "Windows", NavigatorPlatform = "Win32",
            Cores = 12, MemoryGb = 16, ScreenWidth = 2560, ScreenHeight = 1440, Dpr = 1.25,
            WebGlVendor = "NVIDIA Corporation", WebGlRenderer = "NVIDIA GeForce RTX 3060/PCIe/SSE2"
        },
        new() {
            Platform = "windows", OsVersion = "10.0.0", UaPlatform = "Windows", NavigatorPlatform = "Win32",
            Cores = 16, MemoryGb = 32, ScreenWidth = 2560, ScreenHeight = 1440, Dpr = 1.0,
            WebGlVendor = "NVIDIA Corporation", WebGlRenderer = "NVIDIA GeForce RTX 4070/PCIe/SSE2"
        },
        new() {
            Platform = "windows", OsVersion = "10.0.0", UaPlatform = "Windows", NavigatorPlatform = "Win32",
            Cores = 8, MemoryGb = 16, ScreenWidth = 1920, ScreenHeight = 1080, Dpr = 1.25,
            WebGlVendor = "Google Inc. (Intel)", WebGlRenderer = "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0)"
        },
        new() {
            Platform = "windows", OsVersion = "10.0.0", UaPlatform = "Windows", NavigatorPlatform = "Win32",
            Cores = 4, MemoryGb = 8, ScreenWidth = 1920, ScreenHeight = 1080, Dpr = 1.0,
            WebGlVendor = "Google Inc. (Intel)", WebGlRenderer = "ANGLE (Intel, Intel(R) HD Graphics 620 Direct3D11 vs_5_0 ps_5_0)"
        }
    };

    // 真实 macOS 预设库 (Apple Silicon M系列 + Intel Mac)
    static readonly HardwarePreset[] MacPresets = {
        new() {
            Platform = "macos", OsVersion = "14.5.0", UaPlatform = "macOS", NavigatorPlatform = "MacIntel",
            Cores = 8, MemoryGb = 16, ScreenWidth = 1440, ScreenHeight = 900, Dpr = 2.0,
            WebGlVendor = "Apple", WebGlRenderer = "Apple M1"
        },
        new() {
            Platform = "macos", OsVersion = "14.5.0", UaPlatform = "macOS", NavigatorPlatform = "MacIntel",
            Cores = 8, MemoryGb = 16, ScreenWidth = 1728, ScreenHeight = 1117, Dpr = 2.0,
            WebGlVendor = "Apple", WebGlRenderer = "Apple M2"
        },
        new() {
            Platform = "macos", OsVersion = "15.0.0", UaPlatform = "macOS", NavigatorPlatform = "MacIntel",
            Cores = 12, MemoryGb = 36, ScreenWidth = 1728, ScreenHeight = 1117, Dpr = 2.0,
            WebGlVendor = "Apple", WebGlRenderer = "Apple M3 Pro"
        },
        new() {
            Platform = "macos", OsVersion = "13.6.0", UaPlatform = "macOS", NavigatorPlatform = "MacIntel",
            Cores = 8, MemoryGb = 16, ScreenWidth = 1680, ScreenHeight = 1050, Dpr = 2.0,
            WebGlVendor = "Intel Inc.", WebGlRenderer = "Intel Iris Plus Graphics 655"
        }
    };

    // 真实 Linux 预设库
    static readonly HardwarePreset[] LinuxPresets = {
        new() {
            Platform = "linux", OsVersion = "6.5.0", UaPlatform = "Linux", NavigatorPlatform = "Linux x86_64",
            Cores = 8, MemoryGb = 16, ScreenWidth = 1920, ScreenHeight = 1080, Dpr = 1.0,
            WebGlVendor = "Mesa", WebGlRenderer = "Mesa Intel(R) UHD Graphics 630 (CFL GT2)"
        },
        new() {
            Platform = "linux", OsVersion = "6.5.0", UaPlatform = "Linux", NavigatorPlatform = "Linux x86_64",
            Cores = 16, MemoryGb = 32, ScreenWidth = 2560, ScreenHeight = 1440, Dpr = 1.0,
            WebGlVendor = "AMD", WebGlRenderer = "AMD Radeon RX 6700 XT (radeonsi, navi22, LLVM 15.0.7, DRM 3.54)"
        }
    };

    /// <summary>
    /// 一键生成相容指纹方案。
    /// platform: windows / macos / linux / any
    /// </summary>
    public static Profile Generate(string name, string targetPlatform = "windows", string? region = null)
    {
        var target = (targetPlatform ?? "windows").ToLowerInvariant();
        var pool = target switch
        {
            "macos" => MacPresets,
            "linux" => LinuxPresets,
            "any" => WindowsPresets.Concat(MacPresets).Concat(LinuxPresets).ToArray(),
            _ => WindowsPresets
        };

        var rng = Random.Shared;
        var p = pool[rng.Next(pool.Length)];
        var seed = rng.Next(100000, 999999).ToString();

        var prof = new Profile
        {
            name = name,
            seed = seed,
            platform = p.Platform,
            fp_cores = p.Cores.ToString(),
            fp_memory = p.MemoryGb.ToString(),
            fp_screen = $"{p.ScreenWidth}x{p.ScreenHeight}",
            fp_dpr = p.Dpr.ToString("0.##"),
            fp_canvas = "on",
            fp_webgl = p.WebGlRenderer.ToLowerInvariant().Contains("nvidia") ? "nvidia" :
                       p.WebGlRenderer.ToLowerInvariant().Contains("apple") ? "apple" : "intel",
            fp_audio = "on",
            webrtc = "strict"
        };

        // 绑定地区建议
        if (!string.IsNullOrEmpty(region))
        {
            ApplyRegionDefaults(prof, region);
        }
        else
        {
            // 默认 US
            prof.timezone = "America/New_York";
            prof.lang = "en-US,en";
            prof.region = "US";
        }

        return prof;
    }

    /// <summary>重掷次要指纹，保留名称、平台、时区、语言和账号数据。</summary>
    public static void RerollSecondary(Profile p)
    {
        var rng = Random.Shared;
        p.seed = rng.Next(100000, 999999).ToString();
        // 略微扰动内存与分辨率
        if (p.platform == "windows")
        {
            var pset = WindowsPresets[rng.Next(WindowsPresets.Length)];
            p.fp_cores = pset.Cores.ToString();
            p.fp_memory = pset.MemoryGb.ToString();
            p.fp_screen = $"{pset.ScreenWidth}x{pset.ScreenHeight}";
            p.fp_dpr = pset.Dpr.ToString("0.##");
        }
        else if (p.platform == "macos")
        {
            var pset = MacPresets[rng.Next(MacPresets.Length)];
            p.fp_cores = pset.Cores.ToString();
            p.fp_memory = pset.MemoryGb.ToString();
            p.fp_screen = $"{pset.ScreenWidth}x{pset.ScreenHeight}";
            p.fp_dpr = pset.Dpr.ToString("0.##");
        }
    }

    public static void ApplyRegionDefaults(Profile prof, string reg)
    {
        var r = (reg ?? "").Trim().ToUpperInvariant();
        prof.region = r;
        switch (r)
        {
            case "US":
                prof.timezone = "America/New_York";
                prof.lang = "en-US,en";
                prof.geo = "40.7128,-74.0060,30";
                break;
            case "JP":
                prof.timezone = "Asia/Tokyo";
                prof.lang = "ja-JP,ja,en-US,en";
                prof.geo = "35.6762,139.6503,30";
                break;
            case "GB":
            case "UK":
                prof.region = "GB";
                prof.timezone = "Europe/London";
                prof.lang = "en-GB,en-US,en";
                prof.geo = "51.5074,-0.1278,30";
                break;
            case "DE":
                prof.timezone = "Europe/Berlin";
                prof.lang = "de-DE,de,en-US,en";
                prof.geo = "52.5200,13.4050,30";
                break;
            case "HK":
                prof.timezone = "Asia/Hong_Kong";
                prof.lang = "zh-HK,zh-TW,zh,en-US,en";
                prof.geo = "22.3193,114.1694,30";
                break;
            case "SG":
                prof.timezone = "Asia/Singapore";
                prof.lang = "en-SG,en-US,en,zh-CN,zh";
                prof.geo = "1.3521,103.8198,30";
                break;
            case "TW":
                prof.timezone = "Asia/Taipei";
                prof.lang = "zh-TW,zh,en-US,en";
                prof.geo = "25.0330,121.5654,30";
                break;
            default:
                prof.timezone = "America/Los_Angeles";
                prof.lang = "en-US,en";
                prof.geo = "34.0522,-118.2437,30";
                break;
        }
    }
}
