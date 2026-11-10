using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace KZManager;

/// <summary>
/// 带账号密码的 socks5 → 本地无认证 HTTP 代理转发。
///
/// Chromium 的 --proxy-server 不支持 URI 里嵌 socks5 用户名密码
/// (ERR_NO_SUPPORTED_PROXIES)，所以启动实例前拉起 socks5-relay.py。
///
/// 工程化要点（对标商业指纹浏览器）：
///  - 按「节点串」哈希分配独立本地端口，不同节点绝不复用同一转发器
///    （之前固定 10818，两个不同节点的实例会串出口 IP）
///  - 账密写临时 JSON 由 --config 传入，读完即删，不出现在命令行/进程列表
///  - _relay\port-N.json 记录节点签名 + PID，复用前校验，节点变了重起
///  - 转发器带 idle-timeout，无活动自动退出，不留孤儿进程
/// </summary>
public static class ProxyRelay
{
    public static readonly string RelayPy = Path.Combine(Paths.Root, "socks5-relay.py");
    static string RelayDir => Path.Combine(Paths.Root, "_relay");

    sealed class PortRecord
    {
        public string node { get; set; } = "";
        public int pid { get; set; }
    }

    public static bool NeedsRelay(string proxy)
        => !string.IsNullOrWhiteSpace(proxy) && proxy.Contains("@")
           && proxy.StartsWith("socks5://", StringComparison.OrdinalIgnoreCase);

    /// <summary>按节点串稳定映射到 20000-29999，不同节点必不同端口。</summary>
    static int PortFor(string sig)
    {
        var h = SHA256.HashData(Encoding.UTF8.GetBytes(sig));
        int v = (h[0] << 16) | (h[1] << 8) | h[2];
        return 20000 + (v % 10000);
    }

    static string SigFor(string host, int port, string user)
        => $"{host}:{port}:{user}";

    static string PortFile(int port) => Path.Combine(RelayDir, $"port-{port}.json");

    static bool PortUp(int port)
    {
        try { using var c = new TcpClient(); c.Connect(IPAddress.Loopback, port); return true; }
        catch { return false; }
    }

    static PortRecord? ReadRecord(int port)
    {
        try
        {
            var f = PortFile(port);
            return File.Exists(f)
                ? JsonSerializer.Deserialize<PortRecord>(File.ReadAllText(f))
                : null;
        }
        catch { return null; }
    }

    static void WriteRecord(int port, string sig, int pid)
    {
        try
        {
            Directory.CreateDirectory(RelayDir);
            File.WriteAllText(PortFile(port),
                JsonSerializer.Serialize(new PortRecord { node = sig, pid = pid }));
        }
        catch { }
    }

    static void RemoveRecord(int port)
    {
        try { File.Delete(PortFile(port)); } catch { }
    }

    static bool ProcessAlive(int pid)
    {
        try { using var p = Process.GetProcessById(pid); return !p.HasExited; }
        catch { return false; }
    }

    public static string? Ensure(string proxy, string instanceName, Action<string>? say = null)
    {
        if (!NeedsRelay(proxy)) return proxy;
        if (!File.Exists(RelayPy))
        {
            say?.Invoke("找不到 socks5-relay.py，无法转发带认证的 socks5");
            return null;
        }

        try
        {
            var uri = new Uri(proxy);
            var ui = uri.UserInfo.Split(':', 2);
            if (ui.Length != 2) return null;
            var user = Uri.UnescapeDataString(ui[0]);
            var pwd = Uri.UnescapeDataString(ui[1]);
            var sig = SigFor(uri.Host, uri.Port, user);
            var port = PortFor(sig);

            // 只有「端口活着 + 记录的节点签名一致 + 进程还在」才复用
            var rec = ReadRecord(port);
            if (PortUp(port) && rec is not null && rec.node == sig && ProcessAlive(rec.pid))
            {
                say?.Invoke($"复用节点专属转发器 :{port}");
                return $"http://127.0.0.1:{port}";
            }
            // 端口被别的节点占了或记录不符：清掉旧的，重起
            if (rec is not null && rec.node != sig)
                say?.Invoke($"节点已变化，重建转发器 :{port}");
            RemoveRecord(port);

            // 账密写临时文件，不进命令行
            Directory.CreateDirectory(RelayDir);
            var cfgPath = Path.Combine(RelayDir, $"cfg-{port}-{Guid.NewGuid():N}.json");
            File.WriteAllText(cfgPath, JsonSerializer.Serialize(new
            {
                host = uri.Host, port = uri.Port, user, passwd = pwd
            }));

            var psi = new ProcessStartInfo
            {
                FileName = "pythonw.exe",
                Arguments = $"\"{RelayPy}\" --config \"{cfgPath}\" " +
                            $"--listen-port {port} --idle-timeout 300",
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden
            };
            var proc = Process.Start(psi);
            if (proc is null) return null;

            for (int i = 0; i < 30 && !PortUp(port); i++)
                System.Threading.Thread.Sleep(300);
            if (!PortUp(port))
            {
                say?.Invoke("转发器启动超时");
                return null;
            }
            WriteRecord(port, sig, proc.Id);
            say?.Invoke($"已启动节点专属转发器 :{port} → {uri.Host}:{uri.Port}（账密不落命令行）");
            return $"http://127.0.0.1:{port}";
        }
        catch (Exception e)
        {
            say?.Invoke("转发器失败: " + e.Message);
            return null;
        }
    }

    /// <summary>供关闭实例/退出管理器时调用：清掉该实例对应节点的转发器。</summary>
    public static void StopFor(string proxy)
    {
        if (!NeedsRelay(proxy)) return;
        try
        {
            var uri = new Uri(proxy);
            var ui = uri.UserInfo.Split(':', 2);
            var sig = SigFor(uri.Host, uri.Port, Uri.UnescapeDataString(ui[0]));
            var port = PortFor(sig);
            var rec = ReadRecord(port);
            if (rec is not null && rec.node == sig && ProcessAlive(rec.pid))
            {
                try { using var p = Process.GetProcessById(rec.pid); p.Kill(); }
                catch { }
            }
            RemoveRecord(port);
        }
        catch { }
    }
}
