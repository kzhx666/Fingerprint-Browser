# 账号同步面板：登录后勾选要同步的数据，点一下同步
# 依赖: D:\KZProfiles\account-sync.py（客户端）和 sync-server.py（服务器）
param(
    [string]$Root = "D:\KZProfiles",
    [string]$Server = "http://127.0.0.1:8787"
)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$script:Root = $Root
$script:Client = Join-Path $Root 'account-sync.py'
$script:CfgPath = Join-Path $Root 'account-sync.json'

function Load-Cfg {
    if (Test-Path $script:CfgPath) {
        try { return Get-Content $script:CfgPath -Raw | ConvertFrom-Json } catch { }
    }
    return [pscustomobject]@{ server = $Server; user = ''; types = 'bookmarks,history,passwords'; instance = '' }
}

function Save-Cfg($cfg) {
    $cfg | ConvertTo-Json -Depth 5 | Set-Content $script:CfgPath -Encoding UTF8
}

function Get-Instances {
    Get-ChildItem $script:Root -Directory -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName 'Data') } |
        Select-Object -ExpandProperty Name
}

$cfg = Load-Cfg

$form = New-Object System.Windows.Forms.Form
$form.Text = '账号同步'
$form.Size = New-Object System.Drawing.Size(560, 520)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

function Add-Label($text, $x, $y) {
    $l = New-Object System.Windows.Forms.Label
    $l.Text = $text; $l.Location = New-Object System.Drawing.Point($x, $y)
    $l.Size = New-Object System.Drawing.Size(110, 22)
    $form.Controls.Add($l); return $l
}
function Add-Text($text, $x, $y, $w) {
    $t = New-Object System.Windows.Forms.TextBox
    $t.Text = $text; $t.Location = New-Object System.Drawing.Point($x, $y)
    $t.Size = New-Object System.Drawing.Size($w, 22)
    $form.Controls.Add($t); return $t
}

Add-Label '服务器地址' 15 18 | Out-Null
$txtServer = Add-Text $cfg.server 130 16 400
Add-Label '账号' 15 50 | Out-Null
$txtUser = Add-Text $cfg.user 130 48 180
Add-Label '密码' 15 82 | Out-Null
$txtPass = Add-Text '' 130 80 180
$txtPass.UseSystemPasswordChar = $true

Add-Label '实例' 15 114 | Out-Null
$cmbInst = New-Object System.Windows.Forms.ComboBox
$cmbInst.Location = New-Object System.Drawing.Point(130, 112)
$cmbInst.Size = New-Object System.Drawing.Size(180, 22)
$cmbInst.DropDownStyle = 'DropDownList'
foreach ($i in Get-Instances) { [void]$cmbInst.Items.Add($i) }
if ($cfg.instance -and $cmbInst.Items.Contains($cfg.instance)) { $cmbInst.SelectedItem = $cfg.instance }
elseif ($cmbInst.Items.Count -gt 0) { $cmbInst.SelectedIndex = 0 }
$form.Controls.Add($cmbInst)

$gb = New-Object System.Windows.Forms.GroupBox
$gb.Text = '要同步的数据'
$gb.Location = New-Object System.Drawing.Point(15, 148)
$gb.Size = New-Object System.Drawing.Size(515, 62)
$form.Controls.Add($gb)
$chkBookmarks = New-Object System.Windows.Forms.CheckBox
$chkBookmarks.Text = '书签'; $chkBookmarks.Location = New-Object System.Drawing.Point(15, 26)
$chkBookmarks.Size = New-Object System.Drawing.Size(80, 22)
$chkHistory = New-Object System.Windows.Forms.CheckBox
$chkHistory.Text = '历史记录'; $chkHistory.Location = New-Object System.Drawing.Point(110, 26)
$chkHistory.Size = New-Object System.Drawing.Size(100, 22)
$chkPasswords = New-Object System.Windows.Forms.CheckBox
$chkPasswords.Text = '密码'; $chkPasswords.Location = New-Object System.Drawing.Point(225, 26)
$chkPasswords.Size = New-Object System.Drawing.Size(80, 22)
$chkExtensions = New-Object System.Windows.Forms.CheckBox
$chkExtensions.Text = '插件（暂不支持）'; $chkExtensions.Location = New-Object System.Drawing.Point(320, 26)
$chkExtensions.Size = New-Object System.Drawing.Size(150, 22)
$chkExtensions.Enabled = $false
foreach ($c in @($chkBookmarks, $chkHistory, $chkPasswords, $chkExtensions)) { $gb.Controls.Add($c) }
$sel = ($cfg.types -split ',')
$chkBookmarks.Checked = $sel -contains 'bookmarks'
$chkHistory.Checked = $sel -contains 'history'
$chkPasswords.Checked = $sel -contains 'passwords'

$chkAuto = New-Object System.Windows.Forms.CheckBox
$chkAuto.Text = '打开面板时自动同步一次'
$chkAuto.Location = New-Object System.Drawing.Point(18, 222)
$chkAuto.Size = New-Object System.Drawing.Size(240, 22)
$form.Controls.Add($chkAuto)

$btnRegister = New-Object System.Windows.Forms.Button
$btnRegister.Text = '注册账号'; $btnRegister.Location = New-Object System.Drawing.Point(15, 254)
$btnRegister.Size = New-Object System.Drawing.Size(100, 34)
$btnLogin = New-Object System.Windows.Forms.Button
$btnLogin.Text = '登录并同步'; $btnLogin.Location = New-Object System.Drawing.Point(125, 254)
$btnLogin.Size = New-Object System.Drawing.Size(120, 34)
$btnSync = New-Object System.Windows.Forms.Button
$btnSync.Text = '立即同步'; $btnSync.Location = New-Object System.Drawing.Point(255, 254)
$btnSync.Size = New-Object System.Drawing.Size(100, 34)
$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text = '关闭'; $btnClose.Location = New-Object System.Drawing.Point(430, 254)
$btnClose.Size = New-Object System.Drawing.Size(100, 34)
foreach ($b in @($btnRegister, $btnLogin, $btnSync, $btnClose)) { $form.Controls.Add($b) }

$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true
$log.ScrollBars = 'Vertical'
$log.Location = New-Object System.Drawing.Point(15, 300)
$log.Size = New-Object System.Drawing.Size(515, 160)
$log.ReadOnly = $true
$log.Font = New-Object System.Drawing.Font('Consolas', 9)
$form.Controls.Add($log)

$status = New-Object System.Windows.Forms.Label
$status.Location = New-Object System.Drawing.Point(15, 465)
$status.Size = New-Object System.Drawing.Size(515, 20)
$status.Text = '就绪'
$form.Controls.Add($status)

function Write-Log($msg) {
    $log.AppendText(("[" + (Get-Date -Format 'HH:mm:ss') + "] " + $msg + "`r`n"))
    $status.Text = $msg
}

function Get-Types {
    $t = @()
    if ($chkBookmarks.Checked) { $t += 'bookmarks' }
    if ($chkHistory.Checked) { $t += 'history' }
    if ($chkPasswords.Checked) { $t += 'passwords' }
    return ($t -join ',')
}

function Run-Sync([bool]$register) {
    if (-not $cmbInst.SelectedItem) { Write-Log '没有可用实例'; return }
    $types = Get-Types
    if (-not $types) { Write-Log '至少选一种数据'; return }

    $cfg = [pscustomobject]@{
        server = $txtServer.Text.Trim(); user = $txtUser.Text.Trim()
        types = $types; instance = $cmbInst.SelectedItem
    }
    Save-Cfg $cfg

    $prof = Join-Path (Join-Path (Join-Path $script:Root $cmbInst.SelectedItem) 'Data') 'Default'
    $args = @($script:Client, '--profile', $prof, '--server', $cfg.server,
              '--user', $cfg.user, '--pass', $txtPass.Text, '--types', $types, '--json')
    if ($register) { $args += '--register' }

    $btnSync.Enabled = $false; $btnLogin.Enabled = $false
    try {
        Write-Log ("开始" + $(if ($register) { '（含注册）' } else { '' }) + "…")
        $out = & python @args 2>&1
        $text = ($out | Out-String).Trim()
        try {
            $r = $text | ConvertFrom-Json
            foreach ($s in $r.steps) { Write-Log $s }
            if ($r.ok) { Write-Log '✅ 同步完成' } else { Write-Log ("❌ 失败: " + $r.error) }
        } catch {
            Write-Log $text
        }
    } finally {
        $btnSync.Enabled = $true; $btnLogin.Enabled = $true
    }
}

$btnRegister.Add_Click({ Run-Sync $true })
$btnLogin.Add_Click({ Run-Sync $false })
$btnSync.Add_Click({ Run-Sync $false })
$btnClose.Add_Click({ $form.Close() })

$form.Add_Shown({
    if ($chkAuto.Checked -and $txtUser.Text -and $txtPass.Text) { Run-Sync $false }
})

[void]$form.ShowDialog()
