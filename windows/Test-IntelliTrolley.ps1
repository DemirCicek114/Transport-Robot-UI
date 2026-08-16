[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "IntelliTrolley.Common.ps1")

Assert-IntelliTrolleyWindows
$settings = Get-IntelliTrolleySettings
$distroName = [string]$settings.distro
Assert-IntelliTrolleyDistroName -DistroName $distroName

Write-Host "Package version: $($settings.version)"
Write-Host "WSL distribution: $distroName"
if (Test-IntelliTrolleyWsl2 -DistroName $distroName) {
    Write-Host "PASS  WSL 2"
}
else {
    throw "$distroName is not running under WSL 2."
}

$wslConfigPath = Join-Path $HOME ".wslconfig"
$mirroredNetworking = $false
if (Test-Path -LiteralPath $wslConfigPath -PathType Leaf) {
    $wslConfig = Get-Content -LiteralPath $wslConfigPath -Raw
    $mirroredNetworking = $wslConfig -match "(?im)^\s*networkingMode\s*=\s*mirrored\s*$"
}
if ($mirroredNetworking) {
    Write-Host "PASS  WSL mirrored networking configured"
}
else {
    Write-Warning "WSL mirrored networking is not configured. Local UI testing may work, but ROS DDS and phone-LAN access are not ready for acceptance."
}

$robotAddress = ""
$robotSubnet = ""
$rosDomainId = ""
if ($settings.PSObject.Properties.Name -contains "robotAddress") {
    $robotAddress = [string]$settings.robotAddress
}
if ($settings.PSObject.Properties.Name -contains "robotSubnet") {
    $robotSubnet = [string]$settings.robotSubnet
}
if ($settings.PSObject.Properties.Name -contains "rosDomainId") {
    $rosDomainId = [string]$settings.rosDomainId
}
if ($robotAddress) {
    Write-Host "Robot peer: $robotAddress"
    Write-Host "Robot subnet: $robotSubnet"
    Write-Host "ROS domain ID: $rosDomainId"
    if (Test-Connection -ComputerName $robotAddress -Count 1 -Quiet) {
        Write-Host "PASS  Raspberry Pi responds at $robotAddress"
    }
    else {
        Write-Warning "Raspberry Pi does not currently respond at $robotAddress"
    }
}
else {
    Write-Warning "Robot peer is not configured; run 'Configure Robot Network'."
}

if (Test-IntelliTrolleyFirewallRules) {
    Write-Host "PASS  scoped Hyper-V and Windows firewall rules"
}
else {
    Write-Warning "Scoped robot firewall rules are missing; run 'Configure Robot Network' as administrator."
}
if (Test-IntelliTrolleyBroadWslInboundPolicy) {
    Write-Warning "WSL still has the broad troubleshooting policy DefaultInboundAction=Allow. The scoped IntelliTrolley rules are installed; restore the WSL default inbound action to Block after acceptance testing if other WSL services do not require broad inbound access."
}

Invoke-IntelliTrolleyWsl `
    -DistroName $distroName `
    -Arguments @("intellitrolley-central", "doctor")

try {
    $health = Invoke-WebRequest `
        -Uri "http://127.0.0.1:8000/health" `
        -UseBasicParsing `
        -TimeoutSec 2
    if ($health.StatusCode -eq 200) {
        Write-Host "PASS  Windows can reach http://127.0.0.1:8000/ui"
    }
}
catch {
    Write-Warning "Mission Control is not running; start UI-only mode to test the Windows browser path."
}

Write-Host ""
Write-Host "Phone access and physical robot motion remain separate acceptance tests."
