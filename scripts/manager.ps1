<#
多开管理器 (KZ Profile Manager) v1
====================================
用途：一个面板管理所有多开浏览器实例，每个实例独立 data_dir / 指纹 / 代理 / 时区 / 语言。

目录结构：
  D:\KZProfiles\
    manager.json            <- 管理器自身配置（chrome.exe 路径等）
    客服A\
      profile.json          <- 该实例的配置
      Data\                 <- 浏览器数据目录（自动创建）
    内网B\
      profile.json
      Data\

profile.json 字段：
  name     显示名（同时作为窗口标题后缀）
  seed     --fingerprint 种子（数字，决定整套假硬件）
  platform macos / linux / windows / host(不伪装)
  timezone IANA 时区，如 America/New_York；空=跟随系统
  lang     如 en-US,en；空=跟随系统（需要内核支持 --fingerprint-lang）
  proxy    如 http://127.0.0.1:7890 或 socks5://127.0.0.1:1080；空=直连
  rows     书签栏行数 1..15
  extra    额外命令行参数
  enabled  是否在列表里显示

启动参数拼装：
  chrome.exe --user-data-dir=<profile>\Data --fingerprint=<seed>
             --fingerprint-platform=<platform> --timezone=<tz>
             --fingerprint-lang=<lang> --proxy-server=<proxy>
             --bookmark-bar-rows=<rows> --no-first-run --no-default-browser-check <extra>
#>

param(
    [switch]$SelfTest,          # 不启动界面，打印样例命令后退出
    [string]$Root = "D:\KZProfiles"
)

# 让 python 子进程输出 UTF-8，并让 PowerShell 按 UTF-8 解码，否则中文全乱码
$env:PYTHONIOENCODING = 'utf-8'
try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }
try { $OutputEncoding = [Text.UTF8Encoding]::new($false) } catch { }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName Microsoft.VisualBasic
Add-Type -AssemblyName System.Drawing

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- 配置

$script:Root = $Root
$script:CfgPath = Join-Path $Root 'manager.json'
$script:DefaultChrome = 'D:\KZBuild\App\chrome.exe'

function Get-Config {
    if (Test-Path $script:CfgPath) {
        try { return Get-Content $script:CfgPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
    }
    return [pscustomobject]@{ chromeExe = $script:DefaultChrome; defaultRows = 3 }
}

function Save-Config($cfg) {
    $cfg | ConvertTo-Json -Depth 5 | Set-Content $script:CfgPath -Encoding UTF8
}

function Get-ProfilePaths {
    if (-not (Test-Path $script:Root)) { New-Item -ItemType Directory -Force $script:Root | Out-Null }
    Get-ChildItem $script:Root -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'profile.json') }
}

function Get-Profile($dir) {
    $p = Join-Path $dir.FullName 'profile.json'
    try { $o = Get-Content $p -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $o = $null }
    if (-not $o) { $o = [pscustomobject]@{} }
    # 补齐缺省字段
    foreach ($f in 'name','seed','platform','timezone','lang','proxy','rows','extra','enabled') {
        if (-not ($o.PSObject.Properties.Name -contains $f)) {
            $v = switch ($f) {
                'name'     { $dir.Name }
                'seed'     { 1001 }
                'platform' { 'host' }
                'timezone' { '' }
                'lang'     { '' }
                'proxy'    { '' }
                'rows'     { 3 }
                'extra'    { '' }
                'enabled'  { $true }
            }
            $o | Add-Member -NotePropertyName $f -NotePropertyValue $v -Force
        }
    }
    return $o
}

function Save-Profile($dir, $obj) {
    # 不写 BOM：Python 工具的 json.load 会被 BOM 卡住
    $json = $obj | ConvertTo-Json -Depth 5
    [IO.File]::WriteAllText((Join-Path $dir.FullName 'profile.json'), $json,
                             [Text.UTF8Encoding]::new($false))
}

# ---------------------------------------------------------------- 命令拼装

function Set-GoogleOAuthEnv {
    # 注入谷歌登录/同步所需的三项凭据（环境变量覆盖，无需重编译）
    $cfg = Join-Path $script:Root 'google-oauth.json'
    if (-not (Test-Path $cfg)) { return }
    try {
        $g = Get-Content $cfg -Raw | ConvertFrom-Json
    } catch { return }
    if ($g.enabled -eq $false) { return }
    if ($g.api_key) { $env:GOOGLE_API_KEY = $g.api_key }
    if ($g.client_id) { $env:GOOGLE_DEFAULT_CLIENT_ID = $g.client_id }
    if ($g.client_secret) { $env:GOOGLE_DEFAULT_CLIENT_SECRET = $g.client_secret }
}

function Build-Args($dir, $p) {
    $dataDir = Join-Path $dir.FullName 'Data'
    $a = New-Object System.Collections.Generic.List[string]
    $a.Add("--user-data-dir=`"$dataDir`"")
    if ($p.name) { $a.Add('--profile-name="' + $p.name + '"') }
    if ("$($p.seed)" -match '^\d+$') { $a.Add("--fingerprint=$($p.seed)") }
    if ($p.platform -and $p.platform -ne 'host') { $a.Add("--fingerprint-platform=$($p.platform)") }
    if ($p.timezone)  { $a.Add("--timezone=$($p.timezone)") }
    if ($p.lang) {
        # 剥掉 ;q=0.9 这类权重，Chromium 的 --accept-lang 会自己重新生成
        $plain = (@($p.lang -split ',') |
                  ForEach-Object { ($_ -split ';')[0].Trim() } |
                  Where-Object { $_ }) -join ','
        if ($plain) {
            # --accept-lang 是原生开关：同时改 HTTP 请求头 + navigator.languages。
            # 只传 --fingerprint-lang 的话，网站读请求头还是看到系统语言。
            $a.Add("--accept-lang=$plain")
            $a.Add("--fingerprint-lang=$plain")
        }
    }
    if ($p.proxy)     { $a.Add("--proxy-server=$($p.proxy)") }
    if ("$($p.rows)" -match '^\d+$') { $a.Add("--bookmark-bar-rows=$($p.rows)") }
    # 指纹逐项覆盖（留空则跟随 seed）
    if ("$($p.fp_cores)"  -match '^\d+$')       { $a.Add("--fp-cores=$($p.fp_cores)") }
    if ("$($p.fp_memory)" -match '^\d+$')       { $a.Add("--fp-memory=$($p.fp_memory)") }
    if ("$($p.fp_screen)" -match '^\d+x\d+$')   { $a.Add("--fp-screen=$($p.fp_screen)") }
    if ("$($p.fp_dpr)"    -match '^[\d.]+$')     { $a.Add("--fp-dpr=$($p.fp_dpr)") }
    if ($p.fp_canvas)                            { $a.Add("--fp-canvas=$($p.fp_canvas)") }
    if ($p.fp_webgl)                             { $a.Add("--fp-webgl=$($p.fp_webgl)") }
    if ($p.fp_audio)                             { $a.Add("--fp-audio=$($p.fp_audio)") }
    if ($p.fp_fonts -and $p.fp_fonts -ne 'on')   { $a.Add("--fp-fonts=$($p.fp_fonts)") }
    if ($p.geo) { $a.Add("--fp-geo=$($p.geo)") }
    # Google 只对 DiceHeaderVersion2 关闭（v1 协议）的请求发放授权码
    $a.Add('--disable-features=DiceHeaderVersion2')
    $a.Add('--no-first-run')
    $a.Add('--no-default-browser-check')
    if ($p.extra) { $a.Add($p.extra) }
    return ($a -join ' ')
}

# ---------------------------------------------------------------- 运行状态

function Get-RunningMap {
    $map = @{}
    try {
        Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -and $_.CommandLine -notmatch '--type=' } |
            ForEach-Object {
                if ($_.CommandLine -match '--user-data-dir="?([^"]+)"?') {
                    $map[$matches[1].TrimEnd('\')] = $_.ProcessId
                }
            }
    } catch { }
    return $map
}

# ---------------------------------------------------------------- 编辑对话框

# ---------------------------------------------------------------- 地区预设
# 一个地区的时区 / 语言 / 平台 / 分辨率 / 坐标要互相自洽，否则指纹自检会露馅。
$script:RegionPresets = [ordered]@{
    '美国-纽约'     = @{ tz='America/New_York';    lang='en-US,en;q=0.9'; plat='macos';   scr='2560x1440'; dpr='2'; geo='40.7128,-74.0060,30' }
    '美国-洛杉矶'   = @{ tz='America/Los_Angeles'; lang='en-US,en;q=0.9'; plat='macos';   scr='1920x1080'; dpr='2'; geo='34.0522,-118.2437,30' }
    '日本-东京'     = @{ tz='Asia/Tokyo';          lang='ja-JP,ja;q=0.9'; plat='macos';   scr='2560x1440'; dpr='2'; geo='35.6895,139.6917,30' }
    '韩国-首尔'     = @{ tz='Asia/Seoul';          lang='ko-KR,ko;q=0.9'; plat='windows'; scr='1920x1080'; dpr='1'; geo='37.5665,126.9780,30' }
    '新加坡'        = @{ tz='Asia/Singapore';      lang='en-US,en;q=0.9'; plat='windows'; scr='1920x1080'; dpr='1'; geo='1.3521,103.8198,30' }
    '香港'          = @{ tz='Asia/Hong_Kong';      lang='zh-HK,zh;q=0.9'; plat='windows'; scr='1920x1080'; dpr='1'; geo='22.3193,114.1694,30' }
    '台湾'          = @{ tz='Asia/Taipei';         lang='zh-TW,zh;q=0.9'; plat='windows'; scr='1920x1080'; dpr='1'; geo='25.0330,121.5654,30' }
    '德国-法兰克福' = @{ tz='Europe/Berlin';       lang='de-DE,de;q=0.9'; plat='windows'; scr='1920x1080'; dpr='1'; geo='50.1109,8.6821,30' }
    '英国-伦敦'     = @{ tz='Europe/London';       lang='en-GB,en;q=0.9'; plat='macos';   scr='2560x1600'; dpr='2'; geo='51.5074,-0.1278,30' }
    '法国-巴黎'     = @{ tz='Europe/Paris';        lang='fr-FR,fr;q=0.9'; plat='windows'; scr='1920x1080'; dpr='1'; geo='48.8566,2.3522,30' }
    '加拿大-多伦多' = @{ tz='America/Toronto';     lang='en-CA,en;q=0.9'; plat='macos';   scr='1920x1080'; dpr='2'; geo='43.6532,-79.3832,30' }
    '澳洲-悉尼'     = @{ tz='Australia/Sydney';    lang='en-AU,en;q=0.9'; plat='macos';   scr='2560x1440'; dpr='2'; geo='-33.8688,151.2093,30' }
}

function Show-EditDialog($dir, $p, [bool]$isNew) {
    $f = New-Object System.Windows.Forms.Form
    $f.Text = if ($isNew) { '新建实例' } else { "编辑实例 - $($dir.Name)" }
    $f.Size = New-Object System.Drawing.Size(790, 570)
    $f.StartPosition = 'CenterParent'
    $f.FormBorderStyle = 'FixedDialog'
    $f.MaximizeBox = $false

    # 左栏：基础配置
    $rows = @(
        @{ k='name';     l='名称（任务栏标识）'; t='text' },
        @{ k='seed';     l='指纹种子 seed';      t='text' },
        @{ k='platform'; l='平台伪装';           t='combo' },
        @{ k='timezone'; l='时区 (IANA)';        t='combo' },
        @{ k='lang';     l='语言 (Accept-Lang)'; t='combo'; items=@('','zh-CN,zh;q=0.9','zh-TW,zh;q=0.9','en-US,en;q=0.9','ja-JP,ja;q=0.9','ko-KR,ko;q=0.9','de-DE,de;q=0.9','fr-FR,fr;q=0.9','es-ES,es;q=0.9','ru-RU,ru;q=0.9','pt-BR,pt;q=0.9','it-IT,it;q=0.9','ar-SA,ar;q=0.9') },
        @{ k='proxy';    l='代理';               t='text' },
        @{ k='rows';     l='书签栏行数 1-15';    t='text' },
        @{ k='node';     l='节点/节点池(|分隔)'; t='text' },
        @{ k='geo';      l='坐标 lat,lon,精度';  t='text' },
        @{ k='tags';     l='标签（搜索用）';     t='text' },
        @{ k='extra';    l='额外参数';           t='text' }
    )
    # 右栏：指纹逐项覆盖（留空 = 跟随 seed 推导）
    $fpRows = @(
        @{ k='fp_cores';  l='CPU 核数';      t='combo'; items=@('','4','8','12','16','24','32') },
        @{ k='fp_memory'; l='内存 GB';       t='combo'; items=@('','2','4','8','16','32') },
        @{ k='fp_screen'; l='分辨率 WxH';    t='combo'; items=@('','1920x1080','2560x1440','1600x900','1366x768','3840x2160','1280x720') },
        @{ k='fp_dpr';    l='像素比 DPR';    t='combo'; items=@('','1','1.25','1.5','2','3') },
        @{ k='fp_canvas'; l='Canvas 噪声';   t='combo'; items=@('','on','off') },
        @{ k='fp_webgl';  l='WebGL 型号';    t='combo'; items=@('','nvidia','amd','intel','off') },
        @{ k='fp_audio';  l='音频噪声';      t='combo'; items=@('','on','off') },
        @{ k='fp_fonts';  l='字体伪装';      t='combo'; items=@('','on','off') },
        @{ k='webrtc';    l='WebRTC IP 防泄漏'; t='combo'; items=@('','public','strict','off') }
    )
    # 节点库：下拉框里显示可读名字，保存时换成真链接
    $nodeMap = @{}
    $nodeLabels = @()
    $libPath = Join-Path $script:Root 'nodes.json'
    if (Test-Path $libPath) {
        try {
            $lib = Get-Content $libPath -Raw -Encoding UTF8 | ConvertFrom-Json
            foreach ($it in @($lib)) {
                if ($it.label -and $it.link) {
                    $nodeMap["$($it.label)"] = "$($it.link)"
                    $nodeLabels += "$($it.label)"
                }
            }
        } catch { }
    }

    $controls = @{}
    $y = 15
    foreach ($r in $rows) {
        $lb = New-Object System.Windows.Forms.Label
        $lblText = $r.l
        if ($r.k -eq 'node') { $lblText = "节点（库 $($nodeLabels.Count) 个）" }
        $lb.Text = $lblText; $lb.Location = New-Object System.Drawing.Point(15, ($y + 3))
        $lb.Size = New-Object System.Drawing.Size(150, 20)
        $f.Controls.Add($lb)

        if ($r.k -eq 'node') {
            # 节点库下拉：显示可读名字，选中即填入
            $c = New-Object System.Windows.Forms.ComboBox
            $c.DropDownStyle = 'DropDown'
            if ($nodeLabels.Count -gt 0) { $c.Items.AddRange([string[]]$nodeLabels) }
            $cur = "$($p.node)"
            $curLabel = $cur
            foreach ($nk in $nodeMap.Keys) {
                if ($nodeMap[$nk] -eq $cur) { $curLabel = $nk; break }
            }
            $c.Text = $curLabel
            # 展开时重新读库：别的地方加过节点，这里能立刻看到
            $c.add_DropDown({
                param($s, $e)
                $lp = Join-Path $script:Root 'nodes.json'
                if (-not (Test-Path $lp)) { return }
                try {
                    $lib3 = Get-Content $lp -Raw -Encoding UTF8 | ConvertFrom-Json
                    $cur3 = "$($s.Text)"
                    $s.Items.Clear()
                    foreach ($it3 in @($lib3)) {
                        if ($it3.label) { [void]$s.Items.Add("$($it3.label)") }
                    }
                    $s.Text = $cur3
                } catch { }
            })
        } elseif ($r.t -eq 'combo') {
            $c = New-Object System.Windows.Forms.ComboBox
            $c.DropDownStyle = 'DropDown'
            if ($r.items) { $c.Items.AddRange($r.items) }
            elseif ($r.k -eq 'platform') { $c.Items.AddRange(@('macos','linux','windows','host')) }
            else { $c.Items.AddRange(@('America/New_York','Europe/London','Asia/Tokyo','Asia/Shanghai','Europe/Berlin','UTC')) }
        } else {
            $c = New-Object System.Windows.Forms.TextBox
        }
        $c.Location = New-Object System.Drawing.Point(170, $y)
        $c.Size = New-Object System.Drawing.Size(300, 22)
        # 节点字段在上面已经按「库里的名字」设过了，别用原始链接覆盖
        if ($r.k -ne 'node') { $c.Text = "$($p.($r.k))" }
        $f.Controls.Add($c)
        $controls[$r.k] = $c
        $y += 32
    }

    # 右栏：指纹逐项
    $fy = 15
    foreach ($r in $fpRows) {
        $lb = New-Object System.Windows.Forms.Label
        $lb.Text = $r.l; $lb.Location = New-Object System.Drawing.Point(500, ($fy + 3))
        $lb.Size = New-Object System.Drawing.Size(100, 20)
        $f.Controls.Add($lb)

        $c = New-Object System.Windows.Forms.ComboBox
        $c.DropDownStyle = 'DropDown'
        $c.Items.AddRange($r.items)
        $c.Location = New-Object System.Drawing.Point(605, $fy)
        $c.Size = New-Object System.Drawing.Size(150, 22)
        $c.Text = "$($p.($r.k))"
        $f.Controls.Add($c)
        $controls[$r.k] = $c
        $fy += 32
    }

    $hint = New-Object System.Windows.Forms.Label
    $hint.Text = "seed 决定整套假值，同 seed = 同指纹；右栏留空即跟随 seed，填了就覆盖该项。`n" +
                 "Canvas/音频填 off 关掉噪声；WebGL 填 off 不伪造型号；平台填 host 不伪装。`n" +
                 "WebRTC IP：public=视频通话正常、内网 IP 不暴露（不用代理时选这个）；" +
                 "strict=只走代理 UDP，没代理时 WebRTC 完全不可用（用代理防真实 IP 泄漏选这个）；off=不干预。"
    $hint.Location = New-Object System.Drawing.Point(15, ($y + 5))
    $hint.Size = New-Object System.Drawing.Size(740, 72)
    $hint.ForeColor = [System.Drawing.Color]::DimGray
    $f.Controls.Add($hint)

    $ok = New-Object System.Windows.Forms.Button
    $ok.Text = '保存'; $ok.DialogResult = 'OK'
    $ok.Location = New-Object System.Drawing.Point(560, ($y + 70))
    $ok.Size = New-Object System.Drawing.Size(90, 30)
    $f.Controls.Add($ok)

    $cancel = New-Object System.Windows.Forms.Button
    $cancel.Text = '取消'; $cancel.DialogResult = 'Cancel'
    $cancel.Location = New-Object System.Drawing.Point(660, ($y + 70))
    $cancel.Size = New-Object System.Drawing.Size(90, 30)
    $f.Controls.Add($cancel)

    $btnPreset = New-Object System.Windows.Forms.Button
    $btnPreset.Text = '地区预设'
    $btnPreset.Location = New-Object System.Drawing.Point(10, ($y + 72))
    $btnPreset.Size = New-Object System.Drawing.Size(110, 30)
    $f.Controls.Add($btnPreset)
    $btnPreset.Add_Click({
        # GetNewClosure 会把脚本块搬进独立的动态模块，里面用 $script: 前缀
        # 解析不到原脚本的变量（会得到 $null，再调 .Contains 就报"不能对 Null
        # 值表达式调用方法"）。所以先把要用的都取成局部变量，让闭包按值捕获。
        $presets = $script:RegionPresets
        $ctl = $controls
        $apply = {
            param($sender, $e)
            $k = $sender.Tag
            if (-not $k -or -not $presets) { return }
            if (-not $presets.Contains($k)) { return }
            $r = $presets[$k]
            if (-not $r) { return }
            $pairs = @(
                @('timezone',  $r.tz),
                @('lang',      $r.lang),
                @('platform',  $r.plat),
                @('fp_screen', $r.scr),
                @('fp_dpr',    $r.dpr),
                @('geo',       $r.geo)
            )
            foreach ($kv in $pairs) {
                $key = $kv[0]
                if ($ctl.ContainsKey($key) -and $ctl[$key]) {
                    $ctl[$key].Text = "$($kv[1])"
                }
            }
        }.GetNewClosure()
        $menu = New-Object System.Windows.Forms.ContextMenuStrip
        foreach ($n in @($presets.Keys)) {
            $it = $menu.Items.Add($n)
            $it.Tag = $n
            $it.add_Click($apply)
        }
        $menu.Show($btnPreset, 0, $btnPreset.Height)
    })

    $btnPool = New-Object System.Windows.Forms.Button
    $btnPool.Text = '节点池'
    $btnPool.Location = New-Object System.Drawing.Point(250, ($y + 72))
    $btnPool.Size = New-Object System.Drawing.Size(110, 30)
    $f.Controls.Add($btnPool)
    $btnPool.Add_Click({
        if ($nodeLabels.Count -eq 0) {
            [System.Windows.Forms.MessageBox]::Show(
                "节点库是空的。`n`n先点「订阅」拉一批，或者用命令行加：`n" +
                "python node-proxy.py nodes --action add --link `"vless://...`" --name `"名字`"")
            return
        }
        # 当前已选的（把已存在的链接反查成名字）
        $curNames = @{}
        $cur = "$($controls['node'].Text)"
        foreach ($p1 in @($cur -split '\|')) {
            $p1 = $p1.Trim()
            if (-not $p1) { continue }
            $hit = $p1
            foreach ($nk in $nodeMap.Keys) {
                if ($nodeMap[$nk] -eq $p1) { $hit = $nk; break }
            }
            $curNames[$hit] = $true
        }

        $dlg = New-Object System.Windows.Forms.Form
        $dlg.Text = '选择节点（可多选，按顺序组成节点池）'
        $dlg.Size = New-Object System.Drawing.Size(560, 460)
        $dlg.StartPosition = 'CenterParent'
        $dlg.FormBorderStyle = 'FixedDialog'
        $dlg.MaximizeBox = $false
        $dlg.MinimizeBox = $false

        $clb = New-Object System.Windows.Forms.CheckedListBox
        $clb.Location = New-Object System.Drawing.Point(14, 14)
        $clb.Size = New-Object System.Drawing.Size(518, 340)
        $clb.CheckOnClick = $true
        $clb.Font = New-Object System.Drawing.Font('Consolas', 9)
        foreach ($n1 in $nodeLabels) {
            $idx = $clb.Items.Add($n1)
            if ($curNames.ContainsKey($n1)) { $clb.SetItemChecked($idx, $true) }
        }
        $dlg.Controls.Add($clb)

        $lbl = New-Object System.Windows.Forms.Label
        $lbl.Text = '勾选多个即组成节点池，按列表顺序依次尝试'
        $lbl.Location = New-Object System.Drawing.Point(14, 362)
        $lbl.Size = New-Object System.Drawing.Size(518, 20)
        $lbl.ForeColor = [System.Drawing.Color]::DimGray
        $dlg.Controls.Add($lbl)

        $ok2 = New-Object System.Windows.Forms.Button
        $ok2.Text = '确定'; $ok2.DialogResult = 'OK'
        $ok2.Location = New-Object System.Drawing.Point(350, 388)
        $ok2.Size = New-Object System.Drawing.Size(88, 30)
        $dlg.Controls.Add($ok2)
        $no2 = New-Object System.Windows.Forms.Button
        $no2.Text = '取消'; $no2.DialogResult = 'Cancel'
        $no2.Location = New-Object System.Drawing.Point(444, 388)
        $no2.Size = New-Object System.Drawing.Size(88, 30)
        $dlg.Controls.Add($no2)
        $dlg.AcceptButton = $ok2
        $dlg.CancelButton = $no2

        if ($dlg.ShowDialog() -ne 'OK') { $dlg.Dispose(); return }
        $picked = @()
        foreach ($it in $clb.CheckedItems) { $picked += "$it" }
        $dlg.Dispose()
        if ($picked.Count -eq 0) {
            $controls['node'].Text = ''
        } elseif ($picked.Count -eq 1) {
            $controls['node'].Text = $picked[0]
        } else {
            $controls['node'].Text = ($picked -join '|')
            [System.Windows.Forms.MessageBox]::Show(
                "已组成 $($picked.Count) 个节点的池：`n`n" + ($picked -join "`n"))
        }
    })

    $btnSub = New-Object System.Windows.Forms.Button
    $btnSub.Text = '订阅'
    $btnSub.Location = New-Object System.Drawing.Point(130, ($y + 72))
    $btnSub.Size = New-Object System.Drawing.Size(110, 30)
    $f.Controls.Add($btnSub)
    $btnSub.Add_Click({
        $url = [Microsoft.VisualBasic.Interaction]::InputBox(
            "粘贴订阅链接（http/https）。`n`n会拉取并解析出 vless / hysteria2 / tuic / ss 节点，`n填进「节点/节点池」框。",
            '订阅链接', '')
        if (-not $url) { return }
        $url = $url.Trim()
        if ($url -eq '') { return }
        $np = Join-Path $PSScriptRoot 'node-proxy.py'
        if (-not (Test-Path $np)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 node-proxy.py")
            return
        }
        $f.Cursor = [System.Windows.Forms.Cursors]::WaitCursor
        [System.Windows.Forms.Application]::DoEvents()
        $out = (& python $np subscribe --url "$url" 2>&1) -join "`n"
        $f.Cursor = [System.Windows.Forms.Cursors]::Default
        if ($LASTEXITCODE -ne 0) {
            [System.Windows.Forms.MessageBox]::Show("订阅解析失败：`n`n$out")
            return
        }
        # 最后一行是 | 分隔的节点池
        $lines = @($out -split "`n")
        $pool = ''
        if ($lines.Count -gt 0) {
            for ($i = $lines.Count - 1; $i -ge 0; $i--) {
                $ln = $lines[$i]
                if ($ln -and $ln -match '://') { $pool = $ln.Trim(); break }
            }
        }
        if ($pool) {
            $controls['node'].Text = $pool
            # 新节点已入库，重新加载下拉项
            $np2 = Join-Path $PSScriptRoot 'node-proxy.py'
            & python $np2 nodes --action import --url "$url" 2>&1 | Out-Null
            $c2 = $controls['node']
            if (Test-Path $libPath) {
                try {
                    $lib2 = Get-Content $libPath -Raw -Encoding UTF8 | ConvertFrom-Json
                    $c2 = $controls['node']
                    $c2.Items.Clear()
                    foreach ($it2 in @($lib2)) {
                        if ($it2.label) { [void]$c2.Items.Add("$($it2.label)") }
                    }
                } catch { }
            }
            # 自动选第一个，让框里立刻有可见内容
            if ($c2 -and $c2.Items.Count -gt 0) {
                $c2.SelectedIndex = 0
                $controls['node'].Text = "$($c2.Items[0])"
                $pool = "$($c2.Items[0])"
            }
            $n = @($pool -split '\|').Count
            $first = if ($lines.Count -gt 0) { $lines[0] } else { '' }
            [System.Windows.Forms.MessageBox]::Show("已填入 $n 个节点。`n`n$first", '订阅')
        } else {
            [System.Windows.Forms.MessageBox]::Show($out, '订阅结果')
        }
    })

    $btnRand = New-Object System.Windows.Forms.Button
    $btnRand.Text = '一键随机'
    $btnRand.Location = New-Object System.Drawing.Point(430, ($y + 70))
    $btnRand.Size = New-Object System.Drawing.Size(110, 30)
    $f.Controls.Add($btnRand)
    $btnRand.Add_Click({
        $controls['seed'].Text     = Get-Random -Minimum 10000 -Maximum 999999
        $controls['fp_cores'].Text  = @('4','8','12','16','24','32') | Get-Random
        $controls['fp_memory'].Text = @('2','4','8','16','32') | Get-Random
        $controls['fp_screen'].Text = @('1920x1080','2560x1440','1600x900','1366x768','3840x2160','1280x720') | Get-Random
        $controls['fp_dpr'].Text    = @('1','1.25','1.5','2','3') | Get-Random
        $controls['fp_canvas'].Text = @('on','off') | Get-Random
        $controls['fp_webgl'].Text  = @('nvidia','amd','intel') | Get-Random
        $controls['fp_audio'].Text  = @('on','off') | Get-Random
        $controls['fp_fonts'].Text  = @('on','off') | Get-Random
    })

    $f.AcceptButton = $ok
    $f.CancelButton = $cancel

    if ($f.ShowDialog() -ne 'OK') { return $null }

    function Get-Ctl($k) {
        if ($controls.ContainsKey($k) -and $controls[$k]) { return $controls[$k].Text }
        return ''
    }
    $p.name     = Get-Ctl 'name'
    $p.seed     = Get-Ctl 'seed'
    $p.platform = Get-Ctl 'platform'
    $p.timezone = Get-Ctl 'timezone'
    $p.lang     = Get-Ctl 'lang'
    $p.proxy    = Get-Ctl 'proxy'
    $p.rows     = Get-Ctl 'rows'
    $p.extra    = Get-Ctl 'extra'
    $p.fp_cores  = Get-Ctl 'fp_cores'
    $p.fp_memory = Get-Ctl 'fp_memory'
    $p.fp_screen = Get-Ctl 'fp_screen'
    $p.fp_dpr    = Get-Ctl 'fp_dpr'
    $p.fp_canvas = Get-Ctl 'fp_canvas'
    $p.fp_webgl  = Get-Ctl 'fp_webgl'
    $p.fp_audio  = Get-Ctl 'fp_audio'
    $p.fp_fonts  = Get-Ctl 'fp_fonts'
    # 节点字段可能是库里的名字，换成真链接；池用 | 分隔，逐段解析
    $nodeRaw = Get-Ctl 'node'
    $nodeParts = @($nodeRaw -split '\|' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    $nodeResolved = @()
    foreach ($np1 in $nodeParts) {
        if ($nodeMap.ContainsKey($np1)) { $nodeResolved += $nodeMap[$np1] }
        else { $nodeResolved += $np1 }
    }
    $p.node      = ($nodeResolved -join '|')
    $p.geo       = Get-Ctl 'geo'
    $p.webrtc    = Get-Ctl 'webrtc'
    $p.tags      = Get-Ctl 'tags'
    return $p
}

# ---------------------------------------------------------------- 主界面

# ---------------------------------------------------------------- 全局异常
# WinForms 里未捕获的异常会弹"未经处理的异常"框且不给堆栈，先记下来。
$script:GlobalErrorLog = Join-Path $script:Root '_error.log'
[System.Windows.Forms.Application]::SetUnhandledExceptionMode('CatchException')
[System.Windows.Forms.Application]::add_ThreadException({
    param($sender, $e)
    $ex = $e.Exception
    $lines = @(
        "===== $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') =====",
        "Message: $($ex.Message)",
        "Type:    $($ex.GetType().FullName)",
        "Target:  $($ex.TargetSite)"
    )
    if ($ex.StackTrace) { $lines += $ex.StackTrace }
    try { Add-Content -Path $script:GlobalErrorLog -Value ($lines -join "`r`n") -Encoding UTF8 } catch { }
    [System.Windows.Forms.MessageBox]::Show(
        "操作出错：`n`n$($ex.Message)`n`n详细信息已写入：`n$script:GlobalErrorLog",
        '出错了', 'OK', 'Warning')
})

function Show-Manager {
    $cfg = Get-Config

    $form = New-Object System.Windows.Forms.Form
    $form.Text = '多开管理器'
    $form.Size = New-Object System.Drawing.Size(980, 665)
    $form.StartPosition = 'CenterScreen'
    $form.MinimumSize = New-Object System.Drawing.Size(800, 480)

    $lblSearch = New-Object System.Windows.Forms.Label
    $lblSearch.Text = '搜索'
    $lblSearch.Location = New-Object System.Drawing.Point(10, 14)
    $lblSearch.Size = New-Object System.Drawing.Size(40, 22)
    $form.Controls.Add($lblSearch)

    $txtSearch = New-Object System.Windows.Forms.TextBox
    $txtSearch.Location = New-Object System.Drawing.Point(52, 11)
    $txtSearch.Size = New-Object System.Drawing.Size(240, 24)
    $form.Controls.Add($txtSearch)

    $lblFilterHint = New-Object System.Windows.Forms.Label
    $lblFilterHint.Text = '（名称 / 标签 / 平台 / 时区 / seed，留空显示全部）'
    $lblFilterHint.Location = New-Object System.Drawing.Point(300, 14)
    $lblFilterHint.Size = New-Object System.Drawing.Size(400, 22)
    $lblFilterHint.ForeColor = [System.Drawing.Color]::Gray
    $form.Controls.Add($lblFilterHint)

    $grid = New-Object System.Windows.Forms.DataGridView
    $grid.Location = New-Object System.Drawing.Point(10, 40)
    $grid.Size = New-Object System.Drawing.Size(945, 450)
    $grid.Anchor = 'Top,Left,Right,Bottom'
    $grid.AllowUserToAddRows = $false
    $grid.AllowUserToDeleteRows = $false
    $grid.ReadOnly = $true
    $grid.SelectionMode = 'FullRowSelect'
    $grid.MultiSelect = $false
    $grid.AutoSizeColumnsMode = 'Fill'
    $grid.RowHeadersVisible = $false
    foreach ($col in @(
            @{n='名称';w=16},@{n='标签';w=12},@{n='平台';w=8},@{n='时区';w=15},
            @{n='代理';w=18},@{n='seed';w=8},@{n='行';w=4},@{n='状态';w=8},@{n='目录';w=20})) {
        $c = New-Object System.Windows.Forms.DataGridViewTextBoxColumn
        $c.HeaderText = $col.n; $c.Name = $col.n; $c.FillWeight = $col.w
        [void]$grid.Columns.Add($c)
    }
    $form.Controls.Add($grid)

    $status = New-Object System.Windows.Forms.StatusStrip
    $btnFpTest = New-Object System.Windows.Forms.Button
    $btnFpTest.Text = '指纹自检'
    $btnFpTest.Location = New-Object System.Drawing.Point(10, 545)
    $btnFpTest.Size = New-Object System.Drawing.Size(110, 32)
    $form.Controls.Add($btnFpTest)
    $btnFpTest.Add_Click({
        $d = Get-SelectedDir
        if (-not $d) {
            [System.Windows.Forms.MessageBox]::Show('请先在列表里点选一个实例，再点「指纹自检」。')
            return
        }
        $p = Get-Profile $d
        $cfg = Get-Config
        $exe = $cfg.chromeExe
        if (-not (Test-Path $exe)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 chrome.exe：`n$exe")
            return
        }
        $html = Join-Path (Join-Path $PSScriptRoot '..') 'tools\指纹自检.html'
        if (-not (Test-Path $html)) {
            [System.Windows.Forms.MessageBox]::Show("找不到指纹自检.html：`n$html")
            return
        }
        $dataDir = Join-Path $d.FullName 'Data'
        New-Item -ItemType Directory -Force $dataDir | Out-Null
        $a = Build-Args $d $p
        # 用该实例自己的指纹参数打开自检页；实例已在运行时会在其内部新开标签
        # 把实例配置作为「期望值」传给自检页，页面逐项判 PASS/FAIL
        $qs = New-Object System.Collections.Generic.List[string]
        function Add-Q($k, $v) {
            if ($null -ne $v -and "$v".Trim() -ne '') {
                $qs.Add("$k=" + [uri]::EscapeDataString("$v".Trim()))
            }
        }
        Add-Q 'seed'     $p.seed
        Add-Q 'platform' $p.platform
        Add-Q 'timezone' $p.timezone
        Add-Q 'lang'     $p.lang
        Add-Q 'screen'   $p.fp_screen
        Add-Q 'dpr'      $p.fp_dpr
        Add-Q 'cores'    $p.fp_cores
        Add-Q 'memory'   $p.fp_memory
        Add-Q 'geo'      $p.geo
        Add-Q 'fonts'    $p.fp_fonts
        # WebRTC 策略和启动时保持一致
        $wr = "$($p.webrtc)".Trim()
        if ($wr -eq '') { if ($p.proxy -or $p.node) { $wr = 'strict' } else { $wr = 'public' } }
        Add-Q 'webrtc'   $wr
        $url = 'file:///' + ($html -replace '\\', '/')
        if ($qs.Count -gt 0) { $url += '?' + ($qs -join '&') }
        Set-GoogleOAuthEnv
        try {
            Start-Process -FilePath $exe -ArgumentList ($a + ' "' + $url + '"') -WorkingDirectory (Split-Path $exe)
            $statusLabel.Text = "已打开指纹自检：$($d.Name)"
        } catch {
            [System.Windows.Forms.MessageBox]::Show("打开失败：`n$_")
        }
    })

    # ---- 第二排：节点测试 / 批量 / 查重
    $btnNodeTest = New-Object System.Windows.Forms.Button
    $btnNodeTest.Text = '测试节点'
    $btnNodeTest.Location = New-Object System.Drawing.Point(130, 545)
    $btnNodeTest.Size = New-Object System.Drawing.Size(110, 32)
    $btnNodeTest.Anchor = 'Bottom,Left'
    $form.Controls.Add($btnNodeTest)
    $btnNodeTest.Add_Click({
        $d = Get-SelectedDir
        if (-not $d) {
            [System.Windows.Forms.MessageBox]::Show('请先在列表里点选一个实例。')
            return
        }
        $p = Get-Profile $d
        if (-not $p.node -or "$($p.node)".Trim() -eq '') {
            [System.Windows.Forms.MessageBox]::Show("实例「$($d.Name)」没有配置节点链接。`n`n在「编辑 / 设置」的「节点链接(可选)」里粘贴 vless/hy2/tuic/ss 链接。")
            return
        }
        $np = Join-Path $PSScriptRoot 'node-proxy.py'
        if (-not (Test-Path $np)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 node-proxy.py：`n$np")
            return
        }
        $port = 10800 + ([Math]::Abs($d.Name.GetHashCode()) % 100)
        $statusLabel.Text = "正在测试节点（端口 $port）…"
        [System.Windows.Forms.Application]::DoEvents()
        $out = (& python $np test --node "$($p.node)" --port $port --workdir "$($d.FullName)" 2>&1) -join "`n"
        if ($LASTEXITCODE -eq 0) {
            $statusLabel.Text = "节点可用（socks5://127.0.0.1:$port）"
            [System.Windows.Forms.MessageBox]::Show("节点可用 ✓`n`n本地代理：socks5://127.0.0.1:$port`n`n$out", '测试节点')
        } else {
            $statusLabel.Text = '节点测试失败'
            [System.Windows.Forms.MessageBox]::Show("节点测试失败：`n`n$out", '测试节点')
        }
    })

    $btnAllStart = New-Object System.Windows.Forms.Button
    $btnAllStart.Text = '全部启动'
    $btnAllStart.Location = New-Object System.Drawing.Point(370, 545)
    $btnAllStart.Size = New-Object System.Drawing.Size(110, 32)
    $btnAllStart.Anchor = 'Bottom,Left'
    $form.Controls.Add($btnAllStart)
    $btnAllStart.Add_Click({
        $all = @(Get-ProfilePaths)
        if ($all.Count -eq 0) { [System.Windows.Forms.MessageBox]::Show('没有实例。'); return }
        $r = [System.Windows.Forms.MessageBox]::Show(
            "要启动全部 $($all.Count) 个实例吗？`n`n每个实例会占用一份独立内存，请确认机器扛得住。",
            '全部启动', 'YesNo', 'Question')
        if ($r -ne 'Yes') { return }
        $n = 0
        foreach ($d in $all) {
            $statusLabel.Text = "正在启动：$($d.Name)"
            [System.Windows.Forms.Application]::DoEvents()
            try { Start-Instance $d; $n++ } catch { }
            Start-Sleep -Milliseconds 800
        }
        & $refresh
        $statusLabel.Text = "已启动 $n / $($all.Count) 个实例"
    })

    $btnAllStop = New-Object System.Windows.Forms.Button
    $btnAllStop.Text = '全部停止'
    $btnAllStop.Location = New-Object System.Drawing.Point(490, 545)
    $btnAllStop.Size = New-Object System.Drawing.Size(110, 32)
    $btnAllStop.Anchor = 'Bottom,Left'
    $form.Controls.Add($btnAllStop)
    $btnAllStop.Add_Click({
        $all = @(Get-ProfilePaths)
        if ($all.Count -eq 0) { return }
        $r = [System.Windows.Forms.MessageBox]::Show("要停止全部 $($all.Count) 个实例吗？", '全部停止', 'YesNo', 'Question')
        if ($r -ne 'Yes') { return }
        $np = Join-Path $PSScriptRoot 'node-proxy.py'
        $killed = 0
        foreach ($d in $all) {
            if (Test-Path $np) { & python $np stop --workdir "$($d.FullName)" 2>&1 | Out-Null }
            $dataDir = (Join-Path $d.FullName 'Data').TrimEnd('\')
            $procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
                     Where-Object { $_.CommandLine -and $_.CommandLine -like "*$dataDir*" }
            foreach ($pr in $procs) {
                Stop-Process -Id $pr.ProcessId -Force -ErrorAction SilentlyContinue
                $killed++
            }
        }
        Start-Sleep -Milliseconds 800
        & $refresh
        $statusLabel.Text = "已停止 $killed 个进程"
    })

    $btnDup = New-Object System.Windows.Forms.Button
    $btnDup.Text = '指纹查重'
    $btnDup.Location = New-Object System.Drawing.Point(610, 545)
    $btnDup.Size = New-Object System.Drawing.Size(110, 32)
    $btnDup.Anchor = 'Bottom,Left'
    $form.Controls.Add($btnDup)
    $btnDup.Add_Click({
        $all = @(Get-ProfilePaths)
        if ($all.Count -eq 0) { [System.Windows.Forms.MessageBox]::Show('没有实例。'); return }
        $map = @{}
        foreach ($d in $all) {
            $p = Get-Profile $d
            $seed = "$($p.seed)".Trim()
            if ($seed -eq '') { continue }
            if (-not $map.ContainsKey($seed)) { $map[$seed] = @() }
            $map[$seed] += $d.Name
        }
        $dups = @()
        foreach ($k in $map.Keys) {
            if ($map[$k].Count -gt 1) { $dups += ("seed $k  →  " + ($map[$k] -join ', ')) }
        }
        if ($dups.Count -eq 0) {
            [System.Windows.Forms.MessageBox]::Show("共 $($all.Count) 个实例，没有指纹撞车 ✓", '指纹查重')
        } else {
            $msg = "发现 $($dups.Count) 组指纹撞车：`n`n" + ($dups -join "`n") + "`n`n" +
                   "同 seed 的实例指纹完全一样，站点可以把它们关联起来。`n" +
                   "建议在「编辑 / 设置」里点「一键随机」换掉。"
            [System.Windows.Forms.MessageBox]::Show($msg, '指纹查重')
        }
    })

    $btnHealth = New-Object System.Windows.Forms.Button
    $btnHealth.Text = '健康巡检'
    $btnHealth.Location = New-Object System.Drawing.Point(730, 545)
    $btnHealth.Size = New-Object System.Drawing.Size(110, 32)
    $btnHealth.Anchor = 'Bottom,Left'
    $form.Controls.Add($btnHealth)
    $btnHealth.Add_Click({
        $all = @(Get-ProfilePaths)
        if ($all.Count -eq 0) {
            [System.Windows.Forms.MessageBox]::Show('没有实例。')
            return
        }
        $cfg = Get-Config
        $exe = $cfg.chromeExe
        $np = Join-Path $PSScriptRoot 'node-proxy.py'

        # 先统计 seed 重复
        $seedMap = @{}
        foreach ($d in $all) {
            $p = Get-Profile $d
            $s = "$($p.seed)".Trim()
            if ($s -ne '') {
                if (-not $seedMap.ContainsKey($s)) { $seedMap[$s] = @() }
                $seedMap[$s] += $d.Name
            }
        }

        $lines = New-Object System.Collections.Generic.List[string]
        $lines.Add("共 $($all.Count) 个实例`n")
        $bad = 0; $warn = 0; $ok = 0

        foreach ($d in $all) {
            $issues = New-Object System.Collections.Generic.List[string]
            $notes  = New-Object System.Collections.Generic.List[string]

            # profile.json
            $pj = Join-Path $d.FullName 'profile.json'
            if (-not (Test-Path $pj)) {
                $issues.Add('缺 profile.json')
            } else {
                try { $p = Get-Content $pj -Raw -Encoding UTF8 | ConvertFrom-Json }
                catch { $issues.Add('profile.json 解析失败'); $p = $null }
                if ($p) {
                    # seed
                    $s = "$($p.seed)".Trim()
                    if ($s -eq '') {
                        $issues.Add('没有 seed（指纹不会生效）')
                    } elseif ($s -notmatch '^\d+$') {
                        $issues.Add("seed 不是数字：$s")
                    } else {
                        $notes.Add("seed $s / $($p.platform) / $($p.timezone)")
                        if ($seedMap.ContainsKey($s) -and $seedMap[$s].Count -gt 1) {
                            $others = @($seedMap[$s] | Where-Object { $_ -ne $d.Name })
                            $issues.Add("seed 与 $($others -join ', ') 重复")
                        }
                    }
                    # 节点链接
                    $node = "$($p.node)".Trim()
                    if ($node -ne '') {
                        if (-not (Test-Path $np)) {
                            $issues.Add('配了节点但缺 node-proxy.py')
                        } else {
                            $chk = (& python $np check --node "$node" 2>&1) -join ' '
                            if ($LASTEXITCODE -ne 0 -or $chk -match 'ERROR') {
                                $issues.Add("节点链接无效：$($chk.Trim())")
                            } else {
                                $notes.Add("节点 $($chk.Trim() -replace '^OK ','')")
                            }
                        }
                    }
                    # 代理字段格式
                    $px = "$($p.proxy)".Trim()
                    if ($px -ne '' -and $px -notmatch '^(https?|socks[45])://') {
                        $issues.Add("代理格式可疑：$px")
                    }
                }
            }

            # 数据目录
            $dd = Join-Path $d.FullName 'Data'
            if (-not (Test-Path $dd)) {
                $issues.Add('数据目录不存在（首次启动会自动创建）')
            } else {
                $size = (Get-ChildItem $dd -Recurse -File -ErrorAction SilentlyContinue |
                         Measure-Object Length -Sum).Sum
                if ($size -lt 1MB) {
                    $warn++
                    $notes.Add('数据目录几乎为空')
                } else {
                    $notes.Add(('数据目录 {0:N0} MB' -f ($size / 1MB)))
                }
            }

            # 运行状态
            $running = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
                       Where-Object { $_.CommandLine -and $_.CommandLine -like "*$($dd.TrimEnd('\'))*" }
            if ($running) { $notes.Add('运行中') } else { $notes.Add('未运行') }

            if ($issues.Count -gt 0) {
                $bad++
                $lines.Add("[$($d.Name)]  问题")
                foreach ($i in $issues) { $lines.Add("    ! $i") }
            } else {
                $ok++
                $lines.Add("[$($d.Name)]  正常")
            }
            foreach ($n in $notes) { $lines.Add("    - $n") }
            $lines.Add('')
        }

        # 全局检查
        if (-not (Test-Path $exe)) {
            $lines.Add("全局：找不到 chrome.exe -> $exe")
            $bad++
        } else {
            $lines.Add("全局：chrome.exe 正常（{0:N0} MB）" -f ((Get-Item $exe).Length / 1MB))
        }

        $summary = "正常 $ok / 有问题 $bad / 共 $($all.Count)"
        $lines.Insert(0, $summary)
        $statusLabel.Text = "健康巡检：$summary"
        [System.Windows.Forms.MessageBox]::Show(($lines -join "`r`n"), '健康巡检')
    })

    $btnSnap = New-Object System.Windows.Forms.Button
    $btnSnap.Text = '快照'
    $btnSnap.Location = New-Object System.Drawing.Point(850, 545)
    $btnSnap.Size = New-Object System.Drawing.Size(110, 32)
    $btnSnap.Anchor = 'Bottom,Left'
    $form.Controls.Add($btnSnap)
    $btnSnap.Add_Click({
        $d = Get-SelectedDir
        $snapPy = Join-Path $script:Root 'snapshot.py'
        if (-not (Test-Path $snapPy)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 snapshot.py：`n$snapPy")
            return
        }
        # 列出已有快照
        $snapRoot = Join-Path $script:Root '_snapshots'
        $existing = @()
        if (Test-Path $snapRoot) {
            $existing = @(Get-ChildItem $snapRoot -Directory -ErrorAction SilentlyContinue |
                          Select-Object -ExpandProperty Name)
        }
        $listText = if ($existing.Count -gt 0) { "现有快照：`n  " + ($existing -join "`n  ") } else { "现有快照：无" }
        $hint = "输入快照名：`n`n  · 名字已存在  ->  把该快照还原到选中实例`n  · 名字是新的  ->  从选中实例创建快照`n`n$listText"
        $name = [Microsoft.VisualBasic.Interaction]::InputBox($hint, '实例快照', '')
        if (-not $name) { return }
        $name = $name.Trim()
        if ($name -eq '') { return }

        if ($existing -contains $name) {
            if (-not $d) {
                [System.Windows.Forms.MessageBox]::Show('还原需要先在列表里选中目标实例。')
                return
            }
            $r = [System.Windows.Forms.MessageBox]::Show(
                "把快照「$name」还原到实例「$($d.Name)」？`n`n当前数据会先备份到 _backup。",
                '还原快照', 'YesNo', 'Question')
            if ($r -ne 'Yes') { return }
            $statusLabel.Text = "正在还原快照 $name …"
            [System.Windows.Forms.Application]::DoEvents()
            $out = (& python $snapPy restore --name "$name" --to "$($d.Name)" --new-seed 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                [System.Windows.Forms.MessageBox]::Show("还原失败：`n`n$out")
                $statusLabel.Text = '快照还原失败'
                return
            }
            & $script:RefreshAction
            $statusLabel.Text = "已还原：$name -> $($d.Name)"
            [System.Windows.Forms.MessageBox]::Show($out, '还原快照')
        } else {
            if (-not $d) {
                [System.Windows.Forms.MessageBox]::Show('创建快照需要先在列表里选中源实例。')
                return
            }
            $r = [System.Windows.Forms.MessageBox]::Show(
                "从实例「$($d.Name)」创建快照「$name」？`n`n会带上书签/密码/历史/扩展/登录态，跳过缓存。",
                '创建快照', 'YesNo', 'Question')
            if ($r -ne 'Yes') { return }
            $statusLabel.Text = "正在创建快照 $name …"
            [System.Windows.Forms.Application]::DoEvents()
            $out = (& python $snapPy create --from "$($d.Name)" --name "$name" --force 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                [System.Windows.Forms.MessageBox]::Show("创建失败：`n`n$out")
                $statusLabel.Text = '快照创建失败'
                return
            }
            $statusLabel.Text = "快照已创建：$name"
            [System.Windows.Forms.MessageBox]::Show($out, '创建快照')
        }
    })

    $btnBackup = New-Object System.Windows.Forms.Button
    $btnBackup.Text = '备份'
    $btnBackup.Location = New-Object System.Drawing.Point(850, 500)
    $btnBackup.Size = New-Object System.Drawing.Size(110, 32)
    $btnBackup.Anchor = 'Bottom,Left'
    $form.Controls.Add($btnBackup)
    $btnBackup.Add_Click({
        $d = Get-SelectedDir
        $bk = Join-Path $script:Root 'backup.py'
        if (-not (Test-Path $bk)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 backup.py：`n$bk")
            return
        }
        # 收集最近备份
        $rows = @()
        $bkRoot = Join-Path $script:Root '_backup'
        if (Test-Path $bkRoot) {
            Get-ChildItem $bkRoot -Directory -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | ForEach-Object {
                    $m = Join-Path $_.FullName 'meta.json'
                    if (Test-Path $m) {
                        try {
                            $j = Get-Content $m -Raw -Encoding UTF8 | ConvertFrom-Json
                            $rows += [pscustomobject]@{ stamp=$j.stamp; inst=$j.instance; n=$j.bookmarks; tag=$j.tag }
                        } catch { }
                    }
                }
        }
        $rows = @($rows | Select-Object -First 9)
        $listText = if ($rows.Count -gt 0) {
            ($rows | ForEach-Object -Begin { $i = 1 } -Process {
                "  {0}. {1}  {2}  {3} 条" -f $i, $_.stamp, $_.inst, $_.n; $i++
            }) -join "`n"
        } else { "  （还没有备份）" }

        $hint = "输入操作：`n" +
                "  s        备份选中实例`n" +
                "  v        校验书签数量（直接回车也是这个）`n" +
                "  l        列出全部备份`n" +
                "  r1 ~ r9  还原第 N 个备份到选中实例`n" +
                "  p        清理旧的自动备份`n`n" +
                "最近备份：`n$listText"
        $op = [Microsoft.VisualBasic.Interaction]::InputBox($hint, '备份 / 还原', 'v')
        if (-not $op) { return }
        $op = $op.Trim().ToLower()

        if ($op -eq 'l') {
            $out = (& python $bk list 2>&1) -join "`n"
            [System.Windows.Forms.MessageBox]::Show($out, '全部备份')
            return
        }
        if ($op -eq 'p') {
            $out = (& python $bk prune --keep 20 2>&1) -join "`n"
            [System.Windows.Forms.MessageBox]::Show($out, '清理')
            return
        }
        if ($op -eq 's') {
            if (-not $d) { [System.Windows.Forms.MessageBox]::Show('先选中一个实例。'); return }
            $statusLabel.Text = "正在备份 $($d.Name) …"
            [System.Windows.Forms.Application]::DoEvents()
            $out = (& python $bk save --from "$($d.Name)" --tag manual 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                [System.Windows.Forms.MessageBox]::Show("备份失败：`n`n$out")
                $statusLabel.Text = '备份失败'
                return
            }
            $statusLabel.Text = "已备份 $($d.Name)"
            [System.Windows.Forms.MessageBox]::Show($out, '备份')
            return
        }
        if ($op -match '^r([1-9])$') {
            $idx = [int]$matches[1]
            if (-not $d) { [System.Windows.Forms.MessageBox]::Show('先选中目标实例。'); return }
            if (-not $rows -or $rows.Count -eq 0) {
                [System.Windows.Forms.MessageBox]::Show('还没有任何备份，先按 s 备份一次。'); return
            }
            if ($idx -lt 1 -or $idx -gt $rows.Count) {
                [System.Windows.Forms.MessageBox]::Show("编号超出范围（当前 $($rows.Count) 个备份）。"); return
            }
            $pick = $rows[$idx - 1]
            if (-not $pick) { [System.Windows.Forms.MessageBox]::Show('备份记录读取失败。'); return }
            $r = [System.Windows.Forms.MessageBox]::Show(
                "把备份 $($pick.stamp)（$($pick.inst)，$($pick.n) 条书签）还原到「$($d.Name)」？`n`n" +
                "只还原书签。当前状态会先自动备份。",
                '还原书签', 'YesNo', 'Question')
            if ($r -ne 'Yes') { return }
            $statusLabel.Text = '正在还原书签 …'
            [System.Windows.Forms.Application]::DoEvents()
            $out = (& python $bk restore --from "$($pick.stamp)" --to "$($d.Name)" --what bookmarks 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                [System.Windows.Forms.MessageBox]::Show("还原失败：`n`n$out")
                $statusLabel.Text = '还原失败'
                return
            }
            $statusLabel.Text = "已还原书签：$($pick.stamp)"
            [System.Windows.Forms.MessageBox]::Show($out, '还原书签')
            return
        }
        # 默认：校验
        if (-not $d) { [System.Windows.Forms.MessageBox]::Show('先选中一个实例。'); return }
        $out = (& python $bk verify --instance "$($d.Name)" 2>&1) -join "`n"
        $statusLabel.Text = if ($LASTEXITCODE -eq 0) { "校验正常：$($d.Name)" } else { "校验异常：$($d.Name)" }
        [System.Windows.Forms.MessageBox]::Show($out, '书签校验')
    })

    $statusLabel = New-Object System.Windows.Forms.ToolStripStatusLabel
    $statusLabel.Text = '就绪'
    $script:StatusLabel = $statusLabel
    [void]$status.Items.Add($statusLabel)
    $form.Controls.Add($status)

    # ---- 排序状态（点列头切换）
    $script:SortCol = ''
    $script:SortAsc = $true

    # ---- 刷新列表
    $script:dirs = @{}
    $refresh = {
        $running = Get-RunningMap
        $grid.Rows.Clear()
        $script:dirs = @{}
    $script:RefreshAction = $refresh
    $txtSearch.Add_TextChanged({ & $script:RefreshAction })
        # 载入节点库映射，代理列要按链接反查名字
        $script:NodeLibMap = @{}
        $lp0 = Join-Path $script:Root 'nodes.json'
        if (Test-Path $lp0) {
            try {
                $lib0 = Get-Content $lp0 -Raw -Encoding UTF8 | ConvertFrom-Json
                foreach ($it0 in @($lib0)) {
                    if ($it0.label -and $it0.link) { $script:NodeLibMap["$($it0.label)"] = "$($it0.link)" }
                }
            } catch { }
        }

        $filter = "$($txtSearch.Text)".Trim().ToLower()
        $shown = 0
        $items = @()
        foreach ($d in (Get-ProfilePaths)) {
            $p = Get-Profile $d
            # 搜索：名称 / 标签 / 平台 / 时区 / seed 任一命中
            if ($filter -ne '') {
                $hay = ("$($d.Name) $($p.tags) $($p.platform) $($p.timezone) $($p.seed)").ToLower()
                if (-not $hay.Contains($filter)) { continue }
            }
            $script:dirs[$d.Name] = $d
            $shown++
            $dataDir = (Join-Path $d.FullName 'Data').TrimEnd('\')
            $isRunning = $running.ContainsKey($dataDir)
            $st = if ($isRunning) { "运行 $($running[$dataDir])" } else { '未运行' }
            # 代理列：有直连代理显示代理；只配了节点就显示节点摘要；都没有才是直连
            if ($p.proxy) {
                $proxyShow = "$($p.proxy)"
            } elseif ($p.node) {
                $nodeStr = "$($p.node)"
                $nodeCnt = @($nodeStr -split '\|' | Where-Object { $_.Trim() }).Count
                if ($nodeCnt -gt 1) {
                    $proxyShow = "节点池 $nodeCnt 个"
                } else {
                    # 单节点：库里有就用名字，否则用协议名
                    $short = ''
                    foreach ($nk2 in $script:NodeLibMap.Keys) {
                        if ($script:NodeLibMap[$nk2] -eq $nodeStr) { $short = $nk2; break }
                    }
                    if (-not $short) {
                        $short = ($nodeStr -split '://')[0] + ' 节点'
                    }
                    $proxyShow = $short
                }
            } else {
                $proxyShow = '直连'
            }
            $items += [pscustomobject]@{
                name = $d.Name; tags = "$($p.tags)"; platform = "$($p.platform)"
                timezone = "$($p.timezone)"; proxy = $proxyShow
                seed = "$($p.seed)"; rows = "$($p.rows)"; status = $st
                dir = $d.FullName; running = $isRunning
            }
        }
        # 排序：数值列按数字比，其余按字符串比
        if ($script:SortCol -ne '') {
            $k = $script:SortCol
            if ($k -eq 'seed' -or $k -eq 'rows') {
                $items = $items | Sort-Object @{ Expression = { [long]("0" + $_.$k) }; Descending = (-not $script:SortAsc) }
            } else {
                $items = $items | Sort-Object @{ Expression = { "$($_.$k)" }; Descending = (-not $script:SortAsc) }
            }
        }
        foreach ($it in $items) {
            [void]$grid.Rows.Add($it.name, $it.tags, $it.platform, $it.timezone,
                                 $it.proxy, $it.seed, $it.rows, $it.status, $it.dir)
            if ($it.running) {
                $grid.Rows[$grid.Rows.Count - 1].DefaultCellStyle.ForeColor = [System.Drawing.Color]::SeaGreen
            }
        }
        $allCount = @(Get-ProfilePaths).Count
        $fnote = if ($filter -ne '') { "（已过滤，共 $allCount 个）" } else { '' }
        $statusLabel.Text = "显示 $shown 个实例$fnote，运行中 $($running.Count) 个  |  根目录 $script:Root"
    }

    # 点列头排序：同列再点一次反向
    $colKey = @{
        '名称' = 'name'; '标签' = 'tags'; '平台' = 'platform'; '时区' = 'timezone'
        '代理' = 'proxy'; 'seed' = 'seed'; '行' = 'rows'; '状态' = 'status'; '目录' = 'dir'
    }
    $grid.Add_ColumnHeaderMouseClick({
        param($sender, $e)
        $nm = $grid.Columns[$e.ColumnIndex].Name
        if (-not $colKey.ContainsKey($nm)) { return }
        $k = $colKey[$nm]
        if ($script:SortCol -eq $k) {
            $script:SortAsc = -not $script:SortAsc
        } else {
            $script:SortCol = $k
            $script:SortAsc = $true
        }
        $arrow = if ($script:SortAsc) { '↑' } else { '↓' }
        foreach ($c in $grid.Columns) {
            $c.HeaderText = $c.HeaderText -replace ' [↑↓]$', ''
        }
        $grid.Columns[$e.ColumnIndex].HeaderText += " $arrow"
        & $script:RefreshAction
    })

    function Get-SelectedDir {
        if ($grid.SelectedRows.Count -eq 0) { return $null }
        $n = $grid.SelectedRows[0].Cells[0].Value
        if ($script:dirs.ContainsKey($n)) { return $script:dirs[$n] }
        return $null
    }

    # ---- 按钮
    $btnNew = New-Object System.Windows.Forms.Button
    $btnNew.Text = '新建'; $btnNew.Location = New-Object System.Drawing.Point(10, 500); $btnNew.Size = New-Object System.Drawing.Size(90, 32)
    $btnNew.Anchor = 'Bottom,Left'

    $btnEdit = New-Object System.Windows.Forms.Button
    $btnEdit.Text = '编辑 / 设置'; $btnEdit.Location = New-Object System.Drawing.Point(110, 500); $btnEdit.Size = New-Object System.Drawing.Size(110, 32)
    $btnEdit.Anchor = 'Bottom,Left'

    $btnStart = New-Object System.Windows.Forms.Button
    $btnStart.Text = '启动'; $btnStart.Location = New-Object System.Drawing.Point(230, 500); $btnStart.Size = New-Object System.Drawing.Size(90, 32)
    $btnStart.Anchor = 'Bottom,Left'

    $btnStop = New-Object System.Windows.Forms.Button
    $btnStop.Text = '停止'; $btnStop.Location = New-Object System.Drawing.Point(330, 500); $btnStop.Size = New-Object System.Drawing.Size(90, 32)
    $btnStop.Anchor = 'Bottom,Left'

    $btnClone = New-Object System.Windows.Forms.Button
    $btnClone.Text = '复制'; $btnClone.Location = New-Object System.Drawing.Point(430, 500); $btnClone.Size = New-Object System.Drawing.Size(90, 32)
    $btnClone.Anchor = 'Bottom,Left'

    $btnOpen = New-Object System.Windows.Forms.Button
    $btnOpen.Text = '打开目录'; $btnOpen.Location = New-Object System.Drawing.Point(530, 500); $btnOpen.Size = New-Object System.Drawing.Size(100, 32)
    $btnOpen.Anchor = 'Bottom,Left'

    $btnRefresh = New-Object System.Windows.Forms.Button
    $btnRefresh.Text = '刷新'; $btnRefresh.Location = New-Object System.Drawing.Point(640, 500); $btnRefresh.Size = New-Object System.Drawing.Size(90, 32)
    $btnRefresh.Anchor = 'Bottom,Left'
    $btnCore = New-Object System.Windows.Forms.Button
    $btnCore.Text = '检测代理内核'
    $btnCore.Location = New-Object System.Drawing.Point(250, 545)
    $btnCore.Size = New-Object System.Drawing.Size(110, 32)
    $form.Controls.Add($btnCore)
    $btnCore.Add_Click({
        $np = Join-Path $PSScriptRoot 'node-proxy.py'
        if (-not (Test-Path $np)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 node-proxy.py：$np")
            return
        }
        $which = (& python $np which 2>&1) -join ''
        if ($which.Trim() -eq 'NONE' -or $which -match 'ERROR') {
            $r = [System.Windows.Forms.MessageBox]::Show(
                "没有找到代理内核。`n`n要现在自动下载 sing-box 吗？（约 20MB，需能访问 GitHub）",
                '检测代理内核', 'YesNo', 'Question')
            if ($r -ne 'Yes') { return }
            $statusLabel.Text = '正在下载 sing-box…'
            [System.Windows.Forms.Application]::DoEvents()
            $dl = (& python $np ensure-core 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                [System.Windows.Forms.MessageBox]::Show("下载失败：`n`n$dl")
                return
            }
            $statusLabel.Text = '内核已就绪'
            [System.Windows.Forms.MessageBox]::Show("已就绪：`n$dl")
        } else {
            [System.Windows.Forms.MessageBox]::Show("当前内核：`n$which")
        }
    })

    $btnSync = New-Object System.Windows.Forms.Button
    $btnSync.Text = '同步数据'; $btnSync.Location = New-Object System.Drawing.Point(740, 500); $btnSync.Size = New-Object System.Drawing.Size(100, 32)
    $btnSync.Anchor = 'Bottom,Left'


    foreach ($b in @($btnNew, $btnEdit, $btnStart, $btnStop, $btnClone, $btnOpen, $btnRefresh, $btnSync)) { $form.Controls.Add($b) }

    # ---- 事件
    $btnRefresh.Add_Click({ & $refresh })

    $btnSync.Add_Click({
        # 同步前先备份，同步脚本会改书签/密码，出问题能还原
        $bk = Join-Path $script:Root 'backup.py'
        $d = Get-SelectedDir
        if ($d -and (Test-Path $bk)) {
            $statusLabel.Text = "同步前备份 $($d.Name) …"
            [System.Windows.Forms.Application]::DoEvents()
            & python $bk save --from "$($d.Name)" --tag auto 2>&1 | Out-Null
        }
        $bat = Join-Path $script:Root '同步数据.bat'
        if (Test-Path $bat) { Start-Process -FilePath $bat }
        else { [System.Windows.Forms.MessageBox]::Show("找不到 $bat") | Out-Null }
    })

    $btnNew.Add_Click({
        $name = [Microsoft.VisualBasic.Interaction]::InputBox('实例名称（也是目录名）', '新建实例', '新实例')
        if (-not $name) { return }
        $dir = Join-Path $script:Root $name
        if (Test-Path $dir) { [System.Windows.Forms.MessageBox]::Show('同名实例已存在'); return }
        New-Item -ItemType Directory -Force $dir | Out-Null
        New-Item -ItemType Directory -Force (Join-Path $dir 'Data') | Out-Null
        $p = [pscustomobject]@{ name = $name; seed = (Get-Random -Minimum 1000 -Maximum 99999); platform = 'macos'
                                timezone = 'America/New_York'; lang = ''; proxy = ''; rows = 1; extra = ''; enabled = $true
                                fp_cores = ''; fp_memory = ''; fp_screen = ''; fp_dpr = ''
                                fp_canvas = ''; fp_webgl = ''; fp_audio = ''; fp_fonts = ''; node = ''
                                webrtc = ''; geo = ''; tags = '' }
        # 新建后直接进设置界面：地理位置、地区预设、订阅都在这儿，
        # 不打开的话这些项根本选不到。
        $np = Show-EditDialog (Get-Item $dir) $p $true
        if (-not $np) {
            # 用户取消：删掉刚建的目录，避免留空壳
            Remove-Item $dir -Recurse -Force -ErrorAction SilentlyContinue
            & $refresh
            return
        }
        Save-Profile (Get-Item $dir) $np
        & $refresh
        $statusLabel.Text = "已创建实例 $($np.name)" 
    })

    $btnEdit.Add_Click({
        $d = Get-SelectedDir
        if (-not $d) {
            [System.Windows.Forms.MessageBox]::Show('请先在列表里点选一个实例，再点「编辑 / 设置」。')
            return
        }
        $p = Get-Profile $d
        $np = Show-EditDialog $d $p $false
        if ($np) { Save-Profile $d $np; & $refresh }
    })

# --------------------------------------------------------------- 启动实例
# 抽成函数，供「启动」和「全部启动」共用
function Start-Instance($d) {
    $p = Get-Profile $d
    $cfg = Get-Config
    $exe = $cfg.chromeExe
    if (-not (Test-Path $exe)) {
        [System.Windows.Forms.MessageBox]::Show("找不到 chrome.exe：`n$exe`n`n请修改 $script:CfgPath 里的 chromeExe")
        return
    }
    $dataDir = Join-Path $d.FullName 'Data'
    New-Item -ItemType Directory -Force $dataDir | Out-Null
    # WebRTC IP 防泄漏：STUN 会把真实出口 IP 暴露给网页。命令行开关
    # --force-webrtc-ip-handling-policy 在本版本只有测试代码读，生产代码不认，
    # 所以改成启动前写 pref（Chrome 只在启动时读一次）。
    $wr = "$($p.webrtc)".Trim()
    if ($wr -eq '') {
        if ($p.proxy -or $p.node) { $wr = 'strict' } else { $wr = 'public' }
    }
    $wrPolicy = ''
    if ($wr -eq 'strict') { $wrPolicy = 'disable_non_proxied_udp' }
    elseif ($wr -eq 'public') { $wrPolicy = 'default_public_interface_only' }
    elseif ($wr -eq 'off') { $wrPolicy = 'default' }
    $sp = Join-Path $script:Root 'set-pref.py'
    if ($wrPolicy -and (Test-Path $sp)) {
        $profDir = Join-Path $dataDir 'Default'
        New-Item -ItemType Directory -Force $profDir | Out-Null
        & python $sp --profile "$profDir" --set "webrtc.ip_handling_policy=$wrPolicy" 2>&1 | Out-Null
    }
    # 节点字段如果存的是库里的名字（没有 ://），启动前解析成真链接
    if ($p.node -and "$($p.node)" -notmatch '://') {
        $npRes = Join-Path $script:Root 'node-proxy.py'
        if (Test-Path $npRes) {
            $resolved = (& python $npRes resolve --spec "$($p.node)" 2>&1) -join ''
            if ($LASTEXITCODE -eq 0 -and $resolved -match '://') {
                $p.node = $resolved.Trim()
            } else {
                [System.Windows.Forms.MessageBox]::Show(
                    "节点「$($p.node)」在节点库里找不到，无法解析。`n`n" +
                    "请到编辑对话框重新选一个节点。")
                return
            }
        }
    }
    $args = Build-Args $d $p
    # 节点链接：用 sing-box 转成本地 SOCKS5，再把浏览器指过去
    if ($p.node -and "$($p.node)".Trim() -ne '') {
        $port = 10800 + ([Math]::Abs($d.Name.GetHashCode()) % 100)
        $np = Join-Path $PSScriptRoot 'node-proxy.py'
        if (-not (Test-Path $np)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 node-proxy.py：$np")
            return
        }
        # 先确认有可用内核；没有就自动下载 sing-box
        $core = (& python $np which 2>&1) -join ''
        if ($core.Trim() -eq 'NONE' -or $core -match 'ERROR') {
            $script:StatusLabel.Text = '未找到代理内核，正在自动下载 sing-box…'
            [System.Windows.Forms.Application]::DoEvents()
            $dl = (& python $np ensure-core 2>&1) -join "`n"
            if ($LASTEXITCODE -ne 0) {
                $msg = "没有可用的代理内核，自动下载也失败了。`n`n$dl`n`n" +
                       "你可以任选其一：`n" +
                       "  1) 安装 v2rayN / FlClash / sing-box，程序会自动探测`n" +
                       "  2) 手动把 sing-box.exe 放到：`n     $($script:Root)\tools\`n`n" +
                       "不用节点代理的话，把「节点链接」留空即可。"
                [System.Windows.Forms.MessageBox]::Show($msg)
                return
            }
            $script:StatusLabel.Text = '内核已就绪'
        }
        $script:StatusLabel.Text = '启动节点代理…'
        [System.Windows.Forms.Application]::DoEvents()
        $out = (& python $np start --node "$($p.node)" --port $port --workdir "$($d.FullName)" 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0) {
            [System.Windows.Forms.MessageBox]::Show("节点代理启动失败：`n$out")
            return
        }
        $args = $args -replace '--proxy-server=\S+', ''
        $args = $args + " --proxy-server=socks5://127.0.0.1:$port"
        $script:StatusLabel.Text = "节点代理就绪（端口 $port）"
    }
    Set-GoogleOAuthEnv
    try {
        Start-Process -FilePath $exe -ArgumentList $args -WorkingDirectory (Split-Path $exe)
        $script:StatusLabel.Text = "已启动：$($d.Name)"
        Start-Sleep -Milliseconds 1200
        & $script:RefreshAction
        # 校验 data_dir 是否真的落在该实例目录（Chrome++ 的 ini data_dir 可能抢走）
        Start-Sleep -Seconds 4
        if (-not (Test-Path (Join-Path $dataDir 'Local State'))) {
            [System.Windows.Forms.MessageBox]::Show(
                "已启动，但 5 秒内没有在`n$dataDir`n看到 'Local State'。`n`n" +
                "这通常意味着 Chrome++ 的 chrome++.ini 里 data_dir= 抢占了数据目录。`n" +
                "解决办法：把 chrome++.ini 里的 data_dir 一行改成：`n    data_dir=%app%\..\Data`  →  删除或留空`n" +
                "或改成 data_dir=none，让 --user-data-dir 生效。", '数据目录可能没生效')
        }
    } catch {
        [System.Windows.Forms.MessageBox]::Show("启动失败：$($_.Exception.Message)")
    }
}

    $btnStart.Add_Click({
        $d = Get-SelectedDir; if (-not $d) { return }
        Start-Instance $d
    })

    $btnStop.Add_Click({
        $d = Get-SelectedDir; if (-not $d) { return }
        $np = Join-Path $PSScriptRoot 'node-proxy.py'
        if (Test-Path $np) { & python $np stop --workdir "$($d.FullName)" 2>&1 | Out-Null }
        $dataDir = (Join-Path $d.FullName 'Data').TrimEnd('\')
        $procs = Get-CimInstance Win32_Process -Filter "Name='chrome.exe'" -ErrorAction SilentlyContinue |
                 Where-Object { $_.CommandLine -and $_.CommandLine -like "*$dataDir*" }
        foreach ($pr in $procs) { Stop-Process -Id $pr.ProcessId -Force -ErrorAction SilentlyContinue }
        Start-Sleep -Milliseconds 600
        & $refresh
    })

    $btnClone.Add_Click({
        $d = Get-SelectedDir; if (-not $d) { return }
        $name = [Microsoft.VisualBasic.Interaction]::InputBox(
            "新实例名称`n`n接下来会问你：只复制配置，还是连数据目录一起复制。",
            '克隆实例', "$($d.Name)-副本")
        if (-not $name) { return }
        $nd = Join-Path $script:Root $name
        if (Test-Path $nd) { [System.Windows.Forms.MessageBox]::Show('同名已存在'); return }

        # 问是否连数据一起复制
        $withData = $false
        $srcData = Join-Path $d.FullName 'Data'
        if (Test-Path $srcData) {
            $sz = (Get-ChildItem $srcData -Recurse -File -ErrorAction SilentlyContinue |
                   Measure-Object Length -Sum).Sum
            $ans = [System.Windows.Forms.MessageBox]::Show(
                "要连数据目录一起复制吗？`n`n" +
                "  是  = 完整克隆（书签/密码/历史/扩展/登录态，约 " +
                ("{0:N0} MB" -f ($sz / 1MB)) + "）`n" +
                "  否  = 只复制配置（新实例从头开始）`n`n" +
                "完整克隆会跳过缓存目录。",
                '克隆实例', 'YesNoCancel', 'Question')
            if ($ans -eq 'Cancel') { return }
            $withData = ($ans -eq 'Yes')
        }

        $snapPy = Join-Path $script:Root 'snapshot.py'
        if (-not (Test-Path $snapPy)) {
            [System.Windows.Forms.MessageBox]::Show("找不到 snapshot.py：`n$snapPy")
            return
        }
        $statusLabel.Text = if ($withData) { "正在完整克隆 $($d.Name) …" } else { "正在克隆配置 …" }
        [System.Windows.Forms.Application]::DoEvents()
        $clArgs = @('clone', '--from', "$($d.Name)", '--to', "$name", '--force')
        if ($withData) { $clArgs += '--with-data' }
        $out = (& python $snapPy @clArgs 2>&1) -join "`n"
        if ($LASTEXITCODE -ne 0) {
            [System.Windows.Forms.MessageBox]::Show("克隆失败：`n`n$out")
            $statusLabel.Text = '克隆失败'
            return
        }
        & $refresh
        $statusLabel.Text = "已克隆: $($d.Name) -> $name"
        [System.Windows.Forms.MessageBox]::Show($out, '克隆完成')
    })

    $btnOpen.Add_Click({
        $d = Get-SelectedDir; if (-not $d) { return }
        Start-Process explorer.exe $d.FullName
    })

    $grid.Add_CellDoubleClick({ if ($_.RowIndex -ge 0) { $btnStart.PerformClick() } })

    & $refresh
    [void]$form.ShowDialog()
}

# ---------------------------------------------------------------- 入口

if ($SelfTest) {
    Write-Host "=== SelfTest ==="
    Write-Host "root     : $script:Root"
    Write-Host "chromeExe: $((Get-Config).chromeExe)"
    $tmp = Join-Path $script:Root '_selftest'
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $sample = [pscustomobject]@{ name='样例'; seed=1001; platform='macos'; timezone='America/New_York'
                                 lang='en-US,en'; proxy='http://127.0.0.1:7890'; rows=3; extra=''; enabled=$true }
    Save-Profile (Get-Item $tmp) $sample
    Write-Host "样例命令 :"
    Write-Host ("  chrome.exe " + (Build-Args (Get-Item $tmp) $sample))
    Write-Host "运行状态 : $(Get-RunningMap | ConvertTo-Json -Compress)"
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    return
}

Show-Manager
