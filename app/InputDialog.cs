using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;

namespace KZManager;

/// <summary>轻量的单行输入对话框，样式跟主界面一致。</summary>
public static class InputDialog
{
    public static string? Ask(Window owner, string title, string prompt, string initial)
    {
        var win = new Window
        {
            Title = title,
            Width = 520,
            SizeToContent = SizeToContent.Height,
            WindowStartupLocation = WindowStartupLocation.CenterOwner,
            Owner = owner,
            ResizeMode = ResizeMode.NoResize,
            Background = (Brush)Application.Current.Resources["B_Bg"],
            FontFamily = (FontFamily)Application.Current.Resources["F_Ui"],
            Foreground = (Brush)Application.Current.Resources["B_Text"],
            WindowStyle = WindowStyle.ToolWindow
        };

        var root = new Grid { Margin = new Thickness(20) };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });

        var lbl = new TextBlock
        {
            Text = prompt,
            TextWrapping = TextWrapping.Wrap,
            Foreground = (Brush)Application.Current.Resources["B_TextDim"],
            Margin = new Thickness(0, 0, 0, 10)
        };
        Grid.SetRow(lbl, 0);
        root.Children.Add(lbl);

        var tb = new TextBox
        {
            Text = initial,
            Style = (Style)Application.Current.Resources["Input"],
            AcceptsReturn = prompt.Contains('\n')
        };
        if (prompt.Contains('\n'))
        {
            tb.AcceptsReturn = true;
            tb.TextWrapping = TextWrapping.Wrap;
            tb.Height = 90;
            tb.VerticalContentAlignment = VerticalAlignment.Top;
        }
        Grid.SetRow(tb, 1);
        root.Children.Add(tb);

        var bar = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            HorizontalAlignment = HorizontalAlignment.Right,
            Margin = new Thickness(0, 16, 0, 0)
        };
        var cancel = new Button
        {
            Content = "取消",
            Style = (Style)Application.Current.Resources["Btn"],
            MinWidth = 80,
            Margin = new Thickness(0, 0, 8, 0),
            IsCancel = true
        };
        var ok = new Button
        {
            Content = "确定",
            Style = (Style)Application.Current.Resources["BtnPrimary"],
            MinWidth = 80,
            IsDefault = true
        };
        ok.Click += (_, _) => { win.DialogResult = true; win.Close(); };
        bar.Children.Add(cancel);
        bar.Children.Add(ok);
        Grid.SetRow(bar, 2);
        root.Children.Add(bar);

        win.Content = root;
        win.Loaded += (_, _) =>
        {
            tb.Focus();
            if (!prompt.Contains('\n')) tb.SelectAll();
        };

        return win.ShowDialog() == true ? tb.Text : null;
    }
}
