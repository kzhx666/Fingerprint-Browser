using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace KZManager;

/// <summary>
/// 一键备份 / 还原对话框。
/// 备份页：实例多选 + 备份项多选（书签/密码/历史/偏好/扩展）
/// 还原页：选备份批次 + 目标实例 + 还原项
/// 后台跑 backup.py，日志实时回显。
/// </summary>
public static class BackupDialog
{
    public sealed class Item
    {
        public string Key { get; init; } = "";
        public string Label { get; init; } = "";
        public string Desc { get; init; } = "";
    }

    public static readonly Item[] WhatItems =
    {
        new() { Key="bookmarks",  Label="书签",     Desc="Bookmarks / 加密副本" },
        new() { Key="passwords",  Label="密码",     Desc="Login Data / Web Data" },
        new() { Key="history",    Label="历史记录", Desc="History" },
        new() { Key="prefs",      Label="偏好设置", Desc="Preferences / Secure Preferences" },
        new() { Key="extensions", Label="扩展",     Desc="Extensions 目录 + 相关配置" },
    };

    sealed class Row
    {
        public string Name { get; init; } = "";
        public bool Checked { get; set; }
    }

    static Brush Res(string k) => (Brush)Application.Current.Resources[k];

    public static void Show(Window owner, bool restoreMode, string preselectInstance = "")
    {
        var instances = Store.ListInstances().ToList();
        var win = new Window
        {
            Title = restoreMode ? "一键还原" : "一键备份",
            Width = 760, Height = 640,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = owner, ResizeMode = ResizeMode.CanResize,
            Background = Res("B_Bg"), FontFamily = (FontFamily)Application.Current.Resources["F_Ui"],
            Foreground = Res("B_Text"), ShowInTaskbar = false
        };

        var root = new Grid { Margin = new Thickness(16) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var title = new TextBlock
        {
            Text = restoreMode
                ? "选一个备份批次、目标实例和要还原的内容"
                : "勾选要备份的实例和内容，然后点「开始备份」",
            FontSize = 13, Foreground = Res("B_TextDim"), Margin = new Thickness(0, 0, 0, 10)
        };
        Grid.SetRow(title, 0); root.Children.Add(title);

        var body = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        Grid.SetRow(body, 1);
        var stack = new StackPanel();
        body.Content = stack;
        root.Children.Add(body);

        // ---------- 实例区 ----------
        var instBox = new GroupBox
        {
            Header = restoreMode ? "目标实例" : "要备份的实例",
            Foreground = Res("B_Text"), Margin = new Thickness(0, 0, 0, 10),
            Padding = new Thickness(8)
        };
        var instPanel = new WrapPanel { Orientation = Orientation.Horizontal };
        var instRows = new List<(CheckBox cb, string name)>();
        foreach (var n in instances)
        {
            var cb = new CheckBox
            {
                Content = n, Margin = new Thickness(4, 3, 14, 3),
                Foreground = Res("B_Text"),
                IsChecked = restoreMode
                    ? n == preselectInstance
                    : true   // 备份默认全选
            };
            instPanel.Children.Add(cb);
            instRows.Add((cb, n));
        }
        instBox.Content = instPanel;
        stack.Children.Add(instBox);

        // ---------- 备份批次（仅还原）----------
        ComboBox? batchCombo = null;
        if (restoreMode)
        {
            var gb = new GroupBox { Header = "备份批次", Foreground = Res("B_Text"),
                                   Margin = new Thickness(0, 0, 0, 10), Padding = new Thickness(8) };
            batchCombo = new ComboBox { Height = 30, Style = (Style)Application.Current.Resources["Combo"] };
            var bdir = Paths.BackupDir;
            if (Directory.Exists(bdir))
            {
                var dirs = Directory.GetDirectories(bdir)
                    .Select(x => new { Path = x, Name = Path.GetFileName(x), t = Directory.GetLastWriteTime(x) })
                    .OrderByDescending(x => x.t).ToList();
                foreach (var d in dirs)
                {
                    var meta = Path.Combine(d.Path, "meta.json");
                    string hint = "";
                    if (File.Exists(meta))
                    {
                        try { hint = "  (" + d.Name + ")"; } catch { }
                    }
                    batchCombo.Items.Add(new ComboBoxItem { Content = d.Name + hint, Tag = d.Name });
                }
                if (batchCombo.Items.Count > 0) batchCombo.SelectedIndex = 0;
            }
            if (batchCombo.Items.Count == 0)
                batchCombo.Items.Add(new ComboBoxItem { Content = "（暂无备份）", Tag = "" });
            gb.Content = batchCombo;
            stack.Children.Add(gb);
        }

        // ---------- 备份项多选 ----------
        var whatBox = new GroupBox
        {
            Header = restoreMode ? "要还原的内容" : "要备份的内容",
            Foreground = Res("B_Text"), Margin = new Thickness(0, 0, 0, 10),
            Padding = new Thickness(8)
        };
        var whatStack = new StackPanel();
        var bar = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(0, 0, 0, 6) };
        var whatCbs = new List<CheckBox>();
        void MakeWhatRow(Item it)
        {
            var cb = new CheckBox { IsChecked = true, Margin = new Thickness(2),
                                   Foreground = Res("B_Text"), Tag = it.Key,
                                   Name = "chk_" + it.Key };
            var sp = new StackPanel { Orientation = Orientation.Horizontal };
            System.Windows.Automation.AutomationProperties.SetName(cb, it.Label);
            sp.Children.Add(cb);
            sp.Children.Add(new TextBlock { Text = it.Label, Width = 90, FontWeight = FontWeights.SemiBold,
                                            Foreground = Res("B_Text"), VerticalAlignment = VerticalAlignment.Center });
            sp.Children.Add(new TextBlock { Text = it.Desc, Foreground = Res("B_TextDim"),
                                            FontSize = 11, VerticalAlignment = VerticalAlignment.Center });
            whatStack.Children.Add(sp);
            whatCbs.Add(cb);
        }
        foreach (var it in WhatItems) MakeWhatRow(it);
        var allBtn = new Button { Content = "全选", Style = (Style)Application.Current.Resources["Btn"],
                                  MinWidth = 60, Margin = new Thickness(0, 0, 6, 0) };
        allBtn.Click += (_, _) => { foreach (var c in whatCbs) c.IsChecked = true; };
        var noneBtn = new Button { Content = "全不选", Style = (Style)Application.Current.Resources["Btn"],
                                   MinWidth = 60 };
        noneBtn.Click += (_, _) => { foreach (var c in whatCbs) c.IsChecked = false; };
        bar.Children.Add(allBtn); bar.Children.Add(noneBtn);
        whatStack.Children.Insert(0, bar);
        whatBox.Content = whatStack;
        stack.Children.Add(whatBox);

        // ---------- 日志 ----------
        var log = new TextBox
        {
            Height = 130, IsReadOnly = true, TextWrapping = TextWrapping.Wrap,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Background = Res("B_Card"), Foreground = Res("B_Text"),
            BorderBrush = Res("B_Line"), FontFamily = new FontFamily("Consolas"),
            FontSize = 11, Padding = new Thickness(8), Margin = new Thickness(0, 0, 0, 8)
        };
        Grid.SetRow(log, 2); root.Children.Add(log);
        void Log(string s) => win.Dispatcher.Invoke(() =>
        {
            log.AppendText(s + Environment.NewLine);
            log.ScrollToEnd();
        });

        // ---------- 底部 ----------
        var foot = new StackPanel { Orientation = Orientation.Horizontal,
                                    HorizontalAlignment = HorizontalAlignment.Right };
        var status = new TextBlock { Text = "", Foreground = Res("B_TextDim"),
                                     VerticalAlignment = VerticalAlignment.Center,
                                     Margin = new Thickness(0, 0, 12, 0) };
        var close = new Button { Content = "关闭", Style = (Style)Application.Current.Resources["Btn"],
                                 MinWidth = 80, IsCancel = true };
        close.Click += (_, _) => win.Close();
        var go = new Button { Content = restoreMode ? "开始还原" : "开始备份",
                              Style = (Style)Application.Current.Resources["BtnPrimary"],
                              MinWidth = 110, IsDefault = true };
        foot.Children.Add(status); foot.Children.Add(close); foot.Children.Add(go);
        Grid.SetRow(foot, 3); root.Children.Add(foot);

        go.Click += async (_, _) =>
        {
            var chosen = instRows.Where(x => x.cb.IsChecked == true).Select(x => x.name).ToList();
            if (chosen.Count == 0) { MessageBox.Show("至少选一个实例", "提示"); return; }
            var what = whatCbs.Where(c => c.IsChecked == true)
                              .Select(c => (string)c.Tag!).ToList();
            if (what.Count == 0) { MessageBox.Show("至少选一项内容", "提示"); return; }

            if (restoreMode && chosen.Count > 1)
            { MessageBox.Show("还原一次只能选一个目标实例", "提示"); return; }
            var batch = (batchCombo?.SelectedItem as ComboBoxItem)?.Tag as string ?? "";
            if (restoreMode && string.IsNullOrEmpty(batch))
            { MessageBox.Show("没有可用的备份批次", "提示"); return; }

            go.IsEnabled = false;
            status.Text = "处理中…";
            log.Clear();
            var whatArg = string.Join(",", what);
            // 多实例共用一个批次目录，方便还原时一次选全
            var sharedTag = "batch=oneclick-" + DateTime.Now.ToString("yyyyMMdd-HHmmss");
            try
            {
                await Task.Run(() =>
                {
                    foreach (var n in chosen)
                    {
                        string exe, args;
                        if (restoreMode)
                        {
                            exe = "python";
                            args = $"\"{Paths.BackupPy}\" restore --from \"{batch}\" " +
                                   $"--to \"{n}\" --what \"{whatArg}\" --force";
                        }
                        else
                        {
                            exe = "python";
                            args = $"\"{Paths.BackupPy}\" save --from \"{n}\" " +
                                   $"--tag \"{sharedTag}\" --what \"{whatArg}\" --force";
                        }
                        Log($"=== {n} ===");
                        var (code, outp) = RunProc(exe, args);
                        foreach (var line in outp.Split('\n')) if (line.Trim().Length > 0) Log("  " + line.TrimEnd('\r'));
                        Log(code == 0 ? "  ✓ 完成" : $"  ✗ 退出码 {code}");
                    }
                });
                status.Text = "完成";
            }
            catch (Exception ex) { status.Text = "出错"; Log("异常: " + ex.Message); }
            finally { go.IsEnabled = true; }
        };

        win.Content = root;
        win.ShowDialog();
    }

    static (int, string) RunProc(string exe, string args)
    {
        try
        {
            var psi = new System.Diagnostics.ProcessStartInfo
            {
                FileName = exe, Arguments = args,
                UseShellExecute = false, RedirectStandardOutput = true,
                RedirectStandardError = true, CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            using var p = System.Diagnostics.Process.Start(psi);
            if (p is null) return (-1, "无法启动 python");
            var so = p.StandardOutput.ReadToEnd();
            var se = p.StandardError.ReadToEnd();
            p.WaitForExit(120000);
            return (p.ExitCode, so + (se.Length > 0 ? "\n" + se : ""));
        }
        catch (Exception e) { return (-1, e.ToString()); }
    }
}
