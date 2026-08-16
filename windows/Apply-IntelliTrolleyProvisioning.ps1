[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateLength(1, 2048)]
    [string]$ProvisioningUri
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "IntelliTrolley.Common.ps1")

Assert-IntelliTrolleyWindows
if (-not (Test-IntelliTrolleyAdministrator)) {
    throw "Administrator approval is required to apply robot-network settings."
}

$rawUri = $ProvisioningUri.Trim().Trim('"')
try {
    $uri = [Uri]$rawUri
}
catch {
    throw "The IntelliTrolley provisioning link is invalid."
}
if (
    $uri.Scheme -ne "intellitrolley" -or
    $uri.Host -ne "configure-network" -or
    ($uri.AbsolutePath -ne "" -and $uri.AbsolutePath -ne "/")
) {
    throw "The provisioning link does not target IntelliTrolley network configuration."
}

$settings = @{}
$query = $uri.Query.TrimStart("?")
foreach ($item in $query.Split("&", [StringSplitOptions]::RemoveEmptyEntries)) {
    $parts = $item.Split("=", 2)
    $name = [Uri]::UnescapeDataString($parts[0])
    $value = if ($parts.Count -eq 2) {
        [Uri]::UnescapeDataString($parts[1])
    }
    else {
        ""
    }
    if ($name -notin @("robot", "subnet", "domain")) {
        throw "The provisioning link contains an unsupported setting: $name"
    }
    if ($settings.ContainsKey($name)) {
        throw "The provisioning link contains a duplicate setting: $name"
    }
    $settings[$name] = $value
}

foreach ($required in @("robot", "subnet", "domain")) {
    if (-not $settings.ContainsKey($required) -or -not [string]$settings[$required]) {
        throw "The provisioning link is missing the $required setting."
    }
}

$domainId = 0
if (-not [int]::TryParse([string]$settings.domain, [ref]$domainId)) {
    throw "The provisioning ROS domain must be an integer."
}
Assert-IntelliTrolleyRobotNetwork `
    -RobotAddress ([string]$settings.robot) `
    -RobotSubnet ([string]$settings.subnet) `
    -RosDomainId $domainId

Write-Host ""
Write-Host "IntelliTrolley facility-network handoff"
Write-Host "  robot peer: $($settings.robot)"
Write-Host "  robot subnet: $($settings.subnet)"
Write-Host "  ROS domain: $domainId"
Write-Host ""
Write-Host "The standard network tool will now update WSL, firewall, and reciprocal Pi settings."

$configureScript = Join-Path $PSScriptRoot "Configure-IntelliTrolleyNetwork.ps1"
& $configureScript `
    -RobotAddress ([string]$settings.robot) `
    -RobotSubnet ([string]$settings.subnet) `
    -RosDomainId $domainId
