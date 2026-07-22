[CmdletBinding()]
param(
    [string]$FilePath = (Join-Path (Split-Path -Parent $PSScriptRoot) "dist_pyqt6\SoundboardQt6.exe"),
    [string]$CertificateThumbprint = $env:SOUNDBOARD_SIGNING_CERT_THUMBPRINT,
    [ValidateSet("CurrentUser", "LocalMachine")]
    [string]$CertificateStoreLocation = "CurrentUser",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$Description = "Soundboard Studio"
)

$ErrorActionPreference = "Stop"

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    $kitsRoot = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
    if (-not (Test-Path -LiteralPath $kitsRoot)) {
        throw "SignTool was not found. Install the Windows SDK Signing Tools feature."
    }

    $candidates = @(
        Get-ChildItem -LiteralPath $kitsRoot -Filter signtool.exe -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Directory.Name -eq "x64" } |
            Sort-Object {
                try {
                    [version]$_.Directory.Parent.Name
                }
                catch {
                    [version]"0.0"
                }
            } -Descending
    )
    if ($candidates.Count -eq 0) {
        throw "The Windows SDK is present, but its x64 SignTool executable was not found."
    }

    return $candidates[0].FullName
}

function Get-SigningCertificate {
    param(
        [string]$StoreLocation,
        [string]$Thumbprint
    )

    $now = Get-Date
    $storePath = "Cert:\$StoreLocation\My"
    $certificates = @(
        Get-ChildItem -Path $storePath -CodeSigningCert -ErrorAction Stop |
            Where-Object {
                $_.HasPrivateKey -and
                $_.NotBefore -le $now -and
                $_.NotAfter -gt $now
            }
    )

    $normalizedThumbprint = (([string]$Thumbprint) -replace "\s", "").ToUpperInvariant()
    if ($normalizedThumbprint) {
        $certificate = $certificates |
            Where-Object { $_.Thumbprint.ToUpperInvariant() -eq $normalizedThumbprint } |
            Select-Object -First 1
        if (-not $certificate) {
            throw "No valid code-signing certificate with private key and thumbprint '$normalizedThumbprint' was found in $storePath."
        }
        return $certificate
    }

    if ($certificates.Count -eq 0) {
        throw "No valid code-signing certificate with a private key was found in $storePath. Obtain a CA-issued certificate before signing a public release."
    }
    if ($certificates.Count -gt 1) {
        $available = ($certificates | ForEach-Object { "$($_.Subject) [$($_.Thumbprint)]" }) -join "; "
        throw "More than one code-signing certificate is available. Set -CertificateThumbprint explicitly. Available: $available"
    }

    return $certificates[0]
}

$resolvedFile = (Resolve-Path -LiteralPath $FilePath).Path
if ([System.IO.Path]::GetExtension($resolvedFile) -notin @(".exe", ".dll")) {
    throw "Authenticode signing is limited to .exe and .dll files in this project."
}
if ([string]::IsNullOrWhiteSpace($TimestampUrl)) {
    throw "A trusted RFC 3161 timestamp URL is required for release signing."
}

$signTool = Find-SignTool
$certificate = Get-SigningCertificate `
    -StoreLocation $CertificateStoreLocation `
    -Thumbprint $CertificateThumbprint
$temporaryFile = Join-Path `
    (Split-Path -Parent $resolvedFile) `
    ("{0}.signing-{1}{2}" -f `
        [System.IO.Path]::GetFileNameWithoutExtension($resolvedFile), `
        [guid]::NewGuid().ToString("N"), `
        [System.IO.Path]::GetExtension($resolvedFile))

Copy-Item -LiteralPath $resolvedFile -Destination $temporaryFile -Force
try {
    $signArguments = @(
        "sign",
        "/v",
        "/sha1", $certificate.Thumbprint,
        "/s", "My",
        "/fd", "SHA256",
        "/tr", $TimestampUrl,
        "/td", "SHA256",
        "/d", $Description
    )
    if ($CertificateStoreLocation -eq "LocalMachine") {
        $signArguments += "/sm"
    }
    $signArguments += $temporaryFile

    & $signTool @signArguments
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool failed while signing with exit code $LASTEXITCODE. The release EXE was not changed."
    }

    & $signTool verify /pa /all /v $temporaryFile
    if ($LASTEXITCODE -ne 0) {
        throw "SignTool verification failed with exit code $LASTEXITCODE. The release EXE was not changed."
    }

    $signature = Get-AuthenticodeSignature -LiteralPath $temporaryFile
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "Windows reported signature status '$($signature.Status)'. The release EXE was not changed."
    }
    if (-not $signature.TimeStamperCertificate) {
        throw "The signature has no trusted timestamp. The release EXE was not changed."
    }

    Move-Item -LiteralPath $temporaryFile -Destination $resolvedFile -Force

    $installedSignature = Get-AuthenticodeSignature -LiteralPath $resolvedFile
    if ($installedSignature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
        throw "The signed release failed its final verification with status '$($installedSignature.Status)'."
    }

    Write-Host "Signed and verified:"
    Write-Host "  $resolvedFile"
    Write-Host "Publisher:"
    Write-Host "  $($installedSignature.SignerCertificate.Subject)"
    Write-Host "Certificate thumbprint:"
    Write-Host "  $($installedSignature.SignerCertificate.Thumbprint)"
    Write-Host "Timestamp authority:"
    Write-Host "  $($installedSignature.TimeStamperCertificate.Subject)"
}
finally {
    if (Test-Path -LiteralPath $temporaryFile) {
        Remove-Item -LiteralPath $temporaryFile -Force
    }
}
