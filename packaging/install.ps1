# Installs Local Zero for the current user only.
#
# No elevation, by design and by refusal: red line 11 in CLAUDE.md says every process runs
# asInvoker, and an installer that asks for administrator would break that to deliver a
# convenience. Everything here writes under %LOCALAPPDATA%, which the user already owns.
#
#   .\install.ps1                     install to %LOCALAPPDATA%\Programs\LocalZero
#   .\install.ps1 -InstallRoot D:\LZ  install somewhere else
#   .\install.ps1 -NoShortcut         skip the Start Menu entry

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string] $InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\LocalZero"),
    [switch] $NoShortcut
)

$ErrorActionPreference = "Stop"
$package = $PSScriptRoot
$shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Local Zero.lnk"

function Fail([string] $message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    exit 1
}

# What makes a directory a Local Zero install. Both must be there before anything is deleted.
$InstallMarkers = @("run.py", "brain\local_zero_brain")

function Assert-SafeToReplace([string] $path) {
    <#
        Nothing is removed recursively until it has been identified.

        -InstallRoot is a free parameter, and the directory a user is most likely to point it at by
        mistake is %LOCALAPPDATA%\LocalZero - which is not the program, it is their workspace, trust
        state, audit log and memory index. Deleting that to install a program would be the worst bug
        this script could have, so it refuses anything that is not recognisably a previous install.
    #>
    $dataRoot = Join-Path $env:LOCALAPPDATA "LocalZero"
    $resolved = (Resolve-Path -LiteralPath $path).Path.TrimEnd('\')

    if ($resolved -eq $dataRoot.TrimEnd('\')) {
        Fail "$resolved is where your data lives, not a program directory. Refusing to touch it."
    }

    foreach ($marker in $InstallMarkers) {
        if (-not (Test-Path (Join-Path $resolved $marker))) {
            Fail @"
$resolved already exists and does not look like a Local Zero install ($marker is missing).

Refusing to delete a directory this script did not create. Choose an empty path, or remove that one
yourself if it really is an old install.
"@
        }
    }
}

# --- 1. Refuse to install elevated -------------------------------------------------------------
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Fail @"
This is running elevated, and Local Zero installs per-user.

An elevated install would write files the product cannot maintain unelevated and would break the
privilege model the whole design rests on. Close this window and run it as yourself.
"@
}

# --- 2. The one prerequisite --------------------------------------------------------------------
$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $uv) {
    Fail @"
uv is not installed, and Local Zero needs it to set up its Python environment.

Install it with either of:
  winget install --id=astral-sh.uv -e
  powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"

Then run this script again. uv downloads the Python version this needs by itself; nothing else
has to be installed first.
"@
}
Write-Host "uv: $($uv.Source)"

if ($package -eq $InstallRoot) {
    Fail "This package is already at $InstallRoot. Run the installer from where it was unzipped."
}

# --- 3. Copy the package ------------------------------------------------------------------------
Write-Host "installing to $InstallRoot"
if (Test-Path $InstallRoot) {
    Assert-SafeToReplace $InstallRoot
    Write-Host "  an earlier install is there; replacing the program files"
    Remove-Item -LiteralPath $InstallRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $InstallRoot -Force | Out-Null

Get-ChildItem -LiteralPath $package -Exclude "install.ps1", "install.cmd" | ForEach-Object {
    Copy-Item -LiteralPath $_.FullName -Destination $InstallRoot -Recurse -Force
}

# --- 4. The Python environment ------------------------------------------------------------------
Write-Host "resolving the Python environment (uv sync --frozen --no-dev)"
& uv sync --frozen --no-dev --directory $InstallRoot
if ($LASTEXITCODE -ne 0) { Fail "uv sync failed with code $LASTEXITCODE. Nothing was started." }

$python = Join-Path $InstallRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { Fail "uv sync finished but produced no interpreter at $python." }

# --- 5. The Start Menu entry --------------------------------------------------------------------
if (-not $NoShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($shortcut)
    $link.TargetPath = $python
    $link.Arguments = "run.py"
    $link.WorkingDirectory = $InstallRoot
    $link.Description = "Local Zero - a local AI assistant"
    $link.Save()
    Write-Host "shortcut: $shortcut"
}

Write-Host ""
Write-Host "Installed." -ForegroundColor Green
Write-Host "  start it:   Start Menu -> Local Zero"
Write-Host "  or:         $python run.py"
Write-Host "  uninstall:  $InstallRoot\uninstall.ps1"
Write-Host ""
Write-Host "Your data lives at $env:LOCALAPPDATA\LocalZero and is never touched by this installer."
