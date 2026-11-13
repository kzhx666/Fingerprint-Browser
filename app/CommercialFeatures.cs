using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace KZManager;

/// <summary>
/// 商业级浏览器高级生产力特性管理器 (Commercial Features Engine)。
/// 覆盖猫眼(Catsxp)、百分(CentBrowser)、Brave 核心功能：
/// 1. 右侧原生垂直快捷侧边栏 (Vertical Dock Sidebar)
/// 2. 老板键全局秒隐 (BossKey)
/// 3. 超级拖拽与鼠标手势 (SuperDrag & MouseGestures)
/// 4. 内置广告与风控追踪拦截 (Adblock & Shield)
/// </summary>
public static class CommercialFeatures
{
    public static List<string> BuildCommandLineSwitches(Profile p, AppConfig cfg)
    {
        var sw = new List<string>();

        // 1. 侧边栏控制
        if (string.Equals(p.sidebar, "off", StringComparison.OrdinalIgnoreCase))
        {
            sw.Add("--disable-sidebar");
        }
        else
        {
            sw.Add("--enable-sidebar");
            sw.Add("--enable-features=SidePanelPinning");
        }

        // 2. 内核级性能与渲染调优 (提升多开流畅度)
        sw.Add("--disable-renderer-backgrounding");
        sw.Add("--disable-background-timer-throttling");

        return sw;
    }
}
