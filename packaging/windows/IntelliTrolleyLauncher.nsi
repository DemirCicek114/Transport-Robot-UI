Unicode True
Target amd64-unicode

!ifndef VERSION
  !error "VERSION must be provided by the build script."
!endif
!ifndef OUTPUT_FILE
  !error "OUTPUT_FILE must be provided by the build script."
!endif

!include "FileFunc.nsh"
!include "LogicLib.nsh"

Name "IntelliTrolley Central"
OutFile "${OUTPUT_FILE}"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
ShowInstDetails nevershow

VIProductVersion "0.1.0.1"
VIAddVersionKey /LANG=1033 "ProductName" "IntelliTrolley Central"
VIAddVersionKey /LANG=1033 "CompanyName" "IntelliTrolley"
VIAddVersionKey /LANG=1033 "FileDescription" "IntelliTrolley Central launcher"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "IntelliTrolley"

Function LaunchPowerShell
  Exch $0
  ClearErrors
  ExecShell \
    "open" \
    "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" \
    '$0' \
    SW_SHOWNORMAL
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP \
      "Windows PowerShell could not be started. Run IntelliTrolley Setup again."
    SetErrorLevel 1
  ${EndIf}
FunctionEnd

Section
  ${GetParameters} $0

  StrCmp $0 "" navigation
  StrCmp $0 "/ui-only" ui_only
  StrCmp $0 "/navigation" navigation
  StrCmp $0 "/mapping" mapping
  StrCmp $0 "/doctor" doctor
  StrCmp $0 "/network" network
  StrCmp $0 "/stop" stop
  StrCmp $0 "/install" install

  StrCpy $1 $0 17
  StrCmp $1 "intellitrolley://" provisioning

  MessageBox MB_OK|MB_ICONSTOP \
    "Unsupported IntelliTrolley launcher option: $0"
  SetErrorLevel 2
  Quit

ui_only:
  Push '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "$EXEDIR\package\windows\Start-IntelliTrolley.ps1" -Mode "ui-only"'
  Call LaunchPowerShell
  Quit

navigation:
  Push '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "$EXEDIR\package\windows\Start-IntelliTrolley.ps1" -Mode "navigation"'
  Call LaunchPowerShell
  Quit

mapping:
  Push '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "$EXEDIR\package\windows\Start-IntelliTrolley.ps1" -Mode "mapping"'
  Call LaunchPowerShell
  Quit

doctor:
  Push '-NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "$EXEDIR\package\windows\Test-IntelliTrolley.ps1"'
  Call LaunchPowerShell
  Quit

network:
  ClearErrors
  ExecShell \
    "open" \
    "http://zrpi-desktop.local:8090/" \
    "" \
    SW_SHOWNORMAL
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP \
      "The robot Wi-Fi portal could not be opened. Connect Windows to the same Wi-Fi as the Pi, then open http://zrpi-desktop.local:8090/ in a browser."
    SetErrorLevel 1
  ${EndIf}
  Quit

provisioning:
  ClearErrors
  ExecShell \
    "runas" \
    "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" \
    '-NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "$EXEDIR\package\windows\Apply-IntelliTrolleyProvisioning.ps1" -ProvisioningUri "$0"' \
    SW_SHOWNORMAL
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP \
      "Applying robot-network settings requires administrator approval."
    SetErrorLevel 1
  ${EndIf}
  Quit

stop:
  Push '-NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "$EXEDIR\package\windows\Stop-IntelliTrolley.ps1"'
  Call LaunchPowerShell
  Quit

install:
  ClearErrors
  ExecShell \
    "runas" \
    "$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" \
    '-NoLogo -NoProfile -NoExit -ExecutionPolicy Bypass -File "$EXEDIR\package\windows\Install-IntelliTrolley.ps1"' \
    SW_SHOWNORMAL
  ${If} ${Errors}
    MessageBox MB_OK|MB_ICONSTOP \
      "The IntelliTrolley installation could not be started. Administrator approval is required."
    SetErrorLevel 1
  ${EndIf}
SectionEnd
