# ==============================================================================
#  Remote Harness MCP - node agent as a machine service (administrator variant)
# ==============================================================================
#
#  Registered by "install.ps1 -AdminService" as the scheduled task
#  RemoteHarnessMCPService: trigger AtStartup, principal SYSTEM, run level
#  Highest. Task Scheduler is the service host here because a Python process is
#  not a service binary; this script is what the task actually starts.
#
#  It is deliberately dumb: read the machine configuration the installer wrote,
#  run the agent, and restart it if it exits. Restart-on-failure is also set on
#  the task itself, so a crash loops back here instead of leaving a dead node.
#
#  ASCII only: Windows PowerShell 5.1 would otherwise decode an unadorned copy
#  with the ANSI code page.
# ==============================================================================

$ErrorActionPreference = "Continue"

$PayloadDir = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$ConfigDir = if ($env:MESH_CONFIG_DIR) { $env:MESH_CONFIG_DIR } else { Join-Path (Join-Path $env:ProgramData "remote-harness-mcp") "" }
$ConfigDir = $ConfigDir.TrimEnd("\")

# The installer pins the interpreter it proved usable; searching PATH here would
# find the Microsoft Store alias under SYSTEM, which is the failure this whole
# project keeps guarding against.
$Python = ""
$ServiceEnv = Join-Path $ConfigDir "service.env"
if (Test-Path -LiteralPath $ServiceEnv) {
    foreach ($line in (Get-Content -LiteralPath $ServiceEnv -ErrorAction SilentlyContinue)) {
        $trimmed = "$line".Trim()
        if ($trimmed -match '^MESH_PYTHON=(.+)$') { $Python = $Matches[1].Trim() }
        if ($trimmed -match '^MESH_CONFIG_DIR=(.+)$') { $ConfigDir = $Matches[1].Trim() }
    }
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    $Python = "python.exe"
}

# The node resolves its own configuration through MESH_CONFIG_DIR (core/paths.py),
# so agent.env, domain.env and the account DB all live in the machine directory
# instead of the SYSTEM profile.
$env:MESH_CONFIG_DIR = $ConfigDir
$env:MESH_CACHE_DIR = Join-Path $ConfigDir "cache"
if (-not (Test-Path -LiteralPath $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
}

$LogFile = Join-Path $ConfigDir "agent.log"
Set-Location -LiteralPath $PayloadDir

# One marker per start makes a restart loop visible in the log instead of looking
# like a single long session.
while ($true) {
    $stamp = (Get-Date).ToString("s")
    "=== $stamp starting core.agent as $([Security.Principal.WindowsIdentity]::GetCurrent().Name) (pid $PID) ===" |
        Out-File -FilePath $LogFile -Append -Encoding utf8
    try {
        & $Python -u -m core.agent *>&1 | Out-File -FilePath $LogFile -Append -Encoding utf8
    } catch {
        "=== agent runner error: $($_.Exception.Message) ===" | Out-File -FilePath $LogFile -Append -Encoding utf8
    }
    Start-Sleep -Seconds 5
}
