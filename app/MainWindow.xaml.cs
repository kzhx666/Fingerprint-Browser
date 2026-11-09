using System.Collections.ObjectModel;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Text.Json;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Data;
using System.Windows.Input;
using System.Windows.Media;

namespace KZManager;

public partial class MainWindow : Window
{
    readonly ObservableCollection<InstanceRow> _rows = new();
    readonly Dictionary<string, Process> _launched = new();
    AppConfig _cfg = new();
    string _filter = "";
    string _sortCol = "";
    bool _sortAsc = true;

    public MainWindow()
    {
        InitializeComponent();
        _cfg = Store.LoadConfig();
        Theme.Apply(_cfg.theme);
        BtnTheme.Content = Theme.IsLight ? "☾" : "☀";
        RootLabel.Text = Paths.Root;
        Grid.ItemsSource = _rows;
        Grid.AllowDrop = true;
        // 拖拽过程中强制显示四向箭头，别用系统默认的拖放光标
        Grid.GiveFeedback += (_, e) =>
        {
            e.UseDefaultCursors = false;
            Mouse.SetCursor(Cursors.Hand);
            e.Handled = true;
        };
        Grid.PreviewMouseLeftButtonDown += Grid_PreviewMouseLeftButtonDown;
        Grid.PreviewMouseMove += Grid_PreviewMouseMove;
        Grid.Drop += Grid_Drop;
        Grid.DragOver += Grid_DragOver;
        Grid.DragLeave += (_, _) => DropLine.Visibility = Visibility.Collapsed;
        Loaded += (_, _) =>
        {
            RefreshList();
            // 诊断用：--edit-first 启动后直接打开第一个实例的编辑框
            if (Environment.GetCommandLineArgs().Contains("--edit-first") && _rows.Count > 0)
            {
                Grid.SelectedIndex = 0;
                Dispatcher.BeginInvoke(new Action(() => BtnEdit_Click(this, new RoutedEventArgs())));
            }
        };
    }

    // ---------------------------------------------------------------- 列表

    void RefreshList()
    {
        var running = ProcessQuery.ChromeDataDirs();
        var nodes = Store.LoadNodes();
        var nodeByLink = new Dictionary<string, string>();
        foreach (var n in nodes)
            if (!string.IsNullOrEmpty(n.link)) nodeByLink[n.link] = n.label;

        var items = new List<InstanceRow>();
        // 没有列排序时用「自定义顺序」，点列头才临时按列排
        var source = string.IsNullOrEmpty(_sortCol)
            ? Store.ListInstancesOrdered(_cfg)
            : Store.ListInstances();
        foreach (var name in source)
        {
            var p = Store.LoadProfile(name);
            if (!string.IsNullOrEmpty(_filter))
            {
                var hay = $"{name} {p.tags} {p.platform} {p.timezone} {p.seed}".ToLowerInvariant();
                if (!hay.Contains(_filter)) continue;
            }

            var dataDir = Paths.DataDir(name).TrimEnd('\\');
            var isRunning = running.ContainsKey(dataDir);

            items.Add(new InstanceRow
            {
                Name = name,
                Tags = p.tags,
                Platform = p.platform,
                Timezone = p.timezone,
                ProxyDisplay = ProxyText(p, nodeByLink),
                Seed = p.seed,
                Rows = p.rows,
                Status = isRunning ? $"运行中 ({running[dataDir]})" : "未运行",
                Dir = Paths.InstanceDir(name),
                Running = isRunning
            });
        }

        if (!string.IsNullOrEmpty(_sortCol))
        {
            items = _sortCol switch
            {
                "Seed" => _sortAsc ? items.OrderBy(x => Num(x.Seed)).ToList()
                                   : items.OrderByDescending(x => Num(x.Seed)).ToList(),
                "Rows" => _sortAsc ? items.OrderBy(x => Num(x.Rows)).ToList()
                                   : items.OrderByDescending(x => Num(x.Rows)).ToList(),
                "Name" => _sortAsc ? items.OrderBy(x => x.Name, StringComparer.OrdinalIgnoreCase).ToList()
                                   : items.OrderByDescending(x => x.Name, StringComparer.OrdinalIgnoreCase).ToList(),
                "Tags" => _sortAsc ? items.OrderBy(x => x.Tags, StringComparer.OrdinalIgnoreCase).ToList()
                                   : items.OrderByDescending(x => x.Tags, StringComparer.OrdinalIgnoreCase).ToList(),
                "Platform" => _sortAsc ? items.OrderBy(x => x.Platform, StringComparer.OrdinalIgnoreCase).ToList()
                                       : items.OrderByDescending(x => x.Platform, StringComparer.OrdinalIgnoreCase).ToList(),
                "Timezone" => _sortAsc ? items.OrderBy(x => x.Timezone, StringComparer.OrdinalIgnoreCase).ToList()
                                       : items.OrderByDescending(x => x.Timezone, StringComparer.OrdinalIgnoreCase).ToList(),
                _ => items
            };
        }

        _rows.Clear();
        foreach (var r in items) _rows.Add(r);

        EmptyState.Visibility = _rows.Count == 0 ? Visibility.Visible : Visibility.Collapsed;
        var runCount = _rows.Count(x => x.Running);
        var suffix = string.IsNullOrEmpty(_filter) ? "" : $"（已过滤，共 {Store.ListInstances().Count} 个）";
        CountText.Text = $"显示 {_rows.Count} 个{suffix} · 运行中 {runCount} 个";
        UpdateButtons();
    }

    static long Num(string s) => long.TryParse(s, out var v) ? v : 0;

    static string ProxyText(Profile p, Dictionary<string, string> nodeByLink)
    {
        if (!string.IsNullOrEmpty(p.proxy)) return p.proxy;
        if (string.IsNullOrEmpty(p.node)) return "直连";

        var parts = p.node.Split('|', StringSplitOptions.RemoveEmptyEntries)
                           .Select(x => x.Trim()).Where(x => x.Length > 0).ToList();
        if (parts.Count > 1) return $"节点池 {parts.Count} 个";

        var one = parts[0];
        if (nodeByLink.TryGetValue(one, out var label)) return label;
        var scheme = one.Contains("://") ? one[..one.IndexOf("://", StringComparison.Ordinal)] : one;
        return scheme + " 节点";
    }

    InstanceRow? Selected => Grid.SelectedItem as InstanceRow;

    void UpdateButtons()
    {
        var sel = Selected;
        BtnStart.IsEnabled = sel is { Running: false };
        BtnStop.IsEnabled = sel is { Running: true };
        BtnEdit.IsEnabled = sel is not null;
        BtnClone.IsEnabled = sel is not null;
        BtnMore.IsEnabled = sel is not null;
    }

    void SetStatus(string s) => StatusText.Text = s;

    // ---------------------------------------------------------------- 事件

    // ---------------------------------------------------------------- 拖拽排序

    Point _dragStart;
    InstanceRow? _dragRow;
    bool _dragging;

    void Grid_PreviewMouseLeftButtonDown(object sender, MouseButtonEventArgs e)
    {
        _dragStart = e.GetPosition(null);
        _dragging = false;
        _dragRow = null;
        // 只有按在第一列（拖动把手）才允许拖，点其它地方只是选中
        var pt = e.GetPosition(Grid);
        double gripW = Grid.Columns.Count > 0 ? Grid.Columns[0].ActualWidth : 0;
        if (gripW > 0 && pt.X <= gripW + 4)
            _dragRow = RowAt(pt);
    }

    void Grid_PreviewMouseMove(object sender, MouseEventArgs e)
    {
        if (e.LeftButton != MouseButtonState.Pressed || _dragRow is null || _dragging) return;
        var pos = e.GetPosition(null);
        if (Math.Abs(pos.X - _dragStart.X) < 14 && Math.Abs(pos.Y - _dragStart.Y) < 14) return;

        // 有搜索过滤时不让拖，否则重排会乱
        if (!string.IsNullOrEmpty(_filter))
        {
            SetStatus("请先清空搜索框再拖动排序");
            _dragRow = null;
            return;
        }
        _dragging = true;
        Mouse.OverrideCursor = Cursors.Hand;
        try
        {
            DragDrop.DoDragDrop(Grid, _dragRow, DragDropEffects.Move);
        }
        finally
        {
            Mouse.OverrideCursor = null;
        }
        _dragging = false;
        _dragRow = null;
    }

    /// <summary>算出插入位置的 Y 坐标（在 Grid 坐标系里）。</summary>
    double InsertionY(double y)
    {
        for (int i = 0; i < _rows.Count; i++)
        {
            if (Grid.ItemContainerGenerator.ContainerFromIndex(i) is not DataGridRow row) continue;
            var p = row.TranslatePoint(new Point(0, 0), Grid);
            if (y < p.Y + row.ActualHeight / 2) return p.Y;
            if (y < p.Y + row.ActualHeight) return p.Y + row.ActualHeight;
        }
        if (_rows.Count > 0 &&
            Grid.ItemContainerGenerator.ContainerFromIndex(_rows.Count - 1) is DataGridRow last)
            return last.TranslatePoint(new Point(0, last.ActualHeight), Grid).Y;
        return y;
    }

    void Grid_DragOver(object sender, DragEventArgs e)
    {
        if (e.Data.GetData(typeof(InstanceRow)) is not InstanceRow || !string.IsNullOrEmpty(_filter))
        {
            e.Effects = DragDropEffects.None;
            DropLine.Visibility = Visibility.Collapsed;
            e.Handled = true;
            return;
        }
        e.Effects = DragDropEffects.Move;
        var y = InsertionY(e.GetPosition(Grid).Y);
        DropLine.Margin = new Thickness(6, Math.Max(0, y - 1), 6, 0);
        DropLine.Visibility = Visibility.Visible;
        e.Handled = true;
    }

    void Grid_Drop(object sender, DragEventArgs e)
    {
        DropLine.Visibility = Visibility.Collapsed;
        if (e.Data.GetData(typeof(InstanceRow)) is not InstanceRow src) return;
        var target = RowAt(e.GetPosition(Grid));
        if (target is null || ReferenceEquals(src, target)) return;

        var from = _rows.IndexOf(src);
        var to = _rows.IndexOf(target);
        if (from < 0 || to < 0) return;

        // 落到目标行上方还是下方，按鼠标位置判断
        var rowEl = (DataGridRow)Grid.ItemContainerGenerator.ContainerFromItem(target);
        if (rowEl is not null)
        {
            var p = e.GetPosition(rowEl);
            if (p.Y > rowEl.ActualHeight / 2) to++;
        }
        if (to > from) to--;
        if (to < 0) to = 0;
        if (to == from) return;

        _rows.Move(from, to);
        _sortCol = "";                       // 手动拖过就回到自定义顺序
        foreach (var c in Grid.Columns) c.SortDirection = null;
        Store.SaveOrder(_cfg, _rows.Select(x => x.Name));
        SetStatus($"已调整顺序：{src.Name} → 第 {to + 1} 位");
    }

    InstanceRow? RowAt(Point pt)
    {
        // 先走视觉树
        var hit = Grid.InputHitTest(pt) as DependencyObject;
        while (hit is not null and not DataGridRow)
            hit = VisualTreeHelper.GetParent(hit);
        if (hit is DataGridRow r && r.Item is InstanceRow ir) return ir;

        // 兜底：按 Y 坐标反算行号
        for (int i = 0; i < _rows.Count; i++)
        {
            if (Grid.ItemContainerGenerator.ContainerFromIndex(i) is not DataGridRow row)
                continue;
            try
            {
                var p = row.TranslatePoint(new Point(0, 0), Grid);
                if (pt.Y >= p.Y && pt.Y <= p.Y + row.ActualHeight) return _rows[i];
            }
            catch { }
        }
        return null;
    }

    void MoveSelected(int delta)
    {
        var sel = Selected;
        if (sel is null) return;
        if (!string.IsNullOrEmpty(_filter))
        {
            SetStatus("请先清空搜索框再调整顺序");
            return;
        }
        var from = _rows.IndexOf(sel);
        var to = from + delta;
        if (from < 0 || to < 0 || to >= _rows.Count) return;

        _rows.Move(from, to);
        _sortCol = "";
        foreach (var c in Grid.Columns) c.SortDirection = null;
        Store.SaveOrder(_cfg, _rows.Select(x => x.Name));
        Grid.SelectedItem = sel;
        SetStatus($"已调整顺序：{sel.Name} → 第 {to + 1} 位");
    }

    void SearchBox_TextChanged(object sender, TextChangedEventArgs e)
    {
        SearchHint.Visibility = string.IsNullOrEmpty(SearchBox.Text)
            ? Visibility.Visible : Visibility.Collapsed;
        _filter = SearchBox.Text.Trim().ToLowerInvariant();
        RefreshList();
    }

    void Grid_SelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateButtons();

    void Grid_Sorting(object sender, DataGridSortingEventArgs e)
    {
        e.Handled = true;
        var col = e.Column.SortMemberPath;
        if (string.IsNullOrEmpty(col)) return;
        if (_sortCol == col) _sortAsc = !_sortAsc;
        else { _sortCol = col; _sortAsc = true; }

        foreach (var c in Grid.Columns) c.SortDirection = null;
        e.Column.SortDirection = _sortAsc
            ? System.ComponentModel.ListSortDirection.Ascending
            : System.ComponentModel.ListSortDirection.Descending;
        RefreshList();
    }

    void Grid_MouseDoubleClick(object sender, MouseButtonEventArgs e)
    {
        if (Selected is { Running: false }) StartInstance(Selected);
    }

    void BtnRefresh_Click(object sender, RoutedEventArgs e) { RefreshList(); SetStatus("已刷新"); }

    void BtnTheme_Click(object sender, RoutedEventArgs e)
    {
        var next = Theme.Toggle();
        BtnTheme.Content = next == "light" ? "☾" : "☀";
        _cfg.theme = next;
        Store.SaveConfig(_cfg);
        SetStatus(next == "light" ? "已切换到浅色主题" : "已切换到深色主题");
    }

    void BtnNew_Click(object sender, RoutedEventArgs e)
    {
        var name = InputDialog.Ask(this, "新建实例", "实例名称（同时作为目录名）", "新实例");
        if (string.IsNullOrWhiteSpace(name)) return;
        name = name.Trim();
        if (Directory.Exists(Paths.InstanceDir(name)))
        {
            MessageBox.Show("同名实例已存在。", "新建实例", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        var p = new Profile
        {
            name = name,
            seed = Random.Shared.Next(10000, 999999).ToString(),
            platform = "windows",
            timezone = "America/New_York",
            rows = _cfg.defaultRows.ToString()
        };

        var dlg = new EditDialog(p, true) { Owner = this };
        if (dlg.ShowDialog() != true)
        {
            try { Directory.Delete(Paths.InstanceDir(name), true); } catch { }
            return;
        }
        Store.SaveProfile(name, dlg.Result);
        RefreshList();
        SetStatus($"已创建实例 {name}");
    }

    void BtnEdit_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;
        var p = Store.LoadProfile(sel.Name);
        var dlg = new EditDialog(p, false) { Owner = this };
        if (dlg.ShowDialog() != true) return;
        Store.SaveProfile(sel.Name, dlg.Result);
        RefreshList();
        SetStatus($"已保存 {sel.Name}");
    }

    void BtnClone_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;
        var name = InputDialog.Ask(this, "克隆实例", "新实例名称", sel.Name + "-副本");
        if (string.IsNullOrWhiteSpace(name)) return;
        name = name.Trim();
        if (Directory.Exists(Paths.InstanceDir(name)))
        {
            MessageBox.Show("同名实例已存在。", "克隆实例", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        var srcData = Paths.DataDir(sel.Name);
        var withData = false;
        if (Directory.Exists(srcData))
        {
            long size = 0;
            try
            {
                size = new DirectoryInfo(srcData).EnumerateFiles("*", SearchOption.AllDirectories)
                                                 .Sum(f => f.Length);
            }
            catch { }
            var r = MessageBox.Show(
                $"要连数据目录一起复制吗？\n\n是 = 完整克隆（书签/密码/历史/扩展/登录态，约 {size / 1024 / 1024} MB）\n否 = 只复制配置\n\n完整克隆会跳过缓存目录。",
                "克隆实例", MessageBoxButton.YesNoCancel, MessageBoxImage.Question);
            if (r == MessageBoxResult.Cancel) return;
            withData = r == MessageBoxResult.Yes;
        }

        SetStatus(withData ? $"正在完整克隆 {sel.Name} …" : "正在克隆配置 …");
        var args = new List<string> { "clone", "--from", sel.Name, "--to", name, "--force" };
        if (withData) args.Add("--with-data");
        var (code, so, se) = Py.Run(Paths.SnapshotPy, args.ToArray());
        if (code != 0)
        {
            MessageBox.Show($"克隆失败：\n\n{so}\n{se}", "克隆", MessageBoxButton.OK, MessageBoxImage.Error);
            SetStatus("克隆失败");
            return;
        }
        RefreshList();
        SetStatus($"已克隆：{sel.Name} → {name}");
    }

    void BtnMore_Click(object sender, RoutedEventArgs e)
    {
        if (sender is Button b && b.ContextMenu is not null)
        {
            b.ContextMenu.PlacementTarget = b;
            b.ContextMenu.IsOpen = true;
        }
    }

    // ---------------------------------------------------------------- 启动 / 停止

    void BtnStart_Click(object sender, RoutedEventArgs e)
    {
        if (Selected is { } r) StartInstance(r);
    }

    void StartInstance(InstanceRow row)
    {
        if (!File.Exists(_cfg.chromeExe))
        {
            MessageBox.Show($"找不到 chrome.exe：\n{_cfg.chromeExe}\n\n请检查 manager.json",
                            "启动", MessageBoxButton.OK, MessageBoxImage.Error);
            return;
        }

        var p = Store.LoadProfile(row.Name);
        Directory.CreateDirectory(Paths.DataDir(row.Name));

        // 1. 节点：库里存的是名字就解析成链接
        if (!string.IsNullOrEmpty(p.node) && !p.node.Contains("://"))
        {
            var (c, so, se) = Py.Run(Paths.NodeProxy, "resolve", "--spec", p.node);
            var link = so.Trim();
            if (c != 0 || !link.Contains("://"))
            {
                MessageBox.Show($"节点「{p.node}」在节点库里找不到，无法解析。\n\n请重新选一个节点。",
                                "启动", MessageBoxButton.OK, MessageBoxImage.Warning);
                return;
            }
            p.node = link;
        }

        // 2. WebRTC pref
        var policy = Fingerprint.WebRtcPolicy(p) switch
        {
            "strict" => "disable_non_proxied_udp",
            "public" => "default_public_interface_only",
            "off" => "default",
            _ => ""
        };
        if (policy.Length > 0 && File.Exists(Paths.SetPref))
        {
            Directory.CreateDirectory(Paths.ProfileDir(row.Name));
            Py.Run(Paths.SetPref, "--profile", Paths.ProfileDir(row.Name),
                   "--set", $"webrtc.ip_handling_policy={policy}");
        }

        // 3. 节点代理
        var args = Fingerprint.BuildArgs(row.Name, p, _cfg.chromeExe);
        if (!string.IsNullOrEmpty(p.node))
        {
            var port = 10800 + Math.Abs(row.Name.GetHashCode() % 100);
            SetStatus($"启动节点代理（端口 {port}）…");
            var (c, so, se) = Py.Run(Paths.NodeProxy, "start",
                                     "--node", p.node, "--port", port.ToString(),
                                     "--workdir", Paths.InstanceDir(row.Name));
            if (c != 0)
            {
                MessageBox.Show($"节点代理启动失败：\n\n{so}\n{se}", "启动",
                                MessageBoxButton.OK, MessageBoxImage.Error);
                SetStatus("节点代理启动失败");
                return;
            }
            args = System.Text.RegularExpressions.Regex.Replace(
                args, @"--proxy-server=\S+", "");
            args += $" --proxy-server=socks5://127.0.0.1:{port}";
        }

        // 4. 启动
        try
        {
            var psi = new ProcessStartInfo(_cfg.chromeExe, args)
            {
                UseShellExecute = false,
                WorkingDirectory = Path.GetDirectoryName(_cfg.chromeExe) ?? ""
            };
            var proc = Process.Start(psi);
            if (proc is not null) _launched[row.Name] = proc;
            SetStatus($"已启动 {row.Name}");
            Task.Delay(1200).ContinueWith(_ => Dispatcher.Invoke(RefreshList));
        }
        catch (Exception ex)
        {
            MessageBox.Show($"启动失败：{ex.Message}", "启动", MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    void BtnStop_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;

        var dataDir = Paths.DataDir(sel.Name).TrimEnd('\\');
        var killed = ProcessQuery.KillChromeByDataDir(dataDir);

        if (File.Exists(Paths.NodeProxy))
            Py.Run(Paths.NodeProxy, "stop", "--workdir", Paths.InstanceDir(sel.Name));

        SetStatus($"已停止 {sel.Name}（结束 {killed} 个进程）");
        RefreshList();
    }

    // ---------------------------------------------------------------- 菜单

    string? NeedSelection()
    {
        var sel = Selected;
        if (sel is null)
            MessageBox.Show("先在列表里选中一个实例。", "提示", MessageBoxButton.OK, MessageBoxImage.Information);
        return sel?.Name;
    }

    void MenuMoveUp_Click(object sender, RoutedEventArgs e) => MoveSelected(-1);
    void MenuMoveDown_Click(object sender, RoutedEventArgs e) => MoveSelected(1);

    void MenuResetOrder_Click(object sender, RoutedEventArgs e)
    {
        _sortCol = "";
        foreach (var c in Grid.Columns) c.SortDirection = null;
        var r = MessageBox.Show("恢复成按名称字母序？（自定义顺序会被清空）", "恢复默认顺序",
                                MessageBoxButton.YesNo, MessageBoxImage.Question);
        if (r != MessageBoxResult.Yes) return;
        Store.SaveOrder(_cfg, Array.Empty<string>());
        RefreshList();
        SetStatus("已恢复默认顺序");
    }

    void MenuOpenDir_Click(object sender, RoutedEventArgs e)
    {
        var n = NeedSelection();
        if (n is null) return;
        Process.Start(new ProcessStartInfo("explorer.exe", Paths.InstanceDir(n)) { UseShellExecute = true });
    }

    void MenuSelfTest_Click(object sender, RoutedEventArgs e)
    {
        var n = NeedSelection();
        if (n is null) return;
        if (!File.Exists(Paths.SelfTestHtml))
        {
            MessageBox.Show($"找不到自检页：\n{Paths.SelfTestHtml}", "指纹自检",
                            MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        var p = Store.LoadProfile(n);
        var url = Fingerprint.SelfTestUrl(n, p);
        var args = Fingerprint.BuildArgs(n, p, _cfg.chromeExe) + $" \"{url}\"";
        try
        {
            Process.Start(new ProcessStartInfo(_cfg.chromeExe, args)
            {
                UseShellExecute = false,
                WorkingDirectory = Path.GetDirectoryName(_cfg.chromeExe) ?? ""
            });
            SetStatus($"已打开指纹自检：{n}");
        }
        catch (Exception ex)
        {
            MessageBox.Show($"打开失败：{ex.Message}", "指纹自检",
                            MessageBoxButton.OK, MessageBoxImage.Error);
        }
    }

    void MenuHealth_Click(object sender, RoutedEventArgs e)
    {
        var nodes = Store.LoadNodes();
        var nodeByLink = new Dictionary<string, string>();
        foreach (var n in nodes)
            if (!string.IsNullOrEmpty(n.link)) nodeByLink[n.link] = n.label;

        var sb = new StringBuilder();
        int ok = 0, bad = 0;
        var all = Store.ListInstances();
        var seedMap = new Dictionary<string, List<string>>();

        foreach (var name in all)
        {
            var p = Store.LoadProfile(name);
            if (!string.IsNullOrEmpty(p.seed))
            {
                if (!seedMap.TryGetValue(p.seed, out var l)) seedMap[p.seed] = l = new();
                l.Add(name);
            }
        }

        foreach (var name in all)
        {
            var p = Store.LoadProfile(name);
            var issues = new List<string>();
            var notes = new List<string>();

            if (string.IsNullOrEmpty(p.seed)) issues.Add("没有 seed（指纹不会生效）");
            else if (!p.seed.All(char.IsDigit)) issues.Add($"seed 不是数字：{p.seed}");
            else
            {
                notes.Add($"seed {p.seed} / {p.platform} / {p.timezone}");
                if (seedMap.TryGetValue(p.seed, out var dup) && dup.Count > 1)
                    issues.Add($"seed 与 {string.Join(", ", dup.Where(x => x != name))} 重复");
            }

            if (!string.IsNullOrEmpty(p.node) && !p.node.Contains("://"))
                issues.Add("节点存的是名字，启动时会自动解析（若库里没有会失败）");

            var dd = Paths.DataDir(name);
            if (!Directory.Exists(dd)) issues.Add("数据目录不存在（首次启动会自动创建）");
            else
            {
                try
                {
                    var mb = new DirectoryInfo(dd).EnumerateFiles("*", SearchOption.AllDirectories)
                                                  .Sum(f => f.Length) / 1024 / 1024;
                    notes.Add($"数据 {mb} MB");
                }
                catch { }
            }

            if (issues.Count > 0) { bad++; sb.AppendLine($"[{name}]  有问题"); foreach (var i in issues) sb.AppendLine("    ! " + i); }
            else { ok++; sb.AppendLine($"[{name}]  正常"); }
            foreach (var nt in notes) sb.AppendLine("    - " + nt);
            sb.AppendLine();
        }

        sb.Insert(0, $"正常 {ok} / 有问题 {bad} / 共 {all.Count}\n\n");
        if (!File.Exists(_cfg.chromeExe)) sb.AppendLine($"全局：找不到 chrome.exe -> {_cfg.chromeExe}");
        else sb.AppendLine($"全局：chrome.exe 正常（{new FileInfo(_cfg.chromeExe).Length / 1024 / 1024} MB）");

        MessageBox.Show(sb.ToString(), "健康巡检");
        SetStatus($"健康巡检：正常 {ok} / 有问题 {bad}");
    }

    void MenuBackup_Click(object sender, RoutedEventArgs e)
    {
        var n = NeedSelection();
        if (n is null) return;
        var op = InputDialog.Ask(this, "备份",
            "输入操作：\n  s  备份当前实例\n  v  校验书签数量\n  l  列出全部备份\n  p  清理旧备份",
            "v");
        if (string.IsNullOrWhiteSpace(op)) return;
        op = op.Trim().ToLowerInvariant();

        (int code, string so, string se) r = op switch
        {
            "s" => Py.Run(Paths.BackupPy, "save", "--from", n, "--tag", "manual"),
            "l" => Py.Run(Paths.BackupPy, "list"),
            "p" => Py.Run(Paths.BackupPy, "prune", "--keep", "20"),
            _ => Py.Run(Paths.BackupPy, "verify", "--instance", n)
        };
        MessageBox.Show((r.so + r.se).Trim(), "备份");
        SetStatus(op == "s" ? $"已备份 {n}" : "备份操作完成");
    }

    void MenuSnapshot_Click(object sender, RoutedEventArgs e)
    {
        var n = NeedSelection();
        if (n is null) return;
        var snap = InputDialog.Ask(this, "创建快照", "快照名称", DateTime.Now.ToString("MMdd-HHmm"));
        if (string.IsNullOrWhiteSpace(snap)) return;
        SetStatus($"正在创建快照 {snap} …");
        var (c, so, se) = Py.Run(Paths.SnapshotPy, "create", "--from", n, "--name", snap.Trim(), "--force");
        MessageBox.Show((so + se).Trim(), c == 0 ? "快照已创建" : "快照失败");
        SetStatus(c == 0 ? $"快照已创建：{snap}" : "快照失败");
    }

    void MenuRestore_Click(object sender, RoutedEventArgs e)
    {
        var n = NeedSelection();
        if (n is null) return;
        var (c0, list, _) = Py.Run(Paths.SnapshotPy, "list");
        if (c0 != 0) { MessageBox.Show(list, "快照列表"); return; }
        var snap = InputDialog.Ask(this, "从快照还原",
            $"把快照还原到「{n}」（会先自动备份当前状态）\n\n{list}", "");
        if (string.IsNullOrWhiteSpace(snap)) return;
        SetStatus($"正在还原快照 {snap.Trim()} …");
        var (c, so, se) = Py.Run(Paths.SnapshotPy, "restore",
                                 "--name", snap.Trim(), "--to", n, "--new-seed");
        MessageBox.Show((so + se).Trim(), c == 0 ? "已还原" : "还原失败");
        RefreshList();
    }

    void MenuSync_Click(object sender, RoutedEventArgs e)
    {
        var n = NeedSelection();
        if (n is null) return;
        var src = InputDialog.Ask(this, "同步数据",
            $"把哪个实例的数据同步到「{n}」？\n（书签 / 历史 / 密码 / 扩展）", "");
        if (string.IsNullOrWhiteSpace(src)) return;
        src = src.Trim();
        if (!Store.ListInstances().Contains(src))
        {
            MessageBox.Show($"实例不存在：{src}", "同步", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        SetStatus("同步前自动备份 …");
        Py.Run(Paths.BackupPy, "save", "--from", n, "--tag", "auto");
        SetStatus($"正在同步 {src} → {n} …");
        var (c, so, se) = Py.Run(Paths.SyncPy, "--from", src, "--to", n, "--what", "all");
        MessageBox.Show((so + se).Trim(), c == 0 ? "同步完成" : "同步失败");
        RefreshList();
    }

    void MenuNodes_Click(object sender, RoutedEventArgs e)
    {
        var (c, so, se) = Py.Run(Paths.NodeProxy, "nodes", "--action", "list");
        var body = (so + se).Trim();
        if (body.Length == 0) body = "节点库是空的。";

        var r = MessageBox.Show(
            body + "\n\n要不要拉取订阅入库？（是 = 输入订阅链接）",
            "节点库", MessageBoxButton.YesNo, MessageBoxImage.Question);
        if (r != MessageBoxResult.Yes) return;

        var url = InputDialog.Ask(this, "订阅链接", "粘贴订阅地址（http/https）", "");
        if (string.IsNullOrWhiteSpace(url)) return;
        SetStatus("正在拉取订阅 …");
        var (c2, so2, se2) = Py.Run(Paths.NodeProxy, "nodes", "--action", "import", "--url", url.Trim());
        MessageBox.Show((so2 + se2).Trim(), c2 == 0 ? "订阅" : "订阅失败");
        RefreshList();
    }
}
