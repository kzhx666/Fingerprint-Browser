using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;

namespace KZManager;

/// <summary>
/// 给运行中的 chrome 窗口设置实例专属图标（HICON）。
///
/// 任务栏显示的是窗口 HICON，不是快捷方式 .lnk 的 IconLocation。
/// 固定项图标管「没打开时」，这里管「打开后」——两者都按实例图标才不会搞混。
/// </summary>
public static class WindowIcon
{
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool EnumWindows(EnumProc cb, IntPtr l);

    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);

    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr h);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr SendMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern IntPtr LoadImage(IntPtr hInst, string name, uint type,
                                   int cx, int cy, uint fuLoad);

    [DllImport("user32.dll")]
    static extern bool DestroyIcon(IntPtr h);

    [DllImport("shell32.dll")]
    static extern IntPtr ExtractIcon(IntPtr hInst, string file, int nIconIndex);

    delegate bool EnumProc(IntPtr h, IntPtr l);

    const uint WM_SETICON = 0x0080;
    const uint IMAGE_ICON = 1;
    const uint LR_LOADFROMFILE = 0x0010;
    static readonly IntPtr ICON_SMALL = IntPtr.Zero;
    static readonly IntPtr ICON_BIG = new(1);

    // 缓存：icon 路径 -> HICON（避免每 2 秒重复 LoadImage 泄漏句柄）
    static readonly Dictionary<string, IntPtr> _cache = new();

    static IntPtr HiconFromIco(string icoPath)
    {
        if (_cache.TryGetValue(icoPath, out var h) && h != IntPtr.Zero) return h;
        // ExtractIcon 能直接吃 .ico 文件
        h = ExtractIcon(IntPtr.Zero, icoPath, 0);
        if (h == IntPtr.Zero || h == new IntPtr(1))
        {
            // 回退：LoadImage
            h = LoadImage(IntPtr.Zero, icoPath, IMAGE_ICON, 32, 32, LR_LOADFROMFILE);
        }
        if (h != IntPtr.Zero && h != new IntPtr(1)) _cache[icoPath] = h;
        return h;
    }

    static List<IntPtr> WindowsOf(uint pid)
    {
        var list = new List<IntPtr>();
        try
        {
            EnumWindows((h, _) =>
            {
                GetWindowThreadProcessId(h, out var p);
                if (p == pid && IsWindowVisible(h)) list.Add(h);
                return true;
            }, IntPtr.Zero);
        }
        catch { }
        return list;
    }

    /// <summary>给指定实例的所有窗口设置它的图标。返回设置的窗口数。</summary>
    public static int ApplyOnce(string instanceName)
    {
        var p = Store.LoadProfile(instanceName);
        var ico = Paths.IconPath(p.icon ?? "");
        if (ico.Length == 0) return 0;      // 该实例用默认图标，不动

        var hIcon = HiconFromIco(ico);
        if (hIcon == IntPtr.Zero || hIcon == new IntPtr(1)) return 0;

        var dataDir = Paths.DataDir(instanceName).TrimEnd('\\');
        int n = 0;
        try
        {
            foreach (var kv in ProcessQuery.ChromeDataDirs())
            {
                if (!string.Equals(kv.Key.TrimEnd('\\'), dataDir,
                                   StringComparison.OrdinalIgnoreCase))
                    continue;
                foreach (var h in WindowsOf((uint)kv.Value))
                {
                    SendMessage(h, WM_SETICON, ICON_SMALL, hIcon);
                    SendMessage(h, WM_SETICON, ICON_BIG, hIcon);
                    n++;
                }
            }
        }
        catch { }
        return n;
    }

    /// <summary>后台循环：每 2 秒给所有配置过图标的实例的窗口补设。</summary>
    public static void ApplyLoop(System.Threading.CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                foreach (var name in Store.ListInstances())
                {
                    var p = Store.LoadProfile(name);
                    if (!string.IsNullOrEmpty(p.icon)) ApplyOnce(name);
                }
            }
            catch { }
            try { token.WaitHandle.WaitOne(2000); } catch { break; }
        }
    }
}
