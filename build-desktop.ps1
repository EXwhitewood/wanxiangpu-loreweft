param(
    [Parameter(Mandatory = $true)]
    [string]$PythonRuntimePath,

    [string]$GitHubRepository = "",

    [switch]$StageOnly,

    [switch]$Release,

    [switch]$AllowUnsignedInstaller,

    [switch]$PromptForUpdaterKeyPassword
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$updateChecklist = Join-Path $workspace "docs\desktop-updates.md"
if (-not (Test-Path -LiteralPath $updateChecklist -PathType Leaf)) {
    throw "The desktop update guide is missing: $updateChecklist"
}
Write-Host "Desktop update guide: $updateChecklist"
$tauriDir = Join-Path $workspace "src-tauri"
$installerContractScript = Join-Path $tauriDir "installer\installer-contract.ps1"
if (-not (Test-Path -LiteralPath $installerContractScript -PathType Leaf)) {
    throw "The installer contract helper is missing: $installerContractScript"
}
. $installerContractScript
$installerPreflightScript = Join-Path $tauriDir "installer\installer-preflight.ps1"
$backendSource = Join-Path $workspace "backend"
$runtimeSource = (Resolve-Path -LiteralPath $PythonRuntimePath).Path
$runtimePython = Join-Path $runtimeSource "python.exe"

if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    throw "PythonRuntimePath must point to a redistributable Python 3.12 directory containing python.exe."
}

$updateEndpoint = $null
$releaseBuild = $Release -or -not [string]::IsNullOrWhiteSpace($GitHubRepository)
if ($PromptForUpdaterKeyPassword -and -not $releaseBuild) {
    throw "-PromptForUpdaterKeyPassword is only valid for a release/update build."
}
if ($Release -and [string]::IsNullOrWhiteSpace($GitHubRepository)) {
    throw "Release builds require -GitHubRepository owner/repository. Do not ship a client with an unverified placeholder endpoint."
}
if (-not [string]::IsNullOrWhiteSpace($GitHubRepository)) {
    $repository = $GitHubRepository.Trim().TrimEnd('/')
    if ($repository -match '^https://github\.com/(?<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$') {
        $repository = $Matches.slug
    } elseif ($repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
        throw "GitHubRepository must be owner/repository or a GitHub HTTPS URL."
    }
    $updateEndpoint = "https://github.com/$repository/releases/latest/download/latest.json"
    Write-Host "GitHub update endpoint: $updateEndpoint"
}

$runtimeVersion = & $runtimePython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
if ($LASTEXITCODE -ne 0 -or $runtimeVersion.Trim() -ne "3.12") {
    throw "The desktop runtime must be Python 3.12; detected: $runtimeVersion"
}

function Assert-WorkspacePath([string]$PathToCheck) {
    $full = [System.IO.Path]::GetFullPath($PathToCheck)
    $root = $workspace.TrimEnd('\') + '\'
    if (-not $full.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside the workspace: $full"
    }

    # GetFullPath is lexical and does not protect recursive cleanup from a
    # junction/symlink planted below the workspace. Walk from the nearest
    # existing ancestor back to the workspace before any remove/move action.
    $existingPath = $full
    while (-not (Test-Path -LiteralPath $existingPath)) {
        $parentPath = Split-Path -Parent $existingPath
        if ([string]::IsNullOrWhiteSpace($parentPath) -or $parentPath -eq $existingPath) {
            throw "Could not resolve a workspace ancestor for: $full"
        }
        $existingPath = $parentPath
    }
    $current = Get-Item -LiteralPath $existingPath -Force
    while ($current -and -not $current.FullName.Equals($workspace, [System.StringComparison]::OrdinalIgnoreCase)) {
        if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to traverse a junction or symbolic link: $($current.FullName)"
        }
        if ($current -is [System.IO.FileInfo]) {
            $current = $current.Directory
        } else {
            $current = $current.Parent
        }
    }
    if (-not $current) {
        throw "Path is not contained by the workspace: $full"
    }
    return $full
}

function Remove-WorkspaceDirectory([string]$PathToRemove) {
    $full = Assert-WorkspacePath $PathToRemove
    if (Test-Path -LiteralPath $full) {
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function ConvertTo-FlexiblePathRegex([string]$PathValue) {
    if ([string]::IsNullOrWhiteSpace($PathValue)) {
        return $null
    }
    $trimmed = $PathValue.Trim().TrimEnd([char[]]@('\', '/'))
    $segments = @($trimmed -split '[\\/]')
    $pattern = (($segments | ForEach-Object { [regex]::Escape($_) }) -join '[\\/]')
    if ($trimmed -match '^[A-Za-z]:$') {
        # A bare drive token such as `z:` is common in Python source and is
        # not an absolute path. Require the root separator for drive roots.
        return "$pattern[\\/]"
    }
    return "$pattern(?:[\\/]|$)"
}

function Test-EncodedPrivateKeyMaterial([string]$Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $false
    }
    $strictUtf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $tokens = [regex]::Matches(
        $Text,
        '(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{40,}={0,2}(?![A-Za-z0-9+/=])'
    )
    foreach ($token in $tokens) {
        $encoded = $token.Value
        if ($encoded.Length -gt 128KB -or $encoded.Length % 4 -ne 0) {
            continue
        }
        try {
            $decoded = $strictUtf8.GetString([Convert]::FromBase64String($encoded))
        }
        catch {
            continue
        }
        if ($decoded -match '(?i)-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|untrusted comment: minisign (?:encrypted )?secret key') {
            return $true
        }
    }
    return $false
}

function Assert-ReleaseTreeSafe([string[]]$RootPaths) {
    $violations = New-Object System.Collections.Generic.List[string]
    $blockedFileNamePattern = '(?i)(?:\.db(?:3)?(?:-wal|-shm|-journal)?|\.sqlite(?:3)?(?:-wal|-shm|-journal)?|\.log(?:\..+)?|\.key(?:\.pub)?|\.pfx|\.p12|\.jks|\.keystore)$|^(?:id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|\.env(?:\..+)?|credentials\.json|secrets\.json)$'
    $personalPathPatterns = @(
        foreach ($personalRoot in @($env:USERPROFILE, $workspace)) {
            $pathPattern = ConvertTo-FlexiblePathRegex $personalRoot
            if ($pathPattern) {
                $pathPattern
            }
        }
    )
    $sensitiveTextPattern = '(?i)(?:' + ($personalPathPatterns -join '|') + '|-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|untrusted comment: minisign (?:encrypted )?secret key)'
    $textExtensions = @(
        '.cfg', '.cjs', '.css', '.csv', '.html', '.ini', '.js', '.json',
        '.md', '.mjs', '.pem', '.ps1', '.pth', '.pub', '.py', '.pyi',
        '.sig', '.toml', '.ts', '.tsx', '.txt', '.xml', '.yaml', '.yml'
    )
    foreach ($rootPath in $RootPaths) {
        $root = Assert-WorkspacePath $rootPath
        if (-not (Test-Path -LiteralPath $root -PathType Container)) {
            throw "Release candidate scan root does not exist: $root"
        }
        $rootPrefixLength = $root.TrimEnd('\').Length
        $directoryList = New-Object System.Collections.Generic.List[System.IO.DirectoryInfo]
        $fileList = New-Object System.Collections.Generic.List[System.IO.FileInfo]
        $pendingDirectories = New-Object System.Collections.Generic.Stack[string]
        $pendingDirectories.Push($root)
        while ($pendingDirectories.Count -gt 0) {
            $currentDirectory = $pendingDirectories.Pop()
            foreach ($entry in Get-ChildItem -LiteralPath $currentDirectory -Force) {
                $relative = $entry.FullName.Substring($rootPrefixLength).TrimStart('\')
                if (($entry.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    [void]$violations.Add("junction or symbolic link: $relative")
                    continue
                }
                if ($entry -is [System.IO.DirectoryInfo]) {
                    [void]$directoryList.Add($entry)
                    $pendingDirectories.Push($entry.FullName)
                } else {
                    [void]$fileList.Add($entry)
                }
            }
        }
        $directories = @($directoryList)
        foreach ($directory in $directories) {
            if ($directory.Name -match '^(?i:log|logs)$') {
                $relative = $directory.FullName.Substring($rootPrefixLength).TrimStart('\')
                [void]$violations.Add("log directory: $relative")
            }
        }

        $files = @($fileList)
        foreach ($file in $files) {
            $relative = $file.FullName.Substring($rootPrefixLength).TrimStart('\')
            if ($file.Name -match $blockedFileNamePattern) {
                [void]$violations.Add("blocked database/key/log file: $relative")
            }
        }

        $textFiles = @(
            $files | Where-Object {
                $_.Length -le 5MB -and $textExtensions -contains $_.Extension.ToLowerInvariant()
            }
        )
        if ($textFiles.Count -gt 0) {
            $textMatches = @(
                $textFiles | Select-String -Pattern $sensitiveTextPattern -List -ErrorAction Stop
            )
            foreach ($match in $textMatches) {
                $relative = $match.Path.Substring($rootPrefixLength).TrimStart('\')
                [void]$violations.Add("personal path or private key material: $relative")
            }
        }

        $encodedScanFiles = @(
            $files | Where-Object {
                $_.Length -le 5MB -and (
                    $textExtensions -contains $_.Extension.ToLowerInvariant() -or
                    [string]::IsNullOrEmpty($_.Extension)
                )
            }
        )
        foreach ($candidate in $encodedScanFiles) {
            if (Test-EncodedPrivateKeyMaterial ([System.IO.File]::ReadAllText($candidate.FullName))) {
                $relative = $candidate.FullName.Substring($rootPrefixLength).TrimStart('\')
                [void]$violations.Add("base64-encoded private key material: $relative")
            }
        }
    }

    $uniqueViolations = @($violations | Sort-Object -Unique)
    if ($uniqueViolations.Count -gt 0) {
        $preview = @($uniqueViolations | Select-Object -First 20) -join "`n  - "
        throw "Release candidate safety scan failed:`n  - $preview"
    }
}

function Assert-TauriUpdaterSignature(
    [string]$InstallerPath,
    [string]$SignaturePath,
    [string]$ConfiguredPublicKey
) {
    if ([string]::IsNullOrWhiteSpace($ConfiguredPublicKey)) {
        throw "The Tauri updater public key is missing from tauri.conf.json."
    }
    $nodeCommand = Get-Command node -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $nodeCommand) {
        throw "Node.js is required to verify the Tauri updater signature."
    }
    $verificationScript = @'
const crypto = require("node:crypto");
const fs = require("node:fs");
const { TextDecoder } = require("node:util");

function decodeBase64Text(label, encoded) {
  const value = encoded.trim();
  if (!value || value.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    throw new Error(`${label} is not canonical base64`);
  }
  const bytes = Buffer.from(value, "base64");
  const canonical = bytes.toString("base64");
  if (canonical !== value) {
    throw new Error(`${label} is not canonical base64`);
  }
  return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
}

function decodeRecord(label, encoded, length) {
  const value = encoded.trim();
  if (!value || !/^[A-Za-z0-9+/]+={0,2}$/.test(value)) {
    throw new Error(`${label} is not valid base64`);
  }
  const bytes = Buffer.from(value, "base64");
  if (bytes.length !== length || bytes.toString("base64") !== value) {
    throw new Error(`${label} has an invalid length or encoding`);
  }
  return bytes;
}

try {
  const [installerPath, signaturePath, configuredPublicKey] = process.argv.slice(2);
  if (!installerPath || !signaturePath || !configuredPublicKey) {
    throw new Error("installer, signature, and public key are required");
  }

  const publicKeyLines = decodeBase64Text("updater public key", configuredPublicKey)
    .trimEnd()
    .split(/\r?\n/);
  if (publicKeyLines.length !== 2 || !publicKeyLines[0].startsWith("untrusted comment:")) {
    throw new Error("updater public key has an invalid Minisign envelope");
  }
  const publicKeyRecord = decodeRecord("updater public key record", publicKeyLines[1], 42);
  const keyAlgorithm = publicKeyRecord.subarray(0, 2).toString("ascii");
  if (keyAlgorithm !== "Ed" && keyAlgorithm !== "ED") {
    throw new Error("updater public key uses an unsupported algorithm");
  }

  const outerSignature = fs.readFileSync(signaturePath, "utf8");
  const signatureLines = decodeBase64Text("updater signature", outerSignature)
    .trimEnd()
    .split(/\r?\n/);
  if (
    signatureLines.length !== 4 ||
    !signatureLines[0].startsWith("untrusted comment:") ||
    !signatureLines[2].startsWith("trusted comment: ")
  ) {
    throw new Error("updater signature has an invalid Minisign envelope");
  }

  const signatureRecord = decodeRecord("updater signature record", signatureLines[1], 74);
  const signatureAlgorithm = signatureRecord.subarray(0, 2).toString("ascii");
  if (signatureAlgorithm !== "Ed" && signatureAlgorithm !== "ED") {
    throw new Error("updater signature uses an unsupported algorithm");
  }
  if (!publicKeyRecord.subarray(2, 10).equals(signatureRecord.subarray(2, 10))) {
    throw new Error("updater signature was created by a different key");
  }

  const rawPublicKey = publicKeyRecord.subarray(10, 42);
  const spkiPrefix = Buffer.from("302a300506032b6570032100", "hex");
  const publicKey = crypto.createPublicKey({
    key: Buffer.concat([spkiPrefix, rawPublicKey]),
    format: "der",
    type: "spki",
  });
  const installer = fs.readFileSync(installerPath);
  const signedContent = signatureAlgorithm === "ED"
    ? crypto.createHash("blake2b512").update(installer).digest()
    : installer;
  const fileSignature = signatureRecord.subarray(10, 74);
  if (!crypto.verify(null, signedContent, publicKey, fileSignature)) {
    throw new Error("updater signature does not match the installer");
  }

  const globalSignature = decodeRecord("updater trusted-comment signature", signatureLines[3], 64);
  const trustedComment = Buffer.from(signatureLines[2].slice("trusted comment: ".length), "utf8");
  const globalContent = Buffer.concat([fileSignature, trustedComment]);
  if (!crypto.verify(null, globalContent, publicKey, globalSignature)) {
    throw new Error("updater trusted-comment signature is invalid");
  }
  console.log("verified");
} catch (error) {
  console.log(`verification failed: ${error.message}`);
  process.exit(1);
}
'@
    $verificationOutput = @(
        $verificationScript | & $nodeCommand.Source - $InstallerPath $SignaturePath $ConfiguredPublicKey
    )
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri updater signature verification failed: $($verificationOutput -join ' ')"
    }
}

function Test-InstallerAuthenticodeAccepted(
    [System.Management.Automation.SignatureStatus]$Status,
    [bool]$AllowUnsigned
) {
    return $Status -eq [System.Management.Automation.SignatureStatus]::Valid -or (
        $Status -eq [System.Management.Automation.SignatureStatus]::NotSigned -and
        $AllowUnsigned
    )
}

$stageRoot = Assert-WorkspacePath (Join-Path $tauriDir ".release-stage")
$stagePythonDir = Join-Path $stageRoot "python"
$stageBackendDir = Join-Path $stageRoot "backend"
$resourcePythonDir = Assert-WorkspacePath (Join-Path $tauriDir "resources\python")
$resourceBackendDir = Assert-WorkspacePath (Join-Path $tauriDir "resources\backend")

Remove-WorkspaceDirectory $stageRoot
New-Item -ItemType Directory -Path $stagePythonDir -Force | Out-Null
New-Item -ItemType Directory -Path $stageBackendDir -Force | Out-Null

Write-Host "[1/6] Copy Python runtime"
Copy-Item -Path (Join-Path $runtimeSource "*") -Destination $stagePythonDir -Recurse -Force

# Keep the runtime isolated while explicitly exposing the two bundled code roots.
# Paths in python312._pth are resolved relative to python.exe; PYTHONPATH is
# intentionally ignored by the embeddable distribution.
$pthFile = Get-ChildItem -LiteralPath $stagePythonDir -Filter "python*._pth" -File | Select-Object -First 1
if ($pthFile) {
    $pthLines = @(Get-Content -LiteralPath $pthFile.FullName -Encoding utf8)
    $pthLines = @($pthLines | ForEach-Object {
        if ($_ -eq "#import site") { "import site" } else { $_ }
    })
    if ($pthLines -notcontains "Lib\site-packages") {
        $pthLines += "Lib\site-packages"
    }
    if ($pthLines -notcontains "..\backend") {
        $pthLines += "..\backend"
    }
    [System.IO.File]::WriteAllLines(
        $pthFile.FullName,
        $pthLines,
        [System.Text.UTF8Encoding]::new($false)
    )
}

Write-Host "[2/6] Copy backend without databases, secrets, tests, or logs"
$backendAppSource = Join-Path $backendSource "app"
$requirementsSource = Join-Path $backendSource "requirements.txt"
$requirementsDestination = Join-Path $stageBackendDir "requirements.txt"
Copy-Item -LiteralPath $backendAppSource -Destination $stageBackendDir -Recurse -Force
Copy-Item -LiteralPath $requirementsSource -Destination $requirementsDestination -Force
if (-not (Test-Path -LiteralPath $requirementsDestination -PathType Leaf)) {
    throw "requirements.txt was not copied into the release stage."
}
Get-ChildItem -LiteralPath $stageBackendDir -Directory -Recurse -Force |
    Where-Object { $_.Name -eq "__pycache__" } |
    Sort-Object FullName -Descending |
    ForEach-Object {
        $cachePath = Assert-WorkspacePath $_.FullName
        Remove-Item -LiteralPath $cachePath -Recurse -Force
    }
Get-ChildItem -LiteralPath $stageBackendDir -File -Recurse -Force |
    Where-Object { $_.Extension -in ".pyc", ".pyo" } |
    ForEach-Object { Remove-Item -LiteralPath (Assert-WorkspacePath $_.FullName) -Force }

$stagePython = Join-Path $stagePythonDir "python.exe"
$stageSitePackages = Join-Path $stagePythonDir "Lib\site-packages"
New-Item -ItemType Directory -Path $stageSitePackages -Force | Out-Null

Write-Host "[3/6] Install locked backend dependencies"
$pipRequirements = Join-Path $env:TEMP "loreweft-requirements-$PID.txt"
try {
    Copy-Item -LiteralPath $requirementsDestination -Destination $pipRequirements -Force
    & py -m pip install --disable-pip-version-check --no-compile --upgrade --ignore-installed --target $stageSitePackages --requirement $pipRequirements
    if ($LASTEXITCODE -ne 0) {
        throw "Backend dependency installation failed."
    }
}
finally {
    if (Test-Path -LiteralPath $pipRequirements) {
        Remove-Item -LiteralPath $pipRequirements -Force
    }
}

Write-Host "[4/6] Remove packaging caches and executable helper directories"
Remove-WorkspaceDirectory (Join-Path $stageSitePackages "bin")
foreach ($pythonTree in @($stagePythonDir, $stageBackendDir)) {
    Get-ChildItem -LiteralPath $pythonTree -Directory -Recurse -Force |
        Where-Object { $_.Name -eq "__pycache__" } |
        Sort-Object FullName -Descending |
        ForEach-Object {
            Remove-Item -LiteralPath (Assert-WorkspacePath $_.FullName) -Recurse -Force
        }
    Get-ChildItem -LiteralPath $pythonTree -File -Recurse -Force |
        Where-Object { $_.Extension -in ".pyc", ".pyo" } |
        ForEach-Object {
            Remove-Item -LiteralPath (Assert-WorkspacePath $_.FullName) -Force
        }
}
$residualPythonArtifacts = @(
    Get-ChildItem -LiteralPath $stagePythonDir, $stageBackendDir -Recurse -Force |
        Where-Object {
            ($_.PSIsContainer -and $_.Name -eq "__pycache__") -or
            (-not $_.PSIsContainer -and $_.Extension -in ".pyc", ".pyo")
        }
)
if (
    (Test-Path -LiteralPath (Join-Path $stageSitePackages "bin")) -or
    $residualPythonArtifacts.Count -ne 0
) {
    throw "Python release staging still contains site-packages/bin, __pycache__, pyc, or pyo artifacts."
}

Write-Host "[5/6] Validate the isolated runtime and backend import"
$validationData = Join-Path $stageRoot "validation-data"
New-Item -ItemType Directory -Path $validationData -Force | Out-Null
$oldDataDir = $env:DATA_DIR
$oldDatabaseUrl = $env:DATABASE_URL
$oldSqlitePath = $env:SQLITE_PATH
$oldSqliteFtsPath = $env:SQLITE_FTS_PATH
$oldPythonNoUserSite = $env:PYTHONNOUSERSITE
$oldPythonDontWriteBytecode = $env:PYTHONDONTWRITEBYTECODE
try {
    $validationDb = (Join-Path $validationData "validation.db").Replace('\', '/')
    $env:DATA_DIR = $validationData
    $env:DATABASE_URL = "sqlite+aiosqlite:///$validationDb"
    $env:SQLITE_PATH = $validationDb
    $env:SQLITE_FTS_PATH = $validationDb
    $env:PYTHONNOUSERSITE = "1"
    $env:PYTHONDONTWRITEBYTECODE = "1"
    & $stagePython -c "from app.main import app; assert len(app.routes) > 0; print('backend import: ok')"
    if ($LASTEXITCODE -ne 0) {
        throw "The isolated runtime could not import the backend."
    }
}
finally {
    $env:DATA_DIR = $oldDataDir
    $env:DATABASE_URL = $oldDatabaseUrl
    $env:SQLITE_PATH = $oldSqlitePath
    $env:SQLITE_FTS_PATH = $oldSqliteFtsPath
    $env:PYTHONNOUSERSITE = $oldPythonNoUserSite
    $env:PYTHONDONTWRITEBYTECODE = $oldPythonDontWriteBytecode
}
Remove-WorkspaceDirectory $validationData

Assert-ReleaseTreeSafe @($stagePythonDir, $stageBackendDir)

Write-Host "[6/6] Publish verified Tauri resources"
Remove-WorkspaceDirectory $resourcePythonDir
Remove-WorkspaceDirectory $resourceBackendDir
Move-Item -LiteralPath $stagePythonDir -Destination $resourcePythonDir
Move-Item -LiteralPath $stageBackendDir -Destination $resourceBackendDir
Remove-WorkspaceDirectory $stageRoot

if ($StageOnly) {
    Write-Host "Desktop resources staged successfully."
    exit 0
}

$tauriCli = Join-Path $workspace "frontend\node_modules\.bin\tauri.cmd"
$packageJsonPath = Join-Path $workspace "frontend\package.json"
$packageLockPath = Join-Path $workspace "frontend\package-lock.json"
Assert-LoreweftTauriNsisToolchainContract -PackageJsonPath $packageJsonPath -PackageLockPath $packageLockPath -TauriCliPath $tauriCli

$buildConfig = Join-Path $tauriDir "tauri.conf.json"
Assert-LoreweftInstallerTemplateContract -TauriConfigPath $buildConfig
$tauriConfig = Get-Content -LiteralPath $buildConfig -Raw -Encoding UTF8 | ConvertFrom-Json
$appVersion = [string]$tauriConfig.version
if ($appVersion -notmatch '^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$') {
    throw "tauri.conf.json contains an invalid SemVer version: $appVersion"
}

$oldUpdateFlag = $env:VITE_DESKTOP_UPDATES_ENABLED
$oldSigningKey = $env:TAURI_SIGNING_PRIVATE_KEY
$oldSigningKeyPath = $env:TAURI_SIGNING_PRIVATE_KEY_PATH
$oldSigningKeyPassword = $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD
$oldCargoTargetDir = $env:CARGO_TARGET_DIR
$updateConfigPath = $null
$releaseTargetDir = $null
try {
    if ($updateEndpoint) {
        $updateConfigPath = Assert-WorkspacePath (Join-Path $tauriDir ".release-updater.conf.json")
        $signingKey = $env:TAURI_SIGNING_PRIVATE_KEY
        if ([string]::IsNullOrWhiteSpace($signingKey)) {
            $signingKey = $env:TAURI_SIGNING_PRIVATE_KEY_PATH
            if ([string]::IsNullOrWhiteSpace($signingKey)) {
                $signingKey = Join-Path $env:USERPROFILE ".loreweft-updater\updater.key"
            }
            if (-not (Test-Path -LiteralPath $signingKey -PathType Leaf)) {
                throw "A Tauri updater signing key is required. Set TAURI_SIGNING_PRIVATE_KEY to a key path/content, or TAURI_SIGNING_PRIVATE_KEY_PATH to a key path."
            }
        }
        # Tauri 2.10+ supports both variables. Normalize on the documented
        # PRIVATE_KEY contract while retaining PATH compatibility for CI.
        $env:TAURI_SIGNING_PRIVATE_KEY = $signingKey
        if ($PromptForUpdaterKeyPassword -and [string]::IsNullOrEmpty($env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD)) {
            $securePassword = Read-Host "Updater signing key password" -AsSecureString
            if ($securePassword.Length -eq 0) {
                throw "The updater signing key password cannot be empty when prompting is requested."
            }
            $passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($securePassword)
            try {
                $plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
                $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $plainPassword
            }
            finally {
                if ($passwordPointer -ne [IntPtr]::Zero) {
                    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
                }
                Remove-Variable plainPassword -ErrorAction SilentlyContinue
                Remove-Variable securePassword -ErrorAction SilentlyContinue
            }
        }

        $releaseTargetDir = Assert-WorkspacePath (Join-Path $tauriDir "target\release-candidate")
        Remove-WorkspaceDirectory $releaseTargetDir
        $env:CARGO_TARGET_DIR = $releaseTargetDir

        $overlay = @{ bundle = @{ createUpdaterArtifacts = $true }; plugins = @{ updater = @{ endpoints = @($updateEndpoint) } } } |
            ConvertTo-Json -Depth 8
        [System.IO.File]::WriteAllText($updateConfigPath, $overlay, [System.Text.UTF8Encoding]::new($false))
        $env:VITE_DESKTOP_UPDATES_ENABLED = "1"
        & $tauriCli build --config "src-tauri/tauri.conf.json" --config $updateConfigPath
    } else {
        Remove-Item Env:VITE_DESKTOP_UPDATES_ENABLED -ErrorAction SilentlyContinue
        & $tauriCli build --config "src-tauri/tauri.conf.json"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri installer build failed."
    }
}
finally {
    if ($null -eq $oldUpdateFlag) {
        Remove-Item Env:VITE_DESKTOP_UPDATES_ENABLED -ErrorAction SilentlyContinue
    } else {
        $env:VITE_DESKTOP_UPDATES_ENABLED = $oldUpdateFlag
    }
    if ($null -eq $oldSigningKey) {
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY -ErrorAction SilentlyContinue
    } else {
        $env:TAURI_SIGNING_PRIVATE_KEY = $oldSigningKey
    }
    if ($null -eq $oldSigningKeyPath) {
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PATH -ErrorAction SilentlyContinue
    } else {
        $env:TAURI_SIGNING_PRIVATE_KEY_PATH = $oldSigningKeyPath
    }
    if ($null -eq $oldSigningKeyPassword) {
        Remove-Item Env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD -ErrorAction SilentlyContinue
    } else {
        $env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = $oldSigningKeyPassword
    }
    if ($null -eq $oldCargoTargetDir) {
        Remove-Item Env:CARGO_TARGET_DIR -ErrorAction SilentlyContinue
    } else {
        $env:CARGO_TARGET_DIR = $oldCargoTargetDir
    }
    if ($updateConfigPath -and (Test-Path -LiteralPath $updateConfigPath)) {
        Remove-Item -LiteralPath $updateConfigPath -Force
    }
}

if ($releaseBuild) {
    $renderedNsisRoot = Join-Path $releaseTargetDir "release\nsis"
    $renderedInstallers = @(
        Get-ChildItem -LiteralPath $renderedNsisRoot -Recurse -Filter "installer.nsi" -File
    )
    if ($renderedInstallers.Count -ne 1) {
        throw "Expected exactly one rendered installer.nsi in the isolated release target; found $($renderedInstallers.Count)."
    }
    Assert-LoreweftRenderedNsisContract -RenderedInstallerPath $renderedInstallers[0].FullName

    $nsisDirectory = Join-Path $releaseTargetDir "release\bundle\nsis"
    if (-not (Test-Path -LiteralPath $nsisDirectory -PathType Container)) {
        throw "Release NSIS output directory was not created: $nsisDirectory"
    }
    $installers = @(
        Get-ChildItem -LiteralPath $nsisDirectory -Filter "*-setup.exe" -File |
            Where-Object { $_.Name -like "*_${appVersion}_*" }
    )
    if ($installers.Count -ne 1) {
        throw "Expected exactly one $appVersion NSIS installer in the isolated output; found $($installers.Count)."
    }
    $installer = $installers[0]
    $signaturePath = "$($installer.FullName).sig"
    if (-not (Test-Path -LiteralPath $signaturePath -PathType Leaf) -or (Get-Item -LiteralPath $signaturePath).Length -le 0) {
        throw "The updater signature is missing or empty: $signaturePath"
    }
    Assert-TauriUpdaterSignature $installer.FullName $signaturePath ([string]$tauriConfig.plugins.updater.pubkey)
    Assert-LoreweftInstallerEmbedsExactPreflight -InstallerPath $installer.FullName -PreflightPath $installerPreflightScript
    $authenticode = Get-AuthenticodeSignature -LiteralPath $installer.FullName
    if (-not (Test-InstallerAuthenticodeAccepted $authenticode.Status $AllowUnsignedInstaller.IsPresent)) {
        throw "The release installer Authenticode status is $($authenticode.Status). Only NotSigned may be accepted with -AllowUnsignedInstaller for an explicitly risk-accepted public beta/test build. Invalid or damaged signatures are always rejected."
    }
    $hash = Get-LoreweftFileSha256 -Path $installer.FullName
    Write-Host "Verified updater installer: $($installer.FullName)"
    Write-Host "Updater signature and configured public key: verified"
    Write-Host "Installer SHA256: $hash"
} else {
    Write-Host "Local desktop installer build completed under src-tauri/target/release/bundle."
    Write-Warning "This unsigned local build is not a release candidate and has desktop updates disabled."
}
