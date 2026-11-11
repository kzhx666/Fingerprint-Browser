using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;

namespace KZManager;

/// <summary>
/// 14. 扩展插件管理中心。
/// 统一管理本地 _extensions 目录下的扩展、模板配置与批量加载。
/// </summary>
public static class ExtensionManager
{
    public sealed class ExtensionItem
    {
        public string Id { get; set; } = "";
        public string Name { get; set; } = "";
        public string Version { get; set; } = "";
        public string Description { get; set; } = "";
        public string Path { get; set; } = "";
        public bool Enabled { get; set; } = true;
    }

    public static string ExtensionsDir => System.IO.Path.Combine(Paths.Root, "_extensions");

    public static List<ExtensionItem> ListAvailable()
    {
        var list = new List<ExtensionItem>();
        if (!Directory.Exists(ExtensionsDir)) return list;

        foreach (var dir in Directory.GetDirectories(ExtensionsDir))
        {
            var manifest = System.IO.Path.Combine(dir, "manifest.json");
            if (!File.Exists(manifest)) continue;

            try
            {
                using var doc = JsonDocument.Parse(File.ReadAllText(manifest));
                var root = doc.RootElement;
                var item = new ExtensionItem
                {
                    Id = System.IO.Path.GetFileName(dir),
                    Name = root.TryGetProperty("name", out var n) ? n.GetString() ?? System.IO.Path.GetFileName(dir) : System.IO.Path.GetFileName(dir),
                    Version = root.TryGetProperty("version", out var v) ? v.GetString() ?? "1.0" : "1.0",
                    Description = root.TryGetProperty("description", out var d) ? d.GetString() ?? "" : "",
                    Path = dir
                };
                list.Add(item);
            }
            catch { }
        }
        return list;
    }

    /// <summary>根据实例绑定的 extensionIds，拼出 --load-extension 参数。</summary>
    public static string BuildLoadArg(List<string>? extensionIds)
    {
        if (extensionIds is null || extensionIds.Count == 0) return "";
        var available = ListAvailable().ToDictionary(x => x.Id, x => x.Path, StringComparer.OrdinalIgnoreCase);
        var paths = new List<string>();
        foreach (var id in extensionIds)
        {
            if (available.TryGetValue(id, out var p) && Directory.Exists(p))
                paths.Add(p);
            else if (Directory.Exists(id))
                paths.Add(id);
        }
        if (paths.Count == 0) return "";
        return $"--load-extension=\"{string.Join(",", paths)}\"";
    }
}
