using System;
using System.Collections.Generic;
using System.IO;
using System.Net.Http;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 25. 可视化自动化基础 / RPA 任务执行引擎。
/// 支持顺序执行 Page.navigate、DOM 点击、拟人化按键输入与步骤等待。
/// </summary>
public static class SimpleRpa
{
    public sealed class RpaStep
    {
        public string Action { get; set; } = "goto"; // goto / wait / type / click / screenshot
        public string Target { get; set; } = "";
        public string Value { get; set; } = "";
        public int DelayMs { get; set; } = 1000;
    }

    public sealed class RpaReport
    {
        public bool Ok;
        public int CompletedSteps;
        public List<string> Logs = new();
        public string Error = "";
    }

    public static async Task<RpaReport> ExecuteAsync(int cdpPort, List<RpaStep> steps, CancellationToken ct = default)
    {
        var rep = new RpaReport();
        if (steps.Count == 0) { rep.Ok = true; return rep; }

        try
        {
            using var hc = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
            var listJson = await hc.GetStringAsync($"http://127.0.0.1:{cdpPort}/json/list");
            using var doc = JsonDocument.Parse(listJson);
            string? wsUrl = null;
            foreach (var el in doc.RootElement.EnumerateArray())
            {
                if (el.TryGetProperty("type", out var ty) && ty.GetString() == "page" &&
                    el.TryGetProperty("webSocketDebuggerUrl", out var wsProp))
                {
                    wsUrl = wsProp.GetString();
                    if (!string.IsNullOrEmpty(wsUrl)) break;
                }
            }
            if (string.IsNullOrEmpty(wsUrl))
            {
                rep.Error = "未找到可用 CDP 调试页面";
                return rep;
            }

            using var ws = new ClientWebSocket();
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(60));
            await ws.ConnectAsync(new Uri(wsUrl), cts.Token);

            int id = 3000;
            foreach (var step in steps)
            {
                if (ct.IsCancellationRequested) break;
                rep.Logs.Add($"执行动作: {step.Action} -> {step.Target} {step.Value}");

                switch (step.Action.ToLowerInvariant())
                {
                    case "goto":
                        var nav = JsonSerializer.Serialize(new {
                            id = ++id,
                            method = "Page.navigate",
                            @params = new { url = step.Target }
                        });
                        await ws.SendAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(nav)), WebSocketMessageType.Text, true, cts.Token);
                        await Task.Delay(Math.Max(2000, step.DelayMs), cts.Token);
                        break;

                    case "wait":
                        await Task.Delay(step.DelayMs, cts.Token);
                        break;

                    case "type":
                        // 使用拟人化输入器
                        await HumanTyping.TypeTextViaCdpAsync(ws, step.Value, ct: cts.Token);
                        break;

                    case "click":
                        var clickJs = $"document.querySelector('{step.Target}')?.click()";
                        var clickMsg = JsonSerializer.Serialize(new {
                            id = ++id,
                            method = "Runtime.evaluate",
                            @params = new { expression = clickJs }
                        });
                        await ws.SendAsync(new ArraySegment<byte>(Encoding.UTF8.GetBytes(clickMsg)), WebSocketMessageType.Text, true, cts.Token);
                        await Task.Delay(step.DelayMs, cts.Token);
                        break;
                }
                rep.CompletedSteps++;
            }

            await ws.CloseAsync(WebSocketCloseStatus.NormalClosure, "done", CancellationToken.None);
            rep.Ok = true;
            return rep;
        }
        catch (Exception e)
        {
            rep.Ok = false;
            rep.Error = "RPA 执行失败: " + e.Message;
            return rep;
        }
    }
}
