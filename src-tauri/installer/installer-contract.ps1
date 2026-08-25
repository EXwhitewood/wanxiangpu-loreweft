$script:LoreweftInstallerContractRoot = $PSScriptRoot

function Assert-LoreweftContractCondition {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Get-LoreweftCanonicalText {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [System.IO.File]::ReadAllText($Path).Replace("`r`n", "`n").Replace("`r", "`n")
}

function Get-LoreweftSha256FromBytes {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($Bytes))).Replace('-', '')
    }
    finally {
        $sha.Dispose()
    }
}

function Get-LoreweftFileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)

    $sha = [System.Security.Cryptography.SHA256]::Create()
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '')
    }
    finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Get-LoreweftCanonicalTextSha256 {
    param([Parameter(Mandatory = $true)][string]$Text)

    $bytes = [System.Text.UTF8Encoding]::new($false).GetBytes(
        $Text.Replace("`r`n", "`n").Replace("`r", "`n")
    )
    return Get-LoreweftSha256FromBytes -Bytes $bytes
}

function Get-LoreweftLiteralOccurrenceCount {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Needle
    )

    if ($Needle.Length -eq 0) {
        throw 'Cannot count an empty installer-contract needle.'
    }

    $count = 0
    $offset = 0
    while ($offset -lt $Text.Length) {
        $index = $Text.IndexOf($Needle, $offset, [StringComparison]::Ordinal)
        if ($index -lt 0) {
            break
        }
        $count += 1
        $offset = $index + $Needle.Length
    }
    return $count
}

function Replace-LoreweftLiteralOnce {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$OldValue,
        [Parameter(Mandatory = $true)][AllowEmptyString()][string]$NewValue,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $count = Get-LoreweftLiteralOccurrenceCount -Text $Text -Needle $OldValue
    if ($count -ne 1) {
        throw "Installer contract expected exactly one '$Label' block; found $count. Expected literal: <$OldValue>"
    }
    $index = $Text.IndexOf($OldValue, [StringComparison]::Ordinal)
    return $Text.Substring(0, $index) + $NewValue + $Text.Substring($index + $OldValue.Length)
}

function Replace-LoreweftLineByPrefixOnce {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][string]$Prefix,
        [Parameter(Mandatory = $true)][string]$NewLine,
        [Parameter(Mandatory = $true)][string]$Label
    )

    $pattern = '(?m)^' + [regex]::Escape($Prefix) + '[^\r\n]*$'
    $matches = [regex]::Matches($Text, $pattern)
    if ($matches.Count -ne 1) {
        throw "Installer contract expected exactly one '$Label' line; found $($matches.Count)."
    }
    $match = $matches[0]
    return $Text.Substring(0, $match.Index) + $NewLine + $Text.Substring($match.Index + $match.Length)
}

function Get-LoreweftInstallerContract {
    $contractPath = Join-Path $script:LoreweftInstallerContractRoot 'installer-template-contract.json'
    if (-not (Test-Path -LiteralPath $contractPath -PathType Leaf)) {
        throw "Installer template contract is missing: $contractPath"
    }
    return Get-Content -LiteralPath $contractPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Assert-LoreweftTauriNsisToolchainContract {
    param(
        [Parameter(Mandatory = $true)][string]$PackageJsonPath,
        [Parameter(Mandatory = $true)][string]$PackageLockPath,
        [Parameter(Mandatory = $true)][string]$TauriCliPath
    )

    $contract = Get-LoreweftInstallerContract
    $expectedCli = [string]$contract.tauriCliVersion
    $expectedBundler = [string]$contract.tauriBundlerVersion
    foreach ($path in @($PackageJsonPath, $PackageLockPath, $TauriCliPath)) {
        Assert-LoreweftContractCondition (Test-Path -LiteralPath $path -PathType Leaf) "Required Tauri toolchain file is missing: $path"
    }

    $package = Get-Content -LiteralPath $PackageJsonPath -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-LoreweftContractCondition (
        [string]$package.devDependencies.'@tauri-apps/cli' -ceq $expectedCli
    ) "frontend/package.json must pin @tauri-apps/cli exactly to $expectedCli."

    $lockText = Get-Content -LiteralPath $PackageLockPath -Raw -Encoding UTF8
    $rewrittenLockText = [regex]::Replace(
        $lockText,
        '("packages"\s*:\s*\{\s*)""\s*:',
        '$1"__loreweft_root__":',
        1
    )
    Assert-LoreweftContractCondition ($rewrittenLockText -cne $lockText) 'Unable to identify the package-lock.json root package entry.'
    $lock = $rewrittenLockText | ConvertFrom-Json
    $lockRoot = $lock.packages.__loreweft_root__
    $lockCli = $lock.packages.PSObject.Properties['node_modules/@tauri-apps/cli'].Value
    Assert-LoreweftContractCondition (
        [string]$lockRoot.devDependencies.'@tauri-apps/cli' -ceq $expectedCli
    ) "frontend/package-lock.json root must pin @tauri-apps/cli exactly to $expectedCli."
    Assert-LoreweftContractCondition (
        [string]$lockCli.version -ceq $expectedCli
    ) "frontend/package-lock.json resolved Tauri CLI is not $expectedCli."

    $versionOutput = (& $TauriCliPath --version | Out-String).Trim()
    Assert-LoreweftContractCondition (
        $LASTEXITCODE -eq 0 -and $versionOutput -match '^tauri-cli\s+(?<version>\d+\.\d+\.\d+)$'
    ) "Unable to determine the installed Tauri CLI version: $versionOutput"
    Assert-LoreweftContractCondition (
        [string]$Matches.version -ceq $expectedCli
    ) "Installed Tauri CLI must be exactly $expectedCli; detected $($Matches.version)."

    $frontendDirectory = Split-Path -Parent $PackageJsonPath
    $nativePackageDirectory = Join-Path $frontendDirectory 'node_modules\@tauri-apps\cli-win32-x64-msvc'
    $nativePackageJson = Join-Path $nativePackageDirectory 'package.json'
    $nativeBinary = Join-Path $nativePackageDirectory 'cli.win32-x64-msvc.node'
    Assert-LoreweftContractCondition (
        (Test-Path -LiteralPath $nativePackageJson -PathType Leaf) -and
        (Test-Path -LiteralPath $nativeBinary -PathType Leaf)
    ) 'The pinned Windows x64 native Tauri CLI package is missing.'
    $nativePackage = Get-Content -LiteralPath $nativePackageJson -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-LoreweftContractCondition (
        [string]$nativePackage.version -ceq $expectedCli
    ) "Native Windows Tauri CLI package must be exactly $expectedCli."

    $nativeText = [System.Text.Encoding]::GetEncoding(28591).GetString(
        [System.IO.File]::ReadAllBytes($nativeBinary)
    )
    Assert-LoreweftContractCondition (
        $nativeText.Contains("tauri-bundler/$expectedBundler")
    ) "Native Tauri CLI does not identify the pinned tauri-bundler/$expectedBundler."
}

function Assert-LoreweftInstallerTemplateContract {
    param([Parameter(Mandatory = $true)][string]$TauriConfigPath)

    $contract = Get-LoreweftInstallerContract
    Assert-LoreweftContractCondition (Test-Path -LiteralPath $TauriConfigPath -PathType Leaf) "Tauri config is missing: $TauriConfigPath"
    $config = Get-Content -LiteralPath $TauriConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $nsis = $config.bundle.windows.nsis
    Assert-LoreweftContractCondition (
        [string]$nsis.template -ceq 'installer/installer-template.nsi'
    ) 'tauri.conf.json must use the reviewed Loreweft NSIS template.'
    Assert-LoreweftContractCondition (
        [string]$nsis.installMode -ceq 'currentUser'
    ) 'The explicit HKCU post-install version check requires the reviewed currentUser install mode.'
    Assert-LoreweftContractCondition (
        [string]$nsis.installerHooks -ceq 'installer/installer-hooks.nsh'
    ) 'tauri.conf.json must use the reviewed Loreweft installer hooks.'
    Assert-LoreweftContractCondition (
        @($nsis.languages).Count -eq 2 -and
        [string]$nsis.languages[0] -ceq 'English' -and
        [string]$nsis.languages[1] -ceq 'SimpChinese'
    ) 'The installer language contract must be exactly English and SimpChinese.'
    Assert-LoreweftContractCondition (
        [string]$nsis.customLanguageFiles.English -ceq 'installer/languages/English.nsh' -and
        [string]$nsis.customLanguageFiles.SimpChinese -ceq 'installer/languages/SimpChinese.nsh'
    ) 'The reviewed custom installer language files are not configured.'

    $templatePath = Join-Path $script:LoreweftInstallerContractRoot ([string]$contract.template.path)
    $template = Get-LoreweftCanonicalText -Path $templatePath
    $templateHash = Get-LoreweftCanonicalTextSha256 -Text $template
    Assert-LoreweftContractCondition (
        $templateHash -ceq [string]$contract.template.customizedSha256
    ) "Customized NSIS template hash mismatch: $templateHash"

    $template = Replace-LoreweftLiteralOnce -Text $template -Label 'upgrade choices' -OldValue @'
    StrCpy $R2 "$(loreweftCleanReinstallSeparateWindow)"
    StrCpy $R3 "$(loreweftUpgradeInPlaceRecommended)"
'@ -NewValue @'
    StrCpy $R2 "$(uninstallBeforeInstalling)"
    StrCpy $R3 "$(dontUninstall)"
'@
    $template = Replace-LoreweftLiteralOnce -Text $template -Label 'upgrade default and focus' -OldValue @'
    ; Preserve an explicit selection when returning to this page. On the
    ; first interactive upgrade visit, prefer the in-place path so the old
    ; version is not removed before the replacement files are ready.
    ${If} $ReinstallPageCheck == ""
      ${If} $R0 = 1
        StrCpy $ReinstallPageCheck 2
      ${EndIf}
    ${EndIf}

    ${If} $ReinstallPageCheck = 2
      SendMessage $R3 ${BM_SETCHECK} ${BST_CHECKED} 0
      ${NSD_SetFocus} $R3
    ${Else}
      SendMessage $R2 ${BM_SETCHECK} ${BST_CHECKED} 0
      ${NSD_SetFocus} $R2
    ${EndIf}

'@ -NewValue @'
    ; Check the first radio button if this the first time
    ; we enter this page or if the second button wasn't
    ; selected the last time we were on this page
    ${If} $ReinstallPageCheck <> 2
      SendMessage $R2 ${BM_SETCHECK} ${BST_CHECKED} 0
    ${Else}
      SendMessage $R3 ${BM_SETCHECK} ${BST_CHECKED} 0
    ${EndIf}

    ${NSD_SetFocus} $R2
'@
    $template = Replace-LoreweftLiteralOnce -Text $template -Label 'separate uninstaller notice' -OldValue @'
  reinst_uninstall:
    ${If} $PassiveMode <> 1
      ${IfNot} ${Silent}
        MessageBox MB_OK|MB_ICONINFORMATION "$(loreweftSeparateUninstallerNotice)"
      ${EndIf}
    ${EndIf}

    HideWindow
'@ -NewValue @'
  reinst_uninstall:
    HideWindow
'@
    $template = Replace-LoreweftLiteralOnce -Text $template -Label 'returning installer notice' -OldValue @'
    ${EndIf}

    ${If} $PassiveMode <> 1
      ${IfNot} ${Silent}
        MessageBox MB_OK|MB_ICONINFORMATION "$(loreweftResumeInstallationAfterUninstall)"
      ${EndIf}
    ${EndIf}
  reinst_done:
'@ -NewValue @'
    ${EndIf}
  reinst_done:
'@
    $upstreamTemplateHash = Get-LoreweftCanonicalTextSha256 -Text $template
    Assert-LoreweftContractCondition (
        $upstreamTemplateHash -ceq [string]$contract.template.upstreamSha256
    ) "Customized template contains changes outside the reviewed upgrade patch; reconstructed upstream hash is $upstreamTemplateHash."

    $languageRestore = @{
        English = @{
            Prefix = @(
                'LangString choowHowToInstall ${LANG_ENGLISH} ',
                'LangString olderOrUnknownVersionInstalled ${LANG_ENGLISH} '
            )
            Upstream = @(
                'LangString choowHowToInstall ${LANG_ENGLISH} "Choose how you want to install ${PRODUCTNAME}."',
                'LangString olderOrUnknownVersionInstalled ${LANG_ENGLISH} "An $R4 version of ${PRODUCTNAME} is installed on your system. It''s recommended that you uninstall the current version before installing. Select the operation you want to perform and click Next to continue."'
            )
        }
        SimpChinese = @{
            Prefix = @(
                'LangString choowHowToInstall ${LANG_SIMPCHINESE} ',
                'LangString olderOrUnknownVersionInstalled ${LANG_SIMPCHINESE} '
            )
            UpstreamBase64 = @(
                'TGFuZ1N0cmluZyBjaG9vd0hvd1RvSW5zdGFsbCAke0xBTkdfU0lNUENISU5FU0V9ICLpgInmi6nkvaDmg7PopoHlronoo4UgJHtQUk9EVUNUTkFNRX0g55qE5pa55byP44CCIg==',
                'TGFuZ1N0cmluZyBvbGRlck9yVW5rbm93blZlcnNpb25JbnN0YWxsZWQgJHtMQU5HX1NJTVBDSElORVNFfSAi57O757uf5Lit5bey5a2Y5Zyo54mI5pys5Li6ICRSNCDnmoQgJHtQUk9EVUNUTkFNRX3jgILmjqjojZDlhYjljbjovb3lvZPliY3niYjmnKzlkI7lho3ov5vooYzlronoo4XjgILpgInmi6nkvaDmg7PopoHmiafooYznmoTmk43kvZzlkI7ngrnlh7vkuIvkuIDmraXku6Xnu6fnu63jgIIi'
            )
        }
    }

    foreach ($languageName in @('English', 'SimpChinese')) {
        $languageContract = $contract.languages.$languageName
        $languagePath = Join-Path $script:LoreweftInstallerContractRoot ([string]$languageContract.path).Replace('/', [char]92)
        $language = Get-LoreweftCanonicalText -Path $languagePath
        $languageHash = Get-LoreweftCanonicalTextSha256 -Text $language
        Assert-LoreweftContractCondition (
            $languageHash -ceq [string]$languageContract.customizedSha256
        ) "Customized $languageName installer language hash mismatch: $languageHash"

        for ($index = 0; $index -lt 2; $index += 1) {
            $upstreamValue = if ($languageName -ceq 'SimpChinese') {
                [System.Text.Encoding]::UTF8.GetString(
                    [Convert]::FromBase64String([string]$languageRestore[$languageName].UpstreamBase64[$index])
                )
            }
            else {
                [string]$languageRestore[$languageName].Upstream[$index]
            }
            $language = Replace-LoreweftLineByPrefixOnce -Text $language -Label "$languageName translated base string $index" -Prefix ([string]$languageRestore[$languageName].Prefix[$index]) -NewLine $upstreamValue
        }
        $customLines = [regex]::Matches($language, '(?m)^LangString loreweft[^\r\n]*(?:\n|$)')
        Assert-LoreweftContractCondition ($customLines.Count -eq 7) "$languageName must define exactly seven Loreweft installer messages."
        $language = [regex]::Replace($language, '(?m)^LangString loreweft[^\r\n]*(?:\n|$)', '')
        $upstreamLanguageHash = Get-LoreweftCanonicalTextSha256 -Text $language
        Assert-LoreweftContractCondition (
            $upstreamLanguageHash -ceq [string]$languageContract.upstreamSha256
        ) "$languageName contains changes outside the reviewed installer messages; reconstructed upstream hash is $upstreamLanguageHash."
    }

    $licensePath = Join-Path $script:LoreweftInstallerContractRoot ([string]$contract.license.path).Replace('/', [char]92)
    Assert-LoreweftContractCondition (
        (Get-LoreweftFileSha256 -Path $licensePath) -ceq [string]$contract.license.sha256
    ) 'The vendored Tauri MIT license hash does not match the pinned source.'

    $hooksPath = Join-Path $script:LoreweftInstallerContractRoot 'installer-hooks.nsh'
    $hooks = Get-LoreweftCanonicalText -Path $hooksPath
    $requiredHookLines = @(
        '!macro NSIS_HOOK_POSTINSTALL',
        'WriteRegStr HKCU "${UNINSTKEY}" "DisplayVersion" "${VERSION}"',
        'IfErrors loreweft_postinstall_registry_failed',
        'ReadRegStr $R0 HKCU "${UNINSTKEY}" "DisplayVersion"',
        'StrCmp $R0 "${VERSION}" loreweft_postinstall_registry_ok loreweft_postinstall_registry_failed',
        'SetErrorLevel 71',
        'Abort "$(loreweftRegistryVersionFailed)"'
    )
    foreach ($line in $requiredHookLines) {
        Assert-LoreweftContractCondition (
            (Get-LoreweftLiteralOccurrenceCount -Text $hooks -Needle $line) -eq 1
        ) "Installer hooks require exactly one occurrence of: $line"
    }

    Assert-LoreweftRenderedNsisContract -RenderedInstallerPath $templatePath
}

function Assert-LoreweftRenderedNsisContract {
    param([Parameter(Mandatory = $true)][string]$RenderedInstallerPath)

    Assert-LoreweftContractCondition (Test-Path -LiteralPath $RenderedInstallerPath -PathType Leaf) "Rendered NSIS script is missing: $RenderedInstallerPath"
    $text = Get-LoreweftCanonicalText -Path $RenderedInstallerPath

    $upgradeDefaultPattern = @'
    ${If} $ReinstallPageCheck == ""
      ${If} $R0 = 1
        StrCpy $ReinstallPageCheck 2
'@
    $updateDirectPattern = @'
  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_done
  ${EndIf}
'@
    $requiredOnce = @(
        'StrCpy $R2 "$(loreweftCleanReinstallSeparateWindow)"',
        'StrCpy $R3 "$(loreweftUpgradeInPlaceRecommended)"',
        $upgradeDefaultPattern,
        $updateDirectPattern,
        'SendMessage $R3 ${BM_SETCHECK} ${BST_CHECKED} 0',
        '${NSD_SetFocus} $R3',
        'MessageBox MB_OK|MB_ICONINFORMATION "$(loreweftSeparateUninstallerNotice)"',
        'MessageBox MB_OK|MB_ICONINFORMATION "$(loreweftResumeInstallationAfterUninstall)"',
        '!insertmacro NSIS_HOOK_PREINSTALL',
        '!insertmacro NSIS_HOOK_POSTINSTALL'
    )
    foreach ($pattern in $requiredOnce) {
        Assert-LoreweftContractCondition (
            (Get-LoreweftLiteralOccurrenceCount -Text $text -Needle $pattern) -eq 1
        ) "Rendered NSIS contract requires exactly one occurrence of: $pattern"
    }

    $updateStart = $text.IndexOf('; In update mode, always proceeds without uninstalling', [StringComparison]::Ordinal)
    $updateGoto = $text.IndexOf('Goto reinst_done', $updateStart, [StringComparison]::Ordinal)
    $uninstallLabel = $text.IndexOf('reinst_uninstall:', [StringComparison]::Ordinal)
    $uninstallNotice = $text.IndexOf('"$(loreweftSeparateUninstallerNotice)"', [StringComparison]::Ordinal)
    $hideWindow = $text.IndexOf('HideWindow', $uninstallLabel, [StringComparison]::Ordinal)
    $bringToFront = $text.IndexOf('BringToFront', $uninstallLabel, [StringComparison]::Ordinal)
    $resumeNotice = $text.IndexOf('"$(loreweftResumeInstallationAfterUninstall)"', [StringComparison]::Ordinal)
    $doneLabel = $text.IndexOf('reinst_done:', [StringComparison]::Ordinal)
    Assert-LoreweftContractCondition (
        $updateStart -ge 0 -and $updateGoto -gt $updateStart -and $updateGoto -lt $uninstallLabel
    ) '/UPDATE must jump directly to in-place installation before the old-uninstaller branch.'
    Assert-LoreweftContractCondition (
        $uninstallLabel -ge 0 -and $uninstallNotice -gt $uninstallLabel -and $uninstallNotice -lt $hideWindow
    ) 'The separate-uninstaller notice must appear before the installer window is hidden.'
    Assert-LoreweftContractCondition (
        $bringToFront -gt $hideWindow -and $resumeNotice -gt $bringToFront -and $resumeNotice -lt $doneLabel
    ) 'The returning-installer notice must appear after a successful old uninstaller and before reinst_done.'

    $preinstall = $text.IndexOf('!insertmacro NSIS_HOOK_PREINSTALL', [StringComparison]::Ordinal)
    $runningCheck = $text.IndexOf('!insertmacro CheckIfAppIsRunning', $preinstall, [StringComparison]::Ordinal)
    $copyMarker = $text.IndexOf('; Copy main executable', $preinstall, [StringComparison]::Ordinal)
    $firstApplicationFile = if ($copyMarker -ge 0) {
        $text.IndexOf('File "', $copyMarker, [StringComparison]::Ordinal)
    }
    else {
        -1
    }
    Assert-LoreweftContractCondition (
        $preinstall -ge 0 -and $runningCheck -gt $preinstall -and $copyMarker -gt $runningCheck -and $firstApplicationFile -gt $copyMarker
    ) 'NSIS PREINSTALL must run before the app-running check and the first application file copy.'

    $displayVersionWrite = $text.IndexOf('WriteRegStr SHCTX "${UNINSTKEY}" "DisplayVersion" "${VERSION}"', [StringComparison]::Ordinal)
    $postinstall = $text.IndexOf('!insertmacro NSIS_HOOK_POSTINSTALL', [StringComparison]::Ordinal)
    $installSectionEnd = $text.IndexOf('SectionEnd', $postinstall, [StringComparison]::Ordinal)
    Assert-LoreweftContractCondition (
        $displayVersionWrite -gt $firstApplicationFile -and $postinstall -gt $displayVersionWrite -and $installSectionEnd -gt $postinstall
    ) 'NSIS POSTINSTALL must rewrite and verify DisplayVersion after the normal registry writes and before the install section completes.'
}

function Resolve-LoreweftSevenZip {
    param([string]$SevenZipPath)

    $candidates = @(
        $SevenZipPath,
        $(if ($env:ProgramFiles) { Join-Path $env:ProgramFiles '7-Zip\7z.exe' }),
        $(if (${env:ProgramFiles(x86)}) { Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe' })
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $command = Get-Command 7z.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) {
        $candidates += [string]$command.Source
    }
    $resolved = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace([string]$resolved)) {
        throw '7-Zip is required to verify the installer-embedded preflight script.'
    }
    return [System.IO.Path]::GetFullPath([string]$resolved)
}

function Assert-LoreweftInstallerEmbedsExactPreflight {
    param(
        [Parameter(Mandatory = $true)][string]$InstallerPath,
        [Parameter(Mandatory = $true)][string]$PreflightPath,
        [string]$SevenZipPath
    )

    foreach ($path in @($InstallerPath, $PreflightPath)) {
        Assert-LoreweftContractCondition (Test-Path -LiteralPath $path -PathType Leaf) "Installer embedding input is missing: $path"
    }
    $sevenZip = Resolve-LoreweftSevenZip -SevenZipPath $SevenZipPath
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $sevenZip
    $startInfo.Arguments = 'e -so -y "' + $InstallerPath + '" "$PLUGINSDIR\LoreweftInstallerPreflight.ps1"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = [System.Diagnostics.Process]::Start($startInfo)
    $memory = New-Object System.IO.MemoryStream
    try {
        $process.StandardOutput.BaseStream.CopyTo($memory)
        $stderr = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        $embeddedBytes = $memory.ToArray()
        Assert-LoreweftContractCondition (
            $process.ExitCode -eq 0 -and $embeddedBytes.Length -gt 0
        ) "Unable to extract LoreweftInstallerPreflight.ps1 from the final installer. exit=$($process.ExitCode); details=$($stderr.Trim())"
    }
    finally {
        $memory.Dispose()
        $process.Dispose()
    }

    $sourceBytes = [System.IO.File]::ReadAllBytes($PreflightPath)
    $sourceHash = Get-LoreweftSha256FromBytes -Bytes $sourceBytes
    $embeddedHash = Get-LoreweftSha256FromBytes -Bytes $embeddedBytes
    Assert-LoreweftContractCondition (
        $embeddedBytes.Length -eq $sourceBytes.Length -and $embeddedHash -ceq $sourceHash
    ) "Final installer embeds a stale or modified preflight script: embedded=$embeddedHash/$($embeddedBytes.Length), source=$sourceHash/$($sourceBytes.Length)."
    Write-Host "Installer-embedded preflight verified: $embeddedHash ($($embeddedBytes.Length) bytes)"
}
