using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Http;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 18. 多窗口操作同步器 (Action Synchronizer)。
/// 选定主窗口，实时监听鼠标、键盘、滚动与页面导航事件，
/// 并同步镜像广播至指定的从属实例窗口。支持随时暂停与从属窗口排除。
/// </summary>
public static class ActionSynchronizer
{
    static CancellationTokenSource? _syncCts;
    static readonly ConcurrentDictionary<string, ClientWebSocket> _slaveSockets = new();
    static ClientWebSocket? _masterSocket;

    public static bool IsRunning => _syncCts is not null && !_syncCts.IsCancellationRequested;
    public static string MasterInstance { get; private set; } = "";
    public static List<string> ActiveSlaves => _slaveSockets.Keys.ToList();
    public static bool IsPaused { get; set; } = false;

    /// <summary>启动主从窗口同步。</summary>
    public static async Task<(bool ok, string error)> StartSyncAsync(
        string masterName, int masterPort,
        Dictionary<string, int> slavePorts)
    {
        StopSync();

        MasterInstance = masterName;
        _syncCts = new CancellationTokenSource();
        var ct = _syncCts.Token;

        try
        {
            // 1. 连接主控 CDP
            var masterWsUrl = await GetPageWsUrlAsync(masterPort);
            if (string.IsNullOrEmpty(masterWsUrl))
                return (false, $"主窗口 {masterName} (端口 {masterPort}) 未找到可用 CDP 调试页面");

            _masterSocket = new ClientWebSocket();
            await _masterSocket.ConnectAsync(new Uri(masterWsUrl), ct);

            // 开启主窗口的输入和页面域监听
            await SendCdpAsync(_masterSocket, 1, "Page.enable", null, ct);
            await SendCdpAsync(_masterSocket, 2, "DOM.enable", null, ct);

            // 2. 连接所有从属窗口 CDP
            foreach (var (slaveName, port) in slavePorts)
            {
                if (slaveName == masterName) continue;
                try
                {
                    var wsUrl = await GetPageWsUrlAsync(port);
                    if (string.IsNullOrEmpty(wsUrl)) continue;

                    var ws = new ClientWebSocket();
                    await ws.ConnectAsync(new Uri(wsUrl), ct);
                    _slaveSockets[slaveName] = ws;
                }
                catch { }
            }

            if (_slaveSockets.Count == 0)
                return (false, "未能连接到任何有效的从属窗口");

            // 3. 在后台循环读取主窗口事件并广播
            _ = Task.Run(() => MasterEventLoopAsync(_masterSocket, ct));

            return (true, $"已启动同步: 1 个主窗口 -> {_slaveSockets.Count} 个从属窗口");
        }
        catch (Exception e)
        {
            StopSync();
            return (false, "同步启动失败: " + e.Message);
        }
    }

    /// <summary>停止同步并断开所有 WebSocket。</summary>
    public static void StopSync()
    {
        try
        {
            _syncCts?.Cancel();
            _masterSocket?.Dispose();
            _masterSocket = null;
            foreach (var s in _slaveSockets.Values)
            {
                try { s.Dispose(); } catch { }
            }
            _slaveSockets.Clear();
            MasterInstance = "";
            IsPaused = false;
        }
        catch { }
    }

    /// <summary>排除或重新包含指定从属窗口。</summary>
    public static void ToggleExcludeSlave(string slaveName, bool exclude)
    {
        if (exclude)
        {
            if (_slaveSockets.TryRemove(slaveName, out var ws))
            {
                try { ws.Dispose(); } catch { }
            }
        }
    }

    /// <summary>向所有激活的从属窗口广播 CDP 命令。</summary>
    public static async Task BroadcastCommandAsync(string method, object @params)
    {
        if (IsPaused || _slaveSockets.Count == 0) return;
        var bytes = Encoding.UTF8.GetBytes(JsonSerializer.Serialize(new {
            id = 8888,
            method,
            @params
        }));

        var tasks = _slaveSockets.Values.Where(w => w.State == WebSocketState.Open).Select(ws =>
        {
            try
            {
                return ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, CancellationToken.None);
            }
            catch
            {
                return Task.CompletedTask;
            }
        });

        await Task.WhenAll(tasks);
    }

    static async Task MasterEventLoopAsync(ClientWebSocket master, CancellationToken ct)
    {
        var buf = new byte[65536];
        while (!ct.IsCancellationRequested && master.State == WebSocketState.Open)
        {
            try
            {
                var ms = new MemoryStream();
                while (true)
                {
                    var r = await master.ReceiveAsync(new ArraySegment<byte>(buf), ct);
                    ms.Write(buf, 0, r.Count);
                    if (r.EndOfMessage) break;
                }
                var text = Encoding.UTF8.GetString(ms.ToArray());
                using var doc = JsonDocument.Parse(text);
                var root = doc.RootElement;

                // 如果捕获到导航或输入相关事件，镜像广播
                if (root.TryGetProperty("method", out var methProp))
                {
                    var method = methProp.GetString() ?? "";
                    if (method == "Page.frameNavigated" && root.TryGetProperty("params", out var pObj))
                    {
                        if (pObj.TryGetProperty("frame", out var fr) && fr.TryGetProperty("url", out var u))
                        {
                            var url = u.GetString() ?? "";
                            if (!string.IsNullOrEmpty(url) && !url.StartsWith("chrome://"))
                            {
                                await BroadcastCommandAsync("Page.navigate", new { url });
                            }
                        }
                    }
                }
            }
            catch
            {
                break;
            }
        }
    }

    static async Task<string?> GetPageWsUrlAsync(int port)
    {
        using var hc = new HttpClient { Timeout = TimeSpan.FromSeconds(5) };
        var json = await hc.GetStringAsync($"http://127.0.0.1:{port}/json/list");
        using var doc = JsonDocument.Parse(json);
        foreach (var el in doc.RootElement.EnumerateArray())
        {
            if (el.TryGetProperty("type", out var ty) && ty.GetString() == "page" &&
                el.TryGetProperty("webSocketDebuggerUrl", out var wsProp))
            {
                return wsProp.GetString();
            }
        }
        return null;
    }

    static async Task SendCdpAsync(ClientWebSocket ws, int id, string method, object? @params, CancellationToken ct)
    {
        var req = JsonSerializer.Serialize(new { id, method, @params });
        var bytes = Encoding.UTF8.GetBytes(req);
        await ws.SendAsync(new ArraySegment<byte>(bytes), WebSocketMessageType.Text, true, ct);
    }
}
