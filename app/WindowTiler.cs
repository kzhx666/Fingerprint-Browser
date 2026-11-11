using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows;

namespace KZManager;

/// <summary>
/// 20. 多屏网格窗口平铺器 (Window Grid Tiler)。
/// 自动枚举当前所有由 KZManager 管理的 Chrome 实例窗口，
/// 根据主屏幕或工作区分辨率按 2x2、3x2、3x3 等网格无缝平铺对齐。
/// </summary>
public static class WindowTiler
{
    [DllImport("user32.dll", SetLastError = true)]
    static extern bool MoveWindow(IntPtr hWnd, int X, int Y, int nWidth, int nHeight, bool bRepaint);

    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);

    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr hWnd);

    [DllImport("user32.dll", SetLastError = true)]
    static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

    [DllImport("user32.dll")]
    static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

    delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);

    const int SW_RESTORE = 9;

    public static int TileWindows(int preferredCols = 0)
    {
        // 1. 获取所有受管 Chrome 主进程 PID
        var dataDirs = ProcessQuery.ChromeDataDirs();
        var managedPids = new HashSet<uint>();
        foreach (var p in Process.GetProcessesByName("chrome"))
        {
            try
            {
                if (dataDirs.ContainsKey(Paths.DataDir(p.ProcessName)))
                    managedPids.Add((uint)p.Id);
            }
            catch { }
        }

        // 如果没有匹配到特定 dataDir，收集所有顶层 chrome 窗口
        var chromeWindows = new List<IntPtr>();
        EnumWindows((hWnd, lParam) =>
        {
            if (!IsWindowVisible(hWnd)) return true;
            GetWindowThreadProcessId(hWnd, out var pid);
            try
            {
                var proc = Process.GetProcessById((int)pid);
                if (proc.ProcessName.Equals("chrome", StringComparison.OrdinalIgnoreCase) &&
                    proc.MainWindowHandle == hWnd)
                {
                    chromeWindows.Add(hWnd);
                }
            }
            catch { }
            return true;
        }, IntPtr.Zero);

        int count = chromeWindows.Count;
        if (count == 0) return 0;

        // 2. 计算网格行列
        int cols = preferredCols;
        if (cols <= 0)
        {
            cols = count switch
            {
                <= 2 => count,
                <= 4 => 2,
                <= 6 => 3,
                <= 9 => 3,
                <= 12 => 4,
                _ => 4
            };
        }
        int rows = (int)Math.Ceiling((double)count / cols);

        // 3. 获取工作区可用宽高 (避开任务栏)
        var screenW = (int)SystemParameters.WorkArea.Width;
        var screenH = (int)SystemParameters.WorkArea.Height;

        int winW = screenW / cols;
        int winH = screenH / rows;

        // 4. 平铺每个窗口
        for (int i = 0; i < count; i++)
        {
            int r = i / cols;
            int c = i % cols;
            int x = c * winW;
            int y = r * winH;

            var hWnd = chromeWindows[i];
            ShowWindow(hWnd, SW_RESTORE);
            MoveWindow(hWnd, x, y, winW, winH, true);
        }

        return count;
    }
}
