<#
.SYNOPSIS
    把本机 KZ 多开浏览器的全部实例导出到可迁移目录。

.EXAMPLE
    .\export-migration.ps1 -Destination "E:\KZ-Migration"
#>
[CmdletBinding()]
param(
    [string]$Destination = ".\KZ-Migration",
    [string]$ProfilesRoot = "D:\KZProfiles",
    [string]$AppRoot = "D:\KZBuild\App",
    [switch]$IncludeCache
)

$ErrorActionPreference = 'Stop'
$PSDefaultParameterValues['*:Encoding'] = 'utf8'

function W($m) { Write-Host "  $m" -ForegroundColor Cyan }
function OK($m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !   $m" -ForegroundColor Yellow }

# --- 1. 停掉所有相关进程，保证文件一致 ---
W "停止管理器与浏览器进程"
Get-Process KZManager, chrome -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 3

$dest = [IO.Path]::GetFullPath($Destination)
if (Test-Path $dest) { throw "目标已存在：$dest （避免覆盖，请换一个目录或先删除）" }
New-Item -ItemType Directory -Force $dest | Out-Null
New-Item -ItemType Directory -Force (Join-Path $dest 'App') | Out-Null
New-Item -ItemType Directory -Force (Join-Path $dest 'Profiles') | Out-Null

# --- 2. 拷贝内核 App（排除日志/备份/安装包） ---
W "拷贝内核 $AppRoot"
$appExclude = @('*.log','*.bak','*.bak-*','mini_installer.exe','debug.log','chrome++.ini.bak*')
Get-ChildItem $AppRoot -Force | ForEach-Object {
    $skip = $false
    foreach ($pat in $appExclude) { if ($_.Name -like $pat) { $skip = $true; break } }
    if ($skip) { Warn "跳过 $($_.Name)"; return }
    $to = Join-Path (Join-Path $dest 'App') $_.Name
    if ($_.PSIsContainer) { Copy-Item $_.FullName $to -Recurse -Force }
    else { Copy-Item $_.FullName $to -Force }
}
OK "内核已拷贝"

# --- 3. 拷贝 Profiles，逐实例排除缓存 ---
# 缓存目录白名单（相对每个 Data\ 下，含各层 profile 目录）
$cacheNames = @(
    'Cache','Code Cache','GPUCache','ShaderCache','GrShaderCache',
    'DawnCache','DawnGraphiteCache','Service Worker','Crashpad',
    'SingletonLock','SingletonSocket','SingletonCookie','*.tmp','*.log',
    # 以下为浏览器启动后会自动重新下载的组件/模型，删掉不影响任何用户数据
    'component_crx_cache','extensions_crx_cache','SODALanguagePacks','SODA',
    'screen_ai','optimization_guide_model_store','WasmTtsEngine',
    'CertificateRevocation','Safe Browsing','DeferredBrowserMetrics',
    'BrowserMetrics','First Run','Variations','segmentation_platform',
    'Trust Protection Lists','Subresource Filter','OptimizationHints',
    'AutofillStates','pnacl','PepperFlash','WidevineCdm','hyphen-data'
)
$dirsToSkip = @('_backup','_snapshots','_ddtest','__pycache__')

W "拷贝实例数据 $ProfilesRoot"
$instanceNames = @()
Get-ChildItem $ProfilesRoot -Force | ForEach-Object {
    $item = $_
    if ($item.PSIsContainer) {
        if ($dirsToSkip -contains $item.Name) { Warn "跳过 $($item.Name)"; return }
        if ($item.Name -eq 'icons') {
            Copy-Item $item.FullName (Join-Path (Join-Path $dest 'Profiles') 'icons') -Recurse -Force
            return
        }
        # 实例目录：拷 profile.json 等散文件 + Data（排除缓存）
        $instanceNames += $item.Name
        $target = Join-Path (Join-Path $dest 'Profiles') $item.Name
        New-Item -ItemType Directory -Force $target | Out-Null
        Get-ChildItem $item.FullName -Force | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
            Copy-Item $_.FullName (Join-Path $target $_.Name) -Force
        }
        $dataSrc = Join-Path $item.FullName 'Data'
        if (Test-Path $dataSrc) {
            $dataDst = Join-Path $target 'Data'
            New-Item -ItemType Directory -Force $dataDst | Out-Null
            if ($IncludeCache) {
                Copy-Item $dataSrc\* $dataDst -Recurse -Force
            } else {
                # 用 robocopy 排除缓存目录（更可靠，保留其他所有文件）
                $xd = @()
                foreach ($c in $cacheNames) { if ($c -notlike '*.*') { $xd += $c } }
                $rcArgs = @($dataSrc, $dataDst, '/E', '/NFL', '/NDL', '/NJH', '/NJS', '/NP', '/R:1', '/W:1')
                if ($xd.Count -gt 0) { $rcArgs += '/XD'; $rcArgs += $xd }
                & robocopy @rcArgs | Out-Null
                if ($LASTEXITCODE -ge 8) { throw "robocopy 失败 code=$LASTEXITCODE" }
            }
            OK "实例 $($item.Name) Data 已导出"
        }
    } else {
        # 根目录下的脚本/配置（排除日志、快照）
        if ($item.Name -match '\.(log|db)$' -or $item.Name -eq 'autosync.log') { return }
        Copy-Item $item.FullName (Join-Path (Join-Path $dest 'Profiles') $item.Name) -Force
    }
}
OK "配置文件与脚本已拷贝"

# --- 3.5 收集快捷方式 ---
W "收集快捷方式"
$scDir = Join-Path $dest 'Shortcuts'
New-Item -ItemType Directory -Force $scDir | Out-Null
$lnkSources = [ordered]@{
    'Desktop'    = [Environment]::GetFolderPath('DesktopDirectory')
    'StartMenu'  = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
    'TaskBar'    = (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar')
}
$scCount = 0
foreach ($kv in $lnkSources.GetEnumerator()) {
    $bucket = Join-Path $scDir $kv.Key
    if (-not (Test-Path $kv.Value)) { continue }
    Get-ChildItem $kv.Value -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        $sc = $wsh = New-Object -ComObject WScript.Shell
        $lnk = $wsh.CreateShortcut($_.FullName)
        $blob = "$($lnk.TargetPath)`n$($lnk.Arguments)`n$($lnk.IconLocation)`n$($lnk.WorkingDirectory)`n$($lnk.Description)"
        # 只收指向我们内核/管理器的
        if ($blob -match 'KZBuild|KZProfiles|KZManager') {
            $sub = Join-Path $bucket ($_.DirectoryName.Substring($kv.Value.Length).TrimStart('\'))
            New-Item -ItemType Directory -Force $sub | Out-Null
            Copy-Item $_.FullName (Join-Path $sub $_.Name) -Force
            $scCount++
        }
    }
}
OK "已收集 $scCount 个快捷方式"

# --- 4. manifest ---
$manifest = [ordered]@{
    exportedAt    = (Get-Date).ToString('o')
    sourceMachine = $env:COMPUTERNAME
    sourcePaths   = @{ profiles = $ProfilesRoot; app = $AppRoot }
    instances     = $instanceNames
    includeCache  = [bool]$IncludeCache
    shortcuts     = $scCount
    appFiles      = @(Get-ChildItem (Join-Path $dest 'App') -File | ForEach-Object Name)
    notes         = @(
        '登录态(Cookies/密码)用 Windows DPAPI 加密，跨机器会失效，需要重新登录',
        '导入时会自动改 manager.json 的 chromeExe 与 chrome++.ini 的 data_dir',
        'google-oauth.json / nodes.json / .sync-token.json 含凭据，请安全保管'
    )
}
$manifest | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $dest 'manifest.json') -Encoding UTF8

$size = (Get-ChildItem $dest -Recurse -File | Measure-Object Length -Sum).Sum
OK "导出完成"
Write-Host ""
Write-Host "  目录 : $dest" -ForegroundColor White
Write-Host "  大小 : $([math]::Round($size/1GB,2)) GB" -ForegroundColor White
Write-Host "  实例 : $($instanceNames.Count) 个" -ForegroundColor White
Write-Host ""
Write-Host "把整个目录拷到新电脑，然后在那边运行 import-migration.ps1" -ForegroundColor Yellow
