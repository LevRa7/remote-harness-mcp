# ==============================================================================
#  npm/publish-release.ps1 - publish both npm packages for a released version
#
#  The registry refuses nothing, but the alias is only useful once the version it
#  points at resolves, and npm packs the WORKING TREE - not the tag. Two things
#  therefore matter here:
#
#    1. the published bytes must be the released bytes, so the default is to pack
#       from a temporary git worktree of the tag, not from the current checkout
#       (a checkout with local edits publishes something nobody reviewed);
#    2. gemini-computer-use is published only after remote-harness-mcp@<version>
#       actually resolves in the registry (publish-alias.sh guards the same way).
#
#  USAGE
#    powershell -File npm/publish-release.ps1                 # tag = v<version>
#    powershell -File npm/publish-release.ps1 -Tag v0.5.1
#    powershell -File npm/publish-release.ps1 -FromWorkingTree   # local tree (dev)
#    powershell -File npm/publish-release.ps1 -DryRun           # pack only
#    powershell -File npm/publish-release.ps1 -Otp 123456       # 2FA
#
#
#  npm's publishing policy (2026): a token that bypasses 2FA is refused for a
#  direct publish - the registry answers E_STAGE_REQUIRED and points at staged
#  publishing, which splits one release into two steps:
#
#      npx -y npm@latest stage publish --tag next --access public   # in the tag worktree
#      npx -y npm@latest stage approve <stage-id>                   # opens the browser, 2FA there
#
#  The approve step needs a person in front of a browser (a passkey or an
#  authenticator code); it cannot be done by a script that has no presence. That
#  is why the direct path below is kept: with an account that still allows it,
#  a single command publishes both packages and verifies the dist-tags.
#
#  Requires `npm login` (or NODE_AUTH_TOKEN). The CLI's web flow
#  (`npm login --auth-type=web`) authorizes a token from the browser session even
#  when the site's own login form refuses; that is the usual unblock.
# ==============================================================================
param(
    [string]$Tag = "",
    [switch]$FromWorkingTree,
    [switch]$DryRun,
    [string]$Otp = "",
    [string]$AuthToken = "",
    # The npm dist-tag, not the git tag. A release candidate must never become
    # `latest`, or every `npx remote-harness-mcp` picks up an alpha: publish it
    # under `next` and promote it explicitly once it is stable.
    [string]$DistTag = "",
    [ValidateSet('both', 'main', 'alias')]
    [string]$Stage = 'both'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Version-FromModule([string]$dir) {
    $line = Select-String -LiteralPath (Join-Path $dir 'core\version.py') -Pattern '^__version__\s*=\s*"([^"]+)"' |
        Select-Object -First 1
    if (-not $line) { throw "$dir\core\version.py declares no __version__" }
    return $line.Matches[0].Groups[1].Value
}

# --- pick the tree to pack -----------------------------------------------------
if ($FromWorkingTree) {
    $source = $repo
    $dirty = @(git -C $repo status --porcelain | Where-Object { $_ -and $_ -notmatch '^\?\?' })
    if ($dirty.Count -gt 0) {
        Write-Host 'tracked files with uncommitted changes:' -ForegroundColor Yellow
        $dirty | ForEach-Object { Write-Host "  $_" -ForegroundColor Yellow }
        throw 'refusing to publish the working tree while it differs from HEAD; commit, or drop -FromWorkingTree'
    }
    $worktree = $null
} else {
    if (-not $Tag) { $Tag = 'v' + (Version-FromModule $repo) }
    $worktree = Join-Path ([System.IO.Path]::GetTempPath()) ("rhm-publish-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    git -C $repo worktree add --detach $worktree $Tag | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "cannot check out $Tag into a worktree" }
    $source = $worktree
}

try {
    Set-Location $source
    $version = Version-FromModule $source
    $packageVersion = (Get-Content (Join-Path $source 'package.json') -Raw | ConvertFrom-Json).version
    $alias = (Get-Content (Join-Path $source 'npm\gemini-computer-use\package.json') -Raw | ConvertFrom-Json)

    Write-Host "source       : $source"
    Write-Host "node package : remote-harness-mcp@$version"
    Write-Host "package.json : $packageVersion"
    Write-Host "alias        : $($alias.name)@$($alias.version) -> remote-harness-mcp@$($alias.dependencies.'remote-harness-mcp')"
    Write-Host "npm dist-tag : $(if ($DistTag) { $DistTag } else { 'latest (default)' })"

    if ($packageVersion -ne $version) { throw "package.json ($packageVersion) and core/version.py ($version) disagree" }
    if ($alias.version -ne $version) { throw "the alias ($($alias.version)) must ship in lockstep with $version" }
    if (-not $FromWorkingTree -and $Tag -ne "v$version") {
        throw "tag $Tag does not name version $version"
    }

    # A granular access token (npm settings -> Access Tokens, "Bypass 2FA") is passed
    # per command and never written to ~/.npmrc, so the logged-in session stays as it
    # is and nothing secret is left behind.
    $npmExtra = @()
    if ($AuthToken) { $npmExtra = @("--//registry.npmjs.org/:_authToken=$AuthToken") }

    if (-not $DryRun) {
        $who = npm @npmExtra whoami 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $who) {
            throw "not logged in to npm. Run 'npm login --auth-type=web' (it authorizes a token from the browser session) or pass -AuthToken."
        }
        Write-Host "publishing as: $who"
    }

    $publishArgs = @('publish', '--access', 'public')
    if ($DistTag) { $publishArgs += @('--tag', $DistTag) }
    if ($DryRun) { $publishArgs += '--dry-run' }
    if ($Otp) { $publishArgs += @('--otp', $Otp) }

    # -Stage splits the run in two: 2FA is required per write, and one code cannot
    # cover both packages when the registry has to be polled in between. Publishing
    # the alias alone still waits for the main version to resolve first.
    if ($Stage -ne 'alias') {
        Write-Host "`n== 1/4 remote-harness-mcp@$version ==" -ForegroundColor Cyan
        & npm @npmExtra @publishArgs
        if ($LASTEXITCODE -ne 0) { throw "npm publish failed for remote-harness-mcp@$version" }
    } else {
        Write-Host "`n(stage=alias: remote-harness-mcp is not published from this run)" -ForegroundColor Yellow
    }

    if ($DryRun) {
        Write-Host "`n== 2/4 alias (dry run only) ==" -ForegroundColor Cyan
        Push-Location (Join-Path $source 'npm\gemini-computer-use')
        try { & npm @npmExtra @publishArgs } finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw 'npm publish --dry-run failed for the alias' }
        Write-Host "`ndry run complete: nothing was published" -ForegroundColor Green
        exit 0
    }

    if ($Stage -eq 'main') {
        Write-Host "`nstage=main done: remote-harness-mcp@$version published; run again with -Stage alias and a fresh OTP for the alias" -ForegroundColor Green
        exit 0
    }

    Write-Host "`n== 2/4 waiting for remote-harness-mcp@$version to resolve ==" -ForegroundColor Cyan
    $deadline = (Get-Date).AddMinutes(10)
    $resolved = $false
    while ((Get-Date) -lt $deadline) {
        $seen = (npm view "remote-harness-mcp@$version" version 2>$null)
        if ($LASTEXITCODE -eq 0 -and "$seen".Trim() -eq $version) { $resolved = $true; break }
        Start-Sleep -Seconds 10
    }
    if (-not $resolved) {
        throw "remote-harness-mcp@$version does not resolve after 10 minutes; the alias was NOT published (publishing it now would break 'npm install gemini-computer-use')"
    }

    Write-Host "`n== 3/4 gemini-computer-use@$version ==" -ForegroundColor Cyan
    Push-Location (Join-Path $source 'npm\gemini-computer-use')
    try {
        & npm @npmExtra @publishArgs
        $aliasCode = $LASTEXITCODE
    } finally { Pop-Location }
    if ($aliasCode -ne 0) { throw 'npm publish failed for the alias' }

    Write-Host "`n== 4/4 verification ==" -ForegroundColor Cyan
    $mainSeen = (npm view remote-harness-mcp version 2>$null).Trim()
    $aliasSeen = (npm view gemini-computer-use version 2>$null).Trim()
    Write-Host "registry: remote-harness-mcp=$mainSeen  gemini-computer-use=$aliasSeen"
    if ($mainSeen -ne $version -or $aliasSeen -ne $version) {
        throw 'the registry still reports another version; check the publish output above'
    }

    if ($DistTag) {
        foreach ($pkg in @('remote-harness-mcp', 'gemini-computer-use')) {
            $tags = npm view $pkg dist-tags --json 2>$null | ConvertFrom-Json
            $at = $tags.$DistTag
            Write-Host "dist-tag: $pkg@$DistTag = $at"
            if ($at -ne $version) { throw "$pkg@$DistTag points at $at, not $version" }
            if ($tags.latest -eq $version) { throw "$pkg@latest moved to $version; a pre-release must not become latest" }
        }
    }

    Write-Host "`nboth packages published:" -ForegroundColor Green
    Write-Host "  npx remote-harness-mcp@$version --version"
    Write-Host "  npx gemini-computer-use@$version --version"
} finally {
    if ($worktree) {
        Set-Location $repo
        git -C $repo worktree remove --force $worktree 2>$null | Out-Null
    }
}
