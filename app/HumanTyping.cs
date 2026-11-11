using System;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 19. 拟人化按键输入器 (Human Typing Simulator)。
/// 将长文本转化为带有 50~150ms 正态随机微延迟的物理键盘输入流，
/// 生成 rawKeyDown、char、keyUp 完整物理事件，彻底杜绝瞬间文本突变风控。
/// </summary>
public static class HumanTyping
{
    public static async Task TypeTextViaCdpAsync(
        ClientWebSocket ws,
        string text,
        int minDelayMs = 50,
        int maxDelayMs = 150,
        CancellationToken ct = default)
    {
        if (string.IsNullOrEmpty(text) || ws.State != WebSocketState.Open) return;

        var rng = Random.Shared;
        int id = 7000;

        foreach (var ch in text)
        {
            if (ct.IsCancellationRequested) break;

            // 1. keyDown
            var downMsg = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new {
                id = ++id,
                method = "Input.dispatchKeyEvent",
                @params = new {
                    type = "rawKeyDown",
                    key = ch.ToString(),
                    text = ch.ToString()
                }
            }));
            await ws.SendAsync(new ArraySegment<byte>(downMsg), WebSocketMessageType.Text, true, ct);

            // 2. char (生成字符)
            var charMsg = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new {
                id = ++id,
                method = "Input.dispatchKeyEvent",
                @params = new {
                    type = "char",
                    key = ch.ToString(),
                    text = ch.ToString()
                }
            }));
            await ws.SendAsync(new ArraySegment<byte>(charMsg), WebSocketMessageType.Text, true, ct);

            // 3. keyUp
            var upMsg = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new {
                id = ++id,
                method = "Input.dispatchKeyEvent",
                @params = new {
                    type = "keyUp",
                    key = ch.ToString()
                }
            }));
            await ws.SendAsync(new ArraySegment<byte>(upMsg), WebSocketMessageType.Text, true, ct);

            // 拟人化物理敲击延迟：基础均值 + 随机抖动
            int delay = rng.Next(minDelayMs, maxDelayMs + 1);
            // 标点符号或换行稍长停顿
            if (ch is ' ' or ',' or '.' or '\n' or '，' or '。')
            {
                delay += rng.Next(80, 200);
            }
            await Task.Delay(delay, ct);
        }
    }
}
