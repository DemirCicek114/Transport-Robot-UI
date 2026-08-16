[CmdletBinding()]
param(
    [string]$DistroName = "Ubuntu-22.04",
    [string]$RobotAddress = "172.20.10.9",
    [string]$RobotSubnet = "172.20.10.0/28",
    [ValidateRange(0, 232)]
    [int]$RosDomainId = 0,
    [switch]$SkipRobotNetworking
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "IntelliTrolley.Common.ps1")

Assert-IntelliTrolleyWindows
Assert-IntelliTrolleyDistroName -DistroName $DistroName

$packageRoot = Get-IntelliTrolleyPackageRoot
Test-IntelliTrolleyReleaseManifest -PackageRoot $packageRoot
$version = (Get-Content -LiteralPath (Join-Path $packageRoot "PACKAGE-VERSION") -Raw).Trim()
$payloadPath = (Resolve-Path -LiteralPath (Join-Path $packageRoot "payload")).Path

$statusOutput = & wsl.exe --status 2>&1
if ($LASTEXITCODE -ne 0) {
    if (-not (Test-IntelliTrolleyAdministrator)) {
        throw "WSL is not ready. Re-run this script from PowerShell as Administrator."
    }
    Write-Host "Enabling WSL without rebooting automatically..."
    & wsl.exe --install --no-distribution
    Write-Host ""
    Write-Host "Windows may require a restart. Restart manually, then rerun this installer."
    exit 3010
}

$installedDistributions = Get-IntelliTrolleyDistributions
if ($installedDistributions -notcontains $DistroName) {
    if (-not (Test-IntelliTrolleyAdministrator)) {
        throw "Installing $DistroName requires an elevated PowerShell window."
    }
    & wsl.exe --set-default-version 2
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to set WSL 2 as the default."
    }
    Write-Host "Installing the pinned Ubuntu 22.04 WSL distribution..."
    & wsl.exe --install -d $DistroName --no-launch
    if ($LASTEXITCODE -ne 0) {
        throw "Ubuntu 22.04 installation failed."
    }
    Write-Host ""
    Write-Host "Ubuntu was installed. Restart Windows if requested, launch $DistroName once"
    Write-Host "to create its Linux user, then rerun this installer."
    exit 3010
}

if (-not (Test-IntelliTrolleyWsl2 -DistroName $DistroName)) {
    throw "$DistroName is not using WSL 2. Run: wsl.exe --set-version $DistroName 2"
}

$linuxUserId = (& wsl.exe -d $DistroName -- id -u).Trim()
if ($LASTEXITCODE -ne 0 -or $linuxUserId -eq "0") {
    throw "Launch $DistroName once and create a normal Linux user before installing IntelliTrolley."
}

if (-not $SkipRobotNetworking) {
    if (-not (Test-IntelliTrolleyAdministrator)) {
        throw "Administrator approval is required to configure WSL mirrored networking and the scoped robot firewall rules."
    }
    Assert-IntelliTrolleyRobotNetwork `
        -RobotAddress $RobotAddress `
        -RobotSubnet $RobotSubnet `
        -RosDomainId $RosDomainId

    Write-Host "Configuring WSL mirrored networking and scoped robot firewall rules..."
    $wslNetworkingChanged = Set-IntelliTrolleyWslMirroredNetworking
    Set-IntelliTrolleyFirewallRules `
        -RobotAddress $RobotAddress `
        -RobotSubnet $RobotSubnet
    if ($wslNetworkingChanged) {
        Write-Host "Restarting WSL once to apply mirrored networking..."
        & wsl.exe --shutdown
        if ($LASTEXITCODE -ne 0) {
            throw "WSL could not be restarted after configuring mirrored networking."
        }
    }
}

$linuxPayload = (& wsl.exe -d $DistroName -- wslpath -a $payloadPath).Trim()
if ($LASTEXITCODE -ne 0 -or -not $linuxPayload.StartsWith("/")) {
    throw "Could not translate the package payload path into WSL."
}

Write-Host "Installing IntelliTrolley Central $version into the WSL Linux filesystem..."
Invoke-IntelliTrolleyWsl `
    -DistroName $DistroName `
    -Arguments @("bash", "$linuxPayload/install_payload.sh", $linuxPayload, $version)

if (-not $SkipRobotNetworking) {
    Write-Host "Configuring the central Cyclone DDS peer..."
    Invoke-IntelliTrolleyWsl `
        -DistroName $DistroName `
        -Arguments @(
            "intellitrolley-central",
            "configure-network",
            $RobotAddress,
            $RosDomainId.ToString()
        )
}

Save-IntelliTrolleySettings `
    -DistroName $DistroName `
    -Version $version `
    -RobotAddress $(if ($SkipRobotNetworking) { "" } else { $RobotAddress }) `
    -RobotSubnet $(if ($SkipRobotNetworking) { "" } else { $RobotSubnet }) `
    -RosDomainId $RosDomainId

Write-Host ""
Write-Host "IntelliTrolley Central $version is installed."
Write-Host "Test it:  .\Test-IntelliTrolley.ps1"
Write-Host "Start UI: .\Start-IntelliTrolley.ps1 -Mode ui-only"
if (-not $SkipRobotNetworking) {
    Write-Host "Robot peer: $RobotAddress on ROS domain $RosDomainId"
    Write-Host "Use 'Configure Robot Network' from the Start menu to configure or repair the reciprocal Pi peer."
}
