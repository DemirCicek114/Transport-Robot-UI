Unicode True
Target amd64-unicode

!ifndef VERSION
  !error "VERSION must be provided by the build script."
!endif
!ifndef OUTPUT_FILE
  !error "OUTPUT_FILE must be provided by the build script."
!endif
!ifndef PACKAGE_ROOT
  !error "PACKAGE_ROOT must be provided by the build script."
!endif
!ifndef LAUNCHER_EXE
  !error "LAUNCHER_EXE must be provided by the build script."
!endif

!include "LogicLib.nsh"
!include "MUI2.nsh"
!include "nsDialogs.nsh"

!define PRODUCT_NAME "IntelliTrolley Central"
!define PRODUCT_PUBLISHER "IntelliTrolley"
!define PRODUCT_UNINSTALL_KEY \
  "Software\Microsoft\Windows\CurrentVersion\Uninstall\IntelliTrolleyCentral"

Var ConfigureNetworkCheckbox
Var RobotAddressField
Var RobotSubnetField
Var RosDomainField
Var ConfigureNetwork
Var RobotAddress
Var RobotSubnet
Var RosDomainId

Name "${PRODUCT_NAME}"
OutFile "${OUTPUT_FILE}"
InstallDir "$PROGRAMFILES64\IntelliTrolley Central"
InstallDirRegKey HKLM "${PRODUCT_UNINSTALL_KEY}" "InstallLocation"
RequestExecutionLevel admin
SetCompressor /SOLID lzma
SetCompressorDictSize 32
ShowInstDetails show
ShowUninstDetails show

VIProductVersion "0.1.0.1"
VIAddVersionKey /LANG=1033 "ProductName" "${PRODUCT_NAME}"
VIAddVersionKey /LANG=1033 "CompanyName" "${PRODUCT_PUBLISHER}"
VIAddVersionKey /LANG=1033 "FileDescription" "IntelliTrolley Central setup"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "ProductVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "IntelliTrolley"

!define MUI_ABORTWARNING
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom RobotNetworkPage RobotNetworkPageLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Function RobotNetworkPage
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  ${NSD_CreateLabel} 0 0 100% 28u \
    "Configure the private robot network. Setup requests administrator approval and creates firewall rules limited to this robot and subnet."
  Pop $0

  ${NSD_CreateCheckbox} 0 34u 100% 12u \
    "Configure mirrored WSL networking, DDS peer, and scoped firewall rules"
  Pop $ConfigureNetworkCheckbox
  ${NSD_Check} $ConfigureNetworkCheckbox

  ${NSD_CreateLabel} 0 54u 38% 12u "Raspberry Pi IPv4 address:"
  Pop $0
  ${NSD_CreateText} 40% 51u 60% 13u "172.20.10.9"
  Pop $RobotAddressField

  ${NSD_CreateLabel} 0 76u 38% 12u "Robot network CIDR:"
  Pop $0
  ${NSD_CreateText} 40% 73u 60% 13u "172.20.10.0/28"
  Pop $RobotSubnetField

  ${NSD_CreateLabel} 0 98u 38% 12u "ROS domain ID:"
  Pop $0
  ${NSD_CreateText} 40% 95u 60% 13u "0"
  Pop $RosDomainField

  ${NSD_CreateLabel} 0 121u 100% 34u \
    "The central installer does not restart the physical robot automatically. After setup, use 'Configure Robot Wi-Fi' to open the Pi network portal in your default browser."
  Pop $0

  nsDialogs::Show
FunctionEnd

Function RobotNetworkPageLeave
  ${NSD_GetState} $ConfigureNetworkCheckbox $ConfigureNetwork
  ${NSD_GetText} $RobotAddressField $RobotAddress
  ${NSD_GetText} $RobotSubnetField $RobotSubnet
  ${NSD_GetText} $RosDomainField $RosDomainId

  ${If} $ConfigureNetwork == ${BST_CHECKED}
    StrCmp $RobotAddress "" invalid_network
    StrCmp $RobotSubnet "" invalid_network
    StrCmp $RosDomainId "" invalid_network
  ${EndIf}
  Return

invalid_network:
  MessageBox MB_OK|MB_ICONEXCLAMATION \
    "Robot address, robot subnet, and ROS domain ID are required when robot networking is enabled."
  Abort
FunctionEnd

Section "IntelliTrolley Central" SEC_MAIN
  SectionIn RO
  SetRegView 64
  SetShellVarContext current

  SetOutPath "$INSTDIR\package"
  File /r "${PACKAGE_ROOT}\*"

  SetOutPath "$INSTDIR"
  File /oname=IntelliTrolley-Central.exe "${LAUNCHER_EXE}"
  WriteUninstaller "$INSTDIR\Uninstall.exe"

  CreateDirectory "$SMPROGRAMS\IntelliTrolley Central"
  ; Remove shortcuts created by older preview installers during an upgrade.
  Delete "$DESKTOP\IntelliTrolley Central UI Test.lnk"
  Delete "$SMPROGRAMS\IntelliTrolley Central\IntelliTrolley Central UI Test.lnk"
  Delete "$SMPROGRAMS\IntelliTrolley Central\Configure Robot Network.lnk"
  Delete "$SMPROGRAMS\IntelliTrolley Central\Start Mapping.lnk"
  CreateShortCut \
    "$SMPROGRAMS\IntelliTrolley Central\Start Navigation.lnk" \
    "$INSTDIR\IntelliTrolley-Central.exe" \
    "/navigation"
  CreateShortCut \
    "$SMPROGRAMS\IntelliTrolley Central\Diagnostics.lnk" \
    "$INSTDIR\IntelliTrolley-Central.exe" \
    "/doctor"
  CreateShortCut \
    "$SMPROGRAMS\IntelliTrolley Central\Configure Robot Wi-Fi.lnk" \
    "$INSTDIR\IntelliTrolley-Central.exe" \
    "/network"
  CreateShortCut \
    "$SMPROGRAMS\IntelliTrolley Central\Stop IntelliTrolley.lnk" \
    "$INSTDIR\IntelliTrolley-Central.exe" \
    "/stop"
  CreateShortCut \
    "$SMPROGRAMS\IntelliTrolley Central\Finish or Repair Installation.lnk" \
    "$INSTDIR\IntelliTrolley-Central.exe" \
    "/install"
  CreateShortCut \
    "$SMPROGRAMS\IntelliTrolley Central\Uninstall.lnk" \
    "$INSTDIR\Uninstall.exe"

  WriteRegStr HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "DisplayName" "${PRODUCT_NAME}"
  WriteRegStr HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "DisplayIcon" "$INSTDIR\IntelliTrolley-Central.exe"
  WriteRegStr HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "NoModify" 1
  WriteRegDWORD HKLM "${PRODUCT_UNINSTALL_KEY}" \
    "NoRepair" 1

  WriteRegStr HKLM "Software\Classes\intellitrolley" "" \
    "URL:IntelliTrolley robot-network configuration"
  WriteRegStr HKLM "Software\Classes\intellitrolley" "URL Protocol" ""
  WriteRegStr HKLM "Software\Classes\intellitrolley\DefaultIcon" "" \
    "$INSTDIR\IntelliTrolley-Central.exe,0"
  WriteRegStr HKLM "Software\Classes\intellitrolley\shell\open\command" "" \
    '"$INSTDIR\IntelliTrolley-Central.exe" "%1"'

  DetailPrint "Verifying and installing the WSL/ROS payload..."
  ${If} $ConfigureNetwork == ${BST_CHECKED}
    ExecWait \
      '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\package\windows\Install-IntelliTrolley.ps1" -RobotAddress "$RobotAddress" -RobotSubnet "$RobotSubnet" -RosDomainId "$RosDomainId"' \
      $0
  ${Else}
    ExecWait \
      '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\package\windows\Install-IntelliTrolley.ps1" -SkipRobotNetworking' \
      $0
  ${EndIf}

  ${If} $0 == 0
    DetailPrint "IntelliTrolley Central ${VERSION} installed successfully."
  ${ElseIf} $0 == 3010
    MessageBox MB_OK|MB_ICONEXCLAMATION \
      "Windows or Ubuntu needs one manual initialization step.$\r$\n$\r$\nRestart Windows if requested, open Ubuntu 22.04 once to create its Linux username, then use 'Finish or Repair Installation' from the Start menu."
    SetErrorLevel 3010
  ${Else}
    MessageBox MB_OK|MB_ICONSTOP \
      "The WSL/ROS setup exited with code $0.$\r$\n$\r$\nThe installer files were kept. Use 'Finish or Repair Installation' from the Start menu after correcting the reported problem."
    SetErrorLevel $0
  ${EndIf}
SectionEnd

Section "Uninstall"
  SetRegView 64
  SetShellVarContext current

  StrCpy $1 "-Force"
  MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 \
    "Also permanently delete IntelliTrolley maps, destinations, mission history, and logs?$\r$\n$\r$\nChoose No to preserve them." \
    IDNO keep_data
  StrCpy $1 "-Force -RemoveData"

keep_data:
  IfFileExists \
    "$LOCALAPPDATA\IntelliTrolley\settings.json" \
    run_linux_uninstall \
    remove_windows_install

run_linux_uninstall:
  ExecWait \
    '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\package\windows\Uninstall-IntelliTrolley.ps1" $1' \
    $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP \
      "The WSL application could not be removed (exit code $0). The Windows files were left in place so the uninstall can be retried safely."
    Abort
  ${EndIf}

remove_windows_install:
  Delete "$DESKTOP\IntelliTrolley Central UI Test.lnk"
  Delete "$SMPROGRAMS\IntelliTrolley Central\IntelliTrolley Central UI Test.lnk"
  Delete "$SMPROGRAMS\IntelliTrolley Central\Configure Robot Network.lnk"
  RMDir /r "$SMPROGRAMS\IntelliTrolley Central"
  DeleteRegKey HKLM "${PRODUCT_UNINSTALL_KEY}"
  DeleteRegKey HKLM "Software\Classes\intellitrolley"
  RMDir /r "$INSTDIR"
SectionEnd
