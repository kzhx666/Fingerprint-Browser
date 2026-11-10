using System.IO;

namespace KZManager;

/// <summary>给实例生成 .lnk 快捷方式，固定到任务栏后能打开正确的 profile。</summary>
public static class Shortcut
{
    /// <summary>
    /// 创建快捷方式。
    /// args 必须包含 --user-data-dir，否则点开会用 Chromium 默认 profile。
    /// </summary>
    public static bool Create(string lnkPath, string targetExe, string arguments,
                              string workingDir, string description, out string error,
                              string iconPath = "")
    {
        error = "";
        try
        {
            var dir = Path.GetDirectoryName(lnkPath);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);

            var type = Type.GetTypeFromProgID("WScript.Shell");
            if (type is null) { error = "系统没有 WScript.Shell"; return false; }

            dynamic shell = Activator.CreateInstance(type)!;
            dynamic sc = shell.CreateShortcut(lnkPath);
            sc.TargetPath = targetExe;
            sc.Arguments = arguments;
            sc.WorkingDirectory = workingDir;
            // 自定义实例图标优先；否则用目标 exe 的默认图标
            sc.IconLocation = !string.IsNullOrEmpty(iconPath) && File.Exists(iconPath)
                ? iconPath + ",0"
                : targetExe + ",0";
            sc.Description = description;
            sc.Save();
            return File.Exists(lnkPath);
        }
        catch (Exception ex)
        {
            error = ex.Message;
            return false;
        }
    }

    // ---------------------------------------------------------------- .lnk 的 AUMID

    // Windows 右键固定窗口时会把窗口的 AppUserModelID 写进 .lnk。
    // 我们改写 .lnk 目标后必须把该属性写回去，否则所有固定项又会合并。

    [System.Runtime.InteropServices.ComImport,
     System.Runtime.InteropServices.Guid("00021401-0000-0000-C000-000000000046")]
    class ShellLink { }

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential, Pack = 4)]
    struct GUID
    {
        public uint a;
        public ushort b, c;
        [System.Runtime.InteropServices.MarshalAs(System.Runtime.InteropServices.UnmanagedType.ByValArray, SizeConst = 8)]
        public byte[] d;
    }

    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential, Pack = 4)]
    struct PK { public GUID f; public uint pid; }

    // PROPVARIANT 的 VT_LPWSTR 形式：vt + 3 个保留字 + 一个指针
    [System.Runtime.InteropServices.StructLayout(System.Runtime.InteropServices.LayoutKind.Sequential)]
    struct PV
    {
        public ushort vt;
        public ushort r1, r2, r3;
        public IntPtr p;
    }

    [System.Runtime.InteropServices.ComImport,
     System.Runtime.InteropServices.Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"),
     System.Runtime.InteropServices.InterfaceType(System.Runtime.InteropServices.ComInterfaceType.InterfaceIsIUnknown)]
    interface IPS
    {
        void GetCount(out uint c);
        void GetAt(uint i, out PK k);
        void GetValue(ref PK k, out PV pv);
        void SetValue(ref PK k, ref PV pv);
        void Commit();
    }

    [System.Runtime.InteropServices.DllImport("ole32.dll")]
    static extern int PropVariantClear(ref PV pv);

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

    /// <summary>
    /// 打开 .lnk 的 IPropertyStore。
    /// 返回 (store, persistFile)：写操作需要同一个已 Load 的对象来 Save。
    /// </summary>
    static (IPS store, System.Runtime.InteropServices.ComTypes.IPersistFile pf)?
        OpenPropertyStore(string lnkPath, bool readWrite = false)
    {
        try
        {
            object link = new ShellLink();
            var pf = (System.Runtime.InteropServices.ComTypes.IPersistFile)link;
            // 写必须用 STGM_READWRITE(2)，用 0(只读) 会在 SetValue 抛
            // STG_E_ACCESSDENIED (0x80030005)。
            pf.Load(lnkPath, readWrite ? 2 : 0);

            var unk = System.Runtime.InteropServices.Marshal.GetIUnknownForObject(link);
            var iidGuid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
            int hr = System.Runtime.InteropServices.Marshal.QueryInterface(
                unk, ref iidGuid, out var pps);
            IPS? store = (hr == 0 && pps != IntPtr.Zero)
                ? (IPS)System.Runtime.InteropServices.Marshal.GetObjectForIUnknown(pps)
                : null;
            if (unk != IntPtr.Zero) System.Runtime.InteropServices.Marshal.Release(unk);
            if (pps != IntPtr.Zero) System.Runtime.InteropServices.Marshal.Release(pps);
            if (store is null) return null;
            return (store, pf);
        }
        catch { return null; }
    }

    /// <summary>读 .lnk 里保存的 AppUserModelID。</summary>
    public static string GetLnkAumid(string lnkPath)
    {
        try
        {
            var opened = OpenPropertyStore(lnkPath);
            if (opened is null) return "";
            var k = AppIdKey();
            opened.Value.store.GetValue(ref k, out var pv);
            if (pv.vt != 31) return "";
            var s = System.Runtime.InteropServices.Marshal.PtrToStringUni(pv.p) ?? "";
            PropVariantClear(ref pv);
            return s;
        }
        catch { return ""; }
    }

    /// <summary>把 AppUserModelID 写进 .lnk 并保存。</summary>
    public static bool SetLnkAumid(string lnkPath, string value)
    {
        IntPtr mem = IntPtr.Zero;
        try
        {
            var opened = OpenPropertyStore(lnkPath, readWrite: true);
            if (opened is null) return false;
            mem = System.Runtime.InteropServices.Marshal.StringToCoTaskMemUni(value);
            var pv = new PV { vt = 31, p = mem };
            var k = AppIdKey();
            opened.Value.store.SetValue(ref k, ref pv);
            opened.Value.store.Commit();
            // Commit 只写内存，必须 Save 才落盘；且要用同一个已 Load 的对象
            opened.Value.pf.Save(lnkPath, true);
            return true;
        }
        catch { return false; }
        finally
        {
            if (mem != IntPtr.Zero) System.Runtime.InteropServices.Marshal.FreeCoTaskMem(mem);
        }
    }

    public static string DesktopDir =>
        Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);

    public static string StartMenuDir =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                     @"Microsoft\Windows\Start Menu\Programs");

    /// <summary>把所有快捷方式里指向 oldName 的 --launch / --user-data-dir 改成 newName。</summary>
    public static void RenameInAllShortcuts(string oldName, string newName)
    {
        var dirs = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Microsoft\Windows\Start Menu\Programs"),
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
            Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory),
        };
        var wshType = Type.GetTypeFromProgID("WScript.Shell");
        if (wshType is null) return;
        dynamic wsh = Activator.CreateInstance(wshType)!;
        foreach (var d in dirs)
        {
            if (!Directory.Exists(d)) continue;
            foreach (var lnk in Directory.GetFiles(d, "*.lnk", SearchOption.AllDirectories))
            {
                try
                {
                    dynamic sc = wsh.CreateShortcut(lnk);
                    string a = sc.Arguments ?? "";
                    if (!a.Contains(oldName)) continue;
                    a = a.Replace("--launch \"" + oldName + "\"", "--launch \"" + newName + "\"");
                    a = a.Replace("--profile-name=\"" + oldName + "\"", "--profile-name=\"" + newName + "\"");
                    a = a.Replace("KZProfiles\\" + oldName + "\\", "KZProfiles\\" + newName + "\\");
                    sc.Arguments = a;
                    sc.Save();
                }
                catch { }
            }
        }
    }

}
