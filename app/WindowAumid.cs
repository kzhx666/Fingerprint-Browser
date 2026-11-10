using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;

namespace KZManager;

/// <summary>
/// 给每个浏览器窗口设置独立的 AppUserModelID，让 Windows 任务栏按实例分组。
///
/// 背景：内核里的 Chrome++ (version.dll) 把所有实例的 AUMID 写死成
/// ChromePlusNext.&lt;固定哈希&gt;，导致所有窗口合并成一个任务栏按钮，
/// 右键固定也没法按实例区分。
///
/// 但 Windows 分组看的是窗口属性 System.AppUserModel.ID
/// (PKEY_AppUserModel_ID)，这个属性能由外部进程通过 IPropertyStore 改写，
/// 且 Chromium 不会覆盖回去。实测改写后 18 秒仍保持，任务栏成功分成两个按钮。
///
/// 用法：管理器启动实例后调用 ApplyAsync(instanceName)，它会轮询找到该实例
/// 的窗口并写入 "KZ.Browser.&lt;slug&gt;"，slug 由实例名哈希得到，稳定不变。
/// </summary>
public static class WindowAumid
{
    // ---------------------------------------------------------------- P/Invoke

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct GUID
    {
        public uint a;
        public ushort b, c;
        [MarshalAs(UnmanagedType.ByValArray, SizeConst = 8)]
        public byte[] d;
    }

    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PK
    {
        public GUID f;
        public uint pid;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct PV
    {
        public ushort vt;
        public ushort r1, r2, r3;
        public IntPtr p;
        public IntPtr p2;
    }

    [DllImport("shell32.dll", PreserveSig = false)]
    static extern void SHGetPropertyStoreForWindow(IntPtr hwnd, ref GUID iid, out IntPtr ppv);

    [DllImport("ole32.dll")]
    static extern int PropVariantClear(ref PV pv);

    [DllImport("user32.dll")]
    static extern bool EnumWindows(EnumProc cb, IntPtr l);

    [DllImport("user32.dll")]
    static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);

    [DllImport("user32.dll")]
    static extern bool IsWindowVisible(IntPtr h);

    delegate bool EnumProc(IntPtr h, IntPtr l);

    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPS
    {
        void GetCount(out uint c);
        void GetAt(uint i, out PK k);
        void GetValue(ref PK k, out PV pv);
        void SetValue(ref PK k, ref PV pv);
        void Commit();
    }

    static GUID G(string s)
    {
        var g = new GUID { d = new byte[8] };
        byte[] b = new Guid(s).ToByteArray();
        g.a = BitConverter.ToUInt32(b, 0);
        g.b = BitConverter.ToUInt16(b, 4);
        g.c = BitConverter.ToUInt16(b, 6);
        Array.Copy(b, 8, g.d, 0, 8);
        return g;
    }

    static PK AppIdKey()
    {
        var k = new PK();
        k.f = G("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");   // PKEY_AppUserModel_ID
        k.pid = 5;
        return k;
    }

    static IPS? PropertyStoreOf(IntPtr hwnd)
    {
        try
        {
            var iid = G("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
            SHGetPropertyStoreForWindow(hwnd, ref iid, out var pps);
            if (pps == IntPtr.Zero) return null;
            return (IPS)Marshal.GetObjectForIUnknown(pps);
        }
        catch { return null; }
    }

    /// <summary>读窗口当前的 AUMID。</summary>
    public static string Get(IntPtr hwnd)
    {
        try
        {
            var st = PropertyStoreOf(hwnd);
            if (st is null) return "";
            var k = AppIdKey();
            st.GetValue(ref k, out var pv);
            if (pv.vt != 31) return "";              // VT_LPWSTR
            var s = Marshal.PtrToStringUni(pv.p) ?? "";
            PropVariantClear(ref pv);
            return s;
        }
        catch { return ""; }
    }

    /// <summary>给窗口写 AUMID。VT_LPWSTR + CoTaskMem，shell 会自己复制。</summary>
    public static bool Set(IntPtr hwnd, string value)
    {
        IntPtr mem = IntPtr.Zero;
        try
        {
            var st = PropertyStoreOf(hwnd);
            if (st is null) return false;
            mem = Marshal.StringToCoTaskMemUni(value);
            var pv = new PV { vt = 31, p = mem };
            var k = AppIdKey();
            st.SetValue(ref k, ref pv);
            st.Commit();
            return true;
        }
        catch { return false; }
        finally { if (mem != IntPtr.Zero) Marshal.FreeCoTaskMem(mem); }
    }

    // ---------------------------------------------------------------- 实例 -> AUMID

    /// <summary>
    /// 由实例名生成稳定、合法的 AUMID。
    /// 格式必须是 Company.Product.SubProduct.VersionInformation 风格，
    /// Windows 要求至少两段带点；这里用 4 段。
    /// </summary>
    public static string IdFor(string instanceName)
    {
        // FNV-1a 64 位哈希，稳定且不需要引入额外依赖
        ulong h = 14695981039346656037UL;
        foreach (var ch in instanceName)
        {
            h ^= ch;
            h *= 1099511628211UL;
        }
        return $"KZ.Browser.Profile.{h:X12}";
    }

    /// <summary>列出属于指定 PID 的所有可见顶层窗口。</summary>
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

    /// <summary>
    /// 找到该实例的 chrome 主进程，给它的所有可见窗口写入独立 AUMID。
    /// 新窗口（用户按 Ctrl+N）出现时不会自动带上，所以由 ApplyLoop 持续兜底。
    /// </summary>
    public static int ApplyOnce(string instanceName)
    {
        var id = IdFor(instanceName);
        var dataDir = Paths.DataDir(instanceName).TrimEnd('\\');
        int applied = 0;

        try
        {
            // ChromeDataDirs() 返回 user-data-dir -> 主进程 PID。
            // 子进程（--type=renderer 等）没有自己的顶层窗口，不用管。
            foreach (var kv in ProcessQuery.ChromeDataDirs())
            {
                if (!string.Equals(kv.Key.TrimEnd('\\'), dataDir,
                                   StringComparison.OrdinalIgnoreCase))
                    continue;

                foreach (var h in WindowsOf((uint)kv.Value))
                {
                    if (Get(h) == id) continue;         // 已经是对的，跳过
                    if (Set(h, id)) applied++;
                }
            }
        }
        catch { }
        return applied;
    }

    /// <summary>
    /// 后台持续兜底：每 2 秒扫一遍所有实例，给新出现的窗口补 AUMID。
    /// 进程退出时由 CancellationToken 停止。
    /// </summary>
    public static void ApplyLoop(System.Threading.CancellationToken token)
    {
        while (!token.IsCancellationRequested)
        {
            try
            {
                foreach (var name in Store.ListInstances())
                    ApplyOnce(name);
            }
            catch { }
            try { token.WaitHandle.WaitOne(2000); } catch { break; }
        }
    }
}
