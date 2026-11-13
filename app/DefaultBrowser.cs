using System;
using System.Diagnostics;
using System.IO;
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

            return "已注册。\n\n接下来请在 Windows「设置 → 应用 → 默认应用」里，\n" +
                   "把「KZ 浏览器（前台实例优先）」选为默认浏览器。\n\n" +
                   "之后任何程序点开的链接，都会落到你当前正在用的那个实例。";
        }
        catch (Exception ex)
        {
            return "注册失败：" + ex.Message;
        }
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
