using System.IO;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace KZManager;

/// <summary>
/// 监听任务栏固定目录。
///
/// Windows 右键运行中的窗口「固定到任务栏」时，生成的 .lnk 参数是空的，
/// 点开就是一个没有 --user-data-dir 的空白 profile。这个类在新固定项出现时
/// 自动把它改写成 KZManager.exe --launch "实例名"。
///
/// 哪个实例？用「当前正在运行的那个」——你右键固定的就是它。
/// 同时跑着多个时不猜，提示用户用管理器建快捷方式。
/// </summary>
public sealed class PinWatcher : IDisposable
{
    static string PinDir => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        @"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar");

    readonly FileSystemWatcher? _watcher;
    readonly string _chromeExe;
    readonly Action<string> _report;
    readonly System.Windows.Threading.Dispatcher _ui;
    readonly Dictionary<string, DateTime> _seen = new();

    public PinWatcher(string chromeExe, System.Windows.Threading.Dispatcher ui, Action<string> report)
    {
        _chromeExe = chromeExe;
        _ui = ui;
        _report = report;

        if (!Directory.Exists(PinDir)) return;
        try
        {
            _watcher = new FileSystemWatcher(PinDir, "*.lnk")
            {
                NotifyFilter = NotifyFilters.FileName | NotifyFilters.LastWrite |
                               NotifyFilters.CreationTime,
                IncludeSubdirectories = false
            };
            _watcher.Created += (_, e) => Queue(e.FullPath);
            _watcher.Changed += (_, e) => Queue(e.FullPath);
            _watcher.EnableRaisingEvents = true;
        }
        catch { }
    }

    void Queue(string path)
    {
        // Windows 写 .lnk 可能触发多次事件，去抖
        lock (_seen)
        {
            if (_seen.TryGetValue(path, out var last) &&
                (DateTime.UtcNow - last).TotalSeconds < 2)
                return;
            _seen[path] = DateTime.UtcNow;
        }

        Task.Delay(1200).ContinueWith(_ => _ui.BeginInvoke(new Action(() => TryFix(path))));
    }

    void TryFix(string lnkPath)
    {
        try
        {
            if (!File.Exists(lnkPath)) return;

            var info = ReadShortcut(lnkPath);
            if (info is null) return;

            // 只处理「指向 chrome.exe 且没有参数」的
            if (!string.Equals(info.Value.target, _chromeExe, StringComparison.OrdinalIgnoreCase))
                return;
            if (!string.IsNullOrWhiteSpace(info.Value.args))
                return;

            // Windows 右键固定窗口时会把窗口的 AUMID 写进 .lnk。
            // 我们启动实例时已给窗口打了 KZ.Browser.Profile.<hash>，
            // 所以这里能精确反查属于哪个实例，不用猜「哪个在运行」。
            var lnkId = Shortcut.GetLnkAumid(lnkPath);
            if (!string.IsNullOrEmpty(lnkId))
            {
                var exact = InstanceByAumid(lnkId);
                if (exact is not null)
                {
                    Rewrite(lnkPath, exact);
                    return;
                }
            }

            // 兜底：.lnk 没带 AUMID（或不是我们的格式），按运行中的实例判断
            var running = RunningInstances();
            if (running.Count == 0)
            {
                _report("检测到新的任务栏固定项，但没有实例在运行，无法判断属于哪个");
                return;
            }

            string? pick;
            if (running.Count == 1)
            {
                pick = running[0];
            }
            else
            {
                var list = string.Join("\n", running.Select(x => "  · " + x));
                var ans = MessageBox.Show(
                    $"你刚固定了一个浏览器窗口，但当前有 {running.Count} 个实例在运行：\n\n{list}\n\n" +
                    "要把这个任务栏图标指向哪个实例？\n\n" +
                    "点「是」后我会依次询问；点「否」则不改，\n" +
                    "建议用管理器的「创建快捷方式」再拖到任务栏。",
                    "修复任务栏固定项", MessageBoxButton.YesNo, MessageBoxImage.Question);
                if (ans != MessageBoxResult.Yes) return;

                pick = AskWhich(running);
                if (string.IsNullOrEmpty(pick)) return;
            }

            Rewrite(lnkPath, pick);
        }
        catch { }
    }

    /// <summary>把 .lnk 改写成指向管理器的 --launch，并写回该实例的 AUMID。</summary>
    void Rewrite(string lnkPath, string instanceName)
    {
        var managerExe = Environment.ProcessPath;
        if (string.IsNullOrEmpty(managerExe)) return;

        var iconPath = Paths.IconPath(Store.LoadProfile(instanceName).icon ?? "");
        var ok = Shortcut.Create(lnkPath, managerExe, Launcher.ShortcutArgs(instanceName),
                                 Path.GetDirectoryName(managerExe) ?? "",
                                 $"{instanceName} - KZ 多开实例", out var err, iconPath);
        if (!ok)
        {
            _report($"修复失败：{err}");
            return;
        }

        // 关键：写回 AUMID，让每个实例的固定项在任务栏上也是独立的按钮。
        // WScript.Shell 保存 .lnk 时会清掉这个属性，所以必须先 Create 再 Set。
        Shortcut.SetLnkAumid(lnkPath, WindowAumid.IdFor(instanceName));

        _report($"已修复任务栏图标 → {instanceName}");
        MessageBox.Show(
            $"已把任务栏那个图标改成指向「{instanceName}」。\n\n" +
            $"目标：{managerExe}\n参数：{Launcher.ShortcutArgs(instanceName)}\n" +
            $"AUMID：{WindowAumid.IdFor(instanceName)}\n\n" +
            "现在点它会走和管理器「启动」完全一样的流程，\n" +
            "而且在任务栏上是这个实例独立的按钮。\n" +
            "如果任务栏还显示旧的，重启一下资源管理器。",
            "修复完成", MessageBoxButton.OK, MessageBoxImage.Information);
    }

    /// <summary>由 AUMID 反查实例名（AUMID 是实例名哈希，遍历比一下即可）。</summary>
    static string? InstanceByAumid(string aumid)
    {
        foreach (var name in Store.ListInstances())
            if (string.Equals(WindowAumid.IdFor(name), aumid, StringComparison.OrdinalIgnoreCase))
                return name;
        return null;
    }

    string? AskWhich(List<string> options)
    {
        var dlg = new Window
        {
            Title = "选择实例",
            Width = 460,
            SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterScreen,
            Background = (Brush)Application.Current.Resources["B_Bg"],
            FontFamily = (FontFamily)Application.Current.Resources["F_Ui"],
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            WindowStyle = WindowStyle.ToolWindow
        };

        var root = new StackPanel { Margin = new Thickness(20) };
        root.Children.Add(new TextBlock
        {
            Text = "这个任务栏图标应该指向哪个实例？",
            FontSize = 13,
            Margin = new Thickness(0, 0, 0, 12),
            Foreground = (Brush)Application.Current.Resources["B_TextDim"]
        });

        var lb = new ListBox
        {
            Height = 160,
            Background = (Brush)Application.Current.Resources["B_Card"],
            BorderBrush = (Brush)Application.Current.Resources["B_Line"],
            Foreground = (Brush)Application.Current.Resources["B_Text"]
        };
        foreach (var o in options) lb.Items.Add(o);
        if (lb.Items.Count > 0) lb.SelectedIndex = 0;
        root.Children.Add(lb);

        var bar = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 14, 0, 0)
        };
        var cancel = new Button
        {
            Content = "取消",
            Style = (Style)Application.Current.Resources["Btn"],
            MinWidth = 80,
            Margin = new Thickness(0, 0, 8, 0),
            IsCancel = true
        };
        cancel.Click += (_, _) => { dlg.DialogResult = false; dlg.Close(); };
        var ok = new Button
        {
            Content = "确定",
            Style = (Style)Application.Current.Resources["BtnPrimary"],
            MinWidth = 80,
            IsDefault = true
        };
        ok.Click += (_, _) => { dlg.DialogResult = true; dlg.Close(); };
        bar.Children.Add(cancel);
        bar.Children.Add(ok);
        root.Children.Add(bar);

        dlg.Content = root;
        return dlg.ShowDialog() == true ? lb.SelectedItem as string : null;
    }

    /// <summary>用 chrome 进程的 --user-data-dir 反查实例名。</summary>
    List<string> RunningInstances()
    {
        var result = new List<string>();
        var dirs = ProcessQuery.ChromeDataDirs();      // dataDir -> pid
        var root = Paths.Root.TrimEnd('\\');

        foreach (var name in Store.ListInstances())
        {
            var dataDir = Paths.DataDir(name).TrimEnd('\\');
            if (dirs.ContainsKey(dataDir)) result.Add(name);
        }
        return result;
    }

    static (string target, string args)? ReadShortcut(string path)
    {
        try
        {
            var type = Type.GetTypeFromProgID("WScript.Shell");
            if (type is null) return null;
            dynamic shell = Activator.CreateInstance(type)!;
            dynamic sc = shell.CreateShortcut(path);
            string t = sc.TargetPath ?? "";
            string a = sc.Arguments ?? "";
            return (t, a);
        }
        catch { return null; }
    }

    public void Dispose()
    {
        try { _watcher?.Dispose(); } catch { }
    }
}
