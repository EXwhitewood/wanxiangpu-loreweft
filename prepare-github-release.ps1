param(
    [Parameter(Mandatory = $true)]
    [string]$GitHubRepository,

    [string]$ReleaseTag,

    [string]$BundleDirectory = "src-tauri\target\release-candidate\release\bundle\nsis",

    [string]$Notes = "",

    [string]$ReleaseAssetPrefix = "Loreweft",

    [switch]$AllowUnsignedInstaller
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$updateChecklist = Join-Path $workspace "docs\desktop-updates.md"
if (-not (Test-Path -LiteralPath $updateChecklist -PathType Leaf)) {
    throw "The desktop update guide is missing: $updateChecklist"
}
Write-Host "Desktop update guide: $updateChecklist"
$installerContractScript = Join-Path $workspace "src-tauri\installer\installer-contract.ps1"
if (-not (Test-Path -LiteralPath $installerContractScript -PathType Leaf)) {
    throw "The installer contract helper is missing: $installerContractScript"
}
. $installerContractScript
$installerPreflightScript = Join-Path $workspace "src-tauri\installer\installer-preflight.ps1"

function Resolve-WorkspaceDirectory([string]$PathToCheck) {
    if ([System.IO.Path]::IsPathRooted($PathToCheck)) {
        $candidate = [System.IO.Path]::GetFullPath($PathToCheck)
    } else {
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $workspace $PathToCheck))
    }
    $workspacePrefix = $workspace.TrimEnd('\') + '\'
    if (-not $candidate.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "BundleDirectory must be inside the workspace: $candidate"
    }
    if (-not (Test-Path -LiteralPath $candidate -PathType Container)) {
        throw "BundleDirectory does not exist or is not a directory: $candidate"
    }

    $resolved = (Resolve-Path -LiteralPath $candidate).Path
    if (-not $resolved.StartsWith($workspacePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "BundleDirectory resolves outside the workspace: $resolved"
    }
    $current = Get-Item -LiteralPath $resolved -Force
    while ($current -and -not $current.FullName.Equals($workspace, [System.StringComparison]::OrdinalIgnoreCase)) {
        if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "BundleDirectory must not traverse a junction or symbolic link: $($current.FullName)"
        }
        $current = $current.Parent
    }
    if (-not $current) {
        throw "BundleDirectory is not contained by the workspace: $resolved"
    }
    return $resolved
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

function Assert-ReleaseTreeSafe([string]$RootPath) {
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
    $root = Resolve-WorkspaceDirectory $RootPath
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

$bundlePath = Resolve-WorkspaceDirectory $BundleDirectory
$configPath = Join-Path $workspace "src-tauri\tauri.conf.json"
$config = Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json
$version = [string]$config.version

$repository = $GitHubRepository.Trim().TrimEnd('/')
if ($repository -match '^https://github\.com/(?<slug>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?$') {
    $repository = $Matches.slug
} elseif ($repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "GitHubRepository must be owner/repository or a GitHub HTTPS URL."
}

if ([string]::IsNullOrWhiteSpace($ReleaseTag)) {
    $ReleaseTag = "v$version"
}
if ($ReleaseTag -ne "v$version") {
    throw "ReleaseTag must exactly match the application version: v$version"
}
if ($ReleaseAssetPrefix -notmatch '^[A-Za-z0-9][A-Za-z0-9._-]*$') {
    throw "ReleaseAssetPrefix must use only ASCII letters, digits, dots, underscores, or hyphens."
}
$notesPersonalPathPatterns = @(
    foreach ($personalRoot in @($env:USERPROFILE, $workspace)) {
        $pathPattern = ConvertTo-FlexiblePathRegex $personalRoot
        if ($pathPattern) {
            $pathPattern
        }
    }
)
$notesSensitivePattern = '(?i)(?:' + ($notesPersonalPathPatterns -join '|') + '|-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----|untrusted comment: minisign (?:encrypted )?secret key)'
if ($Notes -match $notesSensitivePattern) {
    throw "Release notes must not contain a personal absolute path or private key material."
}
if (Test-EncodedPrivateKeyMaterial $Notes) {
    throw "Release notes must not contain base64-encoded private key material."
}

# Validate every existing input before touching the upload-ready directory.
# A failed gate must leave the last known-good github-release directory intact.
Assert-ReleaseTreeSafe $bundlePath

$installers = @(
    Get-ChildItem -LiteralPath $bundlePath -Filter "*-setup.exe" -File |
        Where-Object { $_.Name -like "*_${version}_*" }
)
if ($installers.Count -ne 1) {
    throw "Expected exactly one $version NSIS installer in $bundlePath; found $($installers.Count). Rebuild in an isolated release target."
}
$installer = $installers[0]
$signaturePath = "$($installer.FullName).sig"
if (-not (Test-Path -LiteralPath $signaturePath -PathType Leaf) -or (Get-Item -LiteralPath $signaturePath).Length -le 0) {
    throw "The updater signature is missing or empty: $signaturePath"
}
$signature = (Get-Content -LiteralPath $signaturePath -Raw -Encoding UTF8).Trim()
if ([string]::IsNullOrWhiteSpace($signature)) {
    throw "The updater signature contains no text: $signaturePath"
}
Assert-TauriUpdaterSignature $installer.FullName $signaturePath ([string]$config.plugins.updater.pubkey)

$releaseOutputRoot = Split-Path -Parent (Split-Path -Parent $bundlePath)
$renderedNsisRoot = Join-Path $releaseOutputRoot "nsis"
if (-not (Test-Path -LiteralPath $renderedNsisRoot -PathType Container)) {
    throw "Rendered NSIS output is missing beside the bundle directory: $renderedNsisRoot"
}
$renderedInstallers = @(
    Get-ChildItem -LiteralPath $renderedNsisRoot -Recurse -Filter "installer.nsi" -File
)
if ($renderedInstallers.Count -ne 1) {
    throw "Expected exactly one rendered installer.nsi beside the release bundle; found $($renderedInstallers.Count)."
}
Assert-LoreweftRenderedNsisContract -RenderedInstallerPath $renderedInstallers[0].FullName
Assert-LoreweftInstallerEmbedsExactPreflight -InstallerPath $installer.FullName -PreflightPath $installerPreflightScript

$authenticode = Get-AuthenticodeSignature -LiteralPath $installer.FullName
if (-not (Test-InstallerAuthenticodeAccepted $authenticode.Status $AllowUnsignedInstaller.IsPresent)) {
    throw "The installer Authenticode status is $($authenticode.Status). Only NotSigned may be accepted with -AllowUnsignedInstaller for an explicitly risk-accepted public beta/test release. Invalid or damaged signatures are always rejected."
}

$versionMarker = "_$version"
$versionMarkerIndex = $installer.Name.IndexOf($versionMarker, [StringComparison]::Ordinal)
if ($versionMarkerIndex -lt 0) {
    throw "The installer name does not contain the expected version marker $versionMarker."
}
$releaseInstallerName = "$ReleaseAssetPrefix$($installer.Name.Substring($versionMarkerIndex))"
$assetName = [Uri]::EscapeDataString($releaseInstallerName)
$downloadUrl = "https://github.com/$repository/releases/download/$ReleaseTag/$assetName"
$manifest = [ordered]@{
    version = $version
    notes = $Notes
    pub_date = [DateTime]::UtcNow.ToString("o")
    platforms = [ordered]@{
        "windows-x86_64" = [ordered]@{
            signature = $signature
            url = $downloadUrl
        }
    }
}

$releaseAssetsPath = Join-Path $bundlePath "github-release"
$stagingAssetsPath = Join-Path $bundlePath (".github-release-staging-" + [Guid]::NewGuid().ToString("N"))
$previousAssetsPath = Join-Path $bundlePath (".github-release-previous-" + [Guid]::NewGuid().ToString("N"))
if ((Test-Path -LiteralPath $stagingAssetsPath) -or (Test-Path -LiteralPath $previousAssetsPath)) {
    throw "A supposedly unique release staging path already exists."
}
[void](New-Item -ItemType Directory -Path $stagingAssetsPath)
$stagingAssetsPath = Resolve-WorkspaceDirectory $stagingAssetsPath
$releaseInstallerPath = Join-Path $stagingAssetsPath $releaseInstallerName
$releaseSignaturePath = "$releaseInstallerPath.sig"
$manifestPath = Join-Path $stagingAssetsPath "latest.json"
$stagingPublished = $false
try {
    Copy-Item -LiteralPath $installer.FullName -Destination $releaseInstallerPath
    Copy-Item -LiteralPath $signaturePath -Destination $releaseSignaturePath
    Assert-TauriUpdaterSignature $releaseInstallerPath $releaseSignaturePath ([string]$config.plugins.updater.pubkey)
    $sourceInstallerHash = Get-LoreweftFileSha256 -Path $installer.FullName
    $stagedInstallerHash = Get-LoreweftFileSha256 -Path $releaseInstallerPath
    if ($sourceInstallerHash -ne $stagedInstallerHash) {
        throw "The upload-ready installer copy does not match the verified bundle installer."
    }

    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    [System.IO.File]::WriteAllText(
        $manifestPath,
        $manifestJson,
        [System.Text.UTF8Encoding]::new($false)
    )
    $verified = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        [string]$verified.version -ne $version -or
        [string]$verified.platforms.'windows-x86_64'.url -ne $downloadUrl -or
        [string]$verified.platforms.'windows-x86_64'.signature -ne $signature
    ) {
        throw "Generated latest.json failed round-trip validation."
    }
    $releaseFiles = @(Get-ChildItem -LiteralPath $stagingAssetsPath -File)
    if ($releaseFiles.Count -ne 3) {
        throw "Expected exactly three GitHub release assets; found $($releaseFiles.Count)."
    }
    $nonAsciiAssets = @($releaseFiles | Where-Object { $_.Name -notmatch '^[\x20-\x7E]+$' })
    if ($nonAsciiAssets.Count -ne 0) {
        throw "Every GitHub release asset name must be ASCII."
    }
    Assert-ReleaseTreeSafe $stagingAssetsPath

    $hadPreviousAssets = Test-Path -LiteralPath $releaseAssetsPath
    if ($hadPreviousAssets) {
        $releaseAssetsPath = Resolve-WorkspaceDirectory $releaseAssetsPath
        Move-Item -LiteralPath $releaseAssetsPath -Destination $previousAssetsPath
    }
    try {
        Move-Item -LiteralPath $stagingAssetsPath -Destination $releaseAssetsPath
        $stagingPublished = $true
    }
    catch {
        if ($hadPreviousAssets -and (Test-Path -LiteralPath $previousAssetsPath)) {
            Move-Item -LiteralPath $previousAssetsPath -Destination $releaseAssetsPath
        }
        throw
    }
    if ($hadPreviousAssets -and (Test-Path -LiteralPath $previousAssetsPath)) {
        $resolvedPreviousAssets = Resolve-WorkspaceDirectory $previousAssetsPath
        Remove-Item -LiteralPath $resolvedPreviousAssets -Recurse -Force
    }
}
finally {
    if (-not $stagingPublished -and (Test-Path -LiteralPath $stagingAssetsPath)) {
        $resolvedStagingAssets = Resolve-WorkspaceDirectory $stagingAssetsPath
        Remove-Item -LiteralPath $resolvedStagingAssets -Recurse -Force
    }
}

$releaseAssetsPath = Resolve-WorkspaceDirectory $releaseAssetsPath
$releaseInstallerPath = Join-Path $releaseAssetsPath $releaseInstallerName
$releaseSignaturePath = "$releaseInstallerPath.sig"
$manifestPath = Join-Path $releaseAssetsPath "latest.json"
Assert-ReleaseTreeSafe $releaseAssetsPath
$installerHash = Get-LoreweftFileSha256 -Path $releaseInstallerPath
Write-Host "Generated $manifestPath"
Write-Host "Updater signature and configured public key: verified"
Write-Host "Installer SHA256: $installerHash"
Write-Host "Upload these three files to GitHub Release ${ReleaseTag}:"
Write-Host "  $releaseInstallerName"
Write-Host "  $([System.IO.Path]::GetFileName($releaseSignaturePath))"
Write-Host "  $([System.IO.Path]::GetFileName($manifestPath))"
