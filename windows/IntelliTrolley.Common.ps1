Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:IntelliTrolleyWslVmCreatorId = "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}"
$script:IntelliTrolleyHyperVDdsRule = "IntelliTrolley-ROS2-DDS-HyperV"
$script:IntelliTrolleyHyperVUiRule = "IntelliTrolley-MissionControl-HyperV"
$script:IntelliTrolleyWindowsDdsRule = "IntelliTrolley-ROS2-DDS-Windows"
$script:IntelliTrolleyWindowsUiRule = "IntelliTrolley-MissionControl-Windows"

function Assert-IntelliTrolleyWindows {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "IntelliTrolley Windows scripts must run from Windows PowerShell or PowerShell."
    }

    $build = [Environment]::OSVersion.Version.Build
    if ($build -lt 22621) {
        throw "Windows 11 22H2 or newer is required. Detected build: $build."
    }
    if (-not (Get-Command "wsl.exe" -ErrorAction SilentlyContinue)) {
        throw "wsl.exe is unavailable. Run this installer from an elevated PowerShell window."
    }
}

function Assert-IntelliTrolleyDistroName {
    param([Parameter(Mandatory = $true)][string]$DistroName)

    if ($DistroName -notmatch "^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$") {
        throw "Invalid WSL distribution name: $DistroName"
    }
}

function Test-IntelliTrolleyAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function ConvertTo-IntelliTrolleyIPv4Number {
    param([Parameter(Mandatory = $true)][string]$Address)

    $parsedAddress = $null
    if (
        -not [Net.IPAddress]::TryParse($Address, [ref]$parsedAddress) -or
        $parsedAddress.AddressFamily -ne [Net.Sockets.AddressFamily]::InterNetwork
    ) {
        throw "Invalid IPv4 address: $Address"
    }
    $bytes = $parsedAddress.GetAddressBytes()
    return (
        ([uint64]$bytes[0] -shl 24) -bor
        ([uint64]$bytes[1] -shl 16) -bor
        ([uint64]$bytes[2] -shl 8) -bor
        [uint64]$bytes[3]
    )
}

function Test-IntelliTrolleyPrivateIPv4 {
    param([Parameter(Mandatory = $true)][string]$Address)

    try {
        [uint64]$number = ConvertTo-IntelliTrolleyIPv4Number -Address $Address
    }
    catch {
        return $false
    }
    return (
        (($number -band [uint64]4278190080) -eq [uint64]167772160) -or
        (($number -band [uint64]4293918720) -eq [uint64]2886729728) -or
        (($number -band [uint64]4294901760) -eq [uint64]3232235520)
    )
}

function Get-IntelliTrolleyCidrParts {
    param([Parameter(Mandatory = $true)][string]$Cidr)

    $parts = $Cidr.Split("/")
    if ($parts.Count -ne 2) {
        throw "Robot subnet must use IPv4 CIDR notation, for example 172.20.10.0/28."
    }
    [uint64]$addressNumber = ConvertTo-IntelliTrolleyIPv4Number -Address $parts[0]
    $prefixLength = 0
    if (
        -not [int]::TryParse($parts[1], [ref]$prefixLength) -or
        $prefixLength -lt 1 -or
        $prefixLength -gt 32
    ) {
        throw "Robot subnet prefix length must be between 1 and 32."
    }
    [uint64]$allBits = 4294967295
    [uint64]$mask = ($allBits -shl (32 - $prefixLength)) -band $allBits
    return [pscustomobject]@{
        Address = $parts[0]
        AddressNumber = $addressNumber
        PrefixLength = $prefixLength
        Mask = $mask
        Network = $addressNumber -band $mask
    }
}

function Test-IntelliTrolleyIPv4InCidr {
    param(
        [Parameter(Mandatory = $true)][string]$Address,
        [Parameter(Mandatory = $true)][string]$Cidr
    )

    try {
        [uint64]$addressNumber = ConvertTo-IntelliTrolleyIPv4Number -Address $Address
        $cidrParts = Get-IntelliTrolleyCidrParts -Cidr $Cidr
        return (($addressNumber -band $cidrParts.Mask) -eq $cidrParts.Network)
    }
    catch {
        return $false
    }
}

function Assert-IntelliTrolleyRobotNetwork {
    param(
        [Parameter(Mandatory = $true)][string]$RobotAddress,
        [Parameter(Mandatory = $true)][string]$RobotSubnet,
        [Parameter(Mandatory = $true)][int]$RosDomainId
    )

    if (-not (Test-IntelliTrolleyPrivateIPv4 -Address $RobotAddress)) {
        throw "Robot address must be a private IPv4 address."
    }
    $cidrParts = Get-IntelliTrolleyCidrParts -Cidr $RobotSubnet
    if ($cidrParts.Network -ne $cidrParts.AddressNumber) {
        throw "Robot subnet must use its network address: $RobotSubnet"
    }
    if (-not (Test-IntelliTrolleyIPv4InCidr -Address $RobotAddress -Cidr $RobotSubnet)) {
        throw "Robot address $RobotAddress is outside robot subnet $RobotSubnet."
    }
    if ($RosDomainId -lt 0 -or $RosDomainId -gt 232) {
        throw "ROS domain ID must be between 0 and 232."
    }
}

function Set-IntelliTrolleyWslMirroredNetworking {
    $wslConfigPath = Join-Path $HOME ".wslconfig"
    $lines = [Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $wslConfigPath -PathType Leaf) {
        foreach ($line in [IO.File]::ReadAllLines($wslConfigPath)) {
            $lines.Add($line)
        }
    }

    $sectionStart = -1
    $sectionEnd = $lines.Count
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match "^\s*\[wsl2\]\s*$") {
            $sectionStart = $index
            break
        }
    }
    if ($sectionStart -ge 0) {
        for ($index = $sectionStart + 1; $index -lt $lines.Count; $index++) {
            if ($lines[$index] -match "^\s*\[[^\]]+\]\s*$") {
                $sectionEnd = $index
                break
            }
        }
        $networkingLines = @()
        for ($index = $sectionStart + 1; $index -lt $sectionEnd; $index++) {
            if ($lines[$index] -match "^\s*networkingMode\s*=") {
                $networkingLines += $index
            }
        }
        if (
            $networkingLines.Count -eq 1 -and
            $lines[$networkingLines[0]] -match "^\s*networkingMode\s*=\s*mirrored\s*$"
        ) {
            return $false
        }
        for ($index = $networkingLines.Count - 1; $index -ge 0; $index--) {
            $lines.RemoveAt($networkingLines[$index])
        }
        $lines.Insert($sectionStart + 1, "networkingMode=mirrored")
    }
    else {
        if ($lines.Count -gt 0 -and $lines[$lines.Count - 1].Trim()) {
            $lines.Add("")
        }
        $lines.Add("[wsl2]")
        $lines.Add("networkingMode=mirrored")
    }

    $content = [string]::Join([Environment]::NewLine, $lines) + [Environment]::NewLine
    [IO.File]::WriteAllText(
        $wslConfigPath,
        $content,
        [Text.UTF8Encoding]::new($false)
    )
    return $true
}

function Remove-IntelliTrolleyFirewallRules {
    if (Get-Command "Get-NetFirewallHyperVRule" -ErrorAction SilentlyContinue) {
        foreach ($ruleName in @(
            $script:IntelliTrolleyHyperVDdsRule,
            $script:IntelliTrolleyHyperVUiRule
        )) {
            if (Get-NetFirewallHyperVRule -Name $ruleName -ErrorAction SilentlyContinue) {
                Remove-NetFirewallHyperVRule -Name $ruleName
            }
        }
    }
    foreach ($ruleName in @(
        $script:IntelliTrolleyWindowsDdsRule,
        $script:IntelliTrolleyWindowsUiRule
    )) {
        if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
            Remove-NetFirewallRule -Name $ruleName
        }
    }
}

function Set-IntelliTrolleyFirewallRules {
    param(
        [Parameter(Mandatory = $true)][string]$RobotAddress,
        [Parameter(Mandatory = $true)][string]$RobotSubnet
    )

    if (-not (Test-IntelliTrolleyAdministrator)) {
        throw "Administrator approval is required to configure the robot firewall rules."
    }
    foreach ($commandName in @(
        "Get-NetFirewallHyperVRule",
        "New-NetFirewallHyperVRule",
        "Remove-NetFirewallHyperVRule"
    )) {
        if (-not (Get-Command $commandName -ErrorAction SilentlyContinue)) {
            throw "$commandName is unavailable. Update Windows 11 and WSL before configuring robot networking."
        }
    }

    Remove-IntelliTrolleyFirewallRules
    $uiRemoteAddresses = @($RobotSubnet, "127.0.0.1")

    New-NetFirewallHyperVRule `
        -Name $script:IntelliTrolleyHyperVDdsRule `
        -DisplayName "IntelliTrolley ROS 2 DDS (WSL)" `
        -Direction Inbound `
        -VMCreatorId $script:IntelliTrolleyWslVmCreatorId `
        -Protocol UDP `
        -LocalPorts "7400-7511" `
        -RemoteAddresses $RobotAddress `
        -Action Allow | Out-Null
    New-NetFirewallHyperVRule `
        -Name $script:IntelliTrolleyHyperVUiRule `
        -DisplayName "IntelliTrolley Mission Control (WSL)" `
        -Direction Inbound `
        -VMCreatorId $script:IntelliTrolleyWslVmCreatorId `
        -Protocol TCP `
        -LocalPorts "8000" `
        -RemoteAddresses $uiRemoteAddresses `
        -Action Allow | Out-Null
    New-NetFirewallRule `
        -Name $script:IntelliTrolleyWindowsDdsRule `
        -DisplayName "IntelliTrolley ROS 2 DDS" `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Protocol UDP `
        -LocalPort "7400-7511" `
        -RemoteAddress $RobotAddress | Out-Null
    New-NetFirewallRule `
        -Name $script:IntelliTrolleyWindowsUiRule `
        -DisplayName "IntelliTrolley Mission Control" `
        -Direction Inbound `
        -Action Allow `
        -Enabled True `
        -Profile Any `
        -Protocol TCP `
        -LocalPort "8000" `
        -RemoteAddress $uiRemoteAddresses | Out-Null
}

function Test-IntelliTrolleyFirewallRules {
    try {
        if (-not (Get-Command "Get-NetFirewallHyperVRule" -ErrorAction SilentlyContinue)) {
            return $false
        }
        foreach ($ruleName in @(
            $script:IntelliTrolleyHyperVDdsRule,
            $script:IntelliTrolleyHyperVUiRule
        )) {
            if (-not (Get-NetFirewallHyperVRule -Name $ruleName -ErrorAction SilentlyContinue)) {
                return $false
            }
        }
        foreach ($ruleName in @(
            $script:IntelliTrolleyWindowsDdsRule,
            $script:IntelliTrolleyWindowsUiRule
        )) {
            $rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
            if (-not $rule -or $rule.Enabled -ne "True") {
                return $false
            }
        }
        return $true
    }
    catch {
        return $false
    }
}

function Test-IntelliTrolleyBroadWslInboundPolicy {
    try {
        $setting = Get-NetFirewallHyperVVMSetting `
            -Name $script:IntelliTrolleyWslVmCreatorId `
            -ErrorAction Stop
        return ([string]$setting.DefaultInboundAction -eq "Allow")
    }
    catch {
        return $false
    }
}

function Get-IntelliTrolleyDistributions {
    $output = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }
    return @(
        $output |
            ForEach-Object { ($_ -replace "`0", "").Trim() } |
            Where-Object { $_ }
    )
}

function Test-IntelliTrolleyWsl2 {
    param([Parameter(Mandatory = $true)][string]$DistroName)

    $escapedName = [Regex]::Escape($DistroName)
    $rows = & wsl.exe --list --verbose 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $false
    }
    foreach ($row in $rows) {
        $cleanRow = $row -replace "`0", ""
        if ($cleanRow -match "^\s*\*?\s*$escapedName\s+\S+\s+2\s*$") {
            return $true
        }
    }
    return $false
}

function Invoke-IntelliTrolleyWsl {
    param(
        [Parameter(Mandatory = $true)][string]$DistroName,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )

    Assert-IntelliTrolleyDistroName -DistroName $DistroName
    & wsl.exe -d $DistroName -- @Arguments
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0 -and -not $AllowFailure) {
        throw "WSL command failed with exit code $exitCode."
    }
}

function Get-IntelliTrolleySettingsPath {
    $settingsDirectory = Join-Path $env:LOCALAPPDATA "IntelliTrolley"
    return Join-Path $settingsDirectory "settings.json"
}

function Get-IntelliTrolleySettings {
    $settingsPath = Get-IntelliTrolleySettingsPath
    if (-not (Test-Path -LiteralPath $settingsPath -PathType Leaf)) {
        throw "IntelliTrolley is not installed. Run Install-IntelliTrolley.ps1 first."
    }
    return Get-Content -LiteralPath $settingsPath -Raw | ConvertFrom-Json
}

function Save-IntelliTrolleySettings {
    param(
        [Parameter(Mandatory = $true)][string]$DistroName,
        [Parameter(Mandatory = $true)][string]$Version,
        [string]$RobotAddress = "",
        [string]$RobotSubnet = "",
        [int]$RosDomainId = 0
    )

    $settingsPath = Get-IntelliTrolleySettingsPath
    $settingsDirectory = Split-Path -Parent $settingsPath
    New-Item -ItemType Directory -Path $settingsDirectory -Force | Out-Null
    [ordered]@{
        distro = $DistroName
        version = $Version
        robotAddress = $RobotAddress
        robotSubnet = $RobotSubnet
        rosDomainId = $RosDomainId
        installedAtUtc = [DateTime]::UtcNow.ToString("o")
    } |
        ConvertTo-Json |
        Set-Content -LiteralPath $settingsPath -Encoding UTF8
}

function Test-IntelliTrolleyReleaseManifest {
    param([Parameter(Mandatory = $true)][string]$PackageRoot)

    $resolvedRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
    $manifestPath = Join-Path $resolvedRoot "release-manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
        throw "Package manifest is missing: $manifestPath"
    }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.algorithm -ne "SHA-256") {
        throw "Unsupported package manifest algorithm: $($manifest.algorithm)"
    }

    foreach ($entry in $manifest.files) {
        $relativePath = [string]$entry.path
        if (
            [IO.Path]::IsPathRooted($relativePath) -or
            $relativePath.Split("/") -contains ".."
        ) {
            throw "Unsafe path in package manifest: $relativePath"
        }
        $platformPath = $relativePath.Replace(
            "/",
            [string][IO.Path]::DirectorySeparatorChar
        )
        $filePath = Join-Path $resolvedRoot $platformPath
        if (-not (Test-Path -LiteralPath $filePath -PathType Leaf)) {
            throw "Package file is missing: $relativePath"
        }
        $actualHash = (Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne ([string]$entry.sha256).ToLowerInvariant()) {
            throw "Package integrity check failed: $relativePath"
        }
    }
}

function Get-IntelliTrolleyPackageRoot {
    return (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
}
