using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 21. 自定义 JS 启动隐蔽注入器 (Userscript Manager)。
/// 在页面加载前通过 CDP Page.addScriptToEvaluateOnNewDocument 隐蔽注入自定义脚本，
/// 无需安装油猴扩展，扩展列表零留痕，用于反爬辅助、自动填表或特征篡改。
/// </summary>
public static class UserscriptManager
{
    public sealed class ScriptItem
    {
        public string Name { get; set; } = "";
        public string Code { get; set; } = "";
        public bool Enabled { get; set; } = true;
    }

    public static string ScriptsDir => Path.Combine(Paths.Root, "_scripts");

    public static List<ScriptItem> ListScripts()
    {
        var list = new List<ScriptItem>();
        if (!Directory.Exists(ScriptsDir))
        {
            Directory.CreateDirectory(ScriptsDir);
            // 写入默认示例脚本
            var demo = Path.Combine(ScriptsDir, "anti-detector-hook.js");
            if (!File.Exists(demo))
            {
                File.WriteAllText(demo,
                    "// 示例：隐蔽注入脚本 (页面加载前执行)\n" +
                    "console.log('[KZ Userscript] Injected on document start');\n");
            }
        }

        foreach (var f in Directory.GetFiles(ScriptsDir, "*.js"))
        {
            try
            {
                list.Add(new ScriptItem {
                    Name = Path.GetFileName(f),
                    Code = File.ReadAllText(f),
                    Enabled = true
                });
            }
            catch { }
        }
        return list;
    }

    /// <summary>通过 WebSocket CDP 向目标页面注册启动注入脚本。</summary>
    public static async Task<int> InjectScriptsAsync(ClientWebSocket ws, CancellationToken ct = default)
    {
        var scripts = ListScripts().Where(x => x.Enabled && !string.IsNullOrWhiteSpace(x.Code)).ToList();
        if (scripts.Count == 0 || ws.State != WebSocketState.Open) return 0;

        int id = 5000;
        int count = 0;
        foreach (var s in scripts)
        {
            var req = JsonSerializer.Serialize(new {
                id = ++id,
                method = "Page.addScriptToEvaluateOnNewDocument",
                @params = new { source = s.Code }
            });
            var bytes = Encoding.UTF8.GetBytes(req);
            await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, ct);
            count++;
        }
        return count;
    }
}
