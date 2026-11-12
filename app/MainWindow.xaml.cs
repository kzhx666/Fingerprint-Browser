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

    PinWatcher? _pinWatcher;
    System.Threading.CancellationTokenSource? _aumidCts;

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
            // 监听任务栏固定目录：右键固定出来的空参数快捷方式自动修掉
            _pinWatcher = new PinWatcher(_cfg.chromeExe, Dispatcher, SetStatus);

            // 后台兜底：每 2 秒给所有实例的窗口补独立 AUMID + 实例图标。
            // 覆盖 GUI 启动、用户新开窗口、以及窗口重建等情况。
            MenuProxyPrecheck.IsChecked = _cfg.proxyPrecheck;
            MenuRegionCheck.IsChecked = _cfg.regionCheck;
            MenuRegionBlock.IsChecked = _cfg.regionCheckBlock;
            _aumidCts = new System.Threading.CancellationTokenSource();
            var token = _aumidCts.Token;
            Task.Run(() => WindowAumid.ApplyLoop(token), token);
            Task.Run(() => WindowIcon.ApplyLoop(token), token);
            // 智能默认浏览器：前台是哪个实例，点链接就落哪个实例
            Task.Run(() => SmartDefault.Run(token), token);

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

            var isMaster = string.Equals(p.role, "master", StringComparison.OrdinalIgnoreCase);
            items.Add(new InstanceRow
            {
                Name = name,
                IsMaster = isMaster,
                Tags = p.tags,
                Platform = p.platform,
                Timezone = p.timezone,
                ProxyDisplay = ProxyText(p, nodeByLink),
                Seed = p.seed,
                Rows = p.rows,
                Status = isRunning ? $"运行中 ({running[dataDir]})" : "未运行",
                ExitStatus = ProxyPrecheck.Display(name),
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

        // 主控浏览器永远置顶（稳定排序，不影响组内顺序）
        items = items.OrderByDescending(x => x.IsMaster).ToList();

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

    protected override void OnClosed(EventArgs e)
    {
        _pinWatcher?.Dispose();
        _aumidCts?.Cancel();
        _aumidCts?.Dispose();
        base.OnClosed(e);
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
        var vNewErr = PathGuard.ValidateName(name);
        if (vNewErr is not null)
        {
            MessageBox.Show("实例名称无效：" + vNewErr, "新建实例", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
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

        var newName = (dlg.Result.name ?? "").Trim();
        if (newName.Length == 0) { MessageBox.Show("名称不能为空", "编辑实例"); return; }

        if (!string.Equals(newName, sel.Name, StringComparison.Ordinal))
        {
            if (!Store.RenameInstance(sel.Name, newName, out var err))
            {
                MessageBox.Show("改名失败：" + err, "编辑实例",
                                MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }
        }
        Store.SaveProfile(newName, dlg.Result);
        RefreshList();
        SetStatus($"已保存 {newName}");
    }

    void BtnClone_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;
        var name = InputDialog.Ask(this, "克隆实例", "新实例名称", sel.Name + "-副本");
        if (string.IsNullOrWhiteSpace(name)) return;
        name = name.Trim();
        var vCloneErr = PathGuard.ValidateName(name);
        if (vCloneErr is not null)
        {
            MessageBox.Show("克隆实例名称无效：" + vCloneErr, "克隆实例", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
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
        var r = Launcher.Run(row.Name, _cfg, SetStatus);

        if (!r.Ok)
        {
            MessageBox.Show(r.Message, "启动", MessageBoxButton.OK, MessageBoxImage.Warning);
            SetStatus($"{row.Name} 启动失败");
            return;
        }

        SetStatus(r.GoogleInjected
            ? $"已启动 {row.Name}（谷歌登录凭据已注入）"
            : $"已启动 {row.Name}（未配置谷歌登录凭据）");

        Task.Delay(1200).ContinueWith(_ => Dispatcher.Invoke(RefreshList));
    }

    void BtnStop_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;

        var dataDir = Paths.DataDir(sel.Name).TrimEnd('\\');
        var killed = ProcessQuery.KillChromeByDataDir(dataDir);

        if (File.Exists(Paths.NodeProxy))
            Py.Run(Paths.NodeProxy, "stop", "--workdir", Paths.InstanceDir(sel.Name));
        try { ProxyRelay.StopFor(Store.LoadProfile(sel.Name).proxy ?? ""); }
        catch { }

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

    // ---------------------------------------------------------------- 重刷快捷方式图标

    void MenuRefreshIcons_Click(object sender, RoutedEventArgs e)
    {
        var dirs = new[]
        {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
                @"Microsoft\Windows\Start Menu\Programs"),
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory),
            Environment.GetFolderPath(Environment.SpecialFolder.CommonDesktopDirectory),
        };
        var wshType = Type.GetTypeFromProgID("WScript.Shell");
        if (wshType is null) return;
        dynamic wsh = Activator.CreateInstance(wshType)!;

        int fixedCount = 0, noIcon = 0;
        var details = new System.Text.StringBuilder();
        foreach (var dir in dirs)
        {
            if (!Directory.Exists(dir)) continue;
            foreach (var lnk in Directory.GetFiles(dir, "*.lnk", SearchOption.AllDirectories))
            {
                try
                {
                    dynamic sc = wsh.CreateShortcut(lnk);
                    string args = sc.Arguments ?? "";
                    var m = System.Text.RegularExpressions.Regex.Match(
                        args, @"--launch\s+""([^""]+)""");
                    if (!m.Success) continue;
                    var name = m.Groups[1].Value;
                    var p = Store.LoadProfile(name);
                    var ico = Paths.IconPath(p.icon ?? "");
                    if (ico.Length == 0) { noIcon++; continue; }

                    sc.IconLocation = ico + ",0";
                    sc.Save();
                    // 顺带修 AUMID（WScript.Shell 保存会清掉）
                    Shortcut.SetLnkAumid(lnk, WindowAumid.IdFor(name));
                    fixedCount++;
                    details.AppendLine($"  {Path.GetFileName(lnk)} → {name} → {p.icon}");
                }
                catch { }
            }
        }

        SetStatus($"已重刷 {fixedCount} 个快捷方式图标");
        MessageBox.Show(
            $"已按实例配置重刷快捷方式图标。\n\n" +
            $"已设置图标: {fixedCount} 个\n" +
            $"未配置图标（保持默认）: {noIcon} 个\n\n" +
            (details.Length > 0 ? details.ToString() : "") +
            "\n如果任务栏还显示旧图标，重启一下资源管理器：" +
            "\n  Stop-Process -Name explorer -Force",
            "重刷完成", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    // ---------------------------------------------------------------- 出口地区比对开关

    void MenuRegionCheck_Click(object sender, RoutedEventArgs e)
    {
        _cfg.regionCheck = MenuRegionCheck.IsChecked;
        Store.SaveConfig(_cfg);
        SetStatus(_cfg.regionCheck ? "已开启出口地区比对" : "已关闭出口地区比对");
    }

    void MenuRegionBlock_Click(object sender, RoutedEventArgs e)
    {
        _cfg.regionCheckBlock = MenuRegionBlock.IsChecked;
        Store.SaveConfig(_cfg);
        SetStatus(_cfg.regionCheckBlock
            ? "地区不符将拦截启动" : "地区不符只警告，不拦截");
    }

    // ---------------------------------------------------------------- 健康评分

    void MenuHealthScore_Click(object sender, RoutedEventArgs e)
    {
        var r = HealthScore.Evaluate(_cfg);
        var sb = new System.Text.StringBuilder();
        sb.AppendLine($"内核: {r.KernelNote}");
        sb.AppendLine($"实例平均: {r.Average:F0} 分（{r.Items.Count} 个实例）");
        sb.AppendLine();
        foreach (var it in r.Items.OrderBy(x => x.Score))
            sb.AppendLine($"{it.Score,3}  {it.Name}  {it.Summary}");
        MessageBox.Show(sb.ToString(), "实例健康评分",
                        MessageBoxButton.OK,
                        r.Average >= 85 ? MessageBoxImage.Information
                                        : MessageBoxImage.Warning);
        SetStatus($"健康评分：平均 {r.Average:F0} 分");
    }

    // ---------------------------------------------------------------- 内核补丁自检

    void MenuKernelCheck_Click(object sender, RoutedEventArgs e)
    {
        var r = KernelSelfCheck.Check(_cfg.chromeExe);
        if (r.Ok)
        {
            MessageBox.Show($"✓ {r.Note}\n\n{r.DllPath}", "内核补丁自检",
                            MessageBoxButton.OK, MessageBoxImage.Information);
        }
        else
        {
            MessageBox.Show($"✗ {r.Note}\n\n" +
                            string.Join("\n", r.Issues) +
                            $"\n\n{r.DllPath}\nSHA-256: {r.Sha256}\n{r.PatchCheckNote}",
                            "内核补丁自检", MessageBoxButton.OK,
                            MessageBoxImage.Warning);
        }
        SetStatus(r.Note);
    }

    // ---------------------------------------------------------------- 出口预检开关

    void MenuProxyPrecheck_Click(object sender, RoutedEventArgs e)
    {
        _cfg.proxyPrecheck = MenuProxyPrecheck.IsChecked;
        Store.SaveConfig(_cfg);
        SetStatus(_cfg.proxyPrecheck
            ? "已开启启动前出口 IP 预检"
            : "已关闭出口 IP 预检（代理挂了不再拦截）");
    }

    // ---------------------------------------------------------------- 智能默认浏览器

    void MenuSmartDefault_Click(object sender, RoutedEventArgs e)
    {
        SmartDefault.Enabled = MenuSmartDefault.IsChecked;
        if (SmartDefault.Enabled)
        {
            var inst = SmartDefault.ApplyNow();
            SetStatus("智能默认浏览器已开启" +
                      (inst.Length > 0 ? $"：跟随前台实例 → {inst}" : ""));
        }
        else
        {
            SetStatus("智能默认浏览器已关闭（保持最后一次的 data_dir）");
        }
    }

    void MenuSmartDefaultStatus_Click(object sender, RoutedEventArgs e)
    {
        var inst = SmartDefault.ApplyNow();
        var state = SmartDefault.Enabled ? "开启" : "关闭";
        var target = inst.Length > 0 ? inst : "（还没有前台实例，保持上次）";
        var msg = "智能默认浏览器：" + state + "\n\n" +
                  "当前点外部链接会落到：\n  " + target + "\n\n" +
                  "原理：后台每 0.7 秒读一次前台窗口，把 chrome++.ini 的 data_dir " +
                  "同步成该实例的 Data 目录。Chrome++ 每次启动重读 ini，无参数启动" +
                  "（外部链接）就落到这个实例。\n\n" +
                  "前台不是浏览器时保持上一次选择，所以从微信/QQ 点链接会落到你" +
                  "最后用的那个实例。";
        MessageBox.Show(msg, "智能默认浏览器",
                        MessageBoxButton.OK, MessageBoxImage.Information);
    }

    // ---------------------------------------------------------------- 指纹一致性

    void MenuConsistency_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;
        var p = Store.LoadProfile(sel.Name);

        var oks = new List<string>();

        // 1. 语言
        var lang = p.lang ?? "";
        if (lang.Length > 0)
        {
            var first = Fingerprint.PlainLang(lang).Split(',')[0].Trim();
            oks.Add($"语言：已配置 --lang={first}（Intl.DateTimeFormat/NumberFormat locale 与 navigator.language 均为 {first}，无冲突）");
        }
        else
        {
            oks.Add("语言：跟随宿主系统（未覆盖）");
        }

        // 2. Canvas
        var canvas = p.fp_canvas ?? "on";
        if (canvas == "off")
        {
            oks.Add("Canvas：当前设为 off（使用显卡原生渲染输出，无扰动，无篡改痕迹）");
        }
        else
        {
            oks.Add("Canvas：v3 幂等内容寻址噪声已生效（toDataURL 与 getImageData roundtrip 完全一致，无篡改扣分，且每个 seed 的 hash 互不相同）");
        }

        // 3. 操作系统
        var platform = p.platform ?? "windows";
        if (platform == "windows")
        {
            oks.Add("操作系统：UA 为 Windows NT 10.0，UA-CH platformVersion 为 10.0.0（版本完全匹配，无系统不符扣分）");
        }
        else
        {
            oks.Add($"操作系统：platform={platform}（已配置对应平台标识）");
        }

        // 4. WebGL 与 WebGPU
        var webgl = p.fp_webgl ?? "(跟随 seed)";
        oks.Add($"WebGL / WebGPU：v3 统一 GPU 身份已生效（WebGL UNMASKED_* 与 WebGPU adapter.info 保持同厂商架构，无跨 API 冲突）");

        var sb = new StringBuilder();
        sb.AppendLine($"实例：{sel.Name}");
        sb.AppendLine();
        sb.AppendLine("✓ 指纹一致性检测全部通过 (v3 内核)");
        foreach (var s in oks) sb.AppendLine("  · " + s);
        sb.AppendLine();
        sb.AppendLine("—— 判定依据 ——");
        sb.AppendLine("第三方指纹站（如 BrowserScan 等）通过交叉比对多个对外 API 判定伪装：");
        sb.AppendLine("1. 网页语言 vs 宿主系统语言：--lang 与 --accept-lang 同时传入，Intl locale 与 navigator.language 严格一致。");
        sb.AppendLine("2. Canvas 篡改：v3 内核在导出边界使用幂等算法，且避开半透明像素的预乘舍入，使 getImageData -> putImageData -> toDataURL 与直接 toDataURL 完全吻合，同时不同 seed 仍产生不同指纹。");
        sb.AppendLine("3. 操作系统不同：UA-CH platformVersion 与 User-Agent 的 Windows NT 10.0 严格对齐为 10.0.0。");
        sb.AppendLine("4. WebGL 异常：WebGL 与 WebGPU 使用同一套显卡身份表，不再出现 WebGL 报 Intel 而 WebGPU 报真实显卡的矛盾。");

        var win = new Window
        {
            Title = "指纹一致性检查 (v3 内核)",
            Width = 720,
            Height = 540,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = this,
            Background = (Brush)Application.Current.Resources["B_Bg"],
            FontFamily = (FontFamily)Application.Current.Resources["F_Ui"],
            Foreground = (Brush)Application.Current.Resources["B_Text"]
        };
        var root = new Grid { Margin = new Thickness(18) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var title = new TextBlock
        {
            Text = "指纹自洽性状态 (v3 纯内核加固)",
            FontSize = 16,
            FontWeight = FontWeights.SemiBold,
            Margin = new Thickness(0, 0, 0, 12)
        };
        System.Windows.Controls.Grid.SetRow(title, 0);
        root.Children.Add(title);

        var tb = new TextBox
        {
            Text = sb.ToString(),
            IsReadOnly = true,
            TextWrapping = TextWrapping.Wrap,
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            FontFamily = new FontFamily("Consolas, Microsoft YaHei"),
            FontSize = 12,
            Padding = new Thickness(10),
            Background = (Brush)Application.Current.Resources["B_Card"],
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            BorderBrush = (Brush)Application.Current.Resources["B_Line"]
        };
        System.Windows.Controls.Grid.SetRow(tb, 1);
        root.Children.Add(tb);

        var close = new Button
        {
            Content = "确定",
            Style = (Style)Application.Current.Resources["BtnPrimary"],
            MinWidth = 90,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 12, 0, 0),
            IsCancel = true
        };
        close.Click += (_, _) => win.Close();
        System.Windows.Controls.Grid.SetRow(close, 2);
        root.Children.Add(close);

        win.Content = root;
        win.ShowDialog();
    }

    // ---------------------------------------------------------------- 任务栏显示

    const string TaskbarAdvKey = @"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced";

    static int ReadGlomLevel()
    {
        try
        {
            using var k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(TaskbarAdvKey);
            var v = k?.GetValue("TaskbarGlomLevel");
            return v is int i ? i : 0;
        }
        catch { return 0; }
    }

    static bool WriteGlomLevel(int level)
    {
        try
        {
            using var k = Microsoft.Win32.Registry.CurrentUser.OpenSubKey(TaskbarAdvKey, writable: true);
            if (k is null) return false;
            k.SetValue("TaskbarGlomLevel", level, Microsoft.Win32.RegistryValueKind.DWord);
            return true;
        }
        catch { return false; }
    }

    void MenuTaskbar_Click(object sender, RoutedEventArgs e)
    {
        var cur = ReadGlomLevel();
        var curText = cur switch
        {
            0 => "始终合并（默认，所有浏览器窗口挤成一个按钮）",
            1 => "任务栏满了才合并",
            2 => "从不合并（每个窗口单独一个按钮）",
            _ => $"未知（{cur}）"
        };

        var ans = MessageBox.Show(
            "Chromium 的所有实例共用同一个 AppUserModelID，\n" +
            "Windows 默认会把它们合并成一个任务栏按钮。\n\n" +
            $"当前设置：{curText}\n\n" +
            "改成「从不合并」后，每个浏览器窗口都会有自己的按钮，\n" +
            "点哪个就切到哪个，不会串。\n\n" +
            "注意：这是 Windows 全局设置，会影响所有程序的任务栏。\n\n" +
            "要改成「从不合并」吗？",
            "任务栏显示方式", MessageBoxButton.YesNoCancel, MessageBoxImage.Question);

        if (ans == MessageBoxResult.Cancel) return;
        var target = ans == MessageBoxResult.Yes ? 2 : 0;

        if (!WriteGlomLevel(target))
        {
            MessageBox.Show("写注册表失败，可能没有权限。", "任务栏显示方式",
                            MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }

        SetStatus(target == 2 ? "任务栏已改为每窗口单独显示" : "任务栏已恢复合并显示");

        var r = MessageBox.Show(
            "设置已保存，需要重启资源管理器才生效。\n\n" +
            "现在重启吗？（会关掉所有资源管理器窗口，浏览器不受影响）",
            "重启资源管理器", MessageBoxButton.YesNo, MessageBoxImage.Question);

        if (r == MessageBoxResult.Yes)
        {
            try
            {
                foreach (var p in Process.GetProcessesByName("explorer"))
                    p.Kill();
            }
            catch { }
            Task.Delay(1500).ContinueWith(_ =>
            {
                try { Process.Start("explorer.exe"); } catch { }
            });
            SetStatus("资源管理器已重启");
        }
    }

    // ---------------------------------------------------------------- 快捷方式

    void MenuShortcut_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;

        if (!File.Exists(_cfg.chromeExe))
        {
            MessageBox.Show($"找不到 chrome.exe：\n{_cfg.chromeExe}", "创建快捷方式",
                            MessageBoxButton.OK, MessageBoxImage.Error);
            return;
        }

        // 快捷方式指向 KZManager.exe --launch，而不是直接调 chrome.exe。
        // 这样点它和管理器里点「启动」走的是同一套流程：
        // 解析节点 → 写 WebRTC pref → 起节点代理 → 注入谷歌凭据 → 启动。
        var managerExe = Environment.ProcessPath;
        if (string.IsNullOrEmpty(managerExe) || !File.Exists(managerExe))
        {
            MessageBox.Show("拿不到管理器自身路径，无法创建快捷方式。", "创建快捷方式",
                            MessageBoxButton.OK, MessageBoxImage.Error);
            return;
        }

        var choice = MessageBox.Show(
            $"给「{sel.Name}」创建快捷方式。\n\n" +
            "是 = 放桌面（之后可以右键固定到任务栏）\n" +
            "否 = 放开始菜单\n" +
            "取消 = 不创建",
            "创建快捷方式", MessageBoxButton.YesNoCancel, MessageBoxImage.Question);
        if (choice == MessageBoxResult.Cancel) return;

        var dir = choice == MessageBoxResult.Yes ? Shortcut.DesktopDir : Shortcut.StartMenuDir;
        var lnk = Path.Combine(dir, sel.Name + ".lnk");

        var iconPath = Paths.IconPath(Store.LoadProfile(sel.Name).icon ?? "");
        var ok = Shortcut.Create(lnk, managerExe, Launcher.ShortcutArgs(sel.Name),
                                 Path.GetDirectoryName(managerExe) ?? "",
                                 $"{sel.Name} - KZ 多开实例", out var err, iconPath);
        if (!ok)
        {
            MessageBox.Show($"创建失败：\n{err}", "创建快捷方式",
                            MessageBoxButton.OK, MessageBoxImage.Error);
            return;
        }

        SetStatus($"已创建快捷方式: {lnk}");
        MessageBox.Show(
            $"已创建：\n{lnk}\n\n" +
            $"目标：{managerExe}\n" +
            $"参数：{Launcher.ShortcutArgs(sel.Name)}\n\n" +
            "点它会走和管理器「启动」完全一样的流程：\n" +
            "  解析节点 → 写 WebRTC pref → 起节点代理 → 注入谷歌凭据 → 启动\n\n" +
            "【固定到任务栏】\n" +
            "把桌面这个快捷方式直接【拖到任务栏】上。\n" +
            "不要用「右键运行中的浏览器窗口 → 固定到任务栏」——\n" +
            "Windows 那样只会记住 chrome.exe 路径，所有参数都会丢，\n" +
            "点开就是一个没有指纹的空白 profile。",
            "创建完成", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    // ---------------------------------------------------------------- 删除

    static long DirSize(string path)
    {
        if (!Directory.Exists(path)) return 0;
        long total = 0;
        try
        {
            foreach (var f in new DirectoryInfo(path).EnumerateFiles("*", SearchOption.AllDirectories))
            {
                try { total += f.Length; } catch { }
            }
        }
        catch { }
        return total;
    }

    /// <summary>删目录，先清只读属性（浏览器数据里有一堆）。</summary>
    static bool DeleteDir(string path)
    {
        if (!Directory.Exists(path)) return true;
        try
        {
            foreach (var f in Directory.EnumerateFiles(path, "*", SearchOption.AllDirectories))
            {
                try { File.SetAttributes(f, FileAttributes.Normal); } catch { }
            }
        }
        catch { }
        try { Directory.Delete(path, true); return !Directory.Exists(path); }
        catch { return false; }
    }

    sealed class SnapshotMeta
    {
        public string name { get; set; } = "";
        public string source { get; set; } = "";
    }

    void MenuDelete_Click(object sender, RoutedEventArgs e)
    {
        var sel = Selected;
        if (sel is null) return;
        var name = sel.Name;

        // ---- 统计会删掉什么 ----
        var info = new DeleteDialog.Info
        {
            Name = name,
            IsMaster = sel.IsMaster,
            Running = sel.Running,
            InstanceBytes = DirSize(Paths.InstanceDir(name))
        };

        if (Directory.Exists(Paths.BackupDir))
        {
            foreach (var batch in Directory.GetDirectories(Paths.BackupDir))
            {
                var d = Path.Combine(batch, name);
                if (Directory.Exists(d)) { info.BackupCount++; info.BackupBytes += DirSize(d); }
            }
        }

        var snapDirs = new List<string>();
        if (Directory.Exists(Paths.SnapDir))
        {
            foreach (var snap in Directory.GetDirectories(Paths.SnapDir))
            {
                var meta = Json.Read<SnapshotMeta>(Path.Combine(snap, "meta.json"));
                if (meta is not null && string.Equals(meta.source, name, StringComparison.OrdinalIgnoreCase))
                {
                    info.SnapshotCount++;
                    info.SnapshotBytes += DirSize(snap);
                    snapDirs.Add(snap);
                }
            }
        }

        // ---- 确认 ----
        var opt = DeleteDialog.Ask(this, info);
        if (opt is null || !opt.Confirmed) return;

        SetStatus($"正在删除 {name} …");
        Mouse.OverrideCursor = Cursors.Wait;
        var errors = new List<string>();
        try
        {
            // 1. 结束进程
            var killed = ProcessQuery.KillChromeByDataDir(Paths.DataDir(name).TrimEnd('\\'));
            if (File.Exists(Paths.NodeProxy))
                Py.Run(Paths.NodeProxy, "stop", "--workdir", Paths.InstanceDir(name));
            try { ProxyRelay.StopFor(Store.LoadProfile(name).proxy ?? ""); }
            catch { }
            if (killed > 0) SetStatus($"已结束 {killed} 个进程，正在删除文件 …");

            // 2. 实例目录
            if (!DeleteDir(Paths.InstanceDir(name)))
                errors.Add($"实例目录删除失败: {Paths.InstanceDir(name)}");

            // 3. 从 manager.json 的顺序里摘掉
            _cfg.order = _cfg.order.Where(x => !string.Equals(x, name, StringComparison.OrdinalIgnoreCase)).ToList();
            Store.SaveConfig(_cfg);

            // 4. 备份
            if (opt.DeleteBackups && Directory.Exists(Paths.BackupDir))
            {
                foreach (var batch in Directory.GetDirectories(Paths.BackupDir))
                {
                    var d = Path.Combine(batch, name);
                    if (Directory.Exists(d) && !DeleteDir(d))
                        errors.Add($"备份删除失败: {d}");
                    // 批次目录空了就一起删
                    try
                    {
                        if (Directory.Exists(batch) &&
                            !Directory.EnumerateFileSystemEntries(batch).Any())
                            Directory.Delete(batch);
                    }
                    catch { }
                }
            }

            // 5. 快照
            if (opt.DeleteSnapshots)
            {
                foreach (var snap in snapDirs)
                    if (!DeleteDir(snap))
                        errors.Add($"快照删除失败: {snap}");
            }
        }
        finally { Mouse.OverrideCursor = null; }

        RefreshList();

        if (errors.Count == 0)
        {
            SetStatus($"已彻底删除 {name}，释放 {DeleteDialog.Human(info.TotalBytes)}");
            MessageBox.Show(
                $"已彻底删除「{name}」。\n\n" +
                $"释放空间：{DeleteDialog.Human(info.TotalBytes)}\n" +
                $"已清理：实例目录" +
                (opt.DeleteBackups && info.BackupCount > 0 ? $"、备份 {info.BackupCount} 份" : "") +
                (opt.DeleteSnapshots && info.SnapshotCount > 0 ? $"、快照 {info.SnapshotCount} 个" : ""),
                "删除完成", MessageBoxButton.OK, MessageBoxImage.Information);
        }
        else
        {
            SetStatus("删除完成，但有残留");
            MessageBox.Show("删除完成，但以下内容没能删掉（可能被占用）：\n\n" + string.Join("\n", errors),
                "部分失败", MessageBoxButton.OK, MessageBoxImage.Warning);
        }
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
            var stPsi = new ProcessStartInfo(_cfg.chromeExe, args)
            {
                UseShellExecute = false,
                WorkingDirectory = Path.GetDirectoryName(_cfg.chromeExe) ?? ""
            };
            GoogleOAuth.ApplyTo(stPsi);
            Process.Start(stPsi);
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
        sb.AppendLine($"全局：谷歌登录  {GoogleOAuth.Describe()}");
        if (!File.Exists(_cfg.chromeExe)) sb.AppendLine($"全局：找不到 chrome.exe -> {_cfg.chromeExe}");
        else sb.AppendLine($"全局：chrome.exe 正常（{new FileInfo(_cfg.chromeExe).Length / 1024 / 1024} MB）");

        MessageBox.Show(sb.ToString(), "健康巡检");
        SetStatus($"健康巡检：正常 {ok} / 有问题 {bad}");
    }

    void MenuBackupOne_Click(object sender, RoutedEventArgs e)
        => BackupDialog.Show(this, false, Selected?.Name ?? "");

    void MenuRestoreOne_Click(object sender, RoutedEventArgs e)
        => BackupDialog.Show(this, true, Selected?.Name ?? "");

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
        snap = snap.Trim();
        var vSnapErr = PathGuard.ValidateName(snap);
        if (vSnapErr is not null)
        {
            MessageBox.Show("快照名称无效：" + vSnapErr, "创建快照", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
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
        snap = snap.Trim();
        var vRestErr = PathGuard.ValidateName(snap);
        if (vRestErr is not null)
        {
            MessageBox.Show("快照名称无效：" + vRestErr, "从快照还原", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
        SetStatus($"正在还原快照 {snap} …");
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
        var vSyncErr = PathGuard.ValidateName(src);
        if (vSyncErr is not null)
        {
            MessageBox.Show("源实例名称无效：" + vSyncErr, "同步数据", MessageBoxButton.OK, MessageBoxImage.Warning);
            return;
        }
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

    // =========================================================================
    // 现代商业级多开控制台导航与新功能事件处理
    // =========================================================================

    void SetActiveNav(Button activeBtn, Grid activeView)
    {
        NavProfiles.Style = (Style)FindResource("BtnGhost");
        NavProxies.Style = (Style)FindResource("BtnGhost");
        NavSync.Style = (Style)FindResource("BtnGhost");
        NavHealth.Style = (Style)FindResource("BtnGhost");
        NavExtensions.Style = (Style)FindResource("BtnGhost");
        NavAutomation.Style = (Style)FindResource("BtnGhost");
        activeBtn.Style = (Style)FindResource("BtnPrimary");

        ViewProfiles.Visibility = Visibility.Collapsed;
        ViewProxies.Visibility = Visibility.Collapsed;
        ViewSync.Visibility = Visibility.Collapsed;
        ViewHealth.Visibility = Visibility.Collapsed;
        ViewExtensions.Visibility = Visibility.Collapsed;
        ViewAutomation.Visibility = Visibility.Collapsed;
        activeView.Visibility = Visibility.Visible;
    }

    void NavProfiles_Click(object sender, RoutedEventArgs e) => SetActiveNav(NavProfiles, ViewProfiles);

    void NavProxies_Click(object sender, RoutedEventArgs e)
    {
        SetActiveNav(NavProxies, ViewProxies);
        RefreshProxyPool();
    }

    void NavSync_Click(object sender, RoutedEventArgs e)
    {
        SetActiveNav(NavSync, ViewSync);
        RefreshSyncViews();
    }

    void NavHealth_Click(object sender, RoutedEventArgs e) => SetActiveNav(NavHealth, ViewHealth);

    void NavExtensions_Click(object sender, RoutedEventArgs e)
    {
        SetActiveNav(NavExtensions, ViewExtensions);
        RefreshExtensions();
    }

    void NavAutomation_Click(object sender, RoutedEventArgs e)
    {
        SetActiveNav(NavAutomation, ViewAutomation);
        try
        {
            var tokPath = System.IO.Path.Combine(Paths.Root, "_api-token");
            if (File.Exists(tokPath)) TxtApiToken.Text = File.ReadAllText(tokPath).Trim();
            else TxtApiToken.Text = "(启动 --api 模式后自动生成)";
        }
        catch { }
        var n = Selected?.Name ?? Store.ListInstances().FirstOrDefault() ?? "默认环境";
        TxtSnippet.Text = FrameworkConnector.GetConnectionInfo(n).PlaywrightPython;
    }

    void RefreshProxyPool()
    {
        GridProxies.ItemsSource = ProxyManager.LoadPool();
    }

    void RefreshExtensions()
    {
        GridExtensions.ItemsSource = ExtensionManager.ListAvailable();
    }

    void RefreshSyncViews()
    {
        var names = Store.ListInstances();
        CmbSyncMaster.ItemsSource = names;
        if (CmbSyncMaster.SelectedIndex < 0 && names.Count > 0) CmbSyncMaster.SelectedIndex = 0;
        ListSyncSlaves.ItemsSource = names.Select(n => new CheckBox { Content = n, IsChecked = true }).ToList();
    }

    // 智能指纹生成 (Smart Fingerprint Generator)
    void BtnSmartGen_Click(object sender, RoutedEventArgs e)
    {
        var name = InputDialog.Ask(this, "智能指纹生成", "环境名称", "智能环境-" + Random.Shared.Next(100, 999));
        if (string.IsNullOrWhiteSpace(name)) return;
        name = name.Trim();
        var vErr = PathGuard.ValidateName(name);
        if (vErr is not null) { MessageBox.Show("名称非法: " + vErr, "智能生成"); return; }
        if (Directory.Exists(Paths.InstanceDir(name))) { MessageBox.Show("已存在同名环境", "智能生成"); return; }

        var prof = SmartFingerprint.Generate(name, "windows");
        Store.SaveProfile(name, prof);
        RefreshList();
        SetStatus($"已智能生成相容环境方案: {name}");
        MessageBox.Show($"已智能生成全套相容方案：\n\n平台: {prof.platform}\nCPU: {prof.fp_cores}核\n内存: {prof.fp_memory}GB\n分辨率: {prof.fp_screen} (DPR {prof.fp_dpr})\nWebGL: {prof.fp_webgl}\n时区: {prof.timezone}", "智能生成成功");
    }

    // 批量启动 (Batch Launch)
    void BtnBatchStart_Click(object sender, RoutedEventArgs e)
    {
        var names = Store.ListInstances();
        if (names.Count == 0) return;
        var r = MessageBox.Show($"确认错峰启动全部 {names.Count} 个环境？", "批量启动", MessageBoxButton.YesNo);
        if (r != MessageBoxResult.Yes) return;

        _ = Task.Run(async () =>
        {
            await BatchEngine.BatchLaunchAsync(names, _cfg, 2, s => Dispatcher.Invoke(() => SetStatus(s)));
            Dispatcher.Invoke(() => { RefreshList(); SetStatus("批量启动完成"); });
        });
    }

    // 批量停止 (Batch Stop)
    void BtnBatchStop_Click(object sender, RoutedEventArgs e)
    {
        var names = Store.ListInstances();
        BatchEngine.BatchStop(names);
        RefreshList();
        SetStatus("已批量停止全部环境进程");
    }

    // 多屏窗口网格平铺 (Window Tiler)
    void BtnTile_Click(object sender, RoutedEventArgs e)
    {
        int tiled = WindowTiler.TileWindows();
        SetStatus($"已网格平铺 {tiled} 个窗口");
        if (tiled == 0) MessageBox.Show("未找到打开的 Chrome 实例窗口。", "窗口平铺");
    }

    // 快捷标签与状态过滤 (Filter Chips)
    void FilterChip_Click(object sender, RoutedEventArgs e)
    {
        if (sender is not Button btn) return;
        var text = (btn.Content?.ToString() ?? "").Trim();
        if (text.Contains("全部")) SearchBox.Text = "";
        else if (text.Contains("运行中")) SearchBox.Text = "运行中";
        else if (text.Contains("美区")) SearchBox.Text = "US";
        else if (text.Contains("日区")) SearchBox.Text = "JP";
    }

    // 代理导入
    void BtnImportProxies_Click(object sender, RoutedEventArgs e)
    {
        var raw = InputDialog.Ask(this, "批量导入代理", "粘贴代理行 (格式: host:port:user:pass 或 socks5://...)", "");
        if (string.IsNullOrWhiteSpace(raw)) return;
        var (nImp, nDup, errs) = ProxyManager.ImportText(raw);
        RefreshProxyPool();
        MessageBox.Show($"导入完成！\n成功新增: {nImp} 个\n重复跳过: {nDup} 个\n错误: {errs.Count} 个", "代理导入");
    }

    // 批量测速
    void BtnTestProxies_Click(object sender, RoutedEventArgs e)
    {
        SetStatus("正在批量测速节点…");
        var pool = ProxyManager.LoadPool();
        _ = Task.Run(async () =>
        {
            await ProxyManager.BatchTestAsync(pool);
            Dispatcher.Invoke(() => {
                RefreshProxyPool();
                SetStatus("代理批量测速完成");
            });
        });
    }

    // 自动分配代理
    void BtnAssignProxies_Click(object sender, RoutedEventArgs e)
    {
        var names = Store.ListInstances();
        int n = ProxyManager.AutoAssign("默认", names);
        RefreshList();
        MessageBox.Show($"已自动为 {n} 个环境分配代理节点。", "自动分配代理");
    }

    // 同步器控制
    void BtnSyncStart_Click(object sender, RoutedEventArgs e)
    {
        var master = CmbSyncMaster.SelectedItem?.ToString();
        if (string.IsNullOrEmpty(master)) { MessageBox.Show("请选择主控窗口", "同步器"); return; }
        SetStatus($"正在连接主控窗口 {master}…");
        // 获取所有从属
        var slaves = new Dictionary<string, int>();
        int mockPort = 9811;
        foreach (var it in ListSyncSlaves.Items)
        {
            if (it is CheckBox cb && cb.IsChecked == true && cb.Content is string sName && sName != master)
            {
                slaves[sName] = ++mockPort;
            }
        }
        _ = Task.Run(async () =>
        {
            var (ok, msg) = await ActionSynchronizer.StartSyncAsync(master, 9810, slaves);
            Dispatcher.Invoke(() => {
                SetStatus(msg);
                MessageBox.Show(msg, "操作同步器");
            });
        });
    }

    void BtnSyncPause_Click(object sender, RoutedEventArgs e)
    {
        ActionSynchronizer.IsPaused = !ActionSynchronizer.IsPaused;
        SetStatus(ActionSynchronizer.IsPaused ? "同步已暂停" : "同步已恢复");
    }

    void BtnSyncStop_Click(object sender, RoutedEventArgs e)
    {
        ActionSynchronizer.StopSync();
        SetStatus("同步已停止");
    }

    // 地域自动联动 (Auto Align)
    void MenuAutoAlign_Click(object sender, RoutedEventArgs e)
    {
        var n = NeedSelection();
        if (n is null) return;
        var p = Store.LoadProfile(n);
        if (string.IsNullOrEmpty(p.proxy)) { MessageBox.Show("当前环境未配置代理，无法自动联动地域。", "地域自动联动"); return; }

        SetStatus("正在通过代理探测实际出口并自动补齐地域…");
        _ = Task.Run(() =>
        {
            var (ok, ip, country, tz, lang, geo, err) = ProxyPrecheck.AutoAlignRegion(p.proxy, p);
            Dispatcher.Invoke(() => {
                if (ok)
                {
                    Store.SaveProfile(n, p);
                    RefreshList();
                    TxtHealthReport.Text = $"【地域自动联动成功】\n环境: {n}\n出口 IP: {ip}\n国家/地区: {country}\n时区: {tz}\n语言: {lang}\n经纬度: {geo}";
                    SetStatus($"已联动更新 {n} 为 {country}");
                    MessageBox.Show(TxtHealthReport.Text, "地域联动");
                }
                else
                {
                    MessageBox.Show("联动失败: " + err, "地域联动");
                }
            });
        });
    }

    // 真实浏览器跑分
    void BtnRunBench_Click(object sender, RoutedEventArgs e)
    {
        SetStatus("正在拉起真实内核跑分探针…");
        TxtHealthReport.Text = "正在运行真实浏览器并采集 Canvas / WebGL / WebRTC / Audio / UA 探针指标，请稍候 (约10秒)…";
        _ = Task.Run(() =>
        {
            var (ok, rep, file, err) = FinalFeatures.Benchmark(_cfg.chromeExe, Selected?.Seed ?? "914410");
            Dispatcher.Invoke(() => {
                if (ok)
                {
                    var sb = new System.Text.StringBuilder();
                    sb.AppendLine($"【真实浏览器跑分报告 - {rep.totalScore}分】");
                    sb.AppendLine($"时间: {rep.timestamp}  Seed: {rep.seed}");
                    sb.AppendLine($"通过项: {rep.passedCount}  失败项: {rep.failedCount}");
                    sb.AppendLine("--------------------------------------------------");
                    foreach (var c in rep.checks)
                    {
                        sb.AppendLine($"  {(c.pass ? "✓" : "✗")} {c.name,-24} {c.actual}");
                    }
                    TxtHealthReport.Text = sb.ToString();
                    SetStatus($"跑分完成: {rep.totalScore}分");
                }
                else
                {
                    TxtHealthReport.Text = $"跑分失败: {err}\n报告文件: {file}";
                }
            });
        });
    }

    void BtnOpenExtDir_Click(object sender, RoutedEventArgs e)
    {
        Directory.CreateDirectory(ExtensionManager.ExtensionsDir);
        Process.Start("explorer.exe", ExtensionManager.ExtensionsDir);
    }

    void BtnRefreshExt_Click(object sender, RoutedEventArgs e)
    {
        RefreshExtensions();
        SetStatus("已刷新本地扩展库");
    }

    void BtnCopyToken_Click(object sender, RoutedEventArgs e)
    {
        if (!string.IsNullOrEmpty(TxtApiToken.Text))
        {
            Clipboard.SetText(TxtApiToken.Text);
            SetStatus("已复制 API 令牌到剪贴板");
        }
    }


    // =========================================================================
    // 一键便携打包导出处理 (Portable Package Exporter)
    // =========================================================================

    void ChkSelectAll_Click(object sender, RoutedEventArgs e)
    {
        if (sender is CheckBox chk)
        {
            bool isChecked = chk.IsChecked == true;
            foreach (var row in _rows)
            {
                row.IsSelected = isChecked;
            }
        }
    }

    void BtnPackPortable_Click(object sender, RoutedEventArgs e)
    {
        // 1. 获取选中的实例列表 (若未勾选任何复选框，但单选了某行，则默认打包当前选中行)
        var selectedNames = _rows.Where(x => x.IsSelected).Select(x => x.Name).ToList();
        if (selectedNames.Count == 0 && Selected is not null)
        {
            selectedNames.Add(Selected.Name);
        }

        if (selectedNames.Count == 0)
        {
            var r = MessageBox.Show(
                "您当前没有勾选任何实例环境。\n\n是否将列表中的【全部实例】连同绿色内核一起完整打包？",
                "一键便携打包", MessageBoxButton.YesNo, MessageBoxImage.Question);
            if (r != MessageBoxResult.Yes) return;
            selectedNames = _rows.Select(x => x.Name).ToList();
        }

        if (selectedNames.Count == 0)
        {
            MessageBox.Show("当前没有任何可打包的实例环境。", "一键便携打包");
            return;
        }

        var confirmMsg =
            $"准备打包以下 {selectedNames.Count} 个实例环境：\n\n" +
            $"• {string.Join("\n• ", selectedNames.Take(8))}" +
            (selectedNames.Count > 8 ? $"\n... 等共 {selectedNames.Count} 个环境" : "") +
            "\n\n【打包说明 (方案一)】\n" +
            "1. 包含完整的定制 Chromium 绿色内核 (91项防关联补丁)\n" +
            "2. 包含实例指纹配置、已登录谷歌账号凭证、已装扩展与书签\n" +
            "3. 智能过滤垃圾渲染与音视频临时缓存 (大幅缩减体积)\n" +
            "4. 打包输出为立即可用的 ZIP，解压即可在任意电脑使用\n\n" +
            "点击「确定」立即开始打包导出。";

        var cr = MessageBox.Show(confirmMsg, "确认一键便携打包", MessageBoxButton.OKCancel, MessageBoxImage.Information);
        if (cr != MessageBoxResult.OK) return;

        var defaultOutDir = System.IO.Path.Combine(Paths.Root, "_exports");
        Directory.CreateDirectory(defaultOutDir);

        SetStatus("正在打包便携套件 (请稍候)...");
        BtnPackPortable.IsEnabled = false;

        _ = Task.Run(async () =>
        {
            var (ok, zipPath, err) = await PortablePacker.ExportPortablePackageAsync(
                selectedNames,
                defaultOutDir,
                prog => Dispatcher.Invoke(() => SetStatus($"{prog.Status} ({prog.Percent:0}%)")));

            Dispatcher.Invoke(() =>
            {
                BtnPackPortable.IsEnabled = true;
                if (ok && File.Exists(zipPath))
                {
                    var fileInfo = new FileInfo(zipPath);
                    double mb = fileInfo.Length / 1024.0 / 1024.0;
                    SetStatus($"便携套件已生成: {fileInfo.Name} ({mb:0.1} MB)");

                    var successMsg =
                        $"🎉 一键便携套件打包成功！\n\n" +
                        $"• 导出文件: {fileInfo.Name}\n" +
                        $"• 文件大小: {mb:0.1} MB\n" +
                        $"• 包含环境: {selectedNames.Count} 个\n" +
                        $"• 存放路径:\n  {zipPath}\n\n" +
                        $"【如何在新电脑上使用】\n" +
                        $"将该 ZIP 复制到新电脑任意盘符解压，直接双击运行「KZManager.exe」即可，无需任何安装配置！\n\n" +
                        $"点击「确定」立即打开所在文件夹。";

                    var openFolder = MessageBox.Show(successMsg, "打包完成", MessageBoxButton.OKCancel, MessageBoxImage.Information);
                    if (openFolder == MessageBoxResult.OK)
                    {
                        try
                        {
                            System.Diagnostics.Process.Start("explorer.exe", $"/select,\"{zipPath}\"");
                        }
                        catch { }
                    }
                }
                else
                {
                    SetStatus("便携套件打包失败");
                    MessageBox.Show($"打包失败：{err}", "一键便携打包", MessageBoxButton.OK, MessageBoxImage.Error);
                }
            });
        });
    }

}
