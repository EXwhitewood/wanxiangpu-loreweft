param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$workspace = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$fixtureId = [Guid]::NewGuid().ToString("N")
$fixtureRoot = Join-Path $workspace ".tmp\release-pipeline-fixture-$fixtureId"
$outsideRoot = Join-Path $workspace ".tmp\release-pipeline-outside-$fixtureId"
$tauriCli = Join-Path $workspace "frontend\node_modules\.bin\tauri.cmd"
$sourcePrepareScript = Join-Path $workspace "prepare-github-release.ps1"
$sourceBuildScript = Join-Path $workspace "build-desktop.ps1"
$sourceInstallerContract = Join-Path $workspace "src-tauri\installer\installer-contract.ps1"
$sourceInstallerPreflight = Join-Path $workspace "src-tauri\installer\installer-preflight.ps1"
$sourceInstallerTemplate = Join-Path $workspace "src-tauri\installer\installer-template.nsi"
$fixturePassword = "release-pipeline-fixture-password"
. $sourceInstallerContract

function Remove-TestDirectory([string]$PathToRemove) {
    $full = [System.IO.Path]::GetFullPath($PathToRemove)
    $allowed = @($fixtureRoot, $outsideRoot) | ForEach-Object { [System.IO.Path]::GetFullPath($_) }
    if (-not ($allowed -contains $full)) {
        throw "Refusing to remove a non-fixture directory: $full"
    }
    if (Test-Path -LiteralPath $full) {
        $current = Get-Item -LiteralPath $full -Force
        while ($current -and -not $current.FullName.Equals($workspace, [System.StringComparison]::OrdinalIgnoreCase)) {
            if (($current.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "Refusing to remove a fixture through a junction or symbolic link: $($current.FullName)"
            }
            $current = $current.Parent
        }
        if (-not $current) {
            throw "Fixture cleanup path is not inside the workspace: $full"
        }
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) {
        throw $Message
    }
}

function Get-ReleaseFingerprint([string]$ReleaseDirectory) {
    return @(
        Get-ChildItem -LiteralPath $ReleaseDirectory -File | Sort-Object Name | ForEach-Object {
            "$($_.Name)|$($_.Length)|$(Get-LoreweftFileSha256 -Path $_.FullName)"
        }
    ) -join "`n"
}

function Invoke-Prepare([string]$ScriptPath, [string]$BundlePath, [string]$Notes = "fixture") {
    try {
        $output = @(
            & $ScriptPath `
                -GitHubRepository "owner/repository" `
                -ReleaseTag "v9.9.9" `
                -BundleDirectory $BundlePath `
                -Notes $Notes `
                -AllowUnsignedInstaller
        )
        return [pscustomobject]@{
            Succeeded = $true
            Output = $output -join "`n"
        }
    }
    catch {
        return [pscustomobject]@{
            Succeeded = $false
            Output = $_.Exception.Message
        }
    }
}

try {
    Assert-True (Test-Path -LiteralPath $tauriCli -PathType Leaf) "Tauri CLI is required for the release fixture."
    Assert-True (-not (Test-Path -LiteralPath $fixtureRoot)) "The unique fixture root already exists."
    Assert-True (-not (Test-Path -LiteralPath $outsideRoot)) "The unique outside root already exists."
    [void](New-Item -ItemType Directory -Path $fixtureRoot)
    [void](New-Item -ItemType Directory -Path $outsideRoot)
    $fixtureConfigDirectory = Join-Path $fixtureRoot "src-tauri"
    $fixtureDocsDirectory = Join-Path $fixtureRoot "docs"
    $fixtureInstallerDirectory = Join-Path $fixtureConfigDirectory "installer"
    $fixtureReleaseDirectory = Join-Path $fixtureRoot "release"
    $fixtureBundleDirectory = Join-Path $fixtureReleaseDirectory "bundle\nsis"
    $fixtureRenderedDirectory = Join-Path $fixtureReleaseDirectory "nsis\x64"
    $fixtureKeyDirectory = Join-Path $fixtureRoot "keys"
    [void](New-Item -ItemType Directory -Path $fixtureConfigDirectory, $fixtureDocsDirectory, $fixtureInstallerDirectory, $fixtureBundleDirectory, $fixtureRenderedDirectory, $fixtureKeyDirectory -Force)

    $fixturePrepareScript = Join-Path $fixtureRoot "prepare-github-release.ps1"
    Copy-Item -LiteralPath $sourcePrepareScript -Destination $fixturePrepareScript -Force
    Copy-Item -LiteralPath $sourceInstallerContract -Destination (Join-Path $fixtureInstallerDirectory "installer-contract.ps1") -Force
    Copy-Item -LiteralPath $sourceInstallerPreflight -Destination (Join-Path $fixtureInstallerDirectory "installer-preflight.ps1") -Force
    Copy-Item -LiteralPath $sourceInstallerTemplate -Destination (Join-Path $fixtureRenderedDirectory "installer.nsi") -Force
    Copy-Item -LiteralPath (Join-Path $workspace "docs\desktop-updates.md") -Destination (Join-Path $fixtureDocsDirectory "desktop-updates.md") -Force

    $privateKeyPath = Join-Path $fixtureKeyDirectory "fixture.key"
    $null = & $tauriCli signer generate --ci --password $fixturePassword --write-keys $privateKeyPath
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to generate the fixture updater keypair."
    }
    $publicKeyPath = "$privateKeyPath.pub"
    $publicKey = [System.IO.File]::ReadAllText($publicKeyPath).Trim()
    $fixtureConfig = [ordered]@{
        version = "9.9.9"
        plugins = [ordered]@{
            updater = [ordered]@{
                pubkey = $publicKey
            }
        }
    } | ConvertTo-Json -Depth 6
    $fixtureConfigPath = Join-Path $fixtureConfigDirectory "tauri.conf.json"
    [System.IO.File]::WriteAllText(
        $fixtureConfigPath,
        $fixtureConfig,
        [System.Text.UTF8Encoding]::new($false)
    )

    $installerPath = Join-Path $fixtureBundleDirectory "Fixture_9.9.9_x64-setup.exe"
    $makensisCandidates = @(
        (Join-Path $env:LOCALAPPDATA "tauri\NSIS\makensis.exe"),
        (Join-Path $env:LOCALAPPDATA "tauri\NSIS\Bin\makensis.exe")
    ) | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf }
    $makensis = $makensisCandidates | Select-Object -First 1
    Assert-True (-not [string]::IsNullOrWhiteSpace([string]$makensis)) "makensis.exe is required for the release fixture."
    $fixtureNsi = Join-Path $fixtureRoot "embedded-preflight-installer.nsi"
    $fixturePreflightPath = Join-Path $fixtureInstallerDirectory "installer-preflight.ps1"
    @"
Unicode true
Name "Loreweft release pipeline fixture"
OutFile "$installerPath"
InstallDir "`$TEMP\LoreweftReleaseFixture"
RequestExecutionLevel user
Section
  InitPluginsDir
  File /oname=`$PLUGINSDIR\LoreweftInstallerPreflight.ps1 "$fixturePreflightPath"
SectionEnd
"@ | Set-Content -LiteralPath $fixtureNsi -Encoding UTF8
    & $makensis /V2 $fixtureNsi
    Assert-True ($LASTEXITCODE -eq 0 -and (Test-Path -LiteralPath $installerPath -PathType Leaf)) "Unable to compile the embedded-preflight installer fixture."
    Assert-True (
        (Get-AuthenticodeSignature -LiteralPath $installerPath).Status -eq
            [System.Management.Automation.SignatureStatus]::NotSigned
    ) "The fixture installer surrogate must report Authenticode NotSigned."
    $null = & $tauriCli signer sign --private-key-path $privateKeyPath --password $fixturePassword $installerPath
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to sign the fixture installer."
    }
    $signaturePath = "$installerPath.sig"
    Assert-True (Test-Path -LiteralPath $signaturePath -PathType Leaf) "The fixture signature was not generated."
    $originalInstaller = [System.IO.File]::ReadAllBytes($installerPath)
    $originalSignature = [System.IO.File]::ReadAllBytes($signaturePath)

    $safe = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
    Assert-True $safe.Succeeded "A valid fixture release was rejected: $($safe.Output)"
    $releaseDirectory = Join-Path $fixtureBundleDirectory "github-release"
    $releaseFiles = @(Get-ChildItem -LiteralPath $releaseDirectory -File)
    Assert-True ($releaseFiles.Count -eq 3) "The fixture release did not contain exactly three assets."
    Assert-True (
        @($releaseFiles | Where-Object { $_.Name -notmatch '^[\x20-\x7E]+$' }).Count -eq 0
    ) "The fixture release contained a non-ASCII asset name."
    $manifest = Get-Content -LiteralPath (Join-Path $releaseDirectory "latest.json") -Raw -Encoding UTF8 | ConvertFrom-Json
    Assert-True (
        [string]$manifest.platforms.'windows-x86_64'.url -eq
        "https://github.com/owner/repository/releases/download/v9.9.9/Loreweft_9.9.9_x64-setup.exe"
    ) "The fixture manifest did not use the expected ASCII asset URL."

    $wrongPrivateKeyPath = Join-Path $fixtureKeyDirectory "wrong-fixture.key"
    $null = & $tauriCli signer generate --ci --password $fixturePassword --write-keys $wrongPrivateKeyPath
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to generate the wrong-key fixture."
    }
    $wrongPublicKey = [System.IO.File]::ReadAllText("$wrongPrivateKeyPath.pub").Trim()
    $wrongConfig = [ordered]@{
        version = "9.9.9"
        plugins = [ordered]@{
            updater = [ordered]@{
                pubkey = $wrongPublicKey
            }
        }
    } | ConvertTo-Json -Depth 6
    [System.IO.File]::WriteAllText(
        $fixtureConfigPath,
        $wrongConfig,
        [System.Text.UTF8Encoding]::new($false)
    )
    $releaseBeforeWrongKey = Get-ReleaseFingerprint $releaseDirectory
    $wrongKey = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
    Assert-True (-not $wrongKey.Succeeded) "A signature from a different updater key was accepted."
    Assert-True ($wrongKey.Output -match 'different key|signature') "The wrong-key fixture failed for an unexpected reason: $($wrongKey.Output)"
    Assert-True ((Get-ReleaseFingerprint $releaseDirectory) -eq $releaseBeforeWrongKey) "The wrong-key failure changed the last known-good release directory."
    [System.IO.File]::WriteAllText(
        $fixtureConfigPath,
        $fixtureConfig,
        [System.Text.UTF8Encoding]::new($false)
    )

    [System.IO.File]::WriteAllBytes(
        $installerPath,
        $originalInstaller + [byte]0x21
    )
    $releaseBeforeTamperFailure = Get-ReleaseFingerprint $releaseDirectory
    $tampered = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
    Assert-True (-not $tampered.Succeeded) "A tampered installer passed updater signature verification."
    Assert-True ($tampered.Output -match 'signature') "The tampered installer failed for an unexpected reason: $($tampered.Output)"
    Assert-True ((Get-ReleaseFingerprint $releaseDirectory) -eq $releaseBeforeTamperFailure) "The tampered-installer failure changed the last known-good release directory."
    [System.IO.File]::WriteAllBytes($installerPath, $originalInstaller)
    [System.IO.File]::WriteAllBytes($signaturePath, $originalSignature)

    $originalFixturePreflight = [System.IO.File]::ReadAllBytes($fixturePreflightPath)
    [System.IO.File]::WriteAllBytes(
        $fixturePreflightPath,
        $originalFixturePreflight + [System.Text.Encoding]::ASCII.GetBytes("`n# stale fixture source")
    )
    $releaseBeforeEmbeddedMismatch = Get-ReleaseFingerprint $releaseDirectory
    $embeddedMismatch = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
    Assert-True (-not $embeddedMismatch.Succeeded) "An installer embedding a stale preflight script was accepted."
    Assert-True ($embeddedMismatch.Output -match 'stale|modified preflight') "The embedded-preflight mismatch failed for an unexpected reason: $($embeddedMismatch.Output)"
    Assert-True ((Get-ReleaseFingerprint $releaseDirectory) -eq $releaseBeforeEmbeddedMismatch) "The embedded-preflight failure changed the last known-good release directory."
    [System.IO.File]::WriteAllBytes($fixturePreflightPath, $originalFixturePreflight)

    $fixtureRenderedPath = Join-Path $fixtureRenderedDirectory "installer.nsi"
    $originalRenderedInstaller = [System.IO.File]::ReadAllText($fixtureRenderedPath)
    $brokenRenderedInstaller = $originalRenderedInstaller.Replace(
        @'
  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_done
'@,
        @'
  ; In update mode, always proceeds without uninstalling
  ${If} $UpdateMode = 1
    Goto reinst_uninstall
'@
    )
    Assert-True ($brokenRenderedInstaller -cne $originalRenderedInstaller) "The rendered-installer negative fixture did not change its /UPDATE branch."
    [System.IO.File]::WriteAllText($fixtureRenderedPath, $brokenRenderedInstaller, [System.Text.UTF8Encoding]::new($false))
    $releaseBeforeRenderedFailure = Get-ReleaseFingerprint $releaseDirectory
    $renderedFailure = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
    Assert-True (-not $renderedFailure.Succeeded) "A release with a broken rendered /UPDATE contract was accepted."
    Assert-True ($renderedFailure.Output -match '/UPDATE|Rendered NSIS contract') "The rendered-installer failure had an unexpected reason: $($renderedFailure.Output)"
    Assert-True ((Get-ReleaseFingerprint $releaseDirectory) -eq $releaseBeforeRenderedFailure) "The rendered-installer failure changed the last known-good release directory."
    [System.IO.File]::WriteAllText($fixtureRenderedPath, $originalRenderedInstaller, [System.Text.UTF8Encoding]::new($false))

    $outside = Invoke-Prepare $fixturePrepareScript $outsideRoot
    Assert-True (-not $outside.Succeeded) "BundleDirectory outside the script workspace was accepted."
    Assert-True ($outside.Output -match 'inside the workspace') "The outside-directory fixture failed for an unexpected reason: $($outside.Output)"

    $thirdPartyPathNotes = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory "Dependency example: C:\Users\third-party-ci\build"
    Assert-True $thirdPartyPathNotes.Succeeded "A harmless third-party example path was rejected: $($thirdPartyPathNotes.Output)"
    $thirdPartyPathFile = Join-Path $fixtureBundleDirectory "third-party-path.txt"
    [System.IO.File]::WriteAllText($thirdPartyPathFile, "C:\Users\third-party-ci\dependency\example", [System.Text.UTF8Encoding]::new($false))
    $thirdPartyPathTree = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
    Assert-True $thirdPartyPathTree.Succeeded "A harmless third-party dependency path in the candidate tree was rejected: $($thirdPartyPathTree.Output)"
    Remove-Item -LiteralPath $thirdPartyPathFile -Force
    $badNotes = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory (Join-Path $env:USERPROFILE "private build")
    Assert-True (-not $badNotes.Succeeded) "Release notes containing a personal path were accepted."
    $encodedPrivateKeyNotes = [Convert]::ToBase64String(
        [System.Text.Encoding]::UTF8.GetBytes("untrusted comment: minisign encrypted secret key")
    )
    $badEncodedNotes = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory "wrapped-key: $encodedPrivateKeyNotes"
    Assert-True (-not $badEncodedNotes.Succeeded) "Release notes containing base64-encoded private key material were accepted."

    $wrappedKeyPath = Join-Path $fixtureBundleDirectory "wrapped-key.json"
    [System.IO.File]::WriteAllText(
        $wrappedKeyPath,
        ('{"key":"' + $encodedPrivateKeyNotes + '"}'),
        [System.Text.UTF8Encoding]::new($false)
    )
    $wrappedKey = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
    Assert-True (-not $wrappedKey.Succeeded) "A JSON-wrapped base64 private key passed the candidate scan."
    Remove-Item -LiteralPath $wrappedKeyPath -Force

    $contaminants = [ordered]@{
        "leak.db" = "database fixture"
        "leak.db-journal" = "rollback journal fixture"
        "leak.key" = "key fixture"
        "leak.log" = "log fixture"
        "personal.txt" = (Join-Path $fixtureRoot "private\source")
    }
    foreach ($entry in $contaminants.GetEnumerator()) {
        $contaminantPath = Join-Path $fixtureBundleDirectory $entry.Key
        [System.IO.File]::WriteAllText(
            $contaminantPath,
            $entry.Value,
            [System.Text.UTF8Encoding]::new($false)
        )
        $releaseBeforeContamination = Get-ReleaseFingerprint $releaseDirectory
        $contaminated = Invoke-Prepare $fixturePrepareScript $fixtureBundleDirectory
        Assert-True (-not $contaminated.Succeeded) "Release contamination was accepted: $($entry.Key)"
        Assert-True ($contaminated.Output -match 'safety scan') "Contamination failed for an unexpected reason: $($contaminated.Output)"
        Assert-True ((Get-ReleaseFingerprint $releaseDirectory) -eq $releaseBeforeContamination) "A failed contamination gate changed the last known-good release directory."
        Remove-Item -LiteralPath $contaminantPath -Force
    }

    $requiredBuildPatterns = @(
        'Remove-WorkspaceDirectory (Join-Path $stageSitePackages "bin")',
        'Where-Object { $_.Name -eq "__pycache__" }',
        'Where-Object { $_.Extension -in ".pyc", ".pyo" }',
        'Assert-ReleaseTreeSafe @($stagePythonDir, $stageBackendDir)',
        'Assert-TauriUpdaterSignature $installer.FullName',
        'Assert-LoreweftTauriNsisToolchainContract',
        'Assert-LoreweftInstallerTemplateContract',
        'Assert-LoreweftRenderedNsisContract',
        'Assert-LoreweftInstallerEmbedsExactPreflight',
        'docs\desktop-updates.md'
    )
    $buildText = Get-Content -LiteralPath $sourceBuildScript -Raw -Encoding UTF8
    foreach ($requiredPattern in $requiredBuildPatterns) {
        Assert-True $buildText.Contains($requiredPattern) "build-desktop.ps1 is missing a release gate: $requiredPattern"
    }

    $prepareText = Get-Content -LiteralPath $sourcePrepareScript -Raw -Encoding UTF8
    foreach ($requiredPattern in @('Assert-LoreweftRenderedNsisContract', 'Assert-LoreweftInstallerEmbedsExactPreflight')) {
        Assert-True $prepareText.Contains($requiredPattern) "prepare-github-release.ps1 is missing a release gate: $requiredPattern"
    }

    foreach ($scriptPath in @($sourceBuildScript, $sourcePrepareScript, $sourceInstallerContract, $MyInvocation.MyCommand.Path)) {
        $tokens = $null
        $errors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile(
            (Resolve-Path -LiteralPath $scriptPath),
            [ref]$tokens,
            [ref]$errors
        )
        $errorMessages = @($errors | ForEach-Object { $_.Message }) -join ' | '
        Assert-True (@($errors).Count -eq 0) "PowerShell AST errors in ${scriptPath}: $errorMessages"
    }

    $buildTokens = $null
    $buildErrors = $null
    $buildAst = [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path -LiteralPath $sourceBuildScript),
        [ref]$buildTokens,
        [ref]$buildErrors
    )
    $prepareTokens = $null
    $prepareErrors = $null
    $prepareAst = [System.Management.Automation.Language.Parser]::ParseFile(
        (Resolve-Path -LiteralPath $sourcePrepareScript),
        [ref]$prepareTokens,
        [ref]$prepareErrors
    )
    $functionSelector = {
        param($node)
        return $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Assert-TauriUpdaterSignature"
    }
    $buildVerifier = $buildAst.Find($functionSelector, $true)
    $prepareVerifier = $prepareAst.Find($functionSelector, $true)
    Assert-True ($null -ne $buildVerifier -and $null -ne $prepareVerifier) "An updater signature verifier is missing."
    Assert-True (
        $buildVerifier.Extent.Text -ceq $prepareVerifier.Extent.Text
    ) "The build and release scripts use different updater signature verification logic."

    $authenticodeSelector = {
        param($node)
        return $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -eq "Test-InstallerAuthenticodeAccepted"
    }
    $buildAuthenticodePolicy = $buildAst.Find($authenticodeSelector, $true)
    $prepareAuthenticodePolicy = $prepareAst.Find($authenticodeSelector, $true)
    Assert-True ($null -ne $buildAuthenticodePolicy -and $null -ne $prepareAuthenticodePolicy) "An Authenticode policy function is missing."
    Assert-True (
        $buildAuthenticodePolicy.Extent.Text -ceq $prepareAuthenticodePolicy.Extent.Text
    ) "The build and release scripts use different Authenticode acceptance logic."
    Invoke-Expression $buildAuthenticodePolicy.Extent.Text
    foreach ($status in [Enum]::GetValues([System.Management.Automation.SignatureStatus])) {
        foreach ($allowUnsigned in @($false, $true)) {
            $expected = $status -eq [System.Management.Automation.SignatureStatus]::Valid -or (
                $status -eq [System.Management.Automation.SignatureStatus]::NotSigned -and $allowUnsigned
            )
            Assert-True (
                (Test-InstallerAuthenticodeAccepted $status $allowUnsigned) -eq $expected
            ) "Unexpected Authenticode policy for status=$status allowUnsigned=$allowUnsigned"
        }
    }

    Write-Host "Release pipeline fixture: passed"
    Write-Host "  cryptographic updater signature: valid accepted, wrong key and tampered bytes rejected"
    Write-Host "  workspace boundary: outside directory rejected"
    Write-Host "  candidate hygiene: database/journal, wrapped key, log, and personal path rejected"
    Write-Host "  failed gates preserve the last known-good upload directory"
    Write-Host "  installer contract: rendered control flow and embedded preflight verified again before staging"
    Write-Host "  Authenticode: only Valid, or explicitly accepted NotSigned, may pass"
    Write-Host "  PowerShell AST: 4 scripts, 0 errors; verifier/policy implementations identical"
}
finally {
    Remove-TestDirectory $fixtureRoot
    Remove-TestDirectory $outsideRoot
}
