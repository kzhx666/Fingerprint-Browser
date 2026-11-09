# KZ 多开管理器

一个 Windows 下的 **Chromium 多开管理器**：用一个面板管理几十个互相隔离的浏览器实例，每个实例有独立的指纹、代理、时区、语言，并且可以克隆、快照、同步数据。

配套一份 Chromium 内核补丁，让浏览器真正报告伪造的硬件参数（不是 JS 层 hook）。

> 本项目由 [apply_patches.py](app/) 维护的内核补丁 + WPF 管理器 + 一组 Python 工具组成。

---

## 功能

### 实例管理

- **多实例隔离** —— 每个实例独立 `user-data-dir`，互不影响
- **拖动排序** —— 每行左侧竖点把手，拖动时显示蓝色插入线
- **克隆** —— 只复制配置（换新 seed），或连数据一起复制（书签/密码/历史/扩展/登录态）
- **快照** —— 硬链接去重，6441 个文件只占 6.3 MB
- **数据同步** —— 从 A 实例把书签/历史/密码/扩展同步到 B，自动备份
- **搜索** —— 按名称/标签/平台/时区/seed 实时过滤

### 指纹伪装

| 项目 | 说明 |
|---|---|
| 平台 | `navigator.platform` / UA / UA-CH 三处一致 |
| 时区 | `Intl.DateTimeFormat().resolvedOptions().timeZone` |
| 语言 | `navigator.language` + **`Accept-Language` 请求头** |
| 硬件 | CPU 核数 / 内存 / 分辨率 / 像素比 |
| Canvas | 每实例独立噪声 |
| WebGL | 显卡型号伪装 |
| Audio | 指纹噪声 |
| 字体 | 按平台过滤（macOS 110 / Windows 136 / Linux 87 种） |
| 地理位置 | 经纬度 + 精度，直接接管 Geolocation API |
| WebRTC | `public` / `strict` / `off` 三档防泄漏 |

**所有值由 `seed` 一个数字推导**，同 seed = 同指纹，换 seed = 换一台新机器。

### 其他

- **深色 / 浅色主题** —— 右上角 ☀ / ☾ 切换，即时生效
- **节点库** —— 订阅链接批量导入 vless / hysteria2 / tuic / shadowsocks，下拉选择
- **节点池** —— 多个节点按顺序自动故障转移（真实 SOCKS5 CONNECT 探测，不是端口监听）
- **指纹自检页** —— 打开浏览器实测 15 项，绿/红判定，直接告诉你伪装有没有生效
- **健康巡检** —— 检查 seed 重复、数据目录、chrome 路径

---

## 界面

```
┌──────────────────────────────────────────────────────────┐
│ KZ 多开管理器        🔍搜索        ☀  刷新  ＋新建实例    │
├──────────────────────────────────────────────────────────┤
│ ⋮⋮ │ 名称 │ 标签 │ 平台 │ 时区 │ 代理/节点 │ seed │ 状态 │
│ ⋮⋮ │ 主控1      windows Asia/Shanghai  直连   66367 ●运行 │
│ ⋮⋮ │ 日本       windows Asia/Tokyo     tuic…  468049 ●停止│
├──────────────────────────────────────────────────────────┤
│ ▶启动 ■停止 编辑 克隆 更多▾   拖动左侧竖点调整顺序        │
└──────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 1. 准备

| 需要 | 说明 |
|---|---|
| Windows 10/11 x64 | |
| 打过补丁的 Chromium | 指纹功能需要内核支持，见下方「内核补丁」 |
| Python 3.10+ | 快照/备份/节点等功能用 |
| .NET 8+ | 只有自己编译管理器时才需要 |

### 2. 部署

```
D:\KZProfiles\
├── KZManager.exe          ← 主程序
├── manager.json           ← 改 chromeExe 指向你的浏览器
├── node-proxy.py          ← 以下脚本必须和 EXE 放一起
├── snapshot.py
├── backup.py
├── sync-data.py
├── set-pref.py
├── 指纹自检.html
└── 启动管理器.bat
```

`manager.json`：

```json
{
  "chromeExe": "D:\\YourBuild\\chrome.exe",
  "defaultRows": 1,
  "theme": "dark"
}
```

### 3. 用

1. 双击 `KZManager.exe`
2. 点「＋ 新建实例」，填名称
3. 选平台/时区/语言，或点「地区预设」一键填（内置 12 个地区）
4. 保存 → 选中 → 点「▶ 启动」

---

## 内核补丁

指纹功能需要 Chromium 源码打补丁后重新编译。补丁涉及这些文件：

```
third_party/blink/renderer/core/frame/fingerprint_params.h       (新增)
third_party/blink/renderer/platform/fonts/fingerprint_font_filter.h (新增)
third_party/blink/renderer/core/frame/navigator_language.cc
third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc
third_party/blink/renderer/core/frame/navigator_device_memory.cc
third_party/blink/renderer/core/frame/navigator_id.cc
third_party/blink/renderer/core/frame/navigator_ua_data.cc
third_party/blink/renderer/core/geolocation/geolocation.cc
third_party/blink/renderer/core/css/font_face_set.cc
third_party/blink/renderer/platform/fonts/font_description.h
content/browser/renderer_host/render_process_host_impl.cc
chrome/browser/ui/views/bookmarks/bookmark_menu_delegate.cc
```

### 新增命令行参数

```
--fingerprint=<seed>              整套指纹的种子
--fingerprint-platform=macos      平台伪装
--fingerprint-lang=zh-CN,zh       语言（建议同时传 --accept-lang）
--fp-cores=8                       CPU 核数
--fp-memory=16                     内存 GB
--fp-screen=2560x1440              分辨率
--fp-dpr=2                         像素比
--fp-canvas=on|off                 Canvas 噪声
--fp-webgl=nvidia|amd|intel|off    WebGL 型号
--fp-audio=on|off                  音频噪声
--fp-fonts=on|off                  字体过滤
--fp-geo=35.6895,139.6917,30       经纬度+精度
--timezone=Asia/Tokyo              时区
--bookmark-bar-rows=1              书签栏行数
```

### 编译（参考）

```bash
is_official_build = true
symbol_level = 0
proprietary_codecs = true
ffmpeg_branding = "Chrome"
target_cpu = "x64"

ninja -C out/Default chrome -j 14
```

---

## 编译管理器

```powershell
cd app
dotnet publish -c Release -r win-x64 --self-contained true `
  -p:PublishSingleFile=true `
  -p:IncludeNativeLibrariesForSelfExtract=true `
  -p:EnableCompressionInSingleFile=true
```

产物：`app/bin/Release/net10.0-windows/win-x64/publish/KZManager.exe`（约 59 MB，自带运行时）

---

## 目录结构

```
.
├── app/                       WPF 管理器源码（C#）
│   ├── KZManager.csproj
│   ├── App.xaml               主题 / 控件样式
│   ├── MainWindow.xaml        主界面
│   ├── EditDialog.cs          实例编辑对话框
│   ├── Models.cs              Profile / NodeEntry / AppConfig
│   ├── Services.cs            路径、JSON、启动参数拼装
│   ├── ProcessQuery.cs        chrome 进程检测（PowerShell 桥）
│   ├── Theme.cs               深浅色配色
│   └── InputDialog.cs         单行输入框
├── node-proxy.py              节点链接 → 本地 SOCKS5（含节点库、故障转移）
├── snapshot.py                实例快照（硬链接去重）
├── backup.py                  书签备份 / 校验 / 还原
├── sync-data.py               实例间数据同步
├── set-pref.py                写 Chromium pref（含 HMAC 重算）
├── 指纹自检.html               浏览器内实测指纹的检查页
├── manager.ps1                旧版 PowerShell 管理器（回退用）
├── manager.example.json       配置样例
└── 项目说明.md                 详细使用文档
```

---

## 配置说明

`profile.json`（每个实例一份）：

| 字段 | 说明 |
|---|---|
| `name` | 显示名（也作为窗口标题） |
| `seed` | 指纹种子，数字 |
| `platform` | `macos` / `linux` / `windows` / `host` |
| `timezone` | IANA 时区，空 = 跟随系统 |
| `lang` | `zh-CN,zh;q=0.9`，空 = 跟随系统 |
| `proxy` | `http://127.0.0.1:7890`，空 = 直连 |
| `node` | 节点链接，多个用 `|` 分隔成节点池 |
| `rows` | 书签栏行数 1–15 |
| `fp_cores` 等 | 逐项覆盖，空 = 跟随 seed |
| `webrtc` | `public` / `strict` / `off` |
| `geo` | `lat,lon,精度` |
| `tags` | 标签，搜索用 |

---

## 常见问题

**Q：Google 登录报 `no_authorization_code=true`**

需要两个条件：
1. 启动参数带 `client_id=77185425430.apps.googleusercontent.com`
2. `--disable-features=DiceHeaderVersion2`

**Q：网站显示的语言和配置的不一样**

只传 `--fingerprint-lang` 只改 JS，网站读的是 `Accept-Language` 请求头。必须同时传 `--accept-lang`。管理器已经自动处理。

**Q：WebRTC 还能测出真实 IP**

`public` 只隐藏内网地址，公网 IP 照样暴露。要用代理防泄漏必须选 `strict`。

**Q：书签丢了**

Chromium 158 用 `EncryptedBookmarks2` 作为权威数据源，纯文本 `Bookmarks` 只是副本。恢复顺序必须是：写纯文本 → 删 `EncryptedBookmarks2*` → 让 Chrome 重新加密。`backup.py restore` 已经按这个顺序做，而且会在动磁盘前先校验备份里有 URL。

**Q：拖动排序拖不动**

先清空搜索框。过滤状态下拖动会导致顺序错乱，所以被禁用了。

---

## 免责声明

本项目仅供**个人多账号管理、自动化测试、前端兼容性验证**等技术用途。

使用者应自行确保：
- 遵守目标网站的服务条款
- 不用于绕过风控、批量注册、虚假流量等行为
- 所在地区的法律法规

作者不对使用者的行为承担任何责任。

---

## License

[MIT](LICENSE)
