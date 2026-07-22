# Third-Party Notices

Soundboard Studio is licensed under GPLv3. The packaged Windows application
also contains or interacts with the components below. Exact license files from
installed Python distributions are collected into `dist_pyqt6/licenses` during
packaging.

## Runtime Components

- CPython: Python Software Foundation License.
- Qt for Python (`PySide6`, `shiboken6`, and Qt libraries): LGPL-3.0-only,
  GPL-2.0-only, or GPL-3.0-only. This project is distributed under GPLv3.
- NumPy: BSD-3-Clause and the additional notices shipped by NumPy.
- Pillow: MIT-CMU.
- aiohttp and its dependencies (`aiohappyeyeballs`, `aiosignal`, `attrs`,
  `frozenlist`, `idna`, `multidict`, `propcache`, `typing_extensions`, and
  `yarl`): Apache-2.0, MIT, BSD, or PSF licenses as identified by each package.
- qrcode: BSD license.
- sounddevice and PortAudio: MIT license.
- soundfile: BSD-3-Clause; its bundled libsndfile library is LGPL-2.1-or-later.
- soxr and libsoxr: LGPL-2.1-or-later.
- cffi and pycparser: MIT-0 and BSD-3-Clause.
- PyInstaller bootloader: GPL-2.0-or-later with the PyInstaller bootloader
  exception.
- OpenSSL libraries included by CPython: Apache-2.0.

Project home pages and complete package metadata are available from the pinned
dependencies in `requirements-lock.txt` and their Python Package Index pages.

## External Optional Components

- [cloudflared](https://github.com/cloudflare/cloudflared) is Apache-2.0
  software downloaded from its official GitHub release only when Internet
  remote mode is requested. It is not embedded in the Soundboard Studio EXE.
- [VB-CABLE](https://vb-audio.com/Cable/) is separately licensed proprietary
  software from VB-Audio. It is downloaded from VB-Audio when virtual microphone
  setup is requested and is not part of the open-source executable.

Pad sounds, pad images, profiles, and other user-selected runtime assets are not
part of the source repository or official EXE.
