[CmdletBinding()]
param(
    [switch]$RemoveData,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "IntelliTrolley.Common.ps1")

Assert-IntelliTrolleyWindows
$settings = Get-IntelliTrolleySettings
$distroName = [string]$settings.distro

if (-not $Force) {
    $confirmation = Read-Host "Type UNINSTALL to remove IntelliTrolley Central"
    if ($confirmation -ne "UNINSTALL") {
        Write-Host "Uninstall canceled."
        exit 0
    }
}

Invoke-IntelliTrolleyWsl `
    -DistroName $distroName `
    -Arguments @(
        "intellitrolley-central",
        "uninstall",
        $(if ($RemoveData) { "remove-data" } else { "keep-data" })
    )

if (Test-IntelliTrolleyAdministrator) {
    Remove-IntelliTrolleyFirewallRules
    Write-Host "Removed IntelliTrolley-owned Windows and Hyper-V firewall rules."
}
else {
    Write-Warning "Administrator approval was not available, so IntelliTrolley firewall rules were left in place."
}

$settingsPath = Get-IntelliTrolleySettingsPath
if (Test-Path -LiteralPath $settingsPath) {
    Remove-Item -LiteralPath $settingsPath -Force
}
Write-Host "The shared Ubuntu WSL distribution was not unregistered."
