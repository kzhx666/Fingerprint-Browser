# KZ 指纹浏览器

Windows 平台上的 **Chromium 多开 + 指纹隔离** 方案，由两部分组成：

| 组成 | 作用 |
|---|---|
| **WPF 管理器**（`app/`） | 管理几十个互相隔离的浏览器实例：指纹、代理、时区、语言、克隆、快照、数据同步 |
| **定制 Chromium 内核**（`kernel/`） | 在 C++ 层直接改写浏览器报告的硬件与渲染参数，而不是在 JS 层做 hook |

> **两者必须配套使用。**
> 只部署管理器、不部署打过补丁的内核，指纹功能不会生效——管理器发出的参数会被原版内核忽略。
> 内核需要自行编译，或使用发布包中已编译好的 `App/` 目录。

完整上手流程见 [`docs/使用指南.md`](docs/使用指南.md)，设计与实现细节见 [`docs/项目说明.md`](docs/项目说明.md)。

---

## 目录

- [工作原理](#工作原理)
- [功能概览](#功能概览)
- [快速开始](#快速开始)
- [内核补丁](#内核补丁)
- [编译](#编译)
- [目录结构](#目录结构)
- [配置说明](#配置说明)
- [命令行工具](#命令行工具)
- [常见问题](#常见问题)

---

## 工作原理

多开浏览器不难，难的是**让每个实例看起来像一台不同的机器**，而且这台"机器"的所有参数彼此自洽。

本项目的做法分三层：

**1. 实例隔离**
每个实例一个独立 `--user-data-dir`，Cookie、缓存、扩展、书签完全独立，进程之间不共享任何状态。

**2. 指纹由种子推导**
每个实例只有一个 `seed` 整数。CPU 核数、内存、屏幕分辨率、像素比、Canvas 噪声、WebGL 显卡型号、Audio 噪声、字体列表、时区、语言、地理位置全部由这一个数字确定性地推导出来。

```
同 seed → 同一台机器
换 seed → 换一台新机器
```

因此克隆一个实例并换 seed，得到的是一台"同型号但不同个体"的机器；而同一 seed 在任何时候打开，指纹都完全一致——这对需要长期保持同一身份的账号是必要条件。

**3. 内核层生效**
上述参数由内核在 C++ 层直接改写返回值：

| 参数 | 改写位置 |
|---|---|
| `navigator.platform` / UA / UA-CH | 三处必须一致，否则会被交叉比对识破 |
| `Intl.DateTimeFormat().resolvedOptions().timeZone` | ICU 层 |
| `navigator.language` + `Accept-Language` 请求头 | 渲染层 + 网络层同时改 |
| CPU 核数 / 内存 / 屏幕 / 像素比 | 硬件查询接口 |
| Canvas / WebGL / Audio | 读取时注入每实例独立噪声 |
| 字体列表 | 按伪装平台过滤（macOS 110 / Windows 136 / Linux 87 种） |
| Geolocation | 直接接管，返回设定经纬度 |
| WebRTC | 四档：直连 / 替换为代理 IP / 指定 IP / 完全禁用 |

---

## 功能概览

### 实例管理

- **多实例隔离** —— 独立 `user-data-dir`，互不影响
- **拖动排序** —— 行首把手拖动，实时显示插入位置
- **克隆** —— 仅复制配置（自动换新 seed），或连数据一起复制
- **快照** —— 硬链接去重，6441 个文件只占 6.3 MB
- **数据同步** —— 书签 / 历史 / 密码 / 扩展从 A 同步到 B，自动备份
- **批量操作** —— 多选启动、批量打包、批量清理缓存
- **实时搜索** —— 按名称、标签、平台、时区、seed 过滤

### 指纹与一致性

- **一键生成指纹** —— 自动组合出彼此相容的软硬件方案（CPU / GPU / 内存 / 分辨率 / DPR）
- **一致性矩阵** —— 锁定 `UA ↔ UA-CH ↔ 平台 ↔ 渲染管线 ↔ 屏幕 ↔ 字体 ↔ 硬件`，避免"Mac 报 Intel 核显"这类矛盾
- **真机画像库** —— 内置 Windows 11/10、macOS（Apple Silicon / Intel）、Linux 桌面采集样本
- **冲突诊断** —— 检测语言与出口国家不符、时区与网络延迟矛盾等，给出风险扣分
- **可视化回归** —— 内置真实浏览器探针，跨 3 个随机 seed 回归 16 项单机指标 + 5 项跨实例独立性

### 网络与代理

- **代理管理** —— 池化管理、分组、批量导入（`host:port:user:pass`）、并发测速、自动分配
- **地域联动** —— 配置代理后一键提取出口 IP，自动匹配时区、经纬度、推荐语言
- **断线熔断** —— 代理异常时内核级阻断全部流量（DNS / WebRTC / IPv6 / QUIC），禁止回退直连
- **节点库** —— 订阅链接批量导入 vless / hysteria2 / tuic / shadowsocks
- **节点池** —— 多节点自动故障转移，基于真实 SOCKS5 CONNECT 探测
- **欺诈度预检** —— 区分机房 IP / 住宅 IP，高风险代理标红

### 账号与数据

- **账号资料** —— 实例可绑定多个平台账号，记录用户名、备忘、2FA 密钥，启动时直达工作页
- **Cookie 管理** —— Netscape / JSON 格式导入导出、去重、合并，启动前写入实例数据库
- **扩展管理** —— 本地 `_extensions` 目录批量预装
- **彩色标签** —— 自定义标签（养号期 / 已实名 / 主账号…），支持交集并集筛选
- **健康总览** —— 代理状态、指纹健康分、Cookie 有效期、磁盘占用一览
- **细粒度清理** —— 只清缓存 / IndexedDB / ServiceWorker，保留 Cookie、书签、密码

### 浏览器界面增强

- **右侧垂直侧边栏** —— 书签、阅读清单、历史、下载，以及**自定义站点图标**
- **自定义站点** —— 点 `+` 自动抓取当前网页的标题与图标加入侧边栏；右键可打开 / 编辑 / 移除
- **默认浏览器路由** —— 外部链接交给"当前前台实例"打开，无需切换窗口
- **深浅色主题** —— 即时切换

---

## 快速开始

### 1. 准备

- Windows 10 / 11 x64
- 发布包：`KZBrowser-Portable-*.zip`（含内核 + 管理器 + 工具）
- 自行编译需要：Visual Studio 2022、depot_tools、.NET 10 SDK、Python 3.11+

### 2. 部署

解压发布包到任意目录（路径不要含空格）：

```
KZBrowser/
├── App/               定制 Chromium 内核
├── KZManager.exe      管理器
├── node-proxy.py      节点工具
└── ...
```

### 3. 启动

双击 `KZManager.exe`（或 `启动管理器.bat`）。

首次启动会在同目录生成 `manager.json` 与 `_proxies`、`_snapshots`、`_backup` 等目录。

### 4. 建实例

1. 点「新建」，填写名称、平台、时区、语言
2. 点「生成」自动产出相容的指纹方案
3. 需要代理时在「代理」页填入节点
4. 双击实例启动

### 5. 验证指纹

实例启动后，从管理器点「指纹自检」，会在浏览器内打开自检页并逐项判定：

```
navigator.platform      macOS ✅
UA / UA-CH 一致性        ✅
时区                    America/New_York ✅
Canvas 噪声              已注入 ✅
WebGL 渲染器            Apple M2 ✅
WebRTC 私网泄漏          无 ✅
```

全部为绿色即表示伪装生效。

---

## 内核补丁

### 一键打补丁

```bash
# 应用全部补丁（幂等，重复执行安全）
python kernel/apply_patches.py C:\chromium\src

# 只检查，不写入
python kernel/apply_patches.py C:\chromium\src --check

# 补丁自测（首次应用 + 幂等 + 锚点校验）
python kernel/test_patches.py
```

当前共 **93 项补丁**，覆盖指纹、代理、侧边栏、便携密钥、界面行为等模块。

### 补丁设计

- **锚点式**：每个补丁按源码中的唯一代码片段定位，不依赖行号
- **幂等**：已打过的补丁报告 `ALREADY`，不会重复插入
- **可校验**：`--check` 在生产源码树上零写入，只做一致性比对
- **资源化**：侧边栏等大段实现存放在 `kernel/sidebar/*.txt`，按锚点原样注入，避免补丁脚本与真实源码脱节

### 验收门禁

发布前必须全部通过（定义见 `kernel/CHROMIUM_VERSION`）：

```bash
python kernel/test_patches.py                      # 补丁全绿
python tests/test_data_integrity.py                # 数据工具全绿
python kernel/apply_patches.py C:\chromium\src --check   # 生产树零漂移
python kernel/run_browser_regression.py            # 真实浏览器回归
```

---

## 编译

### 管理器

```bash
dotnet restore app/KZManager.csproj

dotnet publish app/KZManager.csproj \
  -c Release -r win-x64 --self-contained true \
  -p:PublishSingleFile=true \
  -p:IncludeNativeLibrariesForSelfExtract=true \
  -p:EnableCompressionInSingleFile=true \
  -p:DebugType=none \
  -o out
```

产物 `out/KZManager.exe`，约 60 MB（单文件自包含，目标机无需装 .NET）。

### 内核

```bash
# 1. 拉取对应版本源码
#    158.0.8127.18 / 6449d9f9a6a906edff724b7b220199b3fde4615c

# 2. 打补丁
python kernel/apply_patches.py C:\chromium\src

# 3. 生成构建配置
gn gen out/Default --args="is_official_build=true symbol_level=0 proprietary_codecs=true ffmpeg_branding=Chrome is_component_build=false target_cpu=x64"

# 4. 编译
ninja -C out/Default chrome.dll chrome.exe
```

全量编译约 3 小时（约 51000 个构建步骤）；只改界面层时增量编译约 2–6 分钟。

产物校验：

```bash
python kernel/verify_build.py --dll out/Default/chrome.dll
```

---

## 目录结构

```
.
├── app/                       WPF 管理器源码（C#）
│   ├── KZManager.csproj
│   ├── App.xaml               主题与控件样式
│   ├── MainWindow.xaml        主界面
│   ├── EditDialog.cs          实例编辑（指纹、代理、账号）
│   ├── SmartFingerprint.cs    指纹方案生成
│   ├── FingerprintDiagnostic.cs  一致性诊断
│   ├── ProxyManager.cs        代理池
│   ├── ProxyPrecheck.cs       出口 IP 预检
│   ├── Launcher.cs            启动参数拼装
│   ├── Services.cs            路径 / JSON / 命令行
│   ├── DefaultBrowser.cs      默认浏览器路由注册
│   └── SmartDefault.cs        前台实例识别与链接转发
│
├── kernel/                    Chromium 内核补丁
│   ├── apply_patches.py       93 项补丁，锚点式 + 幂等
│   ├── sidebar/               侧边栏实现（资源文件，按锚点注入）
│   ├── patches_v11.py         代理认证补丁
│   ├── patches_v12.py         WebRTC 补丁
│   ├── verify_build.py        编译产物校验
│   ├── test_patches.py        补丁自测
│   ├── run_browser_regression.py  真实浏览器回归
│   └── CHROMIUM_VERSION       版本基准与验收门禁
│
├── scripts/                   命令行工具
│   ├── node-proxy.py          节点链接 → 本地 SOCKS5（节点库、故障转移）
│   ├── snapshot.py            实例快照（硬链接去重）
│   ├── backup.py              书签 / 配置备份、校验、还原
│   ├── sync-data.py           实例间数据同步
│   ├── sync-menu.py           同步菜单（TUI）
│   ├── autosync.py            定时同步
│   ├── account-sync.py        账号同步
│   ├── sync-server.py         同步服务端
│   ├── set-pref.py            写 Chromium pref（含 HMAC 重算）
│   ├── make_portable_zip.py   便携版打包
│   ├── manager.ps1            旧版 PowerShell 管理器（回退用）
│   └── 账号同步.ps1
│
├── tools/                     开发与运维工具
│   ├── 指纹自检.html            浏览器内实测指纹
│   ├── register-browser.ps1   注册为默认浏览器
│   ├── export-migration.ps1   环境导出
│   ├── import-migration.ps1   环境导入
│   └── patch-icon.py          图标批量处理
│
├── tests/                     测试
│   ├── test_data_integrity.py 克隆 / 快照 / 备份 / 还原 / 同步
│   ├── probe_full.html        回归探针页
│   └── fixtures/              补丁测试用源码片段
│
├── assets/                    图标与资源
├── examples/                  配置样例（OAuth、manager.json）
├── docs/                      详细文档
│   ├── 使用指南.md
│   └── 项目说明.md
│
├── 启动管理器.bat
├── 同步数据.bat
└── 账号同步.bat
```

---

## 配置说明

### `manager.json`（全局）

| 字段 | 说明 |
|---|---|
| `chromeExe` | 内核路径，便携版用相对路径 `App\chrome.exe` |
| `order` | 实例排序 |
| `theme` | `dark` / `light` |
| `proxyPrecheck` | 启动前做出口 IP 预检 |
| `regionCheck` | 出口国家与实例声明地区比对 |
| `regionCheckBlock` | 不符时拦截启动 |
| `nativeProxyAuth` | 使用内核原生 SOCKS5 账密认证 |
| `uaProfile` | UA 画像：`default` / `chrome-157` / `iOS-safari` / `Android-chrome` |
| `defaultInstance` | 外部链接的兜底实例（前台无浏览器时） |

### `profile.json`（每实例一份）

| 字段 | 说明 |
|---|---|
| `name` | 显示名，同时作为窗口标题 |
| `seed` | 指纹种子，整数 |
| `platform` | `macos` / `linux` / `windows` / `host` |
| `timezone` | IANA 时区，空 = 跟随系统 |
| `lang` | 语言，如 `en-US` |
| `webrtc` | `public` / `strict` / `off` / 自定义 IP |
| `proxy` | 代理地址 |
| `tags` | 彩色标签 |
| `accounts` | 绑定的平台账号列表 |

---

## 命令行工具

```bash
# 节点
python scripts/node-proxy.py start --node "vless://..." --port 10801

# 快照
python scripts/snapshot.py create --from 实例A --name 基线模板
python scripts/snapshot.py restore --name 基线模板 --to 实例B --new-seed

# 备份
python scripts/backup.py save --from 实例A
python scripts/backup.py restore --from 20261109-165000 --to 实例A --what bookmarks

# 数据同步
python scripts/sync-data.py --from A --to B,C --what all

# 写 pref
python scripts/set-pref.py --profile "<实例>\Data\Default" --set webrtc.ip_handling_policy disable_non_proxied_udp
```

---

## 常见问题

**Q：实例启动后指纹没变化？**
内核没生效。确认 `manager.json` 的 `chromeExe` 指向的是打过补丁的 `chrome.exe`，不是原版 Chrome。

**Q：登录状态会掉？**
先确认内核版本与实例数据匹配。若曾用未打补丁的内核打开过实例，密钥可能已被重置。建议每个实例的数据目录定期用 `snapshot.py` 快照。

**Q：WebRTC 仍然泄漏内网 IP？**
在实例设置里把 WebRTC 切到 `strict` 或 `off`，重启实例。自检页会直接显示泄漏情况。

**Q：编译内核时补丁报 `anchor not found`？**
源码版本不匹配。核对 `kernel/CHROMIUM_VERSION` 里的 commit hash，必须完全一致。

**Q：管理器能连多少个实例？**
实测 50+ 实例同时运行无压力，主要受内存限制（每个实例约 200–400 MB）。

---

## 免责声明

本项目用于**自有账号的批量管理与环境隔离**、自动化测试、指纹识别研究与反欺诈对抗演练。

使用者应自行确保其使用场景符合所在地区的法律法规以及目标网站的服务条款。作者不对因使用本项目产生的任何后果承担责任。

---

## License

[MIT](LICENSE)
