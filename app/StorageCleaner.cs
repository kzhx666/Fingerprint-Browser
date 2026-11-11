using System;
using System.Collections.Generic;
using System.IO;

namespace KZManager;

/// <summary>
/// 17. 环境存储细粒度清理。
/// 统一管理各实例的缓存、IndexedDB、ServiceWorker、临时文件与下载历史，
/// 严格保留 Cookies、Bookmarks、Login Data 等高价值登录凭证。
/// </summary>
public static class StorageCleaner
{
    public sealed class CleanReport
    {
        public string InstanceName = "";
        public long BytesFreed;
        public int FilesDeleted;
        public List<string> CleanedTargets = new();
        public List<string> Errors = new();
    }

    public static CleanReport Clean(string instanceName, bool cleanCache = true, bool cleanIndexedDb = false, bool cleanServiceWorker = false)
    {
        var rep = new CleanReport { InstanceName = instanceName };
        var profDir = Paths.ProfileDir(instanceName);
        if (!Directory.Exists(profDir)) return rep;

        var targets = new List<string>();
        if (cleanCache)
        {
            targets.Add(Path.Combine(profDir, "Cache"));
            targets.Add(Path.Combine(profDir, "Code Cache"));
            targets.Add(Path.Combine(profDir, "GPUCache"));
            targets.Add(Path.Combine(profDir, "blob_storage"));
            targets.Add(Path.Combine(profDir, "dawn"));
        }
        if (cleanIndexedDb)
        {
            targets.Add(Path.Combine(profDir, "IndexedDB"));
        }
        if (cleanServiceWorker)
        {
            targets.Add(Path.Combine(profDir, "Service Worker"));
        }

        foreach (var t in targets)
        {
            if (Directory.Exists(t))
            {
                try
                {
                    var di = new DirectoryInfo(t);
                    foreach (var f in di.EnumerateFiles("*", SearchOption.AllDirectories))
                    {
                        rep.BytesFreed += f.Length;
                        rep.FilesDeleted++;
                    }
                    Directory.Delete(t, true);
                    rep.CleanedTargets.Add(Path.GetFileName(t));
                }
                catch (Exception e)
                {
                    rep.Errors.Add($"{Path.GetFileName(t)}: {e.Message}");
                }
            }
        }
        return rep;
    }
}
