# Remote Harness MCP tray (Windows)
#
# A small, dependency-free NotifyIcon front-end for the node.  It flips the same
# marker files the agent checks on every call (core/control.py):
#   agent.paused   - refuse every tool call until resumed
#   readonly.flag  - observation only, live, without restarting the service
#
# The tray never talks to the network and never reads the token.  It is safe to
# run as the logged-in user; it needs no administrator rights.
#
# Usage:
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\windows\rhm-tray.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File ops\windows\rhm-tray.ps1 -SelfTest

param(
    [switch]$SelfTest,
    [switch]$InstallAutostart,
    [switch]$RemoveAutostart
)

$ErrorActionPreference = 'Stop'

function Get-ConfigDir {
    if ($env:MESH_CONFIG_DIR) { return $env:MESH_CONFIG_DIR }
    return (Join-Path $env:USERPROFILE '.config\remote-harness-mcp')
}

$ConfigDir = Get-ConfigDir
$PausedFile = Join-Path $ConfigDir 'agent.paused'
$ReadOnlyFile = Join-Path $ConfigDir 'readonly.flag'
$HeartbeatFile = Join-Path $ConfigDir 'agent.heartbeat'
$UsageFile = Join-Path $ConfigDir 'usage.jsonl'
$RunKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$RunValue = 'RemoteHarnessMCPTray'

function Get-NodeState {
    $online = $false
    $age = $null
    if (Test-Path $HeartbeatFile) {
        try {
            $age = [int]((Get-Date) - (Get-Item $HeartbeatFile).LastWriteTime).TotalSeconds
            if ($age -lt 0) { $age = 0 }
            $online = $age -le 60
        } catch { }
    }
    [pscustomobject]@{
        Online = $online
        HeartbeatAge = $age
        Paused = (Test-Path $PausedFile)
        ReadOnly = (Test-Path $ReadOnlyFile)
    }
}

function Set-Paused([bool]$Value) {
    if ($Value) {
        New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
        Set-Content -Path $PausedFile -Value 'paused' -Encoding ASCII
    } elseif (Test-Path $PausedFile) {
        Remove-Item -Path $PausedFile -Force
    }
}

function Set-ReadOnly([bool]$Value) {
    if ($Value) {
        New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
        Set-Content -Path $ReadOnlyFile -Value 'read-only' -Encoding ASCII
    } elseif (Test-Path $ReadOnlyFile) {
        Remove-Item -Path $ReadOnlyFile -Force
    }
}

function Test-Autostart {
    try {
        $value = (Get-ItemProperty -Path $RunKey -Name $RunValue -ErrorAction Stop).$RunValue
        return [bool]$value
    } catch { return $false }
}

function Set-Autostart([bool]$Value) {
    if ($Value) {
        $command = 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
        New-Item -Path $RunKey -Force | Out-Null
        Set-ItemProperty -Path $RunKey -Name $RunValue -Value $command
    } else {
        try { Remove-ItemProperty -Path $RunKey -Name $RunValue -ErrorAction Stop } catch { }
    }
}

if ($InstallAutostart) {
    Set-Autostart $true
    Write-Output 'autostart=installed'
    exit 0
}
if ($RemoveAutostart) {
    Set-Autostart $false
    Write-Output 'autostart=removed'
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

if ($SelfTest) {
    $state = Get-NodeState
    $items = @('status', 'pause', 'readonly', 'usage', 'config', 'autostart', 'restart', 'exit')
    $result = [ordered]@{
        ok = $true
        items = $items
        config_dir = $ConfigDir
        online = $state.Online
        paused = $state.Paused
        read_only = $state.ReadOnly
        autostart = (Test-Autostart)
    }
    $result | ConvertTo-Json -Compress
    exit 0
}

$mutex = New-Object System.Threading.Mutex($false, 'Local\RemoteHarnessMCPTray')
if (-not $mutex.WaitOne(0)) {
    Write-Output 'tray already running'
    exit 0
}

[System.Windows.Forms.Application]::EnableVisualStyles()

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Shield
$notify.Visible = $true
$notify.Text = 'Remote Harness MCP'

$menu = New-Object System.Windows.Forms.ContextMenuStrip
$statusItem = New-Object System.Windows.Forms.ToolStripMenuItem
$statusItem.Enabled = $false
$menu.Items.Add($statusItem) | Out-Null
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$pauseItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Pause commands'
$pauseItem.CheckOnClick = $false
$menu.Items.Add($pauseItem) | Out-Null

$readOnlyItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Read-only mode'
$readOnlyItem.CheckOnClick = $false
$menu.Items.Add($readOnlyItem) | Out-Null

$usageItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Usage log'
$menu.Items.Add($usageItem) | Out-Null
$configItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Open config folder'
$menu.Items.Add($configItem) | Out-Null
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null

$autostartItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Start with Windows'
$autostartItem.CheckOnClick = $false
$menu.Items.Add($autostartItem) | Out-Null
$restartItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Restart node'
$menu.Items.Add($restartItem) | Out-Null
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
$exitItem = New-Object System.Windows.Forms.ToolStripMenuItem 'Exit'
$menu.Items.Add($exitItem) | Out-Null

$notify.ContextMenuStrip = $menu

function Refresh-Tray {
    $state = Get-NodeState
    if ($state.Online) {
        $notify.Text = 'Remote Harness MCP - online'
        $statusItem.Text = 'Online'
    } else {
        $notify.Text = 'Remote Harness MCP - offline'
        $statusItem.Text = 'Offline'
    }
    if ($state.Paused) { $pauseItem.Text = 'Resume commands' } else { $pauseItem.Text = 'Pause commands' }
    $pauseItem.Checked = $state.Paused
    $readOnlyItem.Checked = $state.ReadOnly
    $autostartItem.Checked = (Test-Autostart)
}

$pauseItem.Add_Click({
    $state = Get-NodeState
    Set-Paused (-not $state.Paused)
    Refresh-Tray
})

$readOnlyItem.Add_Click({
    $state = Get-NodeState
    Set-ReadOnly (-not $state.ReadOnly)
    Refresh-Tray
})

$usageItem.Add_Click({
    if (Test-Path $UsageFile) {
        Start-Process -FilePath 'notepad.exe' -ArgumentList ('"' + $UsageFile + '"')
    } else {
        [System.Windows.Forms.MessageBox]::Show('No usage log yet.', 'Remote Harness MCP')
    }
})

$configItem.Add_Click({
    if (Test-Path $ConfigDir) {
        Start-Process -FilePath 'explorer.exe' -ArgumentList ('"' + $ConfigDir + '"')
    }
})

$autostartItem.Add_Click({
    Set-Autostart (-not (Test-Autostart))
    Refresh-Tray
})

$restartItem.Add_Click({
    $watchdog = Join-Path $PSScriptRoot 'agent-watchdog.ps1'
    if (Test-Path $watchdog) {
        Start-Process -FilePath 'powershell.exe' -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $watchdog + '"'), '-Quiet'
        ) -WindowStyle Hidden
    } else {
        [System.Windows.Forms.MessageBox]::Show('agent-watchdog.ps1 was not found next to the tray.', 'Remote Harness MCP')
    }
})

$exitItem.Add_Click({
    $notify.Visible = $false
    $form.Close()
})

$notify.Add_DoubleClick({ Start-Process -FilePath 'notepad.exe' -ArgumentList ('"' + $UsageFile + '"') })

$form = New-Object System.Windows.Forms.Form
$form.Text = 'Remote Harness MCP'
$form.ShowInTaskbar = $false
$form.WindowState = 'Minimized'
$form.FormBorderStyle = 'None'
$form.Opacity = 0
$form.Size = New-Object System.Drawing.Size(1, 1)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({ Refresh-Tray })
$timer.Start()

Refresh-Tray
[System.Windows.Forms.Application]::Run($form)
$notify.Visible = $false
$notify.Dispose()
$mutex.ReleaseMutex()
