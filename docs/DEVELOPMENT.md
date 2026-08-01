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

## Release Signing

The project has been accepted into the SignPath Foundation open-source program.
The GitHub Actions workflow creates and uploads an unsigned source artifact.
After the SignPath project identifiers and repository secret are configured,
the official SignPath GitHub action submits that exact GitHub artifact for
origin-verified signing and downloads the completed result.

Never commit a SignPath API token, private key, PFX password, hardware-token
PIN, or other signing credential. Signing requests require the approval and
verification rules in [Code Signing Policy](../CODE_SIGNING_POLICY.md).

Confirm a signed release independently with:

```powershell
Get-AuthenticodeSignature dist_pyqt6\SoundboardQt6.exe |
  Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate
```

`Status` must be `Valid`, and both signer and timestamp certificates must be
present. Verify the SHA-256 checksum after signing because Authenticode changes
the executable bytes.

`tools/sign-windows-app.ps1` remains available for maintainers who own a
separate CA-issued Authenticode certificate. It is not the SignPath Foundation
workflow. See [Release Process](RELEASE.md) for the authoritative checklist.

## Clean Reproducible Output

```powershell
powershell -ExecutionPolicy Bypass -File tools\clean-project.ps1
```

This removes only rebuildable caches and temporary build output. It deliberately
keeps the packaged EXE, Python environment, profiles, runtime assets, sound
collections, and local Git mirror.
