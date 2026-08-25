!define LOREWEFT_INSTALLER_HOOKS_DIR "${__FILEDIR__}"

!macro NSIS_HOOK_PREINSTALL
  InitPluginsDir
  File /oname=$PLUGINSDIR\LoreweftInstallerPreflight.ps1 "${LOREWEFT_INSTALLER_HOOKS_DIR}\installer-preflight.ps1"

  ClearErrors
  DetailPrint "Stopping the bundled Loreweft background service before upgrade..."
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "$PLUGINSDIR\LoreweftInstallerPreflight.ps1" -InstallDirectory "$INSTDIR"' $0
  IfErrors loreweft_preinstall_launch_failed
  IntCmp $0 0 loreweft_preinstall_ok loreweft_preinstall_failed loreweft_preinstall_failed

  loreweft_preinstall_launch_failed:
    SetErrorLevel 70
    Abort "$(loreweftPreflightLaunchFailed)"

  loreweft_preinstall_failed:
    SetErrorLevel $0
    Abort "$(loreweftPreflightFailed)"

  loreweft_preinstall_ok:
    DetailPrint "Loreweft background service stopped; continuing installation."
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ; The desktop app is installed per-user. Rewrite the version through the
  ; explicit HKCU uninstall key at the end of the install and verify it before
  ; reporting success. A stale DisplayVersion makes future upgrades compare
  ; against the wrong version even when the new binary was copied correctly.
  ClearErrors
  WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"
  IfErrors loreweft_postinstall_registry_failed

  ReadRegStr $R0 HKCU "${UNINSTKEY}" "DisplayVersion"
  StrCmp $R0 "${VERSION}" loreweft_postinstall_registry_ok loreweft_postinstall_registry_failed

  loreweft_postinstall_registry_failed:
    SetErrorLevel 71
    Abort "$(loreweftRegistryVersionFailed)"

  loreweft_postinstall_registry_ok:
    DetailPrint "Registered Loreweft ${VERSION} for Add/Remove Programs."
!macroend
