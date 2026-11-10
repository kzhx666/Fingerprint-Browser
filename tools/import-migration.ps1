<#
.SYNOPSIS
    在新电脑上恢复 KZ 多开浏览器。

.EXAMPLE
    .\import-migration.ps1 -Source "E:\KZ-Migration" -TargetRoot "D:"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Source,
    [string]$TargetRoot = "D:",
    [switch]$RegisterDefault,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
function W($m) { Write-Host "  $m" -ForegroundColor Cyan }
function OK($m) { Write-Host "  OK  $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !   $m" -ForegroundColor Yellow }

$src = [IO.Path]::GetFullPath($Source)
$man = Join-Path $src 'manifest.json'
if (-not (Test-Path $man)) { throw "找不到 manifest.json，目录不完整：$src" }
$m = Get-Content $man -Raw -Encoding UTF8 | ConvertFrom-Json
W "导出自 $($m.sourceMachine) 于 $($m.exportedAt)，共 $($m.instances.Count) 个实例"

# 防呆：在导出它的原机器上跑导入会覆盖源数据 + 改坏本机快捷方式
if (-not $Force -and $m.sourceMachine -eq $env:COMPUTERNAME) {
    throw "这个包是从本机 ($($m.sourceMachine)) 导出的。导入脚本应在【新电脑】上运行。`n" +
          "如果确实要在原机做恢复演练，加 -Force（并确保 -TargetRoot 指向不同目录）。"
}

Get-Process KZManager, chrome -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

$profiles = Join-Path $TargetRoot 'KZProfiles'
$app      = Join-Path $TargetRoot 'KZBuild\App'
New-Item -ItemType Directory -Force $profiles | Out-Null
New-Item -ItemType Directory -Force $app | Out-Null

W "释放内核 → $app"
Copy-Item (Join-Path $src 'App\*') $app -Recurse -Force
W "释放实例 → $profiles"
Copy-Item (Join-Path $src 'Profiles\*') $profiles -Recurse -Force
OK "文件已释放"

# --- 改路径 ---
W "适配新路径"
$cfgPath = Join-Path $profiles 'manager.json'
if (Test-Path $cfgPath) {
    $cfg = Get-Content $cfgPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $newExe = Join-Path $app 'chrome.exe'
    $cfg.chromeExe = $newExe
    $cfg | ConvertTo-Json -Depth 6 | Set-Content $cfgPath -Encoding UTF8
    OK "manager.json chromeExe → $newExe"
}

$ini = Join-Path $app 'chrome++.ini'
if (Test-Path $ini) {
    $txt = [IO.File]::ReadAllText($ini, [Text.Encoding]::Unicode)
    # data_dir 指向主控（静态兜底；智能默认浏览器服务会动态改写）
    $master = Join-Path $profiles '主控浏览器\Data'
    $want = 'data_dir="' + $master + '"'
    if ($txt -match '(?m)^data_dir=.*$') {
        $txt = [regex]::Replace($txt, '(?m)^data_dir=.*$', $want)
    } else {
        $txt = $txt.Replace('[general]', "[general]`r`n$want")
    }
    [IO.File]::WriteAllText($ini, $txt, (New-Object Text.UnicodeEncoding($false,$true)))
    OK "chrome++.ini data_dir → $master"
}

# --- 恢复快捷方式（从导出包 Shortcuts\ 释放，再改路径） ---
W "恢复快捷方式"
$oldApp  = $m.sourcePaths.app
$newApp  = $app
$oldProf = $m.sourcePaths.profiles
$newProf = $profiles
$scSrc = Join-Path $src 'Shortcuts'
$scMap = [ordered]@{
    'Desktop'   = [Environment]::GetFolderPath('DesktopDirectory')
    'StartMenu' = (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs')
    'TaskBar'   = (Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar')
}
$wsh = New-Object -ComObject WScript.Shell
$fixed = 0
foreach ($kv in $scMap.GetEnumerator()) {
    $bucket = Join-Path $scSrc $kv.Key
    if (-not (Test-Path $bucket)) { continue }
    Get-ChildItem $bucket -Filter '*.lnk' -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        $rel = $_.FullName.Substring($bucket.Length).TrimStart('\')
        $dst = Join-Path $kv.Value $rel
        New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
        Copy-Item $_.FullName $dst -Force
        try {
            $sc = $wsh.CreateShortcut($dst)
            $ch = $false
            if ($sc.TargetPath -like "*$oldApp*")      { $sc.TargetPath = $sc.TargetPath.Replace($oldApp, $newApp); $ch = $true }
            if ($sc.Arguments -like "*$oldProf*")      { $sc.Arguments = $sc.Arguments.Replace($oldProf, $newProf); $ch = $true }
            if ($sc.IconLocation -like "*$oldApp*")    { $sc.IconLocation = $sc.IconLocation.Replace($oldApp, $newApp); $ch = $true }
            if ($sc.IconLocation -like "*$oldProf*")   { $sc.IconLocation = $sc.IconLocation.Replace($oldProf, $newProf); $ch = $true }
            if ($sc.WorkingDirectory -like "*$oldApp*"){ $sc.WorkingDirectory = $sc.WorkingDirectory.Replace($oldApp, $newApp); $ch = $true }
            if ($ch) { $sc.Save() }
            $fixed++
        } catch { Warn "快捷方式改写失败: $dst" }
    }
}
OK "恢复并修正了 $fixed 个快捷方式"


# --- 注册默认浏览器（可选） ---
if ($RegisterDefault) {
    $reg = Join-Path $PSScriptRoot 'register-browser.ps1'
    if (Test-Path $reg) {
        W "注册默认浏览器"
        & $reg -ChromePath (Join-Path $app 'chrome.exe') -Name 'KZ Browser' `
              -UserDataDir (Join-Path $profiles '主控浏览器\Data') `
              -IconPath (Join-Path $app 'KZ Browser.ico')
    } else { Warn "register-browser.ps1 不在同目录，跳过注册" }
}

OK "导入完成"
Write-Host ""
Write-Host "  启动: $profiles\KZManager.exe" -ForegroundColor White
Write-Host ""
Write-Host "  注意:" -ForegroundColor Yellow
Write-Host "   1. 所有账号的登录态(Cookies/密码)会失效，需要重新登录" -ForegroundColor Yellow
Write-Host "      （DPAPI 密钥绑定原机器的用户账户，不能跨机器解密）" -ForegroundColor Yellow
Write-Host "   2. Google 登录需要 google-oauth.json 里的客户端凭据，随包已带" -ForegroundColor Yellow
Write-Host "   3. 默认应用列表需手动设一次（Windows 强制用户确认）" -ForegroundColor Yellow
Write-Host "   4. 任务栏固定项图标可能要重启 explorer 才刷新" -ForegroundColor Yellow
Write-Host "   5. 节点/代理配置在 nodes.json，含凭据，确认无误后删掉导出包" -ForegroundColor Yellow
