using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Net.Sockets;
using System.Text.Json;
using System.Threading.Tasks;

namespace KZManager;

/// <summary>
/// 10. 代理管理中心 + 11. 代理 IP 欺诈度预检。
/// 集中管理代理池、分组、批量导入检测、延迟探测、自动分配与风控分值。
/// </summary>
public static class ProxyManager
{
    public sealed class ProxyNode
    {
        public string Id { get; set; } = Guid.NewGuid().ToString("N").Substring(0, 8);
        public string Name { get; set; } = "";
        public string Type { get; set; } = "socks5"; // socks5 / http
        public string Host { get; set; } = "";
        public int Port { get; set; }
        public string User { get; set; } = "";
        public string Pass { get; set; } = "";
        public string Group { get; set; } = "默认";
        public string Region { get; set; } = "";
        public int LatencyMs { get; set; } = -1;
        public bool Alive { get; set; }
        public string LastChecked { get; set; } = "";
        public int FraudScore { get; set; } = 0; // 0-100
        public string Isp { get; set; } = "";
        public bool IsDatacenter { get; set; }
        public int AssignedCount { get; set; }

        public string ToUri()
        {
            var auth = !string.IsNullOrEmpty(User)
                ? $"{Uri.EscapeDataString(User)}:{Uri.EscapeDataString(Pass)}@" : "";
            return $"{Type}://{auth}{Host}:{Port}";
        }

        public string MaskedUri()
        {
            var auth = !string.IsNullOrEmpty(User) ? "***@" : "";
            return $"{Type}://{auth}{Host}:{Port}";
        }
    }

    static string PoolFile => Path.Combine(Paths.Root, "_proxies", "proxy_pool.json");

    public static List<ProxyNode> LoadPool()
    {
        try
        {
            if (!File.Exists(PoolFile)) return new List<ProxyNode>();
            return JsonSerializer.Deserialize<List<ProxyNode>>(File.ReadAllText(PoolFile)) ?? new List<ProxyNode>();
        }
        catch { return new List<ProxyNode>(); }
    }

    public static void SavePool(List<ProxyNode> pool)
    {
        try
        {
            var dir = Path.GetDirectoryName(PoolFile);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            var tmp = PoolFile + ".tmp-" + Guid.NewGuid().ToString("N").Substring(0, 6);
            File.WriteAllText(tmp, JsonSerializer.Serialize(pool, new JsonSerializerOptions { WriteIndented = true }));
            if (File.Exists(PoolFile)) File.Replace(tmp, PoolFile, null);
            else File.Move(tmp, PoolFile);
        }
        catch { }
    }

    /// <summary>批量导入代理文本（支持 host:port:user:pass 或完整 uri）。</summary>
    public static (int imported, int duplicates, List<string> errors) ImportText(string raw, string group = "默认")
    {
        var pool = LoadPool();
        var existing = new HashSet<string>(pool.Select(x => $"{x.Host}:{x.Port}:{x.User}"), StringComparer.OrdinalIgnoreCase);
        int added = 0, dups = 0;
        var errs = new List<string>();

        var lines = raw.Split(new[] { '\r', '\n' }, StringSplitOptions.RemoveEmptyEntries);
        foreach (var l in lines)
        {
            var line = l.Trim();
            if (line.Length == 0 || line.StartsWith("#")) continue;

            try
            {
                var node = ParseLine(line);
                if (node is null) { errs.Add($"无法解析: {line}"); continue; }
                node.Group = group;
                var key = $"{node.Host}:{node.Port}:{node.User}";
                if (existing.Contains(key)) { dups++; continue; }
                existing.Add(key);
                pool.Add(node);
                added++;
            }
            catch (Exception e) { errs.Add($"{line}: {e.Message}"); }
        }

        SavePool(pool);
        return (added, dups, errs);
    }

    static ProxyNode? ParseLine(string line)
    {
        if (line.Contains("://"))
        {
            if (!Uri.TryCreate(line, UriKind.Absolute, out var u)) return null;
            var node = new ProxyNode
            {
                Type = u.Scheme.ToLowerInvariant(),
                Host = u.Host,
                Port = u.Port,
                Name = u.Host
            };
            if (!string.IsNullOrEmpty(u.UserInfo))
            {
                var parts = u.UserInfo.Split(':');
                node.User = Uri.UnescapeDataString(parts[0]);
                if (parts.Length > 1) node.Pass = Uri.UnescapeDataString(parts[1]);
            }
            return node;
        }

        // 格式 host:port:user:pass 或 host:port
        var cols = line.Split(':');
        if (cols.Length < 2) return null;
        if (!int.TryParse(cols[1], out var port) || port <= 0 || port > 65535) return null;

        var n = new ProxyNode
        {
            Type = "socks5",
            Host = cols[0].Trim(),
            Port = port,
            Name = cols[0].Trim()
        };
        if (cols.Length >= 4)
        {
            n.User = cols[2].Trim();
            n.Pass = cols[3].Trim();
        }
        return n;
    }

    /// <summary>单节点测速与连通性检测。</summary>
    public static async Task TestNodeAsync(ProxyNode node)
    {
        var sw = System.Diagnostics.Stopwatch.StartNew();
        try
        {
            using var tcp = new TcpClient();
            var ct = tcp.ConnectAsync(node.Host, node.Port);
            if (!ct.Wait(TimeSpan.FromSeconds(6)) || !tcp.Connected)
            {
                node.Alive = false;
                node.LatencyMs = -1;
                node.LastChecked = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
                return;
            }

            if (node.Type == "socks5")
            {
                var s = tcp.GetStream();
                s.ReadTimeout = s.WriteTimeout = 5000;
                s.Write(new byte[] { 0x05, 0x01, 0x00 }, 0, 3);
                int m0 = s.ReadByte(), m1 = s.ReadByte();
                if (m0 != 0x05) throw new Exception("not socks5");
            }

            sw.Stop();
            node.Alive = true;
            node.LatencyMs = (int)sw.ElapsedMilliseconds;
            node.LastChecked = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
        }
        catch
        {
            node.Alive = false;
            node.LatencyMs = -1;
            node.LastChecked = DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss");
        }
    }

    /// <summary>批量测速全部节点。</summary>
    public static async Task BatchTestAsync(IEnumerable<ProxyNode> nodes)
    {
        var tasks = nodes.Select(TestNodeAsync);
        await Task.WhenAll(tasks);
        SavePool(LoadPool());
    }

    /// <summary>自动分配节点给指定实例。</summary>
    public static int AutoAssign(string group, List<string> instanceNames)
    {
        var pool = LoadPool();
        var candidates = pool.Where(x => string.IsNullOrEmpty(group) || x.Group == group).ToList();
        if (candidates.Count == 0) return 0;

        int assigned = 0;
        int idx = 0;
        foreach (var inst in instanceNames)
        {
            var p = Store.LoadProfile(inst);
            var node = candidates[idx % candidates.Count];
            p.proxy = node.ToUri();
            Store.SaveProfile(inst, p);
            node.AssignedCount++;
            idx++;
            assigned++;
        }
        SavePool(pool);
        return assigned;
    }
}
