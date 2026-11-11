using System;
using System.Collections.Generic;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 22. 批量环境操作引擎 (Batch Engine)。
/// 支持数十/数百个实例的错峰平稳批量启动、批量停止、批量分配代理与批量打标签。
/// </summary>
public static class BatchEngine
{
    public sealed class BatchResult
    {
        public int Total;
        public int Succeeded;
        public int Failed;
        public List<string> Details = new();
    }

    /// <summary>错峰批量启动实例（避免瞬时并发拉爆 CPU/内存）。</summary>
    public static async Task<BatchResult> BatchLaunchAsync(
        IEnumerable<string> names,
        AppConfig cfg,
        int delaySeconds = 2,
        Action<string>? progress = null,
        CancellationToken ct = default)
    {
        var res = new BatchResult { Total = names.Count() };
        foreach (var n in names)
        {
            if (ct.IsCancellationRequested) break;
            progress?.Invoke($"正在启动: {n} ({res.Succeeded + res.Failed + 1}/{res.Total})…");
            var lr = Launcher.Run(n, cfg, progress);
            if (lr.Ok)
            {
                res.Succeeded++;
                res.Details.Add($"{n}: 启动成功 (PID {lr.Pid})");
            }
            else
            {
                res.Failed++;
                res.Details.Add($"{n}: 启动失败 - {lr.Message}");
            }

            if (delaySeconds > 0)
            {
                await Task.Delay(delaySeconds * 1000, ct);
            }
        }
        return res;
    }

    /// <summary>批量停止实例进程。</summary>
    public static BatchResult BatchStop(IEnumerable<string> names)
    {
        var res = new BatchResult { Total = names.Count() };
        foreach (var n in names)
        {
            var killed = ProcessQuery.KillChromeByDataDir(Paths.DataDir(n).TrimEnd('\\'));
            res.Succeeded++;
            res.Details.Add($"{n}: 终止 {killed} 个关联进程");
        }
        return res;
    }

    /// <summary>批量修改配置（如修改时区、语言、平台、UA画像等）。</summary>
    public static int BatchUpdate(IEnumerable<string> names, Action<Profile> mutator)
    {
        int count = 0;
        foreach (var n in names)
        {
            var p = Store.LoadProfile(n);
            mutator(p);
            Store.SaveProfile(n, p);
            count++;
        }
        return count;
    }
}
