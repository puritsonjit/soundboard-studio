# Soundboard Studio

Soundboard Studio is a portable Windows soundboard with a Qt for Python desktop
host and a responsive phone-browser remote. It combines a selected microphone
with pad audio and sends the mix to a VB-CABLE virtual microphone for voice
apps, streaming tools, and recording software.

[Download v1.0.0](https://github.com/puritsonjit/soundboard-studio/releases/tag/v1.0.0)
| [Changelog](CHANGELOG.md)
| [Code signing policy](CODE_SIGNING_POLICY.md)

> **Release status:** The project has been accepted into the SignPath Foundation
> open-source program. The current v1.0.0 preview files remain unsigned while
> the verified signing integration is completed. Signed artifacts will be
> identified explicitly after Authenticode verification succeeds.

## Features

- 24 programmable pads with labels, images, colors, independent volume, and
  optional global keyboard shortcuts
- single-clip pads and random sound-pack folders
- built-in clip editor with a permanent 15-second playback limit
- responsive phone remote for local Wi-Fi and temporary private Internet links
- Stack and Solo playback modes with a universal Stop All control
- microphone and pad mixing at 48 kHz through VB-CABLE
- soundboard-only local monitoring with independent preview volume
- ZIP profile import and export with managed copies of pad sounds and images
- one-file, console-free Windows build with automatic runtime-folder creation

## Requirements

- 64-bit Windows 10 or Windows 11
- a microphone and an output device for local soundboard monitoring
- administrator approval when VB-CABLE must be installed
- a phone and PC on the same reachable network for Local remote mode
- Internet access for first-time helper downloads or Internet remote mode

## Download And Run

1. Open the [GitHub Releases](https://github.com/puritsonjit/soundboard-studio/releases)
   page.
2. Download the portable ZIP and extract it to a writable folder.
3. Launch `SoundboardQt6.exe`.
4. If VB-CABLE is missing, approve its signed installer and restart Windows when
   requested.
5. In Discord, TeamSpeak, OBS, or another voice app, select
   `CABLE Output (VB-Audio Virtual Cable)` as the microphone.

The app creates and manages `data`, `runtime`, and `driver` beside the EXE.
Keep those folders with the EXE when moving an existing setup to another
computer. Export a profile ZIP before replacing or removing a configured copy.

Discord Studio mode is recommended. Voice isolation and aggressive noise
suppression can treat music and effects as unwanted background audio.

## Audio Routing

```text
real microphone ----+
                    +--> Soundboard Studio --> CABLE Input
sound pad ----------+                          |
                                               +--> CABLE Output --> voice app

sound pad only -------------------------------------> headphones / speakers
```

The microphone is not played through the local monitor. Desktop previews are
local only; phone and global-shortcut triggers enter the virtual microphone mix.

## Build From Source

Create the project-local Python 3.12 environment, verify the source, and build
the portable EXE:

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup-python-env.ps1
env\pyqt6\Scripts\python.exe -m unittest discover -s tests -v
env\pyqt6\Scripts\ruff.exe check src\python_qt6 tests tools
powershell -ExecutionPolicy Bypass -File tools\package-pyqt6.ps1
```

See [Development](docs/DEVELOPMENT.md) for the complete workflow.

## Project Layout

```text
.github/workflows/   Verified Windows release build
src/python_qt6/      Desktop application, audio engine, and phone remote
tests/               Automated regression tests
tools/               Environment, validation, packaging, and signing tools
docs/                Architecture, remote, development, and release guides
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Phone Remote](docs/REMOTE.md)
- [Development](docs/DEVELOPMENT.md)
- [Release Process](docs/RELEASE.md)
- [Git Workflow](docs/GIT_WORKFLOW.md)
- [Privacy](PRIVACY.md)
- [Security](SECURITY.md)
- [Code Signing Policy](CODE_SIGNING_POLICY.md)
- [Third-Party Notices](THIRD_PARTY_NOTICES.md)
- [Contributing](CONTRIBUTING.md)
- [Uninstall](docs/UNINSTALL.md)

## Release Integrity

Official release candidates are built from this public repository by the pinned
GitHub Actions workflow. Each release provides a SHA-256 checksum. Signed
releases are promoted only after the Authenticode signature, timestamp, source
commit, and release metadata have been verified.

Free code signing is provided by [SignPath.io](https://signpath.io/), certificate
by [SignPath Foundation](https://signpath.org/).

## License

Soundboard Studio is free and open-source software licensed under the
[GNU General Public License version 3](LICENSE).
