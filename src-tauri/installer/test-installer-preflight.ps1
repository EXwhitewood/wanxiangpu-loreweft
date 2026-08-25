[CmdletBinding()]
param(
    [string]$PythonExecutable,
    [string]$MakensisExecutable
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$installerDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$preflightScript = Join-Path $installerDirectory 'installer-preflight.ps1'
$hooksFile = Join-Path $installerDirectory 'installer-hooks.nsh'
$contractScript = Join-Path $installerDirectory 'installer-contract.ps1'
$tauriConfig = Join-Path (Split-Path -Parent $installerDirectory) 'tauri.conf.json'
$temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("loreweft-installer-preflight-" + [Guid]::NewGuid().ToString('N'))
$targetProcess = $null
$unrelatedProcess = $null
$externalLockerProcess = $null
$escapeJunction = $null

. $contractScript

function Assert-Condition {
    param(
        [Parameter(Mandatory = $true)][bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Message
    )

    if (-not $Condition) {
        throw $Message
    }
}

function Assert-Throws {
    param(
        [Parameter(Mandatory = $true)][scriptblock]$Action,
        [Parameter(Mandatory = $true)][string]$Message
    )

    $threw = $false
    try {
        & $Action
    }
    catch {
        $threw = $true
    }
    Assert-Condition -Condition $threw -Message $Message
}

function Wait-ReadyFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
        [int]$TimeoutSeconds = 10
    )

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            return
        }
        if ($Process.HasExited) {
            $errorLog = $Path + '.error'
            $details = if ($Process.StartInfo.RedirectStandardError) {
                $Process.StandardError.ReadToEnd().Trim()
            }
            elseif (Test-Path -LiteralPath $errorLog -PathType Leaf) {
                (Get-Content -LiteralPath $errorLog -Raw -ErrorAction SilentlyContinue).Trim()
            }
            else {
                ''
            }
            throw "Fixture locker process exited early with code $($Process.ExitCode). $details"
        }
        Start-Sleep -Milliseconds 100
        $Process.Refresh()
    }
    throw "Fixture locker did not become ready: $Path"
}

function Start-LockerProcess {
    param(
        [Parameter(Mandatory = $true)][string]$PythonExecutable,
        [Parameter(Mandatory = $true)][string]$HelperScript,
        [Parameter(Mandatory = $true)][string]$LockedFile,
        [Parameter(Mandatory = $true)][string]$ReadyFile,
        [Parameter(Mandatory = $true)][string]$PythonHome
    )

    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $PythonExecutable
    $startInfo.Arguments = '"' + $HelperScript + '" "' + $LockedFile + '" "' + $ReadyFile + '"'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardError = $true
    $previousPythonHome = $env:PYTHONHOME
    try {
        $env:PYTHONHOME = $PythonHome
        return [System.Diagnostics.Process]::Start($startInfo)
    }
    finally {
        $env:PYTHONHOME = $previousPythonHome
    }
}

function Get-FixturePythonInfo {
    param([string]$RequestedExecutable)

    $candidates = New-Object 'System.Collections.Generic.List[string]'
    if (-not [string]::IsNullOrWhiteSpace($RequestedExecutable)) {
        $candidates.Add($RequestedExecutable)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOREWEFT_TEST_PYTHON)) {
        $candidates.Add($env:LOREWEFT_TEST_PYTHON)
    }
    $pathCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $pathCommand -and -not [string]::IsNullOrWhiteSpace([string]$pathCommand.Source)) {
        $candidates.Add([string]$pathCommand.Source)
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'))
    }
    $workspacePython = Join-Path (Split-Path -Parent (Split-Path -Parent $installerDirectory)) 'python\python.exe'
    $candidates.Add($workspacePython)

    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            continue
        }
        try {
            $json = & $candidate -c "import json,sys; print(json.dumps({'executable':sys.executable,'base_prefix':sys.base_prefix,'dll':f'python{sys.version_info.major}{sys.version_info.minor}.dll'}))"
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($json -join ''))) {
                return (($json -join '') | ConvertFrom-Json)
            }
        }
        catch {
            continue
        }
    }

    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $launcher) {
        try {
            $json = & $launcher.Source -3.12 -c "import json,sys; print(json.dumps({'executable':sys.executable,'base_prefix':sys.base_prefix,'dll':f'python{sys.version_info.major}{sys.version_info.minor}.dll'}))"
            if ($LASTEXITCODE -eq 0 -and -not [string]::IsNullOrWhiteSpace(($json -join ''))) {
                return (($json -join '') | ConvertFrom-Json)
            }
        }
        catch {
        }
    }
    throw 'Unable to locate a runnable Python runtime for the fixture. Pass -PythonExecutable or set LOREWEFT_TEST_PYTHON.'
}

try {
    foreach ($script in @($preflightScript, $MyInvocation.MyCommand.Path)) {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($script, [ref]$tokens, [ref]$errors)
        Assert-Condition -Condition ($errors.Count -eq 0) -Message "PowerShell AST errors in $script`: $($errors -join '; ')"
    }

    $config = Get-Content -LiteralPath $tauriConfig -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-Condition -Condition ($config.bundle.windows.nsis.installerHooks -eq 'installer/installer-hooks.nsh') -Message 'tauri.conf.json does not reference the installer hook.'
    Assert-LoreweftInstallerTemplateContract -TauriConfigPath $tauriConfig
    $hookText = Get-Content -LiteralPath $hooksFile -Raw -Encoding UTF8
    Assert-Condition -Condition ($hookText -match '!macro\s+NSIS_HOOK_PREINSTALL') -Message 'NSIS preinstall hook is missing.'
    Assert-Condition -Condition ($hookText -match '(?m)^\s*Abort\s+') -Message 'NSIS preinstall hook has no fail-closed Abort.'
    Assert-Condition -Condition ($hookText -match 'installer-preflight\.ps1') -Message 'NSIS hook does not embed the preflight script.'

    New-Item -ItemType Directory -Path $temporaryRoot -Force | Out-Null
    $makensisCandidates = @(
        $MakensisExecutable,
        (Join-Path $env:LOCALAPPDATA 'tauri\NSIS\makensis.exe'),
        (Join-Path $env:LOCALAPPDATA 'tauri\NSIS\Bin\makensis.exe')
    ) | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    $makensis = $makensisCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    Assert-Condition -Condition (-not [string]::IsNullOrWhiteSpace($makensis)) -Message 'makensis.exe is required to compile-check the installer hook. Pass -MakensisExecutable if Tauri stores it elsewhere.'
    $nsisFixture = Join-Path $temporaryRoot 'installer-hook-fixture.nsi'
    $nsisOutput = Join-Path $temporaryRoot 'installer-hook-fixture.exe'
    @"
Unicode true
Name "Loreweft installer hook fixture"
OutFile "$nsisOutput"
InstallDir "`$TEMP\LoreweftFixture"
RequestExecutionLevel user
!include MUI2.nsh
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"
LangString loreweftPreflightLaunchFailed `${LANG_ENGLISH} "Safe upgrade check failed to start."
LangString loreweftPreflightFailed `${LANG_ENGLISH} "Safe upgrade check failed."
!include "$hooksFile"
Section
  !insertmacro NSIS_HOOK_PREINSTALL
SectionEnd
"@ | Set-Content -LiteralPath $nsisFixture -Encoding UTF8
    & $makensis /V2 $nsisFixture
    Assert-Condition -Condition ($LASTEXITCODE -eq 0) -Message "NSIS installer hook failed to compile; exit=$LASTEXITCODE."
    Assert-Condition -Condition (Test-Path -LiteralPath $nsisOutput -PathType Leaf) -Message 'NSIS compile did not produce the fixture installer.'
    Assert-LoreweftInstallerEmbedsExactPreflight -InstallerPath $nsisOutput -PreflightPath $preflightScript

    $templateText = Get-LoreweftCanonicalText -Path (Join-Path $installerDirectory 'installer-template.nsi')
    $brokenUpgradeDefault = Replace-LoreweftLiteralOnce -Text $templateText -Label 'fixture upgrade default' -OldValue @'
        StrCpy $ReinstallPageCheck 2
'@ -NewValue @'
        StrCpy $ReinstallPageCheck 1
'@
    $brokenUpdatePath = Replace-LoreweftLiteralOnce -Text $templateText -Label 'fixture update path' -OldValue @'
  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_done
'@ -NewValue @'
  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_uninstall
'@
    $brokenNotice = Replace-LoreweftLiteralOnce -Text $templateText -Label 'fixture separate uninstaller notice' -OldValue @'
        MessageBox MB_OK|MB_ICONINFORMATION "$(loreweftSeparateUninstallerNotice)"
'@ -NewValue ''
    $withoutPreinstall = Replace-LoreweftLiteralOnce -Text $templateText -Label 'fixture preinstall removal' -OldValue @'
    !insertmacro NSIS_HOOK_PREINSTALL
'@ -NewValue ''
    $brokenPreinstallOrder = Replace-LoreweftLiteralOnce -Text $withoutPreinstall -Label 'fixture late preinstall' -OldValue @'
  File "${MAINBINARYSRCPATH}"
'@ -NewValue @'
  File "${MAINBINARYSRCPATH}"
  !insertmacro NSIS_HOOK_PREINSTALL
'@
    $mutations = @(
        @{
            Name = 'upgrade default'
            Text = $brokenUpgradeDefault
        },
        @{
            Name = '/UPDATE direct path'
            Text = $brokenUpdatePath
        },
        @{
            Name = 'separate uninstaller notice'
            Text = $brokenNotice
        },
        @{
            Name = 'PREINSTALL ordering'
            Text = $brokenPreinstallOrder
        }
    )
    foreach ($mutation in $mutations) {
        $mutationPath = Join-Path $temporaryRoot ("mutated-" + ($mutation.Name -replace '[^A-Za-z0-9]+', '-') + '.nsi')
        [System.IO.File]::WriteAllText($mutationPath, [string]$mutation.Text, [System.Text.UTF8Encoding]::new($false))
        Assert-Throws -Message "Rendered NSIS contract accepted a broken $($mutation.Name)." -Action {
            Assert-LoreweftRenderedNsisContract -RenderedInstallerPath $mutationPath
        }
    }

    # A partially removed or brand-new target may contain a python directory
    # with no executable runtime files. This must be a successful no-op rather
    # than an empty-array parameter binding failure that blocks recovery.
    $emptyInstallDirectory = Join-Path $temporaryRoot 'empty-installed\Loreweft'
    $emptyPythonDirectory = Join-Path $emptyInstallDirectory 'python\__pycache__'
    New-Item -ItemType Directory -Path $emptyPythonDirectory -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $emptyPythonDirectory 'residual.pyc') -Value 'fixture' -Encoding ASCII
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $preflightScript -InstallDirectory $emptyInstallDirectory -TimeoutSeconds 2 -PollIntervalMilliseconds 50 -StableIntervalMilliseconds 100
    Assert-Condition -Condition ($LASTEXITCODE -eq 0) -Message "Preflight should allow a residual Python tree with no protected runtime files; exit=$LASTEXITCODE."

    $installDirectory = Join-Path $temporaryRoot 'installed\Loreweft'
    $pythonDirectory = Join-Path $installDirectory 'python'
    $runtimeDirectory = Join-Path $pythonDirectory 'Lib\site-packages\greenlet'
    New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null

    $pythonInfo = Get-FixturePythonInfo -RequestedExecutable $PythonExecutable
    $sourcePython = [System.IO.Path]::GetFullPath([string]$pythonInfo.executable)
    $pythonHome = [System.IO.Path]::GetFullPath([string]$pythonInfo.base_prefix)
    $sourceDll = Join-Path $pythonHome ([string]$pythonInfo.dll)
    Assert-Condition -Condition (Test-Path -LiteralPath $sourcePython -PathType Leaf) -Message 'Current Python executable does not exist.'
    Assert-Condition -Condition (Test-Path -LiteralPath $sourceDll -PathType Leaf) -Message 'Current Python DLL does not exist.'

    $bundledPython = Join-Path $pythonDirectory 'python.exe'
    foreach ($runtimeFile in Get-ChildItem -LiteralPath $pythonHome -File -Force) {
        Copy-Item -LiteralPath $runtimeFile.FullName -Destination (Join-Path $pythonDirectory $runtimeFile.Name)
    }
    if (-not (Test-Path -LiteralPath $bundledPython -PathType Leaf)) {
        Copy-Item -LiteralPath $sourcePython -Destination $bundledPython
    }

    $helperScript = Join-Path $temporaryRoot 'hold-exclusive-file.py'
    @'
import ctypes
import pathlib
import sys
import time
import traceback
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
create_file = kernel32.CreateFileW
create_file.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
create_file.restype = wintypes.HANDLE
close_handle = kernel32.CloseHandle
close_handle.argtypes = [wintypes.HANDLE]
close_handle.restype = wintypes.BOOL

try:
    handle = create_file(sys.argv[1], 0x80000000, 0, None, 3, 0x80, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    pathlib.Path(sys.argv[2]).write_text("ready", encoding="utf-8")
    try:
        time.sleep(120)
    finally:
        close_handle(handle)
except Exception:
    pathlib.Path(sys.argv[2] + ".error").write_text(traceback.format_exc(), encoding="utf-8")
    raise
'@ | Set-Content -LiteralPath $helperScript -Encoding UTF8

    $targetLock = Join-Path $runtimeDirectory '_greenlet-fixture.pyd'
    $unrelatedLock = Join-Path $temporaryRoot 'unrelated-lock.pyd'
    [System.IO.File]::WriteAllBytes($targetLock, [byte[]](1, 2, 3, 4))
    [System.IO.File]::WriteAllBytes($unrelatedLock, [byte[]](5, 6, 7, 8))
    $targetReady = Join-Path $temporaryRoot 'target.ready'
    $unrelatedReady = Join-Path $temporaryRoot 'unrelated.ready'

    $targetProcess = Start-LockerProcess -PythonExecutable $bundledPython -HelperScript $helperScript -LockedFile $targetLock -ReadyFile $targetReady -PythonHome $pythonHome
    $unrelatedProcess = Start-LockerProcess -PythonExecutable $sourcePython -HelperScript $helperScript -LockedFile $unrelatedLock -ReadyFile $unrelatedReady -PythonHome $pythonHome
    Wait-ReadyFile -Path $targetReady -Process $targetProcess
    Wait-ReadyFile -Path $unrelatedReady -Process $unrelatedProcess

    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $preflightScript -InstallDirectory $installDirectory -TimeoutSeconds 10 -PollIntervalMilliseconds 100 -StableIntervalMilliseconds 250
    Assert-Condition -Condition ($LASTEXITCODE -eq 0) -Message "Preflight should succeed after stopping bundled Python; exit=$LASTEXITCODE."
    $targetProcess.WaitForExit(5000) | Out-Null
    $targetProcess.Refresh()
    $unrelatedProcess.Refresh()
    Assert-Condition -Condition $targetProcess.HasExited -Message 'Bundled Python fixture process was not terminated.'
    Assert-Condition -Condition (-not $unrelatedProcess.HasExited) -Message 'Unrelated system Python was terminated.'

    # A system Python locking a file under the install runtime must not be killed.
    Remove-Item -LiteralPath $targetReady -Force -ErrorAction SilentlyContinue
    $externalLockerProcess = Start-LockerProcess -PythonExecutable $sourcePython -HelperScript $helperScript -LockedFile $targetLock -ReadyFile $targetReady -PythonHome $pythonHome
    Wait-ReadyFile -Path $targetReady -Process $externalLockerProcess

    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $preflightScript -InstallDirectory $installDirectory -TimeoutSeconds 2 -PollIntervalMilliseconds 100 -StableIntervalMilliseconds 250
    Assert-Condition -Condition ($LASTEXITCODE -eq 20) -Message "Preflight must fail closed while an external process holds a runtime lock; exit=$LASTEXITCODE."
    $externalLockerProcess.Refresh()
    Assert-Condition -Condition (-not $externalLockerProcess.HasExited) -Message 'External system Python holding a runtime lock was incorrectly terminated.'

    # A crafted python junction must never expand the process-kill or lock-scan boundary.
    $junctionInstall = Join-Path $temporaryRoot 'junction-install'
    $externalRuntime = Join-Path $temporaryRoot 'outside-install-runtime'
    New-Item -ItemType Directory -Path $junctionInstall -Force | Out-Null
    New-Item -ItemType Directory -Path $externalRuntime -Force | Out-Null
    $escapeJunction = Join-Path $junctionInstall 'python'
    New-Item -ItemType Junction -Path $escapeJunction -Target $externalRuntime | Out-Null
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $preflightScript -InstallDirectory $junctionInstall -TimeoutSeconds 2 -PollIntervalMilliseconds 100 -StableIntervalMilliseconds 250
    Assert-Condition -Condition ($LASTEXITCODE -eq 11) -Message "Preflight must reject a bundled-Python junction that escapes the install root; exit=$LASTEXITCODE."

    Write-Output 'installer preflight fixtures passed: AST/toolchain-pinned template/rendered negative contracts/final EXE embedding, empty residual runtime recovery, bundled-process stop, unrelated-process isolation, locked-file fail-closed behavior, and junction escape rejection.'
}
finally {
    foreach ($process in @($targetProcess, $unrelatedProcess, $externalLockerProcess)) {
        if ($null -ne $process) {
            try {
                $process.Refresh()
                if (-not $process.HasExited) {
                    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
                    $process.WaitForExit(5000) | Out-Null
                }
                $process.Dispose()
            }
            catch {
            }
        }
    }
    if ($null -ne $escapeJunction -and (Test-Path -LiteralPath $escapeJunction)) {
        Remove-Item -LiteralPath $escapeJunction -Force -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $temporaryRoot) {
        Remove-Item -LiteralPath $temporaryRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
