using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.Unicode;

namespace KZManager;

public static class Paths
{
    /// <summary>程序所在目录。EXE 放在 KZProfiles 根目录时，这就是根。</summary>
    public static string Root { get; private set; } =
        AppContext.BaseDirectory.TrimEnd('\\');

    public static void SetRoot(string root) => Root = root.TrimEnd('\\');

    public static string ConfigFile => Path.Combine(Root, "manager.json");
    public static string NodesFile => Path.Combine(Root, "nodes.json");
    public static string NodeProxy => Path.Combine(Root, "node-proxy.py");
    public static string SnapshotPy => Path.Combine(Root, "snapshot.py");
    public static string BackupPy => Path.Combine(Root, "backup.py");
    public static string SyncPy => Path.Combine(Root, "sync-data.py");
    public static string SetPref => Path.Combine(Root, "set-pref.py");
    public static string SelfTestHtml => Path.Combine(Root, "指纹自检.html");
    public static string BackupDir => Path.Combine(Root, "_backup");
    public static string SnapDir => Path.Combine(Root, "_snapshots");

    public static string InstanceDir(string name) => Path.Combine(Root, name);
    public static string DataDir(string name) => Path.Combine(InstanceDir(name), "Data");
    public static string ProfileDir(string name) => Path.Combine(DataDir(name), "Default");
    public static string ProfileJson(string name) => Path.Combine(InstanceDir(name), "profile.json");
}

public static class Json
{
    public static readonly JsonSerializerOptions Pretty = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.Create(UnicodeRanges.All),
        DefaultIgnoreCondition = System.Text.Json.Serialization.JsonIgnoreCondition.WhenWritingNull
    };

    public static readonly JsonSerializerOptions Loose = new()
    {
        PropertyNameCaseInsensitive = true,
        ReadCommentHandling = JsonCommentHandling.Skip,
        AllowTrailingCommas = true
    };

    public static T? Read<T>(string path) where T : class
    {
        try
        {
            if (!File.Exists(path)) return null;
            var text = File.ReadAllText(path, Encoding.UTF8).TrimStart('\uFEFF');
            return JsonSerializer.Deserialize<T>(text, Loose);
        }
        catch { return null; }
    }

    public static void Write<T>(string path, T value)
        => File.WriteAllText(path, JsonSerializer.Serialize(value, Pretty),
                              new UTF8Encoding(false));
}

public static class Store
{
    public static AppConfig LoadConfig()
        => Json.Read<AppConfig>(Paths.ConfigFile) ?? new AppConfig();

    public static void SaveConfig(AppConfig cfg) => Json.Write(Paths.ConfigFile, cfg);

    public static Profile LoadProfile(string name)
        => Json.Read<Profile>(Paths.ProfileJson(name)) ?? new Profile { name = name };

    public static void SaveProfile(string name, Profile p)
    {
        p.name = name;
        Directory.CreateDirectory(Paths.InstanceDir(name));
        Json.Write(Paths.ProfileJson(name), p);
    }

    public static List<string> ListInstances()
    {
        var list = new List<string>();
        if (!Directory.Exists(Paths.Root)) return list;
        foreach (var d in Directory.GetDirectories(Paths.Root))
        {
            if (File.Exists(Path.Combine(d, "profile.json")))
                list.Add(Path.GetFileName(d));
        }
        list.Sort(StringComparer.OrdinalIgnoreCase);
        return list;
    }

    /// <summary>按用户自定义顺序返回；不在顺序表里的追加到末尾（字母序）。</summary>
    public static List<string> ListInstancesOrdered(AppConfig cfg)
    {
        var all = ListInstances();
        if (cfg.order is null || cfg.order.Count == 0) return all;

        var known = new HashSet<string>(all, StringComparer.OrdinalIgnoreCase);
        var result = new List<string>();
        foreach (var n in cfg.order)
            if (known.Contains(n)) result.Add(n);

        foreach (var n in all)
            if (!result.Contains(n, StringComparer.OrdinalIgnoreCase)) result.Add(n);
        return result;
    }

    /// <summary>把当前顺序写回 manager.json。</summary>
    public static void SaveOrder(AppConfig cfg, IEnumerable<string> names)
    {
        cfg.order = names.ToList();
        SaveConfig(cfg);
    }

    public static List<NodeEntry> LoadNodes()
        => Json.Read<List<NodeEntry>>(Paths.NodesFile) ?? new List<NodeEntry>();
}

public static class Py
{
    /// <summary>跑 python 脚本，返回 (退出码, 标准输出, 标准错误)。</summary>
    public static (int code, string stdout, string stderr) Run(string script, params string[] args)
    {
        var psi = new ProcessStartInfo("python")
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8
        };
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONUTF8"] = "1";
        psi.ArgumentList.Add(script);
        foreach (var a in args) psi.ArgumentList.Add(a);

        try
        {
            using var p = Process.Start(psi)!;
            var so = p.StandardOutput.ReadToEnd();
            var se = p.StandardError.ReadToEnd();
            p.WaitForExit(120_000);
            return (p.HasExited ? p.ExitCode : -1, so, se);
        }
        catch (Exception e)
        {
            return (-1, "", e.Message);
        }
    }
}

public static class Fingerprint
{
    static readonly Dictionary<string, string> PlatformMap = new()
    {
        ["macos"] = "MacIntel",
        ["windows"] = "Win32",
        ["linux"] = "Linux x86_64",
    };

    /// <summary>剥掉 ;q=0.9 这类权重，Chromium 的 --accept-lang 会自己重新生成。</summary>
    public static string PlainLang(string lang)
        => string.Join(",", lang.Split(',')
            .Select(x => x.Split(';')[0].Trim())
            .Where(x => x.Length > 0));

    public static string WebRtcPolicy(Profile p)
    {
        var w = (p.webrtc ?? "").Trim();
        if (w.Length > 0) return w;
        return (!string.IsNullOrEmpty(p.proxy) || !string.IsNullOrEmpty(p.node))
            ? "strict" : "public";
    }

    public static string BuildArgs(string name, Profile p, string chromeExe)
    {
        var a = new List<string>
        {
            $"--user-data-dir=\"{Paths.DataDir(name)}\""
        };
        if (!string.IsNullOrEmpty(p.name)) a.Add($"--profile-name=\"{p.name}\"");
        if (System.Text.RegularExpressions.Regex.IsMatch(p.seed ?? "", @"^\d+$"))
            a.Add($"--fingerprint={p.seed}");
        if (!string.IsNullOrEmpty(p.platform) && p.platform != "host")
            a.Add($"--fingerprint-platform={p.platform}");
        if (!string.IsNullOrEmpty(p.timezone)) a.Add($"--timezone={p.timezone}");

        if (!string.IsNullOrEmpty(p.lang))
        {
            var plain = PlainLang(p.lang);
            if (plain.Length > 0)
            {
                a.Add($"--accept-lang={plain}");
                a.Add($"--fingerprint-lang={plain}");
            }
        }
        if (!string.IsNullOrEmpty(p.proxy)) a.Add($"--proxy-server={p.proxy}");
        if (System.Text.RegularExpressions.Regex.IsMatch(p.rows ?? "", @"^\d+$"))
            a.Add($"--bookmark-bar-rows={p.rows}");

        if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_cores ?? "", @"^\d+$"))
            a.Add($"--fp-cores={p.fp_cores}");
        if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_memory ?? "", @"^\d+$"))
            a.Add($"--fp-memory={p.fp_memory}");
        if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_screen ?? "", @"^\d+x\d+$"))
            a.Add($"--fp-screen={p.fp_screen}");
        if (System.Text.RegularExpressions.Regex.IsMatch(p.fp_dpr ?? "", @"^[\d.]+$"))
            a.Add($"--fp-dpr={p.fp_dpr}");
        if (!string.IsNullOrEmpty(p.fp_canvas)) a.Add($"--fp-canvas={p.fp_canvas}");
        if (!string.IsNullOrEmpty(p.fp_webgl)) a.Add($"--fp-webgl={p.fp_webgl}");
        if (!string.IsNullOrEmpty(p.fp_audio)) a.Add($"--fp-audio={p.fp_audio}");
        if (!string.IsNullOrEmpty(p.fp_fonts) && p.fp_fonts != "on")
            a.Add($"--fp-fonts={p.fp_fonts}");
        if (!string.IsNullOrEmpty(p.geo)) a.Add($"--fp-geo={p.geo}");

        a.Add("--disable-features=DiceHeaderVersion2");
        a.Add("--no-first-run");
        a.Add("--no-default-browser-check");
        if (!string.IsNullOrEmpty(p.extra)) a.Add(p.extra);
        return string.Join(" ", a);
    }

    public static string SelfTestUrl(string name, Profile p)
    {
        var q = new List<string>();
        void Add(string k, string v)
        {
            if (!string.IsNullOrWhiteSpace(v))
                q.Add($"{k}={Uri.EscapeDataString(v.Trim())}");
        }
        Add("seed", p.seed);
        Add("platform", p.platform);
        Add("timezone", p.timezone);
        Add("lang", p.lang);
        Add("screen", p.fp_screen);
        Add("dpr", p.fp_dpr);
        Add("cores", p.fp_cores);
        Add("memory", p.fp_memory);
        Add("geo", p.geo);
        Add("fonts", p.fp_fonts);
        Add("webrtc", WebRtcPolicy(p));
        var url = "file:///" + Paths.SelfTestHtml.Replace('\\', '/');
        return q.Count > 0 ? url + "?" + string.Join("&", q) : url;
    }
}
