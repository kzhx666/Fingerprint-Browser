using System.Windows;
using System.Windows.Media;

namespace KZManager;

/// <summary>深浅两套配色，运行时整体替换 App 资源里的画刷。</summary>
public static class Theme
{
    public sealed record Palette(
        string Bg, string Card, string CardHover, string Line,
        string Text, string TextDim, string Accent, string Green,
        string Red, string Amber, string Header, string RowHover,
        string RowSel, string Dim2, string Scroll, string BorderHi,
        string AltRow, string PrimaryText);

    public static readonly Palette Dark = new(
        "#0E1116", "#171C24", "#1E242E", "#252C37",
        "#E8EDF4", "#8B97A8", "#4C8DFF", "#3DDC97",
        "#FF6B6B", "#FFC857", "#12161D", "#1B222C",
        "#1E2C45", "#5A6473", "#3A4553", "#3A4553",
        "#12161D", "#FFFFFF");

    public static readonly Palette Light = new(
        "#F4F6FA", "#FFFFFF", "#EAF0F8", "#DCE3EC",
        "#1B2430", "#5C6A7D", "#2F6FEB", "#12A150",
        "#E5484D", "#B7791F", "#FFFFFF", "#EEF3FA",
        "#DCE9FF", "#8B97A8", "#C3CDDA", "#C3CDDA",
        "#FAFBFD", "#FFFFFF");

    public static string Current { get; private set; } = "dark";

    public static void Apply(string name)
    {
        var p = string.Equals(name, "light", StringComparison.OrdinalIgnoreCase)
            ? Light : Dark;
        Current = ReferenceEquals(p, Light) ? "light" : "dark";

        var res = Application.Current?.Resources;
        if (res is null) return;

        void Set(string key, string hex)
            => res[key] = new SolidColorBrush((Color)ColorConverter.ConvertFromString(hex));

        Set("B_Bg", p.Bg);
        Set("B_Card", p.Card);
        Set("B_CardHover", p.CardHover);
        Set("B_Line", p.Line);
        Set("B_Text", p.Text);
        Set("B_TextDim", p.TextDim);
        Set("B_Accent", p.Accent);
        Set("B_Green", p.Green);
        Set("B_Red", p.Red);
        Set("B_Amber", p.Amber);
        Set("B_Header", p.Header);
        Set("B_RowHover", p.RowHover);
        Set("B_RowSel", p.RowSel);
        Set("B_Dim2", p.Dim2);
        Set("B_Scroll", p.Scroll);
        Set("B_BorderHi", p.BorderHi);
        Set("B_AltRow", p.AltRow);
        Set("B_PrimaryText", p.PrimaryText);
    }

    public static string Toggle()
    {
        var next = Current == "dark" ? "light" : "dark";
        Apply(next);
        return next;
    }

    /// <summary>浅色主题下强调色按钮上的文字要变白（深色本来就白）。</summary>
    public static bool IsLight => Current == "light";
}
