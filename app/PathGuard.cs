using System;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

namespace KZManager;

/// <summary>
/// P0-3: 统一实例名 / 快照名 / 目标路径校验。
/// 拒绝绝对路径、..、路径分隔符、Windows 保留名、控制字符；
/// 允许正常中文名称。
/// </summary>
public static class PathGuard
{
    static readonly string[] Reserved = {
        "CON", "PRN", "AUX", "NUL",
        "COM1","COM2","COM3","COM4","COM5","COM6","COM7","COM8","COM9",
        "LPT1","LPT2","LPT3","LPT4","LPT5","LPT6","LPT7","LPT8","LPT9"
    };

    /// <summary>校验一个"名称"（实例名/快照名/配置集名）。返回 null=合法，否则错误信息。</summary>
    public static string? ValidateName(string? name)
    {
        if (string.IsNullOrWhiteSpace(name)) return "名称为空";
        if (name.Length > 120) return "名称过长";
        // 控制字符
        foreach (var c in name) if (char.IsControl(c)) return "名称含控制字符";
        // 路径分隔符 / 绝对路径 / 父目录
        if (name.IndexOfAny(new[] { '/', '\\', ':' }) >= 0) return "名称含路径分隔符或驱动器号";
        if (name.Contains("..")) return "名称含 ..";
        if (Path.IsPathRooted(name)) return "名称是绝对路径";
        // Windows 保留名（含扩展名形式 CON.txt）
        var stem = name.Split('.')[0].Trim().ToUpperInvariant();
        if (Reserved.Contains(stem)) return "名称是 Windows 保留名";
        // 结尾空格/点（Windows 会截断）
        if (name != name.TrimEnd(' ', '.')) return "名称以空格或点结尾";
        // 非法文件名字符（除已单列的分隔符/冒号）
        foreach (var c in Path.GetInvalidFileNameChars())
            if (name.IndexOf(c) >= 0) return "名称含非法字符";
        return null;
    }

    /// <summary>校验目标路径必须落在 root 下（防越界）。</summary>
    public static bool IsUnder(string root, string candidate)
    {
        try
        {
            var r = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar)
                    + Path.DirectorySeparatorChar;
            var c = Path.GetFullPath(candidate);
            return c.StartsWith(r, StringComparison.OrdinalIgnoreCase) ||
                   string.Equals(c, Path.GetFullPath(root), StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    /// <summary>由已校验的名称拼出实例目录（再次确认落界）。</summary>
    public static bool TryInstanceDir(string name, out string dir, out string error)
    {
        dir = ""; error = "";
        var e = ValidateName(name);
        if (e is not null) { error = e; return false; }
        dir = Path.Combine(Paths.Root, name);
        if (!IsUnder(Paths.Root, dir)) { error = "越界路径"; return false; }
        return true;
    }
}
