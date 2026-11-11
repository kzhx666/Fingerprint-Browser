using System.Windows;
using System.IO;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace KZManager;

public partial class EditDialog : Window
{
    readonly Dictionary<string, Control> _ctl = new();
    string _iconValue = "";
    readonly List<NodeEntry> _nodes;
    public Profile Result { get; private set; }

    static readonly string[] Platforms = { "macos", "linux", "windows", "host" };
    static readonly string[] Timezones =
    {
        "America/New_York", "America/Los_Angeles", "America/Chicago",
        "America/Toronto", "Europe/London", "Europe/Berlin", "Europe/Paris",
        "Asia/Tokyo", "Asia/Seoul", "Asia/Shanghai", "Asia/Hong_Kong",
        "Asia/Taipei", "Asia/Singapore", "Australia/Sydney", "UTC"
    };
    static readonly string[] Langs =
    {
        "", "zh-CN,zh;q=0.9", "zh-TW,zh;q=0.9", "en-US,en;q=0.9",
        "en-GB,en;q=0.9", "ja-JP,ja;q=0.9", "ko-KR,ko;q=0.9",
        "de-DE,de;q=0.9", "fr-FR,fr;q=0.9", "es-ES,es;q=0.9",
        "ru-RU,ru;q=0.9", "pt-BR,pt;q=0.9", "it-IT,it;q=0.9"
    };

    public sealed record RegionPreset(
        string Name, string Tz, string Lang, string Platform,
        string Screen, string Dpr, string Geo);

    public static readonly RegionPreset[] Presets =
    {
        new("美国-纽约",     "America/New_York",    "en-US,en;q=0.9", "macos",   "2560x1440", "2", "40.7128,-74.0060,30"),
        new("美国-洛杉矶",   "America/Los_Angeles", "en-US,en;q=0.9", "macos",   "1920x1080", "2", "34.0522,-118.2437,30"),
        new("日本-东京",     "Asia/Tokyo",          "ja-JP,ja;q=0.9", "macos",   "2560x1440", "2", "35.6895,139.6917,30"),
        new("韩国-首尔",     "Asia/Seoul",          "ko-KR,ko;q=0.9", "windows", "1920x1080", "1", "37.5665,126.9780,30"),
        new("新加坡",        "Asia/Singapore",      "en-US,en;q=0.9", "windows", "1920x1080", "1", "1.3521,103.8198,30"),
        new("香港",          "Asia/Hong_Kong",      "zh-TW,zh;q=0.9", "windows", "1920x1080", "1", "22.3193,114.1694,30"),
        new("台湾",          "Asia/Taipei",         "zh-TW,zh;q=0.9", "windows", "1920x1080", "1", "25.0330,121.5654,30"),
        new("德国-法兰克福", "Europe/Berlin",       "de-DE,de;q=0.9", "windows", "1920x1080", "1", "50.1109,8.6821,30"),
        new("英国-伦敦",     "Europe/London",       "en-GB,en;q=0.9", "macos",   "2560x1600", "2", "51.5074,-0.1278,30"),
        new("法国-巴黎",     "Europe/Paris",        "fr-FR,fr;q=0.9", "windows", "1920x1080", "1", "48.8566,2.3522,30"),
        new("加拿大-多伦多", "America/Toronto",     "en-US,en;q=0.9", "macos",   "1920x1080", "2", "43.6532,-79.3832,30"),
        new("澳洲-悉尼",     "Australia/Sydney",    "en-US,en;q=0.9", "macos",   "2560x1440", "2", "-33.8688,151.2093,30"),
    };

    [System.Runtime.InteropServices.DllImport("dwmapi.dll")]
    static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int val, int size);

    protected override void OnSourceInitialized(EventArgs e)
    {
        base.OnSourceInitialized(e);
        try
        {
            var h = new System.Windows.Interop.WindowInteropHelper(this).Handle;
            int dark = Theme.IsLight ? 0 : 1;
            // 20 = DWMWA_USE_IMMERSIVE_DARK_MODE
            DwmSetWindowAttribute(h, 20, ref dark, sizeof(int));
        }
        catch { }
    }

    public EditDialog(Profile p, bool isNew)
    {
        Result = p.Clone();
        _iconValue = Result.icon ?? "";
        _nodes = Store.LoadNodes();

        Title = isNew ? "新建实例" : $"编辑实例 · {p.name}";
        Width = 940;
        // 11 个字段 × (标签 + 输入框 + 间距) 大约要 830，窗口太矮会滚动，
        // 用户看到的就是"方框只露出半截"。按屏幕工作区给足高度。
        Height = Math.Min(920, SystemParameters.WorkArea.Height - 80);
        WindowStartupLocation = WindowStartupLocation.CenterOwner;
        Background = (Brush)Application.Current.Resources["B_Bg"];
        FontFamily = (FontFamily)Application.Current.Resources["F_Ui"];
        Foreground = (Brush)Application.Current.Resources["B_Text"];
        WindowStyle = WindowStyle.SingleBorderWindow;
        ResizeMode = ResizeMode.CanResizeWithGrip;
        UseLayoutRounding = true;
        // 和主窗口保持一致的文字渲染模式，否则两边字形/基线看着不一样
        TextOptions.SetTextFormattingMode(this, TextFormattingMode.Display);
        TextOptions.SetTextHintingMode(this, TextHintingMode.Fixed);
        SnapsToDevicePixels = true;

        var root = new Grid();
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        // ---------- 头部 ----------
        var head = new Border
        {
            Background = (Brush)Application.Current.Resources["B_Header"],
            BorderBrush = (Brush)Application.Current.Resources["B_Line"],
            BorderThickness = new Thickness(0, 0, 0, 1),
            Padding = new Thickness(22, 14, 22, 14)
        };
        var headGrid = new Grid();
        headGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        headGrid.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var titleText = new TextBlock
        {
            Text = isNew ? "新建实例" : $"编辑实例 · {p.name}",
            FontSize = 16,
            FontWeight = FontWeights.SemiBold,
            VerticalAlignment = VerticalAlignment.Center
        };
        Grid.SetColumn(titleText, 0);
        headGrid.Children.Add(titleText);

        head.Child = headGrid;
        Grid.SetRow(head, 0);
        root.Children.Add(head);

        // ---------- 内容 ----------
        var scroll = new ScrollViewer
        {
            VerticalScrollBarVisibility = ScrollBarVisibility.Auto,
            Padding = new Thickness(22, 16, 22, 8)
        };
        var cols = new Grid();
        cols.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        cols.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(18) });
        cols.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });

        var left = BuildColumn(new (string key, string label, Control ctl)[]
        {
            ("name",     "名称（任务栏标识）",      Text(p.name)),
             ("_icon",    "实例图标（任务栏区分）",   IconButton()),
            ("seed",     "指纹种子 seed",           Text(p.seed)),
            ("platform", "平台伪装",                Combo(Platforms, p.platform)),
            ("timezone", "时区 (IANA)",             Combo(Timezones, p.timezone)),
            ("lang",     "语言 (Accept-Language)",  Combo(Langs, p.lang)),
             ("proxy",    "代理 socks5://host:port（或 http://）",  Text(p.proxy)),
            ("rows",     "书签栏行数 1-15",          Text(p.rows)),
            ("node",     "节点 / 节点池",            NodeCombo(p.node)),
            ("geo",      "坐标 lat,lon,精度",        Text(p.geo)),
            ("tags",     "标签（搜索用）",            Text(p.tags)),
            ("extra",    "额外参数",                 Text(p.extra)),
        });
        Grid.SetColumn(left, 0);
        cols.Children.Add(left);

        var right = BuildColumn(new (string key, string label, Control ctl)[]
        {
            ("fp_cores",  "CPU 核数",        Combo(new[] { "", "4", "8", "12", "16", "24", "32" }, p.fp_cores)),
            ("fp_memory", "内存 GB",         Combo(new[] { "", "2", "4", "8", "16", "32" }, p.fp_memory)),
            ("fp_screen", "分辨率 WxH",      Combo(new[] { "", "1920x1080", "2560x1440", "1600x900", "1366x768", "3840x2160", "1280x720", "2560x1600" }, p.fp_screen)),
            ("fp_dpr",    "像素比 DPR",      Combo(new[] { "", "1", "1.25", "1.5", "2", "3" }, p.fp_dpr)),
            ("fp_canvas", "Canvas 噪声",     Combo(new[] { "", "on", "off" }, p.fp_canvas)),
            ("fp_webgl",  "WebGL 型号",      Combo(new[] { "", "nvidia", "amd", "intel", "off" }, p.fp_webgl)),
            ("fp_audio",  "音频噪声",        Combo(new[] { "", "on", "off" }, p.fp_audio)),
            ("fp_fonts",  "字体伪装",        Combo(new[] { "", "on", "off" }, p.fp_fonts)),
            ("webrtc",    "WebRTC IP 防泄漏", Combo(new[] { "", "public", "strict", "off" }, p.webrtc)),
        });
        Grid.SetColumn(right, 2);
        cols.Children.Add(right);

        scroll.Content = cols;
        Grid.SetRow(scroll, 1);
        root.Children.Add(scroll);

        // ---------- 底部 ----------
        var foot = new Border
        {
            Background = (Brush)Application.Current.Resources["B_Header"],
            BorderBrush = (Brush)Application.Current.Resources["B_Line"],
            BorderThickness = new Thickness(0, 1, 0, 0),
            Padding = new Thickness(22, 12, 22, 12)
        };
        var fg = new Grid();
        fg.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        fg.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        fg.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var leftBar = new StackPanel { Orientation = Orientation.Horizontal };
        leftBar.Children.Add(GhostButton("地区预设", RegionPresets_Click));
        leftBar.Children.Add(GhostButton("订阅", Subscribe_Click));
        leftBar.Children.Add(GhostButton("节点池", NodePool_Click));
        leftBar.Children.Add(GhostButton("一键随机", Random_Click));
        Grid.SetColumn(leftBar, 0);
        fg.Children.Add(leftBar);

        var hint = new TextBlock
        {
            Text = "留空 = 跟随 seed 推导；WebRTC：public 隐藏内网 IP，strict 只走代理 UDP",
            Foreground = (Brush)Application.Current.Resources["B_TextDim"],
            FontSize = 11,
            VerticalAlignment = VerticalAlignment.Center,
            Margin = new Thickness(16, 0, 16, 0),
            TextTrimming = TextTrimming.CharacterEllipsis
        };
        Grid.SetColumn(hint, 1);
        fg.Children.Add(hint);

        var rightBar = new StackPanel { Orientation = Orientation.Horizontal };
        var cancel = new Button
        {
            Content = "取消",
            Style = (Style)Application.Current.Resources["Btn"],
            MinWidth = 84,
            Margin = new Thickness(0, 0, 8, 0),
            IsCancel = true
        };
        var save = new Button
        {
            Content = "保存",
            Style = (Style)Application.Current.Resources["BtnPrimary"],
            MinWidth = 84,
            IsDefault = true
        };
        save.Click += (_, _) => Save();
        rightBar.Children.Add(cancel);
        rightBar.Children.Add(save);
        Grid.SetColumn(rightBar, 2);
        fg.Children.Add(rightBar);

        foot.Child = fg;
        Grid.SetRow(foot, 2);
        root.Children.Add(foot);

        Content = root;

        Loaded += (_, _) =>
        {
            scroll.ScrollToTop();
            scroll.ScrollToLeftEnd();
        };
    }

    // ---------------------------------------------------------------- 构建

    Control Text(string v) => new TextBox
    {
        Text = v ?? "",
        Style = (Style)Application.Current.Resources["Input"]
    };

    Control Combo(string[] items, string value)
    {
        var c = new ComboBox { Style = (Style)Application.Current.Resources["Combo"] };
        foreach (var i in items) c.Items.Add(i);
        c.Text = value ?? "";
        return c;
    }

    Control NodeCombo(string value)
    {
        var c = new ComboBox { Style = (Style)Application.Current.Resources["Combo"] };
        foreach (var n in _nodes) c.Items.Add(n.label);
        // 反查：profile 里存的是链接就显示成库里的名字
        var display = value ?? "";
        var hit = _nodes.FirstOrDefault(n => n.link == display);
        if (hit is not null) display = hit.label;
        c.Text = display;
        return c;
    }

    Button GhostButton(string text, RoutedEventHandler handler)
    {
        var b = new Button
        {
            Content = text,
            Style = (Style)Application.Current.Resources["Btn"],
            Margin = new Thickness(0, 0, 8, 0),
            MinWidth = 86
        };
        b.Click += handler;
        return b;
    }

    StackPanel BuildColumn((string key, string label, Control ctl)[] fields)
    {
        var panel = new StackPanel();
        foreach (var f in fields)
        {
            var lbl = new TextBlock
            {
                Text = f.label,
                FontSize = 12.5,
                LineHeight = 17,
                LineStackingStrategy = LineStackingStrategy.BlockLineHeight,
                Foreground = (Brush)Application.Current.Resources["B_TextDim"],
                Margin = new Thickness(2, 0, 0, 5)
            };
            f.ctl.Margin = new Thickness(0, 0, 0, 10);
            f.ctl.Height = 34;
            panel.Children.Add(lbl);
            panel.Children.Add(f.ctl);
            _ctl[f.key] = f.ctl;
        }
        return panel;
    }

    // ---------------------------------------------------------------- 图标选择

    Control IconButton()
    {
        var btn = new Button
        {
            Height = 34,
            HorizontalAlignment = HorizontalAlignment.Stretch,
            Style = (Style)Application.Current.Resources["Btn"],
            Padding = new Thickness(10, 0, 10, 0)
        };
        void Refresh()
        {
            var png = Paths.IconPng(_iconValue);
            var sp = new StackPanel { Orientation = Orientation.Horizontal };
            if (png.Length > 0 && File.Exists(png))
            {
                try
                {
                    var bi = new System.Windows.Media.Imaging.BitmapImage();
                    bi.BeginInit();
                    bi.UriSource = new System.Uri(png, System.UriKind.Absolute);
                    bi.CacheOption = System.Windows.Media.Imaging.BitmapCacheOption.OnLoad;
                    bi.EndInit();
                    sp.Children.Add(new Image { Source = bi, Width = 22, Height = 22,
                                               Margin = new Thickness(0, 0, 8, 0) });
                }
                catch { }
            }
            sp.Children.Add(new TextBlock {
                Text = string.IsNullOrEmpty(_iconValue) ? "默认（跟随浏览器）" : "图标 " + _iconValue,
                VerticalAlignment = VerticalAlignment.Center,
                Foreground = (Brush)Application.Current.Resources["B_Text"] });
            sp.Children.Add(new TextBlock {
                Text = "   点击选择…", VerticalAlignment = VerticalAlignment.Center,
                Foreground = (Brush)Application.Current.Resources["B_TextDim"] });
            btn.Content = sp;
        }
        Refresh();
        btn.Click += (_, _) =>
        {
            var r = IconPickerDialog.Ask(this, _iconValue);
            if (r is null) return;
            _iconValue = r;
            Refresh();
        };
        return btn;
    }

    // ---------------------------------------------------------------- 读取

    string Get(string key)
        => _ctl.TryGetValue(key, out var c)
            ? (c is ComboBox cb ? cb.Text : ((TextBox)c).Text).Trim()
            : "";

    void Set(string key, string value)
    {
        if (!_ctl.TryGetValue(key, out var c)) return;
        if (c is ComboBox cb)
        {
            cb.Text = value;
            var hit = cb.Items.Cast<object>().FirstOrDefault(x => (x?.ToString() ?? "") == value);
            if (hit is not null) cb.SelectedItem = hit;
        }
        else ((TextBox)c).Text = value;
    }

    void Save()
    {
        var r = Result;
        r.name = Get("name");
        r.seed = Get("seed");
        r.platform = Get("platform");
        r.timezone = Get("timezone");
        r.lang = Get("lang");
        r.proxy = Get("proxy");
        r.region = Get("region");
        r.rows = Get("rows");
        r.extra = Get("extra");
        r.fp_cores = Get("fp_cores");
        r.fp_memory = Get("fp_memory");
        r.fp_screen = Get("fp_screen");
        r.fp_dpr = Get("fp_dpr");
        r.fp_canvas = Get("fp_canvas");
        r.fp_webgl = Get("fp_webgl");
        r.fp_audio = Get("fp_audio");
        r.fp_fonts = Get("fp_fonts");
        r.webrtc = Get("webrtc");
        r.geo = Get("geo");
        r.tags = Get("tags");
        r.icon = _iconValue;

        // 节点：库里的名字换成真链接，池逐段解析
        var raw = Get("node");
        if (raw.Length == 0) r.node = "";
        else
        {
            var parts = raw.Split('|', StringSplitOptions.RemoveEmptyEntries)
                           .Select(x => x.Trim()).Where(x => x.Length > 0);
            var resolved = new List<string>();
            foreach (var seg in parts)
            {
                var hit = _nodes.FirstOrDefault(n => n.label == seg || n.name == seg);
                resolved.Add(hit?.link ?? seg);
            }
            r.node = string.Join("|", resolved);
        }

        DialogResult = true;
        Close();
    }

    // ---------------------------------------------------------------- 按钮

    void RegionPresets_Click(object sender, RoutedEventArgs e)
    {
        var menu = new ContextMenu();
        foreach (var p in Presets)
        {
            var mi = new MenuItem { Header = p.Name };
            mi.Click += (_, _) =>
            {
                Set("timezone", p.Tz);
                Set("lang", p.Lang);
                Set("platform", p.Platform);
                Set("fp_screen", p.Screen);
                Set("fp_dpr", p.Dpr);
                Set("geo", p.Geo);
            };
            menu.Items.Add(mi);
        }
        menu.PlacementTarget = (UIElement)sender;
        menu.IsOpen = true;
    }

    void Subscribe_Click(object sender, RoutedEventArgs e)
    {
        var url = InputDialog.Ask(this, "订阅链接", "粘贴订阅地址（http/https）", "");
        if (string.IsNullOrWhiteSpace(url)) return;

        Mouse.OverrideCursor = System.Windows.Input.Cursors.Wait;
        try
        {
            var (c, so, se) = Py.Run(Paths.NodeProxy, "nodes", "--action", "import", "--url", url.Trim());
            if (c != 0)
            {
                MessageBox.Show($"订阅失败：\n\n{so}\n{se}", "订阅", MessageBoxButton.OK, MessageBoxImage.Error);
                return;
            }
            // 重新加载节点库并刷新下拉项
            _nodes.Clear();
            _nodes.AddRange(Store.LoadNodes());
            if (_ctl.TryGetValue("node", out var ctl) && ctl is ComboBox cb)
            {
                var cur = cb.Text;
                cb.Items.Clear();
                foreach (var n in _nodes) cb.Items.Add(n.label);
                cb.Text = cur;
                if (_nodes.Count > 0) cb.Text = _nodes[0].label;
            }
            MessageBox.Show(so.Trim(), "订阅");
        }
        finally { Mouse.OverrideCursor = null; }
    }

    void NodePool_Click(object sender, RoutedEventArgs e)
    {
        if (_nodes.Count == 0)
        {
            MessageBox.Show("节点库是空的。先点「订阅」拉一批。", "节点池",
                            MessageBoxButton.OK, MessageBoxImage.Information);
            return;
        }

        var cur = Get("node");
        var curLabels = new HashSet<string>();
        foreach (var seg in cur.Split('|', StringSplitOptions.RemoveEmptyEntries))
        {
            var s = seg.Trim();
            if (s.Length == 0) continue;
            var hit = _nodes.FirstOrDefault(n => n.link == s || n.label == s);
            curLabels.Add(hit?.label ?? s);
        }

        var win = new Window
        {
            Title = "选择节点（可多选，按顺序组成节点池）",
            Width = 620, Height = 560,
            Owner = this,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Background = (Brush)Application.Current.Resources["B_Bg"],
            FontFamily = (FontFamily)Application.Current.Resources["F_Ui"],
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            WindowStyle = WindowStyle.ToolWindow
        };

        var g = new Grid { Margin = new Thickness(18) };
        g.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        g.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var lb = new ListBox
        {
            Background = (Brush)Application.Current.Resources["B_Card"],
            BorderBrush = (Brush)Application.Current.Resources["B_Line"],
            BorderThickness = new Thickness(1),
            SelectionMode = SelectionMode.Multiple,
            Foreground = (Brush)Application.Current.Resources["B_Text"]
        };
        foreach (var n in _nodes)
        {
            var item = new ListBoxItem
            {
                Content = n.label,
                IsSelected = curLabels.Contains(n.label),
                Padding = new Thickness(8, 6, 8, 6)
            };
            lb.Items.Add(item);
        }
        Grid.SetRow(lb, 0);
        g.Children.Add(lb);

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
            MinWidth = 84,
            Margin = new Thickness(0, 0, 8, 0)
        };
        cancel.Click += (_, _) => win.Close();
        var ok = new Button
        {
            Content = "确定",
            Style = (Style)Application.Current.Resources["BtnPrimary"],
            MinWidth = 84
        };
        ok.Click += (_, _) =>
        {
            var picked = lb.SelectedItems.Cast<ListBoxItem>()
                           .Select(x => x.Content?.ToString() ?? "")
                           .Where(x => x.Length > 0).ToList();
            Set("node", string.Join("|", picked));
            win.Close();
        };
        bar.Children.Add(cancel);
        bar.Children.Add(ok);
        Grid.SetRow(bar, 1);
        g.Children.Add(bar);

        win.Content = g;
        win.ShowDialog();
    }

    void Random_Click(object sender, RoutedEventArgs e)
    {
        var rnd = Random.Shared;
        Set("seed", rnd.Next(10000, 999999).ToString());
        Set("fp_cores", new[] { "4", "8", "12", "16", "24", "32" }[rnd.Next(6)]);
        Set("fp_memory", new[] { "2", "4", "8", "16", "32" }[rnd.Next(5)]);
        Set("fp_screen", new[] { "1920x1080", "2560x1440", "1600x900", "1366x768", "3840x2160", "1280x720" }[rnd.Next(6)]);
        Set("fp_dpr", new[] { "1", "1.25", "1.5", "2", "3" }[rnd.Next(5)]);
        Set("fp_canvas", rnd.Next(2) == 0 ? "on" : "off");
        Set("fp_webgl", new[] { "nvidia", "amd", "intel" }[rnd.Next(3)]);
        Set("fp_audio", rnd.Next(2) == 0 ? "on" : "off");
        Set("fp_fonts", rnd.Next(2) == 0 ? "on" : "off");
    }
}
