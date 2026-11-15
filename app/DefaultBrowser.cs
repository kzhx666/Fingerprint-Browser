using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using Microsoft.Win32;

namespace KZManager;

/// <summary>
/// 把 KZManager 注册成「默认浏览器路由器」。
///
/// 为什么不是直接把 chrome.exe 设成默认：所有实例共用同一个 chrome.exe，
/// Windows 无法区分实例，只能落到某一个固定 profile 上。
///
/// 这里注册的是 KZManager 自己作为 http/https 处理程序：系统把 URL 交给
/// KZManager.exe --route-url "&lt;url&gt;"，再由它判断前台实例并用显式
/// --user-data-dir 转发。全程不修改 chrome++.ini，不存在实例串号风险。
/// </summary>
public static class DefaultBrowser
{
    const string ProgId = "KZManager.Url";
    const string ClientKey = @"Software\Clients\StartMenuInternet\KZManager";

    static string ExePath =>
        Process.GetCurrentProcess().MainModule?.FileName
        ?? Path.Combine(AppContext.BaseDirectory, "KZManager.exe");

    static string OpenCommand => "\"" + ExePath + "\" --route-url \"%1\"";

    /// <summary>注册为可选的默认浏览器。返回给用户看的说明。</summary>
    public static string Register()
    {
        try
        {
            using (var k = Registry.CurrentUser.CreateSubKey($@"Software\Classes\{ProgId}\shell\open\command"))
                k.SetValue("", OpenCommand);
            using (var k = Registry.CurrentUser.CreateSubKey($@"Software\Classes\{ProgId}"))
            {
                k.SetValue("", "KZ 浏览器路由");
                k.SetValue("URL Protocol", "");
            }
            using (var k = Registry.CurrentUser.CreateSubKey($@"Software\Classes\{ProgId}\DefaultIcon"))
                k.SetValue("", "\"" + ExePath + "\",0");

            using (var k = Registry.CurrentUser.CreateSubKey($@"{ClientKey}\shell\open\command"))
                k.SetValue("", OpenCommand);
            using (var k = Registry.CurrentUser.CreateSubKey($@"{ClientKey}\DefaultIcon"))
                k.SetValue("", "\"" + ExePath + "\",0");
            using (var k = Registry.CurrentUser.CreateSubKey($@"{ClientKey}\Capabilities"))
            {
                k.SetValue("ApplicationName", "KZ 浏览器（前台实例优先）");
                k.SetValue("ApplicationDescription", "把外部链接交给当前前台实例打开");
                k.SetValue("ApplicationIcon", "\"" + ExePath + "\",0");
            }
            using (var k = Registry.CurrentUser.CreateSubKey($@"{ClientKey}\Capabilities\URLAssociations"))
            {
                k.SetValue("http", ProgId);
                k.SetValue("https", ProgId);
            }
            using (var k = Registry.CurrentUser.CreateSubKey(@"Software\RegisteredApplications"))
                k.SetValue("KZManager", $@"{ClientKey}\Capabilities");

            var repoint = RepointCurrentDefault();
            return "已注册。\n\n" +
                   "当前默认处理程序已改指向路由器：\n" + repoint + "\n" +
                   "如果上面显示「已经是 KZManager.Url」，说明默认应用已经指向本程序；\n" +
                   "否则系统的默认应用设置无需改动，链接也会经过路由器。\n\n" +
                   "之后任何程序点开的链接，都会落到你当前正在用的那个实例。";
        }
        catch (Exception ex)
        {
            return "注册失败：" + ex.Message;
        }
    }


    /// <summary>
    /// 把"当前实际生效的默认处理程序"改指向 KZManager 路由。
    ///
    /// Windows 的 UserChoice 带哈希保护，程序改不了；但它指向的 ProgId
    /// （例如 chrome.exe 直连的 KZ Browser.URL）的 open 命令是可以改的。
    /// 只改这个命令，默认应用设置不用动，立即生效。
    /// </summary>
    public static string RepointCurrentDefault()
    {
        var report = new StringBuilder();
        try
        {
            foreach (var scheme in new[] { "http", "https" })
            {
                using var uq = Registry.CurrentUser.OpenSubKey(
                    $@"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\{scheme}\UserChoice");
                var progId = uq?.GetValue("ProgId") as string;
                if (string.IsNullOrEmpty(progId))
                {
                    report.AppendLine($"{scheme}: 没有 UserChoice，跳过");
                    continue;
                }
                if (progId == ProgId)
                {
                    report.AppendLine($"{scheme}: 已经是 {ProgId}");
                    continue;
                }

                using var cmd = Registry.CurrentUser.CreateSubKey(
                    $@"Software\Classes\{progId}\shell\open\command");
                if (cmd is null)
                {
                    report.AppendLine($"{scheme}: 无法写入 {progId}");
                    continue;
                }
                var before = cmd.GetValue("") as string ?? "";
                cmd.SetValue("", OpenCommand);
                report.AppendLine($"{scheme}: {progId}");
                report.AppendLine($"   原: {before}");
                report.AppendLine($"   新: {OpenCommand}");
            }
        }
        catch (Exception ex)
        {
            report.AppendLine("失败：" + ex.Message);
        }
        return report.ToString();
    }

    /// <summary>撤销注册。</summary>
    public static string Unregister()
    {
        try
        {
            Registry.CurrentUser.DeleteSubKeyTree($@"Software\Classes\{ProgId}", false);
            Registry.CurrentUser.DeleteSubKeyTree(ClientKey, false);
            using var k = Registry.CurrentUser.OpenSubKey(@"Software\RegisteredApplications", true);
            k?.DeleteValue("KZManager", false);
            return "已撤销默认浏览器路由注册。";
        }
        catch (Exception ex)
        {
            return "撤销失败：" + ex.Message;
        }
    }

    /// <summary>当前是否已注册。</summary>
    public static bool IsRegistered()
    {
        try
        {
            using var k = Registry.CurrentUser.OpenSubKey($@"Software\Classes\{ProgId}\shell\open\command");
            return k?.GetValue("") is string s && s.Contains("--route-url");
        }
        catch { return false; }
    }
}
