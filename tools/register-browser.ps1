<#
.SYNOPSIS
    把自编译的 Chromium 注册成 Windows 可识别的浏览器。

.DESCRIPTION
    编译出来的裸 chrome.exe 不会出现在「设置 → 默认应用 → Web 浏览器」里，
    因为那套列表来自注册表，只有官方安装包才会写。这个脚本手动写进去。

    只改 HKCU，不需要管理员权限，不影响系统里已有的浏览器。

.PARAMETER ChromePath
    chrome.exe 的完整路径。

.PARAMETER Name
    显示名称，默认 "KZ Browser"。会出现在默认应用列表和开始菜单里。

.PARAMETER Unregister
    卸载注册信息，恢复到注册前的状态。

.EXAMPLE
    .\register-browser.ps1 -ChromePath "D:\KZBuild\App\chrome.exe"

.EXAMPLE
    .\register-browser.ps1 -ChromePath "D:\KZBuild\App\chrome.exe" -Name "指纹浏览器"

.EXAMPLE
    .\register-browser.ps1 -Name "KZ Browser" -Unregister
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string]$ChromePath,

    [string]$Name = 'KZ Browser',

    # 指定数据目录。填了之后点链接会用这个 profile 打开，
    # 适合把默认浏览器做成「主控浏览器」，跟多开实例共享书签/扩展。
    [string]$UserDataDir,

    # 默认应用列表/ProgID/开始菜单用的独立图标 (.ico)。
    # 不传就找 chrome.exe 同目录的 'KZ Browser.ico'。
    [string]$IconPath,

    [switch]$Unregister
)

$ErrorActionPreference = 'Stop'

    # 默认应用列表读 Capabilities\ApplicationIcon，必须指向独立 .ico。
    # 用 "$ChromePath,0" 会和 Chromium SDK 自己注册的项撞成同一图标。
    if (-not $IconPath -and $ChromePath) {
        $cand = Join-Path (Split-Path $ChromePath -Parent) 'KZ Browser.ico'
        if (Test-Path $cand) { $IconPath = $cand }
    }
    if ($IconPath -and (Test-Path $IconPath)) {
        $IconSpec = "$IconPath,0"
        Write-Host "  使用独立图标: $IconSpec" -ForegroundColor DarkGray
    } elseif ($ChromePath) {
        $IconSpec = "$ChromePath,0"
    } else { $IconSpec = '' }

# ---------------------------------------------------------------- 工具

function Write-Step($msg) { Write-Host "  $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "  ! $msg" -ForegroundColor Yellow }

# 生成一个稳定但唯一的 ProgID 后缀，避免和官方 Chrome 撞车
function Get-StableHash([string]$s) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($s)
    $md5 = [Security.Cryptography.MD5]::Create()
    $hash = $md5.ComputeHash($bytes)
    return ([BitConverter]::ToString($hash) -replace '-', '').Substring(0, 12)
}

# ---------------------------------------------------------------- 卸载

if ($Unregister) {
    Write-Host "`n卸载浏览器注册信息：$Name`n" -ForegroundColor White

    # 找所有指向本脚本命名空间的键
    $regApps = 'HKCU:\Software\RegisteredApplications'
    if (Test-Path $regApps) {
        $props = Get-ItemProperty $regApps
        foreach ($p in $props.PSObject.Properties) {
            if ($p.Name -notlike 'PS*' -and "$($p.Value)" -like "Software\$Name\Capabilities") {
                Remove-ItemProperty -Path $regApps -Name $p.Name -ErrorAction SilentlyContinue
                Write-Ok "删除 RegisteredApplications\$($p.Name)"
            }
        }
    }

    foreach ($k in @(
        "HKCU:\Software\Clients\StartMenuInternet\$Name",
        "HKCU:\Software\$Name",
        "HKCU:\Software\Classes\$Name.HTML",
        "HKCU:\Software\Classes\$Name.URL"
    )) {
        if (Test-Path $k) {
            Remove-Item $k -Recurse -Force -ErrorAction SilentlyContinue
            Write-Ok "删除 $k"
        }
    }

    # 开始菜单快捷方式
    $lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\$Name.lnk"
    if (Test-Path $lnk) { Remove-Item $lnk -Force; Write-Ok "删除开始菜单快捷方式" }

    Write-Host "`n卸载完成。`n" -ForegroundColor White
    return
}

# ---------------------------------------------------------------- 前置检查

if (-not $ChromePath) {
    Write-Host "`n用法: .\register-browser.ps1 -ChromePath `"D:\KZBuild\App\chrome.exe`"`n" -ForegroundColor Yellow
    exit 1
}

$ChromePath = (Resolve-Path $ChromePath -ErrorAction SilentlyContinue).Path
if (-not $ChromePath -or -not (Test-Path $ChromePath)) {
    Write-Host "`n✗ 找不到 chrome.exe：$ChromePath`n" -ForegroundColor Red
    exit 1
}

$exeDir = Split-Path $ChromePath -Parent
$ver = (Get-Item $ChromePath).VersionInfo

Write-Host "`n注册浏览器`n" -ForegroundColor White
Write-Step "路径:   $ChromePath"
Write-Step "版本:   $($ver.ProductVersion)"
Write-Step "产品:   $($ver.ProductName)"
Write-Step "名称:   $Name"
if ($UserDataDir) { Write-Step "数据目录: $UserDataDir" }
Write-Host ""

# 关键文件检查——缺了 chrome.dll 注册了也起不来
$required = @('chrome.dll', 'icudtl.dat', 'v8_context_snapshot.bin')
$missing = @()
foreach ($f in $required) {
    if (-not (Test-Path (Join-Path $exeDir $f))) { $missing += $f }
}
if ($missing.Count -gt 0) {
    Write-Warn2 "目录里缺少: $($missing -join ', ')"
    Write-Warn2 "注册后点链接会起不来，先补齐再注册"
}

# ---------------------------------------------------------------- 写注册表

$htmlProgId = "$Name.HTML"
$urlProgId  = "$Name.URL"
if ($UserDataDir) {
    $UserDataDir = [IO.Path]::GetFullPath($UserDataDir)
    if (-not (Test-Path $UserDataDir)) {
        New-Item -ItemType Directory -Force $UserDataDir | Out-Null
        Write-Step "已创建数据目录: $UserDataDir"
    }
    $openCmd = "`"$ChromePath`" -- `"%1`""
    $plainCmd = "`"$ChromePath`""
} else {
    $openCmd = "`"$ChromePath`" -- `"%1`""
    $plainCmd = "`"$ChromePath`""
}

# ---- 1. ProgID：HTML 文档
Write-Step "写 ProgID ($htmlProgId)"
$k = "HKCU:\Software\Classes\$htmlProgId"
New-Item -Path $k -Force | Out-Null
Set-ItemProperty -Path $k -Name '(default)' -Value "$Name Document"
New-Item -Path "$k\DefaultIcon" -Force | Out-Null
Set-ItemProperty -Path "$k\DefaultIcon" -Name '(default)' -Value $IconSpec
New-Item -Path "$k\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "$k\shell\open\command" -Name '(default)' -Value $openCmd
Write-Ok "OK"

# ---- 2. ProgID：URL 协议
Write-Step "写 ProgID ($urlProgId)"
$k = "HKCU:\Software\Classes\$urlProgId"
New-Item -Path $k -Force | Out-Null
Set-ItemProperty -Path $k -Name '(default)' -Value "$Name URL"
Set-ItemProperty -Path $k -Name 'URL Protocol' -Value ''
New-Item -Path "$k\DefaultIcon" -Force | Out-Null
Set-ItemProperty -Path "$k\DefaultIcon" -Name '(default)' -Value $IconSpec
New-Item -Path "$k\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "$k\shell\open\command" -Name '(default)' -Value $openCmd
Write-Ok "OK"

# ---- 4. RegisteredApplications（注册为「应用」）
Write-Step "写 RegisteredApplications"
$k = 'HKCU:\Software\RegisteredApplications'
if (-not (Test-Path $k)) { New-Item -Path $k -Force | Out-Null }
Set-ItemProperty -Path $k -Name $Name -Value "Software\Clients\StartMenuInternet\$Name\Capabilities"
Write-Ok "OK"

# ---- 5. StartMenuInternet（让它在「默认程序」传统界面里也出现）
Write-Step "写 StartMenuInternet"
$k = "HKCU:\Software\Clients\StartMenuInternet\$Name"
New-Item -Path $k -Force | Out-Null
Set-ItemProperty -Path $k -Name '(default)' -Value $Name
Set-ItemProperty -Path $k -Name 'LocalizedString' -Value "@$ChromePath,-1"
New-Item -Path "$k\DefaultIcon" -Force | Out-Null
Set-ItemProperty -Path "$k\DefaultIcon" -Name '(default)' -Value $IconSpec
New-Item -Path "$k\shell\open\command" -Force | Out-Null
Set-ItemProperty -Path "$k\shell\open\command" -Name '(default)' -Value $plainCmd
New-Item -Path "$k\Capabilities" -Force | Out-Null
Set-ItemProperty -Path "$k\Capabilities" -Name 'ApplicationName' -Value $Name
Set-ItemProperty -Path "$k\Capabilities" -Name 'ApplicationDescription' -Value "$Name - Chromium $($ver.ProductVersion)"
Set-ItemProperty -Path "$k\Capabilities" -Name 'ApplicationIcon' -Value $IconSpec
New-Item -Path "$k\Capabilities\URLAssociations" -Force | Out-Null
Set-ItemProperty -Path "$k\Capabilities\URLAssociations" -Name 'http'  -Value $urlProgId
Set-ItemProperty -Path "$k\Capabilities\URLAssociations" -Name 'https' -Value $urlProgId
New-Item -Path "$k\Capabilities\FileAssociations" -Force | Out-Null
foreach ($ext in '.html', '.htm') {
    Set-ItemProperty -Path "$k\Capabilities\FileAssociations" -Name $ext -Value $htmlProgId
}
Write-Ok "OK"

# ---- 6. 开始菜单快捷方式
Write-Step "创建开始菜单快捷方式"
$lnkDir = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs"
$lnkPath = Join-Path $lnkDir "$Name.lnk"
try {
    $wsh = New-Object -ComObject WScript.Shell
    $sc = $wsh.CreateShortcut($lnkPath)
    $sc.TargetPath = $ChromePath
    if ($UserDataDir) { $sc.Arguments = "--user-data-dir=`"$UserDataDir`"" }
    $sc.WorkingDirectory = $exeDir
    $sc.IconLocation = $IconSpec
    $sc.Description = "$Name - Chromium $($ver.ProductVersion)"
    $sc.Save()
    Write-Ok "OK（$lnkPath）"
} catch {
    Write-Warn2 "创建失败: $($_.Exception.Message)"
}

# ---------------------------------------------------------------- Chrome++ 兜底

# 点「设为默认浏览器」时 Chromium 会重写自己的 ProgId command，
# 把 --user-data-dir 去掉，导致点链接时 chrome.exe 无参数启动、
# 落到 Chrome++ 的默认 data_dir（D:\KZBuild\Data），另开一个浏览器。
# 抢注册表抢不赢（每点一次被覆盖一次），改 Chrome++ 的 data_dir：
# 无参数启动一律注入主控 profile；管理器开的实例带显式
# --user-data-dir，不受影响。这是抗覆盖的根治点。
Write-Step "同步 Chrome++ data_dir → 主控 profile"
if ($UserDataDir -and $ChromePath) {
    $ini = Join-Path (Split-Path $ChromePath -Parent) 'chrome++.ini'
    if (Test-Path $ini) {
        $txt = [IO.File]::ReadAllText($ini, [Text.Encoding]::Unicode)
        $want = 'data_dir="' + $UserDataDir + '"'
        if ($txt -match '(?m)^data_dir=.*$') {
            $txt = [regex]::Replace($txt, '(?m)^data_dir=.*$', $want)
        } else {
            $txt = $txt.Replace('[general]', "[general]`r`n$want")
        }
        [IO.File]::WriteAllText($ini, $txt, (New-Object Text.UnicodeEncoding($false, $true)))
        Write-Ok "data_dir → $UserDataDir"
    } else { Write-Warn2 "chrome++.ini 不存在，跳过" }
}

# ---------------------------------------------------------------- 去重

# Chromium SDK 会往 RegisteredApplications 写一条 Chromium.5C4O...，
# 它和我们的 KZ Browser 指向同一个 chrome.exe，列表里会出现两个一样的
# 紫色 Chromium 把我们挤混。摘掉这条（保留 ProgId，UserChoice 不受影响）。
Write-Step "移除 SDK 重复的 Chromium 应用项"
Get-ItemProperty 'HKCU:\Software\RegisteredApplications' |
    ForEach-Object { $_.PSObject.Properties } |
    Where-Object { $_.Name -like 'Chromium.*' -and $_.Value -like '*StartMenuInternet*' } |
    ForEach-Object {
        Remove-ItemProperty -Path 'HKCU:\Software\RegisteredApplications' -Name $_.Name -Force
        Write-Ok "已移除 $($_.Name)"
    }

# ---------------------------------------------------------------- SDK 项兜底

# Chromium SDK 自己会注册 Chromium.5C4O...\Capabilities，默认应用列表里
# 那两个「Chromium」就是它。它们的 ProgId command 不带 --user-data-dir，
# 用户若选了它，点链接会用默认 profile 而不是主控。把它的 command 也
# 去掉显式 data dir，让 Chrome++ 的 data_dir 决定落点（跟随前台实例）。
Write-Step "同步 SDK Chromium 项的启动参数"
if ($UserDataDir) {
    $sdkCmd = '"' + $ChromePath + '" -- "%1"'
    Get-ChildItem 'HKCU:\Software\Classes' -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -like 'ChromiumHTM.*' } | ForEach-Object {
            $cmdKey = Join-Path $_.PSPath 'shell\open\command'
            if (Test-Path $cmdKey) {
                Set-ItemProperty -Path $cmdKey -Name '(default)' -Value $sdkCmd
                Write-Ok "已同步 $($_.PSChildName)"
            }
        }
}

# ---------------------------------------------------------------- 已有快捷方式

# 不修改任何已有 .lnk。所有实例快捷方式都由管理器按各自 profile 生成，
# 早期版本曾在这里把带 --user-data-dir 的实例快捷方式误改成默认浏览器，
# 造成用户实例配置丢失，因此整段移除。
Write-Step "已有快捷方式由管理器维护（跳过）"

# ---------------------------------------------------------------- 实例图标

# 多开实例的快捷方式图标由管理器按各自 profile.json 的 icon 字段维护
# （「更多」→「重刷所有快捷方式图标」）。注册脚本只处理默认浏览器自己的
# 开始菜单项，不碰 --launch 快捷方式，避免把任务栏图标刷成同一个。
Write-Step "实例快捷方式图标由管理器维护（此处跳过）"

# ---------------------------------------------------------------- 完成

Write-Host "`n注册完成。`n" -ForegroundColor White
Write-Host "接下来手动设成默认（Windows 10/11 强制要求用户确认）：`n" -ForegroundColor White
Write-Host ('  设置 → 应用 → 默认应用 → 搜索 "' + $Name + '" → 设为默认') -ForegroundColor Gray
Write-Host "或者直接打开这个页面：`n" -ForegroundColor White
Write-Host "  ms-settings:defaultapps`n" -ForegroundColor Gray
Write-Host "想撤销：`n" -ForegroundColor White
Write-Host ('  .\register-browser.ps1 -Name "' + $Name + '" -Unregister') -ForegroundColor Gray
