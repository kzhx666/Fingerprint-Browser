# KZ 多开管理器

一个 Windows 下的 **Chromium 多开管理器**：用一个面板管理几十个互相隔离的浏览器实例，每个实例有独立的指纹、代理、时区、语言，并且可以克隆、快照、同步数据。

配套一份 Chromium 内核补丁，让浏览器真正报告伪造的硬件参数（不是 JS 层 hook）。

> ⚠️ **重要**：本项目分两半——**管理器**和**打过补丁的 Chromium 内核**。
> 只下载管理器，指纹功能不会生效。内核需要自己编译或向作者索取。
>
> 完整上手流程见 **[使用指南.md](使用指南.md)**。

> 本项目由 [kernel/apply_patches.py](kernel/apply_patches.py) 维护的内核补丁 + WPF 管理器 + 一组 Python 工具组成。

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


## 商业级指纹浏览器全景功能体系 (28 项升级)

本项目现已全面达到并超越二线商业指纹浏览器水平，构建了完整的软硬件指纹一致性矩阵、代理全生命周期管控、工作室量产多开工具链与纯本地安全体系：

### 1. 核心指纹仿真与一致性校验 (对抗底层风控)
- **智能指纹生成器 (`SmartFingerprint`)**：一键生成全套相容软硬件方案（CPU、GPU、内存、分辨率、DPR），支持锁定核心参数只重置次要指纹，支持生成前预览。
- **跨维度指纹一致性矩阵**：严格锁定 `UA ↔ UA-CH ↔ 平台 ↔ 渲染管线 ↔ 屏幕参数 ↔ 字体 ↔ 硬件参数` 逻辑闭环，杜绝“Mac 报 Intel 核显”、“DirectX 驱动在 macOS”等致命破绽。
- **多系统真实身份库**：内置经过真机采集验证的 Windows 11 / 10、macOS (Apple Silicon M系列 / Intel) 以及 Linux 桌面级设备画像。
- **浏览器版本严格对齐**：声明版本号与内核能力、特有 API 开关、Client Hints 高低位版本严格对齐。
- **WebRTC 多模式控制**：提供 **直连 (Direct)**、**代理匹配 (Replace/Proxy)**、**自定义指定公网 IP** 以及 **彻底禁用 (Disabled)** 四档平滑切换。
- **指纹冲突精准诊断器 (`FingerprintDiagnostic`)**：多维逻辑矛盾诊断（如：语言与出口国家不符、时区与网络延迟地区矛盾、平台与显卡驱动特征矛盾），直接给出风控风险扣分与排查建议。
- **真实浏览器可视化回归大盘**：内置真实浏览器探针 (`kernel/run_browser_regression.py`)，跨 3 个随机 Seed 全面回归 16 项单机指标与 5 项跨实例独立性。

### 2. 代理网络与安全防线 (防关联核心)
- **地域与网络自动联动 (`AutoAlignRegion`)**：配置代理后，一键提取实际出口 IP，自动匹配并联动修改时区、经纬度、推荐语言及实例 DNS。
- **网络断线熔断器 (Kill Switch)**：统一管控 DNS、WebRTC、IPv6、UDP/QUIC。当代理断线或节点故障时，**内核级彻底阻断全部网络流量，严禁回退直连裸奔**。
- **代理管理中心 (`ProxyManager`)**：代理池管理、分组归类、批量导入 (`host:port:user:pass`)、并发延迟测速、去重与自动均衡分配。
- **代理 IP 欺诈度预检**：识别机房 IP (DataCenter)、住宅 IP (ISP) 与威胁情报风控分值，高危代理标红报警。

### 3. 账号资产与环境工作台 (对标 AdsPower 业务层)
- **账号资料与启动工作台 (`AccountEntry`)**：实例可绑定多个平台账号（Amazon / Google / Facebook / TikTok / 独立站等），记录用户名、备忘与 2FA 密钥；启动时自动以对应账号直达目标工作页。
- **Cookie 管理中心 (`CookieManager`)**：支持 Netscape / JSON 格式 Cookie 的一键导入、导出、批量去重、合并；启动前直接写入实例持久化数据库。
- **扩展插件管理中心 (`ExtensionManager`)**：管理本地 `_extensions` 目录下的扩展包，可在编辑环境时一键批量勾选预装。
- **多维度彩色标签系统**：支持自定义红/黄/蓝/绿彩色标签（如：`养号期`、`已实名`、`主账号`），主界面支持多标签交集/并集快速筛选。
- **环境健康总览面板**：直观展示所有实例的代理状态、指纹健康分、Cookie 有效期与磁盘占用，快速定位异常实例。
- **环境存储细粒度清理 (`StorageCleaner`)**：统一管理各实例的缓存、IndexedDB、ServiceWorker、临时文件与下载历史，严格保留 Cookies、书签与密码。

### 4. 多开与量产效率工具 (工作室核心生产力)
- **多窗口操作同步器 (`ActionSynchronizer`)**：选择一个主控窗口，将鼠标点击、移动、滚轮滚动、键盘打字以及标签页开关**实时同步镜像到指定的其他窗口**，支持暂停与单窗临时排除。
- **拟人化按键输入器 (`HumanTyping`)**：将粘贴行为转换为具备 **50~150ms 随机正态分布延迟** 的物理键盘按键敲击，彻底绕过针对突变文本输入的 Bot 检测。
- **多屏网格窗口平铺器 (`WindowTiler`)**：多开 4~16 个窗口时，根据当前屏幕或扩展屏分辨率，一键按 2x2、3x3、4x2 规则无缝平铺排列并对齐边框。
- **自定义 JS 启动隐蔽注入器 (`UserscriptManager`)**：类似无需安装扩展的隐蔽油猴，管理自定义 JS 脚本，在页面加载前静默注入（用于自动化辅助、特定页面篡改或反爬对抗）。
- **批量环境操作引擎 (`BatchEngine`)**：支持数十/数百个实例的批量新建、批量修改配置、批量绑定代理、批量启动与平稳调度。

### 5. 自动化与开发者生态 (支持无头控制)
- **完整本地自动化 API**：全功能本地 REST API（覆盖环境启停、配置修改、代理切换、Cookie 提取、批量任务全生命周期）。
- **自动化框架无缝连接器 (`FrameworkConnector`)**：一键生成 **Playwright**、**Puppeteer**、**Selenium** 的直连代码片段，提供现成连接端点与参数。
- **简易可视化自动化 (RPA 基础 - `SimpleRpa`)**：支持常见操作录制、步骤编辑、定时批量回放与失败重试。

### 6. 本地安全、灾备与协作
- **本地数据保险箱 (`SecurityVault`)**：引入主密码保护机制，采用 **AES-GCM-256 + PBKDF2 (100,000轮哈希)** 对磁盘上的代理账密、账号资料进行高强度加密。
- **实例配置与数据备份/回滚**：备份创建前结构预览、进度展示、校验核对，以及还原失败时的原子自动回滚。
- **本地团队工作区与操作审计**：支持多工作区切换、多操作员标记、权限配置与详细的本地 JSONL 审计日志。

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

> 详细步骤见 **[使用指南.md](使用指南.md)**，这里只列最短路径。

### 1. 准备

| 需要 | 说明 |
|---|---|
| Windows 10/11 x64 | |
| **打过补丁的 Chromium** | **必需**，指纹功能靠它，见下方「内核补丁」 |
| Python 3.10+ | 快照/备份/节点等功能用 |
| .NET 10+ | 只有自己编译管理器时才需要 |

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

### 一键打补丁

```powershell
python kernel\apply_patches.py --src C:\chromium\src
```

输出 31 行 `APPLIED` / `ALREADY`，有 `FAILED` 就停下检查版本。

只预览不改磁盘：

```powershell
python kernel\apply_patches.py --src C:\chromium\src --check
```

编译完验证：

```powershell
python kernel\verify_build.py --build C:\chromium\src\out\Default
```

### 补丁覆盖

| 分类 | 补丁 |
|---|---|
| 指纹 | 平台 / 时区 / 语言 / CPU 核数 / 内存 / 分辨率 / 像素比 / 电池 |
| 图形 | Canvas（getImageData + toDataURL）/ WebGL / Audio |
| 反检测 | webdriver / 自动化提示条 / 字体过滤 |
| 隐私 | 地理位置 / WebRTC |
| UI | 多行书签栏 / 书签新标签打开 / 双击关标签 / 滚轮切标签 / 保留最后标签 / 窄标签 / 书签菜单多列 |

### 涉及文件

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
├── kernel/                    Chromium 内核补丁脚本
│   ├── apply_patches.py       91 个定制补丁，一键打上 (幂等校验)
│   ├── verify_build.py        编译产物校验
│   └── test_patches.py        补丁自测
├── 使用指南.md                 给新手的完整流程
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
1. 启动参数带 `client_id=YOUR_CLIENT_ID.apps.googleusercontent.com`
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
