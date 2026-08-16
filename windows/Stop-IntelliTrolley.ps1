[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "IntelliTrolley.Common.ps1")

Assert-IntelliTrolleyWindows
$settings = Get-IntelliTrolleySettings
Invoke-IntelliTrolleyWsl `
    -DistroName ([string]$settings.distro) `
    -Arguments @("intellitrolley-central", "stop")
