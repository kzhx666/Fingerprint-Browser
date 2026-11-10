using System.Diagnostics;
using System.IO;

namespace KZManager;

/// <summary>
/// 把 google-oauth.json 里的凭据注入到 chrome 进程的环境变量。
///
/// 非 Google 官方品牌（is_chrome_branded=false）的构建里，Chromium 的
/// GetClientID() 会去读 GOOGLE_DEFAULT_CLIENT_ID 环境变量；没有就用内置的
/// 空值，Google 会返回 no_authorization_code=true，登录按钮直接消失。
///
/// 另外还需要 --disable-features=DiceHeaderVersion2（v1 协议才发授权码），
/// 那条在 Services.BuildArgs 里加。
/// </summary>
public static class GoogleOAuth
{
    sealed class Cfg
    {
        public string? api_key { get; set; }
        public string? client_id { get; set; }
        public string? client_secret { get; set; }
        public bool enabled { get; set; } = true;
    }

    public static string ConfigPath => Path.Combine(Paths.Root, "google-oauth.json");

    public static bool IsConfigured
    {
        get
        {
            var c = Json.Read<Cfg>(ConfigPath);
            return c is not null && c.enabled
                && !string.IsNullOrWhiteSpace(c.client_id);
        }
    }

    /// <summary>把凭据写进子进程环境。返回是否真的设置了。</summary>
    public static bool ApplyTo(ProcessStartInfo psi)
    {
        var c = Json.Read<Cfg>(ConfigPath);
        if (c is null) return false;
        if (!c.enabled) return false;

        bool any = false;
        if (!string.IsNullOrWhiteSpace(c.api_key))
        {
            psi.Environment["GOOGLE_API_KEY"] = c.api_key;
            any = true;
        }
        if (!string.IsNullOrWhiteSpace(c.client_id))
        {
            psi.Environment["GOOGLE_DEFAULT_CLIENT_ID"] = c.client_id;
            any = true;
        }
        if (!string.IsNullOrWhiteSpace(c.client_secret))
        {
            psi.Environment["GOOGLE_DEFAULT_CLIENT_SECRET"] = c.client_secret;
            any = true;
        }
        return any;
    }

    /// <summary>生成一段给人看的说明，用于健康巡检。</summary>
    public static string Describe()
    {
        if (!File.Exists(ConfigPath))
            return $"未找到 {ConfigPath} —— 谷歌登录会失效";

        var c = Json.Read<Cfg>(ConfigPath);
        if (c is null)
            return $"{ConfigPath} 解析失败 —— 谷歌登录会失效";
        if (!c.enabled)
            return "google-oauth.json 里 enabled=false —— 谷歌登录已关闭";
        if (string.IsNullOrWhiteSpace(c.client_id))
            return "google-oauth.json 缺少 client_id —— 谷歌登录会失效";

        var cid = c.client_id!;
        var shown = cid.Length > 14 ? cid[..10] + "…" + cid[^4..] : cid;
        return $"已配置 client_id={shown}";
    }
}
