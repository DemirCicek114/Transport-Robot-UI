[CmdletBinding()]
param(
    [ValidateSet("navigation", "mapping", "ui-only")]
    [string]$Mode = "navigation",
    [switch]$NoBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "IntelliTrolley.Common.ps1")

Assert-IntelliTrolleyWindows
$settings = Get-IntelliTrolleySettings
$distroName = [string]$settings.distro
Assert-IntelliTrolleyDistroName -DistroName $distroName

$arguments = @(
    "-d",
    $distroName,
    "--",
    "intellitrolley-central",
    "start",
    $Mode
)
$wslProcess = Start-Process `
    -FilePath "wsl.exe" `
    -ArgumentList $arguments `
    -NoNewWindow `
    -PassThru

$dashboardUrl = "http://127.0.0.1:8000/ui"
$deadline = [DateTime]::UtcNow.AddSeconds(60)
$healthy = $false
while ([DateTime]::UtcNow -lt $deadline) {
    if ($wslProcess.HasExited) {
        throw "The central stack exited during startup with code $($wslProcess.ExitCode)."
    }
    try {
        $health = Invoke-WebRequest `
            -Uri "http://127.0.0.1:8000/health" `
            -UseBasicParsing `
            -TimeoutSec 1
        if ($health.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    }
    catch {
        Start-Sleep -Milliseconds 500
    }
}

if ($healthy) {
    Write-Host "Dashboard: $dashboardUrl"
    if (-not $NoBrowser) {
        Start-Process $dashboardUrl
    }
}
else {
    Write-Warning "Mission Control did not become healthy within 60 seconds. Review the WSL console and logs."
}

Write-Host "Keep this window open while IntelliTrolley is running. Press Ctrl+C for a controlled stop."
try {
    $wslProcess.WaitForExit()
}
finally {
    if (-not $wslProcess.HasExited) {
        & wsl.exe -d $distroName -- intellitrolley-central stop
    }
}

if ($wslProcess.ExitCode -ne 0) {
    exit $wslProcess.ExitCode
}
