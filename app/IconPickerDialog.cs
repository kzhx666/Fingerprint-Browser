using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;
using System.Windows.Media.Imaging;
using System.IO;

namespace KZManager;

/// <summary>50 个内置实例图标的网格选择器。</summary>
public static class IconPickerDialog
{
    public static string? Ask(Window owner, string current)
    {
        var win = new Window
        {
            Title = "选择实例图标",
            Width = 620,
            Height = 520,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = owner,
            ResizeMode = ResizeMode.NoResize,
            Background = (Brush)Application.Current.Resources["B_Bg"],
            FontFamily = (FontFamily)Application.Current.Resources["F_Ui"],
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            ShowInTaskbar = false
        };

        var root = new Grid { Margin = new Thickness(16) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var head = new TextBlock
        {
            Text = "选一个图标，不同实例用不同图标，任务栏就不会搞混",
            FontSize = 13,
            Foreground = (Brush)Application.Current.Resources["B_TextDim"],
            Margin = new Thickness(0, 0, 0, 12)
        };
        Grid.SetRow(head, 0);
        root.Children.Add(head);

        var scroll = new ScrollViewer { VerticalScrollBarVisibility = ScrollBarVisibility.Auto };
        Grid.SetRow(scroll, 1);
        var wrap = new WrapPanel { Orientation = Orientation.Horizontal };
        scroll.Content = wrap;

        string picked = current;
        var borders = new System.Collections.Generic.List<Border>();

        Border MakeItem(string id, string pngPath)
        {
            var b = new Border
            {
                Width = 84,
                Height = 92,
                Margin = new Thickness(4),
                CornerRadius = new CornerRadius(8),
                Background = (Brush)Application.Current.Resources["B_Card"],
                BorderBrush = (Brush)Application.Current.Resources["B_Line"],
                BorderThickness = new Thickness(2),
                Cursor = System.Windows.Input.Cursors.Hand,
                Tag = id
            };
            var sp = new StackPanel { HorizontalAlignment = HorizontalAlignment.Center };
            Image? img = null;
            if (File.Exists(pngPath))
            {
                try
                {
                    var bi = new BitmapImage();
                    bi.BeginInit();
                    bi.UriSource = new System.Uri(pngPath, System.UriKind.Absolute);
                    bi.CacheOption = BitmapCacheOption.OnLoad;
                    bi.EndInit();
                    img = new Image { Source = bi, Width = 48, Height = 48, Margin = new Thickness(0, 8, 0, 4) };
                    sp.Children.Add(img);
                }
                catch { }
            }
            sp.Children.Add(new TextBlock
            {
                Text = id.Replace("icon-", "#"),
                FontSize = 11,
                HorizontalAlignment = HorizontalAlignment.Center,
                Foreground = (Brush)Application.Current.Resources["B_TextDim"]
            });
            b.Child = sp;
            b.MouseLeftButtonUp += (_, _) =>
            {
                picked = id;
                foreach (var x in borders)
                    x.BorderBrush = (Brush)Application.Current.Resources["B_Line"];
                b.BorderBrush = (Brush)Application.Current.Resources["B_Accent"];
            };
            borders.Add(b);
            return b;
        }

        // 「默认」项
        var def = new Border
        {
            Width = 84, Height = 92, Margin = new Thickness(4),
            CornerRadius = new CornerRadius(8),
            Background = (Brush)Application.Current.Resources["B_Card"],
            BorderBrush = (string.IsNullOrEmpty(current)
                ? (Brush)Application.Current.Resources["B_Accent"]
                : (Brush)Application.Current.Resources["B_Line"]),
            BorderThickness = new Thickness(2),
            Cursor = System.Windows.Input.Cursors.Hand, Tag = ""
        };
        var defSp = new StackPanel { HorizontalAlignment = HorizontalAlignment.Center };
        defSp.Children.Add(new TextBlock {
            Text = "默认", FontSize = 13, Margin = new Thickness(0, 28, 0, 0),
            HorizontalAlignment = HorizontalAlignment.Center,
            Foreground = (Brush)Application.Current.Resources["B_Text"] });
        defSp.Children.Add(new TextBlock {
            Text = "跟随浏览器", FontSize = 10, Margin = new Thickness(0, 6, 0, 0),
            HorizontalAlignment = HorizontalAlignment.Center,
            Foreground = (Brush)Application.Current.Resources["B_TextDim"] });
        def.Child = defSp;
        def.MouseLeftButtonUp += (_, _) => {
            picked = "";
            foreach (var x in borders) x.BorderBrush = (Brush)Application.Current.Resources["B_Line"];
            def.BorderBrush = (Brush)Application.Current.Resources["B_Accent"];
        };
        borders.Add(def);
        wrap.Children.Add(def);

        for (int i = 0; i < 50; i++)
        {
            var id = $"icon-{i:00}";
            var png = Paths.IconPng(id);
            var item = MakeItem(id, png);
            if (id == current)
                item.BorderBrush = (Brush)Application.Current.Resources["B_Accent"];
            wrap.Children.Add(item);
        }
        root.Children.Add(scroll);

        var bar = new StackPanel {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 12, 0, 0)
        };
        var cancel = new Button {
            Content = "取消", Style = (Style)Application.Current.Resources["Btn"],
            MinWidth = 84, Margin = new Thickness(0, 0, 8, 0), IsCancel = true };
        cancel.Click += (_, _) => { win.DialogResult = false; win.Close(); };
        var ok = new Button {
            Content = "确定", Style = (Style)Application.Current.Resources["BtnPrimary"],
            MinWidth = 84, IsDefault = true };
        string? result = null;
        ok.Click += (_, _) => { result = picked; win.DialogResult = true; win.Close(); };
        bar.Children.Add(cancel);
        bar.Children.Add(ok);
        Grid.SetRow(bar, 2);
        root.Children.Add(bar);

        win.Content = root;
        return win.ShowDialog() == true ? result : null;
    }
}
