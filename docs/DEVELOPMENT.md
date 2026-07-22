# Development

## Local Environment

All Python packages are installed under `env/pyqt6`; no global package install
is required. To recreate it on a computer with Python 3.12 and Internet access:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup-python-env.ps1
```

Direct runtime dependencies are listed in `requirements.txt`. The complete
Windows development and release environment, including transitive packages, is
pinned in `requirements-lock.txt`; `requirements-dev.txt` installs that lock.

## Run From Source

```powershell
env\pyqt6\Scripts\python.exe src\python_qt6\main.py
```

Source runs use the repository-level ignored `data` and `runtime` folders. The
packaged EXE uses the folders beside `dist_pyqt6\SoundboardQt6.exe`.

## Verify

```powershell
env\pyqt6\Scripts\python.exe -m unittest discover -s tests -v
env\pyqt6\Scripts\ruff.exe check src\python_qt6 tests tools
env\pyqt6\Scripts\python.exe -m compileall -q src\python_qt6 tests tools
```

## Build The EXE

```powershell
powershell -ExecutionPolicy Bypass -File tools\package-pyqt6.ps1
```

The script regenerates brand assets, creates a windowed one-file PyInstaller
build with stable Windows product/version metadata, collects dependency license
files, preserves personal `data` and `runtime` folders, replaces the generated
driver sidecar with the signed VB-CABLE fallback package when it is cached
locally, and writes:

```text
dist_pyqt6\SoundboardQt6.exe
```

If `env/driver_cache/VBCABLE_Driver_Pack45.zip` is absent, the EXE downloads and
Authenticode-verifies the official package when installation is needed.

## Sign A Windows Release

Public releases should be signed with a CA-issued Authenticode code-signing
certificate. A self-signed certificate is useful only on computers where its
root certificate was installed manually; it does not establish a trusted
publisher on a fresh computer.

Install the release certificate and its private key in the Windows Personal
certificate store for the build account. Hardware-backed and non-exportable
keys are supported. List usable certificates with:

```powershell
Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert |
  Select-Object Subject, Thumbprint, NotAfter, HasPrivateKey
```

Sign and verify an existing build:

```powershell
powershell -ExecutionPolicy Bypass -File tools\sign-windows-app.ps1 `
  -CertificateThumbprint "YOUR_CERTIFICATE_THUMBPRINT"
```

Or build and sign in one fail-closed operation:

```powershell
powershell -ExecutionPolicy Bypass -File tools\package-pyqt6.ps1 `
  -Sign `
  -CertificateThumbprint "YOUR_CERTIFICATE_THUMBPRINT"
```

The signing script uses SHA-256, requires an RFC 3161 timestamp, and verifies
the Windows Authenticode chain before replacing the release EXE. The original
EXE remains untouched if signing or verification fails. The thumbprint may
instead be placed in the `SOUNDBOARD_SIGNING_CERT_THUMBPRINT` environment
variable. Never store a private key, PFX password, token PIN, or signing-service
credential in Git; common private-key formats and the local `signing` directory
are ignored.

Confirm any release independently with:

```powershell
Get-AuthenticodeSignature dist_pyqt6\SoundboardQt6.exe |
  Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate
```

## Clean Reproducible Output

```powershell
powershell -ExecutionPolicy Bypass -File tools\clean-project.ps1
```

This removes only rebuildable caches and temporary build output. It deliberately
keeps the packaged EXE, Python environment, profiles, runtime assets, sound
collections, and local Git mirror.
