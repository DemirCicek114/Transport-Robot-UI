[CmdletBinding()]
param(
    [string]$RobotAddress = "",
    [string]$RobotSubnet = "",
    [int]$RosDomainId = -1,
    [string]$PiUser = "zrpi",
    [switch]$SkipPiConfiguration
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "IntelliTrolley.Common.ps1")

function Get-SavedSetting {
    param(
        [Parameter(Mandatory = $true)]$Settings,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)]$Fallback
    )

    if (
        $Settings.PSObject.Properties.Name -contains $Name -and
        $null -ne $Settings.$Name -and
        [string]$Settings.$Name
    ) {
        return $Settings.$Name
    }
    return $Fallback
}

function Read-Setting {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)]
        [AllowEmptyString()]
        [string]$Default
    )

    $promptText = if ($Default) { "$Prompt [$Default]" } else { $Prompt }
    $value = Read-Host $promptText
    if ($value.Trim()) {
        return $value.Trim()
    }
    return $Default
}

Assert-IntelliTrolleyWindows
if (-not (Test-IntelliTrolleyAdministrator)) {
    throw "Administrator approval is required. Start this tool from the IntelliTrolley Start-menu shortcut."
}

$settings = Get-IntelliTrolleySettings
$distroName = [string]$settings.distro
$version = [string]$settings.version
Assert-IntelliTrolleyDistroName -DistroName $distroName

if (-not $RobotAddress) {
    $RobotAddress = [string](Get-SavedSetting `
        -Settings $settings `
        -Name "robotAddress" `
        -Fallback "172.20.10.9")
    $RobotAddress = Read-Setting -Prompt "Robot IPv4 address" -Default $RobotAddress
}
if (-not $RobotSubnet) {
    $RobotSubnet = [string](Get-SavedSetting `
        -Settings $settings `
        -Name "robotSubnet" `
        -Fallback "172.20.10.0/28")
    $RobotSubnet = Read-Setting -Prompt "Private robot-network CIDR" -Default $RobotSubnet
}
if ($RosDomainId -lt 0) {
    $savedDomain = [int](Get-SavedSetting `
        -Settings $settings `
        -Name "rosDomainId" `
        -Fallback 0)
    $domainText = Read-Setting `
        -Prompt "ROS domain ID (0-232)" `
        -Default $savedDomain.ToString()
    if (-not [int]::TryParse($domainText, [ref]$RosDomainId)) {
        throw "ROS domain ID must be an integer."
    }
}

Assert-IntelliTrolleyRobotNetwork `
    -RobotAddress $RobotAddress `
    -RobotSubnet $RobotSubnet `
    -RosDomainId $RosDomainId

Write-Host ""
Write-Host "Stopping any running central stack before changing DDS networking..."
Invoke-IntelliTrolleyWsl `
    -DistroName $distroName `
    -Arguments @("intellitrolley-central", "stop") `
    -AllowFailure | Out-Null

$wslNetworkingChanged = Set-IntelliTrolleyWslMirroredNetworking
Set-IntelliTrolleyFirewallRules `
    -RobotAddress $RobotAddress `
    -RobotSubnet $RobotSubnet
if (Test-IntelliTrolleyBroadWslInboundPolicy) {
    Write-Warning "A previous broad WSL DefaultInboundAction=Allow override is still active. IntelliTrolley now has scoped rules; restore the default to Block after acceptance testing if other WSL services do not need broad inbound access."
}
if ($wslNetworkingChanged) {
    & wsl.exe --shutdown
    if ($LASTEXITCODE -ne 0) {
        throw "WSL could not be restarted after configuring mirrored networking."
    }
}

Invoke-IntelliTrolleyWsl `
    -DistroName $distroName `
    -Arguments @(
        "intellitrolley-central",
        "configure-network",
        $RobotAddress,
        $RosDomainId.ToString()
    )

Save-IntelliTrolleySettings `
    -DistroName $distroName `
    -Version $version `
    -RobotAddress $RobotAddress `
    -RobotSubnet $RobotSubnet `
    -RosDomainId $RosDomainId

Write-Host ""
Write-Host "Windows and WSL robot networking is configured."
Write-Host "  robot peer: $RobotAddress"
Write-Host "  robot subnet: $RobotSubnet"
Write-Host "  ROS domain: $RosDomainId"

if ($SkipPiConfiguration) {
    exit 0
}

$candidateAddresses = @(
    Get-NetIPAddress `
        -AddressFamily IPv4 `
        -AddressState Preferred `
        -ErrorAction SilentlyContinue |
        Where-Object {
            -not $_.SkipAsSource -and
            (Test-IntelliTrolleyIPv4InCidr `
                -Address $_.IPAddress `
                -Cidr $RobotSubnet)
        } |
        Select-Object -ExpandProperty IPAddress -Unique
)
if ($candidateAddresses.Count -eq 1) {
    $centralAddress = [string]$candidateAddresses[0]
}
else {
    if ($candidateAddresses.Count -gt 0) {
        Write-Host "Detected candidate central addresses: $($candidateAddresses -join ', ')"
        $defaultCentralAddress = [string]$candidateAddresses[0]
    }
    else {
        $defaultCentralAddress = ""
    }
    $centralAddress = Read-Setting `
        -Prompt "Central Windows IPv4 address on the robot network" `
        -Default $defaultCentralAddress
}
if (-not (Test-IntelliTrolleyIPv4InCidr `
    -Address $centralAddress `
    -Cidr $RobotSubnet)) {
    throw "Central address $centralAddress is outside robot subnet $RobotSubnet."
}
if ($PiUser -notmatch "^[a-z_][a-z0-9_-]{0,31}$") {
    throw "Invalid Pi SSH username."
}

$configurePi = Read-Host "Configure the reciprocal peer on $PiUser@$RobotAddress over SSH now? [y/N]"
if ($configurePi -notmatch "^(?i:y|yes)$") {
    Write-Host ""
    Write-Host "Pi configuration was skipped. Run this Start-menu tool again when the Pi is available."
    exit 0
}

Write-Host ""
Write-Host "The Pi service will restart. Raise the wheels or otherwise immobilize the robot."
$confirmation = Read-Host "Type CONFIGURE when the robot is safe"
if ($confirmation -ne "CONFIGURE") {
    Write-Host "Pi configuration canceled; Windows and WSL settings were kept."
    exit 0
}
if (-not (Get-Command "ssh.exe" -ErrorAction SilentlyContinue)) {
    throw "Windows OpenSSH Client is unavailable. Install it from Optional Features, then rerun this tool."
}

$remoteCommand = (
    "sudo sed -i " +
    "-e 's|^ROS_DOMAIN_ID=.*|ROS_DOMAIN_ID=$RosDomainId|' " +
    "-e 's|^ROBOT_CYCLONEDDS_PEERS=.*|ROBOT_CYCLONEDDS_PEERS=$centralAddress|' " +
    "-e 's|^ROBOT_CYCLONEDDS_INTERFACE=.*|ROBOT_CYCLONEDDS_INTERFACE=wlan0|' " +
    "/etc/default/my-bot-robot && " +
    "sudo systemctl restart my-bot-robot.service && " +
    "systemctl is-active my-bot-robot.service"
)

Write-Host "Connecting to the Pi. Enter its SSH and sudo passwords when requested."
& ssh.exe -tt "$PiUser@$RobotAddress" $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "The Pi peer configuration failed with SSH exit code $LASTEXITCODE."
}

Write-Host ""
Write-Host "Reciprocal Pi peer configured. Start Navigation, wait 20 seconds, then run Diagnostics."
