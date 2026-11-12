using System.Text;
using System;
using System.Collections.Generic;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 便携版一键打包导出引擎 (Portable Package Exporter)。
/// 按方案一：打包定制 Chromium 绿色内核、控制台、便携配置，
/// 并按用户多选精准迁移选中的实例核心数据（保留 Cookie/扩展/书签/密码/指纹，智能过滤垃圾缓存），
/// 输出开箱即用的便携绿色完整包。
/// </summary>
public static class PortablePacker
{
    // 需要过滤的纯临时缓存与体积巨大的非必要目录
    static readonly HashSet<string> SkipSubdirs = new(StringComparer.OrdinalIgnoreCase)
    {
        "Cache", "Code Cache", "GPUCache", "DawnGraphiteCache", "DawnWebGPUCache",
        "Crashpad", "component_crx_cache", "SODA", "SODALanguagePacks",
        "blob_storage", "BrowserMetrics", "DeferredBrowserMetrics"
    };

    // App 内核目录中需要排除的冗余编译物
    static readonly HashSet<string> ExcludeAppFiles = new(StringComparer.OrdinalIgnoreCase)
    {
        "mini_installer.exe", "debug.log"
    };

    public sealed class PackProgress
    {
        public string Status = "";
        public double Percent = 0;
    }

    /// <summary>
    /// 执行一键便携打包。
    /// </summary>
    public static async Task<(bool ok, string zipPath, string error)> ExportPortablePackageAsync(
        IEnumerable<string> selectedInstances,
        string outputDirectory,
        Action<PackProgress>? onProgress = null,
        CancellationToken ct = default)
    {
        var selList = selectedInstances.ToList();
        var outDir = string.IsNullOrWhiteSpace(outputDirectory)
            ? Path.Combine(Paths.Root, "_exports")
            : outputDirectory;

        try
        {
            Directory.CreateDirectory(outDir);
            var stamp = DateTime.Now.ToString("yyyyMMdd-HHmmss");
            var zipPath = Path.Combine(outDir, $"KZBrowser-Portable-Suite-{stamp}.zip");

            // 临时组装目录
            var stageDir = Path.Combine(Path.GetTempPath(), "KZ_Portable_Stage_" + Guid.NewGuid().ToString("N")[..8]);
            Directory.CreateDirectory(stageDir);

            void Report(string msg, double pct) => onProgress?.Invoke(new PackProgress { Status = msg, Percent = pct });

            try
            {
                // 1. 复制定制 Chromium 内核 (App 目录)
                Report("正在复制定制 Chromium 内核...", 10);
                var stageApp = Path.Combine(stageDir, "App");
                Directory.CreateDirectory(stageApp);

                var kernelSrc = Path.Combine(Paths.Root, "App");
                if (!Directory.Exists(kernelSrc))
                {
                    // 回退尝试开发目录 D:\KZBuild\App
                    if (Directory.Exists(@"D:\KZBuild\App")) kernelSrc = @"D:\KZBuild\App";
                }

                if (Directory.Exists(kernelSrc))
                {
                    await Task.Run(() => CopyKernelFiles(kernelSrc, stageApp, ct), ct);
                }
                else
                {
                    return (false, "", $"未找到内核目录: {kernelSrc}");
                }

                // 2. 复制控制台与辅助文件
                Report("正在复制 KZManager 控制台与核心工具...", 30);
                var managerSrc = Path.Combine(Paths.Root, "KZManager.exe");
                if (File.Exists(managerSrc))
                {
                    File.Copy(managerSrc, Path.Combine(stageDir, "KZManager.exe"), true);
                }
                else
                {
                    var selfExe = Environment.ProcessPath;
                    if (File.Exists(selfExe)) File.Copy(selfExe, Path.Combine(stageDir, "KZManager.exe"), true);
                }

                // 复制探针、白名单、说明
                foreach (var f in new[] { "probe_full.html", "指纹自检.html", "cn_bypass_domains.txt", "google-oauth.json", "nodes.json" })
                {
                    var p = Path.Combine(Paths.Root, f);
                    if (File.Exists(p)) File.Copy(p, Path.Combine(stageDir, f), true);
                }

                // 复制常用辅助脚本
                foreach (var s in new[] { "node-proxy.py", "snapshot.py", "backup.py", "sync-data.py" })
                {
                    var p = Path.Combine(Paths.Root, s);
                    if (File.Exists(p)) File.Copy(p, Path.Combine(stageDir, s), true);
                }

                // 生成自适应 manager.json (相对路径优先)
                var cfg = Store.LoadConfig();
                cfg.chromeExe = @"App\chrome.exe";
                File.WriteAllText(Path.Combine(stageDir, "manager.json"),
                    JsonSerializer.Serialize(cfg, new JsonSerializerOptions { WriteIndented = true }));

                // 写入使用说明
                var readme = @"========================================================================
  KZ 指纹浏览器便携绿色套件 (Portable Suite)
========================================================================

【使用说明】
1. 本套件已完整包含您的定制 Chromium 内核与选中的实例环境数据。
2. 解压到新电脑的任意目录或移动硬盘，双击「KZManager.exe」直接运行。
3. 绿色免安装，环境内的谷歌账号、插件、书签与指纹全部保留，无需重新登录。

技术支持与源码: https://github.com/kzhx666/Fingerprint-Browser
========================================================================
";
                File.WriteAllText(Path.Combine(stageDir, "使用说明.txt"), readme, Encoding.GetEncoding("gbk"));

                // 3. 复制选中的实例数据 (方案一：智能过滤垃圾缓存)
                double instBasePct = 35.0;
                double instStep = selList.Count > 0 ? (45.0 / selList.Count) : 0;

                for (int i = 0; i < selList.Count; i++)
                {
                    var instName = selList[i];
                    Report($"正在打包实例 ({i + 1}/{selList.Count}): {instName}...", instBasePct + (i * instStep));

                    var srcInstDir = Paths.InstanceDir(instName);
                    if (!Directory.Exists(srcInstDir)) continue;

                    var dstInstDir = Path.Combine(stageDir, instName);
                    Directory.CreateDirectory(dstInstDir);

                    // 复制 profile.json
                    var pj = Paths.ProfileJson(instName);
                    if (File.Exists(pj)) File.Copy(pj, Path.Combine(dstInstDir, "profile.json"), true);

                    // 复制 Data 核心数据 (过滤纯缓存)
                    var srcDataDir = Paths.DataDir(instName);
                    if (Directory.Exists(srcDataDir))
                    {
                        var dstDataDir = Path.Combine(dstInstDir, "Data");
                        await Task.Run(() => CopyLeanData(srcDataDir, dstDataDir, ct), ct);
                    }
                }

                // 4. 压缩为 ZIP
                Report("正在压缩生成便携套件包 (可能需要数十秒)...", 85);
                await Task.Run(() =>
                {
                    if (File.Exists(zipPath)) File.Delete(zipPath);
                    ZipFile.CreateFromDirectory(stageDir, zipPath, CompressionLevel.Optimal, false);
                }, ct);

                Report("便携套件打包完成！", 100);
                return (true, zipPath, "");
            }
            finally
            {
                // 清理临时组装目录
                try { Directory.Delete(stageDir, true); } catch { }
            }
        }
        catch (Exception ex)
        {
            return (false, "", ex.Message);
        }
    }

    static void CopyKernelFiles(string srcDir, string dstDir, CancellationToken ct)
    {
        Directory.CreateDirectory(dstDir);
        var di = new DirectoryInfo(srcDir);
        foreach (var file in di.GetFiles())
        {
            if (ct.IsCancellationRequested) break;
            var ext = file.Extension.ToLowerInvariant();
            if (ExcludeAppFiles.Contains(file.Name) || ext is ".bak" or ".tmp") continue;
            file.CopyTo(Path.Combine(dstDir, file.Name), true);
        }
        foreach (var dir in di.GetDirectories())
        {
            if (ct.IsCancellationRequested) break;
            CopyKernelFiles(dir.FullName, Path.Combine(dstDir, dir.Name), ct);
        }
    }

    static void CopyLeanData(string srcDir, string dstDir, CancellationToken ct)
    {
        Directory.CreateDirectory(dstDir);
        var di = new DirectoryInfo(srcDir);

        foreach (var file in di.GetFiles())
        {
            if (ct.IsCancellationRequested) break;
            var ext = file.Extension.ToLowerInvariant();
            if (ext is ".tmp" or ".bak") continue;
            try { file.CopyTo(Path.Combine(dstDir, file.Name), true); } catch { }
        }

        foreach (var dir in di.GetDirectories())
        {
            if (ct.IsCancellationRequested) break;
            // 跳过纯缓存与超大临时目录
            if (SkipSubdirs.Contains(dir.Name)) continue;

            // 特殊处理 File System 目录下的超大临时 Blob 缓存
            if (dir.Name.Equals("File System", StringComparison.OrdinalIgnoreCase))
            {
                // 仅复制 Origins 元数据，跳过 001 等超大视频/离线分块
                var dstFs = Path.Combine(dstDir, dir.Name);
                Directory.CreateDirectory(dstFs);
                var origDir = Path.Combine(dir.FullName, "Origins");
                if (Directory.Exists(origDir))
                {
                    CopyLeanData(origDir, Path.Combine(dstFs, "Origins"), ct);
                }
                continue;
            }

            CopyLeanData(dir.FullName, Path.Combine(dstDir, dir.Name), ct);
        }
    }
}
