using System.Windows;

namespace KZManager;

internal static class NativeAumid
{
    [System.Runtime.InteropServices.DllImport("shell32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    /// <summary>
    /// 给管理器一个独立的 AppUserModelID。
    ///
    /// 不设的话 Windows 会按 exe 路径推一个；但如果是从「带 AUMID 的快捷方式」
    /// 启动的，Windows 会把快捷方式的 AUMID 赋给进程，导致管理器窗口和浏览器
    /// 合并成同一个任务栏按钮。显式设置可以盖掉那个继承值。
    /// </summary>
    public static void EnsureManagerId()
    {
        try { SetCurrentProcessExplicitAppUserModelID("KZ.Manager"); } catch { }
    }
}

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        base.OnStartup(e);

        // 管理器必须有独立 AUMID，否则会和浏览器合并到同一个任务栏按钮
        NativeAumid.EnsureManagerId();

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

        // ---- 命令行模式：--launch "实例名" ----
        // 给任务栏快捷方式用，走和管理器「启动」完全一样的流程。
        // 15: --api [port] 本地自动化接口（不弹主窗口）
        var argv = e.Args;
        int apiIdx = Array.IndexOf(argv, "--api");
        if (apiIdx >= 0)
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            int port = 8790;
            if (apiIdx + 1 < argv.Length && int.TryParse(argv[apiIdx + 1], out var p)) port = p;
            LocalApi.Start(port);
            return;   // 不创建 MainWindow
        }
        // ---- 命令行模式：--add-sidebar-site "<profile data dir>" ----
        // 由浏览器侧边栏的 "+" 调起：弹输入框，写入该实例的
        // kz_sidebar_sites.txt（name<TAB>url 每行一条）。
        var addSiteIdx = Array.FindIndex(argv, x => x.StartsWith("--add-sidebar-site="));
        if (addSiteIdx >= 0)
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            var profileDir = argv[addSiteIdx].Substring("--add-sidebar-site=".Length).Trim('"');
            var name = InputDialog.Ask(null!, "添加侧边栏网页", "名称（显示在侧边栏图标上）", "");
            if (!string.IsNullOrWhiteSpace(name))
            {
                var url = InputDialog.Ask(null!, "添加侧边栏网页", "网址（https://...）", "https://");
                if (!string.IsNullOrWhiteSpace(url))
                {
                    try
                    {
                        System.IO.Directory.CreateDirectory(profileDir);
                        var file = System.IO.Path.Combine(profileDir, "kz_sidebar_sites.txt");
                        System.IO.File.AppendAllText(file,
                            name.Trim() + "\t" + url.Trim() + "\n",
                            new System.Text.UTF8Encoding(false));
                        MessageBox.Show("已添加：" + name.Trim() +
                                        "\n\n重启该实例（或新开窗口）后，侧边栏会出现这个图标。",
                                        "侧边栏", MessageBoxButton.OK, MessageBoxImage.Information);
                    }
                    catch (Exception ex)
                    {
                        MessageBox.Show("写入失败：" + ex.Message, "侧边栏",
                                        MessageBoxButton.OK, MessageBoxImage.Warning);
                    }
                }
            }
            Shutdown(0);
            return;
        }

        // ---- 命令行模式：--set-default / --unset-default ----
        if (Array.IndexOf(argv, "--set-default") >= 0)
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            var msg = DefaultBrowser.Register();
            System.Windows.MessageBox.Show(msg, "默认浏览器路由",
                MessageBoxButton.OK, MessageBoxImage.Information);
            Shutdown(0);
            return;
        }
        if (Array.IndexOf(argv, "--unset-default") >= 0)
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            System.Windows.MessageBox.Show(DefaultBrowser.Unregister(), "默认浏览器路由",
                MessageBoxButton.OK, MessageBoxImage.Information);
            Shutdown(0);
            return;
        }

        // ---- 命令行模式：--route-url "<url>" ----
        // 作为「默认浏览器」被系统调起时走这里：先判断前台是哪个实例，
        // 再用显式 --user-data-dir 把链接转发过去。绝不修改任何共享配置。
        int routeIdx = Array.IndexOf(argv, "--route-url");
        if (routeIdx >= 0 && routeIdx + 1 < argv.Length)
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;
            var url = argv[routeIdx + 1];
            var ok = SmartDefault.RouteUrl(url);
            Shutdown(ok ? 0 : 1);
            return;
        }

        int idx = Array.IndexOf(argv, "--launch");
        if (idx >= 0)
        {
            ShutdownMode = ShutdownMode.OnExplicitShutdown;

            if (idx + 1 >= argv.Length)
            {
                MessageBox.Show("用法：KZManager.exe --launch \"实例名\"",
                                "参数错误", MessageBoxButton.OK, MessageBoxImage.Warning);
                Shutdown(2);
                return;
            }

            var name = argv[idx + 1];
            var cfg = Store.LoadConfig();
            var r = Launcher.Run(name, cfg);

            if (!r.Ok)
                MessageBox.Show(r.Message, $"启动「{name}」失败",
                                MessageBoxButton.OK, MessageBoxImage.Warning);

            Shutdown(r.Ok ? 0 : 1);
            return;
        }

        // ---- 正常启动界面 ----
        var win = new MainWindow();
        MainWindow = win;
        win.Show();
    }
}
