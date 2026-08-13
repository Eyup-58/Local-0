# Removes Local Zero, and by default leaves everything the user made.
#
#   .\uninstall.ps1                    remove the program, keep the data
#   .\uninstall.ps1 -RemoveData        also remove the workspace, trust file, audit log and index
#   .\uninstall.ps1 -RemoveStoredKey   also remove the cloud API key from Credential Manager
#
# The default is deliberate. The workspace, the audit log and the memory index are the user's own
# record of what this program did; deleting them silently on uninstall would be taking a decision
# that is not the installer's to take. The Obsidian vault is never touched at all - nothing here
# created it, and nothing here removes it.

#Requires -Version 5.1
[CmdletBinding()]
param(
    [string] $InstallRoot = (Join-Path $env:LOCALAPPDATA "Programs\LocalZero"),
    [switch] $RemoveData,
    [switch] $RemoveStoredKey
)

$ErrorActionPreference = "Stop"
$dataRoot = Join-Path $env:LOCALAPPDATA "LocalZero"
$shortcut = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Local Zero.lnk"
$credentialTarget = "LocalZero/gemini"

# Running from inside the directory about to be deleted would leave it half-removed.
if ($PSScriptRoot -eq $InstallRoot) {
    Copy-Item -LiteralPath $PSCommandPath -Destination $env:TEMP -Force
    Write-Host "Run the copy instead, so this directory can be removed:"
    Write-Host "  powershell -ExecutionPolicy Bypass -File $env:TEMP\uninstall.ps1"
    exit 0
}

if (Test-Path $shortcut) {
    Remove-Item -LiteralPath $shortcut -Force
    Write-Host "removed the Start Menu entry"
}

if (Test-Path $InstallRoot) {
    # Identified before it is deleted, for the same reason install.ps1 does it: -InstallRoot is a
    # free parameter and the neighbouring directory is the user's own data.
    $resolved = (Resolve-Path -LiteralPath $InstallRoot).Path.TrimEnd('\')
    if ($resolved -eq $dataRoot.TrimEnd('\')) {
        Write-Host "$resolved is your data directory, not the program. Refusing." -ForegroundColor Red
        exit 1
    }
    foreach ($marker in @("run.py", "brain\local_zero_brain")) {
        if (-not (Test-Path (Join-Path $resolved $marker))) {
            Write-Host "$resolved does not look like a Local Zero install ($marker is missing)." -ForegroundColor Red
            Write-Host "Refusing to delete a directory this script did not create."
            exit 1
        }
    }

    Remove-Item -LiteralPath $resolved -Recurse -Force
    Write-Host "removed $resolved"
} else {
    Write-Host "nothing installed at $InstallRoot"
}

if ($RemoveStoredKey) {
    & cmdkey /delete:$credentialTarget | Out-Null
    Write-Host "removed the stored cloud key ($credentialTarget)"
}

if ($RemoveData) {
    if (Test-Path $dataRoot) {
        Remove-Item -LiteralPath $dataRoot -Recurse -Force
        Write-Host "removed $dataRoot"
    }
} elseif (Test-Path $dataRoot) {
    Write-Host ""
    Write-Host "Kept, because you did not ask for them to go:"
    Get-ChildItem -LiteralPath $dataRoot | ForEach-Object { Write-Host "  $($_.FullName)" }
    Write-Host ""
    Write-Host "Remove them with:  .\uninstall.ps1 -RemoveData"
    if (-not $RemoveStoredKey) {
        Write-Host "A stored cloud key, if you set one, is still in Credential Manager under"
        Write-Host "$credentialTarget - remove it with:  .\uninstall.ps1 -RemoveStoredKey"
    }
}

Write-Host ""
Write-Host "Your Obsidian vault was not touched." -ForegroundColor Green
