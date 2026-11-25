[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidatePattern('^[a-fA-F0-9]{40}$')][string]$Commit,
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA 'QuorunePlay/app'),
    [string]$SourceZip,
    [ValidateRange(1, 65535)][int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$install = (Resolve-Path -LiteralPath $InstallRoot).Path
$parent = Split-Path -Parent $install
$base = Split-Path -Leaf $install
if ((Get-Item -LiteralPath $install -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
    throw "Choose the original install directory instead of a filesystem link."
}
if (-not (Test-Path -LiteralPath (Join-Path $install 'pyproject.toml') -PathType Leaf)) {
    throw "The install path must be the existing Quorune source directory."
}
if (Test-Path -LiteralPath (Join-Path $install '.git')) {
    throw "This procedure is for ZIP installations."
}
if (Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue) {
    throw "Stop the Quorune launcher with Ctrl+C and rerun after port $Port closes."
}
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backup = Join-Path $parent "$base.rollback-$stamp"
$failed = Join-Path $parent "$base.incomplete-$stamp"
$staging = Join-Path $parent "$base.stage-$([guid]::NewGuid().ToString('N'))"
foreach ($target in @($backup, $failed, $staging)) {
    if ((Split-Path -Parent ([IO.Path]::GetFullPath($target))) -ne $parent -or (Test-Path -LiteralPath $target)) {
        throw "Update paths must be unused siblings inside the installation parent."
    }
}
$oldMoved = $false
$newMoved = $false
try {
    New-Item -Path $staging -ItemType Directory | Out-Null
    if ($SourceZip) {
        $archive = (Resolve-Path -LiteralPath $SourceZip).Path
    } else {
        $archive = Join-Path $staging 'source.zip'
        Invoke-WebRequest -Uri "https://codeload.github.com/NullPriority/quorune/zip/$Commit" -OutFile $archive
    }
    $expanded = Join-Path $staging 'source'
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    $roots = @(Get-ChildItem -LiteralPath $expanded -Force)
    if ($roots.Count -ne 1 -or -not $roots[0].PSIsContainer -or $roots[0].Name -ne "quorune-$Commit") {
        throw "The source archive root does not match the requested commit."
    }
    $source = $roots[0].FullName
    if ((Split-Path -Parent $source) -ne $expanded -or
        -not (Test-Path -LiteralPath (Join-Path $source 'scripts/bootstrap_windows.ps1') -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $source 'web/package-lock.json') -PathType Leaf)) {
        throw "The requested archive is missing required Quorune files."
    }

    Move-Item -LiteralPath $install -Destination $backup
    $oldMoved = $true
    Move-Item -LiteralPath $source -Destination $install
    $newMoved = $true
    foreach ($name in @('data', 'local', 'run', '.env', '.env.local')) {
        $path = Join-Path $backup $name
        if (Test-Path -LiteralPath $path) {
            Copy-Item -LiteralPath $path -Destination $install -Recurse -Force
        }
    }
    Set-Content -LiteralPath (Join-Path $install '.quorune-build') -Value $Commit.ToLowerInvariant() -Encoding ascii
    Push-Location -LiteralPath $install
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $install 'scripts/bootstrap_windows.ps1')
        if ($LASTEXITCODE -ne 0) { throw "Python bootstrap failed with exit code $LASTEXITCODE." }
        & npm.cmd ci --prefix web
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed with exit code $LASTEXITCODE." }
        & npm.cmd run build --prefix web
        if ($LASTEXITCODE -ne 0) { throw "The browser build failed with exit code $LASTEXITCODE." }
    } finally {
        Pop-Location
    }
    Write-Host "Installed $Commit at $install"
    Write-Host "Rollback source and runtime data are preserved at $backup"
    Write-Host "Start from the install directory with the same launch settings and port $Port."
} catch {
    if ($newMoved -and (Test-Path -LiteralPath $install)) {
        Move-Item -LiteralPath $install -Destination $failed
    }
    if ($oldMoved -and -not (Test-Path -LiteralPath $install)) {
        Move-Item -LiteralPath $backup -Destination $install
    }
    throw
} finally {
    if (Test-Path -LiteralPath $staging) {
        $resolvedStage = (Resolve-Path -LiteralPath $staging).Path
        if ((Split-Path -Parent $resolvedStage) -eq $parent -and $resolvedStage -eq $staging) {
            Remove-Item -LiteralPath $resolvedStage -Recurse -Force
        }
    }
}
