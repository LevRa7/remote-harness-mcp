# ==============================================================================
#  Remote Harness MCP - Windows PowerShell Universal Installer
# ==============================================================================
param(
    [switch]$Quick,
    [string]$Mode = "funnel",
    [string]$User = "",
    [string]$Token = "",
    # Empty on purpose: the shared domain is resolved from the one source of truth
    # (see the PUBLIC DOMAIN block below), never from a literal in this file.
    [string]$Gateway = "",
    [int]$Port = 8096,
    [string]$Lang = "en",
    [switch]$DryRun,
    # Publish the standalone node through Tailscale Funnel: a public HTTPS
    # hostname on the tailnet, no server, no domain, no open port.
    [switch]$Funnel,
    # Install the MCP service with administrator rights: a machine-wide scheduled
    # task that starts at boot, runs as SYSTEM and is allowed the highest
    # privileges. The default install stays per-user and elevation-free; this is
    # the opt-in variant that gives tool calls administrator rights.
    [switch]$AdminService,
    # Set by the elevated relaunch of this same script, so the child does not try
    # to elevate again (which would be an endless UAC loop).
    [switch]$Elevated,
    # The elevated child does one thing: register the machine service. It must not
    # repeat the node registration the parent already performed.
    [switch]$ServiceOnly,
    # Where the elevated child writes its transcript, so the unelevated parent can
    # print the child's output: -Verb RunAs cannot redirect stdout.
    [string]$Transcript = ""
)

# Whether the operator pinned the port. A default is a preference: when the port
# already belongs to something else, the installer takes the first free one above
# it instead of failing the bind (and the Funnel target with it).
$PortExplicit = $PSBoundParameters.ContainsKey('Port')

# Funnel is the default variant on Windows too: it needs neither a gateway nor a
# domain, and the node stays on loopback. An explicit -Mode standalone keeps the
# plain local server without publishing it.
if ($Mode -eq "funnel") {
    $Funnel = $true
    $Mode = "standalone"
}

# Ensure UTF-8 output encoding in PowerShell console
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
} catch {}

$ErrorActionPreference = "Continue"

# The administrator variant relaunches itself elevated (see the elevation block
# below), and a process started with -Verb RunAs cannot have its stdout
# redirected. A transcript is the one channel that survives: the parent prints
# this file after the child exits, so the operator sees the real output instead
# of an empty console.
if ($Transcript) {
    try { Start-Transcript -Path $Transcript -Force | Out-Null } catch {}
}

if (Test-Path variable:global:PSNativeCommandUseErrorActionPreference) {
    $global:PSNativeCommandUseErrorActionPreference = $false
}

$Hostname = $env:COMPUTERNAME.ToLower() -replace '[^a-z0-9_-]', ''
$IsLaptop = [bool](Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue)

if ($Lang -eq "ru") {
    $DetectedType = if ($IsLaptop) { "Windows Ноутбук (Laptop)" } else { "Windows Десктоп / Сервер" }
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "| [*] Обнаружено устройство:                                           |" -ForegroundColor Cyan
    Write-Host "|   * Имя хоста : $Hostname" -ForegroundColor Green
    Write-Host "|   * Тип       : $DetectedType" -ForegroundColor Yellow
    Write-Host "|   * ОС        : Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor White
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
} else {
    $DetectedType = if ($IsLaptop) { "Windows Laptop" } else { "Windows Desktop / Server" }
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
    Write-Host "| [*] Detected Device:                                                 |" -ForegroundColor Cyan
    Write-Host "|   * Hostname : $Hostname" -ForegroundColor Green
    Write-Host "|   * Type     : $DetectedType" -ForegroundColor Yellow
    Write-Host "|   * OS       : Windows $([System.Environment]::OSVersion.Version)" -ForegroundColor White
    Write-Host "+----------------------------------------------------------------------+" -ForegroundColor Cyan
}

# The dry run is handled after the (read-only) interpreter detection below: a
# preflight that exits before checking anything cannot honestly report that the
# dependencies were verified.

# Python check & auto-install.
# Windows ships a "python.exe" App Execution Alias under
# %LOCALAPPDATA%\Microsoft\WindowsApps that runs Python only while the Microsoft
# Store package stays healthy: it can be on PATH, print nothing usable and exit
# non-zero, and it stops working silently after a Store repair or an update. A
# node whose autostart pins that alias never comes back after a reboot - the only
# trace is a truncated "Python " line in agent.log - so an alias is never
# accepted here. Every candidate must be a real interpreter outside WindowsApps
# that prints 3 for sys.version_info[0] and whose own sys.executable does not
# resolve back into WindowsApps.
$WindowsAppsMarker = "\WindowsApps\"

# ------------------------------------------------------------------------------
#  ARCHITECTURE - which build of Python this machine actually needs
#
#  This installer used to download python-3.12.5-amd64.exe unconditionally, which
#  is wrong in two measurable ways:
#
#    * on 32-bit Windows the amd64 installer refuses to run at all, so the
#      automatic step failed and the operator was told to install Python by hand;
#    * on ARM64 Windows it installs an x64 build that only works under emulation,
#      which is slower and breaks as soon as a dependency has no emulated wheel.
#
#  RuntimeInformation.OSArchitecture is asked FIRST because it reports the native
#  OS architecture even when this PowerShell is itself emulated. The environment
#  variables only describe the process: a 32-bit PowerShell on 64-bit Windows
#  reports PROCESSOR_ARCHITECTURE=x86 and hides the truth in PROCESSOR_ARCHITEW6432.
#  Both are read so the answer still works on a .NET runtime without
#  RuntimeInformation (it needs 4.7.1+).
# ------------------------------------------------------------------------------
function Get-OsArchitecture {
    $raw = ""
    try { $raw = "$([System.Runtime.InteropServices.RuntimeInformation]::OSArchitecture)" } catch {}
    if (-not $raw) {
        if ($env:PROCESSOR_ARCHITEW6432) { $raw = $env:PROCESSOR_ARCHITEW6432 }
        elseif ($env:PROCESSOR_ARCHITECTURE) { $raw = $env:PROCESSOR_ARCHITECTURE }
    }
    switch -Regex ("$raw".Trim().ToUpperInvariant()) {
        # PyOrgSuffix is the python.org file-name suffix; UvTarget is the
        # astral-sh/uv release asset. uv ships no 32-bit Windows build, so that
        # column stays empty for x86 and the chain simply skips it there.
        '^(AMD64|X64)$'     { return @{ Key='amd64'; Label='x64';          PyOrgSuffix='-amd64'; UvTarget='x86_64-pc-windows-msvc';  Supported=$true } }
        '^(ARM64)$'         { return @{ Key='arm64'; Label='ARM64';        PyOrgSuffix='-arm64'; UvTarget='aarch64-pc-windows-msvc'; Supported=$true } }
        '^(X86|I386|I686)$' { return @{ Key='x86';   Label='x86 (32-bit)'; PyOrgSuffix='';      UvTarget=$null;                       Supported=$true } }
        default             { return @{ Key='unknown'; Label="$raw";       PyOrgSuffix=$null;    UvTarget=$null;                       Supported=$false } }
    }
}

$OsArch = Get-OsArchitecture

# The exact CPython the direct-download fallback installs. Kept in one place so the
# URL, the cached file name and the version can never drift apart.
$PyOrgVersion = '3.12.5'

# uv is the last resort: one static binary that can fetch its own CPython and
# install packages without pip. That is what makes it useful on a machine where
# winget is blocked and pip is missing or refuses to write (PEP 668).
$UvDir = "$env:LOCALAPPDATA\uv"
$UvExe = $null

function Get-PyOrgInstallerName {
    param($Arch)
    if (-not $Arch -or -not $Arch.Supported -or $null -eq $Arch.PyOrgSuffix) { return "" }
    return "python-$PyOrgVersion$($Arch.PyOrgSuffix).exe"
}

# A failed download usually leaves an HTML error page on disk, and running that as
# an installer produces a bare "not a valid Win32 application" with no hint of the
# real cause. Checking the DOS 'MZ' magic keeps the diagnosis inside this script.
# The size floor only has to separate a real binary from an error page, so it stays
# small: a large floor would reject a perfectly valid small executable and turn this
# into an "is it big" test rather than an "is it a PE" test.
function Test-PeExecutable {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        if ((Get-Item -LiteralPath $Path).Length -lt 64KB) { return $false }
        # Two bytes through the stream API: "Get-Content -Encoding Byte" is
        # PowerShell 5.1 only, and this file also runs under pwsh 7.
        $fs = [System.IO.File]::OpenRead($Path)
        try { $b0 = $fs.ReadByte(); $b1 = $fs.ReadByte() } finally { $fs.Dispose() }
        return ($b0 -eq 0x4D -and $b1 -eq 0x5A)
    } catch { return $false }
}

function Test-RealPython {
    param([string]$Path)
    if (-not $Path) { return $false }
    if ($Path -like "*$WindowsAppsMarker*") { return $false }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    try {
        # Two answers in one probe: the major version, and the interpreter the
        # alias would actually run (an alias reports a WindowsApps path there).
        $probe = @(& $Path -c "import sys; print(sys.version_info[0]); print(sys.executable)" 2>&1)
        if ($LASTEXITCODE -ne 0) { return $false }
        $rows = @($probe | ForEach-Object { "$_".Trim() } | Where-Object { $_ })
        if ($rows.Count -lt 1 -or "$($rows[0])" -ne "3") { return $false }
        if ($rows.Count -ge 2 -and "$($rows[1])" -like "*$WindowsAppsMarker*") { return $false }
        return $true
    } catch { return $false }
}

function Test-PythonWebsockets {
    param([string]$Path)
    try {
        & $Path -c "import websockets" 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

# ------------------------------------------------------------------------------
#  AUTOMATIC PYTHON INSTALLATION - three sources, tried in that order
#
#  winget first, because it is the platform's own mechanism and picks the correct
#  architecture by itself; the python.org download second, because it also works
#  where winget is missing, blocked by policy or has no configured source; uv last,
#  because it is the only one of the three that needs neither an installer nor a
#  working pip.
#
#  Every step re-resolves the interpreter with Select-RealPython instead of trusting
#  the installer's exit code. A silent per-user install, a queued Store update and
#  an "already installed" no-op all report success, and only a probe tells them
#  apart - which is exactly the failure this chain exists to remove.
# ------------------------------------------------------------------------------

function Install-PythonFromPythonOrg {
    param($Arch)
    $name = Get-PyOrgInstallerName $Arch
    if (-not $name) { return $false }
    $url = "https://www.python.org/ftp/python/$PyOrgVersion/$name"
    $installer = "$env:TEMP\$name"
    # Reuse an earlier good download: this is the same ~25 MB file on every re-run,
    # and a half-written .exe from an interrupted attempt is rejected by the MZ
    # check rather than executed.
    if (-not (Test-PeExecutable $installer)) {
        try { Invoke-WebRequest -Uri $url -OutFile $installer -UseBasicParsing -ErrorAction Stop } catch { return $false }
    }
    if (-not (Test-PeExecutable $installer)) { return $false }
    try {
        # InstallAllUsers=0 keeps this per-user and therefore elevation-free.
        # Include_pip=1 is explicit so the dependency step always has a pip.
        $proc = Start-Process -FilePath $installer `
            -ArgumentList '/quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_pip=1' `
            -Wait -PassThru -ErrorAction Stop
        # 3010 is "success, reboot required" and is a success here.
        return ($proc.ExitCode -eq 0 -or $proc.ExitCode -eq 3010)
    } catch { return $false }
}

function Install-UvWindows {
    # Returns the path to uv.exe, or $null. The release archive is fetched and
    # unpacked here rather than piping astral.sh's install script into iex: a script
    # that arrives over the network and is executed unread is a worse default than
    # an archive this function unpacks itself.
    param($Arch)
    if (-not $Arch -or -not $Arch.UvTarget) { return $null }
    $exe = Join-Path $UvDir 'uv.exe'
    if (Test-Path -LiteralPath $exe) { return $exe }
    $zip = Join-Path $env:TEMP "uv-$($Arch.Key).zip"
    try {
        Invoke-WebRequest -Uri "https://github.com/astral-sh/uv/releases/latest/download/uv-$($Arch.UvTarget).zip" `
            -OutFile $zip -UseBasicParsing -ErrorAction Stop
        if (!(Test-Path $UvDir)) { New-Item -ItemType Directory -Path $UvDir -Force | Out-Null }
        # The asset carries uv.exe, uvw.exe and uvx.exe at its top level.
        Expand-Archive -LiteralPath $zip -DestinationPath $UvDir -Force -ErrorAction Stop
    } catch { return $null }
    if (Test-Path -LiteralPath $exe) { return $exe }
    return $null
}

function Install-PythonViaUv {
    # uv downloads a standalone CPython into its own directory, so no installer,
    # no elevation and no writable system Python is involved. Get-PythonCandidates
    # knows that directory, which is how the freshly fetched interpreter is found
    # by the ordinary resolution path.
    param([string]$Uv)
    if (-not $Uv) { return $false }
    try {
        & $Uv python install $PyOrgVersion 2>&1 | Out-Null
        return $true
    } catch { return $false }
}

# ------------------------------------------------------------------------------
#  DEPENDENCY INSTALLATION - websockets is the node's only external requirement
#
#  core/mcp_tools.py and core/server.py are standard library only, so this one
#  package is the entire dependency surface. It is installed through five different
#  mechanisms because each fails for a different reason on a locked-down machine:
#  a plain pip needs a writable site-packages, --user needs a writable profile,
#  ensurepip needs an interpreter that shipped without pip, uv needs neither pip nor
#  a writable Python directory, and a venv needs only the interpreter itself.
#
#  Returns the interpreter that actually ended up with the package. That return
#  value matters: when only the venv fallback works, the pinned autostart
#  interpreter must BECOME the venv's python.exe, or the node autostarts with an
#  interpreter that cannot import websockets.
# ------------------------------------------------------------------------------
function Install-WebsocketsFor {
    param([string]$Python, [string]$Uv)

    if (Test-PythonWebsockets $Python) { return @{ Ok = $true; Python = $Python } }

    # 1. plain pip, after upgrading it: an old pip cannot find or build wheels.
    & $Python -m pip install --disable-pip-version-check --quiet --upgrade pip 2>&1 | Out-Null
    & $Python -m pip install --disable-pip-version-check --quiet websockets 2>&1 | Out-Null
    if (Test-PythonWebsockets $Python) { return @{ Ok = $true; Python = $Python } }

    # 2. --user: site-packages is read-only, the profile is not.
    & $Python -m pip install --disable-pip-version-check --quiet --user websockets 2>&1 | Out-Null
    if (Test-PythonWebsockets $Python) { return @{ Ok = $true; Python = $Python } }

    # 3. ensurepip: a per-user install lands without pip when the feature set was
    #    trimmed, or when the launcher is disabled by policy.
    & $Python -m ensurepip --default-pip 2>&1 | Out-Null
    & $Python -m pip install --disable-pip-version-check --quiet websockets 2>&1 | Out-Null
    if (Test-PythonWebsockets $Python) { return @{ Ok = $true; Python = $Python } }

    # 4. uv: installs into a managed environment without needing pip at all.
    if ($Uv) {
        & $Uv pip install --python $Python websockets 2>&1 | Out-Null
        if (Test-PythonWebsockets $Python) { return @{ Ok = $true; Python = $Python } }
    }

    # 5. venv: the only fallback that needs no write access to the system Python.
    $venvDir = "$env:USERPROFILE\.config\remote-harness-mcp\venv"
    $venvPython = "$venvDir\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $venvPython)) { & $Python -m venv $venvDir 2>&1 | Out-Null }
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython -m pip install --disable-pip-version-check --quiet --upgrade pip 2>&1 | Out-Null
        & $venvPython -m pip install --disable-pip-version-check --quiet websockets 2>&1 | Out-Null
        if (Test-PythonWebsockets $venvPython) { return @{ Ok = $true; Python = $venvPython } }
        if ($Uv) {
            & $Uv pip install --python $venvPython websockets 2>&1 | Out-Null
            if (Test-PythonWebsockets $venvPython) { return @{ Ok = $true; Python = $venvPython } }
        }
    }
    return @{ Ok = $false; Python = $Python }
}

function Get-PythonCandidates {
    $list = @()
    # Every "python" on PATH, not just the first: the first hit is usually the
    # Store alias while a real install sits further down the same PATH.
    foreach ($cmd in @(Get-Command python -All -ErrorAction SilentlyContinue)) {
        if ($cmd -and $cmd.Source) { $list += $cmd.Source }
    }
    $list += @(
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:ProgramFiles\Python313\python.exe",
        "$env:ProgramFiles\Python312\python.exe",
        "$env:ProgramFiles\Python311\python.exe"
    )
    # Any per-user 3.x the python.org installer left behind, newest first. This
    # used to be a second, separate fallback that forgot to record the path it
    # found, so a machine with only 3.13 was pushed into a fresh download.
    $localRoot = "$env:LOCALAPPDATA\Programs\Python"
    if (Test-Path -LiteralPath $localRoot) {
        $list += @(Get-ChildItem -Path $localRoot -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName 'python.exe' })
    }
    # The uv-managed CPython, for the case where the uv fallback is what produced
    # an interpreter: it lives outside PATH and outside the python.org directories,
    # so without this the freshly fetched Python would be installed and then never
    # found again. UV_PYTHON_INSTALL_DIR is honoured because uv lets a user move it.
    $uvRoots = @("$env:APPDATA\uv\python")
    if ($env:UV_PYTHON_INSTALL_DIR) { $uvRoots += $env:UV_PYTHON_INSTALL_DIR }
    foreach ($uvRoot in $uvRoots) {
        if (Test-Path -LiteralPath $uvRoot) {
            $list += @(Get-ChildItem -Path $uvRoot -Directory -Filter 'cpython-3*' -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending |
                ForEach-Object { Join-Path $_.FullName 'python.exe' })
        }
    }
    # The "py" launcher is not an interpreter: ask it which real python.exe it
    # runs, so the autostart entry always names a python.exe, never "py -3".
    $cmdPy = Get-Command py -ErrorAction SilentlyContinue
    if ($cmdPy -and $cmdPy.Source) {
        try {
            $real = @(& $cmdPy.Source -3 -c "import sys; print(sys.executable)" 2>$null)
            if ($LASTEXITCODE -eq 0 -and $real.Count -ge 1 -and "$($real[0])".Trim()) {
                $list += "$($real[0])".Trim()
            }
        } catch {}
    }
    return @($list | Where-Object { $_ } | Select-Object -Unique)
}

function Select-RealPython {
    # An interpreter that already has websockets wins: the dependency step then
    # cannot silently install into a different, more fragile Python.
    $fallback = $null
    foreach ($cand in (Get-PythonCandidates)) {
        if (-not (Test-RealPython $cand)) { continue }
        if (Test-PythonWebsockets $cand) { return $cand }
        if (-not $fallback) { $fallback = $cand }
    }
    return $fallback
}

function Add-PythonToPath {
    param([string]$Path)
    if (-not $Path) { return }
    $dir = Split-Path -Parent $Path
    if ($dir -and ($env:Path -notlike "*$dir*")) {
        $env:Path = "$dir;$dir\Scripts;$env:Path"
    }
}

$hasPython = $false
$PyExe = $null
$PyExe = Select-RealPython
if ($PyExe) {
    Add-PythonToPath $PyExe
    $hasPython = $true
    $pyVer = & $PyExe --version 2>&1
    if ($Lang -eq "ru") {
        Write-Host "[1/3] Python обнаружен: $pyVer ($PyExe)" -ForegroundColor Green
    } else {
        Write-Host "[1/3] Python detected: $pyVer ($PyExe)" -ForegroundColor Green
    }
    if (-not (Test-PythonWebsockets $PyExe)) {
        if ($Lang -eq "ru") {
            Write-Host "[1/3] В выбранном Python нет websockets - он будет установлен на шаге 2." -ForegroundColor DarkYellow
        } else {
            Write-Host "[1/3] The selected Python has no websockets yet - it will be installed in step 2." -ForegroundColor DarkYellow
        }
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[1/3] Рабочий Python 3 не найден (ярлык Microsoft Store за интерпретатор не считается)." -ForegroundColor Yellow
} else {
    Write-Host "[1/3] No working Python 3 found (the Microsoft Store alias does not count)." -ForegroundColor Yellow
}


# ------------------------------------------------------------------------------
#  PUBLIC DOMAIN - resolved from the one source of truth
#
#  The domain is declared exactly once, in core/domain.py. This block asks that
#  same chain, in the same order, and it is the ONLY place in this script that
#  picks a hostname:
#
#    1. -Gateway on the command line;
#    2. $env:MESH_PUBLIC_URL;
#    3. $env:AGY_PUBLIC_BASE_URL (legacy alias, same meaning);
#    4. the domain file - $env:MESH_DOMAIN_FILE when set, otherwise
#       %USERPROFILE%\.config\remote-harness-mcp\domain.env;
#    5. $PublishedDomain - the domain this copy was published with. The repository
#       carries the placeholder __MESH_DOMAIN__ there and deploy_gateway.sh
#       rewrites that line while publishing, so a served copy knows its domain;
#    6. core/domain.py's DEFAULT_PUBLIC_BASE_URL, read through the interpreter
#       found above (a repository checkout, or a node that bootstrapped already).
#
#  If none of them yields a real hostname the installer stops. A node registered
#  against a domain that does not resolve can never connect, and its autostart
#  entry would retry that dead endpoint forever.
# ------------------------------------------------------------------------------
$DomainPlaceholder = "__MESH_DOMAIN__"
# deploy_gateway.sh rewrites THIS line only (anchored on $PublishedDomain), so the
# placeholder above survives as the "this copy was not published" marker.
$PublishedDomain = "__MESH_DOMAIN__"
$ScriptRoot = $null
if ($MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
}

function Get-NormalisedHost([string]$Value) {
    if ([string]::IsNullOrWhiteSpace($Value)) { return "" }
    $text = $Value.Trim().Trim('"').Trim("'")
    $text = $text -replace '^[A-Za-z][A-Za-z0-9+.-]*://', ''
    $text = $text.Split('/')[0]
    return $text.Trim()
}

function Get-DomainFileValue([string]$Path) {
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return "" }
    $values = @{}
    try {
        # -Encoding UTF8 also absorbs the BOM that PowerShell itself writes.
        foreach ($line in (Get-Content -LiteralPath $Path -Encoding UTF8)) {
            $trimmed = $line.Trim()
            if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) { continue }
            $index = $trimmed.IndexOf('=')
            $key = $trimmed.Substring(0, $index).Trim()
            $value = $trimmed.Substring($index + 1).Trim().Trim('"').Trim("'")
            if ($key) { $values[$key] = $value }
        }
    } catch { return "" }
    if ($values['MESH_PUBLIC_URL']) { return $values['MESH_PUBLIC_URL'] }
    if ($values['AGY_PUBLIC_BASE_URL']) { return $values['AGY_PUBLIC_BASE_URL'] }
    return ""
}

function Get-ModuleDefaultDomain {
    if (-not $ScriptRoot) { return "" }
    $module = Join-Path $ScriptRoot 'core\domain.py'
    if (-not (Test-Path -LiteralPath $module)) { return "" }
    $value = ""
    if ($PyExe) {
        $interpreter = ($PyExe -split ' ')[0]
        $probe = "import sys; sys.path.insert(0, r'$ScriptRoot'); from core import domain; print(domain.DEFAULT_PUBLIC_BASE_URL)"
        try { $value = & $interpreter -c $probe 2>$null } catch { $value = "" }
    }
    if (-not $value) {
        $match = Select-String -LiteralPath $module -Pattern '^DEFAULT_PUBLIC_BASE_URL\s*=\s*"([^"]+)"' |
            Select-Object -First 1
        if ($match) { $value = $match.Matches[0].Groups[1].Value }
    }
    return "$value".Trim()
}

# ------------------------------------------------------------------------------
#  PERSIST THE DOMAIN (what the node reads to build its public links)
#
#  core/domain.py resolves the public domain from MESH_PUBLIC_URL in the
#  environment, then from the domain file, then from its built-in default - and it
#  never reads the legacy MESH_GATEWAY that agent.env carries. A node installed
#  with only agent.env therefore dials the right gateway (gateway_host() does
#  honour MESH_GATEWAY) while minting share links on the built-in default.
#
#  The domain is already resolved here, so it is written down here: the place that
#  decides the domain is then also the place that records it, and the node's
#  links, its tunnel and the gateway agree by construction.
#
#  Never fatal: a profile directory that cannot be written still leaves a working
#  node, so a failure only prints the command the operator can run by hand.
# ------------------------------------------------------------------------------
function Write-DomainFile {
    param([string]$Domain, [string]$Path)

    # The caller hands over a bare host (the resolution block above normalises it),
    # but this function is the last thing between a value and the file the runtime
    # trusts: a scheme or a trailing slash would be written verbatim and become
    # "MESH_PUBLIC_URL=https://https://host/", which no reader recovers from.
    # Normalise exactly the way the resolution chain does.
    $Domain = Get-NormalisedHost $Domain
    if ([string]::IsNullOrWhiteSpace($Domain) -or $Domain -eq $DomainPlaceholder) { return }
    if ([string]::IsNullOrWhiteSpace($Path)) { return }

    # Never record the project's built-in default. It is a fallback, not a decision:
    # writing it would turn "nothing is configured" into a pinned domain file, and
    # once that file exists core/domain.py stops consulting the legacy MESH_GATEWAY
    # at all - the operator's own agent.env knob would silently go inert. Nothing is
    # lost by skipping it: with no file the resolver's last fallback IS that value,
    # so the node's links and its tunnel still name the same host.
    $moduleDefault = Get-NormalisedHost (Get-ModuleDefaultDomain)
    if ($moduleDefault -and $Domain -eq $moduleDefault) { return }

    $existing = Get-NormalisedHost (Get-DomainFileValue $Path)
    if ($existing -eq $Domain) { return }

    $line = "MESH_PUBLIC_URL=https://$Domain"
    $directory = Split-Path -Parent $Path
    try {
        if ($directory -and -not (Test-Path -LiteralPath $directory)) {
            New-Item -ItemType Directory -Force -Path $directory | Out-Null
        }
        # Written without a BOM. Every reader tolerates one (core/domain.py reads
        # utf-8-sig, install.sh strips it), but a BOM-less file is readable by all
        # of them, sed and grep included.
        [System.IO.File]::WriteAllText(
            $Path, $line + "`r`n", (New-Object System.Text.UTF8Encoding($false)))
    } catch {
        if ($Lang -eq "ru") {
            Write-Host "[!] Не удалось записать домен в $Path : $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host "    Ссылки узла останутся без публичного домена, пока вы его не зададите." -ForegroundColor Yellow
            Write-Host "    Запишите вручную:  Set-Content '$Path' '$line'" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Could not write the domain to $Path : $($_.Exception.Message)" -ForegroundColor Yellow
            Write-Host "    The node's links stay without a public domain until you set one." -ForegroundColor Yellow
            Write-Host "    Write it by hand:  Set-Content '$Path' '$line'" -ForegroundColor Yellow
        }
        return
    }

    if ($Lang -eq "ru") {
        if ($existing) {
            Write-Host "[OK] Домен узла: $line ($Path, было https://$existing)" -ForegroundColor Green
        } else {
            Write-Host "[OK] Домен узла: $line ($Path)" -ForegroundColor Green
        }
    } else {
        if ($existing) {
            Write-Host "[OK] Node domain: $line ($Path, was https://$existing)" -ForegroundColor Green
        } else {
            Write-Host "[OK] Node domain: $line ($Path)" -ForegroundColor Green
        }
    }
}

$ConfigDirDefault = "$env:USERPROFILE\.config\remote-harness-mcp"
# The project was renamed from "Antigravity Mesh": an upgraded node keeps the
# configuration directory its token and agent.env already live in, exactly as
# core/paths.py resolves it for the node itself.
$LegacyConfigDir = "$env:USERPROFILE\.config\antigravity-mesh"
if (-not (Test-Path $ConfigDirDefault) -and (Test-Path $LegacyConfigDir)) { $ConfigDirDefault = $LegacyConfigDir }
# The administrator service runs as SYSTEM, whose %USERPROFILE% is not the
# operator's: a per-user agent.env would be invisible to it, and a per-user domain
# file would leave the service publishing links on the wrong gateway. The
# machine-wide location is also what core/paths.py resolves once the service
# exports MESH_CONFIG_DIR (see ops/windows/rhm-service.ps1).
if ($AdminService) {
    $ProgramDataRoot = if ($env:ProgramData) { $env:ProgramData } else { "C:\ProgramData" }
    $ConfigDirDefault = Join-Path $ProgramDataRoot "remote-harness-mcp"
}
$DomainFilePath = if ($env:MESH_DOMAIN_FILE) { $env:MESH_DOMAIN_FILE } else { "$ConfigDirDefault\domain.env" }
$ResolvedDomain = Get-NormalisedHost $Gateway
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost $env:MESH_PUBLIC_URL }
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost $env:AGY_PUBLIC_BASE_URL }
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost (Get-DomainFileValue $DomainFilePath) }
if (-not $ResolvedDomain -and $PublishedDomain -ne $DomainPlaceholder) {
    $ResolvedDomain = Get-NormalisedHost $PublishedDomain
}
if (-not $ResolvedDomain) { $ResolvedDomain = Get-NormalisedHost (Get-ModuleDefaultDomain) }

$DomainMissing = [string]::IsNullOrWhiteSpace($ResolvedDomain) -or $ResolvedDomain -eq $DomainPlaceholder
if ($Mode -eq "standalone") {
    # A standalone node publishes through Funnel or stays local: it never talks to
    # a gateway, so a missing gateway domain is not an error for it.
    $DomainMissing = $false
}
if ($DomainMissing) {
    if ($Lang -eq "ru") {
        Write-Host ""
        Write-Host "[!] Домен шлюза не задан." -ForegroundColor Red
        Write-Host "    Домена нет ни в этой копии установщика, ни в окружении, ни в файле" -ForegroundColor Red
        Write-Host "    $DomainFilePath. Встроенного домена у проекта нет намеренно: чужой" -ForegroundColor Red
        Write-Host "    адрес не должен становиться адресом вашего узла." -ForegroundColor Red
        Write-Host "    Укажите домен одним из способов:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <общий-домен>" -ForegroundColor Yellow
        Write-Host "      `$env:MESH_PUBLIC_URL='https://<общий-домен>'; .\install.ps1" -ForegroundColor Yellow
        Write-Host "      Set-Content '$DomainFilePath' 'MESH_PUBLIC_URL=https://<общий-домен>'" -ForegroundColor Yellow
        Write-Host "    Установка остановлена: регистрация на несуществующем домене не выполняется," -ForegroundColor Yellow
        Write-Host "    файл конфигурации и автозапуск не создаются." -ForegroundColor Yellow
    } else {
        Write-Host ""
        Write-Host "[!] No gateway domain configured." -ForegroundColor Red
        Write-Host "    No domain was found in this installer copy, in the environment or in" -ForegroundColor Red
        Write-Host "    $DomainFilePath. The project ships no built-in domain on purpose: an address" -ForegroundColor Red
        Write-Host "    you do not own must never become the address of your node." -ForegroundColor Red
        Write-Host "    Pass the domain in one of these ways:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <shared-domain>" -ForegroundColor Yellow
        Write-Host "      `$env:MESH_PUBLIC_URL='https://<shared-domain>'; .\install.ps1" -ForegroundColor Yellow
        Write-Host "      Set-Content '$DomainFilePath' 'MESH_PUBLIC_URL=https://<shared-domain>'" -ForegroundColor Yellow
        Write-Host "    Stopping: a node is never registered against a domain that does not exist," -ForegroundColor Yellow
        Write-Host "    no config file and no autostart are created." -ForegroundColor Yellow
    }
    Write-Host ""
    if (-not $DryRun) { exit 1 }
} else {
    $Gateway = $ResolvedDomain
}

# Preflight: report what was detected and change nothing. This runs after the
# detection steps above and before any install (winget, the Python installer,
# pip), so the reported state is real and the machine is untouched.
if ($DryRun) {
    $probeExe = if ($PyExe) { ($PyExe -split " ")[0] } else { $null }
    $wsState = "unknown"
    if ($probeExe) {
        & $probeExe -c "import websockets" 2>$null
        $wsState = if ($LASTEXITCODE -eq 0) { "installed" } else { "missing" }
    }
    $pyState = if ($hasPython) { $PyExe } else { "not found" }
    # The architecture is reported here because it decides which build every
    # automatic step above will fetch, and "why did it install the x64 build on my
    # ARM machine" is not answerable from the rest of this report.
    $archState = if ($OsArch.Supported) { $OsArch.Label } else { "$($OsArch.Label) (unsupported)" }
    $domainState = if ($DomainMissing) { "NOT CONFIGURED" } else { $Gateway }
    # The live node state, so a preflight can say "your node is offline right now"
    # before anything on disk is touched. The name may still gain a suffix at
    # registration (auto_suffix), so this stays best effort.
    $nodeState = "not checked"
    if (-not $DomainMissing) {
        $probeNode = if ($User) { $User } else { $Hostname }
        try {
            $probeHealth = Invoke-RestMethod -Uri "https://$Gateway/health?user=$probeNode" -TimeoutSec 5 -ErrorAction Stop
            $nodeState = if ($probeHealth.node_online) { "online ($probeNode)" } else { "offline ($probeNode)" }
        } catch {
            $nodeState = "gateway unreachable"
        }
    }
    # What the domain file would receive: the very line the real run writes. A dry
    # run changes nothing, so this only reports it - and it reports a skip when the
    # writer would skip, so the two never disagree.
    $domainFileState = if ($DomainMissing) {
        "$DomainFilePath (not written: no domain configured)"

    } else {
        "$DomainFilePath -> MESH_PUBLIC_URL=https://$Gateway"
    }
    # Which autostart shape the real run would build: the per-user Startup entry,
    # or the machine-wide SYSTEM task of the administrator variant.
    $autostartState = if ($AdminService) {
        "scheduled task RemoteHarnessMCPService (at startup, SYSTEM)"
    } else {
        "$([Environment]::GetFolderPath('Startup'))\rhm-agent.vbs"
    }
    $modeState = if ($AdminService) {
        "MCP service (SYSTEM, administrator rights)"
    } else {
        "per-user Startup entry"
    }
    if ($Lang -eq "ru") {
        Write-Host "[DRY-RUN] Python      : $pyState"
        Write-Host "[DRY-RUN] websockets  : $wsState"
        Write-Host "[DRY-RUN] Домен       : $domainState"
        Write-Host "[DRY-RUN] Конфиг      : $ConfigDirDefault"
        Write-Host "[DRY-RUN] Автозапуск  : $autostartState"
        Write-Host "[DRY-RUN] Узел        : $nodeState"
        Write-Host "[DRY-RUN] Архитектура : $archState"
        Write-Host "[DRY-RUN] Файл домена : $domainFileState"
        Write-Host "[DRY-RUN] Режим       : $modeState"
        Write-Host "[DRY-RUN] Действий не выполнено." -ForegroundColor Yellow
    } else {
        Write-Host "[DRY-RUN] Python      : $pyState"
        Write-Host "[DRY-RUN] websockets  : $wsState"
        Write-Host "[DRY-RUN] Domain      : $domainState"
        Write-Host "[DRY-RUN] Config      : $ConfigDirDefault"
        Write-Host "[DRY-RUN] Autostart   : $autostartState"
        Write-Host "[DRY-RUN] Node        : $nodeState"
        Write-Host "[DRY-RUN] Arch        : $archState"
        Write-Host "[DRY-RUN] Domain file : $domainFileState"
        Write-Host "[DRY-RUN] Mode        : $modeState"
        Write-Host "[DRY-RUN] Nothing was changed." -ForegroundColor Yellow
    }
    if ($DomainMissing -or -not $hasPython) { exit 2 }
    exit 0
}

# ------------------------------------------------------------------------------
#  ADMINISTRATOR SERVICE VARIANT - elevation
#
#  Registering a task for SYSTEM is the only privileged operation in this
#  installer, so it is the only thing that asks for elevation. The request
#  happens when the service is actually installed - after the node part ran
#  unelevated - and the elevated child does nothing but register the task.
#  Starting the installer without administrator rights therefore no longer
#  blocks the service option: it prompts when it gets there.
# ------------------------------------------------------------------------------
function Test-ElevatedSession {
    try {
        $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch {
        return $false
    }
}

function Register-McpServiceTask {
    param([string]$ConfigDirPath, [string]$PayloadDir, [string]$Language)
    $taskName = "RemoteHarnessMCPService"
    $scriptPath = Join-Path $PayloadDir "ops\windows\rhm-service.ps1"
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        if ($Language -eq "ru") {
            Write-Host "[!] Не найден ${scriptPath}: службу MCP зарегистрировать нельзя." -ForegroundColor Red
        } else {
            Write-Host "[!] $scriptPath is missing: the MCP service cannot be registered." -ForegroundColor Red
        }
        return $false
    }
    # A full path, not "powershell.exe": a boot-time task runs before any user
    # PATH exists, and the system one is not guaranteed to carry the
    # WindowsPowerShell directory either.
    $servicePowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
    if (-not (Test-Path -LiteralPath $servicePowerShell)) { $servicePowerShell = "powershell.exe" }
    try {
        $action = New-ScheduledTaskAction -Execute $servicePowerShell -WorkingDirectory $PayloadDir -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -NonInteractive -File "' + $scriptPath + '"')
        $trigger = New-ScheduledTaskTrigger -AtStartup
        $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit (New-TimeSpan -Days 3650)
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force -Description "Runs the Remote Harness MCP node agent with administrator rights." -ErrorAction Stop | Out-Null
    } catch {
        if ($Language -eq "ru") {
            Write-Host "[!] Не удалось зарегистрировать задачу '$taskName': $($_.Exception.Message)" -ForegroundColor Red
        } else {
            Write-Host "[!] Could not register the '$taskName' task: $($_.Exception.Message)" -ForegroundColor Red
        }
        return $false
    }
    # A replaced task keeps the instance it is already running: a gateway-era
    # install has core.agent in memory, a Funnel install expects core.server, and
    # the running process keeps the mode it read at start. Registering the new
    # definition therefore changed nothing until the next boot - the Funnel
    # answered 502 while the task looked "Running". Stop it, kill the python it
    # spawned (Stop-ScheduledTask ends the PowerShell host, not its child) and
    # start it again, so the mode in service.env takes effect now.
    try { Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue } catch { }
    Start-Sleep -Seconds 2
    try {
        Get-CimInstance Win32_Process -Filter "Name='python.exe' or Name='pythonw.exe'" | ForEach-Object {
            $cmdline = [string]$_.CommandLine
            if ($cmdline -match 'core\.(agent|server)') {
                $ownerName = ""
                try { $ownerName = [string](Invoke-CimMethod -InputObject $_ -MethodName GetOwner).User } catch { }
                if ($ownerName -match '^(SYSTEM|СИСТЕМА)$') {
                    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
                }
            }
        }
    } catch { }
    Start-Sleep -Seconds 1
    $started = $false
    try {
        Start-ScheduledTask -TaskName $taskName -ErrorAction Stop
        Start-Sleep -Seconds 3
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        $started = [bool]($task -and $task.State -ne "Disabled")
    } catch { $started = $false }
    if ($Language -eq "ru") {
        Write-Host "[OK] Служба MCP '$taskName' зарегистрирована: запуск при загрузке, пользователь SYSTEM, права администратора." -ForegroundColor Green
        Write-Host "    Конфигурация: $ConfigDirPath (машинная, вне профиля пользователя)" -ForegroundColor DarkGray
        Write-Host "    Управление: Start-ScheduledTask / Stop-ScheduledTask -TaskName $taskName" -ForegroundColor DarkGray
        if (-not $started) {
            Write-Host "    [!] Задача создана, но не запустилась: проверьте 'Get-ScheduledTask -TaskName $taskName'." -ForegroundColor Yellow
        }
    } else {
        Write-Host "[OK] MCP service '$taskName' registered: starts at boot, runs as SYSTEM with administrator rights." -ForegroundColor Green
        Write-Host "    Configuration: $ConfigDirPath (machine-wide, outside the user profile)" -ForegroundColor DarkGray
        Write-Host "    Control it with: Start-ScheduledTask / Stop-ScheduledTask -TaskName $taskName" -ForegroundColor DarkGray
        if (-not $started) {
            Write-Host "    [!] The task was created but did not start: check 'Get-ScheduledTask -TaskName $taskName'." -ForegroundColor Yellow
        }
    }
    return $true
}

function Remove-PerUserNodeAutostart {
    # Exactly one autostart may own the MCP port. The machine task starts the server
    # at boot; a leftover per-user logon entry (or a per-user Scheduled Task created
    # by an earlier elevated install) starts a *second* server at logon, loses the
    # race for the port and answers with its own token and its own share links.
    # Switching a no-UAC install to the service therefore removes them here, where
    # the process is already elevated.
    $removed = @()
    try {
        $startup = [Environment]::GetFolderPath("Startup")
        $vbs = Join-Path $startup "rhm-node.vbs"
        if (Test-Path -LiteralPath $vbs) {
            Remove-Item -LiteralPath $vbs -Force -ErrorAction Stop
            $removed += "rhm-node.vbs"
        }
    } catch { }
    try {
        $task = Get-ScheduledTask -TaskName "RemoteHarnessMCPNode" -ErrorAction SilentlyContinue
        if ($task) {
            Unregister-ScheduledTask -TaskName "RemoteHarnessMCPNode" -Confirm:$false -ErrorAction SilentlyContinue
            $removed += "RemoteHarnessMCPNode"
        }
    } catch { }
    return ($removed -join ", ")
}

function Install-RhmCommand {
    param([string]$Python, [string]$PayloadDir, [string]$Language)
    # The npm package ships a 'rhm' bin; a PowerShell install has to place the same
    # command where a fresh shell finds it. %LOCALAPPDATA%\Programs\<product>\bin
    # belongs to the user (no elevation), and the directory is added to the *user*
    # PATH once, so 'rhm' works in cmd.exe and PowerShell alike.
    #
    # The shim must NOT carry the payload path. Baked in, it outlived the install:
    # a checkout that moved (or a second install from another folder) left "rhm"
    # answering "No module named core.menu" with no hint about the stale path. The
    # path now lives in rhm.env next to the shim, and every install rewrites it -
    # one file to look at, one place to fix.
    $binDir = Join-Path $env:LOCALAPPDATA "Programs\RemoteHarnessMCP\bin"
    try { New-Item -ItemType Directory -Force -Path $binDir -ErrorAction Stop | Out-Null } catch { return $false }
    $envFile = Join-Path $binDir "rhm.env"
    $envLines = @(
        ("RHM_PAYLOAD=" + $PayloadDir),
        ("RHM_PYTHON=" + $Python)
    )
    $lines = @(
        '@echo off',
        'rem rhm - Remote Harness MCP console. The payload path lives in rhm.env next',
        'rem to this file, so a reinstalled or moved payload needs no edit here.',
        'setlocal',
        'set "RHM_HOME=%~dp0"',
        'set "RHM_PAYLOAD="',
        'set "RHM_PYTHON="',
        'if exist "%RHM_HOME%rhm.env" for /f "usebackq tokens=1,* delims==" %%A in ("%RHM_HOME%rhm.env") do set "%%A=%%B"',
        'if not defined RHM_PAYLOAD goto :rhm_missing',
        'if not defined RHM_PYTHON set "RHM_PYTHON=python"',
        'if not exist "%RHM_PAYLOAD%\core\menu.py" goto :rhm_missing',
        'pushd "%RHM_PAYLOAD%"',
        '"%RHM_PYTHON%" -m core.menu %*',
        'set "RHM_EXIT=%ERRORLEVEL%"',
        'popd',
        'exit /b %RHM_EXIT%',
        ':rhm_missing',
        'echo rhm: the node payload is not where "%RHM_HOME%rhm.env" says. 1>&2',
        'echo      RHM_PAYLOAD=%RHM_PAYLOAD% 1>&2',
        'echo      Re-run install.ps1 from the folder the payload lives in, or edit that file. 1>&2',
        'exit /b 127'
    )
    $launcher = Join-Path $binDir "rhm.cmd"
    try {
        [System.IO.File]::WriteAllLines($envFile, [string[]]$envLines, (New-Object System.Text.UTF8Encoding($false)))
        [System.IO.File]::WriteAllLines($launcher, [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
    } catch { return $false }
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @()
    if ($userPath) { $parts = @($userPath -split ';' | Where-Object { $_ -and $_.Trim() }) }
    $already = $false
    foreach ($part in $parts) {
        if ($part.TrimEnd('\') -ieq $binDir.TrimEnd('\')) { $already = $true; break }
    }
    if (-not $already) {
        $newPath = ((@($binDir) + $parts) -join ';')
        try {
            [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
            # Tell running Explorer/shells the environment changed; a new console
            # of its own accord only reads PATH at start, which is why the hint
            # below still says "open a new window".
            $signature = '[DllImport("user32.dll", SetLastError=true, CharSet=CharSet.Auto)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);'
            $win32 = Add-Type -MemberDefinition $signature -Name RhmEnvBroadcast -Namespace Rhm -PassThru -ErrorAction Stop
            $result = [UIntPtr]::Zero
            $null = $win32::SendMessageTimeout([IntPtr]0xffff, 0x001A, [UIntPtr]::Zero, "Environment", 2, 5000, [ref]$result)
        } catch { }
    }
    if ($Language -eq "ru") {
        Write-Host "[OK] Команда rhm: $launcher" -ForegroundColor Green
        Write-Host "     Новое окно PowerShell/cmd -> rhm (меню узла)." -ForegroundColor DarkGray
    } else {
        Write-Host "[OK] The rhm command: $launcher" -ForegroundColor Green
        Write-Host "     A new PowerShell/cmd window -> rhm (the node menu)." -ForegroundColor DarkGray
    }
    return $true
}

# The elevated child of -AdminService has exactly one job. It must not repeat the
# node registration: the parent already did that before asking for elevation.
if ($ServiceOnly) {
    $payloadDir = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { (Get-Location).Path }
    $ok = Register-McpServiceTask -ConfigDirPath $ConfigDir -PayloadDir $payloadDir -Language $Lang
    if ($ok) {
        $removed = Remove-PerUserNodeAutostart
        if ($removed) {
            if ($Lang -eq "ru") { Write-Host "    Убраны прежние автозапуски: $removed" -ForegroundColor DarkGray }
            else { Write-Host "    Removed earlier autostart entries: $removed" -ForegroundColor DarkGray }
        }
        exit 0
    }
    exit 1
}

function Resolve-PythonAfterInstall {
    # Re-resolve with exactly the same rules the detection above used, and put the
    # winner on PATH for the child processes below. This is a function because the
    # chain re-resolves after each of the three sources and those call sites must
    # stay identical: a fresh interpreter only counts if it is a real python.exe
    # outside WindowsApps and it actually runs.
    $found = Select-RealPython
    if ($found) { Add-PythonToPath $found }
    return $found
}

if (-not $hasPython) {
    if ($Lang -eq "ru") {
        Write-Host "[1/3] Python не найден. Архитектура: $($OsArch.Label). Автоматическая установка..." -ForegroundColor Yellow
    } else {
        Write-Host "[1/3] Python not found. Architecture: $($OsArch.Label). Installing automatically..." -ForegroundColor Yellow
    }

    # 1. winget - the platform's own mechanism, and the only source that needs no
    #    architecture mapping from this script.
    if (-not $hasPython -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        if ($Lang -eq "ru") { Write-Host "[1/3] Источник 1/3: winget..." -ForegroundColor Yellow }
        else { Write-Host "[1/3] Source 1/3: winget..." -ForegroundColor Yellow }
        & winget install --id Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements
        $fresh = Resolve-PythonAfterInstall
        if ($fresh) { $PyExe = $fresh; $hasPython = $true }
    }

    # 2. python.org, for the architecture this machine actually runs. This used to
    #    be the "winget is missing" branch, so a machine WITH winget never reached
    #    it - and a winget that is present but blocked by policy, has no source, or
    #    silently fails left the installer with nothing to fall back on.
    if (-not $hasPython -and $OsArch.Supported) {
        if ($Lang -eq "ru") { Write-Host "[1/3] Источник 2/3: установщик python.org для $($OsArch.Label)..." -ForegroundColor Yellow }
        else { Write-Host "[1/3] Source 2/3: the python.org installer for $($OsArch.Label)..." -ForegroundColor Yellow }
        if (Install-PythonFromPythonOrg $OsArch) {
            $fresh = Resolve-PythonAfterInstall
            if ($fresh) { $PyExe = $fresh; $hasPython = $true }
        }
    }

    # 3. uv - brings its own CPython and needs neither an installer nor a pip.
    if (-not $hasPython) {
        if ($Lang -eq "ru") { Write-Host "[1/3] Источник 3/3: uv..." -ForegroundColor Yellow }
        else { Write-Host "[1/3] Source 3/3: uv..." -ForegroundColor Yellow }
        $UvExe = Install-UvWindows $OsArch
        if ($UvExe -and (Install-PythonViaUv $UvExe)) {
            $fresh = Resolve-PythonAfterInstall
            if ($fresh) { $PyExe = $fresh; $hasPython = $true }
        }
    }

    if ($hasPython) {
        if ($Lang -eq "ru") { Write-Host "[1/3] Python обнаружен: $PyExe" -ForegroundColor Green }
        else { Write-Host "[1/3] Python detected: $PyExe" -ForegroundColor Green }
    }
}

# Fail closed. Without a real interpreter the autostart entry points at nothing and
# the node stays offline until a human reads the log - the exact state the
# Microsoft Store alias produced, where the only trace was one torn log line.
if (-not $hasPython -or -not $PyExe) {
    if ($Lang -eq "ru") {
        Write-Host "[!] Рабочий Python 3 не найден." -ForegroundColor Red
        Write-Host "    Установите Python с python.org (НЕ из Microsoft Store) и повторите установку." -ForegroundColor Yellow
        Write-Host "    Остановка: без интерпретатора автозапуск узла работать не будет." -ForegroundColor Yellow
    } else {
        Write-Host "[!] No working Python 3 found." -ForegroundColor Red
        Write-Host "    Install Python from python.org (NOT from the Microsoft Store) and re-run." -ForegroundColor Yellow
        Write-Host "    Stopping: an autostart entry without an interpreter can never work." -ForegroundColor Yellow
    }
    exit 1
}
if ("$PyExe" -like "*$WindowsAppsMarker*") {
    if ($Lang -eq "ru") {
        Write-Host "[!] Отказ: выбранный интерпретатор - ярлык Microsoft Store ($PyExe)." -ForegroundColor Red
    } else {
        Write-Host "[!] Refusing to use the Microsoft Store alias as the interpreter ($PyExe)." -ForegroundColor Red
    }
    exit 1
}

    # Install dependencies.
    # The tunnel agent needs "websockets"; core/mcp_tools.py and core/server.py
    # are standard library only, so this is the single external requirement.
    if ($Lang -eq "ru") {
        Write-Host "[2/3] Проверка библиотек (websockets)..." -ForegroundColor Yellow
    } else {
        Write-Host "[2/3] Checking dependencies (websockets)..." -ForegroundColor Yellow
    }

    # Use the interpreter resolved above; fall back to PATH only if that failed.
    $pyRun = if ($PyExe) { $PyExe } else { "python" }
    $pyExeOnly = ($pyRun -split " ")[0]

    # The dependency step may have to SWITCH interpreters (the venv fallback), and
    # everything below - the Startup launcher, the watchdog task, the final "run
    # now" - must use the interpreter that can actually import websockets. Pinning
    # the pre-install choice here is what left a node autostarting against a Python
    # that could not import the one package the agent needs.
    $result = Install-WebsocketsFor -Python $pyExeOnly -Uv $UvExe

    # uv is only fetched when everything else failed. It was not needed for Python
    # itself on this machine, so downloading it up front would be an 18 MB cost for
    # a machine that never needs it - but a pip that cannot write anywhere is
    # precisely the case uv fixes, so the chain is retried once with it.
    if (-not $result.Ok -and -not $UvExe) {
        $UvExe = Install-UvWindows $OsArch
        if ($UvExe) { $result = Install-WebsocketsFor -Python $pyExeOnly -Uv $UvExe }
    }

    if ($result.Ok -and $result.Python) {
        $pyExeOnly = $result.Python
        $PyExe = $result.Python
    }

    if (-not $result.Ok) {
        if ($Lang -eq "ru") {
            Write-Host "[!] Не удалось установить websockets автоматически." -ForegroundColor Red
            Write-Host "    Проверены: pip, pip --user, ensurepip, uv и venv." -ForegroundColor Yellow
            Write-Host "    Выполните вручную: $pyExeOnly -m pip install websockets" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Could not install websockets automatically." -ForegroundColor Red
            Write-Host "    Tried: pip, pip --user, ensurepip, uv and a venv." -ForegroundColor Yellow
            Write-Host "    Run manually: $pyExeOnly -m pip install websockets" -ForegroundColor Yellow
        }
        exit 1
    }
    if ($Lang -eq "ru") {
        Write-Host "[OK] websockets установлен ($pyExeOnly)." -ForegroundColor Green
    } else {
        Write-Host "[OK] websockets is installed ($pyExeOnly)." -ForegroundColor Green
    }

$ConfigDir = $ConfigDirDefault
if (!(Test-Path $ConfigDir)) { New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null }

# Bootstrap project files if piped from irm/iex
$ScriptDir = if ($MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { $null }
if (-not $ScriptDir -or -not (Test-Path "$ScriptDir\core\agent.py")) {
    $BootstrapDir = "$env:USERPROFILE\.remote-harness-mcp"
    $CoreDir = "$BootstrapDir\core"
    $SkillsDir = "$BootstrapDir\skills"
    # The watchdog, the doctor and the updater are fetched too: the scheduled
    # tasks registered below run the first and the last, and a node whose agent
    # dies - or whose code goes stale - must heal and update itself without
    # anyone re-running the installer by hand.
    $OpsDir = "$BootstrapDir\ops\windows"
    if (!(Test-Path $CoreDir)) { New-Item -ItemType Directory -Path $CoreDir -Force | Out-Null }
    if (!(Test-Path $SkillsDir)) { New-Item -ItemType Directory -Path $SkillsDir -Force | Out-Null }
    if (!(Test-Path $OpsDir)) { New-Item -ItemType Directory -Path $OpsDir -Force | Out-Null }
    
    # EVERY module the node imports, in the same shape as install.sh: the platform
    # layer is a package (core/platforms/<os>/...), so a flat list that stopped at the
    # top-level files left a bootstrapped node unable to start ("from core import
    # platforms"). tests/test_installer_bootstrap.py checks both installers and
    # imports the result, so a module cannot be missing from only one of them.
    $files = @(
        "core/__init__.py",
        "core/agent.py",
        "core/agent_harness.py",
        "core/code_cache.py",
        "core/control.py",
        "core/device.py",
        "core/domain.py",
        "core/goal.py",
        "core/hooks.py",
        "core/mcp_tools.py",
        "core/menu.py",
        "core/paths.py",
        "core/policies.py",
        "core/policy.py",
        "core/rekey.py",
        "core/redact.py",
        "core/rescue.py",
        "core/sandbox.py",
        "core/schedule.py",
        "core/schemas.py",
        "core/server.py",
        "core/landing.py",
        "core/settings.py",
        "core/subagents.py",
        "core/termux.py",
        "core/triggers.py",
        "core/updater.py",
        "core/usage.py",
        "core/version.py",
        "core/vitals.py",
        "core/web_share.py",
        "core/web_settings.py",
        "core/platforms/__init__.py",
        "core/platforms/base.py",
        "core/platforms/common.py",
        "core/platforms/encoding.py",
        "core/platforms/png.py",
        "core/platforms/shell.py",
        "core/platforms/windows/__init__.py",
        "core/platforms/windows/clipboard.py",
        "core/platforms/windows/process.py",
        "core/platforms/windows/screen.py",
        "core/platforms/windows/telemetry.py",
        "core/platforms/windows/winapi.py",
        "core/platforms/linux/__init__.py",
        "core/platforms/linux/process.py",
        "core/platforms/linux/procfs.py",
        "core/platforms/linux/telemetry.py",
        "core/platforms/android/__init__.py",
        "core/platforms/android/sysfs.py",
        "core/platforms/android/telemetry.py",
        "core/platforms/android/termux.py",
        "core/platforms/darwin/__init__.py",
        "core/platforms/darwin/clipboard.py",
        "core/platforms/darwin/process.py",
        "core/platforms/darwin/screen.py",
        "core/platforms/darwin/telemetry.py",
        "skills/orchestrator.md",
        "ops/windows/agent-watchdog.ps1",
        "ops/windows/rhm-tray.ps1",
        "ops/windows/rhm-service.ps1",
        "ops/windows/run-hidden.vbs",
        "ops/update.ps1",
        "ops/doctor.ps1"
    )
    foreach ($f in $files) {
        $dest = "$BootstrapDir\$($f -replace '/', '\')"
        # A nested path needs its directory first: Invoke-WebRequest does not create
        # one, and a failure here used to be swallowed and only surface as a Python
        # import error on the first start.
        $parent = Split-Path -Parent $dest
        if ($parent -and !(Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        try {
            Invoke-WebRequest -Uri "https://$Gateway/$f" -OutFile $dest -UseBasicParsing -ErrorAction Stop
        } catch {
            try {
                Invoke-WebRequest -Uri "https://raw.githubusercontent.com/LevRa7/remote-harness-mcp/main/$f" -OutFile $dest -UseBasicParsing
            } catch {}
        }
    }
    $ScriptDir = $BootstrapDir
}

function Print-McpBox($Title, $Url, $CopiedText, $BorderColor = "Green") {
    $MinLen = [Math]::Max($Title.Length, $Url.Length)
    if ($CopiedText) {
        $MinLen = [Math]::Max($MinLen, $CopiedText.Length)
    }
    $ContentWidth = [Math]::Max($MinLen + 4, 84)
    $Border = "  +" + ("-" * $ContentWidth) + "+"
    $Empty  = "  |" + (" " * $ContentWidth) + "|"

    Write-Host $Border -ForegroundColor $BorderColor
    
    $padTitle = $ContentWidth - 2 - $Title.Length
    Write-Host "  | " -NoNewline -ForegroundColor $BorderColor
    Write-Host $Title -NoNewline -ForegroundColor White
    Write-Host ((" " * $padTitle) + " |") -ForegroundColor $BorderColor

    Write-Host $Empty -ForegroundColor $BorderColor

    $padUrl = $ContentWidth - 2 - $Url.Length
    Write-Host "  | " -NoNewline -ForegroundColor $BorderColor
    Write-Host $Url -NoNewline -ForegroundColor Yellow
    Write-Host ((" " * $padUrl) + " |") -ForegroundColor $BorderColor

    Write-Host $Empty -ForegroundColor $BorderColor

    if ($CopiedText) {
        $padCopy = $ContentWidth - 2 - $CopiedText.Length
        Write-Host "  | " -NoNewline -ForegroundColor $BorderColor
        Write-Host $CopiedText -NoNewline -ForegroundColor Green
        Write-Host ((" " * $padCopy) + " |") -ForegroundColor $BorderColor
    }

    Write-Host $Border -ForegroundColor $BorderColor
}

# ---------------------------------------------------------------------------
# Tailscale Funnel helpers (standalone mode without a gateway).
#
# The node is only reachable on loopback, so an edge tunnel is the whole public
# surface. Funnel terminates TLS on <node>.<tailnet>.ts.net and forwards to
# 127.0.0.1, which is also why the query token stays acceptable: the node sees
# a loopback client and accepts ?token=, while a remote caller is refused.
# ---------------------------------------------------------------------------

function Resolve-Tailscale {
    $candidates = @()
    $onPath = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    if ($env:ProgramFiles) { $candidates += (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe") }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) { return $candidate }
    }
    return $null
}

function Get-TailnetName {
    param([string]$Tailscale)
    # Self.DNSName is the MagicDNS name Funnel answers on; the trailing dot is
    # part of the JSON answer, not part of a Host header.
    try {
        $status = (& $Tailscale status --json 2>$null | Out-String) | ConvertFrom-Json
        $name = [string]$status.Self.DNSName
        if ($name) { return $name.TrimEnd(".").ToLower() }
    } catch { }
    return ""
}

function Get-TailscaleState {
    param([string]$Tailscale)
    # BackendState is "Running" once the node is logged in and the netmap is up;
    # "NeedsLogin" means the operator still has to approve the machine in a browser.
    try {
        $status = (& $Tailscale status --json 2>$null | Out-String) | ConvertFrom-Json
        return [string]$status.BackendState
    } catch { return "" }
}

function Install-Tailscale {
    # winget is part of Windows 10 1809+ and needs no extra tooling. A missing
    # package manager must not abort the whole install: the node still works
    # locally, and the operator gets the download link.
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) { return $null }
    & winget.exe install -e --id Tailscale.Tailscale --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Null
    return (Resolve-Tailscale)
}

function Connect-Tailscale {
    param([string]$Tailscale, [int]$TimeoutSec = 300)
    # This is the authorization prompt: "tailscale up" opens the browser itself
    # and prints the device URL, which is echoed here because the GUI hides the
    # browser and the console installer may be run over RDP or in a VM.
    if ((Get-TailscaleState -Tailscale $Tailscale) -eq "Running") { return $true }
    $log = Join-Path $env:TEMP "rhm-tailscale-up.log"
    $errLog = Join-Path $env:TEMP "rhm-tailscale-up.err"
    Remove-Item -Force $log, $errLog -ErrorAction SilentlyContinue
    try {
        $proc = Start-Process -FilePath $Tailscale -ArgumentList "up" -PassThru -NoNewWindow `
            -RedirectStandardOutput $log -RedirectStandardError $errLog
    } catch { return $false }
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    $announced = $false
    $restarts = 0
    while ((Get-Date) -lt $deadline) {
        if (-not $announced) {
            $text = ""
            foreach ($f in @($log, $errLog)) {
                if (Test-Path -LiteralPath $f) { $text += (Get-Content -LiteralPath $f -Raw -ErrorAction SilentlyContinue) }
            }
            $match = [regex]::Match([string]$text, "https://login\.tailscale\.com/a/[A-Za-z0-9]+")
            if ($match.Success) {
                Write-Host ""
                Write-Host "  Откройте ссылку и подтвердите вход в Tailscale / Open and approve:" -ForegroundColor Yellow
                Write-Host "  $($match.Value)" -ForegroundColor Cyan
                Write-Host ""
                $announced = $true
            }
        }
        if ((Get-TailscaleState -Tailscale $Tailscale) -eq "Running") {
            try { if ($proc -and -not $proc.HasExited) { $proc.Kill() } } catch { }
            return $true
        }
        if ($proc -and $proc.HasExited) {
            if ((Get-TailscaleState -Tailscale $Tailscale) -eq "Running") { return $true }
            # Right after a fresh install the service can still be starting, so the
            # first "up" may die before it ever reaches the login server.
            if ($restarts -ge 3) { break }
            $restarts++
            Start-Sleep -Seconds 5
            Remove-Item -Force $log, $errLog -ErrorAction SilentlyContinue
            try {
                $proc = Start-Process -FilePath $Tailscale -ArgumentList "up" -PassThru -NoNewWindow `
                    -RedirectStandardOutput $log -RedirectStandardError $errLog
            } catch { break }
            continue
        }
        Start-Sleep -Seconds 2
    }
    try { if ($proc -and -not $proc.HasExited) { $proc.Kill() } } catch { }
    return ((Get-TailscaleState -Tailscale $Tailscale) -eq "Running")
}

function Show-FunnelAclHint {
    if ($Lang -eq "ru") {
        Write-Host "[!] Funnel не разрешён для вашего tailnet. Включите его один раз в ACL:" -ForegroundColor Yellow
        Write-Host "    https://login.tailscale.com/admin/acls" -ForegroundColor Cyan
        Write-Host '    добавьте: "nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]' -ForegroundColor White
    } else {
        Write-Host "[!] Funnel is not allowed for this tailnet. Enable it once in the ACL:" -ForegroundColor Yellow
        Write-Host "    https://login.tailscale.com/admin/acls" -ForegroundColor Cyan
        Write-Host '    add: "nodeAttrs": [{"target": ["autogroup:member"], "attr": ["funnel"]}]' -ForegroundColor White
    }
}

function Resolve-FreePort {
    param([int]$Start = 8096, [int]$Limit = 50)
    # A default port is a preference, not a promise. Something else (another
    # product, a node of a different install) may own 8096; the Funnel and the
    # printed URL must then name the port the server really bound.
    for ($offset = 0; $offset -lt $Limit; $offset++) {
        $candidate = $Start + $offset
        $probe = $null
        try {
            $probe = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Loopback, $candidate)
            $probe.Start()
            return $candidate
        } catch {
        } finally {
            if ($probe) { try { $probe.Stop() } catch { } }
        }
    }
    return $Start
}

function Stop-StandaloneNode {
    param([int]$Port)
    # A node left by an earlier install keeps the port and answers with ITS
    # environment: the new run would print a URL while the old process (without
    # the Funnel hostname in MESH_ALLOWED_HOSTS) returns 403. Kill only ours.
    try {
        $listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($listener in @($listeners)) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
            if ($proc -and $proc.CommandLine -and $proc.CommandLine -like "*core.server*") {
                Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
            }
        }
        Start-Sleep -Milliseconds 600
    } catch { }
    # Anything still listening is not ours (a WSL relay, another server): report it,
    # because the new node would die on the busy port while /health answers for the
    # old one and the installer would print a URL nobody serves.
    try {
        $left = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($listener in @($left)) {
            $proc = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)" -ErrorAction SilentlyContinue
            if ($proc) { return ("{0} (PID {1})" -f $proc.Name, $proc.ProcessId) }
        }
    } catch { }
    return ""
}

function Wait-StandaloneNode {
    param([int]$Port, [int]$TimeoutSec = 20, [string]$Token = "")
    if (-not $Token) { $Token = $script:StandaloneToken }
    # Starting a process is not the same as a listening server: without this the
    # installer can print a URL for a node that died on a busy port.
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        try {
            $probeUrl = "http://127.0.0.1:$Port/health"
            if ($Token) { $probeUrl += "?token=" + [uri]::EscapeDataString($Token) }
            $probe = Invoke-WebRequest -Uri $probeUrl -TimeoutSec 2 -UseBasicParsing
            if ($probe.StatusCode -ne 200) { continue }
            if (-not $Token) { return $true }
            # With a token we expect OUR OWN node: only it accepts this credential
            # and answers with vitals. Another node on the same port (a stale
            # per-user one, a WSL relay) answers 200 without them, and the install
            # would print a URL that node does not serve.
            if ($probe.Content -match '"vitals"') { return $true }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    return $false
}

function Register-StandaloneStartup {
    param([string]$Python, [int]$Port, [string]$WorkingDirectory)
    # A per-user logon entry needs no elevation, while Register-ScheduledTask does:
    # on a normal account it fails with "Access is denied" and leaves no autostart.
    # The quoting copies the agent launcher this project already ships: cmd /c with
    # an escaped quoted path, output appended to a log so a silent failure is not silent.
    $startup = [Environment]::GetFolderPath("Startup")
    if (-not $startup) { return $false }
    $log = Join-Path (Join-Path $env:USERPROFILE ".config\remote-harness-mcp") "standalone.log"
    $run = 'WshShell.Run "cmd /c """"' + $Python + '"" -u -m core.server --host 127.0.0.1 --port=' + $Port + ' >> """"' + $log + '"""" 2>&1""", 0, False'
    $lines = @(
        'Set WshShell = CreateObject("WScript.Shell")',
        ('WshShell.CurrentDirectory = "' + $WorkingDirectory + '"'),
        $run
    )
    try {
        [System.IO.File]::WriteAllLines((Join-Path $startup "rhm-node.vbs"), [string[]]$lines, (New-Object System.Text.UTF8Encoding($false)))
        return $true
    } catch { return $false }
}

function Register-StandaloneNodeTask {
    param([string]$Python, [int]$Port, [string]$WorkingDirectory)
    # pythonw keeps the logon task windowless; the token is NOT on this command
    # line - the node reads it from standalone.env (core.server.apply_standalone_env).
    $exe = $Python
    $pythonw = Join-Path (Split-Path -Parent $Python) "pythonw.exe"
    if (Test-Path -LiteralPath $pythonw) { $exe = $pythonw }
    $nodeArgs = "-u -m core.server --host 127.0.0.1 --port=$Port"
    $taskCommand = '"' + $exe + '" ' + $nodeArgs
    & schtasks.exe /Create /TN "RemoteHarnessMCPNode" /TR $taskCommand /SC ONLOGON /RL LIMITED /F 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) { return $true }
    try {
        $action = New-ScheduledTaskAction -Execute $exe -Argument $nodeArgs -WorkingDirectory $WorkingDirectory
        $triggers = @((New-ScheduledTaskTrigger -AtLogOn))
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -StartWhenAvailable -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        Register-ScheduledTask -TaskName "RemoteHarnessMCPNode" -Action $action -Trigger $triggers `
            -Settings $settings -Force -Description "Starts the standalone Remote Harness MCP node at logon." | Out-Null
        return $true
    } catch { return $false }
}

if ($Mode -eq "standalone") {
    $standaloneWatch = [System.Diagnostics.Stopwatch]::StartNew()
    if ($Lang -eq "ru") {
        Write-Host "=== Запуск в режиме Local Standalone на порту $Port ===" -ForegroundColor Blue
    } else {
        Write-Host "=== Starting in Local Standalone Mode on port $Port ===" -ForegroundColor Blue
    }
    # A standalone server is never unauthenticated: generate a token, keep it in a
    # user-only env file, and pass it explicitly. The URL carries it only because
    # Gemini's connector field cannot set an Authorization header; the server
    # accepts a query token only from a loopback client.
    $StandaloneEnv = Join-Path $ConfigDir 'standalone.env'
    New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
    # Reuse the token an earlier install wrote: a reinstall must not silently
    # invalidate the connector URL the operator already pasted into Gemini.
    $StandaloneToken = ""
    if ($Token) {
        $StandaloneToken = $Token
    } elseif (Test-Path -LiteralPath $StandaloneEnv) {
        try {
            $existingLine = Select-String -LiteralPath $StandaloneEnv -Pattern '^MESH_TOKEN=(.+)$' | Select-Object -First 1
            if ($existingLine) { $StandaloneToken = $existingLine.Matches[0].Groups[1].Value.Trim() }
        } catch { }
    }
    if (-not $StandaloneToken -and $AdminService) {
        # Upgrading a per-user node to the machine service must not change the
        # token: the connector URL the operator already pasted would stop working.
        $userEnv = Join-Path (Join-Path $env:USERPROFILE ".config\remote-harness-mcp") "standalone.env"
        if (Test-Path -LiteralPath $userEnv) {
            try {
                $userLine = Select-String -LiteralPath $userEnv -Pattern '^MESH_TOKEN=(.+)$' | Select-Object -First 1
                if ($userLine) { $StandaloneToken = $userLine.Matches[0].Groups[1].Value.Trim() }
            } catch { }
        }
    }
    if (-not $StandaloneToken) {
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $tokenBytes = New-Object byte[] 32
        $rng.GetBytes($tokenBytes)
        $rng.Dispose()
        $StandaloneToken = -join ($tokenBytes | ForEach-Object { $_.ToString('x2') })
    }

    # Resolve the Funnel hostname BEFORE the env file is written: the allowlist
    # entry, the Funnel target and the printed link must name one host.
    $FunnelName = ""
    $TsExe = $null
    if ($Funnel) {
        $TsExe = Resolve-Tailscale
        if (-not $TsExe) {
            if ($Lang -eq "ru") { Write-Host "[1/4] Tailscale не найден - устанавливаю..." -ForegroundColor Yellow }
            else { Write-Host "[1/4] Tailscale not found - installing..." -ForegroundColor Yellow }
            $TsExe = Install-Tailscale
        }
        if ($TsExe) {
            # This is the authorization prompt: the browser opens here, right
            # after installation and before the node starts, so the operator
            # approves the machine once and the public URL exists immediately.
            if ((Get-TailscaleState -Tailscale $TsExe) -ne "Running") {
                if ($Lang -eq "ru") { Write-Host "[2/4] Вход в Tailscale (сейчас откроется браузер)..." -ForegroundColor Yellow }
                else { Write-Host "[2/4] Signing in to Tailscale (a browser window will open)..." -ForegroundColor Yellow }
            }
            if (Connect-Tailscale -Tailscale $TsExe) {
                $FunnelName = Get-TailnetName -Tailscale $TsExe
            } elseif ($Lang -eq "ru") {
                Write-Host "[!] Вход не подтверждён за 5 минут: публичная ссылка не создана." -ForegroundColor Yellow
                Write-Host "    Подтвердите вход и повторите: tailscale up" -ForegroundColor Yellow
            } else {
                Write-Host "[!] Sign-in was not approved within 5 minutes: no public URL." -ForegroundColor Yellow
                Write-Host "    Approve the login and re-run: tailscale up" -ForegroundColor Yellow
            }
        } elseif ($Lang -eq "ru") {
            Write-Host "[!] Tailscale не установлен. Поставьте вручную: https://tailscale.com/download/windows" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Tailscale is not installed. Install it: https://tailscale.com/download/windows" -ForegroundColor Yellow
        }
    }

    $envLines = @("MESH_TOKEN=" + $StandaloneToken)
    if ($FunnelName) {
        $envLines += ("MESH_ALLOWED_HOSTS=" + $FunnelName)
        # Share links (and the connection card) must name the address this node is
        # really reached at. The gateway domain belongs to tunnel mode: a Funnel
        # node never dials it, so a link built from it relays nowhere.
        $envLines += ("MESH_PUBLIC_URL=https://" + $FunnelName)
    }
    Set-Content -LiteralPath $StandaloneEnv -Value $envLines -Encoding ASCII
    if ($AdminService) {
        # The machine task runs the server as SYSTEM, so SYSTEM must be able to read
        # the token file; the operator keeps full control as well.
        try {
            icacls $StandaloneEnv /inheritance:r /grant:r ("$($env:USERNAME):F") "/grant:r" "SYSTEM:F" | Out-Null
        } catch { }
    } else {
        try {
            icacls $StandaloneEnv /inheritance:r /grant:r ("$($env:USERNAME):F") | Out-Null
        } catch { }
    }

    $nodeUp = $false
    $nodeTaskOk = $false
    # Which autostart shape the node really got: "service" (SYSTEM task at boot),
    # "logon" (per-user Startup entry) or "none". The summary prints this instead
    # of assuming the requested variant succeeded.
    $autostartKind = "none"
    if ($AdminService) {
        # Funnel + Administrator service: the task runs core.server (not core.agent),
        # keeps its configuration in ProgramData and starts at boot without a logon.
        Stop-StandaloneNode -Port $Port
        # The service has to publish the port it really binds: take the first free
        # one when the default is taken, and let the Funnel target follow it.
        if (-not $PortExplicit) {
            $chosenPort = Resolve-FreePort -Start $Port
            if ($chosenPort -ne $Port) {
                if ($Lang -eq "ru") { Write-Host "[i] Порт $Port занят: служба займёт $chosenPort." -ForegroundColor DarkGray }
                else { Write-Host "[i] Port $Port is taken: the service will use $chosenPort." -ForegroundColor DarkGray }
                $Port = $chosenPort
            }
        }
        # The machine task owns the node now: a leftover per-user launcher would
        # start a second server on the same port at logon.
        try {
            $staleStartup = Join-Path ([Environment]::GetFolderPath("Startup")) "rhm-node.vbs"
            if (Test-Path -LiteralPath $staleStartup) { Remove-Item -LiteralPath $staleStartup -Force }
        } catch { }
        $serviceEnvFile = Join-Path $ConfigDir "service.env"
        @(
            ("MESH_PYTHON=" + $pyExeOnly),
            ("MESH_CONFIG_DIR=" + $ConfigDir),
            ("MESH_SERVICE_MODE=server"),
            ("MESH_SERVICE_PORT=" + $Port)
        ) | Out-File -FilePath $serviceEnvFile -Encoding ASCII

        $serviceOk = $false
        if (Test-ElevatedSession) {
            $serviceOk = Register-McpServiceTask -ConfigDirPath $ConfigDir -PayloadDir $ScriptDir -Language $Lang
        } elseif (-not $PSCommandPath) {
            if ($Lang -eq "ru") {
                Write-Host "[!] Службу нельзя зарегистрировать из 'irm | iex': нужен файл install.ps1." -ForegroundColor Red
            } else {
                Write-Host "[!] The service cannot be registered from 'irm | iex': it needs install.ps1." -ForegroundColor Red
            }
            exit 1
        } else {
            if ($Lang -eq "ru") {
                Write-Host "[*] Узел настроен. Для регистрации службы нужны права администратора - сейчас появится запрос UAC..." -ForegroundColor Yellow
            } else {
                Write-Host "[*] The node is configured. Registering the service needs administrator rights - asking for elevation now..." -ForegroundColor Yellow
            }
            $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'),
                '-AdminService', '-Elevated', '-ServiceOnly', '-Lang', $Lang)
            try {
                $child = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $childArgs -ErrorAction Stop
                $serviceOk = ($child -and $child.ExitCode -eq 0)
            } catch { $serviceOk = $false }
        }
        if ($serviceOk) {
            $autostartKind = "service"
            # The machine task starts the server itself; give it time to bind.
            $nodeUp = Wait-StandaloneNode -Port $Port -TimeoutSec 45
            $nodeTaskOk = $true
        } else {
            # Elevation was refused, or the SYSTEM task could not be registered.
            # The node itself works: fail the *boot* autostart, not the install.
            # A per-user logon entry is exactly what a no-UAC install gets, and the
            # Funnel keeps running either way (it proxies to loopback, not to a
            # specific account).
            if ($Lang -eq "ru") {
                Write-Host "[!] Служба при старте системы не установлена (UAC отклонён или задача не создана)." -ForegroundColor Yellow
                Write-Host "    Узел продолжит работу: автозапуск при входе в систему вместо загрузки." -ForegroundColor Yellow
                Write-Host "    Повторить позже:  .\install.ps1 -Mode standalone -Funnel -AdminService" -ForegroundColor DarkGray
            } else {
                Write-Host "[!] The boot-time service was not installed (elevation refused or the task was not created)." -ForegroundColor Yellow
                Write-Host "    The node keeps working: it autostarts at logon instead of at boot." -ForegroundColor Yellow
                Write-Host "    Retry later:  .\install.ps1 -Mode standalone -Funnel -AdminService" -ForegroundColor DarkGray
            }
            $nodeTaskOk = Register-StandaloneStartup -Python $pyExeOnly -Port $Port -WorkingDirectory $ScriptDir
            if ($nodeTaskOk) {
                $autostartKind = "logon"
                $portOwner = Stop-StandaloneNode -Port $Port
                if (-not $portOwner) {
                    try {
                        $startupVbs = Join-Path ([Environment]::GetFolderPath("Startup")) "rhm-node.vbs"
                        Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $startupVbs + '"') | Out-Null
                    } catch { }
                }
            }
            $nodeUp = Wait-StandaloneNode -Port $Port -TimeoutSec 45
        }
    } else {
        # No --token on purpose: the node reads standalone.env itself, so the secret
        # stays out of the process list and out of the scheduled task.
        $portOwner = Stop-StandaloneNode -Port $Port
        if ($portOwner -and $PortExplicit) {
            # Pinned by the operator: refusing is better than silently moving the
            # port a Funnel or a connector URL already points at.
            if ($Lang -eq "ru") {
                Write-Host "[!] Порт $Port занят процессом $portOwner - узел не сможет его занять." -ForegroundColor Red
                Write-Host "    Остановите этот процесс или укажите другой -Port." -ForegroundColor Yellow
            } else {
                Write-Host "[!] Port $Port is held by $portOwner - the node cannot bind it." -ForegroundColor Red
                Write-Host "    Stop that process or pass another -Port." -ForegroundColor Yellow
            }
            exit 1
        }
        if (-not $PortExplicit) {
            # The default is a preference: something else owning 8096 must not make
            # the install fail. The Funnel, the printed URL and the autostart entry
            # all follow the port the node really binds.
            $chosenPort = Resolve-FreePort -Start $Port
            if ($chosenPort -ne $Port) {
                if ($Lang -eq "ru") { Write-Host "[i] Порт $Port занят процессом $portOwner - узел займёт $chosenPort." -ForegroundColor DarkGray }
                else { Write-Host "[i] Port $Port is held by $portOwner - the node will use $chosenPort." -ForegroundColor DarkGray }
                $Port = $chosenPort
            }
        }
        # The Startup entry is the only autostart a normal account can install:
        # creating a Scheduled Task answers "Access is denied" without elevation.
        # It is written BEFORE the node starts, and the node is started through it,
        # so the server is not a child of this installer session. A child died with
        # the install, and a node reported as "installed" was gone minutes later.
        $nodeTaskOk = Register-StandaloneStartup -Python $pyExeOnly -Port $Port -WorkingDirectory $ScriptDir
        if ($nodeTaskOk) {
            $autostartKind = "logon"
            try {
                $startupVbs = Join-Path ([Environment]::GetFolderPath("Startup")) "rhm-node.vbs"
                Start-Process -FilePath 'wscript.exe' -ArgumentList ('"' + $startupVbs + '"') | Out-Null
            } catch { }
        }
        if (-not $nodeTaskOk) {
            # No autostart at all: at least leave a working node for this session.
            $null = Start-Process $pyExeOnly -ArgumentList "-u -m core.server --host 127.0.0.1 --port=$Port" -WorkingDirectory $ScriptDir -WindowStyle Hidden -PassThru
        }
        $nodeUp = Wait-StandaloneNode -Port $Port
    }

    if ($FunnelName) {
        # Tailscale terminates TLS and proxies to loopback; the node never sees a
        # public address, which is exactly why no port and no certificate is needed.
        # Three attempts: the certificate for the ts.net name is issued lazily and
        # the very first funnel call can lose that race.
        $funnelOk = $false
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            $funnelOut = (& $TsExe funnel --bg --https=443 "http://127.0.0.1:$Port" 2>&1 | Out-String).Trim()
            if ($funnelOut) { Write-Host "    $funnelOut" -ForegroundColor DarkGray }
            if ($LASTEXITCODE -eq 0) { $funnelOk = $true; break }
            Start-Sleep -Seconds 3
        }
        if (-not $funnelOk) {
            Show-FunnelAclHint
            $FunnelName = ""
        }
    }

    # What the node really got, for the wizard and for the operator: a label that
    # says "service (at boot)" while the task was never created is how a 502 after
    # a "successful" install happened.
    $autostartTask = if ($autostartKind -eq "service") { "RemoteHarnessMCPService" } else { "" }
    try {
        $statePath = Join-Path $ConfigDir "autostart.state.json"
        New-Item -ItemType Directory -Force -Path $ConfigDir | Out-Null
        [ordered]@{
            kind = $autostartKind
            task = $autostartTask
            port = $Port
            funnel = $FunnelName
            mode = "standalone"
            updated = (Get-Date).ToString("s")
        } | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
    } catch { }
    $null = Install-RhmCommand -Python $pyExeOnly -PayloadDir $ScriptDir -Language $Lang

    $LocalUrl = "http://localhost:$Port/sse?token=$StandaloneToken"
    $PublicUrl = if ($FunnelName) { "https://$FunnelName/sse?token=$StandaloneToken" } else { "" }
    $CopyUrl = if ($PublicUrl) { $PublicUrl } else { $LocalUrl }
    $standaloneWatch.Stop()

    if ($Funnel -and -not $PublicUrl) {
        # A localhost URL is useless for a cloud connector, so this is a failure,
        # not a "done with a smaller result": the wizard must not report success.
        Write-Host ""
        Write-Host "=== ПУБЛИЧНАЯ ССЫЛКА НЕ СОЗДАНА / NO PUBLIC URL ===" -ForegroundColor Red
        Write-Host "  Узел запущен только на localhost. Проверьте по порядку:" -ForegroundColor Yellow
        Write-Host "   1. tailscale status        -> BackendState должен быть Running" -ForegroundColor Yellow
        Write-Host "   2. tailscale funnel status -> если пусто, запустите установщик ещё раз" -ForegroundColor Yellow
        Write-Host "   3. ACL: nodeAttrs funnel для tailnet (https://login.tailscale.com/admin/acls)" -ForegroundColor Yellow
        Write-Host "  Tailscale: $(if ($TsExe) { $TsExe } else { "не найден" }), состояние: $(if ($TsExe) { Get-TailscaleState -Tailscale $TsExe } else { "n/a" })" -ForegroundColor DarkGray
        Write-Host "  Потрачено: $([int]$standaloneWatch.Elapsed.TotalSeconds) с" -ForegroundColor DarkGray
        exit 1
    }
    $Copied = $false
    try {
        Set-Clipboard -Value $CopyUrl -ErrorAction Stop
        $Copied = $true
    } catch {
        try { $CopyUrl | clip.exe 2>$null; $Copied = $true } catch {}
    }

    Clear-Host

if ($Lang -eq "ru") {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)" } else { "Скопируйте ссылку выше" }
    if ($PublicUrl) {
        Print-McpBox "ПУБЛИЧНАЯ ССЫЛКА MCP (ЭТО ПАРОЛЬ, НЕ ПУБЛИКУЙТЕ):" $PublicUrl $copyMsg
        Write-Host "    Локальная (только с этого ПК): $LocalUrl" -ForegroundColor DarkGray
    } else {
        Print-McpBox "ССЫЛКА ЛОКАЛЬНОГО MCP-СЕРВЕРА (СКОПИРУЙТЕ):" $LocalUrl $copyMsg
        Write-Host "    Публичная ссылка не создана: запустите с -Funnel и установленным Tailscale." -ForegroundColor DarkGray
    }
    if ($autostartKind -eq "service") {
        Write-Host "    Автозапуск узла: служба RemoteHarnessMCPService (SYSTEM, при загрузке)." -ForegroundColor DarkGray
    } elseif ($autostartKind -eq "logon") {
        Write-Host "    Автозапуск узла: при входе в систему (rhm-node.vbs в папке Автозагрузки)." -ForegroundColor DarkGray
        Write-Host "    Запуск при загрузке (до входа): переустановите с -AdminService." -ForegroundColor DarkGray
    } else {
        Write-Host "    [!] Не удалось зарегистрировать автозапуск: узел поднимется только до перезагрузки." -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host " Куда вставлять ссылку в Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Откройте в браузере: https://gemini.google.com/customize/connectors" -ForegroundColor Cyan
    Write-Host " 2. Нажмите 'Добавить коннектор' (Add connector) и задайте имя 'MyDevice'"
    Write-Host " 3. Вставьте скопированную ссылку в поле 'URL сервера' (Ctrl+V) и нажмите Подключить." -ForegroundColor White
    Write-Host ""
} else {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)" } else { "Copy the URL above" }
    if ($PublicUrl) {
        Print-McpBox "PUBLIC MCP URL (THIS IS A PASSWORD - DO NOT SHARE):" $PublicUrl $copyMsg
        Write-Host "    Local only (this PC): $LocalUrl" -ForegroundColor DarkGray
    } else {
        Print-McpBox "LOCAL MCP SERVER URL (COPY THIS):" $LocalUrl $copyMsg
        Write-Host "    No public URL: rerun with -Funnel and Tailscale installed." -ForegroundColor DarkGray
    }
    if ($autostartKind -eq "service") {
        Write-Host "    Node autostart: the RemoteHarnessMCPService task (SYSTEM, at boot)." -ForegroundColor DarkGray
    } elseif ($autostartKind -eq "logon") {
        Write-Host "    Node autostart: at logon (rhm-node.vbs in the Startup folder)." -ForegroundColor DarkGray
        Write-Host "    Boot-time start (before logon): reinstall with -AdminService." -ForegroundColor DarkGray
    } else {
        Write-Host "    [!] Could not register autostart: the node lives until the next reboot." -ForegroundColor Yellow
    }
    Write-Host ""
    Write-Host " Where to paste this URL in Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Open in your browser: https://gemini.google.com/customize/connectors" -ForegroundColor Cyan
    Write-Host " 2. Click 'Add connector' and name it 'MyDevice'"
    Write-Host " 3. Paste the URL into the 'Server URL' field (Ctrl+V) and click Connect." -ForegroundColor White
    Write-Host ""
}
if ($null -ne $standaloneWatch) {
    Write-Host ("    Время установки / install time: {0:N1} с" -f $standaloneWatch.Elapsed.TotalSeconds) -ForegroundColor DarkGray
}
exit 0
}

# Gateway Tunnel Mode
if ([string]::IsNullOrWhiteSpace($User)) {
    $User = $Hostname
}

if ($Lang -eq "ru") {
    Write-Host "[2/3] Регистрация узла '$User' на общем домене ($Gateway)..." -ForegroundColor Yellow
} else {
    Write-Host "[2/3] Registering node '$User' on the shared domain ($Gateway)..." -ForegroundColor Yellow
}
$detectedMac = ""
try {
    $nic = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled = 'TRUE'" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($nic -and $nic.MACAddress) {
        $detectedMac = $nic.MACAddress
    }
} catch {}
if ([string]::IsNullOrWhiteSpace($detectedMac)) {
    try {
        # Use the interpreter resolved above: a bare "python" may be the Microsoft
        # Store App Execution Alias, which is on PATH but runs nothing.
        $res = & $pyExeOnly -c "import uuid; print(':'.join(['{:02x}'.format((uuid.getnode() >> ele) & 0xff) for ele in range(0,8*6,8)][::-1]))" 2>&1
        if ($LASTEXITCODE -eq 0 -and $res) {
            $detectedMac = $res.ToString().Trim()
        }
    } catch {}
}

$detectedOs = "Windows $([System.Environment]::OSVersion.Version.ToString())"

$regPayloadObj = @{
    username = $User
    auto_suffix = $true
    mac_address = $detectedMac
    os = $detectedOs
    # The architecture is part of the device fingerprint: two machines behind one
    # NAT can share a MAC-derived identity only if their OS and architecture agree,
    # and a gateway operator needs to know which build a node is running when a
    # wheel-related failure is reported.
    arch = $OsArch.Key
}
if (-not [string]::IsNullOrWhiteSpace($Token)) {
    $regPayloadObj.token = $Token
}
$regPayload = $regPayloadObj | ConvertTo-Json
# Registration is the one step that cannot be skipped or guessed: without a real
# node name and token the agent can never connect. This used to run under
# $ErrorActionPreference = "Continue", so a DNS/TLS/HTTP failure printed an error,
# the script carried on, wrote an EMPTY agent.env, installed a permanently broken
# Startup launcher, printed a link with empty user/token and claimed that link had
# been copied - leaving the operator with a node that retries a dead endpoint after
# every login. Fail closed instead, before anything on disk is touched.
try {
    $response = Invoke-RestMethod -Uri "https://$Gateway/api/register" -Method Post `
        -Body $regPayload -ContentType "application/json" -ErrorAction Stop
} catch {
    if ($Lang -eq "ru") {
        Write-Host "[!] Не удалось зарегистрировать узел на '$Gateway': $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    Проверьте, что домен шлюза разрешается в DNS и сервер доступен, затем повторите:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <ваш-домен>" -ForegroundColor Yellow
        Write-Host "    Ничего не установлено: файл конфигурации и автозапуск не создавались." -ForegroundColor Yellow
    } else {
        Write-Host "[!] Could not register this node on '$Gateway': $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "    Check that the gateway domain resolves in DNS and the server is reachable, then re-run:" -ForegroundColor Yellow
        Write-Host "      .\install.ps1 -Gateway <your-domain>" -ForegroundColor Yellow
        Write-Host "    Nothing was installed: no config file and no autostart were created." -ForegroundColor Yellow
    }
    exit 1
}

$AssignedUser = $response.username
$AssignedToken = $response.token

if ([string]::IsNullOrWhiteSpace($AssignedUser) -or [string]::IsNullOrWhiteSpace($AssignedToken)) {
    if ($Lang -eq "ru") {
        Write-Host "[!] Шлюз '$Gateway' ответил без имени узла или токена - установка остановлена." -ForegroundColor Red
    } else {
        Write-Host "[!] Gateway '$Gateway' answered without a node name or token - stopping." -ForegroundColor Red
    }
    exit 1
}

# ------------------------------------------------------------------------------
# SHARED-DOMAIN CONTRACT: one public domain serves every node and the node is
# selected by ?user=<node-name>. Never build a per-device subdomain URL: each
# extra hostname needs its own DNS record and TLS SAN, and a missing SAN makes
# the Gemini client fail with an opaque "cannot connect to host" error.
# The gateway returns this canonical URL; the fallback keeps older gateways
# working without ever advertising a subdomain.
# ------------------------------------------------------------------------------
$PublicBaseUrl = "https://$Gateway"
$SseUrl = $response.sse_url
if ([string]::IsNullOrWhiteSpace($SseUrl)) {
    $SseUrl = "$PublicBaseUrl/sse?user=$AssignedUser&token=$AssignedToken"
}

# Quick Copy to Clipboard
$Copied = $false
try {
    Set-Clipboard -Value $SseUrl -ErrorAction Stop
    $Copied = $true
} catch {
    try {
        $SseUrl | clip.exe 2>$null
        $Copied = $true
    } catch {}
}

if ($Lang -eq "ru") {
    Write-Host "[3/3] Настройка автозапуска в Windows..." -ForegroundColor Green
} else {
    Write-Host "[3/3] Configuring Windows background autostart..." -ForegroundColor Green
}
$envFile = "$ConfigDir\agent.env"
@"
MESH_GATEWAY=$Gateway
MESH_USER=$AssignedUser
MESH_TOKEN=$AssignedToken
"@ | Out-File -FilePath $envFile -Encoding utf8

# agent.env carries only the legacy MESH_GATEWAY, which core/domain.py honours for
# the tunnel but never for the links a node publishes. The domain file is what
# keeps share links on the gateway this node is registered on, so it is written
# down before the agent is started.
Write-DomainFile -Domain $Gateway -Path $DomainFilePath

# Start agent in background
$env:MESH_GATEWAY = $Gateway
$env:MESH_USER = $AssignedUser
# The console menu must be reachable in a new shell in every variant, not only in
# Funnel mode: the npm package ships a 'rhm' bin, and a PowerShell install places
# the same command itself.
$null = Install-RhmCommand -Python $pyExeOnly -PayloadDir $ScriptDir -Language $Lang
if ($AdminService) {
    # -------------------------------------------------------------------------
    #  ADMINISTRATOR SERVICE VARIANT
    #
    #  Task Scheduler is the service host Windows offers without an installer
    #  framework: a boot trigger, a machine account and restart-on-failure. A
    #  Python process is not a service binary, so "sc.exe create" would need a
    #  wrapper; a scheduled task needs none and still starts before anyone logs on.
    #
    #  The task runs as SYSTEM at the highest run level, which is exactly the
    #  "MCP service with administrator rights" this variant promises: every tool
    #  call inherits that token, and an unattended machine keeps its node.
    # -------------------------------------------------------------------------
    $serviceTaskName = "RemoteHarnessMCPService"
    $serviceScript = Join-Path $ScriptDir "ops\windows\rhm-service.ps1"
    $serviceEnvFile = Join-Path $ConfigDir "service.env"

    # The per-user entries must go: the machine task already owns the tunnel, and a
    # second agent would register the same node twice and fight over the socket.
    # Retiring them here is also what makes an upgrade from the per-user variant
    # leave exactly one agent behind.
    $startupPaths = @(
        (Join-Path ([Environment]::GetFolderPath("Startup")) "rhm-agent.vbs"),
        (Join-Path ([Environment]::GetFolderPath("Startup")) "agy-agent.vbs")
    )
    foreach ($startupPath in $startupPaths) {
        if (Test-Path -LiteralPath $startupPath) {
            Remove-Item -LiteralPath $startupPath -Force -ErrorAction SilentlyContinue
        }
    }
    try { Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "RemoteHarnessMCPTray" -ErrorAction Stop } catch {}
    foreach ($retiredTask in @("RemoteHarnessMCPWatchdog", "RemoteHarnessMCPUpdater")) {
        $registered = $null
        try { $registered = Get-ScheduledTask -TaskName $retiredTask -ErrorAction Stop } catch { $registered = $null }
        if ($registered) {
            try { Unregister-ScheduledTask -TaskName $retiredTask -Confirm:$false -ErrorAction Stop } catch {}
        }
    }

    # The machine configuration is written BEFORE elevation: creating a folder
    # under ProgramData needs no administrator rights, and the elevated child
    # then has everything it needs.
    @(
        ("MESH_PYTHON=" + $pyExeOnly),
        ("MESH_CONFIG_DIR=" + $ConfigDir)
    ) | Out-File -FilePath $serviceEnvFile -Encoding ASCII

    $serviceOk = $false
    if (Test-ElevatedSession) {
        # Started "as administrator": no second process, no UAC prompt.
        $serviceOk = Register-McpServiceTask -ConfigDirPath $ConfigDir -PayloadDir $ScriptDir -Language $Lang
    } elseif (-not $PSCommandPath) {
        if ($Lang -eq "ru") {
            Write-Host "[!] Службу MCP нельзя зарегистрировать из 'irm | iex': нужен файл install.ps1." -ForegroundColor Red
            Write-Host "    Скачайте установщик и запустите его из PowerShell." -ForegroundColor Yellow
        } else {
            Write-Host "[!] The MCP service cannot be registered from 'irm | iex': it needs the install.ps1 file." -ForegroundColor Red
            Write-Host "    Download the installer and run it from PowerShell." -ForegroundColor Yellow
        }
        exit 1
    } else {
        # Elevation is requested HERE, after the node part has already run: the
        # service option does not require starting the installer as administrator.
        if ($Lang -eq "ru") {
            Write-Host "[*] Узел установлен. Для регистрации службы нужны права администратора - сейчас появится запрос UAC..." -ForegroundColor Yellow
        } else {
            Write-Host "[*] The node is installed. Registering the service needs administrator rights - asking for elevation now..." -ForegroundColor Yellow
        }
        # -Gateway travels with the child: it exits at -ServiceOnly, but only
        # after the domain gate, which the child would otherwise fail on an
        # empty ProgramData configuration.
        $childArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'),
            '-AdminService', '-Elevated', '-ServiceOnly', '-Lang', $Lang)
        if ($Gateway) { $childArgs += @('-Gateway', ('"' + $Gateway + '"')) }
        try {
            $child = Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -PassThru -ArgumentList $childArgs -ErrorAction Stop
            $serviceOk = ($child -and $child.ExitCode -eq 0)
        } catch {
            $serviceOk = $false
        }
        if (-not $serviceOk) {
            if ($Lang -eq "ru") {
                Write-Host "[!] Повышение прав отклонено или регистрация не удалась: служба MCP не установлена." -ForegroundColor Red
                Write-Host "    Узел при этом установлен. Повторите:  .\install.ps1 -AdminService" -ForegroundColor Yellow
            } else {
                Write-Host "[!] Elevation was refused or registration failed: the MCP service was not installed." -ForegroundColor Red
                Write-Host "    The node itself is installed. Re-run:  .\install.ps1 -AdminService" -ForegroundColor Yellow
            }
            exit 1
        }
    }
} else {

    # Create Windows Startup launcher for persistent autostart.
    # The interpreter is pinned by full path because PATH may hold the Microsoft
    # Store alias, which stops working after a Store repair or an update. "-u"
    # keeps stdout unbuffered, so a killed process cannot leave a torn log line as
    # the only evidence of what happened.
    if (-not $pyExeOnly -or "$pyExeOnly" -like "*$WindowsAppsMarker*") {
        Write-Host "[!] Refusing to write an autostart entry for '$pyExeOnly'." -ForegroundColor Red
        exit 1
    }
    $startupDir = [Environment]::GetFolderPath("Startup")
    $startupVbs = "$startupDir\rhm-agent.vbs"
    # The pre-rename Startup entry (the project shipped as "Antigravity Mesh")
    # would keep launching the old agent at every logon: remove it, so only one
    # agent owns the tunnel and the token.
    $legacyStartupVbs = "$startupDir\agy-agent.vbs"
    if (Test-Path -LiteralPath $legacyStartupVbs) {
        Remove-Item -LiteralPath $legacyStartupVbs -Force -ErrorAction SilentlyContinue
        Write-Host "[OK] Removed the pre-rename Startup entry agy-agent.vbs." -ForegroundColor Green
    }
    $pyExeForVbs = $pyExeOnly
    $agentLog = "$ConfigDir\agent.log"
    @"
    Set WshShell = CreateObject("WScript.Shell")
    WshShell.Environment("PROCESS")("MESH_GATEWAY") = "$Gateway"
    WshShell.Environment("PROCESS")("MESH_USER") = "$AssignedUser"
    WshShell.Environment("PROCESS")("MESH_TOKEN") = "$AssignedToken"
    WshShell.CurrentDirectory = "$ScriptDir"
    ' Log the output so a silent autostart failure can be diagnosed later.
    WshShell.Run "cmd /c """"$pyExeForVbs"" -u -m core.agent >> """"$agentLog"""" 2>&1""", 0, False
"@ | Out-File -FilePath $startupVbs -Encoding Unicode

# Tray icon: a per-user observer/controller. It reads the same heartbeat and
# marker files as the agent, so "Pause commands" and "Read-only mode" work
# without restarting the service. Register it in HKCU Run (no admin) and start it
# now; a payload without it is still a working node, so a miss is not fatal.
$trayScript = Join-Path $ScriptDir 'ops\windows\rhm-tray.ps1'
if (Test-Path -LiteralPath $trayScript) {
    try {
        $trayCommand = 'powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $trayScript + '"'
        New-Item -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Force | Out-Null
        Set-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run' -Name 'RemoteHarnessMCPTray' -Value $trayCommand
        Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $trayScript + '"')) -WindowStyle Hidden -ErrorAction SilentlyContinue
        Write-Host "[OK] Tray icon registered and started." -ForegroundColor Green
    } catch {
        Write-Host "[!] Could not start the tray icon: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

# A Startup entry runs once per logon: it cannot recover a node whose agent died
# (crash, Windows Update reboot, broken dependency). A Scheduled Task repeats the
# watchdog every five minutes, so a dead agent comes back on its own.
#
# Both tasks are started through ops\windows\run-hidden.vbs rather than
# powershell.exe directly. A task has no console of its own, so Windows allocates
# one for powershell.exe and only then honours "-WindowStyle Hidden": the window is
# created, drawn and hidden again, and the user sees a black window flash every
# five minutes. wscript.exe is a GUI host - it never allocates a console - and
# WshShell.Run with window style 0 passes SW_HIDE to the child, so nothing is ever
# drawn. The task argument names the node script relative to the payload, which
# run-hidden.vbs resolves from its own location, so the action keeps working if the
# payload moves.
$watchdog = Join-Path $ScriptDir 'ops\windows\agent-watchdog.ps1'
$runHidden = Join-Path $ScriptDir 'ops\windows\run-hidden.vbs'
if (Test-Path -LiteralPath $watchdog) {
    # Two spellings of the same action, because the two registration paths parse
    # quotes differently: schtasks.exe needs the quotes around the script path
    # backslash-escaped (otherwise a profile path with a space is split into two
    # arguments and the task is rejected outright), while the ScheduledTasks
    # cmdlets take the argument literally.
    if (Test-Path -LiteralPath $runHidden) {
        $watchdogHost = 'wscript.exe'
        $watchdogArguments = '"' + $runHidden + '" "ops\windows\agent-watchdog.ps1" -Quiet'
        $watchdogTaskCommand = 'wscript.exe \"' + $runHidden + '\" \"ops\windows\agent-watchdog.ps1\" -Quiet'
    } else {
        # A payload without the launcher still self-heals; it just flashes. Saying
        # so is better than leaving the operator with a window they cannot explain.
        if ($Lang -eq "ru") {
            Write-Host "[!] $runHidden не найден: задача-сторож будет запускаться напрямую и мелькать окном." -ForegroundColor Yellow
        } else {
            Write-Host "[!] $runHidden is missing: the watchdog task will run PowerShell directly and flash a window." -ForegroundColor Yellow
        }
        $watchdogHost = 'powershell.exe'
        $watchdogArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $watchdog + '" -Quiet'
        $watchdogTaskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + $watchdog + '\" -Quiet'
    }
    # Retire the pre-rename scheduled tasks. The project shipped as "Antigravity
    # Mesh"; leaving those tasks registered would keep a second agent and a second
    # updater running against the same configuration after the rename. Absent tasks
    # (every fresh machine) are simply skipped.
    foreach ($legacyTask in @('AntigravityMeshWatchdog', 'AntigravityMeshUpdater')) {
        $legacyRegistered = $null
        try { $legacyRegistered = Get-ScheduledTask -TaskName $legacyTask -ErrorAction Stop } catch { $legacyRegistered = $null }
        if ($legacyRegistered) {
            try { Unregister-ScheduledTask -TaskName $legacyTask -Confirm:$false -ErrorAction Stop } catch {}
            Write-Host "[OK] Retired the pre-rename scheduled task $legacyTask." -ForegroundColor Green
        } else {
            & schtasks.exe /Delete /TN $legacyTask /F 2>$null | Out-Null
        }
    }
    $watchdogRegistered = $false
    $watchdogError = ''
    # schtasks.exe is tried first because it registers a task for the CURRENT user
    # without elevation, while Register-ScheduledTask needs write access to the task
    # store (denied for a normal user on a hardened or domain-joined machine - that
    # was measured on the machine this watchdog was built for). The minute schedule
    # repeats indefinitely, so logon is already covered by the Startup entry above
    # and needs no second trigger here.
    $watchdogError = (& schtasks.exe /Create /TN 'RemoteHarnessMCPWatchdog' /TR $watchdogTaskCommand `
        /SC MINUTE /MO 5 /F 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) { $watchdogRegistered = $true }
    if (-not $watchdogRegistered) {
        try {
            $watchdogAction = New-ScheduledTaskAction -Execute $watchdogHost -Argument $watchdogArguments -WorkingDirectory $ScriptDir
            # No -RepetitionDuration on purpose: PowerShell serialises
            # [TimeSpan]::MaxValue as P99999999DT23H59M59S, which the Task Scheduler
            # schema rejects outright; an omitted duration means "repeat
            # indefinitely", which is exactly what this needs.
            $watchdogTriggers = @(
                (New-ScheduledTaskTrigger -AtLogOn),
                (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5))
            )
            $watchdogSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
            Register-ScheduledTask -TaskName 'RemoteHarnessMCPWatchdog' -Action $watchdogAction `
                -Trigger $watchdogTriggers -Settings $watchdogSettings -Force `
                -Description 'Restarts the Remote Harness MCP node agent when it dies.' | Out-Null
            $watchdogRegistered = $true
            $watchdogError = ''
        } catch {
            $watchdogRegistered = $false
            $watchdogError = $_.Exception.Message
        }
    }
    if ($watchdogRegistered) {
        if ($Lang -eq "ru") {
            Write-Host "[OK] Задача-сторож 'RemoteHarnessMCPWatchdog' зарегистрирована (каждые 5 минут)." -ForegroundColor Green
        } else {
            Write-Host "[OK] Watchdog task 'RemoteHarnessMCPWatchdog' registered (every 5 minutes)." -ForegroundColor Green
        }
    } else {
        if ($Lang -eq "ru") {
            Write-Host "[!] Не удалось зарегистрировать задачу-сторож. Автозапуск при входе в систему всё равно создан." -ForegroundColor Yellow
            Write-Host "    Запустить проверку вручную: $watchdog" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Could not register the watchdog task. The Startup entry still starts the agent at logon." -ForegroundColor Yellow
            Write-Host "    Run the check by hand: $watchdog" -ForegroundColor Yellow
        }
        if ($watchdogError) { Write-Host "    $watchdogError" -ForegroundColor DarkYellow }
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[!] $watchdog не найден: самовосстановления у узла не будет." -ForegroundColor Yellow
} else {
    Write-Host "[!] $watchdog not found: the node has no self-healing task." -ForegroundColor Yellow
}

# The watchdog above recovers a node that is DOWN; this task moves a node that is
# merely OUT OF DATE. A machine nobody logs into would otherwise keep running
# whatever was installed the day it was set up, so the updater checks the newest
# GitHub release once a day, installs it with a rollback backup and lets its
# helper bring the agent back on the new code. The agent also checks for itself
# (MESH_UPDATE_* in core/updater.py); this task is what covers a node whose agent
# is not running at 03:30.
$updaterScript = Join-Path $ScriptDir 'ops\update.ps1'
if (Test-Path -LiteralPath $updaterScript) {
    # Same windowless host as the watchdog: a daily flash is still a flash.
    if (Test-Path -LiteralPath $runHidden) {
        $updaterHost = 'wscript.exe'
        $updaterArguments = '"' + $runHidden + '" "ops\update.ps1" -Quiet'
        $updaterTaskCommand = 'wscript.exe \"' + $runHidden + '\" \"ops\update.ps1\" -Quiet'
    } else {
        $updaterHost = 'powershell.exe'
        $updaterArguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $updaterScript + '" -Quiet'
        $updaterTaskCommand = 'powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"' + $updaterScript + '\" -Quiet'
    }
    $updaterRegistered = $false
    $updaterError = ''
    # schtasks.exe first, for the same reason as the watchdog: it needs no write
    # access to the task store, which a normal user does not have on a hardened
    # or domain-joined machine.
    $updaterError = (& schtasks.exe /Create /TN 'RemoteHarnessMCPUpdater' /TR $updaterTaskCommand `
        /SC DAILY /MO 1 /ST 03:30 /F 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -eq 0) { $updaterRegistered = $true }
    if (-not $updaterRegistered) {
        try {
            $updaterAction = New-ScheduledTaskAction -Execute $updaterHost -Argument $updaterArguments -WorkingDirectory $ScriptDir
            $updaterTriggers = @((New-ScheduledTaskTrigger -Daily -At '03:30'))
            $updaterSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
            Register-ScheduledTask -TaskName 'RemoteHarnessMCPUpdater' -Action $updaterAction `
                -Trigger $updaterTriggers -Settings $updaterSettings -Force `
                -Description 'Updates the Remote Harness MCP node from the newest GitHub release.' | Out-Null
            $updaterRegistered = $true
            $updaterError = ''
        } catch {
            $updaterRegistered = $false
            $updaterError = $_.Exception.Message
        }
    }
    if ($updaterRegistered) {
        if ($Lang -eq "ru") {
            Write-Host "[OK] Задача обновления 'RemoteHarnessMCPUpdater' зарегистрирована (ежедневно в 03:30)." -ForegroundColor Green
            Write-Host "    Проверить сейчас: powershell -File `"$updaterScript`" -Check" -ForegroundColor DarkGray
        } else {
            Write-Host "[OK] Update task 'RemoteHarnessMCPUpdater' registered (daily at 03:30)." -ForegroundColor Green
            Write-Host "    Check right now: powershell -File `"$updaterScript`" -Check" -ForegroundColor DarkGray
        }
    } else {
        if ($Lang -eq "ru") {
            Write-Host "[!] Не удалось зарегистрировать задачу обновления. Узел всё равно проверяет версию сам, пока агент запущен." -ForegroundColor Yellow
            Write-Host "    Запустить проверку вручную: powershell -File `"$updaterScript`" -Check" -ForegroundColor Yellow
        } else {
            Write-Host "[!] Could not register the update task. A running agent still checks for releases itself." -ForegroundColor Yellow
            Write-Host "    Check by hand: powershell -File `"$updaterScript`" -Check" -ForegroundColor Yellow
        }
        if ($updaterError) { Write-Host "    $updaterError" -ForegroundColor DarkYellow }
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[!] $updaterScript не найден: автоматического обновления у узла не будет." -ForegroundColor Yellow
} else {
    Write-Host "[!] $updaterScript not found: the node has no automatic update task." -ForegroundColor Yellow
}
}

# Launch now. The administrator variant is already running as the scheduled task;
# starting a second, per-user agent here would duplicate the node.
if (-not $AdminService) {
    Start-Process $pyExeOnly -ArgumentList "-u -m core.agent" -WorkingDirectory $ScriptDir -WindowStyle Hidden
}

Clear-Host

if ($Lang -eq "ru") {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] ССЫЛКА СКОПИРОВАНА В БУФЕР ОБМЕНА! (Вставьте через Ctrl+V)" } else { "Скопируйте ссылку выше" }
    Print-McpBox "ССЫЛКА MCP-СЕРВЕРА ДЛЯ ПОДКЛЮЧЕНИЯ (СКОПИРУЙТЕ):" $SseUrl $copyMsg
    Write-Host ""
    Write-Host " Куда вставлять ссылку в Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Откройте в браузере: https://gemini.google.com/customize/connectors" -ForegroundColor Cyan
    Write-Host " 2. Нажмите 'Добавить коннектор' (Add connector) и задайте имя 'MyDevice'"
    Write-Host " 3. Вставьте скопированную ссылку в поле 'URL сервера' (Ctrl+V) и нажмите Подключить." -ForegroundColor White
    Write-Host ""
} else {
    Write-Host ""
    $copyMsg = if ($Copied) { "[OK] URL COPIED TO CLIPBOARD! (Press Ctrl+V to paste)" } else { "Copy the URL above" }
    Print-McpBox "MCP SERVER URL (COPY THIS):" $SseUrl $copyMsg
    Write-Host ""
    Write-Host " Where to paste this URL in Google Gemini:" -ForegroundColor Yellow
    Write-Host " 1. Open in your browser: https://gemini.google.com/customize/connectors" -ForegroundColor Cyan
    Write-Host " 2. Click 'Add connector' and name it 'MyDevice'"
    Write-Host " 3. Paste the URL into the 'Server URL' field (Ctrl+V) and click Connect." -ForegroundColor White
    Write-Host ""
}

# ------------------------------------------------------------------------------
# Post-install check. An install that looks successful while the agent never
# connects is what leaves a client with an opaque frontend error, so the node's
# state is verified here and reported with the interpreter and the log to read.
#
# Deliberately no non-zero exit code: install-gui.ps1 only extracts the MCP URL
# from a successful run, and that link matters most exactly when the node is not
# up yet. The watchdog task retries every five minutes in the meantime.
# ------------------------------------------------------------------------------
$NodeOnline = $false
for ($attempt = 1; $attempt -le 15; $attempt++) {
    Start-Sleep -Seconds 2
    try {
        $health = Invoke-RestMethod -Uri "$PublicBaseUrl/health?user=$AssignedUser" -TimeoutSec 10 -ErrorAction Stop
        if ($health.node_online) { $NodeOnline = $true; break }
    } catch {}
}
if ($NodeOnline) {
    if ($Lang -eq "ru") {
        Write-Host "[OK] Узел '$AssignedUser' на связи со шлюзом." -ForegroundColor Green
    } else {
        Write-Host "[OK] Node '$AssignedUser' is online on the gateway." -ForegroundColor Green
    }
} elseif ($Lang -eq "ru") {
    Write-Host "[!] Узел '$AssignedUser' не вышел на связь за 30 с." -ForegroundColor Red
    Write-Host "    Интерпретатор автозапуска: $pyExeOnly" -ForegroundColor Yellow
    Write-Host "    Смотрите хвост $agentLog и запустите .\ops\doctor.ps1" -ForegroundColor Yellow
    Write-Host "    Задача 'RemoteHarnessMCPWatchdog' повторит попытку через 5 минут." -ForegroundColor Yellow
} else {
    Write-Host "[!] Node '$AssignedUser' did not come online within 30 s." -ForegroundColor Red
    Write-Host "    Autostart interpreter: $pyExeOnly" -ForegroundColor Yellow
    Write-Host "    Read the tail of $agentLog and run .\ops\doctor.ps1" -ForegroundColor Yellow
    Write-Host "    The 'RemoteHarnessMCPWatchdog' task retries every 5 minutes." -ForegroundColor Yellow
}
