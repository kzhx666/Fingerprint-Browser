using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace KZManager;

/// <summary>删除实例的确认对话框：列出会删掉什么，要求手打实例名确认。</summary>
public static class DeleteDialog
{
    public sealed class Options
    {
        public bool DeleteBackups = true;
        public bool DeleteSnapshots = true;
        public bool Confirmed;
    }

    public sealed class Info
    {
        public string Name = "";
        public long InstanceBytes;
        public int BackupCount;
        public long BackupBytes;
        public int SnapshotCount;
        public long SnapshotBytes;
        public bool Running;
        public bool IsMaster;

        public long TotalBytes => InstanceBytes + BackupBytes + SnapshotBytes;
    }

    public static string Human(long bytes)
    {
        if (bytes >= 1024L * 1024 * 1024) return $"{bytes / 1024.0 / 1024 / 1024:N2} GB";
        if (bytes >= 1024L * 1024) return $"{bytes / 1024.0 / 1024:N1} MB";
        if (bytes >= 1024) return $"{bytes / 1024.0:N1} KB";
        return $"{bytes} B";
    }

    public static Options? Ask(Window owner, Info info)
    {
        var res = new Options();

        var win = new Window
        {
            Title = "删除实例",
            Width = 520,
            SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = owner,
            ResizeMode = ResizeMode.NoResize,
            Background = (Brush)Application.Current.Resources["B_Bg"],
            FontFamily = (FontFamily)Application.Current.Resources["F_Ui"],
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            WindowStyle = WindowStyle.ToolWindow,
            ShowInTaskbar = false
        };

        var root = new StackPanel { Margin = new Thickness(22) };

        // 标题
        root.Children.Add(new TextBlock
        {
            Text = $"永久删除实例「{info.Name}」？",
            FontSize = 16,
            FontWeight = FontWeights.SemiBold,
            TextWrapping = TextWrapping.Wrap,
            Margin = new Thickness(0, 0, 0, 4)
        });

        root.Children.Add(new TextBlock
        {
            Text = "删除后无法恢复。",
            FontSize = 12,
            Foreground = (Brush)Application.Current.Resources["B_Red"],
            Margin = new Thickness(0, 0, 0, 16)
        });

        if (info.Running)
        {
            var warn = new Border
            {
                Background = new SolidColorBrush(Color.FromArgb(40, 255, 200, 80)),
                BorderBrush = (Brush)Application.Current.Resources["B_Amber"],
                BorderThickness = new Thickness(1, 0, 0, 0),
                Padding = new Thickness(10, 8, 10, 8),
                Margin = new Thickness(0, 0, 0, 14),
                CornerRadius = new CornerRadius(4)
            };
            warn.Child = new TextBlock
            {
                Text = "该实例正在运行，会先强制结束它的所有进程。",
                FontSize = 12,
                TextWrapping = TextWrapping.Wrap,
                Foreground = (Brush)Application.Current.Resources["B_Amber"]
            };
            root.Children.Add(warn);
        }

        if (info.IsMaster)
        {
            var warn = new Border
            {
                Background = new SolidColorBrush(Color.FromArgb(40, 255, 107, 107)),
                BorderBrush = (Brush)Application.Current.Resources["B_Red"],
                BorderThickness = new Thickness(1, 0, 0, 0),
                Padding = new Thickness(10, 8, 10, 8),
                Margin = new Thickness(0, 0, 0, 14),
                CornerRadius = new CornerRadius(4)
            };
            warn.Child = new TextBlock
            {
                Text = "这是主控浏览器。删掉后默认浏览器会指向一个不存在的目录，需要重新注册。",
                FontSize = 12,
                TextWrapping = TextWrapping.Wrap,
                Foreground = (Brush)Application.Current.Resources["B_Red"]
            };
            root.Children.Add(warn);
        }

        // 清单
        var list = new StackPanel { Margin = new Thickness(0, 0, 0, 14) };

        var cbInst = new CheckBox
        {
            Content = $"实例目录   {Human(info.InstanceBytes)}",
            IsChecked = true,
            IsEnabled = false,      // 实例本体必须删
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            FontSize = 13,
            Margin = new Thickness(0, 0, 0, 8)
        };
        list.Children.Add(cbInst);

        var cbBackup = new CheckBox
        {
            Content = info.BackupCount > 0
                ? $"备份 {info.BackupCount} 份   {Human(info.BackupBytes)}"
                : "备份   没有",
            IsChecked = info.BackupCount > 0,
            IsEnabled = info.BackupCount > 0,
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            FontSize = 13,
            Margin = new Thickness(0, 0, 0, 8)
        };
        list.Children.Add(cbBackup);

        var cbSnap = new CheckBox
        {
            Content = info.SnapshotCount > 0
                ? $"快照 {info.SnapshotCount} 个   {Human(info.SnapshotBytes)}"
                : "快照   没有",
            IsChecked = info.SnapshotCount > 0,
            IsEnabled = info.SnapshotCount > 0,
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            FontSize = 13,
            Margin = new Thickness(0, 0, 0, 8)
        };
        list.Children.Add(cbSnap);

        root.Children.Add(list);

        // 合计
        var total = new TextBlock
        {
            Text = $"合计释放约 {Human(info.TotalBytes)}",
            FontSize = 13,
            FontWeight = FontWeights.SemiBold,
            Foreground = (Brush)Application.Current.Resources["B_Accent"],
            Margin = new Thickness(0, 0, 0, 16)
        };
        root.Children.Add(total);

        void Recalc()
        {
            long t = info.InstanceBytes;
            if (cbBackup.IsChecked == true) t += info.BackupBytes;
            if (cbSnap.IsChecked == true) t += info.SnapshotBytes;
            total.Text = $"合计释放约 {Human(t)}";
        }
        cbBackup.Checked += (_, _) => Recalc();
        cbBackup.Unchecked += (_, _) => Recalc();
        cbSnap.Checked += (_, _) => Recalc();
        cbSnap.Unchecked += (_, _) => Recalc();

        // 手打名字确认
        root.Children.Add(new TextBlock
        {
            Text = "输入实例名确认（区分大小写）：",
            FontSize = 12,
            Foreground = (Brush)Application.Current.Resources["B_TextDim"],
            Margin = new Thickness(0, 0, 0, 6)
        });

        var tb = new TextBox
        {
            Style = (Style)Application.Current.Resources["Input"],
            Height = 34,
            Margin = new Thickness(0, 0, 0, 16)
        };
        root.Children.Add(tb);

        // 按钮
        var bar = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right
        };

        var cancel = new Button
        {
            Content = "取消",
            Style = (Style)Application.Current.Resources["Btn"],
            MinWidth = 88,
            Margin = new Thickness(0, 0, 8, 0),
            IsCancel = true
        };
        cancel.Click += (_, _) => { res.Confirmed = false; win.DialogResult = false; win.Close(); };

        var del = new Button
        {
            Content = "永久删除",
            Style = (Style)Application.Current.Resources["BtnDanger"],
            MinWidth = 100,
            IsEnabled = false
        };
        del.Click += (_, _) =>
        {
            res.Confirmed = true;
            res.DeleteBackups = cbBackup.IsChecked == true;
            res.DeleteSnapshots = cbSnap.IsChecked == true;
            // 必须设 DialogResult，否则 ShowDialog() 返回 null，调用方以为用户取消了
            win.DialogResult = true;
            win.Close();
        };

        tb.TextChanged += (_, _) =>
        {
            del.IsEnabled = tb.Text == info.Name;
            del.Style = tb.Text == info.Name
                ? (Style)Application.Current.Resources["BtnPrimary"]
                : (Style)Application.Current.Resources["BtnDanger"];
        };

        bar.Children.Add(cancel);
        bar.Children.Add(del);
        root.Children.Add(bar);

        win.Content = new ScrollViewer { Content = root, VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        win.Loaded += (_, _) => tb.Focus();

        win.ShowDialog();
        return res.Confirmed ? res : null;
    }
}
