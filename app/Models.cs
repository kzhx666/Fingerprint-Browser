using System.Collections.ObjectModel;
using System.ComponentModel;
using System.IO;
using System.Runtime.CompilerServices;
using System.Windows;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace KZManager;

/// <summary>实例配置，对应 profile.json。字段名与 PowerShell 版完全一致。</summary>
public sealed class Profile
{
    public string name { get; set; } = "";

    /// <summary>实例角色：空=普通实例，master=主控浏览器（不伪装，作数据源）。</summary>
    public string role { get; set; } = "";

    public string seed { get; set; } = "";
    public string platform { get; set; } = "windows";
    public string timezone { get; set; } = "";
    public string lang { get; set; } = "";
    public string proxy { get; set; } = "";
    public string rows { get; set; } = "1";
    public string extra { get; set; } = "";
    public bool enabled { get; set; } = true;

    public string fp_cores { get; set; } = "";
    public string fp_memory { get; set; } = "";
    public string fp_screen { get; set; } = "";
    public string fp_dpr { get; set; } = "";
    public string fp_canvas { get; set; } = "";
    public string fp_webgl { get; set; } = "";
    public string fp_audio { get; set; } = "";
    public string fp_fonts { get; set; } = "";

    public string node { get; set; } = "";
    public string webrtc { get; set; } = "";
    public string geo { get; set; } = "";
    /// <summary>实例声明地区：US/HK/JP/... 或中文「美国」。空 = 按名称+时区自动推断。</summary>
    public string region { get; set; } = "";
    public string tags { get; set; } = "";
    // 实例图标 id（icon-00 .. icon-49），为空则用默认浏览器图标
    public string icon { get; set; } = "";

    /// <summary>旧版本可能有额外字段，读进来保留，写回时原样输出。</summary>
    [JsonExtensionData]
    public Dictionary<string, JsonElement>? extraFields { get; set; }

    public Profile Clone() => new()
    {
        name = name, role = role, seed = seed, platform = platform, timezone = timezone,
        lang = lang, proxy = proxy, rows = rows, extra = extra, enabled = enabled,
        fp_cores = fp_cores, fp_memory = fp_memory, fp_screen = fp_screen,
        fp_dpr = fp_dpr, fp_canvas = fp_canvas, fp_webgl = fp_webgl,
        fp_audio = fp_audio, fp_fonts = fp_fonts, node = node,
        webrtc = webrtc, geo = geo, region = region, tags = tags, icon = icon,
        extraFields = extraFields
    };
}

/// <summary>列表里的一行。</summary>
public sealed class InstanceRow : INotifyPropertyChanged
{
    public string Name { get; set; } = "";
    public string Tags { get; set; } = "";
    public string Platform { get; set; } = "";
    public string Timezone { get; set; } = "";
    public string ProxyDisplay { get; set; } = "";
    public string Seed { get; set; } = "";
    public string Rows { get; set; } = "";
    public string Status { get; set; } = "";
    public string Dir { get; set; } = "";
    public bool Running { get; set; }
    public string SortKey { get; set; } = "";

    /// <summary>是否主控浏览器。</summary>
    public bool IsMaster { get; set; }
    public Visibility MasterBadge => IsMaster ? Visibility.Visible : Visibility.Collapsed;

    public event PropertyChangedEventHandler? PropertyChanged;
    private void Raise([CallerMemberName] string? p = null)
        => PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(p));
}

public sealed class NodeEntry
{
    public string name { get; set; } = "";
    public string link { get; set; } = "";
    public string label { get; set; } = "";
    public string type { get; set; } = "";
    public string source { get; set; } = "";
    public string added { get; set; } = "";

    public override string ToString() => string.IsNullOrEmpty(label) ? link : label;
}

public sealed class AppConfig
{
    public string chromeExe { get; set; } = @"D:\KZBuild\App\chrome.exe";
    public int defaultRows { get; set; } = 1;

    /// <summary>实例的自定义顺序（按名称）。空 = 按名称字母序。</summary>
    public List<string> order { get; set; } = new();

    /// <summary>界面主题：dark / light。</summary>
    public string theme { get; set; } = "dark";

    /// <summary>启动实例前做出口 IP 预检（代理挂了拦住裸奔）。</summary>
    public bool proxyPrecheck { get; set; } = true;
    /// <summary>出口 IP 国家与实例声明地区比对（默认只警告）。</summary>
    public bool regionCheck { get; set; } = true;
    /// <summary>地区不符时拦截启动（false = 只警告）。</summary>
    public bool regionCheckBlock { get; set; } = false;
}
