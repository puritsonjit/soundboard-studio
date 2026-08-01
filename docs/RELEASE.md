# Release Process

This checklist applies to official Windows releases. The GitHub Actions build
and SignPath request must originate from the public repository.

## 1. Prepare

1. Update `VERSION` and user-visible release notes.
2. Confirm the same version is written to the Windows executable metadata.
3. Review dependency and GitHub Action updates separately from feature changes.
4. Confirm the privacy policy, third-party notices, and uninstall guide are
   current.

## 2. Verify Source

```powershell
env\pyqt6\Scripts\python.exe -m unittest discover -s tests -v
env\pyqt6\Scripts\ruff.exe check src\python_qt6 tests tools
env\pyqt6\Scripts\python.exe -m compileall -q src\python_qt6 tests tools
```

The release commit must be reviewed, committed, and pushed before creating a
version tag. Do not rebuild a published tag with different source.

## 3. Build And Sign

The pinned Windows workflow must:

1. build on a GitHub-hosted Windows runner;
2. verify tests, lint, bytecode compilation, and executable metadata;
3. upload the unsigned source artifact to GitHub Actions;
4. submit that exact artifact to SignPath with origin verification;
5. wait for the required manual signing approval; and
6. download the completed signed artifact without modifying it.

The SignPath API token is stored only as a GitHub Actions secret. Project,
policy, and artifact-configuration identifiers must match the approved SignPath
project.

## 4. Verify Artifact

Check Authenticode and timestamp status:

```powershell
Get-AuthenticodeSignature .\SoundboardQt6.exe |
  Format-List Status, StatusMessage, SignerCertificate, TimeStamperCertificate
```

`Status` must be `Valid`. Generate the checksum from the final signed file:

```powershell
Get-FileHash .\SoundboardQt6.exe -Algorithm SHA256
```

Run a malware scan and a clean-computer smoke test. Confirm first-launch folder
creation, VB-CABLE guidance, microphone routing, local preview, phone remote,
profile import/export, and application shutdown.

## 5. Publish

The GitHub release must include:

- the final artifact and SHA-256 checksum;
- source commit and successful workflow links;
- clear signed or unsigned status;
- code-signing, privacy, security, and uninstall links; and
- concise user-visible changes from `CHANGELOG.md`.

Never replace a published asset with different bytes under the same filename.
Publish a new version when executable content changes.
