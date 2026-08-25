[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$InstallDirectory,

    [ValidateRange(1, 120)]
    [int]$TimeoutSeconds = 20,

    [ValidateRange(25, 2000)]
    [int]$PollIntervalMilliseconds = 200,

    [ValidateRange(100, 5000)]
    [int]$StableIntervalMilliseconds = 500
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$exitCode = 99
$logDirectory = Join-Path ([System.IO.Path]::GetTempPath()) 'Loreweft'
$logPath = Join-Path $logDirectory 'installer-preflight.log'

function Write-PreflightLog {
    param([Parameter(Mandatory = $true)][string]$Message)

    try {
        if (-not (Test-Path -LiteralPath $logDirectory -PathType Container)) {
            New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
        }
        $timestamp = [DateTime]::UtcNow.ToString('o')
        Add-Content -LiteralPath $logPath -Value "[$timestamp] $Message" -Encoding UTF8
    }
    catch {
        # Logging must never turn a safe installer refusal into an unsafe continuation.
    }
}

if (-not ('Loreweft.Installer.NativePath' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;

namespace Loreweft.Installer
{
    public static class NativePath
    {
        private const uint FILE_SHARE_READ = 0x00000001;
        private const uint FILE_SHARE_WRITE = 0x00000002;
        private const uint FILE_SHARE_DELETE = 0x00000004;
        private const uint OPEN_EXISTING = 3;
        private const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern SafeFileHandle CreateFileW(
            string fileName,
            uint desiredAccess,
            uint shareMode,
            IntPtr securityAttributes,
            uint creationDisposition,
            uint flagsAndAttributes,
            IntPtr templateFile);

        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        private static extern uint GetFinalPathNameByHandleW(
            SafeFileHandle file,
            StringBuilder filePath,
            uint filePathLength,
            uint flags);

        public static string GetFinalPath(string path)
        {
            using (SafeFileHandle handle = CreateFileW(
                path,
                0,
                FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                IntPtr.Zero,
                OPEN_EXISTING,
                FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero))
            {
                if (handle.IsInvalid)
                {
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "Unable to open path for canonicalization.");
                }

                uint capacity = 512;
                while (true)
                {
                    StringBuilder buffer = new StringBuilder((int)capacity);
                    uint length = GetFinalPathNameByHandleW(handle, buffer, capacity, 0);
                    if (length == 0)
                    {
                        throw new Win32Exception(Marshal.GetLastWin32Error(), "Unable to resolve final path.");
                    }
                    if (length < capacity)
                    {
                        return buffer.ToString();
                    }
                    capacity = length + 1;
                }
            }
        }
    }
}
'@
}

function ConvertFrom-ExtendedPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    if ($Path.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
        return '\\' + $Path.Substring(8)
    }
    if ($Path.StartsWith('\\?\', [StringComparison]::OrdinalIgnoreCase)) {
        return $Path.Substring(4)
    }
    return $Path
}

function Get-NormalizedFullPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [switch]$RequireExisting
    )

    $fullPath = [System.IO.Path]::GetFullPath($Path)
    if ($RequireExisting -and -not (Test-Path -LiteralPath $fullPath)) {
        throw "Required path does not exist: $fullPath"
    }

    if (Test-Path -LiteralPath $fullPath) {
        $fullPath = [Loreweft.Installer.NativePath]::GetFinalPath($fullPath)
        $fullPath = ConvertFrom-ExtendedPath -Path $fullPath
        $fullPath = [System.IO.Path]::GetFullPath($fullPath)
    }

    $root = [System.IO.Path]::GetPathRoot($fullPath)
    if ($fullPath.Length -gt $root.Length) {
        $fullPath = $fullPath.TrimEnd([char[]]@('\', '/'))
    }
    return $fullPath
}

function Test-PathWithinRoot {
    param(
        [Parameter(Mandatory = $true)][string]$Candidate,
        [Parameter(Mandatory = $true)][string]$Root,
        [switch]$AllowEqual
    )

    if ($AllowEqual -and $Candidate.Equals($Root, [StringComparison]::OrdinalIgnoreCase)) {
        return $true
    }
    $prefix = $Root.TrimEnd([char[]]@('\', '/')) + [System.IO.Path]::DirectorySeparatorChar
    return $Candidate.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)
}

function Get-ProtectedRuntimeFiles {
    param([Parameter(Mandatory = $true)][string]$PythonRoot)

    $extensions = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    [void]$extensions.Add('.exe')
    [void]$extensions.Add('.dll')
    [void]$extensions.Add('.pyd')

    $files = New-Object 'System.Collections.Generic.List[string]'
    $pending = New-Object 'System.Collections.Generic.Stack[string]'
    $visited = New-Object 'System.Collections.Generic.HashSet[string]' ([StringComparer]::OrdinalIgnoreCase)
    $pending.Push($PythonRoot)

    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        $canonicalDirectory = Get-NormalizedFullPath -Path $directory -RequireExisting
        if (-not (Test-PathWithinRoot -Candidate $canonicalDirectory -Root $PythonRoot -AllowEqual)) {
            throw "Runtime directory escapes the bundled Python root: $directory"
        }
        if (-not $visited.Add($canonicalDirectory)) {
            throw "Runtime directory cycle detected: $directory"
        }

        foreach ($entry in Get-ChildItem -LiteralPath $directory -Force -ErrorAction Stop) {
            if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Reparse points are not allowed inside the bundled Python runtime: $($entry.FullName)"
            }
            if ($entry.PSIsContainer) {
                $pending.Push($entry.FullName)
            }
            elseif ($extensions.Contains($entry.Extension)) {
                $canonicalFile = Get-NormalizedFullPath -Path $entry.FullName -RequireExisting
                if (-not (Test-PathWithinRoot -Candidate $canonicalFile -Root $PythonRoot)) {
                    throw "Runtime file escapes the bundled Python root: $($entry.FullName)"
                }
                $files.Add($canonicalFile)
            }
        }
    }

    return $files.ToArray()
}

function Get-BundledPythonProcesses {
    param([Parameter(Mandatory = $true)][string]$PythonRoot)

    $matches = New-Object 'System.Collections.Generic.List[object]'
    $processes = Get-Process -Name 'python', 'pythonw' -ErrorAction SilentlyContinue
    foreach ($process in $processes) {
        try {
            $process.Refresh()
            $rawPath = [string]$process.Path
            if ([string]::IsNullOrWhiteSpace($rawPath)) {
                continue
            }
            $executablePath = Get-NormalizedFullPath -Path $rawPath -RequireExisting
            $startTimeUtcTicks = $process.StartTime.ToUniversalTime().Ticks
        }
        catch {
            Write-PreflightLog "Skipped PID $($process.Id): executable path could not be canonicalized."
            continue
        }
        if (Test-PathWithinRoot -Candidate $executablePath -Root $PythonRoot) {
            $matches.Add([pscustomobject]@{
                ProcessId = [uint32]$process.Id
                ExecutablePath = $executablePath
                StartTimeUtcTicks = $startTimeUtcTicks
            })
        }
    }
    return $matches.ToArray()
}

function Stop-BundledPythonProcesses {
    param(
        [Parameter(Mandatory = $true)][object[]]$Processes,
        [Parameter(Mandatory = $true)][string]$PythonRoot
    )

    foreach ($process in $Processes) {
        try {
            # Re-read and revalidate immediately before termination to narrow the PID-reuse race.
            $current = Get-Process -Id ([int]$process.ProcessId) -ErrorAction Stop
            $current.Refresh()
            $currentPath = Get-NormalizedFullPath -Path ([string]$current.Path) -RequireExisting
            if (-not $currentPath.Equals($process.ExecutablePath, [StringComparison]::OrdinalIgnoreCase)) {
                Write-PreflightLog "Refused to terminate PID $($process.ProcessId): process identity changed."
                continue
            }
            if ($current.StartTime.ToUniversalTime().Ticks -ne $process.StartTimeUtcTicks) {
                Write-PreflightLog "Refused to terminate PID $($process.ProcessId): process start time changed."
                continue
            }
            if (-not (Test-PathWithinRoot -Candidate $currentPath -Root $PythonRoot)) {
                Write-PreflightLog "Refused to terminate PID $($process.ProcessId): executable is outside bundled Python."
                continue
            }

            Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction Stop
            Write-PreflightLog "Requested termination of bundled Python PID $($process.ProcessId)."
        }
        catch {
            if ($null -eq (Get-Process -Id ([int]$process.ProcessId) -ErrorAction SilentlyContinue)) {
                Write-PreflightLog "Bundled Python PID $($process.ProcessId) exited during verification."
            }
            else {
                Write-PreflightLog "Failed to terminate bundled Python PID $($process.ProcessId): $($_.Exception.Message)"
            }
        }
    }
}

function Get-LockedRuntimeFiles {
    param(
        [Parameter(Mandatory = $true)]
        [AllowEmptyCollection()]
        [string[]]$Files
    )

    $locked = New-Object 'System.Collections.Generic.List[string]'
    foreach ($file in $Files) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            continue
        }
        $stream = $null
        try {
            $stream = New-Object System.IO.FileStream(
                $file,
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::ReadWrite,
                [System.IO.FileShare]::None
            )
        }
        catch {
            $locked.Add($file)
        }
        finally {
            if ($null -ne $stream) {
                $stream.Dispose()
            }
        }
    }
    return $locked.ToArray()
}

try {
    $installRoot = Get-NormalizedFullPath -Path $InstallDirectory
    $installVolumeRoot = [System.IO.Path]::GetPathRoot($installRoot).TrimEnd([char[]]@('\', '/'))
    if ($installRoot.TrimEnd([char[]]@('\', '/')).Equals($installVolumeRoot, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Refusing to inspect a drive root as an installation directory.'
    }

    if (-not (Test-Path -LiteralPath $installRoot -PathType Container)) {
        Write-PreflightLog "No previous installation directory exists; preflight is complete."
        $exitCode = 0
        exit $exitCode
    }

    $pythonLexicalRoot = Join-Path $installRoot 'python'
    if (-not (Test-Path -LiteralPath $pythonLexicalRoot -PathType Container)) {
        Write-PreflightLog "No previous bundled Python runtime exists; preflight is complete."
        $exitCode = 0
        exit $exitCode
    }

    $pythonRoot = Get-NormalizedFullPath -Path $pythonLexicalRoot -RequireExisting
    if (-not (Test-PathWithinRoot -Candidate $pythonRoot -Root $installRoot)) {
        Write-PreflightLog 'Bundled Python resolves outside the installation directory; upgrade refused.'
        $exitCode = 11
        exit $exitCode
    }

    $protectedFiles = @(Get-ProtectedRuntimeFiles -PythonRoot $pythonRoot)
    Write-PreflightLog "Preflight started for $pythonRoot with $($protectedFiles.Count) protected runtime files."

    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    $cleanSince = $null
    do {
        $bundledProcesses = @(Get-BundledPythonProcesses -PythonRoot $pythonRoot)
        if ($bundledProcesses.Count -gt 0) {
            Stop-BundledPythonProcesses -Processes $bundledProcesses -PythonRoot $pythonRoot
        }

        $remainingProcesses = @(Get-BundledPythonProcesses -PythonRoot $pythonRoot)
        $lockedFiles = @(Get-LockedRuntimeFiles -Files $protectedFiles)
        if ($remainingProcesses.Count -eq 0 -and $lockedFiles.Count -eq 0) {
            if ($null -eq $cleanSince) {
                $cleanSince = [DateTime]::UtcNow
            }
            elseif (([DateTime]::UtcNow - $cleanSince).TotalMilliseconds -ge $StableIntervalMilliseconds) {
                Write-PreflightLog 'Bundled Python stopped and runtime files remained unlocked; preflight succeeded.'
                $exitCode = 0
                exit $exitCode
            }
        }
        else {
            $cleanSince = $null
        }

        if ([DateTime]::UtcNow -ge $deadline) {
            $remainingIds = @($remainingProcesses | ForEach-Object { $_.ProcessId }) -join ','
            $lockedNames = @($lockedFiles | ForEach-Object { [System.IO.Path]::GetFileName($_) }) -join ','
            Write-PreflightLog "Preflight timed out. Remaining bundled PIDs=[$remainingIds]; locked runtime files=[$lockedNames]."
            $exitCode = 20
            exit $exitCode
        }
        Start-Sleep -Milliseconds $PollIntervalMilliseconds
    } while ($true)
}
catch {
    Write-PreflightLog "Preflight failed safely: $($_.Exception.Message)"
    exit $exitCode
}
