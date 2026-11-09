using System.Windows;

namespace KZManager;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // EXE 可能被放在任意位置；如果同目录没有实例，往上找两层找 manager.json
        if (!System.IO.File.Exists(Paths.ConfigFile))
        {
            var dir = new System.IO.DirectoryInfo(AppContext.BaseDirectory);
            for (int i = 0; i < 3 && dir is not null; i++, dir = dir!.Parent)
            {
                if (dir is not null &&
                    System.IO.File.Exists(System.IO.Path.Combine(dir.FullName, "manager.json")))
                {
                    Paths.SetRoot(dir.FullName);
                    break;
                }
            }
        }

        DispatcherUnhandledException += (_, ex) =>
        {
            MessageBox.Show($"出错了：\n\n{ex.Exception.Message}\n\n{ex.Exception.StackTrace}",
                            "错误", MessageBoxButton.OK, MessageBoxImage.Warning);
            ex.Handled = true;
        };
    }
}
