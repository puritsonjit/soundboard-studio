# Soundboard Studio

Soundboard Studio is a portable Windows soundboard with a Python Qt6 desktop
app and a responsive phone-browser remote. It combines a selected microphone
with pad audio and sends the mix to the signed VB-CABLE virtual microphone.

Source repository: [github.com/puritsonjit/soundboard-studio](https://github.com/puritsonjit/soundboard-studio)

The project is free and open-source software licensed under the
[GNU General Public License version 3](LICENSE). Selling an unmodified or
modified build does not remove the GPL source and redistribution requirements.

## Current Features

- 24 programmable pads with labels, images, colors, independent volume, and
  optional global keyboard shortcuts
- single-clip pads or random sound-pack folders
- permanent 15-second clip limit with an editor for trimming longer files
- local-only preview from the desktop; phone and shortcut triggers enter the
  virtual microphone mix
- Stack and Solo playback modes plus Stop All
- local LAN or private temporary Internet remote with a QR code
- ZIP profile import/export and managed copies of every pad sound and image
- low-latency 48 kHz microphone and soundboard mixing
- one-file, console-free Windows packaging
- fail-closed Authenticode release signing for a CA-issued publisher certificate

## Run The App

The current packaged build is:

```text
dist_pyqt6\SoundboardQt6.exe
```

The EXE creates `data`, `runtime`, and `driver` beside itself as needed. Keep
those folders with the EXE when moving an existing setup to another computer.
On a fresh computer, the app prepares the signed VB-CABLE installer on first
launch if no virtual route is available. Windows still requires UAC approval
and may require a reboot because the virtual microphone is a system driver.

In Discord or another voice app, select:

```text
CABLE Output (VB-Audio Virtual Cable)
```

Discord Studio mode is recommended. Voice-isolation and noise-suppression modes
often treat music and effects as unwanted background audio.

## Project Layout

```text
src/python_qt6/       Active desktop app, audio engine, and web remote
tests/                Automated regression tests
tools/                Environment, build, cleanup, and local Git scripts
dist_pyqt6/           Packaged EXE and personal runtime data (not tracked)
env/pyqt6/            Project-local Python environment (not tracked)
env/driver_cache/     Cached official VB-CABLE package (not tracked)
Backup/local-git/     Local bare Git recovery mirror (not tracked)
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Phone Remote](docs/REMOTE.md)
- [Development](docs/DEVELOPMENT.md)
- [Local Git](LOCAL_GIT_GUIDE.md)
- [Privacy](PRIVACY.md)
- [Security](SECURITY.md)
- [Code Signing Policy](CODE_SIGNING_POLICY.md)
- [Third-Party Notices](THIRD_PARTY_NOTICES.md)
- [Contributing](CONTRIBUTING.md)
- [Uninstall](docs/UNINSTALL.md)

Official public builds are produced by the pinned GitHub Actions workflow. The
project is preparing an application for free open-source signing through
SignPath Foundation; releases remain explicitly unsigned until that application
is accepted and the signing integration is active.

The unfinished C# prototype and custom test-signed SysVAD driver were removed
from the active tree. Their complete history remains available under the Git tag
`pre-cleanup-2026-07-18`.
