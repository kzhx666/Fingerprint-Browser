using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.Json;
using System.Windows;

namespace KZManager;

/// <summary>
/// 24: 实例健康评分面板。聚合指纹配置一致性 / 代理 / 地区 / 时区 /
/// 内核补丁完整性，给每个实例一个 0-100 分和可读问题清单。
/// </summary>
public static class HealthScore
{
    public sealed class Item
    {
        public string Name = "";
        public int Score;
        public List<string> Issues = new();
        public string Summary => Issues.Count == 0 ? "全部通过" : string.Join("；", Issues);
    }

    public sealed class Report
    {
        public List<Item> Items = new();
        public int KernelScore;
        public string KernelNote = "";
        public double Average => Items.Count == 0 ? 0 : Items.Average(x => x.Score);
    }

    public static Report Evaluate(AppConfig cfg)
    {
        var rep = new Report();
        var k = KernelSelfCheck.Check(cfg.chromeExe);
        rep.KernelScore = k.Ok ? 100 : Math.Max(0, 100 - k.Missing.Count * 8);
        rep.KernelNote = k.Note;
        var exits = ProxyPrecheck.LoadAll();

        foreach (var name in Store.ListInstances())
        {
            var p = Store.LoadProfile(name);
            var it = new Item { Name = name, Score = 100 };
            void Deduct(int n, string why) { it.Score -= n; it.Issues.Add(why); }

            // 代理
            if (string.IsNullOrWhiteSpace(p.proxy) && string.IsNullOrWhiteSpace(p.node))
                Deduct(15, "未配置代理（直连）");
            else if (!Fingerprint.ValidProxy(p.proxy ?? "", out var perr))
                Deduct(25, "代理格式不合法: " + perr);

            // 地区声明 vs 出口
            var declared = ProxyPrecheck.InferDeclared(name, p.timezone, p.region);
            if (exits.TryGetValue(name, out var e))
            {
                if (e.verdict == "mismatch")
                    Deduct(30, $"地区不符（声明 {e.declared} / 出口 {e.country}）");
                else if (e.verdict == "unknown")
                    Deduct(5, "出口地区未比对成功");
            }
            else
            {
                Deduct(10, "尚无出口预检记录");
            }

            // 时区 / 语言 / 地区一致性
            if (string.IsNullOrWhiteSpace(p.timezone)) Deduct(10, "未设置时区");
            if (string.IsNullOrWhiteSpace(p.lang)) Deduct(5, "未设置语言");
            if (declared.Length == 0) Deduct(8, "无法推断声明地区");

            // 指纹开关
            if (string.IsNullOrWhiteSpace(p.seed)) Deduct(15, "无指纹种子");
            if ((p.fp_canvas ?? "") == "off") Deduct(5, "Canvas 伪装关闭");
            if ((p.webrtc ?? "") != "strict") Deduct(10, "WebRTC 非 strict");

            // 内核补丁
            if (!k.Ok) Deduct(10, "内核补丁不完整");

            if (it.Score < 0) it.Score = 0;
            rep.Items.Add(it);
        }
        return rep;
    }
}
